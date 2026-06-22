# KB Sub-project Context

## Last Completed (2026-06-19) -- Reingestion Reliability Slice 5 (uncommitted)

Slice 5 end-to-end smoke path and minimal operator visibility is implemented,
inspected, and validated for MCP server API/webhook behavior. New
`GET /mcp/projects/{project_id}/reingestion-status` returns a compact read-only
status surface covering project status, webhook registration and last
observation, last update action/outcome/reason/error, RAG/regeneration result,
last ingested SHA, remote hygiene, and cache/rebaseline readiness. The local
smoke helper `scripts/smoke_reingestion_webhook.py` posts GitHub-style ping/push
payloads, signs with GitHub's standard `X-Hub-Signature-256` header, redacts
secrets in dry-run output, and fetches status using the existing
`access_token` cookie auth contract. Inspection found and fixed the pre-existing
non-standard signature header issue that would reject real signed GitHub
deliveries. Validation passed: py_compile on touched Slice 5 API/schema/test
files and script, smoke script `--help`, smoke script `--dry-run`, focused MCP
API tests (`42 passed`), full `mcp-server` pytest (`658 passed`), and
`git diff --check`.

## Previous (2026-06-19) -- Reingestion Reliability Slice 4 (uncommitted)

Slice 4 cache-aware targeted regeneration and safe rebaseline behavior is
implemented, inspected, and validated for MCP server services/API. Reingestion
readiness now distinguishes visible docs, published
`docs/_rag_support/phase2_analysis`, and restored `analysis_cache`, including
analysis counts, cache/support hash match, `targeted_regen_ready`,
`requires_full_rebaseline`, and reason codes. Targeted regeneration preflight now
stops before generation when baseline/docs/support/cache/changed groups are not
usable and records `reason_code: full_rebaseline_required` with an expensive
fallback marker. Incremental staging returns a structured result instead of
silently doing nothing. Readiness repair remains credential-hygiene only and
does not scan, generate, patch, or sync RAG. Inspection fixed changed-group
normalization and origin/repo mismatch readiness. Validation passed: py_compile
on touched Slice 4 files, focused MCP tests (`66 passed`), full `mcp-server`
pytest (`656 passed`), and `git diff --check`.

## Previous (2026-06-18) -- Reingestion Reliability Slice 3 (uncommitted)

Slice 3 patch-first decision summaries and SHA/RAG success semantics are
implemented and validated for MCP server services. Git update summaries now keep
legacy internal `action` values for compatibility while persisting normalized
operator-facing `outcome`, `reason_code`, and `decision_strategy` fields. The
normalized outcome vocabulary is `skip`, `bounded_patch`, `targeted_regen`,
`full_regen`, `manual_review`, and `failed`. Bounded patch success now requires
the expected patched docs to be pushed to RAG; RAG errors or zero-pushed required
docs persist `outcome: failed`, `reason_code: rag_sync_failed`, restore docs, and
preserve the previous baseline SHA. Manual review and missing-baseline outcomes
also preserve SHA. Targeted regeneration summaries record `regen_result`, and
`run_git_reingestion()` now fills the `after_sha` baseline only after background
generation leaves the project `ready` with no `error_message`, avoiding a
conflicting second write when generation already recorded it. Inspection also
fixed sanitized Git stderr handling and credential-safe skip/no-op pulls.
Validation passed: py_compile on touched Slice 3 files, focused service pytest
(`46 passed`), and full `mcp-server` pytest (`646 passed`).

## Previous (2026-06-18) -- Reingestion Reliability Slice 2 (uncommitted)

Slice 2 webhook receipt, matching, and setup observability is implemented and
validated. Webhook project/plugin-pack matching now uses canonical sanitized URL
keys from `git_remote_service.py`, so clean HTTPS URLs, token-sanitized stored
URLs, and trailing `.git` differences match correctly. The webhook route records
durable `git_update_summary.webhook` evidence for matched `ping`, `push`, and
`pull_request` deliveries before background work starts, including sanitized repo
URL, canonical repo key, delivery ID, event, status, observations, and push SHAs.
Unsupported events and invalid push SHAs record reason codes for matched projects;
unregistered repos return sanitized no-op responses and schedule no background
project work. Registry summary writes preserve the webhook subdocument across
later patch and remote-hygiene summary updates. `deploy/WEBHOOK-SETUP.md` now
documents observable metadata and troubleshooting reason codes. Slice 2 inspection
verdict is PASS. Validation passed: focused webhook/project/registry tests
(`53 passed`) and full `mcp-server` pytest (`641 passed`).

## Previous (2026-06-18) — Reingestion Reliability Slice 1 (uncommitted)

Slice 1 credential-safe Git operations and repair/report path is implemented for
MCP server. New `services/git_remote_service.py` centralizes credential stripping,
temporary authenticated remote use, clean-origin restoration in `finally`,
non-interactive Git env, readiness classification, and local remote hygiene repair.
Poller, diff patcher, full reingestion, and auto-doc inspection/skip now use the
central helpers. Git clone registration stores sanitized repo URLs and resets
`origin` to the sanitized URL after clone. New project endpoints report and repair
reingestion readiness:
`GET /mcp/projects/{project_id}/reingestion-readiness` and
`POST /mcp/projects/{project_id}/reingestion-readiness/repair`. Reports classify
clean, tokenized, auth-failing, missing-baseline, and missing-cache states without
raw credentials. The repair path is side-effect limited to remote/DB URL hygiene
plus sanitized metadata summaries; it does not scan, generate, patch, reingest,
advance baseline SHAs, or sync RAG. Slice 1 inspection verdict is PASS. Validation
passed: py_compile on touched Python files, 86 focused MCP tests covering git
remote service, project registry, poller, diff patcher, reingestion, auto-doc
update, project routes, and webhook routes, plus full `mcp-server` pytest
(`635 passed`).

## Previous (2026-06-12) — Layered RAG Slice 3 (commit `39895b1`)

Internal-layer ingestion push + entity index shipped across both sub-projects. `RagBridge.sync_internal_layers()` runs after generation and on `/push-to-rag`: wholesale delete-and-repush of `_internal/{layer}/...` docs (technical_analysis, support_maps, scorecard, evidence_slices) with `source_layer`/`visibility: internal` metadata; `_facts` CSVs feed the new `entity_aliases` table (migration 028, transactional per-project replace endpoint). Default retrieval stays canonical-only. Inspector WARN: LIKE-escaping fixed in-session; internal docs appearing in admin document lists is an open follow-up. 353 + 627 tests pass. Also today: Phase 3 LLM-preamble strip fix (`53914ae`, 112/169 historical outputs affected) and Slice 2 (`fc3788a`). Next: Slice 4 retrieval ladder; deploy note — backend needs `alembic upgrade head` (028) on rollout, and existing projects need a re-push to populate internal layers.

## Previous (2026-06-12) — Layered RAG Slice 2 (commit `fc3788a`)

Harness `_rag_support/` persistence and analysis_cache restore shipped. New service `mcp-server/src/mcp_server/services/rag_support_package.py`: Phase 4 packages Phase 2 group analyses (facts-manifest allowlist only) into `docs_staging/_rag_support/{phase2_analysis/,manifest.json}` before deleting `_analysis/`; the atomic swap carries it into `docs/`. The dead post-swap `docs/_analysis` copy in `_run_generate_background` is replaced by `replace_analysis_cache_from_rag_support()` — flat `analysis_cache/*.md` restore; absent/empty package removes stale cache (full-regen fallback). RagBridge still pushes only top-level visible docs (regression-tested). Inspector verdict WARN, all code findings fixed in-session; 613 tests pass. Slice 3 (internal-layer ingestion into RAG) is next.

## Previous (2026-06-10) — Track A COMPLETE (Slices 1-5)

Track A artifact reduction is **shipped**: Slices 1+2 committed in `5e16911`; Slices 3-5 built+inspected+committed in `1a62a82` and pushed to `master`. Default runs now produce ~290 files / ~20 MB (down from 666 / 52 MB). New `transient_artifacts.py` (un-gated mirror of `debug_artifacts.py`) consolidates 5 transient dirs to one `_transient/`; 9 never-read root JSON maps no longer written; `_audit/` reduced from wholesale copies of 3 dirs + 3 files to just `enterprise_gates.json` + `final_doc_hashes.json`; `CLAUDE.md` and `mcp-workflow.md` updated; 590 tests pass. LLM input is byte-identical (the removed artifacts were never sent to the model). Docker: `mcp-server` rebuilt and healthy on port 8002. Inspection verdicts: Slice 4 WARN, Slice 5 WARN (both non-blocking, both documented). Follow-ups logged in PROGRESS_LOG: silent `try/except OSError` in hash compute (more visible now); pre-existing `JSONDecodeError` ordering in REST fallback; manual paired-run acceptance on reference project; Track B (Phase 3 input quality) is unblocked and ready to start.

## Last Completed (2026-06-10) — Documentation Evidence Depth + Repair Quality Slices 1-5

Documentation Evidence Depth + Repair Quality Slices 1-5 are built and inspected. Slices 1-3 added source completeness auditing and the deterministic source-fact ledger. Slice 4 added evidence-retention reporting to compare source facts against analysis/docs and identify facts lost before the chatbot can answer. Slice 5 replaced broad/form-driven repair behavior with evidence-gated repair planning: `repair_planner.py` classifies syntactic Phase 2 failures, applies deterministic marker/`GROUP_SUMMARY` patches only when safe, keeps substantive missing sections blocking, and routes Phase 3 repair through source-grounded retention packets capped at 50k tokens. Inspection verdict: PASS after fixing Phase 3 packet usage and avoiding deterministic shells for substantive Talend sections. Formal `/validate` is intentionally deferred until all planned slices are complete.

## Previous (2026-06-02)

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

- **Track A (artifact reduction)**: COMPLETE. Slices 1-5 shipped. Default run budget: ~290 files / ~20 MB (was 666 / 52 MB). 590 tests pass. `mcp-server` Docker image rebuilt and healthy. Commits `5e16911` (Slices 1+2) and `1a62a82` (Slices 3-5) on `master`.
- **Track B (Phase 3 input quality)**: unblocked. Per Track B PRD, FR-8 dead-fallback removal (Track A prerequisite) is committed. Track B needs the full `/grill` → `/prd` → `/slices` → `/plan` → `/plan-approve pass` → build → `/inspect` → commit cycle. Output-affecting (per-call manifest.json input hashes WILL change); before/after human spot-check on >=3 docs is the quality gate.
- **Track A follow-ups** (logged in PROGRESS_LOG, not blocking):
  1. `_write_audit_bundle` hash compute silently swallows `OSError` — surface via warning log
  2. `_load_enterprise_gate_results` `_assessment/`→`_audit/` fallback short-circuits on `_assessment/` JSONDecodeError — harden
  3. Paired-run acceptance on reference project (≤300 files, ≤20 MB, per-call manifest invariance) — manual, requires scanned project + working model
- Documentation evidence improvement work is in progress. Slices 1-5 are built and have focused smoke checks, but full validation and commit are deferred until all slices are complete.
- New untracked slice files must be added before commit: `knowledge_base/mcp-server/src/mcp_server/services/doc_evidence_auditor.py`, `knowledge_base/mcp-server/tests/test_services/test_doc_evidence_auditor.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/source_fact_ledger.py`, `knowledge_base/mcp-server/tests/test_providers/test_source_fact_ledger.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/evidence_retention.py`, `knowledge_base/mcp-server/tests/test_providers/test_evidence_retention.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/repair_planner.py`, `knowledge_base/mcp-server/tests/test_providers/test_repair_planner.py`.
- Harness A-grade refactor complete. 527 tests pass. Pipeline is now composable: six `Phase` classes, `PipelineRunner`, data-driven doc catalog, file-editable prompt templates, per-phase pack model/concurrency overrides, evidence traceability validator, and dry-run estimate endpoint.
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

1. **Reingestion Reliability commit-readiness**: Slices 1-5 are built,
   inspected, and validated together (`uv run pytest -q` -> 658 passed). Keep
   unrelated dirty files separate. No commit was made in this session.
2. **Reingestion Reliability release smoke**: run a live GitHub delivery or the
   local smoke helper against a running MCP server and a real registered project:
   `python3 scripts/smoke_reingestion_webhook.py --base-url <url> --project-id <id> --repo-url <repo> --secret "$MCP_GITHUB_WEBHOOK_SECRET"`.
3. **Layered RAG Slice 4**: retrieval ladder routing across layers (canonical -> internal fallback), insufficiency gate, doc-gap logging. Slice 3 follow-ups to fold in: exclude `_internal/` docs from admin document-list endpoints; smoke-test internal sync end-to-end on the local stack (`docker-compose up`, re-push the `phase2` project, verify `_internal/` docs + entity_aliases rows + canonical-only default answers); run `alembic upgrade head` (028) on any deployed backend.
4. **Run Track B full pipeline** (`/grill` -> `/prd` -> `/slices` -> `/plan` -> `/plan-approve pass` -> build -> `/inspect` -> commit). Track A FR-8 dead-fallback removal is the prerequisite and is committed. Per Track B PRD: output-affecting, before/after human spot-check on >=3 docs required before commit is accepted. Target: average Phase 3 context <=150 KB per doc (down from ~400 KB); packs for docs with disjoint `phase2_sections` differ by >=30% by byte count.
5. **Optional: paired-run acceptance on reference project** for Track A. Verify staging file count, total bytes, and per-call `_model_inputs/**/manifest.json` invariance against a recorded model-response baseline. Manual step; requires a scanned project + working model + ~10-30 min runtime.
6. **Smoke-test estimate endpoint**: `curl -s http://localhost:8001/mcp/projects/{project_id}/generate/estimate` — confirm 7 phases returned (EvidenceQualityGate should appear with calls_min=0, calls_max=0).
3. **Validate DAG mode on a small project**: Run with `MCP_AGENT_DAG_ENABLED=true`, `MCP_AGENT_PLANNER_ENABLED=false`. Confirm deterministic fallback graph mirrors current behavior, `_plans/*.json` files are written, final gates still pass, and no Phase 2 core tool calls happen unless `phase2.deep_dive` is selected.
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
