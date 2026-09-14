"""Version-pinned OpenCode adapter for bounded landscape claim analysis.

Shared knowledge contracts own meaning, grounding and persistence. This module
only schedules their declared artifacts and records bounded unresolved work.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any


def landscape_coverage(staging_dir: Path) -> Any:
    """Count grounded requirements, explicitly separate from semantic review."""
    from mcp_server.providers.custom.knowledge_contracts import accepted_knowledge_claims, load_knowledge_manifest
    from mcp_server.providers.custom.models import CoverageReport

    manifest = load_knowledge_manifest(staging_dir)
    accepted = accepted_knowledge_claims(staging_dir, manifest)
    addressed = {r for claim in accepted if claim.kind != "unknown" for r in claim.requirement_ids}
    expected = {r.requirement_id for r in manifest.requirements if r.mandatory and r.explanation_required}
    missing = expected - addressed
    missing_units = sorted({r.unit_id for r in manifest.requirements if r.requirement_id in missing})
    return CoverageReport(
        ok=not missing, coverage_count=len(expected & addressed), expected_count=len(expected),
        missing_units=missing_units,
        errors=[f"Unresolved knowledge requirement: {r}" for r in sorted(missing)],
        warnings=["Counts describe source-grounded requirements; semantic interpretation remains unreviewed."],
    )


def submit_landscape_claims(staging_dir: Path, metadata: dict[str, Any], content: str) -> dict[str, Any]:
    from mcp_server.providers.custom.knowledge_contracts import (
        KnowledgeClaim, KnowledgeTask, load_knowledge_manifest, persist_knowledge_batch, validate_knowledge_batch,
    )

    task = KnowledgeTask.model_validate(metadata["knowledge_task"])
    manifest = load_knowledge_manifest(staging_dir)
    validated = validate_knowledge_batch(content, task, manifest)
    persisted = persist_knowledge_batch(staging_dir, task, validated)
    # Repairs may submit only the missing claims. Persistence merges successful
    # siblings, so feedback must describe that same cumulative state; otherwise
    # the agent repeatedly resubmits already accepted explanations.
    record = json.loads(persisted.read_text(encoding="utf-8"))
    accepted = [KnowledgeClaim.model_validate(claim) for claim in record["claims"]]
    addressed = {rid for claim in accepted if claim.kind != "unknown" for rid in claim.requirement_ids}
    unresolved = sorted(set(task.requirement_ids) - addressed)
    return {
        "ok": not validated.errors and not unresolved,
        "output_artifact": task.output_artifact,
        "accepted_count": len(validated.accepted),
        "addressed_requirement_count": len(set(task.requirement_ids) & addressed),
        "errors": validated.errors,
        "unresolved_requirement_ids": unresolved,
    }


def plan_landscape_analysis(ctx: Any, facts: Any) -> Any:
    """Offline preflight; no model work may precede an impossible scope plan."""
    from mcp_server.config import config
    from mcp_server.providers.custom.knowledge_requirements import prepare_landscape
    from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError

    budgets = (ctx.generation_profile or {}).get("budgets", {})
    plan = prepare_landscape(
        staging_dir=ctx.staging_dir,
        scan_output_dir=config.intodoc_output_dir / ctx.project_id,
        facts=facts,
        max_requirements_per_task=int(budgets.get("max_requirements_per_task", 12)),
        max_evidence_bytes=int(budgets.get("max_evidence_bytes", 60_000)),
    )
    manifest = plan.manifest
    from mcp_server.services.generation_profile import normalize_profile
    profile = normalize_profile(ctx.generation_profile)
    if profile and profile.get("analysis_version") == 3:
        from mcp_server.providers.custom.landscape_scopes import prepare_semantic_scopes
        plan = prepare_semantic_scopes(
            staging_dir=ctx.staging_dir, atomic_plan=plan,
            max_scope_evidence_bytes=int(budgets.get("max_scope_evidence_bytes", 400_000)),
            max_units_per_scope=int(budgets.get("max_scope_units", 6)),
        )
        manifest = plan.manifest
    max_tasks = max(0, int(budgets.get("max_analysis_tasks", 200)))
    from mcp_server.providers.custom.knowledge_contracts import LANDSCAPE_ROOT, write_json
    write_json(ctx.staging_dir / LANDSCAPE_ROOT / "analysis_preflight.json", {
        "planned_scopes": len(plan.tasks), "scope_limit": max_tasks,
        "fits": len(plan.tasks) <= max_tasks,
        "atomic_evidence_count": len(manifest.evidence),
        "explanation_requirements": sum(r.explanation_required for r in manifest.requirements),
        "session_timeout_seconds": int(budgets.get("analysis_timeout_seconds", 300)),
        "total_timeout_seconds": int(budgets.get("analysis_total_timeout_seconds", 1800)),
        "session_processed_token_limit": int(budgets.get("max_analysis_session_tokens", 400_000)),
        "total_processed_token_limit": int(budgets.get("max_analysis_total_processed_tokens", 5_000_000)),
        "cost_dispatch_ceiling_usd": float(
            budgets.get("analysis_cost_dispatch_ceiling_usd", 10.0)
        ),
        "semantic_review": "not_performed",
    })
    if len(plan.tasks) > max_tasks:
        raise OpenCodeCompatibilityError(
            f"Analysis plan needs {len(plan.tasks)} scopes but budget permits {max_tasks}; "
            "no analysis sessions started. Review scope sizing or budget before retrying."
        )
    return plan


async def execute_landscape_analysis(
    provider: Any, *, resolved: str, ctx: Any, facts: Any, ledger: Any,
    log_cb: Any, on_complete: Any,
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.knowledge_contracts import (
        LANDSCAPE_ROOT, write_json, knowledge_task_prompt, render_knowledge_group,
    )
    from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError, OpenCodeTaskContract
    plan = plan_landscape_analysis(ctx, facts)
    manifest = plan.manifest
    budgets = (ctx.generation_profile or {}).get("budgets", {})
    followups = max(0, int(budgets.get("max_analysis_followups", 1)))
    semaphore = asyncio.Semaphore(max(1, int(config.opencode_analyze_concurrency)))
    receipts: dict[str, dict[str, Any]] = {}
    analysis_started = time.monotonic()
    total_timeout = int(budgets.get("analysis_total_timeout_seconds", 1800))
    total_token_limit = int(budgets.get("max_analysis_total_processed_tokens", 5_000_000))
    cost_dispatch_ceiling = float(
        budgets.get("analysis_cost_dispatch_ceiling_usd", 10.0)
    )
    total_processed_tokens = 0
    total_cost_usd = 0.0
    await log_cb(ctx.project_id,
        f"Landscape analysis plan: {len(plan.tasks)} scope(s), "
        f"concurrency={config.opencode_analyze_concurrency}; all evidence retained", "info")

    def remaining(task: Any) -> list[str]:
        from mcp_server.providers.custom.knowledge_contracts import accepted_knowledge_claims

        addressed = {
            requirement_id
            for claim in accepted_knowledge_claims(ctx.staging_dir, manifest)
            if claim.kind != "unknown"
            for requirement_id in claim.requirement_ids
        }
        return sorted(set(task.requirement_ids) - addressed)

    async def execute(task: Any, attempt_number: int) -> None:
        nonlocal total_processed_tokens, total_cost_usd
        unresolved = remaining(task)
        attempts = receipts.get(task.task_id, {}).get("attempts", 0)
        async with semaphore:
            elapsed = time.monotonic() - analysis_started
            budget_reason = ""
            if elapsed >= total_timeout:
                budget_reason = "analysis_total_timeout"
            elif total_processed_tokens >= total_token_limit:
                budget_reason = "analysis_total_processed_token_budget"
            elif total_cost_usd >= cost_dispatch_ceiling:
                budget_reason = "analysis_cost_dispatch_ceiling"
            if budget_reason and unresolved:
                receipts[task.task_id] = {
                    "attempts": attempts, "unresolved_requirement_ids": unresolved,
                    "reason": budget_reason,
                }
                return
            for attempt in (attempt_number,):
                if not unresolved:
                    break
                active = task.model_copy(update={"requirement_ids": unresolved})
                task_payload = json.loads(
                    (ctx.staging_dir / task.input_artifact).read_text(encoding="utf-8")
                )
                evidence_artifacts = tuple(dict.fromkeys((
                    task.input_artifact,
                    *task_payload.get("evidence_artifacts", []),
                )))
                contract = OpenCodeTaskContract(
                    phase="analyze", target=f"knowledge:{task.task_id}:{attempt}",
                    prompt_artifact="knowledge-claims-v2",
                    prompt=knowledge_task_prompt(active, manifest),
                    evidence_artifacts=evidence_artifacts,
                    output_artifact=task.output_artifact,
                    metadata={"knowledge_task": active.model_dump(mode="json")},
                )
                step = f"knowledge_{task.task_id}_{attempt}"
                before = tuple(unresolved)
                attempts += 1
                try:
                    execution = await provider._execute_step(
                        resolved=resolved, ctx=ctx, phase="analyze", step=step,
                        contracts=[contract], allowlist={}, session_id=None, ledger=ledger,
                        log_cb=log_cb, require_outputs=False,
                        exploration_artifacts=evidence_artifacts,
                        timeout_seconds=int(budgets.get("analysis_timeout_seconds", 300)),
                        max_model_turns=int(budgets.get("max_analysis_session_turns", 16)),
                        max_processed_tokens=int(budgets.get("max_analysis_session_tokens", 400_000)),
                    )
                    session_processed = getattr(execution, "processed_tokens", None)
                    if session_processed is None:
                        session_processed = (getattr(execution, "input_tokens", 0)
                                             + getattr(execution, "output_tokens", 0))
                    total_processed_tokens += int(session_processed)
                    total_cost_usd += float(getattr(execution, "cost_usd", 0.0))
                    on_complete(step, execution)
                except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                    # A bounded/terminated session returns no execution object,
                    # but the CLI persists completed-turn usage independently.
                    # Charge it to the run ceiling so repeated bounded failures
                    # cannot bypass the aggregate budget.
                    from mcp_server.providers.opencode_cli import _safe_name
                    interrupted = (ctx.staging_dir / "_audit" / "opencode_interrupted_usage"
                                   / f"{_safe_name(step)}.json")
                    try:
                        usage = json.loads(interrupted.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        usage = {}
                    total_processed_tokens += int(usage.get("processed_tokens") or 0)
                    total_cost_usd += float(usage.get("cost_usd") or 0.0)
                    await log_cb(ctx.project_id, f"Landscape analysis {task.task_id}: {exc}; accepted claims retained", "warning")
                unresolved = remaining(task)
                if tuple(unresolved) == before:
                    # One targeted recovery is useful after a failed initial
                    # session. Repeated identical gaps cannot create a loop.
                    if attempt > 0:
                        break
            receipts[task.task_id] = {
                "attempts": attempts, "unresolved_requirement_ids": unresolved,
                "reason": "analysis_unfinished" if unresolved else "grounded_claims_recorded",
            }
            resolved_scopes = sum(not item["unresolved_requirement_ids"] for item in receipts.values())
            attempted_scopes = sum(item["attempts"] > 0 for item in receipts.values())
            write_json(ctx.staging_dir / LANDSCAPE_ROOT / "analysis_progress.json", {
                "contract": "talend-landscape-v2", "snapshot_id": plan.snapshot_id,
                "planned_scopes": len(plan.tasks), "attempted_scopes": attempted_scopes,
                "resolved_scopes": resolved_scopes,
                "processed_tokens": total_processed_tokens, "cost_usd": total_cost_usd,
                "tasks": receipts, "semantic_review": "not_performed",
            })
            await log_cb(ctx.project_id,
                f"Landscape analysis progress: {resolved_scopes}/{len(plan.tasks)} scopes resolved, "
                f"{attempted_scopes} attempted; "
                f"{len(unresolved)} unanswered in {task.task_id}", "info")

    # Every scope gets its first pass before any scope consumes repair capacity.
    for attempt in range(1 + followups):
        await asyncio.gather(*(execute(task, attempt) for task in plan.tasks))
    analysis_dir = ctx.staging_dir / "_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    for group in facts.groups:
        (analysis_dir / f"{group}.md").write_text(
            render_knowledge_group(ctx.staging_dir, group, manifest), encoding="utf-8",
        )
    from mcp_server.providers.custom.flow_analysis import build_source_journeys
    build_source_journeys(ctx.staging_dir, config.intodoc_output_dir / ctx.project_id)
    receipt_path = ctx.staging_dir / "_evidence_registry" / "landscape" / "analysis_progress.json"
    resolved_scopes = sum(not item["unresolved_requirement_ids"] for item in receipts.values())
    attempted_scopes = sum(item["attempts"] > 0 for item in receipts.values())
    write_json(receipt_path, {
        "contract": "talend-landscape-v2", "snapshot_id": plan.snapshot_id,
        "planned_scopes": len(plan.tasks), "attempted_scopes": attempted_scopes,
        "resolved_scopes": resolved_scopes,
        "processed_tokens": total_processed_tokens, "cost_usd": total_cost_usd,
        "tasks": receipts, "semantic_review": "not_performed",
    })
    coverage = landscape_coverage(ctx.staging_dir)
    unresolved_count = coverage.expected_count - coverage.coverage_count
    await log_cb(
        ctx.project_id,
        f"Landscape typed analysis: {len(plan.tasks)} scope(s), "
        f"{unresolved_count} mandatory explanation requirement(s) unresolved",
        "warning" if unresolved_count else "info",
    )
