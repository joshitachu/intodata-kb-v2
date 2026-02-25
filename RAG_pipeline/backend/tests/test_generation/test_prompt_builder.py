from src.generation.prompt_builder import (
    SYSTEM_PROMPT,
    MAX_CONTEXT_TOKENS,
    build_messages,
    _build_context,
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
    # Create a source with a very long text that exceeds MAX_CONTEXT_TOKENS (6144)
    long_text = "word " * 8000  # ~8000 tokens, well exceeds 6144 limit
    sources = [
        _make_source(long_text, chunk_index=0),
        _make_source("Second source text", chunk_index=1),
    ]
    context = _build_context(sources)
    # First source should be truncated (not dropped) with [...] marker
    assert "[...]" in context
    # Source header should still appear
    assert "[Source: test.pdf, chunk 0]" in context
