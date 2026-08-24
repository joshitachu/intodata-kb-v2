"""Credential-safe helpers for Git remote operations and readiness checks."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from mcp_server.config import config
from mcp_server.services.git_credentials import (
    GitCredentialProvider,
    NullCredentialProvider,
    provider_for_project,
)
from mcp_server.services.rag_support_package import inspect_generation_cache_readiness
from mcp_server.utils.encryption import inject_plaintext_token
from mcp_server.utils.error_sanitizer import sanitize_git_error

GitFunc = Callable[[str, str], Awaitable[str]]


def git_subprocess_env() -> dict[str, str]:
    """Return an environment that prevents interactive Git credential prompts."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}


async def run_git(
    local_path: str,
    *args: str,
    timeout: float = 60.0,
    strip: bool = False,
) -> str:
    """Run a local Git command with non-interactive credential handling."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=local_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=git_subprocess_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout:.0f}s")
    if proc.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(sanitize_git_error(detail or f"git {' '.join(args)} failed"))
    result = stdout.decode()
    return result.strip() if strip else result


def _netloc_without_userinfo(parsed) -> str:
    host = parsed.hostname or ""
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def strip_url_credentials(repo_url: str | None) -> str | None:
    """Return *repo_url* without HTTP(S) username/password credentials."""
    if not repo_url:
        return repo_url
    raw = repo_url.strip()
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and "@" in parsed.netloc:
        return urlunparse(parsed._replace(netloc=_netloc_without_userinfo(parsed)))
    return raw


def url_has_credentials(repo_url: str | None) -> bool:
    """True when *repo_url* contains HTTP(S) userinfo."""
    if not repo_url:
        return False
    parsed = urlparse(repo_url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.username or parsed.password)


def authenticated_url_plaintext(repo_url: str, token: str | None) -> str:
    """Build an in-memory token-authenticated URL from a sanitized remote URL.

    One builder for every credential kind. The ``x-access-token`` username is
    required for GitHub App installation tokens (GitHub rejects an installation
    token in the username position), accepted by GitHub for PATs, and ignored by
    Azure DevOps, which authenticates on the password alone.
    """
    return inject_plaintext_token(
        strip_url_credentials(repo_url) or repo_url, token, username="x-access-token"
    )


def canonical_remote_key(repo_url: str | None) -> str | None:
    """Normalize a remote URL for equality checks without preserving credentials."""
    clean = strip_url_credentials(repo_url)
    if not clean:
        return None
    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"}:
        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{parsed.scheme}://{(parsed.hostname or '').lower()}{path.lower()}"
    if clean.startswith("git@") and ":" in clean:
        host, path = clean[4:].split(":", 1)
        path = path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"ssh://{host.lower()}/{path.lower()}"
    path = clean.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path.lower()


def _looks_like_auth_failure(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in (
            "authentication_failed",
            "authentication failed",
            "could not read password",
            "permission denied",
            "repository not found",
            "authentication required",
            "fatal: authentication",
        )
    )


async def get_origin_url(
    local_path: str,
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> str | None:
    """Read the local origin URL, returning None when it is unavailable."""
    try:
        return (await git_func(local_path, "remote", "get-url", "origin")).strip()
    except Exception:
        return None


async def set_origin_url(
    local_path: str,
    url: str,
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> None:
    """Set origin URL to a sanitized value."""
    await git_func(local_path, "remote", "set-url", "origin", url)


async def _with_temporary_origin(
    local_path: str,
    build_authed: Callable[[str], str | None],
    operation: Callable[[], Awaitable[str | None]],
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> str | None:
    """Origin-swap mechanics for every credential kind.

    Restores a clean (credential-free) origin on the way in and always on the way
    out — regardless of how the authenticated URL was built.
    """
    current_url = await get_origin_url(local_path, git_func=git_func)
    clean_url = strip_url_credentials(current_url)
    if current_url and clean_url and current_url != clean_url:
        await set_origin_url(local_path, clean_url, git_func=git_func)

    authed_url = build_authed(clean_url) if clean_url else clean_url
    changed = bool(authed_url and clean_url and authed_url != clean_url)
    try:
        if changed:
            await set_origin_url(local_path, authed_url, git_func=git_func)
        return await operation()
    finally:
        if clean_url and (changed or current_url != clean_url):
            await set_origin_url(local_path, clean_url, git_func=git_func)


async def with_temporary_authenticated_origin_token(
    local_path: str,
    token: str | None,
    operation: Callable[[], Awaitable[str | None]],
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> str | None:
    """Temporarily set origin using a resolved plaintext token; always restore clean origin."""
    return await _with_temporary_origin(
        local_path,
        lambda clean: authenticated_url_plaintext(clean, token) if token else clean,
        operation,
        git_func=git_func,
    )


async def fetch_remote(
    local_path: str,
    credential: GitCredentialProvider | None = None,
    *,
    remote: str = "origin",
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> None:
    """Fetch a remote with temporary auth and clean-origin restoration.

    ``credential`` is a strategy from :mod:`mcp_server.services.git_credentials`,
    so this one entry point serves stored PATs, minted GitHub App tokens and Azure
    DevOps organization PATs alike. ``None`` means an unauthenticated (public) remote.
    """
    token = await (credential or NullCredentialProvider()).get_token()

    async def _fetch() -> str:
        return await git_func(local_path, "fetch", "--quiet", remote)

    await with_temporary_authenticated_origin_token(
        local_path,
        token,
        _fetch,
        git_func=git_func,
    )


async def pull_remote_ff_only(
    local_path: str,
    credential: GitCredentialProvider | None = None,
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> None:
    """Run `git pull --ff-only` with temporary auth and clean-origin restoration."""
    token = await (credential or NullCredentialProvider()).get_token()

    async def _pull() -> str:
        return await git_func(local_path, "pull", "--ff-only")

    await with_temporary_authenticated_origin_token(
        local_path,
        token,
        _pull,
        git_func=git_func,
    )


async def fetch_remote_for_project(
    local_path: str,
    project,
    *,
    remote: str = "origin",
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> None:
    """Fetch using the project's resolved credential."""
    await fetch_remote(
        local_path,
        provider_for_project(project),
        remote=remote,
        git_func=git_func,
    )


async def pull_remote_ff_only_for_project(
    local_path: str,
    project,
    *,
    git_func: Callable[..., Awaitable[str]] = run_git,
) -> None:
    """Run `git pull --ff-only` using the project's resolved credential."""
    await pull_remote_ff_only(local_path, provider_for_project(project), git_func=git_func)


@dataclass(frozen=True)
class ReingestionReadinessReport:
    project_id: str
    source_type: str
    status: str
    ready: bool
    repairable: bool
    repaired: bool = False
    issues: list[str] = field(default_factory=list)
    local_path_exists: bool = False
    is_git_repo: bool = False
    repo_url: str | None = None
    origin_url: str | None = None
    repo_url_had_credentials: bool = False
    origin_had_credentials: bool = False
    origin_matches_repo_url: bool | None = None
    has_github_token: bool = False
    has_last_ingested_commit: bool = False
    has_docs: bool = False
    doc_count: int = 0
    has_rag_support: bool = False
    rag_support_analysis_count: int = 0
    has_analysis_cache: bool = False
    analysis_cache_count: int = 0
    cache_support_match: bool = False
    targeted_regen_ready: bool = False
    requires_full_rebaseline: bool = False
    reason_codes: list[str] = field(default_factory=list)
    can_incremental_regen: bool = False
    auth_failed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "source_type": self.source_type,
            "status": self.status,
            "ready": self.ready,
            "repairable": self.repairable,
            "repaired": self.repaired,
            "issues": list(self.issues),
            "local_path_exists": self.local_path_exists,
            "is_git_repo": self.is_git_repo,
            "repo_url": self.repo_url,
            "origin_url": self.origin_url,
            "repo_url_had_credentials": self.repo_url_had_credentials,
            "origin_had_credentials": self.origin_had_credentials,
            "origin_matches_repo_url": self.origin_matches_repo_url,
            "has_github_token": self.has_github_token,
            "has_last_ingested_commit": self.has_last_ingested_commit,
            "has_docs": self.has_docs,
            "doc_count": self.doc_count,
            "has_rag_support": self.has_rag_support,
            "rag_support_analysis_count": self.rag_support_analysis_count,
            "has_analysis_cache": self.has_analysis_cache,
            "analysis_cache_count": self.analysis_cache_count,
            "cache_support_match": self.cache_support_match,
            "targeted_regen_ready": self.targeted_regen_ready,
            "requires_full_rebaseline": self.requires_full_rebaseline,
            "reason_codes": list(self.reason_codes),
            "can_incremental_regen": self.can_incremental_regen,
            "auth_failed": self.auth_failed,
            "error": self.error,
        }


async def inspect_reingestion_readiness(project) -> ReingestionReadinessReport:
    """Classify credential hygiene, baseline, and incremental-cache readiness."""
    source_type = str(getattr(project, "source_type", "") or "")
    local_path = str(getattr(project, "local_path", "") or "")
    repo_url = strip_url_credentials(getattr(project, "repo_url", None))
    repo_url_had_credentials = url_has_credentials(getattr(project, "repo_url", None))
    project_error = sanitize_git_error(str(getattr(project, "auto_reingest_error", "") or "")) or None
    auth_failed = _looks_like_auth_failure(project_error)
    path = Path(local_path)
    issues: list[str] = []

    if source_type != "git":
        issues.append("not_git_source")

    local_path_exists = path.exists()
    if not local_path_exists:
        issues.append("missing_local_path")

    is_git_repo = False
    origin_url: str | None = None
    origin_had_credentials = False
    error: str | None = None
    if local_path_exists:
        try:
            await run_git(local_path, "rev-parse", "--git-dir", timeout=10.0, strip=True)
            is_git_repo = True
            raw_origin = await get_origin_url(local_path)
            origin_had_credentials = url_has_credentials(raw_origin)
            origin_url = strip_url_credentials(raw_origin)
            if raw_origin is None:
                issues.append("missing_origin")
        except Exception as exc:
            error = sanitize_git_error(str(exc))
            issues.append("not_git_repository")

    if origin_had_credentials:
        issues.append("tokenized_origin")
    if repo_url_had_credentials:
        issues.append("tokenized_repo_url")
    if auth_failed:
        issues.append("auth_failed")

    origin_matches_repo_url: bool | None = None
    if repo_url and origin_url:
        origin_matches_repo_url = canonical_remote_key(repo_url) == canonical_remote_key(origin_url)
        if not origin_matches_repo_url:
            issues.append("origin_repo_mismatch")

    has_last_ingested_commit = bool(getattr(project, "last_ingested_commit_sha", None))
    if not has_last_ingested_commit:
        issues.append("missing_baseline_commit")

    output_dir = config.project_root / "output" / str(project.id)
    cache_readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=has_last_ingested_commit,
    )
    for reason_code in cache_readiness.reason_codes:
        if reason_code not in issues:
            issues.append(reason_code)

    can_incremental_regen = cache_readiness.targeted_regen_ready
    repairable = origin_had_credentials or repo_url_had_credentials
    ready = (
        source_type == "git"
        and local_path_exists
        and is_git_repo
        and not repairable
        and not auth_failed
        and origin_matches_repo_url is not False
        and can_incremental_regen
    )
    if ready:
        status = "ready"
    elif repairable:
        status = "needs_remote_repair"
    elif auth_failed:
        status = "auth_failed"
    elif source_type != "git":
        status = "not_git"
    elif not local_path_exists or not is_git_repo:
        status = "missing_local_repo"
    elif not has_last_ingested_commit:
        status = "missing_baseline"
    elif cache_readiness.requires_full_rebaseline:
        status = "requires_full_rebaseline"
    else:
        status = "needs_attention"

    return ReingestionReadinessReport(
        project_id=str(project.id),
        source_type=source_type,
        status=status,
        ready=ready,
        repairable=repairable,
        issues=issues,
        local_path_exists=local_path_exists,
        is_git_repo=is_git_repo,
        repo_url=repo_url,
        origin_url=origin_url,
        repo_url_had_credentials=repo_url_had_credentials,
        origin_had_credentials=origin_had_credentials,
        origin_matches_repo_url=origin_matches_repo_url,
        # "a credential is resolvable", not "a PAT column is populated": App and
        # Azure DevOps connections carry their credential off the project row.
        has_github_token=not isinstance(provider_for_project(project), NullCredentialProvider),
        has_last_ingested_commit=has_last_ingested_commit,
        has_docs=cache_readiness.has_docs,
        doc_count=cache_readiness.doc_count,
        has_rag_support=cache_readiness.has_rag_support,
        rag_support_analysis_count=cache_readiness.rag_support_analysis_count,
        has_analysis_cache=cache_readiness.has_analysis_cache,
        analysis_cache_count=cache_readiness.analysis_cache_count,
        cache_support_match=cache_readiness.cache_support_match,
        targeted_regen_ready=cache_readiness.targeted_regen_ready,
        requires_full_rebaseline=cache_readiness.requires_full_rebaseline,
        reason_codes=list(issues),
        can_incremental_regen=can_incremental_regen,
        auth_failed=auth_failed,
        error=error or project_error,
    )


async def repair_remote_hygiene(project) -> ReingestionReadinessReport:
    """Remove persisted credentials from local origin and return a fresh report."""
    local_path = str(getattr(project, "local_path", "") or "")
    raw_origin = await get_origin_url(local_path) if local_path else None
    clean_origin = strip_url_credentials(raw_origin)
    repaired = False
    if raw_origin and clean_origin and raw_origin != clean_origin:
        await set_origin_url(local_path, clean_origin)
        repaired = True

    report = await inspect_reingestion_readiness(project)
    return ReingestionReadinessReport(**{**report.to_dict(), "repaired": repaired})
