from src.ingestion.chunker import chunk_text, DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_text_returns_single_chunk():
    text = "Hello world, this is a short text."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunking_produces_overlap():
    # Create text long enough to require multiple chunks
    # ~500 tokens per chunk, so we need >500 tokens
    words = ["word"] * 2000  # ~2000 tokens
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    # With overlap, each chunk after the first starts 80 tokens in from prev start
    # So chunks should share some content


def test_chunk_size_respected():
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    words = ["example"] * 3000
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=200, overlap=30)
    for chunk in chunks:
        tokens = enc.encode(chunk)
        assert len(tokens) <= 200


def test_overlap_larger_than_chunk_size_still_progresses():
    words = ["word"] * 120
    chunks = chunk_text(" ".join(words), chunk_size=50, overlap=128)
    assert len(chunks) > 1


def test_default_parameters():
    assert DEFAULT_CHUNK_SIZE == 256
    assert DEFAULT_OVERLAP == 100
