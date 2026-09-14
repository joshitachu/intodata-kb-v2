"""Shared scaffolding for the ``agent-workspace-v1`` suite.

The workspace builder is written independently of the runner, so nothing here
imports it: a fixture workspace is constructed straight from the path
constants in ``providers/agent_workspace/contract.py``, which is the only
surface the two halves agree on.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.contract import (
    AgentRunResult,
    WorkspaceReceipt,
)


def make_workspace(staging_dir: Path, *, goal: str = "Document this estate") -> Path:
    """Materialise the contract layout. No scan, no model, no builder import."""

    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / contract.AGENT_GUIDE).write_text(
        "# Agent guide\n\nEvidence lives in scan/ and facts/.\n", encoding="utf-8"
    )
    (staging_dir / contract.GOAL).write_text(f"# Goal\n\n{goal}\n", encoding="utf-8")
    (staging_dir / contract.INDEX).write_text(
        "# Index\n\nWhat this estate contains, and where.\n", encoding="utf-8"
    )
    for root in contract.EVIDENCE_ROOTS:
        (staging_dir / root).mkdir(parents=True, exist_ok=True)
    for name in contract.INSTRUCTION_FILES:
        (staging_dir / contract.INSTRUCTIONS / name).write_text(f"# {name}\n", encoding="utf-8")
    (staging_dir / contract.PLATFORM_GUIDE_DIR).mkdir(parents=True, exist_ok=True)
    (staging_dir / contract.PLATFORM_GUIDE_INDEX).write_text(
        "# Guides\n\nOpen one when the investigation needs it.\n", encoding="utf-8"
    )
    (staging_dir / contract.NAVIGATION).mkdir(parents=True, exist_ok=True)
    (staging_dir / contract.SCAN / "jobs").mkdir(parents=True, exist_ok=True)
    (staging_dir / contract.SCAN / "jobs" / "Orders.json").write_text(
        json.dumps({"name": "Orders"}), encoding="utf-8"
    )
    for root in contract.WRITABLE_ROOTS:
        (staging_dir / root).mkdir(parents=True, exist_ok=True)
    return staging_dir


def seed_units(staging_dir: Path, names, *, group: str = "Sales") -> tuple[str, ...]:
    """Write a ``facts/navigation/units`` view holding one row per name.

    The population the coverage ledger is accountable for is recomputed from
    this view, so a test that needs 142 designs writes 142 rows here rather
    than 142 roster lines -- the roster is agent-writable and deliberately not
    the source of truth.
    """

    directory = Path(staging_dir) / contract.NAVIGATION / "units"
    directory.mkdir(parents=True, exist_ok=True)
    unit_ids = tuple(str(name) for name in names)
    rows = [
        {
            "view": "units",
            "unit_id": unit_id,
            "display_name": unit_id,
            "kind": "job",
            "role": "standalone",
            "group": group,
            "source_root": "PROJ",
            "version": "0.1",
            "component_count": 3,
            "component_types": ["tMap"],
            "entry_point": index == 0,
            "has_error_handling": False,
            "scan_quality": "complete",
            "callers": [],
            "callees": [],
            "mapping_tables": [],
            "evidence": f"scan/jobs/{group}/{unit_id}.json",
        }
        for index, unit_id in enumerate(unit_ids)
    ]
    (directory / "part_000.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    return unit_ids


def classify(staging_dir: Path, entries) -> None:
    """Append classification lines exactly as an agent would.

    ``entries`` is an iterable of dicts. Written with ``a`` so a test can build
    a history in several calls and prove that the last line for a unit wins.
    """

    path = Path(staging_dir) / contract.COVERAGE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def workspace_builder(staging_dir_hook=None):
    """Return a ``WorkspaceBuilder`` that stages the fixture layout."""

    def build(
        *,
        project_id: str,
        staging_dir: Path,
        platform: str,
        goal: str,
        scope_mode: str | None = None,
    ):
        # Accepted and unused: the fixture stages a fixed layout, but the
        # signature has to match `contract.WorkspaceBuilder` or the runner's
        # call fails with a TypeError that surfaces as "run failed
        # unexpectedly" rather than as a contract mismatch.
        del scope_mode
        make_workspace(Path(staging_dir), goal=goal)
        if staging_dir_hook is not None:
            staging_dir_hook(Path(staging_dir))
        return WorkspaceReceipt(
            project_id=project_id,
            platform=platform,
            workspace_path=Path(staging_dir),
            design_count=1,
            group_count=1,
            mapping_table_count=0,
            instruction_files=contract.INSTRUCTION_FILES,
        )

    return build


class RecordingAgent:
    """A fake ``AgentProvider``: it writes files and records what it was given.

    ``behaviours`` is one entry per attempt. Each is a callable taking the
    request and returning an :class:`AgentRunResult`; it may write into
    ``output/`` and ``notes/`` first, exactly as a real adapter's session
    would.
    """

    provider_id = "fake"

    def __init__(self, behaviours) -> None:
        self._behaviours = list(behaviours)
        self.requests: list = []

    async def run_agent(self, request, *, log_cb):
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._behaviours) - 1)
        return self._behaviours[index](request)


def analyse_every_group(staging_dir: Path, *, body: str | None = None) -> tuple[str, ...]:
    """Write one analysis per group, as a finishing agent must.

    The gate requires the analysis layer to exist before a whole-estate
    completion claim is accepted, so a test about anything else still has to
    satisfy it. The body clears ``analysis.MIN_ANALYSIS_BYTES`` -- a stub that
    only cleared existence would make every fixture run report ``analysis_thin``
    and bury the advisory that matters.
    """

    from mcp_server.providers.agent_workspace import analysis

    staging_dir = Path(staging_dir)
    groups = analysis.groups_from_workspace(staging_dir)
    directory = staging_dir / contract.ANALYSIS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for group in groups:
        text = body or (
            f"# {group}\n\nWhat the designs in this group do, the flows they carry, "
            "the interfaces they touch, the mappings they apply, the configuration "
            "that changes their behaviour, the error paths they take, and the "
            "questions this investigation could not settle from the staged "
            "evidence alone.\n\n"
            + ("Synthetic fixture content, written to clear the thin-file floor. " * 8)
        )
        (directory / f"{group}.md").write_text(text, encoding="utf-8")
    return groups


def account_for_everything(staging_dir: Path, *, disposition: str = "operational") -> int:
    """Classify every design in the recomputed population, then claim completion.

    The accountability default for a test that is about something ELSE --
    restart plumbing, token accounting, clean-room assembly. Those tests need a
    run that is allowed to publish, and after the completion handshake landed
    "the provider exited zero" stopped being enough to earn that.

    ``operational`` rather than ``material`` on purpose: it is a complete and
    valid accounting that needs no integration model behind it, so a test about
    the runner does not have to construct a journey graph to get past the gate.
    Accountability scenarios have their own module and build the real thing.
    """

    from mcp_server.providers.agent_workspace import ledger

    staging_dir = Path(staging_dir)
    analyse_every_group(staging_dir)
    population = ledger.roster_from_workspace(staging_dir)
    classify(
        staging_dir,
        (
            {
                "unit_id": record.unit_id,
                "disposition": disposition,
                "reason": "synthetic fixture: not a business integration",
            }
            for record in population
        ),
    )
    report = ledger.read_ledger(staging_dir)
    write_receipt(
        staging_dir,
        coverage_digest=report.digest(),
        candidate_counts=report.counts,
        unreviewed_count=len(report.unreviewed),
        unresolved_count=len(report.unresolved),
    )
    return len(population)


def write_receipt(staging_dir: Path, **overrides) -> Path:
    """Write ``notes/completion.json`` exactly as a finishing agent would."""

    payload = {
        "scope_mode": "whole_estate",
        "coverage_digest": "",
        "candidate_counts": {},
        "unreviewed_count": 0,
        "unresolved_count": 0,
        "integration_count": 0,
        "journey_count": 0,
        "document_paths": [],
        "limitations": [],
        "complete": True,
        "notes": "",
    }
    payload.update(overrides)
    path = Path(staging_dir) / contract.COMPLETION_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def writes(
    *documents: str,
    status: str = "completed",
    error: str | None = None,
    accountable: bool = True,
):
    """Behaviour helper: write ``documents`` under ``output/`` then report.

    ``accountable`` also files a complete coverage accounting and a completion
    receipt, which is what a finishing agent does. Pass ``False`` for the case
    that matters on its own: a provider that exits normally having claimed
    nothing, which is a stopped session and not a finished goal.
    """

    def behaviour(request) -> AgentRunResult:
        for relative in documents:
            path = Path(request.output_path) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"# {Path(relative).stem}\n\nWritten by the fake agent.\n",
                encoding="utf-8",
            )
        if accountable:
            account_for_everything(Path(request.workspace_path))
        return AgentRunResult(
            status=status,  # type: ignore[arg-type]
            provider_id="fake",
            model="fake-model",
            error=error,
        )

    return behaviour


# ---------------------------------------------------------------------------
# A synthetic intodoc output directory
#
# Moved here from ``test_workspace.py`` so more than one test module can build a
# REAL workspace from it. The alternative -- each module writing its own scan --
# is how two fixtures end up disagreeing about what a scan looks like, and then
# about which of them the builder is actually wrong against.
#
# Deliberately tiny and synthetic. The real 142-design estate is gitignored
# client material and is exercised by ``scripts/build_agent_workspace.py`` as a
# manual dry run.
# ---------------------------------------------------------------------------


PUBLISHED_ARTIFACTS = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/lineage.json",
    "scan/landscape.json",
)


def design(name: str, *, group: str, source_root: str = "PROJ") -> dict:
    return {
        "name": name,
        "job_id": name,
        "source_root": source_root,
        "source_design": "process",
        "path": f"{group}/{name}_0.1.item",
        "version": "0.1",
        "group": group,
        "artifact_kind": "job",
        "artifact_role": "standalone",
        "scan_quality": "complete",
        "is_esb_route": False,
        "components": [
            {
                "unique_name": "tMap_1",
                "component_type": "tMap",
                "active": True,
                "declaration": "built_in",
                "params": [],
                "schemas": [],
            }
        ],
        "connections": [],
        "context_groups": [],
        "tmap_data": [],
        "txmlmap_data": [],
        "trunjob_refs": [],
        "artifact_refs": [],
        "raw_routines": [],
    }


def write_scan(
    root: Path,
    *,
    designs: list[dict],
    approve: bool = True,
    omit: tuple[str, ...] = (),
) -> Path:
    """A minimal but structurally faithful intodoc output directory.

    ``omit`` leaves out an optional artifact, so a test can reproduce the
    estate that genuinely has no stylesheets (or no TDM maps) rather than
    approximating it by deleting a file after the receipt was written -- which
    is a different scenario and trips a different check.
    """
    scan_dir = root / "PROJECT"
    (scan_dir / "scan" / "jobs").mkdir(parents=True)

    job_files: dict[str, str] = {}
    for design in designs:
        group_dir = scan_dir / "scan" / "jobs" / design["group"]
        group_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{design['name']}.json"
        (group_dir / filename).write_text(json.dumps(design), encoding="utf-8")
        job_files[design["job_id"]] = f"jobs/{design['group']}/{filename}"

    manifest = {
        "job_calls": {},
        "job_context_links": {},
        "unresolved_context_refs": {},
        "job_routine_links": {},
        "unresolved_routine_refs": {},
        "connection_used_by": {},
        "artifact_channels": [],
        "artifact_endpoints": [],
        "artifact_resources": [],
        "artifact_relations": [],
        "source_roots": [{"id": "PROJ", "path": "PROJ", "role": "execution"}],
        "job_files": job_files,
    }
    inventory = [
        {
            "path": f"PROJ/process/{d['name']}_0.1.item",
            "kind": "job",
            "outcome": "parsed",
            "reason": "supported_artifact_extracted",
        }
        for d in designs
    ] + [
        {
            "path": "PROJ/resources/payload.bin",
            "kind": "unknown",
            "outcome": "unsupported",
            "reason": "unrecognized_file_type",
        },
        {
            "path": "PROJ/resources/mapping.xsl",
            "kind": "xslt",
            "outcome": "indexed",
            "reason": "classified_not_proof_of_parse",
        },
    ]
    ledger = {
        "capability_version": "talend-capability-2",
        "file_outcomes": {"parsed": len(designs), "unsupported": 1, "indexed": 1},
        "constructs": [
            {
                "category": "mapping.tmap",
                "construct": "component",
                "status": "supported",
                "count": len(designs),
                "note": "",
            },
            {
                "category": "schema",
                "construct": "dynamic",
                "status": "dynamic",
                "count": 3,
                "note": "contains an id_Dynamic column",
            },
            {
                "category": "unscanned_directory",
                "construct": "PROJ/resources",
                "status": "unsupported",
                "count": 1,
                "note": "Talend repository directory 'resources': 2 files, 0 parsed",
            },
        ],
    }
    scan_result = {
        "platform": "talend",
        "repo_root": "/src/PROJECT",
        "jobs": designs,
        "metadata_connections": [],
        "context_groups": [],
        "routines": [],
        "manifest": manifest,
        "mapping_accounting": [
            {
                "table_id": "aaaa0000bbbb1111",
                "unit_id": designs[0]["job_id"],
                "component_name": "tMap_1",
                "mapper_kind": "tmap",
                "offered_fields": 2,
                "written_fields": 1,
                "rows": [],
            }
        ],
        "capability_ledger": ledger,
        "source_inventory": inventory,
        "warnings": [
            {
                "kind": "partial_parse",
                "file": "PROJ/process/x.item",
                "message": "unmodelled instruction",
                "phase": "xslt",
            }
        ],
    }

    payloads: dict[str, object] = {
        "scan/manifest.json": manifest,
        "scan/meta.json": {
            "platform": "talend",
            "stats": {},
            "warnings": scan_result["warnings"],
            "source_inventory": inventory,
        },
        "scan/contexts.json": [],
        "scan/connections.json": [],
        "scan/routines.json": [],
        "scan/xslt.json": [],
        "scan/services.json": [],
        "scan/structures.json": [],
        "scan/tdm_maps.json": [],
        "scan/landscape.json": {"systems": [], "applications": [], "edges": []},
        "scan/lineage.json": {"entries": []},
        "scan/mapping_accounting.json": scan_result["mapping_accounting"],
        "scan/capability_ledger.json": ledger,
        "scan_result.json": scan_result,
    }
    for relative, payload in payloads.items():
        if relative in omit:
            continue
        path = scan_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # The scanner's own publication receipt, and the redaction record that
    # corroborates it.
    (scan_dir / "redaction_report.json").write_text(
        json.dumps({"total_redactions": 7, "verification": {"passed": True}}),
        encoding="utf-8",
    )
    approval = {
        "schema_version": 2,
        "approved": approve,
        "allow_redaction_failures": False,
        "artifacts": {
            relative: "sha256:" + hashlib.sha256((scan_dir / relative).read_bytes()).hexdigest()
            for relative in PUBLISHED_ARTIFACTS
        },
        "tree": approval_tree(scan_dir),
        "verifications": {relative: {"passed": True} for relative in PUBLISHED_ARTIFACTS},
    }
    (scan_dir / "publication_approval.json").write_text(
        json.dumps(approval, indent=2), encoding="utf-8"
    )
    return scan_dir


#: Mirrors ``intodoc.cli``: the scanner's receipt binds every artifact a
#: workspace may stage, not only the four aggregates in ``artifacts``. Written
#: out here rather than imported because the MCP server does not depend on the
#: scanner package -- the same reason ``contract.CODE_PARAM_NAMES`` is a copy.
TREE_ALGORITHM = "sha256-relpath-pairs-1"
TREE_ROOTS = ("scan",)
TREE_EXCLUDED_PREFIXES = ("scan/evidence/", "scan/sanitized/")


def approval_tree(scan_dir: Path) -> dict:
    """The receipt's agent-eligible artifact tree for a scan on disk."""

    files: dict[str, str] = {}
    for root in TREE_ROOTS:
        base = scan_dir / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(scan_dir).as_posix()
            if any(relative.startswith(prefix) for prefix in TREE_EXCLUDED_PREFIXES):
                continue
            files[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    files = dict(sorted(files.items()))

    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\n")
    return {
        "algorithm": TREE_ALGORITHM,
        "roots": list(TREE_ROOTS),
        "excluded_prefixes": list(TREE_EXCLUDED_PREFIXES),
        "file_count": len(files),
        "digest": f"sha256:{digest.hexdigest()}",
        "files": files,
    }
