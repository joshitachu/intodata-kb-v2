# Standard Agent Runtime Canary Runbook

This runbook governs Nexus agent-runtime replacement Slice 8. Phase A is
default-off and reversible. Nothing here authorizes production traffic,
migration application, full cutover, or legacy deletion without the explicit
gates in the approved plan.

## Stage authority

`AGENT_RUNTIME_V2_CANARY_STAGE` is the server-owned maximum-risk ceiling:

1. `off` — every turn uses the rollback runtime.
2. `internal_no_tool` — selected users use the standard runtime with an empty
   tool manifest.
3. `read_only` — governed read tools are visible; consequential tools are not.
4. `planning` — objective projection and durable plan controls are primary.
5. `reversible_effects` — read and `reversible_write` capabilities only.
6. `workflow_approvals` — workflows and approval-governed effects are eligible.
7. `full_standard` — 100% standard-runtime observation before cutover approval.

The existing allowlists and percentages determine who is eligible inside the
stage. Higher-risk percentages may never exceed lower-risk percentages.
`full_standard` requires read, planning, effects, and persisted live streaming
at 100% on both transports. Invalid combinations fail settings validation.

Promotion is content-blind. Query text, project name, task, incident, keyword,
regex, and model output are never stage inputs.

## Observation metrics

All labels are finite enums; prompts, answers, arguments, evidence, project IDs,
user IDs, and credentials are forbidden from metrics.

Example PromQL:

```promql
sum(increase(agent_runtime_canary_turns_total[24h])) by (stage, cohort, transport, outcome)

histogram_quantile(
  0.95,
  sum(rate(agent_runtime_canary_latency_seconds_bucket[6h]))
    by (le, stage, cohort, transport)
)

histogram_quantile(
  0.95,
  sum(rate(agent_runtime_canary_cost_usd_bucket[6h]))
    by (le, stage, cohort)
)

sum(increase(agent_runtime_canary_guard_total{result="triggered"}[24h]))
  by (stage, cohort, guard)
```

Persisted `harness_agent_turn_audits.flags` contains only the finite
`canary_stage`, `runtime_cohort`, repeat, clarification, denial, stale-workflow,
and state-conflict facts needed by the evidence collector.

## Evidence collection

Prepare a reviewed `CanaryEvidenceInputs` JSON with exact application, image,
database, config, lock, SBOM, and manifest identities; signed security/quality
reviews; and, when available, backup and rollback records. Placeholder values
such as `TBD`, `synthetic`, `template`, or `unverified` are rejected.

```bash
uv run python -m scripts.agent_runtime_canary.cli collect \
  --inputs /protected/canary-inputs.json \
  --started-at 2026-08-01T00:00:00Z \
  --ended-at 2026-08-08T00:00:00Z \
  --output /protected/canary-window.json

uv run python -m scripts.agent_runtime_canary.cli evaluate \
  /protected/canary-window.json \
  --output /protected/canary-verdict.json
```

Collection fails if an audit predates stage/cohort attribution. The evaluator
requires at least 1,000 unique authenticated audits over at least 168 hours,
all stages, at least 100 turns on each transport, no legacy turns, zero absolute
safety failures, 200 reviewed answers at 95% quality, 100% denial correctness,
95% no-evidence correctness, clarification/repeat bounds, latency/cost bounds,
restart and transport parity, verified backup/restore parity, verified rollback,
and explicit full-cutover owner approval.

## Backup and rollback rehearsal

Before a live workflow/approval stage:

1. Capture and protect a database backup tied to the exact image, application
   revision, and Alembic head.
2. Restore it into an isolated environment and verify Agent Runs, Conversation
   State, Workflow Ledger, plans, approvals, receipts, and message counts.
3. Promote only the approved bounded cohort.
4. Rehearse rollback by setting the canary stage to `off`, recreating the
   backend, checking health, and reconciling active state without duplicating an
   effect.
5. Sign the record and attach immutable evidence URIs.

The application/database identities in backup and rollback records must exactly
match the canary release or evidence validation fails.

## Cutover and deletion

A Phase B `GO` permits a separately approved full cutover. It does not permit
legacy deletion. After stable full-standard observation, obtain a distinct
irreversible deletion approval and retain a signed legacy artifact tested
against the final additive schema for at least 90 days.
