"""Tests for optional semantic recall over curated runtime insights."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.db.models.runtime_insights import (
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.runtime.semantic.models import RuntimeSemanticResult, RuntimeSemanticSource
from src.runtime.semantic.projection import (
    error_signature_document,
    execution_summary_document,
    incident_document,
    stable_point_id,
)
from src.runtime.semantic.service import (
    RUNTIME_SEMANTIC_COLLECTION,
    RuntimeSemanticService,
    merge_runtime_search_results,
)


class _RecordingIndexer:
    def __init__(self) -> None:
        self.calls: list[list] = []

    async def upsert(self, documents: list) -> bool:
        self.calls.append(documents)
        return True


def test_projection_is_stable_and_contains_only_curated_insight_fields():
    summary = RuntimeExecutionSummaryRow(
        project_id="project-a",
        execution_id="execution-1",
        job_name="customer-load",
        summary_text="Execution failed with a database timeout",
        failed_component="database-input",
        primary_error_code="ORA-12170",
        primary_exception_class="SQLRecoverableException",
    )
    first = execution_summary_document(summary)
    second = execution_summary_document(summary)

    assert first.point_id == second.point_id
    assert first.point_id == stable_point_id(
        "project-a", RuntimeSemanticSource.EXECUTION_SUMMARY, "execution-1"
    )
    assert "Execution failed" in first.text
    assert "runtime_log_events" not in first.text
    assert set(first.payload) == {
        "project_id",
        "source",
        "record_id",
        "execution_id",
        "incident_id",
        "signature_hash",
        "text",
    }


def test_incident_projection_excludes_evidence_and_signature_is_aggregate_only():
    incident = RuntimeIncidentRow(
        project_id="project-a",
        incident_id="incident-1",
        execution_id="execution-1",
        signature_hash="signature-1",
        symptom_summary="Customer load failed",
        root_cause_candidate="Database connection timed out",
        next_checks=["Check listener availability"],
    )
    signature = RuntimeErrorSignatureRow(
        project_id="project-a",
        signature_hash="signature-1",
        summary_text="Repeated Oracle connection timeout",
        error_code="ORA-12170",
        occurrence_count=3,
    )

    incident_doc = incident_document(incident)
    signature_doc = error_signature_document(signature)

    assert "Check listener availability" in incident_doc.text
    assert "excerpt" not in incident_doc.payload
    assert signature_doc.source is RuntimeSemanticSource.ERROR_SIGNATURE
    assert "occurrences: 3" in signature_doc.text


async def test_insight_hook_indexes_only_persisted_curated_rows(db_session):
    from src.runtime.insights.semantic import index_runtime_insights_for_execution

    db_session.add_all(
        [
            RuntimeExecutionSummaryRow(
                project_id="project-a",
                execution_id="execution-1",
                job_name="customer-load",
                summary_text="Execution failed with database timeout",
            ),
            RuntimeIncidentRow(
                project_id="project-a",
                incident_id="incident-1",
                execution_id="execution-1",
                signature_hash="signature-1",
                symptom_summary="Customer load failed",
                root_cause_candidate="Database connection timed out",
            ),
            RuntimeErrorSignatureRow(
                project_id="project-a",
                signature_hash="signature-1",
                summary_text="Repeated database timeout",
                occurrence_count=2,
            ),
        ]
    )
    await db_session.flush()
    indexer = _RecordingIndexer()

    assert await index_runtime_insights_for_execution(
        db_session,
        project_id="project-a",
        execution_id="execution-1",
        signature_hash="signature-1",
        indexer=indexer,
    )
    documents = indexer.calls[0]
    assert [document.source for document in documents] == [
        RuntimeSemanticSource.EXECUTION_SUMMARY,
        RuntimeSemanticSource.INCIDENT,
        RuntimeSemanticSource.ERROR_SIGNATURE,
    ]
    assert all("raw log" not in document.text.lower() for document in documents)


async def test_upsert_is_deterministic_and_degrades_when_embeddings_fail(monkeypatch):
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(collections=[])
    service = RuntimeSemanticService(client)
    doc = execution_summary_document(
        RuntimeExecutionSummaryRow(
            project_id="project-a",
            execution_id="execution-1",
            job_name="customer-load",
            summary_text="Execution completed with warnings",
        )
    )

    async def embeddings(_: list[str]) -> list[list[float]]:
        return [[0.1, 0.2]]

    monkeypatch.setattr("src.runtime.semantic.service.embed_texts", embeddings)
    assert await service.upsert([doc]) is True
    upsert = client.upsert.call_args.kwargs
    assert upsert["collection_name"] == RUNTIME_SEMANTIC_COLLECTION
    assert upsert["points"][0].id == doc.point_id

    async def unavailable(_: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr("src.runtime.semantic.service.embed_texts", unavailable)
    assert await service.upsert([doc]) is False


async def test_search_hard_filters_projects_bounds_limit_and_degrades(monkeypatch):
    client = AsyncMock()
    client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                score=0.92,
                payload={
                    "project_id": "project-a",
                    "source": "incident",
                    "record_id": "incident-1",
                    "execution_id": "execution-1",
                    "incident_id": "incident-1",
                    "signature_hash": "signature-1",
                    "text": "Database connection timed out.",
                },
            )
        ]
    )
    service = RuntimeSemanticService(client)

    async def embedding(_: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr("src.runtime.semantic.service.embed_text", embedding)
    results = await service.search("why did the load fail", project_ids=["project-a"], top_k=999)

    assert len(results) == 1
    assert results[0].provenance == ("semantic",)
    search = client.query_points.call_args.kwargs
    assert search["limit"] == 20
    assert search["query_filter"].must[0].match.any == ["project-a"]
    assert await service.search("anything", project_ids=[]) == []

    async def unavailable(_: str) -> list[float]:
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr("src.runtime.semantic.service.embed_text", unavailable)
    assert await service.search("anything", project_ids=["project-a"]) == []


async def test_delete_is_idempotent_and_failure_is_non_fatal():
    client = AsyncMock()
    service = RuntimeSemanticService(client)

    assert await service.delete([]) is True
    assert await service.delete(["cfef47c7-5ebc-5f63-8a1a-2e342b20e68d"]) is True
    assert client.delete.call_args.kwargs["points_selector"].points == [
        "cfef47c7-5ebc-5f63-8a1a-2e342b20e68d"
    ]
    client.delete.side_effect = RuntimeError("qdrant unavailable")
    assert await service.delete(["cfef47c7-5ebc-5f63-8a1a-2e342b20e68d"]) is False


def test_merge_keeps_both_provenances_and_non_overlapping_fts_results():
    fts = RuntimeSemanticResult(
        project_id="project-a",
        source=RuntimeSemanticSource.INCIDENT,
        record_id="incident-1",
        text="Database connection timed out.",
        score=0.7,
        incident_id="incident-1",
        provenance=("fts",),
    )
    semantic_same = fts.model_copy(update={"score": 0.9, "provenance": ("semantic",)})
    semantic_other = RuntimeSemanticResult(
        project_id="project-a",
        source=RuntimeSemanticSource.ERROR_SIGNATURE,
        record_id="signature-1",
        text="Repeated Oracle connection timeout.",
        score=0.8,
        provenance=("semantic",),
    )

    merged = merge_runtime_search_results([fts], [semantic_same, semantic_other])

    assert [result.record_id for result in merged] == ["incident-1", "signature-1"]
    assert merged[0].score == 0.9
    assert merged[0].provenance == ("fts", "semantic")
