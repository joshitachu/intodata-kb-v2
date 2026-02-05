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

## Code Patterns

- Python: type hints everywhere, Pydantic validation, async for I/O, no bare except
- TypeScript: strict mode (never `any`), Zod for API validation, handle all states
- RBAC: Filter EVERY Qdrant query by user groups (payload metadata)
- Tests: Write alongside implementation, `@pytest.mark.integration` for Docker-dependent tests

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Project scaffolding | 2026-02-05 | All dirs, placeholders, configs created |

## Pending Tasks

- [ ] Database Agent: models, migrations, session
- [ ] Backend Agent: RAG pipeline, API routes, RBAC
- [ ] Frontend Agent: Next.js app, route handlers, components
- [ ] Integration Agent: Phase 2 (deferred)

## Blockers

None currently.

## Lessons Learned / Gotchas

(Will be updated as we encounter issues)

## Environment

- Platform: Windows (OneDrive path)
- Python: 3.11+
- Node: 18+
- Docker: Required for Qdrant + PostgreSQL
- Ollama: Required locally for embeddings + LLM
