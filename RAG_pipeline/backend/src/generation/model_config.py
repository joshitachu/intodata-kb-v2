"""Superadmin-curated LLM answer-model standard.

The generation model everyone inherits is normally the deployment's env config.
A superadmin can override it (provider + model) via ``/admin/model-config``; the
override is held in-process, loaded at startup from ``system_config``, and
refreshed when changed. ``llm_client`` consults these helpers instead of reading
``settings`` directly, so the override applies to every generation path.

Only the ANSWER (generation) LLM is configurable here — embeddings and rerank
stay env-managed, because changing the embedding model forces a Qdrant re-index.
"""
from __future__ import annotations

import logging
import threading

from src.config import settings

logger = logging.getLogger(__name__)

# Curated allow-list: a superadmin may only pick vetted (provider, model) pairs
# so a typo can't silently break generation. Extend as models are validated.
ALLOWED_MODELS: dict[str, list[str]] = {
    "kimi": ["kimi-k2.5", "kimi-k2"],
    "ollama": ["gemma3:4b", "gemma3:12b", "llama3.1:8b"],
}

# Which settings attribute holds each provider's env-default model.
_ENV_MODEL_ATTR: dict[str, str] = {
    "kimi": "kimi_llm_model",
    "ollama": "ollama_llm_model",
}

_lock = threading.Lock()
_override: dict[str, str] | None = None  # {"provider": ..., "model": ...}


def is_allowed(provider: str, model: str) -> bool:
    # Must be a vetted model AND a provider actually enabled on this deployment
    # (settings.llm_providers) — so a superadmin can't select a provider ops disabled.
    return (
        provider in settings.llm_providers
        and model in ALLOWED_MODELS.get(provider, [])
    )


def allowed_models() -> dict[str, list[str]]:
    # Only offer providers enabled on this deployment.
    return {
        p: list(m)
        for p, m in ALLOWED_MODELS.items()
        if p in settings.llm_providers
    }


def set_override(provider: str, model: str) -> None:
    if not is_allowed(provider, model):
        raise ValueError(f"Model not allowed: {provider}/{model}")
    global _override
    with _lock:
        _override = {"provider": provider, "model": model}
    logger.info("Active LLM answer-model override set: %s / %s", provider, model)


def clear_override() -> None:
    global _override
    with _lock:
        _override = None


def get_override() -> dict[str, str] | None:
    return _override


def order_with_override(base: list[str]) -> list[str]:
    """Reorder a provider list so the override's provider is first (if set).

    Callers pass their own provider list (e.g. ``settings.llm_providers``) so
    the source of the base list stays patchable/testable at the call site.
    """
    override = _override
    ordered = list(base)
    if not override:
        return ordered
    provider = override["provider"]
    # Never resurrect a provider the deployment excluded from its chain.
    if provider not in ordered:
        logger.warning(
            "LLM override provider %s is not in the deployment chain %s — ignoring",
            provider,
            ordered,
        )
        return ordered
    return [provider] + [p for p in ordered if p != provider]


def effective_provider_chain() -> list[str]:
    """Provider order with the override's provider first; fallbacks preserved.
    Reads the live env provider list — for display/endpoint use."""
    return order_with_override(settings.llm_providers)


def model_for(provider: str) -> str:
    """Model id for a provider: the override if it targets this provider, else
    the deployment env default. Absent env mapping → empty string."""
    override = _override
    if override and override["provider"] == provider:
        return override["model"]
    attr = _ENV_MODEL_ATTR.get(provider)
    return getattr(settings, attr) if attr else ""


def load_from_row(llm_provider: str | None, llm_model: str | None) -> None:
    """Apply a persisted system_config override at startup (or clear if unset/invalid)."""
    if llm_provider and llm_model and is_allowed(llm_provider, llm_model):
        set_override(llm_provider, llm_model)
    else:
        if llm_provider or llm_model:
            logger.warning(
                "Ignoring persisted LLM model override %s/%s (not in allow-list)",
                llm_provider,
                llm_model,
            )
        clear_override()
