"""Connector framework protocol for runtime sources.

This protocol is the source-management contract. It sits beside the existing
LLM tool-provider protocol in ``runtime.providers.__init__``: chat tools read
from the stored runtime DB, while only the scheduler/source-management path uses
this connector protocol to validate credentials and ingest from live sources.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Protocol, TypedDict, runtime_checkable

from src.runtime.sync import RuntimeSyncBatch


class CredentialField(TypedDict, total=False):
    name: str
    type: Literal["secret", "url", "text"]
    label: str
    required: bool


@runtime_checkable
class RuntimeSourceProvider(Protocol):
    """Protocol every self-service runtime source connector must satisfy."""

    def provider_key(self) -> str:
        """Return a stable source type id, for example ``mock`` or ``tmc``."""

    def provider_label(self) -> str:
        """Return a human-readable source type label for the admin UI."""

    def credential_schema(self) -> list[CredentialField]:
        """Return the provider-declared credential fields rendered by the UI."""

    def container_key_field(self) -> str:
        """Return the entity field that carries the source-agnostic container key."""

    def static_ref_strategy(self) -> None:
        """Return the static-reference strategy; v1 reserves the slot as ``None``."""

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        """Validate credentials synchronously and return a sanitized status tuple."""

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        """Fetch a source batch for the scheduler/write-port path."""
