# RAG Pipeline -- Tech Stack Reference

## Backend

| Component      | Technology              | Version / Notes                        |
|----------------|-------------------------|----------------------------------------|
| Framework      | FastAPI                 | Async, auto OpenAPI at /docs           |
| Language       | Python                  | 3.11+ (use 3.11 for Docker)            |
| DB ORM         | SQLAlchemy (async)      | asyncpg driver, alembic migrations     |
| Validation     | Pydantic v2             | All request/response models            |
| Auth           | JWT (PyJWT)             | httpOnly cookies, bcrypt passwords     |
| MFA            | pyotp                   | TOTP; plaintext secret, hashed backups |
| Rate limiting  | slowapi                 | Per-route, per-IP                      |
| Task queue     | None (background tasks) | FastAPI BackgroundTasks for ingestion  |
| Build system   | hatchling               | Not setuptools                         |
| Tests          | pytest + pytest-asyncio | asyncio_mode = "auto"                  |

## Vector & Search

| Component      | Technology       | Notes                                     |
|----------------|------------------|-------------------------------------------|
| Vector DB      | Qdrant           | Collection: documents, cosine similarity  |
| Embedding      | nomic-embed-text | Via Ollama, 768 dimensions                |
| Reranker       | cross-encoder    | Re-scores top-K results                   |
| Query expander | LLM-based        | Multi-hop sub-query generation            |
| Threshold      | 0.7 cosine       | Results below this discarded              |
| Top-K          | 5                | Default retrieval count                   |
| Context limit  | 3000 tokens      | Max sent to LLM after retrieval           |

## LLM

| Component | Technology   | Notes                                          |
|-----------|--------------|------------------------------------------------|
| LLM       | gemma3:12b   | Via Ollama, runs on host at :11434             |
| Embedding | nomic-embed-text | Via Ollama, 768-dim                        |
| Fallback  | Provider chain | Kimi, OpenAI, Anthropic available via config |

Ollama runs on the host (not in Docker). Containers reach it via `host.docker.internal:11434`.
Models must be pulled before first use: `ollama pull nomic-embed-text && ollama pull gemma3:12b`.

## Frontend

| Component      | Technology          | Notes                                    |
|----------------|---------------------|------------------------------------------|
| Framework      | Next.js 15          | App Router, BFF pattern                  |
| Language       | TypeScript          | Strict mode, no `any`                    |
| Styling        | Tailwind CSS + shadcn/ui | Component library                   |
| State          | React Query (TanStack) | Loading/error/data states mandatory   |
| Validation     | Zod                 | All API responses, all request inputs    |
| Tests          | Vitest              | `npm test`                               |
| Build          | `npm run build`     | Fails on type errors                     |

## Infrastructure

| Component   | Technology           | Notes                                     |
|-------------|----------------------|-------------------------------------------|
| Database    | PostgreSQL 16-alpine | Via Docker, port 5432                     |
| Vectors     | Qdrant               | Via Docker, ports 6333 (REST), 6334 (gRPC)|
| Memory      | Hindsight            | vectorize-io, ports 8888/9999, long context|
| Reverse proxy| nginx               | SSL termination, prod only (deploy/)      |
| Container   | Docker Compose       | 7 services health-checked                 |

## Key Parameters Summary

```
Embedding model:    nomic-embed-text (768-dim)
LLM model:          gemma3:12b
Chunk size:         500 tokens
Chunk overlap:      50 tokens
Retrieval top-K:    5
Cosine threshold:   0.7
Context window:     3000 tokens max to LLM
Access token TTL:   60 minutes
Refresh token TTL:  7 days
Login rate limit:   5/min
Refresh rate limit: 10/min
```

## Environment Variables

```bash
# backend/.env
OLLAMA_BASE_URL=http://localhost:11434      # host-level for local dev
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=gemma3:12b
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql://postgres:changeme@localhost:5432/intodata_kb
JWT_SECRET_KEY=<strong random key>
COOKIE_DOMAIN=                              # empty for localhost
COOKIE_SECURE=false                         # true behind HTTPS
ADMIN_DEFAULT_PASSWORD=admin

# frontend/.env.local (server-side only — no NEXT_PUBLIC_ prefix)
FASTAPI_URL=http://localhost:8000
```
