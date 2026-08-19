from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from mcp_server.config import config
from mcp_server.services.project_registry import ProjectRegistry


async def _seed(session_factory, project_id: str = "p1") -> None:
    async with session_factory() as session:
        await ProjectRegistry(session).add_project(
            project_id, str(config.project_root / "repos" / project_id),
            platform="generic", source_type="local",
        )
        await session.commit()
    docs = config.project_root / "output" / project_id / "docs"
    docs.mkdir(parents=True)
    (docs / "04_RUNBOOK.md").write_text("# Runbook\n\n## Recovery\n\nOld.\n", encoding="utf-8")


async def test_chat_patch_preview_returns_hash_bound_approval_card(
    client, session_factory, monkeypatch
) -> None:
    await _seed(session_factory)
    monkeypatch.setattr(config, "living_doc_patching_enabled", True)
    proposal = SimpleNamespace(
        proposal_id="ldp-1", project_id="p1", filename="04_RUNBOOK.md",
        section="Recovery", assertion_kind="editorial",
        base_document_sha256="a" * 64, base_knowledge_revision_id="kr-1",
        evidence_refs=("chat:1",), patch_plan_hash="b" * 64,
        compact_diff="+ proposed overlay", changed_scope=("canonical/x", "rag:x"),
        estimated_input_tokens=10, estimated_output_tokens=4,
        expires_at="2026-08-11T01:00:00+00:00",
    )
    with patch(
        "mcp_server.api.routes.projects.ChatPatchService.preview",
        new=AsyncMock(return_value=proposal),
    ) as preview:
        response = await client.post(
            "/mcp/projects/p1/docs/04_RUNBOOK.md/chat-patch/preview",
            json={"section": "Recovery", "replacement_text": "Use manual fallback.",
                  "evidence_refs": ["chat:1"], "assertion_kind": "editorial"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["patch_plan_hash"] == "b" * 64
    assert response.json()["requires_owner_approval"] is True
    preview.assert_awaited_once()


async def test_editorial_activation_fails_closed_for_non_owner(
    client, session_factory, monkeypatch
) -> None:
    await _seed(session_factory)
    monkeypatch.setattr(config, "living_doc_patching_enabled", True)
    async with session_factory() as session:
        await session.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
        await session.execute(text(
            "INSERT INTO project_assignments (user_id, project_id, is_owner) VALUES (1, 'p1', 0)"
        ))
        await session.commit()
    proposal = SimpleNamespace(
        proposal_id="ldp-1", project_id="p1", filename="04_RUNBOOK.md",
        assertion_kind="editorial",
    )
    with patch(
        "mcp_server.api.routes.projects.ChatPatchService.get_proposal",
        return_value=proposal,
    ), patch(
        "mcp_server.api.routes.projects.ChatPatchService.activate",
        new=AsyncMock(side_effect=AssertionError("unauthorized approval executed")),
    ) as activate:
        response = await client.post(
            "/mcp/projects/p1/docs/04_RUNBOOK.md/chat-patch/activate",
            json={"proposal_id": "ldp-1", "patch_plan_hash": "a" * 64},
        )
    assert response.status_code == 403
    activate.assert_not_awaited()


async def test_source_backed_activation_also_fails_closed_for_non_owner(
    client, session_factory, monkeypatch
) -> None:
    await _seed(session_factory)
    monkeypatch.setattr(config, "living_doc_patching_enabled", True)
    async with session_factory() as session:
        await session.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
        await session.execute(text(
            "INSERT INTO project_assignments (user_id, project_id, is_owner) VALUES (1, 'p1', 0)"
        ))
        await session.commit()
    with patch(
        "mcp_server.api.routes.projects.ChatPatchService.activate",
        new=AsyncMock(side_effect=AssertionError("unauthorized source-backed approval executed")),
    ) as activate:
        response = await client.post(
            "/mcp/projects/p1/docs/04_RUNBOOK.md/chat-patch/activate",
            json={"proposal_id": "ldp-1", "patch_plan_hash": "a" * 64},
        )
    assert response.status_code == 403
    activate.assert_not_awaited()
