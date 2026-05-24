"""Internal bounded auto-doc agent workflow for Git updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.doc_impact_agent import DocImpactAgent, DocImpactPlan
from mcp_server.services.git_diff_patcher import (
    DiffPatchResult,
    _chunk_diff,
    _failed,
    _git,
    _manual,
    _run_diff_patch,
    parse_name_status,
)
from mcp_server.services.git_reingestion import run_git_reingestion
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.quick_change_scanner import QuickChangeScanner, QuickScanReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoDocUpdateContext:
    project_id: str
    local_path: str
    doc_generation_provider: str | None
    github_token_enc: str | None


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
        impact_agent: DocImpactAgent | None = None,
        quick_scanner: QuickChangeScanner | None = None,
    ) -> None:
        self.log_mgr = log_mgr
        self.impact_agent = impact_agent or DocImpactAgent()
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
            summary.pop("_quick_scan", None)

            if not quick_scan.doc_relevant_files:
                return await self._skip(
                    ctx.project_id,
                    ctx.local_path,
                    after_sha,
                    summary,
                    "Only ignored/non-doc-impacting files changed",
                )

            plan = await self._decide(
                quick_scan=quick_scan,
                diff_chunks=inspect_result["diff_chunks"],
                doc_generation_provider=ctx.doc_generation_provider,
            )
            summary.update(plan.to_dict())
            summary["touched_docs"] = list(plan.affected_docs)

            if len(plan.affected_docs) > config.git_diff_patch_max_docs:
                return self._manual(
                    summary,
                    f"Agent requested {len(plan.affected_docs)} docs; max auto edit scope is {config.git_diff_patch_max_docs}",
                )

            if plan.strategy == "skip":
                return await self._skip(
                    ctx.project_id,
                    ctx.local_path,
                    after_sha,
                    summary,
                    plan.reason or "Agent determined no doc update is needed",
                )
            if plan.strategy == "manual_review":
                return self._manual(summary, plan.reason or "Agent requested manual review")
            if plan.strategy == "targeted_regen":
                return await self._targeted_regen(
                    ctx,
                    before_sha,
                    after_sha,
                    trigger,
                    summary,
                    plan,
                    plan.reason or "Agent requested targeted regeneration",
                )
            if plan.confidence < config.git_diff_patch_min_confidence and plan.strategy != "bounded_patch":
                return self._manual(
                    summary,
                    f"Agent confidence too low for automatic action ({plan.confidence:.2f})",
                )

            return await self._edit_validate_and_sync(
                ctx,
                before_sha=before_sha,
                after_sha=after_sha,
                trigger=trigger,
                summary=summary,
                plan=plan,
            )
        except AutoDocToolBudgetExceeded as exc:
            return self._manual(summary, str(exc))
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
            "confidence": None,
            "manual_review_reason": None,
            "docs_dir": str(config.project_root / "output" / ctx.project_id / "docs"),
        }

    async def _inspect_diff(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
    ) -> dict[str, Any]:
        if ctx.github_token_enc:
            from mcp_server.utils.encryption import inject_token as _inject_token

            current_url = (await _git(ctx.local_path, "remote", "get-url", "origin")).strip()
            await _git(ctx.local_path, "remote", "set-url", "origin", _inject_token(current_url, ctx.github_token_enc))
        await _git(ctx.local_path, "fetch", "--quiet", "origin")
        name_status = await _git(ctx.local_path, "diff", "--name-status", before_sha, after_sha)
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
                *changed_files,
            )
        quick_scan = self.quick_scanner.scan(changes, full_diff_text)
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
            "_quick_scan": quick_scan,
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
        *,
        quick_scan: QuickScanReport,
        diff_chunks: list[dict[str, Any]],
        doc_generation_provider: str | None,
    ) -> DocImpactPlan:
        plan = await self.impact_agent.plan(
            quick_scan=quick_scan,
            diff_chunks=diff_chunks,
            doc_generation_provider=doc_generation_provider,
        )
        self._record_tool(
            "finish_decision",
            {
                "action": plan.strategy,
                "affected_docs": plan.affected_docs,
                "confidence": plan.confidence,
                "reason": plan.reason,
            },
        )
        return plan

    async def _edit_validate_and_sync(
        self,
        ctx: AutoDocUpdateContext,
        *,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        plan: DocImpactPlan,
    ) -> DiffPatchResult:
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
                github_token_enc=ctx.github_token_enc,
                impact_plan={**plan.to_dict(), "mode": "tool_loop_agent"},
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
            return await self._targeted_regen(
                ctx,
                before_sha,
                after_sha,
                trigger,
                merged,
                plan,
                clean_reason,
            )
        if last_result.action == "failed":
            return _failed(
                self._finalize_summary(
                    merged,
                    action="failed",
                    reason=str(reason),
                    repair_attempts=merged.get("repair_attempts", 0),
                ),
                str(reason),
            )
        return self._manual(
            merged,
            clean_reason,
            repair_attempts=int(merged.get("repair_attempts") or 0),
        )

    def _merge_patch_summary(self, base: dict[str, Any], patch_summary: dict[str, Any]) -> dict[str, Any]:
        merged = {**base, **patch_summary}
        merged["mode"] = "tool_loop_agent"
        merged["touched_docs"] = _summary_docs(merged)
        merged["tool_calls"] = list(self._tool_calls)
        merged["tool_call_count"] = len(self._tool_calls)
        return merged

    async def _skip(
        self,
        project_id: str,
        local_path: str,
        after_sha: str,
        summary: dict[str, Any],
        reason: str,
    ) -> DiffPatchResult:
        self._record_tool("finish", {"action": "skipped", "reason": reason})
        await _git(local_path, "pull", "--ff-only")
        await self.log_mgr.emit(project_id, f"Git update skipped: {reason}", "info")
        return DiffPatchResult(
            action="skipped",
            summary=self._finalize_summary(summary, action="skipped", reason=reason),
            advance_commit=True,
        )

    async def _targeted_regen(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        plan: DocImpactPlan,
        reason: str,
    ) -> DiffPatchResult:
        self._record_tool("targeted_regen_needed", {"reason": reason, "affected_docs": plan.affected_docs})
        await self.log_mgr.emit(ctx.project_id, f"Auto-doc targeted regeneration: {reason}", "warning")
        ok = await run_git_reingestion(
            ctx.project_id,
            before_sha,
            after_sha,
            triggered_by=trigger,
            log_mgr=self.log_mgr,
        )
        final_summary = self._finalize_summary(
            {**summary, **plan.to_dict(), "fallback_used": True, "fallback_reason": reason},
            action="targeted_regen" if ok else "manual_review",
            reason=reason if ok else f"Targeted regeneration failed: {reason}",
            touched_docs=plan.affected_docs,
        )
        if ok:
            return DiffPatchResult(action="targeted_regen", summary=final_summary, advance_commit=False)
        return _manual(final_summary, f"Targeted regeneration failed after agent request: {reason}")

    def _manual(
        self,
        summary: dict[str, Any],
        reason: str,
        *,
        repair_attempts: int | None = None,
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
        )
        final["manual_review_reason"] = reason
        return DiffPatchResult(
            action="manual_review",
            summary=final,
            error=f"Manual review required: {reason}",
        )

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
    ) -> dict[str, Any]:
        final = dict(summary)
        final["mode"] = "tool_loop_agent"
        final["action"] = action
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
        return final


class AutoDocUpdateService:
    """Single entrypoint for webhook and poller Git documentation updates."""

    def __init__(
        self,
        *,
        log_mgr: LogManager,
        impact_agent: DocImpactAgent | None = None,
        quick_scanner: QuickChangeScanner | None = None,
    ) -> None:
        self.log_mgr = log_mgr
        self.impact_agent = impact_agent or DocImpactAgent()
        self.quick_scanner = quick_scanner or QuickChangeScanner()

    async def run(
        self,
        *,
        project_id: str,
        before_sha: str,
        after_sha: str,
        trigger: str = "webhook",
    ) -> bool:
        ctx = await self._load_context(project_id)
        if not ctx:
            return False

        await self.log_mgr.emit(
            project_id,
            f"Git update detected: {before_sha[:12]}..{after_sha[:12]}",
            "info",
        )

        result = await self._run(ctx, before_sha=before_sha, after_sha=after_sha, trigger=trigger)
        await self._persist_result(project_id, after_sha, result)
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
                github_token_enc=getattr(project, "github_token_enc", None),
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
            impact_agent=self.impact_agent,
            quick_scanner=self.quick_scanner,
        ).run(ctx, before_sha=before_sha, after_sha=after_sha, trigger=trigger)

    async def _skip(
        self,
        project_id: str,
        local_path: str,
        after_sha: str,
        summary: dict[str, Any],
        reason: str,
    ) -> DiffPatchResult:
        await _git(local_path, "pull", "--ff-only")
        await self.log_mgr.emit(project_id, f"Git update skipped: {reason}", "info")
        return DiffPatchResult(
            action="skipped",
            summary={**summary, "action": "skipped", "reason": reason},
            advance_commit=True,
        )

    async def _targeted_regen(
        self,
        ctx: AutoDocUpdateContext,
        before_sha: str,
        after_sha: str,
        trigger: str,
        summary: dict[str, Any],
        plan: DocImpactPlan,
        reason: str,
    ) -> DiffPatchResult:
        await self.log_mgr.emit(ctx.project_id, f"Auto-doc fallback: {reason}", "warning")
        ok = await run_git_reingestion(
            ctx.project_id,
            before_sha,
            after_sha,
            triggered_by=trigger,
            log_mgr=self.log_mgr,
        )
        final_summary = {
            **summary,
            **plan.to_dict(),
            "fallback_used": True,
            "fallback_reason": reason,
        }
        if ok:
            return DiffPatchResult(
                action="targeted_regen",
                summary={**final_summary, "action": "targeted_regen"},
                advance_commit=False,
            )
        return _manual(
            {**final_summary, "action": "manual_review"},
            f"Targeted regeneration failed after bounded update issue: {reason}",
        )

    async def _persist_result(
        self,
        project_id: str,
        after_sha: str,
        result: DiffPatchResult,
    ) -> None:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_git_update_summary(project_id, result.summary, error=result.error)
            if result.advance_commit and result.error is None:
                await registry.update_last_ingested_commit(project_id, after_sha)
                await registry.update_status(project_id, "ready", error_message=None)
            elif result.action in {"skipped", "targeted_regen"} and result.error is None:
                await registry.update_status(project_id, "ready", error_message=None)
            elif result.action == "manual_review":
                await registry.update_status(project_id, "ready", error_message=result.error)
            else:
                await registry.update_status(project_id, "error", error_message=result.error)
            await db.commit()
