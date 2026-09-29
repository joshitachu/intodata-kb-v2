import datetime

import pytest

from mcp_server.db.models import GenerateRun, Project, ScanRun
from mcp_server.services.ops_telemetry import build_ops_snapshot, telemetry_enabled


@pytest.mark.asyncio
async def test_ops_snapshot_contains_metadata_without_customer_content(db_session, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "ops_client_slug", "acme")
    monkeypatch.setattr(config, "ops_environment", "production")
    monkeypatch.setattr(config, "ops_release_version", "v1.2.3")

    now = datetime.datetime.now(datetime.timezone.utc)
    db_session.add(
        Project(
            id="acme-main",
            display_name="Acme Main",
            repo_url="https://github.com/acme/private-repo",
            local_path="/opt/intodata-kb/acme/knowledge_base/data/repos/acme-main",
            platform="talend",
            source_type="zip",
            status="ready",
            last_scan_at=now,
            last_generate_at=now,
            last_rag_push_at=now,
            job_count=12,
            doc_count=8,
            error_message="",
            github_token_enc="encrypted-secret-token",
        )
    )
    db_session.add(
        ScanRun(
            project_id="acme-main",
            triggered_by="api",
            status="completed",
            started_at=now,
            completed_at=now,
            job_count=12,
            log_file="acme-main_sensitive.log",
        )
    )
    db_session.add(
        GenerateRun(
            project_id="acme-main",
            triggered_by="api",
            status="completed",
            started_at=now,
            completed_at=now,
            cost_usd=1.25,
            duration_ms=2500,
            input_tokens=1000,
            output_tokens=500,
            doc_count=8,
            model_name="model-a",
            write_model_name="model-b",
            quality_score=4.2,
            error_message="",
            log_file="generation_sensitive.log",
        )
    )
    await db_session.commit()

    snapshot = await build_ops_snapshot(db_session)
    encoded = repr(snapshot)

    assert snapshot["schema_version"] == "ops.telemetry.v1"
    assert snapshot["client_slug"] == "acme"
    assert snapshot["totals"]["project_count"] == 1
    assert snapshot["totals"]["doc_count"] == 8
    assert snapshot["totals"]["cost_usd"] == 1.25
    assert snapshot["totals"]["latest_run_average_quality"] == 4.2
    assert snapshot["projects"][0]["display_name"] == "Acme Main"
    assert snapshot["latest_generate_runs"][0]["quality_score"] == 4.2

    assert "private-repo" not in encoded
    assert "/opt/intodata-kb" not in encoded
    assert "encrypted-secret-token" not in encoded
    assert "sensitive.log" not in encoded


def test_ops_telemetry_requires_url_key_and_client_slug(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "ops_telemetry_url", "https://ops.example.com")
    monkeypatch.setattr(config, "ops_telemetry_api_key", "secret")
    monkeypatch.setattr(config, "ops_client_slug", "")
    assert telemetry_enabled() is False

    monkeypatch.setattr(config, "ops_client_slug", "acme")
    assert telemetry_enabled() is True


@pytest.fixture
def _reset_licence_policy():
    from mcp_server.licensing.runtime import set_policy

    yield
    set_policy(None)


@pytest.mark.asyncio
async def test_ops_snapshot_carries_licence_state_and_usage(
    db_session, session_factory, monkeypatch, _reset_licence_policy
):
    """The Fleet screen reads licence state from the heartbeat (plan Decision 6)."""
    import mcp_server.db.session as session_mod
    from tests.test_licensing.licence_fixtures import install_policy, issue_token

    monkeypatch.setattr(session_mod, "async_session_factory", session_factory)
    token = issue_token(generations=4)
    install_policy(token)
    db_session.add(Project(id="lic-p", display_name="P", local_path="/tmp/lic-p",
                          platform="talend", status="ready"))
    db_session.add(GenerateRun(project_id="lic-p", triggered_by="api", status="completed",
                               started_at=datetime.datetime.now(datetime.timezone.utc)))
    await db_session.commit()

    snapshot = await build_ops_snapshot(db_session)

    licence = snapshot["licence"]
    assert licence["state"] == "valid"
    assert licence["plan"] == "basic"
    assert licence["enforcement"] == "enforce"
    assert licence["usage"] == {"generations_used": 1, "generations_limit": 4}
    assert token not in repr(snapshot)


@pytest.mark.asyncio
async def test_ops_snapshot_survives_a_licence_failure(db_session, monkeypatch):
    """A broken licence read must not cost the heartbeat."""
    import mcp_server.services.ops_telemetry as telemetry_mod

    def _boom():
        raise RuntimeError("licence store exploded")

    monkeypatch.setattr(telemetry_mod, "get_policy", _boom)

    snapshot = await build_ops_snapshot(db_session)

    assert snapshot["licence"] == {"state": "unknown"}
    assert snapshot["schema_version"] == "ops.telemetry.v1"
