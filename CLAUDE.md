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
DATABASE_URL=postgresql+asyncpg://admin:${POSTGRES_PASSWORD}@localhost:5432/intodata_kb

# Models
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=gemma3:12b
```

## Logging Rules

**These rules apply to ALL sub-projects. No exceptions.**

### 1. Read Before Working

At the **start of every session**, before writing any code, read:
- `.claude/context.md` -- current state, blockers, what to work on next
- `.claude/PROJECT_MEMORY.md` -- architecture decisions, patterns, lessons learned
- `.claude/progress.md` -- high-level progress tracker
- `.claude/PROGRESS_LOG.md` -- detailed session history

### 2. Log Every Change

After **every change** (bug fix, feature, refactor, config change -- no matter how small), immediately update:

#### `.claude/PROGRESS_LOG.md` -- Append a new entry:

```markdown
## Session: YYYY-MM-DD

### [Fix/Feature/Refactor]: Short description

- **Files changed**: `path/to/file.py` (lines X-Y)
- **Problem**: Describe the error or missing behavior
- **Root cause**: Why the problem existed
- **Change**: Describe exactly what was added/modified/removed
- **Why this approach**: Reasoning for the chosen solution
- **Verification**: How it was confirmed working
```

#### `.claude/context.md` -- Update:
- Current state / status
- Any new blockers
- What should be done next

#### `.claude/PROJECT_MEMORY.md` -- Update if applicable:
- New architecture decisions (add to the decisions table)
- New lessons learned / gotchas

#### `.claude/progress.md` -- Update:
- Check off completed items
- Add new items if scope changed

### 3. Never Skip Logging

- A "quick fix" still gets logged. There are no exceptions.
- If you fix a typo, log it. If you add an import, log it. If you change a config value, log it.
- Use **specific file paths**, not vague descriptions ("updated the backend").
- Include **error messages** verbatim when fixing bugs.
- The log is the project's memory. Without it, we lose context between sessions.

## Key Rules

1. **Read sub-project CLAUDE.md** before working on knowledge_base/ or RAG_pipeline/
2. **ASCII-only Markdown** in generated docs (Windows cp1252 compatibility)
3. **Never hardcode** group names -- use API results
4. **Credential safety** -- never send raw credentials to LLMs
5. **No intermediate files** -- only final docs in output/{project_id}/docs/
6. **Doc generation uses staging** -- Claude Code writes to `docs_staging/`, server swaps to `docs/` on success (see `knowledge_base/CLAUDE.md` for details)
7. **ALWAYS document changes** -- After ANY code change (bug fix, feature, refactor, config -- no matter how small):
   - **Log it** using the format in "Logging Rules" above (PROGRESS_LOG.md, context.md, PROJECT_MEMORY.md, progress.md)
   - Update the **sub-project CLAUDE.md** (`knowledge_base/CLAUDE.md` or `RAG_pipeline/CLAUDE.md`) if the change affects architecture, conventions, or workflow
   - Update **`.agent/` docs** (SOPs, architecture, lessons-learned, mcp-workflow) if the change affects documented processes or tool behavior
   - Update **this root CLAUDE.md** if the change affects cross-project rules or the overall system
   - If a change contradicts existing docs, **fix the docs immediately** -- stale docs are worse than no docs
   - This is non-negotiable. Code changes without doc updates are incomplete.
