"""Scan-output fingerprint service for dirty-set detection.

Computes per-group SHA-256 hashes over scan JSON output and diffs them against
a persisted baseline to determine which groups changed between pushes.

Patterns:
- Pure static methods for hashing/diffing (no I/O, trivially testable).
- Repository pattern for DB persistence (all SQL in this module; no inline SQL
  in callers).
- Caller owns the transaction boundary for persist_baseline.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import ProjectGroupScanHash

logger = logging.getLogger(__name__)


class ScanFingerprintService:
    """Fingerprint-based dirty-set detector for scan-output groups.

    The service computes SHA-256 digests over the JSON files that the intodoc
    scanner writes under ``intodoc-output/{project_id}/scan/jobs/{group}/``.
    A group is "dirty" when its hash differs from the stored baseline, is
    absent from the baseline (new group), or is in the baseline but absent
    from the current hashes (deleted group).
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Pure static helpers (no I/O)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_group_hash(group_dir: Path) -> str:
        """Return a hex SHA-256 digest over all *.json files in group_dir.

        Files are sorted by filename for determinism.  Each file contributes
        ``filename_bytes + b"\\0" + file_bytes`` so that renames (same content,
        different name) produce a different digest.

        Raises:
            FileNotFoundError: if group_dir does not exist.
        """
        if not group_dir.exists():
            raise FileNotFoundError(f"Group directory not found: {group_dir}")

        h = hashlib.sha256()
        json_files = sorted(group_dir.glob("*.json"), key=lambda p: p.name)
        for f in json_files:
            name_bytes = f.name.encode()
            content = f.read_bytes()
            h.update(name_bytes + b"\0" + content)
        return h.hexdigest()

    @staticmethod
    def compute_project_hashes(scan_jobs_dir: Path) -> dict[str, str]:
        """Walk scan_jobs_dir/<group>/ and return {group_name: hex_sha256}.

        Returns an empty dict if scan_jobs_dir does not exist.  Skips
        unreadable group directories with a warning rather than raising, so a
        single corrupt group does not block the whole project.

        If subdirectories are found inside a group directory they are logged as
        a warning (non-recursive layout is expected; future scanner changes may
        require extending this function).
        """
        if not scan_jobs_dir.exists():
            return {}

        hashes: dict[str, str] = {}
        for group_dir in sorted(scan_jobs_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            # Warn on unexpected subdirectories inside a group directory.
            subdirs = [d for d in group_dir.iterdir() if d.is_dir()]
            if subdirs:
                logger.warning(
                    "Unexpected subdirectories in group %s: %s -- hashing only top-level *.json",
                    group_dir.name,
                    [d.name for d in subdirs],
                )
            try:
                hashes[group_dir.name] = ScanFingerprintService.compute_group_hash(group_dir)
            except OSError as exc:
                logger.warning(
                    "Could not hash group %s: %s -- skipping group",
                    group_dir.name,
                    exc,
                )
        return hashes

    # ------------------------------------------------------------------
    # DB access (Repository pattern)
    # ------------------------------------------------------------------

    async def load_baseline(self, project_id: str) -> dict[str, str]:
        """Load stored hashes from project_group_scan_hashes.

        Returns {group_name: scan_hash}.  Returns {} if no rows exist.
        """
        result = await self._db.execute(
            select(ProjectGroupScanHash).where(
                ProjectGroupScanHash.project_id == project_id
            )
        )
        rows = result.scalars().all()
        return {row.group_name: row.scan_hash for row in rows}

    async def diff_against_baseline(
        self,
        project_id: str,
        current_hashes: dict[str, str],
    ) -> tuple[set[str], bool]:
        """Compare current_hashes against the DB baseline for project_id.

        Returns:
            (dirty_set, is_first_push) where:
              - dirty_set: groups whose hash changed, are new, or are in the
                baseline but absent from current_hashes (deletions).
              - is_first_push: True when no baseline rows exist for this project.
        """
        baseline = await self.load_baseline(project_id)
        is_first_push = len(baseline) == 0

        current_keys = set(current_hashes)
        baseline_keys = set(baseline)

        changed = {g for g in current_keys & baseline_keys if current_hashes[g] != baseline[g]}
        added = current_keys - baseline_keys
        deleted = baseline_keys - current_keys

        dirty_set = changed | added | deleted
        return dirty_set, is_first_push

    async def persist_baseline(
        self,
        db: AsyncSession,
        project_id: str,
        hashes: dict[str, str],
        scanned_at: datetime,
    ) -> None:
        """Upsert hashes into project_group_scan_hashes inside the caller's transaction.

        Rows for groups absent from ``hashes`` are deleted so renamed/removed
        groups do not linger in the baseline.  The DELETE and upsert share the
        caller's open session; the caller is responsible for the commit.

        Uses ORM ``merge()`` for dialect-agnostic upsert (SELECT + INSERT or
        UPDATE by composite PK).

        Args:
            db: The open AsyncSession that owns the transaction.
            project_id: Project identifier.
            hashes: Current group -> hex_sha256 mapping.
            scanned_at: Timestamp to record for all rows.
        """
        current_group_names = set(hashes)

        # Delete rows for groups no longer present in a single statement.
        if current_group_names:
            await db.execute(
                delete(ProjectGroupScanHash).where(
                    ProjectGroupScanHash.project_id == project_id,
                    ProjectGroupScanHash.group_name.not_in(current_group_names),
                )
            )
        else:
            # All groups removed; delete every row for this project.
            await db.execute(
                delete(ProjectGroupScanHash).where(
                    ProjectGroupScanHash.project_id == project_id
                )
            )

        # Upsert surviving/new rows using ORM merge (dialect-agnostic).
        for group_name, scan_hash in hashes.items():
            row = ProjectGroupScanHash(
                project_id=project_id,
                group_name=group_name,
                scan_hash=scan_hash,
                scanned_at=scanned_at,
            )
            await db.merge(row)
