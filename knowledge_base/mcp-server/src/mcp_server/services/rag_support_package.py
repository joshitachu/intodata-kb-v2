"""Hidden RAG support package for Phase 2 group analyses.

Phase 4 cleanup deletes `docs_staging/_analysis/` before the atomic swap, so
the published `docs/` tree would otherwise lose the group-level Phase 2
analyses that later RAG layers and incremental webhook regeneration need.

`build_rag_support_package()` copies an allowlist of `_analysis/{group}.md`
files (groups from the facts manifest only — never `_doc_pack_*`, `_bundle_*`,
reports, or other runtime artifacts) into `docs_staging/_rag_support/` with a
manifest. The atomic swap then carries `_rag_support/` into `docs/`.

`replace_analysis_cache_from_rag_support()` runs after the swap and rebuilds
the flat `output/{project_id}/analysis_cache/` that
`services/git_reingestion.py::setup_incremental_staging()` restores into
`docs_staging/_analysis/` on incremental runs.

`_rag_support/` is internal: `RagBridge` only pushes top-level `docs/*.md`,
and the docs routes never expose underscore-prefixed paths.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

RAG_SUPPORT_DIRNAME = "_rag_support"
PHASE2_ANALYSIS_SUBDIR = "phase2_analysis"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# RAG-side scoping prefix for internal support-layer docs. Must match
# INTERNAL_DOC_PREFIX in RAG_pipeline/backend/src/api/routes/ingest.py —
# the backend's internal-lifecycle delete matches on "{project_id}/_internal/".
INTERNAL_DOC_PREFIX = "_internal"

# (source_layer, artifact_type, docs-relative location) per internal layer.
LAYER_TECHNICAL_ANALYSIS = "technical_analysis"
LAYER_SUPPORT_MAPS = "support_maps"
LAYER_SCORECARD = "scorecard"
LAYER_EVIDENCE_SLICES = "evidence_slices"

REASON_MISSING_BASELINE = "missing_baseline_commit"
REASON_MISSING_DOCS = "missing_docs"
REASON_MISSING_RAG_SUPPORT = "missing_rag_support"
REASON_MISSING_ANALYSIS_CACHE = "missing_analysis_cache"
REASON_CACHE_SUPPORT_MISMATCH = "analysis_cache_rag_support_mismatch"
REASON_MISSING_CHANGED_GROUPS = "missing_changed_groups"

REBASELINE_REASON_CODES = {
    REASON_MISSING_BASELINE,
    REASON_MISSING_DOCS,
    REASON_MISSING_RAG_SUPPORT,
    REASON_MISSING_ANALYSIS_CACHE,
    REASON_CACHE_SUPPORT_MISMATCH,
    REASON_MISSING_CHANGED_GROUPS,
}


@dataclass(frozen=True)
class GenerationCacheReadiness:
    """Filesystem readiness for targeted regeneration."""

    has_docs: bool
    doc_count: int
    has_rag_support: bool
    rag_support_analysis_count: int
    has_analysis_cache: bool
    analysis_cache_count: int
    cache_support_match: bool
    targeted_regen_ready: bool
    requires_full_rebaseline: bool
    reason_codes: list[str] = field(default_factory=list)
    changed_group_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "has_docs": self.has_docs,
            "doc_count": self.doc_count,
            "has_rag_support": self.has_rag_support,
            "rag_support_analysis_count": self.rag_support_analysis_count,
            "has_analysis_cache": self.has_analysis_cache,
            "analysis_cache_count": self.analysis_cache_count,
            "cache_support_match": self.cache_support_match,
            "targeted_regen_ready": self.targeted_regen_ready,
            "requires_full_rebaseline": self.requires_full_rebaseline,
            "reason_codes": list(self.reason_codes),
            "changed_group_count": self.changed_group_count,
        }


def _nonempty_md_names(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    names: set[str] = set()
    for path in directory.glob("*.md"):
        try:
            if path.is_file() and path.stat().st_size > 0:
                names.add(path.name)
        except OSError:
            continue
    return names


def _nonempty_md_hashes(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    hashes: dict[str, str] = {}
    for path in directory.glob("*.md"):
        try:
            if path.is_file() and path.stat().st_size > 0:
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def inspect_generation_cache_readiness(
    output_dir: Path,
    *,
    has_baseline: bool = True,
    changed_groups: Iterable[str] | None = None,
) -> GenerationCacheReadiness:
    """Inspect docs, published support analyses, and restored analysis cache.

    ``changed_groups=None`` means a general project readiness report where no
    diff is in hand yet. Passing an iterable additionally gates targeted
    regeneration on at least one resolvable changed group.
    """
    docs_dir = output_dir / "docs"
    support_dir = docs_dir / RAG_SUPPORT_DIRNAME / PHASE2_ANALYSIS_SUBDIR
    cache_dir = output_dir / "analysis_cache"

    doc_names = _nonempty_md_names(docs_dir)
    support_hashes = _nonempty_md_hashes(support_dir)
    cache_hashes = _nonempty_md_hashes(cache_dir)
    support_names = set(support_hashes)
    cache_names = set(cache_hashes)
    changed_group_names = (
        {str(group).strip() for group in changed_groups if str(group).strip()}
        if changed_groups is not None
        else None
    )

    reason_codes: list[str] = []
    if not has_baseline:
        reason_codes.append(REASON_MISSING_BASELINE)
    if not doc_names:
        reason_codes.append(REASON_MISSING_DOCS)
    if not support_names:
        reason_codes.append(REASON_MISSING_RAG_SUPPORT)
    if not cache_names:
        reason_codes.append(REASON_MISSING_ANALYSIS_CACHE)
    cache_support_match = bool(
        support_hashes and cache_hashes and support_hashes == cache_hashes
    )
    if support_hashes and cache_hashes and not cache_support_match:
        reason_codes.append(REASON_CACHE_SUPPORT_MISMATCH)
    if changed_group_names is not None and not changed_group_names:
        reason_codes.append(REASON_MISSING_CHANGED_GROUPS)

    targeted_regen_ready = not reason_codes
    requires_full_rebaseline = any(code in REBASELINE_REASON_CODES for code in reason_codes)
    return GenerationCacheReadiness(
        has_docs=bool(doc_names),
        doc_count=len(doc_names),
        has_rag_support=bool(support_names),
        rag_support_analysis_count=len(support_names),
        has_analysis_cache=bool(cache_names),
        analysis_cache_count=len(cache_names),
        cache_support_match=cache_support_match,
        targeted_regen_ready=targeted_regen_ready,
        requires_full_rebaseline=requires_full_rebaseline,
        reason_codes=reason_codes,
        changed_group_count=len(changed_group_names) if changed_group_names is not None else None,
    )


def build_rag_support_package(
    staging_dir: Path,
    groups: Iterable[str],
    *,
    project_id: str,
    platform: str,
) -> dict:
    """Package Phase 2 group analyses into `staging_dir/_rag_support/`.

    Recreates `_rag_support/phase2_analysis/` from scratch (stale files from a
    resumed run cannot survive) and copies only `_analysis/{group}.md` for the
    given manifest groups. Groups without an analysis file are recorded in the
    manifest's `missing_groups`; no fallback content is synthesized.

    Returns the manifest dict that was written to `_rag_support/manifest.json`.
    """
    analysis_dir = staging_dir / "_analysis"
    support_dir = staging_dir / RAG_SUPPORT_DIRNAME
    package_dir = support_dir / PHASE2_ANALYSIS_SUBDIR

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict] = []
    missing_groups: list[str] = []
    for group in sorted(set(groups)):
        source = analysis_dir / f"{group}.md"
        if not source.is_file():
            missing_groups.append(group)
            continue
        target = package_dir / source.name
        shutil.copy2(source, target)
        data = target.read_bytes()
        artifacts.append(
            {
                "path": f"{PHASE2_ANALYSIS_SUBDIR}/{source.name}",
                "source_path": f"_analysis/{source.name}",
                "source_layer": "technical_analysis",
                "artifact_type": "phase2_group_analysis",
                "visibility": "internal",
                "group": group,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_id": project_id,
        "platform": platform,
        "artifacts": artifacts,
        "missing_groups": missing_groups,
    }
    (support_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # Carry the doc-pack provenance manifest into _rag_support/ so it survives
    # Phase 4 cleanup and the atomic swap into docs/. Absent on non-Phase-2.8
    # runs (e.g. resumed partial runs); skip silently in that case.
    doc_pack_manifest_src = analysis_dir / "_doc_pack_manifest.json"
    if doc_pack_manifest_src.is_file():
        shutil.copy2(doc_pack_manifest_src, support_dir / "_doc_pack_manifest.json")

    return manifest


def replace_analysis_cache_from_rag_support(output_dir: Path) -> bool:
    """Rebuild `output_dir/analysis_cache/` from the published support package.

    Reads `docs/_rag_support/phase2_analysis/*.md` and replaces the flat
    `analysis_cache/` used by incremental regeneration. When no support package
    exists or it contains no analyses (e.g. an analysis-skip run), any stale
    cache is removed and none is created, so incremental runs fall back to full
    regeneration instead of reusing outdated summaries.

    Returns True when the cache was repopulated, False otherwise.
    """
    cache_dir = output_dir / "analysis_cache"
    package_dir = (
        output_dir / "docs" / RAG_SUPPORT_DIRNAME / PHASE2_ANALYSIS_SUBDIR
    )

    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    analyses = sorted(package_dir.glob("*.md")) if package_dir.is_dir() else []
    if not analyses:
        return False

    cache_dir.mkdir(parents=True)
    for md_file in analyses:
        shutil.copy2(md_file, cache_dir / md_file.name)
    return True


def internal_doc_filename(source_layer: str, name: str) -> str:
    """Deterministic RAG filename for an internal artifact.

    Percent-encodes the artifact name so group names like ``Training|Jasmijn``
    cannot leak raw separators into the RAG path or collide with canonical
    docs. The result is always ``_internal/{layer}/{encoded-name}.md``.
    """
    stem = name[:-3] if name.endswith(".md") else name
    return f"{INTERNAL_DOC_PREFIX}/{source_layer}/{quote(stem, safe='')}.md"


def collect_internal_artifacts(
    docs_dir: Path,
    *,
    project_id: str,
    run_id: int | None = None,
) -> list[dict]:
    """Discover internal support-layer markdown under a published docs/ tree.

    Returns one entry per artifact: ``{"filename", "content", "metadata"}``,
    ready for ``RagBridge.push_markdown``. Only markdown is collected —
    ``_facts/*.csv`` and ``*.json`` feed the entity alias table, never the
    vector corpus. Missing layers are skipped; empty files are dropped.
    """
    group_by_name: dict[str, str] = {}
    manifest_path = docs_dir / RAG_SUPPORT_DIRNAME / MANIFEST_FILENAME
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            group_by_name = {
                Path(a["path"]).name: a["group"]
                for a in manifest.get("artifacts", [])
                if a.get("group")
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            group_by_name = {}

    sources: list[tuple[str, str, list[Path], str | None]] = [
        (
            LAYER_TECHNICAL_ANALYSIS,
            "phase2_group_analysis",
            sorted((docs_dir / RAG_SUPPORT_DIRNAME / PHASE2_ANALYSIS_SUBDIR).glob("*.md")),
            "group",
        ),
        (
            LAYER_SUPPORT_MAPS,
            "support_map",
            sorted((docs_dir / "_facts").glob("*.md")),
            None,
        ),
        (
            LAYER_SCORECARD,
            "scorecard",
            [docs_dir / "_assessment" / "scorecard.md"],
            None,
        ),
        (
            LAYER_EVIDENCE_SLICES,
            "evidence_slice",
            sorted((docs_dir / "_evidence_registry" / "doc_slices").glob("*.md")),
            None,
        ),
    ]

    artifacts: list[dict] = []
    for source_layer, artifact_type, paths, group_key in sources:
        for path in paths:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue
            metadata: dict = {
                "source_layer": source_layer,
                "artifact_type": artifact_type,
                "visibility": "internal",
                "artifact_path": path.relative_to(docs_dir).as_posix(),
                "doc_name": path.stem,
                "project_id": project_id,
            }
            if run_id is not None:
                metadata["run_id"] = run_id
            if group_key == "group":
                metadata["group"] = group_by_name.get(path.name, path.stem)
            artifacts.append(
                {
                    "filename": internal_doc_filename(source_layer, path.name),
                    "content": content,
                    "metadata": metadata,
                }
            )
    return artifacts


def _normalize_alias(alias: str) -> str:
    return " ".join(alias.split()).lower()


def _clamped_confidence(raw: str | None) -> float:
    try:
        value = float(raw) if raw not in (None, "") else 1.0
    except ValueError:
        value = 1.0
    return min(1.0, max(0.0, value))


def build_entity_aliases(docs_dir: Path) -> list[dict]:
    """Build per-project alias records from `_facts/` entity and usage CSVs.

    Deterministic: rows are deduped on (alias_normalized, entity_type,
    unit_name, source_artifact) keeping the highest confidence, then sorted.
    Missing CSV files are a successful no-op.
    """
    facts_dir = docs_dir / "_facts"
    deduped: dict[tuple, dict] = {}

    def _add(record: dict) -> None:
        key = (
            record["alias_normalized"],
            record["entity_type"],
            record["unit_name"] or "",
            record["source_artifact"],
        )
        existing = deduped.get(key)
        if existing is None or record["confidence"] > existing["confidence"]:
            deduped[key] = record

    entity_csv = facts_dir / "entity_index.csv"
    if entity_csv.is_file():
        with entity_csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                _add(
                    {
                        "alias": name,
                        "alias_normalized": _normalize_alias(name),
                        "canonical_name": name,
                        "entity_type": (row.get("entity_type") or "entity").strip(),
                        "unit_name": (row.get("unit_name") or "").strip() or None,
                        "source_path": (row.get("path") or "").strip() or None,
                        "source_artifact": "entity_index.csv",
                        "confidence": _clamped_confidence(row.get("confidence")),
                    }
                )

    usage_csv = facts_dir / "usage_index.csv"
    if usage_csv.is_file():
        with usage_csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                _add(
                    {
                        "alias": name,
                        "alias_normalized": _normalize_alias(name),
                        "canonical_name": name,
                        "entity_type": (row.get("usage_type") or "usage").strip(),
                        "unit_name": None,
                        "source_path": (row.get("location") or "").strip() or None,
                        "source_artifact": "usage_index.csv",
                        "confidence": _clamped_confidence(row.get("confidence")),
                    }
                )

    return sorted(
        deduped.values(),
        key=lambda r: (r["alias_normalized"], r["entity_type"], r["unit_name"] or "", r["source_artifact"]),
    )
