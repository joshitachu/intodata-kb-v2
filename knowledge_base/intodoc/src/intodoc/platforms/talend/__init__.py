"""Talend platform plugin."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from intodoc.models import FileType, ScanResult
from intodoc.platforms.base import PlatformPlugin

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

_PLUGIN_DIR = Path(__file__).parent


class TalendPlugin(PlatformPlugin):
    """Talend Cloud / Open Studio platform plugin."""

    name = "talend"
    display_name = "Talend Cloud"

    def __init__(self) -> None:
        self._file_rules: dict | None = None
        self._best_practices: dict | None = None

    @property
    def file_rules(self) -> dict:
        if self._file_rules is None:
            with open(_PLUGIN_DIR / "file_rules.yaml") as f:
                self._file_rules = yaml.safe_load(f)
        return self._file_rules

    @property
    def best_practices(self) -> dict:
        if self._best_practices is None:
            with open(_PLUGIN_DIR / "best_practices.yaml") as f:
                self._best_practices = yaml.safe_load(f)
        return self._best_practices

    def classify_file(self, path: Path) -> FileType:
        """Classify a file based on Talend file rules."""
        posix = path.as_posix()
        suffix = path.suffix.lower()

        # Screenshot / images — skip
        if suffix in {".screenshot", ".bmp", ".png", ".jpg"}:
            return FileType.UNKNOWN

        # Process jobs
        if "/process/" in posix and suffix == ".item":
            return FileType.JOB
        if "/process/" in posix and suffix == ".properties":
            return FileType.JOB_PROPERTIES

        # Metadata connections
        if "/metadata/connections/" in posix and suffix == ".item":
            return FileType.CONNECTION
        if "/metadata/" in posix and suffix == ".item" and "/connections/" not in posix:
            return FileType.SCHEMA

        # Context files
        if "/context/" in posix and suffix == ".item":
            return FileType.CONTEXT

        # Routines / code
        if "/code/routines/" in posix and suffix == ".item":
            return FileType.ROUTINE

        # Joblets
        if "/joblets/" in posix and suffix == ".item":
            return FileType.JOBLET

        # SQL patterns
        if "/sqlPatterns/" in posix:
            return FileType.SQL_PATTERN

        # Documentation
        if "/documentations/" in posix:
            return FileType.DOCUMENTATION

        # Properties files for non-job types
        if suffix == ".properties":
            return FileType.CONFIG

        return FileType.UNKNOWN

    def parse_project(self, repo_root: Path, cred_filter: CredentialFilter) -> ScanResult:
        """Parse the full Talend project."""
        from intodoc.platforms.talend.parser import TalendParser

        parser = TalendParser(cred_filter)
        return parser.parse(repo_root)

    def get_prompt(self, pass_number: int) -> str:
        """Load a Talend-tuned prompt for a given pass number."""
        prompt_dir = _PLUGIN_DIR / "prompts"
        # Match pNN.txt or pNN_description.txt
        matches = list(prompt_dir.glob(f"p{pass_number:02d}*.txt"))
        if not matches:
            raise FileNotFoundError(f"No prompt for pass {pass_number} in {prompt_dir}")
        return matches[0].read_text(encoding="utf-8")

    def get_best_practices(self) -> dict:
        return self.best_practices
