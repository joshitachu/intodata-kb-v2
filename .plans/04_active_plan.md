---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
slice: Slice 2 -- Harness `_rag_support/` persistence and analysis_cache restore
sub_project: knowledge_base
critical_area: yes -- harness pipeline phases and MCP API routes (`/inspect` required)
verdict: PASS
approved_at: 2026-06-12
---

## Problem Statement

Slice 1 made the RAG backend layer-ready, but the MCP generation harness still
deletes the Phase 2 material that later slices need.

Verified current behavior:

1. `Phase4Cleanup` deletes `docs_staging/_analysis/` before the atomic swap.
   The generated `docs/` directory therefore cannot retain group-level Phase 2
   analyses for hidden RAG layers.
2. `_run_generate_background` tries to repopulate
   `output/{project_id}/analysis_cache/` from `docs/_analysis/` after the
   swap, but that directory no longer exists. Incremental webhook regeneration
   then falls back to full regeneration because the cache is absent.
3. A broad copy of `_analysis/` would be unsafe: `_analysis/` also contains
   runtime doc packs such as `_doc_pack_*.md`, bundle files like
   `_bundle_*.md`, reports, and future internal artifacts. The support package
   must be a positive allowlist driven by `state.facts_manifest.groups`.
4. Visible documentation must remain byte-identical: top-level `NN_*.md` docs,
   RAG push behavior, UI-visible docs, and the atomic swap contract cannot
   change in this slice.

## Proposed Solution

Add a small shared support-package helper and wire it into Phase 4 and the
post-swap generate route.

**1. Support package helper**

Create `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py`
with path constants and pure filesystem helpers:

- `build_rag_support_package(staging_dir: Path, groups: Iterable[str], *, project_id: str, platform: str) -> dict`
  - Recreates `docs_staging/_rag_support/phase2_analysis/` from scratch so
    stale groups from a resumed run cannot survive.
  - Copies only `_analysis/{group}.md` for group names in the facts manifest,
    sorted for deterministic output.
  - Never copies `_doc_pack_*`, `_bundle_*`, `_coverage_report.json`,
    `_depth_report.json`, or any unlisted future artifact.
  - Writes `_rag_support/manifest.json` with `schema_version`, `project_id`,
    `platform`, `artifacts`, and `missing_groups`.
  - Each artifact entry includes `path`, `source_path`, `source_layer:
    "technical_analysis"`, `artifact_type: "phase2_group_analysis"`,
    `visibility: "internal"`, `group`, `bytes`, and `sha256`.
  - Does not synthesize content. Missing group files are recorded in
    `missing_groups` and can be logged, but no fallback file is created.
- `replace_analysis_cache_from_rag_support(output_dir: Path) -> bool`
  - Reads `output/{project_id}/docs/_rag_support/phase2_analysis/*.md`.
  - Replaces `output/{project_id}/analysis_cache/` with a flat copy of those
    group `.md` files.
  - Removes any stale cache when no support package is present, so incremental
    regeneration falls back to full regen instead of using old summaries.

This helper is intentionally service-level, not a private harness method,
because Phase 4 creates the package and `api/routes/projects.py` consumes it
after the swap. Slice 3 can reuse the same constants when it ingests internal
layers.

**2. Phase 4 packaging before cleanup**

Update `Phase4Cleanup.run()` to call `build_rag_support_package()` after audit
and engineering artifact archival, but before the loop that deletes
`_preprocess`, `_analysis`, and `_transient`.

`_rag_support/` is not added to the deletion list. It survives the existing
`swap_docs(output_dir)` because the atomic swap moves the whole staging tree
into `docs/`.

**3. Repoint `analysis_cache` refresh**

Update `_run_generate_background()` in `api/routes/projects.py`:

- Remove the dead post-swap `docs/_analysis` copy block.
- After `swap_docs(output_dir)`, call
  `replace_analysis_cache_from_rag_support(output_dir)`.
- Keep `services/git_reingestion.py::setup_incremental_staging()` compatible by
  preserving the flat `analysis_cache/*.md` layout it already restores into
  `docs_staging/_analysis/`.

**4. Keep visible and RAG-facing behavior unchanged**

No change to `RagBridge._read_md_files()`: it already reads only top-level
markdown files from `docs/` and therefore ignores `_rag_support/`. No frontend
or public docs route changes are needed for this slice.

## Affected Files

- `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py` -- NEW shared package/cache helper
- `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py` -- build `_rag_support/` before deleting `_analysis`
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` -- refresh `analysis_cache` from the persisted support package after swap
- `knowledge_base/mcp-server/tests/test_services/test_rag_support_package.py` -- NEW helper tests
- `knowledge_base/mcp-server/tests/test_pipeline/test_warning_collector.py` or `tests/test_providers/test_custom_provider.py` -- Phase 4 integration coverage
- `knowledge_base/mcp-server/tests/test_services/test_rag_bridge.py` -- assert nested `_rag_support` markdown is not pushed
- `knowledge_base/mcp-server/tests/test_api/test_generate_rag_diff.py` or `tests/test_services/test_rag_support_package.py` -- cache refresh regression coverage

## Architecture Contracts

- Root and `knowledge_base/CLAUDE.md`: no workarounds; generalize the dead
  cache copy instead of adding another one-off `_analysis` lookup.
- Critical-area rule: this touches
  `providers/custom/pipeline/phases/phase4_cleanup.py` and
  `api/routes/projects.py`, so `/inspect` is required before validation and
  commit.
- `knowledge_base/.claude/PROJECT_MEMORY.md`: `_audit/` must not become a
  wholesale copy tree; `_transient/` remains throwaway; `_rag_support/` is a
  separate hidden RAG-support package, not audit/debug output.
- PRD FR2: `_rag_support/` uses a manifest-driven allowlist of Phase 2 group
  analyses and must not include `_doc_pack_*`, `_bundle_*`, prompts, provider
  traces, or scratch/runtime files.
- Staging contract: on success, `docs_staging/` atomically becomes `docs/`; on
  failure, existing `docs/` remains preserved. This slice only changes what
  successful staging contains before the swap.
- RAG contract: server-side RAG push continues to index only visible final docs
  in Slice 2. Internal-layer ingestion waits for Slice 3.

## Implementation Steps

1. **Add support-package helper.**
   Implement `rag_support_package.py` with deterministic manifest generation,
   SHA-256 calculation, stale `_rag_support/phase2_analysis` cleanup, positive
   group allowlist copying, and cache replacement from the final `docs/`
   package.

2. **Unit-test package selection.**
   Build a fake staging tree with:
   - `_analysis/A.md` and `_analysis/B.md` listed in the facts groups
   - `_analysis/C.md` not listed
   - `_analysis/_doc_pack_01_ARCHITECTURE_MAP.md`
   - `_analysis/_bundle_core.md`
   Assert that only `A.md` and `B.md` land in
   `_rag_support/phase2_analysis/`, stale prior files are removed, and
   `manifest.json` contains the expected metadata, byte counts, and hashes.

3. **Wire Phase 4.**
   Call the helper in `Phase4Cleanup.run()` before the cleanup loop. Keep the
   cleanup loop scoped to `_preprocess`, `_analysis`, and `_transient`.

4. **Phase 4 integration test.**
   Extend existing Phase 4 tests or add a focused test that runs
   `Phase4Cleanup` with a facts manifest and `_analysis/A.md`, then asserts:
   `_analysis` is removed, `_rag_support/phase2_analysis/A.md` remains,
   `manifest.json` exists, and the final top-level docs are untouched.

5. **Repoint analysis cache refresh.**
   Replace the dead `docs/_analysis` block in `_run_generate_background()` with
   `replace_analysis_cache_from_rag_support(output_dir)`. The helper should
   delete stale cache first and then copy only package group `.md` files.

6. **Cache restore regression tests.**
   Test that cache refresh creates `analysis_cache/A.md` from
   `docs/_rag_support/phase2_analysis/A.md`; test that a missing support
   package removes stale `analysis_cache/`; and keep
   `git_reingestion.setup_incremental_staging()` flat-cache behavior unchanged.

7. **Visibility/RAG safety tests.**
   Add or update a `RagBridge` test with
   `docs/_rag_support/phase2_analysis/A.md` plus top-level `01_OVERVIEW.md`;
   assert only the top-level visible doc is pushed.

8. **Docs/context update after build.**
   During `/session-end`, record the new `_rag_support/` contract in
   `knowledge_base/.claude/PROJECT_MEMORY.md` and update
   `knowledge_base/.claude/context.md` with Slice 2 status.

## Verification Strategy

- Syntax:
  `cd knowledge_base/mcp-server && uv run python -m py_compile <changed .py files>`
- Targeted tests:
  `cd knowledge_base/mcp-server && uv run pytest tests/test_services/test_rag_support_package.py tests/test_pipeline/test_warning_collector.py tests/test_services/test_rag_bridge.py tests/test_api/test_generate_rag_diff.py`
- Broader critical-area tests:
  `cd knowledge_base/mcp-server && uv run pytest tests/test_providers/test_custom_provider.py tests/test_api/test_projects.py tests/test_api/test_webhook.py`
- Full MCP validation before commit:
  `cd knowledge_base/mcp-server && uv run pytest`
- Inspection:
  Run `/inspect` after implementation because the slice touches the harness
  pipeline and MCP API route.

Manual check after tests, if a local generated project is available:

- Run one generation.
- Confirm `output/{project_id}/docs/_rag_support/phase2_analysis/*.md` exists.
- Confirm `output/{project_id}/docs/_rag_support/manifest.json` lists only
  manifest groups.
- Confirm top-level visible markdown docs did not change because of packaging.
- Confirm `output/{project_id}/analysis_cache/*.md` is populated after success.

## Out of Scope

- Pushing `_rag_support/` to the RAG backend (Slice 3).
- Any RAG backend API, retrieval ladder, or entity-index change.
- Persisting `_facts/`, `_assessment/scorecard.md`, evidence slices, or support
  maps as RAG layers. Slice 2 only preserves Phase 2 group analyses and repairs
  `analysis_cache`.
- Frontend/UI visibility for `_rag_support/`.
- Re-ingesting existing projects into RAG.
- Changing Phase 2 analysis content or final document prompts.
