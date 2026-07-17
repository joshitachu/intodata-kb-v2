from __future__ import annotations

import hashlib
import json

import pytest

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.prompts import doc_prompt, group_analysis_prompt
from mcp_server.schemas import (
    NexusAudienceRender,
    NexusPackSnapshot,
    NexusPrivatePackReceipt,
    NexusPrivateAnalysisArtifactV3,
    NexusPrivateAnalysisPack,
    NexusPresentation,
    compute_nexus_snapshot_hash,
    compute_nexus_private_artifact_hash,
)
from mcp_server.services.generation_run_state import (
    GenerationReceiptError,
    manifest_path,
    nexus_receipt_path,
    persist_nexus_execution_receipt,
    write_run_manifest,
)
from mcp_server.services.nexus_execution_contract import (
    build_nexus_execution_receipt,
    presentation_instructions_for_phase,
    private_analysis_instructions_for_document,
)


def _snapshot(presentation: NexusPresentation | None = None) -> NexusPackSnapshot:
    snapshot = NexusPackSnapshot.model_validate({
        "policy_id": "org.default",
        "policy_hash": "a" * 64,
        "foundation_pack_id": "foundation.core",
        "functional_pack_ids": ["feature.operations"],
        "style_pack_ids": ["style.engineering"],
        "resolved_packs": [
            {"id": "foundation.core", "version": "1.0.0", "content_hash": "b" * 64},
            {"id": "feature.operations", "version": "1.0.0", "content_hash": "c" * 64},
            {"id": "style.engineering", "version": "1.0.0", "content_hash": "d" * 64},
        ],
        "snapshot_hash": "0" * 64,
    })
    return snapshot.model_copy(
        update={"snapshot_hash": compute_nexus_snapshot_hash(snapshot, presentation)}
    )


def _presentation() -> NexusPresentation:
    return NexusPresentation.model_validate({
        "audience": "engineering",
        "tone": "concise",
        "technical_depth": "deep",
        "output_organization": "sectioned",
        "formatting": ["markdown", "tables"],
        "communication": ["qualify_uncertainty", "include_sources"],
    })


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="project",
        platform="generic",
        canonical_unit_label="code units",
        unit_count=1,
        groups={"A": 1},
        units=[UnitFact(name="UnitA", group="A")],
    )


def test_presentation_mapping_is_phase3_only_and_preserves_phase2_prompt():
    presentation = _presentation()
    facts = _facts()
    phase2_before = group_analysis_prompt(facts, "A")

    assert presentation_instructions_for_phase("phase1", presentation) == ""
    assert presentation_instructions_for_phase("phase2", presentation) == ""
    assert group_analysis_prompt(facts, "A") == phase2_before

    phase3 = doc_prompt(facts, "03_DATA_FLOWS_AND_LINEAGE", nexus_presentation=presentation)
    assert "PRESENTATION PROFILE (WORDING AND LAYOUT ONLY)" in phase3
    assert "implementation-level depth for supported claims" in phase3
    assert "## Evidence Register" in phase3
    assert "must not change findings, evidence requirements, required sections" in phase3


def test_receipt_retains_snapshot_and_is_immutable(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    write_run_manifest(staging, run_id=7, project_id="project", phase="startup", status="running")
    receipt = build_nexus_execution_receipt(
        pack_ids=["foundation.core", "feature.operations", "style.engineering"],
        snapshot=_snapshot(_presentation()),
        presentation=_presentation(),
    )

    persist_nexus_execution_receipt(staging, receipt)
    persist_nexus_execution_receipt(staging, receipt)

    manifest = json.loads(manifest_path(staging).read_text(encoding="utf-8"))
    stored = json.loads(nexus_receipt_path(staging).read_text(encoding="utf-8"))
    assert manifest["nexus_execution_contract"] == receipt
    assert len(stored["nexus_pack_snapshot"]["snapshot_hash"]) == 64
    assert stored["nexus_pack_ids"] == [
        "foundation.core",
        "feature.operations",
        "style.engineering",
    ]
    assert len(stored["receipt_sha256"]) == 64

    changed = {**receipt, "nexus_pack_ids": ["foundation.core"]}
    with pytest.raises(GenerationReceiptError, match="cannot change"):
        persist_nexus_execution_receipt(staging, changed)


def test_snapshot_is_not_rendered_as_presentation_instructions():
    receipt = build_nexus_execution_receipt(
        pack_ids=["foundation.core"],
        snapshot=_snapshot(),
        presentation=None,
    )
    assert presentation_instructions_for_phase("phase3", None) == ""
    assert receipt["nexus_pack_snapshot"] is not None


def test_snapshot_hash_matches_nexus_canonical_payload():
    presentation = _presentation()
    snapshot = _snapshot(presentation)
    payload = {
        "policy_id": snapshot.policy_id,
        "policy_hash": snapshot.policy_hash,
        "foundation_pack_id": snapshot.foundation_pack_id,
        "functional_pack_ids": snapshot.functional_pack_ids,
        "style_pack_ids": snapshot.style_pack_ids,
        "resolved_packs": [pack.model_dump(mode="json") for pack in snapshot.resolved_packs],
        "presentation": presentation.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    assert snapshot.snapshot_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_receipt_accepts_normalized_custom_audience_and_exact_private_version():
    receipt = build_nexus_execution_receipt(
        pack_ids=["foundation.core"], snapshot=_snapshot(), presentation=None,
        audience_render={"seniority": "director", "decision_horizon": "immediate"},
        private_pack_receipts=[NexusPrivatePackReceipt(
            version_id=7, pack_id="analysis.supply-chain", version="1.0.0", content_hash="e" * 64,
            audience_render=NexusAudienceRender(seniority="director", decision_horizon="immediate"),
        )],
    )
    assert receipt["nexus_audience_render"] == {"seniority": "director", "decision_horizon": "immediate"}
    assert receipt["nexus_private_pack_receipts"][0]["content_hash"] == "e" * 64


def test_private_analysis_receipt_and_phase3_instructions_are_additive():
    artifact = NexusPrivateAnalysisArtifactV3.model_validate({
        "schema_version": 3,
        "purpose": "Assess supply-chain disruption exposure.",
        "analyses": [{"id": "risk", "label": "Disruption Risk", "purpose": "Assess supplier continuity."}],
        "evidence_requirements": [{"id": "supplier", "label": "Supplier Evidence", "required": True, "weight": 80}],
        "output_documents": [{"id": "cold-chain", "title": "Cold-Chain Readiness", "sections": [{"id": "controls", "heading": "Cold-Chain Controls", "order": 1}]}],
        "scoring_dimensions": [{"id": "continuity", "label": "Continuity", "weight": 60}],
        "audience": {"label": "Cold-chain procurement audience", "render": {"seniority": "manager", "detail_level": "detailed"}},
    })
    pack = NexusPrivateAnalysisPack.model_validate({
        "receipt": {
            "version_id": 7, "pack_id": "analysis.supply-chain", "version": "1.0.0",
            "content_hash": compute_nexus_private_artifact_hash(artifact),
            "audience_render": {"seniority": "manager", "detail_level": "detailed"},
        },
        "artifact": artifact.model_dump(mode="json"),
        "missing_optional_capability_ids": [],
    })
    receipt = build_nexus_execution_receipt(
        pack_ids=["foundation.core"], snapshot=_snapshot(), presentation=None,
        private_pack_receipts=[pack.receipt], private_analysis_packs=[pack],
    )
    instructions = private_analysis_instructions_for_document(pack, "cold-chain")

    assert receipt["nexus_private_analysis_packs"][0]["artifact"]["purpose"] == artifact.purpose
    assert "Cold-chain procurement audience" in instructions
    assert "Disruption Risk" in instructions
    assert "cannot replace certified or plugin Pack rules" in instructions
