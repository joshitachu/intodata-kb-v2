# IntoData KB -- Integration Intelligence Platform

> Multi-project platform: scans integration codebases, generates documentation,
> and provides RAG-powered knowledge search with local LLMs.

## Quick Facts

- **Stack**: Python (FastAPI), React + TypeScript, PostgreSQL, Qdrant, Ollama
- **Sub-projects**: `knowledge_base/` (intodoc scanner + MCP + dashboard), `RAG_pipeline/` (RAG backend + frontend)
- **Start all**: `docker-compose up -d`
- **Test**: `cd RAG_pipeline/backend && pytest` | `cd knowledge_base/dashboard && npm test`

## MANDATORY: Session Start

Run `/session-start` first. It loads context, checks for an active plan, and classifies
the work as Scout or full pipeline. Do not skip.

Manual steps (if not using the command):
1. `knowledge_base/CLAUDE.md` -- KB platform rules, doc generation workflow, known pitfalls
2. `RAG_pipeline/CLAUDE.md` -- RAG system rules, logging format, code style
3. The relevant `.claude/context.md` for the area you're working on
4. The relevant `.claude/PROJECT_MEMORY.md` for architecture decisions and gotchas
5. `.plans/active_plan.md` -- check for an active plan and its verdict before coding

For knowledge_base work, also read `.agent/README.md` to find the right SOPs and system docs.

## Development Workflow

### Scout mode — minor fix (< 3 files, no new abstractions, no schema changes, no critical-area changes)
1. `/session-start` → confirms Scout classification
2. Build
3. `/validate` → commit

### Full pipeline (everything else)
1. `/session-start` → full pipeline classification + pipeline state recovery
2. `/grill <description>` → `.plans/01_grill.md` — clarifying questions, assumptions, risk
3. `/prd` → `.plans/02_prd.md` — product requirements
4. `/slices` → `.plans/03_slices.md` — vertical slice breakdown
5. `/plan <slice>` → `.plans/04_active_plan.md` with `verdict: PENDING`
6. `/plan-approve pass` → `verdict: PASS` (gate opens)
7. Build (only after `verdict: PASS`)
8. `/inspect` → `.plans/05_inspection_report.md`
9. `/validate` → commit (only after Inspector PASS or WARN)

Repeat steps 5–9 for each remaining slice.

### Emergency path
- `/lfe-force <description>` → immediate patch, bypasses plan gate and Inspector
- Logs to `PROTOCOL_DEBT.md` — session-start warns until `/debt-resolve` clears it

**Critical areas requiring `/inspect` without exception:**
- Harness pipeline phases (`knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/`)
- MCP API routes (`knowledge_base/mcp-server/src/mcp_server/api/`)
- RAG retrieval chain (`RAG_pipeline/backend/src/retrieval/`)
- Auth / RBAC (`RAG_pipeline/backend/src/auth/`, `RAG_pipeline/backend/src/rbac/`)

### Pipeline coordination files (`.plans/`)
```
01_grill.md             — clarifying questions + risk assessment
02_prd.md               — product requirements
03_slices.md            — vertical slice breakdown
04_active_plan.md       — current slice plan (verdict: PENDING/PASS/BLOCK)
05_inspection_report.md — Inspector audit result
PROTOCOL_DEBT.md        — open emergency hotfix debt
```
Session-start reads these files to recover the pipeline position after a crash.

## Project Structure

```
intodata-kb/
├── CLAUDE.md                          -- This file
├── docker-compose.yml                 -- Full stack (6 services)
├── .env.production.template           -- Production env vars
├── knowledge_base/                    -- intodoc code intelligence platform
│   ├── CLAUDE.md                      -- Sub-project docs (read for KB tasks)
│   ├── .agent/                        -- Agent context docs (SOPs, system, decisions, tasks)
│   ├── intodoc/                       -- Scanner CLI (Talend, Workato, Generic)
│   ├── mcp-server/                    -- MCP + REST API (port 8001)
│   ├── dashboard/                     -- React control panel (port 5173/8001)
│   ├── intodoc-output/                -- Scan cache (read-only JSON)
│   └── output/                        -- Generated docs (Markdown)
├── RAG_pipeline/                      -- RAG knowledge base system
│   ├── CLAUDE.md                      -- Sub-project docs (read for RAG tasks)
│   ├── backend/                       -- FastAPI + Qdrant + Ollama (port 8000)
│   └── frontend/                      -- Next.js UI (port 3000)
├── deploy/                            -- Deployment scripts + systemd services
├── scripts/                           -- Utility scripts
└── vaxxinova-talend/                  -- Sample Talend codebase for testing
```

## Architecture

Two independent sub-systems sharing infrastructure:

**1. intodoc (knowledge_base/)**: Source Code -> Scanner CLI (Talend/Workato/Generic) -> JSON Cache -> MCP Server -> custom harness generates docs
**2. RAG Pipeline (RAG_pipeline/)**: Documents -> Ingest -> Chunk -> Embed (Ollama) -> Qdrant -> Query -> LLM -> Answer

## Services (docker-compose.yml)

| Service | Image/Build | Port | Purpose |
|---------|------------|------|---------|
| postgres | postgres:16-alpine | 5432 | Document storage + RBAC |
| qdrant | qdrant/qdrant:latest | 6333, 6334 | Vector database |
| hindsight | ghcr.io/vectorize-io/hindsight | 8888, 9999 | Long-context memory |
| backend | RAG_pipeline/backend | 8000 | RAG API server |
| frontend | RAG_pipeline/frontend | 3000 | RAG UI (Next.js) |
| dashboard | knowledge_base/dashboard | 8001 | intodoc control panel |

## Deployment Foundation Status (EP-001)

- EP-001 is now **Deployment Foundation**, generic rather than customer-specific.
- Canonical deploy path is `deploy/deploy-client.sh` + `deploy/nginx/nexus-client.conf.template` + certbot. Caddy files remain legacy/deferred unless a future story revives them.
- Seven Docker services are the Compose health boundary: `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `mcp-server`, `dashboard`.
- MCP API runs as the `mcp-server` Compose service and publishes the VPS-local `${MCP_PORT:-8002}` port for nginx webhooks and operator SSH tunnels.
- Deployment docs now live in `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`.
- EP-001 is not accepted until the runbook and smoke test pass on a clean reference VPS.

## Quick Commands

```bash
# Full stack
docker-compose up -d                           # Start all services
docker-compose down                            # Stop all

# RAG backend (local dev)
cd RAG_pipeline/backend && uvicorn src.api.main:app --reload

# RAG frontend (local dev)
cd RAG_pipeline/frontend && npm run dev

# intodoc MCP server (stdio mode; optional/debug)
cd knowledge_base/mcp-server && python -m mcp_server --mcp

# intodoc REST API (for dashboard)
cd knowledge_base/mcp-server && python -m mcp_server --serve

# intodoc dashboard
cd knowledge_base/dashboard && npm run dev

# Run a scan
cd knowledge_base/intodoc && intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output

# Tests
cd RAG_pipeline/backend && pytest
cd knowledge_base/dashboard && npm test
```

## Sub-Project Docs

Each sub-project has its own detailed CLAUDE.md. **Read the relevant one before working on that area**:

- **`knowledge_base/CLAUDE.md`** -- intodoc scanner, MCP server, dashboard rules & conventions
- **`RAG_pipeline/CLAUDE.md`** -- RAG backend/frontend, logging rules, code style, environment config
- **`knowledge_base/intodoc/CLAUDE.md`** -- Scanner CLI internals (prompts, phases, IR format)

## Logging

After every change run `/session-end` -- it reads git diff, pre-fills the log entry,
and updates context.md. Format and field rules are embedded in that command.
Never skip. Use specific file paths and verbatim error messages.

## Key Rules

1. **Read sub-project CLAUDE.md** before working on knowledge_base/ or RAG_pipeline/
2. **ASCII-only Markdown** in generated docs (Windows cp1252 compatibility)
3. **Never hardcode** group names -- use API results
4. **Credential safety** -- never send raw credentials to LLMs
5. **No intermediate files** -- only final docs in output/{project_id}/docs/
6. **Doc generation uses staging** -- the custom harness writes to `docs_staging/`, server swaps to `docs/` on success (see `knowledge_base/CLAUDE.md` for details)
7. **ALWAYS document changes** -- run `/session-end` after every change; update sub-project CLAUDE.md and `.agent/` docs if architecture or workflow changed; stale docs are worse than no docs
8. **Python builds**: hatchling not setuptools; **Testing**: pytest (Python), Vitest (frontend)
9. **No workarounds** -- fixes must address the root cause, not suppress symptoms. If a proper fix requires broader changes, make them. Never ship a narrow patch when the defect pattern exists elsewhere in the codebase.
10. **Generalize fixes** -- before fixing anything, check whether the same failure can occur in other places. Fix it at the level that prevents all recurrences. One-off special-case branches are not acceptable solutions.
11. **Use design patterns** -- before writing a new class, service, or module, identify whether an established pattern fits (Strategy, Repository, Factory, Observer, Pipeline, etc.). Don't invent bespoke structures when a well-understood pattern solves the problem.
