# KB Sub-project Context

Live state only. Session history is in `.claude/PROGRESS_LOG.md`; durable decisions are in `.agent/decisions/`.

## What this sub-project is

`knowledge_base/` contains the intodoc scanner, MCP/REST orchestration service, and React dashboard for integration-landscape intelligence.

## Current state

- OpenCode documentation generation is a transport adapter for the canonical Claude Code CLI mechanism.
- `OpenCodeCliProvider` sends the byte-identical `ClaudeCliProvider._build_prompt` once to one `opencode run --format json` session over the complete estate.
- `ClaudeService` owns the shared preprocessing, quick scan, resume state, staging lifecycle, validation, atomic publication, and RAG push for both CLI transports.
- The OpenCode-specific Gather/Analyze/Produce scheduler, goal-driven pipeline, agent-workspace pipeline, connector runtime, and reingestion worker are removed.
- OpenCode bypasses the composite partition orchestrator and publishes one root document tree; Claude CLI and custom providers still support partitioned generation.
- Historical generation-profile JSON remains readable, but every profile is rejected for a new or resumed run. The dashboard no longer exposes profile, goal, or agentic controls.
- OpenCode runtime differences are limited to executable arguments, pinned-version preflight, isolated HOME/XDG/auth state, provider configuration, JSON-event parsing, usage accounting, and process cleanup.
- OpenCode Go keys use a temporary 0600 auth file. Known credentials from environment and OpenCode JSON configuration are redacted from event and stderr diagnostics.
- Shared business-topology services and publication paths remain live; deleting `providers/agent_workspace/topology.py` removed only the retired workspace projection.
- Business topology is built by ONE provider-neutral builder (`services/business_topology/builder.py`) for the custom harness (Phase 3.7 adapter), claude-cli and opencode-cli (bounded 2-call model lane each), followed by deterministic scan + TMC enrichment. Composite merge keeps and really validates the whole business layer; a fresh curated file wins everywhere, a stale one's judgement is overlaid and re-validated. UNCOMMITTED as of 2026-09-23; inspection cycle 2 = WARN, post-inspection fixes in its addendum (`.plans/inspection.md`).
- Static topology publication to the RAG runtime mirror was SILENTLY DEAD for every scan since the approval receipt went to
  `schema_version: 2`: `static_topology_publisher._approved_artifact_hashes` pinned `== 1`, and because publication is advisory the
  scan still reported success. v2 only adds `tree` (which `providers/workspace/builder.py` needs, not this publisher), so the check
  now accepts `{1, 2}` and names the version it rejects. `scripts/backfill_static_topology.py` republishes from artifacts already on
  disk -- read-only unless `--publish`. Backfilled: alitalend, pluripharm, nexus_logs, werkorder. Still blocked on a rescan: cdc,
  demo-runtime, mvp_test, reingest-drill, reverse_engineer_VW (no scan output) and cdc_job_route (missing approved artifacts).
- Newline-delimited streams are read through `utils/stream_frames.py`, never `readline()`: the OpenCode event/stderr readers and both ends of the connector-facade relay. Frame size there is model- or payload-controlled, so the stream buffer must not be the ceiling.
- `claude-cli` is RUNNABLE in-container: the image pins `@anthropic-ai/claude-code`, credentials live in the `claude_home` volume with `CLAUDE_CONFIG_DIR` pointing both `.credentials.json` and `.claude.json` into it, and the dashboard method toggle is three-way. Login is `claude auth login --claudeai` (NOT `claude login`, which the CLI parses as a prompt and exits 0 having done nothing). `token_refresh.apply_auth_env` strips `ANTHROPIC_API_KEY` from the CLI subprocess when OAuth credentials exist -- without it the harness's key silently outranks the subscription.
- The CLI lane can QUERY the runtime estate while it writes: `{staging}/_tools/runtime_tools.py` (`list` / `call` / `elastic`) reaches the backend's 25-tool runtime registry and 4 Elastic probes, every call appended to `_assessment/runtime_probe_ledger.jsonl`. `_tools/` is deleted before publication -- the CLI lane never runs `phase4_cleanup.PUBLISHED_STAGING_DIRS`, so nothing generic would have stopped it shipping to a client.
- Runtime evidence still reaches documents ONLY in the custom harness (`phase3_docs.py:53`). The CLI lane's own `fetch_runtime_evidence` at `claude_service.py:2588` runs AFTER `provider.run` and feeds the topology overlay only. `runtime_authority.py` and `elastic_evidence.py` have zero importers, yet `enterprise/phase_contracts.py:438` reads the file the former would write -- so every enterprise unit reports `status=unavailable` regardless of truth.
- **Subscription licence (UNCOMMITTED, 2026-09-28)**: signed per-instance licence (ADR 037, `mcp_server/licensing/`, `core.py`/`keys.py` byte-mirrored with the RAG backend). Scans and generations are refused at `ProjectRegistry.create_*_run` (402); basic plan = 3 top-level generations per licence period under a Postgres advisory lock; LLM routes without a run record carry `require_capability`, pinned by `tests/test_api/test_licence_route_inventory.py`. Dev: `MCP_LICENCE_ENFORCEMENT=off`. Inspection WARN (`.plans/inspection.md`); plan verdict still PENDING (user must set PASS).

## Verification

- Routing/provider backend tests: 103 passed.
- Backend excluding four unrelated failing fixture files: 2424 passed, 5 warnings.
- Dashboard: TypeScript clean; 158 Vitest tests passed.
- Python compilation and `git diff --check`: passed.
- Installed OpenCode version: 1.18.7, matching the configured pin.
- Full backend (2026-09-23): 3404 passed, 10 failed -- all 10 fail identically on clean HEAD (github_app_routes x3, model catalogue x2, platform strategies x1, `test_generate_docs_cli_not_found` (no `intodoc` in .venv), component_description_service x3). The curated VW count drift is fixed.
- Topology parity (offline, VW scan, deterministic floor + enrichment, no LLM, no runtime): 0.366 vs 0.238 before (`scripts/topology_parity.py`).
- Repository scan finds 83 pre-existing variation selectors under generated `knowledge_base/output/` trees.
- Stream-frame fix: `pytest tests/test_providers tests/test_utils tests/test_connectors` -> 940 passed; the regression test was confirmed to fail against the old `readline()` reader.
- Runtime-tools + claude-cli slice: full suite 3474 passed / 10 failed (the same 10 as above, stash-confirmed). Live in-container: `claude auth status` -> `authMethod: "claude.ai"`; `GET /api/v1/runtime/tools` -> 25 tools; `POST .../list_runtime_job_reliability {"project_id":"pluripharm"}` -> real rows (`AIS_DWH_Patient_Split` 299 runs / 41 failed / 13.7%).
- Runtime matching is NOT broken, but exact-name only: pluripharm matches 3/3 by name, while alitalend's scan (`AssetCheck_Main_Job`) and its TMC mirror (`Assetcheck_Interface`) share no identical name across 28 related jobs, and `runtime_job_scan_maps` has 0 rows.

## Next step

Licence: user sets `verdict: PASS`, commit scoped to the licence files (the connector WIP is someone else's), then browser smoke (SMOKE-TEST section 10) with `LICENCE_ENFORCEMENT=enforce` and a dev licence. Existing clients: install a licence BEFORE upgrading (RUNBOOK 17c).

Topology: commit (scoped to the topology files -- the tree also holds another session's work), rebuild `mcp-server`, and run one live claude-cli and one opencode generation to confirm the model lane, the OpenCode wildcard deny (no tool events in the log) and parity above 0.37. Before rollout, run `_sensitive_findings` over deployed instances' published topologies (the widened secret check fails a matching topology closed at read time).

Earlier: rebuild the `mcp-server` image (source is baked in, not bind-mounted) and re-run `ettm_TEST_Claude` with OpenCode -- staging from failed run 257 is preserved, so it resumes from checkpoint. Then, with explicit approval for model spend, compare one representative project through OpenCode and Claude against the same sanitized scan: all nine documents, assessment artifacts, cost, and wall time, before adding any workspace mechanism.

## Unverified

- Live OpenCode 1.18.7 honouring of the inline `permission` wildcard deny; Stage A business-layer recall on a real run; deployment agreement (needs `artifact_version` on `GET /runtime/doc-evidence`); VW runtime binding (rows live under `alitalend`).
- No generation run has yet used the runtime tools. The surface answers live, but an agent choosing tools mid-run, and the five prompt rules actually changing what gets written, are untested. `pluripharm` is the candidate (healthy source, synced daily). Also untested: `claude-cli` as a transport end to end -- the `stream-json` parsing and 200-turn budget have never driven a real generation here.
- The provenance value-check is inert: the ledger records result SHAPE, not values, so `measurement_provenance` verifies structure (window, denominator, register) and skips the figure cross-check rather than faking it.

- Production document-quality parity, model cost, and wall time. The first isolated OpenCode smoke produced no output and was terminated rather than allowed to spend or hang indefinitely.
