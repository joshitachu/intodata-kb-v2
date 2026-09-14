"""Offline acceptance scoring for frozen Talend landscape fixtures.

The evaluator consumes retained v2 artifacts, not model confidence or document
volume. It is safe for CI: no provider, network, or customer input.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.knowledge_contracts import (
    accepted_knowledge_claims,
    load_knowledge_manifest,
)


def evaluate_talend_landscape(staging_dir: Path, expected_path: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    manifest = load_knowledge_manifest(staging_dir)
    claims = accepted_knowledge_claims(staging_dir, manifest)
    retained_root = staging_dir / "_evidence_registry"
    retained_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(retained_root.rglob("*"))
        if path.is_file() and path.suffix in {".md", ".json"}
    )
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(staging_dir.glob("*.md"))
    )
    addressed = {
        requirement_id
        for claim in claims
        if claim.kind != "unknown"
        for requirement_id in claim.requirement_ids
    }
    mandatory = {r.requirement_id for r in manifest.requirements if r.mandatory}
    source_inventory_path = retained_root / "landscape" / "source_inventory.json"
    source_inventory = (
        json.loads(source_inventory_path.read_text(encoding="utf-8"))
        if source_inventory_path.is_file()
        else []
    )
    missing_facts = [
        value for value in expected.get("exact_retained_facts", [])
        if value not in retained_text
    ]
    forbidden = [
        value for value in expected.get("forbidden_public_phrases", [])
        if value in public_text
    ]
    required_gap_stages = set(expected.get("required_gap_stages", []))
    actual_gap_stages = {r.gap_stage for r in manifest.requirements if r.gap_stage}
    inventory_without_outcome = [
        str(row.get("path", "<unknown>"))
        for row in source_inventory
        if not row.get("outcome") or not row.get("reason")
    ]
    checks = {
        "exact_fact_retention": not missing_facts,
        "mandatory_requirements_addressed": not (mandatory - addressed),
        "required_gap_stages_present": required_gap_stages <= actual_gap_stages,
        "source_inventory_accounted": bool(source_inventory) and not inventory_without_outcome,
        "forbidden_public_phrases_absent": not forbidden,
    }
    return {
        "schema_version": "talend-landscape-eval-v1",
        "ok": all(checks.values()),
        "checks": checks,
        "counts": {
            "requirements": len(manifest.requirements),
            "mandatory": len(mandatory),
            "mandatory_addressed": len(mandatory & addressed),
            "accepted_claims": len(claims),
            "source_inventory": len(source_inventory),
        },
        "missing_exact_facts": missing_facts,
        "missing_mandatory_requirement_ids": sorted(mandatory - addressed),
        "missing_gap_stages": sorted(required_gap_stages - actual_gap_stages),
        "source_inventory_without_outcome": inventory_without_outcome,
        "forbidden_public_phrases_found": forbidden,
    }
