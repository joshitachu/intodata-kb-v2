"""Tests for project management REST API endpoints."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select


def test_opencode_generation_bypasses_partition_orchestration(monkeypatch):
    from mcp_server.api.routes.projects import _should_partition_generation
    from mcp_server.config import config

    monkeypatch.setattr(config, "doc_generation_provider", "custom")
    assert _should_partition_generation("opencode-cli") is False


def test_default_non_opencode_generation_retains_partition_orchestration(monkeypatch):
    from mcp_server.api.routes.projects import _should_partition_generation
    from mcp_server.config import config

    monkeypatch.setattr(config, "doc_generation_provider", "custom")
    assert _should_partition_generation(None) is True


def test_default_opencode_generation_bypasses_partition_orchestration(monkeypatch):
    from mcp_server.api.routes.projects import _should_partition_generation
    from mcp_server.config import config

    monkeypatch.setattr(config, "doc_generation_provider", "opencode-cli")
    assert _should_partition_generation(None) is False


def test_component_flow_direction_distinguishes_route_inputs_and_outputs():
    from mcp_server.api.routes.projects import _component_flow_direction

    job_data = {
        "connections": [
            {"source": "queueIn", "target": "processor"},
            {"source": "processor", "target": "queueOut"},
        ],
    }

    assert _component_flow_direction(job_data, "queueIn") == "input"
    assert _component_flow_direction(job_data, "queueOut") == "output"
    assert _component_flow_direction(job_data, "processor") == "both"
    assert _component_flow_direction(job_data, "missing") == "unknown"


def test_component_resource_direction_uses_talend_component_semantics():
    from mcp_server.api.routes.projects import _component_resource_direction

    assert _component_resource_direction("tMSSqlInput") == "input"
    assert _component_resource_direction("tFileOutputDelimited") == "output"
    assert _component_resource_direction("tDBRow") == "unknown"


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


async def test_generate_dispatch_preserves_governed_stack_contract(client, monkeypatch):
    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.schemas import (
        NexusPackSnapshot,
        NexusPresentation,
        compute_nexus_snapshot_hash,
    )

    await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": "governed-dispatch", "platform": "talend"},
    )
    presentation = NexusPresentation.model_validate({
        "audience": "operations",
        "tone": "concise",
        "formatting": ["markdown", "tables"],
        "communication": ["qualify_uncertainty"],
    })
    snapshot = NexusPackSnapshot.model_validate({
        "policy_id": "org.default",
        "policy_hash": "a" * 64,
        "foundation_pack_id": "foundation.core",
        "functional_pack_ids": ["feature.operations"],
        "style_pack_ids": ["style.operations"],
        "resolved_packs": [
            {"id": "foundation.core", "version": "1.0.0", "content_hash": "b" * 64},
            {"id": "feature.operations", "version": "1.0.0", "content_hash": "c" * 64},
            {"id": "style.operations", "version": "1.0.0", "content_hash": "d" * 64},
        ],
        "snapshot_hash": "0" * 64,
    })
    snapshot = snapshot.model_copy(
        update={"snapshot_hash": compute_nexus_snapshot_hash(snapshot, presentation)}
    )
    captured: dict[str, object] = {}

    async def fake_background(_project_id, _run_id, _log_mgr, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(projects_mod, "_run_generate_background", fake_background)
    response = await client.post(
        "/mcp/generate",
        json={
            "project_id": "governed-dispatch",
            "doc_generation_provider": "opencode-cli",
            "nexus_pack_ids": [
                "style.operations",
                "foundation.core",
                "feature.operations",
            ],
            "nexus_pack_snapshot": snapshot.model_dump(mode="json"),
            "nexus_presentation": presentation.model_dump(mode="json"),
        },
    )
    projects_mod._currently_generating.discard("governed-dispatch")

    assert response.status_code == 200
    assert isinstance(response.json()["run_id"], int)
    assert captured["nexus_pack_ids"] == [
        "foundation.core",
        "feature.operations",
        "style.operations",
    ]
    assert captured["nexus_pack_snapshot"] == snapshot
    assert captured["nexus_presentation"] == presentation
    assert captured["doc_generation_provider"] == "opencode-cli"


# --- Pack audit record on GenerateRun (one-pack-program.md section 9) ---


async def _dispatch_generate(client, monkeypatch, project_id: str, payload: dict):
    """POST /mcp/generate with the background task stubbed out; return run_id."""
    import mcp_server.api.routes.projects as projects_mod

    async def fake_background(_project_id, _run_id, _log_mgr, **_kwargs):
        return None

    monkeypatch.setattr(projects_mod, "_run_generate_background", fake_background)
    response = await client.post(
        "/mcp/generate", json={"project_id": project_id, **payload}
    )
    projects_mod._currently_generating.discard(project_id)
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


async def test_generate_records_pack_audit_from_snapshot(client, monkeypatch):
    """Identity is derived from the hash-verified snapshot; actor from the request."""
    import datetime

    from mcp_server.db.models import GenerateRun
    from mcp_server.schemas import NexusPackSnapshot, compute_nexus_snapshot_hash

    await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": "pack-audit", "platform": "talend"},
    )
    snapshot = NexusPackSnapshot.model_validate({
        "policy_id": "org.default",
        "policy_hash": "a" * 64,
        "foundation_pack_id": "foundation.core",
        "functional_pack_ids": ["user.42.sql-review"],
        "style_pack_ids": [],
        "resolved_packs": [
            {"id": "foundation.core", "version": "1.0.0", "content_hash": "b" * 64},
            {"id": "user.42.sql-review", "version": "0.3.0", "content_hash": "c" * 64},
        ],
        "snapshot_hash": "0" * 64,
    })
    snapshot = snapshot.model_copy(
        update={"snapshot_hash": compute_nexus_snapshot_hash(snapshot, None)}
    )
    activated_at = datetime.datetime(2026, 8, 20, 9, 30, tzinfo=datetime.timezone.utc)

    run_id = await _dispatch_generate(
        client,
        monkeypatch,
        "pack-audit",
        {
            "nexus_pack_snapshot": snapshot.model_dump(mode="json"),
            # Deliberately WRONG duplicates: the snapshot must win.
            "pack_id": "spoofed.pack",
            "pack_version": "9.9.9",
            "pack_content_hash": "d" * 64,
            "pack_activated_by": "user:42",
            "pack_activated_at": activated_at.isoformat(),
        },
    )

    import mcp_server.api.routes.projects as projects_mod

    async with projects_mod.async_session_factory() as session:
        run = (
            await session.execute(select(GenerateRun).where(GenerateRun.id == run_id))
        ).scalar_one()
        assert run.pack_id == "user.42.sql-review"
        assert run.pack_version == "0.3.0"
        assert run.pack_content_hash == "c" * 64
        assert run.pack_activated_by == "user:42"
        assert run.pack_activated_at is not None


async def test_generate_records_explicit_pack_audit_without_snapshot(client, monkeypatch):
    """No snapshot: the explicit request fields are the fallback identity source."""
    from mcp_server.db.models import GenerateRun

    await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": "pack-audit-explicit", "platform": "talend"},
    )
    run_id = await _dispatch_generate(
        client,
        monkeypatch,
        "pack-audit-explicit",
        {
            "pack_id": "user.42.sql-review",
            "pack_version": "0.3.0",
            "pack_content_hash": "e" * 64,
            "pack_activated_by": "user:42",
            "pack_activated_at": "2026-08-20T09:30:00+00:00",
        },
    )

    import mcp_server.api.routes.projects as projects_mod

    async with projects_mod.async_session_factory() as session:
        run = (
            await session.execute(select(GenerateRun).where(GenerateRun.id == run_id))
        ).scalar_one()
        assert run.pack_id == "user.42.sql-review"
        assert run.pack_version == "0.3.0"
        assert run.pack_content_hash == "e" * 64
        assert run.pack_activated_by == "user:42"
        assert run.pack_activated_at is not None


async def test_generate_without_pack_identity_leaves_audit_fields_null(client, monkeypatch):
    from mcp_server.db.models import GenerateRun

    await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": "pack-audit-none", "platform": "talend"},
    )
    run_id = await _dispatch_generate(client, monkeypatch, "pack-audit-none", {})

    import mcp_server.api.routes.projects as projects_mod

    async with projects_mod.async_session_factory() as session:
        run = (
            await session.execute(select(GenerateRun).where(GenerateRun.id == run_id))
        ).scalar_one()
        assert run.pack_id is None
        assert run.pack_version is None
        assert run.pack_content_hash is None
        assert run.pack_activated_by is None
        assert run.pack_activated_at is None


async def test_generate_records_resolved_plugin_pack_identity(
    client, monkeypatch, session_factory, tmp_path
):
    """The selected central PluginPack and its immutable hash are the run receipt."""
    from mcp_server.api.routes import projects as projects_mod
    from mcp_server.db.models import GenerateRun, PluginPack
    from mcp_server.services.pack_governance import PackGovernanceDecision

    await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": "plugin-pack-audit", "platform": "talend"},
    )
    pack_dir = tmp_path / "audit-pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        "name: audit-pack\nversion: 2.4.0\nsteps:\n  - id: generate-docs\n    type: harness\n",
        encoding="utf-8",
    )
    async with session_factory() as session:
        pack = PluginPack(
            name="Audit Pack",
            manifest_name="audit-pack",
            version="2.4.0",
            path=str(pack_dir),
            owner_user_id=42,
            auto_update=False,
            origin="personal",
            certification_status="personal",
            content_sha256="f" * 64,
        )
        session.add(pack)
        await session.commit()
        pack_id = pack.id

    async def allow_pack(_pack):
        return PackGovernanceDecision(True, False, "personal", True)

    captured: dict = {}

    async def fake_background(_project_id, _run_id, _log_mgr, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        projects_mod.pack_governance, "require_generation_authority", allow_pack
    )
    monkeypatch.setattr(projects_mod, "_run_generate_background", fake_background)
    response = await client.post(
        "/mcp/generate",
        json={"project_id": "plugin-pack-audit", "plugin_pack_id": pack_id},
    )
    projects_mod._currently_generating.discard("plugin-pack-audit")

    assert response.status_code == 200, response.text
    assert captured["plugin_pack_id"] == pack_id
    async with session_factory() as session:
        run = (
            await session.execute(
                select(GenerateRun).where(GenerateRun.id == response.json()["run_id"])
            )
        ).scalar_one()
        assert run.pack_id == str(pack_id)
        assert run.pack_version == "2.4.0"
        assert run.pack_content_hash == "f" * 64
        assert run.pack_activated_at is not None

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SECURITY_REVIEW.md").write_text("# Review\n", encoding="utf-8")
    await projects_mod._write_plugin_pack_execution_receipt(
        staging,
        run_id=response.json()["run_id"],
        plugin_pack_id=pack_id,
        model_name="gpt-5.6-luna",
        write_model_name="gpt-5.6-luna",
    )
    receipt = json.loads(
        (staging / "_audit" / "plugin_pack_execution_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["plugin_pack"]["id"] == pack_id
    assert receipt["plugin_pack"]["manifest_name"] == "audit-pack"
    assert receipt["models"] == {
        "analysis": "gpt-5.6-luna",
        "writing": "gpt-5.6-luna",
    }
    assert receipt["generated_documents"] == ["SECURITY_REVIEW.md"]


async def test_pack_audit_column_migration_is_idempotent(engine):
    """Running the startup migration twice against a live table is a no-op."""
    from sqlalchemy import inspect as sa_inspect

    from mcp_server.api.app import (
        _GENERATE_RUN_PACK_AUDIT_COLUMNS,
        _apply_column_migrations,
    )

    expected = {name for name, _ in _GENERATE_RUN_PACK_AUDIT_COLUMNS}

    def _columns(sync_conn):
        return {c["name"] for c in sa_inspect(sync_conn).get_columns("generate_runs")}

    async with engine.begin() as conn:
        # create_all already produced the columns; the guard must skip them.
        assert expected <= await conn.run_sync(_columns)
        await conn.run_sync(_apply_column_migrations)
        await conn.run_sync(_apply_column_migrations)
        after = await conn.run_sync(_columns)

    assert expected <= after


async def test_pack_audit_column_migration_adds_missing_columns(engine):
    """The migration is the path for deployments that never ran alembic."""
    from sqlalchemy import inspect as sa_inspect, text

    from mcp_server.api.app import (
        _GENERATE_RUN_PACK_AUDIT_COLUMNS,
        _apply_column_migrations,
    )

    expected = [name for name, _ in _GENERATE_RUN_PACK_AUDIT_COLUMNS]

    def _columns(sync_conn):
        return {c["name"] for c in sa_inspect(sync_conn).get_columns("generate_runs")}

    async with engine.begin() as conn:
        # SQLite cannot DROP COLUMN on older versions; rebuild a legacy-shaped
        # table instead so the ADD COLUMN branch is genuinely exercised.
        await conn.execute(text("DROP TABLE generate_runs"))
        await conn.execute(
            text(
                "CREATE TABLE generate_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "project_id VARCHAR(100) NOT NULL, "
                "triggered_by VARCHAR(40) NOT NULL, "
                "status VARCHAR(20))"
            )
        )
        assert not (set(expected) & await conn.run_sync(_columns))
        await conn.run_sync(_apply_column_migrations)
        added = await conn.run_sync(_columns)
        await conn.run_sync(_apply_column_migrations)
        again = await conn.run_sync(_columns)

    assert set(expected) <= added
    assert added == again
    assert "partition_name" in added


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
    assert data["upload_receipt"]["filename"] == "test.zip"
    assert data["upload_receipt"]["size_bytes"] > 0
    assert len(data["upload_receipt"]["sha256"]) == 64


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


async def test_upload_zip_rejects_oversized_archive(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "max_upload_zip_bytes", 8)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "larger than eight bytes")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("large.zip", buf, "application/zip")},
        data={"project_id": "largezip", "platform": "talend"},
    )

    assert resp.status_code == 413


async def test_upload_zip_rejects_duplicate_project_before_streaming(client, tmp_path):
    """Fast-fail: a conflicting project_id is rejected before any bytes stream."""
    from mcp_server.config import config

    first_buf = io.BytesIO()
    with zipfile.ZipFile(first_buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    first_buf.seek(0)
    created = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", first_buf, "application/zip")},
        data={"project_id": "dupe-project", "platform": "talend"},
    )
    assert created.status_code == 200

    uploads_before = list(config.uploads_dir.iterdir())

    second_buf = io.BytesIO()
    with zipfile.ZipFile(second_buf, "w") as zf:
        zf.writestr("readme.txt", "world")
    second_buf.seek(0)
    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", second_buf, "application/zip")},
        data={"project_id": "dupe-project", "platform": "talend"},
    )

    assert resp.status_code == 409
    # No new temp upload was written for the rejected, conflicting request.
    uploads_after = list(config.uploads_dir.iterdir())
    assert uploads_after == uploads_before


class _FakeUploadFile:
    """Minimal async-read stand-in for Starlette's UploadFile.

    Lets tests exercise the chunked-read loop precisely (call count, partial
    reads) without depending on multipart/HTTP internals.
    """

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)
        self.read_calls: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        return self._buffer.read(size)


async def test_stream_upload_to_temp_file_chunks_and_hashes_correctly(tmp_path):
    import hashlib

    from mcp_server.api.routes.projects import _stream_upload_to_temp_file

    payload = bytes(range(256)) * 50  # 12_800 bytes
    fake_file = _FakeUploadFile(payload)

    path, checksum, size = await _stream_upload_to_temp_file(
        fake_file, tmp_path / "uploads", max_bytes=len(payload) + 1, chunk_bytes=1000
    )

    assert size == len(payload)
    assert checksum == hashlib.sha256(payload).hexdigest()
    assert path.read_bytes() == payload
    # Multiple chunked reads occurred -- never a single full-body read.
    assert len(fake_file.read_calls) >= 12


async def test_stream_upload_to_temp_file_aborts_inline_when_over_limit(tmp_path):
    from mcp_server.api.routes.projects import _stream_upload_to_temp_file

    payload = bytes(range(256)) * 50  # 12_800 bytes, well over the limit below
    fake_file = _FakeUploadFile(payload)
    dest = tmp_path / "uploads"

    with pytest.raises(Exception) as exc_info:
        await _stream_upload_to_temp_file(fake_file, dest, max_bytes=1000, chunk_bytes=1000)

    assert getattr(exc_info.value, "status_code", None) == 413
    # Aborted mid-stream: did not read the whole 13-chunk payload.
    assert len(fake_file.read_calls) < 13
    # No partial temp file left behind.
    assert list(dest.iterdir()) == []


async def test_upload_zip_rejects_unsafe_member_path(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../outside.txt", "unsafe")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("unsafe.zip", buf, "application/zip")},
        data={"project_id": "unsafezip", "platform": "talend"},
    )

    assert resp.status_code == 400
    assert "unsafe" in resp.json()["detail"].lower()


def test_nested_zip_bundle_expands_deterministically(tmp_path):
    """Nested export archives are retained and expanded without flattening."""
    from mcp_server.api.routes.projects import _extract_zip_bundle

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("PROJECT/process/Job_0.1.item", "synthetic")
        zf.writestr("__MACOSX/._ignored", "noise")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("exports/project.zip", nested.getvalue())

    target = tmp_path / "expanded"
    target.mkdir()
    _extract_zip_bundle(outer, target)

    assert (target / "exports" / "project.zip").exists()
    assert (target / "exports" / "project.__intodoc_zip__" / "PROJECT" / "process" / "Job_0.1.item").exists()
    assert not (target / "exports" / "project.__intodoc_zip__" / "__MACOSX").exists()


def test_nested_zip_bundle_enforces_cumulative_entry_limit(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from mcp_server.api.routes.projects import _extract_zip_bundle
    from mcp_server.config import config

    monkeypatch.setattr(config, "max_upload_zip_entries", 2)
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("one.txt", "1")
        zf.writestr("two.txt", "2")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("nested.zip", nested.getvalue())
    target = tmp_path / "expanded"
    target.mkdir()

    with pytest.raises(HTTPException, match="too many"):
        _extract_zip_bundle(outer, target)


def test_nested_zip_bundle_excludes_macos_noise_from_cumulative_limits(tmp_path, monkeypatch):
    from mcp_server.api.routes.projects import _extract_zip_bundle
    from mcp_server.config import config

    monkeypatch.setattr(config, "max_upload_zip_entries", 2)
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("PROJECT/process/Job_0.1.item", "synthetic")
        zf.writestr("__MACOSX/._PROJECT", "x" * 256)
        zf.writestr("__MACOSX/PROJECT/process/._Job_0.1.item", "x" * 256)
    nested_payload = nested.getvalue()
    monkeypatch.setattr(
        config,
        "max_upload_zip_uncompressed_bytes",
        len(nested_payload) + len("synthetic"),
    )
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("project.zip", nested_payload)
    target = tmp_path / "expanded"
    target.mkdir()

    _extract_zip_bundle(outer, target)

    expanded = target / "project.__intodoc_zip__"
    assert (expanded / "PROJECT" / "process" / "Job_0.1.item").exists()
    assert not (expanded / "__MACOSX").exists()


def test_nested_zip_bundle_enforces_depth_and_cumulative_bytes(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from mcp_server.api.routes.projects import _extract_zip_bundle
    from mcp_server.config import config

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("payload.txt", "x" * 32)
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("nested.zip", payload.getvalue())
    target = tmp_path / "expanded"
    target.mkdir()
    # Outer member (the nested ZIP bytes) is permitted; its extracted member
    # pushes the cumulative total over the limit.
    monkeypatch.setattr(config, "max_upload_zip_uncompressed_bytes", len(payload.getvalue()) + 31)
    with pytest.raises(HTTPException, match="expands"):
        _extract_zip_bundle(outer, target)

    # Build a two-level nested archive and make a single nested level illegal.
    innermost = io.BytesIO()
    with zipfile.ZipFile(innermost, "w") as zf:
        zf.writestr("ok.txt", "ok")
    middle = io.BytesIO()
    with zipfile.ZipFile(middle, "w") as zf:
        zf.writestr("inner.zip", innermost.getvalue())
    depth_outer = tmp_path / "depth.zip"
    with zipfile.ZipFile(depth_outer, "w") as zf:
        zf.writestr("middle.zip", middle.getvalue())
    monkeypatch.setattr(config, "max_upload_zip_uncompressed_bytes", 1_000_000)
    monkeypatch.setattr(config, "max_upload_zip_nesting_depth", 1)
    with pytest.raises(HTTPException, match="depth"):
        _extract_zip_bundle(depth_outer, tmp_path / "depth-expanded")


def test_nested_zip_bundle_rejects_unsafe_nested_windows_path(tmp_path):
    from fastapi import HTTPException
    from mcp_server.api.routes.projects import _extract_zip_bundle

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("..\\escape.txt", "unsafe")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("nested.zip", nested.getvalue())
    target = tmp_path / "expanded"
    target.mkdir()
    with pytest.raises(HTTPException, match="unsafe"):
        _extract_zip_bundle(outer, target)


def test_nested_zip_marker_collision_uses_deterministic_fallback(tmp_path):
    from mcp_server.api.routes.projects import _extract_zip_bundle

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("PROJECT/process/Job_0.1.item", "synthetic")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("exports/project.zip", nested.getvalue())
        zf.writestr("exports/project.__intodoc_zip__/user-file.txt", "keep")
    target = tmp_path / "expanded"
    target.mkdir()
    _extract_zip_bundle(outer, target)

    fallback = next((target / "exports").glob("project.__intodoc_zip__-*"))
    assert (fallback / "PROJECT" / "process" / "Job_0.1.item").exists()
    assert (target / "exports" / "project.__intodoc_zip__" / "user-file.txt").exists()


def test_qualified_composite_job_id_round_trips_through_job_loader(tmp_path, monkeypatch):
    from mcp_server.api.routes.projects import _load_job_scan
    from mcp_server.config import config

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path)
    job_id = "exports_first_SYNTH_Shared-0123456789ab"
    relative = "jobs/exports_first-abcdef/Shared-0123456789ab.json"
    scan_dir = tmp_path / "composite" / "scan"
    job_path = scan_dir / relative
    job_path.parent.mkdir(parents=True)
    job_path.write_text(json.dumps({"name": "Shared", "job_id": job_id}), encoding="utf-8")
    (scan_dir / "manifest.json").write_text(
        json.dumps({
            "job_groups": {job_id: "exports/first/SYNTH/Jobs"},
            "job_files": {job_id: relative},
        }),
        encoding="utf-8",
    )

    group, scan_data, resolved_path = _load_job_scan("composite", job_id)

    assert group == "exports/first/SYNTH/Jobs"
    assert scan_data["job_id"] == job_id
    assert resolved_path == job_path


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
    data = resp.json()
    assert data["total"] == 1
    assert data["projects"][0]["plugin_pack_id"] is None


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


async def test_reset_status_cancels_live_generation_task(client, session_factory):
    from mcp_server.api.routes import projects as project_routes
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "cancelproj"
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id=project_id,
            local_path="/tmp/cancelproj",
            platform="generic",
            source_type="zip",
        )
        await registry.update_status(project_id, "generating")
        run = await registry.create_generate_run(project_id, triggered_by="api")
        await session.commit()

    task = asyncio.create_task(asyncio.sleep(60))
    project_routes._generation_tasks[project_id] = task
    project_routes._currently_generating.add(project_id)

    resp = await client.post("/mcp/reset-status", json={"project_id": project_id})

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert task.cancelled() or task.cancelling()
    assert project_id not in project_routes._generation_tasks
    assert project_id not in project_routes._currently_generating

    async with session_factory() as session:
        from mcp_server.db.models import GenerateRun, Project

        project = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one()
        saved_run = (await session.execute(select(GenerateRun).where(GenerateRun.id == run.id))).scalar_one()
        assert project.status == "error"
        assert project.error_message == "Generation cancelled by user"
        assert saved_run.status == "cancelled"
        assert saved_run.error_message == "Generation cancelled by user"
        assert saved_run.run_verdict == "cancelled"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_scan_repairs_stale_managed_repo_path(client, session_factory, tmp_path):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = config.repos_dir / "nexus"
    repo.mkdir(parents=True)
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id="nexus",
            local_path="/home/intodata/intodata-kb/knowledge_base/data/repos/nexus",
            platform="generic",
            source_type="zip",
        )
        await session.commit()

    with patch(
        "mcp_server.api.routes.projects.ScannerService.run_scan",
        new=AsyncMock(
            return_value={
                "success": True,
                "job_count": 0,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "output"),
            }
        ),
    ) as run_scan:
        resp = await client.post("/mcp/scan", json={"project_id": "nexus"})

    assert resp.status_code == 200
    run_scan.assert_awaited_once()
    assert run_scan.await_args.kwargs["repo_path"] == str(repo)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.get_project("nexus")
        assert project is not None
        assert project.local_path == str(repo)


async def test_scan_rejects_second_request_while_output_claimed(client, session_factory, tmp_path):
    """The API rejects a second scan before it can touch the shared output path."""
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry
    from mcp_server.services.scan_output_lock import acquire_scan_output_lock

    repo = tmp_path / "repo"
    repo.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project("locked-scan", str(repo), platform="talend")
        await session.commit()

    lock = acquire_scan_output_lock(config.intodoc_output_dir / "locked-scan")
    assert lock is not None
    try:
        response = await client.post("/mcp/scan", json={"project_id": "locked-scan"})
    finally:
        lock.release()

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


async def test_reingestion_readiness_repair_cleans_remote_without_workflow_side_effects(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://user:credential@example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    output_dir = config.project_root / "output" / "gitproj"
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "group.md").write_text("analysis", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "gitproj",
            str(repo),
            repo_url="https://user:credential@example.com/acme/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()

    report_resp = await client.get("/mcp/projects/gitproj/reingestion-readiness")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["status"] == "needs_remote_repair"
    assert report["origin_had_credentials"] is True
    assert report["repo_url_had_credentials"] is True
    assert report["has_docs"] is True
    assert report["has_rag_support"] is True
    assert report["rag_support_analysis_count"] == 1
    assert report["analysis_cache_count"] == 1
    assert report["targeted_regen_ready"] is True
    assert report["requires_full_rebaseline"] is False
    assert "user:credential@" not in json.dumps(report)

    repair_resp = await client.post("/mcp/projects/gitproj/reingestion-readiness/repair")
    assert repair_resp.status_code == 200
    repaired = repair_resp.json()
    assert repaired["repaired"] is True
    assert repaired["origin_had_credentials"] is False
    assert repaired["repo_url_had_credentials"] is False
    assert repaired["ready"] is True
    assert repaired["targeted_regen_ready"] is True
    assert repaired["requires_full_rebaseline"] is False
    assert repaired["reason_codes"] == []
    assert "user:credential@" not in json.dumps(repaired)

    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert origin == "https://example.com/acme/repo.git"

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("gitproj")
        assert project is not None
        assert project.repo_url == "https://example.com/acme/repo.git"
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.last_scan_at is None
        assert project.last_generate_at is None
        assert project.last_rag_push_at is None
        assert project.git_update_summary["action"] == "reingestion_readiness_repair"
        assert project.git_update_summary["targeted_regen_ready"] is True
        assert project.git_update_summary["requires_full_rebaseline"] is False


async def test_reingestion_readiness_reports_full_rebaseline_for_missing_support(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = tmp_path / "repo-missing-support"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    output_dir = config.project_root / "output" / "gitproj-cache-gap"
    docs = output_dir / "docs"
    cache = output_dir / "analysis_cache"
    docs.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "gitproj-cache-gap",
            str(repo),
            repo_url="https://example.com/acme/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()

    resp = await client.get("/mcp/projects/gitproj-cache-gap/reingestion-readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "requires_full_rebaseline"
    assert data["ready"] is False
    assert data["has_docs"] is True
    assert data["has_analysis_cache"] is True
    assert data["has_rag_support"] is False
    assert data["targeted_regen_ready"] is False
    assert data["requires_full_rebaseline"] is True
    assert data["reason_codes"] == ["missing_rag_support"]


async def test_reingestion_status_aggregates_operator_evidence_without_workflow_side_effects(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "status-operator"
    repo = tmp_path / "repo-status"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/status.git"],
        cwd=repo,
        check=True,
    )

    output_dir = config.project_root / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "group.md").write_text("analysis", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    before_sha = "a" * 40
    after_sha = "b" * 40
    summary = {
        "action": "failed",
        "outcome": "failed",
        "reason_code": "rag_sync_failed",
        "reason": "RAG push failed: rag unavailable",
        "error": "RAG push failed: rag unavailable",
        "before": before_sha,
        "after": after_sha,
        "decision_strategy": "bounded_patch",
        "fallback_used": False,
        "targeted_regen_ready": True,
        "requires_full_rebaseline": False,
        "touched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
        "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
        "rag_result": {"pushed": 0, "errors": ["rag unavailable"]},
        "webhook": {
            "event": "push",
            "delivery_id": "delivery-status",
            "repo_url": "https://github.com/acme/status.git",
            "canonical_repo": "https://github.com/acme/status",
            "registered": True,
            "project_id": project_id,
            "status": "queued",
            "observations": ["webhook_received", "project_matched"],
            "before_sha": before_sha,
            "after_sha": after_sha,
        },
    }

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/status.git",
            platform="generic",
            source_type="git",
        )
        project.status = "ready"
        project.github_webhook_id = 42
        project.last_ingested_commit_sha = before_sha
        project.git_update_summary = summary
        await session.commit()

    with (
        patch("mcp_server.api.routes.projects.ScannerService.run_scan", new=AsyncMock()) as scan,
        patch(
            "mcp_server.api.routes.projects.DocGenerationCoordinator.generate_docs",
            new=AsyncMock(),
        ) as generate,
        patch("mcp_server.api.routes.projects.AutoDocUpdateService.run", new=AsyncMock()) as patcher,
        patch("mcp_server.api.routes.projects.RagBridge.push_docs", new=AsyncMock()) as rag_push,
    ):
        resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert data["project_status"] == "ready"
    assert data["last_ingested_commit_sha"] == before_sha
    assert data["webhook"]["registered"] is True
    assert data["webhook"]["status"] == "active"
    assert data["webhook"]["webhook_id"] == 42
    assert data["webhook"]["last_observation"]["event"] == "push"
    assert data["webhook"]["last_observation"]["delivery_id"] == "delivery-status"
    assert data["webhook"]["last_observation"]["before_sha"] == before_sha
    assert data["webhook"]["last_observation"]["after_sha"] == after_sha
    assert data["last_update"]["action"] == "failed"
    assert data["last_update"]["outcome"] == "failed"
    assert data["last_update"]["reason_code"] == "rag_sync_failed"
    assert data["last_update"]["error"] == "RAG push failed: rag unavailable"
    assert data["last_update"]["rag_result"]["errors"] == ["rag unavailable"]
    assert data["remote_hygiene"]["status"] == "ready"
    assert data["remote_hygiene"]["ready"] is True
    assert data["remote_hygiene"]["origin_matches_repo_url"] is True
    assert data["remote_hygiene"]["origin_had_credentials"] is False
    assert data["cache"]["has_last_ingested_commit"] is True
    assert data["cache"]["has_docs"] is True
    assert data["cache"]["has_rag_support"] is True
    assert data["cache"]["has_analysis_cache"] is True
    assert data["cache"]["targeted_regen_ready"] is True
    assert data["cache"]["requires_full_rebaseline"] is False

    scan.assert_not_awaited()
    generate.assert_not_awaited()
    patcher.assert_not_awaited()
    rag_push.assert_not_awaited()

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project(project_id)
        assert project is not None
        assert project.last_ingested_commit_sha == before_sha
        assert project.last_scan_at is None
        assert project.last_generate_at is None
        assert project.last_rag_push_at is None
        assert project.git_update_summary == summary


async def test_reingestion_status_surfaces_held_and_gate_fields(client, session_factory, tmp_path):
    """Slice 1: a held commit exposes additive `held` + `gate` fields."""
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "status-held"
    output_dir = config.project_root / "output" / project_id
    (output_dir / "docs").mkdir(parents=True)
    (output_dir / "docs" / "01_X.md").write_text("# X", encoding="utf-8")

    summary = {
        "trigger": "webhook",
        "mode": "git_reingestion",
        "action": "manual_review",
        "outcome": "manual_review",
        "reason_code": "correctness_gate_failed",
        "reason": "1 changed unit(s) absent from generated docs: job_alpha",
        "manual_review_reason": "1 changed unit(s) absent from generated docs: job_alpha",
        "before": "a" * 40,
        "after": "b" * 40,
        "held": True,
        "gate": {
            "passed": False,
            "evaluated": True,
            "missing_units": ["job_alpha"],
            "empty_analysis_groups": [],
            "failing_groups": ["A"],
            "reason": "1 changed unit(s) absent from generated docs: job_alpha",
        },
    }

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(tmp_path / "repo-held"),
            repo_url="https://github.com/acme/held.git",
            platform="generic",
            source_type="git",
        )
        project.status = "error"
        project.last_ingested_commit_sha = "a" * 40
        project.git_update_summary = summary
        await session.commit()

    resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")

    assert resp.status_code == 200
    data = resp.json()
    last = data["last_update"]
    assert last["held"] is True
    assert last["reason_code"] == "correctness_gate_failed"
    assert last["manual_review_reason"].endswith("job_alpha")
    assert last["gate"]["missing_units"] == ["job_alpha"]
    assert last["gate"]["passed"] is False
    # SHA held at the prior commit.
    assert data["last_ingested_commit_sha"] == "a" * 40


async def test_run_generate_background_gate_fail_skips_swap_and_holds(
    client, session_factory, tmp_path, monkeypatch
):
    """A failed pre-swap gate must not swap docs and must mark error+manual_review."""
    from unittest.mock import MagicMock

    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry
    from mcp_server.services.reingestion_gate import GateOutcome

    project_id = "gate-fail"
    output_dir = config.project_root / "output" / project_id
    staging = output_dir / "docs_staging"
    docs = output_dir / "docs"
    staging.mkdir(parents=True)
    docs.mkdir(parents=True)
    (staging / "01_X.md").write_text("# new staging doc", encoding="utf-8")
    (docs / "01_X.md").write_text("# OLD published doc", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id, str(tmp_path / "repo-gf"), source_type="git"
        )
        run = await registry.create_generate_run(project_id, triggered_by="webhook")
        await session.commit()
        run_id = run.id

    async def fake_generate_docs(*args, **kwargs):
        return {"success": True, "model_name": "stub", "write_model_name": "stub"}

    monkeypatch.setattr(
        projects_mod.DocGenerationCoordinator, "generate_docs", fake_generate_docs
    )
    swap_spy = MagicMock()
    monkeypatch.setattr(projects_mod, "swap_docs", swap_spy)

    def failing_gate(_staging_dir):
        return GateOutcome(
            passed=False, evaluated=True, missing_units=["job_alpha"], failing_groups=["A"],
            reason="1 changed unit(s) absent from generated docs: job_alpha",
        )

    from mcp_server.api.app import app as _app

    projects_mod._currently_generating.add(project_id)
    await projects_mod._run_generate_background(
        project_id, run_id, _app.state.log_manager,
        resume=True, pre_swap_gate=failing_gate,
    )

    swap_spy.assert_not_called()
    # Old published docs untouched; staging preserved for revisit.
    assert (docs / "01_X.md").read_text(encoding="utf-8") == "# OLD published doc"
    assert (staging / "01_X.md").exists()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project(project_id)
        assert project is not None
        assert project.status == "error"
        assert project.error_message.startswith("manual_review:")


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


# --- EP-000 metadata and review endpoints ---


async def _completed_run(session_factory, project_id: str, **kwargs) -> int:
    from mcp_server.db.models import GenerateRun
    from mcp_server.services.project_registry import ProjectRegistry

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        run = await registry.create_generate_run(project_id)
        await registry.complete_generate_run(run.id, status="completed", **kwargs)
        await session.commit()
        result = await session.execute(select(GenerateRun).where(GenerateRun.id == run.id))
        return result.scalar_one().id


async def test_generate_run_compare_includes_models_quality_deltas(client, session_factory):
    from mcp_server.config import config

    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "cmp", "platform": "talend"})
    run_a = await _completed_run(
        session_factory,
        "cmp",
        doc_count=2,
        cost_usd=1.0,
        duration_ms=1000,
        input_tokens=100,
        output_tokens=50,
        model_name="moonshot/kimi-k2.6",
        write_model_name="openai/deepseek-v4-pro",
        quality_score=4.5,
    )
    run_b = await _completed_run(
        session_factory,
        "cmp",
        doc_count=3,
        cost_usd=1.4,
        duration_ms=900,
        input_tokens=140,
        output_tokens=70,
        model_name="openai/gpt-4o",
        write_model_name="anthropic/claude-sonnet-4-6",
        quality_score=4.0,
    )

    logs_dir = config.project_root / "output" / "cmp" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / f"run_metrics_{run_a}.json").write_text(json.dumps({
        "run_id": run_a,
        "analysis_model": "moonshot/kimi-k2.6",
        "write_model": "openai/deepseek-v4-pro",
        "provider": "custom",
        "cost_usd": 1.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "duration_ms": 1000,
        "doc_quality": {"overall": 4.5, "source_alignment": 4.0},
    }), encoding="utf-8")
    (logs_dir / f"run_metrics_{run_b}.json").write_text(json.dumps({
        "run_id": run_b,
        "analysis_model": "openai/gpt-4o",
        "write_model": "anthropic/claude-sonnet-4-6",
        "provider": "custom",
        "cost_usd": 1.4,
        "input_tokens": 140,
        "output_tokens": 70,
        "duration_ms": 900,
        "doc_quality": {"overall": 4.0, "source_alignment": 4.2},
    }), encoding="utf-8")

    resp = await client.get(f"/mcp/generate-runs/cmp/compare?a={run_a}&b={run_b}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_a"]["analysis_model"] == "moonshot/kimi-k2.6"
    assert data["run_b"]["write_model"] == "anthropic/claude-sonnet-4-6"
    assert data["delta"]["cost_usd"] == 0.4
    assert data["delta"]["quality"] == -0.5
    assert data["verdict"] == "FAIL"
    assert data["run_a"]["doc_quality"]["source_alignment"] == 4.0


async def test_generate_run_compare_warns_when_metrics_missing(client, session_factory):
    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "cmp-missing", "platform": "talend"})
    run_a = await _completed_run(session_factory, "cmp-missing", doc_count=1, quality_score=4.2)
    run_b = await _completed_run(session_factory, "cmp-missing", doc_count=1, quality_score=4.1)

    resp = await client.get(f"/mcp/generate-runs/cmp-missing/compare?a={run_a}&b={run_b}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_a"]["metrics_found"] is False
    assert any(check["metric"] == "metrics" and check["result"] == "WARN" for check in data["checks"])


async def test_quality_history_flags_regression(client):
    from mcp_server.config import config

    logs_dir = config.project_root / "output" / "hist" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run_metrics_1.json").write_text(json.dumps({
        "run_id": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "doc_quality": {"overall": 4.5},
    }), encoding="utf-8")
    (logs_dir / "run_metrics_2.json").write_text(json.dumps({
        "run_id": 2,
        "started_at": "2026-01-02T00:00:00Z",
        "doc_quality": {"overall": 4.0},
    }), encoding="utf-8")

    resp = await client.get("/mcp/projects/hist/quality-history")
    assert resp.status_code == 200
    history = resp.json()["history"]
    assert history[1]["regression"] is True
    assert history[1]["quality_delta_pct"] == -11.11


async def test_validation_questionnaire_submit_and_aggregate(client):
    resp = await client.get("/mcp/projects/review-proj/questionnaire?run_id=7")
    assert resp.status_code == 200
    questions = resp.json()["questions"]

    review = {
        "run_id": 7,
        "reviewer": "qa",
        "overall": 8,
        "questions": [{**q, "score": 4} for q in questions],
        "feedback": "ready",
        "reviewed_at": "2026-01-01T00:00:00Z",
    }
    submit = await client.post("/mcp/projects/review-proj/reviews", json=review)
    assert submit.status_code == 201

    aggregate = await client.get("/mcp/projects/review-proj/reviews?run_id=7")
    assert aggregate.status_code == 200
    data = aggregate.json()
    assert data["total_reviews"] == 1
    assert data["average_overall"] == 8.0
    assert data["pass_result"] is True
    assert data["average_per_question"]["sections_present"] == 4.0


async def test_instruction_pack_metadata_endpoint(client):
    from mcp_server.config import config

    pack_dir = config.project_root / "packs" / "default"
    pack_dir.mkdir(parents=True)
    (pack_dir / "version.txt").write_text("v-test", encoding="utf-8")
    (pack_dir / "phase2_preamble.md").write_text("Phase 2", encoding="utf-8")

    resp = await client.get("/mcp/instruction-pack")
    assert resp.status_code == 200
    data = resp.json()
    assert data["relative_path"] == "packs/default"
    assert data["version"] == "v-test"
    assert data["supported"] is True
    assert data["preambles"][0]["key"] == "phase2_preamble"
