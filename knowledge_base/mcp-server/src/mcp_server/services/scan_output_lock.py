"""Cross-process-safe ownership of a project's mutable scan output directory."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScanOutputLock:
    """An exclusive, token-bound filesystem claim.

    The lock lives beside (rather than inside) the mutable output directory,
    so scanner cleanup cannot accidentally remove it.  ``release`` only
    removes a file that still contains this caller's token; a late cleanup can
    therefore never remove another process's replacement lock.
    """

    path: Path
    token: str

    def release(self) -> None:
        try:
            if self.path.read_text(encoding="ascii") != self.token:
                return
            self.path.unlink(missing_ok=True)
        except OSError:
            # A failed cleanup must not hide the completed scan result.  The
            # remaining lock is safe (it fails closed) and can be cleared by
            # an operator after confirming no scan is running.
            return


def acquire_scan_output_lock(output_dir: str | Path) -> ScanOutputLock | None:
    """Atomically claim ``output_dir`` or return ``None`` if it is busy.

    ``O_CREAT | O_EXCL`` is atomic across processes on the local filesystem.
    We intentionally do not auto-reclaim a lock after a crash: PID-based
    recovery can race with a reused PID or another worker.  An orphaned lock
    is therefore fail-closed and requires explicit operator cleanup.
    """

    output = Path(output_dir)
    path = output.with_name(f".{output.name}.scan.lock")
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    try:
        with os.fdopen(fd, "w", encoding="ascii") as lock_file:
            lock_file.write(token)
            lock_file.flush()
            os.fsync(lock_file.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return ScanOutputLock(path=path, token=token)
