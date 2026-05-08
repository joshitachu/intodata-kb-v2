# IntoData Knowledge Base

Development workflow for the local RAG backend, MCP server, and dashboard.

## Install

From the repository root:

```bash
cd knowledge_base/mcp-server
pip install -e .

cd ../dashboard
npm install
```

## Run Database Migrations

```bash
cd RAG_pipeline/backend
alembic upgrade head
```

## Start Services

Run each service from the repository root in a separate terminal.

```bash
cd RAG_pipeline/backend
python -m uvicorn src.api.main:app --port 8000
```

```bash
cd knowledge_base/mcp-server
python -m mcp_server --serve
```

```bash
cd knowledge_base/dashboard
npm run dev
```

## Smoke Test

```bash
curl http://localhost:8001/health
curl http://localhost:8001/mcp/projects
```

Register a local project by passing a path that exists on your machine:

```bash
curl -X POST http://localhost:8001/mcp/add-repo \
  -H "Content-Type: application/json" \
  -d '{"url": "./path/to/project", "project_id": "example", "platform": "talend"}'
```

Trigger a scan:

```bash
curl -X POST http://localhost:8001/mcp/scan \
  -H "Content-Type: application/json" \
  -d '{"project_id": "example", "force": true}'
```

Check scan status:

```bash
curl http://localhost:8001/mcp/status/example
```

## Dashboard

Open `http://localhost:5173`, add a repo using a project path from your machine, then run scan, push to RAG, or generate docs from the dashboard.

## RAG Markdown Ingest

```bash
curl -X POST http://localhost:8000/api/v1/ingest/md \
  -H "Content-Type: application/json" \
  -d '{"text": "# Test Job\nThis job loads payment data.", "filename": "test_job.md", "source": "intodoc:example", "project_id": "example"}'
```

## Common Issues

| Area | Likely issue | How to check |
| --- | --- | --- |
| `pip install -e .` | Missing MCP package | Install project dependencies for the active environment |
| `alembic upgrade head` | Database connection refused | Make sure PostgreSQL is running |
| `/mcp/scan` | `intodoc` command not found | Install `knowledge_base/intodoc` into the active environment |
| `/mcp/generate` | Claude CLI not in `PATH` | Install/configure the CLI used by the generation provider |
| Dashboard | API errors | Check MCP server logs |
| `/ingest/md` | Embedding service unavailable | Check the configured embedding provider |
