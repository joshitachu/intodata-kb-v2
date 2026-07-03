"""Provider registry (Factory) for RAG chat.

Resolves the provider from the ``provider/`` segment of a selected model id and
returns it paired with the bare provider-native model name. This is the single
place provider selection happens — adding a provider is registering an adapter
here, never a new branch in the call sites.
"""
from __future__ import annotations

import logging

from src.generation.providers.anthropic_provider import AnthropicProvider
from src.generation.providers.base import ChatProvider
from src.generation.providers.ollama_provider import OllamaProvider
from src.generation.providers.openai_compat import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, ChatProvider] | None = None


def _build_default_registry() -> dict[str, ChatProvider]:
    """Construct the provider adapters wired for this deployment.

    Read from settings at build time; clients inside each adapter are still
    created lazily on first use. Slices 2-4 register openai/deepseek/anthropic/
    google adapters here.
    """
    from src.config import settings

    providers: dict[str, ChatProvider] = {
        "kimi": OpenAICompatibleProvider(
            provider_id="kimi",
            base_url="https://api.moonshot.ai/v1",
            api_key=settings.kimi_api_key,
            timeout=settings.kimi_timeout,
            temperature=settings.kimi_temperature,
            # Moonshot-specific: disable server-side reasoning tokens.
            extra_body={"thinking": {"type": "disabled"}},
        ),
        "openai": OpenAICompatibleProvider(
            provider_id="openai",
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout,
            temperature=settings.cloud_llm_temperature,
        ),
        "deepseek": OpenAICompatibleProvider(
            provider_id="deepseek",
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=settings.deepseek_timeout,
            temperature=settings.cloud_llm_temperature,
        ),
        "anthropic": AnthropicProvider(
            api_key=settings.anthropic_api_key,
            timeout=settings.anthropic_timeout,
            temperature=settings.cloud_llm_temperature,
        ),
        "ollama": OllamaProvider(
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout,
        ),
    }
    return providers


def _registry() -> dict[str, ChatProvider]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_default_registry()
    return _REGISTRY


def registered_provider_ids() -> frozenset[str]:
    return frozenset(_registry().keys())


def parse_model_id(model_id: str) -> tuple[str, str]:
    """Split ``provider/model`` into (provider_id, bare_model_name).

    A value with no ``/`` is treated as a bare model for the default provider
    (legacy overrides stored before the provider/model convention).
    """
    from src.config import settings

    raw = (model_id or "").strip()
    provider, sep, name = raw.partition("/")
    if sep and provider and name:
        return provider, name
    # No provider prefix — fall back to the default provider.
    default_provider, _, _ = settings.rag_default_model.partition("/")
    logger.warning(
        "rag chat model %r has no provider prefix; routing to default provider %r",
        raw,
        default_provider,
    )
    return default_provider, raw


def get_provider(provider_id: str) -> ChatProvider:
    reg = _registry()
    provider = reg.get(provider_id)
    if provider is None:
        raise ValueError(
            f"No RAG chat provider registered for {provider_id!r}. "
            f"Registered: {sorted(reg.keys())}"
        )
    return provider


def resolve(model_id: str) -> tuple[ChatProvider, str]:
    """Return (provider, bare_model_name) for a selected ``provider/model`` id."""
    provider_id, bare_model = parse_model_id(model_id)
    return get_provider(provider_id), bare_model


async def close_all() -> None:
    """Close every registered provider's client. Call on shutdown."""
    if _REGISTRY is None:
        return
    for provider in _REGISTRY.values():
        await provider.aclose()


def reset_for_tests() -> None:
    """Drop the built registry so the next access rebuilds from settings."""
    global _REGISTRY
    _REGISTRY = None
