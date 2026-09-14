"""Scanner-aware, bounded evidence packets for Phase 2 analysis.

The scanner may expose multiple roots, typed executable units, references, and
large project-wide derived maps.  Phase 2 tasks must see the relationships that
touch their assigned units without receiving every project row on every call.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable

from mcp_server.providers.custom.dependency_evidence import DependencyEvidenceRepository
from mcp_server.providers.custom.models import FactsManifest


SCOPED_PROJECT_ARTIFACTS = frozenset(
    {
        "change_impact_matrix.md",
        "config_runtime_map.md",
        "data_flow_map.md",
        "dependency_map.md",
        "known_risks.md",
        "sql_usage.md",
    }
)
_FULL_FACTS_NAMES = frozenset({"facts_manifest.json"})
_GLOBAL_EVIDENCE_NAMES = frozenset({"evidence_brief.md", "evidence_registry.md"})
_SEPARATOR_RE = re.compile(r"\|[\s:|-]+\|?")


@dataclass(frozen=True)
class Phase2ContextPacket:
    """Physical bounded packet and its deterministic routing receipt."""

    files: tuple[Path, ...]
    manifest_path: Path
    total_bytes: int
    omitted: tuple[str, ...]
    # Leading bytes of ``files`` that are byte-identical for every task in the
    # run (the cacheable request prefix contributed by this packet).
    shared_prefix_bytes: int = 0


def build_phase2_context_packet(
    *,
    staging_dir: Path,
    facts: FactsManifest,
    group: str,
    task_id: str,
    unit_names: list[str],
    task_evidence: Path,
    shared_context: list[Path],
    investigation_context: list[Path],
    scoped_evidence: Path | None,
    max_bytes: int,
    max_scoped_artifact_bytes: int,
    scan_output_dir: Path | None = None,
    artifact_names: frozenset[str] | None = None,
    include_prompt_pack: bool = True,
    include_investigation: bool = True,
) -> Phase2ContextPacket:
    """Build one complete task packet from canonical scanner scope.

    Raw typed task evidence is always retained.  Project-wide maps are reduced
    to rows that mention an assigned unit, its source path, group, or known
    external system.  Absence from a packet means out of scope, not absent from
    the scanned project.
    """

    packet_root = staging_dir / "_analysis" / "_context_packets" / _safe_name(task_id)
    packet_root.mkdir(parents=True, exist_ok=True)
    selected_unit_names = set(unit_names)
    selected_units = [unit for unit in facts.units if unit.name in selected_unit_names]
    compact_facts = packet_root / "SCOPED_FACTS.json"
    compact_facts.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "canonical-scanner-scope",
                "project": {
                    "id": facts.project_id,
                    "platform": facts.platform,
                    "canonical_unit_count": facts.unit_count,
                    "canonical_unit_label": facts.canonical_unit_label,
                    "group_count": len(facts.groups),
                    "groups": facts.groups,
                    "kind_totals": _kind_totals(facts),
                },
                "scope": {
                    "group": group,
                    "task_id": task_id,
                    "unit_count": len(selected_units),
                    "units": [unit.model_dump() for unit in selected_units],
                },
                "instructions": {
                    "presence": (
                        "The project inventory above is global. This packet contains only "
                        "evidence relevant to scope.units; omitted project evidence is not missing."
                    ),
                    "relationships": (
                        "Scoped relationship rows may name directly connected units outside "
                        "this task. Describe them only as external relationships."
                    ),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    terms = _scope_terms(group, selected_units)
    candidates: list[tuple[int, Path]] = []
    omitted: list[str] = []
    seen: set[Path] = set()

    def add_candidate(priority: int, path: Path | None) -> None:
        if path is None or path in seen or not path.is_file():
            return
        seen.add(path)
        candidates.append((priority, path))

    for path in shared_context:
        if path.name in _FULL_FACTS_NAMES or path.name in _GLOBAL_EVIDENCE_NAMES:
            continue
        if "_prompt_pack" in path.name:
            if include_prompt_pack:
                add_candidate(0, path)
            continue
        if artifact_names is not None and path.name not in artifact_names:
            omitted.append(path.name)
            continue
        if path.name in SCOPED_PROJECT_ARTIFACTS or path.stat().st_size > max_scoped_artifact_bytes:
            scoped = _write_scoped_artifact(
                source=path,
                destination=packet_root / f"scoped_{path.name}",
                terms=terms,
                max_bytes=max_scoped_artifact_bytes,
            )
            if scoped is None:
                omitted.append(path.name)
            else:
                add_candidate(_artifact_priority(path.name), scoped)
            continue
        add_candidate(_artifact_priority(path.name), path)

    if scoped_evidence is not None:
        scoped = _write_scoped_artifact(
            source=scoped_evidence,
            destination=packet_root / "scoped_evidence.md",
            terms=terms,
            max_bytes=max_scoped_artifact_bytes,
            keep_all_when_no_match=True,
        )
        add_candidate(1, scoped)
    if include_investigation:
        for path in investigation_context:
            scoped = _write_scoped_artifact(
                source=path,
                destination=packet_root / f"investigation_{path.name}",
                terms=terms,
                max_bytes=max_scoped_artifact_bytes,
                keep_all_when_no_match=True,
            )
            add_candidate(2, scoped)
    # Source truth gets first claim on the packet budget. Optional deterministic
    # maps and investigation summaries may never crowd out the canonical typed
    # scope or the raw evidence assigned to this task.
    selected: list[Path] = [compact_facts]
    used_bytes = compact_facts.stat().st_size
    if task_evidence != compact_facts:
        selected.append(task_evidence)
        used_bytes += task_evidence.stat().st_size
    dependency_evidence = _write_dependency_evidence(
        scan_output_dir=scan_output_dir,
        destination=packet_root / "ROUTINE_DEPENDENCIES.md",
        unit_names=unit_names,
    )
    if dependency_evidence is not None:
        # Resolved routine bodies are source truth for the assigned callers.
        # Keep them alongside task evidence even when that makes the packet
        # honestly over budget; optional maps are the only budget casualties.
        selected.append(dependency_evidence)
        used_bytes += dependency_evidence.stat().st_size
    for _, path in sorted(candidates, key=lambda item: (item[0], item[1].name)):
        if path in selected:
            continue
        size = path.stat().st_size
        if used_bytes + size > max_bytes:
            omitted.append(_relative(staging_dir, path))
            continue
        selected.append(path)
        used_bytes += size

    # Provider prefix cache: every provider adapter concatenates the context
    # files into ONE request body, so the cacheable prefix runs from the start
    # of the request to the first byte that differs between calls.  Files that
    # are byte-identical for every task in the run (shared project artifacts
    # read straight from staging) therefore LEAD, and the per-task packet files
    # (SCOPED_FACTS, the assigned task evidence, the scoped map copies and the
    # investigation copies -- everything written under ``packet_root``) TRAIL.
    # Same convention as the Phase 2 repair feedback and the Phase 3 slice
    # instruction, which are TAIL files precisely so the head stays byte-stable.
    #
    # SELECTION order above is deliberately unchanged: source truth still gets
    # first claim on the packet budget, so nothing new can be dropped.  Only the
    # EMISSION order changes, and it stays deterministic because ``selected``
    # was built from a total sort on (priority, name).
    per_task_sources = {compact_facts, task_evidence}
    ordered: list[Path] = [
        path
        for path in selected
        if path.parent != packet_root and path not in per_task_sources
    ]
    ordered += [
        path
        for path in selected
        if path.parent == packet_root or path in per_task_sources
    ]
    shared_prefix_bytes = sum(
        path.stat().st_size
        for path in ordered
        if path.parent != packet_root and path not in per_task_sources
    )

    manifest_path = packet_root / "PACKET_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": group,
                "task_id": task_id,
                "unit_names": unit_names,
                "max_bytes": max_bytes,
                "total_bytes": used_bytes,
                "over_budget_required_evidence": used_bytes > max_bytes,
                # Leading run-shared bytes: the portion of this packet that is
                # byte-identical for every task of the run and therefore
                # cacheable as a request prefix.
                "shared_prefix_bytes": shared_prefix_bytes,
                "files": [
                    {
                        "path": _relative(staging_dir, path),
                        "bytes": path.stat().st_size,
                    }
                    for path in ordered
                ],
                "omitted_out_of_scope_or_budget": sorted(set(omitted)),
                "artifact_allowlist": sorted(artifact_names) if artifact_names is not None else None,
                "includes_prompt_pack": include_prompt_pack,
                "includes_investigation": include_investigation,
                "includes_resolved_routine_dependencies": dependency_evidence is not None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return Phase2ContextPacket(
        files=tuple(ordered),
        manifest_path=manifest_path,
        total_bytes=used_bytes,
        omitted=tuple(sorted(set(omitted))),
        shared_prefix_bytes=shared_prefix_bytes,
    )


def _write_dependency_evidence(
    *,
    scan_output_dir: Path | None,
    destination: Path,
    unit_names: list[str],
) -> Path | None:
    """Materialize full scanner-resolved routine source for assigned callers.

    This uses the same canonical/root-aware repository as Phase 3 and OpenCode.
    Old scans without qualified routine metadata remain compatible and simply
    contribute no dependency file instead of guessing by routine name.
    """

    if scan_output_dir is None:
        return None
    try:
        repository = DependencyEvidenceRepository.from_directory(scan_output_dir)
        sections = [repository.render_routines(unit_name) for unit_name in unit_names]
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    content = "\n\n".join(section for section in sections if section.strip()).strip()
    if not content:
        return None
    destination.write_text(
        "# Canonical caller dependency evidence\n\n"
        "The following full sanitized implementations were linked by the scanner "
        "to callers in this task. Explain invoked behavior without transferring "
        "routine ownership to the caller.\n\n"
        f"{content}\n",
        encoding="utf-8",
    )
    return destination


def _write_scoped_artifact(
    *,
    source: Path,
    destination: Path,
    terms: tuple[str, ...],
    max_bytes: int,
    keep_all_when_no_match: bool = False,
) -> Path | None:
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text.encode("utf-8")) <= max_bytes and keep_all_when_no_match:
        destination.write_text(text, encoding="utf-8")
        return destination

    patterns = [_term_pattern(term) for term in terms if term]
    lines = text.splitlines()
    kept: list[str] = []
    pending_heading: str | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            pending_heading = line
            if not kept:
                kept.append(line)
            continue
        is_table = stripped.startswith("|")
        is_separator = is_table and _SEPARATOR_RE.fullmatch(stripped) is not None
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        is_header = is_table and _SEPARATOR_RE.fullmatch(next_line) is not None
        matches = any(pattern.search(line) for pattern in patterns)
        if is_separator or is_header:
            if line not in kept:
                kept.append(line)
            continue
        if not matches:
            continue
        if pending_heading is not None and pending_heading not in kept:
            kept.append(pending_heading)
        kept.append(line)

    if not kept or (len(kept) == 1 and kept[0].lstrip().startswith("#")):
        if not keep_all_when_no_match:
            return None
        kept = lines
    scoped = "\n".join(kept).rstrip() + "\n"
    encoded = scoped.encode("utf-8")
    if len(encoded) > max_bytes:
        scoped = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
        scoped += "\n\n> Scoped artifact truncated at the configured packet limit.\n"
    destination.write_text(scoped, encoding="utf-8")
    return destination


def _scope_terms(group: str, units: Iterable[object]) -> tuple[str, ...]:
    # Project/group/root names occur on nearly every row in scanner-derived
    # maps, so using them as filters recreates a project-wide payload for every
    # unit.  Match on canonical unit identity and exact source path instead.
    # Shared endpoint/channel relationships remain available from rows that
    # directly name the assigned unit and from the deterministic topology.
    del group
    terms: set[str] = set()
    for unit in units:
        terms.add(str(getattr(unit, "name", "")))
        terms.add(str(getattr(unit, "display_name", "")))
        terms.add(str(getattr(unit, "path", "")))
    # Endpoint names such as JMS and broad source-root names are deliberately
    # excluded: matching them would pull every sibling that uses the same
    # technology, whether or not it is connected to this unit.
    return tuple(sorted(term for term in terms if len(term.strip()) >= 2))


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.strip())
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _kind_totals(facts: FactsManifest) -> dict[str, int]:
    totals: dict[str, int] = {}
    for unit in facts.units:
        totals[unit.unit_type] = totals.get(unit.unit_type, 0) + 1
    return dict(sorted(totals.items()))


def _artifact_priority(name: str) -> int:
    return {
        "engineering_facts.md": 1,
        "dependency_map.md": 1,
        "data_flow_map.md": 2,
        "config_runtime_map.md": 2,
        "known_risks.md": 3,
        "sql_usage.md": 3,
        "quick_scan.md": 4,
    }.get(name, 5)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "scope"


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
