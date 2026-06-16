"""Generic runtime tool router (EP-004 slice 2).

Validates calls against the fixed allowlist, resolves the configured
provider, enforces a global timeout/result-size bound, and sanitizes output
before it is eligible for LLM context. No Talend-specific names appear here —
provider adapters translate generic tool calls into their own operation names.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from typing import Any

from pydantic import BaseModel

from src.config import settings
from src.runtime.models import (
    RuntimeToolCall,
    RuntimeToolDefinition,
    RuntimeToolError,
    RuntimeToolErrorCode,
    RuntimeToolResult,
)
from src.runtime.providers import RuntimeProvider, RuntimeProviderError
from src.runtime.registry import (
    RuntimeToolValidationError,
    UnknownRuntimeToolError,
    list_runtime_tool_definitions,
    resolve_tool_call,
)

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_MARKERS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "credential",
)

_PROVIDER_MODULE_PATHS: dict[str, str] = {
    "mock": "src.runtime.providers.mock",
    "talend_tmc": "src.runtime.providers.talend_tmc",
}


def list_runtime_tools() -> list[RuntimeToolDefinition]:
    return list_runtime_tool_definitions()


async def execute_runtime_tool(call: RuntimeToolCall) -> RuntimeToolResult:
    t0 = time.perf_counter()

    provider_key = settings.runtime_provider_key or None
    if not provider_key:
        return _error_result(call.tool_name, RuntimeToolErrorCode.DISABLED, "Live runtime disabled", t0)

    try:
        params = resolve_tool_call(call.tool_name, call.arguments)
    except UnknownRuntimeToolError:
        return _error_result(
            call.tool_name, RuntimeToolErrorCode.UNKNOWN_TOOL, f"Unknown tool: {call.tool_name}", t0
        )
    except RuntimeToolValidationError as e:
        return _error_result(call.tool_name, RuntimeToolErrorCode.VALIDATION_ERROR, e.message, t0)

    provider = _resolve_provider(provider_key)
    if provider is None:
        return _error_result(
            call.tool_name, RuntimeToolErrorCode.UNAVAILABLE, f"Unknown runtime provider: {provider_key}", t0
        )

    method = getattr(provider, call.tool_name)

    try:
        raw_result = await asyncio.wait_for(method(params), timeout=settings.runtime_tool_timeout)
    except TimeoutError:
        logger.warning("Runtime tool %s timed out (provider=%s)", call.tool_name, provider_key)
        return _error_result(call.tool_name, RuntimeToolErrorCode.TIMEOUT, f"{call.tool_name} timed out", t0)
    except RuntimeProviderError as e:
        logger.warning("Runtime tool %s failed (provider=%s): %s", call.tool_name, provider_key, e)
        return _error_result(call.tool_name, RuntimeToolErrorCode.PROVIDER_ERROR, str(e), t0)

    data, truncated = _sanitize_and_bound(raw_result, max_chars=settings.runtime_tool_result_max_chars)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("Runtime tool %s succeeded (provider=%s, %dms)", call.tool_name, provider_key, latency_ms)
    return RuntimeToolResult(
        tool_name=call.tool_name,
        success=True,
        data=data,
        error=None,
        latency_ms=latency_ms,
        truncated=truncated,
    )


def _error_result(tool_name: str, code: RuntimeToolErrorCode, message: str, t0: float) -> RuntimeToolResult:
    return RuntimeToolResult(
        tool_name=tool_name,
        success=False,
        data=None,
        error=RuntimeToolError(code=code, message=message),
        latency_ms=int((time.perf_counter() - t0) * 1000),
        truncated=False,
    )


def _resolve_provider(provider_key: str) -> RuntimeProvider | None:
    """Resolve a provider instance by key. Lazy-imported and cached by
    Python's module cache, following the resolve-at-call-time pattern used by
    llm_client._resolve so tests can patch provider methods directly."""
    module_path = _PROVIDER_MODULE_PATHS.get(provider_key)
    if module_path is None:
        return None
    module = importlib.import_module(module_path)
    return module.provider


def _sanitize_and_bound(value: Any, max_chars: int) -> tuple[Any, bool]:
    json_safe = _to_json_safe(value)
    redacted = _redact(json_safe)
    serialized = json.dumps(redacted, default=str)
    if len(serialized) <= max_chars:
        return redacted, False
    return {"truncated": True, "preview": serialized[:max_chars]}, True


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "***REDACTED***" if any(marker in k.lower() for marker in _SENSITIVE_KEY_MARKERS) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value
