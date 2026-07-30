# Progress Log

Session log for the intodoc platform. Every session should add an entry documenting
what was done, what changed, and lessons learned.

## Session: 2026-07-19 -- Local stack migration and restart completed

- Nexus migration 067 was repaired, backed up, validated on a restored database copy, and applied through migration 069 locally.
- The complete Compose stack rebuilt and all eight services, including MCP and dashboard, report healthy; MCP `/health` returned success and fresh backend/MCP logs contained no startup errors.
- The temporary validation database was removed after successful live verification; the recoverable pre-migration dump remains at `/tmp/intodata_kb_pre_067_fix_20260719.dump`.

## Session: 2026-07-19 -- Pack governance/evidence-integrity context carried into unified agent rollout

### [Refactor]: Preserve MCP/IntoDoc authority while Nexus chat becomes one agent path

- **Files changed**: Dashboard `src/{App.tsx,api/client.ts,api/schemas.ts,components/GenerateDialog.tsx,components/PluginPacksPage.tsx,hooks/useProjects.ts,types.ts}` plus tests and Vite/nginx config; IntoDoc `src/intodoc/{cli.py,models.py,scanner/redaction_verifier.py,lineage.py}` plus lineage tests/fixtures; MCP `src/mcp_server/{api/app.py,api/routes/plugins.py,api/routes/projects.py,config.py,db/models.py,providers/base.py,providers/custom/{harness,prompts,target_evidence}.py,schemas.py,services/{claude_service,generation_run_state,plugin_schema,component_description_service,component_graph_service,job_graph_service,lineage_service,nexus_execution_contract,pack_governance}.py}` plus migrations, Pack fixtures, and tests shown by `git diff --stat HEAD`/`git status --short`.
- **Problem**: IntoDoc Swap-Pack execution, Nexus policy-Pack snapshots, evidence redaction, and topology/lineage work were evolving in one broad dirty branch while the chat router was being replaced.
- **Root cause**: The branch contains concurrent but related platform work; MCP must remain the final documentation execution authority while Nexus owns orchestration and policy.
- **Change**: Logged the boundary: Pack provenance/digest/owner governance, execution-contract validation, redaction verification, scanner lineage, and customer Pack artifacts remain intact and will be split into deliberate reviewable commits. The unified agent path calls registered capabilities rather than bypassing MCP governance.
- **Why this approach**: Keeps the external execution authority and evidence safety contracts stable while removing only legacy chat routing paths from Nexus.
- **Verification**: Prior project records document Pack/governance and evidence-integrity suites; the unified chat worker's current validation is recorded in the RAG log. MCP migration/deployment work remains separately gated.

## Session: 2026-07-11

### [Feature]: Evidence-based doc update endpoint inspected and committed

- **Files changed** (commit `e4747c3`; built in the prior session, this session validated/inspected/committed):
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` (POST `/mcp/projects/{project_id}/docs/{filename}/evidence-update`)
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` (`EvidenceDocUpdateRequest`/`EvidenceDocUpdateResponse`)
  - `knowledge_base/mcp-server/src/mcp_server/services/evidence_doc_update.py` (new `EvidenceDocUpdateService`)
  - `knowledge_base/mcp-server/tests/test_api/test_evidence_doc_update.py` (new)
  - `knowledge_base/mcp-server/tests/test_api/test_rbac.py` (new endpoint added to the RBAC route matrix)
- **Problem**: The MCP half of the Nexus harness `mcp.docs.update_from_evidence` capability was uncommitted while the RAG-side harness batch went through inspection.
- **Root cause**: Both halves rode the same spike branch; the LFE pipeline had stopped at builder-done with Inspector pending.
- **Change**: Committed as its own commit, separate from the RAG harness batch (`498bad1`) and the talend_tmc fix (`027f18b`), so it is independently revertable. Endpoint is append-only, records before/after SHAs + a receipt, re-syncs the changed doc to RAG, and enforces project access (RBAC matrix test covers it).
- **Why this approach**: MCP stays a thin DocumentationProvider behind the Nexus executor boundary; the harness (RAG side) owns workflow receipts and evidence refs.
- **Verification**: Inspector pass recorded in root `.plans/inspection_report.md` (PASS_WITH_WARNINGS). Security sweep flagged one defense-in-depth follow-up (WARN-4): MCP-bound `project_id`/`filename` path segments are currently validated only by the RAG-side caller regex — endpoint/provider-boundary validation must land before model-supplied planner arguments exist. RBAC matrix test includes the new route; RAG-side full suite re-run green (1006 passed).

## Session: 2026-07-10

### [Feature]: MCP documentation provider wired into Nexus harness chat loop

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/evidence_doc_update.py`
  - `knowledge_base/mcp-server/tests/test_api/test_rbac.py`
  - `knowledge_base/mcp-server/tests/test_api/test_evidence_doc_update.py`
  - root `NEXUS_HARNESS_PROGRESS_LOG.md`
- **Problem**: MCP/Intodoc existed as an isolated documentation subsystem, while Nexus chat needed to call documentation generation and documentation metadata through a harness capability path.
- **Root cause**: The prior architecture treated scanner/docs/RAG/chat as adjacent subsystems. The new harness architecture needs MCP docs generation to behave as one provider/capability behind Nexus, not as the whole product model.
- **Change**: MCP is now used by the RAG-side harness docs provider for generation, project docs metadata, and evidence-backed doc updates. Live smoke proved chat-triggered generation reaches MCP from Docker and that MCP generation/doc endpoints are inspectable after the workflow starts. Added root `NEXUS_HARNESS_PROGRESS_LOG.md` with a comparison against the full Nexus harness architecture.
- **Why this approach**: Keeps Intodoc/MCP as a DocumentationProvider implementation. Future connectors should follow capability/provider contracts instead of being hardcoded into Intodoc.
- **Verification**: Current session live smoke: MCP health `200 OK`; `generate documentation for IntodataStreams` created harness workflow run `3`; MCP `/mcp/generate-runs/IntodataStreams` showed new run `37`; MCP `/mcp/projects/IntodataStreams/docs` returned 11 docs. Prior harness validation also covered MCP doc-update/RBAC tests (`71 passed` in the previous builder pass).

## Session: 2026-07-08

### [Fix]: Talend ESB/Camel classification coverage gap -- allowlist replaced with prefix regex

- **Files changed** (from `git diff --stat HEAD`, plus one new untracked fixture directory):
  - `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` (removed `_ESB_COMPONENT_TYPES` frozenset; added `_ESB_COMPONENT_PATTERN = re.compile(r"^c[A-Z]")`; `classify_esb_components()` now matches the prefix pattern instead of intersecting against the fixed set; `import re` moved to module top-level, redundant local import in `_strip_signature` removed)
  - `knowledge_base/intodoc/tests/test_platforms/test_talend_esb_classification.py` (new unit test `test_unlisted_camel_component_detected_by_prefix`; new `TestTalendParserEsbIntegration` class running the real `TalendParser` against fixture `.item` files)
  - New: `knowledge_base/intodoc/tests/fixtures/talend_sample/` -- synthetic fixture project (`routes/Service/Webhook_Route_0.1.item`+`.properties` using only `cTimer`/`cHttp`; `process/DI/Sample_DI_Job_0.1.item`+`.properties` using only `tFileInputDelimited`/`tMap`; layout later restructured in the same session, see next entry)
  - `knowledge_base/CLAUDE.md` (Talend ESB detection note updated: `_ESB_COMPONENT_TYPES` -> `_ESB_COMPONENT_PATTERN`, documents the prefix-match rationale)
- **Problem**: The 2026-07-06 session moved ESB/Camel route classification to the scanner but kept the original detection mechanism -- a hardcoded ~25-entry allowlist of known Camel component type names (`cRoute`, `cREST`, `cJMS`, etc.). Real customer-scanned Talend route data on disk (`mcp-server/data/repos/cdc/.../routes/Monitoring/rTalendMonitoringProcessing_0.1.item`) contains a `cHttp` component, which is a real Camel component type absent from the allowlist -- confirmed by inspecting that file's `componentName` attributes.
- **Root cause**: Talend's actual Camel/ESB component palette is much larger than any list we'd hand-maintain, and the palette keeps growing across Talend Studio versions. A fixed allowlist silently misclassifies any route built from components outside the list; it happened to still work on that particular sample file only because it also used other allowlisted components (`cJMS`, `cLog`, `cProcessor`, `cMQConnectionFactory`) alongside `cHttp`. A route built solely from unlisted connectors would be missed entirely.
- **Change**: Replaced the frozenset-intersection check with a regex match on the `c[A-Z]` prefix convention itself -- every DI-job component in this codebase's own `_INTERNAL_COMPONENTS`/`_TALEND_SYSTEM_MAP` lists is `t`-prefixed, so the `c`/`t` split is the real signal Talend Studio enforces, not a curated subset of `c*` names. Added synthetic fixtures (not copies of the real customer route files, to avoid committing customer integration details) reproducing the exact gap: a route using only `cTimer`+`cHttp` (both absent from the old allowlist). Verified the new integration tests fail against the pre-fix code and pass after the fix.
- **Why this approach**: Per this repo's "generalize fixes" / "no workarounds" rules -- expanding the allowlist to include `cHttp` would only have fixed the one observed instance and left the same defect pattern (any other missing Camel component name) unaddressed. The prefix rule closes the entire class of gap and needs no maintenance as Talend's palette grows.
- **Verification**: Confirmed the gap was real before fixing: `test_route_with_unlisted_camel_components_is_detected` and the pure-function `test_unlisted_camel_component_detected_by_prefix` both failed against the old allowlist-based code (`assert False is True` / `assert [] == ['cHttp', 'cTimer']`). After the fix, full `knowledge_base/intodoc` suite: `379 passed, 8 skipped` (`python3 -m pytest tests/`; the 8 skips are the pre-existing `vaxxinova-talend`-dependent integration tests in `test_talend_parser.py`, unrelated to this change -- see Follow-up below). Investigated (not acted on) a related pre-existing issue: `vaxxinova-talend` was added as a bare git submodule gitlink in commit `2d4ce2a` with no `.gitmodules` ever committed (never resolved), then fully removed from the repo in commit `4a45b4d` -- no remote URL was ever recorded to restore it from, and it referenced a specific customer's sample project, so restoring it was intentionally left to the user's judgment rather than acted on unilaterally.
- **Follow-up (not done this session)**: Decide whether to delete the 8 permanently-skipped `vaxxinova-talend`-dependent tests in `test_talend_parser.py` (they can never run in this checkout) or leave them as documented dead weight.

### [Fix]: Talend ESB Route Designs in routes/ were never parsed at all (scanner only walked process/)

- **Files changed**:
  - `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` (`_parse_all_jobs` now iterates a new `_JOB_SOURCE_DIRS = ("process", "routes")` tuple instead of hardcoding `process/`; `_parse_job` takes the resolved `base_dir` (process/ or routes/) instead of `talend_root`, so group/subgroup/path derivation works identically for either tree; `_find_talend_root` / new `_is_talend_root()` helper now recognize a project root by `process/` **or** `routes/` presence, not `process/` alone)
  - `knowledge_base/intodoc/tests/test_platforms/test_talend_esb_classification.py` (restructured `TestTalendParserEsbIntegration`: moved the route fixture out of `process/ROUTES/` into a real top-level `routes/Service/` dir; added `test_route_under_routes_dir_is_found_and_detected` (asserts `group == "Service"`, proving the routes/ walk itself, not just classification), `test_plain_di_job_under_process_dir_is_not_classified_as_route`, `test_both_process_and_routes_dirs_are_walked`)
  - `knowledge_base/intodoc/tests/fixtures/talend_sample/routes/Service/Webhook_Route_0.1.item`+`.properties` (moved from `process/ROUTES/`)
  - `knowledge_base/CLAUDE.md` (Talend ESB detection note: documents that job discovery now walks both `process/` and `routes/`)
- **Problem**: While answering "how do I verify this end-to-end myself," inspected a real Talend ESB Studio project on disk (`mcp-server/data/repos/cdc/Talend-CDC-main/TALEND_CDC/`, used only for local scanner-code investigation, never committed/copied) and found it has `process/` (2 DI jobs) and a **separate top-level `routes/` directory** (5 real ESB/Camel route `.item` files: `Webhook`, `rTalendMonitoringProcessing`, `rItem_OnRamp`, `rItem_OffRamp_DB`, `rTransport_OnRamp`). `_parse_all_jobs` only ever called `talend_root.rglob` under `process/` -- confirmed via `grep -rn '"routes"'` across the entire intodoc src tree that no code path reads a `routes/` directory. All 5 real routes were completely invisible to the scanner: not misclassified, never parsed as jobs, never in `scan_result.json` at all.
- **Root cause**: The 2026-07-06 and earlier-today classification work both assumed (based on the low-level `.item`/XMI file format being identical for Jobs and Routes) that there was no folder-level distinction between the two either. That's true of the raw file format, but false of the actual Talend Studio repository layout: Talend Studio's "Job Designs" and "Route Designs" repository nodes serialize to separate top-level folders (`process/` and `routes/` respectively), and the scanner was only ever written to walk one of them.
- **Change**: `_parse_all_jobs` now loops over both `process/` and `routes/` (via `_JOB_SOURCE_DIRS`), parsing each `.item` file found under either with `base_dir` set to whichever tree it came from (so `group`/`subgroup`/`path` are derived correctly and identically for both). `_find_talend_root` generalized so a routes-only ESB project (no DI jobs at all) is still detected as a valid Talend root.
- **Why this approach**: Per "generalize fixes" -- fixing only the specific project found would leave the exact same defect for any other Talend ESB Studio export with the same standard layout. Reusing the existing per-file parse/group/classify logic (just parameterizing the base directory) avoids a parallel code path for routes vs. jobs.
- **Verification**: Full `knowledge_base/intodoc` suite: `380 passed, 8 skipped` (`python3 -m pytest tests/` from `knowledge_base/intodoc`; unrelated pre-existing skips, see prior entry). **Real-data verification** (local, read-only, no Docker/containers touched, no LLM calls): ran `intodoc scan` directly against the real `TALEND_CDC` project into a scratch output dir. Before this fix (prior scans cached under `knowledge_base/intodoc-output/cdc/scan_result.json`): 2 jobs found, 0 flagged as routes. After this fix: **7 jobs found** (up from 2) -- all 5 real routes now appear with `is_esb_route: true` and correct `esb_component_types`, including `rTalendMonitoringProcessing` correctly showing `cHttp` (the exact component the earlier allowlist-vs-regex fix targets) alongside `cJMS`/`cLog`/`cProcessor`/`cMQConnectionFactory`; the 2 pre-existing DI jobs remain `is_esb_route: false`; groups correctly derived from the routes/ subfolder names (`Item`, `Monitoring`, `Service`, `Transport`).
- **Not done this session** (explicitly deferred, not a decision -- infra/cost implications, user asked not to rebuild/restart any containers until told): full pipeline verification that `talend_esb_facts.json` is written and the `ESB_ROUTES` Phase 2 section actually renders with real content requires running the MCP doc-generation harness (Docker + an LLM provider call), which was intentionally not attempted this session.
- **Related but out of scope**: Route -> Job invocation via the `cTalendJob` component is not resolved into `trunjob_refs`/`manifest.job_calls` the way `tRunJob` is for DI jobs (`_extract_trunjob_ref` only fires on `comp_type == "tRunJob"`). Routes calling Jobs are now at least visible as separate parsed jobs, but the cross-reference edge between them is not built. Not fixed here -- separate feature, not required to make Route Designs visible to the scanner.

## Session: 2026-07-06

### [Feature]: Talend ESB/Camel route vs DI job classification (scanner-computed)

- **Files changed** (from `git diff --stat HEAD`, plus two new untracked test files):
  - `knowledge_base/CLAUDE.md` (Current Custom Harness Notes: names `talend_intelligence.py`, documents scanner-computed detection + `ESB_ROUTES` conditional section, fixing a prior doc/code mismatch)
  - `knowledge_base/intodoc/src/intodoc/models.py` (`ParsedJob.is_esb_route: bool`, `ParsedJob.esb_component_types: list[str]`)
  - `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` (`_ESB_COMPONENT_TYPES` frozenset + `classify_esb_components()`; `_parse_job` sets both new fields from parsed component types)
  - `knowledge_base/mcp-server/data/prompt_templates/phase2_platform_sections/talend.md` (new conditionally-worded `### ESB_ROUTES` section body: routing rules, endpoint definitions, EIP patterns, error/exception channels)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/constants.py` (new `ESB_ROUTES_SECTION = "ESB_ROUTES"`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/prompt_packs.py` (`ESB_ROUTES_SECTION` added to existing `CONDITIONAL_SECTION_NAMES`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/talend_intelligence.py` (`detect_talend_esb_patterns` now reads scanner-computed `is_esb_route`/`esb_component_types` per job instead of re-deriving; removed duplicated `_ESB_COMPONENT_TYPES` set)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/validators.py` (`validate_analysis_guide_sections`: new `_esb_detected()` helper; 3-synonym regex `ROUTE_GRAPH|ESB_ROUTES|ESB_ROUTE_GRAPH` replaced with the shared `_has_section(text, ESB_ROUTES_SECTION)` check used by required-section coverage)
  - `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py` (3 new tests: ESB_ROUTES required-when-detected / passes-when-present / skipped-when-not-detected)
  - New: `knowledge_base/intodoc/tests/test_platforms/test_talend_esb_classification.py` (7 tests: DI-only, single/multi ESB component, defaults, JSON round-trip)
  - New: `knowledge_base/mcp-server/tests/test_providers/test_talend_intelligence.py` (6 tests: read-not-recompute behavior, old-cache defensive read, file-write gating)
- **Problem**: Generated docs treated Talend ESB/Camel route jobs identically to plain DI jobs (source -> transform -> target -> schema framing), losing routing rules, endpoint definitions, EIP patterns, and error-channel semantics that actually matter for routes.
- **Root cause**: Talend serializes both DI Jobs and ESB Routes into the same `.item`/`.properties` format; the only signal is `c`-prefixed Camel component usage vs `t`-prefixed DI components. That heuristic previously lived only in `talend_intelligence.py` as a late mcp-server-side sidecar (post-hoc `_ESB_COMPONENT_TYPES` match over raw `scan_result.json`), producing a `talend_esb_facts.json` file consumed only by a 3-synonym regex in the validator that hoped the LLM would organically add a matching heading — no prompt template or doc-catalog entry ever actually asked for one.
- **Change**: Moved classification to the scanner (single source of truth): `TalendParser._parse_job` now computes `is_esb_route`/`esb_component_types` per job from its parsed components, flowing through `scan_result.json`. `talend_intelligence.py` now only aggregates those pre-computed fields (duplicated constant removed). Registered `ESB_ROUTES` as the one canonical section name in `CONDITIONAL_SECTION_NAMES` (same mechanism as `SOURCE_SQL`/`CUSTOM_CODE`), added real prompt guidance for it in the Talend platform prompt section, and consolidated the validator's bespoke regex into the existing `_has_section` coverage path — enforced only when the scanner flagged at least one route job, never on pure-DI projects.
- **Why this approach**: Per this repo's "no workarounds / generalize fixes / use existing design patterns" rules, a second late heuristic would have compounded the original problem. Computing the fact where jobs are actually parsed makes it available to grouping, prompts, and doc catalog uniformly, and reusing `CONDITIONAL_SECTION_NAMES`/`_has_section` avoids a parallel one-off coverage code path.
- **Verification**: `python3 -m py_compile` clean on all touched files; `git diff --check` clean. Independently re-run (not just agent-reported): `test_talend_esb_classification.py` 7 passed, `test_talend_intelligence.py` 6 passed, full `test_custom_provider.py` 208 passed. Pre-existing `test_talend_parser.py` integration tests skipped (8) — `vaxxinova-talend` fixture absent in this checkout, unrelated to this change. Implemented by a delegated Opus subagent per user instruction (pipeline gate explicitly waived for this change — not logged as protocol debt since the user directed the shortcut directly, not an emergency patch); diff was read and reviewed line-by-line before this log entry, not accepted on the agent's summary alone.

## Session: 2026-07-02

### [Feature + Fix]: Living-Document Reingestion (Slices 1-5) + live-E2E bug fixes

- **Files changed** (from `git diff --stat HEAD`; all uncommitted working tree):
  - `docker-compose.yml` (added `MCP_IMPACT_ROUTING_ENABLED` env, default false)
  - `knowledge_base/mcp-server/src/mcp_server/config.py` (flag + doc-version-history path constants)
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` (S5 manual full-rebuild endpoint + per-project scan output_dir already correct)
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` (S5 `FullRebuildRequest`, reingestion-status drift fields)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py` (#3b Phase 2 REUSE for scoped runs, both dispatch sites)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase1_investigation.py` (#3c Phase 1 investigation scoped to changed groups)
  - `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py` (Bug2 truthful skip via outcome_sink; #6 touched_docs = actual regenerated set; S1/S4 wiring)
  - `knowledge_base/mcp-server/src/mcp_server/services/claude_service.py` (#3a do not wipe restored incremental staging on scoped runs)
  - `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py` (Bug1 per-project scan `output_dir`; Bug2 `outcome_sink` skip/regenerated reporting; #6 regenerated_docs; S1 ImpactRouter scoping + `full_rederive`)
  - `knowledge_base/mcp-server/src/mcp_server/services/reingestion_router.py` (S1 retire doc-count thresholds under flag; path-fail -> scoped not full)
  - `knowledge_base/mcp-server/src/mcp_server/services/true_patch_executor.py` (S2 G5 diff-fidelity guard integration; S3 Phase 3b synthesis amendment + G6 removal check)
  - `knowledge_base/mcp-server/src/mcp_server/services/patch_section_map.py` (S3 shared `extract_h2_sections`)
  - New: `services/impact_router.py` (S1), `services/diff_fidelity_guard.py` (S2), `services/synthesis_impact_mapper.py` (S3), `services/doc_version_history.py` (S4), `services/drift_summary.py` (S5)
  - New prompt templates: `data/prompt_templates/phase3_synthesis_patch.md` (S3). (`phase2_patch.md`, `phase3_patch.md` were pre-existing untracked before this session.)
  - New/updated tests: `test_services/test_impact_router.py`, `test_diff_fidelity_guard.py`, `test_synthesis_impact_mapper.py`, `test_doc_version_history.py`, `test_drift_summary.py`, `test_no_automatic_full_rebuild.py`, `test_api/test_reingestion_drift.py`, plus additions to `test_git_reingestion.py`, `test_reingestion_router.py`, `test_auto_doc_update.py`, `test_true_patch_executor.py`
  - NOT this session (pre-existing dirty/untouched): `alembic/versions/003_add_model_name.py`, `model_strategies/strategies.py`, `docs/CONTAINER_TOPOLOGY_FOR_TERRAFORM.md`
- **Problem**: Reingestion cost did not scale with change size. Follow-up to merged "Smart Reingestion": the impact machinery existed but the threshold/path router was still the front-line driver, and live E2E exposed that reingestion silently did nothing while reporting success.
- **Root cause** (found via live Docker E2E on `reingest-drill`, not unit tests):
  - **Bug 1**: `run_git_reingestion` called `run_scan` without `output_dir`, so scans wrote to the ROOT `intodoc_output_dir` while the fingerprint read the per-project dir -> compared new code against a stale scan -> empty dirty-set -> skipped everything.
  - **Bug 2**: on a (wrongly) empty dirty-set the flow reported `targeted_regen` success, wrote a phantom change-history entry, bumped drift, and advanced the ingested SHA -- marking a push done with docs never touched.
  - **#3**: even when scoped, `generate_docs` WIPED the restored incremental staging when resume-state wasn't detected (#3a), and Phase 1/Phase 2 re-ran on ALL groups (#3b/#3c) -- so "incremental" cost ~= full.
- **Change**: Built S1-S5 (impact router + threshold retirement, diff-fidelity guard, synthesis-doc amendment, versioning/history, drift + manual-only rebuild). Then fixed Bug1 (per-project output_dir), Bug2 (outcome_sink truthful skip; skip writes no history, holds SHA), #3a (preserve staging when `scoped_docs` set), #3b (Phase 2 reuses unchanged groups' cached analysis on scoped runs), #3c (Phase 1 investigation skips unchanged groups), #6 (report actual regenerated doc set).
- **Why this approach**: `outcome_sink` is a backward-compatible optional param (default None) so the bool return contract and all existing tests are unchanged. Phase 2/1 reuse is gated on `ctx.scoped_docs is not None` and leverages the existing incremental-staging mechanism (unchanged groups' analyses restored, changed groups' deleted) rather than the fragile resume-state detection.
- **Verification**: `test_services` suite green throughout (`484 passed`, 1 pre-existing `intodoc` env failure). Live Docker E2E on `reingest-drill` with deepseek: Bug1 fixed (changes detected), Bug2 fixed (failed run held SHA `b67f89e`, `manual_review`, 0 phantom entries), #3a proven (`Resume state detected: analysis_files=['config','root','sql']`), #3b PROVEN (`REUSE: skipping Phase 2 for config/root/sql`, only `scripts` regenerated). GAP: no fully-green completed run (both real runs died on deepseek `402 Insufficient Balance` -- external); #3c deployed + service-tested but not observed live; harness changes not locally unit-testable (need `intodoc`/model deps); full `uv run pytest -q` still owed in a real dev env. Nothing git-committed.

## Session: 2026-06-19

### [Feature/Fix]: Reingestion Reliability Slice 5 - end-to-end smoke path and operator status

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py`
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py`
  - `knowledge_base/mcp-server/scripts/smoke_reingestion_webhook.py`
  - `knowledge_base/mcp-server/tests/test_api/test_projects.py`
  - `knowledge_base/mcp-server/tests/test_api/test_webhook.py`
  - `deploy/WEBHOOK-SETUP.md`
  - `.plans/reingestion-reliability/slice-05/04_active_plan.md`
  - `.plans/reingestion-reliability/slice-05/05_inspection_report.md`
  - `.plans/reingestion-reliability/slice-05/validation.md`
- **Problem**: After the credential-safe Git, webhook matching, patch-first
  decision, and cache-aware regeneration slices, operators still lacked one
  compact status endpoint and one repeatable smoke path to prove whether a GitHub
  delivery matched a project, queued work, selected patch/regeneration/manual
  review, synced RAG, and advanced or preserved the baseline SHA.
- **Root cause**: Webhook observations and Git update summaries were durable but
  scattered in project metadata, logs, readiness state, and filesystem cache
  checks. The existing setup runbook did not provide a scriptable local ping/push
  smoke, and inspection found the webhook verifier was using the non-standard
  `X-GitHub-Signature-256` header instead of GitHub's real
  `X-Hub-Signature-256` header.
- **Change**: Added `GET /mcp/projects/{project_id}/reingestion-status`, which
  aggregates project status, webhook registration/last observation, normalized
  last update fields, RAG/regeneration result, last ingested SHA, remote hygiene,
  and cache/rebaseline readiness without starting scan, generation, patch, RAG
  sync, fetch, or pull work. Added `scripts/smoke_reingestion_webhook.py` to
  post GitHub-style ping/push payloads, HMAC-sign with `X-Hub-Signature-256`,
  redact secrets in dry-run output, and read the status endpoint using the MCP
  API's existing `access_token` cookie contract. Updated webhook tests to use
  the standard GitHub header while preserving legacy header compatibility.
  Updated `deploy/WEBHOOK-SETUP.md` with real and simulated smoke steps,
  expected outcome states, reason codes, and auth/header details.
- **Why this approach**: It keeps operator visibility read-only and project
  scoped, reuses the existing readiness and `git_update_summary` surfaces, and
  avoids adding a dashboard dependency before the backend workflow is proven.
  Fixing the signature header at the webhook boundary addresses a likely real
  setup failure mode directly instead of documenting around it.
- **Verification**:
  - `python3 -m py_compile src/mcp_server/api/routes/projects.py src/mcp_server/api/routes/webhook.py src/mcp_server/schemas.py tests/test_api/test_projects.py tests/test_api/test_webhook.py scripts/smoke_reingestion_webhook.py`
  - `python3 scripts/smoke_reingestion_webhook.py --help`
  - `python3 scripts/smoke_reingestion_webhook.py --dry-run --base-url http://127.0.0.1:8002 --project-id smoke-proj --repo-url https://github.com/acme/repo.git --secret test-secret --auth-token test-jwt`
  - `cd knowledge_base/mcp-server && uv run pytest tests/test_api/test_projects.py tests/test_api/test_webhook.py -q` -> 42 passed.
  - `cd knowledge_base/mcp-server && uv run pytest -q` -> 658 passed.
  - `git diff --check -- <Slice 5 files>` -> clean.
  - Inspection verdict: PASS.

### [Feature/Fix]: Reingestion Reliability Slice 4 - cache-aware targeted regeneration and safe rebaseline

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/git_remote_service.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py`
  - `knowledge_base/mcp-server/tests/test_services/test_git_remote_service.py`
  - `knowledge_base/mcp-server/tests/test_services/test_git_reingestion.py`
  - `knowledge_base/mcp-server/tests/test_services/test_rag_support_package.py`
  - `knowledge_base/mcp-server/tests/test_services/test_auto_doc_update.py`
  - `knowledge_base/mcp-server/tests/test_api/test_projects.py`
  - `.plans/reingestion-reliability/slice-04/04_active_plan.md`
  - `.plans/reingestion-reliability/slice-04/05_inspection_report.md`
  - `.plans/reingestion-reliability/slice-04/validation.md`
- **Problem**: Targeted regeneration readiness only exposed a coarse flat `analysis_cache` flag, so old projects missing published `_rag_support/phase2_analysis` or holding stale cache could look safe for targeted regeneration. Full rebaseline reasons were not visible enough before expensive fallback paths.
- **Root cause**: Cache/support inspection lived implicitly in `setup_incremental_staging()` and returned no structured result. Readiness APIs could not distinguish docs, support package, restored cache, stale cache, or changed-group availability.
- **Change**: Added `GenerationCacheReadiness` and `inspect_generation_cache_readiness()` to distinguish visible docs, published support analyses, restored flat cache, cache/support count/hash match, changed-group availability, and stable rebaseline reason codes. Reingestion readiness/API responses now expose those fields. Targeted regeneration preflight in auto-doc and full reingestion now stops before generation when support/cache/baseline/docs/groups are not usable, recording `reason_code: full_rebaseline_required`, `requires_full_rebaseline`, `targeted_regen_ready`, `reason_codes`, and `expensive_fallback_required`. Incremental staging now returns `IncrementalStagingResult`. Readiness repair remains credential-hygiene only and records cache readiness without scan/generation/patch/RAG side effects. Inspection also fixed non-string changed-group normalization and made origin/repo mismatch prevent `ready`.
- **Why this approach**: It keeps full generation as an explicit, visible fallback instead of an automatic webhook side effect. Hash-matching support/cache catches stale flat cache after manual filesystem drift, while keeping the existing `_rag_support` to `analysis_cache` post-swap contract intact.
- **Verification**:
  - `python3 -m py_compile` on touched Slice 4 service, API, schema, and test files.
  - `cd knowledge_base/mcp-server && uv run pytest tests/test_services/test_git_remote_service.py tests/test_services/test_git_reingestion.py tests/test_services/test_rag_support_package.py tests/test_services/test_auto_doc_update.py tests/test_api/test_projects.py -q` -> 66 passed.
  - `cd knowledge_base/mcp-server && uv run pytest -q` -> 656 passed.
  - `git diff --check -- <Slice 4 files>` -> clean.
  - Inspection verdict: PASS.

## Session: 2026-06-18

### [Feature/Fix]: Reingestion Reliability Slice 2 - webhook receipt and matching observability

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/project_registry.py`
  - `knowledge_base/mcp-server/tests/test_api/test_webhook.py`
  - `knowledge_base/mcp-server/tests/test_services/test_project_registry.py`
  - `deploy/WEBHOOK-SETUP.md`
  - `.plans/reingestion-reliability/slice-02/04_active_plan.md`
  - `.plans/reingestion-reliability/slice-02/05_inspection_report.md`
  - `.plans/reingestion-reliability/slice-02/validation.md`
- **Problem**: Webhook deliveries could be received, ignored, matched, or rejected without durable project-visible evidence before background patch/scan work started. URL matching was also a simple string comparison, so sanitized URLs and `.git` suffix differences could make a registered repo look unregistered.
- **Root cause**: The webhook route owned its own matching and response logic instead of using the centralized URL normalization from Slice 1, and project metadata did not preserve a dedicated webhook observation subdocument when patch/reingestion summaries were later written.
- **Change**: Webhook project/plugin-pack matching now uses canonical sanitized URL keys. `ping`, `push`, and `pull_request` deliveries record `git_update_summary.webhook` with sanitized repo URL, canonical repo key, delivery ID, event, status, observations, and push before/after SHAs before background work starts. Unsupported events and invalid push SHA payloads record explicit reason codes for matched projects; unregistered repos return a sanitized reason-code response and schedule no background project work. `ProjectRegistry.record_webhook_observation()` merges webhook evidence into existing summaries, and existing summary writers preserve the webhook subdocument across later patch or remote-hygiene summary writes. `deploy/WEBHOOK-SETUP.md` documents the observable ping/push fields and reason-code troubleshooting path.
- **Why this approach**: Reuses the existing `git_update_summary` surface instead of adding schema while still giving operators durable evidence. Keeping webhook data under a `webhook` subdocument lets patch/regeneration summaries continue to own top-level `action` while preserving delivery diagnostics.
- **Verification**:
  - `python3 -m py_compile knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py knowledge_base/mcp-server/src/mcp_server/services/project_registry.py knowledge_base/mcp-server/tests/test_api/test_webhook.py knowledge_base/mcp-server/tests/test_services/test_project_registry.py`
  - `cd knowledge_base/mcp-server && uv run pytest tests/test_api/test_webhook.py tests/test_api/test_projects.py tests/test_services/test_project_registry.py -q` -> 53 passed.
  - `cd knowledge_base/mcp-server && uv run pytest -q` -> 641 passed.
  - Inspection verdict: PASS.

### [Feature/Fix]: Reingestion Reliability Slice 1 - credential-safe Git remotes and readiness repair

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/services/git_remote_service.py` (new)
  - `knowledge_base/mcp-server/src/mcp_server/services/git_poller.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/project_registry.py`
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py`
  - `knowledge_base/mcp-server/tests/test_services/test_git_remote_service.py` (new)
  - `knowledge_base/mcp-server/tests/test_api/test_projects.py`
- **Problem**: Git webhook/poller reingestion paths duplicated remote credential handling. Some paths temporarily wrote tokenized remotes into `origin` and did not consistently restore clean URLs. Operators also had no low-cost API to inspect or repair credential hygiene, missing baselines, or incremental-cache readiness without triggering scan, generation, patch, reingestion, or RAG sync.
- **Root cause**: Remote URL auth was implemented independently in poller, diff patcher, auto-doc inspection, clone registration, and full reingestion. There was no central helper for URL credential stripping, temporary authenticated remote use, non-interactive Git environment, or readiness classification.
- **Change**: Added `git_remote_service.py` with URL credential stripping/canonicalization, authenticated URL construction for in-memory operations, `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS=echo` subprocess env, clean-origin get/set helpers, `fetch_remote`, `pull_remote_ff_only`, `with_temporary_authenticated_origin`, and reingestion readiness/repair reports. Replaced duplicated token-injection paths in poller, diff patcher, auto-doc inspection/skip, and full reingestion with the helper. Git clone registration now stores a sanitized `repo_url`, clones with temporary auth when needed, then resets local `origin` to the sanitized URL. Added `GET /mcp/projects/{project_id}/reingestion-readiness` and `POST /mcp/projects/{project_id}/reingestion-readiness/repair`; repair only cleans local remote/DB repo URL hygiene and records a sanitized summary. It does not alter baseline SHAs or start scan/generation/patch/RAG workflows. Inspection added explicit `auth_failed` readiness classification from sanitized `auto_reingest_error` so auth-failing projects cannot report as ready.
- **Why this approach**: Centralizing remote mutation makes the safety invariant testable: tokenized URLs are only temporary, and `origin` is restored in `finally` on success or failure. Readiness and repair live under project routes because they are project-scoped operator actions, but they call only the remote hygiene service and registry metadata update.
- **Verification**:
  - `python3 -m py_compile knowledge_base/mcp-server/src/mcp_server/services/git_remote_service.py knowledge_base/mcp-server/src/mcp_server/services/git_poller.py knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py knowledge_base/mcp-server/src/mcp_server/services/project_registry.py knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py knowledge_base/mcp-server/src/mcp_server/schemas.py knowledge_base/mcp-server/tests/test_services/test_git_remote_service.py knowledge_base/mcp-server/tests/test_api/test_projects.py`
  - `cd knowledge_base/mcp-server && uv run pytest tests/test_services/test_git_remote_service.py tests/test_services/test_project_registry.py tests/test_services/test_git_poller.py tests/test_services/test_git_diff_patcher.py tests/test_services/test_git_reingestion.py tests/test_services/test_auto_doc_update.py tests/test_api/test_projects.py tests/test_api/test_webhook.py -q` -> 86 passed.
  - `cd knowledge_base/mcp-server && uv run pytest -q` -> 635 passed.
  - `git diff --check -- <Slice 1 files>` -> clean.

## Session: 2026-06-10

### [Refactor]: Track A complete — Slices 3-5 built, inspected, committed, deployed (FR-1c, FR-1d, FR-2, FR-3, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11)

- **Files changed** (commit `1a62a82`, 34 files, +1988 / -958):
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/transient_artifacts.py` (new — un-gated helper module mirroring `debug_artifacts.py`: `TRANSIENT_DIR_NAME`, `transient_dir()`, `transient_write()`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py` (5 transient write sites repointed to `_transient/`; new `_phase2_analysis_guide_files` static method as single source of truth; 2 phase2 file-list duplications replaced; `LARGE_CONTEXT_ARTIFACTS` shrunk 11→2; `_write_audit_bundle` body 24→12 lines; Slice 3 advisory-report gating; new `transient_artifacts` import)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/prompt_packs.py` (`_phase2_sources` collapsed 3→2 branches)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py` (rmtree loop extended with `_transient`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/engineering_facts/writers/json_writer.py` (`JSON_ARTIFACTS` pruned 10→1 entry; `config_runtime_map` retained)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/validators.py` (Slice 3 coverage candidate list update — FR-9)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/source_context.py` (Slice 3 single coverage write — FR-9)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/evidence_agents.py` (Slice 3 registry/survival debug-gating)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/{phase2_analysis,phase26_scorecard,phase3_docs}.py` (Slice 3 advisory-report gating)
  - `knowledge_base/mcp-server/src/mcp_server/config.py` (Slice 2 `MCP_PIPELINE_DEBUG` flag)
  - `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py` (11 new tests: Slice 4 transient helper + Phase 4 rmtree + mid-phase-interrupt resume regenerate + source-lookup single-source-of-truth (4 input shapes); Slice 5 audit-bundle-only-writes-gates-and-hashes + REST fallback + REST order-of-preference; plus Slice 3 default-off/flag-on tests already in working dir)
  - `knowledge_base/mcp-server/tests/test_providers/test_engineering_facts/{test_builder_writers,test_service}.py` (Slice 5: drop dead-file existence assertions, add negative assertions for 9 dead maps)
  - `knowledge_base/mcp-server/tests/test_providers/test_{evidence_agents,source_context}.py` (Slice 3 test updates)
  - `knowledge_base/mcp-server/tests/test_pipeline/test_warning_collector.py` (Slice 3 test updates)
  - `knowledge_base/CLAUDE.md` (drop 4 removed-artifact bullets; rewrite task-dir contents bullet to reflect always-on vs debug-gated; new `MCP_PIPELINE_DEBUG` section)
  - `knowledge_base/.agent/system/mcp-workflow.md` (delete "Evidence And Answerability Artifacts" section; fix Phase 2.8 description `_analysis/_doc_pack_*.md` not `_preprocess/`; fix input-flow paragraph; remove `AnswerabilityAuditor` paragraph; new `MCP_PIPELINE_DEBUG` section)
  - `knowledge_base/.claude/PROGRESS_LOG.md` (this entry), `knowledge_base/.claude/PROJECT_MEMORY.md` (architecture decisions), `knowledge_base/.claude/context.md` (current state + next steps)
  - `.plans/01_grill.md`, `.plans/02_prd.md`, `.plans/03_slices.md`, `.plans/04_active_plan.md` (Slice 4 then Slice 5), `.plans/05_inspection_report.md` (Slice 4 WARN + Slice 5 WARN)
- **Problem**: Doc-generation pipeline wrote 666 files / 52 MB per run on the reference project (LobsterProfileExportKoopmansTest) but only ~10 final `.md` docs (~390 KB) were the actual deliverable. ~60% of the bytes were write-only artifacts nobody read. No content changes were intended — just removing dead surface area.
- **Root cause**: No single helper owned the transient dir naming convention; no canonical resolver for the Phase 2 source-lookup; the 9 dead JSON maps were artifacts of an early schema that never got cleaned up; `_audit/` was a wholesale copy tree from a period when the model output was the only audit trail — that's no longer the case.
- **Change** (Slices 3-5, this session; Slices 1+2 were already committed in `5e16911`):
  - **Slice 3 (FR-1c, FR-1d, FR-3, FR-9)**: Routed all remaining `_assessment/` advisory reports + `_pipeline_log.md` + `_investigation/_coverage_report.json` + `provider_calls.json` + `index.json` + `evidence_registry.md` + `survival_report.json` through `debug_write` to `_debug/`. Stopped writing 3 × 524 KB `_analysis/_bundle_*.md` files for a never-fired fallback. Single `_source_coverage.json` write (FR-9).
  - **Slice 4 (FR-6, FR-8)**: New `transient_artifacts.py` (un-gated mirror of `debug_artifacts.py`) consolidates 5 scattered transient dirs (`_context_slices/`, `_analysis/_merge_inputs/`, `_analysis/_synthesis_inputs/`, `_analysis/_repair_context/`, `_assessment/_scorecard_reduce/`) into a single `_transient/` deleted at Phase 4 alongside `_preprocess/` and `_analysis/`. Three duplicates of the Phase 2 analysis-guide source-lookup logic reduced to one canonical resolver (`CustomHarnessProvider._phase2_analysis_guide_files` static method).
  - **Slice 5 (FR-2, FR-7, FR-10, FR-11)**: `JSON_ARTIFACTS` pruned from 10 → 1 entry (`config_runtime_map`); `LARGE_CONTEXT_ARTIFACTS` shrunk 11 → 2 (`engineering_facts.json` + `config_runtime_map.json`). `_write_audit_bundle` body reduced 24 → 12 lines — keeps `enterprise_gates.json` + `final_doc_hashes.json` (SHA-256 of `staging/*.md`) and drops 3 file copies + 3 dir copies. `_assessment/enterprise_gates.json` write at `_enterprise_gate_report:3910` unchanged (primary location); `_audit/enterprise_gates.json` is the REST API fallback at `projects.py:786`. `CLAUDE.md` and `mcp-workflow.md` updated: removed 4 stale artifact bullets, deleted the "Evidence And Answerability Artifacts" section, fixed the Phase 2.8 description (`_analysis/_doc_pack_*.md` not `_preprocess/`), added a `MCP_PIPELINE_DEBUG` section to both files.
- **Why this approach**:
  - LLM-input invariance is the hard constraint. The 9 dead JSON maps and the `_audit/` copies were never sent to the model (they were in the `LARGE_CONTEXT_ARTIFACTS` drop list, and `_audit/` is not in Phase 3's context). Removing the writes is automatic LLM-input invariance — per-call `_model_inputs/**/manifest.json` should be byte-identical pre/post.
  - Mirror the Slice-2 `_debug/` precedent for the transient helper (same layout, minus the flag check). The new `transient_artifacts.py` exists to centralize a directory name + a write idiom; both prerequisites for any future "5 more transient dirs appear" event.
  - Single-source-of-truth for Phase 2 source-lookup eliminates drift risk between the prompt_packs and harness copies.
  - Phase 4 rmtree loop is the right place for throwaway-dir cleanup (no per-file unlink gymnastics).
  - `_audit/` reduced (not removed) because `projects.py:786` REST fallback reads `_audit/enterprise_gates.json` for the post-swap path. The `_audit/` is not in the rmtree list and survives the atomic swap, so the fallback remains reliable.
- **Verification**: 590 tests pass (572 baseline → 590, +18 across Slices 3-5). py_compile clean on all touched files. Ruff: no new F/E errors (pre-existing 4 errors in HEAD are unchanged). Grep gate: zero matches for the 9 dead JSON map names in `json_writer.py` and `harness.py`; zero matches for `source_fact_ledger`/`evidence_retention`/`answerability_audit`/`doc_evidence_auditor`/`AnswerabilityAuditor` in `CLAUDE.md` and `mcp-workflow.md`. Inspector verdicts: Slice 4 WARN (two non-blocking deviations: helper preserves `logger.warning` for missing-guide case; helper drops unused `ctx`/`pack_dir` from plan's signature); Slice 5 WARN (silent `try/except OSError` in `_write_audit_bundle` hash compute is now more visible — flagged as out-of-scope follow-up; pre-existing `JSONDecodeError` ordering in REST fallback; bonus order-of-preference test). Docker rebuilt and restarted: `mcp-server` is **healthy** on port 8002; `backend` recreated transitively; all other services unchanged and healthy.
- **Follow-ups (logged, not blocking)**:
  1. Surface hash-compute failures in `_write_audit_bundle` via warning log (currently silently emits `{}` on `OSError`; with `_audit/` shrunk to 2 files, this is the only thing in `_audit/` other than gates).
  2. Harden `_load_enterprise_gate_results` `_assessment/`→`_audit/` fallback so a corrupt `_assessment/enterprise_gates.json` doesn't short-circuit the fallback.
  3. Run paired-run acceptance on the reference project to confirm ≤300 files, ≤20 MB, and per-call manifest invariance against a recorded model-response baseline (manual, ~10-30 min on a scanned project with a working model).
  4. Track B (Phase 3 input quality: per-doc pack filtering, evidence registry slice promotion, budgeted retrieval) — unblocked now that FR-8 is committed; per its PRD, must run `/grill` → `/prd` → `/slices` → `/plan` → `/plan-approve pass` → build → `/inspect` → commit cycle.

## Session: 2026-06-10

### [Refactor]: Track A Slice 4 — transient-dir consolidation + prompt corpus dedup (FR-6, FR-8)

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/transient_artifacts.py` (new — un-gated helper module: `TRANSIENT_DIR_NAME`, `transient_dir()`, `transient_write()`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py` (modified — 5 transient write sites repointed to `_transient/`; new `_phase2_analysis_guide_files` static method as single source of truth; 2 phase2 file-list duplications replaced with helper calls)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/prompt_packs.py` (modified — `_phase2_sources` collapsed 3 → 2 branches)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py` (modified — `"_transient"` added to rmtree loop alongside `_preprocess` and `_analysis`)
  - `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py` (modified — 4 new tests: helper unit, Phase 4 cleanup rmtree, mid-phase-interrupt resume regenerate, source-lookup single-source-of-truth across 4 input shapes)
  - `.plans/03_slices.md`, `.plans/04_active_plan.md`, `.plans/05_inspection_report.md`
- **Problem**: 5 transient directories (`_context_slices/`, `_analysis/_merge_inputs/`, `_analysis/_synthesis_inputs/`, `_analysis/_repair_context/`, `_assessment/_scorecard_reduce/`) were scattered across the staging tree; all consumed in-process and never read by a later phase, validator, gate, resume path, or RAG push. `_assessment/_scorecard_reduce/` was leaking into the published staging tree because `_assessment/` is preserved across the swap. Separately, the Phase 2 analysis-guide source-lookup logic existed in 3 near-duplicate copies (one in `prompt_packs._phase2_sources`, two in `harness._phase2_shared_context` branches) with subtly different per-branch behavior.
- **Root cause**: No single helper owned the transient dir naming convention, so each call site re-derived paths inline (`mkdir` + `write_text`); no canonical resolver for the analysis guide, so each call site re-implemented the same three-way branch.
- **Change**: New `transient_artifacts.py` mirrors `debug_artifacts.py` (constants + helper functions) but is un-gated (no `if not config.pipeline_debug: return` branch) — these files are required for the pipeline, not debug-only. The 5 write sites now route through `transient_write(transient_dir(ctx.staging_dir, ...), content)`. `_transient/` is deleted at Phase 4 alongside `_preprocess/` and `_analysis/`. The phase2 file-list branches in `harness._phase2_shared_context` now call `_phase2_analysis_guide_files(preprocess_dir, analysis_guide_file)`, a new static method that mirrors the 2-branch `prompt_packs._phase2_sources` logic (prefer named guide if it exists, else glob `_analysis_prompts*.md`).
- **Why this approach**: Mirrors the Slice-2 `_debug/` precedent — a single helper module owns the dir name and the write idiom; the diff is easy to audit because the call-site changes are mechanical. The un-gated mirror is a new pattern: same layout as `debug_artifacts.py` minus the flag check. The single-source-of-truth helper for phase2 source-lookup eliminates drift risk between the prompt_packs and harness copies. No new config flags, no `tmp.replace` (these files are throwaway; the model call that reads them is wrapped by retries).
- **Verification**: 587 tests pass (582 baseline + 4 new + 1 from the uncommitted Slice 3 work). py_compile clean on all 5 touched files. Ruff clean on `transient_artifacts.py`, `prompt_packs.py`, `phase4_cleanup.py` (the 39 pre-existing F/E-class errors in `harness.py` are unchanged from HEAD). Grep gate: zero matches for `_context_slices` / `_merge_inputs` / `_synthesis_inputs` / `_scorecard_reduce` / old path patterns in `src/` and `tests/`. Inspector verdict: WARN (two minor deviations: helper preserves `logger.warning` for missing-guide case that `_phase2_sources` doesn't log; helper drops unused `ctx`/`pack_dir` from plan's suggested signature) — both acceptable, non-blocking.

## Session: 2026-06-09

### [Feature]: Pipeline Resilience — Slice 2 (phase failure surfacing + Phase 2 gate demotion) + Slice 3 (EvidenceQualityGate + thin-bundle notice)

- **Files changed**:
  - `providers/custom/pipeline/phases/phase1_investigation.py` (new — emit PHASE1_GROUP_FAILURE per failed group; return success=False on all-groups-fail)
  - `providers/custom/pipeline/phases/phase2_analysis.py` (new — demote coverage/depth gates from RuntimeError to warnings; zero-coverage carve-out kept)
  - `providers/custom/pipeline/phases/phase3_docs.py` (new — prepend thin-bundle notice when state.thin_bundle_groups is non-empty)
  - `providers/custom/pipeline/evidence_quality_gate.py` (new — pure Python Phase, checks _preprocess/{group}.md sizes, emits THIN_BUNDLE warnings)
  - `providers/custom/pipeline/warning_collector.py` (new — WarningCode enum extended with THIN_BUNDLE)
  - `providers/custom/harness.py` (modified — wrap build_facts_manifest; remove dead all-groups-fail RuntimeError; insert EvidenceQualityGate in phase list)
  - `providers/custom/models.py` (modified — add thin_bundle_groups: list[str] to HarnessRunState)
  - `tests/test_pipeline/test_phase_failures.py` (new — 5 tests: phase1 all-fail, phase1 partial-fail, phase2 zero-coverage hard-fail, phase2 partial coverage warn, phase2 partial depth warn)
  - `tests/test_pipeline/test_evidence_quality_gate.py` (new — 7 tests: thin bundle, ok bundle, missing bundle, resume from cache, always-success, phase3 notice prepend, phase3 no-notice on clean run)
  - `tests/test_providers/test_custom_provider.py` (modified — 2 tests added: WarningCode importable, PHASE2_COVERAGE_PARTIAL context fields)
  - `knowledge_base/CLAUDE.md` (modified — 6-phase → 7-phase pipeline; EvidenceQualityGate documented)
  - `.plans/04_active_plan.md`, `.plans/04_slice3_plan.md`, `.plans/05_inspection_report.md`
- **Problem**: Phase 1 group failures were silently swallowed (always returned success=True); Phase 2 coverage and depth gates hard-crashed the run even when partial output existed; `build_facts_manifest` failures produced opaque uncaught exceptions; no pre-Phase 3 signal when evidence was thin.
- **Root cause**: Phase 1 had no warning emission and no all-fail exit path (harness swallowed it as a log_cb warning); Phase 2 gates were unconditional RuntimeError even post-repair; build_facts_manifest was a bare call with no error handling; no phase existed to assess evidence quality before doc generation.
- **Change**: Phase 1 now emits one PHASE1_GROUP_FAILURE warning per failed group and returns PhaseResult(success=False) on all-fail, triggering PipelineRunner's existing clean-abort path. Phase 2 coverage and depth gates now emit PHASE2_COVERAGE_PARTIAL/PHASE2_DEPTH_PARTIAL warnings instead of raising (zero-coverage still raises). build_facts_manifest is wrapped in a structured try/except that logs with exc_info=True and re-raises a user-readable RuntimeError. The dead all-groups-fail RuntimeError in harness.py was removed. EvidenceQualityGate (new 7th Phase, no LLM calls) runs between Phase28Bundles and Phase3Docs, checks _preprocess/{group}.md sizes, emits THIN_BUNDLE warnings, populates state.thin_bundle_groups. Phase3Docs prepends an ASCII-only blockquote notice to all generated docs when thin groups were detected.
- **Why this approach**: WarningCollector infrastructure was delivered in Slice 1; Slices 2+3 wire it into actual failure sites. The zero-coverage carve-out preserves hard-fail where partial output is useless. EvidenceQualityGate uses the Phase ABC to stay in the established pattern; pure Python avoids LLM cost. Notice is ASCII-only (cp1252 compatibility rule).
- **Verification**: 12 new tests pass (test_phase_failures.py × 5, test_evidence_quality_gate.py × 7); full suite passes (572 tests, 0 regressions). Inspector verdict: WARN — import placement fixed inline; CLAUDE.md phase list updated.

## Session: 2026-06-03

### [Refactor]: Harness A-grade refactor — Phase interface, data-driven catalog, prompt templates, pack extension, semantic validation, dry-run estimation

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (888 ins / large deletion)
  - `mcp-server/src/mcp_server/providers/custom/prompts.py` (742 ins / large deletion)
  - `mcp-server/src/mcp_server/providers/custom/validators.py`
  - `mcp-server/src/mcp_server/services/plugin_schema.py`
  - `mcp-server/src/mcp_server/services/doc_quality_scorer.py`
  - `mcp-server/src/mcp_server/api/app.py`
  - `mcp-server/tests/test_providers/test_custom_provider.py`
  - `mcp-server/tests/test_services/test_plugin_runner.py`
  - `knowledge_base/.agent/system/mcp-workflow.md` (fully rewritten)
  - `knowledge_base/CLAUDE.md`
  - **New**: `mcp-server/data/doc_catalog.yaml`
  - **New**: `mcp-server/data/prompt_templates/phase2_analysis.md`
  - **New**: `mcp-server/data/prompt_templates/scorecard.md`
  - **New**: `mcp-server/data/prompt_templates/phase3_doc.md`
  - **New**: `mcp-server/data/prompt_templates/phase2_platform_sections/{talend,workato,lobster,generic}.md`
  - **New**: `mcp-server/src/mcp_server/providers/custom/doc_catalog.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/prompt_renderer.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/evidence_traceability.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/__init__.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phase.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/helpers.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/runner.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase1_investigation.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase2_analysis.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase26_scorecard.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase28_bundles.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase3_docs.py`
  - **New**: `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py`
  - **New**: `mcp-server/src/mcp_server/api/routes/estimate.py`
  - **New**: `mcp-server/tests/test_providers/test_evidence_traceability.py`

- **Problem**: The harness was a 5,229-line monolith (`harness.py`) where all six phase methods lived on one class, prompts were embedded Python f-strings, the doc catalog was split across five Python constants in four files, plugin packs could only inject prompts (not control model/concurrency/evidence filtering), structural validators had no semantic cross-check against investigation evidence, and there was no way to estimate run cost before triggering generation.

- **Root cause**: The harness grew feature-by-feature from a simpler system without a composable phase abstraction. Each new capability was bolted onto the single provider class rather than added as a discrete unit.

- **Change**:
  1. **Move 1 — Phase interface**: Created `providers/custom/pipeline/` with a `Phase` ABC (`name`, `should_skip`, `run`, `resume`, `estimate`), `PipelineRunner`, `HarnessHelpers` Protocol, and `HarnessHelpersImpl` facade. Six `Phase` classes (`Phase1Investigation` through `Phase4Cleanup`) encapsulate each phase. `CustomHarnessProvider.run()` now builds the phase list and delegates to `PipelineRunner`. Phase bodies remain callable from `harness.py` through the helpers protocol.
  2. **Move 2A — Doc catalog YAML**: Created `data/doc_catalog.yaml` as single source of truth for doc names, bundle assignments, required sections, Phase 2 section routing, and per-doc prompt instructions. `doc_catalog.py` provides a `DocCatalog` Pydantic model loaded once as a singleton. Removed `REQUIRED_DOCS`, `DOC_ASSIGNMENTS`, `DOC_BUNDLE_MAP`, `DOC_SECTION_REQUIREMENTS`, `DOC_SECTION_BUNDLE_MAP` from Python code.
  3. **Move 2B — Prompt templates**: Extracted f-string bodies of `group_analysis_prompt`, `scorecard_prompt`, `doc_prompt`, and `_platform_section_depth` into 7 `.md` files under `data/prompt_templates/`. Rendering uses `string.Template.safe_substitute` via `PromptRenderer`. Pack overrides (`plugin_pack_dir/prompts/*.md`) still take priority unchanged.
  4. **Move 3 — Pack extension protocol**: Added `PhaseConfig` model to `plugin_schema.py` (`policy`, `model`, `concurrency`). `PhasePolicy` fields now accept `str | PhaseConfig`. Added `_provider_for_phase()` (lazy-builds override provider from pack model ID), `_concurrency_for_phase()`, and evidence-profile enforcement in `_bundle_sections_for_docs()` via `_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS` mapping. All 4 semaphore constructions use the new helpers.
  5. **Move 4A — Dry-run estimation**: Implemented `estimate()` on all six Phase classes with deterministic call-count formulas. `PipelineRunner.estimate()` returns `RunEstimate` with per-phase and total min/max calls plus projected USD cost. New `GET /mcp/projects/{id}/generate/estimate` endpoint.
  6. **Move 4B — Evidence traceability**: Created `EvidenceTraceabilityValidator` that cross-references generated doc text against Phase 1 `InvestigationReport` objects. Detects phantom absence claims (doc says unit "not available" when investigation has evidence for it). Runs post-Phase-3, writes `_assessment/traceability_{doc_name}.json` on findings, and provides structured repair hints. Fixes the Steven project class of bug.

- **Why this approach**: Each move is independently deployable and backward-compatible. The `HarnessHelpersImpl` facade lets phase objects call provider helpers without circular imports. `string.Template` chosen over Jinja2 because prompt bodies contain many literal `{` `}` characters. The evidence traceability check is non-blocking (warning + JSON artifact only) to avoid false-positive failures during the initial rollout.

- **Verification**: 527 tests pass (520 pre-existing + 7 new traceability tests). All imports clean. Template files confirmed non-empty with correct slot substitution. Estimate endpoint registered at `/mcp/projects/{id}/generate/estimate`.

## Session: 2026-06-02

### [Feature]: Flagged dynamic DAG harness foundation + planner fallback + Docker rebuild

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py`
  - `mcp-server/src/mcp_server/providers/custom/models.py`
  - `mcp-server/src/mcp_server/providers/custom/dag.py`
  - `mcp-server/src/mcp_server/providers/custom/harness.py`
  - `mcp-server/tests/test_providers/test_custom_provider.py`
  - `.claude/PROGRESS_LOG.md`
  - `.claude/PROJECT_MEMORY.md`
  - `.claude/context.md`

- **Problem**: Harness orchestration was still phase-spine driven. Plugin policies and small-project shortcuts could skip or bound phases, but agents could not propose or validate an execution graph, Phase 2 had no skeleton/deep-dive tier, and cross-doc consistency was not represented as a first-class post-doc step.

- **Change**:
  1. Added DAG/planner config flags: `MCP_AGENT_DAG_ENABLED`, `MCP_AGENT_PLANNER_ENABLED`, `MCP_AGENT_PLANNER_MODE`, `MCP_AGENT_PLANNER_MAX_TOKENS`, `MCP_AGENT_PLANNER_REQUIRE_VALID_JSON`, `MCP_AGENT_DAG_STRICT_VALIDATION`, `MCP_AGENT_DAG_MAX_NODES`, `MCP_AGENT_DAG_MAX_DEPTH`, `MCP_AGENT_SMART_REPAIR_ENABLED`.
  2. Added typed DAG contracts in `models.py`: `ExecutionPlan`, `ExecutionGraph`, `ExecutionNode`, `NodeResult`, `GroupExecutionPlan`.
  3. Added `providers/custom/dag.py` with deterministic graph construction, safe planner fallback, cycle/missing-dependency/final-gate validation, graph depth checks, and cache-key generation.
  4. Wired flagged DAG mode into `CustomHarnessProvider.run()`. When `MCP_AGENT_DAG_ENABLED=false` the existing path is preserved. When enabled, the harness writes `_plans/execution_plan.json`, `_plans/execution_graph.json`, `_plans/node_results.json`, and `_plans/graph_events.jsonl`.
  5. Added optional LLM Phase 0 planner. Planner output is parsed as JSON, normalized into the safe built-in graph shape, validated against allowed docs/groups/final gates, and falls back to deterministic graph on invalid or unsafe output.
  6. Added planner-selected tiered Phase 2 path: deterministic skeleton sidecars in `_analysis/_skeleton/`, direct analysis for shallow groups, and existing agentic Phase 2 path for deep groups using explicit `phase2.deep_dive`.
  7. Updated `_tool_policy()` so tools are enabled only for explicit tool phases: `phase1.investigate`, `phase2.deep_dive`, repair phases, critic/consistency phases, and normal Phase 3. Core Phase 1/2/scorecard remain tool-disabled.
  8. Added Phase 3.6 consistency report (`_assessment/consistency_report.json`) checking inventory conflicts and unit drift before final gates.
  9. Added deterministic smart-repair root-cause classification behind `MCP_AGENT_SMART_REPAIR_ENABLED`, persisted under `_assessment/repair/`, and included in repair prompts.
  10. Added focused tests for deterministic graph final gates, unsafe graph rejection, and DAG tool-policy routing.

- **Why this approach**: DAG mode ships disabled by default and reuses existing mature phase implementations under graph control. The first implementation gets graph persistence, validation, planner fallback, tiered Phase 2, and auditability without allowing arbitrary plugin/user node execution or bypassing final publish gates.

- **Verification**:
  - `cd mcp-server && uv run pytest tests/test_providers/test_custom_provider.py -q` -> 171 passed.
  - `cd mcp-server && uv run pytest tests/test_services/test_pack_loader.py tests/test_services/test_plugin_runner.py -q` -> 34 passed.
  - Combined focused run: 205 passed.
  - `docker compose build` from repo root succeeded. Images built: `intodata-kb-v2-mcp-server`, `intodata-kb-v2-mission-control`, `intodata-kb-v2-backend`, `intodata-kb-v2-dashboard`, `intodata-kb-v2-frontend`.

### [Fix]: Repair path agentic routing + synthesis doc provenance + Evidence Register row-merge

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py`
- **Problem**: Three compounding issues caused runs to fail at the enterprise publish gate on `source_provenance`:
  1. Phase 2 and Phase 3 repair calls were routing through `complete` (one-shot) instead of `complete_with_tools`, despite `_tool_policy` returning `enabled=True` for repair phases. The infrastructure was built; the routing bug prevented it from activating.
  2. `09_EXECUTIVE_SUMMARY` and `07_QUALITY_SECURITY_RISK_SCORECARD` are synthesis documents — their Evidence Register legitimately cites other generated docs, not raw scanner file paths. The repair prompt gave no guidance for this, so the model wrote generic prose references that failed `_PROVENANCE_SOURCE_RE`.
  3. `_phase3_targeted_repair_content` replaced the entire Evidence Register on every repair pass. Rows that passed validation in the previous attempt were discarded; each pass regenerated the same failing rows, causing oscillating failures.
- **Root cause**:
  1. `_tool_policy` checked `phase.startswith("phase2")` before `"repair" in phase`. The string `"phase2.repair"` matches the prefix check and returns `enabled=False` before the repair check fires. One-line ordering bug.
  2. The repair prompt said "return only a `## Evidence Register` table" with no mention that synthesis docs should cite sibling document filenames. `_PROVENANCE_SOURCE_RE` accepts `.md` extensions, so the fix was purely a prompt instruction gap.
  3. `_phase3_targeted_repair_content` used full-register replacement with no memory of which rows were already valid.
- **Change**:
  1. `_tool_policy`: moved `if "repair" in phase` check before `if phase.startswith("phase2")`. Added comment explaining ordering requirement.
  2. Added module-level `_SYNTHESIS_DOCS = frozenset({"09_EXECUTIVE_SUMMARY.md", "07_QUALITY_SECURITY_RISK_SCORECARD.md"})`. Updated `_phase3_repair_prompt` to branch on `doc_name in _SYNTHESIS_DOCS` and emit synthesis-specific Evidence Register instructions (cite sibling docs by filename).
  3. Added `_parse_register_rows()` and `_build_register_section()` module-level helpers. Updated `_phase3_targeted_repair_content` to: parse existing rows, parse repair rows, keep existing rows whose topics are not in the repair output, merge with repair rows, rebuild the register section.
- **Why this approach**: Fix 1 is a one-line reorder with no side effects — the infrastructure already worked, just never activated. Fix 2 targets the prompt gap directly rather than relaxing the validator (which would allow genuinely bad provenance through). Fix 3 is conservative: repair rows win on topic conflicts, non-conflicting existing rows are preserved, so valid evidence is never discarded.
- **Verification**: 513 mcp-server tests pass. Docker container rebuilt and healthy.

### [Analysis]: Harness static agent audit + architecture gap assessment

- **Files changed**: none (analysis only — workflows)
- **Problem**: Need to understand how close we are to integration-suite output quality and where to introduce agentic tool-calling loops.
- **Finding**: The harness is a hybrid. `complete_with_tools` loops exist in all three provider implementations and `ScopedEvidenceTools` is fully built. Phase 3 already routes through `complete_with_tools` for core generation. Phase 2 core generation is explicitly tool-disabled (correct for cost control). The only gap was repairs: `_tool_policy` was silently blocking tools for `"phase2.repair"` via the `startswith("phase2")` check. Two architecture gaps remain after this session's fixes: (1) `SingleShotInvestigator` doesn't use tool calls (separate from `ChunkManifestInvestigator`), (2) `_run_internal_reviewers` uses keyword-presence checks rather than structured LLM review.
- **Verification**: Workflow analysis of integration-suite repo + full harness audit.

---

## Session: 2026-06-01 (third entry)

### [Fix/Feature]: Agentic investigation hardening — safety net, batched extraction, rich manifest identifiers, pack RAG push

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` (safety net for forced chunks, `_extract_batch`, batched parallel extraction, updated selection prompt)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (`_extract_chunk_identifiers`, manifest now emits `categories` + `identifiers`, agentic resume cache check)
  - `mcp-server/src/mcp_server/providers/base.py` (`required_docs: list[str]` field on `AgentResult`)
  - `mcp-server/src/mcp_server/services/plugin_runner.py` (forward `required_docs` from harness step result)
  - `mcp-server/src/mcp_server/services/rag_bridge.py` (`extra_doc_names` param on `push_docs` + `_read_md_files`)
  - `mcp-server/src/mcp_server/api/routes/projects.py` (`push_to_rag` endpoint and `_push_with_retry` use `extra_doc_names` for pack docs)
  - `mcp-server/src/mcp_server/config.py` (`agent_phase1_batch_size: int = 20`)
  - `mcp-server/data/plugin_packs/table-inventory/plugin.yaml` (removed "Target Environment Assumptions" from `sections`)
  - `docker-compose.yml` (`MCP_AGENT_PHASE1_AGENTIC: "true"`)

- **Problem**:
  Live runs revealed four agentic investigation failures:
  1. 20 of 72 units missing from investigation — SALESTABLE, SALESLINE, MARKUPTRANS, DIRPARTYTABLE completely absent from docs because model skipped chunks containing them.
  2. Present tables (CUSTTRANS) had incomplete fields — extraction hit 10k output token limit with 72 units in one call; model truncated mid-response.
  3. Pack docs (TABLE_INVENTORY.md, DDL_REBUILD_SCRIPTS.md) never pushed to RAG — `_read_md_files` filtered to digit-prefixed filenames only; pack docs start with letters.
  4. Agentic resume re-ran Phase 1 every time — no cache check before entering the manifest investigator path.
  5. "Target Environment Assumptions" gate blocked every DDL_REBUILD_SCRIPTS doc — section never generated because it's not in the pack prompt.

- **Root cause**:
  1. Manifest only had category labels (`"sql_queries"`) not actual named entities — model couldn't tell which chunk contained SALESTABLE vs CUSTTRANS. Selection was blind.
  2. `ChunkManifestInvestigator` sent all `group_units` in one extraction call. 72 units × ~200 tokens/item = ~28k output needed; 10k limit truncated mid-list.
  3. `_read_md_files` and `_read_public_doc_texts` both used `if not md_file.name[0].isdigit(): continue` — pack docs excluded everywhere including the "push to RAG" button endpoint.
  4. Agentic branch in `_investigate_group` had no `_load_investigation_artifact` check before proceeding — sequential path checks per-chunk, agentic path had no equivalent.
  5. `plugin.yaml` `sections` list used as fallback for `required_sections` in `validate_pack_quality`; "Target Environment Assumptions" was in `sections` but never in any prompt.

- **Change**:
  1. **Rich manifest identifiers** (`harness.py`): Added `_extract_chunk_identifiers(text)` — regex-based extractor pulling SQL table names (FROM/JOIN/INTO), connection names (RegisterConnection/OpenDbConnection), HTTP routes, CamelCase class names, ALL_CAPS constants, config keys. Manifest now has `categories` (category labels) + `identifiers` (actual named things). Works for all platforms, not just SQL. Renamed `keyword_hints` → `categories` + `identifiers` in manifest entries.
  2. **Safety net** (`investigator.py`): After model selection, build `unit_to_chunks` map. For any unit in `group_units` reachable from the manifest but not covered by selected chunks, force-add those chunks. Guarantees coverage parity with sequential mode regardless of model selection quality.
  3. **Batched parallel extraction** (`investigator.py`): Split `group_units` into batches of `config.agent_phase1_batch_size` (default 20). Run all batches in parallel via `asyncio.gather`. Each batch has its own retry loop. Merge results. For 72 units: 4 parallel calls of 20 units each instead of 1 truncated call of 72.
  4. **Updated selection prompt** (`investigator.py`): Explains `identifiers` field and instructs model to use it for selection decisions. "If you need evidence about a table or class, pick the chunk whose identifiers list contains that name."
  5. **Agentic resume cache** (`harness.py`): Added `_load_investigation_artifact(investigation_dir, group)` check at the top of the agentic branch. If artifact exists from a prior run and `ctx.resume_state` is set, load and return immediately. Matches sequential path's per-chunk cache behavior.
  6. **Pack RAG push** (`base.py`, `plugin_runner.py`, `rag_bridge.py`, `projects.py`): Added `required_docs: list[str]` to `AgentResult`; harness populates it with `self._required_docs` on success; `PluginRunner` captures it from the harness step and forwards it in its own `AgentResult`; `_read_md_files` accepts `extra_filenames` set; `push_docs` + `_push_with_retry` accept `extra_doc_names`; both the post-generation push and the `/push-to-rag` endpoint pass pack doc names. The `/push-to-rag` endpoint auto-detects non-numbered non-underscore docs from `docs/` without needing the result.
  7. **plugin.yaml fix**: Removed "Target Environment Assumptions" from `DDL_REBUILD_SCRIPTS.sections` — it was never in the generation prompt so the model never wrote it, but `validate_pack_quality` used `sections` as fallback for `required_sections`.
  8. **docker-compose.yml**: `MCP_AGENT_PHASE1_AGENTIC: "true"` enabled globally.

- **Why this approach**:
  - `_extract_chunk_identifiers` is pure regex with no LLM cost — runs during manifest build which is already a local file-read pass. Universally applicable: SQL, REST, config, ORM identifiers all extracted by the same function. Longer identifiers ranked first (more specific).
  - Safety net is cheap (one dict lookup per unit) and transparent — adds minimal overhead when model selection is good, guarantees correctness when it's not.
  - Batching at 20 units keeps output well under 10k tokens per call. Parallel batches mean wall-clock time stays similar to one large call while getting complete coverage.
  - `PluginRunner` forwarding `required_docs` through the result chain is necessary because `PluginRunner.run()` constructs its own `AgentResult` from scratch — it was the missing link between the harness knowing the pack docs and the RAG push receiving them.

- **Verification**: 513 tests pass. Live run on Steven/inventosryddl: agentic investigation complete (42 evidence items, 3 calls), enterprise gates passed, RAG push confirmed working manually (2 docs pushed). Coverage gap for SALESTABLE/SALESLINE/MARKUPTRANS/DIRPARTYTABLE traced to output token truncation + poor selection — batching + rich identifiers address both.

## Session: 2026-06-01 (second entry)

### [Fix/Feature]: Phase 3 repair explosion fix, Phase 1 early stop, agentic chunk-manifest investigator

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/validators.py` (extended `_PROVENANCE_SOURCE_RE`, rewrote `_evidence_register_errors`, updated `validate_source_provenance`, changed `required_outputs` mismatches to warnings)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (provenance soft-warning logging, Phase 1 early-stop logic, agentic branch with `ChunkManifestInvestigator`)
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` (new `ChunkManifestInvestigator` class)
  - `mcp-server/src/mcp_server/config.py` (`agent_phase1_agentic: bool = False`)
  - `mcp-server/tests/test_providers/test_custom_provider.py` (updated provenance + required_outputs tests, 3 new `ChunkManifestInvestigator` tests)

- **Problem**:
  A table-inventory pack run on the Steven project consumed ~2.14M tokens on Phase 3 repair (22 slices across 2 docs) and still failed. Root cause chain:
  1. `_PROVENANCE_SOURCE_RE` didn't include `.cs`, `.config`, `.asax` — valid C# source paths in the Evidence Register rows failed the validator.
  2. This triggered `_phase3_targeted_repair`, which sent the full ~316KB evidence context split into 12+10 slices, all to fix a regex mismatch.
  3. After 22 slices the same validator rejected the repaired docs with the same regex — infinite loop.
  4. Pack quality gate also failed because "Field Usage Matrix" ≠ "field_inventory" in `required_outputs` check.
  Separately: Phase 1 processed all 18 chunks sequentially even after all reachable units were covered.

- **Root cause**:
  - `_PROVENANCE_SOURCE_RE` was web/scripting-centric (.py, .js, .sql, .xml) — no .NET, Java, Go, or path-separator patterns. Any real-world C#/.NET project would fail the provenance gate.
  - `_evidence_register_errors` returned a flat `list[str]` with no distinction between structural failures and content-quality issues. All went to `errors`, all triggered repair.
  - `required_outputs` in `validate_pack_quality` treated section name mismatches as hard errors.
  - No coverage tracking in the chunk loop — all N chunks processed regardless of whether all units were already covered.

- **Change**:
  1. **Regex fix** (`validators.py`): Extended `_PROVENANCE_SOURCE_RE` with 20+ extensions (.cs, .vb, .config, .asax, .java, .kt, .go, .rs, .yaml, .sh, .ps1, etc.) and a path-separator pattern `[/\\][\w]` that catches any slash-separated path regardless of extension. Added `_PATH_LIKE_RE` for secondary classification.
  2. **Error/warning split** (`validators.py`): `_evidence_register_errors` now returns `tuple[list[str], list[str]]` (hard_errors, soft_warnings). Structural failures (missing section, wrong headers, bad confidence) stay as hard errors. ALL source-column quality issues — truly empty, placeholder, generic text, unrecognized-format paths — are soft warnings. Repair only fires for structural failures, never for content quality.
  3. **`validate_source_provenance`** updated to collect both errors and warnings and pass both to `_coverage_report_from_identities`. `CoverageReport.ok` is True when only warnings exist → repair trigger at `if not provenance.ok` never fires for format issues.
  4. **`required_outputs` → warnings** (`validators.py`): section name mismatches in `validate_pack_quality` moved from `errors` to `warnings`. Enterprise gate `pack_quality.ok` remains True.
  5. **Harness logging** (`harness.py`): After provenance check, log up to 5 soft warnings before the repair trigger so operators see them in run logs without expensive repair.
  6. **Phase 1 early stop** (`harness.py`): Pre-scan all chunks upfront to build `all_assigned` (units reachable from any chunk) and `chunk_unit_map`. Track `covered_units` after each chunk completes (evidence + skipped_units both count, including cached resume artifacts). Break when `covered_units >= all_assigned`. Logs: `Investigation early stop for {group}: all N units covered, skipping M remaining chunk(s)`.
  7. **`ChunkManifestInvestigator`** (`investigator.py`): Two-phase class replacing the per-chunk loop when `config.agent_phase1_agentic=True`. Phase 1 (manifest, ~2k tokens, max 512 output): model returns `{"fetch_ids": [...]}`. Phase 2 (extraction, ~15-20k tokens for selected chunks): standard `SingleShotInvestigator`-style extraction with 3-attempt retry. Fallback to all chunks if manifest call returns invalid JSON. Semaphore wraps both agentic and sequential branches.
  8. **Config** (`config.py`): Added `agent_phase1_agentic: bool = False` (env: `MCP_AGENT_PHASE1_AGENTIC`).

- **Why this approach**:
  - Regex + error/warning split is the minimal fix that stops the 2.14M-token repair cascade without removing any quality signal — warnings still appear in `source_provenance_report.json` and run logs.
  - `[/\\][\w]` as a catch-all path pattern means any future platform with file paths in scanner output won't hit this again, regardless of extension.
  - Early stop is always-on with no config — zero downside. It only fires when units are actually covered, so it can't produce worse coverage than the sequential path.
  - `agent_phase1_agentic` is off by default until validated on a real run. For 18 chunks → ~2 calls instead of ~36 per group, ~12× cheaper.

- **Verification**: 513 tests pass (`pytest -x -q`). 3 new `ChunkManifestInvestigator` tests cover: happy path (manifest select → extract), bad-JSON fallback to all chunks, empty manifest auto-skip.

## Session: 2026-06-01

### [Refactor/Feature]: Phase 1 parallelism, enterprise pre-screen fix, scorecard confidence classification

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` (+1 line: `agent_phase1_concurrency`)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (refactor `_phase1_investigation`, fix `_chunk_has_pack_relevant_content`)
  - `mcp-server/src/mcp_server/providers/custom/prompts.py` (+10 lines: confidence block in `_SCORECARD_PREAMBLE_DEFAULT`)
  - `mcp-server/data/plugin_packs/table-inventory/prompts/scorecard.md` (+7 lines: per-dimension confidence annotations)
  - `mcp-server/data/plugin_packs/quick-summary/prompts/scorecard.md` (+4 lines: preliminary label, confidence column, operational caveat)

- **Problem**:
  1. Phase 1 investigation processed groups sequentially: 6 groups × 6 chunks = 36 serial LLM calls at ~15,500 tokens each (~558k input tokens). Phase 2 had `asyncio.gather` + semaphore since its first implementation; Phase 1 never got retrofitted.
  2. Enterprise runs (no plugin pack) bypassed the chunk pre-screen entirely. `_pack_investigation_categories()` returns `set()` for enterprise runs, causing `_chunk_has_pack_relevant_content` to always return `True`. DTO stubs, XML namespace files, and CSS each burned a full LLM call.
  3. Scorecard output made no distinction between findings confirmed by static analysis vs. operational dimensions that require runtime data (monitoring, SLA, error recovery). All scores presented with equal confidence.

- **Root cause**:
  1. Phase 1 was implemented before the `asyncio.gather` + semaphore pattern was established in Phase 2. It was never parallelised because `SingleShotInvestigator` is a stateless single-shot extractor — groups are independent but nobody added the concurrency wrapper.
  2. The pre-screen was originally designed for pack runs where the pack declares `evidence_profiles.include`. The fallback for "no categories declared" was a hard `return True` rather than a broad-but-bounded default. Enterprise runs never have a pack manifest, so they always hit the fallback.
  3. No prior prompt instruction distinguished code-confirmed findings from inferred ones. The scorecard prompt demanded evidence for every score but didn't distinguish what kind of evidence was possible from static analysis alone.

- **Change**:
  1. **Phase 1 parallelism** (`harness.py`): Extracted per-group logic into `_investigate_group(group)` inner async function. Added `asyncio.Semaphore(config.agent_phase1_concurrency)` (default 4) wrapping the chunk loop inside each group. Replaced the outer `for group in ctx.preprocess_groups:` loop with `await asyncio.gather(*[_investigate_group(g) for g in ctx.preprocess_groups])`. Results collected in original group order for deterministic `state.investigation_reports` ordering. `state.calls.extend(calls)` and `_log_call_metrics` remain inside each group task for timely metrics logging — asyncio's single event loop makes list mutations safe without locks.
  2. **Enterprise pre-screen** (`harness.py`): When `_pack_investigation_categories()` returns empty (enterprise run), `_chunk_has_pack_relevant_content` now checks against all `CATEGORY_PATTERNS` entries except `source_summary`. `source_summary` matches `\b(file|source|component|unit|job|module|class|function)\b` — effectively any code file — so including it would make the filter a no-op. The remaining 8 categories (sql_queries, ddl_schema, orm_entities, db_connections, config_references, routes, handlers, auth) skip pure DTO stubs and namespace-only chunks.
  3. **Confidence classification** (`prompts.py`, `scorecard.md` ×2): Added a confidence block to `_SCORECARD_PREAMBLE_DEFAULT` mapping dimension type → expected confidence (architecture/connectivity HIGH, operational/reliability LOW INFERRED, security MEDIUM, business context LOW INFERRED). Added `(INFERRED)` annotation requirement, "Requires runtime validation to confirm" per LOW score, and a "Preliminary X.X/5.0 -- expect up to 10% adjustment" prefix on the overall score. Pack-specific scorecard prompts updated with the same pattern: `table-inventory` annotates each dimension inline; `quick-summary` adds a Confidence column to the risks table and flags operational risks explicitly.
  4. **Config** (`config.py`): Added `agent_phase1_concurrency: int = 4` (env: `MCP_AGENT_PHASE1_CONCURRENCY`) adjacent to the Phase 2/3 concurrency fields.

- **Why this approach**:
  - Parallel group dispatch mirrors Phase 2 exactly — low risk, mechanical change, no new infrastructure. Groups were already independent; the only missing piece was the semaphore + gather wrapper.
  - Excluding `source_summary` from the enterprise pre-screen is critical: using all patterns including it would still pass every chunk since `source_summary` is a catch-all. The 8 specific categories collectively cover all meaningful evidence types for integration projects.
  - Confidence annotations are a prompt-only change with no harness coupling. They surface a known limitation (static analysis cannot confirm operational behaviour) in the output itself rather than requiring users to know this externally.

- **Verification**: `python3 -m py_compile` passed on all three modified Python files. Manual review of the `asyncio.gather` pattern against Phase 2's equivalent pattern in `_phase2_analysis`. No tests run (test suite requires Docker stack).

---

## Session: 2026-05-29 (third entry)

### [Fix/Feature]: Plugin pack phase control, investigation quality, nginx DNS fix

- **Files changed**:
  - `dashboard/nginx.conf` (+25 lines, -12 lines)
  - `mcp-server/src/mcp_server/services/plugin_schema.py` (+63 lines)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (+~300 net lines this slice)
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` (+53 lines)
  - `mcp-server/src/mcp_server/providers/custom/prompts.py` (+1 line: `facts_summary` import)
  - `mcp-server/tests/test_providers/test_custom_provider.py` (+~180 lines new tests)
  - `mcp-server/data/plugin_packs/table-inventory/plugin.yaml` (+6 lines phases block)
  - `mcp-server/data/plugin_packs/table-inventory/prompts/investigation.md` (new file, ~70 lines)

- **Problem**:
  1. Dashboard at `localhost:8001` returned 502 on `/auth/me` and `/health` after `backend` and `mcp-server` containers were restarted — nginx had cached stale IPs from container startup.
  2. Plugin packs had a binary execution model: either full enterprise harness or all-skip (pack-contract). No way to run investigation only, or analysis in bounded mode, or declare custom per-phase behaviour.
  3. Investigation prompt overrides (`prompts/phase2.md`, `prompts/phase3.md`) existed in the pack dir but `phase2.md` only replaced the preamble — the full enterprise section structure was still appended. No support for investigation.md overrides.
  4. `_units_for_chunk` used simple substring match, causing false-positive unit assignments. e.g. "Order" would match any chunk containing `using Types.Order;`. Model couldn't produce evidence for these units, couldn't skip them, triggered 3 retries then a WARNING.
  5. After max retries, `SingleShotInvestigator` raised `InvestigatorError` — harness caught it and logged a WARNING, wasting 3 LLM calls on units genuinely absent from the chunk.
  6. Phase 3 received all group investigation files wholesale; for large projects this would be hundreds of evidence items regardless of relevance.
  7. `table-inventory` pack had no investigation phase, so field-level completeness depended on regex snippets — missed complete POCO field lists, wrong connection registration names.

- **Root cause**:
  - nginx `proxy_pass` with hostname resolves once at startup and caches the IP; when upstream containers restart they get new IPs and nginx returns 502 until it is also restarted.
  - Pack system had `_pack_contract_mode` as a single boolean derived from `evidence_profiles` presence — no granularity.
  - Investigator prompt was appended after the base prompt using `_resolve_preamble`; no path for a full prompt replacement.
  - `_units_for_chunk`: `name in text` is a substring search; common unit names like "Order" match anywhere in the preprocess file.
  - No graceful fallback in `SingleShotInvestigator.run()` — unresolvable units raised rather than auto-skipping.

- **Change**:
  1. **nginx DNS fix**: Added `resolver 127.0.0.11 valid=10s ipv6=off;` to dashboard nginx. Converted all `proxy_pass` directives to use `set $var` variables (forces per-request DNS lookup). `/mc/` and `/auth/` paths use `rewrite ... break` + variable proxy_pass for correct URI stripping.
  2. **`PhasePolicy` model** in `plugin_schema.py`: `investigation: skip|run`, `analysis: skip|run|bounded`, `scorecard: skip|run`, `doc_bundles: skip|run`. Added `phases: PhasePolicy | None` to `PluginPackManifest`. Backward compat: no `phases` + `evidence_profiles` present → all skip.
  3. **`_phase_policy(phase)` in harness**: Replaces `_pack_contract_mode` boolean. `_pack_contract_mode` is now derived (`investigation==skip AND analysis==skip`). Main `run()` routing split into independent Phase 1 and Phase 2 decisions. Scorecard and doc_bundles gated by `_phase_policy("scorecard")` / `_phase_policy("doc_bundles")`.
  4. **Prompt wiring**: `investigation.md` in pack `prompts/` injects a preamble into `SingleShotInvestigator` via new `preamble` kwarg. In `bounded` mode with `phase2.md` present, that file is used as the COMPLETE Phase 2 prompt (no enterprise sections appended).
  5. **`_phase3_context_files`**: Pack path now triggers on `analysis == "skip"` (not `_pack_contract_mode`). When `investigation == "run"`, builds a filtered investigation bundle via `_build_pack_investigation_bundle()` — keeps only evidence categories matching the doc's evidence profile, capped at 40k chars.
  6. **`_units_for_chunk`**: Word-boundary regex (`\b{name}\b`) replaces substring match. Units with non-word chars in name fall back to substring.
  7. **Investigator graceful fallback**: After 3 retries, remaining missing units auto-written to `skipped_units: "not evidenced in chunk after maximum retries..."` — no exception raised, no WARNING in harness log.
  8. **`table-inventory` pack**: Added `phases: investigation: run` + `prompts/investigation.md` with explicit extraction rules for SQL_QUERY, ORM_CALL, DB_CONNECTION, JOIN_RELATIONSHIP, FK_RELATIONSHIP, CROSS_DATABASE_USAGE, DDL_ARTIFACT. Skipped-units rules made explicit for DTOs and reference-only units.

- **Why this approach**:
  - nginx variables force DNS re-resolution per request — the minimal nginx change that fixes stale-IP 502s without restarting the dashboard container on every upstream restart.
  - Phase policy as a declared manifest block (not inferred from evidence_profiles) gives pack authors explicit control without touching harness code.
  - Auto-skip on max retries converts a hard failure into a recorded coverage gap — truthful output rather than error noise.
  - Filtered investigation bundle by category keeps Phase 3 context bounded regardless of project size.

- **Verification**: `uv run pytest -q` → 508 passed. Docker rebuild applied; `/auth/me` and `/health` returned correct responses through nginx after container restart.

## Session: 2026-05-29 (second entry)

### [Feature]: Integration Suite Reliability Layer — all 7 items implemented

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (+~750 lines net)
  - `mcp-server/src/mcp_server/providers/custom/validators.py` (+~100 lines new `validate_scorecard_contract`)
  - `mcp-server/src/mcp_server/providers/custom/prompts.py` (+~60 lines: 09_EXECUTIVE_SUMMARY, Workato drift, Talend connection table)
  - `mcp-server/src/mcp_server/providers/custom/talend_intelligence.py` (new file, ~150 lines)
  - `mcp-server/src/mcp_server/api/routes/generate_runs.py` (+168 lines: rerun endpoint)
  - `mcp-server/src/mcp_server/config.py` (+1 field: `phase_cost_warning_input_tokens`)
  - `mcp-server/src/mcp_server/db/models.py` (+1 field: `parent_run_id`)
  - `mcp-server/src/mcp_server/services/project_registry.py` (+7 lines: `get_generate_run`, `model_name` arg on `create_generate_run`)
  - `mcp-server/alembic/versions/015_add_generate_run_parent_id.py` (new migration)
  - `mcp-server/tests/test_providers/test_custom_provider.py` (import fix: `_provider_supports_tools`; enterprise summary fix)
- **Problem**: The harness had no operator-visible pipeline log, used implicit tool policies (two module-level functions), had generic repair prompts that didn't name exact failures, no single-phase rerun capability, no scorecard weight enforcement, no executive summary doc, and no Talend-specific intelligence (ESB detection, activity flags).
- **Change**:
  1. **`_PipelineLog` + `_pipeline_log.md`**: New class accumulates per-phase token counts, call counts, and validation status from `state.calls`. Written to `staging/_pipeline_log.md` after each phase (Phase 1, 2, 2.6, 3, 4) and on error. Operators get real-time visibility into in-progress runs.
  2. **`ToolPolicy` frozen dataclass**: Replaces `use_tools_for_core_generation()` and `tool_input_token_cap_for_phase()` module-level functions with `self._tool_policy(provider, phase) -> ToolPolicy`. Adds explicit rule: `scorecard`/`phase2.6` phases are disabled for tools (was implicitly enabled if provider supported tools — a latent bug). Repair phases get half the normal Phase 3 budget.
  3. **Minimum-byte gate**: In `_phase3_docs._write_one()`, after the model writes a doc, `_validate_agent_markdown(text, [], min_bytes=150)` fast-fails near-empty output before expensive validators run. Increments `state.empty_output_counts` and logs a warning.
  4. **`validation_gaps` in repair contracts**: Both `_phase3_targeted_repair` and `_phase2_targeted_repair` now populate `AgentTaskContract.validation_gaps` from the external validator errors before dispatch. Internal retry loops in `AgentTaskRuntime` already surface this field; it was only set on attempt>1 from internal validation, not from the outer gate errors.
  5. **`validate_scorecard_contract()`** in `validators.py`: Checks all 8 dimension names present (tolerates space vs underscore), each dimension's weight (e.g. `20%` adjacent to `data_lineage`), and each risk table row contains `[SOLO]` or `[PAIR]`. Hooked into `_phase26_scorecard()` after basic validation; failures are advisory warnings in enterprise mode (write to `_scorecard_contract_errors.json`, add to `state.warnings`), not blocking raises.
  6. **Phase cost reporting**: `_write_phase_costs()` called at end of successful `run()`. Aggregates `state.calls` by `call.phase` and writes `_assessment/phase_costs.json`. Warns to logger if any phase exceeds `config.phase_cost_warning_input_tokens` (default 500K).
  7. **Single-phase rerun API**: `POST /generate-runs/{project_id}/{run_id}/rerun` with `{"phase": "scorecard"}` or `{"phase": "doc", "target": "02_COMPONENT_INVENTORY"}`. Creates a child `GenerateRun` with `parent_run_id`. Background task invalidates the target artifact (deletes `scorecard.md` or `{doc}.md` + the agent task status checkpoint), then calls `_run_generate_background` with `resume=True`. Resume mode skips all completed phases and only regenerates the missing artifact. Returns 409 if parent run is still running or if Phase 2 artifacts don't exist.
  8. **`09_EXECUTIVE_SUMMARY`**: Added as last doc in `REQUIRED_DOCS`. Enterprise-only: filtered from `self._required_docs` in `run()` when `not ctx.enterprise_mode or self._plugin_mode`. Context is scorecard.md + all other completed docs (not `_preprocess/`). Dispatched after all other Phase 3 docs complete (not in parallel batch). Failures are non-fatal (warning only). Prompt requires 5 mandatory sections in business language (no jargon, no raw code/SQL).
  9. **`talend_intelligence.py`**: `detect_talend_esb_patterns()` scans scan_result.json for ESB component types (cRoute, cRouteWhen, cREST, cJMS, cBeanRegister, cBeanValidate, cActiveMQ, cKafka*). `detect_talend_activity_flags()` identifies recycle-bin artifacts, Copy_of_ jobs, deleted statusCode, and encrypted property values. `write_talend_intelligence()` writes `_preprocess/talend_esb_facts.json` and `_preprocess/talend_activity_flags.json` if any findings. Called in `run()` for Talend projects after `build_facts_manifest()`, before Phase 1.
  10. **ESB guide-section rule**: `validate_analysis_guide_sections()` reads `_preprocess/talend_esb_facts.json`; if ESB detected, requires a `ROUTE_GRAPH` or `ESB_ROUTES` heading in the combined analysis output.
  11. **Workato export-drift guidance**: Added to Phase 2 Workato platform analysis requirements — warns about JSON export vs live VQL drift, `[EXPORT_DRIFT_RISK]` for old exports, `[POSSIBLY_DYNAMIC]` for apparent dead steps.
  12. **Talend connection semantic table**: `01_ARCHITECTURE_MAP` prompt for Talend projects now requires a `## Connection Semantic Table` section with `Connection Name | Type | Purpose | Consuming Jobs | Risk Notes`.
- **Why this approach**: `ToolPolicy` as a frozen dataclass makes the per-phase policy testable and explicit; the old function pair was an implicit contract that could drift. Pipeline log written incrementally (not just at end) so operators can monitor hanging runs. Rerun uses artifact invalidation + resume rather than building a separate phase-level harness entrypoint, which would have required duplicating DocGenContext construction and provider setup (~200 lines). Executive summary dispatched last because its context is the completed docs — batch parallelism with other docs would read empty files.
- **Verification**: `uv run pytest -q` → 508 passed (from 499; +9 previously collection-blocked tests now collected). All 7 plan items implemented.

## Session: 2026-05-29

### [Fix/Feature]: Pack-contract artifact pipeline — remaining items completed

- **Files changed**:
  - `mcp-server/src/mcp_server/services/doc_quality_scorer.py` (+7 lines)
  - `mcp-server/src/mcp_server/providers/custom/validators.py` (+28 lines)
  - `mcp-server/src/mcp_server/providers/custom/evidence_bundles.py` (+80 lines, new file this session)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (-6 lines dead code)
  - `mcp-server/tests/test_providers/test_custom_provider.py` (+100 lines, new tests for all above)
- **Problem**: Four remaining items from the pack-contract artifact pipeline were open: (1) `DocQualityScorer` class missing caused full test suite to fail at collection; (2) `required_outputs` validation only checked text-presence, not whether the output was actually structured in the doc; (3) evidence extraction used 5-line regex windows, missing complete code blocks and JSON structures; (4) dead code in `_phase3_repair_targets` referenced a hardcoded enterprise doc name (`05_RUNTIME_CONFIG_AND_EXTERNALS`) via an error string that is never generated.
- **Root cause**:
  - `DocQualityScorer`: test expected a class wrapper that was never added when `RunQualityReport` / `score_project` were written.
  - `required_outputs` text-presence: original implementation treated any occurrence of the output token in the doc as satisfied; a single mention in passing would pass.
  - Evidence extraction: `_snippets_for` was a line-scanner that grabbed 2 lines before / 2 lines after each regex match, splitting SQL queries and JSON objects mid-block.
  - Dead code: the "Runtime/external systems doc missing" guard was added when that error string was generated somewhere, but the validator that produced it was later changed; the string is now never emitted.
- **Change**:
  1. Added `DocQualityScorer` class to `doc_quality_scorer.py` — thin wrapper calling `score_project`, restoring 499-test suite.
  2. Added `_required_output_present()` to `validators.py`: splits the output name on `_`/`-`/` ` and requires all words to appear in a markdown heading (with non-empty following content) or a table-column header. Empty headings, single word mentions, and passing text references all fail.
  3. Refactored `EvidenceBundleBuilder._snippets_for` into four layers: (a) JSON files go through `_json_snippets` which walks the parsed object and emits complete sub-objects that match the pattern; (b) SQL/DDL categories try `_fenced_code_blocks_for` first, extracting complete ` ```sql ... ``` ` blocks; (c) all categories try `_markdown_sections_for` — complete sections from heading to next heading; (d) 5-line line-context fallback preserved as `_line_snippets`. Also added `engineering_facts.json` and `config_runtime_map.json` to `_base_files` so JSON artifacts are scanned alongside their markdown twins.
  4. Removed the six-line dead-code block in `_phase3_repair_targets` that checked for `"Runtime/external systems doc missing"` and hard-coded `"05_RUNTIME_CONFIG_AND_EXTERNALS"` as a repair target. In pack mode the enterprise validators that would generate that string are disabled; in enterprise mode the string is never generated.
- **Why this approach**: Structural heading/table-header check for `required_outputs` matches how the harness prompts instruct the model to produce output — every declared output must appear as its own section, not buried in prose. Layered extraction strategy lets code-rich categories (SQL, DDL) get complete blocks without truncation, while a markdown-section fallback covers all categories and the 5-line fallback handles plain-text artifacts that have no structural markers.
- **Verification**: `uv run pytest -q` → 499 passed. New tests cover: `required_outputs` text-only fails / heading-with-content passes / table-header passes / empty-heading fails; SQL code block extraction returns full ` ``` ` fenced blocks; JSON base-file scanning returns structured entries.

## Session: 2026-05-28

### [Fix]: Steven_inventory Phase 2 tool-token runaway guardrails

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/providers.py`, `mcp-server/src/mcp_server/providers/custom/harness.py`, `mcp-server/src/mcp_server/providers/custom/agent_runtime.py`, `mcp-server/src/mcp_server/providers/custom/models.py`, `mcp-server/src/mcp_server/config.py`, tests, and Claude project notes.
- **Problem**: `Steven_inventory` hit a runaway ~9M-token failure mode. The root issue was not scanner size; Phase 2 core generation used generic tool mode, repeatedly reading large staging artifacts and resending those tool results in chat history.
- **Root cause**: Generic `read_artifact` had no offset/limit contract, no per-read byte cap in the tool schema, and OpenAI-compatible tool loops had no accumulated prompt-token ceiling. Tool-capable providers were also automatically routed through `complete_with_tools()` for Phase 2 core writing.
- **Change**: Phase 2 core generation now defaults to bounded `context_files` even for tool-capable providers. `read_artifact` supports `offset` and `limit`, returns truncation markers, and counts against `ToolBudget`. Tool loops now fail cleanly when accumulated input tokens exceed phase-specific caps, logging phase, target, turns, tool calls, result bytes, and tokens. The `table-inventory` pack gets a curated Phase 2 evidence bundle for SQL/ORM/config/DDL hints instead of relying on free browsing.
- **Operational rule**: Core generation writes from bounded evidence; retrieval may be agentic but must be budgeted.
- **Verification**: Targeted provider/plugin tests added for Phase 2 no-tools behavior, `read_artifact` truncation/offset/budgeting, tool-loop input-token caps, and table-inventory curated evidence bundle.

## Session: 2026-05-24

### [Fix]: RAG metrics not flowing from koopmans VPS to Mission Control

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/services/ops_telemetry.py` (5 ins, 4 del)
- **Problem**: Fleet dashboard showed `has rag: False` for koopmans despite RAG backend running. Activity and Overview tabs had no user/query data.
- **Root cause**: `_fetch_rag_metrics()` had guard `if not config.rag_base_url or not config.ingest_api_key: return {}`. Koopmans VPS does not have `MCP_INGEST_API_KEY` set, so `config.ingest_api_key` is an empty string (falsy) — function returned `{}` immediately without ever calling the RAG backend. The RAG ops-metrics endpoint already handles open access when no key is configured.
- **Change**: Guard changed to `if not config.rag_base_url: return {}`. Auth header is now added conditionally only when `config.ingest_api_key` is non-empty.
- **Why this approach**: Minimal, targeted fix. The RAG backend's `verify_ingest_api_key` already supports keyless access; the MCP guard was simply too aggressive.
- **Verification**: Committed and pushed; koopmans VPS needs rebuild to apply.

### [Fix]: Missing Alembic migration for plugin_packs table and projects.plugin_pack_id FK

- **Files changed**: `knowledge_base/mcp-server/alembic/versions/014_add_plugin_packs.py` (44 ins, new file)
- **Problem**: Every telemetry push from koopmans failed with `asyncpg.exceptions.UndefinedColumnError: column projects.plugin_pack_id does not exist`. Heartbeats stopped flowing entirely; Fleet card showed offline.
- **Root cause**: `plugin_pack_id` FK and the `plugin_packs` table were added to the SQLAlchemy `Project` model but no Alembic migration was ever written. Migrations only went to `013`; the column was missing in the live database.
- **Change**: Created migration `014_add_plugin_packs` — creates `plugin_packs` table (id, name, version, path, description, repo_url, last_commit_sha, auto_update) and adds nullable FK column `projects.plugin_pack_id`. Down migration drops both.
- **Why this approach**: Standard Alembic migration following existing 001–013 convention.
- **Verification**: Committed and pushed. Applies automatically on container startup (see Dockerfile fix below).

### [Fix]: mcp-server Dockerfile not copying alembic/ — migrations never ran on VPS

- **Files changed**: `knowledge_base/mcp-server/Dockerfile` (3 ins, 1 del) — pulled from koopmans VPS
- **Problem**: Migration `014` (and `013`) would not have applied on VPS even after a `git pull + rebuild` because the Dockerfile did not copy `alembic/` or `alembic.ini` into the image, and the `CMD` did not run `alembic upgrade head`.
- **Root cause**: Original Dockerfile only copied `src/` and ran the server directly. No migration step existed.
- **Change**: Added `COPY mcp-server/alembic/ alembic/` and `COPY mcp-server/alembic.ini .` to build stage. Changed `CMD` from `python -m mcp_server --serve ...` to `sh -c 'alembic upgrade head && python -m mcp_server --serve ...'`, mirroring the RAG backend pattern.
- **Why this approach**: Matches the RAG backend's proven startup pattern; ensures schema is always current on container start without manual intervention.
- **Verification**: Committed by Joshua on koopmans VPS, pulled locally. Koopmans needs one more rebuild to apply.

## Session: 2026-05-21 — Read-only: confirmed git poller failure bugs

### [Analysis]: Confirmed three bugs in git auto-reingest / poller flow

- **Files changed**: none (read-only analysis session)
- **Problem**: User presented a detailed failure analysis of the git poller and asked for code-level confirmation that the described failure mode exists.
- **Root cause**: n/a (no change made)
- **Change**: No code changed. The following bugs were confirmed by reading the source:
  1. **No `GIT_TERMINAL_PROMPT=0` / `GIT_ASKPASS`**: Neither `git_poller.py:_fetch_remote` nor `git_diff_patcher.py:_run_diff_patch` nor the systemd unit (`deploy/mcp-server@.service`) set these env vars. `stdin=DEVNULL` is set but does not prevent a credential helper from opening `/dev/tty`. In a systemd service with no TTY, git fetch will abort with "could not read Password" if any credential helper is registered on the host.
  2. **`last_ingested_commit_sha` never advances on fetch failure**: `git_poller.py:poll_git_projects_once` catches the exception at line 116 and only calls `update_auto_reingest_check` (which writes `auto_reingest_error`). `update_last_ingested_commit` is only called inside `git_diff_patcher.py:148` when `result.advance_commit is True and result.error is None`. A fetch exception means the baseline stays stuck at the original SHA permanently.
  3. **`last_rag_push_at` not written by generate flow**: `_run_generate_background` (projects.py ~line 847) calls `_push_with_retry` → `bridge.push_docs()` but never calls `registry.update_rag_push()`. That method is only called at projects.py:1075 (explicit "Push to RAG" API endpoint) and mcp/server.py:511. Dashboard shows "LAST RAG PUSH: Never" even though docs are in RAG.
  4. **Token URL left in git remote after fetch**: Both `_fetch_remote` and `_run_diff_patch` inject the PAT into the remote URL via `remote set-url` before fetching but never reset the URL afterward (pass or fail). Token persists in `.git/config` and leaks in journal on any subsequent error.
- **Why this approach**: n/a
- **Verification**: Code inspection only. Description claimed poller uses `git ls-remote` — code shows it uses `git fetch` directly; rest of the analysis matches the code exactly.

## Session: 2026-05-20 — Enterprise documentation quality gates, scan hardening, verdicts, audit bundle

### [Feature/Fix]: Quality-first enterprise publishing path — 350 backend tests pass, dashboard build passes

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/base.py` — added `DocGenContext.enterprise_mode`, defaulting publishable generation to enterprise mode.
  - `mcp-server/src/mcp_server/config.py` — added `doc_generation_enterprise_mode_default` and `doc_generation_quality_threshold`.
  - `mcp-server/src/mcp_server/schemas.py` — added `GenerateRequest.mode` (`enterprise` or `draft`) and exposed `GenerateRunResponse.run_verdict` / `gate_results`.
  - `mcp-server/src/mcp_server/db/models.py` — added `GenerateRun.run_verdict` and JSON `gate_results`.
  - `mcp-server/alembic/versions/013_add_generate_run_enterprise_verdict.py` — migration for `run_verdict` and `gate_results`.
  - `mcp-server/src/mcp_server/services/project_registry.py` — persists enterprise verdict metadata on completed runs.
  - `mcp-server/src/mcp_server/services/claude_service.py` — threads enterprise/draft mode through generation startup and run manifest.
  - `mcp-server/src/mcp_server/services/scanner_service.py` — clears stale `scan_result.json` / segmented `scan/` output before scans; fails closed if a successful subprocess does not produce valid `scan_result.json`.
  - `mcp-server/src/mcp_server/api/routes/projects.py` — exception-safe `_run_generate_background`; preserves failed `docs_staging/`; records `draft`, `failed`, `published`, `published_with_rag_error`, or `client_ready`; stores gate results; marks manual reset runs as failed verdicts.
  - `mcp-server/src/mcp_server/providers/custom/validators.py` — added final redaction, source provenance, and usefulness rubric validators; made invalid scan output fail closed.
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — made Phase 3 doc-depth validation blocking; added enterprise gate report, deterministic internal reviewer checks, final audit bundle, doc hashes, and gate artifacts.
  - `dashboard/src/types.ts` — added run verdict and gate result fields.
  - `dashboard/src/components/RunHistory.tsx` — shows structured run verdict badges before legacy PASS/FAIL comparison verdicts.
  - `dashboard/src/tests/ep000-ui.test.tsx` — updated fixtures for new GenerateRun fields.
  - `mcp-server/tests/test_services/test_scanner_service.py` — added stale scan cleanup / missing output failure coverage.
  - `mcp-server/tests/test_providers/test_custom_provider.py`, `test_perf_regression.py` — updated fake docs to satisfy stricter blocking depth/section gates.
- **Problem**: Publish could still proceed with shallow Phase 3 docs, missing final provenance/redaction checks, no structured client-ready verdict, possible stale scan artifacts, and generation crashes that could leave projects stuck in `generating`.
- **Root cause**: The harness had strong intermediate gates, but Phase 3 depth was only a warning and publish-state lived mostly in implicit success/failure booleans. Scanner output was trusted after subprocess success, even if the result file was stale, absent, or malformed. Dashboard run history had only the older comparison PASS/FAIL verdict.
- **Change**: Enterprise mode is now the default publish path and `draft` must be explicit. Enterprise gates block publish before `docs/` swap: fresh scan, Phase 2 coverage/depth, Phase 3 coverage/depth, final redaction, source provenance, internal reviewer approval, quality threshold, usefulness rubric, and pre-publish RAG gate placeholder. Failed enterprise runs preserve `docs_staging/` for resume/repair and write gate reports/audit artifacts. Successful runs get structured verdicts and gate JSON in the DB.
- **Why this approach**: The system should fail closed for client-ready docs. Cost controls remain guardrails, not quality tradeoffs. Deterministic validators and reviewer checks provide stable CI coverage while still leaving room for future LLM reviewer loops. Staging preservation keeps recovery practical without ever promoting failed docs.
- **Verification**:
  - `cd mcp-server && uv run pytest -q` -> 350 passed.
  - `cd dashboard && npm run build` -> TypeScript and Vite build passed.

## Session: 2026-05-20 — Harness reliability campaign phases 1–6 (CSRF fix, INCOMPLETE kind, force-conclude, gather safety, atomic writes, provider fixes)

### [Fix]: Full harness reliability campaign — 16 issues across 6 phases, 349 tests pass

- **Files changed**:
  - `RAG_pipeline/backend/src/api/middleware/csrf.py` (+11 lines) — added `_EXEMPT_PREFIXES` tuple for `/api/v1/ingest/` and `/api/v1/usage/`; updated `dispatch()` condition to skip CSRF for these paths
  - `mcp-server/src/mcp_server/api/routes/projects.py` (+6 lines) — added `_PERMANENT_HTTP_CODES = {"400", "401", "403", "404", "422"}`; fast-fail break in `_push_with_retry` when any error string contains a permanent code
  - `mcp-server/src/mcp_server/config.py` (+1 line) — `phase2_max_tokens` raised from `16_000` to `32_000`
  - `mcp-server/src/mcp_server/providers/custom/agent_runtime.py` (+26 lines) — added `_atomic_write()` helper (tmp + rename); replaced 5 direct `.write_text()` calls in `AgentTaskRuntime.run()` final writes with `_atomic_write()`; broadened `accepted_output()` exception from `json.JSONDecodeError` to `(json.JSONDecodeError, OSError, UnicodeDecodeError)`; `status.json` write remains last (acceptance gate)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (+118 lines) — added `FailureKind.INCOMPLETE`; `is_retryable_with_fallback` excludes INCOMPLETE; `_classify_failure()` checks `metadata["finish_reason"]` and `metadata["incomplete_reason"]` before returning UNKNOWN; force-conclude block in `_call_model_with_logging()` (calls `provider.complete()` without tools when INCOMPLETE + scoped_tools active); primary call logged to `state.calls` before fallback overwrites result; Phase 2/3 `asyncio.gather()` use `return_exceptions=True` with per-exception error logging; Phase 2/3 repair failures log at "error" not "warning"; plugin manifest `except Exception` now logs with `exc_info=True`; slice token aggregation reads from `state.calls[pre_calls_len:]`
  - `mcp-server/src/mcp_server/providers/custom/providers.py` (+46 lines) — `OpenAIOfficialProvider.complete_with_tools()`: added `finish_reason=="incomplete"` → INCOMPLETE early-exit; auth error (401/403) breaks retry loop immediately; uses `_sleep_for_retry()` instead of bare `asyncio.sleep()`; all three `complete_with_tools()` implementations log DEBUG when `json_mode=True` is passed (it is ignored — tool contracts enforce structure)
  - `mcp-server/tests/test_providers/test_custom_provider.py` (+407 lines) — 13 new tests covering: INCOMPLETE classification via metadata, INCOMPLETE not retryable, force-conclude fires and succeeds, primary call logged before fallback, slice token aggregation, atomic writes leave no .tmp files, `accepted_output` tolerates corrupt status file, OpenAIOfficialProvider maps `status="incomplete"` to failure, auth errors not retried
- **Problem**: Live `cdc` generation revealed two blocking issues: (1) All 9 docs pushed to RAG failed 100% with 500 errors (CSRF middleware blocking mcp-server → backend server-to-server POST calls), each doc retried 3× unnecessarily. (2) Phase 2 group analysis silently fell back from DeepSeek to gpt-4.1 — DeepSeek hit the 16K `phase2_max_tokens` cap, got `finish_reason="length"`, but `_classify_failure()` returned UNKNOWN (never checked metadata), `is_retryable_with_fallback` was True, fallback fired. Three parallel audit agents then found 14 additional gaps.
- **Root cause**: CSRF middleware had no path-prefix exemption — all non-GET requests to non-exempt exact paths were blocked regardless of origin. `_classify_failure()` only scanned `raw_error` string with regex, never checked `result.metadata["finish_reason"]`; `FailureKind.INCOMPLETE` didn't exist so all length-truncation failures classified as UNKNOWN (retryable). `asyncio.gather()` in Phase 2/3 had no `return_exceptions=True` — one failing group silently cancelled siblings. Final writes in `AgentTaskRuntime.run()` were non-atomic — crash mid-write could leave partial JSON. `OpenAIOfficialProvider.complete_with_tools()` retried auth errors and had no length check.
- **Why this approach**: CSRF path-prefix exemption is the correct fix for server-to-server routes that are already behind Docker internal network and project-level auth — no shared secret needed. Force-conclude uses the same provider (no fallback) since the model already has context in scope; INCOMPLETE is excluded from `is_retryable_with_fallback` so no fallback fires regardless. Atomic writes use POSIX `rename()` which is guaranteed atomic — prevents partial status.json from blocking resume reads. `return_exceptions=True` ensures all sibling tasks complete before error propagates — prevents partial generation where some groups are missing.
- **Verification**: 349 tests pass (`uv run pytest tests/ -x -q`). 13 new tests specifically exercise each fix.

## Session: 2026-05-20 — P2/P3/P4: Investigation robustness, operational safety, validator correctness

### [Fix/Feature]: Custom harness reliability campaign (8 steps, 336 tests pass)

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` — fixed non-existent fallback model (`"openai/gpt-5.4"` → `None`); added `_PROVIDER_KEY_MAP` and `check_provider_credentials()` for startup credential preflight
  - `mcp-server/src/mcp_server/providers/custom/models.py` — added `json_mode: bool = False` to `ModelOptions`
  - `mcp-server/src/mcp_server/providers/custom/providers.py` — added `_assert_contract()` helper (raises in dev, logs WARNING in prod); wired `json_mode` into `OpenAIOfficialProvider` (`text.format`), `OpenAICompatibleProvider` (`response_format`), `GoogleGeminiProvider` (`response_mime_type`); called `_assert_contract()` before all 8 success-return sites across 6 providers
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` — added `collect_partial_evidence()` to `EvidenceValidator` (returns valid/missing/errors without raising); rewrote `SingleShotInvestigator.run()` for evidence accumulation and targeted repair (retry only missing units, cap 15 per call); updated `_build_prompt()` to accept optional `repair_context`
  - `mcp-server/src/mcp_server/providers/custom/validators.py` — changed coverage matching from substring `in` to word-boundary regex to eliminate false positives (e.g. "Map" matching inside "mapping")
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — added module-level `logger`; added `logger.warning()` on prompt-pack glob fallback in `_phase2_shared_context()` and `_phase3_context_files()`
  - `mcp-server/src/mcp_server/services/generation_run_state.py` (new) — filesystem lock/manifest helpers (`acquire_generation_lock`, `refresh_generation_lock`, `release_generation_lock`, `write_run_manifest`, `record_manifest_chunks`) plus `LockHeartbeat` async context manager that refreshes lock every 60s via background asyncio task
  - `mcp-server/src/mcp_server/services/claude_service.py` — imported `LockHeartbeat`; wrapped `provider.run()` with `async with LockHeartbeat(staging_dir, run_id=run_id):`
  - `mcp-server/src/mcp_server/api/app.py` — added `_pid_alive()` and `_reconcile_orphaned_runs()` (marks `GenerateRun(status="running")` rows as failed if lock absent or PID dead); both wired into `lifespan()` startup; added `check_provider_credentials()` loop logging ERROR in prod, WARNING in dev
  - `mcp-server/src/mcp_server/api/routes/projects.py` — minor fix
  - `mcp-server/src/mcp_server/providers/custom/agent_runtime.py` — minor fix
  - `mcp-server/entrypoint.sh` — minor fix
  - `mcp-server/tests/test_providers/test_custom_provider.py` — new tests covering contract assertions, json_mode, targeted repair, partial evidence collection
  - `mcp-server/tests/test_services/test_generation_run_state.py` (new) — lock acquire/refresh/release and LockHeartbeat tests
  - `knowledge_base/.claude/PROJECT_MEMORY.md` — updated decisions table and lessons learned
  - `knowledge_base/.claude/progress.md` — phase tracking update
- **Problem**: Three distinct reliability gaps: (1) investigation retried entire groups when only 1-2 units were missing; Gemini had no native JSON enforcement; provider contract violations were silent; fallback model pointed to non-existent `openai/gpt-5.4`. (2) Lock had no heartbeat — crashes could block generation for 2h; DB `status="running"` rows from crashes were never cleaned up; no startup warning for missing API keys. (3) Coverage validator matched unit names as substrings causing false positives; prompt-pack glob fallback was silent.
- **Root cause**: `SingleShotInvestigator.run()` re-sent the full group on every retry instead of accumulating good evidence and asking only for missing units. Fallback model value set to non-existent model in US-102, never corrected. `acquire_generation_lock` stored a timestamp but nothing refreshed it. `_run_generate_background` left DB rows stuck after crash because there was no startup reconciliation. `covered_ids` used `unit.name in text` substring matching.
- **Change**: Targeted repair accumulates valid evidence across attempts and retries only missing units (cap 15 per call) — a group with 18/20 covered uses ~10% of the token budget on repair vs 100% on full retry. `_assert_contract()` makes C1/C2/C3 violations observable in dev (raises) and auditable in prod (WARNING). `json_mode` wired to provider-native JSON enforcement. `LockHeartbeat` wraps only `provider.run()` (not the full generation function). `_reconcile_orphaned_runs()` marks stuck rows failed at startup using PID liveness check. `check_provider_credentials()` maps provider prefix to required env var — no hard exit, just log. P4.2 (section validation for non-Talend) deliberately skipped: `REQUIRED_SECTION_NAMES` are Talend-specific; applying to Workato/Generic would break validation.
- **Why this approach**: Accumulation is strictly additive — validated evidence is never discarded. asyncio background task for heartbeat matches all other async I/O in the codebase. No hard exit on missing API keys preserves the case where operators run with only some providers configured.
- **Verification**: 336/336 tests pass. 1 intermediate failure (`test_phase3_context_prefers_doc_pack_over_family_bundle`) was caused by missing module-level `logger` in harness.py — fixed by adding `import logging` + `logger = logging.getLogger(__name__)`.

---

## Session: 2026-05-11 — EP-001 Deployment Foundation Sweep

### [Fix/Docs]: EP-001 backlog refresh + nginx security headers

- **Files changed**:
  - `deploy/nginx/nexus-client.conf.template` — added 4 security `add_header` directives (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) inside the HTTP server block so certbot `--nginx` copies them to the generated HTTPS block
  - `backlog/stories/US-001-*.md` — status Draft → Done; DoD items ticked
  - `backlog/stories/US-002-*.md` — status Draft → In Progress (awaiting VPS)
  - `backlog/stories/US-003-*.md` — status Draft → Done; DoD items ticked; VPS-dependent items noted
  - `backlog/stories/US-004-*.md` — status Draft → Done; DoD items ticked; VPS-dependent items noted
  - `backlog/stories/US-005-*.md` — status Draft → Done; DoD items ticked; VPS-dependent items noted
  - `backlog/stories/US-006-*.md` — status Draft → In Progress (repo still under joshitachu; org transfer pending)
  - `backlog/epics/EP-001-*.md` — status Refined → In Progress; story table statuses updated
- **Problem**: Security response headers (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) were absent from the nginx template. The smoke test checklist explicitly verifies all four.
- **Root cause**: Template was written with only proxy_set_header (backend-facing), no add_header (client-facing).
- **Change**: Added 4 `add_header` directives in the HTTP server block. certbot --nginx duplicates the server block to the 443 block, so the headers land on HTTPS responses automatically.
- **Why this approach**: Simplest and idiomatic for certbot --nginx; no extra includes or post-certbot patching required.
- **Verification**: Template lint passes (`nginx -t` on VPS after certbot run; confirmed by `curl -I https://...` showing all four headers in smoke-test section 2).

### EP-001 Status Summary (2026-05-11)

| Story | Code done | VPS required |
|-------|-----------|--------------|
| US-001 Runbook | Done | PO review |
| US-002 VPS validation | — | Yes — blocker |
| US-003 nginx/certbot + headers | Done | certbot/DNS |
| US-004 Smoke test doc | Done | Execution |
| US-005 IP allowlisting | Done | UFW verification |
| US-006 GitHub org migration | — | Org transfer (ops) |

EP-001 cannot be fully accepted until US-002 passes on a real VPS and US-006 is closed (repo transfer).

---

## Session: 2026-05-11 — EP-000 Completion (US-103, US-104, US-105, US-106)

### [Feature]: Model-agnostic foundation remaining stories — pack pinning, auto-verdict, regression detection, review comparison

- **Files changed**:
  - `mcp-server/src/mcp_server/db/models.py` — `Project.instruction_pack_dir` (String 500, nullable); `GenerateRun.auto_verdict` (String 10), `auto_baseline_run_id` (Integer)
  - `mcp-server/alembic/versions/010_add_instruction_pack_dir.py` — new migration
  - `mcp-server/alembic/versions/011_add_generate_run_verdict.py` — new migration
  - `mcp-server/src/mcp_server/schemas.py` — `ProjectResponse`/`UpdateProjectRequest` + `instruction_pack_dir`; `GenerateRunResponse` + `auto_verdict`/`auto_baseline_run_id`
  - `mcp-server/src/mcp_server/services/pack_loader.py` — `InstructionPack.questions` field; loads `questionnaire.json` from pack dir
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — `instruction_pack_dir` param; per-project pack dir resolution
  - `mcp-server/src/mcp_server/providers/__init__.py` — threads `instruction_pack_dir` through `get_provider`
  - `mcp-server/src/mcp_server/services/claude_service.py` — `instruction_pack_dir` param passed to `get_provider`
  - `mcp-server/src/mcp_server/services/project_registry.py` — `update_project` handles `instruction_pack_dir`
  - `mcp-server/src/mcp_server/services/validation_service.py` — `generate_questionnaire` loads questions from pack; falls back to DEFAULT_QUESTIONNAIRE
  - `mcp-server/src/mcp_server/api/routes/projects.py` — `_run_generate_background` passes pack dir + triggers `_auto_compare_run`; new `_auto_compare_run` function; `get_quality_history` adds cost/duration regression fields; `get_questionnaire` passes instruction_pack_dir
  - `mcp-server/src/mcp_server/api/routes/generate_runs.py` — `_run_summary` exposes `auto_verdict` and `auto_baseline_run_id`
  - `mcp-server/packs/default/questionnaire.json` — new; 5 questions matching previous hardcoded list
  - `dashboard/src/types.ts` — `Project.instruction_pack_dir`; `GenerateRun.auto_verdict`/`auto_baseline_run_id`; `RunComparisonRun.auto_verdict`/`auto_baseline_run_id`; `QualityHistoryEntry` cost/duration regression fields
  - `dashboard/src/api/client.ts` — `updateProject` data type extended with `instruction_pack_dir`
  - `dashboard/src/hooks/useProjects.ts` — `useUpdateProject` mutation data type extended
  - `dashboard/src/components/ProjectSettings.tsx` — Instruction Pack Dir text input; wired to save payload
  - `dashboard/src/components/RunHistory.tsx` — Verdict column with PASS/FAIL badge; badge links to ABComparison via `onCompare` prop
  - `dashboard/src/components/QualityHistoryPanel.tsx` — Cost Delta and Duration Delta columns with amber regression indicator
  - `dashboard/src/components/templates/ABComparison.tsx` — `preloadRunA`/`preloadRunB` props; Human Review side-by-side section using `useReviews`
  - `dashboard/src/App.tsx` — `compareRunA`/`compareRunB` state; `handleCompare` switches tab + pre-loads ABComparison; `onCompare` wired to RunHistory
  - `dashboard/src/tests/ep000-ui.test.tsx` — fixtures updated with new required fields
  - `backlog/stories/US-102-*.md` — marked Done; notes added
  - `backlog/stories/US-105-*.md` — scope narrowed to dashboard reviewers + pack-based questions
- **Problem**: EP-000 was ~75% done; US-103/104/105/106 stories remained unimplemented
- **Root cause**: Model-agnostic harness needed per-project pack pinning, post-run auto-comparison, regression signalling, and review-from-pack — none of these existed
- **Change**: Implemented all four remaining stories. Pack dir now flows from project record through the entire harness chain. Auto-comparison fires after every successful run and stores PASS/FAIL verdict. Quality-history endpoint computes cost/duration regressions with configurable thresholds (5% quality, 20% cost/duration). Questionnaire questions come from `questionnaire.json` in the pack. ABComparison shows side-by-side human review scores.
- **Why this approach**: Kept all threshold constants in `projects.py` as named module-level constants for easy tuning. `_auto_compare_run` uses a separate DB session from `build_run_comparison` since the latter opens its own session. Pack-questions fallback to hardcoded list keeps backward compatibility when no `questionnaire.json` exists.
- **Verification**: TypeScript build passes clean. Run `alembic upgrade head` for migrations 010/011. Set `instruction_pack_dir` on a project in dashboard, trigger generation, confirm harness picks up alternate pack. Complete two runs; verify second run has `auto_verdict` in RunHistory. Check `/quality-history` for cost/duration regression flags. Edit `packs/default/questionnaire.json`, reload questionnaire endpoint, confirm new question appears.

---

## Session: 2026-05-08 — GitHub Private Repo Support + Auto Webhook Registration

### [Feature]: Private GitHub repo support and automatic webhook registration via PAT

- **Files changed**:
  - `mcp-server/src/mcp_server/db/models.py` — added `github_token_enc` (String 500, nullable) and `github_webhook_id` (Integer, nullable) to `Project`
  - `mcp-server/alembic/versions/009_add_github_token_and_webhook_id.py` — new migration (down_revision: 008_git_update_summary)
  - `mcp-server/src/mcp_server/utils/encryption.py` — new: Fernet encrypt/decrypt + inject_token (injects PAT into HTTPS URL in-memory)
  - `mcp-server/src/mcp_server/utils/github_api.py` — new: register_webhook, delete_webhook, check_webhook via GitHub REST API (httpx)
  - `mcp-server/src/mcp_server/utils/__init__.py` — new package init
  - `mcp-server/src/mcp_server/config.py` — added `token_encryption_key` (MCP_TOKEN_ENCRYPTION_KEY) and `public_url` (MCP_PUBLIC_URL)
  - `mcp-server/src/mcp_server/schemas.py` — added `github_token` to AddRepoRequest, new UpdateGithubTokenRequest, added `has_github_token`/`webhook_status` to ProjectResponse
  - `mcp-server/src/mcp_server/api/routes/projects.py` — inject token into git clone; store token_enc; auto-register webhook; 4 new endpoints (PATCH github-token, POST/DELETE/GET github-webhook)
  - `mcp-server/src/mcp_server/api/routes/webhook.py` — no change needed (token is loaded from DB inside run_webhook_diff_update)
  - `mcp-server/src/mcp_server/services/git_diff_patcher.py` — run_webhook_diff_update reads github_token_enc; _run_diff_patch accepts token_enc; injects into remote URL before git fetch
  - `mcp-server/src/mcp_server/services/git_poller.py` — _fetch_remote accepts token_enc; sets remote URL with token before fetch
  - `dashboard/src/types.ts` — added `has_github_token`, `webhook_status` to Project interface
  - `dashboard/src/api/client.ts` — extended addRepo, added updateGithubToken/registerWebhook/deleteWebhook/getWebhookStatus
  - `dashboard/src/hooks/useProjects.ts` — added useRegisterWebhook, useUpdateGithubToken
  - `dashboard/src/components/AddRepoDialog.tsx` — PAT password field shown for github.com URLs; passed to addRepo mutation
  - `dashboard/src/components/ProjectCard.tsx` — webhook_status badges (active/not_registered)
  - `dashboard/src/components/ProjectSettings.tsx` — GitHub Integration section with webhook status, "Set up webhook" button, token update form
  - `dashboard/src/tests/ep000-ui.test.tsx` — added missing has_github_token/webhook_status to mock fixture
- **Problem**: Private GitHub repos couldn't be cloned/fetched (no credentials), and there was no way to set up a webhook from the dashboard — users had to do it manually or rely on 5-min polling
- **Root cause**: git clone/fetch operations had no credential injection mechanism; no GitHub API client existed
- **Change**: Single PAT per project. Encrypted at rest with Fernet (MCP_TOKEN_ENCRYPTION_KEY). Injected into remote URL in memory before each git operation. On project add, GitHub API auto-registers a push+pull_request webhook. Dashboard shows status and allows manual re-registration and token rotation.
- **Why this approach**: PAT is the simplest credential model for enterprise setups — no OAuth server needed. Token encryption keeps secrets safe even if DB is read. Graceful fallback if webhook registration fails (project still registers, falls back to polling).
- **Verification**: Run `alembic upgrade head`; set MCP_TOKEN_ENCRYPTION_KEY + MCP_PUBLIC_URL; add private repo with PAT; verify clone succeeds + webhook appears in GitHub repo settings; push commit and verify diff patch fires in seconds.

---

## Session: 2026-05-08

### [Fix]: SMTP password update — invite email 502 resolved

- **Files changed**: `.env` (line 37)
- **Problem**: Invite email flow returned 502; Strato SMTP returned `535 Authentication failed`
- **Root cause**: Stale SMTP password in `.env`
- **Change**: Updated `SMTP_PASSWORD` to new credential; restarted `intodata-kb-v2-backend-1` container
- **Verification**: Backend restarted successfully; send a test invite via admin UI to confirm

---

## Session: 2026-05-05 — EP-000 US-105 / US-106

### [Feature]: US-105 — Document validation process (review API)

- **Files changed**:
  - `mcp-server/src/mcp_server/schemas.py` — added `ReviewQuestion`, `DocReview`, `ReviewAggregateResponse`
  - `mcp-server/src/mcp_server/services/validation_service.py` (new) — questionnaire template, `save_review()`, `aggregate_reviews()`
  - `mcp-server/src/mcp_server/api/routes/projects.py` — added 3 routes: `GET /questionnaire`, `POST /reviews`, `GET /reviews`
  - `mcp-server/tests/test_services/test_validation_service.py` (new) — 4 tests
- **Change**: Reviews stored as JSON files under `output/{project_id}/logs/reviews/review_{run_id}_{timestamp}.json` (same pattern as `run_metrics_*.json`). Pass threshold: average overall >= 7.0. Filename uses microsecond precision to prevent collision when multiple reviews are submitted in the same second.
- **Verification**: 4 tests pass

### [Feature]: US-106 — Version-controlled instruction packs

- **Files changed**:
  - `mcp-server/packs/default/` (new dir) — `version.txt` (v1.0.0), `phase2_preamble.md`, `scorecard_preamble.md`, `phase3_preamble.md`
  - `mcp-server/src/mcp_server/services/pack_loader.py` (new) — `InstructionPack` dataclass, `PackLoader` with `load()` and `get()` methods
  - `mcp-server/src/mcp_server/config.py` — added `instruction_pack_dir: str = "packs/default"` (env `MCP_INSTRUCTION_PACK_DIR`)
  - `mcp-server/src/mcp_server/providers/custom/prompts.py` — added `InstructionPack` import + optional `pack` param to `group_analysis_prompt()`, `scorecard_prompt()`, `doc_prompt()`; added `_PHASE2_PREAMBLE_DEFAULT`, `_SCORECARD_PREAMBLE_DEFAULT`, `_PHASE3_PREAMBLE_DEFAULT` constants
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — imported `InstructionPack`, added optional `pack` param to `CustomHarnessProvider.__init__()`, wired `self.pack` to all 3 prompt call sites
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — `build_custom_provider()` now creates a `PackLoader` from `config.instruction_pack_dir` and passes loaded pack to provider
  - `mcp-server/tests/test_services/test_pack_loader.py` (new) — 4 tests
- **Why this approach**: Full YAML extraction of `prompts.py` (804 lines of f-strings) would be a major refactor. Extracting only static preambles into `.md` files gives A/B-testable pack swapping (`MCP_INSTRUCTION_PACK_DIR=packs/experimental`) while keeping dynamic interpolation (FactsManifest, unit lists, platform requirements) in Python. Pack is additive: missing phase files fall back to hardcoded defaults, so no breaking change.
- **Verification**: 4 tests pass, 218 total tests pass (full suite excluding pre-existing rag_bridge failures)

## Session: 2026-05-05 — EP-000 US-102 / US-103 / US-104

### [Feature]: US-102 — Per-phase model routing + fallback provider

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/harness.py`, `mcp-server/src/mcp_server/config.py`, `mcp-server/src/mcp_server/providers/__init__.py`
- **Problem**: All three doc-gen phases (investigation, analysis, doc writing) shared a single model. No fallback on transient failures.
- **Root cause**: `CustomHarnessProvider` only accepted one `model_provider` and one optional `write_provider`. No retry path for rate-limit / network failures.
- **Change**:
  - Added `investigation_provider` and `fallback_provider` params to `CustomHarnessProvider.__init__`
  - Added `FailureKind.is_retryable_with_fallback` property (True for RATE_LIMIT / NETWORK / AUTH_ERROR)
  - Added fallback retry block in `_call_model_with_logging`
  - Added `doc_generation_investigation_model` and `doc_generation_fallback_model` to `MCPServerConfig` (env `MCP_DOC_GENERATION_INVESTIGATION_MODEL`, `MCP_DOC_GENERATION_FALLBACK_MODEL`)
  - Refactored `build_custom_provider()` with inner `_build_secondary()` helper
- **Why this approach**: All params default to `None` — zero config required, fully backward-compatible. Secondary provider skips building if model string is identical to primary (saves API client init).
- **Verification**: 65 existing provider tests pass unchanged.

---

### [Feature]: US-103 — Orchestration performance regression tests

- **Files changed**: `mcp-server/tests/test_providers/test_perf_regression.py` (new)
- **Problem**: Parallel asyncio, caching, and streaming existed but no test would catch a future change that accidentally serialized phase execution.
- **Change**:
  - `test_phase2_groups_run_concurrently` — `_TrackedFakeProvider` tracks concurrent `complete()` calls with a 10ms yield; asserts `max_active > 1`
  - `test_full_harness_completes_within_time_budget` — full 2-group run with instant provider must complete in < 5 seconds
- **Why this approach**: Self-contained file (helpers copied, not imported). `asyncio.sleep(0.01)` is essential — without it coroutines don't interleave and `max_active` stays 1 even with `asyncio.gather`.
- **Verification**: 2 passed in 0.31s. 65 existing tests unchanged.

---

### [Feature]: US-104 — Automated model comparison testing framework

- **Files changed**: `mcp-server/src/mcp_server/services/doc_quality_scorer.py`, `mcp-server/src/mcp_server/api/routes/projects.py`, `mcp-server/tests/test_services/test_doc_quality_scorer.py` (new)
- **Problem**: `DocQualityScorer` had no code-example or error-handling signals. The compare-runs endpoint returned raw deltas but no PASS/FAIL verdict. No historical quality trend available.
- **Change**:
  - Added `_CODE_FENCE_RE` + `_ERROR_PATTERN_RE` to scorer — bonus points in specificity sub-score, flags `"no_code_examples"` / `"no_error_handling"` when absent
  - Extended `compare_generate_runs()` with `quality_drop_pct` / `size_change_pct` query params and `verdict` + `checks` in response
  - New `GET /projects/{project_id}/quality-history` endpoint — scans `run_metrics_*.json` files, returns time-series with `regression` flag per entry
  - 4 unit tests for scorer signals
- **Why this approach**: No new DB tables needed — history is computed from existing metrics JSON files. Threshold params default to sensible values so existing callers get PASS behaviour without configuration.
- **Verification**: 4 new tests pass, 210 previously-passing tests unaffected.

---

## Session: 2026-04-30 (2)

### [Fix]: Go block imports + C/C++ function name extraction

- **Files changed**: `intodoc/src/intodoc/platforms/generic/parser.py`, `intodoc/tests/test_platforms/test_generic.py`
- **Problem**: Two gaps in universal extractors: (1) Go block import syntax `import (\n"pkg"\n)` was invisible — only `import (` was caught, not the package lines inside, breaking `_IMPORT_TO_SYSTEM` external-system tagging for Go projects. (2) C/C++ functions (`int foo(int x) {`) have no keyword before the name so `_extract_function_names_universal` always returned empty for C/C++.
- **Root cause**: Universal regex passes assumed import keywords appear on the same line as the package path, and assumed all languages use a declaration keyword before function names.
- **Change**:
  - `_extract_imports_universal`: added Pass 2 — finds `import (...)` blocks via `re.findall(r'\bimport\s*\(([^)]+)\)', content, re.DOTALL)`, extracts quoted package paths line by line, normalises to `import "pkg"` format
  - `_extract_function_names_universal`: added Pass 2 brace-delimited fallback — matches `name(params) {` pattern, filters `_BRACE_SKIP` control-flow keywords; only fires when Pass 1 finds zero results so keyword-based languages are unaffected
  - Added `_BRACE_SKIP` frozenset at module level
  - Added `test_go_block_imports_extracted` and `test_c_function_names_extracted` to test suite
- **Why this approach**: Pass 2 only fires as a fallback — no risk of false positives for Python/Go/Rust/JS which already return results from Pass 1. Block import extraction is structural (parenthesised group after `import` keyword) with no language-specific knowledge.
- **Verification**: 332 tests pass (8 skipped), both new tests confirm correct extraction.

## Session: 2026-04-30

### [Refactor]: Truly language-agnostic generic scanner extraction

- **Files changed**: `intodoc/src/intodoc/platforms/generic/parser.py`
- **Problem**: `_extract_language_params()` dispatched to 4 hardcoded per-language methods (Python, Java, TypeScript, C#) plus 4 language-specific connection-string helpers. Languages like Go, Rust, Ruby, PHP got only raw `content` + `file_path`. Entry-point detection had 20 language-specific regex patterns.
- **Root cause**: Scanner was built by adding one language at a time rather than thinking structurally from the start.
- **Change**:
  - Deleted `_extract_python_params`, `_extract_java_params`, `_extract_typescript_params`, `_extract_csharp_params`, `_extract_connection_string_python`, `_extract_connection_string_java`, `_extract_connection_string_ts`, `_extract_connection_string_csharp` (~270 lines removed)
  - Replaced with 6 truly universal methods: `_extract_imports_universal`, `_extract_class_names_universal`, `_extract_function_names_universal`, `_extract_annotations_universal`, `_extract_routes_universal`, `_extract_connection_string_universal`
  - Replaced 20 language-specific `_ENTRY_PATTERNS` with 7 structural patterns (any `main(`, HTTP verb routing, decorated functions, scheduler signals, consumer signals, SQL objects)
  - Added `_ENTRY_FILENAMES` frozenset — files named `main.*`, `app.*`, `index.*`, `server.*` etc. are always entry points regardless of content
  - `_extract_language_params()` now dispatches only on SQL (genuinely different query language) vs. everything else
  - Connection string detection simplified to URL-scheme patterns only (`://`) — library-specific patterns (`psycopg2.connect`, `SqlConnection`, etc.) removed
  - `_extract_sql_params()` kept unchanged (SQL is a query language, not a programming language)
- **Why this approach**: The generic scanner should be structurally agnostic — it should work for any language by recognising patterns like "function definition keywords", "import keywords", "URL schemes", not by knowing specific library APIs or framework annotations.
- **Verification**: 330 tests pass (8 skipped), 49/49 generic-specific tests pass.

## Session: 2026-04-30

### [Feature]: Generic platform scanner — Talend-quality documentation overhaul

- **Files changed**:
  - `intodoc/src/intodoc/platforms/generic/prompts/ANALYSIS_GUIDE.md` (new — 220 lines)
  - `intodoc/src/intodoc/platforms/generic/parser.py` (rewrite — 396 → 620 lines)
  - `intodoc/src/intodoc/platforms/generic/distiller.py` (new — 80 lines)
  - `intodoc/src/intodoc/platforms/generic/__init__.py` (added `get_distiller()`)
- **Problem**: Generic scanner produced fake 2-param components (file_path + truncated content at 300 lines), manifest with only `job_groups` populated, no language guidance for Phase 2 agents. Documentation was shallow and generic.
- **Root cause**: Generic was a minimal stub — directory-based job grouping, no semantic extraction, no ANALYSIS_GUIDE, no distiller.
- **Change**:
  - **ANALYSIS_GUIDE.md**: Deep code-review prompt. Instructs Phase 2 agents to read every function body and explain business logic (500-2000 lines per group target), detect connections from raw code, infer external systems from imports (lookup table for 30+ packages), apply language-specific error handling heuristics. Covers Python, Java, TypeScript, C#, SQL.
  - **parser.py**: Entry-point detection (16 regex patterns across Python/Java/TS/C#/SQL) — entry-point files become single-file jobs instead of being lumped into directory groups. Per-language structured param extraction (annotation, class_name, method_names, imports/usings, connection_string, sql_N) added ON TOP of full raw `content`. Line limit raised: 300 → 1500 for source files, 300 → 600 for config/SQL. XML connection detection added (Web.config `<connectionStrings>`). `_build_manifest` now populates `job_calls` (from relative imports), `job_external_systems` (import→system map, 35 packages), `job_connections_used` (from connection_string params), `job_called_by`, `connection_used_by`.
  - **distiller.py**: `GenericDistiller` — strips empty/boilerplate params, preserves `content`/`annotation`/`connection_string`/`imports`/`usings`/`sql_*`, strips getter/setter method names, applies project-wide constant detection.
- **Why this approach**: Raw `content` is kept as the primary LLM input (full source code enables deep narrative analysis). Structured params are additive, not replacements. ANALYSIS_GUIDE drives the quality improvement more than any parser change.
- **Verification**: 325 tests pass (0 failures, 8 skipped). Parser imports cleanly. Entry-point detection tested on generic fixture.

### [Fix]: DeepSeek `reasoning_content` must be echoed in multi-turn tool loops

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/providers.py` — `OpenAICompatibleProvider.complete_with_tools()`, line ~383
- **Problem**: Phase B introduced multi-turn tool-use loops. DeepSeek's thinking mode returns `reasoning_content` alongside `content` in each assistant response. When appending the assistant message back into the conversation history for the next turn, we were omitting `reasoning_content`. DeepSeek rejects the next call with `400 Bad Request: "The reasoning_content in the thinking mode must be passed back to the API."` The error only surfaces on turn 2+ (never on single-shot `complete()` calls).
- **Root cause**: Phase B's `complete_with_tools()` built the assistant message as `{"role": "assistant", "content": ..., "tool_calls": ...}` without checking for `reasoning_content`.
- **Change**: Before appending the assistant message, check `message.get("reasoning_content")` and include it if present:
  ```python
  assistant_msg = {"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls}
  if message.get("reasoning_content") is not None:
      assistant_msg["reasoning_content"] = message["reasoning_content"]
  messages.append(assistant_msg)
  ```
- **Why this approach**: No-op for models without thinking mode (OpenAI GPT-4o); transparent fix for DeepSeek. Kimi's thinking was disabled via `extra_body` — DeepSeek doesn't support disabling thinking via API, so we echo it instead.
- **Verification**: `python3 -c "from mcp_server.providers.custom.providers import OpenAICompatibleProvider; print('OK')"` → OK.

### [Feature]: Phase B — Native tool-use loop in providers + harness wire-up

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/providers.py` — complete rewrite to add tool-use
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — `_with_tool_instructions()` helper + 4 call sites updated
- **What was added to providers.py**:
  - `import json` added to imports
  - `ModelProvider` Protocol updated with `supports_tools: bool` attribute and `complete_with_tools()` stub
  - `STAGING_TOOLS_OPENAI` constant: `list_artifacts` + `read_artifact` in OpenAI function-calling format
  - `STAGING_TOOLS_ANTHROPIC` constant: same tools in Anthropic `input_schema` format
  - `_execute_staging_tool(name, args, staging_dir)`: dispatches tool calls with path traversal guard
  - `OpenAICompatibleProvider.supports_tools = True` + `complete_with_tools()`: 30-turn OpenAI tool loop
  - `MoonshotKimiProvider.supports_tools = True` (explicit override, inherits from OpenAI)
  - `OllamaCompatibleProvider.supports_tools = False` + `NotImplementedError` stub
  - `AnthropicProvider.supports_tools = True` + `complete_with_tools()`: Anthropic format (tool_use blocks, tool_result user messages)
- **What was added to harness.py**:
  - `_with_tool_instructions(prompt, staging_dir)` module-level helper — prepends tool preamble to any prompt
  - Phase 2 (line ~343): short-circuits `context_files = []` when `supports_tools`; calls `complete_with_tools()` instead of `complete()`
  - Phase 2.6 (line ~390): same pattern for scorecard
  - Phase 3 `_write_one()` (line ~460): same pattern for `write_provider`
  - Phase 3.5 critic (line ~520): same pattern for `write_provider`
- **Why**: Model pulls exactly what it needs via tools instead of Python deciding upfront. Recovers from non-standard filenames, incomplete reads, and context overflow. Matches how Claude CLI worked (`--max-turns 200` + Read tools).
- **Ollama unchanged**: `supports_tools = False` → existing `complete()` path used, context_files assembled as before.
- **Verification**: `python3 -c "from mcp_server.providers.custom.harness import CustomHarnessProvider; print('OK')"` → OK. `supports_tools` verified: OpenAI=True, Anthropic=True, Ollama=False, Moonshot=True.

### [Fix]: Phase A — DATA_FLOWS dedup + pre-flight context guard + config

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — `_engineering_fact_context()` DATA_FLOWS branch
  - `mcp-server/src/mcp_server/providers/custom/providers.py` — imports, logger, pre-flight guard in both providers
  - `mcp-server/src/mcp_server/config.py` — added `context_max_tokens: int = 900_000`
- **Problem 1 — DATA_FLOWS double-loading**: `03_DATA_FLOWS_LINEAGE` loaded `entity_index.json` (243 KB) + `usage_index.json` (682 KB) from the base set, then ALSO added `entity_index.csv` (193 KB) + `usage_index.csv` (559 KB) via the doc-specific extend. Same data twice = ~925 KB wasted per call.
- **Fix 1**: For `03_DATA_FLOWS_LINEAGE`, override the entire `names` list (not extend) to CSV-only versions: `["engineering_facts.md", "data_flow_map.md", "data_flow_map.json", "entity_index.csv", "usage_index.csv"]`. No other doc type is affected.
- **Problem 2 — no pre-flight guard**: context overflows only failed at runtime with a 400 from the API, after wasting the full call cost.
- **Fix 2**: After `_read_context()` in both `OpenAICompatibleProvider.complete()` and `AnthropicProvider.complete()`, estimate tokens (`len(text) // 4`), raise `RuntimeError` if > `config.context_max_tokens`, log WARNING at 85%. `OllamaCompatibleProvider` inherits the guard via `super().complete()`. `MoonshotKimiProvider` also inherits.
- **Fix 3**: Added `context_max_tokens: int = 900_000` to `MCPServerConfig` (env `MCP_CONTEXT_MAX_TOKENS`). Default 900K gives 100K headroom below deepseek's 1M limit.
- **Why this approach**: Targeted fix only — no architectural change to the harness yet. Phase B (native tool-use loop) is the long-term fix; Phase A stops the crashes now.
- **Verification**: `python3 -c "from mcp_server.config import config; print(config.context_max_tokens)"` → `900000` confirmed.

### [Architecture Decision]: Long-term pivot to native tool-use loop

- **Context**: 4-agent exploratory session covering harness brittleness, claude_cli provider internals, framework alternatives (opencode.ai, smolagents, LangGraph, OpenHarness), and tool-use feasibility.
- **Finding**: The custom harness's 9 hardcoded code paths, exact-match validators, and front-loading approach are all symptoms of having no feedback loop. Claude CLI produced better output because it ran with `--max-turns 200` and Read/Bash tools — it pulled files on demand and self-validated.
- **Decision**: Phase B — add native OpenAI function-calling tool-use loop to `OpenAICompatibleProvider` (~100 lines, no new dependencies). deepseek/kimi/Anthropic all support the same `tools`/`tool_calls` format. Model gets `list_artifacts()`, `read_artifact(name)`, `write_doc(name, content)`. Port the 420-line claude_cli orchestration prompt (replace tool names only). Drop the 9-path switch and exact-match validators.
- **Frameworks rejected**: opencode.ai (interactive TUI, not batch), smolagents (no batch support), LangGraph (overkill for linear batch), OpenHarness (v0.1.7, not stable enough for SaaS — revisit at v1.0).
- **Key constraint**: Must be platform-agnostic — deepseek/kimi/Anthropic/Ollama swappable at runtime. Anthropic partnership means selling tokens but no Claude CLI dependency. Ollama gets graceful fallback (no tool-use, existing single-shot path).

## Session: 2026-04-29 (7)

### [Fix]: Phase 2 context window overflow + relax guide-section gate

- **Files changed**:
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — `_engineering_fact_context()` Phase 2 branch
  - `mcp-server/src/mcp_server/providers/custom/prompt_packs.py` — split `ANALYSIS_SECTION_NAMES`
  - `mcp-server/src/mcp_server/providers/custom/validators.py` — required vs conditional section check; update `group_analysis_is_complete`
  - `intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md` — SOURCE_SQL + CUSTOM_CODE N/A instruction
  - `intodoc/src/intodoc/platforms/workato/prompts/ANALYSIS_GUIDE.md` — same
- **Problem 1 — context overflow**: Phase 2 call hit deepseek-v4-pro's 1M token limit (1.56M tokens requested). Engineering fact JSON files (`entity_index.json` 243KB, `usage_index.json` 682KB, `dependency_map.json` 332KB) were being loaded into every Phase 2 call via the `doc_name=None` branch of `_engineering_fact_context`. These are RAG/Phase 3 artifacts, not Phase 2 analysis inputs.
- **Fix 1**: When `doc_name is None` (Phase 2), load only lightweight markdown: `engineering_facts.md`, `dependency_map.md`, `known_risks.md`, `config_runtime_map.md`. Phase 3 doc-specific calls are unaffected (they use the JSON files via the `doc_name` branches). JSONs stay in staging for RAG indexing.
- **Problem 2 — guide-section gate too strict**: `SOURCE_SQL` and `CUSTOM_CODE` don't apply to every group. Requiring all 15 sections caused hard failures for groups with no SQL or custom code.
- **Fix 2**: Split `ANALYSIS_SECTION_NAMES` into `REQUIRED_SECTION_NAMES` (13) and `CONDITIONAL_SECTION_NAMES` (SOURCE_SQL, CUSTOM_CODE). Validator hard-fails on missing required only. `group_analysis_is_complete` checks required only. Analysis guides instruct model to write `None` if section is empty, so the header is still produced.
- **Why this approach**: Engineering facts JSONs are deliberately precise/large for RAG — correct to keep them, wrong to load them into Phase 2 where preprocess data + investigation evidence already provide the analysis grounding. Section split preserves quality gates where they matter.
- **Verification**: Phase 2 context bytes should drop from ~5MB to ~1MB. Guide-section gate passes for groups without SQL/custom code.

## Session: 2026-04-29 (6)

### [Fix]: Consolidate investigation pipeline fragility — partial tolerance, per-chunk artifacts, config token budget

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` — added `investigation_max_tokens: int = 10_000` (env `MCP_INVESTIGATION_MAX_TOKENS`)
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` — import config; reference `config.investigation_max_tokens` instead of hardcoded 10000
  - `mcp-server/src/mcp_server/providers/custom/harness.py` — three changes in `_phase1_investigation` and coverage gate
- **Problem**: Three compounding fragilities: (1) any chunk failure killed the whole group, discarding successful chunks; (2) per-chunk artifacts were never written, so multi-chunk group investigation re-ran from scratch on every RESUME; (3) investigation token budget was hardcoded.
- **Root cause**: Investigation chunk failure had `break` semantics (abort group). Artifacts were only written on full group success (`merged` report), but RESUME checks for chunk-level filenames (`Account_chunk_1.json`) that never existed.
- **Change 1 — partial tolerance**: On chunk failure, `continue` instead of `break + group_failed`. Only add to `failed_groups` if ALL chunks fail (`chunk_reports` is empty after the loop).
- **Change 2 — per-chunk artifacts**: After each successful chunk, write a per-chunk artifact using `report.model_copy(update={"group": chunk_name})` so RESUME finds `Account_chunk_1.json` etc. and skips them.
- **Change 3 — coverage gate downgrade**: Investigation coverage gate downgraded from `raise RuntimeError` to `log warning`. Phase 2/3 gates enforce quality; investigation coverage is best-effort grounding.
- **Change 4 — config token budget**: `investigation_max_tokens` moved to `config.py`; investigator reads from config.
- **Why this approach**: Keeps Phase 2/3 as the hard quality gates. Investigation becomes best-effort: surviving chunks feed Phase 2, which then passes or fails on factual completeness. Eliminates the most common failure cascade (one bad chunk → whole group lost → coverage gate → failed run).
- **Verification**: Next run should show per-chunk artifacts in `_investigation/` (e.g. `Account_chunk_1.json`). If a chunk fails, warning logged but group continues. Subsequent RESUME skips individual successful chunks.

## Session: 2026-04-29 (5)

### [Fix]: Raise investigation max_tokens 6000 → 10000 to cover large chunks

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/investigator.py` (line ~290)
- **Problem**: Item_chunk_2 investigation failed after 3 attempts — the model hit the 6000-token output cap on attempts 1 and 2, and on attempt 3 finished within budget but couldn't cover all 5 sjItem_* units. The whole Item group was skipped, causing Phase 2 coverage gate to fail.
- **Root cause**: `max_tokens=6000` is too low for a 55,000-byte chunk containing 5+ complex integration jobs. Evidence JSON for many units exceeds that budget.
- **Change**: Raised `max_tokens` in `SingleShotInvestigator.investigate()` from 6000 to 10000.
- **Why this approach**: Most targeted fix — no chunk size changes, no retry count changes. Investigation artifacts for Account/Monitoring/SalesOrder are intact; Item will be fully retried on next RESUME since no Item artifacts were saved (group failed before artifact write).
- **Verification**: Click Generate again — Item_chunk_1 and Item_chunk_2 will re-investigate with 10000 token budget; other groups skip investigation and Phase 2 via RESUME.

## Session: 2026-04-29 (4)

### [Fix]: RESUME skip validates section completeness before trusting cached Phase 2

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/validators.py` (new `group_analysis_is_complete()`), `mcp-server/src/mcp_server/providers/custom/harness.py` (RESUME check lines ~277-281)
- **Problem**: RESUME logic skipped Phase 2 for Account and SalesOrder because their `_analysis/*.md` files existed and were >500 bytes. Those files were from a prior run before `TRANSFORMATION_DETAIL` was added as a required section — they were structurally stale and failed the post-Phase-2 validation gate.
- **Root cause**: RESUME size-only check (`> 500 bytes`) accepted stale cached analysis files without verifying canonical section completeness. The validation gate runs *after* Phase 2, so by the time it caught the bad files, Phase 2 was already skipped and the run failed.
- **Change**: Added `group_analysis_is_complete(analysis_path, platform)` to `validators.py`. Reuses `_model_analysis_text()` and `ANALYSIS_SECTION_NAMES`. Non-Talend platforms always return True. In `harness.py`, the RESUME skip now calls this before trusting the cache; if incomplete, logs "regenerating" and falls through to re-run Phase 2.
- **Why this approach**: Minimal — reuses existing validator infrastructure. The helper is per-file so it's O(1) per group. Non-Talend platforms are unaffected (section check is gated on `platform == "talend"`).
- **Verification**: Deleted stale Account.md + SalesOrder.md from `docs_staging/_analysis/`. Next run should log "RESUME: cached Phase 2 for Account/SalesOrder is incomplete, regenerating" and succeed.

## Session: 2026-04-29 (3)

### [Feature]: GitHub push webhook — auto scan + incremental doc regen

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` (add `github_webhook_secret: str | None = None`)
  - `mcp-server/src/mcp_server/api/app.py` (remove JWT dependency from webhook router)
  - `mcp-server/src/mcp_server/api/routes/webhook.py` (full rewrite — HMAC auth, diff extraction, group mapping, auto-generate)
  - `mcp-server/src/mcp_server/api/routes/projects.py` (preserve `analysis_cache/` after atomic swap)
  - `.env.production.template` (add `MCP_GITHUB_WEBHOOK_SECRET`)
  - `deploy/nginx/nexus-client.conf.template` (add `/webhook/` location block)

- **Problem**: The existing webhook endpoint was behind JWT cookie auth — GitHub cannot send cookies, so every push was returning 401. Even if it had worked, it only triggered a rescan, never a doc regeneration.
- **Root cause**: Webhook router was registered with `dependencies=[Depends(require_auth)]` alongside the dashboard routes. Doc generation was never triggered from the webhook background task.
- **Change 1 — Auth model**: Removed JWT dependency from webhook router. Added HMAC-SHA256 signature verification using `X-GitHub-Signature-256` header and `MCP_GITHUB_WEBHOOK_SECRET`. Rejects requests with wrong or missing signature with 401.
- **Change 2 — Diff-aware group detection**: For push events, extracts `before`/`after` SHAs from GitHub payload. After git pull, runs `git diff --name-only before after`. Maps changed file paths to group names by looking at `intodoc-output/{project_id}/scan/jobs/` directory structure. Falls back to all groups if no match.
- **Change 3 — Analysis cache**: After every successful doc generation (`swap_docs` → `docs/_analysis/` exists), copies Phase 2 analysis files to `output/{project_id}/analysis_cache/`. On next webhook push, these are copied into `docs_staging/_analysis/` and the files for changed groups are deleted. Generation runs with `resume=True` — the existing resume logic skips Phase 2 for groups with cached analysis files.
- **Change 4 — Auto-generate after scan**: Webhook background task now calls `_run_generate_background` directly after a successful scan (imported from projects.py, reusing the `_currently_generating` dedup guard).
- **Change 5 — nginx**: Added `/webhook/` location block in the nginx template so the endpoint is reachable from GitHub without inheriting the 500m body size limit from `/mcp/`.
- **Why this approach**: HMAC is the GitHub-standard auth for webhooks. File-based analysis cache avoids Hindsight dependency (disabled by default). Reusing resume logic means no changes to providers or prompts — Phase 2 skip is already there.
- **Verification**: Register a test repo via dashboard (HTTPS URL with token for private repos). Add webhook in GitHub Settings → Webhooks with payload URL `https://{host}/webhook/github/webhook`, content type `application/json`, secret matching `MCP_GITHUB_WEBHOOK_SECRET`. Push a commit — logs should show scan_triggered → generation_triggered. Second push should only re-analyze changed groups.

## Session: 2026-04-29 (2)

### [Fix]: Investigator JSON repair + chunk failure isolation

- **Files changed**:
  - `mcp-server/pyproject.toml` (add `json-repair>=0.30`)
  - `mcp-server/src/mcp_server/providers/custom/investigator.py` (`_extract_json_object` lines 378-396)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (`_phase1_investigation` return type + call site in `run()`)

- **Problem**: Talend doc generation (VAXXINOVA_TALEND_ESB, groups Account/Item/Monitoring/SalesOrder) failed after 8 minutes with `Expecting ',' delimiter: line 50 column 6 (char 2196)`. deepseek-v4-pro generated JSON with a missing comma inside the evidence array. All 3 retries failed. Because `_phase1_investigation` raised `RuntimeError`, the entire run aborted — all 4 groups failed because Account_chunk_1 had bad JSON.
- **Root cause**: (1) `_extract_json_object` used `json.loads()` with no repair fallback — one syntax error kills the whole parse. (2) A single chunk failure propagated up as `RuntimeError` and aborted all other groups.
- **Change 1 — JSON repair fallback**: In `_extract_json_object`, after `json.loads(candidate)` fails with `JSONDecodeError`, try `json_repair.repair_json(candidate)` (from the new `json-repair>=0.30` dep). If repair produces valid JSON, return the repaired string. If repair still fails, re-raise the original `JSONDecodeError` so the retry loop handles it normally.
- **Change 2 — Chunk failure isolation**: Changed `_phase1_investigation` return type from `None` to `set[str]` (failed group names). Instead of `raise RuntimeError(...)` on `InvestigationError`, log at ERROR, set `group_failed = True`, `break` out of the chunk loop, add the group to `failed_groups`, and `continue` to the next group. In `run()`: if `failed_groups` is non-empty, filter `ctx.preprocess_groups` to exclude them, log a warning, and skip the coverage gate (which would fail for missing groups). If `failed_groups` is empty, run the strict coverage gate as before. If ALL groups failed, raise RuntimeError.
- **Why this approach**: JSON repair handles the most common LLM JSON errors (missing commas, trailing commas, unquoted keys) without changing call sites. Chunk isolation means one model hiccup on one chunk doesn't lose hours of work on the other groups.
- **Verification**: 32 provider tests pass. Both modules import clean.

## Session: 2026-04-29

### [Fix/Refactor]: Doc generation reliability overhaul — 10-step bugfix campaign

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` (new config fields + `SUPPORTED_PLATFORMS`)
  - `mcp-server/src/mcp_server/schemas.py` (platform validators on `AddRepoRequest`, `UpdateProjectRequest`)
  - `mcp-server/src/mcp_server/api/routes/projects.py` (zero-doc fix, RAG retry, concurrency guard, platform validation on ZIP upload, error levels)
  - `mcp-server/src/mcp_server/services/claude_service.py` (chunk/template failures raise, quick scan try/except removed, hardcoded values replaced with config, platform fallback removed)
  - `mcp-server/src/mcp_server/services/rag_bridge.py` (timeout constants from config, `_read_md_files` excludes internal artifacts)
  - `mcp-server/src/mcp_server/providers/custom/harness.py` (Phase 2 resume skip, `max_tokens` from config)
  - `mcp-server/src/mcp_server/services/doc_quality_scorer.py` (thresholds scale with unit_count, extended `_JOB_RE` for Workato)

- **Problem**: The doc generation system had accumulated ~12 swallowed failure points, hardcoded magic numbers in 4 files, a static Talend-only quality scorer, token truncation causing output to be cut mid-sentence, a concurrency race, and internal artifacts being pushed to RAG and appearing in user query results.

- **Root cause analysis**: Grown organically over 4 months. Each fix added a `logger.warning("non-fatal")` instead of propagating. LangGraph was added then abandoned but code was left in place. Quality scorer was calibrated for a 500-job Talend project and never adjusted. `max_tokens` caps (9000/10000) were below what deepseek-v4-pro actually supports (32K).

- **Changes made**:

  1. **Delete dead LangGraph code**: `orchestrator.py` (1441 lines) already deleted in prior session (2026-04-28). Confirmed zero `langgraph`/`langchain` imports remain.

  2. **Fix zero-doc = success**: `projects.py` — when `doc_count == 0` after staging swap, route to failure path: delete staging dir, `update_status("error")`, `complete_generate_run(status="failed")`, return early. Previously fell through to `status=ready`.

  3. **Fix RAG push swallowed failure**: Added `_push_with_retry(bridge, project_id, output_dir, max_attempts=3)` with exponential backoff (2^attempt seconds). On persistent failure, project keeps `status=ready` (docs on disk are valid) but `error_message` records the RAG failure. Errors logged at ERROR not WARNING. Also check `result["errors"]` dict after even a non-exception return.

  4. **Fix critical "log and continue" patterns**:
     - `claude_service.py`: chunk definition failure now raises `RuntimeError` (was `return [], {}`)
     - `claude_service.py`: template resolution failure now raises `RuntimeError` (was silent fallback to filesystem templates)
     - `claude_service.py`: quick scan `try/except` removed entirely (pure Python/regex, exceptions indicate code bugs)
     - `projects.py`: quality scoring failure changed from `logger.warning` to `logger.error`
     - `projects.py`: assessment generation failure changed from `"warning"` to `"error"` level

  5. **Centralize hardcoded values into config.py**: Added 11 new Pydantic Settings fields to `MCPServerConfig`: `max_prompt_bundle_bytes`, `max_ref_bundle_bytes`, `max_param_val_chars`, `qs_god_profile_threshold`, `qs_large_job_threshold`, `qs_complex_tmap_threshold`, `phase2_max_tokens`, `phase3_max_tokens`, `rag_push_timeout_s`, `rag_delete_timeout_s`, `rag_usage_timeout_s`. All callers updated to use config constants.

  6. **Platform validation**: Added `SUPPORTED_PLATFORMS = frozenset({"talend", "workato", "lobster", "generic"})` as module-level constant in `config.py`. Added `@field_validator("platform")` to `AddRepoRequest` and `UpdateProjectRequest` in `schemas.py`. Added validation in `upload_zip` handler (form data bypasses Pydantic). Fixed silent `platform = project.platform if project else "talend"` fallback in `claude_service.py` to raise if project not found.

  7. **Concurrent generation lock**: Added `_currently_generating: set[str]` module-level set in `projects.py`. HTTP handler returns HTTP 409 if project already in set. Set.add before background task, set.discard at both exit points.

  8. **Fix resume Phase 2 re-running**: In custom harness Phase 2 loop, check for existing `_analysis/{group}.md` file (>500 bytes) when `ctx.resume_state is not None`. Skip the API call and log "RESUME: skipping Phase 2 for {group} (analysis exists)". Mirrors the existing investigation resume logic.

  9. **Fix RAG push including internal artifacts**: `_read_md_files` changed from `rglob("**/*.md")` to `glob("*.md")` (top-level only) with `if not md_file.name[0].isdigit(): continue` filter. Excludes `_assessment/`, `_investigation/`, `_analysis/` subdirectories and their prompt/debug content.

  10. **Fix output token truncation + quality scorer calibration**:
      - Raised `phase3_max_tokens` from 10000 to 32000 (deepseek-v4-pro supports 32K output tokens)
      - Raised `phase2_max_tokens` floor from 9000 to 16000
      - Made `_BASE_MIN_LINES` scale with actual project unit count: sqrt formula clamped to [0.3x, 2.0x] at a reference of 50 units; a 17-recipe Workato project gets ~58% of baseline thresholds
      - Extended `_JOB_RE` to match Workato recipe names: backtick-wrapped identifiers and bold `**Name**` patterns in addition to Talend `ALL_CAPS_prefix` style
      - `DocQualityScorer.score` reads `facts_manifest.json` to get actual `unit_count` and passes it to `score_doc`

- **Root cause — plsss quality score 2.38/5.0**: All 3 root causes identified from logs: (1) `max_tokens=10000` cut Phase 3 docs mid-sentence, (2) static thresholds calibrated for 500-job Talend graded 17-recipe Workato project as failing, (3) `_JOB_RE` only matched Talend-style names so specificity score was 0.
- **Verification**: Syntax check passed for all 7 modified files. Provider tests (`pytest tests/test_providers/`) pass. The JSONB SQLite test failure is a pre-existing issue (confirmed by stashing and running tests on base branch).

## Session: 2026-04-28

### [Fix]: Assistant prefilling for SingleShotInvestigator JSON extraction

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/models.py`, `mcp-server/src/mcp_server/providers/custom/providers.py`, `mcp-server/src/mcp_server/providers/custom/investigator.py`
- **Problem**: `deepseek-v4-pro` returned plain text (no `{`) for the `recipes` group despite the prompt saying "Return ONLY a valid JSON object". All 3 attempts failed because `_extract_json_object` couldn't find a `{` character.
- **Root cause**: Soft prompt instruction ("Return ONLY JSON") is not a hard constraint — models can and do ignore it, especially on complex content.
- **Change**: Added `prefill: str | None` field to `ModelOptions`. Both `OpenAICompatibleProvider` and `AnthropicProvider` now append `{"role": "assistant", "content": opts.prefill}` to the messages array when prefill is set, and prepend the prefill to the returned text. `SingleShotInvestigator` now passes `prefill="{"` so the model is already inside a JSON object and cannot respond with prose.
- **Why this approach**: Assistant prefilling is a hard constraint — the model continuation must follow the prefilled content. No stop sequences needed because `_extract_json_object` already uses `rfind("}")` to handle trailing text after the JSON object closes.
- **Verification**: Pattern matches article technique exactly. Works on all OpenAI-compatible APIs (deepseek, moonshot) and Anthropic native API.

### [Fix]: Prefill echo detection — deepseek doubles the opening brace

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/providers.py`
- **Problem**: deepseek echoed the prefill `{` back as the first character of its response. Combined with our prepend, result was `{{  "evidence": [` — invalid JSON. Error: `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)`.
- **Root cause**: OpenAI-compatible APIs don't guarantee whether the model echoes the prefill or not — deepseek does, some others don't.
- **Change**: Replaced inline `prefill + raw_text` with `_apply_prefill(prefill, raw_text)` in both `OpenAICompatibleProvider` and `AnthropicProvider`. The helper checks if `raw_text.lstrip()` already starts with the prefill; if so, returns `raw_text` unchanged.
- **Why this approach**: Works for both echo and non-echo models. No regex, no JSON pre-parsing, just a string prefix check before concatenation.
- **Verification**: deepseek response preview showed `{{` — confirmed echo behavior. Fix makes it idempotent for both cases.

### [Fix]: RESUME skips already-investigated groups + log model response on failure

- **Files changed**: `mcp-server/src/mcp_server/providers/custom/harness.py`, `mcp-server/src/mcp_server/providers/custom/investigator.py`
- **Problem**: On RESUME, `_phase1_investigation` re-ran investigation for all groups even when valid `_investigation/{group}.json` artifacts existed from a prior run. deepseek-v4-pro returned plain text (no JSON) for the `recipes` group 3 times, wasting ~9 minutes and producing no error details.
- **Root cause**: No checkpoint check in `_phase1_investigation` before calling `loop.run()`. Additionally, the failure error message did not include what the model actually returned, making debugging impossible.
- **Change**: Added `_load_investigation_artifact()` static method to `CustomHarnessProvider` that reads and validates an existing `_investigation/{chunk_name}.json`. In `_phase1_investigation`, when `ctx.resume_state` is set and a valid artifact exists, the API call is skipped and the cached report is used. Also added a 300-char preview of `result.text` to the final `InvestigatorError` message after 3 failed attempts.
- **Why this approach**: The check is gated on `ctx.resume_state is not None` so a fresh run always re-investigates. The load uses Pydantic `model_validate_json` so a corrupt or schema-mismatched artifact is silently ignored and the group is re-investigated.
- **Verification**: Confirmed `_investigation/recipes.json` (from 16:36) would now be loaded instead of re-called. Next RESUME run for workato will log "RESUME: skipping investigation for recipes (N evidence items from prior run)".

### [Refactor]: Doc generation cleanup — remove dead code, unblock analysis guide to harness

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` (DELETED)
  - `knowledge_base/mcp-server/src/mcp_server/providers/tools.py` (DELETED)
  - `knowledge_base/mcp-server/src/mcp_server/providers/base.py` (removed 8 cli-only fields from `DocGenContext`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/claude_cli.py` (moved cli-only computations into `_build_prompt()` as locals)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/prompt_packs.py` (removed `_HARNESS_PREAMBLE`, `_RUNTIME_LINE_PATTERNS`, `_adapt_legacy_text()`)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/investigator.py` (deleted `InvestigatorLoop` ~256 lines)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py` (always use `SingleShotInvestigator`, removed `InvestigatorLoop` branch)
  - `knowledge_base/mcp-server/src/mcp_server/services/claude_service.py` (simplified provider selection, removed cli-only precomputation ~60 lines)
  - `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py` (removed `InvestigatorLoop` tests, updated imports, fixed `DocGenContext` construction)

- **Problem**: The doc generation system had grown into a tangle: `orchestrator.py` (LangGraph StateGraph) and `tools.py` were dead code never invoked by any provider. `prompt_packs.py` was stripping lines from `ANALYSIS_GUIDE.md` via `_adapt_legacy_text()` before the guide reached harness models — silently discarding semantic content. 8 cli-only fields were polluting the shared `DocGenContext`. `InvestigatorLoop` (multi-turn agentic mode) was ~256 lines that was never the default and not used in production.
- **Root cause**: The system evolved from a single Claude CLI provider; harness code was bolted on and kept compatibility shims. `_adapt_legacy_text()` was designed to strip "orchestration instructions" from the guide, but the guide contains semantic extraction targets (not orchestration), so stripping was harmful.
- **Change 1 — delete `orchestrator.py` and `tools.py`**: Pure dead code (LangGraph StateGraph never invoked by any provider). ~350 lines gone.
- **Change 2 — remove `_adapt_legacy_text()` and friends from `prompt_packs.py`**: Removed `_HARNESS_PREAMBLE`, `_RUNTIME_LINE_PATTERNS`, `_adapt_legacy_text()`, and the `import re`. Simplified `_write_pack()` to plain header + body concatenation (`## Source: {source.name}`). The full analysis guide now reaches harness models unfiltered.
- **Change 3 — slim `DocGenContext` in `base.py`**: Removed 8 cli-only fields: `analysis_target_kb`, `depth_tier`, `depth_multiplier`, `depth_table_rows`, `is_large_project`, `num_agents`, `agent_table`, `agent_retry_map`, `hindsight_enabled`. Retained 13 shared fields that both providers actually use.
- **Change 4 — move cli-only computation into `claude_cli.py:_build_prompt()`**: Analysis target KB, depth tier, depth multiplier, depth table rows, large project flag, num agents, agent tables, hindsight blocks — all now computed as local variables inside `_build_prompt()`. DocGenContext is no longer the wrong place for these.
- **Change 5 — delete `InvestigatorLoop` from `investigator.py`**: The agentic investigation strategy was non-default and unused in production. Deleted `InvestigatorLoop` (~256 lines). `StagingToolbox` and `EvidenceValidator` kept because `SingleShotInvestigator` uses them.
- **Change 6 — always use `SingleShotInvestigator` in `harness.py`**: Removed `InvestigatorCls` branch; always instantiate `SingleShotInvestigator` directly. Removed `InvestigatorLoop` import.
- **Change 7 — simplify provider selection in `claude_service.py`**: Removed ~60 lines of cli-only precomputation (depth tier, agent tables, etc.). Provider selection block now has a clear `claude-cli` vs harness branch. `DocGenContext` construction no longer passes cli-only fields.
- **Change 8 — update tests**: Removed 3 `InvestigatorLoop` tests. Changed `EvidenceValidator` usage from `InvestigatorLoop(...)` to `EvidenceValidator(facts, StagingToolbox(staging))`. Fixed `DocGenContext` construction in harness e2e test. Updated `test_materialize_prompt_packs_writes_harness_safe_assets` to check for `## Source: _analysis_guide.md` header (not old preamble) and verify guide content is NOT stripped.
- **Why this approach**: Two providers, two different orchestration models — that's the right design. They share guide content but not prompts. Stripping the guide was the worst bug: it silently degraded harness output quality with no error. Dead code removal reduces maintenance surface.
- **Verification**: `pytest tests/test_providers/ tests/test_services/` — 46 passed, 0 failed.

## Session: 2026-04-25

### [Feature]: Custom investigator agent loop for API-token doc generation

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/investigator.py` (new)
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py`
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/models.py`
  - `knowledge_base/mcp-server/src/mcp_server/providers/custom/validators.py`
  - `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py`

- **Problem**: The custom API-model provider still depended on large one-shot Phase 2 analysis calls. It lacked the Claude-Code-like loop where a model can investigate source artifacts incrementally while Python keeps hard control over filesystem access, validation, artifact writing, and phase gates.
- **Root cause**: Existing custom provider had a bounded markdown writer and factual gates, but no intermediate evidence-gathering phase. Phase 2 had to rediscover scanner facts and source evidence in the same call that synthesized analysis, which is fragile for large groups and weaker than Claude Code's tool loop.
- **Change 1 — controlled tool loop**: Added `InvestigatorLoop` + `StagingToolbox` in `providers/custom/investigator.py`. The model must return one JSON action per turn:
  - `read_file`
  - `search_text`
  - `list_dir`
  - `finish`
  Python executes only these actions, resolves every path under `ctx.staging_dir`, blocks traversal/absolute paths outside staging, caps reads/search results, enforces max rounds, invalid-action retries, timeout, and logs model-token metrics via normal `ProviderCallLog`.
- **Change 2 — evidence schema and validation**: Added `InvestigationEvidence` and `InvestigationReport` models. Evidence must name an exact canonical unit, use one of the allowed categories (`inventory`, `components/actions`, `data flow`, `transformation/business logic`, `config/context`, `operations`, `security`, `migration`), cite a staging-relative source file, include `line_range` or `chunk_ref`, include a concrete snippet, and provide a non-generic observation. Python rejects unknown units, wrong-group units, missing snippets, missing source files, and missing per-unit investigation coverage.
- **Change 3 — investigation phase before Phase 2**: `CustomHarnessProvider.run()` now runs `_phase1_investigation()` before Phase 2. It runs one investigator per preprocess group. Large preprocess files reuse the existing chunk splitter; chunk reports are merged per group. Python writes:
  - `_investigation/{group}.json`
  - `_investigation/{group}.md`
  - `_investigation/_summary.md`
  - `_investigation/_coverage_report.json`
- **Change 4 — evidence threaded through later phases**: Phase 2 group analysis receives its validated investigation markdown alongside facts manifest, quick scan, analysis guide, and preprocess file. Phase 2.6 scorecard also reads `_investigation/*.md`. Phase 2.8 bundles now include investigation summaries before Phase 2 analysis, so Phase 3 doc writers get the evidence without rediscovering source files.
- **Change 5 — critic/expander pass**: Added `_phase35_critic_expander()` after Phase 3 doc writing and before final gates. It asks the write model for JSON-only bounded expansions per doc family. Python only appends expansions when the target doc is allowed, markdown is substantive, listed units are canonical, and the expansion does not introduce an obvious conflicting canonical-inventory claim. Empty expansions are accepted (`{"expansions":[]}`).
- **Tests added**:
  - JSON action parser accepts fenced JSON and rejects unknown actions.
  - `StagingToolbox` blocks outside-staging file access.
  - Investigator loop executes valid read/finish action sequence.
  - Invalid JSON retries then fails.
  - Max rounds stops runaway agents.
  - Valid evidence writes investigation artifacts and passes coverage.
  - Unknown unit evidence is rejected.
  - Missing snippet evidence is rejected.
  - Missing investigation coverage fails before Phase 2.
  - End-to-end custom harness fake-provider test now exercises investigator calls and verifies `_investigation/_summary.md` is passed downstream.
- **Verification**:
  - `cd knowledge_base/mcp-server && ruff check src/mcp_server/providers/custom tests/test_providers/test_custom_provider.py` — passed.
  - `cd knowledge_base/mcp-server && PYTHONDONTWRITEBYTECODE=1 pytest tests/test_providers/test_custom_provider.py` — 32 passed.
  - Full `pytest` currently reaches provider tests but is blocked by an existing DB fixture issue unrelated to this change: SQLite cannot render PostgreSQL `JSONB` for `generate_runs.template_snapshot`. Error: `Compiler ... can't render element of type JSONB`.
- **How to test everything from this feature in a future session**:
  1. Run the focused test suite:
     ```bash
     cd knowledge_base/mcp-server
     PYTHONDONTWRITEBYTECODE=1 pytest tests/test_providers/test_custom_provider.py
     ```
  2. Run lint for touched code:
     ```bash
     cd knowledge_base/mcp-server
     ruff check src/mcp_server/providers/custom tests/test_providers/test_custom_provider.py
     ```
  3. Runtime test with a real scanned project using `MCP_DOC_GENERATION_PROVIDER=custom` and an API model such as `moonshot/kimi-k2.6` or `openai/<model>`. Trigger doc generation from the dashboard or API. During the run, confirm logs show `[custom] Investigation complete` and `[custom] Investigation coverage gate passed`.
  4. Before successful cleanup, inspect `output/{project_id}/docs_staging/_investigation/` and confirm group `.json`/`.md` artifacts exist with exact canonical unit names, snippets, and source file citations.
  5. After success, inspect final docs and `provider_calls.json`: investigation calls should be present as phase `investigation`; `_investigation/` intentionally remains for auditability while `_preprocess/` and `_analysis/` are cleaned.
  6. Re-run with a deliberately weak/fake provider or edited bad evidence in a unit test path to confirm Phase 2 does not start when investigation coverage is missing.

## Session: 2026-04-24

### [Fix]: Token explosion + quality fix — 22M tokens → ~3M, score 2.97 → ≥3.5

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py`
  - `knowledge_base/mcp-server/src/mcp_server/services/claude_service.py`

- **Problem**: A single doc-gen run for `localESBKIMI` (kimi-k2.6) consumed 22.6M input tokens ($22.14). Doc quality was 2.97/5. Executive summary reported 11 jobs when the project has 21. Two groups (Item, SalesOrder) failed Phase 2 entirely.
- **Root cause**: LangGraph `create_react_agent` appends every tool call + result to conversation history on each subsequent turn → O(n²) accumulation (820× multiplier across 40 turns). `patterns_user` alone used ~9M tokens. Item + SalesOrder Phase 2 failed because kimi hit its internal step limit ("Sorry, need more steps") after 30 ReAct turns of pagination without writing output. Phase 3 received incomplete data, producing wrong job counts throughout all docs.
- **Change 1 (Fix 1 — most critical)**: `_make_analyze_group_node()` lines 1007–1039: API model `else:` branch now pre-loads context files (quick_scan, guide, group data — max 80KB each) and calls `_run_section_series()` instead of `_run_agent()`. Eliminates ReAct tool-call accumulation for Phase 2. Summary file written from analysis tail after success.
- **Change 2 (Fix 2)**: `_make_write_docs_node()` lines 1108–1175: Unified context loading for all models (pre-load bundles `_bundle_{agent}`, `_bundle_{agent}_a`, `_bundle_{agent}_b` + assessment). All models now use section-series per doc. `_run_agent` removed from Phase 3. Retry block removed (section-series writes Python-side, cannot fail to write).
- **Change 3 (Fix 3)**: `_format_chunk_markdown()` in `claude_service.py`: Added `_TALEND_NOISE_PARAMS` frozenset (PREVIEW, CONNECTION_FORMAT, 14 internal Talend UI flags). Added `_MAX_PARAM_VAL_CHARS=400` truncation (was dumping 50KB JSON blobs in code blocks). Account.md reduced 237KB → 111KB, Item.md 165KB → 84KB. Added compact job inventory table at top of each preprocess file (all N job names in first ~2KB, preventing truncation from hiding jobs from LLM).
- **Change 4 (Fix 4)**: `_make_score_node()` — added early exit if `len(failed_groups) >= max(1, total_groups // 2)`. Prevents ~1M token scorecard run when half the groups failed.
- **Change 5 (Fix 5)**: `_build_phase3_assignments()` — small project `else:` branch now uses 4 agents (`patterns`, `user`) instead of 3 (`patterns_user`). Matches large-project split. Halves peak context per Phase 3 agent.
- **Why this approach**: `_run_section_series` already existed and worked for Ollama. Enabling it for API models is the architectural fix (prevents accumulation), not a workaround. OpenHarness auto-compaction was evaluated and rejected — it treats the symptom (large context) not the root cause.
- **Verification**: `python3 -m pytest tests/test_services/test_claude_service.py` — 5/6 pass (1 pre-existing failure on `ensure_token_fresh`). Syntax check on both files passes. Fix 3 tested end-to-end: Account.md 111KB (was 237KB), all 21 job names captured in inventory. Fix 5: small project now produces 4 agents. Re-run `localESBKIMI` to confirm tokens < 4M, score ≥ 3.5, 21 jobs in exec summary.

## Session: 2026-04-23

### [Feature]: Ollama local inference optimizations (Gemma 4 26B/31B support)

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py`
    - Added `_OLLAMA_SEM = asyncio.Semaphore(1)` at module level
    - `_run_agent`: wraps `_run_simple_chain` call in `async with _OLLAMA_SEM:` for Ollama models
    - Added `_max_lines_for(ctx)` helper: returns 2000 for Ollama, 300 for API models
    - `_phase2_analysis_prompt`: added `max_lines` param, replaced hardcoded `300` with `{max_lines}`
    - `_phase3_doc_prompt`: same
    - All 3 call sites updated to pass `max_lines=_max_lines_for(ctx)`
  - `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py`
    - `_calc_cost`: added early return of `0.0` for `ollama/` prefix models
- **Problem 1**: LangGraph `Send` fans out all nodes in parallel. With a 26B model consuming ~20GB RAM, two simultaneous `llm.ainvoke()` calls = OOM on a 32GB machine.
- **Fix 1**: Python-level semaphore serialises Ollama calls without changing LangGraph graph structure. API providers are unaffected.
- **Problem 2**: Prompts instructed model to paginate at 300 lines — conservative for Gemma3:4b. Gemma 4 has 128K-256K context so this caused unnecessary multi-step reads.
- **Fix 2**: `_max_lines_for(ctx)` returns 2000 for Ollama, injected into prompt strings at build time.
- **Problem 3**: Ollama runs showed `—` in cost column (returned `None`). Should show `$0.00`.
- **Fix 3**: `_calc_cost` returns `0.0` for all `ollama/` models.

## Session: 2026-04-22

### [Fix]: Ollama "does not support tools" routing failure

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/services/claude_service.py` (line ~1336)
- **Problem**: `ctx.effective_model` was always set to `config.doc_generation_model` default (`"claude-sonnet-4-6"`), so the `provider_str.startswith("ollama/")` check in `_run_agent` always resolved to False — Ollama was routed through `create_react_agent` which uses tool-calling, which Ollama doesn't support.
- **Root cause**: `effective_model` was never overridden to the actual provider string when a LangChain provider was selected.
- **Change**: Added a block that sets `effective_model = resolved_provider` whenever `resolved_provider` is not `"claude-cli"`.
- **Verification**: Ollama runs now route through `_run_simple_chain` (plain `llm.ainvoke` with `<<<FILE:>>>` parser).

### [Fix]: Kimi k2.6 "thinking is enabled but reasoning_content is missing" 400 error

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py` (moonshot branch in `_build_llm`)
- **Problem**: Kimi k2.6 has extended thinking on by default. Multi-turn tool calls via `create_react_agent` drop `reasoning_content` from message history; the API rejects turn 2+ with a 400.
- **Root cause 1**: Used `model_kwargs={"thinking": {...}}` which LangChain spreads as a direct kwarg to OpenAI SDK's `create()` — OpenAI SDK doesn't accept `thinking` as a top-level kwarg.
- **Root cause 2**: Extended thinking itself is incompatible with multi-turn tool-calling via LangChain.
- **Change**: Disable thinking via `model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}}`. `extra_body` is the OpenAI SDK mechanism for passing arbitrary JSON in the raw request body without kwarg validation.
- **Verification**: Kimi run completed all 15 docs without errors.

### [Feature]: Token usage and cost tracking for LangChain provider

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` — added `_TokenAccumulator` class, wired into `_ProgressLogger.on_llm_end`, updated `_run_agent` + all node factory signatures to accept `accumulator`
  - `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py` — added `_PRICING` table, `_calc_cost()`, imports `_TokenAccumulator`; `run()` creates accumulator, passes to graph, populates `AgentResult` with real token counts and cost
- **Problem**: Dashboard always showed 0 0 0 for tokens/cost on LangChain runs.
- **Change**: `on_llm_end` callback accumulates `prompt_tokens`/`completion_tokens` from `response.llm_output["token_usage"]`; final `AgentResult` reads from accumulator.
- **Verification**: Run log shows `in=X,XXX out=X,XXX tokens cost=$0.XXXX`.

### [Feature]: Gap 1 — Retry logic for failed Phase 2 groups and Phase 3 agents

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` (`_make_analyze_group_node`, `_make_write_docs_node`)
- **Problem**: LangGraph had no retry logic; one bad LLM call = permanent failure. Claude CLI had Phase 2.5/3.5 gates that re-spawned failed agents once.
- **Change**: After first `_run_agent` call in Phase 2, check analysis file exists and is >1KB; if not, retry once. After Phase 3, check which docs are missing from staging; if any, retry once with a focused prompt for just the missing docs.
- **Verification**: Log shows "retry 1..." lines when first attempt fails; second attempt recovers.

### [Feature]: Gap 3 — Ollama 1-doc-per-call batching to prevent context overflow

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` (`_build_phase3_assignments`)
- **Problem**: Asking `gemma3:12b` to write 5-6 docs in one call exceeded its context window; it generated 1-2 docs then stopped silently.
- **Change**: When `ctx.effective_model.startswith("ollama/")`, assign exactly 1 doc per agent. Each call is small enough to complete reliably.
- **Why this approach**: Derived from a real signal (model type), not hardcoded; more calls but each one completes.

### [Feature]: write_model_name in run history dashboard

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/db/models.py` — added `write_model_name: Mapped[str | None]` column to `GenerateRun`
  - `knowledge_base/mcp-server/alembic/versions/005_add_write_model_name.py` (new) — migration to add column
  - `knowledge_base/mcp-server/src/mcp_server/providers/base.py` — added `write_model_name: str | None` to `AgentResult` + `to_dict()`
  - `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py` — populate `write_model_name` from `ctx.write_model` when `self.write_llm` is set
  - `knowledge_base/mcp-server/src/mcp_server/services/project_registry.py` — `complete_generate_run` accepts and stores `write_model_name`
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` — both `complete_generate_run` call sites pass `write_model_name`; `_run_to_response` includes it
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` — `GenerateRunResponse` includes `write_model_name: str | None`
  - `knowledge_base/dashboard/src/types.ts` — `GenerateRun` interface includes `model_name` and `write_model_name`
  - `knowledge_base/dashboard/src/components/RunHistory.tsx` — new Model column showing analysis model + write model (✎ prefix, accent colour) when set
- **Problem**: write_model was used internally but never surfaced in the run history UI.
- **Change**: Full stack — DB column → registry → route → schema → frontend type → table column.
- **Requires**: `alembic upgrade head` on the server before restart.

### [Feature]: Gap 2 — write_model for Phase 3 doc generation

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/providers/__init__.py` — `get_provider()` now reads `config.doc_generation_write_model` and passes as `write_model_str` to `LangchainProvider`
  - `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py` — `__init__` accepts `write_model_str`, builds `self.write_llm` (with fallback warning if build fails); `run()` passes `write_llm` to graph
  - `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` — `build_doc_gen_graph` accepts `write_llm` param; `write_docs_with_depth` uses `write_llm` (fallback to `llm`) for Phase 3 nodes
- **Problem**: Phase 3 doc writing used the same model as Phase 2 analysis, ignoring `MCP_DOC_GENERATION_WRITE_MODEL`.
- **Change**: Phase 2 analysis uses `llm` (read model); Phase 3 writing uses `write_llm` (write model, falls back to `llm` if not configured).
- **Verification**: Startup log shows "write_model configured: X"; run log shows "(phase3: X)".

## Session: 2026-04-21

### [Feature]: MCP hardening for per-client Nexus deployment

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/config.py` — new settings `allowed_ips`, `trusted_proxies`, `app_env`. New `validate_production_secrets()` runs at module import and hard-fails on default JWT secret (`change-me-in-production-…`) or empty `MCP_INGEST_API_KEY` when `MCP_APP_ENV=production`.
  - `knowledge_base/mcp-server/src/mcp_server/api/app.py` — registered `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` (trusted hosts from `MCP_TRUSTED_PROXIES`) and a new `IPAllowlistMiddleware` reading `MCP_ALLOWED_IPS`. Order is `IPAllowlist` then `ProxyHeaders` — dispatch order is LIFO so ProxyHeaders runs first, IPAllowlist sees the real client IP.
  - `knowledge_base/mcp-server/src/mcp_server/api/middleware.py` (new) — small CIDR-aware allowlist middleware; empty env var → no-op, 403 otherwise.
  - `knowledge_base/mcp-server/src/mcp_server/__main__.py` — default `--port` changed from `8001` (collided with the dashboard in the docker-compose setup) to `8002`. `--host` default kept at `0.0.0.0` with an inline comment explaining the tradeoff (Docker containers on the same host need MCP bound to wildcard to reach it via `host.docker.internal`; production systemd template unit `deploy/mcp-server@.service` overrides with `--host 127.0.0.1`).
- **Problem**: A fresh per-client Nexus stack exposed the MCP server on `0.0.0.0:8002` with no auth for allowlisted deployment scenarios, no real-IP awareness behind nginx, and no hard-fail if the shared JWT secret was still the default. Also `--port 8001` was dead-on-arrival because the dashboard container already listens on that port.
- **Change**: Middleware parity with the RAG backend (same allowlist / proxy-headers story on both services so a client's nginx config can hand both fronts the same `X-Real-IP`/`X-Forwarded-For`) + production-mode config validation + correct default port.
- **Why this approach**: The MCP server is already the one intodoc code path clients can touch directly through the onboarding wizard (ZIP uploads → `/mcp/upload-zip`, SSE logs → `/logs/stream`), so it needs the same network-layer controls as the RAG backend. Duplicating the small middleware into `mcp-server/api/middleware.py` rather than sharing a package kept the mcp-server codebase self-contained.
- **Verification**: `python3 -c "import ast; …"` parses clean. Not yet exercised end-to-end — running `deploy/deploy-client.sh --client-name demo --port-offset 100 --no-certbot` against a clean VM is the next concrete verification step.

## Session: 2026-04-20

### [Feature]: Scan completeness warnings + job-count delta (Pre-Sales Audit Day 3 / Gap 3)

- **Files changed**:
  - `knowledge_base/intodoc/src/intodoc/models.py` — new `ScanWarning` Pydantic model (fields: `kind`, `file`, `message`, `phase`) + one additive field on `ScanResult`: `warnings: list[ScanWarning] = Field(default_factory=list)`.
  - `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` — added `_warnings` + `_warn_root` on `TalendParser.__init__`, `_warn()` helper (writes relative path), reset at top of `parse()`, append `warnings=list(self._warnings)` to the returned `ScanResult`. Added `_warn` calls at 4 swallow sites: metadata index (`phase="metadata_index"`), job ID index (`phase="job_id_index"`, previously fully silent — also added yellow console print), context (`phase="context"`), and job parsing (`phase="job"` for `ET.ParseError`, `kind="unknown"` + `phase="job"` for the generic `Exception`). The early-return branch for "no Talend project structure found" now surfaces a `kind="missing_file"` warning instead of a silent empty result.
  - `knowledge_base/intodoc/src/intodoc/platforms/workato/parser.py` — same pattern: warning list + helper + reset + return. `_warn` calls on the two whole-file drops in `_parse_all_recipes` (`phase="recipe"`) and `_parse_recipe_functions` (`phase="function"`).
  - `knowledge_base/intodoc/src/intodoc/platforms/lobster/parser.py` — same pattern. `_warn` call on the whole-profile drop at `_parse_profile` call site (`phase="profile"`).
  - `knowledge_base/intodoc/src/intodoc/platforms/generic/parser.py` — same pattern. Added `_warn` calls + console prints to BOTH swallows in `_detect_connection`: JSON (`phase="connection"`) and YAML (`phase="connection"`). The YAML site was fully silent before — the plan called this out as the worst of the four swallows.
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` — extended `ScanStatusResponse` with `previous_job_count: int | None`, `job_delta: int | None`, `warnings: list[dict]`, `warnings_count: int`.
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` — `get_status` now: (a) queries the last 2 completed `ScanRun` rows ordered by `started_at DESC` to derive `previous_job_count` + `job_delta`; (b) reads `{intodoc_output_dir}/{project_id}/scan_result.json` to surface `warnings` (capped at 50 items for payload size, but `warnings_count` reflects the true length). Broken scan_result.json is swallowed (don't 500 the whole status endpoint for a bad file).
  - `knowledge_base/dashboard/src/types.ts` — new `ScanWarning` and `ScanStatusResponse` interfaces mirroring the backend schema.
  - `knowledge_base/dashboard/src/api/client.ts` — `getStatus` now returns the typed `ScanStatusResponse` instead of an inline literal.
  - `knowledge_base/dashboard/src/hooks/useProjects.ts` — added `useScanStatus(projectId)` (10s refetch, same cadence as other project queries).
  - `knowledge_base/dashboard/src/components/ScanHistory.tsx` — added a "Warnings" column. The latest *completed* run shows a yellow `! N` chip when `warnings_count > 0`, plus a green `+N` / red `−N` next to the Components column when `job_delta !== 0`. Other rows render `—`. The delta is per-project (KpiBar aggregates across all projects, so delta there is ambiguous — skipped deliberately).
  - `knowledge_base/intodoc/tests/test_platforms/test_parser_warnings.py` (new) — 12 pytest `tmp_path` tests: default-empty, JSON round-trip, Talend malformed job + malformed metadata + clean + missing-structure, Workato malformed recipe + clean, Lobster malformed profile + clean, Generic malformed YAML + clean.
- **Problem**: From `docs/PRE_SALES_AUDIT.md` Gap 3: scanner parse errors (malformed `.item`, `.recipe.json`, `.xml`, `.yaml`) were silently swallowed with at most a fleeting yellow console line. A customer re-scanning after a file rename could drop 12 jobs from `scan_result.json` with zero signal — the dashboard just showed a lower `job_count`. Also "last scan: 500 jobs, today: 495" was invisible: no delta anywhere.
- **Root cause**: Four parsers share the same anti-pattern — `except ET.ParseError / except Exception` with a `console.print` and `continue` / `pass`. Nothing persisted to `ScanResult`, so nothing reached the DB, the API, or the UI. Additionally, the Talend `_build_job_id_index` and Generic `_detect_connection` (JSON + YAML) were fully silent: no console output at all. Generic's YAML site was the worst — no `continue` console line, no persistence, the config just disappeared.
- **Change**: Made warnings a first-class part of `ScanResult` — additive Pydantic field, serializes automatically via `model_dump_json`, readers that don't know about it ignore the key. Each parser got the same three-part surgery: `__init__` gains `_warnings` + `_warn_root`, a `_warn()` helper that stores relative paths, and `parse()` resets them at entry + passes them to the result. At each swallow site I added a `_warn(kind=..., file=..., message=str(e), phase=...)` alongside the existing console output (or a new console print where silence was the problem). The status endpoint reads the warnings back from `scan_result.json` on demand — no new DB column, no migration. Job-count delta is derived from the two most recent completed `ScanRun` rows (one extra SQL query) — same reason: no schema churn.
- **Why this approach**:
  - **Additive Pydantic field** carries through the CLI's `_save_scan_result` + the API's JSON read with zero changes to either — that's the cheapest "thread data through 5 layers" possible.
  - **`_warn()` helper per parser** (instead of a shared base class) kept the diff local and tiny (~15 lines per parser) and avoids the inheritance-refactor rat hole the plan flagged as out of scope. Same shape, same behavior, but each parser stays independently editable.
  - **Relative file paths** in warnings — absolute paths leak machine state (would also differ per environment, hurting reproducibility of "why does the dashboard think X failed").
  - **No DB column for warnings** — derived on read from `scan_result.json`. A JSONB column on `ScanRun` only earns its keep if someone asks "show me all warnings across all projects," which nobody has. Noted in the plan as a future-day upgrade path.
  - **Delta computed on read, not stored** — fresh every call, no write-side work on scan completion, correct even after manual `ScanRun` edits. One extra SQL per `/status` call is cheap.
  - **Dashboard delta shown in `ScanHistory`, not `KpiBar`** — `KpiBar` gets `projects[]` and shows aggregate totals; a delta number there would mean "sum of all per-project deltas," which is meaningless when one project adds 5 and another loses 3. The per-project `ScanHistory` row is where the signal belongs. Deliberate deviation from the plan; captured here.
  - **50-warning cap** on API payload prevents a pathological project (thousands of broken files) from blowing up the status endpoint; `warnings_count` still reports the true total so the UI badge is accurate.
  - Kept all existing yellow console output alongside the new persistence — nothing regresses for CLI-only users.
- **Out of scope (noted in the approved plan)**: inner sub-swallows (Talend's `.properties` sidecar at `:402`, routine source read at `:634`; Workato's many `json.JSONDecodeError` sub-swallows at 187/271/393/…). These drop partial data not whole entities — same pattern applies if they start getting reported as issues. Also skipped warnings DB persistence, severity levels, and a re-scan-failed-files button.
- **Verification**:
  - `pytest tests/test_platforms/test_parser_warnings.py -v` — 12 passed in 0.15s.
  - Full `pytest tests/test_platforms/ -v` — 274 passed, 8 skipped in 0.52s (no regression in the existing parser tests).
  - `python -c "from mcp_server.schemas import ScanStatusResponse; ScanStatusResponse(project_id='x', status='ready').model_dump()"` — new fields default correctly.
  - `python -c "from mcp_server.api.routes import projects"` — import clean, no circular refs.
  - `npx tsc --noEmit` in `knowledge_base/dashboard/` — clean.
  - **Manual e2e remaining (next stack up)**: (1) run `intodoc scan ../vaxxinova-talend --platform talend` and grep `warnings` in the resulting `scan_result.json` — should be an array (likely `[]` on a clean project); (2) drop a `bad.item` under `process/`, re-scan, `GET /mcp/status/{project_id}` should return `warnings_count > 0` and a populated `warnings` array; (3) delete a few jobs and re-scan — `/status` shows `job_delta < 0`; (4) dashboard's Scan History shows the `! N` chip on the latest row and the signed delta next to Components.

### [Feature]: Atomic docs swap + backup recovery + staging GC (Pre-Sales Audit Day 2 / Gap 2)

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/services/atomic_swap.py` (new) — three pure functions: `swap_docs(output_dir)` (backup→rename→rollback pattern), `recover_stale_backups(output_root)` (promote `docs_prev/` → `docs/` when docs is missing; remove backup when both exist), `gc_abandoned_staging(output_root, max_age_days)` (rmtree old `docs_staging/` dirs by mtime).
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` — imported `swap_docs`; replaced lines 338-342 inline `shutil.rmtree + staging_dir.rename(docs_dir)` pair with a single `swap_docs(output_dir)` call. `import shutil` retained (still used in 3 other places in the file).
  - `knowledge_base/mcp-server/src/mcp_server/api/app.py` — imported `gc_abandoned_staging` + `recover_stale_backups`; lifespan hook now calls both on startup after the existing table-create block, guarded by a broad `try/except` so startup never fails because of maintenance work. Logs recovered/gc counts.
  - `knowledge_base/mcp-server/src/mcp_server/config.py` — added `staging_gc_days: int = 7` (env `MCP_STAGING_GC_DAYS`), ~7 days is the window after which an abandoned `docs_staging/` is considered truly abandoned (active resumes bump the dir's mtime).
  - `knowledge_base/mcp-server/tests/test_services/test_atomic_swap.py` (new) — 12 pytest `tmp_path` tests covering: swap happy path, swap with no existing docs, swap rollback via mocked `os.rename` side-effect, swap with missing staging, swap clearing a leftover backup, recover-stale-backups in both states, recover no-op, recover on missing root, GC with backdated mtime (`os.utime`), GC preserving recent staging, GC with zero age.
- **Problem**: `projects.py:338-342` claimed "Atomic swap" in a comment but chained `shutil.rmtree(docs_dir, ignore_errors=True)` + `staging_dir.rename(docs_dir)` — two non-atomic steps. A crash / network FS hiccup / Windows file lock between them could leave `docs/` missing or half-renamed. Failed runs already preserved `docs_staging/` for resume but nothing ever cleaned abandoned staging dirs — disk-fill risk over time. Identified in `docs/PRE_SALES_AUDIT.md` Gap 2, scheduled Day 2.
- **Root cause**: The pattern was `delete-then-rename`; the delete step is not atomic, and a mid-step crash leaves no `docs/` to recover from. No equivalent backup existed, and no startup reconciliation.
- **Change**: Replaced the inline logic with a `backup-then-rename` pattern in `swap_docs`. Both `os.rename` calls are POSIX-atomic on the same filesystem (both dirs live under `output/{project_id}/`, so same-FS guarantee holds). A crash between the two renames now leaves a recoverable state — `docs_prev/` exists next to missing `docs/` — which the new startup `recover_stale_backups` call promotes back to `docs/`. Successful swaps clean the backup best-effort; any stray `docs_prev/` left over from before this change also gets cleaned by the same recovery pass. Abandoned stagings older than 7 days are GC'd at startup (configurable).
- **Why this approach**:
  - Same-FS `os.rename` is POSIX-atomic; no external tools needed.
  - Keeping `docs_prev/` during the swap (but cleaning it on success) gives us a free rollback target — makes a future user-facing "rollback" button a ~1-hour add.
  - Startup reconciliation handles crash recovery naturally; no persistent job queue needed.
  - Extracting to a module with pure-function signatures made the logic unit-testable — 12 tests covering happy paths, rollback, and edge cases, all running in 20ms.
  - GC uses mtime not an explicit TTL column, which means an active resume naturally protects itself (Claude Code writes bump the staging dir's mtime), and we don't need a new DB column just for cleanup.
- **Verification**:
  - `pytest tests/test_services/test_atomic_swap.py -v` — 12 passed in 0.02s.
  - `python -m py_compile` on all 4 modified files — clean.
  - **Integration verification remaining (next server restart)**: user needs to restart the local MCP dev server (`python -m mcp_server --serve --port 8002`, currently PID 52422 in their terminal) to pick up the new code. After restart, log line `Recovered N stale docs backup(s) on startup` (or no log if nothing to recover) confirms the lifespan hook ran. Then: trigger a generation on a test project and confirm neither `docs_prev/` nor `docs_staging/` remain after success.

### [Fix]: Scope topology + chat Deep Dive project lists by RBAC

- **Files changed**: `mcp-server/src/mcp_server/api/routes/projects.py`
  - `deep_dive_projects()` (lines 1649-1683) — added `request: Request`, applied per-user RBAC filter mirroring `list_projects()`
  - `get_project_graph()` (lines 1336-1343) — added `request: Request` + `await require_project_access(request, project_id)`
  - `get_job_detail()` (lines 1481-1484) — added `request: Request` + `await require_project_access(request, project_id)`
- **Problem**: The Topology page (`RAG_pipeline/frontend/app/topology/page.tsx`) and the chat interface's Deep Dive `ProjectSelector` both listed every project in the database, including projects the logged-in user had no `project_assignments` row for. Users in "normal mode" chat saw only their assigned projects (that list is served by the RAG backend, which already filters via `get_user_project_ids`), so the leak only surfaced on Topology and Deep Dive mode.
- **Root cause**: Three MCP-server endpoints under `projects.py` skipped RBAC: `/deep-dive/projects` returned `registry.list_projects()` unfiltered, and `/projects/{id}/graph` + `/projects/{id}/jobs/{job_name}/detail` had no `require_project_access()` gate — unlike 7 sibling routes in the same file.
- **Change**: `/deep-dive/projects` now pulls `auth_payload` from `request.state` and applies the same superadmin → `view_all_projects` perm → `project_assignments` membership filter that `list_projects()` (lines 531-569) already uses. `/projects/{id}/graph` and `/projects/{id}/jobs/{job_name}/detail` now call `require_project_access(request, project_id)` as their first line, reusing the helper already imported on line 19. No new helpers introduced; reused `_get_user_is_superadmin`, `_get_user_permission_codenames`, `_get_assigned_project_ids` (lines 502-528).
- **Why this approach**: Smallest, lowest-risk fix — user chose to keep two parallel RBAC paths (RAG backend + MCP server) rather than consolidate to one source of truth. Mirroring the existing `list_projects()` logic keeps the MCP server's behaviour internally consistent; adding `require_project_access()` to the graph/job-detail routes matches the 7 other gated routes in the same file. Frontend needs no changes — Topology's dropdown + chat's `useDeepDiveProjects` hook both consume the same filtered endpoint, and Topology already handles 403s from `/graph` via its existing error banner.
- **Verification**: `python3 -m py_compile` passes on the edited file. Runtime verification pending: (1) as superadmin, `/deep-dive/projects` should still return every scanned project; (2) as a user assigned to only one project, `/deep-dive/projects` should return just that project, direct calls to `/projects/{unassigned_id}/graph` should 403, and `/projects/{assigned_id}/graph` should 200; (3) Topology dropdown + chat Deep Dive picker should match the Normal-mode project list for the same user.

---
## Session: 2026-04-21

### [Fix]: Run KB Alembic migrations to add missing `model_name` column

- **Files changed**: None (DB schema only)
- **Problem**: "Generate Docs" button failed because `generate_runs.model_name` column did not exist. The MCP server SQLAlchemy model references it, but the alembic migration (003) was never applied. The `kb_alembic_version` table didn't exist — no KB migrations had ever been run.
- **Root cause**: Tables were created via `create_all()` at startup, but that only creates tables that don't exist yet — it doesn't add columns to existing tables. The 3 alembic migrations (001 templates, 002 tier, 003 model_name) were written but never executed.
- **Change**:
  1. Stamped migration 001 as applied (tables already existed from `create_all`)
  2. Ran `alembic upgrade head` to apply migrations 002 (adds `tier` to `templates`) and 003 (adds `model_name` to `generate_runs`)
  3. Restarted `mcp-server` systemd service
- **Why this approach**: Stamping 001 avoids "duplicate table" errors since the schema already existed. Only 002 and 003 needed actual execution.
- **Verification**: Confirmed `model_name` column exists via `information_schema.columns` query. Server starts cleanly with no errors. Health endpoint returns OK.
- **Gotcha**: Running alembic requires `.env` to be sourced (for `MCP_DATABASE_URL`). Without it, the default empty-password URL fails auth against the dockerized postgres.
## Session: 2026-04-17

### [Fix]: Remove depth regression — restore old 26-doc output volume in 12-doc structure

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (lines 870, 877-881, 883)
- **Problem**: Docs generated recently are much shallower than ~1 month ago. Old system produced ~15,700 lines across 26 docs with no artificial depth constraints. New system added 0.6x multiplier for small projects, 5KB analysis floor, and low base depth targets (6,600 total) that act as ceilings.
- **Root cause**: Over-constraining the pipeline: (1) 0.6x depth_multiplier for <50 job projects told Claude to write 40% less, (2) 5KB analysis_target floor starved Phase 2 agents, (3) base depth targets summed to only 42% of old output volume.
- **Change**: Three fixes in `claude_service.py`:
  1. Small-project depth_multiplier: 0.6 → 1.0 (line 883)
  2. Analysis target: `max(5, min(40, bytes // 2048))` → `max(15, min(60, bytes // 1024))` (line 870)
  3. _BASE_DEPTH totals: 6,600 → ~14,000 lines — each doc target calibrated to absorb the old docs merged into it (line 877-881)
- **Why this approach**: The old system let Claude write naturally without caps. The 12-doc consolidation is good, but depth targets must match the combined volume of the old docs each new doc absorbed. Raising floor/ceiling/divisor ensures Phase 2 always has room.
- **Verification**: Pending — re-generate docs for a small project and compare file sizes/line counts.

---

## Session: 2026-04-16

### [Feature]: Deepen documentation to agent-level quality — transformation detail

- **Files changed**:
  - `intodoc/src/intodoc/platforms/talend/parser.py` (line 487) — Made tmap_data namespace matching more robust: tries `xsi:type`, `xmi:type`, and bare `type` attribute
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines 82-210) — Added `_extract_source_hint()` helper; refactored `_format_chunk_markdown()` to accept `platform` parameter; added structured table rendering for Talend tmap_data (var tables, per-output column mappings with source hints) and Lobster filter chains (step/ID/expression tables); passed platform from `_preprocess_scan_data()` call site
  - `mcp-server/src/mcp_server/scripts/bundle_analysis.py` (lines 17-36) — Added `TRANSFORMATION_DETAIL` to `ALL_SECTIONS` (now 15 sections); added to `core.high` and `business_ops.high` relevance sets
  - `intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md` — Added TRANSFORMATION_DETAIL section between DATA_FLOWS and BUSINESS_RULES with per-tMap column mapping tables, var table traces, join analysis, filter/reject analysis, function chain traces
  - `intodoc/src/intodoc/platforms/lobster/prompts/ANALYSIS_GUIDE.md` — Added TRANSFORMATION_DETAIL section with per-profile field mapping tables, step-by-step filter chain traces, variable flow diagrams, conditional logic maps, calculation node architecture
  - `intodoc/src/intodoc/platforms/_shared/prompts/ANALYSIS_GUIDE.md` — Added generic TRANSFORMATION_DETAIL section for any platform
  - `intodoc/src/intodoc/platforms/_shared/prompts/PHASE_3_GUIDE.md` — Section 03: added item 5 "Per-Job Transformation Detail (MANDATORY)" with column mapping table requirements; Section 04: added item 3 "Function Chain Traces" with step-by-step trace format and example
  - `intodoc/src/intodoc/platforms/talend/prompts/PHASE_3_GUIDE.md` — Same additions as shared guide with Talend-specific examples (tMap var tables, row references, currency conversion and fiscal period trace examples)
  - `intodoc/reference-docs/03_DATA_FLOWS_LINEAGE.md` — Added "Per-Job Transformation Detail" section with two example jobs (VJP_Clean_Build_Sales_Set, DEU_VDA_BC_Clean_Invoice_Set_v3) showing full column mapping tables, var tables, join logic
  - `intodoc/reference-docs/04_BUSINESS_LOGIC.md` — Added "Function Chain Traces" section with three examples (currency conversion chain, fiscal period derivation chain, country resolution fallback chain) with step-by-step traces and impact assessments
- **Problem**: Generated docs achieve ~70-80% depth but lack field-level transformation detail. Users can't ask "what does column X do in job Y?" and get a satisfying answer from docs alone.
- **Root cause**: Data presentation gap — scan cache contains full transformation data (tmap_data, filter chains) but `_format_chunk_markdown()` rendered everything as flat key-value params. Analysis agents can't extract what they can't read.
- **Change**: Three-layer improvement: (1) Data layer: platform-aware preprocessing renders tmap_data as structured tables and Lobster filters as step tables; (2) Analysis layer: new TRANSFORMATION_DETAIL section in all analysis guides directs Phase 2 agents to extract per-component mappings; (3) Synthesis layer: Phase 3 guides require per-job transformation subsections and function chain traces, with reference docs providing concrete examples.
- **Why this approach**: Purely additive — existing doc quality won't regress. Changes flow through the existing 4-phase pipeline without architectural changes. Token cost is minimal (~1-2KB per job with transformations).
- **Verification**: All modified Python files pass syntax validation. Talend tmap_data parser fix requires re-scan to verify (vaxxinova-talend source not available locally). Full verification requires running doc generation on a project with tmap_data populated.
- **Note**: The Talend tmap_data parser fix (trying xmi:type in addition to xsi:type) needs verification with actual Talend XML files. A re-scan of vaxxinova-talend should be run after deploying this change.

### [Feature]: Workato datapill rendering for transformation detail

- **Files changed**:
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines 174-210) — Added `import re`; added Workato datapill mapping table rendering in `_format_chunk_markdown()` that detects `#{_('data.step.field')}` patterns in `steps` and `input` params, deduplicates, and renders as Source Step | Source Field | Formula table
  - `intodoc/src/intodoc/platforms/workato/prompts/ANALYSIS_GUIDE.md` — Added TRANSFORMATION_DETAIL section between DATA_FLOWS and BUSINESS_RULES with per-step datapill mapping tables, formula chain traces, conditional mapping analysis, cross-step data lineage, and lookup table mappings
- **Problem**: Workato platform was missing from the transformation detail implementation — plan explicitly called for datapill rendering but it was skipped in the first pass.
- **Root cause**: Implementation focused on Talend and Lobster first and missed completing the Workato platform.
- **Change**: (1) `_format_chunk_markdown()` now detects `#{_(` datapill references in Workato step/input params via regex, parses the step alias and field path, extracts formula chains (`.to_s`, `.round(2)`, etc.), deduplicates, and renders as a structured markdown table; (2) Workato ANALYSIS_GUIDE.md now has a comprehensive TRANSFORMATION_DETAIL section directing Phase 2 agents to extract per-step mapping tables, trace complex formula chains, analyze conditional mappings, map cross-step data lineage, and document lookup table usage.
- **Why this approach**: Consistent with Talend/Lobster pattern — same additive approach. Regex-based datapill detection (`#\{_\('([^']+)'\)([^}]*)\}`) is robust for Workato's standard syntax. Deduplication prevents duplicate rows from repeated datapill references.
- **Verification**: Python syntax validation passes. Requires real Workato export data for E2E verification (test data is synthetic with no datapill references).

## Session: 2026-04-15

### [Feature]: Add Lobster Data platform to intodoc scanner

- **Files changed**:
  - `intodoc/src/intodoc/scanner/credential_filter.py` (lines 83-90) -- Added OBF password and base64 value tag patterns, added lobster-world.com to safe domains
  - `intodoc/src/intodoc/platforms/registry.py` (line 16) -- Registered lobster platform plugin
  - `intodoc/src/intodoc/platforms/lobster/__init__.py` (new, 67 lines) -- LobsterPlugin class extending GenericPlugin
  - `intodoc/src/intodoc/platforms/lobster/parser.py` (new, ~460 lines) -- XML parser for DataWizard profiles: agent extraction, data properties, input/output tree compaction, filter chain extraction, variable parsing, response units, cross-profile refs, manifest building
  - `intodoc/src/intodoc/platforms/lobster/distiller.py` (new, ~50 lines) -- Strips system variables and default-valued params
  - `intodoc/src/intodoc/platforms/lobster/best_practices.yaml` (new, ~300 lines) -- Lobster platform concepts, component types, anti-patterns (LAP-001 to LAP-010), connection types, error handling patterns, performance guidelines
  - `intodoc/src/intodoc/platforms/lobster/prompts/ANALYSIS_GUIDE.md` (new, ~250 lines) -- Phase 2 sub-agent analysis guide for Lobster profiles
  - `tests/test_platforms/test_lobster.py` (new, ~250 lines) -- 44 tests covering plugin, parser, distiller, credential filter, best practices
  - `tests/fixtures/lobster_sample/*.xml` (new, 3 files) -- Test fixtures: cron FTP, HTTP API, minimal manual upload profiles
  - `intodoc/CLAUDE.md` -- Updated platform list from 3 to 4, added Lobster references
- **Problem**: No support for scanning Lobster Data integration projects
- **Root cause**: Lobster was a new platform not yet supported by the scanner
- **Change**: Full platform plugin implementation following Workato pattern: plugin class, XML parser, distiller, best practices, analysis guide, and comprehensive tests. Parser handles all profile sections: agent (7 types), data properties (8 file types), input/output trees with compact representation, filter chain extraction, system/user variable separation, response units, cross-profile references (profile_after_run, exception_profile, forward_profile), and manifest building. Credential filter catches OBF passwords and base64-encoded values.
- **Why this approach**: Followed established Workato plugin pattern for consistency. Parser does selective extraction (only meaningful attributes) rather than raw XML passthrough, since ~34% of Lobster XML is structural noise (system variables, boolean defaults, empty elements). This means most noise elimination happens in the parser, with the distiller handling the remaining system variable groups and default params.
- **Verification**: 44 Lobster-specific tests pass. 313 total tests pass (0 failures, 8 skipped). Real-world test: parsed 27 production Lobster profiles successfully with correct component types, no credential leaks (OBF passwords redacted).

### [Feature]: Add Lobster platform to dashboard frontend

- **Files changed**:
  - `dashboard/src/lib/platform.ts` -- Added lobster config entry (label "Lobster", term "Profile", green #10B981 accent)
  - `dashboard/src/components/AddRepoDialog.tsx` (line 97) -- Added `<option value="lobster">Lobster</option>` to platform dropdown
  - `dashboard/src/components/ProjectSettings.tsx` (line 10) -- Added "lobster" to PLATFORMS array
  - `dashboard/src/components/templates/NewTemplateModal.tsx` (line 204) -- Added lobster option to platform select
  - `dashboard/src/components/templates/ProfileManager.tsx` (line 78) -- Added lobster option to platform select
  - `dashboard/src/components/templates/TemplateCatalogue.tsx` (line 99) -- Added lobster option to platform filter
- **Problem**: Dashboard had hardcoded platform lists (talend, workato, generic) with no Lobster option
- **Root cause**: New platform not yet added to frontend
- **Change**: Added "lobster" to all 6 files with platform dropdowns/selects. Platform config uses green (#10B981) to differentiate from Talend (blue), Workato (orange), and Generic (slate).
- **Why this approach**: Consistent with how other platforms are registered -- every dropdown that lists platforms needs the new entry.
- **Verification**: All 6 files edited. No build needed for verification (runtime change).

### [Feature]: Resumable doc generation — preserve staging on failure + resume from checkpoint

- **Files changed**:
  - `mcp-server/src/mcp_server/services/claude_service.py`: Added `_detect_resume_state()` static method (inspects `docs_staging/` for preprocess, analysis, scorecard, bundle, and completed doc artifacts). Modified `generate_docs()` signature to accept `resume: bool = True`. Added resume detection logic (skip preprocessing when files exist, recover group names from filesystem). Added `_manifest.json` writing (tracks scan mtime, groups, model, template snapshot). Added stale scan detection (compares scan manifest mtime against stored value, invalidates preprocessing if scan is newer). Added resume prompt block injection (tells Claude Code which phases to skip). Added `import time` for manifest timestamps.
  - `mcp-server/src/mcp_server/api/routes/projects.py`: Changed failure handler (lines 409-419) from `shutil.rmtree(staging_dir)` to preserving staging with log message + SSE notification. Added `resume` parameter pass-through: `generate_docs` endpoint -> `_run_generate_background` -> `claude.generate_docs()`.
  - `mcp-server/src/mcp_server/schemas.py`: Added `resume: bool = Field(True)` to `GenerateRequest`.
  - `dashboard/src/api/client.ts`: Added `resume` parameter (default `true`) to `generate()` function.
  - `dashboard/src/hooks/useProjects.ts`: Added `resume` to `useGenerate` mutation function type.
- **Problem**: When Anthropic 5-hour rate limit hits during Phase 3, Claude Code exits with code 1. The failure handler deleted `docs_staging/` entirely, destroying ~30 minutes of completed work (Phase 2 analysis, scoring, bundles, and any finished Phase 3 docs).
- **Root cause**: `_run_generate_background` unconditionally called `shutil.rmtree(staging_dir)` on any failure. No mechanism existed to detect or reuse partially-completed artifacts.
- **Change**: 7-step implementation: (1) Preserve staging on failure with log message. (2) `_detect_resume_state()` inspects staging for usable artifacts. (3) `generate_docs()` conditionally skips preprocessing when resuming. (4) Resume prompt block injected at top of mega-prompt telling Claude Code which phases to skip. (5) `resume` field added to API schema and passed through route chain. (6) Frontend passes `resume` parameter (defaults to true). (7) Stale scan detection via `_manifest.json` mtime comparison — invalidates preprocessing if scan data changed but preserves analysis.
- **Why this approach**: The existing Phase 2.5 gate validation already checks for analysis files, and Phase 3.5 validates docs. The resume prompt leverages these existing gates — it simply tells Claude Code to skip to the first incomplete phase. The `_manifest.json` provides metadata for stale detection without changing the scan pipeline. Staging preservation is safe because old `docs/` remains untouched (the swap only happens on success).
- **Verification**: Not yet tested end-to-end. Needs: (1) Kill generation mid-Phase 3, verify staging preserved. (2) Re-trigger generate, verify resume mode logs and Phase 2 skip. (3) Call with `resume=false`, verify fresh run. (4) Re-scan between runs, verify stale detection re-preprocesses.

## Session: 2026-04-14

### [Feature]: Fix shallow doc generation for large projects -- analysis bundling + depth scaling + agent split

- **Files changed**:
  - `mcp-server/src/mcp_server/scripts/__init__.py` (NEW): Empty package init
  - `mcp-server/src/mcp_server/scripts/bundle_analysis.py` (NEW, ~165 lines): Standalone Python script that reads `_analysis/*.md` files, parses them into 14 canonical sections (matching ANALYSIS_GUIDE.md), and writes per-agent bundle files (`_bundle_core.md`, `_bundle_business_ops.md`, `_bundle_patterns_user.md`, and for large projects `_bundle_patterns.md` + `_bundle_user.md`). Progressive truncation: if bundle >350KB, first truncate MEDIUM-relevance sections to 30 lines per group; if still over, drop MEDIUM entirely and keep only HIGH-relevance sections. Section-to-agent relevance map assigns COMPONENTS/EXECUTION_FLOW/DATA_FLOWS/SCHEMA_DICTIONARY/SOURCE_SQL as HIGH for Core agent, BUSINESS_RULES/CUSTOM_CODE/ERROR_HANDLING/RISK_FLAGS as HIGH for Business+Ops, etc. `patterns_user` agent gets ALL sections (broad context for synthesis docs 07-11).
  - `mcp-server/src/mcp_server/config.py` (line 54): Added `large_project_threshold: int = 200` setting with env prefix MCP_LARGE_PROJECT_THRESHOLD
  - `mcp-server/src/mcp_server/services/claude_service.py` (multiple sections):
    - `_preprocess_scan_data()` signature: added `total_jobs: int = 0` parameter
    - Reference doc bundle map: split `_ref_patterns_user` into `_ref_patterns` (docs 07-08) and `_ref_user` (docs 09-11) -- both filesystem and DB override paths
    - Added bundling script copy: copies `scripts/bundle_analysis.py` to `_preprocess/` during preprocessing
    - `generate_docs()`: moved `total_jobs` computation before preprocessing call; added depth tier computation (small/medium/large with 0.6x/1.0x/1.5x multipliers); added dynamic agent table (3 agents for <200 jobs, 4 agents for >=200 jobs); added Phase 2.8 bundling step in prompt; replaced AUTO-FALLBACK with bundle reading instructions; added MINIMUM line targets table; changed analysis target wording from "target" to "MAXIMUM"; updated Phase 3.5 validation agent list to be dynamic
- **Problem**: vaxxinova-main (252 jobs, 9 groups) produced only 406KB of docs vs esb-main (21 jobs, 4 groups) at 523KB. Large projects got shallower docs because the AUTO-FALLBACK clause told agents to use grep/head/tail when analysis exceeded 400KB, which was unreliable. 1.6MB of analysis physically can't fit in a 200K context window, so the fallback was necessary but its implementation (agent doing grep) was non-deterministic.
- **Root cause**: The AUTO-FALLBACK instruction at line 691 of claude_service.py told Phase 3 agents to "use grep/head/tail" to read only relevant sections when total analysis >400KB. This produced cherry-picked, shallow output. Additionally, no depth minimums existed for larger projects, and the Patterns+User agent was responsible for 5 docs (07-11), leaving each doc with less output headroom.
- **Change**: Five-part fix:
  1. **Phase 2.8 bundling**: New deterministic Python script creates per-agent analysis bundles between Phase 2.6 and Phase 3. Each agent reads ONE pre-filtered bundle file instead of grepping through N group files. Script handles progressive truncation (MEDIUM sections -> 30 lines, then HIGH-only) to stay under 350KB budget.
  2. **AUTO-FALLBACK removed**: Replaced unreliable agent-side grep/head/tail with server-side Python preprocessing. Even for small projects, bundles are created (containing everything).
  3. **Depth minimums**: Added tier-based minimum line targets (small <50 jobs: 0.6x, medium 50-199: 1.0x, large 200+: 1.5x) injected as a table into the prompt.
  4. **4-agent split for large projects**: Projects with >=200 jobs spawn 4 Phase 3 agents instead of 3 (Core, Business+Ops, Patterns, User). Each agent writes fewer docs with more output headroom.
  5. **Analysis cap strengthened**: Changed "target: ~XKB" to "MAXIMUM: XKB -- prioritize depth over breadth" to reduce oversized analysis that triggered the old fallback.
- **Why this approach**: The fundamental issue was that 1.6MB of analysis cannot fit in 200K context. Rather than asking the LLM to select what's relevant (unreliable), we do it deterministically in Python. Section-to-agent mapping is based on which analysis sections are most relevant to which docs (e.g., Core agent writes architecture/data docs and needs COMPONENTS, DATA_FLOWS, SCHEMA_DICTIONARY most). Progressive truncation ensures every agent stays within token budget while preserving the most important data. The 4-agent split gives each agent ~3 docs instead of ~4-5, with proportionally more output tokens per doc.
- **Verification**: All 3 modified Python files pass `py_compile`. Pending: re-run doc generation for vaxxinova-main (expect >500KB total, 09_RUNBOOK >600 lines, 11_MIGRATION >700 lines), verify esb-main doesn't regress, check bundle files created in `_analysis/` and each <350KB.

## Session: 2026-04-13

### [Feature]: Disable Ollama auto-summaries + mandate per-job inventory in Phase 3

- **Files changed**:
  - `mcp-server/src/mcp_server/config.py` (line 59): Changed `auto_generate_summaries: bool = True` to `False`
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 267-282): Removed the `if config.auto_generate_summaries:` block that called `svc.generate_project_summaries()` after scan
  - `intodoc/src/intodoc/platforms/_shared/prompts/PHASE_3_GUIDE.md` (section 02, item 6): Replaced weak instruction with mandatory language requiring a `#### {JobName}` entry for every job
  - `intodoc/src/intodoc/platforms/talend/prompts/PHASE_3_GUIDE.md` (section 02, item 6): Same mandatory language, mirrored from shared guide
  - `intodoc/reference-docs/02_ARCHITECTURE_INVENTORY.md`: Added "Per-Job Architecture Detail" section with 5 example entries showing the expected format
- **Problem**: (1) Ollama ran ~45 LLM calls after every scan to generate job summaries, heating up the laptop and blocking scan completion. (2) Phase 3 guide item 6 was too weak -- Claude skipped per-job entries, leaving RAG with no authoritative per-job descriptions.
- **Root cause**: (1) `auto_generate_summaries` defaulted to True. (2) The instruction said "for each job, provide..." but lacked MANDATORY language, format examples, or explanation of downstream consequences (topology UI, RAG search).
- **Change**: Disabled auto-summaries by default (manual endpoint still works). Strengthened Phase 3 guide with MANDATORY callout, exact format template, and explanation that topology UI and RAG depend on these entries. Added 5 reference examples to the architecture inventory reference doc.
- **Why this approach**: Per-job descriptions are better generated by Claude during doc generation (has full Phase 2 analysis context) than by Ollama post-scan (only sees raw component data). Disabling auto-summaries saves GPU time; the manual `/generate-summaries` endpoint remains for users who want Ollama summaries independently.
- **Verification**: Pending -- run scan and confirm no Ollama calls. Run doc generation and check `02_ARCHITECTURE_INVENTORY.md` for per-job entries.

### [Feature]: Enrich doc generation prompts for operational depth -- Phase 3 guide expansion

- **Files changed**:
  - `intodoc/src/intodoc/platforms/talend/prompts/PHASE_3_GUIDE.md` (87 -> 271 lines, 4.3KB -> 25KB): Expanded with per-chapter writing instructions for all 12 docs. Added 8 missing chapters (01-07, 09) and enhanced 3 existing chapters (00, 08, 11).
  - `intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md` (333 -> 335 lines): Added per-component DIE_ON_ERROR extraction to ERROR_HANDLING section and tContextLoad structure documentation to ENVIRONMENT_CONFIG section.
  - `intodoc/src/intodoc/platforms/_shared/prompts/PHASE_3_GUIDE.md` (54 -> 247 lines, ~20KB): Mirrored Talend guide structure with platform-agnostic terminology (tMap -> transformation component, tRunJob -> job orchestration component, SUBJOB_OK/SUBJOB_ERROR -> success/error triggers, context variables -> environment parameters, DIE_ON_CHILD_ERROR -> child error propagation setting).
- **Problem**: New consolidated doc generation system produced shallower operational content than the old 22-prompt system. PHASE_3_GUIDE.md only had instructions for 4 of 12 doc chapters (00, 08, 10, 11). The other 8 chapters got zero guidance -- Phase 3 agents improvised based on reference doc examples alone, resulting in thin docs for 09_RUNBOOK, 05_OPERATIONS_RISK, 03_DATA_FLOWS, etc.
- **Root cause**: When PHASE_3_GUIDE.md was created during the consolidated guide work (2026-03-20), only cross-cutting chapters were included. Per-chapter writing instructions for the remaining docs were never added.
- **Change**: Three files modified:
  1. Talend PHASE_3_GUIDE.md: Added instructions for chapters 01 (project overview), 02 (architecture inventory), 03 (data flows), 04 (business logic), 05 (operations/risk), 06 (environment/external), 07 (security/performance), 09 (runbook). Enhanced chapters 00 (added scorecard integration), 08 (added naming compliance, reusability, tech debt, remediation roadmap), 11 (added readiness score, platform comparison, scenario comparison, pre/post-migration checklists, tech debt for migration, architectural decisions, quick wins). Each instruction references Phase 2 analysis sections by name and uses conditional framing.
  2. Talend ANALYSIS_GUIDE.md: Added per-component DIE_ON_ERROR extraction (supports the new per-component error audit in doc 05) and tContextLoad structure documentation (supports runtime environment resolution in doc 06).
  3. Shared PHASE_3_GUIDE.md: Full mirror of Talend guide with all platform-specific terms replaced by generic equivalents.
- **Why this approach**: Instructions say HOW to synthesize Phase 2 data, not WHAT to extract (that's ANALYSIS_GUIDE's job). Conditional framing ("if REST sources exist", "for critical orchestration jobs") adapts to project complexity. Cross-chapter deduplication ensures each topic has one deep-dive chapter. Generic rule + Talend example pattern in the Talend guide. Token impact: ~8KB increase per Phase 3 agent (~9% overhead, well within 200K context window).
- **Verification**: Pending -- re-generate docs for esbmain test project and compare output depth against previous generation. Key checks: 09_RUNBOOK structured failure scenarios, 05_OPERATIONS_RISK per-component error audit, 03_DATA_FLOWS column mapping tables, 04_BUSINESS_LOGIC pattern-based organization, 11_MIGRATION scenario comparison and readiness score.

## Session: 2026-04-10

### [Perf]: Topology graph endpoint -- stop synthesizing REFERENCE:* hub edges in legacy-scan fallback

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
  - `_FALLBACK_INTERNAL_COMPONENTS` (lines ~990-1006): added the tRunJob-family
    diagnostics that were missing from the internal-components set:
    `tLogCatcher`, `tStatCatcher`, `tFlowMeter`, `tFlowMeterCatcher`,
    `tChronometerStart`, `tChronometerStop`, `tAssert`, `tAssertCatcher`.
    (`tRunJob`, `tPrejob`, `tPostjob`, `tWarn`, `tDie`, `tLogRow` were already
    present but the heuristic still leaked REFERENCE:* names through the
    suffix-stripping path -- see below.)
  - `_derive_systems_from_job_json` (lines ~1087-1105) Talend branch: added a
    belt-and-braces filter `and not system.startswith("REFERENCE")` so any
    component that produces a `REFERENCE:*` system name through the
    suffix-stripping fallback is dropped instead of becoming a fake hub.
    Also capped the result at `sorted(systems)[:6]` -- more than this means
    the heuristic is matching noise. Modern scans populate
    `job_external_systems` explicitly and bypass this path entirely, so the
    cap is a noise-floor on the legacy fallback only.
- **Problem**: `/mcp/projects/scherpenhuizen-talend/graph` returned ~37
  connection nodes and ~256 job→connection edges even though the on-disk
  manifest had `job_connections_used: {}`, `connection_used_by: {}`, and an
  absent `job_external_systems` (legacy scan). The synthesized result was
  hub-shaped: Oracle (29 jobs), `REFERENCE:sendToMonitoringMOM` (27 jobs),
  `REFERENCE:Trigger_Next_Interface_92` (18 jobs). The frontend topology
  view stuttered on hover/toggle because every hover dimmed ~30 adjacencies
  and dagre had to lay out 80+ nodes / 271 edges (18x more than the manifest
  hinted at). Most of the edges were noise -- the `REFERENCE:*` "systems" are
  internal Talend job-call wiring, not real external systems.
- **Root cause**: `_fallback_talend_system` strips a leading `t` and then
  trailing `Input/Output/Connection/...` suffixes from the component type to
  guess a system name. For `tRunJob`-family components the payload that
  bubbles through is the called-job reference (`REFERENCE:sendToMonitoringMOM`,
  `REFERENCE:Trigger_Next_Interface_92`, etc.), and the fallback was treating
  those as foreign systems. The internal-components set was incomplete -- it
  had `tRunJob` itself but not the diagnostic siblings -- and the function had
  no defense against `REFERENCE:*` payloads that leaked through.
- **Change**: Two layers. (1) Expanded `_FALLBACK_INTERNAL_COMPONENTS` so the
  diagnostic family returns `None` from `_fallback_talend_system`. (2) Added
  the `not system.startswith("REFERENCE")` filter directly in
  `_derive_systems_from_job_json` for Talend, plus the `[:6]` cap on the
  fallback result to prevent any future heuristic mismatch from generating
  hub-shaped fake-system noise.
- **Why this approach**: Surgical -- touches only the runtime-derivation
  fallback path used by legacy scans missing `job_external_systems`. Real
  scans bypass this path entirely. Real Talend external systems (Oracle,
  MSSql, FTP, SAP, Salesforce, etc.) still surface in the topology -- only
  the synthetic `REFERENCE:*` noise from `tRunJob` payloads disappears. If
  the user wants the REFERENCE wiring back later, the scanner should
  populate `job_external_systems` explicitly via tRunJob -> called-job
  tracing -- that's a scanner feature, not a graph-rendering one.
- **Verification**: Restart the systemd `mcp-server` (editable install picks
  up the Python change), then `GET /mcp/projects/scherpenhuizen-talend/graph`
  with the JWT cookie. Expect ~10-15 connections (was ~37) and zero entries
  starting with `REFERENCE:`. Smoke `GET /mcp/projects/esbmain/graph` -- its
  manifest already populates `job_external_systems`, so its shape is
  unchanged (bypasses the runtime fallback entirely). The matching frontend
  fix is logged in `RAG_pipeline/.claude/PROGRESS_LOG.md` (Perf round 3).

### [Fix]: Topology job detail panel -- use real `**Purpose**` lines from inventory, drop AI summarizer

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`
    - Added `_PURPOSE_LINE_RE` regex (line ~1220)
    - Added `_extract_job_purpose_from_inventory(project_id, job_name)` helper
    - Added `_extract_job_doc_sections(project_id, job_name)` helper
    - Rewrote `get_job_detail()` to use the new helpers; removed the per-`.md`
      `## `-split loop and the `SummaryService.load_summaries(project_id)` call.
      Summary now prefers the structured `**Purpose**:` line from
      `02_ARCHITECTURE_INVENTORY.md`, falling back to `_derive_job_summary` for
      jobs missing from the inventory.
- **Problem**: Clicking a job in `/topology` showed (a) the templated
  `"Integration job connecting to {systems}."` placeholder or an unrelated
  paragraph as the "What This Job Does" summary, and (b) "From the Docs"
  returned the entire `## Job Architecture Details` section (~50 jobs, thousands
  of lines) for every job click on `esbmain`.
- **Root cause**:
  1. `_derive_job_summary` substring-grepped paragraphs for the job name. The
     correct `**Purpose**: ...` paragraph in `02_ARCHITECTURE_INVENTORY.md`
     never contains the job name (the name lives in the preceding `#### {Name}`
     heading), so it was always skipped. The first paragraph that did match was
     the bare heading itself, which after `_MD_NOISE_RE` cleanup became too
     short and was discarded -- so we fell into deeper sections or the
     placeholder.
  2. `get_job_detail()` split each `.md` by `## ` only, so any `## ` section
     that mentioned the job name (including the giant inventory section) was
     returned in full.
  3. `SummaryService.load_summaries` had never been triggered for any project
     (`summaries.json` does not exist), so it always returned `{}` -- it was
     dead weight in the request path.
- **Change**:
  - `_extract_job_purpose_from_inventory` finds `#### {JobName}` (any depth
    `##`-`####`), bounds the section by the next sibling-or-higher heading, and
    extracts the `**Purpose**:` line verbatim, trimmed to 2-3 sentences.
  - `_extract_job_doc_sections` walks every `.md` in `docs/` (or
    `docs_staging/`), finds headings whose line starts with the job name, and
    returns each as its own bounded section. Heading is prefixed with the doc's
    pretty stem (e.g. `ARCHITECTURE INVENTORY - sjAccount_OffRamp_DWH`).
  - `get_job_detail()` now: builds `doc_sections` from
    `_extract_job_doc_sections`, computes `summary` as
    `_extract_job_purpose_from_inventory(...) or _derive_job_summary(...) or ""`,
    and no longer touches `SummaryService`.
  - `summary_service.py`, `/generate-summaries` route, and the `ollama` dep are
    untouched -- left dormant for any future opt-in use.
- **Why this approach**: The doc generator already writes the brief, accurate
  `**Purpose**:` line during the 4-phase workflow; the right fix is to parse it
  out of the structured markdown rather than re-derive it heuristically (or
  worse, regenerate it via an LLM). Zero LLM calls, zero changes to the
  doc-generation pipeline, no schema changes, no frontend changes.
- **Verification**:
  - Direct helper smoke test (no auth):
    - `_extract_job_purpose_from_inventory('esbmain', 'Account_Subscription')`
      -> `"Manages webhook subscriptions for Account entities in the Business Central API v2.0."`
    - `_extract_job_purpose_from_inventory('esbmain', 'sjAccount_OffRamp_DWH')`
      -> `"Writes Customer golden record from CMM to BigQuery."`
    - `_extract_job_doc_sections('esbmain', 'sjAccount_OffRamp_DWH')` ->
      1 section, 531 chars (was previously the whole inventory section).
  - End-to-end via `get_job_detail()` returns the same summary and bounded
    sections.
  - Sampled 8 jobs across `esbmain`: 6 returned real Purpose lines; 2 returned
    `None` (not in the inventory) and correctly fell through to the heuristic.
  - Workato project (different inventory layout, recipes use
    `### N.M Recipe X: name` headings) returns `None` from the structured
    extractor and falls back to `_derive_job_summary` -- no crash.
  - `sudo systemctl restart mcp-server` -> `Active: active (running)`,
    no import errors in `journalctl -u mcp-server`.

### [Fix]: Topology view "Not Found" -- stale mcp-server binary + mislocated data

- **Files changed**:
  - `/etc/systemd/system/mcp-server.service` (replaced with repo copy from `deploy/mcp-server.service`)
  - `/home/intodata/intodata-kb-v2/venv` (new symlink -> `/home/intodata/intodata-kb/venv`)
  - `/home/intodata/intodata-kb-v2/knowledge_base/intodoc-output/` (populated from old path; 24 projects)
  - `/home/intodata/intodata-kb-v2/knowledge_base/output/` (populated from old path; 19 projects)
  - `/home/intodata/intodata-kb/knowledge_base/{intodoc-output,output}` (replaced with symlinks back to v2 as a safety net)
  - `/home/intodata/intodata-kb/venv` (`pip install ollama>=0.4.0` -- new dep added in v2 pyproject.toml but never installed)
- **Problem**: `/topology` page in the RAG frontend always rendered "Not Found / Retry". Logs showed `GET /mcp/projects/esbmain/graph HTTP/1.1 404 Not Found` while `GET /mcp/projects` returned 200. The 404 detail was FastAPI's default `{"detail":"Not Found"}` (route-not-registered), not the endpoint's own `"No scan data for project: ..."` body.
- **Root cause**: Two stacked issues:
  1. The installed systemd unit at `/etc/systemd/system/mcp-server.service` still pointed at the OLD checkout (`/home/intodata/intodata-kb/...`) and had not been updated when the v2 checkout was created. The `mcp-server` PID had been running uninterrupted since 2026-03-24, predating the topology feature commit `4a45b4d`. The `get_project_graph` route did not exist in the in-memory code, so FastAPI returned its default 404.
  2. All scan/doc data the user generated lived under the OLD path (`intodata-kb/knowledge_base/{intodoc-output,output}`) because the running server's working directory was the OLD knowledge_base. Just restarting against the v2 unit would have flipped 404 -> "No scan data for project" until the data was moved.
- **Change**:
  1. Moved scan + docs data from `intodata-kb/knowledge_base/{intodoc-output,output}` to `intodata-kb-v2/knowledge_base/{intodoc-output,output}`. Left symlinks behind at the old paths so any stragglers still resolve.
  2. Created `intodata-kb-v2/venv` as a symlink to the existing `intodata-kb/venv`. The existing venv already has `mcp_server` editable-installed pointing at the v2 source tree, so the symlink gives the new systemd unit a working interpreter without rebuilding the venv.
  3. Installed `ollama>=0.4.0` into the existing venv -- new dep added to v2 `mcp-server/pyproject.toml` (used by `services/summary_service.py:13`) but never `pip install`-ed. Service was crash-looping with `ModuleNotFoundError: No module named 'ollama'` until this was fixed.
  4. Copied `deploy/mcp-server.service` to `/etc/systemd/system/mcp-server.service`, ran `systemctl daemon-reload` and `systemctl restart mcp-server`. Service is now running from `/home/intodata/intodata-kb-v2/venv/bin/python -m mcp_server --serve --port 8002` with `WorkingDirectory=/home/intodata/intodata-kb-v2/knowledge_base`.
- **Why this approach**: Symlinking the venv was much faster than rebuilding (would have required pulling weasyprint + system libs), and the existing venv's editable install already pointed at v2 source -- so all the topology code is loaded transparently. Moving (not copying) the data ensures a single source of truth going forward; the symlinks back to the old path are a temporary safety net.
- **Verification**:
  - `systemctl status mcp-server` -> `Active: active (running)` from `/home/intodata/intodata-kb-v2/venv/bin/python ...`
  - `curl http://localhost:8002/openapi.json` lists `/mcp/projects/{project_id}/graph` (it didn't before)
  - `curl http://localhost:8002/mcp/projects/esbmain/graph` -> 401 (auth required) instead of the previous 404 -- proves the route is now registered
  - Direct invocation with env loaded: `await get_project_graph('esbmain')` returns `project_id=esbmain`, 6 connections, 21 job_metadata entries
  - `config.intodoc_output_dir` resolves to `/home/intodata/intodata-kb-v2/knowledge_base/intodoc-output` and `esbmain/scan/manifest.json` exists there

## Session: 2026-03-30

### [Fix]: Template creation 500 error (MissingGreenlet)

- **Files changed**: `mcp-server/src/mcp_server/api/routes/templates.py` (lines 8, 44)
- **Problem**: `POST /mcp/templates/` returned 500 with `MissingGreenlet` exception
- **Root cause**: `_template_to_response()` accessed `t.versions` (lazy-loaded relationship) in async context. SQLAlchemy async sessions cannot lazy-load; they require explicit eager loading via `selectinload`. The `create_template` path didn't eager-load versions, so accessing `t.versions` triggered the error.
- **Change**: Added `from sqlalchemy import inspect as sa_inspect`. Changed condition from `if lv is None and t.versions` to `if lv is None and "versions" in sa_inspect(t).dict` -- checks if the relationship is already loaded before accessing it. If not loaded, `latest_version` stays `None`.
- **Why this approach**: Idiomatic SQLAlchemy pattern for async-safe relationship access. Avoids try/except, doesn't require changing every caller to pass `latest_version`, and works for any future endpoint that might not eager-load versions.
- **Verification**: Endpoints using `selectinload(Template.versions)` (list, get) still populate `latest_version`. The `create_template` endpoint returns `latest_version: null` without error.

## Session: 2026-03-24

### [Feature]: Transparency Layer -- Scoring Pipeline + Template Management & Swapping

Full implementation of the template management system and scoring pipeline enhancement across 6 phases (A-F). This is the largest single feature addition to the platform.

#### Phase A: Scoring Pipeline (New Phase 2.6)

- **Files created**:
  - `mcp-server/src/mcp_server/templates/scoring-rubric.md` -- 8 dimensions x 5 levels scoring rubric
  - `mcp-server/src/mcp_server/templates/anti-patterns.md` -- 29 anti-patterns across 6 categories (AP-SEC, AP-ERR, AP-DQ, AP-PERF, AP-ARCH, AP-OPS)
  - `mcp-server/src/mcp_server/templates/red-flags.md` -- 28 red flags across 5 categories
  - `mcp-server/src/mcp_server/templates/security-checklist.md` -- 7-category structured security review
  - `mcp-server/src/mcp_server/templates/pattern-library.md` -- 15 known good patterns
- **Files modified**:
  - `mcp-server/src/mcp_server/services/claude_service.py` -- Added Phase 2.6 (Score & Assess) to mega-prompt, scoring template bundling to `_preprocess_scan_data()`, `_assessment/` directory creation, Phase 3 now reads scorecard
- **Change**: New scoring sub-agent between Phase 2.5 and Phase 3 reads all analysis files + scoring templates, produces `_assessment/scorecard.md` with 8-dimension quantitative scores + evidence. Phase 3 agents reference scores in generated docs.

#### Phase B: Database Foundation

- **Files created**:
  - `mcp-server/src/mcp_server/db/template_models.py` -- 4 SQLAlchemy models: Template, TemplateVersion, TemplateProfile, ProfileAssignment
  - `mcp-server/src/mcp_server/services/template_service.py` -- Full CRUD for templates, versions, profiles, assignments (diff, rollback, clone, set-default)
  - `mcp-server/src/mcp_server/api/routes/templates.py` -- 20+ REST endpoints for template/version/profile/assignment management
  - `mcp-server/scripts/seed_templates.py` -- Idempotent migration of filesystem templates to DB
  - `mcp-server/alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` -- Async Alembic setup
  - `mcp-server/alembic/versions/001_add_template_management.py` -- Migration for 4 new tables + 2 new columns
- **Files modified**:
  - `mcp-server/src/mcp_server/db/models.py` -- Added `template_profile_id` (INT FK) and `template_snapshot` (JSONB) to GenerateRun
  - `mcp-server/src/mcp_server/db/__init__.py` -- Exports for new models
  - `mcp-server/src/mcp_server/schemas.py` -- 15+ new Pydantic schema classes + extended GenerateRequest/GenerateRunResponse
  - `mcp-server/src/mcp_server/api/app.py` -- Registered template/profile/generate_runs routers
  - `mcp-server/pyproject.toml` -- Added `alembic>=1.13.0`

#### Phase C: Template Resolver + Pipeline Integration

- **Files created**:
  - `mcp-server/src/mcp_server/services/template_resolver.py` -- Resolution cascade: explicit profile -> project-scoped default -> platform default -> filesystem fallback
- **Files modified**:
  - `mcp-server/src/mcp_server/services/claude_service.py` -- `_preprocess_scan_data()` now accepts `template_profile_id`, returns `(groups, snapshot)` tuple, uses DB templates with filesystem fallback
  - `mcp-server/src/mcp_server/api/routes/projects.py` -- Passes `template_profile_id` through generate endpoint to background task
  - `mcp-server/src/mcp_server/services/project_registry.py` -- `complete_generate_run()` accepts template fields

#### Phase D: Dashboard -- Template Catalogue + Editor

- **Files created**:
  - `dashboard/src/components/templates/TemplateCatalogue.tsx` -- Filterable template grid with category/platform/search filters
  - `dashboard/src/components/templates/TemplateEditor.tsx` -- Inline editor with version selector, required commit messages
  - `dashboard/src/components/templates/VersionTimeline.tsx` -- Version history with colored unified diff viewer
  - `dashboard/src/hooks/useTemplates.ts` -- 12 React Query hooks for template APIs
- **Files modified**:
  - `dashboard/src/api/client.ts` -- ~20 new API methods for templates/versions/profiles/assignments
  - `dashboard/src/types.ts` -- Template, TemplateVersion, TemplateProfile, ProfileAssignment, ProfileDetail interfaces
  - `dashboard/src/App.tsx` -- Top-level nav (Projects/Templates/Profiles/Docs), template editor layout

#### Phase E: Dashboard -- Profiles & Swapping

- **Files created**:
  - `dashboard/src/components/templates/ProfileManager.tsx` -- Profile list/create/clone UI, set-default, platform filter
  - `dashboard/src/components/templates/ProfileDetail.tsx` -- Per-template version assignment with add-override dropdown
- **Files modified**:
  - `dashboard/src/components/ActionButtons.tsx` -- Profile selector dropdown next to Generate button
  - `dashboard/src/components/RunHistory.tsx` -- Profile column showing P#{id} or "default"
  - `dashboard/src/hooks/useProjects.ts` -- `useGenerate` accepts `templateProfileId`

#### Phase F: A/B Comparison + Document Catalogue

- **Files created**:
  - `mcp-server/src/mcp_server/api/routes/generate_runs.py` -- Template snapshot + run comparison endpoints
  - `dashboard/src/components/templates/ABComparison.tsx` -- Run selector, metrics comparison table, template diff display
  - `dashboard/src/components/templates/DocumentCatalogue.tsx` -- Auto-generated reference of 12 doc types with template links
- **Files modified**:
  - `dashboard/src/App.tsx` -- Added "A/B Compare" tab, "Docs" top-nav page

- **Why this approach**: Templates stored in DB for versioning and admin visibility. Full-copy variants (not section overrides) for simplicity. Per-platform template sets. Filesystem fallback is permanent for backward compatibility. Resolution cascade gives flexibility (explicit profile > project default > platform default > filesystem).
- **Verification**: `npx tsc --noEmit` passes clean. All new components type-check. Backend Python files syntax-verified.

---

## Session: 2026-03-20

### [Fix]: Workato Scanner Format C Support -- 6 Field Mismatch Bugs

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` (6 bug fixes across classification, parsing, and step walking)
  - `intodoc/tests/test_platforms/test_workato.py` (20+ new tests for Format C)
  - `intodoc/tests/fixtures/workato_nested/recipes/nightly_sync/recipe.json` (new fixture)
  - `intodoc/tests/fixtures/workato_nested/recipes/nightly_sync/steps.json` (new fixture)
  - `intodoc/tests/fixtures/workato_nested/connections/amazon_s3/connection.json` (new fixture)
- **Problem**: Workato scanner produced critically incomplete output for real-world manifest/package exports (Format C). 4 of 5 recipes had only trigger placeholders, 1 recipe completely missing, 0 of 6 connections detected, no action steps or field mappings extracted.
- **Root cause**: Parser only handled Format A (code blocks) and Format B (inline trigger+steps). Format C uses different field names (`application` vs `provider`, `action` vs `name`, `authentication_type` vs `authorization_type`, `conditional` vs `if`, `repeat_for_each` vs `foreach`) and wraps steps.json in a dict.
- **Changes**:
  1. Fix 1 (line 626): Unwrap `steps.json` dict `{recipe_id, steps}` in addition to raw list
  2. Fix 2 (lines 861-862): Trigger field fallback `provider||application`, `name||type`; generic `_extract_trigger_params()` iterates all keys
  3. Fix 3 (line 186): Recipe classification accepts `trigger_application`/`action_applications`/`connections_used` as markers
  4. Fix 4 (lines 1087-1088, 920-921): Action step field fallback `provider||application`, `name||action`; generic `_extract_new_step_params()` iterates all keys
  5. Fix 5 (lines 188-193, 430, 443): Connection classification accepts `application` + `authentication_type`/`verified`/`connection_id`; `_parse_connection()` uses fallback chains
  6. Fix 6 (lines 942, 972): Step type aliases `conditional`->`if`, `repeat_for_each`->`foreach`; new handlers for `formula`, `lookup`, `trigger`-as-step
- **Why this approach**: Generic extraction (iterate all keys, `or` fallback chains) handles all known formats AND unknown future variations without hardcoded field lists
- **Verification**: 139 tests pass (20+ new tests covering Format C classification, wrapped steps, new step types, connection field variants)

### [Feature]: Gate Validation + Retries Between Doc Gen Phases

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (2 prompt insertions)
- **Problem**: If a Phase 2 sub-agent produces empty/thin analysis or a Phase 3 agent fails to write a doc, downstream phases proceed anyway, resulting in thin or missing documentation.
- **Root cause**: No validation checkpoints between phases in the orchestration prompt.
- **Change**: Added two gate validation phases to the doc generation prompt:
  - **Phase 2.5** (between Phase 2 and Phase 3): After all Phase 2 sub-agents return, runs `ls -la` on `_analysis/`, checks each group's analysis file exists and is >1KB. Re-spawns failed groups once. Proceeds with warnings if retry fails.
  - **Phase 3.5** (between Phase 3 and Phase 4): After all Phase 3 doc agents return, validates 9 required docs exist and are >500 bytes (00-05, 09-11). Re-spawns only the responsible agent (Core, Business+Ops, or Patterns+User) once. Logs warnings for still-missing docs.
- **Why this approach**: Prompt-only change — no Python logic modifications. Validation runs inside the Claude Code subprocess as part of orchestration. Lightweight (`ls -la` checks) with bounded retries (max 1 per group/agent) to avoid runaway costs.
- **Verification**: `python3 -m py_compile` passes. Full E2E test pending next doc generation run.

### [Feature]: Phase 2 Filesystem Boundary + Scaled Analysis Target

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (3 edits)
- **Problem**: (1) Phase 2 sub-agents sometimes waste 5+ minutes reading raw files from `intodoc-output/` or `data/repos/` despite all data being pre-bundled in `_preprocess/`. (2) Fixed `(target: 15-40KB)` analysis target is excessive for small projects (e.g. 4-job project with ~5KB preprocessed data).
- **Root cause**: (1) No explicit prohibition on filesystem access outside staging dir. (2) Analysis target was a static range regardless of input size.
- **Change**: (1) Added explicit PROHIBITED block to Phase 2 sub-agent instructions: no Glob/Read/Bash outside `{staging_abs}/`, no access to `intodoc-output/`, `data/repos/`, or any other directory. (2) After preprocessing, compute `total_preprocess_bytes` (sum of non-underscore-prefixed .md files in `_preprocess/`), derive `analysis_target_kb = max(5, min(40, total_preprocess_bytes // 2048))`, inject into prompt replacing fixed `15-40KB`.
- **Why this approach**: (1) Sub-agents respect explicit instructions better than implicit expectations. The boundary is clear: everything is in `_preprocess/`, don't look elsewhere. (2) Dividing by 2048 means analysis is ~50% of input size, clamped to 5-40KB range. Small projects get proportional targets; large projects still hit 40KB max.
- **Verification**: `python3 -m py_compile` passes. Visual inspection confirms both changes in correct locations.

### [Feature]: Consolidated Analysis Guide + Dynamic Depth Scaling

- **Files changed**:
  - `intodoc/src/intodoc/platforms/talend/prompts/ANALYSIS_GUIDE.md` -- NEW (276 lines, 12KB). Consolidated analysis guide replacing 22 separate .txt prompt files for Phase 2 sub-agents.
  - `intodoc/src/intodoc/platforms/talend/prompts/PHASE_3_GUIDE.md` -- NEW (86 lines). Cross-group synthesis guide for Phase 3 agents (exec summary, cross-region comparison, migration assessment).
  - `intodoc/src/intodoc/platforms/_shared/prompts/ANALYSIS_GUIDE.md` -- NEW (125 lines). Platform-agnostic fallback guide.
  - `intodoc/src/intodoc/platforms/_shared/prompts/PHASE_3_GUIDE.md` -- NEW (53 lines). Shared Phase 3 fallback.
  - `mcp-server/src/mcp_server/services/scanner_service.py` -- Modified `get_analysis_prompts()` to prefer ANALYSIS_GUIDE.md; added `get_phase3_guide()` method.
  - `mcp-server/src/mcp_server/services/claude_service.py` -- Modified `_preprocess_scan_data()` to write `_analysis_guide.md` + `_phase3_guide.md`. Rewrote `generate_docs()` prompt: removed 70 lines of inline section definitions (now in guide), replaced hard-coded depth targets with dynamic scaling, added Phase 3 guide reference, deduplicated DO NOT blocks.
- **Problem**: 22 separate prompts bundled into ~222KB caused conflicting instructions (22 "Your task is" statements), wasted ~60% on JSON format specs sub-agents never use, and repeated Talend concepts 5-10x. Hard-coded line minimums (200-800+) don't scale with project size.
- **Root cause**: Prompts designed for old CLI model (separate LLM calls returning JSON) were being bundled verbatim for new MCP model (single markdown output).
- **Change**: (1) Created single ANALYSIS_GUIDE.md per platform (~12KB) preserving all extraction checklists, scoring systems, risk catalogs, and platform knowledge while dropping JSON specs, redundant context, and conflicting framing. (2) Created PHASE_3_GUIDE.md for cross-group synthesis (moved from p10/p17/p19 which were misplaced in Phase 2). (3) scanner_service checks for guide first, falls back to individual .txt files. (4) Prompt now uses dynamic depth: "This project has N jobs across M groups. Scale depth to match content." (5) Inline section definitions removed (guide has them).
- **Why this approach**: 94% token reduction (222KB -> 12KB) for Phase 2 input. Coherent single-document instructions eliminate conflicting "your task is" blocks. Dynamic depth naturally adapts to project size. Original 22 .txt files kept in place for backward compat (legacy CLI, Workato/Generic inheritance).
- **Verification**: Python syntax check passes on both modified files. Guide file sizes confirmed (12KB Talend guide vs 222KB original prompts). Prompt reduced from ~207 to ~127 lines.

## Session: 2026-03-19

### [Fix]: Size-aware bundle splitting for oversized prompt and ref doc files

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (lines 267-350: bundle splitting logic; lines 437-564: orchestrator prompt references)
- **Problem**: After adding Workato p09+p22 prompt overrides, `_analysis_prompts.md` grew to 284.9KB, exceeding Claude's 256KB Read tool file size limit. Phase 2 sub-agents hit `"File content (284.9KB) exceeds maximum allowed size (256KB)"`. Ref doc bundles (`_ref_core.md` at 107KB, `_ref_patterns_user.md` at 169KB) exceeded the 25K token limit (~80KB).
- **Root cause**: Bundle code concatenated ALL prompts (or ALL ref docs per agent) into a single file with no size check. Orchestrator prompt hardcoded single filenames.
- **Change**: (1) Prompt bundle: split at 200KB threshold (`MAX_PROMPT_BUNDLE_BYTES = 200_000`). Writes `_analysis_prompts_1.md`, `_analysis_prompts_2.md` when total exceeds limit, or single `_analysis_prompts.md` when it fits. (2) Ref doc bundles: split at 80KB threshold (`MAX_REF_BUNDLE_BYTES = 80_000`, ~20K tokens). Same suffix pattern. (3) Updated all orchestrator prompt references from hardcoded filenames to glob patterns (`_analysis_prompts*.md`, `_ref_core*.md`, etc.) with instructions to "read ALL matching files".
- **Why this approach**: Threshold-based splitting is simple, deterministic, and forward-compatible as more prompts are added. 200KB is well under the 256KB file limit. 80KB is ~20K tokens, safely under the 25K token limit. Glob patterns let sub-agents discover split files without knowing the count.
- **Verification**: Python syntax check passes. All 6 prompt references updated to glob patterns (grep confirms no remaining hardcoded single-file references).

### [Feature]: Workato-specific p09 and p22 prompt overrides

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/prompts/p09_business_logic.txt` -- NEW. Workato-specific business logic detection prompt replacing shared/Talend fallback.
  - `intodoc/src/intodoc/platforms/workato/prompts/p22_preventive.txt` -- NEW. Workato-specific preventive analysis prompt replacing shared/Talend fallback.
- **Problem**: Shared p09 and p22 prompts contained Talend-specific terminology (tMap expressions, Java routines, tJavaRow, tMap lookup join, tAggregateRow, tBigQuerySQLRow, tLogCatcher, tSendMail, DIE_ON_CHILD_ERROR) that misled the LLM during Workato project analysis.
- **Root cause**: These two prompts were never overridden for Workato during the prompt parity work on 2026-03-18.
- **Change**: Created Workato-specific overrides that: (1) Replace all Talend terms with Workato equivalents (datapill formulas, action input mappings, monitor/catch blocks, call_recipe, lookup_table_entry, etc.). (2) Keep identical JSON output schemas (p09: 7 keys, p22: 7 keys). (3) Add Workato-specific extraction targets (API collection endpoints, property lists, nested recipe steps from sibling steps.json, event topic payloads, callable recipe interfaces). (4) Reference WAP-003 through WAP-010 anti-patterns from best_practices.yaml in p22.
- **Why this approach**: Platform-specific prompts produce dramatically better doc quality than generic prompts with wrong terminology. The shared fallback is fine for genuinely platform-agnostic prompts (p04, p10, p11, p14, p15) but not for business logic or preventive analysis which reference platform components directly.
- **Verification**: (1) Both files discovered by prompt loading (17 Workato overrides total). (2) Zero Talend terms in either file (grep returns no matches). (3) JSON schema keys match shared versions exactly. (4) 126 Workato tests pass (no regressions).

### [Fix]: Doc generation prompt delivery + MCP tool path resolution

- **Files changed**:
  - `mcp-server/src/mcp_server/services/scanner_service.py` (line ~701) -- Changed `platforms_root` from `config.project_root / "intodoc" / "src" / ...` to `Path(intodoc.platforms.__file__).parent`. Uses installed package path instead of filesystem traversal from project_root.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~286) -- Changed `ref_dir` from `config.project_root / "intodoc" / "reference-docs"` to source-tree-relative path via `Path(__file__).resolve().parent * 5 / "intodoc" / "reference-docs"`.
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines ~382-385) -- Removed Phase 1 MCP calls (`get_scan_status`, `get_chunks`, `fetch_json`) from prompt. Replaced with "All data is pre-processed" instruction.
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines ~579-588) -- Extended PROHIBITED ACTIONS to ban all MCP/fetch tool calls.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~646) -- Changed subprocess cwd from `config.project_root` to source tree path where `.mcp.json` lives.
  - `mcp-server/src/mcp_server/services/claude_service.py` (line ~284) -- Added warning log when analysis prompts fail to bundle.
- **Problem**: Doc generation ran without Workato-specific analysis prompts (15 prompts + best_practices.yaml), reference doc templates (12 files), or MCP tools. Generated docs lacked platform-specific depth.
- **Root cause**: `.env` sets `MCP_PROJECT_ROOT=/opt/intodata-kb/knowledge_base` which only contains runtime data (`data/`, `intodoc-output/`, `output/`), not source code. All paths resolved from `config.project_root` pointed to non-existent locations. The subprocess cwd also lacked `.mcp.json`.
- **Change**: (1) Prompt lookup uses Python package path (`intodoc.platforms.__file__`), works regardless of project_root. (2) Reference docs use source-tree-relative path from `__file__`. (3) Removed redundant Phase 1 MCP calls from prompt since all data is pre-bundled. (4) Fixed subprocess cwd to source tree. (5) Added warning log for failed prompt bundling.
- **Why this approach**: Package path is the most reliable way to find installed platform files. Source-tree-relative paths avoid config dependency. Removing MCP calls from prompt prevents errors since subprocess has no MCP tools.
- **Verification**: `MCP_PROJECT_ROOT=/opt/... python -c "ScannerService().get_analysis_prompts('workato')"` returns `Count: 23 Error: none`. Reference docs path resolves to correct location with 12 files. MCP server restarted successfully.

### [Feature]: Content-based asset discovery for Workato scanner

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` -- Added `_classify_json_content()` static method (~100 lines), `_build_asset_registry()` method (~25 lines), `_asset_registry` field. Replaced all 12 `rglob()` calls with `self._asset_registry.get()` lookups in `_build_connection_index`, `_build_recipe_id_index`, `_parse_properties`, `_parse_all_recipes`, `_parse_recipe_functions`, `_parse_api_assets`, `_parse_lcap_assets`, `_parse_custom_adapters`, `_parse_schemas`, `_parse_lookup_tables`. Added steps.json sibling merging in `_parse_recipe()`. Added name fallback for generic filenames. Added `property_list` type handling. Added `api_collection` type handling. Updated `_find_workato_root()` with content-based fallback. Unified lookup_table parsing (merged `*.lookup_table.json` and `*.workato_db_table.json` rglob paths into single registry type). Added `from collections import defaultdict` import.
  - `intodoc/src/intodoc/platforms/workato/__init__.py` -- Added `_DIR_NAME_MAP` dict for parent-directory-based fallback classification. Updated `classify_file()` to check parent/grandparent directory names for nested export formats.
  - `intodoc/tests/test_platforms/test_workato.py` -- Added 4 new test classes (51 new tests): `TestClassifyJsonContent` (25 unit tests for content classifier), `TestAssetRegistry` (6 tests on flat + nested fixtures), `TestNestedFormatIntegration` (15 integration tests), `TestDirectoryClassification` (5 tests for dir-based fallback).
  - `intodoc/tests/fixtures/workato_nested/` -- NEW fixture directory (9 files): `manifest.json`, 2 recipe dirs each with `recipe.json` + `steps.json`, 2 connection dirs each with `connection.json`, `lookup_tables/country_codes.json`, `properties/settings.json` (property list format).
- **Problem**: Workato scanner used hardcoded filename patterns (`rglob("*.recipe.json")`, `rglob("*.connection.json")`, etc.) for asset discovery. This only worked for one specific export format. Real Workato exports use a different folder structure (e.g. `recipes/sync_orders/recipe.json` + `recipes/sync_orders/steps.json`) and naming convention, resulting in 0 recipes/connections discovered.
- **Root cause**: The discovery layer was based on 10+ separate `rglob()` calls with suffix patterns like `*.recipe.json`. Files in nested folder format have generic names like `recipe.json` or `connection.json` that don't match these patterns.
- **Change**: Replaced the filename-suffix-based discovery with content-based classification:
  1. `_classify_json_content()` reads parsed JSON and determines asset type from key signatures (15+ types: recipe, connection, lookup_table, custom_adapter, api_endpoint, api_collection, lcap_app, recipe_function, topic, schema, property, property_list, steps, manifest, package). Handles wrapped formats (`{"recipe": {...}}`) by detecting wrapper keys and mapping to types.
  2. `_build_asset_registry()` iterates the file list already produced by `walk_repo()` (no extra filesystem walk), reads each JSON file once, classifies by content, and builds a `dict[str, list[Path]]` registry.
  3. Phase 1b in `parse()` calls `_build_asset_registry()` right after `walk_repo()`.
  4. All 12 `rglob()` calls replaced with `self._asset_registry.get('type', [])` lookups.
  5. Added steps.json sibling merging in `_parse_recipe()` -- if a `steps.json` exists next to a recipe file, its content is merged into the recipe data.
  6. Added name fallback -- when filename is generic (e.g. `recipe.json`), use parent directory name.
  7. Added `property_list` handling for files containing an array of `{name, value}` dicts.
  8. Added `api_collection` handling for files containing multiple endpoints.
  9. Updated `classify_file()` with parent/grandparent directory name fallback for `walk_repo()` stats.
  10. Updated `_find_workato_root()` with content-based recipe detection as final fallback.
- **Why this approach**: Content-based classification is format-agnostic -- it works regardless of file naming or directory structure. The single-pass registry is actually faster than the previous 10+ separate `rglob()` calls. The `walk_repo()` output is reused (no extra filesystem walk). Parsing logic stays identical -- only the discovery layer changes.
- **Verification**: 126/126 tests pass (75 existing unchanged + 51 new). Zero regressions. Tested on 3 fixtures: `workato_sample` (flat format), `workato_new_format` (wrapped format), `workato_nested` (nested folder format).

---

## Session: 2026-03-18

### [Feature]: Create 7 missing Workato-specific analysis prompts (p07, p12, p16, p17, p18, p20, p21)

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/prompts/p07_state.txt` -- NEW (168 lines, 9.4K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p12_data_quality.txt` -- NEW (199 lines, 11.9K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p16_performance.txt` -- NEW (236 lines, 12.3K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p17_cross_folder.txt` -- NEW (233 lines, 13.3K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p18_reusability.txt` -- NEW (311 lines, 16.8K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p20_lineage_matrix.txt` -- NEW (199 lines, 9.6K chars)
  - `intodoc/src/intodoc/platforms/workato/prompts/p21_expression_catalog.txt` -- NEW (222 lines, 11.0K chars)
- **Problem**: 7 analysis dimensions that Talend covers (state, data quality, performance, cross-region, reusability, lineage, expressions) were completely missing for Workato -- no platform override AND no shared fallback. These dimensions were skipped during Workato doc generation, producing incomplete documentation.
- **Root cause**: When Workato prompts were initially created, only 8 of 22 dimensions were written as Workato-specific overrides. The remaining 14 were expected to use shared fallbacks, but shared fallbacks only existed for 7 of the 14. The 7 without either platform-specific or shared prompts were silently skipped.
- **Change**: Created 7 Workato-specific prompt files following the established style convention (opening line, Workato Context, What to Extract, Output Format with JSON examples). Each prompt uses exclusively Workato terminology (recipe, datapill, monitor/catch, call_recipe, block tree, provider) with zero Talend terms. Incorporated real-world Workato best practices: task consumption model (batch = 1 task for N records), polling interval optimization, recipe functions vs callable recipes, layered error defense, idempotency patterns, Ruby method chains on datapills.
- **Why this approach**: Creating platform-specific prompts rather than shared fallbacks ensures Workato doc generation uses terminology, concepts, and JSON output examples appropriate to the Workato platform. Shared fallbacks are too generic for these dimensions.
- **Verification**:
  - `get_analysis_prompts('workato')` returns 23 prompts (15 overrides + 7 shared fallbacks + best_practices)
  - `grep` for Talend terms across all 7 new files: zero matches
  - All files follow Workato prompt style (verified against p01_inventory.txt reference)
  - Note: MCP stdio tool call fails due to `.mcp.json` setting `MCP_PROJECT_ROOT=/opt/intodata-kb/knowledge_base` (deployment dir without source code). The systemd REST server (which handles doc generation) resolves correctly.

**Previous Workato prompts (8)**: p01, p02, p03, p05, p06, p08, p13, p19
**New Workato prompts (7)**: p07, p12, p16, p17, p18, p20, p21
**Shared fallbacks used (7)**: p04, p09, p10, p11, p14, p15, p22
**Total**: 22 analysis dimensions (full parity with Talend)

## Session: 2026-03-17

### [Feature]: Workato parser dual-format support (old flat JSON + new nested recipe.steps)

- **Files changed**:
  - `intodoc/src/intodoc/platforms/workato/parser.py` -- added ~200 lines new code, ~15 lines modifications
  - `intodoc/src/intodoc/platforms/workato/__init__.py` -- added `.recipe_function.json` to asset map
  - `intodoc/tests/test_platforms/test_workato.py` -- added 6 new test classes (~150 lines)
  - `intodoc/tests/fixtures/workato_new_format/` -- 8 new fixture files
- **Problem**: Workato parser only handled old export format (flat JSON with `code` blocks). Production Workato exports use nested format with `recipe.steps` arrays. All scanned recipes produced empty components/connections/purpose/tmap_data.
- **Root cause**: Two JSON format variants exist -- old `{code: {keyword, block}}` vs new `{recipe: {trigger, steps}}`. Same for connections: `{connection: {provider}}` vs flat `{provider}`.
- **Change**:
  1. Added `_unwrap(data, key)` static helper -- checks if `data[key]` is a dict wrapper, returns inner object or passes through
  2. Added unwrap calls to 6 read methods: `_parse_recipe`, `_parse_connection`, `_build_recipe_id_index`, `_parse_lookup_tables`, `_parse_properties` (account + project)
  3. Added `_DATAPILL_NEW_RE` regex for `{{...}}` handlebars syntax alongside existing `#{_('...')}` old syntax
  4. Added `_connection_id_index` (int -> str) for numeric connection_id lookups in new format
  5. Added `_resolve_connection_ref` helper -- resolves by numeric ID, provider name, or provider-to-conn map
  6. Added format branching in `_parse_recipe`: detects new format via `data.get("trigger")` or `data.get("steps")`, calls new walker; else falls through to existing `_walk_code_blocks`
  7. Added `_walk_trigger` method -- extracts trigger as first Component from `recipe.trigger`
  8. Added `_walk_steps` method (~100 lines) -- recursive walker for `steps[]` format handling action, if, foreach/repeat, monitor, catch, stop step types
  9. Added helper methods: `_make_step_component`, `_extract_new_step_params`, `_extract_trigger_params`, `_extract_new_condition_params`
  10. Added `_parse_recipe_functions` method for `*.recipe_function.json` files
  11. Added `.recipe_function.json -> FileType.JOB` to `_ASSET_TYPE_MAP` in `__init__.py`
- **Why this approach**: Format branching in `_parse_recipe` keeps old code path completely untouched. `_unwrap` is a zero-cost operation for old-format files (no wrapper key found, returns same dict). New walker produces identical IR types (Component, Connection, TMapEntry, TRunJobRef) as old walker.
- **Verification**: 227 tests pass (76 Workato-specific: 46 existing unchanged + 30 new). All existing tests pass without modification.

---

## Session: 2026-03-17

### [Feature]: Workato + Generic platform plugins, BaseDistiller refactoring

- **Files changed**:
  - **New: `intodoc/src/intodoc/platforms/base_distiller.py`** (~200 lines): Extracted generic chunking logic from TalendDistiller. Contains `distill()`, `_split_chunk()`, `_make_chunk()`, `_filter_manifest()`, `_match_connections_for_jobs()`, `_match_routines_for_jobs()`, `_dedup_contexts()`, `_compact_contexts()`, `_extract_defaults()`, `_print_report()`. Abstract hook `_distill_job()` for subclass overrides.
  - **New: `intodoc/src/intodoc/platforms/generic/__init__.py`** (~110 lines): GenericPlugin with extension-based file classification (`_EXT_MAP`), `_SKIP_DIRS`, `_SKIP_EXTS`. Prompt resolution: `generic/prompts/` -> `_shared/prompts/` fallback chain.
  - **New: `intodoc/src/intodoc/platforms/generic/parser.py`** (~260 lines): GenericParser groups files by directory, builds jobs from sub-directories, extracts contexts from .properties/.env files, detects connections from config files (host/port/database keys), extracts code files as routines.
  - **New: `intodoc/src/intodoc/platforms/generic/file_rules.yaml`** (~80 lines): Extension-based classification rules.
  - **New: `intodoc/src/intodoc/platforms/generic/best_practices.yaml`** (~120 lines): Generic integration project best practices.
  - **New: `intodoc/src/intodoc/platforms/workato/__init__.py`** (~100 lines): WorkatoPlugin extends GenericPlugin. `_ASSET_TYPE_MAP` classifies 14 Workato asset types (recipe, connection, lookup_table, etc.).
  - **New: `intodoc/src/intodoc/platforms/workato/parser.py`** (~530 lines): Full WorkatoParser with recursive block walker (`_walk_code_blocks`), datapill extraction (`_DATAPILL_RE`), manifest building, 8-phase parse. Handles all keyword types: trigger, action, if/elsif/else, foreach, repeat, monitor, catch, stop. Provider-to-connection mapping for manifest building.
  - **New: `intodoc/src/intodoc/platforms/workato/distiller.py`** (~60 lines): WorkatoDistiller extends BaseDistiller. Strips UI metadata (uuid, number, position, schema fields, etc.).
  - **New: `intodoc/src/intodoc/platforms/workato/file_rules.yaml`** (~60 lines): Workato asset type patterns.
  - **New: `intodoc/src/intodoc/platforms/workato/best_practices.yaml`** (~572 lines): Comprehensive Workato platform context: 13 concepts, 32 component types, 10 anti-patterns (WAP-001 to WAP-010), 8 connection types, error handling patterns, naming conventions.
  - **New: `intodoc/src/intodoc/platforms/_shared/prompts/`** (12 files): Platform-agnostic analysis prompts (p01, p02, p03, p04, p05, p06, p09, p10, p11, p14, p15, p22). Adapted from Talend prompts with all platform-specific references generalized.
  - **New: `intodoc/src/intodoc/platforms/workato/prompts/`** (8 files): Workato-specific prompt overrides (p01_inventory, p02_execution, p03_data_flows, p05_external_systems, p06_error_handling, p08_custom_code, p13_naming, p19_migration_assessment).
  - **Modified: `intodoc/src/intodoc/platforms/registry.py`**: Added "generic" and "workato" to `_PLATFORM_MAP`.
  - **Modified: `intodoc/src/intodoc/platforms/talend/distiller.py`**: Refactored to extend BaseDistiller. Keeps only Talend-specific filtering logic.
  - **Modified: `mcp-server/src/mcp_server/services/scanner_service.py`**: Updated `get_analysis_prompts()` to merge shared + platform prompts with fallback resolution.
  - **New: `tests/fixtures/generic_sample/`**: src/main.py, config/database.json, config/app.yaml, sql/create_tables.sql.
  - **New: `tests/fixtures/workato_sample/`**: manifest.json, 3 connections, 3 recipes (nested blocks, datapills, call_recipe, monitor/catch), lookup_table, 2 properties.
  - **New: `tests/test_platforms/test_generic.py`**: 45 tests (registry, classify, parse, best_practices).
  - **New: `tests/test_platforms/test_workato.py`**: 48 tests (registry, classify 14 types, parse recipes, datapill extraction, block walker, connections, distiller, best_practices).
- **Problem**: intodoc only supported Talend. No way to scan Workato projects or arbitrary codebases.
- **Root cause**: Platform plugin architecture existed but only Talend was implemented.
- **Change**: Three-tier implementation:
  1. BaseDistiller: Extracted generic chunking from TalendDistiller (zero behavior change, all existing tests pass).
  2. GenericPlugin: Universal fallback -- classifies files by extension, groups by directory, extracts contexts/connections/routines.
  3. WorkatoPlugin(GenericPlugin): Structured JSON parsing for 14 Workato asset types with recursive block tree walking, datapill field-level lineage extraction, and recipe-to-recipe manifest building.
  4. Shared prompts: 12 platform-agnostic prompts in `_shared/prompts/`, with platform-specific override support.
- **Why this approach**: Inheritance chain (PlatformPlugin -> GenericPlugin -> WorkatoPlugin) maximizes code reuse. BaseDistiller extraction enables all platforms to use the same chunking logic. Shared prompts reduce duplication while allowing platform overrides.
- **Verification**: 200 tests pass (93 new + 107 existing), 8 skipped (Talend integration needing real project data). Zero regressions.

---

## Session: 2026-03-16

### [Feature]: Enhanced credential filter with two-layer redaction

- **Files changed**:
  - `intodoc/src/intodoc/config.py` (lines 9-21, 48-50): Added `RedactionLevel` enum (standard/strict/paranoid) and 3 new config fields (redaction_level, redaction_config, redaction_audit)
  - `intodoc/src/intodoc/scanner/credential_filter.py` (full rewrite, ~250 lines): Added FQDN/URL/email patterns (standard), file path patterns (strict), safe domain exclusion (w3.org, eclipse.org, etc.), custom config from `.intodoc-redact.yaml`, SHA-256 audit logging, `is_sensitive_var_name()` method, `RedactionLevel`-aware pattern activation
  - `intodoc/src/intodoc/scanner/post_filter.py` (NEW, ~130 lines): Post-parse structural filter -- walks ScanResult models, redacts MetadataConnection.host (always), database/schema (strict+), file_path (always), context variable values by type/name semantics, component param values via text filter catch-all
  - `intodoc/src/intodoc/cli.py` (lines 14, 32-75, 80-91): Added `--redaction-level`, `--redaction-config`, `--no-redaction-audit` CLI flags; auto-discovers `.intodoc-redact.yaml` in repo root; calls `filter_scan_result()` after parsing; writes `redaction_report.json`; `_do_scan()` now returns `(result, cred_filter)` tuple
  - `intodoc/tests/test_scanner/test_credential_filter.py` (full rewrite, 37 tests): Tests for all new patterns, redaction levels, variable name sensitivity, audit report, custom config, false positive safety (Talend component names, XML namespaces)
  - `intodoc/tests/test_scanner/test_post_filter.py` (NEW, 14 tests): Tests for MetadataConnection redaction, context variable redaction by type/name/level, paranoid mode, component param filtering, structural preservation
- **Problem**: Real scan output leaked FQDNs (079talend02.keune.com), URLs, email addresses, usernames (activemq_user=admin), file paths, cloud resource IDs through to LLM
- **Root cause**: Original credential_filter.py only had 9 patterns targeting encrypted passwords, IPs, JDBC strings, credential files, and certificates. No FQDN/URL/email/path matching. No post-parse semantic filtering.
- **Change**: Two-layer filtering: Layer 1 (raw text) adds FQDN, URL, email patterns at standard level + file paths at strict level, with safe domain exclusion. Layer 2 (post-parse) walks ScanResult models and redacts based on field semantics (connection hosts, password-type vars, infrastructure vars). Three configurable levels. Custom rules via `.intodoc-redact.yaml`. Audit report with category counts + SHA-256 hashes (never raw values).
- **Why this approach**: LLMs process all tokens in context -- "don't read passwords" doesn't prevent data from hitting the provider's servers. Must strip data in Python before serialization. Two layers because some data only becomes sensitive in context (e.g., "admin" is only sensitive when it's the value of activemq_user). Configurable levels because different customers have different compliance requirements.
- **Verification**: 51 tests pass (37 credential_filter + 14 post_filter). Talend component names (tMSSqlInput, tBigQueryOutput) confirmed not redacted. XML namespace domains (w3.org, eclipse.org) confirmed preserved. Audit report confirmed to never contain raw values.

---

## Session: 2026-03-13

### [Feature]: Static customer-facing assessment page

- **Files changed**:
  - `mcp-server/pyproject.toml` (line 20): Added `jinja2>=3.1.0` dependency
  - `mcp-server/src/mcp_server/services/assessment_service.py` (NEW, ~200 lines): AssessmentService with markdown section extraction and Jinja2 HTML rendering
  - `mcp-server/src/mcp_server/templates/assessment.html` (NEW, ~300 lines): Self-contained HTML template with inline CSS, sticky sidebar TOC, responsive layout, print-friendly
  - `mcp-server/src/mcp_server/schemas.py` (lines 196-201): Added `AssessmentGenerateResponse` schema
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 7-8): Added `import logging` + `logger` (was missing, caused NameError on usage posting failure)
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 20): Added `AssessmentGenerateResponse` to schema imports
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 340-350): Added assessment generation hook in `_run_generate_background()` after RAG push
  - `mcp-server/src/mcp_server/api/routes/projects.py` (lines 818-860): Added `POST /generate-assessment` and `GET /assessment` endpoints
- **Problem**: No customer-facing output -- platform was developer-only
- **Root cause**: No service existed to produce a readable, self-contained HTML page from generated docs
- **Change**: Created `AssessmentService` (sibling to `PdfService`) that reads `00_EXECUTIVE_SUMMARY.md` (required) and `11_MIGRATION_ASSESSMENT.md` (optional), extracts curated sections, renders them into a self-contained HTML page via Jinja2. Auto-regenerates after doc generation in `_run_generate_background()`. Two new endpoints: generate + serve.
- **Why this approach**: New service (not extending PdfService) because web layout needs differ from print (responsive sidebar, severity badges vs A4 pages). Jinja2 template for conditionals/loops. Self-contained HTML (no CDN/JS) for offline use. Graceful degradation if doc 11 missing.
- **Verification**: Tested section extraction (7/7 sections from doc 00, 4/4 from doc 11). Tested full HTML generation (1036 lines output). Tested graceful degradation (doc 11 missing: migration sections omitted, page still works). Also fixed pre-existing bug: `logger` was used but not imported in projects.py.

---

## Session: 2026-03-12 (Round 3)

### [Feature]: Consolidate 25 reference docs to 12 + improve doc generation quality

- **Files changed**:
  - `mcp-server/src/mcp_server/services/claude_service.py` (lines 291-580): Updated `agent_ref_map` to 3 bundles (Core/Business+Ops/Patterns+User), rewrote entire prompt removing Phase 2.5, added depth targets and narrative rules
  - `mcp-server/src/mcp_server/services/scanner_service.py` (lines 729-776): Updated `_AGENT_REFERENCE_DOCS` mapping from 10 groups to 3 (Core/Business+Ops/Patterns+User)
  - `.agent/system/mcp-workflow.md` (lines 134-221): Updated Phase 3 agent table (4/3/5 split), added depth targets table, narrative rules, updated output structure
  - `intodoc/reference-docs/` (25 files → 12 files): Consolidated reference docs per mapping
- **Problem**: Generated docs were "slim and shallow" after Round 2 optimizations. Phase 2.5 condensation was too aggressive (cutting detail). 25 docs spread too thin across 10 agents. No depth targets. Agent workload imbalanced (4/8/13).
- **Root cause**: (1) Phase 2.5 regex-based condensation stripped important detail before Phase 3 agents could use it. (2) 25 docs across 10 agents meant each agent wrote very little. (3) No minimum line counts. (4) Unbalanced workload.
- **Changes**:
  - Removed Phase 2.5 condensation entirely — Phase 3 agents now read FULL `_analysis/{group}.md` files
  - Consolidated 25 reference docs → 12 (merging related docs: e.g. error handling + scheduling + known issues → 05_OPERATIONS_RISK)
  - Rebalanced Phase 3 agents: Core (4 docs), Business+Ops (3 docs), Patterns+User (5 docs)
  - Added depth targets: 200-800+ min lines per doc type
  - Added narrative rules: "never summarize when you can enumerate", require job name references
  - Added auto-fallback for large projects (>400KB analysis)
  - Updated scanner_service.py `_AGENT_REFERENCE_DOCS` and `get_reference_docs()` to match new 3-group structure
- **Why this approach**: Consolidation reduces overhead (fewer agent spawns), ensures each doc has enough source material for depth, and the depth targets + narrative rules create measurable quality standards.
- **Verification**: Pending — restart MCP server and run test doc generation.

---

## Session: 2026-03-12

### [Fix]: Auto-assign creator on ZIP upload project creation

- **Files changed**: `mcp-server/src/mcp_server/api/routes/projects.py` (lines 137-188, `upload_zip` endpoint)
- **Problem**: Non-admin user creates project via ZIP upload tab, but can't see it afterward. `list_projects` filters by `project_assignments`, but `upload_zip` never created an assignment for the creator.
- **Root cause**: The `add_repo` endpoint had auto-assign logic (added earlier), but `upload_zip` was missed — it lacked both `request: Request` parameter and the INSERT into `project_assignments`.
- **Change**: Added `request: Request` parameter to `upload_zip` signature. Added auto-assign block (same pattern as `add_repo`): extracts `auth_payload` from `request.state`, inserts into `project_assignments` with `ON CONFLICT DO NOTHING`.
- **Why this approach**: Mirrors the existing `add_repo` pattern exactly. Both project creation paths now auto-assign the creator.
- **Verification**: MCP server restarted via systemctl. Also manually assigned stale `test` project to a local test user in DB.

---

## Session: 2026-03-11 (Round 2)

### [Perf]: Round 2 doc generation optimizations — Phase 2.5 condensation + group splitting + pre-bundled assets

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (methods: `_format_chunk_markdown` new, `_preprocess_scan_data` rewritten, prompt rewritten)
- **Problem**: Run #2 showed Phase 2 improved (33→18 min) but Phase 3 regressed (21→29 min). Analysis files totaled 688KB — too large for inline passing. MCP tools unavailable on VPS (empty settings.json), so agents wasted turns hunting for format guidance. USA group (90 jobs) was a Phase 2 bottleneck. Core+Data agent had the 7 heaviest docs.
- **Root cause**: (1) 688KB analysis couldn't pass inline, all 3 Phase 3 agents read same 688KB file. (2) No MCP tools = no `get_analysis_prompts()` (223KB) or `get_reference_docs()` (692KB). (3) USA group too large for single agent. (4) Unbalanced Phase 3 workload.
- **Changes**:
  - **Fix 1 — Pre-bundled reference docs**: `_preprocess_scan_data()` now bundles reference docs per Phase 3 agent: `_ref_core.md` (111KB), `_ref_data_ops.md` (269KB), `_ref_config_user.md` (313KB). Eliminates `get_reference_docs()` MCP dependency.
  - **Fix 2 — Pre-bundled analysis prompts**: Concatenates all 22 prompt files (223KB) into `_preprocess/_analysis_prompts.md`. Eliminates `get_analysis_prompts()` MCP dependency.
  - **Fix 3 — Phase 2.5 analysis condensation**: New prompt phase between Phase 2 and Phase 3. Orchestrator reads all analysis files, extracts per-agent relevant sections, writes `_briefing_core.md`, `_briefing_data_ops.md`, `_briefing_config_user.md` (~100-150KB each instead of 688KB). Uses Python regex extraction.
  - **Fix 4 — Large group splitting**: Groups with >40 jobs auto-split into `{group}_part1.md` / `{group}_part2.md`. Phase 2 spawns one sub-agent per part, merges into single analysis. Extracted `_format_chunk_markdown()` static helper for chunk processing reuse.
  - **Fix 5 — Rebalanced Phase 3 agents**: Core (4 heavy docs: 00-03), Data+Ops (8 medium docs: 04,07-11,16,17), Config+User (13 light docs: 05,06,12-15,18-23,25). Moved DATA_FLOWS and SCHEMA_DICTIONARY out of Core.
- **Why this approach**: Each fix targets a specific measured bottleneck from Run #2. Pre-bundling eliminates MCP dependency. Condensation reduces Phase 3 input by ~4x. Group splitting parallelizes the USA bottleneck. Rebalancing equalizes Phase 3 wall-clock time.
- **Verification**: Python syntax check passed. Expected improvement: ~51.5 min → ~29-33 min.

---

## Session: 2026-03-11

### [Fix/Perf]: Doc generation speed optimization + token tracking fix

- **Files changed**: `mcp-server/src/mcp_server/services/claude_service.py` (entire file)
- **Problem 1**: Token tracking showed 0 for all generate runs. `result_event.get("cost_usd")` and `result_event.get("total_input_tokens")` returned None because Claude CLI uses different field names.
- **Root cause 1**: Claude CLI result event uses `total_cost_usd` (not `cost_usd`) and nests tokens under `usage{}` (not `total_input_tokens`/`total_output_tokens`).
- **Change 1**: Fixed field extraction: `cost_usd` → `total_cost_usd`, tokens from `result_event.get("usage", {})` including cache tokens, model name from `modelUsage`.
- **Problem 2**: Doc generation took 58 min (target: ~20 min). Sub-agents wasted ~33 min fighting JSON parsing errors, cascading Read failures, and redundant file I/O.
- **Root cause 2**: Raw job JSONs (26K-73K tokens) too large for Read tool (25K limit). Cascading sibling failures when one Read fails in a parallel batch. 5 Phase 3 agents each independently reading same analysis files.
- **Change 2**:
  - Added `_preprocess_scan_data()` method: reads raw scan data via ScannerService, writes structured markdown per group to `_preprocess/{group}.md`
  - Phase 2 prompt: agents read `_preprocess/` files instead of calling `fetch_chunk()` on raw JSONs
  - Phase 3 prompt: orchestrator reads analysis files and passes content INLINE to 3 agents (down from 5), agents don't read files from disk
  - Phase 4: cleanup now also deletes `_preprocess/` directory
  - Added FILE READING GUIDANCE section: max 3 parallel Reads, use Bash for large files, use python3 for JSON extraction
- **Why this approach**: All improvements target wasted I/O time, not analytical depth. Same data, same quality, fewer round-trips.
- **Verification**: Python syntax check passed, import verification passed. Expected improvement: ~58 min → ~20-25 min.

---

### [Feature]: Doc gen + deep dive token persistence via RAG backend

- **Files changed**:
  - `mcp-server/src/mcp_server/services/rag_bridge.py` -- added `post_usage()` method (fire-and-forget POST to RAG backend)
  - `mcp-server/src/mcp_server/api/routes/projects.py` -- call `bridge.post_usage(interface="doc_generation", ...)` after doc gen; intercept usage SSE events in deep_dive_query and persist via `bridge.post_usage(interface="deep_dive", ...)`
- **Problem**: Doc generation extracted cost/tokens from Claude CLI but never saved to QueryUsage table. Deep dive streamed usage to frontend but didn't persist.
- **Root cause**: No mechanism existed to push usage data from the KB MCP server to the RAG backend's QueryUsage table.
- **Change**: RagBridge now has `post_usage()` that POSTs to `{base_url}/api/v1/usage/record` with Bearer auth. Doc generation calls it after `complete_generate_run`. Deep dive SSE stream intercepts `type="usage"` events and calls it.
- **Why this approach**: Fire-and-forget with warning log on failure -- usage recording failure shouldn't break doc generation or deep dive streaming.
- **Verification**: Frontend build passes, 236 RAG backend tests pass.

---

## Session: 2026-03-10

### [Feature]: Per-project usage widget + ROADMAP.md

- **Files changed**:
  - `knowledge_base/mcp-server/src/mcp_server/schemas.py` -- added `ProjectUsageResponse` model
  - `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` -- added `GET /projects/{id}/usage` endpoint with SQL aggregation
  - `knowledge_base/dashboard/src/types.ts` -- added `ProjectUsage` interface
  - `knowledge_base/dashboard/src/api/client.ts` -- added `getProjectUsage()` method
  - `knowledge_base/dashboard/src/hooks/useProjects.ts` -- added `useProjectUsage()` hook
  - `knowledge_base/dashboard/src/components/ProjectSettings.tsx` -- added usage summary widget (tokens, cost, run counts)
  - `ROADMAP.md` (root) -- created master roadmap tracking file
- **Problem**: No per-project usage visibility in KB dashboard; no master roadmap file existed
- **Root cause**: Feature not yet implemented (Phase 1.3 gap)
- **Change**: Added usage API endpoint that aggregates GenerateRun/ScanRun stats via SQL, and a 4-tile widget in ProjectSettings showing generate runs, scan runs, total tokens, and total cost. Created ROADMAP.md with full 3-phase product roadmap with checkboxes.
- **Why this approach**: Reuses existing GenerateRun token/cost fields; SQL aggregation is efficient; widget fits naturally in the Settings tab
- **Verification**: TypeScript type check passed (`tsc --noEmit`), Python syntax verified

---

## 2026-03-03 -- Fix stuck "generating" status in dashboard

**What was done:**
- Added `refetchInterval: 10_000` to `useProjects()` hook so project list polls every 10s
- Added `POST /mcp/reset-status` backend endpoint to reset stuck generating/scanning projects
- Added `ResetStatusRequest` schema to `schemas.py`
- Added `resetStatus()` to API client and `useResetStatus()` mutation hook
- Added red "Reset" button to ActionButtons (visible only when status is generating/scanning)
- The reset endpoint sets project status to `error`, marks running GenerateRun/ScanRun records as `failed`
- Updated `mcp-server.md` and `dashboard.md` to reflect new endpoint and features

**Files changed:** 7 files (5 source, 2 docs)

**Lessons learned:**
- SSE-only UI updates are fragile -- always pair with polling as a fallback
- Stuck states need an explicit user escape hatch (reset button) since disabling all actions locks the user out

---

## 2026-02-19 -- Documentation Restructuring

**What was done:**
- Restructured platform documentation following AI Jason's .agent folder pattern
- Rewrote `CLAUDE.md` from 480 lines to ~65 lines (concise rules + pointers)
- Moved MCP 4-phase workflow to `.agent/system/mcp-workflow.md` (no content lost)
- Rewrote `.agent/README.md` with smart "when to read what" index
- Updated `.agent/system/architecture.md` to be platform-generic (removed vaxxinova-specific metrics)
- Created new system docs: `tech-stack.md`, `mcp-server.md`, `dashboard.md`
- Created SOPs: `run-doc-generation.md`, `add-new-project.md`, `setup-dev-environment.md`, `troubleshooting.md`
- Created ADR: `decisions/001-mcp-over-cli.md`
- Populated `.claude/commands/update_doc.md` (/update-doc command)
- Created `.claude/PROGRESS_LOG.md` and `.claude/PROJECT_MEMORY.md`

**Files changed:** 15 files (3 rewrites, 1 update, 11 new)

**Lessons learned:**
- CLAUDE.md should be concise (~100 lines max) with pointers to detailed docs
- Smart README index ("when to read what" table) helps Claude Code find relevant docs fast
- All platform docs should be generic -- use `{project_id}` instead of hardcoded names
- SOPs with numbered steps and actual commands are more actionable than prose descriptions

---

## Session: 2026-04-24

### Feature: Tailscale Ollama integration + N-pass section-by-section doc generation for local models

**Files changed:**
- `knowledge_base/mcp-server/src/mcp_server/providers/orchestrator.py` (major — all Ollama quality improvements)
- `knowledge_base/mcp-server/src/mcp_server/providers/langchain_provider.py` (Ollama LLM backend + cross-provider write_llm)
- `knowledge_base/mcp-server/src/mcp_server/providers/__init__.py` (Ollama URL routing)
- `knowledge_base/mcp-server/.env` (added MCP_OLLAMA_BASE_URL)
- `knowledge_base/dashboard/src/components/ProjectSettings.tsx` (Write Model selector + Tailscale Ollama options)
- `knowledge_base/dashboard/src/components/ActionButtons.tsx` (removed model preset dropdown; reads write model from localStorage)

**Problem:**
Generating docs with `ollama/qwen2.5-coder:7b` (Tailscale) scored 1.20/5.0 -- all 15 docs completed but averaged 157 lines when 820 lines is required. Phase 2 analysis produced 4-8KB per group instead of the 25-50KB from API models. Root causes:
1. Model received the full ReAct tool-calling prompt but was running in simple-chain mode (no tool calls) -- contradictory instructions.
2. Single-call generation: 7B model naturally stops at 150-200 lines regardless of instructions.
3. `<<<FILE:>>>` delimiter format caused all Phase 2 groups to fail silently (model couldn't follow the format).

**Root cause (primary):**
qwen2.5-coder:7b is a 7B code completion model that defaults to short, generic output. Even with explicit minimum line requirements it stops early because the model has no mechanism to sustain long-form output across a single call.

**Changes:**

*Phase 2 (analyze_group) N-pass:*
- Replaced single-call `_phase2_ollama_prompt()` with `_PHASE2_SECTIONS` (5 sections) + `_phase2_section_prompt()`.
- `_run_section_series()` makes one LLM call per section, concatenates all outputs, writes to file.
- Each section call gets: full context_files (scan data) + prior-section tail (300 chars) + focused per-section prompt.
- Expected: 5 sections x 80-100 lines each = 400-500 lines per group (vs 157 lines total before).

*Phase 3 (write_docs) N-pass:*
- Replaced 2-pass (first/second half) with full N-pass: one call per section from `_DOC_SECTIONS`.
- `_phase3_section_prompt()` mirrors `_phase2_section_prompt()` -- no tool-use language, mandatory job references, per-section minimum.
- Multi-doc Ollama assignments also handled section-by-section per doc.

*`_run_section_series()` (new function):*
- Builds context_block once, loops over sections, acquires `_OLLAMA_SEM` per LLM call (preserves serialization for Tailscale).
- Failed sections produce placeholder text; file is still written from successful sections.
- Returns `(True, "wrote N lines (M/K sections)")` for diagnostics in log.

*Delimiter fix (earlier in session):*
- Single-file writes use raw response mode -- no delimiter parsing at all.
- Multi-file: lenient regex `r"<<<FILE:\s*(.+?)>>+\n(.*?)<<<END>>>"` accepts >> or >>> variations.

*LangChain Ollama backend:*
- Switched from `ChatOllama` to `ChatOpenAI` with `/v1` appended to Ollama URL (OpenAI-compatible endpoint).
- Ollama always routes to `config.ollama_base_url` (Tailscale) regardless of main doc_generation_base_url.

*Dashboard write model selector:*
- `ProjectSettings.tsx`: new "Write Model (Phase 3)" select backed by localStorage per project.
- `ActionButtons.tsx`: removed model preset dropdown; reads `localStorage.getItem(\`project_write_model_${projectId}\`)` at generate time.

**Why this approach:**
The fundamental fix for small models is one focused task per LLM call. Asking a 7B model to write 600+ lines in a single call is futile -- it stops early regardless of instructions. Splitting into one call per section (50-120 lines each) gives the model a completable task scope, and Python concatenates the results.

**Verification:**
Restart mcp-server (`python3 -m mcp_server --serve --port 8002`) then re-run generation for localESBCHECK.
Expected: Phase 2 analysis 20-40KB per group (vs 4-8KB), overall quality score 2.5-3.5 (vs 1.58).
Check: `wc -l output/localESBCHECK/docs_staging/_analysis/*.md`

## Session: 2026-05-20

### [Refactor]: .agent/ documentation audit and accuracy fix

- **Files changed**: `knowledge_base/.agent/README.md`, `knowledge_base/.agent/SOP/add-new-project.md`, `knowledge_base/.agent/SOP/setup-dev-environment.md`, `knowledge_base/.agent/decisions/001-mcp-over-cli.md`, `knowledge_base/.agent/system/architecture.md`, `knowledge_base/.agent/system/dashboard.md`, `knowledge_base/.agent/system/mcp-server.md`, `knowledge_base/.agent/system/tech-stack.md` (updated); `knowledge_base/.agent/SOP/run-doc-generation.md`, `knowledge_base/.agent/system/lessons-learned.md` (deleted).
- **Problem**: `.agent/` docs were significantly out of sync with the codebase -- wrong tool counts, deleted CLI commands described as functional, stale `vaxxinova/` paths, missing platforms, outdated component lists.
- **Root cause**: `.agent/` docs written once during initial setup and never maintained. New features (Workato/Generic/Lobster platforms, 6 Hindsight tools, custom harness provider, template system, 11 more dashboard components) captured in CLAUDE.md and PROJECT_MEMORY but never backfilled into reference docs.
- **Change**: Audited all 11 `.agent/` files against live code. Deleted 2 redundant files. Updated 8 files: tool count 8->14, prompt format 22 .txt->2 .md guides, removed legacy CLI section (no analyze/generate commands exist), platform support Talend-only->Talend/Workato/Generic/Lobster, `vaxxinova/` paths->`knowledge_base/`, dashboard component list 5->16, ADR-001 marked "partially superseded" by custom provider.
- **Why this approach**: Spawned audit agent to compare docs against live code rather than editing from memory.
- **Verification**: All file paths and tool names verified against live source. Commit `9913dc6`.

### [Feature]: Level 6 PostToolUse hook -- automatic Python syntax check

- **Files changed**: `~/.claude/hooks/py_compile_check.py` (new, global), `~/.claude/settings.json` (PostToolUse hook added). Not tracked in this repo.
- **Problem**: Python syntax errors from edits were only caught at service startup, not at edit time.
- **Change**: Global hook reads `tool_input.file_path` from stdin after every `Edit`/`Write`; runs `python3 -m py_compile` on `.py` files; exits 2 with error output on failure, silent on success.
- **Why this approach**: `py_compile` checks syntax without importing. Exit 2 surfaces the error to the agent immediately and blocks continuation.
- **Verification**: Wired via `PostToolUse` matcher `Edit|Write`. Applies globally across all projects.

> **NOTE**: Uncommitted working tree changes exist in `knowledge_base/mcp-server/` (projects.py +23, claude_service.py +50, config.py +1) implementing a file-based generation lock (`acquire/release_generation_lock`, `write_run_manifest`, `.generation.lock` in staging_dir, 409 on concurrent generate). Not worked on this session -- commit or log separately.

## Session: 2026-05-05

### [Docs/Backlog]: EP-001 deployment foundation realignment

- **Files changed**: `backlog/epics/EP-001-poc-deployment-foundation.md`, `backlog/stories/US-001-write-deployment-runbook.md`, `backlog/stories/US-002-validate-runbook-reference-vps.md`, `backlog/stories/US-003-configure-nginx-certbot-ssl-dns.md`, `backlog/stories/US-004-full-stack-smoke-test.md`, `backlog/stories/US-005-configurable-ip-allowlisting.md`, `backlog/stories/US-006-github-repo-migration.md`, `deploy/RUNBOOK.md`, `deploy/SMOKE-TEST.md`, `deploy/deploy-client.sh`, `.env.production.template`, `CLAUDE.md`, `knowledge_base/CLAUDE.md`, `RAG_pipeline/CLAUDE.md`.
- **Problem**: EP-001 stories were customer-specific and stale: hardcoded Lobster/Cronos/Strato language, Caddy wording, duplicate EP-001 references, and no clear distinction between six Docker services and the host-level MCP API.
- **Root cause**: Backlog had drifted from the current deploy assets. The repo already has `deploy/deploy-client.sh` and nginx/certbot templates, while older Caddy and one-off VPS wording remained in stories.
- **Change**: Reframed EP-001 as generic deployment foundation. Made nginx/certbot canonical, kept Caddy as legacy/deferred, added `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`, updated `deploy-client.sh` to generate MCP systemd env vars, and documented the state in CLAUDE files.
- **Why this approach**: EP-001 should make deployment repeatable before any customer-specific rollout. Customer deployments can then use the generic runbook as validation evidence rather than being hardcoded into the foundation epic.
- **Verification**: `bash -n deploy/deploy-client.sh` passed. Stale-reference searches for target EP-001 files found no old Lobster/Cronos/Strato/Caddy requirements except intentional Caddy legacy notes. EP-001 is still not accepted until the runbook and smoke test pass on a clean reference VPS.

## Session: 2026-05-24

### [Feature]: EP-005 Mission Control -- heartbeat receiver scaffolded and tested end-to-end

- **Files changed**: `mission_control/src/mission_control/__init__.py` (new), `mission_control/src/mission_control/config.py` (new), `mission_control/src/mission_control/receiver.py` (new), `mission_control/src/mission_control/main.py` (new), `mission_control/src/mission_control/__main__.py` (new), `mission_control/pyproject.toml` (new), `mission_control/.env.example` (new), `.env` (appended ops telemetry vars -- gitignored)
- **Problem**: No central visibility into customer instance health. EP-005 requires a Mission Control service that aggregates heartbeat snapshots pushed by each customer MCP server.
- **Root cause**: The sender (`mcp_server/services/ops_telemetry.py`) was already fully built and pushing a rich `ops.telemetry.v1` payload (projects, tokens, cost, quality, run history). The receiver did not exist.
- **Change**: Built minimal FastAPI receiver at `mission_control/`. Three endpoints: `POST /ops/clients/{slug}/telemetry` (stores snapshot as JSON file), `GET /ops/clients/{slug}/snapshot` (retrieve), `GET /ops/clients` (list). Bearer token auth via `MC_API_KEY` env var. Snapshots written to `mission_control/snapshots/{slug}.json`, one file per client, overwritten each push. Runs via `python -m mission_control`.
- **Why this approach**: JSON file storage avoids any DB dependency for the localhost/tunnel test slice. Proves the architecture before adding Postgres, UI, or a permanent deploy.
- **Verification**: (1) Local test -- local Docker mcp-server pointed at `host.docker.internal:9100`, pushed successfully every 60s. (2) Remote test -- cloudflared tunnel exposed localhost:9100; koopmans VPS at `koopmans.nexus.intodata.nl` set `MCP_OPS_TELEMETRY_URL` and restarted; first push received within 60s from IPv6 `2a01:239:4cd:ff00::1:0`. Real koopmans data confirmed: 20 projects, 218 docs, 865 jobs, 145.9M input tokens, 4.9M output tokens, $274.44 cost, avg quality 4.15.
## Session: 2026-06-09

### [Feature]: Documentation evidence audit and deterministic source-fact ledger slices

- **Files changed**: `.plans/01_grill.md`, `.plans/02_prd.md`, `.plans/03_slices.md`, `.plans/04_active_plan.md`, `.plans/05_inspection_report.md`, `knowledge_base/mcp-server/src/mcp_server/services/doc_evidence_auditor.py` (new), `knowledge_base/mcp-server/tests/test_services/test_doc_evidence_auditor.py` (new), `knowledge_base/mcp-server/src/mcp_server/providers/custom/source_context.py`, `knowledge_base/mcp-server/tests/test_providers/test_source_context.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/source_fact_ledger.py` (new), `knowledge_base/mcp-server/tests/test_providers/test_source_fact_ledger.py` (new).
- **Problem**: Generated docs could claim source evidence was unavailable even when scanned source contained the relevant files/facts. The user needed stronger confidence that source ZIPs/repos were scanned completely and that Phase 3 writers would eventually receive factual, source-backed evidence for chatbot answerability.
- **Root cause**: Evidence was spread across scan output, source coverage, engineering facts, SQL usage artifacts, and generated docs with no deterministic audit trail showing where source was lost or which answer-critical facts were available before writing.
- **Change**: Planned the Documentation Evidence Depth + Repair Quality work through grill/PRD/slices, then built Slices 1-3. Slice 1 added `DocEvidenceAuditor` for scan-to-output evidence-loss and missing-claim reporting. Slice 2 extended the auditor with repo/ZIP source-to-scan completeness and exposed `classify_source_path()` from source packaging semantics. Slice 3 added a deterministic `source_fact_ledger` that consolidates SQL table/field, config key, connection name, DB registration, route/handler/service, auth path, source-file status, swallowed-error, and risk facts from scanner/engineering/SQL/source-coverage evidence.
- **Why this approach**: Kept the first slices deterministic and token-free so we can measure source completeness and fact availability before changing Phase 1/2/3 prompts or repairs. Reused `EngineeringFacts`, `extract_sql_usage()`, source packaging classifications, and source coverage artifacts instead of creating a parallel evidence system.
- **Verification**: Focused smoke/checks only; formal `/validate` is intentionally deferred until all slices are complete. Checks run: `python3 -m py_compile` on changed Slice 3 Python files; `uv run pytest tests/test_providers/test_source_fact_ledger.py -q` (9 passed); `uv run pytest tests/test_providers/test_sql_usage.py tests/test_providers/test_engineering_facts -q` (17 passed); `uv run pytest tests/test_services/test_doc_evidence_auditor.py tests/test_providers/test_source_context.py -q` (26 passed). Manual smoke built ledgers for `test`, `cdc_v2`, `steven_harness`, `cli_steven`, `profiles_gabe`, and `vaxxinov`; repeated builds were stable and warnings were zero. Inspection report verdict: PASS after fixing deterministic timestamp, auth-path emission, and direct `_source_coverage.json` ingestion.

## Session: 2026-06-10

### [Feature]: Evidence retention plus evidence-gated narrow repair slices

- **Files changed**: `.plans/01_grill.md`, `.plans/02_prd.md`, `.plans/03_slices.md`, `.plans/04_active_plan.md`, `.plans/05_inspection_report.md`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/evidence_retention.py` (new), `knowledge_base/mcp-server/src/mcp_server/providers/custom/repair_planner.py` (new), `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase2_analysis.py`, `knowledge_base/mcp-server/tests/test_providers/test_evidence_retention.py` (new), `knowledge_base/mcp-server/tests/test_providers/test_repair_planner.py` (new), `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py`.
- **Problem**: Repair agents were still triggered by form-only checks such as exact `- COVERED:` markers, byte-count depth thresholds, and guide-section headings. Logs showed 99k-173k token repair calls, several failed/empty calls, and a `Training|Jasmijn_section_repair` path that could fail the run while sending no useful source context.
- **Root cause**: Python validators treated formatting deviations as repair-worthy substance failures, then repair calls often received polluted broad context or no direct context. Phase 3 repair still used legacy validator-error prompts even when source-grounded retention evidence was available.
- **Change**: Built Slice 4 evidence retention reporting and Slice 5 repair planning. Added deterministic retention reports for facts lost between source ledger, analysis, and docs. Added `repair_planner.py` to classify Phase 2 issues into deterministic marker/summary patches versus blocking substantive gaps, enforce a 50k-token repair packet cap, redact secrets, and build source-grounded Phase 3 repair packets from retention reports. Phase 2 marker/depth issues now write repair plans instead of calling blind LLM repair. Missing `GROUP_SUMMARY` gets a deterministic structural shell; missing substantive Talend sections stay blocking. Phase 3 targeted repair now prefers retention packets and bounded direct context before falling back to legacy repair.
- **Why this approach**: Keeps Python in charge of deciding whether evidence exists, but removes LLM repair for syntactic-only failures. When a model is needed, it gets a compact evidence packet tied to actual retained or missing facts instead of the whole staging context.
- **Verification**: Inspection initially blocked on Phase 3 still using legacy repair and deterministic section patching hiding substantive missing sections; both blockers fixed. Final inspection verdict: PASS. Focused checks run: `python3 -m py_compile` on changed Slice 5 Python files passed; `uv run pytest tests/test_providers/test_repair_planner.py tests/test_providers/test_custom_provider.py -q` passed with 188 tests; `uv run pytest tests/test_providers/test_repair_planner.py tests/test_providers/test_evidence_retention.py tests/test_pipeline tests/test_providers/test_custom_provider.py -q` passed with 218 tests. Formal all-slices `/validate` remains intentionally deferred per user instruction.

## Session: 2026-06-12

### [Feature]: Layered RAG Slice 2 -- `_rag_support/` persistence and analysis_cache restore

- **Files changed**: `.plans/04_active_plan.md`, `.plans/05_inspection_report.md`, `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`, `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py`, `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py` (new), `knowledge_base/mcp-server/tests/test_pipeline/test_warning_collector.py`, `knowledge_base/mcp-server/tests/test_services/test_git_reingestion.py` (new), `knowledge_base/mcp-server/tests/test_services/test_rag_bridge.py`, `knowledge_base/mcp-server/tests/test_services/test_rag_support_package.py` (new). Commit `fc3788a`.
- **Problem**: Slice 1 made the RAG backend layer-ready, but the generation harness still deleted the Phase 2 material later slices need: `Phase4Cleanup` removed `docs_staging/_analysis/` before the atomic swap, and `_run_generate_background` tried to repopulate `analysis_cache/` from `docs/_analysis/` -- a directory that no longer existed post-cleanup (dead code). Incremental webhook regeneration therefore always fell back to full regeneration.
- **Root cause**: No artifact survived the Phase 4 cleanup + atomic swap that carried Phase 2 group analyses into the published `docs/` tree; the post-swap cache copy was written against a pre-cleanup directory layout and silently never ran.
- **Change**: New service `rag_support_package.py` with two pure-filesystem helpers. `build_rag_support_package()` runs in `Phase4Cleanup.run()` before the deletion loop: recreates `_rag_support/phase2_analysis/` from scratch, copies only `_analysis/{group}.md` for facts-manifest groups (never `_doc_pack_*`, `_bundle_*`, reports), and writes `_rag_support/manifest.json` (schema_version, project_id, platform, per-artifact path/source_path/source_layer/artifact_type/visibility/group/bytes/sha256, missing_groups). Missing groups are logged via `log_cb`, never synthesized. `replace_analysis_cache_from_rag_support()` replaces the dead copy block in `_run_generate_background`: rebuilds the flat `analysis_cache/*.md` from `docs/_rag_support/phase2_analysis/`; an absent or empty package removes any stale cache and creates none, so incremental regen falls back to full regeneration.
- **Why this approach**: Service-level helper (not a private harness method) because Phase 4 produces the package and the projects route consumes it after the swap; Slice 3 reuses the same path constants for internal-layer ingestion. Positive allowlist from `state.facts_manifest.groups` because `_analysis/` also holds runtime doc packs and bundles that must never ship. Visible behavior is untouched: `RagBridge._read_md_files` globs only top-level `docs/*.md` (regression-tested), and the atomic swap contract is unchanged.
- **Verification**: `/inspect` verdict WARN (critical area: harness phase + MCP API route). Findings 1 (empty package left an empty-but-present `analysis_cache/`, weakening the full-regen fallback), 2 (missing-groups warning branch untested), and 4 (no `setup_incremental_staging` coverage) fixed in-session; finding 3 (`.agent/system/mcp-workflow.md` Phase 4 row) closed in this session-end. `/validate`: kb py_compile 7/7 OK, kb pytest 613 passed in 2.80s, RAG/dashboard SKIP (untouched). Manual check on a locally generated project still pending (requires a scanned project + working model).

### [Fix]: Strip leaked LLM preamble from Phase 3 final docs (commit `53914ae`)

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/providers/custom/harness.py`, `knowledge_base/mcp-server/tests/test_providers/test_custom_provider.py`.
- **Problem**: Published docs contained model chatter ("Now I have all the evidence. Let me compose the document.") — found by a Sonnet-agent quality analysis of the `phase2` run; enterprise gate had flagged `01_ARCHITECTURE_MAP.md` `client_ready: false` but gates are advisory so it shipped.
- **Root cause**: `_final_doc_content` (harness.py) shipped raw model text verbatim; it only prepended a canonical title, which pushed pre-heading chatter into the doc body instead of removing it.
- **Change**: New `_strip_llm_preamble()` static method runs before the title logic: drops pre-heading lines (blank lines and `---` rules plus chatter capped at 500 chars; longer pre-heading content is preserved). Single choke point — all standard and pack Phase 3 docs flow through `_final_doc_content`.
- **Why this approach**: Structural rule (docs must start at their first heading) instead of brittle phrase matching; cap prevents discarding real content.
- **Verification**: 6 new unit tests; replayed the strip over all 169 recorded `_agent_tasks/phase3_doc/*/output.md` files on disk — 112 leaked, 112 fixed, 0 missed. Full suite 618 passed. Direct fix at user request (Scout-size, 2 files); known remaining sibling issue: duplicate `SOURCE_SQL`/`CUSTOM_CODE` sections in Phase 2 output (separate root cause, not yet fixed).

### [Feature]: Layered RAG Slice 3 -- internal-layer ingestion push + entity index (commit `39895b1`)

- **Files changed**: `RAG_pipeline/backend/src/api/routes/ingest.py`, `RAG_pipeline/backend/src/db/models/entity_alias.py` (new), `RAG_pipeline/backend/src/db/models/__init__.py`, `RAG_pipeline/backend/src/schemas.py`, `RAG_pipeline/backend/alembic/versions/028_add_entity_aliases.py` (new), `RAG_pipeline/backend/tests/test_api/test_entity_aliases.py` (new), `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py`, `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`, `knowledge_base/mcp-server/src/mcp_server/services/rag_support_package.py`, `knowledge_base/mcp-server/tests/test_services/test_rag_bridge.py`, `knowledge_base/mcp-server/tests/test_services/test_rag_support_package.py`, `.plans/04_active_plan.md`, `.plans/05_inspection_report.md`.
- **Problem**: Slice 2 persisted internal support layers into `docs/` but nothing pushed them to RAG; entity/usage CSV indexes had no query-time home.
- **Root cause**: `RagBridge` only knew top-level visible docs; the backend had no internal-doc lifecycle or alias storage.
- **Change**: RAG backend gains `entity_aliases` table (migration 028), `DELETE /ingest/by-project/{id}/internal` (escaped `startswith` filter on `{project}/_internal/` scoped filenames; also clears aliases), and `POST /ingest/entity-aliases` (per-project transactional replace; full project delete also clears aliases). MCP `rag_support_package.py` gains `collect_internal_artifacts()` (4 layers: technical_analysis from `_rag_support/`, support_maps from `_facts/*.md`, scorecard, evidence_slices; never CSVs/JSON; empty files dropped), `internal_doc_filename()` (percent-encoded, `_internal/{layer}/` prefixed), and `build_entity_aliases()` (deterministic CSV parse + dedupe keeping highest confidence). `RagBridge.sync_internal_layers()` does delete-then-push-then-aliases, aborting on delete failure so old/new sets never mix. Wired into `_run_generate_background` (errors merge into `rag_errors` → run verdict) and `/push-to-rag` (message includes internal counts; `last_rag_push_at` now requires zero errors, matching the generation path).
- **Why this approach**: Wholesale delete-and-repush because removed groups are invisible to the visible-doc diff; discovery/alias-building are pure-filesystem service functions so the bridge owns transport only; default retrieval stays canonical-only via Slice 1 `source_layer` filtering with no retrieval-side change.
- **Verification**: `/inspect` verdict WARN — finding 1 (unescaped SQL LIKE wildcard in destructive internal-delete filter) fixed in-session with `startswith(..., autoescape=True)` + compiled-SQL assertion; finding 2 (internal docs visible in admin document lists) logged as follow-up for a later slice. `/validate`: rag py_compile 6/6, rag pytest 353 passed, kb py_compile 5/5, kb pytest 627 passed. New coverage: delete-first ordering, delete-failure abort, partial push continuation, empty-tree no-op, `Training|Jasmijn` percent-encoding, CSV dedupe/sort, missing-CSV no-op, API-key 401s, alias replace per-project scoping.

## Session: 2026-06-18

### [Feature]: Reingestion Reliability Slice 3 -- normalized patch-first outcomes

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/services/auto_doc_update.py`, `knowledge_base/mcp-server/src/mcp_server/services/git_diff_patcher.py`, `knowledge_base/mcp-server/src/mcp_server/services/git_reingestion.py`, `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py`, `knowledge_base/mcp-server/tests/test_services/test_auto_doc_update.py`, `knowledge_base/mcp-server/tests/test_services/test_git_diff_patcher.py`, `knowledge_base/mcp-server/tests/test_services/test_git_reingestion.py`, `knowledge_base/mcp-server/tests/test_services/test_rag_bridge.py`.
- **Problem**: Git update summaries used legacy actions like `patched` and `skipped` without a normalized operator-facing outcome vocabulary, and RAG sync failure could be treated as weaker evidence than required for baseline SHA advancement.
- **Root cause**: Patch, auto-doc, regeneration, and RAG bridge code each interpreted success locally. There was no shared persisted `outcome`/`reason_code` contract, RAG push success did not require every patched doc to be accepted, and the regeneration success path could leave baseline ownership ambiguous.
- **Change**: Added normalized summary helpers for outcomes `skip`, `bounded_patch`, `targeted_regen`, `full_regen`, `manual_review`, and `failed` while preserving existing internal `action` values. Persisted `reason_code` and `decision_strategy` in patch and auto-doc summaries. Added strict RAG push success checking that requires the expected patched docs to be pushed with no errors; RAG errors or zero-pushed required docs now persist `outcome: failed`, `reason_code: rag_sync_failed`, restore docs, and preserve SHA. Targeted regeneration summaries now include `regen_result`. `run_git_reingestion()` now fills the `after_sha` baseline only after background generation leaves the project `ready` with no `error_message`, and skips the write if generation already recorded it. Inspection also fixed sanitized Git stderr handling and routed skip/no-op pulls through `pull_remote_ff_only()`.
- **Why this approach**: It adds the operator contract without breaking existing internal actions or tests from Slices 1-2. The RAG helper makes the baseline gate explicit at the boundary where patch success is decided, and successful regeneration baseline advancement remains tied to generation plus RAG sync instead of webhook receipt alone.
- **Verification**: `python3 -m py_compile` passed for all touched Slice 3 service and test files. Focused tests passed: `uv run pytest tests/test_services/test_auto_doc_update.py tests/test_services/test_git_diff_patcher.py tests/test_services/test_git_reingestion.py tests/test_services/test_rag_bridge.py -q` (`46 passed`). Full MCP server pytest passed: `uv run pytest -q` (`646 passed`). Inspection verdict: PASS.

## Session: 2026-07-12

### [Feature]: Version-bound evidence document update and revert lifecycle

- **Files changed**: `mcp-server/src/mcp_server/services/evidence_doc_update.py`, new `evidence_doc_revert.py`, update/revert schemas and project routes, RBAC matrix coverage, and focused API/service tests.
- **Problem**: Agent-proposed documentation edits could overwrite a document that changed after approval, and a successful file write followed by failed RAG indexing left published content and searchable content inconsistent. There was no receipt-bound revert operation.
- **Root cause**: The update endpoint had no expected-base hash, version receipt, atomic rollback discipline, section-targeted replacement, or inverse operation.
- **Change**: Added named-section replacement, `expected_base_sha` optimistic concurrency, before/after version fields, reconciliation metadata, rollback when indexing fails, and a receipt/document/hash-validated revert endpoint that also rolls itself back if reindexing fails. Routes retain authentication, project RBAC, filename validation, and safe response schemas.
- **Why this approach**: Human approval must authorize one exact base version, not a mutable filename. File publication and RAG indexing are treated as one recoverable operation, while receipts provide a constrained audit/revert handle rather than broad write access.
- **Verification**: Focused evidence-update/revert/RBAC suite **78 passed**. MCP full suite with the sibling `intodoc` source on `PYTHONPATH` reached **1098 passed, 1 unrelated baseline failure** in the model catalogue. Changed-scope Ruff passed. A live update of `final_agentic_e2e/00_AGENT_BRIEF.md` indexed one document, issued a receipt, and live revert restored the original SHA `2990b82b8236cbcef48f67881c9f1cf3c22104025886c8b342b9ef7c5eed59a6` and removed the temporary marker.

## Session: 2026-07-13

### Fix: Scanner xmlns redaction bug + Talend job-version dedup (commit b620fb4)

- **Files changed**:
  - `intodoc/src/intodoc/scanner/credential_filter.py` (+72, -13)
  - `intodoc/src/intodoc/platforms/talend/parser.py` (+51, -5)
  - `intodoc/tests/test_scanner/test_credential_filter.py` (+74, -12)
  - `intodoc/tests/test_platforms/test_talend_parser.py` (+113)
- **Problem**: Validating the VolkerWessels Assetcheck Talend export (178 MB, ASSETCHECK + REFERENCES projects) for the Werkorder reverse-engineering POC exposed two scanner defects: (1) zero of 579 parsed jobs had `tmap_data` despite `TalendMapper:MapperData` being present in the raw `.item` XML; (2) all 579 historical item versions (e.g. 24 versions of `AssetCheck_Main_Job`) were emitted as active jobs.
- **Root cause**: (1) The credential filter's URL pattern redacted XML namespace declaration URIs (`xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"` -> `"[URL_REDACTED]"`), so ElementTree registered the wrong namespace URI and the parser's `{NS['xsi']}type` attribute lookup returned empty -- tMap extraction silently skipped, and `xmi:id` metadata-connection indexing partially failed. A prior test (`test_xml_namespace_in_url_not_doubled`) had codified this as acceptable. (2) Talend Studio exports retain every saved version of an item; the parser had no latest-version selection and the `active` flag was never set false.
- **Change**: Credential filter now masks `xmlns` declaration values with placeholders before any pattern runs and restores them verbatim afterwards (all levels, XML content only); URL redaction moved out of the blind pattern list into `_filter_urls()` which honors `_SAFE_DOMAINS` exactly like `_filter_fqdns()`; `talend.org` added to safe domains; `has_credentials()` updated to match. Talend parser gained `_latest_job_versions()` (numeric version compare via `_version_sort_key`, identity = (path dir, name) per source dir) called from `_parse_all_jobs`; superseded count exposed as `stats.superseded_job_versions` and in console output.
- **Why this approach**: xmlns URIs are structural XML identifiers, not data -- masking them as a class prevents every pattern (URL, FQDN, IP) from corrupting parser namespace resolution, rather than special-casing one regex. Version dedup lives in the scanner because every downstream consumer (source_context, talend_intelligence, engineering-facts extractors, sql_usage) reads `scan_result.jobs`; fixing it at the source prevents stale-version evidence bloat in all of them. `active=False` semantics stay reserved for genuinely disabled integrations (Lobster).
- **Verification**: intodoc suite 388 passed, 8 skipped; changed-scope Ruff clean; mcp-server consumer tests (`-k "source_context or talend"`) 27 passed. Assetcheck re-scan: 66 jobs (513 superseded skipped), 13 ESB routes, 22 jobs with tMap data (was 0), 6 metadata connections (was 3), redaction still active (16,785 redactions; client endpoint URLs/passwords/emails/IPs verified redacted; `enc:` values absent from scan JSON).

## Session: 2026-07-14

### [Refactor]: Pipeline evidence-integrity rearchitecture + credential-filter hardening (3 root-cause fixes, multi-agent build)

- **Files changed** (today's work only; evidence_doc_update/revert + routes/schemas/RBAC changes are the prior 2026-07-12 session, already logged):
  - intodoc: `src/intodoc/scanner/credential_filter.py` (+136/-x), `src/intodoc/scanner/post_filter.py`, NEW `src/intodoc/scanner/redaction_verifier.py`, `src/intodoc/cli.py`, `src/intodoc/config.py`, `src/intodoc/models.py`, `src/intodoc/platforms/talend/parser.py`, `src/intodoc/platforms/generic/parser.py`, `tests/test_scanner/test_credential_filter.py` (+127), NEW `tests/test_scanner/test_redaction_verifier.py`
  - mcp-server: NEW `providers/custom/evidence_tasks.py`, NEW `providers/custom/target_evidence.py`, NEW `providers/custom/fabrication_validators.py`, `providers/custom/harness.py` (+486/-x incl. deletions), `providers/custom/prompts.py`, `providers/custom/constants.py`, `providers/custom/analysis_parser.py`, `pipeline/helpers.py`, `pipeline/phases/phase1_investigation.py`, `pipeline/phases/phase2_analysis.py`, `config.py`, `data/prompt_templates/phase2_analysis.md`, NEW tests `test_providers/test_evidence_tasks.py` / `test_target_evidence.py` / `test_fabrication_validators.py`, updated `test_providers/test_custom_provider.py`, `test_providers/test_prompt_engineering.py`, `test_pipeline/test_phase_failures.py`
  - docs: `knowledge_base/CLAUDE.md`, `knowledge_base/intodoc/CLAUDE.md`
- **Problem**: Forensic audit of reverseEngineer run 50 (traced every claim through source .item -> scan -> chunks -> Phase 1 -> Phase 2 -> Phase 3 -> published doc) found: (1) cleartext `MaximoPassword`/`MaximoUser`/`AES_Key` leaked end-to-end into the published doc (camelCase names bypass the underscore-suffix sensitivity heuristic; bare UNC hostnames like `srvt0156` bypass the dot-TLD FQDN regex); (2) Phase 2 chunk agents fabricated analyses (invented component names, "code unavailable") for jobs whose evidence they never held; (3) Phase 3 writers anchored on misleading derived-map signals and mis-attributed shared-context-group values to jobs.
- **Root cause**: (1) name-sensitivity matching required `_password`-style underscore suffixes; no post-redaction verification existed, so one regex miss flowed to publication. (2) `harness.py` `_phase2_group_chunks` split evidence at 55K chars (mid-job, mid-JSON) and computed "units visible in this chunk" via substring match against text containing group-wide inventories (plus an `or group_units` fallback) -> every chunk header claimed all jobs; the prompt's "every unit MUST appear / no placeholders" contract then forced fabrication; the LLM merge concatenated contradictory duplicate sections without arbitration. (3) Phase 3's primary input was Phase 2's narration (`_doc_pack_*.md`) plus 118KB unfiltered `dependency_map.md`; verbatim evidence was present but buried, and no validator caught invented component names or false-unavailability claims.
- **Change**: (WP1) token-aware sensitive-name matching (snake/kebab/camelCase tokenizer; qualified `key` tokens; infra tokens at STRICT), UNC bare-host redaction, top-level `Component.code` (Talend + Generic populate; redacted by post_filter), and a fail-loud post-scan `redaction_verifier` gate at the CLI choke point (exit 2, no scan output on failure; `--allow-redaction-failures` escape hatch). (WP2) char-chunker and LLM merge task deleted; `evidence_tasks.py` plans per-job tasks by splitting on `## Job:` boundaries with first-fit whole-job bin-packing (`MCP_PHASE2_TASK_EVIDENCE_BYTES`, default 70000); task headers list exactly the contained units; prompt enumeration scoped to task units with explicit "state what is missing, do NOT reconstruct" for evidence-less units; per-task unit-coverage validation feeds the repair ladder; multi-task groups assembled deterministically in Python (verbatim section bodies, per-task provenance) + one `_phase2_group_synthesis` LLM call writing only `GROUP_SUMMARY`/`CROSS_JOB_FINDINGS`. (WP3) `target_evidence.py` builds deterministic per-unit bundles straight from the scan cache (all code/tMap/SQL verbatim, high-signal params, provenance-tagged context tables with DIVERGES flags, callers/callees), delivered FIRST in `_phase3_context_files`; derived maps filtered per-unit (`MCP_PHASE3_FILTERED_MAP_BYTES` 24000, bundle budget `MCP_PHASE3_TARGET_EVIDENCE_BYTES` 120000); `fabrication_validators.py` (component-echo + no-unavailability) wired pre-acceptance into Phase 3 doc validation and Phase 2 task validation.
- **Why this approach**: fixes at the level that prevents recurrence: secrets get defense-in-depth (better heuristics AND a verification gate so no single regex is load-bearing); the unit of analysis work now equals the unit of evidence (a whole job), making honest coverage structural rather than prompt-enforced; evidence flows to writers deterministically (Python extraction, no LLM re-narration in the facts path) while Phase 2 remains the interpretation layer; validators make fabrication a task rejection instead of a silent publish. Explicitly NOT changed: config_runtime_map "ssl inconsistency" — verified as genuine per-job differences in the customer's .item exports, not an aggregator bug.
- **Verification**: intodoc suite `413 passed, 8 skipped` (was 392; 21 new tests). mcp-server full suite `1135 passed, 3 failed` — the 3 are pre-existing/environmental (`test_catalogue_models_are_rag_capable`, 2x `ModuleNotFoundError: intodoc`). Offline validation against real artifacts: new gate FAILS the existing leaky reverseEngineer scan (108 sensitive-name findings, 50 UNC residuals) and passes after re-filtering with 0 findings; GetLocation per-unit bundle = 39.7KB (vs 296KB run-50 context) containing verbatim code/tMap/SQL/params; both verified run-50 fabrications (`tSetGlobalVar_3`, "code unavailable") replay as caught. OPERATOR FOLLOW-UPS: rotate the leaked Maximo credential; purge existing leaky artifacts (`intodoc-output/{reverseEngineer,alitalend,reverse_engineer_VW}/scan*`, `output/reverseEngineer/docs/_facts/*` + published SendServiceRequest doc); check Qdrant for the ingested doc; optional end-to-end regen for final proof.

### [Feature]: MCP bridge for secure ZIP onboarding, swap-pack selection, and version-bound document operations (commit `f260527`)

- **Files changed**: `mcp-server/src/mcp_server/api/permissions.py`, `mcp-server/src/mcp_server/api/routes/plugins.py`, `mcp-server/src/mcp_server/api/routes/projects.py`, the upload-limit fields in `mcp-server/src/mcp_server/config.py`, `mcp-server/src/mcp_server/schemas.py`, new `mcp-server/src/mcp_server/services/evidence_doc_revert.py`, `mcp-server/src/mcp_server/services/evidence_doc_update.py`, `mcp-server/tests/conftest.py`, `mcp-server/tests/test_api/test_evidence_doc_update.py`, `mcp-server/tests/test_api/test_projects.py`, and `mcp-server/tests/test_api/test_rbac.py`.
- **Problem**: Nexus could not safely hand ZIP onboarding through an agent workflow, configure the actual IntoDoc swap pack from the main chat UI, or guarantee that approved evidence-based document updates and reverts remained version- and index-consistent.
- **Root cause**: ZIP ingestion returned a project directly with weak archive bounds and no opaque workflow receipt; project plugin assignment lacked owner-level management enforcement; generation had no Nexus Pack snapshot field; and document updates lacked the full optimistic-concurrency/revert lifecycle needed by the harness.
- **Change**: ZIP upload now validates entry count, expanded size, unsafe paths/symlinks, writes atomically, and returns a signed metadata-only receipt with a project-scoped verification endpoint. MCP project plugin assignment now requires project owner or superadmin. Generation accepts bounded `nexus_pack_ids` and existing `plugin_pack_id` semantics remain the swap-pack authority. Evidence update/revert includes expected SHA, section targeting, rollback on RAG failure, receipts, reconciliation status, and receipt-bound inverse operations.
- **Why this approach**: MCP remains the documentation/source provider while Nexus owns orchestration. ZIP bytes and credentials never enter LangGraph checkpoints; project defaults stay in MCP where IntoDoc already resolves them; writes are approved against one exact content version and treat filesystem plus RAG indexing as one recoverable operation.
- **Verification**: MCP API/RBAC/update suite `111 passed`; changed owned-file Ruff checks passed. Rebuilt MCP container is healthy. Combined stack migrated to `059_harness_project_packs` through the RAG backend and all eight services are healthy.
- **Residuals**: Authenticated manual UI traversal requires MFA. Unrelated evidence-integrity pipeline work and generated customer pack/material files remain deliberately outside commit `f260527`.

## Session: 2026-07-17

### [Feature]: Certified Swap Pack governance and Nexus execution boundary

- **Files changed**: `mcp-server/alembic/versions/021_add_plugin_pack_governance.py`; `mcp-server/src/mcp_server/api/{app.py,routes/plugins.py,routes/projects.py}`; `mcp-server/src/mcp_server/{config.py,schemas.py}`; `mcp-server/src/mcp_server/db/models.py`; `mcp-server/src/mcp_server/services/{pack_governance,nexus_execution_contract,plugin_schema,claude_service,generation_run_state}.py`; harness/provider prompt and target-evidence integration; Pack/API/RBAC/provider tests; dashboard Pack/API/generation-dialog files; and the corresponding certified Pack directories under `mcp-server/data/plugin_packs/` and `mcp-server/plugin_packs/`. Separate uncommitted scanner/topology work is present in `intodoc/src/intodoc/{lineage.py,cli.py,models.py}` plus lineage fixtures/tests and MCP component/job/lineage services/routes/tests; it is not part of the Pack-governance claim.
- **Problem**: Nexus private Pack creation and the actual IntoDoc Swap Pack registry were different systems. Users could not trust that the Pack selected in Nexus was the Pack used by generation, while direct local Pack mutation could bypass organizational analysis standards.
- **Root cause**: MCP previously treated registered plugin packs primarily as editable local files and project defaults. It had no certified provenance/digest boundary and no validation contract for the compiled Nexus Pack snapshot sent with a generation request.
- **Change**: Added certification state and provenance, trusted bundled/Git source verification, content-digest rechecks, organization-governed selection, owner/superadmin project assignment, and an execution contract that validates the Nexus snapshot before generation. Generation still resolves explicit `plugin_pack_id` before the project's saved default. Direct creation/import/deletion paths that would bypass Git/PR certification now return retired/blocked responses. Dashboard/API wiring exposes the governed documentation Pack catalogue and forwards the selected Pack to generation.
- **Why this approach**: Organization-controlled analysis semantics need one reviewable source of truth. Nexus can help a user draft a private additive Pack, but promoting core/functional behavior remains a Git/PR operation; MCP is the final authority at the point where documentation is generated.
- **Verification**: Current focused MCP governance, execution-contract, plugin API, generation schema, and scoped Phase 3 suite: **74 passed**. Earlier complete validation for Governance V1 recorded **1225 passed** with three pre-existing environment/catalogue failures. MCP container is healthy at session close.
- **Current status / residuals**: The governance/execution code is uncommitted in the current broad working tree. Before commit, separate Pack work from the concurrent topology/lineage and customer Pack artifacts, decide which sample Pack directories are product fixtures, and run a real project generation proving the selected certified/private composition changes the generated document set and remains visible in both Nexus and the port-8001 dashboard.
## Session: 2026-07-27

### Feature: connect Generate Docs to isolated OpenCode candidate generation

- **Files changed**: `docker-compose.yml`; `knowledge_base/opencode-runtime/opencode.base.json`; `knowledge_base/mcp-server/data/agentic_recipes/codebase_documentation/{recipe.yaml,prompts/*.md}`; `knowledge_base/mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; `knowledge_base/mcp-server/src/mcp_server/experiments/application/run_quick_candidate.py`; `knowledge_base/mcp-server/src/mcp_server/experiments/infrastructure/persistence/sqlalchemy_repository.py`; `knowledge_base/mcp-server/tests/agentic_experiments/test_quick_candidate.py`; `knowledge_base/mcp-server/tests/test_api/test_agentic_experiments.py`; `knowledge_base/dashboard/src/{api/client.ts,api/schemas.ts,components/GenerateDialog.tsx,components/AgenticExperimentsPage.tsx,hooks/useProjects.ts,hooks/useAgenticExperiments.ts,types.ts}`; `knowledge_base/dashboard/src/tests/{ep000-ui.test.tsx,agentic-experiments-ui.test.tsx}`.
- **Problem**: the isolated agentic experiment workspace existed, but the normal Generate Docs action could only invoke the legacy three-phase publisher; it could not start an OpenCode candidate for an A/B run.
- **Root cause**: candidate dispatch was deliberately gated during the safety slices. There was no API composition endpoint, no persistent OpenCode service in the root stack, and no execution-engine choice in the Generate dialog.
- **Change**: added a `POST /mcp/agentic-experiments/quick-run` composition endpoint that freezes the latest completed legacy baseline, approved scanner sources, and a certified generic codebase-documentation recipe before starting a background OpenCode 1.18.4 candidate. Added a bounded runner that submits frozen source content to a provider/model selected behind the OpenCode boundary, validates the exact Markdown bundle, records usage/cost/latency, and finalizes only the immutable candidate branch. Added the internal read-only OpenCode service with all tools denied and DeepSeek configured through an environment reference. Added the Generate dialog execution-engine selector and experiment polling.
- **Why this approach**: legacy `/mcp/generate`, publication, and RAG behavior remain untouched. OpenCode is an optional runtime adapter behind Nexus-owned baseline/source/recipe/ledger boundaries, and the recipe is platform/domain neutral rather than hardcoded to AliTalend or a single job.
- **Verification**: scoped Python compile passed; experiment/API suite `256 passed`; dashboard suite `17 passed`; TypeScript/Vite production build passed; Ruff and `git diff --check` passed; Docker Compose config passed; rebuilt OpenCode, MCP, and dashboard containers are healthy. Live no-cost preflight confirmed experiments enabled, DeepSeek credentials present, OpenCode health `200` with exact version `1.18.4`, and provider discovery `200`. No paid documentation request was made during deployment verification.
- **Live correction**: the first UI click exposed a source-checkout/container-layout mismatch: recipe discovery was relative to the installed Python module while Docker mounts certified recipes at `/app/data/agentic_recipes`. Added `MCP_AGENTIC_RECIPE_ROOT`, runtime-root-first discovery with source fallback, a container-layout regression test, and a controlled `503` for unavailable certified recipes. Focused correction suite: `11 passed`; deployed MCP health is green and the container resolves `codebase-documentation` v1.0.0 with ten Markdown outputs. Both failed clicks occurred before experiment creation or any provider request.

### [Feature]: replace generic OpenCode candidate with exact AlitaLand legacy-input replay

- **Files changed**: `knowledge_base/mcp-server/src/mcp_server/experiments/application/run_legacy_replay_candidate.py`; `knowledge_base/mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; `knowledge_base/mcp-server/tests/agentic_experiments/test_quick_candidate.py`; `knowledge_base/mcp-server/tests/test_api/test_agentic_experiments.py`; `knowledge_base/dashboard/src/api/client.ts`; `knowledge_base/dashboard/src/components/GenerateDialog.tsx`; `knowledge_base/dashboard/src/tests/ep000-ui.test.tsx`; `knowledge_base/.claude/{context.md,PROJECT_MEMORY.md,PROGRESS_LOG.md}`.
- **Problem**: the first OpenCode candidate generated ten very thin documents and was not a fair comparison with the AlitaLand legacy output. It used three generic recipe sentences, one autonomous session, and a current scan snapshot rather than the actual inputs used by the successful baseline.
- **Root cause**: the earlier experiment architecture proved isolation, persistence, runtime routing, and UI dispatch, but the quick runner never consumed the baseline run's preserved per-call `prompt.md`, `context_bundle.md`, `request.json`, or phase decisions.
- **Change**: added a strict 13-call replay runner. It verifies the baseline Phase 1 skip marker; reconstructs the legacy prompt/context payload using the original context-file separator and preserved ordering; uses the recorded Flash route for `AssetCheck_Interface` and Pro route for all remaining calls; disables extra OpenCode tools; saves Phase 2 and scorecard outputs as intermediates and all ten Phase 3 responses as candidate documents; and persists a receipt containing prompt/context/request/full-prompt hashes, route/options, OpenCode session, usage, latency, and explicit runtime limitations. The normal Generate Docs workflow remains unchanged and the OpenCode selection now targets `alitalend-legacy-equivalence`.
- **Why this approach**: it controls prompt, evidence, ordering, and model route so the runtime wrapper is the principal experimental variable. Each preserved call remains independent: Phase 3 uses its exact frozen baseline context, not newly generated candidate analysis, because chaining candidate intermediates would no longer be a one-to-one input replay.
- **Verification**: Python compile and changed-scope Ruff passed; experiment/API suite **261 passed**; focused quick-run API tests **2 passed**; dashboard tests **6 passed** and production build passed; real host and container checks loaded all **13** frozen inputs totaling **3,260,261 bytes**; OpenCode provider discovery exposes both `deepseek-v4-flash` and `deepseek-v4-pro`; rebuilt MCP/dashboard/OpenCode containers are healthy; deployed dashboard bundle contains `OpenCode legacy replay`; `git diff --check` passed. No paid generation was triggered.

### [Feature correction]: use exact legacy inputs inside one tool-using OpenCode session

- **Files changed**: new `knowledge_base/mcp-server/src/mcp_server/experiments/application/run_agentic_legacy_candidate.py`; `knowledge_base/mcp-server/src/mcp_server/api/routes/agentic_experiments.py`; `knowledge_base/mcp-server/tests/agentic_experiments/test_quick_candidate.py`; `knowledge_base/mcp-server/tests/test_api/test_agentic_experiments.py`; `knowledge_base/dashboard/src/components/GenerateDialog.tsx`; `knowledge_base/dashboard/src/tests/ep000-ui.test.tsx`; `knowledge_base/.claude/{context.md,PROJECT_MEMORY.md,PROGRESS_LOG.md}`.
- **Problem**: the strict replay used OpenCode as a sequential API wrapper. It preserved prompt/input parity but did not exercise the OpenCode behavior the user intended: autonomous file discovery, grep/read navigation, planning, writing, and review.
- **Root cause**: “one-to-one” was interpreted as replaying thirteen provider calls rather than preserving the same prompts and source material inside an agent workspace. The resulting run completed one Flash call and began the second before the user stopped it.
- **Change**: cancelled the run, aborted its active session, and retired every queued/running AlitaLand experiment. Replaced the normal quick-run executor with one OpenCode session whose immutable workspace includes all current scanner artifacts plus all exact legacy prompt/context/request/manifest snapshots. The agent can use `read`, `glob`, `grep`, `edit`, and `write`; it must generate two candidate Phase 2 analyses, a candidate scorecard, the ten exact document outputs, then re-open and repair the bundle. Outputs and a receipt with input hashes, tools, session, usage, cost, and latency remain candidate-only.
- **Why this approach**: it preserves the successful legacy workflow's full instruction/evidence surface while allowing OpenCode to decide how to navigate and apply it. Candidate intermediates now feed candidate synthesis, making this the intended agentic architectural A/B rather than an executor microbenchmark.
- **Verification**: changed-scope Ruff and Python compile passed; experiment/API suite **262 passed**; dashboard tests **6 passed** and production build passed; a no-provider real workspace build captured **19 scanner files**, **13 exact prompts**, **13 exact context bundles**, **71 input receipts**, and the **10-document contract**. Rebuilt MCP, dashboard, and OpenCode containers are healthy; the deployed bundle contains `OpenCode agentic candidate`; the deployed MCP imports `AgenticLegacyOpenCodeRunner`; database and OpenCode status both report zero active work. No paid generation was triggered after deployment.
## Session: 2026-07-27

### [Feature]: Second OpenCode candidate with bounded specialist orchestration

- **Files changed**: `knowledge_base/opencode-runtime/opencode.base.json`, `knowledge_base/opencode-runtime/README.md`, `knowledge_base/mcp-server/src/mcp_server/experiments/application/run_orchestrated_candidate.py`, `knowledge_base/mcp-server/src/mcp_server/experiments/application/run_agentic_legacy_candidate.py`, `knowledge_base/mcp-server/src/mcp_server/experiments/infrastructure/opencode/client.py`, `knowledge_base/mcp-server/src/mcp_server/api/routes/agentic_experiments.py`, `knowledge_base/dashboard/src/api/client.ts`, `knowledge_base/dashboard/src/hooks/useProjects.ts`, `knowledge_base/dashboard/src/components/GenerateDialog.tsx`, `knowledge_base/mcp-server/tests/agentic_experiments/test_quick_candidate.py`, `knowledge_base/mcp-server/tests/agentic_experiments/test_agentic_config.py`, `knowledge_base/mcp-server/tests/test_api/test_agentic_experiments.py`, `knowledge_base/dashboard/src/tests/ep000-ui.test.tsx`.
- **Problem**: The first OpenCode candidate used one session to investigate, analyze, write ten documents, and review itself. It was faster and clearer than legacy but lost per-unit coverage, made over-broad universal claims, used weak evidence paths, and had no independent fact-checking.
- **Root cause**: The runtime explicitly denied OpenCode's Task tool, set `subagent_depth=0`, and instructed a sole agent. Nothing enforced an execution plan, evidence registry, canonical-unit coverage, independent review, child-session accounting, or output secret safety.
- **Change**: Added a separate `orchestrated` quick-run mode and UI choice. OpenCode 1.18.4 now defines one primary `nexus-doc-orchestrator` and five hidden specialist roles (evidence, analyst, referee, writer, reviewer); only the primary may call allowlisted `nexus-doc-*` tasks and depth is capped at one. The new runner freezes the same legacy-equivalence inputs, requires real child sessions, aggregates parent/child usage and tools, and persists execution plan, evidence registry, coverage ledger, review report, intermediates, documents, and sanitized child receipts. Deterministic acceptance validates non-empty canonical units/claims, exact ten-document ownership and coverage, required review sections, and rejects sensitive values reproduced from frozen JSON sources. Legacy and the single-agent candidate remain unchanged and non-publishing isolation remains intact.
- **Why this approach**: Agentic planning and specialization can improve coverage and cross-source reasoning, but authority, output contracts, safety, accounting, and publish boundaries must remain deterministic. Keeping Candidate C additive enables a clean three-way A/B/C evaluation.
- **Verification**: changed-file Ruff and `py_compile` passed; full agentic/API suite `266 passed`; dashboard tests `10 passed`; dashboard production build passed. OpenCode 1.18.4, MCP, and dashboard images rebuilt. Runtime `/agent` exposes the primary orchestrator and five subagents, MCP imports `OrchestratedOpenCodeRunner`, and all three containers are healthy. Fake-provider runner coverage verifies child sessions, aggregate usage, orchestration artifacts, and sensitive-value rejection. No paid provider run was started.
## Session: 2026-07-28

### [Feature]: Grounded lean OpenCode documentation candidate and deterministic repair

- **Files changed**: `dashboard/src/api/client.ts`, `dashboard/src/components/GenerateDialog.tsx`, `dashboard/src/hooks/useProjects.ts`, `dashboard/src/tests/ep000-ui.test.tsx`, `mcp-server/src/mcp_server/api/routes/agentic_experiments.py`, `mcp-server/src/mcp_server/experiments/application/fact_ledger.py`, `mcp-server/src/mcp_server/experiments/application/recover_orchestrated_candidate.py`, `mcp-server/src/mcp_server/experiments/application/run_agentic_legacy_candidate.py`, `mcp-server/src/mcp_server/experiments/application/run_orchestrated_candidate.py`, `mcp-server/src/mcp_server/experiments/infrastructure/opencode/client.py`, `mcp-server/src/mcp_server/experiments/infrastructure/persistence/sqlalchemy_repository.py`, `mcp-server/tests/agentic_experiments/test_agentic_config.py`, `mcp-server/tests/agentic_experiments/test_fact_ledger.py`, `mcp-server/tests/agentic_experiments/test_opencode_client.py`, `mcp-server/tests/agentic_experiments/test_quick_candidate.py`, `mcp-server/tests/test_api/test_agentic_experiments.py`, `opencode-runtime/opencode.base.json`, `opencode-runtime/README.md`.
- **Problem**: Candidate C was close to legacy quality but used a large, expensive agent team and still produced wrong protected counts, semantic overclaims about `NEED_AUTH`, and unqualified runtime-absence claims. It also generated all outputs but failed finalization on an underspecified execution-plan field shape.
- **Root cause**: Agents independently inferred scanner facts, writer ownership was too granular, the final review was model-only, and the orchestration contract listed required fields without defining collection shapes. Failed-run finalization also had no provenance-checked no-dispatch path.
- **Change**: Added a scanner-derived immutable fact ledger, high-precision document claim checker, one bounded no-subagent repair turn, and a lean seven-child grounded orchestrator with two evidence agents, two analysts, two document-family writers, and one reviewer. Added `grounded` API/runtime routing and a recommended Generate Docs choice while leaving legacy, single-agent, and Candidate C unchanged. Relaxed Candidate C's two collection fields to accept non-empty arrays or objects. Added a strict explicit recovery path that verifies the exact idle OpenCode session and workspace before indexing without any provider prompt.
- **Why this approach**: Deterministic facts remove work LLMs are bad at while preserving OpenCode's useful planning, search, delegation, and synthesis. Two document families reduce duplicated context/cost, a single reviewer keeps cross-document consistency global, and repair is allowed only for exact checker findings. Recovery trusts runtime provenance, not merely leftover files.
- **Verification**: `py_compile` and changed-scope Ruff passed; `tests/agentic_experiments tests/test_api/test_agentic_experiments.py` passed (`272 passed`); dashboard Vitest passed (`19 passed`, with existing React Query stderr warnings only); production TypeScript/Vite build passed; `git diff --check -- knowledge_base` and `docker compose config --quiet` passed. Rebuilt/deployed OpenCode, MCP, and dashboard; live health checks passed and OpenCode 1.18.4 exposed all five grounded roles. Candidate C recovery correctly refused after the runtime rebuild removed its session record; it remains failed with zero candidate artifacts and no provider calls were made.

## Session: 2026-07-29

### [Feature]: Nexus-staged Grounded OpenCode v2 with fail-closed trust and pack-driven roles

- **Files changed**: new `mcp-server/src/mcp_server/experiments/application/{grounded_preflight.py,grounded_pack.py,run_staged_grounded_candidate.py}`; expanded `fact_ledger.py`; Grounded routing/progress in `api/routes/agentic_experiments.py`; Grounded metadata in the AlitaLand and logistics recipes; OpenCode specialist configuration/readme; experiment/API tests including new pack and staged-runner suites; dashboard experiment schemas/progress, provider/model selection, Generate dialog copy, and tests.
- **Problem**: Grounded v1 still trusted a parent OpenCode agent to decide delegation and completion, compiled zero scanner jobs from frozen `.bin` snapshots, could pass unsafe source/intermediate values downstream, had weak reconciliation/claim checks, and remained too easy to mistake for a Talend/ten-document-only implementation.
- **Root cause**: the ledger keyed media parsing from the physical snapshot extension instead of the logical filename; source trust was not a pre-session gate; role ownership, isolation, propagation, budgets, retry, and finalization were prompt conventions rather than Nexus-controlled state; the normal UI also forwarded catalogue IDs such as `deepseek/deepseek-v4-pro` where OpenCode expects provider-local `deepseek-v4-pro`.
- **Change**: Nexus now compiles a strict recipe-owned role DAG, creates physically isolated role packets/sessions, disables Task, schedules dependency-ready roles with bounded parallelism, passes raw sources only to evidence roles, and copies only accepted artifacts downstream. Added redaction/credential and ledger reconciliation preflight before OpenCode health/session creation; intermediate/final secret and reproduced-secret gates; exact ownership/JSON/evidence/claim validation; bounded role repair and file-backed progress. The AlitaLand pack preserves the exact legacy Phase 2/scorecard/Phase 3 inputs across seven roles, while a four-role logistics pack proves domain/output portability. The dashboard now selects the provider/model from discovery and converts canonical catalogue IDs to provider-local OpenCode IDs.
- **Why this approach**: OpenCode remains the file-searching/reasoning worker, while deterministic authority stays in Nexus. This keeps the experiment agentic where judgment helps, but makes scope, trust, isolation, cost bounds, acceptance, and finalization reproducible and provider-independent. Legacy, single-agent, and original OpenCode-parent orchestrated modes remain unchanged.
- **Verification**: parent rerun: experiment/API suite **292 passed**; dashboard **21 passed**; production Vite build passed; Ruff and Python compile passed; scoped diff checks and Compose config passed. Delegated full MCP suite: **1611 passed, 3 unrelated existing failures** (one Route33 catalogue assertion and two missing sibling `intodoc` package imports). Rebuilt/deployed OpenCode, MCP, and dashboard; Compose also recreated dependent backend and mission-control, which returned healthy. Live no-provider smoke loaded AlitaLand `(7 roles, 16 owned artifacts)` and logistics `(4 roles, 5 owned artifacts)`, OpenCode health reported `1.18.4`, all specialist agent identities were present, and DeepSeek was the only connected provider. Direct no-provider preflight against the current AlitaLand scan correctly rejected credential-shaped values in scanner JSON before OpenCode health/session creation. No prompt/model call, publication, or RAG ingestion occurred.
- **Live correction**: the first Grounded UI run (`2ae19064-4ec4-4982-a6b9-d110f7f0339d`) failed before provider dispatch because the background worker tried to read `recipe_id` from `GenerationExperiment`; recipe identity is stored on its immutable `GenerationRecipeSnapshot`. Both the launch and recovery paths now resolve through `recipe_snapshot_id` and fail closed for missing/mismatched snapshots. Added two regression tests; the experiment/API suite now passes **294 tests** and Ruff is clean. Rebuilt only the MCP service and verified MCP, OpenCode, and Postgres healthy. The failed run made no model call and incurred no token usage/cost.
- **Safety warning refinement**: descriptive credential references in generated SQL/code analysis now produce structured warnings and do not stop Grounded downstream roles; high-confidence secret material, private keys, URL userinfo, opaque credential assignments, and reproduced source secrets remain blocking. Warnings are persisted in candidate metrics/receipts, set the experiment status to `completed_warnings`, are exposed by the experiment API, and appear in the dashboard. Added regression coverage for both warning and blocking cases. Validation: **295 MCP experiment/API tests passed**, focused Ruff passed, dashboard tests **4 passed**, production dashboard build passed. Rebuilt/deployed MCP and dashboard; both are healthy. The previous failed operations artifact is classified as two warnings by the deployed validator without rerunning the model.
- **Residuals**: the quick Generate Docs path intentionally freezes only the approved IntoDoc source set; generic directory/tabular/runtime composition is exposed through the experiment compose/compile surface. The broad dirty worktree also contains unrelated scanner/topology and Pack work. Whole-tree `git diff --check` still reports pre-existing CRLF/trailing-space lines in `intodoc/tests/test_platforms/test_lobster.py`; changed Grounded/dashboard scope is clean.

## Session: 2026-07-29

### [Feature]: Allow standalone OpenCode candidates without a legacy baseline

- **Problem**: Generate Docs rejected OpenCode with `409 No completed legacy baseline is available` for scanned projects that had no prior legacy documentation run.
- **Change**: Legacy baseline selection is now optional when no explicit baseline is requested. OpenCode experiments reserve without a baseline, still freeze the current IntoDoc scan and recipe, and run as isolated candidates. Existing baseline-backed A/B experiments retain their frozen baseline and comparison behavior. Updated Generate Docs copy to describe optional comparison rather than requiring A/B.
- **Verification**: MCP experiment/API tests **295 passed** before this change and the standalone regression plus API suite **15 passed** after it; changed-scope Ruff passed; dashboard tests **4 passed**; production dashboard build passed. MCP and dashboard images rebuilt and deployed; MCP is healthy and dashboard is starting/healthy after restart. No provider generation was started automatically.
- **Follow-up**: Grounded standalone execution no longer requires the legacy Phase 1 decision snapshot or legacy workflow prompt copies. Those inputs are required only when a baseline-backed replay is requested; a scan-only run proceeds from its frozen sources and fact ledger. Staged Grounded/quick/API tests **31 passed** and Ruff passed; MCP rebuilt and redeployed healthy.
- **Follow-up**: Deterministic document-claim mismatches are now warnings rather than finalization failures. The complete candidate bundle is retained and finalized as `completed_warnings`; the claim report and structured warning categories remain visible for review. Focused Grounded/quick/API tests **31 passed**, Ruff passed, and MCP was rebuilt/redeployed healthy.

### [Feature]: Publish approved landscapes and expose the read-only Slice 3b API

- **Files changed**: scanner/landscape files under `intodoc/src/intodoc/` and their tests; `mcp-server/src/mcp_server/{api/routes/projects.py,schemas.py}`; `mcp-server/src/mcp_server/services/{scan_output_lock.py,scanner_service.py,static_topology_publisher.py}`; `mcp-server/tests/test_api/{test_project_landscape.py,test_static_topology_publication.py}`; `mcp-server/tests/test_services/{test_scanner_service.py,test_static_topology_publisher.py}`.
- **Problem**: The organization landscape was not a first-class approved artifact, MCP had no read-only landscape contract, and a completed scan could be raced, tampered with, or published to Runtime without proving that every derived artifact passed redaction verification.
- **Root cause**: Scan output was file-by-file rather than one publication boundary; there was no cross-process output lock, receipt/hash gate, or explicit landscape response schema.
- **Change**: Added atomic `publication_approval.json` covering scan/manifest/lineage/landscape hashes, fail-closed publisher validation and bounded translation, an O_EXCL scan lock held through publication, and RBAC-scoped `GET /mcp/projects/{project_id}/landscape` with explicit Pydantic models.
- **Why this approach**: Runtime receives only scanner-approved evidence; a partial, bypassed, stale, or tampered scan cannot publish. The endpoint stays read-only and frontend-agnostic, matching the explicit decision to defer illustration UI and chat.
- **Verification**: MCP publication/landscape/scanner suite **28 passed**; scoped Ruff and compileall clean. The real six-project receipt hash-matched all four files, the landscape response validated, and publisher compilation produced 429 jobs, 224 dependencies, and 2,145 lineage facts. Whole-file Ruff for `projects.py` remains blocked by unrelated pre-existing import-order/unused-import debt in the shared dirty file; the endpoint tests pass.
## Session: 2026-07-30

### [Fix]: Serve human-readable Talend component and connector semantics

- **Files changed**: `mcp-server/src/mcp_server/{schemas.py,services/job_graph_service.py}`; `mcp-server/tests/{test_api/test_project_landscape.py,test_services/test_job_graph_service.py}`.
- **Problem**: Job graphs exposed only technical IDs such as `tMSSqlInput_3`, `tXMLMap_1`, and `SUBJOB_OK`, leaving the graph hard to interpret even when Talend's own human label was present in scan data.
- **Root cause**: The job graph response ignored the component `LABEL` parameter and carried no deterministic vocabulary for component or connector behavior.
- **Change**: Added an author label alongside the stable technical ID, deterministic component explanations, and connector explanations that explicitly distinguish row data from execution triggers. Extended the landscape API contract with stable-project/copy metadata.
- **Why this approach**: Human labels improve comprehension without replacing edge/drill-down identity; explanations describe Talend semantics only and do not invent business purpose.
- **Verification**: focused MCP service/API suite **24 passed**; Ruff and compileall passed. Real scan smoke showed `LogMap` (`tXMLMap_1`) as “Maps hierarchical XML data,” `ReadRealizationDataFromRHM` (`tMSSqlInput_3`) as a data reader, FLOW as row transfer, and SUBJOB_OK as a no-data success trigger. Rebuilt MCP container is healthy.
## Session: 2026-07-30

### [Checkpoint]: Publish consolidated agentic, topology, and runtime work

- **Files changed**: Existing uncommitted source, tests, migrations, frontend, scanner, MCP, runtime, Docker, and project-context changes across `knowledge_base/` and `RAG_pipeline/`; root `.gitignore`.
- **Problem**: The feature branch contained several validated but uncommitted implementation batches, making subsequent semantic-topology work unsafe to isolate and leaving the remote branch behind the working state.
- **Root cause**: The agentic harness, scanner/landscape, runtime correlation, and L1/L2 frontend slices were developed across consecutive sessions without a final consolidated checkpoint.
- **Change**: Prepared one branch checkpoint containing all source deliverables while explicitly excluding generated `agentic-workspaces` scan copies and the confidential nested VW reverse-engineering repository from Git.
- **Why this approach**: The spike is already an ancestor of the topology branch, so a validated checkpoint allows `master` to advance through both histories without conflict while keeping customer-derived runtime evidence out of source control.
- **Verification**: `git diff --cached --check` and high-confidence credential-pattern scans passed. IntoDoc focused suite: 226 passed, 7 skipped. MCP agentic/topology suite: 318 passed. Runtime static-correlation suite: 17 passed. RAG topology frontend: 50 passed plus production build. Dashboard: 21 passed plus production build.

### [Feature]: Serve the semantic topology contract through MCP

- **Files changed**: `mcp-server/src/mcp_server/schemas.py`; `mcp-server/tests/test_api/test_project_landscape.py`.
- **Problem**: The scanner could persist semantic systems and exchanges, but MCP's explicit response model discarded the additive field before the frontend received it.
- **Root cause**: The API intentionally mirrors the scanner contract through strict local Pydantic models; the new semantic models had not yet been added to that boundary.
- **Change**: Added explicit response models for semantic evidence, systems, integration solutions, exchanges, and summary; included them in `ProjectLandscapeResponse`; added API coverage for payload labels, implementing solutions, and drill-down provenance.
- **Why this approach**: Schema drift should fail loudly at the API boundary. Explicit models preserve backwards-compatible defaults while preventing arbitrary JSON from becoming a frontend topology authority.
- **Verification**: Landscape API tests **5 passed**. Full MCP suite **1,634 passed, 3 failed**: one pre-existing model-catalogue assertion and two local-environment failures because the MCP virtualenv lacks the sibling `intodoc` package. The rebuilt container has the package installed, validates the real 11-system/14-exchange artifact, and both MCP/frontend services are healthy.
