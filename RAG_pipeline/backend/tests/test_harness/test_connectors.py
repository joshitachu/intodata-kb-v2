import pytest
import httpx
import json
from pydantic import ValidationError

from src.harness.connectors.catalog import default_connector_provider_registry, load_connector_catalog
from src.harness.connectors.fixture import FIXTURE_CAPABILITY_ID
from src.harness.connectors.http_elastic import ElasticHttpConnector
from src.harness.connectors.models import (
    CapabilityBindingCreate, ConnectorAuthMode, ConnectorInstanceCreate,
    ConnectorInstanceUpdate, ConnectorQuery,
)
from src.harness.connectors.repository import ConnectorRepository
from src.harness.connectors.service import ConnectorPolicyError, ConnectorService, instance_view, redact


async def _public_host(_host: str) -> tuple[str, ...]:
    return ("8.8.8.8",)


def _elastic_config(**overrides) -> dict:
    return {
        "base_url": "https://search.example.com",
        "allowed_hosts": ["search.example.com"],
        "index": "knowledge",
        "max_response_bytes": 4096,
        **overrides,
    }


@pytest.fixture
def service(db_session):
    return ConnectorService(ConnectorRepository(db_session), default_connector_provider_registry())


@pytest.mark.asyncio
async def test_fixture_connector_health_scope_enable_and_read(service):
    row = await service.create(ConnectorInstanceCreate(
        name="fixture", provider_key="fixture_knowledge", config={"max_results": 2}
    ))
    await service.bind(row.id, CapabilityBindingCreate(
        capability_id=FIXTURE_CAPABILITY_ID, project_ids=["alpha"], domains=["operations"]
    ))

    health = await service.check_health(row.id)
    assert health.status == "healthy"
    result = await service.read(
        row.id, FIXTURE_CAPABILITY_ID, "alpha", "operations", ConnectorQuery(query="payments")
    )
    assert [item.source_ref for item in result.items] == ["fixture:runbook", "fixture:ownership"]

    with pytest.raises(ConnectorPolicyError, match="project scope"):
        await service.read(row.id, FIXTURE_CAPABILITY_ID, "beta", "operations", ConnectorQuery(query="payments"))

    await service.update(row.id, ConnectorInstanceUpdate(enabled=False))
    with pytest.raises(ConnectorPolicyError, match="unavailable"):
        await service.read(row.id, FIXTURE_CAPABILITY_ID, "alpha", "operations", ConnectorQuery(query="payments"))


@pytest.mark.asyncio
async def test_credential_reference_is_never_exposed(service):
    row = await service.create(ConnectorInstanceCreate(
        name="credential fixture", provider_key="fixture_knowledge",
        auth_mode=ConnectorAuthMode.CREDENTIAL_REFERENCE,
        credential_reference="vault:connectors/fixture", config={},
    ))
    view = instance_view(row).model_dump(mode="json")
    assert view["credential_present"] is True
    assert view["credential_reference_type"] == "vault"
    assert "vault:connectors/fixture" not in str(view)
    assert "credential_reference" not in view


@pytest.mark.asyncio
async def test_invalid_config_and_binding_are_rejected(service):
    with pytest.raises(ValueError, match="Unknown fixture dataset"):
        await service.create(ConnectorInstanceCreate(
            name="bad", provider_key="fixture_knowledge", config={"dataset": "missing"}
        ))
    row = await service.create(ConnectorInstanceCreate(name="good", provider_key="fixture_knowledge"))
    with pytest.raises(ConnectorPolicyError, match="not provided"):
        await service.bind(row.id, CapabilityBindingCreate(capability_id="connector.fixture.write"))


def test_recursive_redaction_covers_sensitive_keys_and_configured_fields():
    value = {"token": "abc", "nested": [{"custom": "secret", "safe": "yes"}]}
    assert redact(value, {"custom"}) == {
        "token": "[REDACTED]", "nested": [{"custom": "[REDACTED]", "safe": "yes"}]
    }


def test_connector_types_and_capabilities_are_loaded_from_validated_catalog_data():
    catalog = load_connector_catalog()
    registry = default_connector_provider_registry()

    assert {definition.provider_key for definition in catalog} == {"fixture_knowledge", "http_elastic"}
    capability = registry.capability("http_elastic", "connector.http_elastic.search")
    assert capability.action == "read_connector_evidence"
    assert registry.manifest("fixture_knowledge").capability_ids == (FIXTURE_CAPABILITY_ID,)


@pytest.mark.asyncio
async def test_http_elastic_adapter_uses_mocked_transport_and_normalizes_evidence():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/_cluster/health":
            return httpx.Response(200, json={"status": "green"})
        assert request.url.path == "/knowledge/_search"
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "size": 10,
            "query": {"simple_query_string": {"query": "queue"}},
            "_source": True,
        }
        return httpx.Response(200, json={
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {"_id": "a1", "_index": "knowledge", "_source": {"title": "Runbook", "summary": "Check the queue."}},
                    {"_id": "a2", "_index": "knowledge", "_source": {"name": "Owner", "description": "Platform team."}},
                ],
            }
        })

    adapter = ElasticHttpConnector(
        transport=httpx.MockTransport(handler), host_resolver=_public_host
    )
    health = await adapter.health(_elastic_config(), "vault:opaque-reference")
    result = await adapter.read("connector.http_elastic.search", ConnectorQuery(query="queue"), _elastic_config())

    assert health.status.value == "healthy"
    assert [item.source_ref for item in result.items] == ["search:knowledge:a1", "search:knowledge:a2"]
    assert result.items[0].excerpt == "Check the queue."
    assert result.truncated is False
    assert [request.url.path for request in requests] == ["/_cluster/health", "/knowledge/_search"]


@pytest.mark.asyncio
async def test_http_elastic_adapter_rejects_private_dns_redirects_and_oversized_responses():
    async def private_host(_host: str) -> tuple[str, ...]:
        return ("127.0.0.1",)

    private_adapter = ElasticHttpConnector(host_resolver=private_host)
    with pytest.raises(ValueError, match="non-public"):
        await private_adapter.health(_elastic_config(), None)

    redirect_adapter = ElasticHttpConnector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(302, headers={"location": "https://elsewhere"})),
        host_resolver=_public_host,
    )
    with pytest.raises(ValueError, match="redirect"):
        await redirect_adapter.health(_elastic_config(), None)

    large_adapter = ElasticHttpConnector(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 1025)),
        host_resolver=_public_host,
    )
    with pytest.raises(ValueError, match="size limit"):
        await large_adapter.health(_elastic_config(max_response_bytes=1024), None)

    with pytest.raises(ValidationError, match="traversal"):
        ElasticHttpConnector().validate_config(_elastic_config(search_path="/../_search"))
