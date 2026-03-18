"""Generic parser -- universal fallback that reads any codebase."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from intodoc.models import (
    Component,
    ComponentParam,
    Connection,
    ContextGroup,
    ContextVariable,
    FileType,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    Routine,
    ScanResult,
)
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

console = Console()


class GenericParser:
    """Parse any codebase into structured scan results.

    Groups files by top-level directory, creates jobs from sub-directories,
    and extracts context variables from .env/.properties files.
    """

    MAX_FILE_LINES = 300
    MAX_FILE_SIZE = 100_000  # 100 KB

    def __init__(self, cred_filter: CredentialFilter) -> None:
        self.cred_filter = cred_filter

    def parse(self, repo_root: Path) -> ScanResult:
        """Main entry point -- walk, classify, group, and parse."""
        from intodoc.platforms.generic import GenericPlugin

        plugin = GenericPlugin()
        all_files = walk_repo(repo_root, plugin)
        files = [f for f in all_files if f.file_type != FileType.UNKNOWN]

        # Group files by first-level directory
        dir_groups: dict[str, list] = {}
        for f in files:
            parts = Path(f.relative_path).parts
            group = parts[0] if len(parts) > 1 else "root"
            dir_groups.setdefault(group, []).append(f)

        jobs: list[ParsedJob] = []
        context_groups: list[ContextGroup] = []
        routines: list[Routine] = []
        connections: list[MetadataConnection] = []

        for group_name, group_files in sorted(dir_groups.items()):
            # Sub-group by second-level directory for job creation
            sub_groups = self._group_by_subdir(group_files)
            for sub_name, sub_files in sub_groups.items():
                job = self._build_job(sub_name, group_name, sub_files, repo_root)
                if job.components:
                    jobs.append(job)

            # Extract contexts from .env / .properties files
            for f in group_files:
                if f.file_type == FileType.CONTEXT:
                    ctx = self._parse_config_as_context(Path(f.path))
                    if ctx:
                        context_groups.append(ctx)

            # Extract routines from code files
            for f in group_files:
                if f.file_type == FileType.ROUTINE:
                    routine = self._parse_code_as_routine(Path(f.path), repo_root)
                    if routine:
                        routines.append(routine)

            # Extract connections from config files referencing databases/APIs
            for f in group_files:
                if f.file_type == FileType.CONFIG:
                    conn = self._detect_connection(Path(f.path))
                    if conn:
                        connections.append(conn)

        manifest = self._build_manifest(jobs)

        stats = {
            "total_files": len(all_files),
            "classified_files": len(files),
            "jobs": len(jobs),
            "routines": len(routines),
            "context_groups": len(context_groups),
            "connections": len(connections),
        }

        console.print(f"  Generic parser: {len(jobs)} jobs, {len(routines)} routines")

        return ScanResult(
            platform="generic",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=connections,
            context_groups=context_groups,
            routines=routines,
            manifest=manifest,
            stats=stats,
        )

    # ── Grouping ─────────────────────────────────────────────────────

    @staticmethod
    def _group_by_subdir(
        files: list,
    ) -> dict[str, list]:
        """Group files by second-level directory (or filename if flat)."""
        sub_groups: dict[str, list] = {}
        for f in files:
            parts = Path(f.relative_path).parts
            if len(parts) > 2:
                sub_name = parts[1]
            elif len(parts) == 2:
                sub_name = Path(parts[1]).stem
            else:
                sub_name = Path(parts[0]).stem
            sub_groups.setdefault(sub_name, []).append(f)
        return sub_groups

    # ── Job Building ─────────────────────────────────────────────────

    def _build_job(
        self,
        name: str,
        group: str,
        files: list,
        repo_root: Path,
    ) -> ParsedJob:
        """Build a ParsedJob from a group of files."""
        components: list[Component] = []
        job_connections: list[Connection] = []

        prev_name = ""
        for f in files:
            content = self._read_file_content(Path(f.path))
            comp_type = self._infer_component_type(f.file_type)

            comp = Component(
                unique_name=Path(f.relative_path).stem,
                component_type=comp_type,
                params=[
                    ComponentParam(
                        name="file_path",
                        value=f.relative_path,
                        field_type="TEXT",
                    ),
                    ComponentParam(
                        name="content",
                        value=content,
                        field_type="FILE_CONTENT",
                    ),
                ],
            )
            components.append(comp)

            # Create sequential connections between files
            if prev_name:
                job_connections.append(
                    Connection(
                        name=f"seq_{prev_name}_to_{comp.unique_name}",
                        source=prev_name,
                        target=comp.unique_name,
                        connector_name="FLOW",
                        line_style=0,
                    )
                )
            prev_name = comp.unique_name

        return ParsedJob(
            name=name,
            path=f"{group}/{name}",
            group=group,
            components=components,
            connections=job_connections,
        )

    @staticmethod
    def _infer_component_type(ft: FileType) -> str:
        """Map FileType to a component type string."""
        return {
            FileType.ROUTINE: "CodeFile",
            FileType.SQL_PATTERN: "SQLScript",
            FileType.CONFIG: "ConfigFile",
            FileType.SCHEMA: "DataFile",
            FileType.DOCUMENTATION: "DocumentFile",
            FileType.CONTEXT: "ContextFile",
            FileType.CONNECTION: "ConnectionFile",
        }.get(ft, "GenericFile")

    def _read_file_content(self, path: Path) -> str:
        """Read file content with size/line limits and credential filtering."""
        try:
            if path.stat().st_size > self.MAX_FILE_SIZE:
                return f"[FILE TOO LARGE: {path.stat().st_size} bytes, skipped]"
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return "[BINARY OR UNREADABLE FILE]"

        text = self.cred_filter.filter_text(text)
        lines = text.splitlines()
        if len(lines) > self.MAX_FILE_LINES:
            return (
                "\n".join(lines[: self.MAX_FILE_LINES])
                + f"\n[TRUNCATED: {len(lines)} total lines]"
            )
        return text

    # ── Context Parsing ──────────────────────────────────────────────

    def _parse_config_as_context(self, path: Path) -> ContextGroup | None:
        """Parse .env/.properties/.cfg files as context variables."""
        try:
            text = self.cred_filter.filter_text(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            return None

        variables: list[ContextVariable] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            # Handle KEY=VALUE or KEY: VALUE
            sep = "=" if "=" in line else (":" if ":" in line else None)
            if not sep:
                continue
            key, _, val = line.partition(sep)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                variables.append(
                    ContextVariable(
                        name=key,
                        type="String",
                        values={"_default": val},
                    )
                )

        if not variables:
            return None

        return ContextGroup(name=path.stem, variables=variables)

    # ── Routine Parsing ──────────────────────────────────────────────

    def _parse_code_as_routine(
        self, path: Path, repo_root: Path,
    ) -> Routine | None:
        """Parse a code file as a Routine."""
        content = self._read_file_content(path)
        if content.startswith("["):
            return None  # Skipped (too large or binary)

        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.name

        return Routine(
            name=rel,
            source_code=content,
            is_system=False,
        )

    # ── Connection Detection ─────────────────────────────────────────

    def _detect_connection(self, path: Path) -> MetadataConnection | None:
        """Try to detect database/API connection info from config files."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        # Only parse JSON/YAML config files
        suffix = path.suffix.lower()
        data: dict = {}

        if suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(text) or {}
            except Exception:
                return None
        else:
            return None

        if not isinstance(data, dict):
            return None

        # Look for common connection keys
        flat = self._flatten_dict(data)
        host = ""
        port = ""
        database = ""
        conn_type = ""

        for key, val in flat.items():
            key_lower = key.lower()
            val_str = str(val)
            if "host" in key_lower and not host:
                host = val_str
            elif "port" in key_lower and not port:
                port = val_str
            elif "database" in key_lower or "dbname" in key_lower:
                database = val_str
            elif "driver" in key_lower or "dialect" in key_lower:
                conn_type = val_str
            elif key_lower.endswith("url") and ("jdbc" in val_str or "://" in val_str):
                conn_type = "URL"
                host = val_str

        # Only create connection if we found meaningful connection info
        if not (host or database or conn_type):
            return None

        return MetadataConnection(
            xmi_id=path.stem,
            name=path.stem,
            connection_type=conn_type or "unknown",
            host=host,
            port=port,
            database=database,
        )

    @staticmethod
    def _flatten_dict(d: dict, prefix: str = "") -> dict[str, str]:
        """Flatten a nested dict to dot-separated keys."""
        items: dict[str, str] = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.update(GenericParser._flatten_dict(v, key))
            elif isinstance(v, (str, int, float, bool)):
                items[key] = str(v)
        return items

    # ── Manifest Building ────────────────────────────────────────────

    @staticmethod
    def _build_manifest(jobs: list[ParsedJob]) -> ProjectManifest:
        """Build a basic manifest from parsed jobs."""
        job_groups: dict[str, str] = {}
        for job in jobs:
            job_groups[job.name] = job.group

        return ProjectManifest(
            job_groups=job_groups,
        )
