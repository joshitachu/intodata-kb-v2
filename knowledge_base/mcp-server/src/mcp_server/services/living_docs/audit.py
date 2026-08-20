"""Append-only operator receipts and cost/scope proof for living-doc patches."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PatchUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    provider_calls: int = 0
    billed_cost_usd: float = 0.0
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in asdict(self).values()):
            raise ValueError("patch usage values cannot be negative")


@dataclass(frozen=True, slots=True)
class PatchAuditReceipt:
    candidate_id: str
    project_id: str
    source_revision_id: str
    knowledge_revision_id: str
    state: str
    reason_code: str | None = None
    changed_artifacts: tuple[str, ...] = ()
    deleted_artifacts: tuple[str, ...] = ()
    rag_mutation_count: int = 0
    embedded_content_count: int = 0
    reused_content_count: int = 0
    deterministic_scope: bool = False
    usage: PatchUsage = field(default_factory=PatchUsage)
    recovery_action: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, object] = field(default_factory=dict)


class FilePatchAuditStore:
    """One append-only JSONL stream per project plus candidate status reads."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("audit root must be absolute")
        self._root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def append(self, receipt: PatchAuditReceipt) -> None:
        path = self._path(receipt.project_id)
        lock_path = path.with_suffix(".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                payload = asdict(receipt)
                payload["occurred_at"] = receipt.occurred_at.astimezone(timezone.utc).isoformat()
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
                output_fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                with os.fdopen(output_fd, "a", encoding="utf-8") as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def list_project(self, project_id: str) -> tuple[Mapping[str, object], ...]:
        path = self._path(project_id)
        if not path.exists():
            return ()
        return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    def latest_candidate(self, project_id: str, candidate_id: str) -> Mapping[str, object] | None:
        matches = [item for item in self.list_project(project_id) if item.get("candidate_id") == candidate_id]
        return matches[-1] if matches else None

    def _path(self, project_id: str) -> Path:
        if not project_id or any(char in project_id for char in "/\\\x00") or project_id in {".", ".."}:
            raise ValueError("invalid audit project id")
        return self._root / f"{project_id}.jsonl"
