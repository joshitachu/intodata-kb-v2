# ADR-006: The Unit Of Work Is The Unit Of Evidence

## Status
Accepted 2026-07-14; still current

## Context

A forensic audit of a real generation run traced doc claims back to the source `.item`
files and found that Phase 2 chunk agents were fabricating analyses for jobs whose
evidence they never held. A character-based chunker split job evidence arbitrarily, and a
substring-based "visible units" computation told each agent it could see units it could
not.

## Decision

A job's `## Job:` section is never split across tasks. Evidence tasks are bin-packed on
job boundaries (`evidence_tasks.py`, `MCP_PHASE2_TASK_EVIDENCE_BYTES`), and a task's
evidence header lists exactly the units it physically contains.

## Rationale

"Phase 2's unit of work IS the unit of evidence: a job's `## Job:` section is never split
across tasks, and a task's evidence header lists exactly the units it physically
contains. Never reintroduce substring-based 'visible units' computation -- group-wide
inventories mention every job name in every text window, which is how chunk agents were
tricked into fabricating analyses."

"Python assembles Phase 2 group analyses by verbatim section selection with per-task
provenance; it must never paraphrase or synthesize analysis prose. The only LLM call
after per-task analysis is the GROUP_SUMMARY/CROSS_JOB_FINDINGS synthesis."

"Phase 3 technical facts flow through deterministic per-unit evidence bundles
(`target_evidence.py`), NOT through Phase 2 narration; Phase 2 output is the
interpretation layer only. Bundles trim derived niceties first and never silently cut
verbatim code/SQL."

## Consequences

- The char-chunker, its "visible in this chunk" headers and the LLM merge task
  (`MCP_PHASE2_MERGE_MODE`) were deleted.
- Fabrication validators (component-echo, no-unavailability) act at task-acceptance level
  and feed the repair ladder; the final publish gates stay advisory.
- A bin-packed task covering several sibling jobs must be checked per job, not against the
  combined batch evidence, or a component real for one job can be attributed to a sibling
  and still pass.
- Resume identity: single-task groups keep `target_id = group`, so most caches survive;
  old `{group}_chunk_N` caches miss once and re-run.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Lessons Learned (evidence integrity rearchitecture)` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
