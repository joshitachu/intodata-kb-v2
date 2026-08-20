"""Round 2 legacy-engine fixes (R2.1-R2.6), built on the P0-P3 upgrade.

Covers:
- R2.1 true-ceiling budget enforcement + Phase 3 / topology floors, and the
  Assetcheck-shaped goal check (73 units / 17 groups fits all 10 docs +
  topology within the 250-call cap);
- R2.2 LLM scorecard cut by default + deterministic omitted-scorecard marker;
- R2.3 default evidence agents disabled on standard runs (see also
  test_evidence_agents.py);
- R2.4 per-section claim demotion (see also test_salvage_and_budget.py);
- R2.5 input-partitioned Phase 3 slicing: full unit coverage, no N x pack
  re-send, byte-stable shared skeleton, mergeable slices;
- R2.6 cache-friendly Phase 2 retry composition (byte-stable prompt+evidence
  prefix; gap feedback as a tail file).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.agent_runtime import AgentTaskRuntime
from mcp_server.providers.custom.doc_slice_merge import (
    duplicate_heading_errors,
    merge_sliced_doc_outputs,
)
from mcp_server.providers.custom.evidence_tasks import EvidenceTask
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import (
    FactsManifest,
    HarnessRunState,
    ModelOptions,
    ModelResult,
    UnitFact,
)
from mcp_server.providers.custom.phase2_claims import ClaimTaskSpec, plan_claim_tasks
from mcp_server.providers.custom.phase3_slices import plan_phase3_slices


def _facts(names: list[str], groups: dict[str, list[str]] | None = None) -> FactsManifest:
    if groups is None:
        groups = {"A": names}
    unit_to_group = {u: g for g, units in groups.items() for u in units}
    return FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=len(names),
        groups={g: len(units) for g, units in groups.items()},
        units=[UnitFact(name=n, group=unit_to_group[n]) for n in names],
    )


def _provider() -> CustomHarnessProvider:
    mp = MagicMock()
    mp.model_name = "test/model"
    mp.supports_tools = False
    return CustomHarnessProvider(mp)


def _ctx(staging: Path, groups: list[str]) -> DocGenContext:
    return DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(staging.parent / "logs"),
        preprocess_groups=groups,
        total_jobs=1,
        total_groups=len(groups),
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )


async def _noop_log(pid: str, message: str, level: str = "info") -> None:
    return None


# --------------------------------------------------------------------------- #
# R2.1 -- Assetcheck-shaped goal check: the exact failed run now completes
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_assetcheck_shaped_run_fits_all_docs_within_ceiling(tmp_path):
    """Replay the verified Assetcheck consumption profile (73 units / 17
    groups; 34 investigation + 104 phase2 calls actually consumed) against the
    new budget: every early-phase call fits the shared pool, EVERY Phase 3 doc
    call (10 docs x 4 slices) and topology call is granted, and the total
    stays within the 250 ceiling. The old enforcement blocked Phase 3 at
    180/179."""
    staging = tmp_path / "docs_staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)

    groups: dict[str, list[str]] = {}
    names: list[str] = []
    unit_idx = 0
    sizes = [8, 8, 8, 8, 6, 6, 5, 5, 4, 4, 3, 2, 2, 1, 1, 1, 1]  # 73 units / 17 groups
    for gi, count in enumerate(sizes):
        group = f"G{gi:02d}"
        units = [f"Job_{unit_idx + i:03d}" for i in range(count)]
        unit_idx += count
        groups[group] = units
        names.extend(units)
        body = "".join(
            f"## Talend Job: {u}\n\n" + ("x" * 3_000) + "\n\n" for u in units
        )
        (preprocess / f"{group}.md").write_text(
            f"# Pre-processed Scan Data: {group}\n\n{body}", encoding="utf-8"
        )
    assert len(names) == 73

    facts = _facts(names, groups)
    provider = _provider()
    provider._expand_per_job_docs(facts)
    assert len(provider._required_docs) == 10  # unit docs stay OFF by default
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, list(groups))

    plan = provider._plan_call_budget(ctx, state)
    # R2.2/R2.3 defaults: no scorecard, no evidence agents in the plan.
    assert plan["scorecard_calls"] == 0
    assert plan["evidence_agent_calls"] == 0
    assert plan["phase2_tasks"] == 73
    assert plan["ceiling"] == 250
    assert plan["phase3_floor"] == 10 * config.docgen_allowed_slices_per_doc == 40

    async def _consume(phase: str) -> bool:
        reservation, reason = await provider._reserve_model_budget(
            state, estimated_input_tokens=10, max_output_tokens=10, phase=phase
        )
        if reservation is None:
            return False
        await provider._settle_model_budget(
            state, reservation, ModelResult(success=True, text="ok")
        )
        return True

    # Observed real early-phase consumption (scorecard + agents now cut).
    for i in range(34):
        assert await _consume("investigation"), f"investigation call {i} blocked"
    for i in range(104):
        assert await _consume("phase2.claims"), f"phase2 call {i} blocked"

    # Every Phase 3 doc slice fits (the old run stubbed docs 04-09 here).
    for i in range(10 * config.docgen_allowed_slices_per_doc):
        assert await _consume("phase3"), f"phase3 call {i} blocked"
    state.planned_phase_floors.pop("phase3", None)  # _phase3_docs releases it
    for i in range(2):
        assert await _consume("business_topology"), f"topology call {i} blocked"

    assert state.budget_consumed_calls == 34 + 104 + 40 + 2  # 180 <= 250
    assert state.budget_consumed_calls <= config.docgen_max_run_calls


# --------------------------------------------------------------------------- #
# R2.2 -- scorecard gate + deterministic marker
# --------------------------------------------------------------------------- #

def test_scorecard_policy_gated_by_config(monkeypatch):
    provider = _provider()
    assert provider._phase_policy("scorecard") == "skip"  # default off
    assert provider._phase_policy("analysis") == "run"
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", True)
    assert provider._phase_policy("scorecard") == "run"


def test_omitted_scorecard_marker_satisfies_doc_contract(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = _facts(["Alpha", "Beta"])
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, ["A"])

    provider._write_omitted_scorecard_marker(ctx, state)
    marker = staging / "_assessment" / "scorecard.md"
    assert marker.exists()
    text = marker.read_text(encoding="utf-8")
    assert "SCORECARD OMITTED" in text
    assert "2 jobs" in text  # canonical inventory stated, never recomputed
    # The enterprise doc-set validator requires a scorecard file >= 50 bytes.
    assert marker.stat().st_size >= 50


# --------------------------------------------------------------------------- #
# R2.5 -- input-partitioned Phase 3 slices
# --------------------------------------------------------------------------- #

def _write_assetcheck_shaped_context(staging: Path, unit_count: int) -> list[Path]:
    """Doc-pack + noncode + obligations shaped like the verified run."""
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    units = [f"Job_{i:03d}" for i in range(unit_count)]
    pack_header = (
        "# Document Evidence Pack: 02_COMPONENT_INVENTORY\n\n"
        "## Shared Project Scope\n\n- Platform: talend\n"
        f"- Canonical inventory: {unit_count} jobs\n\n"
        "## All Groups And Units\n\n"
        + "".join(f"- {u} | group=G | type=job\n" for u in units)
        + "\n## Documentation Obligations\n\n- units.canonical_inventory\n\n"
    )
    pack_evidence = "## Doc-Specific Phase 2 Evidence\n\n" + "".join(
        f"### Source Analysis: {u}\n\n- **FACT** `{u}` reads table T_{i:03d}. "
        + ("evidence " * 300)
        + "\n\n"
        for i, u in enumerate(units)
    )
    doc_pack = analysis / "_doc_pack_02_COMPONENT_INVENTORY.md"
    doc_pack.write_text(pack_header + pack_evidence, encoding="utf-8")

    noncode = analysis / "_noncode_project_context.md"
    noncode.write_text(
        "## Documented Mappings\n\n" + ("mapping row\n" * 8_000), encoding="utf-8"
    )
    obligations = staging / "documentation_obligations.json"
    obligations.write_text(
        json.dumps({"shared": {"cat": ["term"] * 4_000}}), encoding="utf-8"
    )
    small_map = staging / "cross_project_refs.md"
    small_map.write_text("## Cross Project Refs\n\n- none\n", encoding="utf-8")
    return [noncode, obligations, doc_pack, small_map]


def test_phase3_slices_cover_all_units_without_duplication(tmp_path):
    """A 73-unit inventory pack must be fully covered: every unit's evidence
    section lands in exactly ONE slice part (input ~1x pack, not N x pack),
    the shared skeleton (with the unit index) is byte-stable in every slice,
    and each slice fits the hard ceiling."""
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    context_files = _write_assetcheck_shaped_context(staging, 73)

    hard, soft = 120_000, 80_000
    slices = plan_phase3_slices(
        staging,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="P" * 40_000,  # ~10K prompt tokens incl. system pack
        context_files=context_files,
        hard_tokens=hard,
        soft_tokens=soft,
        max_slices=4,
        shared_file_max_bytes=config.phase3_shared_context_file_bytes,
    )
    assert slices is not None and len(slices) >= 2

    part_files = [s[-1] for s in slices]
    shared_per_slice = [tuple(p.name for p in s[:-1]) for s in slices]
    # Shared prefix identical (byte-stable) across slices; skeleton included.
    assert len(set(shared_per_slice)) == 1
    skeleton = [p for p in slices[0] if p.name.endswith("__skeleton.md")]
    assert skeleton, "doc-pack header must become the shared skeleton"
    skeleton_text = skeleton[0].read_text(encoding="utf-8")
    assert "## All Groups And Units" in skeleton_text
    assert "Job_072" in skeleton_text  # unit index available to every slice

    combined = "".join(p.read_text(encoding="utf-8") for p in part_files)
    for i in range(73):
        marker = f"reads table T_{i:03d}"
        assert combined.count(marker) == 1, f"unit {i} missing or duplicated"

    shared_bytes = sum(p.stat().st_size for p in slices[0][:-1])
    for s in slices:
        slice_bytes = sum(p.stat().st_size for p in s) + 40_000
        assert slice_bytes // 4 <= hard, "slice exceeds the hard token ceiling"
    # No N x pack re-send: total input is ~1x evidence + N x shared skeleton.
    total_part_bytes = sum(p.stat().st_size for p in part_files)
    total_evidence_bytes = sum(p.stat().st_size for p in context_files if p.stat().st_size > config.phase3_shared_context_file_bytes)
    assert total_part_bytes <= total_evidence_bytes * 1.05  # headers only
    assert shared_bytes <= 40_000


def test_phase3_slices_fall_back_when_nothing_to_partition(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    small = staging / "small.md"
    small.write_text("## Tiny\n\n- fact\n", encoding="utf-8")
    assert (
        plan_phase3_slices(
            staging,
            phase="phase3",
            target="00_AGENT_BRIEF",
            prompt="p",
            context_files=[small],
            hard_tokens=120_000,
            soft_tokens=80_000,
            max_slices=4,
            shared_file_max_bytes=16_000,
        )
        is None
    )


@pytest.mark.asyncio
async def test_core_generation_partitions_phase3_input_and_merges(tmp_path, monkeypatch):
    """End-to-end through _complete_core_generation: an oversized Phase 3
    context is partitioned (each call = shared prefix + its own part), the
    slice cap does not drop evidence, and the per-slice restarts merge into
    one document without duplicate headings."""
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 4_000)
    monkeypatch.setattr(config, "core_context_soft_target_tokens", 2_500)
    monkeypatch.setattr(config, "phase3_shared_context_file_bytes", 1_000)

    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    units = [f"Job_{i:02d}" for i in range(20)]
    pack = staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md"
    pack.write_text(
        "# Document Evidence Pack\n\n## All Groups And Units\n\n"
        + "".join(f"- {u}\n" for u in units)
        + "\n## Doc-Specific Phase 2 Evidence\n\n"
        + "".join(
            f"### Source Analysis: {u}\n\n- covers {u}. " + ("e " * 500) + "\n\n"
            for u in units
        ),
        encoding="utf-8",
    )
    small = staging / "cross_project_refs.md"
    small.write_text("## Cross Refs\n\n- none\n", encoding="utf-8")

    facts = _facts(units)

    calls: list[list[str]] = []

    class _Provider:
        model_name = "fake/writer"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            names = [p.name for p in context_files or []]
            calls.append(names)
            covered = sorted(
                u for u in units
                if any(
                    f"covers {u}." in p.read_text(encoding="utf-8")
                    for p in context_files or []
                    if p.name.startswith("02_COMPONENT_INVENTORY__part_")
                )
            )
            body = "".join(f"- {u}: documented\n" for u in covered)
            return ModelResult(
                success=True,
                text=(
                    "# 02 Component Inventory\n\n## Table of Contents\n\n- Inventory\n\n"
                    f"## Inventory\n\n{body}"
                ),
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=10,
            )

    provider = _Provider()
    harness = CustomHarnessProvider(provider)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    result = await harness._complete_core_generation(
        _ctx(staging, ["A"]),
        state,
        provider=provider,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="Write the component inventory.",
        context_files=[small, pack],
        opts=ModelOptions(),
        log_cb=_noop_log,
    )

    assert result.success
    assert len(calls) >= 2
    # Byte-stable shared prefix: every call starts with the same shared files;
    # only the part file (and the tail slice instruction) differ.
    prefixes = {tuple(n for n in c if "__part_" not in n and "__instruction" not in n) for c in calls}
    assert len(prefixes) == 1
    part_names = [n for c in calls for n in c if "__part_" in n]
    assert len(set(part_names)) == len(calls)  # each slice got its OWN portion
    # No slice-cap truncation: all 20 units in the final merged document.
    assert "SLICE CAP NOTICE" not in result.text
    for u in units:
        assert f"- {u}: documented" in result.text
    assert duplicate_heading_errors(result.text) == []
    merged_again = merge_sliced_doc_outputs([result.text])
    assert merged_again.count("## Inventory") == 1


# --------------------------------------------------------------------------- #
# R2.6 -- cache-friendly Phase 2 retry composition
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_phase2_retry_keeps_prompt_and_evidence_prefix_byte_stable(tmp_path):
    """A validation-failure repair must re-send the SAME prompt and evidence
    packet bytes (provider prefix cache holds) and deliver the gap feedback
    as a transient TAIL file, not a prompt mutation."""
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    task_path = staging / "_preprocess" / "A.md"
    task_path.write_text(
        "## Talend Job: Alpha\n\n#### tMSSqlInput_1 (tMSSqlInput)\n"
        "- **QUERY**: `\"SELECT ID FROM T WHERE Code = 'X'\"`\n",
        encoding="utf-8",
    )
    facts = _facts(["Alpha"])
    task = EvidenceTask(index=1, task_id="A", path=task_path, units=["Alpha"])
    spec = plan_claim_tasks(task, evidence_bytes=100, max_output_tokens=4_000)[0]

    def _claims(section: str) -> str:
        return json.dumps({
            "schema_version": "phase2-claims-v1",
            "group": "A",
            "task_id": spec.target_id,
            "concern": spec.concern,
            "claims": [{
                "section": section,
                "kind": "fact",
                "unit_names": ["Alpha"],
                "statement": "Reads `SELECT ID FROM T WHERE Code = 'X'` for the bookmark.",
                "evidence_refs": ["A.md#tMSSqlInput_1"],
                "confidence": "supported",
            }],
        })

    attempts: list[tuple[str, list[str]]] = []

    class _Provider:
        model_name = "fake/claims"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            attempts.append((prompt, [p.name for p in context_files or []]))
            # Attempt 1: structural violation (section outside contract);
            # attempt 2: corrected.
            text = _claims("NOT_A_SECTION" if len(attempts) == 1 else "SOURCE_SQL")
            return ModelResult(
                success=True, text=text, model_name=self.model_name,
                input_tokens=10, output_tokens=10,
            )

    provider = _Provider()
    harness = CustomHarnessProvider(provider)
    harness.model_provider = provider
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    runtime = AgentTaskRuntime(staging, facts)

    batches = await harness._run_phase2_claim_spec(
        _ctx(staging, ["A"]),
        state,
        runtime,
        "A",
        spec,
        [],
        [],
        _noop_log,
        phase_name="phase2",
    )

    assert len(attempts) == 2
    prompt1, files1 = attempts[0]
    prompt2, files2 = attempts[1]
    assert prompt1 == prompt2  # byte-stable prompt head
    assert files2[: len(files1)] == files1  # evidence packet prefix unchanged
    tail = [n for n in files2 if n not in files1]
    assert len(tail) == 1 and "attempt_2" in tail[0]
    tail_path = next(
        p for p in (staging / "_transient" / "claim_repair" / "phase2").rglob("*.md")
        if p.name == tail[0]
    )
    assert "REPAIR INSTRUCTION" in tail_path.read_text(encoding="utf-8")
    assert batches and batches[0].claims[0].section == "SOURCE_SQL"
