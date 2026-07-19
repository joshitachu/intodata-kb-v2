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
