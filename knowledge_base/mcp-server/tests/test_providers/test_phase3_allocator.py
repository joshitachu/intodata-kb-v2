"""Byte allocator of the Phase 3 single-call bounded context.

Verified failure (production run of record): `_plan_single_call` sized every
trim segment at a fixed `budget // 4`. On a ~470 KB budget that is ~117 KB per
segment, while the rank-2 deterministic fact maps reach the allocator with only
~98 KB of `remaining` left after the rank-0 doc pack and the rank-1 obligations
file have filled. Every segment therefore failed `seg_bytes <= remaining` and
the whole file was dropped -- measured trims of 2,615,422 B / 1,755,045 B /
223,001 B, each exactly 100% of the file, with ~98 KB of paid-for budget left
unspent. `dependency_map.md` (1,755,045 B) vanished from 00_AGENT_BRIEF,
01_ARCHITECTURE_MAP and 02_COMPONENT_INVENTORY; `data_flow_map.md` (223,001 B)
from 03_DATA_FLOWS.

The second half of the same defect: a fully-dropped file was `continue`d out of
`files` entirely. The explanatory NOTE was written only on a PARTIAL keep, so
the writer saw no trace that the evidence existed while the prompt still told it
to prioritise that artifact -- "did not fit" was indistinguishable from "does
not exist".

The SECTIONED allocator (`_plan_sectioned`) had the same two defects in a
different shape, reproduced by the tests at the bottom of this file. Its
segments are attribution boundaries with no size cap, so a heading-less
deterministic fact map is ONE block of the whole file: `seg.size_bytes <=
leftover` is all-or-nothing and a 299,943 B `dependency_map.md` was dropped
100% from EVERY part while part 1 still had 261,962 B of unused capacity. No
part named the file, so the same "did not fit" / "does not exist" confusion
applied -- but only when the source reached NO part. Absence from ONE part is
correct and deliberate there (the plan partitions evidence by group on purpose),
which is why the accounting is whole-plan and is pinned as such below.
"""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.phase3_slices import (
    Phase3PlanInputs,
    Phase3TaskPlan,
    plan_phase3_document,
)

# Sized so `budget = (hard_tokens - prompt_tokens - 1024) * 4` lands on the
# ~470 KB of the production run this test reproduces.
_PROMPT = "P" * 40_000  # -> prompt_tokens = 10_000
_HARD_TOKENS = 128_524
_SOFT_TOKENS = 80_000
_BUDGET_BYTES = (_HARD_TOKENS - (len(_PROMPT) // 4) - 1024) * 4  # 470_000


def _plan_inputs() -> Phase3PlanInputs:
    """A fixed-shape document: no unit scoping -> the single-call path."""
    return Phase3PlanInputs(
        doc_name="01_ARCHITECTURE_MAP",
        expected_output_tokens=12_000,
        single_call_output_tokens=16_000,
        required_sections=("Topology", "Entry Points", "Dependencies"),
        unit_scoped_sections=(),
        units=(("Job_000", "G00"),),
    )


def _run(staging: Path, context_files: list[Path]) -> Phase3TaskPlan:
    plan = plan_phase3_document(
        staging,
        phase="phase3",
        target="01_ARCHITECTURE_MAP",
        prompt=_PROMPT,
        context_files=context_files,
        hard_tokens=_HARD_TOKENS,
        soft_tokens=_SOFT_TOKENS,
        shared_file_max_bytes=16_000,
        inputs=_plan_inputs(),
    )
    assert plan is not None
    assert plan.mode == "single"
    assert len(plan.slices) == 1
    return plan


def _sectioned(path: Path, *, total_bytes: int, section_bytes: int) -> Path:
    """A rank-0/rank-1 file that packs tightly (many small H2 sections)."""
    line = "component detail row with enough text to be representative\n"
    per_section = max(1, section_bytes // len(line))
    sections = max(1, total_bytes // (per_section * len(line)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"## Section {i:03d}\n\n{line * per_section}\n" for i in range(sections)),
        encoding="utf-8",
    )
    return path


def _heading_less(path: Path, size_bytes: int) -> Path:
    """A deterministic fact map with NO markdown headings at all.

    `dependency_map.md` is generated as a flat edge list, so `segment_markdown`
    finds no H2/H3 boundary and falls straight through to the line splitter --
    which is exactly why the segment size is the only thing standing between it
    and a 100% drop.
    """
    line = "AssetCheck_GetLocation -> tMSSqlOutput_1 -> STAGING.T_LOCATION\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line * (size_bytes // len(line)), encoding="utf-8")
    return path


def _production_shape(root: Path) -> list[Path]:
    """Rank 0 + rank 1 fill ~372 KB, leaving ~98 KB for a 1.7 MB rank-2 map."""
    doc_pack = _sectioned(
        root / "_analysis" / "_doc_pack_01_ARCHITECTURE_MAP.md",
        total_bytes=300_000,
        section_bytes=10_000,
    )
    obligations = _sectioned(
        root / "obligations.md", total_bytes=72_000, section_bytes=8_000
    )
    dependency_map = _heading_less(root / "dependency_map.md", 1_755_045)
    return [doc_pack, obligations, dependency_map]


def _plan_bytes(plan: Phase3TaskPlan) -> int:
    return sum(p.stat().st_size for p in plan.slices[0].files)


def _by_stem(plan: Phase3TaskPlan, needle: str) -> list[Path]:
    return [p for p in plan.slices[0].files if needle in p.name]


def test_late_low_priority_fact_map_keeps_a_prefix_instead_of_a_whole_file_drop(tmp_path):
    """The verified 100% drop: adaptive segments must salvage a real prefix.

    With a fixed `budget // 4` segment size nothing of `dependency_map.md`
    could ever fit, and the leftover budget was simply never spent.
    """
    context_files = _production_shape(tmp_path / "src")
    plan = _run(tmp_path / "docs_staging", context_files)

    bounded = _by_stem(plan, "__bounded_dependency_map.md")
    assert bounded, (
        "dependency_map.md contributed nothing; the whole-file drop is back. "
        f"files={[p.name for p in plan.slices[0].files]}"
    )
    kept = bounded[0].read_text(encoding="utf-8")
    kept_bytes = len(kept.encode("utf-8"))
    assert kept_bytes > 50_000, f"only {kept_bytes} B of the fact map survived"
    # It is a real prefix of the artifact, not a summary Python invented.
    body = kept.split("\n\n", 1)[1]
    assert context_files[2].read_text(encoding="utf-8").startswith(body[:4_000])

    # The budget is actually spent now: the old path left ~98 KB unused.
    used = _plan_bytes(plan)
    assert used <= _BUDGET_BYTES
    assert used > _BUDGET_BYTES * 0.95, (
        f"only {used} of {_BUDGET_BYTES} budget bytes used"
    )


def test_a_file_that_cannot_contribute_becomes_an_omission_note(tmp_path):
    """A full drop must stay visible in `files`, never vanish silently."""
    root = tmp_path / "src"
    # Rank 0 alone consumes the budget; the rank-3 non-code file that follows is
    # one unsplittable line far larger than whatever is left.
    doc_pack = _sectioned(
        root / "_analysis" / "_doc_pack_01_ARCHITECTURE_MAP.md",
        total_bytes=520_000,
        section_bytes=10_000,
    )
    noncode = root / "noncode_context.md"
    noncode.parent.mkdir(parents=True, exist_ok=True)
    noncode.write_text("x" * 40_000, encoding="utf-8")  # single line, no newline

    plan = _run(tmp_path / "docs_staging", [doc_pack, noncode])

    omitted = _by_stem(plan, "__omitted_noncode_context.md")
    assert omitted, (
        "a fully-dropped file disappeared from the context; the writer cannot "
        "tell 'did not fit' from 'does not exist'. "
        f"files={[p.name for p in plan.slices[0].files]}"
    )
    note = omitted[0].read_text(encoding="utf-8")
    assert "noncode_context.md" in note          # names the file
    assert str(noncode.stat().st_size) in note   # states its byte size
    assert "artifact of record" in note          # points at the on-disk source
    assert "absent from THIS CONTEXT" in note
    # The note is a bounded marker, not a stand-in for the evidence.
    assert len(note.encode("utf-8")) < 1_000
    # And it is still accounted for as trimmed, not quietly forgotten.
    assert "noncode_context.md" in plan.trimmed_sources
    assert plan.trimmed_bytes >= noncode.stat().st_size


def test_trim_accounting_matches_the_bytes_actually_left_out(tmp_path):
    """kept + trimmed_bytes reconstructs each trimmed source exactly."""
    context_files = _production_shape(tmp_path / "src")
    plan = _run(tmp_path / "docs_staging", context_files)

    dependency_map = context_files[2]
    assert plan.trimmed_sources == ("dependency_map.md",)

    bounded = _by_stem(plan, "__bounded_dependency_map.md")[0]
    note, body = bounded.read_text(encoding="utf-8").split("\n\n", 1)
    assert note.startswith("> NOTE:")
    kept_bytes = len(body.encode("utf-8"))
    assert kept_bytes + plan.trimmed_bytes == dependency_map.stat().st_size


def test_notes_are_paid_for_out_of_the_same_budget(tmp_path):
    """Adding NOTE bytes must not push the packet past the hard ceiling."""
    root = tmp_path / "src"
    # Several files trim at once, so several notes are written.
    files = [
        _sectioned(
            root / "_analysis" / "_doc_pack_01_ARCHITECTURE_MAP.md",
            total_bytes=400_000,
            section_bytes=10_000,
        ),
        _sectioned(root / "obligations.md", total_bytes=120_000, section_bytes=8_000),
        _heading_less(root / "dependency_map.md", 400_000),
        _heading_less(root / "data_flow_map.md", 223_001),
        _sectioned(root / "noncode_context.md", total_bytes=60_000, section_bytes=5_000),
    ]
    plan = _run(tmp_path / "docs_staging", files)

    assert _plan_bytes(plan) <= _BUDGET_BYTES, "notes blew the context ceiling"
    # Every input is still represented, trimmed or not.
    represented = " ".join(p.name for p in plan.slices[0].files)
    for original in files:
        assert original.stem in represented, f"{original.name} vanished"


def test_plan_is_byte_stable_for_identical_inputs(tmp_path):
    """Prompt caching depends on the packet being reproducible byte-for-byte."""
    context_files = _production_shape(tmp_path / "src")

    first = _run(tmp_path / "staging_a", context_files)
    second = _run(tmp_path / "staging_b", context_files)

    assert [p.name for p in first.slices[0].files] == [
        p.name for p in second.slices[0].files
    ]
    for a, b in zip(first.slices[0].files, second.slices[0].files, strict=True):
        assert a.read_bytes() == b.read_bytes(), f"{a.name} is not byte-stable"
    assert first.trimmed_bytes == second.trimmed_bytes
    assert first.trimmed_sources == second.trimmed_sources


def test_high_priority_files_still_fill_first_and_whole(tmp_path):
    """The adaptive segment size must not disturb the priority order.

    Rank 0/1 keep their originals (cache-stable); only the rank-2 map is cut.
    """
    context_files = _production_shape(tmp_path / "src")
    plan = _run(tmp_path / "docs_staging", context_files)

    files = plan.slices[0].files
    assert context_files[0] in files, "the doc pack must be kept whole and unmodified"
    assert context_files[1] in files, "obligations must be kept whole and unmodified"
    assert context_files[2] not in files, "the fact map should have been trimmed"
    # Original context order is preserved.
    assert [p.name for p in files][:2] == [
        context_files[0].name,
        context_files[1].name,
    ]


# ---------------------------------------------------------------------------
# The sectioned allocator (`_plan_sectioned`): same two defects, and the one
# behaviour that must NOT be "fixed" -- a source legitimately absent from a
# single part because the plan assigned its bytes to a sibling part.
# ---------------------------------------------------------------------------

_UNITS_PER_GROUP = 5
_GROUP_EVIDENCE_BYTES = 40_000


def _sectioned_units(count: int) -> tuple[tuple[str, str], ...]:
    return tuple(
        (f"Job_{i:03d}", f"G{i // _UNITS_PER_GROUP:02d}") for i in range(count)
    )


def _doc_pack(root: Path, units: tuple[tuple[str, str], ...]) -> Path:
    """A doc pack shaped like `harness._doc_evidence_pack_text`.

    Small named H2 sections (the shared skeleton), then bulk per-group Phase 2
    evidence under `### Source Analysis: {group}.md` markers -- the markers
    `_attribute_blocks` uses to assign a block to a group.
    """
    groups: list[str] = []
    for _, group in units:
        if group not in groups:
            groups.append(group)
    unit_index = "\n".join(f"- {name} (group {group})" for name, group in units)
    parts = [
        "## Shared Project Scope\n\nOne partition, frozen scan.\n",
        f"## All Groups And Units\n\n{unit_index}\n",
        "## Doc-Specific Phase 2 Evidence\n\n",
    ]
    for group in groups:
        line = f"evidence line for {group}\n"
        body = line * (_GROUP_EVIDENCE_BYTES // len(line))
        parts.append(f"### Source Analysis: {group}.md\n\n{body}\n")
    path = root / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _run_sectioned(
    staging: Path, context_files: list[Path], units: tuple[tuple[str, str], ...]
) -> Phase3TaskPlan:
    unit_scoped = ("Canonical Unit Inventory", "Source Artifacts")
    plan = plan_phase3_document(
        staging,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt=_PROMPT,
        context_files=context_files,
        hard_tokens=_HARD_TOKENS,
        soft_tokens=_SOFT_TOKENS,
        shared_file_max_bytes=16_000,
        inputs=Phase3PlanInputs(
            doc_name="02_COMPONENT_INVENTORY",
            expected_output_tokens=48_000,  # 3 x the single-call cap
            single_call_output_tokens=16_000,
            required_sections=(*unit_scoped, "Dependencies"),
            unit_scoped_sections=unit_scoped,
            units=units,
        ),
    )
    assert plan is not None
    assert plan.mode == "sections", f"expected a partitioned plan, got {plan.mode}"
    assert len(plan.slices) >= 2
    return plan


def _slice_text(plan: Phase3TaskPlan, idx: int) -> str:
    return "".join(
        p.read_text(encoding="utf-8", errors="replace") for p in plan.slices[idx].files
    )


def _plan_text(plan: Phase3TaskPlan) -> str:
    return "".join(_slice_text(plan, i) for i in range(len(plan.slices)))


def test_unattributed_fact_map_is_not_dropped_whole_from_the_sectioned_plan(tmp_path):
    """The sectioned analogue of the 100% drop.

    `dependency_map.md` carries no markdown headings, so `_attribute_blocks`
    yields ONE block of the entire file with no group. It is offered only to
    part 1's leftover capacity, and a whole-block fit test can never accept it
    -- it vanished from the plan while part 1 had ~260 KB going unspent.
    """
    root = tmp_path / "src"
    units = _sectioned_units(120)
    doc_pack = _doc_pack(root, units)
    dependency_map = _heading_less(root / "dependency_map.md", 300_000)

    plan = _run_sectioned(tmp_path / "docs_staging", [doc_pack, dependency_map], units)

    original = dependency_map.read_text(encoding="utf-8")
    part_one = _slice_text(plan, 0)
    assert original[:4_000] in part_one, (
        "dependency_map.md contributed nothing to any part; the whole-file drop "
        "is back"
    )
    kept = plan.slices[0].files[-1].stat().st_size
    assert kept > 200_000, f"only {kept} B of part 1 evidence survived"
    # Verbatim: Python re-packs bytes, it never summarises.
    assert original[:100_000] in part_one


def test_a_source_absent_from_every_part_becomes_an_omission_note(tmp_path):
    """A whole-plan drop must stay visible, in every part.

    The single-line non-code file cannot be split below its own size, so no
    prefix can be salvaged -- the only honest outcome is a marker. It rides in
    the shared prefix of EVERY part because any part's writer could otherwise
    assert an absence the artifact of record contradicts.
    """
    root = tmp_path / "src"
    units = _sectioned_units(120)
    doc_pack = _doc_pack(root, units)
    noncode = root / "noncode_context.md"
    noncode.write_text("x" * 200_000, encoding="utf-8")  # one unsplittable line

    plan = _run_sectioned(tmp_path / "docs_staging", [doc_pack, noncode], units)

    assert "x" * 1_000 not in _plan_text(plan), "the fixture no longer drops the file"
    for idx in range(len(plan.slices)):
        names = [p.name for p in plan.slices[idx].files]
        assert any("__omitted_evidence.md" in n for n in names), (
            f"part {idx + 1} carries no omission marker; the writer cannot tell "
            f"'did not fit' from 'does not exist'. files={names}"
        )
        note = _slice_text(plan, idx)
        assert "noncode_context.md" in note                    # names the file
        assert str(noncode.stat().st_size) in note             # states its size
        assert "artifact of record" in note                    # points at the source
        assert "absent from THIS CONTEXT" in note
        assert "omitted IN FULL from EVERY part" in note

    assert "noncode_context.md" in plan.trimmed_sources
    assert plan.trimmed_bytes >= 200_000 - 64

    marker = next(p for p in plan.slices[0].files if "__omitted_evidence.md" in p.name)
    assert marker.stat().st_size < 2_000, "the marker is a note, not a stand-in"


def test_evidence_spread_over_parts_is_not_reported_as_omitted(tmp_path):
    """Absence from ONE part is CORRECT and must not raise a marker.

    A section-partitioned plan distributes group evidence deliberately: every
    group's bytes live in exactly one part. Nothing is trimmed, so there is
    nothing to report -- treating a per-part absence as a defect would put a
    false omission notice in front of every writer.
    """
    root = tmp_path / "src"
    units = _sectioned_units(120)
    doc_pack = _doc_pack(root, units)

    plan = _run_sectioned(tmp_path / "docs_staging", [doc_pack], units)

    assert plan.trimmed_bytes == 0
    assert plan.trimmed_sources == ()
    for sliced in plan.slices:
        assert not [p for p in sliced.files if "__omitted" in p.name], (
            "a fully-distributed plan raised an omission marker"
        )
    # Disjoint: each group's evidence appears in exactly one part.
    groups = sorted({group for _, group in units})
    for group in groups:
        marker = f"### Source Analysis: {group}.md"
        holders = [
            i for i in range(len(plan.slices)) if marker in _slice_text(plan, i)
        ]
        assert len(holders) == 1, f"{group} evidence landed in parts {holders}"


def test_sectioned_notes_are_paid_for_out_of_the_same_ceiling(tmp_path):
    """Omission notes ride in every part, so their bytes are reserved up front."""
    root = tmp_path / "src"
    units = _sectioned_units(120)
    files = [
        _doc_pack(root, units),
        _heading_less(root / "dependency_map.md", 300_000),
        _heading_less(root / "data_flow_map.md", 223_001),
    ]
    noncode = root / "noncode_context.md"
    noncode.write_text("x" * 200_000, encoding="utf-8")
    files.append(noncode)

    plan = _run_sectioned(tmp_path / "docs_staging", files, units)

    prompt_tokens = len(_PROMPT) // 4
    for idx, sliced in enumerate(plan.slices, start=1):
        slice_bytes = sum(p.stat().st_size for p in sliced.files)
        assert prompt_tokens + slice_bytes // 4 <= _HARD_TOKENS, (
            f"part {idx} exceeds the context ceiling ({slice_bytes} evidence bytes)"
        )
    # Every input is still represented somewhere -- carried or named.
    text = _plan_text(plan)
    for original in files:
        assert original.name in text, f"{original.name} vanished from the plan"


def test_sectioned_plan_is_byte_stable_for_identical_inputs(tmp_path):
    """Prompt caching holds across parts only if the packet is reproducible."""
    root = tmp_path / "src"
    units = _sectioned_units(120)
    context_files = [
        _doc_pack(root, units),
        _heading_less(root / "dependency_map.md", 300_000),
    ]

    first = _run_sectioned(tmp_path / "staging_a", context_files, units)
    second = _run_sectioned(tmp_path / "staging_b", context_files, units)

    assert len(first.slices) == len(second.slices)
    for a_slice, b_slice in zip(first.slices, second.slices, strict=True):
        assert [p.name for p in a_slice.files] == [p.name for p in b_slice.files]
        for a, b in zip(a_slice.files, b_slice.files, strict=True):
            assert a.read_bytes() == b.read_bytes(), f"{a.name} is not byte-stable"
        assert a_slice.instruction == b_slice.instruction
    assert first.trimmed_bytes == second.trimmed_bytes
    assert first.trimmed_sources == second.trimmed_sources
