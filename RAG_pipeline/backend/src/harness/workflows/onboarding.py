"""Resumable LangGraph workflow for project/source onboarding."""

from __future__ import annotations

import asyncio
import time
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
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
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    SUPPORTED_PROJECT_PLATFORMS,
    TRIGGER_SCAN_CAPABILITY,
)

_PLATFORM_PROMPT = (
    "Which platform is this project's codebase: "
    + ", ".join(SUPPORTED_PROJECT_PLATFORMS)
    + "? Use 'generic' for anything that is not a Talend, Workato, or Lobster export."
)


class OnboardingRequest(BaseModel):
    workflow_run_id: int
    project_id: str
    repo_url: str | None = None
    platform: str = "generic"
    installation_id: int | None = None
    upload_receipt: str | None = Field(default=None, min_length=32, max_length=4096)
    selected_pack_ids: list[str] = Field(default_factory=list)
    thread_id: str | None = None


class OnboardingResult(BaseModel):
    status: str
    thread_id: str
    current_node: str
    activity: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[CapabilityObservation] = Field(default_factory=list)
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_id: str | None = None
    interrupt_prompt: str | None = None
    interrupt_fields: list[str] = Field(default_factory=list)


class _OnboardingState(TypedDict, total=False):
    project_id: str
    repo_url: str | None
    upload_receipt: str | None
    selected_pack_ids: list[str]
    source_type: str
    platform: str
    installation_id: int | None
    activity: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    artifact_links: list[dict[str, Any]]
    status: str
    current_node: str
    project_status: dict[str, Any]


class ProjectOnboardingWorkflow:
    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        scan_wait_timeout: float = 30.0,
        scan_poll_interval: float = 0.5,
    ) -> None:
        self._checkpointer = checkpointer or InMemorySaver()
        self._scan_wait_timeout = scan_wait_timeout
        self._scan_poll_interval = scan_poll_interval

    async def run(
        self,
        request: OnboardingRequest,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> OnboardingResult:
        thread_id = request.thread_id or f"harness-onboarding-{request.workflow_run_id}"
        graph = self._build_graph(executor, context)
        state: _OnboardingState = {
            "project_id": request.project_id,
            "repo_url": request.repo_url,
            "platform": request.platform,
            "installation_id": request.installation_id,
            "upload_receipt": request.upload_receipt,
            "selected_pack_ids": _selected_pack_ids(request.selected_pack_ids),
            "activity": [],
            "observations": [],
            "artifact_links": [],
            "status": "running",
        }
        final = await graph.ainvoke(state, {"configurable": {"thread_id": thread_id}})
        return _result(final, thread_id)

    async def resume(
        self,
        request: OnboardingRequest,
        *,
        response: dict[str, Any],
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> OnboardingResult:
        thread_id = request.thread_id or f"harness-onboarding-{request.workflow_run_id}"
        graph = self._build_graph(executor, context)
        final = await graph.ainvoke(
            Command(resume=response), {"configurable": {"thread_id": thread_id}}
        )
        return _result(final, thread_id)

    def _build_graph(
        self,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ):
        builder = StateGraph(_OnboardingState)

        async def collect_source(state: _OnboardingState) -> dict[str, Any]:
            repo_url = state.get("repo_url")
            upload_receipt = state.get("upload_receipt")
            platform = _normalized_platform(state.get("platform"))
            source_type = "zip" if upload_receipt else "git" if repo_url else ""
            if not source_type:
                response = interrupt(
                    {
                        "kind": "clarification",
                        "prompt": "Choose a Git repository or upload a ZIP source for this project.",
                        "required_fields": ["source_type"],
                        "options": ["git", "zip"],
                        "default_platform": platform or "generic",
                    }
                )
                if isinstance(response, dict):
                    source_type = str(response.get("source_type") or "").lower()
                    repo_url = response.get("repo_url") if source_type == "git" else None
                    upload_receipt = response.get("upload_receipt") if source_type == "zip" else None
                    platform = _normalized_platform(response.get("platform")) or platform
            if source_type == "git" and not repo_url:
                response = interrupt(
                    {
                        "kind": "clarification",
                        "prompt": "Which Git repository should Nexus use for this project?",
                        "required_fields": ["repo_url"],
                    }
                )
                if isinstance(response, dict):
                    repo_url = response.get("repo_url")
            if source_type == "zip" and not upload_receipt:
                response = interrupt(
                    {
                        "kind": "clarification",
                        "prompt": "Upload a ZIP source to continue this project onboarding.",
                        "required_fields": ["upload_receipt"],
                    }
                )
                if isinstance(response, dict):
                    upload_receipt = response.get("upload_receipt")
            if source_type not in {"git", "zip"} or (source_type == "git" and not repo_url) or (
                source_type == "zip" and not upload_receipt
            ):
                return {
                    "status": "failed",
                    "current_node": "collect_source",
                    "activity": [
                        *state.get("activity", []),
                        _activity("onboarding.source_missing", "A project source is required", "collect_source", status="failed"),
                    ],
                }
            if platform is None:
                # An unsupported platform (e.g. "github") must become a
                # question, not a terminal provider rejection downstream.
                response = interrupt(
                    {
                        "kind": "clarification",
                        "prompt": _PLATFORM_PROMPT,
                        "required_fields": ["platform"],
                        "options": list(SUPPORTED_PROJECT_PLATFORMS),
                    }
                )
                if isinstance(response, dict):
                    platform = _normalized_platform(response.get("platform"))
                if platform is None:
                    return {
                        "repo_url": repo_url,
                        "platform": "generic",
                        "status": "failed",
                        "current_node": "collect_source",
                        "activity": [
                            *state.get("activity", []),
                            _activity(
                                "onboarding.invalid_platform",
                                "Unsupported platform; allowed values: "
                                + ", ".join(SUPPORTED_PROJECT_PLATFORMS),
                                "collect_source",
                                status="failed",
                            ),
                        ],
                    }
            return {
                "repo_url": repo_url,
                "upload_receipt": upload_receipt,
                "source_type": source_type,
                "platform": platform,
                "current_node": "collect_source",
                "activity": [
                    *state.get("activity", []),
                    _activity("onboarding.source_resolved", "Resolved project source", "collect_source"),
                ],
            }

        async def attach_source(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "attach_source"}
            if state.get("source_type") == "zip":
                observation = await executor.execute(
                    CapabilityExecutionRequest(
                        capability_id=ATTACH_ZIP_SOURCE_CAPABILITY.id,
                        arguments={"upload_receipt": state["upload_receipt"]},
                        idempotency_key=f"onboard:{state['project_id']}:attach-zip",
                    ),
                    context,
                )
            else:
                observation = await executor.execute(
                    CapabilityExecutionRequest(
                        capability_id=ATTACH_GIT_SOURCE_CAPABILITY.id,
                        arguments={
                            "project_id": state["project_id"],
                            "repo_url": state["repo_url"],
                            "platform": state["platform"],
                            "installation_id": state.get("installation_id"),
                        },
                        idempotency_key=f"onboard:{state['project_id']}:attach-git",
                    ),
                    context,
                )
            update = _observation_update(state, observation, "attach_source")
            if observation.succeeded and observation.data.get("project_id") != state["project_id"]:
                update["status"] = "failed"
                update["activity"] = [
                    *update["activity"],
                    _activity("onboarding.receipt_mismatch", "Uploaded source belongs to another project", "attach_source", status="failed"),
                ]
            if observation.succeeded and observation.data.get("platform"):
                update["platform"] = str(observation.data["platform"])
            return update

        async def trigger_scan(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "trigger_scan"}
            observation = await executor.execute(
                CapabilityExecutionRequest(
                    capability_id=TRIGGER_SCAN_CAPABILITY.id,
                    arguments={"project_id": state["project_id"]},
                    idempotency_key=f"onboard:{state['project_id']}:scan",
                ),
                context,
            )
            return _observation_update(state, observation, "trigger_scan")

        async def generate_docs(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "generate_docs"}
            observation = await executor.execute(
                CapabilityExecutionRequest(
                    capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
                    arguments={
                        "project_id": state["project_id"],
                        "resume": True,
                        "selected_pack_ids": state.get("selected_pack_ids", []),
                    },
                    idempotency_key=f"onboard:{state['project_id']}:docs",
                ),
                context,
            )
            return _observation_update(state, observation, "generate_docs")

        async def wait_for_docs(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "wait_for_docs"}
            deadline = time.monotonic() + self._scan_wait_timeout
            while True:
                observation = await executor.execute(
                    CapabilityExecutionRequest(
                        capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
                        arguments={"project_id": state["project_id"]},
                    ),
                    context,
                )
                if not observation.succeeded:
                    return _observation_update(state, observation, "wait_for_docs")
                status = str(observation.data.get("status") or "").lower()
                if status in {"completed", "succeeded", "ready"}:
                    return _observation_update(state, observation, "wait_for_docs")
                if status in {"failed", "error", "cancelled"}:
                    failed = observation.model_copy(update={"status": "failed"})
                    return _observation_update(state, failed, "wait_for_docs")
                if time.monotonic() >= deadline:
                    timeout_observation = CapabilityObservation(
                        capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
                        status="failed",
                        output_type=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.output_type,
                        summary="Documentation generation did not complete in time.",
                        error_message="Documentation generation did not complete in time.",
                    )
                    return _observation_update(state, timeout_observation, "wait_for_docs")
                await asyncio.sleep(self._scan_poll_interval)

        async def wait_for_scan(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "wait_for_scan"}
            deadline = time.monotonic() + self._scan_wait_timeout
            while True:
                observation = await executor.execute(
                    CapabilityExecutionRequest(
                        capability_id=PROJECT_STATUS_CAPABILITY.id,
                        arguments={"project_id": state["project_id"]},
                    ),
                    context,
                )
                if not observation.succeeded:
                    return _observation_update(state, observation, "wait_for_scan")
                project_status = str(observation.data.get("status") or "").lower()
                if project_status in {"ready", "completed"}:
                    update = _observation_update(state, observation, "wait_for_scan")
                    update["project_status"] = dict(observation.data)
                    return update
                if time.monotonic() >= deadline:
                    timeout_observation = CapabilityObservation(
                        capability_id=PROJECT_STATUS_CAPABILITY.id,
                        status="failed",
                        output_type=PROJECT_STATUS_CAPABILITY.output_type,
                        summary="The project scan did not become ready in time.",
                        error_message="The project scan did not become ready in time.",
                    )
                    return _observation_update(state, timeout_observation, "wait_for_scan")
                await asyncio.sleep(self._scan_poll_interval)

        async def publish_index(state: _OnboardingState) -> dict[str, Any]:
            if state.get("status") == "failed":
                return {"current_node": "publish_index"}
            observation = await executor.execute(
                CapabilityExecutionRequest(
                    capability_id=PUBLISH_INDEX_CAPABILITY.id,
                    arguments={"project_id": state["project_id"]},
                    idempotency_key=f"onboard:{state['project_id']}:index",
                ),
                context,
            )
            return _observation_update(state, observation, "publish_index")

        async def finalize(state: _OnboardingState) -> dict[str, Any]:
            return {
                "current_node": "finalize",
                "activity": [
                    *state.get("activity", []),
                    _activity(
                        "onboarding.completed" if state.get("status") != "failed" else "onboarding.failed",
                        "Project onboarding started" if state.get("status") != "failed" else "Project onboarding failed",
                        "finalize",
                        status="completed" if state.get("status") != "failed" else "failed",
                    ),
                ],
            }

        for name, node in (
            ("collect_source", collect_source),
            ("attach_source", attach_source),
            ("trigger_scan", trigger_scan),
            ("wait_for_scan", wait_for_scan),
            ("generate_docs", generate_docs),
            ("wait_for_docs", wait_for_docs),
            ("publish_index", publish_index),
            ("finalize", finalize),
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "collect_source")
        builder.add_edge("collect_source", "attach_source")
        builder.add_edge("attach_source", "trigger_scan")
        builder.add_edge("trigger_scan", "wait_for_scan")
        builder.add_edge("wait_for_scan", "generate_docs")
        builder.add_edge("generate_docs", "wait_for_docs")
        builder.add_edge("wait_for_docs", "publish_index")
        builder.add_edge("publish_index", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self._checkpointer)


def _observation_update(
    state: _OnboardingState,
    observation: CapabilityObservation,
    node: str,
) -> dict[str, Any]:
    return {
        "observations": [*state.get("observations", []), observation.model_dump()],
        "artifact_links": [*state.get("artifact_links", []), *observation.artifact_links],
        "status": observation.status if observation.succeeded else "failed",
        "current_node": node,
        "activity": [
            *state.get("activity", []),
            _activity(
                f"onboarding.{node}",
                observation.summary or node.replace("_", " ").title(),
                node,
                status="completed" if observation.succeeded else "failed",
            ),
        ],
    }


def normalize_project_platform(value: Any) -> str | None:
    """Return the canonical supported platform, or None when unsupported."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SUPPORTED_PROJECT_PLATFORMS else None


_normalized_platform = normalize_project_platform


def _activity(event_type: str, label: str, node: str, *, status: str = "completed") -> dict[str, Any]:
    return {
        "event_type": event_type,
        "label": label,
        "status": status,
        "runtime_node": node,
        "safe_details": {},
    }


def _selected_pack_ids(values: list[str]) -> list[str]:
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("selected_pack_ids must contain non-empty strings")
    return sorted(set(value.strip() for value in values))


def _result(final: dict[str, Any], thread_id: str) -> OnboardingResult:
    raw_interrupts = final.get("__interrupt__")
    pending = raw_interrupts[0] if isinstance(raw_interrupts, (list, tuple)) and raw_interrupts else None
    if isinstance(pending, Interrupt):
        value = pending.value if isinstance(pending.value, dict) else {"prompt": str(pending.value)}
        return OnboardingResult(
            status="interrupted",
            thread_id=thread_id,
            current_node="collect_source",
            activity=list(final.get("activity", [])),
            interrupt_id=pending.id,
            interrupt_prompt=str(value.get("prompt") or "Project source is required."),
            interrupt_fields=[str(item) for item in value.get("required_fields", [])],
        )
    return OnboardingResult(
        status=str(final.get("status") or "failed"),
        thread_id=thread_id,
        current_node=str(final.get("current_node") or "finalize"),
        activity=list(final.get("activity", [])),
        observations=[CapabilityObservation.model_validate(item) for item in final.get("observations", [])],
        artifact_links=list(final.get("artifact_links", [])),
    )


_DEFAULT_WORKFLOW: ProjectOnboardingWorkflow | None = None


def default_project_onboarding_workflow() -> ProjectOnboardingWorkflow:
    global _DEFAULT_WORKFLOW
    if _DEFAULT_WORKFLOW is None:
        from src.harness.checkpoints import DurableWorkflowCheckpointSaver

        _DEFAULT_WORKFLOW = ProjectOnboardingWorkflow(
            checkpointer=DurableWorkflowCheckpointSaver()
        )
    return _DEFAULT_WORKFLOW
