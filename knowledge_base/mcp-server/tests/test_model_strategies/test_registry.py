from __future__ import annotations

import pytest

from mcp_server.model_strategies import (
    get_model_strategy,
    list_model_descriptors,
    normalize_model_id,
    resolve_generation_models,
    supported_model_ids,
)
from mcp_server.model_strategies.registry import create_model_provider


def test_model_registry_exposes_current_discovery_models():
    assert supported_model_ids() == frozenset({
        "openai/gpt-5.5",
        "openai/gpt-5.4",
        "openai/gpt-5.4-mini",
        "openai/gpt-5",
        "openai/gpt-4.5",
        "openai/gpt-4.1",
        "openai/gpt-4o",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-reasoner",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-7",
        "google/gemini-3.1-pro-preview",
        "google/gemini-2.5-pro",
        "google/gemini-2.5-flash-lite",
        "google/gemini-2.5-flash",
        "ollama/gemma3:12b",
        "ollama/gemma3:4b",
        "claude-cli",
    })


@pytest.mark.parametrize(
    ("model_id", "provider_id"),
    [
        ("custom", "custom"),
        ("claude-cli", "claude-cli"),
        ("deepseek/deepseek-chat", "deepseek"),
        ("google/gemini-2.5-flash", "google"),
        ("openai/gpt-4o", "openai"),
        ("anthropic/claude-sonnet-4-6", "anthropic"),
        ("ollama/gemma3:12b", "ollama"),
    ],
)
def test_model_registry_resolves_supported_models(model_id, provider_id):
    assert get_model_strategy(model_id).provider_id == provider_id


@pytest.mark.parametrize("model_id", ["moonshot/kimi-k2.6", "kimi-k2.6", "openai/"])
def test_model_registry_rejects_unsupported_or_malformed_models(model_id):
    with pytest.raises(ValueError):
        strategy = get_model_strategy(model_id)
        strategy.parse_model_name(model_id)


def test_descriptors_keep_claude_cli_and_ollama_capabilities():
    descriptors = {descriptor.id: descriptor for descriptor in list_model_descriptors()}

    assert "custom" not in descriptors
    assert descriptors["claude-cli"].supports_analysis is True
    assert descriptors["claude-cli"].supports_writing is True
    assert descriptors["ollama/gemma3:12b"].is_local is True


def test_custom_resolution_uses_default_analysis_model():
    resolved = resolve_generation_models(
        provider_type="custom",
        model=None,
        write_model=None,
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "anthropic/claude-sonnet-4-6"
    assert resolved.analysis_model == "anthropic/claude-sonnet-4-6"
    assert resolved.write_model == "anthropic/claude-sonnet-4-6"
    assert resolved.uses_custom_harness is True
    assert resolved.uses_claude_cli is False


def test_explicit_model_override_wins_over_project_provider():
    resolved = resolve_generation_models(
        provider_type="custom",
        model="openai/gpt-4o",
        write_model=None,
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "openai/gpt-4o"
    assert resolved.analysis_model == "openai/gpt-4o"
    assert resolved.write_model == "anthropic/claude-sonnet-4-6"


def test_write_model_override_is_preserved():
    resolved = resolve_generation_models(
        provider_type="google/gemini-2.5-flash",
        model=None,
        write_model="anthropic/claude-sonnet-4-6",
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.analysis_model == "google/gemini-2.5-flash"
    assert resolved.write_model == "anthropic/claude-sonnet-4-6"


def test_deepseek_provider_is_distinct_from_openai():
    resolved = resolve_generation_models(
        provider_type="deepseek/deepseek-chat",
        model=None,
        write_model="openai/gpt-5.4-mini",
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "deepseek/deepseek-chat"
    assert resolved.analysis_model == "deepseek/deepseek-chat"
    assert resolved.write_model == "openai/gpt-5.4-mini"


def test_first_party_providers_use_official_adapters(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    openai_provider = create_model_provider("openai/gpt-5.4-mini")
    anthropic_provider = create_model_provider("anthropic/claude-sonnet-4-6")
    google_provider = create_model_provider("google/gemini-2.5-flash")
    deepseek_provider = create_model_provider("deepseek/deepseek-chat")

    assert type(openai_provider).__name__ == "OpenAIOfficialProvider"
    assert openai_provider.base_url == "https://api.openai.com/v1"
    assert type(anthropic_provider).__name__ == "AnthropicProvider"
    assert anthropic_provider.base_url == "https://api.anthropic.com"
    assert type(google_provider).__name__ == "GoogleGeminiProvider"
    assert google_provider.base_url == "https://generativelanguage.googleapis.com"
    assert google_provider.supports_tools is False
    assert type(deepseek_provider).__name__ == "OpenAICompatibleProvider"
    assert deepseek_provider.base_url == "https://api.deepseek.com/v1"


def test_legacy_openai_deepseek_id_normalizes_to_deepseek():
    assert normalize_model_id("openai/deepseek-v4-pro") == "deepseek/deepseek-v4-pro"

    resolved = resolve_generation_models(
        provider_type="openai/deepseek-v4-pro",
        model=None,
        write_model="openai/deepseek-chat",
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "deepseek/deepseek-v4-pro"
    assert resolved.analysis_model == "deepseek/deepseek-v4-pro"
    assert resolved.write_model == "deepseek/deepseek-chat"


def test_claude_cli_write_model_override_is_allowed():
    resolved = resolve_generation_models(
        provider_type="google/gemini-2.5-flash",
        model=None,
        write_model="claude-cli",
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.analysis_model == "google/gemini-2.5-flash"
    assert resolved.write_model == "claude-cli"


def test_claude_cli_resolution_skips_custom_harness():
    resolved = resolve_generation_models(
        provider_type="claude-cli",
        model=None,
        write_model=None,
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "claude-cli"
    assert resolved.analysis_model == "sonnet"
    assert resolved.uses_custom_harness is False
    assert resolved.uses_claude_cli is True


def test_claude_cli_resolution_uses_configured_cli_model():
    resolved = resolve_generation_models(
        provider_type="claude-cli",
        model=None,
        write_model=None,
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
        default_claude_cli_model="opus",
    )

    assert resolved.provider_type == "claude-cli"
    assert resolved.analysis_model == "opus"
    assert resolved.uses_claude_cli is True


def test_resolution_accepts_google_provider():
    resolved = resolve_generation_models(
        provider_type="google/gemini-2.5-flash",
        model=None,
        write_model="anthropic/claude-sonnet-4-6",
        default_provider="custom",
        default_analysis_model="anthropic/claude-sonnet-4-6",
        default_write_model="anthropic/claude-sonnet-4-6",
    )

    assert resolved.provider_type == "google/gemini-2.5-flash"
    assert resolved.analysis_model == "google/gemini-2.5-flash"
    assert resolved.write_model == "anthropic/claude-sonnet-4-6"


def test_resolution_rejects_unsupported_provider():
    with pytest.raises(ValueError, match="moonshot"):
        resolve_generation_models(
            provider_type="moonshot/kimi-k2.6",
            model=None,
            write_model=None,
            default_provider="custom",
            default_analysis_model="anthropic/claude-sonnet-4-6",
            default_write_model="anthropic/claude-sonnet-4-6",
        )


def test_resolution_rejects_malformed_provider_model():
    with pytest.raises(ValueError, match="expected 'openai/<model>'"):
        resolve_generation_models(
            provider_type="openai/",
            model=None,
            write_model=None,
            default_provider="custom",
            default_analysis_model="anthropic/claude-sonnet-4-6",
            default_write_model="anthropic/claude-sonnet-4-6",
        )
