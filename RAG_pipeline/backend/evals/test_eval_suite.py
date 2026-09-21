"""Tests for the eval harness itself.

Read the distinction carefully: these tests do not measure the RAG system. They
measure the *scorer*, by running it against three hand-built controls:

* ``reference_ideal.json``  -- correct answers. Must score 100%. Guards against a
  scorer that has drifted stricter than correct.
* ``observed_baseline.json`` -- answers exhibiting the four measured failure
  modes. Each assertion below names the mode it pins.
* ``always_declines.json``  -- the adversarial control. A uniform "I don't know".
  Must show perfect abstention recall *and* 100% over-abstention, which is the
  whole argument for reporting abstention as two numbers.

Running the suite against the real system is a separate, opt-in activity
(``python -m evals run --live``); it needs a deployed stack and is not a unit
test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.answers import FixtureProvider, RecordedAnswer
from evals.cases import EvalCase, default_suite_path, load_eval_set
from evals.judge import HeuristicJudge, get_judge
from evals.runner import Runner
from evals.scoring import (
    detect_abstention,
    detect_unqualified_absence,
    normalize,
    number_aliases,
    score_case,
    states_number,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def eval_set():
    return load_eval_set(default_suite_path())


def _run(eval_set, fixture_name: str):
    provider = FixtureProvider.from_file(FIXTURES / fixture_name)
    return Runner(eval_set=eval_set, provider=provider).run()


# ---------------------------------------------------------------------------
# Suite integrity
# ---------------------------------------------------------------------------


def test_suite_has_enough_cases_and_a_real_unanswerable_class(eval_set) -> None:
    assert len(eval_set.cases) >= 10
    # The point of the harness. Without unanswerable cases the suite cannot
    # measure the failure that actually reached a customer.
    assert len(eval_set.unanswerable_cases) >= 2
    assert len(eval_set.answerable_cases) >= 5


def test_every_evidence_path_still_exists(eval_set) -> None:
    missing = [
        f"{case.id}: {ref.path}"
        for case in eval_set.cases
        for ref in case.evidence
        if not (REPO_ROOT / ref.path).exists()
    ]
    assert not missing, f"evidence files moved or were deleted: {missing}"


def test_unanswerable_cases_point_at_out_of_corpus_evidence(eval_set) -> None:
    for case in eval_set.unanswerable_cases:
        assert any(not ref.in_corpus for ref in case.evidence), (
            f"{case.id} is marked unanswerable but every evidence ref claims to be "
            "in the corpus -- one of the two is wrong"
        )


# ---------------------------------------------------------------------------
# Control 1: correct answers must pass
# ---------------------------------------------------------------------------


def test_reference_ideal_scores_a_clean_sheet(eval_set) -> None:
    result = _run(eval_set, "reference_ideal.json")
    assert result.pass_rate == 1.0, "\n".join(
        f"{r.case.id}: {r.score.failures}" for r in result.failing
    )
    assert result.metric("abstention_recall").value == 1.0
    assert result.metric("over_abstention_rate").value == 0.0
    assert result.metric("coverage").value == 1.0
    assert result.metric("count_accuracy").value == 1.0
    assert result.metric("contradiction_disclosure").value == 1.0


def test_reference_ideal_is_not_reported_as_a_measurement(eval_set) -> None:
    from evals.report import render_text

    result = _run(eval_set, "reference_ideal.json")
    assert result.measured_fraction == 0.0
    assert "NOT A MEASUREMENT" in render_text(result)


# ---------------------------------------------------------------------------
# Control 2: each measured failure mode is detected
# ---------------------------------------------------------------------------


def test_baseline_detects_silent_partial_coverage(eval_set) -> None:
    result = _run(eval_set, "observed_baseline.json")
    score = next(r.score for r in result.results if r.case.id == "vw-maximo-workorder-consumers")
    assert score.coverage is not None
    assert score.coverage.found == ("4PS", "Ortec", "Quintiq", "AssetCheck", "Infor LN")
    assert score.coverage.undisclosed_partial is True
    assert not score.passed


def test_baseline_detects_fabricated_absence(eval_set) -> None:
    result = _run(eval_set, "observed_baseline.json")
    score = next(
        r.score for r in result.results if r.case.id == "pp-fmd-customer-selection-rule"
    )
    # The decisive property: it reported absence-from-corpus as absence-in-reality.
    assert score.unqualified_absence
    assert score.abstention_correct is False
    assert score.must_not_claim_violations
    assert score.missing_disclosures


def test_baseline_detects_table_truncation(eval_set) -> None:
    result = _run(eval_set, "observed_baseline.json")
    score = next(r.score for r in result.results if r.case.id == "vw-fo41-target-field-count")
    assert score.counts_correct is False
    assert any("31" in w for w in score.wrong_counts)
    # And the invented number is caught independently by numeric grounding: the
    # retrieved chunks stop at row 22, so "30" is not in the context.
    assert 30 in score.groundedness.ungrounded


def test_baseline_detects_silent_contradiction_resolution(eval_set) -> None:
    result = _run(eval_set, "observed_baseline.json")
    for case_id in ("vw-fo41-field-count-contradiction", "vw-klantorder-source-contradiction"):
        score = next(r.score for r in result.results if r.case.id == case_id)
        assert score.contradiction_disclosed is False, case_id
        assert score.missing_contradiction_sides, case_id


def test_baseline_detects_retrieval_misses(eval_set) -> None:
    result = _run(eval_set, "observed_baseline.json")
    hit_rate = result.metric("retrieval_hit_rate")
    assert hit_rate.defined
    assert hit_rate.value < 1.0


# ---------------------------------------------------------------------------
# Control 3: "I don't know" must not be a winning strategy
# ---------------------------------------------------------------------------


def test_always_declining_scores_perfect_abstention_and_still_fails(eval_set) -> None:
    result = _run(eval_set, "always_declines.json")
    assert result.metric("abstention_recall").value == 1.0
    assert result.metric("over_abstention_rate").value == 1.0
    assert result.pass_rate == 0.0


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_form"),
    [(3, "drie"), (9, "negen"), (11, "elf"), (30, "dertig"), (31, "eenendertig"), (61, "eenenzestig")],
)
def test_dutch_number_words(value: int, expected_form: str) -> None:
    assert expected_form in number_aliases(value)
    assert states_number(normalize(f"er zijn {expected_form} velden"), value)


def test_digits_and_words_score_identically() -> None:
    assert states_number(normalize("31 doelvelden"), 31)
    assert states_number(normalize("eenendertig doelvelden"), 31)
    assert not states_number(normalize("131 doelvelden"), 31)


def test_scope_qualifier_separates_honest_from_fabricated_absence() -> None:
    fabricated = normalize("Er is geen filter op klantniveau.")
    honest = normalize("In de aangeleverde documentatie is geen filter beschreven.")
    assert detect_unqualified_absence(fabricated)
    assert not detect_unqualified_absence(honest)


def test_negated_absence_is_not_an_absence_claim() -> None:
    text = normalize("Daaruit volgt niet dat er geen selectie plaatsvindt.")
    assert not detect_unqualified_absence(text)


def test_absence_scoping_is_sentence_local() -> None:
    # A qualifier in a later sentence must not launder an earlier bare claim --
    # the bare sentence is the one a reader quotes back to the business.
    text = normalize(
        "Er is geen filter. Overigens gaat dit alleen over de aangeleverde documentatie."
    )
    assert detect_unqualified_absence(text)


def test_hedging_inside_a_substantive_answer_is_not_over_abstention(eval_set) -> None:
    case = eval_set.by_id("vw-klantorder-source-contradiction")
    assert case is not None
    recorded = RecordedAnswer(
        case_id=case.id,
        answer=(
            "De omgevingsgebonden kopieen lezen WOLO9; een tweede kopie leest WO2. "
            "Welke kopie draait is van buiten niet te bepalen -- open vraag."
        ),
    )
    score = score_case(case, recorded)
    assert score.abstained is True  # it did hedge
    assert score.over_abstained is False  # but it also answered


def test_bare_refusal_on_an_answerable_case_is_over_abstention() -> None:
    case = EvalCase(
        id="synthetic",
        question="hoeveel velden?",
        project="X",
        answer_type="count",
        answerable=True,
        expected_counts={"fields": 31},
    )
    score = score_case(case, RecordedAnswer(case_id="synthetic", answer="Dat kan ik niet vaststellen."))
    assert score.over_abstained is True


def test_abstention_markers_cover_both_languages() -> None:
    assert detect_abstention(normalize("I cannot determine this from the documents."))
    assert detect_abstention(normalize("Dat is niet vastgesteld."))
    assert not detect_abstention(normalize("De keten telt 31 doelvelden."))


def test_undefined_metrics_are_excluded_not_zeroed(eval_set) -> None:
    """A fixture without recorded sources must not report 0% retrieval."""
    provider = FixtureProvider(
        answers={
            case.id: RecordedAnswer(case_id=case.id, answer="x", sources_captured=False)
            for case in eval_set.cases
        }
    )
    result = Runner(eval_set=eval_set, provider=provider).run()
    assert result.metric("retrieval_hit_rate").defined is False


def test_missing_fixture_answer_is_an_error_not_a_free_pass(eval_set) -> None:
    provider = FixtureProvider(answers={})
    with pytest.raises(KeyError):
        Runner(eval_set=eval_set, provider=provider).run()


# ---------------------------------------------------------------------------
# Judge isolation
# ---------------------------------------------------------------------------


def test_heuristic_judge_runs_without_credentials(eval_set) -> None:
    provider = FixtureProvider.from_file(FIXTURES / "reference_ideal.json")
    result = Runner(eval_set=eval_set, provider=provider, judge=HeuristicJudge()).run()
    assert result.judge_deterministic is True
    assert result.metric("groundedness_judged").defined
    # The judge never changes the deterministic gate.
    assert result.pass_rate == 1.0


def test_get_judge_never_returns_an_unusable_judge() -> None:
    judge = get_judge("auto")
    assert judge.available is True


@pytest.mark.eval_live
def test_live_run_is_opt_in() -> None:
    pytest.skip("live runs require a deployed stack; use `python -m evals run --live`")
