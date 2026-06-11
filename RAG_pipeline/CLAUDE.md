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
9. **No workarounds**: Fixes must address the root cause, not suppress symptoms. If a proper fix requires broader refactoring, do it. Never ship a narrow patch when the defect pattern exists elsewhere.
10. **Generalize fixes**: Before fixing anything, check whether the same failure can occur in other places. Fix it at the level that prevents all recurrences — one-off special-case branches are not acceptable.
11. **Use design patterns**: Before writing a new class, service, or module, identify whether an established pattern fits (Strategy, Repository, Factory, Observer, Pipeline, etc.). Don't invent bespoke structures when a well-understood pattern solves the problem.
12. **Full pipeline gate**: Any non-Scout change requires the full pipeline: `/grill` → `/prd` → `/slices` → `/plan` → `/plan-approve pass` → build → `/inspect` → `/validate` → commit. See root `CLAUDE.md` Development Workflow.
13. **Inspector required**: After building any change to the RAG retrieval chain (`src/retrieval/`) or auth/RBAC (`src/auth/`, `src/rbac/`), run `/inspect` before committing. Max 2 block cycles before escalating to the human.
14. **Emergency path**: Use `/lfe-force` for production emergencies only; it logs to `PROTOCOL_DEBT.md` and session-start will warn until `/debt-resolve` clears it.

## Agent Docs

Read `.agent/README.md` first — it tells you which docs to read for your task.

| You are working on...           | Read                                     |
|---------------------------------|------------------------------------------|
| Auth, RBAC, Onboarding          | `.agent/system/subsystems.md`            |
| An API endpoint                 | `.agent/system/api-reference.md`         |
| System architecture             | `.agent/system/architecture.md`          |
| A runtime failure               | `.agent/SOP/troubleshooting.md`          |
| Adding a new route              | `.agent/SOP/add-route.md`                |
| Architecture decisions          | `.agent/decisions/` + `.claude/PROJECT_MEMORY.md` |

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
- RAG backend/frontend are two of the seven Docker services validated by `deploy/SMOKE-TEST.md`.
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

## Subsystem Reference

Auth, RBAC, Token/Cost Tracking, Benchmarking, External Usage Recording,
Embedding Token Tracking, Frontend Permission Guards, and Onboarding System
details are in `.agent/system/subsystems.md`. Read that file when working on
those areas — it is not loaded at session start.

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)
