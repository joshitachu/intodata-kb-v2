import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.db.base import Base
from src.db.models import runtime_data  # noqa: F401
from src.runtime.models import RuntimeSourceUpdate
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store import RuntimeDuplicateSourceError
from src.runtime.secrets import PREVIOUS_KEY_ID


@pytest.fixture()
async def source_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    monkeypatch.setattr(settings, "source_credentials_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


async def test_source_config_crud_and_credentials(source_session):
    repo = RuntimeStoreRepository(source_session)
    source = await repo.create_source_config(
        project_id="project-a",
        source_type="mock",
        label="Mock Source",
        sync_interval_seconds=60,
    )
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={"api_token": "super-secret"},
        credential_type="mock",
        rotated_by="1",
    )

    payload, key_id = await repo.read_credentials(source.id)
    listed = await repo.list_source_configs(["project-a"])
    updated = await repo.update_source_config(
        source.id,
        ["project-a"],
        RuntimeSourceUpdate(label="Renamed", enabled=False, sync_interval_seconds=30),
    )

    assert payload == {"api_token": "super-secret"}
    assert key_id == "primary"
    assert len(listed) == 1
    assert listed[0].model_dump_json().find("super-secret") == -1
    assert updated.label == "Renamed"
    assert updated.enabled is False
    assert updated.disabled_reason == "manual"
    assert updated.sync_interval_seconds == 30


async def test_source_config_rejects_duplicate_physical_identity(source_session):
    repo = RuntimeStoreRepository(source_session)
    await repo.create_source_config(
        project_id="project-a", source_type="provider-a", physical_identity="https://runtime.example", label="Primary"
    )
    with pytest.raises(RuntimeDuplicateSourceError):
        await repo.create_source_config(
            project_id="project-b", source_type="provider-a", physical_identity="https://runtime.example", label="Mirror"
        )


async def test_credentials_lazy_rotation(source_session, monkeypatch):
    previous = Fernet.generate_key().decode()
    current = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "source_credentials_encryption_key", previous)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "v1")
    repo = RuntimeStoreRepository(source_session)
    source = await repo.create_source_config(project_id="project-a", source_type="mock", label="Mock")
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={"value": "old"},
        credential_type="mock",
        rotated_by="1",
    )

    monkeypatch.setattr(settings, "source_credentials_encryption_key", current)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", previous)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "v2")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "v1")
    payload, key_id = await repo.read_credentials(source.id)

    assert payload == {"value": "old"}
    assert key_id != PREVIOUS_KEY_ID
    assert key_id == "v2"
