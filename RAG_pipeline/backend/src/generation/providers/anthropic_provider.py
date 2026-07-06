"""Anthropic (Claude) chat provider.

The Messages API differs from the OpenAI wire format: the system prompt is a
separate argument, content is block-structured, and tool use / tool results are
content blocks rather than dedicated message roles. Callers (and the runtime tool
loop) speak OpenAI-shaped messages, so this adapter translates OpenAI-shaped
history → Anthropic on the way in, and returns its assistant_message in
OpenAI shape on the way out so the loop's history stays consistent.
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

_MAX_TOKENS = 2048


def _to_anthropic(messages: list[dict], images: list[str] | None) -> tuple[str, list[dict]]:
    """Convert OpenAI-shaped messages to (system_text, anthropic_messages)."""
    system_parts: list[str] = []
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
            continue
        if role == "tool":
            # OpenAI tool result → Anthropic tool_result block (as a user turn).
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else json.dumps(content, default=str),
                }],
            })
            continue
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn["name"],
                    "input": args,
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        # Plain user/assistant text message.
        out.append({"role": role or "user", "content": content})

    # Attach images to the last user message as image blocks.
    if images:
        for i in range(len(out) - 1, -1, -1):
            if out[i]["role"] == "user":
                text = out[i]["content"]
                blocks = [{"type": "text", "text": text}] if isinstance(text, str) else list(text)
                for img in images:
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img},
                    })
                out[i] = {"role": "user", "content": blocks}
                break
    return "\n".join(system_parts), out


def _to_anthropic_tools(runtime_tools: list[RuntimeToolDefinition]) -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in runtime_tools
    ]


def _text_from_content(content) -> str:
    parts = [b.text for b in content if getattr(b, "type", None) == "text"]
    return "".join(parts)


class AnthropicProvider(ChatProvider):
    provider_id = "anthropic"
    supports_tools = True

    def __init__(self, *, api_key: str, timeout: float = 120.0, temperature: float = 0.6) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._temperature = temperature
        self._client = None
        self._lock = asyncio.Lock()

    async def _get_client(self):
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    from anthropic import AsyncAnthropic

                    self._client = AsyncAnthropic(
                        api_key=self._api_key,
                        timeout=httpx.Timeout(self._timeout, connect=5.0),
                        max_retries=0,
                    )
        return self._client

    async def generate(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> tuple[str, UsageStats]:
        client = await self._get_client()
        system, msgs = _to_anthropic(messages, images)
        t0 = time.perf_counter()
        resp = await client.messages.create(
            model=model, max_tokens=_MAX_TOKENS, temperature=self._temperature,
            system=system or None, messages=msgs,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        usage = UsageStats(
            provider="anthropic", model=model,
            input_tokens=resp.usage.input_tokens if resp.usage else 0,
            output_tokens=resp.usage.output_tokens if resp.usage else 0,
            latency_ms=elapsed,
        )
        usage.calculate_cost()
        return _text_from_content(resp.content), usage

    async def stream(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> AsyncGenerator[str | UsageStats, None]:
        client = await self._get_client()
        system, msgs = _to_anthropic(messages, images)
        t0 = time.perf_counter()
        acc = StreamUsageStats(provider="anthropic", model=model)
        async with client.messages.stream(
            model=model, max_tokens=_MAX_TOKENS, temperature=self._temperature,
            system=system or None, messages=msgs,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text
            final = await stream.get_final_message()
        if final and final.usage:
            acc.input_tokens = final.usage.input_tokens or 0
            acc.output_tokens = final.usage.output_tokens or 0
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
        system, msgs = _to_anthropic(messages, images)
        tools = _to_anthropic_tools(runtime_tools) if runtime_tools else None
        resp = await client.messages.create(
            model=model, max_tokens=_MAX_TOKENS, temperature=self._temperature,
            system=system or None, messages=msgs,
            tools=tools, tool_choice={"type": "auto"} if tools else None,
        )
        usage = UsageStats(
            provider="anthropic", model=model,
            input_tokens=resp.usage.input_tokens if resp.usage else 0,
            output_tokens=resp.usage.output_tokens if resp.usage else 0,
        )
        usage.calculate_cost()

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if tool_uses:
            text = _text_from_content(resp.content)
            # Return assistant_message in OpenAI shape for loop consistency.
            openai_tool_calls = [{
                "id": tu.id,
                "type": "function",
                "function": {"name": tu.name, "arguments": json.dumps(tu.input or {})},
            } for tu in tool_uses]
            return ToolAwareLLMResponse(
                provider="anthropic",
                tool_calls=[
                    RuntimeToolCallRequest(
                        call_id=tu.id, tool_name=tu.name, arguments=dict(tu.input or {})
                    )
                    for tu in tool_uses
                ],
                assistant_message={"role": "assistant", "content": text, "tool_calls": openai_tool_calls},
                usage=usage,
            )
        return ToolAwareLLMResponse(provider="anthropic", text=_text_from_content(resp.content), usage=usage)

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
