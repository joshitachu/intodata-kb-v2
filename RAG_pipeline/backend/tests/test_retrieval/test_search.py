from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.retrieval.search import (
    _build_search_filter,
    _source_from_payload,
    expand_neighbor_chunks,
    merge_sources_by_document,
    search_similar,
    search_similar_hybrid,
    _process_results,
)
from src.schemas import Source


def _make_point(score: float, payload: dict):
    point = MagicMock()
    point.score = score
    point.payload = payload
    return point


def _payload(document_id: int, filename: str, chunk_index: int, text: str) -> dict:
    return {
        "document_id": document_id,
        "filename": filename,
        "chunk_index": chunk_index,
        "text": text,
    }


@pytest.fixture
def mock_qdrant():
    return AsyncMock()


async def test_search_returns_sources_above_threshold(mock_qdrant):
    result = MagicMock()
    result.points = [
        _make_point(0.95, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Relevant text",
        }),
        _make_point(0.5, {
            "document_id": 2,
            "filename": "other.pdf",
            "chunk_index": 1,
            "text": "Irrelevant text",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", ["engineering"])

    assert len(sources) == 2
    assert sources[0].document_id == 1
    assert sources[0].score == 0.95
    assert sources[1].document_id == 2
    assert sources[1].score == 0.5


async def test_search_no_results_below_threshold(mock_qdrant):
    result = MagicMock()
    result.points = [
        _make_point(0.15, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Low relevance",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", ["engineering"])

    assert len(sources) == 0


async def test_search_with_empty_user_groups(mock_qdrant):
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", [])

    # Should call query_points with the default layer filter but no RBAC group filter.
    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    query_filter = call_kwargs["query_filter"]
    assert query_filter is not None
    assert not any(
        getattr(condition, "key", None) == "allowed_groups"
        for condition in query_filter.must
    )
    assert any(
        condition.__class__.__name__ == "Filter"
        for condition in query_filter.must
    )


async def test_search_custom_top_k_and_threshold(mock_qdrant):
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        await search_similar(mock_qdrant, "test", ["eng"], top_k=10, threshold=0.5)

    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    assert call_kwargs["limit"] == 10


# --- Tests for search_similar_hybrid ---


async def test_hybrid_search_calls_both_embeddings(mock_qdrant):
    """Hybrid search generates both dense and sparse embeddings."""
    result = MagicMock()
    result.points = [
        _make_point(0.8, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Hybrid result",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1, 5, 10]
        mock_sparse_vec.values = [0.5, 0.3, 0.8]
        mock_sparse.return_value = mock_sparse_vec

        sources = await search_similar_hybrid(mock_qdrant, "test query", ["eng"])

    assert len(sources) == 1
    assert sources[0].filename == "doc.pdf"
    mock_dense.assert_called_once()
    mock_sparse.assert_called_once()


async def test_hybrid_search_uses_prefetch(mock_qdrant):
    """Hybrid search passes prefetch parameter to Qdrant."""
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1]
        mock_sparse_vec.values = [0.5]
        mock_sparse.return_value = mock_sparse_vec

        await search_similar_hybrid(mock_qdrant, "test", [], prefetch_limit=30)

    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    assert "prefetch" in call_kwargs
    assert len(call_kwargs["prefetch"]) == 2  # dense + sparse


async def test_hybrid_search_lower_default_threshold(mock_qdrant):
    """Hybrid search has a lower default threshold (0.25 vs 0.4 for dense)."""
    result = MagicMock()
    result.points = [
        _make_point(0.3, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "RRF result",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1]
        mock_sparse_vec.values = [0.5]
        mock_sparse.return_value = mock_sparse_vec

        # Default threshold is 0.25, so 0.3 should pass
        sources = await search_similar_hybrid(mock_qdrant, "test", [])

    assert len(sources) == 1


# --- Tests for _process_results ---


def test_process_results_filters_by_threshold():
    points = [
        _make_point(0.9, {"document_id": 1, "filename": "a.pdf", "chunk_index": 0, "text": "Hi"}),
        _make_point(0.1, {"document_id": 2, "filename": "b.pdf", "chunk_index": 0, "text": "Lo"}),
    ]
    sources = _process_results(points, threshold=0.5)
    assert len(sources) == 1
    assert sources[0].document_id == 1


def test_process_results_empty():
    sources = _process_results([], threshold=0.5)
    assert sources == []


# --- Tests for merge_sources_by_document ---

def test_merge_sources_single_chunk():
    sources = [
        Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Hello"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 1
    assert merged[0].document_id == 1
    assert merged[0].filename == "doc.pdf"
    assert merged[0].score == 0.9
    assert merged[0].text == "Hello"
    assert merged[0].reference_number == 1


def test_merge_sources_multiple_chunks_same_doc():
    sources = [
        Source(document_id=1, filename="doc.pdf", chunk_index=2, score=0.8, text="Part C"),
        Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.95, text="Part A"),
        Source(document_id=1, filename="doc.pdf", chunk_index=1, score=0.7, text="Part B"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 1
    assert merged[0].score == 0.95  # highest score
    # Text should be sorted by chunk_index: A, B, C
    assert merged[0].text == "Part A\n\nPart B\n\nPart C"
    assert merged[0].reference_number == 1
    assert merged[0].chunk_indexes == [0, 1, 2]


async def test_expand_neighbor_chunks_fetches_adjacent_chunks(mock_qdrant):
    mock_qdrant.scroll.return_value = (
        [
            _make_point(0.0, _payload(1, "doc.md", 4, "Previous")),
            _make_point(0.0, _payload(1, "doc.md", 5, "Hit")),
            _make_point(0.0, _payload(1, "doc.md", 6, "Next")),
        ],
        None,
    )
    sources = [
        Source(document_id=1, filename="doc.md", chunk_index=5, score=0.9, text="Hit"),
    ]

    expanded = await expand_neighbor_chunks(mock_qdrant, sources, ["engineering"], window=1)

    assert [s.chunk_index for s in expanded] == [4, 5, 6]
    assert expanded[0].text == "Previous"
    assert expanded[1].text == "Hit"
    assert expanded[2].text == "Next"


def test_merge_sources_multiple_documents_default():
    """Default max_documents uses settings.max_result_documents (3) — returns all."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 3
    assert merged[0].filename == "b.pdf"
    assert merged[0].reference_number == 1


def test_merge_sources_multiple_documents_max_3():
    """max_documents=3 returns all documents with correct reference numbers."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources, max_documents=3)
    assert len(merged) == 3
    # Sorted by score descending
    assert merged[0].filename == "b.pdf"
    assert merged[1].filename == "a.pdf"
    assert merged[2].filename == "c.pdf"
    # Reference numbers assigned after sorting
    assert merged[0].reference_number == 1
    assert merged[1].reference_number == 2
    assert merged[2].reference_number == 3


def test_merge_sources_max_documents_limits():
    """max_documents=2 returns only top 2 out of 3."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources, max_documents=2)
    assert len(merged) == 2
    assert merged[0].filename == "b.pdf"
    assert merged[1].filename == "a.pdf"


def test_merge_sources_empty():
    merged = merge_sources_by_document([])
    assert merged == []


# --- Tests for _source_from_payload ---


class TestSourceFromPayload:
    def test_legacy_payload_no_layer_fields(self) -> None:
        """Legacy chunks (no source_layer) deserialize with None for new fields."""
        payload = {
            "document_id": 1,
            "filename": "doc.md",
            "chunk_index": 0,
            "text": "Content",
        }
        src = _source_from_payload(payload, 0.8)
        assert src.document_id == 1
        assert src.filename == "doc.md"
        assert src.score == 0.8
        assert src.source_layer is None
        assert src.section is None
        assert src.unit is None
        assert src.group is None

    def test_full_payload_with_layer_fields(self) -> None:
        payload = {
            "document_id": 2,
            "filename": "proj/api.md",
            "chunk_index": 3,
            "text": "API text",
            "source_layer": "canonical_docs",
            "section": "proj > API Contracts > Auth",
            "unit": "Auth",
            "group": "Nexus",
        }
        src = _source_from_payload(payload, 0.92)
        assert src.source_layer == "canonical_docs"
        assert src.section == "proj > API Contracts > Auth"
        assert src.unit == "Auth"
        assert src.group == "Nexus"
        assert src.score == 0.92

    def test_score_rounded_to_four_places(self) -> None:
        payload = {
            "document_id": 1,
            "filename": "f.md",
            "chunk_index": 0,
            "text": "t",
        }
        src = _source_from_payload(payload, 0.123456789)
        assert src.score == 0.1235


# --- Tests for _build_search_filter layer conditions ---


class TestLayerFilter:
    def test_default_filter_includes_legacy_is_empty(self) -> None:
        """source_layers=None → default filter includes IsEmptyCondition."""
        from qdrant_client.models import IsEmptyCondition
        with patch("src.retrieval.search.settings") as mock_settings:
            mock_settings.default_source_layers = ["canonical_docs"]
            f = _build_search_filter([], source_layers=None)
        # The outer filter must have conditions that include a nested should Filter
        assert f is not None
        # Find the layer condition (a nested Filter in must)
        from qdrant_client.models import Filter as QFilter
        layer_condition = next(
            (c for c in f.must if isinstance(c, QFilter)), None
        )
        assert layer_condition is not None, "Expected a nested Filter for layer conditions"
        should_types = {type(c) for c in layer_condition.should}
        assert IsEmptyCondition in should_types, "Expected IsEmptyCondition for legacy tolerance"

    def test_explicit_layers_no_is_empty(self) -> None:
        """Explicit source_layers list → FieldCondition only, no IsEmptyCondition."""
        from qdrant_client.models import IsEmptyCondition, FieldCondition
        f = _build_search_filter([], source_layers=["technical_analysis"])
        assert f is not None
        # Should be a FieldCondition, not a nested Filter with IsEmptyCondition
        layer_conds = [c for c in f.must if isinstance(c, FieldCondition) and c.key == "source_layer"]
        assert layer_conds, "Expected FieldCondition for source_layer"
        # No IsEmptyCondition anywhere in the top-level conditions
        assert not any(isinstance(c, IsEmptyCondition) for c in f.must)

    def test_empty_layer_list_no_layer_condition(self) -> None:
        """source_layers=[] → no layer condition (all layers visible)."""
        from qdrant_client.models import FieldCondition, Filter as QFilter
        f = _build_search_filter([], source_layers=[])
        # No filter at all (no groups, no layer)
        assert f is None

    def test_admin_with_project_layer_filter_present(self) -> None:
        """Admin with project selection still gets layer filter applied."""
        with patch("src.retrieval.search.settings") as mock_settings:
            mock_settings.default_source_layers = ["canonical_docs"]
            f = _build_search_filter(["admin"], project_ids=["proj1"], source_layers=None)
        assert f is not None
        from qdrant_client.models import Filter as QFilter
        layer_cond = next((c for c in f.must if isinstance(c, QFilter)), None)
        assert layer_cond is not None

    def test_allowed_groups_conditions_unchanged_non_admin(self) -> None:
        """RBAC allowed_groups condition is unaffected by adding source_layers."""
        from qdrant_client.models import FieldCondition
        f = _build_search_filter(["engineering"], source_layers=None)
        group_conds = [
            c for c in f.must
            if isinstance(c, FieldCondition) and c.key == "allowed_groups"
        ]
        assert group_conds, "Expected allowed_groups FieldCondition for non-admin"

    def test_no_groups_no_project_no_layer_returns_none(self) -> None:
        """Empty groups, no projects, empty layer list → no filter."""
        f = _build_search_filter([], project_ids=None, source_layers=[])
        assert f is None

    def test_blocking_condition_no_project_assignments(self) -> None:
        """Non-admin with no groups and empty project list → __none__ block condition."""
        from qdrant_client.models import FieldCondition
        f = _build_search_filter([], project_ids=[], source_layers=[])
        assert f is not None
        group_conds = [
            c for c in f.must
            if isinstance(c, FieldCondition) and c.key == "allowed_groups"
        ]
        assert group_conds
        # Should have __none__ to block all results
        assert group_conds[0].match.any == ["__none__"]


# --- Tests for layer metadata in merge_sources_by_document ---


class TestMergeSourcesLayerMetadata:
    def test_best_chunk_layer_metadata_propagated(self) -> None:
        """DocumentSource gets layer metadata from the highest-scoring chunk."""
        sources = [
            Source(
                document_id=1,
                filename="doc.md",
                chunk_index=0,
                score=0.7,
                text="Low score chunk",
                source_layer="canonical_docs",
                section="doc > A",
                unit="A",
                group="GroupA",
            ),
            Source(
                document_id=1,
                filename="doc.md",
                chunk_index=1,
                score=0.95,
                text="High score chunk",
                source_layer="canonical_docs",
                section="doc > B",
                unit="B",
                group="GroupB",
            ),
        ]
        merged = merge_sources_by_document(sources)
        assert len(merged) == 1
        assert merged[0].score == 0.95
        assert merged[0].source_layer == "canonical_docs"
        assert merged[0].unit == "B"  # from highest-scoring chunk
        assert merged[0].group == "GroupB"

    def test_legacy_source_no_layer_fields(self) -> None:
        """Sources without layer metadata merge without error (all None)."""
        sources = [
            Source(document_id=1, filename="f.md", chunk_index=0, score=0.8, text="T"),
        ]
        merged = merge_sources_by_document(sources)
        assert merged[0].source_layer is None
        assert merged[0].section is None


class TestFriendlyDisplayName:
    def test_canonical_doc(self) -> None:
        from src.retrieval.search import friendly_display_name
        assert friendly_display_name("phase2/01_ARCHITECTURE_MAP.md") == (
            "01_ARCHITECTURE_MAP.md (Project: phase2)"
        )

    def test_internal_doc_never_shows_raw_path(self) -> None:
        from src.retrieval.search import friendly_display_name
        name = friendly_display_name(
            "phase2/_internal/technical_analysis/Item.md", "technical_analysis"
        )
        assert name == "Technical Analysis: Item (Project: phase2)"
        assert "_internal" not in name

    def test_internal_doc_percent_decoded(self) -> None:
        from src.retrieval.search import friendly_display_name
        name = friendly_display_name(
            "proj/_internal/technical_analysis/Training%7CJasmijn.md",
            "technical_analysis",
        )
        assert "Training|Jasmijn" in name
        assert "%7C" not in name

    def test_internal_layer_from_path_when_not_passed(self) -> None:
        from src.retrieval.search import friendly_display_name
        name = friendly_display_name("proj/_internal/scorecard/scorecard.md")
        assert name == "Quality Scorecard: scorecard (Project: proj)"

    def test_unscoped_filename_unchanged(self) -> None:
        from src.retrieval.search import friendly_display_name
        assert friendly_display_name("plain.md") == "plain.md"


class TestSearchLayered:
    """Rung-2 helper: explicit layers + same RBAC/project scoping."""

    async def test_passes_explicit_layers_and_rbac(self) -> None:
        from src.retrieval import search as search_mod

        with (
            patch.object(search_mod.settings, "reranker_enabled", False),
            patch.object(search_mod.settings, "hybrid_search_enabled", False),
            patch.object(search_mod, "search_similar", new_callable=AsyncMock) as mock_dense,
        ):
            mock_dense.return_value = []
            await search_mod.search_layered(
                AsyncMock(),
                "query",
                ["groupA"],
                source_layers=["technical_analysis", "support_maps"],
                project_ids=["phase2"],
            )

        kwargs = mock_dense.await_args.kwargs
        assert kwargs["source_layers"] == ["technical_analysis", "support_maps"]
        assert kwargs["user_groups"] == ["groupA"]
        assert kwargs["project_ids"] == ["phase2"]

    async def test_uses_reranker_strategy_when_enabled(self) -> None:
        from src.retrieval import search as search_mod

        with (
            patch.object(search_mod.settings, "reranker_enabled", True),
            patch.object(search_mod, "search_and_rerank", new_callable=AsyncMock) as mock_rerank,
        ):
            mock_rerank.return_value = []
            await search_mod.search_layered(
                AsyncMock(), "query", ["g"], source_layers=["scorecard"]
            )

        assert mock_rerank.await_args.kwargs["source_layers"] == ["scorecard"]


def _make_chunk(doc_id: int, idx: int, *, section: str | None = None, unit: str | None = None, layer="technical_analysis") -> Source:
    return Source(
        document_id=doc_id, filename=f"p/_internal/{layer}/doc.md", chunk_index=idx,
        score=0.5, text=f"chunk {doc_id}:{idx}", source_layer=layer,
        section=section, unit=unit, project_id="p",
    )


class TestSearchLayeredDeepRung:
    """Rung-2 recall widening: settings, unit merge, forced expansion."""

    async def test_uses_rung2_recall_settings(self) -> None:
        from src.retrieval import search as search_mod

        with (
            patch.object(search_mod.settings, "reranker_enabled", True),
            patch.object(search_mod.settings, "rung2_query_expansion_enabled", False),
            patch.object(search_mod.settings, "rung2_rerank_top_n", 75),
            patch.object(search_mod.settings, "rung2_prefetch_limit", 150),
            patch.object(search_mod, "search_and_rerank", new_callable=AsyncMock) as mock_rerank,
            patch.object(search_mod, "expand_section_chunks", new_callable=AsyncMock) as mock_section,
        ):
            mock_rerank.return_value = [_make_chunk(1, 0)]
            mock_section.side_effect = lambda client, sources, *a, **k: sources

            await search_mod.search_layered(
                AsyncMock(), "q", ["g"], source_layers=["technical_analysis"],
            )

        kwargs = mock_rerank.await_args.kwargs
        assert kwargs["rerank_candidates"] == 75
        assert kwargs["prefetch_limit"] == 150

    async def test_unit_hits_lead_even_when_semantic_misses(self) -> None:
        from src.retrieval import search as search_mod

        unit_chunk = _make_chunk(7, 2, unit="sjItem_OffRamp_DB")
        with (
            patch.object(search_mod.settings, "reranker_enabled", True),
            patch.object(search_mod.settings, "rung2_query_expansion_enabled", False),
            patch.object(search_mod, "search_and_rerank", new_callable=AsyncMock) as mock_rerank,
            patch.object(search_mod, "fetch_unit_chunks", new_callable=AsyncMock) as mock_units,
            patch.object(search_mod, "expand_section_chunks", new_callable=AsyncMock) as mock_section,
        ):
            mock_rerank.return_value = []
            mock_units.return_value = [unit_chunk]
            mock_section.side_effect = lambda client, sources, *a, **k: sources

            results = await search_mod.search_layered(
                AsyncMock(), "q", ["g"], source_layers=["technical_analysis"],
                matched_units=["sjItem_OffRamp_DB"],
            )

        assert results == [unit_chunk], "alias-matched unit chunks must survive a semantic miss"
        assert mock_units.await_args.kwargs["source_layers"] == ["technical_analysis"]

    async def test_forced_expansion_runs_with_global_disabled(self) -> None:
        from src.retrieval import search as search_mod

        with (
            patch.object(search_mod.settings, "reranker_enabled", True),
            patch.object(search_mod.settings, "query_expansion_enabled", False),
            patch.object(search_mod.settings, "rung2_query_expansion_enabled", True),
            patch.object(search_mod, "expand_query", new_callable=AsyncMock) as mock_expand,
            patch.object(search_mod, "search_and_rerank", new_callable=AsyncMock) as mock_rerank,
            patch.object(search_mod, "expand_section_chunks", new_callable=AsyncMock) as mock_section,
        ):
            mock_expand.return_value = ["q", "alt one", "alt two"]
            mock_rerank.return_value = []
            mock_section.side_effect = lambda client, sources, *a, **k: sources

            await search_mod.search_layered(
                AsyncMock(), "q", ["g"], source_layers=["technical_analysis"],
            )

        mock_expand.assert_awaited_once_with("q", force=True)
        assert mock_rerank.await_count == 3, "original + both expansions searched"


class TestUnitFilter:
    def test_unit_filter_carries_rbac_and_layers(self) -> None:
        from src.retrieval.search import _build_unit_filter

        f = _build_unit_filter(
            ["groupA"], ["sjItem_OffRamp_DB"],
            project_ids=["phase2"], source_layers=["technical_analysis"],
        )
        compiled = str(f)
        assert "unit" in compiled
        assert "sjItem_OffRamp_DB" in compiled
        assert "source_layer" in compiled
        assert "technical_analysis" in compiled
        assert "phase2" in compiled


class TestSectionExpansion:
    async def test_fetches_complete_section_in_order(self, mock_qdrant) -> None:
        from src.retrieval.search import expand_section_chunks

        hit = _make_chunk(1, 5, section="Item > ERROR_HANDLING")
        section_points = [
            MagicMock(payload={
                "document_id": 1, "filename": "p/_internal/technical_analysis/doc.md",
                "chunk_index": idx, "text": f"t{idx}", "section": "Item > ERROR_HANDLING",
            })
            for idx in (6, 4, 5)
        ]
        mock_qdrant.scroll = AsyncMock(return_value=(section_points, None))

        result = await expand_section_chunks(
            mock_qdrant, [hit], ["g"], source_layers=["technical_analysis"],
        )

        assert [s.chunk_index for s in result] == [4, 5, 6], "section in chunk_index order"

    async def test_source_without_section_kept_as_is(self, mock_qdrant) -> None:
        from src.retrieval.search import expand_section_chunks

        hit = _make_chunk(1, 5, section=None)
        mock_qdrant.scroll = AsyncMock()

        result = await expand_section_chunks(
            mock_qdrant, [hit], ["g"], source_layers=["technical_analysis"],
        )

        assert result == [hit]
        mock_qdrant.scroll.assert_not_awaited()

    async def test_cap_limits_fetched_chunks(self, mock_qdrant) -> None:
        from src.retrieval.search import expand_section_chunks

        hit = _make_chunk(1, 0, section="S")
        mock_qdrant.scroll = AsyncMock(return_value=([], None))

        await expand_section_chunks(
            mock_qdrant, [hit], ["g"], source_layers=["technical_analysis"], limit=7,
        )

        assert mock_qdrant.scroll.await_args.kwargs["limit"] == 7


class TestEnsureCollectionIndexes:
    async def test_unit_and_section_indexes_requested(self) -> None:
        from src.retrieval.vector_store import ensure_collection

        client = AsyncMock()
        collections = MagicMock()
        existing = MagicMock()
        existing.name = "documents"
        collections.collections = [existing]
        client.get_collections.return_value = collections
        info = MagicMock()
        info.config.params.vectors = {"dense": MagicMock(size=1024)}
        client.get_collection.return_value = info

        with patch("src.retrieval.vector_store.get_embedding_dim", return_value=1024):
            await ensure_collection(client)

        indexed = {c.kwargs["field_name"] for c in client.create_payload_index.await_args_list}
        assert {"allowed_groups", "document_id", "source_layer", "unit", "section"} <= indexed
