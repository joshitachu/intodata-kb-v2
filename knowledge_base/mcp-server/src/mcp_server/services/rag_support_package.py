"""Hidden RAG support package for Phase 2 group analyses.

Cleanup deletes `docs_staging/_analysis/` before the atomic swap -- the custom
lane in its own Phase 4, the claude-cli / opencode lane in
`claude_service._finalize_memory_and_language` -- so the published `docs/` tree
would otherwise lose the group-level Phase 2 analyses that later RAG layers and
incremental webhook regeneration need.

`build_rag_support_package()` packages an allowlist of `_analysis/` group
analyses — including `{group}_part*` slices (large-group splits and
case-collision `_part_ci{k}` slices) resolved back to their facts-manifest
group, never `_doc_pack_*`, `_bundle_*`, reports, or other runtime
artifacts — into `docs_staging/_rag_support/` with a manifest. The atomic swap then carries `_rag_support/` into `docs/`.

`replace_analysis_cache_from_rag_support()` runs after the swap and rebuilds
the flat `output/{project_id}/analysis_cache/` that
`services/git_reingestion.py::setup_incremental_staging()` restores into
`docs_staging/_analysis/` on incremental runs.

`_rag_support/` is internal: `RagBridge` only pushes top-level `docs/*.md`,
and the docs routes never expose underscore-prefixed paths.

TWO LAYOUTS, ONE ARTIFACT PER GROUP
-----------------------------------
Phase 2 stores a group either flat (`_analysis/fourps.md`) or as a directory of
per-section files (`_analysis/fourps/COMPONENTS.md`, ...). Enumeration and
reading go through `providers/custom/analysis_store`, never a glob, because a
glob over the directory layout finds nothing at all and a glob over the flat
one has already shipped the defect where `{group}_summary.md` counted as a
group (run 260: 15 analyses reported as 30).

The package keeps ONE artifact per group in BOTH layouts -- a directory is
rendered back to one `{group}.md` by `analysis_store.read_group_analysis` --
and that is a deliberate choice, not an accident of the old shape:

* Retrieval granularity is already per section without per-section files. The
  RAG ingester chunks with `ingestion/markdown_chunker.chunk_markdown`, which
  is heading-aware for h1-h4, prepends the heading path to the embedded text
  and records `section` / `unit` per chunk. The rendered `### SECTION` headings
  therefore produce the same per-section chunks a per-section file would.
* The package is also the incremental-regeneration cache.
  `replace_analysis_cache_from_rag_support()` copies these filenames verbatim
  into `analysis_cache/`, and `git_reingestion.setup_incremental_staging()`
  copies them straight back into `docs_staging/_analysis/`. A per-section
  filename (`fourps__COMPONENTS.md`) restored there would be read by
  `analysis_store.list_groups` as a GROUP -- the exact confusion above, one
  layout later.
* Measured on run 258 (ETTM, 20 groups): one artifact per group is 20
  artifacts / 532,573 bytes; one per section is 301 artifacts / 526,379 bytes,
  of which 71 are under 500 bytes and 35 under 200 -- a 15x document count for
  no extra evidence and a long tail of documents too thin to answer anything.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import quote

from mcp_server.services.client_language_guard import (
    REDACTED_CATEGORIES,
    leakage_findings,
    redact_internal_language,
)

logger = logging.getLogger(__name__)

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
LAYER_EDITORIAL_OVERLAYS = "editorial_overlays"
# Enterprise Talend runs project the validated knowledge model (journeys,
# designs, unknowns) under _rag_support/knowledge_model/ so RAG can answer
# developer/operator/architect/business-owner questions the client prose omits.
LAYER_KNOWLEDGE_MODEL = "knowledge_model"

#: Internal layers whose content is run through
#: ``client_language_guard.redact_internal_language`` before it is pushed to
#: RAG (see :func:`collect_internal_artifacts`).
#:
#: ``technical_analysis`` only, and the boundary is deliberate. That layer is
#: model-authored prose about the estate, where an internal path is incidental
#: leakage. The other internal layers are deterministic Python renderings whose
#: SUBJECT is often an artifact name -- ``support_maps`` is
#: ``_facts/engineering_facts.md`` and friends, ``evidence_slices`` is per-doc
#: evidence keyed by artifact -- so blanking those names would not remove
#: leakage, it would remove the content. Extending this set to another layer is
#: one line, and needs its own measurement first.
REDACTED_INTERNAL_LAYERS: frozenset[str] = frozenset({LAYER_TECHNICAL_ANALYSIS})

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


def analysis_stem_matches_group(stem: str, group: str) -> bool:
    """True when an ``_analysis/*.md`` slice stem belongs to ``group``.

    Mirrors the pipeline's slice->group resolver convention
    (``harness._group_units``: ``group.startswith(f"{u.group}_part")``;
    ``validators._groups_for_analysis_file``): Phase 2 writes one analysis per
    *preprocess slice*, and a slice base is either the group itself
    (``{group}.md``) or a ``{group}_part*`` disambiguation -- large-group
    splits (``{group}_part1``/``_part2``), case-insensitive-filesystem
    collision slices (``{group}_part_ci{k}``), and nested collider splits
    (``{group}_part_ci{k}_1``/``_2``).

    Comparison is case-SENSITIVE on the actual directory-entry stem: on
    case-insensitive filesystems ``analysis_dir / f"{group}.md"`` can resolve
    to a *different* group's file that differs only in case (verified on the
    werkorder Assetcheck run: ``AssetCheck2Vise`` resolved to
    ``AssetCheck2VISE.md`` and its real ``AssetCheck2Vise_part_ci1.md``
    analysis was silently dropped from the package).
    """
    return stem == group or stem.startswith(f"{group}_part")


def _analysis_store():
    """Import the analysis store lazily.

    A module-level import would pull ``providers/custom/__init__``, which
    imports the 7,876-line harness, into every importer of this module --
    including ``rag_bridge``, which the API routes import at startup. It would
    also create a latent cycle: ``pipeline/phases/phase4_cleanup`` imports this
    module, and the harness imports that phase.
    """
    from mcp_server.providers.custom import analysis_store

    return analysis_store


class AnalysisSliceLayout(str, Enum):
    """How one packaged analysis slice is stored under ``_analysis/``."""

    FLAT = "flat"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class AnalysisSlice:
    """One packageable analysis: its stem, its layout and its flat path."""

    stem: str
    layout: AnalysisSliceLayout
    path: Path | None = None

    @property
    def filename(self) -> str:
        """Name inside the package. One file per slice under either layout."""
        return f"{self.stem}.md"

    @property
    def source_path(self) -> str:
        """Staging-relative origin, recorded in the manifest."""
        if self.layout is AnalysisSliceLayout.DIRECTORY:
            return f"_analysis/{self.stem}/"
        return f"_analysis/{self.stem}.md"


def _analysis_slices(analysis_dir: Path) -> list[AnalysisSlice]:
    """Every stored analysis under ``_analysis/``, sorted, whatever its layout.

    Enumeration is ``analysis_store.list_groups``, which owns the exclusion
    rule (no ``_``-prefixed artifact, no ``{group}_summary.md``) for both
    layouts. Deliberately NOT a glob: ``glob("*.md")`` returns nothing for a
    group stored as a directory, which is how a complete estate would publish
    as an empty package.
    """
    store = _analysis_store()
    slices: list[AnalysisSlice] = []
    for stem in store.list_groups(analysis_dir):
        try:
            layout = store.layout_of(analysis_dir, stem)
        except store.AnalysisPathError:
            continue
        if layout is store.AnalysisLayout.DIRECTORY:
            slices.append(AnalysisSlice(stem, AnalysisSliceLayout.DIRECTORY))
        elif layout is store.AnalysisLayout.FLAT:
            slices.append(
                AnalysisSlice(
                    stem, AnalysisSliceLayout.FLAT, analysis_dir / f"{stem}.md"
                )
            )
    return slices


def resolve_group_analysis_files(
    analysis_dir: Path, groups: Iterable[str]
) -> tuple[dict[str, list[AnalysisSlice]], list[str]]:
    """Assign each stored ``_analysis/`` slice to its owning manifest group.

    Returns ``(owned, missing_groups)`` where ``owned`` maps each group with
    at least one analysis slice to its sorted slices, and ``missing_groups``
    lists manifest groups with none. Ownership follows
    :func:`analysis_stem_matches_group`; an exact stem match wins outright,
    otherwise the longest matching group owns the slice (so a slice can never
    be claimed by a shorter prefix group). Slices whose stem matches no
    manifest group -- runtime artifacts (``_doc_pack_*``, ``_bundle_*``) and
    non-manifest analyses -- are never assigned.
    """
    group_set = set(groups)
    owned: dict[str, list[AnalysisSlice]] = {group: [] for group in group_set}
    for item in _analysis_slices(analysis_dir):
        if item.stem in group_set:
            owned[item.stem].append(item)
            continue
        candidates = [
            group for group in group_set
            if analysis_stem_matches_group(item.stem, group)
        ]
        if candidates:
            owned[max(candidates, key=len)].append(item)
    missing = sorted(group for group, files in owned.items() if not files)
    return {g: files for g, files in owned.items() if files}, missing


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
    resumed run cannot survive) and packages every `_analysis/` slice owned by
    a manifest group -- ``{group}`` plus any ``{group}_part*`` slice the
    pipeline produced (large-group splits, case-collision ``_part_ci{k}``
    slices; see :func:`resolve_group_analysis_files`). A FLAT slice is copied
    byte-for-byte; a DIRECTORY slice is rendered back to one markdown file by
    ``analysis_store.read_group_analysis`` (see the module docstring for why
    one artifact per group rather than one per section). Slice filenames are
    kept verbatim so case-colliding groups cannot clobber each other inside
    the package on case-insensitive filesystems. Groups without any analysis
    slice are recorded in the manifest's `missing_groups`; no fallback content
    is synthesized.

    A slice that cannot be read is contained, not fatal: it is recorded in
    `failed_slices` and the rest of the estate still publishes. Losing 19
    groups because the 20th has an unreadable directory is the fail-closed
    behaviour this lane has been removing everywhere else.

    Returns the manifest dict that was written to `_rag_support/manifest.json`.
    """
    analysis_dir = staging_dir / "_analysis"
    support_dir = staging_dir / RAG_SUPPORT_DIRNAME
    package_dir = support_dir / PHASE2_ANALYSIS_SUBDIR

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    store = _analysis_store()
    owned, missing_groups = resolve_group_analysis_files(analysis_dir, groups)
    artifacts: list[dict] = []
    failed_slices: list[dict] = []
    for group in sorted(owned):
        for item in owned[group]:
            target = package_dir / item.filename
            try:
                if item.layout is AnalysisSliceLayout.DIRECTORY:
                    target.write_text(
                        store.read_group_analysis(analysis_dir, item.stem),
                        encoding="utf-8",
                    )
                    sections = list(store.list_sections(analysis_dir, item.stem))
                else:
                    shutil.copy2(item.path, target)
                    # Empty because a flat file's sections are only knowable by
                    # PARSING it, and the whole point of the directory layout
                    # is that a listing replaces that parse. ``layout`` is what
                    # distinguishes "flat, not enumerated" from "no sections".
                    sections = []
                data = target.read_bytes()
            except (OSError, ValueError, store.SectionContractError) as exc:
                target.unlink(missing_ok=True)
                logger.warning(
                    "RAG support package: cannot read analysis %s (%s)",
                    item.source_path,
                    exc,
                )
                failed_slices.append(
                    {
                        "group": group,
                        "source_path": item.source_path,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            # Diagnostics only. The rewrite that ENFORCES this happens at the
            # RAG boundary in collect_internal_artifacts(), because the bytes
            # on disk here are also restored into analysis_cache/ and back
            # into _analysis/ on an incremental run, where internal paths are
            # correct working evidence.
            residue = leakage_findings(
                data.decode("utf-8", errors="replace"), document=item.filename
            )
            artifacts.append(
                {
                    "path": f"{PHASE2_ANALYSIS_SUBDIR}/{item.filename}",
                    "source_path": item.source_path,
                    "source_layer": LAYER_TECHNICAL_ANALYSIS,
                    "artifact_type": "phase2_group_analysis",
                    "visibility": "internal",
                    "group": group,
                    "layout": item.layout.value,
                    "sections": sections,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "internal_language_findings": len(residue),
                    "internal_language_redactable": sum(
                        1 for f in residue if f.category in REDACTED_CATEGORIES
                    ),
                }
            )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_id": project_id,
        "platform": platform,
        "artifacts": artifacts,
        "missing_groups": missing_groups,
        "failed_slices": failed_slices,
        "internal_language": {
            "findings": sum(a["internal_language_findings"] for a in artifacts),
            "redactable": sum(a["internal_language_redactable"] for a in artifacts),
            "enforced_at": "collect_internal_artifacts",
        },
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

    Content of the layers in :data:`REDACTED_INTERNAL_LAYERS` is rewritten by
    ``client_language_guard.redact_internal_language`` on the way out. This is
    the publication boundary and the only place the rewrite happens: the file
    on disk stays verbatim because it is also the incremental-regeneration
    cache. Marking the layer internal is NOT sufficient on its own -- measured
    on the RAG backend, an internal chunk is out of the default partition but
    rung-2 escalation retrieves it and merges it as an auxiliary source that
    "receive[s] reference numbers and [is] citable", so its text can be quoted
    back to a client verbatim. See the ``client_language_guard`` module
    docstring for the full measurement and for why prose categories are
    reported rather than rewritten.
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
        (
            LAYER_EDITORIAL_OVERLAYS,
            "editorial_overlay",
            sorted((docs_dir / RAG_SUPPORT_DIRNAME / "editorial_overlays").glob("*.md")),
            None,
        ),
        (
            LAYER_KNOWLEDGE_MODEL,
            "knowledge_model",
            sorted((docs_dir / RAG_SUPPORT_DIRNAME / LAYER_KNOWLEDGE_MODEL).glob("*.md")),
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
            redactions = 0
            if source_layer in REDACTED_INTERNAL_LAYERS:
                content, redactions = redact_internal_language(content)
            metadata: dict = {
                "source_layer": source_layer,
                "artifact_type": artifact_type,
                "visibility": "internal",
                "artifact_path": path.relative_to(docs_dir).as_posix(),
                "doc_name": path.stem,
                "project_id": project_id,
            }
            if source_layer in REDACTED_INTERNAL_LAYERS:
                metadata["internal_language_redactions"] = redactions
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
