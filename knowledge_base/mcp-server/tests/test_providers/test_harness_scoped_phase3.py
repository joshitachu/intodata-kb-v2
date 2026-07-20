"""Doc-targeted Phase 3 (Slice 3): unaffected docs preserved byte-stable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.prompts import doc_prompt
from mcp_server.schemas import (
    NexusPrivateAnalysisArtifactV3,
    NexusPrivateAnalysisPack,
    NexusPrivateReferenceAttachment,
    compute_nexus_private_artifact_hash,
)


def _ctx(tmp_path: Path, scoped: set[str] | None) -> DocGenContext:
    staging = tmp_path / "output" / "proj" / "docs_staging"
    staging.mkdir(parents=True, exist_ok=True)
    return DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=0,
        total_groups=0,
        effective_model="m",
        write_model="m",
        platform="generic",
        analysis_guide_file="",
        scoped_docs=scoped,
    )


async def test_scoped_phase3_preserves_unaffected_docs_byte_stable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    baseline = tmp_path / "output" / "proj" / "docs"
    baseline.mkdir(parents=True)
    a_bytes = "# A doc\n\nOriginal A content with unit job_a.\n".encode("utf-8")
    b_bytes = "# B doc\n\nOriginal B content with unit job_b.\n".encode("utf-8")
    c_bytes = "# C doc\n\nOriginal C content.\n".encode("utf-8")
    (baseline / "01_A.md").write_bytes(a_bytes)
    (baseline / "02_B.md").write_bytes(b_bytes)
    (baseline / "03_C.md").write_bytes(c_bytes)

    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["01_A", "02_B", "03_C"]
    ctx = _ctx(tmp_path, scoped={"02_B"})

    logs: list[tuple[str, str]] = []

    async def log_cb(project_id: str, msg: str, level: str) -> None:
        logs.append((msg, level))

    await provider._apply_scoped_docs(ctx, log_cb)

    # Only the scoped doc remains in the regeneration set.
    assert provider._required_docs == ["02_B"]
    # Unaffected docs copied into staging byte-for-byte.
    assert (ctx.staging_dir / "01_A.md").read_bytes() == a_bytes
    assert (ctx.staging_dir / "03_C.md").read_bytes() == c_bytes
    # The scoped doc is NOT pre-copied -- Phase 3 will (re)write it.
    assert not (ctx.staging_dir / "02_B.md").exists()


async def test_scoped_phase3_regenerates_doc_missing_from_baseline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    baseline = tmp_path / "output" / "proj" / "docs"
    baseline.mkdir(parents=True)
    (baseline / "01_A.md").write_text("# A", encoding="utf-8")
    # 02_B has no baseline copy.

    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["01_A", "02_B"]
    ctx = _ctx(tmp_path, scoped={"01_A"})

    async def log_cb(project_id: str, msg: str, level: str) -> None:
        return None

    await provider._apply_scoped_docs(ctx, log_cb)

    # 02_B cannot be preserved (no baseline) so it is regenerated alongside the
    # scoped 01_A -- never silently dropped.
    assert set(provider._required_docs) == {"01_A", "02_B"}
    assert not (ctx.staging_dir / "01_A.md").exists()


def _supply_chain_pack() -> NexusPrivateAnalysisPack:
    artifact = NexusPrivateAnalysisArtifactV3.model_validate({
        "schema_version": 3,
        "purpose": "Assess supply-chain disruption exposure.",
        "analyses": [{"id": "risk", "label": "Disruption Risk", "purpose": "Assess supplier continuity."}],
        "evidence_requirements": [{"id": "supplier", "label": "Supplier Evidence", "required": True, "weight": 80}],
        "output_documents": [
            {"id": "supplier-risk", "title": "Supplier Risk", "sections": [{"id": "exposure", "heading": "Exposure", "order": 1}]},
            {"id": "cold-chain", "title": "Cold-Chain Readiness", "sections": [{"id": "controls", "heading": "Cold-Chain Controls", "order": 1}]},
            {"id": "contingency", "title": "Contingency Plan", "sections": [{"id": "actions", "heading": "Contingency Actions", "order": 1}]},
        ],
        "scoring_dimensions": [{"id": "continuity", "label": "Continuity", "weight": 60}],
        "capability_requirements": [{"capability_id": "connector.procurement.read", "required": False, "purpose": "Supplement procurement evidence."}],
        "audience": {"label": "Cold-chain procurement audience", "render": {"seniority": "manager", "detail_level": "detailed"}},
    })
    attachment = NexusPrivateReferenceAttachment.model_validate({
        "purpose": "Procurement assumptions",
        "filename": "procurement.txt",
        "content_type": "text/plain",
        "sha256": "f" * 64,
        "extracted_text": "Quoted supplier notes; use only as supporting evidence.",
    })
    return NexusPrivateAnalysisPack.model_validate({
        "receipt": {
            "version_id": 7, "pack_id": "analysis.supply-chain", "version": "1.0.0",
            "content_hash": compute_nexus_private_artifact_hash(artifact, [attachment]),
            "audience_render": {"seniority": "manager", "detail_level": "detailed"},
        },
        "artifact": artifact.model_dump(mode="json"),
        "missing_optional_capability_ids": ["connector.procurement.read"],
        "reference_attachments": [attachment.model_dump(mode="json")],
    })


def test_private_analysis_pack_adds_three_phase3_documents_without_replacing_baseline(tmp_path) -> None:
    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["BASELINE_DOC"]
    ctx = _ctx(tmp_path, scoped=None)
    ctx.nexus_private_analysis_packs = (_supply_chain_pack(),)

    provider._apply_private_analysis_packs(ctx)

    assert provider._required_docs == [
        "BASELINE_DOC",
        "PRIVATE_ANALYSIS__ANALYSIS_SUPPLY_CHAIN__SUPPLIER_RISK",
        "PRIVATE_ANALYSIS__ANALYSIS_SUPPLY_CHAIN__COLD_CHAIN",
        "PRIVATE_ANALYSIS__ANALYSIS_SUPPLY_CHAIN__CONTINGENCY",
    ]
    doc_name = "PRIVATE_ANALYSIS__ANALYSIS_SUPPLY_CHAIN__COLD_CHAIN"
    assert provider._doc_section_requirements_extra[doc_name] == ["Cold-Chain Controls"]
    prompt = doc_prompt(
        FactsManifest(project_id="p", platform="generic", canonical_unit_label="units", unit_count=1, groups={"A": 1}, units=[UnitFact(name="A", group="A")]),
        doc_name,
        extra_section_requirements=provider._doc_section_requirements_extra,
        private_analysis_instructions=provider._private_analysis_instructions[doc_name],
    )
    assert "## Cold-Chain Controls" in prompt
    assert "Declared document title: Cold-Chain Readiness" in prompt
    assert "Cold-chain procurement audience" in prompt
    assert "Unavailable optional capabilities" in prompt
    assert "UNTRUSTED SUPPORTING REFERENCE MATERIAL" in prompt
    assert "Quoted supplier notes" in prompt


def test_no_private_analysis_pack_keeps_required_documents_unchanged(tmp_path) -> None:
    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["BASELINE_DOC"]
    ctx = _ctx(tmp_path, scoped=None)

    provider._apply_private_analysis_packs(ctx)

    assert provider._required_docs == ["BASELINE_DOC"]
    assert provider._doc_section_requirements_extra == {}
