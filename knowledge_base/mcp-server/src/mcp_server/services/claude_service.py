"""Claude service — coordinator for doc generation.

Handles preprocessing, resume detection, and context building, then
delegates execution to the configured DocGenerationProvider.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from mcp_server.config import config
from mcp_server.db.models import PluginPack
from mcp_server.services import instance_settings_service
from mcp_server.db.session import async_session_factory
from mcp_server.model_strategies import resolve_generation_models
from mcp_server.providers import get_provider
from mcp_server.providers.base import DocGenContext
from mcp_server.services.extraction_cache import (
    compute_extraction_key,
    extraction_cache_dir,
    restore_extraction,
)
from mcp_server.services.generation_run_state import (
    GenerationLockError,
    LockHeartbeat,
    acquire_generation_lock,
    persist_nexus_execution_receipt,
    persist_generation_profile,
    refresh_generation_lock,
    release_generation_lock,
    write_run_manifest,
)
from mcp_server.providers.custom.source_context import (
    materialize_direct_redacted_source,
    redact_inline_credentials,
)
from mcp_server.services.client_language_guard import (
    validate_client_docs,
    validate_provenance,
    write_leakage_report,
    write_provenance_report,
)
from mcp_server.providers.custom import analysis_store
from mcp_server.services.group_attribution_guard import (
    analysis_group_for_slice,
    attribution_warnings,
    check_group_attribution,
    evidence_files_for_group,
    write_group_attribution_report,
)
from mcp_server.services.generation_method import materialize_generation_method
from mcp_server.services.investigation_method import (
    InvestigationMethodError,
    materialize_investigation_method,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.plugin_runner import build_plugin_runner
from mcp_server.services.project_memory import (
    MemoryStageResult,
    persist_project_memory,
    stage_project_memory,
)
from mcp_server.services.pack_governance import PackGovernanceDenied, pack_governance
from mcp_server.services.rag_support_package import (
    LAYER_TECHNICAL_ANALYSIS,
    build_rag_support_package,
)
from mcp_server.services.nexus_execution_contract import build_nexus_execution_receipt
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scanner_service import ScannerService
from mcp_server.schemas import NexusPackSnapshot, NexusPresentation

logger = logging.getLogger(__name__)

_TALEND_NOISE_PARAMS: frozenset[str] = frozenset({
    "PREVIEW",                          # .bmp thumbnail file path
    "CONNECTION_FORMAT",                # always 'row'
    "USE_SINGLEMODE", "USE_INTABLE", "USE_INLINECONTENT",
    "LKUP_PARALLELIZE", "LEVENSHTEIN", "JACCARD",
    "ENABLE_AUTO_CONVERT_TYPE", "ROWS_BUFFER_SIZE",
    "CHANGE_HASH_AND_EQUALS_FOR_BIGDECIMAL",
    "LINK_STYLE", "TABLE_PRINT", "BASIC_MODE",
    "JDK_VERSION", "JSON_PATH_VERSION", "USE_LOOP_AS_ROOT",
    "CODEGENERATION",
})

_WORKATO_DATAPILL_RE = re.compile(r"#\{_\('([^']+)'\)([^}]*)\}")
_DESIGN_HEADING_RE = re.compile(
    r"^## (?:(?:Talend )?(?:Job|Route)|Artifact):\s*(.+?)\s*$",
)


# --------------------------------------------------------------------------
# Phase 2 work plan: how many sub-agents, and which groups each one covers
# --------------------------------------------------------------------------
#
# Measured, not assumed. On ETTM run 259 the orchestrator was told to spawn one
# sub-agent per group and did -- 20 of them, all at once. The dispatcher ran
# 3-4 at a time: analysis files landed one or two a minute from 09:29:11 to
# 09:45:34, 15 of 20 groups took ~20 minutes and Phase 3 never started.
# ``opencode run --help`` exposes no concurrency flag and the binary has no
# such knob, so the fan-out itself has to change: fewer workers, each covering
# several groups.
#
# Group sizes are wildly uneven and the unevenness differs per estate --
# measured evidence-byte spread, largest group over smallest: ETTM 86x,
# talend8 4994x -- so a constant worker count is wrong at one end or the other
# and the split is bin-packed from the estate in front of us. Exactly two
# numbers are not estate-derived: the dispatch concurrency below (a property of
# the transport, used only to report wave counts) and
# ``config.phase2_max_groups_per_worker`` (an attribution-safety bound).

#: Sub-agents the OpenCode dispatcher actually runs at once, whatever it is
#: asked for. A property of the transport, not of any estate: see run 259
#: above. Used only to report how many dispatch waves a plan implies -- the
#: worker count itself is derived from the estate.
PHASE2_OBSERVED_SUBAGENT_CONCURRENCY = 4

#: Talend artifact kinds that exist to be CALLED by another design rather than
#: to run on their own. Mirrors the scanner's own rule
#: (``intodoc/platforms/talend/parser.py``: joblet/routelet -> artifact_role
#: "reusable"), so the documentation set and the scan agree on what a design is.
REUSABLE_ARTIFACT_KINDS: frozenset[str] = frozenset({"joblet", "routelet"})

#: Smallest group analysis that counts as done on a resume. Below it the group
#: is PENDING, not done. The value is the Phase 2.5 gate's own rule -- the
#: prompt tells the model to re-spawn any group whose analysis is "MISSING or
#: under 1KB" -- so resume detection and the model's own gate agree on what
#: finished work looks like. Move them together.
#:
#: It is measured over the analysis AS A WHOLE, whichever layout stores it:
#: the file for a legacy ``_analysis/{group}.md``, the section files together
#: for ``_analysis/{group}/{SECTION}.md``. One threshold, one question ("is
#: there an analysis here"), asked through ``analysis_store`` so the two
#: layouts cannot answer it differently.
MIN_GROUP_ANALYSIS_BYTES = 1024

_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class GroupWorkItem:
    """One unit of Phase 2 work: a group, and everything Python knows about it.

    ``name`` is the ANALYSIS name (``_analysis/{name}.md``), which is not always
    a ``_preprocess/`` filename: a split group's two slices merge into one
    analysis, so ``evidence_files`` may list more than one.
    """

    name: str
    evidence_files: tuple[str, ...]
    evidence_bytes: int
    design_count: int
    risk_counts: tuple[int, ...] = (0, 0, 0, 0)  # CRITICAL, HIGH, MEDIUM, LOW

    @property
    def flagged(self) -> int:
        return sum(self.risk_counts)


@dataclass(frozen=True)
class Phase2WorkPlan:
    """The suggestion the orchestrator reads. Not an instruction: it balances
    bytes, and bytes are the only thing Python can weigh here."""

    groups: tuple[GroupWorkItem, ...]
    workers: tuple[tuple[str, ...], ...]
    target_weight_bytes: int
    composition: tuple[tuple[str, int], ...] = ()
    #: (design, kind, owning group) for every reusable artifact -- a joblet or
    #: routelet, which exists to be CALLED by another design. These drive the
    #: shared-library pre-pass; empty means the estate has none (talend8) and
    #: the pre-pass is skipped.
    reusable: tuple[tuple[str, str, str], ...] = ()
    risk_scan_available: bool = True

    @property
    def dispatch_waves(self) -> int:
        lanes = max(PHASE2_OBSERVED_SUBAGENT_CONCURRENCY, 1)
        return -(-len(self.workers) // lanes) if self.workers else 0

    @property
    def max_groups_in_a_worker(self) -> int:
        return max((len(names) for names in self.workers), default=0)

    @property
    def total_evidence_bytes(self) -> int:
        return sum(g.evidence_bytes for g in self.groups)

    @property
    def largest_group_bytes(self) -> int:
        return max((g.evidence_bytes for g in self.groups), default=0)

    @property
    def total_designs(self) -> int:
        """Designs in scope. The scan manifest's per-kind census is
        authoritative; the per-group counts are the fallback for a scan that
        has no manifest, and are zero on a resumed run that did not re-run the
        quick scan."""
        from_manifest = sum(count for _kind, count in self.composition)
        return from_manifest or sum(g.design_count for g in self.groups)

    def worker_load(self, index: int) -> int:
        by_name = {g.name: g.evidence_bytes for g in self.groups}
        return sum(by_name.get(name, 0) for name in self.workers[index])

    def reusable_designs(self) -> int:
        return sum(n for kind, n in self.composition if kind in REUSABLE_ARTIFACT_KINDS)

    def standalone_designs(self) -> int:
        return sum(
            n for kind, n in self.composition if kind not in REUSABLE_ARTIFACT_KINDS
        )


def build_group_work_items(
    preprocess_dir: Path,
    groups: Sequence[str],
    *,
    findings: Mapping[str, Sequence[tuple[str, str, str]]] | None = None,
    design_counts: Mapping[str, int] | None = None,
) -> list[GroupWorkItem]:
    """Fold ``_preprocess/`` slices into one work item per analysis group.

    ``groups`` are slice names as ``_preprocess_scan_data`` reports them, so a
    split group arrives as two entries and leaves as one item -- the two parts
    merge into a single ``_analysis/{group}.md``, and a merge split across two
    workers is a merge nobody owns.
    """
    findings = findings or {}
    design_counts = design_counts or {}
    merged: dict[str, dict] = {}
    for slice_name in groups:
        base = analysis_group_for_slice(slice_name)
        entry = merged.setdefault(
            base, {"files": [], "bytes": 0, "designs": 0, "risk": [0, 0, 0, 0]}
        )
        entry["files"].append(slice_name)
        path = preprocess_dir / f"{slice_name}.md"
        try:
            entry["bytes"] += path.stat().st_size
        except OSError:
            pass
        entry["designs"] += int(design_counts.get(slice_name, 0))
        for severity, _flag, _detail in findings.get(slice_name, ()):
            if severity in _SEVERITIES:
                entry["risk"][_SEVERITIES.index(severity)] += 1
    return [
        GroupWorkItem(
            name=name,
            evidence_files=tuple(sorted(entry["files"])),
            evidence_bytes=entry["bytes"],
            design_count=entry["designs"],
            risk_counts=tuple(entry["risk"]),
        )
        for name, entry in sorted(merged.items())
    ]


def plan_phase2_workers(
    items: Sequence[GroupWorkItem],
    *,
    max_groups_per_worker: int | None = None,
) -> tuple[list[list[str]], int]:
    """Bin-pack whole groups onto workers. Returns ``(workers, target_weight)``.

    Everything derives from the estate except ``max_groups_per_worker``, which
    is an attribution-safety bound rather than a tuning knob (see
    ``config.phase2_max_groups_per_worker``).

        weight     = a group's evidence bytes (its ``_preprocess`` slices summed)
        biggest    = the heaviest group
        by_weight  = ceil(total / biggest)
        by_safety  = ceil(n_groups / max_groups_per_worker)
        workers    = min(n_groups, max(by_weight, by_safety))

    ``biggest`` is the target weight because a group that size already has to
    be analysed by one sub-agent, so that much work in one context is proven
    workable on this estate; an oversized group then falls out as a solo worker
    on its own. Simpler rules were measured and rejected: one worker per group
    costs 47 dispatch waves on a 187-group estate, a constant worker count is
    wrong at both ends of a 12x-to-3580x spread, and the target-weight rule
    ALONE concentrates 13 groups into one context on talend8 -- which is the
    misattribution surface this whole change has to keep small.

    Packing is heaviest-group-first into the LIGHTEST worker that still has
    group-count headroom, adding a worker when every one of them is at the cap.
    Enforcing the cap only in the worker-count formula is not enough: greedy
    placement still dumped 13 groups into one bin, because a bin that is light
    in BYTES attracts every remaining small group. Deterministic: groups sort
    on (-bytes, name) and ties break on the lowest worker index, so identical
    input yields a byte-identical plan.
    """
    cap = max_groups_per_worker or config.phase2_max_groups_per_worker
    cap = max(int(cap), 1)
    ordered = sorted(items, key=lambda g: (-g.evidence_bytes, g.name))
    if not ordered:
        return [], 0

    total = sum(g.evidence_bytes for g in ordered)
    biggest = ordered[0].evidence_bytes
    by_weight = -(-total // biggest) if biggest else 1  # ceil
    by_safety = -(-len(ordered) // cap)  # ceil
    count = min(len(ordered), max(by_weight, by_safety, 1))

    bins: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for item in ordered:
        candidates = [k for k in range(len(bins)) if len(bins[k]) < cap]
        if not candidates:
            bins.append([])
            loads.append(0)
            candidates = [len(bins) - 1]
        index = min(candidates, key=lambda k: (loads[k], k))
        bins[index].append(item.name)
        loads[index] += item.evidence_bytes
    return [sorted(names) for names in bins if names], biggest


def _scan_manifest(scan_dir: Path) -> dict:
    try:
        manifest = json.loads((Path(scan_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def read_reusable_artifacts(
    scan_dir: Path, *, unit_allowlist: set[str] | None = None
) -> tuple[tuple[str, str, str], ...]:
    """``(design, kind, owning group)`` for every reusable artifact in the scan.

    A joblet or routelet exists to be CALLED by another design, so the worker
    that analyses the CALLER never sees it: on ETTM, ``maximoFourps`` invokes
    ``fourpsWriter`` 20 times and its evidence contains no definition of it.
    27 of ETTM's 142 designs are reusable and 11 of those live in one group,
    which is why nearly everything references that group. The shared-library
    pre-pass reads exactly these and writes one interface digest the later
    workers share; ownership of each artifact's analysis stays with its group.
    """
    manifest = _scan_manifest(scan_dir)
    kinds = manifest.get("artifact_kinds")
    groups = manifest.get("job_groups")
    if not isinstance(kinds, dict):
        return ()
    if not isinstance(groups, dict):
        groups = {}
    rows: list[tuple[str, str, str]] = []
    for unit, kind in kinds.items():
        if str(kind) not in REUSABLE_ARTIFACT_KINDS:
            continue
        if unit_allowlist is not None and str(unit) not in unit_allowlist:
            continue
        rows.append((str(unit), str(kind), str(groups.get(unit) or "Global")))
    return tuple(sorted(rows))


def resolve_owning_group(raw_group: str, work_item_names: Sequence[str]) -> str:
    """Map a scan-manifest group onto the work item that carries its evidence.

    ``scan/manifest.json`` stores the group as a repository path
    (``ETTM-2/ERP_TO_THE_MAX/fourps``) while the evidence file is named for the
    last segment (``fourps.md``), and a case-insensitive collision renames that
    segment again (``monitoring`` -> ``monitoring_part_ci1``). Pointing a
    worker at ``ETTM-2/ERP_TO_THE_MAX/fourps`` sends it after a file that does
    not exist. Unresolvable names are returned as the bare segment rather than
    dropped: a group we cannot place is still a group the orchestrator can find.
    """
    segment = raw_group.rsplit("/", 1)[-1] or raw_group
    names = set(work_item_names)
    if segment in names:
        return segment
    prefix = f"{segment}_part_ci"
    collided = sorted(n for n in names if n.startswith(prefix))
    return collided[0] if collided else segment


def read_artifact_composition(
    scan_dir: Path, *, unit_allowlist: set[str] | None = None
) -> tuple[tuple[str, int], ...]:
    """Per-kind design counts from the scan manifest (job / route / joblet / ...).

    The documentation set's coverage obligation is stated over "designs", and
    on ETTM 27 of 142 of those are reusable joblets and routelets. A writer
    that is told only the total either drops them silently or pads a table to
    reach the number; both are defects caused by the count not saying what it
    contains. Returns an empty tuple when the manifest is absent (a monolithic
    ``scan_result.json`` scan), and the plan then says so rather than guessing.
    """
    kinds = _scan_manifest(scan_dir).get("artifact_kinds")
    if not isinstance(kinds, dict):
        return ()
    counts: dict[str, int] = {}
    for unit, kind in kinds.items():
        if unit_allowlist is not None and str(unit) not in unit_allowlist:
            continue
        key = str(kind or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _thousands(value: int) -> str:
    return f"{value:,}"


#: Reusable designs named individually in the work plan before it switches to
#: per-group counts. A 187-group estate must not produce a plan that is mostly
#: a list of joblets.
MAX_LISTED_REUSABLE = 60

SHARED_LIBRARY_DIGEST = "_analysis/_shared_library.md"

#: Where the plan is staged. Underscore-prefixed directory, so it is never
#: published to the client corpus or counted as a delivered document.
PHASE2_WORK_PLAN_FILENAME = "phase2_work_plan.md"


def _render_shared_library_section(plan: Phase2WorkPlan) -> list[str]:
    """The pre-pass section, or an explicit statement that there is no library."""
    if not plan.reusable:
        return [
            "\n## Shared-library pre-pass: not needed\n\n",
            "This estate has no reusable designs (no joblets or routelets), so every\n",
            "design a worker analyses is defined in that worker's own evidence.\n",
        ]

    by_group: dict[str, list[tuple[str, str]]] = {}
    for design, kind, group in plan.reusable:
        by_group.setdefault(group, []).append((design, kind))

    total = plan.total_designs
    of_total = (
        f"{len(plan.reusable)} of {total} designs "
        f"({100.0 * len(plan.reusable) / total:.0f}%)"
        if total
        else f"{len(plan.reusable)} designs"
    )
    lines = [
        "\n## Shared-library pre-pass: run this worker FIRST\n\n",
        f"{of_total} are reusable -- joblets and routelets that exist to be\n",
        "CALLED by other designs. A worker analysing a caller does not have the\n",
        "callee's definition in its evidence, so today it describes an invocation it\n",
        "has never seen: one ETTM group invokes a writer joblet 20 times with no\n",
        "definition of that joblet anywhere in its own evidence.\n\n",
        "One extra worker runs BEFORE the group workers. It reads only the reusable\n",
        "designs listed below, from the evidence files named in the Groups table, and\n",
        f"writes an interface digest to {SHARED_LIBRARY_DIGEST}: per design, what it\n",
        "takes in, what it returns, what it expects of its caller, and the context\n",
        "variables and connections it relies on. Every later worker reads that file.\n\n",
        "It does NOT own any group's analysis. The group that contains a reusable\n",
        "design still documents it in full in that group's own analysis file; this is\n",
        "shared context, not a transfer of ownership, and the digest file is\n",
        "underscore-prefixed so it is never mistaken for a group analysis.\n\n",
        "| Owning group | Reusable designs |\n",
        "|--------------|------------------|\n",
    ]
    listed = 0
    for group in sorted(by_group, key=lambda g: (-len(by_group[g]), g)):
        entries = sorted(by_group[group])
        room = max(MAX_LISTED_REUSABLE - listed, 0)
        shown = entries[:room]
        listed += len(shown)
        names = ", ".join(f"{design} ({kind})" for design, kind in shown)
        if len(shown) < len(entries):
            names = (names + ", " if names else "") + f"+{len(entries) - len(shown)} more"
        lines.append(f"| {group} | {len(entries)}: {names} |\n")
    return lines


def render_phase2_work_plan(plan: Phase2WorkPlan) -> str:
    """Render the plan as the ASCII markdown the orchestrator reads."""
    total = plan.total_evidence_bytes
    largest = plan.largest_group_bytes
    smallest = min((g.evidence_bytes for g in plan.groups), default=0)
    spread = f"{largest / smallest:.1f}x" if smallest else "not computable"
    biggest = max(
        plan.groups, key=lambda g: (g.evidence_bytes, g.name), default=None
    )

    lines: list[str] = [
        "# Phase 2 Work Plan\n\n",
        "Computed by the server before Phase 2 (Python only, no model call).\n",
        "This is a SUGGESTION sized on evidence bytes -- the only thing this side\n",
        "can weigh. Read it, then decide.\n\n",
        "## Why batching, and not one sub-agent per group\n\n",
        "Sub-agents do not run 20-wide. On a measured 20-group run every group was\n",
        "requested in parallel and the dispatcher ran 3-4 at a time: analyses landed\n",
        "one or two a minute for 16 minutes, 5 groups never received one at all and\n",
        "the run ended before any document was written. There is no concurrency\n",
        "setting to raise, so the fan-out itself is smaller: one worker covers\n",
        "several groups and writes one analysis file per group it covers.\n\n",
        "## Estate\n\n",
        f"- Groups to analyse: {len(plan.groups)}\n",
        f"- Scanned designs: {plan.total_designs}\n",
    ]
    if plan.composition:
        composition = ", ".join(f"{kind} {count}" for kind, count in plan.composition)
        lines.append(f"- Design composition: {composition}\n")
        lines.append(
            f"- Standalone designs: {plan.standalone_designs()} | reusable designs "
            f"(joblet/routelet, called by another design): {plan.reusable_designs()}\n"
        )
    else:
        lines.append(
            "- Design composition: not available from this scan (no per-kind "
            "manifest); treat every scanned design as in scope.\n"
        )
    lines += [
        f"- Total group evidence: {_thousands(total)} bytes\n",
        (
            f"- Largest single group: {biggest.name} "
            f"({_thousands(biggest.evidence_bytes)} bytes)\n"
            if biggest is not None
            else "- Largest single group: none\n"
        ),
        f"- Evidence spread, largest over smallest: {spread}\n",
        f"- Target weight per worker (the heaviest group, which one sub-agent "
        f"already has to carry): {_thousands(plan.target_weight_bytes)} bytes\n",
        f"- Suggested workers: {len(plan.workers)} "
        f"(at most {plan.max_groups_in_a_worker} groups in any one of them, "
        f"about {plan.dispatch_waves} dispatch wave(s) at the "
        f"{PHASE2_OBSERVED_SUBAGENT_CONCURRENCY}-wide rate observed)\n\n",
    ]

    lines += [
        "## Groups\n\n",
        "A group is never split across workers: its evidence files and its one\n",
        "analysis file belong together.\n\n",
        "| Group | Evidence file(s) | Bytes | Designs | CRITICAL | HIGH | MEDIUM | LOW |\n",
        "|-------|------------------|-------|---------|----------|------|--------|-----|\n",
    ]
    for item in sorted(plan.groups, key=lambda g: (-g.evidence_bytes, g.name)):
        files = ", ".join(f"{name}.md" for name in item.evidence_files)
        crit, high, med, low = item.risk_counts
        lines.append(
            f"| {item.name} | {files} | {_thousands(item.evidence_bytes)} | "
            f"{item.design_count} | {crit} | {high} | {med} | {low} |\n"
        )
    if not plan.risk_scan_available:
        lines.append(
            "\nRisk columns are zero because the quick scan was not re-run this\n"
            "time (resumed run). The per-group flags are still on disk in\n"
            "_assessment/quick_scan/<evidence file name>.md -- read them.\n"
        )
    else:
        lines.append(
            "\nThe per-group flag detail is in _assessment/quick_scan/<evidence file\n"
            "name>.md, one file per evidence file named above.\n"
        )

    lines += _render_shared_library_section(plan)

    lines += [
        f"\n## Suggested split: {len(plan.workers)} group worker(s)\n\n",
        "Heaviest group first into the lightest worker that still has room. No\n",
        "worker holds more than "
        f"{config.phase2_max_groups_per_worker} groups: that bound is what keeps a\n",
        "misattributed component from travelling, not a performance setting.\n\n",
        "| Worker | Groups | Evidence bytes |\n",
        "|--------|--------|----------------|\n",
    ]
    for index, names in enumerate(plan.workers, 1):
        lines.append(
            f"| W{index} | {', '.join(names) or '(none)'} | "
            f"{_thousands(plan.worker_load(index - 1))} |\n"
        )

    lines += [
        "\n## Deviating from this\n\n",
        "You can see things this side cannot: a group with CRITICAL flags may deserve\n",
        "a worker to itself, two small related groups may be better read together,\n",
        "and a group whose evidence is mostly one enormous design is not the same\n",
        "work as one with fifty small ones. Deviate when you have a reason.\n\n",
        "Whatever split you use, record it in _knowledge/decisions.md so the next run\n",
        "can see what was tried:\n\n",
        "    ### <UTC timestamp> | author: agent\n",
        "    Phase 2 split: <N> workers. W1: <groups>. W2: <groups>. ...\n",
        "    Deviated from the suggested <M>-worker split because <reason>, or:\n",
        "    used the suggested split unchanged.\n",
    ]
    return "".join(lines)


def _render_component(comp: dict, platform: str, parts: list[str]) -> None:
    params = comp.get("params", [])
    regular_params: list[dict] = []
    filter_params: list[dict] = []
    if isinstance(params, list):
        for p in params:
            if p.get("field_type") == "FILTER":
                filter_params.append(p)
            else:
                regular_params.append(p)

    for p in regular_params:
        pname = p.get("name", "")
        pval = p.get("value", "")
        if not pval or pname in ("UNIQUE_NAME",):
            continue
        if pname in _TALEND_NOISE_PARAMS:
            continue
        val_str = str(pval)
        if len(val_str) > config.max_param_val_chars:
            val_str = val_str[:config.max_param_val_chars] + f"... [{len(val_str)} chars]"
        parts.append(f"- **{pname}**: `{val_str}`\n")

    if filter_params and platform == "lobster":
        parts.append("\n**Filter Chain (Transformation Logic)**\n\n")
        parts.append("| Step | Filter ID | Expression |\n")
        parts.append("|------|-----------|------------|\n")
        for idx, fp in enumerate(filter_params, 1):
            fid = fp.get("name", "").replace("filter_", "")
            fexpr = fp.get("value", "").replace("|", "\\|")
            parts.append(f"| {idx} | {fid} | `{fexpr}` |\n")
        parts.append("\n")

    if platform == "workato":
        dp_rows: list[tuple[str, str, str]] = []
        for src_param in (p for p in regular_params if p.get("name") in ("steps", "input")):
            raw = str(src_param.get("value", ""))
            for match in _WORKATO_DATAPILL_RE.finditer(raw):
                ref_path = match.group(1)
                formula = match.group(2).strip()
                parts_ref = ref_path.split(".")
                if len(parts_ref) >= 3:
                    src_step, src_field = parts_ref[1], ".".join(parts_ref[2:])
                elif len(parts_ref) == 2:
                    src_step, src_field = parts_ref[0], parts_ref[1]
                else:
                    src_step, src_field = "", ref_path
                dp_rows.append((src_step, src_field, formula))
        if dp_rows:
            parts.append("\n**Datapill Mappings (Transformation Logic)**\n\n")
            parts.append("| Source Step | Source Field | Formula |\n")
            parts.append("|------------|-------------|----------|\n")
            seen: set[tuple[str, str, str]] = set()
            for row in dp_rows:
                if row in seen:
                    continue
                seen.add(row)
                s_step, s_field, s_formula = row
                s_formula_safe = s_formula.replace("|", "\\|") if s_formula else "--"
                parts.append(f"| {s_step} | {s_field} | `{s_formula_safe}` |\n")
            parts.append("\n")

    for direction, key in [("Incoming", "incoming_connections"), ("Outgoing", "outgoing_connections")]:
        conns = comp.get(key, [])
        if conns:
            parts.append(f"- {direction} connections: {json.dumps(conns)}\n")
    ref = comp.get("metadata_connection_ref")
    if ref:
        parts.append(f"- Metadata connection: `{ref}`\n")


def _render_tmap_data(tmap_data: list[dict], parts: list[str], extract_source_hint: object) -> None:
    parts.append("\n### Transformation Detail (tMap Data)\n")
    for tmap_idx, tmap in enumerate(tmap_data):
        input_tables = tmap.get("input_tables", [])
        output_tables = tmap.get("output_tables", [])
        var_entries = tmap.get("var_entries", [])
        entries = tmap.get("entries", [])

        parts.append(f"\n**tMap #{tmap_idx + 1}**")
        if input_tables:
            parts.append(f" | Inputs: {', '.join(input_tables)}")
        if output_tables:
            parts.append(f" | Outputs: {', '.join(output_tables)}")
        parts.append("\n")

        if var_entries:
            parts.append("\n**Var Table**\n\n")
            parts.append("| Variable | Expression | Source Hint |\n")
            parts.append("|----------|------------|-------------|\n")
            for ve in var_entries:
                col = ve.get("column_name", "")
                expr = ve.get("expression", "")
                hint = extract_source_hint(expr)  # type: ignore[operator]
                expr_safe = expr.replace("|", "\\|")
                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
            parts.append("\n")

        tables_seen: dict[str, list[dict]] = {}
        for entry in entries:
            tbl = entry.get("table_name", "output")
            tables_seen.setdefault(tbl, []).append(entry)
        for tbl_name, tbl_entries in tables_seen.items():
            parts.append(f"\n**Output: {tbl_name}**\n\n")
            parts.append("| Target Column | Expression | Source Hint |\n")
            parts.append("|---------------|------------|-------------|\n")
            for te in tbl_entries:
                col = te.get("column_name", "")
                expr = te.get("expression", "")
                hint = extract_source_hint(expr)  # type: ignore[operator]
                expr_safe = expr.replace("|", "\\|")
                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
            parts.append("\n")


def _render_chunk_extras(chunk_data: dict, chunk_id: str, parts: list[str]) -> None:
    related_conns = chunk_data.get("related_connections", [])
    if related_conns:
        parts.append(f"\n---\n## Connections (chunk {chunk_id})\n")
        for conn in related_conns:
            conn_name = conn.get("name", "?")
            parts.append(f"\n### Connection: {conn_name}\n")
            for k, v in conn.items():
                if k == "name":
                    continue
                parts.append(f"- **{k}**: `{v}`\n")

    related_ctx = chunk_data.get("related_contexts", [])
    if related_ctx:
        parts.append(f"\n---\n## Context Groups (chunk {chunk_id})\n")
        for ctx in related_ctx:
            ctx_name = ctx.get("name", "?")
            parts.append(f"\n### Context Group: {ctx_name}\n")
            source_project = ctx.get("source_project", "")
            source_root = ctx.get("source_root", "")
            source_role = ctx.get("source_role", "")
            if source_project:
                parts.append(f"- **Source project**: `{source_project}`\n")
            if source_root:
                role_suffix = f" ({source_role})" if source_role else ""
                parts.append(f"- **Source root**: `{source_root}`{role_suffix}\n")
            if ctx.get("purpose"):
                parts.append(f"- **Purpose**: {ctx['purpose']}\n")
            if ctx.get("description"):
                parts.append(f"- **Description**: {ctx['description']}\n")
            for var in ctx.get("variables", ctx.get("items", [])):
                vname = var.get("name", "?")
                vtype = var.get("type", "")
                values = var.get("values")
                if isinstance(values, dict):
                    rendered_values = json.dumps(values, ensure_ascii=False, sort_keys=True)
                else:
                    rendered_values = str(var.get("value", ""))
                line = f"- `{vname}` ({vtype}): `{rendered_values}`"
                parts.append(line + "\n")

    manifest = chunk_data.get("manifest", {})
    if manifest:
        parts.append(f"\n---\n## Manifest (chunk {chunk_id})\n")
        for section, data in manifest.items():
            if data:
                parts.append(f"\n### {section}\n```json\n{json.dumps(data, indent=2)}\n```\n")


# ─────────────────────────────────────────────────────────────────────────── #
# Runtime tools: the CLI lane's live query surface onto the runtime mirror
# ─────────────────────────────────────────────────────────────────────────── #

#: Where the standalone querying CLI is staged, relative to ``docs_staging/``.
RUNTIME_TOOLS_DIRNAME = "_tools"
RUNTIME_TOOLS_SCRIPT_NAME = "runtime_tools.py"

#: Non-secret per-run identity written beside the script.  The bearer key is
#: deliberately NOT in here: ``docs_staging/`` is swapped into ``docs/`` on
#: success, so anything written here is published.
RUNTIME_TOOLS_SIDECAR_NAME = "runtime_tools.json"


def remove_runtime_tools(staging_dir: Path) -> None:
    """Delete ``{staging}/_tools/`` before the staging tree is published.

    ``_tools/`` is RUN-TIME FACILITY, not a deliverable: the script and its
    identity sidecar exist only so the writing agent can query the runtime
    mirror during the run.  It has to go before publication because the CLI lane
    does NOT apply ``phase4_cleanup.PUBLISHED_STAGING_DIRS`` -- that allowlist
    runs inside the custom harness's Phase 4, which the CLI transports never
    execute.  Verified on a published CLI-lane tree: ``output/alitalend/docs/``
    carries ``_provenance/``, which is absent from that allowlist, so nothing
    would have stopped ``_tools/`` shipping to the client alongside it.

    The durable record of what the agent asked is NOT here -- it is
    ``_assessment/runtime_probe_ledger.jsonl``, and ``_assessment`` is published
    deliberately so two runs of one project stay comparable.

    Advisory: a failure to clean up must never fail a run whose documents are
    already written.
    """
    target = staging_dir / RUNTIME_TOOLS_DIRNAME
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
    except OSError as exc:
        logger.warning("Could not remove %s before publication: %s", target, exc)


def stage_runtime_tools(staging_dir: Path, project_id: str) -> Path | None:
    """Copy ``scripts/runtime_tools.py`` into ``{staging}/_tools/`` for the agent.

    The CLI lane previously received runtime facts only as a pre-computed
    rollup, so it shipped zero runtime facts for questions that rollup was not
    built to answer.  This stages the tool that lets the writing agent ask its
    own questions of the same mirror.

    Returns the staged script path, or ``None`` when it could not be staged.
    Advisory like every other staging aid here (project memory, the work plan):
    losing the ability to ask about runtime must not cost the run its documents.
    It is staged on EVERY path -- fresh preprocess, extraction-cache hit and
    resume alike -- because a resumed run writes the same documents as a fresh
    one and must not be the one that silently has no runtime access.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / RUNTIME_TOOLS_SCRIPT_NAME
    if not source.exists():
        logger.warning("runtime_tools.py not found at %s", source)
        return None
    try:
        tools_dir = staging_dir / RUNTIME_TOOLS_DIRNAME
        tools_dir.mkdir(parents=True, exist_ok=True)
        tools_dir.chmod(0o755)
        destination = tools_dir / RUNTIME_TOOLS_SCRIPT_NAME
        shutil.copy2(source, destination)
        destination.chmod(0o755)
        (tools_dir / RUNTIME_TOOLS_SIDECAR_NAME).write_text(
            json.dumps(
                {
                    "project_id": project_id,
                    "base_url": str(config.rag_base_url or "").rstrip("/"),
                    "staging_dir": str(staging_dir),
                    "written_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("runtime_tools.py could not be staged: %s", exc, exc_info=True)
        return None
    logger.info(
        "Staged runtime tools at %s (project %s)", destination, project_id,
    )
    return destination


@contextmanager
def runtime_tool_environment(project_id: str, staging_dir: Path) -> Iterator[None]:
    """Publish the runtime-tool settings to the provider subprocess environment.

    Both CLI transports build their subprocess environment from ``os.environ``
    (``providers/claude_cli.py``, ``providers/opencode_cli.py::_runtime_env``),
    so this process's environment is the only channel that reaches them without
    changing a provider.  The previous values are restored on the way out.

    ``os.environ`` is process-global and two documentation runs can overlap in
    one server process, so the SECRET travels this way but IDENTITY does not
    depend on it: ``_tools/runtime_tools.json``, written per run by
    :func:`stage_runtime_tools`, is what the script reads first for the project
    id and base URL.  A race here can therefore cost a concurrent run its key
    (visible: the request fails), never bind its queries to the wrong project
    (invisible: another project's runtime data in these documents).
    """
    values = {
        "NEXUS_RAG_BASE_URL": str(config.rag_base_url or "").rstrip("/"),
        "NEXUS_INGEST_KEY": str(config.ingest_api_key or ""),
        "NEXUS_PROJECT_ID": str(project_id),
        "NEXUS_STAGING_DIR": str(staging_dir),
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, old in previous.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


class DocGenerationCoordinator:
    """Orchestrates multi-phase documentation generation: preprocessing, provider dispatch, staging swap, and RAG push."""

    def __init__(self, log_manager: LogManager) -> None:
        self.log_manager = log_manager

    @staticmethod
    def _format_event(event: dict) -> str | None:
        """Turn a stream-json event into a human-readable log line.

        Returns None for events we want to suppress (e.g. large tool results).
        """
        etype = event.get("type", "")

        # --- Assistant text ---
        if etype == "assistant":
            message = event.get("message", {})
            # message.content is a list of content blocks
            parts = []
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        # Truncate very long text to keep logs readable
                        parts.append(text[:500])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Show tool name + key params
                    summary = ", ".join(
                        f"{k}={repr(v)[:80]}" for k, v in list(inp.items())[:4]
                    )
                    parts.append(f"[tool_call] {name}({summary})")
            return " | ".join(parts) if parts else None

        # --- Tool result (keep short) ---
        if etype == "tool_result" or etype == "content_block_stop":
            return None  # suppress noisy tool output

        # --- Result / final output ---
        if etype == "result":
            text = event.get("result", "")
            if text:
                return f"[result] {text[:300]}"
            return "[result] (empty)"

        # --- System / init messages ---
        if etype == "system":
            return f"[system] {event.get('message', '')[:200]}"

        # --- Catch-all for unknown types ---
        if etype:
            return f"[{etype}] {json.dumps(event)[:200]}"

        return None

    @staticmethod
    def _extract_source_hint(expression: str) -> str:
        """Parse common tMap expression patterns to extract a source hint."""
        if not expression:
            return ""
        expr = expression.strip()
        # Direct field reference: row1.field, row2.field, etc.
        if expr.startswith("row") and "." in expr and " " not in expr.split(".", 1)[0]:
            return expr.split("(")[0] if "(" in expr else expr
        # Var reference
        if expr.startswith("Var."):
            return expr.split("(")[0] if "(" in expr else expr
        # Context variable
        if expr.startswith("context."):
            return expr
        # Constant string or number
        if expr.startswith('"') or expr.startswith("'"):
            return "constant"
        try:
            float(expr)
            return "constant"
        except (ValueError, TypeError):
            pass
        # Ternary or complex expression
        if "?" in expr and ":" in expr:
            return "conditional"
        return "expression"

    @staticmethod
    def _format_chunk_markdown(
        scanner: ScannerService, output_dir: str, chunks: list[dict],
        platform: str = "talend",
        unit_allowlist: set[str] | None = None,
    ) -> tuple[list[str], int]:
        """Process chunks into markdown parts. Returns (parts, job_count)."""
        parts: list[str] = []
        job_count = 0

        for chunk in chunks:
            chunk_data = scanner.read_chunk_data(
                output_dir, chunk["chunk_id"], unit_allowlist=unit_allowlist,
            )
            if "error" in chunk_data:
                parts.append(f"\n## Chunk {chunk['chunk_id']} — ERROR: {chunk_data['error']}\n")
                continue

            for job in chunk_data.get("jobs", []):
                job_count += 1
                job_id = job.get("job_id") or job.get("name", "unknown")
                display_name = job.get("name", "unknown")
                source_root = job.get("source_root", "")
                artifact_kind = str(job.get("artifact_kind") or "job")
                is_talend = platform.lower() == "talend"
                artifact_label = (
                    "Talend Route"
                    if artifact_kind == "route"
                    else "Talend Job"
                )
                section_label = artifact_label if is_talend else "Job"
                heading = (
                    f"\n---\n## {section_label}: {job_id}\n"
                    f"\n- Artifact type: **{artifact_label if is_talend else 'Executable unit'}**\n"
                )
                if job_id != display_name or source_root:
                    heading += f"\n- Display name: `{display_name}`\n"
                    if source_root:
                        heading += f"- Source root: `{source_root}`\n"
                parts.append(heading)

                if artifact_kind == "route":
                    parts.append(
                        "- Analyze as message mediation: triggers/endpoints, channels, "
                        "Camel patterns, invoked Jobs, and error paths.\n"
                    )
                else:
                    parts.append(
                        "- Analyze as data processing: inputs, transformations, business "
                        "rules, outputs, and callers.\n"
                    )

                components = job.get("components", [])
                if components:
                    parts.append("\n### Components\n")
                    for comp in components:
                        ctype = comp.get("component_type", "?")
                        cname = comp.get("unique_name", "?")
                        parts.append(f"\n#### {cname} ({ctype})\n")
                        _render_component(comp, platform, parts)

                tmap_data = job.get("tmap_data", [])
                if tmap_data and platform == "talend":
                    _render_tmap_data(tmap_data, parts, DocGenerationCoordinator._extract_source_hint)

                ctx_vars = job.get("context_variables", [])
                if ctx_vars:
                    parts.append("\n### Context Variables\n")
                    for cv in ctx_vars:
                        cv_name = cv.get("name", "?")
                        cv_val = cv.get("value", "")
                        cv_type = cv.get("type", "")
                        prompt_val = cv.get("prompt_value", "")
                        line = f"- `{cv_name}` ({cv_type}): `{cv_val}`"
                        if prompt_val and prompt_val != cv_val:
                            line += f" (prompt: `{prompt_val}`)"
                        parts.append(line + "\n")

                routines = job.get("raw_routines", [])
                if routines:
                    parts.append(f"\n### Routines used: {', '.join(routines)}\n")

            _render_chunk_extras(chunk_data, chunk["chunk_id"], parts)

        return parts, job_count

    @staticmethod
    def _expected_analyses(staging_dir: Path) -> tuple[list[str], str]:
        """The analyses this estate OWES, and where that list came from.

        Returns ``(groups, source)``. ``source`` is one of:

        ``"manifest"``    -- ``docs_staging/_manifest.json`` ``groups``, written
                             by the previous run after preprocessing. Authoritative.
        ``"preprocess"``  -- the previous run never got as far as writing a
                             manifest; the ``_preprocess/`` group files are the
                             next-best checklist.
        ``"unknown"``     -- neither is available; ``groups`` is empty and NO
                             completeness claim may be made from it.

        Slice names are folded onto the analysis that owns them
        (``analysis_group_for_slice``): a group split over ``X_part1`` /
        ``X_part2`` produces ONE ``_analysis/X.md`` because the prompt requires
        the parts to be merged, so counting slices would demand an analysis
        file that is never meant to exist.
        """
        manifest_path = staging_dir / "_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Resume: unreadable _manifest.json (%s)", exc)
            else:
                slices = manifest.get("groups")
                if isinstance(slices, list) and slices:
                    return (
                        sorted({
                            analysis_group_for_slice(str(s)) for s in slices
                        }),
                        "manifest",
                    )

        preprocess_dir = staging_dir / "_preprocess"
        if preprocess_dir.is_dir():
            slices = [
                f.stem for f in preprocess_dir.glob("*.md")
                if not f.name.startswith("_")
            ]
            if slices:
                return (
                    sorted({analysis_group_for_slice(s) for s in slices}),
                    "preprocess",
                )

        return [], "unknown"

    @staticmethod
    def _analysis_bytes(analysis_dir: Path, group: str) -> int:
        """Size of ``group``'s analysis, whichever layout stores it.

        ``analysis_store`` answers for both: the file for the legacy flat
        ``_analysis/{group}.md``, the section files rendered together for
        ``_analysis/{group}/{SECTION}.md``. Measuring the rendered analysis
        rather than ``stat()``-ing paths is what keeps ONE size rule for two
        layouts -- a per-layout rule is a rule free to drift.

        Returns 0 for anything that is not a usable analysis, which is the
        answer that costs a re-run rather than a silent gap:

        * no analysis at all (``FileNotFoundError``);
        * a group DIRECTORY that exists but holds no file the section contract
          names (``SectionContractError``) -- a directory of unnamed files is
          lost evidence, not an empty analysis, and it is logged at WARNING
          because nothing else in the run would report it;
        * a name the store refuses to turn into a path (``AnalysisPathError``);
        * an unreadable file (``OSError``).

        The listing is the only source of group names here, so the last two are
        defence rather than expectation -- but an exception raised inside
        resume detection would abort the whole generation, which is a far worse
        outcome than re-analysing one group.
        """
        try:
            text = analysis_store.read_group_analysis(analysis_dir, group)
        except FileNotFoundError:
            return 0
        except analysis_store.SectionContractError as exc:
            logger.warning(
                "Resume: %r has an analysis directory that resolves to no known "
                "section, so it counts as NOT analysed: %s", group, exc,
            )
            return 0
        except analysis_store.AnalysisPathError as exc:
            logger.warning("Resume: unusable analysis name %r (%s)", group, exc)
            return 0
        except OSError as exc:
            logger.warning("Resume: unreadable analysis for %r (%s)", group, exc)
            return 0
        return len(text.encode("utf-8"))

    @staticmethod
    def _analysed_groups(analysis_dir: Path) -> list[str]:
        """Groups with a USABLE analysis, under EITHER Phase 2 layout.

        ONE definition, shared by resume detection and the post-run coverage
        line, because they are the same question asked at two moments and a
        second copy is free to drift. ``analysis_store.list_groups`` owns the
        exclusion rule -- no ``_``-prefixed artifact, and no
        ``{group}_summary.md`` / ``{group}/_summary.md``, which is a navigation
        aid the previous resume detector counted as an analysis. The size
        filter is added here so a truncated, empty or unresolvable analysis is
        pending work rather than a completion.

        Group names come from the real directory listing so that on a
        case-insensitive filesystem ``Monitoring`` is never read as
        ``monitoring``'s analysis (both are real ETTM groups; preprocessing
        renames the second collider to ``monitoring_part_ci1`` precisely so the
        two cannot share a path -- as a file OR as a directory).
        """
        if not analysis_dir.is_dir():
            return []
        return [
            group for group in analysis_store.list_groups(analysis_dir)
            if DocGenerationCoordinator._analysis_bytes(analysis_dir, group)
            >= MIN_GROUP_ANALYSIS_BYTES
        ]

    @staticmethod
    def _apply_group_checklist(
        state: dict,
        staging_dir: Path,
        expected_groups: list[str],
        source: str,
        analysed: list[str] | None = None,
    ) -> dict:
        """Fill a resume state's remainder fields from ONE group checklist.

        Called twice per run and defined once: when resume is first detected
        (from the previous run's manifest) and again after preprocessing, which
        may have produced a different group set if the scan changed. Two copies
        of this arithmetic would be two chances to report a remainder that is
        not the remainder.

        ``analysed`` lets a caller that has already taken the listing pass it
        in. Reading it is no longer a ``stat()``: it opens each analysis
        through the store, so the caller in ``_detect_resume_state`` hands its
        own result over instead of walking the estate twice. Absent, it is
        computed here -- the post-preprocessing call must re-take it.
        """
        if analysed is None:
            analysed = DocGenerationCoordinator._analysed_groups(
                staging_dir / "_analysis"
            )
        pending = [name for name in expected_groups if name not in set(analysed)]
        # "unknown" means there is no checklist to be complete against. Claiming
        # completeness from an empty expectation is the run-260 failure with a
        # different cause, so it fails toward MORE work: Phase 2 is only
        # "complete" when a real checklist says every group has an analysis.
        complete = source != "unknown" and not pending
        state["expected_groups"] = expected_groups
        state["expected_groups_source"] = source
        state["analysis_files"] = analysed
        state["pending_groups"] = pending
        state["analysis_complete"] = complete
        return state

    @staticmethod
    def _detect_resume_state(staging_dir: Path) -> dict | None:
        """Inspect docs_staging/ for completed artifacts from a prior run.

        Returns None (fresh run) or a dict describing both what already exists
        AND -- the part that was missing -- what is still owed:

        - preprocess_exists: bool  -- _preprocess/ has group markdown files
        - preprocess_files: list[str]
        - expected_groups: list[str]   -- every group this estate owes an analysis
        - expected_groups_source: str  -- "manifest" | "preprocess" | "unknown"
        - analysis_files: list[str]    -- groups WITH a usable analysis, under
                                          EITHER layout: the legacy flat
                                          ``_analysis/{group}.md`` or the
                                          per-section ``_analysis/{group}/``
                                          directory. A project generated before
                                          the per-section layout resumes exactly
                                          as it did, and a directory holding no
                                          file the section contract names is NOT
                                          an analysis (see ``_analysis_bytes``)
        - pending_groups: list[str]    -- groups still to analyse (the REMAINDER)
        - analysis_complete: bool      -- every expected group has one, and the
                                          expectation came from a real checklist
        - scorecard_exists: bool
        - completed_docs: list[str]    -- *.md files in staging root >500 bytes

        WHY the remainder, and not just the completions: this method used to
        report ``f.stem for f in _analysis.glob("*.md")`` and compare it to
        nothing. On ETTM run 260 that listed 30 entries for 15 real analyses --
        every ``{group}_summary.md`` navigation aid counted as an analysis --
        against a 20-group manifest, and the prompt told the model to skip
        Phase 2 for all 30. Five groups (``Monitoring``, ``afas``,
        ``fourpsForSuppliers``, ``fourpsMaximo``, ``utils``) were never analysed,
        the documents shipped without a quarter of the estate, and the run
        reported success. A list of what exists cannot detect absence; only a
        comparison against the manifest can.
        """
        if not staging_dir.exists():
            return None

        preprocess_dir = staging_dir / "_preprocess"
        analysis_dir = staging_dir / "_analysis"
        assessment_dir = staging_dir / "_assessment"

        # Check for any usable artifacts
        preprocess_files = (
            sorted(f.name for f in preprocess_dir.glob("*.md") if not f.name.startswith("_"))
            if preprocess_dir.exists() else []
        )

        analysis_files = DocGenerationCoordinator._analysed_groups(analysis_dir)

        scorecard_path = assessment_dir / "scorecard.md"
        scorecard_exists = scorecard_path.exists() and scorecard_path.stat().st_size > 100
        completed_docs = (
            sorted(
                f.name for f in staging_dir.glob("*.md")
                if f.stat().st_size > 500
            )
        )

        # If nothing useful exists, treat as fresh
        if not preprocess_files and not analysis_files and not completed_docs:
            return None

        expected_groups, expected_source = DocGenerationCoordinator._expected_analyses(
            staging_dir
        )
        state = DocGenerationCoordinator._apply_group_checklist(
            {
                "preprocess_exists": len(preprocess_files) > 0,
                "preprocess_files": preprocess_files,
                "scorecard_exists": scorecard_exists,
                "completed_docs": completed_docs,
            },
            staging_dir,
            expected_groups,
            expected_source,
            analysed=analysis_files,
        )
        logger.info(
            "Resume state: %d/%d group analyses present (source=%s); pending: %s",
            len(state["analysis_files"]), len(expected_groups), expected_source,
            ", ".join(state["pending_groups"]) or "none",
        )
        logger.debug("Resume state detail: %s", state)
        return state

    async def _preprocess_scan_data(
        self,
        project_id: str,
        staging_dir: Path,
        platform: str = "talend",
        total_jobs: int = 0,
        unit_allowlist: set[str] | None = None,
    ) -> list[str]:
        """Pre-process raw job JSONs into structured markdown summaries.

        Reads scan data via ScannerService and writes per-group markdown files
        to staging_dir/_preprocess/{group}.md. Also bundles analysis prompts,
        reference docs, and scoring templates for sub-agents.

        ``unit_allowlist`` (composite sub-project scoping) restricts the
        preprocess to the partition's own canonical unit names: chunk
        definitions, chunk payloads, group files and the design inventory then
        contain ONLY that sub-project's units. Shared-name REFERENCES jobs are
        safe because canonical job ids are unique per zip (see
        ``ScannerService.get_chunk_definitions``). ``None`` => full scan,
        unchanged behaviour.

        Returns list of group names.
        """
        preprocess_dir = staging_dir / "_preprocess"
        preprocess_dir.mkdir(parents=True, exist_ok=True)
        preprocess_dir.chmod(0o755)

        scanner = ScannerService()
        output_dir = str(config.intodoc_output_dir / project_id)

        processed_groups: list[str] = []
        direct_source = (
            platform == "generic"
            and config.source_context_mode.lower() == "direct_redacted"
        )
        if direct_source:
            processed_groups, source_coverage = materialize_direct_redacted_source(
                scan_output_dir=Path(output_dir),
                preprocess_dir=preprocess_dir,
                inline_max_bytes=config.direct_source_inline_max_bytes,
                chunk_bytes=config.direct_source_chunk_bytes,
            )
            logger.info(
                "Direct redacted source preprocessing: %d groups, %d files, %d redactions",
                len(processed_groups),
                source_coverage.get("summary", {}).get("total_files", 0),
                source_coverage.get("summary", {}).get("redactions", 0),
            )
        else:
            # Get chunk definitions (scoped to the partition when allowlisted)
            chunk_defs = scanner.get_chunk_definitions(
                output_dir, unit_allowlist=unit_allowlist,
            )
            if "error" in chunk_defs:
                raise RuntimeError(f"Failed to get chunk definitions: {chunk_defs['error']}")

            # Group chunks by group name
            groups: dict[str, list[dict]] = {}
            for chunk in chunk_defs["chunks"]:
                group = chunk["group"]
                groups.setdefault(group, []).append(chunk)

            # Case-insensitive filesystems (macOS/Windows dev hosts) map group
            # names differing only in case (Talend allows e.g. VISE2AssetCheck
            # and Vise2AssetCheck side by side) onto the SAME {group}.md path,
            # silently clobbering the earlier group's preprocess file and
            # losing its units from Phase 1/2. Disambiguate every collider
            # after the first with a "_part_ci{k}" slice base: the existing
            # slice->group resolvers (harness._group_units, validators.
            # _groups_for_analysis_file, quick-scan globbing) already map any
            # "{group}_part*" slice back to its group, and the suffix keeps
            # paths distinct even case-insensitively. Non-colliding groups are
            # byte-identical to before.
            slice_base: dict[str, str] = {}
            lower_buckets: dict[str, list[str]] = {}
            for group in sorted(groups):
                lower_buckets.setdefault(group.lower(), []).append(group)
            for bucket in lower_buckets.values():
                slice_base[bucket[0]] = bucket[0]
                for k, group in enumerate(bucket[1:], start=1):
                    slice_base[group] = f"{group}_part_ci{k}"
                    logger.warning(
                        "Group %r collides case-insensitively with %r — "
                        "writing its preprocess slice as %r",
                        group, bucket[0], slice_base[group],
                    )

            for group, chunks in sorted(groups.items()):
                total_jobs = sum(c["job_count"] for c in chunks)
                base = slice_base[group]

                # Split large groups (>40 jobs) into parts for parallel Phase 2
                if total_jobs > 40:
                    cumulative = 0
                    split_idx = len(chunks)
                    target = total_jobs // 2
                    for i, c in enumerate(chunks):
                        cumulative += c["job_count"]
                        if cumulative >= target:
                            split_idx = i + 1
                            break
                    if base == group:
                        part_names = (f"{group}_part1", f"{group}_part2")
                    else:
                        # Collision-suffixed base already contains "_part";
                        # nested split parts stay under the same unique base.
                        part_names = (f"{base}_1", f"{base}_2")
                    chunk_slices = [
                        (part_names[0], chunks[:split_idx]),
                        (part_names[1], chunks[split_idx:]),
                    ]
                    logger.info(
                        "Splitting group %s (%d jobs) into 2 parts at chunk %d",
                        group, total_jobs, split_idx,
                    )
                else:
                    chunk_slices = [(base, chunks)]

                for slice_name, slice_chunks in chunk_slices:
                    if not slice_chunks:
                        continue
                    slice_jobs = sum(c["job_count"] for c in slice_chunks)

                    # Compact design inventory at top — always fits in first
                    # ~2KB so truncation never hides the complete unit list.
                    all_job_names: list[str] = []
                    for c in slice_chunks:
                        all_job_names.extend(c.get("job_names", []))
                    is_talend = platform.lower() == "talend"
                    unit_plural = "Talend designs" if is_talend else "jobs"
                    inventory_title = "Talend Design Inventory" if is_talend else "Job Inventory"
                    column_title = "Design ID" if is_talend else "Job Name"
                    inventory_lines = [
                        f"# Pre-processed Scan Data: {slice_name}\n",
                        f"Chunks: {len(slice_chunks)} | {unit_plural}: {slice_jobs}\n\n",
                        f"## {inventory_title} ({slice_jobs} {unit_plural})\n\n",
                        f"| {column_title} | Components |\n",
                        "|----------|------------|\n",
                    ]
                    for jn in all_job_names:
                        inventory_lines.append(f"| {jn} | (see detail below) |\n")
                    inventory_lines.append("\n---\n\n")
                    header = "".join(inventory_lines)

                    body_parts, actual_jobs = self._format_chunk_markdown(
                        scanner, output_dir, slice_chunks, platform=platform,
                        unit_allowlist=unit_allowlist,
                    )
                    content = header + "".join(body_parts)
                    # Defence-in-depth for scan caches produced before the
                    # scanner-side credential-param fix: inline credential
                    # query-params / RAW() values in verbatim code strings are
                    # masked here so no downstream consumer (Phase 1/2 tasks,
                    # evidence bundles built from these files) ever sees them.
                    content, inline_redactions = redact_inline_credentials(content)
                    if inline_redactions:
                        logger.warning(
                            "Preprocess group %s: masked %d inline credential "
                            "parameter(s) left by an older scan cache",
                            slice_name, inline_redactions,
                        )
                    out_path = preprocess_dir / f"{slice_name}.md"
                    out_path.write_text(content, encoding="utf-8")
                    if len(content) > 100_000:
                        logger.warning(
                            "Pre-processed group %s is large (%d bytes) — "
                            "LLM context may truncate job detail; consider splitting",
                            slice_name, len(content),
                        )
                    processed_groups.append(slice_name)
                    logger.info(
                        "Pre-processed group %s: %d bytes, %d jobs",
                        slice_name, len(content), actual_jobs,
                    )

        # --- Bundle analysis guide for Phase 2 sub-agents ---
        prompts_data = scanner.get_analysis_prompts(platform)
        if "prompts" in prompts_data:
            if prompts_data.get("consolidated"):
                # New path: single ANALYSIS_GUIDE.md
                guide_content = list(prompts_data["prompts"].values())[0]
                (preprocess_dir / "_analysis_guide.md").write_text(
                    guide_content, encoding="utf-8",
                )
                logger.info(
                    "Bundled analysis guide (%s): %d bytes",
                    platform, len(guide_content),
                )
            else:
                # Legacy fallback: bundle individual .txt prompt files
                header = (
                    "# Analysis Prompts\n\n"
                    "Use these structured prompts to guide your analysis "
                    "of each group's scan data.\n\n"
                )
                entries = []
                for name, content in sorted(prompts_data["prompts"].items()):
                    entries.append(f"---\n## {name}\n\n{content}\n\n")

                parts: list[list[str]] = [[]]
                part_size = 0
                for text in entries:
                    text_bytes = len(text.encode("utf-8"))
                    if part_size + text_bytes > config.max_prompt_bundle_bytes and parts[-1]:
                        parts.append([])
                        part_size = 0
                    parts[-1].append(text)
                    part_size += text_bytes

                for i, part in enumerate(parts):
                    suffix = f"_{i + 1}" if len(parts) > 1 else ""
                    bundle = header + "".join(part)
                    (preprocess_dir / f"_analysis_prompts{suffix}.md").write_text(
                        bundle, encoding="utf-8",
                    )
                    logger.info(
                        "Bundled analysis prompts%s (%s): %d bytes",
                        suffix, platform, len(bundle),
                    )
        else:
            logger.warning(
                "Analysis prompts NOT bundled for %s: %s",
                platform, prompts_data.get("error", "unknown"),
            )

        # --- Bundle Phase 3 guide for cross-group synthesis ---
        phase3_data = scanner.get_phase3_guide(platform)
        if "content" in phase3_data:
            (preprocess_dir / "_phase3_guide.md").write_text(
                phase3_data["content"], encoding="utf-8",
            )
            logger.info(
                "Bundled Phase 3 guide (%s): %d bytes",
                platform, len(phase3_data["content"]),
            )
        else:
            logger.warning(
                "Phase 3 guide NOT bundled for %s: %s",
                platform, phase3_data.get("error", "unknown"),
            )

        # --- Bundle reference docs per Phase 3 agent ---
        max_ref_bundle_bytes = config.max_ref_bundle_bytes  # ~20K tokens, safely under 25K token limit
        # Resolve from source tree (not config.project_root which may be /opt/)
        # __file__ -> services/ -> mcp_server/ -> src/ -> mcp-server/ -> knowledge_base/
        _source_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        ref_dir = _source_root / "intodoc" / "reference-docs"
        if ref_dir.exists():
            agent_ref_map = {
                "_ref_core": [
                    "00_AGENT_BRIEF", "01_ARCHITECTURE_MAP",
                    "02_COMPONENT_INVENTORY", "03_DATA_FLOWS_AND_LINEAGE",
                ],
                "_ref_business_ops": [
                    "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
                    "05_RUNTIME_CONFIG_AND_EXTERNALS",
                    "06_OPERATIONS_TESTING_AND_DEBUGGING",
                ],
                "_ref_user": [
                    "07_QUALITY_SECURITY_RISK_SCORECARD",
                    "08_CHANGE_PLAYBOOK",
                ],
            }
            for bundle_name, doc_names in agent_ref_map.items():
                header = (
                    f"# Reference Docs Bundle: {bundle_name}\n\n"
                    "These are example reference docs showing the expected "
                    "format, depth, and structure for each document type.\n\n"
                )
                entries = []
                for dn in doc_names:
                    ref_file = ref_dir / f"{dn}.md"
                    if ref_file.exists():
                        entries.append(f"---\n## {dn}\n\n{ref_file.read_text(encoding='utf-8')}\n\n")

                # Split into parts that stay under the size limit
                parts: list[list[str]] = [[]]
                part_size = 0
                for text in entries:
                    text_bytes = len(text.encode("utf-8"))
                    if part_size + text_bytes > max_ref_bundle_bytes and parts[-1]:
                        parts.append([])
                        part_size = 0
                    parts[-1].append(text)
                    part_size += text_bytes

                for i, part in enumerate(parts):
                    suffix = f"_{i + 1}" if len(parts) > 1 else ""
                    bundle_content = header + "".join(part)
                    (preprocess_dir / f"{bundle_name}{suffix}.md").write_text(
                        bundle_content, encoding="utf-8",
                    )
                    logger.info(
                        "Bundled %s%s: %d bytes", bundle_name, suffix, len(bundle_content),
                    )
        else:
            logger.warning("Reference docs dir not found: %s", ref_dir)

        # --- Bundle scoring/assessment templates for Phase 2.6 ---
        _templates_dir = Path(__file__).resolve().parent.parent / "templates"
        scoring_templates = {
            "_scoring_rubric.md": "scoring-rubric.md",
            "_anti_patterns.md": "anti-patterns.md",
            "_red_flags.md": "red-flags.md",
            "_security_checklist.md": "security-checklist.md",
            "_pattern_library.md": "pattern-library.md",
        }
        for dest_name, src_name in scoring_templates.items():
            src_path = _templates_dir / src_name
            if src_path.exists():
                content = src_path.read_text(encoding="utf-8")
                (preprocess_dir / dest_name).write_text(content, encoding="utf-8")
                logger.info("Bundled scoring template %s: %d bytes", dest_name, len(content))
            else:
                logger.warning("Scoring template not found: %s", src_path)

        # --- Write doc templates for Phase 3 agents ---
        self._write_doc_templates(preprocess_dir)

        return processed_groups

    # ------------------------------------------------------------------ #
    # Phase 1.5 — lightweight quick scan (no LLM, Python regex only)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _run_quick_scan(
        staging_dir: Path, groups: list[str]
    ) -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, int]]:
        """Scan pre-processed markdown files for common anti-patterns.

        Writes _assessment/quick_scan.md with a priority-ranked list of
        findings per group.  Phase 2 agents read this to calibrate depth.
        This is intentionally fast — regex only, no model calls.

        Returns ``(findings, design_counts)`` so the Phase 2 work plan can be
        built from the same in-memory pass rather than by re-reading the
        markdown this function just wrote — the coupling that cost run 258 its
        evidence.
        """
        preprocess_dir = staging_dir / "_preprocess"
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)

        # Patterns: (flag_name, severity, regex_pattern)
        PATTERNS = [
            ("hardcoded_credential", "CRITICAL",
             re.compile(
                 r"(?:password|passwd|pwd|secret|api_?key|token)[^:\n]*:\s*[\"'` ]?(?!context\.|row\.|Var\.)[A-Za-z0-9!@#$%^&*_\-]{4,}",
                 re.IGNORECASE,
             )),
            ("literal_ip_address", "HIGH",
             re.compile(r"\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b")),
            ("missing_error_handler", "HIGH",
             None),  # checked per-job via component count heuristic
            ("god_profile", "HIGH",
             None),  # checked per-job via component count heuristic
            ("hardcoded_env_value", "MEDIUM",
             re.compile(r'\b(?:localhost|127\.0\.0\.1|http://localhost)\b', re.IGNORECASE)),
            ("missing_retry_logic", "MEDIUM",
             None),  # checked per-job
            ("no_context_vars", "LOW",
             None),  # checked per-job
        ]

        cred_re = PATTERNS[0][2]
        ip_re = PATTERNS[1][2]
        env_re = PATTERNS[4][2]

        findings: dict[str, list[tuple[str, str, str]]] = {}  # group -> [(severity, flag, detail)]
        design_counts: dict[str, int] = {}

        for group in groups:
            # Account for split groups (e.g. DEU_part1, DEU_part2 → group DEU)
            # by EXACT slice match, never `glob(f"{group}*.md")`: that glob made
            # forSuppliersFourps.md part of forSuppliers's scan (both are real,
            # separate ETTM groups), so one group was handed a sibling's
            # findings and its own slice named designs it does not contain.
            candidate_files = evidence_files_for_group(preprocess_dir, group)
            if not candidate_files:
                continue

            group_flags: list[tuple[str, str, str]] = []
            job_component_counts: dict[str, int] = {}
            job_tmap_counts: dict[str, int] = {}
            job_error_handler: dict[str, bool] = {}
            job_has_context_var: dict[str, bool] = {}
            current_job: str | None = None

            for md_file in candidate_files:
                try:
                    content = md_file.read_text(encoding="utf-8")
                except OSError:
                    continue

                for line in content.splitlines():
                    # Track the current executable design without conflating a
                    # Talend Route with a Talend Job.
                    heading_match = _DESIGN_HEADING_RE.match(line)
                    if heading_match:
                        current_job = heading_match.group(1).strip()
                        job_component_counts.setdefault(current_job, 0)
                        job_tmap_counts.setdefault(current_job, 0)
                        job_error_handler.setdefault(current_job, False)
                        job_has_context_var.setdefault(current_job, False)
                    elif current_job:
                        # Count components
                        if line.startswith("#### ") and "(" in line:
                            job_component_counts[current_job] = (
                                job_component_counts.get(current_job, 0) + 1
                            )
                            # Count tMap components
                            if "tMap" in line:
                                job_tmap_counts[current_job] = (
                                    job_tmap_counts.get(current_job, 0) + 1
                                )
                            # Detect error handling components
                            if any(ec in line for ec in ("tLogCatcher", "tDie", "tWarn", "tRun")):
                                job_error_handler[current_job] = True

                        # Detect context variable usage
                        if "context." in line.lower():
                            job_has_context_var[current_job] = True

                    # Credential patterns
                    if cred_re and cred_re.search(line):
                        detail = line.strip()[:100]
                        group_flags.append(("CRITICAL", "hardcoded_credential", detail))

                    # Literal IP addresses
                    if ip_re and ip_re.search(line):
                        detail = line.strip()[:80]
                        group_flags.append(("HIGH", "literal_ip_address", detail))

                    # Localhost/hardcoded env
                    if env_re and env_re.search(line):
                        detail = line.strip()[:80]
                        group_flags.append(("MEDIUM", "hardcoded_env_value", detail))

            design_counts[group] = len(job_component_counts)

            # Per-job heuristic checks
            for job, count in job_component_counts.items():
                if count > config.qs_god_profile_threshold:
                    group_flags.append(
                        ("HIGH", "god_profile", f"{job}: {count} components")
                    )
                elif count > config.qs_large_job_threshold:
                    group_flags.append(
                        ("MEDIUM", "large_job", f"{job}: {count} components")
                    )

            for job, count in job_tmap_counts.items():
                if count > config.qs_complex_tmap_threshold:
                    group_flags.append(
                        ("HIGH", "complex_tmap", f"{job}: {count} tMap steps")
                    )

            for job, has_handler in job_error_handler.items():
                if not has_handler and job_component_counts.get(job, 0) > 3:
                    group_flags.append(
                        ("HIGH", "missing_error_handler", f"{job}: no tLogCatcher/tDie/tWarn")
                    )

            for job, has_ctx in job_has_context_var.items():
                if not has_ctx and job_component_counts.get(job, 0) > 2:
                    group_flags.append(
                        ("LOW", "no_context_vars", f"{job}: no context.* references found")
                    )

            if group_flags:
                findings[group] = group_flags

        # Sort groups by severity score
        sev_score = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        group_scores: list[tuple[int, str]] = []
        for group, flags in findings.items():
            score = sum(sev_score.get(s, 0) for s, _, _ in flags)
            group_scores.append((score, group))
        group_scores.sort(reverse=True)

        lines: list[str] = [
            "# Quick Scan Report\n\n",
            "Generated by Phase 1.5 lightweight scan (no LLM).\n",
            "Phase 2 agents: read this to calibrate analysis depth.\n\n",
            "## Priority Ranking\n\n",
            "| Priority | Group | Critical | High | Medium | Low |\n",
            "|----------|-------|----------|------|--------|-----|\n",
        ]
        for rank, (score, group) in enumerate(group_scores, 1):
            flags = findings[group]
            c = sum(1 for s, _, _ in flags if s == "CRITICAL")
            h = sum(1 for s, _, _ in flags if s == "HIGH")
            m = sum(1 for s, _, _ in flags if s == "MEDIUM")
            lo = sum(1 for s, _, _ in flags if s == "LOW")
            lines.append(f"| {rank} | {group} | {c} | {h} | {m} | {lo} |\n")

        # Groups with no findings
        no_findings = [g for g in groups if g not in findings]
        if no_findings:
            for g in sorted(no_findings):
                lines.append(f"| - | {g} | 0 | 0 | 0 | 0 |\n")

        lines.append("\n## Findings Detail\n\n")
        for _, group in group_scores:
            flags = findings[group]
            lines.append(f"### {group}\n\n")
            for severity, flag, detail in sorted(flags, key=lambda x: -sev_score.get(x[0], 0)):
                lines.append(f"- **{severity}** `{flag}`: {detail}\n")
            lines.append("\n")

        output_path = assessment_dir / "quick_scan.md"
        output_path.write_text("".join(lines), encoding="utf-8")

        # Per-group slices. A Phase 2 agent needs its OWN group's flags to
        # calibrate depth; it was being handed the whole report. Measured on
        # ETTM: 34.3 KB read by each of 20 agents (686 KB) to use ~1.6 KB.
        # These are written from the in-memory findings, NOT by re-parsing
        # quick_scan.md -- re-reading our own generated markdown is the class
        # of coupling that cost run 258 its evidence.
        rank_by_group = {group: rank for rank, (_, group) in enumerate(group_scores, 1)}
        slice_dir = assessment_dir / "quick_scan"
        slice_dir.mkdir(parents=True, exist_ok=True)
        for group in groups:
            flags = findings.get(group, [])
            rank = rank_by_group.get(group)
            header = [
                f"# Quick Scan: {group}\n\n",
                "Generated by Phase 1.5 lightweight scan (no LLM).\n",
                f"Priority rank: {rank} of {len(group_scores)}\n"
                if rank
                else "Priority rank: not ranked (no findings)\n",
                f"Findings: {len(flags)}\n\n",
                (
                    "The full cross-group report is _assessment/quick_scan.md; "
                    "read it only if you need another group's flags.\n\n"
                ),
            ]
            if flags:
                header.append("## Findings\n\n")
                for severity, flag, detail in sorted(
                    flags, key=lambda x: -sev_score.get(x[0], 0)
                ):
                    header.append(f"- **{severity}** `{flag}`: {detail}\n")
            else:
                header.append(
                    "## Findings\n\nNone. No anti-pattern heuristic fired for this "
                    "group. That is a real result, not a scan failure -- analyse the "
                    "group on its evidence, at normal depth.\n"
                )
            (slice_dir / f"{group}.md").write_text("".join(header), encoding="utf-8")

        logger.info(
            "Quick scan complete: %d groups flagged, %d total findings, "
            "%d per-group slices - %s",
            len(findings),
            sum(len(f) for f in findings.values()),
            len(groups),
            output_path,
        )
        return findings, design_counts

    # ------------------------------------------------------------------ #
    # Phase 1.6 — the Phase 2 work plan the orchestrator batches from
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write_phase2_work_plan(
        staging_dir: Path,
        groups: list[str],
        *,
        scan_dir: Path,
        findings: Mapping[str, Sequence[tuple[str, str, str]]] | None,
        design_counts: Mapping[str, int] | None,
        unit_allowlist: set[str] | None = None,
    ) -> Phase2WorkPlan:
        """Build and stage ``_assessment/phase2_work_plan.md``.

        Advisory input, not an instruction: the orchestrator reads it, may
        deviate, and records what it actually did in ``_knowledge/decisions.md``.
        """
        preprocess_dir = staging_dir / "_preprocess"
        items = build_group_work_items(
            preprocess_dir,
            groups,
            findings=findings,
            design_counts=design_counts,
        )
        workers, target = plan_phase2_workers(items)
        item_names = [item.name for item in items]
        reusable = tuple(
            (design, kind, resolve_owning_group(group, item_names))
            for design, kind, group in read_reusable_artifacts(
                scan_dir, unit_allowlist=unit_allowlist
            )
        )
        plan = Phase2WorkPlan(
            groups=tuple(items),
            workers=tuple(tuple(names) for names in workers),
            target_weight_bytes=target,
            composition=read_artifact_composition(
                scan_dir, unit_allowlist=unit_allowlist
            ),
            reusable=reusable,
            risk_scan_available=findings is not None,
        )
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        (assessment_dir / PHASE2_WORK_PLAN_FILENAME).write_text(
            render_phase2_work_plan(plan), encoding="utf-8"
        )
        logger.info(
            "Phase 2 work plan: %d groups -> %d workers (max %d groups per "
            "worker, ~%d dispatch wave(s)), %d reusable design(s)",
            len(plan.groups),
            len(plan.workers),
            plan.max_groups_in_a_worker,
            plan.dispatch_waves,
            len(plan.reusable),
        )
        return plan

    # ------------------------------------------------------------------ #
    # Deterministic scorecard marker (used when the LLM scorecard is off)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write_omitted_scorecard_marker(
        staging_dir: Path, groups: list[str], total_jobs: int
    ) -> None:
        """State, in Python, that no scores were computed for this run.

        The Phase 2.6 scoring sub-agent reads EVERY group analysis (520 KB on
        ETTM) to produce one advisory file. It is disabled by default
        (MCP_DOCGEN_LLM_SCORECARD_ENABLED, config default False) because the
        same phase was measured in the custom lane at ~0.8M tokens with
        contract gaps.

        Downstream documents must degrade explicitly rather than invent
        numbers, so this marker is written unconditionally in that case.
        Wording deliberately tracks the custom lane's marker so the two lanes
        read the same to a doc writer.
        """
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        (assessment_dir / "scorecard.md").write_text(
            "# Assessment Scorecard\n\n"
            f"Canonical inventory: {total_jobs} designs across "
            f"{len(groups)} groups.\n\n"
            "> **SCORECARD OMITTED:** no quality or risk scores were computed "
            "for this run. There are no dimension scores, ratings or rankings "
            "to cite.\n\n"
            "Writers: present risk and quality findings directly from the "
            "evidence you already have -- each group analysis's RISK_FLAGS and "
            "ERROR_HANDLING sections, and the per-group risk flags. Where a "
            "score would go, say that scores were not computed for this run. "
            "Do not invent a number.\n\n"
            "Do not copy this notice, or any part of it, into a client "
            "document. State the absence in your own words, in client "
            "language.\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # _doc_templates.md -- written to _preprocess/ for Phase 3 agents
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write_doc_templates(preprocess_dir: Path) -> None:
        """Write strict document section specs so Phase 3 agents have a stable template.

        This prevents post-hoc patching by defining required sections, optional
        sections, and naming conventions upfront.
        """
        content = """\
# Document Templates

Phase 3 agents: read this BEFORE writing any document.
These templates define the required structure for every output file.
Do NOT add sections not listed here without a clear content reason.
Do NOT rename sections.

---

## 00_AGENT_BRIEF.md
Required sections (in order):
1. Project Orientation
2. Documentation Map
3. Onboarding
4. Common Tasks
5. Known Traps
6. Glossary

## 01_ARCHITECTURE_MAP.md
Required sections:
1. Topology
2. Entry Points
3. Dependencies
4. Boundaries

---

## 02_COMPONENT_INVENTORY.md
Required sections:
1. Canonical Unit Inventory
2. Source Artifacts
3. Components
4. Dependencies
5. Scanner-Limited Evidence

---

## 03_DATA_FLOWS_AND_LINEAGE.md
Required sections:
1. Data Flow
2. Lineage
3. Transformation
4. Validation

---

## 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md
Required sections:
1. Business Rules
2. Transformation
3. Hardcoded Values
4. Impact

---

## 05_RUNTIME_CONFIG_AND_EXTERNALS.md
Required sections:
1. Runtime Config
2. External Systems
3. Credentials
4. Environment

---

## 06_OPERATIONS_TESTING_AND_DEBUGGING.md
Required sections:
1. Run
2. Debug
3. Verify
4. Recovery
5. Testing

---

## 07_QUALITY_SECURITY_RISK_SCORECARD.md
Required sections:
1. Score
2. Confidence
3. Evidence
4. Red Flags
5. Quick Wins
6. Unknowns

---

## 08_CHANGE_PLAYBOOK.md
Required sections:
1. Change Impact
2. Inspect
3. Test
4. Rollback

---

## Naming & Formatting Rules
- All filenames: exactly as shown above
- Every doc MUST have a table of contents as the first section
- EVERY character you write must be ASCII (codepoints 0-127). This file is read on
  Windows under cp1252, where a non-ASCII character corrupts the document.
  Named, not shown, so this instruction does not itself put the characters in your
  context: do not use Unicode box-drawing glyphs (build tables from `|`, `-` and `+`),
  do not use en/em dashes (write `-` or `--`), do not use curly quotes, arrows or
  non-Latin scripts. A published run leaked CJK characters into a quoted XML
  namespace, which turned a real identifier into a false one.
- Business rule tags: BR-{GROUP}-NNN (e.g. BR-DEU-001)
- Risk severity: CRITICAL / HIGH / MEDIUM / LOW (all caps)
- Job references: always include full job name, not just partial
- Component references: include platform-specific identifiers where present
- Evidence Register confidence cells must start with plain unformatted HIGH / MEDIUM / LOW text;
  do not bold, italicize, quote, or wrap that leading token in backticks; notes may follow it
"""
        (preprocess_dir / "_doc_templates.md").write_text(content, encoding="utf-8")
        logger.info("Wrote _doc_templates.md (%d bytes)", len(content))

    async def generate_docs(
        self,
        project_id: str,
        investigation_model: str | None = None,
        model: str | None = None,
        write_model: str | None = None,
        plugin_pack_id: int | None = None,
        nexus_pack_ids: list[str] | None = None,
        nexus_pack_snapshot: NexusPackSnapshot | None = None,
        nexus_presentation: NexusPresentation | None = None,
        live_evidence: bool = True,
        resume: bool = True,
        run_id: int | None = None,
        doc_generation_provider: str | None = None,
        instruction_pack_dir: str | None = None,
        enterprise_mode: bool | None = None,
        scoped_docs: set[str] | None = None,
        partition=None,
        initiating_user_id: int | None = None,
        generation_profile: dict | None = None,
    ) -> dict:
        """Spawn Claude Code CLI to generate docs for a project.

        When resume=True (default), checks for existing docs_staging/ artifacts
        and skips already-completed phases. Set resume=False for a clean run.

        ``plugin_pack_id`` is an optional per-run override. When supplied it
        takes precedence over the project's saved ``plugin_pack_id``. Pass
        ``None`` (the default) to keep the existing per-project behaviour.

        ``partition`` (a ``partition_planner.Partition``) scopes this run to one
        composite sub-project: preprocessing, the facts manifest, coverage and
        the call budget see only that partition's units, and all output lands
        under ``output/{project_id}/{subproject}/`` (its own docs_staging,
        generation lock and atomic swap). ``None`` => whole-project run with
        today's layout, unchanged.

        Returns dict with success, cost_usd, duration_ms, input_tokens, etc.
        """
        from mcp_server.providers.custom.partition_planner import (
            WHOLE_PROJECT_PARTITION,
            partition_output_subdir,
        )
        from mcp_server.services.generation_profile import normalize_profile, validate_profile_route

        generation_profile = normalize_profile(generation_profile)

        output_dir = config.project_root / "output" / project_id
        unit_allowlist: set[str] | None = None
        if partition is not None:
            subdir = partition_output_subdir(partition.name)
            if subdir is not None:
                output_dir = output_dir / subdir
            if partition.name != WHOLE_PROJECT_PARTITION:
                unit_allowlist = set(partition.unit_names)

        # Use a staging directory so old docs survive if generation fails
        staging_dir = output_dir / "docs_staging"
        try:
            acquire_generation_lock(
                staging_dir,
                project_id=project_id,
                run_id=run_id,
                stale_after_seconds=config.doc_generation_lock_stale_seconds,
            )
        except GenerationLockError as exc:
            return {
                "success": False,
                "error": str(exc),
                "model_name": model or config.doc_generation_model,
                "write_model_name": write_model or config.doc_generation_write_model,
            }

        write_run_manifest(
            staging_dir,
            run_id=run_id,
            project_id=project_id,
            phase="startup",
            status="running",
            extra={"resume_requested": resume, "enterprise_mode": config.doc_generation_enterprise_mode_default if enterprise_mode is None else enterprise_mode},
        )

        # Detect resume state before deciding whether to wipe staging
        resume_state: dict | None = None
        if resume and staging_dir.exists():
            resume_state = self._detect_resume_state(staging_dir)

        # A scoped reingestion (scoped_docs set) arrives with incremental staging
        # already prepared by setup_incremental_staging: the unchanged groups'
        # analyses are restored under _analysis/ and only the changed groups were
        # removed. Wiping here would delete those restored analyses and force a
        # full Phase 1/2 regeneration of every group. Preserve them.
        if resume_state is None and scoped_docs is None:
            # Fresh run — wipe any leftover staging, preserving the active lock.
            for child in list(staging_dir.iterdir()) if staging_dir.exists() else []:
                if child.name == ".generation.lock":
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="startup",
                status="running",
                extra={"fresh_staging": True},
            )

        for d in [
            staging_dir,
            staging_dir / "_analysis",
            staging_dir / "_assessment",
            output_dir / "logs",
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

        try:
            persist_generation_profile(
                staging_dir, generation_profile,
                resume=resume_state is not None or scoped_docs is not None,
            )
            nexus_execution_receipt = build_nexus_execution_receipt(
                pack_ids=nexus_pack_ids or [],
                snapshot=nexus_pack_snapshot,
                presentation=nexus_presentation,
            )
            persist_nexus_execution_receipt(staging_dir, nexus_execution_receipt)
        except Exception as exc:
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="startup",
                status="failed",
                extra={"error": str(exc)},
            )
            release_generation_lock(staging_dir, run_id=run_id)
            return {
                "success": False,
                "error": str(exc),
                "model_name": model or config.doc_generation_model,
                "write_model_name": write_model or config.doc_generation_write_model,
            }

        # Absolute paths for the prompt (subprocess cwd differs from config.project_root)
        staging_abs = str(staging_dir)
        logs_abs = str(output_dir / "logs")

        # Look up the project's platform from the DB
        plugin_pack_dir: Path | None = None
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(project_id)
            if project is None:
                raise RuntimeError(
                    f"Project {project_id!r} not found in database — cannot determine platform"
                )
            platform = project.platform
            effective_plugin_pack_id = (
                plugin_pack_id
                if plugin_pack_id is not None
                else getattr(project, "plugin_pack_id", None)
            )
            if effective_plugin_pack_id is not None:
                pack_row = (
                    await db.execute(
                        select(PluginPack).where(PluginPack.id == effective_plugin_pack_id)
                    )
                ).scalar_one_or_none()
                if pack_row is None:
                    error = (
                        "Selected Pack is no longer registered; governed generation requires "
                        "a certified Git/PR Pack."
                    )
                    write_run_manifest(
                        staging_dir,
                        run_id=run_id,
                        project_id=project_id,
                        phase="startup",
                        status="failed",
                        extra={"error": error},
                    )
                    release_generation_lock(staging_dir, run_id=run_id)
                    return {
                        "success": False,
                        "error": error,
                        "model_name": model or config.doc_generation_model,
                        "write_model_name": write_model or config.doc_generation_write_model,
                    }
                try:
                    await pack_governance.require_generation_authority(pack_row)
                except PackGovernanceDenied as exc:
                    # This is the TOCTOU check after the queued route check.
                    error = str(exc)
                    write_run_manifest(
                        staging_dir,
                        run_id=run_id,
                        project_id=project_id,
                        phase="startup",
                        status="failed",
                        extra={"error": error},
                    )
                    release_generation_lock(staging_dir, run_id=run_id)
                    return {
                        "success": False,
                        "error": error,
                        "model_name": model or config.doc_generation_model,
                        "write_model_name": write_model or config.doc_generation_write_model,
                    }
                plugin_pack_dir = Path(pack_row.path)

        # Compute project metrics early (needed for preprocessing decisions)
        scanner = ScannerService()
        scan_output_dir = str(config.intodoc_output_dir / project_id)
        chunk_defs = scanner.get_chunk_definitions(
            scan_output_dir, unit_allowlist=unit_allowlist,
        )
        total_jobs = chunk_defs.get("total_jobs", 0)

        # Step 7: Stale scan detection — compare scan mtime against manifest
        manifest_path = staging_dir / "_manifest.json"
        if resume_state is not None:
            scan_dir = config.intodoc_output_dir / project_id / "scan"
            scan_manifest = scan_dir / "manifest.json"
            if scan_manifest.exists() and manifest_path.exists():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest_ts = manifest_data.get("scan_mtime", 0)
                    current_scan_mtime = scan_manifest.stat().st_mtime
                    if current_scan_mtime > manifest_ts:
                        logger.info(
                            "Scan data changed since staging was created "
                            "(scan=%.0f > manifest=%.0f) — re-preprocessing",
                            current_scan_mtime, manifest_ts,
                        )
                        await self.log_manager.emit(
                            project_id,
                            "Scan data changed since last run — re-preprocessing (keeping analysis if groups match)",
                            level="warning",
                        )
                        # Invalidate preprocessing but keep analysis/docs
                        preprocess_dir = staging_dir / "_preprocess"
                        if preprocess_dir.exists():
                            shutil.rmtree(preprocess_dir, ignore_errors=True)
                        resume_state["preprocess_exists"] = False
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Could not check stale scan: %s", e)

        # Pre-process scan data (skip if resuming with valid preprocess files)
        skip_preprocess = (
            resume_state is not None
            and resume_state.get("preprocess_exists", False)
        )

        # Universal extraction cache (services/extraction_cache.py). _preprocess/
        # is a pure function of the scan artifacts + the preprocess/redaction
        # code version + platform + the partition unit allowlist, and never of
        # the pack, so a second pack over the same project reuses this layer
        # instead of re-running the deterministic extraction.
        #
        # No extra staleness check is needed here: compute_extraction_key is
        # built on compute_scan_fingerprint, which hashes scan_result.json,
        # scan/manifest.json, scan/lineage.json and scan/landscape.json
        # (business_topology/evidence.py::_SCAN_ARTIFACTS). Any change to the
        # scan therefore produces a DIFFERENT key and a cache MISS, so the
        # mtime-based stale-scan invalidation above stays authoritative and
        # cannot be defeated by a restore.
        extraction_key = ""
        restored_groups: list[str] | None = None
        if (
            not skip_preprocess
            # A scoped reingestion prepares staging deliberately (unchanged
            # groups' analyses restored, changed groups removed) — mirror the
            # fresh-staging guard above and never overwrite that.
            and scoped_docs is None
            and config.extraction_cache_enabled
        ):
            extraction_key = compute_extraction_key(
                Path(scan_output_dir),
                platform=platform,
                unit_allowlist=unit_allowlist,
            )
            if extraction_key:
                try:
                    restored_groups = restore_extraction(
                        extraction_cache_dir(output_dir), extraction_key, staging_dir,
                    )
                except OSError as exc:
                    logger.warning("Extraction cache restore failed: %s", exc)
                    restored_groups = None

        if skip_preprocess:
            await self.log_manager.emit(
                project_id,
                "RESUME: Skipping preprocessing (files already exist)",
                level="info",
            )
            # Recover group names from existing preprocess files
            preprocess_dir = staging_dir / "_preprocess"
            preprocess_groups = sorted(
                f.stem for f in preprocess_dir.glob("*.md")
                if not f.name.startswith("_")
            )
        elif restored_groups is not None:
            await self.log_manager.emit(
                project_id,
                f"Extraction cache HIT ({extraction_key[:12]}) — reused "
                f"{len(restored_groups)} pre-processed group bundle(s), "
                "skipping preprocessing",
                level="info",
            )
            preprocess_groups = restored_groups
        else:
            await self.log_manager.emit(
                project_id, "Pre-processing scan data...", level="info",
            )
            preprocess_groups = await self._preprocess_scan_data(
                project_id, staging_dir, platform,
                total_jobs=total_jobs,
                unit_allowlist=unit_allowlist,
            )

        await self.log_manager.emit(
            project_id,
            f"Pre-processed {len(preprocess_groups)} groups: {', '.join(preprocess_groups)}",
            level="info",
        )

        # Write _manifest.json for stale scan detection and resume metadata
        scan_dir = config.intodoc_output_dir / project_id / "scan"
        scan_manifest_path = scan_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "project_id": project_id,
            "platform": platform,
            "groups": preprocess_groups,
            "total_jobs": total_jobs,
            "partition": partition.name if partition is not None else None,
            "model": model or config.doc_generation_model,
            "scan_mtime": scan_manifest_path.stat().st_mtime if scan_manifest_path.exists() else 0,
            "created_at": time.time(),
        }, indent=2), encoding="utf-8")

        total_groups = len(preprocess_groups)

        # Runtime query tool. Staged here, outside the preprocess branch above,
        # so a resumed run and an extraction-cache hit get it too: those runs
        # write the same documents and must not be the ones with no runtime
        # access. Advisory -- a failure to stage is logged, never fatal.
        if stage_runtime_tools(staging_dir, project_id) is None:
            await self.log_manager.emit(
                project_id,
                "Runtime query tool unavailable this run -- documents will carry "
                "only the pre-computed runtime rollup",
                level="warning",
            )

        # The METHOD -- the four phases and every standing rule -- as a file the
        # orchestrating agent reads. Staged here for the same reason as the tool
        # above: a resumed run and an extraction-cache hit skip preprocessing
        # but still do the whole job, and the run brief names this path
        # unconditionally. A brief that points at a method which was never
        # copied costs a tool call to discover and then improvisation.
        # `_method/_shared/` beside it carries the confidence model and the
        # validation checklist METHOD.md defers to.
        if materialize_generation_method(staging_dir) is None:
            await self.log_manager.emit(
                project_id,
                "Generation method could not be staged -- the agent will have no "
                "METHOD.md to read",
                level="warning",
            )
        try:
            materialize_investigation_method(staging_dir, platform)
        except InvestigationMethodError as exc:
            # A digest mismatch means the method library was edited without
            # updating its manifest. Worth seeing in the log; never worth
            # costing a client their documents.
            await self.log_manager.emit(
                project_id, f"Investigation method not staged: {exc}", level="warning",
            )

        # Resume was detected BEFORE preprocessing, so its checklist came from
        # the PREVIOUS run's manifest. Preprocessing has just rewritten that
        # manifest, and a changed scan produces a different group set -- the
        # stale-scan branch above deletes `_preprocess/` for exactly that case.
        # Recompute the remainder against the groups this run will actually
        # analyse, or the prompt names groups that no longer exist and omits
        # ones that do.
        if resume_state is not None:
            self._apply_group_checklist(
                resume_state,
                staging_dir,
                sorted({analysis_group_for_slice(name) for name in preprocess_groups}),
                "manifest" if preprocess_groups else "unknown",
            )

        # Phase 1.5 — lightweight quick scan (Python regex, no LLM)
        # Only run on fresh preprocess (not when resuming with existing scan)
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        quick_scan_path = assessment_dir / "quick_scan.md"
        # Phase 2 sub-agents are pointed at _assessment/quick_scan/{group}.md, so
        # the slice directory must exist too. A run resumed from a staging tree
        # created before per-group slices were introduced has the roll-up but no
        # slices; regenerating is cheap (regex over _preprocess, no LLM) and the
        # alternative is 20 sub-agents reading a path that is not there.
        slices_complete = all(
            (assessment_dir / "quick_scan" / f"{group}.md").exists()
            for group in preprocess_groups
        )
        quick_scan_findings: dict[str, list[tuple[str, str, str]]] | None = None
        quick_scan_designs: dict[str, int] = {}
        if not skip_preprocess or not quick_scan_path.exists() or not slices_complete:
            quick_scan_findings, quick_scan_designs = self._run_quick_scan(
                staging_dir, preprocess_groups
            )

        # Phase 2 work plan. Sub-agents dispatch 3-4 at a time whatever they are
        # asked for, so one sub-agent per group serialises Phase 2 (ETTM run 259:
        # 20 groups, 16 minutes, 5 groups never analysed, Phase 3 never started).
        # This stages the inventory the orchestrator batches from -- per group
        # its evidence size, design count and risk flags, plus a suggested split
        # bin-packed from this estate's own distribution. Advisory: the
        # orchestrator may deviate and records what it used in
        # _knowledge/decisions.md. Cheap, deterministic, and a failure here must
        # not cost the run its documents.
        try:
            work_plan = self._write_phase2_work_plan(
                staging_dir,
                preprocess_groups,
                scan_dir=scan_dir,
                findings=quick_scan_findings,
                design_counts=quick_scan_designs,
                unit_allowlist=unit_allowlist,
            )
        except (OSError, ValueError) as exc:
            logger.warning("Phase 2 work plan could not be staged: %s", exc, exc_info=True)
            await self.log_manager.emit(
                project_id,
                f"Phase 2 work plan unavailable ({exc}) -- the orchestrator will "
                "fall back to one sub-agent per group",
                level="warning",
            )
        else:
            await self.log_manager.emit(
                project_id,
                f"Phase 2 work plan: {len(work_plan.groups)} group(s) -> "
                f"{len(work_plan.workers)} worker(s), at most "
                f"{work_plan.max_groups_in_a_worker} group(s) each, about "
                f"{work_plan.dispatch_waves} dispatch wave(s)"
                + (
                    f"; shared-library pre-pass over {len(work_plan.reusable)} "
                    "reusable design(s)"
                    if work_plan.reusable
                    else "; no reusable designs, so no shared-library pre-pass"
                ),
                level="info",
            )

        # When the LLM scorecard is disabled, write the deterministic marker NOW
        # so every consumer that reads _assessment/scorecard.md (doc 07, the
        # executive summary, the doc-set validator) finds an explicit statement
        # of absence instead of a missing file it might paper over with invented
        # scores. Mirrors the custom lane's _write_omitted_scorecard_marker.
        if not config.docgen_llm_scorecard_enabled:
            self._write_omitted_scorecard_marker(
                staging_dir, preprocess_groups, total_jobs
            )

        # Project memory (services/project_memory.py). Staged from the durable
        # home at output/{project_id}[/{subproject}]/knowledge/ into
        # docs_staging/knowledge/ so the agent can read AND write it, and
        # copied back by persist_project_memory in the finally below.
        #
        # This is also where the deterministic continuity note
        # (knowledge/_since_last_run.md) is written: what changed in the scan
        # since the last SUCCESSFUL run, in Python, with no model call. A run
        # that knows which designs moved does not have to re-derive the estate.
        #
        # Advisory like the quick scan above: memory is an aid, so a failure to
        # stage it degrades the run's knowledge, never its outcome.
        memory_stage: MemoryStageResult | None = None
        try:
            memory_stage = stage_project_memory(
                output_dir=output_dir,
                staging_dir=staging_dir,
                project_id=project_id,
                scan_output_dir=Path(scan_output_dir),
                platform=platform,
                partition_name=partition.name if partition is not None else None,
                unit_allowlist=unit_allowlist,
                run_id=run_id,
            )
        except (OSError, ValueError) as exc:
            logger.warning("Project memory could not be staged: %s", exc, exc_info=True)
            await self.log_manager.emit(
                project_id,
                f"Project memory unavailable this run ({exc}) -- generation continues "
                "without remembered context",
                level="warning",
            )
        else:
            await self.log_manager.emit(
                project_id, memory_stage.summary_line(), level="info",
            )
            if memory_stage.seeded_files:
                await self.log_manager.emit(
                    project_id,
                    "Project memory seeded (empty, first run for this scope): "
                    + ", ".join(memory_stage.seeded_files),
                    level="info",
                )

        # Determine which analysis guide file sub-agents should read
        analysis_guide_file = "_analysis_guide.md"
        if not (staging_dir / "_preprocess" / "_analysis_guide.md").exists():
            analysis_guide_file = "_analysis_prompts*.md"  # legacy fallback

        # Resolve provider and models through the shared model strategy registry.
        # Per-instance overrides from the dashboard Models tab take precedence
        # over the env-var defaults so superadmins can change the effective
        # model at runtime without restarting the service.
        analysis_default = await instance_settings_service.get_value(
            "doc_gen_analysis_model", default=config.doc_generation_model
        )
        write_default = await instance_settings_service.get_value(
            "doc_gen_write_model", default=config.doc_generation_write_model
        )
        model_resolution = resolve_generation_models(
            provider_type=doc_generation_provider,
            model=model,
            write_model=write_model,
            default_provider=config.doc_generation_provider,
            default_analysis_model=analysis_default or config.doc_generation_model,
            default_write_model=write_default or config.doc_generation_write_model,
            default_claude_cli_model=config.claude_model,
        )
        resolved_provider = model_resolution.provider_type
        effective_model = model_resolution.analysis_model
        effective_write_model = model_resolution.write_model

        try:
            validate_profile_route(generation_profile, platform=platform, engine=resolved_provider)
        except ValueError as exc:
            release_generation_lock(staging_dir, run_id=run_id)
            return {"success": False, "error": str(exc)}

        # Build DocGenContext and hand off to the configured provider
        ctx = DocGenContext(
            project_id=project_id,
            staging_dir=staging_dir,
            staging_abs=staging_abs,
            logs_abs=logs_abs,
            preprocess_groups=preprocess_groups,
            total_jobs=total_jobs,
            total_groups=total_groups,
            effective_model=effective_model,
            write_model=effective_write_model,
            platform=platform,
            analysis_guide_file=analysis_guide_file,
            enterprise_mode=config.doc_generation_enterprise_mode_default if enterprise_mode is None else enterprise_mode,
            resume_state=resume_state,
            scoped_docs=scoped_docs,
            partition=partition,
            nexus_presentation=nexus_presentation,
            live_evidence=live_evidence,
            run_id=run_id,
            generation_profile=generation_profile,
        )

        provider = get_provider(
            provider_type=resolved_provider,
            model=effective_model,
            investigation_model=investigation_model,
            write_model=effective_write_model,
            log_manager=self.log_manager,
            instruction_pack_dir=instruction_pack_dir,
            plugin_pack_dir=plugin_pack_dir,
        )

        if plugin_pack_dir is not None and not model_resolution.uses_opencode_cli:
            provider = build_plugin_runner(
                plugin_pack_dir,
                inner_provider=provider,
                model_provider=getattr(provider, "model_provider", None),
            )
            await self.log_manager.emit(
                project_id,
                f"Using plugin pack: {plugin_pack_dir.name} (v{provider.manifest.version})",
                level="info",
            )

        resolved_main_model = getattr(provider, "model_provider", None)
        resolved_investigation_model = getattr(provider, "investigation_provider", None)
        resolved_write_model_obj = getattr(provider, "write_provider", None)
        investigation_model_name = getattr(
            resolved_investigation_model,
            "model_name",
            investigation_model or config.doc_generation_investigation_model or effective_model,
        )
        main_model_name = getattr(resolved_main_model, "model_name", effective_model)
        write_model_name = getattr(resolved_write_model_obj, "model_name", effective_write_model)
        if model_resolution.uses_opencode_cli:
            # The Claude-compatible OpenCode transport uses one selected route
            # throughout its single canonical CLI session.
            investigation_model_name = effective_model
            main_model_name = effective_model
            write_model_name = effective_model
        provider_details = (
            f"provider={resolved_provider} phase1={investigation_model_name} "
            f"phase2={main_model_name} phase3={write_model_name}"
        )
        await self.log_manager.emit(
            project_id,
            f"Resolved doc generation routing: {provider_details}",
            level="info",
        )

        async def _log_cb(pid: str, message: str, level: str = "info") -> None:
            await self.log_manager.emit(pid, message, level=level)

        finalized = False
        try:
            refresh_generation_lock(staging_dir, run_id=run_id)
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status="running",
                extra={"provider": resolved_provider, "model": effective_model, "write_model": effective_write_model},
            )
            async with LockHeartbeat(staging_dir, run_id=run_id):
                # The runtime-tool settings must be on this process's
                # environment while the provider launches its subprocess -- that
                # is where both CLI transports copy their environment from.
                with runtime_tool_environment(project_id, staging_dir):
                    try:
                        agent_result = await provider.run(ctx, _log_cb)
                    finally:
                        # Runs on the failure path too: a preserved staging tree
                        # is resumed and re-published later, so leaving the
                        # facility behind only defers the leak.
                        remove_runtime_tools(staging_dir)
            result = agent_result.to_dict()
            # The custom harness builds its topology in Phase 3.7; the CLI
            # transports have no phase of their own, so it is built here, after
            # the documents exist and before the checks and publication below.
            if result.get("success") and (
                model_resolution.uses_claude_cli or model_resolution.uses_opencode_cli
            ):
                await self._build_cli_business_topology(
                    ctx,
                    uses_claude_cli=model_resolution.uses_claude_cli,
                    transport_provider=provider,
                    scan_output_dir=Path(scan_output_dir),
                    unit_allowlist=unit_allowlist,
                    plugin_pack_dir=plugin_pack_dir,
                    log_cb=_log_cb,
                )
            finalized = True
            for message, level in self._finalize_memory_and_language(
                output_dir=output_dir,
                staging_dir=staging_dir,
                memory_stage=memory_stage,
                groups=preprocess_groups,
                success=bool(result.get("success")),
                project_id=project_id,
                platform=platform,
            ):
                await self.log_manager.emit(project_id, message, level=level)
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status=(
                    "completed" if result.get("success")
                    else "cancelled" if result.get("cancelled")
                    else "failed"
                ),
                extra={"error": result.get("error")},
            )
            return result
        except Exception as exc:
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status="failed",
                extra={"error": str(exc)},
            )
            return {
                "success": False,
                "error": str(exc),
                "model_name": effective_model,
                "write_model_name": effective_write_model,
            }
        finally:
            # The success path already finalized (and could await log_manager
            # while doing it). This covers the exception and cancellation
            # paths, where what the agent learned before the failure must still
            # be persisted; it logs through `logger` because awaiting inside a
            # `finally` during cancellation is not safe.
            if not finalized:
                for message, level in self._finalize_memory_and_language(
                    output_dir=output_dir,
                    staging_dir=staging_dir,
                    memory_stage=memory_stage,
                    groups=preprocess_groups,
                    success=False,
                    project_id=project_id,
                    platform=platform,
                ):
                    logger.log(
                        logging.WARNING if level == "warning" else logging.INFO, message
                    )
            release_generation_lock(staging_dir, run_id=run_id)

    # ------------------------------------------------------------------ #
    # Post-generation: business topology for the CLI transports
    # ------------------------------------------------------------------ #

    async def _build_cli_business_topology(
        self,
        ctx: DocGenContext,
        *,
        uses_claude_cli: bool,
        scan_output_dir: Path,
        unit_allowlist: set[str] | None,
        transport_provider: object | None = None,
        plugin_pack_dir: Path | None,
        log_cb,
    ) -> None:
        """Build ``docs_staging/_topology/`` for a claude-cli / opencode-cli run.

        Same pipeline as the custom harness's Phase 3.7
        (``services/business_topology/builder.py``); only the model strategy
        differs. Claude CLI gets a bounded single-turn, tool-less completion
        on the run's own model (``ClaudeCliModelProvider``, at most two calls).
        OpenCode gets the same bound through ``OpenCodeCompletionProvider``: a
        single session on the run's own route, in an empty directory, with
        every tool denied.

        Runtime evidence is fetched in memory for enrichment only. It is NOT
        staged under ``_transient/``: nothing on this lane deletes that
        directory before the swap, so a staged copy would publish.

        Advisory, like everything after ``provider.run``: never raises, and a
        failure is recorded in ``state.json`` and the run log, never in the
        run's outcome.
        """
        from mcp_server.services.business_topology.builder import (
            BoundedModelTopologyLLM,
            TopologyBuildInputs,
            build_business_topology,
        )

        project_id = ctx.project_id
        try:
            runtime_payload = await self._cli_runtime_payload(ctx, scan_output_dir)
            strategy: BoundedModelTopologyLLM | None = None
            reason = ""
            if uses_claude_cli:
                from mcp_server.providers.custom.providers import ClaudeCliModelProvider

                strategy = BoundedModelTopologyLLM(
                    ClaudeCliModelProvider(model_name=ctx.effective_model),
                    max_calls=2,
                    timeout_s=300.0,
                    max_tokens=config.phase3_max_tokens,
                )
            else:
                from mcp_server.providers.opencode_cli import OpenCodeCompletionProvider

                strategy = BoundedModelTopologyLLM(
                    OpenCodeCompletionProvider(
                        command=getattr(transport_provider, "command", None),
                        model_route=getattr(transport_provider, "model_route", None),
                    ),
                    max_calls=2,
                    timeout_s=300.0,
                    max_tokens=config.phase3_max_tokens,
                )
            partition = ctx.partition
            inputs = TopologyBuildInputs(
                project_id=project_id,
                staging_dir=ctx.staging_dir,
                scan_output_dir=scan_output_dir,
                subproject=(
                    partition.name
                    if partition is not None and unit_allowlist is not None
                    else ""
                ),
                unit_allowlist=frozenset(unit_allowlist) if unit_allowlist is not None else None,
                plugin_pack_dir=plugin_pack_dir,
                runtime_payload=runtime_payload,
                llm_unavailable_reason=reason,
                log_prefix="[topology]",
            )

            async def _log(message: str, level: str) -> None:
                await log_cb(project_id, message, level)

            result = await build_business_topology(inputs, strategy, log=_log)
            if strategy is not None:
                result.state.setdefault("model_lanes", {})["calls"] = strategy.calls
            result.write(ctx.staging_dir / "_topology")
            for warning in result.run_warnings:
                await log_cb(project_id, f"[topology] {warning}", "warning")
        except Exception as exc:  # noqa: BLE001 - topology must never fail a docs run
            logger.warning(
                "CLI business topology failed for %s: %s", project_id, exc, exc_info=True
            )
            try:
                await log_cb(
                    project_id,
                    f"[topology] Business topology failed (non-blocking): {exc}",
                    "warning",
                )
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _cli_runtime_payload(ctx: DocGenContext, scan_output_dir: Path) -> dict | None:
        """The run's runtime evidence as a payload, or ``None``. Never raises.

        The same producer the harness uses (``fetch_runtime_evidence``) over the
        same partition-filtered facts-manifest units, so both identifier keys
        (``job_id`` and display name) are sent.
        """
        from mcp_server.providers.custom.partition_planner import filter_manifest
        from mcp_server.providers.custom.runtime_evidence import fetch_runtime_evidence
        from mcp_server.providers.custom.validators import build_facts_manifest

        try:
            facts = build_facts_manifest(ctx.project_id, scan_output_dir)
            if ctx.partition is not None:
                facts = filter_manifest(facts, ctx.partition)
            store = await fetch_runtime_evidence(
                ctx.project_id, list(facts.units), live_evidence=ctx.live_evidence
            )
            return store.to_json()
        except Exception as exc:  # noqa: BLE001 - runtime absence is never a gate
            logger.warning(
                "CLI topology: runtime evidence unavailable for %s: %s", ctx.project_id, exc
            )
            return None

    # ------------------------------------------------------------------ #
    # Post-generation: persist memory, then check client language
    # ------------------------------------------------------------------ #

    @staticmethod
    def _finalize_memory_and_language(
        *,
        output_dir: Path,
        staging_dir: Path,
        memory_stage: MemoryStageResult | None,
        groups: list[str],
        success: bool,
        project_id: str = "",
        platform: str = "",
    ) -> list[tuple[str, str]]:
        """Check attribution, persist memory, guard client language, clean up.

        Synchronous on purpose: it is called from a ``finally`` block on the
        failure path, where awaiting is not safe. It returns the lines the
        caller should emit rather than emitting them itself.

        Every step is ADVISORY and none can raise into the run. That matches
        the four Phase 3 final-check gates, which warn and ship rather than
        raise -- the operator decides what to do about a gap. Memory is copied
        back on failure too: what the agent worked out before the run died is
        still worth keeping. Only a successful run advances the continuity
        baseline.

        Evidence cleanup lives here, not in the agent's Phase 4, because a
        model that deletes ``_analysis/`` and ``_preprocess/`` before any
        deterministic check has seen them makes the attribution guard
        unrunnable by construction. Python removes them, after the check, and
        only on a successful run -- a failed run's staging is what resume
        reads.

        For the same reason this is where the analyses are PRESERVED, in the
        last moment they exist: ``build_rag_support_package`` packages them
        into ``_rag_support/``, the atomic swap carries that into ``docs/``,
        and ``RagBridge.sync_internal_layers`` publishes them as the internal
        ``technical_analysis`` layer. That machinery is lane-agnostic and
        already ran for every lane; only its producer was custom-lane-only
        (``pipeline/phases/phase4_cleanup``), so on this lane the sync found
        nothing and a completed run left no trace of what Phase 2 produced.
        """
        messages: list[tuple[str, str]] = []

        # Coverage first, and it is NOT the attribution check below. Attribution
        # answers "is this analysis about the group it is named for"; coverage
        # answers "does this group have an analysis at all". Nothing in Python
        # asked the second question, so ETTM run 260 published documents that
        # omit five of twenty groups and reported success -- the model's own
        # Phase 2.5 gate was the only check, and on a resume it never ran.
        #
        # ADVISORY, like every other step in this method and like the four
        # Phase 3 final-check gates: it names the gap in the run log and never
        # raises. Making it BLOCKING (no swap, no RAG push) is a publication-
        # policy change and needs the plan gate, not this function.
        # Slice names -> analysis names, once: the coverage line and the RAG
        # evidence package below must ask for the same set of groups, and two
        # copies of that mapping is how they drift.
        expected = sorted({analysis_group_for_slice(name) for name in groups})
        try:
            analysed = set(
                DocGenerationCoordinator._analysed_groups(staging_dir / "_analysis")
            )
            missing = [name for name in expected if name not in analysed]
            if expected and missing:
                messages.append((
                    f"Group coverage: {len(expected) - len(missing)}/{len(expected)} "
                    f"groups analysed. NO USABLE ANALYSIS for: {', '.join(missing)}. "
                    "Documents from this run do not describe these groups.",
                    "warning",
                ))
            elif expected:
                messages.append((
                    f"Group coverage: {len(expected)}/{len(expected)} groups analysed.",
                    "info",
                ))
        except OSError as exc:
            logger.warning("Group coverage check failed: %s", exc, exc_info=True)
            messages.append((f"Group coverage check failed: {exc}", "warning"))

        checked_analyses = 0
        try:
            report = check_group_attribution(staging_dir)
            checked_analyses = report.analyses_checked
            report_path = (
                write_group_attribution_report(staging_dir, report)
                if checked_analyses
                else None
            )
            messages.extend(attribution_warnings(report, report_path=report_path))
        except (OSError, ValueError) as exc:
            logger.warning("Group attribution check failed: %s", exc, exc_info=True)
            messages.append((f"Group attribution check failed: {exc}", "warning"))

        try:
            persisted = persist_project_memory(
                output_dir=output_dir,
                staging_dir=staging_dir,
                snapshot=memory_stage.snapshot if memory_stage is not None else None,
                success=success,
            )
            messages.append((persisted.summary_line(), "info"))
        except (OSError, ValueError) as exc:
            logger.warning("Project memory could not be persisted: %s", exc, exc_info=True)
            messages.append((f"Project memory could not be persisted: {exc}", "warning"))

        if not success:
            # No published document set to check; the guard would report on a
            # partial staging tree and mislead.
            return messages

        try:
            leakage = validate_client_docs(staging_dir)
            report_path = write_leakage_report(staging_dir, leakage)
            messages.append(
                (leakage.summary_line(), "info" if leakage.ok else "warning")
            )
            if not leakage.ok:
                for finding in leakage.findings[:3]:
                    messages.append((
                        f"  {finding.document}:{finding.line} [{finding.category}] "
                        f"{finding.marker}: {finding.excerpt}",
                        "warning",
                    ))
                messages.append((
                    f"  Internal language is advisory, so the documents ship as "
                    f"written; the full report is {report_path.parent.name}/"
                    f"{report_path.name}",
                    "warning",
                ))
        except (OSError, ValueError) as exc:
            logger.warning("Client-language guard failed: %s", exc, exc_info=True)
            messages.append((f"Client-language guard failed: {exc}", "warning"))

        try:
            provenance = validate_provenance(
                staging_dir,
                known_groups=groups,
                known_units=memory_stage.unit_names if memory_stage is not None else (),
            )
            write_provenance_report(staging_dir, provenance)
            messages.append(
                (provenance.summary_line(), "info" if provenance.ok else "warning")
            )
            if not provenance.ok:
                for doc in provenance.documents:
                    for error in doc.errors[:2]:
                        messages.append((f"  {doc.document}: {error}", "warning"))
        except (OSError, ValueError) as exc:
            logger.warning("Provenance validation failed: %s", exc, exc_info=True)
            messages.append((f"Provenance validation failed: {exc}", "warning"))

        messages.extend(
            DocGenerationCoordinator._publish_rag_evidence_layer(
                staging_dir=staging_dir,
                expected_groups=expected,
                project_id=project_id or output_dir.name,
                platform=platform,
            )
        )

        # The working evidence and the staged method, removed now that the
        # deterministic checks that read them have run. All `_`-prefixed, so
        # their presence up to this point never counted as a delivered document
        # nor reached the client corpus. `_method/` is prompt input, not a
        # deliverable: the durable record of which method a run used is the
        # repository, not a copy in every customer's document tree.
        #
        # Conditional on the attribution check having had something to check:
        # a run that published no group analyses either cleaned up itself (the
        # custom lane deletes both directories in its own Phase 4, so this is a
        # no-op there) or did something unexpected, and in the second case the
        # evidence is worth more on disk than the megabytes are worth freed.
        if checked_analyses == 0:
            return messages

        removed: list[str] = []
        for dirname in ("_analysis", "_preprocess", "_method"):
            target = staging_dir / dirname
            if not target.is_dir():
                continue
            shutil.rmtree(target, ignore_errors=True)
            if target.exists():
                messages.append(
                    (f"Could not remove working evidence {dirname}/", "warning")
                )
            else:
                removed.append(dirname)
        if removed:
            messages.append(
                (f"Removed working evidence: {', '.join(removed)}", "info")
            )

        return messages

    @staticmethod
    def _publish_rag_evidence_layer(
        *,
        staging_dir: Path,
        expected_groups: list[str],
        project_id: str,
        platform: str,
    ) -> list[tuple[str, str]]:
        """Package the Phase 2 analyses as the internal RAG evidence layer.

        Measured on run 258 against the scan, the analyses are a strictly
        stronger retrieval corpus than the nine published documents -- 97% of
        designs named against 74%, 69% of components against 19%, 62% of
        quoted code verbatim against ~0%, and 1.7x the bytes -- and until this
        ran they were deleted a few lines below and never reached RAG on this
        lane.

        Advisory, like every other step in the caller: a failure here costs
        the run nothing. It is also SKIPPED rather than run when there is
        nothing to package, because ``build_rag_support_package`` recreates
        ``_rag_support/phase2_analysis/`` from scratch -- on the custom lane,
        whose Phase 4 has already built the package and deleted
        ``_analysis/``, running it here would replace a good package with an
        empty one.
        """
        analysis_dir = staging_dir / "_analysis"
        try:
            stored = analysis_store.list_groups(analysis_dir)
        except OSError as exc:
            logger.warning("RAG evidence layer: cannot list analyses: %s", exc)
            return [(f"RAG evidence layer not built: {exc}", "warning")]
        if not stored:
            return []

        try:
            manifest = build_rag_support_package(
                staging_dir,
                expected_groups,
                project_id=project_id,
                platform=platform,
            )
        except Exception as exc:  # noqa: BLE001 - see below
            # Broader than the narrow catches above, deliberately. This step
            # preserves evidence; it delivers nothing. It is called from the
            # SUCCESS path inside `generate_docs`'s try block, so any escaping
            # exception -- including the plain-Exception SectionContractError
            # the store raises on lost evidence -- would turn a run that
            # produced its documents into a failed one. Preservation failing
            # is a warning; the documents still ship.
            logger.warning("RAG evidence layer could not be built: %s", exc, exc_info=True)
            return [(f"RAG evidence layer could not be built: {exc}", "warning")]

        artifacts = manifest["artifacts"]
        total_bytes = sum(int(a["bytes"]) for a in artifacts)
        messages: list[tuple[str, str]] = [(
            f"RAG evidence layer: {len(artifacts)} group analysis artifact(s), "
            f"{total_bytes} bytes, published as the internal "
            f"'{LAYER_TECHNICAL_ANALYSIS}' layer.",
            "info",
        )]
        if manifest["missing_groups"]:
            messages.append((
                "  No analysis to publish for: "
                + ", ".join(manifest["missing_groups"]),
                "warning",
            ))
        for failure in manifest["failed_slices"]:
            messages.append((
                f"  Unreadable analysis {failure['source_path']}: {failure['error']}",
                "warning",
            ))
        residue = manifest["internal_language"]
        if residue["findings"]:
            messages.append((
                f"  Internal language in the analyses: {residue['findings']} "
                f"finding(s), {residue['redactable']} removed at the RAG "
                "boundary; the remainder is prose and is reported only.",
                "info",
            ))
        return messages
