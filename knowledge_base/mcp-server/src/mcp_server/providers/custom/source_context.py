"""Direct redacted source packaging for custom documentation generation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SOURCE_CONTEXT_DIR = "_source_context"
SOURCE_COVERAGE_NAME = "_source_coverage.json"

_SECRET_VALUE_RE = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|license[_-]?key)\b\s*[:=]\s*)(?P<quote>['\"]?)(?P<value>[^'\";\s,}>\n]+)(?P=quote)",
    re.IGNORECASE,
)
_CONNECTION_SECRET_RE = re.compile(
    r"(?P<prefix>\b(?:Password|Pwd|User\s+ID|UID)\s*=\s*)(?P<value>[^;\"'\n]+)",
    re.IGNORECASE,
)
_XML_SENSITIVE_ADD_RE = re.compile(
    r"(?P<prefix>\bkey\s*=\s*['\"](?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|license[_-]?key)['\"][^>\n]*?\bvalue\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_NOISE_PATH_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".mypy_cache", "coverage", "bin", "obj", "target",
}
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".exe", ".dll", ".jar", ".class", ".zip", ".gz",
}
_HIGH_PRIORITY_EXTS = {
    ".config", ".xml", ".json", ".yaml", ".yml", ".toml", ".env",
    ".properties", ".ini", ".cfg", ".conf", ".sql", ".ddl", ".dml",
}


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
            "retrievable": 0,
            "skipped_noise": 0,
            "skipped_binary": 0,
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
    return processed_groups, coverage


def _collect_scanned_sources(scan_output_dir: Path) -> dict[str, list[dict[str, str]]]:
    jobs = _load_jobs(scan_output_dir)
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
    if parts & _NOISE_PATH_PARTS:
        return "skipped_noise"
    if suffix in _BINARY_EXTS:
        return "skipped_binary"
    if content.startswith("[FILE TOO LARGE"):
        return "skipped_too_large"
    if content.startswith("[BINARY"):
        return "skipped_binary"
    return None


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

    redacted = _SECRET_VALUE_RE.sub(replace_secret, content)
    redacted = _CONNECTION_SECRET_RE.sub(replace_connection_secret, redacted)
    redacted = _XML_SENSITIVE_ADD_RE.sub(replace_secret, redacted)
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
