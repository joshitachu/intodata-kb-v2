"""Pre-writing, source-backed execution journeys for landscape documents."""
from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.dependency_evidence import canonical_job_id, load_scan
from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT, accepted_knowledge_claims, load_knowledge_manifest, write_json,
)


def build_source_journeys(staging_dir: Path, scan_output_dir: Path) -> Path:
    scan, _ = load_scan(scan_output_dir)
    manifest = load_knowledge_manifest(staging_dir)
    claims = accepted_knowledge_claims(staging_dir, manifest)
    claim_ids_by_unit: dict[str, list[str]] = {}
    for claim in claims:
        for unit_id in claim.unit_names:
            claim_ids_by_unit.setdefault(unit_id, []).append(claim.claim_id)
    journeys: list[dict] = []
    scoped_units = {r.unit_id for r in manifest.requirements}
    for job in scan.get("jobs", []):
        unit_id = canonical_job_id(job)
        if not unit_id or unit_id not in scoped_units:
            continue
        edges = [{
            "source": edge.get("source", ""), "target": edge.get("target", ""),
            "kind": edge.get("connector_name", "") or "UNKNOWN",
            "label": edge.get("name", ""),
        } for edge in job.get("connections", []) if edge.get("source") or edge.get("target")]
        calls = [{
            "target_unit_id": ref.get("resolved_job_id", ""),
            "declared_target": ref.get("child_job", ""),
            "resolution": ref.get("resolution", "unresolved"),
        } for ref in job.get("trunjob_refs", [])]
        journeys.append({
            "unit_id": unit_id, "display_name": job.get("name", unit_id),
            "artifact_kind": job.get("artifact_kind", "job"),
            "edges": edges, "calls": calls,
            "accepted_claim_ids": sorted(claim_ids_by_unit.get(unit_id, [])),
            "runtime_status": "requires_runtime_evidence",
            "business_identity_status": "requires_supplied_intent",
        })
    payload = {"schema_version": "source-journeys-v2", "snapshot_id": manifest.snapshot_id,
               "authority": "implemented_design", "journeys": journeys}
    write_json(staging_dir / LANDSCAPE_ROOT / "source_journeys.json", payload)
    lines = ["# Source-backed Talend Journeys", "",
             "These paths describe exported implementation. Runtime occurrence and business identity require their own evidence.", ""]
    for journey in journeys:
        lines.extend([f"## {journey['unit_id']}", ""])
        for edge in journey["edges"]:
            lines.append(f"- `{edge['source']}` -> `{edge['target']}` [{edge['kind']}; {edge['label']}]")
        for call in journey["calls"]:
            target = call["target_unit_id"] or call["declared_target"] or "unknown"
            lines.append(f"- Calls `{target}` [resolution: {call['resolution']}]")
        if journey["accepted_claim_ids"]:
            lines.append("- Related interpretations: " + ", ".join(
                f"`claim:{claim_id}`" for claim_id in journey["accepted_claim_ids"]))
        lines.append("")
    output = staging_dir / "_analysis" / "_source_journeys.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
