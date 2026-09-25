"""Contract tests for provider-declared static-reference binding.

The bug these guard against is not "no mapping was created"; it is "the WRONG
mapping was created". A wrong `runtime_job_scan_maps` row attaches one design's
runtime history to a different design and does it invisibly, so every refusal
below is pinned with the literal names that produced it on a real estate.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

import src.runtime.topology.binding as binding_module
from src.config import settings
from src.db.models.runtime_data import RuntimeJobRow
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime import source_registry
from src.runtime.models import RuntimeJob, RuntimeWorkspace
from src.runtime.providers.base import (
    RuntimeSourceProvider,
    SingleTenantIdentityMixin,
    StaticRefStrategy,
)
from src.runtime.providers.mock import provider as mock_provider
from src.runtime.providers.stored import provider as stored_provider
from src.runtime.providers.talend_tmc import provider as tmc_provider
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSyncBatch, run_runtime_source_sync_cycle
from src.runtime.topology.binding import (
    REASON_NO_SNAPSHOT,
    REASON_NO_STATIC_REF,
    resolve_static_refs_for_source,
)
from src.runtime.topology.models import (
    RuntimeJobScanMapCreate,
    RuntimeStaticTopologySnapshotIn,
)
from src.runtime.topology.repository import RuntimeTopologyRepository

_PROJECT = "pluripharm"
_SOURCE_KEY = "tmc"
_NOW = datetime.datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


class _DeclaringProvider:
    """Minimal provider declaring `artifact_name`, like TMC does."""

    def static_ref_strategy(self) -> StaticRefStrategy:
        return StaticRefStrategy(source_field="artifact_name")


class _SilentProvider:
    """Minimal provider declaring nothing, like Elasticsearch does."""

    def static_ref_strategy(self) -> None:
        return None


async def _publish_snapshot(session, jobs: list[dict], *, sequence: int = 1) -> str:
    revision = f"{sequence:064d}"
    await RuntimeTopologyRepository(session).upsert_snapshot(
        RuntimeStaticTopologySnapshotIn(
            project_id=_PROJECT,
            scan_revision=revision,
            source_sequence=sequence,
            platform="talend",
            jobs=jobs,
        ),
        received_at=_NOW,
    )
    await session.flush()
    return revision


def _add_runtime_job(session, *, job_id: str, artifact_name: str | None) -> None:
    session.add(
        RuntimeJobRow(
            project_id=_PROJECT,
            source_key=_SOURCE_KEY,
            job_id=job_id,
            name=f"TMC_TaskDuplicator > {job_id}",
            display_name=job_id,
            job_type="task",
            executable_kind="job",
            artifact_name=artifact_name,
            ingested_at=_NOW,
        )
    )


async def _maps(session) -> list[RuntimeJobScanMapRow]:
    rows = (
        await session.execute(
            select(RuntimeJobScanMapRow).order_by(RuntimeJobScanMapRow.runtime_job_id)
        )
    ).scalars().all()
    return list(rows)


async def _resolve(session, provider):
    return await resolve_static_refs_for_source(
        session,
        provider=provider,
        project_id=_PROJECT,
        source_key=_SOURCE_KEY,
        now=_NOW,
    )


# --- the two "nothing to attempt" outcomes, each with its reason -------------


@pytest.mark.asyncio
async def test_a_provider_declaring_no_static_ref_writes_nothing_and_says_why(db_session):
    await _publish_snapshot(db_session, [{"static_job_id": "s1", "name": "DWH_Facts_001"}])
    _add_runtime_job(db_session, job_id="task-1", artifact_name="DWH_Facts_001")
    await db_session.flush()

    result = await _resolve(db_session, _SilentProvider())

    assert result.attempted is False
    assert result.reason == REASON_NO_STATIC_REF
    assert (result.bound, result.written) == (0, 0)
    assert await _maps(db_session) == []


@pytest.mark.asyncio
async def test_a_project_with_no_snapshot_writes_nothing_and_says_why(db_session):
    _add_runtime_job(db_session, job_id="task-1", artifact_name="DWH_Facts_001")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert result.attempted is False
    assert result.reason == REASON_NO_SNAPSHOT
    # The strategy is still reported: the provider CAN bind, the project cannot
    # yet -- two different states that must not look the same to an operator.
    assert result.source_field == "artifact_name"
    assert await _maps(db_session) == []


# --- the verified production defect: a near miss must not bind --------------


@pytest.mark.asyncio
async def test_exact_names_bind_and_a_near_miss_does_not(db_session):
    """`DWH_Facts_001` must never bind to `DWH_Facts_Master`.

    Both are real, separately deployed jobs on the estate this was measured on,
    and `DWH_Facts_Master` is currently failing. A prefix tier produced 25
    bindings of exactly this shape; every one of them would have blamed a
    healthy design for another design's failures.
    """
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "static-master", "name": "DWH_Facts_Master"},
            {"static_job_id": "static-live", "name": "Daily_Queries_6_uur"},
            {"static_job_id": "static-mirror", "name": "WMS_IE_stock_mirror"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-near-1", artifact_name="DWH_Facts_001")
    _add_runtime_job(db_session, job_id="task-near-2", artifact_name="Daily_Queries_6_uur_old")
    _add_runtime_job(db_session, job_id="task-near-3", artifact_name="WMS_IE_stock_mirror_copy")
    _add_runtime_job(db_session, job_id="task-exact", artifact_name="DWH_Facts_Master")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.bound, result.written, result.unmatched) == (1, 1, 3)
    assert [(row.runtime_job_id, row.static_job_id) for row in await _maps(db_session)] == [
        ("task-exact", "static-master")
    ]


@pytest.mark.asyncio
async def test_a_runtime_job_with_no_static_reference_value_is_unmatched(db_session):
    await _publish_snapshot(db_session, [{"static_job_id": "s1", "name": "DWH_Facts_Master"}])
    _add_runtime_job(db_session, job_id="task-blank", artifact_name=None)
    _add_runtime_job(db_session, job_id="task-empty", artifact_name="")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.bound, result.unmatched) == (0, 2)
    assert await _maps(db_session) == []


@pytest.mark.asyncio
async def test_whitespace_is_not_normalised_away(db_session):
    """A value differing by a space is a different string, not a loose spelling.

    Stripping here would be the same class of mistake as a prefix match: a
    silent normalisation nobody downstream can see or audit.
    """
    await _publish_snapshot(db_session, [{"static_job_id": "s1", "name": "DWH_Facts_Master"}])
    _add_runtime_job(db_session, job_id="task-padded", artifact_name=" DWH_Facts_Master")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.bound, result.unmatched) == (0, 1)


# --- ambiguity is refused, never guessed ------------------------------------


@pytest.mark.asyncio
async def test_one_runtime_value_matching_two_static_jobs_is_refused(db_session):
    """Two designs, one name. There is no evidence saying which one ran."""
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "static-a", "name": "DWH_Facts_Master", "source_root": "zip_a"},
            {"static_job_id": "static-b", "name": "DWH_Facts_Master", "source_root": "zip_b"},
            {"static_job_id": "static-c", "name": "Unique_Job"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-ambiguous", artifact_name="DWH_Facts_Master")
    _add_runtime_job(db_session, job_id="task-unique", artifact_name="Unique_Job")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.ambiguous, result.bound) == (1, 1)
    assert result.ambiguous_values == ("DWH_Facts_Master",)
    assert [(row.runtime_job_id, row.static_job_id) for row in await _maps(db_session)] == [
        ("task-unique", "static-c")
    ]


@pytest.mark.asyncio
async def test_a_name_colliding_with_another_designs_id_is_also_ambiguous(db_session):
    """Both keys are indexed, so a cross-key collision is a real ambiguity."""
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "Shared_Token", "name": "Some_Design"},
            {"static_job_id": "static-other", "name": "Shared_Token"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-collide", artifact_name="Shared_Token")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.ambiguous, result.bound) == (1, 0)
    assert await _maps(db_session) == []


@pytest.mark.asyncio
async def test_many_runtime_tasks_may_share_one_static_design(db_session):
    """Deploying one design as several tasks is correct, not a conflict."""
    await _publish_snapshot(db_session, [{"static_job_id": "static-a", "name": "Shared_Design"}])
    _add_runtime_job(db_session, job_id="task-prod", artifact_name="Shared_Design")
    _add_runtime_job(db_session, job_id="task-acc", artifact_name="Shared_Design")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.bound, result.ambiguous) == (2, 0)
    assert {row.static_job_id for row in await _maps(db_session)} == {"static-a"}


# --- provenance: manual wins, and re-running changes nothing ----------------


@pytest.mark.asyncio
async def test_a_manual_mapping_survives_a_re_run_unchanged(db_session):
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "static-operator-chose", "name": "Renamed_In_Studio"},
            {"static_job_id": "static-name-match", "name": "DWH_Facts_Master"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-1", artifact_name="DWH_Facts_Master")
    await db_session.flush()
    manual = await RuntimeTopologyRepository(db_session).upsert_job_scan_map(
        RuntimeJobScanMapCreate(
            project_id=_PROJECT,
            source_key=_SOURCE_KEY,
            runtime_job_id="task-1",
            static_job_id="static-operator-chose",
        ),
        created_by="operator-7",
        created_at=_NOW,
    )
    assert manual.resolved_by == "manual"

    result = await _resolve(db_session, _DeclaringProvider())

    assert (result.already_manual, result.bound, result.written) == (1, 0, 0)
    rows = await _maps(db_session)
    assert len(rows) == 1
    assert rows[0].static_job_id == "static-operator-chose"
    assert rows[0].resolved_by == "manual"
    assert rows[0].created_by == "operator-7"


@pytest.mark.asyncio
async def test_a_human_edit_promotes_a_resolved_row_to_manual(db_session):
    """Otherwise the next sync cycle would revert the correction."""
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "static-name-match", "name": "DWH_Facts_Master"},
            {"static_job_id": "static-correction", "name": "The_Real_Design"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-1", artifact_name="DWH_Facts_Master")
    await db_session.flush()
    await _resolve(db_session, _DeclaringProvider())

    repository = RuntimeTopologyRepository(db_session)
    await repository.upsert_job_scan_map(
        RuntimeJobScanMapCreate(
            project_id=_PROJECT,
            source_key=_SOURCE_KEY,
            runtime_job_id="task-1",
            static_job_id="static-correction",
        ),
        created_by="operator-7",
        created_at=_NOW,
    )

    result = await _resolve(db_session, _DeclaringProvider())

    assert result.already_manual == 1
    rows = await _maps(db_session)
    assert (rows[0].static_job_id, rows[0].resolved_by) == ("static-correction", "manual")


@pytest.mark.asyncio
async def test_resolving_is_idempotent(db_session):
    await _publish_snapshot(
        db_session,
        [
            {"static_job_id": "static-a", "name": "DWH_Facts_Master"},
            {"static_job_id": "static-b", "name": "Daily_Queries_6_uur"},
        ],
    )
    _add_runtime_job(db_session, job_id="task-a", artifact_name="DWH_Facts_Master")
    _add_runtime_job(db_session, job_id="task-b", artifact_name="Daily_Queries_6_uur")
    _add_runtime_job(db_session, job_id="task-c", artifact_name="DWH_Facts_001")
    await db_session.flush()

    first = await _resolve(db_session, _DeclaringProvider())
    before = [
        (row.runtime_job_id, row.static_job_id, row.resolved_by, row.created_at)
        for row in await _maps(db_session)
    ]
    second = await _resolve(db_session, _DeclaringProvider())
    after = [
        (row.runtime_job_id, row.static_job_id, row.resolved_by, row.created_at)
        for row in await _maps(db_session)
    ]

    assert (first.bound, first.written, first.unmatched) == (2, 2, 1)
    assert (second.bound, second.written, second.unmatched) == (2, 0, 1)
    assert before == after


@pytest.mark.asyncio
async def test_a_resolved_row_records_the_field_that_resolved_it(db_session):
    await _publish_snapshot(db_session, [{"static_job_id": "static-a", "name": "DWH_Facts_Master"}])
    _add_runtime_job(db_session, job_id="task-a", artifact_name="DWH_Facts_Master")
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    rows = await _maps(db_session)
    assert rows[0].resolved_by == "static_ref:artifact_name"
    # Never pinned: a resolved mapping is derived from a name that outlives one
    # scan, so pinning it would expire a correct mapping at the next scan.
    assert rows[0].scan_revision is None
    assert rows[0].created_by is None
    assert result.scan_revision == f"{1:064d}"


@pytest.mark.asyncio
async def test_binding_is_scoped_to_its_own_project_and_source(db_session):
    await _publish_snapshot(db_session, [{"static_job_id": "static-a", "name": "DWH_Facts_Master"}])
    _add_runtime_job(db_session, job_id="task-a", artifact_name="DWH_Facts_Master")
    session_add_other = RuntimeJobRow(
        project_id="another-project",
        source_key=_SOURCE_KEY,
        job_id="task-elsewhere",
        name="task-elsewhere",
        executable_kind="job",
        artifact_name="DWH_Facts_Master",
        ingested_at=_NOW,
    )
    db_session.add(session_add_other)
    db_session.add(
        RuntimeJobRow(
            project_id=_PROJECT,
            source_key="elastic",
            job_id="task-other-source",
            name="task-other-source",
            executable_kind="job",
            artifact_name="DWH_Facts_Master",
            ingested_at=_NOW,
        )
    )
    await db_session.flush()

    result = await _resolve(db_session, _DeclaringProvider())

    assert result.bound == 1
    assert [row.runtime_job_id for row in await _maps(db_session)] == ["task-a"]


# --- the shipped providers declare what this suite assumes ------------------


def test_the_shipped_providers_declare_their_own_static_reference():
    assert tmc_provider.static_ref_strategy() == StaticRefStrategy(source_field="artifact_name")
    # Declared for the non-TMC exercise of the path; the fixture really does set
    # `artifact_name`, so this is not a stub.
    assert mock_provider.static_ref_strategy() == StaticRefStrategy(source_field="artifact_name")
    # Reads the mirror, does not fill it: the ingesting provider's strategy owns
    # those rows.
    assert stored_provider.static_ref_strategy() is None


def test_the_match_mode_is_exact_and_nothing_else():
    """Pinned: a second `match` member is the change this design refuses."""
    assert StaticRefStrategy(source_field="artifact_name").match == "exact"
    assert StaticRefStrategy(source_field="x").resolved_by() == "static_ref:x"


def test_elastic_declares_no_static_reference():
    """Verified against the module: it never sets `artifact_name` on a job."""
    from src.runtime.providers.elastic import provider as elastic_provider

    assert elastic_provider.static_ref_strategy() is None
    assert isinstance(elastic_provider, RuntimeSourceProvider)


def test_executable_semantics_separate_a_run_from_a_deployment():
    assert tmc_provider.executable_semantics() == {
        "job": "execution",
        "route": "lifecycle",
        "data_service": "lifecycle",
    }
    from src.runtime.providers.elastic import provider as elastic_provider

    assert elastic_provider.executable_semantics() == {"job": "execution"}
    assert stored_provider.executable_semantics() == {}


# --- the sync cycle wiring --------------------------------------------------


class _SyncingProvider(SingleTenantIdentityMixin):
    """A whole provider, so the binding is exercised through the real cycle."""

    def physical_source_identity(self, credentials) -> str:
        return "syncing:local"

    def provider_key(self) -> str:
        return "syncing"

    def provider_label(self) -> str:
        return "Syncing"

    def credential_schema(self):
        return []

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> StaticRefStrategy:
        return StaticRefStrategy(source_field="artifact_name")

    def executable_semantics(self):
        return {"job": "execution"}

    async def validate_credentials(self, credentials):
        return True, None

    async def sync(self, *, credentials, project_id: str) -> RuntimeSyncBatch:
        return RuntimeSyncBatch(
            workspaces=[
                RuntimeWorkspace(
                    source_key="syncing",
                    workspace_id="workspace-a",
                    name="Workspace A",
                    last_seen_at=_NOW,
                )
            ],
            jobs=[
                RuntimeJob(
                    source_key="syncing",
                    job_id="task-a",
                    name="TMC_TaskDuplicator > task-a",
                    workspace_id="workspace-a",
                    workspace_name="Workspace A",
                    artifact_name="DWH_Facts_Master",
                    last_seen_at=_NOW,
                )
            ],
            synced_at=_NOW,
        )


@pytest.fixture
def syncing_source(monkeypatch):
    monkeypatch.setattr(
        settings, "source_credentials_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")
    monkeypatch.setitem(source_registry._PROVIDERS, "syncing", _SyncingProvider())


async def _register_syncing_source(session):
    repo = RuntimeStoreRepository(session)
    source = await repo.create_source_config(
        project_id=_PROJECT, source_type="syncing", label="Syncing"
    )
    await repo.write_credentials(
        source_id=source.id,
        plaintext_payload={},
        credential_type="syncing",
        rotated_by="1",
    )
    return source


@pytest.mark.asyncio
async def test_a_successful_sync_cycle_binds_its_jobs(db_session, syncing_source):
    await _publish_snapshot(
        db_session, [{"static_job_id": "static-master", "name": "DWH_Facts_Master"}]
    )
    source = await _register_syncing_source(db_session)

    result = await run_runtime_source_sync_cycle(db_session, source_id=source.id, now=_NOW)

    assert result.status.value == "success"
    assert result.job_project_ids == (_PROJECT,)
    rows = await _maps(db_session)
    assert [(row.source_key, row.runtime_job_id, row.static_job_id) for row in rows] == [
        ("syncing", "task-a", "static-master")
    ]
    assert rows[0].resolved_by == "static_ref:artifact_name"


@pytest.mark.asyncio
async def test_a_failing_resolver_never_fails_the_sync(db_session, syncing_source, monkeypatch):
    """The mirror is the valuable thing; binding is derived convenience.

    The SAVEPOINT matters as much as the `except`: without it the poisoned
    transaction would take the caller's commit -- and the whole batch -- down
    with it, which is the failure this test would otherwise not see.
    """
    await _publish_snapshot(
        db_session, [{"static_job_id": "static-master", "name": "DWH_Facts_Master"}]
    )
    source = await _register_syncing_source(db_session)

    async def _explode(*args, **kwargs):
        raise RuntimeError("snapshot payload is malformed")

    monkeypatch.setattr(binding_module, "resolve_static_refs_for_source", _explode)

    result = await run_runtime_source_sync_cycle(db_session, source_id=source.id, now=_NOW)

    assert result.status.value == "success"
    assert result.jobs_written == 1
    assert await _maps(db_session) == []
    # The batch's own writes survived the rolled-back savepoint.
    jobs = (
        await db_session.execute(
            select(RuntimeJobRow).where(RuntimeJobRow.project_id == _PROJECT)
        )
    ).scalars().all()
    assert [job.job_id for job in jobs] == ["task-a"]
