"""Canonical API-key / base-URL resolution for a ``provider/model`` id.

Every doc-generation surface that builds a ``ModelProvider`` from a model id
(custom harness, deep dive, git-diff patcher, reingestion expander) resolves its
connection settings here, so adding a provider is one edit rather than one per
call site.
"""

from __future__ import annotations

from mcp_server.model_strategies.registry import normalize_model_id

# Providers whose official SDK reads its own first-party credential from the
# environment; passing an explicit key/base URL would only get in the way.
_SDK_MANAGED_PROVIDERS = frozenset({"openai", "anthropic", "google"})


def provider_connection_config(model_id: str) -> dict[str, str | None]:
    """Return ``{"api_key": ..., "base_url": ...}`` for ``model_id``."""

    from mcp_server.config import config

    provider = normalize_model_id(model_id).partition("/")[0]
    if provider == "deepseek":
        return {
            "api_key": config.doc_generation_deepseek_api_key or config.doc_generation_api_key,
            "base_url": config.doc_generation_deepseek_base_url or config.doc_generation_base_url,
        }
    if provider == "openrouter":
        return {
            "api_key": config.doc_generation_openrouter_api_key or config.doc_generation_api_key,
            "base_url": config.doc_generation_openrouter_base_url,
        }
    if provider in _SDK_MANAGED_PROVIDERS:
        return {"api_key": None, "base_url": None}
    return {
        "api_key": config.doc_generation_api_key,
        "base_url": config.doc_generation_base_url,
    }
