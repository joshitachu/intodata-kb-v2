"""Durable scheduler persistence is provider- and recipe-neutral."""

from __future__ import annotations

import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from mcp_server.db.models import (
    GenerateRun,
    GenerationExperiment,
    GenerationRecipeSnapshot,
    GenerationTaskAttempt,
    GenerationTaskEvent,
    GenerationTaskRun,
    Project,
)
from mcp_server.experiments.infrastructure.persistence.task_scheduler_repository import (
    TaskSchedulerRepository,
)


def _at(second: int) -> datetime.datetime:
    return datetime.datetime(2026, 7, 27, 12, 0, second, tzinfo=datetime.timezone.utc)


async def _seed_tasks(db_session, specs: tuple[tuple[str, tuple[str, ...], int], ...]):
    """Seed a minimal compiled graph without coupling tests to any one recipe."""

    project = Project(id=f"project-{uuid4()}", display_name="Fixture", local_path="/fixture")
    baseline = GenerateRun(project_id=project.id, triggered_by="api", status="completed")
    db_session.add_all((project, baseline))
    await db_session.flush()
    experiment = GenerationExperiment(
        id=str(uuid4()),
        project_id=project.id,
        baseline_run_id=baseline.id,
        idempotency_key=str(uuid4()),
        runtime_id="fake",
        publishes=False,
    )
    snapshot = GenerationRecipeSnapshot(
        id=uuid4().hex * 2,
        experiment_id=experiment.id,
        recipe_id="generic-test-recipe",
        recipe_version="1",
        schema_version=1,
        manifest_sha256="a" * 64,
        source_requirements_sha256="b" * 64,
        effective_scope_sha256="c" * 64,
        semantic_prompts_sha256="d" * 64,
        output_contracts_sha256="e" * 64,
        graph_sha256="f" * 64,
        manifest_relative_path="candidate/recipe.json",
    )
    db_session.add_all((experiment, snapshot))
    task_ids: dict[str, str] = {}
    for node_id, dependencies, max_attempts in specs:
        task_id = uuid4().hex * 2
        task_ids[node_id] = task_id
        db_session.add(
            GenerationTaskRun(
                id=task_id,
                experiment_id=experiment.id,
                recipe_snapshot_id=snapshot.id,
                node_id=node_id,
                task_type="generic",
                role="worker",
                dependencies_json=list(dependencies),
                dependency_output_refs_json=[],
                output_ids_json=[node_id],
                status="pending",
                attempt=0,
                max_attempts=max_attempts,
                semantic_prompt_sha256="1" * 64,
                graph_sha256="2" * 64,
            )
        )
    await db_session.flush()
    return experiment, task_ids


@pytest.mark.asyncio
async def test_leases_only_ready_task_and_sequential_duplicate_defense(db_session) -> None:
    experiment, _ = await _seed_tasks(
        db_session,
        (("investigate", (), 2), ("synthesize", ("investigate",), 2)),
    )
    scheduler = TaskSchedulerRepository(db_session)
    first = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="worker-a",
        lease_seconds=30,
        now=_at(0),
    )
    assert first is not None
    assert first.execution_idempotency_key == f"{first.task_run_id}:1"
    assert first.node_id == "investigate"

    # A second scheduler pass cannot lease the already-active task, and its
    # dependent remains unready. The guarded UPDATE is the same protection used
    # when competing worker transactions observe stale rows.
    assert (
        await scheduler.lease_next(
            experiment_id=experiment.id,
            lease_owner="worker-b",
            lease_seconds=30,
            now=_at(1),
        )
        is None
    )

    await scheduler.finalize_attempt(
        task_run_id=first.task_run_id,
        attempt_id=first.attempt_id,
        lease_owner="worker-a",
        outcome="succeeded",
        completed_at=_at(2),
        input_tokens=3,
        output_tokens=4,
    )
    second = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="worker-b",
        lease_seconds=30,
        now=_at(3),
    )
    assert second is not None
    assert second.node_id == "synthesize"
    assert second.attempt_number == 1


@pytest.mark.asyncio
async def test_expired_lease_creates_new_attempt_and_restart_ready_visibility(db_session) -> None:
    experiment, _ = await _seed_tasks(db_session, (("any-domain-node", (), 2),))
    scheduler = TaskSchedulerRepository(db_session)
    first = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="lost-worker",
        lease_seconds=5,
        now=_at(0),
    )
    assert first is not None

    restart_ready = await scheduler.list_restart_ready(experiment_id=experiment.id, now=_at(6))
    assert [task.id for task in restart_ready] == [first.task_run_id]
    # Recovery is triggered by scheduler acquisition but does not silently retry
    # an ambiguous provider submission.
    assert (
        await scheduler.lease_next(
            experiment_id=experiment.id,
            lease_owner="replacement-worker",
            lease_seconds=5,
            now=_at(6),
        )
        is None
    )
    task = await db_session.get(GenerationTaskRun, first.task_run_id)
    assert task is not None and task.status == "failed"
    await scheduler.request_retry(
        task_run_id=first.task_run_id,
        requested_by="operator",
        now=_at(7),
    )
    recovered = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="replacement-worker",
        lease_seconds=5,
        now=_at(8),
    )
    assert recovered is not None
    assert recovered.attempt_number == 2
    assert recovered.execution_idempotency_key == f"{first.task_run_id}:2"
    assert recovered.execution_idempotency_key != first.execution_idempotency_key
    attempts = (
        await db_session.execute(
            select(GenerationTaskAttempt)
            .where(GenerationTaskAttempt.task_run_id == first.task_run_id)
            .order_by(GenerationTaskAttempt.attempt_number)
        )
    ).scalars().all()
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (1, "abandoned"),
        (2, "leased"),
    ]


@pytest.mark.asyncio
async def test_append_only_events_are_ordered_and_idempotent(db_session) -> None:
    experiment, _ = await _seed_tasks(db_session, (("generic-node", (), 2),))
    scheduler = TaskSchedulerRepository(db_session)
    lease = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="worker",
        lease_seconds=30,
        now=_at(0),
    )
    assert lease is not None
    first = await scheduler.append_event(
        task_run_id=lease.task_run_id,
        attempt_id=lease.attempt_id,
        idempotency_key="runtime-progress-1",
        event_type="runtime.progress",
        payload={"progress": 50},
        occurred_at=_at(1),
    )
    replay = await scheduler.append_event(
        task_run_id=lease.task_run_id,
        attempt_id=lease.attempt_id,
        idempotency_key="runtime-progress-1",
        event_type="runtime.progress",
        payload={"progress": 50},
        occurred_at=_at(2),
    )
    assert replay.id == first.id
    with pytest.raises(ValueError, match="different content"):
        await scheduler.append_event(
            task_run_id=lease.task_run_id,
            attempt_id=lease.attempt_id,
            idempotency_key="runtime-progress-1",
            event_type="runtime.progress",
            payload={"progress": 51},
            occurred_at=_at(2),
        )
    events = (
        await db_session.execute(
            select(GenerationTaskEvent)
            .where(GenerationTaskEvent.task_run_id == lease.task_run_id)
            .order_by(GenerationTaskEvent.sequence_number)
        )
    ).scalars().all()
    assert [event.sequence_number for event in events] == [1, 2]
    assert await db_session.scalar(
        select(func.count(GenerationTaskEvent.id)).where(
            GenerationTaskEvent.task_run_id == lease.task_run_id
        )
    ) == 2


@pytest.mark.asyncio
async def test_failed_dependency_is_visible_as_skipped_without_deleting_attempts(db_session) -> None:
    experiment, task_ids = await _seed_tasks(
        db_session,
        (("upstream", (), 2), ("downstream", ("upstream",), 2)),
    )
    scheduler = TaskSchedulerRepository(db_session)
    lease = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="worker",
        lease_seconds=30,
        now=_at(0),
    )
    assert lease is not None and lease.node_id == "upstream"
    failed = await scheduler.finalize_attempt(
        task_run_id=lease.task_run_id,
        attempt_id=lease.attempt_id,
        lease_owner="worker",
        outcome="failed",
        completed_at=_at(1),
        error_message="provider failed",
    )
    assert failed.status == "failed"
    assert await scheduler.skip_blocked_tasks(experiment_id=experiment.id, now=_at(2)) == (
        task_ids["downstream"],
    )
    downstream = await db_session.get(GenerationTaskRun, task_ids["downstream"])
    assert downstream is not None
    assert downstream.status == "skipped"
    assert "upstream" in (downstream.error_message or "")
    assert await db_session.scalar(
        select(func.count(GenerationTaskAttempt.id)).where(
            GenerationTaskAttempt.task_run_id == lease.task_run_id
        )
    ) == 1
    # A failure cannot be picked up again until an explicit retry authorization;
    # descendants reopen deterministically when their sole blocker is reopened.
    assert (
        await scheduler.lease_next(
            experiment_id=experiment.id,
            lease_owner="other-worker",
            lease_seconds=30,
            now=_at(3),
        )
        is None
    )
    await scheduler.request_retry(
        task_run_id=lease.task_run_id,
        requested_by="operator",
        now=_at(4),
    )
    assert downstream.status == "pending"
    retried = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="other-worker",
        lease_seconds=30,
        now=_at(5),
    )
    assert retried is not None
    assert retried.task_run_id == lease.task_run_id
    assert retried.attempt_number == 2
    assert retried.execution_idempotency_key != lease.execution_idempotency_key


@pytest.mark.asyncio
async def test_lease_and_outcome_validation_reject_ambiguous_values(db_session) -> None:
    experiment, _ = await _seed_tasks(db_session, (("generic-node", (), 2),))
    scheduler = TaskSchedulerRepository(db_session)
    with pytest.raises(ValueError, match="positive integer"):
        await scheduler.lease_next(
            experiment_id=experiment.id,
            lease_owner="worker",
            lease_seconds=True,
            now=_at(0),
        )
    lease = await scheduler.lease_next(
        experiment_id=experiment.id,
        lease_owner="worker",
        lease_seconds=5,
        now=_at(0),
    )
    assert lease is not None
    with pytest.raises(ValueError, match="lease expired"):
        await scheduler.finalize_attempt(
            task_run_id=lease.task_run_id,
            attempt_id=lease.attempt_id,
            lease_owner="worker",
            outcome="succeeded",
            completed_at=_at(5),
        )
    with pytest.raises(ValueError, match="finite non-negative"):
        await scheduler.finalize_attempt(
            task_run_id=lease.task_run_id,
            attempt_id=lease.attempt_id,
            lease_owner="worker",
            outcome="failed",
            completed_at=_at(4),
            provider_cost_usd="NaN",
        )
