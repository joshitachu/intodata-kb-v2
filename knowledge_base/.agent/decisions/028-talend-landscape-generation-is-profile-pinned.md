# ADR-028: Talend Landscape Generation Is Profile-Pinned

## Status

Superseded by ADR-032 (contract retired; see RETIRED_CONTRACTS in mcp-server/src/mcp_server/services/generation_profile.py). Originally: Accepted 2026-09-08

## Context

Scanner, evidence, claim and prompt contracts can evolve independently. Resuming a run or
reusing a partition under a newer contract can produce plausible documents whose sections
were built from incompatible evidence semantics. Historical NULL configuration cannot safely
mean “whatever the current default is.”

## Decision

The upgraded Talend workflow is selected by the immutable `talend-landscape-v2` generation
profile. The profile records engine, evidence/requirement/claim/prompt versions and effective
budgets in the database, staging manifest and published audit receipt. Resume, phase rerun,
partition children and Git reingestion retain it. A mismatch starts a fresh attempt; NULL
always means legacy.

OpenCode is the v2 execution engine, while scanner evidence, deterministic references and
the root-aware routine packet are shared with the custom compatibility path. The v2 profile
does not become the default until frozen and real-estate acceptance demonstrates better
grounding and retention at acceptable cost.

## Consequences

- File existence alone never authorizes reuse across profile versions.
- Exact supported source material remains accessible in deterministic references even when
  authored synthesis is partial.
- Coverage counts grounded requirements; skipped units remain accounted-for gaps.
- Highest exported Talend artifact version is export selection evidence, not deployed-version
  evidence. Deployment and business truth require their own authorities.
- Live comparison and default migration are separate, explicitly budgeted decisions.
