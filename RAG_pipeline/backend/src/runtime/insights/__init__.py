"""Deterministic runtime insight layer.

parser     — extracts structured signals from raw log message strings.
builder    — builds and persists insight rows from the raw runtime store.
models     — Pydantic API models returned by the insight read layer.
repository — project-scoped async reads over the insight tables.
"""

from src.runtime.insights.builder import InsightBuildResult, build_runtime_insights_for_execution
from src.runtime.insights.models import (
    RuntimeExecutionSummaryInsight,
    RuntimeIncidentEvidence,
    RuntimeIncidentInsight,
    RuntimeTimelineEvent,
)
from src.runtime.insights.parser import (
    ParsedRuntimeLog,
    parse_runtime_log_message,
    redact_credentials,
    redact_log_text,
)
from src.runtime.insights.repository import RuntimeInsightRepository

__all__ = [
    "ParsedRuntimeLog",
    "parse_runtime_log_message",
    "redact_credentials",
    "redact_log_text",
    "InsightBuildResult",
    "build_runtime_insights_for_execution",
    "RuntimeExecutionSummaryInsight",
    "RuntimeIncidentInsight",
    "RuntimeIncidentEvidence",
    "RuntimeTimelineEvent",
    "RuntimeInsightRepository",
]
