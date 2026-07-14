"""Bounded LangGraph workflow for multi-source evidence collection."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    HarnessCapabilityExecutor,
)


class EvidenceToolCall(BaseModel):
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class EvidenceWorkflowResult(BaseModel):
    status: str
    observations: list[CapabilityObservation]
    activity: list[dict[str, Any]]


class _EvidenceState(TypedDict, total=False):
    calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    activity: list[dict[str, Any]]
    status: str


class EvidenceInvestigationWorkflow:
    async def run(
        self,
        calls: list[EvidenceToolCall],
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
        max_calls: int = 5,
    ) -> EvidenceWorkflowResult:
        bounded = calls[:max_calls]
        builder = StateGraph(_EvidenceState)

        async def collect(state: _EvidenceState) -> dict[str, Any]:
            requests = [
                CapabilityExecutionRequest(
                    capability_id=call["capability_id"],
                    arguments=dict(call.get("arguments") or {}),
                    idempotency_key=f"evidence:{index}:{call['capability_id']}",
                )
                for index, call in enumerate(state["calls"])
            ]
            observations = await asyncio.gather(
                *(executor.execute(request, context) for request in requests)
            )
            return {
                "observations": [item.model_dump() for item in observations],
                "activity": [
                    {
                        "event_type": "evidence.capability_completed",
                        "label": "Collected evidence",
                        "status": "completed" if item.succeeded else "failed",
                        "summary": item.summary,
                        "runtime_node": "collect",
                        "safe_details": {
                            "evidence_count": len(item.data.get("items", []))
                            if isinstance(item.data.get("items"), list)
                            else 0
                        },
                    }
                    for item in observations
                ],
                "status": (
                    "succeeded" if any(item.succeeded for item in observations) else "failed"
                ),
            }

        async def finalize(state: _EvidenceState) -> dict[str, Any]:
            return {
                "activity": [
                    *state.get("activity", []),
                    {
                        "event_type": "evidence.workflow_completed",
                        "label": "Evidence collection completed",
                        "status": "completed" if state.get("status") == "succeeded" else "failed",
                        "runtime_node": "finalize",
                        "safe_details": {"source_count": len(state.get("observations", []))},
                    },
                ]
            }

        builder.add_node("collect", collect)
        builder.add_node("finalize", finalize)
        builder.add_edge(START, "collect")
        builder.add_edge("collect", "finalize")
        builder.add_edge("finalize", END)
        graph = builder.compile()
        final = await graph.ainvoke(
            {"calls": [call.model_dump() for call in bounded], "activity": []}
        )
        return EvidenceWorkflowResult(
            status=str(final.get("status") or "failed"),
            observations=[
                CapabilityObservation.model_validate(item)
                for item in final.get("observations", [])
            ],
            activity=list(final.get("activity", [])),
        )


_DEFAULT_WORKFLOW: EvidenceInvestigationWorkflow | None = None


def default_evidence_investigation_workflow() -> EvidenceInvestigationWorkflow:
    global _DEFAULT_WORKFLOW
    if _DEFAULT_WORKFLOW is None:
        _DEFAULT_WORKFLOW = EvidenceInvestigationWorkflow()
    return _DEFAULT_WORKFLOW
