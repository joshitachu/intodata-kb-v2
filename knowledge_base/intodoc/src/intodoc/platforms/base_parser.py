"""Shared base class for all platform parsers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from intodoc.models import ScanWarning

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter


class BaseParser:
    """Common warning infrastructure and credential-filtered file reading.

    All platform parsers (Talend, Workato, Lobster, Generic) inherit from this
    class to avoid duplicating ~30 LOC of identical boilerplate.
    """

    def __init__(self, cred_filter: CredentialFilter) -> None:
        self.cred_filter = cred_filter
        self._warnings: list[ScanWarning] = []
        self._warn_root: Path | None = None

    def _warn(self, *, kind: str, file: Path, message: str, phase: str) -> None:
        """Record a non-fatal scan warning alongside the usual console output."""
        try:
            rel = str(file.relative_to(self._warn_root)) if self._warn_root else str(file)
        except ValueError:
            rel = str(file)
        self._warnings.append(
            ScanWarning(kind=kind, file=rel, message=message, phase=phase)
        )

    def _read_filtered(self, path: Path) -> str:
        """Read a file and apply credential filtering."""
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.cred_filter.filter_text(text, filename=str(path))
