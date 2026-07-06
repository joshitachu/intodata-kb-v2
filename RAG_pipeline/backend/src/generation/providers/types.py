"""Shared provider-facing types for the RAG chat provider layer.

Kept in their own module (not in ``llm_client`` or ``base``) so provider adapters
and the ``llm_client`` facade can both import them without a circular import.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.generation.usage import UsageStats


@dataclass
class RuntimeToolCallRequest:
    call_id: str
    tool_name: str
    arguments: dict


@dataclass
class ToolAwareLLMResponse:
    provider: str
    text: str | None = None
    tool_calls: list[RuntimeToolCallRequest] = field(default_factory=list)
    """All tool calls from the turn, in wire order. Models may issue several in
    one assistant message; every call_id here MUST later receive a tool message,
    or OpenAI-compat APIs reject the follow-up request."""
    assistant_message: dict | None = None
    """Assistant message shaped for the provider's own tool-call wire format
    (e.g. OpenAI tool_calls), to append to history when tool_calls is non-empty.
    Its ids must match tool_calls exactly — no extra, no missing."""
    no_tool_support: bool = False
    usage: UsageStats | None = None
