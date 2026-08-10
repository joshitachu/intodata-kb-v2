"""Deterministic merge of multi-slice Phase 3 document outputs.

Verified correctness bug (werkorder VQ partitions): when a document's context
exceeds the hard ceiling, `_complete_core_generation` pages the evidence into
N slices and each slice call RESTARTS the whole document (title, table of
contents, every required section) grounded in its own evidence page. The old
assembler concatenated the N restarts with `---`, so the published document
contained the same structure repeated N times (01_ARCHITECTURE_MAP.md: 2547
lines = ~230 unique lines x 11 slices).

Round 3 upgrade (verified on the werkorder Assetcheck deepseek run: overview
paragraphs and inventory tables restated 2-7x with small wording/row diffs,
plus direct self-contradictions -- doc 07 asserted CRITICAL findings AND "No
CRITICAL severity risks were identified"): the merge is now SECTION-level:

- one title (first slice's H1) and one Table of Contents (first occurrence);
- each top-level (H2) section appears exactly once, in first-appearance order;
- within a merged H2, H3 subsections are merged the same way (one heading,
  union of unique content across slices);
- markdown tables with the SAME header row are unioned into one canonical
  table (unique data rows, first-appearance order) instead of restating the
  table per slice;
- exact duplicate blocks (whitespace-normalized) are dropped, and re-worded
  restatements of the same prose (token-set Jaccard >= 0.75) are dropped, so
  boilerplate the model re-emits per slice does not multiply;
- a contradiction guard drops universal ABSENCE claims ("No CRITICAL severity
  risks were identified", "no hardcoded credentials", "no tStatCatcher in any
  job") when another block of the same document positively asserts that very
  term -- the evidence-backed positive claim wins, the negation is dropped;
- `duplicate_heading_errors` is the post-assembly guard: a document whose H1
  or any H2 heading appears more than once must never ship again;
  `contradiction_errors` is the detect-only companion for reporting.

No LLM in the path: Python may select and stitch verbatim model output, never
write new prose (repo rule).
"""

from __future__ import annotations

import re

_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_TOC_TITLES = {"table of contents", "contents"}

# Near-duplicate prose threshold: two paragraphs sharing >= this token-set
# Jaccard similarity are restatements of each other, not new content.
_PROSE_JACCARD_THRESHOLD = 0.75
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")

# ── universal-absence contradiction guard ────────────────────────────────────
#
# Verified shapes (werkorder Assetcheck run): "No CRITICAL severity risks were
# identified", "### CRITICAL: No Critical Severity Flags Identified", "no
# hardcoded credentials", "no tStatCatcher in any job". The captured term is
# normalized to its significant tokens; a block that positively contains ALL
# of them (outside a negation of its own) contradicts the absence claim.
_ABSENCE_CLAIM_RE = re.compile(
    r"(?i)\bno\s+(?:known\s+|explicit\s+|other\s+)?"
    r"(?P<term>[A-Za-z][\w /()-]{2,60}?)"
    r"\s*(?:\bwas\b|\bwere\b|\bis\b|\bare\b|\bhave\s+been\b|\bhas\s+been\b)?"
    r"\s*(?:identified|detected|found|present|observed|documented|configured|"
    r"defined|used|exists?|in\s+any\b)",
)
# Words inside a captured term that carry no identity on their own.
_TERM_STOPWORDS = frozenset({
    "severity", "risks", "risk", "flags", "flag", "issues", "issue",
    "items", "item", "findings", "finding", "was", "were", "is", "are",
    "the", "a", "an", "of", "any", "such", "known", "explicit", "other",
})
# A token occurrence within this many characters after a negator does not
# count as a positive assertion.
_NEGATION_WINDOW_RE_TEMPLATE = r"(?i)\b(?:no|none|not|without|zero)\b[^.\n]{{0,60}}\b{token}\b"


def _normalize_heading(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().strip("#").strip()).lower()


def _normalize_block(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _block_tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.casefold()))


def _is_table_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return lines[0].lstrip().startswith("|") and bool(
        _TABLE_SEPARATOR_RE.match(lines[1])
    )


def _table_parts(block: str) -> tuple[str, str, list[str]]:
    """Split a table block into (header_line, separator_line, data_rows)."""
    lines = [line for line in block.splitlines() if line.strip()]
    return lines[0], lines[1], lines[2:]


class _BlockAccumulator:
    """Order-preserving union of content blocks for ONE (sub)section.

    Three dedup lanes: exact (whitespace-normalized), canonical-table row
    union (same normalized header row), and near-duplicate prose (token-set
    Jaccard). Fenced code blocks only get the exact lane -- code that differs
    is evidence, never a restatement.
    """

    def __init__(self) -> None:
        self._order: list[dict] = []
        self._seen_exact: set[str] = set()
        # normalized header -> entry dict with "rows" + "row_keys"
        self._tables: dict[str, dict] = {}
        self._prose_tokens: list[frozenset[str]] = []

    def add(self, block: str) -> None:
        block = block.strip("\n")
        if not block.strip():
            return
        normalized = _normalize_block(block)
        if normalized in self._seen_exact:
            return
        self._seen_exact.add(normalized)

        if _is_table_block(block):
            header, separator, rows = _table_parts(block)
            key = _normalize_block(header).lower()
            entry = self._tables.get(key)
            if entry is None:
                entry = {
                    "kind": "table",
                    "header": header,
                    "separator": separator,
                    "rows": [],
                    "row_keys": set(),
                }
                self._tables[key] = entry
                self._order.append(entry)
            for row in rows:
                row_key = _normalize_block(row).lower()
                if row_key and row_key not in entry["row_keys"]:
                    entry["row_keys"].add(row_key)
                    entry["rows"].append(row)
            return

        is_code = block.lstrip().startswith("```")
        if not is_code:
            tokens = _block_tokens(block)
            if tokens and any(
                _jaccard(tokens, existing) >= _PROSE_JACCARD_THRESHOLD
                for existing in self._prose_tokens
            ):
                return  # re-worded restatement of prose already kept
            if tokens:
                self._prose_tokens.append(tokens)
        self._order.append({"kind": "text", "text": block})

    def render_blocks(self) -> list[str]:
        blocks: list[str] = []
        for entry in self._order:
            if entry["kind"] == "table":
                blocks.append(
                    "\n".join([entry["header"], entry["separator"], *entry["rows"]])
                )
            else:
                blocks.append(entry["text"])
        return blocks


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _split_level(text: str, heading_re: re.Pattern[str]) -> tuple[str, list[tuple[str, str, str]]]:
    """Split text into (preamble, [(key, heading_line, body)]) at one level."""
    lines = text.splitlines()
    preamble: list[str] = []
    sections: list[tuple[str, str, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []
    in_fence = False

    def _flush() -> None:
        nonlocal current_heading, current_body
        if current_heading is not None:
            sections.append((
                _normalize_heading(current_heading),
                current_heading,
                "\n".join(current_body).strip("\n"),
            ))
        current_heading = None
        current_body = []

    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and heading_re.match(line):
            _flush()
            current_heading = line
            continue
        if current_heading is None:
            preamble.append(line)
        else:
            current_body.append(line)
    _flush()
    return "\n".join(preamble).strip("\n"), sections


class _SectionMerger:
    """Merge same-titled sections across slices, recursing H2 -> H3."""

    def __init__(self, sub_heading_re: re.Pattern[str] | None) -> None:
        self._sub_heading_re = sub_heading_re
        self._order: list[str] = []
        self._headings: dict[str, str] = {}
        self._lead: dict[str, _BlockAccumulator] = {}
        self._subs: dict[str, "_SectionMerger"] = {}

    def add(self, key: str, heading: str, body: str) -> None:
        if key not in self._headings:
            self._order.append(key)
            self._headings[key] = heading
            self._lead[key] = _BlockAccumulator()
            if self._sub_heading_re is not None:
                self._subs[key] = _SectionMerger(None)
        if self._sub_heading_re is not None:
            lead, subsections = _split_level(body, self._sub_heading_re)
            for block in re.split(r"\n\s*\n", lead):
                self._lead[key].add(block)
            for sub_key, sub_heading, sub_body in subsections:
                self._subs[key].add(sub_key, sub_heading, sub_body)
        else:
            for block in re.split(r"\n\s*\n", body):
                self._lead[key].add(block)

    def render(self) -> list[str]:
        parts: list[str] = []
        for key in self._order:
            parts.append(self._headings[key])
            parts.extend(self._lead[key].render_blocks())
            if key in self._subs:
                parts.extend(self._subs[key].render())
        return parts


def merge_sliced_doc_outputs(outputs: list[str]) -> str:
    """Merge N per-slice document restarts into one deduplicated document."""
    outputs = [o for o in outputs if o and o.strip()]
    if not outputs:
        return ""
    if len(outputs) == 1:
        return reconcile_absence_contradictions(outputs[0])
    return _merge(outputs)


def dedupe_document(text: str) -> str:
    """Repair a single document that already contains repeated structure.

    Splits the text at every restarted H1 title (each restart becomes a
    virtual slice) and runs the same section merge, so a document assembled by
    an older run -- or a model that re-emitted its own structure -- collapses
    to one copy of each section.
    """
    lines = text.splitlines()
    restarts: list[list[str]] = [[]]
    for line in lines:
        if _H1_RE.match(line) and any(part.strip() for part in restarts[-1]):
            restarts.append([])
        restarts[-1].append(line)
    parts = ["\n".join(chunk) for chunk in restarts if "\n".join(chunk).strip()]
    return _merge(parts or [text])


def _merge(outputs: list[str]) -> str:
    title_line = ""
    merged_preamble = ""
    merger = _SectionMerger(_H3_RE)
    toc_done = False

    for output in outputs:
        output_lines = output.splitlines()
        h1_index = next(
            (index for index, line in enumerate(output_lines) if _H1_RE.match(line)),
            None,
        )
        if h1_index is not None:
            if not title_line:
                title_line = output_lines[h1_index]
            # Remove only the first H1 line; everything else is content.
            output_lines = output_lines[:h1_index] + output_lines[h1_index + 1:]
        body = "\n".join(output_lines)
        preamble, sections = _split_level(body, _H2_RE)
        if preamble and not merged_preamble:
            merged_preamble = preamble
        for key, heading, section_body in sections:
            if key in _TOC_TITLES:
                if toc_done:
                    continue
                toc_done = True
            merger.add(key, heading, section_body)

    parts: list[str] = []
    if title_line:
        parts.append(title_line)
    if merged_preamble:
        parts.append(merged_preamble)
    parts.extend(merger.render())
    document = "\n\n".join(part for part in parts if part.strip()).rstrip() + "\n"
    return reconcile_absence_contradictions(document)


# ── contradiction guard ──────────────────────────────────────────────────────


def _absence_terms(block: str) -> list[tuple[str, tuple[str, ...]]]:
    """Extract (matched_phrase, significant_term_tokens) absence claims."""
    claims: list[tuple[str, tuple[str, ...]]] = []
    for match in _ABSENCE_CLAIM_RE.finditer(block):
        tokens = tuple(
            token for token in _TOKEN_RE.findall(match.group("term").casefold())
            if token not in _TERM_STOPWORDS
        )
        if tokens:
            claims.append((match.group(0), tokens))
    return claims


def _positively_asserts(block: str, tokens: tuple[str, ...]) -> bool:
    """True when one NON-HEADING line carries every token, all un-negated.

    Line granularity keeps scattered incidental mentions from counting, and
    heading lines are excluded so a bare severity heading ("### CRITICAL
    Severity" whose body says "None") never counts as a positive finding.
    """
    for raw_line in block.splitlines():
        if raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.casefold()
        line_ok = True
        for token in tokens:
            pattern = re.compile(r"\b" + re.escape(token) + r"\b")
            negated = re.compile(
                _NEGATION_WINDOW_RE_TEMPLATE.format(token=re.escape(token))
            )
            positive = False
            for occurrence in pattern.finditer(line):
                window_start = max(0, occurrence.start() - 60)
                window = line[window_start: occurrence.end()]
                if negated.search(window):
                    continue
                positive = True
                break
            if not positive:
                line_ok = False
                break
        if line_ok:
            return True
    return False


def _document_blocks(text: str) -> list[str]:
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def _contradicted(block_index: int, blocks: list[str]) -> str | None:
    """Return the contradicted absence phrase for blocks[block_index], if any."""
    claims = _absence_terms(blocks[block_index])
    for phrase, tokens in claims:
        for other_index, other in enumerate(blocks):
            if other_index == block_index:
                continue
            if _positively_asserts(other, tokens):
                return phrase
    return None


def contradiction_findings(text: str) -> list[str]:
    """Detect universal-absence claims contradicted elsewhere in the document.

    Detection-only companion of :func:`reconcile_absence_contradictions`,
    used by the validation ladder for reporting (advisory warnings).
    """
    blocks = _document_blocks(text)
    findings: list[str] = []
    for index in range(len(blocks)):
        phrase = _contradicted(index, blocks)
        if phrase is not None:
            findings.append(
                f"absence claim {phrase!r} is contradicted by a positive "
                "assertion of the same finding elsewhere in the document"
            )
        if len(findings) >= 10:
            break
    return findings


def reconcile_absence_contradictions(text: str) -> str:
    """Drop universal-absence blocks contradicted by positive evidence blocks.

    The evidence-backed positive claim wins (verified failure: doc 07 shipped
    both CRITICAL findings and "No CRITICAL severity risks were identified");
    the absence block is dropped, and a heading left with no remaining content
    goes with it. Deterministic selection of verbatim model output; no new
    prose is written.
    """
    lines = text.split("\n")
    # Block spans (start line, end line) so drops map back onto exact lines.
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate([*lines, ""]):
        if line.strip():
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    blocks = ["\n".join(lines[s:e]) for s, e in spans]

    dropped: set[int] = set()
    for block_index in range(len(blocks)):
        if _contradicted(block_index, blocks) is not None:
            dropped.add(block_index)
    if not dropped:
        return text

    # A lone heading whose entire following content was dropped is dropped too
    # (an empty section must not ship). Iterate until stable for nested cases.
    changed = True
    while changed:
        changed = False
        for block_index, block in enumerate(blocks):
            if block_index in dropped:
                continue
            block_lines = block.splitlines()
            if len(block_lines) != 1 or not block_lines[0].lstrip().startswith("#"):
                continue
            level = len(re.match(r"#+", block_lines[0].lstrip()).group(0))
            has_content = False
            for next_index in range(block_index + 1, len(blocks)):
                if next_index in dropped:
                    continue
                next_first = blocks[next_index].splitlines()[0].lstrip()
                heading_match = re.match(r"#+", next_first)
                if heading_match and len(heading_match.group(0)) <= level:
                    break
                has_content = True
                break
            if not has_content:
                dropped.add(block_index)
                changed = True

    dropped_lines: set[int] = set()
    for block_index in dropped:
        span_start, span_end = spans[block_index]
        dropped_lines.update(range(span_start, span_end))
    kept = [line for index, line in enumerate(lines) if index not in dropped_lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip() + "\n"


def duplicate_heading_errors(text: str) -> list[str]:
    """Post-assembly guard: flag documents with repeated top-level structure.

    Any H1 appearing more than once, or any H2 heading title appearing more
    than once, means slice restarts (or model repetition) leaked into the
    final document -- the verified werkorder failure shape.
    """
    h1_count = 0
    h2_counts: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _H1_RE.match(line):
            h1_count += 1
        h2 = _H2_RE.match(line)
        if h2 is not None:
            key = _normalize_heading(h2.group(1))
            h2_counts[key] = h2_counts.get(key, 0) + 1
    errors: list[str] = []
    if h1_count > 1:
        errors.append(
            f"document contains {h1_count} top-level (#) titles; a document has "
            "exactly one title -- slice restarts must be merged, not concatenated"
        )
    repeated = sorted(key for key, count in h2_counts.items() if count > 1)
    if repeated:
        errors.append(
            "document repeats top-level sections: "
            + ", ".join(repeated[:8])
            + ". Each section may appear exactly once."
        )
    return errors
