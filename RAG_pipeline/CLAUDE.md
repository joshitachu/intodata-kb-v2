# IntoData Knowledge Base

> RAG-powered knowledge base for querying company documents using natural language with local LLMs.

## Quick Facts

- **Stack**: FastAPI, React + TypeScript, Qdrant, PostgreSQL, Ollama
- **Start**: `docker-compose up -d && cd backend && uvicorn src.api.main:app --reload`
- **Frontend**: `cd frontend && npm run dev`
- **Test**: `pytest` (backend) | `npm test` (frontend)

## Project Structure
```
intodata-kb/
├── .claude/
│   ├── context.md             # Current state, blockers, next steps
│   ├── progress.md            # High-level phase tracking
│   ├── PROGRESS_LOG.md        # Detailed session-by-session log
│   └── PROJECT_MEMORY.md      # Architecture decisions, patterns, gotchas
├── backend/
│   ├── src/
│   │   ├── api/routes/        # FastAPI endpoints (query, auth, admin, conversations, benchmark, ingest)
│   │   ├── auth/              # JWT auth (service.py, security.py)
│   │   ├── services/          # Audit service
│   │   ├── db/models/         # User, Role, Permission, QueryUsage, BenchmarkRun, etc.
│   │   ├── ingestion/         # Document processing
│   │   ├── retrieval/         # Vector search
│   │   ├── generation/        # LLM client, usage tracking, RAG chain
│   │   └── rbac/              # Access control (permissions.py, auth.py)
│   ├── benchmark/             # Benchmarking framework (runner, judge, report, corpus)
│   └── tests/
└── frontend/
    └── tests/
```

## Architecture

**RAG Flow**: User Query → Embed (Ollama) → Search (Qdrant) → Context → LLM (Ollama) → Answer

**Tech Choices**:
- Local models (no API costs): `nomic-embed-text` + `llama3.1:8b`
- No LangChain (direct implementation, full control)
- React (not Streamlit) for production-ready UX

---

## MANDATORY: Logging & Documentation Rules

**These rules are non-negotiable. Every session MUST follow them.**

### 1. Read Before Working

At the **start of every session**, before writing any code, read these files:
- `.claude/context.md` — current state, blockers, what to work on next
- `.claude/progress.md` — high-level progress tracker
- `.claude/PROGRESS_LOG.md` — detailed session history
- `.claude/PROJECT_MEMORY.md` — architecture decisions, patterns, lessons learned

### 2. Log Every Change

After **every change** (bug fix, feature, refactor, config change — no matter how small), immediately update:

#### `.claude/PROGRESS_LOG.md` — Append a new entry with:
- **Date & time** of the change
- **What was changed**: exact file(s) modified, with paths
- **What was the problem**: the bug, error message, or missing feature — be specific
- **What was the fix/change**: describe the exact code change made
- **Why**: the reasoning behind the approach
- **Root cause** (for bugs): what caused the issue in the first place
- **Verification**: how you confirmed the fix works (tests run, manual check, etc.)

Format:
```markdown
## Session: YYYY-MM-DD

### [Fix/Feature/Refactor]: Short description

- **Files changed**: `path/to/file.py` (lines X-Y)
- **Problem**: Describe the error or missing behavior
- **Root cause**: Why the problem existed
- **Change**: Describe exactly what was added/modified/removed
- **Why this approach**: Reasoning for the chosen solution
- **Verification**: How it was confirmed working
```

#### `.claude/context.md` — Update:
- Current state / status
- Any new blockers
- What should be done next

#### `.claude/PROJECT_MEMORY.md` — Update if applicable:
- New architecture decisions (add to the decisions table)
- New lessons learned / gotchas (add to the gotchas section)
- New completed tasks (add to the completed tasks table)

#### `.claude/progress.md` — Update:
- Check off completed items
- Add new items if scope changed

### 3. Never Skip Logging

- A "quick fix" still gets logged. There are no exceptions.
- If you fix a typo, log it. If you add an import, log it. If you change a config value, log it.
- If you're unsure whether to log something, log it.
- The log is the project's memory. Without it, we lose context between sessions.

### 4. Log Format Rules

- Use **specific file paths**, not vague descriptions ("updated the backend")
- Include **error messages** verbatim when fixing bugs
- Include **before/after** for non-obvious changes
- Reference **related issues** if the change connects to a previous log entry

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

## Multi-Agent Workflow

Work as ONE agent at a time. Each agent has its own folder with detailed instructions.

**Start with**: Infrastructure Agent → then choose based on task

See `.claude/agents/[agent-name]/instructions.md` for specific agent tasks.

## Common Commands
```bash
# Dev
uvicorn src.api.main:app --reload    # Backend
python -m uvicorn src.api.main:app --reload
npm run dev                          # Frontend

# Infra
docker-compose up -d                 # Start Qdrant + PostgreSQL
ollama pull nomic-embed-text         # Pull embeddings model
ollama pull llama3.1:8b              # Pull LLM

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
OLLAMA_LLM_MODEL=llama3.1:8b
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql://postgres:changeme@localhost:5432/intodata_kb

# Auth
JWT_SECRET_KEY=change-me-in-production-use-a-strong-random-key
ADMIN_DEFAULT_PASSWORD=admin
COOKIE_DOMAIN=                    # Empty for localhost, set for production domain
COOKIE_SECURE=false               # Set true when behind HTTPS

# frontend/.env
VITE_API_URL=http://localhost:8000/api/v1
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

---

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)
