"""Markdown serializers for EngineeringFacts artifacts.

Two deliberate properties of this module:

* ``engineering_facts.md`` is a real AGGREGATE, not a row census. It is the
  one fact artifact attached to every Phase 2 and Phase 3 prompt, so it is
  also the only one guaranteed to reach the model -- the big relational maps
  (a 1.75 MB ``dependency_map.md`` on a production run) exceed the context
  ceiling and are dropped whole. The aggregate is therefore built from the
  in-memory :class:`EngineeringFacts` model (never by re-parsing the emitted
  ``.md`` files) and is capped at :data:`SUMMARY_MAX_BYTES`, which sits under
  ``config.phase3_shared_context_file_bytes``. One byte over that threshold
  and ``plan_phase3_slices`` reclassifies the file from SHARED to splittable
  EVIDENCE, so slice 1 and slice 3 of one document would see different facts.

* Every list in this file is sorted on explicit keys with explicit
  tie-breakers, and no aggregate is derived from set/dict iteration order.
  Phase 2/3 prompt caching keys on byte-stable context: the same facts in a
  different input order must produce the same bytes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from pydantic import BaseModel

from mcp_server.providers.custom.engineering_facts.models import (
    DependencyFact,
    EngineeringFacts,
    EvidenceRef,
    KnownRiskFact,
)


class ArtifactSpec(NamedTuple):
    """Serialization contract for one derived fact map."""

    field_name: str
    title: str
    columns: list[str]
    # Column to group rows under ``## <value>`` headings. ``None`` keeps the
    # historical single flat table. Opt-in per artifact so only the map that
    # needs section boundaries gets them (see ``_table``).
    group_by: str | None = None
    # Value that sorts first inside a grouped table; the remaining groups
    # follow case-insensitively.
    group_first: str | None = None


MARKDOWN_ARTIFACTS: dict[str, ArtifactSpec] = {
    "dependency_map": ArtifactSpec(
        "dependencies",
        "Dependency Map",
        ["source", "target", "dependency_type", "confidence"],
        group_by="dependency_type",
        group_first="calls_job",
    ),
    "change_impact_matrix": ArtifactSpec(
        "impacts",
        "Change Impact Matrix",
        ["changed_entity", "impacted_entity", "check", "confidence"],
    ),
    "architecture_map": ArtifactSpec(
        "architecture",
        "Architecture Map",
        ["name", "layer", "component", "boundary", "confidence"],
    ),
    "data_flow_map": ArtifactSpec(
        "data_flows",
        "Data Flow Map",
        ["source", "target", "transformation", "schema_hint", "confidence"],
    ),
    "api_surface": ArtifactSpec(
        "api_surface",
        "API Surface",
        ["method", "route", "handler", "name", "confidence"],
    ),
    "config_runtime_map": ArtifactSpec(
        "config_runtime",
        "Config Runtime Map",
        ["config_type", "key", "name", "value_hint", "confidence"],
    ),
    "test_coverage_map": ArtifactSpec(
        "test_coverage",
        "Test Coverage Map",
        ["test_file", "covered_entity", "gap", "suggested_scenario", "confidence"],
    ),
    "known_risks": ArtifactSpec(
        "known_risks",
        "Known Risks",
        ["severity", "affected_entity", "risk_type", "name", "remediation", "confidence"],
    ),
}


# Hard cap for engineering_facts.md. Deliberately below
# config.phase3_shared_context_file_bytes (16_000) with headroom, because a
# file at or below that threshold is replicated byte-stable into every Phase 3
# slice and anything above it is bin-packed as evidence.
SUMMARY_MAX_BYTES = 15_500

# The dependency_type that carries job-to-job orchestration. Its edge list is
# reproduced in full in the aggregate; it is small relative to the map (a
# verified production run: 123 of 17,983 dependency rows) and it is the one
# relation a reader cannot reconstruct from counts.
CALLS_JOB_TYPE = "calls_job"
EXTERNAL_SYSTEM_TYPE = "external_system"

_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


class MarkdownFactWriter:
    """Write human-readable EngineeringFacts artifacts."""

    def write(self, facts: EngineeringFacts, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        summary = output_dir / "engineering_facts.md"
        summary.write_text(_summary(facts), encoding="utf-8")
        written.append(summary)
        for artifact, spec in MARKDOWN_ARTIFACTS.items():
            values = getattr(facts, spec.field_name)
            if not values:
                continue
            path = output_dir / f"{artifact}.md"
            path.write_text(
                _table(
                    spec.title,
                    values,
                    spec.columns,
                    group_by=spec.group_by,
                    group_first=spec.group_first,
                ),
                encoding="utf-8",
            )
            written.append(path)
        return written


# --------------------------------------------------------------------------
# Aggregate summary (engineering_facts.md)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Limits:
    """One rung of the deterministic shrink ladder."""

    external_rows: int
    degree_rows: int
    risk_rows: int
    type_rows: int
    source_samples: int


# Applied in order; the first rung whose rendering fits SUMMARY_MAX_BYTES
# wins. Only aggregated/top-N material shrinks -- the scale line, the
# per-type census head and the evidence pointers are never dropped, and the
# complete calls_job edge list is the LAST thing to give (see _summary).
_SHRINK_LADDER: tuple[_Limits, ...] = (
    _Limits(external_rows=80, degree_rows=25, risk_rows=30, type_rows=60, source_samples=5),
    _Limits(external_rows=60, degree_rows=20, risk_rows=20, type_rows=40, source_samples=4),
    _Limits(external_rows=40, degree_rows=15, risk_rows=12, type_rows=30, source_samples=3),
    _Limits(external_rows=25, degree_rows=10, risk_rows=8, type_rows=25, source_samples=2),
    _Limits(external_rows=15, degree_rows=5, risk_rows=5, type_rows=20, source_samples=1),
    _Limits(external_rows=8, degree_rows=3, risk_rows=3, type_rows=15, source_samples=1),
    _Limits(external_rows=0, degree_rows=0, risk_rows=0, type_rows=10, source_samples=0),
)


def _summary(facts: EngineeringFacts) -> str:
    """Render the bounded, deterministic aggregate of the whole fact model."""
    data = _aggregate(facts)
    for limits in _SHRINK_LADDER:
        text = _render_summary(data, limits, calls_limit=None)
        if _size(text) <= SUMMARY_MAX_BYTES:
            return text

    # Every rung overflowed: the complete calls_job edge list alone does not
    # fit. Give it up last and deterministically -- keep the largest prefix of
    # the sorted edge list that fits and say so in the file, rather than
    # silently emitting an oversized artifact that stops being shared.
    tightest = _SHRINK_LADDER[-1]
    low, high = 0, len(data.calls_rows)
    while low < high:
        mid = (low + high + 1) // 2
        if _size(_render_summary(data, tightest, calls_limit=mid)) <= SUMMARY_MAX_BYTES:
            low = mid
        else:
            high = mid - 1
    return _render_summary(data, tightest, calls_limit=low)


class _Edge(NamedTuple):
    source: str
    target: str
    confidence: str
    evidence: str
    sort_key: tuple[str, str, str, str, str]


@dataclass(frozen=True)
class _TypeStat:
    name: str
    rows: int
    sources: int
    targets: int


@dataclass(frozen=True)
class _ExternalStat:
    target: str
    usages: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class _Aggregate:
    project_id: str
    platform: str
    unit_count: int
    group_count: int
    counts: tuple[tuple[str, int], ...]
    dependency_rows: int
    distinct_sources: int
    distinct_targets: int
    calls_rows: tuple[_Edge, ...]
    external: tuple[_ExternalStat, ...]
    external_rows: int
    out_degree: tuple[tuple[str, int], ...]
    in_degree: tuple[tuple[str, int], ...]
    type_stats: tuple[_TypeStat, ...]
    severity_census: tuple[tuple[str, int], ...]
    risks: tuple[tuple[str, str, str, str, str], ...]
    risk_total: int


def _aggregate(facts: EngineeringFacts) -> _Aggregate:
    deps: list[DependencyFact] = list(facts.dependencies)

    units: set[str] = {a.component for a in facts.architecture if a.component}
    groups: set[str] = {a.layer for a in facts.architecture if a.layer}
    if not units:
        units = {e.unit_name for e in facts.entities if e.unit_name}
    if not groups:
        groups = {
            str(e.metadata.get("group"))
            for e in facts.entities
            if str(e.metadata.get("group") or "")
        }

    sources = Counter(dep.source for dep in deps if dep.source)
    targets = Counter(dep.target for dep in deps if dep.target)

    calls = [
        _Edge(
            source=_cell(dep.source),
            target=_cell(dep.target),
            confidence=_confidence(dep.confidence),
            evidence=_cell(_ref_label(dep.evidence)),
            sort_key=(
                dep.source.casefold(),
                dep.source,
                dep.target.casefold(),
                dep.target,
                dep.id,
            ),
        )
        for dep in deps
        if dep.dependency_type == CALLS_JOB_TYPE
    ]
    calls.sort(key=lambda edge: edge.sort_key)

    ext_usages: Counter[str] = Counter()
    ext_sources: dict[str, set[str]] = {}
    for dep in deps:
        if dep.dependency_type != EXTERNAL_SYSTEM_TYPE or not dep.target:
            continue
        ext_usages[dep.target] += 1
        if dep.source:
            ext_sources.setdefault(dep.target, set()).add(dep.source)
    external = tuple(
        _ExternalStat(
            target=target,
            usages=count,
            sources=tuple(
                sorted(ext_sources.get(target, set()), key=lambda s: (s.casefold(), s))
            ),
        )
        for target, count in sorted(
            ext_usages.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
    )

    by_type: dict[str, list[DependencyFact]] = {}
    for dep in deps:
        by_type.setdefault(dep.dependency_type or "(unspecified)", []).append(dep)
    type_stats = tuple(
        sorted(
            (
                _TypeStat(
                    name=name,
                    rows=len(rows),
                    sources=len({r.source for r in rows if r.source}),
                    targets=len({r.target for r in rows if r.target}),
                )
                for name, rows in by_type.items()
            ),
            key=lambda stat: (-stat.rows, stat.name.casefold(), stat.name),
        )
    )

    severity_counter = Counter(
        (risk.severity or "UNKNOWN").strip().upper() for risk in facts.known_risks
    )
    severity_census = tuple(
        sorted(
            severity_counter.items(),
            key=lambda item: (_severity_rank(item[0]), item[0]),
        )
    )
    ranked_risks = sorted(facts.known_risks, key=_risk_sort_key)
    risks = tuple(
        (
            _cell((risk.severity or "UNKNOWN").strip().upper()),
            _cell(risk.affected_entity or ""),
            _cell(risk.risk_type or ""),
            _cell(risk.name or ""),
            _cell(_ref_label(risk.evidence)),
        )
        for risk in ranked_risks
    )

    return _Aggregate(
        project_id=facts.project_id,
        platform=facts.platform,
        unit_count=len(units),
        group_count=len(groups),
        counts=(
            ("entities", len(facts.entities)),
            ("usage records", len(facts.usage)),
            ("dependencies", len(deps)),
            ("change impacts", len(facts.impacts)),
            ("architecture facts", len(facts.architecture)),
            ("data flows", len(facts.data_flows)),
            ("api surface entries", len(facts.api_surface)),
            ("config/runtime facts", len(facts.config_runtime)),
            ("test coverage facts", len(facts.test_coverage)),
            ("known risks", len(facts.known_risks)),
        ),
        dependency_rows=len(deps),
        distinct_sources=len(sources),
        distinct_targets=len(targets),
        calls_rows=tuple(calls),
        external=external,
        external_rows=sum(ext_usages.values()),
        out_degree=_ranked_degrees(sources),
        in_degree=_ranked_degrees(targets),
        type_stats=type_stats,
        severity_census=severity_census,
        risks=risks,
        risk_total=len(facts.known_risks),
    )


def _ranked_degrees(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold(), item[0]))
    )


def _severity_rank(severity: str) -> int:
    normalized = (severity or "").strip().upper()
    if normalized in _SEVERITY_ORDER:
        return _SEVERITY_ORDER.index(normalized)
    return len(_SEVERITY_ORDER)


def _risk_sort_key(risk: KnownRiskFact) -> tuple[Any, ...]:
    severity = (risk.severity or "UNKNOWN").strip().upper()
    entity = risk.affected_entity or ""
    risk_type = risk.risk_type or ""
    name = risk.name or ""
    return (
        _severity_rank(severity),
        severity,
        -risk.confidence,
        entity.casefold(),
        entity,
        risk_type.casefold(),
        risk_type,
        name.casefold(),
        name,
        risk.id,
    )


def _render_summary(data: _Aggregate, limits: _Limits, *, calls_limit: int | None) -> str:
    lines: list[str] = [
        "# Engineering Facts",
        "",
        "Deterministic aggregate of the extracted engineering facts. Built from",
        "the fact model, not by re-reading the maps below. Counts are exact;",
        "sampled sections say how many rows they omit and where the full rows are.",
        "",
        "## Scale",
        "",
        f"- Project: {_cell(data.project_id)} | Platform: {_cell(data.platform)}",
        (
            f"- Jobs/units: {data.unit_count} | Groups: {data.group_count} | "
            f"Distinct dependency sources: {data.distinct_sources} | "
            f"Distinct dependency targets: {data.distinct_targets}"
        ),
        (
            f"- Dependency rows: {data.dependency_rows} across "
            f"{len(data.type_stats)} dependency types"
        ),
    ]
    lines.extend(f"- {label.capitalize()}: {count}" for label, count in data.counts)
    lines.append("")

    lines.extend(_render_calls(data, calls_limit))
    lines.extend(_render_external(data, limits))
    lines.extend(_render_degrees(data, limits))
    lines.extend(_render_types(data, limits))
    lines.extend(_render_risks(data, limits))
    lines.extend(_render_pointers(data))
    return "\n".join(lines)


def _render_calls(data: _Aggregate, calls_limit: int | None) -> list[str]:
    total = len(data.calls_rows)
    shown = total if calls_limit is None else min(calls_limit, total)
    header = (
        f"## Job Call Graph (`{CALLS_JOB_TYPE}`) -- "
        f"{total} of {data.dependency_rows} dependency rows"
    )
    lines = [header, ""]
    if total == 0:
        lines.extend(
            [
                f"No `{CALLS_JOB_TYPE}` dependency rows were extracted. Absence of "
                "job-to-job orchestration edges is a fact, not a gap in this file.",
                "",
            ]
        )
        return lines
    if shown == total:
        lines.append("Complete edge list, every row verbatim.")
    else:
        lines.append(
            f"TRUNCATED at the {SUMMARY_MAX_BYTES}-byte cap: first {shown} of "
            f"{total} edges in (source, target) order. The remaining "
            f"{total - shown} are in `dependency_map.md` under `## {CALLS_JOB_TYPE}`."
        )
    lines.extend(
        [
            "",
            "|source|target|confidence|evidence|",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        f"|{edge.source}|{edge.target}|{edge.confidence}|{edge.evidence}|"
        for edge in data.calls_rows[:shown]
    )
    lines.append("")
    return lines


def _render_external(data: _Aggregate, limits: _Limits) -> list[str]:
    lines = [
        f"## External Systems (`{EXTERNAL_SYSTEM_TYPE}`) -- "
        f"{len(data.external)} systems over {data.external_rows} rows",
        "",
    ]
    if not data.external:
        lines.extend(
            [
                "No `external_system` dependency rows were extracted.",
                "",
            ]
        )
        return lines
    if limits.external_rows <= 0:
        lines.extend(
            [
                "Omitted at the byte cap; see `dependency_map.md` under "
                f"`## {EXTERNAL_SYSTEM_TYPE}`.",
                "",
            ]
        )
        return lines
    shown = data.external[: limits.external_rows]
    lines.extend(
        [
            "Grouped by target system, ordered by usage count.",
            "",
            "|external system|usages|distinct users|example users|",
            "|---|---|---|---|",
        ]
    )
    for stat in shown:
        sample = "; ".join(stat.sources[: limits.source_samples]) if limits.source_samples else ""
        remainder = len(stat.sources) - limits.source_samples
        if limits.source_samples and remainder > 0:
            sample += f"; +{remainder} more"
        lines.append(
            f"|{_cell(stat.target)}|{stat.usages}|{len(stat.sources)}|{_cell(sample)}|"
        )
    if len(data.external) > len(shown):
        lines.append("")
        lines.append(
            f"+{len(data.external) - len(shown)} further external systems in "
            f"`dependency_map.md` under `## {EXTERNAL_SYSTEM_TYPE}`."
        )
    lines.append("")
    return lines


def _render_degrees(data: _Aggregate, limits: _Limits) -> list[str]:
    lines: list[str] = [
        "## Busiest Jobs by Dependency Degree",
        "",
        "Degree counts dependency rows of every type, not only job calls.",
        "",
    ]
    if not (data.out_degree or data.in_degree):
        lines.extend(["No dependency rows were extracted.", ""])
        return lines
    if limits.degree_rows <= 0:
        lines.extend(["Omitted at the byte cap; see `dependency_map.md`.", ""])
        return lines
    for title, ranked in (
        ("Out-degree (depends on)", data.out_degree),
        ("In-degree (depended on by)", data.in_degree),
    ):
        lines.extend(
            [
                f"### {title} -- top {min(limits.degree_rows, len(ranked))} of {len(ranked)}",
                "",
                "|name|rows|",
                "|---|---|",
            ]
        )
        lines.extend(
            f"|{_cell(name)}|{count}|" for name, count in ranked[: limits.degree_rows]
        )
        lines.append("")
    return lines


def _render_types(data: _Aggregate, limits: _Limits) -> list[str]:
    lines = [
        "## Dependency Rows By Type",
        "",
        "Every row aggregated away above is counted here; the rows themselves "
        "are in `dependency_map.md`, one `##` section per type.",
        "",
    ]
    if not data.type_stats:
        lines.extend(["No dependency rows were extracted.", ""])
        return lines
    lines.extend(
        [
            "|dependency_type|rows|distinct sources|distinct targets|",
            "|---|---|---|---|",
        ]
    )
    shown = data.type_stats[: max(limits.type_rows, 0)]
    lines.extend(
        f"|{_cell(stat.name)}|{stat.rows}|{stat.sources}|{stat.targets}|"
        for stat in shown
    )
    remainder = data.type_stats[len(shown):]
    if remainder:
        lines.append("")
        lines.append(
            f"+{len(remainder)} further dependency types covering "
            f"{sum(stat.rows for stat in remainder)} rows; see `dependency_map.md`."
        )
    lines.append("")
    return lines


def _render_risks(data: _Aggregate, limits: _Limits) -> list[str]:
    lines = [f"## Known Risks -- {data.risk_total} total", ""]
    if not data.risk_total:
        lines.extend(["No known-risk facts were extracted.", ""])
        return lines
    lines.extend(["|severity|count|", "|---|---|"])
    lines.extend(f"|{_cell(sev)}|{count}|" for sev, count in data.severity_census)
    lines.append("")
    if limits.risk_rows <= 0:
        lines.extend(["Individual risks omitted at the byte cap; see `known_risks.md`.", ""])
        return lines
    shown = data.risks[: limits.risk_rows]
    lines.extend(
        [
            f"Top {len(shown)} of {data.risk_total} by severity.",
            "",
            "|severity|affected_entity|risk_type|name|evidence|",
            "|---|---|---|---|---|",
        ]
    )
    lines.extend("|" + "|".join(row) + "|" for row in shown)
    if data.risk_total > len(shown):
        lines.append("")
        lines.append(
            f"+{data.risk_total - len(shown)} further risks in `known_risks.md`."
        )
    lines.append("")
    return lines


def _render_pointers(data: _Aggregate) -> list[str]:
    return [
        "## Evidence Of Record",
        "",
        "This file is an index. The complete, unaggregated rows live in the "
        "sibling artifacts below -- cite those, and read them with the "
        "artifact tools when a specific row matters:",
        "",
        (
            f"- `dependency_map.md` -- all {data.dependency_rows} dependency rows, "
            "grouped into one `##` section per dependency_type."
        ),
        (
            f"- `known_risks.md` -- all {data.risk_total} known-risk rows with "
            "severity, remediation and evidence."
        ),
        "- `config_runtime_map.md` -- all "
        f"{dict(data.counts).get('config/runtime facts', 0)} runtime and "
        "configuration facts (connection hosts, ports, databases, context values).",
        "",
        "Absence of a row in this aggregate is NOT evidence that the fact does "
        "not exist; it means the row was aggregated. Do not report a fact as "
        "unavailable on the strength of this file alone.",
        "",
    ]


def _confidence(value: float) -> str:
    return f"{value:.2f}"


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _ref_label(refs: Iterable[EvidenceRef]) -> str:
    items = list(refs)
    if not items:
        return ""
    first = items[0]
    label = str(first.path or "")
    if first.line:
        label += f":{first.line}"
    if len(items) > 1:
        label += f" (+{len(items) - 1})"
    return label


# --------------------------------------------------------------------------
# Flat / grouped fact tables
# --------------------------------------------------------------------------


def _table(
    title: str,
    values: list[BaseModel],
    columns: list[str],
    *,
    group_by: str | None = None,
    group_first: str | None = None,
) -> str:
    """Render one fact map.

    Without ``group_by`` this is the historical single flat table. With it,
    rows are emitted under one ``## <value>`` heading per distinct value of
    that column, each section repeating the column header. The columns
    themselves are unchanged in both modes, so every consumer that filters on
    ``|`` rows or on ``#`` headings sees the same cells it saw before.

    The grouping exists for Phase 3: ``segment_markdown`` splits on H2
    boundaries first, and a map with a single ``#`` and one 1.75 MB table has
    no boundary to split on -- it degrades to blind line-splitting and the
    file is dropped whole.

    ``phase3_context._markdown_table_rows`` used to take the FIRST pipe line of
    a whole file as the header and every later pipe line as data, so enabling
    ``group_by`` for a map that function reads would have turned the repeated
    per-section headers into bogus rows. It is now section-aware (each ``##``
    block is parsed against its own header), so grouping is safe for any map.
    Grouping is still opt-in per artifact: only ``change_impact_matrix``'s view
    has its bytes pinned by an acceptance test, and turning grouping on for it
    would change those bytes.
    """
    all_columns = columns + ["evidence"]
    header = "|" + "|".join(all_columns) + "|"
    separator = "|" + "|".join("---" for _ in all_columns) + "|"
    dumped_rows = [value.model_dump(mode="json") for value in values]

    if not group_by:
        lines = [f"# {title}", "", header, separator]
        lines.extend(_row(dumped, columns) for dumped in dumped_rows)
        lines.append("")
        return "\n".join(lines)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for dumped in dumped_rows:
        key = _heading_key(dumped.get(group_by))
        grouped.setdefault(key, []).append(dumped)

    lines = [f"# {title}", ""]
    for key in _group_order(grouped.keys(), group_first):
        rows = grouped[key]
        lines.extend([f"## {key}", "", f"{len(rows)} rows.", "", header, separator])
        lines.extend(_row(dumped, columns) for dumped in rows)
        lines.append("")
    return "\n".join(lines)


def _heading_key(value: Any) -> str:
    """A section heading must stay one line and must not open a table row."""
    text = str(value or "").replace("\n", " ").replace("\r", " ").replace("|", "/").strip()
    return text[:120] or "(unspecified)"


def _group_order(keys: Iterable[str], group_first: str | None) -> list[str]:
    """Fixed section order: ``group_first`` (when present), then alphabetical."""
    ordered = sorted(keys, key=lambda key: (key.casefold(), key))
    if group_first and group_first in ordered:
        ordered.remove(group_first)
        ordered.insert(0, group_first)
    return ordered


def _row(dumped: dict[str, Any], columns: list[str]) -> str:
    row = [_cell(dumped.get(column, "")) for column in columns]
    row.append(_cell(_evidence_label(dumped.get("evidence") or [])))
    return "|" + "|".join(row) + "|"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text[:240]


def _evidence_label(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return ""
    first = evidence[0]
    label = str(first.get("path") or "")
    if first.get("line"):
        label += f":{first['line']}"
    if len(evidence) > 1:
        label += f" (+{len(evidence) - 1})"
    return label
