"""In-process shape for registered Nexus harness capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.models import (
    CapabilityDefinition,
    CapabilityHealth,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    OPERATIONAL_INVESTIGATION_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
)


class CapabilityRegistry:
    """Small immutable-ish registry used by workflow selection code.

    This is intentionally only metadata. Provider execution lives behind later
    capability/provider adapters, not inside the registry.
    """

    def __init__(self, capabilities: Iterable[CapabilityDefinition] = ()) -> None:
        self._capabilities = {capability.id: capability for capability in capabilities}

    def register(self, capability: CapabilityDefinition) -> None:
        self._capabilities[capability.id] = capability

    def list(self, *, enabled_only: bool = False) -> list[CapabilityDefinition]:
        capabilities = list(self._capabilities.values())
        if enabled_only:
            capabilities = [
                capability
                for capability in capabilities
                if capability.enabled and capability.health != CapabilityHealth.UNAVAILABLE
            ]
        return sorted(capabilities, key=lambda capability: capability.id)

    def get(self, capability_id: str) -> CapabilityDefinition:
        capability = self._capabilities.get(capability_id)
        if capability is None:
            raise UnknownCapabilityError(f"Unknown capability: {capability_id}")
        return capability

    def require_available(self, capability_id: str) -> CapabilityDefinition:
        capability = self.get(capability_id)
        if not capability.enabled or capability.health == CapabilityHealth.UNAVAILABLE:
            raise CapabilityDisabledError(f"Capability is unavailable: {capability_id}")
        return capability


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry([
        DOCUMENT_SEARCH_CAPABILITY,
        DOCUMENT_LIST_CAPABILITY,
        RUNTIME_EVIDENCE_CAPABILITY,
        DOCUMENTATION_GENERATION_CAPABILITY,
        OPERATIONAL_INVESTIGATION_CAPABILITY,
        DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
        EXTERNAL_CONNECTOR_READ_CAPABILITY,
    ])
