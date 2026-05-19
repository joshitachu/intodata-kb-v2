"""Deterministic quick scan for Git-triggered documentation updates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.services.git_diff_patcher import FileChange, _is_ignored_path


SYMBOL_RE = re.compile(
    r"\b(?:class|def|function|interface|type|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
README_RE = re.compile(r"(^|/)readme(\.[^/]*)?$", re.IGNORECASE)
TEST_RE = re.compile(r"(^|/)(test|tests|__tests__|spec)/|\.(test|spec)\.[A-Za-z0-9]+$")
NUMERIC_CLAIM_RE = re.compile(r"[-+].*?\b\d+(?:\.\d+)?\s*(?:%|percent|ms|seconds?|minutes?|hours?|days?|files?|classes?|tests?|endpoints?|routes?|jobs?|components?|widgets?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QuickScanReport:
    """Small, model-friendly summary of changed files and likely doc impact."""

    changed_files: list[str]
    doc_relevant_files: list[str]
    new_source_files: list[str] = field(default_factory=list)
    deleted_source_files: list[str] = field(default_factory=list)
    added_tests: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    readme_numeric_claims: list[str] = field(default_factory=list)
    likely_doc_impact: list[str] = field(default_factory=list)
    module_folders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_files": self.changed_files,
            "doc_relevant_files": self.doc_relevant_files,
            "new_source_files": self.new_source_files,
            "deleted_source_files": self.deleted_source_files,
            "added_tests": self.added_tests,
            "symbols": self.symbols,
            "readme_numeric_claims": self.readme_numeric_claims,
            "likely_doc_impact": self.likely_doc_impact,
            "module_folders": self.module_folders,
        }


class QuickChangeScanner:
    """Extract stable facts from name-status and unified diff output."""

    def scan(self, changes: list[FileChange], diff_text: str) -> QuickScanReport:
        changed_files = [change.path for change in changes]
        doc_relevant = [change.path for change in changes if not _is_ignored_path(change.path)]
        new_source = [
            change.path for change in changes
            if change.status == "A" and self._is_source_path(change.path)
        ]
        deleted_source = [
            change.path for change in changes
            if change.status == "D" and self._is_source_path(change.path)
        ]
        added_tests = [
            change.path for change in changes
            if change.status == "A" and TEST_RE.search(change.path)
        ]
        symbols = sorted({match.group(1) for match in SYMBOL_RE.finditer(diff_text)})
        claims = self._readme_numeric_claims(changes, diff_text)
        folders = self._module_folders(changes)
        impacts = self._likely_impacts(changes, diff_text, new_source, deleted_source, added_tests, claims, folders)
        return QuickScanReport(
            changed_files=changed_files,
            doc_relevant_files=doc_relevant,
            new_source_files=new_source,
            deleted_source_files=deleted_source,
            added_tests=added_tests,
            symbols=symbols[:40],
            readme_numeric_claims=claims[:20],
            likely_doc_impact=impacts,
            module_folders=folders[:20],
        )

    def _is_source_path(self, path: str) -> bool:
        suffix = Path(path).suffix.lower()
        return suffix in {
            ".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".go",
            ".rs", ".rb", ".php", ".sql", ".xml", ".json", ".yaml", ".yml",
        } and not TEST_RE.search(path) and not README_RE.search(path)

    def _readme_numeric_claims(self, changes: list[FileChange], diff_text: str) -> list[str]:
        if not any(README_RE.search(change.path) for change in changes):
            return []
        claims: list[str] = []
        for line in diff_text.splitlines():
            if NUMERIC_CLAIM_RE.search(line):
                claims.append(line[:240])
        return claims

    def _module_folders(self, changes: list[FileChange]) -> list[str]:
        folders: set[str] = set()
        for change in changes:
            parts = Path(change.path).parts
            for idx, part in enumerate(parts[:-1]):
                if part.lower() in {"src", "app", "lib", "services", "controllers", "patterns", "modules"} and idx + 1 < len(parts):
                    folders.add("/".join(parts[: idx + 2]))
        return sorted(folders)

    def _likely_impacts(
        self,
        changes: list[FileChange],
        diff_text: str,
        new_source: list[str],
        deleted_source: list[str],
        added_tests: list[str],
        readme_claims: list[str],
        folders: list[str],
    ) -> list[str]:
        lowered_paths = " ".join(change.path for change in changes).lower()
        lowered_diff = diff_text.lower()
        impacts: list[str] = []
        if new_source:
            impacts.append("new_source_files")
        if deleted_source:
            impacts.append("deleted_source_files")
        if added_tests:
            impacts.append("added_tests")
        if readme_claims:
            impacts.append("readme_numeric_claims")
        if "/patterns/" in lowered_paths or "chain of responsibility" in lowered_diff:
            impacts.append("architecture_or_patterns")
        if "/api/" in lowered_paths or "endpoint" in lowered_diff or "route" in lowered_diff:
            impacts.append("api_surface")
        if "/config" in lowered_paths or ".env" in lowered_diff:
            impacts.append("runtime_config")
        if folders:
            impacts.append("module_folder_changes")
        return impacts
