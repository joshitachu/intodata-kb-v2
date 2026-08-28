"""Deterministic, bounded evidence views for Phase 3 narrative documents.

WHY THIS MODULE EXISTS
----------------------
Group-level Phase 3 documents used to receive the RAW derived fact maps --
a 1.5 MB ``dependency_map.md``, a 429 KB ``known_risks.md``, a 198 KB
``config_runtime_map.md`` -- and the context planner cut them to fit. That cut
is a BYTE PREFIX chosen by ``phase3_slices._plan_single_call``: on a verified
production run ``00_AGENT_BRIEF`` received a 61,560-byte prefix of a 1,758,306
byte dependency map and a 356-byte prefix of a 428,565 byte risk register. A
356-byte prefix of a risk register is worse than useless -- it is one severity's
worth of alphabetically-first rows presented as if it were the risk picture.

So the reduction is moved UPSTREAM of the planner and made SEMANTIC. Every
derived map has a registered view builder; ``build_fact_map_view`` is consulted
before a path is ever appended to ``context_files``, so no raw map above its
registered cap reaches a group-level document at all.

CONTRACTS EVERY VIEW MUST HONOUR
--------------------------------
* **Deterministic Python, no LLM.** Byte-stable across runs: every ordering has
  an explicit tie-breaker and nothing derives from set/dict iteration order or
  from a timestamp. Provider prefix caching keys on byte-stable context.
* **A view says it is a view.** Each one names the artifact of record in its own
  text and states that absence from the view is not absence from the project --
  the same contract as ``phase3_slices._full_omission_note``. (The one
  exception is the change-impact view, whose bytes are pinned; see
  ``_CHANGE_IMPACT``.)
* **No double coverage.** ``engineering_facts.md`` is already a real aggregate
  (complete ``calls_job`` edge list, external-system rollup, degree rankings,
  complete per-type census, severity census). A view that repeats those numbers
  spends budget to tell the writer something it has already been told. Each view
  below documents what it uniquely contributes.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from mcp_server.config import config

logger = logging.getLogger(__name__)

# A view is written next to its siblings under this name so it can never be
# mistaken for the artifact of record. `dependency_map.compact.md` in a prompt
# is self-evidently not `dependency_map.md`.
_VIEW_SUFFIX = ".compact.md"

_H2_RE = re.compile(r"^## (.*)$", re.MULTILINE)
_ROW_COUNT_RE = re.compile(r"^(\d+) rows\.$")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactMapView:
    """One derived map's reduction contract.

    ``builder`` is called only when the source exceeds ``max_bytes()``; it must
    return a path (its own output, or the source when it cannot reduce).
    ``max_bytes`` is a callable so the cap is read from live config at call
    time rather than frozen at import.
    """

    builder: Callable[[Path, Path, int], Path]
    max_bytes: Callable[[], int]


def _shared_cap() -> int:
    """Default cap: small enough to stay SHARED across Phase 3 slices.

    ``plan_phase3_slices`` classifies a context file as SHARED (replicated
    byte-stable into every slice) purely by size against
    ``phase3_shared_context_file_bytes``. One byte over and the view is
    bin-packed as EVIDENCE -- slice 1 and slice 3 of one document would then
    see different halves of the same reduction, which is precisely the defect
    this module removes.
    """
    return config.phase3_fact_view_bytes


def _change_impact_cap() -> int:
    return config.phase3_change_impact_view_bytes


def _invariant_cap() -> int:
    """Cap for artifacts with no registered semantic view.

    This is the INVARIANT, not the target: a group-level document must never
    receive a raw artifact above the SHARED-context threshold. Artifacts that
    are already bounded aggregates (`engineering_facts.md` is capped at 15,500
    by its writer; `cross_project_refs.md` is a small deterministic census)
    therefore pass through untouched and keep their cache-stable identity,
    while anything genuinely oversized still gets reduced.
    """
    return config.phase3_shared_context_file_bytes


def build_fact_map_view(source: Path, output_dir: Path, *, max_bytes: int | None = None) -> Path:
    """Return the bounded view of ``source``, or ``source`` when none is needed.

    This is the single seam the harness consults. Unregistered files fall back
    to a whole-line head view, so the "no raw map over the cap" invariant holds
    for artifacts nobody has written a semantic view for yet.
    """
    spec = FACT_MAP_VIEWS.get(source.name)
    cap = max_bytes if max_bytes is not None else (spec.max_bytes() if spec else _invariant_cap())
    if cap <= 0:
        return source
    try:
        size = source.stat().st_size
    except OSError:
        return source
    if size <= cap:
        return source
    builder = spec.builder if spec else _head_view
    try:
        view = builder(source, output_dir, cap)
        # BACKSTOP. Every builder declines when it cannot parse its input
        # (`if not rows: return source`), and a fact map can arrive in an
        # unexpected shape -- a staging directory written by an older release,
        # a pack's own artifact reusing a registered name. Declining must not
        # mean handing a document the raw megabyte, so the invariant is enforced
        # here rather than trusted to each builder.
        if view == source and source.stat().st_size > _invariant_cap():
            logger.warning(
                "phase3 fact view for %s could not parse its input; "
                "falling back to a bounded head view",
                source.name,
            )
            return _head_view(source, output_dir, _invariant_cap())
        return view
    except OSError:
        logger.warning("phase3 fact view could not be written for %s", source.name)
        return source


# ---------------------------------------------------------------------------
# Grouped-table view (the generic builder)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Column:
    """One column of the rendered view table.

    ``role`` is ``group`` (echo a group-key component), ``sample`` (a bounded,
    sorted sample of the distinct values seen in that group) or ``count`` (the
    number of source rows the group collapses).
    """

    label: str
    source: str
    role: str
    limit: int = 0


@dataclass(frozen=True)
class _GroupedSpec:
    """How to collapse one pipe-table fact map into grouped evidence."""

    title: str
    # (column, default when the cell is blank) -- the group key, in order.
    group_columns: tuple[tuple[str, str], ...]
    columns: tuple[_Column, ...]
    # ("- <label>: <n>") header bullets counting distinct values of a column.
    distinct_counts: tuple[tuple[str, str], ...]
    group_count_label: str
    group_noun: str
    usage: str
    # Extra reduction banner lines. EMPTY for the change-impact view: its bytes
    # are pinned by an acceptance test and cannot gain a banner.
    notice: tuple[str, ...] = ()


def _grouped_table_view(spec: _GroupedSpec, source: Path, output_dir: Path, max_bytes: int) -> Path:
    rows = _markdown_table_rows(source.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        return source

    sample_columns = tuple(column.source for column in spec.columns if column.role == "sample")
    grouped: dict[tuple[str, ...], dict[str, Any]] = defaultdict(
        lambda: {name: set() for name in sample_columns} | {"rows": 0}
    )
    for row in rows:
        key = tuple(
            row.get(column, "").strip() or default for column, default in spec.group_columns
        )
        group = grouped[key]
        for name in sample_columns:
            _add(group[name], row.get(name, ""))
        group["rows"] = int(group["rows"]) + 1

    ordered_groups = sorted(grouped.items(), key=_group_sort_key)
    lines = [
        spec.title,
        "",
        f"- Evidence of record: `{source.name}`",
        f"- Source rows: {len(rows)}",
    ]
    lines.extend(
        f"- {label}: {len({row.get(column, '').strip() for row in rows if row.get(column, '').strip()})}"
        for label, column in spec.distinct_counts
    )
    lines.append(f"- {spec.group_count_label}: {len(ordered_groups)}")
    lines.append(f"- Usage: {spec.usage}")
    lines.append("")
    if spec.notice:
        lines.extend(spec.notice)
        lines.append("")
    lines.append("| " + " | ".join(column.label for column in spec.columns) + " |")
    lines.append(
        "|" + "|".join("---:" if column.role == "count" else "---" for column in spec.columns) + "|"
    )

    included = 0
    for key, group in ordered_groups:
        cells: list[str] = []
        group_index = 0
        for column in spec.columns:
            if column.role == "group":
                cells.append(_cell(key[group_index]))
                group_index += 1
            elif column.role == "sample":
                cells.append(_cell(_bounded_values(group[column.source], column.limit)))
            else:
                cells.append(str(group["rows"]))
        candidate = "|".join(("", *cells, ""))
        projected = "\n".join((*lines, candidate, "")).encode("utf-8")
        if len(projected) > max_bytes:
            break
        lines.append(candidate)
        included += 1

    omitted = len(ordered_groups) - included
    if omitted:
        omitted_rows = sum(int(group["rows"]) for _, group in ordered_groups[included:])
        lines.extend(
            (
                "",
                f"> {omitted} lower-frequency {spec.group_noun} groups are not expanded in this "
                f"bounded view. Their {omitted_rows} rows remain in `{source.name}`.",
            )
        )
    return _write_view(source, output_dir, "\n".join(lines).rstrip() + "\n")


def _group_sort_key(item: tuple[tuple[str, ...], dict[str, Any]]) -> tuple[Any, ...]:
    """Largest groups first; then the first key component case-insensitively.

    The remaining components break ties verbatim. This reproduces the ordering
    the change-impact view has always used, which is why the byte-identity test
    passes.
    """
    key, group = item
    return (-int(group["rows"]), key[0].casefold(), *key[1:])


# ---------------------------------------------------------------------------
# change_impact_matrix.md
# ---------------------------------------------------------------------------

# BYTES PINNED. `tests/test_providers/test_phase3_context.py` asserts this view
# is byte-identical to the pre-registry `compact_change_impact_matrix`
# implementation on real production matrices. Do not add a banner, reorder a
# column, or change a label without regenerating that pin deliberately -- the
# 08_CHANGE_PLAYBOOK prompt prefix is cached on these bytes.
_CHANGE_IMPACT = _GroupedSpec(
    title="# Compact Change Impact Evidence View",
    group_columns=(
        ("check", "Review the supplied change evidence."),
        ("confidence", "UNKNOWN"),
    ),
    columns=(
        _Column("Review check", "check", "group"),
        _Column("Change targets (examples)", "changed_entity", "sample", 10),
        _Column("Affected entities (examples)", "impacted_entity", "sample", 14),
        _Column("Evidence examples", "evidence", "sample", 4),
        _Column("Confidence", "confidence", "group"),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Unique change targets", "changed_entity"),
        ("Unique affected entities", "impacted_entity"),
    ),
    group_count_label="Grouped review checks",
    group_noun="review-check",
    usage="synthesize a prioritized playbook; do not reproduce the full matrix.",
)


def compact_change_impact_matrix(
    source: Path,
    output_dir: Path,
    *,
    max_bytes: int,
) -> Path:
    """Return a bounded, grouped view of a large change-impact Markdown table.

    Retained as a named entry point because 08_CHANGE_PLAYBOOK's bytes are
    pinned to it; the implementation now lives in the registry.
    """
    if max_bytes <= 0 or source.stat().st_size <= max_bytes:
        return source
    return _grouped_table_view(_CHANGE_IMPACT, source, output_dir, max_bytes)


# ---------------------------------------------------------------------------
# dependency_map.md -- section-aware, deliberately NOT a second aggregate
# ---------------------------------------------------------------------------

# `engineering_facts.md` already carries, in full: the complete `calls_job`
# edge list, the external-system rollup with usage counts, out/in degree
# rankings, and the complete per-dependency_type census (rows / distinct
# sources / distinct targets). Re-deriving any of that here would spend the
# document's budget restating what the writer has already been given.
#
# The one thing those aggregates structurally cannot show is what a row of a
# given relation LOOKS like. That is this view's entire job.
_DEPENDENCY_CALLS_JOB = "calls_job"
_DEPENDENCY_ROWS_PER_TYPE = 6
_DEPENDENCY_FOOTER_RESERVE = 320


def _dependency_map_view(source: Path, output_dir: Path, max_bytes: int) -> Path:
    text = source.read_text(encoding="utf-8", errors="replace")
    sections = _markdown_sections(text) or _flat_dependency_sections(text)
    if not sections:
        return source

    first_header = sections[0].header
    columns = (
        "`" + "` | `".join(_split_row(first_header[0])) + "`"
        if first_header
        else "as written in the artifact of record"
    )
    header = [
        "# Dependency Map -- Bounded Sample View",
        "",
        f"> REDUCTION: this is a deterministic sample of `{source.name}` "
        f"({source.stat().st_size} bytes, "
        f"{sum(section.row_count for section in sections)} rows over "
        f"{len(sections)} dependency types). `{source.name}` is the artifact of "
        "record and is readable in full with the artifact tools.",
        ">",
        "> Aggregate counts are NOT repeated here. `engineering_facts.md` already "
        f"carries the complete per-type census, the complete `{_DEPENDENCY_CALLS_JOB}` "
        "edge list, the external-system rollup and the degree rankings. This view "
        "adds only what an aggregate cannot show: verbatim example rows per "
        "dependency type, so the shape of each relation is visible.",
        ">",
        "> Absence of a row here is NOT evidence that the dependency does not "
        "exist; it means the row was sampled out. Do not report a dependency as "
        "absent on the strength of this file.",
        "",
        "## Sampled Rows",
        "",
        "Every row below is verbatim from the artifact of record, in its "
        f"original order. Columns, stated once for all sections: {columns}.",
        "",
    ]

    ordered = sorted(
        sections, key=lambda section: (-section.row_count, section.name.casefold(), section.name)
    )
    # The trailing "types that did not fit" note is written last but reserved
    # first, so it can never be the line that overruns the cap.
    used = _line_bytes(header) + _DEPENDENCY_FOOTER_RESERVE
    if used >= max_bytes:
        return source

    # Every type earns a heading before any type earns a second row: a reader
    # must be able to see that `uses_routine` exists even when its 6,869 rows
    # are unaffordable.
    kept: dict[str, list[str]] = {}
    order: list[_Section] = []
    for section in ordered:
        cost = _line_bytes(_dependency_block(section, []))
        if used + cost > max_bytes:
            break
        used += cost
        kept[section.name] = []
        order.append(section)

    # Round-robin so no single relation eats the sample allowance: on a real
    # 36-type map `uses_routine` (6,869 rows) would otherwise take every row
    # slot and 35 relations would show nothing.
    for depth in range(_DEPENDENCY_ROWS_PER_TYPE):
        progressed = False
        for section in order:
            if section.name == _DEPENDENCY_CALLS_JOB:
                continue  # complete in engineering_facts.md; sampling it is waste
            if depth >= len(section.rows):
                continue
            cost = _line_bytes([section.rows[depth]])
            if used + cost > max_bytes:
                continue
            used += cost
            kept[section.name].append(section.rows[depth])
            progressed = True
        if not progressed:
            break

    while True:
        lines = list(header)
        for section in order:
            lines.extend(_dependency_block(section, kept[section.name]))
        dropped = len(sections) - len(order)
        if dropped:
            dropped_rows = sum(section.row_count for section in ordered[len(order):])
            lines.extend(
                (
                    f"> {dropped} further dependency types ({dropped_rows} rows) did not "
                    f"fit this view; they are listed with exact counts in "
                    f"`engineering_facts.md` under `## Dependency Rows By Type` and "
                    f"their rows are in `{source.name}`.",
                    "",
                )
            )
        text = "\n".join(lines).rstrip() + "\n"
        # Last-resort guarantee. The greedy accounting above is conservative,
        # but the cap is a contract (one byte over and `plan_phase3_slices`
        # reclassifies the view from SHARED to splittable), so it is enforced
        # rather than assumed.
        if _size(text) <= max_bytes or not order:
            break
        order.pop()
    return _write_view(source, output_dir, text)


def _flat_dependency_sections(text: str) -> list[_Section]:
    """Recover per-type sections from a FLAT dependency map.

    ``markdown_writer`` only started emitting one ``##`` section per
    dependency_type recently; a staging directory written by an earlier release
    (or resumed into a newer one) still carries a single 1.5 MB table. Grouping
    it here keeps the same view for both shapes instead of declining and
    letting the raw map through.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3 or not _is_separator(lines[1]):
        return []
    headers = _split_row(lines[0])
    if "dependency_type" not in headers:
        return []
    index = headers.index("dependency_type")
    grouped: dict[str, list[str]] = {}
    for line in lines[2:]:
        cells = _split_row(line)
        if len(cells) != len(headers):
            continue
        grouped.setdefault(cells[index] or "(unspecified)", []).append(line)
    return [
        _Section(
            name=name,
            subtitle="",
            header=(lines[0], lines[1]),
            rows=tuple(rows),
            row_count=len(rows),
        )
        for name, rows in sorted(grouped.items(), key=lambda item: (item[0].casefold(), item[0]))
    ]


def _dependency_block(section: _Section, rows: list[str]) -> list[str]:
    """One type's sample.

    The column header is deliberately NOT repeated per section -- it is stated
    once in the view preamble. Repeating it (and a per-section "+N further rows"
    footer) across 36 dependency types cost ~3.7 KB of a 12 KB view, which is
    ~40 sampled rows spent restating the same five column names.
    """
    lines = [f"### {section.name} -- {len(rows)} of {section.row_count} rows", ""]
    if section.name == _DEPENDENCY_CALLS_JOB:
        lines.extend(
            [
                "Not sampled here on purpose: `engineering_facts.md` reproduces this "
                "relation's COMPLETE edge list in its Job Call Graph section. "
                "Read it there.",
                "",
            ]
        )
        return lines
    if not rows:
        lines.extend(["Rows omitted at this view's byte cap.", ""])
        return lines
    lines.extend(rows)
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# sql_usage.md -- record index + table census, verbatim SQL dropped
# ---------------------------------------------------------------------------

_SQL_FOOTER_RESERVE = 320


def _sql_usage_view(source: Path, output_dir: Path, max_bytes: int) -> Path:
    """Reduce the SQL usage register to its index.

    The bulk of ``sql_usage.md`` is the ```sql excerpt under every record. The
    excerpts are the reason to open the artifact of record, not something to
    prefix-truncate: this view keeps every record's identity, scope and table
    list, and says where the statements are.
    """
    sections = _markdown_sections(source.read_text(encoding="utf-8", errors="replace"))
    if not sections:
        return source
    if max_bytes <= _SQL_FOOTER_RESERVE:
        return source

    tables: dict[str, int] = {}
    for section in sections:
        for row in section.rows:
            cells = _split_row(row)
            if cells and cells[0]:
                tables[cells[0]] = tables.get(cells[0], 0) + 1

    lines = [
        "# SQL Usage -- Bounded Index View",
        "",
        f"> REDUCTION: index of `{source.name}` ({source.stat().st_size} bytes, "
        f"{len(sections)} records, {len(tables)} distinct tables). The verbatim "
        f"SQL statement of every record is in `{source.name}`, the artifact of "
        "record; it is omitted here, not missing. Do not report a statement as "
        "unavailable on the strength of this file.",
        "",
        "## Records",
        "",
        "|record|scope|tables|",
        "|---|---|---|",
    ]
    record_lines = [
        "|"
        + _cell(section.name)
        + "|"
        + _cell(section.subtitle)
        + "|"
        + _cell(
            _bounded_values({_split_row(row)[0] for row in section.rows if _split_row(row)}, 12)
        )
        + "|"
        for section in sections
    ]
    census_header = ["", "## Table Census", "", "|table|records|", "|---|---:|"]
    census_lines = [
        f"|{_cell(name)}|{count}|"
        for name, count in sorted(
            tables.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
    ]

    # Half the remaining budget to each half of the index, so a project with
    # thousands of records still shows its table census and vice versa.
    budget = max_bytes - _line_bytes(lines + census_header) - _SQL_FOOTER_RESERVE
    kept_records = _take_while_affordable(record_lines, budget // 2)
    budget -= _line_bytes(kept_records)
    kept_census = _take_while_affordable(census_lines, budget)

    lines.extend(kept_records)
    if len(kept_records) < len(record_lines):
        lines.extend(
            ("", f"> +{len(record_lines) - len(kept_records)} further records in `{source.name}`.")
        )
    lines.extend(census_header)
    lines.extend(kept_census)
    if len(kept_census) < len(census_lines):
        lines.extend(
            ("", f"> +{len(census_lines) - len(kept_census)} further tables in `{source.name}`.")
        )
    return _write_view(source, output_dir, "\n".join(lines).rstrip() + "\n")


def _take_while_affordable(lines: Sequence[str], budget: int) -> list[str]:
    kept: list[str] = []
    for line in lines:
        cost = _line_bytes([line])
        if cost > budget:
            break
        budget -= cost
        kept.append(line)
    return kept


# ---------------------------------------------------------------------------
# Generic fallback for anything without a registered semantic view
# ---------------------------------------------------------------------------


def _head_view(source: Path, output_dir: Path, max_bytes: int) -> Path:
    """Whole-line head of an unregistered artifact, with the reduction stated.

    Deliberately dumb -- it exists so the "no raw map over the cap" invariant
    holds for a map nobody has written a semantic view for yet. Any artifact
    that shows up here regularly deserves a registry entry.
    """
    notice = (
        f"> REDUCTION: the first whole lines of `{source.name}` "
        f"({source.stat().st_size} bytes) only. `{source.name}` is the artifact "
        "of record and is readable in full with the artifact tools. Content "
        "absent here is absent from THIS VIEW, not from the project.\n\n"
    )
    budget = max_bytes - _size(notice)
    if budget <= 0:
        return source
    kept: list[str] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
        cost = _size(line)
        if cost > budget:
            break
        budget -= cost
        kept.append(line)
    return _write_view(source, output_dir, notice + "".join(kept))


# ---------------------------------------------------------------------------
# Registry contents
# ---------------------------------------------------------------------------


def _grouped(spec: _GroupedSpec) -> Callable[[Path, Path, int], Path]:
    def build(source: Path, output_dir: Path, max_bytes: int) -> Path:
        return _grouped_table_view(spec, source, output_dir, max_bytes)

    return build


_REDUCTION_NOTICE = (
    "> REDUCTION: rows are grouped, not reproduced. The artifact named above is "
    "the evidence of record and is readable in full with the artifact tools. A "
    "value absent from this view is absent from THIS VIEW, not from the project.",
)

_KNOWN_RISKS = _GroupedSpec(
    title="# Compact Known Risks Evidence View",
    group_columns=(("risk_type", "(unclassified)"), ("severity", "UNKNOWN")),
    columns=(
        _Column("Risk type", "risk_type", "group"),
        _Column("Severity", "severity", "group"),
        _Column("Affected entities (examples)", "affected_entity", "sample", 12),
        _Column("Findings (examples)", "name", "sample", 4),
        _Column("Remediation (examples)", "remediation", "sample", 2),
        _Column("Evidence examples", "evidence", "sample", 2),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Distinct affected entities", "affected_entity"),
        ("Distinct risk types", "risk_type"),
    ),
    group_count_label="Grouped risk classes",
    group_noun="risk-class",
    usage=(
        "report the risk classes and their named entities; the per-row severity "
        "census is already complete in `engineering_facts.md`."
    ),
    notice=_REDUCTION_NOTICE,
)

_CONFIG_RUNTIME = _GroupedSpec(
    title="# Compact Config/Runtime Evidence View",
    group_columns=(("config_type", "(unspecified)"), ("key", "(no key)")),
    columns=(
        _Column("Config type", "config_type", "group"),
        _Column("Key", "key", "group"),
        _Column("Names (examples)", "name", "sample", 12),
        _Column("Value hints (examples)", "value_hint", "sample", 10),
        _Column("Evidence examples", "evidence", "sample", 3),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Distinct config types", "config_type"),
        ("Distinct keys", "key"),
        ("Distinct names", "name"),
    ),
    group_count_label="Grouped configuration classes",
    group_noun="configuration-class",
    usage=(
        "name the configuration classes, keys and observed value hints; never "
        "quote a value hint as a credential."
    ),
    notice=_REDUCTION_NOTICE,
)

_DATA_FLOW = _GroupedSpec(
    title="# Compact Data Flow Evidence View",
    group_columns=(("transformation", "(untransformed)"), ("confidence", "UNKNOWN")),
    columns=(
        _Column("Transformation", "transformation", "group"),
        _Column("Sources (examples)", "source", "sample", 12),
        _Column("Targets (examples)", "target", "sample", 12),
        _Column("Schema hints (examples)", "schema_hint", "sample", 6),
        _Column("Evidence examples", "evidence", "sample", 3),
        _Column("Confidence", "confidence", "group"),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Distinct flow sources", "source"),
        ("Distinct flow targets", "target"),
    ),
    group_count_label="Grouped transformations",
    group_noun="transformation",
    usage="describe lineage by transformation class; cite the map for a specific edge.",
    notice=_REDUCTION_NOTICE,
)

_ARCHITECTURE = _GroupedSpec(
    title="# Compact Architecture Evidence View",
    group_columns=(("layer", "(unassigned layer)"), ("boundary", "(no boundary)")),
    columns=(
        _Column("Layer", "layer", "group"),
        _Column("Boundary", "boundary", "group"),
        _Column("Names (examples)", "name", "sample", 12),
        _Column("Components (examples)", "component", "sample", 12),
        _Column("Evidence examples", "evidence", "sample", 3),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Distinct layers", "layer"),
        ("Distinct components", "component"),
    ),
    group_count_label="Grouped layer/boundary pairs",
    group_noun="layer-boundary",
    usage="describe topology by layer and boundary; cite the map for a specific node.",
    notice=_REDUCTION_NOTICE,
)

_TEST_COVERAGE = _GroupedSpec(
    title="# Compact Test Coverage Evidence View",
    group_columns=(("gap", "(no gap recorded)"), ("confidence", "UNKNOWN")),
    columns=(
        _Column("Coverage gap", "gap", "group"),
        _Column("Test files (examples)", "test_file", "sample", 8),
        _Column("Covered entities (examples)", "covered_entity", "sample", 12),
        _Column("Suggested scenarios (examples)", "suggested_scenario", "sample", 3),
        _Column("Evidence examples", "evidence", "sample", 3),
        _Column("Confidence", "confidence", "group"),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(
        ("Distinct test files", "test_file"),
        ("Distinct covered entities", "covered_entity"),
    ),
    group_count_label="Grouped coverage gaps",
    group_noun="coverage-gap",
    usage="report coverage gaps by class; cite the map for a specific entity.",
    notice=_REDUCTION_NOTICE,
)

_API_SURFACE = _GroupedSpec(
    title="# Compact API Surface Evidence View",
    group_columns=(("method", "(unspecified method)"), ("confidence", "UNKNOWN")),
    columns=(
        _Column("Method", "method", "group"),
        _Column("Routes (examples)", "route", "sample", 14),
        _Column("Handlers (examples)", "handler", "sample", 10),
        _Column("Names (examples)", "name", "sample", 6),
        _Column("Evidence examples", "evidence", "sample", 3),
        _Column("Confidence", "confidence", "group"),
        _Column("Source rows", "", "count"),
    ),
    distinct_counts=(("Distinct routes", "route"), ("Distinct handlers", "handler")),
    group_count_label="Grouped methods",
    group_noun="method",
    usage="enumerate the exposed surface by method; cite the map for a specific route.",
    notice=_REDUCTION_NOTICE,
)


FACT_MAP_VIEWS: dict[str, FactMapView] = {
    "change_impact_matrix.md": FactMapView(
        _grouped(_CHANGE_IMPACT), _change_impact_cap
    ),
    "dependency_map.md": FactMapView(_dependency_map_view, _shared_cap),
    "sql_usage.md": FactMapView(_sql_usage_view, _shared_cap),
    "known_risks.md": FactMapView(_grouped(_KNOWN_RISKS), _shared_cap),
    "config_runtime_map.md": FactMapView(_grouped(_CONFIG_RUNTIME), _shared_cap),
    "data_flow_map.md": FactMapView(_grouped(_DATA_FLOW), _shared_cap),
    "architecture_map.md": FactMapView(_grouped(_ARCHITECTURE), _shared_cap),
    "test_coverage_map.md": FactMapView(_grouped(_TEST_COVERAGE), _shared_cap),
    "api_surface.md": FactMapView(_grouped(_API_SURFACE), _shared_cap),
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Section:
    """One ``## <name>`` block of a grouped markdown fact map."""

    name: str
    subtitle: str
    header: tuple[str, ...]
    rows: tuple[str, ...]
    row_count: int


def _markdown_sections(text: str) -> list[_Section]:
    """Split a grouped fact map into its ``##`` sections.

    ``markdown_writer._table`` emits ``## <dependency_type>`` / ``N rows.`` /
    column header / separator / rows. ``sql_usage._markdown`` emits
    ``## <id> - <unit>`` / a ``Source: ...`` line / a table / a ```sql block.
    Both shapes parse here: the first pipe line and its separator are the
    section's own header, the remaining pipe lines are its rows.
    """
    matches = list(_H2_RE.finditer(text))
    if not matches:
        return []
    sections: list[_Section] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end(): end]
        name = match.group(1).strip()
        subtitle = ""
        declared = 0
        header: list[str] = []
        rows: list[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                if len(header) < 2:
                    header.append(stripped)
                elif not _is_separator(stripped):
                    rows.append(stripped)
                continue
            if stripped.startswith("```"):
                break  # a fenced block ends the structured part of the section
            count = _ROW_COUNT_RE.match(stripped)
            if count:
                declared = int(count.group(1))
            elif not subtitle:
                subtitle = stripped
        if len(header) < 2 or not _is_separator(header[1]):
            header = []
        sections.append(
            _Section(
                name=name,
                subtitle=subtitle,
                header=tuple(header),
                rows=tuple(rows),
                row_count=declared or len(rows),
            )
        )
    return sections


def _markdown_table_rows(text: str) -> list[dict[str, str]]:
    """Parse every pipe table in ``text`` into dicts keyed by its own header.

    Previously this took the FIRST pipe line of the whole file as the header and
    every later pipe line as data, so a grouped map (repeated per-section
    headers, see ``markdown_writer._table``) produced bogus rows. It is now
    section-aware: a file with no ``##`` heading parses exactly as before, which
    is what keeps the change-impact view byte-identical.
    """
    chunks = _H2_RE.split(text)
    blocks = [chunks[0]] + chunks[2::2] if len(chunks) > 1 else [text]
    rows: list[dict[str, str]] = []
    for block in blocks:
        rows.extend(_table_block_rows(block))
    return rows


def _table_block_rows(text: str) -> list[dict[str, str]]:
    table_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 3:
        return []
    headers = _split_row(table_lines[0])
    separator = _split_row(table_lines[1])
    if not headers or not all(set(cell) <= {"-", ":", " "} for cell in separator):
        return []
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        values = _split_row(line)
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values, strict=True)))
    return rows


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(set(cell) <= {"-", ":", " "} for cell in cells)


def _split_row(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]


def _add(values: set[str] | int, value: str) -> None:
    if isinstance(values, set) and value.strip():
        values.add(value.strip())


def _bounded_values(values: Iterable[str] | int, limit: int) -> str:
    """Bounded, byte-stable sample of a set of cell values.

    The tie-breaker is load-bearing. This sorted a SET with `key=str.casefold`
    alone, and `sorted` is stable, so two values that casefold equal
    (`VISE2AssetCheck` / `Vise2AssetCheck` are both real, separately-scanned
    werkorder groups) kept SET ITERATION ORDER -- which varies with string hash
    randomisation from one process to the next. Measured on the Assetcheck
    change-impact matrix: four runs of the identical input produced three
    different 47,760-byte views. That is a silent prompt-cache miss on every
    08_CHANGE_PLAYBOOK call, in the one module whose whole contract is
    byte-stability. The verbatim value breaks the tie.
    """
    if not isinstance(values, (set, frozenset)):
        return ""
    ordered = sorted(values, key=lambda value: (value.casefold(), value))
    suffix = f"; +{len(ordered) - limit} more" if len(ordered) > limit else ""
    return "; ".join(ordered[:limit]) + suffix


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _line_bytes(lines: Sequence[str]) -> int:
    """Cost of appending ``lines`` to a newline-joined document."""
    return sum(_size(line) + 1 for line in lines)


def _write_view(source: Path, output_dir: Path, content: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{source.stem}{_VIEW_SUFFIX}"
    output.write_text(content, encoding="utf-8")
    return output
