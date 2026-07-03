"""Provider abstraction for RAG chat generation.

Each concrete provider owns exactly one upstream (OpenAI-compatible endpoint,
Ollama, Anthropic, Gemini, ...). The registry resolves a provider from the
``provider/`` segment of the selected model id and calls it with the bare model
name. Providers are the Strategy in a Strategy/Factory pair (registry is the
factory); adding a provider is registering a new adapter, not adding a branch.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from src.generation.providers.types import ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition


class ChatProvider(ABC):
    """One upstream chat provider.

    ``supports_tools`` advertises whether ``generate_with_tools`` does anything
    useful; when False the default implementation returns ``no_tool_support`` so
    the caller can fall back to plain generation.
    """

    provider_id: str
    supports_tools: bool = False

    @abstractmethod
    async def generate(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> tuple[str, UsageStats]:
        """Return (text, usage) for a single completion. ``model`` is the bare
        provider-native model name (no ``provider/`` prefix)."""

    @abstractmethod
    def stream(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> AsyncGenerator[str | UsageStats, None]:
        """Yield string tokens, then a final UsageStats. ``model`` is bare."""

    async def generate_with_tools(
        self,
        model: str,
        messages: list[dict],
        runtime_tools: list[RuntimeToolDefinition],
        images: list[str] | None = None,
    ) -> ToolAwareLLMResponse:
        """Tool-aware generation. Default: this provider has no tool support."""
        return ToolAwareLLMResponse(provider=self.provider_id, no_tool_support=True)

    async def aclose(self) -> None:
        """Close any open client. Default no-op."""
        return None
