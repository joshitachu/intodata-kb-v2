"""OpenAI-compatible chat provider.

Parameterized by base_url + api_key so one adapter serves every OpenAI-wire
endpoint: Kimi/Moonshot now, OpenAI and DeepSeek in later slices. Uses the
official ``openai`` AsyncOpenAI client (all three speak that protocol).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

import httpx

from src.generation.providers.base import ChatProvider
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
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
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
        max_tokens: int = 2048,
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
            usage.calculate_cost()
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
        acc.latency_ms = int((time.perf_counter() - t0) * 1000)
        yield acc.finalize()

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
            usage.calculate_cost()

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
