"""Extended generate run endpoints for template snapshot and comparison."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from mcp_server.api.permissions import require_project_access
from mcp_server.config import config
from mcp_server.db.models import GenerateRun
from mcp_server.db.session import async_session_factory
from mcp_server.schemas import RunConnectorEvidenceResponse, RunRuntimeEvidenceSummary
from mcp_server.services.project_registry import ProjectRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generate-runs"])

#: Project ids and partition names reach the filesystem. Anything outside this
#: alphabet is refused rather than normalised -- a traversal attempt is not a
#: naming mistake. `.` and `..` match the alphabet, so every use is paired with
#: a containment check on the resolved path.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_SAFE_PROJECT_ID = _SAFE_PATH_SEGMENT

#: The coverage artifact's name and field set, taken from the producer.
#:
#: Build-plan A12: the writer and this reader disagreed (`available=true`
#: beside `mode="unavailable"`) because the schema existed only as prose in a
#: plan document. `RuntimeEvidenceStore.coverage_payload()` is now the single
#: definition and both sides read it from there -- this module derives the field
#: names from the producer's own empty payload rather than restating them.
#:
#: The fallback is not decoration: this route must answer 200 even if the
#: producer module is mid-change or its constructor signature moves. It is the
#: field list as of schema_version 1, and the tripwire test in
#: `test_run_connector_evidence.py` fails if the two ever drift.
#:
#: FOLLOW-UP (A12 wire-up): once `runtime_evidence.py` settles, this can become
#: a plain unconditional import of an exported constant if MCP-CORE adds one.
_FALLBACK_COVERAGE_FILENAME = "runtime_evidence_coverage.json"
_FALLBACK_COVERAGE_FIELDS = frozenset(
    {
        "schema_version",
        "mode",
        "window_days",
        "units_requested",
        "units_matched",
        "matched",
        "unmatched",
        "unretrieved",
        "orphan_runtime_jobs",
        "orphan_ratio",
        "match_ratio",
        "partial",
        "scope_mismatch",
        "batches",
        "failed_batches",
        "source_key",
        "resolved_by_alias",
        "alias_conflicts",
        "redaction_flag_count",
    }
)
try:
    from mcp_server.providers.custom.runtime_evidence import (
        RUNTIME_COVERAGE_FILENAME,
        RuntimeEvidenceStore,
    )

    COVERAGE_FIELDS = frozenset(RuntimeEvidenceStore(window_days=0).coverage_payload())
except Exception:  # a status route must not die with its producer
    logger.warning(
        "Falling back to the local coverage schema: the shared definition in "
        "providers/custom/runtime_evidence.py could not be read",
        exc_info=True,
    )
    RUNTIME_COVERAGE_FILENAME = _FALLBACK_COVERAGE_FILENAME
    COVERAGE_FIELDS = _FALLBACK_COVERAGE_FIELDS


def _load_run_metrics(project_id: str, run_id: int) -> dict | None:
    metrics_path = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
    if not metrics_path.exists():
        return None
    try:
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _metric_delta(run_a: dict, run_b: dict, key: str) -> float | int | None:
    va = run_a.get(key)
    vb = run_b.get(key)
    if va is None or vb is None:
        return None
    try:
        return round(vb - va, 6)
    except TypeError:
        return None


def _pct_delta(run_a: dict, run_b: dict, key: str) -> float | None:
    va = run_a.get(key)
    delta = _metric_delta(run_a, run_b, key)
    if va in (None, 0) or delta is None:
        return None
    try:
        return round((delta / va) * 100, 2)
    except TypeError:
        return None


def _quality_overall(metrics: dict | None, fallback: float | None) -> float | None:
    quality = (metrics or {}).get("doc_quality") or {}
    return quality.get("overall", fallback)


def _quality_details(metrics: dict | None) -> dict | None:
    if not metrics:
        return None
    return metrics.get("doc_quality")


def _run_summary(run, metrics: dict | None) -> dict:
    analysis_model = (metrics or {}).get("analysis_model") or getattr(run, "model_name", None)
    write_model = (metrics or {}).get("write_model") or getattr(run, "write_model_name", None)
    quality = _quality_overall(metrics, getattr(run, "quality_score", None))
    return {
        "id": run.id,
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "doc_count": run.doc_count,
        "provider": (metrics or {}).get("provider"),
        "analysis_model": analysis_model,
        "model_name": analysis_model,
        "write_model": write_model,
        "write_model_name": write_model,
        "quality": quality,
        "quality_score": quality,
        "quality_pass": quality is not None and quality >= 4.0,
        "doc_quality": _quality_details(metrics),
        "cost_usd": (metrics or {}).get("cost_usd", run.cost_usd),
        "duration_ms": (metrics or {}).get("duration_ms", run.duration_ms),
        "input_tokens": (metrics or {}).get("input_tokens", run.input_tokens),
        "output_tokens": (metrics or {}).get("output_tokens", run.output_tokens),
        "num_turns": (metrics or {}).get("num_turns", run.num_turns),
        "metrics_found": metrics is not None,
        "auto_verdict": getattr(run, "auto_verdict", None),
        "auto_baseline_run_id": getattr(run, "auto_baseline_run_id", None),
    }


async def build_run_comparison(
    project_id: str,
    run_a_id: int,
    run_b_id: int,
    *,
    quality_drop_pct: float = 5.0,
):
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run_map = {r.id: r for r in runs}

        run_a = run_map.get(run_a_id)
        run_b = run_map.get(run_b_id)
        if not run_a or not run_b:
            raise HTTPException(404, "One or both runs not found")

        metrics_a = _load_run_metrics(project_id, run_a_id)
        metrics_b = _load_run_metrics(project_id, run_b_id)
        summary_a = _run_summary(run_a, metrics_a)
        summary_b = _run_summary(run_b, metrics_b)

    delta = {
        key: _metric_delta(summary_a, summary_b, key)
        for key in ("doc_count", "cost_usd", "duration_ms", "input_tokens", "output_tokens", "num_turns", "quality")
    }
    delta_pct = {
        key: _pct_delta(summary_a, summary_b, key)
        for key in ("cost_usd", "duration_ms", "input_tokens", "output_tokens", "num_turns", "quality")
    }

    checks: list[dict] = []
    qa = summary_a.get("quality")
    qb = summary_b.get("quality")
    if qa is not None and qb is not None:
        quality_change_pct = _pct_delta(summary_a, summary_b, "quality")
        quality_drop = -(quality_change_pct or 0.0)
        checks.append({
            "metric": "quality",
            "run_a": qa,
            "run_b": qb,
            "delta": delta["quality"],
            "pct_change": quality_change_pct,
            "threshold": quality_drop_pct,
            "result": "FAIL" if quality_drop > quality_drop_pct else "PASS",
        })
    if metrics_a is None or metrics_b is None:
        checks.append({
            "metric": "metrics",
            "run_a": metrics_a is not None,
            "run_b": metrics_b is not None,
            "result": "WARN",
        })

    verdict = "FAIL" if any(c["result"] == "FAIL" for c in checks) else "PASS"

    return {
        "project_id": project_id,
        "run_a": summary_a,
        "run_b": summary_b,
        "delta": delta,
        "delta_pct": delta_pct,
        "quality_delta": delta["quality"],
        "quality_delta_pct": delta_pct["quality"],
        "verdict": verdict,
        "checks": checks,
    }


@router.get("/generate-runs/{project_id}/compare")
async def compare_runs(project_id: str, a: int, b: int):
    """Compare two generation runs: profiles, templates, and output metrics."""
    return await build_run_comparison(project_id, a, b)


def _coerce_int(value: object, default: int = 0) -> int:
    """Best-effort int from an artifact field written by another package."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return default


def _count(value: object) -> int:
    """Length of a list field, tolerating an already-summarised integer."""
    if isinstance(value, (list, tuple)):
        return len(value)
    return _coerce_int(value)


def _runtime_coverage_path(project_id: str, subproject: str | None = None) -> Path | None:
    """Resolved path to a published coverage artifact, or None.

    Composite (partitioned) uploads publish to
    ``output/{project_id}/{subproject}/docs``; every other run publishes to
    ``output/{project_id}/docs``. ``subproject`` selects between the two.

    Returns None when either segment is not a safe filesystem component or when
    the resolved path escapes the project's own output directory. The project
    id comes from a database row and the sub-project from an operator-supplied
    zip name, so both are treated as untrusted.
    """
    if not _SAFE_PATH_SEGMENT.match(project_id):
        return None
    output_root = (config.project_root / "output").resolve()
    project_dir = (output_root / project_id).resolve()
    if project_dir != output_root and output_root not in project_dir.parents:
        return None
    docs_root = project_dir
    if subproject is not None:
        if not _SAFE_PATH_SEGMENT.match(subproject):
            return None
        docs_root = (project_dir / subproject).resolve()
        if project_dir not in docs_root.parents:
            return None
    return docs_root / "docs" / "_assessment" / RUNTIME_COVERAGE_FILENAME


def _partition_subdir(partition_name: str) -> str | None:
    """Output sub-directory the orchestrator published this partition under.

    ``None`` means "the project's own ``docs/``" -- the whole-project partition
    keeps the single-run layout.
    """
    try:
        from mcp_server.providers.custom.partition_planner import partition_output_subdir
    except ImportError:  # pragma: no cover - planner is part of the same package
        return partition_name
    return partition_output_subdir(partition_name)


def _read_coverage_payload(path: Path | None) -> dict | None:
    """The artifact as a dict, or None for absent/unreadable/malformed.

    Runtime absence is never an error (build plan R17), so every failure mode
    collapses to None rather than raising.
    """
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Unreadable JSON artifact at %s", path)
        return None
    return payload if isinstance(payload, dict) else None


def _infer_mode(payload: dict) -> str:
    """The artifact's mode, inferred when the writer did not record one.

    Build-plan A12: the shipped writer emits five of the ten contract fields and
    ``mode`` is not one of them, which made a perfectly good artifact report the
    self-contradictory ``available=true, mode="unavailable"``. A payload that is
    present, parseable and carries any coverage field is evidence the lane ran,
    so it reports ``data``; only a payload that says otherwise -- or one with no
    coverage field at all -- reports ``unavailable``.
    """
    raw = payload.get("mode")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if any(field in payload for field in COVERAGE_FIELDS):
        return "data"
    return "unavailable"


def _orphan_names(payload: dict) -> set[str] | None:
    """Orphan runtime job names, or None when this payload cannot report them.

    None (missing key, or a pre-summarised count) excludes the payload from the
    cross-sub-project intersection rather than poisoning it with an empty set --
    the same rule build-plan A2 applies to a failed fetch batch.
    """
    value = payload.get("orphan_runtime_jobs")
    if isinstance(value, (list, tuple)):
        return {str(item) for item in value}
    return None


def _summarise_coverage(payload: dict, *, subproject: str | None) -> RunRuntimeEvidenceSummary:
    """One artifact rendered as the counts-only response block."""
    matched = _count(payload.get("matched"))
    unmatched = _count(payload.get("unmatched"))
    source_key = payload.get("source_key")
    return RunRuntimeEvidenceSummary(
        subproject=subproject,
        available=True,
        mode=_infer_mode(payload),
        window_days=_coerce_int(payload.get("window_days")) or None,
        # Prefer the writer's own counters; fall back to the arrays it also
        # writes, so a partially-populated older artifact still reads sensibly.
        units_requested=_coerce_int(payload.get("units_requested")) or (matched + unmatched),
        units_matched=_coerce_int(payload.get("units_matched")) or matched,
        orphan_runtime_job_count=_count(payload.get("orphan_runtime_jobs")),
        # `failed_batches` and `partial` are the same fact from two sides; a
        # writer that reports one without the other still reads as partial.
        partial=bool(payload.get("partial", False)) or _coerce_int(payload.get("failed_batches")) > 0,
        scope_mismatch=bool(payload.get("scope_mismatch", False)),
        batches=_coerce_int(payload.get("batches")) or None,
        source_key=source_key if isinstance(source_key, str) and source_key else None,
    )


def _subproject_subdirs(project_id: str) -> list[str]:
    """Published sub-project directories of a composite upload, sorted.

    Two sources, unioned, because either can be stale on its own: the
    orchestrator's `topology/partition_state.json` (authoritative for what it
    ran, but written only on success) and the directories that actually hold a
    coverage artifact (authoritative for what is on disk). Empty means the
    project is not partitioned and keeps the single-run layout.
    """
    project_dir = config.project_root / "output" / project_id
    found: set[str] = set()

    state_path = project_dir / "topology" / "partition_state.json"
    state = _read_coverage_payload(state_path) or {}
    for name in state:
        subdir = _partition_subdir(str(name))
        if subdir and _SAFE_PATH_SEGMENT.match(subdir):
            found.add(subdir)

    try:
        for child in project_dir.iterdir():
            if not child.is_dir() or not _SAFE_PATH_SEGMENT.match(child.name):
                continue
            if (child / "docs" / "_assessment" / RUNTIME_COVERAGE_FILENAME).is_file():
                found.add(child.name)
    except OSError:
        pass
    return sorted(found)


def _aggregate_coverage(
    summaries: list[RunRuntimeEvidenceSummary],
    payloads: list[dict],
) -> RunRuntimeEvidenceSummary:
    """Roll several sub-project artifacts into one project-level summary.

    Counts sum, except orphans. Every sub-project of one upload is bound to the
    same runtime source, so each artifact's orphan list is drawn from the same
    runtime vocabulary and summing would count the same job once per
    sub-project. Build-plan A2's merge rule applies unchanged: intersect the
    name sets, and exclude any artifact that cannot report names.
    """
    present = [s for s in summaries if s.available]
    if not present:
        return RunRuntimeEvidenceSummary()

    orphan_sets = [names for names in (_orphan_names(p) for p in payloads) if names is not None]
    if orphan_sets:
        orphan_count = len(set.intersection(*orphan_sets))
    else:
        # Nobody reported names. The smallest reported count is the only sound
        # lower bound for an intersection.
        orphan_count = min((s.orphan_runtime_job_count for s in present), default=0)

    modes = {s.mode for s in present}
    if "data" in modes:
        mode = "data"
    elif modes == {"opted_out"}:
        mode = "opted_out"
    elif len(modes) == 1:
        mode = next(iter(modes))
    else:
        mode = "unavailable"

    windows = {s.window_days for s in present if s.window_days}
    source_keys = {s.source_key for s in present if s.source_key}
    batches = [s.batches for s in present if s.batches]
    return RunRuntimeEvidenceSummary(
        subproject=None,
        available=True,
        mode=mode,
        window_days=next(iter(windows)) if len(windows) == 1 else (max(windows) if windows else None),
        units_requested=sum(s.units_requested for s in present),
        units_matched=sum(s.units_matched for s in present),
        orphan_runtime_job_count=orphan_count,
        # A sub-project whose artifact is missing makes the project-level figure
        # partial even when every artifact that WAS found is complete.
        partial=any(s.partial for s in present) or len(present) != len(summaries),
        scope_mismatch=any(s.scope_mismatch for s in present),
        batches=sum(batches) if batches else None,
        source_key=next(iter(source_keys)) if len(source_keys) == 1 else None,
        subproject_count=len(summaries),
    )


def _load_runtime_coverage(
    project_id: str, partition_name: str | None = None
) -> tuple[RunRuntimeEvidenceSummary, list[RunRuntimeEvidenceSummary]]:
    """Coverage for one run: the headline summary plus any per-sub-project rows.

    Three layouts, all reachable:

    * a composite child run (`GenerateRun.partition_name` set) reads only its
      own sub-project's artifact and says which one it read;
    * a top-level run on a partitioned project AGGREGATES every sub-project --
      the headline is the whole upload, and each contributing sub-project is
      listed beside it so the total can be read back apart. Returning the first
      artifact found would silently describe one zip as if it were the upload;
    * anything else reads `output/{project_id}/docs`, unchanged.

    Total by construction: an absent artifact (every run before it existed), an
    unreadable one, malformed JSON, or a payload missing any field all degrade
    to `available=False, mode="unavailable"`. This never raises.
    """
    if partition_name:
        subdir = _partition_subdir(partition_name)
        payload = _read_coverage_payload(_runtime_coverage_path(project_id, subdir))
        if payload is None:
            return RunRuntimeEvidenceSummary(subproject=subdir), []
        return _summarise_coverage(payload, subproject=subdir), []

    subdirs = _subproject_subdirs(project_id)
    if subdirs:
        summaries: list[RunRuntimeEvidenceSummary] = []
        payloads: list[dict] = []
        for subdir in subdirs:
            payload = _read_coverage_payload(_runtime_coverage_path(project_id, subdir))
            if payload is None:
                summaries.append(RunRuntimeEvidenceSummary(subproject=subdir))
                continue
            summaries.append(_summarise_coverage(payload, subproject=subdir))
            payloads.append(payload)
        return _aggregate_coverage(summaries, payloads), summaries

    payload = _read_coverage_payload(_runtime_coverage_path(project_id))
    if payload is None:
        return RunRuntimeEvidenceSummary(), []
    return _summarise_coverage(payload, subproject=None), []


@router.get("/generate-runs/{run_id}/connector-evidence", response_model=RunConnectorEvidenceResponse)
async def run_connector_evidence(run_id: int, request: Request):
    """Evidence status for one generation run.

    Read-only. The `runtime` block is read from the project's *published*
    coverage artifact, which describes the latest published documents rather
    than this historical run -- the client states that plainly, and no per-run
    archival exists yet (deferred, build plan section 8).

    A composite child run reads its own sub-project; a top-level run on a
    composite upload aggregates every sub-project and lists them in
    `runtime_subprojects`.
    """
    async with async_session_factory() as db:
        run = await ProjectRegistry(db).get_generate_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found")

    await require_project_access(request, run.project_id)

    runtime, subprojects = _load_runtime_coverage(
        run.project_id, getattr(run, "partition_name", None)
    )
    return RunConnectorEvidenceResponse(
        run_id=run_id,
        runtime=runtime,
        runtime_subprojects=subprojects,
    )


class RerunRequest(BaseModel):
    phase: Literal["scorecard", "doc"]
    target: str | None = None  # required when phase == "doc"


@router.post("/generate-runs/{project_id}/{run_id}/rerun")
async def rerun_phase(
    project_id: str,
    run_id: int,
    req: RerunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Rerun a single harness phase (scorecard or one doc) without rerunning the full pipeline.

    Prerequisites:
    - Parent run must be completed or failed (not running).
    - Phase 2 analysis artifacts must exist in the staging dir.
    - For doc reruns, target must be the doc stem (e.g. '02_COMPONENT_INVENTORY').
    """
    if req.phase == "doc" and not req.target:
        raise HTTPException(status_code=422, detail="target is required when phase is 'doc'")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)

        # Verify parent run exists and belongs to this project
        parent_run = await registry.get_generate_run(run_id)
        if parent_run is None or parent_run.project_id != project_id:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found for project {project_id}")
        if parent_run.status == "running":
            raise HTTPException(status_code=409, detail="Parent run is still running; wait for it to complete")

        # Verify staging prerequisites
        staging_dir = config.project_root / "output" / project_id / "docs_staging"
        analysis_dir = staging_dir / "_analysis"
        if not staging_dir.exists() or not any(analysis_dir.glob("*.md")):
            raise HTTPException(
                status_code=409,
                detail="No Phase 2 analysis artifacts found in staging; run full generation first",
            )
        if req.phase == "doc":
            target_stem = req.target.removesuffix(".md")
            if not (staging_dir / f"{target_stem}.md").exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Doc {target_stem}.md does not exist in staging; run full generation first",
                )

        # Create child run record
        project = await registry.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

        new_run = await registry.create_generate_run(
            project_id=project_id,
            triggered_by="api",
            model_name=parent_run.model_name,
        )
        new_run_id = new_run.id
        # Record parent link
        from sqlalchemy import update
        await db.execute(
            update(GenerateRun).where(GenerateRun.id == new_run_id).values(parent_run_id=run_id)
        )
        await db.commit()

    log_mgr = request.app.state.log_manager

    background_tasks.add_task(
        _run_phase_rerun_background,
        project_id=project_id,
        run_id=new_run_id,
        parent_run_id=run_id,
        phase=req.phase,
        target=req.target,
        log_mgr=log_mgr,
    )

    return {
        "run_id": new_run_id,
        "parent_run_id": run_id,
        "phase": req.phase,
        "target": req.target,
        "status": "running",
    }


async def _run_phase_rerun_background(
    *,
    project_id: str,
    run_id: int,
    parent_run_id: int,
    phase: str,
    target: str | None,
    log_mgr,
) -> None:
    """Background task: invalidate the target artifact and resume the full pipeline.

    Resume mode skips all phases that have accepted artifacts in staging, so only
    the invalidated artifact will be regenerated.  This is correct and safe because:
    - Phase 1/2 artifacts are never deleted — they are preserved.
    - Only the specific scorecard.md or doc.md is deleted before resuming.
    - The resumed run inherits the same staging dir as the parent run.
    """
    import datetime
    import time

    from sqlalchemy import update as sa_update
    from mcp_server.db.models import GenerateRun
    from mcp_server.db.session import async_session_factory
    from mcp_server.api.routes.projects import _run_generate_background

    start_ms = int(time.time() * 1000)
    staging_dir = config.project_root / "output" / project_id / "docs_staging"

    async def _log(msg: str, level: str = "info") -> None:
        await log_mgr.emit(project_id, msg, level=level)

    try:
        await _log(f"[rerun] Starting phase rerun: phase={phase} target={target}")

        # Invalidate the target artifact so the resumed run regenerates it.
        if phase == "scorecard":
            scorecard = staging_dir / "_assessment" / "scorecard.md"
            scorecard.unlink(missing_ok=True)
            # Also remove the agent task checkpoint so resume doesn't skip it.
            scorecard_task = staging_dir / "_agent_tasks" / "scorecard" / "scorecard" / "status.json"
            scorecard_task.unlink(missing_ok=True)
        elif phase == "doc" and target:
            target_stem = target.removesuffix(".md")
            (staging_dir / f"{target_stem}.md").unlink(missing_ok=True)
            # Remove the agent task checkpoint for this doc.
            doc_task_status = staging_dir / "_agent_tasks" / "phase3_doc" / target_stem / "status.json"
            doc_task_status.unlink(missing_ok=True)

        await _log(f"[rerun] Artifact invalidated; resuming pipeline with resume=True")

        # Run the full pipeline with resume=True — it will skip completed phases and
        # only regenerate the invalidated artifact.
        await _run_generate_background(
            project_id,
            run_id,
            log_mgr,
            resume=True,
        )

    except Exception as exc:
        logger.exception("Phase rerun failed for %s/%s phase=%s", project_id, run_id, phase)
        await _log(f"[rerun] Phase rerun failed: {exc}", level="error")
        async with async_session_factory() as db:
            await db.execute(
                sa_update(GenerateRun)
                .where(GenerateRun.id == run_id)
                .values(
                    status="failed",
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    error_message=str(exc)[:500],
                )
            )
            await db.commit()
