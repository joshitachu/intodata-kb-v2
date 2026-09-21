"""Reading a document in document order, and knowing what you read.

The failure these cover is one measured answer: asked how many fields an
uploaded mapping had, the agent used a tool called `read_document`, received
eight similarity-ranked chunks of a 31-row table, and told a client "30". No
whole-document read existed -- one path only served generated docs under
`output/{project}/docs/`, and the other, `document_texts`, held 3 rows against
551 documents.

Three properties carry the fix and each is asserted rather than described:

* Reading is in DOCUMENT order and says so, so a count taken from it is a count
  of the document.
* Nothing is truncated without a marker. The serializer cuts an evidence item's
  summary at ``EVIDENCE_SUMMARY_MAX_CHARS`` and leaves no trace, so every piece
  handed over is split below that and labelled with its position.
* Every result, including every failure, says what it covered. An empty or
  refused read that does not say why is what produced the wrong answer in the
  first place.

A fourth property was added after the capability had been measured in use. Asked
to walk through one integration, an agent read the outline and then made sixteen
single-section calls; an earlier run of the same question made thirty-four. The
tool itself had told it to -- the oversize-full result said to read the sections
"one at a time" -- and each extra call re-sent the whole conversation, which is
where the cost was. So: a chosen set of sections must be reachable in ONE call,
that call must survive a bad identifier, and no guidance string may send a reader
back to reading one section per call.
"""

from __future__ import annotations

import datetime
import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document import Document
from src.db.models.document_text import (
    DOCUMENT_TEXT_SOURCE_PARSED,
    DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
    DocumentTextRow,
)
from src.harness.capability_handlers import _MAX_SCOPE_LIST_ENTRIES
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
)
from src.harness.evidence import (
    _FILENAME_CANDIDATE_LIMIT,
    _FULL_READ_MAX_CHARS,
    _SECTIONS_READ_MAX_CHARS,
    _filename_matches,
    _resolve_filename_candidates,
    _select_sections,
    build_registered_read_adapters,
    read_document_text_evidence,
    split_document_sections,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import (
    DOCUMENT_FULL_TEXT_CAPABILITY,
    EVIDENCE_SUMMARY_MAX_CHARS,
    EvidenceQueryResult,
)
from src.harness.registry import base_capability_registry
from src.harness.trusted_answer import TrustedAnswerSink

PROJECT = "acme"

MAPPING_DOC = """# Interface contract

Exchanged nightly.

## Field mapping

| source | target |
| --- | --- |
| A | a |
| B | b |

## Selection

Only ABCLASS01 = 'FMD' rows are selected.
"""

#: Five sections, s1 to s5, so a batch can be asked for out of order and still
#: be checked against document order.
CONTRACT_DOC = """# Integration

Exchanged nightly between two systems.

## Endpoints

Endpoint detail ALPHA.

## Field mapping

| source | target |
| --- | --- |
| A | a |

## Error handling

Retry detail BRAVO.

## Scheduling

Runs nightly, detail CHARLIE.
"""


def _body_of(result: EvidenceQueryResult, section_id: str) -> str:
    """Reassemble one section's text out of the parts it was split into."""

    return "".join(
        item.summary.split("\n\n", 1)[1]
        for item in result.items
        if item.metadata.get("section_id") == section_id
    )


async def _store(
    db: AsyncSession,
    *,
    text: str,
    filename: str = "ortec_mapping.md",
    project_id: str | None = PROJECT,
    role: str = "source",
    source: str = DOCUMENT_TEXT_SOURCE_PARSED,
    store_text: bool = True,
    deleted_at: datetime.datetime | None = None,
) -> int:
    document = Document(
        filename=filename,
        file_type="md",
        document_role=role,
        project_id=project_id,
        deleted_at=deleted_at,
    )
    db.add(document)
    await db.flush()
    if store_text:
        db.add(
            DocumentTextRow(
                document_id=document.id,
                text=text,
                char_count=len(text),
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                source=source,
            )
        )
        await db.flush()
    return document.id


# -- splitting ---------------------------------------------------------------


def test_splitting_is_lossless() -> None:
    # The property that lets section mode and full mode be one read at two
    # granularities rather than two implementations that can disagree.
    sections = split_document_sections(MAPPING_DOC)

    assert "".join(section.text for section in sections) == MAPPING_DOC


def test_heading_paths_carry_their_ancestors() -> None:
    sections = split_document_sections(MAPPING_DOC)
    paths = [section.path for section in sections]

    assert paths == [
        "Interface contract",
        "Interface contract > Field mapping",
        "Interface contract > Selection",
    ]


def test_a_document_without_headings_is_one_section() -> None:
    # A flat text genuinely has one section, and saying so keeps every caller on
    # one code path instead of branching on "is this markdown".
    sections = split_document_sections("no headings at all\nsecond line\n")

    assert len(sections) == 1
    assert sections[0].level == 0
    assert sections[0].path == ""


def test_content_before_the_first_heading_is_kept() -> None:
    sections = split_document_sections("preamble\n\n# Title\n\nbody\n")

    assert sections[0].text == "preamble\n\n"
    assert sections[1].path == "Title"


# -- outline -----------------------------------------------------------------


async def test_outline_is_the_default_and_names_every_section(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
    )

    body = "\n".join(item.summary for item in result.items)
    assert result.status == "succeeded"
    assert "Interface contract > Field mapping" in body
    assert "Interface contract > Selection" in body
    # The outline is structure, not content: it must not be mistaken for a read.
    assert result.scope["complete"] is False
    assert result.scope["section_count"] == 3


async def test_the_size_signal_is_stated_before_the_document_is_spent(
    db_session: AsyncSession,
) -> None:
    # "A 120 KB document is ~30k tokens" is only actionable if the model is told
    # the number before it asks for the whole thing.
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="outline",
    )

    assert result.scope["total_chars"] == len(MAPPING_DOC)
    assert result.scope["estimated_tokens"] == len(MAPPING_DOC) // 4
    assert str(len(MAPPING_DOC)) in result.scope["searched_what"]


# -- section -----------------------------------------------------------------


@pytest.mark.parametrize(
    "asked_for",
    [
        "s2",
        "Interface contract > Field mapping",
        "Field mapping",
        "field MAPPING",
        "contract > Field mapping",
    ],
)
async def test_a_section_resolves_from_any_spelling_the_outline_supports(
    db_session: AsyncSession, asked_for: str
) -> None:
    # A model that saw `A > B > C` will variously ask for the path, the leaf and
    # the tail. Refusing two of the three costs a call to learn a spelling rule.
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="section",
        section=asked_for,
    )

    assert result.status == "succeeded"
    assert result.scope["complete"] is True
    assert result.scope["section_id"] == "s2"
    assert "| A | a |" in result.items[0].summary
    assert "| B | b |" in result.items[0].summary


async def test_a_section_read_is_complete_and_says_the_rest_was_not_read(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="section",
        section="Selection",
    )

    assert "ABCLASS01 = 'FMD'" in result.items[0].summary
    assert "Nothing inside this section was left out" in result.scope["searched_what"]
    assert "rest of the document was not read" in result.scope["searched_what"]


async def test_an_unknown_section_lists_the_ones_that_exist(
    db_session: AsyncSession,
) -> None:
    # The alternative -- "no such section" -- costs a call and teaches nothing.
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="section",
        section="Nonexistent",
    )

    assert result.status == "failed"
    assert any(
        "Interface contract > Field mapping" in item
        for item in result.scope["available_sections"]
    )


# -- several sections in one call --------------------------------------------


async def test_a_batch_returns_every_named_section_complete(
    db_session: AsyncSession,
) -> None:
    # The measured failure was sixteen calls for sixteen sections. One call has
    # to return all of them, each whole, or nothing changes.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s2", "s4", "s5"],
    )

    assert result.status == "succeeded"
    assert result.scope["complete"] is True
    assert result.scope["mode"] == "sections"
    assert "ALPHA" in _body_of(result, "s2")
    assert "BRAVO" in _body_of(result, "s4")
    assert "CHARLIE" in _body_of(result, "s5")
    # And only what was asked for: a batch is not an excuse to return the rest.
    assert _body_of(result, "s3") == ""


async def test_a_batch_comes_back_in_document_order_whatever_order_was_asked(
    db_session: AsyncSession,
) -> None:
    # A procedure or a numbered mapping read out of order is as wrong as ranked
    # chunks, which is the failure this whole capability exists to remove.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s5", "s2", "Field mapping"],
    )

    assert [item.metadata["section_id"] for item in result.items] == ["s2", "s3", "s5"]
    assert result.scope["sections_read"] == [
        "s2: Integration > Endpoints",
        "s3: Integration > Field mapping",
        "s5: Integration > Scheduling",
    ]


async def test_the_same_section_asked_for_twice_is_read_once(
    db_session: AsyncSession,
) -> None:
    # Observed for real: a run asked for s23 twice in one breath. Paying for it
    # twice, and handing the model the same rows twice to count, helps nobody.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s3", "Field mapping", "Integration > Field mapping", "s3"],
    )

    assert [item.metadata["section_id"] for item in result.items] == ["s3"]
    assert result.scope["sections_read"] == ["s3: Integration > Field mapping"]


async def test_one_unmatched_id_does_not_sink_the_rest_of_the_batch(
    db_session: AsyncSession,
) -> None:
    # Failing three good sections because a fourth id was wrong would teach the
    # model that batching is risky and send it straight back to one call each.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s2", "Nonexistent", "s4"],
    )

    assert result.status == "succeeded"
    assert "ALPHA" in _body_of(result, "s2")
    assert "BRAVO" in _body_of(result, "s4")
    assert result.scope["sections_not_found"] == ["Nonexistent"]
    # Not complete, because something asked for is missing, and the reader is
    # told what the document does contain so the retry is not another guess.
    assert result.scope["complete"] is False
    assert any(
        "Integration > Endpoints" in entry
        for entry in result.scope["available_sections"]
    )


async def test_a_batch_that_matches_nothing_fails_with_the_sections_that_exist(
    db_session: AsyncSession,
) -> None:
    # Succeeding with zero items and no explanation is the absence-without-a-
    # reason shape that published a wrong client-facing answer once already.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["Nonexistent", "s99"],
    )

    assert result.status == "failed"
    assert result.items == []
    assert result.scope["sections_not_found"] == ["Nonexistent", "s99"]
    assert any(
        "Integration > Scheduling" in entry
        for entry in result.scope["available_sections"]
    )


async def test_the_budget_truncates_between_sections_and_names_what_it_skipped(
    db_session: AsyncSession,
) -> None:
    # Completeness per section is the entire reason this capability exists, so
    # the budget may drop a section but may never cut one in half.
    # Sized from the budget rather than hardcoded, so the assertions below stay
    # true when the ceiling is retuned -- it already moved once, from 60k to
    # 28k, when the serialized envelope turned out to be 1.55x the text.
    # At 40% of the budget each, exactly two sections fit and the third cannot.
    _PADDING_LINE_CHARS = len("line 00000 of padding\n")
    lines = int(_SECTIONS_READ_MAX_CHARS * 0.4) // _PADDING_LINE_CHARS
    chunk = "\n".join(f"line {index:05d} of padding" for index in range(lines))
    assert _SECTIONS_READ_MAX_CHARS // 3 < len(chunk) < _SECTIONS_READ_MAX_CHARS // 2
    oversize = "".join(f"# Part {name}\n\n{chunk}\n" for name in "ABCD")
    document_id = await _store(db_session, text=oversize)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s1", "s2", "s3", "s4"],
    )

    assert result.status == "succeeded"
    assert result.scope["sections_read"] == ["s1: Part A", "s2: Part B"]
    assert result.scope["sections_omitted_for_size"] == ["s3: Part C", "s4: Part D"]
    assert result.scope["sections_read_limit_chars"] == _SECTIONS_READ_MAX_CHARS
    assert result.scope["returned_chars"] <= _SECTIONS_READ_MAX_CHARS
    assert result.scope["complete"] is False
    # Whole sections, not a prefix of the budget: the last line of the last
    # section that was returned is present.
    assert f"line {lines - 1:05d} of padding" in _body_of(result, "s2")


def test_the_first_match_is_returned_even_when_it_alone_exceeds_the_budget() -> None:
    # `mode="section"` would have handed this section over regardless of size,
    # so returning nothing here would make batching strictly worse than the
    # single read it is meant to replace.
    huge = "# Big\n\n" + ("x" * (_SECTIONS_READ_MAX_CHARS + 10)) + "\n"
    sections = split_document_sections(huge + "# Small\n\ny\n")
    assert len(sections) == 2

    selection = _select_sections(sections, ["s1", "s2"])

    assert selection.indexes == [0]
    assert selection.over_budget == [1]


def test_selection_orders_deduplicates_and_reports_in_one_pass() -> None:
    # The pure core of the batch: every rule that can be subtly wrong while each
    # individual section still looks right.
    sections = split_document_sections(CONTRACT_DOC)

    selection = _select_sections(
        sections, ["s5", "s2", "s2", "nope", "Field mapping"], budget=10_000
    )

    assert selection.indexes == [1, 2, 4]
    assert selection.unmatched == ["nope"]
    assert selection.over_budget == []
    assert selection.returned_chars == sum(
        len(sections[index].text) for index in (1, 2, 4)
    )


async def test_sections_overrides_mode(db_session: AsyncSession) -> None:
    # A list of what to read is more specific than a mode. Honouring the mode
    # instead would return an outline to a caller that already chose.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="outline",
        sections=["s4"],
    )

    assert result.scope["mode"] == "sections"
    assert "BRAVO" in _body_of(result, "s4")


async def test_batched_parts_carry_distinct_identifiers(
    db_session: AsyncSession,
) -> None:
    # Without a per-section key every section's first part would be
    # `doctext:{id}:1`, so anything keyed on the identifier would see one item.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        sections=["s1", "s2", "s3"],
    )

    identifiers = [item.id for item in result.items]
    assert len(identifiers) == len(set(identifiers))


async def test_the_batch_survives_the_wire_from_schema_to_reader(
    db_session: AsyncSession,
) -> None:
    # The function can accept `sections` and the capability still refuse it: the
    # executor validates arguments against the published schema, and the adapter
    # has to forward the ones it passes. Both are load-bearing, so both are
    # exercised here rather than assumed.
    document_id = await _store(db_session, text=CONTRACT_DOC)
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    result = await HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers=adapters,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            arguments={
                "project_id": PROJECT,
                "document_id": document_id,
                "sections": ["s2", "s4"],
            },
        ),
        CapabilityExecutionContext(
            user_id=7, conversation_id=41, project_scope=[PROJECT]
        ),
    )

    assert result.status == "succeeded"
    body = " ".join(item["summary"] for item in result.data["items"])
    assert "ALPHA" in body
    assert "BRAVO" in body


async def test_a_sections_argument_that_is_not_a_list_of_names_is_refused(
    db_session: AsyncSession,
) -> None:
    # The executor's schema check stops this on the advertised path, but the
    # adapter is reachable from any caller that assembles its own arguments.
    # Coercing the value would read the wrong part of the document silently.
    document_id = await _store(db_session, text=CONTRACT_DOC)
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    result = await adapters[DOCUMENT_FULL_TEXT_CAPABILITY.id](
        {
            "project_id": PROJECT,
            "document_id": document_id,
            "sections": "s2,s4",
        },
        CapabilityExecutionContext(
            user_id=7, conversation_id=41, project_scope=[PROJECT]
        ),
    )

    assert result.status == "failed"
    assert result.items == []
    assert "'sections' must be an array" in result.error_message


async def test_a_single_section_still_works_through_the_singular_argument(
    db_session: AsyncSession,
) -> None:
    # `section` is persisted in conversation history. A batched read is not a
    # reason to invalidate calls a model already made.
    document_id = await _store(db_session, text=CONTRACT_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="section",
        section="Error handling",
    )

    assert result.status == "succeeded"
    assert result.scope["complete"] is True
    assert result.scope["section_id"] == "s4"
    assert "BRAVO" in result.items[0].summary


# -- full --------------------------------------------------------------------


async def test_full_returns_the_document_verbatim_and_claims_completeness(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.scope["complete"] is True
    assert result.scope["returned_chars"] == len(MAPPING_DOC)
    # Every line of the document survives, which is the entire point: a count
    # taken from this is a count of the document.
    for line in MAPPING_DOC.splitlines():
        if line.strip():
            assert line in result.items[0].summary


async def test_a_long_document_is_split_into_ordered_parts_below_the_cut(
    db_session: AsyncSession,
) -> None:
    # The serializer truncates a summary at EVIDENCE_SUMMARY_MAX_CHARS with no
    # marker, so a piece larger than that loses its tail silently -- which is
    # the failure this capability exists to remove.
    long_text = "# Rows\n\n" + "".join(f"| row-{index:05d} | value |\n" for index in range(600))
    document_id = await _store(db_session, text=long_text)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="full",
    )

    assert len(result.items) > 1
    assert [item.metadata["part"] for item in result.items] == list(
        range(1, len(result.items) + 1)
    )
    for item in result.items:
        assert len(item.summary) <= EVIDENCE_SUMMARY_MAX_CHARS
        assert item.metadata["part_count"] == len(result.items)
    # And no row is lost or cut in half across the split.
    assembled = "".join(
        item.summary.split("\n\n", 1)[1] for item in result.items
    )
    for index in range(600):
        assert f"| row-{index:05d} | value |" in assembled


async def test_a_document_over_the_full_read_limit_returns_the_outline_and_says_why(
    db_session: AsyncSession,
) -> None:
    # Bounded retrieval rather than a blind dump, and the outline still comes
    # back because choosing what to read is the point of the limit.
    oversize = "# Section one\n\n" + ("padding line\n" * (_FULL_READ_MAX_CHARS // 10))
    assert len(oversize) > _FULL_READ_MAX_CHARS
    document_id = await _store(db_session, text=oversize)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "succeeded"
    assert result.scope["complete"] is False
    assert result.scope["full_read_limit_chars"] == _FULL_READ_MAX_CHARS
    assert "Outline" in result.items[0].summary
    # The instruction the reader acts on at this exact decision point. Its
    # predecessor said "one at a time" and got sixteen calls for one answer.
    guidance = result.scope["searched_what"]
    assert "one at a time" not in guidance
    assert "sections=['s6','s9','s12']" in guidance
    assert "ONE further call" in guidance
    assert str(_SECTIONS_READ_MAX_CHARS) in guidance


# -- absence, provenance and authorization -----------------------------------


async def test_an_unstored_text_fails_with_the_reason_and_the_next_step(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(db_session, text="", store_text=False)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    assert result.scope["text_stored"] is False
    assert "Search inside it instead" in result.error_message


async def test_a_missing_document_says_only_what_it_actually_checked(
    db_session: AsyncSession,
) -> None:
    # Absence discipline at the tool layer: the result may state that this
    # identifier was not found, never that the project holds nothing like it.
    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=999_999,
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    assert "says nothing about the project's other" in result.scope["searched_what"]


async def test_a_reconstructed_text_is_never_presented_as_the_original(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(
        db_session, text=MAPPING_DOC, source=DOCUMENT_TEXT_SOURCE_RECONSTRUCTED
    )

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.scope["text_source"] == DOCUMENT_TEXT_SOURCE_RECONSTRUCTED
    assert "not byte-identical" in result.scope["provenance_warning"]


async def test_a_document_in_another_project_is_not_readable(
    db_session: AsyncSession,
) -> None:
    # The executor validates `project_id` against the caller's scope, but this
    # reads Postgres by primary key: an authorized project id paired with
    # someone else's document id would otherwise be a working cross-project
    # read.
    other = await _store(db_session, text=MAPPING_DOC, project_id="other-client")

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=other,
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    assert "MAPPING" not in str(result.items)


async def test_an_unauthorized_project_is_refused_before_any_query(
    db_session: AsyncSession,
) -> None:
    document_id = await _store(db_session, text=MAPPING_DOC)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=document_id,
        project_scope=["a-different-project"],
    )

    assert result.status == "failed"
    assert result.items == []


# -- naming the document by filename -----------------------------------------
#
# The measured dead end this section closes: one document told the agent that
# the per-field mapping table it needed was in `13_VELDINDEX.md`. `read_document`
# took an id and nothing else, the agent had never seen that file's id, so it
# guessed three similarity queries against remembered field names, missed, and
# told the user "die exacte per-kolom tabel heb ik hier niet uit de corpus
# gehaald". The table was in the corpus, in a document that had been named to it.


#: Stored with a project prefix, which is the real shape: filenames in the
#: corpus are paths, and a document referring to another writes the bare name.
VELDINDEX = f"{PROJECT}/13_VELDINDEX.md"


@pytest.mark.parametrize(
    "asked_for",
    [
        pytest.param(VELDINDEX, id="the-exact-stored-name"),
        pytest.param("13_VELDINDEX.md", id="the-trailing-segment"),
        pytest.param("13_VELDINDEX", id="the-stem-without-its-extension"),
        pytest.param("13_veldindex.md", id="another-case"),
        pytest.param("ACME/13_veldindex", id="another-case-and-no-extension"),
        pytest.param("  13_VELDINDEX.md  ", id="surrounded-by-whitespace"),
    ],
)
async def test_a_document_resolves_from_the_name_another_document_used(
    db_session: AsyncSession, asked_for: str
) -> None:
    # A model writes the name it read in prose, not the stored path. Refusing
    # any of these spellings puts it back where it started: guessing queries.
    document_id = await _store(db_session, text=MAPPING_DOC, filename=VELDINDEX)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename=asked_for,
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "succeeded"
    assert result.scope["document_id"] == document_id
    assert result.scope["filename"] == VELDINDEX
    # The result says the id came from a name, and shows both, so a caller that
    # meant a different file sees it here rather than in the content.
    assert result.scope["resolved_from_filename"] == asked_for
    assert "| A | a |" in result.items[0].summary


async def test_every_mode_reads_the_same_whether_the_document_was_named_or_numbered(
    db_session: AsyncSession,
) -> None:
    # Resolution is addressing, not a second way to read. If any mode behaved
    # differently by name, the name would be a worse tool rather than the same
    # tool reached another way.
    document_id = await _store(db_session, text=CONTRACT_DOC, filename=VELDINDEX)
    calls = {
        "outline": {"mode": "outline"},
        "section": {"mode": "section", "section": "Error handling"},
        "sections": {"sections": ["s2", "s5"]},
        "full": {"mode": "full"},
    }

    for label, arguments in calls.items():
        named = await read_document_text_evidence(
            db=db_session,
            project_id=PROJECT,
            filename="13_VELDINDEX",
            project_scope=[PROJECT],
            **arguments,
        )
        numbered = await read_document_text_evidence(
            db=db_session,
            project_id=PROJECT,
            document_id=document_id,
            project_scope=[PROJECT],
            **arguments,
        )

        assert named.status == "succeeded", label
        assert [item.summary for item in named.items] == [
            item.summary for item in numbered.items
        ], label
        # Identical down to the scope, except for the one field that records
        # how the document was addressed.
        assert named.scope == numbered.scope | {
            "resolved_from_filename": "13_VELDINDEX"
        }, label


async def test_naming_no_document_at_all_says_which_argument_to_send(
    db_session: AsyncSession,
) -> None:
    result = await read_document_text_evidence(
        db=db_session, project_id=PROJECT, project_scope=[PROJECT]
    )

    assert result.status == "failed"
    assert result.items == []
    assert "'document_id'" in result.error_message
    assert "'filename'" in result.error_message
    assert "Nothing was read" in result.scope["searched_what"]


async def test_naming_the_document_twice_is_refused_rather_than_resolved(
    db_session: AsyncSession,
) -> None:
    # The two arguments can name different documents. Preferring either one
    # silently answers a question the caller did not ask.
    await _store(db_session, text=MAPPING_DOC, filename=VELDINDEX)
    other = await _store(db_session, text=CONTRACT_DOC, filename=f"{PROJECT}/other.md")

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        document_id=other,
        filename="13_VELDINDEX.md",
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "failed"
    assert "not both" in result.error_message
    # Neither of the two was read, not one of them.
    assert result.items == []
    assert "Nothing was read" in result.scope["searched_what"]


async def test_an_ambiguous_name_fails_and_lists_every_candidate_with_its_id(
    db_session: AsyncSession,
) -> None:
    # Not hypothetical: one real project stores eleven documents whose last path
    # segment is `01_ARCHITECTURE_MAP.md`, one per scanned sub-project. Quietly
    # returning the first would present a different sub-project's architecture
    # as the answer, with full confidence and no way to notice.
    first = await _store(
        db_session, text=MAPPING_DOC, filename=f"{PROJECT}/A/01_ARCHITECTURE_MAP.md"
    )
    second = await _store(
        db_session, text=CONTRACT_DOC, filename=f"{PROJECT}/B/01_ARCHITECTURE_MAP.md"
    )

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="01_ARCHITECTURE_MAP.md",
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "failed"
    assert result.items == []
    assert result.scope["candidates"] == [
        f"document_id {first}: {PROJECT}/A/01_ARCHITECTURE_MAP.md",
        f"document_id {second}: {PROJECT}/B/01_ARCHITECTURE_MAP.md",
    ]
    # The ids are in the message too: that is the string the model reads before
    # deciding what to call next.
    assert str(first) in result.error_message
    assert str(second) in result.error_message
    assert "no document was picked" in result.scope["searched_what"]
    # And neither document's content leaked into the refusal.
    assert "ALPHA" not in str(result.items)


async def test_a_deeper_name_disambiguates_what_the_bare_name_could_not(
    db_session: AsyncSession,
) -> None:
    # The recovery path from the failure above, without needing the id: the
    # caller adds the segment that tells the two apart.
    await _store(
        db_session, text=MAPPING_DOC, filename=f"{PROJECT}/A/01_ARCHITECTURE_MAP.md"
    )
    second = await _store(
        db_session, text=CONTRACT_DOC, filename=f"{PROJECT}/B/01_ARCHITECTURE_MAP.md"
    )

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="B/01_ARCHITECTURE_MAP.md",
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "succeeded"
    assert result.scope["document_id"] == second


async def test_an_unmatched_name_reports_what_it_searched_and_claims_nothing_more(
    db_session: AsyncSession,
) -> None:
    # Absence discipline. "No document by that name in this project" is a fact
    # about one project's document list; "that does not exist" is a claim about
    # a client's estate, and the model must not be able to turn one into the
    # other on the strength of this result.
    await _store(db_session, text=MAPPING_DOC, filename=VELDINDEX)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="14_VELDINDEX.md",
        project_scope=[PROJECT],
    )

    searched = result.scope["searched_what"]
    assert result.status == "failed"
    assert result.items == []
    assert result.scope["documents_searched"] == 1
    assert result.scope["filename_requested"] == "14_VELDINDEX.md"
    # What was matched, against how many documents, in which project.
    assert "14_VELDINDEX.md" in searched
    assert PROJECT in searched
    assert "last path segment" in searched
    # And the limits of the statement, spelled out rather than implied.
    assert "No document in THIS project answers to that name" in searched
    assert "Do not report it as evidence that the content does not exist" in searched


async def test_a_deleted_document_is_not_reachable_by_name(
    db_session: AsyncSession,
) -> None:
    await _store(
        db_session,
        text=MAPPING_DOC,
        filename=VELDINDEX,
        deleted_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="13_VELDINDEX.md",
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "failed"
    assert result.items == []
    assert result.scope["documents_searched"] == 0
    assert "| A | a |" not in str(result.items)


async def test_the_same_name_in_another_project_is_not_reachable(
    db_session: AsyncSession,
) -> None:
    # A name is a weaker key than an id, so the project filter is the thing that
    # keeps it from being a cross-project read.
    await _store(
        db_session,
        text=MAPPING_DOC,
        filename="other-client/13_VELDINDEX.md",
        project_id="other-client",
    )

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="13_VELDINDEX.md",
        project_scope=[PROJECT],
        mode="full",
    )

    assert result.status == "failed"
    assert result.items == []
    assert result.scope["documents_searched"] == 0
    assert "| A | a |" not in str(result.items)


async def test_an_unauthorized_project_is_refused_before_the_name_is_looked_at(
    db_session: AsyncSession,
) -> None:
    await _store(db_session, text=MAPPING_DOC, filename=VELDINDEX)

    result = await read_document_text_evidence(
        db=db_session,
        project_id=PROJECT,
        filename="13_VELDINDEX.md",
        project_scope=["a-different-project"],
    )

    assert result.status == "failed"
    assert result.items == []
    assert "not in the authorized scope" in result.error_message


def test_a_name_matches_whole_segments_and_not_the_end_of_a_longer_one() -> None:
    # A plain string suffix would make `INDEX.md` match `FIELD_INDEX.md`, and a
    # near-miss here hands over a different document's mappings with full
    # confidence -- strictly worse than the dead end this replaces.
    assert _filename_matches(f"{PROJECT}/FIELD_INDEX.md", "FIELD_INDEX.md") is True
    assert _filename_matches(f"{PROJECT}/FIELD_INDEX.md", "FIELD_INDEX") is True
    assert _filename_matches(f"{PROJECT}/FIELD_INDEX.md", "INDEX.md") is False
    assert _filename_matches(f"{PROJECT}/FIELD_INDEX.md", "INDEX") is False
    assert _filename_matches(f"{PROJECT}/deep/FIELD_INDEX.md", "deep/FIELD_INDEX.md") is True
    assert _filename_matches(f"{PROJECT}/deep/FIELD_INDEX.md", "other/FIELD_INDEX.md") is False
    # A name longer than the stored path cannot be a suffix of it.
    assert _filename_matches("FIELD_INDEX.md", f"{PROJECT}/FIELD_INDEX.md") is False
    # An extensionless name keeps its leading dot rather than becoming empty and
    # matching every other extensionless segment in the project.
    assert _filename_matches(f"{PROJECT}/.gitignore", ".gitignore") is True
    assert _filename_matches(f"{PROJECT}/README", ".gitignore") is False


def test_a_whole_path_outranks_the_same_path_as_another_document_s_suffix() -> None:
    # Without the exact tier a document becomes unreachable by its own full
    # name the moment anything is stored beneath a prefix of it -- the full name
    # is a suffix of the other, so both would match and the call would fail.
    documents = [
        (1, f"{PROJECT}/13_VELDINDEX.md"),
        (2, f"{PROJECT}/archive/{PROJECT}/13_VELDINDEX.md"),
    ]

    assert _resolve_filename_candidates(documents, f"{PROJECT}/13_VELDINDEX.md") == [
        (1, f"{PROJECT}/13_VELDINDEX.md")
    ]
    # Ranking is not choosing: the bare name is genuinely ambiguous and both
    # come back, so the caller is told so rather than handed one of them.
    assert len(_resolve_filename_candidates(documents, "13_VELDINDEX.md")) == 2


async def test_a_filename_read_survives_the_wire_from_schema_to_reader(
    db_session: AsyncSession,
) -> None:
    # The schema had `document_id` in `required`. If it still did, the executor
    # would deny this before the reader ever ran, with a validation code and a
    # field path -- a dead end with no recovery, which is the exact shape this
    # change exists to remove.
    document_id = await _store(db_session, text=CONTRACT_DOC, filename=VELDINDEX)
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    result = await HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers=adapters,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            arguments={
                "project_id": PROJECT,
                "filename": "13_VELDINDEX.md",
                "sections": ["s2", "s4"],
            },
        ),
        CapabilityExecutionContext(
            user_id=7, conversation_id=41, project_scope=[PROJECT]
        ),
    )

    assert result.status == "succeeded"
    body = " ".join(item["summary"] for item in result.data["items"])
    assert "ALPHA" in body
    assert "BRAVO" in body
    assert result.data["scope"]["document_id"] == document_id


async def test_the_candidate_ids_survive_the_scope_serializer(
    db_session: AsyncSession,
) -> None:
    # The candidates are the whole value of an ambiguous failure, and the scope
    # serializer carries strings, numbers and lists of those while dropping
    # anything else. A list of {"document_id": ..., "filename": ...} objects
    # would arrive as an empty list and the reader would be told only that its
    # name was ambiguous, with no way to resolve it.
    first = await _store(
        db_session, text=MAPPING_DOC, filename=f"{PROJECT}/A/01_ARCHITECTURE_MAP.md"
    )
    second = await _store(
        db_session, text=CONTRACT_DOC, filename=f"{PROJECT}/B/01_ARCHITECTURE_MAP.md"
    )
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    result = await HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers=adapters,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            arguments={"project_id": PROJECT, "filename": "01_ARCHITECTURE_MAP.md"},
        ),
        CapabilityExecutionContext(
            user_id=7, conversation_id=41, project_scope=[PROJECT]
        ),
    )

    assert result.status == "failed"
    candidates = result.data["scope"]["candidates"]
    assert str(first) in " ".join(candidates)
    assert str(second) in " ".join(candidates)


def test_the_candidate_cap_stays_inside_what_the_serializer_will_carry() -> None:
    # Two constants in two modules, and the smaller one has to be the local one.
    # Exceed the serializer's list bound and the tail of the candidate list is
    # dropped on the way to the model with nothing saying it was.
    assert _FILENAME_CANDIDATE_LIMIT <= _MAX_SCOPE_LIST_ENTRIES


async def test_a_call_that_names_nothing_reaches_the_reader_and_gets_an_instruction(
    db_session: AsyncSession,
) -> None:
    # The cost of dropping `document_id` from `required`: a call naming neither
    # argument is now the reader's problem. It has to come back as a sentence
    # saying what to send, not as a schema denial naming a field path.
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    result = await HarnessCapabilityExecutor(
        base_capability_registry(),
        evidence_handlers=adapters,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            arguments={"project_id": PROJECT},
        ),
        CapabilityExecutionContext(
            user_id=7, conversation_id=41, project_scope=[PROJECT]
        ),
    )

    assert result.status == "failed"
    assert "'document_id'" in result.error_message
    assert "'filename'" in result.error_message


# -- the capability contract -------------------------------------------------


def test_the_capability_advertises_a_read_and_not_a_search() -> None:
    # The name is the fix. `read_document` was a similarity search, and an agent
    # told it had read a document answers completeness questions from fragments.
    assert DOCUMENT_FULL_TEXT_CAPABILITY.semantic_name == "read_document"
    assert DOCUMENT_FULL_TEXT_CAPABILITY.id == "rag.docs.read_document_text"
    assert "completeness" in DOCUMENT_FULL_TEXT_CAPABILITY.purpose


def test_the_schema_offers_the_batch_without_retiring_the_singular() -> None:
    # `section` is persisted in conversation history and referenced by the
    # executor and the pack fixtures; the batch is an addition, not a rename.
    properties = DOCUMENT_FULL_TEXT_CAPABILITY.input_schema["properties"]

    assert properties["section"]["type"] == "string"
    assert properties["sections"]["type"] == "array"
    assert properties["sections"]["items"]["type"] == "string"
    assert properties["sections"]["minItems"] == 1
    # Capped at more sections than a real 32-section document needs, so the
    # ceiling never becomes the reason a reader splits a batch in two.
    assert properties["sections"]["maxItems"] >= 24


def test_the_schema_quotes_the_budget_the_code_actually_enforces() -> None:
    # The number lives in `evidence.py`, which `models.py` cannot import without
    # a cycle, so it is restated in the description. This is the drift guard:
    # a tool that advertises a budget it does not honour is worse than silent.
    description = DOCUMENT_FULL_TEXT_CAPABILITY.input_schema["properties"][
        "sections"
    ]["description"]

    assert str(_SECTIONS_READ_MAX_CHARS) in description


def test_the_schema_accepts_a_filename_and_no_longer_demands_an_identifier() -> None:
    # "exactly one of the two" is deliberately NOT a schema `oneOf`. The
    # executor reports a schema violation as a validation code and a field path,
    # with no sentence saying what to send instead -- which is the dead end this
    # capability keeps running into. The reader enforces it and explains it.
    schema = DOCUMENT_FULL_TEXT_CAPABILITY.input_schema

    assert schema["required"] == ["project_id"]
    assert schema["properties"]["filename"]["type"] == "string"
    assert schema["properties"]["document_id"]["type"] == "integer"


def test_the_capability_text_tells_a_model_the_filename_option_exists() -> None:
    # A model picks an argument from this text. If nothing here says a document
    # can be opened by name, the option may as well not exist -- the measured
    # run had the name in front of it and still guessed similarity queries.
    capability = DOCUMENT_FULL_TEXT_CAPABILITY
    advice = " ".join(
        [
            capability.purpose,
            *capability.preconditions,
            *capability.use_when,
            capability.input_schema["properties"]["filename"]["description"],
        ]
    )

    assert "'filename'" in capability.purpose
    # The motivating case, named: another document referred to a file by name.
    assert "13_VELDINDEX.md" in " ".join(capability.use_when)
    assert any("filename" in entry for entry in capability.preconditions)
    # And what happens when the name is not unique, so the failure is not a
    # surprise that costs a call to learn.
    assert "lists every candidate with its id" in advice


def test_the_capability_text_names_batching_as_the_normal_way_to_read_several() -> None:
    # A model picks a tool, and an argument, from this text. If it still reads
    # as one section per call, the measured behaviour does not change.
    capability = DOCUMENT_FULL_TEXT_CAPABILITY
    advice = " ".join(
        [
            capability.purpose,
            *capability.use_when,
            *capability.do_not_use_when,
            capability.input_schema["properties"]["mode"]["description"],
            capability.input_schema["properties"]["sections"]["description"],
            capability.input_schema["properties"]["section"]["description"],
        ]
    )

    assert "one at a time" not in advice
    assert advice.count("'sections'") >= 3
    assert "overrides 'mode'" in advice
