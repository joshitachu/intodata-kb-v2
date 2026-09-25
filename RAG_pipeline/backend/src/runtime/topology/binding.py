"""Resolve the provider-declared static reference into explicit job mappings.

Why this exists
---------------
``runtime_job_scan_maps`` is the only link between a runtime task and the design
that was scanned, and ``RuntimeCorrelationService.correlate_execution`` returns
``unmatched`` with no impacts at all when the row is missing. The table has zero
rows in production, so the whole impact engine has been unreachable: the only
way to create a row was for a human to POST one per task, by hand, for hundreds
of tasks per estate.

This module closes that gap without inventing a link that the evidence does not
support. It binds a runtime job to a static design only when the provider itself
DECLARES which of its fields names the design
(``RuntimeSourceProvider.static_ref_strategy``) and that field's value equals a
scanned job's name or id EXACTLY.

Three rules, each of which is a refusal rather than a heuristic:

* **Exact only.** There is no prefix, casefold, strip or similarity tier, here
  or anywhere downstream. Measured on the Pluripharm estate, a prefix tier
  produced 25 wrong bindings, every one of them onto a job that genuinely
  exists: ``DWH_Facts_001`` -> ``DWH_Facts_Master`` (a different job, currently
  failing), ``Daily_Queries_6_uur_old`` -> the live ``Daily_Queries_6_uur``,
  ``WMS_IE_stock_mirror_copy`` -> ``WMS_IE_stock_mirror``. A wrong binding is
  worse than none: it attributes one design's failures to another and does so
  invisibly.
* **Ambiguity is never guessed.** One runtime value resolving to more than one
  static job is skipped and counted, not resolved to the first, the newest or
  the closest.
* **Manual wins.** A human who mapped a renamed task by hand made a statement
  this resolver cannot check; it is never overwritten. Many runtime tasks
  mapping to ONE static design is correct and expected (a design is commonly
  deployed several times), which is why uniqueness lives on the runtime side.

"Nothing to do" is a first-class outcome, reported with its reason rather than
swallowed: a provider may legitimately expose no static reference at all
(Elasticsearch does not), and a project may have no published scan snapshot yet.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import RuntimeJobRow
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime.providers.base import RuntimeSourceProvider, StaticRefStrategy
from src.runtime.topology.models import static_ref_resolved_by
from src.runtime.topology.repository import RuntimeTopologyRepository

logger = logging.getLogger(__name__)

#: The provider exposes no field naming a scanned design, so there is nothing to
#: resolve from. Not a failure: it is the correct answer for a log-only source.
REASON_NO_STATIC_REF = "provider declares no static reference"

#: No scan has been published for this project, so there is nothing to bind TO.
REASON_NO_SNAPSHOT = "no static topology snapshot for this project"

#: Bounded so a pathological estate cannot turn a diagnostic into a payload.
_MAX_REPORTED_AMBIGUOUS = 10


@dataclass(frozen=True)
class StaticRefBindingResult:
    """What one resolver pass did, and what it refused to do.

    ``bound`` counts runtime jobs that ARE mapped by this resolver after the
    pass, including the ones that were already mapped correctly -- so a second
    run of unchanged inputs reports the same ``bound`` with ``written == 0``.
    """

    project_id: str
    source_key: str
    source_field: str | None = None
    scan_revision: str | None = None
    bound: int = 0
    already_manual: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    #: Rows actually inserted or changed; 0 on an idempotent re-run.
    written: int = 0
    #: Why nothing could be attempted, or None when the pass ran.
    reason: str | None = None
    #: A bounded sample of the runtime values that resolved to several designs.
    ambiguous_values: tuple[str, ...] = field(default_factory=tuple)

    @property
    def attempted(self) -> bool:
        return self.reason is None


def _static_job_index(payload: dict | None) -> dict[str, set[str]]:
    """Exact lookup from a scanned job's name AND id to its static job id.

    Both keys, because which one a provider's static reference corresponds to is
    a per-platform fact: a Talend artifact name is the design NAME, while a
    generic scan may well publish an id the runtime also uses. Indexing both and
    refusing collisions is honest; picking one and hoping is not.

    Values are sets, so a name that belongs to two different designs is visible
    as an ambiguity instead of silently resolving to whichever was scanned last.
    """
    index: dict[str, set[str]] = {}
    for job in (payload or {}).get("jobs", []):
        if not isinstance(job, dict):
            continue
        static_job_id = job.get("static_job_id")
        if not isinstance(static_job_id, str) or not static_job_id:
            continue
        for key in (job.get("name"), static_job_id):
            if isinstance(key, str) and key:
                index.setdefault(key, set()).add(static_job_id)
    return index


async def resolve_static_refs_for_source(
    session: AsyncSession,
    *,
    provider: RuntimeSourceProvider,
    project_id: str,
    source_key: str,
    now: datetime.datetime | None = None,
) -> StaticRefBindingResult:
    """Bind this project's runtime jobs for one source to scanned designs.

    Reads the provider's declared strategy, the project's latest published
    topology snapshot and the stored ``runtime_jobs`` rows; writes only
    ``runtime_job_scan_maps``. Does not commit -- the caller owns the
    transaction, exactly as the rest of the sync cycle does.
    """
    strategy: StaticRefStrategy | None = provider.static_ref_strategy()
    if strategy is None:
        return StaticRefBindingResult(
            project_id=project_id, source_key=source_key, reason=REASON_NO_STATIC_REF
        )

    repository = RuntimeTopologyRepository(session)
    snapshot = await repository.latest_snapshot(project_id)
    if snapshot is None:
        return StaticRefBindingResult(
            project_id=project_id,
            source_key=source_key,
            source_field=strategy.source_field,
            reason=REASON_NO_SNAPSHOT,
        )

    index = _static_job_index(snapshot.payload)
    existing_rows = await repository.list_job_scan_map_rows(project_id, source_key)
    existing_by_job: dict[str, RuntimeJobScanMapRow] = {
        row.runtime_job_id: row for row in existing_rows
    }
    resolved_by = static_ref_resolved_by(strategy.source_field)
    written_at = now or datetime.datetime.now(datetime.UTC)

    runtime_jobs = (
        await session.execute(
            select(RuntimeJobRow).where(
                RuntimeJobRow.project_id == project_id,
                RuntimeJobRow.source_key == source_key,
            )
        )
    ).scalars().all()

    bound = already_manual = ambiguous = unmatched = written = 0
    ambiguous_values: list[str] = []
    for job in runtime_jobs:
        # The provider named this field; nothing here knows what it means, and
        # that is the point -- `artifact_name` exists only on TMC rows.
        value = getattr(job, strategy.source_field, None)
        # Compared as it was stored. No strip, no casefold: a value that differs
        # by so much as a space is a different string, and treating it as equal
        # is the same class of mistake as a prefix match.
        candidates = index.get(value) if isinstance(value, str) and value else None
        if not candidates:
            unmatched += 1
            continue
        if len(candidates) > 1:
            ambiguous += 1
            if len(ambiguous_values) < _MAX_REPORTED_AMBIGUOUS:
                ambiguous_values.append(value)
            continue
        outcome = await repository.record_resolved_job_scan_map(
            project_id=project_id,
            source_key=source_key,
            runtime_job_id=job.job_id,
            static_job_id=next(iter(candidates)),
            resolved_by=resolved_by,
            existing=existing_by_job.get(job.job_id),
            created_at=written_at,
        )
        if outcome == "manual":
            already_manual += 1
            continue
        bound += 1
        if outcome != "unchanged":
            written += 1

    return StaticRefBindingResult(
        project_id=project_id,
        source_key=source_key,
        source_field=strategy.source_field,
        scan_revision=snapshot.scan_revision,
        bound=bound,
        already_manual=already_manual,
        ambiguous=ambiguous,
        unmatched=unmatched,
        written=written,
        ambiguous_values=tuple(ambiguous_values),
    )
