"""Generic parser -- language-aware scanner for any codebase."""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

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
from intodoc.platforms.base_parser import BaseParser
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

console = Console()

# ── File size limits by type ──────────────────────────────────────────────────

_SOURCE_EXTS: frozenset[str] = frozenset({
    ".py", ".java", ".kt", ".scala", ".groovy",
    ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte",
    ".cs", ".vb", ".fs", ".fsx", ".cshtml", ".razor", ".aspx", ".ascx",
    ".go", ".rs", ".rb", ".rake", ".erb", ".pl", ".lua", ".r",
    ".sh", ".bash", ".ps1",
    ".php", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".swift", ".m", ".ex", ".exs", ".erl", ".dart",
    ".clj", ".cljs", ".hs", ".jl",
})

_CONFIG_EXTS: frozenset[str] = frozenset({
    ".xml", ".json", ".yaml", ".yml", ".toml", ".hcl",
    ".sql", ".ddl", ".dml",
    ".config", ".properties", ".env", ".cfg", ".ini", ".conf",
})

MAX_FILE_SIZE = 200_000  # 200 KB
MAX_SCHEMA_FILE_SIZE = 2_000_000  # DDL can be large but still cheap to parse deterministically.
MAX_LINES_SOURCE = 1500
MAX_LINES_CONFIG = 600
MAX_LINES_DEFAULT = 300

# ── Entry-point detection patterns ───────────────────────────────────────────

# Structural patterns that signal a file is an entry point -- no language
# knowledge required. Tag is informational only.
_ENTRY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Any function/method named "main" (Go, Rust, C, Java static, C# static, etc.)
    (re.compile(r'\bmain\s*\('), "ep_main"),
    # HTTP verb routing (.get("/path"), .post("/path"), etc. -- any framework)
    (re.compile(r'(?:\.|\b)(?:get|post|put|delete|patch)\s*\(\s*["\'][/\w]', re.I), "ep_route"),
    # Decorator/attribute before a function definition (@Something\ndef/func/fn ...)
    (re.compile(r'^@\w[\w.]*.*\n\s*(?:def|func|fn|function|fun)\s+\w', re.MULTILINE), "ep_decorated"),
    # C#/Rust bracket attributes before a function ([HttpGet], [tokio::main], etc.)
    (re.compile(r'^\s*\[\w[\w.:]+\]\s*\n\s*(?:public|async|fn|static)\s+', re.MULTILINE), "ep_attribute"),
    # Scheduled/worker signals
    (re.compile(r'\bschedule\w*\s*\(|@cron\b|cron\s*\.', re.I), "ep_scheduled"),
    # Message/event consumer signals
    (re.compile(r'\blisten(?:er)?\s*\(|\bsubscribe\s*\(|\bconsume\s*\(', re.I), "ep_consumer"),
    # SQL stored objects
    (re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION|TRIGGER)\b', re.I), "sql_object"),
]

# Filenames that indicate an entry point regardless of content
_ENTRY_FILENAMES: frozenset[str] = frozenset({
    "main", "app", "index", "server", "cmd", "run", "start", "entrypoint",
    "handler", "lambda_function", "function", "worker",
})

# Names that look like function calls but are control-flow or type keywords
_BRACE_SKIP: frozenset[str] = frozenset({
    "if", "for", "while", "switch", "catch", "else", "do",
    "return", "case", "class", "struct", "namespace", "try",
})

# ── Import → external system map ─────────────────────────────────────────────

_IMPORT_TO_SYSTEM: dict[str, str] = {
    "boto3": "AWS", "botocore": "AWS", "com.amazonaws": "AWS", "software.amazon": "AWS",
    "google.cloud": "GCP", "com.google.cloud": "GCP", "googleapiclient": "GCP",
    "azure": "Azure", "Microsoft.Azure": "Azure", "azure.storage": "Azure",
    "redis": "Redis", "ioredis": "Redis", "StackExchange.Redis": "Redis", "aioredis": "Redis",
    "pymongo": "MongoDB", "mongoengine": "MongoDB", "mongoose": "MongoDB",
    "MongoDB.Driver": "MongoDB", "motor": "MongoDB",
    "kafka": "Kafka", "org.apache.kafka": "Kafka", "Confluent.Kafka": "Kafka",
    "confluent_kafka": "Kafka", "aiokafka": "Kafka",
    "elasticsearch": "Elasticsearch", "elastic": "Elasticsearch",
    "org.elasticsearch": "Elasticsearch",
    "psycopg2": "PostgreSQL", "asyncpg": "PostgreSQL", "Npgsql": "PostgreSQL",
    "org.postgresql": "PostgreSQL",
    "pymssql": "SQL Server", "System.Data.SqlClient": "SQL Server",
    "com.microsoft.sqlserver": "SQL Server", "pyodbc": "SQL Server",
    "Microsoft.Data.SqlClient": "SQL Server",
    "cx_Oracle": "Oracle DB", "oracledb": "Oracle DB", "oracle.jdbc": "Oracle DB",
    "pika": "RabbitMQ", "aio_pika": "RabbitMQ", "com.rabbitmq": "RabbitMQ",
    "sendgrid": "SendGrid", "mailchimp": "Mailchimp",
    "stripe": "Stripe", "paypalrestsdk": "PayPal",
    "twilio": "Twilio",
    "Microsoft.ApplicationInsights": "Application Insights",
    "opentelemetry": "OpenTelemetry",
    "requests": "HTTP API", "httpx": "HTTP API", "aiohttp": "HTTP API",
    "axios": "HTTP API", "node-fetch": "HTTP API",
    "System.Net.Http": "HTTP API", "RestSharp": "HTTP API",
    "celery": "Celery",
    "ldap3": "LDAP", "Microsoft.Identity": "Azure AD",
    "ServiceStack.OrmLite": "OrmLite/DB", "ServiceStack": "ServiceStack",
    "Funq": "Funq IoC", "Jose": "JWT", "pyotp": "TOTP/MFA",
    "PyJWT": "JWT", "sqlalchemy": "SQLAlchemy/ORM", "alembic": "Alembic/Migrations",
    "django.db": "SQL DB",
    "spring.data": "SQL DB", "hibernate": "SQL DB",
    "Microsoft.EntityFrameworkCore": "SQL DB",
}

_SENSITIVE_CONFIG_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|credential|"
    r"connectionstring|clientsecret|client[_-]?secret)",
    re.IGNORECASE,
)


class GenericParser(BaseParser):
    """Parse any codebase into structured scan results with language-aware extraction."""

    def __init__(self, cred_filter: CredentialFilter) -> None:
        super().__init__(cred_filter)

    # ── Main entry ────────────────────────────────────────────────────────────

    def parse(self, repo_root: Path) -> ScanResult:
        """Walk, classify, detect entry points, group, and parse."""
        from intodoc.platforms.generic import GenericPlugin

        self._warnings = []
        self._warn_root = repo_root

        plugin = GenericPlugin()
        all_files = walk_repo(repo_root, plugin)
        files = [f for f in all_files if f.file_type != FileType.UNKNOWN]

        # Phase 1: Identify entry-point files (each becomes its own job)
        entry_point_paths: set[str] = set()
        for f in files:
            if f.file_type == FileType.ROUTINE:
                snippet = self._read_file_snippet(Path(f.path))
                if self._is_entry_point(Path(f.path), snippet):
                    entry_point_paths.add(f.relative_path)

        # Phase 2: Group remaining files by directory for jobs
        dir_groups: dict[str, list] = {}
        for f in files:
            if f.relative_path in entry_point_paths:
                continue
            parts = Path(f.relative_path).parts
            group = parts[0] if len(parts) > 1 else "root"
            dir_groups.setdefault(group, []).append(f)

        jobs: list[ParsedJob] = []
        context_groups: list[ContextGroup] = []
        routines: list[Routine] = []
        connections: list[MetadataConnection] = []

        # Build entry-point jobs (one file → one job)
        ep_by_group: dict[str, list] = {}
        for f in files:
            if f.relative_path in entry_point_paths:
                parts = Path(f.relative_path).parts
                group = parts[0] if len(parts) > 1 else "root"
                ep_by_group.setdefault(group, []).append(f)

        for group_name, ep_files in sorted(ep_by_group.items()):
            for f in ep_files:
                job = self._build_entry_point_job(f, repo_root)
                if job.components:
                    jobs.append(job)

        # Build directory-grouped jobs
        for group_name, group_files in sorted(dir_groups.items()):
            sub_groups = self._group_by_subdir(group_files)
            for sub_name, sub_files in sub_groups.items():
                job = self._build_group_job(sub_name, group_name, sub_files, repo_root)
                if job.components:
                    jobs.append(job)

            # Context, routines, connections from this group
            for f in group_files:
                if f.file_type == FileType.CONTEXT:
                    ctx = self._parse_config_as_context(Path(f.path))
                    if ctx:
                        context_groups.append(ctx)
                if f.file_type == FileType.ROUTINE:
                    routine = self._parse_code_as_routine(Path(f.path), repo_root)
                    if routine:
                        routines.append(routine)
                if f.file_type == FileType.CONFIG:
                    detected, app_settings = self._detect_connection(Path(f.path), f.relative_path)
                    connections.extend(detected)
                    context_groups.extend(app_settings)

        # Also extract context/routines/connections from entry-point files
        for f in files:
            if f.relative_path not in entry_point_paths:
                continue
            if f.file_type == FileType.ROUTINE:
                routine = self._parse_code_as_routine(Path(f.path), repo_root)
                if routine:
                    routines.append(routine)
            if f.file_type == FileType.CONTEXT:
                ctx = self._parse_config_as_context(Path(f.path))
                if ctx:
                    context_groups.append(ctx)
            if f.file_type == FileType.CONFIG:
                detected, app_settings = self._detect_connection(Path(f.path), f.relative_path)
                connections.extend(detected)
                context_groups.extend(app_settings)

        self._apply_component_connection_refs(jobs, connections)

        manifest = self._build_manifest(jobs, connections)
        schema_definitions = self._extract_schema_definitions(files, repo_root)

        stats = {
            "total_files": len(all_files),
            "classified_files": len(files),
            "entry_point_jobs": len(entry_point_paths),
            "jobs": len(jobs),
            "routines": len(routines),
            "context_groups": len(context_groups),
            "connections": len(connections),
            "schema_definitions": len(schema_definitions),
            "total_warnings": len(self._warnings),
        }

        console.print(
            f"  Generic parser: {len(jobs)} jobs "
            f"({len(entry_point_paths)} entry-point, "
            f"{len(jobs) - len(entry_point_paths)} directory-grouped), "
            f"{len(routines)} routines"
        )

        return ScanResult(
            platform="generic",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=connections,
            context_groups=context_groups,
            routines=routines,
            manifest=manifest,
            schema_definitions=schema_definitions,
            stats=stats,
            warnings=list(self._warnings),
        )

    # ── File reading ──────────────────────────────────────────────────────────

    def _max_lines(self, path: Path) -> int:
        ext = path.suffix.lower()
        if ext in _SOURCE_EXTS:
            return MAX_LINES_SOURCE
        if ext in _CONFIG_EXTS:
            return MAX_LINES_CONFIG
        return MAX_LINES_DEFAULT

    def _read_file_content(self, path: Path) -> str:
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                return f"[FILE TOO LARGE: {path.stat().st_size} bytes, skipped]"
            text = self._read_text_with_encoding_detection(path)
        except OSError:
            return "[BINARY OR UNREADABLE FILE]"
        text = self.cred_filter.filter_text(text, filename=str(path))
        lines = text.splitlines()
        limit = self._max_lines(path)
        if len(lines) > limit:
            return (
                "\n".join(lines[:limit])
                + f"\n[TRUNCATED: {len(lines)} total lines]"
            )
        return text

    def _read_file_snippet(self, path: Path, lines: int = 50) -> str:
        """Read only the first N lines for entry-point detection."""
        try:
            text = self._read_text_with_encoding_detection(path)
        except OSError:
            return ""
        return "\n".join(text.splitlines()[:lines])

    def _read_schema_content(self, path: Path) -> str:
        """Read SQL DDL for deterministic schema extraction without prompt truncation."""
        try:
            if path.stat().st_size > MAX_SCHEMA_FILE_SIZE:
                return ""
            text = self._read_text_with_encoding_detection(path)
        except OSError:
            return ""
        return self.cred_filter.filter_text(text, filename=str(path))

    @staticmethod
    def _read_text_with_encoding_detection(path: Path) -> str:
        raw = path.read_bytes()
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace")
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig", errors="replace")

        # SQL Server exports are often UTF-16 LE without a useful extension hint.
        sample = raw[:4096]
        if sample:
            even_nulls = sample[0::2].count(0)
            odd_nulls = sample[1::2].count(0)
            if odd_nulls > max(8, len(sample) // 10) and even_nulls < odd_nulls // 4:
                return raw.decode("utf-16-le", errors="replace")
            if even_nulls > max(8, len(sample) // 10) and odd_nulls < even_nulls // 4:
                return raw.decode("utf-16-be", errors="replace")

        return raw.decode("utf-8", errors="replace")

    # ── Entry-point detection ─────────────────────────────────────────────────

    @staticmethod
    def _is_entry_point(path: Path, snippet: str) -> bool:
        """Return True if the file is an entry point by name or content."""
        if path.stem.lower() in _ENTRY_FILENAMES:
            return True
        for pattern, _ in _ENTRY_PATTERNS:
            if pattern.search(snippet):
                return True
        if path.suffix.lower() in (".sql", ".ddl", ".dml") and snippet.strip():
            return True
        return False

    # ── Job building ──────────────────────────────────────────────────────────

    def _build_entry_point_job(self, f: object, repo_root: Path) -> ParsedJob:
        """Build a single-file job from an entry-point file."""
        path = Path(f.path)  # type: ignore[attr-defined]
        rel = f.relative_path  # type: ignore[attr-defined]
        parts = Path(rel).parts
        group = parts[0] if len(parts) > 1 else "root"
        name = path.stem

        content = self._read_file_content(path)
        comp = self._build_component(f, content, path)
        return ParsedJob(
            name=name,
            path=rel,
            group=group,
            components=[comp],
            connections=[],
        )

    def _build_group_job(
        self,
        name: str,
        group: str,
        files: list,
        repo_root: Path,
    ) -> ParsedJob:
        """Build a job from a directory-grouped set of files."""
        components: list[Component] = []
        job_connections: list[Connection] = []
        prev_name = ""

        for f in files:
            path = Path(f.path)
            content = self._read_file_content(path)
            comp = self._build_component(f, content, path)
            components.append(comp)

            if prev_name:
                job_connections.append(Connection(
                    name=f"seq_{prev_name}_to_{comp.unique_name}",
                    source=prev_name,
                    target=comp.unique_name,
                    connector_name="FLOW",
                    line_style=0,
                ))
            prev_name = comp.unique_name

        return ParsedJob(
            name=name,
            path=f"{group}/{name}",
            group=group,
            components=components,
            connections=job_connections,
        )

    @staticmethod
    def _group_by_subdir(files: list) -> dict[str, list]:
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

    # ── Component building ────────────────────────────────────────────────────

    def _build_component(self, f: object, content: str, path: Path) -> Component:
        """Build a Component with full content + language-specific structured params."""
        ft = f.file_type  # type: ignore[attr-defined]
        rel = f.relative_path  # type: ignore[attr-defined]

        base_params = [
            ComponentParam(name="file_path", value=rel, field_type="TEXT"),
            ComponentParam(name="content", value=content, field_type="FILE_CONTENT"),
        ]

        if not content.startswith("["):
            extra = self._extract_language_params(path, content)
            base_params.extend(extra)

        return Component(
            unique_name=path.stem,
            component_type=self._infer_component_type(ft),
            params=base_params,
        )

    @staticmethod
    def _infer_component_type(ft: FileType) -> str:
        return {
            FileType.ROUTINE: "CodeFile",
            FileType.SQL_PATTERN: "SQLScript",
            FileType.CONFIG: "ConfigFile",
            FileType.SCHEMA: "DataFile",
            FileType.DOCUMENTATION: "DocumentFile",
            FileType.CONTEXT: "ContextFile",
            FileType.CONNECTION: "ConnectionFile",
        }.get(ft, "GenericFile")

    # ── Universal param extraction (no language-specific knowledge) ──────────

    def _extract_language_params(self, path: Path, content: str) -> list[ComponentParam]:
        """Extract structured params. SQL gets dedicated extraction; all other files use universal heuristics."""
        if path.suffix.lower() in (".sql", ".ddl", ".dml"):
            return self._extract_sql_params(content)
        return self._extract_universal_params(content)

    def _extract_universal_params(self, content: str) -> list[ComponentParam]:
        params: list[ComponentParam] = []

        imports = self._extract_imports_universal(content)
        if imports:
            params.append(ComponentParam(name="imports", value="\n".join(imports[:40]), field_type="TEXT"))

        classes = self._extract_class_names_universal(content)
        if classes:
            params.append(ComponentParam(name="class_name", value=", ".join(classes[:10]), field_type="TEXT"))

        funcs = self._extract_function_names_universal(content)
        if funcs:
            params.append(ComponentParam(name="method_names", value=", ".join(funcs[:20]), field_type="TEXT"))

        annotations = self._extract_annotations_universal(content)
        if annotations:
            params.append(ComponentParam(name="annotation", value=", ".join(annotations[:10]), field_type="TEXT"))

        routes = self._extract_routes_universal(content)
        if routes:
            params.append(ComponentParam(name="routes", value=", ".join(routes[:10]), field_type="TEXT"))

        conn = self._extract_connection_string_universal(content)
        if conn:
            params.append(ComponentParam(name="connection_string", value=conn, field_type="TEXT"))

        for i, sql in enumerate(self._extract_embedded_sql_universal(content)[:5], 1):
            params.append(ComponentParam(name=f"sql_{i}", value=sql[:500], field_type="TEXT"))

        return params

    @staticmethod
    def _extract_imports_universal(content: str) -> list[str]:
        # Pass 1: single-line import statements (Python, Java, Rust, Ruby, C#, TS, C, PHP, ...)
        single = re.findall(
            r'^\s*(?:import|from|require|use|using|include|#include)\s+\S[^\n]*',
            content, re.MULTILINE,
        )
        # Pass 2: parenthesised import blocks (Go: import (\n    "pkg"\n))
        block: list[str] = []
        for block_body in re.findall(r'\bimport\s*\(([^)]+)\)', content, re.DOTALL):
            for line in block_body.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                    continue
                m = re.search(r'["\']([^"\']+)["\']', stripped)
                if m:
                    block.append(f'import "{m.group(1)}"')
        return list(dict.fromkeys(single + block))

    @staticmethod
    def _extract_class_names_universal(content: str) -> list[str]:
        return re.findall(
            r'(?:^|\s)(?:class|struct|interface|trait|enum|record)\s+(\w+)',
            content, re.MULTILINE,
        )

    @staticmethod
    def _extract_function_names_universal(content: str) -> list[str]:
        # Pass 1: keyword-prefixed definitions (Python def, Go func, Rust fn, JS function, ...)
        keyword_funcs = re.findall(
            r'(?:^|\s)(?:def|func|fn|function|fun|sub|proc|method)\s+([a-zA-Z][a-zA-Z0-9_]*)\s*[\(\[]',
            content, re.MULTILINE,
        )
        funcs = [f for f in dict.fromkeys(keyword_funcs) if not f.startswith("_")]
        if funcs:
            return funcs
        # Pass 2: brace-delimited fallback — name(params) { pattern (C, C++, and similar)
        # Only fires when Pass 1 finds nothing so it won't affect keyword-based languages.
        brace_funcs = re.findall(
            r'\b([a-zA-Z_]\w*)\s*\([^;)]{0,200}\)\s*(?:const\s*|noexcept\s*|override\s*|final\s*)*\{',
            content,
        )
        return [f for f in dict.fromkeys(brace_funcs) if f not in _BRACE_SKIP and not f.startswith("_")]

    @staticmethod
    def _extract_annotations_universal(content: str) -> list[str]:
        at_style = re.findall(r'@(\w+(?:\.\w+)*)(?:\s*\([^\n)]*\))?', content)
        bracket_style = re.findall(r'^\s*\[(\w+(?:[.:][\w.:]+)*)(?:\([^\]]*\))?\]', content, re.MULTILINE)
        return list(dict.fromkeys(at_style + bracket_style))

    @staticmethod
    def _extract_routes_universal(content: str) -> list[str]:
        dot_routes = re.findall(
            r'\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
            content, re.I,
        )
        verb_routes = re.findall(
            r'\b(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s"\'<>{}\n]+)',
            content,
        )
        combined = list(dict.fromkeys(dot_routes + [f"{v} {p}" for v, p in verb_routes]))
        return combined

    def _extract_connection_string_universal(self, content: str) -> str:
        patterns = [
            r'["\'](\w{2,}://[^\s"\']{5,})["\']',  # "scheme://..." in string literal
            r'(?:^|\s)(\w{2,}://[^\s"\'<>]{5,})',   # bare scheme://... (env var value, config)
        ]
        for pat in patterns:
            m = re.search(pat, content, re.MULTILINE)
            if m:
                return self.cred_filter.filter_text(m.group(1)[:200])
        return ""

    @staticmethod
    def _extract_embedded_sql_universal(content: str) -> list[str]:
        return re.findall(
            r'["\']([^"\']{10,}(?:SELECT|INSERT|UPDATE|DELETE)\s+[^"\']{5,})["\']',
            content, re.I,
        )

    @staticmethod
    def _extract_sql_params(content: str) -> list[ComponentParam]:
        params: list[ComponentParam] = []

        # Statement type
        stmt_types = re.findall(
            r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE\s+\w+|DROP\s+\w+|ALTER\s+\w+|MERGE|CALL|EXEC(?:UTE)?)\b',
            content, re.I,
        )
        if stmt_types:
            unique_types = list(dict.fromkeys(t.upper().split()[0] for t in stmt_types[:10]))
            params.append(ComponentParam(name="statement_type", value=", ".join(unique_types), field_type="TEXT"))

        # Procedure/function name
        proc = re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+(\w+)', content, re.I)
        if proc:
            params.append(ComponentParam(name="procedure_name", value=proc.group(1), field_type="TEXT"))

        # Table names from FROM / JOIN / INTO
        tables = re.findall(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:\[?[\w\.]+\]?)', content, re.I)
        table_names = [re.sub(r'[\[\]]', '', t.split()[-1]) for t in tables]
        unique_tables = list(dict.fromkeys(table_names[:20]))
        if unique_tables:
            params.append(ComponentParam(name="tables", value=", ".join(unique_tables), field_type="TEXT"))

        return params

    # ── SQL Server DDL extraction ───────────────────────────────────────────────

    def _extract_schema_definitions(self, files: list, repo_root: Path) -> list[dict]:
        definitions: list[dict] = []
        by_key: dict[tuple[str, str], dict] = {}

        for f in files:
            if getattr(f, "file_type", None) != FileType.SQL_PATTERN:
                continue
            path = Path(getattr(f, "path"))
            rel = getattr(f, "relative_path")
            content = self._read_schema_content(path)
            if not content:
                continue
            parsed = _parse_sql_server_schema(content, rel)
            for definition in parsed:
                key = (definition["schema"].lower(), definition["table"].lower())
                if key in by_key:
                    by_key[key]["duplicate_sources"].append(rel)
                    continue
                by_key[key] = definition
                definitions.append(definition)

        return definitions

    # ── Context parsing ───────────────────────────────────────────────────────

    def _parse_config_as_context(self, path: Path) -> ContextGroup | None:
        try:
            text = self.cred_filter.filter_text(
                path.read_text(encoding="utf-8", errors="replace"),
                filename=str(path),
            )
        except OSError:
            return None

        variables: list[ContextVariable] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            sep = "=" if "=" in line else (":" if ":" in line else None)
            if not sep:
                continue
            key, _, val = line.partition(sep)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                variables.append(ContextVariable(name=key, type="String", values={"_default": val}))

        if not variables:
            return None
        return ContextGroup(name=path.stem, variables=variables)

    # ── Routine parsing ───────────────────────────────────────────────────────

    def _parse_code_as_routine(self, path: Path, repo_root: Path) -> Routine | None:
        content = self._read_file_content(path)
        if content.startswith("["):
            return None
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.name
        return Routine(name=rel, source_code=content, is_system=False)

    # ── Connection detection ──────────────────────────────────────────────────

    def _detect_connection(self, path: Path, relative_path: str = "") -> tuple[list[MetadataConnection], list[ContextGroup]]:
        """Detect connection info from config files (JSON, YAML, XML)."""
        suffix = path.suffix.lower()
        if suffix in (".xml", ".config"):
            return self._detect_connection_xml(path, relative_path)
        if suffix not in (".json", ".yaml", ".yml"):
            return [], []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], []

        data: dict = {}
        if suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                self._warn(kind="parse_error", file=path, message=str(e), phase="connection")
                return [], []
        else:
            try:
                import yaml
                data = yaml.safe_load(text) or {}
            except Exception as e:
                self._warn(kind="parse_error", file=path, message=str(e), phase="connection")
                return [], []

        if not isinstance(data, dict):
            return [], []

        flat = self._flatten_dict(data)
        host = port = database = conn_type = ""

        for key, val in flat.items():
            kl = key.lower()
            vs = str(val)
            if ("host" in kl or "server" in kl) and not host:
                host = vs
            elif "port" in kl and not port:
                port = vs
            elif ("database" in kl or "dbname" in kl or "db" == kl.split(".")[-1]) and not database:
                database = vs
            elif ("driver" in kl or "dialect" in kl or "type" in kl) and not conn_type:
                conn_type = vs
            elif kl.endswith("url") and ("jdbc" in vs or "://" in vs):
                conn_type = "URL"
                host = vs

        if not (host or database or conn_type):
            return [], []

        return [MetadataConnection(
            xmi_id=path.stem,
            name=path.stem,
            connection_type=self.cred_filter.filter_text(conn_type or "unknown"),
            host=self.cred_filter.filter_text(host),
            port=port,
            database=self.cred_filter.filter_text(database),
            file_path=relative_path or path.name,
        )], []

    def _detect_connection_xml(self, path: Path, relative_path: str = "") -> tuple[list[MetadataConnection], list[ContextGroup]]:
        """Parse Web.config / app.config <connectionStrings> entries."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], []

        # Quick pre-check to avoid parsing unrelated XML
        if (
            "connectionString" not in text
            and "ConnectionString" not in text
            and "connectionStrings" not in text
            and "appSettings" not in text
        ):
            return [], []

        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return [], []

        connections: list[MetadataConnection] = []
        # Look for <connectionStrings><add name="..." connectionString="..."/>
        # and appSettings keys such as FooConnectionString.
        for elem in root.iter():
            cs = elem.get("connectionString") or elem.get("value", "")
            name = elem.get("name") or path.stem
            key = elem.get("key") or name
            if "connectionString" not in elem.attrib and not str(key).lower().endswith("connectionstring"):
                continue
            if cs and len(cs) > 5:
                cs_filtered = self.cred_filter.filter_text(cs)
                # Infer type from connection string
                conn_type = "unknown"
                type_source = f"{cs} {elem.get('providerName') or ''}".lower()
                for marker, ctype in [
                    ("sqlclient", "SQL Server"),
                    ("sqlserver", "SQL Server"), ("mssql", "SQL Server"),
                    ("postgresql", "PostgreSQL"), ("postgres", "PostgreSQL"),
                    ("mysql", "MySQL"), ("oracle", "Oracle"),
                    ("mongodb", "MongoDB"),
                ]:
                    if marker in type_source:
                        conn_type = ctype
                        break
                parts = self._parse_connection_string_parts(cs_filtered)
                connections.append(MetadataConnection(
                    xmi_id=name,
                    name=name,
                    connection_type=conn_type,
                    host=parts.get("host", cs_filtered[:200]),
                    port=parts.get("port", ""),
                    database=parts.get("database", ""),
                    schema_name=parts.get("schema_name", ""),
                    file_path=relative_path or path.name,
                    additional_params={
                        key: value for key, value in parts.items()
                        if key in {"user_id", "password"}
                    },
                ))
        return connections, self._detect_appsettings_xml(root, path)

    @staticmethod
    def _parse_connection_string_parts(connection_string: str) -> dict[str, str]:
        parts: dict[str, str] = {}
        for segment in connection_string.split(";"):
            if "=" not in segment:
                continue
            key, _, value = segment.partition("=")
            key_l = key.strip().lower()
            value = value.strip()
            if key_l in {"data source", "server", "host"}:
                host, _, port = value.partition(",")
                parts["host"] = host
                if port:
                    parts["port"] = port
            elif key_l in {"initial catalog", "database"}:
                parts["database"] = value
                parts["schema_name"] = value
            elif key_l in {"user id", "uid"}:
                parts["user_id"] = value
            elif key_l in {"password", "pwd"}:
                parts["password"] = "[REDACTED]"
        return parts

    def _detect_appsettings_xml(self, root: ElementTree.Element, path: Path) -> list[ContextGroup]:
        variables: list[ContextVariable] = []
        for parent in root.iter():
            if parent.tag.split("}")[-1] != "appSettings":
                continue
            for elem in parent:
                key = elem.get("key") or elem.get("name") or ""
                if not key:
                    continue
                value = elem.get("value", "")
                if self.cred_filter.is_sensitive_var_name(key) or _SENSITIVE_CONFIG_KEY_RE.search(key):
                    value = "[REDACTED]"
                else:
                    value = self.cred_filter.filter_text(value)
                variables.append(ContextVariable(name=key, values={"default": value}))
        if not variables:
            return []
        return [ContextGroup(name=f"{path.name}_appSettings", variables=variables)]

    @staticmethod
    def _apply_component_connection_refs(
        jobs: list[ParsedJob],
        connections: list[MetadataConnection],
    ) -> None:
        by_file: dict[str, list[str]] = {}
        for conn in connections:
            if conn.file_path:
                by_file.setdefault(conn.file_path, []).append(conn.name)

        for job in jobs:
            for comp in job.components:
                params = comp.compact_params()
                file_path = params.get("file_path", "")
                if file_path in by_file:
                    comp.metadata_connection_ref = ",".join(by_file[file_path])
                    continue

                conn_text = params.get("connection_string", "")
                if conn_text:
                    matches = [
                        conn.name for conn in connections
                        if conn.name.lower() in conn_text.lower()
                    ]
                    if not matches and len(connections) == 1:
                        matches = [connections[0].name]
                    if matches:
                        comp.metadata_connection_ref = ",".join(matches)

    @staticmethod
    def _flatten_dict(d: dict, prefix: str = "") -> dict[str, str]:
        items: dict[str, str] = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.update(GenericParser._flatten_dict(v, key))
            elif isinstance(v, (str, int, float, bool)):
                items[key] = str(v)
        return items

    # ── Manifest building ─────────────────────────────────────────────────────

    @staticmethod
    def _build_manifest(
        jobs: list[ParsedJob],
        connections: list[MetadataConnection],
    ) -> ProjectManifest:
        """Build manifest from job groups, imports, and detected connections."""
        job_groups: dict[str, str] = {j.name: j.group for j in jobs}

        # Index: lowercase stem → job name (for call-graph matching)
        stem_to_job: dict[str, str] = {j.name.lower(): j.name for j in jobs}
        # Also index by path stem (handles ./relative imports)
        for j in jobs:
            stem = Path(j.path).stem.lower()
            stem_to_job.setdefault(stem, j.name)

        job_calls: dict[str, list[str]] = {}
        job_called_by: dict[str, list[str]] = {}
        job_external_systems: dict[str, list[str]] = {}
        job_connections_used: dict[str, list[str]] = {}
        connection_used_by: dict[str, list[str]] = {}

        conn_names = {c.name for c in connections}

        for job in jobs:
            # Collect all imports/usings text from this job's components
            all_imports: list[str] = []
            all_conn_strings: list[str] = []

            for comp in job.components:
                for p in comp.params:
                    if p.name in ("imports", "usings") and p.value:
                        all_imports.append(str(p.value))
                    if p.name == "connection_string" and p.value:
                        all_conn_strings.append(str(p.value))

            imports_text = "\n".join(all_imports)

            # -- job_calls from relative imports --
            called: list[str] = []
            for line in imports_text.splitlines():
                # Python: from .module import x  OR  from project.module import x
                # TS: import {...} from './module'
                # C#: using Project.Namespace;
                stems = re.findall(
                    r'(?:from\s+\.?[\w/]+/)?(\w+)\s+import'  # Python
                    r"|from\s+['\"]\.{1,2}/(\w+)['\"]"       # TS relative
                    r"|\busing\s+\w+\.(\w+)\s*;",             # C#
                    line,
                )
                for tup in stems:
                    for candidate in tup:
                        if candidate and candidate.lower() in stem_to_job:
                            target = stem_to_job[candidate.lower()]
                            if target != job.name and target not in called:
                                called.append(target)
            if called:
                job_calls[job.name] = called
                for target in called:
                    job_called_by.setdefault(target, [])
                    if job.name not in job_called_by[target]:
                        job_called_by[target].append(job.name)

            # -- job_external_systems from imports --
            systems: list[str] = []
            for pkg_prefix, system in _IMPORT_TO_SYSTEM.items():
                if pkg_prefix.lower() in imports_text.lower():
                    if system not in systems:
                        systems.append(system)
            if systems:
                job_external_systems[job.name] = sorted(systems)

            # -- job_connections_used from connection_string params --
            used_conns: list[str] = []
            for cs in all_conn_strings:
                # Match against known metadata_connections by name
                for cname in conn_names:
                    if cname.lower() in cs.lower() and cname not in used_conns:
                        used_conns.append(cname)
                        connection_used_by.setdefault(cname, [])
                        if job.name not in connection_used_by[cname]:
                            connection_used_by[cname].append(job.name)
                # If no match but we have a real connection string, synthesise a name
                if not used_conns and cs and len(cs) > 5:
                    synth = "detected_connection"
                    if synth not in used_conns:
                        used_conns.append(synth)
            if used_conns:
                job_connections_used[job.name] = used_conns

        return ProjectManifest(
            job_groups=job_groups,
            job_calls=job_calls,
            job_called_by=job_called_by,
            job_external_systems=job_external_systems,
            job_connections_used=job_connections_used,
            connection_used_by=connection_used_by,
        )


_SQL_IDENTIFIER = r"(?:\[[^\]]+\]|[A-Za-z_][\w$#]*)"
_CREATE_TABLE_RE = re.compile(
    rf"\bCREATE\s+TABLE\s+(?P<name>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER})\s*\(",
    re.IGNORECASE,
)


def _parse_sql_server_schema(content: str, source_path: str) -> list[dict]:
    normalized = _strip_sql_comments(content)
    definitions: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}

    for match in _CREATE_TABLE_RE.finditer(normalized):
        open_paren = normalized.find("(", match.end() - 1)
        close_paren = _find_matching_paren(normalized, open_paren)
        if open_paren < 0 or close_paren < 0:
            continue

        schema, table = _parse_table_name(match.group("name"))
        body = normalized[open_paren + 1:close_paren]
        definition = {
            "dialect": "sqlserver",
            "object_type": "table",
            "schema": schema,
            "table": table,
            "name": f"{schema}.{table}",
            "source_path": source_path,
            "source_line": _line_no(normalized, match.start()),
            "columns": [],
            "constraints": [],
            "indexes": [],
            "default_constraints": [],
            "duplicate_sources": [],
        }
        for item in _split_top_level_commas(body):
            parsed = _parse_table_body_item(item)
            if parsed is None:
                continue
            if parsed["kind"] == "column":
                definition["columns"].append(parsed["value"])
            else:
                definition["constraints"].append(parsed["value"])
        definitions.append(definition)
        by_key[(schema.lower(), table.lower())] = definition

    _apply_alter_defaults(normalized, by_key)
    _apply_alter_primary_keys(normalized, by_key)
    _apply_alter_foreign_keys(normalized, by_key)
    _apply_create_indexes(normalized, by_key)
    return definitions


def _strip_sql_comments(content: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    return re.sub(r"--[^\n\r]*", "", without_block)


def _parse_table_name(raw: str) -> tuple[str, str]:
    parts = [_unquote_identifier(part) for part in re.findall(_SQL_IDENTIFIER, raw)]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if parts:
        return "dbo", parts[-1]
    return "dbo", raw.strip()


def _unquote_identifier(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1].replace("]]", "]")
    return value


def _find_matching_paren(text: str, open_index: int) -> int:
    if open_index < 0:
        return -1
    depth = 0
    in_string = False
    i = open_index
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_string and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _split_top_level_commas(body: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "'":
            if in_string and i + 1 < len(body) and body[i + 1] == "'":
                i += 2
                continue
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                item = body[start:i].strip()
                if item:
                    parts.append(item)
                start = i + 1
        i += 1
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_table_body_item(item: str) -> dict | None:
    stripped = " ".join(item.strip().split())
    if not stripped:
        return None
    if re.match(r"^(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b", stripped, re.IGNORECASE):
        return {"kind": "constraint", "value": _parse_table_constraint(stripped)}

    match = re.match(rf"^(?P<name>{_SQL_IDENTIFIER})\s+(?P<rest>.+)$", stripped, re.DOTALL)
    if not match:
        return None
    name = _unquote_identifier(match.group("name"))
    rest = match.group("rest").strip()
    computed_match = re.match(r"AS\s+(?P<expr>.+)$", rest, re.IGNORECASE)
    if computed_match:
        return {
            "kind": "column",
            "value": {
                "name": name,
                "data_type": "computed",
                "base_type": "computed",
                "max_length": None,
                "precision": None,
                "scale": None,
                "nullable": None,
                "default": None,
                "identity": None,
                "computed": True,
                "computed_expression": computed_match.group("expr").strip(),
                "collation": None,
                "raw_definition": stripped,
            },
        }

    type_info, tail = _parse_column_type(rest)
    nullable = None
    if re.search(r"\bNOT\s+NULL\b", tail, re.IGNORECASE):
        nullable = False
    elif re.search(r"\bNULL\b", tail, re.IGNORECASE):
        nullable = True

    return {
        "kind": "column",
        "value": {
            "name": name,
            **type_info,
            "nullable": nullable,
            "default": _parse_inline_default(tail),
            "identity": _parse_identity(tail),
            "computed": False,
            "computed_expression": None,
            "collation": _parse_collation(tail),
            "raw_definition": stripped,
        },
    }


def _parse_column_type(rest: str) -> tuple[dict, str]:
    match = re.match(
        rf"(?P<base>{_SQL_IDENTIFIER})(?P<args>\s*\([^)]*\))?(?P<tail>.*)$",
        rest,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {"data_type": rest.split()[0], "base_type": rest.split()[0].lower(), "max_length": None, "precision": None, "scale": None}, ""
    base = " ".join(str(_unquote_identifier(part)) for part in re.findall(_SQL_IDENTIFIER, match.group("base"))).lower()
    args = (match.group("args") or "").strip()
    tail = match.group("tail") or ""
    max_length = precision = scale = None
    if args:
        values = [v.strip() for v in args[1:-1].split(",")]
        if len(values) == 1:
            max_length = values[0]
        elif len(values) >= 2:
            precision = values[0]
            scale = values[1]
    return {
        "data_type": f"{base}{args}".strip(),
        "base_type": base,
        "max_length": max_length,
        "precision": precision,
        "scale": scale,
    }, tail


def _parse_identity(tail: str) -> dict | None:
    match = re.search(r"\bIDENTITY\s*\((?P<seed>[^,]+),(?P<increment>[^)]+)\)", tail, re.IGNORECASE)
    if not match:
        return None
    return {"seed": match.group("seed").strip(), "increment": match.group("increment").strip()}


def _parse_inline_default(tail: str) -> str | None:
    match = re.search(r"\bDEFAULT\s+(?P<value>\(.+?\)|'[^']*'|[^\s,]+)", tail, re.IGNORECASE)
    return match.group("value").strip() if match else None


def _parse_collation(tail: str) -> str | None:
    match = re.search(r"\bCOLLATE\s+([A-Za-z0-9_]+)", tail, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_table_constraint(text: str) -> dict:
    name = None
    name_match = re.match(rf"CONSTRAINT\s+(?P<name>{_SQL_IDENTIFIER})\s+(?P<body>.+)$", text, re.IGNORECASE)
    if name_match:
        name = _unquote_identifier(name_match.group("name"))
        body = name_match.group("body")
    else:
        body = text
    kind = "constraint"
    if re.search(r"\bPRIMARY\s+KEY\b", body, re.IGNORECASE):
        kind = "primary_key"
    elif re.search(r"\bFOREIGN\s+KEY\b", body, re.IGNORECASE):
        kind = "foreign_key"
    elif re.search(r"\bUNIQUE\b", body, re.IGNORECASE):
        kind = "unique"
    elif re.search(r"\bCHECK\b", body, re.IGNORECASE):
        kind = "check"
    return {"name": name, "kind": kind, "columns": _parse_column_list(body), "raw_definition": text}


def _parse_column_list(text: str) -> list[str]:
    match = re.search(r"\((?P<cols>[^)]*)\)", text, re.DOTALL)
    if not match:
        return []
    return _identifier_list_without_sort_tokens(match.group("cols"))


def _identifier_list_without_sort_tokens(text: str) -> list[str]:
    names = [str(_unquote_identifier(col)) for col in re.findall(_SQL_IDENTIFIER, text)]
    return [name for name in names if name.upper() not in {"ASC", "DESC"}]


def _apply_alter_defaults(content: str, by_key: dict[tuple[str, str], dict]) -> None:
    pattern = re.compile(
        rf"\bALTER\s+TABLE\s+(?P<table>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER})\s+ADD\s+"
        rf"(?:(?:CONSTRAINT\s+(?P<constraint>{_SQL_IDENTIFIER})\s+)?DEFAULT\s+(?P<value>.*?)\s+FOR\s+(?P<column>{_SQL_IDENTIFIER}))",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(content):
        definition = _definition_for_match(match.group("table"), by_key)
        if not definition:
            continue
        column = str(_unquote_identifier(match.group("column")))
        value = " ".join(match.group("value").split())
        entry = {
            "name": _unquote_identifier(match.group("constraint")) if match.group("constraint") else None,
            "column": column,
            "value": value,
            "source_line": _line_no(content, match.start()),
        }
        definition["default_constraints"].append(entry)
        for col in definition["columns"]:
            if col["name"].lower() == column.lower() and not col.get("default"):
                col["default"] = value
                break


def _apply_alter_primary_keys(content: str, by_key: dict[tuple[str, str], dict]) -> None:
    pattern = re.compile(
        rf"\bALTER\s+TABLE\s+(?P<table>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER}).*?"
        rf"ADD\s+CONSTRAINT\s+(?P<name>{_SQL_IDENTIFIER})\s+PRIMARY\s+KEY(?:\s+\w+)?\s*\((?P<cols>.*?)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(content):
        definition = _definition_for_match(match.group("table"), by_key)
        if not definition:
            continue
        definition["constraints"].append({
            "name": _unquote_identifier(match.group("name")),
            "kind": "primary_key",
            "columns": _identifier_list_without_sort_tokens(match.group("cols")),
            "source_line": _line_no(content, match.start()),
            "raw_definition": " ".join(match.group(0).split()),
        })


def _apply_alter_foreign_keys(content: str, by_key: dict[tuple[str, str], dict]) -> None:
    pattern = re.compile(
        rf"\bALTER\s+TABLE\s+(?P<table>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER}).*?"
        rf"ADD\s+CONSTRAINT\s+(?P<name>{_SQL_IDENTIFIER})\s+FOREIGN\s+KEY\s*\((?P<cols>.*?)\)\s+"
        rf"REFERENCES\s+(?P<ref_table>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER})\s*\((?P<ref_cols>.*?)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(content):
        definition = _definition_for_match(match.group("table"), by_key)
        if not definition:
            continue
        ref_schema, ref_table = _parse_table_name(match.group("ref_table"))
        definition["constraints"].append({
            "name": _unquote_identifier(match.group("name")),
            "kind": "foreign_key",
            "columns": _identifier_list_without_sort_tokens(match.group("cols")),
            "references": {
                "schema": ref_schema,
                "table": ref_table,
                "columns": _identifier_list_without_sort_tokens(match.group("ref_cols")),
            },
            "source_line": _line_no(content, match.start()),
            "raw_definition": " ".join(match.group(0).split()),
        })


def _apply_create_indexes(content: str, by_key: dict[tuple[str, str], dict]) -> None:
    pattern = re.compile(
        rf"\bCREATE\s+(?P<unique>UNIQUE\s+)?(?P<kind>CLUSTERED|NONCLUSTERED)?\s*INDEX\s+(?P<name>{_SQL_IDENTIFIER})\s+"
        rf"ON\s+(?P<table>(?:{_SQL_IDENTIFIER}\s*\.\s*)?{_SQL_IDENTIFIER})\s*\((?P<cols>.*?)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(content):
        definition = _definition_for_match(match.group("table"), by_key)
        if not definition:
            continue
        definition["indexes"].append({
            "name": _unquote_identifier(match.group("name")),
            "unique": bool(match.group("unique")),
            "kind": (match.group("kind") or "").upper() or None,
            "columns": _identifier_list_without_sort_tokens(match.group("cols")),
            "source_line": _line_no(content, match.start()),
            "raw_definition": " ".join(match.group(0).split()),
        })


def _definition_for_match(raw_table: str, by_key: dict[tuple[str, str], dict]) -> dict | None:
    schema, table = _parse_table_name(raw_table)
    return by_key.get((schema.lower(), table.lower()))


def _line_no(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1
