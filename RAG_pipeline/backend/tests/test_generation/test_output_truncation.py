"""A model that runs out of output budget must not look like one that finished.

Every provider reports the difference -- OpenAI-compatible ``finish_reason ==
"length"``, Anthropic ``stop_reason == "max_tokens"``, Gemini ``MAX_TOKENS`` --
and all three adapters used to discard it. A truncated response ends its stream
cleanly and raises nothing, so the fragment was persisted and rendered as a
complete answer. Six answers in the production history stopped exactly on an
output ceiling with no indication to the reader.

These tests pin both halves of the fix: the signal survives every adapter, and
it reaches the answer text the reader actually sees.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.generation.providers.anthropic_provider import AnthropicProvider
from src.generation.providers.google_provider import GoogleProvider
from src.generation.providers.openai_compat import OpenAICompatibleProvider
from src.generation.usage import (
    TRUNCATION_NOTICE,
    UsageStats,
    append_truncation_notice,
    mark_if_truncated,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def _openai_provider():
    return OpenAICompatibleProvider(
        provider_id="openai", base_url="https://api.openai.com/v1", api_key="sk-x"
    )


def _usage(prompt=10, completion=5):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


# --- OpenAI-compatible wire (kimi / openai / deepseek) ---------------------


@pytest.mark.parametrize(
    "finish_reason,expected",
    [("length", True), ("stop", False), ("tool_calls", False), (None, False)],
)
async def test_generate_flags_only_the_output_ceiling(finish_reason, expected):
    provider = _openai_provider()
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="answer", tool_calls=None),
                    finish_reason=finish_reason,
                )
            ],
            usage=_usage(),
        )
    )
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        _, usage = await provider.generate(model="gpt-4o", messages=MESSAGES)

    assert usage.truncated is expected


async def test_stream_carries_truncation_through_to_final_usage():
    provider = _openai_provider()

    async def _fake_stream(*a, **k):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"), finish_reason=None)],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"), finish_reason="length")],
            usage=None,
        )
        yield SimpleNamespace(choices=[], usage=_usage())

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=lambda *a, **k: _fake_stream())
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        out = [item async for item in provider.stream(model="gpt-4o", messages=MESSAGES)]

    assert [x for x in out if isinstance(x, str)] == ["Hel", "lo"]
    final = [x for x in out if isinstance(x, UsageStats)][-1]
    assert final.truncated is True


async def test_stream_that_finished_normally_is_not_flagged():
    provider = _openai_provider()

    async def _fake_stream(*a, **k):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="done"), finish_reason="stop")],
            usage=None,
        )
        yield SimpleNamespace(choices=[], usage=_usage())

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=lambda *a, **k: _fake_stream())
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        out = [item async for item in provider.stream(model="gpt-4o", messages=MESSAGES)]

    assert [x for x in out if isinstance(x, UsageStats)][-1].truncated is False


# --- Anthropic and Gemini use different words for the same condition -------


@pytest.mark.parametrize(
    "stop_reason,expected", [("max_tokens", True), ("end_turn", False), (None, False)]
)
async def test_anthropic_maps_max_tokens_stop_reason(stop_reason, expected):
    provider = AnthropicProvider(api_key="sk-ant")
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="answer")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason=stop_reason,
        )
    )
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        _, usage = await provider.generate(model="claude-sonnet-4-6", messages=MESSAGES)

    assert usage.truncated is expected


@pytest.mark.parametrize(
    "finish_reason,expected", [("MAX_TOKENS", True), ("STOP", False), (None, False)]
)
async def test_google_maps_max_tokens_by_enum_name(finish_reason, expected):
    """Gemini's finish_reason is an enum, so it is compared by ``.name``."""
    provider = GoogleProvider(api_key="k")
    reason = SimpleNamespace(name=finish_reason) if finish_reason else None
    resp = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=reason, content=SimpleNamespace(parts=[]))],
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        text="answer",
    )
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=resp)
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        _, usage = await provider.generate(model="gemini-2.5-pro", messages=MESSAGES)

    assert usage.truncated is expected


# --- the notice the reader actually sees ----------------------------------


def test_notice_is_appended_only_when_truncated():
    assert mark_if_truncated("body", UsageStats(truncated=False)) == "body"
    assert mark_if_truncated("body", None) == "body"
    assert TRUNCATION_NOTICE.strip() in mark_if_truncated("body", UsageStats(truncated=True))


def test_notice_is_idempotent_across_layers():
    """Both the streaming helper and the caller may mark; twice must be once."""
    once = append_truncation_notice("body")
    assert append_truncation_notice(once) == once
    assert once.count("cut off before it finished") == 1


def test_notice_preserves_the_partial_answer():
    """The fragment is still shown -- it is labelled, not discarded."""
    marked = append_truncation_notice("| job | port |\n| a | 25 |")
    assert marked.startswith("| job | port |")


# --- one ceiling, applied everywhere --------------------------------------


def test_every_provider_shares_one_configured_ceiling():
    """Four hardcoded ceilings (4096/2048/2048/none) is what let the agent path
    -- the one producing the longest answers -- carry the lowest limit."""
    from src.generation.providers import anthropic_provider, google_provider

    expected = settings.llm_max_output_tokens
    assert anthropic_provider._MAX_TOKENS == expected
    assert google_provider._MAX_TOKENS == expected
    assert _openai_provider()._max_tokens == expected


def test_explicit_ceiling_still_overrides_the_default():
    assert OpenAICompatibleProvider(
        provider_id="openai", base_url="u", api_key="k", max_tokens=123
    )._max_tokens == 123


# --- the two paths that render an answer ----------------------------------


def test_agent_path_reads_langchain_response_metadata():
    """The agent does not go through these adapters; LangChain carries the same
    signal on ``response_metadata``, and that is where #545 was lost."""
    from langchain_core.messages import AIMessage

    from src.harness.readonly_runtime import _hit_output_ceiling

    truncated = AIMessage(content="half a table", response_metadata={"finish_reason": "length"})
    finished = AIMessage(content="whole answer", response_metadata={"finish_reason": "stop"})

    assert _hit_output_ceiling([truncated]) is True
    assert _hit_output_ceiling([finished]) is False
    assert _hit_output_ceiling([]) is False
    # No metadata at all must not be read as truncated.
    assert _hit_output_ceiling([AIMessage(content="x")]) is False


def test_agent_path_ignores_tool_call_messages():
    """Only the terminal answer message matters; a tool-calling turn legitimately
    stops for another reason."""
    from langchain_core.messages import AIMessage

    from src.harness.readonly_runtime import _hit_output_ceiling

    tool_turn = AIMessage(
        content="",
        response_metadata={"finish_reason": "length"},
        tool_calls=[{"name": "search", "args": {}, "id": "1"}],
    )
    answer = AIMessage(content="done", response_metadata={"finish_reason": "stop"})
    assert _hit_output_ceiling([tool_turn, answer]) is False


async def test_streaming_rag_publishes_the_notice_live():
    """A reader watching the answer arrive must see the notice too, not only
    someone who reloads the conversation afterwards."""
    from src.generation import rag_chain

    async def _fake_stream(_messages):
        yield "partial answer"

    published: list[str] = []

    async def _on_delta(text: str) -> None:
        published.append(text)

    with (
        patch.object(rag_chain, "generate_answer_stream", _fake_stream),
        patch.object(rag_chain, "get_last_usage", lambda: UsageStats(truncated=True)),
    ):
        answer = await rag_chain._generate_streaming_answer([], _on_delta)

    assert answer.startswith("partial answer")
    assert TRUNCATION_NOTICE.strip() in answer
    assert any(TRUNCATION_NOTICE.strip() in chunk for chunk in published), published


async def test_streaming_rag_leaves_a_complete_answer_alone():
    from src.generation import rag_chain

    async def _fake_stream(_messages):
        yield "complete answer"

    published: list[str] = []

    async def _on_delta(text: str) -> None:
        published.append(text)

    with (
        patch.object(rag_chain, "generate_answer_stream", _fake_stream),
        patch.object(rag_chain, "get_last_usage", lambda: UsageStats(truncated=False)),
    ):
        answer = await rag_chain._generate_streaming_answer([], _on_delta)

    assert answer == "complete answer"
    assert "".join(published) == "complete answer"
