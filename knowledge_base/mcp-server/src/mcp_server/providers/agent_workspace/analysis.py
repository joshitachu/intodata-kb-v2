"""The analysis layer: one written analysis per group, before any document.

> The agent is free to choose how it investigates, but it is not free to choose
> what must be accounted for.

``ledger.py`` is that sentence applied to the ESTATE -- every parsed design ends
in one explicit state. This module is the same sentence applied to the WORK: a
group whose designs were never written up in the agent's own words has not been
investigated, whatever the documents say about it.

Why it exists
-------------

MEASURED (run 252, a 142-design estate). The agent read all four bootstrap
files, all five instruction files and four platform guides -- roughly 21 KB of
method -- then classified 142 designs in about ninety seconds having opened SIX
design files, and wrote seven documents straight from the index. Depth guidance
was not missed; it was read, and it did not change the behaviour. Prompting
harder is not the lever, because the prompt was already there.

The predecessor Claude Code workflow produced markedly deeper documentation,
and the mechanism was not a bigger prompt either: it required one analysis file
per group before any document could be written, so the prose layer could not be
reached without passing through the evidence layer.

This restores that obligation WITHOUT restoring the orchestrator. There is no
phase driver here, no assigned worker, no batch, no ordering imposed by Nexus.
The agent chooses when to write these files, in what order, and whether to fan
out with its ``task`` tool. It simply cannot claim completion without them.

The population is recomputed, never read back from the notes
------------------------------------------------------------

:data:`~contract.ANALYSIS_DIR` is ``notes/analysis``, and ``notes/`` is
agent-writable. So the set of groups this module reports against is recomputed
from ``facts/navigation/units/part_*.jsonl`` on every call -- inside
``contract.EVIDENCE_ROOTS``, therefore inside the integrity digest the
publisher re-checks after the run. Deriving the group list from the files that
happen to be in ``notes/analysis/`` would mean an agent that wrote one file had
satisfied a population of one, which is a gate that measures nothing. Same
reasoning as ``ledger.roster_from_workspace``, and the same test pins it: delete
every analysis file and the group list does not move.

Names are matched EXACTLY, and only against what is really on disk
------------------------------------------------------------------

Files are discovered by listing the directory and keeping the names the
filesystem reports, then matching those names to groups by exact string
equality. Never ``(directory / f"{group}.md").is_file()``: on a
case-insensitive filesystem -- macOS, which is where this is developed -- that
probe answers ``True`` for ``billing`` when the only file on disk is
``Billing.md``, and one file would silently satisfy two different groups. Two
Talend groups differing in the case of one letter are two groups (``coverage.py``
records the production cases), and a group and its plural are two groups. The
collision is DETECTED and reported as a notice; the two groups are never merged
into one.

Nothing here raises on agent input
----------------------------------

An absent directory, a directory where a file was expected, an unreadable
entry, a file named after no known group: each is data for the report. An
exception on this path would turn one stray file into a failed run, which is
precisely the trade this lane refuses to make.

Nothing here reads a document's TEXT. Byte counts, file existence and group
names only -- plan section 8 forbids failing a run because prose uses a
slightly different job name, and a size is not a wording judgement.

Deterministic Python. No model, no network, no config.
"""

from __future__ import annotations

import json
import logging
import stat as stat_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.providers.agent_workspace.contract import ANALYSIS_DIR, NAVIGATION

logger = logging.getLogger(__name__)

#: Where the deterministic summary is persisted. Underscore-prefixed for the
#: same reason ``coverage.REACH_ARTIFACT`` and ``ledger.LEDGER_ARTIFACT`` are:
#: ``publish._is_visible`` and ``is_published_doc_filename`` both reject such a
#: segment, so run control state cannot reach a client document by being
#: forgotten in a cleanup list.
ANALYSIS_ARTIFACT = Path("_audit") / "analysis_coverage.json"

#: The navigation view that carries the group of every parsed design. Named
#: once; the whole recomputation rule collapses if a caller reads a different
#: view.
UNITS_VIEW = "units"

#: The extension an analysis file must carry. The convention is
#: ``notes/analysis/<group>.md`` and the group name is the whole stem.
ANALYSIS_SUFFIX = ".md"

#: A floor against an empty stub, and NOT a measure of quality. 500 bytes is
#: the number the legacy pipeline used for exactly this judgement:
#: ``EvidenceQualityGate.THIN_THRESHOLD_BYTES = 500`` on ``_preprocess/{group}.md``,
#: compared against ``stat().st_size`` and emitted as ``THIN_BUNDLE``. Reusing
#: it keeps two generations of this product describing "too thin to be an
#: investigation" with one number. Be honest about what it can tell you: a
#: 4 KB file can still be restated index rows, and no byte count will catch
#: that. It catches the heading-and-nothing-else case, which is the one that
#: was actually observed.
MIN_ANALYSIS_BYTES = 500

#: How many names a notice prints before it starts counting instead. Ten is
#: enough to recognise a pattern and short enough for a log line; the notice
#: always states the true total. Matches ``accountability.MAX_SUBJECTS``.
MAX_NAMED = 10

SCHEMA_VERSION = 1

#: Stated inside the artifact, because the file will outlive the person who
#: computed it and the recomputation rule is the whole design.
_POPULATION_NOTE = (
    "The group list below is recomputed from facts/navigation/units/part_*.jsonl, "
    "which is read-only evidence inside the integrity digest. It is NOT derived "
    "from what is present in notes/analysis/: a gate whose population comes from "
    "the files it is checking measures nothing."
)

#: Stated inside the artifact for the same reason, because a byte count invites
#: being read as a quality score.
_FLOOR_NOTE = (
    "A size floor is a guard against an empty stub, not a measure of quality. "
    "A file above it may still be shallow; a file below it is not an "
    "investigation."
)


@dataclass(frozen=True)
class AnalysisCoverage:
    """Which groups have a written analysis, and which do not.

    Every collection is sorted on a total key -- the exact group name, never a
    case-folded one -- so two reads of one workspace produce byte-identical
    output and an unchanged workspace does not move the artifact's bytes.
    """

    #: Every group in the recomputed population, sorted. The obligation.
    groups: tuple[str, ...] = ()
    #: group -> bytes on disk, for groups that HAVE a file. Insertion order is
    #: :attr:`groups` order, so the artifact is byte-stable.
    written: dict[str, int] = field(default_factory=dict)
    #: In the population, with no analysis file at all.
    missing: tuple[str, ...] = ()
    #: Present, but under :data:`MIN_ANALYSIS_BYTES`. A subset of
    #: :attr:`written`'s keys: a thin file is still a written file, and the two
    #: findings it drives have different severities.
    thin: tuple[str, ...] = ()
    #: Analysis files naming no known group. Reported, never counted as
    #: coverage -- crediting one would let a misspelling clear an obligation,
    #: the same rule as ``LedgerReport.unknown``.
    unexpected: tuple[str, ...] = ()
    #: Why something could not be counted, or why a count needs reading with
    #: care. Never fatal.
    notices: tuple[str, ...] = ()

    @property
    def substantial(self) -> tuple[str, ...]:
        """Groups with a file at or above the floor. The work that landed.

        Derived here rather than left to each consumer to recompute as
        ``written`` minus ``thin``: that subtraction is the floor's definition,
        and a second copy of it is free to drift from the one
        :func:`read_analysis_coverage` applied.
        """

        thin = set(self.thin)
        return tuple(group for group in self.groups if group in self.written and group not in thin)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": "nexus-analysis-coverage",
            "analysis_dir": ANALYSIS_DIR.as_posix(),
            "population_source": f"{(NAVIGATION / UNITS_VIEW).as_posix()}/part_*.jsonl",
            "population_is_recomputed": _POPULATION_NOTE,
            "minimum_bytes": MIN_ANALYSIS_BYTES,
            "minimum_bytes_is_a_floor": _FLOOR_NOTE,
            "group_total": len(self.groups),
            "written_total": len(self.written),
            "groups": list(self.groups),
            "written": dict(self.written),
            "missing": list(self.missing),
            "thin": list(self.thin),
            "unexpected": list(self.unexpected),
            "notices": list(self.notices),
        }

    def log_line(self) -> str:
        """One line for the run log, readable without this module open."""

        return (
            f"analysis layer: {len(self.written)} of {len(self.groups)} group(s) have an "
            f"analysis note, {len(self.missing)} with none, "
            f"{len(self.thin)} under {MIN_ANALYSIS_BYTES} bytes"
        )


# --------------------------------------------------------------------------
# The population
# --------------------------------------------------------------------------


def groups_from_workspace(staging_dir: Path) -> tuple[str, ...]:
    """Every group the scan parsed, recomputed from evidence. Sorted, distinct.

    Reads the ``units`` navigation shards directly rather than the manifest's
    row counts: the obligation is a set of names, not a total.

    A shard that cannot be read is logged and skipped rather than raised. That
    is not a hole: ``facts/`` is inside ``contract.EVIDENCE_ROOTS`` and
    therefore inside the digest the publisher re-checks after the run, so a
    deleted or edited shard fails publication there, naming the exact file and
    the exact digest mismatch -- strictly better information than an exception
    from here.
    """

    groups, _ = _group_population(Path(staging_dir))
    return groups


def _group_population(staging_dir: Path) -> tuple[tuple[str, ...], int]:
    """Every distinct group, sorted, plus how many designs carry no group.

    The ungrouped count is returned rather than discarded because zero groups
    on a real estate and zero groups on an empty one look identical from the
    group list alone, and the first one means the analysis obligation silently
    evaporated.

    Sorted on the exact string, never on ``casefold``: ``Billing`` and
    ``billing`` are two groups and must keep a total order between them.
    """

    directory = staging_dir / NAVIGATION / UNITS_VIEW
    seen: dict[str, None] = {}
    ungrouped = 0
    if not directory.is_dir():
        return (), ungrouped

    for shard in sorted(directory.glob("part_*.jsonl")):
        for row in _read_jsonl(shard):
            group = _text(row.get("group"))
            if not group:
                ungrouped += 1
                continue
            seen.setdefault(group, None)
    return tuple(sorted(seen)), ungrouped


# --------------------------------------------------------------------------
# What the agent wrote
# --------------------------------------------------------------------------


def read_analysis_coverage(staging_dir: Path) -> AnalysisCoverage:
    """Report the written analyses against the recomputed group population.

    Never raises. An absent ``notes/analysis/`` is the normal state of a run
    that has not started writing, and reports every group as missing.
    """

    staging_dir = Path(staging_dir)
    groups, ungrouped = _group_population(staging_dir)
    known = set(groups)

    files, scan_notices = _analysis_files(staging_dir)

    written = {group: files[group] for group in groups if group in files}
    missing = tuple(group for group in groups if group not in files)
    thin = tuple(group for group, size in written.items() if size < MIN_ANALYSIS_BYTES)
    unexpected = tuple(name for name in sorted(files) if name not in known)

    notices: list[str] = list(scan_notices)
    if ungrouped:
        notices.append(
            f"{ungrouped} parsed design(s) carry no group name, so no "
            f"{ANALYSIS_DIR.as_posix()}/<group>.md file covers them."
        )
    if not groups and not ungrouped:
        notices.append(
            f"{(NAVIGATION / UNITS_VIEW).as_posix()} holds no rows, so no analysis "
            "file is expected."
        )
    notices.extend(_case_collision_notices(groups, unexpected))

    return AnalysisCoverage(
        groups=groups,
        written=written,
        missing=missing,
        thin=thin,
        unexpected=unexpected,
        notices=tuple(notices),
    )


def write_analysis_artifact(staging_dir: Path, coverage: AnalysisCoverage) -> Path | None:
    """Persist the report into the run's audit record. Best effort.

    An unwritable artifact is logged and nothing else. The obligation's
    authority comes from the object the gate already holds; a summary file that
    could fail a run would make the audit trail a second gate, free to disagree
    with the first.
    """

    path = Path(staging_dir) / ANALYSIS_ARTIFACT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(coverage.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
    except OSError:
        logger.warning("Could not persist the agent-workspace analysis coverage report")
        return None
    return path


# --------------------------------------------------------------------------
# Reading the directory
# --------------------------------------------------------------------------


def _analysis_files(staging_dir: Path) -> tuple[dict[str, int], list[str]]:
    """``exact on-disk stem -> bytes``, plus notices for what was not counted.

    The names come from ``iterdir``, i.e. from the filesystem, and are compared
    to group names by exact equality. A ``(directory / f"{group}.md").is_file()``
    probe would be shorter and wrong: on a case-insensitive filesystem it
    answers ``True`` for ``billing`` while the only file on disk is
    ``Billing.md``, and one file would clear two groups' obligations.
    """

    directory = staging_dir / ANALYSIS_DIR
    notices: list[str] = []
    if not directory.is_dir():
        notices.append(
            f"{ANALYSIS_DIR.as_posix()}/ does not exist; no group analysis has been "
            "written."
        )
        return {}, notices

    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        notices.append(
            f"{ANALYSIS_DIR.as_posix()}/ could not be listed; no analysis file was "
            "counted from it."
        )
        return {}, notices

    files: dict[str, int] = {}
    unreadable: list[str] = []
    non_conforming: list[str] = []
    for entry in entries:
        name = entry.name
        if not name.endswith(ANALYSIS_SUFFIX) or len(name) == len(ANALYSIS_SUFFIX):
            non_conforming.append(name)
            continue
        try:
            info = entry.stat()
        except OSError:
            # A dangling symlink lands here, and so does an entry whose
            # permissions changed mid-run. "Present but unmeasurable" is
            # reported as absent ON PURPOSE: crediting a file whose size could
            # not be read would be the one direction that turns a defect into a
            # pass.
            unreadable.append(name)
            continue
        if not stat_module.S_ISREG(info.st_mode):
            non_conforming.append(name)
            continue
        files[name[: -len(ANALYSIS_SUFFIX)]] = int(info.st_size)

    if unreadable:
        notices.append(
            f"{len(unreadable)} entry/entries in {ANALYSIS_DIR.as_posix()}/ could not be "
            "measured and are reported as absent: " + _named(unreadable) + "."
        )
    if non_conforming:
        notices.append(
            f"{len(non_conforming)} entry/entries in {ANALYSIS_DIR.as_posix()}/ do not "
            "follow the <group>.md convention and were not counted: "
            + _named(non_conforming)
            + "."
        )
    return files, notices


def _case_collision_notices(
    groups: tuple[str, ...], unexpected: tuple[str, ...]
) -> list[str]:
    """Where a case-insensitive filesystem cannot hold what the estate contains.

    Two notices, and they are different problems. The first is a property of
    the ESTATE: two groups whose names differ only in case cannot both have a
    file on macOS, so one of them will read as missing forever and the operator
    needs to know why before treating it as agent negligence. The second is a
    probable SLIP: a file whose name differs from a group's only in case is
    almost certainly meant for that group, and saying so is cheaper than
    leaving the agent to compare two strings by eye.

    Neither one merges anything. ``Billing`` and ``billing`` stay two groups,
    and ``LoadCustomer`` and ``LoadCustomers`` stay two groups, because on real
    estates they are.
    """

    notices: list[str] = []

    folded: dict[str, list[str]] = {}
    for group in groups:
        folded.setdefault(group.casefold(), []).append(group)
    collisions = [name for names in folded.values() if len(names) > 1 for name in names]
    if collisions:
        ordered = sorted(collisions)
        notices.append(
            f"{len(ordered)} group name(s) differ from another only in letter case: "
            + _named(ordered)
            + f". They are separate groups and are counted separately; on a "
            f"case-insensitive filesystem one {ANALYSIS_DIR.as_posix()}/<group>.md "
            "file cannot satisfy both."
        )

    # Built from the already-sorted ``groups``, so when a fold is shared the
    # name this notice prints is decided by that order rather than by dict
    # iteration over a grouping built somewhere else.
    group_folds = {group.casefold(): group for group in groups}
    near_misses = sorted(
        f"{name}{ANALYSIS_SUFFIX} (group {group_folds[name.casefold()]})"
        for name in unexpected
        if name.casefold() in group_folds
    )
    if near_misses:
        notices.append(
            f"{len(near_misses)} analysis file(s) differ from a group name only in "
            "letter case and therefore satisfy no group: " + _named(near_misses) + "."
        )
    return notices


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("Analysis coverage: unreadable navigation shard %s", path.name)
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _text(value: Any) -> str:
    if value is None or isinstance(value, (bool, dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _named(values: list[str]) -> str:
    """``a, b, c (+7 more)``. The total is always visible.

    De-duplicated in the order it was handed, and callers hand it an
    exact-string sort -- never a ``casefold`` one. A fold-equal pair sorted on
    ``casefold`` alone keeps whatever order the input happened to have, and
    this text reaches a persisted artifact that is expected to be byte-stable.
    """

    shown = list(dict.fromkeys(values))
    head = ", ".join(shown[:MAX_NAMED])
    if len(shown) > MAX_NAMED:
        return f"{head} (+{len(shown) - MAX_NAMED} more)"
    return head
