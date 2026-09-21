"""LLM-as-judge, isolated behind one interface with a deterministic fallback.

Only two metrics need a judge: *semantic* groundedness (is every claim supported
by the retrieved text, not just every number) and correctness against a prose
ground truth. Everything else in this harness is deterministic, and the pass/fail
gate uses only the deterministic metrics -- see ``README.md``.

Why the split is enforced rather than merely recommended:

* A judge needs an API key. CI does not have one, and a suite that cannot run in
  CI is a suite that rots.
* A judge is not reproducible. Claude Opus 5 runs adaptive thinking and no
  longer accepts ``temperature``, so two runs of the same prompt can differ. A
  regression gate built on a non-reproducible number produces flaky builds and
  gets disabled within a month.

So the judge adds information; it never removes or overrides a deterministic
verdict.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from evals.answers import RecordedAnswer
from evals.cases import EvalCase
from evals.scoring import normalize

logger = logging.getLogger(__name__)

# Model id per the current Anthropic model table. Overridable for cost control,
# but the default is deliberately the strong model: a judge that is weaker than
# the system under test grades noise.
DEFAULT_JUDGE_MODEL = "claude-opus-5"


@dataclass(frozen=True)
class JudgeVerdict:
    judge: str
    groundedness: float | None
    correctness: float | None
    unsupported_claims: tuple[str, ...]
    rationale: str
    # "deterministic-proxy" verdicts must never be presented next to model
    # verdicts without this label -- they measure word overlap, not entailment.
    confidence: str


class Judge(Protocol):
    name: str
    available: bool
    deterministic: bool

    def judge(self, case: EvalCase, recorded: RecordedAnswer) -> JudgeVerdict | None: ...


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9_./-]{4,}")

# Dutch/English function words that survive the >=4-character filter and would
# otherwise inflate every overlap score.
_STOPWORDS = frozenset(
    {
        "deze", "wordt", "worden", "hebben", "heeft", "zijn", "waar", "welke",
        "niet", "voor", "door", "naar", "over", "onder", "tussen", "omdat",
        "maar", "ook", "alle", "geen", "daar", "hier", "want", "zoals", "meer",
        "that", "this", "with", "from", "have", "been", "were", "which", "their",
        "there", "these", "those", "into", "than", "then", "also", "only", "some",
        "such", "each", "when", "what", "where", "them", "they", "will", "would",
    }
)


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(normalize(text)) if w not in _STOPWORDS}


@dataclass
class HeuristicJudge:
    """Always-available proxy so the suite runs identically with no API key.

    This is lexical overlap, not entailment. It is reported with
    ``confidence="deterministic-proxy"`` and is useful for one thing only:
    spotting an answer whose vocabulary has almost nothing to do with what was
    retrieved. Do not read a 0.7 here as "70% grounded".
    """

    name: str = "heuristic-overlap"
    available: bool = True
    deterministic: bool = True

    def judge(self, case: EvalCase, recorded: RecordedAnswer) -> JudgeVerdict:
        answer_words = _content_words(recorded.answer)
        context_words = _content_words(recorded.context_text)
        truth_words = _content_words(case.ground_truth)

        groundedness = None
        unsupported: tuple[str, ...] = ()
        if context_words and answer_words:
            supported = answer_words & context_words
            groundedness = len(supported) / len(answer_words)
            unsupported = tuple(sorted(answer_words - context_words))[:15]

        correctness = None
        if truth_words and answer_words:
            correctness = len(answer_words & truth_words) / len(truth_words)

        return JudgeVerdict(
            judge=self.name,
            groundedness=groundedness,
            correctness=correctness,
            unsupported_claims=unsupported,
            rationale="lexical content-word overlap; not an entailment check",
            confidence="deterministic-proxy",
        )


# ---------------------------------------------------------------------------
# Claude judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """You grade answers produced by a retrieval-augmented \
documentation assistant. The documentation is Dutch; answers may be Dutch or \
English. Grade what is written, not what you believe to be true about the world.

Return two independent scores in [0, 1]:

- groundedness: the fraction of the answer's factual claims that are supported \
by the RETRIEVED CONTEXT. A claim that is true but absent from the context is \
NOT grounded. If no context was retrieved, return null.
- correctness: agreement with the GROUND TRUTH. If no ground truth is supplied, \
return null.

List every claim you judged unsupported, quoted verbatim and briefly. Be strict \
about counts, entity names and field names: a plausible-looking number that does \
not appear in the context is the single most common defect in this system."""

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groundedness": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "correctness": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["groundedness", "correctness", "unsupported_claims", "rationale"],
    "additionalProperties": False,
}

# Retrieved context can be enormous. The judge only needs enough to decide
# support; truncating the *context* biases towards "ungrounded", which is the
# safe direction for a quality gate (it over-reports problems, never hides them).
_MAX_CONTEXT_CHARS = 60_000


@dataclass
class ClaudeJudge:
    """LLM judge over the Anthropic Messages API with a strict JSON schema.

    Notes on the request shape (these are current-API details, not preferences):
    ``temperature`` is rejected by Opus 5, thinking is adaptive by default, and
    structured output goes through ``output_config.format``, not the retired
    ``output_format`` parameter.
    """

    model: str = DEFAULT_JUDGE_MODEL
    name: str = "claude"
    deterministic: bool = False
    _client: Any = None

    def __post_init__(self) -> None:
        self.name = f"claude:{self.model}"

    @property
    def available(self) -> bool:
        # An unset ANTHROPIC_API_KEY does not prove there are no credentials --
        # the SDK also resolves ANTHROPIC_AUTH_TOKEN and an `ant auth login`
        # profile on disk. We only claim unavailability when the client itself
        # refuses to construct.
        try:
            self._ensure_client()
        except Exception:  # noqa: BLE001 - availability probe, never fatal
            return False
        return True

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic  # noqa: PLC0415 - optional, only in judge mode

            self._client = anthropic.Anthropic()
        return self._client

    def judge(self, case: EvalCase, recorded: RecordedAnswer) -> JudgeVerdict | None:
        try:
            client = self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Claude judge unavailable: %s", exc)
            return None

        context = recorded.context_text[:_MAX_CONTEXT_CHARS]
        prompt = (
            f"QUESTION:\n{case.question}\n\n"
            f"GROUND TRUTH:\n{case.ground_truth or '(none supplied)'}\n\n"
            f"RETRIEVED CONTEXT:\n{context or '(nothing was retrieved)'}\n\n"
            f"ANSWER UNDER TEST:\n{recorded.answer}"
        )

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {"type": "json_schema", "schema": _JUDGE_SCHEMA}
                },
            )
        except Exception as exc:  # noqa: BLE001 - judge failure must not abort a run
            logger.warning("Claude judge call failed for %s: %s", case.id, exc)
            return None

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Claude judge returned non-JSON for %s: %s", case.id, exc)
            return None

        return JudgeVerdict(
            judge=self.name,
            groundedness=data.get("groundedness"),
            correctness=data.get("correctness"),
            unsupported_claims=tuple(str(c) for c in data.get("unsupported_claims", ())),
            rationale=str(data.get("rationale", "")),
            confidence="model",
        )


def get_judge(kind: str = "auto") -> Judge:
    """Resolve a judge.

    ``auto`` prefers Claude when credentials resolve and silently falls back to
    the heuristic otherwise. The report always prints which judge ran, so the
    fallback is visible rather than silent in the output.
    """
    if kind == "heuristic":
        return HeuristicJudge()
    if kind == "claude":
        return ClaudeJudge(model=os.environ.get("EVAL_JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    if kind == "auto":
        candidate = ClaudeJudge(model=os.environ.get("EVAL_JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
        return candidate if candidate.available else HeuristicJudge()
    raise ValueError(f"unknown judge kind: {kind!r}")
