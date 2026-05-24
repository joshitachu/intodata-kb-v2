"""Model/provider strategy registry."""

from mcp_server.model_strategies.registry import (
    GenerationModelResolution,
    get_model_strategy,
    list_model_descriptors,
    list_model_strategies,
    normalize_model_id,
    resolve_generation_models,
    supported_model_ids,
)
from mcp_server.model_strategies.strategies import ModelProviderStrategy

__all__ = [
    "GenerationModelResolution",
    "ModelProviderStrategy",
    "get_model_strategy",
    "list_model_descriptors",
    "list_model_strategies",
    "normalize_model_id",
    "resolve_generation_models",
    "supported_model_ids",
]
