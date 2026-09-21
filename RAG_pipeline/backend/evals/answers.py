"""Answer providers: where the text being scored comes from.

Two implementations, one protocol:

* :class:`FixtureProvider` -- replays answers recorded in a JSON file. This is
  the CI path. No Qdrant, no Postgres, no Ollama, no network.
* :class:`HttpProvider` -- calls a running backend over HTTP.

The ``live`` path speaks HTTP rather than importing ``src.generation.rag_chain``
on purpose. Importing the chain would drag in Qdrant/SQLAlchemy/settings at
module import time and couple the suite to internals that are being rewritten.
HTTP is the product's actual contract.

Provenance
----------
Every recorded answer carries a ``provenance`` field, and the report prints the
breakdown. This is not bookkeeping for its own sake: a fixture that was *written
by hand* to illustrate a failure mode must never be reported as a measurement of
the live system. The scorer cannot tell the difference, so the file says so and
the report repeats it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from evals.cases import EvalCase

# How an answer came to exist. Only ``live_capture`` is a measurement.
Provenance = Literal[
    "live_capture",  # captured from a real run of the deployed system
    "reconstructed_from_incident",  # paraphrase of an answer observed in a real incident
    "handwritten_reference",  # authored by hand as a positive control for the scorer
    "handwritten_negative",  # authored by hand to exercise a specific failure mode
]

MEASURED_PROVENANCES: frozenset[str] = frozenset({"live_capture"})


@dataclass(frozen=True)
class RetrievedSource:
    """A retrieved document as the eval harness needs to see it.

    Deliberately a subset of the backend's ``DocumentSource``: only the fields a
    scorer can use. Extra keys in a fixture file are ignored rather than
    rejected, so a fixture captured from a future response shape still loads.
    """

    filename: str
    text: str = ""
    score: float | None = None
    document_id: int | None = None
    project_id: str | None = None
    reference_number: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RetrievedSource:
        return cls(
            filename=str(raw.get("filename", "")),
            text=str(raw.get("text", "")),
            score=float(raw["score"]) if raw.get("score") is not None else None,
            document_id=raw.get("document_id"),
            project_id=raw.get("project_id"),
            reference_number=raw.get("reference_number"),
        )


@dataclass(frozen=True)
class RecordedAnswer:
    case_id: str
    answer: str
    sources: tuple[RetrievedSource, ...] = ()
    provenance: Provenance = "handwritten_negative"
    model: str = ""
    captured_at: str = ""
    latency_ms: int | None = None
    notes: str = ""
    # Distinguishes "the run retrieved nothing" (captured, empty -> a retrieval
    # miss) from "this fixture never recorded sources" (not captured -> the
    # retrieval metric is undefined here). Collapsing the two would either
    # invent a 0% retrieval rate for source-less fixtures or hide a real
    # zero-retrieval run.
    sources_captured: bool = True

    @property
    def context_text(self) -> str:
        """Concatenated retrieved text -- the denominator for groundedness."""
        return "\n\n".join(s.text for s in self.sources if s.text)

    @property
    def is_measurement(self) -> bool:
        return self.provenance in MEASURED_PROVENANCES


class AnswerProvider(Protocol):
    """Anything that can produce an answer for a case."""

    name: str

    def answer(self, case: EvalCase) -> RecordedAnswer: ...


@dataclass
class FixtureProvider:
    """Replays recorded answers. Offline, deterministic, CI-safe."""

    answers: dict[str, RecordedAnswer]
    name: str = "fixtures"
    source_path: str = ""
    # Missing answers are a hard error by default. A fixture file that silently
    # skips the cases the system fails on would report a perfect score, which is
    # exactly the class of bug this harness exists to catch.
    strict: bool = True

    @classmethod
    def from_file(cls, path: str | Path, *, strict: bool = True) -> FixtureProvider:
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw["answers"] if isinstance(raw, dict) else raw
        answers: dict[str, RecordedAnswer] = {}
        for entry in entries:
            recorded = RecordedAnswer(
                case_id=str(entry["case_id"]),
                answer=str(entry["answer"]),
                sources=tuple(
                    RetrievedSource.from_dict(s) for s in entry.get("sources", ())
                ),
                provenance=entry.get("provenance", "handwritten_negative"),
                model=str(entry.get("model", "")),
                captured_at=str(entry.get("captured_at", "")),
                latency_ms=entry.get("latency_ms"),
                notes=str(entry.get("notes", "")),
                sources_captured="sources" in entry,
            )
            if recorded.case_id in answers:
                raise ValueError(f"{path}: duplicate answer for case {recorded.case_id!r}")
            answers[recorded.case_id] = recorded
        label = str(raw.get("name", path.stem)) if isinstance(raw, dict) else path.stem
        return cls(answers=answers, name=f"fixtures:{label}", source_path=str(path), strict=strict)

    def answer(self, case: EvalCase) -> RecordedAnswer:
        recorded = self.answers.get(case.id)
        if recorded is None:
            if self.strict:
                raise KeyError(
                    f"no recorded answer for case {case.id!r} in {self.source_path or self.name}"
                )
            return RecordedAnswer(case_id=case.id, answer="", provenance="handwritten_negative")
        return recorded

    def provenance_breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for recorded in self.answers.values():
            counts[recorded.provenance] = counts.get(recorded.provenance, 0) + 1
        return dict(sorted(counts.items()))


@dataclass
class HttpProvider:
    """Calls a running backend's non-streaming query endpoint.

    Uses ``POST {base_url}/api/v1/query`` with the backend's ``QueryRequest``
    body. That endpoint requires a JWT access-token cookie and is rate limited
    to 30 requests/minute, so the runner throttles between calls.
    """

    base_url: str
    access_token: str
    name: str = "live-http"
    top_k: int = 8
    threshold: float = 0.3
    timeout_s: float = 180.0
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        import httpx  # noqa: PLC0415 -- only needed in live mode

        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout_s,
            cookies={"access_token": self.access_token},
        )

    def answer(self, case: EvalCase) -> RecordedAnswer:
        import datetime as _dt  # noqa: PLC0415

        response = self._client.post(
            "/api/v1/query",
            json={
                "query": case.question,
                "top_k": self.top_k,
                "threshold": self.threshold,
                "project_id": case.project,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return RecordedAnswer(
            case_id=case.id,
            answer=str(payload.get("answer", "")),
            sources=tuple(
                RetrievedSource.from_dict(s) for s in payload.get("sources", ())
            ),
            provenance="live_capture",
            latency_ms=payload.get("latency_ms"),
            captured_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def dump_answers(
    answers: list[RecordedAnswer],
    path: str | Path,
    *,
    name: str,
    note: str = "",
) -> None:
    """Write answers back out as a fixture file.

    This is how a live run becomes a CI fixture: run live once, dump, commit.
    """
    payload = {
        "name": name,
        "note": note,
        "answers": [
            {
                "case_id": a.case_id,
                "provenance": a.provenance,
                "model": a.model,
                "captured_at": a.captured_at,
                "latency_ms": a.latency_ms,
                "notes": a.notes,
                "answer": a.answer,
                "sources": [
                    {
                        "filename": s.filename,
                        "score": s.score,
                        "document_id": s.document_id,
                        "project_id": s.project_id,
                        "reference_number": s.reference_number,
                        "text": s.text,
                    }
                    for s in a.sources
                ],
            }
            for a in answers
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
