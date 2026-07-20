from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.generation.agent_loop import RuntimeProgressEvent, build_runtime_tool_summary
from src.harness.evidence import search_runtime_log_evidence
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.manifest import ManifestScope, build_runtime_capability_manifest
from src.harness.models import LOG_SEARCH_EVIDENCE_CAPABILITY
from src.harness.registry import base_capability_registry, default_capability_registry
from src.runtime.providers import RuntimeProviderContext
from src.runtime.providers.stored import StoredRuntimeProvider
from src.runtime.registry import SearchLogsInput


def test_log_search_is_runtime_safe_and_declaratively_guided() -> None:
    manifest = build_runtime_capability_manifest(default_capability_registry(), scope=ManifestScope())
    tool = next(tool for tool in manifest.tools if tool.id == LOG_SEARCH_EVIDENCE_CAPABILITY.id)

    assert tool.provider_type == "log_provider"
    assert tool.evidence_output is True
    assert tool.redaction_policy["credential_redaction"] == "required"
    assert tool.input_schema["additionalProperties"] is False
    assert any("log content" in item for item in tool.use_when)
    assert any("raw log dump" in item for item in tool.do_not_use_when)


async def test_log_search_is_denied_by_turn_policy_before_handler_runs() -> None:
    called = False

    async def handler(_arguments, _context):  # noqa: ANN001
        nonlocal called
        called = True
        return []

    executor = HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers={LOG_SEARCH_EVIDENCE_CAPABILITY.id: handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
            arguments={"question": "search logs for timeout", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            denied_capability_ids={LOG_SEARCH_EVIDENCE_CAPABILITY.id},
        ),
    )

    assert observation.status == "denied"
    assert called is False
    assert "payload" not in observation.model_dump_json()


async def test_log_search_rejects_an_unauthorized_project_before_handler_runs() -> None:
    called = False

    async def handler(_arguments, _context):  # noqa: ANN001
        nonlocal called
        called = True
        return []

    executor = HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers={LOG_SEARCH_EVIDENCE_CAPABILITY.id: handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
            arguments={"question": "search logs for timeout", "project_ids": ["project-b"]},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert observation.status == "denied"
    assert observation.error_message == "Project access required"
    assert called is False


async def test_stored_log_search_projects_to_redacted_bounded_snippets(monkeypatch) -> None:
    async def search_log_events(_self, _scope, **_kwargs):  # noqa: ANN001
        return [
            {
                "id": 99,
                "execution_id": "run-42",
                "project_id": "project-a",
                "job_name": "sync_orders",
                "timestamp": "2026-07-15T10:00:00+00:00",
                "severity": "error",
                "message": "api_token=super-secret " + ("x" * 800),
                "payload": {"authorization": "Bearer should-not-leak"},
            }
        ]

    monkeypatch.setattr(
        "src.runtime.providers.stored.RuntimeStoreRepository.search_log_events",
        search_log_events,
    )
    result = await StoredRuntimeProvider().search_logs(
        SearchLogsInput(question="token error"),
        RuntimeProviderContext(project_scope=["project-a"], db=SimpleNamespace()),
    )

    assert result[0]["execution_id"] == "run-42"
    assert "id" not in result[0]
    assert "payload" not in result[0]
    assert "super-secret" not in result[0]["snippet"]
    assert len(result[0]["snippet"]) <= 360


@pytest.mark.parametrize(
    ("authorized_scope", "requested_scope", "expected_scope"),
    [
        (None, None, None),
        (["project-a", "project-b"], ["project-a"], ["project-a"]),
        (["project-a", "project-b"], ["project-a", "project-b"], ["project-a", "project-b"]),
    ],
)
async def test_log_evidence_preserves_global_single_and_multi_project_scope(
    monkeypatch, authorized_scope, requested_scope, expected_scope
) -> None:
    seen: dict[str, object] = {}

    async def search_logs(_self, _params, context):  # noqa: ANN001
        seen["scope"] = context.project_scope
        return [
            {
                "execution_id": "run-42",
                "project_id": "project-a",
                "job_name": "sync_orders",
                "timestamp": "2026-07-15T10:00:00+00:00",
                "severity": "error",
                "snippet": "Connection timed out",
                "id": 99,
                "payload": {"raw": "not evidence"},
            }
        ]

    monkeypatch.setattr("src.harness.evidence.StoredRuntimeProvider.search_logs", search_logs)
    result = await search_runtime_log_evidence(
        question="search logs for timeout",
        project_scope=authorized_scope,
        project_ids=requested_scope,
        db=SimpleNamespace(),
    )

    assert seen["scope"] == expected_scope
    assert result.status == "succeeded"
    assert result.items[0].metadata == {
        "execution_ref": "run-42",
        "project_id": "project-a",
        "source_key": None,
        "severity": "error",
        "occurrence_count": 1,
    }
    assert "payload" not in result.items[0].model_dump_json()
    assert "id" not in result.items[0].metadata
    assert result.answer_draft and "search logs for timeout" in result.answer_draft


def test_log_search_snippets_never_enter_runtime_continuation_memory() -> None:
    summary = build_runtime_tool_summary([
        RuntimeProgressEvent(
            "runtime_tool_result",
            {
                "tool_name": "search_logs",
                "success": True,
                "data": [{"execution_id": "run-42", "snippet": "api_token=not-for-memory"}],
            },
        )
    ])

    assert summary == [{"tool_name": "search_logs", "execution_id": "run-42"}]
