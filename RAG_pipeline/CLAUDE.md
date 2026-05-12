# IntoData Knowledge Base

> RAG-powered knowledge base for querying company documents using natural language with local LLMs.

## Quick Facts

- **Stack**: FastAPI, React + TypeScript, Qdrant, PostgreSQL, Ollama
- **Start**: `docker-compose up -d && cd backend && uvicorn src.api.main:app --reload`
- **Frontend**: `cd frontend && npm run dev`
- **Test**: `pytest` (backend) | `npm test` (frontend)

## Project Structure
```
RAG_pipeline/
├── .claude/
│   ├── context.md             # Current state, blockers, next steps
│   ├── progress.md            # High-level phase tracking
│   ├── PROGRESS_LOG.md        # Detailed session-by-session log
│   └── PROJECT_MEMORY.md      # Architecture decisions, patterns, gotchas
├── backend/
│   ├── src/
│   │   ├── api/routes/        # FastAPI endpoints:
│   │   │   # auth, query, ingest, admin, conversations, benchmark,
│   │   │   # health, feedback, documents, notifications, telemetry,
│   │   │   # projects_api, tenant, memory, usage,
│   │   │   # onboarding, company_onboarding
│   │   ├── auth/              # JWT auth (service.py, security.py, totp.py)
│   │   ├── services/          # audit, email, notification, onboarding, system_config
│   │   ├── db/models/         # user, access, benchmark, conversation, document,
│   │   │   #                    feedback, ingestion_job, onboarding, query_log,
│   │   │   #                    query_usage, system_config, wizard_event
│   │   ├── ingestion/         # Document processing
│   │   ├── retrieval/         # Vector search + embedder + reranker + query_expander
│   │   ├── generation/        # LLM client, usage tracking, RAG chain, conversational RAG
│   │   ├── memory/            # Hindsight long-context memory (hindsight_client.py)
│   │   └── rbac/              # Access control (permissions.py, auth.py)
│   ├── benchmark/             # Benchmarking framework (runner, judge, report, corpus)
│   └── tests/
└── frontend/                  # Next.js 15 app
    ├── app/                   # Pages + API route handlers (app/api/* proxies to FastAPI)
    ├── components/            # UI components (chat, onboarding, admin, layout)
    ├── contexts/              # React contexts (auth, chat, onboarding, tenant)
    ├── hooks/                 # Custom hooks (usePermissions, useSystemConfig, etc.)
    └── types/                 # TypeScript type definitions
```

## Architecture

**RAG Flow**: User Query → Embed (Ollama) → Search (Qdrant) → Context → LLM (Ollama) → Answer

**Tech Choices**:
- Local models (no API costs): `nomic-embed-text` + `gemma3:12b`
- No LangChain (direct implementation, full control)
- React (not Streamlit) for production-ready UX

---

## MANDATORY: Logging & Documentation Rules

See root `CLAUDE.md` (section "Logging Rules") for the full logging format and rules. They apply here without exception — same format, same discipline.

**Before coding**: read `.claude/context.md`, `.claude/PROGRESS_LOG.md`, `.claude/PROJECT_MEMORY.md`, `.claude/progress.md`.

**After every change**: update `PROGRESS_LOG.md` (files changed, problem, root cause, fix, verification) and `context.md`.

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
6. **Logging**: Log EVERY change to `.claude/PROGRESS_LOG.md` — no exceptions (see Logging rules above)
7. **Context**: Read all `.claude/` files at session start, update them after every change
8. **Specificity**: Always reference exact file paths, line numbers, and error messages in logs

## Agent Workflow

Work as ONE agent at a time. Read `.claude/context.md` to determine what to work on next.

## Common Commands
```bash
# Dev
uvicorn src.api.main:app --reload    # Backend
python -m uvicorn src.api.main:app --reload
npm run dev                          # Frontend

# Infra
docker-compose up -d                 # Start Qdrant + PostgreSQL
ollama pull nomic-embed-text         # Pull embeddings model
ollama pull gemma3:12b               # Pull LLM

# DB
alembic upgrade head                 # Run migrations
alembic revision -m "msg"            # Create migration

# Test
pytest                               # Backend tests
npm test                             # Frontend tests
```

## Deployment Foundation Notes (EP-001)

- Canonical deployment now uses nginx/certbot from root `deploy/deploy-client.sh`; Caddy is legacy/deferred.
- RAG backend/frontend are two of the six Docker services validated by `deploy/SMOKE-TEST.md`.
- Ollama remains host-level and must be reachable from containers at `host.docker.internal:11434`.
- Backend `/health` is part of EP-001 acceptance and should verify database, Qdrant, Ollama, and Hindsight state.
- EP-001 remains open until the generic runbook is executed on a clean reference VPS.

## Environment
```bash
# backend/.env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=gemma3:12b
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql://postgres:changeme@localhost:5432/intodata_kb

# Auth
JWT_SECRET_KEY=change-me-in-production-use-a-strong-random-key
ADMIN_DEFAULT_PASSWORD=admin
COOKIE_DOMAIN=                    # Empty for localhost, set for production domain
COOKIE_SECURE=false               # Set true when behind HTTPS

# frontend/.env (Next.js — server-side only, no NEXT_PUBLIC_ prefix)
FASTAPI_URL=http://localhost:8000
```

## Key Features

- **Authentication**: JWT httpOnly cookies (access 60min + refresh 7d rotation), bcrypt passwords, rate-limited login (5/min), admin user management panel. Default creds: admin/admin
- **RBAC**: 3 roles (admin, manager, developer), 9 permissions, project-level access control
- **Token/Cost Tracking**: Automatic usage persistence on all query paths, admin usage dashboard
- **Benchmarking**: CLI framework for precision@5, LLM-as-judge relevance, latency, cost metrics
- Document ingestion (PDF, DOCX, PPTX)
- Text chunking (500 tokens, 50 overlap)
- Vector search with RBAC filtering
- RAG with local LLM
- Source citations in responses

---

## Auth System

JWT httpOnly cookie authentication with token rotation:

- **Access token**: 60min expiry, stored in httpOnly cookie on `/`
- **Refresh token**: 7-day expiry, stored in httpOnly cookie on `/api/v1/auth/refresh`, rotated on use
- **Endpoints**: `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `GET /auth/me`
- **Dependencies**: `CurrentUser`, `AdminUser`, `ProjectManager`, `ManagerUser` (in `src/api/dependencies.py`)
- **Cookie settings**: `COOKIE_DOMAIN`, `COOKIE_SECURE`, `samesite=lax`
- **Rate limiting**: 5/min login, 10/min refresh (via slowapi)
- **Password hashing**: bcrypt
- **Token storage**: Refresh token SHA-256 hashes in `refresh_tokens` table

## RBAC System

Role-based access control with 3 default roles and 9 permissions:

- **Roles**: admin (all perms), manager (project management), developer (query + view)
- **Permissions**: `manage_users`, `manage_roles`, `assign_projects`, `view_all_projects`, `manage_projects`, `view_assigned_projects`, `query_rag`, `ingest_documents`, `view_audit_logs`
- **Project assignments**: Filter Qdrant queries via `_build_search_filter()` in `src/retrieval/search.py`
- **Admin endpoints**: User CRUD (`/admin/users`), Role CRUD (`/admin/roles`), project assignments (`/admin/project-assignments`), audit logs (`/admin/audit-logs`), permissions listing (`/admin/permissions`)
- **Permission checks**: `has_permission()`, `get_user_permissions()`, `can_view_all_projects()` in `src/rbac/permissions.py`
- **Superadmin**: Bypasses all permission checks

## Token/Cost Tracking

Automatic token usage and cost tracking for all LLM interactions:

- **Model**: `QueryUsage` in `src/db/models/query_usage.py` (15 fields: user_id, interface, query_text, model_name, provider, input/output/embedding tokens, cost_usd, latency/embedding/search/llm ms, project_id, conversation_id)
- **Persistence**: Automatic on all query paths:
  - Non-streaming RAG query (`rag_chain.py:run_rag_query()`)
  - Non-streaming conversation (`conversational_rag.py:run_conversational_rag()`)
  - Streaming conversation (`conversational_rag.py:run_conversational_rag_stream()`)
- **Usage tracking**: `UsageStats` / `StreamUsageStats` dataclasses in `src/generation/usage.py`
- **Cost calculation**: `MODEL_PRICING` table with per-million-token pricing (Kimi, Ollama, Voyage)
- **SSE event**: `event: usage` emitted after stream completes (input_tokens, output_tokens, model, provider, cost_usd, latency_ms)
- **Admin endpoints**: `GET /admin/usage/summary` (aggregated by interface/model/day), `GET /admin/usage/recent` (paginated list)
- **Thread-local storage**: `get_last_usage()` in `llm_client.py` returns stats from the most recent LLM call

## Benchmarking

Automated RAG quality benchmarking framework:

- **CLI**: `python -m benchmark run|list|show|compare` (in `backend/benchmark/`)
- **Model**: `BenchmarkRun` in `src/db/models/benchmark.py` with JSONB results field
- **Metrics**: precision@5, LLM-as-judge relevance (1-5 scale), latency, tokens, cost
- **Test corpus**: `benchmark/corpus/default.json` (10 Q&A pairs)
- **API**: `GET /benchmark/runs` (list all), `GET /benchmark/runs/{id}` (details with per-question results)
- **Frontend**: `/admin/benchmark` page with runs list + detail drill-down, summary cards, color-coded relevance

## External Usage Recording

The KB MCP server posts usage records for doc generation and deep dive to the RAG backend:

- **Endpoint**: `POST /api/v1/usage/record` (Bearer auth via `MCP_INGEST_API_KEY`)
- **Accepts**: `interface`, `model_name`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `project_id`, `query_text`
- **Route**: `src/api/routes/usage.py`
- **Called by**: `RagBridge.post_usage()` in `knowledge_base/mcp-server`

## Embedding Token Tracking

Embedding token counts from Voyage AI and Ollama are captured per call:

- **Storage**: Thread-local `_last_embed_tokens` in `src/retrieval/embedder.py`
- **Access**: `get_last_embed_tokens()` — returns token count from most recent embedding call
- **Populated**: All 4 `QueryUsage` creation sites include `embedding_tokens=get_last_embed_tokens()`

## Frontend Permission Guards

Admin UI elements are filtered by user permissions:

- **Hook**: `usePermissions()` in `frontend/hooks/usePermissions.ts` — wraps `useAuthContext()`, provides `hasPermission(codename)`, `hasAnyAdminPermission`, convenience booleans
- **Sidebar**: Admin sub-links filtered by required permission (Users→`manage_users`, Roles→`manage_roles`, Projects→`assign_projects`, Usage→`manage_users`, Benchmark→`manage_users`)
- **Admin layout**: Checks `hasAnyAdminPermission` (any admin permission grants access, not just `manage_users`)

## Onboarding System

Two-tier onboarding guards app access. Both wizards must complete before the main app is accessible.

**Flow**: Cold start → `OnboardingGuard` checks backend state → routes to appropriate wizard → app unlocks

### Tier 1: Admin/Company Wizard (`AdminOnboardingWizard.tsx`)
- Triggered on first boot when no system config exists
- Steps: Welcome → Platform select → Source upload → Workspace → Communication tools → Provisioning → Complete
- Backend: `POST /api/v1/company-onboarding/step/{step}`, `GET /api/v1/company-onboarding/status`
- Persists to `system_config` table via `system_config_service.py`
- Frontend API routes: `app/api/onboarding/company/` (complete, status, setup-status, step)

### Tier 2: User Welcome Wizard (`UserWelcomeWizard.tsx`)
- Triggered for each new user after company setup completes
- Steps: UserWelcomeStep (intro, tour, preferences)
- Backend: `POST /api/v1/onboarding/step/{step}`, `GET /api/v1/onboarding/status`
- Persists to `onboarding` table per user

### Guard (`OnboardingGuard.tsx`)
- Wraps the entire app
- Checks `/api/onboarding/setup-status` → if company not set up, shows `AdminOnboardingWizard`
- Checks `/api/onboarding/status` → if user not onboarded, shows `UserWelcomeWizard`
- Hook: `useSystemConfig.ts` for reading/updating system-wide config state

### Key files
- `src/api/routes/company_onboarding.py` — company setup endpoints
- `src/api/routes/onboarding.py` — per-user onboarding endpoints
- `src/db/models/system_config.py` — system-wide key/value config store
- `src/db/models/onboarding.py` — per-user onboarding state
- `src/services/system_config_service.py` — CRUD for system_config
- `src/services/onboarding_service.py` — onboarding step progression logic
- `frontend/components/onboarding/AdminOnboardingWizard.tsx`
- `frontend/components/onboarding/UserWelcomeWizard.tsx`
- `frontend/hooks/useSystemConfig.ts`

---

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)
