"""Focused contract tests for deterministic static/runtime correlation."""

from __future__ import annotations

import datetime
import hashlib
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from src.api.main import app
from src.db.models import Role, User
from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeJobRunRow, RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeExecutionSummaryRow
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.insights.parser import parse_runtime_log_message
from src.runtime.topology.correlation import RuntimeCorrelationService
from src.runtime.topology.models import (
    RuntimeJobScanMapCreate,
    RuntimeStaticTopologySnapshotIn,
)
from src.runtime.topology.repository import RuntimeTopologyRepository
from src.runtime.topology.repository import (
    RuntimeJobScanMapValidationError,
    StaticTopologySnapshotConflictError,
)

_PROJECT = "assetcheck"
_OTHER = "other-project"
_EXECUTION = "tmc-exec-1"
_NOW = datetime.datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


async def _seed_correlatable_failure(session) -> None:
    session.add(RuntimeExecutionRow(
        project_id=_PROJECT,
        execution_id=_EXECUTION,
        job_name="AssetCheckTask",
        project_name=_PROJECT,
        state="failed",
        started_at=_NOW,
        ended_at=_NOW + datetime.timedelta(seconds=2),
        duration_ms=2_000,
        source_key="tmc",
        ingested_at=_NOW,
    ))
    session.add(RuntimeJobRunRow(
        project_id=_PROJECT,
        source_key="tmc",
        run_id=_EXECUTION,
        job_id="tmc-task-42",
        job_name="AssetCheckTask",
        execution_id=_EXECUTION,
        state="failed",
        started_at=_NOW,
        ended_at=_NOW + datetime.timedelta(seconds=2),
        duration_ms=2_000,
        ingested_at=_NOW,
    ))
    message = "[tMap_1] Cannot insert NULL into column 'requiredField'"
    session.add(RuntimeLogEventRow(
        project_id=_PROJECT,
        execution_id=_EXECUTION,
        timestamp=_NOW + datetime.timedelta(seconds=1),
        severity="error",
        message=message,
        line_ordinal=0,
        event_fingerprint=hashlib.sha256(message.encode()).hexdigest(),
        source_key="tmc",
        ingested_at=_NOW,
    ))
    await session.flush()
    await build_runtime_insights_for_execution(
        session,
        project_id=_PROJECT,
        execution_id=_EXECUTION,
        source_key="tmc",
        ingested_at=_NOW,
    )


async def _publish_snapshot_and_map(session) -> None:
    snapshot = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="a" * 64,
        source_sequence=10,
        platform="talend",
        jobs=[
            {"static_job_id": "talend-maximo", "name": "Maximo extract"},
            {
                "static_job_id": "talend-transform",
                "name": "Transform to AssetCheck",
                "source_root": "AssetCheck",
                "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
            },
            {"static_job_id": "talend-assetcheck", "name": "AssetCheck write"},
        ],
        dependencies=[
            {
                "source_job_id": "talend-transform",
                "target_job_id": "talend-assetcheck",
                "dependency_type": "control_flow",
                "evidence_id": "manifest:relation:1",
            },
            {
                "source_job_id": "talend-transform",
                "target_job_id": "talend-assetcheck",
                "dependency_type": "data_lineage",
                "evidence_id": "lineage:boundary:1",
            },
        ],
        lineage=[
            {
                "job_id": "talend-transform",
                "source_component": "tRESTClient_1",
                "target_component": "tMap_1",
                "source_table": "maximo",
                "source_column": "field1",
                "target_table": "assetcheck",
                "target_column": "requiredField",
                "confidence": "certain",
                "transformation_types": ["direct_map"],
                "expressions": ["maximo.field1"],
                "evidence_id": "lineage:field1-requiredField",
            },
        ],
    )
    repo = RuntimeTopologyRepository(session)
    await repo.upsert_snapshot(snapshot, received_at=_NOW)
    await repo.upsert_job_scan_map(
        RuntimeJobScanMapCreate(
            project_id=_PROJECT,
            source_key="tmc",
            runtime_job_id="tmc-task-42",
            static_job_id="talend-transform",
        ),
        created_by="tester",
        created_at=_NOW,
    )
    await session.flush()


async def test_correlates_null_rejection_to_static_mapping_and_downstream_impact(db_session):
    await _seed_correlatable_failure(db_session)
    await _publish_snapshot_and_map(db_session)

    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)

    assert result.status == "matched"
    assert result.static_job_id == "talend-transform"
    assert result.component_match == "exact_normalized"
    assert result.field_violation is not None
    assert result.field_violation.field_name == "requiredField"
    assert result.field_violation.kind == "null_rejected"
    assert result.lineage_evidence[0].source_column == "field1"
    assert result.lineage_evidence[0].target_column == "requiredField"
    assert result.lineage_evidence[0].transformation_types == ["direct_map"]
    assert result.lineage_evidence[0].expressions == ["maximo.field1"]
    assert [impact.static_job_id for impact in result.control_flow_impacts] == ["talend-assetcheck"]
    assert [impact.static_job_id for impact in result.data_lineage_impacts] == ["talend-assetcheck"]
    assert "API specification change" in result.note


async def test_unmapped_runtime_job_is_explicitly_unmatched(db_session):
    await _seed_correlatable_failure(db_session)
    await RuntimeTopologyRepository(db_session).upsert_snapshot(
        RuntimeStaticTopologySnapshotIn(
            project_id=_PROJECT,
            scan_revision="b" * 64,
            source_sequence=10,
            platform="talend",
            jobs=[{"static_job_id": "talend-transform", "name": "Transform"}],
        )
    )
    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)
    assert result.status == "unmatched"
    assert result.static_job_id is None


async def test_snapshot_revision_replay_is_idempotent_and_conflicts_are_rejected(db_session):
    repo = RuntimeTopologyRepository(db_session)
    old = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="1" * 64,
        source_sequence=1,
        platform="talend",
        jobs=[{"static_job_id": "old", "name": "Old"}],
    )
    newest = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="2" * 64,
        source_sequence=2,
        platform="talend",
        jobs=[{"static_job_id": "new", "name": "New"}],
    )
    first = await repo.upsert_snapshot(old, received_at=_NOW)
    await repo.upsert_snapshot(newest, received_at=_NOW + datetime.timedelta(minutes=1))
    replay = await repo.upsert_snapshot(old, received_at=_NOW + datetime.timedelta(days=1))
    assert replay.received_at == first.received_at
    assert (await repo.latest_snapshot(_PROJECT)).scan_revision == newest.scan_revision

    conflict = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision=old.scan_revision,
        source_sequence=old.source_sequence,
        platform="talend",
        jobs=[{"static_job_id": "old", "name": "Changed evidence"}],
    )
    with pytest.raises(StaticTopologySnapshotConflictError):
        await repo.upsert_snapshot(conflict)


async def test_source_sequence_is_authoritative_when_snapshots_arrive_out_of_order(db_session):
    repo = RuntimeTopologyRepository(db_session)
    newer = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="9" * 64,
        source_sequence=20,
        platform="talend",
        jobs=[{"static_job_id": "new", "name": "New"}],
    )
    delayed_older = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="8" * 64,
        source_sequence=19,
        platform="talend",
        jobs=[{"static_job_id": "old", "name": "Old"}],
    )
    await repo.upsert_snapshot(newer, received_at=_NOW)
    await repo.upsert_snapshot(delayed_older, received_at=_NOW + datetime.timedelta(days=1))

    latest = await repo.latest_snapshot(_PROJECT)
    assert latest is not None
    assert latest.scan_revision == newer.scan_revision
    assert latest.source_sequence == 20

    reused_generation = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="7" * 64,
        source_sequence=20,
        platform="talend",
        jobs=[{"static_job_id": "other", "name": "Other"}],
    )
    with pytest.raises(StaticTopologySnapshotConflictError):
        await repo.upsert_snapshot(reused_generation)


async def test_job_scan_mapping_must_target_real_static_job(db_session):
    repo = RuntimeTopologyRepository(db_session)
    invalid = RuntimeJobScanMapCreate(
        project_id=_PROJECT,
        source_key="tmc",
        runtime_job_id="task-unknown",
        static_job_id="missing-static-job",
    )
    with pytest.raises(RuntimeJobScanMapValidationError):
        await repo.upsert_job_scan_map(invalid, created_by="tester")

    await repo.upsert_snapshot(RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="f" * 64,
        source_sequence=1,
        platform="talend",
        jobs=[{"static_job_id": "real-static-job", "name": "Real"}],
    ))
    with pytest.raises(RuntimeJobScanMapValidationError):
        await repo.upsert_job_scan_map(invalid, created_by="tester")

    mapped = await repo.upsert_job_scan_map(
        invalid.model_copy(update={"static_job_id": "real-static-job"}),
        created_by="tester",
    )
    assert mapped.static_job_id == "real-static-job"


async def test_missing_field_lineage_is_partial_not_fabricated(db_session):
    await _seed_correlatable_failure(db_session)
    await _publish_snapshot_and_map(db_session)
    snapshot = RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="c" * 64,
        source_sequence=11,
        platform="talend",
        jobs=[{
            "static_job_id": "talend-transform", "name": "Transform",
            "components": [{"unique_name": "tMap_1"}],
        }],
    )
    await RuntimeTopologyRepository(db_session).upsert_snapshot(snapshot)
    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)
    assert result.status == "partial"
    assert result.component_match == "exact_normalized"
    assert result.lineage_evidence == []


async def test_pinned_mapping_uses_its_selected_snapshot_after_a_newer_scan(db_session):
    await _seed_correlatable_failure(db_session)
    await _publish_snapshot_and_map(db_session)
    repo = RuntimeTopologyRepository(db_session)
    await repo.upsert_job_scan_map(
        RuntimeJobScanMapCreate(
            project_id=_PROJECT,
            source_key="tmc",
            runtime_job_id="tmc-task-42",
            static_job_id="talend-transform",
            scan_revision="a" * 64,
        ),
        created_by="tester",
    )
    await repo.upsert_snapshot(RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="g" * 64,
        source_sequence=11,
        platform="talend",
        jobs=[{"static_job_id": "replacement", "name": "Replacement"}],
    ), received_at=_NOW + datetime.timedelta(minutes=1))

    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)
    assert result.status == "matched"
    assert result.scan_revision == "a" * 64
    assert result.static_job_id == "talend-transform"


async def test_unpinned_mapping_uses_latest_snapshot_and_surfaces_removed_static_job(db_session):
    await _seed_correlatable_failure(db_session)
    await _publish_snapshot_and_map(db_session)
    await RuntimeTopologyRepository(db_session).upsert_snapshot(RuntimeStaticTopologySnapshotIn(
        project_id=_PROJECT,
        scan_revision="h" * 64,
        source_sequence=11,
        platform="talend",
        jobs=[{"static_job_id": "replacement", "name": "Replacement"}],
    ), received_at=_NOW + datetime.timedelta(minutes=1))

    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)
    assert result.status == "unmatched"
    assert result.scan_revision == "h" * 64
    assert result.static_job_id is None


async def test_correlation_scope_hides_cross_project_execution(db_session):
    await _seed_correlatable_failure(db_session)
    from src.runtime.store import RuntimeRecordNotFoundError

    with pytest.raises(RuntimeRecordNotFoundError):
        await RuntimeCorrelationService(db_session).correlate_execution([_OTHER], _EXECUTION)


async def test_malformed_legacy_evidence_refs_are_filtered_before_correlation(db_session):
    await _seed_correlatable_failure(db_session)
    await _publish_snapshot_and_map(db_session)
    summary = (await db_session.execute(
        select(RuntimeExecutionSummaryRow).where(
            RuntimeExecutionSummaryRow.project_id == _PROJECT,
            RuntimeExecutionSummaryRow.execution_id == _EXECUTION,
        )
    )).scalars().one()
    summary.evidence_refs = [1, "2", None, {}, True, 1.5, "not-an-id"]
    await db_session.flush()

    result = await RuntimeCorrelationService(db_session).correlate_execution([_PROJECT], _EXECUTION)
    assert result.status == "matched"
    assert all(isinstance(value, int) and not isinstance(value, bool) for value in result.runtime_evidence_log_event_ids)


def test_field_violation_parser_keeps_only_explicit_structural_field_fact():
    parsed = parse_runtime_log_message("[tMap_1] Cannot insert NULL into column 'requiredField'")
    assert parsed.field_name == "requiredField"
    assert parsed.field_violation_kind == "null_rejected"
    assert parse_runtime_log_message("payload has null somewhere").field_name is None


def test_snapshot_rejects_unbounded_mapping_expression():
    with pytest.raises(ValidationError, match="at most 1000"):
        RuntimeStaticTopologySnapshotIn(
            project_id=_PROJECT,
            scan_revision="e" * 64,
            source_sequence=1,
            platform="talend",
            jobs=[{"static_job_id": "talend-transform", "name": "Transform"}],
            lineage=[{
                "job_id": "talend-transform",
                "expressions": ["x" * 1_001],
            }],
        )


def _mock_user() -> User:
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = 1
    user.roles = [role]
    return user


async def test_correlation_endpoint_returns_404_for_out_of_scope_execution(db_session):
    from src.api.dependencies import get_current_user, get_db

    await _seed_correlatable_failure(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        with patch("src.api.routes.runtime.get_user_project_ids", AsyncMock(return_value=[_OTHER])):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/api/v1/runtime/executions/{_EXECUTION}/correlation")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_internal_snapshot_ingest_requires_existing_mcp_api_key_pattern(db_session, monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "snapshot-test-key")
    app.dependency_overrides[get_db] = lambda: db_session
    payload = {
        "project_id": _PROJECT,
        "scan_revision": "d" * 64,
        "source_sequence": 1,
        "platform": "talend",
        "jobs": [{"static_job_id": "talend-transform", "name": "Transform"}],
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/api/v1/ingest/runtime-static-topology", json=payload)
            accepted = await client.post(
                "/api/v1/ingest/runtime-static-topology",
                json=payload,
                headers={"Authorization": "Bearer snapshot-test-key"},
            )
            replay = await client.post(
                "/api/v1/ingest/runtime-static-topology",
                json=payload,
                headers={"Authorization": "Bearer snapshot-test-key"},
            )
            conflicting_payload = {**payload, "jobs": [{"static_job_id": "other", "name": "Changed"}]}
            conflict = await client.post(
                "/api/v1/ingest/runtime-static-topology",
                json=conflicting_payload,
                headers={"Authorization": "Bearer snapshot-test-key"},
            )
        assert denied.status_code == 401
        assert accepted.status_code == 200
        assert replay.status_code == 200
        assert replay.json()["received_at"] == accepted.json()["received_at"]
        assert conflict.status_code == 409
        assert accepted.json()["scan_revision"] == "d" * 64
    finally:
        app.dependency_overrides.clear()


async def test_machine_topology_ingest_is_disabled_without_key_in_production(db_session, monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    # Settings validates this at process startup too.  Patching the singleton
    # lets this request-time gate be exercised without re-importing the app.
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "")
    app.dependency_overrides[get_db] = lambda: db_session
    payload = {
        "project_id": _PROJECT,
        "scan_revision": "i" * 64,
        "source_sequence": 1,
        "platform": "talend",
        "jobs": [{"static_job_id": "talend-transform", "name": "Transform"}],
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/ingest/runtime-static-topology", json=payload)
        assert response.status_code == 503
        assert "MCP_INGEST_API_KEY" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_production_settings_require_mcp_ingest_key_at_startup():
    from src.config import Settings

    production = Settings(
        app_env="production",
        jwt_secret_key="not-the-default-secret",
        admin_default_password="not-admin",
        source_credentials_encryption_key="configured-for-test",
        public_app_url="https://kb.example.test",
        mcp_ingest_api_key="",
    )
    with pytest.raises(ValueError, match="MCP_INGEST_API_KEY"):
        production.validate_production_secrets()


async def test_machine_topology_ingest_allows_explicit_development_mode_without_key(db_session, monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "")
    app.dependency_overrides[get_db] = lambda: db_session
    payload = {
        "project_id": _PROJECT,
        "scan_revision": "j" * 64,
        "source_sequence": 1,
        "platform": "talend",
        "jobs": [{"static_job_id": "talend-transform", "name": "Transform"}],
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/ingest/runtime-static-topology", json=payload)
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
