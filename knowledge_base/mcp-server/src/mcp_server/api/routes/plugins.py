"""Plugin pack registry endpoints.

GET    /mcp/plugins                             — list registered packs
POST   /mcp/plugins/upload                      — upload a zip and register the pack
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
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, update

from mcp_server.config import config
from mcp_server.db.models import PluginPack, Project
from mcp_server.db.session import async_session_factory
from mcp_server.services.plugin_schema import load_plugin_manifest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plugins"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PluginPackOut(BaseModel):
    id: int
    name: str
    version: str
    path: str
    description: str | None
    repo_url: str | None
    last_commit_sha: str | None
    auto_update: bool

    model_config = {"from_attributes": True}


class RegisterPackRequest(BaseModel):
    path: str | None = None
    repo_url: str | None = None


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
_ZIP_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _git_output(cwd: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git {' '.join(args)} timed out")
    if proc.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return stdout.decode().strip()


async def _clone_pack(repo_url: str) -> tuple[Path, str]:
    """Clone repo_url into data_dir/plugin_packs/<name>/ and return (path, sha)."""
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    clone_dir = config.data_dir / "plugin_packs" / name
    if clone_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Clone directory already exists at {clone_dir}. Delete it or use a different name.",
        )
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    await _git_output(str(clone_dir.parent), "clone", repo_url, str(clone_dir))
    sha = await _git_output(str(clone_dir), "rev-parse", "HEAD")
    return clone_dir, sha


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
    for name in zf.namelist():
        parts = Path(name).parts
        if ".." in parts or name.startswith("/"):
            raise HTTPException(status_code=400, detail=f"Unsafe path in zip: {name}")
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
            existing.version = manifest.version
            existing.path = str(dest)
            existing.description = manifest.description or None
            await db.commit()
            await db.refresh(existing)
            return PluginPackOut.model_validate(existing)

        pack = PluginPack(
            name=manifest.name,
            version=manifest.version,
            path=str(dest),
            description=manifest.description or None,
            repo_url=None,
            last_commit_sha=None,
            auto_update=False,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return PluginPackOut.model_validate(pack)


@router.get("/plugins", response_model=list[PluginPackOut])
async def list_plugins():
    async with async_session_factory() as db:
        rows = (await db.execute(select(PluginPack).order_by(PluginPack.name))).scalars().all()
        return [PluginPackOut.model_validate(r) for r in rows]


@router.post("/plugins", response_model=PluginPackOut, status_code=201)
async def register_plugin(body: RegisterPackRequest):
    if not body.path and not body.repo_url:
        raise HTTPException(status_code=422, detail="Provide either 'path' or 'repo_url'.")

    repo_url: str | None = None
    last_commit_sha: str | None = None

    if body.repo_url:
        pack_dir, last_commit_sha = await _clone_pack(body.repo_url)
        repo_url = body.repo_url
    else:
        pack_dir = Path(body.path)  # type: ignore[arg-type]

    if not pack_dir.exists():
        raise HTTPException(status_code=404, detail=f"Pack directory not found: {pack_dir}")

    try:
        manifest = load_plugin_manifest(pack_dir)
    except (FileNotFoundError, Exception) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid plugin pack: {exc}")

    async with async_session_factory() as db:
        existing = (
            await db.execute(select(PluginPack).where(PluginPack.name == manifest.name))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A plugin pack named '{manifest.name}' is already registered (id={existing.id}).",
            )
        pack = PluginPack(
            name=manifest.name,
            version=manifest.version,
            path=str(pack_dir),
            description=manifest.description or None,
            repo_url=repo_url,
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

    paths: list[str] = []
    for p in sorted(pack_dir.rglob("*")):
        if p.is_file() and p.suffix in _ALLOWED_EXTENSIONS and not any(part.startswith(".") for part in p.parts):
            paths.append(p.relative_to(pack_dir).as_posix())
    return paths


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
