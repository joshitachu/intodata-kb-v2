"""Issuance ledger: an append-only record of every licence Mission Control signed.

Repository pattern: callers depend on ``IssuedLicenceLedger``; the JSONL file is
today's implementation (Mission Control has no database). Option B moves this
into the tenant subscription table behind the same interface.

The ledger never holds a token. ``LedgerEntry`` has no field for one, so a
token cannot be written by accident.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "issued.jsonl"


@dataclass(frozen=True)
class LedgerEntry:
    licence_id: str
    client: str
    plan: str
    period_start: str
    period_end: str
    issued_at: str
    issued_by: str | None
    kid: str
    #: ``None`` when no push was attempted (push=false).
    push_ok: bool | None
    push_status_code: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LedgerEntry:
        known = {f.name for f in fields(cls)}
        values = {k: raw.get(k) for k in known}
        missing = [k for k in ("licence_id", "client", "plan") if not values.get(k)]
        if missing:
            raise ValueError(f"missing {', '.join(missing)}")
        return cls(**values)


@runtime_checkable
class IssuedLicenceLedger(Protocol):
    def check_writable(self) -> None:
        """Raise ``OSError`` if an append would fail (checked before signing)."""
        ...

    def append(self, entry: LedgerEntry) -> None: ...

    def list_for_client(self, client: str) -> list[LedgerEntry]:
        """The client's entries, newest first."""
        ...


class JsonlLedger:
    """One JSON object per line; appends are locked, ``O_APPEND`` and fsynced."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        return self._directory / LEDGER_FILENAME

    def _open_append(self) -> int:
        self._directory.mkdir(parents=True, exist_ok=True)
        return os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)

    def check_writable(self) -> None:
        os.close(self._open_append())

    def append(self, entry: LedgerEntry) -> None:
        line = (json.dumps(entry.to_dict(), separators=(",", ":"), sort_keys=True) + "\n")
        data = line.encode("utf-8")
        fd = self._open_append()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                written = 0
                while written < len(data):
                    written += os.write(fd, data[written:])
                os.fsync(fd)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def list_for_client(self, client: str) -> list[LedgerEntry]:
        try:
            fh = self.path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return []
        entries: list[LedgerEntry] = []
        with fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                for lineno, line in enumerate(fh, start=1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise TypeError("not an object")
                        entry = LedgerEntry.from_dict(raw)
                    except (ValueError, TypeError) as exc:
                        logger.warning("ledger %s line %d skipped: %s", self.path, lineno,
                                       type(exc).__name__)
                        continue
                    if entry.client == client:
                        entries.append(entry)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        entries.reverse()  # appended chronologically; newest first
        return entries
