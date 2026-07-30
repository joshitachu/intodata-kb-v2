"""Recoverable retention for isolated experiment artifact roots.

The retention boundary is intentionally narrower than general filesystem cleanup:
it only moves one exact ``output/<project>/experiments/<uuid>`` directory into a
project-local quarantine.  Legacy documentation and publication directories are
never addressable by this module, and it never hard-deletes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal

from mcp_server.experiments.domain.models import require_safe_identifier, require_uuid
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.storage.hashing import sha256_bytes


TerminalExperimentStatus = Literal["completed", "completed_warnings", "failed", "cancelled"]
_TERMINAL_STATUSES = frozenset({"completed", "completed_warnings", "failed", "cancelled"})
_RECEIPT_NAME = ".agentic-retention-receipt.json"
_TRASH_DIR_NAME = ".agentic-experiment-trash"


class ExperimentRetentionError(RuntimeError):
    """Base error for non-destructive experiment retention."""


class RetentionAcknowledgementRequired(ExperimentRetentionError):
    """A state-changing quarantine or restore was not explicitly acknowledged."""


class RetentionEligibilityError(ExperimentRetentionError):
    """The experiment is not old, terminal, or lease-free enough to retain."""


class UnsafeRetentionPathError(ExperimentRetentionError):
    """A requested retention path is not the exact allowed experiment root."""


class ImmutableRetentionReceiptError(ExperimentRetentionError):
    """A retention receipt already exists and must not be replaced."""


@dataclass(frozen=True)
class RetentionCandidate:
    """Database-derived eligibility facts; this storage layer never queries ORM state."""

    project_id: str
    experiment_id: str
    status: str
    completed_at: datetime.datetime | None
    has_live_lease: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            require_safe_identifier(self.project_id, field_name="project_id"),
        )
        object.__setattr__(self, "experiment_id", require_uuid(self.experiment_id))
        if not isinstance(self.has_live_lease, bool):
            raise ValueError("has_live_lease must be boolean")


@dataclass(frozen=True)
class RetentionReceipt:
    """Sanitized immutable receipt stored with a quarantined experiment tree."""

    project_id: str
    experiment_id: str
    status: TerminalExperimentStatus
    completed_at: datetime.datetime
    cutoff: datetime.datetime
    quarantined_at: datetime.datetime
    source_relative_path: str
    trash_relative_path: str
    receipt_sha256: str


@dataclass(frozen=True)
class RestoreReceipt:
    """Return value for an acknowledged atomic restore; original receipt remains intact."""

    retention: RetentionReceipt
    restored_at: datetime.datetime


class ExperimentRetention:
    """Move/restore experiment roots through a recoverable project-local trash."""

    def __init__(self, *, output_root: Path) -> None:
        self._output_root = Path(output_root)

    def quarantine(
        self,
        *,
        candidate: RetentionCandidate,
        cutoff: datetime.datetime,
        acknowledged: bool,
        now: datetime.datetime | None = None,
    ) -> RetentionReceipt:
        """Atomically quarantine one eligible experiment after explicit acknowledgement."""

        if acknowledged is not True:
            raise RetentionAcknowledgementRequired("quarantine requires explicit acknowledgement")
        cutoff = _require_aware_utc(cutoff, field="cutoff")
        quarantined_at = _require_aware_utc(
            now or datetime.datetime.now(datetime.timezone.utc),
            field="now",
        )
        completed_at = self._require_eligible(candidate, cutoff=cutoff)
        project_root = self._project_root(candidate.project_id)
        source = self._experiment_path(project_root, candidate.experiment_id)
        trash_root = self._trash_root(project_root)
        destination = trash_root / candidate.experiment_id
        self._assert_exact_child(destination, trash_root, candidate.experiment_id)
        if _path_exists_or_symlink(destination):
            # If a prior rename completed but its caller crashed before receiving
            # the return value, acknowledge the immutable completed move instead
            # of treating a retry as a conflicting mutation.
            if _path_exists_or_symlink(source):
                raise ImmutableRetentionReceiptError(
                    "both experiment and quarantine destination exist"
                )
            self._require_tree_without_symlinks(destination, label="quarantined experiment root")
            retention = self._read_receipt(destination / _RECEIPT_NAME)
            self._require_matching_receipt(
                retention,
                candidate=candidate,
                completed_at=completed_at,
                cutoff=cutoff,
            )
            return retention

        self._require_tree_without_symlinks(source, label="experiment root")

        receipt_path = source / _RECEIPT_NAME
        if _path_exists_or_symlink(receipt_path):
            # The only permitted pre-existing receipt is the retained evidence of
            # a prior failed rename. Verify it and resume that exact move.
            receipt = self._read_receipt(receipt_path)
            self._require_matching_receipt(
                receipt,
                candidate=candidate,
                completed_at=completed_at,
                cutoff=cutoff,
            )
        else:
            receipt = RetentionReceipt(
                project_id=candidate.project_id,
                experiment_id=candidate.experiment_id,
                status=_require_terminal_status(candidate.status),
                completed_at=completed_at,
                cutoff=cutoff,
                quarantined_at=quarantined_at,
                source_relative_path=f"experiments/{candidate.experiment_id}",
                trash_relative_path=f"{_TRASH_DIR_NAME}/{candidate.experiment_id}",
                receipt_sha256="",
            )
            receipt_bytes = canonical_json_bytes(_receipt_payload(receipt))
            receipt = replace(receipt, receipt_sha256=sha256_bytes(receipt_bytes))
            # The receipt is created before rename so it moves atomically with the
            # evidence.  It is create-only and contains relative, non-secret fields.
            self._atomic_create_bytes(receipt_path, receipt_bytes)
        try:
            os.replace(source, destination)
            _fsync_directory(project_root)
            _fsync_directory(trash_root)
        except OSError as exc:
            raise ExperimentRetentionError("unable to atomically quarantine experiment") from exc
        return receipt

    def restore(
        self,
        *,
        project_id: str,
        experiment_id: str,
        acknowledged: bool,
        now: datetime.datetime | None = None,
    ) -> RestoreReceipt:
        """Atomically restore one quarantined experiment; no receipt is rewritten."""

        if acknowledged is not True:
            raise RetentionAcknowledgementRequired("restore requires explicit acknowledgement")
        restored_at = _require_aware_utc(
            now or datetime.datetime.now(datetime.timezone.utc),
            field="now",
        )
        project_id = require_safe_identifier(project_id, field_name="project_id")
        experiment_id = require_uuid(experiment_id)
        project_root = self._project_root(project_id)
        trash_root = self._trash_root(project_root)
        source = trash_root / experiment_id
        self._assert_exact_child(source, trash_root, experiment_id)
        experiments_root = self._experiments_root(project_root)
        destination = experiments_root / experiment_id
        self._assert_exact_child(destination, experiments_root, experiment_id)
        if _path_exists_or_symlink(destination):
            # A completed rename can outlive its caller response. Only accept the
            # exact restored experiment when its immutable retention receipt still
            # proves the identity; an arbitrary existing output remains a conflict.
            if _path_exists_or_symlink(source):
                raise ImmutableRetentionReceiptError(
                    "both quarantine source and restore destination exist"
                )
            self._require_tree_without_symlinks(destination, label="restored experiment root")
            retention = self._read_receipt(destination / _RECEIPT_NAME)
            if retention.project_id != project_id or retention.experiment_id != experiment_id:
                raise UnsafeRetentionPathError(
                    "retention receipt identity does not match restore target"
                )
            return RestoreReceipt(retention=retention, restored_at=restored_at)
        self._require_tree_without_symlinks(source, label="quarantined experiment root")
        retention = self._read_receipt(source / _RECEIPT_NAME)
        if retention.project_id != project_id or retention.experiment_id != experiment_id:
            raise UnsafeRetentionPathError("retention receipt identity does not match restore target")

        try:
            os.replace(source, destination)
            _fsync_directory(project_root)
            _fsync_directory(experiments_root)
        except OSError as exc:
            raise ExperimentRetentionError("unable to atomically restore experiment") from exc
        return RestoreReceipt(retention=retention, restored_at=restored_at)

    def _require_eligible(
        self,
        candidate: RetentionCandidate,
        *,
        cutoff: datetime.datetime,
    ) -> datetime.datetime:
        if candidate.status not in _TERMINAL_STATUSES:
            raise RetentionEligibilityError("only terminal experiments may be quarantined")
        if candidate.has_live_lease:
            raise RetentionEligibilityError("experiments with a live lease may not be quarantined")
        if candidate.completed_at is None:
            raise RetentionEligibilityError("terminal experiment needs a completion timestamp")
        completed_at = _require_aware_utc(candidate.completed_at, field="completed_at")
        if completed_at >= cutoff:
            raise RetentionEligibilityError("experiment is not older than retention cutoff")
        return completed_at

    @staticmethod
    def _require_matching_receipt(
        receipt: RetentionReceipt,
        *,
        candidate: RetentionCandidate,
        completed_at: datetime.datetime,
        cutoff: datetime.datetime,
    ) -> None:
        if (
            receipt.project_id != candidate.project_id
            or receipt.experiment_id != candidate.experiment_id
            or receipt.status != candidate.status
            or receipt.completed_at != completed_at
            or receipt.cutoff != cutoff
        ):
            raise ImmutableRetentionReceiptError(
                "existing retention receipt does not match requested quarantine"
            )

    def _project_root(self, project_id: str) -> Path:
        self._require_directory(self._output_root, label="output root")
        project_root = self._output_root / project_id
        self._assert_exact_child(project_root, self._output_root, project_id)
        self._require_directory(project_root, label="project root")
        return project_root

    def _experiments_root(self, project_root: Path) -> Path:
        root = project_root / "experiments"
        self._assert_exact_child(root, project_root, "experiments")
        self._require_directory(root, label="experiments root")
        return root

    def _experiment_path(self, project_root: Path, experiment_id: str) -> Path:
        experiments_root = self._experiments_root(project_root)
        path = experiments_root / experiment_id
        self._assert_exact_child(path, experiments_root, experiment_id)
        return path

    def _trash_root(self, project_root: Path) -> Path:
        trash_root = project_root / _TRASH_DIR_NAME
        self._assert_exact_child(trash_root, project_root, _TRASH_DIR_NAME)
        if not _path_exists_or_symlink(trash_root):
            try:
                trash_root.mkdir(mode=0o700)
            except OSError as exc:
                raise UnsafeRetentionPathError("cannot create project retention trash") from exc
        self._require_directory(trash_root, label="project retention trash")
        return trash_root

    @staticmethod
    def _assert_exact_child(path: Path, parent: Path, name: str) -> None:
        if path.parent != parent or path.name != name:
            raise UnsafeRetentionPathError("retention path is not an exact allowed child")

    @classmethod
    def _require_tree_without_symlinks(cls, root: Path, *, label: str) -> None:
        cls._require_directory(root, label=label)
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            cls._require_directory(current_path, label=label)
            for name in [*directory_names, *file_names]:
                child = current_path / name
                try:
                    mode = child.lstat().st_mode
                except OSError as exc:
                    raise UnsafeRetentionPathError("cannot inspect experiment tree") from exc
                if stat.S_ISLNK(mode):
                    raise UnsafeRetentionPathError("experiment tree may not contain symlinks")
                if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                    continue
                raise UnsafeRetentionPathError("experiment tree contains unsupported file type")

    @staticmethod
    def _require_directory(path: Path, *, label: str) -> None:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise UnsafeRetentionPathError(f"cannot inspect {label}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise UnsafeRetentionPathError(f"{label} must be a non-symlink directory")

    def _read_receipt(self, path: Path) -> RetentionReceipt:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise UnsafeRetentionPathError("retention receipt is missing") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise UnsafeRetentionPathError("retention receipt must be a regular non-symlink file")
        if mode & 0o222:
            raise UnsafeRetentionPathError("retention receipt must be immutable")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise UnsafeRetentionPathError("cannot read retention receipt") from exc
        if len(content) > 16_384:
            raise UnsafeRetentionPathError("retention receipt exceeds size limit")
        try:
            payload = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise UnsafeRetentionPathError("retention receipt is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise UnsafeRetentionPathError("retention receipt must be an object")
        required = {
            "schema_version",
            "project_id",
            "experiment_id",
            "status",
            "completed_at",
            "cutoff",
            "quarantined_at",
            "source_relative_path",
            "trash_relative_path",
        }
        if set(payload) != required:
            raise UnsafeRetentionPathError("retention receipt schema is invalid")
        try:
            retention = RetentionReceipt(
                project_id=require_safe_identifier(payload["project_id"], field_name="project_id"),
                experiment_id=require_uuid(payload["experiment_id"]),
                status=_require_terminal_status(payload["status"]),
                completed_at=_parse_utc(payload["completed_at"]),
                cutoff=_parse_utc(payload["cutoff"]),
                quarantined_at=_parse_utc(payload["quarantined_at"]),
                source_relative_path=_require_expected_relative(
                    payload["source_relative_path"],
                    "experiments",
                    payload["experiment_id"],
                ),
                trash_relative_path=_require_expected_relative(
                    payload["trash_relative_path"],
                    _TRASH_DIR_NAME,
                    payload["experiment_id"],
                ),
                receipt_sha256=sha256_bytes(content),
            )
        except (TypeError, ValueError) as exc:
            raise UnsafeRetentionPathError("retention receipt values are invalid") from exc
        return retention

    @staticmethod
    def _atomic_create_bytes(target: Path, content: bytes) -> None:
        if _path_exists_or_symlink(target):
            raise ImmutableRetentionReceiptError("retention receipt already exists")
        fd, temporary_name = tempfile.mkstemp(prefix=".retention-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o444)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ImmutableRetentionReceiptError("retention receipt already exists") from exc
        finally:
            temporary.unlink(missing_ok=True)


def _receipt_payload(receipt: RetentionReceipt) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": receipt.project_id,
        "experiment_id": receipt.experiment_id,
        "status": receipt.status,
        "completed_at": receipt.completed_at.isoformat(),
        "cutoff": receipt.cutoff.isoformat(),
        "quarantined_at": receipt.quarantined_at.isoformat(),
        "source_relative_path": receipt.source_relative_path,
        "trash_relative_path": receipt.trash_relative_path,
    }


def _require_aware_utc(value: datetime.datetime, *, field: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(datetime.timezone.utc)


def _require_terminal_status(value: object) -> TerminalExperimentStatus:
    if value not in _TERMINAL_STATUSES:
        raise ValueError("status must be terminal")
    return value  # type: ignore[return-value]


def _parse_utc(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    return _require_aware_utc(datetime.datetime.fromisoformat(value), field="receipt timestamp")


def _require_expected_relative(value: object, parent: str, experiment_id: str) -> str:
    expected = f"{parent}/{require_uuid(experiment_id)}"
    if value != expected:
        raise ValueError("receipt relative path is invalid")
    return expected


def _path_exists_or_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
