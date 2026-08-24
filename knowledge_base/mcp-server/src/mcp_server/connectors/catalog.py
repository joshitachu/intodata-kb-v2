"""Code-owned connector catalogue; no dynamic filesystem plugins in v1."""

from __future__ import annotations

from collections.abc import Iterable

from .domain import ConnectorCapability, ConnectorProviderManifest


class ConnectorCatalog:
    def __init__(self) -> None:
        self._manifests: dict[str, ConnectorProviderManifest] = {}
        self._providers: dict[str, object] = {}

    def register(self, manifest: ConnectorProviderManifest, provider: object) -> None:
        if manifest.provider_id in self._manifests:
            raise ValueError(f"Duplicate connector provider ID: {manifest.provider_id}")
        if not callable(getattr(provider, "test", None)) or not callable(getattr(provider, "execute", None)):
            raise TypeError("Connector provider must implement test and execute")
        self._manifests[manifest.provider_id] = manifest
        self._providers[manifest.provider_id] = provider

    def manifest(self, provider_id: str) -> ConnectorProviderManifest:
        try:
            return self._manifests[provider_id]
        except KeyError as exc:
            raise KeyError(f"Unknown connector provider {provider_id!r}") from exc

    def provider(self, provider_id: str) -> object:
        self.manifest(provider_id)
        return self._providers[provider_id]

    def manifests(self) -> tuple[ConnectorProviderManifest, ...]:
        return tuple(self._manifests.values())

    def capability(self, provider_id: str, capability_id: str) -> ConnectorCapability:
        return self.manifest(provider_id).capability(capability_id)

    def validate_grant(self, provider_id: str, capability_ids: Iterable[str]) -> None:
        for capability_id in capability_ids:
            self.capability(provider_id, capability_id).validate_run_eligible()


connector_catalog = ConnectorCatalog()
