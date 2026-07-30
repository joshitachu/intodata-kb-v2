"""Append-only, redacted tool audit records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from mcp_server.experiments.domain.runtime import redact_payload


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    sequence: int
    occurred_at: datetime
    tool_name: str
    outcome: str
    operation_id: str
    detail: Mapping[str, Any]


class ToolAuditLedger:
    def __init__(self) -> None:
        self._records: list[ToolAuditRecord] = []

    @property
    def records(self) -> tuple[ToolAuditRecord, ...]:
        return tuple(self._records)

    def append(self, *, tool_name: str, outcome: str, operation_id: str, detail: Mapping[str, Any]) -> ToolAuditRecord:
        record = ToolAuditRecord(
            sequence=len(self._records) + 1,
            occurred_at=datetime.now(timezone.utc),
            tool_name=tool_name,
            outcome=outcome,
            operation_id=operation_id,
            detail=redact_payload(detail),
        )
        self._records.append(record)
        return record
