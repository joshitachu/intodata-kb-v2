"""Phase contracts: durable ledgers, typed submissions, gates and assembly.

Each phase accepts submissions in small batches through the artifact facade.
Accepted entries are persisted immediately into a per-phase ledger (one JSON
file per key), so a session that dies, times out or exhausts its budget loses
nothing that was already accepted, and a later session only sees what is still
remaining. The remaining set is a scheduling record, never a read boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from mcp_server.providers.custom.enterprise import knowledge_model as km
from mcp_server.providers.custom.enterprise.client_language import leakage_findings
from mcp_server.providers.custom.enterprise.integration_projection import (
    CATALOGUE_SECTIONS,
    DOSSIER_SECTIONS,
    DossierPlan,
)
from mcp_server.providers.custom.enterprise.layout import (
    CATALOGUE_DOC,
    PHASES_ROOT,
    RUNTIME_AUTHORITY_JSON,
)
from mcp_server.providers.custom.enterprise.workspace import ExactTables
from mcp_server.providers.custom.knowledge_contracts import write_json

PHASE_DEEP_ANALYZE = "deep_analyze_estate"
PHASE_RECONSTRUCT = "reconstruct_integrations"
PHASE_MODEL = "build_knowledge_model"
PHASE_RECONCILE = "reconcile_authorities"
PHASE_WRITE = "write_catalogue_dossiers"
PHASE_REVIEW = "review"
PHASE_REPAIR = "targeted_repair"

MODEL_PHASES: tuple[str, ...] = (PHASE_DEEP_ANALYZE, PHASE_RECONSTRUCT, PHASE_MODEL, PHASE_RECONCILE)
PHASE_ORDER: tuple[str, ...] = (*MODEL_PHASES, PHASE_WRITE, PHASE_REVIEW, PHASE_REPAIR)

#: Minimum prose per client section. A depth floor, not a target.
MIN_SECTION_CHARS = 160
_SHORT_SECTIONS = {"how_to_read", "risks_and_open_questions", "estate_observations"}


class EnterpriseContractError(ValueError):
    """A submission violates its phase contract."""


def _safe_key(key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return f"{cleaned}__{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


class PhaseLedger:
    """Atomic per-key JSON records under ``phases/<phase>/ledger``."""

    def __init__(self, staging_dir: Path, phase: str) -> None:
        self.phase = phase
        self.root = staging_dir / PHASES_ROOT / phase
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

    def _lock(self):
        lock_path = self.root / ".lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def accept(self, key: str, payload: dict[str, Any]) -> None:
        handle = self._lock()
        try:
            write_json(self.ledger_dir / f"{_safe_key(key)}.json", {"key": key, "accepted_at": time.time(), "payload": payload})
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def remove(self, key: str) -> None:
        (self.ledger_dir / f"{_safe_key(key)}.json").unlink(missing_ok=True)

    def entries(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for path in sorted(self.ledger_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and isinstance(record.get("key"), str):
                out[record["key"]] = record.get("payload") or {}
        return out

    def keys(self) -> set[str]:
        return set(self.entries())

    def clear(self) -> None:
        for path in self.ledger_dir.glob("*.json"):
            path.unlink(missing_ok=True)

    def audit(self, record: dict[str, Any]) -> None:
        path = self.root / "submissions.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": time.time(), **record}, default=str, ensure_ascii=True) + "\n")
        except OSError:
            pass


@dataclass
class EnterpriseContext:
    staging_dir: Path
    project_id: str
    snapshot_id: str
    exact: ExactTables
    unit_ids: set[str]
    dossier_plan: DossierPlan | None = None
    knowledge: km.KnowledgeModel | None = None
    claim_ids: set[str] = field(default_factory=set)

    def ledger(self, phase: str) -> PhaseLedger:
        return PhaseLedger(self.staging_dir, phase)


@dataclass
class SubmissionResult:
    ok: bool
    accepted: list[str]
    rejected: list[dict[str, Any]]
    remaining: list[str]
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "accepted_count": len(self.accepted), "accepted_keys": self.accepted[:50],
            "rejected": self.rejected[:50], "remaining_count": len(self.remaining),
            "remaining": self.remaining[:200], "note": self.note,
        }


def _parse_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnterpriseContractError(f"submission is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise EnterpriseContractError("submission must be a JSON object")
    return payload


def _list(payload: dict[str, Any], name: str) -> list[Any]:
    value = payload.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise EnterpriseContractError(f"{name} must be a list")
    return value


def _ground(ectx: EnterpriseContext, refs: list[km.EvidenceRef], where: str, *, required: bool = True) -> list[str]:
    errors: list[str] = []
    km._check_evidence(ectx.staging_dir, refs, where, errors, {}, required=required)
    return errors


def _validate(model_type: type[BaseModel], raw: Any, index: int, name: str) -> tuple[BaseModel | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"{name}[{index}]: entry must be an object"
    raw = {k: v for k, v in raw.items() if k != "claim_id"}
    try:
        return model_type.model_validate(raw), None
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        return None, f"{name}[{index}]: {loc}: {first.get('msg', 'invalid')}"


# --------------------------------------------------------------------------
# Phase 1: deep analyze estate
# --------------------------------------------------------------------------

def deep_analyze_keys(ectx: EnterpriseContext) -> list[str]:
    return sorted(ectx.unit_ids)


def deep_analyze_remaining(ectx: EnterpriseContext) -> list[str]:
    done = {k.removeprefix("design:") for k in ectx.ledger(PHASE_DEEP_ANALYZE).keys() if k.startswith("design:")}
    return sorted(ectx.unit_ids - done)


def handle_deep_analyze(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_DEEP_ANALYZE)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(payload, "designs")):
        entry, error = _validate(km.DesignEntry, raw, index, "designs")
        if entry is None:
            rejected.append({"section": "designs", "index": index, "errors": [error]})
            continue
        errors: list[str] = []
        if entry.unit_id not in ectx.unit_ids:
            errors.append(f"unknown design {entry.unit_id}; use canonical ids from _analysis/_exact/units.json")
        errors += _ground(ectx, entry.evidence, f"designs[{entry.unit_id}]")
        if errors:
            rejected.append({"section": "designs", "index": index, "key": entry.unit_id, "errors": errors})
            continue
        ledger.accept(f"design:{entry.unit_id}", entry.model_dump(mode="json"))
        accepted.append(f"design:{entry.unit_id}")
    for index, raw in enumerate(_list(payload, "systems")):
        entry, error = _validate(km.SystemEntry, raw, index, "systems")
        if entry is None:
            rejected.append({"section": "systems", "index": index, "errors": [error]})
            continue
        errors = _ground(ectx, entry.evidence, f"systems[{entry.system_id}]")
        if errors:
            rejected.append({"section": "systems", "index": index, "key": entry.system_id, "errors": errors})
            continue
        ledger.accept(f"system:{entry.system_id}", entry.model_dump(mode="json"))
        accepted.append(f"system:{entry.system_id}")
    accepted += _accept_unknowns(ectx, ledger, payload, rejected)
    remaining = deep_analyze_remaining(ectx)
    ledger.audit({"phase": PHASE_DEEP_ANALYZE, "accepted": len(accepted), "rejected": len(rejected), "remaining": len(remaining)})
    return SubmissionResult(ok=not rejected, accepted=accepted, rejected=rejected, remaining=remaining,
                            note="remaining lists designs without an accepted analysis")


def _accept_unknowns(ectx: EnterpriseContext, ledger: PhaseLedger, payload: dict[str, Any], rejected: list[dict[str, Any]]) -> list[str]:
    accepted: list[str] = []
    for index, raw in enumerate(_list(payload, "unknowns")):
        entry, error = _validate(km.Unknown, raw, index, "unknowns")
        if entry is None:
            rejected.append({"section": "unknowns", "index": index, "errors": [error]})
            continue
        ledger.accept(f"unknown:{entry.unknown_id}", entry.model_dump(mode="json"))
        accepted.append(f"unknown:{entry.unknown_id}")
    return accepted


# --------------------------------------------------------------------------
# Phase 2: reconstruct end-to-end integrations
# --------------------------------------------------------------------------

def _placed_units(ectx: EnterpriseContext) -> tuple[set[str], set[str]]:
    placed: set[str] = set()
    excluded: set[str] = set()
    for key, payload in ectx.ledger(PHASE_RECONSTRUCT).entries().items():
        if key.startswith("journey:"):
            placed.add(str(payload.get("trigger", {}).get("unit_id") or ""))
            placed.update(str(u) for u in payload.get("implementing_units", []))
            placed.update(str(s.get("unit_id")) for s in payload.get("steps", []) if isinstance(s, dict))
        elif key.startswith("excluded:"):
            excluded.add(key.removeprefix("excluded:"))
    return placed, excluded


def reconstruct_keys(ectx: EnterpriseContext) -> list[str]:
    return sorted(ectx.unit_ids)


def reconstruct_remaining(ectx: EnterpriseContext) -> list[str]:
    placed, excluded = _placed_units(ectx)
    return sorted(ectx.unit_ids - placed - excluded)


def handle_reconstruct(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_RECONSTRUCT)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    tables = set(ectx.exact.mapping_table_ids)
    for index, raw in enumerate(_list(payload, "journeys")):
        entry, error = _validate(km.Journey, raw, index, "journeys")
        if entry is None:
            rejected.append({"section": "journeys", "index": index, "errors": [error]})
            continue
        errors: list[str] = []
        for unit in {entry.trigger.unit_id, *entry.implementing_units, *(s.unit_id for s in entry.steps)}:
            if unit not in ectx.unit_ids:
                errors.append(f"unknown design {unit}")
        for table_id in entry.mapping_table_ids:
            if table_id not in tables:
                errors.append(f"unknown mapping table {table_id}; ids are in _analysis/_exact/mapping_accounting.json")
        if entry.error_path_ids:
            errors.append("error_path_ids are linked deterministically later; leave the list empty")
        orders = [s.order for s in entry.steps]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            errors.append("step order must be strictly increasing")
        errors += _ground(ectx, entry.evidence, f"journeys[{entry.journey_id}]")
        for step in entry.steps:
            errors += _ground(ectx, step.evidence, f"journeys[{entry.journey_id}].step{step.order}", required=False)
        if errors:
            rejected.append({"section": "journeys", "index": index, "key": entry.journey_id, "errors": errors})
            continue
        ledger.accept(f"journey:{entry.journey_id}", entry.model_dump(mode="json"))
        accepted.append(f"journey:{entry.journey_id}")
    for index, raw in enumerate(_list(payload, "excluded_units")):
        entry, error = _validate(km.ExcludedUnit, raw, index, "excluded_units")
        if entry is None:
            rejected.append({"section": "excluded_units", "index": index, "errors": [error]})
            continue
        if entry.unit_id not in ectx.unit_ids:
            rejected.append({"section": "excluded_units", "index": index, "key": entry.unit_id, "errors": ["unknown design"]})
            continue
        ledger.accept(f"excluded:{entry.unit_id}", entry.model_dump(mode="json"))
        accepted.append(f"excluded:{entry.unit_id}")
    remaining = reconstruct_remaining(ectx)
    entry_points = sorted(set(ectx.exact.entry_point_units) & set(remaining))
    ledger.audit({"phase": PHASE_RECONSTRUCT, "accepted": len(accepted), "rejected": len(rejected), "remaining": len(remaining)})
    return SubmissionResult(ok=not rejected, accepted=accepted, rejected=rejected, remaining=remaining,
                            note=f"{len(entry_points)} unplaced entry points: {', '.join(entry_points[:20])}")


# --------------------------------------------------------------------------
# Phase 3: build the enterprise knowledge model
# --------------------------------------------------------------------------

def model_keys(ectx: EnterpriseContext) -> list[str]:
    keys = [f"mapping:{t}" for t in ectx.exact.mapping_table_ids]
    keys += [f"config:context_group:{k}" for k in ectx.exact.context_group_keys]
    keys += [f"config:unit:{u}" for u in ectx.exact.inline_context_units]
    keys += [f"error:{u}" for u in ectx.exact.error_handling_units]
    journeys = [k.removeprefix("journey:") for k in ectx.ledger(PHASE_RECONSTRUCT).keys() if k.startswith("journey:")]
    keys += [f"business:{j}" for j in sorted(journeys)]
    return sorted(keys)


def model_remaining(ectx: EnterpriseContext) -> list[str]:
    done = ectx.ledger(PHASE_MODEL).keys()
    satisfied: set[str] = set()
    for key in done:
        if key.startswith("error_path:"):
            continue
        satisfied.add(key)
    for key, payload in ectx.ledger(PHASE_MODEL).entries().items():
        if key.startswith("error_path:"):
            satisfied.add(f"error:{payload.get('unit_id')}")
    return sorted(set(model_keys(ectx)) - satisfied)


def handle_model(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_MODEL)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    tables = set(ectx.exact.mapping_table_ids)
    journeys = {k.removeprefix("journey:") for k in ectx.ledger(PHASE_RECONSTRUCT).keys() if k.startswith("journey:")}
    for index, raw in enumerate(_list(payload, "mappings")):
        entry, error = _validate(km.MappingInterpretation, raw, index, "mappings")
        if entry is None:
            rejected.append({"section": "mappings", "index": index, "errors": [error]})
            continue
        errors: list[str] = []
        if entry.table_id not in tables:
            errors.append(f"unknown mapping table {entry.table_id}")
        if km._looks_like_field_table(entry.summary) or any(km._looks_like_field_table(r) for r in entry.rules):
            errors.append("do not retype the mapping table; explain it and reference the exact table")
        errors += _ground(ectx, entry.evidence, f"mappings[{entry.table_id}]")
        if errors:
            rejected.append({"section": "mappings", "index": index, "key": entry.table_id, "errors": errors})
            continue
        ledger.accept(f"mapping:{entry.table_id}", entry.model_dump(mode="json"))
        accepted.append(f"mapping:{entry.table_id}")
    for index, raw in enumerate(_list(payload, "configuration")):
        entry, error = _validate(km.ConfigurationEntry, raw, index, "configuration")
        if entry is None:
            rejected.append({"section": "configuration", "index": index, "errors": [error]})
            continue
        key = f"config:{entry.scope}:{entry.key}"
        errors = _ground(ectx, entry.evidence, f"configuration[{entry.key}]")
        if entry.scope == "unit" and entry.key not in ectx.unit_ids:
            errors.append(f"unknown design {entry.key}")
        if entry.scope == "context_group" and entry.key not in set(ectx.exact.context_group_keys):
            errors.append(f"unknown context group {entry.key}; keys are in _analysis/_exact/configuration.json")
        if errors:
            rejected.append({"section": "configuration", "index": index, "key": entry.key, "errors": errors})
            continue
        ledger.accept(key, entry.model_dump(mode="json"))
        accepted.append(key)
    for index, raw in enumerate(_list(payload, "error_paths")):
        entry, error = _validate(km.ErrorPath, raw, index, "error_paths")
        if entry is None:
            rejected.append({"section": "error_paths", "index": index, "errors": [error]})
            continue
        errors = _ground(ectx, entry.evidence, f"error_paths[{entry.error_path_id}]")
        if entry.unit_id not in ectx.unit_ids:
            errors.append(f"unknown design {entry.unit_id}")
        if errors:
            rejected.append({"section": "error_paths", "index": index, "key": entry.error_path_id, "errors": errors})
            continue
        ledger.accept(f"error_path:{entry.error_path_id}", entry.model_dump(mode="json"))
        accepted.append(f"error_path:{entry.error_path_id}")
    for index, raw in enumerate(_list(payload, "business")):
        entry, error = _validate(km.BusinessMeaning, raw, index, "business")
        if entry is None:
            rejected.append({"section": "business", "index": index, "errors": [error]})
            continue
        errors = []
        if entry.journey_id not in journeys:
            errors.append(f"unknown journey {entry.journey_id}")
        if entry.status == "evidenced" and (not entry.evidence or entry.source_authority == "inference"):
            errors.append("an evidenced business meaning needs evidence from documented intent, code or runtime")
        errors += _ground(ectx, entry.evidence, f"business[{entry.journey_id}]", required=False)
        if errors:
            rejected.append({"section": "business", "index": index, "key": entry.journey_id, "errors": errors})
            continue
        ledger.accept(f"business:{entry.journey_id}", entry.model_dump(mode="json"))
        accepted.append(f"business:{entry.journey_id}")
    accepted += _accept_unknowns(ectx, ledger, payload, rejected)
    remaining = model_remaining(ectx)
    ledger.audit({"phase": PHASE_MODEL, "accepted": len(accepted), "rejected": len(rejected), "remaining": len(remaining)})
    return SubmissionResult(ok=not rejected, accepted=accepted, rejected=rejected, remaining=remaining,
                            note="keys: mapping:<table_id>, config:context_group:<key>, config:unit:<unit>, error:<unit>, business:<journey>")


# --------------------------------------------------------------------------
# Phase 4: reconcile code / design / runtime
# --------------------------------------------------------------------------

def reconcile_keys(ectx: EnterpriseContext) -> list[str]:
    return ["reconciliation"]


def reconcile_remaining(ectx: EnterpriseContext) -> list[str]:
    return [] if "reconciliation" in ectx.ledger(PHASE_RECONCILE).keys() else ["reconciliation"]


def deterministic_runtime_observations(ectx: EnterpriseContext) -> list[km.RuntimeObservation]:
    """Every design gets an explicit runtime status from the authority bundle."""
    path = ectx.staging_dir / RUNTIME_AUTHORITY_JSON
    bundle: dict[str, Any] = {}
    if path.is_file():
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bundle = {}
    observations = {str(o.get("unit_name")): o for o in bundle.get("observations", []) or [] if isinstance(o, dict)}
    gaps = {str(g.get("unit_name")): str(g.get("status")) for g in bundle.get("deployed_set_gaps", []) or [] if isinstance(g, dict)}
    authorities = bundle.get("authorities") or {}
    tmc_status = str(authorities.get("tmc") or "unavailable")
    window = bundle.get("window_days")
    evidence_path = RUNTIME_AUTHORITY_JSON.as_posix()
    out: list[km.RuntimeObservation] = []
    for unit_id in sorted(ectx.unit_ids):
        unit = ectx.exact.units.get(unit_id, {})
        if unit.get("kind") == "route":
            out.append(km.RuntimeObservation(unit_id=unit_id, status="not_applicable",
                                             summary="Talend Management Console execution metrics cover DI jobs only; routes run as Camel microservices.",
                                             authority="tmc"))
            continue
        observation = observations.get(unit_id)
        if observation is not None:
            envs = [str(observation.get("environment") or "")] if observation.get("environment") else []
            summary = (f"{observation.get('total_runs', 0)} runs, {observation.get('failed_runs', 0)} failed "
                       f"(failure rate {float(observation.get('failure_rate') or 0.0):.1%}); last run {observation.get('last_run_at') or 'unknown'}.")
            out.append(km.RuntimeObservation(unit_id=unit_id, status="observed", summary=summary, environments=envs,
                                             window_days=int(window) if isinstance(window, int) else None,
                                             authority=str(observation.get("provenance", {}).get("authority") or "tmc"),
                                             evidence=[km.EvidenceRef(path=evidence_path, locator=f"observations[unit_name={unit_id}]")]))
            continue
        if tmc_status == "unavailable" or not bundle:
            out.append(km.RuntimeObservation(unit_id=unit_id, status="unavailable",
                                             summary="No runtime source was available for this run; absence is not evidence of non-execution.",
                                             authority="tmc"))
        elif tmc_status == "opted_out":
            out.append(km.RuntimeObservation(unit_id=unit_id, status="unavailable",
                                             summary="Runtime evidence was switched off for this run (code-only run); nothing was looked up.",
                                             authority="tmc"))
        elif gaps.get(unit_id) == "lookup_failed":
            out.append(km.RuntimeObservation(unit_id=unit_id, status="lookup_failed",
                                             summary="The runtime lookup for this design did not complete.", authority="tmc",
                                             window_days=int(window) if isinstance(window, int) else None))
        else:
            out.append(km.RuntimeObservation(unit_id=unit_id, status="not_observed_in_window",
                                             summary="No execution record in the read window; child jobs and joblets are commonly not scheduled on their own.",
                                             authority="tmc", window_days=int(window) if isinstance(window, int) else None))
    return out


def handle_reconcile(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_RECONCILE)
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(payload, "contradictions")):
        entry, error = _validate(km.Contradiction, raw, index, "contradictions")
        if entry is None:
            rejected.append({"section": "contradictions", "index": index, "errors": [error]})
            continue
        errors = _ground(ectx, entry.evidence, f"contradictions[{entry.contradiction_id}]")
        if len(set(entry.authorities)) < 2:
            errors.append("a contradiction needs two distinct authorities")
        if errors:
            rejected.append({"section": "contradictions", "index": index, "key": entry.contradiction_id, "errors": errors})
            continue
        ledger.accept(f"contradiction:{entry.contradiction_id}", entry.model_dump(mode="json"))
        accepted.append(f"contradiction:{entry.contradiction_id}")
    accepted += _accept_unknowns(ectx, ledger, payload, rejected)
    if not rejected:
        ledger.accept("reconciliation", {"contradiction_count": sum(1 for k in ledger.keys() if k.startswith("contradiction:")),
                                         "confirmations": [str(c) for c in _list(payload, "confirmations")][:200]})
        accepted.append("reconciliation")
    ledger.audit({"phase": PHASE_RECONCILE, "accepted": len(accepted), "rejected": len(rejected)})
    return SubmissionResult(ok=not rejected, accepted=accepted, rejected=rejected, remaining=reconcile_remaining(ectx),
                            note="submit an empty contradictions list when the authorities agree")


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def assemble_knowledge_model(ectx: EnterpriseContext) -> km.KnowledgeModel:
    designs, systems, unknowns = [], [], []
    for key, payload in ectx.ledger(PHASE_DEEP_ANALYZE).entries().items():
        if key.startswith("design:"):
            designs.append(km.DesignEntry.model_validate(payload))
        elif key.startswith("system:"):
            systems.append(km.SystemEntry.model_validate(payload))
        elif key.startswith("unknown:"):
            unknowns.append(km.Unknown.model_validate(payload))
    journeys, excluded = [], []
    for key, payload in ectx.ledger(PHASE_RECONSTRUCT).entries().items():
        if key.startswith("journey:"):
            journeys.append(km.Journey.model_validate(payload))
        elif key.startswith("excluded:"):
            excluded.append(km.ExcludedUnit.model_validate(payload))
    mappings, configuration, error_paths, business = [], [], [], []
    for key, payload in ectx.ledger(PHASE_MODEL).entries().items():
        if key.startswith("mapping:"):
            mappings.append(km.MappingInterpretation.model_validate(payload))
        elif key.startswith("config:"):
            configuration.append(km.ConfigurationEntry.model_validate(payload))
        elif key.startswith("error_path:"):
            error_paths.append(km.ErrorPath.model_validate(payload))
        elif key.startswith("business:"):
            business.append(km.BusinessMeaning.model_validate(payload))
        elif key.startswith("unknown:"):
            unknowns.append(km.Unknown.model_validate(payload))
    contradictions = []
    for key, payload in ectx.ledger(PHASE_RECONCILE).entries().items():
        if key.startswith("contradiction:"):
            contradictions.append(km.Contradiction.model_validate(payload))
        elif key.startswith("unknown:"):
            unknowns.append(km.Unknown.model_validate(payload))
    # Deterministic linking: error paths belong to the journeys their unit implements.
    paths_by_unit: dict[str, list[str]] = {}
    for path in error_paths:
        paths_by_unit.setdefault(path.unit_id, []).append(path.error_path_id)
    for journey in journeys:
        linked = sorted({pid for unit in {journey.trigger.unit_id, *journey.implementing_units, *(s.unit_id for s in journey.steps)}
                         for pid in paths_by_unit.get(unit, [])})
        journey.error_path_ids = linked
    seen_unknown: set[str] = set()
    unique_unknowns = []
    for unknown in unknowns:
        if unknown.unknown_id not in seen_unknown:
            seen_unknown.add(unknown.unknown_id)
            unique_unknowns.append(unknown)
    model = km.KnowledgeModel(
        snapshot_id=ectx.snapshot_id, project_id=ectx.project_id,
        designs=sorted(designs, key=lambda d: d.unit_id), systems=sorted(systems, key=lambda s: s.system_id),
        journeys=sorted(journeys, key=lambda j: j.journey_id), excluded_units=sorted(excluded, key=lambda e: e.unit_id),
        mappings=sorted(mappings, key=lambda m: m.table_id), dependencies=ectx.exact.dependencies,
        configuration=sorted(configuration, key=lambda c: (c.scope, c.key)),
        error_paths=sorted(error_paths, key=lambda e: e.error_path_id),
        runtime=deterministic_runtime_observations(ectx), business=sorted(business, key=lambda b: b.journey_id),
        contradictions=sorted(contradictions, key=lambda c: c.contradiction_id),
        unknowns=sorted(unique_unknowns, key=lambda u: u.unknown_id),
    )
    return km.assign_claim_ids(model)


def validate_assembled_model(ectx: EnterpriseContext, model: km.KnowledgeModel) -> km.ValidationReport:
    return km.validate_knowledge_model(
        model, staging_dir=ectx.staging_dir, unit_ids=ectx.unit_ids,
        mapping_table_ids=set(ectx.exact.mapping_table_ids), entry_point_units=set(ectx.exact.entry_point_units),
        error_handling_units=set(ectx.exact.error_handling_units), context_group_keys=set(ectx.exact.context_group_keys),
        inline_context_units=set(ectx.exact.inline_context_units),
    )


def repair_targets_from_validation(report: km.ValidationReport) -> dict[str, list[str]]:
    """Map validation failures back to the phase and ledger keys that must be resubmitted."""
    targets: dict[str, list[str]] = {}
    for section, keys in report.failed_entries.items():
        if section in {"designs", "systems"}:
            phase = PHASE_DEEP_ANALYZE
        elif section == "journeys":
            phase = PHASE_RECONSTRUCT
        elif section in {"mappings", "configuration", "error_paths", "business", "unknowns"}:
            phase = PHASE_MODEL
        elif section == "contradictions":
            phase = PHASE_RECONCILE
        else:
            continue
        targets.setdefault(phase, []).extend(f"{section}:{key}" for key in keys)
    return {phase: sorted(set(keys)) for phase, keys in targets.items()}


# --------------------------------------------------------------------------
# Phase 5: write catalogue and dossiers
# --------------------------------------------------------------------------

def write_keys(ectx: EnterpriseContext) -> list[str]:
    if ectx.dossier_plan is None:
        return []
    return list(ectx.dossier_plan.required_docs)


def write_remaining(ectx: EnterpriseContext) -> list[str]:
    done = {k.removeprefix("doc:") for k in ectx.ledger(PHASE_WRITE).keys() if k.startswith("doc:")}
    return [doc for doc in write_keys(ectx) if doc not in done]


def _section_errors(doc: str, sections: Any, required: tuple[str, ...], claim_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(sections, dict):
        return ["sections must be an object keyed by section name"]
    for name in required:
        section = sections.get(name)
        if not isinstance(section, dict):
            errors.append(f"section {name} is missing")
            continue
        text = str(section.get("text") or "").strip()
        minimum = 40 if name in _SHORT_SECTIONS else MIN_SECTION_CHARS
        if len(text) < minimum:
            errors.append(f"section {name} is too thin ({len(text)} chars; minimum {minimum})")
        leaks = leakage_findings(text)
        if leaks:
            errors.append(f"section {name} leaks internal provenance: {leaks[0]}")
        if km._looks_like_field_table(text):
            errors.append(f"section {name} retypes a mapping table; exact tables are inserted deterministically")
        ids = section.get("claim_ids")
        if not isinstance(ids, list):
            errors.append(f"section {name} needs a claim_ids list")
            continue
        unknown = [str(i) for i in ids if str(i) not in claim_ids]
        if unknown:
            errors.append(f"section {name} cites unknown claim ids: {', '.join(unknown[:5])}")
        if name not in _SHORT_SECTIONS and not ids:
            errors.append(f"section {name} must cite at least one knowledge-model claim id")
    extra = sorted(set(sections) - set(required))
    if extra:
        errors.append(f"unexpected sections for {doc}: {', '.join(extra)}")
    return errors


def handle_write(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_WRITE)
    if ectx.dossier_plan is None or ectx.knowledge is None:
        raise EnterpriseContractError("writing is not open: the knowledge model has not passed validation")
    doc = str(payload.get("doc") or "")
    if doc not in set(ectx.dossier_plan.required_docs):
        raise EnterpriseContractError(f"unknown document {doc!r}; required documents: {', '.join(ectx.dossier_plan.required_docs)}")
    required = CATALOGUE_SECTIONS if doc == CATALOGUE_DOC else DOSSIER_SECTIONS
    errors = _section_errors(doc, payload.get("sections"), required, ectx.claim_ids)
    if errors:
        ledger.audit({"phase": PHASE_WRITE, "doc": doc, "rejected": errors})
        return SubmissionResult(ok=False, accepted=[], rejected=[{"section": "sections", "key": doc, "errors": errors}],
                                remaining=write_remaining(ectx))
    sections = {name: {"text": str(payload["sections"][name].get("text") or "").strip(),
                       "claim_ids": [str(i) for i in payload["sections"][name].get("claim_ids") or []]}
                for name in required}
    ledger.accept(f"doc:{doc}", {"doc": doc, "sections": sections})
    ledger.audit({"phase": PHASE_WRITE, "doc": doc, "accepted": True})
    return SubmissionResult(ok=True, accepted=[f"doc:{doc}"], rejected=[], remaining=write_remaining(ectx))


# --------------------------------------------------------------------------
# Phase 6: review
# --------------------------------------------------------------------------

def review_remaining(ectx: EnterpriseContext) -> list[str]:
    return [] if "review" in ectx.ledger(PHASE_REVIEW).keys() else ["review"]


def handle_review(ectx: EnterpriseContext, payload: dict[str, Any]) -> SubmissionResult:
    ledger = ectx.ledger(PHASE_REVIEW)
    docs = set(ectx.dossier_plan.required_docs) if ectx.dossier_plan else set()
    findings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(payload, "findings")):
        if not isinstance(raw, dict):
            rejected.append({"section": "findings", "index": index, "errors": ["finding must be an object"]})
            continue
        doc = str(raw.get("doc") or "")
        severity = str(raw.get("severity") or "")
        kind = str(raw.get("kind") or "")
        errors = []
        if doc not in docs:
            errors.append(f"unknown document {doc}")
        if severity not in {"blocking", "advisory"}:
            errors.append("severity must be blocking or advisory")
        if kind not in {"unsupported_claim", "missing_required_content", "contradiction", "leakage", "shallow", "inconsistent_with_model", "other"}:
            errors.append("unknown finding kind")
        if not str(raw.get("detail") or "").strip():
            errors.append("detail is required")
        if errors:
            rejected.append({"section": "findings", "index": index, "errors": errors})
            continue
        findings.append({"doc": doc, "section": str(raw.get("section") or ""), "severity": severity, "kind": kind,
                         "detail": str(raw.get("detail")).strip()[:2000], "claim_ids": [str(c) for c in raw.get("claim_ids") or []][:20],
                         "source": "model"})
    if rejected:
        return SubmissionResult(ok=False, accepted=[], rejected=rejected, remaining=review_remaining(ectx))
    ledger.accept("review", {"findings": findings})
    return SubmissionResult(ok=True, accepted=["review"], rejected=[], remaining=[])


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

_HANDLERS = {
    PHASE_DEEP_ANALYZE: handle_deep_analyze,
    PHASE_RECONSTRUCT: handle_reconstruct,
    PHASE_MODEL: handle_model,
    PHASE_RECONCILE: handle_reconcile,
    PHASE_WRITE: handle_write,
    PHASE_REVIEW: handle_review,
    PHASE_REPAIR: handle_write,
}

_REMAINING = {
    PHASE_DEEP_ANALYZE: deep_analyze_remaining,
    PHASE_RECONSTRUCT: reconstruct_remaining,
    PHASE_MODEL: model_remaining,
    PHASE_RECONCILE: reconcile_remaining,
    PHASE_WRITE: write_remaining,
    PHASE_REVIEW: review_remaining,
    PHASE_REPAIR: write_remaining,
}


def submission_artifact(phase: str) -> str:
    return (PHASES_ROOT / phase / "submission.json").as_posix()


def remaining_for(ectx: EnterpriseContext, phase: str) -> list[str]:
    return _REMAINING[phase](ectx)


def handle_submission(ectx: EnterpriseContext, phase: str, content: str) -> dict[str, Any]:
    handler = _HANDLERS.get(phase)
    if handler is None:
        raise EnterpriseContractError(f"unknown enterprise phase {phase}")
    payload = _parse_payload(content)
    return handler(ectx, payload).to_json()
