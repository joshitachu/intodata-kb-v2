"""Input-partitioned Phase 3 slice planning (R2.5).

Verified failure (werkorder Assetcheck run, 73 units / 17 groups): the generic
``_split_context_pack`` splitter replicated every "common" context file
(~195 KB: the 120 KB non-code project context, the 62 KB obligations file,
fact maps) into EVERY slice and paged the remaining evidence into small
chunks sized by whatever budget was left. Wanted slice counts hit 19-22, the
4-slice cap dropped the excess chunks, and the published summary docs were
truncated mid-inventory while total input was ~N x the whole pack.

This module plans Phase 3 document slices the way Phase 2 plans evidence
tasks (``evidence_tasks._pack_bins``): the slicer decides UP FRONT which
sections each slice covers and gives each slice ONLY its portion of the
evidence, plus a small byte-stable SHARED skeleton every slice needs
(unit index, cross-refs, small relational maps, doc-pack header):

* Context files at or below ``shared_file_max_bytes`` are SHARED and appear
  in every slice, byte-stable and in stable order (provider prefix cache
  holds through prompt + shared files across a document's slices).
* Larger files are EVIDENCE: each is split into whole markdown sections
  (H2 first, oversized H2s on H3, oversized leaves at line boundaries) and
  the sections are first-fit bin-packed into per-slice part files. A section
  is never split across slices unless it alone exceeds a slice's capacity.
* The leading small sections of an evidence file (e.g. the doc pack's
  "Shared Project Scope" / "All Groups And Units" header) join the shared
  skeleton up to ``skeleton_budget_bytes`` so relational docs keep the unit
  index and cross-reference map in every slice.
* Slice count is chosen to COVER all evidence: prefer ``max_slices``
  (the per-doc allowance), but grow beyond it when even full-capacity
  slices cannot hold everything -- an input-partitioned extra slice costs
  one shared prefix, not another full pack, and dropped evidence was the
  verified truncation driver.

Python only selects and re-packs verbatim file content; it never writes new
prose (repo rule). All decisions are deterministic in file order.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from mcp_server.providers.custom.transient_artifacts import transient_dir, transient_write

logger = logging.getLogger(__name__)

_H2_BOUNDARY_RE = re.compile(r"^## ", re.MULTILINE)
_H3_BOUNDARY_RE = re.compile(r"^### ", re.MULTILINE)

# A leading section may join the shared skeleton only when it is small; the
# first bulk section ends skeleton accumulation for its file. Sized so a
# ~150-unit "All Groups And Units" index (the relational anchor every slice
# needs) still qualifies; larger projects partition into sub-runs anyway.
_SKELETON_SECTION_MAX_BYTES = 12_000

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(value: str) -> str:
    return _SAFE_NAME_RE.sub("_", value)


def _split_on(pattern: re.Pattern[str], text: str) -> list[str]:
    """Split text into (preamble, section...) pieces on heading boundaries."""
    starts = [m.start() for m in pattern.finditer(text)]
    if not starts:
        return [text]
    pieces: list[str] = []
    if starts[0] > 0:
        pieces.append(text[: starts[0]])
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        pieces.append(text[start:end])
    return pieces


def _split_lines_to_size(text: str, max_bytes: int) -> list[str]:
    """Last-resort split of one oversized section at line boundaries."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8"))
        if current and current_bytes + line_bytes > max_bytes:
            out.append("".join(current))
            current = []
            current_bytes = 0
        current.append(line)
        current_bytes += line_bytes
    if current:
        out.append("".join(current))
    return out or [text]


def segment_markdown(text: str, max_segment_bytes: int) -> list[str]:
    """Split markdown into whole sections no larger than ``max_segment_bytes``.

    H2 boundaries first; an oversized H2 section is re-split on its H3
    boundaries; a still-oversized leaf is split at line boundaries. Section
    text is preserved verbatim (concatenating all segments reproduces the
    original file byte-for-byte).
    """
    segments: list[str] = []
    for h2_piece in _split_on(_H2_BOUNDARY_RE, text):
        if len(h2_piece.encode("utf-8")) <= max_segment_bytes:
            segments.append(h2_piece)
            continue
        for h3_piece in _split_on(_H3_BOUNDARY_RE, h2_piece):
            if len(h3_piece.encode("utf-8")) <= max_segment_bytes:
                segments.append(h3_piece)
            else:
                segments.extend(_split_lines_to_size(h3_piece, max_segment_bytes))
    return [s for s in segments if s.strip()]


@dataclass(frozen=True)
class _EvidenceSegment:
    source: str  # file name the bytes came from
    text: str

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


def plan_phase3_slices(
    staging_dir: Path,
    *,
    phase: str,
    target: str,
    prompt: str,
    context_files: list[Path],
    hard_tokens: int,
    soft_tokens: int,
    max_slices: int,
    shared_file_max_bytes: int,
    skeleton_budget_bytes: int = 24_000,
) -> list[list[Path]] | None:
    """Plan input-partitioned slices for one Phase 3 document call.

    Returns a list of per-slice context-file lists (shared files + skeleton +
    one part file each), or ``None`` when partitioning is not applicable
    (nothing to partition, or the shared prefix alone exceeds the hard
    ceiling -- the caller then falls back to the generic splitter).
    """
    existing = [p for p in context_files if p.exists()]
    if not existing:
        return None

    shared: list[Path] = []
    evidence_files: list[Path] = []
    for path in existing:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        (shared if size <= shared_file_max_bytes else evidence_files).append(path)

    if not evidence_files:
        return None

    # Per-slice evidence capacity in bytes, from the token ceilings the
    # harness uses everywhere (~4 bytes/token, same as estimate_input_tokens).
    prompt_tokens = max(1, len(prompt) // 4)
    shared_bytes = sum(p.stat().st_size for p in shared)
    skeleton_parts: list[str] = []
    skeleton_used = 0

    segments: list[_EvidenceSegment] = []
    # Leaf segments must fit a slice on their own; sized against the SOFT
    # target so a single section never forces an oversized slice.
    max_segment_bytes = max(8_000, (soft_tokens * 4) // 2)
    for path in evidence_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Skeleton selection happens at the TOP (H2) level only: the file's
        # leading small top-level sections (doc-pack header: project scope,
        # unit index, obligations digest, evidence pointers) join the shared
        # skeleton; the first bulk top-level section ("Doc-Specific Phase 2
        # Evidence") ends accumulation. Selecting at leaf level instead would
        # wrongly promote small per-group evidence sections produced by
        # splitting a bulk section.
        top_pieces = _split_on(_H2_BOUNDARY_RE, text)
        remaining_from = 0
        for i, piece in enumerate(top_pieces):
            piece_bytes = len(piece.encode("utf-8"))
            if (
                piece_bytes <= _SKELETON_SECTION_MAX_BYTES
                and skeleton_used + piece_bytes <= skeleton_budget_bytes
            ):
                skeleton_parts.append(piece)
                skeleton_used += piece_bytes
                remaining_from = i + 1
                continue
            break
        for piece in top_pieces[remaining_from:]:
            segments.extend(
                _EvidenceSegment(source=path.name, text=seg)
                for seg in segment_markdown(piece, max_segment_bytes)
            )

    if not segments:
        return None

    slice_dir = transient_dir(staging_dir, "context_slices", phase)
    skeleton_path: Path | None = None
    if skeleton_parts:
        skeleton_path = slice_dir / f"{_safe_name(target)}__skeleton.md"
        transient_write(skeleton_path, "".join(skeleton_parts))
        shared_bytes += skeleton_path.stat().st_size

    shared_tokens = prompt_tokens + shared_bytes // 4 + 512
    capacity_tokens = hard_tokens - shared_tokens
    if capacity_tokens < 2_000:
        # Shared prefix alone (nearly) fills the ceiling; partitioning cannot
        # help here -- let the generic splitter degrade as before.
        return None
    capacity_bytes = capacity_tokens * 4

    total_evidence_bytes = sum(seg.size_bytes for seg in segments)
    # Prefer soft-target-sized slices within the per-doc allowance; grow the
    # slice count beyond the allowance only when even full-capacity slices
    # cannot cover the evidence (coverage beats the cap: a partitioned extra
    # slice costs one shared prefix, not another full pack).
    target_bytes = max(8_000, (soft_tokens - shared_tokens) * 4)
    wanted = max(1, math.ceil(total_evidence_bytes / target_bytes))
    needed_at_capacity = max(1, math.ceil(total_evidence_bytes / capacity_bytes))
    slice_count = min(max(1, max_slices), wanted)
    slice_count = max(slice_count, needed_at_capacity)
    per_slice_bytes = min(
        capacity_bytes,
        max(target_bytes, math.ceil(total_evidence_bytes / slice_count)),
    )

    # First-fit bin packing in stable file/section order (same discipline as
    # evidence_tasks._pack_bins: whole sections, never split further here).
    bins: list[list[_EvidenceSegment]] = []
    current: list[_EvidenceSegment] = []
    current_bytes = 0
    for seg in segments:
        if current and current_bytes + seg.size_bytes > per_slice_bytes:
            bins.append(current)
            current = []
            current_bytes = 0
        current.append(seg)
        current_bytes += seg.size_bytes
    if current:
        bins.append(current)

    shared_with_skeleton = [*shared, *([skeleton_path] if skeleton_path else [])]
    slices: list[list[Path]] = []
    total = len(bins)
    for idx, bin_segments in enumerate(bins, start=1):
        sources = sorted({seg.source for seg in bin_segments})
        header = (
            f"# Evidence part {idx} of {total} for {target}\n\n"
            "Verbatim evidence sections from: " + ", ".join(sources) + "\n\n---\n\n"
        )
        part_path = slice_dir / f"{_safe_name(target)}__part_{idx:02d}.md"
        transient_write(part_path, header + "".join(seg.text for seg in bin_segments))
        slices.append([*shared_with_skeleton, part_path])

    logger.info(
        "[phase3-slices] %s: %d shared file(s) (%d bytes incl. skeleton), "
        "%d evidence file(s) -> %d segment(s) -> %d slice(s) "
        "(evidence %d bytes, per-slice budget %d bytes)",
        target, len(shared_with_skeleton), shared_bytes, len(evidence_files),
        len(segments), len(slices), total_evidence_bytes, per_slice_bytes,
    )
    return slices
