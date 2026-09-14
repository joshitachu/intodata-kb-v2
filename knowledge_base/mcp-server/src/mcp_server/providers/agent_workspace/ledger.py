"""The coverage ledger: what the estate contains, and how the agent accounted for it.

> The agent is free to choose how it investigates, but it is not free to choose
> what must be accounted for.

This module is the machine-readable half of that sentence. It holds a
POPULATION (every parsed design the scan produced) and a RECORD OF DECISIONS
(one line per classification the agent wrote), and it reports the difference
between them. Nothing here judges the quality of an investigation; it reports
whether every candidate was answered for, and the answers that end an
obligation carry the justification the contract requires.

**It is not ``coverage.py``.** That module is an advisory prose gauge -- it
counts how many parsed identifiers the published narrative happens to mention,
it may not block anything, and a low number there is not a defect. This module
is the binding register: its ``unreviewed`` set is a statement about what the
run still owes. The two answer different questions, write different artifacts
and must never be merged.

The roster file is a convenience, never the obligation
------------------------------------------------------

:data:`~contract.COVERAGE_ROSTER` lives under agent-writable ``notes/``, so an
agent that deletes rows from it -- or the whole file -- must not shrink what
the run owes. :func:`roster_from_workspace` therefore RECOMPUTES the population
from ``facts/navigation/units/part_*.jsonl`` on every call, and every report is
computed against that recomputation. ``facts/`` is inside
``contract.EVIDENCE_ROOTS``, so it is inside the integrity digest the publisher
re-checks after the run: tampering with the population is detected there, with
better information than this module could produce. A control file the
controlled party can edit controls nothing, and this is the same defect class
as an approval receipt that names its own allowed roots.

The log the agent writes
------------------------

:data:`~contract.COVERAGE_LOG` is append-only JSONL. One object per line, one
line per decision::

    {"unit_id": "orders/LoadCustomer", "disposition": "material", "reason": "", "integration_ids": ["int-order-intake"]}

Fields:

``unit_id``
    Required. The scanner id, exactly as the roster carries it. Never a
    display name -- the gate compares ids for equality and compares no names,
    because a document is allowed to call a design whatever the client calls
    it.
``disposition``
    Required. One of :data:`DISPOSITIONS`.
``reason``
    A concrete sentence. Required for every disposition in
    :data:`~contract.DISPOSITION_REQUIRES_REASON`, i.e. every one that ENDS an
    obligation. Checked for length only, never for wording.
``integration_ids``
    A list of integration ids from the integration model. Required for every
    disposition in :data:`~contract.DISPOSITION_REQUIRES_MODEL_LINK`. A bare
    string, and the singular key ``integration_id``, are accepted: both are
    predictable slips and rejecting them would manufacture a false
    ``missing_link`` -- which would report an honest run as incomplete.

APPEND, never rewrite, and the LAST line for a unit wins. A 142-line JSON
object rewritten after every discovery loses an investigation to one
interrupted write; last-line-wins makes revising an earlier call a one-line
append rather than a read-modify-write the agent has to get right.

Nothing here raises on agent input
----------------------------------

A malformed line, a disposition typo, a row that is not an object, a file that
is not valid UTF-8: each is recorded in ``LedgerReport.malformed`` and skipped.
An exception on this path would turn an agent typo into a failed run, which is
precisely the trade this lane refuses to make.

Deterministic Python. No model, no network, no config.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from mcp_server.providers.agent_workspace.contract import (
    COVERAGE_LOG,
    COVERAGE_ROSTER,
    DISPOSITION_REQUIRES_MODEL_LINK,
    DISPOSITION_REQUIRES_REASON,
    NAVIGATION,
    CandidateDisposition,
)

logger = logging.getLogger(__name__)

#: Where the deterministic summary is persisted. Underscore-prefixed for the
#: same reason ``coverage.REACH_ARTIFACT`` is: ``publish._is_visible`` and
#: ``is_published_doc_filename`` both reject such a segment, so run control
#: state cannot reach a client document by being forgotten in a cleanup list.
LEDGER_ARTIFACT = Path("_audit") / "coverage_ledger.json"

#: The navigation view that IS the population. Named once; the whole
#: recomputation rule collapses if a caller reads a different view.
UNITS_VIEW = "units"

#: Derived from the contract's ``Literal`` rather than retyped, so the
#: vocabulary cannot fork. Order is the contract's order: most work owed
#: first.
DISPOSITIONS: tuple[str, ...] = tuple(get_args(CandidateDisposition))

#: A floor under ``"n/a"``, ``"-"``, ``"tbd"`` and ``"no"``. NOT a quality bar
#: and not a wording check: the contract authorises a length check and nothing
#: more, and any real reason ("test harness, never scheduled") clears this
#: without trying. Kept here rather than in ``contract`` because only the
#: ledger enforces it; a gate that wants the same number imports it from here.
MIN_REASON_CHARS = 12

#: ``malformed`` is a diagnostic, not a data channel. An agent looping on a
#: broken writer can produce thousands of bad lines, and an artifact that grows
#: with them is an artifact nobody opens. The overflow is COUNTED, so the
#: reader still learns the scale.
MAX_MALFORMED = 50

#: Reasons are clipped in the persisted artifact only. The in-memory
#: ``Classification`` keeps the full text for anything that wants to quote it.
MAX_ARTIFACT_REASON_CHARS = 400

#: Repr'd values from agent input are bounded before they reach a diagnostic
#: string: a 2 MB "disposition" must not become a 2 MB log line.
_MAX_ECHO_CHARS = 40

SCHEMA_VERSION = 1

#: Stated inside the artifact, because the file will outlive the person who
#: computed it and the roster/population distinction is the whole design.
_POPULATION_NOTE = (
    "The population below is recomputed from facts/navigation/units/part_*.jsonl, "
    "which is read-only evidence inside the integrity digest. It is NOT read from "
    "notes/coverage/roster.jsonl: that file is a convenience for the agent and "
    "editing it cannot change what the run owes."
)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRecord:
    """One parsed design, as the population knows it.

    Deliberately thin. It carries what an agent needs to ROUTE -- which group,
    how big, is it an entry point, and the canonical file that holds the rest
    -- and nothing that would let the roster be mistaken for the evidence. The
    navigation row it comes from carries callers, callees, component types and
    mapping tables; the agent reads those from the view, not from here.
    """

    unit_id: str
    display_name: str
    group: str
    kind: str
    entry_point: bool
    component_count: int
    evidence: str

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """A TOTAL order on exact strings.

        Never ``str.casefold`` alone, and never a prefix: two Talend groups
        differing only in the case of one letter are two groups, and a design
        and its plural (``LoadCustomer`` beside ``LoadCustomers``) are two
        separately-scheduled designs. Both shapes occur on real estates;
        folding either one merges two candidates into one and quietly shrinks
        the population. ``unit_id`` is unique, so the third element makes the
        key total even when a display name repeats across groups.
        """

        return (self.group, self.display_name, self.unit_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "display_name": self.display_name,
            "group": self.group,
            "kind": self.kind,
            "entry_point": self.entry_point,
            "component_count": self.component_count,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Classification:
    """One decision the agent wrote, with the line it wrote it on.

    ``line_number`` is kept so a diagnostic can point at the line rather than
    at the unit: an agent correcting its own log needs to know which append it
    is superseding, and an operator reading ``malformed`` beside this needs the
    two to be in the same coordinate system.
    """

    unit_id: str
    disposition: str
    reason: str
    integration_ids: tuple[str, ...]
    line_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "disposition": self.disposition,
            "reason": _clip(self.reason, MAX_ARTIFACT_REASON_CHARS),
            "integration_ids": list(self.integration_ids),
            "line": self.line_number,
        }


@dataclass(frozen=True)
class LedgerReport:
    """The whole register: population, decisions, and every gap between them.

    Every collection is sorted on a total key, so two reads of one workspace
    produce byte-identical output and the digest of an unchanged ledger does
    not move.
    """

    population: tuple[CandidateRecord, ...] = ()
    #: ``unit_id`` -> the LAST classification written for it.
    classified: dict[str, Classification] = field(default_factory=dict)
    #: In the population, never classified. This is what the run still owes.
    unreviewed: tuple[str, ...] = ()
    #: Classified, but not in the population. Usually a typo'd or invented id,
    #: and it is reported rather than counted as coverage -- crediting it would
    #: let a misspelling clear an obligation.
    unknown: tuple[str, ...] = ()
    #: Bounded diagnostic strings, in file order. See :data:`MAX_MALFORMED`.
    malformed: tuple[str, ...] = ()
    #: Dispositions that end an obligation without the justification the
    #: contract requires.
    missing_reason: tuple[str, ...] = ()
    #: Dispositions that assert documented integration behaviour without
    #: naming the integration that covers them.
    missing_link: tuple[str, ...] = ()
    #: disposition -> count, over every classification held. All seven keys are
    #: always present (zero-valued when absent) so a consumer can format a
    #: table without knowing the vocabulary. Ids in :attr:`unknown` are counted
    #: here too -- they are decisions the agent made -- so a consumer that
    #: needs population-only totals subtracts that set.
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def unresolved(self) -> tuple[str, ...]:
        """Candidates the agent looked at and could not settle.

        A legitimate answer, and deliberately not the same thing as
        :attr:`unreviewed`: an investigation that cannot settle a design must
        be able to say so, rather than inventing a verdict to clear the gate.
        Both keep the run incomplete, for different reasons, and the completion
        report names them separately.
        """

        return tuple(
            sorted(
                unit_id
                for unit_id, item in self.classified.items()
                if item.disposition == "unresolved"
            )
        )

    def digest(self) -> str:
        """sha256 over the ``(unit_id, disposition)`` pairs, and nothing else.

        It identifies the CLASSIFICATION STATE, so rewording a reason or
        adding an integration id must not move it -- a receipt bound to this
        digest stays valid across a prose edit, and a changed digest means a
        candidate genuinely changed hands. Canonical JSON with fixed
        separators, sorted pairs, ASCII-escaped, so the value is stable across
        processes and platforms.
        """

        pairs = sorted((unit_id, item.disposition) for unit_id, item in self.classified.items())
        payload = json.dumps([list(pair) for pair in pairs], separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": "nexus-coverage-ledger",
            "digest": self.digest(),
            "population_source": f"{(NAVIGATION / UNITS_VIEW).as_posix()}/part_*.jsonl",
            "population_is_recomputed": _POPULATION_NOTE,
            "population_total": len(self.population),
            "classified_total": len(self.classified),
            "counts": {name: self.counts.get(name, 0) for name in DISPOSITIONS},
            "unreviewed": list(self.unreviewed),
            "unresolved": list(self.unresolved),
            "unknown": list(self.unknown),
            "missing_reason": list(self.missing_reason),
            "missing_link": list(self.missing_link),
            "malformed": list(self.malformed),
            "classifications": [
                self.classified[unit_id].to_dict() for unit_id in sorted(self.classified)
            ],
        }

    def log_line(self) -> str:
        """One line for the run log, readable without this module open."""

        return (
            f"coverage ledger: {len(self.classified)} of {len(self.population)} candidates "
            f"classified, {len(self.unreviewed)} unreviewed, "
            f"{len(self.unresolved)} unresolved"
        )


# --------------------------------------------------------------------------
# The population
# --------------------------------------------------------------------------


def roster_from_workspace(staging_dir: Path) -> tuple[CandidateRecord, ...]:
    """THE population, recomputed from evidence. Never read from the roster file.

    Reads the ``units`` navigation shards directly rather than the manifest's
    row counts: the obligation is a set of identities, not a total.

    A shard that cannot be read is logged and skipped rather than raised. That
    is not a hole: ``facts/`` is inside ``contract.EVIDENCE_ROOTS`` and
    therefore inside the digest the publisher re-checks after the run, so a
    deleted or edited shard fails publication there -- naming the exact file
    and the exact digest mismatch, which is strictly better information than an
    exception from here.
    """

    directory = Path(staging_dir) / NAVIGATION / UNITS_VIEW
    if not directory.is_dir():
        return ()

    records: dict[str, CandidateRecord] = {}
    for shard in sorted(directory.glob("part_*.jsonl")):
        for row in _read_jsonl(shard):
            unit_id = _text(row.get("unit_id"))
            # First wins on a duplicate id. The builder keys units by id so
            # this should not occur; if it ever does, the population must not
            # double-count, and taking the first keeps shard order the only
            # thing that decides.
            if not unit_id or unit_id in records:
                continue
            records[unit_id] = CandidateRecord(
                unit_id=unit_id,
                display_name=_text(row.get("display_name")),
                group=_text(row.get("group")),
                kind=_text(row.get("kind")),
                entry_point=bool(row.get("entry_point")),
                component_count=_count(row.get("component_count")),
                evidence=_text(row.get("evidence")),
            )
    return tuple(sorted(records.values(), key=lambda record: record.sort_key))


def seed_roster(staging_dir: Path) -> int:
    """Write the convenience roster the agent reads. Returns the row count.

    Called by the workspace builder before the agent starts, so the agent can
    see the whole population on one greppable file instead of reconstructing it
    from the shards. It is the agent's map, not its obligation -- deleting it
    changes nothing that :func:`read_ledger` computes.

    Unlike everything else here this DOES propagate an ``OSError``: it runs
    during workspace construction, and a workspace whose notes directory cannot
    be written is a failed build that should stop at the build rather than
    surface later as an agent reading a file that is not there.
    """

    staging_dir = Path(staging_dir)
    records = roster_from_workspace(staging_dir)
    target = staging_dir / COVERAGE_ROSTER
    target.parent.mkdir(parents=True, exist_ok=True)
    # `ensure_ascii=False`: a row whose accented characters are escaped is not greppable by the
    # name the agent read in the navigation view, and these two files describe
    # the same designs.
    target.write_text(
        "".join(
            json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    return len(records)


# --------------------------------------------------------------------------
# The decisions
# --------------------------------------------------------------------------


def read_ledger(staging_dir: Path) -> LedgerReport:
    """Read the agent's classification log and report it against the population.

    Never raises. An absent log is the normal state of a run that has not
    started classifying, and reports every candidate as unreviewed.
    """

    staging_dir = Path(staging_dir)
    population = roster_from_workspace(staging_dir)
    known = {record.unit_id for record in population}

    malformed: list[str] = []
    text, read_note = _read_log(staging_dir / COVERAGE_LOG)
    if read_note:
        malformed.append(read_note)

    classified: dict[str, Classification] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        entry, notes = _parse_line(line, number)
        malformed.extend(notes)
        if entry is not None:
            # LAST line wins: the agent revises a call by appending, and the
            # append it wrote most recently is the one it means.
            classified[entry.unit_id] = entry

    counts = dict.fromkeys(DISPOSITIONS, 0)
    for item in classified.values():
        counts[item.disposition] = counts.get(item.disposition, 0) + 1

    return LedgerReport(
        population=population,
        classified=classified,
        unreviewed=tuple(sorted(unit_id for unit_id in known if unit_id not in classified)),
        unknown=tuple(sorted(unit_id for unit_id in classified if unit_id not in known)),
        malformed=_bounded(malformed),
        missing_reason=tuple(
            sorted(
                unit_id
                for unit_id, item in classified.items()
                if item.disposition in DISPOSITION_REQUIRES_REASON
                and len(item.reason) < MIN_REASON_CHARS
            )
        ),
        missing_link=tuple(
            sorted(
                unit_id
                for unit_id, item in classified.items()
                if item.disposition in DISPOSITION_REQUIRES_MODEL_LINK and not item.integration_ids
            )
        ),
        counts=counts,
    )


def write_summary(staging_dir: Path, report: LedgerReport) -> Path | None:
    """Persist the register into the run's audit record. Best effort.

    An unwritable artifact is logged and nothing else. The register's authority
    comes from the report object the gate already holds; a summary file that
    could fail a run would make the audit trail a second gate, free to disagree
    with the first.
    """

    path = Path(staging_dir) / LEDGER_ARTIFACT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
    except OSError:
        logger.warning("Could not persist the agent-workspace coverage ledger summary")
        return None
    return path


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _read_log(path: Path) -> tuple[str, str | None]:
    """The log's text, plus one diagnostic if reading it was not clean.

    Undecodable bytes are REPLACED rather than fatal: one corrupt byte in the
    middle of a 140-line log must not discard the other 139 decisions.
    """

    if not path.is_file():
        return "", None
    try:
        raw = path.read_bytes()
    except OSError:
        return "", f"{path.name}: could not be read; no classification was counted from it"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return (
            raw.decode("utf-8", errors="replace"),
            f"{path.name}: not valid UTF-8; undecodable bytes were replaced and the "
            "lines containing them are likely to be reported as malformed",
        )


def _parse_line(line: str, number: int) -> tuple[Classification | None, list[str]]:
    """One log line to a classification, or to diagnostics. Never raises."""

    notes: list[str] = []
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None, [f"line {number}: not valid JSON"]
    if not isinstance(payload, dict):
        return None, [f"line {number}: not a JSON object"]

    unit_id = _text(payload.get("unit_id"))
    if not unit_id:
        return None, [f"line {number}: no unit_id"]

    disposition = _text(payload.get("disposition"))
    if disposition not in DISPOSITIONS:
        return None, [
            f"line {number}: unknown disposition "
            f"{_clip(disposition, _MAX_ECHO_CHARS)!r} for {_clip(unit_id, _MAX_ECHO_CHARS)!r}"
        ]

    integration_ids, link_note = _integration_ids(payload)
    if link_note:
        notes.append(f"line {number}: {link_note}")

    return (
        Classification(
            unit_id=unit_id,
            disposition=disposition,
            reason=_text(payload.get("reason")),
            integration_ids=integration_ids,
            line_number=number,
        ),
        notes,
    )


def _integration_ids(payload: dict[str, Any]) -> tuple[tuple[str, ...], str | None]:
    """Ordered, de-duplicated integration ids, tolerating two predictable slips.

    A bare string and the singular key ``integration_id`` are both accepted.
    Neither is what the format teaches, but both are slips a model makes, and
    rejecting them would report a linked candidate as ``missing_link`` -- which
    turns a typo into "this run is incomplete" and sends the agent looking for
    a problem that is not there.
    """

    raw = payload.get("integration_ids")
    if raw is None:
        raw = payload.get("integration_id")
    if raw is None:
        return (), None
    if isinstance(raw, str):
        value = raw.strip()
        return ((value,) if value else ()), None
    if not isinstance(raw, (list, tuple)):
        return (), "integration_ids is not a list of ids; it was ignored"

    ordered: dict[str, None] = {}
    for item in raw:
        if isinstance(item, (dict, list, tuple, set)):
            continue
        value = _text(item)
        if value:
            ordered.setdefault(value, None)
    return tuple(ordered), None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("Coverage ledger: unreadable navigation shard %s", path.name)
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
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _bounded(notes: list[str]) -> tuple[str, ...]:
    if len(notes) <= MAX_MALFORMED:
        return tuple(notes)
    extra = len(notes) - MAX_MALFORMED
    return (*notes[:MAX_MALFORMED], f"... and {extra} more malformed line(s) were skipped")
