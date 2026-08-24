"""Provider protocol for semantic evidence adapters."""

from __future__ import annotations

from typing import Protocol

from ..domain import (
    ConnectorInstanceConfig,
    ConnectorSourceProfile,
    EvidenceEnvelope,
    EvidenceQuestion,
)


class ConnectorProvider(Protocol):
    """A provider may only receive typed, server-built capability requests."""

    manifest: object

    async def test(self, instance: ConnectorInstanceConfig, credential: str) -> dict[str, object]: ...

    async def discover(
        self, instance: ConnectorInstanceConfig, credential: str, **options: object
    ) -> list[ConnectorSourceProfile]: ...

    async def describe(
        self, instance: ConnectorInstanceConfig, source: ConnectorSourceProfile, credential: str
    ) -> ConnectorSourceProfile: ...

    async def execute(
        self,
        *,
        instance: ConnectorInstanceConfig,
        source: ConnectorSourceProfile,
        credential: str,
        capability_id: str,
        question: EvidenceQuestion,
        run_id: int,
        project_id: str,
    ) -> EvidenceEnvelope: ...
