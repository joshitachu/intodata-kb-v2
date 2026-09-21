"""What the documentation run learned, made reachable from the chat lane.

Every documentation run keeps a per-project memory beside the published docs
(``<project>/knowledge/``): the vocabulary this client actually uses, what the
scan could not see, where a person corrected the pipeline, and the questions
the run could not settle. On one real estate ``open-questions.md`` is 30 KB of
consultant-grade questions -- "where is ``context.maximo_objectEndpointInvoice``
defined?", "should the exposed API keys be rotated?" -- and until now nothing
read it. The assistant answering questions about that same project had no
access to the most honest thing the pipeline ever wrote about it.

This module is the read side of that memory, and the shape of it is dictated
by three constraints that were already settled elsewhere:

* **The memory is not authored here and is never written back.**
  :mod:`src.labs.workspace` is explicit that ``.nexus/`` is a read path --
  "learned guidance goes to the database through the repository, never to a
  file the agent can see change". So the memory travels the same route an
  uploaded document travels: an extracted copy under ``.nexus/project/``, named
  in the always-loaded context and read only if the agent decides to. The
  copy deliberately drops the record's own "HOW TO WRITE HERE" preamble, which
  is an instruction to the *generation* agent to append entries; handing that
  to a read-only assistant would be an invitation to do the one thing this lane
  forbids.

* **Role and size decide independently** (:mod:`src.harness.guidance.material`
  argues this at length). Each memory file is classified once, by what force it
  carries: terminology, known limitations and human corrections BIND an answer,
  so they are constraints; decisions, notes and open questions INFORM it, so
  they are references. Size is a separate gate, and it bites on exactly one
  file -- ``open-questions.md`` -- for which the module's usual answer ("too
  big to read, search it instead") is unavailable, because the memory is
  deliberately never pushed to the client's RAG corpus. So an oversized file is
  reduced by dropping whole ENTRIES, never by cutting bytes, and says in its own
  header how many entries exist, how many are shown, and where the record is.

* **Stale memory is worse than none**, and the staleness rule already exists.
  Every entry carries the scan fingerprint it was written against, and
  ``_scan_snapshot.json`` records the fingerprint the record as a whole was last
  persisted against. This module reuses that comparison rather than inventing a
  second one, and reuses its asymmetry too: a human correction outranks an agent
  conclusion, so a stale human entry is marked as still authoritative while a
  stale agent entry is marked not-to-be-cited. Disappearance is handled one level
  up, by the projection and its sweep: a file that was deleted, emptied or grown
  past the ceiling simply stops being projected, exactly as a withdrawn document
  does.

The on-disk format is a cross-sub-project contract
(``knowledge_base/mcp-server/src/mcp_server/services/project_memory.py`` is the
writer). It is re-declared here rather than imported because the two live in
different distributions with different dependency sets; the constants below are
that declaration, and ``test_generation_memory.py`` pins them against the real
record on disk so a drift is a failing test rather than an empty section.

Never raises. A memory that fails to load costs the turn a section it did not
have yesterday; an exception would cost it the answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from src.harness.guidance.material import MATERIALIZE_MAX_CHARS, MaterialKind
from src.harness.guidance.renderer import MATERIAL_KIND_PRIORITY
from src.labs.project_output import KNOWLEDGE_DIRNAME, project_knowledge_dir

logger = logging.getLogger(__name__)

__all__ = [
    "ENTRIES_MARKER",
    "MEMORY_DIRNAME",
    "MEMORY_FILES",
    "MEMORY_MAX_CHARS",
    "MEMORY_SECTION_HEADING",
    "NOTE_PREFIX",
    "REVIEW_PREFIX",
    "SNAPSHOT_FILENAME",
    "GenerationMemory",
    "MemoryDocument",
    "MemoryEntry",
    "MemoryFileSpec",
    "compile_generation_memory",
    "memory_enabled",
    "parse_entries",
    "read_generation_memory",
    "render_memory_section",
    "staleness_note",
]


# -- the on-disk contract ----------------------------------------------------

#: Everything above this line in a memory file is the writer's own instructions
#: to the generation agent; everything below it is entries. Verbatim from
#: ``project_memory.ENTRIES_MARKER``.
ENTRIES_MARKER: Final = "--- entries below; append only, never edit above this line ---"

#: The durable snapshot of the scan the record was last persisted against.
SNAPSHOT_FILENAME: Final = "_scan_snapshot.json"

#: ``### <ISO timestamp> | author: <agent|human> | scan: <fingerprint>``
_ENTRY_HEADER_RE: Final = re.compile(r"^###[ \t]+(?P<rest>\S.*?)[ \t]*$")
_FIELD_RE: Final = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?P<value>.*)$")
_ISO_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$")

AUTHOR_AGENT: Final = "agent"
AUTHOR_HUMAN: Final = "human"

#: How many characters of a fingerprint the writer records in an entry header.
#: Comparisons are therefore prefix comparisons -- see :func:`same_fingerprint`.
SHORT_FINGERPRINT_CHARS: Final = 12

#: The annotation prefixes the writer already uses for exactly these two cases.
#: Reused verbatim so one vocabulary describes a stale entry on both sides of
#: the boundary, whether a person is reading the record or the agent is reading
#: the extract.
REVIEW_PREFIX: Final = "> [MEMORY REVIEW]"
NOTE_PREFIX: Final = "> [MEMORY NOTE]"

#: Off-switch, not a tuning knob. The memory is the pipeline's internal working
#: record and this lane answers client-facing questions as well as consultant
#: ones, so an operator who decides a given deployment should not surface it at
#: all needs one place to say so. Default on: the whole point of the feature is
#: that the record was unreachable.
_ENABLED_ENV: Final = "LABS_GENERATION_MEMORY"


def memory_enabled() -> bool:
    """Whether the generation memory may be mounted at all.

    Read at call time, like every other labs environment lookup, so a
    deployment can flip it without a rebuild and a test can set it per case.
    """

    value = os.environ.get(_ENABLED_ENV, "").strip().lower()
    return value not in {"0", "false", "no", "off"}


# -- what each file is, and when to read it ----------------------------------


class MemoryFileSpec(BaseModel):
    """One memory file, classified once.

    ``kind`` is the authority axis from :mod:`src.harness.guidance.material`,
    reused rather than paralleled: the manifest, the workspace and this module
    then order and describe material by one vocabulary instead of two.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    kind: MaterialKind
    #: What the file is, in the clause that follows the name in the manifest.
    summary: str
    #: When to open it. Measured behaviour on this lane is that the model acts
    #: on a specific trigger and ignores "more context is available", so every
    #: line ends in a condition -- the same rule ``renderer._KIND_TRIGGER``
    #: follows.
    trigger: str


#: The six files, and the reasoning behind each classification.
#:
#: CONSTRAINT is not "important", it is "the answer is wrong if this is
#: ignored":
#:   * ``terminology.md`` -- the worked example in the record itself is that
#:     ``VISE2AssetCheck`` and ``Vise2AssetCheck`` are two different Talend
#:     groups. An answer that normalises them has merged two integrations.
#:   * ``known-limitations.md`` -- the project-specific form of the lane's
#:     standing rule that it may never assert something does not exist. It is
#:     the list of absences that are explained rather than discovered.
#:   * ``lessons-learned.md`` -- where a person corrected the pipeline. A human
#:     correction outranks an agent conclusion, here as in the writer.
#:
#: REFERENCE is background that informs without binding: why the documentation
#: is shaped the way it is (``decisions.md``), loose notes (``key-notes.md``),
#: and what is still open (``open-questions.md``). An open question is
#: emphatically not a finding, and typing it as a constraint would invite the
#: agent to answer from the question rather than from evidence.
MEMORY_FILES: Final[tuple[MemoryFileSpec, ...]] = (
    MemoryFileSpec(
        filename="terminology.md",
        kind=MaterialKind.CONSTRAINT,
        summary="this client's own vocabulary, including names that look like typos and are not",
        trigger="before using, normalising or correcting a client term that is new to you",
    ),
    MemoryFileSpec(
        filename="known-limitations.md",
        kind=MaterialKind.CONSTRAINT,
        summary="what the scan could not see about this project, and why",
        trigger="before treating an absence in the documentation as an absence in the estate",
    ),
    MemoryFileSpec(
        filename="lessons-learned.md",
        kind=MaterialKind.CONSTRAINT,
        summary="corrections a person made to this project's documentation",
        trigger="before repeating an interpretation someone has already corrected",
    ),
    MemoryFileSpec(
        filename="decisions.md",
        kind=MaterialKind.REFERENCE,
        summary="what the documentation run decided about this project, and why",
        trigger="before contradicting how the documentation is scoped or organised",
    ),
    MemoryFileSpec(
        filename="key-notes.md",
        kind=MaterialKind.REFERENCE,
        summary="notes the run kept that did not belong anywhere else",
        trigger="before answering a question it covers",
    ),
    MemoryFileSpec(
        filename="open-questions.md",
        kind=MaterialKind.REFERENCE,
        summary=(
            "what the run could not settle, and the questions it would put to the client"
        ),
        trigger="before presenting something as settled, and when asked what is still open",
    ),
)

#: Where the extracts land under ``.nexus/project/``. Its own directory rather
#: than sharing ``references/`` with the uploaded documents: these are the
#: pipeline's notes, not something a person handed over, and a reader able to
#: tell those apart from the path alone is a reader who can attribute a claim
#: correctly.
MEMORY_DIRNAME: Final = "generation"

#: The always-loaded section heading. Public because the workspace splices it
#: into ``AGENTS.md`` and the tests key off it.
MEMORY_SECTION_HEADING: Final = "## What the documentation run recorded"

#: The read-whole ceiling, reused rather than re-chosen. A memory file is the
#: same kind of object as a mounted document -- text the agent reads in one go
#: -- so it gets the same answer to "can the agent hold this".
MEMORY_MAX_CHARS: Final = MATERIALIZE_MAX_CHARS


# -- parsing the record ------------------------------------------------------


class MemoryEntry(BaseModel):
    """One appended entry, as the record stores it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: str = ""
    author: str = AUTHOR_AGENT
    scan_fingerprint: str = ""
    title: str = ""
    body: str = ""
    #: The header line verbatim, so the extract reproduces the record rather
    #: than a re-rendering of it.
    header: str = ""

    @property
    def human_authored(self) -> bool:
        return self.author.lower() == AUTHOR_HUMAN


def short_fingerprint(fingerprint: str) -> str:
    return fingerprint[:SHORT_FINGERPRINT_CHARS]


def same_fingerprint(left: str, right: str) -> bool:
    """True when two fingerprints agree, tolerating the short display form.

    The writer records a 12-character prefix in an entry header and the full
    digest in the snapshot, so a plain equality test would report every entry
    ever written as stale.
    """

    if not left or not right:
        return False
    shortest = min(len(left), len(right))
    return left[:shortest] == right[:shortest]


def _parse_header(rest: str) -> tuple[str, str, str, str]:
    """Parse a header body into ``(timestamp, author, scan, title)``."""

    timestamp = ""
    author = ""
    scan = ""
    titles: list[str] = []
    for raw in rest.split("|"):
        field = raw.strip()
        if not field:
            continue
        match = _FIELD_RE.match(field)
        if match:
            key = match.group("key").lower()
            value = match.group("value").strip()
            if key == "author":
                author = value
                continue
            if key == "scan":
                scan = value
                continue
            titles.append(field)
            continue
        if not timestamp and _ISO_RE.match(field):
            timestamp = field
            continue
        titles.append(field)
    return timestamp, author, scan, " | ".join(titles)


def split_preamble(text: str) -> tuple[str, str]:
    """Split a memory file into ``(preamble including the marker, entries)``.

    The preamble is the writer's instructions to the generation agent -- how to
    append, what to put where. It is dropped from the extract on purpose: it
    tells a reader to write into a file that is read-only here, and the one
    behaviour this lane must never learn is writing back into the record.
    """

    index = text.find(ENTRIES_MARKER)
    if index == -1:
        return "", text
    newline = text.find("\n", index)
    if newline == -1:
        return text, ""
    return text[: newline + 1], text[newline + 1 :]


def parse_entries(text: str) -> tuple[MemoryEntry, ...]:
    """Parse the entries region of a memory file, in file (append) order.

    Tolerant in exactly the way the writer is: a header missing ``author`` or
    ``scan`` still parses, because discarding something an agent recorded is
    worse than carrying an entry that has to be flagged as uncheckable.
    """

    _, entries_text = split_preamble(text)
    headers: list[tuple[str, str, str, str, str]] = []
    bodies: list[list[str]] = []
    for line in entries_text.split("\n"):
        match = _ENTRY_HEADER_RE.match(line)
        if match:
            timestamp, author, scan, title = _parse_header(match.group("rest"))
            headers.append((timestamp, author, scan, title, line))
            bodies.append([])
        elif headers:
            bodies[-1].append(line)

    return tuple(
        MemoryEntry(
            timestamp=timestamp,
            author=author or AUTHOR_AGENT,
            scan_fingerprint=scan,
            title=title,
            body="\n".join(body).strip("\n"),
            header=header,
        )
        for (timestamp, author, scan, title, header), body in zip(headers, bodies)
    )


# -- staleness ---------------------------------------------------------------


def staleness_note(entry: MemoryEntry, snapshot_fingerprint: str) -> str:
    """The line that goes under a header the snapshot cannot vouch for, or "".

    The three cases, and their wording, are the writer's
    (``project_memory.annotate_for_review``). Carrying them across rather than
    inventing a second policy is the point: an operator reading the durable
    record and an agent reading this extract should be told the same thing
    about the same entry.

    The reference point differs, and only because the reader does. In a run,
    "current" is the scan that run just read. Here there is no run, so the
    reference is ``_scan_snapshot.json`` -- the scan the record as a whole was
    last persisted against. An entry that disagrees with it predates the last
    documentation run and describes an estate that has since been re-scanned.
    """

    current = short_fingerprint(snapshot_fingerprint)
    scan = entry.scan_fingerprint
    if scan and snapshot_fingerprint and same_fingerprint(scan, snapshot_fingerprint):
        return ""
    if not scan:
        return (
            f"{NOTE_PREFIX} This entry carries no scan fingerprint, so it cannot be "
            f"checked against the project's last scan ({current or 'unknown'}). "
            "Confirm it against current evidence before relying on it."
        )
    if not snapshot_fingerprint:
        return (
            f"{NOTE_PREFIX} Written against scan {short_fingerprint(scan)}. The "
            "project's scan snapshot is missing, so this cannot be checked. Confirm "
            "it against current evidence before relying on it."
        )
    if entry.human_authored:
        return (
            f"{NOTE_PREFIX} Human-authored against scan {short_fingerprint(scan)}; the "
            f"project's last scan is {current}. A human correction outranks an agent "
            "conclusion, so treat this as authoritative unless current evidence "
            "directly contradicts it -- and say so explicitly if it does."
        )
    return (
        f"{REVIEW_PREFIX} Written against scan {short_fingerprint(scan)}; the project's "
        f"last scan is {current}. Re-verify against current evidence before reusing "
        "this, and do not cite it as established fact until you have."
    )


def _keep_priority(entry: MemoryEntry, snapshot_fingerprint: str) -> int:
    """How hard to hold on to an entry when the file will not fit.

    Higher survives. The order encodes the two rules the record already states:
    a human correction outranks an agent conclusion, and an entry the snapshot
    cannot vouch for is the least trustworthy thing in the file. Dropping by
    age alone would have thrown away a person's correction to keep an agent's
    superseded guess.
    """

    if entry.human_authored:
        return 2
    if staleness_note(entry, snapshot_fingerprint):
        return 0
    return 1


# -- rendering the extract ---------------------------------------------------


def _entry_block(entry: MemoryEntry, snapshot_fingerprint: str) -> str:
    note = staleness_note(entry, snapshot_fingerprint)
    header = entry.header or "###"
    parts = [header]
    if note:
        parts.append(note)
    if entry.body:
        parts.append(entry.body)
    return "\n".join(parts)


def _document_header(
    spec: MemoryFileSpec,
    *,
    project_id: str,
    fingerprint: str,
    captured_at_iso: str,
    run_id: int | None,
    total: int,
    shown: int,
) -> str:
    """The provenance block, which has to answer four questions at once.

    Where this text came from, that it is read-only, that it is internal rather
    than client documentation, and -- when the file did not fit -- exactly what
    is missing and where the whole of it lives. A reduction that does not
    announce itself is the failure mode this lane cares most about: the agent
    reads it, believes it is complete, and says so with confidence.
    """

    scan = short_fingerprint(fingerprint) or "unknown"
    run = f"run {run_id}" if run_id is not None else "run unknown"
    captured = captured_at_iso or "unknown"
    lines = [
        (
            "> Read-only extract of the documentation run's memory for project "
            f"`{project_id}`, file `{spec.filename}`."
        ),
        (
            "> Nexus copied it here from the generation record. Nothing written in "
            "this workspace is read back into that record, and you must not try to "
            "append to it."
        ),
        (
            f"> Record: {total} {'entry' if total == 1 else 'entries'}, last "
            f"persisted against scan {scan} ({run}, {captured})."
        ),
    ]
    if shown < total:
        lines.append(
            f"> Shown here: {shown} of {total}. This is a reduction, not the record. "
            f"The {total - shown} omitted "
            f"{'entry was' if total - shown == 1 else 'entries were'} dropped to keep "
            "this file readable in one pass: unverifiable entries first, then oldest, "
            "plus any single entry too large to fit on its own. The complete record "
            f"is `{KNOWLEDGE_DIRNAME}/{spec.filename}` under the project's generation "
            "output, which is not reachable from this workspace; ask for it if the "
            "omitted entries matter."
        )
    lines.append(
        "> These are internal working notes, not published client documentation. Use "
        "them to know what is uncertain and what this client's words mean; never "
        "present one to a client as a finding."
    )
    return "\n".join(lines)


class MemoryDocument(BaseModel):
    """One memory file, ready to mount."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    kind: MaterialKind
    summary: str
    trigger: str
    #: Entries in the record.
    entry_count: int
    #: Entries in this extract. Lower than ``entry_count`` only when the file
    #: exceeded the read-whole ceiling.
    shown_count: int
    #: How many of the shown entries carry a staleness note.
    flagged_count: int
    #: The extract as it will be written, provenance header included.
    body: str

    @property
    def omitted_count(self) -> int:
        return self.entry_count - self.shown_count

    @property
    def relpath(self) -> str:
        """Where this lands under ``.nexus/project/``."""

        return f"{MEMORY_DIRNAME}/{self.filename}"


def _build_document(
    spec: MemoryFileSpec,
    entries: tuple[MemoryEntry, ...],
    *,
    project_id: str,
    fingerprint: str,
    captured_at_iso: str,
    run_id: int | None,
    ceiling: int,
) -> MemoryDocument | None:
    """Render one file's extract, dropping whole entries if it will not fit.

    Returns None when there is nothing to mount. An empty memory file is the
    common case on a first run -- it exists, and says in words that it is empty
    -- and mounting it would cost the agent a read to learn nothing, while
    advertising it would cost every turn a line that points at nothing. The
    projection's rule is silence over empty scaffolding, and this is where that
    is decided for memory.

    Truncation is by ENTRY, never by byte. A half-sentence is indistinguishable
    from a complete claim once the agent has read it, whereas a missing entry
    is announced in the header with a count.

    Selection is greedy over a preference order -- :func:`_keep_priority`
    descending, then newest first -- and it SKIPS rather than stops when an
    entry will not fit. That last part was measured, not guessed: the real
    ``open-questions.md`` on ``ettm_fix`` ends with a single 12.8 KB entry, and
    a rule that simply dropped from the oldest end kept that one entry and
    discarded the other eleven -- or, under a tighter ceiling, mounted nothing
    at all because the one survivor was itself too big. Skipping the entry that
    does not fit and continuing down the list keeps nine useful entries where
    the simpler rule kept none.

    The assembled body is re-measured on every tentative keep rather than
    estimated, because the header's own text changes with the omitted count.
    Only reached when the whole file does not fit; the common case is one
    length check.

    A consequence worth stating so nobody "fixes" it: the entry COUNT is not
    monotonic in the ceiling. Raise the ceiling far enough to admit that 12.8 KB
    entry and it is taken first, leaving room for three others instead of six
    smaller ones. Bytes kept are monotonic; entries are not, because the
    preference is for the newest material and not for the largest number of
    headers.
    """

    if not entries:
        return None

    blocks = [_entry_block(entry, fingerprint) for entry in entries]

    def _assemble(indices: list[int]) -> str:
        header = _document_header(
            spec,
            project_id=project_id,
            fingerprint=fingerprint,
            captured_at_iso=captured_at_iso,
            run_id=run_id,
            total=len(entries),
            shown=len(indices),
        )
        return "\n\n".join([header, *(blocks[index] for index in indices)]) + "\n"

    kept = list(range(len(entries)))
    body = _assemble(kept)

    if len(body) > ceiling:
        keep_order = sorted(
            range(len(entries)),
            # Highest trust first; within a tier, newest first. Negating the
            # index is what makes "newest" the preference without reversing the
            # whole key and inverting the trust order with it.
            key=lambda index: (-_keep_priority(entries[index], fingerprint), -index),
        )
        kept = []
        for index in keep_order:
            candidate = sorted([*kept, index])
            attempt = _assemble(candidate)
            if len(attempt) <= ceiling:
                kept = candidate
                body = attempt
        if not kept:
            # Never mount a header with no entries under it: a file whose whole
            # content is an apology costs a read and returns nothing. The
            # manifest line disappears with it, so the agent is not pointed at
            # something it cannot use.
            logger.warning(
                "Generation memory %s for project %s: no single entry fits in %d "
                "characters; not mounting it",
                spec.filename,
                project_id,
                ceiling,
            )
            return None

    return MemoryDocument(
        filename=spec.filename,
        kind=spec.kind,
        summary=spec.summary,
        trigger=spec.trigger,
        entry_count=len(entries),
        shown_count=len(kept),
        flagged_count=sum(
            1 for index in kept if staleness_note(entries[index], fingerprint)
        ),
        body=body,
    )


class GenerationMemory(BaseModel):
    """Everything the documentation run recorded that is worth mounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    #: The scan the record was last persisted against. Empty when the snapshot
    #: is missing, which makes every entry uncheckable rather than current.
    fingerprint: str = ""
    captured_at_iso: str = ""
    run_id: int | None = None
    documents: tuple[MemoryDocument, ...] = ()
    #: Digest of everything that fed this compile. Same contract as the
    #: material repository's: an unchanged key promises an unchanged prefix.
    cache_key: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.documents

    @property
    def flagged_count(self) -> int:
        return sum(document.flagged_count for document in self.documents)


def render_memory_section(memory: GenerationMemory | None) -> str:
    """The always-loaded block that makes the extracts reachable, or "".

    Without this the mounted files are unreachable in practice: the agent has
    no reason to list a directory it was never told about, which is the exact
    failure -- a record on disk that nothing reads -- this whole module exists
    to end. It is one line per file, so the per-turn cost is bounded by the six
    files that can exist, and the bodies stay on demand. That asymmetry is the
    same one the document manifest uses, and it is how a 30 KB question list
    stays reachable for the price of a sentence.
    """

    if memory is None or memory.is_empty:
        return ""

    # Constraints before references, for the reason the document manifest
    # sorts the same way: the list is read top-down, so the file that changes
    # whether an answer is right goes above the one that merely informs it.
    ordered = sorted(
        memory.documents,
        key=lambda document: (MATERIAL_KIND_PRIORITY[document.kind], document.filename),
    )
    bullets: list[str] = []
    for document in ordered:
        if document.omitted_count:
            count = f"{document.shown_count} of {document.entry_count} entries"
        else:
            plural = "entry" if document.entry_count == 1 else "entries"
            count = f"{document.entry_count} {plural}"
        flagged = (
            f", {document.flagged_count} flagged for re-verification"
            if document.flagged_count
            else ""
        )
        bullets.append(
            f"- `.nexus/project/{document.relpath}` -- {document.summary}; read it "
            f"{document.trigger} ({count}{flagged})."
        )

    # Blank lines between the blocks, not decoration: without one the trailing
    # paragraph is lazy continuation of the last bullet, and the warning about
    # flagged entries reads as part of whichever file happened to sort last.
    return "\n\n".join(
        [
            MEMORY_SECTION_HEADING,
            (
                "The documentation pipeline keeps working notes on this project. "
                "They are mounted read-only below, they are internal rather than "
                "client-facing, and they are the only record of what the run was "
                "unsure about."
            ),
            "\n".join(bullets),
            (
                "A flagged entry was written against a different scan than the one "
                "the record was last persisted against; the file says so on the "
                "entry itself. Do not cite a flagged entry as established fact, and "
                "never quote these notes to a client as a finding."
            ),
        ]
    )


# -- reading it off disk -----------------------------------------------------


def _read_text(path: Path) -> str:
    """Read one memory file, or "" for anything that goes wrong.

    ``newline=""`` because the writer's bytes are the record: universal-newline
    translation would rewrite CRLF on the way in, and an extract that differs
    from the record by invisible bytes is an extract nobody can diff.
    """

    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            return handle.read()
    except OSError:
        logger.warning("Could not read generation memory file %s", path, exc_info=True)
        return ""


def _read_snapshot(knowledge_dir: Path) -> tuple[str, str, int | None]:
    """``(fingerprint, captured_at_iso, run_id)`` from the durable snapshot.

    A missing or unreadable snapshot is not an error: it yields an empty
    fingerprint, which makes every entry uncheckable rather than current. That
    is the correct direction -- an unverifiable entry should read as
    unverifiable, never as fresh.
    """

    path = knowledge_dir / SNAPSHOT_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.info(
            "No usable scan snapshot at %s; generation memory entries will be "
            "reported as uncheckable",
            path,
        )
        return "", "", None
    if not isinstance(payload, dict):
        return "", "", None

    fingerprint = payload.get("fingerprint")
    captured = payload.get("captured_at_iso")
    run_id = payload.get("run_id")
    return (
        fingerprint if isinstance(fingerprint, str) else "",
        captured if isinstance(captured, str) else "",
        run_id if isinstance(run_id, int) else None,
    )


def _cache_key(project_id: str, fingerprint: str, documents: list[MemoryDocument]) -> str:
    payload = {
        "project_id": project_id,
        "fingerprint": fingerprint,
        "documents": [
            [
                document.filename,
                document.entry_count,
                document.shown_count,
                hashlib.sha256(document.body.encode("utf-8")).hexdigest(),
            ]
            for document in documents
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def read_generation_memory(
    knowledge_dir: Path | None, *, project_id: str, ceiling: int = MEMORY_MAX_CHARS
) -> GenerationMemory | None:
    """Compile the mountable memory for one project. Blocking; never raises.

    Returns None for every case in which nothing should be mounted -- no
    directory, no files, no entries -- which is the same value
    :func:`compile_generation_memory` returns on failure. The caller does not
    need to tell "there is no memory" from "the memory could not be read",
    because the correct behaviour is identical: mount nothing, say nothing, and
    let the projection's sweep remove whatever a previous turn left behind.

    ``ceiling`` is a parameter so a test can exercise truncation without
    building a 32 KB fixture; production callers take the default.
    """

    if knowledge_dir is None:
        return None

    fingerprint, captured_at_iso, run_id = _read_snapshot(knowledge_dir)

    documents: list[MemoryDocument] = []
    for spec in MEMORY_FILES:
        path = knowledge_dir / spec.filename
        if not path.is_file():
            continue
        entries = parse_entries(_read_text(path))
        document = _build_document(
            spec,
            entries,
            project_id=project_id,
            fingerprint=fingerprint,
            captured_at_iso=captured_at_iso,
            run_id=run_id,
            ceiling=ceiling,
        )
        if document is not None:
            documents.append(document)

    if not documents:
        return None

    documents.sort(key=lambda document: document.filename)
    return GenerationMemory(
        project_id=project_id,
        fingerprint=fingerprint,
        captured_at_iso=captured_at_iso,
        run_id=run_id,
        documents=tuple(documents),
        cache_key=_cache_key(project_id, fingerprint, documents),
    )


async def compile_generation_memory(project_id: str | None) -> GenerationMemory | None:
    """The async entry point both callers use. Never raises.

    Mirrors ``compile_project_material``: one call per turn, off the event loop
    because it touches the filesystem, and degrading to None on anything that
    goes wrong. A turn that loses the memory section answers exactly as well as
    it did before the section existed; a turn that raises over it answers not
    at all.
    """

    if not project_id or not memory_enabled():
        return None
    try:
        knowledge_dir = await asyncio.to_thread(project_knowledge_dir, project_id)
        return await asyncio.to_thread(
            read_generation_memory, knowledge_dir, project_id=project_id
        )
    except Exception:  # deliberately blind: see the docstring
        logger.error(
            "Generation memory compile failed for project=%s; continuing without it",
            project_id,
            exc_info=True,
        )
        return None
