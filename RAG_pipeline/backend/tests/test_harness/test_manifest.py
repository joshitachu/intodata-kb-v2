from __future__ import annotations

from src.harness.manifest import ManifestScope, build_runtime_capability_manifest
from src.harness.models import (
    CapabilityDefinition,
    CapabilityHealth,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.harness.registry import CapabilityRegistry


def test_runtime_manifest_hides_provider_metadata_and_raw_config() -> None:
    registry = CapabilityRegistry([
        CapabilityDefinition(
            id="connector.secret.search",
            label="Search external system",
            provider_type=HarnessProviderType.SOURCE,
            provider_id="secret.provider",
            action="search",
            input_schema={"type": "object", "required": ["query"]},
            output_type="evidence_items",
            risk_tier=HarnessRiskTier.READ_ONLY,
            metadata={
                "execution_surface": "internal",
                "api_key": "should-not-leak",
                "activity_label": "Searching external system",
                "redaction_policy": {"drop": ["api_key"]},
            },
        )
    ])

    manifest = build_runtime_capability_manifest(registry)

    assert manifest.total == 1
    tool = manifest.tools[0]
    dumped = tool.model_dump()
    assert dumped["id"] == "connector.secret.search"
    assert dumped["activity_label"] == "Searching external system"
    assert "provider_id" not in dumped
    assert "metadata" not in dumped
    assert "should-not-leak" not in str(dumped)
    assert "execution_surface" not in str(dumped)


def test_runtime_manifest_filters_unavailable_pack_and_permission_mismatch() -> None:
    registry = CapabilityRegistry([
        CapabilityDefinition(
            id="docs.generate",
            label="Generate docs",
            provider_type=HarnessProviderType.DOCUMENTATION,
            provider_id="mcp.docs",
            action="generate",
            output_type="receipt",
            risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
            required_permissions=["manage_projects"],
            packs=["documentation"],
        ),
        CapabilityDefinition(
            id="logs.search",
            label="Search logs",
            provider_type=HarnessProviderType.LOG,
            provider_id="logs",
            action="search",
            output_type="evidence_items",
            risk_tier=HarnessRiskTier.READ_ONLY,
            packs=["operations"],
        ),
        CapabilityDefinition(
            id="disabled",
            label="Disabled",
            provider_type=HarnessProviderType.SOURCE,
            provider_id="source",
            action="read",
            output_type="none",
            risk_tier=HarnessRiskTier.READ_ONLY,
            health=CapabilityHealth.UNAVAILABLE,
        ),
    ])

    manifest = build_runtime_capability_manifest(
        registry,
        scope=ManifestScope(permissions=set(), packs={"operations"}),
    )

    assert [tool.id for tool in manifest.tools] == ["logs.search"]
    assert manifest.filtered == 2
