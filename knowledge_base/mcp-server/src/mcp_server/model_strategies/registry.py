"""Registry and resolution helpers for doc-generation models."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.model_strategies.strategies import (
    AnthropicModelStrategy,
    ClaudeCliStrategy,
    CustomHarnessStrategy,
    DeepSeekModelStrategy,
    GoogleModelStrategy,
    KimiModelStrategy,
    ModelDescriptorData,
    ModelProviderStrategy,
    OllamaModelStrategy,
    OpenCodeCliStrategy,
    OpenCodeGoModelStrategy,
    OpenAIModelStrategy,
    OpenRouterModelStrategy,
)


@dataclass(frozen=True)
class GenerationModelResolution:
    provider_type: str
    analysis_model: str
    write_model: str
    uses_custom_harness: bool
    uses_claude_cli: bool
    uses_opencode_cli: bool = False


_STRATEGIES: tuple[ModelProviderStrategy, ...] = (
    CustomHarnessStrategy(),
    OpenAIModelStrategy(),
    DeepSeekModelStrategy(),
    KimiModelStrategy(),
    OpenRouterModelStrategy(),
    AnthropicModelStrategy(),
    GoogleModelStrategy(),
    OllamaModelStrategy(),
    ClaudeCliStrategy(),
    OpenCodeCliStrategy(),
    OpenCodeGoModelStrategy(),
)


def list_model_strategies() -> list[ModelProviderStrategy]:
    return list(_STRATEGIES)


def get_model_strategy(model_id: str) -> ModelProviderStrategy:
    normalized = normalize_model_id(model_id)
    for strategy in _STRATEGIES:
        if strategy.owns(normalized):
            return strategy
    raise ValueError(_unsupported_model_message(model_id))


def list_model_descriptors() -> list[ModelDescriptorData]:
    """Real-provider descriptors from the shared catalogue file, plus any
    code-defined special descriptors (e.g. claude-cli). The JSON catalogue is the
    single source of truth; strategies no longer hardcode descriptor data."""
    from mcp_server.model_strategies.catalogue import load_catalogue

    descriptors: list[ModelDescriptorData] = list(load_catalogue())
    # Meta/code-only strategies (claude-cli) still contribute their own descriptor.
    for strategy in _STRATEGIES:
        descriptors.extend(strategy.descriptors())
    return descriptors


def supported_model_ids() -> frozenset[str]:
    return frozenset(descriptor.id for descriptor in list_model_descriptors())


def normalize_model_id(model_id: str) -> str:
    """Normalize legacy provider/model aliases before provider resolution."""

    normalized = model_id.strip()
    provider, sep, model_name = normalized.partition("/")
    if provider == "openai" and sep and model_name.startswith("deepseek"):
        return f"deepseek/{model_name}"
    return normalized


def create_model_provider(
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    ollama_base_url: str | None = None,
):
    model_id = normalize_model_id(model_id)
    strategy = get_model_strategy(model_id)
    return strategy.create_model_provider(
        model_id,
        api_key=api_key,
        base_url=base_url,
        ollama_base_url=ollama_base_url,
    )


def resolve_generation_models(
    *,
    provider_type: str | None,
    model: str | None,
    write_model: str | None,
    default_provider: str,
    default_analysis_model: str,
    default_write_model: str,
    default_claude_cli_model: str = "sonnet",
) -> GenerationModelResolution:
    resolved_provider = normalize_model_id(provider_type or default_provider)

    if resolved_provider == "claude-cli":
        cli_model = (model or default_claude_cli_model or "").strip()
        return GenerationModelResolution(
            provider_type="claude-cli",
            analysis_model=cli_model,
            write_model=write_model or default_write_model,
            uses_custom_harness=False,
            uses_claude_cli=True,
            uses_opencode_cli=False,
        )

    if resolved_provider == "opencode-cli":
        requested_model = normalize_model_id(model or "")
        if requested_model in {"", "opencode", "opencode-cli"}:
            requested_model = ""
        elif "/" not in requested_model:
            raise ValueError(
                "OpenCode requires a provider/model route, for example "
                "'openai/gpt-5.6-sol'."
            )
        return GenerationModelResolution(
            provider_type="opencode-cli",
            analysis_model=requested_model or "opencode",
            write_model=requested_model or "opencode",
            uses_custom_harness=False,
            uses_claude_cli=False,
            uses_opencode_cli=True,
        )

    if model and "/" in model:
        analysis_model = normalize_model_id(model)
        resolved_provider = analysis_model
    elif resolved_provider == "custom":
        analysis_model = normalize_model_id(default_analysis_model)
        resolved_provider = analysis_model
    else:
        analysis_model = normalize_model_id(resolved_provider)
        resolved_provider = analysis_model

    strategy = get_model_strategy(analysis_model)
    if strategy.provider_id in {"custom", "claude-cli"}:
        raise ValueError(_unsupported_model_message(analysis_model))
    strategy.parse_model_name(analysis_model)

    resolved_write_model = normalize_model_id(write_model or default_write_model)
    if resolved_write_model:
        if "/" not in resolved_write_model:
            write_strategy = get_model_strategy(resolved_write_model)
            if resolved_write_model == analysis_model:
                pass
            elif not write_strategy.supports_writing:
                raise ValueError(
                    f"Invalid write model {resolved_write_model!r}; expected 'provider/model'"
                )
        else:
            write_strategy = get_model_strategy(resolved_write_model)
            if not write_strategy.supports_writing:
                raise ValueError(f"Model {resolved_write_model!r} does not support doc writing")
            write_strategy.parse_model_name(resolved_write_model)

    return GenerationModelResolution(
        provider_type=resolved_provider,
        analysis_model=analysis_model,
        write_model=resolved_write_model,
        uses_custom_harness=True,
        uses_claude_cli=False,
        uses_opencode_cli=False,
    )


def _unsupported_model_message(model_id: str) -> str:
    return (
        f"Unsupported model/provider: {model_id!r}. "
        "Supported: 'custom', 'openai/<model>', 'deepseek/<model>', 'kimi/<model>', "
        "'openrouter/<model>', 'anthropic/<model>', 'google/<model>', 'ollama/<model>', "
        "'claude-cli', or 'opencode-cli'."
    )
