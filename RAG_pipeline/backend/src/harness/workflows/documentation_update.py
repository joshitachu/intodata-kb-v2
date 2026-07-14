"""Resumable proposal/approval/publish workflow for documentation updates."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, interrupt
from pydantic import BaseModel, Field

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    HarnessCapabilityExecutor,
)
from src.harness.models import DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY


class DocumentationUpdateRequest(BaseModel):
    workflow_run_id: int
    project_id: str
    target_doc: str
    evidence_refs: list[str]
    evidence_text: str
    expected_base_sha: str
    section: str | None = None
    thread_id: str | None = None

    def executor_arguments(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "target_doc": self.target_doc,
            "evidence_refs": self.evidence_refs,
            "evidence_text": self.evidence_text,
            "expected_base_sha": self.expected_base_sha,
            "section": self.section,
            "workflow_run_id": str(self.workflow_run_id),
        }


class DocumentationUpdateResult(BaseModel):
    status: str
    thread_id: str
    current_node: str
    activity: list[dict[str, Any]] = Field(default_factory=list)
    observation: CapabilityObservation | None = None
    interrupt_id: str | None = None
    interrupt_prompt: str | None = None


class _UpdateState(TypedDict, total=False):
    arguments: dict[str, Any]
    proposal: dict[str, Any]
    activity: list[dict[str, Any]]
    observation: dict[str, Any]
    status: str
    current_node: str


class DocumentationUpdateWorkflow:
    def __init__(self, *, checkpointer: InMemorySaver | None = None) -> None:
        self._checkpointer = checkpointer or InMemorySaver()

    async def run(
        self,
        request: DocumentationUpdateRequest,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> DocumentationUpdateResult:
        thread_id = request.thread_id or f"harness-doc-update-{request.workflow_run_id}"
        final = await self._build_graph(executor, context).ainvoke(
            {"arguments": request.executor_arguments(), "activity": [], "status": "running"},
            {"configurable": {"thread_id": thread_id}},
        )
        return _result(final, thread_id)

    async def resume(
        self,
        request: DocumentationUpdateRequest,
        *,
        approved: bool,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> DocumentationUpdateResult:
        thread_id = request.thread_id or f"harness-doc-update-{request.workflow_run_id}"
        final = await self._build_graph(executor, context).ainvoke(
            Command(resume={"approved": approved}),
            {"configurable": {"thread_id": thread_id}},
        )
        return _result(final, thread_id)

    def _build_graph(
        self,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ):
        builder = StateGraph(_UpdateState)

        async def resolve(state: _UpdateState) -> dict[str, Any]:
            arguments = state["arguments"]
            proposal = {
                "project_id": arguments["project_id"],
                "target_doc": arguments["target_doc"],
                "section": arguments.get("section"),
                "evidence_refs": list(arguments.get("evidence_refs") or []),
                "expected_base_sha": arguments["expected_base_sha"],
            }
            return _update(state, "resolve", "documentation_update.scope_resolved", "Resolved target document and base version", proposal=proposal)

        async def propose(state: _UpdateState) -> dict[str, Any]:
            return _update(
                state,
                "propose",
                "documentation_update.proposal_created",
                "Prepared an evidence-backed documentation change proposal",
            )

        async def approve(state: _UpdateState) -> dict[str, Any]:
            if DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id in context.approvals:
                approved = True
            else:
                response = interrupt(
                    {
                        "kind": "approval",
                        "prompt": "Approve this evidence-backed documentation update?",
                        "proposal": state["proposal"],
                    }
                )
                approved = bool(response.get("approved")) if isinstance(response, dict) else False
            return _update(
                state,
                "approve",
                "documentation_update.approved" if approved else "documentation_update.declined",
                "Documentation update approved" if approved else "Documentation update declined",
                status="running" if approved else "cancelled",
            )

        async def publish(state: _UpdateState) -> dict[str, Any]:
            if state.get("status") == "cancelled":
                return {"current_node": "publish"}
            observation = await executor.execute(
                CapabilityExecutionRequest(
                    capability_id=DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id,
                    arguments=state["arguments"],
                    idempotency_key=f"doc-update:{state['arguments']['workflow_run_id']}",
                ),
                context.model_copy(
                    update={
                        "approvals": {
                            *context.approvals,
                            DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id,
                        }
                    }
                ),
            )
            return _update(
                state,
                "publish",
                "documentation_update.published" if observation.succeeded else "documentation_update.failed",
                observation.summary or "Documentation update provider returned no summary",
                observation=observation.model_dump(mode="json"),
                status="succeeded" if observation.succeeded else "failed",
            )

        async def finalize(state: _UpdateState) -> dict[str, Any]:
            return _update(
                state,
                "finalize",
                "documentation_update.completed",
                "Documentation update lifecycle completed",
            )

        for name, node in (
            ("resolve", resolve),
            ("propose", propose),
            ("approve", approve),
            ("publish", publish),
            ("finalize", finalize),
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "resolve")
        builder.add_edge("resolve", "propose")
        builder.add_edge("propose", "approve")
        builder.add_edge("approve", "publish")
        builder.add_edge("publish", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self._checkpointer)


def _update(
    state: _UpdateState,
    node: str,
    event_type: str,
    label: str,
    **changes: Any,
) -> dict[str, Any]:
    return {
        **changes,
        "current_node": node,
        "activity": [
            *state.get("activity", []),
            {
                "event_type": event_type,
                "label": label,
                "status": "completed" if changes.get("status") != "failed" else "failed",
                "runtime_node": node,
                "safe_details": {},
            },
        ],
    }


def _result(final: dict[str, Any], thread_id: str) -> DocumentationUpdateResult:
    raw_interrupts = final.get("__interrupt__")
    pending = raw_interrupts[0] if isinstance(raw_interrupts, (list, tuple)) and raw_interrupts else None
    if isinstance(pending, Interrupt):
        value = pending.value if isinstance(pending.value, dict) else {}
        return DocumentationUpdateResult(
            status="interrupted",
            thread_id=thread_id,
            current_node="approve",
            activity=list(final.get("activity", [])),
            interrupt_id=pending.id,
            interrupt_prompt=str(value.get("prompt") or "Approve this documentation update?"),
        )
    raw_observation = final.get("observation")
    return DocumentationUpdateResult(
        status=str(final.get("status") or "failed"),
        thread_id=thread_id,
        current_node=str(final.get("current_node") or "finalize"),
        activity=list(final.get("activity", [])),
        observation=(
            CapabilityObservation.model_validate(raw_observation)
            if isinstance(raw_observation, dict)
            else None
        ),
    )


_DEFAULT_WORKFLOW: DocumentationUpdateWorkflow | None = None


def default_documentation_update_workflow() -> DocumentationUpdateWorkflow:
    global _DEFAULT_WORKFLOW
    if _DEFAULT_WORKFLOW is None:
        _DEFAULT_WORKFLOW = DocumentationUpdateWorkflow()
    return _DEFAULT_WORKFLOW
