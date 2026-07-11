"""Agent runtime provider boundary for Nexus harness orchestration."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    HarnessCapabilityExecutor,
)
from src.harness.manifest import RuntimeCapabilityManifest


class RuntimeTurnRequest(BaseModel):
    """Runtime request created by the Nexus orchestrator API."""

    message: str
    manifest: RuntimeCapabilityManifest
    planned_capability_id: str | None = None
    arguments: dict = Field(default_factory=dict)
    workflow_run_id: int | None = None
    thread_id: str | None = None
    step_limit: int = Field(default=10, ge=2, le=100)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300.0)


class RuntimeTurnResult(BaseModel):
    """Sanitized runtime result returned to Nexus."""

    runtime_provider: str
    status: str
    selected_capability_id: str | None = None
    observation: CapabilityObservation | None = None
    activity: list[dict] = Field(default_factory=list)
    final_summary: str | None = None
    thread_id: str | None = None
    runtime_run_id: str | None = None
    current_node: str | None = None
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRuntimeProvider(Protocol):
    """Minimal provider interface shared by LangGraph/OpenCode adapters."""

    provider_id: str

    async def run_turn(
        self,
        request: RuntimeTurnRequest,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> RuntimeTurnResult:
        """Run one orchestrator turn using only Nexus executor calls."""


class LangGraphRuntimeProvider:
    """LangGraph-backed runtime boundary for Nexus harness workflows."""

    provider_id = "langgraph.nexus"

    def __init__(self, *, checkpointer: InMemorySaver | None = None) -> None:
        self._checkpointer = checkpointer or InMemorySaver()

    async def run_turn(
        self,
        request: RuntimeTurnRequest,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> RuntimeTurnResult:
        thread_id = request.thread_id or _thread_id(request)
        runtime_run_id = f"lg-run-{uuid.uuid4().hex[:16]}"
        graph = self._build_graph(executor=executor, context=context)
        state: _LangGraphHarnessState = {
            "message": request.message,
            "manifest": request.manifest,
            "planned_capability_id": request.planned_capability_id,
            "arguments": dict(request.arguments),
            "activity": [],
            "runtime_run_id": runtime_run_id,
        }
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": request.step_limit,
        }
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(
                    state,
                    config,
                ),
                timeout=request.timeout_seconds,
            )
        except TimeoutError:
            return _runtime_failure_result(
                provider_id=self.provider_id,
                thread_id=thread_id,
                runtime_run_id=runtime_run_id,
                current_node="timeout",
                error_type="timeout",
                message=f"LangGraph runtime timed out after {request.timeout_seconds:g} seconds.",
                request=request,
            )
        except Exception as exc:
            return _runtime_failure_result(
                provider_id=self.provider_id,
                thread_id=thread_id,
                runtime_run_id=runtime_run_id,
                current_node="runtime_error",
                error_type=exc.__class__.__name__,
                message="LangGraph runtime failed before completing the workflow.",
                request=request,
            )
        snapshot = graph.get_state(config)
        metadata = _runtime_metadata(snapshot, request=request)
        observation = None
        observation_data = final_state.get("observation")
        if isinstance(observation_data, dict):
            observation = CapabilityObservation(**observation_data)
        return RuntimeTurnResult(
            runtime_provider=self.provider_id,
            status=str(final_state.get("status") or "failed"),
            selected_capability_id=final_state.get("selected_capability_id"),
            observation=observation,
            activity=list(final_state.get("activity") or []),
            final_summary=final_state.get("final_summary"),
            thread_id=thread_id,
            runtime_run_id=runtime_run_id,
            current_node=str(final_state.get("current_node") or "finalize"),
            runtime_metadata=metadata,
        )

    def _build_graph(
        self,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ):
        builder = StateGraph(_LangGraphHarnessState)

        async def load_manifest(state: _LangGraphHarnessState) -> dict[str, Any]:
            activity = _append_activity(
                state,
                event_type="runtime.manifest_loaded",
                label="Loaded runtime-safe capability manifest",
                runtime_node="load_manifest",
                safe_details={"tool_count": state["manifest"].total},
            )
            return {"activity": activity, "current_node": "load_manifest"}

        async def select_capability(state: _LangGraphHarnessState) -> dict[str, Any]:
            selected = _select_capability_from_manifest(
                state["manifest"],
                state.get("planned_capability_id"),
            )
            if selected is None:
                activity = _append_activity(
                    state,
                    event_type="runtime.interrupted",
                    label="Runtime needs clarification",
                    runtime_node="select_capability",
                    status="skipped",
                    summary="No supported capability could be selected from the runtime manifest.",
                )
                return {
                    "activity": activity,
                    "current_node": "select_capability",
                    "status": "interrupted",
                    "final_summary": "I need a supported capability before I can continue.",
                }
            tool = next(tool for tool in state["manifest"].tools if tool.id == selected)
            activity = _append_activity(
                state,
                event_type="runtime.capability_selected",
                label="Selected capability",
                runtime_node="select_capability",
                safe_details={"capability": tool.label},
            )
            return {
                "activity": activity,
                "current_node": "select_capability",
                "selected_capability_id": selected,
            }

        async def execute_capability(state: _LangGraphHarnessState) -> dict[str, Any]:
            selected = state.get("selected_capability_id")
            if selected is None:
                return {"current_node": "execute_capability"}
            request = CapabilityExecutionRequest(
                capability_id=selected,
                arguments=dict(state.get("arguments") or {}),
                idempotency_key=str(state.get("runtime_run_id") or ""),
            )
            activity = _append_activity(
                state,
                event_type="runtime.executor_request",
                label="Calling Nexus executor",
                runtime_node="execute_capability",
                status="running",
                safe_details={"capability": _tool_label(state["manifest"], selected)},
            )
            observation = await executor.execute(request, context)
            activity = [
                *activity,
                _activity(
                    event_type="runtime.observation",
                    label="Received capability observation",
                    runtime_node="execute_capability",
                    status="succeeded" if observation.succeeded else observation.status,
                    summary=observation.summary,
                    safe_details={"status": observation.status},
                ),
            ]
            return {
                "activity": activity,
                "current_node": "execute_capability",
                "observation": observation.model_dump(),
                "status": observation.status,
            }

        async def finalize(state: _LangGraphHarnessState) -> dict[str, Any]:
            observation = state.get("observation")
            summary = None
            if isinstance(observation, dict):
                summary = observation.get("summary")
            status = str(state.get("status") or "interrupted")
            activity = _append_activity(
                state,
                event_type="runtime.completed" if status in {"succeeded", "triggered"} else "runtime.paused",
                label="Graph workflow completed" if status in {"succeeded", "triggered"} else "Graph workflow paused",
                runtime_node="finalize",
                status="succeeded" if status in {"succeeded", "triggered"} else status,
                summary=summary,
            )
            return {
                "activity": activity,
                "current_node": "finalize",
                "final_summary": summary or state.get("final_summary"),
            }

        builder.add_node("load_manifest", load_manifest)
        builder.add_node("select_capability", select_capability)
        builder.add_node("execute_capability", execute_capability)
        builder.add_node("finalize", finalize)
        builder.add_edge(START, "load_manifest")
        builder.add_edge("load_manifest", "select_capability")
        builder.add_edge("select_capability", "execute_capability")
        builder.add_edge("execute_capability", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self._checkpointer)


class _LangGraphHarnessState(TypedDict, total=False):
    message: str
    manifest: RuntimeCapabilityManifest
    planned_capability_id: str | None
    selected_capability_id: str | None
    arguments: dict[str, Any]
    observation: dict[str, Any]
    activity: list[dict[str, Any]]
    status: str
    final_summary: str | None
    current_node: str
    runtime_run_id: str


def _select_capability_from_manifest(
    manifest: RuntimeCapabilityManifest,
    planned_capability_id: str | None,
) -> str | None:
    available_ids = {tool.id for tool in manifest.tools if tool.available}
    if planned_capability_id in available_ids:
        return planned_capability_id
    if len(available_ids) == 1:
        return next(iter(available_ids))
    return None


def _thread_id(request: RuntimeTurnRequest) -> str:
    if request.workflow_run_id is not None:
        return f"harness-run-{request.workflow_run_id}"
    return f"harness-thread-{uuid.uuid4().hex[:16]}"


def _append_activity(
    state: _LangGraphHarnessState,
    *,
    event_type: str,
    label: str,
    runtime_node: str,
    status: str = "succeeded",
    summary: str | None = None,
    safe_details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        *list(state.get("activity") or []),
        _activity(
            event_type=event_type,
            label=label,
            runtime_node=runtime_node,
            status=status,
            summary=summary,
            safe_details=safe_details,
        ),
    ]


def _activity(
    *,
    event_type: str,
    label: str,
    runtime_node: str,
    status: str = "succeeded",
    summary: str | None = None,
    safe_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "label": label,
        "status": status,
        "summary": summary,
        "runtime_node": runtime_node,
        "safe_details": safe_details or {},
    }


def _tool_label(manifest: RuntimeCapabilityManifest, capability_id: str) -> str:
    for tool in manifest.tools:
        if tool.id == capability_id:
            return tool.label
    return "selected capability"


def _runtime_metadata(snapshot: Any, *, request: RuntimeTurnRequest) -> dict[str, Any]:
    config = getattr(snapshot, "config", {}) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    metadata = getattr(snapshot, "metadata", {}) or {}
    return {
        "graph": "docs_generation",
        "checkpoint_id": configurable.get("checkpoint_id"),
        "checkpoint_ns": configurable.get("checkpoint_ns", ""),
        "step": metadata.get("step") if isinstance(metadata, dict) else None,
        "budget": {
            "step_limit": request.step_limit,
            "timeout_seconds": request.timeout_seconds,
        },
    }


def _runtime_failure_result(
    *,
    provider_id: str,
    thread_id: str,
    runtime_run_id: str,
    current_node: str,
    error_type: str,
    message: str,
    request: RuntimeTurnRequest,
) -> RuntimeTurnResult:
    return RuntimeTurnResult(
        runtime_provider=provider_id,
        status="failed",
        activity=[
            _activity(
                event_type="runtime.failed",
                label="Graph runtime failed",
                runtime_node=current_node,
                status="failed",
                summary=message,
                safe_details={"error_type": error_type},
            )
        ],
        final_summary=message,
        thread_id=thread_id,
        runtime_run_id=runtime_run_id,
        current_node=current_node,
        runtime_metadata={
            "graph": "docs_generation",
            "error": {"type": error_type},
            "budget": {
                "step_limit": request.step_limit,
                "timeout_seconds": request.timeout_seconds,
            },
        },
    )
