# intodoc Platform Architecture

## Overview

intodoc is a multi-project, multi-platform code intelligence tool:

1. **Scan & Parse** -- Extract structured data from integration codebases
2. **Document** -- Generate comprehensive documentation (business + technical)

## Architecture

The platform operates in two modes:

### Mode 1: Scanner CLI (intodoc)

- `intodoc scan` parses source code into structured scan cache JSON in `intodoc-output/`
- Analysis prompts: `ANALYSIS_GUIDE.md` and `PHASE_3_GUIDE.md` (consolidated from legacy 22 .txt files)
- 9 reference docs in `intodoc/reference-docs/` define output format/depth
- Read-only tool -- scans source code, does not generate documentation
- Supported platforms: Talend, Workato, Generic, Lobster

### Mode 2: Documentation Harness (MCP + provider)

- MCP server (`mcp-server/`) exposes scan data via 14 tools (`get_chunks`, `fetch_chunk`, etc.)
- CLI generation has one canonical four-phase workflow. `claude-cli` and
  `opencode-cli` receive the same prepared staging workspace and exact prompt;
  they differ only in CLI transport and event parsing. OpenCode has no
  scheduler, workspace contract, or goal-driven route.
- The custom API harness runs the phase pipeline listed in `mcp-workflow.md` (the one
  place the phase table is kept). RAG push is handled server-side via `RagBridge.push_docs()`.
- Task artifacts persisted in `docs_staging/_agent_tasks/{phase}/{target_id}/` (see
  `mcp-workflow.md` "Debug Forensics" for which files are always-on)
- Custom provider requires API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, etc.)
- Enterprise publish gates are advisory: failures surface as warnings and the swap proceeds
  (see `mcp-workflow.md` "Evidence Traceability").

### Supporting Services

- Dashboard (`dashboard/`) -- React + Vite control panel (:5173)
- RAG Pipeline (`../RAG_pipeline/`) -- FastAPI + Qdrant for doc search (:8000)

Technology stack and versions: root `ARCHITECTURE.md`.

## Multi-Project Architecture

Projects are registered via `add_repo(url, project_id, platform)` and tracked in PostgreSQL.
Each project has independent scan cache, output directory, and status tracking. The platform
is designed to manage multiple projects simultaneously through the dashboard or MCP tools.

## Multi-Platform Support

The scanner uses a platform plugin architecture (`src/intodoc/platforms/<name>/`). Each
platform plugin is self-contained with its own parser, prompts, and best practices. The
implemented platforms are Talend, Workato, Lobster and Generic (`platforms/registry.py`);
new ones are added per `intodoc/.agent/sop/add-platform.md` (ADR-027).

## Data Pipeline

```
Source Code (XML, properties, configs, etc.)
    -> Scanner (file walker + credential filter)
    -> Platform Parser (platform-specific plugin)
    -> Scan Cache (intodoc-output/ JSON files)
    -> MCP Server (serves chunks, manifest, connections)
    -> Doc Generation Provider (custom API harness, analyzes chunks)
    -> Documentation (9 enterprise markdown docs in output/{project_id}/docs_staging/)
    -> Enterprise gates + audit bundle
    -> Server swaps docs_staging/ -> docs/ only on success, then pushes to RAG via RagBridge
```

## Project Structure

```
knowledge_base/                     -- Platform root
  .agent/                           -- Agent context docs
    system/                         -- Architecture, workflow, server, dashboard
    SOP/                            -- Standard operating procedures
    decisions/                      -- Architecture Decision Records
  CLAUDE.md                         -- Platform rules
  mcp-server/                       -- MCP + REST API server
    src/mcp_server/
      mcp/server.py                 -- 14 MCP tools
      api/                          -- REST API routes (FastAPI, JWT auth on /mcp/*)
      services/                     -- 25+ service files
      providers/                    -- claude_cli, opencode_cli, custom API harness
      db/                           -- SQLAlchemy models + session
  dashboard/                        -- React control panel
    src/
      components/                   -- 16 components (ProjectList, ActionButtons, LogPanel,
                                    --   RunHistory, QualityHistoryPanel, ABComparison, etc.)
      hooks/                        -- useProjects, useLogStream, useAuth, useDiscovery
      api/client.ts                 -- REST API client
  intodoc/                          -- Scanner CLI
    src/intodoc/
      cli.py                        -- Click CLI entry point
      models.py                     -- 30+ Pydantic data models
      scanner/                      -- File walking, credential filter
      platforms/                    -- talend, workato, generic, lobster plugins
      lineage.py, landscape.py      -- Deterministic lineage + landscape passes
      mapping_accounting.py, capability_ledger.py -- Coverage ledgers
    reference-docs/                 -- 9 format/structure specifications
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```

## Integration Points

- **MCP Server** -- Exposes scan data to the custom harness and optional MCP tools
- **REST API** -- Dashboard communicates with MCP server via REST (:8002)
- **Dashboard** -- React control panel for project lifecycle management
- **RAG Pipeline** -- Ingests generated markdown docs for knowledge base search
  - Uses nomic-embed-text (768-dim) + BM25 hybrid search via Qdrant
