"""Multi-session OpenCode documentation candidate with bounded subagents."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import json
from pathlib import Path, PurePosixPath
import re
from time import monotonic
from typing import Any

from mcp_server.experiments.application.fact_ledger import validate_document_claims
from mcp_server.experiments.application.grounded_pack import (
    GroundedExecutionPack,
    grounded_pack_payload,
)
from mcp_server.experiments.application.grounded_preflight import (
    preflight_grounded_sources,
    reject_secret_like_output,
)
from mcp_server.experiments.application.run_agentic_legacy_candidate import (
    _INTERMEDIATE_OUTPUTS,
    _prepare_agentic_workspace,
    _read_candidate_outputs,
    _tool_summary,
)
from mcp_server.experiments.application.run_legacy_replay_candidate import (
    _REPLAY_TASKS,
    _require_phase_one_skip,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
    _has_completed_assistant,
    _latest_assistant,
    _usage,
)
from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeHttpClient,
    OpenCodeSession,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


_ORCHESTRATION_OUTPUTS = (
    ("execution_plan.json", "application/json", "orchestration-plan"),
    ("evidence_registry.json", "application/json", "evidence-registry"),
    ("coverage_ledger.json", "application/json", "coverage-ledger"),
    ("review_report.md", "text/markdown", "review"),
)
_REQUIRED_JSON_FIELDS = {
    "execution_plan.json": {
        "source_types",
        "canonical_units",
        "partitions",
        "agent_assignments",
        "document_owners",
        "acceptance_checks",
    },
    "evidence_registry.json": {"claims"},
    "coverage_ledger.json": {
        "canonical_units",
        "concerns",
        "document_coverage",
        "gaps",
    },
}
_REQUIRED_CLAIM_FIELDS = {
    "claim_id",
    "statement",
    "scope",
    "evidence_locators",
    "confidence",
    "contradictions",
    "used_by_documents",
}
_REQUIRED_REVIEW_SECTIONS = (
    "Factual review",
    "Coverage review",
    "Cross-document consistency",
    "Secret and evidence-path review",
    "Resolved findings",
    "Final verdict",
)
_MINIMUM_CHILD_SESSIONS = 3
_GROUNDED_MINIMUM_CHILD_SESSIONS = 5
_GROUNDED_MAXIMUM_CHILD_SESSIONS = 8
_GROUNDED_FACT_LEDGER = "FACT_LEDGER.json"
_GROUNDED_CLAIM_REPORT = "deterministic_claim_report.json"


class OrchestratedOpenCodeRunner:
    """Use one primary OpenCode orchestrator and bounded specialist child sessions."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        project_root: Path,
        workspace_root: Path,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
        timeout_seconds: int = 3600,
        grounded: bool = False,
        execution_pack: GroundedExecutionPack | None = None,
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root)
        self._runtime_workspace_root = Path(runtime_workspace_root)
        self._timeout_seconds = timeout_seconds
        self._grounded = grounded
        self._execution_pack = execution_pack

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
        output_names: tuple[str, ...],
    ) -> QuickCandidateResult:
        if not source_items:
            raise QuickCandidateError(
                "orchestrated candidate requires frozen scanner sources"
            )
        fact_ledger: dict[str, Any] | None = None
        source_preflight: dict[str, Any] | None = None
        if self._grounded:
            if self._execution_pack is None:
                raise QuickCandidateError(
                    "grounded candidate requires a certified execution pack"
                )
            pack_documents = tuple(
                path.removeprefix("candidate/docs/")
                for path in self._execution_pack.owned_artifacts
                if path.startswith("candidate/docs/")
            )
            if output_names != pack_documents:
                raise QuickCandidateError(
                    "grounded output contract does not match recipe ownership"
                )
            fact_ledger, source_preflight = await asyncio.to_thread(
                preflight_grounded_sources,
                source_items,
                max_source_items=self._execution_pack.max_source_items,
                max_source_bytes=self._execution_pack.max_source_bytes,
            )
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError("OpenCode runtime version is not pinned to 1.18.4")
        docs_root = self._project_root / "output" / project_id / "docs"
        if not self._grounded or self._execution_pack.workflow_mode == "legacy_replay":
            _require_phase_one_skip(docs_root)
        if not self._grounded:
            expected_outputs = tuple(
                task.output_name for task in _REPLAY_TASKS if task.output_name is not None
            )
            if output_names != expected_outputs:
                raise QuickCandidateError(
                    "orchestrated output contract does not match the preserved Phase 3 run"
                )

        workspace, input_receipts = await asyncio.to_thread(
            _prepare_agentic_workspace,
            workspace_root=self._workspace_root,
            experiment_id=experiment_id,
            source_items=source_items,
            docs_root=docs_root,
            output_names=output_names,
            orchestrated=True,
            grounded=self._grounded,
            grounded_pack=self._execution_pack,
            fact_ledger=fact_ledger,
            source_preflight=source_preflight,
        )
        started = monotonic()
        execution_label = "grounded" if self._grounded else "orchestrated"
        primary_agent = (
            "nexus-doc-grounded-orchestrator"
            if self._grounded
            else "nexus-doc-orchestrator"
        )
        parent = await self._client.create_session(
            title=f"Nexus {execution_label} documentation {project_id} {experiment_id}",
            directory=str(self._runtime_workspace_root / experiment_id),
        )
        await self._client.prompt_async(
            parent.session_id,
            payload={
                "model": {"providerID": provider_id, "modelID": model_id},
                "agent": primary_agent,
                "parts": [
                    {
                        "type": "text",
                        "text": (
                            _grounded_orchestrator_prompt()
                            if self._grounded
                            else _orchestrator_prompt()
                        ),
                    }
                ],
                "tools": {
                    "read": True,
                    "glob": True,
                    "grep": True,
                    "edit": True,
                    "write": True,
                    "task": True,
                },
            },
            idempotency_key=f"{experiment_id}:{execution_label}-documentation",
        )
        if self._grounded:
            parent_messages, children = await self._wait_for_grounded_artifacts(
                parent.session_id,
                workspace=workspace,
                provider_id=provider_id,
                model_id=model_id,
                experiment_id=experiment_id,
            )
        else:
            parent_messages = await self._wait_for_messages(parent.session_id)
            _latest_assistant(parent_messages)
            children = await self._client.children(parent.session_id)
        minimum_children = (
            _GROUNDED_MINIMUM_CHILD_SESSIONS
            if self._grounded
            else _MINIMUM_CHILD_SESSIONS
        )
        if len(children) < minimum_children:
            raise QuickCandidateError(
                "OpenCode orchestrator did not dispatch enough specialist child sessions"
            )
        if self._grounded and len(children) > len(self._execution_pack.roles):
            raise QuickCandidateError(
                "OpenCode grounded orchestrator exceeded the bounded child-session limit"
            )
        child_messages = {
            child.session_id: [
                dict(item) for item in await self._client.messages(child.session_id)
            ]
            for child in children
        }
        for messages in child_messages.values():
            _latest_assistant(messages)

        intermediate_names = (
            tuple(
                path.removeprefix("candidate/intermediates/")
                for path in self._execution_pack.owned_artifacts
                if path.startswith("candidate/intermediates/")
            )
            if self._grounded
            else _INTERMEDIATE_OUTPUTS
        )
        documents, intermediates = await asyncio.to_thread(
            _read_grounded_candidate_outputs
            if self._grounded
            else _read_candidate_outputs,
            workspace,
            output_names,
            *(
                (intermediate_names,)
                if self._grounded
                else ()
            ),
        )
        orchestration = await asyncio.to_thread(
            _read_orchestration_outputs,
            workspace,
            output_names,
            validate_evidence_locators=self._grounded,
        )
        claim_report: dict[str, Any] | None = None
        if self._grounded:
            fact_ledger = await asyncio.to_thread(_read_fact_ledger, workspace)
            claim_report = await asyncio.to_thread(
                validate_document_claims,
                documents,
                fact_ledger,
            )
            await asyncio.to_thread(
                _write_claim_report,
                workspace,
                claim_report,
            )
            if claim_report["issue_count"]:
                previous_assistant_count = _assistant_message_count(parent_messages)
                await self._client.prompt_async(
                    parent.session_id,
                    payload={
                        "model": {"providerID": provider_id, "modelID": model_id},
                        "agent": primary_agent,
                        "parts": [
                            {
                                "type": "text",
                                "text": _grounded_repair_prompt(claim_report),
                            }
                        ],
                        "tools": {
                            "read": True,
                            "glob": True,
                            "grep": True,
                            "edit": True,
                            "write": True,
                            "task": False,
                        },
                    },
                    idempotency_key=f"{experiment_id}:grounded-fact-repair",
                )
                parent_messages = await self._wait_for_new_completion(
                    parent.session_id,
                    previous_assistant_count=previous_assistant_count,
                )
                documents, intermediates = await asyncio.to_thread(
                    _read_grounded_candidate_outputs,
                    workspace,
                    output_names,
                    intermediate_names,
                )
                orchestration = await asyncio.to_thread(
                    _read_orchestration_outputs,
                    workspace,
                    output_names,
                    validate_evidence_locators=True,
                )
                claim_report = await asyncio.to_thread(
                    validate_document_claims,
                    documents,
                    fact_ledger,
                )
                await asyncio.to_thread(
                    _write_claim_report,
                    workspace,
                    claim_report,
                )
                if claim_report["issue_count"]:
                    raise QuickCandidateError(
                        "OpenCode grounded candidate retained deterministic claim "
                        f"errors after repair: {claim_report['issue_count']}"
                    )
        await asyncio.to_thread(
            _reject_reproduced_secrets,
            source_items,
            documents,
            intermediates,
            orchestration,
        )
        if self._grounded:
            await asyncio.to_thread(
                reject_secret_like_output,
                {
                    **{f"docs/{key}": value for key, value in documents.items()},
                    **{
                        f"intermediates/{key}": value
                        for key, value in intermediates.items()
                    },
                    **{
                        f"orchestration/{key}": value.decode(
                            "utf-8", errors="ignore"
                        )
                        for key, value in orchestration.items()
                    },
                },
            )
        if self._grounded:
            child_messages = {
                child.session_id: [
                    dict(item) for item in await self._client.messages(child.session_id)
                ]
                for child in children
            }
        all_messages = [parent_messages, *child_messages.values()]
        usage = _aggregate_usage(all_messages)
        tool_summary = _aggregate_tool_summary(all_messages)
        if self._grounded:
            _enforce_grounded_runtime_budgets(
                self._execution_pack,
                usage=usage,
                tool_summary=tool_summary,
            )
        child_receipts = [
            _child_receipt(child, child_messages[child.session_id])
            for child in children
        ]

        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        for name in output_names:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"docs/{name}",
                    relative_path=f"docs/{name}",
                    content=documents[name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="document",
                )
            )
        for relative_name in intermediate_names:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"intermediates/{relative_name}",
                    relative_path=f"intermediates/{relative_name}",
                    content=intermediates[relative_name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="analysis",
                )
            )
        for name, media_type, kind in _ORCHESTRATION_OUTPUTS:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"orchestration/{name}",
                    relative_path=f"orchestration/{name}",
                    content=orchestration[name],
                    media_type=media_type,
                    kind=kind,
                )
            )
        if self._grounded:
            artifacts.extend(
                (
                    store.write_bytes(
                        branch="candidate",
                        logical_name="orchestration/fact_ledger.json",
                        relative_path="orchestration/fact_ledger.json",
                        content=(workspace / _GROUNDED_FACT_LEDGER).read_bytes(),
                        media_type="application/json",
                        kind="deterministic-fact-ledger",
                    ),
                    store.write_bytes(
                        branch="candidate",
                        logical_name=f"orchestration/{_GROUNDED_CLAIM_REPORT}",
                        relative_path=f"orchestration/{_GROUNDED_CLAIM_REPORT}",
                        content=canonical_json_bytes(claim_report or {}),
                        media_type="application/json",
                        kind="deterministic-claim-check",
                    ),
                )
            )

        execution_mode = (
            "grounded-lean-orchestrated-legacy-equivalence"
            if self._grounded
            else "multi-session-orchestrated-legacy-equivalence"
        )
        receipt_name = (
            "grounded_execution.json"
            if self._grounded
            else "orchestrated_execution.json"
        )
        receipt = {
            "schema_version": 1,
            "execution_mode": execution_mode,
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": "opencode/1.18.4",
            "provider_id": provider_id,
            "model_id": model_id,
            "phase1": {
                "status": "skipped",
                "reason": "Direct path: Phase 1 skipped for small project.",
            },
            "primary_agent": primary_agent,
            "allowed_subagents": (
                [
                    "nexus-doc-evidence",
                    "nexus-doc-analyst",
                    "nexus-doc-pack-writer",
                    "nexus-doc-reviewer",
                ]
                if self._grounded
                else [
                    "nexus-doc-evidence",
                    "nexus-doc-analyst",
                    "nexus-doc-referee",
                    "nexus-doc-writer",
                    "nexus-doc-reviewer",
                ]
            ),
            "subagent_depth": 1,
            "parent_session_id": parent.session_id,
            "child_sessions": child_receipts,
            "allowed_tools": ["read", "glob", "grep", "edit", "write", "task"],
            "tool_summary": tool_summary,
            "input_receipts": input_receipts,
            "usage": usage,
            "deterministic_claim_check": claim_report,
            "source_preflight": source_preflight,
            "execution_pack": (
                grounded_pack_payload(self._execution_pack)
                if self._grounded
                else None
            ),
            "progress": (
                _grounded_progress(self._execution_pack, workspace, children)
                if self._grounded
                else None
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_artifact = store.write_bytes(
            branch="candidate",
            logical_name=f"receipts/{receipt_name}",
            relative_path=f"receipts/{receipt_name}",
            content=canonical_json_bytes(receipt),
            media_type="application/json",
            kind="receipt",
        )
        artifacts.append(receipt_artifact)
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 1,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_mode": execution_mode,
                "publishes": False,
                "parent_session_id": parent.session_id,
                "child_session_count": len(children),
                "artifacts": [
                    {
                        "logical_name": item.logical_name,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "kind": item.kind,
                    }
                    for item in artifacts
                ],
                "usage": usage,
                "tool_summary": tool_summary,
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=parent.session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=int((monotonic() - started) * 1000),
        )

    async def _wait_for_grounded_artifacts(
        self,
        session_id: str,
        *,
        workspace: Path,
        provider_id: str,
        model_id: str,
        experiment_id: str,
    ) -> tuple[list[dict[str, Any]], list[OpenCodeSession]]:
        """Reconcile owned artifacts even when the parent never emits a final turn."""

        assert self._execution_pack is not None
        deadline = monotonic() + self._timeout_seconds
        repair_attempts = 0
        while monotonic() < deadline:
            messages = [
                dict(item) for item in await self._client.messages(session_id)
            ]
            children = await self._client.children(session_id)
            missing = _missing_pack_artifacts(self._execution_pack, workspace)
            parent_active = await self._client.session_status(session_id) is not None
            if (
                not missing
                and len(children) >= _GROUNDED_MINIMUM_CHILD_SESSIONS
                and not parent_active
            ):
                return messages, children
            if (
                missing
                and len(children) >= _GROUNDED_MINIMUM_CHILD_SESSIONS
                and not parent_active
            ):
                if repair_attempts >= self._execution_pack.repair_limit:
                    raise QuickCandidateError(
                        "OpenCode grounded orchestration became idle with missing "
                        f"owned artifacts after bounded repair: {len(missing)}"
                    )
                repair_attempts += 1
                await self._client.prompt_async(
                    session_id,
                    payload={
                        "model": {
                            "providerID": provider_id,
                            "modelID": model_id,
                        },
                        "agent": "nexus-doc-grounded-orchestrator",
                        "parts": [
                            {
                                "type": "text",
                                "text": _missing_artifact_repair_prompt(
                                    self._execution_pack,
                                    missing,
                                ),
                            }
                        ],
                        "tools": {
                            "read": True,
                            "glob": True,
                            "grep": True,
                            "edit": True,
                            "write": True,
                            "task": False,
                        },
                    },
                    idempotency_key=(
                        f"{experiment_id}:grounded-owned-artifact-repair:"
                        f"{repair_attempts}"
                    ),
                )
            await asyncio.sleep(1)
        raise QuickCandidateError(
            "OpenCode grounded artifact reconciliation timed out"
        )

    async def _wait_for_messages(self, session_id: str) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [dict(item) for item in await self._client.messages(session_id)]
            if _has_completed_assistant(messages):
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError("OpenCode orchestrated documentation generation timed out")

    async def _wait_for_new_completion(
        self,
        session_id: str,
        *,
        previous_assistant_count: int,
    ) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [dict(item) for item in await self._client.messages(session_id)]
            if (
                _assistant_message_count(messages) > previous_assistant_count
                and _has_completed_assistant(messages)
            ):
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError("OpenCode grounded repair timed out")


class GroundedOpenCodeRunner(OrchestratedOpenCodeRunner):
    """Lean orchestration with deterministic scanner facts and claim repair."""

    def __init__(
        self,
        *,
        execution_pack: GroundedExecutionPack,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            **kwargs,
            grounded=True,
            execution_pack=execution_pack,
        )


def _orchestrator_prompt() -> str:
    return (
        "Read AGENTS.md, SOURCES.md, WORKFLOW.md, and "
        "ORCHESTRATION_CONTRACT.json completely. Build the execution plan before "
        "drafting. You must use the Task tool to dispatch multiple bounded "
        "nexus-doc-evidence and nexus-doc-analyst investigations, an independent "
        "nexus-doc-referee audit, one nexus-doc-writer owner per final document, and "
        "at least two nexus-doc-reviewer tasks. Parallelize independent tasks with at "
        "most six active children. Consolidate the evidence and coverage ledgers, "
        "execute the exact preserved analysis/scorecard/document prompts, repair "
        "review findings, and create every required orchestration, intermediate, and "
        "final artifact. Do not finish with a plan only."
    )


def _grounded_orchestrator_prompt() -> str:
    return (
        "Read AGENTS.md, SOURCES.md, WORKFLOW.md, ORCHESTRATION_CONTRACT.json, "
        "FACT_LEDGER.json, SOURCE_PREFLIGHT.json, and "
        "GROUNDED_EXECUTION_PACK.json completely. Treat FACT_LEDGER.json as "
        "authoritative for protected scanner facts. Build the execution plan, then "
        "dispatch exactly the pack-declared role graph within its parallelism budget "
        "and with no nested children. Every child starts from only its "
        "`role_inputs/<role>/PACKET.json`, writes only its owned artifacts, and "
        "consumes only accepted dependency outputs. Consolidate primary artifacts, "
        "apply reviewer findings, and finish only when every pack-required file and "
        "protected fact passes. Static absence is not runtime absence; static scanner "
        "evidence cannot prove network reachability. Never publish or enter RAG."
    )


def _missing_pack_artifacts(
    pack: GroundedExecutionPack,
    workspace: Path,
) -> tuple[str, ...]:
    return tuple(
        path
        for path in pack.required_artifacts
        if not _nonempty_regular_file(workspace / path)
    )


def _nonempty_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _read_grounded_candidate_outputs(
    workspace: Path,
    output_names: tuple[str, ...],
    intermediate_names: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    documents = {
        name: _read_nonempty_text(workspace / "candidate" / "docs" / name)
        for name in output_names
    }
    intermediates = {
        name: _read_nonempty_text(
            workspace / "candidate" / "intermediates" / name
        )
        for name in intermediate_names
    }
    return documents, intermediates


def _read_nonempty_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise QuickCandidateError(
            f"OpenCode grounded candidate is missing required artifact: {path.name}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QuickCandidateError(
            f"OpenCode grounded candidate artifact is unreadable: {path.name}"
        ) from exc
    if not text.strip():
        raise QuickCandidateError(
            f"OpenCode grounded candidate artifact is empty: {path.name}"
        )
    return text


def _missing_artifact_repair_prompt(
    pack: GroundedExecutionPack,
    missing: tuple[str, ...],
) -> str:
    ownership = {
        path: role.id
        for role in pack.roles
        for path in role.owns
    }
    lines = [
        f"- `{path}` (owner: {ownership.get(path, 'primary-orchestrator')})"
        for path in missing
    ]
    return (
        "Deterministic reconciliation found the following required owned artifacts "
        "missing after all dispatched work became idle:\n"
        + "\n".join(lines)
        + "\nCreate only these missing artifacts from already-produced evidence. "
        "Do not dispatch any task or rewrite unrelated accepted outputs. This is the "
        "single bounded artifact-recovery attempt."
    )


def _grounded_progress(
    pack: GroundedExecutionPack,
    workspace: Path,
    children: list[OpenCodeSession],
) -> dict[str, Any]:
    roles = []
    for role in pack.roles:
        accepted = [
            path
            for path in role.owns
            if _nonempty_regular_file(workspace / path)
        ]
        roles.append(
            {
                "role_id": role.id,
                "stage": role.stage,
                "owned_artifact_count": len(role.owns),
                "accepted_artifact_count": len(accepted),
                "status": (
                    "completed" if len(accepted) == len(role.owns) else "incomplete"
                ),
            }
        )
    return {
        "stage": "completed",
        "role_count": len(pack.roles),
        "child_session_count": len(children),
        "artifact_count": len(
            [
                path
                for path in pack.required_artifacts
                if _nonempty_regular_file(workspace / path)
            ]
        ),
        "required_artifact_count": len(pack.required_artifacts),
        "roles": roles,
    }


def _enforce_grounded_runtime_budgets(
    pack: GroundedExecutionPack,
    *,
    usage: dict[str, Any],
    tool_summary: dict[str, Any],
) -> None:
    input_tokens = usage.get("input_tokens")
    if isinstance(input_tokens, int) and input_tokens > pack.max_input_tokens:
        raise QuickCandidateError("OpenCode grounded input-token budget exceeded")
    tool_calls = tool_summary.get("total_calls")
    if isinstance(tool_calls, int) and tool_calls > pack.max_tool_calls:
        raise QuickCandidateError("OpenCode grounded tool-call budget exceeded")


def _grounded_repair_prompt(claim_report: dict[str, Any]) -> str:
    compact_issues = json.dumps(
        claim_report.get("issues", [])[:50],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "The deterministic fact checker rejected protected claims. Do not use the "
        "Task tool and do not create child sessions. Read FACT_LEDGER.json and "
        f"candidate/orchestration/{_GROUNDED_CLAIM_REPORT}, then repair only the "
        "identified claims and any directly related cross-document contradictions. "
        "Update the evidence registry and review report to record the corrections. "
        "Recheck all ten documents against the ledger and finish with a short repair "
        f"summary. Current issues: {compact_issues}"
    )


def _read_orchestration_outputs(
    workspace: Path,
    output_names: tuple[str, ...],
    *,
    validate_evidence_locators: bool = False,
) -> dict[str, bytes]:
    root = workspace / "candidate" / "orchestration"
    outputs: dict[str, bytes] = {}
    parsed_json: dict[str, dict[str, Any]] = {}
    for name, media_type, _kind in _ORCHESTRATION_OUTPUTS:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise QuickCandidateError(
                f"OpenCode orchestration artifact is missing: {name}"
            )
        content = path.read_bytes()
        if not content.strip():
            raise QuickCandidateError(f"OpenCode orchestration artifact is empty: {name}")
        if media_type == "application/json":
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise QuickCandidateError(
                    f"OpenCode orchestration artifact is invalid JSON: {name}"
                ) from exc
            if not isinstance(payload, dict):
                raise QuickCandidateError(
                    f"OpenCode orchestration artifact must be an object: {name}"
                )
            missing = _REQUIRED_JSON_FIELDS[name] - payload.keys()
            if missing:
                raise QuickCandidateError(
                    f"OpenCode orchestration artifact {name} is missing fields: "
                    + ", ".join(sorted(missing))
                )
            parsed_json[name] = payload
        outputs[name] = content
    _validate_orchestration_contract(
        parsed_json=parsed_json,
        review_report=outputs["review_report.md"].decode("utf-8"),
        output_names=output_names,
    )
    if validate_evidence_locators:
        _validate_evidence_registry_locators(
            workspace,
            parsed_json["evidence_registry.json"],
            output_names,
        )
    return outputs


def _validate_evidence_registry_locators(
    workspace: Path,
    registry: dict[str, Any],
    output_names: tuple[str, ...],
) -> None:
    workspace_resolved = workspace.resolve()
    for index, claim in enumerate(registry.get("claims", []), start=1):
        locators = claim.get("evidence_locators")
        if (
            not isinstance(locators, list)
            or not locators
            or any(not isinstance(item, str) or not item.strip() for item in locators)
        ):
            raise QuickCandidateError(
                f"orchestration evidence claim {index} has invalid evidence locators"
            )
        used_by = claim.get("used_by_documents")
        if (
            not isinstance(used_by, list)
            or any(item not in output_names for item in used_by)
        ):
            raise QuickCandidateError(
                f"orchestration evidence claim {index} references unknown documents"
            )
        for locator in locators:
            raw_path = locator.split("#", 1)[0]
            raw_path = re.sub(r":\d+(?::\d+)?$", "", raw_path)
            relative = PurePosixPath(raw_path)
            if (
                relative.is_absolute()
                or "\\" in raw_path
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise QuickCandidateError(
                    f"orchestration evidence claim {index} has unsafe locator"
                )
            try:
                resolved = workspace.joinpath(*relative.parts).resolve(strict=True)
                resolved.relative_to(workspace_resolved)
            except (OSError, ValueError) as exc:
                raise QuickCandidateError(
                    f"orchestration evidence claim {index} locator does not exist"
                ) from exc
            if resolved.is_symlink() or not resolved.is_file():
                raise QuickCandidateError(
                    f"orchestration evidence claim {index} locator is not a file"
                )


def _validate_orchestration_contract(
    *,
    parsed_json: dict[str, dict[str, Any]],
    review_report: str,
    output_names: tuple[str, ...],
) -> None:
    plan = parsed_json["execution_plan.json"]
    units = plan["canonical_units"]
    if not isinstance(units, list) or not units:
        raise QuickCandidateError("orchestration plan has no canonical units")
    if not _is_nonempty_collection(plan["source_types"]):
        raise QuickCandidateError("orchestration plan has no source types")
    if not _is_nonempty_collection(plan["agent_assignments"]):
        raise QuickCandidateError("orchestration plan has no agent assignments")
    owners = plan["document_owners"]
    if not isinstance(owners, dict) or set(owners) != set(output_names):
        raise QuickCandidateError(
            "orchestration plan does not assign exactly one owner to every document"
        )
    if any(not isinstance(value, str) or not value.strip() for value in owners.values()):
        raise QuickCandidateError("orchestration plan has an invalid document owner")

    registry = parsed_json["evidence_registry.json"]
    claims = registry["claims"]
    if not isinstance(claims, list) or not claims:
        raise QuickCandidateError("orchestration evidence registry has no claims")
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            raise QuickCandidateError(
                f"orchestration evidence claim {index} is not an object"
            )
        missing = _REQUIRED_CLAIM_FIELDS - claim.keys()
        if missing:
            raise QuickCandidateError(
                f"orchestration evidence claim {index} is missing fields: "
                + ", ".join(sorted(missing))
            )

    coverage = parsed_json["coverage_ledger.json"]
    if coverage["canonical_units"] != units:
        raise QuickCandidateError(
            "coverage ledger canonical units differ from the execution plan"
        )
    document_coverage = coverage["document_coverage"]
    if not isinstance(document_coverage, dict) or set(document_coverage) != set(
        output_names
    ):
        raise QuickCandidateError(
            "coverage ledger does not cover every required document"
        )
    missing_sections = [
        section
        for section in _REQUIRED_REVIEW_SECTIONS
        if f"## {section}".casefold() not in review_report.casefold()
    ]
    if missing_sections:
        raise QuickCandidateError(
            "orchestration review report is missing sections: "
            + ", ".join(missing_sections)
        )


def _read_fact_ledger(workspace: Path) -> dict[str, Any]:
    path = workspace / _GROUNDED_FACT_LEDGER
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuickCandidateError("deterministic fact ledger is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("authority") != (
        "deterministic-frozen-scanner-facts"
    ):
        raise QuickCandidateError("deterministic fact ledger is invalid")
    return payload


def _write_claim_report(workspace: Path, report: dict[str, Any]) -> None:
    path = workspace / "candidate" / "orchestration" / _GROUNDED_CLAIM_REPORT
    path.write_bytes(canonical_json_bytes(report))


def _is_nonempty_collection(value: Any) -> bool:
    return isinstance(value, (list, dict)) and bool(value)


def _assistant_message_count(messages: list[dict[str, Any]]) -> int:
    return sum(
        isinstance(message.get("info"), dict)
        and message["info"].get("role") == "assistant"
        for message in messages
    )


def _reject_reproduced_secrets(
    source_items: tuple[tuple[str, Path], ...],
    documents: dict[str, str],
    intermediates: dict[str, str],
    orchestration: dict[str, bytes],
) -> None:
    secrets: set[str] = set()
    for logical_identity, path in source_items:
        logical_path = logical_identity.split(":", 1)[-1].replace("\\", "/")
        logical_suffix = PurePosixPath(logical_path).suffix.casefold()
        if logical_suffix and logical_suffix != ".json":
            continue
        if not logical_suffix and path.suffix.casefold() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _collect_sensitive_values(payload, secrets)
    if not secrets:
        return
    outputs = [
        *documents.values(),
        *intermediates.values(),
        *(content.decode("utf-8", errors="ignore") for content in orchestration.values()),
    ]
    if any(secret in output for secret in secrets for output in outputs):
        raise QuickCandidateError(
            "OpenCode orchestrated candidate reproduced a sensitive source value"
        )


def _collect_sensitive_values(value: Any, secrets: set[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_sensitive_values(item, secrets)
        return
    if not isinstance(value, dict):
        return
    raw_name = value.get("name")
    name = str(raw_name).casefold() if raw_name is not None else ""
    is_sensitive = any(
        marker in name
        for marker in ("password", "secret", "api_key", "apikey", "aes_key", "token")
    )
    if is_sensitive:
        for candidate in _strings_below(value.get("values", value.get("value"))):
            normalized = candidate.strip()
            if (
                len(normalized) >= 6
                and normalized.casefold()
                not in {"[redacted]", "[url_redacted]", "unknown", "default"}
            ):
                secrets.add(normalized)
    for nested in value.values():
        _collect_sensitive_values(nested, secrets)


def _strings_below(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [
            nested
            for item in value
            for nested in _strings_below(item)
        ]
    if isinstance(value, dict):
        return [
            nested
            for item in value.values()
            for nested in _strings_below(item)
        ]
    return []


def _aggregate_usage(message_sets: list[list[dict[str, Any]]]) -> dict[str, Any]:
    values = [_usage(messages) for messages in message_sets]
    return {
        "input_tokens": _sum_optional_int(values, "input_tokens"),
        "output_tokens": _sum_optional_int(values, "output_tokens"),
        "cache_read_tokens": _sum_optional_int(values, "cache_read_tokens"),
        "billed_cost_usd": _sum_optional_decimal(values, "billed_cost_usd"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _sum_optional_int(values: list[dict[str, Any]], key: str) -> int | None:
    found = [value[key] for value in values if isinstance(value.get(key), int)]
    return sum(found) if found else None


def _sum_optional_decimal(values: list[dict[str, Any]], key: str) -> str | None:
    total = Decimal("0")
    seen = False
    for value in values:
        raw = value.get(key)
        if not isinstance(raw, str):
            continue
        try:
            total += Decimal(raw)
            seen = True
        except DecimalException:
            continue
    return str(total) if seen else None


def _aggregate_tool_summary(
    message_sets: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    by_tool: dict[str, int] = {}
    for messages in message_sets:
        for name, count in _tool_summary(messages)["by_tool"].items():
            by_tool[name] = by_tool.get(name, 0) + int(count)
    return {
        "total_calls": sum(by_tool.values()),
        "by_tool": dict(sorted(by_tool.items())),
    }


def _child_receipt(
    child: OpenCodeSession,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = child.raw or {}
    return {
        "session_id": child.session_id,
        "title": str(raw.get("title") or ""),
        "parent_id": str(raw.get("parentID") or raw.get("parent_id") or ""),
        "usage": _usage(messages),
        "tool_summary": _tool_summary(messages),
        "completed": _has_completed_assistant(messages),
    }
