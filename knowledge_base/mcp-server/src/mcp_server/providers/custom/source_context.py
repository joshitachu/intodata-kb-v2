"""Direct redacted source packaging for custom documentation generation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.sql_usage import write_sql_usage_artifacts


SOURCE_CONTEXT_DIR = "_source_context"
SOURCE_COVERAGE_NAME = "_source_coverage.json"

_SECRET_VALUE_RE = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|license[_-]?key|authorization|x-api-key|x-auth-token)\b\s*[:=]\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^'\";\s,}>\n]+)(?P=quote)",
    re.IGNORECASE,
)
_CONNECTION_SECRET_RE = re.compile(
    r"(?P<prefix>\b(?:Password|Pwd|User\s+ID|UID)\s*=\s*)(?P<value>[^;\"'\n]+)",
    re.IGNORECASE,
)
_XML_SENSITIVE_ADD_RE = re.compile(
    r"(?P<prefix>\bkey\s*=\s*['\"](?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|license[_-]?key|authorization|x-api-key|credential|connectionstring|"
    r"clientsecret|client[_-]?secret)['\"][^>\n]*?\bvalue\s*=\s*)"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_XML_VALUE_THEN_SENSITIVE_KEY_RE = re.compile(
    r"(?P<prefix>\bvalue\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
    r"(?P<suffix>[^>\n]*?\bkey\s*=\s*['\"][^'\"]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|license[_-]?key|authorization|x-api-key|credential|"
    r"connectionstring|clientsecret|client[_-]?secret)[^'\"]*['\"])",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>(?:password|pwd|secret|api.?key|token|credential|connectionstring|clientsecret|"
    r"accesskey)\s*=\s*\")(?P<value>[^\"]*)(?P<suffix>\")",
    re.IGNORECASE,
)
_SENSITIVE_NAME_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
    r"license[_-]?key|authorization|x-api-key|credential|connectionstring|clientsecret|"
    r"client[_-]?secret)",
    re.IGNORECASE,
)
# Simple substitution patterns — applied in sequence in _redact()
_SIMPLE_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # HTTP auth headers (Authorization first to avoid double-sub with Bearer)
    (re.compile(r"(?i)\bAuthorization\s*[:=]\s*\S+\s+[A-Za-z0-9._+/=\-]{10,}"), "[AUTH_HEADER_REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._+/=\-]{10,}"), "[BEARER_TOKEN_REDACTED]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{10,}"), "[BASIC_AUTH_REDACTED]"),
    (re.compile(r"(?i)\b(?:X-API-Key|X-Auth-Token|Api-Key|X-Access-Token|X-Token|X-Client-Secret)\s*[:=]\s*[A-Za-z0-9._+/=\-]{10,}"), "[API_KEY_REDACTED]"),
    # JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[JWT_REDACTED]"),
    # Source-control tokens
    (re.compile(r"\bgh[phosrux]_[A-Za-z0-9_]{10,}\b|\bgithub_pat_[A-Za-z0-9_]{10,}\b"), "[GITHUB_TOKEN_REDACTED]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}\b|\bglptt-[A-Za-z0-9_\-]{10,}\b"), "[GITLAB_TOKEN_REDACTED]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "[NPM_TOKEN_REDACTED]"),
    (re.compile(r"\bpypi-[A-Za-z0-9_\-]{40,}\b"), "[PYPI_TOKEN_REDACTED]"),
    # Cloud credentials
    (re.compile(r"\b(?:AKIA|ASIA|AROA|ANPA|ANVA|AIDA)[0-9A-Z]{16}\b"), "[AWS_ACCESS_KEY_REDACTED]"),
    (re.compile(r"(?i)\baws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*[\"']?[A-Za-z0-9+/]{40}[\"']?"), "[AWS_SECRET_KEY_REDACTED]"),
    (re.compile(r"\bAIzaSy[A-Za-z0-9_\-]{33}\b"), "[GCP_API_KEY_REDACTED]"),
    (re.compile(r"(?i)\bAccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}"), "[AZURE_ACCOUNT_KEY_REDACTED]"),
    (re.compile(r"\bdapi[a-f0-9]{32}\b"), "[DATABRICKS_TOKEN_REDACTED]"),
    # LLM provider keys
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b"), "[LLM_API_KEY_REDACTED]"),
    # SaaS tokens
    (re.compile(r"\bxox[bpaos]-[0-9A-Za-z\-]{10,}\b"), "[SLACK_TOKEN_REDACTED]"),
    (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"), "[STRIPE_KEY_REDACTED]"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "[SENDGRID_KEY_REDACTED]"),
    (re.compile(r"\bkey-[a-f0-9]{32}\b"), "[MAILGUN_KEY_REDACTED]"),
]
_NOISE_PATH_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".mypy_cache", "coverage", "bin", "obj", "target",
}
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".exe", ".dll", ".jar", ".class", ".zip", ".gz",
}
_STATIC_RESOURCE_EXTS = {
    ".dat", ".bin", ".bmp", ".svg", ".pdf", ".psd", ".ai", ".mp3", ".mp4",
    ".mov", ".avi", ".wav", ".ttf", ".otf", ".woff", ".woff2", ".eot",
}
_HIGH_PRIORITY_EXTS = {
    ".config", ".xml", ".json", ".yaml", ".yml", ".toml", ".env",
    ".properties", ".ini", ".cfg", ".conf", ".sql", ".ddl", ".dml",
}
_GENERATED_SOURCE_NAMES = {
    "assemblyinfo.cs",
}
_GENERATED_SOURCE_SUFFIXES = (
    ".designer.cs",
    ".g.cs",
    ".g.i.cs",
    ".generated.cs",
)
_LOW_RELEVANCE_VENDOR_PARTS = {
    "barcode",
    "resources",
    "vendor",
    "thirdparty",
    "third_party",
}
_RELEVANCE_TERMS_RE = re.compile(
    r"\b("
    r"select|insert|update|delete|merge|from|join|where|sql|ddl|"
    r"dbcontext|dbconnection|connectionstring|opendbconnection|ormlite|dapper|"
    r"entity|table|repository|controller|service|endpoint|route|httpclient|"
    r"appsettings|configurationmanager|getconnectionstring"
    r")\b",
    re.IGNORECASE,
)


def _write_schema_dictionary(scan_output_dir: Path, staging_dir: Path) -> None:
    """Write schema_dictionary.md from scan_result.schema_definitions for all platforms.

    schema_definitions contains fully-parsed CREATE TABLE output with column names, types,
    nullability, constraints, and indexes. Without this file the evidence bundle has no
    structured column data — large DDL files get truncated to 4000 chars per fenced block,
    silently dropping all but the first 1-2 tables.
    """
    scan_result = _load_scan_result(scan_output_dir)
    schema_defs: list[dict[str, Any]] = scan_result.get("schema_definitions", []) or []
    if not schema_defs:
        return

    lines = ["# Schema Dictionary", "", "Structured table definitions extracted from DDL by the scanner.", ""]
    for table in schema_defs:
        db_schema = table.get("schema", "dbo")
        name = table.get("table") or table.get("name", "?")
        obj_type = table.get("object_type", "table")
        source = table.get("source_path", "")
        qualified = f"[{db_schema}].[{name}]" if db_schema else f"[{name}]"
        lines.append(f"## {qualified}")
        lines.append(f"Type: {obj_type}  Source: {source}")
        lines.append("")

        columns: list[dict[str, Any]] = table.get("columns", []) or []
        if columns:
            lines.append("| Column | Type | MaxLen | Precision | Scale | Nullable | Identity | PK | Computed |")
            lines.append("|--------|------|--------|-----------|-------|----------|----------|----|----------|")
            for col in columns:
                col_name = col.get("name", "")
                col_type = col.get("data_type", "")
                max_len = col.get("max_length", "")
                precision = col.get("precision", "")
                scale = col.get("scale", "")
                nullable_value = col.get("nullable")
                nullable = "UNKNOWN" if nullable_value is None else ("YES" if nullable_value else "NO")
                identity = _format_schema_identity(col.get("identity"))
                is_pk = "YES" if _column_is_primary_key(col_name, table.get("constraints", []) or []) else ""
                computed = col.get("computed_expression", "")
                lines.append(
                    f"| {col_name} | {col_type} | {max_len} | {precision} | {scale}"
                    f" | {nullable} | {identity} | {is_pk} | {computed} |"
                )
            lines.append("")

        constraints: list[dict[str, Any]] = table.get("constraints", []) or []
        if constraints:
            lines.append("Constraints:")
            for c in constraints:
                ctype = c.get("constraint_type") or c.get("kind", "")
                cname = c.get("constraint_name") or c.get("name", "")
                cols_str = ", ".join(c.get("columns", []) or [])
                references = c.get("references", {}) or {}
                ref = c.get("referenced_table") or (
                    f"{references.get('schema')}.{references.get('table')}"
                    if references.get("table")
                    else ""
                )
                ref_cols = ", ".join(c.get("referenced_columns") or references.get("columns", []) or [])
                if ref:
                    lines.append(f"- [{ctype}] {cname}: ({cols_str}) -> {ref}({ref_cols})")
                else:
                    lines.append(f"- [{ctype}] {cname}: ({cols_str})")
            lines.append("")

        indexes: list[dict[str, Any]] = table.get("indexes", []) or []
        if indexes:
            lines.append("Indexes:")
            for idx in indexes:
                iname = idx.get("index_name") or idx.get("name", "")
                unique = "UNIQUE " if (idx.get("is_unique") or idx.get("unique")) else ""
                icols = ", ".join(idx.get("columns", []) or [])
                lines.append(f"- {unique}{iname}: ({icols})")
            lines.append("")

    out_path = staging_dir / "schema_dictionary.md"
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _format_schema_identity(identity: Any) -> str:
    if isinstance(identity, dict):
        seed = identity.get("seed", "")
        increment = identity.get("increment", "")
        return f"YES ({seed},{increment})" if seed or increment else "YES"
    return "YES" if identity else ""


def _column_is_primary_key(column_name: str, constraints: list[dict[str, Any]]) -> bool:
    for constraint in constraints:
        kind = str(constraint.get("kind") or constraint.get("constraint_type") or "").lower()
        if kind != "primary_key":
            continue
        columns = [str(col).lower() for col in constraint.get("columns", []) or []]
        if column_name.lower() in columns:
            return True
    return False


def materialize_direct_redacted_source(
    *,
    scan_output_dir: Path,
    preprocess_dir: Path,
    inline_max_bytes: int = 120_000,
    chunk_bytes: int = 80_000,
) -> tuple[list[str], dict[str, Any]]:
    """Write direct redacted source bundles and coverage metadata.

    The function packages already-scanned source content. It does not summarize
    or interpret source semantics; it only redacts obvious sensitive values,
    preserves file boundaries, and records coverage.
    """

    preprocess_dir.mkdir(parents=True, exist_ok=True)
    source_dir = preprocess_dir / SOURCE_CONTEXT_DIR
    files_dir = source_dir / "files"
    chunks_dir = source_dir / "chunks"
    files_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    sources_by_group = _collect_scanned_sources(scan_output_dir)
    coverage: dict[str, Any] = {
        "mode": "direct_redacted",
        "scan_output_dir": str(scan_output_dir),
        "groups": {},
        "files": [],
        "summary": {
            "total_files": 0,
            "included_full": 0,
            "included_chunked": 0,
            "summarized_low_relevance": 0,
            "retrievable": 0,
            "skipped_noise": 0,
            "skipped_binary": 0,
            "skipped_static_resource": 0,
            "skipped_generated": 0,
            "skipped_too_large": 0,
            "redactions": 0,
        },
    }

    processed_groups: list[str] = []
    for group, items in sorted(sources_by_group.items()):
        group_items = sorted(items, key=lambda item: (_priority(item["path"]), item["path"]))
        lines = [
            f"# Redacted Source Evidence: {group}",
            "",
            "This bundle contains redacted source text. File content is the evidence of record.",
            "Values marked `[REDACTED]` were intentionally hidden before model exposure.",
            "",
        ]
        group_paths: list[str] = []
        for item in group_items:
            path = item["path"]
            content = str(item.get("content") or "")
            skipped = _skip_reason(path, content)
            if skipped:
                record = _coverage_record(path, group, skipped, content, redactions=0)
                _add_coverage(coverage, record)
                continue

            redacted, redactions = _redact(content)
            summary = _low_relevance_summary(path, redacted)
            if summary is not None:
                status = "summarized_low_relevance"
                lines.extend(_file_block(path, summary))
                record = _coverage_record(
                    path,
                    group,
                    status,
                    summary,
                    redactions=redactions,
                    original_size_bytes=len(redacted.encode("utf-8")),
                )
                _add_coverage(coverage, record)
                group_paths.append(path)
                continue

            retrievable_path = _write_retrievable_file(files_dir, path, redacted)
            encoded = redacted.encode("utf-8")
            if len(encoded) > inline_max_bytes:
                chunk_paths = _write_chunks(chunks_dir, path, redacted, chunk_bytes)
                status = "included_chunked"
                lines.extend(_chunk_references(path, chunk_paths))
                record = _coverage_record(
                    path,
                    group,
                    status,
                    redacted,
                    redactions=redactions,
                    retrievable_path=retrievable_path,
                    chunk_paths=chunk_paths,
                )
            else:
                status = "included_full"
                lines.extend(_file_block(path, redacted))
                record = _coverage_record(
                    path,
                    group,
                    status,
                    redacted,
                    redactions=redactions,
                    retrievable_path=retrievable_path,
                )
            _add_coverage(coverage, record)
            group_paths.append(path)

        coverage["groups"][group] = group_paths
        (preprocess_dir / f"{group}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        processed_groups.append(group)

    coverage_path = source_dir / "coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    (preprocess_dir / SOURCE_COVERAGE_NAME).write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    _write_schema_dictionary(scan_output_dir, preprocess_dir.parent)
    write_sql_usage_artifacts(scan_output_dir, preprocess_dir.parent)
    return processed_groups, coverage


def _collect_scanned_sources(scan_output_dir: Path) -> dict[str, list[dict[str, str]]]:
    scan_result = _load_scan_result(scan_output_dir)
    platform = str(scan_result.get("platform") or "").lower()
    if platform == "talend":
        return _collect_talend_sources(scan_result)
    if platform == "workato":
        return _collect_workato_sources(scan_result)
    if platform == "lobster":
        return _collect_structured_sources(scan_result)

    jobs = list(scan_result.get("jobs", []) or []) if scan_result else _load_jobs(scan_output_dir)
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        group = str(job.get("group") or "Global")
        job_path = str(job.get("path") or job.get("name") or "unknown")
        for component in job.get("components", []) or []:
            params = component.get("params", []) or []
            content = _param_value(params, "content")
            if content is None:
                continue
            path = _param_value(params, "file_path") or job_path
            key = (group, path)
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(group, []).append({"path": path, "content": content})
    return grouped


def _load_scan_result(scan_output_dir: Path) -> dict[str, Any]:
    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        return json.loads(mono.read_text(encoding="utf-8"))
    return {}


def _collect_talend_sources(scan_result: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    contexts = scan_result.get("context_groups", []) or []
    routines = scan_result.get("routines", []) or []
    metadata_connections = scan_result.get("metadata_connections", []) or []
    for job in scan_result.get("jobs", []) or []:
        group = str(job.get("group") or "Global")
        path = str(job.get("path") or job.get("name") or "unknown")
        content = _talend_job_text(job, contexts, routines, metadata_connections)
        if content.strip():
            grouped.setdefault(group, []).append({"path": path, "content": content})
    return grouped


def _talend_job_text(
    job: dict[str, Any],
    contexts: list[dict[str, Any]],
    routines: list[dict[str, Any]],
    metadata_connections: list[dict[str, Any]],
) -> str:
    lines = [f"# Talend Job Evidence: {job.get('name') or job.get('path') or 'unknown'}", ""]
    components = job.get("components", []) or []
    if components:
        lines.extend(["## Component Inventory"])
        for component in components:
            ctype = str(component.get("component_type") or "")
            unique_name = str(component.get("unique_name") or "")
            lines.append(f"- {ctype} {unique_name}".strip())
        lines.append("")

    evidence_param_names = {
        "QUERY", "QUERY_STORE_FROM", "TABLE_NAME", "SCHEMA_NAME", "CODE", "EXPRESSION",
        "METHOD", "HTTP_METHOD", "VERB", "HTTP_VERB",
    }
    for component in components:
        ctype = str(component.get("component_type") or "")
        unique_name = str(component.get("unique_name") or "")
        params = component.get("params", []) or []
        for param in params:
            name = str(param.get("name") or "")
            value = str(param.get("value") or "")
            if not value:
                continue
            if name in evidence_param_names or (name == "URL" and ctype.startswith("tREST")):
                lines.extend([f"## {ctype} {unique_name} {name}", value, ""])

    tmap_lines: list[str] = []
    for tmap in job.get("tmap_data", []) or []:
        for entry in list(tmap.get("entries", []) or []) + list(tmap.get("var_entries", []) or []):
            expression = str(entry.get("expression") or "")
            if expression:
                tmap_lines.append(expression)
    if tmap_lines:
        lines.extend(["## tMap Expressions", *tmap_lines, ""])

    context_lines: list[str] = []
    for context in contexts:
        for variable in context.get("variables", []) or []:
            name = str(variable.get("name") or "")
            if not name or _SENSITIVE_NAME_RE.search(name):
                continue
            values = variable.get("values", {}) or {}
            for env_name, value in values.items():
                value_s = str(value or "")
                if not value_s or str(env_name).lower() in {"prod", "production"}:
                    continue
                if _SENSITIVE_NAME_RE.search(value_s):
                    continue
                context_lines.append(f"{context.get('name')}.{name}[{env_name}]={value_s}")
    if context_lines:
        lines.extend(["## Context Variables", *context_lines[:500], ""])

    routine_lines: list[str] = []
    for routine in routines:
        if routine.get("is_system"):
            continue
        source = str(routine.get("source_code") or "")
        if source:
            routine_lines.extend([f"### Routine: {routine.get('name')}", source, ""])
    if routine_lines:
        lines.extend(["## Routine Source Code", *routine_lines])

    conn_lines: list[str] = []
    for conn in metadata_connections:
        parts = [
            str(conn.get("name") or ""),
            str(conn.get("connection_type") or ""),
            str(conn.get("host") or ""),
            str(conn.get("schema_name") or ""),
        ]
        conn_lines.append(" | ".join(part for part in parts if part))
    if conn_lines:
        lines.extend(["## Metadata Connections", *conn_lines, ""])

    refs = [str(ref.get("child_job") or "") for ref in job.get("trunjob_refs", []) or [] if ref.get("child_job")]
    if refs:
        lines.extend(["## Child Job References", *refs, ""])

    return "\n".join(lines).strip() + "\n"


def _collect_workato_sources(scan_result: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped = _collect_structured_sources(scan_result)
    manifest = scan_result.get("manifest", {}) or {}
    job_connections = manifest.get("job_connections_used", {}) or {}
    metadata_connections = {
        str(conn.get("name") or ""): conn
        for conn in scan_result.get("metadata_connections", []) or []
    }
    schema_definitions = scan_result.get("schema_definitions", []) or []
    for group_items in grouped.values():
        for item in group_items:
            job_name = item.get("job_name") or item["path"].rsplit("/", 1)[-1]
            item["content"] += _workato_job_evidence(item["content"], job_connections.get(job_name, []), metadata_connections, schema_definitions)
    return grouped


def _workato_job_evidence(
    content: str,
    connection_names: list[str],
    metadata_connections: dict[str, dict[str, Any]],
    schema_definitions: list[dict[str, Any]],
) -> str:
    try:
        marker = "steps = "
        steps_line = next(line for line in content.splitlines() if line.startswith(marker))
        steps = json.loads(steps_line[len(marker):])
    except (StopIteration, json.JSONDecodeError):
        steps = []
    try:
        marker = "trigger = "
        trigger_line = next(line for line in content.splitlines() if line.startswith(marker))
        trigger = json.loads(trigger_line[len(marker):])
    except (StopIteration, json.JSONDecodeError):
        trigger = {}

    lines: list[str] = []
    flat_steps = _flatten_workato_steps(trigger) + _flatten_workato_steps(steps)
    if flat_steps:
        lines.extend(["", "## Step Evidence"])
        for idx, step in enumerate(flat_steps, start=1):
            provider = step.get("provider") or step.get("application") or step.get("app") or ""
            action = step.get("action") or step.get("name") or step.get("type") or ""
            conn_id = step.get("connection_id") or step.get("account_id") or ""
            lines.append(f"{idx}: {provider}.{action} connection_id={conn_id}".strip())

        sql_lines: list[str] = []
        endpoint_lines: list[str] = []
        for step in flat_steps:
            _collect_workato_key_values(step, {"query", "sql", "statement", "select", "where_clause", "filter"}, sql_lines)
            endpoint_bits = []
            for key in ("type", "http_method", "path", "webhook_url", "auth_type"):
                value = step.get(key)
                if value:
                    endpoint_bits.append(f"{key}={value}")
            if endpoint_bits:
                endpoint_lines.append(" ".join(endpoint_bits))
        if sql_lines:
            lines.extend(["", "## SQL Evidence", *sql_lines])
        if endpoint_lines:
            lines.extend(["", "## Endpoints", *endpoint_lines])

    if connection_names:
        lines.extend(["", "## Connections"])
        for name in connection_names:
            conn = metadata_connections.get(name, {})
            auth = (conn.get("additional_params") or {}).get("authorization_type", "")
            lines.append(f"{name}: {conn.get('connection_type', '')} auth={auth}".strip())
    elif flat_steps:
        unresolved = sorted({
            str(step.get("account_id") or "")
            for step in flat_steps
            if step.get("account_id")
        })
        if unresolved:
            lines.extend(["", "## Connections", "Unresolved account IDs: " + ", ".join(unresolved)])

    if schema_definitions:
        lines.extend(["", "## Schema Definitions"])
        for schema in schema_definitions:
            cols = schema.get("columns") or schema.get("fields") or []
            if isinstance(cols, list):
                col_text = ", ".join(str(c.get("name", c)) if isinstance(c, dict) else str(c) for c in cols)
            else:
                col_text = str(cols)
            lines.append(f"{schema.get('name') or schema.get('id')}: {col_text}")
    return "\n".join(lines) + ("\n" if lines else "")


def _flatten_workato_steps(obj: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(key in obj for key in ("provider", "application", "action", "connection_id", "account_id")):
            steps.append(obj)
        for value in obj.values():
            steps.extend(_flatten_workato_steps(value))
    elif isinstance(obj, list):
        for item in obj:
            steps.extend(_flatten_workato_steps(item))
    return steps


def _collect_workato_key_values(obj: Any, wanted: set[str], lines: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in wanted and value not in (None, ""):
                lines.append(f"{key}: {value}")
            _collect_workato_key_values(value, wanted, lines)
    elif isinstance(obj, list):
        for item in obj:
            _collect_workato_key_values(item, wanted, lines)


def _collect_structured_sources(scan_result: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for job in scan_result.get("jobs", []) or []:
        group = str(job.get("group") or "Global")
        path = str(job.get("path") or job.get("name") or "unknown")
        lines = []
        if job.get("active") is False:
            lines.extend(["> **INACTIVE PROFILE** - this integration is disabled and should not be treated as production-active", ""])
        lines.append(f"# Job Evidence: {job.get('name') or path}")
        for component in job.get("components", []) or []:
            lines.append(f"## {component.get('component_type')} {component.get('unique_name')}")
            if component.get("metadata_connection_ref"):
                lines.append(f"metadata_connection_ref = {component.get('metadata_connection_ref')}")
            for param in component.get("params", []) or []:
                name = str(param.get("name") or "")
                value = str(param.get("value") or "")
                if value:
                    lines.append(f"{name} = {value}")
        grouped.setdefault(group, []).append({"path": path, "job_name": str(job.get("name") or path), "content": "\n".join(lines) + "\n"})
    return grouped


def _load_jobs(scan_output_dir: Path) -> list[dict[str, Any]]:
    scan_dir = scan_output_dir / "scan"
    jobs: list[dict[str, Any]] = []
    if (scan_dir / "jobs").exists():
        for group_dir in sorted(path for path in (scan_dir / "jobs").iterdir() if path.is_dir()):
            for path in sorted(group_dir.glob("*.json")):
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
        return jobs

    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        data = json.loads(mono.read_text(encoding="utf-8"))
        return list(data.get("jobs", []) or [])
    return []


def _param_value(params: list[dict[str, Any]], name: str) -> str | None:
    for param in params:
        if str(param.get("name") or "").lower() == name:
            return str(param.get("value") or "")
    return None


def _skip_reason(path: str, content: str) -> str | None:
    parts = set(Path(path).parts)
    suffix = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if parts & _NOISE_PATH_PARTS:
        return "skipped_noise"
    if suffix in _BINARY_EXTS:
        return "skipped_binary"
    if suffix in _STATIC_RESOURCE_EXTS:
        return "skipped_static_resource"
    if name in _GENERATED_SOURCE_NAMES or name.endswith(_GENERATED_SOURCE_SUFFIXES):
        return "skipped_generated"
    if suffix == ".cd":
        return "skipped_generated"
    if content.startswith("[FILE TOO LARGE"):
        return "skipped_too_large"
    if content.startswith("[BINARY"):
        return "skipped_binary"
    return None


def classify_source_path(path: str, content_hint: str | None = None) -> str | None:
    """Classify a source path using the same skip labels as source packaging.

    Returns one of the ``skipped_*`` labels used in ``_source_coverage.json`` or
    ``None`` when the file is in scope for source packaging.
    """
    return _skip_reason(path, content_hint or "")


def _low_relevance_summary(path: str, content: str) -> str | None:
    """Summarize likely vendored/resource code without dropping normal source files."""

    suffix = Path(path).suffix.lower()
    if suffix not in {".cs", ".java", ".js", ".ts", ".py", ".go", ".rb", ".php"}:
        return None
    parts = {part.lower() for part in Path(path).parts}
    if not (parts & _LOW_RELEVANCE_VENDOR_PARTS):
        return None
    if _RELEVANCE_TERMS_RE.search(content):
        return None

    classes = re.findall(r"\b(?:class|interface|enum|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", content)
    methods = re.findall(
        r"\b(?:public|private|protected|internal|static|virtual|override|abstract|async|\s)+"
        r"[A-Za-z0-9_<>,\[\]?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        content,
    )
    lines = [
        f"Summarized low-relevance source file: {path}",
        "Reason: file appears to be vendored/resource-library code and contains no DB, SQL, config, API, or domain integration terms.",
        f"Original bytes: {len(content.encode('utf-8'))}",
    ]
    if classes:
        lines.append("Types: " + ", ".join(sorted(set(classes))[:20]))
    if methods:
        lines.append("Methods: " + ", ".join(sorted(set(methods))[:30]))
    lines.append("Full file content omitted from LLM context by source packaging filter.")
    return "\n".join(lines)


def _redact(content: str) -> tuple[str, int]:
    count = 0

    def replace_secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"

    def replace_connection_secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group('prefix')}[REDACTED]"

    def replace_reordered_xml_secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return (
            f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"
            f"{match.group('suffix')}"
        )

    def replace_sensitive_assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group('prefix')}[REDACTED]{match.group('suffix')}"

    redacted = _SECRET_VALUE_RE.sub(replace_secret, content)
    redacted = _CONNECTION_SECRET_RE.sub(replace_connection_secret, redacted)
    redacted = _XML_SENSITIVE_ADD_RE.sub(replace_secret, redacted)
    redacted = _XML_VALUE_THEN_SENSITIVE_KEY_RE.sub(replace_reordered_xml_secret, redacted)
    redacted = _SENSITIVE_ASSIGNMENT_RE.sub(replace_sensitive_assignment, redacted)

    for pattern, placeholder in _SIMPLE_REDACT_PATTERNS:
        def _make_sub(ph: str = placeholder) -> re.Match[str]:
            def _sub(m: re.Match[str]) -> str:
                nonlocal count
                count += 1
                return ph
            return _sub
        redacted = pattern.sub(_make_sub(), redacted)

    return redacted, count


def _write_retrievable_file(files_dir: Path, source_path: str, content: str) -> str:
    target = files_dir / _safe_source_name(source_path)
    target.write_text(_file_text(source_path, content), encoding="utf-8")
    return target.relative_to(files_dir.parent.parent).as_posix()


def _write_chunks(chunks_dir: Path, source_path: str, content: str, chunk_bytes: int) -> list[str]:
    chunk_paths: list[str] = []
    start = 0
    idx = 1
    while start < len(content):
        end = min(len(content), start + chunk_bytes)
        if end < len(content):
            newline = content.rfind("\n", start, end)
            if newline > start:
                end = newline
        body = content[start:end]
        target = chunks_dir / f"{Path(_safe_source_name(source_path)).stem}_chunk_{idx}.md"
        target.write_text(_file_text(source_path, body, chunk=idx), encoding="utf-8")
        chunk_paths.append(target.relative_to(chunks_dir.parent.parent).as_posix())
        if end >= len(content):
            break
        start = end + 1
        idx += 1
    return chunk_paths


def _file_block(path: str, content: str) -> list[str]:
    return [
        f"---\n## File: {path}",
        "",
        "```text",
        content.rstrip(),
        "```",
        "",
    ]


def _chunk_references(path: str, chunk_paths: list[str]) -> list[str]:
    lines = [
        f"---\n## File: {path}",
        "",
        "File is available in redacted chunks:",
    ]
    lines.extend(f"- `{chunk}`" for chunk in chunk_paths)
    lines.append("")
    return lines


def _file_text(path: str, content: str, chunk: int | None = None) -> str:
    title = f"# Source file: {path}" if chunk is None else f"# Source file: {path} (chunk {chunk})"
    return "\n".join([title, "", "```text", content.rstrip(), "```", ""])


def _coverage_record(
    path: str,
    group: str,
    status: str,
    content: str,
    *,
    redactions: int,
    retrievable_path: str | None = None,
    chunk_paths: list[str] | None = None,
    original_size_bytes: int | None = None,
) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    record = {
        "path": path,
        "group": group,
        "status": status,
        "size_bytes": len(encoded),
        "token_estimate": max(1, len(encoded) // 4) if encoded else 0,
        "sha256": hashlib.sha256(encoded).hexdigest() if encoded else "",
        "redactions": redactions,
    }
    if original_size_bytes is not None:
        record["original_size_bytes"] = original_size_bytes
    if retrievable_path:
        record["retrievable_path"] = retrievable_path
    if chunk_paths:
        record["chunk_paths"] = chunk_paths
    return record


def _add_coverage(coverage: dict[str, Any], record: dict[str, Any]) -> None:
    coverage["files"].append(record)
    summary = coverage["summary"]
    summary["total_files"] += 1
    status = record["status"]
    if status in summary:
        summary[status] += 1
    if record.get("retrievable_path") or record.get("chunk_paths"):
        summary["retrievable"] += 1
    summary["redactions"] += int(record.get("redactions") or 0)


def _priority(path: str) -> tuple[int, str]:
    lower = path.lower()
    suffix = Path(path).suffix.lower()
    if suffix in _HIGH_PRIORITY_EXTS or "config" in lower or "settings" in lower:
        return (0, lower)
    if Path(lower).name in {"program.cs", "startup.cs", "global.asax.cs", "app.py", "main.py", "server.js", "app.js"}:
        return (1, lower)
    if suffix in {".cs", ".py", ".java", ".js", ".ts", ".go", ".rb", ".php"}:
        return (2, lower)
    if suffix in {".md", ".rst", ".txt"}:
        return (4, lower)
    return (3, lower)


def _safe_source_name(path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "__", path).strip("._") or "source"
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:120]}__{digest}.md"
