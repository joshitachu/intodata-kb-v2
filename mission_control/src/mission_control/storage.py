"""Small file-storage primitives shared by the registry and the snapshot writer."""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace ``path`` with ``text`` so a reader sees the old or the new file, never half.

    Temp file in the SAME directory (``os.replace`` is only atomic within one
    filesystem), created with ``mode`` from the start (``mkstemp`` is 0600, so
    a secret is never briefly world-readable), fsynced before the rename, and
    removed if anything fails, so no temp file is left behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    # Make the rename itself durable (best effort: not every platform allows it).
    with contextlib.suppress(OSError):
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


class InterProcessLock:
    """A lock that holds across threads (``threading.Lock``) AND worker processes
    (``fcntl.flock`` on a sidecar ``.lock`` file).

    ``flock`` locks belong to the open file description, so two instances in
    the same process also exclude each other, which is what the tests rely on.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._thread_lock = threading.Lock()

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
        with self._thread_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
