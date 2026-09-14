# intodoc Documentation Index

## Tasks (PRD & Implementation Plans)
- `tasks/milestone-1-scaffold.md` — Project scaffold, CLI, config, models, platform interface
- `tasks/milestone-2-talend-scanner.md` — Talend platform plugin + scanner core
- `tasks/milestone-3-llm-chunker.md` — LLM client + chunker
- `tasks/milestone-4-pass1.md` — Pass 1 (Component Inventory) end-to-end
- `tasks/milestone-5-pass3.md` — Pass 3 (Data Flows)
- `tasks/milestone-6-passes.md` — Remaining passes (2, 4-9)
- `tasks/milestone-7-synthesis.md` — Pass 10 (Synthesis) + remaining templates
- `tasks/milestone-8-export.md` — PDF + Qdrant export
- `tasks/milestone-9-polish.md` — Git analyzer, incremental mode, progress bars

## System Documentation
- `system/architecture.md` — Platform-explicit architecture, plugin system, data flow
- `system/models.md` — Pydantic model reference
- `system/config.md` — Configuration options and environment variables

## SOPs (Standard Operating Procedures)
- `sop/add-platform.md` — How to add a new platform plugin
- `sop/add-pass.md` — How to add a new analysis pass
- `sop/run-analysis.md` — How to run intodoc against a codebase

## Decisions (ADRs)
- `decisions/001-platform-explicit.md` — Why platform-explicit over auto-detect
- `decisions/002-credential-filter-first.md` — Why credential filtering runs before parsing
- `decisions/003-enterprise-artifact-fidelity.md` — Structures/XSLT/services parsing, the two deterministic ledgers, identity-first design references, and the sanitized workspace
