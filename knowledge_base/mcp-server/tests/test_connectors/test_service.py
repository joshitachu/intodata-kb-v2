import pytest

from mcp_server.connectors.service import _safe_config


def test_safe_config_allows_kibana_credential_reuse_switch():
    config = {
        "endpoint": "https://elastic.example",
        "kibana_reuse_elasticsearch_credentials": True,
    }

    assert _safe_config(config) == config


@pytest.mark.parametrize("key", ["credential", "api_key", "password", "authorization_header"])
def test_safe_config_still_rejects_secret_material(key: str):
    with pytest.raises(ValueError, match="write-only credential fields"):
        _safe_config({key: "secret"})


def test_safe_config_rejects_non_boolean_reuse_value():
    with pytest.raises(ValueError, match="write-only credential fields"):
        _safe_config({"kibana_reuse_elasticsearch_credentials": "yes"})
