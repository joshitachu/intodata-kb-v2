# RAG Pipeline -- Agent Documentation Index

## When to Read What

| You are...                                         | Read these files                                            |
|----------------------------------------------------|-------------------------------------------------------------|
| Understanding the overall system                   | `system/architecture.md`                                    |
| Looking up an API endpoint                         | `system/api-reference.md`                                   |
| Working on Auth / RBAC / Onboarding                | `system/subsystems.md` (Auth, RBAC, Onboarding sections)    |
| Working on Token Tracking / Benchmarking           | `system/subsystems.md` (Token/Cost, Benchmarking sections)  |
| Looking up the tech stack or model params          | root `ARCHITECTURE.md` section 3, `backend/src/config.py`   |
| Setting up local dev for the first time            | `SOP/setup-dev.md`                                          |
| Diagnosing a runtime error or service issue        | `SOP/troubleshooting.md`                                    |
| Adding a new API endpoint                          | `SOP/add-route.md`                                          |
| Checking architecture decisions                    | `knowledge_base/.agent/decisions/`                          |

## Directory Contents

### system/
- `architecture.md` -- RAG-specific notes (auth flow, data models, Qdrant payload, frontend BFF); the full flow is in root `ARCHITECTURE.md`
- `api-reference.md` -- Main API routes grouped by domain (`backend/src/api/routes/` is authoritative)
- `subsystems.md` -- Auth, RBAC, Token/Cost Tracking, Benchmarking, Onboarding (detailed reference)

### SOP/ -- How to do things
- `setup-dev.md` -- Get the full stack running locally from scratch
- `troubleshooting.md` -- Common failures and fixes (model providers, Qdrant, migrations, JWT)
- `add-route.md` -- How to add a new API endpoint end-to-end

## Related Documentation
- `CLAUDE.md` -- RAG rules, code style, environment, licence enforcement
- root `readme.md` -- start commands and ports
- `knowledge_base/.agent/decisions/` -- Architecture decision records (shared across sub-projects)
