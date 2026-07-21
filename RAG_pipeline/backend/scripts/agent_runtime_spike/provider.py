"""Exact live Kimi profile and authenticated-attempt planning."""

from __future__ import annotations

from pydantic import SecretStr

from langchain_openai import ChatOpenAI

from scripts.agent_runtime_eval.models import Scenario

from .models import KIMI_PROFILE


def build_kimi_chat_model(api_key: str) -> ChatOpenAI:
    if not api_key.strip():
        raise ValueError("KIMI_API_KEY is unavailable")
    return ChatOpenAI(
        model=KIMI_PROFILE.provider_model,
        api_key=SecretStr(api_key),
        base_url=KIMI_PROFILE.base_url,
        temperature=KIMI_PROFILE.temperature,
        timeout=KIMI_PROFILE.timeout_seconds,
        max_retries=KIMI_PROFILE.max_retries,
        extra_body={**KIMI_PROFILE.reasoning, "max_tokens": KIMI_PROFILE.max_tokens},
        streaming=True,
        stream_usage=True,
    )


def category_system_suffix(category: str, scenario: Scenario) -> str:
    if category == "direct":
        return "Answer directly with a natural sentence containing the word Nexus. Do not call a tool."
    if category == "single_read":
        tool = scenario.turns[0].expected.allowed_tool_sequences[0][0]
        return (
            f"Call {tool} exactly once, consume its observation, then answer. "
            "Use project_id fixture-project and technology Talend whenever those fields exist."
        )
    if category in {"two_tool", "checkpoint"}:
        return (
            "Call workflow_status_read first. After consuming that observation, call pack_list. "
            "Never propose both calls in one message. Then give one natural combined answer."
        )
    if category == "typed_error":
        return (
            "Call documentation_search first with sort_mode made-up. Consume the typed validation "
            "failure, retry the same tool with sort_mode relevance, then answer naturally."
        )
    if category == "streaming":
        return "Answer directly in at least eight words containing Nexus. Do not call a tool."
    raise ValueError(f"unknown attempt category: {category}")
