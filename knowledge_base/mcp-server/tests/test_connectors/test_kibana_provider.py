import pytest

from mcp_server.connectors.domain import ConnectorInstanceConfig
from mcp_server.connectors.providers.kibana import KibanaDiscoveryProvider


class FakeTransport:
    async def request_json(self, method, url, **kwargs):
        if url.endswith("/api/spaces/space"):
            return [{"id": "default", "name": "Default"}], 200
        if url.endswith("/api/data_views/data_view/logs"):
            return {"data_view": {"id": "logs", "title": "logs-*", "timeFieldName": "@timestamp", "fields": [{"name": "@timestamp"}, {"name": "message"}], "runtimeFieldMap": {"service.name": {}}}}, 200
        raise AssertionError(url)


def _instance():
    return ConnectorInstanceConfig("con", "elastic_stack", "Elastic", {"endpoint": "https://elastic.example", "kibana_endpoint": "https://kibana.example", "auth_type": "api_key"})


@pytest.mark.asyncio
async def test_kibana_uses_documented_spaces_and_data_view_apis():
    provider = KibanaDiscoveryProvider(FakeTransport())
    assert await provider.list_spaces(_instance(), "credential") == [{"id": "default", "name": "Default"}]
    source = await provider.get_data_view(_instance(), "credential", "logs")
    assert source.locator == "logs-*"
    assert source.fields["timestamp_field"] == "@timestamp"
