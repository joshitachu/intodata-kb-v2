"""The run brief and the method it points at, shared by claude-cli and opencode-cli.

Every assertion here is a verified production failure of run 258, whose writers
were forbidden to open the evidence and so invented a SOAP namespace, a context
variable and a component type that do not exist -- while the real values sat in
``_analysis/``.  ``opencode_cli._build_prompt`` returns this prompt verbatim, so
each rule below covers both lanes.

WHAT SPLIT, AND WHY.  ``_build_prompt`` used to return a ~47 KB f-string that
carried the whole four-phase procedure: ~98 interpolated staging paths, a
step-by-step script, and every imperative the pipeline had accumulated.  The
procedure is now ``data/generation_method/METHOD.md``, staged to
``<staging>/_method/METHOD.md``; the prompt is a run brief carrying only what a
method file cannot know -- where this run's workspace is, how big this estate
is, which groups it owes an analysis, which switches are on, and what a previous
attempt already finished.

So each test below asks its question of whichever artifact now owns the answer,
and the split itself is asserted (a rule that is true of every run must not be
in the brief; a fact about THIS run must not be in the method).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider
from mcp_server.services import doc_quality_scorer
from mcp_server.services.doc_quality_scorer import collapse_floor_by_doc, min_lines_by_doc
from mcp_server.services.generation_method import (
    materialize_generation_method,
    method_source_path,
    staged_method_path,
)

from tests.test_providers.method_source import method, method_text, squash, states


def _ctx(tmp_path: Path, **over) -> DocGenContext:
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        project_id="p",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["afas", "fourps"],
        total_jobs=142,
        total_groups=20,
        effective_model="m",
        write_model="m",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=False,
        resume_state=None,
        scoped_docs=None,
    )
    kwargs.update(over)
    return DocGenContext(**kwargs)


def _prompt(tmp_path: Path, **over) -> str:
    return ClaudeCliProvider._build_prompt(_ctx(tmp_path, **over))


#: A resume that still owes work -- the run-260 shape, in miniature. Written as
#: ``_detect_resume_state`` now returns it: what exists AND what remains.
_RESUME_STATE = {
    "preprocess_exists": True,
    "expected_groups": ["afas", "fourps", "utils"],
    "expected_groups_source": "manifest",
    "analysis_files": ["afas"],
    "pending_groups": ["fourps", "utils"],
    "analysis_complete": False,
    "scorecard_exists": True,
    "completed_docs": ["00_AGENT_BRIEF"],
}

#: The same estate once Phase 2 really is finished. Resume must stay useful:
#: the fix for "skipped everything" must not become "re-runs everything".
_RESUME_STATE_COMPLETE = {
    **_RESUME_STATE,
    "analysis_files": ["afas", "fourps", "utils"],
    "pending_groups": [],
    "analysis_complete": True,
}

#: A staging tree with no manifest: the server cannot name the remainder, so the
#: brief must not let the model assume there is none.
_RESUME_STATE_NO_CHECKLIST = {
    "preprocess_exists": True,
    "expected_groups": [],
    "expected_groups_source": "unknown",
    "analysis_files": ["afas"],
    "pending_groups": [],
    "analysis_complete": False,
    "scorecard_exists": False,
    "completed_docs": [],
}

#: Every shape the brief can take. A rule that holds for one and not the others
#: is not a rule; run 258 shipped from the default (small, fresh) shape.
_SHAPES: dict[str, dict] = {
    "small_fresh": {},
    "large_fresh": {"total_jobs": 400},
    "small_resume": {"resume_state": _RESUME_STATE},
    "large_resume": {"total_jobs": 400, "resume_state": _RESUME_STATE},
    "resume_complete": {"resume_state": _RESUME_STATE_COMPLETE},
    "resume_no_checklist": {"resume_state": _RESUME_STATE_NO_CHECKLIST},
}


@pytest.fixture(params=sorted(_SHAPES), ids=sorted(_SHAPES))
def every_shape(request, tmp_path: Path) -> str:
    return _prompt(tmp_path, **_SHAPES[request.param])


# --------------------------------------------------------------------------
# 0. The brief points at a file that is really there
# --------------------------------------------------------------------------
#
# A pointer is only as good as the staging step behind it. The failure mode
# this guards is a run told to read a method that was never copied: the agent
# spends a tool call discovering the brief lied, then improvises the procedure.


def test_the_brief_names_the_method_first(every_shape: str) -> None:
    head = every_shape.split("THIS RUN:")[0]
    assert "THE METHOD IS A FILE. READ IT FIRST AND FOLLOW IT:" in head
    assert "/_method/METHOD.md" in head
    assert "Read it end to end before you" in head


def test_the_path_the_brief_names_is_where_staging_puts_the_method(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    staged = materialize_generation_method(staging)
    assert staged is not None and staged.is_file()
    assert staged == staged_method_path(staging)
    assert str(staged) in _prompt(tmp_path)
    assert staged.read_text(encoding="utf-8") == method_source_path().read_text(
        encoding="utf-8"
    )


def test_the_brief_points_at_the_shared_confidence_model_and_checklist(
    every_shape: str,
) -> None:
    """The vocabulary and the self-check are staged by
    ``investigation_method.materialize_shared_method`` into
    ``_method/_shared/``. The brief names them because a method that grades
    findings in a vocabulary the reader never opens grades them in no
    vocabulary at all."""
    assert "/_method/_shared/confidence-model.md" in every_shape
    assert "/_method/_shared/validation-checklist.md" in every_shape
    assert "do not restate them" in every_shape


def test_the_method_points_at_the_shared_files_rather_than_copying_them() -> None:
    """The upstream file says so itself: "Do not restate any part of this file
    elsewhere". A second copy of the Basis table is a second contract."""
    assert not states(
        "_method/_shared/confidence-model.md",
        "_method/_shared/validation-checklist.md",
    )
    # The two rules are cited, never restated as their own table.
    assert "a finding graded `Confirmed` must carry a locus" in method()
    assert squash(
        "absence is not `Confirmed` -- a search returning nothing is `Confirmed`"
        " about the search and `Inferred` about the thing's actual absence"
    ) in method()
    body = method_text()
    assert "| Basis | Definition |" not in body
    assert "Description only" not in body  # the Ceiling table, not ours to copy


def test_the_method_is_not_rebuilt_per_run() -> None:
    """The point of the split. Two runs of different estates read byte-identical
    method; only the brief differs."""
    assert method_source_path().is_file()
    assert "{" + "staging_abs" + "}" not in method_text()


# --------------------------------------------------------------------------
# 1. The blindfold is gone, the sandbox is not
# --------------------------------------------------------------------------

#: A prohibition whose OBJECT is an analysis/evidence file. Deliberately narrow:
#: it must not match the sandbox rule, which prohibits a LOCATION (outside the
#: staging directory) rather than the evidence itself.
_BLINDFOLD = re.compile(
    r"(?:do\s+not|don't|never)\s+(?:read|open)[^.\n]*"
    r"(?:_analysis|analysis file|group analys|individual analys)"
    r"|use\s+ONLY\s+your\s+bundle",
    re.IGNORECASE,
)

#: The two literal lines that caused the fabrications. Kept as fixtures so the
#: regex above cannot silently rot into something that matches nothing.
_HISTORICAL_BLINDFOLDS = (
    "   Do NOT read individual _analysis/{group}.md files -- use the bundles + index.",
    "   - Do NOT read individual _analysis/{group}.md files -- use ONLY your bundle + index.",
)


def test_the_blindfold_regex_is_not_vacuous() -> None:
    """Guard the guard: it must still recognise the lines it was written for."""
    for line in _HISTORICAL_BLINDFOLDS:
        assert _BLINDFOLD.search(line), f"regex no longer detects the blindfold: {line!r}"


def test_no_instruction_forbids_reading_the_evidence(every_shape: str) -> None:
    for text, where in ((every_shape, "the brief"), (method_text(), "METHOD.md")):
        hits = [m.group(0) for m in _BLINDFOLD.finditer(text)]
        assert not hits, f"{where} still forbids reading the evidence: {hits}"


def test_the_group_analyses_are_named_as_the_evidence_of_record() -> None:
    assert not states(
        "THE SUMMARY IS A NAVIGATION AID, NOT THE EVIDENCE OF RECORD.",
        "OPEN the group analysis whenever you need to check a claim",
        "The group analyses are the evidence of record",
    )


def test_the_sandbox_rule_survives(every_shape: str, tmp_path: Path) -> None:
    """A cost control was removed; a security boundary was not. The brief states
    it against THIS run's absolute path (the only place that path is known) and
    the method states it as the standing rule for every sub-agent."""
    assert str(tmp_path / "docs_staging") in every_shape
    assert "the security boundary: read nothing outside it" in every_shape
    assert not states(
        "PROHIBITED: do not read any files outside the staging directory. This"
        " is a security boundary and it is absolute.",
        "inside it, read whatever your claims need",
        "The boundary rules in section 2 apply to every sub-agent too.",
    )


def test_the_sandbox_rule_would_not_satisfy_the_blindfold_test(tmp_path: Path) -> None:
    """The two tests above must be measuring different things."""
    staging_abs = str(tmp_path / "docs_staging")
    assert not _BLINDFOLD.search(f"Do NOT read any files outside {staging_abs}/.")


# --------------------------------------------------------------------------
# 2. One depth table, addressed to the documents that exist
# --------------------------------------------------------------------------

#: The retired 12-document set the depth table handed line targets to, while the
#: DOCUMENT CATALOG three lines above forbade writing any of them.
_RETIRED_DOCS = (
    "00_EXEC", "01_OVERVIEW", "02_ARCH", "03_DATA", "04_BIZ", "05_OPS",
    "06_ENV", "07_SEC", "08_PAT", "09_RUN", "10_ONBOARD", "11_MIG",
)

_LIVE_DOCS = (
    "00_AGENT_BRIEF", "01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY",
    "03_DATA_FLOWS_AND_LINEAGE", "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
    "05_RUNTIME_CONFIG_AND_EXTERNALS", "06_OPERATIONS_TESTING_AND_DEBUGGING",
    "07_QUALITY_SECURITY_RISK_SCORECARD", "08_CHANGE_PLAYBOOK",
)


def test_no_retired_document_name_appears(every_shape: str) -> None:
    for text in (every_shape, method_text()):
        for name in _RETIRED_DOCS:
            # \b stops 03_DATA from matching 03_DATA_FLOWS_AND_LINEAGE.
            assert not re.search(rf"\b{name}\b", text), f"retired doc name {name}"


def test_the_nine_live_documents_are_still_demanded(every_shape: str) -> None:
    """The brief demands them through the floor table (which scales per estate);
    the method demands them through the agent table and the catalog."""
    for name in _LIVE_DOCS:
        assert name in every_shape, f"live doc {name} missing from the brief"
        assert name in method_text(), f"live doc {name} missing from METHOD.md"


def test_the_depth_table_and_its_plumbing_are_gone(every_shape: str) -> None:
    for text in (every_shape, method_text()):
        for stale in (
            "MINIMUM line targets", "Min Lines", "depth scaling", "DEPTH GUIDANCE",
            "1000+ line docs", "x depth scaling",
        ):
            assert stale not in text, f"stale depth guidance: {stale!r}"


_FLOOR_ROW = re.compile(r"^\s*\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*$", re.MULTILINE)


def _floor_rows(prompt: str) -> dict[str, int]:
    """The depth floors the brief actually states, parsed back out of it."""
    return {
        name: int(value) for name, value in _FLOOR_ROW.findall(prompt)
        if not set(name) <= {"-"}
    }


# WHY THIS REPLACED A BAN. This block used to assert that NO minimum-line
# instruction appeared anywhere: the depth table had just been deleted because
# it named a retired 12-document set the DOCUMENT CATALOG forbids. Removing it
# was right on the facts and wrong in effect -- it was the only depth signal in
# the instructions, 00_AGENT_BRIEF went from 628 lines (run 258) to 76 (run
# 260), and `doc_quality_scorer` flagged all nine documents `below_min_lines`
# against the minimums it still grades by. The defect was never "a line minimum
# exists"; it was TWO tables free to disagree, one of them addressed to
# documents that cannot be written. So the rule under test is now: exactly one
# table, sourced from the grader, addressed to the nine live documents, framed
# as a floor that detects missed evidence rather than a length to fill.
#
# The table stays in the BRIEF and not in METHOD.md because it is the one depth
# signal that scales with the estate. How to read it is method, and lives there.


def test_a_depth_floor_is_stated_for_every_live_document(every_shape: str) -> None:
    floors = _floor_rows(every_shape)
    for name in _LIVE_DOCS:
        assert name in floors, f"no depth floor for {name}"


def test_no_depth_floor_is_addressed_to_a_document_that_cannot_be_written(
    every_shape: str,
) -> None:
    """The original defect, stated directly: a floor for a document the
    DOCUMENT CATALOG forbids is an instruction the model cannot obey."""
    assert set(_floor_rows(every_shape)) == set(_LIVE_DOCS)


@pytest.mark.parametrize("total_jobs", [7, 20, 142, 400])
def test_the_floors_are_the_graders_numbers_and_scale_with_the_estate(
    tmp_path: Path, total_jobs: int,
) -> None:
    prompt = _prompt(tmp_path, total_jobs=total_jobs)
    # The brief states the COLLAPSE floor, not the grading floor. Handing the
    # model the grade would ask for ~1.7x the best output ever measured on this
    # pipeline (run 258 is below the grading floor on all nine documents), and
    # the model would pad to reach it. Both views derive from one table.
    assert _floor_rows(prompt) == collapse_floor_by_doc(total_jobs)
    grading = min_lines_by_doc(total_jobs)
    assert all(_floor_rows(prompt)[d] < grading[d] for d in grading)


def test_a_bigger_estate_gets_a_higher_floor(tmp_path: Path) -> None:
    small = _floor_rows(_prompt(tmp_path, total_jobs=20))
    large = _floor_rows(_prompt(tmp_path, total_jobs=400))
    assert all(large[name] > small[name] for name in _LIVE_DOCS), (small, large)


def test_the_floors_come_from_the_scorer_not_from_a_copy_in_the_brief(
    tmp_path: Path, monkeypatch,
) -> None:
    """The single-source-of-truth check. Move the grader's table and the brief
    must move with it; a literal in the provider module would not."""
    monkeypatch.setitem(
        doc_quality_scorer._BASE_MIN_LINES_AT_50, "00_AGENT_BRIEF", 12345,
    )
    floors = _floor_rows(_prompt(tmp_path, total_jobs=50))
    assert floors["00_AGENT_BRIEF"] == collapse_floor_by_doc(50)["00_AGENT_BRIEF"]
    assert floors["00_AGENT_BRIEF"] != 12345, "the brief states the collapse view"
    assert floors["00_AGENT_BRIEF"] > 4000, "but it still MOVED with the grader"


def test_the_floor_is_framed_as_a_detector_not_as_a_quota(every_shape: str) -> None:
    """Restoring a depth expectation must not restore padding: longer and
    wronger is a failure, not a win."""
    assert "DETECTOR of missed evidence, never a quota" in every_shape
    assert not states(
        "DEPTH FLOOR -- A SHORT DOCUMENT MEANS EVIDENCE WAS MISSED",
        "Read the floor as a DETECTOR, not as a quota.",
        "It is NEVER a reason to stretch prose.",
        "A floor is not a ceiling.",
        "Do NOT pad, and Do NOT truncate to hit a target.",
    )


def test_no_depth_number_is_written_into_the_method() -> None:
    """The numbers scale with the estate, so a copy in the method would be a
    second table addressed to no estate in particular -- the original defect
    with a new home."""
    assert not _floor_rows(method_text())


# --------------------------------------------------------------------------
# 2b. Resume states the remainder
# --------------------------------------------------------------------------
#
# Run 260 resumed, was told "COMPLETED ANALYSIS (30 groups) -> Skip Phase 2
# entirely for these groups" for 15 real analyses against a 20-group manifest
# (each `{group}_summary.md` counted as an analysis), was never told what
# remained, and published documents missing a quarter of the estate.


def test_a_resume_that_owes_work_names_the_groups_it_owes(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE)
    assert "STILL TO ANALYSE (2): fourps, utils" in prompt
    assert "ANALYSED ALREADY (1): afas" in prompt
    assert "GROUPS IN THIS ESTATE (3): afas, fourps, utils" in prompt
    assert "Run Phase 2 for the groups on the STILL TO ANALYSE line" in prompt


def test_a_resume_never_instructs_a_blanket_phase_2_skip(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE)
    assert "Skip Phase 2 entirely for these groups" not in prompt
    assert "COMPLETED ANALYSIS" not in prompt


def test_a_summary_file_is_named_as_not_an_analysis(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE)
    assert squash(
        "A group's summary file is a navigation aid, NEVER an analysis"
    ) in squash(prompt)


def test_a_stale_document_is_not_offered_as_completed_work(tmp_path: Path) -> None:
    """Publishing over a pending group republishes the same gap."""
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE)
    assert "Re-write ALL of them in Phase 3" in prompt


def test_a_finished_resume_still_skips_what_is_finished(tmp_path: Path) -> None:
    """The fix must not turn every resume into a full regeneration."""
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE_COMPLETE)
    assert "STILL TO ANALYSE (0): none" in prompt
    assert "Skip Phase 2 and check its coverage as METHOD.md describes." in prompt
    assert "-> Skip Phase 2.6." in prompt
    assert "only spawn agents whose assigned docs are MISSING" in prompt


def test_a_resume_with_no_checklist_assumes_nothing_is_done(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE_NO_CHECKLIST)
    assert "GROUP CHECKLIST: UNAVAILABLE" in prompt
    assert "Do NOT assume Phase 2 is finished." in prompt
    assert "STILL TO ANALYSE" not in prompt


def test_the_brief_states_the_estates_own_group_list(tmp_path: Path) -> None:
    """A split group owes ONE merged analysis, so the checklist is the analyses
    owed, not the ``_preprocess/`` slices on disk."""
    prompt = _prompt(
        tmp_path,
        preprocess_groups=["afas", "maximoFourps_part1", "maximoFourps_part2", "utils"],
    )
    assert "Groups owed an analysis (3): afas, maximoFourps, utils" in prompt
    assert "Every one of them owes ONE" in prompt


def test_a_huge_estate_defers_the_group_list_to_the_work_plan(tmp_path: Path) -> None:
    """The checklist is a per-run fact and worth stating, but an estate with
    hundreds of groups would turn the brief back into the thing it replaced.
    The work plan carries the complete table either way."""
    groups = [f"g{i:03d}" for i in range(200)]
    prompt = _prompt(tmp_path, preprocess_groups=groups)
    assert "Groups owed an analysis (200):" in prompt
    assert "and 140 more" in prompt
    assert "phase2_work_plan.md" in prompt
    assert len(prompt.encode("utf-8")) < 6000


def test_a_legacy_per_section_analysis_still_counts_on_a_resume(tmp_path: Path) -> None:
    """A project mid-flight has a ``{group}/`` directory of section files on
    disk. The store serves both layouts, so the brief must not send the model to
    rewrite work that is already done."""
    prompt = squash(_prompt(tmp_path, resume_state=_RESUME_STATE))
    assert "Both count as analysed and neither is rewritten." in prompt
    assert "directory of section files rather than one" in prompt


# --------------------------------------------------------------------------
# 2c. Phase 2 writes ONE FILE PER GROUP
# --------------------------------------------------------------------------
#
# The heading parse between Phase 2 and everything after it is the only seam in
# this pipeline that can fail silently, and it did: the guide asked for
# `### COMPONENTS`, the model wrote `## COMPONENTS`, every consumer matched zero
# sections, ~95% of the evidence was dropped without a warning and Phase 3
# documented an estate it could not read.
#
# The answer at the time was one FILE per section, so a missing section was a
# missing file rather than an empty parse. That layout existed to serve two
# Python consumers -- `scripts/bundle_analysis.py` and a Phase 2.5 file-count
# gate -- and both are now deleted, along with the last consumer that parsed
# model prose looking for section names. With nothing counting files, one file
# per group is the simpler artifact, and the absence distinction is carried by
# the rule instead of by the filesystem.


def test_phase_2_writes_one_file_per_group() -> None:
    assert not states(
        "Write ONE FILE PER GROUP: `<staging>/_analysis/{group}.md`, with one H2"
        " heading per section of the analysis guide.",
        "ONE ANALYSIS FILE PER GROUP, ALWAYS.",
    )


def test_the_brief_says_where_each_group_analysis_goes(every_shape: str) -> None:
    assert "/_analysis/{group}.md" in every_shape


def test_the_section_name_is_the_contract_and_the_body_is_free() -> None:
    """The point of the layout: nothing parses inside a section, so the shape
    the model chooses under a heading cannot cost the run its evidence."""
    assert not states(
        "The NAME is the contract.",
        "What goes under a heading is yours to choose -- tables, prose, code"
        " fences, any sub-heading shape that suits the evidence.",
        "spelled in CAPITALS exactly as the guide spells it",
    )


def test_a_section_with_nothing_in_it_is_written_down_not_omitted() -> None:
    """Absence must be representable. A short section says somebody looked; a
    missing one says nobody did. Collapsing the two is what made a thin analysis
    indistinguishable from an unanalysed group."""
    assert not states(
        "A SECTION YOU FOUND NOTHING FOR STILL GETS ITS HEADING",
        "A MISSING HEADING MEANS NOBODY LOOKED",
        "do not pad it into a finding",
    )


def test_the_honest_absence_example_is_a_real_search_not_a_placeholder() -> None:
    """A real one that works, and the degenerate one that does not. The rule is
    unusable without both: "write something" is what produced the five-byte
    file containing the word "None"."""
    body = method()
    assert "No SQL in this group: none of the 14 designs uses tDBInput, tDBRow or tELT*" in body
    assert squash('A heading followed by the single word "None" is NOT that.') in body
    assert "It records no search, supports no grade" in body


def test_one_file_per_group_including_split_groups() -> None:
    assert not states(
        "A split group is merged into ONE `{group}.md` covering both parts;"
        " never one file per part.",
        "Never merge two groups into one file",
    )


def test_the_summary_sits_beside_the_analysis() -> None:
    assert not states(
        "`<staging>/_analysis/{group}_summary.md`",
        "It is a navigation aid; it does not replace the analysis as the"
        " evidence of record.",
    )


def test_the_group_analysis_is_covered_whole_and_never_drops_a_unit() -> None:
    """This used to also assert the per-group byte cap ("MAXIMUM: {n}KB for the
    group's section files TOGETHER").  That cap is gone: it was computed from
    the PROJECT total and applied PER GROUP, so on a measured 24-group estate
    every group got the same 60 KB regardless of its own evidence -- 954% of
    one group's 6 KB and 2.3% of another's 2,569 KB.  The rule that was
    ATTACHED to it is not a size rule and is what this test now pins; the cap's
    removal and the retrieval-corpus intent that replaced it are covered by
    ``test_prompt_analysis_budget.py``."""
    assert "MAXIMUM:" not in method_text()
    assert "for the group's section files TOGETHER" not in method_text()
    assert "EVERY job in the group must still be named" in method()


def test_coverage_is_checked_by_the_agent_and_not_by_a_file_count() -> None:
    """The Phase 2.5 gate was `ls | grep -c` against a denominator Python
    computed from the analysis guide. Both are gone: the count only existed
    because the per-section layout existed, and a count of files was never the
    question -- whether every group was analysed is."""
    assert not states(
        "There is no Python gate counting your files.",
        "per GROUP and never per worker",
        "a worker that returned \"done\" having written three of its four groups"
        " passes a per-worker check and fails this one",
    )
    assert "grep -c" not in method_text()


def test_a_thin_or_missing_group_is_retried_once_then_reported() -> None:
    assert not states(
        "re-spawn a sub-agent for THAT group only",
        "One retry per group.",
        "log a WARNING naming every group and every section still missing",
        "publishing what is good with the gap named beats publishing nothing",
    )


# --------------------------------------------------------------------------
# 3. Coverage replaces length
# --------------------------------------------------------------------------


def test_the_coverage_obligation_is_stated() -> None:
    assert not states(
        "COVERAGE OBLIGATION",
        "ACCOUNT FOR EVERY scanned design in its scope",
        "named in a row of a table, or named in an explicit omission list",
    )


def test_a_listed_omission_is_allowed_and_a_silent_one_is_not() -> None:
    assert not states(
        "Deliberate omissions are ACCEPTABLE and must be LISTED.",
        "An explicit, listed omission is fine. A silent one is a defect.",
        "Copy_of_*",
    )


def test_the_measured_coverage_failure_is_named() -> None:
    assert '"All 142 jobs" and named 78 of them' in method()


def test_the_anti_padding_rules_are_kept() -> None:
    assert not states("Do NOT pad.", "Do NOT truncate to hit a target.")


def test_the_estate_size_is_a_brief_fact_the_method_defers_to(
    tmp_path: Path,
) -> None:
    """The count belongs to the run; what the count CONTAINS is method."""
    prompt = _prompt(tmp_path, total_jobs=7, total_groups=2)
    assert "Scanned designs:   7 across 2 groups" in prompt
    assert not states(
        "The run message states how many scanned designs this project has,"
        " across how many groups.",
        "It counts every scanned design, of every kind",
        "on a measured estate nearly a fifth of it was reusable",
    )


# --------------------------------------------------------------------------
# 4. The parallel-read throttle is recovery guidance now
# --------------------------------------------------------------------------


def test_the_parallel_read_cap_is_gone() -> None:
    for stale in (
        "Never issue more than 3 parallel Read calls",
        "more than 3 parallel",
        "cascade-fail",
    ):
        assert stale not in method_text(), f"read throttle survives: {stale!r}"


def test_a_failed_read_is_retried_not_abandoned() -> None:
    assert not states(
        "If a batch of reads fails, retry the failed reads individually",
        "never a reason to proceed without the evidence",
        "Read in parallel as widely as the work needs.",
    )


def test_a_large_file_is_walked_to_the_end() -> None:
    """`head -N` alone reads the first window and drops the rest."""
    assert not states(
        "Walk it in successive windows",
        "silently leaves the rest of your evidence unread",
    )


# --------------------------------------------------------------------------
# 5. Grounding without leaking internals
# --------------------------------------------------------------------------


def test_quoted_evidence_must_be_copied_not_reconstructed() -> None:
    assert not states(
        "EVIDENCE GROUNDING -- COPY, NEVER RECONSTRUCT",
        "COPIED character for character",
        "if you did not copy it, do not quote it",
    )


def test_the_measured_grounding_failure_is_named() -> None:
    assert "only 16% of the quoted code lines existed verbatim" in method()


def test_client_prose_never_names_the_machinery() -> None:
    assert not states(
        "CLIENT PROSE -- NAME THE ESTATE, NEVER THE MACHINERY",
        "They NEVER name the machinery that produced them.",
        "based on our internal analysis",
        "per the analysis in ...",
        "the analysis file states",
    )
    # The internal file names the writer may not put in a document, enumerated
    # in the prohibition itself -- not merely present somewhere in the method.
    assert squash(
        "(_analysis, _assessment, _preprocess, _ref, _provenance, quick_scan,"
        " docs_staging, scan_result.json)"
    ) in method()


def test_the_retired_bundle_is_not_still_named_as_a_leakage_phrase() -> None:
    """``_bundle_*`` files no longer exist, so prohibiting the phrase "according
    to the bundle" would be a rule about an artifact the run cannot produce --
    prompt bytes that teach the reader a shape that is gone."""
    assert "_bundle" not in method_text()
    assert "according to the bundle" not in method_text()


def test_provenance_goes_to_a_sidecar_the_reader_never_sees() -> None:
    assert not states(
        "`<staging>/_provenance/{DOC_NAME}.json`",
        '"verbatim": true',
        "A quoted block with no sidecar entry counts as unsourced.",
        # Created once, up front, before Phase 3 writes into it.
        "mkdir -p <staging>/_provenance <staging>/_knowledge",
        # ...and it survives cleanup.
        "KEEP `<staging>/_provenance/`",
    )


def test_the_sidecar_declares_the_components_a_claim_rests_on() -> None:
    """The one field the attribution guard acts on.

    Without it, verifying a document means running a regex over its prose --
    the same dependency on the model's writing shape that cost run 258 ~95% of
    its evidence at the Phase 2 seam. Declared, it is set membership against
    the evidence of the group the writer itself named.
    """
    assert not states(
        '"components": ["tMSSqlOutput_1", "tMap_3"]',
        '"components" lists the component instances the claim rests on',
        # Absence must be representable, or the writer pads the field to look
        # compliant and the check verifies invented declarations.
        "Use [] when the claim rests on no single component",
        # And a wrong declaration must be named as worse than no declaration.
        "naming a component you did not read is worse than naming none",
    )


def test_the_declared_components_come_from_the_declared_group() -> None:
    """Per-GROUP, not per-run: a component real in a sibling group is exactly
    the misattribution the guard exists to catch, and it passes any check made
    against the combined evidence."""
    assert not states(
        'copied from the evidence for that "group"',
        "each name is looked up in that group's own evidence",
    )


def test_prose_citation_is_design_plus_component() -> None:
    assert not states(
        "name the DESIGN and the COMPONENT",
        "never the file you read it from",
    )


# --------------------------------------------------------------------------
# 6. Uncertainty has somewhere to go
# --------------------------------------------------------------------------


def test_uncertainty_has_a_ledger() -> None:
    assert not states(
        "UNRESOLVED EVIDENCE",
        "_knowledge/open-questions.md",
        "Would settle it:",
    )


def test_the_ledger_is_appended_never_overwritten() -> None:
    assert not states(
        "APPEND one entry per open question",
        "Append with a heredoc, never the Write tool",
        "a read-modify-write destroys the others' entries",
        # An indented terminator does not end a heredoc, and that has cost a run.
        "Put the closing `EOF` at the start of its own line",
    )


def test_an_honest_gap_in_a_document_is_correct() -> None:
    assert not states(
        '"not determinable from the scanned sources" is a CORRECT and wanted answer',
        "an honest gap is a correct one",
    )


def test_the_ledger_never_ships_as_a_client_document() -> None:
    """The agent's private notes must live behind an underscore from the start.

    ``services/rag_bridge.py`` pushes any nested ``*.md`` under the docs root
    unless a path segment starts with ``_`` or ``.``::

        if any(part.startswith(("_", ".")) for part in relative.parts):
            continue

    and ``api/routes/projects.py::_count_docs`` counts it as a delivered
    document by the same rule.  So an unprefixed ``knowledge/`` would push the
    agent's unresolved questions into the CLIENT's corpus.

    This asserts the structural fix rather than a cleanup step: every authoring
    path is ``_knowledge/`` already, so there is no window in which the notes
    sit somewhere publishable and no mv that can fail and leave them there.
    """
    body = method_text()
    assert "/_knowledge/" in body, "the ledger must be written behind an underscore"
    # No unprefixed `knowledge` path segment anywhere. Deliberately NOT anchored
    # on a trailing slash: the first version of this check required one, and so
    # missed `mkdir -p {staging}/knowledge` at the end of a line -- the very
    # instruction that created the publishable directory. The publication filter
    # keys on the segment's first character, so match the segment, not the shape.
    offenders = re.findall(r"\S*/(?<!_)knowledge(?:/\S*|\b)", body)
    assert not offenders, (
        f"unprefixed knowledge path(s) would be published to the client: {offenders[:3]}"
    )
    # ...and therefore nothing needs relocating during cleanup.
    cleanup = body.split("## 7. Phase 4")[1]
    assert "mv " not in cleanup, "relocating the ledger at cleanup is a window for losing it"


# --------------------------------------------------------------------------
# 7. Mechanical integrity
# --------------------------------------------------------------------------


def test_prompt_is_ascii(every_shape: str) -> None:
    offenders = sorted({f"U+{ord(c):04X}" for c in every_shape if ord(c) > 127})
    assert not offenders, f"non-ASCII in the prompt: {offenders}"


def test_no_local_variable_is_left_unsubstituted(every_shape: str) -> None:
    """Derived from the function's own locals, so it cannot go out of date."""
    for name in ClaudeCliProvider._build_prompt.__code__.co_varnames:
        assert "{" + name + "}" not in every_shape, f"unsubstituted slot {{{name}}}"


def test_placeholders_addressed_to_the_model_survive_escaping(
    every_shape: str,
) -> None:
    """These braces are literal instructions, not slots; over-eager f-string
    cleanup has silently substituted them before."""
    assert "{timestamp}" in every_shape
    assert "{group}" in every_shape
    for literal in ("{group}", "{DOC_NAME}", "{GROUP}"):
        assert literal in method_text(), f"literal placeholder {literal} lost"


@pytest.mark.parametrize("shape", sorted(_SHAPES), ids=sorted(_SHAPES))
def test_the_brief_is_a_brief_and_not_a_procedure(tmp_path: Path, shape: str) -> None:
    """The size is the point: ~47 KB of interpolated procedure became a page of
    facts. A regression here is somebody moving the method back into Python.

    Rendered against a DEPLOYMENT-SHAPED staging path rather than pytest's
    ``/private/var/folders/...`` tmpdir. The brief names the staging directory
    about eight times, so a 60-character tmpdir adds ~400 bytes that no real
    run pays and that measure the fixture rather than the brief.
    """
    prompt = _prompt(
        tmp_path,
        staging_abs="/srv/nexus/output/acme/docs_staging",
        logs_abs="/srv/nexus/output/acme/logs",
        **_SHAPES[shape],
    )
    assert len(prompt.encode("utf-8")) < 5000


def test_only_reference_bundles_that_are_staged_are_named() -> None:
    """services/claude_service.py stages _ref_core, _ref_business_ops, _ref_user."""
    body = method_text()
    assert "_ref_patterns" not in body
    assert "_ref_{agent}" not in body
    for staged in ("_ref_core*.md", "_ref_business_ops*.md", "_ref_user*.md"):
        assert staged in body


# --------------------------------------------------------------------------
# 8. What made the output good is preserved verbatim
# --------------------------------------------------------------------------

_PRESERVED = (
    "Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.",
    "Never summarize when you can enumerate",
    '"7 of 12 DEU jobs have no tLogCatcher" IS acceptable',
    '"Error handling is weak" is NOT acceptable',
    "Show ACTUAL code/expressions/SQL, then explain",
)


def test_the_instructions_that_worked_are_untouched() -> None:
    assert not states(*_PRESERVED)


def test_the_phase_structure_is_intact() -> None:
    """Phase 2.5 (a Python file-count gate) and Phase 2.8 (the bundler) are
    deliberately absent: both were Python standing between phases, and both are
    deleted. What remains is the work itself."""
    body = method_text()
    for phase in (
        "## 1. The shape of the run",
        "## 2. The workspace",
        "## 3. Evidence discipline",
        "## 4. Runtime evidence",
        "## 5. Phase 2 -- group analysis",
        "## 5b. Phase 2.6 -- scorecard",
        "## 6. Phase 3 -- write the documents",
        "## 7. Phase 4 -- publish",
    ):
        assert phase in body, f"section missing: {phase}"
    assert "PHASE 2.5" not in body
    assert "PHASE 2.8" not in body


def test_the_runtime_tool_surface_is_kept() -> None:
    """The agentic direction. It is a local script, and the method must say so
    in the same breath as the MCP prohibition or the two read as contradictory."""
    assert not states(
        "_tools/runtime_tools.py list",
        "START WITH `list`.",
        "do not guess a tool name, and do not guess an argument",
        "a local script run with Bash, not an MCP tool, and it is not an"
        " exception to this rule",
        "If the script is not there, no runtime source is connected for this"
        " project.",
    )


def test_every_runtime_measurement_rule_survives() -> None:
    assert not states(
        "EVERY PUBLISHED RUNTIME NUMBER CARRIES ITS MEASUREMENT REFERENCE.",
        "NEVER MIX ENVIRONMENTS IN ONE RATIO.",
        "ZERO IS AMBIGUOUS -- TREAT IT AS A QUESTION, NOT AN ANSWER.",
        "ESTABLISH PRESENCE WITHOUT READING CONTENT.",
        "ABSENCE IS A FINDING, AND IT IS STATED PLAINLY.",
    )


def test_the_server_owns_publication_not_the_agent() -> None:
    assert not states(
        "Do NOT delete `<staging>/_analysis/` or `<staging>/_preprocess/`.",
        "Do NOT push to RAG. The server does that after a successful run.",
    )


# --------------------------------------------------------------------------
# 9. Both scorecard modes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_both_scorecard_modes_render_in_the_brief(
    tmp_path: Path, monkeypatch, enabled: bool,
) -> None:
    """Whether the scorecard runs is a per-run switch, so the brief states it
    and the method describes both branches. Before this, the prompt hardcoded
    the phase and the config flag had no effect on this lane at all."""
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", enabled, raising=False)
    prompt = _prompt(tmp_path)

    assert "{scorecard_line}" not in prompt
    if enabled:
        assert "LLM scorecard:     enabled -- run the Phase 2.6 scoring sub-agent" in prompt
        assert "DISABLED" not in prompt
    else:
        assert "LLM scorecard:     DISABLED" in prompt
        assert "do not spawn a scoring sub-agent" in prompt

    assert prompt.isascii()
    assert not _BLINDFOLD.search(prompt)


def test_the_method_describes_both_scorecard_branches() -> None:
    assert not states(
        "When it is ENABLED, spawn ONE sub-agent to produce a structured assessment",
        "When it is DISABLED, do not spawn a scoring sub-agent.",
        "Do not invent a number to fill the shape of a scorecard.",
        "A score without evidence is invalid.",
    )


def test_the_scorecard_doc_does_not_demand_scores_that_were_not_computed() -> None:
    assert not states(
        "If the scores were not computed for this run, say so where a score would go.",
        '"Security: 2/5 (see 07_QUALITY_SECURITY_RISK_SCORECARD)"',
    )
    assert "see Assessment Scorecard for details" not in method_text()


# --------------------------------------------------------------------------
# 10. Agent count matches the agent table
# --------------------------------------------------------------------------


def test_the_spawn_count_equals_the_number_of_defined_agents() -> None:
    """The large-project branch used to announce 4 agents while defining 3, and
    the retry map listed 3. It also defined two near-identical tables that
    differed only in which bundle the 07/08 writer read -- and the bundler is
    gone, so there is one table."""
    body = method_text()
    rows = [
        line for line in body.splitlines()
        if line.startswith("| ") and "_ref_" in line
    ]
    assert len(rows) == 3, rows
    assert "Three agents, and that is the whole table." in body
    for doc in _LIVE_DOCS:
        assert any(doc in row for row in rows), f"{doc} is assigned to no agent"


def test_the_write_model_is_a_brief_fact(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, write_model="sonnet-x")
    assert 'Pass model="sonnet-x" to every Phase 3 Task call.' in prompt


# --------------------------------------------------------------------------
# 11. The depth floor is CALIBRATED, not aspirational
#
# Restoring a depth expectation is only safe if a healthy run never trips it.
# The grading table in doc_quality_scorer has never been met by any run: on
# ETTM (142 units) it totals 14,825 lines, run 258 -- the best output this
# pipeline has produced -- reached 8,949 (60%), and it is below the grading
# floor on all nine documents. Stating that as the instruction asks every run
# for ~1.7x the best ever measured, and the model pads to reach it. Filler is
# indistinguishable from content to a reader and buries the real findings, so
# longer-and-wronger is a failure, not a win.
#
# These two runs are the calibration data. Measured, not estimated.
# --------------------------------------------------------------------------

#: ettm_TEST_Claude run 258 -- accurate findings, real domain depth, the bar.
_RUN_258_LINES = {
    "00_AGENT_BRIEF": 628,
    "01_ARCHITECTURE_MAP": 640,
    "02_COMPONENT_INVENTORY": 854,
    "03_DATA_FLOWS_AND_LINEAGE": 1177,
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS": 1247,
    "05_RUNTIME_CONFIG_AND_EXTERNALS": 884,
    "06_OPERATIONS_TESTING_AND_DEBUGGING": 1046,
    "07_QUALITY_SECURITY_RISK_SCORECARD": 1294,
    "08_CHANGE_PLAYBOOK": 1179,
}
#: ettm_fix run 260 -- resumed on 15 of 20 groups with no depth signal at all.
_RUN_260_LINES = {
    "00_AGENT_BRIEF": 76,
    "01_ARCHITECTURE_MAP": 83,
    "02_COMPONENT_INVENTORY": 66,
    "03_DATA_FLOWS_AND_LINEAGE": 97,
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS": 66,
    "05_RUNTIME_CONFIG_AND_EXTERNALS": 31,
    "06_OPERATIONS_TESTING_AND_DEBUGGING": 50,
    "07_QUALITY_SECURITY_RISK_SCORECARD": 94,
    "08_CHANGE_PLAYBOOK": 47,
}
_ETTM_UNITS = 142


def test_the_best_run_we_have_does_not_trip_the_floor() -> None:
    """A floor that fires on good output is noise, and gets ignored."""
    floors = collapse_floor_by_doc(_ETTM_UNITS)
    tripped = {d: (_RUN_258_LINES[d], floors[d]) for d in floors if _RUN_258_LINES[d] < floors[d]}
    assert not tripped, (
        f"the floor fires on run 258, the best output this pipeline has produced: {tripped}"
    )


def test_the_collapsed_run_trips_the_floor_on_every_document() -> None:
    """And a floor that misses the regression it was added for is pointless."""
    floors = collapse_floor_by_doc(_ETTM_UNITS)
    missed = [d for d in floors if _RUN_260_LINES[d] >= floors[d]]
    assert not missed, f"the floor misses run 260's collapse on: {missed}"


def test_the_floor_sits_below_the_grade_on_every_document() -> None:
    grading = min_lines_by_doc(_ETTM_UNITS)
    floors = collapse_floor_by_doc(_ETTM_UNITS)
    assert all(floors[d] < grading[d] for d in grading)


def test_the_floor_keeps_margin_under_the_worst_healthy_document() -> None:
    """Pins the derivation, so a later edit cannot quietly raise it into the
    healthy band. Run 258's weakest document reached 0.42 of the grading floor;
    the collapse fraction must stay meaningfully under that."""
    grading = min_lines_by_doc(_ETTM_UNITS)
    worst = min(_RUN_258_LINES[d] / grading[d] for d in grading)
    assert worst == pytest.approx(0.42, abs=0.01), worst
    assert doc_quality_scorer._COLLAPSE_FLOOR_FRACTION < worst * 0.9, (
        "the collapse floor has drifted up into the range real output occupies"
    )
