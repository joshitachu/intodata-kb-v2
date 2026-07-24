from __future__ import annotations

from src.harness.grounding import (
    DISCLOSURE_NO_EVIDENCE,
    DISCLOSURE_DENIED,
    DISCLOSURE_SCOPE_DENIED,
    DISCLOSURE_SOURCE_UNAVAILABLE,
    DISCLOSURE_TOOL_FAILED,
    AnswerStatus,
    GroundedAnswer,
    GroundingClaim,
    GroundingValidator,
    GroundingVerdict,
    evidence_ref_key,
)
from src.harness.semantic_tools import ToolEnvelope


def _envelope(status: str, refs: list[dict] | None = None) -> ToolEnvelope:
    return ToolEnvelope(status=status, summary="s", evidence_refs=refs or [])


def _answer(**kwargs) -> GroundedAnswer:
    base = {
        "content": "The project ingests orders nightly.",
        "citations": [],
        "disclosures": [],
        "answer_status": AnswerStatus.GROUNDED,
    }
    base.update(kwargs)
    return GroundedAnswer(**base)


VALIDATOR = GroundingValidator()


def test_allowed_evidence_is_assembled_from_executed_reads() -> None:
    envelopes = [
        _envelope("succeeded", [{"id": "doc:1"}, {"source_type": "rag", "uri": "u1"}]),
        _envelope("succeeded", [{"evidence_id": "ev:2"}]),
    ]
    allowed = VALIDATOR.allowed_evidence(envelopes)
    assert allowed == {"doc:1", "rag:u1", "ev:2"}


def test_citation_to_allowed_ref_is_valid() -> None:
    allowed = {"doc:1"}
    answer = _answer(
        citations=[GroundingClaim(claim_key="orders", evidence_refs=["doc:1"])]
    )
    outcome = VALIDATOR.validate(answer, allowed=allowed)
    assert outcome.verdict is GroundingVerdict.VALID


def test_citation_to_unknown_ref_is_correctable_not_persisted() -> None:
    answer = _answer(
        citations=[GroundingClaim(claim_key="orders", evidence_refs=["doc:UNKNOWN"])]
    )
    outcome = VALIDATOR.validate(answer, allowed={"doc:1"})
    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "citation_not_in_allowed_evidence"


def test_unused_evidence_is_never_auto_injected() -> None:
    # A valid answer citing only one of several allowed refs is accepted as-is;
    # the validator does not add the unused refs.
    answer = _answer(
        citations=[GroundingClaim(claim_key="orders", evidence_refs=["doc:1"])]
    )
    outcome = VALIDATOR.validate(answer, allowed={"doc:1", "doc:2", "doc:3"})
    assert outcome.verdict is GroundingVerdict.VALID
    assert answer.cited_refs == {"doc:1"}


def test_internal_shaped_output_is_unsafe() -> None:
    for canary in (
        "The capability rag.docs.search_evidence returned results.",
        '{"status": "succeeded", "data": {"raw": true}}',
    ):
        outcome = VALIDATOR.validate(_answer(content=canary), allowed=set())
        assert outcome.verdict is GroundingVerdict.UNSAFE
        assert outcome.reason_code == "internal_output_detected"


def test_internal_correction_narration_is_unsafe() -> None:
    outcome = VALIDATOR.validate(
        _answer(
            content=(
                "I don't have any tool result to report limitations for—my "
                "previous response was a greeting with no tool calls. I'll "
                "restate it clearly."
            )
        ),
        allowed=set(),
    )
    assert outcome.verdict is GroundingVerdict.UNSAFE
    assert outcome.reason_code == "internal_output_detected"


def test_visible_tool_names_and_evidence_keys_are_correctable() -> None:
    for content in (
        "The search_documentation output lists ten jobs.",
        "The project is ready (`metadata:project_detail:alitalend`).",
        "See mcp-doc:alitalend:02_COMPONENT_INVENTORY.md.",
        "The relevant passage is doc:392:3.",
    ):
        outcome = VALIDATOR.validate(_answer(content=content), allowed=set())
        assert outcome.verdict is GroundingVerdict.CORRECTABLE
        assert outcome.reason_code == "visible_implementation_reference"


def test_unparsed_grounding_marker_is_unsafe() -> None:
    outcome = VALIDATOR.validate(
        _answer(content="Visible answer.\n```json\nnexus-grounding\nnot-json\n```"),
        allowed=set(),
    )
    assert outcome.verdict is GroundingVerdict.UNSAFE
    assert outcome.reason_code == "internal_output_detected"


def test_grounded_with_evidence_but_no_citation_is_correctable() -> None:
    answer = _answer(answer_status=AnswerStatus.GROUNDED, citations=[])
    outcome = VALIDATOR.validate(answer, allowed={"doc:1"})
    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "grounded_requires_citation"


def test_grounded_no_evidence_available_needs_no_citation() -> None:
    # A direct answer (greeting/capability) with no evidence executed is valid.
    answer = _answer(content="Hello, I can help with your projects.", citations=[])
    outcome = VALIDATOR.validate(answer, allowed=set())
    assert outcome.verdict is GroundingVerdict.VALID


def test_required_state_maps_each_no_evidence_condition() -> None:
    cases = {
        "succeeded": (AnswerStatus.NO_EVIDENCE, DISCLOSURE_NO_EVIDENCE),
        "unavailable": (AnswerStatus.SOURCE_UNAVAILABLE, DISCLOSURE_SOURCE_UNAVAILABLE),
        "denied": (AnswerStatus.SCOPE_DENIED, DISCLOSURE_SCOPE_DENIED),
        "failed": (AnswerStatus.TOOL_FAILED, DISCLOSURE_TOOL_FAILED),
    }
    for status, (expected_status, disclosure) in cases.items():
        requirement = VALIDATOR.required_state([_envelope(status, refs=[])])
        assert requirement.status is expected_status
        assert requirement.disclosure == disclosure


def test_forced_state_requires_matching_status_and_disclosure() -> None:
    requirement = VALIDATOR.required_state([_envelope("succeeded", refs=[])])
    # Wrong status -> correctable.
    wrong = _answer(answer_status=AnswerStatus.GROUNDED, content="It ingests orders.")
    outcome = VALIDATOR.validate(wrong, allowed=set(), requirement=requirement)
    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "answer_status_mismatch"
    # Right status but missing disclosure -> correctable.
    missing = _answer(
        answer_status=AnswerStatus.NO_EVIDENCE,
        content="I found nothing relevant.",
        disclosures=[],
    )
    outcome = VALIDATOR.validate(missing, allowed=set(), requirement=requirement)
    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "missing_required_disclosure"
    # Right status and disclosure -> valid.
    honest = _answer(
        answer_status=AnswerStatus.NO_EVIDENCE,
        content="I found no relevant evidence in the searched scope.",
        disclosures=[DISCLOSURE_NO_EVIDENCE],
    )
    outcome = VALIDATOR.validate(honest, allowed=set(), requirement=requirement)
    assert outcome.verdict is GroundingVerdict.VALID


def test_conflicting_status_requires_multiple_sources() -> None:
    answer = _answer(
        answer_status=AnswerStatus.CONFLICTING,
        citations=[GroundingClaim(claim_key="a", evidence_refs=["doc:1"])],
    )
    outcome = VALIDATOR.validate(answer, allowed={"doc:1", "doc:2"})
    assert outcome.verdict is GroundingVerdict.CORRECTABLE
    assert outcome.reason_code == "conflict_requires_multiple_sources"


def test_evidence_present_does_not_force_no_evidence_state() -> None:
    requirement = VALIDATOR.required_state([_envelope("succeeded", [{"id": "doc:1"}])])
    assert requirement.status is None


def test_plan_control_projection_is_neutral_to_evidence_state() -> None:
    projection = ToolEnvelope(
        status="succeeded",
        summary="Objective topology projected without persistence.",
        data={"plan_required": False},
        evidence_state_relevant=False,
    )
    assert VALIDATOR.allowed_evidence([projection]) == set()
    assert VALIDATOR.required_state([projection]).status is None


def test_successful_execution_receipt_does_not_force_an_evidence_limitation() -> None:
    execution = ToolEnvelope(
        status="succeeded",
        summary="Created private Analysis Pack Incident Review version 1.0.0.",
        receipt_ref={"idempotency_key": "pack-create-1"},
        evidence_state_relevant=False,
        execution_state_relevant=True,
    )

    requirement = VALIDATOR.required_state([execution])
    answer = _answer(
        content="The Incident Review Pack was created successfully.",
        citations=[],
        disclosures=[],
    )

    assert requirement.status is None
    assert VALIDATOR.allowed_evidence([execution]) == set()
    assert (
        VALIDATOR.validate(answer, allowed=set(), requirement=requirement).verdict
        is GroundingVerdict.VALID
    )


def test_denied_execution_requires_the_typed_denial_state() -> None:
    execution = ToolEnvelope(
        status="denied",
        summary="The requested action was denied.",
        evidence_state_relevant=False,
        execution_state_relevant=True,
    )

    requirement = VALIDATOR.required_state([execution])

    assert requirement.status is AnswerStatus.DENIED
    assert requirement.disclosure == DISCLOSURE_DENIED


def test_to_structured_answer_is_additive_v2_payload() -> None:
    answer = _answer(
        citations=[GroundingClaim(claim_key="orders", evidence_refs=["doc:1"])],
        disclosures=[DISCLOSURE_NO_EVIDENCE],
    )
    payload = answer.to_structured_answer(
        runtime_release="rel",
        manifest_snapshot_hash="hash",
        model_profile="kimi/kimi-k2.5",
        prompt_version="v1",
    )
    assert payload["response_type"] == "agent_response_v2_grounded"
    assert payload["content"] == answer.content
    assert payload["answer_status"] == "grounded"
    assert payload["citations"][0]["evidence_refs"] == ["doc:1"]
    assert payload["attribution"]["model_profile"] == "kimi/kimi-k2.5"


def test_evidence_ref_key_prefers_id_then_composite() -> None:
    assert evidence_ref_key({"id": "x", "uri": "u"}) == "x"
    assert evidence_ref_key({"evidence_id": "e"}) == "e"
    assert evidence_ref_key({"source_type": "rag", "uri": "u"}) == "rag:u"
    assert evidence_ref_key({"score": 0.5}) is None
