# IntoData KB — Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| No LangChain | Direct Ollama client | Full control, fewer deps | 2026-02-05 |
| Embeddings | Ollama nomic-embed-text (768-dim) | Same interface as LLM, good quality | 2026-02-05 |
| LLM | Ollama (Mistral or Llama 3) | Local, no API costs | 2026-02-05 |
| Vector DB | Qdrant (Docker) | HNSW, payload filtering for RBAC | 2026-02-05 |
| Relational DB | PostgreSQL (async) | Users, roles, doc metadata, audit | 2026-02-05 |
| Auth (Phase 1) | Header-based (X-User-Id) | Simple for dev; JWT/OAuth later | 2026-02-05 |
| Frontend | Next.js App Router + Tailwind + shadcn/ui | SSR, route handlers as BFF proxy | 2026-02-05 |
| State mgmt | React Query | Server state caching | 2026-02-05 |
| API pattern | BFF via Next.js route.ts → FastAPI | Client never calls FastAPI directly | 2026-02-05 |
| Integrations | Phase 2 (deferred) | Company tooling unknown | 2026-02-05 |
| Intent routing | LLM router (gemma3:4b) | Replaces regex — more robust routing + typo correction; safe fallback to SEARCH on failure | 2026-02-10 |
| Search threshold | 0.4 (was 0.7→0.4→0.2→0.4) | 0.2 too permissive, 0.58 too strict; 0.4 is the sweet spot for nomic-embed-text | 2026-02-10 |
| Chunk size | 256 tokens / 32 overlap | Smaller chunks = more focused embeddings = better similarity scores | 2026-02-09 |
| Context window | 4096 tokens | Fits ~16 chunks of 256 tokens | 2026-02-09 |
| Source merging | Document-level (not chunk-level) | Users don't need chunk granularity; numbered refs enable clickable UI links | 2026-02-09 |
| Markdown rendering | react-markdown + remark-gfm | Standard, lightweight, supports GFM tables/lists | 2026-02-09 |

## Code Patterns

- Python: type hints everywhere, Pydantic validation, async for I/O, no bare except
- TypeScript: strict mode (never `any`), Zod for API validation, handle all states
- RBAC: Filter EVERY Qdrant query by user groups (payload metadata)
- Tests: Write alongside implementation, `@pytest.mark.integration` for Docker-dependent tests

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Project scaffolding | 2026-02-05 | All dirs, placeholders, configs created |
| Database Agent | 2026-02-05 | Config, models, session, Alembic, 11 tests passing |
| Backend Agent | 2026-02-05 | Full RAG pipeline, all API routes, RBAC, 48 tests passing |
| Frontend Agent | 2026-02-06 | Next.js App Router, route handlers, components, hooks, build passing |
| Qdrant payload index fix | 2026-02-06 | Added KEYWORD index on `allowed_groups` + INTEGER index on `document_id` in `ensure_collection()` |
| CLAUDE.md logging overhaul | 2026-02-06 | Added mandatory logging rules, structured format, read-before-work policy |
| Intent routing for RAG pipeline | 2026-02-06 | Regex classifier skips search for chitchat, LLM fallback for no-results. 156 tests passing |
| Search recall improvements | 2026-02-09 | Threshold 0.2, top_k 10, chunk size 256, context 4096, debug endpoint, pipeline logging. 157 tests passing |
| UI/UX & response formatting | 2026-02-09 | DocumentSource schema, merge_sources_by_document(), numbered citations [1], markdown rendering, LoadingMessages, clickable citation links. 164 tests passing |
| Fix frontend conversation 500s | 2026-02-09 | Cleared stale .next cache, added 204 handling in fastapiFetch, hardened useConversations error handling for non-JSON responses |
| LLM query router | 2026-02-10 | Replaced regex intent classifier with LLM-based router (gemma3:4b). Routes search/respond + typo correction. 48 generation tests passing |
| Retrieval config unification | 2026-02-10 | Unified top_k=3/threshold=0.4 across all defaults, silenced SQLAlchemy echo, added reasoning logs |

## Pending Tasks

- [x] Database Agent: models, migrations, session
- [x] Backend Agent: RAG pipeline, API routes, RBAC — 48 tests passing
- [x] Frontend Agent: Next.js app, route handlers, components — build passing
- [ ] Integration Agent: Phase 2 (deferred)

## Blockers

None currently.

## Lessons Learned / Gotchas

- PostgreSQL `JSONB` → generic `JSON` for SQLite test compat (still works as JSONB on Postgres)
- SQLite needs `PRAGMA foreign_keys=ON` for CASCADE tests — added to conftest.py
- SQLAlchemy needs `passive_deletes=True` on parent relationship for DB-level CASCADE to work
- `setuptools.backends._legacy:_Backend` doesn't exist on Python 3.14 — use `setuptools.build_meta`
- Python 3.14 is installed on this machine (not 3.11)
- Zod `.default()` doesn't make field optional in `z.infer` — use `.optional().default()` + `z.input` for request types
- OneDrive paths can cause build issues — delete `.next` cache if build fails with manifest errors
- Qdrant requires explicit `create_payload_index()` on any field used in `FieldCondition` filters — just creating the collection is not enough
- `create_payload_index()` is idempotent — safe to call on every startup without checking if index exists
- `nomic-embed-text` cosine similarity for natural language queries against relevant chunks: typically 0.2-0.5. Threshold must be ≤0.2 to avoid dropping borderline-relevant results
- Changing chunk size requires re-ingestion of all existing documents
- Always use `res.text()` + `try { JSON.parse(...) } catch {}` for error responses — never assume error bodies are valid JSON (Next.js can return HTML error pages from webpack crashes)
- `fastapiFetch` must check `res.status === 204` before calling `res.json()` — DELETE endpoints return empty bodies

## Environment

- Platform: Windows (OneDrive path)
- Python: 3.14.3 (installed), pyproject says >=3.11
- Node: v24.13.0 (installed)
- Docker: Required for Qdrant + PostgreSQL (not available on dev machine)
- Ollama: Required locally for embeddings + LLM

## File Structure Summary

```
intodata-kb/
├── backend/
│   ├── src/
│   │   ├── api/          # FastAPI app, routes, dependencies
│   │   ├── db/           # SQLAlchemy models, session
│   │   ├── ingestion/    # Parser, chunker, metadata, pipeline
│   │   ├── retrieval/    # Embedder, vector store, search
│   │   ├── generation/   # LLM client, prompt builder, RAG chain
│   │   ├── rbac/         # Auth, permissions
│   │   ├── config.py     # Pydantic Settings
│   │   └── schemas.py    # Request/response models
│   ├── tests/            # 48 tests across all modules
│   ├── alembic/          # Database migrations
│   └── pyproject.toml
├── frontend/
│   ├── app/              # Next.js pages + API route handlers
│   ├── components/       # React components
│   ├── hooks/            # React Query hooks
│   ├── lib/              # Utils, FastAPI client
│   ├── types/            # Zod schemas
│   └── package.json
├── docker-compose.yml    # Qdrant + PostgreSQL
└── .claude/              # Project tracking files
```

## How to Resume Development

1. Read `context.md` for current state
2. Read `PROJECT_MEMORY.md` (this file) for architecture and patterns
3. Check `PROGRESS_LOG.md` for what was done in previous sessions
4. Run backend tests: `cd backend && python -m pytest tests/ -v`
5. Run frontend build: `cd frontend && npm run build`
