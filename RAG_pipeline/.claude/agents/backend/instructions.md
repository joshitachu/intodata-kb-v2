# Backend Agent (RAG Pipeline)

## Responsibilities
- FastAPI application setup (CORS, lifespan, middleware)
- Document ingestion pipeline (parse → chunk → embed → store)
- Vector retrieval (Qdrant search with RBAC)
- LLM generation (Ollama client, prompt building, RAG chain)
- API routes and Pydantic schemas
- RBAC (auth middleware, permission checking)

## Files Owned
- `backend/src/config.py`
- `backend/src/schemas.py`
- `backend/src/api/` — FastAPI app, routes, dependencies
- `backend/src/ingestion/` — Parser, chunker, metadata, pipeline
- `backend/src/retrieval/` — Embedder, vector store, search
- `backend/src/generation/` — LLM client, prompt builder, RAG chain
- `backend/src/rbac/` — Auth, permissions
- `backend/tests/` (except test_db/)

## Key Parameters
- Embedding: nomic-embed-text via Ollama (768-dim)
- LLM: Mistral or Llama3 via Ollama
- Chunking: 500 tokens, 50 overlap
- Retrieval: Top-K=5, threshold ≥ 0.7 cosine
- Context window: ≤ 3000 tokens to LLM
- RBAC: Qdrant payload filter on `allowed_groups`

## API Endpoints
- `GET /health` — Check all services
- `POST /api/v1/query` — RAG query (streaming response)
- `POST /api/v1/ingest` — File upload + ingestion
- `GET /api/v1/documents` — List documents
- `DELETE /api/v1/documents/{id}` — Remove document

## Acceptance Criteria
- Upload PDF → chunks in Qdrant with metadata
- Query returns answer + source citations
- RBAC filtering works on every query
- Health check verifies all services
