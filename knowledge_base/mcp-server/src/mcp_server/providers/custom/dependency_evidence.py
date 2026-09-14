"""Repository for scanner-owned Talend dependencies; never parse raw source.

Only typed scanner associations establish routine ownership. A same-name routine
in another root is not a fallback. Legacy unresolved associations stay explicit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_scan(scan_output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = scan_output_dir.resolve()
    def read(relative: str) -> dict:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("scan evidence path escapes source cache")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return read("scan_result.json"), read("scan/manifest.json")


def canonical_job_id(job: dict[str, Any]) -> str:
    return str(job.get("job_id") or job.get("name") or "")


class DependencyEvidenceRepository:
    def __init__(self, scan: dict[str, Any], manifest: dict[str, Any]):
        self.scan = scan
        self.manifest = manifest
        self.jobs: dict[str, dict] = {}
        for job in scan.get("jobs", []):
            identity = canonical_job_id(job)
            if not identity or identity in self.jobs:
                raise ValueError(f"ambiguous canonical scan unit: {identity}")
            self.jobs[identity] = job
        self.routines: dict[str, tuple[int, dict]] = {}
        for index, routine in enumerate(scan.get("routines", [])):
            identity = routine.get("routine_id")
            if identity:
                if identity in self.routines:
                    raise ValueError(f"ambiguous canonical routine: {identity}")
                self.routines[str(identity)] = (index, routine)

    @classmethod
    def from_directory(cls, scan_output_dir: Path) -> "DependencyEvidenceRepository":
        return cls(*load_scan(scan_output_dir))

    def routine_dependencies(self, unit_id: str) -> tuple[list[tuple[int, dict]], list[str]]:
        if unit_id not in self.jobs:
            raise ValueError(f"unknown canonical unit: {unit_id}")
        links = self.manifest.get("job_routine_links", {}).get(unit_id, [])
        gaps: list[str] = []
        resolved: dict[str, tuple[int, dict]] = {}
        for link in links:
            identity = str(link.get("routine_id") or "")
            candidate = self.routines.get(identity)
            if link.get("resolution") not in (None, "resolved", "exact", "same_root", "same_root_exact") or not candidate:
                gaps.append(f"Routine association unresolved: {identity or link.get('name', 'unknown')}")
                continue
            routine = candidate[1]
            declared_root = str(link.get("source_root") or "")
            if declared_root and declared_root != str(routine.get("source_root") or ""):
                gaps.append(f"Routine origin mismatch: {identity}")
                continue
            resolved[identity] = candidate
        for gap in self.manifest.get("unresolved_routine_refs", {}).get(unit_id, []):
            gaps.append("Routine reference unresolved: " + json.dumps(gap, sort_keys=True))
        return list(resolved.values()), gaps

    def render_routines(self, unit_id: str) -> str:
        routines, gaps = self.routine_dependencies(unit_id)
        if not routines and not gaps:
            return ""
        lines = ["## REFERENCED ROUTINE IMPLEMENTATIONS", "",
                 "Scanner-resolved dependencies of this unit; dependency ownership remains separate.", ""]
        for _, routine in routines:
            source = str(routine.get("source_code") or "")
            lines.extend([f"### {routine['routine_id']}", "",
                          f"Origin: {routine.get('source_root', '')}/{routine.get('path', '')}", "",
                          "Source completeness: " + ("complete" if routine.get("source_complete") else "legacy/partial; refresh required"), ""])
            fence = "`" * max(3, 1 + max((len(s) for s in __import__('re').findall(r"`+", source)), default=0))
            lines.extend([fence + "java", source, fence, ""])
        lines.extend("- Resolution gap: " + gap for gap in gaps)
        return "\n".join(lines)
