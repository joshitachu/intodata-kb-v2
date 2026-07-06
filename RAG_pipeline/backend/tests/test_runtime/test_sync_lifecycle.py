import datetime

import pytest
from cryptography.fernet import Fernet

from src.config import settings
from src.db.models import runtime_data  # noqa: F401
from src.runtime import source_registry
from src.runtime.models import ListRuntimeJobsInput, RuntimeJob, RuntimeWorkspace
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSyncBatch, run_runtime_source_sync_cycle


class _LifecycleProvider:
    def __init__(self) -> None:
        self.fail = False

    def provider_key(self) -> str:
        return "lifecycle"

    def provider_label(self) -> str:
        return "Lifecycle"

    def credential_schema(self):
        return []

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        return None

    async def validate_credentials(self, credentials):
        return True, None

    async def sync(self, *, credentials, project_id: str) -> RuntimeSyncBatch:
        if self.fail:
            raise RuntimeError("api_token=super-secret exploded")
        return RuntimeSyncBatch(synced_at=datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC))


class _MappedProvider(_LifecycleProvider):
    def provider_key(self) -> str:
        return "mapped"

    async def sync(self, *, credentials, project_id: str) -> RuntimeSyncBatch:
        synced_at = datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC)
        return RuntimeSyncBatch(
            workspaces=[
                RuntimeWorkspace(
                    source_key="mapped",
                    workspace_id="workspace-a",
                    name="Workspace A",
                    last_seen_at=synced_at,
                )
            ],
            jobs=[
                RuntimeJob(
                    source_key="mapped",
                    job_id="job-a",
                    name="Job A",
                    workspace_id="workspace-a",
                    workspace_name="Workspace A",
                    last_seen_at=synced_at,
                )
            ],
            synced_at=synced_at,
        )


@pytest.fixture(autouse=True)
def source_sync_setup(monkeypatch):
    provider = _LifecycleProvider()
    monkeypatch.setattr(settings, "source_credentials_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")
    monkeypatch.setitem(source_registry._PROVIDERS, "lifecycle", provider)
    monkeypatch.setitem(source_registry._PROVIDERS, "mapped", _MappedProvider())
    return provider


async def test_source_sync_success_updates_health(db_session):
    repo = RuntimeStoreRepository(db_session)
    source = await repo.create_source_config(project_id="project-a", source_type="lifecycle", label="Lifecycle")
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={},
        credential_type="lifecycle",
        rotated_by="1",
    )

    result = await run_runtime_source_sync_cycle(db_session, source_id=source.id)
    updated = await repo.get_source_config(source.id, None)

    assert result.status.value == "success"
    assert updated.health is not None
    assert updated.health.consecutive_failures == 0
    assert updated.health.last_success_at is not None
    assert updated.enabled is True


async def test_source_sync_failures_auto_disable(db_session, source_sync_setup):
    source_sync_setup.fail = True
    repo = RuntimeStoreRepository(db_session)
    source = await repo.create_source_config(project_id="project-a", source_type="lifecycle", label="Lifecycle")
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={},
        credential_type="lifecycle",
        rotated_by="1",
    )

    for _ in range(3):
        result = await run_runtime_source_sync_cycle(db_session, source_id=source.id)
        assert result.status.value == "failed"

    updated = await repo.get_source_config(source.id, None)
    assert updated.enabled is False
    assert updated.disabled_reason == "auto-disabled"
    assert updated.health is not None
    assert updated.health.consecutive_failures == 3
    assert "super-secret" not in (updated.health.last_failure_sanitized or "")


async def test_source_sync_applies_workspace_project_map(db_session):
    repo = RuntimeStoreRepository(db_session)
    source = await repo.create_source_config(project_id="default-project", source_type="mapped", label="Mapped")
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={},
        credential_type="mapped",
        rotated_by="1",
    )
    await repo.add_container_mapping(
        source_id=source.id,
        container_key="workspace-a",
        container_name="Workspace A",
        project_id="mapped-project",
        created_by="1",
    )

    await run_runtime_source_sync_cycle(db_session, source_id=source.id)
    jobs = await repo.list_jobs(None, ListRuntimeJobsInput(project_id="mapped-project"))
    default_jobs = await repo.list_jobs(None, ListRuntimeJobsInput(project_id="default-project"))

    assert [job.job_id for job in jobs] == ["job-a"]
    assert default_jobs == []
