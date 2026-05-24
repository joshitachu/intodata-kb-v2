"""Tests for managed project repository path repair."""

from __future__ import annotations

from mcp_server.db.models import Project
from mcp_server.services.project_paths import (
    repair_project_repo_path,
    repair_managed_project_repo_paths,
)


async def test_repair_project_repo_path_updates_stale_managed_path(monkeypatch, tmp_path):
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    project = Project(
        id="nexus",
        display_name="nexus",
        local_path="/home/intodata/intodata-kb/knowledge_base/data/repos/nexus",
        source_type="zip",
        platform="generic",
        status="registered",
    )

    repaired = await repair_project_repo_path(project)

    assert repaired is True
    assert project.local_path == str(tmp_path / "data" / "repos" / "nexus")


async def test_repair_project_repo_path_leaves_existing_path(monkeypatch, tmp_path):
    from mcp_server.config import config

    repo = tmp_path / "custom" / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    project = Project(
        id="nexus",
        display_name="nexus",
        local_path=str(repo),
        source_type="zip",
        platform="generic",
        status="registered",
    )

    repaired = await repair_project_repo_path(project)

    assert repaired is False
    assert project.local_path == str(repo)


async def test_repair_managed_project_repo_paths_commits_stale_rows(
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id="nexus",
            local_path="/home/intodata/intodata-kb/knowledge_base/data/repos/nexus",
            platform="generic",
            source_type="zip",
        )
        await session.commit()

    repaired = await repair_managed_project_repo_paths(session_factory)

    assert repaired == 1
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.get_project("nexus")
        assert project is not None
        assert project.local_path == str(tmp_path / "data" / "repos" / "nexus")
