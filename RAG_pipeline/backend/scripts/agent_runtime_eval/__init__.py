"""Versioned behavioral evaluation contract for the Nexus agent runtime."""

from .corpus import CORPUS_VERSION, REQUIRED_FAMILIES, load_corpus
from .grader import grade_scenario
from .models import EvaluationReport, RunTrace, Scenario
from .runner import (
    AuthenticatedDeployedApiTarget,
    LocalIntegrationTarget,
    RecordedTraceTarget,
    ScriptedTraceTarget,
    evaluate_target,
)

__all__ = [
    "CORPUS_VERSION",
    "REQUIRED_FAMILIES",
    "AuthenticatedDeployedApiTarget",
    "EvaluationReport",
    "LocalIntegrationTarget",
    "RecordedTraceTarget",
    "RunTrace",
    "Scenario",
    "ScriptedTraceTarget",
    "evaluate_target",
    "grade_scenario",
    "load_corpus",
]
