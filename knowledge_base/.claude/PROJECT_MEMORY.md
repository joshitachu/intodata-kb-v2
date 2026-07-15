# Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

- **Reingestion scan MUST target the per-project output dir, or the fingerprint reads a stale scan.** `ScannerService.run_scan` defaults `output_dir` to the ROOT `config.intodoc_output_dir`, but the scan fingerprint, delta classifier, and generation all read `intodoc_output_dir/{project_id}/scan/...`. `run_git_reingestion` must call `run_scan(..., output_dir=str(config.intodoc_output_dir / project_id))` (mirroring `_run_scan_background`). Omitting it writes the fresh scan to the root dir while the per-project dir stays stale -> the fingerprint sees "no change" -> reingestion silently skips generation for real code changes. This was a live-only bug (unit tests stub the scanner). Added 2026-07-02.

- **A scoped reingestion arrives with incremental staging pre-restored; `generate_docs` must not wipe it.** When `scoped_docs` is set, `setup_incremental_staging` has already restored the UNCHANGED groups' analyses into `docs_staging/_analysis/` and deleted only the CHANGED groups'. `generate_docs` wipes `docs_staging` whenever `resume_state` is None — so the wipe is guarded by `and scoped_docs is None`. Consequently, in the harness a present `_analysis/{group}.md` on a scoped run == an unchanged group: Phase 1 investigation and Phase 2 analysis both reuse it (gated on `ctx.scoped_docs is not None`, not the fragile resume-state detection) and skip regeneration. Phase 3 doc preservation is separate (`_apply_scoped_docs`). Without these, "incremental" reingestion cost ~= full. Added 2026-07-02.

- **`run_git_reingestion` reports true work via the optional `outcome_sink` dict; callers must honor it.** The function returns `bool` (unchanged contract), but when passed `outcome_sink={}` it records `{"skipped": True|False, "regenerated_docs": [...]}`. A skip (empty fingerprint dirty-set or infra-only push) advances the SHA and returns True — so `auto_doc_update._scoped_regen` must check `outcome_sink["skipped"]` and report a truthful `skip` (no change-history entry, no drift bump) instead of a phantom `targeted_regen`. Change-history `touched_docs` and drift are derived from `regenerated_docs` (actual set), not the router's path-based estimate. Added 2026-07-02.

- **Webhook smoke/status visibility is read-only and must use GitHub's standard signature header.** Reingestion reliability Slice 5 adds `GET /mcp/projects/{project_id}/reingestion-status` as the compact operator surface for webhook registration/last observation, normalized update outcome/reason/error, RAG/regeneration result, last ingested SHA, remote hygiene, and cache/rebaseline readiness. This endpoint may inspect local readiness only; it must not start scan, generation, patching, RAG sync, fetch, or pull work. GitHub signed deliveries use `X-Hub-Signature-256`; the route keeps `X-GitHub-Signature-256` only as a legacy fallback. The local smoke script must sign with `X-Hub-Signature-256`, redact secrets in dry-run output, and authenticate the status GET with the existing `access_token` cookie contract. Added 2026-06-19.

- **Targeted regeneration requires both published support and restored cache readiness.** Reingestion reliability Slice 4 makes targeted regeneration unavailable unless a project has a baseline, visible generated docs, published `docs/_rag_support/phase2_analysis/*.md`, matching restored `analysis_cache/*.md`, and resolvable changed groups when a diff is in hand. `inspect_generation_cache_readiness()` compares support/cache filenames and SHA-256 hashes; missing or stale support/cache yields `requires_full_rebaseline` and stable reason codes such as `missing_rag_support`, `missing_analysis_cache`, and `analysis_cache_rag_support_mismatch`. Readiness/repair endpoints only report or repair credential hygiene and must not run scan, generation, patch, or RAG sync. Added 2026-06-19.

- **Git update summaries use normalized operator outcomes while preserving legacy actions.** Reingestion reliability Slice 3 keeps internal/backward-compatible `action` values such as `skipped` and `patched`, but every persisted Git update summary must also carry `outcome`, `reason_code`, and relevant `decision_strategy`. Operator outcomes are limited to `skip`, `bounded_patch`, `targeted_regen`, `full_regen`, `manual_review`, and `failed`. Bounded patch baseline advancement requires a successful required RAG push: any RAG error or fewer pushed docs than expected is `outcome: failed`, `reason_code: rag_sync_failed`, restores docs, and preserves the prior `last_ingested_commit_sha`. Manual review and missing-baseline outcomes also preserve SHA. Regeneration baseline advancement must be tied to successful background generation and RAG sync: `_run_generate_background` is the primary writer, and `run_git_reingestion()` may only fill `after_sha` after the project is `ready` with no `error_message`, skipping the write if generation already recorded it. Added 2026-06-18.

- **Git remote credential handling is centralized in `services/git_remote_service.py`.** Reingestion reliability Slice 1 added the single allowed path for token-authenticated Git remote work: strip credentials for persisted `repo_url`/`origin`, build authenticated URLs only in memory, set non-interactive Git env (`GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=echo`), and restore sanitized `origin` in `finally` after fetch/pull operations. Poller, diff patcher, full reingestion, auto-doc inspection/skip, and clone registration now route through this helper. Readiness reports also classify sanitized auth failures from `auto_reingest_error`, missing baseline, and missing incremental cache so auth-failing projects cannot look ready. Future webhook/reingestion slices must not reintroduce direct `inject_token` + `git remote set-url` call sites outside the helper and clone cleanup. Added 2026-06-18.

- **Webhook diagnostics live under `git_update_summary.webhook` and must be preserved by later summaries.** Reingestion reliability Slice 2 records matched webhook `ping`/`push`/`pull_request` observations before background work starts: sanitized repo URL, canonical repo key, delivery ID, event, status, reason code, observations, and push SHAs. `ProjectRegistry.record_webhook_observation()` merges into the existing summary, while `update_git_update_summary()` and `update_git_remote_metadata()` preserve the `webhook` subdocument when writing patch or remote-hygiene summaries. Future patch/regen code should update top-level action fields without dropping `git_update_summary.webhook`. Added 2026-06-18.

- **Core generation writes from bounded evidence; retrieval may be agentic but must be budgeted.** As of 2026-05-28, the `Steven_inventory` incident established that generic staging tools (`list_artifacts`, `read_artifact`) must not be exposed to Phase 2/3 core generation without hard byte caps and accumulated input-token caps. Phase 2 core generation uses bounded `context_files` by default; pack-specific depth should come from curated evidence bundles, not unbounded staging browsing.

- **`source_summary` in CATEGORY_PATTERNS is a catch-all — never use it as an evidence filter.** It matches `\b(file|source|component|unit|job|module|class|function)\b`, which hits virtually every code file. Using it in `_chunk_has_pack_relevant_content` would make the pre-screen a no-op. Enterprise runs filter on the 8 specific categories only (sql_queries, ddl_schema, orm_entities, db_connections, config_references, routes, handlers, auth).

- **Phase 1 group concurrency uses the same pattern as Phase 2.** `asyncio.gather(*[_investigate_group(g) for g in ctx.preprocess_groups])` with `asyncio.Semaphore(config.agent_phase1_concurrency)` inside each group task. Chunks within a group remain sequential (Phase 1 does not fan out at the chunk level). `state.calls.extend()` is safe inside concurrent group tasks because asyncio is single-threaded — list mutations are atomic from a coroutine's perspective.

- **`_evidence_register_errors` returns `(errors, warnings)` — callers must unpack a tuple.** Changed 2026-06-01. The old single-list signature is gone. `validate_source_provenance` unpacks both. Do NOT revert to `list[str]` — the tuple is the contract that separates structural failures (trigger repair) from content quality issues (never trigger repair).

- **ALL Evidence Register source-column quality issues are soft warnings — none are hard errors.** This includes: truly empty cells, placeholders (`-`, `n/a`, `tbd`), generic text ("reviewed notes"), and unrecognized path formats. Only structural failures (missing section, wrong headers, too few rows, bad confidence value) are hard errors that trigger repair. This is intentional: the repair cannot add provenance that wasn't in the scanner output.

- **`_PROVENANCE_SOURCE_RE` has a path-separator catch-all `[/\\][\w]`.** Any string containing a forward-slash or backslash followed by a word character is treated as valid provenance. This is the real cross-platform fix — new platforms and file types don't need individual extensions added. Only add explicit extensions for bare filenames without path separators.

- **`_tool_policy` repair check MUST come before phase-prefix checks.** `"phase2.repair".startswith("phase2")` is True, so putting the prefix check first silently disables tools for all Phase 2 repairs. The correct order: `"repair" in phase` first, then phase prefix checks. Any future `_tool_policy` changes must preserve this ordering or Phase 2 repairs will regress to one-shot silently. Fixed 2026-06-02.

- **`_phase3_targeted_repair_content` merges Evidence Register rows by topic — do not revert to full-replace.** Repair rows win on topic conflicts; existing rows for topics not addressed by the repair are preserved. Full-replace causes oscillating failures where each pass fixes some rows and drops others. Fixed 2026-06-02.

- **Synthesis docs (`09_EXECUTIVE_SUMMARY`, `07_QUALITY_SECURITY_RISK_SCORECARD`) must cite sibling doc filenames in their Evidence Register, not raw scanner paths.** The repair prompt branches on module-level `_SYNTHESIS_DOCS` in `harness.py`. `_PROVENANCE_SOURCE_RE` already accepts `.md` extensions. If adding new synthesis doc types, add them to `_SYNTHESIS_DOCS`. Fixed 2026-06-02.

- **Cross-package facts (`intodoc` scanner -> `mcp-server`) must be computed once in the scanner and only read on the mcp-server side, never re-derived.** `intodoc` and `mcp-server` are separate installable packages with no dependency between them; they communicate only through `scan_result.json` on disk. Talend ESB/Camel route detection previously lived as a late `talend_intelligence.py` heuristic re-matching component types from raw scan output — a duplicated-logic smell the project's "generalize fixes" rule flags. Fixed 2026-07-06 by moving classification into `TalendParser` (`is_esb_route`/`esb_component_types` on `ParsedJob`, flowing through `scan_result.json`); `talend_intelligence.py` now only aggregates those pre-computed fields and treats a missing field as `False` (defensive boundary read, not a fallback re-derivation). Apply the same pattern to any future scanner-derived classification: compute it where the platform parser already has the structured data, not downstream from raw JSON.

- **New conditional Phase 2 sections belong in `CONDITIONAL_SECTION_NAMES` (`prompt_packs.py`) + the shared `_has_section()` coverage check in `validators.py`, not a bespoke regex.** `SOURCE_SQL`/`CUSTOM_CODE`/`ESB_ROUTES` all follow this: one canonical section name in `constants.py`, registered in `CONDITIONAL_SECTION_NAMES`, prompt guidance added to the relevant platform section file, and coverage enforced only when a project-specific gate is true (e.g. `_esb_detected()` reading a `_preprocess/*.json` sidecar). Do not add a second synonym-matching regex for a heading the validator merely hopes the model writes — if a section matters enough to check for, it must exist in the doc catalog/prompt path too. Fixed 2026-07-06 (collapsed the old `ROUTE_GRAPH`/`ESB_ROUTES`/`ESB_ROUTE_GRAPH` 3-synonym regex into this pattern).

- **DAG mode is flagged and must preserve final gates.** `MCP_AGENT_DAG_ENABLED=false` by default. When enabled, the harness persists `_plans/execution_plan.json`, `_plans/execution_graph.json`, `_plans/node_results.json`, and `_plans/graph_events.jsonl`. Planner output is advisory: Python validates docs, groups, dependencies, cycles, graph depth, node count, and required final gates. Unsafe/invalid planner output falls back to the deterministic graph. Added 2026-06-02.

- **LLM planner is optional and never authoritative.** `MCP_AGENT_PLANNER_ENABLED=false` by default. If enabled, Phase 0 planner returns JSON matching `ExecutionPlan`; harness rebuilds the graph from normalized plan decisions rather than executing arbitrary planner-provided node code. Final validators and publish gates cannot be bypassed by planner output. Added 2026-06-02.

- **Phase 2 deep-dive tools are explicit phase-only.** Core Phase 1/2/scorecard remain tool-disabled. Tools are allowed only for explicit phases: `phase1.investigate`, `phase2.deep_dive`, repair phases, `critic`, `consistency`, and normal Phase 3. Do not globally enable tools for `phase.startswith("phase2")`. Added 2026-06-02.

- **DAG Phase 2 tiering writes skeleton sidecars, then reuses mature analysis paths.** `analysis_mode="skeleton_plus_deep_dive"` writes `_analysis/_skeleton/{group}.json`; shallow groups use `_phase2_group_direct`, deep groups use `_phase2_group_agent` with `phase_name="phase2.deep_dive"`. Final Phase 2 validators still validate normal `_analysis/*.md` outputs. Added 2026-06-02.

- **Phase 3.6 consistency is a report-first gate.** DAG mode can run `_phase36_consistency()` after docs and before final gates. It writes `_assessment/consistency_report.json` and currently checks inventory-count conflicts and source-like unit drift. It does not wholesale regenerate docs. Added 2026-06-02.

- **Smart repair root-cause classification is deterministic and flagged.** `MCP_AGENT_SMART_REPAIR_ENABLED=false` by default. When enabled, Phase 2/3 targeted repairs write root-cause reports under `_assessment/repair/` and include category/reason lines in repair prompts. Categories: coverage, provenance, depth, contradiction, format, scanner_limited. Added 2026-06-02.

- **`MCP_AGENT_PHASE1_AGENTIC=true` enables `ChunkManifestInvestigator`. Now on by default in docker-compose.** Three extraction safeguards: (1) safety net force-adds chunks for units the model missed, (2) extraction batched at `agent_phase1_batch_size=20` units/call run in parallel, (3) manifest includes `identifiers` (SQL table names, class names, connection names, URLs) from `_extract_chunk_identifiers` so the model can reason about specific named entities. The manifest field was renamed from `keyword_hints` to `categories` + `identifiers`.

- **`PluginRunner.run()` constructs its own `AgentResult` from scratch — it drops any fields the harness set.** `required_docs` on `AgentResult` must be captured in `_run_harness_step` and stored on the runner instance, then forwarded in the final `AgentResult`. If you add new fields to `AgentResult` that the downstream needs, check `plugin_runner.py` — it won't automatically forward them.

- **RAG push has two code paths that both need `extra_doc_names`: post-generation push in `_run_generate_background` and the manual `/push-to-rag` endpoint.** The post-generation path uses `result.get("required_docs")` from the `AgentResult`. The `/push-to-rag` endpoint auto-detects non-numbered, non-underscore `.md` files from `docs/` directly — no dependency on the result chain. Both use `_push_with_retry(..., extra_doc_names=...)` which threads into `push_docs(..., extra_doc_names=...)` which threads into `_read_md_files(..., extra_filenames=...)`.

- **Phase 1 early stop pre-scans ALL chunks upfront using `_units_for_chunk`.** This adds one file read + regex pass per chunk before the LLM loop, which is fast (<50ms for 18 chunks). The pre-scan builds `all_assigned` (union of units reachable from any chunk) — the early stop target. If a unit never appears in any chunk's `_units_for_chunk` result, it will never be covered no matter how many chunks are processed; the early stop ignores it rather than blocking forever.

- **Agentic investigation (items 4–5) scaffolding is already in the codebase but disconnected.** `StagingToolbox`, `ALLOWED_ACTIONS = {"read_file", "search_text", "list_dir", "finish"}`, and `InvestigatorLimits.max_rounds = 18` are all declared in `investigator.py` but unused in the live `SingleShotInvestigator` path. `ChunkManifestInvestigator` is the live implementation added 2026-06-01.

- **`HarnessHelpersImpl` is the only place outside `harness.py` that calls private provider methods.** This is intentional: the `HarnessHelpers` Protocol exposes a clean testable interface; `HarnessHelpersImpl` delegates to `self._p._private_method()` directly. This is the approved pattern — do not add provider private-method calls anywhere else. Added 2026-06-03.

- **Phase bodies still live in `harness.py` even after the Phase interface refactor.** `_phase2_analysis()`, `_phase2_group_agent()`, `_phase3_docs()`, targeted repair methods, etc. are shared infrastructure called by multiple phase classes through `HarnessHelpers`. They were not moved to individual phase files. `harness.py` is 4,819 lines post-refactor; `pipeline/phases/` contains orchestration logic, not implementation. Do not try to move the helper methods — they are dependencies shared across phases. Added 2026-06-03.

- **`string.Template.safe_substitute` not Jinja2 for prompt templates.** Prompt text contains many literal `{` and `}` characters (code blocks, JSON examples, markdown tables). Jinja2 `{{ }}` delimiters would require `{% raw %}` escaping throughout. `${UPPER_SNAKE_CASE}` never appears naturally in prompts. `safe_substitute` leaves unknown `${...}` tokens as-is rather than raising — useful during development. Added 2026-06-03.

- **`Phase.estimate()` is designed to work with `helpers=None`.** The dry-run estimate endpoint constructs Phase objects with `helpers=None` because `estimate()` is purely deterministic and never calls helpers. This is the intended usage. Do not add a None-guard that raises — it would break the estimate endpoint. Added 2026-06-03.

- **`load_doc_catalog()` is a module-level singleton — tests must pass a `path` arg to override.** The singleton caches on first call. If a test modifies doc catalog state and does not pass a custom `path`, it will see the production catalog. Use `load_doc_catalog(path=Path("...test_catalog.yaml"))` in tests; the function bypasses the singleton when `path` is provided. Added 2026-06-03.

- **`EvidenceTraceabilityValidator` is non-blocking by design.** Phantom absence detections are warnings + JSON artifacts only — they do not trigger repair or fail the run. This is intentional for initial rollout: the regex window approach may have edge-case false positives on some projects. Promote to blocking only after confirming low false-positive rate on real projects. Added 2026-06-03.

- **`EvidenceQualityGate.should_skip()` always returns False — it reads `_preprocess/`, not `_analysis/`.** Phase28Bundles writes to `_analysis/`; `_preprocess/{group}.md` files are written by harness setup before the pipeline runs. The gate's thin-check is valid and independent of whether Phase28Bundles was skipped. Do not add a coordination check with Phase28Bundles' doc_bundles flag — it is not needed. Added 2026-06-09.

- **Form-only Phase 2 validation findings must not trigger LLM repair.** Missing `- COVERED:` markers, byte-count depth warnings, and missing `GROUP_SUMMARY` headings are repair-plan findings first. `repair_planner.py` may deterministically append covered markers only when the unit already appears in the group analysis, and may add a structural `GROUP_SUMMARY` shell. It must not create deterministic shells for substantive Talend guide sections; those stay blocking so missing content is visible. Added 2026-06-10.

- **Phase 3 targeted repair should prefer evidence-retention packets over legacy broad repair.** When an `evidence_retention.json` report exists, `_phase3_targeted_repair` builds a source-grounded repair packet and sends bounded direct context capped at 50k tokens. Do not fall back to broad staging context before trying the retention packet path. Empty or source-less packet plans should not run an LLM call. Added 2026-06-10.

- **Thin-bundle notice is prepended before Phase 3 validation gates.** In `phase3_docs.py`, the `_thin_bundle_notice` block runs immediately after `helpers.phase3_docs()` and before `validate_docs`. If validation subsequently fails and `phase3_targeted_repair` rewrites a doc, the notice is lost. This is a known, accepted limitation (thin evidence groups are unlikely to pass repair). Do not move the notice after repair without ensuring it re-applies post-repair. Added 2026-06-09.

- **`WarningCode` must be imported at top level in Phase files — not as a local import.** It is a lightweight `StrEnum` with no circular import risk. `phase1_investigation.py` and `phase2_analysis.py` both import it at top level; `evidence_quality_gate.py` was corrected to match during inspection. Any new Phase file that emits warnings must follow the same pattern. Added 2026-06-09.

- **`source_fact_ledger` JSON must be stable for identical scan artifacts.** The ledger is intended for retention/diff checks in later documentation-evidence slices, so repeated builds against the same scan must serialize identically. `generated_at` is derived from scan artifact mtime rather than wall-clock time. Do not reintroduce `datetime.now()` into the canonical ledger payload; put run-time timestamps in outer reports if needed. Added 2026-06-09.

- **`transient_artifacts.py` is the un-gated sibling of `debug_artifacts.py`; same module layout (constant + helpers) but no `if not config.pipeline_debug: return` branch.** `_transient/` holds in-pipeline throwaway files consumed in-process by the model call that produced them (merge inputs, synthesis inputs, repair bundles, scorecard-slice outputs, split-context chunks). The dir is deleted at Phase 4 alongside `_preprocess/` and `_analysis/`. No new config flags, no `tmp.replace` (throwaway; the model call is wrapped by retries). When adding a new transient write site: route through `transient_write(transient_dir(staging_dir, ...), content)` — never inline `mkdir` + `write_text` with a literal `staging_dir / "_transient" / ...` path. Added 2026-06-10.

- **`_audit/` is not a wholesale copy tree — it is two synthesized files only.** `enterprise_gates.json` (passed in as the `gate_report` param) and `final_doc_hashes.json` (SHA-256 of `staging/*.md`). The REST API fallback at `projects.py:786` tries `_assessment/enterprise_gates.json` first, then `_audit/enterprise_gates.json` — both must be present. `_audit/` is intentionally not in the Phase 4 rmtree list (`phase4_cleanup.py:76-79` deletes only `_preprocess`, `_analysis`, `_transient`) so the fallback survives the atomic swap. Adding a new wholesale copy to `_audit/` is a regression — the audit trail is `enterprise_gates.json` (gates) + `final_doc_hashes.json` (content hashes), not copies of intermediate artifacts. Added 2026-06-10.

- **The 9 root JSON maps deleted in Track A Slice 5 were never read by anything.** `entity_index`, `usage_index`, `dependency_map`, `architecture_map`, `data_flow_map`, `api_surface`, `known_risks`, `test_coverage_map`, `change_impact_matrix` — all data is in the `engineering_facts.json` aggregate (kept). Their `.md`/`.csv` siblings stayed (live scoped-tool corpus for `search_evidence`/`read_artifact`). If a future feature claims to need a per-artifact `.json` for any of these, the right answer is to read it from the aggregate, not re-introduce the per-artifact file. The live per-artifact JSONs are: `engineering_facts.json` (aggregate, SQL gate reader at `harness.py:4391-4394`), `sql_usage.json` (SQL classification at `harness.py:4332, 4379, 4408`), `config_runtime_map.json` (validator candidate list at `validators.py:1559-1564`, evidence bundle `_base_files` at `evidence_bundles.py:107`). Added 2026-06-10.

- **`_rag_support/` is the hidden RAG-support package — not audit output, not debug output, not a wholesale copy tree.** Built by `services/rag_support_package.py::build_rag_support_package()`, called from `Phase4Cleanup.run()` before the `_preprocess`/`_analysis`/`_transient` deletion loop; the atomic swap carries it into `docs/`. Contents are a positive allowlist: `phase2_analysis/{group}.md` for `state.facts_manifest.groups` only, plus `manifest.json` (path/source_path/source_layer/artifact_type/visibility/group/bytes/sha256 per artifact, `missing_groups` recorded — never synthesized). `_doc_pack_*`, `_bundle_*`, reports, and any unlisted artifact must never land there. `_rag_support/` is intentionally not in the Phase 4 rmtree list, and `RagBridge._read_md_files` ignores it (top-level glob only). `analysis_cache/` has exactly one writer — `replace_analysis_cache_from_rag_support()` post-swap in `_run_generate_background` — and one reader, `git_reingestion.setup_incremental_staging()`. An absent or empty package removes any stale cache and creates none: present-but-empty `analysis_cache/` would defeat the full-regen fallback check at `git_reingestion.py:215`. Slice 3 (internal-layer RAG ingestion) must reuse these path constants. Added 2026-06-12.

- **`_internal/` is the cross-codebase scoping prefix for internal RAG docs — it pairs two constants that must stay in sync.** `INTERNAL_DOC_PREFIX` exists in `mcp-server/src/mcp_server/services/rag_support_package.py` (builds filenames `_internal/{layer}/{percent-encoded-name}.md`) and in `RAG_pipeline/backend/src/api/routes/ingest.py` (the internal-lifecycle delete matches `Document.filename.startswith(f"{project_id}/_internal/", autoescape=True)` — autoescape is mandatory: `_` is a SQL LIKE wildcard and project ids contain underscores). Internal sync is wholesale delete-then-repush (`RagBridge.sync_internal_layers`): delete failure aborts the push so stale and fresh sets never mix; per-doc push failures continue and merge into `rag_errors`. Entity/usage CSVs go to the `entity_aliases` table via transactional per-project replace — never into the vector corpus. Default retrieval is protected by `source_layer` filtering (Slice 1), NOT by the filename prefix; the prefix only scopes the delete lifecycle and keeps internal docs out of canonical push/diff globs (top-level, digit-prefixed). Known gap: admin document-list endpoints show `_internal/` rows (follow-up). Added 2026-06-12.

- **`MCP_PIPELINE_DEBUG` is the single forensic-record gate; default off; **per-call `manifest.json` is always-on regardless.** Default runs (`MCP_PIPELINE_DEBUG=0`) write only the breadcrumb artifacts: per-call `_model_inputs/<phase>/<NNN>_<target>/manifest.json` and `_agent_tasks/<phase>/<target>/{status.json,output.md}`. Setting the flag to `1` materializes the rest under `docs_staging/_debug/...`: per-call `prompt.md`/`context_bundle.md`/`request.json`/`response.json`; agent-task `contract.json`/`validation_report.json`/`tool_call_ledger.json`/`provider_metrics.json`; gated `_assessment/` advisory reports (phase_costs, repair plans, consistency, reviewers, sql_usage_reconciliation/publication, scorecard_contract_errors, traceability); `_pipeline_log.md`; `provider_calls.json`; `index.json`; `evidence_registry.md`/`survival_report.json`; `_debug/_audit/provider_calls.json`. LLM input is byte-identical between flag-on and flag-off. The flag is the only place a forensic-record decision lives — no scattered `os.environ` reads, no per-call checks. Read the flag once via `config.pipeline_debug` and route through the helpers (`debug_write`, `debug_dir`). Added 2026-06-10 (Slice 2 implementation), confirmed in Track A Slice 5 doc updates.

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| xmlns declarations exempt from redaction | `CredentialFilter.filter_text` masks `xmlns[:prefix]="..."` values before any pattern runs and restores them verbatim; URL redaction honors `_SAFE_DOMAINS` like FQDN filtering | Namespace URIs are structural XML identifiers -- redacting them silently breaks every namespaced attribute lookup downstream (`xsi:type` -> tMap data lost for ALL jobs, `xmi:id` -> connection index degraded). A test had codified the broken behavior as acceptable; never assert that xmlns redaction "is fine" | 2026-07 |
| Talend scan emits latest item versions only | `TalendParser._latest_job_versions` keeps the highest numeric version per (path dir, name) per source dir; dropped count in `stats.superseded_job_versions` | Talend Studio exports retain every saved version (`Job_0.1.item`...`Job_3.3.item`); emitting all floods every downstream consumer with stale-logic evidence (VW export: 579 items vs 66 current). `active=False` stays reserved for genuinely disabled integrations (Lobster semantics -- `source_context.py` labels those INACTIVE, it does not drop them) | 2026-07 |
| MCP over CLI | Claude Code + MCP replaces CLI-based LLM analyzer | No API keys needed, better quality, parallel sub-agents | 2026-02 |
| Doc generation | 4-phase MCP workflow (Setup, Group Analysis, Write Docs, Publish) | Parallel sub-agents per group for throughput | 2026-02 |
| Staging directory | Claude writes to `docs_staging/`, server swaps to `docs/` on success | Protects existing docs from failed runs | 2026-02 |
| Resumable generation | On failure, staging is preserved; on resume, `_detect_resume_state()` inspects artifacts and `_manifest.json` tracks scan mtime for stale detection | Prevents loss of ~30 min work on rate limit failures | 2026-04 |
| RAG push server-side | `_run_generate_background` calls `RagBridge.push_docs()` | Claude prompt should NOT call `insert_to_rag` -- server handles it | 2026-02 |
| SSE streaming | Real-time log streaming from MCP server to dashboard | Dashboard shows live operation progress | 2026-02 |
| JWT auth | Shared JWT_SECRET_KEY across RAG backend, MCP server, dashboard | Stateless auth, works across all services | 2026-03 |
| Webhook auth | GitHub webhook uses HMAC-SHA256 (`X-GitHub-Signature-256`), NOT JWT | GitHub cannot send cookies; webhook router is registered without `require_auth` | 2026-04 |
| Analysis cache | After every successful generation, `docs/_analysis/` is copied to `output/{project_id}/analysis_cache/` | Enables incremental webhook regen: unchanged groups skip Phase 2 by restoring cached summaries into `docs_staging/_analysis/` before generation with `resume=True` | 2026-04 |
| Deploy user | Dedicated `intodoc` user on VPS | Security isolation for production deployment | 2026-03 |
| .agent folder pattern | AI Jason pattern: CLAUDE.md (rules) + .agent/ (system, SOPs, decisions, tasks) | CLAUDE.md stays concise (~100 lines), detailed docs are discoverable via README.md index | 2026-02 |
| Mission Control architecture | Push-based: customer MCP server calls `POST /ops/clients/{slug}/telemetry` on Mission Control; MC never calls out to customer VPS | Avoids inbound firewall rules on customer servers; sender already implemented in `mcp_server/services/ops_telemetry.py` | 2026-05 |
| Mission Control storage (MVP) | JSON file per client slug at `mission_control/snapshots/{slug}.json`, overwritten each push | No DB dependency for the initial slice; Postgres can replace it when the UI needs historical data | 2026-05 |
| mcp-server Dockerfile migration pattern | `CMD` runs `alembic upgrade head` before starting the server (same as RAG backend) | Without this, model changes land in code but never in the live DB — column-missing errors appear only at runtime, not build time | 2026-05 |
| `_fetch_rag_metrics` auth guard | Guard only on `rag_base_url`; auth header added conditionally when `ingest_api_key` non-empty | VPS instances commonly omit `MCP_INGEST_API_KEY`; RAG backend already handles open access — the MCP-side guard must not short-circuit on a missing key | 2026-05 |
| Docker-to-host communication | Use `host.docker.internal` (not `localhost`) when a Docker container must reach a service on the Mac host | `localhost` inside a container resolves to the container itself, not the host | 2026-05 |
| Tunnel for remote VPS testing | `cloudflared tunnel --url http://localhost:9100` gives a temporary public HTTPS URL; useful to prove remote push flow before deploying Mission Control permanently | URL changes each run -- not for production | 2026-05 |
| Dual server mode | `--mcp` (stdio) and `--serve` (HTTP) in same codebase | Share business logic between Claude Code and dashboard | 2026-02 |
| Scanner plugins | `platforms/<name>/` plugin architecture | Each platform self-contained with parser, prompts, best practices | 2026-02 |
| Chunk size | ~30K tokens (~120KB) per chunk | Fits Claude Code context with room for instructions; overhead added on top | 2026-02 |
| Build system | hatchling (NOT setuptools) | pyproject.toml configured for hatchling; setuptools causes build failures | 2026-02 |
| Hindsight memory | Cross-project + project-specific memory via recall/retain tools | Lessons from past runs inform future generation | 2026-03 |
| Polling + SSE | Dashboard uses both SSE log streaming and 10s polling | SSE alone is fragile; polling catches missed state changes | 2026-03 |
| Content-based discovery | Workato scanner uses JSON content classification instead of filename patterns | Works with any export format (flat suffix, wrapped, nested folder); single pass reuses walk_repo() output | 2026-03 |
| Workato generic extraction | All field reads use `or` fallback chains (e.g. `provider or application`); param extractors iterate all keys instead of hardcoded lists | Handles 3 known export formats (A=code blocks, B=flat, C=manifest/package) plus unknown future variations | 2026-03 |
| Bundle splitting | Prompt bundles split at 200KB, ref doc bundles at 80KB; glob patterns in orchestrator prompt | Prevents exceeding Claude's 256KB file-read limit and 25K token limit as platforms add more prompts | 2026-03 |
| Consolidated analysis guide | Single ANALYSIS_GUIDE.md per platform replaces 22 bundled .txt prompts; PHASE_3_GUIDE.md for cross-group synthesis | 94% token reduction (222KB->12KB), eliminates conflicting instructions, coherent single-document format; original .txt files kept for backward compat | 2026-03 |
| Transformation detail depth | Platform-aware preprocessing renders tmap_data/filter chains as structured tables; TRANSFORMATION_DETAIL section in analysis guides; per-job transformation subsections mandatory in Phase 3 | Closes the field-level documentation gap: users can now ask "what does column X do in job Y?" and get an answer from docs alone | 2026-04 |
| Dynamic depth scaling | Inject project metrics (total_jobs, total_groups) into prompt instead of hard-coded line minimums | Small projects don't pad, large projects don't truncate; depth driven by content not arbitrary targets | 2026-03 |
| Template DB storage | Templates, versions, profiles in PostgreSQL with Alembic migrations | Enables admin visibility, versioning with commit messages, A/B testing via profiles | 2026-03 |
| Template resolution cascade | Explicit profile -> project-scoped default -> platform default -> filesystem fallback | Flexible selection with permanent backward compat; filesystem always works as safety net | 2026-03 |
| Full-copy template variants | Profiles map template slots to specific versions (full copies, not section overrides) | Simpler to reason about; each version is self-contained and independently testable | 2026-03 |
| Per-platform template sets | Templates have optional `platform` field; profiles scoped to platform | Talend and Workato templates are independent; prevents cross-contamination | 2026-03 |
| Phase 2.6 scoring | New scoring sub-agent between Phase 2.5 gate and Phase 3 doc writing | Quantitative grounding for docs; enables comparison across runs; scorecard included in final output | 2026-03 |
| Template snapshots on runs | `template_snapshot` JSONB on GenerateRun records `{slug: version_id}` | Exact reproducibility; enables meaningful A/B comparison between runs using different profiles | 2026-03 |
| Phase 2.8 analysis bundling | Deterministic Python script bundles analysis per Phase 3 agent; replaces agent-side grep/head/tail | Large projects (>400KB analysis) can't fit in 200K context; server-side section selection is reliable, agent-side grep is not | 2026-04 |
| Depth tier scaling | small (<50 jobs, 0.6x), medium (50-199, 1.0x), large (200+, 1.5x) minimum line targets | Large projects need proportionally deeper docs; prevents the paradox of more jobs producing less content | 2026-04 |
| 4-agent Phase 3 split | Projects >=200 jobs use 4 agents (Core, Business+Ops, Patterns, User) instead of 3 | Fewer docs per agent = more output headroom per doc; Patterns+User was overloaded with 5 docs at large scale | 2026-04 |
| Custom provider investigator loop | API-model custom harness now runs a Python-owned JSON action loop before Phase 2 | Gets Claude-Code-like investigation without Claude CLI or LangChain in production path; model can request read/search/list, but Python controls permissions, validation, artifact writes, and phase gates | 2026-04 |
| nginx upstream DNS caching | nginx resolves hostnames once at startup and caches IPs; restarting upstream containers causes 502s until dashboard is also restarted. Fix: `resolver 127.0.0.11 valid=10s ipv6=off` + `set $var http://host:port` in every `proxy_pass` location. Without the variable, nginx ignores the resolver. | 2026-05 |
| Pack phase policy backward compat | `_pack_contract_mode` (all phases skip) is now DERIVED from `_phase_policy()`. Existing packs with `evidence_profiles` and no `phases` block default to all-skip. New packs can declare `phases` to override. Never check `_pack_contract_mode` for individual phase decisions — always use `_phase_policy(phase)`. | 2026-05 |
| Investigation preamble is prepended, bounded phase2.md is full replacement | `investigation.md` in pack prompts is PREPENDED before the base JSON extraction prompt (units, JSON schema, rules are still added). `phase2.md` in bounded mode is used as the COMPLETE prompt — no enterprise sections are appended. These are different behaviours for different prompt files. | 2026-05 |
| `_units_for_chunk` word-boundary rule | Unit names are matched with `\bname\b` to avoid assigning "Order" to chunks that only contain `using Types.Order;`. Units with non-word chars in name (e.g. "Global.asax") fall back to substring. Still possible to get false positives from `.Name` patterns — auto-skip on max retries is the safety net. | 2026-05 |
| Investigator max-retry auto-skip | After 3 failed attempts, `SingleShotInvestigator` writes remaining missing units to `skipped_units` with reason "not evidenced in chunk after maximum retries" and returns a valid report instead of raising. This replaces the previous hard raise that the harness caught as a WARNING. The coverage gap is visible in the investigation artifact. | 2026-05 |
| Pack Phase 3 investigation bundle is filtered and capped | `_build_pack_investigation_bundle()` reads all `_investigation/*.md` files, keeps only evidence items whose category matches the doc's evidence profile via `_INV_CATEGORY_MAP`, and caps at 40k chars. The bundle is written to `_investigation/_bundles/{doc_name}.md`. If no relevant items exist, returns None (no file added to context). | 2026-05 |
| Two-provider doc gen design | Only `claude-cli` and `custom harness` providers; orchestrator.py + tools.py deleted | CLI prompt IS orchestration; harness uses per-call focused prompts. They share the same `ANALYSIS_GUIDE.md` content (not the same prompts). `DocGenContext` holds only shared fields; cli-only computations live in `_build_prompt()`. | 2026-04 |
| Analysis guide not stripped for harness | `_adapt_legacy_text()` removed from `prompt_packs.py`; guide content passes through unmodified | The guide is about semantic extraction targets, not orchestration — stripping it degraded harness output quality silently. Full guide now reaches harness model calls via `_analysis_prompt_pack.md`. | 2026-04 |
| Config-driven generation constants | All bundle limits, QS thresholds, token caps, and RAG timeouts are Pydantic Settings fields (MCP_ prefix) | Eliminates magic numbers scattered across 4 files; operator can tune via env vars without code changes | 2026-04 |
| RAG push filter: top-level NN_*.md only | `_read_md_files` uses `glob("*.md")` + digit-prefix filter, not `rglob("**/*.md")` | `_assessment/`, `_analysis/`, `_investigation/` subdirs contain system prompts and debug output that must not appear in user query results | 2026-04 |
| Doc quality scorer scales with unit_count | `_scaled_min_lines(doc_key, unit_count)` applies sqrt(unit_count/50) clamped [0.3x, 2.0x] | Static thresholds calibrated for 500-job Talend project incorrectly graded 17-recipe Workato project as failing; scoring must be relative to project size | 2026-04 |
| phase3_max_tokens = 32000 | deepseek-v4-pro (and kimi-k2.6) support 32K output tokens; config default raised from 10000 | Old 10000 cap truncated docs mid-sentence — root cause of quality score 2.38/5.0 on plsss | 2026-04 |
| context_max_tokens pre-flight guard | Estimate tokens before every API call; raise at 900K, warn at 85% | Prevents silent context overflows that waste the full API cost and produce no output; configurable via MCP_CONTEXT_MAX_TOKENS | 2026-04 |
| DATA_FLOWS CSV-only (no JSON base) | 03_DATA_FLOWS_LINEAGE overrides base names list to CSV versions only | entity_index + usage_index were loaded twice (JSON base + CSV extra = ~925 KB duplicate); CSVs are more readable for LLMs and cover the same data | 2026-04 |
| Phase B: native tool-use loop | list_artifacts/read_artifact tools on OpenAICompatibleProvider + AnthropicProvider; Ollama keeps single-shot fallback; supports_tools flag routes call sites in harness.py | Model pulls context on demand instead of Python front-loading; recovers from non-standard filenames; context_bytes=0 for tool-use paths eliminates context overflow risk | 2026-04 |
| DeepSeek multi-turn reasoning_content | complete_with_tools() echoes reasoning_content back in assistant messages when present | DeepSeek thinking mode returns reasoning_content in every response; multi-turn loops must include it in history or API returns 400. Single-shot complete() unaffected. No-op for non-thinking models. | 2026-04 |
| Platform-agnostic provider requirement | No Claude CLI dependency; all features must work with deepseek/kimi/Anthropic/Ollama swappable at runtime | Anthropic partnership (token resale) requires Claude support but architecture must not be Claude-only; tool-use implementation uses OpenAI function-calling format which all three cloud providers support | 2026-04 |
| Fallback model default = None | `doc_generation_fallback_model` defaults to `None`, not a model string | `"openai/gpt-5.4"` (the previous default) doesn't exist — every fallback caused a 404. Operators opt in explicitly via `MCP_DOC_GENERATION_FALLBACK_MODEL`. | 2026-05 |
| REQUIRED_SECTION_NAMES are Talend-only | Section validation (P4.2) skipped for non-Talend platforms | COMPONENTS, EXECUTION_FLOW, DATA_FLOWS, TRANSFORMATION_DETAIL are Talend concepts. Applying them to Workato/Generic would break validation. `group_analysis_is_complete()` already gates on `platform == "talend"`. | 2026-05 |
| json_mode is set by the investigator, not by prefill | `ModelOptions.json_mode=True` is passed from `SingleShotInvestigator.run()`, not from the prefill proxy | prefill="{" and json_mode are complementary: prefill forces the model to start a JSON object, json_mode tells the provider to enforce JSON at the API level. Both are set. | 2026-05 |
| LockHeartbeat wraps only provider.run() | `async with LockHeartbeat(staging_dir, run_id=run_id):` is placed around the single `provider.run(ctx, _log_cb)` call, not around the full generation function | The lock is acquired before the heartbeat starts and released in the `finally` block after the heartbeat exits. Heartbeat start and lock acquire are coordinated via this placement. | 2026-05 |
| check_provider_credentials() never hard-exits | Returns a list of warnings, never raises or exits the process | Operators may intentionally run with only some providers configured (e.g. only Anthropic). Hard exit belongs only in `validate_production_secrets()` for auth/ingest keys, not API keys. | 2026-05 |
| CSRF exemption uses path prefix, not exact match | `_EXEMPT_PREFIXES` checked with `str.startswith()` in RAG backend `csrf.py`; `_EXEMPT_PATHS` uses exact set membership | Ingest routes have project-specific suffixes (`/api/v1/ingest/md`, etc.) — exact match would miss them. Internal routes are behind Docker private network so path-prefix bypass adds no meaningful attack surface. | 2026-05 |
| FailureKind.INCOMPLETE is not retryable_with_fallback | `is_retryable_with_fallback` excludes INCOMPLETE alongside CONTEXT_TOO_LARGE and BUDGET_EXCEEDED | Force-conclude (same provider, no tools) is attempted first on INCOMPLETE; if that also fails, the run is marked incomplete. Falling back to a different provider on truncation is not useful — the other provider would face the same context. | 2026-05 |
| status.json is the acceptance gate for AgentTaskRuntime resume | `_atomic_write(task_dir / "status.json", ...)` is called last in `run()` | On resume, `accepted_output()` reads status.json first; if it's absent or corrupt, the task re-runs. Writing it last means a crash before status.json is committed forces a safe re-run rather than a phantom acceptance. | 2026-05 |
| _classify_failure() must check metadata before returning UNKNOWN | finish_reason/incomplete_reason keys are in result.metadata, not in raw_error | Providers set finish_reason in metadata (e.g. `{"finish_reason": "length"}`); raw_error contains the SDK exception text which may not mention tokens. Checking only raw_error missed all length-truncation failures, classifying them as UNKNOWN (retryable). | 2026-05 |
| phase2_max_tokens default is 32K | DeepSeek needed ~17K output tokens on a 2-job group; 16K cap caused silent truncation and fallback | Always raise output token limits proportionally when adding a new provider. The cap applies globally to all providers via opts.max_tokens. | 2026-05 |
| Enterprise mode is the default publish path | `GenerateRequest.mode` defaults to `enterprise`; `draft` must be explicit | Client-ready docs must be source-backed, redacted, deep, current, indexed, auditable, and useful. Cheaper/looser behavior is allowed only as draft output. | 2026-05 |
| Phase 3 doc depth is blocking | `validate_doc_depth()` failure now raises in the custom harness | Shallow docs with correct unit mentions are not acceptable for paid client delivery. Repair/resume should happen in `docs_staging/`, not by publishing partial docs. | 2026-05 |
| Enterprise run verdicts are persisted | `GenerateRun.run_verdict` stores `draft`, `failed`, `published`, `published_with_rag_error`, or `client_ready`; `gate_results` stores gate JSON | Dashboard/API consumers need one structured outcome instead of inferring readiness from status, doc_count, RAG timestamps, and quality score. | 2026-05 |
| Scanner output fails closed | `ScannerService.run_scan()` clears old scan artifacts and requires a valid new `scan_result.json` after subprocess success | Stale scan artifacts can make docs current-looking but factually stale. Missing/invalid scan output must block generation rather than reuse old facts. | 2026-05 |
| generate flow does NOT update last_rag_push_at | `_run_generate_background` calls `push_docs()` but never `registry.update_rag_push()`. Only the explicit "Push to RAG" endpoint (projects.py:1075) and the MCP tool (mcp/server.py:511) set the timestamp. | Dashboard "LAST RAG PUSH" shows Never even when docs are in RAG after generation. Fix: add `update_rag_push()` call in `_run_generate_background` after a successful push. | 2026-05 |
| Token-injected remote URL is never cleaned up | Both `_fetch_remote` (git_poller.py) and `_run_diff_patch` (git_diff_patcher.py) inject PAT into the remote URL via `remote set-url` before git fetch but do not reset it afterward (pass or fail). | Token persists in `.git/config` and appears in journal output on any future git error. Fix: wrap inject+fetch in try/finally that resets URL to clean version. | 2026-05 |
| git fetch in systemd fails without GIT_TERMINAL_PROMPT=0 | `stdin=DEVNULL` blocks stdin-based credential prompts but a host credential helper can still open `/dev/tty` directly. Neither the service file nor the subprocess env sets GIT_TERMINAL_PROMPT or GIT_ASKPASS. | In a no-TTY systemd context, any host-level credential helper causes git fetch to abort with "could not read Password". Fix: set `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=echo` in the service unit and/or subprocess env. | 2026-05 |
| `required_outputs` validation is structural, not text-presence | `_required_output_present()` in `validators.py` requires the output name to appear as a markdown section heading (with content below it) or a table-column header. Bare text occurrence in prose does not satisfy the gate. | An output like `table_inventory` declared in `plugin.yaml` must appear as `## Table Inventory` with content, not just mentioned in passing. The helper splits on `_`/`-`/` ` and checks all words against heading/cell tokens. | 2026-05 |
| `EvidenceBundleBuilder._snippets_for` uses layered extraction | Order: (1) JSON files → `_json_snippets` (parsed object walk); (2) SQL/DDL categories → complete fenced code blocks; (3) all categories → complete markdown sections; (4) 5-line context window fallback (`_line_snippets`). `engineering_facts.json` and `config_runtime_map.json` added to `_base_files`. | Line-scanner approach split SQL queries and JSON objects mid-block. Complete code blocks and sections preserve the entire evidence unit, which is what the LLM needs for accurate extraction. | 2026-05 |
| `09_EXECUTIVE_SUMMARY` is enterprise-only and dispatched last | Filtered from `self._required_docs` in `run()` when `not ctx.enterprise_mode or self._plugin_mode`. Its `_phase3_context_files()` returns `scorecard.md + completed docs` (not `_preprocess/`). Dispatched after all other Phase 3 docs complete, not in the parallel batch. | Context is the finished docs — batching it with other docs would read empty files. Non-enterprise runs must not be blocked by this doc. | 2026-05 |
| Single-phase rerun uses artifact invalidation + resume, not direct phase dispatch | `POST /generate-runs/{id}/rerun` deletes `scorecard.md` or `{doc}.md` + the agent task `status.json`, then calls `_run_generate_background(resume=True)`. Resume skips phases that have accepted artifacts. | Building a direct phase-level harness entrypoint would require duplicating DocGenContext construction and provider setup (~200 lines). Artifact invalidation + resume achieves the same result with zero new harness code. | 2026-05 |
| `ToolPolicy.scorecard` / `phase2.6` are explicitly disabled for tools | `_tool_policy()` returns `enabled=False` for `phase in {"scorecard", "phase2.6"}` even when the provider supports tools. Previously these phases would have received tool-enabled calls (latent bug). | Scorecard and Phase 2.6 use bounded context files only — tool browsing would allow the model to pull in unreliable extra context that bypasses the curated evidence bundle. | 2026-05 |
| `_pipeline_log.md` is written incrementally, not just at the end | `_PipelineLog.flush()` is called after Phase 1, Phase 2, Phase 2.6, and Phase 3 complete, and on exception before the error return. | Operators watching a long run need visibility before it finishes. Writing only at the end would produce no log for runs that hang or crash mid-phase. | 2026-05 |
| Dynamic DAG harness mode | Flagged DAG executor foundation with validated planner/fallback graph; existing phase implementations remain the execution substrate | Gives planner-driven control flow and persisted graph telemetry without risking arbitrary node execution or skipped final gates. | 2026-06 |
| Talend ESB/Camel classification uses prefix regex, not a component allowlist | `classify_esb_components()` in `intodoc/platforms/talend/parser.py` matches `^c[A-Z]` against component type names; the prior `_ESB_COMPONENT_TYPES` hardcoded frozenset (~25 names) was removed | A real customer route (`cHttp` connector) was found to be missed by the allowlist — Talend's Camel palette is larger than any list we'd maintain, and every DI-job component in this codebase is t-prefixed, so the c/t prefix split is itself the reliable signal, not a curated subset of it. If ESB detection ever needs per-component EIP-role categorization again, re-derive it from the matched prefix list rather than reintroducing a hardcoded allowlist. | 2026-07 |
| Talend job discovery walks both `process/` and `routes/` | `_parse_all_jobs`/`_find_talend_root` in `intodoc/platforms/talend/parser.py` iterate `_JOB_SOURCE_DIRS = ("process", "routes")`; previously only `process/` was walked | A real Talend ESB Studio export puts Route Designs in a separate top-level `routes/` directory, sibling to `process/` (Job Designs) — this is a genuine repository-layout difference, not just an `.item`/XMI format quirk. Routes in that layout were completely invisible to the scanner (never parsed, not just misclassified) until this fix. If any future Talend directory-structure assumption is added, check both trees, not just `process/`. | 2026-07 |

## Code Patterns

### Python (Scanner + MCP Server)
- Python 3.11+, type hints everywhere
- Pydantic v2 for data models and settings
- Click for CLI entry points
- async/await for I/O (FastAPI routes, DB queries)
- Never bare except -- always catch specific exceptions
- hatchling build system (NOT setuptools)

### Dashboard (React)
- React 19 + Vite 6 + TypeScript 5 strict
- Tailwind CSS 3 for styling
- Custom hooks: `useProjects()`, `useLogStream()`, `useResetStatus()`, `useTemplates()`, `useProfiles()`
- API client pattern: centralized `client.ts` with typed fetch wrappers
- credentials:"include" on all fetch calls (JWT cookies)
- 401 response -> redirect to login page
- Top-level nav: Projects / Templates / Profiles / Docs
- Template editor: inline textarea with version selector + required commit messages
- Profile dropdown on Generate button for template swapping

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Phase 0: Cleanup | 2026-02 | Repository restructuring, knowledge base |
| Phase 1: Analysis passes | 2026-02 | 21 passes (p01-p21), 32 templates, 25 reference docs |
| Phase 2: Universal IR | 2026-02 | 99.4% expression parse rate |
| MCP server v1 | 2026-02 | 8 MCP tools, REST API, dual mode |
| Dashboard v1 | 2026-02 | Project list, actions, log panel |
| Doc generation pipeline | 2026-02 | 4-phase workflow, parallel sub-agents, staging dir |
| Template management system | 2026-03-24 | DB-backed templates, versioning, profiles, A/B comparison, dashboard UI |
| Scoring pipeline (Phase 2.6) | 2026-03-24 | 8-dimension rubric, anti-patterns, red flags, security checklist, scorecard output |
| Custom investigator loop | 2026-04-25 | `providers/custom/investigator.py`; JSON actions (`read_file`, `search_text`, `list_dir`, `finish`), validated `_investigation/` artifacts, Phase 2/3 evidence threading, critic/expander pass |
| P2/P3/P4 harness reliability | 2026-05-20 | Targeted repair loop, provider contract assertions, json_mode, LockHeartbeat, startup orphaned-run reconciler, credential preflight, word-boundary coverage; 336 tests |
| Enterprise documentation gates | 2026-05-20 | Enterprise default mode, blocking Phase 3 depth, final redaction/provenance/usefulness/reviewer/quality gates, audit bundle, structured run verdicts, stale scan cleanup; 350 backend tests |
| Flagged dynamic DAG foundation | 2026-06-02 | `ExecutionPlan`/`ExecutionGraph`, deterministic fallback, optional LLM planner, tiered Phase 2 skeleton/deep-dive, consistency report, DAG event ledgers; 205 focused tests |
| Documentation restructuring | 2026-02-19 | .agent pattern, SOPs, system docs |
| Fix stuck status | 2026-03-03 | Reset endpoint, polling fallback, reset button |
| JWT auth integration | 2026-03-04 | Shared JWT across all services |
| Doc gen run: TESTClAUDE | 2026-02-20 | 25 docs generated, 24/25 pushed to RAG |

## Lessons Learned / Gotchas

### Evidence integrity invariants (2026-07-14 rearchitecture)
- Phase 2's unit of work IS the unit of evidence: a job's `## Job:` section is never split across tasks, and a task's evidence header lists exactly the units it physically contains. Never reintroduce substring-based "visible units" computation -- group-wide inventories mention every job name in every text window, which is how chunk agents were tricked into fabricating analyses.
- Python assembles Phase 2 group analyses by verbatim section selection with per-task provenance; it must never paraphrase or synthesize analysis prose (repo rule). The only LLM call after per-task analysis is the GROUP_SUMMARY/CROSS_JOB_FINDINGS synthesis.
- Resume identity: single-task groups keep `target_id = group`, so most caches survive the rearchitecture; old `{group}_chunk_N` caches miss once and re-run.
- Phase 3 technical facts flow through deterministic per-unit evidence bundles (`target_evidence.py`), NOT through Phase 2 narration; Phase 2 output is the interpretation layer only. Bundles trim derived niceties first and never silently cut verbatim code/SQL.
- Fabrication validators (component-echo, no-unavailability) act at TASK-ACCEPTANCE level feeding the repair ladder; the final publish gates stay advisory per the June 2026 decision. Keep that separation.
- Redaction defense-in-depth: the post-scan `redaction_verifier` gate at the CLI `scan` command is the single choke point (mcp-server shells out to it). If a new output path writes scan artifacts, it MUST pass the gate too. Sensitivity matching is token-based (camelCase-aware) -- never regress to underscore-suffix matching.
- The per-job `ssl=require` differences in `config_runtime_map` are GENUINE per-.item context snapshots in the customer source (verified against GetLocation/GetWorkOrder/GetLocations 3.1 items), not an aggregator bug. Docs should state context provenance (job-embedded vs shared group vs Default) instead of "fixing" the data.

### Formatting
- Windows cp1252 encoding breaks Unicode -- always use ASCII in all output (chars 32-126)
- No em-dashes, smart quotes, or Unicode arrows -- use `--`, `'`, `->` instead
- All tables must use plain ASCII pipe characters

### Content
- Never hardcode group names -- use `get_chunks()` return values
- Never assume specific region names -- they vary per project
- Every document MUST have a table of contents
- Business rules tagged as BR-{GROUP}-NNN
- Quantify everything: "75 jobs" not "many jobs"
- Do NOT invent data -- every claim must trace to a group summary or manifest
- **Per-job purpose lines live in `02_ARCHITECTURE_INVENTORY.md` as `#### {JobName}` followed by a `**Purpose**: ...` paragraph.** Do NOT substring-grep paragraphs for the job name -- the Purpose paragraph does NOT repeat the job name, so a naive grep matches the heading itself (too short, gets discarded) or an unrelated deeper section. Always parse the heading boundary and take the next paragraph that starts with `**Purpose**`. See `_extract_job_purpose_from_inventory` in `routes/projects.py`.
- The Talend per-job JSON `purpose` field from the scanner is always empty -- the only source of real, brief job descriptions is the markdown `**Purpose**:` line written by Claude during doc generation.

### Process
- Background sub-agents may return empty output files. Use `TaskOutput` or `resume` to retrieve results. Plan for fallback.
- Doc generation uses staging dir (`docs_staging/`). Server swaps to `docs/` on success, pushes to RAG automatically.
- Do NOT add `insert_to_rag` to the Claude Code prompt -- RAG push is server-side.
- Enterprise generation must never publish to `docs/` unless all gates pass. Failed enterprise runs preserve `docs_staging/` for repair/resume; treat that directory as work-in-progress, not client-ready output.
- Always check `_assessment/enterprise_gates.json` and `generate_runs.run_verdict` before calling generated docs client-ready. `status="completed"` alone is not enough if the verdict is `draft`, `published`, or `published_with_rag_error`.
- After adding or modifying scan behavior, verify stale scan cleanup. A scan subprocess exit code 0 without a fresh valid `scan_result.json` must remain a failure.
- Custom API provider has its own investigator loop now. The model never writes files directly in that phase. It returns JSON actions only; Python executes read-only tools inside `docs_staging/`, validates evidence, and writes `_investigation/{group}.json` + `.md`. If investigation coverage fails, Phase 2 must not start.
- **`.agent/` docs drift silently.** CLAUDE.md and PROJECT_MEMORY get updated when features ship, but `.agent/system/` and `.agent/SOP/` are often forgotten. When working in this codebase after a significant feature (new tools, platforms, dashboard components), run a quick audit of affected `.agent/` files in the same commit -- not retroactively.
- Very large docs (e.g. 17_DATA_QUALITY.md with 1,102 embedded rules) may fail RAG ingestion with 500 errors. Consider splitting.
- SSE-only UI updates are fragile -- always pair with polling as a fallback.
- Stuck states need an explicit user escape hatch (reset button) since disabling all actions locks the user out.

### Custom Provider Testing
- Focused regression command for DAG/planner custom-provider changes:
  ```bash
  cd knowledge_base/mcp-server
  uv run pytest tests/test_providers/test_custom_provider.py tests/test_services/test_pack_loader.py tests/test_services/test_plugin_runner.py -q
  ```
- Focused regression command for the custom provider:
  ```bash
  cd knowledge_base/mcp-server
  PYTHONDONTWRITEBYTECODE=1 pytest tests/test_providers/test_custom_provider.py
  ```
- Lint command for the touched custom provider code:
  ```bash
  cd knowledge_base/mcp-server
  ruff check src/mcp_server/providers/custom tests/test_providers/test_custom_provider.py
  ```
- Runtime smoke test for the investigator feature:
  1. Configure `MCP_DOC_GENERATION_PROVIDER=custom` and a real API model (`moonshot/kimi-k2.6`, `openai/<model>`, or another supported `provider/model` string).
  2. Generate docs for a small scanned project.
  3. Watch logs for `[custom] Investigation complete` and `[custom] Investigation coverage gate passed`.
  4. During staging, inspect `_investigation/*.json` and `_investigation/*.md` for exact canonical unit names, source files, snippets, and categories.
  5. After success, confirm `provider_calls.json` includes phase `investigation` entries. `_preprocess/` and `_analysis/` are cleaned; `_investigation/` is kept as audit evidence.
- Full `pytest` in `mcp-server` currently has an unrelated SQLite fixture blocker: PostgreSQL `JSONB` column `generate_runs.template_snapshot` cannot compile under SQLite. The provider test file passes independently.

### Scanner
- Workato file discovery uses content-based classification (`_classify_json_content`), NOT filename patterns
- The asset registry is built once from `walk_repo()` output -- no extra filesystem walk, no rglob
- Three Workato export formats exist: flat suffix (`.recipe.json`), wrapped (`{"recipe": {...}}`), nested folder (`recipes/name/recipe.json` + `steps.json`)
- Steps can live in a sibling `steps.json` file -- `_parse_recipe()` auto-merges them
- When recipe filename is generic (e.g. `recipe.json`), use parent directory name as recipe name

### Build & Tooling
- intodoc uses hatchling build system, NOT setuptools
- Legacy CLI analyzer default $5 budget; can be exceeded with large projects
- tMap distiller must strip noise BEFORE chunking to avoid token bloat
- ENDPOINT/URL parameters are semantic -- never strip them
- Connection matching uses REPOSITORY_PROPERTY_TYPE refs, not name heuristics

### Deployment / Systemd

- **EP-001 canonical deployment path is nginx/certbot, not Caddy.** As of 2026-05-05, EP-001 is generic Deployment Foundation. Use `deploy/deploy-client.sh`, `deploy/nginx/nexus-client.conf.template`, `deploy/mcp-server@.service`, `deploy/RUNBOOK.md`, and `deploy/SMOKE-TEST.md` for new deployment work. Caddy files remain legacy/deferred and should not be used as EP-001 acceptance unless a future story explicitly revives them.
- **Six Docker services plus host MCP API are the deployment health boundary.** Docker Compose covers `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, and `dashboard`. MCP API is required but runs via systemd (`mcp-server@<client>.service`), so runbooks and smoke tests must verify both `docker compose ps` and `/mcp/health`.
- **EP-001 is not accepted by docs alone.** Backlog/docs are aligned, but final acceptance requires executing `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md` on a clean reference VPS, including HTTPS/security headers, login/logout, ingest, streaming query, dashboard, Ollama container access, and IP allowlisting.

- **Editing `deploy/mcp-server.service` in the repo does NOT update the installed unit.** The systemd unit at `/etc/systemd/system/mcp-server.service` must be re-copied + `systemctl daemon-reload` + `systemctl restart mcp-server` whenever the repo file changes (working directory, venv path, env file, etc.). On 2026-04-10 this caused the v2 checkout's MCP server to keep running stale code from a Mar-24 process for 2+ weeks; the topology endpoint `/mcp/projects/{id}/graph` returned FastAPI's default 404 because the route did not exist in the in-memory binary.
- **After adding a new Python dependency to `mcp-server/pyproject.toml`, you MUST `pip install`** it into the venv that systemd uses. Editable installs do NOT auto-install new deps -- they only re-link source files. The `ollama` package was added to v2 deps but never installed, crash-looping the service with `ModuleNotFoundError: No module named 'ollama'` after the unit was finally re-installed.
- **Canonical data location is now under `intodata-kb-v2/`**: `intodoc-output/` and `output/` live at `/home/intodata/intodata-kb-v2/knowledge_base/{intodoc-output,output}`. The old paths under `/home/intodata/intodata-kb/knowledge_base/` are now symlinks pointing at v2 as a temporary safety net.

### Topology graph endpoint

- **`/projects/{id}/graph` synthesizes `job_connections_used` / `connection_used_by` at request time when the manifest is missing `job_external_systems`.** This is the legacy-scan fallback path in `_derive_systems_from_job_json` -> `_fallback_talend_system` (`mcp-server/src/mcp_server/api/routes/projects.py`). Modern scans populate `job_external_systems` explicitly and bypass this path entirely. The Talend heuristic strips a leading `t` and trailing `Input/Output/Connection/...` suffixes from the component type to guess a system name -- so it MUST exclude every internal Talend component type that carries non-system payloads, otherwise it produces hub-shaped fake-system noise that wrecks topology rendering performance for legacy scans. Internal components to exclude: tMap, tRunJob, tPrejob, tPostjob, tWarn, tDie, tLog* (tLogRow, tLogCatcher), tStatCatcher, tFlowMeter, tFlowMeterCatcher, tChronometerStart, tChronometerStop, tAssert, tAssertCatcher, tJava*, tJavaRow, tJavaFlex, tFixedFlowInput, tFlowToIterate, tSetGlobalVar, tContextLoad, tRoute*, tUnite, tReplicate, tSortRow, tFilterRow, tAggregateRow, tFile* (Input/Output Delimited/Excel, List, Delete, Copy, Exist), tWriteJSONField, tExtract*Field, tSchemaComplianceCheck, tSleep, tLoop, tMsgBox, tBufferInput, tBufferOutput, tNormalize, tDenormalize, tIterateToFlow.
- **The `tRunJob` family wraps internal job-call wiring whose payload looks like `REFERENCE:sendToMonitoringMOM`, `REFERENCE:Trigger_Next_Interface_92`, etc.** When this leaks through the suffix-stripping path it becomes a synthetic "REFERENCE:..." system that fans out across the entire view (one per called job). Always filter `system.startswith("REFERENCE")` defensively in the Talend branch of `_derive_systems_from_job_json` AND keep the tRunJob family in `_FALLBACK_INTERNAL_COMPONENTS`. Cap the fallback's per-job result at 6 systems (`sorted(systems)[:6]`) -- modern scans bypass this so the cap only floors heuristic noise on legacy scans.
- **Symptoms when this gets out of hand**: scherpenhuizen-talend (legacy scan) was returning ~37 connection nodes and ~256 job-conn edges from a manifest that on disk had zero connection data; the resulting hub-shaped graph (Oracle 29 jobs, REFERENCE:sendToMonitoringMOM 27 jobs) made the topology view stutter on hover and on every direction/compact toggle. Round 3 of the topology perf pass (2026-04-10) cleaned this up.

### RBAC is duplicated across MCP server and RAG backend

- **The MCP server and the RAG backend each have their own RBAC code path against the same PostgreSQL tables (`users`, `user_roles`, `role_permissions`, `permissions`, `project_assignments`).** RAG backend uses SQLAlchemy via `get_user_project_ids()` in `RAG_pipeline/backend/src/rbac/permissions.py`; MCP server uses raw-SQL helpers in `mcp-server/src/mcp_server/api/routes/projects.py` (`_get_user_is_superadmin`, `_get_user_permission_codenames`, `_get_assigned_project_ids`) plus `require_project_access()` in `mcp-server/src/mcp_server/api/permissions.py`. **Any change to the permission model (new codename, new role, change to assignment semantics) must be made in BOTH services or the two surfaces will disagree.**
- **Routes that need RBAC on the MCP side are easy to miss.** Router-level `dependencies=[Depends(require_auth)]` only guarantees the user is authenticated, not that they can see the `project_id`. Every new route under `/projects/{project_id}/...` must either call `await require_project_access(request, project_id)` explicitly, or filter its output by user assignment. On 2026-04-20 three routes were found missing this: `/deep-dive/projects` returned every project, and `/projects/{id}/graph` + `/projects/{id}/jobs/{job_name}/detail` accepted any project_id from any authenticated user. Fixed by mirroring the `list_projects()` pattern for the list route and adding `require_project_access` to the two detail routes.

### Evidence document update/revert integrity (2026-07-12)

- **Approval targets an exact document version, not just a project and filename.** Read the current SHA before proposing an edit and require that SHA as `expected_base_sha` during publish; a mismatch is a conflict requiring a fresh proposal.
- **Filesystem publication and RAG indexing form one recoverable operation.** If indexing the updated document fails, restore the original bytes. If indexing a reverted document fails, restore the updated bytes. Never report success while the file and index disagree.
- **Revert is receipt-bound.** Validate project, filename, and before/after hashes from the update receipt before restoring content; do not expose a generic arbitrary-version write endpoint.

### Nexus-to-MCP onboarding and swap-pack boundary (2026-07-14)

- **ZIP source bytes terminate at MCP.** Validate compressed size, entry count, expanded size, paths, drives, and symlinks before atomic extraction. Agent workflows receive only a short-lived signed receipt plus safe filename/size/hash metadata; never checkpoint the archive or receipt signing secret.
- **IntoDoc swap-pack ownership remains in MCP.** Nexus may list safe pack metadata and request a project selection, but MCP persists `Project.plugin_pack_id` and generation resolves explicit per-run override before the project default.
- **Changing a project's documentation pack is a management operation.** Authentication plus read access is insufficient; require project ownership or superadmin at the MCP route even when the request arrived through Nexus.
- **`nexus_pack_ids` are an orchestration-policy snapshot, not an IntoDoc plugin-pack substitute.** Keep the two fields and responsibilities separate so application capabilities and generated document templates can evolve independently.

## Environment

- Python: 3.11+ (pyproject.toml), 3.14 on some dev machines
- Node: v24+ (dashboard)
- Build: hatchling (Python), Vite 6 (dashboard)
- Deployment: VPS with `intodoc` user, systemd services, nginx reverse proxy
- Ports: 8001 (MCP REST API), 5173 (dashboard dev)
- Docker: dashboard served via nginx in docker-compose

## File Structure Summary

```
knowledge_base/
  .agent/                           -- Agent context docs
    README.md                       -- Smart index ("when to read what")
    system/                         -- architecture.md, mcp-workflow.md, mcp-server.md, etc.
    sop/                            -- run-doc-generation.md, add-new-project.md, etc.
    decisions/                      -- 001-mcp-over-cli.md
    tasks/                          -- Phase FRDs
  .claude/                          -- Session tracking
    context.md                      -- Current state, blockers, next steps
    progress.md                     -- High-level phase tracking
    PROGRESS_LOG.md                 -- Detailed session log
    PROJECT_MEMORY.md               -- This file
  CLAUDE.md                         -- Platform rules (concise, ~65 lines)
  .mcp.json                         -- MCP server config for Claude Code
  mcp-server/src/mcp_server/        -- MCP + REST server
    mcp/server.py                   -- 8 MCP tools
    api/routes/                     -- REST endpoints (projects, health, logs, webhook, templates, generate_runs)
    services/                       -- Business logic (registry, scanner, RAG bridge, claude, template_service, template_resolver)
    db/                             -- SQLAlchemy models (Project, ScanRun, GenerateRun, Template, TemplateVersion, TemplateProfile, ProfileAssignment)
    templates/                      -- Scoring templates (rubric, anti-patterns, red-flags, security, patterns)
    scripts/seed_templates.py       -- DB seeder for filesystem templates
    alembic/                        -- Database migrations
  dashboard/src/                    -- React control panel
    components/                     -- ProjectList, ActionButtons, LogPanel, etc.
    components/templates/           -- TemplateCatalogue, TemplateEditor, VersionTimeline, ProfileManager, ProfileDetail, ABComparison, DocumentCatalogue
    hooks/                          -- useProjects, useLogStream, useResetStatus, useTemplates
    api/client.ts                   -- REST API client
  intodoc/src/intodoc/              -- Scanner CLI
    cli.py                          -- Click entry point
    models.py                       -- 30+ Pydantic models
    scanner/                        -- File walker, credential filter
    platforms/talend/               -- Talend parser, prompts, best practices
    chunker/                        -- Region-based chunking
    ir/                             -- Universal IR
  intodoc-output/                   -- Scan cache (read-only during doc gen)
  output/                           -- Generated docs + logs
```

## How to Resume Development

1. Read `context.md` for current state
2. Read `PROJECT_MEMORY.md` (this file) for architecture and patterns
3. Read `.agent/README.md` for the "when to read what" index
4. Check `PROGRESS_LOG.md` for what was done in previous sessions
5. For doc generation work, read `.agent/system/mcp-workflow.md`
6. For MCP server work, read `.agent/system/mcp-server.md`
7. Run dashboard: `cd dashboard && npm run dev`
8. Run MCP server: `cd mcp-server && python -m mcp_server --serve`
