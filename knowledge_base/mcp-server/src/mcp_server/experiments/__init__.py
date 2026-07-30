"""Additive, non-publishing documentation generation experiments.

The package deliberately depends only on its own domain ports at this layer.  Web,
ORM, legacy-generation, publication, and RAG composition belongs outside it.
"""

from mcp_server.experiments.domain.models import (
    ExperimentArtifact,
    GenerationExperiment,
    LegacyBaselineRun,
)
from mcp_server.experiments.domain.statuses import ExperimentStatus

__all__ = [
    "ExperimentArtifact",
    "ExperimentStatus",
    "GenerationExperiment",
    "LegacyBaselineRun",
]
