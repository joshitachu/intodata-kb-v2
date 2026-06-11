"""Deterministic SQL usage extraction for scanned source artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_SQL_KEYWORD_RE = re.compile(r"\b(select|insert|update|delete|merge)\b", re.IGNORECASE)
_SQL_STATEMENT_RE = re.compile(
    r"\b("
    r"select\b[\s\S]{0,1200}?\bfrom\b|"
    r"insert\s+into\b|"
    r"update\s+(?:\[[^\]]+\]|\w+)(?:\s*\.\s*(?:\[[^\]]+\]|\w+)){0,2}\s+set\b|"
    r"delete\s+from\b|"
    r"merge\s+into\b"
    r")",
    re.IGNORECASE,
)
_DDL_HEAVY_RE = re.compile(r"\bcreate\s+table\b|\balter\s+table\b|\bprimary\s+key\b|\bforeign\s+key\b", re.IGNORECASE)
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join|into|update|merge\s+into)\s+"
    r"(?P<table>(?:\[[^\]]+\]|\w+)(?:\s*\.\s*(?:\[[^\]]+\]|\w+)){0,2})"
    r"(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)
_ALIAS_FIELD_RE = re.compile(
    r"\b(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(
    r"\b(?P<section>select|on|where|group\s+by|order\s+by)\b(?P<body>.*?)(?=\bselect\b|\bfrom\b|\bjoin\b|\bon\b|\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bunion\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_SELECT_FROM_RE = re.compile(
    r"\bselect\b(?P<select>.*?)\bfrom\s+"
    r"(?P<table>(?:\[[^\]]+\]|\w+)(?:\s*\.\s*(?:\[[^\]]+\]|\w+)){0,2})"
    r"(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE | re.DOTALL,
)
_UNQUALIFIED_COL_RE = re.compile(r"(?:^|[,\s])(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))(?:\s+as\b|\s*,|$)", re.IGNORECASE)
_STRING_NOISE_RE = re.compile(r"^\d+$|^(as|null|isnull|case|when|then|else|end|and|or|not|in|on|from|select|where|join|left|outer|inner|union|all|order|group|by|option|dateadd|datediff)$", re.IGNORECASE)
_CODE_SUFFIXES = {".cs", ".java", ".js", ".ts", ".py", ".go", ".rb", ".php"}
_STRING_LITERAL_RE = re.compile(r'@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"', re.DOTALL)
_TABLE_NAME_NOISE = {
    "a", "b", "c", "d", "e", "r", "t", "x", "y", "z",
    "the", "bar", "offset", "segment", "start", "end", "result", "results",
    "item", "items", "options",
}


def write_sql_usage_artifacts(scan_output_dir: Path, staging_dir: Path) -> None:
    """Write sql_usage.json and sql_usage.md from scanner source content.

    This is a platform-neutral pre-pass. It does not replace LLM investigation;
    it gives later phases durable SQL/table/field facts that are not vulnerable
    to chunk selection, context clipping, or short engineering-fact excerpts.
    """

    scan_result = _load_scan_result(scan_output_dir)
    records = extract_sql_usage(scan_result)
    if not records:
        return

    payload = {
        "records": records,
        "summary": {
            "record_count": len(records),
            "table_count": len({usage["table"] for record in records for usage in record.get("tables", [])}),
            "source_count": len({record["source_path"] for record in records}),
        },
    }
    (staging_dir / "sql_usage.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (staging_dir / "sql_usage.md").write_text(_markdown(payload), encoding="utf-8")


def extract_sql_usage(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for job in scan_result.get("jobs", []) or []:
        unit = str(job.get("name") or job.get("path") or "unknown")
        group = str(job.get("group") or "Global")
        job_path = str(job.get("path") or unit)
        for component in job.get("components", []) or []:
            params = component.get("params", []) or []
            for param in params:
                if not _should_process_param(param):
                    continue
                content = str(param.get("value") or "")
                if not content:
                    continue
                path = str(param.get("file_path") or job_path)
                for candidate in _sql_candidates(content, path):
                    record = _record_for_content(candidate, unit=unit, group=group, path=path, source_content=content)
                    if not record["tables"]:
                        continue
                    key = (record["source_path"], record["unit"], _fingerprint(record["sql_excerpt"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)
    return sorted(records, key=lambda item: (item["group"], item["source_path"], item["unit"]))


def _should_process_param(param: dict[str, Any]) -> bool:
    """Return True if the param is a candidate for SQL extraction.

    Filter: skip params whose key has no SQL hint and whose value
    does not match the SQL keyword regex. This avoids false
    positives from short config values like
    ``extra_agent.sql.timeout=0`` while catching the lobster's
    ``extra_response.query`` / ``extra_agent.sql_query`` convention.
    """
    key = str(param.get("name") or "").lower()
    value = str(param.get("value") or "")
    if any(token in key for token in ("query", "sql", "statement")):
        return True
    if _SQL_KEYWORD_RE.search(value):
        return True
    return False


def _sql_candidates(content: str, path: str) -> list[str]:
    """Return SQL-looking statement windows from source content.

    The scanner stores full source files in `content`. Treating an entire C# file
    as SQL causes false tables from prose/LINQ/barcode code (`bar`, `the`,
    `offset`). This extracts bounded windows around real SQL statement shapes.
    DDL-heavy .sql files are schema artifacts, not runtime usage, and are handled
    by schema_dictionary instead.
    """

    suffix = Path(path).suffix.lower()
    normalized = _normalize_sql_text(content)
    if suffix in {".sql", ".ddl", ".dml"} and _DDL_HEAVY_RE.search(normalized):
        # DDL files define schema; they are not evidence that the application
        # reads/writes every table they define.
        return []

    if suffix in _CODE_SUFFIXES:
        candidates: list[str] = []
        for literal in _extract_string_literals(content):
            normalized_literal = _normalize_sql_text(literal)
            if not _SQL_KEYWORD_RE.search(normalized_literal) or not _TABLE_REF_RE.search(normalized_literal):
                continue
            if _SQL_STATEMENT_RE.search(normalized_literal) or re.search(r"\b(from|join|into|update)\b", normalized_literal, re.I):
                if normalized_literal not in candidates:
                    candidates.append(normalized_literal)
        return candidates

    candidates: list[str] = []
    for match in _SQL_STATEMENT_RE.finditer(content):
        start = max(0, match.start() - 500)
        end = min(len(content), match.start() + 12_000)
        window = content[start:end]
        if _TABLE_REF_RE.search(window):
            candidate = _normalize_sql_text(window)
            if candidate and candidate not in candidates:
                candidates.append(candidate)

    # Some source stores a SQL query across many adjacent string literals where
    # the exact SELECT...FROM shape is broken by concatenation. Keep a fallback
    # only when table refs are explicit and no candidate was found.
    if not candidates and _SQL_KEYWORD_RE.search(content) and _TABLE_REF_RE.search(content):
        candidates.append(normalized[:12_000])
    return candidates


def _extract_string_literals(content: str) -> list[str]:
    literals: list[str] = []
    for match in _STRING_LITERAL_RE.finditer(content):
        raw = match.group(0)
        if raw.startswith('@"'):
            value = raw[2:-1].replace('""', '"')
        else:
            value = raw[1:-1]
            value = value.replace(r"\"", '"').replace(r"\r\n", "\n").replace(r"\n", "\n")
        if value.strip():
            literals.append(value)
    return literals


def _record_for_content(content: str, *, unit: str, group: str, path: str, source_content: str | None = None) -> dict[str, Any]:
    sql_text = _normalize_sql_text(content)
    alias_to_tables = _aliases(sql_text)
    referenced_tables = sorted({table for tables in alias_to_tables.values() for table in tables})
    field_usage: dict[str, dict[str, set[str]]] = {
        table: {
            "selected_fields": set(),
            "join_fields": set(),
            "filter_fields": set(),
            "order_group_fields": set(),
            "all_fields": set(),
        }
        for table in referenced_tables
    }

    for match in _ALIAS_FIELD_RE.finditer(sql_text):
        alias = match.group("alias").lower()
        tables = alias_to_tables.get(alias, [])
        if not tables:
            continue
        field = _clean_identifier(match.group("bracket") or match.group("plain") or "")
        if not field:
            continue
        category = _category_for_position(sql_text, match.start())
        for table in tables:
            field_usage.setdefault(table, _empty_field_sets())[category].add(field)
            field_usage[table]["all_fields"].add(field)

    for match in _SELECT_FROM_RE.finditer(sql_text):
        alias = (match.group("alias") or _last_identifier(match.group("table"))).lower()
        tables = alias_to_tables.get(alias) or [_normalize_table_name(match.group("table"))]
        tables = [table for table in tables if table]
        if not tables:
            continue
        for field in _unqualified_select_fields(match.group("select")):
            for table in tables:
                field_usage.setdefault(table, _empty_field_sets())["selected_fields"].add(field)
                field_usage[table]["all_fields"].add(field)

    tables: list[dict[str, Any]] = []
    for table, fields in sorted(field_usage.items()):
        aliases = sorted(alias for alias, mapped_tables in alias_to_tables.items() if table in mapped_tables)
        tables.append({
            "table": table,
            "aliases": aliases,
            "selected_fields": sorted(fields["selected_fields"]),
            "join_fields": sorted(fields["join_fields"]),
            "filter_fields": sorted(fields["filter_fields"]),
            "order_group_fields": sorted(fields["order_group_fields"]),
            "all_fields": sorted(fields["all_fields"]),
        })

    return {
        "id": f"SQL-{_fingerprint(path + unit + sql_text)[:8].upper()}",
        "group": group,
        "unit": unit,
        "source_path": path,
        "connection_name": _connection_name(source_content or content),
        "parse_status": "PARSED" if tables else "UNPARSEABLE",
        "sql_excerpt": _focused_sql_excerpt(sql_text),
        "tables": tables,
    }


def _aliases(sql_text: str) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for match in _TABLE_REF_RE.finditer(sql_text):
        table = _normalize_table_name(match.group("table"))
        if not table:
            continue
        alias = match.group("alias") or _last_identifier(table)
        alias = alias.lower()
        if alias in {"on", "where", "inner", "left", "right", "full", "cross", "join", "union"}:
            alias = _last_identifier(table).lower()
        aliases.setdefault(alias, [])
        if table not in aliases[alias]:
            aliases[alias].append(table)
    return aliases


def _category_for_position(sql_text: str, pos: int) -> str:
    current = "selected_fields"
    for match in _SECTION_RE.finditer(sql_text):
        if match.start() > pos:
            break
        section = match.group("section").lower().replace(" ", "_")
        if section == "on":
            current = "join_fields"
        elif section == "where":
            current = "filter_fields"
        elif section in {"group_by", "order_by"}:
            current = "order_group_fields"
        elif section == "select":
            current = "selected_fields"
    return current


def _unqualified_select_fields(select_body: str) -> list[str]:
    body = re.sub(r"'[^']*'|\"[^\"]*\"", " ", select_body)
    body = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", " ", body)
    fields: list[str] = []
    for match in _UNQUALIFIED_COL_RE.finditer(body):
        field = _clean_identifier(match.group("bracket") or match.group("plain") or "")
        if not field or _STRING_NOISE_RE.search(field):
            continue
        if field not in fields:
            fields.append(field)
    return fields


def _normalize_sql_text(content: str) -> str:
    text = content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _focused_sql_excerpt(sql_text: str) -> str:
    first = _SQL_KEYWORD_RE.search(sql_text)
    if not first:
        return sql_text[:12000]
    start = max(0, first.start() - 250)
    end = min(len(sql_text), first.start() + 12000)
    return sql_text[start:end].strip()


def _normalize_table_name(raw: str) -> str:
    parts = [_clean_identifier(part) for part in re.split(r"\s*\.\s*", raw or "")]
    parts = [part for part in parts if part]
    if len(parts) == 1:
        table = parts[0]
        if table.lower() in _TABLE_NAME_NOISE or len(table) <= 1:
            return ""
        return table
    if len(parts) >= 2:
        schema = parts[-2]
        table = parts[-1]
        if table.lower() in _TABLE_NAME_NOISE or len(table) <= 1:
            return ""
        return ".".join([schema, table])
    return ""


def _last_identifier(raw: str) -> str:
    return (_normalize_table_name(raw).split(".")[-1] or "").strip("[]")


def _clean_identifier(raw: str) -> str:
    return (raw or "").strip().strip("[]").strip()


def _connection_name(content: str) -> str | None:
    patterns = [
        r"\bOpenDbConnection\s*\(\s*[\"']([^\"']+)[\"']",
        r"\bSqlList\s*<[^>]+>\s*\(",
    ]
    for pattern in patterns[:1]:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _empty_field_sets() -> dict[str, set[str]]:
    return {
        "selected_fields": set(),
        "join_fields": set(),
        "filter_fields": set(),
        "order_group_fields": set(),
        "all_fields": set(),
    }


def _param_value(params: list[dict[str, Any]], name: str) -> str | None:
    for param in params:
        if str(param.get("name") or "").lower() == name.lower():
            return str(param.get("value") or "")
    return None


def _fingerprint(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()


def _load_scan_result(scan_output_dir: Path) -> dict[str, Any]:
    path = scan_output_dir / "scan_result.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# SQL Usage",
        "",
        "Structured SQL/table/field usage extracted deterministically from scanned source content.",
        "",
        f"Records: {payload['summary']['record_count']}  Tables: {payload['summary']['table_count']}  Sources: {payload['summary']['source_count']}",
        "",
    ]
    for record in payload["records"]:
        lines.extend([
            f"## {record['id']} - {record['unit']}",
            f"Source: {record['source_path']}  Group: {record['group']}  Connection: {record.get('connection_name') or 'UNKNOWN'}  Status: {record['parse_status']}",
            "",
            "| Table | Aliases | Selected fields | Join fields | Filter fields | Order/group fields | All fields |",
            "|-------|---------|-----------------|-------------|---------------|--------------------|------------|",
        ])
        for usage in record.get("tables", []):
            lines.append(
                f"| {usage['table']} | {', '.join(usage['aliases'])} | "
                f"{', '.join(usage['selected_fields']) or 'UNKNOWN'} | "
                f"{', '.join(usage['join_fields']) or 'UNKNOWN'} | "
                f"{', '.join(usage['filter_fields']) or 'UNKNOWN'} | "
                f"{', '.join(usage['order_group_fields']) or 'UNKNOWN'} | "
                f"{', '.join(usage['all_fields']) or 'UNKNOWN'} |"
            )
        lines.extend(["", "```sql", record["sql_excerpt"], "```", ""])
    return "\n".join(lines).rstrip() + "\n"
