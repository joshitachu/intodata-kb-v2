"""Periodic structural drift audit. Reports discrepancies; never edits docs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.services.living_docs.change_analysis import section_catalog
from mcp_server.services.living_docs.run_journal import atomic_json
from mcp_server.services.git_remote_service import run_git


async def audit_document_drift(
    output: Path, repo: str, source_revision: str, *, force: bool = False
) -> dict:
    report_path = output / "_version_history/drift_audit.json"
    if report_path.is_file() and not force:
        previous = json.loads(report_path.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(previous["checked_at"])
        if age.total_seconds() < 86400 and previous.get("checked_revision") == source_revision:
            return previous
    index_path = output / "_version_history/evidence_index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {}
    current = section_catalog(output / "docs")
    findings = []
    indexed = index.get("documents", {})
    for doc, entries in indexed.items():
        for section, facts in entries.items():
            if current.get(doc, {}).get(section) != facts.get("sha256"):
                findings.append(
                    {
                        "document": doc,
                        "section": section,
                        "reason": "document_changed_outside_recorded_update",
                    }
                )
    for doc, entries in current.items():
        for section in entries:
            if not indexed.get(doc, {}).get(section, {}).get("sources"):
                findings.append(
                    {
                        "document": doc,
                        "section": section,
                        "reason": "source_evidence_not_yet_mapped",
                    }
                )
    old = index.get("source_revision")
    if old and old != source_revision:
        changed = set(
            (await run_git(repo, "diff", "--name-only", old, source_revision)).splitlines()
        )
        for doc, entries in indexed.items():
            for section, facts in entries.items():
                overlap = changed.intersection(facts.get("sources", []))
                if overlap:
                    findings.append(
                        {
                            "document": doc,
                            "section": section,
                            "reason": "source_changed_since_index",
                            "sources": sorted(overlap),
                        }
                    )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked_revision": source_revision,
        "kind": "structural_evidence_audit",
        "findings": findings,
        "limitation": "A clean structural audit does not prove semantic correctness.",
    }
    atomic_json(report_path, report)
    return report
