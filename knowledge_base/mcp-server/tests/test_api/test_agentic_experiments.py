"""API isolation tests for the additive experiment baseline shell."""

from __future__ import annotations

import datetime
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from mcp_server.api.routes import agentic_experiments
from mcp_server.config import config
from mcp_server.db.models import (
    GenerateRun,
    GenerationExperiment,
    GenerationExperimentArtifact,
    GenerationRecipeSnapshot,
    GenerationSourceSnapshot,
    GenerationSourceSnapshotItem,
    GenerationTaskAttempt,
    GenerationTaskEvent,
    GenerationTaskRun,
    Project,
    ProjectDocRagHash,
)


async def _seed_legacy_baseline(db_session, tmp_path) -> tuple[Project, GenerateRun, dict[str, str]]:
    project = Project(
        id="alitalend-fixture",
        display_name="AliTalend Fixture",
        local_path=str(tmp_path / "repo"),
        status="ready",
        job_count=2,
        doc_count=2,
    )
    run = GenerateRun(
        project_id=project.id,
        triggered_by="api",
        status="completed",
        started_at=datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc),
        completed_at=datetime.datetime(2026, 7, 27, 0, 1, tzinfo=datetime.timezone.utc),
        doc_count=2,
        input_tokens=100,
        output_tokens=20,
        model_name="deepseek-v4-pro",
    )
    db_session.add_all((project, run))
    await db_session.commit()

    output = tmp_path / "project_root" / "output" / project.id
    docs = output / "docs"
    logs = output / "logs"
    docs.mkdir(parents=True)
    logs.mkdir(parents=True)
    (docs / "00_FIRST.md").write_text("# First\n\nFrozen fact.\n", encoding="utf-8")
    (docs / "01_SECOND.md").write_text("# Second\n\nAnother fact.\n", encoding="utf-8")
    (docs / "_internal.md").write_text("# Internal receipt\n", encoding="utf-8")
    (logs / f"run_metrics_{run.id}.json").write_text(
        json.dumps(
            {
                "run_id": run.id,
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": None,
            }
        ),
        encoding="utf-8",
    )
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(docs.glob("*.md"))
    }
    return project, run, hashes


@pytest.mark.asyncio
async def test_experiment_api_is_hidden_when_feature_is_disabled(client) -> None:
    response = await client.get("/mcp/agentic-experiments", params={"project_id": "anything"})
    assert response.status_code == 404
    capabilities = await client.get("/mcp/agentic-experiments/capabilities")
    assert capabilities.status_code == 404


def test_bundled_recipe_discovery_honors_runtime_data_directory(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "mounted-data" / "agentic_recipes"
    runtime_root.mkdir(parents=True)
    monkeypatch.setattr(config, "agentic_recipe_root", runtime_root)

    assert agentic_experiments._bundled_recipe_root() == runtime_root


@pytest.mark.asyncio
async def test_background_candidate_resolves_recipe_from_immutable_snapshot() -> None:
    experiment = SimpleNamespace(
        id="experiment-id",
        recipe_snapshot_id="recipe-snapshot-id",
    )
    snapshot = SimpleNamespace(
        id="recipe-snapshot-id",
        experiment_id="experiment-id",
        recipe_id="alitalend-legacy-equivalence",
        recipe_version="1.0.0",
    )
    db = AsyncMock()
    db.get.return_value = snapshot

    _, manifest = await agentic_experiments._resolve_experiment_recipe(
        db,
        experiment,
    )

    db.get.assert_awaited_once_with(
        GenerationRecipeSnapshot,
        "recipe-snapshot-id",
    )
    assert manifest.id == "alitalend-legacy-equivalence"
    assert manifest.version == "1.0.0"


@pytest.mark.asyncio
async def test_background_candidate_fails_closed_without_recipe_snapshot() -> None:
    experiment = SimpleNamespace(
        id="experiment-id",
        recipe_snapshot_id=None,
    )

    with pytest.raises(LookupError, match="has no recipe snapshot"):
        await agentic_experiments._resolve_experiment_recipe(
            AsyncMock(),
            experiment,
        )


@pytest.mark.asyncio
async def test_quick_run_reports_missing_runtime_recipe_without_internal_error(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    empty_root = tmp_path / "empty-agentic-recipes"
    empty_root.mkdir()
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_opencode_password", "test-only-password")
    monkeypatch.setattr(config, "doc_generation_deepseek_api_key", "test-only-key")
    monkeypatch.setattr(config, "agentic_recipe_root", empty_root)
    monkeypatch.setattr(
        agentic_experiments,
        "__file__",
        str(tmp_path / "installed" / "mcp_server" / "api" / "routes" / "route.py"),
    )

    response = await client.post(
        "/mcp/agentic-experiments/quick-run",
        json={
            "project_id": "any-project",
            "acknowledge_non_publishing": True,
            "idempotency_key": "missing-recipe",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Certified OpenCode documentation recipe is unavailable"
    )


@pytest.mark.asyncio
async def test_capabilities_are_provider_neutral_safe_and_report_dispatch_blockers(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_paid_execution_enabled", False)
    monkeypatch.setattr(config, "agentic_max_cost_usd", 0.0)

    response = await client.get("/mcp/agentic-experiments/capabilities")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider_neutral"] is True
    assert body["model_selection"] == "runtime_discovered"
    assert body["publishes"] is False
    assert body["candidate_dispatch_available"] is False
    assert body["paid_execution_enabled"] is False
    assert body["opencode_version"] == "1.18.4"
    assert {recipe["recipe_id"] for recipe in body["recipes"]} == {
        "alitalend-legacy-equivalence",
        "codebase-documentation",
        "logistics-operations",
    }
    logistics = next(
        recipe
        for recipe in body["recipes"]
        if recipe["recipe_id"] == "logistics-operations"
    )
    assert {
        requirement["source_role"]
        for requirement in logistics["source_requirements"]
        if not requirement["optional"]
    } == {
        "operational_policy",
        "shipment_events",
        "mapping_reference",
        "runtime_observation",
    }
    assert "OpenCode runtime authentication is unavailable" in body["execution_blockers"]
    assert "DeepSeek credentials are unavailable" in body["execution_blockers"]
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "token" not in serialized
    assert "api_key" not in serialized


@pytest.mark.asyncio
async def test_create_freezes_baseline_idempotently_without_legacy_mutation(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project, run, original_hashes = await _seed_legacy_baseline(db_session, tmp_path)
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    payload = {
        "project_id": project.id,
        "baseline_run_id": run.id,
        "runtime_id": "fake",
        "acknowledge_non_publishing": True,
        "idempotency_key": "api-freeze-44",
    }

    first = await client.post("/mcp/agentic-experiments", json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["baseline_run_id"] == run.id
    assert body["status"] == "queued"
    assert body["publishes"] is False
    assert body["candidate_not_started"] is True
    assert body["source_snapshot_id"] is None
    assert body["source_snapshot_frozen"] is False
    assert body["recipe_snapshot_id"] is None
    assert body["recipe_id"] is None
    assert body["baseline_document_count"] == 3
    assert body["artifact_count"] == 5
    assert len(body["baseline_manifest_sha256"]) == 64
    assert body["baseline_metrics"] == {
        "document_count": 2,
        "duration_ms": None,
        "provider_latency_ms": None,
        "input_tokens": 100,
        "output_tokens": 20,
        "billed_cost_usd": None,
        "quality_score": None,
        "model_id": "deepseek-v4-pro",
        "attempt_count": None,
        "retry_count": None,
    }
    assert body["candidate_metrics"] == {
        "document_count": 0,
        "duration_ms": None,
        "provider_latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "billed_cost_usd": None,
        "quality_score": None,
        "model_id": None,
        "attempt_count": 0,
        "retry_count": 0,
    }

    second = await client.post("/mcp/agentic-experiments", json=payload)
    assert second.status_code == 201
    assert second.json()["experiment_id"] == body["experiment_id"]
    listed = await client.get(
        "/mcp/agentic-experiments",
        params={"project_id": project.id},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["artifact_count"] == 5
    assert listed.json()[0]["baseline_document_count"] == 3
    assert listed.json()[0]["artifacts"] == []

    run_id = run.id
    project_id = project.id
    db_session.expire_all()
    legacy_run = await db_session.get(GenerateRun, run_id)
    legacy_project = await db_session.get(Project, project_id)
    assert legacy_run is not None and legacy_run.status == "completed"
    assert legacy_run.doc_count == 2
    assert legacy_project is not None and legacy_project.status == "ready"
    assert legacy_project.doc_count == 2
    assert await db_session.scalar(select(func.count(GenerationExperiment.id))) == 1
    assert await db_session.scalar(select(func.count(GenerationExperimentArtifact.id))) == 5
    assert await db_session.scalar(select(func.count(ProjectDocRagHash.project_id))) == 0

    docs = tmp_path / "project_root" / "output" / project_id / "docs"
    after_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(docs.glob("*.md"))
    }
    assert after_hashes == original_hashes
    assert not (docs.parent / "docs_staging").exists()


@pytest.mark.asyncio
async def test_create_allows_standalone_candidate_without_legacy_baseline(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project = Project(
        id="standalone-opencode-fixture",
        display_name="Standalone OpenCode Fixture",
        local_path=str(tmp_path / "repo"),
        status="ready",
        job_count=1,
        doc_count=0,
    )
    db_session.add(project)
    await db_session.commit()
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)

    response = await client.post(
        "/mcp/agentic-experiments",
        json={
            "project_id": project.id,
            "runtime_id": "opencode",
            "acknowledge_non_publishing": True,
            "idempotency_key": "standalone-opencode-1",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["baseline_run_id"] is None
    assert body["comparison_status"] == "not_available"
    assert body["baseline_manifest_sha256"] is None
    assert body["baseline_document_count"] == 0
    assert body["artifact_count"] == 0
    assert body["baseline_metrics"]["document_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_mode", "expected_runtime"),
    [
        ("single_agent", "opencode"),
        ("orchestrated", "opencode-orchestrated"),
        ("grounded", "opencode-grounded"),
    ],
)
async def test_quick_run_freezes_generic_inputs_and_queues_isolated_opencode_candidate(
    client,
    db_session,
    tmp_path,
    monkeypatch,
    candidate_mode,
    expected_runtime,
) -> None:
    project, run, original_hashes = await _seed_legacy_baseline(db_session, tmp_path)
    scan_root = config.intodoc_output_dir / project.id
    scan_root.mkdir(parents=True)
    (scan_root / "scan_result.json").write_text(
        json.dumps({"jobs": [{"name": "JobA", "platform": "any"}]}),
        encoding="utf-8",
    )
    (scan_root / "redaction_report.json").write_text(
        json.dumps(
            {
                "redaction_level": "standard",
                "total_redactions": 0,
                "summary": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_opencode_password", "test-only-password")
    monkeypatch.setattr(config, "doc_generation_deepseek_api_key", "test-only-key")
    dispatched: list[dict[str, object]] = []

    async def fake_dispatch(**kwargs: object) -> None:
        dispatched.append(kwargs)

    monkeypatch.setattr(
        agentic_experiments,
        "_run_quick_opencode_candidate",
        fake_dispatch,
    )
    response = await client.post(
        "/mcp/agentic-experiments/quick-run",
        json={
            "project_id": project.id,
            "baseline_run_id": run.id,
            "candidate_mode": candidate_mode,
            "provider_id": "provider-neutral-test",
            "model_id": "model-neutral-test",
            "acknowledge_non_publishing": True,
            "idempotency_key": f"generate-dialog-opencode-{candidate_mode}",
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["runtime_id"] == expected_runtime
    assert body["provider_id"] == "provider-neutral-test"
    assert body["model_id"] == "model-neutral-test"
    assert body["recipe_id"] == "alitalend-legacy-equivalence"
    assert body["source_snapshot_frozen"] is True
    assert body["publishes"] is False
    assert body["status"] == "queued"
    assert len(dispatched) == 1
    dispatch = dispatched[0]
    assert dispatch["experiment_id"] == body["experiment_id"]
    assert dispatch["project_id"] == project.id
    assert dispatch["provider_id"] == "provider-neutral-test"
    assert dispatch["model_id"] == "model-neutral-test"
    assert dispatch["candidate_mode"] == candidate_mode
    assert dispatch["output_names"] == (
        "00_AGENT_BRIEF.md",
        "01_ARCHITECTURE_MAP.md",
        "02_COMPONENT_INVENTORY.md",
        "03_DATA_FLOWS_AND_LINEAGE.md",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md",
        "05_RUNTIME_CONFIG_AND_EXTERNALS.md",
        "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
        "07_QUALITY_SECURITY_RISK_SCORECARD.md",
        "08_CHANGE_PLAYBOOK.md",
        "09_EXECUTIVE_SUMMARY.md",
    )
    docs = config.project_root / "output" / project.id / "docs"
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(docs.glob("*.md"))
    } == original_hashes
    assert await db_session.scalar(select(func.count(ProjectDocRagHash.project_id))) == 0


@pytest.mark.asyncio
async def test_retention_api_only_performs_acknowledged_recoverable_moves(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project, run, original_hashes = await _seed_legacy_baseline(db_session, tmp_path)
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    monkeypatch.setattr(config, "agentic_retention_days", 30)
    created = await client.post(
        "/mcp/agentic-experiments",
        json={
            "project_id": project.id,
            "baseline_run_id": run.id,
            "runtime_id": "fake",
            "acknowledge_non_publishing": True,
            "idempotency_key": "retention-api",
        },
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["experiment_id"]
    experiment = await db_session.get(GenerationExperiment, experiment_id)
    assert experiment is not None
    experiment.status = "failed"
    experiment.completed_at = datetime.datetime(
        2020,
        1,
        1,
        tzinfo=datetime.timezone.utc,
    )
    await db_session.commit()

    missing_ack = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/quarantine",
        json={"acknowledge_recoverable_move": False},
    )
    assert missing_ack.status_code == 422

    project_output = config.project_root / "output" / project.id
    experiment_root = project_output / "experiments" / experiment_id
    trash_root = project_output / ".agentic-experiment-trash" / experiment_id
    assert experiment_root.is_dir()
    quarantined = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/quarantine",
        json={"acknowledge_recoverable_move": True},
    )
    assert quarantined.status_code == 200, quarantined.text
    assert quarantined.json()["state"] == "quarantined"
    assert quarantined.json()["hard_deleted"] is False
    assert quarantined.json()["publishes"] is False
    assert not experiment_root.exists()
    assert trash_root.is_dir()

    restored = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/restore",
        json={"acknowledge_recoverable_move": True},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["state"] == "restored"
    assert experiment_root.is_dir()
    assert not trash_root.exists()
    docs = project_output / "docs"
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(docs.glob("*.md"))
    } == original_hashes


@pytest.mark.asyncio
async def test_task_progress_and_explicit_retry_api_never_dispatch_or_expose_payloads(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project, run, _ = await _seed_legacy_baseline(db_session, tmp_path)
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    created = await client.post(
        "/mcp/agentic-experiments",
        json={
            "project_id": project.id,
            "baseline_run_id": run.id,
            "runtime_id": "fake",
            "acknowledge_non_publishing": True,
            "idempotency_key": "task-progress-retry",
        },
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["experiment_id"]

    snapshot_id = "b" * 64
    task_id = "c" * 64
    attempt_id = "00000000-0000-4000-8000-000000000025"
    snapshot = GenerationRecipeSnapshot(
        id=snapshot_id,
        experiment_id=experiment_id,
        recipe_id="generic-test-recipe",
        recipe_version="1",
        schema_version=1,
        manifest_sha256="1" * 64,
        source_requirements_sha256="2" * 64,
        effective_scope_sha256="3" * 64,
        semantic_prompts_sha256="4" * 64,
        output_contracts_sha256="5" * 64,
        graph_sha256="6" * 64,
        manifest_relative_path="shared/recipe/manifest.json",
    )
    db_session.add(snapshot)
    await db_session.flush()
    experiment = await db_session.get(GenerationExperiment, experiment_id)
    assert experiment is not None
    experiment.recipe_snapshot_id = snapshot_id
    task = GenerationTaskRun(
        id=task_id,
        experiment_id=experiment_id,
        recipe_snapshot_id=snapshot_id,
        node_id="document:any-output",
        task_type="synthesis",
        role="writer",
        dependencies_json=[],
        dependency_output_refs_json=[],
        output_ids_json=["any-output"],
        status="failed",
        attempt=1,
        max_attempts=2,
        semantic_prompt_sha256="7" * 64,
        graph_sha256="6" * 64,
        runtime_id="fake",
        provider_id="fake-provider",
        model_id="fake-model",
        completed_at=datetime.datetime(
            2026,
            7,
            27,
            tzinfo=datetime.timezone.utc,
        ),
    )
    attempt = GenerationTaskAttempt(
        id=attempt_id,
        task_run_id=task_id,
        attempt_number=1,
        execution_idempotency_key=f"{task_id}:1",
        status="failed",
        lease_owner="worker",
        started_at=datetime.datetime(
            2026,
            7,
            27,
            tzinfo=datetime.timezone.utc,
        ),
        completed_at=datetime.datetime(
            2026,
            7,
            27,
            0,
            0,
            1,
            tzinfo=datetime.timezone.utc,
        ),
        input_tokens=10,
        output_tokens=1,
    )
    event = GenerationTaskEvent(
        task_run_id=task_id,
        attempt_id=attempt_id,
        sequence_number=1,
        idempotency_key="failed-event",
        event_type="attempt.finished",
        payload_json={"secret": "must-not-be-returned"},
        occurred_at=datetime.datetime(
            2026,
            7,
            27,
            0,
            0,
            1,
            tzinfo=datetime.timezone.utc,
        ),
    )
    db_session.add_all((task, attempt, event))
    await db_session.commit()

    progress = await client.get(
        f"/mcp/agentic-experiments/{experiment_id}/tasks"
    )
    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["total"] == 1
    assert body["publishes"] is False
    assert body["tasks"][0]["status"] == "failed"
    assert body["tasks"][0]["attempts"][0]["input_tokens"] == 10
    assert body["tasks"][0]["events"][0]["event_type"] == "attempt.finished"
    assert "must-not-be-returned" not in progress.text
    assert f"{task_id}:1" not in progress.text

    retried = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/tasks/{task_id}/retry",
        json={"acknowledge_explicit_retry": True},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending"
    assert retried.json()["attempt"] == 1
    assert len(retried.json()["attempts"]) == 1
    assert [item["event_type"] for item in retried.json()["events"]] == [
        "attempt.finished",
        "retry.authorized",
    ]

    repeated = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/tasks/{task_id}/retry",
        json={"acknowledge_explicit_retry": True},
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "pending"
    assert len(repeated.json()["events"]) == 2


@pytest.mark.asyncio
async def test_reconcile_expires_ambiguous_lease_without_dispatch_or_automatic_retry(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project, run, _ = await _seed_legacy_baseline(db_session, tmp_path)
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    created = await client.post(
        "/mcp/agentic-experiments",
        json={
            "project_id": project.id,
            "baseline_run_id": run.id,
            "runtime_id": "opencode",
            "acknowledge_non_publishing": True,
            "idempotency_key": "reconcile-ambiguous-lease",
        },
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["experiment_id"]

    snapshot_id = "d" * 64
    task_id = "e" * 64
    attempt_id = "00000000-0000-4000-8000-000000000026"
    expired_at = datetime.datetime(
        2026,
        7,
        26,
        tzinfo=datetime.timezone.utc,
    )
    db_session.add(
        GenerationRecipeSnapshot(
            id=snapshot_id,
            experiment_id=experiment_id,
            recipe_id="generic-recovery-recipe",
            recipe_version="1",
            schema_version=1,
            manifest_sha256="1" * 64,
            source_requirements_sha256="2" * 64,
            effective_scope_sha256="3" * 64,
            semantic_prompts_sha256="4" * 64,
            output_contracts_sha256="5" * 64,
            graph_sha256="6" * 64,
            manifest_relative_path="shared/recovery/manifest.json",
        )
    )
    await db_session.flush()
    experiment = await db_session.get(GenerationExperiment, experiment_id)
    assert experiment is not None
    experiment.recipe_snapshot_id = snapshot_id
    db_session.add(
        GenerationTaskRun(
            id=task_id,
            experiment_id=experiment_id,
            recipe_snapshot_id=snapshot_id,
            node_id="document:operations-runbook",
            task_type="synthesis",
            role="writer",
            dependencies_json=[],
            dependency_output_refs_json=[],
            output_ids_json=["operations-runbook"],
            status="leased",
            attempt=1,
            max_attempts=2,
            semantic_prompt_sha256="7" * 64,
            graph_sha256="6" * 64,
            runtime_id="opencode",
            provider_id="deepseek",
            model_id="deepseek-v4",
            lease_owner="crashed-worker",
            lease_expires_at=expired_at,
            started_at=expired_at - datetime.timedelta(minutes=5),
        )
    )
    db_session.add(
        GenerationTaskAttempt(
            id=attempt_id,
            task_run_id=task_id,
            attempt_number=1,
            execution_idempotency_key=f"{task_id}:1",
            status="leased",
            lease_owner="crashed-worker",
            lease_expires_at=expired_at,
            started_at=expired_at - datetime.timedelta(minutes=5),
        )
    )
    await db_session.commit()

    missing_ack = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/reconcile",
        json={"acknowledge_no_dispatch": False},
    )
    assert missing_ack.status_code == 422

    response = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/reconcile",
        json={"acknowledge_no_dispatch": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["derived_status"] == "failed"
    assert body["recovered_expired_task_ids"] == [task_id]
    assert body["manual_retry_task_ids"] == [task_id]
    assert body["missing_required_outputs"] == ["operations-runbook"]
    assert body["dispatch_performed"] is False
    assert body["publishes"] is False
    assert body["tasks"] == [
        {
            "task_run_id": task_id,
            "state": "failed",
            "manual_retry_required": True,
            "reason": "expired_lease_external_call_ambiguous",
        }
    ]

    db_session.expire_all()
    task = await db_session.get(GenerationTaskRun, task_id)
    attempt = await db_session.get(GenerationTaskAttempt, attempt_id)
    assert task is not None and task.status == "failed"
    assert task.attempt == 1
    assert attempt is not None and attempt.status == "abandoned"
    assert (
        await db_session.scalar(
            select(func.count(GenerationTaskAttempt.id)).where(
                GenerationTaskAttempt.task_run_id == task_id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_source_snapshot_freezes_project_scanner_content_without_publishing(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    project, run, original_hashes = await _seed_legacy_baseline(db_session, tmp_path)
    monkeypatch.setattr(config, "agentic_experiments_enabled", True)
    experiment_response = await client.post(
        "/mcp/agentic-experiments",
        json={
            "project_id": project.id,
            "baseline_run_id": run.id,
            "runtime_id": "fake",
            "acknowledge_non_publishing": True,
            "idempotency_key": "source-freeze",
        },
    )
    assert experiment_response.status_code == 201
    experiment_id = experiment_response.json()["experiment_id"]
    scan_root = config.intodoc_output_dir / project.id
    scan_root.mkdir(parents=True)
    (scan_root / "scan_result.json").write_text(
        json.dumps({"jobs": [{"name": "JobA"}]}),
        encoding="utf-8",
    )
    (scan_root / "redaction_report.json").write_text(
        json.dumps(
            {
                "redaction_level": "standard",
                "total_redactions": 1,
                "summary": {"password": 1},
            }
        ),
        encoding="utf-8",
    )
    source_payload = {
        "sources": [
            {
                "id": "primary-scan",
                "source_kind": "intodoc_scan",
                "source_role": "implementation",
                "capabilities": [
                    "structured_records",
                    "searchable_text",
                ],
            }
        ]
    }

    first = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/source-snapshot",
        json=source_payload,
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["security_state"] == "approved"
    assert body["item_count"] == 2
    assert body["publishes"] is False
    assert body["source_kinds"] == ["intodoc_scan"]
    second = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/source-snapshot",
        json=source_payload,
    )
    assert second.status_code == 201
    assert second.json()["snapshot_id"] == body["snapshot_id"]
    recipe_payload = {
        "recipe_id": "alitalend-legacy-equivalence",
        "recipe_version": "1.0.0",
        "model_capabilities": ["tools"],
        "scope": {
            "required_capabilities": ["searchable_text"],
            "requested_output_ids": ["09_EXECUTIVE_SUMMARY"],
            "budgets": {
                "max_source_items": 1000,
                "max_evidence_items": 10000,
                "max_bytes": 104857600,
                "max_input_tokens": 900000,
                "max_tool_calls": 25,
                "max_elapsed_seconds": 3600,
            },
            "unknown_policy": "disclose",
        },
    }
    recipe = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/recipe-snapshot",
        json=recipe_payload,
    )
    assert recipe.status_code == 201, recipe.text
    recipe_body = recipe.json()
    assert recipe_body["recipe_id"] == "alitalend-legacy-equivalence"
    assert recipe_body["task_count"] == 3
    assert recipe_body["document_output_count"] == 1
    assert recipe_body["preflight_required"] is True
    assert recipe_body["publishes"] is False
    recipe_again = await client.post(
        f"/mcp/agentic-experiments/{experiment_id}/recipe-snapshot",
        json=recipe_payload,
    )
    assert recipe_again.status_code == 201
    assert recipe_again.json()["snapshot_id"] == recipe_body["snapshot_id"]
    detail = await client.get(f"/mcp/agentic-experiments/{experiment_id}")
    assert detail.status_code == 200
    assert detail.json()["source_snapshot_id"] == body["snapshot_id"]
    assert detail.json()["source_snapshot_frozen"] is True
    assert detail.json()["recipe_snapshot_id"] == recipe_body["snapshot_id"]
    assert detail.json()["recipe_id"] == "alitalend-legacy-equivalence"
    assert detail.json()["recipe_version"] == "1.0.0"

    project_id = project.id
    db_session.expire_all()
    assert await db_session.scalar(select(func.count(GenerationSourceSnapshot.id))) == 1
    assert (
        await db_session.scalar(
            select(func.count(GenerationSourceSnapshotItem.id))
        )
        == 2
    )
    assert await db_session.scalar(select(func.count(GenerationRecipeSnapshot.id))) == 1
    assert await db_session.scalar(select(func.count(GenerationTaskRun.id))) == 3
    docs = config.project_root / "output" / project_id / "docs"
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(docs.glob("*.md"))
    } == original_hashes
    assert not (docs.parent / "docs_staging").exists()
