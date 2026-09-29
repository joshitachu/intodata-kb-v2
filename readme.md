# IntoData Knowledge Base

Human quickstart: how to install, start, and smoke-test the platform locally.
This file is the single home for start commands and local ports; other docs
point here.

- Platform overview, services table, and repo rules: `CLAUDE.md`
- System architecture: `ARCHITECTURE.md`
- Production deployment: `deploy/RUNBOOK.md`

## Ports

| Service | How it runs | Port |
| --- | --- | --- |
| RAG backend (FastAPI) | `uvicorn` or Compose `backend` | 8000 |
| RAG frontend (Next.js) | `npm run dev` or Compose `frontend` | 3000 |
| MCP server / REST API | `python -m mcp_server --serve` or Compose `mcp-server` | 8002 |
| Dashboard (Vite dev server) | `npm run dev` in `knowledge_base/dashboard` | 5173 |
| Dashboard (built, nginx container) | Compose `dashboard` | 8001 |
| PostgreSQL / Qdrant / Hindsight | Compose | 5432 / 6333, 6334 / 8888, 9999 |

Compose publishes every port on `127.0.0.1` only. The full list of Compose
services is in the services table in `CLAUDE.md`.

## Option A: Full stack in Docker

```bash
cp .env.production.template .env   # then set at least POSTGRES_PASSWORD
docker compose up -d
docker compose down                # stop
```

For local use set `APP_ENV=development` and `LICENCE_ENFORCEMENT=off` in `.env`;
root `docker-compose.yml` defaults licence enforcement to `enforce`.
The backend and MCP server reach Ollama on the host at
`host.docker.internal:11434`, so run Ollama on the host if you use it.

## Option B: Services from source

### Install

From the repository root:

```bash
cd RAG_pipeline/backend && pip install -e ".[dev]" && cd -
cd RAG_pipeline/frontend && npm install && cd -
cd knowledge_base/intodoc && pip install -e . && cd -
cd knowledge_base/mcp-server && pip install -e . && cd -
cd knowledge_base/dashboard && npm install && cd -
```

### Infrastructure

```bash
docker compose up -d postgres qdrant hindsight
```

Point the backend at it in `RAG_pipeline/backend/.env` (for example
`DATABASE_URL=postgresql+asyncpg://admin:<POSTGRES_PASSWORD>@localhost:5432/intodata_kb`).
`RAG_pipeline/frontend/.env.example` shows the one frontend variable
(`FASTAPI_URL=http://localhost:8000`).

### Run database migrations

```bash
cd RAG_pipeline/backend
alembic upgrade head
```

### Start services

Run each service from the repository root in a separate terminal.

```bash
cd RAG_pipeline/backend
python -m uvicorn src.api.main:app --reload --port 8000
```

```bash
cd RAG_pipeline/frontend
npm run dev                         # http://localhost:3000
```

```bash
cd knowledge_base/mcp-server
python -m mcp_server --serve        # REST API on :8002
python -m mcp_server --mcp          # optional: stdio MCP mode (debug)
```

```bash
cd knowledge_base/dashboard
npm run dev                         # http://localhost:5173
```

In development (`APP_ENV=development`) the backend seeds an `admin` user whose
password is `ADMIN_DEFAULT_PASSWORD` (default `admin`).

## Smoke Test

```bash
curl http://localhost:8000/health
curl http://localhost:8002/health
```

Every `/mcp/*` route requires the `access_token` cookie issued by the RAG
backend login. Both services must use the same signing secret: `JWT_SECRET_KEY`
for the backend, `MCP_JWT_SECRET_KEY` for the MCP server (the built-in
development defaults match; Compose feeds both from `JWT_SECRET_KEY`).

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'

curl -b cookies.txt http://localhost:8002/mcp/projects
```

Register a local project by passing a path that exists on your machine:

```bash
curl -b cookies.txt -X POST http://localhost:8002/mcp/add-repo \
  -H "Content-Type: application/json" \
  -d '{"url": "./path/to/project", "project_id": "example", "platform": "talend"}'
```

Trigger a scan:

```bash
curl -b cookies.txt -X POST http://localhost:8002/mcp/scan \
  -H "Content-Type: application/json" \
  -d '{"project_id": "example", "force": true}'
```

Check scan status:

```bash
curl -b cookies.txt http://localhost:8002/mcp/status/example
```

Run a scan directly with the CLI:

```bash
cd knowledge_base/intodoc
intodoc scan <source-dir> --platform talend --output-dir ../intodoc-output
```

## Dashboard

Open `http://localhost:5173`, add a repo using a project path from your
machine, then run scan, push to RAG, or generate docs from the dashboard.

## RAG Markdown Ingest

With `MCP_INGEST_API_KEY` unset the endpoint is open; when it is set, add
`-H "Authorization: Bearer <MCP_INGEST_API_KEY>"`.

```bash
curl -X POST http://localhost:8000/api/v1/ingest/md \
  -H "Content-Type: application/json" \
  -d '{"text": "# Test Job\nThis job loads payment data.", "filename": "test_job.md", "source": "intodoc:example", "project_id": "example"}'
```

## Tests

```bash
cd RAG_pipeline/backend && pytest
cd RAG_pipeline/frontend && npm test
cd knowledge_base/dashboard && npm test
```

## Common Issues

| Area | Likely issue | How to check |
| --- | --- | --- |
| `pip install -e .` | Missing MCP package | Install project dependencies for the active environment |
| `alembic upgrade head` | Database connection refused | Make sure PostgreSQL is running and `DATABASE_URL` is correct |
| `/mcp/*` returns 401 | No or invalid `access_token` cookie | Log in via the backend; check `JWT_SECRET_KEY` and `MCP_JWT_SECRET_KEY` match |
| `/mcp/scan` | `intodoc` command not found | Install `knowledge_base/intodoc` into the active environment |
| `/mcp/generate` | Provider/API key/model configuration issue | Check the selected generation provider, model catalogue, and required API key env vars |
| Chat returns 402 | Licence enforcement is on | Set `LICENCE_ENFORCEMENT=off` for local dev (see `deploy/RUNBOOK.md` section 17) |
| Dashboard | API errors | Check MCP server logs |
| `/ingest/md` | Embedding service unavailable | Check the configured embedding provider (`EMBEDDING_PROVIDERS`, default Voyage then Ollama) |
