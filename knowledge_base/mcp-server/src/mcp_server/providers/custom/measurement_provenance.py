"""Deterministic provenance check for published RUNTIME measurements.

A documentation run can now query the live runtime estate (the Talend TMC
execution mirror and an Elasticsearch log estate) while it writes. That turns a
class of statement that used to be impossible -- "this interface failed 785 of
3,392 runs" -- into a statement the model can make casually, and a runtime
number without its measurement reference is not a fact, it is a coin flip:

* The SAME estate reads as 0.087% failing or 30.8% failing depending on which
  register the number covers and what it is divided by -- a factor of 350.
* One verified mirror reports 23.1% failing unscoped, while PROD alone is 0.8%
  and ACC is 60.7%. A rate computed over a register that blends environments
  describes no environment that exists.

So a published runtime figure must carry three things: the register or
aggregate it came from, the time WINDOW it covers, and the DENOMINATOR it was
divided over. This module finds runtime measurement claims in generated
document text and reports the ones that do not.

WHAT IT MATCHES (a "runtime measurement claim")
-----------------------------------------------
Four shapes, and only when the claim's own sentence / table row shows it is
about OBSERVED EXECUTION rather than about the scanned code:

``rate``
    A percentage or a "... rate" figure in a unit that also carries a strong
    runtime term (``executions``, ``job runs``, ``runs``, ``failure rate``,
    ``uptime``, ``latency``, ``of the time``, ``in production``, ...).
``count``
    A number counting executions, runs, job runs, invocations, failures,
    failed runs, incidents, or records/messages/rows *processed*. The noun
    itself is the runtime signal; a number may be separated from it by at most
    two adjectives drawn from a closed list (``total``, ``failed``,
    ``successful``, ``scheduled``, ``PROD`` ...). Also ``ran N times`` --
    the verb must sit immediately before the number, so the scan-derived
    "tRunJob is invoked 4 times across the estate" is not a runtime count.
``duration``
    A number with a time unit in a unit that also carries a statistic term
    (``p95``, ``percentile``, ``median``, ``average``, ``duration``,
    ``latency``, ``elapsed``, ``execution time``).
``cadence``
    ``every N minutes`` / ``N times per day`` in a unit that also carries an
    execution verb (``runs``, ``executes``, ``scheduled``, ``polls``,
    ``triggered``, ...).

Inside a markdown table, a numeric cell is also a claim when its COLUMN HEADER
names a runtime quantity (``Runs``, ``Executions``, ``Failures``, ``Failure
rate``, ``p95``, ``Cadence``) -- a cell's meaning comes from its header, and
most published runtime numbers live in tables.

WHAT IT DELIBERATELY DOES NOT MATCH
-----------------------------------
Ordinary scan-derived prose numbers, because flagging those would make the gate
noise and the noise would get it switched off:

* ``the project has 429 jobs across 20 groups`` -- ``jobs`` and ``groups`` are
  not runtime nouns.
* ``55% of designs are documented`` / ``85% of the runtime configuration values
  are environment-specific`` -- no strong runtime term (``runtime`` immediately
  followed by ``config``/``configuration``/``parameter`` is explicitly NOT a
  runtime term; doc 05 is named 05_RUNTIME_CONFIG_AND_EXTERNALS).
* ``12% of designs fail the naming convention`` -- bare ``fail``/``fails`` is
  not a runtime term; only ``failure rate``, ``failed run(s)``, ``runs`` and
  friends are.
* ``2 points of failure`` -- the count pattern only bridges a closed adjective
  list between the number and the noun.
* ``the job has 4 retries configured`` -- ``retries``/``timeouts``/``restarts``
  are configuration in a scan and were left out of the count nouns.

WHERE THE REFERENCE MAY LIVE (scope)
------------------------------------
Real documents state the window and the register once, in a caption or a
heading, and then publish rows. So a claim is checked against a SCOPE, not just
its own words:

* the nearest preceding markdown heading,
* the preceding block when that block is a short caption (<= 3 lines),
* for a table row: the table's header row(s) and the block text before the
  table -- never a sibling DATA row,
* for prose: the text of its own block up to and including the claim -- never
  text that FOLLOWS it. A bare bullet cannot borrow the window from the
  properly-referenced bullet underneath it.

LEDGER CROSS-CHECK
------------------
``ledger`` takes the parsed entries of ``_assessment/runtime_probe_ledger.jsonl``
-- what the run actually asked the runtime tools. A published figure that no
probe returned is the strongest available signal of fabrication.

``ledger=None`` means "no ledger available, skip the cross-check".
``ledger=[]`` means "the ledger exists and records no usable probe", which is a
different fact: every runtime figure in the text is then unprovenanced. (Same
None-vs-empty distinction the runtime read scope already makes for
``environment_ids``.) Entries that record an error, ``ok``/``success`` false, a
failure status, or ``field_unmapped`` contribute NO values -- a probe that
reported "this field does not exist" must never be what legitimises a published
zero, which is exactly the zero-is-ambiguous failure.

The check has THREE outcomes, not two, because the shipped CLI
(``_tools/runtime_tools.py``) records each result's SHAPE -- "keys and counts,
never values" -- so that ``_assessment/`` never becomes a copy of an
unredacted payload:

* the ledger carries values -> every published figure is checked against them;
* the ledger records successful probes but no values -> the value-level check
  is SKIPPED. Reporting here would accuse a correctly-evidenced document of
  fabrication on the strength of a redaction policy;
* the ledger records no successful probe at all -> every runtime figure in the
  document is unprovenanced, and that is reported. This is the outcome that
  works against the shipped ledger today.

ADVISORY BY CONVENTION
----------------------
Like the other Phase 3 final checks, these errors are meant to surface as
``log_cb`` warnings and as a report under ``_assessment/``; documents ship
as-written and the operator decides. Nothing here raises.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "measurement_provenance_errors",
    "find_runtime_measurement_claims",
    "measurement_reference",
    "ledger_figures",
    "ledger_records_a_probe",
    "MeasurementReference",
    "RuntimeMeasurementClaim",
]

# Cap reported items the way the sibling fabrication validators do: a repair
# prompt that carries 200 errors is not actionable.
_MAX_REPORTED_CLAIMS = 5
_MAX_REPORTED_FIGURES = 8

# --------------------------------------------------------------------------- #
# Number shapes
# --------------------------------------------------------------------------- #

#: A published number, with or without thousands separators. Grouped form first,
#: so "3,392" is never read as "3".
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"

_NUMERIC_STRING_RE = re.compile(rf"^([-+]?{_NUM})\s*%?$")
_NUMERIC_CELL_RE = re.compile(
    rf"^\s*[~<>]?\s*({_NUM})\s*"
    r"(?:%|ms|s|sec|secs|seconds|min|mins|minutes|h|hrs|hours)?\s*$",
    re.IGNORECASE,
)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "").replace(" ", "").strip())


def _decimals(raw: str) -> int:
    _, _, frac = raw.partition(".")
    return len(frac)


# --------------------------------------------------------------------------- #
# Runtime vocabulary
# --------------------------------------------------------------------------- #

#: Terms that mark a unit as being about OBSERVED EXECUTION. Deliberately
#: strong: bare "run", "fail", "fails" and bare "failure" are absent because
#: they occur constantly in static-analysis prose ("designs run without error
#: handling", "a single point of failure", "fails the naming convention").
_RUNTIME_TERM_PATTERNS: tuple[str, ...] = (
    r"executions?",
    r"executed",
    r"execution times?",
    r"job runs?",
    r"runs",
    r"ran",
    r"re-?runs?",
    r"failure rates?",
    r"failed runs?",
    r"success rates?",
    r"error rates?",
    r"uptime",
    r"downtime",
    r"availability",
    r"reliability",
    r"latency",
    r"throughput",
    r"elapsed",
    # "runtime configuration" is a document section, not a measurement.
    r"runtime(?!\s+(?:config|configuration|parameter|variable))",
    r"percentiles?",
    r"p(?:50|75|90|95|99)",
    r"incidents?",
    r"sla",
    r"of the time",
    r"in production",
    r"cadence",
    r"scheduled?",
)
_RUNTIME_TERM_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(_RUNTIME_TERM_PATTERNS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)

#: Nouns whose COUNT is a runtime observation by itself. "retries", "restarts"
#: and "timeouts" are absent on purpose: in a scan they are configuration.
_COUNT_NOUNS = (
    r"(?:job\s+)?runs?",
    r"executions?",
    r"invocations?",
    r"failures?",
    r"failed\s+runs?",
    r"incidents?",
    r"(?:records?|messages?|rows?)\s+processed",
)
#: The only words allowed to sit between the number and the runtime noun. A
#: closed list is what stops "2 points of failure" being read as a failure count.
_COUNT_FILLERS = (
    r"(?:total|distinct|unique|separate|successful|failed|failing|scheduled|"
    r"observed|recorded|production|prod|acc|acceptance|test|tst|dev)\s+"
)
_RUNTIME_COUNT_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_NUM}|zero)\s+(?:{_COUNT_FILLERS}){{0,2}}"
    rf"(?:{'|'.join(_COUNT_NOUNS)})(?![A-Za-z])",
    re.IGNORECASE,
)

#: "ran 2,122 times" is an execution count in the most natural phrasing there is.
#: The verb must sit immediately before the number, which is what keeps the
#: scan-derived "tRunJob is invoked 4 times across the estate" out of it.
_EXECUTION_TIMES_RE = re.compile(
    rf"(?<![A-Za-z])(?:ran|runs|executed|executes)\s+(?:successfully\s+)?({_NUM})"
    r"\s+times(?![A-Za-z])",
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(rf"(?<![A-Za-z0-9])({_NUM})\s*(?:%|per\s?cent\b)", re.IGNORECASE)

_RATE_PHRASE_RE = re.compile(
    rf"(?:failure|success|error|completion)\s+rate\s*(?:of|:|is|was|=)?\s*({_NUM})",
    re.IGNORECASE,
)

_STAT_TERM_RE = re.compile(
    r"(?<![A-Za-z])(?:p(?:50|75|90|95|99)|\d{1,3}(?:st|nd|rd|th)\s+percentile|percentiles?|"
    r"median|average|mean|duration|durations|latency|elapsed|execution times?|"
    r"run times?|slowest|longest|fastest)(?![A-Za-z])",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_NUM})\s*"
    r"(?:ms|milliseconds?|s|secs?|seconds?|mins?|minutes?|hrs?|hours?)(?![A-Za-z])",
    re.IGNORECASE,
)

_EXEC_VERB_RE = re.compile(
    r"(?<![A-Za-z])(?:runs|ran|executes?|executed|scheduled?|triggers?|triggered|"
    r"fires?|polls?|invoked|invokes|cadence|repeats?)(?![A-Za-z])",
    re.IGNORECASE,
)
_CADENCE_RE = re.compile(
    rf"(?<![A-Za-z])every\s+({_NUM})\s*"
    r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)(?![A-Za-z])",
    re.IGNORECASE,
)
_FREQUENCY_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_NUM})\s*(?:x|times)\s*(?:per|a|/)\s*"
    r"(?:second|minute|hour|day|week|month)(?![A-Za-z])",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Measurement reference vocabulary
# --------------------------------------------------------------------------- #

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december"
)
_WINDOW_RE = re.compile(
    "|".join(
        (
            rf"(?:last|past|previous|trailing|preceding)\s+{_NUM}\s*"
            r"(?:calendar\s+)?(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)",
            rf"{_NUM}\s*-?\s*(?:second|minute|hour|day|week|month|year)"
            r"\s*-?\s*(?:window|period|sample|history|horizon|lookback)",
            rf"(?:window|period|sample|lookback|horizon)\s+of\s+{_NUM}\s*"
            r"(?:seconds?|minutes?|hours?|days?|weeks?|months?)",
            rf"over\s+{_NUM}\s*(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)",
            rf"{_NUM}\s*-\s*day",
            r"(?:since|from)\s+\d{4}-\d{2}-\d{2}",
            r"between\s+\d{4}-\d{2}-\d{2}\s+and\s+\d{4}-\d{2}-\d{2}",
            r"\d{4}-\d{2}-\d{2}\s*(?:to|until|[-–—]+)\s*\d{4}-\d{2}-\d{2}",
            rf"(?:in|during|for|across)\s+(?:{_MONTHS})\s+\d{{4}}",
            r"(?:in|during|for|across)\s+(?:Q[1-4]|H[12])\s+\d{4}",
        )
    ),
    re.IGNORECASE,
)

_DENOMINATOR_RE = re.compile(
    "|".join(
        (
            rf"out\s+of\s+{_NUM}",
            rf"of\s+{_NUM}\s+(?:total\s+)?(?:job\s+)?"
            r"(?:runs?|executions?|invocations?|records?|messages?|samples?|tasks?)",
            rf"\bn\s*=\s*{_NUM}",
            rf"(?:across|over|from|based\s+on)\s+{_NUM}\s+(?:total\s+)?(?:job\s+)?"
            r"(?:runs?|executions?|invocations?|samples?)",
            rf"{_NUM}\s+(?:total\s+)?(?:job\s+)?(?:runs?|executions?|invocations?)"
            r"(?![A-Za-z])",
            # The population stated as "ran N times" -- the same sentence shape
            # _EXECUTION_TIMES_RE reads as a count, read here as its denominator.
            rf"(?:ran|runs|executed|executes)\s+(?:successfully\s+)?{_NUM}\s+times",
            r"denominator",
        )
    ),
    re.IGNORECASE,
)

#: A named source of record, in the language a client document may use. Internal
#: table and file names are forbidden in published prose, so these are the
#: business-language forms a writer is actually allowed to publish.
_REGISTER_RE = re.compile(
    "|".join(
        (
            r"\bTMC\b",
            r"Talend\s+Management\s+Console",
            r"Talend\s+Administration\s+Cent(?:er|re)",
            r"\bElastic(?:search)?\b",
            r"\bOpenSearch\b",
            r"\bKibana\b",
            r"(?:execution|job\s+run|run|failure|scheduler)\s+"
            r"(?:history|register|records?|log|store|mirror|archive)",
            r"(?:monitoring|logging|log|search)\s+ind(?:ex|ices)",
            r"index\s+[A-Za-z0-9_.*-]+",
            r"[A-Za-z0-9_.-]+-\*",
            r"runtime\s+(?:mirror|register|estate|history|evidence|telemetry|monitor(?:ing)?)",
        )
    ),
    re.IGNORECASE,
)

#: Table headers that make a numeric cell a runtime figure, and the subset that
#: makes it a POPULATION (i.e. the denominator is present in the row).
_TABLE_POPULATION_HEADER_RE = re.compile(
    r"(?<![A-Za-z])(?:(?:job\s+)?runs?|executions?|invocations?|samples?|"
    r"n|count|total)(?![A-Za-z])",
    re.IGNORECASE,
)
_TABLE_RUNTIME_HEADER_RE = re.compile(
    r"(?<![A-Za-z])(?:(?:job\s+)?runs?|executions?|invocations?|failures?|failed|"
    r"failure\s+rate|success\s+rate|error\s+rate|uptime|downtime|availability|"
    r"reliability|incidents?|latency|duration|elapsed|p(?:50|75|90|95|99)|"
    r"percentile|median|cadence|schedule|frequency|throughput)(?![A-Za-z])",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Public data shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MeasurementReference:
    """What a claim's scope says about how the figure was measured."""

    window: str | None = None
    denominator: str | None = None
    register: str | None = None

    @property
    def satisfied(self) -> bool:
        """A window AND (a denominator or a named register)."""
        return bool(self.window) and bool(self.denominator or self.register)

    @property
    def missing(self) -> tuple[str, ...]:
        gaps: list[str] = []
        if not self.window:
            gaps.append("no time window")
        if not self.denominator and not self.register:
            gaps.append("no denominator and no named register")
        elif not self.denominator:
            gaps.append("no denominator")
        return tuple(gaps)


@dataclass(frozen=True)
class RuntimeMeasurementClaim:
    """A published figure about observed runtime behaviour."""

    line: int
    kind: str  # "rate" | "count" | "duration" | "cadence"
    text: str
    scope: str
    figures: tuple[str, ...]

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(_to_float(raw) for raw in self.figures)


# --------------------------------------------------------------------------- #
# Unit / scope segmentation
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def _blocks(lines: Sequence[str]) -> list[tuple[int, int]]:
    """Maximal runs of non-blank lines, as (start, end) half-open indices."""
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = index
        elif start is not None:
            blocks.append((start, index))
            start = None
    if start is not None:
        blocks.append((start, len(lines)))
    return blocks


def _nearest_heading(lines: Sequence[str], before: int) -> str:
    for index in range(before - 1, max(-1, before - 60), -1):
        if _HEADING_RE.match(lines[index]):
            return lines[index].strip()
    return ""


def _caption(lines: Sequence[str], blocks: Sequence[tuple[int, int]], position: int) -> str:
    """The preceding block when it reads as a caption (at most 3 lines)."""
    if position == 0:
        return ""
    start, end = blocks[position - 1]
    if end - start > 3:
        return ""
    return "\n".join(line.strip() for line in lines[start:end])


def _sentences(joined: str) -> Iterator[tuple[int, str]]:
    """Yield (end_offset, sentence) over ``joined``.

    Numbers survive: the terminator must be followed by whitespace or the end of
    the string, so "0.8%" and "23.1%" are never split mid-figure.
    """
    cursor = 0
    for match in _SENTENCE_END_RE.finditer(joined):
        end = match.end()
        chunk = joined[cursor:end].strip()
        if chunk:
            yield end, chunk
        cursor = end
    tail = joined[cursor:].strip()
    if tail:
        yield len(joined), tail


def _iter_units(text: str) -> Iterator[tuple[int, str, str, str]]:
    """Yield (line_no, unit_text, scope_text, table_header) over ``text``.

    ``unit_text`` is what the trigger patterns run against; ``scope_text`` is
    what the measurement-reference patterns run against. See the module
    docstring for the scoping rules and why a claim never inherits from text
    that follows it.
    """
    lines = text.splitlines()
    blocks = _blocks(lines)
    for position, (start, end) in enumerate(blocks):
        heading = _nearest_heading(lines, start)
        caption = _caption(lines, blocks, position)
        prelude = "\n".join(part for part in (heading, caption) if part)
        consumed: list[str] = []  # block text already passed, in order

        index = start
        while index < end:
            line = lines[index]
            if _TABLE_ROW_RE.match(line):
                table_start = index
                while index < end and _TABLE_ROW_RE.match(lines[index]):
                    index += 1
                header = lines[table_start].strip()
                block_prefix = "\n".join(consumed)
                for row_index in range(table_start, index):
                    row = lines[row_index]
                    if row_index == table_start or _TABLE_SEP_RE.match(row):
                        continue
                    unit = f"{header}\n{row.strip()}"
                    scope = "\n".join(
                        part for part in (prelude, block_prefix, unit) if part
                    )
                    yield row_index + 1, unit, scope, header
                consumed.extend(line.strip() for line in lines[table_start:index])
                continue

            run_start = index
            index += 1
            while (
                index < end
                and not _TABLE_ROW_RE.match(lines[index])
                and not _LIST_MARKER_RE.match(lines[index])
                and not _HEADING_RE.match(lines[index])
            ):
                index += 1
            run_lines = [lines[i].strip() for i in range(run_start, index)]
            joined = " ".join(run_lines)
            block_prefix = "\n".join(consumed)
            for offset, sentence in _sentences(joined):
                scope = "\n".join(
                    part
                    for part in (prelude, block_prefix, joined[:offset].strip())
                    if part
                )
                yield run_start + 1, sentence, scope, ""
            consumed.extend(run_lines)


# --------------------------------------------------------------------------- #
# Claim detection
# --------------------------------------------------------------------------- #


def _split_cells(row: str) -> list[str]:
    stripped = row.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _table_figures(header: str, row: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Runtime figures in a table row, plus the population counts it states.

    Returns ``([(raw_figure, kind)], population_raws)``. A cell only counts when
    its own column header names a runtime quantity -- a numeric cell under
    "Components" or "Columns" is scan data and is left alone. ``population_raws``
    are the cells under a header that names the population ("Runs",
    "Executions", "Total", "n"): those ARE the row's denominator, stated in the
    table's own structure rather than in prose.
    """
    header_cells = _split_cells(header)
    row_cells = _split_cells(row)
    figures: list[tuple[str, str]] = []
    population: list[str] = []
    for header_cell, row_cell in zip(header_cells, row_cells):
        match = _NUMERIC_CELL_RE.match(row_cell)
        if not match or not _TABLE_RUNTIME_HEADER_RE.search(header_cell):
            continue
        raw = match.group(1)
        lowered = f"{header_cell} {row_cell}".lower()
        if "%" in lowered or "rate" in lowered or "uptime" in lowered:
            kind = "rate"
        elif _STAT_TERM_RE.search(header_cell) or re.search(
            r"(?<![A-Za-z])(?:ms|s|sec|secs|seconds|min|mins|minutes|h|hrs|hours)"
            r"(?![A-Za-z])",
            row_cell,
            re.IGNORECASE,
        ):
            kind = "duration"
        elif re.search(r"(?<![A-Za-z])(?:cadence|schedule|frequency)(?![A-Za-z])",
                       header_cell, re.IGNORECASE):
            kind = "cadence"
        else:
            kind = "count"
        figures.append((raw, kind))
        if _TABLE_POPULATION_HEADER_RE.search(header_cell):
            population.append(raw)
    return figures, population


def _prose_figures(unit: str) -> list[tuple[str, str]]:
    """Runtime figures in a prose unit, as (raw_figure, kind)."""
    figures: list[tuple[str, str]] = []
    has_runtime_term = bool(_RUNTIME_TERM_RE.search(unit))

    for match in _RUNTIME_COUNT_RE.finditer(unit):
        raw = match.group(1)
        figures.append(("0" if raw.lower() == "zero" else raw, "count"))
    for match in _EXECUTION_TIMES_RE.finditer(unit):
        figures.append((match.group(1), "count"))

    if has_runtime_term:
        for match in _PERCENT_RE.finditer(unit):
            figures.append((match.group(1), "rate"))
    for match in _RATE_PHRASE_RE.finditer(unit):
        figures.append((match.group(1), "rate"))

    if _STAT_TERM_RE.search(unit):
        for match in _DURATION_RE.finditer(unit):
            figures.append((match.group(1), "duration"))

    if _EXEC_VERB_RE.search(unit):
        for match in _CADENCE_RE.finditer(unit):
            figures.append((match.group(1), "cadence"))
        for match in _FREQUENCY_RE.finditer(unit):
            figures.append((match.group(1), "cadence"))
    return figures


_KIND_PRIORITY = ("rate", "duration", "cadence", "count")


def find_runtime_measurement_claims(text: str) -> list[RuntimeMeasurementClaim]:
    """Locate published claims about observed runtime behaviour.

    One claim per unit (a sentence, or a table row read against its header).
    ``kind`` is the strongest shape found in the unit, ordered rate > duration >
    cadence > count, because that is the order in which a wrong figure misleads.
    """
    claims: list[RuntimeMeasurementClaim] = []
    for line_no, unit, scope, header in _iter_units(text or ""):
        found: list[tuple[str, str]] = list(_prose_figures(unit))
        population: list[str] = []
        if header:
            row = unit.split("\n", 1)[1] if "\n" in unit else ""
            table_found, population = _table_figures(header, row)
            found.extend(table_found)
        if not found:
            continue

        seen: set[str] = set()
        figures: list[str] = []
        kinds: set[str] = set()
        for raw, kind in found:
            kinds.add(kind)
            if raw not in seen:
                seen.add(raw)
                figures.append(raw)
        kind = next(k for k in _KIND_PRIORITY if k in kinds)
        if population:
            # The row states its own denominator structurally (a "Runs" column).
            # Restate it in the scope in the prose form the reference check
            # reads, so a correctly-built reliability table is not reported as
            # missing the denominator sitting two cells to its left.
            scope = "\n".join(
                [scope] + [f"based on {raw} runs" for raw in population]
            )
        claims.append(
            RuntimeMeasurementClaim(
                line=line_no,
                kind=kind,
                text=unit.replace("\n", " / ").strip(),
                scope=scope,
                figures=tuple(figures),
            )
        )
    return claims


def measurement_reference(scope: str) -> MeasurementReference:
    """What ``scope`` establishes about window, denominator and register."""
    window = _WINDOW_RE.search(scope or "")
    denominator = _DENOMINATOR_RE.search(scope or "")
    register = _REGISTER_RE.search(scope or "")
    return MeasurementReference(
        window=window.group(0).strip() if window else None,
        denominator=denominator.group(0).strip() if denominator else None,
        register=register.group(0).strip() if register else None,
    )


# --------------------------------------------------------------------------- #
# Probe ledger
# --------------------------------------------------------------------------- #

#: Ledger keys that are NOT a measurement: the request that was sent, the
#: bookkeeping around it, and the shape digest. Harvesting the request would let
#: a window argument ("30") provenance a published "30%"; harvesting the digest
#: would let a row count or an HTTP 404 do the same.
_LEDGER_INPUT_KEYS = frozenset(
    {
        # the request
        "tool",
        "probe",
        "command",
        "invocation",
        "arguments",
        "args",
        "input",
        "inputs",
        "params",
        "parameters",
        "request",
        "query",
        "filter",
        "filters",
        "scope",
        # bookkeeping
        "id",
        "run_id",
        "project_id",
        "source_key",
        "config_source",
        "timestamp",
        "ts",
        "started_at",
        "finished_at",
        "duration_ms",
        "error_code",
        "status_code",
        "http_status",
        "reason",
        "truncated",
        # shape, by the CLI's own contract: "keys and counts, never values"
        "digest",
        "shape",
    }
)

_FAILED_STATUSES = frozenset({"error", "errored", "failed", "failure", "timeout", "unavailable"})


def _contains_field_unmapped(node: object) -> bool:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if str(key).lower() == "field_unmapped" and value:
                return True
            if _contains_field_unmapped(value):
                return True
        return False
    if isinstance(node, (list, tuple)):
        return any(_contains_field_unmapped(item) for item in node)
    if isinstance(node, str):
        return node.strip().lower() == "field_unmapped"
    return False


def _entry_is_unusable(entry: Mapping[str, object]) -> bool:
    if entry.get("error"):
        return True
    for key in ("ok", "success"):
        if key in entry and entry[key] is False:
            return True
    status = str(entry.get("status", "")).strip().lower()
    if status in _FAILED_STATUSES:
        return True
    # A probe that reported "this field does not exist" measured nothing. Its
    # zero must never legitimise a published zero -- those are different facts.
    return _contains_field_unmapped(entry)


def _harvest(node: object, values: set[float]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if str(key).lower() in _LEDGER_INPUT_KEYS:
                continue
            _harvest(value, values)
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            _harvest(item, values)
        return
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        values.add(float(node))
        return
    if isinstance(node, str):
        match = _NUMERIC_STRING_RE.match(node.strip())
        if match:
            values.add(_to_float(match.group(1)))


def ledger_figures(ledger: Sequence[Mapping[str, object]] | None) -> set[float]:
    """Every value a successful probe in ``ledger`` actually returned.

    Empty for a ledger that records only the SHAPE of each result -- which is
    what the shipped ``_tools/runtime_tools.py`` writes, deliberately, so that
    ``_assessment/`` never becomes a copy of an unredacted payload. See
    :func:`ledger_records_a_probe` for the check that still works in that case.
    """
    values: set[float] = set()
    for entry in ledger or ():
        if not isinstance(entry, Mapping) or _entry_is_unusable(entry):
            continue
        _harvest(entry, values)
    return values


def ledger_records_a_probe(ledger: Sequence[Mapping[str, object]] | None) -> bool:
    """Did any probe in ``ledger`` complete successfully?"""
    return any(
        isinstance(entry, Mapping) and not _entry_is_unusable(entry)
        for entry in ledger or ()
    )


def _figure_is_recorded(raw: str, values: set[float], *, allow_scaling: bool) -> bool:
    """Does any recorded probe value round to the published figure?

    Tolerance is the published precision: a ledger 23.076 provenances "23.1"
    (0 decimals -> +/- 0.5, 1 decimal -> +/- 0.05). Percent scaling is applied
    for rate claims only, so a ledger fraction 0.008 provenances "0.8%".
    """
    value = _to_float(raw)
    tolerance = 0.5 * (10.0 ** -_decimals(raw)) + 1e-9
    for recorded in values:
        candidates = (recorded, recorded * 100.0) if allow_scaling else (recorded,)
        for candidate in candidates:
            if abs(candidate - value) <= tolerance:
                return True
    return False


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

_REFERENCE_RATIONALE = (
    "The same estate reads as 0.087% or 30.8% failing depending on which "
    "register the number covers and what it is divided by; a figure with no "
    "window and no denominator is not reportable. State which register or "
    "aggregate it came from, which environments that register covers, the time "
    "window, and the denominator."
)


def measurement_provenance_errors(
    text: str,
    *,
    ledger: Sequence[Mapping[str, object]] | None = None,
    source_label: str = "the runtime probe ledger",
) -> list[str]:
    """Flag published runtime figures that carry no measurement reference.

    Two independent checks, both deterministic:

    1. Every runtime measurement claim must be accompanied -- in its own text,
       its table header, its caption or its heading -- by a time WINDOW and by
       either a DENOMINATOR or a named REGISTER.
    2. When ``ledger`` is supplied, every published figure must correspond to a
       value a probe actually returned. ``None`` skips this check; ``[]`` means
       the ledger recorded no usable probe, so nothing in the text is
       provenanced.

    Returns human-readable error strings in the style of the sibling
    fabrication validators. Advisory by convention: never raise on these.
    """
    claims = find_runtime_measurement_claims(text)
    if not claims:
        return []

    errors: list[str] = []
    for claim in claims:
        reference = measurement_reference(claim.scope)
        if reference.satisfied:
            continue
        if len(errors) >= _MAX_REPORTED_CLAIMS:
            break
        errors.append(
            f"output publishes a runtime {claim.kind} with no measurement "
            f'reference ("{_snippet(claim.text)}", line {claim.line}): '
            f"{', '.join(reference.missing)}. {_REFERENCE_RATIONALE}"
        )

    if ledger is None:
        return errors

    values = ledger_figures(ledger)
    if not values:
        if ledger_records_a_probe(ledger):
            # Probes ran, but this ledger records their SHAPE only. The
            # value-level check cannot be performed and must not be faked:
            # reporting every figure here would accuse a correctly-evidenced
            # document of fabrication on the strength of a redaction policy.
            return errors
        published = _published_figures(claims)
        errors.append(
            f"output publishes runtime figures ({published}) but {source_label} "
            "records no probe that returned a value. Every published runtime "
            "number must come from a probe that actually ran; run the probe and "
            "quote what it returned, or withdraw the figure."
        )
        return errors

    reported = 0
    for claim in claims:
        unrecorded = [
            raw
            for raw in claim.figures
            if not _figure_is_recorded(raw, values, allow_scaling=claim.kind == "rate")
        ]
        if not unrecorded:
            continue
        if reported >= _MAX_REPORTED_CLAIMS:
            break
        reported += 1
        errors.append(
            f"output publishes the runtime {claim.kind} figure(s) "
            f"{', '.join(unrecorded)} "
            f'("{_snippet(claim.text)}", line {claim.line}) but no probe in '
            f"{source_label} returned that value. A number with no probe behind "
            "it is fabrication; re-run the probe and quote what it returned, or "
            "withdraw the figure."
        )
    return errors


def _snippet(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."


def _published_figures(claims: Sequence[RuntimeMeasurementClaim]) -> str:
    seen: list[str] = []
    for claim in claims:
        for raw in claim.figures:
            if raw not in seen:
                seen.append(raw)
    shown = ", ".join(seen[:_MAX_REPORTED_FIGURES])
    if len(seen) > _MAX_REPORTED_FIGURES:
        shown += f", +{len(seen) - _MAX_REPORTED_FIGURES} more"
    return shown
