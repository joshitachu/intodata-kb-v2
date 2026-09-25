"""Public contracts for configured harness connectors."""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConnectorAuthMode(StrEnum):
    NONE = "none"
    CREDENTIAL_REFERENCE = "credential_reference"


class ConnectorHealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ConnectorManifest(BaseModel):
    provider_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    auth_modes: tuple[ConnectorAuthMode, ...]
    capability_ids: tuple[str, ...]
    config_schema: dict[str, Any] = Field(default_factory=dict)


class ConnectorCapabilityDefinition(BaseModel):
    """Validated, provider-neutral capability data loaded from the connector catalog."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{1,159}$")
    label: str = Field(min_length=1, max_length=120)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    input_schema: dict[str, Any]
    output_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    risk_tier: str = Field(default="read_only", pattern=r"^read_only$")
    packs: tuple[str, ...] = Field(default=("connectors",), max_length=20)
    activity_label: str = Field(min_length=1, max_length=160)
    semantic_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,47}$")
    purpose: str | None = Field(default=None, min_length=1, max_length=500)
    use_when: tuple[str, ...] = Field(default=(), max_length=10)
    do_not_use_when: tuple[str, ...] = Field(default=(), max_length=10)
    preconditions: tuple[str, ...] = Field(default=(), max_length=10)
    failure_recovery: dict[str, str] = Field(default_factory=dict, max_length=10)
    shadow_safe: bool = False


class ConnectorProviderDefinition(BaseModel):
    """Trusted, file-backed connector type definition.

    The adapter reference is deliberately restricted to this package. Credential
    material is never part of catalog data or connector configuration.
    """

    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    adapter_ref: str = Field(pattern=r"^src\.harness\.connectors\.[a-zA-Z0-9_.]+$")
    auth_modes: tuple[ConnectorAuthMode, ...]
    capabilities: tuple[ConnectorCapabilityDefinition, ...] = Field(min_length=1, max_length=50)
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capability_ids(
        cls, values: tuple[ConnectorCapabilityDefinition, ...]
    ) -> tuple[ConnectorCapabilityDefinition, ...]:
        ids = [value.id for value in values]
        if len(ids) != len(set(ids)):
            raise ValueError("connector capability ids must be unique")
        return values

    def manifest(self) -> ConnectorManifest:
        return ConnectorManifest(
            provider_key=self.provider_key,
            display_name=self.display_name,
            description=self.description,
            auth_modes=self.auth_modes,
            capability_ids=tuple(capability.id for capability in self.capabilities),
            config_schema=self.config_schema,
        )


class ConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(default="default", min_length=1, max_length=80)
    max_results: int = Field(default=10, ge=1, le=50)


class ConnectorInstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    config: dict[str, Any] = Field(default_factory=dict)
    auth_mode: ConnectorAuthMode = ConnectorAuthMode.NONE
    credential_reference: str | None = Field(default=None, max_length=255)
    enabled: bool = True

    @field_validator("credential_reference")
    @classmethod
    def validate_reference(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ch.isspace() for ch in value)):
            raise ValueError("credential_reference must be a non-empty opaque reference")
        return value


class ConnectorInstanceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    credential_reference: str | None = Field(default=None, max_length=255)
    clear_credential_reference: bool = False
    enabled: bool | None = None


class CapabilityBindingCreate(BaseModel):
    capability_id: str = Field(min_length=1, max_length=160)
    project_ids: list[str] = Field(default_factory=list, max_length=100)
    domains: list[str] = Field(default_factory=list, max_length=50)
    risk_tier: str = Field(default="read_only", pattern=r"^[a-z_]{1,30}$")
    redaction_fields: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True


class CapabilityBindingUpdate(BaseModel):
    """Owner edits to a project-scoped capability grant."""

    model_config = ConfigDict(extra="forbid")

    project_ids: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class ConnectorInstanceView(BaseModel):
    id: int
    name: str
    provider_key: str
    config: dict[str, Any]
    auth_mode: ConnectorAuthMode
    credential_reference_type: str | None
    credential_present: bool
    enabled: bool
    health_status: ConnectorHealthStatus
    health_summary: str | None
    health_checked_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CapabilityBindingView(BaseModel):
    id: int
    connector_instance_id: int
    capability_id: str
    project_ids: list[str]
    domains: list[str]
    risk_tier: str
    redaction_fields: list[str]
    enabled: bool


class ConnectorQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class ConnectorResultItem(BaseModel):
    title: str = Field(max_length=200)
    excerpt: str = Field(max_length=2000)
    source_ref: str = Field(max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorReadResult(BaseModel):
    items: list[ConnectorResultItem]
    truncated: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ConnectorFreshnessSource(BaseModel):
    source_id: str
    source: str
    status: Literal["available", "unavailable"]
    latest_event_at: datetime.datetime | None = None
    events_in_window: int | None = None
    total_documents: int | None = None
    timestamped_documents: int | None = None
    freshness_status: Literal["active", "stale", "timestamp_unavailable", "empty"] | None = None
    error_code: str | None = None
    error: str | None = None


class ConnectorFreshnessResult(BaseModel):
    checked_at: datetime.datetime
    window_days: int
    sources: list[ConnectorFreshnessSource]


class ConnectorHealthResult(BaseModel):
    status: ConnectorHealthStatus
    summary: str = Field(max_length=500)
