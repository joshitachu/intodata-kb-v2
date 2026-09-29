"""Eval-set schema and loader.

An eval *case* is a question plus everything needed to decide whether an answer
to it was acceptable. The shape is driven by four measured failure modes that a
conventional "question + expected answer, score 1-5" corpus cannot detect:

1. **Silent partial coverage** -- the answer names 5 of 9 entities and says
   nothing about the other 4. Handled by ``expected_entities`` (coverage) plus
   ``min_coverage`` and the partial-coverage disclosure check.
2. **Fabricated absence** -- the corpus does not contain the fact, the system
   searched the corpus, found nothing, and reported "there is no such rule".
   Handled by ``answerable: false`` plus ``required_disclosures`` plus
   ``must_not_claim``.
3. **Table truncation** -- a count read out of a chunked table comes back one
   short. Handled by ``expected_counts`` / ``forbidden_counts``.
4. **Cross-document contradiction** -- two documents disagree and the answer
   silently picks a side. Handled by ``contradiction``.

The critical field is ``answerable``. A suite made only of in-corpus questions
scores near-perfect while the system is confidently wrong on everything outside
the corpus, because nothing in the suite ever asks it to decline. Unanswerable
cases are therefore first-class, and abstention is scored as *two* numbers (see
``scoring.py``) so that a model which always says "I don't know" cannot win.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# The answer shapes we grade differently. This is not a taxonomy for its own
# sake: enumeration cases get coverage scoring, count cases get numeric
# grounding, contradiction cases require both sides to be surfaced.
AnswerType = Literal[
    "enumeration",  # "which systems consume X" -- graded on coverage
    "count",  # "how many fields" -- graded on exact numeric claims
    "lookup",  # "what is the source field for Y" -- single fact
    "explanation",  # free prose -- only judge + must_not_claim apply
    "contradiction",  # the corpus disagrees with itself; both sides required
]


@dataclass(frozen=True)
class EvidenceRef:
    """Where the ground truth physically lives.

    ``path`` is relative to the repository root so the suite is portable, and
    ``validate`` (CLI) checks that every referenced file still exists. A case
    whose evidence file was renamed is a silently dead case otherwise.

    ``in_corpus`` distinguishes "this file is ingested into the RAG corpus" from
    "this file exists in the repo but is not something the system can retrieve"
    (e.g. raw scanner JSON). That distinction is what makes the SVD case
    unanswerable rather than merely hard.
    """

    path: str
    locator: str = ""  # section / heading / component name, free text
    in_corpus: bool = True


@dataclass(frozen=True)
class Entity:
    """One thing the answer is expected to name, with accepted surface forms.

    Aliases matter more than they look. "4FS" and "Dynamics 365" are the same
    consumer; grading on one spelling turns a correct answer into a miss and
    makes the coverage number meaningless.
    """

    name: str
    aliases: tuple[str, ...] = ()

    @property
    def surface_forms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class ContradictionSpec:
    """Two (or more) competing claims that the corpus itself contains.

    ``sides`` each carry the regex alternatives that identify that side in an
    answer. An answer passes only if *every* side is present -- picking one side
    silently is the failure we are measuring.
    """

    sides: tuple[tuple[str, ...], ...]
    description: str = ""


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    project: str
    answer_type: AnswerType

    # --- the core discriminator -------------------------------------------
    # True  = a correct system can and should answer from the retrievable corpus
    # False = it cannot, and the only correct behaviour is a scoped decline
    answerable: bool
    unanswerable_reason: str = ""

    ground_truth: str = ""
    evidence: tuple[EvidenceRef, ...] = ()

    # --- coverage ----------------------------------------------------------
    expected_entities: tuple[Entity, ...] = ()
    min_coverage: float = 1.0

    # --- numeric claims ----------------------------------------------------
    # expected_counts: the numbers a correct answer must state.
    # forbidden_counts: near-misses that indicate truncated reads. Listing the
    # near-miss explicitly is what turns "the count was wrong" from a judgement
    # call into a deterministic assertion.
    expected_counts: dict[str, int] = field(default_factory=dict)
    forbidden_counts: dict[str, tuple[int, ...]] = field(default_factory=dict)

    # --- assertions --------------------------------------------------------
    # Regexes (case-insensitive) the answer must NOT match anywhere.
    must_not_claim: tuple[str, ...] = ()
    # Regexes the answer MUST match; each entry is a list of alternatives and
    # at least one alternative must hit. Used for scope disclosures such as
    # "the source code was not searched".
    required_disclosures: tuple[tuple[str, ...], ...] = ()

    contradiction: ContradictionSpec | None = None

    # --- retrieval ---------------------------------------------------------
    # Filename substrings that must appear among the retrieved sources for the
    # answer to have been retrievable at all. Only meaningful when the answer
    # provider reports sources.
    expected_sources: tuple[str, ...] = ()

    tags: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_coverage <= 1.0:
            raise ValueError(f"{self.id}: min_coverage must be in [0, 1]")
        if not self.answerable and self.expected_entities:
            # An unanswerable case with expected entities is a contradiction in
            # terms -- if we can name what the answer should contain, it is
            # answerable. Catching this at load time stops the suite drifting
            # into "everything is answerable" over time.
            raise ValueError(
                f"{self.id}: unanswerable cases must not declare expected_entities"
            )
        if not self.answerable and not self.required_disclosures:
            raise ValueError(
                f"{self.id}: unanswerable cases must declare required_disclosures "
                "(otherwise 'I don't know' scores the same as a scoped decline)"
            )


@dataclass(frozen=True)
class EvalSet:
    name: str
    description: str
    version: str
    cases: tuple[EvalCase, ...]

    def by_id(self, case_id: str) -> EvalCase | None:
        return next((c for c in self.cases if c.id == case_id), None)

    @property
    def answerable_cases(self) -> tuple[EvalCase, ...]:
        return tuple(c for c in self.cases if c.answerable)

    @property
    def unanswerable_cases(self) -> tuple[EvalCase, ...]:
        return tuple(c for c in self.cases if not c.answerable)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_raw(path: Path) -> dict[str, Any]:
    """Read a suite file. YAML when available, JSON always.

    We accept both rather than forcing one: YAML is far easier to author for a
    suite with multi-line questions and ground truth, but JSON keeps the loader
    usable in an environment where PyYAML is missing. PyYAML ships transitively
    with the backend's existing dependency set; the fallback exists so a missing
    optional dep degrades to a clear message instead of an ImportError traceback.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # noqa: PLC0415 -- optional import, deliberate
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                f"{path} is YAML but PyYAML is not installed. "
                "Install PyYAML or convert the suite to JSON."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: suite file must contain a mapping at the top level")
    return data


def _parse_entity(raw: Any) -> Entity:
    if isinstance(raw, str):
        return Entity(name=raw)
    if isinstance(raw, dict):
        return Entity(
            name=str(raw["name"]),
            aliases=tuple(str(a) for a in raw.get("aliases", ())),
        )
    raise ValueError(f"unparseable entity: {raw!r}")


def _parse_evidence(raw: Any) -> EvidenceRef:
    if isinstance(raw, str):
        return EvidenceRef(path=raw)
    if isinstance(raw, dict):
        return EvidenceRef(
            path=str(raw["path"]),
            locator=str(raw.get("locator", "")),
            in_corpus=bool(raw.get("in_corpus", True)),
        )
    raise ValueError(f"unparseable evidence ref: {raw!r}")


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    contradiction_raw = raw.get("contradiction")
    contradiction = None
    if contradiction_raw:
        contradiction = ContradictionSpec(
            sides=tuple(
                tuple(str(alt) for alt in side)
                for side in contradiction_raw.get("sides", ())
            ),
            description=str(contradiction_raw.get("description", "")),
        )

    return EvalCase(
        id=str(raw["id"]),
        question=str(raw["question"]).strip(),
        project=str(raw["project"]),
        answer_type=raw.get("answer_type", "explanation"),
        answerable=bool(raw["answerable"]),
        unanswerable_reason=str(raw.get("unanswerable_reason", "")).strip(),
        ground_truth=str(raw.get("ground_truth", "")).strip(),
        evidence=tuple(_parse_evidence(e) for e in raw.get("evidence", ())),
        expected_entities=tuple(_parse_entity(e) for e in raw.get("expected_entities", ())),
        min_coverage=float(raw.get("min_coverage", 1.0)),
        expected_counts={str(k): int(v) for k, v in raw.get("expected_counts", {}).items()},
        forbidden_counts={
            str(k): tuple(int(x) for x in v)
            for k, v in raw.get("forbidden_counts", {}).items()
        },
        must_not_claim=tuple(str(p) for p in raw.get("must_not_claim", ())),
        required_disclosures=tuple(
            tuple(str(alt) for alt in group) for group in raw.get("required_disclosures", ())
        ),
        contradiction=contradiction,
        expected_sources=tuple(str(s) for s in raw.get("expected_sources", ())),
        tags=tuple(str(t) for t in raw.get("tags", ())),
        notes=str(raw.get("notes", "")).strip(),
    )


# The suite is committed with anonymised evidence paths; the client corpus they
# stand for lives in gitignored output directories. This gitignored map turns an
# anonymised path back into the real local one, so the strict existence check
# still runs wherever the corpus is present.
LOCAL_EVIDENCE_ALIASES = Path(__file__).resolve().parent / "suite" / "evidence_aliases.local.json"


def load_evidence_aliases(path: Path = LOCAL_EVIDENCE_ALIASES) -> dict[str, str]:
    """Return ``{suite path: real local path}``; empty when no map exists."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(f"{path}: must be a JSON object mapping path -> path")
    return data


def resolve_evidence_path(ref: EvidenceRef, repo_root: Path, aliases: dict[str, str]) -> Path:
    """Where ``ref`` lives on this machine."""
    return repo_root / aliases.get(ref.path, ref.path)


def load_eval_set(path: str | Path) -> EvalSet:
    """Load and validate an eval set from YAML or JSON."""
    path = Path(path)
    data = _load_raw(path)
    cases = tuple(_parse_case(c) for c in data.get("cases", ()))

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"{path}: duplicate case id {case.id!r}")
        seen.add(case.id)

    if not cases:
        raise ValueError(f"{path}: suite contains no cases")

    return EvalSet(
        name=str(data.get("name", path.stem)),
        description=str(data.get("description", "")),
        version=str(data.get("version", "0")),
        cases=cases,
    )


def default_suite_path() -> Path:
    return Path(__file__).resolve().parent / "suite" / "core.yaml"
