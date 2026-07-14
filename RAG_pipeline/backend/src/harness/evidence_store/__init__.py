"""Evidence persistence and reasoning primitives for harness workflows."""

from .conflicts import group_conflicts
from .models import EvidenceSnapshot, EvidenceSourceClass
from .ranking import EvidenceRankingContext, rank_evidence
from .repository import HarnessEvidenceRepository
from .writer import EvidenceGroundedWriter, GroundedResponse
from .composer import (
    InvestigationAnswer,
    InvestigationConfidence,
    OperationalAnswerComposer,
)
from .synthesis import (
    EvidenceAnswerStatus,
    EvidenceAnswerSynthesizer,
    StructuredEvidenceAnswer,
    aggregate_runtime_failures,
)

__all__ = [
    "EvidenceGroundedWriter",
    "EvidenceRankingContext",
    "EvidenceSnapshot",
    "EvidenceSourceClass",
    "GroundedResponse",
    "HarnessEvidenceRepository",
    "group_conflicts",
    "rank_evidence",
    "InvestigationAnswer",
    "InvestigationConfidence",
    "OperationalAnswerComposer",
    "EvidenceAnswerStatus",
    "EvidenceAnswerSynthesizer",
    "StructuredEvidenceAnswer",
    "aggregate_runtime_failures",
]
