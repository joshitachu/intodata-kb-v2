# KB Sub-project Context

## Last Completed (2026-05-20)

P2/P3/P4 custom harness reliability campaign: targeted investigation repair, provider contract enforcement, native json_mode, LockHeartbeat, startup orphaned-run reconciler, credential preflight, word-boundary coverage fix, prompt-pack fallback warnings. 336/336 tests pass.

## Current State

- Custom harness provider is the primary focus for ongoing stability work.
- All changes in working tree, uncommitted. Two new files untracked: `services/generation_run_state.py`, `tests/test_services/test_generation_run_state.py`.
- `.agent/` docs were audited and updated in the previous session (commit 9913dc6).
- EP-001 (deployment foundation) blocked on VPS availability for US-002 validation.

## Immediate Next Steps

1. Commit the P2/P3/P4 working tree changes (15 modified files, 2 new untracked).
2. Manual verification checklist (from plan):
   - Fallback disabled by default: set `MCP_DOC_GENERATION_FALLBACK_MODEL=openai/gpt-5.4`; confirm clean error, not 404 cascade.
   - Credential warning: unset `ANTHROPIC_API_KEY` with `doc_generation_model=anthropic/...`; confirm startup WARNING.
   - Lock heartbeat: run a generation; confirm lock `updated_at` increments every ~60s.
   - Startup reconciler: insert `GenerateRun(status="running")` with no lock file; restart server; confirm row becomes `status="failed"`.
   - Coverage false-positive: doc with text "mapping"; confirm unit named "Map" is NOT marked covered.
3. EP-001: validate runbook on a clean reference VPS (US-002, still the main blocker).

## Blockers

- EP-001 US-002: requires a clean reference VPS. No code blocker.

## Active Work Areas

| Area | Status |
|------|--------|
| Custom harness (P2/P3/P4) | Complete, uncommitted |
| EP-001 deployment | In Progress — VPS blocked |
| Dashboard | Stable |
| RAG pipeline | Stable |
