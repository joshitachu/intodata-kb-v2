from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Conversation, ConversationMessage, User
from src.harness.packs.builder_workflow import PackBuilderWorkflow


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _supply_chain_analysis_pack() -> dict:
    return {
        "schema_version": 3,
        "purpose": "Support 48-hour cold-chain allocation decisions.",
        "analyses": [
            {"id": "exposure", "label": "Exposure", "purpose": "Map supplier and node exposure."},
        ],
        "evidence_requirements": [
            {"id": "supplier-status", "label": "Supplier status", "required": True, "weight": 100},
        ],
        "output_documents": [
            {
                "id": "executive-disruption-brief",
                "title": "Executive Disruption Brief",
                "sections": [{"id": "decision", "heading": "Decision summary", "order": 1}],
            },
            {
                "id": "supplier-node-scorecard",
                "title": "Supplier and Node Risk Scorecard",
                "sections": [{"id": "risk", "heading": "Prioritized risks", "order": 1}],
            },
            {
                "id": "mitigation-plan",
                "title": "Mitigation Action Plan",
                "sections": [{"id": "actions", "heading": "Actions", "order": 1}],
            },
        ],
        "audience": {
            "label": "Regional procurement directors",
            "roles": ["regional procurement directors"],
            "decision_needs": ["48-hour allocation decisions"],
            "domain_familiarity": "regulated cold-chain medicines",
            "desired_outcome": "protect patient supply",
            "render": {
                "seniority": "director",
                "domain_familiarity": "expert",
                "decision_horizon": "immediate",
                "detail_level": "brief",
            },
        },
    }


async def test_style_api_rejects_semantic_content_and_compiles_activation(db_session) -> None:
    user = User(username="governed-pack-api", email="governed-pack-api@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            semantic = await client.post(
                "/api/v1/harness/packs/styles/private",
                json={
                    "id": "style.unsafe",
                    "name": "Unsafe",
                    "version": "1.0.0",
                    "presentation": {"audience": "engineering"},
                    "workflows": [{"id": "unsafe.workflow"}],
                },
                headers=CSRF_HEADERS,
            )
            created = await client.post(
                "/api/v1/harness/packs/styles/private",
                json={
                    "id": "style.engineering-api",
                    "name": "Engineering API",
                    "version": "1.0.0",
                    "presentation": {"audience": "engineering", "tone": "concise"},
                },
                headers=CSRF_HEADERS,
            )
            forbidden_policy = await client.put(
                "/api/v1/harness/packs/projects/project-packs/activations/documentation.generate",
                json={
                    "foundation_pack_ids": ["nexus.documentation"],
                    "functional_pack_ids": ["nexus.data-integration"],
                    "organization_policy": {"allowed_functional_pack_ids": []},
                },
                headers=CSRF_HEADERS,
            )
            activated = await client.put(
                "/api/v1/harness/packs/projects/project-packs/activations/documentation.generate",
                json={
                    "foundation_pack_ids": ["nexus.documentation"],
                    "functional_pack_ids": ["nexus.data-integration"],
                    "style_pack_ids": [created.json()["pack"]["id"]],
                },
                headers=CSRF_HEADERS,
            )
            current = await client.get(
                "/api/v1/harness/packs/projects/project-packs/activations/documentation.generate"
            )
            scored = await client.put(
                f"/api/v1/harness/packs/styles/private/{created.json()['pack']['id']}/quality",
                json={
                    "quality_score": 0.9,
                    "evaluation_score": 0.9,
                    "evaluation_metadata": {"review": "export-ready"},
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert semantic.status_code == 422
    assert created.status_code == 201
    created_pack = created.json()["pack"]
    assert len(created_pack["content_hash"]) == 64
    assert created_pack["includes"] == []
    assert created_pack["workflow_labels"] == []
    assert created_pack["workflow_descriptions"] == []
    assert created_pack["entity_labels"] == []
    assert created_pack["default_domains"] == []
    assert created_pack["documentation_provider_present"] is False
    assert created_pack["documentation_provider_ref"] is None
    assert forbidden_policy.status_code == 422
    assert activated.status_code == 200
    assert activated.json()["compiled_snapshot"]["snapshot_hash"]
    assert activated.json()["presentation"]["audience"] == "engineering"
    assert current.status_code == 200
    assert current.json()["compiled_snapshot"] == activated.json()["compiled_snapshot"]
    assert scored.status_code == 200
    assert scored.json()["promotion_eligible"] is True
    assert scored.json()["pack"]["trust"] == "unreviewed"


async def test_certified_library_is_read_only_and_policy_is_visible(db_session) -> None:
    user = User(username="library-api", email="library-api@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            library = await client.get("/api/v1/harness/packs/library")
            policy = await client.get("/api/v1/harness/packs/policy")
            mutation = await client.post(
                "/api/v1/harness/packs/projects/project-packs/custom",
                json={"manifest": {"id": "nexus.documentation"}},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert library.status_code == 200
    packs = {pack["id"]: pack for pack in library.json()["packs"]}
    assert {pack["phase"] for pack in packs.values()} == {"foundation", "functional"}
    documentation = packs["nexus.documentation"]
    assert len(documentation["content_hash"]) == 64
    assert documentation["includes"] == []
    assert "Generate documentation" in documentation["workflow_labels"]
    assert len(documentation["workflow_labels"]) == len(documentation["workflow_descriptions"])
    assert documentation["entity_labels"] == ["Document"]
    assert documentation["default_domains"] == ["documentation"]
    assert documentation["documentation_provider_present"] is True
    assert documentation["documentation_provider_ref"] == "mcp.docs.default"
    functional = packs["nexus.data-integration"]
    assert functional["includes"] == ["nexus.documentation"]
    assert functional["documentation_provider_present"] is False
    assert functional["documentation_provider_ref"] is None
    assert "policy_hints" not in documentation
    assert "style" not in documentation
    assert policy.status_code == 200
    assert policy.json()["id"] == "org.default"
    assert mutation.status_code == 410


async def test_style_owner_cannot_self_attest_quality(db_session) -> None:
    user = User(username="style-owner-api", email="style-owner-api@example.com")
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/harness/packs/styles/private",
                json={
                    "id": "style.owner-quality",
                    "name": "Owner Quality",
                    "version": "1.0.0",
                    "presentation": {"audience": "business"},
                },
                headers=CSRF_HEADERS,
            )
            score = await client.put(
                "/api/v1/harness/packs/styles/private/style.owner-quality/quality",
                json={"quality_score": 1, "evaluation_score": 1, "evaluation_metadata": {}},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert score.status_code == 403
    assert "trusted evaluator" in score.json()["detail"].lower()


async def test_admin_can_score_another_users_private_style_pack(db_session) -> None:
    owner = User(username="evaluated-owner", email="evaluated-owner@example.com")
    evaluator = User(
        username="trusted-evaluator", email="trusted-evaluator@example.com", is_superadmin=True
    )
    db_session.add_all([owner, evaluator])
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/harness/packs/styles/private",
                json={
                    "id": "style.external-review",
                    "name": "External Review",
                    "version": "1.0.0",
                    "presentation": {"audience": "engineering"},
                },
                headers=CSRF_HEADERS,
            )
            app.dependency_overrides[get_current_user] = lambda: evaluator
            scored = await client.put(
                f"/api/v1/harness/packs/styles/private/{created.json()['pack']['id']}/quality",
                json={
                    "quality_score": 0.9,
                    "evaluation_score": 0.9,
                    "evaluation_metadata": {"suite": "trusted-eval"},
                    "target_owner_user_id": owner.id,
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert scored.status_code == 200
    assert scored.json()["evaluated_by_user_id"] == evaluator.id
    assert scored.json()["owner_user_id"] == owner.id


async def test_private_analysis_pack_lifecycle_is_project_independent_and_additive(db_session) -> None:
    user = User(
        username="analysis-pack-api",
        email="analysis-pack-api@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Supply-chain Pack")
    db_session.add(conversation)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            governed = await client.put(
                "/api/v1/harness/packs/projects/logistics-project/activations/default",
                json={
                    "foundation_pack_ids": ["nexus.documentation"],
                    "functional_pack_ids": ["nexus.data-integration"],
                },
                headers=CSRF_HEADERS,
            )
            draft = await client.post(
                "/api/v1/harness/packs/builder/drafts",
                json={"conversation_id": conversation.id, "name": "Supply-Chain Disruption Assessment"},
                headers=CSRF_HEADERS,
            )
            changed = await client.patch(
                f"/api/v1/harness/packs/builder/drafts/{draft.json()['id']}",
                json={
                    "expected_revision": draft.json()["revision"],
                    "name": "Supply-Chain Disruption Assessment",
                    "patch": _supply_chain_analysis_pack(),
                },
                headers=CSRF_HEADERS,
            )
            saved = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft.json()['id']}/save",
                json={"expected_revision": changed.json()["revision"]},
                headers=CSRF_HEADERS,
            )
            version_id = saved.json()["id"]
            compatibility = await client.post(
                f"/api/v1/harness/packs/builder/private/{version_id}/compatibility",
                json={"project_id": "logistics-project"},
                headers=CSRF_HEADERS,
            )
            preview = await client.post(
                f"/api/v1/harness/packs/builder/private/{version_id}/preview",
                json={"project_id": "logistics-project"},
                headers=CSRF_HEADERS,
            )
            activated = await client.post(
                f"/api/v1/harness/packs/builder/private/{version_id}/activate",
                json={"project_id": "logistics-project", "workflow_key": "default"},
                headers=CSRF_HEADERS,
            )
            current = await client.get(
                "/api/v1/harness/packs/projects/logistics-project/activations/default"
            )
            proposal = await client.post(
                f"/api/v1/harness/packs/builder/private/{version_id}/promotion-proposals",
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert governed.status_code == 200
    assert draft.status_code == 201
    assert changed.status_code == 200
    assert changed.json()["validation"]["valid"] is True
    assert saved.status_code == 201
    assert compatibility.status_code == 200
    assert compatibility.json()["compatible"] is True
    assert preview.status_code == 200
    assert "Executive Disruption Brief" in preview.json()["markdown"]
    assert activated.status_code == 200
    assert activated.json()["private_version_receipts"][0]["content_hash"] == saved.json()["content_hash"]
    assert current.status_code == 200
    assert current.json()["selection"]["functional_pack_ids"] == ["nexus.data-integration"]
    assert proposal.status_code == 201
    assert proposal.json()["status"] == "blocked_not_configured"


async def test_detached_pack_assist_retains_discovery_without_chat_persistence(
    db_session, monkeypatch
) -> None:
    from src.api.routes import harness_packs as harness_packs_module

    user = User(username="detached-assist-api", email="detached-assist-api@example.com")
    db_session.add(user)
    await db_session.flush()
    calls: list[list[dict[str, str]]] = []

    async def model(messages: list[dict[str, str]]) -> str:
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"question": "Who will use this Pack?"})
        return json.dumps({"name": "Cold-Chain Decisions", "artifact": _supply_chain_analysis_pack()})

    monkeypatch.setattr(
        harness_packs_module,
        "PackBuilderWorkflow",
        lambda: PackBuilderWorkflow(model_callable=model),
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/harness/packs/builder/drafts", json={}, headers=CSRF_HEADERS
            )
            draft_id = created.json()["id"]
            vague = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/assist",
                json={"instruction": ""},
                headers=CSRF_HEADERS,
            )
            questioned = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/assist",
                json={
                    "instruction": (
                        "Create a supplier disruption Pack with an executive brief and allocation plan "
                        "for cold-chain procurement leaders."
                    )
                },
                headers=CSRF_HEADERS,
            )
            completed = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/assist",
                json={"instruction": "Regional procurement directors."},
                headers=CSRF_HEADERS,
            )
            described = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/assist",
                json={"instruction": "What is in this Pack?"},
                headers=CSRF_HEADERS,
            )
            saved = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/save",
                json={"expected_revision": completed.json()["draft"]["revision"]},
                headers=CSRF_HEADERS,
            )
            forked = await client.post(
                f"/api/v1/harness/packs/builder/private/{saved.json()['id']}/drafts",
                json={},
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    conversation_count = (await db_session.execute(select(func.count()).select_from(Conversation))).scalar_one()
    message_count = (await db_session.execute(select(func.count()).select_from(ConversationMessage))).scalar_one()
    assert created.status_code == 201
    assert created.json()["conversation_id"] is None
    assert "discovery_context" not in created.json()
    assert vague.status_code == 200
    assert vague.json()["draft"]["revision"] == created.json()["revision"]
    assert "analysis goal" in vague.json()["message"]
    assert questioned.status_code == 200
    assert questioned.json()["draft"]["artifact"] == {}
    assert questioned.json()["message"] == "Who will use this Pack?"
    assert completed.status_code == 200
    assert completed.json()["draft"]["validation"]["valid"] is True
    assert "Create a supplier disruption Pack" in calls[1][1]["content"]
    assert "Who will use this Pack?" in calls[1][1]["content"]
    assert described.status_code == 200
    assert described.json()["draft"]["revision"] == completed.json()["draft"]["revision"]
    assert "Cold-Chain Decisions" in described.json()["message"]
    assert len(calls) == 2
    assert saved.status_code == 201
    assert forked.status_code == 201
    assert forked.json()["conversation_id"] is None
    assert conversation_count == 0
    assert message_count == 0


async def test_private_pack_attachment_api_uploads_mutates_deletes_and_forks(db_session) -> None:
    user = User(username="pack-reference-api", email="pack-reference-api@example.com", is_superadmin=True)
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Reference Pack")
    fork_conversation = Conversation(user_id=user.id, title="Fork Reference Pack")
    db_session.add_all([conversation, fork_conversation])
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            draft = await client.post(
                "/api/v1/harness/packs/builder/drafts",
                json={"conversation_id": conversation.id}, headers=CSRF_HEADERS,
            )
            draft_id = draft.json()["id"]
            uploaded = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/attachments",
                files={"file": ("supplier.txt", b"Supplier A is delayed.", "text/plain")},
                data={"purpose": "Supplier status"}, headers=CSRF_HEADERS,
            )
            attachment_id = uploaded.json()["id"]
            changed = await client.patch(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/attachments/{attachment_id}",
                json={"purpose": "Current supplier status"}, headers=CSRF_HEADERS,
            )
            content = await client.get(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/attachments/{attachment_id}/content"
            )
            hydrated = await client.get(f"/api/v1/harness/packs/builder/drafts/{draft_id}")
            removed = await client.delete(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/attachments/{attachment_id}",
                headers=CSRF_HEADERS,
            )
            replacement = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/attachments",
                files={"file": ("supplier.txt", b"Supplier A is delayed.", "text/plain")},
                data={"purpose": "Supplier status"}, headers=CSRF_HEADERS,
            )
            artifact = await client.patch(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}",
                json={"expected_revision": draft.json()["revision"], "patch": _supply_chain_analysis_pack()},
                headers=CSRF_HEADERS,
            )
            saved = await client.post(
                f"/api/v1/harness/packs/builder/drafts/{draft_id}/save",
                json={"expected_revision": artifact.json()["revision"]}, headers=CSRF_HEADERS,
            )
            forked = await client.post(
                f"/api/v1/harness/packs/builder/private/{saved.json()['id']}/drafts",
                json={"conversation_id": fork_conversation.id}, headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert draft.status_code == 201
    assert uploaded.status_code == 201
    assert changed.json()["purpose"] == "Current supplier status"
    assert content.content == b"Supplier A is delayed."
    assert content.headers["content-disposition"] == 'inline; filename="supplier.txt"'
    assert content.headers["x-content-type-options"] == "nosniff"
    assert hydrated.json()["attachments"][0]["purpose"] == "Current supplier status"
    assert removed.status_code == 204
    assert replacement.status_code == 201
    assert saved.status_code == 201
    assert saved.json()["attachments"][0]["purpose"] == "Supplier status"
    assert forked.status_code == 201
    assert forked.json()["attachments"][0]["sha256"] == replacement.json()["sha256"]
