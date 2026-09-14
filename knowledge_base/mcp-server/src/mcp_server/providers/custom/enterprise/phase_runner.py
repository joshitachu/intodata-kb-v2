"""Durable, resumable, budgeted execution of the enterprise phase sequence.

Adapted from the Integration Suite's headless per-phase model (independent
sessions, gate per phase, repair-what-exists / regenerate-what-is-absent) to
OpenCode: every session gets the goal, the pinned Talend method, the complete
sanitized workspace with native read/grep/glob/list, the prior durable
artifacts, and exactly one typed submission tool. No permission bypass, no
direct writes, no web access.

Resource ceilings stop new dispatches and leave an explicitly INCOMPLETE run
(staging preserved for resume; the previous published baseline untouched).
They never narrow what a session may read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp_server.providers.custom.enterprise import phase_contracts as pc
from mcp_server.providers.custom.enterprise.integration_projection import (
    build_dossier_plan,
    render_catalogue,
    render_dossier,
    write_client_document,
    write_dossier_plan,
)
from mcp_server.providers.custom.enterprise import knowledge_model as km
from mcp_server.providers.custom.enterprise.knowledge_model import (
    KnowledgeModel,
    persist_knowledge_model,
)
from mcp_server.providers.custom.enterprise.knowledge_questions import write_question_coverage
from mcp_server.providers.custom.enterprise.layout import (
    CATALOGUE_DOC,
    EXACT_INDEX,
    KNOWLEDGE_MODEL,
    METHOD_ROOT,
    PHASE_STATE,
    RAG_KNOWLEDGE_ROOT,
    REPAIR_LEDGER,
    REVIEW_REPORT,
    RUN_RECEIPT,
    RUNTIME_AUTHORITY_JSON,
    WORKSPACE_MANIFEST,
    WORKSPACE_ROOT,
)
from mcp_server.providers.custom.enterprise.publication_policy import (
    deterministic_review,
    evaluate_publication,
    write_enterprise_gates,
)
from mcp_server.providers.custom.enterprise.workspace import (
    EnterpriseWorkspaceError,
    ExactTables,
    build_exact_tables,
    stage_sanitized_workspace,
)
from mcp_server.providers.custom.knowledge_contracts import write_json
from mcp_server.providers.custom.prompt_renderer import get_renderer
from mcp_server.services.generation_profile import EnterpriseBudgets

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "talend-enterprise-v1"

# Session-local registry: the artifact facade runs in this process and looks up
# the live context by staging directory when the model submits.
_CONTEXTS: dict[str, pc.EnterpriseContext] = {}


def register_context(ectx: pc.EnterpriseContext) -> None:
    _CONTEXTS[str(ectx.staging_dir.resolve())] = ectx


def unregister_context(staging_dir: Path) -> None:
    _CONTEXTS.pop(str(staging_dir.resolve()), None)


def submit_enterprise_artifact(staging_dir: Path, metadata: dict[str, Any], content: str) -> dict[str, Any]:
    """Facade entry point: validate and persist one typed submission."""
    ectx = _CONTEXTS.get(str(Path(staging_dir).resolve()))
    if ectx is None:
        return {"ok": False, "error": "EnterpriseContextUnavailable",
                "reason": "the enterprise run is not active in this process; the submission was not recorded"}
    phase = str(metadata.get("enterprise_phase") or "")
    try:
        return pc.handle_submission(ectx, phase, content)
    except pc.EnterpriseContractError as exc:
        return {"ok": False, "error": "EnterpriseContractError", "reason": str(exc)[:1000]}


@dataclass
class PhaseAttempt:
    step: str
    kind: str  # initial | recovery | repair | regenerate
    assignment_count: int
    session_id: str | None = None
    processed_tokens: int = 0
    cost_usd: float = 0.0
    outcome: str = ""  # completed | interrupted | error
    remaining_before: int = 0
    remaining_after: int = 0
    detail: str = ""


@dataclass
class PhaseRecord:
    status: str = "pending"  # pending | running | complete | incomplete
    attempts: list[dict[str, Any]] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    gate_errors: list[str] = field(default_factory=list)
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class RunState:
    schema_version: str = "enterprise-phase-state-1"
    contract: str = CONTRACT_VERSION
    snapshot_id: str = ""
    fingerprint: str = ""
    phases: dict[str, PhaseRecord] = field(default_factory=dict)
    processed_tokens: int = 0
    cost_usd: float = 0.0
    sessions: int = 0
    started_at: float = 0.0
    stop_reason: str = ""
    model_route: str = ""
    model_source: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phases"] = {name: asdict(record) for name, record in self.phases.items()}
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RunState":
        phases = {name: PhaseRecord(**record) for name, record in (payload.get("phases") or {}).items()}
        known = {k: v for k, v in payload.items() if k in cls.__dataclass_fields__ and k != "phases"}
        state = cls(**known)
        state.phases = phases
        return state


class BudgetExhausted(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EnterpriseRunner:
    def __init__(
        self,
        provider: Any,
        *,
        resolved: str,
        ctx: Any,
        facts: Any,
        ledger: Any,
        log_cb: Callable[..., Any],
        on_complete: Callable[[str, Any], None],
        profile: dict[str, Any],
        scan_output_dir: Path,
    ) -> None:
        self.provider = provider
        self.resolved = resolved
        self.ctx = ctx
        self.facts = facts
        self.ledger = ledger
        self.log_cb = log_cb
        self.on_complete = on_complete
        self.profile = profile
        self.budgets = EnterpriseBudgets.model_validate(profile.get("enterprise") or {})
        self.scan_output_dir = scan_output_dir
        self.staging = Path(ctx.staging_dir)
        self.renderer = get_renderer()
        self.state = RunState()
        self.ectx: pc.EnterpriseContext | None = None
        self.exact: ExactTables | None = None
        self._lock = asyncio.Lock()
        # Monotonic session number. The step name derived from it is the key
        # `OpenCodeAttemptLedger` counts dispatches against, and it permits
        # exactly one dispatch per key. Deriving the name from
        # `len(record.attempts)` looked equivalent and was not: chunks inside
        # one round run concurrently and all read that length before any of
        # them appends, so every chunk produced the SAME step name and the
        # ledger rejected all but the first with "exceeds max_attempts=1".
        # Verified on a real run: phase 1 accepted one chunk of 12 designs,
        # then died with 38 keys still outstanding.
        self._session_seq = 0

    # ------------------------------------------------------------------
    # logging / persistence helpers
    # ------------------------------------------------------------------
    async def _log(self, message: str, level: str = "info") -> None:
        await self.log_cb(self.ctx.project_id, f"[enterprise] {message}", level)

    def _save_state(self) -> None:
        write_json(self.staging / PHASE_STATE, self.state.to_json())

    def _record(self, phase: str) -> PhaseRecord:
        return self.state.phases.setdefault(phase, PhaseRecord())

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------
    def _resolve_model(self) -> tuple[str, str]:
        pinned = self.profile.get("model")
        if pinned:
            self.provider.model_route = str(pinned)
            return str(pinned), "pinned_by_profile"
        route = str(getattr(self.provider, "model_route", "") or "")
        requested = str(getattr(self.ctx, "effective_model", "") or "")
        if route and requested and requested not in {"opencode", "opencode-cli", ""} and requested == route:
            return route, "pinned_by_request"
        if route:
            return route, "configured_default"
        return "", "opencode_runtime_default"

    def _fingerprint(self) -> str:
        parts: list[str] = [CONTRACT_VERSION]
        for relative in (WORKSPACE_MANIFEST, EXACT_INDEX, METHOD_ROOT / "talend" / "manifest.json"):
            path = self.staging / relative
            parts.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent")
        template_dir = Path(self.renderer._root) / "enterprise"
        for template in sorted(template_dir.glob("*.md")):
            parts.append(hashlib.sha256(template.read_bytes()).hexdigest())
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    async def prepare(self) -> None:
        unit_allowlist = set(self.facts.unit_names()) if getattr(self.ctx, "partition", None) is not None else None
        receipt = stage_sanitized_workspace(self.scan_output_dir, self.staging)
        await self._log(f"sanitized workspace staged: {receipt.file_count} files, {receipt.byte_count} bytes, "
                        f"{len(receipt.excluded)} excluded for residual redaction findings")
        self.exact = build_exact_tables(self.scan_output_dir, self.staging, unit_allowlist=unit_allowlist)
        unit_ids = set(self.facts.unit_names())
        missing = unit_ids - set(self.exact.units)
        if missing:
            raise EnterpriseWorkspaceError(
                "facts manifest units absent from the scan: " + ", ".join(sorted(missing)[:5])
            )
        self._stage_method()
        await self._stage_runtime_authority()
        snapshot_material = json.dumps({
            "workspace": receipt.manifest_sha256, "project": self.ctx.project_id,
            "units": sorted(unit_ids),
        }, sort_keys=True)
        snapshot_id = hashlib.sha256(snapshot_material.encode("utf-8")).hexdigest()[:24]
        self.ectx = pc.EnterpriseContext(
            staging_dir=self.staging, project_id=self.ctx.project_id, snapshot_id=snapshot_id,
            exact=self.exact, unit_ids=unit_ids,
        )
        register_context(self.ectx)
        fingerprint = self._fingerprint()
        previous = self._load_state()
        if previous is not None and previous.fingerprint == fingerprint and previous.snapshot_id == snapshot_id:
            self.state = previous
            await self._log("resuming from durable phase state; accepted ledger entries are kept")
        else:
            if previous is not None:
                await self._log("evidence, method or prompts changed since the previous attempt; ledgers reset", "warning")
                for phase in pc.PHASE_ORDER:
                    self.ectx.ledger(phase).clear()
            self.state = RunState(snapshot_id=snapshot_id, fingerprint=fingerprint, started_at=time.time())
        self._session_seq = sum(
            len(record.attempts) for record in self.state.phases.values()
        )
        route, source = self._resolve_model()
        self.state.model_route, self.state.model_source = route, source
        if not self.state.started_at:
            self.state.started_at = time.time()
        self._save_state()
        await self._log(f"model route: {route or 'OpenCode runtime default'} ({source}); "
                        f"budgets: {self.budgets.total_timeout_seconds}s total, "
                        f"{self.budgets.total_processed_token_limit} processed tokens, "
                        f"USD {self.budgets.cost_dispatch_ceiling_usd} dispatch ceiling")

    def _load_state(self) -> RunState | None:
        path = self.staging / PHASE_STATE
        if not path.is_file():
            return None
        try:
            return RunState.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _stage_method(self) -> None:
        try:
            from mcp_server.services.investigation_method import materialize_investigation_method
        except ImportError:  # pragma: no cover - the method module is delivered separately
            logger.warning("investigation method module unavailable; guides not staged")
            return
        materialize_investigation_method(self.staging, str(getattr(self.ctx, "platform", "talend") or "talend"))

    async def _stage_runtime_authority(self) -> None:
        """Fuse every runtime authority into `_analysis/_runtime_authority.json`.

        Stays in `prepare()`, and must. The TMC side reads
        `_transient/runtime/runtime_evidence.json`, which
        `OpenCodeCliProvider._prepare_run` produces (via
        `HarnessHelpersImpl.ensure_runtime_evidence`) BEFORE the enterprise
        runner is constructed and which Phase 4 cleanup destroys long after --
        so this sits inside the only window where both authorities are readable.

        Async because the log-estate side is a bounded network read. It is
        best-effort by contract: `_fetch_elastic_aggregates` returns `None` for
        every kind of absence and the fusion states `no_elastic_source`, so an
        Elastic estate that is down costs the documents a figure and never
        costs the run.
        """
        target = self.staging / RUNTIME_AUTHORITY_JSON
        try:
            from mcp_server.providers.custom.runtime_authority import (
                fuse_runtime_authorities,
                load_tmc_store_payload,
                write_runtime_authority,
            )
        except ImportError:  # pragma: no cover - delivered separately; absence stays explicit
            write_json(target, {
                "schema_version": "runtime-authority-1", "project_id": self.ctx.project_id,
                "authorities": {"tmc": "unavailable", "elastic": "unavailable"},
                "observations": [], "deployed_set": [], "deployed_set_gaps": [], "correlation_latency": [],
                "limitations": [{"code": "runtime_unavailable", "message": "runtime authority fusion unavailable in this build", "scope": "project"}],
                "authority_notes": ["runtime authority module unavailable"],
            })
            return
        unit_kinds = {unit_id: str(unit.get("kind") or "job") for unit_id, unit in (self.exact.units if self.exact else {}).items()}
        elastic_payload = await self._fetch_elastic_aggregates()
        bundle = fuse_runtime_authorities(load_tmc_store_payload(self.staging), elastic_payload,
                                          project_id=self.ctx.project_id, unit_kinds=unit_kinds)
        write_runtime_authority(self.staging, bundle)

    async def _fetch_elastic_aggregates(self) -> dict[str, Any] | None:
        """Best-effort log-estate read. `None` means "nothing was supplied".

        The raw body is returned UNPARSED: `fuse_runtime_authorities` validates
        it against `ElasticAggregatePayload` and records
        `elastic_payload_invalid` for a malformed one. Validating here as well
        would be a second definition of one contract, free to drift from the
        one the bundle is actually built against.
        """
        try:
            from mcp_server.providers.custom.elastic_evidence import (
                fetch_elastic_aggregates,
                per_job_count,
            )
        except ImportError:
            # Its own try, NOT the fusion's above: a build without this producer
            # must lose the Elastic authority only. Folding it into the block
            # above would trip the whole-bundle stub and declare TMC unavailable
            # too, discarding evidence that was successfully fetched.
            logger.warning(
                "elastic evidence module unavailable; the log-estate authority stays "
                "unavailable and the TMC lane is unaffected"
            )
            return None
        payload = await fetch_elastic_aggregates(
            self.ctx.project_id,
            live_evidence=bool(getattr(self.ctx, "live_evidence", True)),
        )
        if payload is None:
            await self._log(
                "log-estate (Elastic) aggregate not available for this run; message "
                "volumes, error volumes and end-to-end latency are stated as "
                "unavailable rather than inferred"
            )
        else:
            await self._log(
                f"log-estate (Elastic) aggregate fetched: "
                f"{per_job_count(payload)} job aggregate(s) offered to the fusion"
            )
        return payload

    # ------------------------------------------------------------------
    # budgets
    # ------------------------------------------------------------------
    def _check_budget(self) -> None:
        elapsed = time.time() - self.state.started_at
        if elapsed >= self.budgets.total_timeout_seconds:
            raise BudgetExhausted("total_timeout")
        if self.state.processed_tokens >= self.budgets.total_processed_token_limit:
            raise BudgetExhausted("total_processed_token_budget")
        if self.state.cost_usd >= self.budgets.cost_dispatch_ceiling_usd:
            raise BudgetExhausted("cost_dispatch_ceiling")

    def _charge(self, processed: int, cost: float) -> None:
        self.state.processed_tokens += int(processed)
        self.state.cost_usd += float(cost)
        self.state.sessions += 1

    # ------------------------------------------------------------------
    # prompts
    # ------------------------------------------------------------------
    def _method_index(self) -> str:
        try:
            from mcp_server.services.investigation_method import method_prompt_index
            text = method_prompt_index(self.staging, str(getattr(self.ctx, "platform", "talend") or "talend"))
            if text:
                return text
        except ImportError:  # pragma: no cover
            pass
        method_dir = self.staging / METHOD_ROOT / "talend"
        if method_dir.is_dir():
            return "\n".join(f"- {METHOD_ROOT.as_posix()}/talend/{p.name}" for p in sorted(method_dir.glob("*.md")))
        return "- (no method guides staged for this platform)"

    def _workspace_layout(self) -> str:
        manifest = self.staging / WORKSPACE_MANIFEST
        roots: list[str] = []
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            roots = [str(r) for r in payload.get("source_roots") or []]
            count = payload.get("file_count", 0)
        except (OSError, json.JSONDecodeError):
            count = 0
        lines = [f"- {WORKSPACE_ROOT.as_posix()}/ : complete sanitized Talend export ({count} files); source roots: "
                 + (", ".join(roots) if roots else "single root")]
        lines.append(f"- {EXACT_INDEX.parent.as_posix()}/ : deterministic exact tables (see index.md)")
        lines.append("- facts_manifest.json, engineering_facts.json, dependency_map.md : scanner-computed maps")
        if (self.staging / RUNTIME_AUTHORITY_JSON).is_file():
            lines.append(f"- {RUNTIME_AUTHORITY_JSON.as_posix()} : runtime authorities (TMC and optional Elastic)")
        return "\n".join(lines)

    def _prior_artifacts(self, phase: str) -> str:
        items: list[str] = []
        if phase in {pc.PHASE_RECONSTRUCT, pc.PHASE_MODEL, pc.PHASE_RECONCILE}:
            items.append(f"- {(pc.PHASES_ROOT / pc.PHASE_DEEP_ANALYZE / 'ledger').as_posix()}/ : accepted design analyses")
        if phase in {pc.PHASE_MODEL, pc.PHASE_RECONCILE}:
            items.append(f"- {(pc.PHASES_ROOT / pc.PHASE_RECONSTRUCT / 'ledger').as_posix()}/ : accepted journeys and exclusions")
        if phase == pc.PHASE_RECONCILE:
            items.append(f"- {(pc.PHASES_ROOT / pc.PHASE_MODEL / 'ledger').as_posix()}/ : accepted mappings, configuration, error paths, business meaning")
        if phase in {pc.PHASE_WRITE, pc.PHASE_REVIEW, pc.PHASE_REPAIR}:
            items.append(f"- {KNOWLEDGE_MODEL.as_posix()} : the validated enterprise knowledge model (claim ids live here)")
            items.append(f"- {pc.PHASES_ROOT.parent.as_posix()}/dossier_plan.json : the document plan")
        if phase in {pc.PHASE_REVIEW, pc.PHASE_REPAIR}:
            items.append("- *.md at the workspace root: the drafted client documents")
        return "\n".join(items) or "- none"

    def _prompt(self, phase: str, assignment: list[str], kind: str, feedback: str = "") -> str:
        common = self.renderer.render("enterprise/phase_common.md", {
            "PROJECT_ID": self.ctx.project_id,
            "WORKSPACE_LAYOUT": self._workspace_layout(),
            "METHOD_INDEX": self._method_index(),
            "PRIOR_ARTIFACTS": self._prior_artifacts(phase),
            "OUTPUT_ARTIFACT": pc.submission_artifact(phase if phase != pc.PHASE_REPAIR else pc.PHASE_WRITE),
        })
        body = self.renderer.render(f"enterprise/{phase}.md", {
            "ASSIGNMENT": json.dumps(assignment, indent=1, ensure_ascii=True),
            "ASSIGNMENT_COUNT": str(len(assignment)),
        })
        tail = ""
        if kind == "recovery":
            tail = self.renderer.render("enterprise/recovery_suffix.md", {"FEEDBACK": feedback})
        elif kind == "repair":
            tail = self.renderer.render("enterprise/repair_suffix.md", {"FEEDBACK": feedback})
        return "\n\n".join(part for part in (common, body, tail) if part.strip())

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    async def _session(self, phase: str, assignment: list[str], kind: str, feedback: str = "") -> PhaseAttempt:
        from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError, OpenCodeTaskContract, _safe_name

        record = self._record(phase)
        async with self._lock:
            self._session_seq += 1
            sequence = self._session_seq
        step = f"enterprise_{phase}_{kind}_{sequence:03d}"
        submit_phase = pc.PHASE_WRITE if phase == pc.PHASE_REPAIR else phase
        contract = OpenCodeTaskContract(
            phase="analyze" if phase in pc.MODEL_PHASES else "produce",
            target=f"enterprise:{phase}:{sequence}",
            prompt_artifact=f"enterprise/{phase}.md",
            prompt=self._prompt(phase, assignment, kind, feedback),
            evidence_artifacts=(WORKSPACE_MANIFEST.as_posix(), EXACT_INDEX.as_posix()),
            output_artifact=pc.submission_artifact(submit_phase),
            metadata={"enterprise_phase": submit_phase, "assignment_count": len(assignment), "assignment": list(assignment), "kind": kind},
        )
        attempt = PhaseAttempt(step=step, kind=kind, assignment_count=len(assignment),
                               remaining_before=len(pc.remaining_for(self.ectx, phase)))
        try:
            execution = await self.provider._execute_step(
                resolved=self.resolved, ctx=self.ctx, phase=contract.phase, step=step,
                contracts=[contract], allowlist={}, session_id=None, ledger=self.ledger,
                log_cb=self.log_cb, prompt_override=contract.prompt, require_outputs=False,
                exploration_artifacts=None,
                timeout_seconds=self.budgets.session_timeout_seconds,
                max_model_turns=self.budgets.session_max_turns,
                max_processed_tokens=self.budgets.session_max_processed_tokens,
            )
            processed = getattr(execution, "processed_tokens", None)
            if processed is None:
                processed = getattr(execution, "input_tokens", 0) + getattr(execution, "output_tokens", 0)
            attempt.session_id = getattr(execution, "session_id", None)
            attempt.processed_tokens = int(processed or 0)
            attempt.cost_usd = float(getattr(execution, "cost_usd", 0.0) or 0.0)
            attempt.outcome = "completed"
            self.on_complete(step, execution)
        except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
            usage_path = self.staging / "_audit" / "opencode_interrupted_usage" / f"{_safe_name(step)}.json"
            try:
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                usage = {}
            attempt.processed_tokens = int(usage.get("processed_tokens") or 0)
            attempt.cost_usd = float(usage.get("cost_usd") or 0.0)
            attempt.session_id = usage.get("session_id")
            attempt.outcome = "interrupted"
            attempt.detail = str(exc)[:500]
            await self._log(f"{phase} session ended early: {exc}; accepted submissions were kept", "warning")
        async with self._lock:
            self._charge(attempt.processed_tokens, attempt.cost_usd)
            attempt.remaining_after = len(pc.remaining_for(self.ectx, phase))
            record.attempts.append(asdict(attempt))
            self._save_state()
        return attempt

    def _chunks(self, keys: list[str]) -> list[list[str]]:
        size = max(1, self.budgets.units_per_session)
        return [keys[i:i + size] for i in range(0, len(keys), size)] or [[]]

    async def _run_ledger_phase(self, phase: str) -> bool:
        """Cover every remaining key; returns True when the ledger is complete."""
        record = self._record(phase)
        if record.status == "complete" and not pc.remaining_for(self.ectx, phase):
            await self._log(f"{phase}: already complete (resume)")
            return True
        record.status = "running"
        record.started_at = record.started_at or time.time()
        self._save_state()
        rounds = 1 + self.budgets.max_regenerate_attempts
        for round_index in range(rounds):
            remaining = pc.remaining_for(self.ectx, phase)
            if not remaining and phase not in {pc.PHASE_RECONCILE, pc.PHASE_REVIEW}:
                break
            if not remaining and phase in {pc.PHASE_RECONCILE, pc.PHASE_REVIEW} and record.attempts:
                break
            kind = "initial" if round_index == 0 and not record.attempts else "recovery"
            feedback = ""
            if kind == "recovery":
                feedback = f"Still unaccounted after the previous session(s): {len(remaining)} key(s). " \
                           f"First: {', '.join(remaining[:20])}."
            chunks = self._chunks(remaining) if remaining else [[]]
            semaphore = asyncio.Semaphore(max(1, self.budgets.concurrency))

            async def run_chunk(chunk: list[str]) -> None:
                async with semaphore:
                    self._check_budget()
                    await self._session(phase, chunk, kind, feedback)

            before = len(remaining)
            # Let every in-flight session finish before surfacing a ceiling:
            # gather() without return_exceptions would raise on the first hit
            # while siblings keep running and mutating state after the receipt.
            outcomes = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks), return_exceptions=True)
            failures = [item for item in outcomes if isinstance(item, BaseException)]
            budget_hits = [item for item in failures if isinstance(item, BudgetExhausted)]
            if budget_hits:
                raise budget_hits[0]
            if failures:
                raise failures[0]
            after = len(pc.remaining_for(self.ectx, phase))
            await self._log(f"{phase}: round {round_index + 1}, remaining {before} -> {after}")
            if after == 0:
                break
            if after >= before and round_index > 0:
                await self._log(f"{phase}: no progress in a recovery round; stopping this phase", "warning")
                break
        remaining = pc.remaining_for(self.ectx, phase)
        record.remaining = remaining
        if remaining:
            record.status = "incomplete"
            record.gate_errors = [f"{len(remaining)} key(s) never accepted: {', '.join(remaining[:10])}"]
            self._save_state()
            return False
        record.status = "complete"
        record.completed_at = time.time()
        self._save_state()
        return True

    # ------------------------------------------------------------------
    # knowledge model assembly + repair
    # ------------------------------------------------------------------
    async def _assemble_and_validate(self) -> KnowledgeModel | None:
        for attempt in range(1 + self.budgets.max_repair_attempts):
            model = pc.assemble_knowledge_model(self.ectx)
            report = pc.validate_assembled_model(self.ectx, model)
            persist_knowledge_model(self.staging, model, report)
            if report.ok:
                self.ectx.knowledge = model
                self.ectx.claim_ids = {
                    entry.claim_id for section in km._SECTION_KEYS for entry in getattr(model, section)
                }
                await self._log(f"knowledge model validated: {json.dumps(report.coverage, sort_keys=True)}")
                return model
            targets = pc.repair_targets_from_validation(report)
            await self._log(f"knowledge model validation failed with {len(report.errors)} error(s); "
                            f"repair targets: {sum(len(v) for v in targets.values())}", "warning")
            if attempt >= self.budgets.max_repair_attempts:
                break
            for phase, keys in targets.items():
                errors = [e for e in report.errors if any(k.split(':', 1)[1] in e for k in keys)]
                feedback = "\n".join(f"- {e}" for e in errors[:60])
                for chunk in self._chunks(keys):
                    self._check_budget()
                    await self._session(phase, chunk, "repair", feedback)
        self._record(pc.PHASE_MODEL).gate_errors = report.errors[:100]
        self._record(pc.PHASE_MODEL).status = "incomplete"
        self._save_state()
        return None

    # ------------------------------------------------------------------
    # writing, review, repair
    # ------------------------------------------------------------------
    def _render_docs(self, docs: list[str] | None = None) -> None:
        plan = self.ectx.dossier_plan
        model = self.ectx.knowledge
        entries = self.ectx.ledger(pc.PHASE_WRITE).entries()
        for key, payload in entries.items():
            doc = key.removeprefix("doc:")
            if docs is not None and doc not in docs:
                continue
            sections = payload.get("sections") or {}
            if doc == CATALOGUE_DOC:
                markdown, provenance = render_catalogue(plan, sections, model, self.exact.units, self.exact.capability)
            else:
                entry = next((d for d in plan.dossiers if f"INTEGRATION__{d.slug}" == doc), None)
                if entry is None:
                    continue
                markdown, provenance = render_dossier(entry, sections, model, self.exact.units, plan.interfaces, self.staging)
            write_client_document(self.staging, doc, markdown, provenance)

    async def _write_phase(self) -> bool:
        plan = build_dossier_plan(self.ectx.knowledge, self.exact.units, self.exact.interfaces,
                                  min_designs=self.budgets.dossier_min_designs)
        write_dossier_plan(self.staging, plan)
        self.ectx.dossier_plan = plan
        await self._log(f"document plan: {len(plan.dossiers)} dossier(s), {len(plan.catalogue_only)} catalogue-only group(s), "
                        f"{len(plan.interfaces)} interfaces")
        complete = await self._run_ledger_phase(pc.PHASE_WRITE)
        self._render_docs()
        return complete

    async def _review_and_repair(self) -> list[dict[str, Any]]:
        plan = self.ectx.dossier_plan
        findings: list[dict[str, Any]] = []
        for attempt in range(1 + self.budgets.max_repair_attempts):
            self.ectx.ledger(pc.PHASE_REVIEW).clear()
            self._record(pc.PHASE_REVIEW).status = "pending"
            await self._run_ledger_phase(pc.PHASE_REVIEW)
            model_findings = list((self.ectx.ledger(pc.PHASE_REVIEW).entries().get("review") or {}).get("findings") or [])
            deterministic = deterministic_review(self.staging, plan, self.ectx.knowledge)
            findings = [*deterministic, *model_findings]
            write_json(self.staging / REVIEW_REPORT, {"attempt": attempt + 1, "findings": findings,
                                                      "blocking": sum(f["severity"] == "blocking" for f in findings)})
            blocking = {f["doc"] for f in findings if f["severity"] == "blocking"}
            if not blocking:
                return findings
            if attempt >= self.budgets.max_repair_attempts:
                break
            await self._log(f"review found blocking findings in {len(blocking)} document(s); targeted repair", "warning")
            for doc in sorted(blocking):
                self.ectx.ledger(pc.PHASE_WRITE).remove(f"doc:{doc}")
            repair_record = self._record(pc.PHASE_REPAIR)
            repair_record.status = "running"
            for doc in sorted(blocking):
                doc_findings = [f for f in findings if f["doc"] == doc and f["severity"] == "blocking"]
                feedback = "\n".join(f"- [{f['kind']}] {f.get('section') or 'document'}: {f['detail']}" for f in doc_findings)
                self._check_budget()
                await self._session(pc.PHASE_REPAIR, [doc], "repair", feedback)
            self._render_docs(sorted(blocking))
            write_json(self.staging / REPAIR_LEDGER, {"attempt": attempt + 1, "repaired": sorted(blocking),
                                                      "still_missing": pc.write_remaining(self.ectx)})
        return findings

    # ------------------------------------------------------------------
    # projections
    # ------------------------------------------------------------------
    def _project_rag(self) -> None:
        model = self.ectx.knowledge
        root = self.staging / RAG_KNOWLEDGE_ROOT
        root.mkdir(parents=True, exist_ok=True)
        for journey in model.journeys:
            lines = [f"# Journey: {journey.name}", "", f"Trigger: {journey.trigger.kind} at {journey.trigger.unit_id}. "
                     f"{journey.trigger.detail}", ""]
            for step in journey.steps:
                lines.append(f"{step.order}. {step.unit_id} ({step.mechanism}): {step.description}")
            meaning = next((m for m in model.business if m.journey_id == journey.journey_id), None)
            if meaning:
                lines += ["", f"Business meaning ({meaning.status}, {meaning.source_authority}): {meaning.statement}"]
            lines += ["", "Evidence: " + "; ".join(f"{r.path} {r.locator}".strip() for r in journey.evidence),
                      f"Claim: {journey.claim_id}", ""]
            (root / f"journey__{journey.journey_id}.md").write_text("\n".join(lines), encoding="utf-8")
        for design in model.designs:
            lines = [f"# Design: {design.unit_id}", "", design.summary, "",
                     f"Developer: {design.views.developer}", f"Operator: {design.views.operator}",
                     f"Architect: {design.views.architect}", f"Business owner: {design.views.business_owner}", "",
                     "Evidence: " + "; ".join(f"{r.path} {r.locator}".strip() for r in design.evidence),
                     f"Claim: {design.claim_id}", ""]
            (root / f"design__{pc._safe_key(design.unit_id)}.md").write_text("\n".join(lines), encoding="utf-8")
        unknown_lines = ["# Open questions and contradictions", ""]
        unknown_lines += [f"- {u.subject}: {u.question} ({u.reason}) [{', '.join(u.audience) or 'all'}]" for u in model.unknowns]
        unknown_lines += [f"- Contradiction {c.contradiction_id} ({c.resolution}): {c.subject}" for c in model.contradictions]
        (root / "unknowns.md").write_text("\n".join(unknown_lines) + "\n", encoding="utf-8")
        # Group analyses for the technical-analysis RAG layer and the incremental cache.
        analysis_dir = self.staging / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        by_group: dict[str, list[str]] = {}
        for design in model.designs:
            group = str(self.exact.units.get(design.unit_id, {}).get("group") or "Global")
            by_group.setdefault(group, []).append(f"- **{design.unit_id}**: {design.summary} <!-- claim:{design.claim_id} -->")
        for group in self.facts.groups:
            body = "\n".join(by_group.get(group, [])) or "- No accepted design analysis for this group."
            (analysis_dir / f"{group}.md").write_text(
                f"# Phase 2 Analysis: {group}\n\n### GROUP_SUMMARY\n\nEnterprise knowledge model projection.\n\n"
                f"### DESIGNS\n\n{body}\n", encoding="utf-8")

    def _receipt(self, status: str, error: str = "") -> dict[str, Any]:
        receipt = {
            "schema_version": "enterprise-run-receipt-1", "contract": CONTRACT_VERSION,
            "status": status, "error": error, "stop_reason": self.state.stop_reason,
            "snapshot_id": self.state.snapshot_id, "fingerprint": self.state.fingerprint,
            "model": {"route": self.state.model_route, "source": self.state.model_source},
            "budgets": self.budgets.model_dump(mode="json"),
            "usage": {"processed_tokens": self.state.processed_tokens, "cost_usd": round(self.state.cost_usd, 6),
                      "sessions": self.state.sessions,
                      "elapsed_seconds": round(time.time() - self.state.started_at, 1) if self.state.started_at else None},
            "phases": {name: {"status": r.status, "attempts": len(r.attempts), "remaining": len(r.remaining),
                              "gate_errors": r.gate_errors[:20]} for name, r in self.state.phases.items()},
            "method_version": self.profile.get("method_version"),
        }
        write_json(self.staging / RUN_RECEIPT, receipt)
        return receipt

    # ------------------------------------------------------------------
    # main
    # ------------------------------------------------------------------
    async def run(self) -> tuple[bool, str, list[str]]:
        """Returns (success, error, required_docs)."""
        try:
            await self.prepare()
            for phase in pc.MODEL_PHASES:
                if not await self._run_ledger_phase(phase):
                    self.state.stop_reason = self.state.stop_reason or f"{phase}_incomplete"
                    self._receipt("incomplete", f"{phase} did not account for every key")
                    return False, f"Enterprise run incomplete: {phase} left {len(self._record(phase).remaining)} key(s) unaccounted; staging preserved for resume", []
            model = await self._assemble_and_validate()
            if model is None:
                self.state.stop_reason = self.state.stop_reason or "knowledge_model_invalid"
                self._receipt("incomplete", "knowledge model failed validation after repair")
                return False, "Enterprise run incomplete: the knowledge model did not pass grounding/completeness after targeted repair; writing is blocked", []
            write_question_coverage(self.staging, model, self.exact.units)
            if not await self._write_phase():
                self.state.stop_reason = self.state.stop_reason or "writing_incomplete"
                self._receipt("incomplete", "not every planned document was written")
                return False, "Enterprise run incomplete: planned documents missing; staging preserved for resume", []
            findings = await self._review_and_repair()
            self._project_rag()
            required = list(self.ectx.dossier_plan.required_docs)
            # The verdict reads the receipt, so record completion first; the
            # final status below only refines it with the verdict outcome.
            self._receipt("complete")
            verdict = evaluate_publication(self.staging)
            write_enterprise_gates(self.staging, verdict)
            self._receipt("complete" if verdict.ok else "complete_not_client_ready")
            blocking = [f for f in findings if f["severity"] == "blocking"]
            if blocking:
                await self._log(f"{len(blocking)} blocking review finding(s) remain after repair; publication will be held", "warning")
            return True, "", required
        except BudgetExhausted as exc:
            self.state.stop_reason = exc.reason
            self._save_state()
            self._receipt("incomplete", f"budget exhausted: {exc.reason}")
            return False, f"Enterprise run incomplete: {exc.reason}; no absence was inferred and staging is preserved for resume", []
        except EnterpriseWorkspaceError as exc:
            self._receipt("failed", str(exc))
            return False, f"Enterprise run cannot start: {exc}", []
        finally:
            if self.ectx is not None:
                unregister_context(self.staging)
