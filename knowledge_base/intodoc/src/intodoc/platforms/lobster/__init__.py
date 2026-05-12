"""Lobster Data platform plugin -- XML profile parsing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from intodoc.models import FileType, ScanResult
from intodoc.platforms.generic import GenericPlugin
from intodoc.platforms.base_distiller import BaseDistiller

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

_PLUGIN_DIR = Path(__file__).parent


class LobsterPlugin(GenericPlugin):
    """Lobster Data integration platform plugin.

    Parses Lobster/DataWizard XML profile exports into structured scan results.
    Each profile is a self-contained <datawizardprofile> XML file describing
    an integration flow with agent, data properties, input/output trees,
    filter chains, and response units.
    """

    name = "lobster"
    display_name = "Lobster Data"

    def classify_file(self, path: Path) -> FileType:
        """Classify Lobster files by extension."""
        suffix = path.suffix.lower()
        if suffix == ".xml":
            return FileType.JOB
        if suffix == ".pak":
            return FileType.UNKNOWN
        return super().classify_file(path)

    def parse_project(self, repo_root: Path, cred_filter: CredentialFilter) -> ScanResult:
        """Parse using the Lobster-specific XML parser."""
        from intodoc.platforms.lobster.parser import LobsterParser

        return LobsterParser(cred_filter).parse(repo_root)

    def get_prompt(self, pass_number: int) -> str:
        """lobster/prompts/ -> _shared/prompts/ fallback chain."""
        prompt_dir = _PLUGIN_DIR / "prompts"
        if prompt_dir.exists():
            matches = list(prompt_dir.glob(f"p{pass_number:02d}*.txt"))
            if matches:
                return matches[0].read_text(encoding="utf-8")
        return super().get_prompt(pass_number)

    def get_distiller(self) -> BaseDistiller:
        """Return the Lobster-specific distiller."""
        from intodoc.platforms.lobster.distiller import LobsterDistiller

        return LobsterDistiller()

    def get_best_practices(self) -> dict:
        """Load Lobster-specific best practices."""
        if self._best_practices is None:
            bp_file = _PLUGIN_DIR / "best_practices.yaml"
            if bp_file.exists():
                self._best_practices = yaml.safe_load(
                    bp_file.read_text(encoding="utf-8")
                )
            else:
                self._best_practices = {"platform_name": "Lobster Data"}
        return self._best_practices
