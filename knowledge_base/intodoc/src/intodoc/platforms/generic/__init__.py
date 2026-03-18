"""Generic platform plugin -- universal fallback for any codebase."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from intodoc.models import FileType, ScanResult
from intodoc.platforms.base import PlatformPlugin

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

_PLUGIN_DIR = Path(__file__).parent
_SHARED_DIR = _PLUGIN_DIR.parent / "_shared"


class GenericPlugin(PlatformPlugin):
    """Universal fallback plugin for any uploaded ZIP/codebase."""

    name = "generic"
    display_name = "Generic Integration Project"

    # Extension -> FileType mapping
    _EXT_MAP: dict[str, FileType] = {
        # Code files -> ROUTINE
        ".py": FileType.ROUTINE,
        ".java": FileType.ROUTINE,
        ".scala": FileType.ROUTINE,
        ".rb": FileType.ROUTINE,
        ".js": FileType.ROUTINE,
        ".ts": FileType.ROUTINE,
        ".go": FileType.ROUTINE,
        ".rs": FileType.ROUTINE,
        ".sh": FileType.ROUTINE,
        ".bash": FileType.ROUTINE,
        ".ps1": FileType.ROUTINE,
        ".groovy": FileType.ROUTINE,
        ".kt": FileType.ROUTINE,
        ".r": FileType.ROUTINE,
        ".pl": FileType.ROUTINE,
        ".lua": FileType.ROUTINE,
        # SQL -> SQL_PATTERN
        ".sql": FileType.SQL_PATTERN,
        ".ddl": FileType.SQL_PATTERN,
        ".dml": FileType.SQL_PATTERN,
        # Config -> CONFIG
        ".json": FileType.CONFIG,
        ".yaml": FileType.CONFIG,
        ".yml": FileType.CONFIG,
        ".xml": FileType.CONFIG,
        ".toml": FileType.CONFIG,
        ".hcl": FileType.CONFIG,
        # Docs -> DOCUMENTATION
        ".md": FileType.DOCUMENTATION,
        ".rst": FileType.DOCUMENTATION,
        ".txt": FileType.DOCUMENTATION,
        # Data / Schema -> SCHEMA
        ".csv": FileType.SCHEMA,
        ".tsv": FileType.SCHEMA,
        ".parquet": FileType.SCHEMA,
        ".avro": FileType.SCHEMA,
        ".xsd": FileType.SCHEMA,
        ".proto": FileType.SCHEMA,
        # Env/config -> CONTEXT
        ".env": FileType.CONTEXT,
        ".properties": FileType.CONTEXT,
        ".cfg": FileType.CONTEXT,
        ".ini": FileType.CONTEXT,
        ".conf": FileType.CONTEXT,
    }

    _SKIP_DIRS: frozenset[str] = frozenset({
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".tox", "dist", "build", ".eggs", ".mypy_cache",
        ".pytest_cache", ".idea", ".vscode", "target",
        ".gradle", ".mvn", "bin", "obj", ".next",
    })

    _SKIP_EXTS: frozenset[str] = frozenset({
        ".pyc", ".class", ".jar", ".war", ".exe", ".dll", ".so",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
        ".woff", ".woff2", ".ttf", ".eot",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".o", ".a", ".lib", ".pdb", ".map",
    })

    def __init__(self) -> None:
        self._file_rules: dict | None = None
        self._best_practices: dict | None = None

    def classify_file(self, path: Path) -> FileType:
        """Classify a file by extension, skipping binary/noise."""
        suffix = path.suffix.lower()
        if suffix in self._SKIP_EXTS:
            return FileType.UNKNOWN
        if any(part in self._SKIP_DIRS for part in path.parts):
            return FileType.UNKNOWN
        return self._EXT_MAP.get(suffix, FileType.UNKNOWN)

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
