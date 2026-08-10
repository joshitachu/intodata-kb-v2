from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.grounded_evidence_enrichment import (
    PROJECT_EVIDENCE_HANDOFF_NAME,
    PROJECT_EVIDENCE_INDEX_NAME,
    evidence_relative_suffix,
    has_supplemental_project_evidence,
    project_evidence_index,
    supplemental_project_evidence,
    validate_analysis_enrichment,
    validate_investigation_packets,
    validate_writing_enrichment,
)
from mcp_server.experiments.infrastructure.openai_agents.scoped_evidence import (
    ScopedGroundedEvidence,
)
from mcp_server.experiments.application.grounded_pack import load_grounded_execution_pack
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    GroundedRuntimeRunner,
)
from mcp_server.experiments.application.snapshot_project_evidence import (
    CreateProjectEvidenceSnapshotRequest,
    ProjectEvidenceSnapshotService,
)
from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.opencode.runtime import OpenCodeGroundedTaskBuilder
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    CapturedRawSource,
    SourceAdapterRegistry,
)


class _PreparedAdapter:
    id = "prepared-fixture"
    version = "1"

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind in {
            "directory_evidence",
            "runtime_export",
            "intodoc_scan",
        }

    def snapshot(
        self,
        descriptor: SourceDescriptor,
        _limits: SourceLimits,
    ) -> AdapterSnapshotResult:
        content = descriptor.source_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        logical_identity = f"fixture:{descriptor.id}"
        item_id = hashlib.sha256(logical_identity.encode()).hexdigest()
        item = SourceSnapshotItem(
            id=item_id,
            source_descriptor_id=descriptor.id,
            logical_identity=logical_identity,
            source_kind=descriptor.source_kind,
            source_role=descriptor.source_role,
            capabilities=descriptor.capabilities,
            locator=descriptor.provenance_locator,
            raw_sha256=digest,
            extracted_sha256=digest,
            media_type="text/plain",
            size_bytes=len(content),
            extracted_size_bytes=len(content),
            trust_level=descriptor.trust_level,
            redaction_state=RedactionState.APPROVED,
            adapter_id=self.id,
            adapter_version=self.version,
        )
        return AdapterSnapshotResult(
            items=(item,),
            contents=(
                SourceSnapshotContent(
                    item_id=item_id,
                    extracted_bytes=content,
                    extracted_sha256=digest,
                    media_type="text/plain",
                    size_bytes=len(content),
                ),
            ),
            raw_sources=(CapturedRawSource(logical_identity, digest, len(content)),),
        )


def _receipt(path: Path, content: bytes) -> Path:
    receipt = path.with_suffix(path.suffix + ".receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_files": [
                    {
                        "raw_sha256": hashlib.sha256(content).hexdigest(),
                        "size_bytes": len(content),
                    }
                ],
                "status": "approved",
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return receipt


def _descriptor(
    tmp_path: Path,
    *,
    identifier: str,
    source_kind: str,
    source_role: str,
    content: bytes,
) -> SourceDescriptor:
    path = tmp_path / f"{identifier}.txt"
    path.write_bytes(content)
    return SourceDescriptor(
        id=identifier,
        source_kind=source_kind,
        source_role=source_role,
        source_path=path,
        provenance_locator=f"fixture://{identifier}",
        capabilities=frozenset({"searchable_text"}),
        redaction_receipt_path=_receipt(path, content),
    )


def _snapshot(tmp_path: Path, *, scanner_only: bool = False):
    scanner = _descriptor(
        tmp_path,
        identifier="scanner",
        source_kind="intodoc_scan",
        source_role="implementation",
        content=b"scanner truth",
    )
    descriptors = [scanner]
    if not scanner_only:
        descriptors.extend(
            (
                _descriptor(
                    tmp_path,
                    identifier="runtime",
                    source_kind="runtime_export",
                    source_role="runtime_observation",
                    content=b"observed production behavior",
                ),
                _descriptor(
                    tmp_path,
                    identifier="policy",
                    source_kind="directory_evidence",
                    source_role="supporting_reference",
                    content=b"documented intended behavior",
                ),
                _descriptor(
                    tmp_path,
                    identifier="unavailable",
                    source_kind="unsupported_kind",
                    source_role="supporting_reference",
                    content=b"declared but unsupported",
                ),
            )
        )
    return ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((_PreparedAdapter(),))
    ).create(CreateProjectEvidenceSnapshotRequest(descriptors=tuple(descriptors)))


def _pack():
    recipe = (
        Path(__file__).parents[2]
        / "data"
        / "agentic_recipes"
        / "logistics_operations"
        / "recipe.yaml"
    )
    return load_grounded_execution_pack(load_recipe(recipe))


def _runner(tmp_path: Path) -> GroundedRuntimeRunner:
    return GroundedRuntimeRunner(
        runtime=FakeAgentRuntime(),
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        execution_pack=_pack(),
    )


def _ledger() -> dict[str, object]:
    return {
        "project": {"id": "project"},
        "canonical_units": [],
        "protected_facts": [
            {
                "fact_id": "project.job_count",
                "kind": "job_count",
                "subject": "project",
                "value": 1,
            }
        ],
    }


def _valid_packet(index: dict[str, object]) -> dict[str, object]:
    assigned = index["assigned_evidence"]
    assert isinstance(assigned, list)
    by_class = {str(item["claim_class"]): str(item["evidence_id"]) for item in assigned}
    declared = index["declared_sources"]
    assert isinstance(declared, list)
    declared_ids = [str(item["source_descriptor_id"]) for item in declared]
    unavailable_ids = [
        str(item["source_descriptor_id"])
        for item in declared
        if item["status"] != "readable"
    ]
    runtime_id = by_class["observed_runtime"]
    policy_id = by_class["intended_documented"]
    return {
        "schema_version": 1,
        "coverage": {
            "declared_source_ids": declared_ids,
            "unavailable_source_ids": unavailable_ids,
            "assigned_evidence_ids": [str(item["evidence_id"]) for item in assigned],
            "examined_evidence_ids": [str(item["evidence_id"]) for item in assigned],
            "missing_evidence_ids": [],
        },
        "claims": [
            {
                "claim_id": "runtime-claim",
                "claim_class": "observed_runtime",
                "statement": "Runtime differs from the scanner-defined implementation.",
                "evidence_ids": [runtime_id],
                "fact_ids": [],
            },
            {
                "claim_id": "policy-claim",
                "claim_class": "intended_documented",
                "statement": "The policy documents an intended behavior.",
                "evidence_ids": [policy_id],
                "fact_ids": [],
            },
            {
                "claim_id": "implemented-claim",
                "claim_class": "implemented",
                "statement": "The scanner records one implemented job.",
                "evidence_ids": [],
                "fact_ids": ["project.job_count"],
            },
        ],
        "agreements": [],
        "conflicts": [
            {
                "summary": "Observed behavior conflicts with implemented truth.",
                "evidence_ids": [runtime_id],
                "fact_ids": ["project.job_count"],
                "resolution": "scanner_authoritative",
            }
        ],
        "unknowns": [
            {
                "summary": "The declared unsupported source could not be examined.",
                "evidence_ids": [],
                "source_descriptor_ids": unavailable_ids,
            }
        ],
    }


def test_runner_stages_full_declared_inventory_but_only_supplemental_prepared_bytes(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    workspace = _runner(tmp_path)._prepare_workspace(
        experiment_id="enrichment-staging",
        source_items=(),
        docs_root=tmp_path / "docs",
        ledger=_ledger(),
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=snapshot,
    )
    evidence_input = workspace / "roles" / "shipment-evidence" / "input"
    index = json.loads((evidence_input / PROJECT_EVIDENCE_INDEX_NAME).read_bytes())

    assert {item["source_descriptor_id"]: item["status"] for item in index["declared_sources"]} == {
        "policy": "readable",
        "runtime": "readable",
        "scanner": "readable",
        "unavailable": "unsupported",
    }
    assert len(index["assigned_evidence"]) == 2
    # Staged supplemental evidence must never be a ``.bin`` file: the OpenCode
    # built-in read tool refuses ``.bin`` as binary. text/plain prepared
    # evidence stages under a ``.txt`` suffix a line-based reader will decode.
    assert not list((evidence_input / "project_evidence" / "items").rglob("*.bin"))
    staged = sorted((evidence_input / "project_evidence" / "items").rglob("*.txt"))
    assert {path.read_bytes() for path in staged} == {
        b"documented intended behavior",
        b"observed production behavior",
    }
    assert not (workspace / "roles" / "operations-analysis" / "input" / "project_evidence").exists()
    assert (
        workspace / "roles" / "operations-analysis" / "input" / PROJECT_EVIDENCE_HANDOFF_NAME
    ).is_file()


def test_closed_enrichment_contracts_keep_scanner_authoritative_and_render_conflicts(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    assigned = supplemental_project_evidence(snapshot)
    index = project_evidence_index(snapshot, assigned)
    packet = _valid_packet(index)
    summary = validate_investigation_packets(
        {"packet.json": packet},
        index=index,
        fact_ledger=_ledger(),
    )
    assert summary["missing_evidence_ids"] == []
    ids = {str(item["claim_class"]): str(item["evidence_id"]) for item in index["assigned_evidence"]}
    runtime_id = ids["observed_runtime"]
    policy_id = ids["intended_documented"]
    analysis = "\n".join(
        (
            "# Analysis",
            "## Implemented Truth",
            "Scanner truth remains authoritative [fact:project.job_count].",
            "## Observed Runtime",
            f"Observed behavior [evidence:{runtime_id}].",
            "## Intended or Documented",
            f"Documented intent [evidence:{policy_id}].",
            "## Agreements",
            "No supported agreement was found.",
            "## Conflicts",
            f"Scanner authoritative for the conflict [evidence:{runtime_id}].",
            "## Unknowns",
            "Unavailable source is explicit [source:unavailable].",
        )
    )
    validate_analysis_enrichment(
        {"analysis.md": analysis},
        investigation_packets={"packet.json": packet},
    )

    with pytest.raises(ValueError, match="Evidence Discrepancies"):
        validate_writing_enrichment(
            {"operations.md": "# Operations\n\nExisting required content.\n"},
            analyses={"analysis.md": analysis},
        )
    validate_writing_enrichment(
        {
            "operations.md": (
                "# Operations\n\nExisting required content.\n\n"
                "## Evidence Discrepancies\n"
                f"Observed behavior differs; scanner truth is unchanged [evidence:{runtime_id}].\n"
            )
        },
        analyses={"analysis.md": analysis},
    )

    override = deepcopy(packet)
    override["conflicts"][0]["resolution"] = "unresolved"
    with pytest.raises(ValueError, match="cannot override"):
        validate_investigation_packets(
            {"packet.json": override},
            index=index,
            fact_ledger=_ledger(),
        )


def test_non_scanner_implementation_role_is_forced_to_supplemental_intent(
    tmp_path: Path,
) -> None:
    disguised = _descriptor(
        tmp_path,
        identifier="disguised-implementation",
        source_kind="directory_evidence",
        source_role="implementation",
        content=b"caller-controlled supporting evidence",
    )
    snapshot = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((_PreparedAdapter(),))
    ).create(CreateProjectEvidenceSnapshotRequest(descriptors=(disguised,)))

    assert has_supplemental_project_evidence(snapshot)
    supplemental = supplemental_project_evidence(snapshot)
    assert len(supplemental) == 1
    assert supplemental[0].claim_class == "intended_documented"
    assert supplemental[0].relative_path.startswith("project_evidence/items/")
    assert supplemental[0].relative_path.count("/") == 3


def test_writing_rejects_uncited_or_non_additive_supplemental_assertions() -> None:
    evidence_id = "a" * 64
    analysis = "\n".join(
        (
            "# Analysis",
            "## Implemented Truth",
            "Scanner truth [fact:project.job_count].",
            "## Observed Runtime",
            f"Observed behavior [evidence:{evidence_id}].",
            "## Intended or Documented",
            "No intended claim.",
            "## Agreements",
            "No agreement.",
            "## Conflicts",
            "No conflict.",
            "## Unknowns",
            "No unknown.",
        )
    )
    with pytest.raises(ValueError, match="uncited supplemental assertion"):
        validate_writing_enrichment(
            {
                "operations.md": (
                    "# Operations\n\n## Supplemental Evidence\n"
                    "Runtime evidence says every shipment is deleted automatically.\n"
                )
            },
            analyses={"analysis.md": analysis},
        )
    with pytest.raises(ValueError, match="additive evidence sections"):
        validate_writing_enrichment(
            {
                "operations.md": (
                    "# Operations\n\n## Existing Section\n"
                    f"Observed behavior [evidence:{evidence_id}].\n"
                )
            },
            analyses={"analysis.md": analysis},
        )
    validate_writing_enrichment(
        {
            "operations.md": (
                "# Operations\n\n## Supplemental Evidence\n"
                f"Observed behavior [evidence:{evidence_id}].\n"
            )
        },
        analyses={"analysis.md": analysis},
    )


def test_missing_declared_evidence_must_be_explicit_and_contract_is_closed(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    index = project_evidence_index(snapshot, supplemental_project_evidence(snapshot))
    packet = _valid_packet(index)
    packet["unknowns"] = []
    with pytest.raises(ValueError, match="omit unavailable"):
        validate_investigation_packets(
            {"packet.json": packet},
            index=index,
            fact_ledger=_ledger(),
        )
    # A model-added extra field alongside every required field is not a
    # content defect (verified in production: a role added harmless
    # self-descriptive `packet_role`/`owned_artifact` fields) and must not
    # fail the role -- only a genuinely missing required field must.
    packet = _valid_packet(index)
    packet["packet_role"] = "evidence-structure"
    packet["owned_artifact"] = "candidate/orchestration/evidence_packets/structure.json"
    validate_investigation_packets(
        {"packet.json": packet},
        index=index,
        fact_ledger=_ledger(),
    )
    packet = _valid_packet(index)
    del packet["claims"]
    with pytest.raises(ValueError, match="missing required fields"):
        validate_investigation_packets(
            {"packet.json": packet},
            index=index,
            fact_ledger=_ledger(),
        )


def test_deterministic_orchestration_retains_supplemental_coverage_and_packet_locators(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    index = project_evidence_index(snapshot, supplemental_project_evidence(snapshot))
    packet = _valid_packet(index)
    workspace = tmp_path / "workspace"
    packet_path = (
        workspace
        / "candidate"
        / "orchestration"
        / "evidence_packets"
        / "shipments.json"
    )
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    _runner(tmp_path)._refresh_deterministic_orchestration(
        workspace,
        _ledger(),
        {"candidate/orchestration/evidence_packets/shipments.json": packet_path},
        ("operations_runbook.md", "exception_inventory.md"),
    )

    registry = json.loads(
        (workspace / "candidate" / "orchestration" / "evidence_registry.json").read_bytes()
    )
    supplemental_claim = next(
        item for item in registry["claims"] if item["confidence"] == "supplemental"
    )
    assert supplemental_claim["evidence_locators"] == [
        "candidate/orchestration/evidence_packets/shipments.json"
    ]
    coverage = json.loads(
        (workspace / "candidate" / "orchestration" / "coverage_ledger.json").read_bytes()
    )
    assert "supplemental_evidence" in coverage["concerns"]
    assert coverage["gaps"] == ["source:unavailable"]


def test_scanner_only_snapshot_preserves_talend_only_packet_compatibility(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path, scanner_only=True)
    assert supplemental_project_evidence(snapshot) == ()
    workspace = _runner(tmp_path)._prepare_workspace(
        experiment_id="scanner-only-staging",
        source_items=(),
        docs_root=tmp_path / "docs",
        ledger=_ledger(),
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=snapshot,
    )
    assert not list(workspace.rglob(PROJECT_EVIDENCE_INDEX_NAME))
    assert not list(workspace.rglob(PROJECT_EVIDENCE_HANDOFF_NAME))
    assert not list(workspace.rglob("project_evidence"))


def test_declared_unavailable_supplemental_source_is_staged_without_searchable_bytes(
    tmp_path: Path,
) -> None:
    scanner = _descriptor(
        tmp_path,
        identifier="scanner",
        source_kind="intodoc_scan",
        source_role="implementation",
        content=b"scanner truth",
    )
    unavailable = _descriptor(
        tmp_path,
        identifier="unavailable",
        source_kind="unsupported_kind",
        source_role="supporting_reference",
        content=b"declared unavailable source",
    )
    snapshot = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((_PreparedAdapter(),))
    ).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(scanner, unavailable))
    )
    assert has_supplemental_project_evidence(snapshot)
    assert supplemental_project_evidence(snapshot) == ()

    workspace = _runner(tmp_path)._prepare_workspace(
        experiment_id="unavailable-only-staging",
        source_items=(),
        docs_root=tmp_path / "docs",
        ledger=_ledger(),
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=snapshot,
    )
    evidence_input = workspace / "roles" / "shipment-evidence" / "input"
    index = json.loads((evidence_input / PROJECT_EVIDENCE_INDEX_NAME).read_bytes())
    declared = {item["source_descriptor_id"]: item for item in index["declared_sources"]}
    assert declared["unavailable"]["status"] == "unsupported"
    assert declared["unavailable"]["status_reason"] == "no_registered_adapter"
    assert index["assigned_evidence"] == []
    assert not (evidence_input / "project_evidence" / "items").exists()


def test_evidence_relative_suffix_maps_text_types_and_never_bin() -> None:
    assert evidence_relative_suffix("text/plain; charset=utf-8") == ".txt"
    assert evidence_relative_suffix("application/json") == ".json"
    assert evidence_relative_suffix("application/vnd.api+json") == ".json"
    assert evidence_relative_suffix("text/markdown") == ".md"
    assert evidence_relative_suffix("text/csv") == ".csv"
    assert evidence_relative_suffix("application/xml") == ".xml"
    assert evidence_relative_suffix("image/svg+xml") == ".xml"
    # An unrecognized declared type still stages as decodable text, never .bin.
    assert evidence_relative_suffix("application/octet-stream") == ".txt"


def test_staged_supplemental_evidence_is_read_as_decoded_text_not_refused(
    tmp_path: Path,
) -> None:
    """A role reader must return the decoded content of staged evidence.

    Reproduces the werkorder depth loss at the read boundary: prepared
    supplemental evidence was staged as ``.bin`` and the runtime read tool
    refused it as binary, so no role saw a byte of content. With the fix each
    item stages under a text suffix and a reader over the exact staged packet
    returns the original decoded bytes with a text media type.
    """

    snapshot = _snapshot(tmp_path)
    workspace = _runner(tmp_path)._prepare_workspace(
        experiment_id="read-decoded-staging",
        source_items=(),
        docs_root=tmp_path / "docs",
        ledger=_ledger(),
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=snapshot,
    )
    packet_root = workspace / "roles" / "shipment-evidence"
    items_root = packet_root / "input" / "project_evidence" / "items"

    # No staged supplemental item may be a .bin file (the refused case).
    assert not list(items_root.rglob("*.bin"))
    staged = sorted(items_root.rglob("*"))
    assert staged and all(path.suffix == ".txt" for path in staged if path.is_file())

    # A real reader over the exact staged layout returns decoded content.
    evidence = ScopedGroundedEvidence.from_packet(
        packet_root, max_context_bytes=10_000_000
    )
    supplemental_items = [
        item
        for item in evidence.items
        if item.logical_name.startswith("input/project_evidence/items/")
    ]
    assert len(supplemental_items) == 2
    decoded = set()
    for item in supplemental_items:
        assert item.media_type == "text/plain"
        result = evidence.read(evidence_id=item.evidence_id, max_bytes=4096)
        assert result["returned_bytes"] > 0
        assert result["content"]
        decoded.add(result["content"])
    assert decoded == {"documented intended behavior", "observed production behavior"}
