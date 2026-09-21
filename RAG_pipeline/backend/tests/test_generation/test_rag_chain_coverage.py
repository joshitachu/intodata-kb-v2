"""The two halves of the partial-coverage fix, wired into a real turn.

The measured failure: a question about nine known interfaces retrieved five and
produced a confident answer about five, with no disclosure anywhere. Two root
causes, and both are exercised here end to end rather than in isolation --

* ``search_layered(matched_units=...)`` is the one deterministic "cannot lose the
  ranking contest" path in the codebase, and nothing ever populated it. Rung 1
  had no exact-name path at all.
* ``NO_RESULTS_PROMPT`` fires only when retrieval returns literally zero, so
  there was no state between "complete" and "nothing".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.rag_chain import run_rag_query
from src.retrieval.entity_matcher import EntityMatch, EntityMatchResult
from src.schemas import QueryRequest, Source


@pytest.fixture
def mock_qdrant():
    return AsyncMock()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture(autouse=True)
def plain_retrieval():
    """Dense-only rung 1, no escalation: these cases are about what is REPORTED."""

    with patch("src.generation.rag_chain.settings") as mock_settings:
        mock_settings.query_expansion_enabled = False
        mock_settings.reranker_enabled = False
        mock_settings.hybrid_search_enabled = False
        mock_settings.system_knowledge_injection = False
        mock_settings.deep_layer_escalation_enabled = False
        mock_settings.entity_exact_match_enabled = True
        mock_settings.entity_exact_match_limit = 12
        yield mock_settings


def _source(text: str, *, document_id: int = 1, chunk_index: int = 0) -> Source:
    return Source(
        document_id=document_id,
        filename=f"doc{document_id}.md",
        chunk_index=chunk_index,
        score=0.9,
        text=text,
    )


def _match(canonical: str) -> EntityMatch:
    return EntityMatch(
        alias=canonical,
        canonical_name=canonical,
        entity_type="job",
        project_id="proj",
        unit_name=canonical,
        source_path=None,
        source_artifact=None,
        confidence=1.0,
    )


async def _run(
    *,
    query: str,
    search_results: list[Source],
    answer: str,
    matches: list[EntityMatch],
    unit_chunks: list[Source] | None = None,
    qdrant,
    db,
):
    """One turn with retrieval, entity matching and generation all pinned."""

    with (
        patch(
            "src.generation.rag_chain.search_similar", new_callable=AsyncMock
        ) as mock_search,
        patch(
            "src.generation.rag_chain.match_query_entities", new_callable=AsyncMock
        ) as mock_entities,
        patch(
            "src.generation.rag_chain.fetch_unit_chunks", new_callable=AsyncMock
        ) as mock_units,
        patch(
            "src.generation.rag_chain.generate_answer", new_callable=AsyncMock
        ) as mock_gen,
        patch("src.generation.rag_chain.build_messages") as mock_build,
    ):
        mock_search.side_effect = lambda **kwargs: (
            list(search_results) if kwargs.get("document_roles") == ["source"] else []
        )
        mock_entities.return_value = EntityMatchResult(
            matches=matches, alias_rows_present=bool(matches)
        )
        mock_units.return_value = list(unit_chunks or [])
        mock_gen.return_value = answer
        mock_build.return_value = [{"role": "user", "content": "x"}]

        response = await run_rag_query(
            request=QueryRequest(query=query),
            user_groups=["engineering"],
            user_id=1,
            qdrant=qdrant,
            db=db,
            project_ids=["proj"],
        )
    return response, mock_build, mock_units


# --- The exact-name safety net ----------------------------------------------


async def test_a_named_entity_is_fetched_by_payload_not_by_ranking(
    mock_qdrant, mock_db
) -> None:
    guaranteed = _source("sjItem_OnRamp loads staging", document_id=7)

    _response, _build, mock_units = await _run(
        query="what does sjItem_OnRamp do?",
        search_results=[_source("unrelated but well-ranked prose", document_id=1)],
        answer="It loads staging. [1]",
        matches=[_match("sjItem_OnRamp")],
        unit_chunks=[guaranteed],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    mock_units.assert_awaited_once()
    kwargs = mock_units.await_args.kwargs
    # Rung 1's guarantee runs against the DEFAULT canonical partition, not the
    # internal analysis layers rung 2 searches.
    assert kwargs["source_layers"] is None
    assert kwargs["document_roles"] == ["source"]
    assert mock_units.await_args.args[2] == ["sjItem_OnRamp"]


async def test_the_guaranteed_chunk_leads_the_ranked_ones(
    mock_qdrant, mock_db
) -> None:
    guaranteed = _source("sjItem_OnRamp loads staging", document_id=7)
    ranked = _source("a chunk that merely reads like the question", document_id=1)

    _response, mock_build, _units = await _run(
        query="what does sjItem_OnRamp do?",
        search_results=[ranked],
        answer="It loads staging. [1]",
        matches=[_match("sjItem_OnRamp")],
        unit_chunks=[guaranteed],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    passed_sources = mock_build.call_args.args[1]
    assert [s.document_id for s in passed_sources] == [7, 1]


async def test_a_question_naming_nothing_pays_for_no_extra_fetch(
    mock_qdrant, mock_db
) -> None:
    _response, _build, mock_units = await _run(
        query="how does the estate handle errors?",
        search_results=[_source("error handling prose")],
        answer="Errors are logged. [1]",
        matches=[],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    mock_units.assert_not_awaited()


# --- The coverage signal ----------------------------------------------------


async def test_an_uncovered_named_entity_reaches_the_synthesis_prompt(
    mock_qdrant, mock_db
) -> None:
    _response, mock_build, _units = await _run(
        query="compare iface_alpha and iface_beta",
        # Only one of the two named interfaces is in the retrieved evidence.
        search_results=[_source("iface_alpha maps orders to the warehouse")],
        answer="They differ. [1]",
        matches=[_match("iface_alpha"), _match("iface_beta")],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    coverage = mock_build.call_args.kwargs["coverage"]
    assert coverage.entities.missing == ("iface_beta",)
    assert coverage.has_gap


async def test_coverage_counts_documents_retrieved_against_documents_referenced(
    mock_qdrant, mock_db
) -> None:
    _response, mock_build, _units = await _run(
        query="what runs nightly?",
        search_results=[
            _source("a", document_id=1),
            _source("b", document_id=2),
            _source("c", document_id=3),
        ],
        answer="Three jobs. [1]",
        matches=[],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    coverage = mock_build.call_args.kwargs["coverage"]
    assert coverage.retrieved_documents == 3
    assert coverage.referenced_documents == 3


# --- Citation binding on the production path --------------------------------


async def test_a_fabricated_reference_number_is_disclosed_in_the_answer(
    mock_qdrant, mock_db
) -> None:
    response, _build, _units = await _run(
        query="what runs nightly?",
        search_results=[_source("the loader runs at 02:00")],
        answer="The loader runs nightly [1] and alerts on failure [4].",
        matches=[],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    assert "[4]" in response.answer
    assert "do not resolve" in response.answer
    # The model's own prose is kept; only the caveat is added.
    assert response.answer.startswith("The loader runs nightly [1]")


async def test_the_sources_panel_shows_only_what_the_answer_cited(
    mock_qdrant, mock_db
) -> None:
    response, _build, _units = await _run(
        query="what runs nightly?",
        search_results=[
            _source("the loader runs at 02:00", document_id=1),
            _source("an unrelated retrieved document", document_id=2),
        ],
        answer="The loader runs nightly [1].",
        matches=[],
        qdrant=mock_qdrant,
        db=mock_db,
    )

    assert [doc.document_id for doc in response.sources] == [1]


async def test_the_repair_notice_is_streamed_as_well_as_returned(
    mock_qdrant, mock_db
) -> None:
    """The live text and the persisted text must not diverge."""

    published: list[str] = []

    async def on_delta(chunk: str) -> None:
        published.append(chunk)

    async def fake_stream(_messages, callback):
        await callback("The loader runs nightly [9].")
        return "The loader runs nightly [9]."

    with (
        patch(
            "src.generation.rag_chain.search_similar", new_callable=AsyncMock
        ) as mock_search,
        patch(
            "src.generation.rag_chain.match_query_entities", new_callable=AsyncMock
        ) as mock_entities,
        patch(
            "src.generation.rag_chain._generate_streaming_answer", new=fake_stream
        ),
    ):
        mock_search.side_effect = lambda **kwargs: (
            [_source("the loader runs at 02:00")]
            if kwargs.get("document_roles") == ["source"]
            else []
        )
        mock_entities.return_value = EntityMatchResult(
            matches=[], alias_rows_present=False
        )

        response = await run_rag_query(
            request=QueryRequest(query="what runs nightly?"),
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
            on_delta=on_delta,
        )

    assert "".join(published) == response.answer
    assert "do not resolve" in published[-1]


# --- Degradation ------------------------------------------------------------


async def test_an_entity_lookup_failure_costs_the_net_not_the_turn(
    mock_qdrant, mock_db, caplog
) -> None:
    with (
        patch(
            "src.generation.rag_chain.search_similar", new_callable=AsyncMock
        ) as mock_search,
        patch(
            "src.generation.rag_chain.match_query_entities", new_callable=AsyncMock
        ) as mock_entities,
        patch(
            "src.generation.rag_chain.generate_answer", new_callable=AsyncMock
        ) as mock_gen,
    ):
        mock_search.side_effect = lambda **kwargs: (
            [_source("prose")] if kwargs.get("document_roles") == ["source"] else []
        )
        mock_entities.side_effect = RuntimeError("alias table unreachable")
        mock_gen.return_value = "An answer. [1]"

        response = await run_rag_query(
            request=QueryRequest(query="what does job_a do?"),
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    assert response.answer.startswith("An answer.")
    assert any(
        record.levelname == "ERROR" and "Entity matching failed" in record.getMessage()
        for record in caplog.records
    )
