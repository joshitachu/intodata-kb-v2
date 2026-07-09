# SOP: Set Up Development Environment

Get the intodoc platform running locally for development.

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- PostgreSQL running locally (or via `docker-compose up -d postgres`)
- Ollama with `nomic-embed-text` model (for RAG pipeline embeddings)

## Step 1: Install Python Dependencies

```bash
# Scanner CLI
cd knowledge_base/intodoc
pip install -e ".[dev]"

# MCP server
cd ../mcp-server
pip install -e .
```

## Step 2: Install Node Dependencies

```bash
cd knowledge_base/dashboard
npm install
```

## Step 3: Environment Variables

Create `knowledge_base/mcp-server/.env`:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:changeme@localhost:5432/intodata_kb
JWT_SECRET_KEY=change-me-for-local-dev

# Optional: for custom API provider
# MCP_DOC_GENERATION_PROVIDER=custom
# ANTHROPIC_API_KEY=...
```

## Step 4: Database Setup

```bash
# RAG pipeline migrations
cd RAG_pipeline/backend
alembic upgrade head

# MCP server migrations
cd knowledge_base/mcp-server
alembic upgrade head
```

The MCP server also auto-creates tables on first startup, but Alembic is required for schema upgrades.

## Step 5: Start Services (3 terminals)

```bash
# Terminal 1: RAG pipeline (port 8000)
cd RAG_pipeline/backend
python -m uvicorn src.api.main:app --port 8000

# Terminal 2: MCP REST API (port 8001)
cd knowledge_base/mcp-server
python -m mcp_server --serve

# Terminal 3: Dashboard (port 5173)
cd knowledge_base/dashboard
npm run dev
```

## Step 6: Verify

```bash
# Health check
curl http://localhost:8001/health

# List projects (requires auth cookie -- use dashboard login instead)
open http://localhost:5173
```

Default dashboard credentials: `admin` / `admin`

## Step 7: Optional MCP stdio configuration

The `.mcp.json` file in `knowledge_base/` configures the optional MCP stdio server.
Normal dashboard-driven doc generation uses the REST API plus the custom harness, so
Claude Code is not required.

Test with:
```
> Use the intodoc MCP tools to list projects
```

## Notes

- The MCP server (stdio mode) is optional/debug; the dashboard uses the REST API server
- The REST API server (port 8001) must be started manually for the dashboard
- Ollama must be running with `nomic-embed-text` pulled for RAG embeddings
- Ollama is also used directly by the MCP server if `MCP_DOC_GENERATION_PROVIDER=ollama`
