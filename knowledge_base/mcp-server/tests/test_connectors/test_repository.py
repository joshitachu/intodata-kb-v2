import datetime as dt

import pytest

from mcp_server.connectors.repository import ConnectorRepository
from mcp_server.db.models import GenerateRun, Project


class FakeSecrets:
    def encrypt(self, value):
        return f"enc:{value}"

    def decrypt_for_provider(self, value):
        assert value.startswith("enc:")
        return value[4:]


@pytest.mark.asyncio
async def test_connector_credentials_and_locators_are_not_stored_in_plaintext(db_session):
    repository = ConnectorRepository(db_session, FakeSecrets())
    instance = await repository.create_instance(
        provider_id="elastic_stack", display_name="Elastic", safe_config={"endpoint": "https://elastic.example"},
        credential_type="api_key", credential="super-secret", kibana_credential=None, user_id=1,
    )
    assert instance.credential_ciphertext == "enc:super-secret"
    assert "super-secret" not in str(instance.safe_config)


@pytest.mark.asyncio
async def test_binding_version_increments_and_snapshot_is_frozen(db_session):
    secrets = FakeSecrets()
    repository = ConnectorRepository(db_session, secrets)
    db_session.add(Project(id="p", display_name="Project", local_path="/tmp/project"))
    db_session.add(GenerateRun(id=1, project_id="p", triggered_by="api"))
    await db_session.flush()
    instance = await repository.create_instance(
        provider_id="elastic_stack", display_name="Elastic", safe_config={"endpoint": "https://elastic.example"},
        credential_type="api_key", credential="secret", kibana_credential=None, user_id=1,
    )
    from mcp_server.connectors.domain import ConnectorSourceProfile
    rows = await repository.upsert_sources([ConnectorSourceProfile(
        "source", instance.id, "index", "Logs", "logs-*", {"timestamp_field": "@timestamp"}
    )])
    binding = await repository.upsert_binding(
        project_id="p", connector_instance_id=instance.id, source_ids=[rows[0].id],
        capability_ids=["runtime.events.search"], budgets={"max_calls": 2}, max_time_window_days=7,
        enabled=True, user_id=1,
    )
    initial_version = binding.version
    updated = await repository.upsert_binding(
        project_id="p", connector_instance_id=instance.id, source_ids=[rows[0].id],
        capability_ids=["runtime.events.search"], budgets={"max_calls": 1}, max_time_window_days=7,
        enabled=True, user_id=1,
    )
    assert updated.version == initial_version + 1
    now = dt.datetime.now(dt.timezone.utc)
    snapshot = await repository.create_snapshot(
        generate_run_id=1, project_id="p", user_id=1, binding=updated, instance=instance, sources=rows,
        issued_at=now, expires_at=now + dt.timedelta(minutes=15),
    )
    assert "locator_ciphertext" in str(snapshot.snapshot)
    assert snapshot.snapshot["instance_safe_config"] == {"endpoint": "https://elastic.example"}
    assert snapshot.snapshot["credential_ciphertext"] == "enc:secret"
    assert snapshot.snapshot["source_profiles"][0]["locator_ciphertext"] == "enc:logs-*"


@pytest.mark.asyncio
async def test_remote_mcp_tool_drift_revokes_previous_review(db_session):
    from mcp_server.connectors.domain import ConnectorSourceProfile

    repository = ConnectorRepository(db_session, FakeSecrets())
    instance = await repository.create_instance(
        provider_id="remote_mcp",
        display_name="Support MCP",
        safe_config={"endpoint": "https://mcp.example.test/mcp"},
        credential_type="none",
        credential=None,
        kibana_credential=None,
        user_id=1,
    )

    def profile(digest: str) -> ConnectorSourceProfile:
        return ConnectorSourceProfile(
            id="remote-tool",
            connector_instance_id=instance.id,
            source_type="mcp_tool",
            display_label="Search tickets",
            locator="search_tickets",
            fields={
                "input_schema": {"type": "object", "properties": {}},
                "schema_digest": digest,
                "review_status": "pending",
            },
            enabled=False,
            discovery_digest=digest,
        )

    row = (await repository.upsert_sources([profile("sha256:one")]))[0]
    await repository.review_remote_tool(
        row,
        enabled=True,
        approved_purpose="Search the approved ticket workspace",
        user_id=1,
    )
    unchanged = (await repository.upsert_sources([profile("sha256:one")]))[0]
    assert unchanged.enabled is True
    assert unchanged.field_profile["review_status"] == "approved"

    changed = (await repository.upsert_sources([profile("sha256:two")]))[0]
    assert changed.enabled is False
    assert changed.field_profile["review_status"] == "changed"
    assert "approved_purpose" not in changed.field_profile
