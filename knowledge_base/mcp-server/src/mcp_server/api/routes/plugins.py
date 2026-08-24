"""Plugin pack endpoints -- the single pack API.

Two lanes, one format (see ``db.models.PluginPack``):

* PERSONAL -- authored in Nexus, stored against the author's user account,
  visible and usable only to them, freely editable. This is where every pack
  starts.
* SHARED -- a branch of the configured pack repository, cloned in and available
  to every user. A personal pack becomes shared by being published: Nexus
  pushes it as a branch and opens a pull request, and a human reviewer decides
  whether it lands. Nexus opens the door; it does not grant the access.

  GET    /mcp/plugins                             - list packs visible to the caller
  GET    /mcp/plugins/starter                     - shared repo + starter metadata
  GET    /mcp/plugins/prompt-slots                - every prompt a pack can override
  GET    /mcp/plugins/prompt-slots/{path}         - one slot's built-in body
  GET    /mcp/plugins/starter.zip                 - download the starter skeleton
  POST   /mcp/plugins/discover                    - discover shared pack branches
  POST   /mcp/plugins/branch-files                - list editable files in a branch
  POST   /mcp/plugins/branch-file                 - preview one branch file
  POST   /mcp/plugins                             - register a shared pack branch
  POST   /mcp/plugins/personal                    - create a personal pack
  POST   /mcp/plugins/{id}/publish                - push a branch + open its PR
  PATCH  /mcp/plugins/{id}                        - pull a shared pack
  DELETE /mcp/plugins/{id}                        - delete a personal pack
  GET    /mcp/plugins/{id}/files                  - list editable files in a pack
  GET    /mcp/plugins/{id}/files/{file_path}      - read a file
  PUT    /mcp/plugins/{id}/files/{file_path}      - write a file
  PATCH  /mcp/projects/{id}/plugin                - select / clear a project's pack
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_ as sa_or
from sqlalchemy import select

from mcp_server.api.permissions import require_pack_management, require_project_access
from mcp_server.config import config
from mcp_server.db.models import PluginPack, Project
from mcp_server.db.session import async_session_factory
from mcp_server.services import pack_architect
from mcp_server.services.git_remote_service import (
    authenticated_url_plaintext,
    canonical_remote_key,
    pull_remote_ff_only,
    run_git,
    strip_url_credentials,
)
from mcp_server.services.pack_governance import (
    PackGovernanceDenied,
    pack_governance,
)
from mcp_server.services.pack_prompt_slots import prompt_slots, slot_for
from mcp_server.services.pack_validation import validate_generated_pack_files
from mcp_server.services.plugin_schema import PluginPackManifest, load_plugin_manifest
from mcp_server.utils.error_sanitizer import sanitize_git_error
from mcp_server.utils.github_app import (
    github_app_configured,
    list_app_installations,
    mint_installation_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plugins"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PluginPackOut(BaseModel):
    id: int
    name: str
    manifest_name: str | None = None
    version: str
    description: str | None
    repo_url: str | None
    repo_branch: str | None = None
    last_commit_sha: str | None
    auto_update: bool
    origin: str
    certification_status: str
    certification_provenance: str | None = None
    #: True for a pack the caller authored; False for a shared pack.
    personal: bool = False
    #: May drive document generation.
    usable: bool = True
    #: May be edited in-app (personal packs only).
    mutable: bool
    blocked_reason: str | None = None

    model_config = {"from_attributes": True}


class CreatePersonalPackRequest(BaseModel):
    name: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    description: str | None = Field(default=None, max_length=1_000)
    #: Full ``plugin.yaml`` text. Omitted means "start from the starter
    #: skeleton", which is the manual path for anyone clicking New Pack.
    manifest_yaml: str | None = Field(default=None, max_length=200_000)
    #: Extra pack files, relative path -> content (``prompts/phase2.md``,
    #: ``prompts/docs/MY_DOC.md``, ``examples/…``). Same extension allowlist as
    #: the file editor.
    files: dict[str, str] = Field(default_factory=dict)
    #: The AI author sends a complete file set and disables starter inheritance
    #: so an unrelated starter prompt/example cannot leak into the new pack.
    inherit_starter: bool = True
    #: Needed only to validate an AI-generated platform extension as part of
    #: the complete file set.
    platform: str | None = Field(
        default=None,
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class CreatePackArchitectSessionRequest(BaseModel):
    pack_id: int | None = Field(default=None, ge=1)
    platform: str | None = Field(
        default=None,
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    manifest_yaml: str | None = Field(default=None, max_length=200_000)
    files: dict[str, str] = Field(default_factory=dict)


class PackArchitectTurnRequest(BaseModel):
    message: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)


class PackArchitectSaveRequest(BaseModel):
    name: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    expected_revision: int = Field(ge=1)


class PackArchitectAbandonRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class PackArchitectSessionOut(BaseModel):
    id: str
    status: str
    revision: int
    source_pack_id: int | None = None
    platform: str | None = None
    model_id: str
    name: str | None = None
    description: str | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    manifest_yaml: str
    files: dict[str, str] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)
    complete: bool
    turn_status: str
    turn_stage: str | None = None
    turn_detail: str | None = None
    turn_started_at: str | None = None
    turn_finished_at: str | None = None
    turn_error: str | None = None


class PublishPackRequest(BaseModel):
    #: Defaults to ``{prefix}{pack name}``.
    branch: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=10_000)


class PublishPackOut(BaseModel):
    branch: str
    repo_url: str
    commit_sha: str
    pull_request_url: str | None = None
    #: Present when the branch pushed but the pull request could not be opened
    #: automatically -- the author can still open it from the branch.
    note: str | None = None


class StarterPackOut(BaseModel):
    repo_url: str
    clone_url: str
    zip_url: str
    name: str | None = None
    version: str | None = None
    description: str | None = None


class PromptSlotOut(BaseModel):
    """One overridable prompt, without its body."""

    path: str
    phase: str
    #: preamble | body | document | platform_extension. The distinction is not
    #: cosmetic: a `preamble` is substituted INTO a prompt, a `body` REPLACES
    #: one. An author who confuses them ships a pack that renders the section
    #: skeleton three times with no framing (a real, verified failure).
    kind: str
    title: str
    description: str
    default_bytes: int
    has_default: bool


class PromptSlotContentOut(PromptSlotOut):
    content: str


class PluginPackBranchOut(BaseModel):
    branch: str
    name: str
    manifest_name: str
    version: str
    description: str | None
    repo_url: str
    commit_sha: str
    registered: bool = False
    registered_pack_id: int | None = None


class DiscoverPackBranchesRequest(BaseModel):
    repo_url: str | None = None


class DiscoverPackBranchesOut(BaseModel):
    repo_url: str
    branches: list[PluginPackBranchOut]


class PreviewPackBranchFilesRequest(BaseModel):
    repo_url: str
    repo_branch: str


class PreviewPackBranchFileRequest(BaseModel):
    repo_url: str
    repo_branch: str
    file_path: str


class RegisterPackRequest(BaseModel):
    path: str | None = None
    repo_url: str | None = None
    repo_branch: str | None = None


class PatchPackRequest(BaseModel):
    auto_update: bool | None = None
    pull: bool = False


class AssignPluginRequest(BaseModel):
    plugin_pack_id: int | None = None


class FileContentOut(BaseModel):
    path: str
    content: str


class SaveFileRequest(BaseModel):
    content: str


_ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt"}
_PACK_SLUG_RE = re.compile(r"[^a-z0-9]+")
_BRANCH_NAME_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*[A-Za-z0-9._-]")
_COMMIT_NAME = "Nexus Pack Author"
_COMMIT_EMAIL = "packs@nexus.local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_pack_file(pack_path: str, file_path: str) -> Path:
    """Return a resolved Path inside pack_path, rejecting traversal and bad extensions."""
    pack_dir = Path(pack_path).resolve()
    target = (pack_dir / file_path).resolve()
    if not str(target).startswith(str(pack_dir) + "/") and target != pack_dir:
        raise HTTPException(status_code=400, detail="Path traversal not allowed.")
    if target.suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File extension '{target.suffix}' not allowed. "
                f"Allowed: {sorted(_ALLOWED_EXTENSIONS)}"
            ),
        )
    return target


def _starter_pack_dir() -> Path:
    return config.data_dir / "swap_pack_starter"


def _documentation_pack_slug(name: str) -> str:
    slug = _PACK_SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(status_code=422, detail="Pack name must contain letters or numbers.")
    return slug[:100]


async def _plugin_pack_out(pack: PluginPack) -> PluginPackOut:
    """Return catalog data without exposing local installation paths."""
    decision = await pack_governance.evaluate(pack)
    personal = pack.owner_user_id is not None
    return PluginPackOut(
        id=pack.id,
        name=pack.name,
        manifest_name=pack.manifest_name,
        version=pack.version,
        description=pack.description,
        repo_url=strip_url_credentials(pack.repo_url) if pack.repo_url else None,
        repo_branch=pack.repo_branch,
        last_commit_sha=pack.last_commit_sha,
        auto_update=pack.auto_update,
        origin=decision.origin,
        certification_status=(
            "personal"
            if personal
            else (
                "certified"
                if decision.certified
                else (
                    "untrusted"
                    if pack.certification_status == "certified"
                    else pack.certification_status
                )
            )
        ),
        certification_provenance=decision.provenance or pack.certification_provenance,
        personal=personal,
        usable=decision.usable,
        mutable=decision.mutable,
        blocked_reason=decision.blocked_reason,
    )


# ---------------------------------------------------------------------------
# Caller identity and visibility
# ---------------------------------------------------------------------------


def _caller_user_id(request: Request) -> int:
    """The authenticated user's id. Personal packs are keyed on this."""
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def _visible_to(user_id: int):
    """SQLAlchemy predicate: shared packs plus this user's own packs.

    A personal pack is private. This predicate is the single place that decides
    what "private" means, so no listing or lookup can leak one by omission.
    """
    return sa_or(
        PluginPack.owner_user_id.is_(None),
        PluginPack.owner_user_id == user_id,
    )


async def _load_visible_pack(pack_id: int, user_id: int) -> PluginPack:
    async with async_session_factory() as db:
        pack = (
            await db.execute(
                select(PluginPack).where(
                    PluginPack.id == pack_id, _visible_to(user_id)
                )
            )
        ).scalar_one_or_none()
    if not pack:
        # Deliberately 404, not 403: whether someone else's private pack exists
        # is not the caller's business.
        raise HTTPException(status_code=404, detail="Plugin pack not found.")
    return pack


async def _load_own_pack(pack_id: int, user_id: int) -> PluginPack:
    """Load a pack the caller personally owns, for edit/delete/publish."""
    pack = await _load_visible_pack(pack_id, user_id)
    if pack.owner_user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "This is a shared pack. Shared packs change through a pull "
                "request against the pack repository. Create your own pack, or "
                "publish a copy of it."
            ),
        )
    return pack


def _personal_packs_root(user_id: int) -> Path:
    return config.data_dir / "plugin_packs" / "users" / str(user_id)


def _personal_pack_dir(user_id: int, slug: str) -> Path:
    return _personal_packs_root(user_id) / slug


def _governance_conflict(exc: PackGovernanceDenied) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _branch_slug(branch: str) -> str:
    return (
        branch.strip()
        .strip("/")
        .replace("/", "-")
        .replace("\\", "-")
        .replace(" ", "-")
        .lower()
    ) or "main"


def _repo_name_from_url(repo_url: str) -> str:
    parsed = urlparse(strip_url_credentials(repo_url) or repo_url)
    if parsed.path:
        return Path(parsed.path.rstrip("/")).name.removesuffix(".git") or "swap-pack"
    return Path(repo_url.rstrip("/")).name.removesuffix(".git") or "swap-pack"


def _clone_dir_for_repo_branch(repo_url: str, repo_branch: str) -> Path:
    repo_name = _repo_name_from_url(repo_url)
    branch_name = _branch_slug(repo_branch)
    digest_source = f"{strip_url_credentials(repo_url)}:{repo_branch}".encode()
    digest = hashlib.sha1(digest_source).hexdigest()[:10]
    return config.data_dir / "plugin_packs" / f"{repo_name}__{branch_name}__{digest}"


def _pack_registry_name(repo_url: str | None, repo_branch: str | None, manifest_name: str) -> str:
    if repo_url and repo_branch:
        return _branch_slug(repo_branch)[:100]
    return manifest_name[:100]


def _manifest_from_yaml_text(text: str) -> PluginPackManifest:
    with tempfile.TemporaryDirectory() as tmp_dir:
        pack_dir = Path(tmp_dir)
        (pack_dir / "plugin.yaml").write_text(text, encoding="utf-8")
        return load_plugin_manifest(pack_dir)


def _parse_remote_branch_refs(raw: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            branch, sha = line.split("\t", 1)
        except ValueError:
            continue
        branch = branch.strip()
        sha = sha.strip()
        if not branch or branch == "HEAD":
            continue
        refs.append((branch, sha))
    return refs


def _list_editable_pack_files(pack_dir: Path) -> list[str]:
    paths: list[str] = []
    root = pack_dir.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _ALLOWED_EXTENSIONS:
            continue
        rel_parts = path.relative_to(root).parts
        if ".git" in rel_parts or "__pycache__" in rel_parts:
            continue
        paths.append(path.relative_to(root).as_posix())
    return paths


def _pack_platform(files: dict[str, str]) -> str | None:
    prefix = "prompts/extensions/platforms/"
    platforms = {
        Path(path).stem
        for path in files
        if path.startswith(prefix) and Path(path).suffix == ".md"
    }
    return next(iter(platforms)) if len(platforms) == 1 else None


async def _replace_personal_pack_from_architect(
    pack: PluginPack,
    snapshot: pack_architect.PackArchitectSnapshot,
) -> PluginPackManifest:
    """Atomically replace editable Pack files while retaining its Git history."""
    pack_dir = Path(pack.path).resolve()
    if not pack_dir.is_dir():
        raise HTTPException(status_code=404, detail="Pack directory not found on disk.")
    parent = pack_dir.parent
    staged_parent = Path(tempfile.mkdtemp(prefix="pack-ai-refine-", dir=parent))
    staged = staged_parent / "pack"
    backup = parent / f".{pack_dir.name}-backup-{uuid.uuid4().hex}"
    swapped = False
    try:
        shutil.copytree(pack_dir, staged)
        for relative in _list_editable_pack_files(staged):
            _resolve_pack_file(str(staged), relative).unlink(missing_ok=True)
        (staged / "plugin.yaml").write_text(snapshot.manifest_yaml, encoding="utf-8")
        for relative, content in snapshot.files.items():
            target = _resolve_pack_file(str(staged), relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        manifest = load_plugin_manifest(staged)
        completion_errors = validate_generated_pack_files(
            manifest,
            snapshot.files,
            platform=snapshot.platform,
        )
        if completion_errors:
            raise HTTPException(
                status_code=422,
                detail="Pack is not ready to save: " + "; ".join(completion_errors),
            )
        await _git_commit_all(staged, "Refine pack with Pack Architect")

        os.replace(pack_dir, backup)
        try:
            os.replace(staged, pack_dir)
            swapped = True
        except OSError:
            os.replace(backup, pack_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return manifest
    finally:
        if not swapped and backup.exists() and not pack_dir.exists():
            os.replace(backup, pack_dir)
        shutil.rmtree(staged_parent, ignore_errors=True)


def _build_starter_zip() -> bytes:
    starter_dir = _starter_pack_dir()
    if not starter_dir.exists():
        raise HTTPException(status_code=404, detail="Starter pack skeleton is not available.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(starter_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(starter_dir).as_posix()
            zf.write(path, rel)
    return buffer.getvalue()


async def _git_output(cwd: str, *args: str, timeout: float = 120.0) -> str:
    return await run_git(cwd, *args, timeout=timeout, strip=True)


async def _clone_pack_branch_to(
    repo_url: str,
    repo_branch: str,
    target_dir: Path,
) -> tuple[Path, str]:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    await _git_output(
        str(target_dir.parent),
        "clone",
        "--depth",
        "1",
        "--branch",
        repo_branch,
        "--single-branch",
        repo_url,
        str(target_dir),
        timeout=180.0,
    )
    sha = await _git_output(str(target_dir), "rev-parse", "HEAD")
    return target_dir, sha


async def _clone_pack(repo_url: str, repo_branch: str) -> tuple[Path, str]:
    """Clone repo_url/repo_branch into data_dir/plugin_packs and return (path, sha)."""
    clone_dir = _clone_dir_for_repo_branch(repo_url, repo_branch)
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    return await _clone_pack_branch_to(repo_url, repo_branch, clone_dir)


async def _pull_pack(pack: PluginPack) -> str:
    """Run git pull in the pack's local clone and return the new HEAD SHA."""
    await pull_remote_ff_only(pack.path)
    return await _git_output(pack.path, "rev-parse", "HEAD")


async def _git_init_pack(pack_dir: Path) -> str:
    """Initialise a personal pack as a git repo and commit its contents."""
    await _git_output(str(pack_dir), "init", "-q")
    await _git_output(str(pack_dir), "config", "user.email", _COMMIT_EMAIL)
    await _git_output(str(pack_dir), "config", "user.name", _COMMIT_NAME)
    await _git_output(str(pack_dir), "add", "-A")
    await _git_output(str(pack_dir), "commit", "-q", "-m", "Create pack")
    return await _git_output(str(pack_dir), "rev-parse", "HEAD")


async def _git_commit_all(pack_dir: Path, message: str) -> str | None:
    """Commit any pending edits. Returns the new SHA, or None if nothing changed."""
    dirty = await _git_output(str(pack_dir), "status", "--porcelain")
    if not dirty:
        return None
    await _git_output(str(pack_dir), "add", "-A")
    await _git_output(str(pack_dir), "commit", "-q", "-m", message)
    return await _git_output(str(pack_dir), "rev-parse", "HEAD")


def _publish_branch_name(pack_name: str, requested: str | None) -> str:
    if requested:
        candidate = requested.strip().strip("/")
    else:
        candidate = f"{config.pack_publish_branch_prefix}{_branch_slug(pack_name)}"
    if not _BRANCH_NAME_RE.fullmatch(candidate):
        raise HTTPException(
            status_code=422,
            detail=(
                "Branch names may contain letters, numbers, '.', '_', '-' and '/', "
                "and may not begin or end with '/'."
            ),
        )
    return candidate


async def _pack_repo_push_token() -> str:
    """Resolve a token that can push to the shared pack repository.

    A GitHub App installation is preferred: its tokens are short-lived and
    scoped to the repository. A configured PAT is the fallback for installs that
    do not run the App. With neither, publishing is refused with instructions --
    silently skipping the push would leave the author believing they had shared
    their pack.
    """
    repo_url = config.swap_pack_starter_repo_url
    owner_repo = _owner_repo_from_url(repo_url)
    if github_app_configured() and owner_repo:
        owner = owner_repo.split("/", 1)[0]
        try:
            # `list_app_installations` returns NORMALIZED records --
            # {installation_id, account_login} -- not GitHub's raw shape. Reading
            # the raw shape here (installation["account"]["login"], ["id"]) found
            # nothing, silently fell through to the PAT branch, and reported
            # "publishing is not configured" on an instance where the App was
            # installed and working. Verified against the live App.
            for installation in await list_app_installations():
                account = str(installation.get("account_login") or "")
                if account.lower() == owner.lower():
                    # Returns the token string, not a (token, expiry) pair --
                    # unpacking it destructured the token's characters.
                    return await mint_installation_token(
                        int(installation["installation_id"])
                    )
            logger.warning(
                "GitHub App is configured but has no installation on '%s'; "
                "install it there or set MCP_PACK_REPO_TOKEN",
                owner,
            )
        except Exception as exc:  # pragma: no cover - network/credential dependent
            logger.warning("GitHub App token for pack publishing failed: %s", exc)

    if config.pack_repo_token:
        return config.pack_repo_token

    raise HTTPException(
        status_code=503,
        detail=(
            "Publishing is not configured on this instance. Install the GitHub "
            "App on the pack repository, or set MCP_PACK_REPO_TOKEN to a token "
            "with push access. Until then you can still export the pack and "
            "open the pull request by hand."
        ),
    )


def _is_credential_rejected(message: str) -> bool:
    """Whether a push failed because the credential was refused.

    ``run_git`` sanitizes before raising -- raw git stderr echoes the remote URL,
    which carries the token -- so the only signal available here is the
    sanitizer's bucket. That bucket cannot separate "invalid credential" from
    "valid credential without write access", so the message names both. Matching
    the raw GitHub wording too, for any caller that does reach this with it.
    """
    lowered = message.lower()
    return (
        "authentication_failed" in lowered
        or "denied to" in lowered
        or "403" in lowered
        or ("permission" in lowered and "denied" in lowered)
        or "write access to repository not granted" in lowered
    )


def _owner_repo_from_url(repo_url: str) -> str | None:
    """Extract ``owner/repo`` from a GitHub URL, or None if it is not one."""
    parsed = urlparse(strip_url_credentials(repo_url) or repo_url)
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


async def _open_pull_request(
    *,
    owner_repo: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    token: str,
) -> str | None:
    """Open the PR and return its URL, or None if GitHub declined.

    A failed PR is NOT a failed publish: the branch is already pushed, so the
    author can open the request themselves. Returning None lets the caller say
    that precisely instead of reporting an error over completed work.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://api.github.com/repos/{owner_repo}/pulls",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"title": title, "body": body, "head": branch, "base": base},
            )
        if response.status_code in (200, 201):
            return response.json().get("html_url")
        logger.warning(
            "Pull request for %s:%s was declined (%s): %s",
            owner_repo,
            branch,
            response.status_code,
            response.text[:400],
        )
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Pull request creation failed for %s: %s", branch, exc)
    return None


async def _discover_github_pack_branches(repo_url: str) -> list[PluginPackBranchOut]:
    """Clone the repo and return branches whose root contains a valid plugin.yaml."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        repo_dir = tmp_path / "repo"
        try:
            await _git_output(
                str(tmp_path),
                "clone",
                "--depth",
                "1",
                "--no-single-branch",
                repo_url,
                str(repo_dir),
                timeout=180.0,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not load GitHub branches: {exc}")

        refs_raw = await _git_output(
            str(repo_dir),
            "for-each-ref",
            "--format=%(refname:strip=3)%09%(objectname)",
            "refs/remotes/origin",
        )
        branches: list[PluginPackBranchOut] = []
        for branch, sha in _parse_remote_branch_refs(refs_raw):
            try:
                manifest_text = await _git_output(
                    str(repo_dir),
                    "show",
                    f"refs/remotes/origin/{branch}:plugin.yaml",
                )
                manifest = _manifest_from_yaml_text(manifest_text)
                source = pack_governance.require_configured_git_source(repo_url, branch)
            except Exception:
                continue
            if manifest.certification is None or manifest.certification.provenance != source.provenance:
                continue
            branches.append(
                PluginPackBranchOut(
                    branch=branch,
                    name=_pack_registry_name(repo_url, branch, manifest.name),
                    manifest_name=manifest.name,
                    version=manifest.version,
                    description=manifest.description or None,
                    repo_url=strip_url_credentials(repo_url) or repo_url,
                    commit_sha=sha,
                )
            )

    return sorted(branches, key=lambda b: (b.branch != "main", b.branch.lower()))


def _registered_pack_for_branch(
    packs: list[PluginPack],
    repo_url: str,
    repo_branch: str,
) -> PluginPack | None:
    repo_key = canonical_remote_key(repo_url)
    for pack in packs:
        if not pack.repo_url or canonical_remote_key(pack.repo_url) != repo_key:
            continue
        if (pack.repo_branch or "main") == repo_branch:
            return pack
    return None


# ---------------------------------------------------------------------------
# Static GitHub/starter routes
# ---------------------------------------------------------------------------


@router.get("/plugins/starter", response_model=StarterPackOut)
async def get_starter_pack():
    repo_url = config.swap_pack_starter_repo_url
    manifest: PluginPackManifest | None = None
    starter_dir = _starter_pack_dir()
    if starter_dir.exists():
        try:
            manifest = load_plugin_manifest(starter_dir)
        except Exception:
            manifest = None
    return StarterPackOut(
        repo_url=repo_url,
        clone_url=f"git clone {repo_url}",
        zip_url="/mcp/plugins/starter.zip",
        name=manifest.name if manifest else None,
        version=manifest.version if manifest else None,
        description=(manifest.description or None) if manifest else None,
    )


@router.get("/plugins/prompt-slots", response_model=list[PromptSlotOut])
async def list_prompt_slots():
    """Every prompt a pack can override, in pipeline order.

    Declared BEFORE the ``/plugins/{pack_id}/...`` routes: ``pack_id`` is typed
    ``int``, so "prompt-slots" could never bind to it, but keeping authoring
    endpoints above the id-scoped ones removes the question.

    Content is omitted here. An authoring UI needs the list on every render but
    a body only when the author asks to customize it. Platform-analysis entries
    are empty additive extension slots; Nexus never exposes its immutable core
    guide as replaceable pack content.
    """
    return [
        PromptSlotOut(
            path=slot.path,
            phase=slot.phase,
            kind=slot.kind,
            title=slot.title,
            description=slot.description,
            default_bytes=len(slot.default_content),
            has_default=slot.has_default,
        )
        for slot in prompt_slots()
    ]


@router.get("/plugins/prompt-slots/{slot_path:path}", response_model=PromptSlotContentOut)
async def read_prompt_slot(slot_path: str):
    """The built-in text for one slot, so an override can start from it.

    Without this an author overriding ``phase3_doc.md`` would be hand-writing
    7.7 KB of contract from nothing, and would silently drop the redaction and
    fabrication rules -- the override REPLACES the built-in body, so anything
    left out is simply gone.
    """
    slot = slot_for(slot_path)
    if slot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{slot_path}' is not a prompt the pipeline reads. "
                "GET /mcp/plugins/prompt-slots lists the ones it does."
            ),
        )
    return PromptSlotContentOut(
        path=slot.path,
        phase=slot.phase,
        kind=slot.kind,
        title=slot.title,
        description=slot.description,
        default_bytes=len(slot.default_content),
        has_default=slot.has_default,
        content=slot.default_content,
    )


@router.get("/plugins/starter.zip")
async def download_starter_pack():
    payload = _build_starter_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="swap-pack-starter.zip"'},
    )


@router.post("/plugins/discover", response_model=DiscoverPackBranchesOut)
async def discover_plugin_branches(body: DiscoverPackBranchesRequest, request: Request):
    await require_pack_management(request)
    repo_url = (body.repo_url or config.swap_pack_starter_repo_url).strip()
    if not repo_url:
        raise HTTPException(status_code=422, detail="repo_url is required.")
    try:
        pack_governance.require_configured_git_repository(repo_url)
    except PackGovernanceDenied as exc:
        raise _governance_conflict(exc)

    branches = await _discover_github_pack_branches(repo_url)
    async with async_session_factory() as db:
        packs = (
            await db.execute(select(PluginPack).where(PluginPack.repo_url.isnot(None)))
        ).scalars().all()
    for branch in branches:
        registered = _registered_pack_for_branch(packs, repo_url, branch.branch)
        if registered:
            branch.registered = True
            branch.registered_pack_id = registered.id

    return DiscoverPackBranchesOut(
        repo_url=strip_url_credentials(repo_url) or repo_url,
        branches=branches,
    )


@router.post("/plugins/branch-files", response_model=list[str])
async def list_plugin_branch_files(body: PreviewPackBranchFilesRequest, request: Request):
    await require_pack_management(request)
    try:
        pack_governance.require_configured_git_source(body.repo_url, body.repo_branch)
    except PackGovernanceDenied as exc:
        raise _governance_conflict(exc)
    with tempfile.TemporaryDirectory() as tmp_dir:
        pack_dir, sha = await _clone_pack_branch_to(
            body.repo_url,
            body.repo_branch,
            Path(tmp_dir) / "pack",
        )
        try:
            manifest = load_plugin_manifest(pack_dir)
            await pack_governance.certify_new_git_pack(
                repo_url=body.repo_url,
                repo_branch=body.repo_branch,
                pack_dir=pack_dir,
                commit_sha=sha,
                manifest=manifest,
            )
        except PackGovernanceDenied as exc:
            raise _governance_conflict(exc)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")
        return _list_editable_pack_files(pack_dir)


@router.post("/plugins/branch-file", response_model=FileContentOut)
async def read_plugin_branch_file(body: PreviewPackBranchFileRequest, request: Request):
    await require_pack_management(request)
    try:
        pack_governance.require_configured_git_source(body.repo_url, body.repo_branch)
    except PackGovernanceDenied as exc:
        raise _governance_conflict(exc)
    with tempfile.TemporaryDirectory() as tmp_dir:
        pack_dir, sha = await _clone_pack_branch_to(
            body.repo_url,
            body.repo_branch,
            Path(tmp_dir) / "pack",
        )
        try:
            manifest = load_plugin_manifest(pack_dir)
            await pack_governance.certify_new_git_pack(
                repo_url=body.repo_url,
                repo_branch=body.repo_branch,
                pack_dir=pack_dir,
                commit_sha=sha,
                manifest=manifest,
            )
        except PackGovernanceDenied as exc:
            raise _governance_conflict(exc)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")
        target = _resolve_pack_file(str(pack_dir), body.file_path)
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {body.file_path}")
        return FileContentOut(
            path=body.file_path,
            content=target.read_text(encoding="utf-8"),
        )


# ---------------------------------------------------------------------------
# Registration routes
# ---------------------------------------------------------------------------


@router.get("/plugins", response_model=list[PluginPackOut])
async def list_plugins(request: Request):
    """Shared packs plus the caller's own. Never anyone else's private packs."""
    user_id = _caller_user_id(request)
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(PluginPack)
                .where(_visible_to(user_id))
                .order_by(PluginPack.owner_user_id.isnot(None), PluginPack.name)
            )
        ).scalars().all()
        return [await _plugin_pack_out(r) for r in rows]


def _architect_out(
    snapshot: pack_architect.PackArchitectSnapshot,
) -> PackArchitectSessionOut:
    try:
        identity = _manifest_from_yaml_text(snapshot.manifest_yaml)
    except Exception:
        identity = None
    return PackArchitectSessionOut(
        id=snapshot.id,
        status=snapshot.status,
        revision=snapshot.revision,
        source_pack_id=snapshot.source_pack_id,
        platform=snapshot.platform,
        model_id=snapshot.model_id,
        name=identity.name if identity else None,
        description=(identity.description or None) if identity else None,
        messages=list(snapshot.messages),
        manifest_yaml=snapshot.manifest_yaml,
        files=snapshot.files,
        validation_errors=list(snapshot.validation_errors),
        complete=snapshot.complete,
        turn_status=snapshot.turn_status,
        turn_stage=snapshot.turn_stage,
        turn_detail=snapshot.turn_detail,
        turn_started_at=snapshot.turn_started_at,
        turn_finished_at=snapshot.turn_finished_at,
        turn_error=snapshot.turn_error,
    )


def _architect_http_error(exc: pack_architect.PackArchitectError) -> HTTPException:
    if isinstance(exc, pack_architect.PackArchitectNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, pack_architect.PackArchitectConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/plugins/architect/sessions",
    response_model=PackArchitectSessionOut,
    status_code=201,
)
async def create_pack_architect_session(
    body: CreatePackArchitectSessionRequest, request: Request
):
    """Start an owner-private workspace from the starter or an owned Pack."""
    try:
        user_id = _caller_user_id(request)
        manifest_yaml = body.manifest_yaml
        files = dict(body.files)
        platform = body.platform
        if body.pack_id is not None:
            if body.manifest_yaml is not None or body.files:
                raise pack_architect.PackArchitectError(
                    "Choose either pack_id or supplied Pack files, not both."
                )
            pack = await _load_own_pack(body.pack_id, user_id)
            pack_dir = Path(pack.path).resolve()
            paths = _list_editable_pack_files(pack_dir)
            if "plugin.yaml" not in paths:
                raise pack_architect.PackArchitectError("The selected Pack has no plugin.yaml.")
            manifest_yaml = (pack_dir / "plugin.yaml").read_text(encoding="utf-8")
            files = {
                path: _resolve_pack_file(str(pack_dir), path).read_text(encoding="utf-8")
                for path in paths
                if path != "plugin.yaml"
            }
            platform = platform or _pack_platform(files)
        return _architect_out(
            await pack_architect.create_session(
                user_id,
                source_pack_id=body.pack_id,
                platform=platform,
                manifest_yaml=manifest_yaml,
                files=files,
                inherit_starter=manifest_yaml is None and not files,
            )
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc


@router.get(
    "/plugins/architect/sessions/current",
    response_model=PackArchitectSessionOut,
)
async def get_current_pack_architect_session(request: Request):
    try:
        return _architect_out(
            await pack_architect.get_latest_session(_caller_user_id(request))
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc


@router.get(
    "/plugins/architect/sessions/{session_id}",
    response_model=PackArchitectSessionOut,
)
async def get_pack_architect_session(session_id: str, request: Request):
    try:
        return _architect_out(
            await pack_architect.get_session(session_id, _caller_user_id(request))
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc


@router.post(
    "/plugins/architect/sessions/{session_id}/messages",
    response_model=PackArchitectSessionOut,
    status_code=202,
)
async def run_pack_architect_turn(
    session_id: str, body: PackArchitectTurnRequest, request: Request
):
    try:
        return _architect_out(
            await pack_architect.run_turn(
                session_id,
                _caller_user_id(request),
                body.message,
                expected_revision=body.expected_revision,
            )
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc
    except Exception as exc:
        logger.warning("Pack Architect turn failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="The Pack Architect is unavailable. Your draft was not changed.",
        ) from exc


@router.post(
    "/plugins/architect/sessions/{session_id}/messages/cancel",
    response_model=PackArchitectSessionOut,
)
async def cancel_pack_architect_turn(
    session_id: str, body: PackArchitectAbandonRequest, request: Request
):
    try:
        return _architect_out(
            await pack_architect.cancel_turn(
                session_id,
                _caller_user_id(request),
                expected_revision=body.expected_revision,
            )
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc


@router.post(
    "/plugins/architect/sessions/{session_id}/save",
    response_model=PluginPackOut,
    status_code=201,
)
async def save_pack_architect_session(
    session_id: str, body: PackArchitectSaveRequest, request: Request
):
    user_id = _caller_user_id(request)
    try:
        snapshot = await pack_architect.get_session(session_id, user_id)
        if snapshot.status != "active":
            raise pack_architect.PackArchitectConflict(
                "This Pack Architect session is no longer active."
            )
        if snapshot.revision != body.expected_revision:
            raise pack_architect.PackArchitectConflict(
                "The Pack Architect session changed; reload it and retry."
            )
        manifest = pack_architect.manifest_for_snapshot(snapshot)
        if snapshot.source_pack_id is not None:
            pack = await _load_own_pack(snapshot.source_pack_id, user_id)
            manifest = await _replace_personal_pack_from_architect(pack, snapshot)
            async with async_session_factory() as db:
                row = (
                    await db.execute(
                        select(PluginPack).where(PluginPack.id == snapshot.source_pack_id)
                    )
                ).scalar_one_or_none()
                if row is None or row.owner_user_id != user_id:
                    raise HTTPException(status_code=404, detail="Plugin pack not found.")
                row.manifest_name = manifest.name
                row.version = manifest.version
                row.description = manifest.description or None
                await db.commit()
                await db.refresh(row)
                result = await _plugin_pack_out(row)
        else:
            result = await create_personal_pack(
                CreatePersonalPackRequest(
                    name=body.name,
                    description=manifest.description or None,
                    manifest_yaml=snapshot.manifest_yaml,
                    files=snapshot.files,
                    inherit_starter=False,
                    platform=snapshot.platform,
                ),
                request,
            )
        await pack_architect.mark_saved(
            session_id,
            user_id,
            expected_revision=snapshot.revision,
            pack_id=result.id,
        )
        return result
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc


@router.delete(
    "/plugins/architect/sessions/{session_id}",
    status_code=204,
)
async def abandon_pack_architect_session(
    session_id: str, body: PackArchitectAbandonRequest, request: Request
):
    try:
        await pack_architect.abandon_session(
            session_id,
            _caller_user_id(request),
            expected_revision=body.expected_revision,
        )
    except pack_architect.PackArchitectError as exc:
        raise _architect_http_error(exc) from exc
    return Response(status_code=204)


@router.post("/plugins/personal", response_model=PluginPackOut, status_code=201)
async def create_personal_pack(body: CreatePersonalPackRequest, request: Request):
    """Author a pack against the caller's own account.

    Every pack starts here. It is written to disk as a real pack tree with a git
    repository around it, which is what makes publishing later a push rather
    than an export: the thing the reviewer sees is the thing that ran.

    Authoring is open to any authenticated user -- the point of the personal
    lane is that you do not need workspace privileges to try an idea. Nothing
    here is visible to anyone else until it is published and merged.
    """
    user_id = _caller_user_id(request)
    slug = _documentation_pack_slug(body.name)

    # Manual creation starts from the whole starter. AI creation sends a
    # complete file set with inherit_starter=false; mixing those files with the
    # starter would re-introduce unrelated prompts and its SINGLE_SUMMARY
    # example into a custom pack.
    starter_files: dict[str, str] = {}
    starter_dir = _starter_pack_dir()
    should_inherit_starter = body.manifest_yaml is None or body.inherit_starter
    if should_inherit_starter and starter_dir.is_dir():
        for path in sorted(starter_dir.rglob("*")):
            if not path.is_file() or path.suffix not in _ALLOWED_EXTENSIONS:
                continue
            relative = path.relative_to(starter_dir)
            if relative.parts[0].startswith(".") or relative.name == "plugin.yaml":
                continue
            starter_files[relative.as_posix()] = path.read_text(encoding="utf-8")

    manifest_text = body.manifest_yaml
    if manifest_text is None:
        starter_manifest = starter_dir / "plugin.yaml"
        if not starter_manifest.is_file():
            raise HTTPException(
                status_code=503,
                detail="Starter pack skeleton is unavailable; supply manifest_yaml.",
            )
        manifest_text = starter_manifest.read_text(encoding="utf-8")

    # Validate before touching the filesystem: a pack that cannot load is not a
    # pack, and a half-written directory is worse than a rejected request.
    try:
        manifest = _manifest_from_yaml_text(manifest_text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid plugin.yaml: {exc}") from exc

    for rel_path in body.files:
        if Path(rel_path).suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"File '{rel_path}' has a disallowed extension. "
                    f"Allowed: {sorted(_ALLOWED_EXTENSIONS)}"
                ),
            )
        normalized = rel_path.replace("\\", "/").lstrip("/")
        if (
            normalized == "prompts/phase2_platform_sections.md"
            or normalized.startswith("prompts/phase2_platform_sections/")
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Nexus platform analysis prompts are immutable. Use additive "
                    "guidance under prompts/extensions/platforms/<platform>.md."
                ),
            )

    if not should_inherit_starter:
        completion_errors = validate_generated_pack_files(
            manifest,
            body.files,
            platform=body.platform.lower() if body.platform else None,
        )
        if completion_errors:
            raise HTTPException(
                status_code=422,
                detail="Incomplete generated pack: " + "; ".join(completion_errors),
            )

    async with async_session_factory() as db:
        clash = (
            await db.execute(
                select(PluginPack).where(
                    PluginPack.owner_user_id == user_id, PluginPack.name == slug
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"You already have a pack named '{slug}' (id={clash.id}).",
            )

    pack_dir = _personal_pack_dir(user_id, slug)
    if pack_dir.exists():
        shutil.rmtree(pack_dir, ignore_errors=True)
    pack_dir.mkdir(parents=True, exist_ok=True)
    try:
        (pack_dir / "plugin.yaml").write_text(manifest_text, encoding="utf-8")
        # Caller-supplied files win: an author who sends their own
        # prompts/phase2.md means it.
        for rel_path, content in {**starter_files, **body.files}.items():
            target = _resolve_pack_file(str(pack_dir), rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        # A git repo from the start: publishing is then a branch and a push, and
        # the author gets local history on their own edits for free.
        await _git_init_pack(pack_dir)
    except HTTPException:
        shutil.rmtree(pack_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(pack_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Could not create pack: {exc}") from exc

    async with async_session_factory() as db:
        pack = PluginPack(
            name=slug,
            owner_user_id=user_id,
            manifest_name=manifest.name,
            version=manifest.version,
            path=str(pack_dir),
            description=(body.description or manifest.description) or None,
            repo_url=None,
            repo_branch=None,
            last_commit_sha=None,
            auto_update=False,
            origin="personal",
            certification_status="personal",
            certification_provenance=None,
            content_sha256=None,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return await _plugin_pack_out(pack)


@router.post("/plugins", response_model=PluginPackOut, status_code=201)
async def register_plugin(body: RegisterPackRequest, request: Request):
    await require_pack_management(request)
    if body.path:
        raise HTTPException(status_code=403, detail="Local Pack registration is prohibited. Submit Pack changes through Git/PR.")
    if not body.repo_url:
        raise HTTPException(status_code=422, detail="A configured trusted Git repository is required.")

    repo_url: str | None = None
    repo_branch: str | None = None
    last_commit_sha: str | None = None

    repo_url = strip_url_credentials(body.repo_url.strip()) or body.repo_url.strip()
    repo_branch = (body.repo_branch or "main").strip() or "main"
    try:
        pack_governance.require_configured_git_source(repo_url, repo_branch)
    except PackGovernanceDenied as exc:
        raise _governance_conflict(exc)
    async with async_session_factory() as db:
        packs = (await db.execute(select(PluginPack).where(PluginPack.repo_url.isnot(None)))).scalars().all()
        existing = _registered_pack_for_branch(packs, repo_url, repo_branch)
        if existing:
            raise HTTPException(status_code=409, detail=f"Branch '{repo_branch}' is already registered as pack id={existing.id}.")
    try:
        pack_dir, last_commit_sha = await _clone_pack(repo_url, repo_branch)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone trusted Pack source: {exc}") from exc

    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail=f"Pack directory not found: {pack_dir}")

    try:
        manifest = load_plugin_manifest(pack_dir)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")

    try:
        decision = await pack_governance.certify_new_git_pack(
            repo_url=repo_url,
            repo_branch=repo_branch,
            pack_dir=pack_dir,
            commit_sha=last_commit_sha,
            manifest=manifest,
        )
    except PackGovernanceDenied as exc:
        shutil.rmtree(pack_dir, ignore_errors=True)
        raise _governance_conflict(exc)

    registry_name = _pack_registry_name(repo_url, repo_branch, manifest.name)

    async with async_session_factory() as db:
        existing_by_name = (
            await db.execute(select(PluginPack).where(PluginPack.name == registry_name))
        ).scalar_one_or_none()
        if existing_by_name:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A plugin pack named '{registry_name}' is already registered "
                    f"(id={existing_by_name.id})."
                ),
            )
        pack = PluginPack(
            name=registry_name,
            manifest_name=manifest.name,
            version=manifest.version,
            path=str(pack_dir),
            description=manifest.description or None,
            repo_url=repo_url,
            repo_branch=repo_branch,
            last_commit_sha=last_commit_sha,
            auto_update=bool(repo_url),
            origin=decision.origin,
            certification_status="certified",
            certification_provenance=decision.provenance,
            content_sha256=decision.content_sha256,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return await _plugin_pack_out(pack)


@router.post("/plugins/{pack_id}/publish", response_model=PublishPackOut)
async def publish_plugin(pack_id: int, body: PublishPackRequest, request: Request):
    """Offer a personal pack to everyone: push it as a branch, open its PR.

    This does not make the pack shared. It asks. A human reviewer on the pack
    repository decides by merging or closing the pull request; once merged, the
    branch shows up in discovery and anyone can register it. That separation is
    the whole point -- authoring stays open to everybody, and what everybody
    *receives* stays reviewed.
    """
    user_id = _caller_user_id(request)
    pack = await _load_own_pack(pack_id, user_id)

    pack_dir = Path(pack.path)
    if not (pack_dir / "plugin.yaml").is_file():
        raise HTTPException(status_code=404, detail="Pack content is missing on disk.")
    try:
        manifest = load_plugin_manifest(pack_dir)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Fix the pack before publishing it: {exc}",
        ) from exc

    repo_url = config.swap_pack_starter_repo_url
    owner_repo = _owner_repo_from_url(repo_url)
    branch = _publish_branch_name(pack.name, body.branch)
    token = await _pack_repo_push_token()

    # Commit any unsaved edits first, so the branch is exactly what the author
    # has been running -- not the state it was in when they last published.
    await _git_commit_all(pack_dir, f"Publish {pack.name} {manifest.version}")

    push_url = authenticated_url_plaintext(repo_url, token)
    try:
        await _git_output(str(pack_dir), "branch", "-f", branch, "HEAD")
        await _git_output(
            str(pack_dir), "push", "--force", push_url, f"{branch}:refs/heads/{branch}",
            timeout=180.0,
        )
    except Exception as exc:
        # Classify on the RAW message -- the sanitizer collapses this whole
        # class to "error_type: authentication_failed", which cannot distinguish
        # a bad credential from a read-only one, and those have different fixes.
        # Only the sanitized text is ever returned: raw git stderr echoes the
        # remote URL, which carries the token.
        rejected = _is_credential_rejected(str(exc))
        message = sanitize_git_error(str(exc))
        # A 403 here is the single most likely publish failure and it has one
        # specific cause: the credential can read the repository but not write
        # to it. Verified live -- the GitHub App shipped with `contents: read`,
        # so clone/discovery worked and only push failed. Reporting the raw git
        # error sends the operator looking for a network or branch problem.
        if rejected:
            raise HTTPException(
                status_code=403,
                detail=(
                    "The pack repository refused the credential. Nothing was "
                    "pushed. Most often the credential can read but not write: "
                    "grant the GitHub App 'Contents: Read and write' (plus "
                    "'Pull requests: Read and write' to open the request "
                    "automatically) and accept the permission update on the "
                    "installation. Otherwise the credential is invalid or "
                    "expired -- reinstall the App, or set MCP_PACK_REPO_TOKEN "
                    "to a token with push access."
                ),
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Could not push the pack branch: {message}",
        ) from exc
    sha = await _git_output(str(pack_dir), "rev-parse", "HEAD")

    pull_request_url: str | None = None
    note: str | None = None
    if owner_repo:
        pull_request_url = await _open_pull_request(
            owner_repo=owner_repo,
            branch=branch,
            base="main",
            title=body.title or f"Add pack: {manifest.name} {manifest.version}",
            body=body.body
            or (
                f"Proposed documentation pack `{manifest.name}` "
                f"v{manifest.version}.\n\n{manifest.description or ''}\n\n"
                "Merging this branch makes the pack available to every user."
            ),
            token=token,
        )
    if pull_request_url is None:
        note = (
            f"Branch '{branch}' is pushed. The pull request could not be opened "
            "automatically -- open it from the branch in the pack repository."
        )

    return PublishPackOut(
        branch=branch,
        repo_url=strip_url_credentials(repo_url) or repo_url,
        commit_sha=sha,
        pull_request_url=pull_request_url,
        note=note,
    )


@router.patch("/plugins/{pack_id}", response_model=PluginPackOut)
async def update_plugin(pack_id: int, body: PatchPackRequest, request: Request):
    await require_pack_management(request)
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
        if not pack:
            raise HTTPException(status_code=404, detail="Plugin pack not found.")
        if pack.owner_user_id is not None:
            raise HTTPException(
                status_code=422,
                detail="Personal packs have no upstream to pull from.",
            )

        try:
            await pack_governance.require_generation_authority(pack)
        except PackGovernanceDenied as exc:
            raise _governance_conflict(exc)

        if body.auto_update is not None:
            pack.auto_update = body.auto_update

        if body.pull:
            if not pack.repo_url:
                raise HTTPException(
                    status_code=422,
                    detail="Pack was not installed from a URL; cannot pull.",
                )
            try:
                new_sha = await _pull_pack(pack)
                pack.last_commit_sha = new_sha
                manifest = load_plugin_manifest(Path(pack.path))
                pack.manifest_name = manifest.name
                pack.version = manifest.version
                pack.description = manifest.description or None
                decision = await pack_governance.certify_new_git_pack(
                    repo_url=pack.repo_url,
                    repo_branch=pack.repo_branch or "main",
                    pack_dir=Path(pack.path),
                    commit_sha=new_sha,
                    manifest=manifest,
                )
                pack.origin = decision.origin
                pack.certification_status = "certified"
                pack.certification_provenance = decision.provenance
                pack.content_sha256 = decision.content_sha256
            except Exception as exc:
                if isinstance(exc, PackGovernanceDenied):
                    raise _governance_conflict(exc) from exc
                raise HTTPException(status_code=500, detail=f"git pull failed: {exc}")

        await db.commit()
        await db.refresh(pack)
        return await _plugin_pack_out(pack)


@router.delete("/plugins/{pack_id}", status_code=204)
async def delete_plugin(pack_id: int, request: Request):
    """Delete one of the caller's own packs. Shared packs are managed in git."""
    user_id = _caller_user_id(request)
    pack = await _load_own_pack(pack_id, user_id)
    pack_dir = Path(pack.path)

    async with async_session_factory() as db:
        # Any project still pointing at this pack must fall back to the built-in
        # document set rather than keep a dangling reference.
        await db.execute(
            select(Project).where(Project.plugin_pack_id == pack_id)
        )
        for project in (
            await db.execute(select(Project).where(Project.plugin_pack_id == pack_id))
        ).scalars().all():
            project.plugin_pack_id = None
        row = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
        await db.commit()

    # Content goes only after the row is gone: an orphaned row pointing at a
    # missing directory breaks listing, an orphaned directory costs disk.
    if _personal_packs_root(user_id) in pack_dir.parents:
        shutil.rmtree(pack_dir, ignore_errors=True)
    return Response(status_code=204)


@router.get("/plugins/{pack_id}/files", response_model=list[str])
async def list_plugin_files(pack_id: int, request: Request):
    """Return relative paths of all editable files in the pack directory."""
    pack = await _load_visible_pack(pack_id, _caller_user_id(request))

    pack_dir = Path(pack.path).resolve()
    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail="Pack directory not found on disk.")

    return _list_editable_pack_files(pack_dir)


@router.get("/plugins/{pack_id}/files/{file_path:path}", response_model=FileContentOut)
async def read_plugin_file(pack_id: int, file_path: str, request: Request):
    pack = await _load_visible_pack(pack_id, _caller_user_id(request))

    target = _resolve_pack_file(pack.path, file_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return FileContentOut(path=file_path, content=target.read_text(encoding="utf-8"))


@router.put("/plugins/{pack_id}/files/{file_path:path}", response_model=FileContentOut)
async def save_plugin_file(pack_id: int, file_path: str, body: SaveFileRequest, request: Request):
    """Edit a pack you own. Shared pack content changes through a pull request."""
    user_id = _caller_user_id(request)
    pack = await _load_own_pack(pack_id, user_id)

    target = _resolve_pack_file(pack.path, file_path)
    normalized_path = target.relative_to(Path(pack.path).resolve()).as_posix()
    if (
        normalized_path == "prompts/phase2_platform_sections.md"
        or normalized_path.startswith("prompts/phase2_platform_sections/")
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Nexus platform analysis prompts are immutable. Save additive "
                "guidance under prompts/extensions/platforms/<platform>.md."
            ),
        )

    if target.name == "plugin.yaml":
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
            tf.write(body.content)
            tf_path = Path(tf.name)
        test_dir = tf_path.parent / ("_validate_" + pack.name)
        try:
            test_dir.mkdir(exist_ok=True)
            shutil.copy(tf_path, test_dir / "plugin.yaml")
            load_plugin_manifest(test_dir)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin.yaml: {exc}")
        finally:
            tf_path.unlink(missing_ok=True)
            if test_dir.exists():
                shutil.rmtree(test_dir, ignore_errors=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    # Commit the edit so the author has history, and so publishing later pushes
    # exactly what they have been running.
    await _git_commit_all(Path(pack.path), f"Edit {file_path}")

    if target.name == "plugin.yaml":
        async with async_session_factory() as db:
            pack_row = (
                await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
            ).scalar_one_or_none()
            if pack_row:
                manifest = load_plugin_manifest(Path(pack.path))
                pack_row.manifest_name = manifest.name
                pack_row.version = manifest.version
                pack_row.description = manifest.description or None
                await db.commit()

    return FileContentOut(path=file_path, content=body.content)


@router.patch("/projects/{project_id}/plugin")
async def assign_plugin_to_project(project_id: str, body: AssignPluginRequest, request: Request):
    """Select the pack a project generates with.

    Any user with access to the project may select its pack. The pack must be
    one they can see -- a shared pack, or their own -- which is what keeps a
    personal pack private even though project access is broader.
    """
    await require_project_access(request, project_id)
    user_id = _caller_user_id(request)
    async with async_session_factory() as db:
        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found.")

        if body.plugin_pack_id is not None:
            pack = (
                await db.execute(
                    select(PluginPack).where(
                        PluginPack.id == body.plugin_pack_id, _visible_to(user_id)
                    )
                )
            ).scalar_one_or_none()
            if not pack:
                raise HTTPException(status_code=404, detail="Plugin pack not found.")
            try:
                await pack_governance.require_generation_authority(pack)
            except PackGovernanceDenied as exc:
                raise _governance_conflict(exc)

        project.plugin_pack_id = body.plugin_pack_id
        await db.commit()
    return {"project_id": project_id, "plugin_pack_id": body.plugin_pack_id}
