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
    # such as Claude/OpenCode CLI are False).
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


class KimiModelStrategy(ModelProviderStrategy):
    """Moonshot Kimi using the same canonical ``kimi/<model>`` id as RAG chat."""

    provider_id = "kimi"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.providers.custom.providers import MoonshotKimiProvider

        return MoonshotKimiProvider(
            self.parse_model_name(model_id),
            api_key=api_key or os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY"),
            base_url=base_url or "https://api.moonshot.ai/v1",
        )


class OpenRouterModelStrategy(ModelProviderStrategy):
    """OpenRouter aggregator, reached over its OpenAI-compatible endpoint.

    Model ids keep the vendor path OpenRouter itself uses, so the descriptor id is
    ``openrouter/<vendor>/<model>`` and the wire model name is ``<vendor>/<model>``.
    """

    provider_id = "openrouter"

    def create_model_provider(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        ollama_base_url: str | None = None,
    ):
        from mcp_server.config import config
        from mcp_server.providers.custom.providers import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            self.parse_model_name(model_id),
            api_key=(
                api_key
                or config.doc_generation_openrouter_api_key
                or os.environ.get("OPENROUTER_API_KEY")
            ),
            base_url=base_url or config.doc_generation_openrouter_base_url,
            provider_name="openrouter",
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


class OpenCodeGoModelStrategy(ModelProviderStrategy):
    """`opencode-go/<model>` is a ROUTE handed to the OpenCode CLI, not a provider.

    The Python harness never constructs a client for these ids: the subscription
    is redeemed inside the CLI session, which reads the key from a private
    per-run auth file (`providers/opencode_cli.py`). They belong in the catalogue
    because the dashboard's OpenCode session-model picker offers them.

    Without this strategy every `opencode-go/...` entry failed
    `get_model_strategy()` -- the catalogue carried 20 of them and not one
    resolved, which is what `test_every_catalogue_model_resolves_to_a_strategy`
    had been reporting (one id at a time, so it read as a single stale model
    rather than a whole missing provider).

    Descriptors come from `data/model_catalogue.json`, so this returns none of
    its own: listing them here too would mint a second, drifting source.
    """

    provider_id = "opencode-go"

    def owns(self, model_id: str) -> bool:
        return model_id.startswith(f"{self.provider_id}/")

    def create_model_provider(self, model_id: str, **_kwargs):
        raise ValueError(
            f"{model_id!r} is an OpenCode subscription route; it is resolved inside the "
            "OpenCode CLI session, not by a harness model provider."
        )

    def descriptors(self) -> list[ModelDescriptorData]:
        return []


class OpenCodeCliStrategy(ModelProviderStrategy):
    """OpenCode is a documentation runtime, not a direct API model provider."""

    provider_id = "opencode-cli"

    def owns(self, model_id: str) -> bool:
        return model_id == self.provider_id

    def create_model_provider(self, model_id: str, **_kwargs):
        raise ValueError("opencode-cli is resolved by the documentation provider adapter")

    def descriptors(self) -> list[ModelDescriptorData]:
        return [
            ModelDescriptorData(
                id="opencode-cli",
                provider=self.provider_id,
                label="OpenCode CLI",
                supports_analysis=True,
                supports_writing=True,
                rag_capable=False,
            )
        ]
