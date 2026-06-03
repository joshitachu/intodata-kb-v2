# intodoc -- Code Intelligence Platform

Multi-project, multi-platform tool that scans integration codebases (Talend, MuleSoft, dbt,
etc.), generates comprehensive documentation, and feeds a RAG knowledge base.

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Scanner CLI | `intodoc/` | Parses source code into structured scan cache JSON |
| MCP Server | `mcp-server/` | Exposes scan data to Claude Code via MCP tools + REST API |
| Dashboard | `dashboard/` | React control panel for managing projects, scans, docs |
| RAG Pipeline | `../RAG_pipeline/` | FastAPI + Qdrant for document search |

## Quick Commands

```bash
# MCP server (stdio, for Claude Code)
cd mcp-server && python -m mcp_server --mcp

# REST API (port 8001, for dashboard)
cd mcp-server && python -m mcp_server --serve

# Dashboard (port 5173)
cd dashboard && npm run dev

# Run a scan
cd intodoc && intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output

# RAG pipeline (port 8000)
cd ../RAG_pipeline/backend && python -m uvicorn src.api.main:app --port 8000
```

## Rules

1. **Read `.agent/README.md` first** -- it tells you which docs to read for your task
2. **Read `.claude/` files before starting work** -- read `context.md`, `PROJECT_MEMORY.md`, `progress.md`, and `PROGRESS_LOG.md` at session start. Follow the unified logging rules in root `CLAUDE.md` (section "Logging Rules") for all changes -- same format, same discipline as `RAG_pipeline/`.
3. **Credential filter first** -- never send raw credentials to any LLM
4. **ASCII-only Markdown** -- no Unicode box-drawing characters in generated docs
5. **No intermediate files** -- only final `.md` docs in `output/{project_id}/docs/` (generated via `docs_staging/`, see Documentation Generation)
6. **Never hardcode group names** -- use whatever `get_chunks()` returns
7. **Never call `trigger_scan()`** unless cache is explicitly stale or missing

## Documentation Generation

The MCP-based 4-phase documentation workflow is documented in:
**`.agent/system/mcp-workflow.md`**

This is the primary method for generating project documentation. Follow it exactly.

### Current Custom Harness Notes

- Custom doc generation now uses a bounded internal agent runtime in `mcp-server/src/mcp_server/providers/custom/agent_runtime.py`.
- Phase 2 dispatches one persisted group-analysis task per scanner group, with configurable concurrency (`MCP_AGENT_PHASE2_CONCURRENCY`, default 6). Oversized/split group work is merged by an LLM merge task, not by Python-authored prose.
- Phase 2.6 scorecard and Phase 3 document writing are also persisted agent tasks under `docs_staging/_agent_tasks/{phase}/{target_id}/`.
- Phase 3 dispatches one document task per required markdown document, with configurable concurrency (`MCP_AGENT_PHASE3_CONCURRENCY`, default 4).
- Runtime task directories store `contract.json`, exact LLM `output.md`, `validation_report.json`, `tool_call_ledger.json`, `provider_metrics.json`, and `status.json`. Resume should skip accepted task artifacts and retry rejected/incomplete tasks independently.
- Python may construct contracts, constrain context/tools, validate coverage, trigger retries/repairs, and persist exact LLM output. Python must not synthesize business explanations or fill final documentation prose itself.
- Generic `list_artifacts` / `read_artifact` tools must not be exposed to Phase 2/3 core generation without hard byte and accumulated input-token caps. Phase 2 core writing uses bounded context files by default; retrieval may be agentic only in budgeted selection/repair steps.

### Steven Project Finding

- For project `Steven`, the generated docs/RAG answer incorrectly claimed `Web.config` and `Global.asax.cs` were unavailable. Local source and scan output show both files exist and scanner output contains substantial content.
- The real issue is a documentation synthesis/coverage bug: Phase 3 docs inferred absence from thin normalized artifacts such as `config_runtime_map.md` / `dependency_map.md`, even though `scan_result.json` contains the relevant `content` / `source_code`.
- Do not repeat raw secrets from Steven. `Web.config` contains sensitive app settings and database connection strings. Any fix must preserve credential redaction before LLM calls and should document connection names/systems without exposing secret values.
- Follow-up fix direction: ensure generic-platform evidence tools / EngineeringFacts surface redacted config keys and database connection names from `Web.config`, plus OrmLite registration names from `Global.asax.cs`, so docs stop saying configuration is missing when scanner evidence exists.

### Safe Re-Runs (Staging Directory)

Doc generation uses a **staging directory** (`docs_staging/`) to protect existing docs:

1. Claude Code writes new docs to `output/{project_id}/docs_staging/`
2. On **success**: old `docs/` is removed, `docs_staging/` is renamed to `docs/`, then server pushes to RAG automatically
3. On **failure**: `docs_staging/` is deleted, old `docs/` remains intact, `doc_count` unchanged

Key files: `claude_service.py` (prompt + staging dir setup), `projects.py:_run_generate_background` (swap logic + server-side RAG push).

**Do NOT** add `insert_to_rag` back to the Claude Code prompt -- RAG push is handled server-side in `_run_generate_background` via `RagBridge.push_docs()`.

## Code Conventions

- **Python**: 3.11+, Pydantic v2, Click CLI, hatchling build system
- **Frontend**: React 19, Vite 6, TypeScript 5, Tailwind CSS 3
- **Tests**: pytest (Python), Vitest (frontend)

## Known Pitfalls

- **Windows encoding**: cp1252 breaks Unicode chars -- use ASCII in all output
- **Token bloat**: tMap distiller must strip noise BEFORE chunking
- **ENDPOINT preservation**: don't strip URL/REST params -- they are semantic
- **Connection matching**: use `REPOSITORY_PROPERTY_TYPE` refs, not name matching
- **hatchling**: use hatchling, NOT setuptools -- pyproject.toml is configured for it
- **Budget**: legacy CLI analyzer default $5 limit; 18 passes on 9 chunks can exceed

## Deployment Foundation Notes (EP-001)

- EP-001 has been realigned to generic deployment foundation, not a customer-specific rollout.
- nginx/certbot is canonical for new deployments. Caddy is legacy/deferred.
- Dashboard depends on MCP REST API, and MCP API runs as the `mcp-server` service in the Docker Compose stack.
- Runbook and smoke-test docs are in root `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`.
- Before marking EP-001 done, validate on a clean reference VPS: seven Docker healthchecks, MCP `/health`, HTTPS/security headers, login/logout, ingest, streaming query, dashboard, and IP allowlist.

## Project Structure

```
knowledge_base/                     -- Platform root
  .agent/                           -- Agent context docs (README, system, SOPs, tasks)
  .claude/                          -- Claude Code settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- This file
  mcp-server/                       -- MCP + REST API server (Python, FastAPI)
  dashboard/                        -- React control panel (Vite + Tailwind, :5173)
  intodoc/                          -- Scanner CLI (see intodoc/CLAUDE.md)
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```
