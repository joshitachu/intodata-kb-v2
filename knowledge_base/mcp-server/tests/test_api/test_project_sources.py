"""API boundary for additive ZIP sources (POST /projects/{id}/sources).

Adding a second source to an already-scanned project is the whole point of the
incremental sub-project feature, and the risky part is not the upload -- it is
the migration of the pre-existing FLAT tree into a marker directory. If that
migration is wrong, the old tree either disappears or stops being attributable
to a sub-project, and the project's existing documentation is regenerated (or
mis-attributed) instead of reused.
"""

from __future__ import annotations

import io
import json
import zipfile


def _zip_bytes(name: str = "source.txt", content: str = "content") -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    buffer.seek(0)
    return buffer


def _nested_zip_bytes() -> io.BytesIO:
    """An outer archive containing an inner archive (a composite upload)."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("inner.txt", "inner")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("Alpha.zip", inner.getvalue())
    outer.seek(0)
    return outer


async def _create_zip_project(client, project_id: str, payload=None) -> None:
    response = await client.post(
        "/mcp/upload-zip",
        files={"file": ("initial.zip", payload or _zip_bytes(), "application/zip")},
        data={"project_id": project_id, "platform": "talend"},
    )
    assert response.status_code == 200, response.text


def _repo_root(project_id: str):
    from mcp_server.config import config

    return config.repos_dir / project_id


def _marker_dirs(project_id: str) -> list[str]:
    from mcp_server.services.repo_sources import origin_marked_source_dirs

    return sorted(p.name for p in origin_marked_source_dirs(_repo_root(project_id)))


async def test_additive_source_migrates_the_flat_tree_and_adds_a_sibling(client) -> None:
    from mcp_server.services import partition_orchestrator as po

    await _create_zip_project(client, "src-flat")
    root = _repo_root("src-flat")
    assert (root / "source.txt").exists()

    response = await client.post(
        "/mcp/projects/src-flat/sources",
        files={"file": ("Archive.zip", _zip_bytes("new.txt", "new"), "application/zip")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "project_id": "src-flat",
        "subproject": "Archive",
        "migrated_existing_source": True,
        "requires_rescan": True,
        "status": "added",
    }

    # Two sub-project boundaries, and the original bytes travelled with the move.
    assert _marker_dirs("src-flat") == [
        "Archive.__intodoc_repo__",
        "src-flat.__intodoc_repo__",
    ]
    assert (root / "src-flat.__intodoc_repo__" / "source.txt").read_text() == "content"
    assert (root / "Archive.__intodoc_repo__" / "new.txt").read_text() == "new"
    assert not (root / "source.txt").exists()

    # The migration is recorded so the orchestrator can adopt the flat docs.
    state = json.loads(po._partition_state_path("src-flat").read_text())
    assert state[po.STATE_META_KEY]["flat_source_subproject"] == "src-flat"

    # The project's next step is a scan.
    from mcp_server.db.session import async_session_factory
    from mcp_server.services.project_registry import ProjectRegistry

    async with async_session_factory() as db:
        project = await ProjectRegistry(db).get_project("src-flat")
        assert project.status == "registered"


async def test_second_additive_source_does_not_migrate_again(client) -> None:
    await _create_zip_project(client, "src-twice")

    first = await client.post(
        "/mcp/projects/src-twice/sources",
        files={"file": ("Beta.zip", _zip_bytes("b.txt"), "application/zip")},
    )
    second = await client.post(
        "/mcp/projects/src-twice/sources",
        files={"file": ("Gamma.zip", _zip_bytes("g.txt"), "application/zip")},
    )

    assert first.json()["migrated_existing_source"] is True
    assert second.json()["migrated_existing_source"] is False
    assert second.json()["subproject"] == "Gamma"
    assert _marker_dirs("src-twice") == [
        "Beta.__intodoc_repo__",
        "Gamma.__intodoc_repo__",
        "src-twice.__intodoc_repo__",
    ]


async def test_colliding_source_names_are_allocated_distinctly(client) -> None:
    """Two uploads with one filename must not share a sub-project directory."""
    await _create_zip_project(client, "src-collide")

    first = await client.post(
        "/mcp/projects/src-collide/sources",
        files={"file": ("Same.zip", _zip_bytes("one.txt"), "application/zip")},
    )
    second = await client.post(
        "/mcp/projects/src-collide/sources",
        files={"file": ("Same.zip", _zip_bytes("two.txt"), "application/zip")},
    )

    assert first.json()["subproject"] == "Same"
    assert second.json()["subproject"] == "Same_2"
    root = _repo_root("src-collide")
    assert (root / "Same.__intodoc_repo__" / "one.txt").exists()
    assert (root / "Same_2.__intodoc_repo__" / "two.txt").exists()


async def test_an_already_composite_tree_is_left_alone(client) -> None:
    """A nested-archive upload is already partitioned; wrapping it would collapse
    every inner sub-project into one outer marker."""
    await _create_zip_project(client, "src-composite", payload=_nested_zip_bytes())
    assert _marker_dirs("src-composite") == ["Alpha.__intodoc_zip__"]

    response = await client.post(
        "/mcp/projects/src-composite/sources",
        files={"file": ("Delta.zip", _zip_bytes("d.txt"), "application/zip")},
    )

    assert response.json()["migrated_existing_source"] is False
    assert _marker_dirs("src-composite") == [
        "Alpha.__intodoc_zip__",
        "Delta.__intodoc_repo__",
    ]
    root = _repo_root("src-composite")
    assert (root / "Alpha.__intodoc_zip__" / "inner.txt").exists()


async def test_additive_source_rejects_a_git_project(client) -> None:
    from mcp_server.db.session import async_session_factory
    from mcp_server.services.project_registry import ProjectRegistry

    async with async_session_factory() as db:
        await ProjectRegistry(db).add_project(
            project_id="src-git", local_path="/tmp/src-git", source_type="git"
        )
        await db.commit()

    response = await client.post(
        "/mcp/projects/src-git/sources",
        files={"file": ("Delta.zip", _zip_bytes(), "application/zip")},
    )

    assert response.status_code == 400
    assert "ZIP source" in response.json()["detail"]


async def test_additive_source_404s_for_an_unknown_project(client) -> None:
    response = await client.post(
        "/mcp/projects/src-missing/sources",
        files={"file": ("Delta.zip", _zip_bytes(), "application/zip")},
    )
    assert response.status_code == 404


async def test_additive_source_requires_a_zip(client) -> None:
    await _create_zip_project(client, "src-notzip")
    response = await client.post(
        "/mcp/projects/src-notzip/sources",
        files={"file": ("notes.txt", io.BytesIO(b"plain"), "text/plain")},
    )
    assert response.status_code == 400


async def test_additive_source_rejects_an_invalid_archive(client) -> None:
    await _create_zip_project(client, "src-badzip")
    response = await client.post(
        "/mcp/projects/src-badzip/sources",
        files={"file": ("broken.zip", io.BytesIO(b"not a zip"), "application/zip")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid ZIP file"
    # The existing tree is untouched by a rejected upload.
    assert (_repo_root("src-badzip") / "source.txt").exists()


async def test_additive_source_enforces_the_upload_size_limit(client, monkeypatch) -> None:
    from mcp_server.config import config

    await _create_zip_project(client, "src-large")
    monkeypatch.setattr(config, "max_upload_zip_bytes", 8)

    response = await client.post(
        "/mcp/projects/src-large/sources",
        files={
            "file": (
                "big.zip",
                _zip_bytes("big.txt", "definitely larger than eight bytes"),
                "application/zip",
            )
        },
    )

    assert response.status_code == 413
    assert (_repo_root("src-large") / "source.txt").exists()


async def test_additive_source_requires_project_management(client, session_factory) -> None:
    """The mutating path is owner-gated; view access is not enough."""
    from sqlalchemy import text

    await _create_zip_project(client, "src-gated")
    async with session_factory() as db:
        await db.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
        await db.commit()

    response = await client.post(
        "/mcp/projects/src-gated/sources",
        files={"file": ("Delta.zip", _zip_bytes(), "application/zip")},
    )

    assert response.status_code == 403


async def test_upload_zip_still_refuses_an_existing_source_tree(client) -> None:
    """The additive path must not weaken the CREATE path's conflict check."""
    await _create_zip_project(client, "src-guard")

    response = await client.post(
        "/mcp/upload-zip",
        files={"file": ("again.zip", _zip_bytes(), "application/zip")},
        data={"project_id": "src-guard", "platform": "talend"},
    )

    assert response.status_code == 409
