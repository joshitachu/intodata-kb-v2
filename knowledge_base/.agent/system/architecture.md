# intodoc Platform Architecture

## Overview

intodoc is a multi-project, multi-platform code intelligence tool built in phases:

1. **Scan & Parse** -- Extract structured data from integration codebases
2. **Document** -- Generate comprehensive documentation (business + technical)
3. **Normalize** -- Convert platform-specific models to universal IR
4. **Migrate** -- Auto-generate code for target platforms (planned)

## Architecture

The platform operates in two modes:

### Mode 1: Scanner CLI (intodoc)

- `intodoc scan` parses source code into structured scan cache JSON in `intodoc-output/`
- Analysis prompts: `ANALYSIS_GUIDE.md` and `PHASE_3_GUIDE.md` (consolidated from legacy 22 .txt files)
- 9 reference docs in `intodoc/reference-docs/` define output format/depth
- Read-only tool -- scans source code, does not generate documentation
- Supported platforms: Talend, Workato, Generic, Lobster

### Mode 2: Documentation Agent (MCP + provider)

- MCP server (`mcp-server/`) exposes scan data via 14 tools (`get_chunks`, `fetch_chunk`, etc.)
- Two provider backends in `providers/`: `claude_cli` (Claude Code subprocess) and `custom` (API harness)
- Phase 1: Setup (load scan data via MCP)
- Phase 2: Group Analysis (parallel per-group tasks, configurable concurrency via `MCP_AGENT_PHASE2_CONCURRENCY`)
- Phase 3: Write Docs (parallel per-document tasks via `MCP_AGENT_PHASE3_CONCURRENCY`)
- Phase 4: Enterprise gates + publish (`enterprise_gates.json`, `_audit/`, `index.json`, run log; RAG push is handled server-side via `RagBridge.push_docs()`)
- Task artifacts persisted in `docs_staging/_agent_tasks/{phase}/{target_id}/` (contract.json, output.md, validation_report.json)
- Custom provider requires API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, etc.)
- Publishable runs default to enterprise mode. Failed gates preserve `docs_staging/` for repair/resume and block swap to `docs/`.

### Supporting Services

- Dashboard (`dashboard/`) -- React + Vite control panel (:5173)
- RAG Pipeline (`../RAG_pipeline/`) -- FastAPI + Qdrant for doc search (:8000)

## Multi-Project Architecture

Projects are registered via `add_repo(url, project_id, platform)` and tracked in PostgreSQL.
Each project has independent scan cache, output directory, and status tracking. The platform
is designed to manage multiple projects simultaneously through the dashboard or MCP tools.

## Multi-Platform Support

The scanner uses a platform plugin architecture (`src/intodoc/platforms/<name>/`). Each
platform plugin is self-contained with its own parser, prompts, and best practices. Current
production platforms include Talend, Workato, Generic, and Lobster; the architecture still
supports adding MuleSoft, dbt, and other integration platforms.

## Data Pipeline

```
Source Code (XML, properties, configs, etc.)
    -> Scanner (file walker + credential filter)
    -> Platform Parser (platform-specific plugin)
    -> Scan Cache (intodoc-output/ JSON files)
    -> MCP Server (serves chunks, manifest, connections)
    -> Doc Generation Provider (claude_cli or custom API harness, analyzes chunks)
    -> Documentation (9 enterprise markdown docs in output/{project_id}/docs_staging/)
    -> Enterprise gates + audit bundle
    -> Server swaps docs_staging/ -> docs/ only on success, then pushes to RAG via RagBridge
```

## Project Structure

```
knowledge_base/                     -- Platform root
  .agent/                           -- Agent context docs
    system/                         -- Architecture, workflow, tech stack
    SOP/                            -- Standard operating procedures
    tasks/                          -- Phase FRDs
    decisions/                      -- Architecture Decision Records
  .claude/                          -- Claude Code settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- Platform rules and quick reference
  mcp-server/                       -- MCP + REST API server
    src/mcp_server/
      mcp/server.py                 -- 14 MCP tools
      api/                          -- REST API routes (FastAPI, JWT auth on /mcp/*)
      services/                     -- 25+ service files
      providers/                    -- claude_cli + custom API harness
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
      ir/                           -- Universal Intermediate Representation
    reference-docs/                 -- 9 format/structure specifications
  intodoc-output/                   -- Scan cache (read-only during doc generation)
  output/                           -- Agent output (docs + logs only)
```

## Integration Points

- **MCP Server** -- Exposes scan data to Claude Code agent via MCP protocol
- **REST API** -- Dashboard communicates with MCP server via REST (:8001)
- **Dashboard** -- React control panel for project lifecycle management
- **RAG Pipeline** -- Ingests generated markdown docs for knowledge base search
  - Uses nomic-embed-text (768-dim) + BM25 hybrid search via Qdrant

## Current State (Feb 2026)

- Phase 0 complete (cleanup + knowledge base)
- Phase 1 complete (21 analysis passes, 32 templates, 3 new passes P19-P21)
- Phase 2 complete (Universal IR: 99.4% expression parse rate)
- MCP-based doc generation operational (12 output documents)
- Phase 3 (migration code generator) not yet started
