"""Pack-scoped evidence bundle generation for deterministic artifact contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_SECTION_HEADING_RE = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


SUPPORTED_EVIDENCE_CATEGORIES = {
    "sql_queries",
    "ddl_schema",
    "orm_entities",
    "db_connections",
    "config_references",
    "routes",
    "handlers",
    "auth",
    "source_summary",
}


CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "sql_queries": re.compile(r"\b(select|insert|update|delete|merge|from|join|where)\b", re.I),
    "ddl_schema": re.compile(r"\b(create\s+table|alter\s+table|drop\s+table|constraint|primary\s+key|foreign\s+key|schema|ddl)\b", re.I),
    "orm_entities": re.compile(r"\b(entity|model|poco|dbcontext|repository|hibernate|jpa|sequelize|typeorm)\b", re.I),
    "db_connections": re.compile(r"\b(connection|string|datasource|jdbc|odbc|database|db_|server=|host=)\b", re.I),
    "config_references": re.compile(r"\b(config|setting|environment|env|properties|yaml|json|xml|ini)\b", re.I),
    "routes": re.compile(r"\b(route|router|endpoint|controller|@get|@post|app\.(get|post|put|delete))\b", re.I),
    "handlers": re.compile(r"\b(handler|listener|consumer|callback|webhook|process|execute)\b", re.I),
    "auth": re.compile(r"\b(auth|oauth|jwt|token|permission|role|login|credential|secret)\b", re.I),
    "source_summary": re.compile(r"\b(file|source|component|unit|job|module|class|function)\b", re.I),
}

_EXCLUDE_PATTERNS = {
    "binary": re.compile(r"\.(png|jpg|jpeg|gif|webp|pdf|zip|jar|class|dll|exe|bin|ico|woff2?)($|\.md$)", re.I),
    "static_assets": re.compile(r"\.(css|scss|svg|png|jpg|jpeg|gif|webp|ico|woff2?)($|\.md$)", re.I),
    "generated_files": re.compile(r"(^|/)(dist|build|target|node_modules|__pycache__|generated)(/|$)", re.I),
}


class EvidenceBundleBuilder:
    """Build stable JSON and Markdown evidence bundles from staging artifacts."""

    def __init__(self, staging_dir: Path, manifest: Any) -> None:
        self.staging_dir = staging_dir
        self.manifest = manifest

    def build_all(self) -> dict[str, dict[str, str]]:
        outputs: dict[str, dict[str, str]] = {}
        for profile_name, profile in getattr(self.manifest, "evidence_profiles", {}).items():
            outputs[profile_name] = self.build(profile_name, profile)
        return outputs

    def build(self, profile_name: str, profile: Any) -> dict[str, str]:
        include = [cat for cat in getattr(profile, "include", []) if cat in SUPPORTED_EVIDENCE_CATEGORIES]
        exclude = list(getattr(profile, "exclude", []))
        evidence_dir = self.staging_dir / "_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        json_path = evidence_dir / f"{profile_name}.json"
        md_path = evidence_dir / f"{profile_name}.md"

        categories = {category: [] for category in include}
        source_files = self._source_files(exclude)
        base_files = self._base_files()
        for category in include:
            for path in [*base_files, *source_files]:
                snippets = self._snippets_for(path, category)
                for snippet in snippets:
                    categories[category].append({
                        "source": path.relative_to(self.staging_dir).as_posix(),
                        "snippet": snippet,
                    })

        missing = [category for category, entries in categories.items() if not entries]
        payload = {
            "profile": profile_name,
            "pack": {
                "name": getattr(self.manifest, "name", ""),
                "version": getattr(self.manifest, "version", ""),
            },
            "include": include,
            "exclude": exclude,
            "categories": categories,
            "missing_categories": missing,
            "unknown_policy": getattr(getattr(self.manifest, "validation", None), "unknown_policy", "explicit"),
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        md_path.write_text(self._markdown(payload), encoding="utf-8")
        return {
            "json": json_path.relative_to(self.staging_dir).as_posix(),
            "md": md_path.relative_to(self.staging_dir).as_posix(),
        }

    def _base_files(self) -> list[Path]:
        names = [
            "facts_manifest.json",
            "engineering_facts.json",
            "engineering_facts.md",
            "schema_dictionary.md",
            "sql_usage.json",
            "sql_usage.md",
            "config_runtime_map.json",
            "config_runtime_map.md",
            "data_flow_map.md",
            "api_surface.md",
            "_preprocess/_source_coverage.json",
        ]
        return [self.staging_dir / name for name in names if (self.staging_dir / name).is_file()]

    def _source_files(self, exclude: list[str]) -> list[Path]:
        preprocess = self.staging_dir / "_preprocess"
        if not preprocess.exists():
            return []
        files: list[Path] = []
        for path in sorted(preprocess.glob("*.md")):
            rel = path.relative_to(self.staging_dir).as_posix()
            if path.name.startswith("_"):
                continue
            if any(_EXCLUDE_PATTERNS[key].search(rel) for key in exclude if key in _EXCLUDE_PATTERNS):
                continue
            files.append(path)
        return files

    def _snippets_for(self, path: Path, category: str) -> list[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        if path.suffix.lower() == ".json":
            return self._json_snippets(text, category)

        pattern = CATEGORY_PATTERNS[category]

        if category == "ddl_schema" and path.name == "schema_dictionary.md":
            sections = self._schema_dictionary_sections(text)
            if sections:
                return sections

        if category == "sql_queries" and path.name == "sql_usage.md":
            sections = self._sql_usage_sections(text)
            if sections:
                return sections

        # Code-heavy categories: prefer complete fenced code blocks.
        if category in {"sql_queries", "ddl_schema"}:
            raw_blocks = [
                m.group(0).strip()
                for m in _FENCED_BLOCK_RE.finditer(text)
                if pattern.search(m.group(0))
            ]
            if raw_blocks:
                blocks: list[str] = []
                # For ddl_schema, large blocks (>4000 chars) are split per CREATE TABLE so
                # each table definition survives instead of being cut mid-column.
                _CREATE_TABLE_SPLIT_RE = re.compile(
                    r"(?=CREATE\s+TABLE\b)", re.IGNORECASE
                )
                for block in raw_blocks:
                    if category == "ddl_schema" and len(block) > 4000:
                        parts = _CREATE_TABLE_SPLIT_RE.split(block)
                        for part in parts:
                            part = part.strip()
                            if part and pattern.search(part):
                                blocks.append(part[:4000])
                    else:
                        blocks.append(block[:4000])
                    if len(blocks) >= 24:
                        break
                if blocks:
                    return blocks[:24]

        # All categories: try section-level extraction first.
        sections = self._markdown_sections_for(text, pattern)
        if sections:
            return sections[:8]

        # Fallback: 5-line context windows around matching lines.
        return self._line_snippets(text, pattern, category)

    def _json_snippets(self, text: str, category: str) -> list[str]:
        """Extract relevant entries from a JSON artifact without regex line scanning."""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return self._line_snippets(text, CATEGORY_PATTERNS[category], category)

        pattern = CATEGORY_PATTERNS[category]
        snippets: list[str] = []

        def _walk(node: Any, depth: int = 0) -> None:
            if len(snippets) >= 12 or depth > 6:
                return
            if isinstance(node, str):
                if pattern.search(node):
                    snippet = node.strip()[:4000]
                    if snippet and snippet not in snippets:
                        snippets.append(snippet)
            elif isinstance(node, dict):
                serialized = json.dumps(node, indent=2)
                if pattern.search(serialized) and len(serialized) < 4000:
                    if serialized not in snippets:
                        snippets.append(serialized)
                        return
                for value in node.values():
                    _walk(value, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    _walk(item, depth + 1)

        _walk(data)
        return snippets

    def _markdown_sections_for(self, text: str, pattern: re.Pattern[str]) -> list[str]:
        """Extract complete markdown sections that contain a category pattern match."""
        heading_spans = [(m.start(), m.end()) for m in _SECTION_HEADING_RE.finditer(text)]
        if not heading_spans:
            return []

        snippets: list[str] = []
        for i, (start, _) in enumerate(heading_spans):
            end = heading_spans[i + 1][0] if i + 1 < len(heading_spans) else len(text)
            section = text[start:end].strip()
            if not pattern.search(section):
                continue
            if section and section not in snippets:
                snippets.append(section[:4000])
            if len(snippets) >= 8:
                break
        return snippets

    def _line_snippets(self, text: str, pattern: re.Pattern[str], category: str) -> list[str]:
        """5-line context windows around pattern-matching lines (original fallback)."""
        lines = text.splitlines()
        snippets: list[str] = []
        for idx, line in enumerate(lines):
            if not pattern.search(line):
                continue
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            snippet = "\n".join(lines[start:end]).strip()
            if snippet and snippet not in snippets:
                snippets.append(snippet[:4000])
            if len(snippets) >= 12:
                break
        if not snippets and category == "source_summary" and text.strip():
            snippets.append(text.strip()[:4000])
        return snippets

    def _schema_dictionary_sections(self, text: str) -> list[str]:
        """Return each parsed table section from schema_dictionary.md as DDL evidence."""
        heading_spans = [(m.start(), m.end()) for m in _SECTION_HEADING_RE.finditer(text)]
        snippets: list[str] = []
        for i, (start, _) in enumerate(heading_spans):
            end = heading_spans[i + 1][0] if i + 1 < len(heading_spans) else len(text)
            section = text[start:end].strip()
            if not section.startswith("## "):
                continue
            if section and section not in snippets:
                snippets.append(section[:12000])
        return snippets

    def _sql_usage_sections(self, text: str) -> list[str]:
        """Return each deterministic SQL usage section as SQL evidence."""
        heading_spans = [(m.start(), m.end()) for m in _SECTION_HEADING_RE.finditer(text)]
        snippets: list[str] = []
        for i, (start, _) in enumerate(heading_spans):
            end = heading_spans[i + 1][0] if i + 1 < len(heading_spans) else len(text)
            section = text[start:end].strip()
            if not section.startswith("## "):
                continue
            if section and section not in snippets:
                snippets.append(section[:16000])
        return snippets

    def _markdown(self, payload: dict[str, Any]) -> str:
        parts = [
            f"# Evidence Bundle: {payload['profile']}",
            "",
            f"Pack: {payload['pack']['name']} {payload['pack']['version']}".strip(),
            f"Unknown policy: {payload['unknown_policy']}",
            "",
            "Use only evidenced facts below. If required evidence is absent, write `UNKNOWN` or `not evidenced`.",
            "",
        ]
        for category, entries in payload["categories"].items():
            parts.append(f"## {category}")
            if not entries:
                parts.append("UNKNOWN - not evidenced in supplied artifacts.")
                parts.append("")
                continue
            for entry in entries:
                parts.append(f"### {entry['source']}")
                parts.append(entry["snippet"])
                parts.append("")
        return "\n".join(parts).rstrip() + "\n"
