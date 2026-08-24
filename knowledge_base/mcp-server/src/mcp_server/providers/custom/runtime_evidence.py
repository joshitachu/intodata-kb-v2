"""Runtime execution evidence for Phase 3 documents.

A scan says what a job is written to do. This module says what it actually did:
how often it ran, in which environments, how long it took, and how it failed.
The facts come from the RAG backend's runtime mirror (TMC today) through one
bounded read endpoint, and are rendered deterministically -- no LLM tool calls,
no agentic retrieval -- exactly like the scan-fact bundle they are appended to.

Two deliberate constraints:

* **Aggregates only.** The endpoint returns counts, percentiles and redacted
  failure headlines; raw log lines never cross the boundary. A document needs to
  say how a job behaves, not replay its output, and the narrow payload keeps the
  redaction surface to one field.
* **Absence is stated, never implied.** Units with no runtime data are recorded
  so a document can say "no runtime data was available" instead of leaving a
  reader to infer from silence that a job never runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from mcp_server.config import config

logger = logging.getLogger(__name__)

# Talend serializes DI Jobs and ESB Routes into the same item format, and a
# Route is a long-running listener rather than a batch job: werkorder's
# `Maximo2Quintiq_Workorder_Route` legitimately ran for 76 days in one
# execution. Reporting that under "run duration" reads as a defect, so routes
# get their own wording. Classification is scanner-computed (`is_esb_route`),
# never inferred here from a name suffix.
_ROUTE_DURATION_LABEL = "Uptime per start"
_JOB_DURATION_LABEL = "Run duration"


@dataclass(frozen=True)
class RuntimeUnitEvidence:
    """One unit's runtime profile, as returned by the backend."""

    unit_name: str
    job_name: str
    source_key: str
    resolved_by: str
    environments: tuple[str, ...]
    total_runs: int
    success_runs: int
    failed_runs: int
    unfinished_runs: int
    failure_rate: float
    current_failure_streak: int
    first_run_at: str | None
    last_run_at: str | None
    runs_per_day: float | None
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    max_duration_ms: int | None
    plans: tuple[str, ...]
    # (failed_at, error_summary, occurrences) -- distinct failure MODES, so a
    # count of 700 is one entry rather than 700 near-identical lines.
    recent_failures: tuple[tuple[str, str, int], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RuntimeUnitEvidence":
        failures = tuple(
            (
                str(item.get("failed_at") or ""),
                str(item.get("error_summary") or ""),
                int(item.get("occurrences") or 1),
            )
            for item in payload.get("recent_failures") or []
        )
        return cls(
            unit_name=str(payload.get("unit_name") or ""),
            job_name=str(payload.get("job_name") or ""),
            source_key=str(payload.get("source_key") or ""),
            resolved_by=str(payload.get("resolved_by") or ""),
            environments=tuple(payload.get("environments") or ()),
            total_runs=int(payload.get("total_runs") or 0),
            success_runs=int(payload.get("success_runs") or 0),
            failed_runs=int(payload.get("failed_runs") or 0),
            unfinished_runs=int(payload.get("unfinished_runs") or 0),
            failure_rate=float(payload.get("failure_rate") or 0.0),
            current_failure_streak=int(payload.get("current_failure_streak") or 0),
            first_run_at=payload.get("first_run_at"),
            last_run_at=payload.get("last_run_at"),
            runs_per_day=payload.get("runs_per_day"),
            p50_duration_ms=payload.get("p50_duration_ms"),
            p95_duration_ms=payload.get("p95_duration_ms"),
            max_duration_ms=payload.get("max_duration_ms"),
            plans=tuple(payload.get("plans") or ()),
            recent_failures=failures,
        )


@dataclass(frozen=True)
class RuntimeEvidenceStore:
    """Runtime evidence for a whole project, fetched once per run."""

    window_days: int
    by_unit: Mapping[str, RuntimeUnitEvidence] = field(default_factory=dict)
    unmatched_units: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.by_unit)

    def section_for(
        self, unit_name: str, *, is_esb_route: bool = False, max_bytes: int
    ) -> str:
        """Render one unit's runtime section, or "" when there is nothing to say.

        An unmatched unit renders an explicit no-data notice rather than
        nothing: a writer that sees no runtime section at all cannot tell the
        difference between "this job never runs" and "we did not look".
        """
        evidence = self.by_unit.get(unit_name)
        if evidence is None:
            if unit_name in self.unmatched_units:
                return _render_absent(unit_name, self.window_days)
            return ""
        rendered = _render(evidence, self.window_days, is_esb_route=is_esb_route)
        if len(rendered.encode("utf-8")) > max_bytes:
            rendered = _render(
                evidence, self.window_days, is_esb_route=is_esb_route, include_failures=False
            )
        if len(rendered.encode("utf-8")) > max_bytes:
            return ""
        return rendered


def _ms(value: int | None) -> str:
    """Human-scaled duration. ASCII only (Windows cp1252 output constraint)."""
    if value is None:
        return "n/a"
    seconds = value / 1000
    if seconds < 1:
        return f"{value} ms"
    if seconds < 90:
        return f"{seconds:.1f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.1f} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def _date(value: str | None) -> str:
    return value[:10] if value else "n/a"


def _render_absent(unit_name: str, window_days: int) -> str:
    return (
        "## RUNTIME EVIDENCE\n\n"
        f"No runtime execution data was available for `{unit_name}` in the last "
        f"{window_days} days.\n\n"
        "State this explicitly if the document discusses operational behaviour. "
        "Absence of data is NOT evidence that the unit never runs -- the runtime "
        "source may not cover it.\n"
    )


def _render(
    evidence: RuntimeUnitEvidence,
    window_days: int,
    *,
    is_esb_route: bool = False,
    include_failures: bool = True,
) -> str:
    duration_label = _ROUTE_DURATION_LABEL if is_esb_route else _JOB_DURATION_LABEL
    lines = [
        "## RUNTIME EVIDENCE",
        "",
        f"Observed production behaviour of `{evidence.job_name}` over the last "
        f"{window_days} days, from runtime source `{evidence.source_key}`.",
        "These are measured facts; do not recompute, estimate or round them.",
        "",
        "| Fact | Value |",
        "| --- | --- |",
        f"| Runs observed | {evidence.total_runs} |",
        f"| Succeeded | {evidence.success_runs} |",
        f"| Failed | {evidence.failed_runs} ({evidence.failure_rate * 100:.1f}%) |",
    ]
    if evidence.unfinished_runs:
        lines.append(f"| Never completed | {evidence.unfinished_runs} |")
    if evidence.current_failure_streak:
        lines.append(
            f"| Consecutive failures (most recent) | {evidence.current_failure_streak} |"
        )
    if evidence.environments:
        lines.append(f"| Environments | {', '.join(evidence.environments)} |")
    if evidence.plans:
        lines.append(f"| Orchestration plans | {', '.join(evidence.plans)} |")
    if evidence.runs_per_day is not None:
        lines.append(f"| Observed frequency | {evidence.runs_per_day:.2f} runs/day |")
    lines.append(
        f"| {duration_label} (median) | {_ms(evidence.p50_duration_ms)} |"
    )
    lines.append(f"| {duration_label} (p95) | {_ms(evidence.p95_duration_ms)} |")
    lines.append(f"| {duration_label} (max) | {_ms(evidence.max_duration_ms)} |")
    lines.append(
        f"| Observed window | {_date(evidence.first_run_at)} to "
        f"{_date(evidence.last_run_at)} |"
    )
    lines.append("")
    if is_esb_route:
        lines.append(
            "> This unit is an ESB route (a long-running listener), so the "
            "duration figures describe how long one start stayed up, NOT how "
            "long a batch takes to process."
        )
        lines.append("")
    lines.append(
        "> Latency is measured over completed, successful runs only. Runs that "
        "failed or never completed are counted above but excluded from the "
        "duration figures."
    )
    lines.append("")
    if include_failures and evidence.recent_failures:
        lines.append("### Distinct failure modes observed")
        lines.append("")
        for failed_at, summary, occurrences in evidence.recent_failures:
            times = "once" if occurrences == 1 else f"{occurrences} times"
            lines.append(f"- Seen {times}, most recently {_date(failed_at)}: {summary}")
        lines.append("")
        lines.append(
            "> Grouped by failure mode: one entry can represent many executions. "
            "Failure text is provider-reported and redacted. Quote it as an "
            "observed symptom; do not infer a root cause it does not state."
        )
        lines.append("")
    return "\n".join(lines)


async def fetch_runtime_evidence(
    project_id: str, unit_names: Sequence[str]
) -> RuntimeEvidenceStore | None:
    """Fetch runtime evidence for a project, or None when unavailable.

    Failure is ALWAYS degradation, never a run failure: a documentation run must
    not die because the runtime mirror is down, mid-migration, or not configured
    for this deployment. The caller simply gets no runtime sections.
    """
    if not config.runtime_evidence_enabled or not unit_names:
        return None
    params: list[tuple[str, str]] = [
        ("project_id", project_id),
        ("window_days", str(config.runtime_evidence_window_days)),
        ("max_failures_per_unit", str(config.runtime_evidence_max_failures)),
    ]
    params.extend(("units", name) for name in unit_names)
    headers = {}
    if config.ingest_api_key:
        headers["Authorization"] = f"Bearer {config.ingest_api_key}"
    url = f"{config.rag_base_url.rstrip('/')}/api/v1/runtime/doc-evidence"
    try:
        async with httpx.AsyncClient(timeout=config.runtime_evidence_timeout_s) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning(
            "runtime_evidence: lookup failed for project %s; continuing without "
            "runtime facts", project_id, exc_info=True,
        )
        return None
    units = {
        item["unit_name"]: RuntimeUnitEvidence.from_payload(item)
        for item in payload.get("units") or []
        if item.get("unit_name")
    }
    store = RuntimeEvidenceStore(
        window_days=int(payload.get("window_days") or config.runtime_evidence_window_days),
        by_unit=units,
        unmatched_units=tuple(payload.get("unmatched_units") or ()),
    )
    logger.info(
        "runtime_evidence: project %s matched %d/%d units",
        project_id, len(units), len(unit_names),
    )
    return store
