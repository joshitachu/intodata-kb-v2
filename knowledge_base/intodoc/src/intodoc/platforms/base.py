"""Abstract base class for platform plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from intodoc.models import FileType, ScanResult
    from intodoc.scanner.credential_filter import CredentialFilter


class PlatformPlugin(ABC):
    """Interface that every platform plugin must implement."""

    name: str  # e.g. "talend", "mulesoft", "dbt"
    display_name: str  # e.g. "Talend Cloud", "MuleSoft Anypoint"

    @abstractmethod
    def classify_file(self, path: Path) -> FileType:
        """Classify a file by its path/extension into a FileType."""
        ...

    @abstractmethod
    def parse_project(
        self, repo_root: Path, cred_filter: CredentialFilter, *, cache_dir: Path | None = None,
    ) -> ScanResult:
        """Parse the entire project and return structured scan results.

        The credential filter must be applied to raw text BEFORE parsing.
        Platforms without deterministic parse caching may ignore ``cache_dir``.
        """
        ...

    @abstractmethod
    def get_prompt(self, pass_number: int) -> str:
        """Return the platform-tuned prompt for a given analysis pass."""
        ...

    @abstractmethod
    def get_best_practices(self) -> dict:
        """Return platform-specific best practices for LLM context."""
        ...
