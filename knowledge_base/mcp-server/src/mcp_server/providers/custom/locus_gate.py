"""Deterministic locus gate -- resolve the loci that group analyses cite.

METHOD.md requires every ``Confirmed`` finding to carry a locus, and the CLI
lane emits them as file+line references into the staged evidence::

    Basis/locus: `_preprocess/Collectors.md` lines 7524-8164.
    Basis: Confirmed. Locus: `_preprocess/Staging.md:344-367,705-753`.

This module reads ``_analysis/`` and, for every such citation, asks three
mechanical questions:

1. does the cited evidence file EXIST inside the staging workspace,
2. is every cited line range INSIDE that file (``M <= len(file)``),
3. is every range non-degenerate (``1 <= N <= M``).

WHAT THIS GATE DOES NOT CHECK
-----------------------------
The published gate documentation this method descends from is explicit that
"the gate checks that a decision was RECORDED, never that it was RIGHT", and
concedes that it "checks that a locus was recorded, never that it RESOLVES --
no check opens the cited file at the cited expression". Ours opens the file,
because our loci are file+line. It stops there, and the honest boundary is:

- It does NOT check that the cited lines contain the claimed expression.
  ``_preprocess/G.md:1-9000`` over a 9000-line file passes every check here
  while citing the entire estate for one sentence.
- It does NOT check that the claim is TRUE, or that the grade attached to it
  (``Confirmed`` / ``Inferred`` / ``Unknown``) is the right one.
- It does NOT check that the enumeration was COMPLETE -- a section that cited
  three of forty designs and omitted the rest scores perfectly here.
- It does NOT check claims carrying no locus at all. An uncited sentence is
  invisible to this gate; only what was cited is resolved.
- It does NOT reconcile a citation against the group that owns the evidence.
  That is ``group_attribution_guard``'s question, not this one.
- It does NOT decide coverage. ``group_summaries_without_analysis`` is an
  OBSERVATION derived from the same naming rule, reported because it is free
  once the files are classified; it never moves ``ok``.

A contract that over-states its enforcement is worse than a thin one: it tells
a reader an output was verified in a way it was not. The five bullets above are
the reason this module reports ``loci_resolved``, never ``analysis verified``.

ADVISORY, LIKE EVERY OTHER PHASE 3 FINAL CHECK
----------------------------------------------
Per ``knowledge_base/CLAUDE.md``, the final-check gates are advisory: findings
become ``log_cb`` warnings, the report lands under ``_assessment/``, and the
documents ship as written. Nothing here raises, and nothing here blocks a swap.

BARE FILENAMES
--------------
Analyses also emit the directory-less shorthand ``` `Staging.md:344-367` ```,
resolved here against ``_preprocess/`` first (see ``_CANDIDATE_DIRS``). That
precedence is not a guess: measured over the pluripharm run's 104 bare-path
ranges, resolving to ``_preprocess/`` put 104 of 104 in bounds while resolving
to ``_analysis/`` put 100 of 104 OUT of bounds. The shorthand is still counted
separately (``bare_path_citations``) because a locus that omits its directory
is only resolvable by convention, and METHOD.md asks for the full path.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ANALYSIS_DIRNAME = "_analysis"
ASSESSMENT_DIRNAME = "_assessment"
LOCUS_REPORT_FILENAME = "locus_gate.json"

#: Where a directory-less citation is looked up, in order. ``_preprocess`` is
#: first because a locus points at EVIDENCE by definition, and because the
#: measurement in the module docstring says so.
_CANDIDATE_DIRS: tuple[str, ...] = (
    "_preprocess",
    "_assessment/quick_scan",
    "_method/_shared",
    "_assessment",
    "_analysis",
    "",
)

#: Statuses that count as a gate finding. ``file_only`` does not: a citation
#: naming a file and no lines is a WEAKER locus, not a broken one, and it is
#: reported as its own count rather than as a failure.
FAILURE_STATUSES: frozenset[str] = frozenset(
    {"missing_file", "out_of_range", "degenerate", "outside_workspace", "unparsed_range"}
)

# --------------------------------------------------------------------------- #
# Grammar
#
# Every shape below was taken from a real run's `_analysis/`, not invented:
#   `_preprocess/PPDWH_part1.md:41-818`
#   `_preprocess/Global.md:1613-1650,1767-1795,1937-1959`
#   `_preprocess/demo.md:42`
#   `Staging.md:9230-EOF`
#   `_preprocess/Datawarehouse.md`, lines 163-227
#   `_preprocess/Collectors.md` lines 630-649 and 1470-1510
#   `_preprocess/WMS.md`                        (file-level, no lines)
# --------------------------------------------------------------------------- #

_RANGE = r"\d{1,9}(?:\s*-\s*(?:\d{1,9}|EOF))?"
_RANGE_LIST = rf"{_RANGE}(?:\s*(?:,|and)\s*{_RANGE})*"

#: The leading character class deliberately admits ``.`` and ``/`` so that
#: ``../secret.md`` and ``/etc/passwd.md`` are MATCHED and then rejected by
#: ``_resolve_path`` as ``outside_workspace``. Excluding them from the grammar
#: is also safe -- nothing would ever stat them -- but it is safe by silence:
#: the escaping citation simply disappears from the report, which is the one
#: outcome a security boundary must not have.
_CITATION = re.compile(
    rf"`(?P<path>[A-Za-z0-9_./][A-Za-z0-9_./-]{{0,200}}?\.md)"
    rf"(?::(?P<inline>{_RANGE_LIST}))?`"
    rf"(?P<trail>\s*,?\s*lines?\s+{_RANGE_LIST})?",
    re.IGNORECASE,
)
_TRAIL_PREFIX = re.compile(r"^\s*,?\s*lines?\s+", re.IGNORECASE)
_RANGE_EXACT = re.compile(r"(\d{1,9})(?:\s*-\s*(\d{1,9}|EOF))?", re.IGNORECASE)
_RANGE_SEPARATOR = re.compile(r"\s*(?:,|and)\s*", re.IGNORECASE)

_H2 = re.compile(r"^##\s+(.+?)\s*$")

#: METHOD.md mandates ``BR-{GROUP}-NNN`` business rules, ``AP-xxx-nnn``
#: anti-patterns and ``RF-xxx-nnn`` red flags. Deliberately not a generic
#: ``[A-Z]+-[A-Z]+-\d+``, which harvests ``ISO-8601-1`` as a claim id.
_CLAIM_ID = re.compile(r"\b((?:BR|AP|RF)-[A-Za-z0-9_]{1,40}-\d{1,4})\b")

#: Only the first match per line is kept as the owning claim, and only when it
#: sits at or before the citation.
_MAX_EXCERPT = 240


# --------------------------------------------------------------------------- #
# The `_analysis/` naming rule (METHOD.md section 5)
# --------------------------------------------------------------------------- #

def is_internal_analysis_file(relative_path: str) -> bool:
    """True when an ``_analysis/`` entry is an internal artifact, not a group.

    ``_analysis/_shared_library.md`` is the shared-library pre-pass digest --
    METHOD.md is explicit that the pre-pass "writes NO group analysis" -- but it
    sits beside the ``{group}.md`` files, so any count of ``_analysis/*.md``
    over-reports completed groups by one. The rule METHOD.md now states, and
    this function applies, is positional and needs no allowlist: a leading
    underscore on ANY path segment means internal.
    """
    return any(part.startswith("_") for part in Path(relative_path).parts)


def classify_analysis_file(relative_path: str) -> str:
    """Return ``internal`` / ``group_summary`` / ``group_section`` / ``group_analysis``.

    ``group_section`` covers the ``_analysis/{group}/{SECTION}.md`` layout an
    earlier run may have left behind; it belongs to a group analysis without
    being one on its own, so counting it as a group over-reports exactly the
    way ``_shared_library.md`` does.
    """
    path = Path(relative_path)
    if is_internal_analysis_file(relative_path):
        return "internal"
    if len(path.parts) > 1:
        return "group_section"
    if path.stem.endswith("_summary"):
        return "group_summary"
    return "group_analysis"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class LocusRange(BaseModel):
    """One ``N-M`` span of a citation, resolved against the cited file."""

    model_config = ConfigDict(extra="forbid")

    text: str
    start: int
    end: int
    eof_endpoint: bool = False
    status: str = "resolved"


class LocusCitation(BaseModel):
    """One ``path[:lines]`` reference found in an analysis file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    line: int
    section: str = ""
    claim_id: str = ""
    citation: str
    path: str
    resolved_path: str = ""
    target_lines: int = 0
    bare_path: bool = False
    status: str = "resolved"
    detail: str = ""
    ranges: list[LocusRange] = Field(default_factory=list)
    excerpt: str = ""

    def failed(self) -> bool:
        return self.status in FAILURE_STATUSES

    def finding_line(self) -> str:
        where = f"{self.file}:{self.line}"
        if self.section:
            where += f" [{self.section}]"
        if self.claim_id:
            where += f" {self.claim_id}"
        return f"{where}: {self.citation} -- {self.status}: {self.detail}"


class LocusFileReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    kind: str
    citations: int = 0
    ranges: int = 0
    resolved_ranges: int = 0
    failures: int = 0


class LocusGateReport(BaseModel):
    """Advisory result of resolving every locus an analysis cited."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    ok: bool = True
    advisory: bool = True
    checks: list[str] = Field(
        default_factory=lambda: [
            "cited evidence file exists inside the staging workspace",
            "every cited line range ends at or before the file's last line",
            "every cited line range is non-degenerate (1 <= start <= end)",
        ]
    )
    not_checked: list[str] = Field(
        default_factory=lambda: [
            "that the cited lines contain the claimed expression",
            "that the claim is true, or graded correctly",
            "that the enumeration was complete",
            "claims that carry no locus at all",
        ]
    )
    files_scanned: int = 0
    group_analyses: list[str] = Field(default_factory=list)
    group_summaries: list[str] = Field(default_factory=list)
    internal_files: list[str] = Field(default_factory=list)
    #: Observation, not a verdict. Derived from the same naming rule, and it
    #: does NOT move ``ok`` -- this gate's contract is locus resolution, and
    #: METHOD.md hands coverage to the orchestrator's own per-group check.
    #: Reported because a ``{group}_summary.md`` with no ``{group}.md`` beside
    #: it is exactly the miscount that the ``_`` rule exists to prevent.
    group_summaries_without_analysis: list[str] = Field(default_factory=list)
    citations: int = 0
    ranges: int = 0
    resolved_ranges: int = 0
    file_only_citations: int = 0
    bare_path_citations: int = 0
    eof_endpoints: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    files: list[LocusFileReport] = Field(default_factory=list)
    findings: list[LocusCitation] = Field(default_factory=list)
    truncated_findings: int = 0

    def summary_line(self) -> str:
        if not self.citations:
            return (
                f"Locus gate: no file+line loci cited across "
                f"{self.files_scanned} analysis file(s)"
            )
        head = (
            f"Locus gate: {self.resolved_ranges}/{self.ranges} cited line range(s) "
            f"resolved across {self.citations} citation(s) in "
            f"{len(self.group_analyses)} group analysis file(s)"
        )
        if self.ok:
            return head
        worst = sorted(
            ((s, c) for s, c in self.by_status.items() if s in FAILURE_STATUSES),
            key=lambda item: (-item[1], item[0]),
        )
        detail = ", ".join(f"{count} {status}" for status, count in worst)
        return f"{head} -- {detail}"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

class _LineCounter:
    """Cache ``len(file.splitlines())``; evidence files run to 68k lines."""

    def __init__(self) -> None:
        self._cache: dict[Path, int] = {}

    def __call__(self, path: Path) -> int:
        cached = self._cache.get(path)
        if cached is None:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - unreadable staged file
                logger.warning("Locus gate could not read %s: %s", path, exc)
                text = ""
            cached = len(text.splitlines())
            self._cache[path] = cached
        return cached


def _inside(staging: Path, candidate: Path) -> bool:
    """True when ``candidate`` stays inside the staging boundary.

    METHOD.md section 2 makes the staging directory an absolute security
    boundary. A citation is untrusted model output, so ``../`` in a cited path
    must never turn into a stat outside the workspace.
    """
    try:
        return candidate.resolve().is_relative_to(staging)
    except (OSError, ValueError):  # pragma: no cover - resolve() edge cases
        return False


def _resolve_path(staging: Path, cited: str) -> tuple[Path | None, str, bool]:
    """Resolve a cited path to a staged file.

    Returns ``(path_or_None, status, bare)``. ``status`` is ``resolved``,
    ``missing_file`` or ``outside_workspace``.
    """
    bare = "/" not in cited
    candidates = (
        [staging / directory / cited if directory else staging / cited for directory in _CANDIDATE_DIRS]
        if bare
        else [staging / cited]
    )
    escaped = False
    for candidate in candidates:
        if not _inside(staging, candidate):
            escaped = True
            continue
        if candidate.is_file():
            return candidate, "resolved", bare
    if escaped and not bare:
        return None, "outside_workspace", bare
    return None, "missing_file", bare


def _parse_ranges(spec: str, target_lines: int) -> tuple[list[LocusRange], int]:
    """Split a range list into resolved ``LocusRange`` objects.

    Returns the ranges and the count of segments that did not parse. An
    unparsed segment is reported, never silently dropped: a locus this gate
    could not read is a locus it did not check.
    """
    ranges: list[LocusRange] = []
    unparsed = 0
    for segment in _RANGE_SEPARATOR.split(spec.strip()):
        segment = segment.strip()
        if not segment:
            continue
        match = _RANGE_EXACT.fullmatch(segment)
        if match is None:
            unparsed += 1
            continue
        start = int(match.group(1))
        raw_end = match.group(2)
        eof = bool(raw_end) and raw_end.upper() == "EOF"
        if raw_end is None:
            end = start
        elif eof:
            end = target_lines
        else:
            end = int(raw_end)
        if start < 1 or end < start:
            status = "degenerate"
        elif end > target_lines:
            status = "out_of_range"
        else:
            status = "resolved"
        ranges.append(
            LocusRange(text=segment, start=start, end=end, eof_endpoint=eof, status=status)
        )
    return ranges, unparsed


def _line_index(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    low, high = 0, len(starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return low + 1


def _context_for(text: str, starts: list[int], offset: int) -> tuple[str, str]:
    """Return ``(section, claim_id)`` owning the citation at ``offset``."""
    line_number = _line_of(starts, offset)
    line_start = starts[line_number - 1]
    line_end = text.find("\n", offset)
    line = text[line_start : line_end if line_end != -1 else len(text)]

    claim = ""
    for match in _CLAIM_ID.finditer(line):
        if line_start + match.start() <= offset:
            claim = match.group(1)
            break

    section = ""
    for index in range(line_number - 1, -1, -1):
        candidate = text[starts[index] : starts[index + 1] if index + 1 < len(starts) else len(text)]
        heading = _H2.match(candidate.rstrip("\n"))
        if heading is not None:
            section = heading.group(1).strip()
            break
    return section, claim


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

def citations_in(
    staging_dir: Path | str,
    analysis_file: Path | str,
    *,
    count_lines: Callable[[Path], int] | None = None,
) -> list[LocusCitation]:
    """Resolve every citation in ONE analysis file, resolved ones included.

    The single parse path. ``validate_loci`` aggregates what this returns, and
    the report only RETAINS the failures; a caller (or a test) that needs to
    see the resolved ones asks here rather than re-implementing the grammar.
    """
    staging = Path(staging_dir).resolve()
    path = Path(analysis_file)
    if not path.is_absolute():
        path = staging / ANALYSIS_DIRNAME / path
    counter = count_lines or _LineCounter()
    try:
        relative = path.relative_to(staging / ANALYSIS_DIRNAME).as_posix()
    except ValueError:  # pragma: no cover - caller passed a path outside _analysis
        relative = path.name

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Locus gate could not read analysis %s: %s", path, exc)
        return []

    starts = _line_index(text)
    citations: list[LocusCitation] = []

    for match in _CITATION.finditer(text):
        cited = match.group("path")
        spec = match.group("inline") or ""
        trail = match.group("trail") or ""
        if not spec and trail:
            spec = _TRAIL_PREFIX.sub("", trail, count=1)

        section, claim_id = _context_for(text, starts, match.start())
        citation = LocusCitation(
            file=relative,
            line=_line_of(starts, match.start()),
            section=section,
            claim_id=claim_id,
            citation=" ".join(match.group(0).split())[:_MAX_EXCERPT],
            path=cited,
        )

        target, status, bare = _resolve_path(staging, cited)
        citation.bare_path = bare

        if target is None:
            citation.status = status
            citation.detail = (
                "cited path escapes the staging workspace"
                if status == "outside_workspace"
                else "no such file under the staging workspace"
            )
        else:
            citation.resolved_path = target.relative_to(staging).as_posix()
            citation.target_lines = counter(target)
            if not spec.strip():
                citation.status = "file_only"
                citation.detail = "file cited without a line range"
            else:
                ranges, unparsed = _parse_ranges(spec, citation.target_lines)
                citation.ranges = ranges
                bad = [r for r in ranges if r.status != "resolved"]
                if unparsed:
                    citation.status = "unparsed_range"
                    citation.detail = f"{unparsed} line range(s) could not be parsed"
                elif bad:
                    citation.status = bad[0].status
                    citation.detail = "; ".join(
                        f"{r.text} ({r.status}), file has {citation.target_lines} lines"
                        for r in bad[:3]
                    )
                else:
                    citation.status = "resolved"

        citations.append(citation)

    return citations


def validate_loci(staging_dir: Path | str, max_findings: int = 200) -> LocusGateReport:
    """Resolve every file+line locus cited under ``_analysis/``.

    Never raises: an unreadable analysis file is skipped with a log line, the
    same way every other advisory gate in this lane degrades.
    """
    staging = Path(staging_dir).resolve()
    report = LocusGateReport()
    analysis_dir = staging / ANALYSIS_DIRNAME
    if not analysis_dir.is_dir():
        return report

    count_lines = _LineCounter()

    for path in sorted(analysis_dir.rglob("*.md")):
        relative = path.relative_to(analysis_dir).as_posix()
        kind = classify_analysis_file(relative)
        report.files_scanned += 1
        if kind == "group_analysis":
            report.group_analyses.append(relative)
        elif kind == "group_summary":
            report.group_summaries.append(relative)
        elif kind == "internal":
            report.internal_files.append(relative)

        entry = LocusFileReport(file=relative, kind=kind)

        for citation in citations_in(staging, path, count_lines=count_lines):
            if citation.bare_path:
                report.bare_path_citations += 1
            if citation.status == "file_only":
                report.file_only_citations += 1
            report.eof_endpoints += sum(1 for r in citation.ranges if r.eof_endpoint)

            entry.citations += 1
            entry.ranges += len(citation.ranges)
            entry.resolved_ranges += sum(1 for r in citation.ranges if r.status == "resolved")
            report.by_status[citation.status] = report.by_status.get(citation.status, 0) + 1

            if citation.failed():
                entry.failures += 1
                if len(report.findings) < max_findings:
                    report.findings.append(citation)
                else:
                    report.truncated_findings += 1

        report.citations += entry.citations
        report.ranges += entry.ranges
        report.resolved_ranges += entry.resolved_ranges
        report.files.append(entry)

    analysed = {Path(name).stem for name in report.group_analyses}
    report.group_summaries_without_analysis = sorted(
        {
            Path(name).stem[: -len("_summary")]
            for name in report.group_summaries
            if Path(name).stem[: -len("_summary")] not in analysed
        }
    )

    report.ok = not any(entry.failures for entry in report.files)
    return report


def write_locus_gate_report(staging_dir: Path | str, report: LocusGateReport) -> Path:
    """Write the locus report to ``_assessment/locus_gate.json``."""
    target = Path(staging_dir) / ASSESSMENT_DIRNAME / LOCUS_REPORT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def locus_gate_messages(report: LocusGateReport, max_detail: int = 3) -> list[tuple[str, str]]:
    """Render the report as ``(message, level)`` pairs for ``log_cb``.

    Advisory by construction: the worst level emitted is ``warning``. Mirrors
    how the client-language and provenance guards surface their findings.
    """
    messages: list[tuple[str, str]] = [
        (report.summary_line(), "info" if report.ok else "warning")
    ]
    if report.group_summaries_without_analysis:
        orphans = ", ".join(report.group_summaries_without_analysis[:5])
        messages.append(
            (
                f"  {len(report.group_summaries_without_analysis)} group(s) have a "
                f"summary but no group analysis: {orphans}",
                "warning",
            )
        )
    if report.ok:
        return messages
    for citation in report.findings[:max_detail]:
        messages.append((f"  {citation.finding_line()}", "warning"))
    remaining = len(report.findings) - max_detail
    if remaining > 0 or report.truncated_findings:
        messages.append(
            (
                f"  ...and {remaining + report.truncated_findings} further "
                f"unresolved locus/loci; the full report is "
                f"{ASSESSMENT_DIRNAME}/{LOCUS_REPORT_FILENAME}",
                "warning",
            )
        )
    else:
        messages.append(
            (
                f"  Loci are advisory, so the documents ship as written; the full "
                f"report is {ASSESSMENT_DIRNAME}/{LOCUS_REPORT_FILENAME}",
                "warning",
            )
        )
    return messages
