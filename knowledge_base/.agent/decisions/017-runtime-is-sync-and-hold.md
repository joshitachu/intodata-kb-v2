# ADR-017: Runtime Data Is Synced And Held In Postgres, Never Vectorized

## Status
Accepted 2026-06-16; still current

## Context

Two models were considered for answering questions about live integration runs: pull from
the runtime platform per question, or mirror it into our own store. Separately, the
retrieval stack already existed, so vectorizing logs into Qdrant looked like reuse.

## Decision

Model 2, "sync-and-hold": a Postgres store fed by pluggable source adapters. Logs are
queried through predefined parameterized tools over Postgres and are NEVER vectorized into
Qdrant.

## Rationale

"Chat product: per-question source calls put latency/rate-limits/availability on the
critical path and can't be queried/trended; a held store decouples chat and absorbs the
still-undecided source behind the existing provider seam."

"Log volume too large to embed; questions hinge on exact tokens (ids/codes/timestamps) that
vector search misses; filters are structured predicates. Predefined parameterized tools
(not free text-to-SQL) keep RBAC server-side. Only future exception: semantic 'similar
failures' over failure-summary text."

"Runtime ingestion is source-agnostic and DB-first: runtime source -> adapter -> central DB
-> insight/health APIs -> dashboard/chat. Adapters may use source-specific mechanics, but
the store, APIs, chat tools, and dashboard models stay generic. Prevents TMC from becoming
the product model."

## Consequences

- Source-specific endpoint mechanics belong only in the adapter module and its tests. No
  generic DB, schema or API type may be source-shaped.
- Providers are a Strategy/registry: adding one is a registry entry plus a module exposing
  a `provider` singleton.
- Polling still runs inside the API process, so a misconfigured interval contends with
  requests; moving it out is open work.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
