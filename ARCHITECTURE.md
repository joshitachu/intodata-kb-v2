# IntoData KB - Platform Architecture Document

RAG Knowledge Base + Code Intelligence Dashboard | Confidential

Services and ports: root `CLAUDE.md`. Start commands: `readme.md`. Parameter defaults:
`RAG_pipeline/backend/src/config.py` and `knowledge_base/mcp-server/src/mcp_server/config.py`
(the code wins over this document).

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [RAG Pipeline - Ingestion](#4-rag-pipeline---ingestion)
5. [RAG Pipeline - Query & Retrieval](#5-rag-pipeline---query--retrieval)
6. [RAG Pipeline - Generation & Streaming](#6-rag-pipeline---generation--streaming)
7. [Knowledge Base & Dashboard](#7-knowledge-base--dashboard)
8. [API Layer & Endpoints](#8-api-layer--endpoints)
9. [Database Architecture](#9-database-architecture)
10. [Vector Store (Qdrant)](#10-vector-store-qdrant)
11. [Authentication & Authorization](#11-authentication--authorization)
12. [Infrastructure & Services](#12-infrastructure--services)
13. [External Integrations](#13-external-integrations)
14. [Key Architectural Decisions](#14-key-architectural-decisions)

---

## 1. Executive Summary

IntoData KB is a production-grade platform composed of two tightly integrated subsystems: a Retrieval-Augmented Generation (RAG) knowledge base and a code intelligence dashboard. Together, they enable organizations to ingest documents, scan codebases, generate documentation, and query all accumulated knowledge through a conversational AI interface with fine-grained access control.

The RAG subsystem handles document upload, semantic chunking, hybrid vector search (dense + sparse), reranking, and LLM-powered answer generation with streaming responses. The dashboard subsystem provides a control plane for scanning code repositories (Talend, Workato, Lobster, and generic platforms), triggering documentation generation via a configurable AI agent, and pushing generated docs into the RAG pipeline.

The platform is containerized via Docker Compose (seven services in the health boundary; full list in the root `CLAUDE.md` services table), fronted by an nginx reverse proxy on the VPS with SSL termination, and secured with JWT-based authentication, MFA support, role-based access control (RBAC), and project-level document filtering enforced at the vector search layer.

### Key Capabilities

- Multi-format document ingestion (PDF, DOCX, PPTX, XLSX, Markdown)
- Semantic and heading-aware chunking preserving meaning boundaries (max 512 tokens per chunk)
- Hybrid vector search: dense embeddings (1024-dim) + hash-based BM25 sparse vectors (30,000-dim)
- Model-agnostic LLM inference: one selected `provider/model` per instance, optional explicitly approved fallbacks, Ollama as the local option
- Multi-turn agent chat (harness agent runtime) with conversation history, bounded tool budgets, and grounded citations
- RBAC with 3 roles, 9 permissions, and project-level access filtering enforced at the vector layer
- TOTP MFA, email OTP, invite-based onboarding, and access-request flows
- Code intelligence scanning for Talend, Workato, Lobster, and generic platforms
- AI-powered documentation generation via configurable doc-generation provider
- Custom bounded agent runtime for multi-phase document generation with task persistence and validation
- Real-time streaming (SSE) for both query responses and operation logs
- Token/cost tracking with per-model pricing and detailed usage dashboards
- Optional long-context memory via Hindsight memory bank
- RAG quality benchmarking framework with LLM-as-judge evaluation
- Full audit trail logging for compliance
- Tenant branding config, onboarding wizard, notifications, and feedback collection

---

## 2. System Architecture Overview

The platform follows a microservices-inspired architecture with seven containerized application services (the EP-001 health boundary) orchestrated by Docker Compose. All external traffic enters through an nginx reverse proxy on the VPS which handles TLS termination, security headers, and path-based routing to the appropriate upstream service.

### High-Level Architecture Diagram

```
+-------------------------------------------------------------------+
|               nginx Reverse Proxy (VPS)                           |
|     (HTTPS/SSL, security headers, path-based routing)             |
+--------+-----------+--------------+------------------------------+
         |           |              |
  /      |  /api/v1/*|  /dashboard/*|  /mcp/* /logs/* /webhook/*
  |      |  /auth/*  |              |
  v      v           v              v
+------+ +----------+ +-----------+ +------------+
|Next  | | FastAPI  | | Vite +    | | FastAPI    |
|.js   | | RAG      | | React     | | MCP Server |
|UI    | | Backend  | | Dashboard | | :8002      |
|:3000 | | :8000    | | :8001     | | :8002      |
+--+---+ +----+-----+ +-----+-----+ +------+-----+
   |          |             |               |
   +----------+------+------+-------+-------+
                     |              |
              +------+------+ +-----+-------+
              |              | |             |
              v              v v             v
          +---------+ +------+ +----------+ +-----------+
          |Postgres | |Qdrant| |Hindsight | | Ollama    |
          | :5432   | |:6333 | | :8888    | | :11434    |
          +---------+ +------+ +----------+ +-----------+
```

### Service Communication Patterns

The two frontend applications communicate with their respective backends via REST APIs. The RAG UI (Next.js, port 3000) calls the RAG Backend (FastAPI, port 8000) for document queries, ingestion, authentication, and admin operations. The Dashboard (Vite+React, port 8001) calls the MCP Server (FastAPI, port 8002) for project management, scans, and documentation generation.

Cross-subsystem communication occurs when the MCP Server pushes generated documentation into the RAG pipeline via `POST /api/v1/ingest/md`, authenticated with a shared API key (`MCP_INGEST_API_KEY`). Both backends share the same PostgreSQL instance for user and project data.

The dashboard container runs its own internal nginx instance that serves the static Vite build and proxies API calls to the MCP server (`mcp-server:8002`) and RAG backend (`backend:8000`) over the Compose network. This means there are two nginx layers: one at the VPS edge for external traffic, and one inside the dashboard container for internal routing.

### End-to-End Data Flow

```
Code Repo --> intodoc scan --> MCP Server --> AI agent (doc gen) --> Markdown
                                                    |
                             POST /api/v1/ingest/md-+
                                                    v
User uploads docs ----> RAG Backend --> parse --> chunk --> embed --> Qdrant
                                                                        |
User asks question ---> agent chat runtime --> hybrid search (dense+BM25) -+
                                                    |
                        rerank --> LLM gen --> SSE stream --> answer + sources
```

---

## 3. Technology Stack

### 3.1 Backend & API

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Python | 3.11+ | Backend services |
| API Framework | FastAPI + Uvicorn | 0.115+ | REST API, SSE streaming |
| ORM | SQLAlchemy (async, asyncpg driver) | 2.0+ | Async database abstraction |
| Validation / settings | Pydantic v2 + pydantic-settings | 2.0+ | Request/response schemas, env config |
| Migrations | Alembic | - | Schema versioning |
| Auth | JWT via PyJWT (httpOnly cookies) | - | Session management |
| MFA | TOTP via pyotp | - | Two-factor authentication |
| Password Hash | bcrypt | - | Credential security |
| Rate Limiting | slowapi | - | Per-route auth rate limits |
| Build | `pyproject.toml` (RAG backend: setuptools backend) | - | Python packaging |
| Testing | pytest + pytest-asyncio (`asyncio_mode = "auto"`) | - | Backend test suite |

### 3.2 Frontend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| RAG Frontend | Next.js (App Router, BFF route handlers) + React | 15.1 / 19 | Server-rendered RAG chat UI |
| Dashboard | Vite + React | 6 / 19 | Client-side KB control panel |
| Language | TypeScript (strict) | - | No `any` |
| Styling | Tailwind CSS + Radix UI primitives (shadcn/ui) | 3.4 | Utility-first CSS, components |
| Data Fetching | TanStack React Query | 5.60 | Server state management |
| Validation | Zod | 3 (RAG) / 4 (dashboard) | API response and input schemas |
| Visualization | Recharts + XYFlow | - | Charts and topology graphs |
| Build | npm | - | JS package management |
| Testing | Vitest | 2.1 | Frontend test suite |

### 3.3 AI & ML

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Embeddings (primary) | Voyage `voyage-4-large`, 1024-dim (`EMBEDDING_PROVIDERS`) | Dense vector embeddings |
| Embeddings (fallback) | Ollama `nomic-embed-text`, 768-dim | Local embedding fallback |
| Sparse Embeddings | Hash-based BM25, 30,000-dim (pure Python) | Keyword matching vectors |
| Chat / answer LLM | Selected `provider/model` (`RAG_DEFAULT_MODEL`, default `kimi/kimi-k2.6`; superadmin override) | Answer generation |
| LLM fallback | Only models listed in `RAG_APPROVED_FALLBACK_MODELS` (empty by default); Ollama (`gemma3:4b`) is selectable, not a silent fallback | Explicit, contract-compatible fallback |
| Reranker | Voyage `rerank-2.5` | Result re-scoring |
| Memory | Hindsight | Long-context user memory |
| Code Scanner | intodoc (custom CLI) | Talend/Workato/Lobster/generic parsing |
| Doc Generation | Configurable provider (`MCP_DOC_GENERATION_PROVIDER`, default `custom` harness) | AI-powered doc writing |

### 3.4 Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Database | PostgreSQL 16 | Relational data (auth, docs, usage, audit) |
| Vector Store | Qdrant (REST 6333, gRPC 6334) | Dense + sparse vector search |
| Reverse Proxy | nginx + certbot | HTTPS/SSL, routing, security headers |
| Containers | Docker Compose | Service orchestration (see root `CLAUDE.md`) |
| Local LLM Runtime | Ollama (on the host, optional) | Local model inference |

---

## 4. RAG Pipeline - Ingestion

The RAG pipeline is implemented entirely from scratch with no LangChain dependency, giving full control over every stage of retrieval and generation.

### 4.1 Ingestion Entry Points

| Endpoint | Method | Source | Format |
|----------|--------|--------|--------|
| `POST /api/v1/ingest` | Multipart upload | RAG UI / API | PDF, DOCX, PPTX, XLSX |
| `POST /api/v1/ingest/md` | JSON body | MCP Server | Markdown string |

Both endpoints are project-scoped: every document is assigned to a `project_id`. RBAC checks ensure the calling user has the `ingest_documents` permission. If a document with the same filename already exists in the project, it is replaced via cascade delete (old chunks removed from Qdrant, new ones inserted).

### 4.2 Document Parsing (`parser.py`)

| Format | Library | Extraction Method |
|--------|---------|-------------------|
| PDF | pypdf | Page-by-page text extraction |
| DOCX | python-docx | Paragraph and metadata extraction |
| PPTX | python-pptx | Slide text frame extraction |
| XLSX | openpyxl | Cell data as structured text |
| Markdown | Direct | Raw text (from MCP `/ingest/md` endpoint) |

### 4.3 Semantic Chunking (`semantic_chunker.py`)

Rather than splitting text at arbitrary character counts, the semantic chunker detects natural boundaries (paragraphs, headings, section breaks) to create chunks that preserve meaning.

- Maximum chunk size: 512 tokens (`DEFAULT_MAX_TOKENS`); oversized paragraphs are split mechanically with overlap (`chunker.py`)
- Markdown is split on headings (`markdown_chunker.py`); small sibling sections are merged up to the maximum
- Sections named in `CHUNK_EXCLUDED_SECTIONS` (default: "Evidence Register") are dropped at chunk time
- Metadata preserved per chunk: source filename, position index, `document_id`

### 4.4 Embedding Pipeline (`embedder.py` + `sparse_embedder.py`)

**Dense Embeddings**

- Primary: Voyage (`voyage-4-large`) — 1024-dimensional vectors. Batch processing up to 128 texts per request for throughput.
- Fallback: Ollama `nomic-embed-text` — 768-dimensional vectors. Used when the primary provider is unavailable or during local development.
- Token usage tracked via thread-local storage for cost monitoring.

**Sparse Embeddings (BM25)**

When hybrid search is enabled (`HYBRID_SEARCH_ENABLED`), BM25 sparse vectors are generated alongside dense embeddings using a pure-Python hash-based implementation (30,000 dimensions, no external dependencies). These capture exact keyword matches that dense embeddings might miss, improving recall for technical terms, acronyms, and proper nouns.

### 4.5 Vector Storage (`vector_store.py`)

Each chunk is upserted to Qdrant with the following payload:

| Field | Type | Purpose |
|-------|------|---------|
| `document_id` | integer | Links back to PostgreSQL document record |
| `text` | string | The actual text content of the chunk |
| `filename` | string | Source document filename |
| `chunk_index` | integer | Position within the document |
| `allowed_groups` | list[string] | RBAC: groups permitted to access this chunk |
| `project_id` | string | Multi-tenant scoping identifier |

Further caller metadata (`source_layer`, `document_role`, `knowledge_revision_id`, `artifact_id`, `unit`, `section`) is merged in; the base fields always win. Payload indexes cover all of these keys (`src/retrieval/vector_store.py`). The document record is also persisted in PostgreSQL with `chunk_count` and `status` for the admin dashboard.

---

## 5. RAG Pipeline - Query & Retrieval

### 5.1 Query Routing

There is no intent classifier. Conversation turns run through the harness agent
runtime (`RAG_pipeline/backend/src/harness/`), which chooses tools (documentation
answers, metadata, topology, runtime evidence) within bounded budgets. The
`/api/v1/query*` endpoints run the RAG chain directly (`src/generation/rag_chain.py`).
Project and document metadata questions are served by
`src/retrieval/metadata_retriever.py` when `METADATA_QUERIES_ENABLED` is on.

### 5.2 Memory Recall (`hindsight_client.py`)

Optional stage running in parallel with search (non-blocking). Retrieves user-specific context from the Hindsight memory bank.

- Per-user memory scoping (by uid); separate project-scoped banks
- Budget-aware recall: low/medium/high (default: low, max 1,024 tokens)
- Returns three types: memories, quality insights, past corrections
- If Hindsight is unavailable, pipeline continues without memory context

### 5.3 Hybrid Search (`search.py`)

**Search Strategy**

- Dense path: embeds the query with the configured provider, performs cosine similarity search — captures semantic meaning and paraphrasing.
- Sparse path: generates BM25 sparse vector from the query, searches against stored sparse vectors — captures exact keyword matches.
- Fusion: Reciprocal Rank Fusion (RRF) merges both result lists; documents appearing in both receive boosted scores.
- Fallback: if hybrid is disabled or fails, falls back to dense-only search.

**Search Parameters**

| Parameter | Value | Description |
|-----------|-------|-------------|
| Top-K | 8 | Maximum results returned |
| Prefetch limit | 50 | Candidates per method before fusion |
| Similarity threshold | 0.3 | Minimum cosine score (below = discarded) |
| Max documents | 6 | Unique source documents per query |
| Distance metric | Cosine | Vector similarity measure |

**RBAC Filtering at Search Time**

Access control is enforced at the Qdrant query level via `_build_search_filter()` (`src/retrieval/search.py`) — not via post-hoc Python filtering. Users in the `admin` group skip the `allowed_groups` condition; everyone else is matched on `allowed_groups` against their role groups plus assigned project ids (no groups and no assignments means no results). Project scope, corpus layer, document role, and the active knowledge revision per project are added as further conditions.

### 5.4 Reranking (`reranker.py`)

Optional stage (`RERANKER_ENABLED`, default on) using Voyage `rerank-2.5` to re-score initial search results for higher precision.

- Takes top-30 candidates from the search stage
- Re-scores each candidate against the original query text
- Applies minimum score threshold of 0.15 (very low scores discarded)
- Retry logic with exponential backoff for rate-limited providers (2s, 5s, 15s)

---

## 6. RAG Pipeline - Generation & Streaming

### 6.1 Answer Generation (`rag_chain.py` + `llm_client.py`)

**Model Selection**

Answer generation calls exactly the selected model: `RAG_DEFAULT_MODEL` (default `kimi/kimi-k2.6`), overridable by a superadmin via the `rag_chat_model` instance setting. The provider is resolved from the `provider/` prefix by the provider registry. Documentation answers may retry on models listed in `RAG_APPROVED_FALLBACK_MODELS` (empty by default); a local Ollama model is never treated as an equivalent silently. Provider models, temperatures, and timeouts are configurable via environment variables; one output ceiling (`LLM_MAX_OUTPUT_TOKENS`, default 8192) applies to every provider.

**Prompt Construction**

The RAG chain assembles the final prompt from:

1. System prompt — defines the assistant's role, response format, citation rules
2. Retrieved chunks — top-K search results with source attribution metadata
3. Memory context — Hindsight recall results (if available)
4. Conversation history — prior messages in the current conversation
5. User query — the original question

**Credential Filtering**

Credentials are redacted at scan time by intodoc (two layers, see 7.1), so generated docs do not carry them into the index. On the chat path the harness applies `redact_credentials` (`src/runtime/redaction.py`) to evidence previews.

### 6.2 Streaming (SSE)

`POST /api/v1/query/stream` uses Server-Sent Events for real-time token-by-token streaming. Chat turns stream through `POST /api/v1/conversations/{id}/messages/stream` (harness progress and answer events).

| SSE Event | Payload | Purpose |
|-----------|---------|---------|
| `token` | Token text | Incremental answer content |
| `sources` | JSON array | Source documents with filenames |
| `usage` | JSON object | Token counts, cost, latency |
| `done` | Empty | Stream completion signal |
| `error` | Error message | Error reporting |

A non-streaming endpoint (`POST /api/v1/query`) is also available for programmatic use.

### 6.3 Conversational Chat (harness agent runtime)

- Conversations tracked in PostgreSQL (`conversations` + `conversation_messages`)
- Each turn runs in `src/harness/agent_chat_runtime.py` with bounded iterations, tool calls, deadlines, and cost (`AGENT_CHAT_*`, `AGENT_RUNTIME_V2_*` settings in `config.py`)
- Answers carry grounded citations; source tracking is per message
- Session isolation: each conversation belongs to a single user
- Rollout and operation: `RAG_pipeline/docs/agent-chat-cutover-runbook.md`, `RAG_pipeline/docs/agent-runtime-canary-runbook.md`

### 6.4 Token & Cost Tracking

Every LLM and embedding call is tracked in `query_usage` with full latency decomposition.

| Field | Description |
|-------|-------------|
| `input_tokens` | Tokens sent to the LLM (prompt + context) |
| `output_tokens` | Tokens generated by the LLM (answer) |
| `embedding_tokens` | Tokens processed for query embedding |
| `cost_usd` | Calculated cost based on per-model pricing table |
| `latency_ms` / `embedding_ms` / `search_ms` / `llm_ms` | Stage-level latency breakdown |
| `provider` / `model` | Which provider/model handled this query |
| `interface` | Source: web, api, or mcp |
| `had_results` | Whether search found relevant documents |

---

## 7. Knowledge Base & Dashboard

### 7.1 intodoc Scanner

intodoc is a custom Python CLI tool that analyzes codebases and produces structured chunks of knowledge. It supports four platform adapters via a `PlatformPlugin` ABC.

| Platform | What It Scans | Output |
|----------|--------------|--------|
| Talend | ETL jobs (XML/XMI), contexts, schemas, routines | Structured chunks per job/component |
| Workato | JSON recipes, connectors, triggers, actions, datapills | Structured chunks per recipe |
| Lobster | XML profiles (DataWizard), agents, filters, connections | Structured chunks per profile |
| Generic | Any codebase (AST + heuristics, extension-based classification) | Code structure & documentation chunks |

Scanner output is cached in `intodoc-output/` (read-only after scan). Credential filtering is applied in two layers:
- Text regex: strips known secret patterns from raw source
- Post-parse structural: removes sensitive fields from parsed component objects

Redaction levels: standard | strict | paranoid (configurable per scan).

### 7.2 MCP Server (port 8002)

The MCP Server is a FastAPI application running as the `mcp-server` Docker Compose service, serving as both a REST API for the dashboard and an MCP-compatible server for AI agent integration.

- Project registry: manages code repositories and their metadata
- Scan orchestration: triggers intodoc scans and tracks status/progress
- Documentation generation: invokes the configured AI doc-generation provider with scan output and the selected pack
- Custom bounded agent runtime (`agent_runtime.py`): multi-phase doc generation (Phases 2-3) with persisted task contracts, validation reports, and configurable concurrency (`MCP_AGENT_PHASE2_CONCURRENCY`, `MCP_AGENT_PHASE3_CONCURRENCY`)
- RAG integration: server-side push of generated docs via `POST /api/v1/ingest/md` on successful generation
- Log streaming: SSE endpoint for real-time scan/generation output
- GitHub webhooks: receives push events to trigger automatic re-scans

### 7.3 Documentation Generation Flow

1. User triggers Generate in dashboard → `POST /mcp/generate` to MCP Server
2. MCP fetches scan chunks, dispatches per-group analysis agent tasks (Phase 2)
3. Phase 2.6 scorecard + Phase 3 document agents write to `docs_staging/`
4. Validation checks coverage; targeted repair tasks created for gaps before failure
5. **On success:** `docs_staging/` atomically swapped to `docs/`; server pushes to RAG via `POST /api/v1/ingest/md`
6. **On failure:** `docs_staging/` is preserved so the next Generate resumes from the checkpoint; existing `docs/` remains intact

Real-time logs stream via SSE throughout generation. User can review docs in DocPreview before or after push.

### 7.4 Dashboard UI (Vite + React; dev 5173, container 8001)

| Component | Purpose |
|-----------|---------|
| `ProjectList.tsx` | Registered projects as cards with scan/generation status |
| `ProjectCard.tsx` | Per-project KPI card |
| `ActionButtons.tsx` | Scan, Generate, and Push to RAG action triggers |
| `LogPanel.tsx` | Real-time SSE log viewer |
| `AddRepoDialog.tsx` | Modal to register a new code repository |
| `DocPreview.tsx` | Markdown viewer for generated documentation |
| `RunHistory.tsx` | Past doc generation runs with timestamps |
| `ScanHistory.tsx` | Past scan executions |
| `QualityHistoryPanel.tsx` | Quality metrics over time |
| `ValidationReviewPanel.tsx` | Validation results review |
| `ProjectSettings.tsx` | Advanced project configuration |

The dashboard container runs nginx internally (`proxy_buffering off`, `proxy_read_timeout 600s` for SSE).

---

## 8. API Layer & Endpoints

### 8.1 RAG Backend API (port 8000)

Route reference: `RAG_pipeline/.agent/system/api-reference.md`. The routers in
`RAG_pipeline/backend/src/api/routes/` are authoritative; everything except
`/health` is mounted under `/api/v1`.

### 8.2 MCP Server API (port 8002)

Route reference: `knowledge_base/.agent/system/mcp-server.md` ("REST API
Endpoints"). Project, scan, generation and discovery routes are mounted under
`/mcp` and require the RAG backend's `access_token` cookie; `/health`,
`/logs/stream` and `/webhook/github/webhook` are outside that prefix.

### 8.3 nginx Reverse Proxy Routing

Rendered per client from `deploy/nginx/nexus-client.conf.template` (the template is authoritative); ports come from environment variables.

| Path Pattern | Upstream | Notes |
|-------------|----------|-------|
| `/api/v1/*`, `= /health` | `127.0.0.1:${BACKEND_PORT}` | RAG API |
| `/webhook/*` | `127.0.0.1:${MCP_PORT}` | GitHub webhooks |
| `/ops/control/*` | `127.0.0.1:${MCP_PORT}` | Mission Control triggers; path allowlist, `OPS_CONTROL_ALLOW` rules, 64k body |
| `/mcp/*`, `/logs/*` | `127.0.0.1:${MCP_PORT}` | Superadmin-gated (`auth_request`); `/logs/` unbuffered, 600s timeout |
| `/dashboard/*`, `/assets/*` | `127.0.0.1:${DASHBOARD_PORT}` | Superadmin-gated |
| `/auth/*`, `/nexus-api/*` | `127.0.0.1:${BACKEND_PORT}` | Rewritten to `/api/v1/auth/*` and `/api/v1/*`; superadmin-gated |
| `/` (catch-all) | `127.0.0.1:${FRONTEND_PORT}` | Next.js RAG UI, WebSocket upgrade |

---

## 9. Database Architecture

PostgreSQL 16 serves as the relational data store for both subsystems. Schema is managed via Alembic migrations.

### 9.1 Entity Relationship Overview

| Table | Key Fields | Relationships |
|-------|-----------|---------------|
| `users` | id, username, email, password_hash, is_superadmin, mfa_enabled, totp_secret, backup_codes_hashed | Has many: roles, conversations, docs |
| `roles` | id, name, description | Has many: permissions, users |
| `permissions` | id, codename (9 types) | Belongs to: roles |
| `user_roles` | user_id, role_id | M2M: users ↔ roles |
| `role_permissions` | role_id, permission_id | M2M: roles ↔ permissions |
| `refresh_tokens` | id, user_id, token_hash, expires_at | Belongs to: user |
| `project_assignments` | user_id, project_id | M2M: users ↔ projects |
| `projects` | project_id, platform, doc_count | Owned by the MCP server models; has many: docs, assignments |
| `documents` | id, filename, file_type, chunk_count, project_id, uploaded_by | Belongs to: user |
| `ingestion_jobs` | id, document_id, status, chunks_created | Belongs to: document |
| `conversations` | id, user_id, title, created_at | Has many: messages |
| `conversation_messages` | id, conversation_id, role, content, sources, latency_ms | Belongs to: conversation |
| `query_usage` | id, user_id, model_name, provider, input/output/embedding tokens, cost_usd, latency breakdown | Belongs to: user |
| `query_logs` | id, user_id, query_text, results_count, latency_ms | Belongs to: user |
| `audit_logs` | id, user_id, action, resource_type, resource_id, changes | Belongs to: user |
| `benchmark_runs` | id, results (JSONB), created_at | Standalone |
| `user_onboarding` | id, user_id, wizard_completed, tour_completed, completed_at | Belongs to: user |
| `notifications` | id, user_id, type, title, message, is_read | Belongs to: user |
| `wizard_events` | id, user_id, event_type, step, event_data (JSONB) | Belongs to: user |
| `feedback` | id, user_id, category, message | Belongs to: user |
| `access_requests` | id, email, status, created_at, processed_at | Standalone |
| `user_invites` | id, email, token, expires_at, accepted_at | Standalone |
| `email_otp_codes` | id, email, code, expires_at, used_at | Standalone |

### 9.2 Key Indexes

- `documents`: filename, project_id, uploaded_by
- `ingestion_jobs`: status, document_id
- `query_usage`: user_id, created_at, interface
- `conversations`: user_id, created_at
- `audit_logs`: user_id, action, created_at
- `refresh_tokens`: token_hash, user_id

---

## 10. Vector Store (Qdrant)

Qdrant serves as the vector database. It supports both local Docker deployment and Qdrant Cloud, configured via `QDRANT_URL` and `QDRANT_API_KEY`.

### 10.1 Collection Configuration

| Property | Value |
|----------|-------|
| Collection name | `documents` (configurable via `QDRANT_COLLECTION`) |
| Distance metric | Cosine |
| Named vectors | `dense` (cosine) and `sparse` |
| Dense vector dimension | 1024 (Voyage) or 768 (Ollama fallback) |
| Sparse vectors | Hash-based BM25, 30,000 dimensions |
| Payload indexes | `allowed_groups`, `document_id`, `project_id`, `chunk_index`, `source_layer`, `document_role`, `knowledge_revision_id`, `artifact_id`, `unit`, `section` |

### 10.2 Hybrid Search Flow

1. Query embedded as both a dense vector and a BM25 sparse vector
2. Both vectors search Qdrant in parallel (50 candidates each, RBAC-filtered)
3. Results merged via RRF: documents in both lists receive boosted scores
4. Top-K (8) returned, threshold ≥ 0.3, max 6 unique source documents
5. Optional reranker re-scores top-30 candidates, min score 0.15

### 10.3 RBAC-Aware Vector Search

Access control is enforced inside the Qdrant query via `_build_search_filter()` — users cannot receive chunks outside their access scope, regardless of semantic relevance. The filter rules are in 5.3.

---

## 11. Authentication & Authorization

### 11.1 JWT Authentication Flow

Authentication uses JWT tokens in httpOnly cookies to prevent XSS-based token theft, with token rotation on every refresh.

**Login**
- `POST /auth/login` (rate limited: 5/min) → bcrypt validation → access token (60 min) + refresh token (14 days)
- Tokens stored as httpOnly cookies (`Secure=true` in prod, `SameSite=lax`); refresh token hash stored in PostgreSQL; a non-httpOnly `csrf_token` cookie backs the double-submit CSRF check
- If TOTP MFA is enabled: login returns a temporary challenge; client must call `POST /auth/verify-mfa`

**Token Refresh**
- `POST /auth/refresh` (rate limited: 60/min) → validate + revoke old refresh token → issue new token pair

**Logout**
- `POST /auth/logout` → revoke refresh token in DB → clear cookies

**MFA (TOTP — RFC 6238)**
- Setup: `POST /auth/totp/setup` returns secret + QR code URI
- Verification: `POST /auth/totp/verify` confirms setup
- Hashed backup codes stored in DB for account recovery
- Disable: `POST /auth/totp/disable` requires the account password (when set) and a current TOTP or backup code

**Email OTP**
- `POST /auth/otp/send` → generates time-limited code, sends via SMTP
- `POST /auth/otp/verify` → validates code, issues session

**Invite & Access Request Flows**
- `POST /auth/request-access` → queued in `access_requests`; admin approves/rejects
- Admin sends invite → tokenized invite link → `POST /auth/invite/accept` activates account

**First-Admin Bootstrap**
- `GET /auth/bootstrap-status` returns whether any admin exists
- `POST /auth/bootstrap-admin` callable only when the `users` table is empty

### 11.2 Role-Based Access Control (RBAC)

Three default roles with nine granular permissions:

| Role | Permissions | Scope |
|------|-------------|-------|
| admin | All 9 permissions | All projects, all documents |
| manager | manage_projects, assign_projects, view_all_projects, query_rag, ingest_documents | Assigned projects + mgmt |
| developer | query_rag, view_assigned_projects | Only assigned projects |

**All 9 Permission Types**

| Permission | Description |
|-----------|-------------|
| `manage_users` | Create, update, delete user accounts |
| `manage_roles` | Create and modify roles and their permissions |
| `assign_projects` | Assign/revoke user access to specific projects |
| `view_all_projects` | See all projects regardless of assignment |
| `manage_projects` | Create, modify, delete projects |
| `view_assigned_projects` | See only projects the user is assigned to |
| `query_rag` | Submit queries to the RAG system |
| `ingest_documents` | Upload and ingest new documents |
| `view_audit_logs` | Access the audit trail |

### 11.3 Project-Level Access Control

Two-layer access model:

- **Layer 1 (Role):** determines what actions a user can perform (query, ingest, admin).
- **Layer 2 (Project):** determines which documents/data a user can see — enforced at the Qdrant search filter level, not in application code.

### 11.4 Security Measures

- Password hashing: bcrypt with automatic salt generation
- MFA: TOTP (RFC 6238) with hashed backup codes; optional email OTP
- Credential redaction: two-layer filtering prevents secrets from reaching the LLM
- Audit logging: all admin actions recorded with user_id, action, details, timestamp
- Rate limiting: login 5/min, token refresh 60/min (slowapi)
- Cookie security: httpOnly, Secure (HTTPS), SameSite=lax
- nginx security headers: HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy (the template sets no CSP)
- IP allowlist: optional `ALLOWED_IPS` (comma-separated CIDRs)
- Trusted proxies: `TRUSTED_PROXIES` setting for correct client IP resolution behind reverse proxy

---

## 12. Infrastructure & Services

### 12.1 Docker Compose Services

Services, images, ports, and the health boundary: root `CLAUDE.md` services table.
Memory limits and health checks: `docker-compose.yml`.

All internal ports are bound to `127.0.0.1` only. Only ports 22 (SSH), 80, and 443 are exposed externally via UFW. External access routes exclusively through the VPS nginx proxy.

The MCP server runs as a Docker Compose service on port 8002 with bind mounts for generated data, scan output, documentation output, and instruction packs.

### 12.2 nginx Architecture (Two Layers)

**Layer 1: VPS Edge Proxy** (`deploy/nginx/nexus-client.conf.template`)

- Handles all external HTTPS traffic; port values configurable via environment variables
- SSL/TLS termination (Let's Encrypt / certbot)
- Path-based routing to backend, MCP server, dashboard, and frontend (table in 8.3)
- Operator surfaces (`/mcp`, `/logs`, `/dashboard`, `/auth`, `/nexus-api`, `/assets`) gated to superadmins via `auth_request`
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- SSE support: `proxy_buffering off` for `/logs/*` (600s read timeout)
- WebSocket upgrade support for the Next.js frontend

**Layer 2: Dashboard Container nginx**

- Serves the Vite static build; SPA fallback to `index.html`
- Proxies `/mcp/`, `/logs/`, `/webhook/`, `/health` to `mcp-server:8002` and `/mc/` to `mission-control:9100`
- Proxies `/auth/` and `/nexus-api/` to `backend:8000` (rewritten to `/api/v1/auth/` and `/api/v1/`)
- SSE: `proxy_buffering off`, `proxy_read_timeout 600s`
- Max upload body: 3072 MB (`knowledge_base/dashboard/nginx.conf`)

### 12.3 MCP Server Deployment (Docker Compose)

The MCP server is deployed as the `mcp-server` service in the root Docker Compose file:

- Service name: `mcp-server`
- Image build context: `knowledge_base`
- EnvironmentFile: `/etc/intodata-kb/envs/{client}.env` via `docker compose --env-file`
- Port: configurable via `MCP_PORT` (default: 8002)
- Restart: unless-stopped
- Mounted state: MCP data, intodoc scan output, generated docs, instruction packs

It runs in Docker, not on the host; source trees, scan output, and packs reach it through those bind mounts.

---

## 13. External Integrations

| Service | Purpose | Optional? | Auth |
|---------|---------|-----------|------|
| Dense embedding provider | 1024-dim vector embeddings | Yes (Ollama fallback) | API key |
| Reranker provider | Result re-scoring | Yes | API key |
| Chat LLM provider (default Kimi) | Answer generation | No (fallbacks only if listed in `RAG_APPROVED_FALLBACK_MODELS`) | API key |
| AI doc-generation provider | Documentation writing | Yes (configurable) | API key / CLI |
| Ollama | Local LLM option + embedding fallback | Yes, but a core backend `/health` check | Local |
| Qdrant | Vector database (Cloud or Docker) | No | API key (Cloud) |
| PostgreSQL 16 | Relational database | No | Password |
| Hindsight | Long-context user memory | Yes | None |
| GitHub Webhooks | Auto-trigger scans on push | Yes | Webhook secret (HMAC) |
| SMTP | Email invites, OTP codes | Yes | SMTP credentials |

### 13.1 LLM Provider Selection

See 6.1. There is no implicit provider chain for answers: the selected model is called, and only explicitly approved fallback models are tried after it. (`LLM_PROVIDERS` exists in `config.py` but is read only by the benchmark runner to label runs.)

### 13.2 Embedding Provider Failover

Embeddings walk `EMBEDDING_PROVIDERS` (default Voyage 1024-dim, then Ollama `nomic-embed-text` 768-dim) and use the first that succeeds. The vector dimension must be consistent within a Qdrant collection — switching providers mid-collection requires full re-indexing.

### 13.3 Doc Generation Provider

Documentation generation is decoupled from the query-time LLM. It is configured via `MCP_DOC_GENERATION_PROVIDER` and `MCP_DOC_GENERATION_WRITE_MODEL`, allowing larger or more capable models to be used for the batch doc-generation workload. The custom bounded agent runtime manages multi-phase generation, task persistence, validation, and repair independently of the RAG answer chain.

### 13.4 Benchmarking Framework

A built-in framework evaluates RAG quality. Benchmarks run via CLI (`python -m benchmark run|list|show|compare`); the API (`/api/v1/benchmark/runs`) only lists and shows runs.

| Metric | Description | Method |
|--------|-------------|--------|
| Precision@5 | Relevant docs in top-5 results | Automated scoring |
| Relevance Score | Answer quality (1–5 scale) | LLM-as-judge |
| Latency | End-to-end query time | Measured |
| Token Usage | Input + output tokens consumed | Tracked |
| Cost | USD cost per query | Model pricing table |

Results stored in PostgreSQL (`benchmark_runs`, JSONB). Run comparison diffs track quality changes over time.

---

## 14. Key Architectural Decisions

Decision records: `knowledge_base/.agent/decisions/` (for example 002 staging swap,
003 server-side RAG push, 022 no LangChain / configurable providers, 024 one VPS
per client, 037 subscription licence). The rationale for choices without an ADR
(hybrid BM25, semantic chunking, RBAC in Qdrant filters, httpOnly JWT cookies,
two-layer nginx) is stated in the relevant section above.

---

*For implementation details, the source code and inline documentation in the referenced modules are authoritative.*
