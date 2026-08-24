import pytest

from mcp_server.connectors.catalog import ConnectorCatalog
from mcp_server.connectors.domain import ConnectorCapability, ConnectorProviderManifest


class FakeProvider:
    async def test(self, *args, **kwargs):
        return {}

    async def execute(self, *args, **kwargs):
        return None


def _manifest(capability: ConnectorCapability | None = None) -> ConnectorProviderManifest:
    return ConnectorProviderManifest(
        provider_id="fake", label="Fake", version="1", authentication_types=("token",),
        configuration_schema={}, secret_fields=("credential",),
        capabilities=(capability or ConnectorCapability("read", "Read", "read"),),
    )


def test_catalogue_rejects_duplicate_provider_ids():
    catalogue = ConnectorCatalog()
    catalogue.register(_manifest(), FakeProvider())
    with pytest.raises(ValueError, match="Duplicate"):
        catalogue.register(_manifest(), FakeProvider())


def test_catalogue_fails_closed_for_non_gather_or_write_capability():
    catalogue = ConnectorCatalog()
    catalogue.register(_manifest(ConnectorCapability("write", "Write", "write", read_only=False)), FakeProvider())
    with pytest.raises(ValueError, match="read-only Gather"):
        catalogue.validate_grant("fake", ["write"])
