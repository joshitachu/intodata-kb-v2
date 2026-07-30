"""Strict, bounded import of one preserved model-input task bundle.

This is deliberately an offline reader.  It neither discovers sibling receipts
nor follows checkpoint paths, so imported evidence cannot silently broaden into
the mutable legacy output tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from errno import ELOOP
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp_server.experiments.domain.parity import (
    GoldenFakeRuntimeInput,
    GoldenLineageInput,
    GoldenParityReceipt,
    GoldenParityRequest,
    GoldenRunLineage,
    GoldenTaskUsage,
    JsonValue,
    ModelRoute,
    freeze_json,
    thaw_json,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


class GoldenImportError(RuntimeError):
    """The preserved bundle cannot be safely treated as parity evidence."""


_ARTIFACT_NAMES = frozenset({"prompt", "context", "request", "response"})
_DEFAULT_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenImportCommand:
    """All file references are relative to exactly one approved bundle root."""

    task_id: str
    allowed_root: Path
    prompt_relative_path: Path
    context_relative_path: Path
    request_relative_path: Path
    response_relative_path: Path
    output_contract: Mapping[str, JsonValue]
    expected_sha256: Mapping[str, str]
    lineage: GoldenLineageInput
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if self.max_artifact_bytes <= 0:
            raise ValueError("max_artifact_bytes must be positive")
        for path in (
            self.prompt_relative_path,
            self.context_relative_path,
            self.request_relative_path,
            self.response_relative_path,
        ):
            _validate_relative_path(path)
        if set(self.expected_sha256) != _ARTIFACT_NAMES:
            raise ValueError("expected_sha256 must contain exactly prompt, context, request, response")
        normalized_hashes = dict(self.expected_sha256)
        for name, digest in normalized_hashes.items():
            _validate_sha256(digest, f"expected_sha256[{name}]")
        if not isinstance(self.output_contract, Mapping):
            raise ValueError("output_contract must be a JSON object")
        object.__setattr__(self, "expected_sha256", MappingProxyType(normalized_hashes))
        object.__setattr__(self, "output_contract", freeze_json(self.output_contract))


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenImportedBundle:
    """Imported receipt plus the exact verified bytes to freeze immediately."""

    receipt: GoldenParityReceipt
    artifacts: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if set(self.artifacts) != _ARTIFACT_NAMES:
            raise ValueError(
                "golden artifacts must contain exactly prompt, context, request, response"
            )
        copied: dict[str, bytes] = {}
        for name, content in self.artifacts.items():
            if not isinstance(content, bytes):
                raise TypeError("golden artifact content must be bytes")
            if hashlib.sha256(content).hexdigest() != self.receipt.source_hashes[name]:
                raise ValueError("golden artifact bytes do not match imported receipt")
            copied[name] = bytes(content)
        object.__setattr__(self, "artifacts", MappingProxyType(copied))


class GoldenParityImporter:
    """Read the four explicitly approved baseline task files and nothing else."""

    def import_receipt(self, *, command: GoldenImportCommand) -> GoldenParityReceipt:
        return self.import_bundle(command=command).receipt

    def import_bundle(
        self,
        *,
        command: GoldenImportCommand,
    ) -> GoldenImportedBundle:
        root = _validate_root(command.allowed_root)
        paths = {
            "prompt": command.prompt_relative_path,
            "context": command.context_relative_path,
            "request": command.request_relative_path,
            "response": command.response_relative_path,
        }
        content = {
            name: self._read_text(
                root=root,
                relative_path=relative_path,
                expected_sha256=command.expected_sha256[name],
                max_bytes=command.max_artifact_bytes,
            )
            for name, relative_path in paths.items()
        }
        request_payload = _parse_json_object(content["request"], artifact_name="request")
        response_payload = _parse_json_object(content["response"], artifact_name="response")
        task_route = _task_route(request_payload)
        run_route = _run_route(command.lineage.run_model_route, fallback=task_route)
        output_contract = thaw_json(command.output_contract)
        output_contract_sha256 = hashlib.sha256(canonical_json_bytes(output_contract)).hexdigest()
        artifact_hashes = {
            name: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for name, value in content.items()
        }
        lineage = GoldenRunLineage(
            baseline_run_id=command.lineage.baseline_run_id,
            resume_requested=command.lineage.resume_requested,
            run_model_route=run_route,
            task_model_route=task_route,
            task_model_precedence="task_request",
            attributable_call_count=command.lineage.attributable_call_count,
            inherited_checkpoint_artifacts=command.lineage.inherited_checkpoint_artifacts,
        )
        request = GoldenParityRequest(
            task_id=command.task_id,
            semantic_prompt=content["prompt"],
            context_bundle=content["context"],
            output_contract=output_contract,
            task_model_route=task_route,
            model_options=_model_options(request_payload),
            lineage=lineage,
            artifact_hashes=artifact_hashes,
            output_contract_sha256=output_contract_sha256,
        )
        usage = _usage_from_response(response_payload)
        aggregate = _aggregate_receipt_sha256(
            request=request,
            response=response_payload,
            usage=usage,
        )
        receipt = GoldenParityReceipt(
            request=request,
            baseline_response=response_payload,
            baseline_usage=usage,
            aggregate_receipt_sha256=aggregate,
            fake_runtime=GoldenFakeRuntimeInput(
                provider_id=task_route.provider_id,
                model_id=task_route.model_id,
                usage=usage.runtime_usage,
                provider_latency_ms=usage.latency_ms,
                retry_count=usage.retries,
            ),
        )
        return GoldenImportedBundle(
            receipt=receipt,
            artifacts={
                name: value.encode("utf-8")
                for name, value in content.items()
            },
        )

    @staticmethod
    def _read_text(
        *,
        root: Path,
        relative_path: Path,
        expected_sha256: str,
        max_bytes: int,
    ) -> str:
        try:
            data = _read_bounded_file(root=root, relative_path=relative_path, max_bytes=max_bytes)
        except OSError as exc:
            raise GoldenImportError("golden artifact could not be read") from exc
        if len(data) > max_bytes:
            raise GoldenImportError("golden artifact exceeds the approved byte limit")
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise GoldenImportError("golden artifact hash drift")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise GoldenImportError("golden artifact must be strict UTF-8") from exc


def _validate_relative_path(path: Path) -> None:
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("golden artifact paths must be safe relative paths")


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_root(root: Path) -> Path:
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise GoldenImportError("approved golden root is unavailable") from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise GoldenImportError("approved golden root must be a regular directory")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise GoldenImportError("approved golden root cannot be resolved") from exc


def _read_bounded_file(*, root: Path, relative_path: Path, max_bytes: int) -> bytes:
    """Read through directory file descriptors without following any symlink.

    The preceding relative-path validation is convenient for error reporting; this
    descriptor walk is the enforcement point, including against a parent being
    swapped for a symlink after its initial inspection.
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise GoldenImportError("safe golden artifact traversal is unavailable")
    directory_fd = os.open(root, os.O_RDONLY | directory | nofollow)
    try:
        for part in relative_path.parts[:-1]:
            if stat.S_ISLNK(os.stat(part, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                raise GoldenImportError("golden artifact path may not contain symlinks")
            next_fd = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=directory_fd)
            try:
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    raise GoldenImportError("golden artifact parent must be a directory")
            except Exception:
                os.close(next_fd)
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        if stat.S_ISLNK(
            os.stat(relative_path.parts[-1], dir_fd=directory_fd, follow_symlinks=False).st_mode
        ):
            raise GoldenImportError("golden artifact path may not contain symlinks")
        file_fd = os.open(relative_path.parts[-1], os.O_RDONLY | nofollow, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise GoldenImportError("golden artifact must be a regular file")
            with os.fdopen(file_fd, "rb", closefd=True) as handle:
                return handle.read(max_bytes + 1)
        except Exception:
            # ``fdopen`` owns the descriptor only after it is constructed.
            try:
                os.close(file_fd)
            except OSError:
                pass
            raise
    except OSError as exc:
        if exc.errno == ELOOP:
            raise GoldenImportError("golden artifact path may not contain symlinks") from exc
        raise
    finally:
        os.close(directory_fd)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse_json_object(text: str, *, artifact_name: str) -> Mapping[str, JsonValue]:
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_json_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise GoldenImportError(f"golden {artifact_name} JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise GoldenImportError(f"golden {artifact_name} JSON must be a top-level object")
    try:
        frozen = freeze_json(payload)
    except ValueError as exc:
        raise GoldenImportError(f"golden {artifact_name} JSON is not supported") from exc
    assert isinstance(frozen, Mapping)
    return frozen


def _required_string(payload: Mapping[str, JsonValue], name: str, *, artifact_name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise GoldenImportError(f"golden {artifact_name} has no usable {name}")
    return value.strip()


def _canonical_route(*, raw_provider_route: str, model_id: str) -> ModelRoute:
    raw = raw_provider_route.strip()
    route_part = raw.split(" @ ", maxsplit=1)[0].strip()
    route_parts = route_part.split("/", maxsplit=1)
    provider_id = route_parts[0].strip()
    if not provider_id:
        raise GoldenImportError("golden request has no usable provider route")
    if len(route_parts) == 2 and route_parts[1].strip() != model_id:
        raise GoldenImportError(
            "golden request provider route and task model disagree"
        )
    return ModelRoute(
        provider_id=provider_id,
        model_id=model_id,
        canonical_route=f"{provider_id}/{model_id}",
        raw_provider_route=_sanitized_provider_route(raw),
    )


def _sanitized_provider_route(raw: str) -> str:
    """Retain route evidence without URL credentials, query data, or fragments."""

    route_parts = raw.split(" @ ", maxsplit=1)
    if len(route_parts) == 1:
        return route_parts[0].strip()
    route, endpoint = (part.strip() for part in route_parts)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise GoldenImportError("golden provider endpoint is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GoldenImportError("golden provider endpoint is invalid")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    sanitized = urlunsplit(
        (parsed.scheme, netloc, parsed.path, "", "")
    )
    return f"{route} @ {sanitized}"


def _task_route(request: Mapping[str, JsonValue]) -> ModelRoute:
    return _canonical_route(
        raw_provider_route=_required_string(request, "provider", artifact_name="request"),
        model_id=_required_string(request, "model", artifact_name="request"),
    )


def _run_route(raw_route: str | None, *, fallback: ModelRoute) -> ModelRoute | None:
    if raw_route is None:
        return None
    route_part = raw_route.strip().split(" @ ", maxsplit=1)[0].strip()
    if "/" not in route_part:
        return _canonical_route(raw_provider_route=raw_route, model_id=fallback.model_id)
    provider_id, model_id = route_part.split("/", maxsplit=1)
    if not provider_id.strip() or not model_id.strip():
        raise GoldenImportError("baseline run model route is invalid")
    return _canonical_route(raw_provider_route=raw_route, model_id=model_id.strip())


def _model_options(request: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Preserve only explicitly recorded model-call options; never infer defaults."""

    provider_options = request.get(
        "provider_options",
        request.get("options", {}),
    )
    if not isinstance(provider_options, Mapping):
        raise GoldenImportError("golden request model options must be an object when recorded")
    options: dict[str, JsonValue] = {
        "provider_options": freeze_json(provider_options)
    }
    for name in ("max_tokens", "temperature"):
        if name in request:
            options[name] = request[name]
    try:
        frozen = freeze_json(options)
    except ValueError as exc:
        raise GoldenImportError(
            "golden request model options are invalid"
        ) from exc
    assert isinstance(frozen, Mapping)
    return frozen


def _non_negative_int(payload: Mapping[str, JsonValue], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GoldenImportError(f"golden response {name} must be a non-negative integer or null")
    return value


def _non_negative_float(payload: Mapping[str, JsonValue], name: str) -> float | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise GoldenImportError(f"golden response {name} must be a non-negative number or null")
    return float(value)


def _usage_from_response(response: Mapping[str, JsonValue]) -> GoldenTaskUsage:
    return GoldenTaskUsage(
        input_tokens=_non_negative_int(response, "input_tokens"),
        output_tokens=_non_negative_int(response, "output_tokens"),
        input_cache_read_tokens=_non_negative_int(response, "input_cache_read_tokens"),
        input_cache_miss_tokens=_non_negative_int(response, "input_cache_miss_tokens"),
        latency_ms=_non_negative_int(response, "latency_ms"),
        retries=_non_negative_int(response, "retries"),
        billed_cost_usd=_non_negative_float(response, "billed_cost_usd"),
        raw=response,
    )


def _aggregate_receipt_sha256(
    *,
    request: GoldenParityRequest,
    response: Mapping[str, JsonValue],
    usage: GoldenTaskUsage,
) -> str:
    """Hash the complete semantic receipt, never paths or mutable object identity."""

    lineage = request.lineage
    payload = {
        "artifact_hashes": dict(request.artifact_hashes),
        "baseline_response": thaw_json(freeze_json(response)),
        "lineage": {
            "attributable_call_count": lineage.attributable_call_count,
            "baseline_run_id": lineage.baseline_run_id,
            "inherited_checkpoint_artifacts": [
                {
                    "attributable_to_baseline_run": item.attributable_to_baseline_run,
                    "logical_id": item.logical_id,
                    "raw_model_route": item.raw_model_route,
                    "sha256": item.sha256,
                }
                for item in lineage.inherited_checkpoint_artifacts
            ],
            "resume_requested": lineage.resume_requested,
            "run_model_route": _route_payload(lineage.run_model_route),
            "task_model_precedence": lineage.task_model_precedence,
            "task_model_route": _route_payload(lineage.task_model_route),
        },
        "model_options": thaw_json(request.model_options),
        "output_contract": thaw_json(request.output_contract),
        "output_contract_sha256": request.output_contract_sha256,
        "semantic_prompt_sha256": request.artifact_hashes["prompt"],
        "context_bundle_sha256": request.artifact_hashes["context"],
        "request_sha256": request.artifact_hashes["request"],
        "response_sha256": request.artifact_hashes["response"],
        "task_id": request.task_id,
        "usage": {
            "billed_cost_usd": usage.billed_cost_usd,
            "input_cache_miss_tokens": usage.input_cache_miss_tokens,
            "input_cache_read_tokens": usage.input_cache_read_tokens,
            "input_tokens": usage.input_tokens,
            "latency_ms": usage.latency_ms,
            "output_tokens": usage.output_tokens,
            "retries": usage.retries,
        },
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _route_payload(route: ModelRoute | None) -> dict[str, str] | None:
    if route is None:
        return None
    return {
        "canonical_route": route.canonical_route,
        "model_id": route.model_id,
        "provider_id": route.provider_id,
        "raw_provider_route": route.raw_provider_route,
    }
