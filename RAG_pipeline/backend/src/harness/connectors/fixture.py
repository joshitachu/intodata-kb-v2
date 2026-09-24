"""Deterministic read-only connector used for contract and fixture testing."""

from __future__ import annotations

from .models import (
    ConnectorConfig,
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)

FIXTURE_CAPABILITY_ID = "connector.fixture.search"
_DATASETS = {
    "default": (
        ("runbook", "Payments API runbook", "Restart only after checking queue depth."),
        ("ownership", "Service ownership", "The platform team owns the Payments API."),
        ("release", "Release policy", "Production releases require an approved change."),
    )
}


class FixtureKnowledgeConnector:
    def validate_config(self, config: dict) -> dict:
        parsed = ConnectorConfig.model_validate(config)
        if parsed.dataset not in _DATASETS:
            raise ValueError("Unknown fixture dataset")
        return parsed.model_dump()

    async def health(
        self, config: dict, credential_reference: str | None
    ) -> ConnectorHealthResult:
        _ = credential_reference
        self.validate_config(config)
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.HEALTHY,
            summary="Fixture dataset is available",
        )

    async def read(
        self, capability_id: str, query: ConnectorQuery, config: dict,
        credential_reference: str | None = None,
        project_id: str | None = None,
    ) -> ConnectorReadResult:
        _ = credential_reference
        _ = project_id
        if capability_id != FIXTURE_CAPABILITY_ID:
            raise ValueError("Unsupported connector capability")
        parsed = ConnectorConfig.model_validate(self.validate_config(config))
        needle = query.query.casefold()
        matches = [item for item in _DATASETS[parsed.dataset] if needle in " ".join(item).casefold()]
        limit = min(query.limit, parsed.max_results)
        return ConnectorReadResult(
            items=[
                ConnectorResultItem(
                    source_ref=f"fixture:{key}", title=title, excerpt=excerpt, metadata={"dataset": parsed.dataset}
                )
                for key, title, excerpt in matches[:limit]
            ],
            truncated=len(matches) > limit,
        )


def default_connector_provider_registry():
    """Compatibility import for existing fixture users."""
    from .catalog import default_connector_provider_registry as _registry

    return _registry()
