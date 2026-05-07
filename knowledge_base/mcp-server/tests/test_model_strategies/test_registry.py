from __future__ import annotations

import pytest

from mcp_server.model_strategies import (
    get_model_strategy,
    list_model_descriptors,
    resolve_generation_models,
    supported_model_ids,
)


def test_model_registry_exposes_current_discovery_models():
    assert supported_model_ids() == frozenset({
        "openai/deepseek-v4-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-7",
        "moonshot/kimi-k2.6",
        "ollama/gemma3:12b",
        "ollama/gemma3:4b",
        "claude-cli",
    })


@pytest.mark.parametrize(
    ("model_id", "provider_id"),
    [
        ("custom", "custom"),
        ("claude-cli", "claude-cli"),
        ("moonshot/kimi-k2.6", "moonshot"),
        ("openai/gpt-4o", "openai"),
        ("anthropic/claude-sonnet-4-6", "anthropic"),
        ("ollama/gemma3:12b", "ollama"),
    ],
)
def test_model_registry_resolves_supported_models(model_id, provider_id):
    assert get_model_strategy(model_id).provider_id == provider_id


@pytest.mark.parametrize("model_id", ["google/gemini-pro", "kimi-k2.6", "openai/"])
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
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
    )

    assert resolved.provider_type == "moonshot/kimi-k2.6"
    assert resolved.analysis_model == "moonshot/kimi-k2.6"
    assert resolved.write_model == "moonshot/kimi-k2.6"
    assert resolved.uses_custom_harness is True
    assert resolved.uses_claude_cli is False


def test_explicit_model_override_wins_over_project_provider():
    resolved = resolve_generation_models(
        provider_type="custom",
        model="openai/gpt-4o",
        write_model=None,
        default_provider="custom",
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
    )

    assert resolved.provider_type == "openai/gpt-4o"
    assert resolved.analysis_model == "openai/gpt-4o"
    assert resolved.write_model == "moonshot/kimi-k2.6"


def test_write_model_override_is_preserved():
    resolved = resolve_generation_models(
        provider_type="moonshot/kimi-k2.6",
        model=None,
        write_model="anthropic/claude-sonnet-4-6",
        default_provider="custom",
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
    )

    assert resolved.analysis_model == "moonshot/kimi-k2.6"
    assert resolved.write_model == "anthropic/claude-sonnet-4-6"


def test_claude_cli_write_model_override_is_allowed():
    resolved = resolve_generation_models(
        provider_type="moonshot/kimi-k2.6",
        model=None,
        write_model="claude-cli",
        default_provider="custom",
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
    )

    assert resolved.analysis_model == "moonshot/kimi-k2.6"
    assert resolved.write_model == "claude-cli"


def test_claude_cli_resolution_skips_custom_harness():
    resolved = resolve_generation_models(
        provider_type="claude-cli",
        model=None,
        write_model=None,
        default_provider="custom",
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
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
        default_analysis_model="moonshot/kimi-k2.6",
        default_write_model="moonshot/kimi-k2.6",
        default_claude_cli_model="opus",
    )

    assert resolved.provider_type == "claude-cli"
    assert resolved.analysis_model == "opus"
    assert resolved.uses_claude_cli is True


def test_resolution_rejects_unsupported_provider():
    with pytest.raises(ValueError, match="google"):
        resolve_generation_models(
            provider_type="google/gemini-pro",
            model=None,
            write_model=None,
            default_provider="custom",
            default_analysis_model="moonshot/kimi-k2.6",
            default_write_model="moonshot/kimi-k2.6",
        )


def test_resolution_rejects_malformed_provider_model():
    with pytest.raises(ValueError, match="expected 'openai/<model>'"):
        resolve_generation_models(
            provider_type="openai/",
            model=None,
            write_model=None,
            default_provider="custom",
            default_analysis_model="moonshot/kimi-k2.6",
            default_write_model="moonshot/kimi-k2.6",
        )
