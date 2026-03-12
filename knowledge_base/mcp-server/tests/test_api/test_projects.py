"""Tests for project management REST API endpoints."""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest


# --- Add Repo ---


async def test_add_repo_local_path(client, tmp_path):
    # Create a local path that exists
    local = tmp_path / "data" / "repos" / "myproject"
    local.mkdir(parents=True)

    resp = await client.post("/mcp/add-repo", json={
        "url": str(local),
        "project_id": "myproject",
        "platform": "talend",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "myproject"
    assert data["platform"] == "talend"
    assert data["status"] == "registered"


async def test_add_repo_zip_placeholder(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "zipproj",
        "platform": "talend",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "zipproj"
    assert data["status"] == "registered"


async def test_add_repo_invalid_url(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "not_a_valid_url_or_path",
        "project_id": "bad",
        "platform": "talend",
    })
    assert resp.status_code == 400


async def test_add_repo_invalid_project_id_path_traversal(client):
    """Security: project_id with path traversal must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "../etc",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_invalid_project_id_semicolon(client):
    """Security: project_id with shell metacharacters must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "foo;rm",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_invalid_project_id_slash(client):
    """Security: project_id with slash must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "a/b",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_valid_project_id_with_hyphens_underscores(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "valid-project_1",
        "platform": "talend",
    })
    assert resp.status_code == 200
    assert resp.json()["id"] == "valid-project_1"


# --- Upload ZIP ---


async def test_upload_zip_success(client, tmp_path):
    # Create a valid ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", buf, "application/zip")},
        data={"project_id": "ziptest", "platform": "talend"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "ziptest"


async def test_upload_zip_invalid_project_id(client):
    """Security: upload_zip form endpoint validates project_id."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", buf, "application/zip")},
        data={"project_id": "../etc/passwd", "platform": "talend"},
    )
    assert resp.status_code == 400
    assert "Invalid project_id" in resp.json()["detail"]


async def test_upload_zip_not_zip_file(client):
    buf = io.BytesIO(b"not a zip")
    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.txt", buf, "text/plain")},
        data={"project_id": "proj", "platform": "talend"},
    )
    assert resp.status_code == 400


# --- Project List ---


async def test_list_projects_empty(client):
    resp = await client.get("/mcp/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["projects"] == []


async def test_list_projects_returns_added(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "p1", "platform": "talend"
    })
    resp = await client.get("/mcp/projects")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# --- Status ---


async def test_get_status(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "statproj", "platform": "talend"
    })
    resp = await client.get("/mcp/status/statproj")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "statproj"
    assert data["status"] == "registered"


async def test_get_status_not_found(client):
    resp = await client.get("/mcp/status/nonexistent")
    assert resp.status_code == 404


# --- Reset Status ---


async def test_reset_status_not_generating(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "resetproj", "platform": "talend"
    })
    resp = await client.post("/mcp/reset-status", json={"project_id": "resetproj"})
    assert resp.status_code == 400


# --- Logs ---


async def test_list_logs_empty(client):
    resp = await client.get("/mcp/logs/testproj")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


async def test_log_content_path_traversal(client):
    """Security: .. in log filename must be rejected."""
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "logproj", "platform": "talend"
    })
    resp = await client.get("/mcp/logs/logproj/logproj_..evil.log")
    assert resp.status_code == 400
    assert "Invalid filename" in resp.json()["detail"]
