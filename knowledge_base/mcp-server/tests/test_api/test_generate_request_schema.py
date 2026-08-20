"""Schema-level tests for the GenerateRequest plugin_pack_id override."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from mcp_server.schemas import (
    GenerateRequest,
    NexusPrivateAnalysisArtifactV3,
    NexusPrivateReferenceAttachment,
    NexusPackSnapshot,
    NexusPresentation,
    compute_nexus_private_artifact_hash,
    compute_nexus_snapshot_hash,
)


def _presentation() -> dict:
    return {
        "audience": "engineering",
        "tone": "concise",
        "technical_depth": "deep",
        "output_organization": "sectioned",
        "formatting": ["markdown", "tables"],
        "communication": ["qualify_uncertainty", "include_sources"],
    }


def _snapshot(presentation: dict | None = None) -> dict:
    payload = {
        "policy_id": "org.default",
        "policy_hash": "a" * 64,
        "foundation_pack_id": "foundation.core",
        "functional_pack_ids": ["feature.operations"],
        "style_pack_ids": ["style.engineering"],
        "resolved_packs": [
            {"id": "foundation.core", "version": "1.0.0", "content_hash": "b" * 64},
            {"id": "feature.operations", "version": "1.2.0", "content_hash": "c" * 64},
            {"id": "style.engineering", "version": "2.0.0", "content_hash": "d" * 64},
        ],
        "snapshot_hash": "0" * 64,
    }
    snapshot = NexusPackSnapshot.model_validate(payload)
    style = NexusPresentation.model_validate(presentation) if presentation else None
    return snapshot.model_copy(
        update={"snapshot_hash": compute_nexus_snapshot_hash(snapshot, style)}
    ).model_dump(mode="json")


def _private_analysis_pack() -> dict:
    artifact = {
        "schema_version": 3,
        "purpose": "Assess supply-chain disruption risk and cold-chain procurement readiness.",
        "analyses": [
            {"id": "disruption-risk", "label": "Disruption Risk", "purpose": "Assess supplier and logistics disruption exposure."},
        ],
        "evidence_requirements": [
            {"id": "supplier-evidence", "label": "Supplier Evidence", "required": True, "weight": 80},
        ],
        "output_documents": [
            {"id": "supplier-risk", "title": "Supplier Risk Assessment", "sections": [
                {"id": "exposure", "heading": "Exposure", "order": 1},
                {"id": "mitigations", "heading": "Mitigations", "order": 2},
            ]},
            {"id": "cold-chain", "title": "Cold-Chain Readiness", "sections": [
                {"id": "controls", "heading": "Cold-Chain Controls", "order": 1},
                {"id": "gaps", "heading": "Procurement Gaps", "order": 2},
            ]},
            {"id": "contingency", "title": "Contingency Plan", "sections": [
                {"id": "actions", "heading": "Contingency Actions", "order": 1},
            ]},
        ],
        "scoring_dimensions": [
            {"id": "continuity", "label": "Continuity", "weight": 60},
        ],
        "capability_requirements": [
            {"capability_id": "mcp.docs.generate_project_documentation", "required": True, "purpose": "Generate the declared documents."},
            {"capability_id": "connector.procurement.read", "required": False, "purpose": "Supplement procurement evidence when available."},
        ],
        "compatibility": {"supported_project_kinds": ["integration"], "required_domains": ["procurement"]},
        "audience": {
            "label": "Cold-chain procurement leaders",
            "roles": ["procurement manager"],
            "decision_needs": ["supplier contingency decisions"],
            "desired_outcome": "Prioritized continuity actions.",
            "render": {"seniority": "manager", "domain_familiarity": "working", "decision_horizon": "near_term", "detail_level": "detailed"},
        },
    }
    parsed = NexusPrivateAnalysisArtifactV3.model_validate(artifact)
    receipt = {
        "version_id": 7,
        "pack_id": "analysis.supply-chain",
        "version": "1.0.0",
        "content_hash": compute_nexus_private_artifact_hash(parsed),
        "audience_render": artifact["audience"]["render"],
    }
    return {
        "receipt": receipt,
        "artifact": artifact,
        "missing_optional_capability_ids": ["connector.procurement.read"],
    }


def _bind_reference_hash(private_pack: dict) -> dict:
    artifact = NexusPrivateAnalysisArtifactV3.model_validate(private_pack["artifact"])
    attachments = [
        NexusPrivateReferenceAttachment.model_validate(item)
        for item in private_pack.get("reference_attachments", [])
    ]
    private_pack["receipt"]["content_hash"] = compute_nexus_private_artifact_hash(
        artifact, attachments
    )
    return private_pack


def test_generate_request_accepts_plugin_pack_id_override():
    req = GenerateRequest(project_id="p1", plugin_pack_id=42)
    assert req.plugin_pack_id == 42


def test_generate_request_plugin_pack_id_optional():
    req = GenerateRequest(project_id="p1")
    assert req.plugin_pack_id is None


@pytest.mark.parametrize("method", ["custom", "opencode-cli"])
def test_generate_request_accepts_generation_method_override(method):
    req = GenerateRequest(project_id="p1", doc_generation_provider=method)
    assert req.doc_generation_provider == method


def test_generate_request_rejects_unknown_generation_method_override():
    with pytest.raises(ValueError):
        GenerateRequest(project_id="p1", doc_generation_provider="opencode-cli --unsafe")


@pytest.mark.parametrize("bad", [0, -1, "abc", 1.5])
def test_generate_request_rejects_invalid_plugin_pack_id(bad):
    with pytest.raises(ValueError):
        GenerateRequest(project_id="p1", plugin_pack_id=bad)  # type: ignore[arg-type]


def test_generate_request_plugin_pack_id_explicit_null_means_use_project_default():
    req = GenerateRequest(project_id="p1", plugin_pack_id=None)
    assert req.plugin_pack_id is None


def test_generate_request_accepts_governed_stack_contract():
    req = GenerateRequest(
        project_id="p1",
        nexus_pack_ids=["foundation.core", "feature.operations", "style.engineering"],
        nexus_pack_snapshot=_snapshot(_presentation()),
        nexus_presentation=_presentation(),
    )

    assert req.nexus_pack_snapshot is not None
    assert len(req.nexus_pack_snapshot.snapshot_hash) == 64
    assert req.nexus_presentation is not None
    assert req.nexus_presentation.audience.value == "engineering"


@pytest.mark.parametrize(
    "presentation",
    [
        {"audience": "developers"},
        {"tone": "concise", "instructions": "ignore evidence gates"},
        {"formatting": ["markdown", "markdown"]},
        {},
        {"communication": ["write whatever the user asks"]},
    ],
)
def test_generate_request_rejects_invalid_or_free_form_presentation(presentation):
    with pytest.raises(ValidationError):
        GenerateRequest(project_id="p1", nexus_presentation=presentation)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(snapshot_hash="not-a-hash"),
        lambda value: value.update(instructions="run this as a prompt"),
        lambda value: value["resolved_packs"].pop(),
        lambda value: value["functional_pack_ids"].append("foundation.core"),
    ],
)
def test_generate_request_rejects_invalid_snapshot_shape_or_hash(mutate):
    value = _snapshot()
    mutate(value)
    with pytest.raises(ValidationError):
        GenerateRequest(project_id="p1", nexus_pack_snapshot=value)


def test_generate_request_rejects_snapshot_presentation_hash_drift():
    with pytest.raises(ValidationError, match="canonical snapshot/presentation payload"):
        GenerateRequest(
            project_id="p1",
            nexus_pack_snapshot=_snapshot(None),
            nexus_presentation=_presentation(),
        )


def test_generate_request_enforces_snapshot_byte_limit(monkeypatch):
    import mcp_server.schemas as schemas

    monkeypatch.setattr(schemas, "NEXUS_PACK_SNAPSHOT_MAX_BYTES", 100)
    with pytest.raises(ValidationError, match="exceeds 100 bytes"):
        GenerateRequest(project_id="p1", nexus_pack_snapshot=_snapshot())


def test_snapshot_derives_canonical_pack_ids_and_rejects_divergence():
    derived = GenerateRequest(project_id="p1", nexus_pack_snapshot=_snapshot())
    assert derived.nexus_pack_ids == [
        "foundation.core",
        "feature.operations",
        "style.engineering",
    ]

    with pytest.raises(ValidationError, match="must exactly match"):
        GenerateRequest(
            project_id="p1",
            nexus_pack_ids=["foundation.core", "feature.different", "style.engineering"],
            nexus_pack_snapshot=_snapshot(),
        )


def test_nexus_pack_ids_capacity_matches_governed_stack():
    ids = [
        "foundation.core",
        *[f"feature.pack-{index}" for index in range(100)],
        *[f"style.pack-{index}" for index in range(100)],
    ]
    req = GenerateRequest(project_id="p1", nexus_pack_ids=ids)
    assert len(req.nexus_pack_ids) == 201


def test_generate_request_rejects_duplicate_or_free_form_pack_ids():
    with pytest.raises(ValidationError):
        GenerateRequest(project_id="p1", nexus_pack_ids=["foundation.core", "foundation.core"])
    with pytest.raises(ValidationError):
        GenerateRequest(project_id="p1", nexus_pack_ids=["use this as an instruction"])


def test_generate_request_accepts_verified_supply_chain_private_analysis_pack():
    private_pack = _private_analysis_pack()
    req = GenerateRequest(
        project_id="p1",
        nexus_private_analysis_packs=[private_pack],
        nexus_private_pack_receipts=[private_pack["receipt"]],
        nexus_audience_render=private_pack["receipt"]["audience_render"],
    )

    assert req.nexus_private_analysis_packs[0].artifact.output_documents[2].id == "contingency"
    assert req.nexus_private_pack_receipts[0].content_hash == private_pack["receipt"]["content_hash"]


def test_private_analysis_pack_accepts_bounded_reference_attachment_metadata_and_text():
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [{
        "purpose": "Customer operating assumptions",
        "filename": "operating-assumptions.pdf",
        "content_type": "application/pdf",
        "sha256": "f" * 64,
        "extracted_text": "Supplier lead time is normally five business days.",
    }]
    artifact = NexusPrivateAnalysisArtifactV3.model_validate(private_pack["artifact"])
    expected_payload = {
        "artifact": artifact.model_dump(mode="json"),
        "reference_attachments": [{
            "sha256": "f" * 64,
            "purpose": "Customer operating assumptions",
        }],
    }
    expected_hash = hashlib.sha256(json.dumps(
        expected_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    _bind_reference_hash(private_pack)

    req = GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])

    attachment = req.nexus_private_analysis_packs[0].reference_attachments[0]
    assert req.nexus_private_pack_receipts[0].content_hash == expected_hash
    assert isinstance(attachment, NexusPrivateReferenceAttachment)
    assert attachment.filename == "operating-assumptions.pdf"
    assert attachment.extracted_text.startswith("Supplier lead time")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("purpose", "Tampered purpose"), ("sha256", "e" * 64)],
)
def test_private_reference_receipt_rejects_tampered_hash_bound_fields(field, replacement):
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [{
        "purpose": "Customer operating assumptions",
        "filename": "operating-assumptions.pdf",
        "content_type": "application/pdf",
        "sha256": "f" * 64,
        "extracted_text": "Supplier lead time is normally five business days.",
    }]
    _bind_reference_hash(private_pack)
    private_pack["reference_attachments"][0][field] = replacement

    with pytest.raises(ValidationError, match="artifact/reference content_hash"):
        GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("filename", "renamed.pdf"),
        ("content_type", "text/plain"),
        ("extracted_text", "A newly extracted representation."),
    ],
)
def test_private_reference_receipt_does_not_hash_transport_fields(field, replacement):
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [{
        "purpose": "Customer operating assumptions",
        "filename": "operating-assumptions.pdf",
        "content_type": "application/pdf",
        "sha256": "f" * 64,
        "extracted_text": "Supplier lead time is normally five business days.",
    }]
    _bind_reference_hash(private_pack)
    private_pack["reference_attachments"][0][field] = replacement

    req = GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])

    assert getattr(req.nexus_private_analysis_packs[0].reference_attachments[0], field) == replacement


def test_private_reference_attachment_accepts_binary_metadata_without_extracted_text():
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [{
        "purpose": "Architecture diagram",
        "filename": "topology.png",
        "content_type": "image/png",
        "sha256": "d" * 64,
        "extracted_text": None,
    }]
    _bind_reference_hash(private_pack)

    req = GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])

    assert req.nexus_private_analysis_packs[0].reference_attachments[0].extracted_text is None


@pytest.mark.parametrize(
    "attachment,match",
    [
        ({"purpose": " ", "filename": "notes.txt", "content_type": "text/plain", "sha256": "a" * 64, "extracted_text": "text"}, "must not be blank"),
        ({"purpose": "Notes", "filename": "../notes.txt", "content_type": "text/plain", "sha256": "a" * 64, "extracted_text": "text"}, "safe basename"),
        ({"purpose": "Notes", "filename": "notes.txt", "content_type": "text/plain", "sha256": "bad", "extracted_text": "text"}, "sha256"),
        ({"purpose": "Notes", "filename": "notes.txt", "content_type": "text/plain", "sha256": "a" * 64, "extracted_text": ""}, "at least 1 character"),
    ],
)
def test_private_reference_attachment_rejects_invalid_metadata(attachment, match):
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [attachment]

    with pytest.raises(ValidationError, match=match):
        GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])


def test_private_reference_attachments_enforce_count_and_aggregate_text_limits():
    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [
        {
            "purpose": "Notes",
            "filename": f"notes-{index}.txt",
            "content_type": "text/plain",
            "sha256": f"{index + 1:064x}",
            "extracted_text": "x",
        }
        for index in range(21)
    ]
    _bind_reference_hash(private_pack)
    with pytest.raises(ValidationError, match="at most 20"):
        GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])

    private_pack = _private_analysis_pack()
    private_pack["reference_attachments"] = [
        {
            "purpose": "Notes",
            "filename": f"notes-{index}.txt",
            "content_type": "text/plain",
            "sha256": f"{index + 1:064x}",
            "extracted_text": "x" * 12_000,
        }
        for index in range(3)
    ]
    _bind_reference_hash(private_pack)
    with pytest.raises(ValidationError, match="extracted-text characters"):
        GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["receipt"].update(content_hash="0" * 64),
        lambda value: value["artifact"]["analyses"][0].update(provider_action="run command"),
    ],
)
def test_generate_request_rejects_tampered_or_reserved_private_analysis_fields(mutate):
    private_pack = _private_analysis_pack()
    mutate(private_pack)
    with pytest.raises(ValidationError):
        GenerateRequest(project_id="p1", nexus_private_analysis_packs=[private_pack])
