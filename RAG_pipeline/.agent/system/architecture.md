# RAG Pipeline -- Architecture Notes

RAG-specific notes that are not in the platform docs. For everything else:

- Services, ports, and the health boundary: root `CLAUDE.md` (services table)
- Ingestion, retrieval, generation flow and search parameters: root `ARCHITECTURE.md`
  sections 4-6 and 10 (parameter defaults are in `backend/src/config.py`)
- Architecture decisions: `knowledge_base/.agent/decisions/`

## Authentication Flow

```
Login POST /auth/login
  -> bcrypt verify password
  -> Check MFA required (if totp_secret set)
    -> If MFA: return mfa_token JWT, client POSTs /auth/verify-mfa with TOTP code
    -> If no MFA: set access + refresh httpOnly cookies directly
  -> Access token: 60min (ACCESS_TOKEN_EXPIRE_MINUTES), cookie on /
  -> Refresh token: 14d (REFRESH_TOKEN_EXPIRE_DAYS), cookie on /, hash stored in DB
  -> CSRF: login/refresh also set a non-httpOnly csrf_token cookie (double-submit, src/api/middleware/csrf.py)
  -> Token rotation: refresh generates new pair, old refresh invalidated
```

## Database Models

All models are in `backend/src/db/models/`. SQLAlchemy async + Alembic for migrations.
Main tables (not exhaustive; harness, runtime, guidance and knowledge-revision
tables are in the same directory):

| Model           | Table              | Purpose                                  |
|-----------------|--------------------|------------------------------------------|
| User            | users              | Auth, roles M2M, TOTP secret             |
| Role            | roles              | Named roles with permissions             |
| Permission      | permissions        | Codename-based permission definitions    |
| ProjectAssignment | project_assignments | User-project access (owner or member) |
| Document        | documents          | Document metadata (vectors in Qdrant)    |
| IngestionJob    | ingestion_jobs     | Track ingest status per document         |
| QueryLog        | query_logs         | Audit trail for all RAG queries          |
| QueryUsage      | query_usage        | Token/cost/latency per query             |
| Conversation    | conversations      | Multi-turn chat sessions                 |
| BenchmarkRun    | benchmark_runs     | Automated quality evaluation results     |
| UserOnboarding  | user_onboarding    | Per-user wizard completion state         |
| SystemConfig    | system_config      | Key/value system-wide configuration      |
| InstanceSetting | instance_settings  | Superadmin instance overrides (e.g. `rag_chat_model`) |
| WizardEvent     | wizard_events      | Telemetry for onboarding wizard steps    |
| Feedback        | feedback           | User feedback on RAG responses           |
| Notification    | notifications      | In-app notification records              |
| RefreshToken    | refresh_tokens     | SHA-256 hashes of active refresh tokens  |

The `projects` table belongs to the MCP server (`knowledge_base/mcp-server/src/mcp_server/db/models.py`)
in the same Postgres database.

## Qdrant Schema

Collection: `documents` (`QDRANT_COLLECTION`). Named vectors: `dense` (cosine) and
`sparse` (BM25). Written by `src/retrieval/vector_store.py`.

Base payload per point (always set; overwrites caller metadata so RBAC keys cannot be spoofed):
- `document_id` (int) -- FK to PostgreSQL documents table
- `filename` (str)
- `chunk_index` (int) -- position within document
- `text` (str) -- raw chunk text
- `allowed_groups` (list[str]) -- RBAC filter field

Caller metadata merged in at ingest, with payload indexes: `project_id`,
`source_layer`, `document_role`, `knowledge_revision_id`, `artifact_id`, `unit`, `section`.

Every search goes through `_build_search_filter()` in `src/retrieval/search.py`,
which combines the RBAC `allowed_groups` match (skipped for admins), project
scope, corpus layer (`source_layer`, default `canonical_docs` plus legacy chunks),
document role, and active knowledge revision per project.

## Frontend Architecture

Next.js 15 App Router with BFF (Backend-For-Frontend) pattern:

```
Browser (React) -> /app/api/*/route.ts (Next.js route handlers) -> FastAPI :8000
```

Client components never call FastAPI directly. Route handlers proxy all requests
and own server-side auth cookie handling. `lib/fastapi.ts` is the shared server-side
fetch helper. `FASTAPI_URL` is a server-only env var (no `NEXT_PUBLIC_` prefix).

## Key Design Constraints

- No LangChain -- direct implementation (ADR `knowledge_base/.agent/decisions/022-no-langchain-configurable-provider-chain.md`)
- Model providers are configuration (defaults: Kimi chat, Voyage embeddings/reranker, Ollama local option)
- RBAC filter on every Qdrant query without exception
- Async all the way: asyncpg + asyncio, no sync I/O on hot paths
- JWT in httpOnly cookies -- no localStorage token storage
- One admin bootstrap on first deploy; no open registration
