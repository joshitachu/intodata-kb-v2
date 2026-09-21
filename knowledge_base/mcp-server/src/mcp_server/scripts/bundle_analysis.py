#!/usr/bin/env python3
"""Bundle analysis files per Phase 3 agent.

Usage: python3 bundle_analysis.py <staging_dir> <total_jobs> [large_threshold]

Written by MCP server preprocessing; executed by the orchestrator agent
between Phase 2.6 and Phase 3.  Creates per-agent analysis bundles in
<staging_dir>/_analysis/_bundle_<agent>.md so that each Phase 3 agent
reads ONE pre-filtered file instead of grepping through all group files.

Bundle size is capped at MAX_BUNDLE_BYTES (~15k tokens) to prevent context
window overflow.  If a bundle would exceed this after filtering, it is
auto-split into _bundle_<agent>_a.md and _bundle_<agent>_b.md.

Also writes:
  _analysis/_index.md     -- navigation table (group, jobs, token estimate, flags)

TWO LAYOUTS, ONE BUNDLER
------------------------
Phase 2 stores a group's analysis in one of two shapes and this script serves
both, deciding PER GROUP (the directory wins when both exist)::

    DIRECTORY (current)          FLAT (legacy, still on disk in old projects)
    _analysis/fourps/            _analysis/fourps.md
      COMPONENTS.md              _analysis/fourps_summary.md
      SOURCE_SQL.md
      _summary.md

On the DIRECTORY path bundling is FILE SELECTION: a section is a file named
after it, so this script lists, keeps the stems its agent wants, and
concatenates.  No heading recognition, no normalisation, no enumeration
stripping, no fence tracking, no substring matching -- none of it can apply to
a directory listing, and none of it runs there.

The FLAT path still has to find section boundaries inside one prose file, so
``parse_sections`` and its helpers below remain, clearly fenced off as
flat-only.  They are the seam that produced run 258: the guide asked for
``### COMPONENTS``, the model wrote ``## COMPONENTS``, this script matched
nothing, and Phase 3 documented an estate it could not read.  Slice 5 deletes
the flat layout and those helpers with it.

A TRANSFORM THAT LOSES EVERYTHING IS LOUD, ON BOTH PATHS.  No analyses at all,
a group directory with nothing in it, a group directory whose files are named
for nothing on the contract, a filtered bundle that carries almost none of its
input -- each raises.  Warning and exiting 0 with no bundles written is how
Phase 3 ends up pointed at ``_bundle_*.md`` files that do not exist.

STANDALONE BY DESIGN.  ``services/claude_service.py`` copies this file into
``{staging}/_preprocess/_bundle_analysis.py`` and the orchestrator agent runs it
with a bare ``python3``.  It must therefore import nothing from ``mcp_server``
and nothing outside the standard library.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

#: The 15 canonical section names from ANALYSIS_GUIDE.md, IN GUIDE ORDER.
#:
#: A tuple, not a set: this is both the membership contract and the order in
#: which a group's section files are concatenated, and a set would make that
#: order depend on process hash seed.  (That is not hypothetical here -- the
#: previous substring match over this collection resolved ``### LOW`` to
#: EXECUTION_FLOW or DATA_FLOWS depending on frozenset iteration order.)
ALL_SECTIONS: tuple[str, ...] = (
    "COMPONENTS", "EXECUTION_FLOW", "DATA_FLOWS", "TRANSFORMATION_DETAIL",
    "BUSINESS_RULES", "SCHEMA_DICTIONARY", "SOURCE_SQL", "EXTERNAL_SYSTEMS",
    "ERROR_HANDLING", "CUSTOM_CODE", "ENVIRONMENT_CONFIG", "SCHEDULING",
    "NAMING_PATTERNS", "CROSS_GROUP_REFS", "RISK_FLAGS",
)

#: Membership test derived from the ordered contract above.  Never iterated.
_CANONICAL_SECTIONS = frozenset(ALL_SECTIONS)

#: Position of each section in the contract, for deterministic concatenation.
_SECTION_ORDER: dict[str, int] = {name: i for i, name in enumerate(ALL_SECTIONS)}

# Section relevance per agent.  None = include all sections.
RELEVANCE: dict[str, dict[str, set[str]] | None] = {
    "core": {
        "high": {
            "COMPONENTS", "EXECUTION_FLOW", "DATA_FLOWS",
            "TRANSFORMATION_DETAIL", "SCHEMA_DICTIONARY", "SOURCE_SQL",
        },
        "medium": {"EXTERNAL_SYSTEMS", "SCHEDULING"},
    },
    "business_ops": {
        "high": {
            "BUSINESS_RULES", "TRANSFORMATION_DETAIL", "CUSTOM_CODE",
            "ERROR_HANDLING", "RISK_FLAGS",
        },
        "medium": {
            "ENVIRONMENT_CONFIG", "EXTERNAL_SYSTEMS",
            "CROSS_GROUP_REFS", "SCHEDULING",
        },
    },
    "patterns_user": None,  # broad context needed for docs 07-11
    "patterns": {
        "high": {
            "NAMING_PATTERNS", "RISK_FLAGS", "CUSTOM_CODE", "ERROR_HANDLING",
        },
        "medium": {
            "COMPONENTS", "DATA_FLOWS", "EXTERNAL_SYSTEMS", "BUSINESS_RULES",
        },
    },
    "user": {
        "high": {
            "ERROR_HANDLING", "ENVIRONMENT_CONFIG",
            "SCHEDULING", "EXECUTION_FLOW",
        },
        "medium": {
            "COMPONENTS", "DATA_FLOWS", "CROSS_GROUP_REFS",
            "RISK_FLAGS", "BUSINESS_RULES",
        },
    },
}

# Hard cap per bundle: ~15k tokens (1 token ~ 4 chars, 60KB ~ 15k tokens)
# This prevents context window overflow and forces progressive truncation.
MAX_BUNDLE_BYTES = 60_000

#: Catastrophe floor, NOT a focus target.  A filtered bundle is *supposed* to drop
#: the sections its agent does not need, so a low-ish ratio can be perfectly
#: correct; only a near-total loss means the selection failed.
#:
#: Measured on the 20 real run-258 group analyses (532,573 bytes):
#:   broken parse (h3-only): core 8.2%, business_ops 2.3%, patterns 1.9%, user 2.7%
#:   correct parse:          core 52.7%, business_ops 59.1%, patterns 74.0%, user 76.6%
#:
#: The floor sits in the wide gap between those bands.  A tighter floor (0.50 was
#: the first attempt) lands *inside* the legitimate band -- ``core`` deliberately
#: excludes RISK_FLAGS, which is 17.6% of these analyses, so a risk-heavy project
#: could legitimately fall under it and hard-fail a good run.  A guard that blocks
#: valid work is a guard the next operator switches off.
#:
#: It applies to BOTH layouts, on the same reasoning.  On the DIRECTORY path the
#: broken band is unreachable by construction -- selection is a stem comparison,
#: so it cannot mis-fire the way a heading match did -- and what is left to catch
#: is an estate whose evidence landed almost entirely in files this agent never
#: asks for.  That is measured the same way: bytes copied over bytes available.
MIN_RETENTION_RATIO = 0.15

PREAMBLE = "_PREAMBLE"

#: Below this many input bytes, "no canonical sections" is unremarkable -- a stub
#: analysis or a single error note legitimately has none.
_SUBSTANTIVE_INPUT_BYTES = 2_000

#: DIRECTORY layout: a group is a folder, a section is a file named for it, and
#: the group summary is ``_summary.md`` inside it.  Leading ``_`` is what keeps
#: the summary out of the section count, so this script must not treat any
#: ``_``-prefixed file as a section either.
DIRECTORY_LAYOUT = "directory"
SUMMARY_FILENAME = "_summary.md"

#: FLAT layout: ``{group}.md`` beside ``{group}_summary.md``.
FLAT_LAYOUT = "flat"
FLAT_SUMMARY_SUFFIX = "_summary"

#: Heading rendered above each section file's body when a directory group is
#: concatenated.  h3 is what the guide asks for and what
#: ``providers/custom/analysis_store.read_group_analysis`` renders, so a bundle
#: built from a migrated project reads exactly like one built from a flat file.
_RENDER_HEADING = "###"


class MissingAnalysisError(RuntimeError):
    """Phase 2 left nothing this script can bundle.

    Raised instead of the previous ``print(WARNING); sys.exit(0)``, which wrote
    no bundles, reported success, and left Phase 3 pointed at ``_bundle_*.md``
    files that do not exist.  A transform that loses everything must be loud:
    exiting 0 with zero output is the same defect class as the h3-only parse.
    """

    def __init__(self, analysis_dir: Path, entries: list[str]) -> None:
        self.analysis_dir = analysis_dir
        super().__init__(
            f"No group analysis found in {analysis_dir}. Expected either "
            "{group}/ directories of section files (current layout) or "
            "{group}.md files (legacy layout). Found instead: "
            f"{entries[:12] or 'nothing at all'}. Phase 2 did not write its "
            "analyses, or wrote them somewhere else -- Phase 3 must not run on "
            "bundles that were never built."
        )


class SectionFileContractError(RuntimeError):
    """A group DIRECTORY exists but holds no file named for a section.

    The directory-layout counterpart of :class:`SectionRecognitionError`, and
    loud for the same reason: an empty directory means the writer never wrote
    its sections, and files named for nothing on the contract mean its evidence
    is on disk under names nothing downstream can route.  Neither is an empty
    analysis, so neither may be reported as one.
    """

    def __init__(self, directory: Path, filenames: list[str]) -> None:
        self.directory = directory
        self.filenames = filenames
        if filenames:
            detail = (
                f"holds {len(filenames)} markdown file(s) but none is named for "
                f"a section: {sorted(filenames)[:12]}"
            )
        else:
            detail = "holds no section file at all"
        super().__init__(
            f"Analysis directory {directory.name}/ {detail}. Expected files named "
            f"for sections, e.g. {list(ALL_SECTIONS[:6])}, each ending in .md. "
            "This is lost evidence, not an empty analysis."
        )


class SectionRecognitionError(RuntimeError):
    """Not one canonical section name was recognised in substantive input.

    FLAT-LAYOUT guard.  The precise run-258 signature: every one of the 20 group
    analyses put its section names on ``## `` while this script matched only
    ``### ``, so parsing recognised ZERO canonical sections and every relevance
    test missed.  The bundles were written anyway and Phase 3 documented an
    estate it could not read, inventing code that was sitting on disk the whole
    time.

    Unlike a retention ratio this cannot false-positive: a genuine Phase 2
    analysis always contains at least one canonical section, whatever heading
    level or enumeration style its author chose.
    """

    def __init__(
        self,
        agent: str,
        input_bytes: int,
        all_sections: dict[str, list[tuple[str, str]]],
        groups: list[str],
    ) -> None:
        self.agent = agent
        self.input_bytes = input_bytes
        seen = sorted(
            {
                sec_name
                for group in groups
                for sec_name, _ in all_sections.get(group, [])
                if sec_name != PREAMBLE
            }
        )[:10]
        super().__init__(
            f"Bundle '{agent}': not one canonical section name was recognised in "
            f"{input_bytes} bytes of analysis across {len(groups)} group(s). "
            f"Section names found instead: {seen or 'none'}. "
            f"Expected names from ALL_SECTIONS, e.g. {list(ALL_SECTIONS[:6])}. "
            "Refusing to write a bundle that would deliver no evidence -- this is "
            "the defect that caused fabricated documentation in run 258."
        )


class BundleRetentionError(RuntimeError):
    """A filtered bundle dropped most of its input.

    Raised instead of writing a bundle that silently delivers almost nothing --
    the run-258 failure mode, where every section name was misparsed, every
    relevance test missed, and Phase 3 wrote documentation from an empty bundle.
    """

    def __init__(self, agent: str, retained_bytes: int, input_bytes: int) -> None:
        self.agent = agent
        self.retained_bytes = retained_bytes
        self.input_bytes = input_bytes
        ratio = (retained_bytes / input_bytes) if input_bytes else 0.0
        super().__init__(
            f"Bundle '{agent}' retained {retained_bytes} of {input_bytes} analysis bytes "
            f"({ratio:.1%}), below the {MIN_RETENTION_RATIO:.0%} floor. "
            "Check the section file names under _analysis/{group}/ (directory "
            "layout) or the headings in _analysis/{group}.md (legacy layout) "
            "against ALL_SECTIONS before publishing anything built from them."
        )


# ───────────────────────────────────────────────────────────────────────────
# I/O primitives
# ───────────────────────────────────────────────────────────────────────────


def _read_text(path: Path) -> str:
    """Read a file without translating its line endings.

    ``Path.read_text`` opens in universal-newline mode, which rewrites ``\\r\\n``
    to ``\\n`` -- a silent edit of the model's bytes before they reach the next
    phase.  ``newline=""`` disables it, so the bundle carries what the writer
    wrote.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: Path, content: str) -> None:
    """Write a file without translating its line endings (see :func:`_read_text`)."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


# ───────────────────────────────────────────────────────────────────────────
# DIRECTORY LAYOUT -- file selection.  No parsing happens below this line.
# ───────────────────────────────────────────────────────────────────────────


def _render_section(name: str, body: str) -> str:
    """Render one section file as ``### NAME`` + its body.

    The heading is added for the READER: a Phase 3 agent needs to know which
    section it is looking at, and this is the same shape
    ``analysis_store.read_group_analysis`` renders, so the two agree.  Nothing
    downstream parses it back out of a directory-layout bundle.
    """
    title = name.replace("\r", " ").replace("\n", " ").strip()
    text = body.strip()
    return f"{_RENDER_HEADING} {title}\n\n{text}\n\n" if text else f"{_RENDER_HEADING} {title}\n\n"


def load_directory_sections(directory: Path) -> list[tuple[str, str]]:
    """Select a group's section files: ``[(section name, rendered text), ...]``.

    THE FILE NAME IS THE CONTRACT.  A stem that is on ``ALL_SECTIONS`` is that
    section, in contract order.  Everything else is an ``*.md`` file the writer
    put there under a name nothing can route, so it is carried into every bundle
    as unclassified content and REPORTED -- never silently dropped, because a
    file Python does not recognise is still evidence somebody wrote.

    The stem is compared exactly.  This script deliberately does not fold
    ``components.md`` onto ``COMPONENTS``: the prompt states the name in capitals
    is the contract, and a normaliser here is a second, drifting copy of the
    naming rule.  An off-contract name therefore costs targeting (the file
    reaches every agent instead of the right one) and is named on stdout -- it
    costs no evidence.

    Raises :class:`SectionFileContractError` when the directory is empty or when
    no file in it is named for a section.
    """
    files = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix == ".md"
            and not path.name.startswith(("_", "."))
        ),
        key=lambda path: path.name,
    )
    canonical = [path for path in files if path.stem in _CANONICAL_SECTIONS]
    extras = [path for path in files if path.stem not in _CANONICAL_SECTIONS]
    if not canonical:
        raise SectionFileContractError(directory, [path.name for path in files])

    sections = [
        (path.stem, _render_section(path.stem, _read_text(path)))
        for path in sorted(canonical, key=lambda path: _SECTION_ORDER[path.stem])
    ]
    for path in extras:
        print(
            f"  {directory.name}/{path.name}: not a section name on the contract "
            "-- carried unclassified into every bundle, not dropped"
        )
        sections.append((PREAMBLE, _render_section(path.stem, _read_text(path))))
    return sections


# ───────────────────────────────────────────────────────────────────────────
# FLAT LAYOUT -- heading parsing.  Legacy only; slice 5 deletes this block
# along with the flat layout itself.  Nothing above or below it parses prose.
# ───────────────────────────────────────────────────────────────────────────

#: HEADING RECOGNITION v1.  A section name is recognised at ANY heading level.
#: Phase 2 analyses put the canonical names on ``## `` (often with an enumeration
#: prefix, e.g. ``## 4. TRANSFORMATION_DETAIL``); the guide asks for ``### ``.
#: Matching only one level silently discarded ~95% of the analysis (run 258).
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_ENUM_PREFIX_RE = re.compile(r"^\d+\s*[.)]\s*")
_NON_NAME_RE = re.compile(r"[^A-Z0-9_]+")
_FENCE_RE = re.compile(r"^(?:```|~~~)")


def normalize_section_name(header: str) -> str:
    """Normalise a heading title to a comparable section name.  FLAT LAYOUT ONLY.

    ``4. TRANSFORMATION_DETAIL`` -> ``TRANSFORMATION_DETAIL``;
    ``Job: afasReader`` -> ``JOB_AFASREADER``.
    """
    name = _ENUM_PREFIX_RE.sub("", header.strip()).upper()
    name = _NON_NAME_RE.sub("_", name)
    return name.strip("_")


def section_name_for_heading(line: str) -> str | None:
    """Return the canonical section name a markdown line opens, else ``None``.

    FLAT LAYOUT ONLY.  Exact equality against ALL_SECTIONS.  The previous
    substring match ran in both directions, so ``### LOW`` resolved to
    ``EXECUTION_FLOW`` *or* ``DATA_FLOWS`` depending on frozenset iteration
    order -- a different answer per process, i.e. non-deterministic bundles.
    """
    match = _HEADING_RE.match(line.strip())
    if match is None:
        return None
    name = normalize_section_name(match.group(1))
    return name if name in _CANONICAL_SECTIONS else None


def parse_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content on canonical section headings, at any level.

    FLAT LAYOUT ONLY.  Returns (section_name, section_text) pairs in document
    order; section_text includes the heading line.  The split is lossless: every
    input line lands in exactly one section, so the section byte totals equal the
    input size.

    A heading that is not a canonical section name is NOT a boundary -- it stays
    inside the section it is nested under (``### LOW`` under ``## RISK_FLAGS``,
    ``### Job: X`` under ``## COMPONENTS``).  Headings inside fenced code blocks
    are ignored entirely.
    """
    sections: list[tuple[str, str]] = []
    current_name = PREAMBLE
    current_lines: list[str] = []
    in_fence = False
    for line in content.splitlines(keepends=True):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            current_lines.append(line)
            continue
        name = None if in_fence else section_name_for_heading(line)
        if name is not None:
            if current_lines:
                sections.append((current_name, "".join(current_lines)))
            current_name = name
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_name, "".join(current_lines)))
    return sections


# ───────────────────────────────────────────────────────────────────────────
# Layout-agnostic loading
# ───────────────────────────────────────────────────────────────────────────


def discover_groups(analysis_dir: Path) -> dict[str, str]:
    """Map every stored group to its layout, sorted by group name.

    The DIRECTORY wins when both shapes exist for one group: a migrated project
    keeps its old flat file on disk until slice 5 removes it, and the migrated
    copy is the current one.

    ``{group}_summary.md`` is NOT a group.  Counting it as one is the live defect
    that reported 30 analyses for a 20-group estate and shipped documents with a
    quarter of the estate missing.  ``_``/``.``-prefixed entries are internal
    (``_bundle_*``, ``_index.md``, ``_coverage_units/``, ``_context_packets/``).

    EVERY other subdirectory is a group, including an empty one.
    ``analysis_store.list_groups`` requires a ``.md`` child before it will name a
    directory; that is right for an enumerator, which must not explode while
    counting.  Here the directory is about to become the evidence Phase 3 reads,
    and a worker that ran ``mkdir -p {group}/`` and then died wrote no analysis at
    all -- skipping it silently is how a missing group reaches the documents
    unnamed.  It is reported by :class:`SectionFileContractError` at load.
    """
    if not analysis_dir.is_dir():
        return {}

    layouts: dict[str, str] = {}
    for entry in sorted(analysis_dir.iterdir(), key=lambda path: path.name):
        name = entry.name
        if name.startswith(("_", ".")):
            continue
        if entry.is_dir():
            layouts[name] = DIRECTORY_LAYOUT
            continue
        if entry.is_file() and name.endswith(".md"):
            stem = name[: -len(".md")]
            if stem.endswith(FLAT_SUMMARY_SUFFIX):
                continue
            layouts.setdefault(stem, FLAT_LAYOUT)
    return {group: layouts[group] for group in sorted(layouts)}


def load_group_sections(
    analysis_dir: Path, group: str, layout: str
) -> list[tuple[str, str]]:
    """Load one group's sections from whichever layout stores it."""
    if layout == DIRECTORY_LAYOUT:
        return load_directory_sections(analysis_dir / group)
    return parse_sections(_read_text(analysis_dir / f"{group}.md"))


def summary_path(analysis_dir: Path, group: str, layout: str) -> Path | None:
    """The group's summary file, or ``None`` when it has none.

    A directory group falls back to the flat ``{group}_summary.md`` beside it,
    the same order ``analysis_store.read_summary`` uses: a project migrated
    mid-flight has its sections in the directory and its summary still outside
    it, and dropping the summary there would leave the bundle header and the
    index blank for a group that has one.
    """
    if layout == DIRECTORY_LAYOUT:
        path = analysis_dir / group / SUMMARY_FILENAME
        if path.is_file():
            return path
    path = analysis_dir / f"{group}{FLAT_SUMMARY_SUFFIX}.md"
    return path if path.is_file() else None


def load_summary(analysis_dir: Path, group: str, layout: str) -> str:
    """Read the group summary from whichever layout stores it; ``""`` if none."""
    path = summary_path(analysis_dir, group, layout)
    return _read_text(path).strip() if path is not None else ""


# ───────────────────────────────────────────────────────────────────────────
# Bundle assembly (layout-agnostic: it only ever sees (name, text) pairs)
# ───────────────────────────────────────────────────────────────────────────


def truncate_section(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines + 1:
        return text
    omitted = len(lines) - max_lines - 1
    return "".join(lines[: max_lines + 1]) + f"\n... ({omitted} lines omitted)\n"


def _build_metadata_section(
    groups: list[str],
    summaries: dict[str, str],
    total_bytes: int,
    quick_scan_path: Path | None,
) -> str:
    """Build the METADATA and SUMMARIES header block for a bundle."""
    parts = [
        "## METADATA\n",
        f"Groups: {', '.join(sorted(groups))}\n",
        f"Total analysis size: {total_bytes / 1024:.0f}KB\n\n",
    ]

    if quick_scan_path and quick_scan_path.exists():
        # Include critical flags from quick scan
        qs = _read_text(quick_scan_path)
        critical_lines = [
            ln for ln in qs.splitlines()
            if any(tag in ln for tag in ("CRITICAL", "HIGH", "god_profile", "hardcoded"))
        ][:20]
        if critical_lines:
            parts.append("**Quick scan highlights:**\n")
            for ln in critical_lines:
                parts.append(f"  {ln}\n")
            parts.append("\n")

    parts.append("## SUMMARIES\n")
    for group in sorted(groups):
        s = summaries.get(group, "")
        if s:
            parts.append(f"\n### {group}\n{s}\n")
        else:
            parts.append(f"\n### {group}\n(no summary available - see DETAIL section)\n")

    parts.append("\n---\n\n## DETAIL\n\n")
    return "".join(parts)


def _is_relevant(agent: str, section_name: str) -> bool:
    """Would ``agent``'s bundle carry this section at full detail?"""
    if section_name == PREAMBLE:
        return True
    rel = RELEVANCE.get(agent)
    if rel is None:
        return True
    return section_name in rel["high"] or section_name in rel["medium"]


def measure_retention(
    agent: str,
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
) -> tuple[int, int]:
    """Return (retained_bytes, input_bytes) for an agent across ALL its groups.

    ``retained`` is what section selection carries through, measured before the
    MAX_BUNDLE_BYTES size management runs: a bundle that is split in two or has
    its medium sections truncated has still *selected* the same evidence, and a
    legitimate size-driven split must not read as a retention failure.  What this
    measures is the failure that produced fabricated documentation -- selection
    matching nothing at all.

    Identical arithmetic on both layouts: bytes copied over bytes available.
    """
    retained = 0
    total = 0
    for group in sorted(groups):
        for sec_name, sec_text in all_sections.get(group, []):
            size = len(sec_text.encode("utf-8"))
            total += size
            if _is_relevant(agent, sec_name):
                retained += size
    return retained, total


def _build_bundle_content(
    agent: str,
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    total_bytes: int,
    summaries: dict[str, str],
    quick_scan_path: Path | None,
) -> str:
    rel = RELEVANCE.get(agent)
    include_all = rel is None
    high = rel["high"] if rel else set()
    medium = rel["medium"] if rel else set()

    header = f"# Analysis Bundle: {agent}\n"
    metadata = _build_metadata_section(list(groups), summaries, total_bytes, quick_scan_path)

    def _collect(
        full_high: bool = True,
        full_medium: bool = True,
        include_medium: bool = True,
    ) -> str:
        detail_parts: list[str] = []
        for group in sorted(groups):
            detail_parts.append(f"\n## Group: {group}\n\n")
            for sec_name, sec_text in all_sections.get(group, []):
                if sec_name == PREAMBLE:
                    detail_parts.append(sec_text)
                elif include_all:
                    detail_parts.append(sec_text + "\n")
                elif sec_name in high:
                    detail_parts.append(
                        (sec_text if full_high else truncate_section(sec_text)) + "\n"
                    )
                elif sec_name in medium and include_medium:
                    detail_parts.append(
                        (sec_text if full_medium else truncate_section(sec_text)) + "\n"
                    )
        return header + metadata + "".join(detail_parts)

    content = _collect()
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES or include_all:
        return content

    content = _collect(full_medium=False)
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES:
        return content

    return _collect(include_medium=False)


def _split_groups(groups: list[str]) -> tuple[list[str], list[str]]:
    """Split group list in half for bundle overflow handling."""
    mid = max(1, len(groups) // 2)
    return sorted(groups)[:mid], sorted(groups)[mid:]


def write_bundles(
    agent: str,
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    total_bytes: int,
    summaries: dict[str, str],
    quick_scan_path: Path | None,
    analysis_dir: Path,
) -> list[str]:
    """Write bundle file(s) for an agent. Returns list of written filenames.

    Two guards run before anything is written, in order of precision:

    1. ``SectionRecognitionError`` -- not one canonical section name was found in
       substantive input.  This is the run-258 signature exactly, and it has no
       legitimate cause: a real analysis always contains at least one canonical
       section, so this check cannot false-positive on a valid run.  On the
       directory path it is unreachable, because ``load_directory_sections``
       already refused to return a group whose files are named for nothing
       (``SectionFileContractError``, raised earlier and more precisely).
    2. ``BundleRetentionError`` -- a *filtered* agent carries less than
       MIN_RETENTION_RATIO of its groups' bytes.  A blunter catastrophe floor.
       ``include_all`` agents are exempt; they keep everything by definition.
    """
    recognised = sum(
        1
        for group in groups
        for sec_name, _ in all_sections.get(group, [])
        if sec_name in _CANONICAL_SECTIONS
    )
    _, input_bytes = measure_retention(agent, groups, all_sections)
    if recognised == 0 and input_bytes > _SUBSTANTIVE_INPUT_BYTES:
        raise SectionRecognitionError(agent, input_bytes, all_sections, groups)

    retained, input_bytes = measure_retention(agent, groups, all_sections)
    if RELEVANCE.get(agent) is not None and input_bytes > 0:
        print(
            f"Bundle {agent}: retention {retained}/{input_bytes} bytes "
            f"({retained / input_bytes:.1%}), {recognised} canonical sections"
        )
        if retained < MIN_RETENTION_RATIO * input_bytes:
            raise BundleRetentionError(agent, retained, input_bytes)

    content = _build_bundle_content(
        agent, groups, all_sections, total_bytes, summaries, quick_scan_path,
    )
    size = len(content.encode("utf-8"))
    written: list[str] = []

    if size <= MAX_BUNDLE_BYTES or len(groups) <= 1:
        path = analysis_dir / f"_bundle_{agent}.md"
        _write_text(path, content)
        print(f"Bundle {agent}: {size / 1024:.1f}KB")
        written.append(path.name)
    else:
        # Auto-split: divide groups between _a and _b
        groups_a, groups_b = _split_groups(groups)
        for suffix, grp_list in [("a", groups_a), ("b", groups_b)]:
            c = _build_bundle_content(
                agent, grp_list, all_sections, total_bytes, summaries, quick_scan_path,
            )
            p = analysis_dir / f"_bundle_{agent}_{suffix}.md"
            _write_text(p, c)
            kb = len(c.encode("utf-8")) / 1024
            print(f"Bundle {agent}_{suffix}: {kb:.1f}KB ({len(grp_list)} groups)")
            written.append(p.name)

    return written


def write_index(
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    summaries: dict[str, str],
    quick_scan_path: Path | None,
    analysis_dir: Path,
    layouts: dict[str, str] | None = None,
) -> None:
    """Write _analysis/_index.md for Phase 3 agents to navigate quickly.

    The "Analysis" cell must name what is actually on disk: a directory-layout
    group is a folder of section files, and telling an agent to open
    ``{group}.md`` sends it to a path that does not exist.
    """
    layouts = layouts or {}
    lines = [
        "# Analysis Index\n\n",
        "Phase 3 agents: read this first to locate sections you need.\n",
        "Load `_index.md`, then load only the specific group detail you need.\n\n",
        "| Group | Summary File | Analysis | Quick Scan Flags |\n",
        "|-------|-------------|----------|------------------|\n",
    ]

    qs_flags: dict[str, list[str]] = {}
    if quick_scan_path and quick_scan_path.exists():
        for ln in _read_text(quick_scan_path).splitlines():
            for g in groups:
                if g in ln and any(t in ln for t in ("CRITICAL", "HIGH", "god_profile", "hardcoded")):
                    qs_flags.setdefault(g, []).append(ln.strip()[:80])

    for group in sorted(groups):
        layout = layouts.get(group, FLAT_LAYOUT)
        if layout == DIRECTORY_LAYOUT:
            named = [name for name, _ in all_sections.get(group, []) if name != PREAMBLE]
            analysis_file = f"{group}/ ({len(named)} section files)"
        else:
            analysis_file = f"{group}.md"
        # Name the summary file that EXISTS, not the one this layout usually has:
        # a group migrated mid-flight keeps its summary outside its directory.
        path = summary_path(analysis_dir, group, layout) if summaries.get(group) else None
        summary_file = (
            path.relative_to(analysis_dir).as_posix() if path is not None else "-"
        )
        flags = "; ".join(qs_flags.get(group, [])[:3]) or "-"
        lines.append(f"| {group} | {summary_file} | {analysis_file} | {flags} |\n")

    _write_text(analysis_dir / "_index.md", "".join(lines))
    print(f"Index written: {len(groups)} groups")


def main() -> None:
    staging_dir = Path(sys.argv[1])
    total_jobs = int(sys.argv[2])
    large_threshold = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    analysis_dir = staging_dir / "_analysis"
    quick_scan_path = staging_dir / "_assessment" / "quick_scan.md"

    layouts = discover_groups(analysis_dir)
    if not layouts:
        entries = (
            sorted(p.name for p in analysis_dir.iterdir()) if analysis_dir.is_dir() else []
        )
        raise MissingAnalysisError(analysis_dir, entries)

    all_sections: dict[str, list[tuple[str, str]]] = {}
    summaries: dict[str, str] = {}
    total_bytes = 0
    for group, layout in layouts.items():
        sections = load_group_sections(analysis_dir, group, layout)
        all_sections[group] = sections
        summaries[group] = load_summary(analysis_dir, group, layout)
        total_bytes += sum(len(text.encode("utf-8")) for _, text in sections)

    groups = list(all_sections)
    directory_groups = sum(1 for layout in layouts.values() if layout == DIRECTORY_LAYOUT)
    print(
        f"Groups: {len(groups)} ({directory_groups} directory, "
        f"{len(groups) - directory_groups} flat)"
    )

    is_large = total_jobs >= large_threshold
    agents = (
        ["core", "business_ops", "patterns", "user"]
        if is_large
        else ["core", "business_ops", "patterns_user"]
    )

    all_written: list[str] = []
    for agent in agents:
        written = write_bundles(
            agent, groups, all_sections, total_bytes, summaries, quick_scan_path, analysis_dir,
        )
        all_written.extend(written)

    write_index(groups, all_sections, summaries, quick_scan_path, analysis_dir, layouts)

    print(
        f"Total: {total_bytes / 1024:.1f}KB, agents: {len(agents)}, "
        f"large_project: {is_large}, bundles: {len(all_written)}"
    )


if __name__ == "__main__":
    main()
