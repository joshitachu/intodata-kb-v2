"""Structure-aware markdown chunker for retrieval quality.

Parses the heading tree (h1-h4), emits chunks with the heading-path prefix
[doc_name > H2 > H3 > H4] prepended to the text so the embedder and reranker
receive section identity alongside the body.

Small sibling sections (same parent heading) are merged up to max_tokens.
Oversized sections fall back to paragraph-based semantic splitting.
Text with no headings delegates to chunk_text_semantic unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple

import tiktoken
from pydantic import BaseModel

from src.ingestion.semantic_chunker import (
    DEFAULT_MAX_TOKENS,
    ENCODING_NAME,
    chunk_text_semantic,
)

logger = logging.getLogger(__name__)

# Matches h1-h4 headings only; h5+ are treated as plain content.
# re.MULTILINE required so ^ matches after \n and $ matches before \n in multi-line text
# (used in search() for the fast path check) and correctly in match() on per-line splits.
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


class MarkdownChunk(BaseModel):
    text: str
    section: str  # heading path, e.g. "doc_name > H2 > H3"
    unit: str     # deepest heading title (or doc_name when no heading applies)


class _Section(NamedTuple):
    level: int        # 1–4; 0 = pre-heading content
    heading_path: str  # full path, e.g. "doc_name > H2 > H3"
    unit: str         # deepest heading title
    content: str      # body text (heading line excluded)


def chunk_markdown(
    text: str,
    doc_name: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[MarkdownChunk]:
    """Chunk markdown with heading-path context prefix.

    Each chunk's text begins with `[heading_path]\\n` followed by the body so
    the embedding space includes section identity. When no headings exist, the
    doc_name is used as the single path element.

    Args:
        text: Raw markdown content.
        doc_name: Document name used as the root of every heading path.
        max_tokens: Maximum tokens per chunk (same default as semantic_chunker).

    Returns:
        List of MarkdownChunk objects. Empty input returns empty list.
    """
    if not text.strip():
        return []

    # Fast path: no headings → delegate entirely to semantic_chunker.
    if not HEADING_RE.search(text):
        plain_chunks = chunk_text_semantic(text, file_type="md", max_tokens=max_tokens)
        return [
            MarkdownChunk(
                text=f"[{doc_name}]\n{c}",
                section=doc_name,
                unit=doc_name,
            )
            for c in plain_chunks
        ]

    sections = _parse_sections(text, doc_name)
    if not sections:
        return []

    return _emit_chunks(sections, max_tokens)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_sections(text: str, doc_name: str) -> list[_Section]:
    """Walk the heading tree and return a flat list of sections.

    Each section holds the body text that appears between the section's own
    heading line and the next heading of any level. Code fences are tracked so
    lines starting with `#` inside a fence are not treated as headings.
    """
    heading_stack: list[str] = [""] * 4  # index i → level i+1
    sections: list[_Section] = []
    in_code_fence = False

    current_level = 0
    current_content_lines: list[str] = []
    current_heading_path = doc_name
    current_unit = doc_name

    def _flush() -> None:
        content = "\n".join(current_content_lines).strip()
        if content:
            sections.append(
                _Section(
                    level=current_level,
                    heading_path=current_heading_path,
                    unit=current_unit,
                    content=content,
                )
            )
        current_content_lines.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            current_content_lines.append(line)
            continue

        if in_code_fence:
            current_content_lines.append(line)
            continue

        m = HEADING_RE.match(line)
        if m:
            _flush()

            level = len(m.group(1))
            title = m.group(2).strip()

            heading_stack[level - 1] = title
            for i in range(level, 4):
                heading_stack[i] = ""

            path_parts = [doc_name] + [
                heading_stack[i] for i in range(level) if heading_stack[i]
            ]
            current_heading_path = " > ".join(path_parts)
            current_unit = title
            current_level = level
        else:
            current_content_lines.append(line)

    _flush()
    return sections


def _emit_chunks(sections: list[_Section], max_tokens: int) -> list[MarkdownChunk]:
    """Merge sibling sections, split oversized ones, emit MarkdownChunks."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    chunks: list[MarkdownChunk] = []

    i = 0
    while i < len(sections):
        sec = sections[i]
        parent = _parent_path(sec)

        merged_parts = [sec.content]
        merged_tokens = len(enc.encode(sec.content))
        next_i = i + 1

        # Greedily merge subsequent siblings (same level + same parent heading).
        # Only applies to headed sections; pre-heading level-0 content is never merged.
        if sec.level > 0:
            j = i + 1
            while j < len(sections):
                candidate = sections[j]
                if (
                    candidate.level == sec.level
                    and _parent_path(candidate) == parent
                ):
                    candidate_tokens = len(enc.encode(candidate.content))
                    if merged_tokens + candidate_tokens <= max_tokens:
                        merged_parts.append(candidate.content)
                        merged_tokens += candidate_tokens
                        j += 1
                        next_i = j
                    else:
                        break
                else:
                    break

        merged_content = "\n\n".join(merged_parts)
        final_tokens = len(enc.encode(merged_content))
        prefix = f"[{sec.heading_path}]"

        if final_tokens <= max_tokens:
            chunks.append(
                MarkdownChunk(
                    text=f"{prefix}\n{merged_content}",
                    section=sec.heading_path,
                    unit=sec.unit,
                )
            )
        else:
            # Oversized: fall back to semantic chunker, re-prefix each sub-chunk.
            sub_chunks = chunk_text_semantic(
                merged_content, file_type="md", max_tokens=max_tokens
            )
            for sub in sub_chunks:
                chunks.append(
                    MarkdownChunk(
                        text=f"{prefix}\n{sub}",
                        section=sec.heading_path,
                        unit=sec.unit,
                    )
                )

        i = next_i

    return chunks


def _parent_path(sec: _Section) -> str:
    if " > " not in sec.heading_path:
        return sec.heading_path
    return sec.heading_path.rsplit(" > ", 1)[0]
