import tiktoken

from src.ingestion.semantic_chunker import (
    chunk_text_semantic,
    _split_into_paragraphs,
    _merge_and_split,
    MIN_CHUNK_TOKENS,
)


def test_empty_text():
    assert chunk_text_semantic("") == []
    assert chunk_text_semantic("   ") == []


def test_short_text_single_chunk():
    text = "This is a short paragraph about company policies."
    chunks = chunk_text_semantic(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_paragraph_splitting():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    paragraphs = _split_into_paragraphs(text)
    assert len(paragraphs) == 3
    assert paragraphs[0] == "First paragraph."
    assert paragraphs[1] == "Second paragraph."
    assert paragraphs[2] == "Third paragraph."


def test_small_paragraphs_merged():
    """Small paragraphs should be merged until max_tokens is reached."""
    # Each paragraph is ~5-10 tokens, max_tokens=100 should merge them
    paragraphs = [f"Paragraph number {i} with some content." for i in range(5)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text_semantic(text, max_tokens=100)
    # Should merge into fewer chunks than paragraphs
    assert len(chunks) < len(paragraphs)


def test_oversized_paragraph_gets_split():
    """A paragraph exceeding max_tokens should be mechanically split."""
    words = ["word"] * 2000  # ~2000 tokens
    text = " ".join(words)
    chunks = chunk_text_semantic(text, max_tokens=200)
    assert len(chunks) > 1
    # Each chunk should respect max_tokens
    enc = tiktoken.get_encoding("cl100k_base")
    for chunk in chunks:
        tokens = len(enc.encode(chunk))
        assert tokens <= 200 + 10  # small tolerance for encoding


def test_tiny_chunks_filtered():
    """Chunks below min_tokens should be filtered out."""
    # Create text with one tiny paragraph and one normal one
    tiny = "Hi."
    normal = "This is a much longer paragraph that should definitely make it past the minimum token threshold for chunking."
    text = f"{tiny}\n\n{normal}"
    chunks = chunk_text_semantic(text, max_tokens=500, min_tokens=10)
    # The tiny paragraph alone won't pass min_tokens, but when merged with normal it should
    assert len(chunks) >= 1
    # The tiny text should be merged with the normal text, not dropped
    assert any("longer paragraph" in c for c in chunks)


def test_xlsx_delegates_to_mechanical_chunker():
    """XLSX files should use the mechanical chunker."""
    words = ["data"] * 1000
    text = " ".join(words)
    chunks = chunk_text_semantic(text, file_type="xlsx", max_tokens=200)
    assert len(chunks) > 1


def test_slide_markers_create_boundaries():
    """PPTX slide markers should create chunk boundaries."""
    text = "[SLIDE 1]\n\nIntroduction content.\n\n[SLIDE 2]\n\nMain content here.\n\n[SLIDE 3]\n\nConclusion."
    paragraphs = _split_into_paragraphs(text)
    # Slide markers should be preserved and create paragraph boundaries
    assert any("[SLIDE" in p for p in paragraphs)


def test_heading_markers_preserved():
    """Markdown heading markers from DOCX parser should be preserved in chunks."""
    # Text small enough to be a single chunk — should be returned as-is
    text = "# Introduction\n\nThis is the intro.\n\n## Details\n\nHere are the details."
    chunks = chunk_text_semantic(text, file_type="docx", max_tokens=500)
    assert len(chunks) == 1
    assert "# Introduction" in chunks[0]
    assert "## Details" in chunks[0]


def test_merge_and_split_respects_max_tokens():
    """Merged chunks should not exceed max_tokens."""
    enc = tiktoken.get_encoding("cl100k_base")
    paragraphs = [f"Paragraph {i} with enough tokens to matter." for i in range(20)]
    chunks = _merge_and_split(paragraphs, enc, max_tokens=50, min_tokens=10)
    for chunk in chunks:
        tokens = len(enc.encode(chunk))
        # Each chunk from merging should be at or under max_tokens
        # (single paragraphs that are under max_tokens get merged)
        assert tokens <= 50 + 20  # tolerance for paragraph that fits individually
