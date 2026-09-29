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

## Step 5: Start Services

Follow the "Start Services" section of the root `readme.md` (the single quickstart).
Ports: RAG backend 8000, MCP REST API 8002 (`python -m mcp_server --serve` default),
dashboard dev server 5173.

## Step 6: Verify

```bash
curl http://localhost:8002/health
open http://localhost:5173    # log in through the dashboard for authenticated calls
```

Default dashboard credentials: `admin` / `admin`

## Step 7: Optional MCP stdio server

`cd knowledge_base/mcp-server && python -m mcp_server --mcp` runs the optional/debug MCP
stdio server; point a local (untracked) MCP client config at that command. Normal
dashboard-driven doc generation uses the REST API plus the custom harness.

## Notes

- The MCP server (stdio mode) is optional/debug; the dashboard uses the REST API server
- The REST API server (port 8002) must be started manually for the dashboard
- Ollama must be running with `nomic-embed-text` pulled for RAG embeddings
- Ollama is also used directly by the MCP server if `MCP_DOC_GENERATION_PROVIDER=ollama`
