# 029: Talend analysis uses lossless semantic scopes

## Status
Superseded by ADR-032 (contract retired; see RETIRED_CONTRACTS in mcp-server/src/mcp_server/services/generation_profile.py).

## Decision

For newly created `talend-landscape-v2` OpenCode runs, analysis version 3 keeps the
deterministic atomic evidence ledger but schedules model work by bounded unit/concern
semantic scopes. Every evidence ID must be assigned to at least one declared scope.
Evidence shared by several concerns may be repeated, and that overhead is reported.

Legacy model-driven Gather is not dispatched for version 3 because semantic analysis
reads the deterministic evidence pages directly. Analysis may read only its declared
contract and evidence artifacts. It records review attestations and exact citations;
neither is treated as proof of semantic correctness.

Scope count is checked before any model call. A plan over its limit fails closed rather
than processing the first N scopes. Accepted claims persist cumulatively across repair
and resume. Per-session turn/time/processed-token limits and run-wide time/token/reported-
cost dispatch limits stop new work while retaining exact evidence and accepted claims.

Previously persisted profiles without an `analysis_version` remain version 2. They are
never silently reinterpreted as version 3; operators must start a fresh run.

## Consequences

- Exact source fidelity is independent of model compression.
- TEST_V2 planning falls from 644 atomic model tasks to 41 semantic scopes while retaining
  6,605 unique evidence records.
- Cost ceilings are dispatch boundaries, not guaranteed billing caps: concurrent work may
  already be in flight and a provider may omit reported cost. Time and processed-token
  limits are the provider-independent controls.
- A paid, manually reviewed pilot remains necessary to establish semantic quality, actual
  latency and spend.
