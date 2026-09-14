# ADR-015: Runtime Read Scope Is Per Project And Separate From Ingest Scope

## Status
Accepted 2026-08-25; extended by read grants 2026-08-26

## Context

Ingest scope decides what the scheduler polls at the vendor API. A documentation run needs
a different question answered: which of the already-polled data should this project's
documents describe. Conflating the two makes a narrowed read look like a shrinking estate,
and makes an unscoped read describe an environment that does not exist.

## Decision

Ingest scope (`environment_ids`, set in the connect dialog) decides what is POLLED. Read
scope (`doc_environment_ids`, `doc_window_days`) decides what a documentation run READS out
of what was polled. The filter is applied server-side, once, in `GET /runtime/doc-evidence`.

## Rationale

"Read scope [...] costs nothing at the vendor API and needs no re-mirroring."

"Why it matters, measured on the live werkorder mirror: unscoped, the estate reports 3392
runs / 785 failures = 23.1% failure rate. That figure describes no environment that exists
-- PROD alone is 2122 runs / 17 failures (0.8%), ACC is 1266 / 768 (60.7%). Filtering
per-aggregate instead would let one number describe PROD while its neighbour described
PROD+TST."

"A run must state its scope. Without it two runs of one project are not comparable and a
narrowed read is indistinguishable from a shrinking estate."

"`environment_ids: None` ('every mirrored environment') is a DISTINCT state from a
selection listing all of them: a later ingest-scope widening extends the former and not the
latter."

## Consequences

- The picker offers mirrored environments only; offering the provider's live list would let
  an operator select an environment that was never ingested.
- One mirror can back several documenting projects through a read grant
  (`runtime_source_read_grants`); the tenant is still polled once and the credential is
  still registered once.
- Filter on `project_id`, never `source_key`: the four insights tables carry `project_id`
  only, and a mixed predicate is two scoping rules in one lane.
- A shared read suppresses the mis-binding verdict, not the numbers: under a shared read a
  sibling project's jobs are orphans by design, so leaving `scope_mismatch` armed publishes
  an accusation against a correct configuration.

## Source
Promoted from `knowledge_base/CLAUDE.md -- Runtime READ scope` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
