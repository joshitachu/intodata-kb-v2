"""Provider protocol and registry for generic connectors."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ConnectorHealthResult,
    ConnectorCapabilityDefinition,
    ConnectorManifest,
    ConnectorProviderDefinition,
    ConnectorQuery,
    ConnectorReadResult,
)


class ConnectorProvider(Protocol):
    def validate_config(self, config: dict) -> dict: ...

    async def health(self, config: dict, credential_reference: str | None) -> ConnectorHealthResult: ...

    async def read(
        self, capability_id: str, query: ConnectorQuery, config: dict,
        credential_reference: str | None, project_id: str | None,
    ) -> ConnectorReadResult: ...


class ConnectorProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, tuple[ConnectorProviderDefinition, ConnectorProvider]] = {}

    def register(self, definition: ConnectorProviderDefinition, provider: ConnectorProvider) -> None:
        key = definition.provider_key
        if key in self._providers:
            raise ValueError(f"Connector provider already registered: {key}")
        self._providers[key] = (definition, provider)

    def get(self, provider_key: str) -> ConnectorProvider:
        try:
            return self._providers[provider_key][1]
        except KeyError as exc:
            raise KeyError(f"Unknown connector provider: {provider_key}") from exc

    def definition(self, provider_key: str) -> ConnectorProviderDefinition:
        try:
            return self._providers[provider_key][0]
        except KeyError as exc:
            raise KeyError(f"Unknown connector provider: {provider_key}") from exc

    def manifest(self, provider_key: str) -> ConnectorManifest:
        return self.definition(provider_key).manifest()

    def capability(
        self, provider_key: str, capability_id: str
    ) -> ConnectorCapabilityDefinition:
        definition = self.definition(provider_key)
        for capability in definition.capabilities:
            if capability.id == capability_id:
                return capability
        raise KeyError(f"Unknown connector capability: {capability_id}")

    def capabilities_for_instance(
        self, provider_key: str, config: dict
    ) -> tuple[ConnectorCapabilityDefinition, ...]:
        """Return the attested capability snapshot for one connector instance."""

        definition = self.definition(provider_key)
        dynamic = getattr(self.get(provider_key), "capabilities_for_instance", None)
        if callable(dynamic):
            values = dynamic(config)
            return tuple(ConnectorCapabilityDefinition.model_validate(value) for value in values)
        return definition.capabilities

    def capability_for_instance(
        self, provider_key: str, capability_id: str, config: dict
    ) -> ConnectorCapabilityDefinition:
        for capability in self.capabilities_for_instance(provider_key, config):
            if capability.id == capability_id:
                return capability
        raise KeyError(f"Unknown connector capability: {capability_id}")

    def provider_keys(self) -> frozenset[str]:
        return frozenset(self._providers)

    def manifests(self) -> list[ConnectorManifest]:
        return [self.manifest(key) for key in sorted(self._providers)]
