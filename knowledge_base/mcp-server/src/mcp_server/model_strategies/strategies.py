"""Strategies for doc-generation model/provider behavior."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDescriptorData:
    id: str
    provider: str
    label: str
    supports_analysis: bool
    supports_writing: bool
    is_local: bool = False
    # Env vars any of which, when set, make a cloud model available (empty for
    # local models). Populated from data/model_catalogue.json.
    credential_env: tuple[str, ...] = ()
    # Whether the RAG chat surface may offer this model (doc-gen-only mechanisms
    # such as claude-cli are False).
    rag_capable: bool = True


class ModelProviderStrategy(ABC):
    provider_id: str
    supports_analysis: bool = True
    supports_writing: bool = True
    is_local: bool = False

    def owns(self, model_id: str) -> bool:
        return model_id.startswith(f"{self.provider_id}/")

    def parse_model_name(self, model_id: str) -> str:
        provider, sep, model_name = model_id.partition("/")
        if provider != self.provider_id or not sep or not model_name:
            raise ValueError(
                f"Invalid model string {model_id!r}; expected '{self.provider_id}/<model>'"
            )
        return model_name

    @abstractmethod
    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        ...

    def descriptors(self) -> list[ModelDescriptorData]:
        return []


class OpenAIModelStrategy(ModelProviderStrategy):
    provider_id = "openai"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import OpenAIOfficialProvider

        return OpenAIOfficialProvider(
            self.parse_model_name(model_id),
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
        )


class DeepSeekModelStrategy(ModelProviderStrategy):
    provider_id = "deepseek"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            self.parse_model_name(model_id),
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url=base_url or "https://api.deepseek.com/v1",
            provider_name="deepseek",
        )


class Route33ModelStrategy(ModelProviderStrategy):
    provider_id = "route33"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.config import config
        from mcp_server.providers.custom.providers import (
            OpenAICompatibleProvider,
            Route33AnthropicMessagesProvider,
        )

        model_name = self.parse_model_name(model_id)
        resolved_api_key = (
            api_key
            or config.doc_generation_route33_api_key
            or os.environ.get("ROUTE33_API_KEY")
        )
        if model_name.startswith("anthropic/"):
            return Route33AnthropicMessagesProvider(
                model_name,
                api_key=resolved_api_key,
                base_url=base_url or config.doc_generation_route33_base_url,
            )

        return OpenAICompatibleProvider(
            model_name,
            api_key=resolved_api_key,
            base_url=base_url or config.doc_generation_route33_base_url,
            provider_name="route33",
        )


class GoogleModelStrategy(ModelProviderStrategy):
    provider_id = "google"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import GoogleGeminiProvider

        return GoogleGeminiProvider(
            self.parse_model_name(model_id),
            api_key=api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
            base_url=base_url,
        )


class AnthropicModelStrategy(ModelProviderStrategy):
    provider_id = "anthropic"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import AnthropicProvider

        return AnthropicProvider(
            self.parse_model_name(model_id),
            api_key=os.environ.get("ANTHROPIC_API_KEY") or api_key,
        )


class OllamaModelStrategy(ModelProviderStrategy):
    provider_id = "ollama"
    is_local = True

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import OllamaCompatibleProvider

        return OllamaCompatibleProvider(
            self.parse_model_name(model_id),
            base_url=ollama_base_url or base_url,
        )


class CustomHarnessStrategy(ModelProviderStrategy):
    provider_id = "custom"
    supports_writing = False

    def owns(self, model_id: str) -> bool:
        return model_id == self.provider_id

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        raise ValueError("'custom' resolves to MCP_DOC_GENERATION_MODEL before provider construction")

    def descriptors(self) -> list[ModelDescriptorData]:
        return []


class ClaudeCliStrategy(ModelProviderStrategy):
    provider_id = "claude-cli"
    supports_writing = True

    def owns(self, model_id: str) -> bool:
        return model_id == self.provider_id

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.config import config
        from mcp_server.providers.custom.providers import ClaudeCliModelProvider

        return ClaudeCliModelProvider(config.claude_model)

    def descriptors(self) -> list[ModelDescriptorData]:
        # Doc-generation-only mechanism (not a network provider) — kept in code,
        # not in the shared catalogue, and never offered on the RAG chat surface.
        return [
            ModelDescriptorData(
                id="claude-cli",
                provider=self.provider_id,
                label="Claude Code CLI",
                supports_analysis=True,
                supports_writing=True,
                rag_capable=False,
            )
        ]
