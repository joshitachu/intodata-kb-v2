"""Stable data contracts shared by coding-agent provider adapters."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

InvestigationStatus = Literal["running", "completed", "incomplete", "cancelled", "failed"]
TerminalInvestigationStatus = Literal["completed", "incomplete", "cancelled", "failed"]


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class InvestigationFingerprints(BaseModel):
    """Immutable identities that make a checkpoint safe (or unsafe) to resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace: str = Field(pattern=r"^[a-f0-9]{64}$")
    fact_graph: str = Field(pattern=r"^[a-f0-9]{64}$")
    method: str = Field(pattern=r"^[a-f0-9]{64}$")
    packs: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def build(
        cls,
        *,
        goal: str,
        workspace_digest: str,
        fact_graph_digest: str,
        method_digest: str,
        pack_versions: dict[str, str] | None = None,
    ) -> InvestigationFingerprints:
        normalized_goal = " ".join(goal.split())
        return cls(
            goal=_digest(normalized_goal),
            workspace=_digest(workspace_digest),
            fact_graph=_digest(fact_graph_digest),
            method=_digest(method_digest),
            packs=_digest(pack_versions or {}),
        )

    def differences(self, other: InvestigationFingerprints) -> list[str]:
        return [
            name
            for name in ("goal", "workspace", "fact_graph", "method", "packs")
            if getattr(self, name) != getattr(other, name)
        ]


class ResourceCeiling(BaseModel):
    """Run-level circuit breakers; never evidence-selection or batching limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_time_seconds: int | None = Field(default=None, ge=1)
    processed_tokens: int | None = Field(default=None, ge=1)
    cost_usd: float | None = Field(default=None, gt=0)

    def reached_by(self, usage: UsageReceipt) -> list[str]:
        reasons: list[str] = []
        if (
            self.wall_time_seconds is not None
            and usage.wall_time_ms >= self.wall_time_seconds * 1000
        ):
            reasons.append("wall_time")
        if self.processed_tokens is not None and usage.processed_tokens >= self.processed_tokens:
            reasons.append("processed_tokens")
        if self.cost_usd is not None and usage.cost_usd >= self.cost_usd:
            reasons.append("cost_usd")
        return reasons


class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_resume: bool
    native_file_tools: bool
    structured_checkpoints: bool
    cancellation_receipts: bool
    detailed_usage: bool
    native_delegation: bool = False


class UsageReceipt(BaseModel):
    """Provider usage dimensions without conflating cache traffic with input."""

    model_config = ConfigDict(extra="forbid")

    uncached_input_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    processed_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tool_failures: int = Field(default=0, ge=0)
    model_turns: int = Field(default=0, ge=0)
    provider_time_ms: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    complete: bool = True
    note: str | None = None

    @model_validator(mode="after")
    def _derive_processed_tokens(self) -> UsageReceipt:
        self.processed_tokens = (
            self.uncached_input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )
        return self

    def merged(self, other: UsageReceipt) -> UsageReceipt:
        notes = [note for note in (self.note, other.note) if note]
        return UsageReceipt(
            uncached_input_tokens=self.uncached_input_tokens + other.uncached_input_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            tool_failures=self.tool_failures + other.tool_failures,
            model_turns=self.model_turns + other.model_turns,
            provider_time_ms=self.provider_time_ms + other.provider_time_ms,
            wall_time_ms=self.wall_time_ms + other.wall_time_ms,
            cost_usd=self.cost_usd + other.cost_usd,
            complete=self.complete and other.complete,
            note="; ".join(dict.fromkeys(notes)) or None,
        )


class InvestigationRequest(BaseModel):
    """One coherent goal, independent of provider-specific session mechanics."""

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    authorized_workspace: Path
    fact_graph: Path
    method_files: tuple[Path, ...] = ()
    tool_names: tuple[str, ...] = ()
    notebook_path: Path
    fingerprints: InvestigationFingerprints
    resource_ceiling: ResourceCeiling = Field(default_factory=ResourceCeiling)
    initial_scope_seed: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _paths_are_authorized(self) -> InvestigationRequest:
        root = self.authorized_workspace.resolve()
        for label, path in (("fact_graph", self.fact_graph), ("notebook_path", self.notebook_path)):
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{label} must be inside authorized_workspace") from exc
        for path in self.method_files:
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError("method_files must be inside authorized_workspace") from exc
        expected_goal = InvestigationFingerprints.build(
            goal=self.goal,
            workspace_digest="unused",
            fact_graph_digest="unused",
            method_digest="unused",
        ).goal
        if self.fingerprints.goal != expected_goal:
            raise ValueError("goal fingerprint does not match the normalized goal")
        return self


class InvestigationCheckpoint(BaseModel):
    """Durable provider-neutral notebook envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coding-agent-checkpoint-1"] = "coding-agent-checkpoint-1"
    investigation_id: str
    provider_id: str
    model: str
    status: InvestigationStatus = "running"
    fingerprints: InvestigationFingerprints
    session_id: str | None = None
    revision: int = Field(default=0, ge=0)
    state: dict[str, Any] = Field(default_factory=dict)
    discovered_scope: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    accepted_artifacts: list[str] = Field(default_factory=list)
    in_flight: dict[str, Any] | None = None
    usage: UsageReceipt = Field(default_factory=UsageReceipt)
    updated_at: float = Field(default_factory=time.time)


class InvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TerminalInvestigationStatus
    provider_id: str
    model: str
    output: str = ""
    checkpoint: InvestigationCheckpoint
    usage: UsageReceipt
    stop_reason: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "completed"
