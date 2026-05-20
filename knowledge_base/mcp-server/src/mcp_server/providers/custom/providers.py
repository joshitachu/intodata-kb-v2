"""Thin async model adapters used by the custom harness."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mcp_server.config import config
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.services.token_refresh import ensure_token_fresh

logger = logging.getLogger(__name__)


class ProviderPayloadError(RuntimeError):
    """Raised when an outbound provider request is structurally invalid."""


class ChatMessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = ""


class OpenAIChatCompletionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessagePayload] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    temperature: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


class GeminiGenerateContentPayload(BaseModel):
    model: str
    contents: str = Field(min_length=1)
    config: dict[str, Any]

    @model_validator(mode="after")
    def _has_max_output_tokens(self):
        if int(self.config.get("max_output_tokens") or 0) <= 0:
            raise ValueError("config.max_output_tokens must be positive")
        return self


class AnthropicMessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    max_tokens: int = Field(gt=0)
    messages: list[ChatMessagePayload] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None


def _validated_payload(model: type[BaseModel], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return model.model_validate(payload).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ProviderPayloadError(str(exc)) from exc


class ModelProvider(Protocol):
    model_name: str
    supports_tools: bool

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        ...

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
        scoped_tools: Any | None = None,
    ) -> ModelResult:
        ...


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _read_context(context_files: list[Path] | None) -> str:
    if not context_files:
        return ""
    parts: list[str] = []
    for path in context_files:
        try:
            parts.append(f"\n\n--- CONTEXT FILE: {path.name} ---\n")
            parts.append(path.read_text(encoding="utf-8"))
        except OSError as exc:
            parts.append(f"\n\n--- CONTEXT FILE ERROR: {path} ({exc}) ---\n")
    return "".join(parts)


def _context_metadata(context_files: list[Path] | None) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path in context_files or []:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        files.append({"name": path.name, "bytes": size})
    return {
        "context_file_count": len(files),
        "context_bytes": total_bytes,
        "context_files": files,
    }


def _apply_prefill(prefill: str | None, raw_text: str) -> str:
    """Prepend prefill to raw_text, but skip if the model already echoed it."""
    if not prefill:
        return raw_text
    if raw_text.lstrip().startswith(prefill.lstrip()):
        return raw_text
    return prefill + raw_text


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError))


async def _sleep_for_retry(attempt: int, response: httpx.Response | None = None) -> None:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        await asyncio.sleep(min(retry_after, 30.0))
        return
    delay = min(2 ** attempt, 12) + random.uniform(0, 0.5)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Staging and scoped evidence tool definitions
# ---------------------------------------------------------------------------

STAGING_TOOLS_OPENAI: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_artifacts",
            "description": (
                "List files available in the staging directory. "
                "Pass subdir to scope to a subdirectory "
                "(e.g. '_preprocess', '_analysis', '_assessment')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subdir": {
                        "type": "string",
                        "description": "Optional subdirectory within staging (e.g. '_preprocess')",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": (
                "Read a file from the staging directory. "
                "Path is relative to staging root "
                "(e.g. '_preprocess/Item.md', '_analysis/_bundle_core.md')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within staging (e.g. '_preprocess/Item.md')",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_evidence",
            "description": "Search task-scoped EngineeringFacts, investigation, and analysis evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_unit_evidence",
            "description": "Fetch evidence for one canonical unit allowed by the current task contract.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_name": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["unit_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_evidence",
            "description": "Fetch evidence for one canonical group allowed by the current task contract.",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_name": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["group_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fact_table",
            "description": "Read a small task-relevant fact table such as units, groups, key_systems, or component_counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "filters": {"type": "object"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["table_name"],
            },
        },
    },
]

# Anthropic format: same semantics, different schema key (input_schema not parameters)
STAGING_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": tool["function"]["name"],
        "description": tool["function"]["description"],
        "input_schema": tool["function"]["parameters"],
    }
    for tool in STAGING_TOOLS_OPENAI
]

STAGING_TOOLS_OPENAI_RESPONSES: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": tool["function"]["name"],
        "description": tool["function"]["description"],
        "parameters": tool["function"]["parameters"],
    }
    for tool in STAGING_TOOLS_OPENAI
]


def _safe_limit(args: dict[str, Any], default: int) -> int:
    try:
        return max(1, int(args.get("limit") or default))
    except (TypeError, ValueError):
        return default


def _execute_staging_tool(
    name: str,
    args: dict[str, Any],
    staging_dir: Path,
    scoped_tools: Any | None = None,
) -> str:
    if name == "list_artifacts":
        subdir = args.get("subdir")
        target = (staging_dir / subdir).resolve() if subdir else staging_dir.resolve()
        if not str(target).startswith(str(staging_dir.resolve())):
            return "ERROR: path outside staging directory"
        if not target.exists():
            return f"Directory not found: {subdir or '.'}"
        files = sorted(p.relative_to(staging_dir).as_posix() for p in target.iterdir())
        return "\n".join(files) if files else "(empty)"

    if name == "read_artifact":
        path = args.get("path", "")
        target = (staging_dir / path).resolve()
        # Block path traversal
        if not str(target).startswith(str(staging_dir.resolve())):
            return "ERROR: path outside staging directory"
        if not target.exists():
            return f"ERROR: not found: {path}"
        return target.read_text(encoding="utf-8", errors="replace")

    if scoped_tools is None:
        return f"ERROR: scoped evidence tool '{name}' unavailable for this call"
    if name == "search_evidence":
        return scoped_tools.search_evidence(
            str(args.get("query") or ""),
            scope=args.get("scope"),
            categories=args.get("categories") or None,
            limit=_safe_limit(args, 20),
        )
    if name == "get_unit_evidence":
        return scoped_tools.get_unit_evidence(
            str(args.get("unit_name") or ""),
            categories=args.get("categories") or None,
            limit=_safe_limit(args, 20),
        )
    if name == "get_group_evidence":
        return scoped_tools.get_group_evidence(
            str(args.get("group_name") or ""),
            categories=args.get("categories") or None,
            limit=_safe_limit(args, 40),
        )
    if name == "get_fact_table":
        return scoped_tools.get_fact_table(
            str(args.get("table_name") or ""),
            filters=args.get("filters") or None,
            limit=_safe_limit(args, 50),
        )

    return f"ERROR: unknown tool '{name}'"


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _sdk_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _assert_contract(result: ModelResult, context: str = "") -> None:
    tag = f" [{context}]" if context else ""
    issues: list[str] = []
    if result.success and not result.text:
        issues.append(f"C1 violation: success=True but text is empty{tag}")
    if not result.success and result.raw_error is None:
        issues.append(f"C2 violation: success=False but raw_error is None{tag}")
    if result.input_tokens < 0 or result.output_tokens < 0:
        issues.append(f"C3 violation: token counts are negative{tag}")
    if not issues:
        return
    for msg in issues:
        if config.app_env != "production":
            raise AssertionError(msg)
        logger.warning("[provider-contract] %s", msg)


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_dict(value: Any) -> dict[str, Any]:
    items = _as_list(value)
    return _as_dict(items[0]) if items else {}


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            data = _as_dict(item)
            if data:
                chunks.append(str(data.get("text") or data.get("content") or ""))
            elif isinstance(item, str):
                chunks.append(item)
        return "".join(chunks)
    return ""


def _response_output_items(response: Any) -> list[Any]:
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    return list(output or [])


def _response_output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text is None and isinstance(response, dict):
        text = response.get("output_text")
    if text:
        return str(text)
    chunks: list[str] = []
    for item in _response_output_items(response):
        data = _model_dump(item)
        if data.get("type") == "message":
            for content in data.get("content") or []:
                if isinstance(content, dict):
                    chunks.append(str(content.get("text") or ""))
    return "".join(chunks)


def _response_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    data = _model_dump(usage)
    input_tokens = data.get("input_tokens") or data.get("prompt_tokens") or 0
    output_tokens = data.get("output_tokens") or data.get("completion_tokens") or 0
    return int(input_tokens or 0), int(output_tokens or 0)


def _chat_completion_message_text(message: Any) -> str:
    return _message_content_text(_as_dict(message).get("content"))


def _chat_completion_usage(data: Any) -> dict[str, Any]:
    return _as_dict(_as_dict(data).get("usage"))


def _normalize_openai_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in _as_list(tool_calls):
        call = _as_dict(item)
        if not call:
            continue
        function = _as_dict(call.get("function"))
        if not function:
            # Some compatible providers flatten the function payload.
            function = {
                "name": call.get("name") or call.get("tool_name") or call.get("tool"),
                "arguments": call.get("arguments") or call.get("args") or "{}",
            }
        normalized.append({
            **call,
            "id": str(call.get("id") or call.get("call_id") or ""),
            "function": function,
        })
    return normalized


class OpenAIOfficialProvider:
    """Adapter for OpenAI's official Python SDK and Responses API."""

    supports_tools: bool = True

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        self.provider_name = "openai"
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"

    def _client(self, timeout_s: float):
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self.api_key, timeout=timeout_s)

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        context = _read_context(context_files)
        full_prompt = prompt + context
        estimated_tokens = _estimate_tokens(full_prompt)
        ctx_limit = config.context_max_tokens
        if estimated_tokens > ctx_limit:
            raise RuntimeError(
                f"Context too large: ~{estimated_tokens:,} estimated tokens exceeds "
                f"MCP_CONTEXT_MAX_TOKENS={ctx_limit:,}. Reduce engineering facts or split the group."
            )
        metadata = {
            "provider": self.provider_name,
            "estimated_input_tokens": estimated_tokens,
            **_context_metadata(context_files),
        }
        start = time.monotonic()
        last_error: str | None = None

        for attempt in range(opts.retries + 1):
            try:
                payload: dict[str, Any] = {
                    "model": self.model_name,
                    "input": full_prompt,
                    "max_output_tokens": opts.max_tokens,
                }
                if opts.json_mode:
                    payload["text"] = {"format": {"type": "json_object"}}
                payload.update(opts.provider_options)
                response = await self._client(opts.timeout_s).responses.create(**payload)
                raw_text = _response_output_text(response)
                text = raw_text
                input_tokens, output_tokens = _response_usage(response)
                result = ModelResult(
                    success=True,
                    text=text,
                    model_name=self.model_name,
                    input_tokens=input_tokens or estimated_tokens,
                    output_tokens=output_tokens or _estimate_tokens(text),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    retries=attempt,
                    metadata=metadata,
                )
                _assert_contract(result, self.provider_name)
                return result
            except Exception as exc:
                last_error = _sdk_error(exc)
                if attempt < opts.retries:
                    await asyncio.sleep(min(2 ** attempt, 4))

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=estimated_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=opts.retries,
            raw_error=last_error,
            metadata=metadata,
        )

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
        scoped_tools: Any | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        start = time.monotonic()
        total_in = total_out = 0
        tool_call_count = 0
        tool_result_bytes = 0
        finish_reasons: list[str] = []
        conversation_trace: list[dict[str, Any]] = []
        tool_ledger: list[dict[str, Any]] = []
        response_id: str | None = None
        next_input: Any = prompt
        last_error: str | None = None

        for turn in range(max_turns):
            payload: dict[str, Any] = {
                "model": self.model_name,
                "input": next_input,
                "tools": STAGING_TOOLS_OPENAI_RESPONSES,
                "max_output_tokens": opts.max_tokens,
            }
            if response_id:
                payload["previous_response_id"] = response_id
            payload.update(opts.provider_options)

            try:
                response = await self._client(opts.timeout_s).responses.create(**payload)
                response_id = str(getattr(response, "id", "") or "")
                in_tokens, out_tokens = _response_usage(response)
                total_in += in_tokens
                total_out += out_tokens
                output_items = _response_output_items(response)
                function_calls: list[dict[str, Any]] = []
                for item in output_items:
                    data = _model_dump(item)
                    item_type = data.get("type")
                    if item_type in {"function_call", "function_call_output"} and data.get("name"):
                        function_calls.append(data)
                finish_reason = str(getattr(response, "status", "") or "")
                if finish_reason:
                    finish_reasons.append(finish_reason)
                conversation_trace.append({
                    "turn": turn + 1,
                    "role": "assistant",
                    "finish_reason": finish_reason,
                    "tool_calls": [
                        {
                            "id": fc.get("call_id", ""),
                            "name": fc.get("name", ""),
                            "arguments": fc.get("arguments", ""),
                        }
                        for fc in function_calls
                    ],
                    "content_bytes": len(_response_output_text(response).encode("utf-8")),
                })

                if not function_calls:
                    text = _apply_prefill(opts.prefill, _response_output_text(response))
                    result = ModelResult(
                        success=True,
                        text=text,
                        model_name=self.model_name,
                        input_tokens=total_in or _estimate_tokens(prompt),
                        output_tokens=total_out or _estimate_tokens(text),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=turn,
                        metadata={
                            "provider": self.provider_name,
                            "turns": turn + 1,
                            "tool_calls": tool_call_count,
                            "tool_result_bytes": tool_result_bytes,
                            "finish_reason": finish_reason,
                            "finish_reasons": finish_reasons,
                            "conversation_trace": conversation_trace,
                            "tool_call_ledger": tool_ledger,
                            "context_bytes": 0,
                        },
                    )
                    _assert_contract(result, self.provider_name)
                    return result

                next_input = []
                for call in function_calls:
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    args = _as_dict(args)
                    tool_name = str(call.get("name") or "")
                    ok = True
                    error = None
                    try:
                        result = _execute_staging_tool(tool_name, args, staging_dir, scoped_tools)
                    except Exception as exc:
                        ok = False
                        error = str(exc)
                        result = f"ERROR: {error}"
                    tool_call_count += 1
                    result_bytes = len(result.encode("utf-8"))
                    tool_result_bytes += result_bytes
                    tool_ledger.append({
                        "turn": turn + 1,
                        "tool": tool_name,
                        "args": args,
                        "ok": ok,
                        "bytes": result_bytes,
                        "error": error,
                    })
                    next_input.append({
                        "type": "function_call_output",
                        "call_id": call.get("call_id", ""),
                        "output": result,
                    })
            except Exception as exc:
                last_error = _sdk_error(exc)
                response_id = None
                if turn < max_turns - 1:
                    await asyncio.sleep(min(2 ** turn, 8))

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=max_turns,
            raw_error=last_error or "max_turns exceeded",
            metadata={
                "provider": self.provider_name,
                "turns": max_turns,
                "tool_calls": tool_call_count,
                "tool_result_bytes": tool_result_bytes,
                "finish_reasons": finish_reasons,
                "conversation_trace": conversation_trace,
                "tool_call_ledger": tool_ledger,
                "incomplete_reason": "max_turns",
            },
        )


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible chat-completions APIs."""

    supports_tools: bool = True

    def __init__(
        self,
        model_name: str,
        *,
        api_key: str | None,
        base_url: str,
        extra_body: dict[str, Any] | None = None,
        include_temperature: bool = True,
        provider_name: str = "openai",
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.extra_body = extra_body or {}
        self.include_temperature = include_temperature

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        context = _read_context(context_files)
        full_prompt = prompt + context
        estimated_tokens = _estimate_tokens(full_prompt)
        ctx_limit = config.context_max_tokens
        if estimated_tokens > ctx_limit:
            raise RuntimeError(
                f"Context too large: ~{estimated_tokens:,} estimated tokens exceeds "
                f"MCP_CONTEXT_MAX_TOKENS={ctx_limit:,}. Reduce engineering facts or split the group."
            )
        if estimated_tokens > int(ctx_limit * 0.85):
            logger.warning(
                "[provider] context near limit: ~%d tokens (limit %d, 85%% = %d)",
                estimated_tokens, ctx_limit, int(ctx_limit * 0.85),
            )
        metadata = {
            "provider": self.provider_name,
            "estimated_input_tokens": estimated_tokens,
            **_context_metadata(context_files),
        }
        if opts.provider_options:
            metadata["provider_options"] = opts.provider_options
        start = time.monotonic()
        last_error: str | None = None

        for attempt in range(opts.retries + 1):
            try:
                messages: list[dict[str, str]] = [{"role": "user", "content": full_prompt}]
                if opts.prefill:
                    messages.append({"role": "assistant", "content": opts.prefill})
                payload: dict[str, Any] = {
                    "model": self.model_name,
                    "messages": messages,
                    "max_tokens": opts.max_tokens,
                }
                if self.include_temperature:
                    payload["temperature"] = opts.temperature
                if opts.json_mode:
                    payload["response_format"] = {"type": "json_object"}
                payload.update(self.extra_body)
                payload.update(opts.provider_options)
                payload = _validated_payload(OpenAIChatCompletionPayload, payload)
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                async with httpx.AsyncClient(timeout=opts.timeout_s) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = _as_dict(resp.json())
                choice = _first_dict(_as_dict(data).get("choices"))
                message = _as_dict(choice.get("message"))
                raw_text = _chat_completion_message_text(message)
                text = _apply_prefill(opts.prefill, raw_text)
                usage = _chat_completion_usage(data)
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    metadata["finish_reason"] = finish_reason
                result = ModelResult(
                    success=True,
                    text=text,
                    model_name=self.model_name,
                    input_tokens=int(usage.get("prompt_tokens") or _estimate_tokens(full_prompt)),
                    output_tokens=int(usage.get("completion_tokens") or _estimate_tokens(text)),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    retries=attempt,
                    metadata=metadata,
                )
                _assert_contract(result, self.provider_name)
                return result
            except httpx.HTTPStatusError as exc:
                body = exc.response.text.strip()
                last_error = (
                    f"{type(exc).__name__}: {exc.response.status_code} "
                    f"{exc.response.reason_phrase} for {exc.request.method} {exc.request.url}"
                )
                if body:
                    last_error = f"{last_error} :: {body}"
                if attempt < opts.retries and _is_retryable_exception(exc):
                    await _sleep_for_retry(attempt, exc.response)
                else:
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < opts.retries and _is_retryable_exception(exc):
                    await _sleep_for_retry(attempt)
                else:
                    break

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=_estimate_tokens(full_prompt),
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=opts.retries,
            raw_error=last_error,
            metadata=metadata,
        )

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
        scoped_tools: Any | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        start = time.monotonic()
        total_in = total_out = 0
        tool_call_count = 0
        tool_result_bytes = 0
        finish_reasons: list[str] = []
        conversation_trace: list[dict[str, Any]] = []
        tool_ledger: list[dict[str, Any]] = []
        last_error: str | None = None

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for turn in range(max_turns):
            payload: dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "tools": STAGING_TOOLS_OPENAI,
                "tool_choice": "auto",
                "max_tokens": opts.max_tokens,
            }
            if self.include_temperature:
                payload["temperature"] = opts.temperature
            payload.update(self.extra_body)
            payload.update(opts.provider_options)
            payload = _validated_payload(OpenAIChatCompletionPayload, payload)

            try:
                async with httpx.AsyncClient(timeout=opts.timeout_s) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                data = _as_dict(resp.json())

                usage = _chat_completion_usage(data)
                total_in += int(usage.get("prompt_tokens") or 0)
                total_out += int(usage.get("completion_tokens") or 0)

                choice = _first_dict(data.get("choices"))
                message = _as_dict(choice.get("message"))
                tool_calls = _normalize_openai_tool_calls(message.get("tool_calls"))
                finish_reason = choice.get("finish_reason", "")
                if finish_reason:
                    finish_reasons.append(finish_reason)
                conversation_trace.append({
                    "turn": turn + 1,
                    "role": "assistant",
                    "finish_reason": finish_reason,
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "name": _as_dict(tc.get("function")).get("name", ""),
                            "arguments": _as_dict(tc.get("function")).get("arguments", ""),
                        }
                        for tc in tool_calls
                    ],
                    "content_bytes": len(_chat_completion_message_text(message).encode("utf-8")),
                })

                if finish_reason == "length":
                    text = _apply_prefill(opts.prefill, _chat_completion_message_text(message))
                    return ModelResult(
                        success=False,
                        text=text,
                        model_name=self.model_name,
                        input_tokens=total_in or _estimate_tokens(prompt),
                        output_tokens=total_out or _estimate_tokens(text),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=turn,
                        raw_error="incomplete: provider stopped because max tokens were reached",
                        metadata={
                            "provider": self.provider_name,
                            "turns": turn + 1,
                            "tool_calls": tool_call_count,
                            "tool_result_bytes": tool_result_bytes,
                            "finish_reason": finish_reason,
                            "finish_reasons": finish_reasons,
                            "conversation_trace": conversation_trace,
                            "tool_call_ledger": tool_ledger,
                            "incomplete_reason": "length",
                            "context_bytes": 0,
                        },
                    )

                if not tool_calls or finish_reason == "stop":
                    text = _apply_prefill(opts.prefill, _chat_completion_message_text(message))
                    result = ModelResult(
                        success=True,
                        text=text,
                        model_name=self.model_name,
                        input_tokens=total_in or _estimate_tokens(prompt),
                        output_tokens=total_out or _estimate_tokens(text),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=turn,
                        metadata={
                            "provider": self.provider_name,
                            "turns": turn + 1,
                            "tool_calls": tool_call_count,
                            "tool_result_bytes": tool_result_bytes,
                            "finish_reason": finish_reason,
                            "finish_reasons": finish_reasons,
                            "conversation_trace": conversation_trace,
                            "tool_call_ledger": tool_ledger,
                            "context_bytes": 0,
                        },
                    )
                    _assert_contract(result, self.provider_name)
                    return result

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
                if message.get("reasoning_content") is not None:
                    assistant_msg["reasoning_content"] = message["reasoning_content"]
                messages.append(assistant_msg)
                for tc in tool_calls:
                    fn = _as_dict(tc.get("function"))
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    args = _as_dict(args)
                    tool_name = fn.get("name", "")
                    ok = True
                    error = None
                    try:
                        result = _execute_staging_tool(tool_name, args, staging_dir, scoped_tools)
                    except Exception as exc:
                        ok = False
                        error = str(exc)
                        result = f"ERROR: {error}"
                    tool_call_count += 1
                    result_bytes = len(result.encode("utf-8"))
                    tool_result_bytes += result_bytes
                    tool_ledger.append({
                        "turn": turn + 1,
                        "tool": tool_name,
                        "args": args,
                        "ok": ok,
                        "bytes": result_bytes,
                        "error": error,
                    })
                    logger.debug("[provider] tool %s -> %d chars", fn.get("name"), len(result))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    })

            except httpx.HTTPStatusError as exc:
                body = exc.response.text.strip()
                last_error = (
                    f"{type(exc).__name__}: {exc.response.status_code} "
                    f"{exc.response.reason_phrase} for {exc.request.method} {exc.request.url}"
                )
                if body:
                    last_error = f"{last_error} :: {body}"
                if turn < max_turns - 1 and _is_retryable_exception(exc):
                    await _sleep_for_retry(turn, exc.response)
                else:
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if turn < max_turns - 1 and _is_retryable_exception(exc):
                    await _sleep_for_retry(turn)
                else:
                    break

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=max_turns,
            raw_error=last_error or "max_turns exceeded",
            metadata={
                "provider": self.provider_name,
                "turns": max_turns,
                "tool_calls": tool_call_count,
                "tool_result_bytes": tool_result_bytes,
                "finish_reasons": finish_reasons,
                "conversation_trace": conversation_trace,
                "tool_call_ledger": tool_ledger,
                "incomplete_reason": "max_turns",
            },
        )


class MoonshotKimiProvider(OpenAICompatibleProvider):
    """Moonshot/Kimi adapter using its OpenAI-compatible endpoint."""

    supports_tools: bool = True

    def __init__(self, model_name: str, api_key: str | None, base_url: str | None = None) -> None:
        super().__init__(
            model_name,
            api_key=api_key or os.environ.get("MOONSHOT_API_KEY"),
            base_url=base_url or "https://api.moonshot.ai/v1",
            extra_body={"thinking": {"type": "disabled"}},
            include_temperature=False,
            provider_name="moonshot",
        )


class GoogleGeminiProvider:
    """Gemini adapter using Google's official Gen AI SDK."""

    supports_tools: bool = False

    def __init__(self, model_name: str, api_key: str | None, base_url: str | None = None) -> None:
        self.provider_name = "google"
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.base_url = base_url or "https://generativelanguage.googleapis.com"

    def _client(self):
        from google import genai

        return genai.Client(api_key=self.api_key)

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        context = _read_context(context_files)
        full_prompt = prompt + context
        estimated_tokens = _estimate_tokens(full_prompt)
        ctx_limit = config.context_max_tokens
        if estimated_tokens > ctx_limit:
            raise RuntimeError(
                f"Context too large: ~{estimated_tokens:,} estimated tokens exceeds "
                f"MCP_CONTEXT_MAX_TOKENS={ctx_limit:,}. Reduce engineering facts or split the group."
            )
        metadata = {
            "provider": self.provider_name,
            "estimated_input_tokens": estimated_tokens,
            **_context_metadata(context_files),
        }
        start = time.monotonic()
        last_error: str | None = None

        for attempt in range(opts.retries + 1):
            try:
                client = self._client()
                gemini_config: dict[str, Any] = {
                    "max_output_tokens": opts.max_tokens,
                    "temperature": opts.temperature,
                    **opts.provider_options,
                }
                if opts.json_mode:
                    gemini_config["response_mime_type"] = "application/json"
                payload = _validated_payload(
                    GeminiGenerateContentPayload,
                    {
                        "model": self.model_name,
                        "contents": full_prompt,
                        "config": gemini_config,
                    },
                )
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    **payload,
                )
                raw_text = str(getattr(response, "text", "") or "")
                # Gemini does not support server-side prefill; applying it would
                # corrupt the output when the model returns a preamble before JSON.
                text = raw_text
                usage = getattr(response, "usage_metadata", None)
                usage_data = _model_dump(usage)
                result = ModelResult(
                    success=True,
                    text=text,
                    model_name=self.model_name,
                    input_tokens=int(usage_data.get("prompt_token_count") or estimated_tokens),
                    output_tokens=int(usage_data.get("candidates_token_count") or _estimate_tokens(text)),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    retries=attempt,
                    metadata=metadata,
                )
                _assert_contract(result, self.provider_name)
                return result
            except Exception as exc:
                last_error = _sdk_error(exc)
                if attempt < opts.retries:
                    await _sleep_for_retry(attempt)

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=estimated_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=opts.retries,
            raw_error=last_error,
            metadata=metadata,
        )

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
        scoped_tools: Any | None = None,
    ) -> ModelResult:
        raise NotImplementedError("GoogleGeminiProvider does not support harness tool-use")


class OllamaCompatibleProvider(OpenAICompatibleProvider):
    """Ollama adapter via its OpenAI-compatible `/v1` API."""

    supports_tools: bool = False

    def __init__(self, model_name: str, base_url: str | None = None) -> None:
        self.api_root = (base_url or "http://localhost:11434").rstrip("/")
        url = self.api_root
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        super().__init__(model_name, api_key="ollama", base_url=url, provider_name="ollama")

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        provider_options = dict(opts.provider_options)
        options_payload = dict(provider_options.get("options") or {})
        options_payload.setdefault("num_ctx", 32768)
        provider_options["options"] = options_payload
        opts = opts.model_copy(update={"provider_options": provider_options})
        try:
            async with httpx.AsyncClient(timeout=min(opts.timeout_s, 5.0)) as client:
                resp = await client.get(f"{self.api_root}/api/version")
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip()
            detail = (
                f"Ollama endpoint {self.api_root} returned {exc.response.status_code} "
                f"for GET /api/version"
            )
            if body:
                detail = f"{detail} :: {body}"
            return ModelResult(
                success=False,
                model_name=self.model_name,
                input_tokens=_estimate_tokens(prompt + _read_context(context_files)),
                raw_error=detail,
                metadata={
                    "provider": self.provider_name,
                    "configured_context": options_payload.get("num_ctx"),
                    **_context_metadata(context_files),
                },
            )
        except Exception as exc:
            return ModelResult(
                success=False,
                model_name=self.model_name,
                input_tokens=_estimate_tokens(prompt + _read_context(context_files)),
                raw_error=f"Ollama endpoint unreachable at {self.api_root}: {type(exc).__name__}: {exc}",
                metadata={
                    "provider": self.provider_name,
                    "configured_context": options_payload.get("num_ctx"),
                    **_context_metadata(context_files),
                },
            )
        return await super().complete(prompt, context_files, opts)

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
    ) -> ModelResult:
        raise NotImplementedError("OllamaCompatibleProvider does not support tool-use")


class ClaudeCliModelProvider:
    """Text-completion adapter backed by the local Claude Code CLI."""

    supports_tools: bool = False
    provider_name = "claude-cli"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = (model_name or "").strip()

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        context = _read_context(context_files)
        full_prompt = prompt + context
        estimated_tokens = _estimate_tokens(full_prompt)
        metadata = {
            "provider": self.provider_name,
            "estimated_input_tokens": estimated_tokens,
            **_context_metadata(context_files),
        }
        start = time.monotonic()

        if not ensure_token_fresh():
            return ModelResult(
                success=False,
                model_name=self.model_name or "claude",
                input_tokens=estimated_tokens,
                latency_ms=int((time.monotonic() - start) * 1000),
                raw_error="Claude OAuth token expired and refresh failed. Run 'claude login' as the service user.",
                metadata=metadata,
            )

        resolved_cmd = shutil.which(config.claude_command)
        if not resolved_cmd:
            return ModelResult(
                success=False,
                model_name=self.model_name or "claude",
                input_tokens=estimated_tokens,
                latency_ms=int((time.monotonic() - start) * 1000),
                raw_error=f"Claude CLI not found on PATH: {config.claude_command}",
                metadata=metadata,
            )

        cmd = [
            resolved_cmd,
            "-p", "-",
            "--output-format", "json",
            "--max-turns", "1",
            "--tools", "",
        ]
        if self.model_name:
            cmd[3:3] = ["--model", self.model_name]

        last_error: str | None = None
        for attempt in range(opts.retries + 1):
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    input=full_prompt,
                    text=True,
                    encoding="utf-8",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=opts.timeout_s,
                    cwd=str(Path(__file__).resolve().parent.parent.parent.parent.parent),
                    env={**os.environ, "PYTHONUTF8": "1"},
                    check=False,
                )
                if proc.returncode != 0:
                    last_error = proc.stderr.strip() or proc.stdout.strip() or f"Claude CLI exited with {proc.returncode}"
                    if attempt < opts.retries:
                        await asyncio.sleep(min(2 ** attempt, 4))
                        continue
                    break

                raw_stdout = proc.stdout.strip()
                data = json.loads(raw_stdout) if raw_stdout else {}
                text = _apply_prefill(opts.prefill, data.get("result") or data.get("text") or raw_stdout)
                usage = data.get("usage", {}) or {}
                input_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                )
                model_usage = data.get("modelUsage", {}) or {}
                result_model = next(iter(model_usage.keys()), self.model_name or "claude")
                result = ModelResult(
                    success=True,
                    text=text,
                    model_name=result_model,
                    input_tokens=int(input_tokens or estimated_tokens),
                    output_tokens=int(usage.get("output_tokens") or _estimate_tokens(text)),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    retries=attempt,
                    metadata={**metadata, "session_id": data.get("session_id")},
                )
                _assert_contract(result, self.provider_name)
                return result
            except subprocess.TimeoutExpired as exc:
                last_error = f"Claude CLI timed out after {exc.timeout}s"
            except json.JSONDecodeError as exc:
                last_error = f"Claude CLI returned invalid JSON: {exc}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < opts.retries:
                await asyncio.sleep(min(2 ** attempt, 4))

        return ModelResult(
            success=False,
            model_name=self.model_name or "claude",
            input_tokens=estimated_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=opts.retries,
            raw_error=last_error,
            metadata=metadata,
        )

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
    ) -> ModelResult:
        raise NotImplementedError("ClaudeCliModelProvider does not support tool-use")


class AnthropicProvider:
    """Adapter for Anthropic's official Python SDK and Messages API."""

    supports_tools: bool = True

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.provider_name = "anthropic"
        self.base_url = "https://api.anthropic.com"

    def _client(self, timeout_s: float):
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=self.api_key, timeout=timeout_s)

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        opts = options or ModelOptions()
        context = _read_context(context_files)
        full_prompt = prompt + context
        estimated_tokens = _estimate_tokens(full_prompt)
        ctx_limit = config.context_max_tokens
        if estimated_tokens > ctx_limit:
            raise RuntimeError(
                f"Context too large: ~{estimated_tokens:,} estimated tokens exceeds "
                f"MCP_CONTEXT_MAX_TOKENS={ctx_limit:,}. Reduce engineering facts or split the group."
            )
        if estimated_tokens > int(ctx_limit * 0.85):
            logger.warning(
                "[provider] context near limit: ~%d tokens (limit %d, 85%% = %d)",
                estimated_tokens, ctx_limit, int(ctx_limit * 0.85),
            )
        metadata = {
            "provider": self.provider_name,
            "estimated_input_tokens": estimated_tokens,
            **_context_metadata(context_files),
        }
        start = time.monotonic()
        last_error: str | None = None

        for attempt in range(opts.retries + 1):
            try:
                messages: list[dict[str, str]] = [{"role": "user", "content": full_prompt}]
                if opts.prefill:
                    messages.append({"role": "assistant", "content": opts.prefill})
                payload = _validated_payload(
                    AnthropicMessagePayload,
                    {
                        "model": self.model_name,
                        "max_tokens": opts.max_tokens,
                        "messages": messages,
                        **opts.provider_options,
                    },
                )
                response = await self._client(opts.timeout_s).messages.create(**payload)
                data = response.model_dump()
                blocks = data.get("content") or []
                raw_text = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
                stop_reason = data.get("stop_reason")
                usage = data.get("usage", {}) or {}
                if stop_reason == "max_tokens":
                    return ModelResult(
                        success=False,
                        text=_apply_prefill(opts.prefill, raw_text),
                        model_name=self.model_name,
                        input_tokens=int(usage.get("input_tokens") or _estimate_tokens(full_prompt)),
                        output_tokens=int(usage.get("output_tokens") or _estimate_tokens(raw_text)),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=attempt,
                        raw_error="incomplete: stop_reason=max_tokens",
                        metadata=metadata,
                    )
                text = _apply_prefill(opts.prefill, raw_text)
                result = ModelResult(
                    success=True,
                    text=text,
                    model_name=self.model_name,
                    input_tokens=int(usage.get("input_tokens") or _estimate_tokens(full_prompt)),
                    output_tokens=int(usage.get("output_tokens") or _estimate_tokens(text)),
                    latency_ms=int((time.monotonic() - start) * 1000),
                    retries=attempt,
                    metadata=metadata,
                )
                _assert_contract(result, self.provider_name)
                return result
            except Exception as exc:
                last_error = _sdk_error(exc)
                if attempt < opts.retries:
                    await _sleep_for_retry(attempt)

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=_estimate_tokens(full_prompt),
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=opts.retries,
            raw_error=last_error,
            metadata=metadata,
        )

    async def complete_with_tools(
        self,
        prompt: str,
        staging_dir: Path,
        options: ModelOptions | None = None,
        max_turns: int = 30,
        scoped_tools: Any | None = None,
    ) -> ModelResult:
        import anthropic as _anthropic

        opts = options or ModelOptions()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        start = time.monotonic()
        total_in = total_out = 0
        tool_call_count = 0
        tool_result_bytes = 0
        finish_reasons: list[str] = []
        conversation_trace: list[dict[str, Any]] = []
        tool_ledger: list[dict[str, Any]] = []
        last_error: str | None = None

        for turn in range(max_turns):
            try:
                payload = _validated_payload(
                    AnthropicMessagePayload,
                    {
                        "model": self.model_name,
                        "max_tokens": opts.max_tokens,
                        "messages": messages,
                        "tools": STAGING_TOOLS_ANTHROPIC,
                        **opts.provider_options,
                    },
                )
                response = await self._client(opts.timeout_s).messages.create(**payload)
                data = response.model_dump()

                usage = data.get("usage") or {}
                total_in += int(usage.get("input_tokens") or 0)
                total_out += int(usage.get("output_tokens") or 0)

                content_blocks = data.get("content") or []
                stop_reason = data.get("stop_reason", "")
                if stop_reason:
                    finish_reasons.append(stop_reason)
                tool_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
                conversation_trace.append({
                    "turn": turn + 1,
                    "role": "assistant",
                    "finish_reason": stop_reason,
                    "tool_calls": [
                        {"id": b.get("id", ""), "name": b.get("name", ""), "arguments": b.get("input") or {}}
                        for b in tool_blocks
                    ],
                    "content_bytes": sum(
                        len((b.get("text") or "").encode("utf-8"))
                        for b in content_blocks
                        if b.get("type") == "text"
                    ),
                })

                if stop_reason == "max_tokens":
                    final_text = "".join(
                        b.get("text", "") for b in content_blocks if b.get("type") == "text"
                    )
                    return ModelResult(
                        success=False,
                        text=_apply_prefill(opts.prefill, final_text),
                        model_name=self.model_name,
                        input_tokens=total_in or _estimate_tokens(prompt),
                        output_tokens=total_out or _estimate_tokens(final_text),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=turn,
                        raw_error="incomplete: provider stopped because max tokens were reached",
                        metadata={
                            "provider": self.provider_name,
                            "turns": turn + 1,
                            "tool_calls": tool_call_count,
                            "tool_result_bytes": tool_result_bytes,
                            "finish_reason": "length",
                            "finish_reasons": finish_reasons,
                            "conversation_trace": conversation_trace,
                            "tool_call_ledger": tool_ledger,
                            "incomplete_reason": "length",
                            "context_bytes": 0,
                        },
                    )

                if stop_reason != "tool_use" or not tool_blocks:
                    final_text = "".join(
                        b.get("text", "") for b in content_blocks if b.get("type") == "text"
                    )
                    result = ModelResult(
                        success=True,
                        text=_apply_prefill(opts.prefill, final_text),
                        model_name=self.model_name,
                        input_tokens=total_in or _estimate_tokens(prompt),
                        output_tokens=total_out or _estimate_tokens(final_text),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        retries=turn,
                        metadata={
                            "provider": self.provider_name,
                            "turns": turn + 1,
                            "tool_calls": tool_call_count,
                            "tool_result_bytes": tool_result_bytes,
                            "finish_reason": stop_reason,
                            "finish_reasons": finish_reasons,
                            "conversation_trace": conversation_trace,
                            "tool_call_ledger": tool_ledger,
                            "context_bytes": 0,
                        },
                    )
                    _assert_contract(result, self.provider_name)
                    return result

                # Append full assistant turn (content blocks)
                messages.append({"role": "assistant", "content": content_blocks})

                # Execute tools and collect results as a single user message
                tool_results = []
                for block in tool_blocks:
                    args = block.get("input") or {}
                    tool_name = block.get("name", "")
                    ok = True
                    error = None
                    try:
                        result = _execute_staging_tool(tool_name, args, staging_dir, scoped_tools)
                    except Exception as exc:
                        ok = False
                        error = str(exc)
                        result = f"ERROR: {error}"
                    tool_call_count += 1
                    result_bytes = len(result.encode("utf-8"))
                    tool_result_bytes += result_bytes
                    tool_ledger.append({
                        "turn": turn + 1,
                        "tool": tool_name,
                        "args": args,
                        "ok": ok,
                        "bytes": result_bytes,
                        "error": error,
                    })
                    logger.debug("[provider] tool %s -> %d chars", block.get("name"), len(result))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.get("id", ""),
                        "content": result,
                    })
                messages.append({"role": "user", "content": tool_results})

            except (_anthropic.AuthenticationError, _anthropic.PermissionDeniedError) as exc:
                last_error = _sdk_error(exc)
                break
            except Exception as exc:
                last_error = _sdk_error(exc)
                if turn < max_turns - 1:
                    await _sleep_for_retry(turn)

        return ModelResult(
            success=False,
            model_name=self.model_name,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=int((time.monotonic() - start) * 1000),
            retries=max_turns,
            raw_error=last_error or "max_turns exceeded",
            metadata={
                "provider": self.provider_name,
                "turns": max_turns,
                "tool_calls": tool_call_count,
                "tool_result_bytes": tool_result_bytes,
                "finish_reasons": finish_reasons,
                "conversation_trace": conversation_trace,
                "tool_call_ledger": tool_ledger,
                "incomplete_reason": "max_turns",
            },
        )


def build_model_provider(
    model_str: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    ollama_base_url: str | None = None,
) -> ModelProvider:
    """Create a provider from `provider/model` strings."""
    from mcp_server.model_strategies.registry import create_model_provider

    return create_model_provider(
        model_str,
        api_key=api_key,
        base_url=base_url,
        ollama_base_url=ollama_base_url,
    )
