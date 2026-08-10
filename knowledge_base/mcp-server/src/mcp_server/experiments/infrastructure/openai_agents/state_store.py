"""Append-only local durability for OpenAI Agents task reservations."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    RuntimeTaskHandle,
    RuntimeTaskResult,
    RuntimeTaskStatus,
    RuntimeTimings,
    RuntimeUsage,
)
from mcp_server.experiments.infrastructure.openai_agents.runtime import (
    RuntimeTaskReservation,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


class FileRuntimeTaskStateStore:
    """Write-once reservation/completion records safe across process restarts."""

    durable = True

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("OpenAI Agents task-state root must be a regular directory")

    async def reserve(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        proposed_handle: RuntimeTaskHandle,
    ) -> RuntimeTaskReservation:
        return await asyncio.to_thread(
            self._reserve,
            idempotency_key,
            request_sha256,
            proposed_handle,
        )

    async def complete(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        result: RuntimeTaskResult,
    ) -> None:
        await asyncio.to_thread(
            self._complete,
            idempotency_key,
            request_sha256,
            result,
        )

    def _reserve(
        self,
        idempotency_key: str,
        request_sha256: str,
        proposed_handle: RuntimeTaskHandle,
    ) -> RuntimeTaskReservation:
        reservation_path, completion_path = self._paths(idempotency_key)
        payload = {
            "handle": asdict(proposed_handle),
            "idempotency_key_sha256": sha256(idempotency_key.encode("utf-8")).hexdigest(),
            "request_sha256": request_sha256,
            "schema_version": 1,
        }
        created = _create_once(reservation_path, canonical_json_bytes(payload))
        stored = _read_json(reservation_path)
        handle = RuntimeTaskHandle(**_required_mapping(stored, "handle"))
        stored_request = stored.get("request_sha256")
        if stored_request != request_sha256 or handle != proposed_handle:
            raise ValueError("durable OpenAI Agents reservation conflicts with this request")
        result = _decode_result(_read_json(completion_path)) if completion_path.is_file() else None
        return RuntimeTaskReservation(
            handle=handle,
            request_sha256=request_sha256,
            created=created,
            result=result,
        )

    def _complete(
        self,
        idempotency_key: str,
        request_sha256: str,
        result: RuntimeTaskResult,
    ) -> None:
        reservation_path, completion_path = self._paths(idempotency_key)
        reservation = _read_json(reservation_path)
        if reservation.get("request_sha256") != request_sha256:
            raise ValueError("durable OpenAI Agents completion conflicts with its reservation")
        payload = {
            "request_sha256": request_sha256,
            "result": _encode_result(result),
            "schema_version": 1,
        }
        encoded = canonical_json_bytes(payload)
        if not _create_once(completion_path, encoded):
            if completion_path.read_bytes() != encoded:
                raise ValueError("durable OpenAI Agents completion is already different")

    def _paths(self, idempotency_key: str) -> tuple[Path, Path]:
        identity = sha256(idempotency_key.encode("utf-8")).hexdigest()
        directory = self._root / identity[:2] / identity
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "reservation.json", directory / "completion.json"


def _create_once(path: Path, content: bytes) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise
    return True


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("durable OpenAI Agents state is unreadable") from error
    if not isinstance(value, dict):
        raise ValueError("durable OpenAI Agents state must be an object")
    return value


def _required_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ValueError(f"durable OpenAI Agents state is missing {key}")
    return nested


def _encode_result(result: RuntimeTaskResult) -> dict[str, Any]:
    return {
        "failure_category": result.failure_category,
        "failure_detail": dict(result.failure_detail),
        "message_id": result.message_id,
        "model_id": result.model_id,
        "output_refs": [asdict(reference) for reference in result.output_refs],
        "provider_id": result.provider_id,
        "retry_count": result.retry_count,
        "runtime_id": result.runtime_id,
        "runtime_version": result.runtime_version,
        "session_id": result.session_id,
        "status": result.status.value,
        "timings": {
            "completed_at": _iso(result.timings.completed_at),
            "elapsed_ms": result.timings.elapsed_ms,
            "provider_latency_ms": result.timings.provider_latency_ms,
            "started_at": _iso(result.timings.started_at),
        },
        "tool_bytes": result.tool_bytes,
        "tool_calls": result.tool_calls,
        "usage": asdict(result.usage),
    }


def _decode_result(value: dict[str, Any]) -> RuntimeTaskResult:
    result = _required_mapping(value, "result")
    timings = _required_mapping(result, "timings")
    usage = _required_mapping(result, "usage")
    refs = result.get("output_refs")
    if not isinstance(refs, list) or any(not isinstance(item, dict) for item in refs):
        raise ValueError("durable OpenAI Agents output references are invalid")
    return RuntimeTaskResult(
        status=RuntimeTaskStatus(str(result["status"])),
        runtime_id=str(result["runtime_id"]),
        runtime_version=_optional_text(result.get("runtime_version")),
        provider_id=str(result["provider_id"]),
        model_id=str(result["model_id"]),
        session_id=_optional_text(result.get("session_id")),
        message_id=_optional_text(result.get("message_id")),
        output_refs=tuple(ArtifactReference(**item) for item in refs),
        usage=RuntimeUsage(**usage),
        timings=RuntimeTimings(
            started_at=_datetime(timings.get("started_at")),
            completed_at=_datetime(timings.get("completed_at")),
            elapsed_ms=timings.get("elapsed_ms"),
            provider_latency_ms=timings.get("provider_latency_ms"),
        ),
        retry_count=int(result.get("retry_count", 0)),
        tool_calls=int(result.get("tool_calls", 0)),
        tool_bytes=int(result.get("tool_bytes", 0)),
        failure_category=_optional_text(result.get("failure_category")),
        failure_detail=(
            result["failure_detail"] if isinstance(result.get("failure_detail"), dict) else {}
        ),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None
