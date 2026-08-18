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

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:
        """Return a stable, secret-free identity for duplicate registration checks."""

    def source_type_aliases(self) -> set[str]:
        """Return historical source-type keys whose credentials use this identity."""

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        """Validate credentials synchronously and return a sanitized status tuple."""

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        """Fetch a source batch for the scheduler/write-port path."""


def find_non_ascii_credential_field(
    schema: list[CredentialField], credentials: Mapping[str, Any]
) -> str | None:
    """Return the label of the first declared credential field holding a non-ASCII value, else None.

    Connector adapters commonly interpolate credential values straight into HTTP
    headers or URLs (see ``httpx.Headers``, which defaults to ascii encoding).
    Values corrupted by rich-text paste (e.g. a Cyrillic look-alike character
    swapped for a Latin one) crash deep inside the HTTP client with a raw
    ``UnicodeEncodeError`` instead of a clear validation error. Checking every
    provider's declared schema here catches this for all connectors, not just
    the ones we've hit it on.
    """
    for field in schema:
        name = field.get("name")
        if not name:
            continue
        value = credentials.get(name)
        if not isinstance(value, str):
            continue
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            return field.get("label") or name
    return None
