# intodoc -- Code Intelligence Platform

Multi-project, multi-platform tool that scans integration codebases (Talend, Workato,
Lobster, and a Generic fallback -- the platforms in `intodoc/src/intodoc/platforms/registry.py`),
generates comprehensive documentation, and feeds a RAG knowledge base.

Components (`intodoc/` scanner CLI, `mcp-server/` MCP + REST API, `dashboard/` React
control panel), services, ports and quick commands are listed once in the root
`CLAUDE.md`; the local quickstart is the root `readme.md`. The MCP REST API
(`python -m mcp_server --serve`) listens on port 8002 by default
(`mcp-server/src/mcp_server/__main__.py`).

## Rules

1. **Read `.agent/README.md` first** -- it tells you which docs to read for your task
2. **Check the decision record** -- architecture decisions live in `.agent/decisions/`; read the relevant one before changing a design
3. **Credential filter first** -- never send raw credentials to any LLM
4. **ASCII-only Markdown** -- no Unicode box-drawing characters in generated docs
5. **No intermediate files** -- only final `.md` docs in `output/{project_id}/docs/` (generated via `docs_staging/`, see Documentation Generation)
6. **Never hardcode group names** -- use whatever `get_chunks()` returns
7. **Never call `trigger_scan()`** unless cache is explicitly stale or missing
8. **Never resolve git credentials by hand** -- call `provider_for_project()` in `services/git_credentials.py`. Reading `project.github_token_enc` directly skips GitHub App and Azure DevOps connections, whose credential lives off the project row. See `.agent/system/mcp-server.md` "Git connection kinds".
9. **No workarounds** -- same as root `CLAUDE.md` rule 9.
10. **Generalize fixes** -- same as root `CLAUDE.md` rule 10.
11. **Use design patterns** -- same as root `CLAUDE.md` rule 11.

## Documentation Generation

For CLI providers, `claude-cli` is the canonical four-phase generation
mechanism. `opencode-cli` is a transport adapter for that exact prompt and the
same coordinator-prepared evidence, staging, resume, validation and publication
path. Do not add an OpenCode scheduler, generation profile, goal-driven path or
agent workspace; those architectures are retired.

### The CLI lane's method is a FILE, not a prompt (2026-09-24)

`data/generation_method/METHOD.md` is the whole procedure -- the four phases,
the workspace layout, what each phase produces, and the self-check. It is staged
to `{staging}/_method/METHOD.md` by `services/generation_method.py` and read by
the agent. `ClaudeCliProvider._build_prompt` is now ~2.8 KB (was ~47 KB) and
carries only what a method file cannot know: paths, counts, models, resume
state. Change the METHOD, not the prompt.

Evidence discipline is NOT restated in METHOD.md -- it points at
`data/investigation_methods/_shared/confidence-model.md` (Basis
`Confirmed`/`Inferred`/`Unknown` per finding; document Ceiling derived from the
input class, never hand-set; Aggregation is a minimum, never a product; a
`Confirmed` finding must carry a locus) and `validation-checklist.md`. Both are
digest-pinned copies from the Integration Suite and live in a `_shared`
namespace because they apply to every platform. `materialize_investigation_method`
stages them plus the platform's own guides; it had been dead code, so the pinned
Talend guides were reaching no lane at all until this date.

REMOVED from this lane, and do not reintroduce:
- `scripts/bundle_analysis.py` and Phase 2.8. Doc-pack bundling belongs to the
  custom harness (`phase28_bundles.py`), which is unaffected.
- The Phase 2.5 `ls | grep -c` coverage gate. Coverage is the agent's own check.
- `analysis_target_kb`, a per-group byte cap computed from the PROJECT total.
  Measured on `talend8_main`: every group got the same 60 KB ceiling, so a
  2,013 KB group was analysed at 3.0% while a 6 KB group had 9x more headroom
  than it needed. The governing rule is now intent, not bytes -- **Phase 2 is
  the retrieval corpus (exhaustive, depth scales with the evidence); Phase 3 is
  the human deliverable (selective, and the only place omission is decided).**
- The per-section `_analysis/{group}/{SECTION}.md` split. Back to one
  `_analysis/{group}.md` with H2 headings -- but ONLY because the two Python
  consumers above were deleted first. `analysis_store` still reads both layouts
  so in-flight runs resume. NEVER reintroduce Python that parses model prose to
  find sections: that is the run-258 defect (see `.agent/decisions/035`).

KNOWN, not yet decided: `--max-turns 200` on the subprocess is now the binding
constraint on Phase 2 depth for a large estate, and it is also the runaway guard.
UNPROVEN: no generation has run against this method. It trades Python validators
for a method document -- better rules, less enforcement.

### Custom Harness (summary)

- Doc generation in the custom harness runs a **phase pipeline** orchestrated by
  `PipelineRunner` (`mcp-server/src/mcp_server/providers/custom/pipeline/runner.py`);
  phase bodies live in `providers/custom/pipeline/phases/`, and `harness.py` owns setup,
  state, enterprise gate evaluation and the staging swap.
- The phase table, config knobs, packs, prompt templates, evidence lanes, budgets and
  slicing, runtime evidence (TMC) and read scope/grants, Talend ESB detection, business
  topology, composite-upload orchestration and debug forensics are all documented in
  **`.agent/system/mcp-workflow.md`** -- that file is the single home for these
  implementation notes. Read the relevant section before changing a phase.
- Python constructs contracts, bounds context, validates and persists exact LLM output;
  it never writes documentation prose itself.

### Safe Re-Runs (Staging Directory)

Doc generation uses a **staging directory** (`docs_staging/`) to protect existing docs:

1. The custom harness writes new docs to `output/{project_id}/docs_staging/`
2. On **success**: old `docs/` is removed, `docs_staging/` is renamed to `docs/`, then server pushes to RAG automatically
3. On **failure**: `docs_staging/` is preserved for resume (the next Generate resumes from checkpoint), old `docs/` remains intact, `doc_count` unchanged (`projects.py::_run_generate_background`)

Key files: `claude_service.py` / `DocGenerationCoordinator` (generation orchestration and provider selection), `providers/custom/harness.py` (custom harness setup/state), and `projects.py:_run_generate_background` (swap logic + server-side RAG push).

**Do NOT** add `insert_to_rag` back to doc-generation prompts -- RAG push is handled server-side in `_run_generate_background` via `RagBridge.push_docs()`.

Debug forensics (`MCP_PIPELINE_DEBUG`): see `.agent/system/mcp-workflow.md`.

OpenCode runtime homes, credentials and failure diagnostics (including running
`docker compose exec` with `-u appuser`): see `mcp-server/OPENCODE_OPERATOR.md`.

## Code Conventions

- **Python**: 3.11+, Pydantic v2, Click CLI, hatchling build system
- **Frontend**: React 19, Vite 6, TypeScript 5, Tailwind CSS 3
- **Tests**: pytest (Python), Vitest (frontend)

## Known Pitfalls

This is the single list of scanner/platform pitfalls (formerly duplicated in
`intodoc/CLAUDE.md` and `.agent/SOP/troubleshooting.md`).

- **Windows encoding**: cp1252 breaks Unicode chars (em-dashes, arrows) -- use ASCII in all CLI text and generated output
- **Token bloat**: the tMap distiller must strip noise BEFORE chunking -- `field_type` filtering is critical
- **ENDPOINT preservation**: don't strip URL-related params -- REST/OData endpoints are semantically important
- **Connection matching**: use component `REPOSITORY_PROPERTY_TYPE` refs, NOT region-name matching
- **hatchling**: use hatchling, NOT setuptools -- pyproject.toml is configured for it

## Deployment

EP-001 status, the canonical nginx/certbot path and the Compose health boundary are in
the root `CLAUDE.md`; the procedure and acceptance checks are `deploy/RUNBOOK.md` and
`deploy/SMOKE-TEST.md`.

## Subscription Licence

ADR: `.agent/decisions/037-subscription-licence-is-a-signed-file.md`. Operator
procedure: root `deploy/RUNBOOK.md` section 17.

- **Where it lives**: `mcp-server/src/mcp_server/licensing/`. `core.py` holds the verifier and
  `EntitlementPolicy`. `keys.py` holds the pinned public keys. `runtime.py` holds the process policy and
  the startup line `licence: enforcement=... state=...`. Settings are
  `MCP_LICENCE_ENFORCEMENT`, `MCP_LICENCE_FILE` and `MCP_LICENCE_CLIENT_SLUG`. In compose
  they are fed from the same `LICENCE_*` env-file vars as the RAG backend, so they
  cannot disagree. Never give them separate values.
- **Enforcement**: the chokepoint is run *creation*.
  `ProjectRegistry.create_scan_run` / `create_generate_run` check the policy and
  raise `LicenceRefused`, which `api/app.py` maps to 402. Every caller goes through them:
  routes, webhook, ops_control, partition orchestrator, git reingestion and the MCP tool.
  Background callers (git poller, webhook) log once and skip. They never retry-loop.
  The generation quota counts top-level runs (`partition_name IS NULL`, not a
  gate retry) in `[nbf, exp)`, excluding failed runs, under
  `pg_advisory_xact_lock`. `licensing/usage.py` is the single definition; do not
  re-count elsewhere. LLM routes
  that create no run carry the route dependency instead. Never add a run-creation
  path that bypasses the registry.
- **402 contract and mirror rule**: the shared 402 `code` list and the byte-identical
  verifier mirror with the RAG backend are defined once in ADR-037. mcp-server specifics:
  the dashboard shows a banner and disables generate/scan without logging out, and
  `GET /mcp/licence/status` reports state plus used/allowed generations.
- **Dev**: `MCP_LICENCE_ENFORCEMENT=off` (the Settings default for a bare run; root `docker-compose.yml` defaults to `enforce`, so set `LICENCE_ENFORCEMENT=off` in `.env` for compose dev) disables every check. With
  `MCP_APP_ENV=production`, startup refuses `off` or an empty slug.
- **Operator side**: licences are issued with
  `python -m mission_control.licensing issue` on the operator machine. The signing
  key (`MC_LICENCE_SIGNING_KEY_FILE`) must never be on a client stack.
- **Issuing UI + push (ADR-038)**: the Fleet tab calls Mission Control `/ops/licensing/plans` and
  `/ops/clients/{slug}/licences`, which issue, record and push. The instance endpoint
  `POST /ops/control/licence` (`api/routes/ops_control.py` -> `licensing/install.py`) verifies the token
  against the pinned keys and slug BEFORE an atomic write: 422 `licence_rejected`, 413 when oversize,
  503 when the store is not writable. Only mcp-server mounts the licence dir read-write. The heartbeat
  carries a `licence` block (with `next_licence`). Mission Control addresses every control call by the
  `mcp_url` pinned in `clients.json` (https, required; the heartbeat URL is never used). A renewal
  pushed while the current licence is valid is queued in `licence.next.jwt` and served once it starts
  (`EntitlementPolicy(next_source=...)`); do not write a not-yet-valid token over the current file.
