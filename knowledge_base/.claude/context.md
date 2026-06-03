# KB Sub-project Context

## Last Completed (2026-06-02)

Flagged dynamic DAG harness foundation implemented. New `ExecutionPlan` / `ExecutionGraph` / `ExecutionNode` contracts, deterministic graph fallback, optional LLM Phase 0 planner, graph validation, and `_plans/` telemetry (`execution_plan.json`, `execution_graph.json`, `node_results.json`, `graph_events.jsonl`) are in place. DAG mode is off by default (`MCP_AGENT_DAG_ENABLED=false`), planner is separately off (`MCP_AGENT_PLANNER_ENABLED=false`), and final validators/publish gates remain mandatory. Phase 2 now has a planner-selected skeleton/deep-dive path: skeleton sidecars in `_analysis/_skeleton/`, shallow groups via direct Phase 2, deep groups via `phase2.deep_dive`. Tools are enabled only for explicit phases (`phase1.investigate`, `phase2.deep_dive`, repair, critic/consistency, Phase 3), never globally for Phase 2. Added Phase 3.6 consistency report and flagged smart-repair root-cause classification. Focused tests: 205 passed. Docker rebuild succeeded for mcp-server, backend, frontend, dashboard, and mission-control images.

## Previous (2026-06-02)

Three compounding repair-path bugs fixed: (1) `_tool_policy` ordering bug silently disabled tools for `"phase2.repair"` — `"repair" in phase` check now runs before `startswith("phase2")`; (2) synthesis doc Evidence Register repair prompt now tells the model to cite sibling doc filenames for `09_EXECUTIVE_SUMMARY` and `07_QUALITY_SECURITY_RISK_SCORECARD`; (3) `_phase3_targeted_repair_content` now merges rows by topic instead of replacing the full register, preventing oscillating failures. 513 tests pass. Container rebuilt and healthy.

## Previous (2026-06-01)

Agentic investigation hardened through live run analysis on Steven/inventosryddl: (1) manifest now includes `identifiers` (actual SQL table names, class names, connection names, URLs, config keys) extracted by `_extract_chunk_identifiers` — model can reason about specific named entities per chunk; (2) safety net force-adds any chunks containing units the model missed; (3) extraction batched into parallel calls of 20 units each to avoid 10k output token truncation; (4) pack docs (TABLE_INVENTORY.md etc.) now pushed to RAG via `required_docs` on `AgentResult` forwarded through `PluginRunner`; (5) `/push-to-rag` endpoint auto-detects pack docs. `MCP_AGENT_PHASE1_AGENTIC=true` set in docker-compose. 513 tests pass.

## Previous (2026-05-29)

Plugin pack phase control + investigation reliability + nginx DNS fix. `PhasePolicy` model added to manifest schema; `_phase_policy()` replaces single `_pack_contract_mode` boolean; `investigation: run` now supported with preamble override via `prompts/investigation.md`; `analysis: bounded` uses pack's `phase2.md` as full prompt; `_units_for_chunk` uses word-boundary regex; investigator auto-skips on max retries; Phase 3 receives filtered, capped investigation bundle per doc. nginx resolver and variable-based proxy_pass fix stale-IP 502s after upstream container restarts. `table-inventory` pack updated to `investigation: run` with comprehensive DB extraction prompt. 508 tests pass.

## Previous (2026-05-29)

Integration Suite Reliability Layer — all 7 plan items implemented and 508 tests pass:
(1) `_PipelineLog` class writes `_pipeline_log.md` per phase during run; (2) `ToolPolicy` frozen dataclass replaces `use_tools_for_core_generation()` / `tool_input_token_cap_for_phase()` — scorecard phase now explicitly disabled for tools; (3) minimum-byte gate (150 bytes) in `_phase3_docs` fast-fails near-empty output before expensive validators; (4) `validation_gaps` threaded into repair `AgentTaskContract` from external validator errors; (5) `validate_scorecard_contract()` checks 8 dimensions, weights, [SOLO]/[PAIR] tags in risk rows; (6) `_write_phase_costs()` writes `_assessment/phase_costs.json` and warns on threshold breaches; (7) single-phase rerun API (`POST /generate-runs/{project_id}/{run_id}/rerun`) + migration 015; (8) `09_EXECUTIVE_SUMMARY` added as enterprise-only doc dispatched last; (9) `talend_intelligence.py` with ESB detection and activity flags; (10) ESB guide-section validator rule; (11) Workato export-drift guidance in Phase 2 prompt; (12) Talend connection semantic table requirement in `01_ARCHITECTURE_MAP`.

## Previous (2026-05-28)

Stopped the `Steven_inventory` Phase 2 tool-token runaway path. Phase 2 core generation now uses bounded context files instead of generic tool mode by default; `read_artifact` has offset/limit caps and budget accounting; OpenAI-compatible tool loops have accumulated input-token ceilings; `table-inventory` receives a curated Phase 2 evidence bundle. Durable rule: core generation writes from bounded evidence; retrieval may be agentic but must be budgeted.

## Previous (2026-05-24)

Fixed three EP-005 blockers preventing koopmans heartbeats from reaching Mission Control: (1) `_fetch_rag_metrics()` guard was too strict — returned `{}` when `MCP_INGEST_API_KEY` unset even though RAG backend supports open access; (2) missing Alembic migration `014` for `plugin_packs` table and `projects.plugin_pack_id` FK caused `UndefinedColumnError` on every telemetry push; (3) mcp-server Dockerfile was not copying `alembic/` into the image so migrations never ran on VPS. All three committed and pushed; koopmans needs one more `git pull + rebuild` to apply.

## Previous (2026-05-21)

Enterprise documentation quality gates and verdicts: enterprise mode is now the default publish path, Phase 3 doc depth is blocking, final redaction/provenance/usefulness/reviewer/quality gates are written to `_assessment/enterprise_gates.json`, audit bundles and final doc hashes are preserved under `_audit/`, scans fail closed on missing/invalid `scan_result.json`, generation background crashes complete runs as failed instead of leaving projects stuck, and dashboard run history shows structured verdicts. 350/350 backend tests pass; dashboard build passes.

## Current State

- Integration Suite Reliability Layer complete. 508+ focused tests have passed across recent harness changes.
- Flagged dynamic DAG harness foundation is implemented but disabled by default. Enable with `MCP_AGENT_DAG_ENABLED=true`; enable LLM planning separately with `MCP_AGENT_PLANNER_ENABLED=true`.
- DAG mode persists `_plans/execution_plan.json`, `_plans/execution_graph.json`, `_plans/node_results.json`, and `_plans/graph_events.jsonl` for audit.
- `_PipelineLog` writes `_pipeline_log.md` incrementally during every run for operator visibility.
- `ToolPolicy` is the explicit per-phase tool contract; scorecard phase now correctly disables tools.
- Core Phase 1/2/scorecard remain tool-disabled; explicit DAG deep-dive/repair/consistency phases can use tools.
- Phase cost reporting (`phase_costs.json`) and minimum-byte fast-fail gate are live.
- Single-phase rerun (`POST /generate-runs/{id}/rerun`) works via artifact invalidation + resume; migration 015 pending deploy.
- `09_EXECUTIVE_SUMMARY` is the 10th enterprise required doc; dispatched after all other Phase 3 docs complete.
- Talend intelligence (ESB detection, activity flags, connection semantic table, ESB guide-section rule) is live.
- Workato export-drift guidance added to Phase 2 analysis prompt.
- Enterprise publish gates are implemented but not yet deployed/committed (pre-existing).
- EP-001 (deployment foundation) blocked on VPS availability for US-002 validation (pre-existing).

## Immediate Next Steps

1. **Validate DAG mode on a small project**: Run with `MCP_AGENT_DAG_ENABLED=true`, `MCP_AGENT_PLANNER_ENABLED=false`. Confirm deterministic fallback graph mirrors current behavior, `_plans/*.json` files are written, final gates still pass, and no Phase 2 core tool calls happen unless `phase2.deep_dive` is selected.
2. **Validate LLM planner separately**: Run with `MCP_AGENT_DAG_ENABLED=true`, `MCP_AGENT_PLANNER_ENABLED=true` on a non-client draft project. Confirm unsafe/invalid JSON falls back cleanly and valid planner output cannot add undeclared docs or omit final gates.
3. **Validate tiered Phase 2 on complex project**: Force or request `analysis_mode=skeleton_plus_deep_dive`; confirm `_analysis/_skeleton/*.json` exists, deep groups use `phase2.deep_dive`, shallow groups use direct Phase 2, and final Phase 2 validators still pass.
4. **Validate consistency report**: Introduce or find a known inventory-count contradiction in staging and confirm `_assessment/consistency_report.json` flags it before final gates.
1. **Validate repair routing on a real run**: Trigger a fresh generation on a project with a known provenance failure (e.g. the Item project from the logs). Confirm: (a) Phase 2 repair and Phase 3 repair now log `uses_tools: true`, (b) `09_EXECUTIVE_SUMMARY` Evidence Register passes without repair loop, (c) run completes without `source_provenance` gate failure.
2. **Validate batched agentic run**: Clean run on Steven project — delete staging, trigger fresh run. Check: (a) no "missing unit coverage" warning, (b) SALESTABLE/SALESLINE/MARKUPTRANS/DIRPARTYTABLE appear in TABLE_INVENTORY.md with full fields, (c) `finish=stop` on all extraction calls (no truncation), (d) `RAG push complete: 2 docs pushed`.
3. **Targeted repair context narrowing**: When provenance repair fires, send only evidence for failing rows instead of full 300KB bundle. Link: unit name in failing row → investigation summary section → narrow context.
2. **koopmans rebuild**: `git pull && sudo docker compose -p koopmans up -d --build --no-deps mcp-server` — applies migrations 013+014+015, reliability layer, and phase-policy changes. Also rebuild dashboard for nginx DNS fix.
2. **EP-005 deploy**: deploy Mission Control to nexus VPS, wire nginx, set `MCP_OPS_TELEMETRY_URL=https://nexus.intodata.nl` on koopmans, remove cloudflared tunnel dependency. Nexus only needs `MC_API_KEY` env var.
3. **EP-005 after deploy**: build static report UI (Jinja2 template per client slug) on top of stored snapshots.
4. Fix git poller TTY / token leak bugs (see PROGRESS_LOG 2026-05-21):
   - Add `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=echo` to `deploy/mcp-server@.service` env
   - Clean up token-injected remote URL after fetch in both `_fetch_remote` and `_run_diff_patch`
   - Call `registry.update_rag_push()` in `_run_generate_background` after a successful RAG push
5. Commit the enterprise gate changes and migration `013_add_generate_run_enterprise_verdict.py`.
6. EP-001: validate runbook on a clean reference VPS (US-002, still the main blocker).

## Blockers

- EP-001 US-002: requires a clean reference VPS. No code blocker.

## Active Work Areas

| Area | Status |
|------|--------|
| Integration Suite Reliability Layer | Complete -- 508 tests pass |
| EP-005 Mission Control | In Progress -- receiver built, tunnel test passed, deploy pending |
| Enterprise documentation gates | Complete, uncommitted |
| Harness reliability (phases 1–6) | Complete, uncommitted |
| EP-001 deployment | In Progress -- VPS blocked |
| Dashboard | Stable |
| RAG pipeline | Stable |
