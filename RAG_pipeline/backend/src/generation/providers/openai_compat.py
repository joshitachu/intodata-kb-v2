"""OpenAI-compatible chat provider.

Parameterized by base_url + api_key so one adapter serves every OpenAI-wire
endpoint: Kimi/Moonshot now, OpenAI and DeepSeek in later slices. Uses the
official ``openai`` AsyncOpenAI client (all three speak that protocol).

Prompt caching on this wire format is AUTOMATIC, not requested. Probed against
api.moonshot.ai with kimi-k2.5 on 2026-08-20 (that model has since been retired
upstream; the findings are recorded as measured and are assumed to hold for the
rest of the kimi family, but have not been re-probed):

* Repeating an identical request reported ``cached_tokens == prompt_tokens``
  (6266/6266); the first, cold call reported ``prompt_tokens_details: null``.
* Holding the tool block constant while changing the system message still
  cached 2304 tokens — exactly the tool block — proving the tool schemas are
  serialized BEFORE the system message and cache independently of it.
* Cached counts only ever land on multiples of 128, so the cache is granular to
  128-token blocks.
* ``POST /v1/caching`` (Moonshot's explicit context-cache API) returns
  ``400 model family is invalid`` for kimi-k2.5 — explicit caching is a
  moonshot-v1 feature and is not available to us.
* Anthropic-style ``cache_control`` blocks are silently ACCEPTED but change
  nothing (same 6144 cached tokens with and without them).

So there is no explicit marker worth sending here: adding one would be a no-op
parameter that merely looks like a feature. The work that actually pays is
keeping the prefix byte-stable (``canonical_schema``) and reading the hit counts
back so the saving is measurable (``read_openai_cache_tokens``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

import httpx

from src.generation.providers.base import ChatProvider
from src.generation.providers.prompt_cache import (
    apply_cache_tokens,
    canonical_schema,
    check_tool_prefix_stability,
    log_cache_effect,
    read_openai_cache_tokens,
)
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import StreamUsageStats, UsageStats
from src.runtime.models import RuntimeToolDefinition

logger = logging.getLogger(__name__)


def _apply_images(messages: list[dict], images: list[str] | None) -> list[dict]:
    """Convert the last user message to OpenAI multimodal format."""
    if not images:
        return messages
    msgs = [m.copy() for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "user":
            text = msgs[i]["content"]
            content: list[dict] = [{"type": "text", "text": text}]
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img}"},
                })
            msgs[i] = {**msgs[i], "content": content}
            break
    return msgs


def _to_openai_tool_schema(tool: RuntimeToolDefinition) -> dict:
    """Serialize one tool for the wire, with object keys canonically ordered.

    ``input_schema`` reaches us as a plain dict assembled upstream, so its key
    order depends on how it was built. Since the tool block heads the cacheable
    prefix, an order difference between two otherwise identical schemas would
    invalidate the cache for the whole request.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": canonical_schema(tool.input_schema),
        },
    }


class OpenAICompatibleProvider(ChatProvider):
    supports_tools = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        timeout: float = 300.0,
        temperature: float = 0.6,
        max_tokens: int = 4096,
        extra_body: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens
        # extra_body lets a provider disable "thinking" etc. (Kimi/Moonshot).
        self._extra_body = extra_body or None
        self._client = None
        self._lock = asyncio.Lock()

    async def _get_client(self):
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    from openai import AsyncOpenAI

                    self._client = AsyncOpenAI(
                        base_url=self._base_url,
                        api_key=self._api_key,
                        timeout=httpx.Timeout(self._timeout, connect=5.0),
                        # Our layer surfaces errors; don't let the SDK retry internally.
                        max_retries=0,
                    )
        return self._client

    async def generate(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> tuple[str, UsageStats]:
        client = await self._get_client()
        t0 = time.perf_counter()
        response = await client.chat.completions.create(
            model=model,
            messages=_apply_images(messages, images),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            extra_body=self._extra_body,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        usage = UsageStats(provider=self.provider_id, model=model, latency_ms=elapsed)
        if response.usage:
            usage.input_tokens = response.usage.prompt_tokens or 0
            usage.output_tokens = response.usage.completion_tokens or 0
            apply_cache_tokens(usage, *read_openai_cache_tokens(response.usage))
            usage.calculate_cost()
            log_cache_effect(self.provider_id, model, usage)
        return response.choices[0].message.content, usage

    async def stream(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> AsyncGenerator[str | UsageStats, None]:
        client = await self._get_client()
        t0 = time.perf_counter()
        stream = await client.chat.completions.create(
            model=model,
            messages=_apply_images(messages, images),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=self._extra_body,
        )
        acc = StreamUsageStats(provider=self.provider_id, model=model)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            # OpenAI-compat APIs send usage in the final chunk.
            if chunk.usage:
                acc.input_tokens = chunk.usage.prompt_tokens or 0
                acc.output_tokens = chunk.usage.completion_tokens or 0
                apply_cache_tokens(acc, *read_openai_cache_tokens(chunk.usage))
        acc.latency_ms = int((time.perf_counter() - t0) * 1000)
        final = acc.finalize()
        log_cache_effect(self.provider_id, model, final)
        yield final

    async def generate_with_tools(
        self,
        model: str,
        messages: list[dict],
        runtime_tools: list[RuntimeToolDefinition],
        images: list[str] | None = None,
    ) -> ToolAwareLLMResponse:
        client = await self._get_client()
        t0 = time.perf_counter()
        tools_schema = [_to_openai_tool_schema(t) for t in runtime_tools] if runtime_tools else None
        check_tool_prefix_stability(self.provider_id, tools_schema)
        response = await client.chat.completions.create(
            model=model,
            messages=_apply_images(messages, images),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            tools=tools_schema,
            tool_choice="auto" if tools_schema else "none",
            extra_body=self._extra_body,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        usage = UsageStats(provider=self.provider_id, model=model, latency_ms=elapsed)
        if response.usage:
            usage.input_tokens = response.usage.prompt_tokens or 0
            usage.output_tokens = response.usage.completion_tokens or 0
            apply_cache_tokens(usage, *read_openai_cache_tokens(response.usage))
            usage.calculate_cost()
            log_cache_effect(self.provider_id, model, usage)

        message = response.choices[0].message
        if message.tool_calls:
            requests = []
            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                requests.append(
                    RuntimeToolCallRequest(
                        call_id=call.id, tool_name=call.function.name, arguments=arguments
                    )
                )
            return ToolAwareLLMResponse(
                provider=self.provider_id,
                tool_calls=requests,
                assistant_message={
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in message.tool_calls
                    ],
                },
                usage=usage,
            )
        return ToolAwareLLMResponse(provider=self.provider_id, text=message.content, usage=usage)

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
