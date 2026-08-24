from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.generation import rag_chain
from src.retrieval.topology_hints import TopologyHintStrength, TopologyRetrievalHints
from src.schemas import QueryRequest, Source


def _source(document_id: int, filename: str, chunk_index: int = 0) -> Source:
    return Source(
        document_id=document_id,
        filename=filename,
        chunk_index=chunk_index,
        score=0.9,
        text=f"content from {filename}",
        project_id="allowed",
    )


@pytest.mark.asyncio
async def test_hint_search_adds_recall_without_suppressing_normal_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def fake_search(
        query,  # noqa: ANN001
        user_groups,  # noqa: ANN001
        qdrant,  # noqa: ANN001
        top_k,  # noqa: ANN001
        threshold,  # noqa: ANN001
        document_ids,  # noqa: ANN001
        project_ids,  # noqa: ANN001
        document_roles=None,  # noqa: ANN001
        knowledge_revisions=None,  # noqa: ANN001
    ):
        del user_groups, qdrant, top_k, threshold, document_ids, knowledge_revisions
        calls.append(
            {"query": query, "project_ids": project_ids, "roles": document_roles}
        )
        if document_roles != ["source"]:
            return []
        if "Related project identifiers" in query:
            return [_source(2, "VQ_Quintiq2MX/03_DATA_FLOWS_AND_LINEAGE.md")]
        return [_source(1, "general-project-overview.md")]

    monkeypatch.setattr(rag_chain, "_execute_search", fake_search)
    hints = TopologyRetrievalHints(
        project_id="allowed",
        strength=TopologyHintStrength.EXACT,
        matched_entity_names=("Maximo", "Quintiq"),
        job_names=("Quintiq2Maximo_PlannedWorkOrder",),
        retrieval_terms=("Maximo", "Quintiq", "Quintiq2Maximo_PlannedWorkOrder"),
    )

    sources, auxiliary = await rag_chain._execute_role_aware_search(
        "How does Quintiq send work orders to Maximo?",
        QueryRequest(query="How does Quintiq send work orders to Maximo?", top_k=8),
        ["engineering"],
        AsyncMock(),
        ["allowed"],
        topology_hints=hints,
    )

    assert [source.document_id for source in sources] == [1, 2]
    assert auxiliary == []
    assert len(calls) == 3
    assert all(call["project_ids"] == ["allowed"] for call in calls)
    assert calls[0]["query"] == "How does Quintiq send work orders to Maximo?"


@pytest.mark.asyncio
async def test_generic_hint_is_additive_and_duplicate_chunks_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _source(7, "complete-documentation.md", chunk_index=3)

    async def fake_search(query, *args, document_roles=None, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        if document_roles != ["source"]:
            return []
        return [original] if "Related project identifiers" not in query else [original]

    monkeypatch.setattr(rag_chain, "_execute_search", fake_search)
    hints = TopologyRetrievalHints(
        project_id="allowed",
        strength=TopologyHintStrength.AMBIGUOUS,
        ambiguous=True,
        generic_labels=("Log", "Talend", "Quintiq"),
        retrieval_terms=("Log", "Talend", "Quintiq"),
    )

    sources, _ = await rag_chain._execute_role_aware_search(
        "Explain Log, Talend, and Quintiq",
        QueryRequest(query="Explain Log, Talend, and Quintiq", top_k=8),
        ["engineering"],
        AsyncMock(),
        ["allowed"],
        topology_hints=hints,
    )

    assert sources == [original]

