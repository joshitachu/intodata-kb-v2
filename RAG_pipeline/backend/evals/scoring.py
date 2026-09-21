"""Deterministic scoring.

Everything in this module is pure and offline: same inputs, same numbers, no
model call, no network. The LLM judge lives in ``judge.py`` and its output is
reported in a separate block so that a judge outage, a model swap or a missing
API key cannot move the numbers CI gates on.

The corpus this harness grades is Dutch-language generated documentation, so
every lexical detector carries Dutch and English surface forms, and all matching
runs on accent-stripped text (generated docs are ASCII-only by project rule, but
questions and live answers are not).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from evals.answers import RecordedAnswer
from evals.cases import EvalCase

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_MARKDOWN_NOISE = re.compile(r"[*_`#>|]+")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents and markdown scaffolding, collapse whitespace.

    Punctuation is preserved because sentence splitting depends on it -- the
    absence detector below is sentence-scoped, and that scoping is the whole
    reason it can tell "no filter is recorded" apart from "no filter is recorded
    in the documentation".
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = _MARKDOWN_NOISE.sub(" ", stripped)
    return _WHITESPACE.sub(" ", stripped).lower().strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def sentences(normalized_text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(normalized_text) if s.strip()]


def contains_phrase(haystack_normalized: str, needle: str) -> bool:
    """Substring match with word boundaries when the needle is word-like.

    Without the boundary, ``"SO"`` matches inside ``"source"`` and every
    worktype-enumeration case scores 100%. With it, multi-word and
    punctuation-bearing needles still work because we only add boundaries on the
    sides that are actually alphanumeric.
    """
    needle_n = normalize(needle)
    if not needle_n:
        return False
    left = r"\b" if needle_n[0].isalnum() else ""
    right = r"\b" if needle_n[-1].isalnum() else ""
    return re.search(left + re.escape(needle_n) + right, haystack_normalized) is not None


# ---------------------------------------------------------------------------
# Number words (Dutch + English), so "31" and "eenendertig" score the same
# ---------------------------------------------------------------------------

_NL_UNITS = [
    "nul", "een", "twee", "drie", "vier", "vijf", "zes", "zeven", "acht", "negen",
    "tien", "elf", "twaalf", "dertien", "veertien", "vijftien", "zestien",
    "zeventien", "achttien", "negentien",
]
_NL_TENS = {
    20: "twintig", 30: "dertig", 40: "veertig", 50: "vijftig",
    60: "zestig", 70: "zeventig", 80: "tachtig", 90: "negentig",
}
_EN_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_EN_TENS = {
    20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
    60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
}


def number_aliases(value: int) -> tuple[str, ...]:
    """Accepted written forms of an integer.

    Only 0-99 get spelled out; above that, generated documentation uses digits
    (and hand-spelling "achthonderdeenenvijftig" is not a real surface form).
    Dutch compounds are emitted accent-free -- ``normalize`` strips the diaeresis
    from "tweeentwintig" either way.
    """
    forms: list[str] = [str(value)]
    if 0 <= value < 20:
        forms.append(_NL_UNITS[value])
        forms.append(_EN_UNITS[value])
    elif 20 <= value < 100:
        tens, unit = divmod(value, 10)
        tens_value = tens * 10
        if unit == 0:
            forms.append(_NL_TENS[tens_value])
            forms.append(_EN_TENS[tens_value])
        else:
            forms.append(f"{_NL_UNITS[unit]}en{_NL_TENS[tens_value]}")
            forms.append(f"{_EN_TENS[tens_value]}-{_EN_UNITS[unit]}")
            forms.append(f"{_EN_TENS[tens_value]} {_EN_UNITS[unit]}")
    return tuple(dict.fromkeys(forms))


def states_number(normalized_answer: str, value: int) -> bool:
    return any(contains_phrase(normalized_answer, form) for form in number_aliases(value))


# ---------------------------------------------------------------------------
# Abstention vs. absence assertion -- the distinction the whole suite turns on
# ---------------------------------------------------------------------------

# "I cannot answer this from what I can see." Correct on an unanswerable case.
_ABSTENTION_MARKERS: tuple[str, ...] = (
    r"niet vast(gesteld|stellen)",
    r"kan ik niet",
    r"is niet af te leiden",
    r"niet te (bepalen|beantwoorden|herleiden)",
    r"niet beoordeeld",
    r"geen (bron|bewijs|documentatie|vindplaats)",
    r"buiten (het bereik|de reikwijdte|de scope)",
    r"niet in de (aangeleverde |beschikbare |geindexeerde )?(documentatie|documenten|stukken|set)",
    r"komt (hier )?niet in voor",
    # "the thing you asked for is not in this material" -- a statement about the
    # corpus, which is the correct shape of a decline. Deliberately distinct from
    # the absence list below, which is about reality.
    r"\b(is|zijn) er (nog )?niet\b",
    r"\bontbreek(t|en)\b",
    r"\bnog niet (opgesteld|gemaakt|beschikbaar|aanwezig|geleverd)\b",
    r"\bniet (opgesteld|opgenomen|beschikbaar|aanwezig|gedocumenteerd|beschreven)\b",
    r"i (don'?t|do not|cannot|can'?t) (know|say|determine|tell|answer|confirm)",
    r"not (established|documented|recorded|covered|available|derivable) (in|from)",
    r"(is|are) not (yet )?(available|present|produced|documented|recorded)",
    r"no (evidence|source|document|documentation) (is |was )?(found|available)",
    r"outside (the )?(scope|corpus)",
    r"unable to (determine|establish|answer|confirm)",
    r"insufficient (evidence|information|documentation)",
)

# "The thing does not exist." Wrong on an unanswerable case, because the corpus
# not containing a rule is not the same as the rule not existing. This is the
# fabricated-absence failure mode in one regex list.
_ABSENCE_ASSERTION_MARKERS: tuple[str, ...] = (
    r"\ber (is|zijn|wordt|worden) geen\b",
    r"\bgeen (filter|selectie|regel|beperking|voorwaarde|afbakening)\b",
    r"\bwordt niet (gefilterd|beperkt|geselecteerd)\b",
    r"\balle klanten\b",
    r"\bthere (is|are) no\b",
    r"\bno (filter|rule|selection|restriction|criteri(on|a)|condition)\b",
    r"\bis not filtered\b",
    r"\ball customers\b",
    r"\bdoes not exist\b",
    r"\bbestaat niet\b",
)

# Words that, inside the same sentence, turn an absence assertion into a scoped
# statement about the corpus. "no filter is recorded" is a claim about reality;
# "no filter is recorded in the supplied documentation" is a claim about a
# document set, and only the second one is honest.
_SCOPE_QUALIFIERS: tuple[str, ...] = (
    r"\bin (de|het|deze) (documentatie|documenten|stukken|set|aangeleverde|beschikbare)",
    r"\bin the (documentation|documents|corpus|indexed|available|supplied)",
    r"\bvolgens de documentatie\b",
    r"\baccording to the documentation\b",
    r"\bin de aangeleverde\b",
    r"\bin de geindexeerde\b",
    r"\bdocumented\b",
    r"\bgedocumenteerd\b",
    r"\bbeschreven\b",
)

# Sentences that *deny* an absence claim rather than make one. "That does not
# mean there is no selection" is the opposite of "there is no selection", and a
# bare keyword match cannot tell them apart -- which matters, because this
# construction is exactly what a well-calibrated answer uses to stop the reader
# drawing the wrong conclusion from an empty search.
_ABSENCE_NEGATORS: tuple[str, ...] = (
    r"\bvolgt (daaruit )?niet dat\b",
    r"\bbetekent niet dat\b",
    r"\bwil niet zeggen dat\b",
    r"\bniet dat er geen\b",
    r"\bhoudt niet in dat\b",
    r"\bdoes not mean (that )?\b",
    r"\bdoes not (follow|imply)\b",
    r"\bis not (the same as|evidence that)\b",
    r"\bthat is not to say\b",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def detect_abstention(normalized_answer: str) -> bool:
    """Did the answer decline, defer, or flag a limit of its evidence?"""
    return bool(_matches_any(normalized_answer, _ABSTENTION_MARKERS))


def detect_unqualified_absence(normalized_answer: str) -> list[str]:
    """Sentences asserting that something does not exist, without scoping it.

    Returns the offending sentences (empty list = clean). Scope is judged per
    sentence: a qualifier three paragraphs later does not retroactively make a
    bare "there is no such rule" honest, because that is the sentence a reader
    quotes back to the business.
    """
    offenders: list[str] = []
    for sentence in sentences(normalized_answer):
        if not _matches_any(sentence, _ABSENCE_ASSERTION_MARKERS):
            continue
        if _matches_any(sentence, _SCOPE_QUALIFIERS):
            continue
        if _matches_any(sentence, _ABSENCE_NEGATORS):
            continue
        offenders.append(sentence)
    return offenders


# Phrases that disclose an incomplete enumeration. Failure mode 1 is not
# "the answer named 5 of 9" -- it is "the answer named 5 of 9 and said nothing".
_PARTIAL_DISCLOSURE_MARKERS: tuple[str, ...] = (
    r"\bmogelijk (niet )?volledig\b",
    r"\bniet (uitputtend|volledig|compleet)\b",
    r"\bonvolledig\b",
    r"\bten minste\b",
    r"\bminimaal\b",
    r"\bonder (meer|andere)\b",
    r"\bo\.?a\.?\b",
    r"\bnot (exhaustive|complete)\b",
    r"\bmay be incomplete\b",
    r"\bat least\b",
    r"\bamong others\b",
    r"\bpartial\b",
    r"\bi (only )?found\b",
    r"\bkon ik (alleen|slechts)\b",
    r"\bslechts\b",
)


def detect_partial_disclosure(normalized_answer: str) -> bool:
    return bool(_matches_any(normalized_answer, _PARTIAL_DISCLOSURE_MARKERS))


# ---------------------------------------------------------------------------
# Per-metric results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageResult:
    found: tuple[str, ...]
    missing: tuple[str, ...]
    disclosed: bool

    @property
    def ratio(self) -> float:
        total = len(self.found) + len(self.missing)
        return 1.0 if total == 0 else len(self.found) / total

    @property
    def undisclosed_partial(self) -> bool:
        """The exact shape of failure mode 1."""
        return self.ratio < 1.0 and not self.disclosed


@dataclass(frozen=True)
class GroundednessResult:
    """Deterministic numeric grounding.

    Every integer the answer states is checked against the retrieved text. This
    catches the truncated-table class of error precisely: if the retrieved chunks
    never contain "30", an answer that says "30 fields" invented it.

    Known limitation, stated rather than hidden: *derived* numbers (percentages,
    sums the model computed) are legitimately absent from the context and score
    as ungrounded. That is why this number is reported, but is not part of the
    pass/fail gate -- ``expected_counts`` / ``forbidden_counts`` are.
    """

    checked: tuple[int, ...]
    ungrounded: tuple[int, ...]
    citations_seen: tuple[int, ...]
    invalid_citations: tuple[int, ...]
    context_available: bool

    @property
    def numeric_ratio(self) -> float | None:
        if not self.context_available or not self.checked:
            return None
        return 1.0 - (len(self.ungrounded) / len(self.checked))

    @property
    def citation_ratio(self) -> float | None:
        if not self.citations_seen:
            return None
        return 1.0 - (len(self.invalid_citations) / len(self.citations_seen))


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    answerable: bool
    answer_type: str
    provenance: str

    retrieval_hit: bool | None
    coverage: CoverageResult | None
    groundedness: GroundednessResult
    abstained: bool
    delivered_substance: bool
    unqualified_absence: tuple[str, ...]
    abstention_correct: bool | None  # unanswerable cases only
    over_abstained: bool | None  # answerable cases only
    counts_correct: bool | None
    wrong_counts: tuple[str, ...]
    must_not_claim_violations: tuple[str, ...]
    missing_disclosures: tuple[str, ...]
    contradiction_disclosed: bool | None
    missing_contradiction_sides: tuple[str, ...]

    failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------

# Digit runs, ignoring the ones inside markdown citation brackets ("[3]") and
# inside ISO-ish dates, both of which are formatting rather than claims.
_CITATION_RE = re.compile(r"\[(\d{1,3})\]")
_DATE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_NUMBER_RE = re.compile(r"(?<![\w.\-])(\d{1,7})(?![\w.])")

# Integers too generic to grade. "1" and "2" appear in ordinals, list markers and
# version strings constantly; grading them produces noise, not signal.
_TRIVIAL_NUMBERS = frozenset({0, 1, 2})


def extract_claimed_numbers(answer: str) -> tuple[int, ...]:
    text = _CITATION_RE.sub(" ", answer)
    text = _DATE_RE.sub(" ", text)
    values: list[int] = []
    for match in _NUMBER_RE.finditer(text):
        value = int(match.group(1))
        if value in _TRIVIAL_NUMBERS:
            continue
        if 1900 <= value <= 2100:  # years
            continue
        values.append(value)
    return tuple(dict.fromkeys(values))


def _score_groundedness(recorded: RecordedAnswer) -> GroundednessResult:
    context = normalize(recorded.context_text)
    context_available = bool(context)

    checked = extract_claimed_numbers(recorded.answer)
    ungrounded: list[int] = []
    if context_available:
        for value in checked:
            if not states_number(context, value):
                ungrounded.append(value)

    citations = tuple(dict.fromkeys(int(m) for m in _CITATION_RE.findall(recorded.answer)))
    valid_refs = {
        s.reference_number for s in recorded.sources if s.reference_number is not None
    } or set(range(1, len(recorded.sources) + 1))
    invalid = tuple(c for c in citations if c not in valid_refs)

    return GroundednessResult(
        checked=checked,
        ungrounded=tuple(ungrounded),
        citations_seen=citations,
        invalid_citations=invalid,
        context_available=context_available,
    )


def _score_coverage(case: EvalCase, normalized_answer: str) -> CoverageResult | None:
    if not case.expected_entities:
        return None
    found: list[str] = []
    missing: list[str] = []
    for entity in case.expected_entities:
        if any(contains_phrase(normalized_answer, form) for form in entity.surface_forms):
            found.append(entity.name)
        else:
            missing.append(entity.name)
    return CoverageResult(
        found=tuple(found),
        missing=tuple(missing),
        disclosed=detect_partial_disclosure(normalized_answer),
    )


# An answer is "substantive" when it actually delivered what the case asked for.
# This exists so that a well-calibrated answer -- one that gives the facts *and*
# names the residual uncertainty -- is not scored as a refusal. Without it, the
# harness would punish exactly the behaviour it is meant to reward, because
# honest hedging and outright declining share vocabulary ("niet vastgesteld").
_SUBSTANCE_MIN_CHARS = 200


def _delivered_substance(
    case: EvalCase,
    normalized_answer: str,
    coverage: CoverageResult | None,
) -> bool:
    if coverage is not None:
        return coverage.ratio > 0.0
    if case.expected_counts:
        return any(states_number(normalized_answer, v) for v in case.expected_counts.values())
    if case.contradiction is not None:
        return any(
            any(re.search(alt, normalized_answer) for alt in side)
            for side in case.contradiction.sides
        )
    return len(normalized_answer) >= _SUBSTANCE_MIN_CHARS


def _missing_sources(case: EvalCase, recorded: RecordedAnswer) -> tuple[str, ...]:
    retrieved = " | ".join(normalize(s.filename) for s in recorded.sources)
    return tuple(e for e in case.expected_sources if normalize(e) not in retrieved)


def _score_retrieval(case: EvalCase, recorded: RecordedAnswer) -> bool | None:
    """Was the evidence even retrieved?

    ``None`` means "not measurable here": the case declares no expected sources,
    or this answer never recorded any. Undefined is reported as undefined -- it
    is never folded into the average as a zero, which would make a fixture that
    predates source capture look like a retrieval regression.

    A *captured but empty* source list is a miss, not undefined: that is a live
    run in which retrieval returned nothing, which is precisely the case the
    metric exists to count.
    """
    if not case.expected_sources or not recorded.sources_captured:
        return None
    return not _missing_sources(case, recorded)


def score_case(case: EvalCase, recorded: RecordedAnswer) -> CaseScore:
    normalized = normalize(recorded.answer)
    failures: list[str] = []

    # --- must-not-claim ----------------------------------------------------
    violations = tuple(p for p in case.must_not_claim if re.search(p, normalized))
    for pattern in violations:
        failures.append(f"must_not_claim matched: /{pattern}/")

    # --- required disclosures ---------------------------------------------
    missing_disclosures: list[str] = []
    for group in case.required_disclosures:
        if not any(re.search(alt, normalized) for alt in group):
            missing_disclosures.append(" | ".join(group))
    for group in missing_disclosures:
        failures.append(f"required disclosure missing: [{group}]")

    # --- coverage ----------------------------------------------------------
    # Computed before abstention because the substance test below depends on it.
    coverage = _score_coverage(case, normalized)

    # --- abstention: two separate numbers, never one ----------------------
    abstained = detect_abstention(normalized)
    absence_offenders = tuple(detect_unqualified_absence(normalized))
    substantive = _delivered_substance(case, normalized, coverage)

    abstention_correct: bool | None = None
    over_abstained: bool | None = None
    if case.answerable:
        # Over-abstention is declining *instead of* answering -- an abstention
        # marker in an otherwise substantive answer is calibration, not refusal.
        # Counting a decline as "correctly cautious" is how a model that says
        # "I don't know" to everything ends up with a good abstention score.
        over_abstained = abstained and not substantive
        if over_abstained:
            failures.append("over-abstained on an answerable case (declined, no substance)")
        if absence_offenders:
            failures.append(
                f"asserted unqualified absence: {absence_offenders[0][:120]!r}"
            )
    else:
        abstention_correct = abstained and not absence_offenders
        if not abstained:
            failures.append("failed to abstain on an unanswerable case")
        if absence_offenders:
            failures.append(
                "reported absence-from-corpus as absence-in-reality: "
                f"{absence_offenders[0][:120]!r}"
            )

    if coverage is not None:
        if coverage.ratio < case.min_coverage:
            failures.append(
                f"coverage {coverage.ratio:.0%} < required {case.min_coverage:.0%} "
                f"(missing: {', '.join(coverage.missing)})"
            )
        if coverage.undisclosed_partial:
            failures.append(
                f"undisclosed partial coverage: named {len(coverage.found)} of "
                f"{len(coverage.found) + len(coverage.missing)} without saying so"
            )

    # --- numeric claims ----------------------------------------------------
    counts_correct: bool | None = None
    wrong_counts: list[str] = []
    if case.expected_counts:
        counts_correct = True
        for label, value in case.expected_counts.items():
            if not states_number(normalized, value):
                counts_correct = False
                wrong_counts.append(f"{label}: expected {value}, not stated")
        # A forbidden near-miss only counts against the answer when the right
        # value is ABSENT. Otherwise this check fights the contradiction check:
        # the correct answer to "how many fields does FO 4.1 have" is "31, and
        # note that the register says 30" -- which states a forbidden value on
        # purpose, and is the behaviour we are trying to produce. Firing on the
        # mere presence of the number would train the system to hide the
        # disagreement it is supposed to surface.
        for label, forbidden in case.forbidden_counts.items():
            expected = case.expected_counts.get(label)
            if expected is not None and states_number(normalized, expected):
                continue
            for bad in forbidden:
                if states_number(normalized, bad):
                    counts_correct = False
                    wrong_counts.append(
                        f"{label}: stated near-miss {bad} and never stated the "
                        f"correct value{f' {expected}' if expected is not None else ''}"
                    )
        for problem in wrong_counts:
            failures.append(f"count claim wrong -- {problem}")

    # --- cross-document contradiction --------------------------------------
    contradiction_disclosed: bool | None = None
    missing_sides: list[str] = []
    if case.contradiction is not None:
        for side in case.contradiction.sides:
            if not any(re.search(alt, normalized) for alt in side):
                missing_sides.append(" | ".join(side))
        contradiction_disclosed = not missing_sides
        if missing_sides:
            failures.append(
                "silently picked one side of a documented contradiction; "
                f"absent: [{missing_sides[0]}]"
            )

    # --- retrieval ---------------------------------------------------------
    retrieval_hit = _score_retrieval(case, recorded)
    if retrieval_hit is False:
        missing_sources = _missing_sources(case, recorded)
        detail = list(missing_sources) if missing_sources else "nothing was retrieved"
        failures.append(f"retrieval miss: expected evidence not retrieved -- {detail}")

    return CaseScore(
        case_id=case.id,
        answerable=case.answerable,
        answer_type=case.answer_type,
        provenance=recorded.provenance,
        retrieval_hit=retrieval_hit,
        coverage=coverage,
        groundedness=_score_groundedness(recorded),
        abstained=abstained,
        delivered_substance=substantive,
        unqualified_absence=absence_offenders,
        abstention_correct=abstention_correct,
        over_abstained=over_abstained,
        counts_correct=counts_correct,
        wrong_counts=tuple(wrong_counts),
        must_not_claim_violations=violations,
        missing_disclosures=tuple(missing_disclosures),
        contradiction_disclosed=contradiction_disclosed,
        missing_contradiction_sides=tuple(missing_sides),
        failures=tuple(failures),
    )
