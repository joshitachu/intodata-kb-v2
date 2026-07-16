"""Curated, deterministic projections from runtime insight rows to Qdrant."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.db.models.runtime_insights import (
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.runtime.semantic.models import RuntimeSemanticSource

_POINT_NAMESPACE = uuid.UUID("92cc15ca-5ef1-5b68-8678-220a672a78dc")


@dataclass(frozen=True)
class RuntimeSemanticDocument:
    """A single safe-to-embed runtime insight projection."""

    point_id: str
    project_id: str
    source: RuntimeSemanticSource
    record_id: str
    text: str
    execution_id: str | None = None
    incident_id: str | None = None
    signature_hash: str | None = None

    @property
    def payload(self) -> dict[str, str | None]:
        return {
            "project_id": self.project_id,
            "source": self.source.value,
            "record_id": self.record_id,
            "execution_id": self.execution_id,
            "incident_id": self.incident_id,
            "signature_hash": self.signature_hash,
            "text": self.text,
        }


def stable_point_id(project_id: str, source: RuntimeSemanticSource, record_id: str) -> str:
    """Return a stable UUID accepted by Qdrant for an insight identity."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{project_id}:{source.value}:{record_id}"))


def execution_summary_document(row: RuntimeExecutionSummaryRow) -> RuntimeSemanticDocument:
    """Project one already-curated execution summary. Raw log rows are never read."""
    record_id = row.execution_id
    details = _present(
        ("failed component", row.failed_component),
        ("error code", row.primary_error_code),
        ("exception", row.primary_exception_class),
    )
    text = _join("Execution summary", row.summary_text, f"Job: {row.job_name}", details)
    return RuntimeSemanticDocument(
        point_id=stable_point_id(row.project_id, RuntimeSemanticSource.EXECUTION_SUMMARY, record_id),
        project_id=row.project_id,
        source=RuntimeSemanticSource.EXECUTION_SUMMARY,
        record_id=record_id,
        execution_id=row.execution_id,
        text=text,
    )


def incident_document(row: RuntimeIncidentRow) -> RuntimeSemanticDocument:
    """Project incident diagnosis fields; deliberately excludes evidence excerpts."""
    record_id = row.incident_id
    checks = "; ".join(str(check) for check in (row.next_checks or []) if check)
    text = _join(
        "Runtime incident",
        row.symptom_summary,
        row.root_cause_candidate,
        f"Recommended checks: {checks}" if checks else None,
    )
    return RuntimeSemanticDocument(
        point_id=stable_point_id(row.project_id, RuntimeSemanticSource.INCIDENT, record_id),
        project_id=row.project_id,
        source=RuntimeSemanticSource.INCIDENT,
        record_id=record_id,
        execution_id=row.execution_id,
        incident_id=row.incident_id,
        signature_hash=row.signature_hash,
        text=text,
    )


def error_signature_document(row: RuntimeErrorSignatureRow) -> RuntimeSemanticDocument:
    """Project the aggregated signature, never an individual raw failure line."""
    record_id = row.signature_hash
    details = _present(
        ("error code", row.error_code),
        ("exception", row.exception_class),
        ("component", row.component_name),
        ("occurrences", str(row.occurrence_count)),
    )
    text = _join("Runtime error signature", row.summary_text, details)
    return RuntimeSemanticDocument(
        point_id=stable_point_id(row.project_id, RuntimeSemanticSource.ERROR_SIGNATURE, record_id),
        project_id=row.project_id,
        source=RuntimeSemanticSource.ERROR_SIGNATURE,
        record_id=record_id,
        signature_hash=row.signature_hash,
        text=text,
    )


def _present(*values: tuple[str, str | None]) -> str | None:
    parts = [f"{label}: {value}" for label, value in values if value]
    return "; ".join(parts) or None


def _join(*parts: str | None) -> str:
    return ". ".join(part.strip().rstrip(".") for part in parts if part and part.strip()) + "."
