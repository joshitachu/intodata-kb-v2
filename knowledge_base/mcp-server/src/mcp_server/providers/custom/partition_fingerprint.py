"""Per-sub-project scan fingerprints, so one changed repo regenerates alone.

``partition_orchestrator`` skips a sub-project whose recorded fingerprint still
matches the current scan. Until now that fingerprint was
``compute_scan_fingerprint(scan_output_dir)`` -- ONE digest over the whole scan,
shared by every partition. Any change anywhere therefore mismatched every
partition and the whole project regenerated. For a 10-repo project that is the
difference between paying for one repo's documentation and paying for ten.

This module computes one fingerprint per partition, over only the scan artifacts
that partition's units actually own:

* **membership** -- the partition name and its sorted unit ids, so a unit moving
  between repos invalidates both sides.
* **per-unit job artifacts** -- each unit's segmented job JSON, located through
  the scanner's authoritative ``manifest.job_files`` index (job_key ->
  ``jobs/<safe_group>/<file>.json``) rather than by guessing the escaped
  directory name. Hashing the file BYTES is what catches an in-file edit: a
  changed tMap expression alters no ``UnitFact`` field, so a fingerprint built
  from the facts manifest alone would silently miss it and publish stale docs.
* **scoped contexts** -- ``scan/contexts.json`` entries whose ``source_root``
  belongs to this partition. ``ContextGroup`` carries ``source_root``, so
  context changes attribute to one sub-project.
* **the unattributable digest** -- see below.

Deliberately EXCLUDED: ``scan/manifest.json`` as a whole. It holds the
project-wide call graph, so any repo's change rewrites it; including it would
re-couple every partition and undo the entire point. Cross-partition edges feed
the upload-level cross-project index instead, which is recomputed on every run
at zero model cost.

Also excluded: ``scan/landscape.json``. It is *derived* by the scanner from the
job artifacts and the manifest, so a landscape change is always accompanied by a
job-artifact change that the per-unit hashes already catch.

The unattributable digest -- an honest limitation
-------------------------------------------------
Current scans qualify routines by ``source_root`` and hash them only into their
own partition. Historical routines without that field remain in the shared
digest and invalidate every partition. That over-invalidation is safe; silently
reusing a document after an unowned routine changed is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.models import FactsManifest
from mcp_server.providers.custom.partition_planner import (
    PartitionPlan,
    WHOLE_PROJECT_PARTITION,
    subproject_key,
)

logger = logging.getLogger(__name__)

# Bump when the fingerprint INPUTS or layout change, so every partition is
# treated as dirty once after a deploy rather than skipped on a stale digest.
FINGERPRINT_VERSION = "2"

# Scan artifacts that carry no source_root and so cannot be attributed to one
# sub-project. Their digest enters every partition's fingerprint. Shrink this
# list only by adding provenance in the scanner.
UNATTRIBUTABLE_ARTIFACTS: tuple[str, ...] = ("routines.json",)


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _unattributable_digest(scan_dir: Path) -> str:
    """Digest of the scan artifacts that carry no sub-project provenance."""
    digest = hashlib.sha256()
    for relative in UNATTRIBUTABLE_ARTIFACTS:
        path = scan_dir / relative
        payload = _load_json(path)
        if relative == "routines.json" and isinstance(payload, list):
            payload = [entry for entry in payload if not isinstance(entry, dict) or not entry.get("source_root")]
        digest.update(relative.encode())
        digest.update(b"\0")
        # A missing artifact hashes distinctly from an empty one, so deleting
        # routines.json is not mistaken for "no routines".
        digest.update(b"\x01" if payload is None else _canonical(payload))
        digest.update(b"\0")
    return digest.hexdigest()


def _routines_by_partition(scan_dir: Path) -> dict[str, list[Any]]:
    payload = _load_json(scan_dir / "routines.json")
    grouped: dict[str, list[Any]] = {}
    if not isinstance(payload, list):
        return grouped
    for entry in payload:
        if not isinstance(entry, dict) or not entry.get("source_root"):
            continue
        key = subproject_key(str(entry["source_root"])) or WHOLE_PROJECT_PARTITION
        grouped.setdefault(key, []).append(entry)
    for entries in grouped.values():
        entries.sort(key=_canonical)
    return grouped


def unattributable_digest(scan_output_dir: Path) -> str:
    """Public digest of the scan's provenance-free artifacts (shared routines).

    The same value :func:`compute_partition_fingerprints` mixes into EVERY
    partition, exposed on its own because a caller that reports on the plan has
    to explain *why* every sub-project is dirty. Without this, a shared-routine
    edit is indistinguishable from "all six repos changed at once": the
    per-partition digests all moved and nothing says they moved for one reason.

    Takes the scan OUTPUT dir (``intodoc-output/{project_id}``), matching
    :func:`compute_partition_fingerprints`, rather than the ``scan/`` subdir the
    private helper works on -- callers should not have to know that layout.
    """
    return _unattributable_digest(Path(scan_output_dir) / "scan")


def _contexts_by_partition(scan_dir: Path) -> dict[str, list[Any]]:
    """Group ``scan/contexts.json`` entries by the partition they belong to.

    Entries whose ``source_root`` carries no origin marker land under
    :data:`WHOLE_PROJECT_PARTITION`, matching how the planner treats an
    unmarked scan.
    """
    payload = _load_json(scan_dir / "contexts.json")
    grouped: dict[str, list[Any]] = {}
    if not isinstance(payload, list):
        return grouped
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        key = subproject_key(str(entry.get("source_root") or "")) or WHOLE_PROJECT_PARTITION
        grouped.setdefault(key, []).append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda item: _canonical(item))
    return grouped


def compute_partition_fingerprints(
    scan_output_dir: Path,
    plan: PartitionPlan,
    facts: FactsManifest,
) -> dict[str, str]:
    """Return ``{partition_name: fingerprint}`` for every partition in *plan*.

    A partition maps to the empty string when its fingerprint cannot be
    computed with confidence -- a unit missing from ``manifest.job_files``, or an
    unreadable job artifact. The orchestrator treats an empty fingerprint as
    "never skip", so an indeterminate input costs a regeneration instead of
    risking a stale publish.
    """
    scan_dir = Path(scan_output_dir) / "scan"
    manifest = _load_json(scan_dir / "manifest.json")
    job_files: dict[str, str] = {}
    if isinstance(manifest, dict) and isinstance(manifest.get("job_files"), dict):
        job_files = {str(k): str(v) for k, v in manifest["job_files"].items()}

    unattributable = _unattributable_digest(scan_dir)
    contexts = _contexts_by_partition(scan_dir)
    routines = _routines_by_partition(scan_dir)
    # Units are looked up by canonical id; display name is the scanner's own
    # fallback when job_id is empty (validators sets UnitFact.name the same way).
    display_by_name = {u.name: (u.display_name or u.name) for u in facts.units}

    fingerprints: dict[str, str] = {}
    for part in plan.partitions:
        digest = hashlib.sha256()
        digest.update(FINGERPRINT_VERSION.encode())
        digest.update(b"\0")
        digest.update(part.name.encode())
        digest.update(b"\0")
        digest.update(unattributable.encode())
        digest.update(b"\0")
        digest.update(_canonical(contexts.get(part.name, [])))
        digest.update(b"\0")
        digest.update(_canonical(routines.get(part.name, [])))
        digest.update(b"\0")

        indeterminate = False
        for unit_name in sorted(part.unit_names):
            relative = job_files.get(unit_name) or job_files.get(
                display_by_name.get(unit_name, "")
            )
            if not relative:
                logger.warning(
                    "partition fingerprint: unit %r of %r absent from manifest.job_files "
                    "-- forcing regeneration",
                    unit_name,
                    part.name,
                )
                indeterminate = True
                break
            path = scan_dir / relative
            try:
                content = path.read_bytes()
            except OSError:
                logger.warning(
                    "partition fingerprint: unreadable job artifact %s for %r "
                    "-- forcing regeneration",
                    path,
                    part.name,
                )
                indeterminate = True
                break
            # Membership AND content: the unit id and its relative location are
            # hashed alongside the bytes, so a rename with identical content
            # still moves the fingerprint.
            digest.update(unit_name.encode())
            digest.update(b"\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(content).digest())
            digest.update(b"\0")

        fingerprints[part.name] = "" if indeterminate else digest.hexdigest()

    return fingerprints
