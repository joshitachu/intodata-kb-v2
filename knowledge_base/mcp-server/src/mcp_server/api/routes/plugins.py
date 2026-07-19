"""Plugin pack registry endpoints.

GET    /mcp/plugins                             - list registered packs
GET    /mcp/plugins/starter                     - starter repo metadata
GET    /mcp/plugins/starter.zip                 - download local starter skeleton
POST   /mcp/plugins/discover                    - discover valid pack branches
POST   /mcp/plugins/branch-files                - list editable files in a branch
POST   /mcp/plugins/branch-file                 - preview one branch file
POST   /mcp/plugins/upload                      - retired (ZIP provenance is not certifiable)
POST   /mcp/plugins                             - register a configured Git Pack source
POST   /mcp/documentation-packs                 - retired (use Git/PR)
PATCH  /mcp/plugins/{id}                        - pull a certified Git Pack only
DELETE /mcp/plugins/{id}                        - retired (Git/PR lifecycle)
GET    /mcp/plugins/{id}/files                  - list editable files in pack
GET    /mcp/plugins/{id}/files/{file_path}      - read a file
PUT    /mcp/plugins/{id}/files/{file_path}      - write a file
PATCH  /mcp/projects/{id}/plugin                - assign / unassign a pack to a project
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from stat import S_IFLNK, S_IFMT
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from mcp_server.config import config
from mcp_server.db.models import PluginPack, Project
from mcp_server.db.session import async_session_factory
from mcp_server.services.git_remote_service import (
    canonical_remote_key,
    pull_remote_ff_only,
    run_git,
    strip_url_credentials,
)
from mcp_server.services.plugin_schema import PluginPackManifest, load_plugin_manifest
from mcp_server.services.pack_governance import (
    PackGovernanceDenied,
    pack_governance,
)
from mcp_server.api.permissions import require_pack_management, require_project_management

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
    mutable: bool
    blocked_reason: str | None = None

    model_config = {"from_attributes": True}


class StarterPackOut(BaseModel):
    repo_url: str
    clone_url: str
    zip_url: str
    name: str | None = None
    version: str | None = None
    description: str | None = None


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


class DocumentationPackDocumentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    sections: list[str] = Field(default_factory=list, max_length=30)
    instructions: str | None = Field(default=None, max_length=8_000)


class CreateDocumentationPackRequest(BaseModel):
    name: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    description: str | None = Field(default=None, max_length=1_000)
    documents: list[DocumentationPackDocumentRequest] = Field(min_length=1, max_length=30)


class FileContentOut(BaseModel):
    path: str
    content: str


class SaveFileRequest(BaseModel):
    content: str


_ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md", ".txt"}
_ZIP_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_PACK_SLUG_RE = re.compile(r"[^a-z0-9]+")


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
            "certified"
            if decision.certified
            else ("untrusted" if pack.certification_status == "certified" else pack.certification_status)
        ),
        certification_provenance=decision.provenance or pack.certification_provenance,
        mutable=decision.mutable,
        blocked_reason=decision.blocked_reason,
    )


def _governance_conflict(exc: PackGovernanceDenied) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _retired_mutation() -> HTTPException:
    return HTTPException(
        status_code=410,
        detail="API Pack creation/import is retired. Submit Pack changes through a configured Git repository and PR.",
    )


def _normalized_document_definition(
    document: DocumentationPackDocumentRequest,
) -> tuple[dict[str, object], str | None]:
    name = document.name.strip().upper().replace("-", "_")
    sections = list(dict.fromkeys(section.strip() for section in document.sections if section.strip()))
    definition: dict[str, object] = {"name": name}
    if sections:
        definition["required_sections"] = sections
    instructions = document.instructions.strip() if document.instructions else None
    return definition, instructions or None


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


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return S_IFMT(info.external_attr >> 16) == S_IFLNK


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


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if ".." in parts or name.startswith("/"):
            raise HTTPException(status_code=400, detail=f"Unsafe path in zip: {name}")
        if _zip_member_is_symlink(info):
            raise HTTPException(status_code=400, detail=f"Zip contains symlink: {name}")
        if not name.endswith("/") and Path(name).suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Zip contains disallowed file type: {name}",
            )


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


@router.post(
    "/documentation-packs",
    response_model=PluginPackOut,
    status_code=201,
)
async def create_documentation_pack(
    request: Request,
):
    """Retired: Foundation/Functional Packs are certified through Git/PR."""
    await require_pack_management(request)
    raise _retired_mutation()


@router.post("/plugins/upload", response_model=PluginPackOut, status_code=201)
async def upload_plugin(request: Request):
    """Retired: ZIP imports cannot establish governed Pack provenance."""
    await require_pack_management(request)
    raise _retired_mutation()


@router.get("/plugins", response_model=list[PluginPackOut])
async def list_plugins():
    async with async_session_factory() as db:
        rows = (await db.execute(select(PluginPack).order_by(PluginPack.name))).scalars().all()
        return [await _plugin_pack_out(r) for r in rows]


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


@router.patch("/plugins/{pack_id}", response_model=PluginPackOut)
async def update_plugin(pack_id: int, body: PatchPackRequest, request: Request):
    await require_pack_management(request)
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
        if not pack:
            raise HTTPException(status_code=404, detail="Plugin pack not found.")

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
    await require_pack_management(request)
    raise HTTPException(status_code=410, detail="Pack deregistration is retired. Manage certified Pack lifecycle through Git/PR deployment.")


@router.get("/plugins/{pack_id}/files", response_model=list[str])
async def list_plugin_files(pack_id: int):
    """Return relative paths of all editable files in the pack directory."""
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
async def save_plugin_file(pack_id: int, file_path: str, body: SaveFileRequest, request: Request):
    await require_pack_management(request)
    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
        ).scalar_one_or_none()
    if not pack:
        raise HTTPException(status_code=404, detail="Plugin pack not found.")

    decision = await pack_governance.evaluate(pack)
    if decision.certified:
        raise HTTPException(status_code=403, detail="Certified Pack content is immutable in Nexus. Submit changes through Git/PR.")

    target = _resolve_pack_file(pack.path, file_path)

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

    async with async_session_factory() as db:
        pack_row = (await db.execute(select(PluginPack).where(PluginPack.id == pack_id))).scalar_one_or_none()
        if pack_row:
            pack_row.origin = "edited"
            pack_row.certification_status = "untrusted"
            pack_row.certification_provenance = None
            pack_row.content_sha256 = None
            await db.commit()

    if target.name == "plugin.yaml":
        async with async_session_factory() as db:
            pack_row = (
                await db.execute(select(PluginPack).where(PluginPack.id == pack_id))
            ).scalar_one_or_none()
            if pack_row:
                try:
                    manifest = load_plugin_manifest(Path(pack.path))
                    pack_row.manifest_name = manifest.name
                    pack_row.version = manifest.version
                    pack_row.description = manifest.description or None
                    await db.commit()
                except Exception:
                    pass

    return FileContentOut(path=file_path, content=body.content)


@router.patch("/projects/{project_id}/plugin")
async def assign_plugin_to_project(project_id: str, body: AssignPluginRequest, request: Request):
    await require_project_management(request, project_id)
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
            try:
                await pack_governance.require_generation_authority(pack)
            except PackGovernanceDenied as exc:
                raise _governance_conflict(exc)

        project.plugin_pack_id = body.plugin_pack_id
        await db.commit()
    return {"project_id": project_id, "plugin_pack_id": body.plugin_pack_id}
