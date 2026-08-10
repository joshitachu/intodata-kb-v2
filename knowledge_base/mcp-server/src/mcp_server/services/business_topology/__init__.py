"""Agent-synthesised, evidence-backed business topology support.

The scanner remains the source of technical facts.  This package only prepares
those facts for a bounded language-model task and validates the separate
business-facing result.
"""

from mcp_server.services.business_topology.context import (
    build_topology_context_files,
)
from mcp_server.services.business_topology.deterministic import (
    build_deterministic_topology,
)
from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
    artifact_evidence_id,
    build_business_topology_evidence,
    component_evidence_id,
    compute_scan_fingerprint,
    lineage_evidence_id,
    load_business_topology_evidence,
)
from mcp_server.services.business_topology.models import BusinessTopology
from mcp_server.services.business_topology.validation import (
    BusinessTopologyValidationReport,
    parse_and_validate_business_topology,
    validate_business_topology,
)

__all__ = [
    "BusinessTopology",
    "BusinessTopologyEvidenceBundle",
    "BusinessTopologyValidationReport",
    "artifact_evidence_id",
    "build_business_topology_evidence",
    "build_deterministic_topology",
    "build_topology_context_files",
    "component_evidence_id",
    "compute_scan_fingerprint",
    "lineage_evidence_id",
    "load_business_topology_evidence",
    "parse_and_validate_business_topology",
    "validate_business_topology",
]
