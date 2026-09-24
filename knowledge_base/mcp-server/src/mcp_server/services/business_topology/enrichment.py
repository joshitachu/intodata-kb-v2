"""Deterministic enrichment of a business topology with scanner and runtime facts.

The overlay builders (``assembly``, ``deterministic``, the single-shot lane)
declare structured stream slots -- contract width, conditions, mechanism,
direction pattern, lifecycle, deployment, document references -- plus per-field
transformation detail and runtime observations, but leave them empty. This
module fills them from what the run already holds, with no model call:

* the scan cache (``scan_result.json``, ``scan/lineage.json``,
  ``scan/manifest.json``) for designs, components, edges and lineage;
* the staged TMC payload (``_transient/runtime/runtime_evidence.json``) for
  executions, failures, cadence and (when the endpoint reports it) the deployed
  version;
* the run's own top-level documents for ``document_refs``.

Contract
--------
``enrich_topology`` is pure with respect to its inputs: it deep-copies the
topology, never mutates the argument, never writes a file, never calls a model,
and never raises on missing or malformed inputs -- whatever could not be
established is recorded in ``EnrichmentReport.skipped`` with the reason.
Iteration is sorted wherever a set or mapping is walked, so the output is
byte-stable for identical inputs, and a second pass over its own output is a
no-op (idempotent).

Precedence
----------
Upstream values win, with three deliberate exceptions where the scanner or the
runtime mirror is the authority and a model or a document is not:

=====================  =========================================================
``contract_width``     REPLACED when the scan yields at least one count. Counts
                       are measured, not narrated. Left untouched otherwise.
``deployment``         REPLACED when both a deployed and a scanned version exist.
``runtime_observations``  The observation ``rt-{stream.id}`` is OWNED by this
                       module: regenerated on every pass (so a re-run reflects the
                       current payload), linked from the stream, never merged.
=====================  =========================================================

Every other field is filled only when empty/``unknown``: ``mechanism``,
``direction_pattern``, ``lifecycle``, transformation ``kind`` and per-mapping
``expression``/``category``/``reaches_target``. ``conditions`` and
``document_refs`` are unions (conditions deduplicated on ``(kind, statement)``).

Absence is not non-execution
----------------------------
``lifecycle="dormant"`` is set only when the runtime mirror KNOWS every
implementing design and reports zero runs for all of them. A design the mirror
does not know gets an ``absence`` observation that states why (a ``tRunJob``
child, an ESB route, a joblet, outside the read scope, a failed lookup), never a
lifecycle verdict.

Runtime evidence registration
-----------------------------
The validator requires every runtime observation to cite evidence of type
``observed_runtime`` that is registered in the bundle. The bundle builder only
registers Phase 1 supporting claims of that type, so the runtime aggregates this
module cites are returned in ``EnrichmentReport.runtime_evidence``. The caller
must, before validating the enriched topology:

1. ``bundle = register_runtime_evidence(bundle, report.runtime_evidence)`` --
   the in-memory extension, same pattern as ``register_doc_evidence``;
2. ``persist_runtime_evidence(bundle.root / "evidence", report.runtime_evidence)``
   -- so ``load_business_topology_evidence`` at publication time (which re-reads
   the ledger from disk and revalidates) knows the same ids. Call it even with an
   empty mapping: it removes a stale ``runtime.jsonl`` left by a previous attempt
   in a reused staging directory, which would otherwise make the loader reject
   the whole bundle.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
    lineage_evidence_id,
)
from mcp_server.services.business_topology.models import (
    BusinessDataStream,
    BusinessTopology,
    BusinessTransformation,
    ContractWidth,
    DeliveryPath,
    DeploymentAgreement,
    RuntimeMetric,
    RuntimeObservation,
    StreamCondition,
)
from mcp_server.services.business_topology.runtime_facts import (
    RUNTIME_EVIDENCE_PREFIX,
    RUNTIME_EVIDENCE_TYPE,
    RuntimeView,
    UnitRuntime,
    absence_evidence_record,
    absence_reason,
    compare_versions,
    format_rate,
    load_runtime_view,
    unit_evidence_record,
)
from mcp_server.services.business_topology.stream_facts import (
    Design,
    ScanIndex,
    column_key,
    deactivated_components,
    design_is_stopped,
    direction_signals,
    edge_conditions,
    entry_reaches_target,
    iter_sorted_entries,
    lineage_category,
    lineage_expression,
    load_scan_index,
    mechanism_chain,
    source_schema_columns,
    component_type_family,
)
from mcp_server.services.business_topology.validation import redact_secretlike

#: File the runtime evidence records are persisted to, beside the scanner
#: ``*.jsonl`` files that ``load_business_topology_evidence`` reads.
RUNTIME_EVIDENCE_FILENAME = "runtime.jsonl"

#: Fields reported in ``EnrichmentReport.filled`` (always present, possibly 0).
FILLED_FIELDS = (
    "contract_width",
    "conditions",
    "mechanism",
    "direction_pattern",
    "lifecycle",
    "deployment",
    "document_refs",
    "transformations.expression",
    "transformations.kind",
    "runtime_observations",
    "runtime_observations.absence",
)

#: Per stream and per condition kind, how many conditions are listed one by one
#: before the rest are folded into a single counted statement.
_MAX_CONDITIONS_PER_KIND = 10
_MAX_UNIT_METRICS = 10
_MAX_MECHANISM_DESIGNS = 3


@dataclass(frozen=True)
class EnrichmentSources:
    scan_dir: Path  # intodoc-output/<pid>
    runtime_payload: dict | None = None  # parsed runtime_evidence.json, or None
    docs_dir: Path | None = None  # the run's published/staged top-level *.md docs
    unit_allowlist: frozenset[str] | None = None  # partition scoping


@dataclass
class EnrichmentReport:
    filled: dict[str, int]  # field name -> number of items filled
    skipped: dict[str, str]  # field name -> reason it could not be established
    #: ``observed_runtime`` evidence records cited by the runtime observations.
    #: Must be registered (and persisted) before validation -- see module doc.
    runtime_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "filled": {key: self.filled[key] for key in sorted(self.filled)},
            "skipped": {key: self.skipped[key] for key in sorted(self.skipped)},
            "runtime_evidence_ids": sorted(self.runtime_evidence),
        }


class _Tally:
    def __init__(self) -> None:
        self.filled: Counter[str] = Counter({name: 0 for name in FILLED_FIELDS})
        self.skipped: dict[str, Counter[str]] = {}
        self.runtime_evidence: dict[str, dict[str, Any]] = {}

    def fill(self, name: str, count: int = 1) -> None:
        self.filled[name] += count

    def skip(self, name: str, reason: str) -> None:
        self.skipped.setdefault(name, Counter())[reason] += 1

    def report(self) -> EnrichmentReport:
        skipped = {
            name: "; ".join(
                f"{reason} ({count})" if count > 1 else reason
                for reason, count in sorted(reasons.items())
            )
            for name, reasons in self.skipped.items()
        }
        return EnrichmentReport(
            filled={name: int(self.filled[name]) for name in sorted(self.filled)},
            skipped=dict(sorted(skipped.items())),
            runtime_evidence={
                key: self.runtime_evidence[key] for key in sorted(self.runtime_evidence)
            },
        )


def _redact(text: str) -> str:
    return redact_secretlike(text or "").strip()


def _names(designs: Iterable[Design], limit: int = 3) -> str:
    names = sorted({design.name or design.job_id for design in designs})
    shown = ", ".join(names[:limit])
    return shown + (f" (+{len(names) - limit} more)" if len(names) > limit else "")


# --- Resolution ---------------------------------------------------------------


def _stream_artifact_ids(
    stream: BusinessDataStream, paths: list[DeliveryPath]
) -> list[str]:
    ids = set(stream.artifact_ids)
    for path in paths:
        if path.id == stream.delivery_path_id or path.stream_id == stream.id:
            for segment in path.segments:
                ids.update(segment.artifact_ids)
    return sorted(ids)


def _designs_for(artifact_ids: Iterable[str], index: ScanIndex) -> list[Design]:
    designs = {
        artifact_id: index.designs[artifact_id]
        for artifact_id in artifact_ids
        if artifact_id in index.designs
    }
    return sorted(designs.values(), key=lambda item: (item.name, item.job_id, item.artifact_id))


# --- Per-field fillers --------------------------------------------------------


def _contract_width(
    designs: list[Design], index: ScanIndex
) -> tuple[ContractWidth | None, str]:
    if not designs:
        return None, "no scanned design resolves from the stream's artifact_ids"
    entries = [(design, entry) for design in designs for entry in index.lineage_for(design)]
    mapped = {
        column_key(entry.get("source_column"))
        for _, entry in entries
        if column_key(entry.get("source_column"))
    }
    written: set[str] = set()
    destination_fields: set[str] = set()
    for design, entry in entries:
        source = column_key(entry.get("source_column"))
        if source and entry_reaches_target(design, entry) == "yes":
            written.add(source)
            target = column_key(entry.get("target_column"))
            if target:
                destination_fields.add(target)

    offered_columns: set[str] | None = set()
    offered_reason = ""
    for design in designs:
        columns, reason = source_schema_columns(design)
        if columns is None:
            offered_columns, offered_reason = None, reason
            break
        offered_columns |= columns
    if offered_columns is not None and mapped and not mapped <= offered_columns:
        offered_reason = (
            f"reading-component schemas list {len(offered_columns)} columns but do not "
            f"cover {len(mapped - offered_columns)} of the mapped columns, so the two "
            "counts do not share a basis"
        )
        offered_columns = None
    if offered_columns is not None and not offered_columns:
        offered_columns, offered_reason = None, "source row schemas not recorded by scanner"

    offered = len(offered_columns) if offered_columns is not None else None
    mapped_count = len(mapped) if mapped else None
    written_count = len(written) if written else None
    if offered is None and mapped_count is None and written_count is None:
        return None, (
            "no lineage entries for the stream's designs and "
            f"{offered_reason or 'no source schema'}"
        )

    parts = [
        f"Scanner basis: lineage over {len(designs)} design(s), {len(entries)} entries."
    ]
    if mapped_count is not None:
        parts.append(f"mapped = {mapped_count} distinct source columns read by the mapping.")
    else:
        parts.append("mapped not established: no lineage entry names a source column.")
    if written_count is not None:
        parts.append(
            f"written = {written_count} of those source columns whose lineage resolves "
            f"into a destination-side component ({len(destination_fields)} distinct "
            "destination fields)."
        )
    else:
        parts.append(
            "written not established: no lineage entry resolves into a destination-side "
            "component."
        )
    if offered is not None:
        parts.append(f"offered = {offered} columns on the reading components' row schemas.")
    else:
        parts.append(f"offered not established: {offered_reason}.")
    return (
        ContractWidth(
            offered=offered,
            mapped=mapped_count,
            written=written_count,
            note=" ".join(parts),
        ),
        "",
    )


def _capped(conditions: list[StreamCondition], kind: str, noun: str) -> list[StreamCondition]:
    if len(conditions) <= _MAX_CONDITIONS_PER_KIND:
        return conditions
    head = conditions[:_MAX_CONDITIONS_PER_KIND]
    head.append(
        StreamCondition(
            kind=kind,  # type: ignore[arg-type]
            statement=(
                f"{len(conditions) - _MAX_CONDITIONS_PER_KIND} further {noun} on this "
                "stream's designs are not listed individually"
            ),
            evidence_note="scan_result.json",
        )
    )
    return head


def _scan_conditions(designs: list[Design]) -> list[StreamCondition]:
    by_kind: dict[str, list[StreamCondition]] = {"filter": [], "trigger": [], "disabled": []}
    for design in designs:
        for item in edge_conditions(design):
            by_kind[item.kind].append(
                StreamCondition(
                    kind=item.kind,  # type: ignore[arg-type]
                    statement=_redact(item.statement),
                    evidence_note=_redact(item.evidence_note),
                )
            )
        label = design.name or design.job_id
        if not design.active:
            by_kind["disabled"].append(
                StreamCondition(
                    kind="disabled",
                    statement=f"Design {label} is deactivated and does not run",
                    evidence_note=f"{label} (scan_result.json: active=false)",
                )
            )
            continue
        relevant = [
            name
            for name in deactivated_components(design)
            if component_type_family(
                str((design.component(name) or {}).get("component_type") or "")
            )
            != "utility"
        ]
        if relevant:
            total = sum(
                1
                for item in design.components
                if component_type_family(str(item.get("component_type") or "")) != "utility"
            )
            shown = ", ".join(relevant[:5]) + (
                f" (+{len(relevant) - 5} more)" if len(relevant) > 5 else ""
            )
            by_kind["disabled"].append(
                StreamCondition(
                    kind="disabled",
                    statement=(
                        f"{len(relevant)} of {total} non-logging components in {label} "
                        f"are deactivated: {shown}"
                    ),
                    evidence_note=f"{label} components with ACTIVATE=false",
                )
            )
    return [
        *_capped(by_kind["filter"], "filter", "route branch predicates"),
        *_capped(by_kind["trigger"], "trigger", "conditional triggers"),
        *_capped(by_kind["disabled"], "disabled", "deactivation facts"),
    ]


def _schedule_conditions(
    designs: list[Design], view: RuntimeView | None
) -> list[StreamCondition]:
    if view is None:
        return []
    results: list[StreamCondition] = []
    for design in designs:
        status, unit = view.lookup(design)
        if status != "covered" or unit is None or unit.cadence_basis != "gaps":
            continue
        env = unit.dominant_environment or "unnamed environment"
        results.append(
            StreamCondition(
                kind="schedule",
                statement=_redact(f"{design.name or design.job_id} runs {unit.cadence_label}"),
                evidence_note=(
                    f"TMC runtime mirror, dominant environment {env}, {view.window}, "
                    f"read scope {view.read_scope}"
                ),
            )
        )
    return _capped(results, "schedule", "observed schedules")


def _mechanism(
    stream: BusinessDataStream, designs: list[Design], paths: list[DeliveryPath]
) -> str:
    chains: list[str] = []
    for design in designs:
        chain = mechanism_chain(design)
        if chain and chain not in chains:
            chains.append(chain)
    if chains:
        text = "; ".join(chains[:_MAX_MECHANISM_DESIGNS])
        if len(chains) > _MAX_MECHANISM_DESIGNS:
            text += f" (+{len(chains) - _MAX_MECHANISM_DESIGNS} more designs)"
        return text
    for path in paths:
        if path.id != stream.delivery_path_id and path.stream_id != stream.id:
            continue
        steps: list[str] = []
        for segment in sorted(path.segments, key=lambda item: item.sequence):
            label = next(
                (
                    value.strip()
                    for value in (segment.protocol, segment.mechanism)
                    if value.strip() and value.strip().casefold() != "unknown"
                ),
                "",
            )
            if segment.component.strip():
                label = f"{label} ({segment.component.strip()})" if label else segment.component.strip()
            if label and label not in steps:
                steps.append(label)
        if steps:
            return "Delivery path: " + " -> ".join(steps)
    return ""


def _direction(designs: list[Design]) -> tuple[str, str]:
    signals: set[str] = set()
    for design in designs:
        found, _ = direction_signals(design)
        signals |= found
    if signals == {"push", "pull"}:
        return "hybrid", ""
    if signals:
        return next(iter(signals)), ""
    return "", "no reading or receiving component identified in the stream's designs"


def _runtime_statuses(
    designs: list[Design], view: RuntimeView
) -> list[tuple[Design, str, UnitRuntime | None]]:
    return [(design, *view.lookup(design)) for design in designs]


def _lifecycle(
    designs: list[Design], view: RuntimeView | None
) -> tuple[str, str]:
    if not designs:
        return "", "no scanned design resolves from the stream's artifact_ids"
    if view is not None:
        statuses = _runtime_statuses(designs, view)
        covered = [unit for _, status, unit in statuses if status == "covered" and unit]
        if any(unit.total_runs > 0 for unit in covered):
            return "active", ""
    if all(design_is_stopped(design) for design in designs):
        return "stopped", ""
    if view is None:
        return "", "no runtime payload and the designs are not deactivated"
    if covered and len(covered) == len(designs):
        return "dormant", ""
    if covered:
        return "", (
            "runtime shows zero executions for the covered designs but not every "
            "implementing design is covered; absence is not non-execution"
        )
    return "", "no implementing design is covered by the runtime mirror"


def _deployment(
    designs: list[Design], view: RuntimeView | None
) -> tuple[DeploymentAgreement | None, str]:
    if view is None:
        return None, "no runtime payload"
    pairs: list[tuple[Design, UnitRuntime]] = []
    for design, status, unit in _runtime_statuses(designs, view):
        if status == "covered" and unit is not None and unit.deployed_version:
            pairs.append((design, unit))
    if not pairs:
        return None, "runtime payload reports no deployed version for the stream's designs"
    comparable = [(design, unit) for design, unit in pairs if design.version.strip()]
    if not comparable:
        return None, "scanned designs carry no version to compare"
    verdicts = {
        (design.name or design.job_id): compare_versions(design.version, unit.deployed_version)
        for design, unit in comparable
    }
    distinct = set(verdicts.values())
    agreement = distinct.pop() if len(distinct) == 1 else "unknown"
    if len(comparable) == 1:
        design, unit = comparable[0]
        supplied, deployed = design.version.strip(), unit.deployed_version
    else:
        supplied = "; ".join(
            f"{design.name or design.job_id}={design.version.strip()}"
            for design, _ in comparable
        )
        deployed = "; ".join(
            f"{design.name or design.job_id}={unit.deployed_version}"
            for design, unit in comparable
        )
    note = f"Scanned design version vs TMC deployed version over {len(comparable)} design(s)"
    if agreement == "unknown" and len(verdicts) > 1:
        note += "; designs disagree: " + ", ".join(
            f"{name} {verdict}" for name, verdict in sorted(verdicts.items())
        )
    return (
        DeploymentAgreement(
            supplied_version=supplied,
            deployed_version=deployed,
            agreement=agreement,  # type: ignore[arg-type]
            note=note,
        ),
        "",
    )


class _DocIndex:
    """Top-level documents and a memoised boundary-aware name search."""

    def __init__(self, docs_dir: Path | None) -> None:
        self.texts: dict[str, str] = {}
        self.problem = ""
        self._hits: dict[str, frozenset[str]] = {}
        if docs_dir is None:
            self.problem = "no docs_dir supplied"
            return
        try:
            if not Path(docs_dir).is_dir():
                self.problem = "docs_dir does not exist"
                return
            for path in sorted(Path(docs_dir).glob("*.md")):
                if path.name.startswith(("_", ".")) or not path.is_file():
                    continue
                try:
                    self.texts[path.name] = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        except OSError as exc:
            self.problem = f"docs_dir unreadable ({type(exc).__name__})"
            return
        if not self.texts:
            self.problem = "no top-level documents in docs_dir"

    def files_naming(self, name: str) -> frozenset[str]:
        if name not in self._hits:
            # Exact, case-sensitive, and bounded on identifier characters:
            # `X_GetLocation` must not match `X_GetLocations` or `X_GetLocation_v2`.
            pattern = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
            )
            self._hits[name] = frozenset(
                filename for filename, text in sorted(self.texts.items()) if pattern.search(text)
            )
        return self._hits[name]


def _document_refs(designs: list[Design], docs: _DocIndex) -> set[str]:
    refs: set[str] = set()
    for design in designs:
        for key in design.keys:
            if len(key) >= 3:
                refs |= docs.files_naming(key)
    return refs


# --- Runtime observations -----------------------------------------------------


def _runtime_observation(
    stream: BusinessDataStream,
    designs: list[Design],
    view: RuntimeView,
    index: ScanIndex,
    tally: _Tally,
) -> RuntimeObservation | None:
    statuses = _runtime_statuses(designs, view)
    covered = [(design, unit) for design, status, unit in statuses if status == "covered" and unit]
    uncovered = [(design, status) for design, status, _ in statuses if status != "covered"]
    evidence_ids: set[str] = set()
    for _, unit in covered:
        record = unit_evidence_record(view, unit)
        tally.runtime_evidence[record["id"]] = record
        evidence_ids.add(record["id"])
    reasons: dict[str, list[Design]] = {}
    for design, status in uncovered:
        reason = absence_reason(view, index, design, status)
        reasons.setdefault(reason, []).append(design)
        record = absence_evidence_record(view, design, status, reason)
        tally.runtime_evidence[record["id"]] = record
        evidence_ids.add(record["id"])
    reason_text = "; ".join(
        f"{_names(group)}: {reason}" for reason, group in sorted(reasons.items())
    )

    if not covered:
        behavior = (
            f"No TMC runtime record for {len(uncovered)} implementing design(s) in the "
            f"{view.window} (read scope {view.read_scope}). {reason_text}. Absence of a "
            "measurement is not absence of execution."
        )
        return RuntimeObservation(
            id=f"rt-{stream.id}",
            name=f"{stream.name}: no runtime measurement",
            description=(
                "Deterministic transcription of the staged TMC runtime lookup; states why "
                "nothing was measured. No model involved."
            ),
            status="observed",
            confidence=0.8,
            evidence_ids=sorted(evidence_ids),
            stream_ids=[stream.id],
            observed_behavior=_redact(behavior),
            observation_window=view.window,
            kind="absence",
            metrics=[
                RuntimeMetric(
                    label="implementing designs without a runtime record",
                    value=str(len(uncovered)),
                    window=view.window,
                    source=view.source_for(()),
                    note=_redact(reason_text)[:500],
                )
            ],
            source=view.source_for(()),
        )

    runs = sum(unit.total_runs for _, unit in covered)
    failed = sum(unit.failed_runs for _, unit in covered)
    environments = tuple(sorted({env for _, unit in covered for env in unit.environments}))
    metrics: list[RuntimeMetric] = []
    aggregate_source = view.source_for(environments)
    if len(covered) > 1 or uncovered:
        metrics.append(
            RuntimeMetric(
                label="designs measured",
                value=f"{len(covered)} of {len(designs)}",
                window=view.window,
                source=aggregate_source,
            )
        )
    multi = len(covered) > 1
    if multi:
        metrics.extend(
            [
                RuntimeMetric(label="runs", value=str(runs), window=view.window, source=aggregate_source),
                RuntimeMetric(label="failures", value=str(failed), window=view.window, source=aggregate_source),
                RuntimeMetric(
                    label="failure rate",
                    value=format_rate(failed, runs),
                    unit="fraction",
                    window=view.window,
                    source=aggregate_source,
                ),
            ]
        )
    ranked = sorted(covered, key=lambda item: (-item[1].total_runs, item[0].name, item[0].job_id))
    for design, unit in ranked[:_MAX_UNIT_METRICS]:
        prefix = f"{design.name or design.job_id}: " if multi else ""
        source = view.source_for(unit.environments)
        window = view.window
        if unit.first_run_at or unit.last_run_at:
            window += f" (first run {unit.first_run_at or 'n/a'}, last run {unit.last_run_at or 'n/a'})"
        metrics.append(RuntimeMetric(label=f"{prefix}runs", value=str(unit.total_runs), window=window, source=source))
        metrics.append(RuntimeMetric(label=f"{prefix}failures", value=str(unit.failed_runs), window=window, source=source))
        metrics.append(
            RuntimeMetric(
                label=f"{prefix}failure rate",
                value=format_rate(unit.failed_runs, unit.total_runs),
                unit="fraction",
                window=window,
                source=source,
            )
        )
        if unit.gap_p50_seconds is not None:
            metrics.append(
                RuntimeMetric(
                    label=f"{prefix}median gap between runs",
                    value=f"{unit.gap_p50_seconds:.0f}",
                    unit="seconds",
                    window=window,
                    source=source,
                    note=f"dominant environment {unit.dominant_environment or 'unnamed'}",
                )
            )
        metrics.append(
            RuntimeMetric(
                label=f"{prefix}environments",
                value=", ".join(unit.environments) or "none reported",
                window=window,
                source=source,
            )
        )
    if len(ranked) > _MAX_UNIT_METRICS:
        metrics.append(
            RuntimeMetric(
                label="further measured designs not itemised",
                value=str(len(ranked) - _MAX_UNIT_METRICS),
                window=view.window,
                source=aggregate_source,
            )
        )

    if multi:
        behavior = (
            f"{len(covered)} of {len(designs)} implementing designs have TMC runtime "
            f"records: {runs} runs, {failed} failed ({format_rate(failed, runs)}) in the "
            f"{view.window} across {', '.join(environments) or 'no named environment'}."
        )
    else:
        design, unit = covered[0]
        behavior = (
            f"{design.name or design.job_id} ran {unit.total_runs} times in the "
            f"{view.window} ({', '.join(unit.environments) or 'no named environment'}); "
            f"{unit.failed_runs} failed ({format_rate(unit.failed_runs, unit.total_runs)})."
        )
    if uncovered:
        behavior += f" Not measured: {reason_text}."
    if view.partial:
        behavior += " The runtime fetch was partial; some lookups did not complete."
    return RuntimeObservation(
        id=f"rt-{stream.id}",
        name=f"{stream.name}: runtime {'reliability' if runs else 'volume'}",
        description=(
            "Deterministic transcription of the staged TMC runtime aggregate. No model "
            "involved."
        ),
        status="observed",
        confidence=0.9,
        evidence_ids=sorted(evidence_ids),
        stream_ids=[stream.id],
        observed_behavior=_redact(behavior),
        observation_window=view.window,
        kind="reliability" if runs else "volume",
        metrics=metrics,
        source=aggregate_source,
    )


# --- Transformations ----------------------------------------------------------


def _enrich_transformation(
    transformation: BusinessTransformation,
    designs: list[Design],
    index: ScanIndex,
    tally: _Tally,
) -> None:
    # A transformation that cites lineage rows is matched against exactly those
    # rows: the same column pair recurs across a design's maps, and only the
    # cited row is the one the claim is about.
    cited = set(transformation.evidence_ids)
    candidates = [
        (design, entry)
        for design in designs
        for entry in iter_sorted_entries(index.lineage_for(design))
    ]
    cited_candidates = [
        (design, entry) for design, entry in candidates if lineage_evidence_id(entry) in cited
    ]
    by_pair: dict[tuple[str, str], list[tuple[Design, Mapping[str, Any]]]] = {}
    for design, entry in cited_candidates or candidates:
        key = (column_key(entry.get("source_column")), column_key(entry.get("target_column")))
        by_pair.setdefault(key, []).append((design, entry))
    for mapping in transformation.mappings:
        if mapping.expression.strip():
            continue
        matches = by_pair.get((column_key(mapping.source), column_key(mapping.target)), [])
        if not matches:
            tally.skip(
                "transformations.expression",
                "no lineage entry of the transformation's designs matches source/target",
            )
            continue
        expressions = {lineage_expression(entry) for _, entry in matches}
        expressions.discard("")
        if len(expressions) != 1:
            tally.skip(
                "transformations.expression",
                "lineage records no expression"
                if not expressions
                else "several lineage entries with differing expressions match source/target",
            )
            continue
        mapping.expression = _redact(next(iter(expressions)))
        if not mapping.category.strip():
            categories = sorted({lineage_category(entry) for _, entry in matches} - {""})
            mapping.category = "/".join(categories)
        if mapping.reaches_target == "unknown":
            reaches = {entry_reaches_target(design, entry) for design, entry in matches}
            if len(reaches) == 1:
                mapping.reaches_target = reaches.pop()  # type: ignore[assignment]
            elif "yes" in reaches:
                mapping.reaches_target = "partial"
        tally.fill("transformations.expression")
    if transformation.kind == "unknown" and transformation.mappings and all(
        mapping.source.strip()
        and mapping.target.strip()
        and mapping.mapping_type not in {"value", "constant"}
        for mapping in transformation.mappings
    ):
        transformation.kind = "field_mapping"
        tally.fill("transformations.kind")


# --- Public API ---------------------------------------------------------------


def _guard(tally: _Tally, name: str, action: Any, *args: Any) -> Any:
    try:
        return action(*args)
    except Exception as exc:  # noqa: BLE001 - enrichment is additive and never raises
        tally.skip(name, f"enrichment error ({type(exc).__name__})")
        return None


def enrich_topology(
    topology: BusinessTopology, sources: EnrichmentSources
) -> tuple[BusinessTopology, EnrichmentReport]:
    """Return an enriched deep copy of ``topology`` and what was (not) filled."""

    tally = _Tally()
    result = topology.model_copy(deep=True)
    try:
        index = load_scan_index(Path(sources.scan_dir), sources.unit_allowlist)
    except Exception as exc:  # noqa: BLE001
        index = ScanIndex(problems=[f"scan index failed ({type(exc).__name__})"])
    if not index.designs:
        tally.skip(
            "scan",
            "; ".join(sorted(set(index.problems))) or "no scanned design in scope",
        )
    try:
        view, runtime_reason = load_runtime_view(sources.runtime_payload)
    except Exception as exc:  # noqa: BLE001
        view, runtime_reason = None, f"runtime payload unreadable ({type(exc).__name__})"
    docs = _DocIndex(sources.docs_dir)

    paths = list(result.delivery_paths)
    designs_by_stream: dict[str, list[Design]] = {}
    owned_ids = {f"rt-{stream.id}" for stream in result.data_streams}
    new_observations: list[RuntimeObservation] = []

    for stream in result.data_streams:
        designs = _designs_for(_stream_artifact_ids(stream, paths), index)
        designs_by_stream[stream.id] = designs

        width, reason = _guard(tally, "contract_width", _contract_width, designs, index) or (None, "")
        if width is not None:
            stream.contract_width = width
            tally.fill("contract_width")
        elif reason:
            tally.skip("contract_width", reason)

        conditions = _guard(tally, "conditions", _scan_conditions, designs) or []
        conditions += _guard(tally, "conditions", _schedule_conditions, designs, view) or []
        seen = {(item.kind, item.statement.strip()) for item in stream.conditions}
        for condition in conditions:
            key = (condition.kind, condition.statement.strip())
            if not condition.statement.strip() or key in seen:
                continue
            seen.add(key)
            stream.conditions.append(condition)
            tally.fill("conditions")

        if not stream.mechanism.strip():
            mechanism = _guard(tally, "mechanism", _mechanism, stream, designs, paths) or ""
            if mechanism:
                stream.mechanism = _redact(mechanism)
                tally.fill("mechanism")
            elif designs:
                tally.skip("mechanism", "the stream's designs declare no active non-logging component")
            else:
                tally.skip("mechanism", "no scanned design and no labelled delivery segment")

        if stream.direction_pattern == "unknown":
            direction, reason = _guard(tally, "direction_pattern", _direction, designs) or ("", "")
            if direction:
                stream.direction_pattern = direction  # type: ignore[assignment]
                tally.fill("direction_pattern")
            elif reason:
                tally.skip("direction_pattern", reason)

        if stream.lifecycle == "unknown":
            lifecycle, reason = _guard(tally, "lifecycle", _lifecycle, designs, view) or ("", "")
            if lifecycle:
                stream.lifecycle = lifecycle  # type: ignore[assignment]
                tally.fill("lifecycle")
            elif reason:
                tally.skip("lifecycle", reason)

        deployment, reason = _guard(tally, "deployment", _deployment, designs, view) or (None, "")
        if deployment is not None:
            stream.deployment = deployment
            tally.fill("deployment")
        elif reason:
            tally.skip("deployment", reason)

        if docs.problem:
            tally.skip("document_refs", docs.problem)
        else:
            refs = _guard(tally, "document_refs", _document_refs, designs, docs) or set()
            merged = sorted(set(stream.document_refs) | refs)
            if merged != sorted(set(stream.document_refs)):
                tally.fill("document_refs")
            if refs or stream.document_refs:
                stream.document_refs = merged

        observation_id = f"rt-{stream.id}"
        stream.runtime_observation_ids = [
            ref for ref in stream.runtime_observation_ids if ref != observation_id
        ]
        if view is None:
            tally.skip("runtime_observations", runtime_reason)
            continue
        if not designs:
            tally.skip(
                "runtime_observations",
                "no scanned design resolves from the stream's artifact_ids",
            )
            continue
        observation = _guard(
            tally, "runtime_observations", _runtime_observation, stream, designs, view, index, tally
        )
        if observation is None:
            continue
        new_observations.append(observation)
        stream.runtime_observation_ids.append(observation.id)
        tally.fill("runtime_observations")
        if observation.kind == "absence":
            tally.fill("runtime_observations.absence")

    # Owned observations are regenerated, never merged: drop every previous one
    # (also for a stream that no longer yields one) before appending.
    result.runtime_observations = [
        item for item in result.runtime_observations if item.id not in owned_ids
    ] + new_observations

    for transformation in result.transformations:
        artifact_ids = set(transformation.artifact_ids)
        for stream_id in transformation.stream_ids:
            artifact_ids.update(
                design.artifact_id for design in designs_by_stream.get(stream_id, [])
            )
        designs = _designs_for(sorted(artifact_ids), index)
        if not designs:
            tally.skip("transformations.expression", "no scanned design resolves for the transformation")
            continue
        _guard(tally, "transformations.expression", _enrich_transformation, transformation, designs, index, tally)

    return result, tally.report()


# --- Runtime evidence registration --------------------------------------------


def register_runtime_evidence(
    bundle: BusinessTopologyEvidenceBundle,
    records: Mapping[str, Mapping[str, Any]],
) -> BusinessTopologyEvidenceBundle:
    """Return a bundle copy that also knows the runtime aggregates cited.

    Mirrors ``register_doc_evidence``: the bundle is frozen, so this replaces
    rather than mutates, and a caller that needs the scanner-only view keeps
    the original.
    """

    if not records:
        return bundle
    extra = {str(key): dict(value) for key, value in sorted(records.items())}
    return dataclasses.replace(
        bundle,
        evidence_ids=frozenset(bundle.evidence_ids | set(extra)),
        evidence_types={
            **bundle.evidence_types,
            **{key: RUNTIME_EVIDENCE_TYPE for key in extra},
        },
        evidence_records={**bundle.evidence_records, **extra},
    )


def persist_runtime_evidence(
    evidence_root: Path, records: Mapping[str, Mapping[str, Any]]
) -> None:
    """Write the runtime records beside the scanner ledger so a reload sees them.

    ``evidence_root`` is ``<staging>/_topology/evidence``. Replaces
    ``runtime.jsonl`` wholesale (removing it when ``records`` is empty) and
    rewrites ``evidence_ids.json`` so it lists exactly these module-owned
    ``runtime:`` ids -- a stale file from an earlier attempt would otherwise
    make ``load_business_topology_evidence`` reject the whole bundle. This is
    the one writer in this module and is deliberately separate from
    :func:`enrich_topology`, which stays pure.
    """

    ledger_path = evidence_root / "evidence_ids.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    types = dict(ledger.get("evidence") or {})
    types = {
        key: value
        for key, value in types.items()
        if not (value == RUNTIME_EVIDENCE_TYPE and key.startswith(f"{RUNTIME_EVIDENCE_PREFIX}:"))
    }
    path = evidence_root / RUNTIME_EVIDENCE_FILENAME
    if records:
        rows = [dict(records[key]) for key in sorted(records)]
        for row in rows:
            types[str(row["id"])] = RUNTIME_EVIDENCE_TYPE
        path.write_text(
            "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    else:
        path.unlink(missing_ok=True)
    ledger["evidence"] = dict(sorted(types.items()))
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


__all__ = [
    "EnrichmentReport",
    "EnrichmentSources",
    "FILLED_FIELDS",
    "RUNTIME_EVIDENCE_FILENAME",
    "enrich_topology",
    "persist_runtime_evidence",
    "register_runtime_evidence",
]
