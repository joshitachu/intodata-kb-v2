"""Repository over Phase 2 group analyses -- one reader, two layouts.

Phase 2 stores a group's analysis in one of two shapes, and no caller should
have to know which:

``FLAT`` (legacy, on disk in every project generated before slice 2)::

    _analysis/fourps.md              # every section as a heading inside prose
    _analysis/fourps_summary.md

``DIRECTORY`` (target, plan decision 2)::

    _analysis/fourps/COMPONENTS.md
    _analysis/fourps/TRANSFORMATION_DETAIL.md
    _analysis/fourps/SOURCE_SQL.md
    _analysis/fourps/_summary.md

The directory layout exists to delete a parse.  In the flat layout the model's
chosen heading shape is load-bearing: run 258 asked for ``### COMPONENTS``, the
model wrote ``## COMPONENTS``, every consumer matched zero sections and ~95% of
the evidence was dropped with no warning at all.  In the directory layout a
missing section is a missing FILE, which cannot be confused with an empty parse.

INVARIANTS
----------
1. **A transform that loses everything must be loud.**  A group directory that
   exists but resolves to no known section raises
   :class:`~mcp_server.providers.custom.section_parser.SectionContractError` --
   the same exception type the heading parser raises for the same reason, on
   purpose.  Returning ``{}`` or ``""`` there is the defect, not the report of
   it.
2. **Absent and empty are different facts.**  ``read_section`` returns ``None``
   for a section that was never written and ``""`` for one that was written
   empty.  Callers depend on the difference: "the writer said there is no SQL
   here" is evidence; "nobody looked" is a gap.
3. **Nothing is silently dropped.**  A section file whose name is not on the
   contract is still reported and still read, after the canonical sections.
   Two files that normalise to the same section name are concatenated in
   filename order, never deduplicated away.
4. **Deterministic and byte-stable across processes.**  Every listing is
   explicitly sorted; no set iteration order reaches an output.  This repo has
   shipped an artifact that was not byte-stable across processes before (a set
   sorted on ``casefold`` alone), which silently voided a provider prompt cache.
5. **The store never edits the model's bytes.**  ``write_section`` writes
   exactly what it is given and ``read_section`` returns exactly what is on
   disk, so a round trip is byte-exact.

Section names come from
:data:`~mcp_server.providers.custom.analysis_parser.PHASE2_SECTION_NAMES` and
flat-layout parsing is delegated to
:func:`~mcp_server.providers.custom.analysis_parser.parse_phase2_sections`.
Neither is re-implemented here: three duplicated heading parsers is what this
lane is removing.

KNOWN ASYMMETRIES between the two layouts (they matter to slices 2-5):

* A flat section cannot be empty.  ``parse_phase2_sections`` drops sections
  whose body is blank, so ``read_section`` returns ``None`` for an empty flat
  section and ``""`` for an empty section file.
* Flat section bodies are ``strip()``ped by the parser; directory section
  bodies are returned verbatim.
* Text before the first recognised heading in a flat file (the ``# Analysis:
  <group>`` title, an "Executive Summary" preamble) is parsed into
  ``UNCLASSIFIED_OR_EXTRA_FINDINGS``.  The directory layout has no preamble
  position at all, so a flat file will commonly report one section a directory
  never would.
* ``read_group_analysis`` returns a flat file verbatim but RENDERS a directory
  (``### NAME`` per section, canonical order).  The two are equivalent under
  ``parse_phase2_sections``, not byte-identical.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from mcp_server.providers.custom.analysis_parser import (
    PHASE2_SECTION_NAMES,
    parse_phase2_sections,
)
from mcp_server.providers.custom.section_parser import (
    SectionContractError,
    normalise_heading,
)

__all__ = [
    "SUMMARY_FILENAME",
    "AnalysisLayout",
    "AnalysisPathError",
    "SectionContractError",
    "canonical_section_names",
    "group_dir",
    "layout_of",
    "list_groups",
    "list_sections",
    "read_group_analysis",
    "read_section",
    "read_summary",
    "section_path",
    "summary_path",
    "write_section",
    "write_summary",
]

#: The group summary inside a group DIRECTORY.
SUMMARY_FILENAME = "_summary.md"
#: The group summary in the FLAT layout: ``{group}_summary.md``.
FLAT_SUMMARY_SUFFIX = "_summary"

#: Heading level used when rendering a directory back into one text blob.
#: Any level parses (see ``analysis_parser``); h3 is what the guide asks for.
_RENDER_HEADING = "###"

#: Canonical order == declaration order of the contract.  A dict, not a set:
#: the ORDER is the output contract and a set would make it process-dependent.
_CANONICAL_ORDER: dict[str, int] = {
    name: index for index, name in enumerate(PHASE2_SECTION_NAMES)
}


class AnalysisLayout(str, Enum):
    """How one group's analysis is stored."""

    DIRECTORY = "directory"
    FLAT = "flat"
    ABSENT = "absent"


class AnalysisPathError(ValueError):
    """A group or section name that must not be turned into a path."""


def canonical_section_names() -> tuple[str, ...]:
    """The contract's section names, in contract order."""
    return PHASE2_SECTION_NAMES


# ── path safety ─────────────────────────────────────────────────────────────


def _safe_component(name: str, kind: str) -> str:
    """Return ``name`` if it is a single, non-traversing path component.

    A group name arrives from the facts manifest and a section name can arrive
    from a caller; as far as the filesystem is concerned both are untrusted.
    ``PurePosixPath``/``PureWindowsPath`` are consulted together so that
    ``a/b``, ``a\\b``, ``C:x`` and absolute paths are all rejected on every
    platform, not just on the one the check happens to run on.
    """
    if not isinstance(name, str) or not name.strip():
        raise AnalysisPathError(f"{kind} name must be a non-empty string, got {name!r}")
    if name != name.strip():
        raise AnalysisPathError(f"{kind} name has leading/trailing whitespace: {name!r}")
    if name.startswith("."):
        raise AnalysisPathError(f"{kind} name may not start with a dot: {name!r}")
    if "\x00" in name:
        raise AnalysisPathError(f"{kind} name contains a NUL byte: {name!r}")
    if PurePosixPath(name).name != name or PureWindowsPath(name).name != name:
        raise AnalysisPathError(
            f"{kind} name must be a single path component, got {name!r}"
        )
    return name


def group_dir(analysis_dir: Path, group: str) -> Path:
    """Path of the group's DIRECTORY-layout folder (it need not exist)."""
    return Path(analysis_dir) / _safe_component(group, "group")


def _flat_path(analysis_dir: Path, group: str) -> Path:
    return Path(analysis_dir) / f"{_safe_component(group, 'group')}.md"


def _flat_summary_path(analysis_dir: Path, group: str) -> Path:
    safe = _safe_component(group, "group")
    return Path(analysis_dir) / f"{safe}{FLAT_SUMMARY_SUFFIX}.md"


def section_path(analysis_dir: Path, group: str, section: str) -> Path:
    """Path of one section file in the DIRECTORY layout (it need not exist)."""
    name = _require_section_name(section)
    return group_dir(analysis_dir, group) / f"{_safe_component(name, 'section')}.md"


def summary_path(analysis_dir: Path, group: str) -> Path:
    """Path of the group summary in the DIRECTORY layout."""
    return group_dir(analysis_dir, group) / SUMMARY_FILENAME


# ── section-name normalisation ──────────────────────────────────────────────


def _normalise_name(raw: str) -> str | None:
    """Normalise a section NAME with the same rule that normalises a heading.

    Presenting the stem as a heading line is deliberate: a second normaliser is
    exactly how ``components.md`` and ``### COMPONENTS`` end up as two different
    sections.  One rule, one spec (``section_parser`` module docstring).
    """
    if not isinstance(raw, str):
        return None
    return normalise_heading(f"# {raw.strip()}")


def _require_section_name(section: str) -> str:
    name = _normalise_name(section)
    if name is None:
        raise AnalysisPathError(f"not a usable section name: {section!r}")
    return name


def _order_key(name: str) -> tuple[int, str]:
    """Canonical sections first in contract order, then extras alphabetically."""
    return (_CANONICAL_ORDER.get(name, len(_CANONICAL_ORDER)), name)


# ── layout detection ────────────────────────────────────────────────────────


def layout_of(analysis_dir: Path, group: str) -> AnalysisLayout:
    """Which layout stores ``group``.

    The directory wins when both exist: a project that has been migrated keeps
    its old flat file on disk until slice 5 removes it, and the migrated copy is
    the one that is current.  Callers should log this so a run states which
    layout it read.
    """
    if group_dir(analysis_dir, group).is_dir():
        return AnalysisLayout.DIRECTORY
    if _flat_path(analysis_dir, group).is_file():
        return AnalysisLayout.FLAT
    return AnalysisLayout.ABSENT


def list_groups(analysis_dir: Path) -> tuple[str, ...]:
    """Every group with a stored analysis, sorted, each named once.

    ``{group}_summary.md`` is NOT a group.  Counting it as one is the live
    defect in ``_detect_resume_state``: 15 analysed groups were reported as 30,
    the prompt told the model 30 of 20 groups were already complete, and five
    groups were never analysed at all.

    A summary with no analysis beside it is likewise not a group -- there is no
    analysis to read.  Underscore- and dot-prefixed entries are internal
    (``_bundle_*``, ``_doc_pack_*``, ``_index.md``, ``_coverage_units/``), and a
    directory with no ``.md`` file in it is not an analysis.
    """
    directory = Path(analysis_dir)
    if not directory.is_dir():
        return ()

    groups: set[str] = set()
    for entry in directory.iterdir():
        name = entry.name
        if name.startswith(("_", ".")):
            continue
        if entry.is_dir():
            if any(child.suffix == ".md" for child in entry.iterdir() if child.is_file()):
                groups.add(name)
            continue
        if entry.is_file() and name.endswith(".md"):
            stem = name[: -len(".md")]
            if stem.endswith(FLAT_SUMMARY_SUFFIX):
                continue
            groups.add(stem)
    return tuple(sorted(groups))


# ── directory layout internals ──────────────────────────────────────────────


def _directory_section_files(directory: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    """Return ``({section name: files}, all candidate files)`` for a group dir.

    Candidates are ``*.md`` files whose name does not start with ``_`` (that
    prefix is reserved for ``_summary.md`` and any future internal file).  A
    name that normalises to nothing keeps its raw stem so that it is still
    reported rather than disappearing.
    """
    by_name: dict[str, list[Path]] = {}
    candidates: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.suffix != ".md" or path.name.startswith("_"):
            continue
        candidates.append(path)
        name = _normalise_name(path.stem) or path.stem
        by_name.setdefault(name, []).append(path)
    return by_name, candidates


def _no_known_section_error(directory: Path, candidates: list[Path]) -> SectionContractError:
    seen = sorted(path.name for path in candidates)
    return SectionContractError(
        f"No known analysis section was found in {directory.name}/ although it "
        f"holds {len(candidates)} markdown file(s): {seen[:12]}. Expected files "
        f"named for sections such as {sorted(PHASE2_SECTION_NAMES)[:6]}. This is "
        "lost evidence, not an empty analysis."
    )


def _empty_directory_error(directory: Path) -> SectionContractError:
    return SectionContractError(
        f"{directory.name}/ exists but holds no section file. An analysis "
        "directory with nothing in it means the writer never wrote its sections "
        "-- it is not an empty analysis."
    )


def _read_text(path: Path) -> str:
    """Read a file without translating its line endings.

    ``Path.read_text`` opens in universal-newline mode, which rewrites ``\\r\\n``
    to ``\\n`` -- a silent edit of the model's bytes, and one that matters in a
    codebase that ships Windows-authored content.  ``newline=""`` disables it.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: Path, content: str) -> None:
    """Write a file without translating its line endings (see :func:`_read_text`)."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _read_files(paths: list[Path]) -> str:
    """Read one or more files that resolve to the same section.

    One file is returned byte-exact.  Two files that normalise to the same name
    (``SOURCE_SQL.md`` and ``source sql.md``) are concatenated in filename order
    rather than one of them being dropped.
    """
    if len(paths) == 1:
        return _read_text(paths[0])
    return "\n\n".join(_read_text(path).strip("\n") for path in paths)


# ── reads ───────────────────────────────────────────────────────────────────


def list_sections(analysis_dir: Path, group: str) -> tuple[str, ...]:
    """Section names stored for ``group``, canonical order first, then extras.

    From the directory LISTING when the group is stored as a directory; by
    parsing only when it is stored flat.  An absent group has no sections and
    reports ``()`` -- use :func:`layout_of` to tell absent from empty.

    Raises :class:`SectionContractError` when files exist but none of them
    resolves to a known section: a listing that reports zero sections next to
    files on disk is a lie, and that lie is the run-258 failure.
    """
    layout = layout_of(analysis_dir, group)
    if layout is AnalysisLayout.ABSENT:
        return ()
    if layout is AnalysisLayout.DIRECTORY:
        directory = group_dir(analysis_dir, group)
        by_name, candidates = _directory_section_files(directory)
        if candidates and not any(name in _CANONICAL_ORDER for name in by_name):
            raise _no_known_section_error(directory, candidates)
        return tuple(sorted(by_name, key=_order_key))

    text = _read_text(_flat_path(analysis_dir, group))
    parsed = parse_phase2_sections(text, strict=True)
    return tuple(sorted(parsed, key=_order_key))


def read_section(analysis_dir: Path, group: str, section: str) -> str | None:
    """The stored text of one section, or ``None`` when it is genuinely absent.

    ``None`` means "never written".  ``""`` means "written empty" and is only
    reachable in the directory layout (see the asymmetries in the module
    docstring).
    """
    name = _require_section_name(section)
    layout = layout_of(analysis_dir, group)
    if layout is AnalysisLayout.ABSENT:
        return None
    if layout is AnalysisLayout.DIRECTORY:
        directory = group_dir(analysis_dir, group)
        by_name, candidates = _directory_section_files(directory)
        if candidates and not any(known in _CANONICAL_ORDER for known in by_name):
            raise _no_known_section_error(directory, candidates)
        paths = by_name.get(name)
        return None if paths is None else _read_files(paths)

    text = _read_text(_flat_path(analysis_dir, group))
    return parse_phase2_sections(text, strict=True).get(name)


def read_group_analysis(analysis_dir: Path, group: str) -> str:
    """The whole analysis for ``group`` as text, whichever layout stores it.

    Flat files are returned verbatim.  A directory is rendered with one
    ``### SECTION`` heading per file in canonical order, so a prose consumer --
    a bundler, a prompt context file, the patch lane -- sees text that parses to
    the same sections it would have parsed out of the flat file.

    Raises ``FileNotFoundError`` when the group has no analysis at all, and
    :class:`SectionContractError` when a directory exists but yields nothing.
    """
    layout = layout_of(analysis_dir, group)
    if layout is AnalysisLayout.ABSENT:
        raise FileNotFoundError(
            f"No analysis for group {group!r}: neither "
            f"{_flat_path(analysis_dir, group).name} nor "
            f"{group_dir(analysis_dir, group).name}/ exists in {analysis_dir}"
        )
    if layout is AnalysisLayout.FLAT:
        return _read_text(_flat_path(analysis_dir, group))

    directory = group_dir(analysis_dir, group)
    by_name, candidates = _directory_section_files(directory)
    if not candidates:
        raise _empty_directory_error(directory)
    if not any(name in _CANONICAL_ORDER for name in by_name):
        raise _no_known_section_error(directory, candidates)

    blocks: list[str] = []
    for name in sorted(by_name, key=_order_key):
        body = _read_files(by_name[name]).strip()
        blocks.append(f"{_RENDER_HEADING} {name}\n\n{body}" if body else f"{_RENDER_HEADING} {name}")
    return "\n\n".join(blocks) + "\n"


def read_summary(analysis_dir: Path, group: str) -> str | None:
    """The group summary from either layout, or ``None`` when there is none."""
    directory_summary = summary_path(analysis_dir, group)
    if directory_summary.is_file():
        return _read_text(directory_summary)
    flat_summary = _flat_summary_path(analysis_dir, group)
    if flat_summary.is_file():
        return _read_text(flat_summary)
    return None


# ── writes (directory layout only) ──────────────────────────────────────────


def write_section(analysis_dir: Path, group: str, section: str, content: str) -> Path:
    """Write one section file and return its path.

    The writer only ever produces the DIRECTORY layout; the flat layout is
    read-only legacy.  Content is written exactly as given -- the store does not
    add a heading, a trailing newline, or anything else to the model's bytes.
    """
    path = section_path(analysis_dir, group, section)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content)
    return path


def write_summary(analysis_dir: Path, group: str, content: str) -> Path:
    """Write the group's ``_summary.md`` and return its path."""
    path = summary_path(analysis_dir, group)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content)
    return path
