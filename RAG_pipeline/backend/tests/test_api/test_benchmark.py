"""Tests for benchmark API endpoints (src/api/routes/benchmark.py)."""

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import BenchmarkRun


@pytest.fixture
def mock_deps():
    """Override dependencies for benchmark tests."""
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()

    # Benchmark endpoints don't require auth, but app might still resolve deps
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant

    yield {"db": mock_db}
    app.dependency_overrides.clear()


def _mock_benchmark_run(run_id: int = 1):
    run = MagicMock(spec=BenchmarkRun)
    run.id = run_id
    run.tag = "test-v1"
    run.corpus_name = "default"
    run.corpus_version = "1.0"
    run.llm_model = "kimi-k2.6"
    run.embed_model = "voyage-4-large"
    run.reranker_model = "rerank-2.5"
    run.num_questions = 10
    run.avg_precision_at_5 = 0.8
    run.avg_relevance_score = 4.2
    run.avg_latency_ms = 3500
    run.total_input_tokens = 50000
    run.total_output_tokens = 10000
    run.total_cost_usd = 0.15
    run.results = [{"question": "test?", "answer": "yes", "relevance": 4}]
    run.created_at = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)
    return run


class TestListBenchmarkRuns:
    async def test_list_runs_empty(self, mock_deps):
        mock_db = mock_deps["db"]
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/benchmark/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["total"] == 0

    async def test_list_runs_with_data(self, mock_deps):
        mock_db = mock_deps["db"]
        run = _mock_benchmark_run()

        result = MagicMock()
        result.scalars.return_value.all.return_value = [run]
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/benchmark/runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["runs"][0]["tag"] == "test-v1"
        assert data["runs"][0]["num_questions"] == 10


class TestGetBenchmarkRun:
    async def test_get_run_found(self, mock_deps):
        mock_db = mock_deps["db"]
        run = _mock_benchmark_run(run_id=5)
        mock_db.get = AsyncMock(return_value=run)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/benchmark/runs/5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 5
        assert data["tag"] == "test-v1"
        assert data["results"] is not None

    async def test_get_run_not_found(self, mock_deps):
        mock_db = mock_deps["db"]
        mock_db.get = AsyncMock(return_value=None)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/benchmark/runs/999")

        assert resp.status_code == 404
