from __future__ import annotations

import datetime
from datetime import UTC
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Conversation, ProjectAssignment, User
from src.harness import HarnessWorkflowRepository
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_PACK_LIST_CAPABILITY,
    WorkflowInterruptCreate,
    WorkflowInterruptType,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


async def _async_result(value):
    return value


async def test_list_harness_capabilities(db_session) -> None:
    user = User(
        username="harness-api-user",
        email="harness-api@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/harness/capabilities")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    capability_ids = {item["id"] for item in body["capabilities"]}
    assert DOCUMENTATION_GENERATION_CAPABILITY.id in capability_ids
    assert DOCUMENTATION_PACK_LIST_CAPABILITY.id in capability_ids


async def test_runtime_manifest_exposes_filtered_safe_tools(db_session) -> None:
    user = User(
        username="harness-manifest-user",
        email="harness-manifest@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/harness/manifest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    tool = next(
        item
        for item in body["tools"]
        if item["id"] == DOCUMENTATION_GENERATION_CAPABILITY.id
    )
    assert tool["label"] == "Generate project documentation"
    assert "provider_id" not in tool
    assert "metadata" not in tool
    assert "active_pack_ids" not in body["pack_snapshot"]
    assert body["pack_snapshot"] == {}
    assert body["application_context"] == {}


async def test_legacy_pack_catalog_route_is_retired(db_session) -> None:
    user = User(
        username="harness-pack-user",
        email="harness-pack@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/harness/packs")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


async def test_list_harness_connectors_reuses_runtime_source_registry(db_session) -> None:
    user = User(
        username="harness-connector-user",
        email="harness-connector@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/harness/connector-types")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    provider_keys = {item["provider_key"] for item in body["connectors"]}
    assert "mock" in provider_keys
    assert all("capability_ids" in item for item in body["connectors"])


async def test_list_and_get_harness_workflow_receipt(db_session) -> None:
    user = User(
        username="harness-api-run-user",
        email="harness-api-run@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Harness")
    db_session.add(conversation)
    await db_session.flush()

    repo = HarnessWorkflowRepository(db_session)
    run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            provider_id=DOCUMENTATION_GENERATION_CAPABILITY.provider_id,
            status=WorkflowRunStatus.TRIGGERED,
            input_summary="Generate docs",
        )
    )
    child = await repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id=81,
        project_id="project-a",
        provider_status="completed",
        owner_user_id=user.id,
    )
    run = await repo.update_run(
        ["project-a"],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
    )

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            list_response = await client.get("/api/v1/harness/workflows?project_id=project-a")
            detail_response = await client.get(f"/api/v1/harness/workflows/{run.id}")
    finally:
        app.dependency_overrides.clear()

    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["id"] == run.id
    assert list_response.json()["runs"][0]["children"][0]["id"] == child.id
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["run"]["id"] == run.id
    assert "runtime_provider_id" in detail["run"]
    assert detail["run"]["children"] == list_response.json()["runs"][0]["children"]


async def test_owner_can_view_resume_once_and_cancel_interrupted_workflow(db_session) -> None:
    owner = User(username="interrupt-owner", email="interrupt-owner@example.com", roles=[])
    other = User(username="interrupt-other", email="interrupt-other@example.com", roles=[])
    db_session.add_all([owner, other])
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Pending harness")
    db_session.add(conversation)
    await db_session.flush()
    repo = HarnessWorkflowRepository(db_session)
    run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=owner.id,
            project_id="__pending_project_scope__",
            intent_mode="generate_docs",
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            provider_id=DOCUMENTATION_GENERATION_CAPABILITY.provider_id,
            status=WorkflowRunStatus.INTERRUPTED,
            current_node="await_project_scope",
        )
    )
    interrupt = await repo.create_interrupt(
        WorkflowInterruptCreate(
            workflow_run_id=run.id,
            owner_user_id=owner.id,
            interrupt_type=WorkflowInterruptType.CLARIFICATION,
            prompt="Choose a project",
            options=[{"id": "project-a", "label": "Project A"}],
            argument_hash="b" * 64,
            expires_at=datetime.datetime.now(UTC) + datetime.timedelta(minutes=30),
        )
    )
    owner_auth = SimpleNamespace(id=owner.id, username=owner.username, is_superadmin=False, roles=[])
    other_auth = SimpleNamespace(id=other.id, username=other.username, is_superadmin=False, roles=[])
    await db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: other_auth
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            hidden = await client.get(f"/api/v1/harness/workflows/{run.id}")
        app.dependency_overrides[get_current_user] = lambda: owner_auth
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            detail = await client.get(f"/api/v1/harness/workflows/{run.id}")
            resumed = await client.post(
                f"/api/v1/harness/workflows/{run.id}/resume",
                json={
                    "interrupt_id": interrupt.id,
                    "expected_version": run.version,
                    "response": "Project A",
                },
                headers=CSRF_HEADERS,
            )
        await db_session.commit()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cancelled = await client.post(
                f"/api/v1/harness/workflows/{run.id}/cancel",
                json={"expected_version": run.version + 1, "reason": "No longer needed"},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert hidden.status_code == 404
    assert detail.status_code == 200
    pending = detail.json()["pending_interrupts"][0]
    assert pending["prompt"] == "Choose a project"
    assert "argument_hash" not in pending
    assert "owner_user_id" not in pending
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run"]["status"] == "pending"
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert cancelled.json()["run"]["cancellation_reason"] == "No longer needed"
