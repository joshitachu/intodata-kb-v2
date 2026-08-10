"""Prompt contract for the additive business-topology synthesis task."""

from __future__ import annotations

import json

from mcp_server.services.business_topology.models import BusinessTopology


def business_topology_prompt(*, project_id: str, scan_fingerprint: str) -> str:
    schema = json.dumps(BusinessTopology.model_json_schema(), indent=2)
    return f"""You are the final business-topology synthesis task in an existing documentation run.

Your only output is one JSON object matching the schema below. Do not use Markdown fences.
Emit the JSON object and nothing else: no preamble, no explanation, no code fences.

EVIDENCE IS PROVIDED DIRECTLY IN YOUR CONTEXT (no tool calls are needed)
The high-signal scanner evidence is attached to this prompt as context files. You do NOT
have to fetch anything; read the attached files:
1. `factual_evidence_index.json` — the bounded evidence overview (counts, artifact inventory,
   evidence IDs). Start here.
2. Scanner evidence rows: `systems.jsonl`, `applications.jsonl`, `edges.jsonl`,
   `connections.jsonl`, `relations.jsonl`, and the trimmed `artifacts_min.jsonl` artifact index.
   These are the implementation authority for system identities and directional edges.
3. `_doc_pack_business_topology.md` — the starter pack: accepted Phase 1/2 analyses (use only for
   names, purpose, ownership, intended behavior, risks, unknowns, candidate business meaning) and,
   for composite uploads, the cross-project index describing sibling sub-projects as external systems.
4. When present, `supporting_claims.jsonl` (typed runtime / intended-documented provenance) and
   `phase2_claims.jsonl` (concise implementation-analysis index). Cite the typed supporting-claim
   IDs, not a whole-file ID, for runtime, intended-state, and conflict claims.
Cite ONLY evidence IDs that appear in the attached context. Lower-signal bulk evidence
(components, full lineage, channels) may be omitted from your context; do not cite IDs you were
not given, and record any resulting gaps in `unknowns` / `coverage.notes`.

GROUNDING RULES
- Scanner facts are the implementation authority. Supporting analyses and runtime observations
  cannot replace, reverse, or add implementation relationships.
- Never promote a filename, source root, folder, component type, endpoint, queue, payload, or interface to a software system merely because its name looks system-like.
- A non-unresolved system requires explicit `scanner_system`, `scanner_application`, or
  `scanner_connection` identity evidence. Endpoints, resources, channels, filenames, components,
  and supporting prose cannot promote a system.
- Systems, business objects, purposes, and business streams are semantic conclusions. Mark them `inferred` or `proposed`, never `confirmed`, unless an approved business declaration is present in evidence.
- `observed` is reserved for a directly stated technical fact, such as a mechanism or exact mapping.
- Use `not_established` for facts the evidence does not establish. Do not guess owners, criticality, retry policies, SLAs, or business purposes.
- Every claim must cite resolvable evidence IDs from `_topology/evidence/evidence_ids.json`.
- Every technical relationship (data-stream direction and every delivery segment) must cite a
  scanner edge or artifact relation whose source and destination establish that exact direction.
  Endpoint/resource/channel direction alone cannot create an inter-system edge.
- Every data stream must list the scanner artifact IDs that implement it, and its cited directional
  evidence must name at least one of those artifacts. This preserves drill-down from L1 to technical source.
- Generic Phase 1/2 whole-file evidence IDs may support summaries, risks, and unknowns. Only typed
  `intended_documented` or approved-business evidence may support intended-state claims, and only
  typed `observed_runtime` evidence may support runtime observations. Supporting evidence never
  authorizes a new technical edge or system identity.
- If supporting evidence disagrees with scanner implementation, keep the scanner-backed relationship
  and emit a `conflicts` item citing both evidence sets. Never silently reconcile the disagreement.
- Runtime observations may only annotate existing scanner-backed streams or delivery paths through
  `runtime_observations`. They cannot create systems, integration solutions, streams, or segments.
- Runtime behavior that contradicts implementation evidence belongs in `conflicts`; it does not
  rewrite the implementation topology.
- `supporting_vs_implementation` conflicts require scanner implementation evidence plus typed
  intended/documented evidence. `runtime_vs_implementation` requires scanner implementation plus
  typed runtime evidence. `supporting_sources_disagree` requires at least two typed supporting IDs
  and no implementation-evidence IDs.
- Do not read or cite Phase 3 generated documentation; topology is derived from accepted Phase 1/2
  evidence and scanner facts only.
- Do not reproduce secrets, credentials, tokens, complete sensitive URLs, or private keys.
- Keep unresolved candidate identities separate by using system kind `unresolved` and status `not_established` or `proposed`.
- L1 content: organization systems, integration solutions, meaningful directional business data streams, and business objects.
- L2 content: ordered delivery paths, mechanisms, controls, runtime observations, conflicts, risks,
  and explicitly unknown facts.
- L3/L4 enrichment: annotations only. Do not create new technical graph edges or lineage entries.
- Prefer a smaller graph of well-supported streams over a hairball of weak guesses, while recording material omissions in `unknowns` and `coverage.notes`.
- Every emitted item requires status, confidence, and evidence_ids. Always emit coverage; its numeric
  values are deterministically recalculated after generation.

FIXED METADATA
- project_id: {project_id}
- scan_fingerprint (both top-level and metadata): {scan_fingerprint}
- schema_version: business-topology-v1

The `coverage` values will be recalculated from your citations after generation. Set them to non-negative placeholders.

JSON SCHEMA
{schema}
"""
