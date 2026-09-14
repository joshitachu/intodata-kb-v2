# ADR-026: Middleware Order: IP Allowlist Is Registered Before ProxyHeaders

## Status
Accepted 2026-04-21; still current

## Context

Behind nginx, every request arrives from the reverse proxy's address. Any middleware that
makes a decision from the client IP sees the proxy unless the forwarded headers have
already been applied.

## Decision

Register `IPAllowlist` BEFORE `ProxyHeaders` on both the RAG backend and MCP.

## Rationale

"Starlette stacks LIFO, so dispatch is reversed. The allowlist must see the real client IP
(rewritten by ProxyHeaders), not the reverse proxy's IP. Same trick fixes slowapi
rate-limiting behind nginx."

## Consequences

- The same class of defect recurs anywhere the client IP is not forwarded end to end: a
  Next.js proxy that does not forward `X-Forwarded-For`, or a `TRUSTED_PROXIES` value that
  does not cover the Compose bridge range, collapses every per-IP limit into one shared
  bucket keyed on the frontend container.
- A generated env file that sets `TRUSTED_PROXIES` twice silently keeps the last value.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
