"""Run-creation licence chokepoint and generation quota (plan Decision 6).

The suite runs on SQLite, which has no advisory locks, so a true two-session
race for the last slot cannot be reproduced here. ``test_quota_lock_*`` instead
proves the PostgreSQL path on the SQL actually executed: the lock statement is
issued on the same connection, before the count and the insert, with no commit
in between.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import event

from mcp_server.db.models import GenerateRun, Project
from mcp_server.licensing import usage
from mcp_server.licensing.core import LicenceRefused, RefusalCode
from mcp_server.licensing.runtime import set_policy
from mcp_server.licensing.usage import GATE_RETRY_TRIGGER_SUFFIX, RunAdmission
from mcp_server.services.project_registry import ProjectRegistry
from tests.test_licensing.licence_fixtures import expired_token, install_policy, issue_token

PROJECT = "quota-project"


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    set_policy(None)


@pytest.fixture
async def project(session_factory):
    async with session_factory() as db:
        db.add(Project(id=PROJECT, display_name=PROJECT, local_path="/tmp/x", platform="talend"))
        await db.commit()
    return PROJECT


async def _start(session_factory, **kwargs) -> int:
    async with session_factory() as db:
        run = await ProjectRegistry(db).create_generate_run(PROJECT, **kwargs)
        await db.commit()
        return run.id


async def _finish(session_factory, run_id: int, status: str) -> None:
    async with session_factory() as db:
        await ProjectRegistry(db).complete_generate_run(run_id, status=status)
        await db.commit()


async def test_fourth_generation_in_period_is_refused(session_factory, project):
    install_policy(issue_token(generations=3))
    first = await _start(session_factory)
    await _finish(session_factory, first, "completed")
    second = await _start(session_factory)
    await _finish(session_factory, second, "completed_warnings")
    await _start(session_factory)  # still running: a running run holds its slot

    with pytest.raises(LicenceRefused) as refused:
        await _start(session_factory)

    detail = refused.value.refusal.to_detail()
    assert detail["code"] == RefusalCode.QUOTA_EXHAUSTED.value
    assert detail["used"] == 3 and detail["limit"] == 3
    async with session_factory() as db:
        # Refused before anything was written.
        assert len(await ProjectRegistry(db).list_generate_runs(PROJECT)) == 3


async def test_failed_run_does_not_consume_quota(session_factory, project):
    install_policy(issue_token(generations=3))
    failed = await _start(session_factory)
    await _finish(session_factory, failed, "failed")
    for _ in range(3):
        await _start(session_factory)
    with pytest.raises(LicenceRefused):
        await _start(session_factory)


async def test_user_cancelled_run_consumes_quota(session_factory, project):
    """Cancel spends tokens and the next run resumes its staging; it must count."""
    install_policy(issue_token(generations=3))
    for _ in range(3):
        run = await _start(session_factory)
        await _finish(session_factory, run, "cancelled")
    with pytest.raises(LicenceRefused) as exc:
        await _start(session_factory)
    assert exc.value.refusal.code.value == "quota_exhausted"


async def test_reruns_count(session_factory, project):
    """A phase rerun is a top-level run (it spends tokens) and counts."""
    install_policy(issue_token(generations=1))
    await _start(session_factory, triggered_by="api", model_name="m")
    with pytest.raises(LicenceRefused):
        await _start(session_factory, triggered_by="api", model_name="m")


async def test_continuations_are_licensed_but_not_counted(session_factory, project):
    install_policy(issue_token(generations=2))
    parent = await _start(session_factory)
    for name in ("part_a", "part_b", "part_c"):
        await _start(
            session_factory,
            triggered_by="composite",
            partition_name=name,
            admission=RunAdmission.CONTINUATION,
        )
    await _start(
        session_factory,
        triggered_by=f"webhook{GATE_RETRY_TRIGGER_SUFFIX}",
        admission=RunAdmission.CONTINUATION,
    )
    # 1 counted so far despite 5 rows: the second top-level run is admitted.
    await _start(session_factory)
    with pytest.raises(LicenceRefused):
        await _start(session_factory)
    # An admitted parent's children still start once the allowance is used up.
    await _start(
        session_factory,
        triggered_by="composite",
        partition_name="part_d",
        admission=RunAdmission.CONTINUATION,
    )
    async with session_factory() as db:
        status = install_policy(issue_token(generations=2)).status()
        assert await usage.count_generations(db, status.claims) == 2
    assert parent


async def test_continuation_still_requires_a_valid_licence(session_factory, project):
    install_policy(expired_token())
    with pytest.raises(LicenceRefused) as refused:
        await _start(
            session_factory,
            triggered_by="composite",
            partition_name="part_a",
            admission=RunAdmission.CONTINUATION,
        )
    assert refused.value.refusal.code is RefusalCode.LICENCE_EXPIRED


@pytest.mark.parametrize(
    "kwargs",
    [
        # A partition row is excluded from the count, so it must be a continuation.
        {"triggered_by": "composite", "partition_name": "part_a"},
        # A continuation must be a row the count actually excludes.
        {"triggered_by": "api", "admission": RunAdmission.CONTINUATION},
    ],
)
async def test_admission_must_agree_with_the_count_rule(session_factory, project, kwargs):
    install_policy(issue_token())
    with pytest.raises(ValueError):
        await _start(session_factory, **kwargs)


async def test_runs_outside_the_period_do_not_count(session_factory, project):
    now = datetime.datetime.now(datetime.timezone.utc)
    install_policy(issue_token(start=now - datetime.timedelta(days=2), generations=1))
    async with session_factory() as db:
        db.add(
            GenerateRun(
                project_id=PROJECT,
                triggered_by="api",
                status="completed",
                started_at=now - datetime.timedelta(days=10),
            )
        )
        await db.commit()
    await _start(session_factory)  # the old run belongs to the previous period
    with pytest.raises(LicenceRefused):
        await _start(session_factory)


async def test_expired_licence_refuses_scans_and_generations(session_factory, project):
    install_policy(expired_token())
    async with session_factory() as db:
        registry = ProjectRegistry(db)
        with pytest.raises(LicenceRefused) as scan_refused:
            await registry.create_scan_run(PROJECT)
        with pytest.raises(LicenceRefused) as gen_refused:
            await registry.create_generate_run(PROJECT)
    assert scan_refused.value.refusal.code is RefusalCode.LICENCE_EXPIRED
    assert gen_refused.value.refusal.code is RefusalCode.LICENCE_EXPIRED


async def test_missing_licence_refuses_a_scan(session_factory, project):
    install_policy(None)
    async with session_factory() as db:
        with pytest.raises(LicenceRefused) as refused:
            await ProjectRegistry(db).create_scan_run(PROJECT)
    assert refused.value.refusal.code is RefusalCode.LICENCE_MISSING


async def test_enforcement_off_is_unaffected_and_counts_nothing(
    session_factory, project, monkeypatch
):
    install_policy(expired_token(), enforce=False)

    async def _no_counting(*_args, **_kwargs):
        raise AssertionError("usage must not be counted when enforcement is off")

    monkeypatch.setattr(usage, "count_generations", _no_counting)
    monkeypatch.setattr(usage, "acquire_quota_lock", _no_counting)
    for _ in range(5):
        await _start(session_factory)
    async with session_factory() as db:
        await ProjectRegistry(db).create_scan_run(PROJECT)
        await db.commit()


async def test_quota_lock_is_taken_before_count_and_insert_in_one_transaction(
    engine, session_factory, project, monkeypatch
):
    """PostgreSQL path, proven on the executed SQL (SQLite stands in for the server).

    SQLite gets a ``pg_advisory_xact_lock`` function so the real statement runs;
    ``_uses_advisory_lock`` is forced to the PostgreSQL answer.
    """
    install_policy(issue_token(generations=3))
    monkeypatch.setattr(usage, "_uses_advisory_lock", lambda _db: True)

    lock_keys: list[int] = []
    trace: list[tuple[str, int]] = []  # (event, dbapi connection id)

    await engine.dispose()  # new connections pick up the function below

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_function("pg_advisory_xact_lock", 1, lambda key: lock_keys.append(key))

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record_sql(conn, _cursor, statement, _params, _context, _many):
        trace.append((statement.strip().split()[0].upper() + ":" + statement, id(conn.connection.dbapi_connection)))

    @event.listens_for(engine.sync_engine, "commit")
    def _record_commit(conn):
        trace.append(("COMMIT", id(conn.connection.dbapi_connection)))

    try:
        await _start(session_factory)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record_sql)
        event.remove(engine.sync_engine, "commit", _record_commit)

    def _index(predicate) -> int:
        return next(i for i, (sql, _conn) in enumerate(trace) if predicate(sql))

    lock_at = _index(lambda sql: "pg_advisory_xact_lock" in sql)
    count_at = _index(lambda sql: sql.startswith("SELECT:") and "count(" in sql.lower() and "generate_runs" in sql)
    insert_at = _index(lambda sql: sql.startswith("INSERT:") and "generate_runs" in sql)
    commit_at = _index(lambda sql: sql == "COMMIT")

    assert lock_keys == [usage.QUOTA_ADVISORY_LOCK_KEY]
    assert lock_at < count_at < insert_at < commit_at
    # One connection, and no commit between taking the lock and the insert:
    # the lock is held across count AND insert.
    assert len({conn for _sql, conn in trace[lock_at : commit_at + 1]}) == 1
    assert all(sql != "COMMIT" for sql, _conn in trace[lock_at:insert_at])


async def test_no_lock_and_no_count_on_an_invalid_licence(session_factory, project, monkeypatch):
    """The refusal does not depend on usage, so nothing is locked or counted."""
    install_policy(expired_token())
    calls: list[str] = []

    async def _spy(*_args, **_kwargs):
        calls.append("called")
        return 0

    monkeypatch.setattr(usage, "count_generations", _spy)
    monkeypatch.setattr(usage, "acquire_quota_lock", _spy)
    with pytest.raises(LicenceRefused):
        await _start(session_factory)
    assert calls == []
