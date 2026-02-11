import json
from unittest.mock import AsyncMock, patch

import pytest

from src.generation.intent_classifier import (
    Action,
    QueryAnalysis,
    QueryType,
    _parse_router_response,
    analyze_query,
)


class TestParseRouterResponse:
    def test_valid_search_response(self) -> None:
        raw = '{"action": "search", "cleaned_query": "what is the onboarding process"}'
        result = _parse_router_response(raw, "what is the onbording process")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "what is the onboarding process"

    def test_valid_respond_response(self) -> None:
        raw = '{"action": "respond", "cleaned_query": "hello"}'
        result = _parse_router_response(raw, "hello")
        assert result.action == Action.RESPOND
        assert result.cleaned_query == "hello"

    def test_markdown_code_fence_stripped(self) -> None:
        raw = '```json\n{"action": "search", "cleaned_query": "test query"}\n```'
        result = _parse_router_response(raw, "test query")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "test query"

    def test_invalid_json_falls_back_to_search(self) -> None:
        raw = "This is not JSON at all"
        result = _parse_router_response(raw, "original query")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "original query"

    def test_missing_action_defaults_to_search(self) -> None:
        raw = '{"cleaned_query": "some query"}'
        result = _parse_router_response(raw, "some query")
        assert result.action == Action.SEARCH

    def test_unknown_action_defaults_to_search(self) -> None:
        raw = '{"action": "unknown_thing", "cleaned_query": "query"}'
        result = _parse_router_response(raw, "query")
        assert result.action == Action.SEARCH

    def test_empty_cleaned_query_uses_original(self) -> None:
        raw = '{"action": "search", "cleaned_query": ""}'
        result = _parse_router_response(raw, "original")
        assert result.cleaned_query == "original"

    def test_missing_cleaned_query_uses_original(self) -> None:
        raw = '{"action": "respond"}'
        result = _parse_router_response(raw, "hi there")
        assert result.cleaned_query == "hi there"

    def test_query_type_qa_default(self) -> None:
        raw = '{"action": "search", "cleaned_query": "what is onboarding"}'
        result = _parse_router_response(raw, "what is onboarding")
        assert result.query_type == QueryType.QA

    def test_query_type_summary_parsed(self) -> None:
        raw = '{"action": "search", "query_type": "summary", "cleaned_query": "summarize the HR policy"}'
        result = _parse_router_response(raw, "summarize the HR policy")
        assert result.query_type == QueryType.SUMMARY
        assert result.action == Action.SEARCH

    def test_query_type_missing_defaults_to_qa(self) -> None:
        raw = '{"action": "search", "cleaned_query": "some query"}'
        result = _parse_router_response(raw, "some query")
        assert result.query_type == QueryType.QA

    def test_query_type_unknown_defaults_to_qa(self) -> None:
        raw = '{"action": "search", "query_type": "banana", "cleaned_query": "query"}'
        result = _parse_router_response(raw, "query")
        assert result.query_type == QueryType.QA


class TestAnalyzeQuery:
    @pytest.mark.asyncio
    async def test_empty_query_returns_respond(self) -> None:
        result = await analyze_query("")
        assert result.action == Action.RESPOND

    @pytest.mark.asyncio
    async def test_whitespace_query_returns_respond(self) -> None:
        result = await analyze_query("   ")
        assert result.action == Action.RESPOND

    @pytest.mark.asyncio
    async def test_calls_ollama_and_parses_response(self) -> None:
        mock_response = {
            "message": {
                "content": '{"action": "search", "cleaned_query": "what is the onboarding process"}',
            }
        }
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await analyze_query("what is the onbording process")

        assert result.action == Action.SEARCH
        assert result.cleaned_query == "what is the onboarding process"
        mock_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_greeting_routed_to_respond(self) -> None:
        mock_response = {
            "message": {
                "content": '{"action": "respond", "cleaned_query": "hello"}',
            }
        }
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await analyze_query("hello")

        assert result.action == Action.RESPOND

    @pytest.mark.asyncio
    async def test_ollama_failure_falls_back_to_search(self) -> None:
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.side_effect = ConnectionError("Ollama unavailable")
            mock_cls.return_value = mock_client

            result = await analyze_query("what is the vacation policy")

        assert result.action == Action.SEARCH
        assert result.cleaned_query == "what is the vacation policy"

    @pytest.mark.asyncio
    async def test_garbage_llm_response_falls_back_to_search(self) -> None:
        mock_response = {
            "message": {
                "content": "I'm sorry, I can't help with that.",
            }
        }
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await analyze_query("test query")

        assert result.action == Action.SEARCH
        assert result.cleaned_query == "test query"

    @pytest.mark.asyncio
    async def test_conversation_context_included_in_prompt(self) -> None:
        """When conversation_context is provided, it should be prepended to the user message."""
        mock_response = {
            "message": {
                "content": '{"action": "search", "query_type": "summary", "cleaned_query": "summarize The Quiet Revolution"}',
            }
        }
        context = "user: is there a file about The Quiet Revolution?\nassistant: Yes, the file is The Quiet Revolution.pdf"
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await analyze_query("summarize it", conversation_context=context)

        assert result.action == Action.SEARCH
        assert result.query_type == QueryType.SUMMARY
        assert result.cleaned_query == "summarize The Quiet Revolution"
        # Verify context was included in the prompt
        call_args = mock_client.chat.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert "Recent conversation:" in user_msg
        assert "The Quiet Revolution" in user_msg

    @pytest.mark.asyncio
    async def test_no_context_sends_plain_query(self) -> None:
        """Without conversation_context, only the bare query is sent."""
        mock_response = {
            "message": {
                "content": '{"action": "search", "cleaned_query": "what is onboarding"}',
            }
        }
        with patch("src.generation.intent_classifier.ollama_lib.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.chat.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await analyze_query("what is onboarding")

        call_args = mock_client.chat.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert user_msg == "what is onboarding"
        assert "Recent conversation:" not in user_msg
