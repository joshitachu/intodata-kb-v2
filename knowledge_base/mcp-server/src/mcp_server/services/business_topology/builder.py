"""Provider-agnostic business topology build (Pipeline + Strategy).

One pipeline, every engine. Until this module existed the three topology lanes
lived inside the custom harness's Phase 3.7 class, coupled to its model-call
plumbing, so the canonical Claude CLI provider and the OpenCode transport --
which both publish through :mod:`mcp_server.services.claude_service` -- shipped
no business topology at all.

Pipeline, in order (unchanged from Phase 3.7):

1. **Flow extraction** (Stage A + B): the model extracts business-language flows
   from this run's own finished documents, Python assembles the overlay, the
   scanner floor is merged in, and claim-local defects are pruned.
2. **Single-shot synthesis**: only when lane 1 produced nothing publishable.
3. **Deterministic floor**: evidence-only, no model, when both model lanes fail.
4. **Deterministic enrichment** (:mod:`enrichment`): fills fields from the scan,
   runtime evidence and the documents. Advisory -- an enrichment that breaks
   validation is discarded and the unenriched topology publishes.
5. **Validation** against the exact payload that will be served.

Strategy: the only engine-specific piece is how a prompt becomes text. Each
engine supplies a :class:`TopologyLLM`; ``llm=None`` runs the deterministic
lanes only and says so in ``state.json`` (``model_lanes``), so a floor-only
topology is never mistaken for a model lane that tried and failed.

Nothing here raises into the caller: a topology failure must never fail a
documentation run. The caller receives a :class:`TopologyBuildResult` and
writes it with :meth:`TopologyBuildResult.write`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from mcp_server.services.business_topology.evidence import (
        BusinessTopologyEvidenceBundle,
    )
    from mcp_server.services.business_topology.models import BusinessTopology
    from mcp_server.services.business_topology.validation import (
        BusinessTopologyValidationReport,
    )

logger = logging.getLogger(__name__)

#: ``purpose`` values handed to :meth:`TopologyLLM.complete`. They are also the
#: target-id prefixes, which the custom harness persists agent tasks under, so
#: they must not change (a resume keys on them).
PURPOSE_FLOW_EXTRACTION = "flow_extraction"
PURPOSE_SINGLE_SHOT = "business_topology"

LogFn = Callable[[str, str], Awaitable[None]]
ValidateFn = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class TopologyCompletion:
    """Text a strategy produced for one lane, plus the model that produced it."""

    text: str
    model_name: str = ""


class TopologyLLM(Protocol):
    """The engine-specific strategy: turn one prompt into one completion.

    ``validate`` is the lane's own acceptance check. A strategy that can repair
    (the custom harness's agent-task runtime) uses its ``errors`` as retry
    feedback; a strategy that cannot may ignore it. Either way the strategy
    returns its LAST completion even when validation still fails -- the
    pipeline re-parses and prunes, so a rejected-but-rich answer can still
    publish its valid claims. Return ``None`` only when there is no text at all
    (transport failure, budget exhausted).
    """

    async def complete(
        self,
        prompt: str,
        *,
        context_files: list[Path],
        purpose: str,
        target_id: str,
        validate: ValidateFn,
    ) -> TopologyCompletion | None: ...


@dataclass(frozen=True)
class TopologyBuildInputs:
    """Everything the pipeline needs, independent of which engine ran."""

    project_id: str
    staging_dir: Path
    #: ``intodoc-output/{project_id}`` (holds ``scan_result.json`` and ``scan/``).
    scan_output_dir: Path
    #: Composite partition name; ``""`` for a whole-project run. Names the
    #: solution node and the extraction's ``subproject``.
    subproject: str = ""
    #: Canonical unit ids of a scoped partition; ``None`` keeps the whole scan.
    unit_allowlist: frozenset[str] | None = None
    plugin_pack_dir: Path | None = None
    #: The staged runtime-evidence payload (``RuntimeEvidenceStore.to_json()``),
    #: when the run produced one.
    runtime_payload: dict[str, Any] | None = None
    #: Why ``llm`` is ``None``, recorded verbatim in ``state.json``.
    llm_unavailable_reason: str = ""
    log_prefix: str = "[custom]"


@dataclass
class TopologyBuildResult:
    """What a build produced, in the shape ``docs_staging/_topology/`` stores."""

    available: bool
    validated: bool
    lane: str
    topology: "BusinessTopology | None"
    #: Body of ``validation_report.json``, already serialised.
    validation_json: str
    #: Body of ``state.json``.
    state: dict[str, Any]
    #: Lines the caller appends to its run warnings.
    run_warnings: list[str] = field(default_factory=list)
    #: Phase-result style summary for the caller's own reporting.
    metadata: dict[str, Any] = field(default_factory=dict)

    def write(self, output_dir: Path) -> None:
        """Persist to ``output_dir`` (``docs_staging/_topology``)."""

        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "validation_report.json").write_text(
            self.validation_json, encoding="utf-8"
        )
        topology_path = output_dir / "business_topology.json"
        if self.available and self.validated and self.topology is not None:
            topology_path.write_text(
                self.topology.model_dump_json(indent=2), encoding="utf-8"
            )
        else:
            topology_path.unlink(missing_ok=True)
        (output_dir / "state.json").write_text(
            json.dumps(self.state, indent=2), encoding="utf-8"
        )


def _state(
    *,
    available: bool,
    validated: bool,
    scan_fingerprint: str,
    errors: list[str],
    warnings: list[str] | None = None,
    lane: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": available,
        "validated": validated,
        "scan_fingerprint": scan_fingerprint,
        "errors": errors,
        "warnings": warnings or [],
    }
    if lane:
        payload["lane"] = lane
    if extra:
        payload.update(extra)
    return payload


async def build_business_topology(
    inputs: TopologyBuildInputs,
    llm: TopologyLLM | None,
    *,
    log: LogFn | None = None,
) -> TopologyBuildResult:
    """Run the topology pipeline. Never raises."""

    async def _log(message: str, level: str = "info") -> None:
        if log is None:
            return
        try:
            await log(f"{inputs.log_prefix} {message}", level)
        except Exception:  # noqa: BLE001 - logging must not fail the build
            logger.debug("topology log callback failed", exc_info=True)

    model_lanes: dict[str, Any] = (
        {"enabled": True}
        if llm is not None
        else {
            "enabled": False,
            "reason": inputs.llm_unavailable_reason
            or "no model strategy was supplied for this engine",
        }
    )
    # Filled by `_build` as soon as the evidence bundle exists, so a failure
    # after that point still reports evidence counts exactly as Phase 3.7 did.
    progress: dict[str, Any] = {}
    try:
        return await _build(inputs, llm, _log, model_lanes=model_lanes, progress=progress)
    except Exception as exc:  # noqa: BLE001 - this pipeline is explicitly non-blocking
        bundle = progress.get("bundle")
        fingerprint = bundle.scan_fingerprint if bundle is not None else ""
        report_payload = {
            "validated": False,
            "scan_fingerprint": fingerprint,
            "project_id": inputs.project_id,
            "errors": [str(exc)],
            "warnings": ["Documentation generation continued without a business topology overlay"],
            "evidence_count": len(bundle.evidence_ids) if bundle is not None else 0,
            "cited_evidence_count": 0,
            "artifact_count": len(bundle.artifact_ids) if bundle is not None else 0,
            "cited_artifact_count": 0,
        }
        await _log(f"Business topology failed (non-blocking): {exc}", "warning")
        return TopologyBuildResult(
            available=False,
            validated=False,
            lane="",
            topology=None,
            validation_json=json.dumps(report_payload, indent=2),
            state=_state(
                available=False,
                validated=False,
                scan_fingerprint=fingerprint,
                errors=[str(exc)],
                extra={"model_lanes": model_lanes},
            ),
            run_warnings=[f"Business topology synthesis failed non-fatally: {exc}"],
            metadata={"available": False, "validated": False, "error": str(exc)},
        )


async def _build(
    inputs: TopologyBuildInputs,
    llm: TopologyLLM | None,
    _log: Callable[[str, str], Awaitable[None]],
    *,
    model_lanes: dict[str, Any],
    progress: dict[str, Any],
) -> TopologyBuildResult:
    from mcp_server.config import config
    # Resolved through the package on purpose: callers and tests patch the
    # package attributes, and Phase 3.7 always looked them up there.
    from mcp_server.services import business_topology as bt
    from mcp_server.services.business_topology.doc_evidence import (
        discover_doc_filenames,
    )

    staging_dir = inputs.staging_dir
    bundle = bt.build_business_topology_evidence(
        project_id=inputs.project_id,
        scan_output_dir=inputs.scan_output_dir,
        staging_dir=staging_dir,
        # Composite sub-project runs scope the artifact/lineage ledger to
        # this partition's units; the starter pack's cross-project index
        # (staged by the harness) carries the connective edges instead.
        unit_allowlist=inputs.unit_allowlist,
    )
    progress["bundle"] = bundle

    # The generated documents become a typed evidence tier so a flow taken
    # from `03_DATA_FLOWS_AND_LINEAGE.md` can cite a resolvable id. The
    # scanner-only bundle is kept for the deterministic overlay's own
    # validation: its claims must not become citable via this tier. Whatever
    # this run actually published, ranked -- not a fixed catalog list. A pack
    # replaces the document set wholesale, and a hardcoded filename tuple
    # would silently disable this lane for it.
    doc_filenames = discover_doc_filenames(staging_dir)
    doc_bundle, doc_records = bt.register_doc_evidence(
        bundle, staging_dir, filenames=doc_filenames
    )
    if not doc_records and llm is not None:
        await _log(
            "Business topology: no generated documents found to extract "
            "flows from; falling back to single-shot synthesis",
            "warning",
        )

    # Built once, used two ways: (a) the floor the model lanes must beat -- a
    # schema-valid but EMPTY topology must not be published when scanner
    # edges ground real streams; (b) the published fallback.
    deterministic_candidate = bt.build_deterministic_topology(
        bundle, project_id=inputs.project_id
    )
    deterministic_report = bt.validate_business_topology(
        deterministic_candidate, bundle=bundle, project_id=inputs.project_id
    )
    deterministic_streams = (
        len(deterministic_candidate.data_streams) if deterministic_report.validated else 0
    )

    lane = "deterministic"
    topology: BusinessTopology | None = None
    report: BusinessTopologyValidationReport | None = None
    active_bundle = bundle
    notes: list[str] = []

    if llm is not None:
        # PUSH the high-signal evidence directly into the model context
        # instead of relying on budget-limited agentic tool pulls (the
        # verified ETTM_part1 starvation, where the model reported "without
        # the ability to read evidence files (tool budget exhausted)").
        extraction_context = bt.build_topology_context_files(
            doc_bundle,
            staging_dir,
            byte_cap=config.topology_context_evidence_bytes,
            include_generated_docs=bool(doc_records),
            doc_filenames=doc_filenames,
        )
        synthesis_context = bt.build_topology_context_files(
            bundle,
            staging_dir,
            byte_cap=config.topology_context_evidence_bytes,
        )
        if doc_records:
            topology, report, notes = await _run_extraction_lane(
                inputs,
                llm,
                _log,
                bundle=doc_bundle,
                doc_records=doc_records,
                deterministic=deterministic_candidate if deterministic_streams else None,
                context_files=extraction_context,
            )
            if topology is not None:
                lane = "flow_extraction"
                active_bundle = doc_bundle

        # Stream COUNT is not the quality signal -- the verified complaint
        # about the old overlay was 18 unlabelled scanner edges. The extraction
        # lane already absorbs the scanner floor (`merge_scanner_floor`), so
        # the second model lane runs only when the first produced nothing
        # publishable at all.
        if topology is None:
            fallback_topology, fallback_report = await _run_synthesis_lane(
                inputs, llm, _log, bundle=bundle, context_files=synthesis_context
            )
            if fallback_topology is not None:
                topology, report = fallback_topology, fallback_report
                lane = "single_shot_synthesis"
                active_bundle = bundle
                notes = []

    extra: dict[str, Any] = {"model_lanes": model_lanes}

    if topology is None or not topology.data_streams:
        if deterministic_streams:
            reason = (
                "model lanes did not produce a publishable overlay"
                if llm is not None
                else "model lanes are disabled for this engine"
            )
            await _log(
                f"Business topology: {reason}; using deterministic evidence-only fallback "
                f"({len(deterministic_candidate.systems)} systems, "
                f"{deterministic_streams} streams)",
                "warning",
            )
            topology = deterministic_candidate
            report = deterministic_report
            lane = "deterministic"
            active_bundle = bundle
            notes = []
        else:
            errors = list(report.errors) if report is not None else [
                "no publishable business topology was produced"
            ]
            await _log(
                "Business topology validation failed (non-blocking): " + "; ".join(errors[:3]),
                "warning",
            )
            return TopologyBuildResult(
                available=False,
                validated=False,
                lane="",
                topology=None,
                validation_json=json.dumps({"validated": False, "errors": errors}, indent=2),
                state=_state(
                    available=False,
                    validated=False,
                    scan_fingerprint=bundle.scan_fingerprint,
                    errors=errors,
                    extra=extra,
                ),
                run_warnings=[
                    "Business topology synthesis was not published; documentation is unaffected"
                ],
                metadata={"available": False, "validated": False, "errors": errors[:5]},
            )

    topology, report, enrichment_state = await _enrich_and_validate(
        inputs, topology, active_bundle, _log
    )
    extra["enrichment"] = enrichment_state
    if notes:
        report.warnings = [*[f"assembly: {note}" for note in notes[:10]], *report.warnings]
    validation_json = report.model_dump_json(indent=2)
    if not report.validated:
        return TopologyBuildResult(
            available=False,
            validated=False,
            lane="",
            topology=None,
            validation_json=validation_json,
            state=_state(
                available=False,
                validated=False,
                scan_fingerprint=bundle.scan_fingerprint,
                errors=report.errors,
                warnings=report.warnings,
                extra=extra,
            ),
            run_warnings=["Business topology failed final provenance validation"],
            metadata={"available": False, "validated": False, "errors": report.errors[:5]},
        )

    await _log(
        f"Business topology complete via {lane}: "
        f"{len(topology.systems)} systems, {len(topology.data_streams)} data streams, "
        f"{len(topology.business_objects)} business objects, "
        f"{len(topology.delivery_paths)} delivery paths",
        "info",
    )
    return TopologyBuildResult(
        available=True,
        validated=True,
        lane=lane,
        topology=topology,
        validation_json=validation_json,
        state=_state(
            available=True,
            validated=True,
            scan_fingerprint=bundle.scan_fingerprint,
            errors=[],
            warnings=report.warnings,
            lane=lane,
            extra=extra,
        ),
        metadata={
            "available": True,
            "validated": True,
            "lane": lane,
            "systems": len(topology.systems),
            "data_streams": len(topology.data_streams),
            "business_objects": len(topology.business_objects),
            "delivery_paths": len(topology.delivery_paths),
            "deterministic_fallback": lane == "deterministic",
            "model_lanes_enabled": bool(model_lanes.get("enabled")),
        },
    )


async def _enrich_and_validate(
    inputs: TopologyBuildInputs,
    topology: "BusinessTopology",
    bundle: "BusinessTopologyEvidenceBundle",
    _log: Callable[[str, str], Awaitable[None]],
) -> tuple["BusinessTopology", "BusinessTopologyValidationReport", dict[str, Any]]:
    """Deterministic enrichment, then validation of the exact served payload.

    Enrichment is ADDITIVE and advisory. When the enriched topology fails
    validation (or its runtime evidence cannot be persisted) but the
    unenriched one passes, the unenriched one publishes and the rejection is
    recorded -- enrichment can improve a topology, never cost the run one.

    Runtime observations cite ``observed_runtime`` evidence the scanner bundle
    does not hold. Those records are registered on an in-memory bundle for
    validation AND persisted into ``_topology/evidence/`` (the publication
    read path re-loads the ledger from disk and re-validates). Persistence is
    called with an empty mapping when the unenriched topology is chosen, which
    removes a stale ``runtime.jsonl`` a previous attempt may have left.
    """

    from mcp_server.services import business_topology as bt

    try:
        from mcp_server.services.business_topology.enrichment import (
            EnrichmentSources,
            enrich_topology,
            persist_runtime_evidence,
            register_runtime_evidence,
        )

        # `enrich_topology` is pure: it copies, never mutates its argument.
        enriched, enrichment_report = enrich_topology(
            topology,
            EnrichmentSources(
                scan_dir=inputs.scan_output_dir,
                runtime_payload=inputs.runtime_payload,
                docs_dir=inputs.staging_dir,
                unit_allowlist=inputs.unit_allowlist,
            ),
        )
        runtime_records = dict(getattr(enrichment_report, "runtime_evidence", {}) or {})
        enriched_bundle = register_runtime_evidence(bundle, runtime_records)
        enriched_report = bt.validate_business_topology(
            enriched, bundle=enriched_bundle, project_id=inputs.project_id
        )
    except Exception as exc:  # noqa: BLE001 - enrichment is advisory
        logger.warning("business topology enrichment failed: %s", exc, exc_info=True)
        await _log(f"Business topology: enrichment skipped ({exc})", "warning")
        report = bt.validate_business_topology(
            topology, bundle=bundle, project_id=inputs.project_id
        )
        return topology, report, {"status": "failed", "error": str(exc)}

    enrichment_state: dict[str, Any] = {
        "status": "applied",
        "runtime_payload": inputs.runtime_payload is not None,
        **enrichment_report.as_dict(),
    }
    evidence_root = bundle.root / "evidence"
    if enriched_report.validated:
        try:
            persist_runtime_evidence(evidence_root, runtime_records)
        except Exception as exc:  # noqa: BLE001 - fall back to the unenriched overlay
            logger.warning("runtime evidence could not be persisted: %s", exc, exc_info=True)
            enriched_report.validated = False
            enriched_report.errors = [
                f"runtime evidence could not be persisted: {exc}",
                *enriched_report.errors,
            ]
        else:
            filled = sum(int(v) for v in (enrichment_report.filled or {}).values())
            if filled:
                await _log(f"Business topology: enrichment filled {filled} field(s)", "info")
            return enriched, enriched_report, enrichment_state

    try:
        persist_runtime_evidence(evidence_root, {})
    except Exception:  # noqa: BLE001 - best effort; the unenriched overlay cites none
        logger.warning("stale runtime evidence could not be cleared", exc_info=True)
    report = bt.validate_business_topology(topology, bundle=bundle, project_id=inputs.project_id)
    if report.validated:
        enrichment_state["status"] = "rejected"
        enrichment_state["validation_errors"] = list(enriched_report.errors[:10])
        await _log(
            "Business topology: enriched overlay failed validation ("
            + "; ".join(enriched_report.errors[:2])
            + "); publishing the unenriched overlay",
            "warning",
        )
        return topology, report, enrichment_state
    # Neither validates: the defect predates enrichment, so the unenriched
    # report is the one that describes it.
    enrichment_state["status"] = "not_published"
    return topology, report, enrichment_state


async def _run_extraction_lane(
    inputs: TopologyBuildInputs,
    llm: TopologyLLM,
    _log: Callable[[str, str], Awaitable[None]],
    *,
    bundle: "BusinessTopologyEvidenceBundle",
    doc_records: dict[str, dict[str, Any]],
    deterministic: "BusinessTopology | None",
    context_files: list[Path],
) -> tuple["BusinessTopology | None", "BusinessTopologyValidationReport | None", list[str]]:
    """Stage A + B: extract business-level flows, then assemble in Python."""

    from mcp_server.services import business_topology as bt
    from mcp_server.services.business_topology.assembly import merge_scanner_floor
    from mcp_server.services.business_topology.prompt import flow_extraction_prompt

    partition_name = inputs.subproject
    subproject = partition_name or inputs.project_id
    prompt = flow_extraction_prompt(
        subproject=subproject,
        documents=sorted(
            {str(record.get("document") or "") for record in doc_records.values()} - {""}
        ),
        plugin_pack_dir=inputs.plugin_pack_dir,
    )

    def _validate(text: str) -> dict[str, Any]:
        extraction, dropped = bt.parse_flow_extraction(text, subproject=subproject)
        if extraction is None:
            return {"ok": False, "errors": dropped}
        if not extraction.flows:
            # An extraction with systems but no flows describes no
            # architecture; the retry is worth one call.
            return {
                "ok": False,
                "errors": ["flow extraction contained no usable flows", *dropped[:5]],
            }
        return {"ok": True, "errors": [], "warnings": dropped[:10]}

    result = await llm.complete(
        prompt,
        context_files=context_files,
        purpose=PURPOSE_FLOW_EXTRACTION,
        target_id=f"{PURPOSE_FLOW_EXTRACTION}_{bundle.scan_fingerprint[:16]}",
        validate=_validate,
    )
    if result is None:
        await _log(
            "Business topology: flow extraction did not produce a usable result; falling back",
            "warning",
        )
        return None, None, []

    extraction, dropped = bt.parse_flow_extraction(result.text, subproject=subproject)
    if extraction is None or not extraction.flows:
        return None, None, []
    if dropped:
        await _log(
            f"Business topology: flow extraction dropped {len(dropped)} "
            f"unusable item(s) (e.g. {dropped[0]})",
            "warning",
        )

    topology, notes = bt.assemble_topology(
        extraction,
        bundle=bundle,
        project_id=inputs.project_id,
        doc_records=doc_records,
        subproject=partition_name,
        model_name=result.model_name,
    )
    if deterministic is not None:
        floor_notes = merge_scanner_floor(topology, deterministic)
        notes.extend(floor_notes)
        if floor_notes:
            await _log(f"Business topology: {floor_notes[0]}", "info")
    # Pruning runs LAST, after the floor merge, so a defect introduced by
    # either source costs only its own claim.
    topology, report, pruned = bt.prune_to_valid(
        topology, bundle=bundle, project_id=inputs.project_id
    )
    if pruned:
        await _log(
            f"Business topology: pruned {len(pruned)} claim(s) that failed "
            f"provenance validation (e.g. {pruned[0]}); publishing the remainder",
            "warning",
        )
    if not report.validated:
        await _log(
            "Business topology: assembled overlay did not validate ("
            + "; ".join(report.errors[:2])
            + ")",
            "warning",
        )
        return None, report, notes + [*dropped]
    return topology, report, notes + [*dropped]


async def _run_synthesis_lane(
    inputs: TopologyBuildInputs,
    llm: TopologyLLM,
    _log: Callable[[str, str], Awaitable[None]],
    *,
    bundle: "BusinessTopologyEvidenceBundle",
    context_files: list[Path],
) -> tuple["BusinessTopology | None", "BusinessTopologyValidationReport | None"]:
    """The previous single-shot contract, kept as the second model lane."""

    from mcp_server.services import business_topology as bt
    from mcp_server.services.business_topology.prompt import business_topology_prompt

    prompt = business_topology_prompt(
        project_id=inputs.project_id,
        scan_fingerprint=bundle.scan_fingerprint,
        plugin_pack_dir=inputs.plugin_pack_dir,
    )

    def _validate(text: str) -> dict[str, Any]:
        parsed, validation, repair_notes = bt.parse_validate_repair_business_topology(
            text, bundle=bundle, project_id=inputs.project_id
        )
        payload: dict[str, Any] = {"ok": validation.validated, **validation.model_dump()}
        if parsed is not None and not parsed.data_streams:
            payload["ok"] = False
            payload["errors"] = [
                *validation.errors,
                "topology contains no data streams; emit the grounded streams",
            ]
        if repair_notes:
            payload["repair_notes"] = repair_notes
        return payload

    result = await llm.complete(
        prompt,
        context_files=context_files,
        purpose=PURPOSE_SINGLE_SHOT,
        target_id=f"{PURPOSE_SINGLE_SHOT}_{bundle.scan_fingerprint[:16]}",
        validate=_validate,
    )
    if result is None:
        return None, None

    topology, report, repair_notes = bt.parse_validate_repair_business_topology(
        result.text, bundle=bundle, project_id=inputs.project_id
    )
    if topology is None:
        return None, report
    if not report.validated:
        # Partial acceptance: a claim-local defect costs that claim, not the
        # whole architecture (the verified ETTM_part2 loss).
        topology, report, pruned = bt.prune_to_valid(
            topology, bundle=bundle, project_id=inputs.project_id
        )
        if pruned:
            await _log(
                f"Business topology: pruned {len(pruned)} invalid claim(s) "
                f"from single-shot synthesis (e.g. {pruned[0]})",
                "warning",
            )
    if repair_notes and report.validated:
        await _log(
            "Business topology: accepted after deterministic repair "
            f"({len(repair_notes)} corrections, e.g. {repair_notes[0]})",
            "warning",
        )
    return (topology if report.validated else None), report


class BoundedModelTopologyLLM:
    """Strategy over any single-completion ``ModelProvider``, with a hard call cap.

    Used by engines that have no agent-task runtime (the Claude CLI lane): one
    non-agentic completion per lane, no tools, no retry-with-feedback, and at
    most ``max_calls`` model calls for the whole build. When the cap is reached
    the lane gets ``None`` and the pipeline falls through to the deterministic
    floor, so the spend of a topology build is bounded by construction.
    ``calls`` records what was actually spent, for the caller's state record.
    """

    def __init__(
        self,
        provider: Any,
        *,
        max_calls: int = 2,
        timeout_s: float = 300.0,
        max_tokens: int = 32_000,
    ) -> None:
        self._provider = provider
        self._max_calls = max(0, int(max_calls))
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self.calls: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return str(getattr(self._provider, "model_name", "") or "")

    async def complete(
        self,
        prompt: str,
        *,
        context_files: list[Path],
        purpose: str,
        target_id: str,
        validate: ValidateFn,
    ) -> TopologyCompletion | None:
        from mcp_server.providers.custom.models import ModelOptions

        if len(self.calls) >= self._max_calls:
            self.calls.append({"purpose": purpose, "status": "skipped_call_budget"})
            return None
        options = ModelOptions(
            max_tokens=self._max_tokens,
            temperature=0.0,
            retries=0,
            timeout_s=self._timeout_s,
            json_mode=True,
        )
        record: dict[str, Any] = {"purpose": purpose, "target_id": target_id}
        self.calls.append(record)
        try:
            result = await self._provider.complete(prompt, context_files, options)
        except Exception as exc:  # noqa: BLE001 - a lane failure is a fallback, not a crash
            record.update(status="error", error=str(exc)[:500])
            return None
        record.update(
            status="ok" if result.success else "failed",
            model_name=result.model_name,
            input_tokens=int(result.input_tokens or 0),
            output_tokens=int(result.output_tokens or 0),
        )
        if not result.success or not (result.text or "").strip():
            record["error"] = str(result.raw_error or "empty completion")[:500]
            return None
        try:
            verdict = validate(result.text)
            record["accepted"] = bool(verdict.get("ok"))
        except Exception as exc:  # noqa: BLE001 - validation is advisory here
            record["accepted"] = False
            record["validate_error"] = str(exc)[:500]
        return TopologyCompletion(text=result.text, model_name=result.model_name or self.model_name)


def load_staged_runtime_payload(staging_dir: Path) -> dict[str, Any] | None:
    """Read ``_transient/runtime/runtime_evidence.json`` if a producer staged it."""

    from mcp_server.providers.custom.runtime_evidence import RUNTIME_CACHE_JSON

    path = staging_dir / RUNTIME_CACHE_JSON
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "BoundedModelTopologyLLM",
    "PURPOSE_FLOW_EXTRACTION",
    "PURPOSE_SINGLE_SHOT",
    "TopologyBuildInputs",
    "TopologyBuildResult",
    "TopologyCompletion",
    "TopologyLLM",
    "build_business_topology",
    "load_staged_runtime_payload",
]
