# IntoData KB -- Integration Intelligence Platform

> Multi-project platform: scans integration codebases, generates documentation,
> and provides RAG-powered knowledge search over configurable LLM providers.

## Quick Facts

- **Stack**: Python (FastAPI), React + TypeScript, PostgreSQL, Qdrant. Model providers are configurable; the RAG backend defaults to Kimi for chat and Voyage for embeddings/reranking, with Ollama on the host as the local option (`RAG_pipeline/backend/src/config.py`)
- **Sub-projects**: `knowledge_base/` (intodoc scanner + MCP + dashboard), `RAG_pipeline/` (RAG backend + frontend)
- **Start, ports, tests**: `readme.md` (human quickstart; the only home for start commands)
- **Architecture**: `ARCHITECTURE.md`

## Before You Start

Read the sub-project doc for the area you are changing (`knowledge_base/CLAUDE.md`,
`RAG_pipeline/CLAUDE.md`, `knowledge_base/intodoc/CLAUDE.md`). For knowledge_base work,
`knowledge_base/.agent/README.md` points to the right SOPs and system docs. Architecture
decisions live in `knowledge_base/.agent/decisions/`.

## Project Structure

```
intodata-kb/
├── CLAUDE.md                          -- This file
├── docker-compose.yml                 -- Full stack (10 services; 7 in the health boundary)
├── .env.production.template           -- Production env vars
├── knowledge_base/                    -- intodoc code intelligence platform
│   ├── CLAUDE.md                      -- Sub-project docs (read for KB tasks)
│   ├── .agent/                        -- Agent context docs (SOPs, system, decisions, tasks)
│   ├── intodoc/                       -- Scanner CLI (Talend, Workato, Generic)
│   ├── mcp-server/                    -- MCP + REST API (port 8002)
│   ├── dashboard/                     -- React control panel (dev 5173, container 8001)
│   ├── intodoc-output/                -- Scan cache (read-only JSON)
│   └── output/                        -- Generated docs (Markdown)
├── RAG_pipeline/                      -- RAG knowledge base system
│   ├── CLAUDE.md                      -- Sub-project docs (read for RAG tasks)
│   ├── backend/                       -- FastAPI + Qdrant + Ollama (port 8000)
│   └── frontend/                      -- Next.js UI (port 3000)
├── deploy/                            -- deploy-client.sh, nginx template, runbook, smoke test
├── scripts/                           -- Utility scripts
├── examples/demo-mcp/                 -- Fictional read-only demo MCP server (Compose `demo-mcp`)
├── labs/opencode/                     -- Experimental OpenCode lane (Compose profile `labs`)
└── mission_control/                   -- Heartbeat aggregator (operator-side Compose service)
```

Client source trees and sample codebases are deliberately NOT in the repo. They
are gitignored (see the "Confidential client materials" block in `.gitignore`)
and any local copies live outside it in `../intodata-kb-archive/client-source/`.

## Architecture

Two independent sub-systems sharing infrastructure:

**1. intodoc (knowledge_base/)**: Source Code -> Scanner CLI (Talend/Workato/Generic) -> JSON Cache -> MCP Server -> custom harness generates docs
**2. RAG Pipeline (RAG_pipeline/)**: Documents -> Ingest -> Chunk -> Embed (Voyage, Ollama fallback) -> Qdrant -> Query -> LLM (selected provider) -> Answer

## Services (docker-compose.yml)

Ten services are defined. Seven are the EP-001 Compose health boundary.
`mission-control` also starts with `docker compose up -d` but is outside it;
`demo-mcp` starts only with `--profile demo` and `opencode-labs` only with `--profile labs`.

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
| demo-mcp | examples/demo-mcp | `${DEMO_MCP_PORT:-8765}` | Read-only MCP server with invented work-order data, for demonstrating project-scoped MCP calls. Profile `demo` only; never in a client deployment. |
| opencode-labs | labs/opencode | none (compose network, 4096) | Experimental OpenCode loop behind `/labs/chat`. Profile `labs` only; not in the health boundary. |

`RAG_pipeline/docker-compose.yml` is a **dev-only** file that redefines four of
these (`postgres`, `qdrant`, `hindsight`, `backend`) so the RAG stack can run
without the full stack. It is not the production topology; this file is. Its
port and Hindsight-model values are hardcoded where the root file parameterises
them, so treat root as authoritative on any disagreement.

## Deployment Foundation Status (EP-001)

- EP-001 is now **Deployment Foundation**, generic rather than customer-specific.
- Canonical deploy path is `deploy/deploy-client.sh` + `deploy/nginx/nexus-client.conf.template` + certbot. nginx/certbot is the only supported path.
- Seven Docker services are the Compose health boundary: `postgres`, `qdrant`, `hindsight`, `backend`, `frontend`, `mcp-server`, `dashboard`.
- MCP API runs as the `mcp-server` Compose service and publishes the VPS-local `${MCP_PORT:-8002}` port for nginx webhooks and operator SSH tunnels.
- Deployment docs now live in `deploy/RUNBOOK.md` and `deploy/SMOKE-TEST.md`.
- EP-001 is not accepted until the runbook and smoke test pass on a clean reference VPS.

## Quick Commands

Start commands, ports, smoke tests, and test commands live in `readme.md`.

## Sub-Project Docs

Each sub-project has its own detailed CLAUDE.md. **Read the relevant one before working on that area**:

- **`knowledge_base/CLAUDE.md`** -- intodoc scanner, MCP server, dashboard rules & conventions
- **`RAG_pipeline/CLAUDE.md`** -- RAG backend/frontend, logging rules, code style, environment config
- **`knowledge_base/intodoc/CLAUDE.md`** -- Scanner CLI internals (architecture, layout, known pitfalls)

## Key Rules

1. **Read sub-project CLAUDE.md** before working on knowledge_base/ or RAG_pipeline/
2. **ASCII-only Markdown** in generated docs (Windows cp1252 compatibility)
3. **Never hardcode** group names -- use API results
4. **Credential safety** -- never send raw credentials to LLMs
5. **No intermediate files** -- only final docs in output/{project_id}/docs/
6. **Doc generation uses staging** -- the custom harness writes to `docs_staging/`, server swaps to `docs/` on success; on failure `docs_staging/` is preserved for resume and `docs/` stays intact (see `knowledge_base/CLAUDE.md` for details)
7. **ALWAYS document changes** -- update the sub-project CLAUDE.md and `.agent/` docs when architecture or behaviour changes; stale docs are worse than no docs
8. **Python builds**: hatchling not setuptools; **Testing**: pytest (Python), Vitest (frontend)
9. **No workarounds** -- fixes must address the root cause, not suppress symptoms. If a proper fix requires broader changes, make them. Never ship a narrow patch when the defect pattern exists elsewhere in the codebase.
10. **Generalize fixes** -- before fixing anything, check whether the same failure can occur in other places. Fix it at the level that prevents all recurrences. One-off special-case branches are not acceptable solutions.
11. **Use design patterns** -- before writing a new class, service, or module, identify whether an established pattern fits (Strategy, Repository, Factory, Observer, Pipeline, etc.). Don't invent bespoke structures when a well-understood pattern solves the problem.
