# intodoc Platform -- Agent Documentation Index

## When to Read What

| You are...                        | Read these files                                    |
|-----------------------------------|-----------------------------------------------------|
| Starting any session              | This file                                           |
| Generating documentation          | `system/mcp-workflow.md`                              |
| Understanding the architecture    | `system/architecture.md`, root `ARCHITECTURE.md`    |
| Adding a new project to scan      | `SOP/add-new-project.md`                            |
| Working on the scanner (intodoc)  | `intodoc/.agent/README.md`, `intodoc/CLAUDE.md`     |
| Working on the MCP server         | `system/mcp-server.md`                              |
| Working on the dashboard          | `system/dashboard.md`                               |
| Setting up the dev environment    | root `readme.md`, `SOP/setup-dev-environment.md`    |
| Debugging an issue                | `SOP/troubleshooting.md`                            |
| Changing a design                 | the relevant ADR in `decisions/`                    |

## Directory Contents

### system/ -- How things work
- `architecture.md` -- Platform architecture, components, data pipeline
- `mcp-workflow.md` -- custom-harness doc generation pipeline; its phase table is the one place the phase list is kept, plus all harness implementation notes (packs, prompts, budgets, runtime evidence, topology, composite runs)
- `mcp-server.md` -- MCP tools, REST API endpoints, git connections, living-doc reingestion
- `dashboard.md` -- Dashboard components, features, API integration

### SOP/ -- How to do things
- `add-new-project.md` -- Register and scan a new project
- `setup-dev-environment.md` -- Get the platform running locally
- `troubleshooting.md` -- Common issues and fixes

### decisions/ -- Architecture Decision Records

The canonical home for cross-cutting architecture decisions across BOTH sub-projects
(knowledge_base and RAG_pipeline). Consolidated here on 2026-09-04 from the former
per-sub-project `PROJECT_MEMORY.md` files (no longer in the repo).

Read the ones touching your area before changing it. Grouped by theme:

- **Doc generation & evidence** -- `001` MCP over CLI, `002` staging swap,
  `004` scanner facts computed once, `005` bounded evidence for core generation,
  `006` unit of work = unit of evidence, `013` documentation is an evidence tier,
  `014` producer writes files, `019` failure knowledge is data,
  `032` OpenCode is a Claude CLI transport adapter,
  `033` model-sized frames are never read with readline, `035` a zero must never be ambiguous
- **Topology & landscape** -- `007` redaction captures structure,
  `008` topology publication approved, `009` landscape API is read-only evidence,
  `010` semantic topology is additive, `027` platform-explicit scanning,
  `034` business topology: facts from code, judgement from documents
- **Superseded by ADR-032** (contracts retired) -- `028` Talend landscape generation is
  profile-pinned, `029` lossless semantic scopes, `030` enterprise Talend method is a
  fail-closed knowledge-model projection, `031` goal-driven investigation is independent
  and provider-neutral
- **RAG & retrieval** -- `003` RAG push is server-side, `022` no LangChain /
  provider chain, `023` layered RAG default canonical
- **Runtime & capability** -- `015` runtime read scope per project,
  `017` runtime is sync-and-hold, `018` runtime scope from principal,
  `020` no raw log exposure, `021` capability identity from registry,
  `036` the runtime-to-scan binding is provider-declared, never inferred
- **Packs & connections** -- `011` git credentials centralized, `012` one pack format
- **Security & deployment** -- `016` RBAC duplicated across services,
  `024` one VPS per client, `025` production secrets hard-fail,
  `026` IP allowlist before ProxyHeaders, `037` subscription licence is a signed
  file verified offline, `038` licences issued centrally and pushed,
  verified before storing

New decisions continue the numbering. One decision per file; do not add a count to
this heading -- a hardcoded number here would drift the moment a file is added.

## Related Documentation
- `knowledge_base/CLAUDE.md` -- Platform identity and rules; root `CLAUDE.md` -- services and quick commands
- `intodoc/CLAUDE.md` -- Scanner CLI architecture and known pitfalls
- `intodoc/.agent/` -- Scanner-specific SOP and ADRs
- `mcp-server/OPENCODE_OPERATOR.md` -- OpenCode transport adapter operation
- `mcp-server/docs/git-document-updates.md` -- Git-driven document update lane
