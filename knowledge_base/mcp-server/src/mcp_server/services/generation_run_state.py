"""Generation lock and manifest helpers for docs_staging."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class GenerationLockError(RuntimeError):
    """Raised when docs_staging is owned by another active generation."""


class GenerationReceiptError(RuntimeError):
    """Raised when a resumed run attempts to replace immutable receipt data."""


def lock_path(staging_dir: Path) -> Path:
    return staging_dir / ".generation.lock"


def manifest_path(staging_dir: Path) -> Path:
    return staging_dir / "_run_manifest.json"


def nexus_receipt_path(staging_dir: Path) -> Path:
    return staging_dir / "_audit" / "nexus_pack_execution_receipt.json"


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def acquire_generation_lock(
    staging_dir: Path,
    *,
    project_id: str,
    run_id: int | None,
    stale_after_seconds: int,
) -> dict[str, Any]:
    """Create a per-staging lock, refusing to overwrite a fresh active lock."""

    staging_dir.mkdir(parents=True, exist_ok=True)
    path = lock_path(staging_dir)
    now = time.time()
    existing = read_json_file(path)
    if existing:
        existing_run_id = existing.get("run_id")
        age = now - float(existing.get("created_at") or existing.get("updated_at") or 0)
        if existing_run_id != run_id and age < stale_after_seconds:
            raise GenerationLockError(
                f"active generation lock exists for project {project_id}: "
                f"run_id={existing_run_id} age_seconds={int(age)}"
            )

    data = {
        "project_id": project_id,
        "run_id": run_id,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "created_at": existing.get("created_at") if existing and existing.get("run_id") == run_id else now,
        "updated_at": now,
        "stale_after_seconds": stale_after_seconds,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def refresh_generation_lock(staging_dir: Path, *, run_id: int | None) -> None:
    data = read_json_file(lock_path(staging_dir)) or {}
    if data and data.get("run_id") not in {run_id, None}:
        return
    data["run_id"] = run_id
    data["updated_at"] = time.time()
    lock_path(staging_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")


def release_generation_lock(staging_dir: Path, *, run_id: int | None) -> None:
    path = lock_path(staging_dir)
    data = read_json_file(path)
    if not data or data.get("run_id") == run_id:
        path.unlink(missing_ok=True)


def write_run_manifest(
    staging_dir: Path,
    *,
    run_id: int | None,
    project_id: str,
    phase: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    current = read_json_file(manifest_path(staging_dir)) or {}
    phases = current.get("phases")
    if not isinstance(phases, dict):
        phases = {}
    phases[phase] = {"status": status, "updated_at": time.time(), **(extra or {})}
    current.update({
        "run_id": run_id,
        "project_id": project_id,
        "status": status,
        "current_phase": phase,
        "updated_at": time.time(),
        "phases": phases,
    })
    current.setdefault("created_at", time.time())
    manifest_path(staging_dir).write_text(json.dumps(current, indent=2), encoding="utf-8")


def persist_nexus_execution_receipt(
    staging_dir: Path,
    receipt: dict[str, object],
) -> None:
    """Persist the exact governed stack contract once in manifest and audit receipt."""
    current = read_json_file(manifest_path(staging_dir)) or {}
    existing_manifest_receipt = current.get("nexus_execution_contract")
    if existing_manifest_receipt is not None and existing_manifest_receipt != receipt:
        raise GenerationReceiptError("Nexus execution contract cannot change during a resumed run")
    current["nexus_execution_contract"] = receipt

    receipt_path = nexus_receipt_path(staging_dir)
    existing_file_receipt = read_json_file(receipt_path)
    if existing_file_receipt is not None and existing_file_receipt != receipt:
        raise GenerationReceiptError("Nexus execution receipt cannot be replaced during a resumed run")

    manifest_path(staging_dir).write_text(json.dumps(current, indent=2), encoding="utf-8")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if existing_file_receipt is None:
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def persist_generation_profile(staging_dir: Path, profile: dict | None, *, resume: bool) -> None:
    """Bind resume artifacts to their original contract, including legacy NULL."""
    from mcp_server.services.generation_profile import (
        assert_profile_matches, normalize_profile, profile_digest,
    )

    current = read_json_file(manifest_path(staging_dir)) or {}
    receipt_path = staging_dir / "_audit" / "generation_profile.json"
    receipt = read_json_file(receipt_path)
    if resume:
        # Old staging without a profile is legacy, never implicitly upgraded.
        assert_profile_matches(current.get("generation_profile"), profile)
        if receipt is not None:
            assert_profile_matches(receipt.get("generation_profile"), profile)
            if receipt.get("digest") != profile_digest(profile):
                raise GenerationReceiptError("Generation profile receipt digest mismatch")
    normalized = normalize_profile(profile)
    current["generation_profile"] = normalized
    current["generation_profile_digest"] = profile_digest(normalized)
    manifest_path(staging_dir).write_text(json.dumps(current, indent=2), encoding="utf-8")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "generation_profile": normalized, "digest": profile_digest(normalized),
    }, indent=2), encoding="utf-8")


def record_manifest_chunks(
    staging_dir: Path,
    *,
    phase: str,
    group: str,
    chunks: list[Path],
) -> None:
    current = read_json_file(manifest_path(staging_dir)) or {}
    chunk_entries = current.get("chunks")
    if not isinstance(chunk_entries, dict):
        chunk_entries = {}
    chunk_entries[f"{phase}:{group}"] = [
        {
            "path": p.relative_to(staging_dir).as_posix() if p.is_relative_to(staging_dir) else str(p),
            "bytes": p.stat().st_size if p.exists() else 0,
            "exists": p.exists(),
        }
        for p in chunks
    ]
    current["chunks"] = chunk_entries
    current["updated_at"] = time.time()
    manifest_path(staging_dir).write_text(json.dumps(current, indent=2), encoding="utf-8")


class LockHeartbeat:
    """Async context manager that refreshes the generation lock every interval_s seconds."""

    def __init__(
        self,
        staging_dir: Path,
        *,
        run_id: int | None,
        interval_s: float = 60.0,
    ) -> None:
        self.staging_dir = staging_dir
        self.run_id = run_id
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "LockHeartbeat":
        self._task = asyncio.create_task(
            self._beat(), name=f"lock-heartbeat-{self.run_id}"
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                refresh_generation_lock(self.staging_dir, run_id=self.run_id)
            except Exception:
                _logger.warning(
                    "lock heartbeat refresh failed for run_id=%s", self.run_id, exc_info=True
                )
