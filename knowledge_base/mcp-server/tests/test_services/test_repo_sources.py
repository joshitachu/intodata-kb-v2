"""Multi-repo membership and per-repo git sync.

The sync tests drive REAL ``git`` against local bare repositories over
``file://``. Nothing reaches the network, but the clone/fetch/ff-only paths are
genuinely exercised, which is the point: the incremental promise is a property of
those subprocess calls, not of a mock.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.db.models import Project
from mcp_server.services.repo_sources import (
    PROVIDER_AZURE_DEVOPS,
    MultiRepoSyncService,
    RepoSourceRepository,
    allocate_subproject_name,
    member_clone_path,
    sanitize_subproject_name,
)

# `asyncio_mode = "auto"` in pyproject drives the async tests; no marker needed.


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #

def test_sanitize_keeps_recognisable_repo_names():
    assert sanitize_subproject_name("pplus_ais") == "pplus_ais"
    assert sanitize_subproject_name("Integratie.Talend") == "Integratie.Talend"


def test_sanitize_strips_path_and_marker_characters():
    assert sanitize_subproject_name("a/b c:d") == "a_b_c_d"
    # A marker inside the stem would give the clone directory two markers, and
    # subproject_key would resolve to the wrong one.
    assert "__intodoc_repo__" not in sanitize_subproject_name("x.__intodoc_repo__y")


def test_sanitize_never_returns_empty():
    assert sanitize_subproject_name("") == "repo"
    assert sanitize_subproject_name("///") == "repo"


def test_allocate_disambiguates_same_leaf_name():
    """Two ADO projects can both contain a repo called `common`."""
    taken: set[str] = set()
    first = allocate_subproject_name("common", taken)
    taken.add(first)
    second = allocate_subproject_name("common", taken)
    assert first == "common"
    assert second == "common_2"
    assert first != second


def test_clone_path_carries_the_origin_marker():
    path = member_clone_path("proj", "Talend8")
    assert path.name == "Talend8.__intodoc_repo__"
    # This is what makes the partition planner see a sub-project boundary.
    from mcp_server.utils.source_origin import subproject_key

    assert subproject_key(str(path)) == "Talend8"


# --------------------------------------------------------------------------- #
# git fixtures
# --------------------------------------------------------------------------- #

def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
             "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)},
    )
    return result.stdout.strip()


def _make_origin(root: Path, name: str, *, content: str = "one") -> Path:
    """Create a real repository with one commit and return its path."""
    work = root / f"{name}_work"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    (work / "job.item").write_text(content, encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "initial")
    return work


def _commit(work: Path, content: str) -> str:
    (work / "job.item").write_text(content, encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "change")
    return _git(work, "rev-parse", "HEAD")


@pytest.fixture
def repos_env(tmp_path, monkeypatch):
    """Point the clone root at a temp dir and provide two real origin repos.

    ``repos_dir`` is a read-only property derived from ``data_dir``, so the
    override goes one level up.
    """
    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    origins = tmp_path / "origins"
    origins.mkdir()
    return {
        "root": tmp_path,
        "a": _make_origin(origins, "repo_a", content="A1"),
        "b": _make_origin(origins, "repo_b", content="B1"),
    }


async def _seed_project(db_session, project_id="proj"):
    db_session.add(
        Project(
            id=project_id,
            display_name=project_id,
            local_path=str(member_clone_path(project_id, "unused").parent),
            platform="talend",
            source_type="git",
        )
    )
    await db_session.flush()


async def _add_two_members(db_session, repos_env, project_id="proj"):
    repo = RepoSourceRepository(db_session)
    for key, name in (("a", "repo_a"), ("b", "repo_b")):
        await repo.add(
            project_id=project_id,
            provider=PROVIDER_AZURE_DEVOPS,
            repo_identity=f"org/Talend/{name}",
            repo_name=name,
            clone_url=f"file://{repos_env[key]}",
            subproject_name=name,
        )
    await db_session.flush()
    return repo


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #

async def test_members_are_listed_deterministically(db_session, repos_env):
    await _seed_project(db_session)
    repo = await _add_two_members(db_session, repos_env)
    names = [s.subproject_name for s in await repo.list_for_project("proj")]
    assert names == ["repo_a", "repo_b"]


async def test_find_by_identity_is_the_webhook_join(db_session, repos_env):
    await _seed_project(db_session)
    repo = await _add_two_members(db_session, repos_env)
    found = await repo.find_by_identity("org/Talend/repo_b")
    assert [s.subproject_name for s in found] == ["repo_b"]
    assert await repo.find_by_identity("org/Talend/absent") == []


async def test_credentials_are_never_persisted_in_a_clone_url(db_session, repos_env):
    await _seed_project(db_session)
    repo = RepoSourceRepository(db_session)
    source = await repo.add(
        project_id="proj",
        provider=PROVIDER_AZURE_DEVOPS,
        repo_identity="org/Proj/secret",
        repo_name="secret",
        clone_url="https://user:tok3n@dev.azure.com/org/Proj/_git/secret",
        subproject_name="secret",
    )
    await db_session.flush()
    assert "tok3n" not in source.clone_url
    assert source.clone_url == "https://dev.azure.com/org/Proj/_git/secret"


# --------------------------------------------------------------------------- #
# Sync — the incremental promise
# --------------------------------------------------------------------------- #

async def test_first_sync_clones_every_member(db_session, repos_env):
    await _seed_project(db_session)
    await _add_two_members(db_session, repos_env)

    report = await MultiRepoSyncService(db_session).sync("proj")

    assert [o.action for o in report.outcomes] == ["cloned", "cloned"]
    assert not report.failed
    for name in ("repo_a", "repo_b"):
        clone = member_clone_path("proj", name)
        assert (clone / ".git").is_dir()
        assert (clone / "job.item").is_file()


async def test_second_sync_with_no_upstream_change_is_unchanged(db_session, repos_env):
    await _seed_project(db_session)
    await _add_two_members(db_session, repos_env)
    svc = MultiRepoSyncService(db_session)
    await svc.sync("proj")

    report = await svc.sync("proj")
    assert [o.action for o in report.outcomes] == ["unchanged", "unchanged"]
    assert report.changed == []


async def test_only_filter_touches_one_repo(db_session, repos_env):
    """A push event names one repo; the others must not even be fetched."""
    await _seed_project(db_session)
    await _add_two_members(db_session, repos_env)
    svc = MultiRepoSyncService(db_session)
    await svc.sync("proj")

    _commit(repos_env["a"], "A2")
    _commit(repos_env["b"], "B2")

    report = await svc.sync("proj", only={"org/Talend/repo_b"})
    by_name = {o.subproject_name: o for o in report.outcomes}

    assert by_name["repo_b"].action == "pulled"
    assert by_name["repo_b"].changed is True
    # repo_a had an upstream commit too, but was not named -- so it is skipped
    # and its working tree still holds the old content.
    assert by_name["repo_a"].action == "skipped"
    assert (member_clone_path("proj", "repo_a") / "job.item").read_text() == "A1"
    assert (member_clone_path("proj", "repo_b") / "job.item").read_text() == "B2"


async def test_pull_records_the_new_head_sha_per_repo(db_session, repos_env):
    await _seed_project(db_session)
    repo = await _add_two_members(db_session, repos_env)
    svc = MultiRepoSyncService(db_session)
    await svc.sync("proj")

    sha_before = {s.subproject_name: s.last_ingested_commit_sha
                  for s in await repo.list_for_project("proj")}
    expected = _commit(repos_env["b"], "B2")

    await svc.sync("proj", only={"org/Talend/repo_b"})
    after = {s.subproject_name: s.last_ingested_commit_sha
             for s in await repo.list_for_project("proj")}

    assert after["repo_b"] == expected
    assert after["repo_a"] == sha_before["repo_a"]


async def test_one_failing_member_does_not_stop_the_others(db_session, repos_env):
    await _seed_project(db_session)
    repo = await _add_two_members(db_session, repos_env)
    await repo.add(
        project_id="proj",
        provider=PROVIDER_AZURE_DEVOPS,
        repo_identity="org/Talend/gone",
        repo_name="gone",
        clone_url=f"file://{repos_env['root']}/origins/does_not_exist",
        subproject_name="gone",
    )
    await db_session.flush()

    report = await MultiRepoSyncService(db_session).sync("proj")
    by_name = {o.subproject_name: o for o in report.outcomes}

    assert by_name["gone"].action == "failed"
    assert by_name["gone"].error
    assert by_name["repo_a"].action == "cloned"
    assert by_name["repo_b"].action == "cloned"
    assert len(report.failed) == 1


async def test_branch_pin_is_honoured(db_session, repos_env):
    """A member pinned to a branch must ingest that branch, not the default."""
    work = repos_env["a"]
    _git(work, "checkout", "-q", "-b", "release")
    release_sha = _commit(work, "A-release")
    _git(work, "checkout", "-q", "main")

    await _seed_project(db_session)
    repo = RepoSourceRepository(db_session)
    await repo.add(
        project_id="proj",
        provider=PROVIDER_AZURE_DEVOPS,
        repo_identity="org/Talend/repo_a",
        repo_name="repo_a",
        clone_url=f"file://{work}",
        subproject_name="repo_a",
        branch="release",
    )
    await db_session.flush()

    report = await MultiRepoSyncService(db_session).sync("proj")
    assert report.outcomes[0].action == "cloned"
    assert report.outcomes[0].head_sha == release_sha
    assert (member_clone_path("proj", "repo_a") / "job.item").read_text() == "A-release"


async def test_disabled_member_is_not_synced(db_session, repos_env):
    await _seed_project(db_session)
    repo = await _add_two_members(db_session, repos_env)
    sources = await repo.list_for_project("proj")
    sources[0].enabled = False
    await db_session.flush()

    report = await MultiRepoSyncService(db_session).sync("proj")
    assert [o.subproject_name for o in report.outcomes] == ["repo_b"]
    assert not member_clone_path("proj", "repo_a").exists()
