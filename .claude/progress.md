# Progress

## Phase 1 — MVP (COMPLETE)

- [x] Planning & architecture decisions
- [x] Project scaffolding (dirs, configs, placeholders)
- [x] Database Agent — models, migrations, session (11 tests)
- [x] Backend Agent — RAG pipeline, API routes, RBAC (48 tests)
- [x] Frontend Agent — Next.js, route handlers, components (build passing)

## Phase 1.5 — Scaling & Quality (COMPLETE)

- [x] Map-reduce summarization (full-document summaries via QueryType routing)
- [x] Document re-upload replacement (same filename replaces old vectors + DB record)
- [x] merge_sources_by_document max_documents parameter
- [x] Phase 3 future issues documented (multi-hop, chunking, embedding drift)
- [x] Conversation memory fix (pronoun resolution via assistant context + intent classifier context)
- [x] Context-first chat (follow-ups from previous context, [NEED_SEARCH] fallback)
- [x] LLM search reformulation (replaced expand_query with gemma3:4b reformulator) — **removed in later session**
- [x] Fix [NEED_SEARCH] leak (separate prompt for context-first, safety strip, removed reformulation)

## Phase 2 — Future

- [ ] Integration Agent (Slack, SharePoint, etc.) — deferred until company tooling known
- [ ] JWT/OAuth authentication
- [ ] E2E testing with real Docker services

## Phase 3 — Quality Improvements (Documented)

- [ ] Paragraph-aware chunking (Tier 1)
- [ ] Query expansion glossary
- [ ] Multi-hop query decomposition
- [ ] Cross-encoder re-ranking
- [ ] Section-aware chunking (Tier 2)
- [ ] Re-index CLI tool

## Quick Stats

| Component | Files | Tests | Status |
|-----------|-------|-------|--------|
| Backend | 30+ Python files | 154 passing | Complete |
| Frontend | 20+ TypeScript files | - | Build passing |
| Database | 5 models + migration | 11 passing | Complete |

See `PROGRESS_LOG.md` for detailed session history.
