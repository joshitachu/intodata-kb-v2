"""Internal bounded auto-doc agent workflow for Git updates."""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.licensing.core import Capability, LicenceRefused
from mcp_server.licensing.usage import check_capability, log_refusal
from mcp_server.db.session import async_session_factory
from mcp_server.services.git_diff_patcher import (
    DiffPatchResult,
    _chunk_diff,
    _failed,
    _git,
    _manual,
    _run_diff_patch,
    _summary_with_outcome,
    parse_name_status,
)
from mcp_server.services.true_patch_executor import TruePatchExecutor
from mcp_server.services.git_credentials import GitCredentialProvider, provider_for_project
from mcp_server.services.git_remote_service import fetch_remote, pull_remote_ff_only
from mcp_server.services.git_reingestion import (
    ChangedFile,
    resolve_changed_groups,
    run_git_reingestion,
)
from mcp_server.services.doc_version_history import record_push_outcome
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.git_patch_coordinator import GitPatchCoordinator
from mcp_server.services.living_docs.baseline import prime_project_baseline
from mcp_server.services.living_docs.run_journal import ProjectRunLock, RunJournal
from mcp_server.services.living_docs.rollout import RolloutStage, inspect_project_patch_readiness
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.reingestion_router import ReingestionRouter, RoutingDecision
from mcp_server.services.rag_support_package import (
    GenerationCacheReadiness,
    inspect_generation_cache_readiness,
)
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.quick_change_scanner import QuickChangeScanner, QuickScanReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoDocUpdateContext:
    project_id: str
    local_path: str
    doc_generation_provider: str | None
    credential: GitCredentialProvider | None
    last_ingested_commit_sha: str | None = None


class AutoDocToolBudgetExceeded(RuntimeError):
    """Raised when the auto-doc agent exceeds its deterministic tool budget."""


def _summary_docs(summary: dict[str, Any]) -> list[str]:
    docs: list[str] = []
    for key in ("patched_docs", "touched_docs", "affected_docs", "matched_docs", "candidate_docs"):
        value = summary.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and item not in docs:
                    docs.append(item)
    return docs[: config.git_diff_patch_max_docs]


def _coerce_validation_errors(summary: dict[str, Any]) -> list[str]:
    value = summary.get("validation_errors")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _should_targeted_regen_after_patch_failure(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        marker in lowered
        for marker in (
            "diff requires",
            "no existing docs found",
            "could not be mapped to existing doc sections",
            "could not be narrowed to a bounded set",
            "no doc edits for a doc-relevant code diff",
        )
    )


def _full_rebaseline_reason(reason_codes: list[str]) -> str:
    suffix = ", ".join(reason_codes) if reason_codes else "targeted_regen_not_ready"
    return f"Full rebaseline required before targeted regeneration: {suffix}"


class AutoDocAgentRuntime:
    """Bounded docs-only tool loop for Git-triggered documentation updates.

    The model is only used for decisions and Markdown edits through existing
    guarded helpers. This runtime owns the trajectory, budgets, fallback
    decisions, and final summary shape.
    """

    def __init__(
        self,
        *,
        log_mgr: LogManager,
        router: ReingestionRouter | None = None,
        quick_scanner: QuickChangeScanner | None = None,
    ) -> None:
        self.log_mgr = log_mgr
        self.router = router or ReingestionRouter()
        self.quick_scanner = quick_scanner or QuickChangeScanner()
        self._tool_calls: list[dict[str, Any]] = []
        self._tool_bytes = 0

    async def run(
        self,
        ctx: AutoDocUpdateContext,
        *,
        before_sha: str,
        after_sha: str,
        trigger: str,
    ) -> DiffPatchResult:
        summary = self._base_summary(ctx, before_sha, after_sha, trigger)
        if not before_sha or before_sha == "0" * 40:
            return self._manual(summary, "First push requires initial manual scan/generate")

        try:
            inspect_result = await self._inspect_diff(ctx, before_sha, after_sha)
            summary.update(inspect_result)
            quick_scan: QuickScanReport = inspect_result["_quick_scan"]
            full_diff_text = str(inspect_result["_full_diff_text"])
            summary.pop("_quick_scan", None)
            summary.pop("_changed_files", None)
            summary.pop("_full_diff_text", None)

            if not quick_scan.doc_relevant_files:
                return await self._skip(
                    ctx,
                    after_sha,
                    summary,
                    "Only ignored/non-doc-impacting files changed",
                )

            decision = await self._decide(
                ctx,
                quick_scan=quick_scan,
                changed_files=inspect_result["_changed_files"],
                diff_chunks=inspect_result["diff_chunks"],
                generated_docs=inspect_result["generated_docs"],
            )
            impact = decision.to_impact_dict()
            summary.update(impact)
            summary["routing"] = decision.model_dump()
            summary["touched_docs"] = list(impact["affected_docs"])

            if decision.strategy == "skip":
                return await self._skip(
                    ctx,
                    after_sha,
                    summary,
                    decision.reason or "No documentation update required",
                )
            if decision.strategy == "held":
                return self._held(
                    summary,
                    decision.reason or "Automatic patch scope is not safely traceable",
                    reason_code=decision.reason_code or "traceability_required",
                )
            if decision.strategy == "full_regen":
                # The automatic entrypoint must be safe even if a legacy or
                # injected router returns this retired strategy.
                return self._held(
                    summary,
                    "Automatic exhaustive generation is forbidden; run an explicit manual rebuild",
                    reason_code="automatic_full_generation_forbidden",
                )
            if decision.strategy == "scoped_regen":
                return await self._scoped_regen(
                    ctx,
                    before_sha,
                    after_sha,
                    trigger,
                    summary,
                    decision,
                    decision.reason or "Deterministic router requested regeneration",
                    targeted_docs=list(decision.affected_docs),
                )

            return await self._edit_validate_and_sync(
                ctx,
                before_sha=before_sha,
                after_sha=after_sha,
                trigger=trigger,
                summary=summary,
                decision=decision,
            )
        except AutoDocToolBudgetExceeded as exc:
            return self._held(summary, str(exc), reason_code="agent_budget_exceeded")
        except Exception as exc:
            logger.exception("Auto-doc agent failed for %s", ctx.project_id)
            return _failed(self._finalize_summary(summary, action="failed", reason=str(exc)), str(exc))

    def _base_summary(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
        trigger: str,
    ) -> dict[str, Any]:
        return {
            "trigger": trigger,
            "mode": "tool_loop_agent",
            "action": "unknown",
            "before": before_sha,
            "after": after_sha,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "changed_files": [],
            "touched_docs": [],
            "tool_calls": [],
            "tool_call_count": 0,
            "validation_errors": [],
            "repair_attempts": 0,
            "fallback_used": False,
            "reason": "",
            "outcome": "unknown",
            "reason_code": None,
            "decision_strategy": "tool_loop_agent",
            "confidence": None,
            "manual_review_reason": None,
            "docs_dir": str(config.project_root / "output" / ctx.project_id / "docs"),
            "has_baseline": bool(ctx.last_ingested_commit_sha),
        }

    async def _inspect_diff(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
    ) -> dict[str, Any]:
        await fetch_remote(ctx.local_path, ctx.credential, git_func=_git)
        name_status = await _git(ctx.local_path, "diff", "--name-status", before_sha, after_sha, "-M")
        changes = parse_name_status(name_status)
        changed_files = [change.path for change in changes]
        full_diff_text = ""
        if changes:
            full_diff_text = await _git(
                ctx.local_path,
                "diff",
                f"--unified={config.git_diff_patch_context_lines}",
                before_sha,
                after_sha,
                "--",
                *sorted(set(changed_files) | {change.previous_path for change in changes if change.previous_path}),
            )
        quick_scan = self.quick_scanner.scan(changes, full_diff_text)
        changed_file_list = [
            ChangedFile(
                status=change.status,
                path=change.path,
                previous_path=change.previous_path,
            )
            for change in changes
        ]
        changed_groups = sorted(
            resolve_changed_groups(ctx.project_id, changed_file_list, False)
        )
        chunks = _chunk_diff(full_diff_text, changes)
        chunk_summary = [
            {"index": chunk.index, "paths": chunk.paths, "chars": len(chunk.text)}
            for chunk in chunks
        ]
        docs_dir = config.project_root / "output" / ctx.project_id / "docs"
        generated_docs = await self._generated_doc_names(docs_dir)
        self._record_tool(
            "inspect",
            {
                "changed_files": len(changed_files),
                "diff_chars": len(full_diff_text),
                "generated_docs": len(generated_docs),
            },
        )
        if len(changes) > config.git_diff_patch_max_files and quick_scan.doc_relevant_files:
            # Keep this as context rather than an immediate failure; the agent can
            # request targeted regeneration with a concrete reason.
            quick_dict = quick_scan.to_dict()
            quick_dict["scope_warning"] = f"{len(changes)} files changed; bounded patch max is {config.git_diff_patch_max_files}"
        else:
            quick_dict = quick_scan.to_dict()
        return {
            "changed_files": changed_files,
            "quick_scan": quick_dict,
            "diff_chunks": chunk_summary,
            "generated_docs": generated_docs,
            "changed_groups": changed_groups,
            "_quick_scan": quick_scan,
            "_changed_files": changed_file_list,
            "_full_diff_text": full_diff_text,
        }

    async def _generated_doc_names(self, docs_dir: Path) -> list[str]:
        def read_names() -> list[str]:
            if not docs_dir.exists():
                return []
            return sorted(path.name for path in docs_dir.glob("*.md") if path.name[:1].isdigit())

        import asyncio

        names = await asyncio.to_thread(read_names)
        self._record_tool("read_generated_docs", {"docs": names[: config.git_diff_patch_max_docs * 2]})
        return names

    async def _decide(
        self,
        ctx: AutoDocUpdateContext,
        *,
        quick_scan: QuickScanReport,
        changed_files: list[ChangedFile],
        diff_chunks: list[dict[str, Any]],
        generated_docs: list[str],
    ) -> RoutingDecision:
        output_dir = config.project_root / "output" / ctx.project_id
        decision = self.router.decide(
            project_id=ctx.project_id,
            changed_files=changed_files,
            quick_scan=quick_scan,
            output_dir=output_dir,
        )
        # Expand-only, non-blocking semantic pass. Disabled by default; on any
        # failure it returns the deterministic decision unchanged. It can only
        # ADD docs, never shrink the set or change the strategy.
        all_docs = [name[:-3] if name.endswith(".md") else name for name in generated_docs]
        if not config.living_doc_patching_enabled:
            decision = await self.router.expand(
                decision,
                diff_chunks=diff_chunks,
                all_docs=all_docs,
                doc_generation_provider=ctx.doc_generation_provider,
            )
        self._record_tool(
            "finish_decision",
            {
                "action": decision.strategy,
                "affected_docs": decision.affected_docs,
                "expanded_docs": decision.expanded_docs,
                "reason": decision.reason,
            },
        )
        return decision

    async def _edit_validate_and_sync(
        self,
        ctx: AutoDocUpdateContext,
        *,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        decision: RoutingDecision,
    ) -> DiffPatchResult:
        if decision.strategy == "bounded_patch":
            return await self._true_patch(
                ctx, before_sha, after_sha, trigger, summary, decision
            )
        max_repairs = max(0, min(config.agent_max_repair_attempts, 2))
        last_result: DiffPatchResult | None = None
        for attempt in range(max_repairs + 1):
            if attempt:
                await self.log_mgr.emit(ctx.project_id, f"Auto-doc repair attempt {attempt}", "warning")
            patch_result = await _run_diff_patch(
                project_id=ctx.project_id,
                local_path=ctx.local_path,
                before_sha=before_sha,
                after_sha=after_sha,
                doc_generation_provider=ctx.doc_generation_provider,
                log_mgr=self.log_mgr,
                trigger=trigger,
                credential=ctx.credential,
                impact_plan={**decision.to_impact_dict(), "mode": "tool_loop_agent"},
            )
            last_result = patch_result
            validation_errors = _coerce_validation_errors(patch_result.summary)
            self._record_tool(
                "edit_validate_sync",
                {
                    "attempt": attempt,
                    "action": patch_result.action,
                    "patched_docs": patch_result.patched_docs or patch_result.summary.get("patched_docs", []),
                    "validation_errors": validation_errors,
                    "error": patch_result.error,
                },
            )
            if patch_result.error is None and patch_result.advance_commit:
                merged = self._merge_patch_summary(summary, patch_result.summary)
                return DiffPatchResult(
                    action=patch_result.action,
                    summary=self._finalize_summary(
                        merged,
                        action=patch_result.action,
                        touched_docs=_summary_docs(merged),
                        repair_attempts=attempt,
                    ),
                    patched_docs=patch_result.patched_docs,
                    advance_commit=True,
                )
            if validation_errors and attempt < max_repairs:
                summary["validation_errors"] = validation_errors
                summary["repair_attempts"] = attempt + 1
                continue
            break

        assert last_result is not None
        merged = self._merge_patch_summary(summary, last_result.summary)
        reason = last_result.error or merged.get("reason") or "Doc edit did not complete"
        clean_reason = str(reason).removeprefix("Manual review required: ")
        if _should_targeted_regen_after_patch_failure(clean_reason):
            return self._held(
                merged,
                clean_reason,
                reason_code="patch_failed",
                repair_attempts=int(merged.get("repair_attempts") or 0),
            )
        return self._held(
            merged,
            clean_reason,
            reason_code="patch_failed",
            repair_attempts=int(merged.get("repair_attempts") or 0),
        )

    async def _true_patch(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        decision: RoutingDecision,
    ) -> DiffPatchResult:
        """Execute Slice 4 true patch mode for the bounded_patch lane.

        Runs TruePatchExecutor for each group in decision.changed_groups.
        On success: returns advance_commit=True DiffPatchResult.
        On scoped_regen_fallback: holds the source revision for review.
        On hard failure: returns _failed(...).
        """
        output_dir = config.project_root / "output" / ctx.project_id
        platform = await self._project_platform(ctx.project_id)
        executor = TruePatchExecutor(
            doc_generation_provider=ctx.doc_generation_provider
        )
        if config.living_doc_patching_enabled:
            # The enabled route has one authority: candidate docs + a
            # revision-aware RAG membership.  The HTTP adapter stages changed
            # artifacts and lets RAG clone unchanged vectors into the candidate
            # revision without embedding them again.
            raw_bridge = RagBridge(log_manager=self.log_mgr)
            revision_bridge = RagBridge(
                log_manager=self.log_mgr,
                revisioned_rag=raw_bridge.http_revisioned_adapter(),
            )
            store = FileCandidateStore(
                (config.data_dir / "living_docs" / "candidates" / ctx.project_id).resolve()
            )
            coordinator = GitPatchCoordinator(
                store=store,
                publisher=CandidatePublisher(store=store, rag=revision_bridge),
                executor=executor,
            )
            try:
                base_revision = await revision_bridge.active_knowledge_revision(ctx.project_id)
                # Do the same baseline preflight/activation as the legacy
                # agent lane.  Candidate true-patching must never silently
                # operate with a missing revisioned RAG baseline.
                preflight = inspect_project_patch_readiness(
                    project_id=ctx.project_id,
                    output_dir=output_dir,
                    feature_enabled=True,
                    stage=RolloutStage(config.living_doc_rollout_stage),
                    source_revision_id=before_sha or None,
                    active_knowledge_revision_id=base_revision or "baseline-pending",
                )
                if not preflight.ready:
                    return self._held(
                        summary,
                        f"Living-document baseline is not ready: {', '.join(preflight.reason_codes)}",
                        reason_code=preflight.reason_codes[0],
                    )
                if base_revision is None:
                    baseline_receipt = await prime_project_baseline(
                        project_id=ctx.project_id,
                        source_revision_id=before_sha,
                        docs_dir=output_dir / "docs",
                        rag=revision_bridge,
                    )
                    base_revision = baseline_receipt.knowledge_revision_id
                    await self.log_mgr.emit(
                        ctx.project_id,
                        (
                            "Living-document baseline activated "
                            f"({baseline_receipt.artifact_count} artifacts, "
                            f"revision={base_revision})"
                        ),
                        "info",
                    )
                readiness = inspect_project_patch_readiness(
                    project_id=ctx.project_id,
                    output_dir=output_dir,
                    feature_enabled=True,
                    stage=RolloutStage(config.living_doc_rollout_stage),
                    source_revision_id=before_sha or None,
                    active_knowledge_revision_id=base_revision,
                )
                if not readiness.ready:
                    return self._held(
                        summary,
                        f"Living-document baseline is not ready: {', '.join(readiness.reason_codes)}",
                        reason_code=readiness.reason_codes[0],
                    )
                candidate = coordinator.create_candidate(
                    project_id=ctx.project_id,
                    source_identity=ctx.local_path,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    changed_groups=decision.changed_groups,
                    active_docs_dir=output_dir / "docs",
                    base_knowledge_revision_id=base_revision,
                    repair_attempt_id=(trigger.removeprefix("repair_latest:") if trigger.startswith("repair_latest:") else None),
                )
            except Exception as exc:
                return self._held(
                    summary,
                    f"Candidate creation held: {exc}",
                    reason_code="traceability_required",
                )
            outcome = await coordinator.run(
                candidate,
                active_output_dir=output_dir,
                candidate_scan_output_dir=config.intodoc_output_dir / ctx.project_id,
                changed_groups=decision.changed_groups,
                platform=platform,
                log_mgr=self.log_mgr,
                before_sha=before_sha,
                after_sha=after_sha,
                active_source_revision_key=candidate.change_set.source_revision.key,
                source_path=ctx.local_path,
                rollout_stage=config.living_doc_rollout_stage,
                low_risk_max_artifacts=config.living_doc_low_risk_max_artifacts,
            )
            self._record_tool("living_doc_candidate", {
                "candidate_id": outcome.candidate_id,
                "knowledge_revision_id": outcome.knowledge_revision_id,
                "action": outcome.action,
                "reason_code": outcome.reason_code,
            })
            if outcome.action != "active":
                return self._held(
                    summary,
                    "Candidate patch held; active docs and RAG revision are unchanged",
                    reason_code=outcome.reason_code or "candidate_patch_held",
                )
            return DiffPatchResult(
                action="true_patch",
                summary=self._finalize_summary(
                    {
                        **summary,
                        "candidate_id": outcome.candidate_id,
                        "knowledge_revision_id": outcome.knowledge_revision_id,
                        "publication": (
                            {
                                "changed_logical_ids": outcome.publication.changed_logical_ids,
                                "deleted_logical_ids": outcome.publication.deleted_logical_ids,
                                "embedded_content_count": outcome.publication.embedded_content_count,
                                "reused_content_count": outcome.publication.reused_content_count,
                            }
                            if outcome.publication else {}
                        ),
                    },
                    action="true_patch",
                    touched_docs=list(outcome.patched_docs),
                    reason_code="knowledge_revision_activated",
                ),
                patched_docs=list(outcome.patched_docs),
                advance_commit=True,
            )
        self._record_tool("true_patch_start", {
            "changed_groups": decision.changed_groups,
            "platform": platform,
        })
        result = await executor.run(
            project_id=ctx.project_id,
            output_dir=output_dir,
            changed_groups=decision.changed_groups,
            platform=platform,
            log_mgr=self.log_mgr,
            before_sha=before_sha,
            after_sha=after_sha,
            trigger=trigger,
        )
        self._record_tool("true_patch_done", {
            "action": result.action,
            "patched_groups": result.patched_groups,
            "patched_docs": result.patched_docs,
            "error": result.error,
        })
        if result.action == "true_patch":
            merged = self._merge_patch_summary(summary, result.summary)
            return DiffPatchResult(
                action="true_patch",
                summary=self._finalize_summary(
                    merged,
                    action="true_patch",
                    touched_docs=result.patched_docs,
                    reason_code="true_patch_succeeded",
                ),
                patched_docs=result.patched_docs,
                advance_commit=True,
            )
        if result.action == "scoped_regen_fallback":
            fallback_reason = result.error or "True patch validation failed; scoped_regen fallback"
            await self.log_mgr.emit(
                ctx.project_id,
                f"TruePatch held; exhaustive/scoped regeneration is not automatic: {fallback_reason}",
                "warning",
            )
            return self._held(
                {**summary, **result.summary, "true_patch_fallback": True},
                fallback_reason,
                reason_code="patch_failed",
            )
        # A patch executor failure must not escalate into a broader generation.
        err = result.error or "True patch executor failed"
        return self._held(
            {**summary, **result.summary},
            err,
            reason_code="patch_failed",
        )

    async def _project_platform(self, project_id: str) -> str:
        """Return the platform string for a project (default 'talend')."""
        try:
            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                project = await registry.get_project(project_id)
                if project and project.platform:
                    return project.platform.lower()
        except Exception:
            pass
        return "talend"

    def _merge_patch_summary(self, base: dict[str, Any], patch_summary: dict[str, Any]) -> dict[str, Any]:
        merged = {**base, **patch_summary}
        merged["mode"] = "tool_loop_agent"
        merged["touched_docs"] = _summary_docs(merged)
        merged["tool_calls"] = list(self._tool_calls)
        merged["tool_call_count"] = len(self._tool_calls)
        return merged

    async def _skip(
        self,
        ctx: AutoDocUpdateContext,
        after_sha: str,
        summary: dict[str, Any],
        reason: str,
    ) -> DiffPatchResult:
        self._record_tool("finish", {"action": "skipped", "reason": reason})
        await pull_remote_ff_only(ctx.local_path, ctx.credential, git_func=_git)
        await self.log_mgr.emit(ctx.project_id, f"Git update skipped: {reason}", "info")
        return DiffPatchResult(
            action="skipped",
            summary=self._finalize_summary(summary, action="skipped", reason=reason),
            advance_commit=True,
        )

    async def _scoped_regen(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        decision: RoutingDecision,
        reason: str,
        *,
        targeted_docs: list[str] | None,
    ) -> DiffPatchResult:
        # Automatic regeneration is only allowed with an explicit, non-empty
        # target collection. ``None`` used to mean regenerate every document.
        if not targeted_docs:
            return self._held(
                summary,
                "Automatic regeneration has no bounded target documents",
                reason_code="empty_target_scope",
            )
        action_label = "targeted_regen"
        self._record_tool(
            f"{action_label}_needed",
            {"reason": reason, "affected_docs": decision.affected_docs, "targeted_docs": targeted_docs},
        )
        cache_readiness = self._targeted_regen_cache_readiness(ctx, before_sha, summary)
        if not cache_readiness.targeted_regen_ready:
            rebaseline_reason = _full_rebaseline_reason(cache_readiness.reason_codes)
            await self.log_mgr.emit(ctx.project_id, rebaseline_reason, "warning")
            return self._manual(
                {
                    **summary,
                    **decision.to_impact_dict(),
                    "targeted_regen_ready": False,
                    "requires_full_rebaseline": cache_readiness.requires_full_rebaseline,
                    "reason_codes": cache_readiness.reason_codes,
                    "targeted_regen_preflight": cache_readiness.to_dict(),
                    "fallback_used": False,
                    "fallback_reason": rebaseline_reason,
                    "expensive_fallback_required": True,
                    "regen_result": {
                        "type": action_label,
                        "success": False,
                        "skipped": True,
                        "reason": "preflight_failed",
                        "reason_codes": cache_readiness.reason_codes,
                    },
                },
                rebaseline_reason,
                reason_code="full_rebaseline_required",
            )

        await self.log_mgr.emit(
            ctx.project_id, f"Auto-doc {action_label} regeneration: {reason}", "warning"
        )
        regen_sink: dict[str, Any] = {}
        ok = await run_git_reingestion(
            ctx.project_id,
            before_sha,
            after_sha,
            triggered_by=trigger,
            log_mgr=self.log_mgr,
            targeted_docs=targeted_docs,
            outcome_sink=regen_sink,
        )
        # Bug fix: run_git_reingestion may SKIP (fingerprint unchanged / infra-only)
        # instead of regenerating. Report that truthfully as a skip rather than a
        # targeted_regen that never ran -- otherwise the change history records a
        # phantom amendment, the drift counter over-counts, and the commit is
        # marked done while docs are untouched.
        if ok and regen_sink.get("skipped"):
            return await self._skip(
                ctx,
                after_sha,
                {**summary, **decision.to_impact_dict()},
                "Scan fingerprint unchanged; no regeneration performed",
            )
        final_summary = self._finalize_summary(
            {
                **summary,
                **decision.to_impact_dict(),
                "targeted_regen_ready": True,
                "requires_full_rebaseline": False,
                "targeted_regen_preflight": cache_readiness.to_dict(),
                "fallback_used": True,
                "fallback_reason": reason,
                "regen_result": {
                    "type": action_label,
                    "success": ok,
                    "targeted_docs": targeted_docs,
                    "baseline_source": "generation_rag_sync",
                },
            },
            action=action_label if ok else "manual_review",
            reason=reason if ok else f"Regeneration failed: {reason}",
            # Prefer the actual regenerated set reported by run_git_reingestion over
            # the router's path-based estimate, so history/telemetry are accurate.
            touched_docs=(
                regen_sink.get("regenerated_docs")
                if ok and regen_sink.get("regenerated_docs") is not None
                else list(decision.affected_docs)
            ),
        )
        if ok:
            return DiffPatchResult(action=action_label, summary=final_summary, advance_commit=False)
        return _manual(final_summary, f"Regeneration failed after router decision: {reason}")

    def _targeted_regen_cache_readiness(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        summary: dict[str, Any],
    ) -> GenerationCacheReadiness:
        changed_groups = [
            str(group).strip()
            for group in summary.get("changed_groups", [])
            if str(group).strip()
        ]
        has_baseline = bool(
            ctx.last_ingested_commit_sha and before_sha and before_sha != "0" * 40
        )
        return inspect_generation_cache_readiness(
            config.project_root / "output" / ctx.project_id,
            has_baseline=has_baseline,
            changed_groups=changed_groups,
        )

    def _manual(
        self,
        summary: dict[str, Any],
        reason: str,
        *,
        repair_attempts: int | None = None,
        reason_code: str | None = None,
    ) -> DiffPatchResult:
        try:
            self._record_tool("finish", {"action": "manual_review", "reason": reason})
        except AutoDocToolBudgetExceeded:
            pass
        final = self._finalize_summary(
            summary,
            action="manual_review",
            reason=reason,
            repair_attempts=repair_attempts,
            reason_code=reason_code,
        )
        final["manual_review_reason"] = reason
        return DiffPatchResult(
            action="manual_review",
            summary=final,
            error=f"Manual review required: {reason}",
        )

    def _held(
        self,
        summary: dict[str, Any],
        reason: str,
        *,
        reason_code: str,
        repair_attempts: int | None = None,
    ) -> DiffPatchResult:
        """Return a terminal, non-mutating automatic-update outcome."""
        try:
            self._record_tool("finish", {"action": "held", "reason": reason, "reason_code": reason_code})
        except AutoDocToolBudgetExceeded:
            pass
        final = self._finalize_summary(
            summary,
            action="held",
            reason=reason,
            repair_attempts=repair_attempts,
            reason_code=reason_code,
        )
        final["held"] = True
        final["held_reason"] = reason
        return DiffPatchResult(action="held", summary=final, error=f"Update held: {reason}")

    def _record_tool(self, name: str, payload: dict[str, Any]) -> None:
        import json

        payload_bytes = len(json.dumps(payload, ensure_ascii=False, default=str))
        self._tool_bytes += payload_bytes
        if len(self._tool_calls) + 1 > config.agent_max_tool_calls:
            raise AutoDocToolBudgetExceeded(
                f"Agent tool budget exceeded ({config.agent_max_tool_calls} calls)"
            )
        if self._tool_bytes > config.agent_max_tool_bytes:
            raise AutoDocToolBudgetExceeded(
                f"Agent read budget exceeded ({self._tool_bytes} bytes > {config.agent_max_tool_bytes})"
            )
        self._tool_calls.append({"name": name, "bytes": payload_bytes, "result": payload})

    def _finalize_summary(
        self,
        summary: dict[str, Any],
        *,
        action: str,
        reason: str | None = None,
        touched_docs: list[str] | None = None,
        repair_attempts: int | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        final = dict(summary)
        final.setdefault("mode", "tool_loop_agent")
        final["tool_calls"] = list(self._tool_calls)
        final["tool_call_count"] = len(self._tool_calls)
        final["touched_docs"] = touched_docs if touched_docs is not None else _summary_docs(final)
        final.setdefault("validation_errors", [])
        if repair_attempts is not None:
            final["repair_attempts"] = repair_attempts
        else:
            final.setdefault("repair_attempts", 0)
        final.setdefault("fallback_used", False)
        if reason:
            final["reason"] = reason
        final.setdefault("reason", "")
        final.setdefault("confidence", final.get("confidence"))
        return _summary_with_outcome(
            final,
            action=action,
            reason=final.get("reason"),
            decision_strategy=str(final.get("strategy") or final.get("decision_strategy") or "tool_loop_agent"),
            reason_code=reason_code,
        )


class AutoDocUpdateService:
    """Single entrypoint for webhook and poller Git documentation updates."""

    def __init__(
        self,
        *,
        log_mgr: LogManager,
        router: ReingestionRouter | None = None,
        quick_scanner: QuickChangeScanner | None = None,
    ) -> None:
        self.log_mgr = log_mgr
        self.router = router or ReingestionRouter()
        self.quick_scanner = quick_scanner or QuickChangeScanner()

    async def run(
        self, *, project_id: str, before_sha: str, after_sha: str,
        trigger: str = "webhook",
    ) -> bool:
        output = config.project_root / "output" / project_id
        lock = ProjectRunLock(output)
        journal = RunJournal(output)
        run_id = journal.start(before_sha, after_sha, trigger)
        # Licence: every git-triggered entry (webhook, poller, repair-latest)
        # lands here, and the patch lane spends tokens without a run record, so
        # the run-creation chokepoint alone would miss it. "deferred" leaves the
        # baseline where it is, so the push is replayed once a licence is back.
        refusal = await check_capability(Capability.GENERATION)
        if refusal is not None:
            log_refusal("auto_doc_update", LicenceRefused(refusal), project_id=project_id, trigger=trigger)
            journal.finish(run_id, {"action": "deferred", "reason": f"licence refused: {refusal.code.value}"})
            return False
        # Persist receipt before waiting. Polling retries timed-out events from
        # the last successful baseline, including all intervening commits.
        for _ in range(1200):
            if lock.acquire():
                break
            try:
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                journal.finish(run_id, {"action": "interrupted", "reason": "Cancelled while waiting for another update"})
                raise
        else:
            journal.finish(run_id, {"action": "deferred", "reason": "Another project update is still running"})
            return False
        try:
            journal.activate(run_id)
            return await self._run_serialized(project_id=project_id, before_sha=before_sha,
                after_sha=after_sha, trigger=trigger, journal=journal, run_id=run_id)
        except BaseException as exc:
            journal.finish(run_id, {"action": "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed",
                                    "reason": str(exc)})
            raise
        finally:
            lock.release()

    async def _run_serialized(
        self,
        *,
        project_id: str,
        before_sha: str,
        after_sha: str,
        trigger: str = "webhook",
        journal: RunJournal,
        run_id: str,
    ) -> bool:
        ctx = await self._load_context(project_id)
        if not ctx:
            journal.finish(run_id, {"action": "failed", "reason": "Project not found"})
            return False
        candidate_root = (config.data_dir / "living_docs/candidates" / project_id).resolve()
        if (candidate_root / "candidates").is_dir():
            from mcp_server.services.living_docs.git_recovery import recover_git_activations
            bridge = RagBridge(log_manager=self.log_mgr)
            revision_bridge = RagBridge(log_manager=self.log_mgr, revisioned_rag=bridge.http_revisioned_adapter())
            recovered = await recover_git_activations(store=FileCandidateStore(candidate_root),
                rag=revision_bridge, output=config.project_root / "output" / project_id,
                source_baseline=ctx.last_ingested_commit_sha)
            for summary in recovered:
                recovery_run = journal.start(summary["before"] or "", summary["after"], "recovery")
                await self._persist_result(project_id, summary["after"], DiffPatchResult(
                    action="true_patch", summary=summary, patched_docs=summary["touched_docs"], advance_commit=True))
                journal.finish(recovery_run, summary)
            if recovered:
                ctx = await self._load_context(project_id)
                if ctx is None:
                    raise ValueError("Project disappeared during recovery")

        await self.log_mgr.emit(
            project_id,
            f"Git update detected: {before_sha[:12]}..{after_sha[:12]}",
            "info",
        )

        effective_before = ctx.last_ingested_commit_sha or before_sha
        if effective_before == after_sha:
            journal.finish(run_id, {"action": "skipped", "reason": "Commit already documented"})
            return True
        if ctx.last_ingested_commit_sha:
            await fetch_remote(ctx.local_path, ctx.credential, git_func=_git)
            common = (await _git(ctx.local_path, "merge-base", effective_before, after_sha)).strip()
            if common == after_sha:
                journal.finish(run_id, {"action": "skipped", "reason": "Out-of-order delivery; newer commit already documented"})
                return True
            if common and common != effective_before and not trigger.startswith("repair_latest:"):
                journal.finish(run_id, {"action": "held", "reason": "History was rewritten; explicit repair required"})
                return False
        if effective_before != before_sha:
            await self.log_mgr.emit(
                project_id,
                (
                    "Webhook base differs from the active documentation baseline; "
                    f"reconciling {effective_before[:12]}..{after_sha[:12]}"
                ),
                "warning",
            )
        result = await self._run(
            ctx,
            before_sha=effective_before,
            after_sha=after_sha,
            trigger=trigger,
        )
        await self._persist_result(project_id, after_sha, result)
        journal.finish(run_id, result.summary)
        return result.error is None

    async def _load_context(self, project_id: str) -> AutoDocUpdateContext | None:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(project_id)
            if not project:
                logger.warning("Auto-doc update ignored; project not found: %s", project_id)
                return None
            return AutoDocUpdateContext(
                project_id=project.id,
                local_path=project.local_path,
                doc_generation_provider=project.doc_generation_provider,
                credential=provider_for_project(project),
                last_ingested_commit_sha=project.last_ingested_commit_sha,
            )

    async def _run(
        self,
        ctx: AutoDocUpdateContext,
        *,
        before_sha: str,
        after_sha: str,
        trigger: str,
    ) -> DiffPatchResult:
        return await AutoDocAgentRuntime(
            log_mgr=self.log_mgr,
            router=self.router,
            quick_scanner=self.quick_scanner,
        ).run(ctx, before_sha=before_sha, after_sha=after_sha, trigger=trigger)

    async def _persist_result(
        self,
        project_id: str,
        after_sha: str,
        result: DiffPatchResult,
    ) -> None:
        # Slice 4 (US-9): this is the single choke point every webhook/poller
        # push flows through exactly once (AutoDocUpdateService.run() is the
        # sole entrypoint -- see git_reingestion.run_git_reingestion, which is
        # only ever reached via this class's _scoped_regen). result.summary is
        # already the finalized, normalized outcome summary (before/after
        # SHAs, outcome, touched_docs, reason_code), so it is reused directly
        # as the change-history source of truth rather than recomputed.
        record_push_outcome(project_id, result.summary)
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_git_update_summary(project_id, result.summary, error=result.error)
            if result.advance_commit and result.error is None:
                await registry.update_last_ingested_commit(project_id, after_sha)
                await registry.update_status(project_id, "ready", error_message=None)
            elif result.action in {"skipped", "targeted_regen", "full_regen"} and result.error is None:
                await registry.update_status(project_id, "ready", error_message=None)
            elif result.action in {"manual_review", "held"}:
                await registry.update_status(project_id, "ready", error_message=result.error)
            else:
                await registry.update_status(project_id, "error", error_message=result.error)
            await db.commit()
