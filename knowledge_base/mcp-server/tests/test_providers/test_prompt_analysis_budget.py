"""Phase 2 has no analysis byte budget, and the method says why.

Measured on a production run of ``talend8_main`` (24 groups, 8.3 MB of
evidence).  ``_build_prompt`` computed::

    analysis_target_kb = max(15, min(60, total_preprocess_bytes // 1024))

from the PROJECT's total evidence, then stated it PER GROUP, so every group on
the estate was handed the same 60 KB ceiling regardless of its own evidence:

    group                          its evidence     cap     = share of its evidence
    Global                                6 KB     60 KB        954%   (never binds)
    PPDWH                             2,013 KB     60 KB          3.0%
    Pluripharm_Data_Integratie        2,569 KB     60 KB          2.3%

The analysis-to-evidence ratio on that run ran 114-155% for small groups (an
analysis longer than its evidence -- healthy interpretation), 23-64% for medium
ones, and 2.2-7.5% for large ones.  The four largest evidence files were 54% of
the estate and produced 101 KB of analysis between them, and the published
``01_ARCHITECTURE_MAP`` reported "detailed analysis is incomplete" against both
50-design groups.

The cap was deleted rather than recalibrated, because the number it was trying
to be is not one this side can compute.  In its place the METHOD states the
INTENT that governs the two phases -- Phase 2 is a RETRIEVAL CORPUS (nobody
reads it end to end, a retriever does, so capping it is strictly wrong and depth
scales with the evidence the agent is holding), Phase 3 is the HUMAN DELIVERABLE
(selective, narrative, and the only place editorial omission belongs).

WHERE THESE ASSERTIONS LIVE NOW.  The intent moved out of the prompt and into
``data/generation_method/METHOD.md`` when the prompt collapsed from ~47 KB to a
run brief.  The rules did not change, so the tests did not go away -- they read
the method instead, through ``method_source``.  What is still asserted against
the RENDERED PROMPT is what the prompt still owns: that no number in it is
derived from evidence volume, which is the root cause the deleted formula had.

``opencode_cli._build_prompt`` returns this prompt verbatim, so every assertion
here covers both lanes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider

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


def _stage_evidence(tmp_path: Path, *, kb_per_group: int, groups: int = 2) -> None:
    """Stage ``_preprocess/{group}.md`` files of a chosen size.

    These are exactly the files the deleted formula summed.  Nothing in the
    prompt may vary with them.
    """
    preprocess = tmp_path / "docs_staging" / "_preprocess"
    preprocess.mkdir(parents=True, exist_ok=True)
    for i in range(groups):
        (preprocess / f"group{i}.md").write_text("x" * (kb_per_group * 1024), encoding="utf-8")


#: The prompt's shapes.  A budget rule that holds in one and not the others is
#: not a rule -- the measured failure shipped from the default (fresh) shape.
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

_SHAPES: dict[str, dict] = {
    "small_fresh": {},
    "large_fresh": {"total_jobs": 400},
    "small_resume": {"resume_state": _RESUME_STATE},
    "large_resume": {"total_jobs": 400, "resume_state": _RESUME_STATE},
    "tiny_estate": {"total_jobs": 7, "total_groups": 2},
}


@pytest.fixture(params=sorted(_SHAPES), ids=sorted(_SHAPES))
def every_shape(request, tmp_path: Path) -> str:
    return _prompt(tmp_path, **_SHAPES[request.param])


# --------------------------------------------------------------------------
# 1. No per-group KB maximum, in the prompt or in the method
# --------------------------------------------------------------------------

#: The literal line the run shipped with, rendered at its two clamp bounds and
#: at a value in between.  Kept as fixtures so the regexes below cannot rot
#: into something that matches nothing.
_HISTORICAL_CAP_LINES = (
    "      (MAXIMUM: 15KB for the group's section files TOGETHER.)",
    "      (MAXIMUM: 42KB for the group's section files TOGETHER.)",
    "      (MAXIMUM: 60KB for the group's section files TOGETHER.)",
)

#: A ceiling on OUTPUT VOLUME: a maximum/cap/budget/limit quantified in KB, MB,
#: bytes, lines or words.  Deliberately not anchored on the word "MAXIMUM" --
#: the defect is the instrument, not the spelling.
_OUTPUT_CEILING = re.compile(
    r"(?:MAXIMUM|MAX|no more than|at most|cap(?:ped)? (?:at|to)|"
    r"budget of|limit of|keep (?:it |them |this )?under|stay under)"
    r"[^.\n]{0,40}?\b\d[\d,]*\s*(?:KB|MB|kilobytes|megabytes|bytes|lines|words)\b",
    re.IGNORECASE,
)


def test_the_cap_regex_is_not_vacuous() -> None:
    """Guard the guard."""
    for line in _HISTORICAL_CAP_LINES:
        assert _OUTPUT_CEILING.search(line), f"regex no longer detects the cap: {line!r}"


def _phase2_block(text: str) -> str:
    """The method's Phase 2 instructions, header to the next phase."""
    after = text.split("## 5. Phase 2 -- group analysis")
    assert len(after) == 2, "the Phase 2 header moved or is duplicated"
    return after[1].split("## 5b.")[0]


def _analysis_output_ceilings(block: str) -> list[str]:
    """Quantified output ceilings in Phase 2 that bound the ANALYSIS.

    Bounds on the two NAVIGATION artifacts are excluded by name, not by luck:
    ``{group}_summary.md`` ("max 50 lines") and the orchestrator return value
    ("~2KB") exist to be skimmed, are explicitly not the evidence of record,
    and keeping them short is their function. A ceiling on the analysis itself
    is the defect.
    """
    hits = []
    for match in _OUTPUT_CEILING.finditer(block):
        line_start = block.rfind("\n", 0, match.start()) + 1
        line_end = block.find("\n", match.end())
        line = block[line_start : line_end if line_end != -1 else len(block)]
        if "summary" in line.lower() or "navigation" in line.lower():
            continue
        hits.append(line.strip())
    return hits


def test_the_ceiling_scan_still_sees_a_cap_on_an_analysis() -> None:
    """Guard the exemption: excluding the navigation aids must not excuse a cap
    on the analysis itself."""
    assert _analysis_output_ceilings(_HISTORICAL_CAP_LINES[2])
    assert not _analysis_output_ceilings(
        "f. Write a concise summary (max 50 lines) to _analysis/{group}_summary.md"
    )


def test_the_phase_2_analysis_size_cap_is_gone_from_the_method() -> None:
    hits = _analysis_output_ceilings(_phase2_block(method_text()))
    assert not hits, f"Phase 2 still carries an output ceiling: {hits}"


def test_the_prompt_carries_no_output_ceiling_at_all(every_shape: str) -> None:
    """The run brief states facts. A ceiling is not a fact about this estate."""
    assert not _OUTPUT_CEILING.search(every_shape)


def test_the_exact_cap_sentence_is_gone(every_shape: str) -> None:
    """The defect stated literally, so a re-introduction in any other clamp
    value still fails here -- in either place it could reappear."""
    for text in (every_shape, method_text()):
        assert "for the group's section files TOGETHER" not in text
        assert not re.search(r"MAXIMUM:\s*\d+\s*KB", text)


def test_no_local_named_analysis_target_survives() -> None:
    """The variable itself, not just its rendering.  A live local with no call
    site is the state this file exists to prevent returning to."""
    locals_ = ClaudeCliProvider._build_prompt.__code__.co_varnames
    assert "analysis_target_kb" not in locals_
    assert "total_preprocess_bytes" not in locals_


@pytest.mark.parametrize("kb_per_group", [1, 6, 2013, 2569])
def test_the_prompt_does_not_move_with_the_estates_evidence_size(
    tmp_path: Path, kb_per_group: int,
) -> None:
    """The root cause, pinned.

    The deleted formula summed ``_preprocess/*.md`` -- a PROJECT total -- and
    stated the result as a PER GROUP ceiling.  Staging 6 KB (``Global``) or
    2,569 KB (``Pluripharm_Data_Integratie``) must produce byte-identical
    prompts: nothing in the brief is derived from evidence volume, so there is
    no project-total number left to misapply per group.
    """
    baseline = _prompt(tmp_path)
    _stage_evidence(tmp_path, kb_per_group=kb_per_group)
    assert _prompt(tmp_path) == baseline


def test_no_clamp_bound_of_the_old_formula_is_stated_as_a_target() -> None:
    """``max(15, min(60, ...))``: 15 and 60 KB were the only two values the
    cap ever really took.  Neither may reappear as a Phase 2 output target.

    The 60 KB that used to be allowed to remain was the Phase 3 bundle, a
    Python-produced extract sized for one agent's context.  That bundler
    (``scripts/bundle_analysis.py``) is deleted, so the exemption is gone with
    it and the check is now unconditional.
    """
    block = _phase2_block(method_text())
    for bound in ("15KB", "15 KB", "60KB", "60 KB"):
        assert bound not in block, f"old clamp bound {bound!r} survives in Phase 2"


# --------------------------------------------------------------------------
# 2. Intent replaced the number
# --------------------------------------------------------------------------


def test_phase_2_is_declared_a_retrieval_corpus() -> None:
    block = squash(_phase2_block(method_text()))
    assert squash("PHASE 2 IS FOR THE RETRIEVAL CORPUS. PHASE 3 IS FOR THE READER.") in block
    assert squash("Nobody reads these analyses end to end.") in block
    assert squash("A retriever does, one passage at a time") in block
    assert squash("no byte budget to stay under") in block


def test_phase_2_is_told_to_capture_every_category_of_evidence() -> None:
    """Exhaustive is the instruction, and it is enumerated rather than
    asserted -- "be thorough" is what the cap was competing with before."""
    assert not states(
        "name every design, record every business rule, copy every SQL statement"
        " and every custom-code body verbatim, and list every context variable"
        " and every schema column.",
        "this is the only place it was ever written down",
    )


def test_depth_is_delegated_to_the_agent_holding_the_evidence() -> None:
    """The replacement for the formula: a judgement, made where the evidence
    is, not a constant computed where it is not."""
    assert not states(
        "Depth scales with the evidence, and you are the one holding it.",
        "A group carrying two megabytes of evidence warrants far more analysis"
        " than one carrying six kilobytes",
        "writing them to the same length is the failure",
    )


def test_editorial_omission_is_routed_to_phase_3_not_phase_2() -> None:
    assert not states(
        "Deciding what to leave out is editorial judgement, it belongs in"
        " Phase 3 where a person reads the result, and it does not belong here.",
    )


def test_phase_3_carries_the_human_deliverable_counterpart() -> None:
    """Both halves of the distinction must be stated, and the Phase 3 half must
    sit inside COVERAGE OBLIGATION -- that is one of the blocks the orchestrator
    is told to copy VERBATIM into every doc sub-agent prompt, so it is the only
    placement that reaches the agents that actually write the documents."""
    text = method_text()
    assert "PHASE 3 IS FOR THE READER. PHASE 2 WAS FOR THE RETRIEVAL CORPUS." in text
    coverage = squash(
        text.split("### COVERAGE OBLIGATION")[1].split("### DEPTH FLOOR")[0]
    )
    assert "PHASE 3 IS FOR THE READER." in coverage
    assert "The group analyses behind you are exhaustive on purpose" in coverage
    assert "A published document IS read end to end, by a person" in coverage
    assert squash(
        "selection, narrative and structure are your job and they are your job"
        " only here"
    ) in coverage
    assert "it is yours now" in coverage


def test_the_copy_verbatim_instruction_still_names_coverage_obligation() -> None:
    """Pins the delivery mechanism the test above depends on."""
    assert not states(
        "Copy the four blocks in this section -- COVERAGE OBLIGATION, EVIDENCE"
        " GROUNDING, CLIENT PROSE and UNRESOLVED EVIDENCE",
        "VERBATIM into every doc sub-agent prompt",
    )


def test_the_distinction_is_stated_once_per_phase_not_scattered() -> None:
    """Prompts are prefix-cached on their bytes and read by a model that treats
    repetition as emphasis. One statement per phase."""
    body = method()
    assert body.count("PHASE 2 IS FOR THE RETRIEVAL CORPUS.") == 1
    assert body.count("PHASE 3 IS FOR THE READER. PHASE 2 WAS FOR") == 1


# --------------------------------------------------------------------------
# 3. The honesty rule is not a size rule, and survives
# --------------------------------------------------------------------------


def test_a_group_that_cannot_be_covered_still_names_every_unit() -> None:
    """This rule was attached to the cap sentence and had to survive its
    deletion: it is about NOT LYING by omission, not about length."""
    block = squash(_phase2_block(method_text()))
    assert "EVERY job in the group must still be named" in block
    assert "with its components and its role" in block
    assert "shorten the prose, never drop a unit" in block


def test_the_shortfall_is_reported_rather_than_hidden() -> None:
    block = squash(_phase2_block(method_text()))
    assert "A unit missing here is a unit missing from every document downstream." in block
    assert "Record the depth you had to sacrifice in the open-questions ledger." in block


def test_the_honesty_rule_no_longer_depends_on_a_cap_binding() -> None:
    """It used to read "If that cap binds, ...", which with no cap would leave
    it unreachable -- the rule would have been deleted in effect while still
    appearing in the instructions."""
    block = squash(_phase2_block(method_text()))
    assert "If that cap binds" not in block
    assert "If you still cannot cover a group fully" in block


def test_the_empty_section_rule_is_untouched() -> None:
    """The other absence rule in the same paragraph. A short section means
    somebody looked; a missing one means nobody did.  The layout changed (one
    file per group with a heading per section, not one file per section), so
    the noun changed with it -- the distinction did not."""
    block = squash(_phase2_block(method_text()))
    assert "A SECTION YOU FOUND NOTHING FOR STILL GETS ITS HEADING" in block
    assert "A MISSING HEADING MEANS NOBODY LOOKED" in block


def test_a_bare_none_is_named_as_not_an_honest_absence() -> None:
    """The degenerate form the rule has to exclude by name: a five-byte section
    containing the word "None" records no search, supports no confidence grade
    and is indistinguishable from a worker that never looked."""
    block = squash(_phase2_block(method_text()))
    assert squash(
        'A heading followed by the single word "None" is NOT that.'
    ) in block
    assert "It records no search, supports no grade" in block
    # ...and the worked example of the good form is concrete, not abstract.
    assert "No SQL in this group: none of the 14 designs uses tDBInput" in block


def test_the_exhaustiveness_instruction_that_predates_this_change_survives() -> None:
    assert not states(
        "Be EXHAUSTIVE. Include every expression, every rule, every column."
        " Depth over brevity.",
    )


# --------------------------------------------------------------------------
# 4. What is NOT a ceiling on analysis volume, and must stay
# --------------------------------------------------------------------------
#
# The bound that was removed sized TOTAL OUTPUT for a phase. Bounds that size a
# single model CALL are context-window safety and are real; so is a bound on a
# navigation aid, whose whole purpose is to be short enough to skim.


def test_the_group_analysis_is_still_named_the_evidence_of_record() -> None:
    """The Phase 3 bundle is gone with its bundler, so the sentence that sent a
    writer past the extract to the real evidence now points at the summary --
    the only remaining artifact that could be mistaken for the analysis."""
    assert not states(
        "THE SUMMARY IS A NAVIGATION AID, NOT THE EVIDENCE OF RECORD.",
        "The group analyses are the evidence of record, and they are several"
        " times larger.",
    )


def test_the_navigation_summaries_are_still_bounded() -> None:
    """``{group}_summary.md`` and the orchestrator return values are navigation
    aids, explicitly not the evidence of record. Short is their function."""
    assert not states(
        "Write a concise summary (max 50 lines)",
        "it does not replace the analysis as the evidence of record",
        "Return a ~2KB navigation summary to the orchestrator",
    )


def test_the_groups_per_worker_cap_survives() -> None:
    """A context-window bound on one worker, and the guard against writing one
    group's components into another's analysis. Not an output ceiling."""
    assert not states(
        "caps how many groups sit in one worker's context",
        "do not exceed the group-per-worker cap the plan states",
    )


def test_the_phase_3_depth_floor_is_a_floor_and_not_a_ceiling(
    every_shape: str,
) -> None:
    """The one quantified length signal left points the other way, and says so.

    It is split across the two artifacts on purpose: the NUMBERS scale with
    this estate and are therefore per-run fact in the prompt, while how to read
    them has never varied and is method.
    """
    assert "DETECTOR of missed evidence, never a quota" in every_shape
    assert not states(
        "DEPTH FLOOR -- A SHORT DOCUMENT MEANS EVIDENCE WAS MISSED",
        "Read the floor as a DETECTOR, not as a quota.",
        "A floor is not a ceiling.",
        "Do NOT pad, and Do NOT truncate to hit a target.",
    )


# --------------------------------------------------------------------------
# 5. Mechanical integrity
# --------------------------------------------------------------------------


def test_prompt_is_ascii(every_shape: str) -> None:
    """Generated text reaches Windows cp1252 consumers; the repo rule is ASCII
    everywhere. Matches the assertion in ``test_claude_cli_prompt.py``."""
    offenders = sorted({f"U+{ord(c):04X}" for c in every_shape if ord(c) > 127})
    assert not offenders, f"non-ASCII in the prompt: {offenders}"


def test_the_method_is_ascii() -> None:
    offenders = sorted({f"U+{ord(c):04X}" for c in method_text() if ord(c) > 127})
    assert not offenders, f"non-ASCII in METHOD.md: {offenders}"


def test_no_local_variable_is_left_unsubstituted(every_shape: str) -> None:
    for name in ClaudeCliProvider._build_prompt.__code__.co_varnames:
        assert "{" + name + "}" not in every_shape, f"unsubstituted slot {{{name}}}"


def test_the_prompt_is_byte_stable_across_builds(tmp_path: Path) -> None:
    """Prefix caching keys on the bytes. The brief carries no set iteration
    and no timestamp."""
    assert _prompt(tmp_path) == _prompt(tmp_path)


@pytest.mark.parametrize("enabled", [True, False])
def test_the_rules_hold_in_both_scorecard_modes(
    tmp_path: Path, monkeypatch, enabled: bool,
) -> None:
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", enabled, raising=False)
    prompt = _prompt(tmp_path)
    assert not _OUTPUT_CEILING.search(prompt)
    assert prompt.isascii()
    # The method does not vary with a run switch at all -- that is the point of
    # it being a file.
    assert "PHASE 2 IS FOR THE RETRIEVAL CORPUS. PHASE 3 IS FOR THE READER." in method_text()
    assert "PHASE 3 IS FOR THE READER. PHASE 2 WAS FOR THE RETRIEVAL CORPUS." in method_text()
    assert "EVERY job in the group must still be named" in method()
