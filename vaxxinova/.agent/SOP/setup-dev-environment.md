# SOP: Set Up Development Environment

Get the intodoc platform running locally for development.

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- PostgreSQL running locally
- Ollama with `nomic-embed-text` model (for RAG pipeline embeddings)

## Step 1: Install Python Dependencies

```bash
# Scanner CLI
cd intodoc
pip install -e ".[dev]"

# MCP server
cd ../mcp-server
pip install -e .
```

## Step 2: Install Node Dependencies

```bash
cd dashboard
npm install
```

## Step 3: Database Setup

The MCP server and RAG pipeline share a PostgreSQL database.

```bash
# Run Alembic migrations for RAG pipeline
cd ../RAG_pipeline/backend
alembic upgrade head
```

## Step 4: Start Services (3 terminals)

```bash
# Terminal 1: RAG pipeline (port 8000)
cd RAG_pipeline/backend
python -m uvicorn src.api.main:app --port 8000

# Terminal 2: MCP REST API (port 8001)
cd vaxxinova/mcp-server
python -m mcp_server --serve

# Terminal 3: Dashboard (port 5173)
cd vaxxinova/dashboard
npm run dev
```

## Step 5: Verify

```bash
# Health check
curl http://localhost:8001/health

# List projects
curl http://localhost:8001/mcp/projects
```

Open http://localhost:5173 to verify the dashboard loads.

## Step 6: Configure Claude Code MCP

The `.mcp.json` file in the project root configures the MCP server for Claude Code.
Restart Claude Code in the `vaxxinova/` directory so it picks up the config.

Test with:
```
> Use the intodoc MCP tools to list projects
```

## Notes

- The MCP server (stdio mode) is started automatically by Claude Code via `.mcp.json`
- The REST API server (port 8001) must be started manually for the dashboard
- Ollama must be running with `nomic-embed-text` pulled for RAG embeddings
