"""RAG chat provider layer — registry + per-provider adapters."""
from src.generation.providers.base import ChatProvider
from src.generation.providers.registry import (
    close_all,
    get_provider,
    parse_model_id,
    registered_provider_ids,
    reset_for_tests,
    resolve,
)
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse

__all__ = [
    "ChatProvider",
    "RuntimeToolCallRequest",
    "ToolAwareLLMResponse",
    "close_all",
    "get_provider",
    "parse_model_id",
    "registered_provider_ids",
    "reset_for_tests",
    "resolve",
]
