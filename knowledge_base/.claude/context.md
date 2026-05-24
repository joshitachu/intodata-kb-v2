# KB Sub-project Context

## Last Completed (2026-05-24)

EP-005 Mission Control receiver built (`mission_control/` at repo root). Three-endpoint FastAPI service accepts heartbeat pushes from customer MCP servers, stores snapshots as JSON files per client slug. Tested end-to-end: local Docker stack pushed via `host.docker.internal:9100`; real koopmans VPS pushed via cloudflared tunnel. Both confirmed working. Next: deploy receiver to a permanent server, remove tunnel, build static report UI.

## Previous (2026-05-21)

Enterprise documentation quality gates and verdicts: enterprise mode is now the default publish path, Phase 3 doc depth is blocking, final redaction/provenance/usefulness/reviewer/quality gates are written to `_assessment/enterprise_gates.json`, audit bundles and final doc hashes are preserved under `_audit/`, scans fail closed on missing/invalid `scan_result.json`, generation background crashes complete runs as failed instead of leaving projects stuck, and dashboard run history shows structured verdicts. 350/350 backend tests pass; dashboard build passes.

## Current State

- Enterprise publish gates are implemented but not yet deployed/committed.
- Structured run verdicts now exist: `draft`, `failed`, `published`, `published_with_rag_error`, `client_ready`.
- Failed enterprise runs preserve `output/{project_id}/docs_staging/` for repair/resume and must not be treated as client-ready.
- Scanner output cleanup is stricter: stale `scan_result.json` and segmented `scan/` output are removed before scan; subprocess success without a valid new `scan_result.json` is a failure.
- EP-001 (deployment foundation) blocked on VPS availability for US-002 validation.

## Immediate Next Steps

1. **EP-005 next**: deploy Mission Control to a permanent server (dev/nexus server), wire nginx, set permanent `MCP_OPS_TELEMETRY_URL` on koopmans VPS, remove cloudflared tunnel dependency.
2. **EP-005 after deploy**: build static report UI (Jinja2 template per client slug) on top of stored snapshots.
3. Fix git poller TTY / token leak bugs (see PROGRESS_LOG 2026-05-21):
   - Add `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=echo` to `deploy/mcp-server@.service` env
   - Clean up token-injected remote URL after fetch in both `_fetch_remote` and `_run_diff_patch`
   - Call `registry.update_rag_push()` in `_run_generate_background` after a successful RAG push
4. Commit the enterprise gate changes and migration `013_add_generate_run_enterprise_verdict.py`.
5. EP-001: validate runbook on a clean reference VPS (US-002, still the main blocker).

## Blockers

- EP-001 US-002: requires a clean reference VPS. No code blocker.

## Active Work Areas

| Area | Status |
|------|--------|
| EP-005 Mission Control | In Progress -- receiver built, tunnel test passed, deploy pending |
| Enterprise documentation gates | Complete, uncommitted |
| Harness reliability (phases 1–6) | Complete, uncommitted |
| EP-001 deployment | In Progress -- VPS blocked |
| Dashboard | Stable |
| RAG pipeline | Stable |
