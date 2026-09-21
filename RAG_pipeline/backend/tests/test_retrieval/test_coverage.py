"""What retrieval covered, and whether the model is told when it did not.

The failure these tests pin is not a crash. It is a well-formed, confident answer
about five of nine things, with nothing anywhere in the pipeline saying that four
were missing. Every case here is about a loss being *reported* rather than
absorbed.
"""

from __future__ import annotations

from src.generation.prompt_builder import (
    SYSTEM_PROMPT,
    build_messages,
    coverage_instruction,
)
from src.retrieval.coverage import (
    ContextFit,
    EntityCoverage,
    RetrievalCoverage,
    build_retrieval_coverage,
    entity_coverage,
)
from src.retrieval.entity_matcher import EntityMatch
from src.schemas import Source


def _source(
    text: str = "body", *, document_id: int = 1, unit: str | None = None
) -> Source:
    return Source(
        document_id=document_id,
        filename="doc.md",
        chunk_index=0,
        score=0.5,
        text=text,
        unit=unit,
    )


def _match(canonical: str, *, unit_name: str | None = None) -> EntityMatch:
    return EntityMatch(
        alias=canonical,
        canonical_name=canonical,
        entity_type="job",
        project_id="proj",
        unit_name=unit_name,
        source_path=None,
        source_artifact=None,
        confidence=1.0,
    )


# --- ContextFit -------------------------------------------------------------


def test_dropped_is_derived_and_never_negative() -> None:
    assert ContextFit(offered=5, used=2).dropped == 3
    # A caller that mis-reports cannot produce a negative "dropped" count that
    # would then render as prose.
    assert ContextFit(offered=1, used=4).dropped == 0


def test_sections_sum_rather_than_overwrite() -> None:
    merged = ContextFit(offered=3, used=2, truncated=1).merged_with(
        ContextFit(offered=4, used=4, truncated=0)
    )
    assert (merged.offered, merged.used, merged.truncated) == (7, 6, 1)


# --- Entity coverage --------------------------------------------------------


def test_a_named_entity_with_no_passage_is_reported_missing() -> None:
    result = entity_coverage(
        [_match("tDBOutput_1"), _match("sjItem_OnRamp")],
        [_source("the tDBOutput_1 component writes rows")],
        index_available=True,
    )

    assert result.named == ("tDBOutput_1", "sjItem_OnRamp")
    assert result.covered == ("tDBOutput_1",)
    assert result.missing == ("sjItem_OnRamp",)


def test_the_unit_payload_counts_as_coverage_without_a_text_mention() -> None:
    # The chunk is *about* the job; the ingester said so structurally. Requiring
    # the name to also appear in the prose would report a covered entity as
    # missing and put a false caveat in the answer.
    result = entity_coverage(
        [_match("sjItem_OnRamp", unit_name="sjItem_OnRamp")],
        [_source("This flow loads the staging table.", unit="sjItem_OnRamp")],
        index_available=True,
    )

    assert result.missing == ()


def test_a_longer_identifier_does_not_cover_a_shorter_one() -> None:
    # Boundary-aware, not substring: tDBOutput_11 is a different component, and
    # counting it as coverage of tDBOutput_1 would suppress a true caveat.
    result = entity_coverage(
        [_match("tDBOutput_1")],
        [_source("tDBOutput_11 writes the reject rows")],
        index_available=True,
    )

    assert result.missing == ("tDBOutput_1",)


def test_the_same_entity_matched_twice_is_named_once() -> None:
    result = entity_coverage(
        [_match("tMap_1"), _match("tMap_1", unit_name="job_a")],
        [],
        index_available=True,
    )

    assert result.named == ("tMap_1",)


def test_an_unsynced_project_is_not_reported_as_fully_covered() -> None:
    # "We cannot tell" and "nothing is missing" must not render identically.
    result = entity_coverage([], [_source()], index_available=False)

    assert result.index_available is False
    assert result.named == ()
    assert result.missing == ()


# --- The assembled report ---------------------------------------------------


def test_documents_are_counted_across_both_role_partitions() -> None:
    coverage = build_retrieval_coverage(
        sources=[_source(document_id=1), _source(document_id=2)],
        auxiliary_sources=[_source(document_id=3), _source(document_id=1)],
        referenced_documents=2,
    )

    assert coverage.retrieved_chunks == 4
    assert coverage.retrieved_documents == 3
    assert coverage.unreferenced_documents == 1


def test_a_complete_retrieval_reports_no_gap() -> None:
    coverage = build_retrieval_coverage(
        sources=[_source(document_id=1)],
        referenced_documents=1,
        entities=EntityCoverage(
            named=("job_a",), covered=("job_a",), index_available=True
        ),
        context=ContextFit(offered=1, used=1),
    )

    assert coverage.has_gap is False


def test_every_kind_of_loss_opens_the_gap() -> None:
    base = dict(sources=[_source(document_id=1)], referenced_documents=1)

    dropped = build_retrieval_coverage(**base, context=ContextFit(offered=3, used=1))
    truncated = build_retrieval_coverage(
        **base, context=ContextFit(offered=1, used=1, truncated=1)
    )
    missing_entity = build_retrieval_coverage(
        **base,
        entities=EntityCoverage(named=("job_a",), index_available=True),
    )
    unreferenced = build_retrieval_coverage(
        sources=[_source(document_id=1), _source(document_id=2)],
        referenced_documents=1,
    )

    assert dropped.has_gap
    assert truncated.has_gap
    assert missing_entity.has_gap
    assert unreferenced.has_gap


def test_the_log_line_carries_no_prose() -> None:
    coverage = build_retrieval_coverage(
        sources=[_source(document_id=1, text="a client secret in the chunk")],
        referenced_documents=1,
        entities=EntityCoverage(named=("job_a",), index_available=True),
        context=ContextFit(offered=1, used=1),
    )

    line = coverage.log_line()
    assert "client secret" not in line
    assert "entities_missing=1" in line
    assert "entity_index=yes" in line


# --- The disclosure the model actually receives ------------------------------


def test_a_complete_retrieval_produces_no_instruction() -> None:
    # Silence is the point: a block that fires every turn is one the model learns
    # to skip, and there is no honest way to assert "coverage is complete".
    assert coverage_instruction(None) is None
    assert (
        coverage_instruction(
            RetrievalCoverage(
                retrieved_documents=1,
                referenced_documents=1,
                context=ContextFit(offered=1, used=1),
            )
        )
        is None
    )


def test_the_instruction_names_the_missing_entities_and_forbids_absence_claims() -> None:
    instruction = coverage_instruction(
        RetrievalCoverage(
            retrieved_chunks=5,
            retrieved_documents=5,
            referenced_documents=5,
            entities=EntityCoverage(
                named=("iface_a", "iface_b"),
                covered=("iface_a",),
                index_available=True,
            ),
            context=ContextFit(offered=5, used=5),
        )
    )

    assert instruction is not None
    assert "iface_b" in instruction
    assert "iface_a" not in instruction.replace("iface_b", "")
    assert "never say it does not exist" in instruction
    # The reader is a consultant: our plumbing vocabulary stays out of the answer.
    assert "Do not mention retrieval" in instruction


def test_the_disclosure_reaches_the_prompt_the_model_is_sent() -> None:
    coverage = RetrievalCoverage(
        retrieved_chunks=2,
        retrieved_documents=2,
        referenced_documents=2,
        entities=EntityCoverage(
            named=("iface_b",), covered=(), index_available=True
        ),
    )

    messages = build_messages(
        "compare the interfaces", [_source("iface_a maps orders")], coverage=coverage
    )

    assert messages[0]["content"] == SYSTEM_PROMPT
    disclosure = next(
        message
        for message in messages
        if "Coverage of this question" in message["content"]
    )
    assert disclosure["role"] == "system"
    assert "iface_b" in disclosure["content"]
    # It precedes the question, so the constraint is in place before the model
    # reads what it is being asked.
    assert messages.index(disclosure) < len(messages) - 1


def test_a_complete_retrieval_adds_no_message_to_the_prompt() -> None:
    messages = build_messages(
        "what is X?",
        [_source("X is a thing")],
        coverage=build_retrieval_coverage(
            sources=[_source("X is a thing")], referenced_documents=1
        ),
    )

    assert not any("Coverage of this question" in m["content"] for m in messages)


def test_the_token_budget_verdict_overrides_what_the_caller_guessed() -> None:
    """The caller cannot know the budget's verdict, so `build_messages` corrects it.

    A `context` supplied from outside is replaced, not merged: only this function
    has run the arithmetic that decides what actually fits.
    """

    long_text = "word " * 40_000
    coverage = build_retrieval_coverage(
        sources=[_source(long_text), _source("second")],
        referenced_documents=1,
        # Deliberately wrong: claims everything fit.
        context=ContextFit(offered=2, used=2, truncated=0),
    )

    messages = build_messages("what is X?", [_source(long_text), _source("second")], coverage=coverage)

    disclosure = next(
        message
        for message in messages
        if "Coverage of this question" in message["content"]
    )
    assert "did not fit" in disclosure["content"]


def test_the_instruction_reports_dropped_and_truncated_separately() -> None:
    instruction = coverage_instruction(
        RetrievalCoverage(
            retrieved_chunks=9,
            retrieved_documents=4,
            referenced_documents=4,
            context=ContextFit(offered=9, used=6, truncated=2),
        )
    )

    assert instruction is not None
    assert "3 of 9 retrieved passages did not fit" in instruction
    assert "2 passage(s) are present but cut off" in instruction
