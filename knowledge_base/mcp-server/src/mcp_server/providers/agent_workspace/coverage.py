"""Documentation REACH: how much of the parsed estate the prose mentions.

A GAUGE, and the plan (decision 10) is explicit that it is only that: nothing
in publication may read it, it cannot block, and it cannot shape a document.
``test_coverage.py`` pins that ``publish.py`` never mentions it and that the
runner never branches on its result.

**It is not the agent's coverage register, and must not be mistaken for one.**
PRD section 10.1 defines an AGENT-owned register at ``notes/coverage.json``
that classifies integration-bearing candidates
(``meaningful-integration`` / ``shared-component`` / ``utility`` / ``test`` /
``inactive`` / ``dead-configuration`` / ``duplicate`` / ``unresolved``) and is
the input to the agent's own completion decision. This module writes nothing
under ``notes/``, uses a different name for its artifact and a different
vocabulary for its fields, and answers a different question: not "has every
candidate been classified" but "how much of the parse does the published prose
mention".

**A low number here is not a defect, and the artifact says so in its own
text.** PRD 10.2 is explicit that documentation does not require one document
per candidate, one section per job, every candidate mentioned in prose, or an
exact string match between documents and scanner names; several candidates
legitimately belong to one meaningful integration. PRD 15.3 forbids rejecting
a document on job-name occurrence counts or scanner-string-versus-prose
comparison -- a run was failed for exactly that when a client-facing project
name differed from an internal parser identifier. This module computes such a
comparison and REPORTS it; anything that turns it into a verdict is
reintroducing the defect.

**Why it exists anyway.** Run 250 published eight documents and reported
success. Establishing that the narrative described roughly fifteen of a hundred
and thirty-nine designs took a three-agent investigation. One deterministic
count on the run log is what turns "is this thin?" from an investigation into a
glance, and the operator -- not a gate -- decides what it means.

**What it measures, precisely.** Four evidence classes, each with a parsed
population taken from the staged navigation views (the materialized view over
the canonical parse -- ``facts/navigation/{view}/part_*.jsonl``) and two counts
over the AGENT-AUTHORED documents:

* ``mentioned`` -- the identifier occurs anywhere in the document text.
* ``sectioned`` -- the identifier occurs in a Markdown heading, i.e. the
  documentation gave it a heading of its own. Deliberately NOT called
  "documented": a design covered inside its integration's journey narrative is
  documented and will not be counted here, which is a limit of the measure and
  not a gap in the documentation.

The counts are taken over the agent-authored set only, NOT over
``output/reference/``. The reference registers are compiler-authored and
complete by construction: every design, channel, routine and mapping row is in
them. Including them would make every run read as full reach and the gauge
would report nothing. The compiler-authored document count is reported
separately so the split is visible rather than implied.

Matching is boundary-aware and case-sensitive, and both halves of that are
load-bearing on real estates. A design and its plural (``LoadCustomer`` beside
``LoadCustomers``) are routinely two separately-scheduled designs, so a
substring match credits one for the other; and two Talend groups differing only
in the case of one letter are likewise two groups, so a case-insensitive match
merges them. Both shapes have been observed in production scans. Never
normalise two names into one.

Deterministic Python. No model, no network, no config.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from mcp_server.providers.agent_workspace import contract

logger = logging.getLogger(__name__)

#: Where the gauge is persisted: the run's audit record, which
#: ``publish.NEXUS_OWNED_ROOTS`` carries into the published tree for the
#: operator. Underscore-prefixed, so no client document, no RAG layer, no PDF
#: export and no dashboard listing can see it.
#:
#: NOT ``notes/coverage.json``. That path is the AGENT's coverage register
#: (PRD 10.1) and ``notes/`` is agent-owned: nothing in Nexus writes there.
#: The filename is different too, so a reader who finds one of them cannot
#: mistake it for the other.
REACH_ARTIFACT = Path("_audit") / "documentation_reach.json"

#: Stated in the artifact itself, because the number will outlive the person
#: who computed it. PRD 10.2 and 15.3.
_NOT_A_VERDICT = (
    "This is a reported measure, not a completion criterion and not a gate. "
    "Documentation does not require one document per candidate, one section "
    "per job, or every parsed identifier to appear in prose; several "
    "candidates may belong to one meaningful integration. A low number here "
    "is a prompt to look, never a defect on its own."
)

#: The document tree the deterministic register compiler owns. Excluded from
#: the measured corpus; see the module docstring.
COMPILER_DOC_ROOT = "reference"

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$", re.MULTILINE)

#: A line-drawn identifier boundary. Underscores and digits are part of a
#: design name, so the usual ``\b`` is wrong at both ends.
_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9_])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9_])"

#: Alternations are built in chunks so one estate's 7,000 routine references
#: cannot produce a single pattern the regex engine refuses to compile.
_ALTERNATION_CHUNK = 500


@dataclass(frozen=True)
class EvidenceClassReach:
    """One evidence class: what was parsed, and how much of it is written up."""

    name: str
    #: What the scan parsed. Distinct identities for a countable class; a row
    #: total for ``mapping_rows``.
    parsed: int
    #: The identifier occurs somewhere in the agent-authored prose.
    mentioned: int
    #: The identifier occurs in a Markdown heading. Named for what is measured
    #: -- a heading -- and not for what a reader might infer from it.
    sectioned: int
    #: What the count is a count OF, in one clause, so a log line is readable
    #: without this module open beside it.
    unit: str

    def to_dict(self) -> dict:
        return {
            "class": self.name,
            "unit": self.unit,
            "parsed": self.parsed,
            "mentioned": self.mentioned,
            "sectioned": self.sectioned,
        }

    def log_line(self) -> str:
        if self.parsed == 0:
            return f"{self.name}: none parsed"
        return (
            f"{self.name}: {self.mentioned} of {self.parsed} {self.unit} mentioned, "
            f"{self.sectioned} with a heading"
        )


@dataclass(frozen=True)
class DocumentationReachReport:
    """The whole gauge. Reported, never enforced."""

    classes: tuple[EvidenceClassReach, ...] = ()
    #: Documents the measurement ran over, and the compiler-authored ones it
    #: deliberately did not.
    agent_documents: tuple[str, ...] = ()
    compiler_documents: tuple[str, ...] = ()
    #: Why a class is absent, or why the whole gauge is empty. Never fatal.
    notices: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "artifact": "nexus-documentation-reach",
            "not_a_completion_criterion": _NOT_A_VERDICT,
            "measured_over": "agent-authored documents only",
            "agent_documents": list(self.agent_documents),
            "compiler_documents": list(self.compiler_documents),
            "classes": [item.to_dict() for item in self.classes],
            "notices": list(self.notices),
        }

    def log_line(self) -> str:
        if not self.classes:
            return "documentation reach: no parsed evidence to measure against"
        return "documentation reach: " + "; ".join(item.log_line() for item in self.classes)


# --------------------------------------------------------------------------
# Reading the parsed population
# --------------------------------------------------------------------------


def _read_view(staging_dir: Path, view: str) -> list[dict]:
    """Every row of one navigation view, or an empty list.

    Reads the shards directly rather than the manifest's row counts: the gauge
    needs the identities, not the totals, and a view with no directory is a
    view with no rows (the builder states the absence in the manifest instead
    of writing an empty directory).
    """

    directory = staging_dir / contract.NAVIGATION / view
    if not directory.is_dir():
        return []
    rows: list[dict] = []
    for shard in sorted(directory.glob("part_*.jsonl")):
        try:
            text = shard.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _identities(rows: Iterable[Mapping], *keys: str) -> list[str]:
    """Distinct non-empty identity strings, first non-empty key per row."""

    seen: dict[str, None] = {}
    for row in rows:
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value:
                seen.setdefault(value, None)
                break
    return list(seen)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _present(identities: list[str], haystack: str) -> set[str]:
    """Which identities occur in ``haystack`` on an identifier boundary."""

    if not identities or not haystack:
        return set()
    found: set[str] = set()
    # Longest first so an alternation prefers ``Orders_LoadDelta`` over
    # ``Orders_Load`` when both are real names on the estate.
    ordered = sorted(identities, key=lambda value: (-len(value), value))
    for start in range(0, len(ordered), _ALTERNATION_CHUNK):
        chunk = ordered[start : start + _ALTERNATION_CHUNK]
        pattern = re.compile(
            _BOUNDARY_BEFORE
            + "(?:"
            + "|".join(re.escape(value) for value in chunk)
            + ")"
            + _BOUNDARY_AFTER
        )
        found.update(match.group(0) for match in pattern.finditer(haystack))
    return found


def _headings(text: str) -> str:
    return "\n".join(match.group(1) for match in _HEADING_RE.finditer(text))


# --------------------------------------------------------------------------
# The gauge
# --------------------------------------------------------------------------


def measure_documentation_reach(
    staging_dir: Path, documents: Mapping[str, str]
) -> DocumentationReachReport:
    """Count what the published prose mentions against what the scan parsed.

    ``documents`` is the accepted publication snapshot keyed by its
    ``output/``-relative path, exactly as ``PublicationOutcome.documents``
    holds it. Must be called while the staged ``facts/`` tree is still intact,
    i.e. before ``finalize_publication`` assembles the clean room.

    Never raises. A missing view, an unreadable shard or a malformed row
    reduces what can be measured and is recorded as a notice; a gauge that
    could fail a run would be a gate.
    """

    staging_dir = Path(staging_dir)
    notices: list[str] = []

    agent_docs = {
        name: text
        for name, text in documents.items()
        if Path(name).parts[:1] != (COMPILER_DOC_ROOT,)
    }
    compiler_docs = tuple(sorted(set(documents) - set(agent_docs)))

    corpus = "\n".join(agent_docs[name] for name in sorted(agent_docs))
    headings = _headings(corpus)
    if not agent_docs:
        notices.append(
            "no agent-authored document was published; every count below is zero "
            "by construction, not by absence of evidence"
        )

    classes: list[EvidenceClassReach] = []

    units = _read_view(staging_dir, "units")
    design_names = _identities(units, "display_name", "unit_id")
    mentioned_designs = _present(design_names, corpus)
    sectioned_designs = _present(design_names, headings)
    classes.append(
        EvidenceClassReach(
            name="designs",
            parsed=len(design_names),
            mentioned=len(mentioned_designs),
            sectioned=len(sectioned_designs),
            unit="designs",
        )
    )

    channels = _read_view(staging_dir, "channel_pairings")
    channel_names = _identities(channels, "channel")
    if not channel_names:
        # A generic or Workato estate produces interface rows without paired
        # channels; the interface's own name is the identity there.
        channel_names = _identities(_read_view(staging_dir, "interfaces"), "name")
    classes.append(
        EvidenceClassReach(
            name="channels",
            parsed=len(channel_names),
            mentioned=len(_present(channel_names, corpus)),
            sectioned=len(_present(channel_names, headings)),
            unit="channels",
        )
    )

    routines = _read_view(staging_dir, "routine_usage")
    routine_names = _identities(routines, "routine")
    classes.append(
        EvidenceClassReach(
            name="routines",
            parsed=len(routine_names),
            mentioned=len(_present(routine_names, corpus)),
            sectioned=len(_present(routine_names, headings)),
            unit="routines",
        )
    )

    # Mapping ROWS, attributed through their owning design. A field mapping has
    # no identifier a document could plausibly name, so the honest measure is:
    # how many exact mapping rows belong to a design the documentation writes
    # about. Reported as rows rather than tables because rows are the unit the
    # client cares about and the unit the scan's mapping accounting counts.
    mappings = _read_view(staging_dir, "mappings")
    total_rows = 0
    mentioned_rows = 0
    sectioned_rows = 0
    for row in mappings:
        try:
            written = int(row.get("written_fields") or 0)
        except (TypeError, ValueError):
            written = 0
        total_rows += written
        owner = str(row.get("display_name") or "").strip() or str(row.get("unit_id") or "").strip()
        if not owner:
            continue
        if owner in mentioned_designs:
            mentioned_rows += written
        if owner in sectioned_designs:
            sectioned_rows += written
    classes.append(
        EvidenceClassReach(
            name="mapping_rows",
            parsed=total_rows,
            mentioned=mentioned_rows,
            sectioned=sectioned_rows,
            unit="exact mapping rows (attributed through their owning design)",
        )
    )

    if not any(item.parsed for item in classes):
        notices.append(
            "the staged navigation views hold no rows for any measured class; "
            "there is nothing to measure reach against"
        )

    return DocumentationReachReport(
        classes=tuple(classes),
        agent_documents=tuple(sorted(agent_docs)),
        compiler_documents=compiler_docs,
        notices=tuple(notices),
    )


def write_reach_artifact(staging_dir: Path, report: DocumentationReachReport) -> Path | None:
    """Persist the gauge into the run's audit record.

    Best effort. A gauge that can fail a publication is a gate, so an
    unwritable artifact is logged and nothing else.
    """

    path = Path(staging_dir) / REACH_ARTIFACT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
    except OSError:
        logger.warning("Could not persist the agent-workspace documentation-reach gauge")
        return None
    return path
