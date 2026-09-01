"""Tests for the RAG chat facade — route-by-provider semantics.

The facade resolves the superadmin-selected ``provider/model`` id to exactly one
provider and calls it with the bare model name. There is no cross-provider
fallback chain; a provider error is surfaced.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.generation import llm_client
from src.generation.llm_client import (
    RuntimeToolCallRequest,
    ToolAwareLLMResponse,
    generate_answer,
    generate_answer_stream,
    generate_answer_with_runtime_tools,
    generate_document_answer,
    generate_document_answer_stream,
    get_documentation_provider_trace,
    get_last_usage,
    get_turn_usage,
    reset_turn_usage,
)
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition

MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is 2+2?"},
]


class FakeProvider:
    """Duck-typed stand-in for a ChatProvider; behavior wired per test."""

    def __init__(self, provider_id="fake", supports_tools=True):
        self.provider_id = provider_id
        self.supports_tools = supports_tools
        self.generate = AsyncMock()
        self.generate_with_tools = AsyncMock()
        self.seen_model = None
        self._stream_tokens = []
        self._stream_error = None

    async def stream(self, model, messages, images=None):  # type: ignore[override]
        self.seen_model = model
        if self._stream_error:
            raise self._stream_error
        for t in self._stream_tokens:
            yield t
        yield UsageStats(provider=self.provider_id, model=model)


def _patch_selection(provider, bare_model):
    """Patch the facade so resolution returns (provider, bare_model)."""
    return (
        patch.object(llm_client, "get_rag_chat_model_id", AsyncMock(return_value=f"{provider.provider_id}/{bare_model}")),
        patch.object(llm_client.registry, "resolve", return_value=(provider, bare_model)),
    )


# ---------------------------------------------------------------------------
# generate_answer
# ---------------------------------------------------------------------------


async def test_routes_to_selected_provider_with_bare_model():
    """ollama/gemma3:12b → ollama provider called with bare 'gemma3:12b'."""
    provider = FakeProvider(provider_id="ollama")
    provider.generate.return_value = ("Ollama answer", UsageStats(provider="ollama", model="gemma3:12b"))
    p_id, p_resolve = _patch_selection(provider, "gemma3:12b")
    with p_id, p_resolve:
        result = await generate_answer(MESSAGES)

    assert result == "Ollama answer"
    provider.generate.assert_awaited_once_with(messages=MESSAGES, model="gemma3:12b", images=None)


async def test_provider_error_surfaces_no_fallback():
    """Selected provider fails → RuntimeError; no other provider is tried."""
    provider = FakeProvider(provider_id="kimi")
    provider.generate.side_effect = Exception("kimi 500")
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        with pytest.raises(RuntimeError, match="kimi.*failed"):
            await generate_answer(MESSAGES)


async def test_document_answer_retries_then_uses_approved_fallback():
    kimi = FakeProvider(provider_id="kimi")
    kimi.generate.side_effect = RuntimeError("transient")
    openai = FakeProvider(provider_id="openai")
    openai.generate.return_value = (
        "grounded answer",
        UsageStats(provider="openai", model="gpt-5"),
    )

    def resolve(model_id):  # noqa: ANN001, ANN202
        return {
            "kimi/kimi-k2.6": (kimi, "kimi-k2.6"),
            "openai/gpt-5": (openai, "gpt-5"),
        }[model_id]

    with (
        patch.object(llm_client, "get_rag_chat_model_id", AsyncMock(return_value="kimi/kimi-k2.6")),
        patch.object(llm_client.registry, "resolve", side_effect=resolve),
        patch.object(settings, "rag_approved_fallback_models", ["openai/gpt-5"]),
        patch.object(settings, "rag_documentation_provider_max_retries", 1),
    ):
        assert await generate_document_answer(MESSAGES) == "grounded answer"

    assert kimi.generate.await_count == 2
    assert openai.generate.await_count == 1
    assert get_documentation_provider_trace() == {
        "provider_used": "openai",
        "fallback_used": True,
        "attempts": [
            {"provider": "kimi", "status": "failed"},
            {"provider": "openai", "status": "succeeded"},
        ],
    }


async def test_document_answer_all_approved_providers_fail_explicitly():
    kimi = FakeProvider(provider_id="kimi")
    kimi.generate.side_effect = RuntimeError("down")
    with (
        patch.object(llm_client, "get_rag_chat_model_id", AsyncMock(return_value="kimi/kimi-k2.6")),
        patch.object(llm_client.registry, "resolve", return_value=(kimi, "kimi-k2.6")),
        patch.object(settings, "rag_approved_fallback_models", []),
        patch.object(settings, "rag_documentation_provider_max_retries", 0),
    ):
        with pytest.raises(RuntimeError, match="All approved documentation providers"):
            await generate_document_answer(MESSAGES)

    assert get_documentation_provider_trace() == {
        "provider_used": None,
        "fallback_used": False,
        "attempts": [{"provider": "kimi", "status": "failed"}],
    }


async def test_document_stream_never_fails_over_after_output_begins():
    kimi = FakeProvider(provider_id="kimi")
    openai = FakeProvider(provider_id="openai")

    async def partial_stream(model, messages, images=None):  # noqa: ANN001, ANN202
        del model, messages, images
        yield "visible"
        raise RuntimeError("mid-stream")

    kimi.stream = partial_stream
    openai._stream_tokens = ["replacement"]

    def resolve(model_id):  # noqa: ANN001, ANN202
        return {
            "kimi/kimi-k2.6": (kimi, "kimi-k2.6"),
            "openai/gpt-5": (openai, "gpt-5"),
        }[model_id]

    with (
        patch.object(llm_client, "get_rag_chat_model_id", AsyncMock(return_value="kimi/kimi-k2.6")),
        patch.object(llm_client.registry, "resolve", side_effect=resolve),
        patch.object(settings, "rag_approved_fallback_models", ["openai/gpt-5"]),
        patch.object(settings, "rag_documentation_provider_max_retries", 1),
    ):
        seen = []
        with pytest.raises(RuntimeError, match="after answer output began"):
            async for item in generate_document_answer_stream(MESSAGES):
                seen.append(item)

    assert seen == ["visible"]
    assert openai.seen_model is None


async def test_unknown_provider_raises():
    """An id whose provider is not registered raises a clear error."""
    with (
        patch.object(llm_client, "get_rag_chat_model_id", AsyncMock(return_value="mystery/model-x")),
        patch.object(llm_client.registry, "reset_for_tests"),
    ):
        # Use the real registry.resolve so the unknown provider raises.
        with pytest.raises((RuntimeError, ValueError)):
            await generate_answer(MESSAGES)


# ---------------------------------------------------------------------------
# generate_answer_stream
# ---------------------------------------------------------------------------


async def test_stream_routes_and_strips_prefix():
    provider = FakeProvider(provider_id="ollama")
    provider._stream_tokens = ["Hello ", "world"]
    p_id, p_resolve = _patch_selection(provider, "gemma3:12b")
    with p_id, p_resolve:
        tokens = [t async for t in generate_answer_stream(MESSAGES)]

    assert tokens == ["Hello ", "world"]
    assert provider.seen_model == "gemma3:12b"  # bare name, no 'ollama/' prefix


async def test_stream_error_surfaces():
    provider = FakeProvider(provider_id="kimi")
    provider._stream_error = Exception("stream boom")
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        with pytest.raises(RuntimeError, match="streaming"):
            async for _ in generate_answer_stream(MESSAGES):
                pass


# ---------------------------------------------------------------------------
# generate_answer_with_runtime_tools
# ---------------------------------------------------------------------------

RUNTIME_TOOLS = [
    RuntimeToolDefinition(name="list_executions", description="List executions", input_schema={"type": "object"}),
]


async def test_tool_aware_returns_tool_call():
    provider = FakeProvider(provider_id="kimi", supports_tools=True)
    provider.generate_with_tools.return_value = ToolAwareLLMResponse(
        provider="kimi",
        tool_calls=[RuntimeToolCallRequest(call_id="call_1", tool_name="list_executions", arguments={})],
        usage=UsageStats(provider="kimi", model="kimi-k2.6"),
    )
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

    assert response.tool_calls[0].tool_name == "list_executions"
    assert response.no_tool_support is False
    provider.generate_with_tools.assert_awaited_once_with(
        model="kimi-k2.6", messages=MESSAGES, runtime_tools=RUNTIME_TOOLS, images=None
    )


async def test_tool_aware_provider_without_tools_degrades():
    provider = FakeProvider(provider_id="ollama", supports_tools=False)
    p_id, p_resolve = _patch_selection(provider, "gemma3:4b")
    with p_id, p_resolve:
        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

    assert response.no_tool_support is True
    provider.generate_with_tools.assert_not_awaited()


async def test_tool_aware_provider_error_degrades():
    provider = FakeProvider(provider_id="kimi", supports_tools=True)
    provider.generate_with_tools.side_effect = Exception("kimi tool error")
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

    assert response.no_tool_support is True


# ---------------------------------------------------------------------------
# usage tracking: per-context last call + accumulated turn usage
# ---------------------------------------------------------------------------


async def test_turn_usage_accumulates_across_calls():
    """Two generate calls in one turn sum into get_turn_usage()."""
    reset_turn_usage()
    provider = FakeProvider(provider_id="kimi")
    provider.generate.side_effect = [
        ("a1", UsageStats(provider="kimi", model="kimi-k2.6", input_tokens=10, output_tokens=5, cost_usd=0.001, latency_ms=100)),
        ("a2", UsageStats(provider="kimi", model="kimi-k2.6", input_tokens=20, output_tokens=7, cost_usd=0.002, latency_ms=200)),
    ]
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        await generate_answer(MESSAGES)
        await generate_answer(MESSAGES)

    assert get_last_usage().input_tokens == 20
    total = get_turn_usage()
    assert total.input_tokens == 30
    assert total.output_tokens == 12
    assert total.cost_usd == pytest.approx(0.003)
    assert total.latency_ms == 300
    assert total.model == "kimi-k2.6"


async def test_reset_turn_usage_clears_last_and_accumulated():
    reset_turn_usage()
    provider = FakeProvider(provider_id="kimi")
    provider.generate.return_value = (
        "a", UsageStats(provider="kimi", model="kimi-k2.6", input_tokens=10, output_tokens=5),
    )
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        await generate_answer(MESSAGES)

    reset_turn_usage()
    assert get_last_usage() is None
    assert get_turn_usage() is None


async def test_stream_records_usage():
    """A streaming call's trailing UsageStats is recorded like a sync call."""
    reset_turn_usage()
    provider = FakeProvider(provider_id="ollama")
    provider._stream_tokens = ["hi"]
    p_id, p_resolve = _patch_selection(provider, "gemma3:12b")
    with p_id, p_resolve:
        async for _ in generate_answer_stream(MESSAGES):
            pass

    total = get_turn_usage()
    assert total is not None
    assert total.provider == "ollama"
    assert total.model == "gemma3:12b"


async def test_usage_is_isolated_between_concurrent_tasks():
    """Concurrent turns cannot overwrite each other's usage (ContextVar)."""
    reset_turn_usage()
    provider = FakeProvider(provider_id="kimi")
    provider.generate.return_value = (
        "a", UsageStats(provider="kimi", model="kimi-k2.6", input_tokens=5, output_tokens=2),
    )
    p_id, p_resolve = _patch_selection(provider, "kimi-k2.6")
    with p_id, p_resolve:
        async def worker():
            await generate_answer(MESSAGES)
            return get_turn_usage().input_tokens

        results = await asyncio.gather(worker(), worker())
        assert results == [5, 5]
        # The parent context is untouched by child-task calls.
        assert get_turn_usage() is None
