"""Orchestration and aggregation.

Every metric is reported with its own denominator. That is the single most
important thing in this file: metrics here are defined on different subsets
(coverage only on enumeration cases, abstention on two disjoint subsets,
groundedness only where context was captured), and averaging an undefined case
in as a 0 or a 1 is how an eval report starts lying.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from evals.answers import AnswerProvider, RecordedAnswer
from evals.cases import EvalCase, EvalSet
from evals.judge import Judge, JudgeVerdict
from evals.scoring import CaseScore, score_case


@dataclass(frozen=True)
class Metric:
    """A ratio with its denominator attached, or an explicit 'not measured'."""

    key: str
    label: str
    numerator: float
    denominator: int
    description: str
    higher_is_better: bool = True

    @property
    def defined(self) -> bool:
        return self.denominator > 0

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    recorded: RecordedAnswer
    score: CaseScore
    verdict: JudgeVerdict | None = None


@dataclass(frozen=True)
class RunResult:
    suite_name: str
    suite_version: str
    provider_name: str
    judge_name: str
    judge_deterministic: bool
    results: tuple[CaseResult, ...]
    metrics: tuple[Metric, ...]
    provenance: dict[str, int]
    duration_s: float
    measured_fraction: float = 0.0

    @property
    def failing(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if not r.score.passed)

    @property
    def pass_rate(self) -> float:
        return 0.0 if not self.results else 1 - len(self.failing) / len(self.results)

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)


def _ratio(values: Iterable[bool | None]) -> tuple[float, int]:
    """Count True over the defined subset. ``None`` is excluded, not zeroed."""
    defined = [v for v in values if v is not None]
    return float(sum(1 for v in defined if v)), len(defined)


def _mean(values: Iterable[float | None]) -> tuple[float, int]:
    defined = [v for v in values if v is not None]
    return float(sum(defined)), len(defined)


def aggregate(results: Iterable[CaseResult]) -> tuple[Metric, ...]:
    results = tuple(results)
    scores = [r.score for r in results]
    answerable = [s for s in scores if s.answerable]
    unanswerable = [s for s in scores if not s.answerable]

    metrics: list[Metric] = []

    # --- retrieval ---------------------------------------------------------
    num, den = _ratio(s.retrieval_hit for s in answerable)
    metrics.append(
        Metric(
            key="retrieval_hit_rate",
            label="Retrieval hit-rate",
            numerator=num,
            denominator=den,
            description=(
                "Answerable cases whose declared evidence file appears among the "
                "retrieved sources. Undefined when the provider reports no sources."
            ),
        )
    )

    # --- groundedness ------------------------------------------------------
    total, den = _mean(s.groundedness.numeric_ratio for s in scores)
    metrics.append(
        Metric(
            key="groundedness_numeric",
            label="Groundedness (numeric, deterministic)",
            numerator=total,
            denominator=den,
            description=(
                "Fraction of integers stated in the answer that also occur in the "
                "retrieved text. Derived numbers (percentages, sums) score as "
                "ungrounded by construction -- signal, not a gate."
            ),
        )
    )

    total, den = _mean(s.groundedness.citation_ratio for s in scores)
    metrics.append(
        Metric(
            key="citation_validity",
            label="Citation validity (deterministic)",
            numerator=total,
            denominator=den,
            description="Inline [N] markers that resolve to a retrieved source.",
        )
    )

    total, den = _mean(
        r.verdict.groundedness for r in results if r.verdict is not None
    )
    metrics.append(
        Metric(
            key="groundedness_judged",
            label="Groundedness (semantic, judge)",
            numerator=total,
            denominator=den,
            description="Judge-scored claim support. Needs a judge; not a gate.",
        )
    )

    total, den = _mean(r.verdict.correctness for r in results if r.verdict is not None)
    metrics.append(
        Metric(
            key="correctness_judged",
            label="Correctness vs ground truth (judge)",
            numerator=total,
            denominator=den,
            description="Judge-scored agreement with the case's ground truth.",
        )
    )

    # --- abstention: deliberately two numbers ------------------------------
    num, den = _ratio(s.abstention_correct for s in unanswerable)
    metrics.append(
        Metric(
            key="abstention_recall",
            label="Abstention correctness (unanswerable)",
            numerator=num,
            denominator=den,
            description=(
                "Unanswerable cases where the system declined AND did not report "
                "absence-from-corpus as absence-in-reality."
            ),
        )
    )

    num, den = _ratio(s.over_abstained for s in answerable)
    metrics.append(
        Metric(
            key="over_abstention_rate",
            label="Over-abstention (answerable)",
            numerator=num,
            denominator=den,
            description=(
                "Answerable cases the system declined anyway. Reported separately "
                "so a system that always says 'I don't know' cannot score well."
            ),
            higher_is_better=False,
        )
    )

    # --- coverage ----------------------------------------------------------
    total, den = _mean(s.coverage.ratio if s.coverage else None for s in scores)
    metrics.append(
        Metric(
            key="coverage",
            label="Coverage (K of N entities)",
            numerator=total,
            denominator=den,
            description="Mean fraction of the expected entities the answer names.",
        )
    )

    num, den = _ratio(
        s.coverage.undisclosed_partial if s.coverage else None for s in scores
    )
    metrics.append(
        Metric(
            key="undisclosed_partial_rate",
            label="Undisclosed partial coverage",
            numerator=num,
            denominator=den,
            description=(
                "Enumeration cases answered incompletely with no signal that the "
                "list was incomplete."
            ),
            higher_is_better=False,
        )
    )

    # --- numeric claims and contradictions ---------------------------------
    num, den = _ratio(s.counts_correct for s in scores)
    metrics.append(
        Metric(
            key="count_accuracy",
            label="Count accuracy",
            numerator=num,
            denominator=den,
            description=(
                "Cases stating every expected count and no declared near-miss. "
                "This is the table-truncation detector."
            ),
        )
    )

    num, den = _ratio(s.contradiction_disclosed for s in scores)
    metrics.append(
        Metric(
            key="contradiction_disclosure",
            label="Contradiction disclosure",
            numerator=num,
            denominator=den,
            description=(
                "Cases where the corpus disagrees with itself and the answer "
                "surfaced every side instead of silently picking one."
            ),
        )
    )

    num, den = _ratio(bool(s.must_not_claim_violations) for s in scores)
    metrics.append(
        Metric(
            key="must_not_claim_violation_rate",
            label="must_not_claim violations",
            numerator=num,
            denominator=den,
            description="Cases matching at least one forbidden assertion.",
            higher_is_better=False,
        )
    )

    num, den = _ratio(s.passed for s in scores)
    metrics.append(
        Metric(
            key="pass_rate",
            label="Case pass-rate (deterministic gate)",
            numerator=num,
            denominator=den,
            description="Cases with zero deterministic failures. This is the gate.",
        )
    )

    return tuple(metrics)


@dataclass
class Runner:
    eval_set: EvalSet
    provider: AnswerProvider
    judge: Judge | None = None
    # Live providers hit a 30 req/min rate limit on /api/v1/query.
    throttle_s: float = 0.0
    progress: Callable[[str], None] | None = None
    _answers: list[RecordedAnswer] = field(default_factory=list, repr=False)

    def run(self, case_ids: Iterable[str] | None = None) -> RunResult:
        started = time.perf_counter()
        selected = (
            self.eval_set.cases
            if case_ids is None
            else tuple(c for c in self.eval_set.cases if c.id in set(case_ids))
        )
        if not selected:
            raise ValueError("no cases selected")

        results: list[CaseResult] = []
        provenance: dict[str, int] = {}

        for index, case in enumerate(selected):
            if self.progress:
                self.progress(f"[{index + 1}/{len(selected)}] {case.id}")
            recorded = self.provider.answer(case)
            self._answers.append(recorded)
            provenance[recorded.provenance] = provenance.get(recorded.provenance, 0) + 1

            score = score_case(case, recorded)
            verdict = self.judge.judge(case, recorded) if self.judge else None
            results.append(CaseResult(case=case, recorded=recorded, score=score, verdict=verdict))

            if self.throttle_s and index < len(selected) - 1:
                time.sleep(self.throttle_s)

        measured = sum(1 for r in results if r.recorded.is_measurement)
        return RunResult(
            suite_name=self.eval_set.name,
            suite_version=self.eval_set.version,
            provider_name=getattr(self.provider, "name", type(self.provider).__name__),
            judge_name=getattr(self.judge, "name", "none") if self.judge else "none",
            judge_deterministic=bool(getattr(self.judge, "deterministic", True)),
            results=tuple(results),
            metrics=aggregate(results),
            provenance=dict(sorted(provenance.items())),
            duration_s=time.perf_counter() - started,
            measured_fraction=measured / len(results) if results else 0.0,
        )

    @property
    def collected_answers(self) -> list[RecordedAnswer]:
        return list(self._answers)
