"""Runtime (TMC mirror) facts for topology enrichment.

Reads the staged ``_transient/runtime/runtime_evidence.json`` payload that
``providers/custom/runtime_evidence.py`` writes, through that module's own
``RuntimeEvidenceStore.from_json`` so the payload shape and the cadence
classifier are defined once.

Two rules carried over from the runtime lane:

* **Exact identity, two keys.** ``by_unit`` is keyed on the manifest unit name
  (``job_id`` or display name). A design is looked up under its ``job_id`` and
  its ``name``, exactly -- never normalised: ``X_GetLocation`` and
  ``X_GetLocations`` are two separately scheduled jobs.
* **Absence is not non-execution.** A design the mirror does not know is
  reported as an ``absence`` with the reason it is not measured; only a design
  the mirror DOES know, with zero runs in the window, is evidence of dormancy.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp_server.services.business_topology.stream_facts import Design, ScanIndex

#: Evidence type the validator accepts for runtime observations
#: (``validation._RUNTIME_EVIDENCE_TYPES``).
RUNTIME_EVIDENCE_TYPE = "observed_runtime"
RUNTIME_EVIDENCE_PREFIX = "runtime"
RUNTIME_PAYLOAD_NAME = "_transient/runtime/runtime_evidence.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def runtime_evidence_id(value: Mapping[str, Any]) -> str:
    return f"{RUNTIME_EVIDENCE_PREFIX}:{hashlib.sha256(_canonical(value)).hexdigest()[:20]}"


@dataclass(frozen=True)
class UnitRuntime:
    key: str
    job_name: str
    environments: tuple[str, ...]
    total_runs: int
    failed_runs: int
    failure_rate: float
    first_run_at: str
    last_run_at: str
    runs_per_day: float | None
    p50_duration_ms: int | None
    gap_p50_seconds: float | None
    cadence_label: str
    cadence_basis: str
    deployed_version: str

    @property
    def dominant_environment(self) -> str:
        return self.environments[0] if self.environments else ""


@dataclass
class RuntimeView:
    window_days: int
    source_key: str
    read_environments: tuple[str, ...]
    partial: bool
    units: dict[str, UnitRuntime] = field(default_factory=dict)
    unmatched: frozenset[str] = frozenset()
    unretrieved: frozenset[str] = frozenset()

    @property
    def window(self) -> str:
        return f"last {self.window_days} days"

    @property
    def read_scope(self) -> str:
        if self.read_environments:
            return "environments " + ", ".join(self.read_environments)
        return "all mirrored environments"

    def source_for(self, environments: tuple[str, ...]) -> str:
        env = ", ".join(environments) if environments else "none reported"
        return f"TMC runtime mirror, env {env}, read scope {self.read_scope}"

    def lookup(self, design: Design) -> tuple[str, UnitRuntime | None]:
        """``(status, unit)``; status is covered/unretrieved/unmatched/not_requested."""

        for key in design.keys:
            unit = self.units.get(key)
            if unit is not None:
                return "covered", unit
        if any(key in self.unretrieved for key in design.keys):
            return "unretrieved", None
        if any(key in self.unmatched for key in design.keys):
            return "unmatched", None
        return "not_requested", None


def _deployed_version(raw: Mapping[str, Any]) -> str:
    # Forward-compatible: the doc-evidence endpoint does not emit a deployed
    # version today. When it does, either key is read; nothing is inferred.
    for key in ("deployed_version", "artifact_version"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def load_runtime_view(payload: Any) -> tuple[RuntimeView | None, str]:
    """Parse the staged payload. ``(None, reason)`` when it is unusable."""

    if payload is None:
        return None, "no runtime payload supplied"
    if not isinstance(payload, Mapping):
        return None, "runtime payload is not an object"
    try:
        from mcp_server.providers.custom.runtime_evidence import (
            RuntimeEvidenceStore,
            RuntimeMode,
        )

        store = RuntimeEvidenceStore.from_json(payload)
    except Exception as exc:  # noqa: BLE001 - malformed input degrades, never raises
        return None, f"runtime payload unreadable ({type(exc).__name__})"
    if store.mode is not RuntimeMode.DATA:
        return None, f"runtime lane mode is {store.mode.value}; no runtime data in this run"
    raw_units = payload.get("by_unit") if isinstance(payload.get("by_unit"), Mapping) else {}
    view = RuntimeView(
        window_days=int(store.window_days),
        source_key=store.source_key,
        read_environments=tuple(store.read_environments),
        partial=bool(store.partial),
        unmatched=frozenset(store.unmatched_units),
        unretrieved=frozenset(store.unretrieved_units),
    )
    for key, evidence in sorted(store.by_unit.items()):
        try:
            cadence = evidence.cadence()
            label, basis = cadence.label, cadence.basis
        except Exception:  # noqa: BLE001
            label, basis = "", "no_gap_data"
        raw = raw_units.get(key) if isinstance(raw_units.get(key), Mapping) else {}
        view.units[key] = UnitRuntime(
            key=key,
            job_name=evidence.job_name,
            # Original order: the server names the DOMINANT environment first,
            # and the gap quantiles describe that environment.
            environments=tuple(evidence.environments),
            total_runs=int(evidence.total_runs),
            failed_runs=int(evidence.failed_runs),
            failure_rate=float(evidence.failure_rate),
            first_run_at=evidence.first_run_at or "",
            last_run_at=evidence.last_run_at or "",
            runs_per_day=(
                float(evidence.runs_per_day)
                if isinstance(evidence.runs_per_day, (int, float))
                else None
            ),
            p50_duration_ms=(
                int(evidence.p50_duration_ms)
                if isinstance(evidence.p50_duration_ms, (int, float))
                else None
            ),
            gap_p50_seconds=evidence.gap_p50_seconds,
            cadence_label=label,
            cadence_basis=basis,
            deployed_version=_deployed_version(raw),
        )
    return view, ""


def unit_evidence_record(view: RuntimeView, unit: UnitRuntime) -> dict[str, Any]:
    identity = {
        "kind": "unit_aggregate",
        "unit": unit.key,
        "source_key": view.source_key,
        "window_days": view.window_days,
        "read_environments": list(view.read_environments),
    }
    return {
        "id": runtime_evidence_id(identity),
        "type": RUNTIME_EVIDENCE_TYPE,
        "name": f"TMC runtime aggregate for {unit.key}",
        "runtime_unit": unit.key,
        "runtime_task": unit.job_name,
        "source_key": view.source_key,
        "window_days": view.window_days,
        "read_environments": list(view.read_environments),
        "environments": list(unit.environments),
        "total_runs": unit.total_runs,
        "failed_runs": unit.failed_runs,
        "failure_rate": unit.failure_rate,
        "first_run_at": unit.first_run_at,
        "last_run_at": unit.last_run_at,
        "gap_p50_seconds": unit.gap_p50_seconds,
        "origin": RUNTIME_PAYLOAD_NAME,
    }


def absence_evidence_record(
    view: RuntimeView, design: Design, status: str, reason: str
) -> dict[str, Any]:
    unit = design.name or design.job_id
    identity = {
        "kind": "absence",
        "unit": unit,
        "status": status,
        "source_key": view.source_key,
        "window_days": view.window_days,
        "read_environments": list(view.read_environments),
    }
    return {
        "id": runtime_evidence_id(identity),
        "type": RUNTIME_EVIDENCE_TYPE,
        "name": f"TMC runtime lookup without a record for {unit}",
        "runtime_unit": unit,
        "lookup_status": status,
        "reason": reason,
        "source_key": view.source_key,
        "window_days": view.window_days,
        "read_environments": list(view.read_environments),
        "origin": RUNTIME_PAYLOAD_NAME,
    }


def absence_reason(view: RuntimeView, index: ScanIndex, design: Design, status: str) -> str:
    """Why this design has no runtime measurement -- stated, never implied."""

    if status == "unretrieved":
        return "the runtime lookup for this design did not complete (its batch failed)"
    if design.kind in {"joblet", "routelet"}:
        return (
            f"a {design.kind} is embedded in its parent designs and never runs as its "
            "own scheduled task"
        )
    if design.is_route:
        return (
            "ESB routes run continuously inside the ESB runtime and are not TMC-scheduled "
            "tasks, so the scheduler records no executions for them"
        )
    callers = index.callers_of(design)
    if callers:
        shown = ", ".join(callers[:3]) + (f" (+{len(callers) - 3} more)" if len(callers) > 3 else "")
        return (
            f"child job invoked via tRunJob by {shown}; its executions are recorded under "
            "the calling task, not under its own name"
        )
    if status == "not_requested":
        return "this design was not part of this run's runtime request"
    if view.read_environments:
        return (
            "no runtime record under its job id or name within the read scope "
            f"({view.read_scope}); executions outside that scope are not visible"
        )
    return (
        f"no runtime record under its job id or name in the {view.window}; the design "
        "may be scheduled under a different task name or not deployed"
    )


_VERSION_PART = re.compile(r"\d+")


def compare_versions(supplied: str, deployed: str) -> str:
    left = [int(part) for part in _VERSION_PART.findall(supplied)]
    right = [int(part) for part in _VERSION_PART.findall(deployed)]
    if not left or not right:
        return "unknown"
    width = max(len(left), len(right))
    left += [0] * (width - len(left))
    right += [0] * (width - len(right))
    if left == right:
        return "equal"
    return "supplied_behind" if left < right else "supplied_ahead"


def format_rate(failed: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{failed / total:.4f}"
