# IntoData KB -- Integration Intelligence Platform

> Multi-project platform: scans integration codebases, generates documentation,
> and provides RAG-powered knowledge search with local LLMs.

## Quick Facts

- **Stack**: Python (FastAPI), React + TypeScript, PostgreSQL, Qdrant, Ollama
- **Sub-projects**: `knowledge_base/` (intodoc scanner + MCP + dashboard), `RAG_pipeline/` (RAG backend + frontend)
- **Start all**: `docker-compose up -d`
- **Test**: `cd RAG_pipeline/backend && pytest` | `cd knowledge_base/dashboard && npm test`

## Project Structure

```
intodata-kb/
├── CLAUDE.md                          -- This file
├── docker-compose.yml                 -- Full stack (6 services)
├── .env.production.template           -- Production env vars
├── knowledge_base/                    -- intodoc code intelligence platform
│   ├── CLAUDE.md                      -- Sub-project docs (read for KB tasks)
│   ├── intodoc/                       -- Scanner CLI (Talend, MuleSoft, dbt)
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

**1. intodoc (knowledge_base/)**: Source Code -> Scanner CLI -> JSON Cache -> MCP Server -> Claude Code generates docs
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

## Code Conventions

- **Python**: 3.11+, type hints, Pydantic v2, async/await for I/O, hatchling builds
- **Frontend**: React 19, TypeScript 5 strict (never `any`), Tailwind CSS 3
- **Testing**: pytest (Python), Vitest (frontend)
- **Error handling**: Never swallow errors, always log + show user feedback
- **RBAC**: Filter EVERY Qdrant query by user groups

## Environment

```bash
# docker-compose defaults
POSTGRES_DB=intodata_kb
POSTGRES_USER=admin
OLLAMA_BASE_URL=http://localhost:11434       # Host machine Ollama

# RAG backend
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql+asyncpg://admin:voetbal123@localhost:5432/intodata_kb

# Models
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=gemma3:12b
```

## Key Rules

1. **Read sub-project CLAUDE.md** before working on knowledge_base/ or RAG_pipeline/
2. **ASCII-only Markdown** in generated docs (Windows cp1252 compatibility)
3. **Never hardcode** group names -- use API results
4. **Credential safety** -- never send raw credentials to LLMs
5. **No intermediate files** -- only final docs in output/{project_id}/docs/
