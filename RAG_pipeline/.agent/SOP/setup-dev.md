# SOP: Local Dev Setup

Follow these steps to get the full RAG pipeline running locally from scratch.

## Prerequisites

- Docker Desktop running
- Ollama installed (`brew install ollama` on Mac)
- Python 3.11+ and Node 18+
- `gh` CLI authenticated (for PR workflows)

---

## Step 1: Clone and configure environment

```bash
cd intodata-kb-v2
cp RAG_pipeline/backend/.env.template RAG_pipeline/backend/.env
```

Edit `backend/.env` — the defaults work for local dev except:
- Set a strong `JWT_SECRET_KEY` (any random string for local is fine)
- Leave `COOKIE_DOMAIN` empty and `COOKIE_SECURE=false`

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

## Step 3: Pull Ollama models

```bash
ollama pull nomic-embed-text
ollama pull gemma3:12b
```

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

The first user is created from `ADMIN_DEFAULT_PASSWORD` in `.env` on startup.
Default: `admin` / `admin`. Change in `.env` before starting if you want something else.

---

## Step 6: Start backend

```bash
cd RAG_pipeline/backend
uvicorn src.api.main:app --reload --port 8000
```

Verify health:
```bash
curl http://localhost:8000/health
```

All components should show `"status": "ok"`.

---

## Step 7: Start frontend

```bash
cd RAG_pipeline/frontend
cp .env.local.template .env.local   # or create manually with FASTAPI_URL=http://localhost:8000
npm install
npm run dev
```

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
- **`nomic-embed-text` connection refused**: Ollama not running. Start with `ollama serve`.
- **Frontend 500 on login**: `FASTAPI_URL` not set in `frontend/.env.local`.
- **Empty search results**: Document ingestion failed or Qdrant collection not created. Check backend logs.

See `SOP/troubleshooting.md` for more.
