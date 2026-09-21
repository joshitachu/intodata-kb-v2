"""Tests for the per-project documentation memory.

The headline properties under test:

* memory SURVIVES a run -- ``docs_staging/`` is destroyed on every run, so the
  record lives in ``output/{project_id}/knowledge/`` and is staged in and out;
* the continuity note is deterministic Python and names designs, never values;
* a remembered conclusion written against a different scan is marked for
  review instead of being silently trusted;
* a human correction is never overwritten by a later run;
* concurrent appends to one file do not lose an entry;
* the staged directory is ``_knowledge/``, underscore-prefixed, because the
  RAG nested-document glob and the delivered-document count both admit any
  segment that is not ``_``- or ``.``-prefixed -- a staged ``knowledge/`` would
  push the agent's private notes into the client corpus.
"""

from __future__ import annotations

import json
import os
import threading

from pathlib import Path

import pytest

from mcp_server.services.project_memory import (
    AUTHOR_HUMAN,
    DURABLE_DIRNAME,
    ENTRIES_MARKER,
    MEMORY_FILES,
    NOTE_PREFIX,
    REVIEW_PREFIX,
    SINCE_LAST_RUN_FILENAME,
    SNAPSHOT_FILENAME,
    STAGED_DIRNAME,
    annotate_for_review,
    append_entry,
    ascii_only,
    build_snapshot,
    compute_memory_fingerprint,
    compute_shared_artifact_fingerprint,
    compute_unit_facts,
    diff_snapshots,
    durable_memory_dir,
    load_snapshot,
    normalize_entries,
    parse_entries,
    persist_project_memory,
    render_since_last_run,
    save_snapshot,
    seed_memory,
    staged_memory_dir,
    stage_project_memory,
)

ETTM_SCAN = (
    Path(__file__).resolve().parents[3] / "intodoc-output" / "ettm_TEST_Claude"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _job(name: str, group: str, *, components: int = 2, extra: dict | None = None) -> dict:
    job = {
        "job_id": name,
        "name": name,
        "group": group,
        "components": [
            {"unique_name": f"tDBInput_{i}", "component_type": "tDBInput"}
            for i in range(components)
        ],
        "tmap_data": [],
        "context_groups": [group],
        "trunjob_refs": [],
    }
    if extra:
        job.update(extra)
    return job


def _write_scan(scan_root: Path, jobs: list[dict], *, shared: dict | None = None) -> None:
    """Write a scan cache in the per-unit ``scan/jobs/{group}/`` layout.

    Rewrites the unit tree from scratch so a second call models a re-scan
    (units really can disappear) rather than an accumulation.
    """
    import shutil as _shutil

    _shutil.rmtree(scan_root / "scan" / "jobs", ignore_errors=True)
    for job in jobs:
        # The on-disk directory name is irrelevant to the module (it reads the
        # group out of the JSON), but it has to be a real directory.
        group_dir = scan_root / "scan" / "jobs" / (job["group"] or "ungrouped")
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / f"{job['job_id']}.json").write_text(
            json.dumps(job), encoding="utf-8"
        )
    scan_dir = scan_root / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "contexts.json").write_text(
        json.dumps(shared if shared is not None else {"contexts": []}), encoding="utf-8"
    )


def _stage(
    tmp_path: Path,
    jobs: list[dict],
    *,
    shared: dict | None = None,
    run_id: int | None = 1,
    unit_allowlist: set[str] | None = None,
    partition_name: str | None = None,
):
    scan_root = tmp_path / "scan_cache"
    _write_scan(scan_root, jobs, shared=shared)
    return stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="proj",
        scan_output_dir=scan_root,
        platform="talend",
        partition_name=partition_name,
        unit_allowlist=unit_allowlist,
        run_id=run_id,
    )


def _persist(tmp_path: Path, result, *, success: bool = True):
    """Finish a run: merge memory back and clear staging, as the service does."""
    return persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=result.snapshot,
        success=success,
    )


# --------------------------------------------------------------------------- #
# Seeding: honest, and empty when it is empty
# --------------------------------------------------------------------------- #

def test_first_run_seeds_every_file_and_says_it_is_empty(tmp_path: Path) -> None:
    result = _stage(tmp_path, [_job("A_Main", "Flows")])

    assert set(result.seeded_files) == set(MEMORY_FILES)
    for name in MEMORY_FILES:
        durable = result.durable_dir / name
        staged = result.staged_dir / name
        assert durable.is_file(), name
        assert staged.is_file(), name
        text = durable.read_text(encoding="utf-8")
        assert "WHAT THIS IS" in text
        assert "first documentation run" in text
        assert "Do not invent history" in text
        assert ENTRIES_MARKER in text
        # Honest means empty: a seeded file has no entries.
        assert parse_entries(text) == []
    assert result.delta.first_run is True
    assert result.entries_total == 0


def test_seeding_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.durable_dir / "lessons-learned.md",
        "Run 1 claimed AssetCheck writes to tMSSqlOutput_1. It does not.",
        author=AUTHOR_HUMAN,
        scan_fingerprint=first.fingerprint,
        title="correction",
    )

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)

    assert second.seeded_files == ()
    text = (second.durable_dir / "lessons-learned.md").read_text(encoding="utf-8")
    assert "tMSSqlOutput_1" in text
    entries = parse_entries(text)
    assert len(entries) == 1
    assert entries[0].human_authored is True


def test_a_file_deleted_by_hand_is_reseeded_without_claiming_history(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    _persist(tmp_path, first)
    (first.durable_dir / "terminology.md").unlink()

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)

    assert second.seeded_files == ("terminology.md",)
    text = (second.durable_dir / "terminology.md").read_text(encoding="utf-8")
    assert "Seeded during a later run" in text
    assert "first documentation run" not in text


# --------------------------------------------------------------------------- #
# Survival across runs
# --------------------------------------------------------------------------- #

def test_memory_survives_a_staging_wipe(tmp_path: Path) -> None:
    """The point of the whole module: docs_staging/ is destroyed every run."""
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.staged_dir / "key-notes.md",
        "REFERENCES group holds shared joblets only; it has no entry points.",
        scan_fingerprint=first.fingerprint,
    )
    persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=True,
    )

    # Simulate the atomic swap: the whole staging tree goes away.
    import shutil

    shutil.rmtree(tmp_path / "output" / "docs_staging", ignore_errors=True)

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)

    staged = (second.staged_dir / "key-notes.md").read_text(encoding="utf-8")
    assert "shared joblets only" in staged
    assert second.entries_total == 1


def test_the_staged_directory_name_is_what_keeps_memory_out_of_the_client_corpus(
    tmp_path: Path,
) -> None:
    """``_knowledge`` cannot publish; ``knowledge`` would.

    ``RagBridge._read_nested_md_files`` globs ``docs/**/*.md`` and admits any
    path whose every segment is free of a leading ``_`` or ``.``, and
    ``projects._count_staged_docs`` applies the same rule to the delivered
    document count. This pins BOTH directions: the underscore-free name really
    would be pushed and counted, and the name actually used really is not.
    """
    from mcp_server.services.rag_bridge import RagBridge

    published = tmp_path / "published_docs"
    published.mkdir()
    (published / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    for dirname in (DURABLE_DIRNAME, STAGED_DIRNAME, "_provenance"):
        (published / dirname).mkdir()
        (published / dirname / "open-questions.md").write_text("q", encoding="utf-8")

    names = {doc["filename"] for doc in RagBridge()._read_md_files(str(published))}

    assert f"{DURABLE_DIRNAME}/open-questions.md" in names, (
        "an underscore-free memory directory inside docs/ IS pushed to RAG -- "
        "which is exactly why the staged name is underscore-prefixed"
    )
    assert f"{STAGED_DIRNAME}/open-questions.md" not in names
    assert not any(name.startswith("_provenance") for name in names)
    assert STAGED_DIRNAME.startswith("_")


def test_persist_copies_back_and_clears_staging(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.staged_dir / "open-questions.md",
        "Is 4PS the system of record for supplier bank details?",
        scan_fingerprint=first.fingerprint,
    )

    result = persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=True,
    )
    assert result.staged_removed is True
    assert not staged_memory_dir(tmp_path / "output" / "docs_staging").exists()
    assert "open-questions.md" in result.persisted_files
    assert result.new_entries == 1
    durable = (durable_memory_dir(tmp_path / "output") / "open-questions.md").read_text(
        encoding="utf-8"
    )
    assert "system of record for supplier bank details" in durable


def test_only_a_successful_run_advances_the_snapshot(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    failed = persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=False,
    )
    assert failed.snapshot_advanced is False
    assert load_snapshot(first.durable_dir) is None

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)
    # Still a first run for continuity purposes: the baseline never moved.
    assert second.delta.first_run is True

    ok = persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=second.snapshot,
        success=True,
    )
    assert ok.snapshot_advanced is True
    assert load_snapshot(second.durable_dir) is not None

    third = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=3)
    assert third.delta.first_run is False


def test_failed_run_still_keeps_what_the_agent_learned(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.staged_dir / "open-questions.md",
        "Which environment owns the invoice sequence?",
        scan_fingerprint=first.fingerprint,
    )

    persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=False,
    )

    durable = (first.durable_dir / "open-questions.md").read_text(encoding="utf-8")
    assert "invoice sequence" in durable


def test_resume_adopts_an_orphaned_staged_copy_without_duplicating(tmp_path: Path) -> None:
    """A run killed between provider return and persist leaves staging behind."""
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.staged_dir / "key-notes.md",
        "Only two groups carry entry points.",
        scan_fingerprint=first.fingerprint,
    )
    # No persist call: the process died here.

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)

    assert second.adopted_orphan is True
    durable = (second.durable_dir / "key-notes.md").read_text(encoding="utf-8")
    assert durable.count("Only two groups carry entry points.") == 1
    assert second.entries_total == 1
    # Staging was rebuilt from the durable home, not appended to.
    staged = (second.staged_dir / "key-notes.md").read_text(encoding="utf-8")
    assert staged.count("Only two groups carry entry points.") == 1


# --------------------------------------------------------------------------- #
# The continuity note
# --------------------------------------------------------------------------- #

def test_first_run_note_says_so_plainly(tmp_path: Path) -> None:
    result = _stage(tmp_path, [_job("A_Main", "Flows"), _job("B_Sub", "Flows")])
    note = (result.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert "FIRST RUN" in note
    assert "no previous successful" in note
    assert "Do not describe anything as 'unchanged'" in note
    assert "2 designs across 1 groups" in note
    assert note.isascii()


def test_note_reports_added_removed_modified_and_group_changes(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows"), _job("Gone", "Flows")])
    _persist(tmp_path, first)

    second = _stage(
        tmp_path,
        [
            _job("A_Main", "Flows", components=5),   # modified
            _job("New_Job", "Logging"),              # added + new group
        ],
        run_id=2,
    )
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert "SCAN CHANGED" in note
    assert second.delta.units_added == ["New_Job"]
    assert second.delta.units_removed == ["Gone"]
    assert [c.unit for c in second.delta.units_modified] == ["A_Main"]
    assert second.delta.units_modified[0].dimensions == ["components 2 -> 5"]
    assert second.delta.groups_added == ["Logging"]
    assert second.delta.groups_removed == []
    assert "| A_Main | Flows | components 2 -> 5 |" in note
    assert note.isascii()


def test_note_reports_no_change_when_the_scan_is_identical(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    _persist(tmp_path, first)

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert second.delta.changed is False
    assert "NO CHANGE" in note
    assert "open-questions.md" in note


def test_note_separates_shared_artifact_drift_from_design_change(tmp_path: Path) -> None:
    """A changed context group is not a changed design, and must not read as one."""
    first = _stage(tmp_path, [_job("A_Main", "Flows")], shared={"contexts": ["PROD"]})
    _persist(tmp_path, first)

    second = _stage(
        tmp_path,
        [_job("A_Main", "Flows")],
        shared={"contexts": ["PROD", "ACC"]},
        run_id=2,
    )
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert second.delta.shared_artifacts_changed is True
    assert second.delta.unit_level_change is False
    assert "No design-level change was detected" in note
    assert "unverified" in note


def test_note_reports_a_design_moved_between_groups(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    _persist(tmp_path, first)

    second = _stage(tmp_path, [_job("A_Main", "Logging")], run_id=2)

    assert [(c.unit, c.previous_group, c.group) for c in second.delta.units_moved] == [
        ("A_Main", "Flows", "Logging")
    ]
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")
    assert "| A_Main | Flows | Logging |" in note


def test_note_never_reproduces_a_configuration_value(tmp_path: Path) -> None:
    """Credential safety: the note is derived from scan data, so it must not
    carry values. A changed password shows up as a changed design."""
    secret = "S3cr3t-Pa55w0rd-DO-NOT-LEAK"
    before = _job(
        "A_Main",
        "Flows",
        extra={
            "components": [
                {
                    "unique_name": "tDBInput_1",
                    "component_type": "tDBInput",
                    "params": [{"name": "PASS", "value": "old-value"}],
                }
            ]
        },
    )
    after = _job(
        "A_Main",
        "Flows",
        extra={
            "components": [
                {
                    "unique_name": "tDBInput_1",
                    "component_type": "tDBInput",
                    "params": [{"name": "PASS", "value": secret}],
                }
            ]
        },
    )

    first = _stage(tmp_path, [before])
    _persist(tmp_path, first)
    second = _stage(tmp_path, [after], run_id=2)

    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")
    snapshot_text = json.dumps(second.snapshot.model_dump())

    assert secret not in note
    assert "old-value" not in note
    assert secret not in snapshot_text
    # It IS reported as a change, just without the value.
    assert [c.unit for c in second.delta.units_modified] == ["A_Main"]
    assert second.delta.units_modified[0].dimensions == [
        "content changed with no change in structural counts"
    ]
    assert "A_Main" in note


def test_note_and_delta_are_deterministic(tmp_path: Path) -> None:
    jobs_before = [_job(f"J{i:02d}", "Flows") for i in range(5)]
    jobs_after = [_job(f"J{i:02d}", "Flows", components=3) for i in range(5)]

    first = _stage(tmp_path, jobs_before)
    _persist(tmp_path, first)
    second = _stage(tmp_path, jobs_after, run_id=2)
    note_a = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    third = _stage(tmp_path, jobs_after, run_id=3)
    note_b = (third.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert note_a == note_b


def test_long_lists_are_capped_with_an_explicit_remainder() -> None:
    from mcp_server.services.project_memory import MAX_LISTED_ITEMS, ScanDelta

    delta = ScanDelta(
        first_run=False,
        previous_fingerprint="a" * 64,
        current_fingerprint="b" * 64,
        units_added=[f"J{i:04d}" for i in range(MAX_LISTED_ITEMS + 25)],
    )
    note = render_since_last_run(
        delta, project_id="p", platform="talend", partition_name=None
    )

    assert "and 25 more" in note
    assert f"J{MAX_LISTED_ITEMS + 24:04d}" not in note


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #

def test_agent_entry_written_against_an_older_scan_is_flagged_for_review(
    tmp_path: Path,
) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.durable_dir / "key-notes.md",
        "A_Main is the only entry point.",
        scan_fingerprint=first.fingerprint,
    )
    _persist(tmp_path, first)

    second = _stage(tmp_path, [_job("A_Main", "Flows", components=9)], run_id=2)

    assert second.fingerprint != first.fingerprint
    assert second.entries_flagged == 1
    staged = (second.staged_dir / "key-notes.md").read_text(encoding="utf-8")
    assert REVIEW_PREFIX in staged
    assert "Re-verify against current evidence" in staged
    # The record itself is untouched.
    durable = (second.durable_dir / "key-notes.md").read_text(encoding="utf-8")
    assert REVIEW_PREFIX not in durable
    assert NOTE_PREFIX not in durable


def test_entry_matching_the_current_scan_is_not_flagged(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.durable_dir / "key-notes.md",
        "A_Main is the only entry point.",
        scan_fingerprint=first.fingerprint,
    )
    _persist(tmp_path, first)

    second = _stage(tmp_path, [_job("A_Main", "Flows")], run_id=2)

    assert second.entries_flagged == 0
    staged = (second.staged_dir / "key-notes.md").read_text(encoding="utf-8")
    assert REVIEW_PREFIX not in staged


def test_human_entry_is_annotated_with_its_authority_restated(tmp_path: Path) -> None:
    text = (
        "# Lessons Learned\n\n"
        f"{ENTRIES_MARKER}\n"
        "### 2026-01-01T00:00:00Z | author: human | scan: aaaaaaaaaaaa\n"
        "VISE2AssetCheck and Vise2AssetCheck are two different groups.\n"
    )

    annotated, flagged = annotate_for_review(text, "bbbbbbbbbbbbbbbb")

    assert flagged == 1
    assert NOTE_PREFIX in annotated
    assert REVIEW_PREFIX not in annotated
    assert "human correction outranks an agent conclusion" in annotated
    assert "VISE2AssetCheck and Vise2AssetCheck are two different groups." in annotated


def test_an_entry_with_no_fingerprint_is_flagged_as_uncheckable() -> None:
    text = (
        f"{ENTRIES_MARKER}\n"
        "### 2026-01-01T00:00:00Z | author: agent\n"
        "Something remembered.\n"
    )
    annotated, flagged = annotate_for_review(text, "cccccccccccc")

    assert flagged == 1
    assert "carries no scan fingerprint" in annotated


def test_annotation_is_idempotent_and_reversible() -> None:
    text = (
        f"{ENTRIES_MARKER}\n"
        "### 2026-01-01T00:00:00Z | author: agent | scan: aaaaaaaaaaaa\n"
        "Body.\n"
    )
    once, _ = annotate_for_review(text, "bbbbbbbbbbbb")
    twice, _ = annotate_for_review(once, "bbbbbbbbbbbb")

    assert once == twice
    from mcp_server.services.project_memory import strip_annotations

    assert strip_annotations(once) == text


# --------------------------------------------------------------------------- #
# Entries: append-only, concurrent, normalised
# --------------------------------------------------------------------------- #

def test_concurrent_appends_do_not_lose_an_entry(tmp_path: Path) -> None:
    """Several sub-agents append to open-questions.md during one run."""
    durable = tmp_path / DURABLE_DIRNAME
    seed_memory(durable, fingerprint="f" * 64)
    path = durable / "open-questions.md"

    writers = 24
    barrier = threading.Barrier(writers)

    def _write(index: int) -> None:
        barrier.wait()
        append_entry(
            path,
            f"Question number {index:03d} about the estate.",
            scan_fingerprint="f" * 64,
            title=f"q{index:03d}",
            timestamp="2026-01-01T00:00:00Z",
        )

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    text = path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    assert len(entries) == writers
    for index in range(writers):
        assert f"Question number {index:03d}" in text


def test_persist_fills_in_a_partial_header_and_keeps_an_existing_one(
    tmp_path: Path,
) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    path = first.staged_dir / "decisions.md"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            "### 2025-05-05T05:05:05Z | author: human | scan: 0123456789ab\n"
            "Treat REFERENCES as out of scope.\n\n"
            "### 2026-02-02T02:02:02Z\n"
            "Classified the ESB routes as mediation, not data movement.\n"
        )

    persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=True,
    )

    entries = parse_entries(
        (first.durable_dir / "decisions.md").read_text(encoding="utf-8")
    )
    assert len(entries) == 2
    assert entries[0].author == AUTHOR_HUMAN
    assert entries[0].scan_fingerprint == "0123456789ab"   # untouched
    assert entries[1].author == "agent"                    # defaulted
    assert entries[1].scan_fingerprint == first.fingerprint[:12]


def test_normalize_entries_is_idempotent() -> None:
    text = f"{ENTRIES_MARKER}\n### 2026-01-01T00:00:00Z\nBody.\n"
    once = normalize_entries(text, fingerprint="a" * 64, timestamp="2026-01-01T00:00:00Z")
    twice = normalize_entries(once, fingerprint="b" * 64, timestamp="2026-01-02T00:00:00Z")
    assert once == twice


def test_an_agent_invented_memory_file_persists(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    (first.staged_dir / "supplier-quirks.md").write_text(
        f"# Supplier quirks\n\n{ENTRIES_MARKER}\n"
        "### 2026-01-01T00:00:00Z | author: agent\n"
        "Supplier 4PS sends empty VAT codes on credit notes.\n",
        encoding="utf-8",
    )

    result = persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=True,
    )

    assert "supplier-quirks.md" in result.persisted_files
    assert (first.durable_dir / "supplier-quirks.md").is_file()


def test_the_run_local_continuity_note_is_not_copied_into_the_durable_home(
    tmp_path: Path,
) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    assert (first.staged_dir / SINCE_LAST_RUN_FILENAME).is_file()

    persist_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        snapshot=first.snapshot,
        success=True,
    )

    assert not (first.durable_dir / SINCE_LAST_RUN_FILENAME).exists()
    assert (first.durable_dir / SNAPSHOT_FILENAME).is_file()


# --------------------------------------------------------------------------- #
# Scan facts, fingerprints, partitions
# --------------------------------------------------------------------------- #

def test_unit_facts_use_the_canonical_id_and_the_global_group_fallback(
    tmp_path: Path,
) -> None:
    scan_root = tmp_path / "scan_cache"
    _write_scan(
        scan_root,
        [
            {"job_id": "canon-1", "name": "DisplayName", "group": "Flows", "components": []},
            {"job_id": "canon-2", "name": "Other", "group": "", "components": []},
        ],
    )
    # The group directory name is irrelevant; the group INSIDE the JSON wins.
    facts = compute_unit_facts(scan_root)

    assert set(facts) == {"canon-1", "canon-2"}
    assert facts["canon-1"].group == "Flows"
    assert facts["canon-2"].group == "Global"


def test_unit_facts_fall_back_to_the_monolithic_scan_result(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan_cache"
    scan_root.mkdir(parents=True)
    (scan_root / "scan_result.json").write_text(
        json.dumps({"jobs": [_job("A_Main", "Flows")]}), encoding="utf-8"
    )

    facts = compute_unit_facts(scan_root)
    assert list(facts) == ["A_Main"]
    # No shared artifacts at all: reported as unknown, never as "no change".
    assert compute_shared_artifact_fingerprint(scan_root) == ""


def test_a_partition_only_remembers_its_own_units(tmp_path: Path) -> None:
    jobs = [_job("Mine", "Flows"), _job("Sibling", "Flows")]
    result = _stage(
        tmp_path,
        jobs,
        unit_allowlist={"Mine"},
        partition_name="sub_a",
    )

    assert result.unit_names == ("Mine",)
    note = (result.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")
    assert "Scope: sub_a" in note
    assert "Sibling" not in note


def test_fingerprint_is_scoped_by_partition_and_platform(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan_cache"
    _write_scan(scan_root, [_job("A_Main", "Flows")])
    units = compute_unit_facts(scan_root)

    base = compute_memory_fingerprint(
        units, platform="talend", partition_name=None, shared_fingerprint="x"
    )
    assert base == compute_memory_fingerprint(
        units, platform="talend", partition_name=None, shared_fingerprint="x"
    )
    assert base != compute_memory_fingerprint(
        units, platform="generic", partition_name=None, shared_fingerprint="x"
    )
    assert base != compute_memory_fingerprint(
        units, platform="talend", partition_name="sub_a", shared_fingerprint="x"
    )
    assert base != compute_memory_fingerprint(
        units, platform="talend", partition_name=None, shared_fingerprint="y"
    )


def test_a_corrupt_snapshot_is_treated_as_absent_not_trusted(tmp_path: Path) -> None:
    durable = tmp_path / DURABLE_DIRNAME
    durable.mkdir(parents=True)
    (durable / SNAPSHOT_FILENAME).write_text("{not json", encoding="utf-8")

    assert load_snapshot(durable) is None


def test_diff_against_a_missing_snapshot_is_a_first_run(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan_cache"
    _write_scan(scan_root, [_job("A_Main", "Flows")])
    snapshot = build_snapshot(
        compute_unit_facts(scan_root),
        project_id="p",
        platform="talend",
        partition_name=None,
        shared_fingerprint="x",
        run_id=None,
    )

    delta = diff_snapshots(None, snapshot)
    assert delta.first_run is True
    assert delta.unit_count_after == 1
    assert delta.units_added == []


# --------------------------------------------------------------------------- #
# ASCII discipline
# --------------------------------------------------------------------------- #

def test_a_non_ascii_design_name_is_escaped_not_emitted(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    _persist(tmp_path, first)

    second = _stage(tmp_path, [_job("A_Main", "Flows"), _job("Fakturac\u00e4o", "Flows")], run_id=2)
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert note.isascii()
    assert "Fakturac" in note
    assert "\\xe4" in note


def test_every_staged_file_is_ascii(tmp_path: Path) -> None:
    result = _stage(tmp_path, [_job("A_Main", "Flows")])
    for path in sorted(result.staged_dir.iterdir()):
        text = path.read_text(encoding="utf-8")
        assert text.isascii(), path.name
        assert "\u2014" not in text   # em dash
        assert "\u2500" not in text   # box drawing


def test_ascii_only_escapes_rather_than_dropping() -> None:
    assert ascii_only("caf\u00e9") == "caf\\xe9"
    assert ascii_only("plain") == "plain"


# --------------------------------------------------------------------------- #
# Real scan data (ETTM)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not ETTM_SCAN.is_dir(), reason="ETTM scan cache not present")
def test_real_ettm_scan_produces_a_usable_first_run_note(tmp_path: Path) -> None:
    result = stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN,
        platform="talend",
        run_id=1,
    )

    note = (result.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")
    assert "FIRST RUN" in note
    assert note.isascii()
    assert len(result.unit_names) > 100
    assert len(result.groups) > 5
    assert f"{len(result.unit_names)} designs across {len(result.groups)} groups" in note
    # The fingerprint is stable across two reads of the same scan cache.
    again = stage_project_memory(
        output_dir=tmp_path / "output2",
        staging_dir=tmp_path / "output2" / "docs_staging",
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN,
        platform="talend",
        run_id=2,
    )
    assert again.fingerprint == result.fingerprint


@pytest.mark.skipif(not ETTM_SCAN.is_dir(), reason="ETTM scan cache not present")
def test_real_ettm_scan_second_run_reports_a_real_delta(tmp_path: Path) -> None:
    first = stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN,
        platform="talend",
        run_id=1,
    )
    _persist(tmp_path, first)

    # Drop one real unit from the allowlist and pretend another group vanished.
    kept = set(first.unit_names)
    removed = sorted(kept)[0]
    kept.discard(removed)

    second = stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN,
        platform="talend",
        unit_allowlist=kept,
        run_id=2,
    )
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert second.delta.units_removed == [removed]
    assert "SCAN CHANGED" in note
    assert removed in note
    assert note.isascii()


@pytest.mark.skipif(not ETTM_SCAN.is_dir(), reason="ETTM scan cache not present")
def test_real_ettm_note_carries_no_scan_values(tmp_path: Path) -> None:
    """The note is derived from real scan data; it must stay value-free."""
    result = stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN,
        platform="talend",
        run_id=1,
    )
    note = (result.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    for smell in ("password", "jdbc:", "Passwd", "pwd=", "http://", "https://"):
        assert smell.lower() not in note.lower(), smell


def test_staged_dir_is_under_docs_staging_and_durable_is_not(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    staging_dir = output_dir / "docs_staging"

    assert durable_memory_dir(output_dir) == output_dir / DURABLE_DIRNAME
    assert staged_memory_dir(staging_dir) == staging_dir / STAGED_DIRNAME
    # The record is a SIBLING of docs/, not inside the published tree.
    assert durable_memory_dir(output_dir).parent == output_dir
    assert "docs" not in durable_memory_dir(output_dir).parts[-2:]


# --------------------------------------------------------------------------- #
# Merge semantics: the record is only ever added to
# --------------------------------------------------------------------------- #

def test_a_human_correction_written_during_a_run_is_not_lost(tmp_path: Path) -> None:
    """The defect this merge replaced.

    A human can write a correction straight into the durable record at any
    time, including while a run is in flight. Copying the staged mirror back
    over the record discarded it; merging keeps both.
    """
    first = _stage(tmp_path, [_job("A_Main", "Flows")])

    # The agent appends to its staged working copy ...
    append_entry(
        first.staged_dir / "lessons-learned.md",
        "A_Main appears to write directly to the asset table.",
        scan_fingerprint=first.fingerprint,
        timestamp="2026-01-01T00:00:00Z",
    )
    # ... while a human corrects the record itself.
    append_entry(
        first.durable_dir / "lessons-learned.md",
        "It does not. A_Main writes to a staging table; the merge is downstream.",
        author=AUTHOR_HUMAN,
        scan_fingerprint=first.fingerprint,
        timestamp="2026-01-01T00:00:01Z",
    )

    _persist(tmp_path, first)

    text = (first.durable_dir / "lessons-learned.md").read_text(encoding="utf-8")
    entries = parse_entries(text)

    assert len(entries) == 2
    assert [e.author for e in entries] == [AUTHOR_HUMAN, "agent"]
    assert "writes to a staging table" in text
    assert "appears to write directly" in text


def test_merge_does_not_propagate_a_deletion(tmp_path: Path) -> None:
    """These files are append-only, so a missing entry means "not seen"."""
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        first.durable_dir / "decisions.md",
        "REFERENCES is out of scope.",
        author=AUTHOR_HUMAN,
        scan_fingerprint=first.fingerprint,
        timestamp="2026-01-01T00:00:00Z",
    )
    # The agent hands back a staged copy that never had the entry.
    (first.staged_dir / "decisions.md").write_text(
        f"# Decisions\n\n{ENTRIES_MARKER}\n", encoding="utf-8"
    )

    _persist(tmp_path, first)

    text = (first.durable_dir / "decisions.md").read_text(encoding="utf-8")
    assert "REFERENCES is out of scope." in text


def test_merge_is_idempotent(tmp_path: Path) -> None:
    from mcp_server.services.project_memory import merge_memory_text

    durable = (
        f"# Key Notes\n\n{ENTRIES_MARKER}\n"
        "### 2026-01-01T00:00:00Z | author: human | scan: aaaaaaaaaaaa\n"
        "Known fact.\n\n"
    )
    staged = durable + (
        "### 2026-01-02T00:00:00Z | author: agent | scan: aaaaaaaaaaaa\n"
        "New fact.\n\n"
    )

    once = merge_memory_text(durable, staged, fingerprint="a" * 64)
    twice = merge_memory_text(once, staged, fingerprint="a" * 64)

    assert once == twice
    assert len(parse_entries(once)) == 2


def test_merge_keeps_the_records_preamble(tmp_path: Path) -> None:
    from mcp_server.services.project_memory import merge_memory_text

    durable = f"# Key Notes\n\nOFFICIAL INSTRUCTIONS\n\n{ENTRIES_MARKER}\n"
    staged = f"# Key Notes\n\nAGENT REWROTE THE RULES\n\n{ENTRIES_MARKER}\n"

    merged = merge_memory_text(durable, staged, fingerprint="a" * 64)

    assert "OFFICIAL INSTRUCTIONS" in merged
    assert "AGENT REWROTE THE RULES" not in merged


def test_unstructured_staged_text_is_recovered_not_dropped(tmp_path: Path) -> None:
    from mcp_server.services.project_memory import merge_memory_text

    durable = f"# Open Questions\n\n{ENTRIES_MARKER}\n"
    staged = durable + "Does 4PS own the supplier master?\n"

    merged = merge_memory_text(durable, staged, fingerprint="a" * 64)

    assert "Does 4PS own the supplier master?" in merged
    entries = parse_entries(merged)
    assert len(entries) == 1
    assert entries[0].title == "unstructured note recovered from the run"


def test_snapshot_round_trips_through_disk(tmp_path: Path) -> None:
    first = _stage(tmp_path, [_job("A_Main", "Flows")])
    save_snapshot(first.durable_dir, first.snapshot)

    loaded = load_snapshot(first.durable_dir)

    assert loaded is not None
    assert loaded.fingerprint == first.snapshot.fingerprint
    assert loaded.units == first.snapshot.units
    assert loaded.groups == ["Flows"]


# --------------------------------------------------------------------------- #
# The wiring in claude_service
# --------------------------------------------------------------------------- #

def test_finalizer_persists_memory_and_reports_leakage_advisorily(tmp_path: Path) -> None:
    """The post-run step the coordinator calls: memory back, guard reports out.

    Advisory by design, matching the four Phase 3 final-check gates: findings
    become log lines and a report under ``_assessment/``, never an exception.
    """
    import json as _json

    from mcp_server.services.claude_service import DocGenerationCoordinator

    output_dir = tmp_path / "output"
    staging_dir = output_dir / "docs_staging"
    stage = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        stage.staged_dir / "open-questions.md",
        "Does 4PS own the supplier master?",
        scan_fingerprint=stage.fingerprint,
    )
    # A document the run "published", leaking a staging path.
    (staging_dir / "00_AGENT_BRIEF.md").write_text(
        "# Brief\n\n| Fact | Source |\n| x | _analysis/_doc_pack_00_AGENT_BRIEF.md |\n",
        encoding="utf-8",
    )

    messages = DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=output_dir,
        staging_dir=staging_dir,
        memory_stage=stage,
        groups=["Flows"],
        success=True,
    )
    text = "\n".join(message for message, _ in messages)

    # Memory persisted and staging cleared.
    assert not staged_memory_dir(staging_dir).exists()
    durable = (durable_memory_dir(output_dir) / "open-questions.md").read_text(
        encoding="utf-8"
    )
    assert "supplier master" in durable
    assert load_snapshot(durable_memory_dir(output_dir)) is not None

    # Leakage reported, not raised.
    assert "Project memory persisted" in text
    assert "Client-language guard" in text
    assert "_analysis" in text
    report = _json.loads(
        (staging_dir / "_assessment" / "client_language.json").read_text(encoding="utf-8")
    )
    assert report["ok"] is False
    assert report["advisory"] is True
    assert (staging_dir / "_assessment" / "provenance_validation.json").is_file()


def test_finalizer_on_a_failed_run_keeps_memory_and_skips_the_guard(
    tmp_path: Path,
) -> None:
    from mcp_server.services.claude_service import DocGenerationCoordinator

    output_dir = tmp_path / "output"
    staging_dir = output_dir / "docs_staging"
    stage = _stage(tmp_path, [_job("A_Main", "Flows")])
    append_entry(
        stage.staged_dir / "key-notes.md",
        "Worked out before the run died.",
        scan_fingerprint=stage.fingerprint,
    )

    DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=output_dir,
        staging_dir=staging_dir,
        memory_stage=stage,
        groups=["Flows"],
        success=False,
    )

    durable = durable_memory_dir(output_dir)
    assert "Worked out before the run died." in (durable / "key-notes.md").read_text(
        encoding="utf-8"
    )
    # The baseline did not move, so the next run still reports the same delta.
    assert load_snapshot(durable) is None
    # No published document set to judge.
    assert not (staging_dir / "_assessment" / "client_language.json").exists()


def test_finalizer_survives_a_missing_memory_stage(tmp_path: Path) -> None:
    """Staging memory is advisory: a run whose memory failed must still finish."""
    from mcp_server.services.claude_service import DocGenerationCoordinator

    staging_dir = tmp_path / "output" / "docs_staging"
    staging_dir.mkdir(parents=True)
    (staging_dir / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")

    messages = DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=tmp_path / "output",
        staging_dir=staging_dir,
        memory_stage=None,
        groups=[],
        success=True,
    )

    assert messages
    assert (staging_dir / "_assessment" / "client_language.json").is_file()


def test_an_unreadable_scan_is_not_reported_as_a_deleted_estate(tmp_path: Path) -> None:
    """A vanished scan cache must not become 142 fabricated deletions."""
    first = _stage(tmp_path, [_job(f"J{i}", "Flows") for i in range(5)])
    _persist(tmp_path, first)

    import shutil

    shutil.rmtree(tmp_path / "scan_cache" / "scan" / "jobs", ignore_errors=True)
    second = stage_project_memory(
        output_dir=tmp_path / "output",
        staging_dir=tmp_path / "output" / "docs_staging",
        project_id="proj",
        scan_output_dir=tmp_path / "scan_cache",
        platform="talend",
        run_id=2,
    )
    note = (second.staged_dir / SINCE_LAST_RUN_FILENAME).read_text(encoding="utf-8")

    assert "SCAN UNREADABLE OR EMPTY" in note
    assert "Do NOT report any design as removed" in note
    assert "SCAN CHANGED" not in note
    # The removal list is suppressed entirely, not merely caveated.
    assert "## Designs removed" not in note
    assert "J0" not in note
