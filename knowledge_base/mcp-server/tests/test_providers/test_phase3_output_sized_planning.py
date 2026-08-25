"""Output-sized Phase 3 document planning (slicing redesign) and its wiring.

Verified failure this covers (werkorder run of record): every Phase 3 document
whose context exceeded the hard ceiling was planned by the R2.5 INPUT
partitioner, which pages the evidence but leaves the prompt byte-stable. That
prompt is the whole-document contract ("MANDATORY HEADINGS ... a response
missing any heading is REJECTED" -- data/prompt_templates/phase3_doc.md), so
every slice restarted the whole document from its own evidence page: 4-5 full
restarts per document, glued together by a lossy dedupe merge. Measured result:
01_ARCHITECTURE_MAP ~47K output tokens over 4 slices, 04_BUSINESS_RULES ~59K
over 5, with repeated headings, restated inventories and per-slice repetition of
the same "evidence is limited" caveats.

``plan_phase3_document`` fixes the shape (one call when the expected OUTPUT
fits; disjoint per-unit assignments when it does not). It only ever runs if the
doc-write dispatch seeds it with ``phase3_inputs`` -- which is exactly what was
missing, so the planner was unreachable and the old path ran every time.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.custom import harness as harness_module
from mcp_server.providers.custom.phase3_slices import (
    Phase3PlanInputs,
    plan_phase3_document,
)

_HARD_TOKENS = 120_000
_SOFT_TOKENS = 80_000


def _units(count: int, per_group: int = 5) -> tuple[tuple[str, str], ...]:
    return tuple(
        (f"Job_{i:03d}", f"G{i // per_group:02d}") for i in range(count)
    )


def _write_doc_pack_context(
    staging: Path, units: tuple[tuple[str, str], ...], *, evidence_bytes_per_group: int
) -> list[Path]:
    """A doc pack shaped like `harness._doc_evidence_pack_text` output.

    Small named H2 sections first (these become the shared skeleton), then the
    bulk per-group Phase 2 evidence under `### Source Analysis: {group}.md`
    markers, which is what `_plan_sectioned` attributes to groups.
    """
    staging.mkdir(parents=True, exist_ok=True)
    analysis = staging / "_analysis"
    analysis.mkdir(exist_ok=True)

    groups: list[str] = []
    for _, group in units:
        if group not in groups:
            groups.append(group)

    unit_index = "\n".join(f"- {name} (group {group})" for name, group in units)
    parts = [
        "## Shared Project Scope\n\nOne partition, frozen scan.\n",
        f"## All Groups And Units\n\n{unit_index}\n",
        "## Cross-Group Reference Index\n\n- none observed\n",
        "## Doc-Specific Phase 2 Evidence\n\n",
    ]
    for group in groups:
        group_units = [n for n, g in units if g == group]
        body = "\n".join(
            f"- {name} reads table T_{name} and writes STAGING. " + "detail " * 40
            for name in group_units
        )
        filler_line = f"evidence line for {group}\n"
        repeats = max(1, evidence_bytes_per_group // len(filler_line))
        filler = "\n\n" + filler_line * repeats
        parts.append(f"### Source Analysis: {group}.md\n\n{body}{filler}\n")

    doc_pack = analysis / "_doc_pack_02_COMPONENT_INVENTORY.md"
    doc_pack.write_text("\n".join(parts), encoding="utf-8")

    small = staging / "cross_project_refs.md"
    small.write_text("## Cross Project Refs\n\n- none\n", encoding="utf-8")
    return [small, doc_pack]


def test_phase3_doc_dispatch_seeds_the_output_sized_plan():
    """The doc-write dispatch must pass ``phase3_inputs``.

    Without it ``_complete_core_generation`` cannot size the plan by expected
    output and silently falls back to whole-document restarts. This is the
    ONLY phase3 doc-write call site, so nothing else can seed the plan -- and
    the failure mode is invisible at runtime (no error, just repetitive docs),
    which is why it needs a static guard.
    """
    source = inspect.getsource(harness_module.CustomHarnessProvider._phase3_docs)
    assert "phase3_inputs=self._phase3_plan_inputs(" in source, (
        "Phase 3 doc dispatch no longer seeds the output-sized planner; "
        "every over-ceiling document will revert to N whole-document restarts."
    )


def test_phase3_plan_inputs_are_built_from_the_catalog_and_manifest():
    """The seed carries what the planner needs: sections, unit-scoping, units."""
    signature = inspect.signature(
        harness_module.CustomHarnessProvider._complete_core_generation
    )
    assert "phase3_inputs" in signature.parameters

    plan_inputs = Phase3PlanInputs(
        doc_name="01_ARCHITECTURE_MAP",
        expected_output_tokens=config.docgen_doc_expected_output_tokens,
        single_call_output_tokens=config.phase3_single_call_output_tokens,
        required_sections=("Topology", "Entry Points", "Dependencies", "Boundaries"),
        unit_scoped_sections=(),
        units=_units(43),
    )
    # A fixed-shape document is expected well under the single-call output cap.
    assert plan_inputs.expected_output_tokens <= plan_inputs.single_call_output_tokens


def test_fixed_shape_document_is_written_in_one_call(tmp_path):
    """No unit-scoped sections -> one writer, no restarts, no merge."""
    staging = tmp_path / "docs_staging"
    units = _units(43)
    context_files = _write_doc_pack_context(staging, units, evidence_bytes_per_group=9_000)

    plan = plan_phase3_document(
        staging,
        phase="phase3",
        target="01_ARCHITECTURE_MAP",
        prompt="P" * 40_000,
        context_files=context_files,
        hard_tokens=_HARD_TOKENS,
        soft_tokens=_SOFT_TOKENS,
        shared_file_max_bytes=config.phase3_shared_context_file_bytes,
        inputs=Phase3PlanInputs(
            doc_name="01_ARCHITECTURE_MAP",
            expected_output_tokens=12_000,
            single_call_output_tokens=16_000,
            required_sections=("Topology", "Entry Points", "Dependencies", "Boundaries"),
            unit_scoped_sections=(),
            units=units,
        ),
    )

    assert plan is not None
    assert plan.mode == "single"
    assert len(plan.slices) == 1
    assert plan.slices[0].instruction is None


def test_large_inventory_partitions_into_disjoint_unit_assignments(tmp_path):
    """A genuinely large document splits the TASK, not just the evidence.

    Each part is assigned its own units and told to write ONLY its sections;
    part 1 alone owns the title, the TOC and the cross-cutting sections. That
    is what removes the restart duplication -- the old path gave every slice
    the same whole-document contract.
    """
    staging = tmp_path / "docs_staging"
    units = _units(120, per_group=5)
    context_files = _write_doc_pack_context(staging, units, evidence_bytes_per_group=9_000)

    unit_scoped = ("Canonical Unit Inventory", "Source Artifacts", "Components")
    plan = plan_phase3_document(
        staging,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="P" * 40_000,
        context_files=context_files,
        hard_tokens=_HARD_TOKENS,
        soft_tokens=_SOFT_TOKENS,
        shared_file_max_bytes=config.phase3_shared_context_file_bytes,
        inputs=Phase3PlanInputs(
            doc_name="02_COMPONENT_INVENTORY",
            expected_output_tokens=48_000,  # 3 x the single-call cap
            single_call_output_tokens=16_000,
            required_sections=(*unit_scoped, "Dependencies", "Scanner-Limited Evidence"),
            unit_scoped_sections=unit_scoped,
            units=units,
        ),
    )

    assert plan is not None and plan.mode == "sections"
    assert len(plan.slices) >= 2

    instructions = [s.instruction or "" for s in plan.slices]
    # Only part 1 writes the title/TOC and the cross-cutting sections.
    assert "Table of Contents" in instructions[0]
    for later in instructions[1:]:
        assert "Do NOT write a document title, a Table of Contents" in later
        assert "Write ONLY these sections" in later

    # Unit assignments are disjoint and cover every unit exactly once.
    assigned: list[str] = []
    for instruction in instructions:
        assigned.extend(
            line.split(" (group ")[0].removeprefix("- ").strip()
            for line in instruction.splitlines()
            if line.startswith("- Job_") and " (group " in line
        )
    assert sorted(assigned) == sorted(name for name, _ in units)
    assert len(assigned) == len(set(assigned)), "a unit was assigned to two parts"

    # Every part still sees the canonical unit index, so no part can contradict
    # a sibling on project-wide totals.
    for sliced in plan.slices:
        skeleton = [p for p in sliced.files if p.name.endswith("__skeleton.md")]
        assert skeleton, "each part must carry the shared skeleton"
        assert "## All Groups And Units" in skeleton[0].read_text(encoding="utf-8")
