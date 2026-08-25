"""Universal, pack-INDEPENDENT extraction cache for doc generation.

Why this exists
---------------
``docs_staging/_preprocess/`` is the deterministic extraction layer: group
source bundles plus the platform analysis guide, the Phase 3 guide, the
reference/scoring bundles and the doc templates. Every one of those files is a
pure function of

* the intodoc scan artifacts,
* the preprocess / redaction / bundling code and the on-disk templates it
  reads (covered by :data:`EXTRACTION_CODE_VERSION`),
* the platform,
* the partition unit allowlist (composite sub-project scoping).

It is NOT a function of the pack: ``_preprocess_scan_data`` never sees a pack
id, a pack contract or a doc catalog. Phase 4 nevertheless deletes the whole
directory, so running a second pack over the same project re-runs the entire
deterministic extraction from the scan cache. This module persists that layer
outside ``docs_staging/`` so swapping the pack (the "lens") reuses the
evidence.

Deliberately NOT cached here (recon section 4.6):

* ``_analysis/_context_packets/`` -- deterministic from the Phase 2 task
  evidence plus per-run byte caps, 6.4M measured on one project. Caching it
  buys disk, not model calls.
* ``_analysis/_claims/``, ``_analysis/_doc_pack_*``,
  ``_analysis/_evidence_{doc}.md`` -- pack-scoped *outputs*, not extraction.
  Serving them to a new pack would replay the OLD pack's interpretation, which
  is exactly the defect this cache exists to avoid.
* the engineering-fact artifacts -- they already survive Phase 4 and the atomic
  swap under ``docs/_facts/``.

Invariants
----------
* **An empty key means DO NOT CACHE.** It never means "cache under the empty
  key". :func:`compute_extraction_key` returns ``""`` when the scan fingerprint
  cannot be computed, and both :func:`store_extraction` and
  :func:`restore_extraction` no-op on an empty key.
* The cache root lives OUTSIDE ``docs_staging/`` (see
  :func:`extraction_cache_dir`) so it survives Phase 4's rmtree and the atomic
  swap, and it is rooted at the *partition-scoped* output dir so partitions of
  a composite upload get independent entries.
* A restore either populates ``staging_dir/_preprocess/`` completely or not at
  all. Any manifest/size/digest mismatch invalidates the whole entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from mcp_server.services.business_topology.evidence import compute_scan_fingerprint

logger = logging.getLogger(__name__)

# Bump MANUALLY whenever the extraction layer's *content* can change without
# the scan changing: ClaudeService._preprocess_scan_data (chunk formatting,
# group slicing, case-collision naming), the credential redaction filter
# (providers/custom/source_context.py), the bundled analysis / Phase 3 guides,
# the reference-doc and scoring-template bundles, or _doc_templates.md. A stale
# constant serves an outdated bundle to a new run; there is no cheap automatic
# detection because the inputs include the whole source tree.
EXTRACTION_CODE_VERSION = "1"

CACHE_DIRNAME = "extraction_cache"
PREPROCESS_DIRNAME = "_preprocess"
MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

# Literal digest stand-in for "no partition scoping" (whole-project run).
ALL_UNITS_SENTINEL = "__all__"

_TMP_PREFIX = ".tmp-"
_STALE_PREFIX = ".stale-"
_RESTORE_TMP_PREFIX = "._preprocess_restore-"


# --------------------------------------------------------------------------- #
# Key composition
# --------------------------------------------------------------------------- #


def extraction_cache_dir(output_dir: Path) -> Path:
    """Cache root for one project (or one composite partition).

    ``output_dir`` must be the *partition-scoped* output directory --
    ``output/{project_id}`` for a whole-project run, or
    ``output/{project_id}/{subproject}`` for a partition. That is
    ``ctx.staging_dir.parent`` inside the pipeline. The root is a sibling of
    ``docs_staging/``, never a child, so Phase 4 cleanup and the atomic swap
    leave it alone.
    """

    return Path(output_dir) / CACHE_DIRNAME


def unit_allowlist_digest(unit_allowlist: set[str] | None) -> str:
    """Stable digest of a partition's canonical unit allowlist.

    ``None`` (whole-project run) maps to the literal ``"__all__"`` so it can
    never collide with a real -- even empty -- allowlist.
    """

    if unit_allowlist is None:
        return ALL_UNITS_SENTINEL
    digest = hashlib.sha256()
    for name in sorted(unit_allowlist):
        digest.update(str(name).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def unit_allowlist_for_partition(partition: Any) -> set[str] | None:
    """Recover the unit allowlist that scoped a run, from its partition.

    Mirrors ``ClaudeService.generate_docs`` exactly so the store side (Phase 4,
    which only has ``ctx``) and the restore side (which has the local
    ``unit_allowlist``) always derive the same value.
    """

    from mcp_server.providers.custom.partition_planner import WHOLE_PROJECT_PARTITION

    if partition is None:
        return None
    if getattr(partition, "name", None) == WHOLE_PROJECT_PARTITION:
        return None
    return set(partition.unit_names)


def compute_extraction_key(
    scan_output_dir: Path,
    *,
    platform: str,
    unit_allowlist: set[str] | None,
) -> str:
    """Cache key for the extraction layer, or ``""`` when it cannot be computed.

    Composed as ``sha256`` over, NUL-separated:

    1. ``compute_scan_fingerprint(scan_output_dir)`` -- the one existing
       fingerprint notion in this codebase (``services/business_topology/
       evidence.py``). It hashes ``scan_result.json``, ``scan/manifest.json``,
       ``scan/lineage.json`` and ``scan/landscape.json`` (minus semantic
       overlays), so ANY change to the scan yields a different key and
       therefore a cache miss. No second fingerprint is invented here.
    2. :data:`EXTRACTION_CODE_VERSION` -- see that constant's comment; bump it
       by hand when the preprocess/redaction/bundling code changes. This
       follows the ``evidence_merge.marker_value`` precedent of composing the
       scan fingerprint with a code-version input.
    3. the normalised platform.
    4. :func:`unit_allowlist_digest` of the partition unit allowlist.

    Deliberately NOT part of the key: pack id, pack contract, doc catalog,
    prompt templates, model. Those select the *interpretation*; this layer is
    the *evidence*, and keeping it pack-independent is the entire point.

    Returns ``""`` when no scan artifact exists or the scan cannot be read.
    ``""`` means DO NOT CACHE -- never "cache under the empty key".
    """

    try:
        fingerprint = compute_scan_fingerprint(Path(scan_output_dir))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug(
            "Extraction cache: no key for %s (%s)", scan_output_dir, exc,
        )
        return ""
    if not fingerprint:
        return ""

    digest = hashlib.sha256()
    for part in (
        fingerprint,
        EXTRACTION_CODE_VERSION,
        (platform or "").strip().lower(),
        unit_allowlist_digest(unit_allowlist),
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_files(root: Path) -> Iterable[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _group_names(preprocess_dir: Path) -> list[str]:
    """Group names as ``_preprocess_scan_data`` reports them.

    Stems of the top-level, non ``_``-prefixed ``*.md`` files.
    """

    return sorted(
        path.stem
        for path in preprocess_dir.glob("*.md")
        if not path.name.startswith("_")
    )


def _read_manifest(entry_dir: Path) -> dict[str, Any] | None:
    manifest_path = entry_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _remove_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Store / restore
# --------------------------------------------------------------------------- #


def store_extraction(
    cache_root: Path, key: str, staging_dir: Path
) -> dict[str, Any] | None:
    """Copy ``staging_dir/_preprocess/`` into ``cache_root/{key}/``.

    Returns the written manifest, or ``None`` when nothing was stored (empty
    key, or an absent/empty ``_preprocess/``).

    The entry is built in a temporary sibling directory and moved into place
    with :func:`os.replace`, so a crash can never leave a half-populated entry
    that a later run would trust.
    """

    if not key:
        return None
    cache_root = Path(cache_root)
    source = Path(staging_dir) / PREPROCESS_DIRNAME
    if not source.is_dir():
        return None
    files = list(_iter_files(source))
    if not files:
        return None

    cache_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"{_TMP_PREFIX}{key[:12]}-", dir=cache_root))
    stale_dir: Path | None = None
    try:
        shutil.copytree(source, tmp_dir / PREPROCESS_DIRNAME)
        records: list[dict[str, Any]] = []
        total_bytes = 0
        for path in _iter_files(tmp_dir / PREPROCESS_DIRNAME):
            name = path.relative_to(tmp_dir / PREPROCESS_DIRNAME).as_posix()
            size = path.stat().st_size
            total_bytes += size
            records.append(
                {"name": name, "bytes": size, "sha256": _sha256_file(path)}
            )
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "key": key,
            "code_version": EXTRACTION_CODE_VERSION,
            "groups": _group_names(tmp_dir / PREPROCESS_DIRNAME),
            "files": records,
            "total_bytes": total_bytes,
            "created_at": time.time(),
        }
        (tmp_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        entry_dir = cache_root / key
        if entry_dir.exists():
            stale_dir = cache_root / f"{_STALE_PREFIX}{key[:12]}-{os.getpid()}"
            _remove_dir(stale_dir)
            os.replace(entry_dir, stale_dir)
        os.replace(tmp_dir, entry_dir)
    except (OSError, ValueError):
        _remove_dir(tmp_dir)
        raise
    finally:
        if stale_dir is not None:
            _remove_dir(stale_dir)
    logger.info(
        "Extraction cache: stored %d file(s), %d bytes under %s",
        len(records), total_bytes, key[:12],
    )
    return manifest


def restore_extraction(
    cache_root: Path, key: str, staging_dir: Path
) -> list[str] | None:
    """Populate ``staging_dir/_preprocess/`` from ``cache_root/{key}/``.

    Returns the sorted group names (the same value
    ``ClaudeService._preprocess_scan_data`` returns) on a verified hit, else
    ``None``.

    Every file listed in the entry manifest must exist with the recorded size
    AND sha256. Any mismatch, a missing/unparseable manifest, a schema or
    code-version mismatch, or a key mismatch invalidates the whole entry: the
    function returns ``None`` and ``staging_dir`` is left untouched. Verification
    completes before a single byte is copied, and the copy lands via an atomic
    rename, so a partial population is not reachable.
    """

    if not key:
        return None
    entry_dir = Path(cache_root) / key
    if not entry_dir.is_dir():
        return None
    manifest = _read_manifest(entry_dir)
    if manifest is None:
        return None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        logger.warning(
            "Extraction cache: entry %s has schema_version %r, ignoring",
            key[:12], manifest.get("schema_version"),
        )
        return None
    if manifest.get("code_version") != EXTRACTION_CODE_VERSION:
        return None
    if manifest.get("key") != key:
        logger.warning("Extraction cache: entry %s carries a foreign key", key[:12])
        return None

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        return None
    cached_preprocess = entry_dir / PREPROCESS_DIRNAME
    if not cached_preprocess.is_dir():
        return None

    for record in records:
        if not isinstance(record, dict):
            return None
        name = record.get("name")
        if not isinstance(name, str) or not name:
            return None
        path = cached_preprocess / name
        try:
            resolved = path.resolve()
            root = cached_preprocess.resolve()
        except OSError:
            return None
        if root != resolved and root not in resolved.parents:
            logger.warning(
                "Extraction cache: entry %s lists an out-of-tree file %r",
                key[:12], name,
            )
            return None
        if not path.is_file():
            logger.warning(
                "Extraction cache: entry %s is missing %s -- treating as invalid",
                key[:12], name,
            )
            return None
        try:
            if path.stat().st_size != record.get("bytes"):
                logger.warning(
                    "Extraction cache: entry %s has a size mismatch on %s",
                    key[:12], name,
                )
                return None
            if _sha256_file(path) != record.get("sha256"):
                logger.warning(
                    "Extraction cache: entry %s has a digest mismatch on %s",
                    key[:12], name,
                )
                return None
        except OSError as exc:
            logger.warning(
                "Extraction cache: cannot verify %s in entry %s (%s)",
                name, key[:12], exc,
            )
            return None

    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / PREPROCESS_DIRNAME
    tmp_target = Path(
        tempfile.mkdtemp(prefix=f"{_RESTORE_TMP_PREFIX}{key[:12]}-", dir=staging_dir)
    )
    stale_target: Path | None = None
    try:
        _remove_dir(tmp_target)
        shutil.copytree(cached_preprocess, tmp_target)
        if target.exists():
            stale_target = staging_dir / f"{_STALE_PREFIX}preprocess-{os.getpid()}"
            _remove_dir(stale_target)
            os.replace(target, stale_target)
        os.replace(tmp_target, target)
    except (OSError, ValueError):
        _remove_dir(tmp_target)
        raise
    finally:
        if stale_target is not None:
            _remove_dir(stale_target)

    groups = _group_names(target)
    logger.info(
        "Extraction cache: restored %d file(s) for %d group(s) from %s",
        len(records), len(groups), key[:12],
    )
    return groups


def prune_extraction_cache(cache_root: Path, *, keep: int) -> list[str]:
    """Bounded retention: keep the ``keep`` newest entries, drop the rest.

    Ordering is newest-first by the entry manifest's ``created_at``. Entries
    without a readable manifest sort last (they are unusable anyway) and are
    the first to go. Leftover temp/stale scratch directories are always
    removed. Returns the keys that were dropped.
    """

    cache_root = Path(cache_root)
    if not cache_root.is_dir():
        return []
    entries: list[tuple[float, str]] = []
    for child in cache_root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith((_TMP_PREFIX, _STALE_PREFIX)):
            _remove_dir(child)
            continue
        manifest = _read_manifest(child)
        created_at = 0.0
        if isinstance(manifest, dict):
            raw = manifest.get("created_at")
            if isinstance(raw, (int, float)):
                created_at = float(raw)
        entries.append((created_at, child.name))

    if keep < 0:
        keep = 0
    entries.sort(key=lambda item: (-item[0], item[1]))
    dropped = [name for _, name in entries[keep:]]
    for name in dropped:
        _remove_dir(cache_root / name)
    if dropped:
        logger.info(
            "Extraction cache: pruned %d entry(ies), keeping %d", len(dropped), keep,
        )
    return dropped
