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
│   │   ├── api/routes/        # FastAPI endpoints
│   │   ├── ingestion/         # Document processing
│   │   ├── retrieval/         # Vector search
│   │   ├── generation/        # LLM client
│   │   └── rbac/              # Access control
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

## Environment
```bash
# backend/.env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=llama3.1:8b
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql://postgres:changeme@localhost:5432/intodata_kb

# Auth (shared between backend + MCP server)
JWT_SECRET_KEY=change-me-in-production-use-a-strong-random-key
ADMIN_DEFAULT_PASSWORD=admin

# frontend/.env
VITE_API_URL=http://localhost:8000/api/v1
```

## Key Features

- **Authentication**: JWT httpOnly cookies (access 15min + refresh 7d rotation), bcrypt passwords, rate-limited login (5/min), admin user management panel. Default creds: admin/admin
- Document ingestion (PDF, DOCX, PPTX)
- Text chunking (500 tokens, 50 overlap)
- Vector search with RBAC filtering
- RAG with local LLM
- Source citations in responses

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)