"""Composite-upload orchestration: one scoped doc-generation run per sub-project.

A composite upload (several independent exports zipped into one scan) or an
oversized single-root upload cannot run the legacy pipeline whole -- the
per-run model-call budget starves and Phase 2 coverage collapses (verified on
the werkorder scan: 429 units, ~350 ``budget_exhaustion`` skips). The
deterministic partition planner (``providers/custom/partition_planner``) splits
such a scan into sub-projects that each fit the budget; this module is the
"hands" that runs the legacy pipeline once per partition:

* each partition run is a full, independent ``DocGenerationCoordinator``
  generation with its own staging dir, generation lock, call budget, atomic
  swap and DB run record, publishing to ``output/{project_id}/{subproject}/docs``;
* before any partition runs, the deterministic upload-level cross-project
  index (job-call edges across sub-projects, shared systems) is written to
  ``output/{project_id}/topology/`` -- partition runs consume it to NOTE their
  external references, and it is rendered/pushed as the upload-level
  connective-topology document;
* single-partition scans never reach this module -- the caller keeps the
  legacy single-run path unchanged.

Failure isolation: one failed sub-project never rolls back or blocks the
others; its staging is preserved for resume, and the orchestration reports a
project-level error listing the failed sub-projects while the succeeded ones
stay published.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.providers.custom.partition_planner import (
    PartitionPlan,
    build_cross_project_index,
    cross_project_index_path,
    partition_output_subdir,
    plan_partitions,
    render_cross_project_topology_md,
    upload_topology_dir,
)
from mcp_server.providers.custom.validators import build_facts_manifest
from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.rag_support_package import (
    build_entity_aliases,
    collect_internal_artifacts,
    replace_analysis_cache_from_rag_support,
)

if TYPE_CHECKING:
    from mcp_server.providers.custom.models import FactsManifest
    from mcp_server.services.claude_service import DocGenerationCoordinator

logger = logging.getLogger(__name__)

# Upload-level artifacts (deterministic, no LLM).
CROSS_PROJECT_TOPOLOGY_DOC = "CROSS_PROJECT_TOPOLOGY.md"
PARTITION_STATE_FILENAME = "partition_state.json"


@dataclass
class PartitionRunOutcome:
    """Result of one sub-project's scoped generation run."""

    partition: str
    subdir: str
    run_id: int | None = None
    success: bool = False
    skipped: bool = False
    doc_count: int = 0
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


def plan_composite_generation(
    project_id: str,
) -> "tuple[PartitionPlan, FactsManifest] | None":
    """Deterministic partition plan for the orchestrator.

    Returns ``None`` whenever the legacy single-run path must be used: planner
    disabled, scan missing/corrupt, or the scan resolves to a single partition
    (every existing single-root project). Never raises.
    """
    if not config.docgen_partition_orchestration:
        return None
    try:
        scan_output_dir = config.intodoc_output_dir / project_id
        facts = build_facts_manifest(project_id, scan_output_dir)
        plan = plan_partitions(facts)
    except Exception:
        logger.warning(
            "Partition planning failed for %s -- using the single-run path",
            project_id,
            exc_info=True,
        )
        return None
    if len(plan.partitions) <= 1:
        return None
    return plan, facts


def write_cross_project_artifacts(
    project_id: str,
    facts: "FactsManifest",
    plan: PartitionPlan,
) -> dict[str, Any]:
    """Build and persist the upload-level cross-project index + connective map.

    Written BEFORE any partition run so each scoped run can stage its own
    ``cross_project_refs.md`` from it. Purely deterministic (scan facts only).
    """
    scan_output_dir = config.intodoc_output_dir / project_id
    scan_manifest = _load_json(scan_output_dir / "scan" / "manifest.json")
    landscape = _load_json(scan_output_dir / "scan" / "landscape.json")

    index = build_cross_project_index(
        facts, plan, scan_manifest=scan_manifest, landscape=landscape
    )

    topo_dir = upload_topology_dir(project_id)
    topo_dir.mkdir(parents=True, exist_ok=True)
    cross_project_index_path(project_id).write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (topo_dir / CROSS_PROJECT_TOPOLOGY_DOC).write_text(
        render_cross_project_topology_md(index), encoding="utf-8"
    )
    return index


async def run_partitioned_generation(
    project_id: str,
    run_id: int,
    log_mgr,
    coordinator: "DocGenerationCoordinator",
    *,
    plan: PartitionPlan,
    facts: "FactsManifest",
    resume: bool = True,
    generate_kwargs: dict[str, Any] | None = None,
) -> list[PartitionRunOutcome]:
    """Run one scoped legacy generation per partition and publish the results.

    ``run_id`` is the orchestration-level GenerateRun created by the route; it
    is completed at the end with aggregated totals. Each partition gets its own
    child GenerateRun (``triggered_by="composite"``, ``partition_name=<name>``)
    so per-sub-project cost/duration/verdict stay inspectable.
    """
    generate_kwargs = dict(generate_kwargs or {})
    output_root = config.project_root / "output" / project_id
    scan_output_dir = config.intodoc_output_dir / project_id

    await log_mgr.emit(
        project_id,
        "Composite upload detected -- running one scoped generation per "
        f"sub-project:\n{plan.table()}",
        level="info",
    )

    # Upload-level deterministic connective layer, consumed by every partition run.
    try:
        write_cross_project_artifacts(project_id, facts, plan)
        await log_mgr.emit(
            project_id,
            f"Cross-project index written to {cross_project_index_path(project_id)}",
            level="info",
        )
    except Exception as exc:  # noqa: BLE001 - additive layer must not block generation
        logger.warning("Cross-project index build failed for %s: %s", project_id, exc, exc_info=True)
        await log_mgr.emit(
            project_id,
            f"Cross-project index build failed (runs continue without it): {exc}",
            level="warning",
        )

    fingerprint = _scan_fingerprint(scan_output_dir)
    partition_state = _read_partition_state(project_id)

    outcomes: list[PartitionRunOutcome] = []
    cancelled = False
    for part in plan.partitions:
        subdir = partition_output_subdir(part.name) or part.name
        outcome = PartitionRunOutcome(partition=part.name, subdir=subdir)
        outcomes.append(outcome)
        part_output = output_root / subdir

        if _partition_completed(partition_state, part.name, fingerprint, part_output) and resume:
            outcome.skipped = True
            outcome.success = True
            outcome.doc_count = _count_public_docs(part_output / "docs")
            await log_mgr.emit(
                project_id,
                f"[{part.name}] up to date for current scan -- skipping "
                f"({outcome.doc_count} docs published)",
                level="info",
            )
            continue

        child_run_id = await _create_child_run(project_id, part.name)
        outcome.run_id = child_run_id
        await log_mgr.emit(
            project_id,
            f"[{part.name}] starting scoped run "
            f"({part.unit_count} units, {part.group_count} groups, "
            f"budget {part.call_budget}/{plan.cap}) -> {part_output / 'docs'}",
            level="info",
        )

        try:
            result = await coordinator.generate_docs(
                project_id,
                resume=resume,
                run_id=child_run_id if child_run_id is not None else run_id,
                partition=part,
                **generate_kwargs,
            )
        except asyncio.CancelledError:
            outcome.error = "Generation cancelled by user"
            await _complete_child_run(child_run_id, status="failed", error=outcome.error)
            await log_mgr.emit(project_id, "Generation cancelled by user.", level="warning")
            cancelled = True
            break
        except Exception as exc:  # noqa: BLE001 - isolate partition failures
            logger.exception("Partition run crashed: %s/%s", project_id, part.name)
            outcome.error = str(exc)
            outcome.result = {"success": False, "error": str(exc)}
            await _complete_child_run(child_run_id, status="failed", error=str(exc))
            continue

        outcome.result = result
        staging_dir = part_output / "docs_staging"
        success = bool(result.get("success"))
        doc_count = _count_public_docs(staging_dir)

        if success and doc_count > 0:
            try:
                swap_docs(part_output)
                replace_analysis_cache_from_rag_support(part_output)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Swap failed for %s/%s", project_id, part.name)
                outcome.error = f"Publish swap failed: {exc}"
                await _complete_child_run(
                    child_run_id, status="failed", error=outcome.error, result=result
                )
                continue

            outcome.success = True
            outcome.doc_count = doc_count
            quality_score = _score_partition_docs(project_id, part_output / "docs")

            rag_errors = await _push_partition_docs(
                project_id, subdir, part_output / "docs", log_mgr
            )
            if rag_errors:
                await log_mgr.emit(
                    project_id,
                    f"[{part.name}] RAG push errors (docs are saved): {rag_errors}",
                    level="error",
                )

            await _complete_child_run(
                child_run_id,
                status="completed",
                doc_count=doc_count,
                result=result,
                quality_score=quality_score,
                error="; ".join(rag_errors) if rag_errors else None,
                run_verdict="published_with_rag_error" if rag_errors else "published",
            )
            partition_state[part.name] = {
                "scan_fingerprint": fingerprint,
                "doc_count": doc_count,
                "run_id": child_run_id,
            }
            _write_partition_state(project_id, partition_state)
            await log_mgr.emit(
                project_id,
                f"[{part.name}] published {doc_count} docs to {part_output / 'docs'}",
                level="info",
            )
        elif success:
            # Success with no docs is a failure (mirrors the single-run path).
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            outcome.error = "Generation produced no documents"
            await _complete_child_run(
                child_run_id, status="failed", error=outcome.error, result=result
            )
            await log_mgr.emit(
                project_id,
                f"[{part.name}] produced no documents -- marked failed",
                level="error",
            )
        else:
            outcome.error = result.get("error") or "Doc generation failed"
            await _complete_child_run(
                child_run_id, status="failed", error=outcome.error, result=result
            )
            await log_mgr.emit(
                project_id,
                f"[{part.name}] failed: {outcome.error} "
                "(staging preserved -- generate again to resume this sub-project)",
                level="warning",
            )

    succeeded = [o for o in outcomes if o.success]
    failed = [o for o in outcomes if not o.success and not o.skipped]
    total_docs = sum(o.doc_count for o in succeeded)

    # Upload-level connective topology + internal support layers in RAG.
    if succeeded and not cancelled:
        await _push_upload_level_artifacts(project_id, log_mgr)
        await _sync_internal_layers_multi(
            project_id, run_id, output_root, [o for o in outcomes if o.success], log_mgr
        )

    # Aggregate + finalize the orchestration run and the project status.
    totals = _aggregate_totals([o.result for o in outcomes if o.result])
    error_message = (
        "Sub-project runs failed: "
        + ", ".join(f"{o.partition} ({o.error})" for o in failed)
        if failed
        else None
    )
    if cancelled and error_message is None:
        error_message = "Generation cancelled by user"

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if succeeded and not failed and not cancelled:
            await registry.update_status(project_id, "ready")
        elif succeeded:
            await registry.update_status(project_id, "error", error_message=error_message)
        else:
            await registry.update_status(
                project_id, "error", error_message=error_message or "Doc generation failed"
            )
        if total_docs:
            await registry.update_generate_result(project_id, doc_count=total_docs)
        log_path = log_mgr.get_current_log_path(project_id)
        await registry.complete_generate_run(
            run_id,
            status="completed" if succeeded and not failed and not cancelled else "failed",
            doc_count=total_docs,
            cost_usd=totals.get("cost_usd"),
            duration_ms=totals.get("duration_ms"),
            input_tokens=totals.get("input_tokens"),
            output_tokens=totals.get("output_tokens"),
            model_name=totals.get("model_name"),
            write_model_name=totals.get("write_model_name"),
            error_message=error_message,
            log_file=log_path.name if log_path else None,
            run_verdict="published" if succeeded and not failed and not cancelled else "failed",
        )
        await db.commit()

    await log_mgr.emit(
        project_id,
        f"Composite generation finished: {len(succeeded)}/{len(outcomes)} sub-projects "
        f"published, {total_docs} docs total"
        + (f"; failed: {', '.join(o.partition for o in failed)}" if failed else ""),
        level="error" if failed else "info",
    )
    return outcomes


# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_fingerprint(scan_output_dir: Path) -> str:
    try:
        from mcp_server.services.business_topology.evidence import compute_scan_fingerprint

        return compute_scan_fingerprint(scan_output_dir)
    except Exception:  # noqa: BLE001 - fingerprint is only a resume optimization
        return ""


def _partition_state_path(project_id: str) -> Path:
    return upload_topology_dir(project_id) / PARTITION_STATE_FILENAME


def _read_partition_state(project_id: str) -> dict[str, Any]:
    return _load_json(_partition_state_path(project_id))


def _write_partition_state(project_id: str, state: dict[str, Any]) -> None:
    path = _partition_state_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _partition_completed(
    state: dict[str, Any],
    partition_name: str,
    fingerprint: str,
    part_output: Path,
) -> bool:
    """A partition is up to date when its recorded fingerprint matches the
    current scan AND its published docs still exist on disk."""
    if not fingerprint:
        return False
    entry = state.get(partition_name)
    if not isinstance(entry, dict) or entry.get("scan_fingerprint") != fingerprint:
        return False
    return _count_public_docs(part_output / "docs") > 0


def _count_public_docs(docs_dir: Path) -> int:
    if not docs_dir.exists():
        return 0
    return len([p for p in docs_dir.glob("*.md") if not p.name.startswith("_")])


async def _create_child_run(project_id: str, partition_name: str) -> int | None:
    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            # The trigger source is "composite"; the partition is its own
            # field (encoding it into triggered_by overflowed the column).
            run = await registry.create_generate_run(
                project_id, triggered_by="composite", partition_name=partition_name
            )
            await db.commit()
            return run.id
    except Exception:  # noqa: BLE001 - run records are observability, not control flow
        logger.warning(
            "Could not create child GenerateRun for %s/%s",
            project_id,
            partition_name,
            exc_info=True,
        )
        return None


async def _complete_child_run(
    child_run_id: int | None,
    *,
    status: str,
    doc_count: int = 0,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    quality_score: float | None = None,
    run_verdict: str | None = None,
) -> None:
    if child_run_id is None:
        return
    result = result or {}
    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.complete_generate_run(
                child_run_id,
                status=status,
                doc_count=doc_count,
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                quality_score=quality_score,
                error_message=error,
                run_verdict=run_verdict or ("failed" if status == "failed" else None),
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not complete child GenerateRun %s", child_run_id, exc_info=True)


def _score_partition_docs(project_id: str, docs_dir: Path) -> float | None:
    try:
        from mcp_server.services.doc_quality_scorer import score_project

        report = score_project(project_id, docs_dir=docs_dir)
        metrics = {"doc_quality": report.to_dict()}
        (docs_dir / "run_metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str), encoding="utf-8"
        )
        return report.overall
    except Exception:  # noqa: BLE001 - scoring is advisory
        logger.warning("Doc quality scoring failed for %s (%s)", project_id, docs_dir, exc_info=True)
        return None


async def _push_partition_docs(
    project_id: str,
    subdir: str,
    docs_dir: Path,
    log_mgr,
) -> list[str]:
    """Push one sub-project's public docs to RAG under `{subproject}/` names.

    Prefixing keeps the six sub-projects' identically named enterprise docs
    (00_AGENT_BRIEF.md x N) distinct in the vector store while retaining the
    project-level tenancy (`project_id` is unchanged).
    """
    docs_payload: list[dict[str, str]] = []
    for path in sorted(docs_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s for RAG push: %s", path, exc)
            continue
        if content.strip():
            docs_payload.append({"filename": f"{subdir}/{path.name}", "content": content})
    if not docs_payload:
        return []
    bridge = RagBridge(log_manager=log_mgr)
    try:
        result = await bridge.push_docs(project_id=project_id, docs=docs_payload)
    except Exception as exc:  # noqa: BLE001
        return [f"RAG push failed for {subdir}: {exc}"]
    return list(result.get("errors") or [])


async def _push_upload_level_artifacts(project_id: str, log_mgr) -> None:
    """Push the deterministic cross-project topology doc to RAG (upload level)."""
    topo_md = upload_topology_dir(project_id) / CROSS_PROJECT_TOPOLOGY_DOC
    if not topo_md.exists():
        return
    try:
        bridge = RagBridge(log_manager=log_mgr)
        await bridge.push_docs(
            project_id=project_id,
            docs=[{
                "filename": CROSS_PROJECT_TOPOLOGY_DOC,
                "content": topo_md.read_text(encoding="utf-8"),
            }],
        )
    except Exception as exc:  # noqa: BLE001
        await log_mgr.emit(
            project_id,
            f"Cross-project topology RAG push failed (doc is saved on disk): {exc}",
            level="error",
        )


async def _sync_internal_layers_multi(
    project_id: str,
    run_id: int,
    output_root: Path,
    succeeded: list[PartitionRunOutcome],
    log_mgr,
) -> None:
    """Internal-layer sync across sub-project docs trees.

    ``RagBridge.sync_internal_layers`` deletes the project's internal set
    wholesale before re-pushing one docs tree; calling it once per partition
    would wipe each previous partition's layers. This variant deletes once and
    pushes every succeeded sub-project's internal artifacts with a
    ``{subproject}/`` filename prefix, then replaces the merged entity aliases.
    """
    bridge = RagBridge(log_manager=log_mgr)
    try:
        await bridge.delete_internal_documents(project_id)
    except Exception as exc:  # noqa: BLE001
        await log_mgr.emit(
            project_id,
            f"Internal-layer delete failed -- skipping internal sync to avoid "
            f"mixing stale and new layers: {exc}",
            level="error",
        )
        return

    pushed = 0
    errors: list[str] = []
    merged_aliases: list[dict] = []
    for outcome in succeeded:
        docs_dir = output_root / outcome.subdir / "docs"
        try:
            artifacts = collect_internal_artifacts(
                docs_dir, project_id=project_id, run_id=outcome.run_id or run_id
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{outcome.partition}: collect failed ({exc})")
            continue
        for artifact in artifacts:
            metadata = dict(artifact.get("metadata") or {})
            metadata["subproject"] = outcome.partition
            try:
                await bridge.push_markdown(
                    project_id=project_id,
                    filename=f"{outcome.subdir}/{artifact['filename']}",
                    content=artifact["content"],
                    metadata=metadata,
                )
                pushed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{outcome.partition}/{artifact['filename']}: {exc}")
        try:
            merged_aliases.extend(build_entity_aliases(docs_dir))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{outcome.partition}: alias build failed ({exc})")

    if merged_aliases:
        try:
            await bridge.post_entity_aliases(project_id, merged_aliases)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"entity aliases: {exc}")

    await log_mgr.emit(
        project_id,
        f"Internal RAG sync (composite): {pushed} docs, {len(merged_aliases)} aliases, "
        f"{len(errors)} errors",
        level="error" if errors else "info",
    )


def _aggregate_totals(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for key in ("cost_usd", "duration_ms", "input_tokens", "output_tokens"):
        values = [r.get(key) for r in results if isinstance(r.get(key), (int, float))]
        totals[key] = sum(values) if values else None
    for key in ("model_name", "write_model_name"):
        names = [str(r.get(key)) for r in results if r.get(key)]
        totals[key] = names[0] if names else None
    return totals
