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
│   ├── context.md             # Current state, decisions
│   └── progress.md            # Session logs
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

## Code Style

### Python
```python
# ✅ Always type hints
def process(path: str) -> list[Chunk]:
    pass

# ✅ Pydantic validation
class Request(BaseModel):
    query: str = Field(..., min_length=3)

# ✅ Never bare except
try:
    result = operation()
except SpecificError as e:
    logger.error(f"Failed: {e}", exc_info=True)
    raise

# ✅ Async for I/O
async def fetch(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(url) as resp:
            return await resp.json()
```

### TypeScript
```typescript
// ✅ Never 'any', use 'unknown'
function handle(data: unknown): void {
  if (isValid(data)) { /* ... */ }
}

// ✅ Explicit props
interface Props {
  onSubmit: (query: string) => Promise<void>;
  isLoading: boolean;
}

// ✅ Zod for API validation
const Schema = z.object({ answer: z.string() });
type Response = z.infer<typeof Schema>;

// ✅ Handle all states
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

## Multi-Agent Workflow

Work as ONE agent at a time. Each agent has its own folder with detailed instructions.

**Start with**: Infrastructure Agent → then choose based on task

See `.claude/agents/[agent-name]/instructions.md` for specific agent tasks.

## Common Commands
```bash
# Dev
uvicorn src.api.main:app --reload    # Backend
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

## Context System

**Start every session**: Read `.claude/context.md` and `.claude/progress.md`

**After work**: Update both files with decisions and completed tasks

## Environment
```bash
# backend/.env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=llama3.1:8b
QDRANT_URL=http://localhost:6333
DATABASE_URL=postgresql://postgres:changeme@localhost:5432/intodata_kb

# frontend/.env
VITE_API_URL=http://localhost:8000/api/v1
```

## Key Features

- Document ingestion (PDF, DOCX, PPTX)
- Text chunking (500 tokens, 50 overlap)
- Vector search with RBAC filtering
- RAG with local LLM
- Source citations in responses
- Slack message history (next phase)

## Out of Scope

- Legacy code ingestion (deferred)
- Real-time sync (batch only)
- Multi-language (English only)