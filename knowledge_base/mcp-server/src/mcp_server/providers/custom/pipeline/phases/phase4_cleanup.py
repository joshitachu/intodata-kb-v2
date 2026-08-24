"""Phase 4: Cleanup — post-generation artifact cleanup and staging finalisation."""
from __future__ import annotations

import json
import logging
import shutil
import time
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

        for directory in ("_preprocess", "_analysis", "_transient"):
            target = ctx.staging_dir / directory
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        # Connector providers may stage redacted working excerpts only beneath
        # this private transient area. Keep the accepted claim links and audit
        # receipts, but never publish or retain event/provider payloads by
        # default. This is deliberately independent of provider type.
        for directory in (
            "_connector_evidence/_provider_items",
            "_connector_evidence/_excerpts",
        ):
            target = ctx.staging_dir / directory
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        from pathlib import Path as _Path
        logs_dir = _Path(ctx.logs_abs)
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
