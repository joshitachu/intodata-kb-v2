"""Stable provider-neutral types for the live connector boundary.

These types contain no provider credentials or vendor query language.  They are
used by management, the capability broker, and provider adapters alike.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ConnectorRisk(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class ConnectorStatus(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    NOT_CONNECTED = "not_connected"
    DISABLED = "disabled"


class ObservationClassification(StrEnum):
    CORROBORATES = "corroborates"
    OPERATIONAL_CONTEXT = "operational_context"
    DISCREPANCY = "discrepancy"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class ConnectorBudget:
    """Hard limits.  A lower project binding budget may never expand these."""

    max_calls: int = 12
    max_items: int = 30
    max_bytes: int = 131_072
    max_total_bytes: int = 524_288
    max_time_window_days: int = 30
    max_buckets: int = 25
    timeout_seconds: int = 15

    def narrowed_by(self, requested: Mapping[str, int] | None) -> "ConnectorBudget":
        if not requested:
            return self
        values: dict[str, int] = {}
        for key in self.__dataclass_fields__:
            configured = requested.get(key)
            current = getattr(self, key)
            values[key] = current if configured is None else min(current, max(0, int(configured)))
        return ConnectorBudget(**values)


@dataclass(frozen=True, slots=True)
class ConnectorCapability:
    id: str
    label: str
    description: str
    read_only: bool = True
    gather_eligible: bool = True
    # Kept explicitly as trusted metadata for the capability broker/OpenCode
    # configuration. V1 permits only Gather; callers must never infer this from
    # a capability name.
    phase_eligibility: tuple[str, ...] = ("gather",)
    risk: ConnectorRisk = ConnectorRisk.LOW
    accepted_entity_ref_types: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    default_budget: ConnectorBudget = field(default_factory=ConnectorBudget)
    use_when: str = ""
    do_not_use_when: str = ""

    def validate_run_eligible(self) -> None:
        if not self.read_only or not self.gather_eligible or "gather" not in self.phase_eligibility:
            raise ValueError(
                f"Capability {self.id!r} is not an eligible read-only Gather capability"
            )


class ConnectorKind(StrEnum):
    """What a provider contributes, which is the directory's Type column."""

    # Queried on demand during a run; returns evidence envelopes.
    LIVE_EVIDENCE = "live_evidence"
    # Mirrored on a schedule into the runtime store; read back as aggregates.
    RUNTIME_SYNC = "runtime_sync"


class ConnectorBuildState(StrEnum):
    """Whether a listed provider can actually be connected yet.

    The directory renders every registered manifest so the list doubles as a
    roadmap. ``COMING_SOON`` rows are shown without a Connect action -- offering
    one that leads to a non-functional form is worse than showing nothing.
    """

    AVAILABLE = "available"
    COMING_SOON = "coming_soon"


@dataclass(frozen=True, slots=True)
class ConnectorProviderManifest:
    provider_id: str
    label: str
    version: str
    authentication_types: tuple[str, ...]
    configuration_schema: Mapping[str, Any]
    secret_fields: tuple[str, ...]
    capabilities: tuple[ConnectorCapability, ...]
    redaction_policy_version: str = "v1"
    setup_capabilities: tuple[str, ...] = ("test", "discover")
    kind: ConnectorKind = ConnectorKind.LIVE_EVIDENCE
    build_state: ConnectorBuildState = ConnectorBuildState.AVAILABLE
    description: str = ""
    # Field in ``safe_config`` identifying the account/domain this instance
    # points at. Instances are unique per (provider, account) so the same
    # cluster or tenant cannot be registered twice under different names.
    account_key_field: str = ""

    def __post_init__(self) -> None:
        if not self.provider_id or any(ch.isspace() for ch in self.provider_id):
            raise ValueError("provider_id must be a non-empty stable identifier")
        ids = [capability.id for capability in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Provider {self.provider_id!r} declares duplicate capability IDs")
        if not self.authentication_types:
            raise ValueError("provider manifest must declare authentication types")

    def capability(self, capability_id: str) -> ConnectorCapability:
        for capability in self.capabilities:
            if capability.id == capability_id:
                return capability
        raise KeyError(f"Unknown capability {capability_id!r} for {self.provider_id!r}")


def account_key_for(
    manifest: ConnectorProviderManifest, safe_config: Mapping[str, Any]
) -> str:
    """Normalized identity of the account/domain an instance points at.

    Returns "" when the manifest declares no account field, which disables the
    uniqueness check rather than collapsing every instance of that provider onto
    one empty key.

    Normalization is deliberately conservative -- lowercase, strip a trailing
    slash and a scheme -- so that `https://cluster.example.com/` and
    `cluster.example.com` are recognised as the same cluster, while anything
    that genuinely differs stays distinct.
    """
    field_name = manifest.account_key_field
    if not field_name:
        return ""
    raw = safe_config.get(field_name)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    value = raw.strip().casefold().rstrip("/")
    for scheme in ("https://", "http://"):
        if value.startswith(scheme):
            value = value[len(scheme) :]
            break
    return value


@dataclass(frozen=True, slots=True)
class ConnectorInstanceConfig:
    id: str
    provider_id: str
    display_name: str
    safe_config: Mapping[str, Any]
    ownership_scope: str = "workspace"
    enabled: bool = True
    status: ConnectorStatus = ConnectorStatus.NOT_CONNECTED
    provider_version: str = ""
    redaction_policy_version: str = "v1"


@dataclass(frozen=True, slots=True)
class ConnectorSourceProfile:
    id: str
    connector_instance_id: str
    source_type: str
    display_label: str
    locator: str
    fields: Mapping[str, Any]
    enabled: bool = True
    discovered_at: dt.datetime | None = None
    discovery_digest: str | None = None

    @property
    def approved_fields(self) -> tuple[str, ...]:
        values: list[str] = []
        for value in self.fields.values():
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple)):
                values.extend(item for item in value if isinstance(item, str))
        return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class ProjectConnectorBinding:
    id: str
    project_id: str
    connector_instance_id: str
    source_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    budgets: Mapping[str, int] = field(default_factory=dict)
    max_time_window_days: int | None = None
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class RunConnectorSnapshot:
    id: str
    generate_run_id: int
    project_id: str
    initiating_user_id: int
    connector_instance_id: str
    binding_id: str
    binding_version: int
    source_profiles: tuple[ConnectorSourceProfile, ...]
    capability_ids: tuple[str, ...]
    budgets: Mapping[str, int]
    issued_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    nonce: str
    snapshot_id: str
    generate_run_id: int
    project_id: str
    initiating_user_id: int
    connector_instance_id: str
    source_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    issued_at: dt.datetime
    expires_at: dt.datetime
    phase: str = "gather"

    def is_valid_for(self, *, now: dt.datetime, phase: str, project_id: str) -> bool:
        return (
            self.phase == "gather"
            and phase == "gather"
            and self.project_id == project_id
            and self.issued_at <= now < self.expires_at
        )


@dataclass(frozen=True, slots=True)
class EvidenceQuestion:
    question: str
    entity_refs: tuple[str, ...] = ()
    time_from: dt.datetime | None = None
    time_to: dt.datetime | None = None
    severities: tuple[str, ...] = ()
    group_by: tuple[str, ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    reference: str
    observed_at: dt.datetime | None
    fields: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ConnectorLimitation:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    evidence_id: str
    run_id: int
    project_id: str
    connector_instance_id: str
    source_id: str
    capability_id: str
    question: str
    entity_refs: tuple[str, ...]
    observed_at: dt.datetime
    source_window: Mapping[str, str]
    items: tuple[EvidenceItem, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[ConnectorLimitation, ...] = ()
    redaction: Mapping[str, Any] = field(default_factory=lambda: {"applied": False, "count": 0})
    truncation: Mapping[str, Any] = field(default_factory=lambda: {"applied": False})
    digest: str = ""
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
