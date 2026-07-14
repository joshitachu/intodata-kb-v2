"""Isolated generic connector domain."""

from .catalog import ConnectorCatalogError, default_connector_provider_registry, load_connector_catalog
from .fixture import FIXTURE_CAPABILITY_ID, FixtureKnowledgeConnector
from .models import *  # noqa: F403
from .providers import ConnectorProvider, ConnectorProviderRegistry
from .repository import ConnectorNotFoundError, ConnectorRepository
from .service import ConnectorPolicyError, ConnectorService

__all__ = [
    "FIXTURE_CAPABILITY_ID", "FixtureKnowledgeConnector", "default_connector_provider_registry",
    "ConnectorCatalogError", "load_connector_catalog",
    "ConnectorProvider", "ConnectorProviderRegistry", "ConnectorNotFoundError",
    "ConnectorRepository", "ConnectorPolicyError", "ConnectorService",
]
