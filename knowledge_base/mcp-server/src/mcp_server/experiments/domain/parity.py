"""Immutable, runtime-neutral receipts for a preserved parity task.

The types in this module intentionally contain logical identities and bytes-derived
facts only.  They never retain a host path: a golden bundle is an input boundary,
not evidence a later task may read from the filesystem.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from mcp_server.experiments.domain.runtime import RuntimeUsage


JsonValue = None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def freeze_json(value: Any) -> JsonValue:
    """Return a recursively immutable JSON value, rejecting non-JSON input."""

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            frozen[key] = freeze_json(nested)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise ValueError("value must be JSON serializable")


def thaw_json(value: JsonValue) -> Any:
    """Return a JSON-serializable copy of a frozen value for canonical hashing."""

    if isinstance(value, Mapping):
        return {key: thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _non_negative(value: int | None, field_name: str) -> None:
    if value is not None and (isinstance(value, bool) or value < 0):
        raise ValueError(f"{field_name} must be a non-negative integer or unknown")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRoute:
    """Pinned model identity with its exact historical provider route retained."""

    provider_id: str
    model_id: str
    canonical_route: str
    raw_provider_route: str

    def __post_init__(self) -> None:
        for field_name in ("provider_id", "model_id", "canonical_route", "raw_provider_route"):
            _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenCheckpointArtifact:
    """A known inherited checkpoint item, explicitly excluded from run-call totals."""

    logical_id: str
    raw_model_route: str | None = None
    sha256: str | None = None
    attributable_to_baseline_run: bool = False

    def __post_init__(self) -> None:
        _required(self.logical_id, "logical_id")
        if self.logical_id.startswith("/") or ".." in self.logical_id.split("/"):
            raise ValueError("logical_id must be a safe relative identity")
        if self.raw_model_route is not None:
            _required(self.raw_model_route, "raw_model_route")
        if self.sha256 is not None:
            _sha256(self.sha256, "sha256")
        if self.attributable_to_baseline_run:
            raise ValueError("inherited checkpoint artifacts cannot be attributed to the baseline run")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenLineageInput:
    """Explicit run/checkpoint facts supplied by the caller's immutable baseline."""

    baseline_run_id: int | str
    run_model_route: str | None = None
    resume_requested: bool | None = None
    attributable_call_count: int | None = None
    inherited_checkpoint_artifacts: tuple[GoldenCheckpointArtifact, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.baseline_run_id, str):
            _required(self.baseline_run_id, "baseline_run_id")
        elif (
            isinstance(self.baseline_run_id, bool)
            or not isinstance(self.baseline_run_id, int)
            or self.baseline_run_id < 0
        ):
            raise ValueError("baseline_run_id must be a non-negative integer or non-blank string")
        if self.run_model_route is not None:
            _required(self.run_model_route, "run_model_route")
        if self.resume_requested is not None and not isinstance(self.resume_requested, bool):
            raise ValueError("resume_requested must be boolean or unknown")
        _non_negative(self.attributable_call_count, "attributable_call_count")
        object.__setattr__(
            self,
            "inherited_checkpoint_artifacts",
            tuple(self.inherited_checkpoint_artifacts),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenRunLineage:
    """Receipt showing exact task facts separately from aggregate/run assumptions."""

    baseline_run_id: int | str
    resume_requested: bool | None
    run_model_route: ModelRoute | None
    task_model_route: ModelRoute
    task_model_precedence: str
    attributable_call_count: int | None
    inherited_checkpoint_artifacts: tuple[GoldenCheckpointArtifact, ...]

    def __post_init__(self) -> None:
        if self.task_model_precedence != "task_request":
            raise ValueError("task_model_precedence must record task_request")
        _non_negative(self.attributable_call_count, "attributable_call_count")
        object.__setattr__(
            self,
            "inherited_checkpoint_artifacts",
            tuple(self.inherited_checkpoint_artifacts),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenTaskUsage:
    """Recorded telemetry; ``None`` means the historical receipt did not report it."""

    input_tokens: int | None
    output_tokens: int | None
    input_cache_read_tokens: int | None
    input_cache_miss_tokens: int | None
    latency_ms: int | None
    retries: int | None
    billed_cost_usd: float | None
    raw: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "input_cache_read_tokens",
            "input_cache_miss_tokens",
            "latency_ms",
            "retries",
        ):
            _non_negative(getattr(self, field_name), field_name)
        if self.billed_cost_usd is not None and (
            isinstance(self.billed_cost_usd, bool) or self.billed_cost_usd < 0
        ):
            raise ValueError("billed_cost_usd must be non-negative or unknown")
        object.__setattr__(self, "raw", freeze_json(self.raw))

    @property
    def runtime_usage(self) -> RuntimeUsage:
        """A lossless-as-possible bridge for the offline fake runtime.

        A cache miss is not assumed to be a cache write; the generic runtime
        field therefore remains unknown instead of fabricating a value.
        """

        return RuntimeUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.input_cache_read_tokens,
            cache_write_tokens=None,
            billed_cost_usd=self.billed_cost_usd,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenFakeRuntimeInput:
    """Offline-ready input for a fake scenario; it cannot start a provider call."""

    provider_id: str
    model_id: str
    usage: RuntimeUsage
    provider_latency_ms: int | None
    retry_count: int | None

    def __post_init__(self) -> None:
        _required(self.provider_id, "provider_id")
        _required(self.model_id, "model_id")
        _non_negative(self.provider_latency_ms, "provider_latency_ms")
        _non_negative(self.retry_count, "retry_count")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenParityRequest:
    """The normalized semantic task input, independent of any agent runtime."""

    task_id: str
    semantic_prompt: str
    context_bundle: str
    output_contract: Mapping[str, JsonValue]
    task_model_route: ModelRoute
    model_options: Mapping[str, JsonValue]
    lineage: GoldenRunLineage
    artifact_hashes: Mapping[str, str]
    output_contract_sha256: str

    def __post_init__(self) -> None:
        _required(self.task_id, "task_id")
        if not self.semantic_prompt or not self.context_bundle:
            raise ValueError("semantic_prompt and context_bundle must not be empty")
        expected = {"prompt", "context", "request", "response"}
        if set(self.artifact_hashes) != expected:
            raise ValueError("artifact_hashes must contain exactly prompt, context, request, response")
        for name, digest in self.artifact_hashes.items():
            _sha256(digest, f"artifact_hashes[{name}]")
        _sha256(self.output_contract_sha256, "output_contract_sha256")
        object.__setattr__(self, "output_contract", freeze_json(self.output_contract))
        object.__setattr__(self, "model_options", freeze_json(self.model_options))
        object.__setattr__(self, "artifact_hashes", MappingProxyType(dict(self.artifact_hashes)))

    @property
    def provider_id(self) -> str:
        return self.task_model_route.provider_id

    @property
    def model_id(self) -> str:
        return self.task_model_route.model_id

    @property
    def canonical_model_route(self) -> str:
        return self.task_model_route.canonical_route

    @property
    def raw_provider_route(self) -> str:
        return self.task_model_route.raw_provider_route


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenParityReceipt:
    """Immutable imported baseline receipt and safe fake-runtime projection."""

    request: GoldenParityRequest
    baseline_response: Mapping[str, JsonValue]
    baseline_usage: GoldenTaskUsage
    aggregate_receipt_sha256: str
    fake_runtime: GoldenFakeRuntimeInput

    def __post_init__(self) -> None:
        _sha256(self.aggregate_receipt_sha256, "aggregate_receipt_sha256")
        object.__setattr__(self, "baseline_response", freeze_json(self.baseline_response))

    @property
    def source_hashes(self) -> Mapping[str, str]:
        """Compatibility alias for callers interested only in the four input hashes."""

        return self.request.artifact_hashes
