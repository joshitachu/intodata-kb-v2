from __future__ import annotations

from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentLoopInput,
    AgentModelResult,
    BoundedAgentLoop,
)
from src.harness.executor import CapabilityExecutionContext, HarnessCapabilityExecutor
from src.harness.registry import base_capability_registry


async def test_agent_follow_up_reads_typed_runtime_scope_without_legacy_continuation() -> None:
    class RecordingModel:
        request = None

        async def decide(self, request):  # noqa: ANN001
            self.request = request
            return AgentModelResult(
                decision=AgentDecision(
                    action=AgentBoundaryAction.ANSWER,
                    content="I’ll use the selected project and the last two days.",
                )
            )

    model = RecordingModel()
    registry = base_capability_registry()
    result = await BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=model,
    ).run(
        AgentLoopInput(
            user_message="last 2 days",
            conversation_state={
                "objective": "Show failed jobs for project-a.",
                "referenced_entities": [
                    {
                        "entity_type": "project",
                        "stable_id": "project-a",
                        "project_id": "project-a",
                    }
                ],
                "requested_inputs": [
                    {
                        "field": "time_window",
                        "prompt": "Which time window should I inspect?",
                        "options": [],
                    }
                ],
                "active_tasks": [],
            },
            workflow_state=[],
        ),
        context=CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert result.stop_reason.value == "answered"
    assert model.request.conversation_state["referenced_entities"][0]["stable_id"] == (
        "project-a"
    )
    assert model.request.conversation_state["requested_inputs"][0]["field"] == (
        "time_window"
    )


def test_runtime_record_summary_names_its_own_project() -> None:
    """Runtime reads are ordered by recency across the whole authorized scope,
    so an unscoped question returns whichever project failed most recently.

    Observed live: a question about one estate was answered with a different
    project's failing jobs, named individually, with nothing indicating they
    belonged elsewhere. The id was already in the evidence item's metadata but
    absent from the prose the model reads, so nothing made attribution
    unavoidable.
    """
    from src.harness.evidence import _runtime_record_summary

    summary = _runtime_record_summary(
        {
            "project_id": "other-customer",
            "job_name": "Storingstool_Service_Job",
            "failed_at": "2026-08-23T22:17:00Z",
            "error_summary": "NullPointerException in tSetGlobalVar_3",
        }
    )

    assert "other-customer" in summary
    assert "Storingstool_Service_Job" in summary


def test_runtime_items_carry_the_row_project_over_the_requested_one() -> None:
    """A row's own project wins over the request argument, so a cross-project
    result cannot be relabelled as the project that was asked about."""
    from src.harness.evidence import _generic_runtime_items

    items = _generic_runtime_items(
        "detect_failures",
        [{"project_id": "other-customer", "job_name": "j1", "failed_at": "2026-08-23T22:17:00Z"}],
        "asked-about-this-one",
    )

    assert len(items) == 1
    assert items[0].metadata["project_id"] == "other-customer"
    assert "other-customer" in items[0].summary
