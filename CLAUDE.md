# IntoData KB -- Integration Intelligence Platform

> Multi-project platform: scans integration codebases, generates documentation,
> and provides RAG-powered knowledge search with local LLMs.

## Quick Facts

- **Stack**: Python (FastAPI), React + TypeScript, PostgreSQL, Qdrant, Ollama
- **Sub-projects**: `knowledge_base/` (intodoc scanner + MCP + dashboard), `RAG_pipeline/` (RAG backend + frontend)
- **Start all**: `docker-compose up -d`
- **Test**: `cd RAG_pipeline/backend && pytest` | `cd knowledge_base/dashboard && npm test`

## MANDATORY: Session Start

Before writing ANY code, read these files:
1. `knowledge_base/CLAUDE.md` -- KB platform rules, doc generation workflow, known pitfalls
2. `RAG_pipeline/CLAUDE.md` -- RAG system rules, logging format, code style
3. The relevant `.claude/context.md` for the area you're working on
4. The relevant `.claude/PROJECT_MEMORY.md` for architecture decisions and gotchas

For knowledge_base work, also read `.agent/README.md` to find the right SOPs and system docs.

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

**1. intodoc (knowledge_base/)**: Source Code -> Scanner CLI (Talend/Workato/Generic) -> JSON Cache -> MCP Server -> Claude Code generates docs
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

# intodoc MCP server (for Claude Code)
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
6. **Doc generation uses staging** -- Claude Code writes to `docs_staging/`, server swaps to `docs/` on success (see `knowledge_base/CLAUDE.md` for details)
7. **ALWAYS document changes** -- run `/session-end` after every change; update sub-project CLAUDE.md and `.agent/` docs if architecture or workflow changed; stale docs are worse than no docs
8. **Python builds**: hatchling not setuptools; **Testing**: pytest (Python), Vitest (frontend)
