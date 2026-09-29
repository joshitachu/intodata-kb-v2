# SOP: Local Dev Setup

Follow these steps to get the full RAG pipeline running locally from scratch.
Start commands and ports are canonical in root `readme.md`; this SOP adds the
RAG-specific configuration and checks.

## Prerequisites

- Docker Desktop running
- Python 3.11+ and Node 18+
- API keys for the default providers (`KIMI_API_KEY` for chat, `VOYAGE_API_KEY` for
  embeddings + reranker), or Ollama on the host (`brew install ollama`) as the local option
- `gh` CLI authenticated (for PR workflows)

---

## Step 1: Clone and configure environment

There is no backend env template. Create `RAG_pipeline/backend/.env` with the
minimum from the "Environment" section of `RAG_pipeline/CLAUDE.md`; every other
setting has a default in `backend/src/config.py`. For local dev:
- Set `DATABASE_URL` with the Postgres password you gave Compose
- Set a `JWT_SECRET_KEY` (any random string for local is fine)
- Leave `COOKIE_DOMAIN` empty and `COOKIE_SECURE=false`
- `LICENCE_ENFORCEMENT=off` (the Settings default) so chat is not refused with 402

---

## Step 2: Start infrastructure

```bash
docker compose up -d postgres qdrant hindsight
```

Wait ~10 seconds for postgres to initialize. Verify:

```bash
docker compose ps
```

All three should show `healthy` or `running`.

---

## Step 3: Pull Ollama models (only if using the local provider)

```bash
ollama pull nomic-embed-text
ollama pull gemma3:4b
```

Ollama embeddings are 768-dim, Voyage 1024-dim; a Qdrant collection must use one
dimension throughout, so switching providers requires re-indexing.

Verify:
```bash
ollama list
curl -s http://localhost:11434/api/tags | python3 -m json.tool
```

---

## Step 4: Run database migrations

```bash
cd RAG_pipeline/backend
pip install -e ".[dev]"
alembic upgrade head
```

Verify:
```bash
alembic current
```

Should show the latest revision as `(head)`.

---

## Step 5: Bootstrap admin user

With `APP_ENV=development` the backend seeds user `admin` with password
`ADMIN_DEFAULT_PASSWORD` (default `admin`) on startup (`src/api/main.py`). In
production no user is seeded; the first admin is created via
`POST /api/v1/auth/bootstrap-admin`.

---

## Step 6: Start backend

Start the backend as in root `readme.md` (port 8000). Verify health:
```bash
curl http://localhost:8000/health
```

Database, Qdrant and Ollama are core checks; Hindsight down only degrades the overall status.

---

## Step 7: Start frontend

```bash
cd RAG_pipeline/frontend
cp .env.example .env.local   # FASTAPI_URL=http://localhost:8000
```

Then install and start it as in root `readme.md` (port 3000).

Open http://localhost:3000. Log in with `admin` / `admin` (or your configured password).

---

## Step 8: Verify end-to-end

1. Upload a test document via the Documents page
2. Wait for ingestion to complete (poll the document status)
3. Run a query related to the document content
4. Verify you get an answer with source citations

---

## Common First-Run Issues

- **`alembic upgrade head` fails**: Postgres container not ready yet — wait 10s and retry.
- **Embedding connection refused**: check `VOYAGE_API_KEY`, or, when using Ollama, that `ollama serve` is running.
- **Frontend 500 on login**: `FASTAPI_URL` not set in `frontend/.env.local`.
- **Empty search results**: Document ingestion failed or Qdrant collection not created. Check backend logs.

See `SOP/troubleshooting.md` for more.
