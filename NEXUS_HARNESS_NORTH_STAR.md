# Nexus Harness North Star

Last updated: 2026-07-11

This file records what Nexus must ultimately become BEYOND the current 16-slice
plan (`.plans/03_slices.md`). Completing all 16 slices delivers the V0 target of
`NEXUS_HARNESS_DESIGN_V0.md` - a design-partner-ready pilot where the "why did
this fail" investigation demo works end to end. It does NOT deliver the five
goals below. They are the finish line, not the slice plan.

Rule: when the slice plan is revised, rotated, or archived, this file survives.
Every planning session that closes a slice should check whether one of these
goals can now be promoted into concrete slices.

## Current Position (for orientation)

- Done: Slices 0-5 (rails: manifest, guarded executor, receipts spine,
  LangGraph runtime boundary, docs generation through the graph).
- Next: Slice 6 (resume/interrupts), then an explicit model-backed planner
  slice (shadow mode), then Slices 7-16.
- Felt agency arrives across Slice 6 -> planner -> 9 -> 10; Slice 11 is the
  demo proof.

## Goal 1 - Live External Connectors

What: Real Slack, Confluence, Teams/Outlook, and log-platform (e.g. Elastic)
connectors in production use - not fixtures. A customer admin enables a
connector in the UI, Nexus discovers its capabilities through the connector
contract, and investigations pull real evidence from it.

Why it is not in the slices: Slices 8-9 prove the connector contract and
evidence normalization with fixtures/mocks; live integrations are HITL add-ons
(auth, tenant approval, rate limits, data residency).

Done when: at least two live external connectors run in a customer or internal
production environment, enabled/disabled without code changes, with secrets
never appearing in observations or activity.

Promote when: Slice 8 contract is stable and a design partner names their
first system. Data-protection note: connector evidence may contain personal
data - redaction rules and residency review are part of this goal, not an
afterthought.

## Goal 2 - Entity Facts Memory

What: The learned-facts store from the design doc ("Entity Facts And Memory"):
claims attached to entities with source/provenance, timestamp, confidence,
evidence links, originating workflow run - plus expiry and revalidation so
stale facts are detectable and conflicting facts surface as conflicts.

Why it is not in the slices: Slice 10 resolves names to entities and attaches
entity references to runs; it does not persist learned claims, confidence
decay, or revalidation.

Done when: Nexus can answer "runs daily at 02:00 (learned from runtime,
observed 3 days ago, confidence 0.93)" and flags when a newer observation
contradicts a stored fact.

Promote when: Slice 11 investigations start re-deriving the same facts
repeatedly (token cost and latency will make this visible).

## Goal 3 - Proactive Behavior

What: Nexus acts without being asked: background stale-doc detection
(docs vs runtime drift), maintenance mode, scheduled re-scans, and
notifications when documentation no longer matches observed behavior.
The design doc deliberately starts chat-triggered; this goal is the graduation.

Why it is not in the slices: every slice is chat-triggered by design; the
design doc itself defers autonomous behavior until the harness model is proven.

Done when: at least one proactive workflow (stale-doc detection) runs on a
schedule, produces evidence-backed update proposals as interrupted workflow
runs awaiting approval, and never writes without the versioned/reversible
guarantees.

Promote when: Slice 12 (doc update lifecycle) is stable - proactive updates
reuse exactly that machinery with a scheduler in front.

## Goal 4 - Multi-Domain Proof

What: A second, non-data-integration domain pack (logistics, support,
marketing...) that makes the application-agnostic claim real: same Nexus core,
different vocabulary, entities, workflows, and artifact templates.

Why it is not in the slices: Slice 13 ships the pack schema plus the
data-integration pack only. Until a second pack exists, "application-agnostic"
is architecture, not evidence.

Done when: a demo shows the same core experience (investigate / generate /
update / publish) producing quality artifacts in a second domain with zero
core-code changes.

Promote when: Slice 13 schema has survived contact with the first pack, and a
concrete second-domain prospect or internal use case exists. Building it
speculatively is not worth it; building it for a real prospect is.

## Goal 5 - Production Hardening

What: What a pilot needs to become a product:

- multi-tenant scale (per-tenant isolation of packs, connectors, facts, runs)
- cost/token controls beyond per-run budgets (per-tenant quotas, alerts,
  degradation strategy)
- external publishing targets (Confluence/SharePoint as PublishingProviders)
- persistent checkpoint storage at scale, run retention/GC policies
- eval suite (Slice 19) running continuously, not once
- operational runbook for the harness itself (it must be debuggable with its
  own receipts)

Why it is not in the slices: Slice 19 adds evals and regression gates for the
demo path; the rest is explicitly out of the V0 design.

Done when: two tenants run concurrently with isolated data, enforced budgets,
and a documented incident procedure.

Promote when: first paying design partner commits, or internal usage makes
cost visible.

## Anti-Goals (unchanged from the design doc - re-affirm before promoting anything)

- Nexus does not modify application code, deploy code, or change infrastructure.
- Nexus does not mutate external business systems.
- Documentation writes stay versioned, evidence-backed, reversible.
- No regex routing as the primary planner - deterministic routing may only
  ever be a fallback.
- No merging of all UI surfaces before the harness model is proven.

## Review Cadence

Check this file at every slice-completion session (`/session-end` of a slice)
and at every re-planning session. If a goal's "promote when" condition is met,
turn it into grill/PRD input instead of letting it rot here.
