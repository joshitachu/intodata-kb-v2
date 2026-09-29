# ADR-034: Business topology -- facts come from code, judgement comes from documents, one builder serves every engine

- **Status**: Accepted
- **Date**: 2026-09-23
- **Supersedes**: nothing

## Context

The analyst-curated `output/CLIENTA_FULL_LANDSCAPE/topology/business_topology.curated.json`
is the quality bar for the business topology. It was built by hand from sixteen
verified documents plus ~500 KB of human-written constants. The contract
(`services/business_topology/models.py`) grew to hold everything that file carries,
but no producer filled the new fields: measured on disk, every generated topology
had 0% of contract_width, conditions, mechanism, lifecycle, verdict, business rules,
conflicts and runtime metrics. Only the custom harness's Phase 3.7 produced a
topology at all -- claude-cli and opencode-cli runs published none -- and the
composite org merge dropped enriched fields and was written `validated: true`
without validation.

## Decision

1. **Facts are computed; judgement is extracted.** A fact that the scan or the
   runtime mirror can state -- column counts, route predicates, deactivated designs,
   run counts, failure rates, deployed versions -- is filled by deterministic Python
   (`enrichment.py`, `stream_facts.py`, `runtime_facts.py`) and never asked of a
   model. Judgement that only the documents hold -- business rules and their owner
   questions, risks, controls, verdicts, key form -- comes from the Stage A
   extraction and stays in the documentation tier (capped at 0.6, never `observed`).
   This keeps the harness rule that Python may not write business prose: Python
   states numbers and verbatim predicates, not explanations.
2. **Absence is a stated observation, never an inference.** A stream whose units
   the runtime mirror does not cover gets a `kind="absence"` observation saying
   why (tRunJob child, route, joblet, not in read scope). Lifecycle is never set
   to `dormant` from missing coverage.
3. **One builder, strategies per engine.** `builder.build_business_topology(inputs,
   llm)` owns lane order, pruning, enrichment and validation. Each engine supplies
   only a `TopologyLLM` strategy: the harness wraps its budgeted agent-task
   dispatch, and the CLI engines use `BoundedModelTopologyLLM` with a hard 2-call
   cap. A new engine that needs a topology implements a strategy; it does not get
   its own lane code.
4. **Nothing inherits a `validated` flag it did not earn.** A composite merge, a
   stale curated overlay, or anything else layered after validation is itself
   validated and pruned against the evidence bundle of what it is served with.
   If it cannot validate it is not served, and the state says why.
5. **The secret scrubber and the secret check share one pattern set**
   (`validation.py`). A scrubber that misses what the check catches turns a
   redaction gap into a whole-lane failure, because secret findings are
   non-prunable.

## Consequences

- Topology depth no longer depends on which engine generated the documents.
- `scripts/topology_parity.py` measures any topology against the curated baseline;
  "is the generator keeping up" is a number, not an opinion.
- Fields that need upstream data stay empty rather than guessed until that data
  exists: `offered` (the scanner discards row schemas), filter conditions on scans
  that predate `Connection.params`, and deployment agreement (the doc-evidence
  endpoint does not expose `artifact_version`).
