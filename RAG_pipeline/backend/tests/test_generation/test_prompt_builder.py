from src.generation.prompt_builder import (
    CODE_ASSISTANT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    MAX_CONTEXT_TOKENS,
    build_messages,
    build_no_results_messages,
    _build_context,
    should_use_code_assistant,
)
from src.schemas import Source


def _make_source(text: str = "Some chunk text", chunk_index: int = 0, document_id: int = 1) -> Source:
    return Source(
        document_id=document_id,
        filename="test.pdf",
        chunk_index=chunk_index,
        score=0.9,
        text=text,
    )


def test_build_messages_structure():
    sources = [_make_source()]
    messages = build_messages("What is X?", sources)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert messages[1]["role"] == "user"
    assert "What is X?" in messages[1]["content"]
    assert "test.pdf" in messages[1]["content"]


def test_build_messages_no_sources():
    messages = build_messages("What is X?", [])
    assert len(messages) == 2
    assert "What is X?" in messages[1]["content"]


def test_build_messages_with_reference_numbers():
    sources = [_make_source()]
    ref_map = {1: 1}
    messages = build_messages("What is X?", sources, reference_numbers=ref_map)
    assert "[Reference 1: test.pdf]" in messages[1]["content"]
    assert "[Source:" not in messages[1]["content"]


def test_build_messages_code_answer_style():
    sources = [_make_source()]
    messages = build_messages("How is auth implemented?", sources, answer_style="code")
    assert messages[0]["content"] == CODE_ASSISTANT_SYSTEM_PROMPT
    assert "Finding" in messages[0]["content"]
    assert "Deep Dive" in messages[0]["content"]


def test_build_no_results_code_answer_style_suggests_deep_dive():
    messages = build_no_results_messages("Where is the stream route?", answer_style="code")
    assert "Deep Dive" in messages[0]["content"]
    assert "indexed project documentation" in messages[0]["content"]


def test_should_use_code_assistant_for_engineering_query():
    assert should_use_code_assistant("Where is streaming chat implemented?")
    assert should_use_code_assistant("How does this project handle data flow?", ["proj"])
    assert not should_use_code_assistant("Hello there")


def test_build_context_includes_headers():
    sources = [
        _make_source("Text A", chunk_index=0),
        _make_source("Text B", chunk_index=3),
    ]
    context = _build_context(sources)
    assert "[Source: test.pdf, chunk 0]" in context
    assert "[Source: test.pdf, chunk 3]" in context
    assert "Text A" in context
    assert "Text B" in context


def test_build_context_with_reference_numbers():
    sources = [
        _make_source("Text A", chunk_index=0, document_id=1),
        _make_source("Text B", chunk_index=1, document_id=2),
    ]
    # Rename second source for clarity
    sources[1].filename = "other.pdf"
    ref_map = {1: 1, 2: 2}
    context = _build_context(sources, reference_numbers=ref_map)
    assert "[Reference 1: test.pdf]" in context
    assert "[Reference 2: other.pdf]" in context
    assert "[Source:" not in context


def test_build_context_truncates_per_source():
    # Create a source with text that exceeds the current context token budget.
    long_text = "word " * (MAX_CONTEXT_TOKENS + 1000)
    sources = [
        _make_source(long_text, chunk_index=0),
        _make_source("Second source text", chunk_index=1),
    ]
    context = _build_context(sources)
    # First source should be truncated (not dropped) with [...] marker
    assert "[...]" in context
    # Source header should still appear
    assert "[Source: test.pdf, chunk 0]" in context
