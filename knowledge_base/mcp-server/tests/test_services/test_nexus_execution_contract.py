from __future__ import annotations

import hashlib
import json

import pytest

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.prompts import doc_prompt, group_analysis_prompt
from mcp_server.schemas import (
    NexusPackSnapshot,
    NexusPresentation,
    compute_nexus_snapshot_hash,
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
