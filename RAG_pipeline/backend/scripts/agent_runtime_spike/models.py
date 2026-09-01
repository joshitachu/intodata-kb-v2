"""Strict, secret-free contracts for the isolated compatibility spike."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CANDIDATE_VERSIONS = {
    "langchain": "1.3.14",
    "langchain-openai": "1.3.5",
    "langgraph": "1.2.9",
    "langchain-core": "1.4.9",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KimiProfile(StrictModel):
    selected_model: Literal["kimi/kimi-k2.6"] = "kimi/kimi-k2.6"
    provider_model: Literal["kimi-k2.6"] = "kimi-k2.6"
    base_url: Literal["https://api.moonshot.ai/v1"] = "https://api.moonshot.ai/v1"
    temperature: Literal[0.6] = 0.6
    timeout_seconds: Literal[300.0] = 300.0
    tool_choice: Literal["auto"] = "auto"
    reasoning: dict[str, dict[str, Literal["disabled"]]] = Field(
        default_factory=lambda: {"thinking": {"type": "disabled"}}
    )
    max_tokens: Literal[2048] = 2048
    max_retries: Literal[0] = 0

    @model_validator(mode="after")
    def exact_reasoning_profile(self) -> "KimiProfile":
        if self.reasoning != {"thinking": {"type": "disabled"}}:
            raise ValueError("Kimi reasoning profile must exactly disable thinking")
        return self


KIMI_PROFILE = KimiProfile()


class SpikeRunIdentity(StrictModel):
    agent_run_id: str = Field(pattern=r"^spike-[a-z0-9-]{8,80}$")
    manifest_hash: str = Field(pattern=r"^[a-zA-Z0-9._:-]{3,128}$")
    model_profile: Literal["kimi/kimi-k2.6"] = "kimi/kimi-k2.6"
    runtime_release: Literal["slice-1-shadow-spike"] = "slice-1-shadow-spike"

    @property
    def checkpoint_namespace(self) -> str:
        return f"agent-runtime-spike:{self.manifest_hash}:{self.model_profile}"

    @property
    def runnable_config(self) -> dict[str, Any]:
        return {
            "configurable": {
                # LangGraph reserves checkpoint_ns for compiled subgraph paths.
                # Nexus's run namespace is therefore bound into the durable key.
                "thread_id": f"{self.checkpoint_namespace}:{self.agent_run_id}",
            },
            "metadata": {
                "agent_run_id": self.agent_run_id,
                "nexus_checkpoint_namespace": self.checkpoint_namespace,
                "manifest_hash": self.manifest_hash,
                "model_profile": self.model_profile,
                "runtime_release": self.runtime_release,
            },
        }


class SpikeEvent(StrictModel):
    event_id: str
    run_id: str
    sequence: int = Field(ge=1)
    kind: str
    safe_payload: dict[str, Any] = Field(default_factory=dict)


class SpikeExecution(StrictModel):
    identity: SpikeRunIdentity
    status: Literal["completed", "interrupted", "failed"]
    response: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    proposed_call_ids: list[str] = Field(default_factory=list)
    intercepted_call_ids: list[str] = Field(default_factory=list)
    events: list[SpikeEvent] = Field(default_factory=list)
    interrupted: bool = False
    resumed: bool = False
    checkpoint_namespace: str
    private_reasoning_persisted: bool = False
    unauthorized_effects: int = 0
    duplicate_effects: int = 0
    error_code: str | None = None

    @property
    def middleware_complete(self) -> bool:
        completed = [
            str(event.safe_payload.get("call_id") or "")
            for event in self.events
            if event.kind == "tool.completed"
        ]
        return (
            len(self.intercepted_call_ids) == len(set(self.intercepted_call_ids))
            and self.intercepted_call_ids == completed
            and all(call_id in self.proposed_call_ids for call_id in self.intercepted_call_ids)
        )

    @property
    def ordered_event_identity(self) -> bool:
        if not self.events:
            return False
        first = self.events[0].sequence
        return [event.sequence for event in self.events] == list(
            range(first, first + len(self.events))
        )


class AttemptResult(StrictModel):
    attempt_id: str
    category: Literal[
        "direct", "single_read", "two_tool", "typed_error", "streaming", "checkpoint"
    ]
    scenario_id: str
    completed: bool
    protocol_valid: bool
    middleware_complete: bool
    unauthorized_effects: int
    duplicate_effects: int
    checkpoint_identity_valid: bool
    stream_identity_valid: bool
    latency_ms: int = Field(ge=0)
    failure_code: str | None = None


class CompatibilityReport(StrictModel):
    report_version: Literal["1.0.0"] = "1.0.0"
    runtime_versions: dict[str, str]
    model_profile: KimiProfile
    credential_available: bool
    network_available: bool
    attempts: list[AttemptResult]
    verdict: Literal["GO", "NO_GO", "BLOCKED"]
    blockers: list[str] = Field(default_factory=list)
