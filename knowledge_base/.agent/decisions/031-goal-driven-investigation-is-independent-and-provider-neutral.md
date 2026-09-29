# ADR-031: Goal-driven investigation is independent and provider-neutral

## Status
Superseded by ADR-032 (contract retired; see RETIRED_CONTRACTS in mcp-server/src/mcp_server/services/generation_profile.py).

## Decision

`integration-intelligence-v1` is a separate documentation pipeline. Nexus always inventories,
sanitizes and deterministically parses the complete upload into an authoritative fact graph. The
requested goal then determines the initial scope seed and how much expensive agent investigation is
needed. The coding agent owns later scope expansion and may delegate only naturally independent work
it discovers; Nexus never derives reasoning sessions from unit, key, document or token counts.

Provider mechanics live behind `providers/coding_agent/`. OpenCode is the first real adapter, not the
architecture. Methodology packs can add questions, concepts, evidence hints, outputs and sufficiency
rules, but cannot select parsers, providers, phases, batching or overwrite parser truth.

Canonical knowledge is accepted only after semantic and client-readiness validation. Catalogue,
integration dossiers, topology and RAG are deterministic projections of that accepted knowledge.
Failure or cancellation leaves the previous published baseline untouched and preserves compatible
checkpoint state.

## Why

The prior enterprise workflow replayed the estate through fixed batches and independent sessions.
That spent most token traffic rereading context while producing fragmented knowledge. Separating
complete deterministic truth from goal-dependent reasoning retains coverage without forcing every
question to pay for a whole-estate analysis.

## Consequences

- Full documentation can still investigate the complete estate coherently.
- Focused questions deeply investigate only their relevant connected subgraph.
- Resource ceilings stop and checkpoint work; they never truncate the evidence the agent may read.
- Parser facts carry exact, partial, dynamic, unresolved or unsupported state and remain immutable.
- Reingestion reuses content-addressed parsing and unaffected accepted interpretation.
- A provider is selectable only after a real adapter is registered; unsupported selections fail
  closed rather than falling back.
- Production acceptance requires a paid estate-scale pilot and manual source comparison.
