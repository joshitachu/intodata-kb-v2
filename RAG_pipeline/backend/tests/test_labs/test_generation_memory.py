"""Reading the documentation run's memory, and refusing to read it wrongly.

The record these tests parse is written by another sub-project
(`knowledge_base/mcp-server/.../services/project_memory.py`), so two classes of
failure matter here and only one of them is about this module:

* **Contract drift.** The entry header, the entries marker and the snapshot
  field names are a cross-distribution agreement that nothing enforces at
  import. `test_the_real_record_parses` reads the actual record on disk when it
  is there, so a format change shows up as a failing test rather than as a
  silently empty section in production.
* **Trust.** Everything else: a stale entry must be marked, a human correction
  must outrank an agent conclusion, a reduction must announce itself, and an
  extract must never carry the record's own "HOW TO WRITE HERE" instructions
  into a workspace where writing back is forbidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.harness.guidance.material import MATERIALIZE_MAX_CHARS, MaterialKind
from src.labs.generation_memory import (
    ENTRIES_MARKER,
    MEMORY_FILES,
    MEMORY_MAX_CHARS,
    MEMORY_SECTION_HEADING,
    NOTE_PREFIX,
    REVIEW_PREFIX,
    compile_generation_memory,
    memory_enabled,
    parse_entries,
    read_generation_memory,
    render_memory_section,
)
from src.labs.project_output import KNOWLEDGE_DIRNAME, project_knowledge_dir

PROJECT = "acme_landscape"
CURRENT = "50a547820030aaaa"
PREVIOUS = "9911223344556677"

#: The record's preamble, shortened but structurally identical to the real one:
#: instructions addressed to the GENERATION agent, telling it how to append.
PREAMBLE = f"""# Open Questions

WHAT THIS IS

  Questions this project has not answered yet.

HOW TO WRITE HERE

  Append a new entry at the end of this file. Never edit an existing entry.

{ENTRIES_MARKER}
"""


def entry(body: str, *, scan: str = CURRENT, author: str = "agent", stamp: str) -> str:
    return f"### {stamp} | author: {author} | scan: {scan}\n{body}\n"


def write_record(
    root: Path,
    *,
    project_id: str = PROJECT,
    files: dict[str, str] | None = None,
    snapshot: dict[str, object] | None = None,
) -> Path:
    """Lay out a project's knowledge directory the way the pipeline does."""

    knowledge = root / project_id / KNOWLEDGE_DIRNAME
    knowledge.mkdir(parents=True, exist_ok=True)
    for filename, text in (files or {}).items():
        (knowledge / filename).write_text(text, encoding="utf-8")
    if snapshot is not None:
        (knowledge / "_scan_snapshot.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
    return knowledge


def snapshot(fingerprint: str = CURRENT) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": PROJECT,
        "platform": "talend",
        "fingerprint": fingerprint,
        "captured_at_iso": "2026-09-17T18:27:42Z",
        "run_id": 260,
        "units": {},
    }


@pytest.fixture
def docs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    monkeypatch.delenv("LABS_GENERATION_MEMORY", raising=False)
    return root


# -- the on-disk contract ----------------------------------------------------


def test_entries_parse_in_append_order() -> None:
    text = PREAMBLE + entry("first", stamp="2026-09-01T00:00:00Z") + "\n" + entry(
        "second", stamp="2026-09-02T00:00:00Z"
    )

    entries = parse_entries(text)

    assert [item.body for item in entries] == ["first", "second"]
    assert [item.timestamp for item in entries] == [
        "2026-09-01T00:00:00Z",
        "2026-09-02T00:00:00Z",
    ]
    assert all(item.scan_fingerprint == CURRENT for item in entries)


def test_nothing_above_the_marker_is_an_entry() -> None:
    # The preamble contains a '###'-free instruction block today, but the rule
    # has to hold even if the writer starts using headings up there: the marker
    # is the boundary, not the absence of headings.
    text = (
        "# Open Questions\n\n### not an entry, this is documentation\n\n"
        + ENTRIES_MARKER
        + "\n"
        + entry("real", stamp="2026-09-01T00:00:00Z")
    )

    assert [item.body for item in parse_entries(text)] == ["real"]


def test_an_entry_missing_its_fields_still_parses() -> None:
    # Tolerant in exactly the way the writer is: discarding something an agent
    # recorded is worse than carrying one that has to be flagged.
    entries = parse_entries(f"{ENTRIES_MARKER}\n### 2026-09-01T00:00:00Z\nbody\n")

    assert len(entries) == 1
    assert entries[0].scan_fingerprint == ""
    assert entries[0].author == "agent"


# -- what gets mounted, and what does not ------------------------------------


def test_a_seed_only_file_is_not_mounted(docs_root: Path) -> None:
    # Five of the six files on a real first run contain nothing but the
    # preamble and "Empty. This is the first documentation run". Mounting that
    # costs the agent a read to learn nothing and costs every turn a line
    # pointing at it.
    knowledge = write_record(
        docs_root, files={"terminology.md": PREAMBLE}, snapshot=snapshot()
    )

    assert read_generation_memory(knowledge, project_id=PROJECT) is None


def test_a_project_with_no_knowledge_directory_mounts_nothing(docs_root: Path) -> None:
    assert project_knowledge_dir(PROJECT) is None
    assert read_generation_memory(None, project_id=PROJECT) is None


def test_the_preamble_never_reaches_the_extract(docs_root: Path) -> None:
    # The preamble tells the reader how to APPEND to the record. This workspace
    # is read-only by construction and the one behaviour it must never learn is
    # writing back, so the instructions to do so are dropped rather than copied.
    knowledge = write_record(
        docs_root,
        files={"open-questions.md": PREAMBLE + entry("q", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )

    memory = read_generation_memory(knowledge, project_id=PROJECT)

    assert memory is not None
    body = memory.documents[0].body
    assert "HOW TO WRITE HERE" not in body
    assert "Append a new entry" not in body
    assert "you must not try to append to it" in body


def test_every_memory_file_is_classified_and_none_is_evidence() -> None:
    # Role decides how a file is described and ordered; a file with no
    # classification would be described as nothing. EVIDENCE is excluded by
    # meaning: the whole point of these files is that they are NOT the
    # searchable corpus.
    assert {spec.filename for spec in MEMORY_FILES} == {
        "key-notes.md",
        "decisions.md",
        "lessons-learned.md",
        "terminology.md",
        "open-questions.md",
        "known-limitations.md",
    }
    for spec in MEMORY_FILES:
        assert spec.kind is not MaterialKind.EVIDENCE, spec.filename
        assert spec.summary and spec.trigger, spec.filename


def test_the_three_binding_files_are_constraints() -> None:
    # Terminology, limitations and human corrections change whether an answer
    # is right, not merely how well informed it is. Decisions, notes and open
    # questions inform without binding -- an open question typed as a
    # constraint would invite the agent to answer from the question.
    kinds = {spec.filename: spec.kind for spec in MEMORY_FILES}

    assert kinds["terminology.md"] is MaterialKind.CONSTRAINT
    assert kinds["known-limitations.md"] is MaterialKind.CONSTRAINT
    assert kinds["lessons-learned.md"] is MaterialKind.CONSTRAINT
    assert kinds["decisions.md"] is MaterialKind.REFERENCE
    assert kinds["key-notes.md"] is MaterialKind.REFERENCE
    assert kinds["open-questions.md"] is MaterialKind.REFERENCE


def test_the_read_whole_ceiling_is_the_document_ceiling() -> None:
    # A memory extract is the same kind of object as a mounted document -- text
    # the agent reads in one go -- so "can the agent hold this" gets one answer
    # in this codebase, not two.
    assert MEMORY_MAX_CHARS == MATERIALIZE_MAX_CHARS


# -- staleness ---------------------------------------------------------------


def _single(knowledge: Path) -> str:
    memory = read_generation_memory(knowledge, project_id=PROJECT)
    assert memory is not None
    return memory.documents[0].body


def test_an_entry_matching_the_snapshot_is_not_flagged(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={"terminology.md": PREAMBLE + entry("fresh", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )

    body = _single(knowledge)

    assert REVIEW_PREFIX not in body
    assert NOTE_PREFIX not in body


def test_the_short_form_fingerprint_still_matches(docs_root: Path) -> None:
    # The writer records a 12-character prefix in a header and the full digest
    # in the snapshot. A plain equality test would report every entry ever
    # written as stale, which would flag the whole record and teach the agent
    # to ignore the flag.
    knowledge = write_record(
        docs_root,
        files={
            "terminology.md": PREAMBLE
            + entry("fresh", scan=CURRENT[:12], stamp="2026-09-01T00:00:00Z")
        },
        snapshot=snapshot(CURRENT),
    )

    assert REVIEW_PREFIX not in _single(knowledge)


def test_a_stale_agent_entry_is_marked_not_to_be_cited(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={
            "terminology.md": PREAMBLE
            + entry("old conclusion", scan=PREVIOUS, stamp="2026-08-01T00:00:00Z")
        },
        snapshot=snapshot(),
    )

    body = _single(knowledge)

    assert REVIEW_PREFIX in body
    assert "do not cite it as established fact" in body


def test_a_stale_human_entry_stays_authoritative(docs_root: Path) -> None:
    # The asymmetry is the writer's, carried across rather than re-decided: a
    # person's correction outranks an agent's conclusion even when the estate
    # has been re-scanned since.
    knowledge = write_record(
        docs_root,
        files={
            "lessons-learned.md": PREAMBLE
            + entry(
                "the invoice lane is out of scope",
                scan=PREVIOUS,
                author="human",
                stamp="2026-08-01T00:00:00Z",
            )
        },
        snapshot=snapshot(),
    )

    body = _single(knowledge)

    assert NOTE_PREFIX in body
    assert "outranks an agent conclusion" in body
    assert REVIEW_PREFIX not in body


def test_an_entry_with_no_fingerprint_is_uncheckable_not_current(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={
            "terminology.md": f"{ENTRIES_MARKER}\n### 2026-09-01T00:00:00Z\nbody\n"
        },
        snapshot=snapshot(),
    )

    body = _single(knowledge)

    assert NOTE_PREFIX in body
    assert "carries no scan fingerprint" in body


def test_a_missing_snapshot_makes_everything_uncheckable(docs_root: Path) -> None:
    # Failing the other way -- treating an entry as current because there is
    # nothing to compare it against -- is the failure this whole rule exists to
    # prevent.
    knowledge = write_record(
        docs_root,
        files={"terminology.md": PREAMBLE + entry("x", stamp="2026-09-01T00:00:00Z")},
    )

    body = _single(knowledge)

    assert NOTE_PREFIX in body
    assert "scan snapshot is missing" in body


def test_an_unparseable_snapshot_is_treated_as_missing(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={"terminology.md": PREAMBLE + entry("x", stamp="2026-09-01T00:00:00Z")},
    )
    (knowledge / "_scan_snapshot.json").write_text("{not json", encoding="utf-8")

    memory = read_generation_memory(knowledge, project_id=PROJECT)

    assert memory is not None
    assert memory.fingerprint == ""
    assert NOTE_PREFIX in memory.documents[0].body


# -- the size ceiling --------------------------------------------------------


def _many(count: int, *, size: int, scan: str = CURRENT, author: str = "agent") -> str:
    return PREAMBLE + "\n".join(
        entry("x" * size, scan=scan, author=author, stamp=f"2026-09-{day:02d}T00:00:00Z")
        for day in range(1, count + 1)
    )


def test_an_oversized_file_is_reduced_by_whole_entries(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root, files={"open-questions.md": _many(20, size=800)}, snapshot=snapshot()
    )

    memory = read_generation_memory(knowledge, project_id=PROJECT, ceiling=5_000)

    assert memory is not None
    document = memory.documents[0]
    assert document.entry_count == 20
    assert 0 < document.shown_count < 20
    assert len(document.body) <= 5_000
    # Every kept entry is whole: no entry body is a prefix of the real one.
    assert document.body.count("x" * 800) == document.shown_count


def test_a_reduction_says_it_is_one(docs_root: Path) -> None:
    # A reduction that does not announce itself is the failure that matters:
    # the agent reads it, believes it is complete, and says so with confidence.
    knowledge = write_record(
        docs_root, files={"open-questions.md": _many(20, size=800)}, snapshot=snapshot()
    )

    memory = read_generation_memory(knowledge, project_id=PROJECT, ceiling=5_000)

    assert memory is not None
    body = memory.documents[0].body
    assert "This is a reduction, not the record" in body
    assert f"Shown here: {memory.documents[0].shown_count} of 20" in body
    assert f"`{KNOWLEDGE_DIRNAME}/open-questions.md`" in body


def test_an_entry_too_large_to_fit_is_skipped_not_fatal(docs_root: Path) -> None:
    # Measured on the real record: `ettm_fix`'s open-questions ends with a
    # 12.8 KB entry. A rule that dropped strictly from the oldest end kept only
    # that entry -- or, under a tighter ceiling, mounted nothing at all.
    text = (
        PREAMBLE
        + entry("small one", stamp="2026-09-01T00:00:00Z")
        + "\n"
        + entry("small two", stamp="2026-09-02T00:00:00Z")
        + "\n"
        + entry("z" * 20_000, stamp="2026-09-03T00:00:00Z")
    )
    knowledge = write_record(
        docs_root, files={"open-questions.md": text}, snapshot=snapshot()
    )

    memory = read_generation_memory(knowledge, project_id=PROJECT, ceiling=5_000)

    assert memory is not None
    document = memory.documents[0]
    assert document.shown_count == 2
    assert "small one" in document.body and "small two" in document.body
    assert "z" * 20_000 not in document.body


def test_a_human_entry_survives_a_reduction_that_drops_agent_entries(
    docs_root: Path,
) -> None:
    # Drop order is trust, not age. Losing a person's correction to keep an
    # agent's superseded guess is the one reduction that must not happen.
    text = (
        PREAMBLE
        + entry(
            "human correction",
            author="human",
            scan=PREVIOUS,
            stamp="2026-08-01T00:00:00Z",
        )
        + "\n"
        + "\n".join(
            entry("a" * 900, stamp=f"2026-09-{day:02d}T00:00:00Z") for day in range(1, 11)
        )
    )
    knowledge = write_record(
        docs_root, files={"open-questions.md": text}, snapshot=snapshot()
    )

    memory = read_generation_memory(knowledge, project_id=PROJECT, ceiling=4_000)

    assert memory is not None
    assert "human correction" in memory.documents[0].body


def test_a_file_whose_smallest_entry_does_not_fit_is_not_mounted(
    docs_root: Path,
) -> None:
    # A header with no entries under it is a file whose whole content is an
    # apology. Better to mount nothing and advertise nothing.
    knowledge = write_record(
        docs_root,
        files={"open-questions.md": PREAMBLE + entry("q" * 9_000, stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )

    assert read_generation_memory(knowledge, project_id=PROJECT, ceiling=2_000) is None


# -- the always-loaded section -----------------------------------------------


def test_the_section_names_every_mounted_file_constraints_first(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={
            "open-questions.md": PREAMBLE + entry("q", stamp="2026-09-01T00:00:00Z"),
            "terminology.md": PREAMBLE + entry("t", stamp="2026-09-01T00:00:00Z"),
        },
        snapshot=snapshot(),
    )
    memory = read_generation_memory(knowledge, project_id=PROJECT)

    section = render_memory_section(memory)

    assert section.startswith(MEMORY_SECTION_HEADING)
    terminology = section.index(".nexus/project/generation/terminology.md")
    questions = section.index(".nexus/project/generation/open-questions.md")
    # A constraint before a reference, for the reason the manifest sorts the
    # same way: the list is read top-down.
    assert terminology < questions


def test_the_section_is_empty_when_there_is_nothing_to_say() -> None:
    assert render_memory_section(None) == ""


def test_the_section_reports_flagged_entries(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={
            "terminology.md": PREAMBLE
            + entry("old", scan=PREVIOUS, stamp="2026-08-01T00:00:00Z")
        },
        snapshot=snapshot(),
    )
    memory = read_generation_memory(knowledge, project_id=PROJECT)

    section = render_memory_section(memory)

    assert "1 flagged for re-verification" in section
    assert "never quote these notes to a client as a finding" in section


def test_the_section_stays_a_bounded_per_turn_cost(docs_root: Path) -> None:
    # It is re-sent on every turn. Six files can exist, so the bound is
    # structural -- but only as long as the line stays one line per file.
    files = {
        spec.filename: PREAMBLE
        + "\n".join(
            entry("x" * 400, stamp=f"2026-09-{day:02d}T00:00:00Z") for day in range(1, 30)
        )
        for spec in MEMORY_FILES
    }
    knowledge = write_record(docs_root, files=files, snapshot=snapshot())
    memory = read_generation_memory(knowledge, project_id=PROJECT)

    section = render_memory_section(memory)

    assert memory is not None and len(memory.documents) == len(MEMORY_FILES)
    assert len(section) < 2_500, len(section)


# -- failing soft ------------------------------------------------------------


async def test_the_compile_returns_none_for_an_unbound_project() -> None:
    assert await compile_generation_memory(None) is None


async def test_the_compile_refuses_an_unsafe_project_id(docs_root: Path) -> None:
    # The id becomes a path segment. It arrives from RBAC-derived scope, and is
    # validated anyway.
    assert await compile_generation_memory("../../etc") is None


async def test_the_off_switch_suppresses_everything(
    docs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_record(
        docs_root,
        files={"open-questions.md": PREAMBLE + entry("q", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )
    assert await compile_generation_memory(PROJECT) is not None

    monkeypatch.setenv("LABS_GENERATION_MEMORY", "0")

    assert memory_enabled() is False
    assert await compile_generation_memory(PROJECT) is None


async def test_the_compile_survives_a_broken_record(
    docs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An enhancement must never be able to cost the turn its answer.
    write_record(
        docs_root,
        files={"open-questions.md": PREAMBLE + entry("q", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )

    def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("src.labs.generation_memory.read_generation_memory", _explode)

    assert await compile_generation_memory(PROJECT) is None


def test_an_unreadable_file_is_skipped_rather_than_raised(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={"terminology.md": PREAMBLE + entry("t", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )
    # A directory where a file belongs: `is_file()` is False, so it is skipped.
    (knowledge / "open-questions.md").mkdir()

    memory = read_generation_memory(knowledge, project_id=PROJECT)

    assert memory is not None
    assert [document.filename for document in memory.documents] == ["terminology.md"]


def test_the_cache_key_changes_only_when_the_extract_does(docs_root: Path) -> None:
    knowledge = write_record(
        docs_root,
        files={"open-questions.md": PREAMBLE + entry("q", stamp="2026-09-01T00:00:00Z")},
        snapshot=snapshot(),
    )
    first = read_generation_memory(knowledge, project_id=PROJECT)
    second = read_generation_memory(knowledge, project_id=PROJECT)
    assert first is not None and second is not None
    assert first.cache_key == second.cache_key

    (knowledge / "open-questions.md").write_text(
        PREAMBLE
        + entry("q", stamp="2026-09-01T00:00:00Z")
        + "\n"
        + entry("another", stamp="2026-09-02T00:00:00Z"),
        encoding="utf-8",
    )
    third = read_generation_memory(knowledge, project_id=PROJECT)

    assert third is not None
    assert third.cache_key != first.cache_key


# -- the real record ---------------------------------------------------------

REAL_ROOT = Path(__file__).resolve().parents[4] / "knowledge_base" / "output"
REAL_PROJECT = "ettm_fix"
REAL_KNOWLEDGE = REAL_ROOT / REAL_PROJECT / KNOWLEDGE_DIRNAME


@pytest.mark.skipif(
    not REAL_KNOWLEDGE.is_dir(),
    reason="no generated knowledge record on this machine",
)
def test_the_real_record_parses() -> None:
    """The cross-sub-project format contract, pinned against real bytes.

    Everything else here runs against fixtures this file wrote, which proves
    the module is self-consistent and nothing about whether it agrees with the
    writer. This is the test that fails if the header format, the entries
    marker or the snapshot field names change.
    """

    memory = read_generation_memory(REAL_KNOWLEDGE, project_id=REAL_PROJECT)

    assert memory is not None, "the real record produced nothing mountable"
    assert len(memory.fingerprint) > 12, "no scan fingerprint parsed from the snapshot"
    assert memory.run_id is not None

    by_name = {document.filename: document for document in memory.documents}
    # The five seed-only files are correctly absent; the one with real content
    # is present. If that inverts, either the writer changed or the empty-file
    # rule broke.
    assert "open-questions.md" in by_name, sorted(by_name)
    questions = by_name["open-questions.md"]
    assert questions.entry_count >= 10
    assert questions.kind is MaterialKind.REFERENCE
    assert len(questions.body) <= MEMORY_MAX_CHARS
    assert "HOW TO WRITE HERE" not in questions.body
    # One real entry carries `scan: manualFixJob`, which is not a fingerprint.
    # It has to be flagged, not silently trusted.
    assert questions.flagged_count >= 1
    assert REVIEW_PREFIX in questions.body
