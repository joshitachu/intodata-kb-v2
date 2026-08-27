"""Multi-repo project membership: persistence, naming, and per-repo git sync.

A multi-repo project is one project whose source tree is N cloned repositories,
each in its own origin-marked directory:

    {repos_dir}/{project_id}/
        JDE_DWH.__intodoc_repo__/          <- clone of Talend/JDE_DWH
        pplus_ais.__intodoc_repo__/        <- clone of Talend/pplus_ais
        Talend8.__intodoc_repo__/          <- clone of Talend/Talend8

That layout is the whole trick: the scanner reports one ``source_root`` per
clone, the marker makes each one a sub-project boundary
(``utils.source_origin``), and the partition planner therefore produces one
scoped generation -- one doc set, one call budget -- per repository. Nothing
downstream needed a multi-repo concept; it reuses the composite path that
already serves multi-ZIP uploads.

Patterns:
- **Repository** (:class:`RepoSourceRepository`) -- every statement touching
  ``project_repo_sources`` lives here, so callers never embed SQL.
- **Service** (:class:`MultiRepoSyncService`) -- clone/pull orchestration, with
  credential resolution delegated to the existing per-provider helpers.

Incremental by construction: :meth:`MultiRepoSyncService.sync` accepts an
``only`` filter, so a push event pulls exactly the repo it names and leaves the
siblings' working trees untouched. Whether a repo's *documentation* is
regenerated is decided separately and deterministically by the per-partition
fingerprints (``providers/custom/partition_fingerprint``).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.config import config
from mcp_server.db.models import ProjectRepoSource
from mcp_server.services.git_remote_service import (
    authenticated_url_plaintext,
    run_git,
    strip_url_credentials,
)
from mcp_server.utils.error_sanitizer import sanitize_git_error
from mcp_server.utils.source_origin import REPO_MARKER, marker_dir_name

logger = logging.getLogger(__name__)

PROVIDER_GITHUB = "github"
PROVIDER_AZURE_DEVOPS = "azure_devops"
SUPPORTED_PROVIDERS = (PROVIDER_GITHUB, PROVIDER_AZURE_DEVOPS)

#: Characters that are unsafe in a path segment, a partition name, or an output
#: directory. The sub-project name flows into all three.
_UNSAFE_SEGMENT = re.compile(r'[/\\:*?"<>|\s]+')

#: Bound on a sub-project name. It becomes a directory under the project root
#: and a prefix of scan job directories, so it must stay well clear of PATH_MAX.
MAX_SUBPROJECT_NAME = 64


def sanitize_subproject_name(raw: str) -> str:
    """Reduce a repository name to a safe sub-project / partition name.

    The marker itself must never appear in the sanitized stem, otherwise the
    clone directory would carry two markers and ``subproject_key`` would resolve
    to the wrong (first) one.
    """
    text = _UNSAFE_SEGMENT.sub("_", str(raw or "").strip())
    text = text.replace(REPO_MARKER, "_").strip("._")
    text = text[:MAX_SUBPROJECT_NAME].strip("._")
    return text or "repo"


def allocate_subproject_name(repo_name: str, taken: set[str]) -> str:
    """Return a sanitized sub-project name not already in *taken*.

    Two repos with the same leaf name -- entirely normal when a project spans
    two Azure DevOps projects -- would otherwise collide on one partition and
    silently merge their documentation. A numeric suffix keeps them distinct and
    keeps the name recognisable, unlike a hash.
    """
    base = sanitize_subproject_name(repo_name)
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}_{suffix}"[:MAX_SUBPROJECT_NAME].strip("._")
        if candidate and candidate not in taken:
            return candidate
    raise ValueError(f"Unable to allocate a sub-project name for {repo_name!r}")


def project_repos_root(project_id: str) -> Path:
    """Container directory holding every member clone of *project_id*."""
    return config.repos_dir / project_id


def member_clone_path(project_id: str, subproject_name: str) -> Path:
    """Where a member repo is cloned, carrying its origin marker."""
    return project_repos_root(project_id) / marker_dir_name(subproject_name, REPO_MARKER)


@dataclass
class RepoSyncOutcome:
    """What happened to one member repo during a sync."""

    subproject_name: str
    repo_identity: str
    action: str = "skipped"  # cloned | pulled | unchanged | skipped | failed
    head_sha: str | None = None
    previous_sha: str | None = None
    error: str | None = None

    @property
    def changed(self) -> bool:
        """True when this repo's working tree moved, so its docs may be stale."""
        return self.action in {"cloned", "pulled"} and self.head_sha != self.previous_sha


@dataclass
class MultiRepoSyncReport:
    outcomes: list[RepoSyncOutcome] = field(default_factory=list)

    @property
    def changed(self) -> list[RepoSyncOutcome]:
        return [o for o in self.outcomes if o.changed]

    @property
    def failed(self) -> list[RepoSyncOutcome]:
        return [o for o in self.outcomes if o.action == "failed"]


class RepoSourceRepository:
    """All persistence for ``project_repo_sources``. No SQL escapes this class."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_project(
        self, project_id: str, *, enabled_only: bool = False
    ) -> list[ProjectRepoSource]:
        stmt = select(ProjectRepoSource).where(ProjectRepoSource.project_id == project_id)
        if enabled_only:
            stmt = stmt.where(ProjectRepoSource.enabled.is_(True))
        # Deterministic order so partition planning, logs and API responses agree.
        stmt = stmt.order_by(ProjectRepoSource.subproject_name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get(self, source_id: str) -> ProjectRepoSource | None:
        stmt = select(ProjectRepoSource).where(ProjectRepoSource.id == source_id)
        return (await self._db.execute(stmt)).scalars().first()

    async def find_by_identity(
        self, repo_identity: str, *, project_id: str | None = None
    ) -> list[ProjectRepoSource]:
        """Resolve a push event's repository to the member rows that track it.

        One repo may be a member of several projects, so this returns a list.
        Indexed on ``repo_identity`` because it is the webhook hot path.
        """
        stmt = select(ProjectRepoSource).where(ProjectRepoSource.repo_identity == repo_identity)
        if project_id is not None:
            stmt = stmt.where(ProjectRepoSource.project_id == project_id)
        return list((await self._db.execute(stmt)).scalars().all())

    async def add(
        self,
        *,
        project_id: str,
        provider: str,
        repo_identity: str,
        repo_name: str,
        clone_url: str,
        subproject_name: str,
        branch: str | None = None,
        github_installation_id: int | None = None,
        azure_devops_connection_id: str | None = None,
    ) -> ProjectRepoSource:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider!r}")
        source = ProjectRepoSource(
            id=str(uuid.uuid4()),
            project_id=project_id,
            provider=provider,
            repo_identity=repo_identity,
            repo_name=repo_name,
            # Defence in depth: a credential must never be persisted in a URL.
            clone_url=strip_url_credentials(clone_url) or clone_url,
            branch=branch or None,
            subproject_name=subproject_name,
            local_path=str(member_clone_path(project_id, subproject_name)),
            github_installation_id=github_installation_id,
            azure_devops_connection_id=azure_devops_connection_id,
        )
        self._db.add(source)
        return source

    async def remove(self, source_id: str) -> None:
        await self._db.execute(delete(ProjectRepoSource).where(ProjectRepoSource.id == source_id))

    async def mark_ingested(self, source_id: str, head_sha: str | None) -> None:
        source = await self.get(source_id)
        if source is None:
            return
        source.last_ingested_commit_sha = head_sha
        source.last_ingested_at = datetime.now(UTC)
        source.updated_at = datetime.now(UTC)

    async def taken_subproject_names(self, project_id: str) -> set[str]:
        stmt = select(ProjectRepoSource.subproject_name).where(
            ProjectRepoSource.project_id == project_id
        )
        return set((await self._db.execute(stmt)).scalars().all())


async def resolve_clone_token(source: ProjectRepoSource, db: AsyncSession) -> str | None:
    """Resolve *source*'s clone credential, or ``None`` for a public repo.

    Same precedence as the single-repo path in ``git_credentials``: an Azure
    DevOps organization PAT, then a GitHub App installation token, then nothing.
    Never returns a credential embedded in a URL, and never logs one.
    """
    if source.azure_devops_connection_id:
        from mcp_server.services.azure_devops_connections import resolve_pat

        return await resolve_pat(db, source.azure_devops_connection_id)
    if source.github_installation_id:
        from mcp_server.utils.github_app import mint_installation_token

        return await mint_installation_token(int(source.github_installation_id))
    return None


class MultiRepoSyncService:
    """Clone or fast-forward each member repo of a multi-repo project.

    ``only`` is what makes re-ingestion incremental at the clone layer: a push
    to one repo touches one working tree. Every other member is left exactly as
    it was, so the scan that follows sees unchanged bytes for them and their
    per-partition fingerprints do not move.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = RepoSourceRepository(db)

    async def sync(
        self,
        project_id: str,
        *,
        only: set[str] | None = None,
        clone_timeout: float = 600.0,
    ) -> MultiRepoSyncReport:
        """Bring member clones up to date. *only* filters by ``repo_identity``."""
        report = MultiRepoSyncReport()
        sources = await self._repo.list_for_project(project_id, enabled_only=True)
        root = project_repos_root(project_id)
        root.mkdir(parents=True, exist_ok=True)

        for source in sources:
            outcome = RepoSyncOutcome(
                subproject_name=source.subproject_name,
                repo_identity=source.repo_identity,
                previous_sha=source.last_ingested_commit_sha,
            )
            report.outcomes.append(outcome)
            if only is not None and source.repo_identity not in only:
                continue
            try:
                outcome.action, outcome.head_sha = await self._sync_one(
                    source, timeout=clone_timeout
                )
                await self._repo.mark_ingested(source.id, outcome.head_sha)
            except Exception as exc:  # noqa: BLE001 - one repo must not fail the set
                outcome.action = "failed"
                # Sanitized: a clone URL may carry a token in the error text.
                outcome.error = sanitize_git_error(str(exc))
                logger.warning(
                    "Member repo sync failed for %s/%s: %s",
                    project_id,
                    source.subproject_name,
                    outcome.error,
                )
        return report

    async def _sync_one(
        self, source: ProjectRepoSource, *, timeout: float
    ) -> tuple[str, str | None]:
        target = Path(source.local_path)
        token = await resolve_clone_token(source, self._db)
        # The authenticated URL exists only for the duration of the subprocess
        # argv and is never persisted or returned.
        auth_url = authenticated_url_plaintext(source.clone_url, token)

        if not (target / ".git").exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            args = ["clone"]
            if source.branch:
                args += ["--branch", source.branch, "--single-branch"]
            args += ["--", auth_url, str(target)]
            await run_git(str(target.parent), *args, timeout=timeout)
            return "cloned", await self._head_sha(target)

        before = await self._head_sha(target)
        await run_git(str(target), "fetch", "--prune", auth_url, timeout=timeout)
        ref = f"refs/remotes/origin/{source.branch}" if source.branch else "FETCH_HEAD"
        try:
            await run_git(str(target), "merge", "--ff-only", ref, timeout=timeout)
        except RuntimeError:
            # A non-fast-forwardable member is reported, never force-reset: the
            # working tree may hold a locally applied documentation patch.
            raise
        after = await self._head_sha(target)
        return ("pulled" if after != before else "unchanged"), after

    @staticmethod
    async def _head_sha(repo_dir: Path) -> str | None:
        try:
            return await run_git(str(repo_dir), "rev-parse", "HEAD", strip=True) or None
        except RuntimeError:
            return None
