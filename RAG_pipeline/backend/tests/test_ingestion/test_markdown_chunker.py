"""Tests for structure-aware markdown chunker."""
from __future__ import annotations

import pytest

from src.ingestion.markdown_chunker import MarkdownChunk, chunk_markdown


class TestChunkMarkdown:
    def test_empty_returns_empty(self) -> None:
        assert chunk_markdown("", doc_name="doc") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert chunk_markdown("   \n\n  ", doc_name="doc") == []

    def test_heading_path_prefix_format(self) -> None:
        md = "## Section A\n\nContent under A."
        chunks = chunk_markdown(md, doc_name="MyDoc")
        assert len(chunks) >= 1
        assert chunks[0].text.startswith("[MyDoc > Section A]")

    def test_section_metadata_set_correctly(self) -> None:
        md = "## Section A\n\nContent."
        chunks = chunk_markdown(md, doc_name="MyDoc")
        assert chunks[0].section == "MyDoc > Section A"
        assert chunks[0].unit == "Section A"

    def test_nested_h2_h3_path(self) -> None:
        md = "## Analysis\n\n### Components\n\nComponent details here."
        chunks = chunk_markdown(md, doc_name="MyDoc")
        assert chunks[0].section == "MyDoc > Analysis > Components"
        assert chunks[0].unit == "Components"
        assert "[MyDoc > Analysis > Components]" in chunks[0].text

    def test_nested_h1_h2_h3_h4(self) -> None:
        md = (
            "# Top\n\n"
            "## H2\n\n"
            "### H3\n\n"
            "#### H4\n\n"
            "Leaf content."
        )
        chunks = chunk_markdown(md, doc_name="doc")
        leaf = [c for c in chunks if c.unit == "H4"]
        assert leaf, "Expected a chunk for the H4 section"
        assert leaf[0].section == "doc > Top > H2 > H3 > H4"
        assert "[doc > Top > H2 > H3 > H4]" in leaf[0].text

    def test_headingless_text_delegated_with_doc_prefix(self) -> None:
        md = "Just some plain text with no headings.\n\nSecond paragraph."
        chunks = chunk_markdown(md, doc_name="MyDoc")
        assert len(chunks) >= 1
        assert chunks[0].text.startswith("[MyDoc]")
        assert chunks[0].section == "MyDoc"
        assert chunks[0].unit == "MyDoc"

    def test_ascii_passthrough(self) -> None:
        md = "## ASCII\n\nOnly ASCII chars: a-z 0-9 !@#$%"
        chunks = chunk_markdown(md, doc_name="doc")
        assert chunks[0].text.isascii()

    def test_deterministic_chunking(self) -> None:
        md = "## Section\n\nRepeatable content."
        c1 = chunk_markdown(md, doc_name="doc")
        c2 = chunk_markdown(md, doc_name="doc")
        assert [c.text for c in c1] == [c.text for c in c2]

    def test_every_chunk_has_bracket_prefix(self) -> None:
        md = (
            "## Section A\n\nContent A.\n\n"
            "## Section B\n\nContent B."
        )
        chunks = chunk_markdown(md, doc_name="doc")
        for chunk in chunks:
            assert chunk.text.startswith("["), f"Chunk missing bracket prefix: {chunk.text[:40]!r}"

    def test_code_fence_heading_not_parsed(self) -> None:
        md = "## Real Heading\n\nContent.\n\n```python\n# not a heading\ncode()\n```\n\nMore content."
        chunks = chunk_markdown(md, doc_name="doc")
        for chunk in chunks:
            assert "# not a heading" not in chunk.section, (
                f"Code fence heading leaked into section: {chunk.section!r}"
            )

    def test_phase2_section_shape(self) -> None:
        """Mirrors Phase 2 group analysis: ### SECTION + #### unit — content at h4 level.

        The h3 RISK_FLAGS heading has no direct body content, so no chunk is emitted
        for that path alone. Chunks carry the full path including RISK_FLAGS as parent.
        """
        md = (
            "### RISK_FLAGS\n\n"
            "#### HIGH\n\n"
            "High severity risk item.\n\n"
            "#### MEDIUM\n\n"
            "Medium severity risk item."
        )
        chunks = chunk_markdown(md, doc_name="Alis")
        assert chunks, "Expected at least one chunk"
        # Every chunk's section path must include RISK_FLAGS as the h3 parent
        for c in chunks:
            assert "RISK_FLAGS" in c.section, (
                f"Expected RISK_FLAGS in section path, got {c.section!r}"
            )
        # Content from both h4 sub-sections must appear
        combined = "\n".join(c.text for c in chunks)
        assert "High severity" in combined
        assert "Medium severity" in combined


class TestSiblingMerge:
    def test_small_siblings_merged(self) -> None:
        """Two short sibling sections under the same parent should merge into one chunk."""
        md = (
            "## Parent\n\n"
            "### Sub A\n\nShort A.\n\n"
            "### Sub B\n\nShort B."
        )
        chunks = chunk_markdown(md, doc_name="doc")
        # Both sub-sections should be merged since they're tiny
        sub_chunks = [c for c in chunks if c.section.endswith("Sub A") or c.section.endswith("Sub B")]
        # Merged chunks are attributed to the first sibling's section
        total_sub_text = "\n".join(c.text for c in sub_chunks)
        assert "Short A" in total_sub_text
        assert "Short B" in total_sub_text

    def test_oversized_section_splits(self) -> None:
        """A section with >512 tokens of content should produce multiple chunks."""
        # 600+ tokens of content
        long_content = ("word " * 120).strip()
        md = f"## BigSection\n\n{long_content}"
        chunks = chunk_markdown(md, doc_name="doc", max_tokens=50)
        assert len(chunks) > 1, "Expected oversized section to produce multiple chunks"
        for c in chunks:
            assert c.section == "doc > BigSection"
            assert c.text.startswith("[doc > BigSection]")

    def test_pre_heading_content(self) -> None:
        """Content before the first heading is captured under doc_name."""
        md = "Intro text before any heading.\n\n## Section\n\nSection content."
        chunks = chunk_markdown(md, doc_name="doc")
        intro = [c for c in chunks if c.section == "doc"]
        assert intro, "Expected pre-heading content under doc_name section"
        assert "Intro text" in intro[0].text
