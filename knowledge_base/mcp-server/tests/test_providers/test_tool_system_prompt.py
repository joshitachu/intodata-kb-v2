"""Regression tests: the tool-calling loops must deliver opts.system_prompt as a
stable prefix (the per-doc evidence pack), instead of silently dropping it and
forcing the model to re-fetch everything via tools.

Covers the token-bloat fix: OpenAI-compatible providers seed a leading system
message; the Anthropic provider sends a cache-controlled system block + the
prompt-caching beta header. Both surface cache tokens (including at the
tool-input-cap return site).
"""

from __future__ import annotations

from mcp_server.providers.custom import providers as P
from mcp_server.providers.custom.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
)
from mcp_server.providers.custom.models import ModelOptions


# --- OpenAI-compatible (DeepSeek/Kimi/etc.) tool loop ---------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _openai_client_factory(sink, responses):
    """Build a fake httpx.AsyncClient class that records each posted payload into
    ``sink`` and returns the next canned response per turn. ``sink``/``responses``
    are per-test instances, so there is no cross-test shared state."""
    resp_iter = iter(responses)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            sink.append(json)
            return _FakeResp(next(resp_iter))

    return _FakeAsyncClient


def _stop_response(prompt_tokens=100, cache_hit=80):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": "DOC BODY"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": cache_hit,
        },
    }


def _tool_call_response():
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_artifact", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 80},
    }


def _openai_provider():
    return OpenAICompatibleProvider(
        "deepseek-v4-flash",
        api_key="k",
        base_url="https://api.deepseek.com/v1",
        provider_name="deepseek",
    )


async def test_openai_tool_loop_injects_system_prompt(monkeypatch, tmp_path):
    sink: list[dict] = []
    monkeypatch.setattr(P.httpx, "AsyncClient", _openai_client_factory(sink, [_stop_response()]))
    res = await _openai_provider().complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="STATIC EVIDENCE PACK", max_tokens=100),
        max_turns=3,
    )
    assert res.success
    # System pack must be the FIRST message (stable, cacheable prefix), then the user prompt.
    assert sink[0]["messages"][0] == {"role": "system", "content": "STATIC EVIDENCE PACK"}
    assert sink[0]["messages"][1]["role"] == "user"
    assert "write the document" in sink[0]["messages"][1]["content"]
    assert res.cache_read_tokens == 80


async def test_openai_tool_loop_no_system_message_when_absent(monkeypatch, tmp_path):
    sink: list[dict] = []
    monkeypatch.setattr(P.httpx, "AsyncClient", _openai_client_factory(sink, [_stop_response()]))
    res = await _openai_provider().complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(max_tokens=100),  # no system_prompt
        max_turns=3,
    )
    assert res.success
    assert sink[0]["messages"][0]["role"] == "user"


async def test_openai_system_prompt_stable_across_turns(monkeypatch, tmp_path):
    """The whole point of the fix: the system message stays the leading, identical
    prefix on EVERY turn (so caching holds and the model isn't re-fed context)."""
    sink: list[dict] = []
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        _openai_client_factory(sink, [_tool_call_response(), _stop_response()]),
    )
    res = await _openai_provider().complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="STATIC EVIDENCE PACK", max_tokens=100),
        max_turns=4,
    )
    assert res.success
    assert len(sink) == 2  # one tool turn, then the stop turn
    expected_system = {"role": "system", "content": "STATIC EVIDENCE PACK"}
    assert sink[0]["messages"][0] == expected_system
    # Turn 2 carries the grown conversation, but the system prefix is unchanged and first.
    assert sink[1]["messages"][0] == expected_system
    assert len(sink[1]["messages"]) > len(sink[0]["messages"])


async def test_openai_tool_cap_reports_cache_tokens(monkeypatch, tmp_path):
    """When the tool-input cap fires, cache tokens accumulated so far are still
    surfaced (no silent zeroing in the cap return path)."""
    sink: list[dict] = []
    monkeypatch.setattr(P.httpx, "AsyncClient", _openai_client_factory(sink, [_stop_response()]))
    res = await _openai_provider().complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="PACK", max_tokens=100, tool_input_token_cap=1),
        max_turns=3,
    )
    assert not res.success  # cap tripped
    assert "cap" in (res.raw_error or "").lower()
    assert res.cache_read_tokens == 80


# --- Anthropic tool loop --------------------------------------------------------


class _FakeAnthropicResponse:
    def model_dump(self):
        return {
            "content": [{"type": "text", "text": "DOC BODY"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 90,
                "cache_creation_input_tokens": 10,
            },
        }


class _FakeMessages:
    def __init__(self, sink):
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeAnthropicResponse()


class _FakeAnthropicClient:
    def __init__(self, sink):
        self.messages = _FakeMessages(sink)


async def test_anthropic_tool_loop_sends_cached_system_block(monkeypatch, tmp_path):
    sink: list[dict] = []
    prov = AnthropicProvider("claude-sonnet-4-6", api_key="k")
    monkeypatch.setattr(prov, "_client", lambda timeout_s: _FakeAnthropicClient(sink))
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="STATIC EVIDENCE PACK", max_tokens=100),
        max_turns=3,
    )
    assert res.success
    block = sink[0]["system"][0]
    assert block["text"] == "STATIC EVIDENCE PACK"
    assert block["cache_control"] == {"type": "ephemeral"}
    # Prompt caching is GA — cache_control alone is enough, no beta header.
    assert "extra_headers" not in sink[0]
    assert res.cache_read_tokens == 90
    assert res.cache_creation_tokens == 10


async def test_anthropic_tool_loop_no_system_when_absent(monkeypatch, tmp_path):
    sink: list[dict] = []
    prov = AnthropicProvider("claude-sonnet-4-6", api_key="k")
    monkeypatch.setattr(prov, "_client", lambda timeout_s: _FakeAnthropicClient(sink))
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(max_tokens=100),  # no system_prompt
        max_turns=3,
    )
    assert res.success
    assert "system" not in sink[0]
    assert "extra_headers" not in sink[0]


# --- OpenAI official Responses API tool loop ------------------------------------
#
# This provider was the one route the tests above never covered, and it carried
# both defects the others were fixed for: complete_with_tools built its payload
# without ``instructions`` (dropping opts.system_prompt entirely) and
# _response_usage discarded input_tokens_details.cached_tokens (so every call
# reported cache_read_tokens=0 whether or not the API charged a cache hit).


class _FakeResponsesResult:
    """Minimal stand-in for an openai Responses API object."""

    def __init__(self, *, output_text="DOC BODY", status="completed", output=None, cached=80):
        self.id = "resp_1"
        self.output_text = output_text
        self.status = status
        self.output = output or []
        self.usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": cached},
        }


def _function_call_result():
    return _FakeResponsesResult(
        output_text="",
        status="completed",
        output=[{"type": "function_call", "name": "read_artifact", "arguments": "{}", "call_id": "c1"}],
    )


def _official_client_factory(sink, results):
    result_iter = iter(results)

    class _FakeResponses:
        async def create(self, **kwargs):
            sink.append(kwargs)
            return next(result_iter)

    class _FakeClient:
        responses = _FakeResponses()

    return lambda timeout_s: _FakeClient()


def _official_provider():
    return P.OpenAIOfficialProvider("gpt-5.6-luna", api_key="k")


async def test_official_tool_loop_sends_instructions(monkeypatch, tmp_path):
    """The harness injects the doc pack into system_prompt precisely because
    complete_with_tools takes no context_files. Dropping it sent the model
    nothing."""
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(prov, "_client", _official_client_factory(sink, [_FakeResponsesResult()]))
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="STATIC EVIDENCE PACK", max_tokens=100),
        max_turns=3,
    )
    assert res.success
    assert sink[0]["instructions"] == "STATIC EVIDENCE PACK"


async def test_official_tool_loop_no_instructions_when_absent(monkeypatch, tmp_path):
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(prov, "_client", _official_client_factory(sink, [_FakeResponsesResult()]))
    res = await prov.complete_with_tools(
        "write the document", tmp_path, ModelOptions(max_tokens=100), max_turns=3
    )
    assert res.success
    assert "instructions" not in sink[0]


async def test_official_instructions_stable_across_turns(monkeypatch, tmp_path):
    """Instructions are NOT inherited through previous_response_id, so they must be
    resent every turn — identical bytes each time, so the prefix stays cacheable."""
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(
        prov,
        "_client",
        _official_client_factory(sink, [_function_call_result(), _FakeResponsesResult()]),
    )
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="STATIC EVIDENCE PACK", max_tokens=100),
        max_turns=4,
    )
    assert res.success
    assert len(sink) == 2
    assert sink[0]["instructions"] == "STATIC EVIDENCE PACK"
    assert sink[1]["instructions"] == "STATIC EVIDENCE PACK"
    assert sink[1]["previous_response_id"] == "resp_1"


async def test_official_tool_loop_reports_cache_tokens(monkeypatch, tmp_path):
    """Responses API nests cache hits under input_tokens_details, which
    _cache_hit_tokens previously only looked for under prompt_tokens_details."""
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(prov, "_client", _official_client_factory(sink, [_FakeResponsesResult()]))
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="PACK", max_tokens=100),
        max_turns=3,
    )
    assert res.success
    assert res.cache_read_tokens == 80


async def test_official_tool_loop_accumulates_cache_across_turns(monkeypatch, tmp_path):
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(
        prov,
        "_client",
        _official_client_factory(sink, [_function_call_result(), _FakeResponsesResult()]),
    )
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="PACK", max_tokens=100),
        max_turns=4,
    )
    assert res.success
    assert res.cache_read_tokens == 160  # 80 per turn, two turns


async def test_official_tool_cap_reports_cache_tokens(monkeypatch, tmp_path):
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(prov, "_client", _official_client_factory(sink, [_FakeResponsesResult()]))
    res = await prov.complete_with_tools(
        "write the document",
        tmp_path,
        ModelOptions(system_prompt="PACK", max_tokens=100, tool_input_token_cap=1),
        max_turns=3,
    )
    assert not res.success
    assert "cap" in (res.raw_error or "").lower()
    assert res.cache_read_tokens == 80


async def test_official_complete_reports_cache_tokens(monkeypatch, tmp_path):
    """The non-tool path (phase2.claims, phase3 first-pass docs) was equally blind."""
    sink: list[dict] = []
    prov = _official_provider()
    monkeypatch.setattr(prov, "_client", _official_client_factory(sink, [_FakeResponsesResult()]))
    res = await prov.complete(
        "write the document", None, ModelOptions(system_prompt="PACK", max_tokens=100)
    )
    assert res.success
    assert res.cache_read_tokens == 80
    assert sink[0]["instructions"] == "PACK"
