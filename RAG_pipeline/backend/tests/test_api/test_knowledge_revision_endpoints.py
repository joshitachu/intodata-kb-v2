"""Authenticated HTTP boundary for the active RAG revision authority."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock

from src.api.main import app


async def test_revision_endpoints_prepare_then_activate(db_session, monkeypatch) -> None:
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")
    qdrant = AsyncMock()
    inherited = MagicMock()
    inherited.id = "r1-unchanged"
    inherited.vector = {"dense": [0.1]}
    inherited.payload = {
        "project_id": "p",
        "knowledge_revision_id": "R1",
        "artifact_id": "02_UNCHANGED.md",
        "filename": "p/02_UNCHANGED.md",
    }
    qdrant.scroll.return_value = ([inherited], None)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.post(
                "/api/v1/ingest/revisions/prepare",
                json={"project_id": "p", "knowledge_revision_id": "R1"},
            )
            assert unauthenticated.status_code == 401

            prepared = await client.post(
                "/api/v1/ingest/revisions/prepare",
                headers={"Authorization": "Bearer test-key"},
                json={
                    "project_id": "p",
                    "knowledge_revision_id": "R1",
                    "membership_hashes": {
                        "01_CHANGED.md": "a" * 64,
                        "02_UNCHANGED.md": "u" * 64,
                    },
                },
            )
            assert prepared.status_code == 200
            assert prepared.json()["state"] == "prepared"

            before = await client.get(
                "/api/v1/ingest/revisions/p/active",
                headers={"Authorization": "Bearer test-key"},
            )
            assert before.json()["knowledge_revision_id"] is None

            activated = await client.post(
                "/api/v1/ingest/revisions/p/R1/activate",
                headers={"Authorization": "Bearer test-key"},
            )
            assert activated.status_code == 200
            assert activated.json()["state"] == "active"

            after = await client.get(
                "/api/v1/ingest/revisions/p/active",
                headers={"Authorization": "Bearer test-key"},
            )
            assert after.json()["knowledge_revision_id"] == "R1"

            prepared_r2 = await client.post(
                "/api/v1/ingest/revisions/prepare",
                headers={"Authorization": "Bearer test-key"},
                json={
                    "project_id": "p",
                    "knowledge_revision_id": "R2",
                    "base_knowledge_revision_id": "R1",
                    "membership_hashes": {
                        "01_CHANGED.md": "b" * 64,
                        "02_UNCHANGED.md": "u" * 64,
                    },
                },
            )
            assert prepared_r2.status_code == 200
            clone = qdrant.upsert.await_args.kwargs["points"][0]
            assert clone.payload["knowledge_revision_id"] == "R2"
            assert clone.payload["artifact_id"] == "02_UNCHANGED.md"

            activated_r2 = await client.post(
                "/api/v1/ingest/revisions/p/R2/activate",
                headers={"Authorization": "Bearer test-key"},
            )
            assert activated_r2.status_code == 200
            assert activated_r2.json()["previous_knowledge_revision_id"] == "R1"

            stale_rollback = await client.post(
                "/api/v1/ingest/revisions/p/R1/rollback",
                headers={"Authorization": "Bearer test-key"},
                json={"expected_active_revision_id": "wrong"},
            )
            assert stale_rollback.status_code == 409

            rolled_back = await client.post(
                "/api/v1/ingest/revisions/p/R1/rollback",
                headers={"Authorization": "Bearer test-key"},
                json={"expected_active_revision_id": "R2"},
            )
            assert rolled_back.status_code == 200
            assert rolled_back.json()["previous_knowledge_revision_id"] == "R2"

            after_rollback = await client.get(
                "/api/v1/ingest/revisions/p/active",
                headers={"Authorization": "Bearer test-key"},
            )
            assert after_rollback.json()["knowledge_revision_id"] == "R1"
    finally:
        app.dependency_overrides.clear()
