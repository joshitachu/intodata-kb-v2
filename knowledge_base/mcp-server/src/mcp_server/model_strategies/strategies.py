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
        from mcp_server.providers.custom.providers import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            self.parse_model_name(model_id),
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url or "https://api.openai.com/v1",
        )

    def descriptors(self) -> list[ModelDescriptorData]:
        return [
            ModelDescriptorData(
                id="openai/gpt-5.5",
                provider=self.provider_id,
                label="OpenAI GPT-5.5",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-5.4",
                provider=self.provider_id,
                label="OpenAI GPT-5.4",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-5.4-mini",
                provider=self.provider_id,
                label="OpenAI GPT-5.4 Mini",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-5",
                provider=self.provider_id,
                label="OpenAI GPT-5",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-4.5",
                provider=self.provider_id,
                label="OpenAI GPT-4.5",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-4.1",
                provider=self.provider_id,
                label="OpenAI GPT-4.1",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="openai/gpt-4o",
                provider=self.provider_id,
                label="OpenAI GPT-4o",
                supports_analysis=True,
                supports_writing=True,
            ),
        ]


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

    def descriptors(self) -> list[ModelDescriptorData]:
        return [
            ModelDescriptorData(
                id="google/gemini-3.1-pro-preview",
                provider=self.provider_id,
                label="Gemini 3.1 Pro Preview",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="google/gemini-2.5-pro",
                provider=self.provider_id,
                label="Gemini 2.5 Pro",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="google/gemini-2.5-flash-lite",
                provider=self.provider_id,
                label="Gemini 2.5 Flash-Lite",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="google/gemini-2.5-flash",
                provider=self.provider_id,
                label="Gemini 2.5 Flash",
                supports_analysis=True,
                supports_writing=True,
            ),
        ]


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

    def descriptors(self) -> list[ModelDescriptorData]:
        return [
            ModelDescriptorData(
                id="anthropic/claude-sonnet-4-6",
                provider=self.provider_id,
                label="Claude Sonnet 4.6",
                supports_analysis=True,
                supports_writing=True,
            ),
            ModelDescriptorData(
                id="anthropic/claude-opus-4-7",
                provider=self.provider_id,
                label="Claude Opus 4.7",
                supports_analysis=True,
                supports_writing=True,
            ),
        ]


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

    def descriptors(self) -> list[ModelDescriptorData]:
        return [
            ModelDescriptorData(
                id="ollama/gemma3:12b",
                provider=self.provider_id,
                label="Ollama Gemma 3 12B (local)",
                supports_analysis=True,
                supports_writing=True,
                is_local=True,
            ),
            ModelDescriptorData(
                id="ollama/gemma3:4b",
                provider=self.provider_id,
                label="Ollama Gemma 3 4B (local)",
                supports_analysis=True,
                supports_writing=True,
                is_local=True,
            ),
        ]


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
        return [
            ModelDescriptorData(
                id="claude-cli",
                provider=self.provider_id,
                label="Claude Code CLI",
                supports_analysis=True,
                supports_writing=True,
            )
        ]
