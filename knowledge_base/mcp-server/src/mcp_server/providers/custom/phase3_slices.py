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


# ---------------------------------------------------------------------------
# Output-sized Phase 3 document planning (slicing redesign).
#
# Verified failure (werkorder ESB_part1, run of record): the R2.5 planner
# above partitions the INPUT (evidence bytes), but every slice is still asked
# to write the WHOLE document from its evidence chunk. Each slice restated
# the full document structure ("Project Orientation" appeared 6x inside one
# H2 after merge; inventory tables restated 4x) and slices CONTRADICTED each
# other because each was blind to the sibling chunks (0 vs 1 vs 2 tLogCatcher
# jobs claimed within one doc set). Meanwhile the model's real per-call output
# is ~16-18K tokens and most documents' deduplicated content is ~7-16K tokens,
# so the multi-slice restarts were self-inflicted.
#
# The redesign sizes the plan by the document's expected OUTPUT:
#
# * SINGLE-CALL path: expected output fits one call -> the document is
#   generated in ONE call. All of its evidence is delivered, deterministically
#   trimmed to the context hard ceiling by priority (doc pack / target
#   evidence first, Tier-3 non-code context trimmed first) at whole-section
#   granularity. No slicing, no restart duplication, no merge.
# * SECTION-PARTITIONED path (genuinely large docs: unit-scaled inventories /
#   rule catalogs): the TASK is partitioned, not just the evidence. Units are
#   packed into disjoint per-slice ranges along whole groups; each slice
#   receives the full shared skeleton (canonical unit index + cross-cutting
#   facts, so no slice can deny a fact a sibling asserts) plus ONLY its own
#   groups' evidence, and is instructed to write ONLY its assigned sections /
#   unit entries. Slice 1 additionally writes the title, the TOC, and the
#   cross-cutting (non unit-scoped) sections. Assembly is section-by-section:
#   each heading appears exactly once; there are no overlapping sections to
#   merge.
#
# Python only selects/re-packs verbatim evidence and writes mechanical
# instructions/markers; it never writes documentation prose (repo rule).
# ---------------------------------------------------------------------------

_SOURCE_ANALYSIS_RE = re.compile(r"^###\s+Source Analysis:\s+(?P<stem>.+?)\.md\s*$")
_INVESTIGATION_RE = re.compile(r"^#\s+Investigation Evidence:\s+(?P<group>.+?)\s*$")
_TOP_HEADING_RE = re.compile(r"^#{1,3}\s")

# H2 sections of the deterministic doc pack that belong in the shared
# skeleton for every slice (relational anchors + cross-cutting facts). The
# titles are produced by ``harness._doc_evidence_pack_text`` in this repo.
_SKELETON_TITLES = frozenset({
    "shared project scope",
    "all groups and units",
    "cross-group reference index",
    "external systems summary",
    "top risks summary",
    "source coverage and scanner limits",
    "documentation obligations",
    "deep evidence pointers",
})

_SECTIONED_SKELETON_BUDGET_BYTES = 64_000

# Floor for the adaptive trim segment size. A late, low-priority file reaching
# the allocator with almost nothing left still gets segmented small enough that
# a useful prefix can be kept instead of the whole file failing the fit test.
_MIN_TRIM_SEGMENT_BYTES = 2_000


def _norm_title(line: str) -> str:
    return re.sub(r"\s+", " ", line.lstrip("#").strip()).lower()


def _trim_priority(name: str) -> int:
    """Deterministic trim order for the single-call bounded context.

    Lower = kept first. The doc pack / deterministic target-evidence bundles
    are the fact source of record; Tier-3 non-code documented context is
    additive narrative and is trimmed first when the ceiling forces a cut.
    """
    lowered = name.lower()
    if "_doc_pack_" in lowered or lowered.startswith("_evidence_") or "_evidence_" in lowered:
        return 0
    if "facts_manifest" in lowered or "_unit_docs_digest" in lowered:
        return 0
    # The runtime rollup is ranked explicitly, not by matching the `_evidence_`
    # convention above (its filename deliberately avoids that substring). At the
    # default rank 2 it is trimmed at SECTION granularity, which can keep the
    # coverage header while cutting the unobserved-units list -- exactly the
    # silence the lane exists to prevent, and it would leave the header's
    # counts describing rows the writer can no longer see.
    if "_runtime_profile" in lowered:
        return 0
    if "obligations" in lowered:
        return 1
    if "noncode" in lowered or "non_code" in lowered:
        return 3
    return 2


@dataclass(frozen=True)
class Phase3PlanInputs:
    """Doc-level facts the harness supplies for output-sized planning."""

    doc_name: str
    expected_output_tokens: int
    single_call_output_tokens: int
    required_sections: tuple[str, ...]
    unit_scoped_sections: tuple[str, ...]
    # (canonical unit name, group) in facts-manifest order.
    units: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Phase3Slice:
    files: list[Path]
    instruction: str | None = None


@dataclass(frozen=True)
class Phase3TaskPlan:
    mode: str  # "single" | "sections"
    slices: list[Phase3Slice]
    trimmed_bytes: int = 0
    trimmed_sources: tuple[str, ...] = ()
    section_order: tuple[str, ...] = ()


@dataclass
class _AttributedSegment:
    source: str
    group: str | None  # manifest group, or None when unattributable
    text: str

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8"))


def _resolve_group(stem: str, groups: list[str]) -> str | None:
    """Map an analysis-file stem back to its manifest group.

    Mirrors the existing slice->group convention (``harness._group_units``):
    a preprocess/analysis slice for group G is named ``G`` or ``G_part*``
    (large-group splits and ``_part_ci{k}`` case-collision suffixes).
    Longest matching group wins so ``X_GetLocation`` never matches
    ``X_GetLocations``-style prefixes.
    """
    best: str | None = None
    for group in groups:
        if stem == group or stem.startswith(f"{group}_part"):
            if best is None or len(group) > len(best):
                best = group
    return best


def _attribute_blocks(
    text: str, source: str, groups: list[str]
) -> tuple[list[_AttributedSegment], list[_AttributedSegment]]:
    """Split one evidence file into skeleton parts + group-attributed blocks.

    Boundaries are the doc-pack structure: H2 sections, ``### Source
    Analysis: {slice}.md`` markers (Phase 2 evidence per group) and
    ``# Investigation Evidence: {group}`` markers (Phase 1 evidence per
    group). Named skeleton sections are returned separately; every other
    block is attributed to a group when its heading resolves to one, else
    left unattributed. Content is preserved verbatim.

    Skeleton parts carry their ``source`` too: the skeleton is truncated at
    ``_SECTIONED_SKELETON_BUDGET_BYTES`` and the plan cannot tell whether a
    file vanished entirely without knowing which file those bytes came from.
    """
    lines = text.splitlines(keepends=True)
    skeleton_parts: list[_AttributedSegment] = []
    segments: list[_AttributedSegment] = []
    current: list[str] = []
    current_group: str | None = None
    current_is_skeleton = False
    in_fence = False

    def _flush() -> None:
        nonlocal current
        if not current:
            return
        block = "".join(current)
        if block.strip():
            if current_is_skeleton:
                skeleton_parts.append(
                    _AttributedSegment(source=source, group=None, text=block)
                )
            else:
                segments.append(_AttributedSegment(source=source, group=current_group, text=block))
        current = []

    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        boundary = not in_fence and _TOP_HEADING_RE.match(line)
        if boundary:
            source_match = _SOURCE_ANALYSIS_RE.match(line.rstrip("\n"))
            investigation_match = _INVESTIGATION_RE.match(line.rstrip("\n"))
            if source_match:
                _flush()
                current_group = _resolve_group(source_match.group("stem").strip(), groups)
                current_is_skeleton = False
            elif investigation_match:
                _flush()
                current_group = _resolve_group(investigation_match.group("group").strip(), groups)
                current_is_skeleton = False
            elif line.startswith("## ") or line.startswith("# "):
                _flush()
                current_group = None
                current_is_skeleton = line.startswith("## ") and _norm_title(line) in _SKELETON_TITLES
            # H3 headings that are not Source Analysis markers stay inside
            # the current block (they are sub-structure, not boundaries).
        current.append(line)
    _flush()
    return skeleton_parts, segments


def plan_phase3_document(
    staging_dir: Path,
    *,
    phase: str,
    target: str,
    prompt: str,
    context_files: list[Path],
    hard_tokens: int,
    soft_tokens: int,
    shared_file_max_bytes: int,
    inputs: Phase3PlanInputs,
) -> Phase3TaskPlan | None:
    """Plan one Phase 3 document call sized by expected OUTPUT.

    Returns a single-call plan (with deterministic priority trimming when the
    evidence exceeds the context ceiling), a section-partitioned plan for
    genuinely large documents, or ``None`` when this planner does not apply
    (the caller falls back to the legacy input-partitioned path).
    """
    existing = [p for p in context_files if p.exists()]
    if not existing:
        return None

    single_cap = max(2_000, inputs.single_call_output_tokens)
    section_order = tuple(
        list(inputs.required_sections)
        + (["Evidence Register"] if "Evidence Register" not in inputs.required_sections else [])
    )

    if inputs.expected_output_tokens <= single_cap or not inputs.unit_scoped_sections or len(inputs.units) < 2:
        return _plan_single_call(
            staging_dir,
            phase=phase,
            target=target,
            prompt=prompt,
            context_files=existing,
            hard_tokens=hard_tokens,
            section_order=section_order,
        )
    plan = _plan_sectioned(
        staging_dir,
        phase=phase,
        target=target,
        prompt=prompt,
        context_files=existing,
        hard_tokens=hard_tokens,
        shared_file_max_bytes=shared_file_max_bytes,
        inputs=inputs,
        single_cap=single_cap,
        section_order=section_order,
    )
    if plan is not None:
        return plan
    # No group-attributable evidence: a partitioned TASK cannot be assigned
    # safely. Degrade to the bounded single call (one coherent document beats
    # N contradicting restarts).
    return _plan_single_call(
        staging_dir,
        phase=phase,
        target=target,
        prompt=prompt,
        context_files=existing,
        hard_tokens=hard_tokens,
        section_order=section_order,
    )


def _partial_trim_note(name: str) -> str:
    """Marker for a file that reached the context with only part of its bytes."""
    return (
        f"> NOTE: lower-priority sections of `{name}` were omitted to fit "
        "this document's single-call context ceiling; the deterministic "
        "artifacts of record retain the full content.\n\n"
    )


def _full_omission_note(name: str, size_bytes: int) -> str:
    """Marker for a file that could not contribute a single segment.

    A fully-dropped file used to be removed from the context list outright, so
    the writer saw no trace of it while the prompt still instructed it to
    prioritise that evidence -- indistinguishable from "this evidence does not
    exist". The note keeps the file present, names it, states its size, and
    points at the on-disk artifact of record.
    """
    return (
        f"> NOTE: `{name}` ({size_bytes} bytes) was omitted IN FULL to fit this "
        "document's single-call context ceiling; the deterministic artifact of "
        "record retains the full content.\n>\n"
        "> This evidence is absent from THIS CONTEXT only. Do not report its "
        "subject matter as absent from the project, and do not treat its "
        "absence here as a scanner or coverage limit.\n\n"
    )


_OMISSION_FILE_HEADER = "# Evidence omitted from this context\n\n"


def _sectioned_full_omission_note(name: str, size_bytes: int) -> str:
    """Marker for a source that reached NO part of a section-partitioned plan.

    Sibling of ``_full_omission_note`` for the sectioned path. "Absent from
    this part" is normal there -- the plan distributes evidence deliberately --
    but "absent from every part" is the same defect the single-call path had:
    the writers see no trace of an artifact the prompt still tells them to
    prioritise, so "did not fit" reads exactly like "does not exist".
    """
    return (
        f"> NOTE: `{name}` ({size_bytes} bytes) was omitted IN FULL from EVERY part of "
        "this document's section-partitioned context; the deterministic artifact of "
        "record retains the full content.\n>\n"
        "> This evidence is absent from THIS CONTEXT only. Do not report its subject "
        "matter as absent from the project, and do not treat its absence here as a "
        "scanner or coverage limit.\n\n"
    )


def _sectioned_partial_trim_note(name: str, kept_bytes: int, size_bytes: int) -> str:
    """Marker for a source only partly carried by a section-partitioned plan.

    Reports the WHOLE-PLAN split, not this part's share: a source spread over
    several parts is fully present and gets no note at all.
    """
    return (
        f"> NOTE: {kept_bytes} of {size_bytes} bytes of `{name}` were distributed over "
        "the parts of this document; the remainder was omitted to fit the per-part "
        "context ceiling, and the deterministic artifact of record retains the full "
        "content.\n>\n"
        "> Do not report the omitted subject matter as absent from the project, and do "
        "not treat its absence here as a scanner or coverage limit.\n\n"
    )


def _fit_segments(
    segments: list[_AttributedSegment],
    *,
    remaining: int,
    max_segment_bytes: int,
) -> tuple[list[tuple[str, str]], int, dict[str, int]]:
    """First-fit pack of whole blocks, sub-segmenting only when one will not fit.

    Returns ``(kept (source, text) pairs, remaining bytes, kept bytes by
    source)``. A block that still fits is kept WHOLE and verbatim, so the
    common case is byte-identical to the plain fit test it replaces.

    A block that does NOT fit used to contribute nothing at all. The blocks
    here are attribution boundaries with no size cap -- a heading-less
    deterministic fact map is ONE block of the whole file -- so a single
    oversized block dropped 100% of its source while the part's capacity went
    unused (measured below). It is now re-split by ``segment_markdown`` at a
    cap adapted to what is still affordable, exactly as ``_plan_single_call``
    adapts its trim segment size, so a real verbatim prefix survives.
    """
    kept: list[tuple[str, str]] = []
    kept_by_source: dict[str, int] = {}
    for seg in segments:
        size = seg.size_bytes
        if size <= remaining:
            kept.append((seg.source, seg.text))
            remaining -= size
            kept_by_source[seg.source] = kept_by_source.get(seg.source, 0) + size
            continue
        if remaining < _MIN_TRIM_SEGMENT_BYTES:
            continue
        cap = min(max_segment_bytes, max(_MIN_TRIM_SEGMENT_BYTES, remaining))
        for piece in segment_markdown(seg.text, cap):
            piece_bytes = len(piece.encode("utf-8"))
            if piece_bytes <= remaining:
                kept.append((seg.source, piece))
                remaining -= piece_bytes
                kept_by_source[seg.source] = kept_by_source.get(seg.source, 0) + piece_bytes
    return kept, remaining, kept_by_source


def _plan_single_call(
    staging_dir: Path,
    *,
    phase: str,
    target: str,
    prompt: str,
    context_files: list[Path],
    hard_tokens: int,
    section_order: tuple[str, ...],
) -> Phase3TaskPlan | None:
    prompt_tokens = max(1, len(prompt) // 4)
    total_bytes = 0
    sizes: dict[Path, int] = {}
    for path in context_files:
        try:
            sizes[path] = path.stat().st_size
        except OSError:
            sizes[path] = 0
        total_bytes += sizes[path]
    if prompt_tokens + total_bytes // 4 + 512 <= hard_tokens:
        return Phase3TaskPlan(
            mode="single",
            slices=[Phase3Slice(files=list(context_files))],
            section_order=section_order,
        )

    # Evidence exceeds the ceiling: keep every file's content in original file
    # order but allocate the byte budget by trim priority (lowest priority
    # number fills first), cutting at whole-section granularity.
    budget = (hard_tokens - prompt_tokens - 1024) * 4
    if budget < 8_000:
        return None
    # Every trimmed file carries an explanatory NOTE into the same context, so
    # those bytes are reserved UP FRONT: adding a note can never push the packet
    # past the ceiling. Whatever a file does not spend on its note is refunded
    # to `remaining` right after it settles, in the same deterministic order, so
    # the reservation costs later files nothing when earlier ones fit whole.
    notes: dict[Path, tuple[str, str, int]] = {}
    note_reserve = 0
    for path in context_files:
        partial = _partial_trim_note(path.name)
        full = _full_omission_note(path.name, sizes.get(path, 0))
        reserved = max(len(partial.encode("utf-8")), len(full.encode("utf-8")))
        notes[path] = (partial, full, reserved)
        note_reserve += reserved

    allocations: dict[Path, list[str]] = {}
    dropped_by_path: dict[Path, int] = {}
    trimmed_sources: list[str] = []
    trimmed_bytes = 0
    remaining = max(0, budget - note_reserve)
    ordered_by_priority = sorted(
        context_files, key=lambda p: (_trim_priority(p.name), context_files.index(p))
    )
    max_segment = max(8_000, budget // 4)
    for path in ordered_by_priority:
        partial_note, full_note, reserved = notes[path]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            remaining += reserved  # no note will be written for an unreadable file
            continue
        # The segment size ADAPTS to what is still affordable. A fixed
        # `budget // 4` (~117 KB on a ~470 KB budget) is coarser than the budget
        # left once the rank-0/1 files have filled, so every segment of a late
        # rank-2 fact map failed `seg_bytes <= remaining` and the file was
        # dropped 100% while ~98 KB of paid-for budget went unused (verified:
        # dependency_map.md, 1,755,045 B, lost in full from three documents).
        seg_cap = min(max_segment, max(_MIN_TRIM_SEGMENT_BYTES, remaining))
        kept: list[str] = []
        dropped_here = 0
        for segment in segment_markdown(text, seg_cap):
            seg_bytes = len(segment.encode("utf-8"))
            if seg_bytes <= remaining:
                kept.append(segment)
                remaining -= seg_bytes
            else:
                dropped_here += seg_bytes
        allocations[path] = kept
        if dropped_here:
            dropped_by_path[path] = dropped_here
            trimmed_sources.append(path.name)
            trimmed_bytes += dropped_here
            spent = len(partial_note.encode("utf-8")) if kept else len(full_note.encode("utf-8"))
        else:
            spent = 0  # nothing cut -> the original file is used, no note
        remaining += reserved - spent

    slice_dir = transient_dir(staging_dir, "context_slices", phase)
    files: list[Path] = []
    fully_omitted: list[str] = []
    for path in context_files:
        kept = allocations.get(path)
        if kept is None:
            files.append(path)
            continue
        partial_note, full_note, _reserved = notes[path]
        content = "".join(kept)
        if not content.strip():
            if path not in dropped_by_path:
                continue  # the file itself was empty; there is nothing to report
            # FULL DROP. Removing the file from `files` here was the second
            # defect: the document writer lost every trace that the evidence
            # existed, so "did not fit" read exactly like "does not exist".
            omitted = slice_dir / f"{_safe_name(target)}__omitted_{_safe_name(path.name)}"
            transient_write(omitted, full_note)
            files.append(omitted)
            fully_omitted.append(path.name)
            continue
        if len(content.encode("utf-8")) >= sizes.get(path, 0) - 64:
            files.append(path)  # fully kept -- use the original (cache-stable)
            continue
        bounded = slice_dir / f"{_safe_name(target)}__bounded_{_safe_name(path.name)}"
        transient_write(bounded, partial_note + content)
        files.append(bounded)
    if not files:
        return None
    logger.info(
        "[phase3-plan] %s: SINGLE call with bounded context "
        "(%d bytes trimmed from %s; %d byte(s) of %d budget unused; "
        "fully omitted: %s)",
        target, trimmed_bytes, ", ".join(trimmed_sources) or "nothing",
        remaining, budget, ", ".join(fully_omitted) or "nothing",
    )
    return Phase3TaskPlan(
        mode="single",
        slices=[Phase3Slice(files=files)],
        trimmed_bytes=trimmed_bytes,
        trimmed_sources=tuple(trimmed_sources),
        section_order=section_order,
    )


def _plan_sectioned(
    staging_dir: Path,
    *,
    phase: str,
    target: str,
    prompt: str,
    context_files: list[Path],
    hard_tokens: int,
    shared_file_max_bytes: int,
    inputs: Phase3PlanInputs,
    single_cap: int,
    section_order: tuple[str, ...],
) -> Phase3TaskPlan | None:
    groups_in_order: list[str] = []
    units_by_group: dict[str, list[str]] = {}
    for unit_name, group in inputs.units:
        if group not in units_by_group:
            groups_in_order.append(group)
            units_by_group[group] = []
        units_by_group[group].append(unit_name)

    shared: list[Path] = []
    evidence_files: list[Path] = []
    for path in context_files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        (shared if size <= shared_file_max_bytes else evidence_files).append(path)

    skeleton_parts: list[_AttributedSegment] = []
    attributed: list[_AttributedSegment] = []
    # Whole-plan byte accounting per SOURCE FILE. A source absent from one part
    # is normal (the plan partitions evidence deliberately); a source absent
    # from EVERY part is the sectioned analogue of the single-call whole-file
    # drop and must never be silent.
    source_order: list[str] = []
    file_sizes: dict[str, int] = {}
    offered_by_source: dict[str, int] = {}
    for path in evidence_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # unreadable: it offered nothing, so there is nothing to report
        file_skeleton, file_segments = _attribute_blocks(text, path.name, groups_in_order)
        skeleton_parts.extend(file_skeleton)
        attributed.extend(file_segments)
        if path.name not in offered_by_source:
            source_order.append(path.name)
            offered_by_source[path.name] = 0
            file_sizes[path.name] = path.stat().st_size
        offered_by_source[path.name] += sum(
            seg.size_bytes for seg in (*file_skeleton, *file_segments)
        )

    group_bytes: dict[str, int] = {g: 0 for g in groups_in_order}
    has_group_evidence = False
    for seg in attributed:
        if seg.group is not None:
            group_bytes[seg.group] = group_bytes.get(seg.group, 0) + seg.size_bytes
            has_group_evidence = True
    if not has_group_evidence:
        return None

    kept_by_source: dict[str, int] = {name: 0 for name in source_order}

    slice_dir = transient_dir(staging_dir, "context_slices", phase)
    skeleton_path: Path | None = None
    skeleton_bytes = 0
    if skeleton_parts:
        skeleton_text_parts: list[str] = []
        used = 0
        for part in skeleton_parts:
            part_bytes = part.size_bytes
            if used + part_bytes > _SECTIONED_SKELETON_BUDGET_BYTES and skeleton_text_parts:
                break
            skeleton_text_parts.append(part.text)
            kept_by_source[part.source] = kept_by_source.get(part.source, 0) + part_bytes
            used += part_bytes
        skeleton_path = slice_dir / f"{_safe_name(target)}__skeleton.md"
        transient_write(skeleton_path, "".join(skeleton_text_parts))
        skeleton_bytes = skeleton_path.stat().st_size

    # Every source can end up needing an omission NOTE, and those notes ride in
    # the shared prefix of EVERY part, so their bytes are reserved UP FRONT --
    # adding a note can never push a part past the ceiling. The reservation is
    # the worst case (the longer of the two note forms, with the largest numbers
    # they can carry) and is not refunded: it is a few hundred bytes per
    # evidence file against a ~400 KB per-part capacity, and a refund pass would
    # make the capacity depend on the packing it is an input to.
    note_reserve = len(_OMISSION_FILE_HEADER.encode("utf-8")) if source_order else 0
    for name in source_order:
        size = file_sizes[name]
        note_reserve += max(
            len(_sectioned_full_omission_note(name, size).encode("utf-8")),
            len(_sectioned_partial_trim_note(name, size, size).encode("utf-8")),
        )

    prompt_tokens = max(1, len(prompt) // 4)
    shared_bytes = (
        sum(p.stat().st_size for p in shared if p.exists()) + skeleton_bytes + note_reserve
    )
    capacity_bytes = (hard_tokens - prompt_tokens - shared_bytes // 4 - 1024) * 4
    if capacity_bytes < 16_000:
        return None

    total_units = len(inputs.units)
    slice_count = max(2, math.ceil(inputs.expected_output_tokens / single_cap))
    unit_budget = max(1, math.ceil(total_units / slice_count))

    # Pack whole groups into ordered bins: close a bin when its unit budget is
    # met or its evidence would exceed the per-slice byte capacity.
    bins: list[list[str]] = []
    current_groups: list[str] = []
    current_units = 0
    current_bytes = 0
    for group in groups_in_order:
        g_units = len(units_by_group[group])
        g_bytes = group_bytes.get(group, 0)
        if current_groups and (
            current_units + g_units > unit_budget or current_bytes + g_bytes > capacity_bytes
        ):
            bins.append(current_groups)
            current_groups = []
            current_units = 0
            current_bytes = 0
        current_groups.append(group)
        current_units += g_units
        current_bytes += g_bytes
    if current_groups:
        bins.append(current_groups)
    if len(bins) < 2:
        # Everything fits one output bin after all -- write it in one call.
        return None

    group_to_slice = {g: i for i, bin_groups in enumerate(bins) for g in bin_groups}
    unit_scoped = list(inputs.unit_scoped_sections)
    if "Evidence Register" in section_order and "Evidence Register" not in unit_scoped:
        unit_scoped.append("Evidence Register")
    cross_cutting = [s for s in section_order if s not in unit_scoped]

    total = len(bins)
    shared_with_skeleton = [*shared, *([skeleton_path] if skeleton_path else [])]
    part_paths: list[Path] = []
    instructions: list[str] = []
    # Trim segment cap for a block that will not fit whole; mirrors the
    # single-call allocator's `max(8_000, budget // 4)` coarse cap, then
    # adapted per block to what is still affordable inside `_fit_segments`.
    max_segment_bytes = max(8_000, capacity_bytes // 4)
    for idx, bin_groups in enumerate(bins, start=1):
        bin_set = set(bin_groups)
        segments = [seg for seg in attributed if seg.group in bin_set]
        if idx == 1:
            # Unattributed evidence (non-code documented context, obligations
            # JSON, deterministic fact maps, unmatched blocks) supports the
            # cross-cutting sections and rides with slice 1, byte-bounded with
            # priority trimming.
            segments.extend(
                sorted(
                    (seg for seg in attributed if seg.group is None),
                    key=lambda s: (_trim_priority(s.source), attributed.index(s)),
                )
            )
        # A bin whose evidence exceeds capacity is trimmed at segment
        # granularity (deterministic, recorded) rather than aborted. Blocks
        # that fit are kept whole and verbatim.
        sized, _remaining, bin_kept = _fit_segments(
            segments, remaining=capacity_bytes, max_segment_bytes=max_segment_bytes
        )
        for name, kept in bin_kept.items():
            kept_by_source[name] = kept_by_source.get(name, 0) + kept
        sources = sorted({source for source, _text in sized})
        header = (
            f"# Evidence part {idx} of {total} for {target}\n\n"
            f"Assigned groups: {', '.join(bin_groups)}\n\n"
            "Verbatim evidence sections from: " + ", ".join(sources) + "\n\n---\n\n"
        )
        part_path = slice_dir / f"{_safe_name(target)}__part_{idx:02d}.md"
        transient_write(part_path, header + "".join(text for _source, text in sized))
        part_paths.append(part_path)
        unit_lines = "\n".join(
            f"- {unit} (group {group})"
            for group in bin_groups
            for unit in units_by_group[group]
        )
        instructions.append(
            _sectioned_instruction(
                idx=idx,
                total=total,
                target=target,
                unit_scoped=unit_scoped,
                cross_cutting=cross_cutting,
                unit_lines=unit_lines,
            )
        )

    # WHOLE-PLAN omission accounting. Only now is it known what every part
    # actually carries, so a source is judged against the plan, not a bin: a
    # source spread over several parts is fully present and gets no note.
    notes: list[str] = []
    fully_omitted: list[str] = []
    trimmed_bytes = 0
    trimmed_sources: list[str] = []
    for name in source_order:
        offered = offered_by_source[name]
        kept = min(kept_by_source.get(name, 0), offered)
        dropped = offered - kept
        if dropped <= 0:
            continue
        trimmed_bytes += dropped
        trimmed_sources.append(name)
        size = file_sizes[name]
        if kept == 0:
            fully_omitted.append(name)
            notes.append(_sectioned_full_omission_note(name, size))
        else:
            notes.append(_sectioned_partial_trim_note(name, kept, size))
    if notes:
        # ONE shared file in every part's prefix, byte-stable and in evidence
        # file order. Shared rather than part-1-only because any part's writer
        # can otherwise assert an absence the artifact of record contradicts.
        omissions_path = slice_dir / f"{_safe_name(target)}__omitted_evidence.md"
        transient_write(omissions_path, _OMISSION_FILE_HEADER + "".join(notes))
        shared_with_skeleton = [*shared_with_skeleton, omissions_path]

    slices = [
        Phase3Slice(files=[*shared_with_skeleton, part_path], instruction=instruction)
        for part_path, instruction in zip(part_paths, instructions, strict=True)
    ]

    logger.info(
        "[phase3-plan] %s: SECTION-PARTITIONED into %d part(s) "
        "(expected_output=%d tokens, cap=%d, unit_scoped=%s; "
        "%d bytes trimmed from %s; fully omitted: %s)",
        target, total, inputs.expected_output_tokens, single_cap, ", ".join(unit_scoped),
        trimmed_bytes, ", ".join(trimmed_sources) or "nothing",
        ", ".join(fully_omitted) or "nothing",
    )
    return Phase3TaskPlan(
        mode="sections",
        slices=slices,
        trimmed_bytes=trimmed_bytes,
        trimmed_sources=tuple(trimmed_sources),
        section_order=section_order,
    )


def _sectioned_instruction(
    *,
    idx: int,
    total: int,
    target: str,
    unit_scoped: list[str],
    cross_cutting: list[str],
    unit_lines: str,
) -> str:
    unit_scoped_block = "\n".join(f"  ## {s}" for s in unit_scoped)
    if idx == 1:
        cross_block = "\n".join(f"  ## {s}" for s in cross_cutting)
        return (
            f"PART ASSIGNMENT: `{target}` is written in {total} parts by parallel "
            "writers over disjoint unit ranges and assembled section-by-section "
            f"afterwards. You are part 1 of {total}.\n\n"
            "Write, in this order:\n"
            "1. The document title (single `#` heading) and the Table of Contents "
            "covering ALL mandatory headings of the assembled document.\n"
            "2. These cross-cutting sections in full (they appear only in this part):\n"
            f"{cross_block or '  (none)'}\n"
            "3. These unit-scoped sections, covering ONLY your assigned units below:\n"
            f"{unit_scoped_block}\n\n"
            "Your assigned units (write entries/rows for these and no others):\n"
            f"{unit_lines}\n\n"
            "The shared skeleton in your context lists ALL units and the "
            "whole-partition cross-cutting facts: use it for totals, counts, and "
            "cross-references so your statements agree with the other parts, but do "
            "NOT write per-unit entries for units assigned to other parts. "
            "The MANDATORY HEADINGS requirement applies to the assembled document, "
            "not to this part alone."
        )
    return (
        f"PART ASSIGNMENT: `{target}` is written in {total} parts by parallel "
        "writers over disjoint unit ranges and assembled section-by-section "
        f"afterwards. You are part {idx} of {total}.\n\n"
        "Write ONLY these sections, each as a `##` heading with exactly this wording:\n"
        f"{unit_scoped_block}\n\n"
        "Under each heading write ONLY the entries/rows/subsections for your "
        "assigned units:\n"
        f"{unit_lines}\n\n"
        "Do NOT write a document title, a Table of Contents, or any other section. "
        "Do NOT restate project-wide totals, absence claims, or cross-cutting "
        "findings -- those belong to part 1. The shared skeleton lists ALL units "
        "and the whole-partition cross-cutting facts: use it so your statements "
        "agree with the other parts. Reuse the exact table header columns the "
        "section requires so the parts union into one table."
    )
