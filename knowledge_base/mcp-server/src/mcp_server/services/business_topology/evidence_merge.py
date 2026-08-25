"""Deterministic org-level evidence catalog merge for composite uploads.

A composite upload (one ZIP per sub-project) generates documentation one
partition at a time. Each partition publishes its own topology evidence
catalog under ``output/{pid}/{subproject}/docs/_topology/evidence/*.jsonl``
(``artifacts.jsonl``, ``lineage.jsonl``, ``components.jsonl``, ...). There is
no ``output/{pid}/topology/evidence/`` tree, so
``projects.py::_load_topology_evidence_catalog`` -- which reads evidence rows
from ``topology_dir / "evidence"`` for the org-level landscape endpoint -- has
nothing to resolve L1/L2 identities down to L3/L4 artifacts and components.

This module builds that missing org-level catalog by concatenating the
per-partition ``*.jsonl`` files, deduplicating rows by their ``id`` field
(first occurrence wins, in sorted-partition order) so a design shared across
sub-projects only appears once. The result is a pure function of the
partition files on disk plus a scan fingerprint: unchanged inputs and an
unchanged fingerprint always reproduce the same output, and rebuilding is
skipped entirely when the fingerprint marker already matches.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FINGERPRINT_MARKER = ".fingerprint"
_CATALOG_VERSION = "2"


def marker_value(scan_fingerprint: str) -> str:
    """Version the derived catalogue independently of the source scan."""

    return f"{scan_fingerprint}:{_CATALOG_VERSION}"


def _derived_trigger_relations(artifacts: list[dict], relations: list[dict]) -> list[dict]:
    """Connect file-trigger routes to one uniquely named processing job.

    Talend file triggers are generated as ``trigger_OnFile_<job>``. Some
    composite exports keep the trigger and processing job in different ZIPs,
    so the scanner cannot emit a same-root ``route_invokes_job`` relation.
    The exact suffix is still useful evidence, but only when it resolves to
    exactly one production job across the entire composite catalogue.
    """

    jobs_by_name: dict[str, list[dict]] = {}
    for artifact in artifacts:
        if str(artifact.get("artifact_kind") or "") != "job":
            continue
        for value in {artifact.get("name"), artifact.get("job_id")}:
            if value:
                jobs_by_name.setdefault(str(value), []).append(artifact)

    existing = {
        (str(row.get("source") or ""), str(row.get("target") or ""))
        for row in relations
    }
    derived: list[dict] = []
    prefix = "trigger_OnFile_"
    for artifact in artifacts:
        source = str(artifact.get("name") or artifact.get("job_id") or "")
        if str(artifact.get("artifact_kind") or "") != "route" or not source.startswith(prefix):
            continue
        target_name = source[len(prefix):]
        candidates = {
            str(candidate.get("id")): candidate
            for candidate in jobs_by_name.get(target_name, [])
            if candidate.get("id")
        }
        if len(candidates) != 1 or (source, target_name) in existing:
            continue
        relation_key = f"{source}\0{target_name}\0unique_exact_name"
        derived.append({
            "id": f"relation:{hashlib.sha256(relation_key.encode('utf-8')).hexdigest()[:20]}",
            "type": "derived_relation",
            "source": source,
            "target": target_name,
            "relation_type": "file_trigger_invokes_job",
            "resolution": "unique_exact_name",
            "provenance": "trigger_OnFile exact suffix",
        })
    return derived


def _partition_evidence_dirs(project_root: Path, project_id: str) -> list[Path]:
    """Discover per-partition evidence dirs, sorted by path for determinism.

    The glob pattern ``output/{project_id}/*/docs/_topology/evidence`` only
    matches sub-project directories: the org-level directory is
    ``output/{project_id}/topology`` (no ``docs/_topology`` beneath it), so it
    is naturally excluded without any name-based filtering.
    """

    base = project_root / "output" / project_id
    matches = sorted(
        (p for p in base.glob("*/docs/_topology/evidence") if p.is_dir()),
        key=lambda p: str(p),
    )
    return matches


def _read_jsonl_rows(path: Path) -> list[tuple[str | None, dict]]:
    """Read a ``.jsonl`` file, returning ``(id_or_none, row)`` pairs.

    Lines that fail to parse as JSON are skipped (and logged). Rows that
    parse but are not JSON objects are skipped as well -- the evidence
    catalog format is line-delimited objects only.
    """

    rows: list[tuple[str | None, dict]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read partition evidence file %s: %s", path, exc)
        return rows

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping unparseable evidence row in %s", path)
            continue
        if not isinstance(value, dict):
            continue
        row_id = value.get("id")
        rows.append((str(row_id) if row_id is not None else None, value))
    return rows


def build_composite_evidence_catalog(
    project_root: Path, project_id: str, scan_fingerprint: str
) -> bool:
    """Merge per-partition topology evidence catalogs into one org-level catalog.

    Discovers every ``output/{project_id}/*/docs/_topology/evidence`` directory
    under ``project_root``, unions their ``*.jsonl`` filenames, and writes each
    merged file to ``output/{project_id}/topology/evidence/{name}.jsonl`` --
    concatenating rows in sorted-partition-dir order and deduplicating by the
    row's ``id`` field (first occurrence wins; rows without an ``id`` are kept
    as-is and never deduplicated). This feeds
    ``projects.py::_load_topology_evidence_catalog``, which otherwise has no
    org-level evidence to resolve.

    Returns ``False`` (and creates nothing) when no partition evidence
    directories exist. Returns ``True`` immediately without rebuilding when
    the org catalog already exists and its ``.fingerprint`` marker matches
    ``scan_fingerprint``. Otherwise rebuilds deterministically and returns
    ``True``.
    """

    org_evidence = project_root / "output" / project_id / "topology" / "evidence"
    partition_dirs = _partition_evidence_dirs(project_root, project_id)

    if not partition_dirs:
        return False

    marker_path = org_evidence / FINGERPRINT_MARKER
    if org_evidence.is_dir() and marker_path.is_file():
        try:
            current = marker_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read evidence fingerprint marker: %s", exc)
            current = None
        if current == marker_value(scan_fingerprint):
            return True

    filenames: set[str] = set()
    for partition_dir in partition_dirs:
        try:
            entries = list(partition_dir.glob("*.jsonl"))
        except OSError as exc:
            logger.warning(
                "Could not list partition evidence dir %s: %s", partition_dir, exc
            )
            continue
        filenames.update(entry.name for entry in entries)

    try:
        org_evidence.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create org evidence dir %s: %s", org_evidence, exc)
        return False

    merged_by_filename: dict[str, list[dict]] = {}
    for filename in sorted(filenames):
        seen_ids: set[str] = set()
        merged_rows: list[dict] = []
        for partition_dir in partition_dirs:
            path = partition_dir / filename
            if not path.is_file():
                continue
            for row_id, row in _read_jsonl_rows(path):
                if row_id is not None:
                    if row_id in seen_ids:
                        continue
                    seen_ids.add(row_id)
                merged_rows.append(row)

        merged_by_filename[filename] = merged_rows

    artifacts = merged_by_filename.get("artifacts.jsonl", [])
    relations = merged_by_filename.setdefault("relations.jsonl", [])
    relations.extend(_derived_trigger_relations(artifacts, relations))

    for filename, merged_rows in sorted(merged_by_filename.items()):
        out_path = org_evidence / filename
        try:
            out_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in merged_rows)
                + ("\n" if merged_rows else ""),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not write merged evidence file %s: %s", out_path, exc)
            continue

    try:
        marker_path.write_text(marker_value(scan_fingerprint), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write evidence fingerprint marker: %s", exc)
        return False

    return True
