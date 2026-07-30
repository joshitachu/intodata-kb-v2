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
    assert body["pack_snapshot"]["active_pack_ids"] == ["nexus.data-integration"]
    assert any(
        item["label"] == "Investigate integration failure"
        for item in body["application_context"]["workflows"]
    )
    assert "packs" not in body["application_context"]


async def test_pack_catalog_exposes_safe_views_and_active_snapshot(db_session) -> None:
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

    assert response.status_code == 200
    body = response.json()
    assert body["active"]["active_pack_ids"] == ["nexus.data-integration"]
    assert {item["id"] for item in body["packs"]} == {
        "nexus.documentation",
        "nexus.data-integration",
    }
    assert all("policy_hints" not in item for item in body["packs"])


async def test_project_pack_api_rejects_legacy_custom_pack_mutation(db_session) -> None:
    user = User(username="project-pack-admin", email="project-pack-admin@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    manifest = {
        "schema_version": 1,
        "id": "custom.api-pack",
        "name": "API Pack",
        "version": "1.0.0",
        "pack_type": "domain",
        "includes": ["nexus.documentation"],
        "documentation_provider_ref": None,
        "vocabulary": [], "entities": [], "workflows": [], "evidence_ranking": [],
        "artifact_templates": [], "connector_requirements": [],
        "default_scopes": {"project_required": True, "permissions": [], "domains": ["documentation"]},
        "policy_hints": {"approval_required_for": [], "evidence_required": True, "read_only": True},
        "output_rules": {"required_sections": [], "include_confidence": True, "include_artifact_links": True},
    }
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/harness/packs/projects/project-packs/custom", json={"manifest": manifest}, headers=CSRF_HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 410
    assert "legacy" in created.json()["detail"].lower()


async def test_project_members_can_browse_pack_recommendations_but_cannot_change_packs(db_session) -> None:
    user = User(username="project-pack-member", email="project-pack-member@example.com", roles=[])
    db_session.add(user)
    await db_session.flush()
    db_session.add(ProjectAssignment(user_id=user.id, project_id="project-packs", is_owner=False))
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            browse = await client.get("/api/v1/harness/packs/projects/project-packs/recommendations")
            mutate = await client.post(
                "/api/v1/harness/packs/projects/project-packs/custom",
                json={"manifest": {}},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert browse.status_code == 200
    assert mutate.status_code == 403


async def test_documentation_pack_api_returns_safe_mcp_metadata_and_allows_project_members(db_session, monkeypatch) -> None:
    from src.api.routes import harness_packs as harness_packs_mod

    # Non-owner member with project access, plus an outsider with none.
    user = User(username="documentation-pack-member", email="documentation-pack-member@example.com", roles=[])
    outsider = User(username="documentation-pack-outsider", email="documentation-pack-outsider@example.com", roles=[])
    db_session.add_all([user, outsider])
    await db_session.flush()
    db_session.add(ProjectAssignment(user_id=user.id, project_id="project-docs", is_owner=False))
    await db_session.flush()
    calls: dict[str, object] = {}

    class FakeDocumentationProvider:
        async def list_documentation_packs(self, *, access_token=None):
            calls["list_token"] = access_token
            return [
                harness_packs_mod.McpDocumentationPack(
                    id=7,
                    name="Talend migration",
                    version="2.1.0",
                    description="Migration-specific documentation instructions.",
                    certification_status="certified",
                    mutable=False,
                )
            ]

        async def get_project_documentation_pack_id(self, *, project_id, access_token=None):
            calls["get_project"] = project_id
            return 7

        async def set_project_documentation_pack(self, **kwargs):
            calls["set"] = kwargs
            return kwargs["plugin_pack_id"]

    monkeypatch.setattr(harness_packs_mod, "McpDocumentationProvider", FakeDocumentationProvider)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            browse = await client.get("/api/v1/harness/packs/projects/project-docs/documentation-pack")
            # A project member (non-owner) may now select the documentation pack.
            mutate = await client.put(
                "/api/v1/harness/packs/projects/project-docs/documentation-pack",
                json={"plugin_pack_id": 7},
                headers=CSRF_HEADERS,
            )
            # A user with no access to the project still cannot (404 info-hiding).
            app.dependency_overrides[get_current_user] = lambda: outsider
            denied = await client.put(
                "/api/v1/harness/packs/projects/project-docs/documentation-pack",
                json={"plugin_pack_id": 7},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert browse.status_code == 200
    assert browse.json() == {
        "project_id": "project-docs",
        "selected_plugin_pack_id": 7,
        "packs": [{"id": 7, "name": "Talend migration", "version": "2.1.0", "description": "Migration-specific documentation instructions."}],
    }
    assert "path" not in browse.text
    assert mutate.status_code == 200
    assert mutate.json()["selected_plugin_pack_id"] == 7
    assert calls["set"]["project_id"] == "project-docs"
    assert calls["set"]["plugin_pack_id"] == 7
    assert denied.status_code == 404


async def test_documentation_pack_api_sets_the_mcp_project_default(db_session, monkeypatch) -> None:
    from src.api.routes import harness_packs as harness_packs_mod

    user = User(username="documentation-pack-admin", email="documentation-pack-admin@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    calls: dict[str, object] = {}

    class FakeDocumentationProvider:
        async def list_documentation_packs(self, *, access_token=None):
            return [
                harness_packs_mod.McpDocumentationPack(
                    id=7,
                    name="Talend migration",
                    version="2.1.0",
                    description=None,
                    certification_status="certified",
                    mutable=False,
                )
            ]

        async def set_project_documentation_pack(self, **kwargs):
            calls.update(kwargs)
            return kwargs["plugin_pack_id"]

    monkeypatch.setattr(harness_packs_mod, "McpDocumentationProvider", FakeDocumentationProvider)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/v1/harness/packs/projects/project-docs/documentation-pack",
                json={"plugin_pack_id": 7},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["selected_plugin_pack_id"] == 7
    assert calls["project_id"] == "project-docs"
    assert calls["plugin_pack_id"] == 7


async def test_documentation_pack_workspace_combines_organization_and_personal_packs(
    db_session, monkeypatch
) -> None:
    from src.api.routes import harness_packs as harness_packs_mod

    user = User(
        username="documentation-workspace-admin",
        email="documentation-workspace-admin@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    class FakeDocumentationProvider:
        async def list_documentation_packs(self, *, access_token=None):
            return [
                harness_packs_mod.McpDocumentationPack(
                    id=7,
                    name="VolkerWessels Reverse Engineering",
                    version="2.1.0",
                    description="Organization documentation baseline.",
                    certification_status="certified",
                    mutable=False,
                )
            ]

        async def get_project_documentation_pack_id(self, **_kwargs):
            return 7

    personal = SimpleNamespace(
        id=12,
        name="Security Investment Case",
        version="1.0.0",
        artifact=SimpleNamespace(
            purpose="Add code security analysis and a management brief.",
            output_documents=[SimpleNamespace(title="Security Findings"), SimpleNamespace(title="Investment Brief")],
        ),
    )
    monkeypatch.setattr(harness_packs_mod, "McpDocumentationProvider", FakeDocumentationProvider)
    monkeypatch.setattr(
        harness_packs_mod,
        "_latest_personal_pack_versions",
        lambda *_args, **_kwargs: _async_result([personal]),
    )
    monkeypatch.setattr(
        harness_packs_mod,
        "_current_private_version_ids",
        lambda *_args, **_kwargs: _async_result([12]),
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/harness/packs/projects/reingest-drill/workspace"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "reingest-drill",
        "organization_packs": [{
            "id": 7,
            "name": "VolkerWessels Reverse Engineering",
            "version": "2.1.0",
            "description": "Organization documentation baseline.",
        }],
        "personal_packs": [{
            "id": 12,
            "name": "Security Investment Case",
            "version": "1.0.0",
            "description": "Add code security analysis and a management brief.",
            "output_documents": ["Security Findings", "Investment Brief"],
        }],
        "selected_organization_pack_id": 7,
        "selected_private_version_ids": [12],
    }


async def test_workspace_generation_sends_selected_personal_pack_to_intodoc(
    db_session, monkeypatch
) -> None:
    from src.api.routes import harness_packs as harness_packs_mod
    from src.harness.packs.draft_service import PackDraftService
    from src.harness.providers.mcp_docs import McpDocumentationGenerationResult
    from src.harness.registry import base_capability_registry

    user = User(
        username="workspace-generation-admin",
        email="workspace-generation-admin@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Pack builder")
    db_session.add(conversation)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())
    draft = await service.create_draft(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        name="Security Investment Case",
    )
    draft = await service.mutate(
        draft_id=draft.id,
        owner_user_id=user.id,
        expected_revision=draft.revision,
        patch={
            "schema_version": 3,
            "purpose": "Add a security investment case.",
            "analyses": [{"id": "security", "label": "Security", "purpose": "Find code risks."}],
            "evidence_requirements": [{"id": "code", "label": "Source code", "required": True, "weight": 100}],
            "output_documents": [{"id": "brief", "title": "Security Investment Brief", "sections": [{"id": "summary", "heading": "Summary", "order": 1}]}],
            "audience": {"label": "Engineering management", "render": {"detail_level": "brief"}},
        },
    )
    version = await service.save(
        draft_id=draft.id,
        owner_user_id=user.id,
        expected_revision=draft.revision,
    )
    captured: dict[str, object] = {}

    class FakeDocumentationProvider:
        async def list_documentation_packs(self, *, access_token=None):
            return [harness_packs_mod.McpDocumentationPack(
                id=7, name="VolkerWessels Reverse Engineering", version="2.1.0",
                description=None, certification_status="certified", mutable=False,
            )]

        async def set_project_documentation_pack(self, **kwargs):
            return kwargs["plugin_pack_id"]

        async def trigger_generation(self, **kwargs):
            captured.update(kwargs)
            return McpDocumentationGenerationResult(
                project_id="reingest-drill",
                status="generation_triggered",
                message="Documentation generation started",
                status_path="/status",
                docs_path="/docs",
            )

    monkeypatch.setattr(harness_packs_mod, "McpDocumentationProvider", FakeDocumentationProvider)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/harness/packs/projects/reingest-drill/generate",
                json={
                    "organization_pack_id": 7,
                    "private_version_ids": [version.id],
                    "resume": True,
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "generating"
    assert captured["plugin_pack_id"] == 7
    private_payload = captured["nexus_private_analysis_packs"]
    assert private_payload[0]["receipt"]["version_id"] == version.id
    assert private_payload[0]["artifact"]["output_documents"][0]["title"] == "Security Investment Brief"


async def test_documentation_pack_api_retires_free_form_creation(db_session) -> None:
    user = User(username="documentation-pack-creator", email="documentation-pack-creator@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/harness/packs/documentation-packs",
                json={
                    "name": "Operations Handbook",
                    "description": "Operations docs",
                    "documents": [
                        {
                            "name": "RUNBOOK",
                            "sections": ["Overview", "Recovery"],
                            "instructions": "Use evidence.",
                        }
                    ],
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 410
    assert "file/git" in response.json()["detail"].lower()


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
