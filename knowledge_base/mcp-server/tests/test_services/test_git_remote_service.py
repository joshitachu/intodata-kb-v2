"""Tests for credential-safe Git remote helpers."""

from __future__ import annotations

from pathlib import Path
import subprocess


def _make_ready_cache_tree(root: Path, project_id: str) -> None:
    output_dir = root / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "group.md").write_text("analysis", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")


def test_strip_url_credentials_and_canonical_keys():
    from mcp_server.services.git_remote_service import (
        canonical_remote_key,
        strip_url_credentials,
        url_has_credentials,
    )

    tokenized = "https://user:credential@example.com/Org/Repo.git"

    assert strip_url_credentials(tokenized) == "https://example.com/Org/Repo.git"
    assert url_has_credentials(tokenized) is True
    assert url_has_credentials("https://example.com/Org/Repo.git") is False
    assert canonical_remote_key(tokenized) == canonical_remote_key(
        "https://example.com/org/repo"
    )
    assert canonical_remote_key("git@example.com:Org/Repo.git") == "ssh://example.com/org/repo"


async def test_temporary_authenticated_origin_restores_clean_remote_on_failure(monkeypatch):
    from mcp_server.services import git_remote_service as service

    calls: list[tuple[str, ...]] = []
    remote_url = "https://example.com/acme/repo.git"

    async def git_func(local_path: str, *args: str, **kwargs) -> str:
        calls.append(args)
        if args == ("remote", "get-url", "origin"):
            return remote_url
        if args[:3] == ("remote", "set-url", "origin"):
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(
        service,
        "authenticated_url",
        lambda repo_url, token_enc: "https://tokenized.example/acme/repo.git",
    )

    async def failing_operation() -> str:
        raise RuntimeError("boom")

    try:
        await service.with_temporary_authenticated_origin(
            "/repo",
            "encrypted-token",
            failing_operation,
            git_func=git_func,
        )
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("operation should have raised")

    assert calls == [
        ("remote", "get-url", "origin"),
        ("remote", "set-url", "origin", "https://tokenized.example/acme/repo.git"),
        ("remote", "set-url", "origin", remote_url),
    ]


async def test_readiness_classifies_tokenized_origin_and_cache(tmp_path, monkeypatch):
    from mcp_server.config import config
    from mcp_server.services.git_remote_service import inspect_reingestion_readiness

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://user:credential@example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    monkeypatch.setattr(config, "project_root", tmp_path)
    _make_ready_cache_tree(tmp_path, "proj")

    class ProjectStub:
        id = "proj"
        source_type = "git"
        local_path = str(repo)
        repo_url = "https://example.com/acme/repo.git"
        last_ingested_commit_sha = "a" * 40
        github_token_enc = "encrypted-token"

    report = await inspect_reingestion_readiness(ProjectStub())

    assert report.status == "needs_remote_repair"
    assert report.origin_had_credentials is True
    assert report.origin_url == "https://example.com/acme/repo.git"
    assert "tokenized_origin" in report.issues
    assert report.can_incremental_regen is True
    assert report.targeted_regen_ready is True
    assert report.has_docs is True
    assert report.has_rag_support is True
    assert report.rag_support_analysis_count == 1
    assert report.analysis_cache_count == 1


async def test_readiness_classifies_sanitized_auth_failure(tmp_path, monkeypatch):
    from mcp_server.config import config
    from mcp_server.services.git_remote_service import inspect_reingestion_readiness

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    monkeypatch.setattr(config, "project_root", tmp_path)
    _make_ready_cache_tree(tmp_path, "proj")

    class ProjectStub:
        id = "proj"
        source_type = "git"
        local_path = str(repo)
        repo_url = "https://example.com/acme/repo.git"
        last_ingested_commit_sha = "a" * 40
        github_token_enc = "encrypted-token"
        auto_reingest_error = (
            "authentication_failed: could not read Password for "
            "https://token@example.com/acme/repo.git"
        )

    report = await inspect_reingestion_readiness(ProjectStub())

    assert report.status == "auth_failed"
    assert report.auth_failed is True
    assert "auth_failed" in report.issues
    assert report.ready is False
    assert "token@" not in (report.error or "")


async def test_readiness_requires_support_package_even_when_flat_cache_exists(
    tmp_path,
    monkeypatch,
):
    from mcp_server.config import config
    from mcp_server.services.git_remote_service import inspect_reingestion_readiness

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    cache = output_dir / "analysis_cache"
    docs.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    class ProjectStub:
        id = "proj"
        source_type = "git"
        local_path = str(repo)
        repo_url = "https://example.com/acme/repo.git"
        last_ingested_commit_sha = "a" * 40
        github_token_enc = "encrypted-token"

    report = await inspect_reingestion_readiness(ProjectStub())

    assert report.status == "requires_full_rebaseline"
    assert report.ready is False
    assert report.has_docs is True
    assert report.has_analysis_cache is True
    assert report.has_rag_support is False
    assert report.analysis_cache_count == 1
    assert report.rag_support_analysis_count == 0
    assert report.targeted_regen_ready is False
    assert report.requires_full_rebaseline is True
    assert "missing_rag_support" in report.reason_codes


async def test_readiness_origin_repo_mismatch_is_not_ready(tmp_path, monkeypatch):
    from mcp_server.config import config
    from mcp_server.services.git_remote_service import inspect_reingestion_readiness

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/acme/other.git"],
        cwd=repo,
        check=True,
    )
    monkeypatch.setattr(config, "project_root", tmp_path)
    _make_ready_cache_tree(tmp_path, "proj")

    class ProjectStub:
        id = "proj"
        source_type = "git"
        local_path = str(repo)
        repo_url = "https://example.com/acme/repo.git"
        last_ingested_commit_sha = "a" * 40
        github_token_enc = "encrypted-token"

    report = await inspect_reingestion_readiness(ProjectStub())

    assert report.ready is False
    assert report.status == "needs_attention"
    assert report.origin_matches_repo_url is False
    assert "origin_repo_mismatch" in report.issues
