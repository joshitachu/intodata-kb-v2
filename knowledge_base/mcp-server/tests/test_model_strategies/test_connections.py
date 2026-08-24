"""Provider connection resolution is shared by every doc-generation surface."""
from __future__ import annotations

import pytest

from mcp_server.config import config
from mcp_server.model_strategies import provider_connection_config


@pytest.mark.parametrize(
    "model_id",
    ["openai/gpt-4o", "anthropic/claude-sonnet-4-6", "google/gemini-2.5-flash"],
)
def test_sdk_managed_providers_get_no_explicit_connection(model_id):
    assert provider_connection_config(model_id) == {"api_key": None, "base_url": None}


def test_openrouter_uses_its_own_key_and_base_url(monkeypatch):
    monkeypatch.setattr(config, "doc_generation_openrouter_api_key", "or-test", raising=False)
    resolved = provider_connection_config("openrouter/stealth/ox-alpha")
    assert resolved["api_key"] == "or-test"
    assert resolved["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_falls_back_to_the_generic_doc_generation_key(monkeypatch):
    monkeypatch.setattr(config, "doc_generation_openrouter_api_key", None, raising=False)
    monkeypatch.setattr(config, "doc_generation_api_key", "generic", raising=False)
    assert provider_connection_config("openrouter/stealth/ox-alpha")["api_key"] == "generic"


def test_route33_and_deepseek_keep_their_dedicated_settings(monkeypatch):
    monkeypatch.setattr(config, "doc_generation_route33_api_key", "r33", raising=False)
    monkeypatch.setattr(config, "doc_generation_deepseek_api_key", "ds", raising=False)
    assert provider_connection_config("route33/z-ai/glm-5.2")["api_key"] == "r33"
    assert provider_connection_config("deepseek/deepseek-chat")["api_key"] == "ds"


@pytest.mark.parametrize(
    "model_id",
    [
        "openrouter/stealth/ox-alpha",
        "route33/z-ai/glm-5.2",
        "deepseek/deepseek-chat",
        "anthropic/claude-sonnet-4-6",
        "ollama/gemma3:12b",
    ],
)
def test_every_call_site_delegates_to_the_shared_helper(model_id, monkeypatch):
    """One resolver, several surfaces — guards against the copies coming back."""
    from mcp_server.services import deep_dive_service, git_diff_patcher, reingestion_router

    monkeypatch.setattr(config, "doc_generation_openrouter_api_key", "or-test", raising=False)
    expected = provider_connection_config(model_id)
    assert deep_dive_service._provider_config(model_id) == expected
    assert git_diff_patcher._provider_config(model_id) == expected
    assert reingestion_router._expander_provider_config(model_id) == expected
