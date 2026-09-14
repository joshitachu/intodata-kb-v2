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
        self._skip_extensions: frozenset[str] | None = None

    @property
    def file_rules(self) -> dict:
        if self._file_rules is None:
            with open(_PLUGIN_DIR / "file_rules.yaml") as f:
                self._file_rules = yaml.safe_load(f)
        return self._file_rules

    @property
    def skip_extensions(self) -> frozenset[str]:
        """Extensions never classified, read from file_rules.yaml.

        Derived from the rules file rather than duplicated in code: the
        hardcoded set used to omit ``.jpeg`` and ``.gif``, which the YAML
        already listed, so those images were classified (and hashed) as
        UNKNOWN payload on every scan.
        """
        if self._skip_extensions is None:
            self._skip_extensions = frozenset(
                str(ext).lower()
                for ext in (self.file_rules.get("skip_extensions") or ())
            )
        return self._skip_extensions

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

        # Screenshot / images — skip (see `skip_extensions`)
        if suffix in self.skip_extensions:
            return FileType.UNKNOWN

        # Project descriptor: the only file that names the Talend project
        # itself, and the anchor _find_talend_project_roots discovers roots by.
        if path.name == "talend.project":
            return FileType.PROJECT

        # Transformation and contract artifacts, matched by extension so they
        # are recognized wherever a route stores them (route_resources/,
        # resources/, or beside the design). These must precede the
        # directory rules below, which would otherwise claim them as
        # undifferentiated resources.
        if suffix in {".xsl", ".xslt"}:
            return FileType.XSLT
        if suffix == ".wsdl":
            return FileType.WSDL

        # ESB service designs
        if "/services/" in posix and suffix == ".item":
            return FileType.SERVICE

        # Talend Data Mapper: maps carry the transformation, structures the
        # schema it is written against.
        if "/Maps/" in posix and suffix == ".xml":
            return FileType.TDM_MAP
        if "/Structures/" in posix and suffix == ".xml":
            return FileType.STRUCTURE
        if "/Namespace Containers/" in posix and suffix == ".xml":
            return FileType.NAMESPACE

        # Executable and reusable designs. All four directories hold the
        # same serialized ProcessType file; the Talend taxonomy (job vs
        # route vs joblet vs routelet) is `ParsedJob.artifact_kind`, which
        # the parser assigns from the directory. Before this, routes/ and
        # routelets/ items classified as UNKNOWN, so Route designs -- which
        # the parser has always parsed -- were absent from files_index.json
        # and from every file-count stat.
        if any(f"/{directory}/" in posix for directory in ("process", "routes")):
            if suffix == ".item":
                return FileType.JOB
            if suffix == ".properties":
                return FileType.JOB_PROPERTIES
        if any(f"/{directory}/" in posix for directory in ("joblets", "routelets")):
            if suffix == ".item":
                return FileType.JOBLET
            if suffix == ".properties":
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

        # SQL patterns
        if "/sqlPatterns/" in posix:
            return FileType.SQL_PATTERN

        # Documentation
        if "/documentations/" in posix:
            return FileType.DOCUMENTATION

        # Properties files for non-job types
        if suffix == ".properties":
            return FileType.CONFIG

        # Route/job support payloads (XSD, JSON samples, property files,
        # jars...). Recognized as inventory so they are never silently
        # invisible, but classification is not a claim that they are parsed:
        # see the capability ledger's unscanned_directory rows.
        if "/resources/" in posix or "/route_resources/" in posix:
            return FileType.RESOURCE

        return FileType.UNKNOWN

    def parse_project(
        self, repo_root: Path, cred_filter: CredentialFilter, *, cache_dir: Path | None = None,
    ) -> ScanResult:
        """Parse the full Talend project."""
        from intodoc.platforms.talend.parser import TalendParser

        parser = TalendParser(cred_filter, parse_cache_dir=cache_dir)
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
