"""What a `job-health` row IS, and that the surface answers for every kind of row.

Two defects are pinned here.

The first is structural: `_job_run_base` and `_job_label_select` ended with
`params.executable_kind or RuntimeExecutableKind.JOB`, so `GET
/api/v1/runtime/job-health` could only ever return `job`-kind rows, for every
provider, with nothing in the response saying so. On one measured Talend estate
that hides 49 routes and 1 data service out of 145 stream-bound deployed
designs, and leaves 14 of 28 business streams with no row at all.

The second is semantic, and is why simply widening the filter is not enough: a
route row is a DEPLOYMENT, not an execution. A route reported `running` is
deployed and up, not busy, so the `state != success` arithmetic that is correct
for a job counts a healthy live route as a failure. Only the provider that wrote
the row can say which it is, so each row now carries the `liveness` its provider
DECLARES for its kind -- and `unknown` when the provider declares nothing, which
is an answer. Defaulting an undeclared kind to `execution` would reintroduce
exactly the miscount the field exists to prevent.
"""

import contextlib
import datetime
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Role, User
from src.runtime.models import (
    ListRuntimeJobsInput,
    RuntimeExecutableKind,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeSource,
    RuntimeSyncStatus,
)
from src.runtime.store import RuntimeStoreRepository

_P = "proj-liveness"
_TMC = "tmc-abcdef123456"
_MOCK = "mock-abcdef123456"
_ELASTIC = "elastic-abcdef123456"
#: The historical `runtime_sources.source_type` a TMC mirror was written with
#: before self-service source configuration standardised on `tmc`. The provider
#: still claims it in `source_type_aliases()`.
_LEGACY_TMC = "tmc-legacy-key"
_T0 = datetime.datetime(2026, 4, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime.datetime:
    return _T0 + datetime.timedelta(minutes=minutes)


async def _seed_source(repo, source_key: str, source_type: str) -> None:
    await repo.upsert_source(
        _P,
        RuntimeSource(
            estate_key=_P,
            source_key=source_key,
            source_type=source_type,
            label=source_key,
            status=RuntimeSyncStatus.SUCCESS,
            last_synced_at=_T0,
            last_seen_at=_T0,
            ingested_at=_T0,
        ),
        _T0,
    )


async def _seed_job(
    repo,
    *,
    job_id: str,
    name: str,
    kind: RuntimeExecutableKind,
    source_key: str = _TMC,
    artifact_name: str | None = None,
) -> None:
    await repo.upsert_job(
        _P,
        RuntimeJob(
            job_id=job_id,
            source_key=source_key,
            name=name,
            executable_kind=kind,
            artifact_name=artifact_name,
            environment_id="env-prod",
            environment_name="Production",
            last_seen_at=_T0,
        ),
        source_key,
        _T0,
    )


async def _seed_run(
    repo,
    *,
    run_id: str,
    job_id: str,
    job_name: str,
    kind: RuntimeExecutableKind,
    state: RuntimeExecutionState = RuntimeExecutionState.SUCCESS,
    minute: int = 0,
    source_key: str = _TMC,
) -> None:
    await repo.upsert_job_run(
        _P,
        RuntimeJobRun(
            source_key=source_key,
            run_id=run_id,
            job_id=job_id,
            job_name=job_name,
            execution_id=run_id,
            state=state,
            executable_kind=kind,
            environment_id="env-prod",
            environment_name="Production",
            started_at=_at(minute),
            ended_at=_at(minute) + datetime.timedelta(seconds=1),
            duration_ms=1000,
        ),
        source_key,
        _T0,
    )


async def _seed_tmc_estate(repo) -> None:
    """One TMC source holding one executable of each kind it can deploy."""
    await _seed_source(repo, _TMC, "tmc")
    for index, (job_id, name, kind, artifact) in enumerate(
        (
            ("exec-job", "nightly_load", RuntimeExecutableKind.JOB, "NightlyLoad"),
            ("exec-route", "order_intake", RuntimeExecutableKind.ROUTE, "OrderIntake"),
            (
                "exec-ds",
                "price_lookup",
                RuntimeExecutableKind.DATA_SERVICE,
                "PriceLookup",
            ),
        )
    ):
        await _seed_job(
            repo, job_id=job_id, name=name, kind=kind, artifact_name=artifact
        )
        await _seed_run(
            repo, run_id=f"r-{job_id}", job_id=job_id, job_name=name, kind=kind,
            minute=index,
        )


class TestEveryKindIsAnswered:
    async def test_no_executable_kind_returns_every_kind(self, db_session):
        """REGRESSION PIN: this used to silently return only `job`-kind rows."""
        repo = RuntimeStoreRepository(db_session)
        await _seed_tmc_estate(repo)

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert {row.job_id for row in health} == {"exec-job", "exec-route", "exec-ds"}
        assert {row.executable_kind for row in health} == {
            "job",
            "route",
            "data_service",
        }

    @pytest.mark.parametrize(
        "kind,expected_job_id",
        [
            (RuntimeExecutableKind.JOB, "exec-job"),
            (RuntimeExecutableKind.ROUTE, "exec-route"),
            (RuntimeExecutableKind.DATA_SERVICE, "exec-ds"),
        ],
    )
    async def test_an_explicit_kind_still_narrows(self, db_session, kind, expected_job_id):
        repo = RuntimeStoreRepository(db_session)
        await _seed_tmc_estate(repo)

        health = await repo.list_job_health([_P], ListRuntimeJobsInput(executable_kind=kind))

        assert [row.job_id for row in health] == [expected_job_id]

    async def test_model_default_is_unchanged(self, db_session):
        """`ListRuntimeJobsInput()` still means `job`.

        Four other call sites lean on that default -- `/runtime/jobs`,
        `get_estate_overview`, `StoredRuntimeProvider.list_health_summaries` and
        the `list_runtime_jobs` chat tool -- so the widening is expressed at the
        one route that wanted it, not in the shared input model.
        """
        repo = RuntimeStoreRepository(db_session)
        await _seed_tmc_estate(repo)

        health = await repo.list_job_health([_P], ListRuntimeJobsInput())

        assert [row.job_id for row in health] == ["exec-job"]


class TestLiveness:
    """`liveness` is the provider's declaration, never this layer's inference."""

    async def test_execution_and_lifecycle_are_taken_from_the_provider(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_tmc_estate(repo)

        by_id = {
            row.job_id: row
            for row in await repo.list_job_health(
                [_P], ListRuntimeJobsInput(executable_kind=None)
            )
        }

        assert by_id["exec-job"].liveness == "execution"
        assert by_id["exec-route"].liveness == "lifecycle"
        assert by_id["exec-ds"].liveness == "lifecycle"

    async def test_mirrored_rows_stamped_with_the_bare_provider_key_resolve(
        self, db_session
    ):
        """A mirrored row carries the PROVIDER key, not a configured source key.

        Measured on the live mirror: `runtime_jobs.source_key` holds `tmc` for
        1118 rows, while the two configured `runtime_sources` rows are
        `tmc-ddf1ed37af12` and `tmc-a335fb698715`. The two value spaces do not
        intersect, so resolving semantics only through a `runtime_sources` join
        returned nothing for every mirrored row and all 158 Pluripharm rows
        reported `liveness="unknown"` -- restoring exactly the defect this field
        exists to fix, because a consumer then reads a live Route deployment as
        an execution again.

        Deliberately seeds NO source row: the provider key must be enough.
        """
        repo = RuntimeStoreRepository(db_session)
        for job_id, name, kind in (
            ("bare-job", "batch", RuntimeExecutableKind.JOB),
            ("bare-route", "route", RuntimeExecutableKind.ROUTE),
        ):
            await _seed_job(repo, job_id=job_id, name=name, kind=kind, source_key="tmc")
            await _seed_run(
                repo,
                run_id=f"r-{job_id}",
                job_id=job_id,
                job_name=name,
                kind=kind,
                source_key="tmc",
            )

        by_id = {
            row.job_id: row
            for row in await repo.list_job_health(
                [_P], ListRuntimeJobsInput(executable_kind=None)
            )
        }

        assert by_id["bare-job"].liveness == "execution"
        assert by_id["bare-route"].liveness == "lifecycle"

    async def test_undeclared_kind_is_unknown_not_execution(self, db_session):
        """The mock provider declares `job` only; its other kinds stay unknown.

        `unknown` has to survive here. Falling back to `execution` is what makes
        a deployed-and-idle route report as a failed run.
        """
        repo = RuntimeStoreRepository(db_session)
        await _seed_source(repo, _MOCK, "mock")
        await _seed_job(
            repo,
            job_id="mock-route",
            name="mock_route",
            kind=RuntimeExecutableKind.ROUTE,
            source_key=_MOCK,
        )
        await _seed_run(
            repo,
            run_id="r-mock-route",
            job_id="mock-route",
            job_name="mock_route",
            kind=RuntimeExecutableKind.ROUTE,
            source_key=_MOCK,
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert [row.liveness for row in health] == ["unknown"]

    async def test_unregistered_source_type_is_unknown(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_source(repo, "ghost-000000000000", "no-such-provider")
        await _seed_job(
            repo,
            job_id="ghost-job",
            name="ghost",
            kind=RuntimeExecutableKind.JOB,
            source_key="ghost-000000000000",
        )
        await _seed_run(
            repo,
            run_id="r-ghost",
            job_id="ghost-job",
            job_name="ghost",
            kind=RuntimeExecutableKind.JOB,
            source_key="ghost-000000000000",
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert [row.liveness for row in health] == ["unknown"]

    async def test_missing_source_row_is_unknown(self, db_session):
        """No source row means nobody can say what the rows mean."""
        repo = RuntimeStoreRepository(db_session)
        await _seed_run(
            repo,
            run_id="r-orphan",
            job_id="orphan-job",
            job_name="orphan",
            kind=RuntimeExecutableKind.JOB,
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert [row.liveness for row in health] == ["unknown"]

    async def test_historical_source_type_alias_still_resolves(self, db_session):
        """A mirror written as `talend_tmc` is still a TMC mirror.

        `runtime_sources.source_type` holds whatever key was current when the
        row was written. Resolving providers on `provider_key()` alone would
        report `unknown` for every row of a pre-rename TMC mirror -- i.e. lose
        the route/job distinction on exactly the oldest estates.
        """
        repo = RuntimeStoreRepository(db_session)
        await _seed_source(repo, _LEGACY_TMC, "talend_tmc")
        await _seed_job(
            repo,
            job_id="legacy-route",
            name="legacy_route",
            kind=RuntimeExecutableKind.ROUTE,
            source_key=_LEGACY_TMC,
        )
        await _seed_run(
            repo,
            run_id="r-legacy-route",
            job_id="legacy-route",
            job_name="legacy_route",
            kind=RuntimeExecutableKind.ROUTE,
            source_key=_LEGACY_TMC,
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert [row.liveness for row in health] == ["lifecycle"]


class TestArtifactName:
    """The design name, carried so a consumer can join on equality."""

    async def test_carried_when_the_inventory_row_has_one(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_tmc_estate(repo)

        by_id = {
            row.job_id: row
            for row in await repo.list_job_health(
                [_P], ListRuntimeJobsInput(executable_kind=None)
            )
        }

        assert by_id["exec-route"].artifact_name == "OrderIntake"
        # It is NOT the same string as the runtime-side name, which is the whole
        # reason guessing one from the other is not a join.
        assert by_id["exec-route"].job_name == "order_intake"

    async def test_none_when_the_inventory_row_has_no_artifact(self, db_session):
        """The Elastic case: reconstructed interface runs carry no artifact."""
        repo = RuntimeStoreRepository(db_session)
        await _seed_source(repo, _ELASTIC, "elastic")
        await _seed_job(
            repo,
            job_id="iface-1",
            name="ORDER_IMPORT",
            kind=RuntimeExecutableKind.JOB,
            source_key=_ELASTIC,
            artifact_name=None,
        )
        await _seed_run(
            repo,
            run_id="r-iface-1",
            job_id="iface-1",
            job_name="ORDER_IMPORT",
            kind=RuntimeExecutableKind.JOB,
            source_key=_ELASTIC,
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert [row.artifact_name for row in health] == [None]
        assert [row.liveness for row in health] == ["execution"]

    async def test_none_when_there_is_no_inventory_row_at_all(self, db_session):
        """Runs without inventory still report; the kind comes off the run row."""
        repo = RuntimeStoreRepository(db_session)
        await _seed_source(repo, _TMC, "tmc")
        await _seed_run(
            repo,
            run_id="r-unlisted",
            job_id="unlisted-route",
            job_name="unlisted_route",
            kind=RuntimeExecutableKind.ROUTE,
        )

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=None)
        )

        assert len(health) == 1
        assert health[0].artifact_name is None
        assert health[0].executable_kind == "route"
        assert health[0].liveness == "lifecycle"


def _mock_user(id: int = 1) -> User:
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = id
    user.roles = [role]
    return user


@contextlib.contextmanager
def _scope(value):
    with (
        patch("src.api.dependencies.get_user_project_ids", AsyncMock(return_value=value)),
        patch("src.api.routes.runtime.get_user_project_ids", AsyncMock(return_value=value)),
    ):
        yield


@pytest.fixture
async def estate_client(db_session):
    from src.api.dependencies import get_current_user, get_db

    await _seed_tmc_estate(RuntimeStoreRepository(db_session))

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


class TestJobHealthEndpoint:
    async def test_default_returns_every_kind(self, estate_client):
        """REGRESSION PIN at the wire: no `executable_kind` now means ALL kinds."""
        with _scope([_P]):
            resp = await estate_client.get("/api/v1/runtime/job-health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert {row["executable_kind"] for row in body["health"]} == {
            "job",
            "route",
            "data_service",
        }
        assert {row["job_id"]: row["liveness"] for row in body["health"]} == {
            "exec-job": "execution",
            "exec-route": "lifecycle",
            "exec-ds": "lifecycle",
        }
        assert {row["job_id"]: row["artifact_name"] for row in body["health"]} == {
            "exec-job": "NightlyLoad",
            "exec-route": "OrderIntake",
            "exec-ds": "PriceLookup",
        }

    async def test_explicit_kind_narrows(self, estate_client):
        """What the L3 job-graph overlay sends to keep its current content."""
        with _scope([_P]):
            resp = await estate_client.get(
                "/api/v1/runtime/job-health?executable_kind=job"
            )

        assert resp.status_code == 200
        assert [row["job_id"] for row in resp.json()["health"]] == ["exec-job"]

    async def test_unknown_kind_is_rejected(self, estate_client):
        with _scope([_P]):
            resp = await estate_client.get(
                "/api/v1/runtime/job-health?executable_kind=not_a_kind"
            )

        assert resp.status_code == 422

    async def test_runtime_jobs_endpoint_is_unchanged(self, estate_client):
        """`/runtime/jobs` still defaults to `job`. It shares the input model."""
        with _scope([_P]):
            resp = await estate_client.get("/api/v1/runtime/jobs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert [job["name"] for job in body["jobs"]] == ["nightly_load"]

    async def test_runtime_jobs_still_honours_an_explicit_kind(self, estate_client):
        with _scope([_P]):
            resp = await estate_client.get(
                "/api/v1/runtime/jobs?executable_kind=route"
            )

        assert resp.status_code == 200
        assert [job["name"] for job in resp.json()["jobs"]] == ["order_intake"]
