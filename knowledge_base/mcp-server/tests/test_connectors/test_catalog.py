import pytest

from mcp_server.connectors.catalog import ConnectorCatalog
from mcp_server.connectors.domain import (
    ConnectorCapability,
    ConnectorManagedBy,
    ConnectorProviderManifest,
)
from mcp_server.connectors.providers.talend_tmc import TALEND_TMC_MANIFEST


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


def test_manifest_credentials_default_to_this_services_connector_store():
    assert _manifest().managed_by is ConnectorManagedBy.CONNECTOR_STORE


def test_talend_tmc_declares_its_credential_home_as_runtime_sources():
    """TMC stays listed but is not connectable here (build plan R14).

    Its credential lives in the RAG backend's `runtime_sources` tables next to
    the scheduler that reads it; a connect form in this service would write a
    secret nothing ever loads.
    """
    assert TALEND_TMC_MANIFEST.managed_by == "runtime_sources"
    assert TALEND_TMC_MANIFEST.managed_by is not ConnectorManagedBy.CONNECTOR_STORE
