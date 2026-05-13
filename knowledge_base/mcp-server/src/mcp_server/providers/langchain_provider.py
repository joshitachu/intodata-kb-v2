"""LangChain/LangGraph provider for API-based doc generation.

Supports any model reachable via LangChain:
  "anthropic/claude-sonnet-4-6"
  "openai/gpt-4o"
  "google/gemini-2.0-flash"
  "ollama/llama3.2"
  "moonshot/moonshot-v1-8k"
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.language_models import BaseChatModel

from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.providers.orchestrator import DocGenState, _TokenAccumulator, build_doc_gen_graph


# USD per 1M tokens: (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "moonshot/kimi-k2.6":           (0.95,  4.00),
    "openai/gpt-4o":                (2.50, 10.00),
    "openai/gpt-4o-mini":           (0.15,  0.60),
    "anthropic/claude-sonnet-4-6":  (3.00, 15.00),
    "anthropic/claude-opus-4-7":   (15.00, 75.00),
    "anthropic/claude-haiku-4-5":   (0.80,  4.00),
    "google/gemini-2.0-flash":      (0.10,  0.40),
    "google/gemini-2.5-pro":        (1.25, 10.00),
}


def _calc_cost(model_str: str, input_tokens: int, output_tokens: int) -> float | None:
    if model_str.startswith("ollama/"):
        return 0.0
    rates = _PRICING.get(model_str)
    if not rates:
        return None
    return round(input_tokens / 1_000_000 * rates[0] + output_tokens / 1_000_000 * rates[1], 6)

logger = logging.getLogger(__name__)


def _build_llm(model_str: str, api_key: str | None, base_url: str | None) -> BaseChatModel:
    """Map 'provider/model' string to a LangChain chat model instance."""
    provider, _, model = model_str.partition("/")
    if not model:
        raise ValueError(f"Invalid model string {model_str!r} — expected 'provider/model'")

    if provider == "anthropic":
        import os
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                f"No API key for '{model_str}'. Set MCP_DOC_GENERATION_API_KEY in .env and restart the server."
            )
        from langchain_anthropic import ChatAnthropic  # type: ignore[import-untyped]
        kwargs: dict[str, Any] = {"model": model, "anthropic_api_key": resolved_key}
        return ChatAnthropic(**kwargs)  # type: ignore[return-value]

    if provider == "openai":
        import os
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError(
                f"No API key for '{model_str}'. Set MCP_DOC_GENERATION_API_KEY in .env and restart the server."
            )
        from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]
        kwargs = {"model": model, "openai_api_key": resolved_key}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)  # type: ignore[return-value]

    if provider == "google":
        import os
        resolved_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not resolved_key:
            raise ValueError(
                f"No API key for '{model_str}'. Set MCP_DOC_GENERATION_API_KEY in .env and restart the server."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-untyped]
        kwargs = {"model": model, "google_api_key": resolved_key}
        return ChatGoogleGenerativeAI(**kwargs)  # type: ignore[return-value]

    if provider == "ollama":
        from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]
        ollama_url = (base_url or "http://localhost:11434").rstrip("/")
        if not ollama_url.endswith("/v1"):
            ollama_url += "/v1"
        return ChatOpenAI(model=model, openai_api_key="ollama", base_url=ollama_url)  # type: ignore[return-value]

    if provider == "moonshot":
        import os
        resolved_key = api_key or os.environ.get("MOONSHOT_API_KEY")
        if not resolved_key:
            raise ValueError(
                f"No API key for '{model_str}'. Set MCP_DOC_GENERATION_API_KEY in .env and restart the server."
            )
        from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]
        kwargs = {"model": model, "openai_api_key": resolved_key}
        kwargs["base_url"] = base_url or "https://api.moonshot.ai/v1"
        # Disable Kimi's extended thinking via extra_body (OpenAI SDK vendor extension field).
        # LangChain drops reasoning_content between turns, causing 400s on turn 2+.
        kwargs["model_kwargs"] = {"extra_body": {"thinking": {"type": "disabled"}}}
        return ChatOpenAI(**kwargs)  # type: ignore[return-value]

    raise ValueError(
        f"Unknown provider {provider!r} in {model_str!r}. "
        "Expected: anthropic, openai, google, ollama, or moonshot."
    )


class LangchainProvider(DocGenerationProvider):
    """Run doc generation via LangGraph + LangChain (API providers, local Ollama)."""

    def __init__(
        self,
        model_str: str,
        api_key: str | None = None,
        base_url: str | None = None,
        write_model_str: str | None = None,
    ) -> None:
        self.model_str = model_str
        self.llm = _build_llm(model_str, api_key, base_url)
        self.write_llm: BaseChatModel | None = None
        if write_model_str and write_model_str != model_str:
            try:
                # If write_model uses a different provider than the main model, don't pass
                # the main api_key — let each provider resolve its own env var credentials.
                from mcp_server.config import config as _cfg
                main_provider = model_str.partition("/")[0]
                write_provider = write_model_str.partition("/")[0]
                write_api_key = api_key if write_provider == main_provider else None
                # Ollama always uses its own base URL regardless of the main provider
                if write_provider == "ollama":
                    write_base_url = _cfg.ollama_base_url
                elif write_provider == main_provider:
                    write_base_url = base_url
                else:
                    write_base_url = None
                self.write_llm = _build_llm(write_model_str, write_api_key, write_base_url)
                logger.info("write_model configured: %s (phase3)", write_model_str)
            except Exception as exc:
                logger.warning("write_model build failed (%s), using same model for all phases", exc)

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        start_ms = int(time.time() * 1000)

        phase3_model = self.write_llm and ctx.write_model or self.model_str
        await log_cb(ctx.project_id, f"[langchain] starting with {self.model_str} (phase3: {phase3_model})", "info")

        acc = _TokenAccumulator()
        graph = build_doc_gen_graph(self.llm, log_cb, accumulator=acc, write_llm=self.write_llm)

        initial_state: DocGenState = {
            "ctx": ctx,
            "completed_groups": [],
            "failed_groups": [],
            "completed_docs": [],
            "failed_docs": [],
            "errors": [],
            "log_messages": [],
            "scorecard_done": False,
            "bundles_done": False,
        }

        try:
            final_state: DocGenState = await graph.ainvoke(initial_state)
        except Exception as exc:
            logger.exception("LangGraph graph.ainvoke failed")
            cost = _calc_cost(self.model_str, acc.input_tokens, acc.output_tokens)
            return AgentResult(
                success=False,
                error=str(exc),
                model_name=self.model_str,
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                cost_usd=cost,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        # Flush any remaining log messages that nodes emitted via state
        for msg, level in final_state.get("log_messages", []):
            await log_cb(ctx.project_id, msg, level)

        errors = final_state.get("errors", [])
        failed_docs = final_state.get("failed_docs", [])
        completed_docs = final_state.get("completed_docs", [])

        success = len(completed_docs) > 0 and len(failed_docs) == 0

        if not success:
            await log_cb(
                ctx.project_id,
                f"[langchain] generation incomplete — completed={completed_docs}, failed={failed_docs}",
                "warning",
            )

        duration_ms = int(time.time() * 1000) - start_ms
        cost = _calc_cost(self.model_str, acc.input_tokens, acc.output_tokens)

        await log_cb(
            ctx.project_id,
            f"[langchain] done — in={acc.input_tokens:,} out={acc.output_tokens:,} tokens"
            + (f" cost=${cost:.4f}" if cost is not None else ""),
            "info",
        )

        write_model_str = ctx.write_model if self.write_llm else None
        return AgentResult(
            success=success,
            error="; ".join(errors) if errors else None,
            model_name=self.model_str,
            write_model_name=write_model_str,
            input_tokens=acc.input_tokens,
            output_tokens=acc.output_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )
