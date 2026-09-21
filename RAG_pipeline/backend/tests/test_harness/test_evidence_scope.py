"""Every evidence result says what it covered, so a negative can be scoped.

The measured failure: the agent searched a prose corpus that provably does not
contain a rule, found nothing, and published "No order- or product-level filter
is recorded ... worth confirming with the business" to a client. Telling the
model not to do that is only half a fix -- it had no way to know what it had
searched, so it could not have scoped the negative honestly even if it had tried.

Two facts were invisible to it and are now stated in every result:

* ``search_documentation`` searches ``document_role="source"`` ONLY. A policy, a
  standard or a template is not in the corpus it looks at.
* Similarity retrieval returns the best ``top_k``, never all matches, so no
  result from it can support "there is no such thing".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document import Document
from src.harness import evidence as evidence_module
from src.harness.capability_handlers import RegisteredEvidenceHandler, _safe_scope
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
)
from src.harness.models import (
    DOCUMENT_SEARCH_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)

PROJECT = "acme"


class Source(SimpleNamespace):
    """The shape `_execute_search` returns, reduced to what the mapper reads."""


def _source() -> Source:
    return Source(
        document_id=19,
        filename="02_COMPONENT_INVENTORY.md",
        chunk_index=3,
        score=0.9,
        text="Ten indexed jobs are documented here.",
        project_id=PROJECT,
        display_name=None,
        artifact_path=None,
        source_layer="canonical_docs",
    )


async def _add_documents(db: AsyncSession, *, count: int, role: str) -> None:
    for index in range(count):
        db.add(
            Document(
                filename=f"{PROJECT}/doc-{role}-{index}.md",
                file_type="md",
                document_role=role,
                project_id=PROJECT,
            )
        )
    await db.flush()


async def test_a_documentation_search_reports_the_roles_it_did_not_search(
    db_session: AsyncSession, monkeypatch
) -> None:
    async def search(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return []

    monkeypatch.setattr(evidence_module, "_execute_search", search)
    await _add_documents(db_session, count=3, role="source")
    await _add_documents(db_session, count=2, role="policy")

    result = await evidence_module.search_document_evidence(
        qdrant=object(),  # type: ignore[arg-type]
        query="which customers are selected",
        user_groups=["engineering"],
        project_ids=[PROJECT],
        db=db_session,
    )

    assert result.items == []
    assert result.scope["document_roles"] == ["source"]
    # The two policies exist and were not looked at. Counting them into the
    # scope would be the same lie in a different direction.
    assert result.scope["documents_in_scope"] == 3
    assert result.scope["exhaustive"] is False
    assert "were NOT searched" in result.scope["searched_what"]
    assert "not that the estate does not contain it" in result.scope["searched_what"]


async def test_the_scope_survives_without_a_session(monkeypatch) -> None:
    # The count is a label on the answer, not part of it: a caller with no
    # session still gets the evidence and the filters, minus the number.
    async def search(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return [_source()]

    monkeypatch.setattr(evidence_module, "_execute_search", search)

    result = await evidence_module.search_document_evidence(
        qdrant=object(),  # type: ignore[arg-type]
        query="anything",
        user_groups=["engineering"],
        project_ids=[PROJECT],
    )

    assert result.items
    assert "documents_in_scope" not in result.scope
    assert result.scope["document_roles"] == ["source"]


async def test_searching_inside_a_document_says_it_is_not_a_read(monkeypatch) -> None:
    async def search(**_kwargs):  # noqa: ANN003
        return [_source()]

    monkeypatch.setattr(evidence_module.settings, "reranker_enabled", False)
    monkeypatch.setattr(evidence_module, "search_similar", search)

    result = await evidence_module.search_within_document_evidence(
        qdrant=object(),  # type: ignore[arg-type]
        query="which fields",
        user_groups=["engineering"],
        project_id=PROJECT,
        document_id=19,
        max_chunks=3,
    )

    assert result.scope["exhaustive"] is False
    assert "not a read of it" in result.scope["searched_what"]
    assert "read_document" in result.scope["searched_what"]


# -- the scope reaches the model ---------------------------------------------


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=1,
        conversation_id=1,
        project_scope=[PROJECT],
        permissions=set(),
        allowed_capability_ids={DOCUMENT_SEARCH_CAPABILITY.id},
    )


async def test_the_handler_forwards_the_scope_on_a_successful_read() -> None:
    # An empty `items` list without it is uninterpretable, which is the whole
    # defect. The handler must not drop the one field that explains it.
    async def adapter(_arguments, _context):  # noqa: ANN001
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[],
            scope={"documents_in_scope": 3, "searched_what": "three source documents"},
        )

    handler = RegisteredEvidenceHandler(DOCUMENT_SEARCH_CAPABILITY)
    observation = await handler(
        DOCUMENT_SEARCH_CAPABILITY,
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id, arguments={"query": "x"}
        ),
        _context(),
        SimpleNamespace(evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: adapter}),
    )

    assert observation.status == "succeeded"
    assert observation.data["scope"]["documents_in_scope"] == 3


async def test_the_handler_forwards_the_scope_on_a_failure_too() -> None:
    # "I could not read that" is only actionable alongside why.
    async def adapter(_arguments, _context):  # noqa: ANN001
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="failed",
            error_message="not stored",
            scope={"text_stored": False},
        )

    handler = RegisteredEvidenceHandler(DOCUMENT_SEARCH_CAPABILITY)
    observation = await handler(
        DOCUMENT_SEARCH_CAPABILITY,
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id, arguments={"query": "x"}
        ),
        _context(),
        SimpleNamespace(evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: adapter}),
    )

    assert observation.status == "failed"
    assert observation.data["scope"] == {"text_stored": False}


@pytest.mark.parametrize("empty", [None, {}, "not a dict", 7])
def test_an_absent_scope_adds_no_key(empty) -> None:  # noqa: ANN001
    assert _safe_scope(empty) is None


def test_scope_values_are_redacted_but_not_pruned_to_an_allowlist() -> None:
    # Unlike evidence metadata, the keys are written by the capability that
    # knows what it covered; an allowlist here would silently drop the next
    # explanation somebody adds.
    safe = _safe_scope(
        {
            "a_brand_new_key": "value",
            "searched_what": "Bearer abcdefghijklmnop was used",
            "projects": ["a", "b"],
        }
    )

    assert safe is not None
    assert safe["a_brand_new_key"] == "value"
    assert "abcdefghijklmnop" not in safe["searched_what"]
    assert safe["projects"] == ["a", "b"]


def test_a_scope_sentence_is_not_cut_at_the_identifier_budget() -> None:
    # `searched_what` is the sentence the model quotes back to itself when it
    # writes "I did not find X". Truncating it at the 500-character identifier
    # budget would cut the explanation rather than an identifier.
    sentence = "word " * 200
    safe = _safe_scope({"searched_what": sentence})

    assert safe is not None
    assert len(safe["searched_what"]) > 900


def test_the_evidence_item_contract_still_bounds_a_summary() -> None:
    # The constant producers split against is the one the serializer enforces.
    from src.harness.capability_handlers import _serialized_evidence_item
    from src.harness.models import EVIDENCE_SUMMARY_MAX_CHARS

    item = EvidenceItem(
        id="doctext:1:1",
        capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
        source_type="rag_document_text",
        title="doc.md",
        summary="x" * (EVIDENCE_SUMMARY_MAX_CHARS + 500),
    )
    serialized = _serialized_evidence_item(item)

    assert serialized is not None
    assert len(serialized["summary"]) == EVIDENCE_SUMMARY_MAX_CHARS
