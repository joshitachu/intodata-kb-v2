---
inspected_at: 2026-06-12
verdict: WARN
---

## Verdict Summary
Implementation conforms to the approved Slice 2 plan with no blocking defects;
four warnings, the most substantive being an empty-package edge case that
weakens the documented full-regen fallback semantics.

## Check Results
| Check              | Result | Notes |
|--------------------|--------|-------|
| Plan conformance   | PASS   | All 7 build steps implemented as specified; step 8 (docs/context update) is explicitly deferred to /session-end. |
| Root cause         | PASS   | Replaces the dead post-swap `docs/_analysis` copy with a persisted package built before Phase 4 deletes `_analysis/`; fixes the cause, not the symptom. |
| Generalized        | PASS   | `analysis_cache` now has exactly one writer (`rag_support_package.py`) and one reader (`git_reingestion.py`); no other `docs/_analysis` lookups remain in `src/`. |
| Design patterns    | PASS   | Service-level pure-filesystem helper shared by the Phase 4 producer and the route consumer; allowlist-driven, deterministic, no synthesis. |
| Contract respect   | WARN   | Staging/atomic-swap contract intact; RagBridge pushes only top-level docs (tested). But `.agent/system/mcp-workflow.md` Phase 4 row does not yet mention `_rag_support/` packaging — must be updated at /session-end (Finding 3). |
| No special-casing  | PASS   | No flags, shims, or one-off branches; the helper is generic across standard and resumed runs (stale package files are removed before rebuild, tested). |
| Edge cases         | WARN   | Plan's verification strategy executed (targeted + critical-area + full suite, 608 passed). Two gaps: empty-package semantics (Finding 1) and untested missing-groups warning branch (Finding 2). |
| RBAC               | PASS   | No route signatures or access checks changed; `_run_generate_background` is an internal background task behind the existing generate route's `require_project_access`. |
| Credential safety  | PASS   | Manifest contains only paths, byte counts, and SHA-256 hashes; no content is sent to any LLM; no credentials touched. |

## Findings

1. **WARN — empty support package produces an empty-but-present `analysis_cache/`**
   `rag_support_package.py:104-118`. When Phase 2 is skipped
   (`plan.analysis_mode == "skip"`) or all groups are missing, the package dir
   exists with zero `.md` files, so `replace_analysis_cache_from_rag_support`
   creates an empty `analysis_cache/` and returns True. Verified by probe.
   `setup_incremental_staging` (`git_reingestion.py:215`) treats a
   present-but-empty cache as usable and proceeds with incremental staging
   instead of the documented full-regen fallback. Functionally Phase 2
   regenerates the groups anyway, but the plan's stated semantics ("falls back
   to full regen") are better served by treating an empty package as no
   package: skip cache creation and return False.

2. **WARN — missing-groups warning branch untested**
   `phase4_cleanup.py:85-91`. The `await log_cb(...)` path fires only when
   manifest groups lack `_analysis/{group}.md` (e.g. analysis-skip runs); no
   test exercises it. Note the existing Phase 4 tests pass `log_cb=MagicMock()`,
   which would raise if this branch were hit — a test must use an async-capable
   mock.

3. **WARN — workflow contract doc not yet updated**
   `.agent/system/mcp-workflow.md` (Phase 4 row, line 21) describes Phase 4 as
   "Enterprise gate evaluation, audit bundle write, engineering artifact
   archival, and run log" — it now also builds `_rag_support/`. Plan step 8
   defers docs updates to /session-end; include this file alongside
   `PROJECT_MEMORY.md` and `context.md`.

4. **WARN — `setup_incremental_staging` has no direct test coverage**
   Pre-existing gap, not introduced by this diff. Plan step 6 says to "keep
   git_reingestion.setup_incremental_staging() flat-cache behavior unchanged";
   no code change was made, but there is no regression net asserting the flat
   `analysis_cache/*.md` → `docs_staging/_analysis/` behavior the new helper
   now feeds.

## Required Actions

No blocking changes.

WARN (optional improvements):
- Finding 1: make `replace_analysis_cache_from_rag_support` treat an empty
  package as absent (return False, leave no cache dir).
- Finding 2: add a missing-groups test with an async log_cb mock.
- Finding 3: update `.agent/system/mcp-workflow.md` Phase 4 row at /session-end.
- Finding 4: add a minimal `setup_incremental_staging` regression test.

## Resolution (post-inspection, same session)

- Finding 1: FIXED — empty package now returns False and leaves no cache dir
  (`rag_support_package.py`); regression test
  `test_cache_restore_treats_empty_package_as_absent`.
- Finding 2: FIXED — `test_phase4_logs_missing_group_analyses` exercises the
  warning branch with an AsyncMock log_cb.
- Finding 3: OPEN — deferred to /session-end docs update as planned.
- Finding 4: FIXED — new `tests/test_services/test_git_reingestion.py` covers
  flat-cache staging, absent-cache fallback, and first-push skip.

Full suite after fixes: 613 passed.
