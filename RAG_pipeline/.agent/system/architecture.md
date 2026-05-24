# RAG Pipeline -- Architecture

## Overview

IntoData RAG is a retrieval-augmented generation system for querying company documents
using natural language. It is one of two sub-systems in the intodata-kb platform
(the other is the intodoc knowledge base scanner).

## Services (docker-compose)

| Service    | Image/Build           | Port(s)    | Purpose                          |
|------------|-----------------------|------------|----------------------------------|
| postgres   | postgres:16-alpine    | 5432       | Auth, RBAC, audit, usage, config |
| qdrant     | qdrant/qdrant         | 6333, 6334 | Vector storage + search          |
| hindsight  | vectorize-io/hindsight| 8888, 9999 | Long-context process memory      |
| backend    | RAG_pipeline/backend  | 8000       | FastAPI RAG API                  |
| frontend   | RAG_pipeline/frontend | 3000       | Next.js 15 UI                    |
| ollama     | host-level            | 11434      | Embedding + LLM inference        |

Ollama runs on the host, not in Docker. Containers reach it via `host.docker.internal:11434`.

## RAG Flow

```
User query (text)
  -> Embed query (Ollama nomic-embed-text, 768-dim)
  -> Search Qdrant (top-K=5, cosine >= 0.7, RBAC filter on allowed_groups)
  -> Reranker (cross-encoder, re-scores retrieved chunks)
  -> Query expander (generates sub-queries for multi-hop reasoning)
  -> Build prompt (system + context chunks + user query, <= 3000 tokens)
  -> LLM (Ollama gemma3:12b) -> streamed answer + source citations
  -> Persist QueryUsage (tokens, cost, latency)
```

## Document Ingestion Flow

```
File upload (PDF/DOCX/PPTX)
  -> Parser (python-docx, pdfplumber, python-pptx)
  -> Chunker (500 tokens, 50 overlap, sentence boundaries)
  -> Metadata extraction (filename, file_type, source, department, uploader)
  -> Embed chunks (Ollama nomic-embed-text)
  -> Store in Qdrant (collection: documents, payload: metadata + allowed_groups)
  -> Store document record in PostgreSQL
  -> Update IngestionJob status
```

## Authentication Flow

```
Login POST /auth/login
  -> bcrypt verify password
  -> Check MFA required (if totp_secret set)
    -> If MFA: return mfa_token JWT, client POSTs /auth/verify-mfa with TOTP code
    -> If no MFA: set access + refresh httpOnly cookies directly
  -> Access token: 60min, cookie on /
  -> Refresh token: 7d, cookie on /api/v1/auth/refresh, hash stored in DB
  -> Token rotation: refresh generates new pair, old refresh invalidated
```

## Database Models

All models are in `backend/src/db/models/`. AsyncAlchemy + alembic for migrations.

| Model           | Table              | Purpose                                  |
|-----------------|--------------------|------------------------------------------|
| User            | users              | Auth, roles M2M, TOTP secret             |
| Role            | roles              | Named roles with permissions             |
| Permission      | permissions        | Codename-based permission definitions    |
| Project         | projects           | Document namespaces for RBAC             |
| Document        | documents          | Document metadata (vectors in Qdrant)    |
| IngestionJob    | ingestion_jobs     | Track ingest status per document         |
| QueryLog        | query_logs         | Audit trail for all RAG queries          |
| QueryUsage      | query_usage        | Token/cost/latency per query             |
| Conversation    | conversations      | Multi-turn chat sessions                 |
| BenchmarkRun    | benchmark_runs     | Automated quality evaluation results     |
| Onboarding      | onboarding         | Per-user wizard completion state         |
| SystemConfig    | system_config      | Key/value system-wide configuration      |
| WizardEvent     | wizard_events      | Telemetry for onboarding wizard steps    |
| Feedback        | feedback           | User thumbs up/down on RAG responses     |
| Notification    | notifications      | In-app notification records              |
| RefreshToken    | refresh_tokens     | SHA-256 hashes of active refresh tokens  |
| AccessGroup     | access_groups      | RBAC group ↔ project assignments         |

## Qdrant Schema

Collection: `documents`

Payload fields per vector point:
- `document_id` (int) — FK to PostgreSQL documents table
- `chunk_index` (int) — position within document
- `text` (str) — raw chunk text
- `filename` (str)
- `file_type` (str: pdf/docx/pptx)
- `source` (str)
- `department` (str)
- `allowed_groups` (list[str]) — RBAC filter field; every search must filter on this

Search filter applied to every query:
```python
Filter(must=[FieldCondition(key="allowed_groups", match=MatchAny(any=user_groups))])
```

## Frontend Architecture

Next.js 15 App Router with BFF (Backend-For-Frontend) pattern:

```
Browser (React) -> /app/api/*/route.ts (Next.js route handlers) -> FastAPI :8000
```

Client components never call FastAPI directly. Route handlers proxy all requests
and own server-side auth cookie handling. `lib/fastapi.ts` is the shared server-side
fetch helper. `FASTAPI_URL` is a server-only env var (no `NEXT_PUBLIC_` prefix).

## Key Design Constraints

- No LangChain — direct implementation (see `decisions/001-no-langchain.md`)
- No external embedding API — Ollama only (no API cost, runs on-prem)
- RBAC filter on every Qdrant query without exception
- Async all the way: asyncpg + asyncio, no sync I/O on hot paths
- JWT in httpOnly cookies — no localStorage token storage
- One admin bootstrap on first deploy; no open registration
