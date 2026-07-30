from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
    OutputContract,
    RuntimeBudgets,
    RuntimeTaskEvent,
    RuntimeTaskRequest,
    redact_payload,
)
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime


def _request() -> RuntimeTaskRequest:
    prompt_ref = ArtifactReference("semantic-prompt", "a" * 64, "text/markdown", 42)
    return RuntimeTaskRequest(
        experiment_id="experiment-1",
        task_id="doc-09",
        idempotency_key="request-1",
        provider_id="provider-x",
        model_id="model-y",
        semantic_prompt="Write the requested document from the evidence.",
        semantic_prompt_sha256=prompt_ref.sha256,
        context_refs=(ArtifactReference("evidence", "b" * 64, "application/json", 8),),
        output_contract=OutputContract("09_EXECUTIVE_SUMMARY.md", "text/markdown", max_bytes=1000),
        allowed_tool_names=("nexus_experiment_read_evidence",),
        budgets=RuntimeBudgets(max_input_tokens=100, max_output_tokens=50, max_elapsed_ms=1000),
        deadline_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        required_capabilities=frozenset({"tools", "structured-output"}),
    )


def test_runtime_request_uses_generic_open_provider_model_and_evidence_contracts():
    request = _request()

    assert request.provider_id == "provider-x"
    assert request.model_id == "model-y"
    assert request.context_refs[0].logical_name == "evidence"
    assert request.output_contract.artifact_name == "09_EXECUTIVE_SUMMARY.md"
    assert "job" not in request.__dataclass_fields__
    assert "opencode" not in request.__class__.__module__.lower()


def test_event_and_helper_redact_sensitive_payload_keys():
    event = RuntimeTaskEvent(
        event_id="event-1",
        sequence=1,
        category="error",
        occurred_at=datetime.now(timezone.utc),
        payload={"Authorization": "not-for-ledger", "nested": {"api_key": "also-not"}},
    )

    assert event.payload == {"Authorization": "[REDACTED]", "nested": {"api_key": "[REDACTED]"}}
    assert redact_payload({"normal": "kept", "token": "hidden"}) == {
        "normal": "kept",
        "token": "[REDACTED]",
    }


def test_fake_runtime_satisfies_runtime_protocol():
    assert isinstance(FakeAgentRuntime(), AgentRuntime)
