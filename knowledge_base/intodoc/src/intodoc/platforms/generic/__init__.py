"""Generic platform plugin -- universal fallback for any codebase."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from intodoc.models import FileType, ScanResult
from intodoc.platforms.base import PlatformPlugin
from intodoc.platforms.base_distiller import BaseDistiller

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

_PLUGIN_DIR = Path(__file__).parent
_SHARED_DIR = _PLUGIN_DIR.parent / "_shared"


class GenericPlugin(PlatformPlugin):
    """Universal fallback plugin for any uploaded ZIP/codebase."""

    name = "generic"
    display_name = "Generic Integration Project"

    # Maps extensions to non-ROUTINE types only.
    # Purpose: route files to the right processing path (connection detection,
    # SQL extraction, etc.). NOT a gate -- code files are found via binary detection.
    # Adding a new programming language requires zero changes here.
    _EXT_MAP: dict[str, FileType] = {
        # SQL -> SQL_PATTERN (triggers SQL-specific param extraction)
        ".sql": FileType.SQL_PATTERN,
        ".ddl": FileType.SQL_PATTERN,
        ".dml": FileType.SQL_PATTERN,
        # Config -> CONFIG (triggers connection string detection)
        ".json": FileType.CONFIG,
        ".yaml": FileType.CONFIG,
        ".yml": FileType.CONFIG,
        ".xml": FileType.CONFIG,
        ".toml": FileType.CONFIG,
        ".hcl": FileType.CONFIG,
        ".tf": FileType.CONFIG,
        ".tfvars": FileType.CONFIG,
        ".config": FileType.CONFIG,
        ".csproj": FileType.CONFIG,
        ".vbproj": FileType.CONFIG,
        ".fsproj": FileType.CONFIG,
        ".sln": FileType.CONFIG,
        ".gradle": FileType.CONFIG,
        ".gemspec": FileType.CONFIG,
        ".nuspec": FileType.CONFIG,
        # Docs -> DOCUMENTATION
        ".md": FileType.DOCUMENTATION,
        ".rst": FileType.DOCUMENTATION,
        ".txt": FileType.DOCUMENTATION,
        # Data / Schema -> SCHEMA
        ".csv": FileType.SCHEMA,
        ".tsv": FileType.SCHEMA,
        ".xsd": FileType.SCHEMA,
        ".proto": FileType.SCHEMA,
        # Env/config -> CONTEXT
        ".env": FileType.CONTEXT,
        ".properties": FileType.CONTEXT,
        ".cfg": FileType.CONTEXT,
        ".ini": FileType.CONTEXT,
        ".conf": FileType.CONTEXT,
    }

    # Extensions with zero integration semantics -- skip before binary detection.
    # HTML is intentionally kept (form actions, API refs, data bindings).
    _SKIP_EXTS: frozenset[str] = frozenset({
        # Styling
        ".css", ".scss", ".sass", ".less", ".styl",
        # Fonts
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        # Images
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".tiff", ".svg",
        # Sourcemaps
        ".map",
    })

    # Skip entire directory subtrees (third-party code, build output, caches).
    # This is the only list that can't be replaced by binary detection --
    # node_modules contains 50,000 valid text files we simply don't want.
    _SKIP_DIRS: frozenset[str] = frozenset({
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".tox", "dist", "build", ".eggs", ".mypy_cache",
        ".pytest_cache", ".idea", ".vscode", "target",
        ".gradle", ".mvn", "bin", "obj", ".next",
        "vendor", ".bundle", "packages", "Pods",
        "coverage", ".dart_tool", "__generated__", ".nyc_output",
    })

    def __init__(self) -> None:
        self._file_rules: dict | None = None
        self._best_practices: dict | None = None

    @staticmethod
    def _is_binary(path: Path, sample: int = 8192) -> bool:
        """Return True if the file appears to be binary (contains null bytes)."""
        try:
            chunk = path.read_bytes()[:sample]
            return b"\x00" in chunk
        except OSError:
            return True

    def classify_file(self, path: Path) -> FileType:
        """Classify a file for scanning.

        1. Skip noise directories.
        2. If extension maps to a non-ROUTINE type, return it (CONFIG, SQL, etc.).
        3. Binary detection -- skip if null bytes found in first 8 KB.
        4. Anything readable: scan as ROUTINE.

        Code files (.py, .cs, .rb, .zig, anything) are found by step 3+4,
        not by a hardcoded list. No language ever needs to be added here.
        """
        if any(part in self._SKIP_DIRS for part in path.parts):
            return FileType.UNKNOWN

        suffix = path.suffix.lower()

        if suffix in self._SKIP_EXTS:
            return FileType.UNKNOWN

        if suffix in self._EXT_MAP:
            return self._EXT_MAP[suffix]

        if self._is_binary(path):
            return FileType.UNKNOWN

        return FileType.ROUTINE

    def parse_project(self, repo_root: Path, cred_filter: CredentialFilter) -> ScanResult:
        """Parse the project using the generic parser."""
        from intodoc.platforms.generic.parser import GenericParser

        return GenericParser(cred_filter).parse(repo_root)

    def get_prompt(self, pass_number: int) -> str:
        """Load prompt: generic/prompts/ -> _shared/prompts/ fallback chain."""
        for prompt_dir in [_PLUGIN_DIR / "prompts", _SHARED_DIR / "prompts"]:
            if prompt_dir.exists():
                matches = list(prompt_dir.glob(f"p{pass_number:02d}*.txt"))
                if matches:
                    return matches[0].read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"No prompt for pass {pass_number} in generic or _shared"
        )

    def get_distiller(self) -> BaseDistiller:
        """Return a GenericDistiller instance."""
        from intodoc.platforms.generic.distiller import GenericDistiller
        return GenericDistiller()

    def get_best_practices(self) -> dict:
        """Load best practices from YAML."""
        if self._best_practices is None:
            bp_file = _PLUGIN_DIR / "best_practices.yaml"
            if bp_file.exists():
                self._best_practices = yaml.safe_load(
                    bp_file.read_text(encoding="utf-8")
                )
            else:
                self._best_practices = {"platform_name": "Generic"}
        return self._best_practices
