from __future__ import annotations

from unittest.mock import AsyncMock, patch

from mcp_server.config import config
from mcp_server.services.doc_version_history import DocVersionHistoryRepository
from mcp_server.services.project_registry import ProjectRegistry


async def _seed_project_with_doc(session_factory, project_id: str, filename: str, content: str) -> None:
    async with session_factory() as session:
        await ProjectRegistry(session).add_project(
            project_id,
            str(config.project_root / "repos" / project_id),
            platform="generic",
            source_type="local",
        )
        await session.commit()

    docs_dir = config.project_root / "output" / project_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / filename).write_text(content, encoding="utf-8")


async def test_evidence_update_appends_section_records_receipt_and_pushes_changed_doc(
    client,
    session_factory,
) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    await _seed_project_with_doc(session_factory, project_id, filename, "# Runbook\n\nExisting content.\n")

    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ) as push_docs:
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={
                "evidence_text": "Failure window moved to 02:00 UTC. api_key=secret-value",
                "evidence_refs": ["chat_prompt", "runtime-failure:exec-1"],
                "workflow_run_id": "workflow-123",
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "updated"
    assert data["filename"] == filename
    assert data["workflow_run_id"] == "workflow-123"
    assert data["receipt_path"].endswith(".receipt.json")
    assert data["rag_result"] == {"pushed": 1, "skipped": 0, "errors": []}

    doc_path = config.project_root / "output" / project_id / "docs" / filename
    content = doc_path.read_text(encoding="utf-8")
    assert "## Evidence Update" in content
    assert "Failure window moved to 02:00 UTC." in content
    assert "secret-value" not in content
    assert "workflow-123" in content

    push_docs.assert_awaited_once()
    kwargs = push_docs.await_args.kwargs
    assert kwargs["project_id"] == project_id
    assert kwargs["filenames"] == {filename}
    assert kwargs["force"] is True

    history = DocVersionHistoryRepository(project_id).read_all()
    assert len(history) == 1
    assert history[0]["outcome"] == "bounded_patch"
    assert history[0]["touched_docs"] == [filename]
    assert history[0]["reason_code"] == "harness_evidence_update"


async def test_evidence_update_rolls_back_when_rag_sync_fails(client, session_factory) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    original = "# Runbook\n\nExisting content.\n"
    await _seed_project_with_doc(session_factory, project_id, filename, original)

    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 0, "skipped": 0, "errors": ["rag down"]}),
    ):
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={
                "evidence_text": "This should not persist.",
                "evidence_refs": ["chat_prompt"],
            },
        )

    assert response.status_code == 502
    assert "rolled back" in response.json()["detail"]
    doc_path = config.project_root / "output" / project_id / "docs" / filename
    assert doc_path.read_text(encoding="utf-8") == original
    assert DocVersionHistoryRepository(project_id).read_all() == []


async def test_evidence_update_rejects_unsafe_filename(client, session_factory) -> None:
    project_id = "p1"
    await _seed_project_with_doc(session_factory, project_id, "safe.md", "# Safe\n")

    response = await client.post(
        f"/mcp/projects/{project_id}/docs/../secret.md/evidence-update",
        json={"evidence_text": "Evidence"},
    )

    assert response.status_code in {400, 404}
