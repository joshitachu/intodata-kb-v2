"""Thread-safe warning accumulator for pipeline phases."""
from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WarningCode(StrEnum):
    PHASE1_GROUP_FAILURE = "PHASE1_GROUP_FAILURE"
    PHASE2_COVERAGE_PARTIAL = "PHASE2_COVERAGE_PARTIAL"
    PHASE2_DEPTH_PARTIAL = "PHASE2_DEPTH_PARTIAL"
    THIN_BUNDLE = "THIN_BUNDLE"


class PipelineWarning(BaseModel):
    phase: str
    code: WarningCode
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class WarningCollector:
    """Accumulates structured warnings emitted by pipeline phases.

    Plain Python class (not Pydantic) — asyncio.Lock does not compose with Pydantic
    model validation.
    """

    def __init__(self) -> None:
        self._warnings: list[PipelineWarning] = []
        self._lock: asyncio.Lock = asyncio.Lock()

    async def emit(
        self,
        phase: str,
        code: WarningCode,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            self._warnings.append(
                PipelineWarning(phase=phase, code=code, message=message, context=context or {})
            )

    def has_warnings(self) -> bool:
        return len(self._warnings) > 0

    @property
    def warnings(self) -> list[PipelineWarning]:
        return list(self._warnings)
