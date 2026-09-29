# MCP Server

The MCP server (`mcp-server/`) exposes scan data to the custom documentation harness,
keeps optional MCP tooling available, and provides a REST API for the dashboard.

## Dual Mode

The server runs in two modes via `python -m mcp_server`:

- `--mcp` -- MCP stdio server for optional/debug tool access
- `--serve` -- REST API on port 8002 (`--port` default in `__main__.py`) for the dashboard

## MCP Tools (14 tools)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `add_repo` | `url`, `project_id`, `platform?` | Register a project (local path, git URL, or 'zip') |
| `fetch_json` | `project_id`, `section?` | Read cached scan JSON by section |
| `trigger_scan` | `project_id`, `force?` | Run `intodoc scan` subprocess — only if cache is stale/missing |
| `get_scan_status` | `project_id` | Cache age, job count, status |
| `get_chunks` | `project_id` | Chunk definitions (chunk_id, group, job_count, token_estimate) |
| `fetch_chunk` | `project_id`, `chunk_id` | Full job data for one chunk |
| `insert_to_rag` | `project_id`, `docs?` | Push .md files to RAG — do not call during doc generation; push is server-side via `RagBridge.push_docs()` |
| `list_projects` | -- | All registered projects with status |
| `get_analysis_prompts` | `project_id`, `platform?` | Returns bundled `ANALYSIS_GUIDE.md` and `PHASE_3_GUIDE.md` content |
| `get_reference_docs` | -- | Returns reference doc content for output format/structure |
| `recall_process_memory` | `query` | Query Hindsight for cross-project process lessons |
| `recall_project_memory` | `project_id`, `query` | Query Hindsight for project-specific findings |
| `retain_findings` | `project_id`, `content` | Store project findings in Hindsight |
| `retain_process_lesson` | `content` | Store cross-project lesson in Hindsight |

### fetch_json Sections

- `meta` -- platform, repo_root, stats, scanned_at
- `manifest` -- job_calls, job_called_by, connections_used_by, routines_used_by
- `connections` -- all metadata connections
- `contexts` -- context variable groups
- `jobs/{GROUP}` -- all jobs in a specific group
- `all` -- meta + manifest + connections + job group summary (not full job data)

## REST API Endpoints

All endpoints are under `/mcp/` prefix:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/mcp/add-repo` | Register a project |
| POST | `/mcp/upload-zip` | Upload ZIP, unpack, register |
| GET | `/mcp/plugins/starter` | Return Swap Pack starter repo metadata |
| GET | `/mcp/plugins/starter.zip` | Download bundled Swap Pack starter (compatibility) |
| POST | `/mcp/plugins/discover` | Discover valid Swap Pack branches in a GitHub repo |
| POST | `/mcp/plugins` | Register a Swap Pack from local path or GitHub repo branch |
| POST | `/mcp/scan` | Trigger scan (background task) |
| POST | `/mcp/generate` | Run the configured doc-generation provider/harness |
| POST | `/mcp/push-to-rag` | Push docs to RAG pipeline |
| POST | `/mcp/reset-status` | Reset stuck generating/scanning project to error |
| GET | `/mcp/status/{project_id}` | Get project scan status |
| GET | `/mcp/projects` | List all projects |
| GET | `/health` | Health check |
| GET | `/logs/stream?project_id=...` | SSE log streaming |
| POST | `/webhook/github/webhook` | GitHub webhook (HMAC-verified, bypasses JWT) |
| POST | `/mcp/azure-devops/connections` | Connect an Azure DevOps organization (PAT verified, then encrypted) |
| GET | `/mcp/azure-devops/connections` | List the caller's Azure DevOps connections (never returns tokens) |
| POST | `/mcp/azure-devops/connections/{id}/test` | Re-verify a stored PAT against Azure DevOps |
| GET | `/mcp/azure-devops/connections/{id}/repos` | List repositories the connection can read (repo picker) |
| DELETE | `/mcp/azure-devops/connections/{id}` | Remove a connection and unbind its projects |

## Git connection kinds

`projects.connection_kind` selects how a project's git credential is resolved. All
three resolve through one Strategy in `services/git_credentials.py`
(`provider_for_project`) -- never branch on connection kind at a call site.

| Kind | Credential source | Notes |
|------|-------------------|-------|
| `pat` | `projects.github_token_enc` | Legacy/fallback; per-project encrypted PAT |
| `app` | `projects.github_installation_id` | GitHub App; short-lived token minted on demand |
| `azure_devops` | `projects.azure_devops_connection_id` -> `azure_devops_connections.pat_enc` | One organization PAT shared by every project from that organization |

Azure DevOps specifics:

- No App-style install exists, so a Personal Access Token (Code: Read is enough) is
  the only credential. It is verified against `_apis/connectionData` before it is
  persisted, so a stored connection always worked at least once.
- `utils/azure_devops_api.py` owns URL handling: `normalize_organization_url`
  reduces organization / project / `/_git/` / `_apis` / `dev.azure.com` /
  `*.visualstudio.com` / on-prem-collection URLs to one organization base.
  `add_repo` normalizes an Azure DevOps URL BEFORE its shell-metacharacter check,
  because browser URLs legitimately carry `?path=&version=`.
- Auto-reingestion for Azure DevOps repositories runs through the git poller. No
  service hook is registered (Azure DevOps service hooks have no HMAC signature).

## Source Structure

```
mcp-server/src/mcp_server/
  __main__.py          -- Entry point (--mcp or --serve)
  config.py            -- Pydantic Settings (paths, ports, URLs, API keys)
  schemas.py           -- API request/response models
  mcp/server.py        -- 14 MCP tools + dispatch
  api/
    app.py             -- FastAPI app factory (JWT auth middleware on /mcp/*)
    routes/
      projects.py      -- Project CRUD + scan/generate/RAG/GitHub webhook endpoints
      health.py        -- Health check
      logs.py          -- SSE log streaming (/logs/stream?project_id=...)
      webhook.py       -- GitHub webhook (HMAC, no JWT)
  services/            -- 25+ service files including:
    project_registry.py -- DB operations for projects + scan/generate runs
    scanner_service.py  -- Run intodoc scan, read scan cache/chunks
    rag_bridge.py       -- Push docs to RAG pipeline
    claude_service.py   -- DocGenerationCoordinator; resolves models and routes generation to the active provider
    log_manager.py      -- Log file management + streaming
    assessment_service.py, doc_quality_scorer.py
    auto_doc_update.py, git_poller.py, git_reingestion.py
    pdf_service.py, plugin_runner.py, summary_service.py
    model_pricing.py, ops_telemetry.py, validation_service.py
  providers/           -- Doc generation backends
    base.py            -- Abstract provider interface
    claude_cli.py      -- Canonical CLI generation provider (four-phase METHOD.md workflow)
    opencode_cli.py    -- OpenCode transport adapter for the same workflow (ADR-032)
    custom/            -- API-based custom harness (pipeline/, harness.py, agent_runtime.py)
  db/
    models.py          -- SQLAlchemy models (Project, ScanRun, GenerateRun, PluginPack)
    session.py         -- Async session factory
```

Note: all `/mcp/*` routes require JWT cookie auth. The GitHub webhook route explicitly bypasses JWT and uses HMAC verification instead.

## Living-Doc Reingestion (candidate lane)

When `MCP_LIVING_DOC_PATCHING_ENABLED=true`, every Git push that survives the
cheap non-document filter bypasses the legacy path-to-scan-group router and runs
through one bounded candidate (`services/auto_doc_update.py` ->
`services/living_docs/git_patch_coordinator.py`):

1. The webhook revision is reconciled from `last_ingested_commit_sha`, not
   blindly from the webhook's `before` value. Queued pushes therefore cannot
   skip an earlier held revision.
2. Existing validated docs without an active knowledge revision are imported
   byte-for-byte through `living_docs/baseline.py`; no generation call occurs.
3. `GitPatchCoordinator.create_agent_candidate()` binds the candidate to the
   exact before/after SHAs and makes the Git diff the deterministic evidence
   floor. Candidate IDs are idempotent per project and after SHA.
4. The candidate is executed by `TruePatchExecutor`
   (`services/true_patch_executor.py`, satisfying the `CandidatePatchExecutor`
   protocol); its report lands at `_reingestion/patch_report.json`.
5. Nexus verifies every operation's path, base/content hash, evidence citation,
   secret scan, uniqueness, and blast-radius limit before applying it inside the
   candidate workspace.
6. `CandidatePublisher` stages only changed/deleted logical IDs, validates the
   complete membership, then activates the RAG revision and matching docs tree.
   A replay returns the already-active candidate before executing again.

Append-only receipts live under
`data/living_docs/audit/{project_id}.jsonl` and include Git SHAs, candidate and
knowledge revisions, changed/deleted artifacts, embedding reuse, model usage,
files read, duration, and reason codes. The legacy deterministic patch and
targeted-regeneration paths remain available only when living-doc patching is
disabled.

The per-step contract of the Git-driven update lane (locking, recovery, analysis /
editing / review contracts, run journal) is in
`knowledge_base/mcp-server/docs/git-document-updates.md`.

## Swap Pack Starter

The dashboard-facing Swap Pack flow is backed by the existing `PluginPack`
runtime. Admins provide a public GitHub repository URL, the dashboard calls
`POST /mcp/plugins/discover`, and each branch with a valid root `plugin.yaml`
can be registered as its own pack. Registered GitHub packs store both
`repo_url` and `repo_branch`, clone into branch-specific local directories, and
are updated by the poller/webhook for that branch.

The starter repository shown in the dashboard comes from
`MCP_SWAP_PACK_STARTER_REPO_URL` and defaults to
`https://github.com/joshitachu/swap-pack-starter`. The starter repo contains
runtime pack files plus AI-authoring instructions (`AGENTS.md`, Codex skill,
Claude command). ZIP endpoints remain available for compatibility but are not
the visible dashboard path.

## Enterprise Generation Verdicts

`POST /mcp/generate` accepts `mode`:

- `enterprise` (default) -- blocks publish unless all enterprise gates pass
- `draft` -- allows exploratory generation but cannot be considered client-ready

Generation runs persist:

- `run_verdict`: `draft`, `failed`, `published`, `published_with_rag_error`, `client_ready`,
  `held_manual_review` (reingestion correctness gate held the swap) or `held_client_readiness`
  (enterprise Talend client-readiness verdict held the swap; `docs_staging/` is the labelled draft)
- `gate_results`: JSON copy of the enterprise gate report

Operational meaning:
- `client_ready` means docs passed enterprise gates and final RAG sync did not fail.
- `published_with_rag_error` means docs were saved but indexing failed; do not advance client delivery until RAG is repaired.
- `published` means docs saved but did not meet full client-ready conditions.
- `draft` means intentionally non-enterprise output.
- `failed` means docs were not swapped into `docs/`; inspect preserved `docs_staging/`.
- `held_client_readiness` means an enterprise Talend run (`generation_profile.contract =
  talend-enterprise-v1`) completed but `_assessment/publication_verdict.json` lists blocking
  findings (missing dossier, mapping table not embedded in full, internal-path leakage,
  undisclosed contradiction, unplaced design, open review finding). Nothing was swapped or
  pushed to RAG; the previous baseline stays published. Fix the findings and resume the run.
  The `talend-enterprise-v1` contract is retired (ADR-032, `RETIRED_CONTRACTS`); the
  verdict value is still written by the publication path and read for historical runs.
  An enterprise run that stops on a budget ceiling or an incomplete phase is `failed` with
  `_assessment/enterprise_run_receipt.json` naming the stop reason; resuming continues from
  the durable ledgers under `_evidence_registry/enterprise/`.

## Scan Output Contract

`ScannerService.run_scan()` clears stale scan artifacts before invoking `intodoc`.
After subprocess success it requires a fresh valid `scan_result.json` object with a
`jobs` list. Missing or invalid output is a failed scan. This prevents stale facts
from being reused by enterprise generation.

## Adding New Tools/Endpoints

1. Add the tool definition to `mcp/server.py` `list_tools()` return list
2. Add the handler function and register in `_dispatch()`
3. For REST: add the route to `api/routes/projects.py` or a new route file
4. Add request/response schemas to `schemas.py`
