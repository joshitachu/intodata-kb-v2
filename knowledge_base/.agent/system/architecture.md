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
- 22 prompt files (p01-p22) define analysis dimensions
- 25 reference docs define output format/depth
- Read-only tool -- scans source code, does not generate documentation
- Currently supports Talend; designed for multi-platform extension (MuleSoft, dbt, etc.)

### Mode 2: Documentation Agent (Claude Code + MCP)

- MCP server (`mcp-server/`) exposes scan data via tools (`get_chunks`, `fetch_chunk`, etc.)
- Claude Code acts as the documentation agent following `.agent/system/mcp-workflow.md`
- Phase 1: Setup (load scan data via MCP)
- Phase 2: Group Analysis (parallel sub-agents read prompts + analyze chunks)
- Phase 3: Write Docs (parallel sub-agents write .md files to `output/`)
- Phase 4: Publish (`index.json` + run log; RAG push is handled server-side)
- No LLM API keys needed -- Claude Code IS the LLM
- No Jinja2 templates for doc generation -- docs written directly by agents

### Supporting Services

- Dashboard (`dashboard/`) -- React + Vite control panel (:5173)
- RAG Pipeline (`../RAG_pipeline/`) -- FastAPI + Qdrant for doc search (:8000)

## Multi-Project Architecture

Projects are registered via `add_repo(url, project_id, platform)` and tracked in PostgreSQL.
Each project has independent scan cache, output directory, and status tracking. The platform
is designed to manage multiple projects simultaneously through the dashboard or MCP tools.

## Multi-Platform Support

The scanner uses a platform plugin architecture (`src/intodoc/platforms/<name>/`). Each
platform plugin is self-contained with its own parser, prompts, and best practices. Currently
only Talend is implemented; the architecture supports adding MuleSoft, dbt, and other
integration platforms.

## Data Pipeline

```
Source Code (XML, properties, configs, etc.)
    -> Scanner (file walker + credential filter)
    -> Platform Parser (platform-specific plugin)
    -> Scan Cache (intodoc-output/ JSON files)
    -> MCP Server (serves chunks, manifest, connections)
    -> Claude Code Agent (reads prompts p01-p22, analyzes chunks)
    -> Documentation (up to 25 markdown docs in output/{project_id}/docs_staging/)
    -> Server swaps docs_staging/ -> docs/ on success, then pushes to RAG via RagBridge
```

### Legacy CLI Path (still functional)

```
Source Code
    -> Scanner + Parser
    -> Chunker (groups by region, ~80K tokens/chunk)
    -> Analyzer (21 LLM passes per chunk via LiteLLM API)
    -> Generator (32 Jinja2 templates -> markdown)
    -> Exporter (PDF / Qdrant)
```

## Project Structure

```
vaxxinova/                          -- Platform root
  .agent/                           -- Agent context docs
    system/                         -- Architecture, workflow, tech stack
    sop/                            -- Standard operating procedures
    tasks/                          -- Phase FRDs
    decisions/                      -- Architecture Decision Records
  .claude/                          -- Claude Code settings + session logs
  .mcp.json                         -- MCP server configuration
  CLAUDE.md                         -- Platform rules and quick reference
  mcp-server/                       -- MCP + REST API server
    src/mcp_server/
      mcp/server.py                 -- 8 MCP tools
      api/                          -- REST API routes (FastAPI)
      services/                     -- Business logic (registry, scanner, RAG bridge)
      db/                           -- SQLAlchemy models + session
  dashboard/                        -- React control panel
    src/
      components/                   -- ProjectList, ActionButtons, LogPanel, etc.
      hooks/                        -- useProjects, useLogStream
      api/client.ts                 -- REST API client
  intodoc/                          -- Scanner CLI
    src/intodoc/
      cli.py                        -- Click CLI entry point
      models.py                     -- 30+ Pydantic data models
      scanner/                      -- File walking, credential filter
      platforms/talend/             -- Talend parser, prompts, best practices
      chunker/                      -- Group jobs into LLM-sized chunks
      ir/                           -- Universal Intermediate Representation
    reference-docs/                 -- 25 format/structure specifications
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
- MCP-based doc generation operational (up to 25 output documents)
- Phase 3 (migration code generator) not yet started
