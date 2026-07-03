"""Tests for the provider registry — model-id parsing and provider resolution."""
import pytest

from src.generation.providers import registry
from src.generation.providers.base import ChatProvider
from src.generation.providers.ollama_provider import OllamaProvider
from src.generation.providers.openai_compat import OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def test_parse_strips_provider_prefix():
    assert registry.parse_model_id("ollama/gemma3:12b") == ("ollama", "gemma3:12b")
    assert registry.parse_model_id("kimi/kimi-k2.5") == ("kimi", "kimi-k2.5")


def test_parse_openai_style_id():
    assert registry.parse_model_id("openai/gpt-4o") == ("openai", "gpt-4o")


def test_parse_no_prefix_falls_back_to_default_provider():
    # settings.rag_default_model defaults to 'kimi/kimi-k2.5' → default provider 'kimi'.
    provider_id, bare = registry.parse_model_id("gemma3:12b")
    assert provider_id == "kimi"
    assert bare == "gemma3:12b"


def test_default_registry_has_expected_providers():
    ids = registry.registered_provider_ids()
    assert {"kimi", "ollama", "openai", "deepseek", "anthropic", "google"} <= ids


# The providers offered by the shared model catalogue (data/model_catalogue.json
# in the MCP server). The RAG registry MUST implement every one so any model the
# dashboard offers is runnable. RAG cannot import the MCP file (separate service),
# so this set is the contract; update both together if the catalogue gains a
# provider.
CATALOGUE_PROVIDERS = {"openai", "deepseek", "anthropic", "google", "ollama"}


def test_registry_covers_every_catalogue_provider():
    ids = registry.registered_provider_ids()
    missing = CATALOGUE_PROVIDERS - ids
    assert not missing, f"RAG registry missing catalogue providers: {missing}"


def test_resolve_openai_and_deepseek_use_openai_compatible_adapter():
    for pid, model_id, bare in [
        ("openai", "openai/gpt-4o", "gpt-4o"),
        ("deepseek", "deepseek/deepseek-chat", "deepseek-chat"),
    ]:
        provider, name = registry.resolve(model_id)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.provider_id == pid
        assert name == bare


def test_resolve_ollama_returns_provider_and_bare_model():
    provider, bare = registry.resolve("ollama/gemma3:12b")
    assert isinstance(provider, OllamaProvider)
    assert bare == "gemma3:12b"


def test_resolve_kimi_uses_openai_compatible_adapter():
    provider, bare = registry.resolve("kimi/kimi-k2.5")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_id == "kimi"
    assert bare == "kimi-k2.5"


def test_resolve_unknown_provider_raises():
    with pytest.raises(ValueError, match="No RAG chat provider"):
        registry.resolve("mistral/mistral-large")  # provider not implemented


def test_registered_providers_are_chat_providers():
    for pid in registry.registered_provider_ids():
        assert isinstance(registry.get_provider(pid), ChatProvider)
