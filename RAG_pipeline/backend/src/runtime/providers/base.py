"""Connector framework protocol for runtime sources.

This protocol is the source-management contract. It sits beside the existing
LLM tool-provider protocol in ``runtime.providers.__init__``: chat tools read
from the stored runtime DB, while only the scheduler/source-management path uses
this connector protocol to validate credentials and ingest from live sources.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Literal, Mapping, Protocol, TypedDict, runtime_checkable

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
        """Return the endpoint-scoped identity: stable, secret-free, offline-derivable.

        This identifies the API endpoint, not the account behind it. On a shared
        multi-tenant endpoint it is a prefix shared by every tenant, so it must
        not be used as the duplicate-registration key on its own.
        """

    async def resolve_physical_identity(self, credentials: Mapping[str, Any]) -> str:
        """Return the identity stored on the source and checked for duplicates.

        Providers whose endpoint serves exactly one tenant return the endpoint
        identity unchanged (see ``SingleTenantIdentityMixin``). Providers on a
        shared endpoint must narrow it to the account the credentials belong to,
        which usually means one live call.
        """

    def physical_identity_from_batch(
        self, credentials: Mapping[str, Any], batch: RuntimeSyncBatch
    ) -> str | None:
        """Return the identity implied by a freshly synced batch, or None to keep the stored one.

        Lets the identity follow the tenant when the markers it was derived from
        change (e.g. an environment added in TMC), so re-registering the same
        physical source keeps colliding instead of silently mirroring its data.
        """

    def source_type_aliases(self) -> set[str]:
        """Return historical source-type keys whose credentials use this identity."""

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        """Validate credentials synchronously and return a sanitized status tuple."""

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        """Fetch a source batch for the scheduler/write-port path."""


class SingleTenantIdentityMixin:
    """Identity behaviour for providers whose endpoint serves exactly one tenant.

    For these the endpoint already identifies the source uniquely, so there is
    nothing to narrow and nothing a synced batch could tell us that we did not
    already know at registration time.
    """

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def resolve_physical_identity(self, credentials: Mapping[str, Any]) -> str:
        return self.physical_source_identity(credentials)

    def physical_identity_from_batch(
        self, credentials: Mapping[str, Any], batch: RuntimeSyncBatch
    ) -> str | None:
        return None


def compose_tenant_identity(endpoint_identity: str, tenant_markers: Iterable[str]) -> str:
    """Narrow an endpoint identity to the tenant that endpoint was observed to serve.

    Multi-tenant SaaS runtimes put every customer behind one regional endpoint,
    so an endpoint-only identity treats unrelated accounts as the same physical
    source. Markers are ids only the owning tenant can produce; they are hashed
    so the stored identity stays free of anything secret or customer-identifying.
    """
    markers = sorted({marker.strip() for marker in tenant_markers if marker and marker.strip()})
    if not markers:
        raise ValueError("Cannot derive a tenant identity without markers")
    digest = hashlib.sha256("\n".join(markers).encode("utf-8")).hexdigest()[:16]
    return f"{endpoint_identity}#{digest}"


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
