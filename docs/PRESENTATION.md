# IntoData KB -- Internship Presentation

---

## Slide 1: Title

### IntoData KB — Integration Intelligence Platform

**[Your Name]** | March 2026

*Auto-scan integration codebases, generate documentation, search with AI*

- 5 weeks of development
- 1 developer
- ~18,000 lines of code
- Phase 1 complete — production-deployed on VPS

> **Speaker notes:** This project started February 3rd. In 5 weeks we went from zero to a deployed platform that scans Talend codebases, generates documentation with AI, and lets users search that documentation using natural language. Everything runs on a single VPS.

---

## Slide 2: The Problem & Our Solution

### Problem

- Integration codebases (Talend, ADF, Workato) are complex and poorly documented
- Manual documentation takes **days per project** and goes stale immediately
- Migrating between platforms requires deep understanding of both source and target
- Knowledge lives in people's heads, not in searchable systems

### Solution

```
Source Code --> Scanner CLI --> AI Doc Generation --> RAG Search --> Answers
```

- **Scan**: Parse integration source code into structured data (XML, JSON, ARM templates)
- **Generate**: AI analyzes scan data and writes comprehensive documentation (22 analysis dimensions)
- **Search**: Upload docs to vector database, query with natural language, get cited answers
- **Track**: Every AI call tracked for cost and quality

> **Speaker notes:** What used to take days now takes minutes. The scanner handles the tedious parsing, the documentation harness generates the docs through the configured AI provider, and users can immediately search through everything with natural language. The key insight: instead of reading code, ask questions about it.

---

## Slide 3: What We've Built (Architecture)

### 6 Docker Services

```
+------------------+     +------------------+     +------------------+
|   RAG Frontend   |     |  KB Dashboard    |     |     Ollama       |
|   (Next.js)      |     |  (React+Vite)    |     |  (Local LLMs)   |
|   :3000          |     |  :8001           |     |  :11434          |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
+--------+---------+     +--------+---------+     +--------+---------+
|   RAG Backend    |     |   PostgreSQL     |     |     Qdrant       |
|   (FastAPI)      |     |   (16-alpine)    |     | (Vector Search)  |
|   :8000          |     |   :5432          |     |  :6333           |
+------------------+     +------------------+     +------------------+
```

### Key Numbers

| Metric | Value |
|--------|-------|
| Passing tests | 236 |
| Query latency | 27s --> 3-5s (6x faster) |
| Database models | 11 |
| MCP tools | 8 |
| REST endpoints | 12+ |
| Auth | JWT + RBAC (3 roles, 9 permissions) |
| Analysis prompts | 22 (Talend scanner) |

### Tech Stack

Python/FastAPI, Next.js/React, PostgreSQL, Qdrant, Ollama, Kimi K2.5, Voyage AI

> **Speaker notes:** The architecture is two independent subsystems sharing infrastructure. The RAG pipeline handles document search. The knowledge base handles scanning and doc generation. Everything is containerized with Docker. We went from 27-second query latency to 3-5 seconds by eliminating unnecessary LLM calls and switching to faster providers.

---

## Slide 4: Key Features (What Works Today)

### RAG Search & Chat
- Hybrid search: dense vectors + sparse vectors + reciprocal rank fusion
- Voyage AI reranking for result quality
- Streaming answers with numbered citations `[1]`, `[2]`
- Conversational mode with memory (Hindsight)

### Talend Scanner
- Parses XML/XMI source code into structured data
- 50-65% token reduction via distillation (strips noise before AI sees it)
- 22 analysis prompts covering architecture, data flow, error handling, security

### Auth & Access Control
- JWT httpOnly cookies (access 60min + refresh 7d)
- 3 roles (admin, manager, developer), 9 permissions
- Project-level access control + audit logging

### Token/Cost Tracking
- Every LLM call tracked: tokens, cost, latency, model, provider
- Admin usage dashboard with per-user and per-project breakdowns

### Benchmarking
- CLI runner + API + frontend dashboard
- Precision@5, LLM-as-judge relevance scoring, latency metrics

### VPS Deployment
- Docker stack, UFW + fail2ban, daily PostgreSQL backups, Caddy-ready

> **Speaker notes:** All of this is running in production on our VPS right now. The scanner handles Talend today — the plugin architecture means adding new platforms reuses 80% of the existing code. Every feature was built with production in mind: auth, RBAC, cost tracking, security hardening.

---

## Slide 5: Roadmap — Next 12 Weeks

### Phase 2: Multi-Platform (Weeks 5-9)

| Week | Deliverable |
|------|-------------|
| 5 | Workato scanner (JSON recipes, simplest platform) |
| 6 | Lobster scanner (XML configs, German iPaaS) |
| 7-8 | Azure Data Factory scanner (ARM templates + pipeline JSON) |
| 8-9 | Cross-platform migration comparison view |
| 9 | Auto-generated HTML assessment sites per project |

### Phase 3: Advanced Features (Weeks 10-12)

| Week | Deliverable |
|------|-------------|
| 10 | Runtime model switching + Bring Your Own Model (BYOM) |
| 11 | A/B model benchmarking with side-by-side comparison UI |
| 12 | Risk assessment scoring in reports |

### Dependency Flow

```
Phase 1 (DONE)     Phase 2 (Weeks 5-9)              Phase 3 (Weeks 10-12)
--------------     -------------------              ---------------------
RBAC          -->  Workato --> Lobster --> ADF  -->  Model Switching
Token Tracking -->  Migration View                -->  A/B Benchmarking
Benchmarking  -->  Assessment Sites               -->  Risk Assessment
```

> **Speaker notes:** Each new scanner shares 80% of the code structure with the Talend scanner, so they get faster to build. Phase 3 adds model flexibility — users can swap LLMs at query time, compare quality vs cost, and get risk scores in their assessment reports. Phase 3 can compress if time is tight.

---

## Slide 6: Costs & Infrastructure

### Running Costs (per month, estimated)

| Service | Cost | Notes |
|---------|------|-------|
| Kimi K2.5 (primary LLM) | ~$0.60 / million tokens | 1T MoE model, 256K context |
| Voyage AI embeddings | $0 (free tier) | 200M tokens/month, includes reranker |
| Ollama (local fallback) | $0 | Runs on VPS, no API costs |
| Hindsight memory | $0 | Uses Ollama gemma3:4b locally |
| VPS hosting | [your cost here] | 6 Docker services |
| **Total AI cost** | **< $5/month** (typical usage) | |

### Built-in Cost Controls

- Token tracking on every LLM call (input, output, embedding tokens)
- Per-user and per-model usage dashboards
- Cost calculated automatically using model pricing tables
- Full visibility: no surprise bills

### Development Effort

| Phase | Status | Duration |
|-------|--------|----------|
| Phase 1 (Foundation + Polish) | DONE | 5 weeks |
| Phase 2 (Multi-Platform) | Planned | 5 weeks |
| Phase 3 (Advanced Features) | Planned | 3 weeks |
| **Total** | | **13 weeks** |

> **Speaker notes:** The cost story is strong. By using Kimi K2.5 ($0.60/M tokens) and Voyage AI's free tier, the AI costs are negligible. The main cost is the VPS. We track every token so there are never surprise costs. Development is 1 person for 13 weeks total — 5 done, 8 remaining.

---

## Slide 7: Challenges & Next Steps

### Current Blockers

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| Need sample data for Workato/Lobster/ADF | Can't build scanners without real exports | Get customer exports or create synthetic test data |
| Voyage AI rate limit (3 RPM free tier) | Slow benchmarks | Use Ollama for speed tests, Voyage for quality |
| VPS memory (6 services) | May need upgrade when adding platforms | Monitor; split services if needed |

### What Makes This Manageable

- Scanner plugin architecture: each new platform reuses 80% of existing code
- Phase 3 can compress if time is tight (benchmarking + risk are incremental)
- All infrastructure (auth, RBAC, tracking, deployment) is done — Phase 2-3 is features only

### Immediate Next Steps

1. Deploy latest Phase 1 changes to VPS
2. Record baseline benchmark scores
3. Get sample data for Phase 2 platforms (Workato, Lobster, ADF)
4. Begin Workato scanner (simplest new platform, ~4-5 days)

> **Speaker notes:** The biggest risk is access to sample data for new platforms — without real exports we can't validate the scanners. The good news is that all the hard infrastructure work is done. Phase 2 and 3 are purely additive features on top of a solid foundation. The plugin architecture means each new scanner is faster to build than the last.

---

*Generated from project data on 2026-03-11. See `ROADMAP.md` for full details.*
