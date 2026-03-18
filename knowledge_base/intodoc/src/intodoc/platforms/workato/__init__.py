"""Workato platform plugin -- structured JSON recipe parsing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from intodoc.models import FileType, ScanResult
from intodoc.platforms.generic import GenericPlugin

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

_PLUGIN_DIR = Path(__file__).parent


class WorkatoPlugin(GenericPlugin):
    """Workato integration platform plugin.

    Extends GenericPlugin with structured parsing of Workato recipe exports.
    Supports all 14 Workato asset types.
    """

    name = "workato"
    display_name = "Workato"

    # Asset type suffix -> FileType mapping
    _ASSET_TYPE_MAP: dict[str, FileType] = {
        ".recipe.json": FileType.JOB,
        ".connection.json": FileType.CONNECTION,
        ".lookup_table.json": FileType.SCHEMA,
        ".workato_db_table.json": FileType.SCHEMA,
        ".account_property.json": FileType.CONTEXT,
        ".project_property.json": FileType.CONTEXT,
        ".workato_schema.json": FileType.SCHEMA,
        ".workato_template.json": FileType.CONFIG,
        ".custom_adapter.json": FileType.ROUTINE,
        ".topic.json": FileType.CONNECTION,
        ".api_group.json": FileType.CONFIG,
        ".api_endpoint.json": FileType.JOB,
        ".lcap_app.json": FileType.JOB,
        ".lcap_page.json": FileType.CONFIG,
        ".recipe_function.json": FileType.JOB,
    }

    def classify_file(self, path: Path) -> FileType:
        """Classify by Workato asset type suffix, then fall back to Generic."""
        name = path.name.lower()

        # Check Workato-specific asset type suffixes
        for suffix, ftype in self._ASSET_TYPE_MAP.items():
            if name.endswith(suffix):
                return ftype

        # manifest.json at root
        if name == "manifest.json":
            return FileType.CONFIG

        # Fall back to Generic classification
        return super().classify_file(path)

    def parse_project(self, repo_root: Path, cred_filter: CredentialFilter) -> ScanResult:
        """Parse using the Workato-specific parser."""
        from intodoc.platforms.workato.parser import WorkatoParser

        return WorkatoParser(cred_filter).parse(repo_root)

    def get_prompt(self, pass_number: int) -> str:
        """workato/prompts/ -> _shared/prompts/ fallback chain."""
        prompt_dir = _PLUGIN_DIR / "prompts"
        if prompt_dir.exists():
            matches = list(prompt_dir.glob(f"p{pass_number:02d}*.txt"))
            if matches:
                return matches[0].read_text(encoding="utf-8")
        # Fall back to Generic (which falls to _shared)
        return super().get_prompt(pass_number)

    def get_best_practices(self) -> dict:
        """Load Workato-specific best practices."""
        if self._best_practices is None:
            bp_file = _PLUGIN_DIR / "best_practices.yaml"
            if bp_file.exists():
                self._best_practices = yaml.safe_load(
                    bp_file.read_text(encoding="utf-8")
                )
            else:
                self._best_practices = {"platform_name": "Workato"}
        return self._best_practices
