"""In-process shape for registered Nexus harness capabilities."""

from __future__ import annotations

from collections.abc import Iterable

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    CapabilityDefinition,
    CapabilityHealth,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENTATION_PACK_CREATE_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    OPERATIONAL_INVESTIGATION_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    SOURCE_OPTIONS_CAPABILITY,
    TRIGGER_SCAN_CAPABILITY,
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


def base_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry([
        DOCUMENT_SEARCH_CAPABILITY,
        DOCUMENT_LIST_CAPABILITY,
        RUNTIME_EVIDENCE_CAPABILITY,
        DOCUMENTATION_GENERATION_CAPABILITY,
        DOCUMENTATION_PACK_CREATE_CAPABILITY,
        DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
        OPERATIONAL_INVESTIGATION_CAPABILITY,
        DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
        DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
        EXTERNAL_CONNECTOR_READ_CAPABILITY,
        PROJECT_ONBOARDING_CAPABILITY,
        SOURCE_OPTIONS_CAPABILITY,
        ATTACH_GIT_SOURCE_CAPABILITY,
        ATTACH_ZIP_SOURCE_CAPABILITY,
        TRIGGER_SCAN_CAPABILITY,
        PROJECT_STATUS_CAPABILITY,
        PUBLISH_INDEX_CAPABILITY,
    ])


def default_capability_registry() -> CapabilityRegistry:
    from src.harness.packs.runtime import compile_active_pack_registry

    return compile_active_pack_registry(base_capability_registry())
