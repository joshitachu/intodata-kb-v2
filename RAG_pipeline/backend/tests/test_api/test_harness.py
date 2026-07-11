from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Conversation, User
from src.harness import HarnessWorkflowRepository
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY, WorkflowRunCreate, WorkflowRunStatus


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
            response = await client.get("/api/v1/harness/connectors")
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
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["run"]["id"] == run.id
    assert "runtime_provider_id" in detail["run"]
