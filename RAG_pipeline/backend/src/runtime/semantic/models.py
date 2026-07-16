"""Public models for bounded runtime semantic recall."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RuntimeSemanticSource(str, Enum):
    EXECUTION_SUMMARY = "execution_summary"
    INCIDENT = "incident"
    ERROR_SIGNATURE = "error_signature"


class RuntimeSemanticResult(BaseModel):
    """One curated runtime-insight hit returned from the semantic index."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    source: RuntimeSemanticSource
    record_id: str
    text: str
    score: float
    execution_id: str | None = None
    incident_id: str | None = None
    signature_hash: str | None = None
    provenance: tuple[str, ...] = Field(default=("semantic",))
