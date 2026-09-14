"""Deterministic claim/requirement-to-output retention receipt."""
from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT, accepted_knowledge_claims, load_knowledge_manifest, write_json,
)


def reconcile_landscape_documents(staging_dir: Path) -> dict:
    manifest = load_knowledge_manifest(staging_dir)
    claims = accepted_knowledge_claims(staging_dir, manifest)
    public_docs = sorted(p for p in staging_dir.glob("*.md") if not p.name.startswith("_"))
    doc_text = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in public_docs}
    all_text = "\n".join(doc_text.values())
    retained_claims = sorted(c.claim_id for c in claims if f"claim:{c.claim_id}" in all_text)
    claim_ids = {c.claim_id for c in claims}
    missing_claims = sorted(claim_ids - set(retained_claims))
    explained = {rid for c in claims if c.kind != "unknown" for rid in c.requirement_ids}
    missing_explanations = sorted(r.requirement_id for r in manifest.requirements
                                  if r.mandatory and r.explanation_required
                                  and r.requirement_id not in explained)
    refs_root = staging_dir / "_evidence_registry" / "doc_slices"
    requirements = []
    for requirement in manifest.requirements:
        destinations = [path for path in requirement.output_destinations if (staging_dir / path.split("#", 1)[0]).is_file()]
        requirements.append({
            "requirement_id": requirement.requirement_id,
            "unit_id": requirement.unit_id,
            "extraction_status": requirement.extraction_status,
            "gap_stage": requirement.gap_stage,
            "explanation_required": requirement.explanation_required,
            "explanation_recorded": requirement.requirement_id in explained,
            "accessible_destinations": destinations,
            "document_mentions": sorted(name for name, text in doc_text.items()
                                        if requirement.requirement_id in text),
        })
    missing_destinations = sorted(r["requirement_id"] for r in requirements
                                  if r["extraction_status"] == "extracted" and not r["accessible_destinations"])
    report = {
        "schema_version": "landscape-document-reconciliation-v2",
        "snapshot_id": manifest.snapshot_id,
        "ok": not missing_destinations and not missing_claims and not missing_explanations,
        "missing_explanation_requirement_ids": missing_explanations,
        "semantic_review": "not_performed",
        "accepted_claim_count": len(claim_ids),
        "retained_claim_count": len(retained_claims),
        "missing_claim_ids": missing_claims,
        "missing_reference_requirement_ids": missing_destinations,
        "requirements": requirements,
        "reference_index": (refs_root / "landscape_index.md").relative_to(staging_dir).as_posix(),
    }
    write_json(staging_dir / LANDSCAPE_ROOT / "document_reconciliation.json", report)
    return report
