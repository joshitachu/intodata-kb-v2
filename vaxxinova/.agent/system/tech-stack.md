# Technology Stack

## Scanner CLI (intodoc/)

| Category | Technology | Version |
|----------|-----------|---------|
| Language | Python | 3.11+ |
| CLI framework | Click | latest |
| Data models | Pydantic | v2 |
| Build system | hatchling | latest |
| XML parsing | lxml | latest |
| LLM client (legacy) | LiteLLM | latest |
| Templates (legacy) | Jinja2 | latest |
| PDF export | WeasyPrint | latest |
| Expression parsing | Custom recursive descent | -- |

## MCP Server (mcp-server/)

| Category | Technology | Version |
|----------|-----------|---------|
| Language | Python | 3.11+ |
| Web framework | FastAPI | latest |
| MCP protocol | mcp (Python SDK) | latest |
| ORM | SQLAlchemy | 2.x (async) |
| Database driver | asyncpg | latest |
| Migrations | Alembic | latest |
| Data models | Pydantic | v2 |

## Dashboard (dashboard/)

| Category | Technology | Version |
|----------|-----------|---------|
| Language | TypeScript | 5.x |
| UI framework | React | 19 |
| Build tool | Vite | 6 |
| CSS | Tailwind CSS | 3 |
| Data fetching | TanStack Query | 5 |

## RAG Pipeline (../RAG_pipeline/)

| Category | Technology | Version |
|----------|-----------|---------|
| Language | Python | 3.11+ |
| Web framework | FastAPI | latest |
| Vector database | Qdrant | latest |
| Embeddings | nomic-embed-text (768-dim) | via Ollama |
| Search | Hybrid (vector + BM25) | -- |
| Frontend | Next.js | latest |

## Infrastructure

| Category | Technology | Notes |
|----------|-----------|-------|
| Database | PostgreSQL | Project registry, scan runs |
| LLM inference | Ollama | For embeddings (nomic-embed-text) |
| Package managers | pip, npm | Python and Node.js |
| Version control | Git | Monorepo structure |
