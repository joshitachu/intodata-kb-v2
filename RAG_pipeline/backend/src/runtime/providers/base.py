"""Connector framework protocol for runtime sources.

This protocol is the source-management contract. It sits beside the existing
LLM tool-provider protocol in ``runtime.providers.__init__``: chat tools read
from the stored runtime DB, while only the scheduler/source-management path uses
this connector protocol to validate credentials and ingest from live sources.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Protocol, TypedDict, runtime_checkable

from src.runtime.models import RuntimeScopeOption, RuntimeScopeSelection
from src.runtime.sync import RuntimeSyncBatch


class CredentialField(TypedDict, total=False):
    name: str
    type: Literal["secret", "url", "text"]
    label: str
    required: bool


#: Provenance recorded on a mapping a human created through the write route.
RESOLVED_BY_MANUAL = "manual"


@dataclass(frozen=True)
class StaticRefStrategy:
    """How this provider names the static (scanned) design behind a runtime task.

    A provider knows which of its own fields carries a reference to the design
    that was scanned; nothing else does. Shared code reading a field name
    directly would be reading a provider-specific fact it cannot check: TMC is
    the only source that publishes ``artifact_name``, and Elasticsearch has no
    equivalent at all, so the field has to be DECLARED rather than assumed.

    ``match`` is exact-only and deliberately has no other member. A prefix or
    normalising tier was measured against the Pluripharm estate and produced 25
    wrong bindings, each of which attaches one job's runtime history to a
    different, genuinely existing job: ``DWH_Facts_001`` -> ``DWH_Facts_Master``
    (a separate job, currently failing), ``Daily_Queries_6_uur_old`` -> the live
    ``Daily_Queries_6_uur``, ``WMS_IE_stock_mirror_copy`` ->
    ``WMS_IE_stock_mirror``. The two consumers that got here first state the
    same rule (``RuntimeStoreRepository.doc_evidence_for_units`` and the MCP
    harness's ``runtime_facts``): a near-miss name is a DIFFERENT job, not the
    same one spelled loosely.
    """

    #: Attribute on this provider's ``RuntimeJob`` carrying the static reference.
    source_field: str
    match: Literal["exact"] = "exact"

    def resolved_by(self) -> str:
        """Provenance stamped on every mapping this strategy resolves."""
        return f"static_ref:{self.source_field}"


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

    def static_ref_strategy(self) -> StaticRefStrategy | None:
        """Return how to name the scanned design behind a runtime task.

        ``None`` is a first-class answer, not a failure: it means this provider
        exposes no static reference, so nothing can be bound automatically and
        the mapping stays a human decision.
        """

    def executable_semantics(self) -> Mapping[str, Literal["execution", "lifecycle"]]:
        """What ONE runtime row MEANS, per executable kind.

        ``execution`` -- one row is one run of the thing, with a start and an
        end. ``lifecycle`` -- one row is a deployment that stays up, so
        ``state="running"`` means "deployed and up", NOT "busy doing work".
        Reading a lifecycle row as an execution turns a healthy always-on route
        into a run that never finished, which is why the distinction is declared
        by the provider that produced the row instead of inferred downstream.

        Keys are ``RuntimeExecutableKind`` values. A provider that ingests
        nothing returns an empty mapping.
        """

    def scope_selection(self) -> RuntimeScopeSelection | None:
        """Describe the selectable ingest scope, or ``None`` for whole-source providers."""

    async def discover_scopes(self, credentials: Mapping[str, Any]) -> list[RuntimeScopeOption]:
        """Validate credentials and return the scopes they can reach.

        This is the "test connection" call: reaching the source is what proves
        the credentials work, and the scope list is the useful by-product.
        Providers without a scope partition return an empty list.
        """

    def identity_credential_fields(self) -> set[str]:
        """Return credential field names the physical identity is derived from.

        These are frozen after registration: editing one would repoint the
        source at a different account while keeping its stored identity and
        ingested history, so the config-patch path refuses them.
        """

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


class NoScopeSelectionMixin:
    """Default behaviour for providers that ingest their whole source."""

    def scope_selection(self) -> RuntimeScopeSelection | None:
        return None

    async def discover_scopes(self, credentials: Mapping[str, Any]) -> list[RuntimeScopeOption]:
        return []


#: Per-source READ scope: which slice of the mirror documentation runs may use,
#: and how far back. Distinct from the ingest scope a provider declares -- that
#: one decides what gets polled and stored, this one decides what gets read out
#: of what was stored, so narrowing it costs nothing at the vendor API and can
#: be changed without re-mirroring. Provider-independent by construction: any
#: source that mirrors something a document reads has the same two questions.
#:
#: `doc_environment_ids` absent (or None) means every mirrored environment,
#: which is the behaviour every existing source already has.
DOC_READ_SCOPE_FIELDS = ("doc_environment_ids", "doc_window_days")


def editable_config_fields(provider: "RuntimeSourceProvider") -> list[str]:
    """Return the credential keys a config patch may write.

    Secrets are excluded because the patch path never asks for them, and
    identity fields because changing one detaches the source from the account
    its history belongs to. Everything else a provider declares -- filters,
    windows, and the scope selection -- is operator-editable by construction,
    so new settings become editable without touching the route.

    The documentation read scope is appended for every provider. It is not in
    any `credential_schema()` deliberately: the connect dialog renders that
    schema, and read scope is not something an operator should have to decide
    while holding an API token -- it is chosen later, per project, once the
    mirror can say which environments actually exist.
    """
    frozen = provider.identity_credential_fields()
    fields = [
        name
        for field in provider.credential_schema()
        if (name := field.get("name")) and field.get("type") != "secret" and name not in frozen
    ]
    selection = provider.scope_selection()
    if selection is not None and selection.field not in fields:
        fields.append(selection.field)
    fields.extend(name for name in DOC_READ_SCOPE_FIELDS if name not in fields)
    return fields


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
