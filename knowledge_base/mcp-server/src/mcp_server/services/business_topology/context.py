"""Push-evidence context assembly for the business-topology synthesis task.

The topology step used to PULL its evidence through budget-limited scoped tools
(``read_artifact`` on ``_topology/evidence/*.jsonl``). On large partitions the
model exhausted its tool-call budget before it had read the evidence and then
fabricated an evidence-less topology that failed validation. This module builds
a bounded, priority-ordered set of context files that are PUSHED directly into
the model context (the same discipline Phase 2/3 core generation use), so the
tool budget is never the blocker.

Selection rules:

* Validation-critical evidence is added first and is never dropped:
  the factual overview, scanner system/application/edge/connection/relation
  rows, a trimmed artifact index (identity fields only), the cross-project
  index, and the Phase 1/2 starter pack.
* Lower-signal bulk (endpoints, resources, supporting claims, phase2 claims,
  channels, lineage, components) is appended only while it fits under the byte
  cap. These files feed L3/L4 annotations and labels, not L1/L2 validity, so
  trimming them first never costs a passing topology.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)

# Identity-bearing keys kept in the trimmed artifact index. The full artifact
# rows embed component_ids/connections arrays that dominate the byte size but
# are not needed to resolve directional edges to artifacts; the identity fields
# (name/job_id/path) are what the validator's identity map keys on.
_ARTIFACT_MIN_KEYS = (
    "id",
    "type",
    "name",
    "job_id",
    "path",
    "source_root",
    "group",
    "subgroup",
    "artifact_kind",
    "artifact_role",
)

_TECHNICAL_FACT_KEYS: dict[str, tuple[str, ...]] = {
    "scanner_endpoint": (
        "id", "type", "artifact", "component_name", "direction",
        "endpoint_type", "endpoint_descriptor",
    ),
    "scanner_channel": (
        "id", "type", "artifact", "component_name", "direction",
        "channel_type", "channel", "channel_descriptor",
    ),
    "scanner_resource": (
        "id", "type", "artifact", "component_name", "direction",
        "resource_type", "resource", "resource_descriptor",
    ),
    "scanner_lineage": (
        "id", "type", "job_name", "resolved", "confidence",
        "source_system", "source_table", "source_column", "source_component",
        "target_table", "target_column", "target_component", "transformations",
    ),
}


def _write_trimmed_artifacts(context_dir: Path, artifacts_path: Path) -> Path | None:
    """Write an identity-only projection of artifacts.jsonl and return its path.

    Falls back to the original file when it cannot be read.
    """

    if not artifacts_path.is_file():
        return None
    try:
        lines = artifacts_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return artifacts_path
    rows: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        trimmed = {key: record[key] for key in _ARTIFACT_MIN_KEYS if key in record}
        rows.append(json.dumps(trimmed, sort_keys=True, ensure_ascii=False))
    context_dir.mkdir(parents=True, exist_ok=True)
    out = context_dir / "artifacts_min.jsonl"
    out.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return out


def _write_technical_facts(
    context_dir: Path,
    bundle: BusinessTopologyEvidenceBundle,
    *,
    max_lineage_per_artifact: int = 40,
    max_bytes: int = 100_000,
) -> Path:
    """Write a compact, generation-critical technical enrichment index.

    The full endpoint/channel/resource/lineage ledgers can be too large to push
    reliably. This projection retains the fields needed to structure protocols,
    ordered paths, queues/endpoints and mappings. Lineage is bounded per job so
    one mapping-heavy artifact cannot evict the rest of the architecture.
    """

    context_dir.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    total_bytes = 0
    lineage_counts: dict[str, int] = {}
    for evidence_id in sorted(bundle.evidence_ids):
        evidence_type = bundle.evidence_types.get(evidence_id, "")
        keys = _TECHNICAL_FACT_KEYS.get(evidence_type)
        if keys is None:
            continue
        record = bundle.evidence_records.get(evidence_id) or {}
        if evidence_type == "scanner_lineage":
            artifact = str(record.get("job_name") or "")
            count = lineage_counts.get(artifact, 0)
            if count >= max_lineage_per_artifact:
                continue
            lineage_counts[artifact] = count + 1
        trimmed = {key: record[key] for key in keys if key in record}
        if evidence_type == "scanner_lineage":
            trimmed["transformations"] = [
                {
                    "transformation_type": str(item.get("transformation_type") or ""),
                    "expression": str(item.get("expression") or "")[:500],
                }
                for item in record.get("transformations") or []
                if isinstance(item, dict)
            ]
        line = json.dumps(trimmed, sort_keys=True, ensure_ascii=False)
        line_bytes = len(line.encode("utf-8")) + 1
        if total_bytes + line_bytes > max_bytes:
            continue
        rows.append(line)
        total_bytes += line_bytes
    out = context_dir / "technical_facts_min.jsonl"
    out.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return out


def build_topology_context_files(
    bundle: BusinessTopologyEvidenceBundle,
    staging_dir: Path,
    *,
    byte_cap: int,
    include_generated_docs: bool = False,
    doc_filenames: tuple[str, ...] = (),
) -> list[Path]:
    """Return the ordered list of context files to PUSH to the topology model.

    ``byte_cap`` bounds the total pushed evidence. Critical files are always
    included even if the cap is exceeded (a passing topology is worth more than
    a byte budget); non-critical files are appended only while they fit.

    ``include_generated_docs`` adds this run's finished Phase 3 documents FIRST.
    Phase 3.7 runs after ``Phase3Docs`` with those documents on disk in
    ``docs_staging/``, and they are the deepest evidence the run produces -- the
    hand-curated werkorder topology was extracted from exactly these files while
    the old prompt forbade reading them. They lead the context because Stage A's
    job is to convert their flow narrative into data.
    """

    topo_root = bundle.root
    evidence_dir = topo_root / "evidence"
    context_dir = topo_root / "_context"

    trimmed_artifacts = _write_trimmed_artifacts(context_dir, evidence_dir / "artifacts.jsonl")
    technical_facts = _write_technical_facts(context_dir, bundle)

    # (path, critical). Order is priority order.
    candidates: list[tuple[Path, bool]] = []
    if include_generated_docs:
        candidates.extend((staging_dir / name, True) for name in doc_filenames)
    candidates.extend([
        (bundle.overview_path, True),                       # factual_evidence_index.json
        (evidence_dir / "systems.jsonl", True),
        (evidence_dir / "applications.jsonl", True),
        (evidence_dir / "edges.jsonl", True),
        (evidence_dir / "connections.jsonl", True),
        (evidence_dir / "relations.jsonl", True),
    ])
    if trimmed_artifacts is not None:
        candidates.append((trimmed_artifacts, True))
    candidates.append((technical_facts, True))
    cross_index = topo_root / "cross_project_index.json"
    candidates.append((cross_index, True))
    candidates.append((bundle.starter_pack_path, True))     # _doc_pack_business_topology.md
    # Non-critical, priority-ordered. Bulk / annotation-only evidence last.
    candidates.extend(
        (evidence_dir / name, False)
        for name in (
            "supporting_artifacts.jsonl",
            "supporting_claims.jsonl",
            "endpoints.jsonl",
            "resources.jsonl",
            "phase2_claims.jsonl",
            "channels.jsonl",
            "lineage.jsonl",
            "components.jsonl",
        )
    )

    selected: list[Path] = []
    seen: set[Path] = set()
    total = 0
    for path, critical in candidates:
        if path in seen or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if not critical and total + size > byte_cap:
            continue
        selected.append(path)
        seen.add(path)
        total += size
    return selected
