---
record_type: slice_7_full_agent_retirement_evidence
status: template_unverified
deployment_release: null
window_started_at: null
window_ended_at: null
consecutive_hours: null
authenticated_turns: null
prepared_by: null
prepared_at: null
reviewed_by: null
reviewed_at: null
approved_for_slice_8_deployment: false
---

# Slice 8 Retirement Evidence — Operator Template

This is an empty operator-owned template, not retirement evidence. Local or
synthetic results must not be entered as substitutes for deployed Slice 7
observation. Slice 8 deployment remains blocked while the front matter says
`template_unverified`, any required value/artifact is missing, or approval is
false.

This file is the blank template and stays blank in the repository. Fill in a
copy, and attach the signed copy to the deployment record (see
`agent-chat-cutover-runbook.md`, "Deployment authority gate").

## Observation authority

- Deployed Slice 7 image digest:
- Deployment/config record:
- Observation start and end (UTC):
- Consecutive full-agent hours (required: at least 168):
- Authenticated agent turns (required: at least 1,000):
- Query/audit artifact link:
- Reviewer notes:

## Required suite artifacts

- [ ] Conversation-163/166-shaped replay
- [ ] Paraphrase suite
- [ ] Security and cross-scope suite
- [ ] Restart/reconciliation suite
- [ ] Full backend suite
- [ ] Full frontend suite and production build

For every checked item, attach the immutable artifact URL, release/image
digest, timestamps, command or job ID, result counts, and reviewer.

## Required measured outcomes

| Measure | Required | Observed | Artifact |
|---|---:|---:|---|
| Repeat rate | <= 1% | | |
| Unexpected clarification | <= 3% | | |
| Denial correctness | 100% | | |
| Quality | >= 95% | | |
| Stale workflow cards | 0 | | |
| Duplicate effects | 0 | | |
| Unauthorized effects | 0 | | |
| Cross-scope evidence | 0 | | |
| Shadow side effects | 0 | | |
| Secret leaks | 0 | | |
| p95 latency | approved bound | | |
| Mean/p95/max cost | approved bounds | | |

Attach the exact numerator, denominator, time window, query/report, and any
incident links for every row. A blank cell or inferred zero fails the gate.

## Backup and rollback drill

- Protected pre-069 backup identifier:
- Backup timestamp, size, checksum, retention authority:
- Application image used by drill:
- Database revision before/after drill:
- Drill timestamps and outcome:
- [ ] Restored application remained on the full-agent route
- [ ] Downgrade limitations were acknowledged (empty continuation schema only)
- [ ] Authenticated smoke/security/restart checks passed after recovery

## Exceptions and incidents

List every incident, alert, waived sample, excluded turn, and data-quality gap.
An exception requires owner, rationale, scope, remediation, and reviewer; it
does not automatically satisfy a threshold.

## Sign-off

- Prepared by / date:
- Security reviewer / date:
- Operations reviewer / date:
- Product/quality reviewer / date:
- Final deployment authority / date:
- Decision: **BLOCKED — TEMPLATE ONLY**

Changing the decision requires all evidence above and setting
`status: reviewed_pass` plus `approved_for_slice_8_deployment: true` in one
reviewed change.
