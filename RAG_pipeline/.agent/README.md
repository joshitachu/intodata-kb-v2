# RAG Pipeline -- Agent Documentation Index

## When to Read What

| You are...                                         | Read these files                                            |
|----------------------------------------------------|-------------------------------------------------------------|
| Understanding the overall system                   | `system/architecture.md`                                    |
| Looking up an API endpoint                         | `system/api-reference.md`                                   |
| Working on Auth / RBAC / Onboarding                | `system/subsystems.md` (Auth, RBAC, Onboarding sections)    |
| Working on Token Tracking / Benchmarking           | `system/subsystems.md` (Token/Cost, Benchmarking sections)  |
| Looking up the tech stack or model params          | `system/tech-stack.md`                                      |
| Setting up local dev for the first time            | `SOP/setup-dev.md`                                          |
| Diagnosing a runtime error or service issue        | `SOP/troubleshooting.md`                                    |
| Adding a new API endpoint                          | `SOP/add-route.md`                                          |
| Checking architecture decisions                    | `decisions/` + `knowledge_base/.agent/decisions/`           |

## Directory Contents

### system/
- `architecture.md` -- Full system architecture: services, RAG flow, auth flow, data models, Qdrant schema
- `tech-stack.md` -- Stack reference: models, parameters, library versions, key constraints
- `api-reference.md` -- All API routes grouped by domain
- `subsystems.md` -- Auth, RBAC, Token/Cost Tracking, Benchmarking, Onboarding (detailed reference)

### SOP/ -- How to do things
- `setup-dev.md` -- Get the full stack running locally from scratch
- `troubleshooting.md` -- Common failures and fixes (Ollama, Qdrant, migrations, JWT)
- `add-route.md` -- How to add a new API endpoint end-to-end

### decisions/ -- Architecture Decision Records
- `001-no-langchain.md` -- Why direct implementation over LangChain

## Related Documentation
- `CLAUDE.md` -- Platform rules, quick commands, code style, critical rules
- `knowledge_base/.agent/decisions/` -- Architecture decision records (shared across sub-projects)
