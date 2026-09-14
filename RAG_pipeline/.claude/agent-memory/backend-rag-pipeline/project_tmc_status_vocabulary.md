---
name: tmc-status-vocabulary
description: Live-probed Talend TMC execution status vocabulary and payload quirks (six statuses, executionStatus twin, misfiredExecutionReason) that cannot be re-derived from the repo
metadata:
  type: project
---

TMC's real execution-status vocabulary was established on 2026-09-07 by probing two live tenants
(7,160 executions across 737 executables in a 30-day window), not from vendor docs:

- Six statuses occur in practice: `execution_successful`, `execution_failed`, `deploy_failed`,
  `terminated_timeout`, `terminated`, `executing`. Only the first two were mapped before; the
  other four were stored as `unknown`.
- `executionStatus` is the UPPER-CASE twin of `status` (`DEPLOY_FAILED`,
  `EXECUTION_TERMINATED_TIMEOUT`, `STARTING_FLOW_EXECUTION`). `status` is present on 100% of
  items, so any "first field that is present" rule makes `executionStatus` unreachable.
- `deploy_failed` is ambiguous: with `misfiredExecutionReason` it is a scheduled run that never
  started; without one it is a real deployment failure. Reasons observed:
  `PARALLEL_EXECUTION_FORBIDDEN`, `TASK_IS_PAUSED`, `MAX_DEPLOYMENT_ATTEMPTS_REACHED`, `UNKNOWN`.
- `executionType` (`MANUAL` | `SCHEDULED` | `PLAN`) is on 100% of executions.
- `errorMessage` is present on far more than the failed rows (713/2532 in one sample) — misfires
  carry one too.
- Which of these strings TMC accepts as a **query** value on
  `/processing/executables/tasks/executions?status=` was NOT probed. Only `EXECUTION_FAILED` and
  `EXECUTION_SUCCESSFUL` are in known use; `EXECUTION_RUNNING` matches no observed value and is
  suspect.

**Why:** the vendor publishes none of this, the code was calibrated on two statuses, and getting
it wrong silently converted ~15% of all runs into `unknown`. Re-probing costs a live API run
against customer tenants.

**How to apply:** when touching TMC ingest, treat this list as the observed floor, never the
closed set — an unmapped status must warn and keep the verbatim string, not be assumed absent.
Do not assume `misfiredExecutionReason` is always present on `deploy_failed`. Before changing a
server-side `status` query parameter, remember that a wrong value narrows results to nothing
rather than erroring. See [[topology-wave-build-protocol]] for how these builds are split across
agents.
