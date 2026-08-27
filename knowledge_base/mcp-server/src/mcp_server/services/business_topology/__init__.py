"""Agent-synthesised, evidence-backed business topology support.

The scanner remains the source of technical facts.  This package only prepares
those facts for a bounded language-model task and validates the separate
business-facing result.
"""

from mcp_server.services.business_topology.assembly import (
    GENERATED_BY as FLOW_EXTRACTION_GENERATED_BY,
    assemble_topology,
)
from mcp_server.services.business_topology.composite import (
    build_composite_org_topology,
)
from mcp_server.services.business_topology.context import (
    build_topology_context_files,
)
from mcp_server.services.business_topology.deterministic import (
    build_deterministic_topology,
)
from mcp_server.services.business_topology.doc_evidence import (
    collect_doc_evidence,
    discover_doc_filenames,
    register_doc_evidence,
)
from mcp_server.services.business_topology.flow_extraction import (
    FlowExtraction,
    parse_flow_extraction,
)
from mcp_server.services.business_topology.pruning import prune_to_valid
from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
    artifact_evidence_id,
    build_business_topology_evidence,
    component_evidence_id,
    compute_scan_fingerprint,
    lineage_evidence_id,
    load_business_topology_evidence,
)
from mcp_server.services.business_topology.evidence_merge import (
    build_composite_evidence_catalog,
)
from mcp_server.services.business_topology.models import BusinessTopology
from mcp_server.services.business_topology.repair import (
    parse_validate_repair_business_topology,
    repair_business_topology,
)
from mcp_server.services.business_topology.validation import (
    BusinessTopologyValidationReport,
    parse_and_validate_business_topology,
    validate_business_topology,
)

__all__ = [
    "discover_doc_filenames",
    "FLOW_EXTRACTION_GENERATED_BY",
    "BusinessTopology",
    "BusinessTopologyEvidenceBundle",
    "BusinessTopologyValidationReport",
    "FlowExtraction",
    "artifact_evidence_id",
    "assemble_topology",
    "collect_doc_evidence",
    "parse_flow_extraction",
    "prune_to_valid",
    "register_doc_evidence",
    "build_business_topology_evidence",
    "build_composite_evidence_catalog",
    "build_composite_org_topology",
    "build_deterministic_topology",
    "build_topology_context_files",
    "component_evidence_id",
    "compute_scan_fingerprint",
    "lineage_evidence_id",
    "load_business_topology_evidence",
    "parse_and_validate_business_topology",
    "parse_validate_repair_business_topology",
    "repair_business_topology",
    "validate_business_topology",
]
