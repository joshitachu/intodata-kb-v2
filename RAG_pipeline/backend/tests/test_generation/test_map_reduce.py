from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.map_reduce import (
    MAP_BATCH_SIZE,
    map_summarize,
    reduce_summaries,
    resolve_document_id,
    retrieve_document_chunks,
    run_map_reduce,
)


def _make_point(score: float, payload: dict):
    point = MagicMock()
    point.score = score
    point.payload = payload
    return point


class TestResolveDocumentId:
    @pytest.mark.asyncio
    async def test_returns_doc_id_and_filename(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = [
            _make_point(0.8, {"document_id": 42, "filename": "hr_policy.pdf"}),
        ]
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            resolved = await resolve_document_id(mock_qdrant, "summarize HR policy", ["hr"])

        assert resolved == (42, "hr_policy.pdf")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_results(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = []
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            resolved = await resolve_document_id(mock_qdrant, "summarize nothing", ["hr"])

        assert resolved is None

    @pytest.mark.asyncio
    async def test_returns_none_when_score_too_low(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = [
            _make_point(0.1, {"document_id": 1, "filename": "low.pdf"}),
        ]
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            resolved = await resolve_document_id(mock_qdrant, "summarize xyz", [])

        assert resolved is None

    @pytest.mark.asyncio
    async def test_applies_rbac_filter(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = [
            _make_point(0.8, {"document_id": 1, "filename": "doc.pdf"}),
        ]
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            await resolve_document_id(mock_qdrant, "summarize doc", ["engineering"])

        call_kwargs = mock_qdrant.query_points.call_args.kwargs
        assert call_kwargs["query_filter"] is not None

    @pytest.mark.asyncio
    async def test_no_rbac_group_filter_when_no_groups(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = [
            _make_point(0.8, {"document_id": 1, "filename": "doc.pdf"}),
        ]
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            await resolve_document_id(mock_qdrant, "summarize doc", [])

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

    @pytest.mark.asyncio
    async def test_applies_project_filter_when_provided(self) -> None:
        mock_qdrant = AsyncMock()
        result = MagicMock()
        result.points = [
            _make_point(0.8, {"document_id": 1, "filename": "doc.pdf"}),
        ]
        mock_qdrant.query_points.return_value = result

        with patch("src.generation.map_reduce.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 768
            await resolve_document_id(
                mock_qdrant,
                "summarize doc",
                ["admin"],
                project_ids=["project-alpha"],
            )

        call_kwargs = mock_qdrant.query_points.call_args.kwargs
        query_filter = call_kwargs["query_filter"]
        assert query_filter is not None
        assert any(
            getattr(condition, "key", None) == "allowed_groups"
            and condition.match.any == ["project-alpha"]
            for condition in query_filter.must
        )


class TestRetrieveDocumentChunks:
    @pytest.mark.asyncio
    async def test_returns_chunks_sorted_by_index(self) -> None:
        mock_qdrant = AsyncMock()

        point1 = MagicMock()
        point1.payload = {"chunk_index": 2, "text": "chunk C"}
        point2 = MagicMock()
        point2.payload = {"chunk_index": 0, "text": "chunk A"}
        point3 = MagicMock()
        point3.payload = {"chunk_index": 1, "text": "chunk B"}

        mock_qdrant.scroll.return_value = ([point1, point2, point3], None)

        chunks = await retrieve_document_chunks(mock_qdrant, 42, ["hr"])
        assert chunks == ["chunk A", "chunk B", "chunk C"]

    @pytest.mark.asyncio
    async def test_handles_pagination(self) -> None:
        mock_qdrant = AsyncMock()

        point1 = MagicMock()
        point1.payload = {"chunk_index": 0, "text": "page 1"}
        point2 = MagicMock()
        point2.payload = {"chunk_index": 1, "text": "page 2"}

        # First scroll returns 1 point + next_offset, second returns 1 point + None
        mock_qdrant.scroll.side_effect = [
            ([point1], "next-id"),
            ([point2], None),
        ]

        chunks = await retrieve_document_chunks(mock_qdrant, 42, [])
        assert chunks == ["page 1", "page 2"]
        assert mock_qdrant.scroll.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_chunks(self) -> None:
        mock_qdrant = AsyncMock()
        mock_qdrant.scroll.return_value = ([], None)

        chunks = await retrieve_document_chunks(mock_qdrant, 999, [])
        assert chunks == []


class TestMapSummarize:
    @pytest.mark.asyncio
    async def test_calls_generate_answer(self) -> None:
        with patch("src.generation.map_reduce.generate_answer", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "Summary of batch"
            result = await map_summarize(["chunk 1", "chunk 2"], batch_index=0)

        assert result == "Summary of batch"
        mock_gen.assert_called_once()
        # Verify the prompt contains the chunk text
        messages = mock_gen.call_args[0][0]
        assert "chunk 1" in messages[0]["content"]
        assert "chunk 2" in messages[0]["content"]


class TestReduceSummaries:
    @pytest.mark.asyncio
    async def test_combines_summaries(self) -> None:
        with patch("src.generation.map_reduce.generate_answer", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "Final combined summary"
            result = await reduce_summaries(["summary A", "summary B"], "doc.pdf")

        assert result == "Final combined summary"
        mock_gen.assert_called_once()
        messages = mock_gen.call_args[0][0]
        assert "doc.pdf" in messages[0]["content"]
        assert "summary A" in messages[0]["content"]
        assert "summary B" in messages[0]["content"]


class TestRunMapReduce:
    @pytest.mark.asyncio
    async def test_single_batch_skips_reduce(self) -> None:
        """When chunks fit in a single batch, only map is called (no reduce)."""
        mock_qdrant = AsyncMock()

        with (
            patch("src.generation.map_reduce.retrieve_document_chunks", new_callable=AsyncMock) as mock_retrieve,
            patch("src.generation.map_reduce.map_summarize", new_callable=AsyncMock) as mock_map,
            patch("src.generation.map_reduce.reduce_summaries", new_callable=AsyncMock) as mock_reduce,
        ):
            # Fewer chunks than MAP_BATCH_SIZE
            mock_retrieve.return_value = ["chunk 1", "chunk 2", "chunk 3"]
            mock_map.return_value = "Single batch summary"

            answer, source = await run_map_reduce(mock_qdrant, 1, "test.pdf", ["hr"])

        assert answer == "Single batch summary"
        assert source.document_id == 1
        assert source.filename == "test.pdf"
        assert source.reference_number == 1
        mock_map.assert_called_once()
        mock_reduce.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_batches_uses_reduce(self) -> None:
        """When chunks exceed MAP_BATCH_SIZE, map+reduce are both called."""
        mock_qdrant = AsyncMock()

        with (
            patch("src.generation.map_reduce.retrieve_document_chunks", new_callable=AsyncMock) as mock_retrieve,
            patch("src.generation.map_reduce.map_summarize", new_callable=AsyncMock) as mock_map,
            patch("src.generation.map_reduce.reduce_summaries", new_callable=AsyncMock) as mock_reduce,
        ):
            # More chunks than MAP_BATCH_SIZE (8) → 2 batches
            mock_retrieve.return_value = [f"chunk {i}" for i in range(12)]
            mock_map.side_effect = ["Summary batch 0", "Summary batch 1"]
            mock_reduce.return_value = "Final reduced summary"

            answer, source = await run_map_reduce(mock_qdrant, 1, "big.pdf", [])

        assert answer == "Final reduced summary"
        assert mock_map.call_count == 2
        mock_reduce.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_chunks_returns_message(self) -> None:
        """When document has no chunks, returns informative message."""
        mock_qdrant = AsyncMock()

        with patch("src.generation.map_reduce.retrieve_document_chunks", new_callable=AsyncMock) as mock_retrieve:
            mock_retrieve.return_value = []

            answer, source = await run_map_reduce(mock_qdrant, 1, "empty.pdf", [])

        assert "no content" in answer.lower()
        assert source.document_id == 1

    @pytest.mark.asyncio
    async def test_batching_logic(self) -> None:
        """Verify correct number of batches are created."""
        mock_qdrant = AsyncMock()

        with (
            patch("src.generation.map_reduce.retrieve_document_chunks", new_callable=AsyncMock) as mock_retrieve,
            patch("src.generation.map_reduce.map_summarize", new_callable=AsyncMock) as mock_map,
            patch("src.generation.map_reduce.reduce_summaries", new_callable=AsyncMock) as mock_reduce,
        ):
            # 20 chunks / batch_size 8 = 3 batches (8, 8, 4)
            mock_retrieve.return_value = [f"chunk {i}" for i in range(20)]
            mock_map.side_effect = ["S1", "S2", "S3"]
            mock_reduce.return_value = "Final"

            await run_map_reduce(mock_qdrant, 1, "doc.pdf", [])

        assert mock_map.call_count == 3
        # Verify batch sizes
        batch_sizes = [len(call.args[0]) for call in mock_map.call_args_list]
        assert batch_sizes == [8, 8, 4]
