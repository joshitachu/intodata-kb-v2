# ADR-024: One VPS Per Client; Tenant Identity Is A Deploy-Time Artifact

## Status
Accepted 2026-04-17 / 2026-04-21; still current

## Context

An earlier design carried a `Tenant` database table. Multi-tenancy inside one stack would
have meant every query, every Qdrant filter and every branding lookup carrying a tenant
predicate that must never be forgotten.

## Decision

One VPS per client. Tenant configuration (name, slug, environment, branding) lives in
environment variables per deployment and is surfaced through a public
`GET /api/v1/tenant/config`. The Qdrant collection name is env-driven.

## Rationale

"Dropped the earlier Tenant DB table (schema-only). Deploying one VPS per client keeps
tenants fully isolated; branding/subdomain/feature-flags live in env vars per deployment.
No shared tenants table needed."

"One VPS per client means each stack already has its own Qdrant; parameterizing the name
lets us run multiple stacks on one host (dev/staging) without collision, and keeps options
open if we ever move to multi-tenant within a single Qdrant."

"Smaller blast radius, simpler audit. Port offsets are kept for dev/staging where multiple
stacks share a host. `docker compose -p <client>` handles per-client container/volume
prefixing automatically."

## Consequences

- The tenant config endpoint must be public, because the login page brands before
  authentication.
- A single shared `<TenantMark />` component prevents drift across sidebar, login and
  wizard.
- Mission Control is push-based: the customer stack calls out to the aggregator, which
  never calls in. This avoids inbound firewall rules on customer servers.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
