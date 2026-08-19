"""API boundary for safely staging a ZIP replacement."""

from __future__ import annotations

import io
import zipfile


def _zip_bytes(value: str) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("source.txt", value)
    buffer.seek(0)
    return buffer


async def test_replace_zip_stages_a_candidate_and_keeps_active_source(client, tmp_path) -> None:
    from mcp_server.api.app import app
    from mcp_server.config import config
    from mcp_server.services.living_docs.zip_revisions import ZipRevisionService

    created = await client.post(
        "/mcp/upload-zip",
        files={"file": ("initial.zip", _zip_bytes("active"), "application/zip")},
        data={"project_id": "zip-revision", "platform": "talend"},
    )
    assert created.status_code == 200
    active_file = config.repos_dir / "zip-revision" / "source.txt"
    before = active_file.read_text(encoding="utf-8")
    app.state.zip_revision_service = ZipRevisionService(tmp_path / "zip-candidates")

    response = await client.post(
        "/mcp/projects/zip-revision/replace-zip",
        files={"file": ("replacement.zip", _zip_bytes("candidate"), "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "held"
    assert body["reason_code"] == "zip_coordinator_not_configured"
    assert len(body["sha256"]) == 64
    assert active_file.read_text(encoding="utf-8") == before


async def test_replace_zip_rejects_oversized_archive_before_staging(client, monkeypatch) -> None:
    from mcp_server.config import config

    await client.post(
        "/mcp/upload-zip",
        files={"file": ("initial.zip", _zip_bytes("active"), "application/zip")},
        data={"project_id": "zip-revision-oversized", "platform": "talend"},
    )
    uploads_before = list(config.uploads_dir.iterdir())

    monkeypatch.setattr(config, "max_upload_zip_bytes", 8)
    response = await client.post(
        "/mcp/projects/zip-revision-oversized/replace-zip",
        files={"file": ("replacement.zip", _zip_bytes("candidate, more than eight bytes"), "application/zip")},
    )

    assert response.status_code == 413
    # No leftover partial upload from the rejected, oversized request.
    assert list(config.uploads_dir.iterdir()) == uploads_before
