"""Registry and resolution helpers for doc-generation models."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.model_strategies.strategies import (
    AnthropicModelStrategy,
    ClaudeCliStrategy,
    CustomHarnessStrategy,
    GoogleModelStrategy,
    ModelDescriptorData,
    ModelProviderStrategy,
    OllamaModelStrategy,
    OpenAIModelStrategy,
)


@dataclass(frozen=True)
class GenerationModelResolution:
    provider_type: str
    analysis_model: str
    write_model: str
    uses_custom_harness: bool
    uses_claude_cli: bool


_STRATEGIES: tuple[ModelProviderStrategy, ...] = (
    CustomHarnessStrategy(),
    OpenAIModelStrategy(),
    AnthropicModelStrategy(),
    GoogleModelStrategy(),
    OllamaModelStrategy(),
    ClaudeCliStrategy(),
)


def list_model_strategies() -> list[ModelProviderStrategy]:
    return list(_STRATEGIES)


def get_model_strategy(model_id: str) -> ModelProviderStrategy:
    normalized = model_id.strip()
    for strategy in _STRATEGIES:
        if strategy.owns(normalized):
            return strategy
    raise ValueError(_unsupported_model_message(model_id))


def list_model_descriptors() -> list[ModelDescriptorData]:
    descriptors: list[ModelDescriptorData] = []
    for strategy in _STRATEGIES:
        descriptors.extend(strategy.descriptors())
    return descriptors


def supported_model_ids() -> frozenset[str]:
    return frozenset(descriptor.id for descriptor in list_model_descriptors())


def create_model_provider(
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    ollama_base_url: str | None = None,
):
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
    resolved_provider = provider_type or default_provider

    if resolved_provider == "claude-cli":
        cli_model = (model or default_claude_cli_model or "").strip()
        return GenerationModelResolution(
            provider_type="claude-cli",
            analysis_model=cli_model,
            write_model=write_model or default_write_model,
            uses_custom_harness=False,
            uses_claude_cli=True,
        )

    if model and "/" in model:
        analysis_model = model
        resolved_provider = model
    elif resolved_provider == "custom":
        analysis_model = default_analysis_model
        resolved_provider = analysis_model
    else:
        analysis_model = resolved_provider

    strategy = get_model_strategy(analysis_model)
    if strategy.provider_id in {"custom", "claude-cli"}:
        raise ValueError(_unsupported_model_message(analysis_model))
    strategy.parse_model_name(analysis_model)

    resolved_write_model = write_model or default_write_model
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
    )


def _unsupported_model_message(model_id: str) -> str:
    return (
        f"Unsupported model/provider: {model_id!r}. "
        "Supported: 'custom', 'openai/<model>', 'anthropic/<model>', 'google/<model>', "
        "'ollama/<model>', or 'claude-cli'."
    )
