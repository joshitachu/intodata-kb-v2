"""Grounding and disclosure validation for the read-only primary runtime.

The model writes natural prose. This module owns the machine-readable grounded
answer contract, the citation-subset discipline, the leak/internal-output
guard, and the six honest no-evidence states. It reuses the citation-subset
rule and leak regexes from ``evidence_store.synthesis`` rather than duplicating
them; it never mutates an answer or auto-injects unused evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Reuse the existing leak-detection discipline without duplicating it. Importing
# the submodule directly avoids the evidence_store package import graph and any
# circular import; synthesis.py is unchanged.
from src.harness.evidence_store.synthesis import _INTERNAL_ID_RE, _RAW_PAYLOAD_RE
from src.harness.semantic_tools import ToolEnvelope

_REF_ID_KEYS = ("id", "evidence_id")
_REF_COMPOSITE_KEYS = (
    "uri",
    "document_id",
    "runtime_record_id",
    "external_run_id",
    "chunk_index",
)


class AnswerStatus(StrEnum):
    """The honest state of a read-only answer against observed evidence."""

    GROUNDED = "grounded"
    PARTIAL = "partial"
    NO_EVIDENCE = "no_evidence"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SCOPE_DENIED = "scope_denied"
    TOOL_FAILED = "tool_failed"
    CONFLICTING = "conflicting"
    DENIED = "denied"


# Stable disclosure codes required for each honest evidence state.
DISCLOSURE_NO_EVIDENCE = "no_relevant_evidence_in_scope"
DISCLOSURE_SOURCE_UNAVAILABLE = "source_unavailable"
DISCLOSURE_SCOPE_DENIED = "authorization_boundary"
DISCLOSURE_TOOL_FAILED = "lookup_failed"
DISCLOSURE_CONFLICTING = "evidence_conflict"
DISCLOSURE_PARTIAL = "partial_support"
DISCLOSURE_DENIED = "request_denied"

_STATUS_DISCLOSURE: dict[AnswerStatus, str] = {
    AnswerStatus.NO_EVIDENCE: DISCLOSURE_NO_EVIDENCE,
    AnswerStatus.SOURCE_UNAVAILABLE: DISCLOSURE_SOURCE_UNAVAILABLE,
    AnswerStatus.SCOPE_DENIED: DISCLOSURE_SCOPE_DENIED,
    AnswerStatus.TOOL_FAILED: DISCLOSURE_TOOL_FAILED,
    AnswerStatus.CONFLICTING: DISCLOSURE_CONFLICTING,
    AnswerStatus.PARTIAL: DISCLOSURE_PARTIAL,
    AnswerStatus.DENIED: DISCLOSURE_DENIED,
}


class GroundingClaim(BaseModel):
    """One material claim and the allowed evidence references that support it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_key: str = Field(min_length=1, max_length=200)
    evidence_refs: list[str] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    """The single grounded-answer authority for the read-only cohort."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    citations: list[GroundingClaim] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    answer_status: AnswerStatus

    @property
    def cited_refs(self) -> set[str]:
        refs: set[str] = set()
        for citation in self.citations:
            refs.update(citation.evidence_refs)
        return refs

    def to_structured_answer(
        self,
        *,
        runtime_release: str,
        manifest_snapshot_hash: str,
        model_profile: str,
        prompt_version: str,
    ) -> dict[str, Any]:
        """Serialize to the additive ``agent_response_v2_grounded`` payload."""

        return {
            "response_type": "agent_response_v2_grounded",
            "content": self.content,
            "answer_status": self.answer_status.value,
            "citations": [
                {
                    "claim_key": citation.claim_key,
                    "evidence_refs": list(citation.evidence_refs),
                }
                for citation in self.citations
            ],
            "disclosures": list(self.disclosures),
            "attribution": {
                "runtime_release": runtime_release,
                "manifest_snapshot_hash": manifest_snapshot_hash,
                "model_profile": model_profile,
                "prompt_version": prompt_version,
            },
        }


class GroundingVerdict(StrEnum):
    VALID = "valid"
    CORRECTABLE = "correctable"
    UNSAFE = "unsafe"


class GroundingOutcome(BaseModel):
    """The result of validation: a verdict, a stable reason, and a safe hint.

    The correction hint is model-facing and must contain no schema, validator,
    exception, or fact-catalog text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: GroundingVerdict
    reason_code: str
    correction_hint: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.verdict is GroundingVerdict.VALID


class EvidenceStateRequirement(BaseModel):
    """The honest state and disclosure the observed evidence forces, if any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AnswerStatus | None = None
    disclosure: str | None = None


def evidence_ref_key(reference: Mapping[str, Any]) -> str | None:
    """Return a stable citation key for one sanitized evidence reference."""

    if not isinstance(reference, Mapping):
        return None
    for key in _REF_ID_KEYS:
        value = reference.get(key)
        if value not in (None, ""):
            return str(value)
    source_type = reference.get("source_type")
    for key in _REF_COMPOSITE_KEYS:
        value = reference.get(key)
        if value not in (None, ""):
            return f"{source_type}:{value}" if source_type else str(value)
    return None


class GroundingValidator:
    """Validate a grounded answer against the turn's allowed evidence set."""

    def allowed_evidence(self, envelopes: Iterable[ToolEnvelope]) -> set[str]:
        """Assemble the allowed-evidence reference set from executed reads.

        Only references produced by the turn's real read observations are
        allowed. Unauthorized or unknown references are never in the set and
        therefore cannot be cited.
        """

        allowed: set[str] = set()
        for envelope in envelopes:
            for reference in envelope.evidence_refs:
                key = evidence_ref_key(reference)
                if key is not None:
                    allowed.add(key)
        return allowed

    def required_state(
        self, envelopes: Sequence[ToolEnvelope]
    ) -> EvidenceStateRequirement:
        """Derive a forced no-evidence/failure state from observed reads.

        Grounded/partial/conflicting answers depend on the cited evidence and
        the model's honest declaration, so they are not forced here. Denial,
        unavailability, failure, and empty-but-successful reads are derived
        deterministically from the stable Slice 2 envelope statuses.
        """

        if not envelopes:
            return EvidenceStateRequirement()
        statuses = [envelope.status for envelope in envelopes]
        # Any executed read that returned evidence means the turn is answerable
        # from evidence; do not force a no-evidence state.
        if any(envelope.evidence_refs for envelope in envelopes):
            return EvidenceStateRequirement()
        # Precedence follows the safety-first grading order: denial/scope first,
        # then unavailability, then failure, then empty success.
        if any(status == "denied" for status in statuses):
            return EvidenceStateRequirement(
                status=AnswerStatus.SCOPE_DENIED, disclosure=DISCLOSURE_SCOPE_DENIED
            )
        if any(status == "unavailable" for status in statuses):
            return EvidenceStateRequirement(
                status=AnswerStatus.SOURCE_UNAVAILABLE,
                disclosure=DISCLOSURE_SOURCE_UNAVAILABLE,
            )
        if any(status == "failed" for status in statuses):
            return EvidenceStateRequirement(
                status=AnswerStatus.TOOL_FAILED, disclosure=DISCLOSURE_TOOL_FAILED
            )
        if all(status in {"succeeded", "triggered"} for status in statuses):
            return EvidenceStateRequirement(
                status=AnswerStatus.NO_EVIDENCE, disclosure=DISCLOSURE_NO_EVIDENCE
            )
        return EvidenceStateRequirement()

    def validate(
        self,
        answer: GroundedAnswer,
        *,
        allowed: set[str],
        required_disclosures: frozenset[str] = frozenset(),
        requirement: EvidenceStateRequirement | None = None,
    ) -> GroundingOutcome:
        """Validate one grounded answer. It is never mutated."""

        # 1. Internal-shaped output is a hard safety failure, not correctable by
        #    re-citing. Reuse the shared leak regexes.
        stripped = answer.content.strip()
        if _INTERNAL_ID_RE.search(answer.content) or (
            stripped and _RAW_PAYLOAD_RE.match(stripped)
        ):
            return GroundingOutcome(
                verdict=GroundingVerdict.UNSAFE,
                reason_code="internal_output_detected",
                correction_hint=None,
            )

        # 2. Every cited reference must be in the allowed evidence set.
        unknown = answer.cited_refs - set(allowed)
        if unknown:
            return GroundingOutcome(
                verdict=GroundingVerdict.CORRECTABLE,
                reason_code="citation_not_in_allowed_evidence",
                correction_hint=(
                    "Cite only evidence returned by the tools you called this "
                    "turn. Remove any reference that did not come from a result."
                ),
            )

        # 3. A forced evidence state must be honestly declared with disclosure.
        forced = requirement or EvidenceStateRequirement()
        needed_disclosures = set(required_disclosures)
        if forced.status is not None:
            if answer.answer_status is not forced.status:
                return GroundingOutcome(
                    verdict=GroundingVerdict.CORRECTABLE,
                    reason_code="answer_status_mismatch",
                    correction_hint=_state_hint(forced.status),
                )
            if forced.disclosure is not None:
                needed_disclosures.add(forced.disclosure)

        present = set(answer.disclosures)
        missing = needed_disclosures - present
        if missing:
            return GroundingOutcome(
                verdict=GroundingVerdict.CORRECTABLE,
                reason_code="missing_required_disclosure",
                correction_hint=(
                    "State the honest limitation for this result before "
                    "presenting the answer."
                ),
            )

        # 4. A materially factual grounded answer must cite evidence when
        #    evidence exists. Never auto-inject unused evidence: correct instead.
        if (
            answer.answer_status is AnswerStatus.GROUNDED
            and allowed
            and not answer.cited_refs
        ):
            return GroundingOutcome(
                verdict=GroundingVerdict.CORRECTABLE,
                reason_code="grounded_requires_citation",
                correction_hint=(
                    "Support each factual claim by citing the specific evidence "
                    "it came from, or lower the answer status if unsupported."
                ),
            )

        # 5. A declared conflict must actually cite the conflicting sources.
        if answer.answer_status is AnswerStatus.CONFLICTING and len(answer.cited_refs) < 2:
            return GroundingOutcome(
                verdict=GroundingVerdict.CORRECTABLE,
                reason_code="conflict_requires_multiple_sources",
                correction_hint=(
                    "Name and cite the distinct sources that conflict, or choose "
                    "a status that matches the evidence."
                ),
            )

        return GroundingOutcome(
            verdict=GroundingVerdict.VALID, reason_code="grounded", correction_hint=None
        )


def _state_hint(status: AnswerStatus) -> str:
    hints = {
        AnswerStatus.NO_EVIDENCE: (
            "No relevant evidence was found in the searched scope. Say so plainly "
            "and offer to refine the search."
        ),
        AnswerStatus.SOURCE_UNAVAILABLE: (
            "The source could not be reached. Say it is unavailable; do not imply "
            "the fact does not exist."
        ),
        AnswerStatus.SCOPE_DENIED: (
            "This is outside the authorized scope. Explain the boundary; do not "
            "suggest a way around it."
        ),
        AnswerStatus.TOOL_FAILED: (
            "The lookup failed. Explain that plainly and say whether retrying is "
            "reasonable."
        ),
        AnswerStatus.CONFLICTING: (
            "The evidence conflicts. Describe the conflict and name the sources "
            "without silently choosing one."
        ),
        AnswerStatus.PARTIAL: (
            "Only part of the question is supported. Answer that part and label "
            "the unsupported remainder."
        ),
    }
    return hints.get(status, "Match the answer status to the evidence honestly.")


def required_disclosure_for(status: AnswerStatus) -> str | None:
    """Return the required disclosure code for an honest state, if any."""

    return _STATUS_DISCLOSURE.get(status)


__all__ = [
    "AnswerStatus",
    "DISCLOSURE_CONFLICTING",
    "DISCLOSURE_DENIED",
    "DISCLOSURE_NO_EVIDENCE",
    "DISCLOSURE_PARTIAL",
    "DISCLOSURE_SCOPE_DENIED",
    "DISCLOSURE_SOURCE_UNAVAILABLE",
    "DISCLOSURE_TOOL_FAILED",
    "EvidenceStateRequirement",
    "GroundedAnswer",
    "GroundingClaim",
    "GroundingOutcome",
    "GroundingValidator",
    "GroundingVerdict",
    "evidence_ref_key",
    "required_disclosure_for",
]
