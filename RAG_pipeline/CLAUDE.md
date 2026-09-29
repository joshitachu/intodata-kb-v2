# IntoData Knowledge Base

> RAG-powered knowledge base for querying company documents using natural language over configurable LLM providers.

## Quick Facts

- **Stack**: FastAPI, Next.js + TypeScript, Qdrant, PostgreSQL; model providers configurable (defaults below)
- **Start, ports, tests**: root `readme.md`
- **Architecture**: root `ARCHITECTURE.md` (sections 4-6 and 10 cover this sub-project)

## Project Structure
```
RAG_pipeline/
├── backend/
│   ├── src/
│   │   ├── api/routes/        # FastAPI endpoints:
│   │   │   # auth, query, ingest, admin, conversations, benchmark,
│   │   │   # health, feedback, documents, notifications, telemetry,
│   │   │   # projects_api, tenant, memory, usage,
│   │   │   # onboarding, company_onboarding, runtime source management
│   │   ├── auth/              # JWT auth (service.py, security.py, totp.py)
│   │   ├── harness/           # Agent chat runtime behind /conversations (tools, grounding, plans)
│   │   ├── licensing/         # Subscription licence verifier + enforcement (see below)
│   │   ├── services/          # audit, email, notification, onboarding, system_config
│   │   ├── db/models/         # user, access, benchmark, conversation, document,
│   │   │   #                    feedback, ingestion_job, onboarding, query_log,
│   │   │   #                    query_usage, system_config, wizard_event
│   │   ├── ingestion/         # Document processing
│   │   ├── retrieval/         # Vector search + embedder + reranker + query_expander
│   │   ├── generation/        # LLM client + provider adapters, usage tracking, RAG chain, map-reduce, citation audit
│   │   ├── memory/            # Hindsight long-context memory (hindsight_client.py)
│   │   ├── runtime/           # Runtime mirror, source connectors, encrypted credentials,
│   │   │   #                    source sync, source audit, provider contracts
│   │   └── rbac/              # Access control (permissions.py, auth.py, persona.py)
│   ├── benchmark/             # Benchmarking framework (runner, judge, report, corpus)
│   └── tests/
└── frontend/                  # Next.js 15 app
    ├── app/                   # Pages + API route handlers (app/api/* proxies to FastAPI)
    ├── components/            # UI components (chat, onboarding, admin, layout)
    ├── contexts/              # React contexts (chat, onboarding, tenant; auth is hooks/useAuth.ts)
    ├── hooks/                 # Custom hooks (usePermissions, useSystemConfig, etc.)
    └── types/                 # TypeScript type definitions
```

## Architecture

**RAG Flow**: User Query -> Embed (Voyage, Ollama fallback) -> Hybrid search (Qdrant dense + BM25) -> Rerank -> LLM (selected provider) -> Answer.
Details: root `ARCHITECTURE.md`.

**Tech Choices**:
- Model providers are configuration, not code. Defaults in `backend/src/config.py`:
  chat `RAG_DEFAULT_MODEL=kimi/kimi-k2.6` (superadmin override `rag_chat_model`),
  embeddings `EMBEDDING_PROVIDERS=["voyage","ollama"]`, reranker Voyage `rerank-2.5`.
  Ollama (`gemma3:4b`, `nomic-embed-text`) is the local option; it is not a silent
  fallback for chat answers (`RAG_APPROVED_FALLBACK_MODELS` is empty by default).
- No LangChain: `knowledge_base/.agent/decisions/022-no-langchain-configurable-provider-chain.md`
- React (not Streamlit) for production-ready UX

---

## Code Style

### Python
```python
# Always type hints
def process(path: str) -> list[Chunk]:
    pass

# Pydantic validation
class Request(BaseModel):
    query: str = Field(..., min_length=3)

# Never bare except
try:
    result = operation()
except SpecificError as e:
    logger.error(f"Failed: {e}", exc_info=True)
    raise

# Async for I/O
async def fetch(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(url) as resp:
            return await resp.json()
```

### TypeScript
```typescript
// Never 'any', use 'unknown'
function handle(data: unknown): void {
  if (isValid(data)) { /* ... */ }
}

// Explicit props
interface Props {
  onSubmit: (query: string) => Promise<void>;
  isLoading: boolean;
}

// Zod for API validation
const Schema = z.object({ answer: z.string() });
type Response = z.infer<typeof Schema>;

// Handle all states
const { data, isLoading, error } = useQuery();
return (
  <>
    {isLoading && <Spinner />}
    {error && <Error error={error} />}
    {data && <Results data={data} />}
  </>
);
```

## Critical Rules

1. **Type Safety**: Python type hints + TS strict mode (never `any`)
2. **Error Handling**: Never swallow errors, always log + show user feedback
3. **RBAC**: Filter EVERY Qdrant query by user groups
4. **Async**: Use async/await for all I/O operations
5. **Testing**: Write tests before committing
6. **Document changes**: update this file and the `.agent/` docs when behaviour or architecture changes
7. **Decisions**: architecture decisions live in `knowledge_base/.agent/decisions/`; check them before changing a design
8. **Specificity**: Always reference exact file paths, line numbers, and error messages in logs
9. **No workarounds**: same as root `CLAUDE.md` rule 9.
10. **Generalize fixes**: same as root `CLAUDE.md` rule 10.
11. **Use design patterns**: same as root `CLAUDE.md` rule 11.

## Agent Docs

Read `.agent/README.md` first — it tells you which docs to read for your task.

| You are working on...           | Read                                     |
|---------------------------------|------------------------------------------|
| Auth, RBAC, Onboarding          | `.agent/system/subsystems.md`            |
| An API endpoint                 | `.agent/system/api-reference.md`         |
| System architecture             | `.agent/system/architecture.md`          |
| A runtime failure               | `.agent/SOP/troubleshooting.md`          |
| Adding a new route              | `.agent/SOP/add-route.md`                |
| Architecture decisions          | `knowledge_base/.agent/decisions/`       |

## Common Commands

Start and test commands: root `readme.md`. Commands only relevant here:

```bash
alembic upgrade head                 # Run migrations (from backend/)
alembic revision -m "msg"            # Create migration
ollama pull nomic-embed-text         # Only if using the Ollama embedding/LLM option
ollama pull gemma3:4b
docker compose -f RAG_pipeline/docker-compose.yml up -d   # Dev-only 4-service stack (see root CLAUDE.md)
```

## Deployment Foundation Notes (EP-001)

EP-001 status, the health boundary, and the nginx/certbot path: root `CLAUDE.md`
and `deploy/RUNBOOK.md`. RAG-specific points:

- Ollama is host-level; containers reach it at `host.docker.internal:11434`.
- Backend `/health` (`src/api/routes/health.py`) checks database, Qdrant, Ollama
  (core) and Hindsight (degraded-only), and is part of `deploy/SMOKE-TEST.md`.

## Subscription Licence

ADR: `knowledge_base/.agent/decisions/037-subscription-licence-is-a-signed-file.md`.
Operator procedure: root `deploy/RUNBOOK.md` section 17.

- **Where it lives**: `backend/src/licensing/`. `core.py` holds the verifier, `EntitlementPolicy`
  and `FileLicenceSource`. `keys.py` holds the pinned public keys. `runtime.py` holds the process policy
  and the startup log line `licence: enforcement=... state=...`. The licence file is
  `LICENCE_FILE` (default `/etc/intodata-kb/licence/licence.jwt`, mounted read-only
  from the host). It is re-read when its mtime changes.
- **Enforcement**: every route that spends LLM tokens declares
  `require_capability(Capability.CHAT)` (`ChatEntitled` / `ChatEntitledUser` in
  `src/api/dependencies.py`). It is a dependency, so it refuses before an SSE stream
  opens. `tests/test_api/test_route_inventory.py` fails when a mutating route has
  no gate and is not on the reviewed allowlist. A new LLM route must add the gate.
  Do not extend the allowlist to get past the test.
- **402 contract and mirror rule**: see ADR-037 (refusal codes; never 401;
  `core.py`, `keys.py` and `golden_vectors.json` are byte-identical with the
  mcp-server copies, enforced by `tests/test_licensing/test_licence_mirror_parity.py`).
  `runtime.py` is service-specific and not mirrored. `GET /api/v1/licence/status`
  is open to any authenticated user.
- **Dev**: `LICENCE_ENFORCEMENT=off` (the Settings default for a bare `uvicorn` run; root `docker-compose.yml` defaults to `enforce`, so set `off` in `.env` with `APP_ENV=development`) disables every gate. To exercise
  enforcement locally, set `LICENCE_ENFORCEMENT=enforce` and `LICENCE_CLIENT_SLUG=<slug>`,
  and put a licence issued for that slug in `LICENCE_DIR` (compose default
  `./deploy/licence`). With `APP_ENV=production`, startup refuses `off` or an empty slug.

## Environment

Settings and their defaults are in `backend/src/config.py` (pydantic-settings,
reads `backend/.env`); the production contract is root `.env.production.template`.
Minimum for local dev:

```bash
# backend/.env
DATABASE_URL=postgresql+asyncpg://admin:<password>@localhost:5432/intodata_kb
QDRANT_URL=http://localhost:6333
KIMI_API_KEY=...                  # default chat model kimi/kimi-k2.6
VOYAGE_API_KEY=...                # embeddings (1024-dim) + reranker; without it embeddings fall back to Ollama (768-dim)
OLLAMA_BASE_URL=http://localhost:11434   # optional local provider
JWT_SECRET_KEY=change-me-in-production-use-a-strong-random-key
ADMIN_DEFAULT_PASSWORD=admin
COOKIE_DOMAIN=                    # Empty for localhost, set for production domain
COOKIE_SECURE=false               # Set true when behind HTTPS
LICENCE_ENFORCEMENT=off

# frontend/.env.local (Next.js, server-side only, no NEXT_PUBLIC_ prefix; see frontend/.env.example)
FASTAPI_URL=http://localhost:8000
```

## Key Features

- **Authentication**: JWT httpOnly cookies (access 60min + refresh 14d rotation), bcrypt passwords, rate-limited login (5/min), admin user management panel. Dev-mode seeded creds: admin/admin
- **RBAC**: 3 roles (admin, manager, developer), 9 permissions, project-level access control
- **Token/Cost Tracking**: Automatic usage persistence on all query paths, admin usage dashboard
- **Benchmarking**: CLI framework for precision@5, LLM-as-judge relevance, latency, cost metrics
- Document ingestion (PDF, DOCX, PPTX, XLSX, Markdown)
- Semantic / heading-aware chunking (max 512 tokens; `src/ingestion/semantic_chunker.py`, `markdown_chunker.py`)
- Hybrid vector search with RBAC filtering
- Agent chat runtime (`src/harness/`) with grounded, cited answers

## Subsystem Reference

Auth, RBAC, Token/Cost Tracking, Benchmarking, External Usage Recording,
Embedding Token Tracking, Frontend Permission Guards, and Onboarding System
details are in `.agent/system/subsystems.md`. Read that file when working on
those areas — it is not loaded at session start.

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)
