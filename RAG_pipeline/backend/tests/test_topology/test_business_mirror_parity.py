"""The lenient business-topology mirror declares every field of the MCP contract.

The mirror ignores unknown keys by design, which is exactly why drift here is
silent: a field MCP publishes and this package does not declare is dropped at
parse time, and every assistant answer then reads it as "not established".

The MCP contract is read as SOURCE (AST), not imported, because the two
services have separate environments. The check skips when the contract file is
absent (a split checkout).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.topology import models as mirror

CONTRACT = (
    Path(__file__).resolve().parents[4]
    / "knowledge_base"
    / "mcp-server"
    / "src"
    / "mcp_server"
    / "services"
    / "business_topology"
    / "models.py"
)

#: MCP class name -> mirror class name.
CLASS_MAP = {
    "BusinessSystem": "BusinessSystem",
    "IntegrationSolution": "BusinessIntegrationSolution",
    "BusinessObject": "BusinessObject",
    "DeliverySegment": "BusinessDeliverySegment",
    "DeliveryPath": "BusinessDeliveryPath",
    "ContractWidth": "BusinessContractWidth",
    "StreamCondition": "BusinessStreamCondition",
    "DeploymentAgreement": "BusinessDeploymentAgreement",
    "BusinessDataStream": "BusinessDataStream",
    "TransformationMapping": "BusinessTransformationMapping",
    "BusinessTransformation": "BusinessTransformation",
    "BusinessRule": "BusinessRule",
    "BusinessControl": "BusinessControl",
    "BusinessRisk": "BusinessRisk",
    "BusinessUnknown": "BusinessUnknown",
    "RuntimeMetric": "BusinessRuntimeMetric",
    "RuntimeObservation": "BusinessRuntimeObservation",
    "BusinessConflict": "BusinessConflict",
    "ArtifactAnnotation": "BusinessArtifactAnnotation",
    "ComponentAnnotation": "BusinessComponentAnnotation",
    "LineageAnnotation": "BusinessLineageAnnotation",
    "BusinessTopologyCoverage": "BusinessCoverage",
    "BusinessTopology": "BusinessTopology",
}

#: Fields the mirror omits ON PURPOSE (see the `src/topology/models.py` module
#: docstring): values most likely to embed an address or a credential.
DELIBERATELY_OMITTED = {("DeliverySegment", "resource")}

#: Contract classes that are not claim shapes and have no mirror class.
NOT_MIRRORED = {"StrictModel", "EvidenceBackedClaim", "BusinessTopologyMetadata"}


def _contract_fields() -> dict[str, set[str]]:
    tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
    own: dict[str, set[str]] = {}
    bases: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases[node.name] = [base.id for base in node.bases if isinstance(base, ast.Name)]
        own[node.name] = {
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            and item.target.id != "model_config"
        }

    def resolved(name: str) -> set[str]:
        fields = set(own.get(name, set()))
        for base in bases.get(name, []):
            fields |= resolved(base)
        return fields

    return {name: resolved(name) for name in own}


pytestmark = pytest.mark.skipif(not CONTRACT.is_file(), reason="MCP contract not in this checkout")


def test_every_contract_class_has_a_mirror():
    contract = _contract_fields()
    unmapped = sorted(set(contract) - set(CLASS_MAP) - NOT_MIRRORED)
    assert not unmapped, f"contract classes with no mirror mapping: {unmapped}"


@pytest.mark.parametrize("contract_name", sorted(CLASS_MAP))
def test_mirror_declares_every_contract_field(contract_name: str):
    contract = _contract_fields()
    mirror_class = getattr(mirror, CLASS_MAP[contract_name])
    declared = set(mirror_class.model_fields)
    expected = {
        field
        for field in contract[contract_name]
        if (contract_name, field) not in DELIBERATELY_OMITTED
    }
    missing = sorted(expected - declared)
    assert not missing, f"{mirror_class.__name__} drops contract fields {missing}"


def test_full_payload_round_trips_the_previously_dropped_slots():
    claim = {"name": "n", "description": "", "status": "inferred", "confidence": 0.5, "evidence_ids": ["ev:1"]}
    topology = mirror.BusinessTopology.model_validate(
        {
            "schema_version": "business-topology-v1",
            "scan_fingerprint": "scan:x",
            "metadata": {"project_id": "p"},
            "data_streams": [{
                **claim, "id": "ds-1", "source_system_id": "sys-a", "destination_system_id": "sys-b",
                "delivery_path_id": "dp-1", "runtime_observation_ids": ["ro-1"], "conflict_ids": ["cf-1"],
            }],
            "delivery_paths": [{
                **claim, "id": "dp-1", "stream_id": "ds-1",
                "segments": [{
                    **claim, "id": "seg-1", "sequence": 1, "source_ref": "sys-a", "destination_ref": "int-1",
                    "mechanism": "queue", "protocol": "JMS", "mode": "buffered",
                    "resource": "queue://secret-host/Q1", "component": "cJMS_1",
                }],
            }],
            "controls": [{**claim, "id": "ct-1", "stream_ids": ["ds-1"], "control_type": "reconciliation"}],
            "runtime_observations": [{
                **claim, "id": "ro-1", "stream_ids": ["ds-1"], "observed_behavior": "Runs every 30 minutes.",
                "kind": "cadence", "source": "mirror",
                "metrics": [{"label": "runs", "value": "48", "window": "1 day", "source": "mirror"}],
            }],
            "conflicts": [{
                **claim, "id": "cf-1", "conflict_type": "runtime_vs_implementation",
                "implementation_evidence_ids": ["ev:2"], "supporting_evidence_ids": ["ev:3"],
            }],
            "artifact_annotations": [{**claim, "id": "aa-1", "artifact_id": "artifact:1", "business_role": "sender"}],
            "component_annotations": [{**claim, "id": "ca-1", "component_id": "c:1", "human_explanation": "Maps"}],
            "lineage_annotations": [{**claim, "id": "la-1", "lineage_id": "l:1", "business_meaning": "Order no"}],
        }
    )
    stream = topology.data_streams[0]
    assert stream.delivery_path_id == "dp-1"
    assert stream.runtime_observation_ids == ["ro-1"]
    assert stream.conflict_ids == ["cf-1"]
    segment = topology.delivery_paths[0].segments[0]
    assert (segment.mode, segment.protocol, segment.component) == ("buffered", "JMS", "cJMS_1")
    assert "resource" not in segment.model_dump()
    assert topology.controls[0].control_type == "reconciliation"
    assert topology.runtime_observations[0].metrics[0].window == "1 day"
    assert topology.conflicts[0].supporting_evidence_ids == ["ev:3"]
    assert topology.artifact_annotations[0].business_role == "sender"
    assert topology.component_annotations[0].human_explanation == "Maps"
    assert topology.lineage_annotations[0].business_meaning == "Order no"
