"""Install a pushed licence into the licence file (not mirrored).

Command handler behind ``POST /ops/control/licence`` (plan Decisions 3 and 4).
The token is verified BEFORE anything touches the disk, against the pinned key
ring and this instance's slug, so a leaked control key can at worst replay a
genuine licence for this same client. It can never install a forged or foreign one.

Storage stays the file that both sub-systems read. mcp-server holds the
licence directory read-write and the RAG backend holds it read-only. The write
is atomic: temp file in the same directory, fsync, ``os.replace``, then fsync
the directory. Every reader sees either the old token or the new one and never
a torn file. The rename gives the file a new inode, which the directory bind
mount exposes to the backend container.

Slots (``core`` module docstring). A VALID token always goes to the current
file; pushing a genuine but shorter licence there is the documented
revoke-by-replace path. A NOT_YET_VALID token (a gapless renewal) goes to the
next file while the current licence is still VALID, so the running licence is
never displaced; with no VALID current licence it goes to the current file.
A current-slot install keeps a queued renewal only if it continues the new
current licence without a gap, so revoke-by-replace cannot be undone later by
a renewal queued before it. Promotion (next -> current once next has started)
runs at every install and at mcp-server startup; the policy serves the next
slot on time regardless, so promotion is housekeeping, not correctness.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from mcp_server.config import config

from .core import LEEWAY_SECONDS, LicenceState, LicenceStatus, next_licence_path, verify_token
from .keys import PINNED_PUBLIC_KEYS

logger = logging.getLogger(__name__)

#: A token is ~455 bytes today. The cap is a sanity bound far above any
#: realistic plan, checked before signature verification so an oversized body
#: costs nothing.
MAX_TOKEN_BYTES = 16 * 1024

#: Only these verdicts may be installed. NOT_YET_VALID is a gapless renewal
#: pushed before the current period ends.
INSTALLABLE_STATES = frozenset({LicenceState.VALID, LicenceState.NOT_YET_VALID})

LICENCE_FILE_MODE = 0o640

#: Temp files older than this are leftovers of a crashed install (a live one
#: exists for milliseconds) and are removed at the next install.
STALE_TEMP_SECONDS = 10 * 60

Slot = Literal["current", "next"]

#: One install at a time per process. The mtime bump below reads the current
#: file's mtime and then writes a newer one, so two interleaved installs could
#: otherwise produce the same mtime.
_install_lock = threading.Lock()


class LicenceTooLarge(Exception):
    """The token exceeds ``MAX_TOKEN_BYTES``; nothing was verified or written."""


class LicenceRejected(Exception):
    """The token did not verify for this instance; the file was not touched."""

    def __init__(self, status: LicenceStatus) -> None:
        super().__init__(status.reason or status.state.value)
        self.status = status


class LicenceStoreUnwritable(Exception):
    """The licence directory cannot take the new file; the old file is intact."""


@dataclass(frozen=True)
class InstallResult:
    #: Current slot before this install (after any promotion it performed).
    previous: LicenceStatus
    installed: LicenceStatus
    slot: Slot


class LicenceInstallService:
    """Verify-then-store for one licence file."""

    def __init__(
        self,
        licence_file: str | Path,
        *,
        public_keys: Mapping[str, str],
        expected_subject: str,
    ) -> None:
        self._path = Path(licence_file)
        self._next_path = next_licence_path(self._path)
        self._public_keys = dict(public_keys)
        self._expected_subject = expected_subject

    @classmethod
    def from_config(cls) -> "LicenceInstallService":
        """The same key ring, file and subject that ``runtime.build_policy`` uses."""
        return cls(
            config.licence_file,
            public_keys=PINNED_PUBLIC_KEYS,
            expected_subject=config.licence_client_slug,
        )

    def _verify(self, token: str | None) -> LicenceStatus:
        return verify_token(token, public_keys=self._public_keys,
                            expected_subject=self._expected_subject)

    def _slot_status(self, path: Path) -> LicenceStatus:
        try:
            token = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            token = None
        return self._verify(token)

    def _current_status(self) -> LicenceStatus:
        return self._slot_status(self._path)

    def install(self, token: str) -> InstallResult:
        """Verify ``token`` and, only if it is installable, store it in the right slot.

        Raises ``LicenceTooLarge``, ``LicenceRejected`` or ``LicenceStoreUnwritable``.
        In every one of those cases both slot files are unchanged (apart from a
        due promotion, which is itself atomic).
        """
        if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            raise LicenceTooLarge(f"licence token exceeds {MAX_TOKEN_BYTES} bytes")
        token = token.strip()
        status = self._verify(token)
        if status.state not in INSTALLABLE_STATES:
            logger.warning(
                "licence install rejected: state=%s reason=%s lid=%s",
                status.state.value,
                status.reason or None,
                status.claims.licence_id if status.claims else None,
            )
            raise LicenceRejected(status)
        claims = status.claims
        assert claims is not None  # VALID / NOT_YET_VALID always carry claims

        with _install_lock:
            self._sweep_stale_temps()
            self._promote_locked()
            previous = self._current_status()
            slot: Slot
            if status.state is LicenceState.NOT_YET_VALID and previous.state is LicenceState.VALID:
                slot = "next"
                self._write_atomically(self._next_path, token + "\n")
            else:
                slot = "current"
                self._write_atomically(self._path, token + "\n")
                self._drop_next_unless_continuing(status)

        prev_claims = previous.claims
        if slot == "next":
            # The current licence is untouched; say so, so the line cannot be
            # read as a downgrade of what the instance is serving.
            logger.info(
                "licence installed: slot=next lid=%s plan=%s starts=%s ends=%s "
                "(current licence unchanged: %s until %s)",
                claims.licence_id,
                claims.plan,
                claims.period_start.isoformat(),
                claims.period_end.isoformat(),
                previous.state.value,
                prev_claims.period_end.isoformat() if prev_claims else None,
            )
        else:
            logger.info(
                "licence installed: slot=current lid=%s plan=%s period_end %s->%s state %s->%s",
                claims.licence_id,
                claims.plan,
                prev_claims.period_end.isoformat() if prev_claims else None,
                claims.period_end.isoformat(),
                previous.state.value,
                status.state.value,
            )
        return InstallResult(previous=previous, installed=status, slot=slot)

    def promote_if_due(self) -> bool:
        """Promote a started renewal into the current slot. ``True`` if it did."""
        with _install_lock:
            return self._promote_locked()

    def _promote_locked(self) -> bool:
        """Next -> current when next is VALID and current is not; tidy a dead next.

        Caller holds ``_install_lock``. A next slot that can never be served
        (expired, invalid, or the same licence as current) is removed.
        """
        if not self._next_path.exists():
            return False
        queued = self._slot_status(self._next_path)
        current = self._current_status()
        if queued.state is LicenceState.VALID and current.state is not LicenceState.VALID:
            try:
                token = self._next_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise LicenceStoreUnwritable(f"cannot read {self._next_path}: {exc}") from exc
            self._write_atomically(self._path, token + "\n")
            self._remove_next()
            logger.info(
                "licence promoted: lid=%s period_end %s->%s state %s->valid",
                queued.claims.licence_id if queued.claims else None,
                current.claims.period_end.isoformat() if current.claims else None,
                queued.claims.period_end.isoformat() if queued.claims else None,
                current.state.value,
            )
            return True
        same_licence = (
            queued.claims is not None and current.claims is not None
            and queued.claims.licence_id == current.claims.licence_id
        )
        if queued.state not in INSTALLABLE_STATES or same_licence:
            self._remove_next()
            logger.info("licence next slot cleared: state=%s same_as_current=%s",
                        queued.state.value, same_licence)
        return False

    def _drop_next_unless_continuing(self, installed: LicenceStatus) -> None:
        """After a current-slot install, keep a queued renewal only if it has no gap.

        A renewal that starts after the new current licence ends would
        otherwise resurrect a licence that was just cut short (revoke-by-replace).
        """
        if not self._next_path.exists():
            return
        queued = self._slot_status(self._next_path)
        new_claims = installed.claims
        continues = (
            queued.state in INSTALLABLE_STATES
            and queued.claims is not None
            and new_claims is not None
            and queued.claims.licence_id != new_claims.licence_id
            and queued.claims.period_start.timestamp()
            <= new_claims.period_end.timestamp() + LEEWAY_SECONDS
        )
        if not continues:
            self._remove_next()
            logger.info(
                "licence next slot dropped: lid=%s does not continue the installed licence",
                queued.claims.licence_id if queued.claims else None,
            )

    def _remove_next(self) -> None:
        try:
            self._next_path.unlink(missing_ok=True)
        except OSError as exc:
            raise LicenceStoreUnwritable(
                f"cannot remove {self._next_path}: {exc.strerror or exc}"
            ) from exc
        self._fsync_directory(self._next_path.parent)

    def _sweep_stale_temps(self) -> None:
        """Remove temp files a crashed install left behind (best effort)."""
        cutoff = time.time() - STALE_TEMP_SECONDS
        for leftover in self._path.parent.glob(f".{self._path.stem}*.tmp"):
            try:
                if leftover.is_file() and leftover.stat().st_mtime < cutoff:
                    leftover.unlink()
                    logger.info("licence temp file swept: %s", leftover.name)
            except OSError as exc:
                logger.warning("licence temp sweep skipped %s: %s", leftover.name, exc)

    def _write_atomically(self, target: Path, content: str) -> None:
        directory = target.parent
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=directory, prefix=f".{target.name}.", suffix=".tmp"
            )
        except OSError as exc:
            raise LicenceStoreUnwritable(
                f"cannot create a file in {directory}: {exc.strerror or exc}"
            ) from exc

        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # On the descriptor, not the path: nothing can swap the name
                # between creation and chmod.
                os.fchmod(handle.fileno(), LICENCE_FILE_MODE)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._ensure_newer_mtime(target, tmp_path)
            os.replace(tmp_path, target)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise LicenceStoreUnwritable(
                f"cannot write {target}: {exc.strerror or exc}"
            ) from exc

        self._fsync_directory(directory)

    @staticmethod
    def _ensure_newer_mtime(target: Path, tmp_path: Path) -> None:
        """Give the new file an mtime strictly later than the file it replaces.

        Every reader (this process's policy and the RAG backend's) caches the
        token by inode, size and ``st_mtime_ns`` (``core.FileLicenceSource``).
        The atomic replace already changes the inode, but an inode number can be
        recycled, and two installs inside one timestamp tick (up to 1-2s on a
        coarse filesystem) would then look unchanged. Bumping past the old
        mtime by a whole second survives any granularity, so a renewal can
        never stay invisible. Only the mtime is changed; the content is already
        the new token.
        """
        try:
            old = target.stat()
        except FileNotFoundError:
            return
        new = tmp_path.stat()
        # Compare both forms: the readers compare the float ``st_mtime``.
        if new.st_mtime_ns <= old.st_mtime_ns or new.st_mtime <= old.st_mtime:
            bumped = old.st_mtime_ns + 1_000_000_000
            os.utime(tmp_path, ns=(bumped, bumped))

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Persist the rename itself. The file is already in place either way."""
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError as exc:
            logger.warning("licence dir fsync skipped (%s): %s", directory, exc)
            return
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            # The replace has happened and every reader already sees it. Only
            # crash durability of the rename is weaker, so warn and do not fail.
            logger.warning("licence dir fsync failed (%s): %s", directory, exc)
        finally:
            os.close(dir_fd)
