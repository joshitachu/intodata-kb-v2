"""Contracts and storage for immutable living-document patch candidates."""

from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    ArtifactMembership,
    CandidateState,
    CandidateStatus,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    EvidenceReference,
    KnowledgeRevision,
    PatchCandidate,
    PatchOperation,
    PatchOperationKind,
    PatchPlan,
    PatchPlanProducer,
    PatchTarget,
    RagMutationIntent,
    RagMutationKind,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
    ValidationReceipt,
    candidate_id_for,
    candidate_from_dict,
    candidate_to_dict,
    can_transition,
    content_sha256,
)
from mcp_server.services.living_docs.patch_worker import (
    PATCH_MCP_TOOLS,
    DeterministicPatchPlanProducer,
    PatchTaskContract,
    hold_if_budget_exceeded,
    validate_patch_plan,
)

__all__ = [
    "ArtifactKind", "ArtifactMembership", "CandidateState", "CandidateStatus", "ChangeKind",
    "ChangeSet", "DeterministicImpactFloor", "EvidenceReference", "KnowledgeRevision",
    "PatchCandidate", "PatchOperation", "PatchOperationKind", "PatchPlan", "PatchPlanProducer",
    "PatchTarget", "RagMutationIntent", "RagMutationKind", "SemanticChange", "SourceOrigin",
    "SourceRevision", "ValidationReceipt", "candidate_id_for", "candidate_from_dict",
    "candidate_to_dict", "can_transition", "content_sha256",
    "PATCH_MCP_TOOLS", "DeterministicPatchPlanProducer", "PatchTaskContract",
    "hold_if_budget_exceeded", "validate_patch_plan",
]
