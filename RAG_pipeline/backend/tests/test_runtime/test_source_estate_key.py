"""A runtime source with no owning project, and what must not change because of it.

`runtime_sources.project_id` used to answer three different questions at once --
who pays the poll, what mirrored rows are stamped with, and who may read them --
so a vendor tenant could not be registered before a project existed, a project
could not connect a second tenant, and deleting the project orphaned the source.
`estate_key` now answers only the second; `runtime_source_read_grants` answers
the third; nothing answers the first, because ownership was never a real
relation.

The properties pinned here are the ones the split is worthless without:

* a tenant registers with NO project, stays visible, and becomes readable the
  moment a project is attached;
* a project can connect a SECOND, different tenant -- the case the old
  per-project TMC index refused while advising the operator to widen the first
  source's ingest scope, which reaches none of the second tenant;
* a source attached to exactly ONE project -- the overwhelmingly common case,
  and every source that predates this change -- produces byte-identical SQL and
  identical results to the code before the split;
* an attachment's `environment_ids` / `window_days` bind an ownerless estate
  exactly as they bound a grant, including `[]` meaning "nothing" and the
  fail-closed rule for tables that can express neither narrowing;
* a read grant over ANOTHER project's estate still does not confer management of
  the connector.
"""

import datetime
from datetime import UTC

import pytest
from sqlalchemy import select

from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.db.models.runtime_data import RuntimeJobRow, RuntimeSourceRow, derive_estate_key
from src.runtime.models import (
    ListRuntimeArtifactsInput,
    ListRuntimeJobsInput,
    RuntimeArtifact,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeExecutionState,
    RuntimeSourceUpdate,
)
from src.runtime.read_scope import RuntimeReadTerm, resolve_runtime_read_scope
from src.runtime.store import (
    RuntimeDuplicateSourceError,
    RuntimeRecordNotFoundError,
    RuntimeStoreRepository,
)

_T0 = datetime.datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
_SRC = "tmc"
_PROD = "env-prod"
_ACC = "env-acc"


async def _mirror_job(
    store: RuntimeStoreRepository, *, stamp: str, job_name: str, environment_id: str
) -> None:
    """One inventory row, written with the stamp the sync writer would use."""
    await store.upsert_job(
        stamp,
        RuntimeJob(
            job_id=f"job-{job_name}",
            source_key=_SRC,
            name=job_name,
            environment_id=environment_id,
            environment_name=environment_id.upper(),
        ),
        _SRC,
        _T0,
    )


async def _mirror_run(
    store: RuntimeStoreRepository, *, stamp: str, run_id: str, environment_id: str
) -> None:
    await store.upsert_job_run(
        stamp,
        RuntimeJobRun(
            run_id=run_id,
            job_id="job-x",
            job_name="Job_X",
            source_key=_SRC,
            state=RuntimeExecutionState.SUCCESS,
            environment_id=environment_id,
            environment_name=environment_id.upper(),
            started_at=_T0,
            execution_id=run_id,
        ),
        _SRC,
        _T0,
    )


class TestOwnerlessRegistration:
    async def test_a_tenant_registers_with_no_project_at_all(self, db_session):
        """The whole point: no project has to exist first."""
        store = RuntimeStoreRepository(db_session)

        source = await store.create_source_config(source_type="tmc", label="Tenant E")

        assert source.estate_key == derive_estate_key(source.source_key)
        assert source.linked_project_ids == []
        # The derived key cannot collide with a project id -- a project id is
        # also a directory name, and ':' is not legal in a Windows path.
        assert ":" in source.estate_key

    async def test_an_unattached_tenant_stays_visible_to_a_scoped_operator(self, db_session):
        """Hiding it would make it unattachable -- the trap this change removes."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")

        listed = await store.list_source_configs(["proj-x"])
        fetched = await store.get_source_config(source.id, ["proj-x"])

        assert [item.id for item in listed] == [source.id]
        assert fetched.label == "Tenant E"

    async def test_attaching_a_project_makes_the_estate_readable(self, db_session):
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        estate = source.estate_key
        await _mirror_job(store, stamp=estate, job_name="Estate_Job", environment_id=_PROD)

        before = await resolve_runtime_read_scope(
            store, user_scope=["proj-x"], project_id="proj-x", now=_T0
        )
        await store.add_read_grant(
            source_id=source.id,
            project_id="proj-x",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        after = await resolve_runtime_read_scope(
            store, user_scope=["proj-x"], project_id="proj-x", now=_T0
        )
        jobs = await store.list_jobs(
            after.scope, ListRuntimeJobsInput(project_id="proj-x", read_terms=after.terms)
        )

        assert [term.project_id for term in before.terms] == ["proj-x"]
        assert [term.project_id for term in after.terms] == ["proj-x", estate]
        assert [job.name for job in jobs] == ["Estate_Job"]

    async def test_is_ownerless_is_derived_from_the_estate_key(self, db_session):
        """One rule, computed server-side, for both shapes of estate key.

        The prefix is a server rule; a client that re-implemented it would be
        duplicating something it cannot see change, and the two shapes are the
        only thing distinguishing "registered before any project" from
        "registered against one". Attaching a project does NOT make an ownerless
        estate owned -- the key is what it was written with.
        """
        store = RuntimeStoreRepository(db_session)
        ownerless = await store.create_source_config(source_type="tmc", label="Tenant E")
        owned = await store.create_source_config(
            project_id="proj-x", source_type="mock", label="Legacy"
        )
        await store.add_read_grant(
            source_id=ownerless.id,
            project_id="proj-x",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        attached = await store.get_source_config(ownerless.id, None)

        assert ownerless.is_ownerless is True
        assert owned.is_ownerless is False
        assert attached.is_ownerless is True
        assert attached.linked_project_ids == ["proj-x"]
        # Published on the wire, not merely available in Python.
        assert attached.model_dump()["is_ownerless"] is True

    async def test_an_attached_tenant_reports_its_projects(self, db_session):
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        for project_id in ("proj-z", "proj-x"):
            await store.add_read_grant(
                source_id=source.id,
                project_id=project_id,
                environment_ids=None,
                window_days=None,
                created_by="1",
            )

        fetched = await store.get_source_config(source.id, None)

        assert fetched.linked_project_ids == ["proj-x", "proj-z"]


class TestSecondTenantForOneProject:
    async def test_a_project_may_connect_two_different_tenants(self, db_session):
        """`uq_runtime_sources_project_tmc` is gone, and this is why.

        Two TMC sources in one project are two DIFFERENT vendor tenants behind
        two different credentials. The refused registration used to advise
        widening the first source's ingest scope, which reaches nothing at all of
        the second one.
        """
        store = RuntimeStoreRepository(db_session)

        first = await store.create_source_config(
            project_id="proj-x", source_type="tmc", physical_identity="tmc:eu#a", label="A"
        )
        second = await store.create_source_config(
            project_id="proj-x", source_type="tmc", physical_identity="tmc:eu#b", label="B"
        )

        assert first.id != second.id
        assert {item.id for item in await store.list_source_configs(["proj-x"])} == {
            first.id,
            second.id,
        }

    async def test_the_same_tenant_is_still_refused(self, db_session):
        """One tenant, one mirror, one poll. That guard is untouched."""
        store = RuntimeStoreRepository(db_session)
        await store.create_source_config(
            project_id="proj-x", source_type="tmc", physical_identity="tmc:eu#a", label="A"
        )

        with pytest.raises(RuntimeDuplicateSourceError):
            await store.create_source_config(
                project_id="proj-y", source_type="tmc", physical_identity="tmc:eu#a", label="Copy"
            )


class TestSingleProjectIsUnchanged:
    """The common case, and every source that predates this change."""

    @pytest.fixture
    async def legacy(self, db_session):
        """A source registered against one project, exactly as the migration leaves it.

        `estate_key` equals the former owner's project id and a single
        attachment row records it -- the two facts the migration writes for
        every pre-existing source.
        """
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id="proj-x", source_type="tmc", label="Legacy"
        )
        await _mirror_job(store, stamp="proj-x", job_name="Own_Job", environment_id=_ACC)
        return store, source

    async def test_the_estate_key_is_the_project_and_the_attachment_exists(self, legacy):
        store, source = legacy

        assert source.estate_key == "proj-x"
        assert source.linked_project_ids == ["proj-x"]
        assert await store.list_linked_projects(source.id) == ["proj-x"]

    async def test_the_resolved_read_set_is_one_unrestricted_term(self, legacy):
        """Identical to what the resolver produced before estate keys existed."""
        store, _source = legacy

        scope = await resolve_runtime_read_scope(
            store, user_scope=None, project_id="proj-x", now=_T0
        )

        assert scope.terms == [RuntimeReadTerm(project_id="proj-x")]
        assert scope.scope == ["proj-x"]
        # The source's own attachment is not a share: it stamps the very rows
        # the reader already owns.
        assert scope.shared_read is False
        assert scope.shared_from == []
        assert await store.resolve_runtime_read_projects("proj-x") == ["proj-x"]

    async def test_it_emits_byte_identical_sql_with_or_without_terms(self, legacy):
        """The predicate itself, compiled both ways and compared as a string.

        Modelled on `test_read_scope.py::test_an_ungranted_read_emits_byte_identical_sql...`:
        a single unrestricted term must still collapse to a bare equality, not to
        a one-element `IN` and not to a one-branch `OR`, because the unshared
        path is nearly every read this store serves and it has to keep its query
        plan and its index use.
        """
        store, _source = legacy
        scope = await resolve_runtime_read_scope(
            store, user_scope=None, project_id="proj-x", now=_T0
        )

        with_terms = store._apply_project_filter(
            select(RuntimeJobRow),
            RuntimeJobRow.project_id,
            ListRuntimeJobsInput(project_id="proj-x", read_terms=scope.terms),
            environment_column=RuntimeJobRow.environment_id,
        )
        # Exactly the statement this lane built before any of this existed.
        without_terms = store._apply_project_filter(
            select(RuntimeJobRow),
            RuntimeJobRow.project_id,
            ListRuntimeJobsInput(project_id="proj-x"),
            environment_column=RuntimeJobRow.environment_id,
        )

        assert str(with_terms.compile()) == str(without_terms.compile())
        assert " OR " not in str(with_terms.compile())
        assert "project_id IN" not in str(with_terms.compile())

    async def test_mirrored_rows_are_stamped_with_the_project_as_before(self, legacy):
        store, source = legacy

        stamp, unmapped = await store.resolve_project_id(source.id, None)

        assert (stamp, unmapped) == ("proj-x", True)

    async def test_the_source_still_appears_in_the_mirror_inventory(self, legacy):
        store, _source = legacy

        listed = await store.list_sources(["proj-x"], project_id="proj-x")

        assert [item.label for item in listed] == ["Legacy"]
        assert listed[0].estate_key == "proj-x"
        # `project_id` is gone from the model, not merely deprecated: while both
        # names existed, a consumer could keep reading the one that claims an
        # ownership relation the store no longer has.
        assert not hasattr(listed[0], "project_id")


class TestStamping:
    async def test_an_ownerless_source_stamps_its_estate_key(self, db_session):
        """The fallback `resolve_project_id` uses when no container is mapped."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")

        stamp, unmapped = await store.resolve_project_id(source.id, "ws-1")

        assert stamp == derive_estate_key(source.source_key)
        assert unmapped is True

    async def test_the_workspace_map_still_wins(self, db_session):
        """Mapping is unchanged: it re-points containers at REAL projects."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        await store.add_container_mapping(
            source_id=source.id,
            container_key="ws-1",
            container_name="Workspace 1",
            project_id="proj-mapped",
            created_by="1",
        )

        assert await store.resolve_project_id(source.id, "ws-1") == ("proj-mapped", False)
        assert await store.resolve_project_id(source.id, "ws-other") == (
            derive_estate_key(source.source_key),
            True,
        )

    async def test_a_mapped_project_is_named_as_exposed(self, db_session):
        """An attachment reaches whatever the workspace map re-points, and says so."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        await store.add_container_mapping(
            source_id=source.id,
            container_key="ws-1",
            container_name="Workspace 1",
            project_id="proj-mapped",
            created_by="1",
        )

        grant = await store.add_read_grant(
            source_id=source.id,
            project_id="proj-x",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        assert grant.exposes_project_ids == [
            derive_estate_key(source.source_key),
            "proj-mapped",
        ]


class TestAttachmentNarrowing:
    """`environment_ids` / `window_days` bind an estate the same way they bound a grant."""

    @pytest.fixture
    async def estate(self, db_session):
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        stamp = source.estate_key
        await _mirror_job(store, stamp=stamp, job_name="Estate_Prod", environment_id=_PROD)
        await _mirror_job(store, stamp=stamp, job_name="Estate_Acc", environment_id=_ACC)
        await _mirror_run(store, stamp=stamp, run_id="prod-1", environment_id=_PROD)
        await store.upsert_artifact(
            stamp,
            RuntimeArtifact(artifact_id="art-1", source_key=_SRC, name="art-1"),
            _SRC,
            _T0,
        )
        return store, source, stamp

    async def _scope(self, store, source, *, environment_ids, window_days=None):
        await store.add_read_grant(
            source_id=source.id,
            project_id="proj-x",
            environment_ids=environment_ids,
            window_days=window_days,
            created_by="1",
        )
        return await resolve_runtime_read_scope(
            store, user_scope=["proj-x"], project_id="proj-x", now=_T0
        )

    async def test_an_environment_narrows_the_estate(self, estate):
        store, source, _stamp = estate
        scope = await self._scope(store, source, environment_ids=[_PROD])

        jobs = await store.list_jobs(
            scope.scope, ListRuntimeJobsInput(project_id="proj-x", read_terms=scope.terms)
        )

        assert [job.name for job in jobs] == ["Estate_Prod"]

    async def test_an_empty_environment_list_means_nothing(self, estate):
        """`[]` is a deliberate "no environment", never "unset"."""
        store, source, _stamp = estate
        scope = await self._scope(store, source, environment_ids=[])

        jobs = await store.list_jobs(
            scope.scope, ListRuntimeJobsInput(project_id="proj-x", read_terms=scope.terms)
        )

        assert jobs == []

    async def test_a_table_that_can_express_neither_narrowing_fails_closed(self, estate):
        """`runtime_artifacts` has no environment and no execution id.

        Letting it through unfiltered would hand a reader narrowed to one
        environment the estate's COMPLETE artifact inventory. A missing row is a
        reportable gap; a leaked one cannot be taken back.
        """
        store, source, _stamp = estate
        scope = await self._scope(store, source, environment_ids=[_PROD])

        artifacts = await store.list_artifacts(
            scope.scope,
            ListRuntimeArtifactsInput(project_id="proj-x", read_terms=scope.terms),
        )

        assert artifacts == []

    async def test_an_unnarrowed_attachment_exposes_the_whole_estate(self, estate):
        store, source, _stamp = estate
        scope = await self._scope(store, source, environment_ids=None)

        artifacts = await store.list_artifacts(
            scope.scope,
            ListRuntimeArtifactsInput(project_id="proj-x", read_terms=scope.terms),
        )

        assert [item.artifact_id for item in artifacts] == ["art-1"]


class TestAttachmentRules:
    async def test_attaching_the_estate_s_own_project_is_refused(self, db_session):
        """It would store a limit that cannot remove a single row."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id="proj-x", source_type="tmc", label="Legacy"
        )

        with pytest.raises(RuntimeDuplicateSourceError):
            await store.add_read_grant(
                source_id=source.id,
                project_id="proj-x",
                environment_ids=[_PROD],
                window_days=None,
                created_by="1",
            )

    async def test_detaching_the_estate_s_own_project_is_a_no_op(self, db_session):
        """Symmetric with the refusal above: a half-revocation is worse than none."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id="proj-x", source_type="tmc", label="Legacy"
        )

        await store.delete_read_grant(source.id, "proj-x")

        assert await store.list_linked_projects(source.id) == ["proj-x"]

    async def test_the_estate_s_own_project_is_not_listed_as_a_reader(self, db_session):
        """`GET .../readers` lists SHARES. The estate's own project is not one."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id="proj-x", source_type="tmc", label="Legacy"
        )
        await store.add_read_grant(
            source_id=source.id,
            project_id="proj-y",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        grants = await store.list_read_grants(source.id)

        assert [grant.project_id for grant in grants] == ["proj-y"]
        assert grants[0].source_estate_key == "proj-x"
        # Published under one name only. `source_project_id` said "the project
        # that owns this source", which for an ownerless estate is a claim with
        # no referent at all.
        assert not hasattr(grants[0], "source_project_id")


class TestConnectorVisibility:
    """Sharing a MIRROR is not sharing the CONNECTOR."""

    @pytest.fixture
    async def shared(self, db_session):
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id="proj-owner", source_type="tmc", label="Owned"
        )
        await store.add_read_grant(
            source_id=source.id,
            project_id="proj-reader",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        return store, source

    async def test_a_granted_reader_cannot_reach_the_connector(self, shared):
        """Otherwise a read grant would silently carry credential rotation and
        DELETE over another project's tenant."""
        store, source = shared

        with pytest.raises(RuntimeRecordNotFoundError):
            await store.get_source_config(source.id, ["proj-reader"])
        assert await store.list_source_configs(["proj-reader"]) == []

    async def test_a_granted_reader_cannot_delete_the_source(self, shared):
        store, source = shared

        with pytest.raises(RuntimeRecordNotFoundError):
            await store.delete_source_config(source.id, ["proj-reader"])

        assert (
            await db_session_source_count(store) == 1
        )

    async def test_a_granted_reader_still_reads_the_mirror(self, shared):
        """The share itself is untouched: only the connector is out of reach."""
        store, source = shared
        await _mirror_job(
            store, stamp="proj-owner", job_name="Owner_Job", environment_id=_PROD
        )

        scope = await resolve_runtime_read_scope(
            store, user_scope=["proj-reader"], project_id="proj-reader", now=_T0
        )
        jobs = await store.list_jobs(
            scope.scope,
            ListRuntimeJobsInput(project_id="proj-reader", read_terms=scope.terms),
        )

        assert [job.name for job in jobs] == ["Owner_Job"]
        assert scope.shared_read is True

    async def test_an_ownerless_estate_is_managed_by_the_projects_attached_to_it(
        self, db_session
    ):
        """There is no owner to prefer, so its stakeholders are all there is."""
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(source_type="tmc", label="Tenant E")
        await store.add_read_grant(
            source_id=source.id,
            project_id="proj-x",
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        updated = await store.update_source_config(
            source.id, ["proj-x"], RuntimeSourceUpdate(label="Renamed")
        )

        assert updated.label == "Renamed"
        with pytest.raises(RuntimeRecordNotFoundError):
            await store.get_source_config(source.id, ["proj-unrelated"])

    async def test_a_mirrored_source_row_stays_scoped_by_its_stamp(self, db_session):
        """A discovered row carries no attachment; its estate key is the stamp,
        which is the rule it had before this change."""
        store = RuntimeStoreRepository(db_session)
        from src.runtime.models import RuntimeSource, RuntimeSyncStatus

        await store.upsert_source(
            "proj-d",
            RuntimeSource(
                estate_key="proj-d",
                source_key="talend_tmc",
                source_type="talend_tmc",
                label="Discovered",
                status=RuntimeSyncStatus.SUCCESS,
                ingested_at=_T0,
            ),
            _T0,
        )

        assert [item.label for item in await store.list_source_configs(["proj-d"])] == [
            "Discovered"
        ]
        assert await store.list_source_configs(["proj-other"]) == []


async def db_session_source_count(store: RuntimeStoreRepository) -> int:
    return len(
        list((await store._session.execute(select(RuntimeSourceRow.id))).scalars().all())
    )


class TestDenyAll:
    async def test_an_empty_scope_sees_no_source_at_all(self, db_session):
        """Including the unattached ownerless one.

        That branch is satisfied by the row alone -- no project is named in it
        -- so a deny-all scope has to be refused inside the predicate, not only
        in each caller's early return.
        """
        store = RuntimeStoreRepository(db_session)
        ownerless = await store.create_source_config(source_type="tmc", label="Tenant E")
        owned = await store.create_source_config(
            project_id="proj-x", source_type="mock", label="Owned"
        )

        assert await store.list_source_configs([]) == []
        assert await store.own_source_ids_for_project("proj-x", []) == []
        for source_id in (ownerless.id, owned.id):
            with pytest.raises(RuntimeRecordNotFoundError):
                await store.get_source_config(source_id, [])
