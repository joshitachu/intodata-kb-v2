"""Google Gemini chat provider (google-genai SDK).

Gemini uses roles user/model, a separate ``system_instruction``, and function
calling via function_call/function_response parts. Callers speak OpenAI-shaped
messages, so this adapter translates inbound history → Gemini and returns its
assistant_message in OpenAI shape so the runtime tool loop stays provider-agnostic.
Contents/config are built as plain dicts (the SDK coerces them), which keeps the
adapter easy to unit-test without importing SDK types.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from src.generation.providers.base import ChatProvider
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import StreamUsageStats, UsageStats
from src.runtime.models import RuntimeToolDefinition

logger = logging.getLogger(__name__)

_MAX_TOKENS = 2048


def _to_gemini(messages: list[dict], images: list[str] | None) -> tuple[str, list[dict]]:
    """Convert OpenAI-shaped messages to (system_instruction, gemini_contents)."""
    system_parts: list[str] = []
    contents: list[dict] = []
    # Map tool_call_id -> function name, so OpenAI tool results (which carry only
    # the id) can be turned into Gemini function_response parts (which need name).
    call_names: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
            continue
        if role == "tool":
            call_id = msg.get("tool_call_id", "")
            name = call_names.get(call_id, call_id)
            try:
                payload = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                payload = {"result": content}
            if not isinstance(payload, dict):
                payload = {"result": payload}
            contents.append({
                "role": "user",
                "parts": [{"function_response": {"name": name, "response": payload}}],
            })
            continue
        if role == "assistant" and msg.get("tool_calls"):
            parts: list[dict] = []
            if content:
                parts.append({"text": content})
            for tc in msg["tool_calls"]:
                fn = tc["function"]
                call_names[tc.get("id", "")] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                parts.append({"function_call": {"name": fn["name"], "args": args}})
            contents.append({"role": "model", "parts": parts})
            continue
        gem_role = "model" if role == "assistant" else "user"
        contents.append({"role": gem_role, "parts": [{"text": content}]})

    if images:
        for i in range(len(contents) - 1, -1, -1):
            if contents[i]["role"] == "user":
                for img in images:
                    contents[i]["parts"].append(
                        {"inline_data": {"mime_type": "image/jpeg", "data": img}}
                    )
                break
    return "\n".join(system_parts), contents


def _to_gemini_tools(runtime_tools: list[RuntimeToolDefinition]) -> list[dict]:
    return [{
        "function_declarations": [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in runtime_tools
        ]
    }]


def _parts(resp) -> list:
    try:
        return resp.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return []


def _text(resp) -> str:
    txt = getattr(resp, "text", None)
    if txt:
        return txt
    return "".join(getattr(p, "text", "") or "" for p in _parts(resp))


def _first_function_call(resp):
    for p in _parts(resp):
        fc = getattr(p, "function_call", None)
        if fc is not None:
            return fc
    return None


def _usage(resp, model: str) -> UsageStats:
    um = getattr(resp, "usage_metadata", None)
    usage = UsageStats(
        provider="google", model=model,
        input_tokens=getattr(um, "prompt_token_count", 0) or 0 if um else 0,
        output_tokens=getattr(um, "candidates_token_count", 0) or 0 if um else 0,
    )
    usage.calculate_cost()
    return usage


class GoogleProvider(ChatProvider):
    provider_id = "google"
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
                    from google import genai

                    self._client = genai.Client(
                        api_key=self._api_key,
                        http_options={"timeout": int(self._timeout * 1000)},
                    )
        return self._client

    def _config(self, system: str, tools: list[dict] | None) -> dict:
        cfg: dict = {
            "temperature": self._temperature,
            "max_output_tokens": _MAX_TOKENS,
        }
        if system:
            cfg["system_instruction"] = system
        if tools:
            cfg["tools"] = tools
        return cfg

    async def generate(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> tuple[str, UsageStats]:
        client = await self._get_client()
        system, contents = _to_gemini(messages, images)
        t0 = time.perf_counter()
        resp = await client.aio.models.generate_content(
            model=model, contents=contents, config=self._config(system, None)
        )
        usage = _usage(resp, model)
        usage.latency_ms = int((time.perf_counter() - t0) * 1000)
        return _text(resp), usage

    async def stream(
        self, model: str, messages: list[dict], images: list[str] | None = None
    ) -> AsyncGenerator[str | UsageStats, None]:
        client = await self._get_client()
        system, contents = _to_gemini(messages, images)
        t0 = time.perf_counter()
        acc = StreamUsageStats(provider="google", model=model)
        stream = await client.aio.models.generate_content_stream(
            model=model, contents=contents, config=self._config(system, None)
        )
        async for chunk in stream:
            token = getattr(chunk, "text", None)
            if token:
                yield token
            um = getattr(chunk, "usage_metadata", None)
            if um:
                acc.input_tokens = getattr(um, "prompt_token_count", 0) or 0
                acc.output_tokens = getattr(um, "candidates_token_count", 0) or 0
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
        system, contents = _to_gemini(messages, images)
        tools = _to_gemini_tools(runtime_tools) if runtime_tools else None
        resp = await client.aio.models.generate_content(
            model=model, contents=contents, config=self._config(system, tools)
        )
        usage = _usage(resp, model)
        fc = _first_function_call(resp)
        if fc is not None:
            name = fc.name
            args = dict(fc.args or {})
            call_id = f"google_{name}"
            openai_tool_calls = [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }]
            return ToolAwareLLMResponse(
                provider="google",
                tool_call=RuntimeToolCallRequest(call_id=call_id, tool_name=name, arguments=args),
                assistant_message={"role": "assistant", "content": _text(resp), "tool_calls": openai_tool_calls},
                usage=usage,
            )
        return ToolAwareLLMResponse(provider="google", text=_text(resp), usage=usage)

    async def aclose(self) -> None:
        # google-genai client holds no long-lived connection to close explicitly.
        self._client = None
