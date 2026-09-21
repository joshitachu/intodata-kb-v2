"""Phase 4: Cleanup — post-generation artifact cleanup and staging finalisation."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_server.config import config
from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write
from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult
from mcp_server.services.extraction_cache import (
    compute_extraction_key,
    extraction_cache_dir,
    prune_extraction_cache,
    store_extraction,
    unit_allowlist_for_partition,
)
from mcp_server.services.rag_support_package import build_rag_support_package

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl

logger = logging.getLogger(__name__)


#: Staging subdirectories the atomic swap is allowed to carry into the
#: PUBLISHED tree (``output/{project_id}[/{subproject}]/docs/``).
#:
#: Publication is a whole-directory rename, so this phase is the only thing
#: between the pipeline's working set and a client-visible documentation tree.
#: This used to be a DENYLIST of three internals (``_preprocess``,
#: ``_analysis``, ``_transient``): every directory added to the pipeline after
#: that list was written shipped by default. Measured on a live deployment,
#: 2,175,241 bytes of ``_investigation/_tasks/`` were published inside
#: ``docs/``, alongside ``_model_inputs/``, ``_agent_tasks/`` and ``_debug/``.
#: An allowlist fails closed: the NEXT internal directory someone adds is
#: unpublished until it is deliberately listed here with a named consumer.
#:
#: Entry criterion: a POST-SWAP consumer, i.e. code that reads the directory
#: from ``docs/`` rather than from ``docs_staging/``. Prompt inputs, resume
#: checkpoints and forensics are not consumers -- they read staging, which is
#: gone the moment the swap succeeds.
PUBLISHED_STAGING_DIRS = frozenset(
    {
        # services/rag_support_package.py:
        #   * replace_analysis_cache_from_rag_support() rebuilds the flat
        #     analysis_cache/ from docs/_rag_support/phase2_analysis/ in
        #     _run_generate_background immediately after swap_docs(); removing
        #     this directory silently disables incremental webhook regeneration.
        #   * collect_internal_artifacts() ingests it as the technical-analysis
        #     RAG layer.
        # services/group_doc_dependency.py reads
        #   docs/_rag_support/_doc_pack_manifest.json.
        # services/true_patch_executor.py writes back into
        #   docs/_rag_support/phase2_analysis/{group}.md on the living-docs lane.
        "_rag_support",
        # api/routes/generate_runs.py: the connector-evidence endpoint reads
        #   docs/_assessment/runtime_evidence_coverage.json and discovers
        #   partitions by globbing {sub}/docs/_assessment/ for the same file.
        # api/routes/projects.py::_load_enterprise_gate_results reads
        #   _assessment/enterprise_gates.json at publish time.
        # rag_support_package.collect_internal_artifacts reads
        #   docs/_assessment/scorecard.md as the scorecard RAG layer.
        # failed_docs.json / slice_cap_flags.json consumers read the published copy.
        "_assessment",
        # rag_support_package.collect_internal_artifacts globs docs/_facts/*.md
        #   for the support-map RAG layer; build_entity_aliases() reads
        #   docs/_facts/entity_index.csv and usage_index.csv.
        "_facts",
        # rag_support_package.collect_internal_artifacts globs
        #   docs/_evidence_registry/doc_slices/*.md for the evidence-slice RAG layer.
        "_evidence_registry",
        # api/routes/projects.py::_load_published_business_topology reads
        #   docs/_topology/business_topology.json + validation_report.json;
        #   services/business_topology/{composite,evidence_merge}.py glob
        #   output/{pid}/*/docs/_topology/ to build the org-level view.
        "_topology",
        # Bounded publish-time receipts (enterprise gate report, final doc
        # hashes, connector claim linkage). Read at publish time by
        # api/routes/projects.py::_load_enterprise_gate_results as the fallback
        # when _assessment/enterprise_gates.json is absent -- which is exactly
        # what happens when this phase's safety net calls write_audit_bundle(),
        # since that writes only into _audit/. Also re-created AFTER this phase
        # by _write_plugin_pack_execution_receipt(), so deleting it here would
        # lose the fallback without preventing publication.
        "_audit",
    }
)

#: Where ``_debug/`` is parked instead of being published.
#:
#: DECISION: ``_debug/`` does NOT publish, not even with ``MCP_PIPELINE_DEBUG=1``.
#: It holds verbatim prompts, model responses, provider error text and context
#: snapshots derived from the client's own source; a client-visible ``docs/``
#: tree (also served by the doc viewer) is the wrong place for it, and
#: debug_artifacts.py states by contract that nothing in there has a runtime
#: consumer. The forensic value is not lost:
#:   * a FAILED run never reaches this phase, so staging (and ``_debug/`` in it)
#:     is preserved intact for resume/diagnosis -- the case the flag exists for;
#:   * a SUCCESSFUL run's record is moved beside the output tree, operator-side,
#:     at ``output/{project_id}[/{subproject}]/_debug/``.
#: The leading underscore keeps it invisible to services/doc_layout.py, which
#: skips ``_``-prefixed siblings when enumerating sub-projects.
DEBUG_ARCHIVE_DIRNAME = "_debug"

#: The run's project memory, staged inside docs_staging so the agent can read
#: and write it. Persisted to its durable home rather than deleted -- see
#: Phase4Cleanup._persist_memory_directory. Kept in step with
#: services/project_memory.STAGED_DIRNAME; the import is local to the method so
#: this phase module stays free of a service-layer import at module scope.
MEMORY_STAGED_DIRNAME = "_knowledge"


class Phase4Cleanup(Phase):
    def __init__(
        self,
        helpers: "HarnessHelpersImpl",
        *,
        provider_id: str = "custom",
    ) -> None:
        self._helpers = helpers
        self._provider_id = provider_id

    @property
    def name(self) -> str:
        return "phase4_cleanup"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return False

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        # The enterprise gate report is always written before this phase on the normal
        # generate() path; this guard is a safety net for direct phase invocations.
        if not (ctx.staging_dir / "_assessment" / "enterprise_gates.json").exists():
            gate_report = self._helpers.enterprise_gate_report(ctx, state)
            await self._helpers.write_audit_bundle(ctx, state, gate_report)
        archived_engineering_facts = self._helpers.archive_engineering_artifacts(ctx, state)
        index = {
            "project_id": ctx.project_id,
            "platform": state.facts_manifest.platform,
            "groups": state.facts_manifest.groups,
            "unit_count": state.facts_manifest.unit_count,
            "canonical_unit_label": state.facts_manifest.canonical_unit_label,
            "docs": self._helpers.required_docs_list,
            "engineering_facts": archived_engineering_facts,
            "provider": self._provider_id,
            "model": self._helpers.model_provider.model_name,
            "write_model": self._helpers.write_provider.model_name,
            "generated_at": time.time(),
        }
        if state.warnings:
            index["warnings"] = state.warnings
            index["warnings_count"] = len(state.warnings)
        debug_write(
            debug_dir(ctx.staging_dir, "index.json"),
            json.dumps(index, indent=2),
        )
        if state.warning_collector.has_warnings():
            debug_write(
                debug_dir(ctx.staging_dir, "_assessment", "warnings.json"),
                json.dumps(
                    {
                        "schema_version": 1,
                        "warnings": [w.model_dump() for w in state.warning_collector.warnings],
                    },
                    indent=2,
                ),
            )
        calls_json = json.dumps([c.model_dump() for c in state.calls], indent=2)
        debug_write(debug_dir(ctx.staging_dir, "provider_calls.json"), calls_json)
        debug_write(debug_dir(ctx.staging_dir, "_audit", "provider_calls.json"), calls_json)

        # Persist Phase 2 group analyses into _rag_support/ before _analysis/
        # is deleted; the atomic swap carries the package into docs/.
        package_manifest = build_rag_support_package(
            ctx.staging_dir,
            state.facts_manifest.groups,
            project_id=ctx.project_id,
            platform=state.facts_manifest.platform,
        )
        if package_manifest["missing_groups"]:
            await log_cb(
                ctx.project_id,
                f"[{self._provider_id}] RAG support package missing group analyses: "
                + ", ".join(package_manifest["missing_groups"]),
                "warning",
            )

        # Persist the pack-INDEPENDENT extraction layer BEFORE the rmtree below
        # destroys it. Swapping the pack (the "lens") must reuse the evidence;
        # the cached interpretation belongs to the OLD pack.
        #
        # Deliberately not cached, and why (recon section 4.6):
        #   * _analysis/_context_packets/ -- deterministic from the Phase 2 task
        #     evidence plus per-run byte caps (6.4M measured). Caching it buys
        #     disk, not model calls.
        #   * _analysis/_claims/, _analysis/_doc_pack_*,
        #     _analysis/_evidence_{doc}.md -- pack-scoped OUTPUTS, not
        #     extraction. Serving them to a NEW pack would replay the OLD pack's
        #     interpretation, which is exactly the defect this cache fixes.
        #   * the engineering-fact artifacts -- already archived into _facts/
        #     above and carried through the atomic swap.
        # The rmtree list itself is unchanged: staging stays the working set.
        self._store_extraction_cache(ctx)

        # The publish step is a whole-directory rename, so staging is reduced to
        # the publishable set HERE. See PUBLISHED_STAGING_DIRS for the rule and
        # the per-directory consumers. Runtime evidence is the worked example of
        # the split this enforces:
        #   * `_transient/runtime/runtime_evidence.json` (the fetched payload,
        #     carrying provider failure text) and `_analysis/_runtime_profile.md`
        #     (the rollup) are destroyed -- they are prompt inputs, not
        #     deliverables;
        #   * `_assessment/runtime_evidence_coverage.json` survives: the
        #     operator coverage endpoint reads it off the PUBLISHED tree.
        # Connector evidence follows the same rule. `_connector_evidence/` is
        # Phase-2 prompt input (the OpenCode analyze contract attaches
        # `_connector_evidence/{group}/accepted_findings.json`), and the durable
        # provenance an operator actually needs is written separately to
        # `_audit/connector_claim_linkage.json`, which is on the allowlist. The
        # old partial purge of `_provider_items/` and `_excerpts/` is therefore
        # subsumed: the whole directory, payloads included, now goes.
        self._prune_unpublished_directories(ctx.staging_dir)

        logs_dir = Path(ctx.logs_abs)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        (logs_dir / f"run_{ctx.project_id}_{ts}.log").write_text(
            f"project_id: {ctx.project_id}\n"
            f"provider: {self._provider_id}\n"
            f"model: {self._helpers.model_provider.model_name}\n"
            f"write_model: {self._helpers.write_provider.model_name}\n"
            f"unit_count: {state.facts_manifest.unit_count}\n"
            f"input_tokens: {state.input_tokens}\n"
            f"output_tokens: {state.output_tokens}\n",
            encoding="utf-8",
        )
        return PhaseResult(phase_name=self.name, success=True)

    @staticmethod
    def _prune_unpublished_directories(staging_dir: Path) -> list[str]:
        """Reduce staging to the publishable set; return the names removed.

        Only DIRECTORIES are considered. Top-level files are left alone: the
        deliverables are the top-level ``*.md`` documents, and the remaining
        top-level files are run bookkeeping (``.generation.lock`` is still held
        by the caller at this point, ``_run_manifest.json`` is the resume
        manifest) whose lifecycles are owned elsewhere.

        Symlinked directories are skipped rather than removed: ``shutil.rmtree``
        refuses a symlink anyway, and following one could reach outside the
        output tree. A symlink in staging is not something this pipeline
        creates, so it is logged rather than silently handled.
        """

        removed: list[str] = []
        try:
            entries = sorted(staging_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return removed
        for entry in entries:
            if entry.name in PUBLISHED_STAGING_DIRS:
                continue
            if entry.is_symlink():
                if entry.is_dir():
                    logger.warning(
                        "Phase 4 cleanup: refusing to publish or remove symlinked "
                        "staging directory %s", entry,
                    )
                continue
            if not entry.is_dir():
                continue
            if entry.name == DEBUG_ARCHIVE_DIRNAME:
                Phase4Cleanup._archive_debug_directory(entry)
                removed.append(entry.name)
                continue
            if entry.name == MEMORY_STAGED_DIRNAME:
                Phase4Cleanup._persist_memory_directory(entry)
                removed.append(entry.name)
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
        if removed:
            logger.info(
                "Phase 4 cleanup: removed %d unpublished staging director%s (%s)",
                len(removed),
                "y" if len(removed) == 1 else "ies",
                ", ".join(removed),
            )
        return removed

    @staticmethod
    def _persist_memory_directory(memory_dir_path: Path) -> None:
        """Copy the run's project memory to its durable home before pruning it.

        ``_knowledge/`` is what the agent learned this run: the open questions
        it could not resolve, the terminology it worked out, the decisions it
        made and why. It is staged INSIDE ``docs_staging/``, which this phase
        reduces to the publishable set, so without this the custom lane would
        delete it here -- inside ``provider.run``, before the coordinator that
        owns the copy-back ever regains control. The claude-cli and opencode
        lanes are unaffected because they have no Phase 4 of their own.

        Same contract as ``_archive_debug_directory``: the invariant is that it
        does not publish, and preserving it is the nice-to-have. A failure to
        persist therefore falls back to deletion and never fails the run.
        """

        from mcp_server.services.project_memory import persist_project_memory

        staging_dir = memory_dir_path.parent
        try:
            persist_project_memory(
                output_dir=staging_dir.parent,
                staging_dir=staging_dir,
                success=True,
            )
            logger.info(
                "Phase 4 cleanup: project memory persisted to %s",
                staging_dir.parent / "knowledge",
            )
        except Exception as exc:  # noqa: BLE001 - never fail the run over memory
            logger.warning(
                "Phase 4 cleanup: could not persist project memory from %s (%s); "
                "deleting it rather than publishing it", memory_dir_path, exc,
            )
        if memory_dir_path.exists():
            shutil.rmtree(memory_dir_path, ignore_errors=True)

    @staticmethod
    def _archive_debug_directory(debug_dir_path: Path) -> None:
        """Move ``_debug/`` out of staging, beside the output tree.

        See DEBUG_ARCHIVE_DIRNAME for why it must not publish. A failure to
        relocate falls back to deletion: not publishing is the invariant, and
        keeping the forensic copy is the nice-to-have.
        """

        destination = debug_dir_path.parent.parent / DEBUG_ARCHIVE_DIRNAME
        try:
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination, ignore_errors=True)
                else:
                    destination.unlink()
            shutil.move(str(debug_dir_path), str(destination))
            logger.info(
                "Phase 4 cleanup: debug forensics kept out of the published tree, "
                "archived to %s", destination,
            )
        except (OSError, shutil.Error) as exc:  # noqa: BLE001 - never fail the run
            logger.warning(
                "Phase 4 cleanup: could not archive %s to %s (%s); deleting it "
                "rather than publishing it", debug_dir_path, destination, exc,
            )
        finally:
            if debug_dir_path.exists():
                shutil.rmtree(debug_dir_path, ignore_errors=True)

    def _store_extraction_cache(self, ctx: "DocGenContext") -> None:
        """Persist docs_staging/_preprocess/ into the universal extraction cache.

        This is a cache: every failure is a warning, never a phase failure.

        Key inputs are taken from ``ctx`` so they match the restore side in
        ``ClaudeService.generate_docs`` byte for byte:

        * scan dir  -- ``config.intodoc_output_dir / ctx.project_id``
        * platform  -- ``ctx.platform`` (the DB platform that generate_docs also
          keys on). ``state.facts_manifest.platform`` is derived independently
          from ``scan_result.json`` and lower-cased, so keying on it could make
          the store and restore keys disagree.
        * allowlist -- rebuilt from ``ctx.partition`` via
          ``unit_allowlist_for_partition``, which mirrors generate_docs exactly.
          The partition NAME is deliberately not used: the allowlist is the
          value that actually scopes the extraction.

        Cache root is ``ctx.staging_dir.parent`` -- the partition-scoped output
        dir (``output/{project_id}[/{subproject}]``), a sibling of
        ``docs_staging/``, so it survives this phase and the atomic swap and
        partitions never share entries.
        """

        if not config.extraction_cache_enabled:
            return
        # A scoped reingestion prepares staging deliberately and may carry a
        # partial extraction layer; never promote that into the cache.
        if getattr(ctx, "scoped_docs", None):
            return
        try:
            key = compute_extraction_key(
                config.intodoc_output_dir / ctx.project_id,
                platform=ctx.platform,
                unit_allowlist=unit_allowlist_for_partition(
                    getattr(ctx, "partition", None)
                ),
            )
            if not key:
                # Empty key means DO NOT CACHE (no readable scan artifacts).
                return
            cache_root = extraction_cache_dir(ctx.staging_dir.parent)
            if store_extraction(cache_root, key, ctx.staging_dir) is None:
                return
            prune_extraction_cache(cache_root, keep=config.extraction_cache_keep)
        except Exception as exc:  # noqa: BLE001 - a cache must never fail the run
            logger.warning(
                "Extraction cache: store failed for project %s (%s)",
                ctx.project_id, exc,
            )

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=0,
            calls_max=0,
            notes="No LLM calls",
        )
