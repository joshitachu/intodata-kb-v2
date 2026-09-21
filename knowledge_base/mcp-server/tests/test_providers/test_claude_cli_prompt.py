"""The doc-generation prompt shared by ``claude-cli`` and ``opencode-cli``.

Every assertion here is a verified production failure of run 258, whose writers
were forbidden to open the evidence and so invented a SOAP namespace, a context
variable and a component type that do not exist -- while the real values sat in
``_analysis/``.  ``opencode_cli._build_prompt`` returns this prompt verbatim, so
each rule below covers both lanes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider, expected_analysis_sections
from mcp_server.providers.custom.analysis_parser import PHASE2_SECTION_NAMES
from mcp_server.services import doc_quality_scorer
from mcp_server.services.doc_quality_scorer import collapse_floor_by_doc, min_lines_by_doc

#: A staged ANALYSIS_GUIDE, in the shape every shipped guide has: the evidence
#: sections the workers must produce, mixed with headings that are instructions
#: to the worker rather than sections it owes a file for.
_GUIDE_EVIDENCE_SECTIONS = (
    "COMPONENTS", "TRANSFORMATION_DETAIL", "SOURCE_SQL", "RISK_FLAGS",
)
_GUIDE_INSTRUCTION_HEADINGS = ("SELF_CHECK_BEFORE_RETURNING", "ASSISTANT_DOC_OBLIGATIONS")


def _stage_guide(tmp_path: Path, sections=_GUIDE_EVIDENCE_SECTIONS) -> Path:
    """Write the analysis guide preprocessing stages before the prompt is built."""
    preprocess = tmp_path / "docs_staging" / "_preprocess"
    preprocess.mkdir(parents=True, exist_ok=True)
    body = ["# Analysis Guide\n", "## Platform Reference\n", "### Job Structure\n"]
    for name in sections:
        body.append(f"### {name}\n\nWhat to extract for {name}.\n")
    for name in _GUIDE_INSTRUCTION_HEADINGS:
        body.append(f"### {name}\n\nAn instruction, not a section you owe a file for.\n")
    path = preprocess / "_analysis_guide.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


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


def _prompt(tmp_path: Path, *, guide: bool = False, **over) -> str:
    """Build the prompt, optionally with the analysis guide staged.

    Both branches are real: preprocessing stages the guide before the provider
    runs, and a staging tree that lost it must still produce a prompt that
    states the section contract by reference instead of inventing a count.
    """
    if guide:
        _stage_guide(tmp_path)
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
    "bundles_exist": False,
    "bundles_stale": False,
    "completed_docs": ["00_AGENT_BRIEF"],
}

#: The same estate once Phase 2 really is finished. Resume must stay useful:
#: the fix for "skipped everything" must not become "re-runs everything".
_RESUME_STATE_COMPLETE = {
    **_RESUME_STATE,
    "analysis_files": ["afas", "fourps", "utils"],
    "pending_groups": [],
    "analysis_complete": True,
    "bundles_exist": True,
}

#: A staging tree with no manifest: the server cannot name the remainder, so the
#: prompt must not let the model assume there is none.
_RESUME_STATE_NO_CHECKLIST = {
    "preprocess_exists": True,
    "expected_groups": [],
    "expected_groups_source": "unknown",
    "analysis_files": ["afas"],
    "pending_groups": [],
    "analysis_complete": False,
    "scorecard_exists": False,
    "bundles_exist": False,
    "bundles_stale": False,
    "completed_docs": [],
}

#: Every shape the prompt can take. A rule that holds for one and not the others
#: is not a rule; run 258 shipped from the default (small, fresh) shape.
_SHAPES: dict[str, dict] = {
    "small_fresh": {},
    "large_fresh": {"total_jobs": 400},
    "small_resume": {"resume_state": _RESUME_STATE},
    "large_resume": {"total_jobs": 400, "resume_state": _RESUME_STATE},
    "resume_complete": {"resume_state": _RESUME_STATE_COMPLETE},
    "resume_no_checklist": {"resume_state": _RESUME_STATE_NO_CHECKLIST},
    # The section contract has two renderings -- an exact list read off the
    # staged guide, and the by-reference wording when there is no guide to read.
    # Every rule below must hold in both, or it holds in whichever one the next
    # run happens to take.
    "guide_staged": {"guide": True},
    "guide_staged_resume": {"guide": True, "resume_state": _RESUME_STATE},
}


@pytest.fixture(params=sorted(_SHAPES), ids=sorted(_SHAPES))
def every_shape(request, tmp_path: Path) -> str:
    return _prompt(tmp_path, **_SHAPES[request.param])


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
    hits = [m.group(0) for m in _BLINDFOLD.finditer(every_shape)]
    assert not hits, f"the prompt still forbids reading the evidence: {hits}"


def test_the_group_analyses_are_named_as_the_evidence_of_record(every_shape: str) -> None:
    assert "THE BUNDLE IS THE STARTING POINT, NOT THE EVIDENCE OF RECORD." in every_shape
    assert "OPEN the group analysis whenever" in every_shape
    assert "the group analysis is the\n     evidence of record" in every_shape


def test_the_sandbox_rule_survives_in_both_phases(every_shape: str, tmp_path: Path) -> None:
    """A cost control was removed; a security boundary was not."""
    rule = f"Do NOT read any files outside {tmp_path / 'docs_staging'}/"
    assert every_shape.count(rule) >= 2, (
        "the sandbox rule must be stated for Phase 2 analysts and Phase 3 writers"
    )
    # And it must be stated as absolute, not as a hint.
    assert "This is a security\n     boundary and it is absolute." in every_shape
    assert "That is a security boundary\n     and it is absolute." in every_shape


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
    for name in _RETIRED_DOCS:
        # \b stops 03_DATA from matching 03_DATA_FLOWS_AND_LINEAGE.
        assert not re.search(rf"\b{name}\b", every_shape), f"retired doc name {name}"


def test_the_nine_live_documents_are_still_demanded(every_shape: str) -> None:
    for name in _LIVE_DOCS:
        assert name in every_shape, f"live doc {name} missing from the prompt"


def test_the_depth_table_and_its_plumbing_are_gone(every_shape: str) -> None:
    for stale in (
        "MINIMUM line targets", "Min Lines", "depth scaling", "DEPTH GUIDANCE",
        "1000+ line docs", "x depth scaling",
    ):
        assert stale not in every_shape, f"stale depth guidance: {stale!r}"


_FLOOR_ROW = re.compile(r"^\s*\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*$", re.MULTILINE)


def _floor_rows(prompt: str) -> dict[str, int]:
    """The depth floors the prompt actually states, parsed back out of it."""
    return {
        name: int(value) for name, value in _FLOOR_ROW.findall(prompt)
        if not set(name) <= {"-"}
    }


# WHY THIS REPLACED A BAN. This block used to assert that NO minimum-line
# instruction appeared anywhere: the depth table had just been deleted because
# it named a retired 12-document set the DOCUMENT CATALOG forbids. Removing it
# was right on the facts and wrong in effect -- it was the only depth signal in
# the prompt, 00_AGENT_BRIEF went from 628 lines (run 258) to 76 (run 260), and
# `doc_quality_scorer` flagged all nine documents `below_min_lines` against the
# minimums it still grades by. The defect was never "a line minimum exists"; it
# was TWO tables free to disagree, one of them addressed to documents that
# cannot be written. So the rule under test is now: exactly one table, sourced
# from the grader, addressed to the nine live documents, framed as a floor that
# detects missed evidence rather than a length to fill.


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
    # The prompt states the COLLAPSE floor, not the grading floor. Handing the
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


def test_the_floors_come_from_the_scorer_not_from_a_copy_in_the_prompt(
    tmp_path: Path, monkeypatch,
) -> None:
    """The single-source-of-truth check. Move the grader's table and the
    prompt must move with it; a literal in the provider module would not."""
    monkeypatch.setitem(
        doc_quality_scorer._BASE_MIN_LINES_AT_50, "00_AGENT_BRIEF", 12345,
    )
    floors = _floor_rows(_prompt(tmp_path, total_jobs=50))
    assert floors["00_AGENT_BRIEF"] == collapse_floor_by_doc(50)["00_AGENT_BRIEF"]
    assert floors["00_AGENT_BRIEF"] != 12345, "the prompt states the collapse view"
    assert floors["00_AGENT_BRIEF"] > 4000, "but it still MOVED with the grader"


def test_the_floor_is_framed_as_a_detector_not_as_a_quota(every_shape: str) -> None:
    """Restoring a depth expectation must not restore padding: longer and
    wronger is a failure, not a win."""
    assert "DEPTH FLOOR -- A SHORT DOCUMENT MEANS EVIDENCE WAS MISSED:" in every_shape
    assert "Read the floor as a DETECTOR, not as a quota." in every_shape
    assert "It is NEVER a reason to stretch prose." in every_shape
    assert "A floor is not a ceiling." in every_shape
    assert "Do NOT pad, and Do NOT truncate to hit\n   a target." in every_shape


# --------------------------------------------------------------------------
# 2b. Resume states the remainder, and the gate counts against a checklist
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
    assert (
        "A group's summary file is a\n   navigation aid, NEVER an analysis" in prompt
    )


def test_stale_artifacts_are_not_offered_as_completed_work(tmp_path: Path) -> None:
    """Bundling or publishing over a pending group republishes the same gap."""
    prompt = _prompt(
        tmp_path, resume_state={**_RESUME_STATE, "bundles_exist": True, "bundles_stale": True},
    )
    assert "-> Skip Phase 2.8." not in prompt
    assert "Re-run Phase 2.8 once those analyses exist" in prompt
    assert "Re-write ALL of them in Phase 3" in prompt


def test_a_finished_resume_still_skips_what_is_finished(tmp_path: Path) -> None:
    """The fix must not turn every resume into a full regeneration."""
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE_COMPLETE)
    assert "STILL TO ANALYSE (0): none" in prompt
    assert "Skip Phase 2 and go to the Phase 2.5 gate." in prompt
    assert "-> Skip Phase 2.6." in prompt
    assert "-> Skip Phase 2.8." in prompt
    assert "only spawn agents whose assigned docs are MISSING" in prompt


def test_a_resume_with_no_checklist_assumes_nothing_is_done(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE_NO_CHECKLIST)
    assert "GROUP CHECKLIST: UNAVAILABLE" in prompt
    assert "Do NOT assume Phase 2 is finished." in prompt
    assert "STILL TO ANALYSE" not in prompt


def test_the_gate_counts_against_the_estates_own_group_list(tmp_path: Path) -> None:
    """A split group owes ONE merged analysis, so the gate's checklist is the
    analyses owed, not the ``_preprocess/`` slices on disk."""
    prompt = _prompt(
        tmp_path,
        preprocess_groups=["afas", "maximoFourps_part1", "maximoFourps_part2", "utils"],
    )
    assert "3 group(s) this estate owes an analysis" in prompt
    assert "afas, maximoFourps, utils" in prompt
    assert "A group with no directory counts as 0 sections." in prompt
    assert "_summary.md is NOT a section" in prompt


# --------------------------------------------------------------------------
# 2c. Phase 2 writes one FILE per section (plan slice 2, decision 2)
# --------------------------------------------------------------------------
#
# The heading parse between Phase 2 and everything after it is the only seam in
# this pipeline that can fail silently, and it did: the guide asked for
# `### COMPONENTS`, the model wrote `## COMPONENTS`, every consumer matched zero
# sections, ~95% of the evidence was dropped without a warning and Phase 3
# documented an estate it could not read -- inventing a SOAP namespace, a
# `context.fourps_token` that does not exist and a `tSchematron_1` that is not a
# Talend component, while the real values sat in `_analysis/`.  A missing FILE
# cannot be confused with an empty parse.


def test_phase_2_writes_one_file_per_section(every_shape: str, tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    assert "Writes the analysis as ONE FILE PER SECTION, into a directory named for the" in every_shape
    assert f"{staging}/_analysis/{{group}}/COMPONENTS.md" in every_shape
    assert f"{staging}/_analysis/{{group}}/{{SECTION}}.md -- one Write per section." in every_shape
    # ...and the sub-agent is allowed to create the directory it writes into.
    assert f"Bash mkdir -p {staging}/_analysis/{{group}}" in every_shape
    prohibition = every_shape.split("PROHIBITED ACTIONS:")[1]
    assert f"{staging}/_analysis/{{group}}/ directories" in prohibition, (
        "the mkdir prohibition must exempt the directory Phase 2 is told to create"
    )


def test_the_file_name_is_the_contract_and_the_body_is_free(every_shape: str) -> None:
    """The point of the layout: nothing parses inside a section file, so the
    heading shape the model chooses cannot cost the run its evidence again."""
    assert "THE FILE NAME IS THE CONTRACT; the format INSIDE a file is yours to choose." in every_shape
    assert "Nothing downstream looks inside a section file for structure" in every_shape
    assert "the section name in CAPITALS, then .md." in every_shape
    assert "not one file holding every section." in every_shape


def test_a_section_with_nothing_in_it_is_written_down_not_omitted(
    every_shape: str,
) -> None:
    """Absence must be representable -- plan decision 5. A short file says
    somebody looked; a missing file says nobody did. Collapsing the two is what
    made a thin analysis indistinguishable from an unanalysed group."""
    assert "A SECTION YOU FOUND NOTHING FOR STILL GETS ITS FILE" in every_shape
    assert "A MISSING FILE MEANS NOBODY LOOKED." in every_shape
    assert "do not pad it into a finding" in every_shape
    # And the gate must not read a missing file as "nothing to report".
    assert (
        "a missing file means nobody looked, never \"nothing to say\"" in every_shape
    )


def test_one_directory_per_group_and_one_per_split_group(every_shape: str) -> None:
    assert "ONE ANALYSIS DIRECTORY PER GROUP, ALWAYS." in every_shape
    assert "Never one directory per part." in every_shape
    assert "one set of section files covering both" in every_shape


def test_the_summary_lives_inside_the_group_directory(
    every_shape: str, tmp_path: Path,
) -> None:
    staging = tmp_path / "docs_staging"
    assert f"{staging}/_analysis/{{group}}/_summary.md" in every_shape
    assert "The leading underscore keeps it out of the section count." in every_shape


def test_the_size_cap_applies_to_the_directory_but_still_never_drops_a_unit(
    every_shape: str,
) -> None:
    assert "for the group's section files TOGETHER." in every_shape
    assert "EVERY job in the group must\n      still be named" in every_shape


def test_the_gate_counts_sections_from_the_directory_listing(
    every_shape: str, tmp_path: Path,
) -> None:
    """Advisory as before -- the change is that the numbers are now exact,
    taken from `ls` rather than from the model's impression of a file."""
    staging = tmp_path / "docs_staging"
    assert (
        f"Bash ls -1 {staging}/_analysis/{{group}}/ | grep -c '^[A-Z].*[.]md$'"
        in every_shape
    )
    assert "sections\", and name the" in every_shape
    assert "A group with no directory counts as 0 sections." in every_shape
    assert "or its section files\n      total under 1KB" in every_shape


def test_the_gate_states_the_exact_section_count_when_the_guide_is_staged(
    tmp_path: Path,
) -> None:
    prompt = _prompt(tmp_path, guide=True)
    n = len(_GUIDE_EVIDENCE_SECTIONS)
    assert f"This estate's guide names {n} sections" in prompt
    assert f"That count must be {n} -- one file for each section of" in prompt
    assert f"\"<group>: N of {n} sections\"" in prompt
    for name in _GUIDE_EVIDENCE_SECTIONS:
        assert name in prompt


def test_a_guide_heading_that_is_an_instruction_is_not_a_file_anybody_owes(
    tmp_path: Path,
) -> None:
    """The guides carry all-caps headings that are instructions to the worker
    (`SELF_CHECK_BEFORE_RETURNING`), not sections it produces. Counting them
    would make every group permanently short of its own contract."""
    guide = _stage_guide(tmp_path)
    for name in _GUIDE_INSTRUCTION_HEADINGS:
        assert f"### {name}" in guide.read_text(encoding="utf-8")
    sections = expected_analysis_sections(tmp_path / "docs_staging", "_analysis_guide.md")
    assert sections == tuple(
        sorted(_GUIDE_EVIDENCE_SECTIONS, key=PHASE2_SECTION_NAMES.index)
    )
    for name in _GUIDE_INSTRUCTION_HEADINGS:
        assert name not in sections


def test_the_section_list_is_read_off_the_guide_not_kept_beside_it(
    tmp_path: Path,
) -> None:
    """One contract. Change the guide and the gate's denominator changes with
    it; a copy in the provider module would keep counting the old set."""
    _stage_guide(tmp_path, sections=("COMPONENTS", "RISK_FLAGS"))
    prompt = _prompt(tmp_path)
    assert "This estate's guide names 2 sections" in prompt
    assert "COMPONENTS, RISK_FLAGS" in prompt
    assert "TRANSFORMATION_DETAIL" not in prompt.split("PHASE 2.5")[0].split("step e")[0]


def test_the_section_list_is_in_one_deterministic_order(tmp_path: Path) -> None:
    """A prompt is prefix-cached on its bytes. A set-ordered list would void
    that cache non-deterministically -- this repo has shipped that defect once
    already (a casefold-sorted set in the change-impact view)."""
    scrambled = ("RISK_FLAGS", "COMPONENTS", "SOURCE_SQL")
    in_contract_order = sorted(scrambled, key=PHASE2_SECTION_NAMES.index)
    assert in_contract_order != list(scrambled)
    _stage_guide(tmp_path, sections=scrambled)
    prompt = _prompt(tmp_path)
    assert ", ".join(in_contract_order) in prompt, (
        "sections must render in contract order regardless of guide order"
    )
    assert prompt == _prompt(tmp_path)


def test_no_guide_means_the_contract_is_stated_by_reference_not_invented(
    tmp_path: Path,
) -> None:
    """A staging tree that lost its guide must not produce a made-up count:
    the gate then points at the guide itself, which is where the list lives."""
    prompt = _prompt(tmp_path)
    assert "One file for every section heading in the analysis guide" in prompt
    assert "That count must equal the number of section headings in" in prompt
    assert "This estate's guide names" not in prompt


def test_a_legacy_flat_analysis_still_counts_on_a_resume(tmp_path: Path) -> None:
    """A project mid-flight has one `{group}.md` per group on disk. The store
    serves both layouts, so the prompt must not send the model to rewrite work
    that is already done."""
    prompt = _prompt(tmp_path, resume_state=_RESUME_STATE)
    assert "Both count as analysed and neither is\n   rewritten." in prompt
    assert "A group analysed by an earlier run may be one {group}.md file instead of a" in prompt


def test_the_shipped_talend_guide_yields_the_fifteen_evidence_sections(
    tmp_path: Path,
) -> None:
    """The denominator the gate will actually use in production, measured
    against the guide preprocessing really stages."""
    shipped = (
        Path(__file__).resolve().parents[3]
        / "intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md"
    )
    if not shipped.is_file():
        pytest.skip(f"scanner guide not present at {shipped}")
    preprocess = tmp_path / "docs_staging" / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "_analysis_guide.md").write_text(
        shipped.read_text(encoding="utf-8"), encoding="utf-8",
    )
    sections = expected_analysis_sections(tmp_path / "docs_staging", "_analysis_guide.md")
    assert len(sections) == 15, sections
    assert sections[0] == "COMPONENTS"
    assert set(sections) <= set(PHASE2_SECTION_NAMES)


# --------------------------------------------------------------------------
# 3. Coverage replaces length
# --------------------------------------------------------------------------


def test_the_coverage_obligation_is_stated(every_shape: str) -> None:
    assert "COVERAGE OBLIGATION" in every_shape
    assert "ACCOUNT FOR EVERY scanned\n     design in its scope" in every_shape
    assert "named in a row of a table, or named in an\n     explicit omission list" in every_shape


def test_a_listed_omission_is_allowed_and_a_silent_one_is_not(every_shape: str) -> None:
    assert "Deliberate omissions are ACCEPTABLE and must be LISTED." in every_shape
    assert "An explicit, listed omission is fine. A silent one is a defect." in every_shape
    assert "Copy_of_*" in every_shape


def test_the_measured_coverage_failure_is_named(every_shape: str) -> None:
    assert '"All 142 jobs" and named 78 of them' in every_shape


def test_the_anti_padding_rules_are_kept(every_shape: str) -> None:
    """With the table gone these finally mean something."""
    assert "Do NOT pad." in every_shape
    assert "Do NOT truncate to hit a target." in every_shape


def test_the_analysis_size_cap_may_cost_depth_but_never_a_unit(every_shape: str) -> None:
    """The upstream half of the coverage gap: a group analysis that drops jobs to
    fit its byte cap makes every downstream document incomplete."""
    assert "EVERY job in the group must\n      still be named" in every_shape
    assert "shorten the prose, never drop a\n      unit" in every_shape
    assert "prioritize depth over breadth" not in every_shape


def test_the_coverage_obligation_scales_with_the_project(tmp_path: Path) -> None:
    prompt = _prompt(tmp_path, total_jobs=7, total_groups=2)
    assert "This project has 7 scanned designs across 2 groups." in prompt
    # The quick-scan steer must name the real group count, not a hardcoded 20.
    assert "it carries all 2\n      groups' flags" in prompt


# --------------------------------------------------------------------------
# 4. The parallel-read throttle is recovery guidance now
# --------------------------------------------------------------------------


def test_the_parallel_read_cap_is_gone(every_shape: str) -> None:
    for stale in (
        "Never issue more than 3 parallel Read calls",
        "more than 3 parallel",
        "cascade-fail",
    ):
        assert stale not in every_shape, f"read throttle survives: {stale!r}"


def test_a_failed_read_is_retried_not_abandoned(every_shape: str) -> None:
    assert every_shape.count("retry the failed") >= 2
    assert "never a reason to\n     proceed without the evidence" in every_shape
    assert "Read in parallel as widely as" in every_shape


def test_a_large_file_is_walked_to_the_end(every_shape: str) -> None:
    """`head -N` alone reads the first window and drops the rest."""
    assert every_shape.count("successive windows") >= 2
    assert "leaves the rest of the evidence unread" in every_shape


# --------------------------------------------------------------------------
# 5. Grounding without leaking internals
# --------------------------------------------------------------------------


def test_quoted_evidence_must_be_copied_not_reconstructed(every_shape: str) -> None:
    assert "EVIDENCE GROUNDING -- COPY, NEVER RECONSTRUCT:" in every_shape
    assert "COPIED character for character" in every_shape
    assert "COPY, NEVER RECONSTRUCT." in every_shape  # Phase 2, where quotes originate
    assert "If you did\n     not copy it, do not quote it." in every_shape


def test_the_measured_grounding_failure_is_named(every_shape: str) -> None:
    assert "only 16% of the quoted code lines existed verbatim" in every_shape


def test_client_prose_never_names_the_machinery(every_shape: str) -> None:
    assert "CLIENT PROSE -- NAME THE ESTATE, NEVER THE MACHINERY:" in every_shape
    assert "They NEVER name the machinery that produced them." in every_shape
    for phrase in (
        "based on our internal analysis",
        "per the analysis in ...",
        "according to the bundle",
    ):
        assert phrase in every_shape, f"leakage phrase not prohibited: {phrase!r}"
    # The internal file names the writer may not put in a document, enumerated in
    # the prohibition itself -- not merely present somewhere in the prompt.
    assert (
        "the names of internal working files (_analysis, _assessment, _preprocess,\n"
        "     _bundle, _ref, _provenance, quick_scan, docs_staging, scan_result.json)"
    ) in every_shape


def test_provenance_goes_to_a_sidecar_the_reader_never_sees(
    every_shape: str, tmp_path: Path,
) -> None:
    staging = tmp_path / "docs_staging"
    assert f"{staging}/_provenance/{{DOC_NAME}}.json" in every_shape
    assert '"verbatim": true' in every_shape
    assert "A quoted block\n     with no sidecar entry counts as unsourced." in every_shape
    # The sidecar directory must be created in Phase 1, before Phase 3 writes into
    # it (every other staging directory is pre-created by the server)...
    setup = every_shape.split("PHASE 2 -- GROUP ANALYSIS")[0]
    assert f"Bash mkdir -p {staging}/_provenance {staging}/_knowledge" in setup, (
        "both run-record directories must be underscore-prefixed; an unprefixed\n"
        "knowledge/ is published to the client corpus by RagBridge"
    )
    # ...and must survive Phase 4 cleanup.
    assert f"KEEP {staging}/_provenance/" in every_shape.split("PHASE 4 -- CLEANUP")[1]


def test_the_sidecar_declares_the_components_a_claim_rests_on(
    every_shape: str,
) -> None:
    """The one field the attribution guard acts on.

    Without it, verifying a document means running a regex over its prose --
    the same dependency on the model's writing shape that cost run 258 ~95% of
    its evidence at the Phase 2 seam. Declared, it is set membership against
    the evidence of the group the writer itself named.
    """
    assert '"components": ["tMSSqlOutput_1", "tMap_3"]' in every_shape
    assert '"components" lists the component instances the claim rests on' in every_shape
    # Absence must be representable, or the writer pads the field to look
    # compliant and the check verifies invented declarations.
    assert "Use [] when the claim rests on no single component" in every_shape
    # And it must say a wrong declaration is worse than no declaration.
    assert "naming a component you did not read is worse than naming" in every_shape


def test_the_declared_components_come_from_the_declared_group(every_shape: str) -> None:
    """Per-GROUP, not per-run: a component real in a sibling group is exactly
    the misattribution the guard exists to catch, and it passes any check made
    against the combined evidence."""
    assert 'copied from the\n     evidence for that "group"' in every_shape
    assert "each name\n     is looked up in that group's own evidence" in every_shape


def test_prose_citation_is_design_plus_component(every_shape: str) -> None:
    assert "name the DESIGN and the\n     COMPONENT" in every_shape
    assert "never the file you read it from" in every_shape


# --------------------------------------------------------------------------
# 6. Uncertainty has somewhere to go
# --------------------------------------------------------------------------


def test_uncertainty_has_a_ledger(every_shape: str) -> None:
    assert "UNRESOLVED EVIDENCE" in every_shape
    assert "knowledge/open-questions.md" in every_shape
    assert "Would settle it:" in every_shape


def test_the_ledger_is_appended_never_overwritten(every_shape: str) -> None:
    assert "APPEND one entry per open question" in every_shape
    assert "Append with a heredoc, never the Write tool" in every_shape
    assert "a read-modify-write destroys the others'\n     entries" in every_shape


def test_an_honest_gap_in_a_document_is_correct(every_shape: str) -> None:
    assert '"not determinable from the scanned sources" is a CORRECT and wanted' in every_shape
    assert "an honest gap is a correct one" in every_shape


def test_the_ledger_never_ships_as_a_client_document(every_shape: str) -> None:
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
    assert "/_knowledge/" in every_shape, "the ledger must be written behind an underscore"
    # No unprefixed `knowledge` path segment anywhere. Deliberately NOT anchored
    # on a trailing slash: the first version of this check required one, and so
    # missed `mkdir -p {staging}/knowledge` at the end of a line -- the very
    # instruction that created the publishable directory. The publication filter
    # keys on the segment's first character, so match the segment, not the shape.
    offenders = re.findall(r"\S*/(?<!_)knowledge(?:/\S*|\b)", every_shape)
    assert not offenders, (
        f"unprefixed knowledge path(s) would be published to the client: {offenders[:3]}"
    )
    # ...and therefore nothing needs relocating during cleanup.
    cleanup = every_shape.split("PHASE 4 -- CLEANUP")[1]
    assert "mv " not in cleanup, "relocating the ledger at cleanup is a window for losing it"


# --------------------------------------------------------------------------
# 7. Mechanical integrity of the template
# --------------------------------------------------------------------------


def test_prompt_is_ascii(every_shape: str) -> None:
    offenders = sorted({f"U+{ord(c):04X}" for c in every_shape if ord(c) > 127})
    assert not offenders, f"non-ASCII in the prompt: {offenders}"


def test_no_local_variable_is_left_unsubstituted(every_shape: str) -> None:
    """Derived from the function's own locals, so it cannot go out of date."""
    for name in ClaudeCliProvider._build_prompt.__code__.co_varnames:
        assert "{" + name + "}" not in every_shape, f"unsubstituted slot {{{name}}}"


def test_placeholders_addressed_to_the_model_survive_escaping(every_shape: str) -> None:
    """These braces are literal instructions, not slots; over-eager f-string
    cleanup has silently substituted them before."""
    for literal in (
        "{group}", "{agent}", "{timestamp}", "{DOC_NAME}", "{GROUP}", "{SECTION}",
    ):
        assert literal in every_shape, f"literal placeholder {literal} lost"


def test_only_reference_bundles_that_are_staged_are_named(every_shape: str) -> None:
    """services/claude_service.py stages _ref_core, _ref_business_ops, _ref_user."""
    assert "_ref_patterns" not in every_shape
    assert "_ref_{agent}" not in every_shape


def test_a_split_bundle_instruction_reads_both_halves(every_shape: str) -> None:
    """When a bundle splits, _bundle_<agent>.md does not exist at all."""
    assert "if it is absent, read both" in every_shape
    assert "one half is half the estate" in every_shape


# --------------------------------------------------------------------------
# 8. What made the output good is preserved verbatim
# --------------------------------------------------------------------------

_PRESERVED = (
    "Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.",
    "Never summarize when you can enumerate",
    '"7 of 12 DEU jobs have no tLogCatcher" IS acceptable',
    '"Error handling is weak" is NOT acceptable',
    "Show ACTUAL code/expressions/SQL, then explain",
    "PHASE 2.5 -- GATE VALIDATION + INDEX BUILD (before Phase 3):",
)


def test_the_instructions_that_worked_are_untouched(every_shape: str) -> None:
    for text in _PRESERVED:
        assert text in every_shape, f"preserved instruction lost: {text!r}"


def test_the_phase_structure_is_unchanged(every_shape: str) -> None:
    for phase in (
        "PHASE 1 -- SETUP:",
        "PHASE 2 -- GROUP ANALYSIS",
        "PHASE 2.5 -- GATE VALIDATION",
        "PHASE 2.6 --",
        "PHASE 2.8 -- BUNDLE ANALYSIS",
        "PHASE 3 -- WRITE DOCS",
        "PHASE 3.5 -- DOC VALIDATION",
        "PHASE 4 -- CLEANUP & PUBLISH",
    ):
        assert phase in every_shape, f"phase missing: {phase}"


# --------------------------------------------------------------------------
# 9. Both scorecard modes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_both_scorecard_modes_render_with_every_rule(
    tmp_path: Path, monkeypatch, enabled: bool,
) -> None:
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", enabled, raising=False)
    prompt = _prompt(tmp_path)

    assert "{phase26_block}" not in prompt
    if enabled:
        assert "PHASE 2.6 -- SCORE & ASSESS" in prompt
        assert "PHASE 2.6 -- SKIPPED" not in prompt
    else:
        assert "PHASE 2.6 -- SKIPPED" in prompt
        assert "PHASE 2.6 -- SCORE & ASSESS" not in prompt
        assert "Do not invent a number" in prompt

    # The fixes must hold in both modes, not just the default one.
    assert not _BLINDFOLD.search(prompt)
    assert "COVERAGE OBLIGATION" in prompt
    assert "COPIED character for character" in prompt
    assert "UNRESOLVED EVIDENCE" in prompt
    assert prompt.isascii()


def test_the_scorecard_doc_does_not_demand_scores_that_were_not_computed(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", False, raising=False)
    prompt = _prompt(tmp_path)
    assert "If the scores were not computed for this run, say so where a score would go." in prompt
    # And a score citation must point at a published document, not an internal file.
    assert '"Security: 2/5 (see 07_QUALITY_SECURITY_RISK_SCORECARD)"' in prompt
    assert "see Assessment Scorecard for details" not in prompt


# --------------------------------------------------------------------------
# 10. Agent count matches the agent table
# --------------------------------------------------------------------------


@pytest.mark.parametrize("total_jobs", [12, 142, 400])
def test_the_spawn_count_equals_the_number_of_defined_agents(
    tmp_path: Path, total_jobs: int,
) -> None:
    prompt = _prompt(tmp_path, total_jobs=total_jobs)
    spawn = re.search(r"spawn (\d+) doc-assembly sub-agents", prompt)
    assert spawn, "the spawn instruction is gone"
    rows = [
        line for line in prompt.splitlines()
        if line.strip().startswith("| ") and "_analysis/_bundle_" in line
    ]
    assert int(spawn.group(1)) == len(rows), (
        f"prompt asks for {spawn.group(1)} agents and defines {len(rows)}"
    )
    retry = re.search(r"was responsible \(([^)]+)\)", prompt)
    assert retry and len(retry.group(1).split(",")) == len(rows)


# --------------------------------------------------------------------------
# 9. The depth floor is CALIBRATED, not aspirational
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
