# ADR-014: Deterministic Evidence Is A Staging Artifact, Not In-Memory Provider State

## Status
Accepted 2026-08-24; still current

## Context

Runtime (TMC) evidence was the only deterministic evidence tier held as in-memory provider
state, populated inside one engine's phase driver. The OpenCode engine reuses the CONSUMER
(`_phase3_context_files`) but structurally could not reuse the producer, so it silently
shipped zero runtime facts. The lane was also gated behind `_per_job_doc_units`, which is
only populated when unit docs are enabled, so a standard run shipped nothing either.

## Decision

Producer writes files; consumers read files. One producer
(`harness._ensure_runtime_evidence`, exposed on the shared facade as
`HarnessHelpersImpl.ensure_runtime_evidence`) writes three named artifacts, and every
consumer reads them by name.

## Rationale

"Runtime evidence was previously an in-memory attribute populated inside one engine's
phase driver, which is exactly why OpenCode -- which reuses the CONSUMER
(`_phase3_context_files`) but could not reuse the producer -- silently shipped zero runtime
facts. Producer writes files; consumers read files."

"producer-writes-files/consumer-reads-files is the pattern every other evidence tier
already uses (`_cross_cutting_facts.md`, `_noncode_project_context.md`), so parity stops
depending on discipline."

## Consequences

- Absence is always stated: the rollup is written unconditionally in one of three modes
  (`data` / `opted_out` / `unavailable`) with independent `partial` and `scope_mismatch`
  flags, and the coverage artifact is written even when the fetch fails.
- The once-per-run guard is keyed on `(run_id, staging_dir)` in memory; an on-disk check
  would make a resumed run adopt a failed run's snapshot and pin `unavailable`.
- Two identifier keys are always sent (`unit.name` and `unit.display_name`) because the
  facts manifest returns `job_id`, not the display name a scheduler uses. Do not add a
  normaliser: `InspectHub_GetLocation` and `InspectHub_GetLocations` are both real,
  separately-scheduled jobs.
- Low coverage is correct, not a defect. Never accuse a project of a bad binding from match
  ratio alone; report the runtime-side orphan ratio as an observation.

## Source
Promoted from `knowledge_base/CLAUDE.md and knowledge_base/.claude/PROGRESS_LOG.md` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
