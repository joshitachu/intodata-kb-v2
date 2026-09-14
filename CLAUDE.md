# IntoData KB -- Integration Intelligence Platform

> Multi-project platform: scans integration codebases, generates documentation,
> and provides RAG-powered knowledge search with local LLMs.

## Quick Facts

- **Stack**: Python (FastAPI), React + TypeScript, PostgreSQL, Qdrant, Ollama
- **Sub-projects**: `knowledge_base/` (intodoc scanner + MCP + dashboard), `RAG_pipeline/` (RAG backend + frontend)
- **Start all**: `docker-compose up -d`
- **Test**: `cd RAG_pipeline/backend && pytest` | `cd knowledge_base/dashboard && npm test`

## MANDATORY: Session Start

Invoke the `session-start` skill first. It loads current state, recovers the pipeline
position, and classifies the work. Do not skip.

Manual steps (if not using the skill):
1. `knowledge_base/CLAUDE.md` -- KB platform rules, doc generation workflow, known pitfalls
2. `RAG_pipeline/CLAUDE.md` -- RAG system rules, logging format, code style
3. The relevant `.claude/context.md` -- live state only, no history
4. `.plans/plan.md` -- if it exists, check its `verdict` before coding

For knowledge_base work, also read `.agent/README.md` to find the right SOPs and system docs.
Architecture decisions live in `knowledge_base/.agent/decisions/`.

## Development Workflow

Two paths. The gate that matters is plan approval; everything else is a check, not a ritual.

### Fast path — small, well-understood change
1. `session-start` -- confirms fast-path classification
2. Build
3. `validate` -> commit

Qualifies when: fewer than ~3 files, no new module or schema or API contract, and no
critical area touched.

### Planned — everything else
1. `session-start` -- classification + pipeline recovery
2. `plan <description>` -> `.plans/plan.md` with `verdict: PENDING`
3. **You** set `verdict: PASS` -- this is the gate, and the agent must not open it itself
4. Build
5. `inspect` -> `.plans/inspection.md` (mandatory for critical areas)
6. `validate` -> commit (only on inspection PASS or WARN)

Multi-slice work repeats steps 4-6 per slice against the same plan.

**Critical areas requiring `inspect` without exception:**
- Harness pipeline phases (`knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/`)
- MCP API routes (`knowledge_base/mcp-server/src/mcp_server/api/`)
- RAG retrieval chain (`RAG_pipeline/backend/src/retrieval/`)
- Auth / RBAC (`RAG_pipeline/backend/src/auth/`, `RAG_pipeline/backend/src/rbac/`)

### Pipeline coordination files (`.plans/`)
```
plan.md         -- intent, decisions, design, slices, risk (verdict: PENDING/PASS/BLOCK)
inspection.md   -- audit result (verdict: PASS/WARN/BLOCK)
archive/        -- completed and abandoned features; never read at session start
```
Only loose `.md` files at the top level are live. `session-start` reads them to recover
position after a crash. **One feature at a time** -- archive the previous feature's
artifacts before starting the next, or pipeline detection becomes ambiguous.

### Agent config
- `.claude/skills/` is the single source of truth for skills.
- `.codex/skills/` is a generated mirror. After editing a skill, run
  `scripts/sync-agent-skills.sh`; `--check` fails if the mirror is stale.
- Never hand-edit `.codex/skills/`. Three hand-maintained copies is what caused the
  last drift, where every file disagreed with every other.

## Project Structure

```
intodata-kb/
├── CLAUDE.md                          -- This file
├── docker-compose.yml                 -- Full stack (8 services; 7 in the health boundary)
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
└── mission_control/                   -- Heartbeat aggregator (operator-side; 8th Compose service)
```

Client source trees and sample codebases are deliberately NOT in the repo. They
are gitignored (see the "Confidential client materials" block in `.gitignore`)
and any local copies live outside it in `../intodata-kb-archive/client-source/`.

## Architecture

Two independent sub-systems sharing infrastructure:

**1. intodoc (knowledge_base/)**: Source Code -> Scanner CLI (Talend/Workato/Generic) -> JSON Cache -> MCP Server -> custom harness generates docs
**2. RAG Pipeline (RAG_pipeline/)**: Documents -> Ingest -> Chunk -> Embed (Ollama) -> Qdrant -> Query -> LLM -> Answer

## Services (docker-compose.yml)

Eight services are defined. Seven are the EP-001 Compose health boundary;
`mission-control` is the exception (see below).

| Service | Image/Build | Port | Purpose |
|---------|------------|------|---------|
| postgres | postgres:16-alpine | 5432 | Document storage + RBAC |
| qdrant | qdrant/qdrant:latest | 6333, 6334 | Vector database |
| hindsight | ghcr.io/vectorize-io/hindsight | 8888, 9999 | Long-context memory |
| backend | RAG_pipeline/backend | 8000 | RAG API server |
| frontend | RAG_pipeline/frontend | 3000 | RAG UI (Next.js) |
| mcp-server | knowledge_base/mcp-server | `${MCP_PORT:-8002}` | MCP + REST API (nginx webhooks, operator tunnels) |
| dashboard | knowledge_base/dashboard | 8001 | intodoc control panel |
| mission-control | mission_control/ | `${MC_PORT:-9100}` (127.0.0.1) | Aggregates heartbeat snapshots from customer instances. NOT part of the health boundary -- it is an operator-side aggregator, not a per-client service. |

`RAG_pipeline/docker-compose.yml` is a **dev-only** file that redefines four of
these (`postgres`, `qdrant`, `hindsight`, `backend`) so the RAG stack can run
without the full eight. It is not the production topology; this file is. Its
port and Hindsight-model values are hardcoded where the root file parameterises
them, so treat root as authoritative on any disagreement.

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

After every change run the `session-end` skill -- it reads the git diff, prepends the
log entry to `PROGRESS_LOG.md` (newest-first), and rewrites `context.md` in place.
Format and field rules are in that skill. Never skip. Use specific file paths and
verbatim error messages.

Two invariants keep these files usable:
- `PROGRESS_LOG.md` is append-only history, **newest-first**, rotated to
  `.claude/archive/` after ~30 days.
- `context.md` is **live state only** -- no dated sections, under 60 lines. Adding a
  `## Current State (date)` block to it is the regression that made it useless before.

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
