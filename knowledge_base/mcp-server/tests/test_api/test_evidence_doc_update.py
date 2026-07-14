from __future__ import annotations

import hashlib
import json
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
    assert data["version_id"] == data["after_sha"]
    assert data["section"] is None
    assert data["revert_path"].endswith(f"/{filename}/evidence-revert")
    assert data["reconciliation_status"] == "indexed"
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

    receipt = json.loads(
        (config.project_root / "output" / project_id / data["receipt_path"]).read_text(
            encoding="ascii"
        )
    )
    assert receipt["receipt_type"] == "evidence_doc_update"
    assert receipt["status"] == "updated"
    assert receipt["reconciliation_status"] == "indexed"


async def test_evidence_update_replaces_named_section_with_optimistic_base_sha(
    client,
    session_factory,
) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    original = (
        "# Runbook\n\nIntro.\n\n## Recovery\n\nOld procedure.\n\n"
        "### Detail\n\nOld detail.\n\n## Alerts\n\nKeep this.\n"
    )
    await _seed_project_with_doc(session_factory, project_id, filename, original)
    expected_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()

    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ):
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={
                "evidence_text": "Run the recovery job at 02:00 UTC.",
                "evidence_refs": ["runtime:exec-1"],
                "section": "## Recovery",
                "expected_base_sha": f"sha256:{expected_sha}",
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["section"] == "Recovery"
    content = (
        config.project_root / "output" / project_id / "docs" / filename
    ).read_text(encoding="utf-8")
    assert "## Recovery\n\nRun the recovery job at 02:00 UTC." in content
    assert "Old procedure." not in content
    assert "Old detail." not in content
    assert "## Alerts\n\nKeep this." in content
    history = DocVersionHistoryRepository(project_id).read_all()
    assert history[0]["sections"] == {filename: ["Recovery"]}


async def test_evidence_update_rejects_stale_expected_base_sha(client, session_factory) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    original = "# Runbook\n\n## Recovery\n\nCurrent.\n"
    await _seed_project_with_doc(session_factory, project_id, filename, original)

    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(),
    ) as push_docs:
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={
                "evidence_text": "Proposed.",
                "section": "Recovery",
                "expected_base_sha": "0" * 64,
            },
        )

    assert response.status_code == 409
    assert "changed since the proposal" in response.json()["detail"]
    push_docs.assert_not_awaited()
    assert (
        config.project_root / "output" / project_id / "docs" / filename
    ).read_text(encoding="utf-8") == original


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
    receipts = list(
        (config.project_root / "output" / project_id / "_harness_doc_updates").glob(
            "*.receipt.json"
        )
    )
    assert len(receipts) == 1
    failure_receipt = json.loads(receipts[0].read_text(encoding="ascii"))
    assert failure_receipt["status"] == "rolled_back"
    assert failure_receipt["reconciliation_status"] == "restored_after_index_failure"


async def test_evidence_revert_restores_prior_content_reindexes_and_records_receipt(
    client,
    session_factory,
) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    original = "# Runbook\n\n## Recovery\n\nOld procedure.\n"
    await _seed_project_with_doc(session_factory, project_id, filename, original)

    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ):
        update_response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={"evidence_text": "New procedure.", "section": "Recovery"},
        )
    update_data = update_response.json()

    with patch(
        "mcp_server.services.evidence_doc_revert.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ) as push_docs:
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-revert",
            json={
                "receipt_path": update_data["receipt_path"],
                "expected_base_sha": update_data["after_sha"],
                "workflow_run_id": "workflow-revert-1",
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "reverted"
    assert data["after_sha"] == update_data["before_sha"]
    assert data["version_id"] == data["after_sha"]
    assert data["reverted_receipt_path"] == update_data["receipt_path"]
    assert data["reconciliation_status"] == "indexed"
    assert (
        config.project_root / "output" / project_id / "docs" / filename
    ).read_text(encoding="utf-8") == original
    push_docs.assert_awaited_once()

    history = DocVersionHistoryRepository(project_id).read_all()
    assert [item["reason_code"] for item in history] == [
        "harness_evidence_update",
        "harness_evidence_revert",
    ]
    revert_receipt = json.loads(
        (config.project_root / "output" / project_id / data["receipt_path"]).read_text(
            encoding="ascii"
        )
    )
    assert revert_receipt["receipt_type"] == "evidence_doc_revert"
    assert revert_receipt["status"] == "reverted"


async def test_evidence_revert_refuses_to_overwrite_later_content(client, session_factory) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    await _seed_project_with_doc(
        session_factory, project_id, filename, "# Runbook\n\n## Recovery\n\nOld.\n"
    )
    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ):
        update_response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={"evidence_text": "Updated.", "section": "Recovery"},
        )
    doc_path = config.project_root / "output" / project_id / "docs" / filename
    doc_path.write_text("# Runbook\n\nLater manual edit.\n", encoding="utf-8")

    with patch(
        "mcp_server.services.evidence_doc_revert.RagBridge.push_docs",
        new=AsyncMock(),
    ) as push_docs:
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-revert",
            json={"receipt_path": update_response.json()["receipt_path"]},
        )

    assert response.status_code == 409
    assert "refusing to overwrite" in response.json()["detail"]
    push_docs.assert_not_awaited()
    assert "Later manual edit" in doc_path.read_text(encoding="utf-8")


async def test_evidence_revert_restores_updated_version_when_reindex_fails(
    client,
    session_factory,
) -> None:
    project_id = "p1"
    filename = "04_RUNBOOK.md"
    original = "# Runbook\n\n## Recovery\n\nOld.\n"
    await _seed_project_with_doc(session_factory, project_id, filename, original)
    with patch(
        "mcp_server.services.evidence_doc_update.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 1, "skipped": 0, "errors": []}),
    ):
        update_response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-update",
            json={"evidence_text": "Updated.", "section": "Recovery"},
        )
    updated = (
        config.project_root / "output" / project_id / "docs" / filename
    ).read_text(encoding="utf-8")

    with patch(
        "mcp_server.services.evidence_doc_revert.RagBridge.push_docs",
        new=AsyncMock(return_value={"pushed": 0, "skipped": 0, "errors": ["rag down"]}),
    ):
        response = await client.post(
            f"/mcp/projects/{project_id}/docs/{filename}/evidence-revert",
            json={"receipt_path": update_response.json()["receipt_path"]},
        )

    assert response.status_code == 502
    assert "revert was rolled back" in response.json()["detail"]
    assert (
        config.project_root / "output" / project_id / "docs" / filename
    ).read_text(encoding="utf-8") == updated
    history = DocVersionHistoryRepository(project_id).read_all()
    assert [item["reason_code"] for item in history] == ["harness_evidence_update"]
    receipts = list(
        (config.project_root / "output" / project_id / "_harness_doc_updates").glob(
            "*.revert.receipt.json"
        )
    )
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="ascii"))
    assert receipt["status"] == "rolled_back"
    assert receipt["reconciliation_status"] == "restored_updated_version_after_index_failure"


async def test_evidence_update_rejects_unsafe_filename(client, session_factory) -> None:
    project_id = "p1"
    await _seed_project_with_doc(session_factory, project_id, "safe.md", "# Safe\n")

    response = await client.post(
        f"/mcp/projects/{project_id}/docs/../secret.md/evidence-update",
        json={"evidence_text": "Evidence"},
    )

    assert response.status_code in {400, 404}
