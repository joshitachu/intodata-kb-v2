"""Durable, append-only candidate state and immutable candidate artifacts.

This is intentionally a small filesystem adapter.  It gives the Git, ZIP,
and chat orchestration seams a crash-recoverable lifecycle today without
coupling the living-document contracts to an ORM.  A future database adapter
can replay the same creation and journal records.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterator, Mapping

from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    ArtifactMembership,
    CandidateState,
    CandidateStatus,
    LivingDocsContractError,
    PatchCandidate,
    can_transition,
    candidate_from_dict,
    candidate_to_dict,
    canonical_json_bytes,
    content_sha256,
)


class CandidateStoreError(RuntimeError):
    pass


class CandidateConflict(CandidateStoreError):
    pass


class CandidateNotFound(CandidateStoreError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateJournalEntry:
    candidate_id: str
    version: int
    from_state: CandidateState
    to_state: CandidateState
    occurred_at: datetime
    reason_code: str | None = None


class FileCandidateStore:
    """Owns candidate roots only; it deliberately has no active-doc root."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("candidate store root must be absolute")
        self._root = root
        self._candidates_root = root / "candidates"
        self._idempotency_root = root / "idempotency"
        self._ensure_directory(self._root)
        self._ensure_directory(self._candidates_root)
        self._ensure_directory(self._idempotency_root)

    @property
    def root(self) -> Path:
        return self._root

    def create(self, candidate: PatchCandidate) -> PatchCandidate:
        """Create a candidate, or return the exact prior idempotent candidate.

        The idempotency record binds a user-visible trigger key to the exact
        candidate bytes.  Reusing that key for different source bytes is a
        conflict rather than an accidental overwrite.
        """

        record = candidate_to_dict(candidate)
        # ``created_at`` is an observation timestamp, not the idempotent work
        # identity.  Two deliveries of the same push/ZIP/chat trigger may be
        # constructed milliseconds apart and must resolve to the first
        # immutable candidate rather than conflict.
        identity_record = json.loads(canonical_json_bytes(record))
        identity_record.pop("created_at", None)
        identity_record["change_set"]["source_revision"].pop("observed_at", None)
        digest = content_sha256(canonical_json_bytes(identity_record))
        index_path = self._idempotency_path(candidate.change_set.idempotency_key)
        with self._locked(index_path.with_suffix(".lock")):
            if index_path.exists():
                index = self._read_json(index_path)
                if index.get("candidate_sha256") != digest:
                    raise CandidateConflict("idempotency key is already bound to different candidate bytes")
                candidate_id = index.get("candidate_id")
                if not isinstance(candidate_id, str):
                    raise CandidateStoreError("corrupt candidate idempotency record")
                existing = self.get_candidate(candidate_id)
                if existing.id != candidate.id:
                    raise CandidateStoreError("idempotency record does not match candidate receipt")
                return existing
            candidate_root = self._candidate_root(candidate.id)
            with self._locked(candidate_root.parent / f".{candidate.id}.lock"):
                if candidate_root.exists():
                    existing = self.get_candidate(candidate.id)
                    if existing != candidate:
                        raise CandidateConflict("candidate id is already bound to different candidate bytes")
                else:
                    candidate_root.mkdir(mode=0o700)
                    self._ensure_directory(candidate_root / "artifacts")
                    self._write_new_json(candidate_root / "candidate.json", record)
                    self._write_new_json(
                        candidate_root / "journal.jsonl",
                        {
                            "candidate_id": candidate.id,
                            "from_state": None,
                            "occurred_at": candidate.created_at.astimezone(timezone.utc).isoformat(),
                            "reason_code": None,
                            "to_state": CandidateState.QUEUED.value,
                            "version": 0,
                        },
                    )
                self._write_new_json(
                    index_path,
                    {"candidate_id": candidate.id, "candidate_sha256": digest, "schema_version": 1},
                )
        return candidate

    def get_candidate(self, candidate_id: str) -> PatchCandidate:
        return candidate_from_dict(self._read_json(self._candidate_root(candidate_id) / "candidate.json"))

    def candidate_root(self, candidate_id: str) -> Path:
        """Return the absolute candidate root after validating its existence."""

        root = self._candidate_root(candidate_id)
        if not root.is_dir():
            raise CandidateNotFound("candidate does not exist")
        return root

    def status(self, candidate_id: str) -> CandidateStatus:
        entries = self.journal(candidate_id)
        if not entries:
            raise CandidateStoreError("candidate journal is empty")
        last = entries[-1]
        return CandidateStatus(
            candidate_id=candidate_id,
            state=last.to_state,
            version=last.version,
            updated_at=last.occurred_at,
            reason_code=last.reason_code,
        )

    def journal(self, candidate_id: str) -> tuple[CandidateJournalEntry, ...]:
        candidate_root = self.candidate_root(candidate_id)
        path = candidate_root / "journal.jsonl"
        entries: list[CandidateJournalEntry] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            raise CandidateStoreError("candidate journal is absent") from exc
        for line in lines:
            try:
                value = json.loads(line)
                occurred_at = datetime.fromisoformat(str(value["occurred_at"]))
                from_raw = value.get("from_state")
                entries.append(
                    CandidateJournalEntry(
                        candidate_id=str(value["candidate_id"]),
                        version=int(value["version"]),
                        from_state=CandidateState(from_raw) if from_raw is not None else CandidateState.QUEUED,
                        to_state=CandidateState(value["to_state"]),
                        occurred_at=occurred_at,
                        reason_code=value.get("reason_code"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CandidateStoreError("candidate journal is corrupt") from exc
        self._validate_journal(candidate_id, entries)
        return tuple(entries)

    def transition(
        self,
        candidate_id: str,
        *,
        expected_version: int,
        target: CandidateState,
        reason_code: str | None = None,
        active_source_revision_key: str | None = None,
        active_knowledge_revision_id: str | None = None,
        now: datetime | None = None,
    ) -> CandidateStatus:
        """Append one CAS-protected transition and return the new status.

        Activation additionally checks the source and knowledge revision the
        candidate was based on.  This keeps a stale candidate from becoming
        active after a newer source/revision wins elsewhere.
        """

        candidate = self.get_candidate(candidate_id)
        candidate_root = self.candidate_root(candidate_id)
        with self._locked(candidate_root / ".journal.lock"):
            current = self.status(candidate_id)
            if current.version != expected_version:
                raise CandidateConflict("candidate state version compare-and-swap failed")
            if not can_transition(current.state, target):
                raise CandidateConflict(f"candidate transition {current.state} -> {target} is not allowed")
            if target is CandidateState.ACTIVE:
                if active_source_revision_key != candidate.change_set.source_revision.key:
                    raise CandidateConflict("candidate source revision is stale")
                if active_knowledge_revision_id != candidate.base_knowledge_revision_id:
                    raise CandidateConflict("candidate base knowledge revision is stale")
            occurred_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            entry = {
                "candidate_id": candidate_id,
                "from_state": current.state.value,
                "occurred_at": occurred_at.isoformat(),
                "reason_code": reason_code,
                "to_state": target.value,
                "version": current.version + 1,
            }
            self._append_json(candidate_root / "journal.jsonl", entry)
            return CandidateStatus(
                candidate_id=candidate_id,
                state=target,
                version=current.version + 1,
                updated_at=occurred_at,
                reason_code=reason_code,
            )

    def write_artifact(
        self,
        candidate_id: str,
        *,
        logical_id: str,
        artifact_kind: ArtifactKind,
        media_type: str,
        content: bytes,
    ) -> ArtifactMembership:
        """Write a hash-addressed artifact without exposing a writable path."""

        candidate_root = self.candidate_root(candidate_id)
        digest = content_sha256(content)
        membership = ArtifactMembership(
            logical_id=logical_id,
            artifact_kind=artifact_kind,
            content_sha256=digest,
            media_type=media_type,
            size_bytes=len(content),
        )
        with self._locked(candidate_root / ".artifacts.lock"):
            existing = {item.logical_id: item for item in self.memberships(candidate_id)}
            prior = existing.get(logical_id)
            if prior is not None:
                if prior != membership:
                    raise CandidateConflict("candidate artifact logical id is immutable")
                return prior
            destination = candidate_root / "artifacts" / digest[:2] / digest
            self._ensure_directory(destination.parent)
            if destination.exists():
                if destination.read_bytes() != content:
                    raise CandidateStoreError("content-addressed candidate artifact hash collision")
            else:
                self._write_new_bytes(destination, content)
            self._append_json(candidate_root / "memberships.jsonl", _membership_json(membership))
        return membership

    def memberships(self, candidate_id: str) -> tuple[ArtifactMembership, ...]:
        candidate_root = self.candidate_root(candidate_id)
        path = candidate_root / "memberships.jsonl"
        if not path.exists():
            return ()
        output: list[ArtifactMembership] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                output.append(
                    ArtifactMembership(
                        logical_id=str(item["logical_id"]),
                        artifact_kind=ArtifactKind(item["artifact_kind"]),
                        content_sha256=str(item["content_sha256"]),
                        media_type=str(item["media_type"]),
                        size_bytes=int(item["size_bytes"]),
                    )
                )
            except (KeyError, TypeError, ValueError, LivingDocsContractError) as exc:
                raise CandidateStoreError("candidate artifact membership journal is corrupt") from exc
        if len({item.logical_id for item in output}) != len(output):
            raise CandidateStoreError("candidate artifact membership journal is not immutable")
        return tuple(output)

    def detail(self, candidate_id: str) -> Mapping[str, Any]:
        """Read-only detail suitable for a dashboard or chat status endpoint."""

        candidate = self.get_candidate(candidate_id)
        status = self.status(candidate_id)
        return {
            "candidate": candidate_to_dict(candidate),
            "membership_count": len(self.memberships(candidate_id)),
            "state": status.state.value,
            "status_version": status.version,
            "updated_at": status.updated_at.isoformat(),
        }

    def _candidate_root(self, candidate_id: str) -> Path:
        if not candidate_id or any(char in candidate_id for char in "/\\\x00") or candidate_id in {".", ".."}:
            raise CandidateNotFound("invalid candidate id")
        return self._candidates_root / candidate_id

    def _idempotency_path(self, key: str) -> Path:
        if not key or "\x00" in key:
            raise CandidateStoreError("idempotency key is invalid")
        return self._idempotency_root / f"{content_sha256(key.encode('utf-8'))}.json"

    def _validate_journal(self, candidate_id: str, entries: list[CandidateJournalEntry]) -> None:
        if not entries or entries[0].candidate_id != candidate_id or entries[0].version != 0:
            raise CandidateStoreError("candidate journal has no valid creation record")
        if entries[0].to_state is not CandidateState.QUEUED:
            raise CandidateStoreError("candidate journal must begin queued")
        previous = entries[0]
        for entry in entries[1:]:
            if (
                entry.candidate_id != candidate_id
                or entry.version != previous.version + 1
                or entry.from_state is not previous.to_state
                or not can_transition(previous.to_state, entry.to_state)
            ):
                raise CandidateStoreError("candidate journal transition sequence is invalid")
            previous = entry

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        self._ensure_directory(path.parent)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(descriptor, "a+b", closefd=True) as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            # fdopen owns the descriptor; this is deliberately only a safety
            # net for an exception before it gains ownership.
            pass

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise CandidateStoreError("candidate store path must be a non-symlink directory")

    @staticmethod
    def _write_new_bytes(path: Path, content: bytes) -> None:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise CandidateConflict("immutable candidate path already exists") from exc
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())

    def _write_new_json(self, path: Path, value: Mapping[str, Any]) -> None:
        self._write_new_bytes(path, canonical_json_bytes(value) + b"\n")

    def _append_json(self, path: Path, value: Mapping[str, Any]) -> None:
        payload = canonical_json_bytes(value) + b"\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CandidateNotFound("candidate does not exist") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CandidateStoreError("candidate receipt is corrupt") from exc
        if not isinstance(value, Mapping):
            raise CandidateStoreError("candidate receipt must be an object")
        return value


def _membership_json(membership: ArtifactMembership) -> Mapping[str, Any]:
    return {
        "artifact_kind": membership.artifact_kind.value,
        "content_sha256": membership.content_sha256,
        "logical_id": membership.logical_id,
        "media_type": membership.media_type,
        "size_bytes": membership.size_bytes,
    }
