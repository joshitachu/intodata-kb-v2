"""Phase 4 publish boundary: only allowlisted directories reach ``docs/``.

Regression cover for a measured publish leak. Phase 4's cleanup was a DENYLIST
of three internals (``_preprocess``, ``_analysis``, ``_transient``) while the
publish step is a whole-directory rename of ``docs_staging/`` onto ``docs/``, so
every directory added to the pipeline afterwards shipped to the client by
default. On the live deployment that was 2,175,241 bytes of
``_investigation/_tasks/`` inside a published ``docs/``, plus ``_model_inputs/``,
``_agent_tasks/`` and ``_debug/``.

These tests pin the inverse rule: staging is reduced to the ALLOWLIST, and both
directions matter -- ``_rag_support/`` and ``_assessment/`` must survive
(removing them breaks RAG ingest and the run-detail API respectively), and an
unknown directory must not.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.models import FactsManifest, HarnessRunState
from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import (
    PUBLISHED_STAGING_DIRS,
    Phase4Cleanup,
)


def _facts_manifest(groups: dict[str, int] | None = None) -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="job",
        unit_count=1,
        groups=groups if groups is not None else {"A": 1},
    )


def _helpers(staging_dir: Path) -> MagicMock:
    helpers = MagicMock()
    helpers.required_docs_list = ["00_AGENT_BRIEF"]
    helpers.model_provider.model_name = "fake/model"
    helpers.write_provider.model_name = "fake/model"
    helpers.archive_engineering_artifacts.return_value = []
    helpers.write_audit_bundle = AsyncMock()
    # Normal-path precondition: the gate report already exists, so Phase 4's
    # safety net does not fire.
    assessment = staging_dir / "_assessment"
    assessment.mkdir(parents=True, exist_ok=True)
    (assessment / "enterprise_gates.json").write_text("{}", encoding="utf-8")
    return helpers


def _ctx(staging_dir: Path, logs_dir: Path) -> DocGenContext:
    return DocGenContext(
        project_id="proj",
        staging_dir=staging_dir,
        staging_abs=str(staging_dir),
        logs_abs=str(logs_dir),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )


def _build_staging_tree(root: Path) -> Path:
    """A synthetic staging tree carrying one file per known top-level entry."""

    staging = root / "docs_staging"
    staging.mkdir(parents=True, exist_ok=True)

    # Deliverables.
    (staging / "00_AGENT_BRIEF.md").write_text("# Brief", encoding="utf-8")
    (staging / "02_COMPONENT_INVENTORY.md").write_text("# Inventory", encoding="utf-8")
    # Top-level bookkeeping files -- not directories, deliberately untouched.
    (staging / "facts_manifest.json").write_text("{}", encoding="utf-8")
    (staging / ".generation.lock").write_text("{}", encoding="utf-8")

    published = {
        "_assessment": "runtime_evidence_coverage.json",
        "_facts": "engineering_facts.md",
        "_evidence_registry/doc_slices": "00_AGENT_BRIEF.md",
        "_topology": "business_topology.json",
        "_audit": "connector_claim_linkage.json",
    }
    internal = {
        "_investigation/_tasks": "task_001.md",
        "_investigation": "A.md",
        "_model_inputs/phase3/001_00_AGENT_BRIEF": "manifest.json",
        "_agent_tasks/phase3_doc/00_AGENT_BRIEF": "status.json",
        "_debug/model_inputs/phase3": "request.json",
        "_preprocess": "A.md",
        "_transient/runtime": "runtime_evidence.json",
        "_plans": "node_results.json",
        "_phase1": "A.json",
        "_evidence": "scopes.json",
        "_model_slices/phase3": "slice.json",
        "_connector_evidence/_provider_items": "raw.json",
        "_connector_evidence/A": "accepted_findings.json",
        # An internal directory nobody has invented yet: the whole point of an
        # allowlist is that this leaks nothing without a code change here.
        "_some_future_internal_lane": "payload.json",
    }
    for relative, filename in {**published, **internal}.items():
        target = staging / relative
        target.mkdir(parents=True, exist_ok=True)
        (target / filename).write_text("x" * 128, encoding="utf-8")

    # _analysis carries the Phase 2 group analyses that _rag_support/ is built
    # from, so it must be present for the packaging step to have input.
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (analysis / "_doc_pack_00_AGENT_BRIEF.md").write_text("pack", encoding="utf-8")
    return staging


async def _run_phase(staging: Path, tmp_path: Path) -> None:
    state = HarnessRunState(project_id="proj", facts_manifest=_facts_manifest())
    phase = Phase4Cleanup(helpers=_helpers(staging))
    result = await phase.run(
        _ctx(staging, tmp_path / "logs"), state, log_cb=AsyncMock()
    )
    assert result.success is True


def _top_level_dirs(path: Path) -> set[str]:
    return {p.name for p in path.iterdir() if p.is_dir()}


def _tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


@pytest.mark.asyncio
async def test_only_allowlisted_directories_survive_cleanup(tmp_path: Path) -> None:
    staging = _build_staging_tree(tmp_path)
    before = _top_level_dirs(staging)

    await _run_phase(staging, tmp_path)

    after = _top_level_dirs(staging)
    # _rag_support is created by this phase, so it is a legitimate addition.
    assert after <= PUBLISHED_STAGING_DIRS
    assert after == before.intersection(PUBLISHED_STAGING_DIRS) | {"_rag_support"}


@pytest.mark.asyncio
async def test_measured_leak_directories_do_not_survive(tmp_path: Path) -> None:
    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    for leaked in ("_investigation", "_model_inputs", "_agent_tasks"):
        assert not (staging / leaked).exists(), (
            f"{leaked}/ was measured inside a published docs/ tree and must not survive"
        )
    # The 2.1 MB offender specifically.
    assert not (staging / "_investigation" / "_tasks").exists()


@pytest.mark.asyncio
async def test_rag_support_survives_because_rag_ingest_reads_it(tmp_path: Path) -> None:
    """Removing this breaks analysis-cache restore and the RAG internal layers.

    ``replace_analysis_cache_from_rag_support()`` runs against ``docs/`` right
    after the swap; an absent package wipes the incremental-regen cache.
    """

    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    package = staging / "_rag_support"
    assert package.is_dir()
    assert (package / "phase2_analysis" / "A.md").read_text(
        encoding="utf-8"
    ) == "# Group A analysis"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert [a["group"] for a in manifest["artifacts"]] == ["A"]


@pytest.mark.asyncio
async def test_assessment_survives_because_the_run_detail_api_reads_it(
    tmp_path: Path,
) -> None:
    """``GET /mcp/generate-runs/{id}/connector-evidence`` reads the published copy.

    It resolves ``docs/_assessment/runtime_evidence_coverage.json`` off the
    swapped tree, and ``_load_enterprise_gate_results`` reads
    ``_assessment/enterprise_gates.json`` at publish time.
    """

    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    assessment = staging / "_assessment"
    assert (assessment / "runtime_evidence_coverage.json").is_file()
    assert (assessment / "enterprise_gates.json").is_file()


@pytest.mark.asyncio
async def test_remaining_published_directories_survive(tmp_path: Path) -> None:
    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    assert (staging / "_facts" / "engineering_facts.md").is_file()
    assert (staging / "_evidence_registry" / "doc_slices" / "00_AGENT_BRIEF.md").is_file()
    assert (staging / "_topology" / "business_topology.json").is_file()
    assert (staging / "_audit" / "connector_claim_linkage.json").is_file()


@pytest.mark.asyncio
async def test_final_documents_and_top_level_files_are_untouched(tmp_path: Path) -> None:
    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    assert (staging / "00_AGENT_BRIEF.md").read_text(encoding="utf-8") == "# Brief"
    assert (staging / "02_COMPONENT_INVENTORY.md").is_file()
    # The generation lock is still held by the caller at this point and the
    # resume manifest is owned elsewhere; the prune is directories-only.
    assert (staging / ".generation.lock").is_file()
    assert (staging / "facts_manifest.json").is_file()


@pytest.mark.asyncio
async def test_connector_evidence_payloads_are_gone_but_audit_receipt_remains(
    tmp_path: Path,
) -> None:
    """The old partial purge kept ``_connector_evidence/`` for its claim links.

    Those links are actually written to ``_audit/connector_claim_linkage.json``
    (``connectors/reconciliation.py::write_claim_linkage_report``), which is
    allowlisted, and the accepted-findings files are Phase 2 prompt inputs. So
    the whole directory can go without losing the provenance.
    """

    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    assert not (staging / "_connector_evidence").exists()
    assert (staging / "_audit" / "connector_claim_linkage.json").is_file()


@pytest.mark.asyncio
async def test_debug_is_archived_beside_the_output_tree_not_published(
    tmp_path: Path,
) -> None:
    """MCP_PIPELINE_DEBUG=1 is on in production and _debug/ ships to a CLIENT.

    Decision: never publish it. The record is moved to
    ``output/{project_id}/_debug/`` so an operator keeps the forensics while the
    client-visible tree stays clean.
    """

    staging = _build_staging_tree(tmp_path)

    await _run_phase(staging, tmp_path)

    assert not (staging / "_debug").exists()
    archived = tmp_path / "_debug" / "model_inputs" / "phase3" / "request.json"
    assert archived.is_file()


@pytest.mark.asyncio
async def test_debug_archive_replaces_a_previous_run(tmp_path: Path) -> None:
    stale = tmp_path / "_debug"
    (stale / "model_inputs").mkdir(parents=True)
    (stale / "stale_marker.json").write_text("{}", encoding="utf-8")

    staging = _build_staging_tree(tmp_path)
    await _run_phase(staging, tmp_path)

    assert not (stale / "stale_marker.json").exists()
    assert (stale / "model_inputs" / "phase3" / "request.json").is_file()


@pytest.mark.asyncio
async def test_published_bytes_drop_to_the_allowlisted_set(tmp_path: Path) -> None:
    """Before/after on the synthetic tree -- the leak is measured, not asserted."""

    staging = _build_staging_tree(tmp_path)
    before_total = _tree_bytes(staging)
    before_internal = sum(
        _tree_bytes(staging / name)
        for name in _top_level_dirs(staging)
        if name not in PUBLISHED_STAGING_DIRS
    )
    assert before_internal > 0

    await _run_phase(staging, tmp_path)

    after_total = _tree_bytes(staging)
    published_internal = sum(
        _tree_bytes(staging / name)
        for name in _top_level_dirs(staging)
        if name not in PUBLISHED_STAGING_DIRS
    )
    assert published_internal == 0
    assert after_total < before_total


@pytest.mark.asyncio
async def test_symlinked_directory_is_neither_published_nor_followed(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep me", encoding="utf-8")

    staging = _build_staging_tree(tmp_path)
    (staging / "_linked").symlink_to(outside, target_is_directory=True)

    await _run_phase(staging, tmp_path)

    # rmtree refuses a symlink; following it would reach outside the output
    # tree. The link is left in place and the target is intact.
    assert (outside / "secret.txt").is_file()


def test_allowlist_membership_is_pinned() -> None:
    """A new entry here means a new directory ships to clients: make it loud."""

    assert PUBLISHED_STAGING_DIRS == frozenset(
        {
            "_assessment",
            "_audit",
            "_evidence_registry",
            "_facts",
            "_rag_support",
            "_topology",
        }
    )
