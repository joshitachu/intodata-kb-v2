"""Resume detection must report the REMAINDER, not a list of completions.

Every assertion here is ETTM run 260, which completed and reported success
having never analysed a quarter of the estate.  ``_detect_resume_state`` listed
``f.stem for f in _analysis.glob("*.md")`` -- which counts each
``{group}_summary.md`` navigation aid as an analysis -- and compared that list
to nothing.  Reproduced:

======================== =====
manifest groups             20
groups actually analysed    15
``analysis_files`` reports  30
missing groups named      none
======================== =====

The prompt then told the model "COMPLETED ANALYSIS (30 groups) -> Skip Phase 2
entirely for these groups" and never stated what remained, so the model
correctly concluded Phase 2 was finished.  ``Monitoring``, ``afas``,
``fourpsForSuppliers``, ``fourpsMaximo`` and ``utils`` are absent from the
published documents.

A list of what exists cannot express absence.  Only a comparison against the
manifest can, so these tests fix the comparison, not the listing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.custom.analysis_store import write_section, write_summary
from mcp_server.services.claude_service import (
    MIN_GROUP_ANALYSIS_BYTES,
    DocGenerationCoordinator,
)

_detect = DocGenerationCoordinator._detect_resume_state


#: The 20 groups of the ETTM estate as ``_manifest.json`` recorded them, and the
#: five that run 260 published without. ``monitoring`` carries a ``_part_ci1``
#: suffix because ``Monitoring`` already owns that path on a case-insensitive
#: filesystem -- the real ETTM staging tree holds
#: ``_analysis/monitoring_part_ci1.md`` for exactly that reason.
_RUN_260_GROUPS = [
    "Global", "Misc", "MessageLogging", "Monitoring", "TestTools", "VRI",
    "afas", "afasFourps", "alertErrorsEmail", "forSuppliers",
    "forSuppliersFourps", "fourps", "fourpsForSuppliers", "fourpsMaximo",
    "manualFixJobs", "maximo", "maximoFourps", "mockApiService",
    "monitoring_part_ci1", "utils",
]
_RUN_260_MISSING = [
    "Monitoring", "afas", "fourpsForSuppliers", "fourpsMaximo", "utils",
]

_ANALYSIS_BODY = "# analysis\n" + ("x" * MIN_GROUP_ANALYSIS_BYTES)
_SUMMARY_BODY = "# summary\n" + ("y" * MIN_GROUP_ANALYSIS_BYTES)

#: A group's analysis in the DIRECTORY layout (plan slice 2): one file per
#: section, written through the same store the writer and every consumer use.
#: Deliberately more than one section and comfortably over the 1KB floor --
#: the thin and empty cases are their own tests below.
_SECTION_BODIES = {
    "COMPONENTS": "tMSSqlInput_1 reads dbo.Orders.\n" + ("c" * 600),
    "SOURCE_SQL": "SELECT OrderId FROM dbo.Orders\n" + ("s" * 600),
    "RISK_FLAGS": "HIGH: no tLogCatcher on 7 of 12 designs.\n" + ("r" * 600),
}


def _write_directory_analysis(
    analysis_dir: Path, group: str, sections: dict[str, str] | None = None,
) -> None:
    """Write one group in the per-section layout, plus its ``_summary.md``."""
    for name, body in (sections if sections is not None else _SECTION_BODIES).items():
        write_section(analysis_dir, group, name, body)
    write_summary(analysis_dir, group, _SUMMARY_BODY)


def _staging(
    tmp_path: Path,
    *,
    manifest_groups: list[str] | None,
    analysed: list[str],
    summaries: list[str] | None = None,
    preprocess: list[str] | None = None,
    empty_analyses: list[str] = (),
    directory_analysed: list[str] = (),
) -> Path:
    """Build a staging tree with the shape a real run leaves behind.

    ``analysed`` are groups in the legacy FLAT layout (``{group}.md``),
    ``directory_analysed`` groups in the per-section DIRECTORY layout. A real
    estate can carry both at once -- a resumed project analysed before the
    layout changed keeps its flat files while this run writes directories --
    so the helper takes both and the tests below exercise the mixture.
    """
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    preprocess_dir = staging / "_preprocess"
    analysis.mkdir(parents=True)
    preprocess_dir.mkdir(parents=True)

    for name in (preprocess if preprocess is not None else manifest_groups or []):
        (preprocess_dir / f"{name}.md").write_text("evidence\n", encoding="utf-8")
    for name in analysed:
        (analysis / f"{name}.md").write_text(_ANALYSIS_BODY, encoding="utf-8")
    for name in empty_analyses:
        (analysis / f"{name}.md").write_text("", encoding="utf-8")
    for name in directory_analysed:
        _write_directory_analysis(analysis, name)
    # The summaries every Phase 2 worker writes alongside its analysis.
    for name in (summaries if summaries is not None else analysed):
        (analysis / f"{name}_summary.md").write_text(_SUMMARY_BODY, encoding="utf-8")

    if manifest_groups is not None:
        (staging / "_manifest.json").write_text(
            json.dumps({
                "project_id": "ettm_fix",
                "platform": "talend",
                "groups": manifest_groups,
                "total_jobs": 142,
            }),
            encoding="utf-8",
        )
    return staging


# --------------------------------------------------------------------------
# 1. The run-260 regression, replayed exactly
# --------------------------------------------------------------------------


@pytest.fixture()
def run_260(tmp_path: Path) -> dict:
    analysed = [g for g in _RUN_260_GROUPS if g not in _RUN_260_MISSING]
    assert len(_RUN_260_GROUPS) == 20 and len(analysed) == 15
    staging = _staging(tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=analysed)
    # The staging tree on disk is exactly what the old detector saw: 30 .md
    # files in _analysis/ for 15 real analyses.
    assert len(list((staging / "_analysis").glob("*.md"))) == 30
    state = _detect(staging)
    assert state is not None
    return state


def test_the_five_missing_groups_are_named_for_analysis(run_260: dict) -> None:
    """(a) The remainder is stated, by name. This is the whole defect."""
    assert run_260["pending_groups"] == _RUN_260_MISSING


def test_a_summary_file_is_not_an_analysis(run_260: dict) -> None:
    """(b) 30 files, 15 analyses. Counting the summaries is what made 15 look
    like more than the 20 the manifest asked for."""
    assert len(run_260["analysis_files"]) == 15
    assert not any(name.endswith("_summary") for name in run_260["analysis_files"])


def test_the_estate_is_not_reported_complete(run_260: dict) -> None:
    assert run_260["analysis_complete"] is False
    assert run_260["expected_groups"] == sorted(_RUN_260_GROUPS)
    assert run_260["expected_groups_source"] == "manifest"


def test_a_doc_written_before_the_pending_analyses_is_reported_as_such(
    tmp_path: Path,
) -> None:
    """A document written before the pending analyses landed describes an
    estate that is missing them, so it is reported alongside an incomplete
    Phase 2 rather than as finished work."""
    analysed = [g for g in _RUN_260_GROUPS if g not in _RUN_260_MISSING]
    staging = _staging(tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=analysed)
    (staging / "00_AGENT_BRIEF.md").write_text("d" * 2000, encoding="utf-8")

    state = _detect(staging)
    assert state["analysis_complete"] is False
    assert state["completed_docs"] == ["00_AGENT_BRIEF.md"]


# --------------------------------------------------------------------------
# 2. A complete estate must still resume as complete
# --------------------------------------------------------------------------


def test_a_fully_analysed_estate_reports_phase_2_complete(tmp_path: Path) -> None:
    """(c) The fix must not turn every resume into a full re-run."""
    staging = _staging(
        tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=_RUN_260_GROUPS,
    )
    state = _detect(staging)
    assert state["pending_groups"] == []
    assert state["analysis_complete"] is True
    assert len(state["analysis_files"]) == 20


def test_an_underscore_artifact_in_the_analysis_dir_is_not_a_group(
    tmp_path: Path,
) -> None:
    """Resume counts GROUPS. A run-time artifact dropped into ``_analysis/``
    (the shared-library digest, anything a future phase writes) is
    ``_``-prefixed and must never be mistaken for an analysed group -- that
    over-count is what made 15 analyses read as 30."""
    staging = _staging(
        tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=_RUN_260_GROUPS,
    )
    (staging / "_analysis" / "_shared_library.md").write_text(
        "b" * 2000, encoding="utf-8",
    )
    state = _detect(staging)
    assert len(state["analysis_files"]) == 20
    assert state["analysis_complete"] is True


# --------------------------------------------------------------------------
# 3. A split group owes ONE analysis, not one per slice
# --------------------------------------------------------------------------


def test_a_split_group_owes_one_merged_analysis(tmp_path: Path) -> None:
    """``_preprocess/`` writes a >40-design group as two slices and the prompt
    requires them MERGED into one ``_analysis/{group}.md``. Counting slices
    would demand a file that is never meant to exist, and would re-run Phase 2
    for a group that is finished."""
    staging = _staging(
        tmp_path,
        manifest_groups=["afas", "maximoFourps_part1", "maximoFourps_part2"],
        analysed=["afas", "maximoFourps"],
    )
    state = _detect(staging)
    assert state["expected_groups"] == ["afas", "maximoFourps"]
    assert state["pending_groups"] == []
    assert state["analysis_complete"] is True


def test_a_case_collision_slice_keeps_its_own_analysis(tmp_path: Path) -> None:
    """``monitoring`` and ``Monitoring`` are both real ETTM groups; the
    ``_part_ci{k}`` suffix exists because they cannot share a path, so stripping
    it would re-create the collision it was minted to avoid."""
    staging = _staging(
        tmp_path,
        manifest_groups=["Monitoring", "monitoring_part_ci1"],
        analysed=["Monitoring"],
    )
    state = _detect(staging)
    assert state["expected_groups"] == ["Monitoring", "monitoring_part_ci1"]
    assert state["pending_groups"] == ["monitoring_part_ci1"]


# --------------------------------------------------------------------------
# 4. Fail toward more work: an unusable analysis is pending work
# --------------------------------------------------------------------------


def test_a_zero_byte_analysis_counts_as_missing(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        manifest_groups=["afas", "fourps", "utils"],
        analysed=["afas", "fourps"],
        empty_analyses=["utils"],
        summaries=["afas", "fourps", "utils"],
    )
    state = _detect(staging)
    assert state["pending_groups"] == ["utils"]
    assert "utils" not in state["analysis_files"]


def test_a_truncated_analysis_counts_as_missing(tmp_path: Path) -> None:
    """The threshold is the Phase 2.5 gate's own rule ("MISSING or under 1KB"),
    so the server's resume detection and the model's gate agree on what a
    finished group looks like."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "fourps"], analysed=["afas", "fourps"],
    )
    (staging / "_analysis" / "fourps.md").write_text(
        "x" * (MIN_GROUP_ANALYSIS_BYTES - 1), encoding="utf-8",
    )
    state = _detect(staging)
    assert state["pending_groups"] == ["fourps"]


# --------------------------------------------------------------------------
# 4b. The same questions, asked of the per-section DIRECTORY layout
# --------------------------------------------------------------------------
#
# Phase 2 now writes ``_analysis/{group}/{SECTION}.md`` (plan slice 2). Resume
# detection must treat a group as analysed under EITHER layout and must keep
# its bias intact: anything it cannot read as an analysis is pending work, not
# a completion. Every assertion in section 1-4 above is repeated here against
# the new shape, because a rule that holds for one layout and not the other is
# a migration bug with a date on it.


def test_a_directory_layout_group_is_detected_as_analysed(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        manifest_groups=["afas", "fourps"],
        analysed=[],
        summaries=[],
        directory_analysed=["afas", "fourps"],
    )
    state = _detect(staging)
    assert state["analysis_files"] == ["afas", "fourps"]
    assert state["pending_groups"] == []
    assert state["analysis_complete"] is True


def test_an_empty_group_directory_is_not_an_analysis(tmp_path: Path) -> None:
    """The directory is the claim; the section files are the evidence for it.
    An empty one is a group somebody started and did not analyse."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "utils"], analysed=[], summaries=[],
        directory_analysed=["afas"],
    )
    (staging / "_analysis" / "utils").mkdir()
    state = _detect(staging)
    assert state["pending_groups"] == ["utils"]
    assert state["analysis_files"] == ["afas"]


def test_a_group_directory_holding_only_a_summary_is_not_an_analysis(
    tmp_path: Path,
) -> None:
    """The run-260 defect in the new layout: a navigation aid counted as work.
    ``_summary.md`` is behind an underscore precisely so it cannot be one."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "utils"], analysed=[], summaries=[],
        directory_analysed=["afas"],
    )
    write_summary(staging / "_analysis", "utils", _SUMMARY_BODY)
    state = _detect(staging)
    assert state["pending_groups"] == ["utils"]


def test_a_group_directory_of_unnamed_files_is_not_an_analysis(
    tmp_path: Path, caplog,
) -> None:
    """A directory whose files resolve to no section on the contract is LOST
    evidence, not an empty analysis. It fails toward more work AND says so --
    the run-258 failure was silent, which is why nobody noticed it for a run."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "utils"], analysed=[], summaries=[],
        directory_analysed=["afas"],
    )
    stray = staging / "_analysis" / "utils"
    stray.mkdir()
    (stray / "notes.md").write_text("x" * 4000, encoding="utf-8")

    with caplog.at_level("WARNING"):
        state = _detect(staging)
    assert state["pending_groups"] == ["utils"]
    assert "no known section" in caplog.text
    assert "utils" in caplog.text


def test_a_group_directory_the_store_refuses_to_open_does_not_abort_the_run(
    tmp_path: Path, caplog,
) -> None:
    """Resume detection now OPENS each analysis instead of stat()ing a path, so
    a name the store rejects would raise out of detection and abort the whole
    generation. Verified reachable: a directory whose name carries a trailing
    space is a legal directory and an illegal group name."""
    staging = _staging(
        tmp_path, manifest_groups=["afas"], analysed=[], summaries=[],
        directory_analysed=["afas"],
    )
    stray = staging / "_analysis" / "weird "
    stray.mkdir()
    (stray / "COMPONENTS.md").write_text("x" * 3000, encoding="utf-8")

    with caplog.at_level("WARNING"):
        state = _detect(staging)
    assert state["analysis_files"] == ["afas"]
    assert state["analysis_complete"] is True
    assert "unusable analysis name" in caplog.text


def test_a_thin_directory_analysis_counts_as_missing(tmp_path: Path) -> None:
    """The 1KB floor is the Phase 2.5 gate's own rule and it applies to the
    group's section files TOGETHER, so the two layouts answer one question."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "fourps"], analysed=[], summaries=[],
        directory_analysed=["afas"],
    )
    _write_directory_analysis(
        staging / "_analysis", "fourps", {"COMPONENTS": "tMap_1.", "SOURCE_SQL": "none"},
    )
    state = _detect(staging)
    assert state["pending_groups"] == ["fourps"]


def test_the_run_260_remainder_is_identical_under_the_directory_layout(
    tmp_path: Path,
) -> None:
    """The regression this whole plan started from, replayed in the new shape:
    20 manifest groups, 15 analysed, and the five that were never analysed must
    be named. Same estate, same arithmetic, different bytes on disk."""
    analysed = [g for g in _RUN_260_GROUPS if g not in _RUN_260_MISSING]
    staging = _staging(
        tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=[], summaries=[],
        directory_analysed=analysed,
    )
    state = _detect(staging)
    assert len(state["analysis_files"]) == 15
    assert state["pending_groups"] == _RUN_260_MISSING
    assert state["analysis_complete"] is False


def test_a_mixed_estate_counts_both_layouts_once_each(tmp_path: Path) -> None:
    """A project mid-migration: groups analysed before the change are flat,
    groups analysed by this run are directories, and the rest are owed."""
    staging = _staging(
        tmp_path,
        manifest_groups=["afas", "fourps", "maximo", "utils"],
        analysed=["afas", "fourps"],
        directory_analysed=["maximo"],
    )
    state = _detect(staging)
    assert state["analysis_files"] == ["afas", "fourps", "maximo"]
    assert state["pending_groups"] == ["utils"]


def test_a_migrated_group_is_counted_once_not_twice(tmp_path: Path) -> None:
    """Slice 5 removes the flat file only when nothing needs it, so a group can
    hold both shapes at once. The store prefers the directory; the checklist
    must still see ONE group -- double-counting is how 15 analyses were
    reported as 30."""
    staging = _staging(
        tmp_path, manifest_groups=["afas"], analysed=["afas"],
        directory_analysed=["afas"],
    )
    state = _detect(staging)
    assert state["analysis_files"] == ["afas"]
    assert state["pending_groups"] == []


def test_a_split_group_owes_one_directory_not_one_per_part(tmp_path: Path) -> None:
    """``_manifest.json`` holds SLICE names; ``X_part1``/``X_part2`` merge into
    ONE analysis, so the directory layout owes ONE ``X/`` directory."""
    staging = _staging(
        tmp_path,
        manifest_groups=["afas", "maximoFourps_part1", "maximoFourps_part2"],
        analysed=[], summaries=[],
        directory_analysed=["afas", "maximoFourps"],
    )
    state = _detect(staging)
    assert state["expected_groups"] == ["afas", "maximoFourps"]
    assert state["pending_groups"] == []
    assert state["analysis_complete"] is True


def test_case_colliding_groups_get_their_own_directories(tmp_path: Path) -> None:
    """``Monitoring`` and ``monitoring`` are both real ETTM groups. A
    case-insensitive filesystem cannot hold both as directories any more than
    it could as files, which is exactly why preprocessing renames the second
    collider to ``monitoring_part_ci1`` before anything writes a path.

    Measured on this filesystem: ``_analysis/Monitoring/`` also answers to
    ``_analysis/monitoring``, and ``_analysis/afas.md`` also answers to
    ``_analysis/AFAS.md`` -- the two layouts are equally case-blind, so the
    directory is not a new collision surface. The defence is that the names
    reaching ``_analysis/`` are already unique case-insensitively, and that
    detection reports what it LISTED rather than asking the filesystem about a
    name it invented.
    """
    staging = _staging(
        tmp_path,
        manifest_groups=["Monitoring", "monitoring_part_ci1"],
        analysed=[], summaries=[],
        directory_analysed=["Monitoring"],
    )
    analysis = staging / "_analysis"
    assert (analysis / "Monitoring").is_dir()
    state = _detect(staging)
    assert state["analysis_files"] == ["Monitoring"]
    assert state["pending_groups"] == ["monitoring_part_ci1"]

    _write_directory_analysis(analysis, "monitoring_part_ci1")
    assert _detect(staging)["pending_groups"] == []
    # And the two analyses are genuinely separate on disk, not one clobbered
    # by the other -- the failure the _part_ci suffix exists to prevent.
    assert {p.name for p in analysis.iterdir() if p.is_dir()} == {
        "Monitoring", "monitoring_part_ci1",
    }


def test_the_post_run_coverage_line_reads_the_directory_layout(
    tmp_path: Path,
) -> None:
    """The same question asked after the run. It must not report a complete
    estate as empty just because the analyses are directories now."""
    analysed = [g for g in _RUN_260_GROUPS if g not in _RUN_260_MISSING]
    staging = _staging(
        tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=[], summaries=[],
        directory_analysed=analysed,
    )
    messages = DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=tmp_path / "out",
        staging_dir=staging,
        memory_stage=None,
        groups=_RUN_260_GROUPS,
        success=True,
    )
    coverage = [text for text, _level in messages if text.startswith("Group coverage:")]
    assert coverage and "15/20 groups analysed" in coverage[0]
    for group in _RUN_260_MISSING:
        assert group in coverage[0]


# --------------------------------------------------------------------------
# 5. No manifest: fall back to more work, never to "everything is done"
# --------------------------------------------------------------------------


def test_an_absent_manifest_falls_back_to_the_preprocess_groups(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        manifest_groups=None,
        preprocess=["afas", "fourps", "utils"],
        analysed=["fourps"],
    )
    state = _detect(staging)
    assert state["expected_groups_source"] == "preprocess"
    assert state["expected_groups"] == ["afas", "fourps", "utils"]
    assert state["pending_groups"] == ["afas", "utils"]


def test_an_unreadable_manifest_falls_back_rather_than_claiming_completion(
    tmp_path: Path,
) -> None:
    staging = _staging(
        tmp_path,
        manifest_groups=None,
        preprocess=["afas", "fourps"],
        analysed=["afas"],
    )
    (staging / "_manifest.json").write_text("{not json", encoding="utf-8")
    state = _detect(staging)
    assert state["expected_groups_source"] == "preprocess"
    assert state["pending_groups"] == ["fourps"]


def test_no_checklist_at_all_is_never_reported_complete(tmp_path: Path) -> None:
    """With neither a manifest nor ``_preprocess/`` there is nothing to be
    complete against. Claiming completion from an empty expectation is the
    run-260 failure with a different cause, so it fails toward MORE work."""
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis" / "afas.md").write_text(_ANALYSIS_BODY, encoding="utf-8")

    state = _detect(staging)
    assert state is not None
    assert state["expected_groups_source"] == "unknown"
    assert state["expected_groups"] == []
    assert state["analysis_complete"] is False


# --------------------------------------------------------------------------
# 6. The fresh-run contract is unchanged
# --------------------------------------------------------------------------


def test_an_absent_staging_directory_is_a_fresh_run(tmp_path: Path) -> None:
    assert _detect(tmp_path / "nothing_here") is None


def test_an_empty_staging_directory_is_a_fresh_run(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_preprocess").mkdir(parents=True)
    assert _detect(staging) is None


# --------------------------------------------------------------------------
# 7. The same question, asked again after the run
# --------------------------------------------------------------------------


def test_the_post_run_coverage_line_names_the_groups_that_shipped_without_one(
    tmp_path: Path,
) -> None:
    """Attribution asks "is this analysis about the group it is named for".
    Coverage asks "does this group have an analysis at all". Nothing asked the
    second question, so run 260 published without five groups and said success.
    Advisory by design: it warns, it never raises."""
    analysed = [g for g in _RUN_260_GROUPS if g not in _RUN_260_MISSING]
    staging = _staging(tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=analysed)

    messages = DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=tmp_path / "out",
        staging_dir=staging,
        memory_stage=None,
        groups=_RUN_260_GROUPS,
        success=True,
    )
    coverage = [text for text, _level in messages if text.startswith("Group coverage:")]
    assert coverage, messages
    assert "15/20 groups analysed" in coverage[0]
    for group in _RUN_260_MISSING:
        assert group in coverage[0]
    assert [level for text, level in messages if text.startswith("Group coverage:")] == [
        "warning"
    ]


def test_the_post_run_coverage_line_is_quiet_on_a_complete_run(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path, manifest_groups=_RUN_260_GROUPS, analysed=_RUN_260_GROUPS,
    )
    messages = DocGenerationCoordinator._finalize_memory_and_language(
        output_dir=tmp_path / "out",
        staging_dir=staging,
        memory_stage=None,
        groups=_RUN_260_GROUPS,
        success=True,
    )
    coverage = [
        (text, level) for text, level in messages if text.startswith("Group coverage:")
    ]
    assert coverage == [("Group coverage: 20/20 groups analysed.", "info")]


# --------------------------------------------------------------------------
# 8. The checklist is recomputed after preprocessing
# --------------------------------------------------------------------------


def test_the_checklist_is_refreshed_against_the_groups_this_run_will_analyse(
    tmp_path: Path,
) -> None:
    """Resume is detected BEFORE preprocessing, from the PREVIOUS run's
    manifest. A changed scan invalidates `_preprocess/` and produces a
    different group set, so the remainder must be recomputed against it --
    otherwise the prompt names groups that no longer exist and omits ones
    that do."""
    staging = _staging(
        tmp_path, manifest_groups=["afas", "retired"], analysed=["afas"],
    )
    state = _detect(staging)
    assert state["pending_groups"] == ["retired"]

    # The rescan dropped `retired` and added `newGroup`.
    DocGenerationCoordinator._apply_group_checklist(
        state, staging, ["afas", "newGroup"], "manifest",
    )
    assert state["expected_groups"] == ["afas", "newGroup"]
    assert state["pending_groups"] == ["newGroup"]
    assert state["analysis_complete"] is False
