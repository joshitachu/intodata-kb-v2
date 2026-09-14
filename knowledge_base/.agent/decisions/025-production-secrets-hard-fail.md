# ADR-025: Production Secret Validation Hard-Fails At Startup

## Status
Accepted 2026-04-21; still current

## Context

Default credentials were previously reported as startup warnings. Warnings in a container
log are not read.

## Decision

`validate_production_secrets()` aborts startup in production mode on a default JWT secret,
a default admin password, or an empty `MCP_INGEST_API_KEY`. There is no public
`/auth/register`; the first admin is created once through `POST /auth/bootstrap-admin`,
which returns 409 forever after the first user exists.

## Rationale

"Silent warnings got missed. Default JWT secret, default admin password, and empty
`MCP_INGEST_API_KEY` now abort startup in production mode."

"Smallest attack surface: endpoint returns 409 forever once any user exists. Login page
checks `/auth/bootstrap-status` on mount and switches into admin-creation mode
automatically on a fresh stack."

## Consequences

- Provider API keys are the deliberate exception: `check_provider_credentials()` returns a
  list of warnings and never exits, because operators may intentionally run with only some
  providers configured. Hard exit belongs only to auth and ingest keys.
- A default deployment ships no model API key, so it starts clean, passes health checks,
  and then fails at the first generation run. This is documented in the runbook rather than
  papered over.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
