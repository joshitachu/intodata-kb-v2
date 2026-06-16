"""DB-backed runtime provider (held-data Model 2 skeleton, Slice 2).

Answers the four generic runtime tools from the held runtime-data store instead of
calling a live source, by delegating to the Slice 1 RuntimeStoreRepository. Reads are
project-scoped using the scope carried on RuntimeProviderContext — which originates
from the authenticated request, never from LLM tool arguments.

Source-agnostic: no Talend-specific names. A future sync worker (or any source)
populates the store through the repository write-port; this provider only reads.
"""

from __future__ import annotations

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeLogEvent,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository


class StoredRuntimeProvider:
    """Reads held runtime data via the repository, scoped per request."""

    @staticmethod
    def _repository(context: RuntimeProviderContext | None) -> RuntimeStoreRepository:
        if context is None or context.db is None:
            raise RuntimeProviderError("stored runtime provider requires a database session")
        return RuntimeStoreRepository(context.db)

    @staticmethod
    def _scope(context: RuntimeProviderContext | None):
        # context is non-None here (checked by _repository); kept explicit for clarity.
        return context.project_scope if context is not None else None

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        repo = self._repository(context)
        return await repo.list_executions(self._scope(context), params)

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        repo = self._repository(context)
        try:
            return await repo.get_execution_details(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        repo = self._repository(context)
        return await repo.detect_failures(self._scope(context), params)

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        repo = self._repository(context)
        try:
            return await repo.get_execution_logs(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e


provider = StoredRuntimeProvider()
