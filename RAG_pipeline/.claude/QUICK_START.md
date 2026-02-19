# IntoData KB — Quick Start

## Current State
- **Phase**: Scaffolding complete, starting Database Agent
- **What works**: Nothing yet — project structure only
- **What's next**: Docker Compose, DB models, Alembic migrations

## Project Overview
RAG knowledge base: upload documents → embed with Ollama → store in Qdrant → query with natural language → LLM generates answer with citations.

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Docker Desktop (for Qdrant + PostgreSQL)
- Ollama installed locally

### Steps
```bash
# 1. Start infrastructure
docker-compose up -d              # Qdrant (6333) + PostgreSQL (5432)

# 2. Pull Ollama models
ollama pull nomic-embed-text      # Embeddings (768-dim)
ollama pull mistral               # LLM (or llama3)

# 3. Backend
cd backend
cp .env.example .env              # Edit with your values
pip install -e ".[dev]"           # Install dependencies
alembic upgrade head              # Run migrations
uvicorn src.api.main:app --reload # Start API on :8000

# 4. Frontend
cd frontend
cp .env.example .env.local
npm install
npm run dev                       # Start on :3000
```

## Key Files
- `claude.md` — Project specification
- `.claude/PROJECT_MEMORY.md` — Architecture decisions + state
- `.claude/PROGRESS_LOG.md` — Session-by-session log
- `.claude/plans/` — Implementation plans

## Architecture
```
Browser → Next.js (route.ts) → FastAPI → Qdrant (search) + Ollama (embed/generate)
                                    ↕
                               PostgreSQL (users, roles, metadata)
```

## What Works / What Doesn't
| Component | Status |
|-----------|--------|
| Project structure | Created |
| Docker Compose | Not yet |
| Database models | Not yet |
| Backend API | Not yet |
| RAG pipeline | Not yet |
| Frontend | Not yet |
