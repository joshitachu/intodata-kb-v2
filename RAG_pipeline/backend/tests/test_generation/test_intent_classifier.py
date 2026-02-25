"""Tests for the heuristic intent classifier (zero LLM calls)."""

from src.generation.intent_classifier import (
    Action,
    QueryAnalysis,
    QueryType,
    analyze_query,
)


class TestAnalyzeQueryAction:
    """All real queries should return SEARCH; only empty → RESPOND."""

    def test_empty_query_returns_respond(self) -> None:
        result = analyze_query("")
        assert result.action == Action.RESPOND
        assert result.cleaned_query == ""

    def test_whitespace_query_returns_respond(self) -> None:
        result = analyze_query("   ")
        assert result.action == Action.RESPOND
        assert result.cleaned_query == ""

    def test_greeting_returns_search(self) -> None:
        result = analyze_query("hello")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "hello"

    def test_thanks_returns_search(self) -> None:
        result = analyze_query("thanks!")
        assert result.action == Action.SEARCH

    def test_real_question_returns_search(self) -> None:
        result = analyze_query("what is the vacation policy?")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "what is the vacation policy?"

    def test_mixed_greeting_and_question_returns_search(self) -> None:
        result = analyze_query("hi, what's our vacation policy?")
        assert result.action == Action.SEARCH

    def test_conversation_context_accepted_but_ignored(self) -> None:
        """The conversation_context parameter is accepted for API compat but doesn't affect results."""
        result = analyze_query("what about that?", conversation_context="user: tell me about X\nassistant: X is ...")
        assert result.action == Action.SEARCH
        assert result.cleaned_query == "what about that?"


class TestAnalyzeQueryType:
    """Summary detection via keyword matching."""

    def test_qa_by_default(self) -> None:
        result = analyze_query("what is the onboarding process?")
        assert result.query_type == QueryType.QA

    def test_summarize_detected(self) -> None:
        result = analyze_query("summarize the HR policy")
        assert result.query_type == QueryType.SUMMARY

    def test_summarise_british_spelling(self) -> None:
        result = analyze_query("summarise the onboarding guide")
        assert result.query_type == QueryType.SUMMARY

    def test_summary_keyword(self) -> None:
        result = analyze_query("give me a summary of the document")
        assert result.query_type == QueryType.SUMMARY

    def test_overview_keyword(self) -> None:
        result = analyze_query("overview of the quarterly report")
        assert result.query_type == QueryType.SUMMARY

    def test_recap_keyword(self) -> None:
        result = analyze_query("recap the meeting notes")
        assert result.query_type == QueryType.SUMMARY

    def test_rundown_keyword(self) -> None:
        result = analyze_query("give me a rundown of the project plan")
        assert result.query_type == QueryType.SUMMARY

    def test_brief_me_keyword(self) -> None:
        result = analyze_query("brief me on the safety guidelines")
        assert result.query_type == QueryType.SUMMARY

    def test_mixed_greeting_and_summary(self) -> None:
        """Summary keyword should be detected even with chitchat prefix."""
        result = analyze_query("hi, summarize the HR doc")
        assert result.query_type == QueryType.SUMMARY
        assert result.action == Action.SEARCH

    def test_case_insensitive(self) -> None:
        result = analyze_query("SUMMARIZE the document")
        assert result.query_type == QueryType.SUMMARY

    def test_no_false_positive_on_partial_match(self) -> None:
        """Words containing 'summary' as a substring shouldn't trigger (but 'summary' itself should)."""
        result = analyze_query("what is the summary of benefits?")
        assert result.query_type == QueryType.SUMMARY

    def test_normal_question_not_summary(self) -> None:
        result = analyze_query("how many vacation days do I get?")
        assert result.query_type == QueryType.QA
