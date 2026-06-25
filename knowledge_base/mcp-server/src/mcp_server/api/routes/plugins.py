"""Plugin pack registry endpoints.

GET    /mcp/plugins                             — list registered packs
POST   /mcp/plugins/upload                      — upload a zip and register the pack
POST   /mcp/plugins/discover                    — discover valid pack branches in a GitHub repo
POST   /mcp/plugins                             — register a pack (local path or GitHub URL)
PATCH  /mcp/plugins/{id}                        — update auto_update flag or trigger a pull
DELETE /mcp/plugins/{id}                        — delete pack registration
GET    /mcp/plugins/{id}/files                  — list editable files in pack
GET    /mcp/plugins/{id}/files/{file_path}      — read a file
PUT    /mcp/plugins/{id}/files/{file_path}      — write a file
PATCH  /mcp/projects/{id}/plugin                — assign / unassign a pack to a project
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update

from mcp_server.config import config
from mcp_server.db.models import PluginPack, Project
from mcp_server.db.session import async_session_factory
from mcp_server.services.git_remote_service import canonical_remote_key, git_subprocess_env
from mcp_server.services.plugin_schema import PluginPackManifest, load_plugin_manifest
from mcp_server.utils.error_sanitizer import sanitize_git_error

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
    path: str
    description: str | None
    repo_url: str | None
    repo_branch: str | None = None
    last_commit_sha: str | None
    auto_update: bool

    model_config = {"from_attributes": True}


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


class StarterPackOut(BaseModel):
    zip_url: str
    repo_url: str | None
    clone_command: str | None


class DiscoverPackBranchesRequest(BaseModel):
    repo_url: str


class PreviewPackBranchFilesRequest(BaseModel):
    repo_url: str
    repo_branch: str


class PreviewPackBranchFileRequest(BaseModel):
    repo_url: str
    repo_branch: str
    file_path: str


class PluginPackBranchOut(BaseModel):
    branch: str
    commit_sha: str | None = None
    has_pack: bool
    valid: bool
    name: str | None = None
    version: str | None = None
    description: str | None = None
    registered_pack_id: int | None = None
    error: str | None = None


class DiscoverPackBranchesOut(BaseModel):
    repo_url: str
    branches: list[PluginPackBranchOut]


_ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt"}
_ZIP_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_PREVIEW_MAX_BYTES = 512 * 1024
_STARTER_DIRNAME = "swap_pack_starter"
_BRANCH_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _resolve_pack_file(pack_path: str, file_path: str) -> Path:
    """Return resolved Path inside pack_path, raising 400 on traversal or bad extension."""
    pack_dir = Path(pack_path).resolve()
    target = (pack_dir / file_path).resolve()
    if not str(target).startswith(str(pack_dir) + "/") and target != pack_dir:
        raise HTTPException(status_code=400, detail="Path traversal not allowed.")
    if target.suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{target.suffix}' not allowed. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )
    return target


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    """Return True when a zip member represents a Unix symlink."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _starter_pack_dir() -> Path:
    return config.data_dir / _STARTER_DIRNAME


def _build_starter_zip(starter_dir: Path) -> bytes:
    """Build the downloadable starter pack zip from a trusted skeleton directory."""
    if not starter_dir.exists() or not starter_dir.is_dir():
        raise FileNotFoundError(f"Starter pack directory not found: {starter_dir}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(starter_dir.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink():
                continue
            rel = path.relative_to(starter_dir).as_posix()
            zf.write(path, f"swap-pack-starter/{rel}")
    return buffer.getvalue()


def _list_editable_pack_files(pack_dir: Path) -> list[str]:
    """Return editable text files under a pack directory."""
    paths: list[str] = []
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file() or path.suffix not in _ALLOWED_EXTENSIONS:
            continue
        rel = path.relative_to(pack_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        paths.append(rel.as_posix())
    return paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _branch_slug(branch: str | None) -> str:
    slug = _BRANCH_SLUG_RE.sub("-", (branch or "default").strip()).strip(".-_")
    return (slug or "branch")[:72]


def _repo_name_from_url(repo_url: str) -> str:
    raw = repo_url.rstrip("/")
    name = raw.split("/")[-1].removesuffix(".git")
    return _BRANCH_SLUG_RE.sub("-", name).strip(".-_") or "swap-pack"


def _clone_dir_for_repo_branch(repo_url: str, repo_branch: str | None) -> Path:
    identity = f"{repo_url}|{repo_branch or ''}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
    repo_name = _repo_name_from_url(repo_url)
    branch_slug = _branch_slug(repo_branch)
    return config.data_dir / "plugin_packs" / f"{repo_name}__{branch_slug}__{digest}"


def _pack_registry_name(manifest_name: str, repo_branch: str | None) -> str:
    if not repo_branch:
        return manifest_name[:100]

    digest = hashlib.sha1(repo_branch.encode("utf-8")).hexdigest()[:8]
    branch_slug = _branch_slug(repo_branch)
    branch_label = branch_slug if len(branch_slug) <= 48 else f"{branch_slug[:39]}-{digest}"
    suffix = f"@{branch_label}"
    max_manifest = max(1, 100 - len(suffix))
    return f"{manifest_name[:max_manifest]}{suffix}"


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    raw = repo_url.strip()
    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
    else:
        parsed = urlparse(raw)
        if parsed.hostname != "github.com":
            raise HTTPException(status_code=422, detail="Only GitHub repository URLs are supported.")
        path = parsed.path.lstrip("/")

    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise HTTPException(status_code=422, detail="Provide a GitHub repository URL like https://github.com/org/repo.")
    return parts[0], parts[1]


def _manifest_from_yaml_text(content: str) -> PluginPackManifest:
    raw = yaml.safe_load(content)
    return PluginPackManifest.model_validate(raw)


def _parse_remote_branch_refs(refs_output: str) -> list[tuple[str, str | None]]:
    """Parse `git for-each-ref` output into branch names and commit SHAs."""
    branches: list[tuple[str, str | None]] = []
    for line in refs_output.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        branch_name, commit_sha = line.split("\t", 1)
        branch_name = branch_name.strip()
        if not branch_name or branch_name == "HEAD":
            continue
        branches.append((branch_name, commit_sha.strip() or None))
    return branches


async def _discover_github_pack_branches(repo_url: str) -> list[PluginPackBranchOut]:
    """Discover pack branches through Git, avoiding GitHub REST API rate limits."""
    _parse_github_owner_repo(repo_url)

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir) / "repo"
        try:
            await _git_output(
                tmp_dir,
                "clone",
                "--depth",
                "1",
                "--no-single-branch",
                repo_url,
                str(repo_dir),
            )
            refs_output = await _git_output(
                str(repo_dir),
                "for-each-ref",
                "--format=%(refname:strip=3)%09%(objectname)",
                "refs/remotes/origin",
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"GitHub branch discovery failed: {exc}",
            ) from exc

        results: list[PluginPackBranchOut] = []
        for branch_name, commit_sha in _parse_remote_branch_refs(refs_output):
            try:
                content = await _git_output(
                    str(repo_dir),
                    "show",
                    f"refs/remotes/origin/{branch_name}:plugin.yaml",
                )
            except RuntimeError:
                results.append(
                    PluginPackBranchOut(
                        branch=branch_name,
                        commit_sha=commit_sha,
                        has_pack=False,
                        valid=False,
                        error="No plugin.yaml at repository root.",
                    )
                )
                continue

            try:
                manifest = _manifest_from_yaml_text(content)
                results.append(
                    PluginPackBranchOut(
                        branch=branch_name,
                        commit_sha=commit_sha,
                        has_pack=True,
                        valid=True,
                        name=manifest.name,
                        version=manifest.version,
                        description=manifest.description or None,
                    )
                )
            except Exception as exc:
                results.append(
                    PluginPackBranchOut(
                        branch=branch_name,
                        commit_sha=commit_sha,
                        has_pack=True,
                        valid=False,
                        error=f"Invalid plugin.yaml: {exc}",
                    )
                )

    return results

async def _git_output(cwd: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env={**git_subprocess_env(), "HOME": os.environ.get("HOME", "/tmp")},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git {' '.join(args)} timed out")
    if proc.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(sanitize_git_error(detail or f"git {' '.join(args)} failed"))
    return stdout.decode().strip()


async def _clone_pack(repo_url: str, repo_branch: str | None = None) -> tuple[Path, str]:
    """Clone repo_url/branch into data_dir/plugin_packs/ and return (path, sha)."""
    clone_dir = _clone_dir_for_repo_branch(repo_url, repo_branch)
    if clone_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Clone directory already exists at {clone_dir}. Delete it or use a different name.",
        )
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    if repo_branch:
        await _git_output(
            str(clone_dir.parent),
            "clone",
            "--depth",
            "1",
            "--branch",
            repo_branch,
            repo_url,
            str(clone_dir),
        )
    else:
        await _git_output(str(clone_dir.parent), "clone", repo_url, str(clone_dir))
    sha = await _git_output(str(clone_dir), "rev-parse", "HEAD")
    return clone_dir, sha


async def _clone_pack_branch_to(repo_url: str, repo_branch: str, target_dir: Path) -> None:
    """Clone one public GitHub branch into target_dir for read-only previews."""
    _parse_github_owner_repo(repo_url)
    if not repo_branch.strip():
        raise HTTPException(status_code=422, detail="Provide 'repo_branch'.")
    try:
        await _git_output(
            str(target_dir.parent),
            "clone",
            "--depth",
            "1",
            "--branch",
            repo_branch,
            repo_url,
            str(target_dir),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub branch preview failed: {exc}",
        ) from exc


async def _pull_pack(pack: PluginPack) -> str:
    """Run git pull in the pack's local clone, return new HEAD SHA."""
    await _git_output(pack.path, "pull")
    return await _git_output(pack.path, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/plugins/upload", response_model=PluginPackOut, status_code=201)
async def upload_plugin(file: UploadFile = File(...)):
    """Accept a .zip upload, validate it, extract to data_dir/plugin_packs/<name>, register."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted.")

    data = await file.read()
    if len(data) > _ZIP_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Zip file exceeds 10 MB limit.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Not a valid zip file.")

    # Security: reject path traversal and non-allowed extensions inside the zip
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if ".." in parts or name.startswith("/"):
            raise HTTPException(status_code=400, detail=f"Unsafe path in zip: {name}")
        if _zip_member_is_symlink(info):
            raise HTTPException(
                status_code=400,
                detail=f"Symlinks are not allowed in zip: {name}",
            )
        if not name.endswith("/") and Path(name).suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Zip contains disallowed file type: {name}",
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        zf.extractall(tmp_dir)

        # Support both flat zips and zips with a single top-level directory
        tmp_path = Path(tmp_dir)
        top_level = list(tmp_path.iterdir())
        if len(top_level) == 1 and top_level[0].is_dir():
            extract_dir = top_level[0]
        else:
            extract_dir = tmp_path

        try:
            manifest = load_plugin_manifest(extract_dir)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")

        dest = config.data_dir / "plugin_packs" / manifest.name
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extract_dir, dest)

    async with async_session_factory() as db:
        existing = (
            await db.execute(select(PluginPack).where(PluginPack.name == manifest.name))
        ).scalar_one_or_none()
        if existing:
            # Update in place
            existing.manifest_name = manifest.name
            existing.version = manifest.version
            existing.path = str(dest)
            existing.description = manifest.description or None
            await db.commit()
            await db.refresh(existing)
            return PluginPackOut.model_validate(existing)

        pack = PluginPack(
            name=manifest.name,
            manifest_name=manifest.name,
            version=manifest.version,
            path=str(dest),
            description=manifest.description or None,
            repo_url=None,
            repo_branch=None,
            last_commit_sha=None,
            auto_update=False,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return PluginPackOut.model_validate(pack)


@router.get("/plugins/starter", response_model=StarterPackOut)
async def get_starter_pack():
    """Return clone/download metadata for the public Swap Pack starter."""
    repo_url = config.swap_pack_starter_repo_url.strip() or None
    return StarterPackOut(
        zip_url="/mcp/plugins/starter.zip",
        repo_url=repo_url,
        clone_command=f"git clone {repo_url}" if repo_url else None,
    )


@router.get("/plugins/starter.zip")
async def download_starter_pack():
    """Return a starter Swap Pack authoring kit as a zip archive."""
    try:
        payload = _build_starter_zip(_starter_pack_dir())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="swap-pack-starter.zip"',
        },
    )


@router.post("/plugins/discover", response_model=DiscoverPackBranchesOut)
async def discover_plugin_branches(body: DiscoverPackBranchesRequest):
    """Return GitHub branches and whether each branch contains a valid Swap Pack."""
    repo_url = body.repo_url.strip()
    if not repo_url:
        raise HTTPException(status_code=422, detail="Provide 'repo_url'.")

    branches = await _discover_github_pack_branches(repo_url)
    clone_key = canonical_remote_key(repo_url)

    async with async_session_factory() as db:
        rows = (
            await db.execute(select(PluginPack).where(PluginPack.repo_url.isnot(None)))
        ).scalars().all()

    registered_by_branch: dict[str | None, int] = {}
    for pack in rows:
        if pack.repo_url and canonical_remote_key(pack.repo_url) == clone_key:
            registered_by_branch[pack.repo_branch] = pack.id

    for branch in branches:
        branch.registered_pack_id = registered_by_branch.get(branch.branch)

    return DiscoverPackBranchesOut(repo_url=repo_url, branches=branches)


@router.post("/plugins/branch-files", response_model=list[str])
async def list_plugin_branch_files(body: PreviewPackBranchFilesRequest):
    """Return editable files from an unregistered GitHub branch."""
    repo_url = body.repo_url.strip()
    repo_branch = body.repo_branch.strip()
    if not repo_url or not repo_branch:
        raise HTTPException(status_code=422, detail="Provide 'repo_url' and 'repo_branch'.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        pack_dir = Path(tmp_dir) / "pack"
        await _clone_pack_branch_to(repo_url, repo_branch, pack_dir)
        try:
            load_plugin_manifest(pack_dir)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}") from exc
        return _list_editable_pack_files(pack_dir)


@router.post("/plugins/branch-file", response_model=FileContentOut)
async def read_plugin_branch_file(body: PreviewPackBranchFileRequest):
    """Read one editable file from an unregistered GitHub branch."""
    repo_url = body.repo_url.strip()
    repo_branch = body.repo_branch.strip()
    if not repo_url or not repo_branch:
        raise HTTPException(status_code=422, detail="Provide 'repo_url' and 'repo_branch'.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        pack_dir = Path(tmp_dir) / "pack"
        await _clone_pack_branch_to(repo_url, repo_branch, pack_dir)
        target = _resolve_pack_file(str(pack_dir), body.file_path)
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {body.file_path}")
        if target.stat().st_size > _PREVIEW_MAX_BYTES:
            raise HTTPException(status_code=413, detail="File is too large to preview.")
        return FileContentOut(
            path=body.file_path,
            content=target.read_text(encoding="utf-8"),
        )


@router.get("/plugins", response_model=list[PluginPackOut])
async def list_plugins():
    async with async_session_factory() as db:
        rows = (await db.execute(select(PluginPack).order_by(PluginPack.name))).scalars().all()
        return [PluginPackOut.model_validate(r) for r in rows]


@router.post("/plugins", response_model=PluginPackOut, status_code=201)
async def register_plugin(body: RegisterPackRequest):
    if not body.path and not body.repo_url:
        raise HTTPException(status_code=422, detail="Provide either 'path' or 'repo_url'.")
    if body.repo_branch and not body.repo_url:
        raise HTTPException(status_code=422, detail="'repo_branch' can only be used with 'repo_url'.")

    repo_url: str | None = None
    repo_branch: str | None = body.repo_branch.strip() if body.repo_branch else None
    last_commit_sha: str | None = None
    cloned_from_repo = False

    if body.repo_url:
        repo_url = body.repo_url.strip()
        _parse_github_owner_repo(repo_url)
        clone_key = canonical_remote_key(repo_url)
        async with async_session_factory() as db:
            rows = (
                await db.execute(select(PluginPack).where(PluginPack.repo_url.isnot(None)))
            ).scalars().all()
        for existing_pack in rows:
            if (
                existing_pack.repo_url
                and canonical_remote_key(existing_pack.repo_url) == clone_key
                and existing_pack.repo_branch == repo_branch
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Branch '{repo_branch or 'default'}' is already registered "
                        f"(id={existing_pack.id})."
                    ),
                )
        pack_dir, last_commit_sha = await _clone_pack(repo_url, repo_branch)
        cloned_from_repo = True
    else:
        pack_dir = Path(body.path)  # type: ignore[arg-type]

    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail=f"Pack directory not found: {pack_dir}")

    try:
        manifest = load_plugin_manifest(pack_dir)
    except (FileNotFoundError, Exception) as exc:
        if cloned_from_repo:
            shutil.rmtree(pack_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")

    async with async_session_factory() as db:
        registry_name = _pack_registry_name(manifest.name, repo_branch)
        existing = (
            await db.execute(select(PluginPack).where(PluginPack.name == registry_name))
        ).scalar_one_or_none()
        if existing:
            if cloned_from_repo:
                shutil.rmtree(pack_dir, ignore_errors=True)
            raise HTTPException(
                status_code=409,
                detail=f"A plugin pack named '{registry_name}' is already registered (id={existing.id}).",
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
            auto_update=True,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return PluginPackOut.model_validate(pack)


@router.patch("/plugins/{pack_id}", response_model=PluginPackOut)
async def update_plugin(pack_id: int, body: PatchPackRequest):
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
        if not pack:
            raise HTTPException(status_code=404, detail="Plugin pack not found.")

        if body.auto_update is not None:
            pack.auto_update = body.auto_update

        if body.pull:
            if not pack.repo_url:
                raise HTTPException(status_code=422, detail="Pack was not installed from a URL; cannot pull.")
            try:
                new_sha = await _pull_pack(pack)
                pack.last_commit_sha = new_sha
                # Reload manifest in case version changed
                manifest = load_plugin_manifest(Path(pack.path))
                pack.manifest_name = manifest.name
                pack.name = _pack_registry_name(manifest.name, pack.repo_branch)
                pack.version = manifest.version
                pack.description = manifest.description or None
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"git pull failed: {exc}")

        await db.commit()
        await db.refresh(pack)
        return PluginPackOut.model_validate(pack)


@router.delete("/plugins/{pack_id}", status_code=204)
async def delete_plugin(pack_id: int):
    """Unassign pack from all projects and remove DB registration. Files are left on disk."""
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
        if not pack:
            raise HTTPException(status_code=404, detail="Plugin pack not found.")

        await db.execute(
            update(Project)
            .where(Project.plugin_pack_id == pack_id)
            .values(plugin_pack_id=None)
        )
        await db.delete(pack)
        await db.commit()


@router.get("/plugins/{pack_id}/files", response_model=list[str])
async def list_plugin_files(pack_id: int):
    """Return relative paths of all editable files (.yaml, .yml, .md) in the pack directory."""
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Plugin pack not found.")

    pack_dir = Path(pack.path).resolve()
    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail="Pack directory not found on disk.")

    return _list_editable_pack_files(pack_dir)


@router.get("/plugins/{pack_id}/files/{file_path:path}", response_model=FileContentOut)
async def read_plugin_file(pack_id: int, file_path: str):
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Plugin pack not found.")

    target = _resolve_pack_file(pack.path, file_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    return FileContentOut(path=file_path, content=target.read_text(encoding="utf-8"))


@router.put("/plugins/{pack_id}/files/{file_path:path}", response_model=FileContentOut)
async def save_plugin_file(pack_id: int, file_path: str, body: SaveFileRequest):
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Plugin pack not found.")

    target = _resolve_pack_file(pack.path, file_path)

    # Validate plugin.yaml before writing
    if target.name == "plugin.yaml":
        import tempfile as _tmp
        with _tmp.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
            tf.write(body.content)
            tf_path = Path(tf.name)
        try:
            import shutil as _shutil
            test_dir = tf_path.parent / ("_validate_" + pack.name)
            test_dir.mkdir(exist_ok=True)
            _shutil.copy(tf_path, test_dir / "plugin.yaml")
            load_plugin_manifest(test_dir)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin.yaml: {exc}")
        finally:
            tf_path.unlink(missing_ok=True)
            if test_dir.exists():
                _shutil.rmtree(test_dir, ignore_errors=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")

    # Refresh pack version/description from plugin.yaml if it was just saved
    if target.name == "plugin.yaml":
        async with async_session_factory() as db:
            pack_row = (
                await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
            ).scalar_one_or_none()
            if pack_row:
                try:
                    manifest = load_plugin_manifest(Path(pack.path))
                    pack_row.manifest_name = manifest.name
                    pack_row.name = _pack_registry_name(manifest.name, pack_row.repo_branch)
                    pack_row.version = manifest.version
                    pack_row.description = manifest.description or None
                    await db.commit()
                except Exception:
                    pass

    return FileContentOut(path=file_path, content=body.content)


@router.patch("/projects/{project_id}/plugin")
async def assign_plugin_to_project(project_id: str, body: AssignPluginRequest):
    async with async_session_factory() as db:
        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found.")

        if body.plugin_pack_id is not None:
            pack = (
                await db.execute(select(PluginPack).where(PluginPack.id == body.plugin_pack_id))
            ).scalar_one_or_none()
            if not pack:
                raise HTTPException(status_code=404, detail="Plugin pack not found.")

        project.plugin_pack_id = body.plugin_pack_id
        await db.commit()
    return {"project_id": project_id, "plugin_pack_id": body.plugin_pack_id}
