"""Ollama (local) chat provider.

Talks to a local Ollama daemon via the ``ollama`` async client. Model names are
bare tags (e.g. ``gemma3:12b``) — the ``ollama/`` prefix is stripped by the
registry before we get here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

import httpx
import ollama as ollama_lib

from src.generation.providers.base import ChatProvider
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import StreamUsageStats, UsageStats
from src.runtime.models import RuntimeToolDefinition

logger = logging.getLogger(__name__)


def _apply_images(messages: list[dict], images: list[str] | None) -> list[dict]:
    """Attach images to the last user message for Ollama."""
    if not images:
        return messages
    msgs = [m.copy() for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user":
            msgs[i] = {**msgs[i], "images": images}
            break
    return msgs


def _to_ollama_tool_schema(tool: RuntimeToolDefinition) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


class OllamaProvider(ChatProvider):
    provider_id = "ollama"
    # Tool support in Ollama is per-MODEL, not per-provider (e.g. llama3.1/qwen
    # support tools; gemma3 does not). We advertise True so tool-capable models
    # are attempted, and fall back per-model on the "does not support tools" 400
    # (cached below so it is attempted at most once per model).
    supports_tools = True

    def __init__(self, *, base_url: str, timeout: float = 300.0) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._client = None
        self._lock = asyncio.Lock()
        # Models known (this process) not to support tools — skip the doomed
        # tool call and degrade gracefully without re-hitting the 400 each turn.
        self._no_tool_models: set[str] = set()

    async def _get_client(self):
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = ollama_lib.AsyncClient(
                        host=self._base_url,
                        timeout=httpx.Timeout(self._timeout, connect=5.0),
                    )
        return self._client

    async def generate(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> tuple[str, UsageStats]:
        client = await self._get_client()
        t0 = time.perf_counter()
        response = await client.chat(
            model=model,
            messages=_apply_images(messages, images),
            options={"num_ctx": 32768, "temperature": 0.45},
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        usage = UsageStats(
            provider="ollama",
            model=model,
            input_tokens=response.get("prompt_eval_count", 0) or 0,
            output_tokens=response.get("eval_count", 0) or 0,
            latency_ms=elapsed,
        )
        return response["message"]["content"], usage

    async def stream(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> AsyncGenerator[str | UsageStats, None]:
        client = await self._get_client()
        t0 = time.perf_counter()
        stream = await client.chat(
            model=model,
            messages=_apply_images(messages, images),
            stream=True,
            options={"num_ctx": 32768, "temperature": 0.45},
        )
        acc = StreamUsageStats(provider="ollama", model=model)
        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token
            # Ollama sends eval counts in the final chunk (done=true).
            if chunk.get("done"):
                acc.input_tokens = chunk.get("prompt_eval_count", 0) or 0
                acc.output_tokens = chunk.get("eval_count", 0) or 0
        acc.latency_ms = int((time.perf_counter() - t0) * 1000)
        yield acc.finalize()

    async def generate_with_tools(
        self,
        model: str,
        messages: list[dict],
        runtime_tools: list[RuntimeToolDefinition],
        images: list[str] | None = None,
    ) -> ToolAwareLLMResponse:
        # Model already known not to support tools → degrade without a call.
        if model in self._no_tool_models:
            return ToolAwareLLMResponse(provider="ollama", no_tool_support=True)
        client = await self._get_client()
        tools_schema = [_to_ollama_tool_schema(t) for t in runtime_tools] if runtime_tools else None
        try:
            response = await client.chat(
                model=model,
                messages=_apply_images(messages, images),
                tools=tools_schema,
                options={"num_ctx": 32768, "temperature": 0.45},
            )
        except ollama_lib.ResponseError as e:
            # Ollama returns 400 "<model> does not support tools" for models
            # without tool support. Remember it and degrade — not an error.
            if "does not support tools" in str(e):
                self._no_tool_models.add(model)
                logger.info("ollama model %s does not support tools; degrading to plain generation", model)
                return ToolAwareLLMResponse(provider="ollama", no_tool_support=True)
            raise
        usage = UsageStats(
            provider="ollama",
            model=model,
            input_tokens=response.get("prompt_eval_count", 0) or 0,
            output_tokens=response.get("eval_count", 0) or 0,
        )
        message = response["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            import json

            requests = []
            for i, call in enumerate(tool_calls):
                fn = call["function"]
                arguments = fn.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                # Ollama has no tool-call id; synthesize one, index-suffixed so
                # parallel calls to the same tool stay distinguishable.
                requests.append(
                    RuntimeToolCallRequest(
                        call_id=f"ollama_{fn['name']}_{i}", tool_name=fn["name"], arguments=arguments
                    )
                )
            return ToolAwareLLMResponse(
                provider="ollama",
                tool_calls=requests,
                assistant_message={
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                },
                usage=usage,
            )
        return ToolAwareLLMResponse(provider="ollama", text=message.get("content"), usage=usage)

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client._client.aclose()
            except Exception:
                pass
            self._client = None
