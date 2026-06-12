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

import hashlib
import json
import shutil
from collections.abc import Iterable
from pathlib import Path

RAG_SUPPORT_DIRNAME = "_rag_support"
PHASE2_ANALYSIS_SUBDIR = "phase2_analysis"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1


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
