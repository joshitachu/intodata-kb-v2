Migration Sequence:
- Order the jobs for migration using the evidenced dependency tree:
  leaf jobs first, orchestration jobs last, CUSTOM jobs with their manual
  work called out inline.
- For each wave: which pipelines go live, which Talend jobs they replace,
  and the entry criteria (secrets set, integration runtime online,
  validation from the previous wave passed).

Data Validation:
- Concrete checks per target: row-count comparison queries between the
  Talend-loaded and ADF-loaded objects, checksum or aggregate comparisons
  on evidenced key columns, and reject-flow counts matched against the
  Talend equivalents.
- Provide the actual SQL for evidenced database targets, parameterized by
  schema and table names from the analysis.

Parallel Run:
- How to run Talend and ADF side by side safely for the evidenced write
  semantics: which loads are idempotent and can dual-run, which
  truncate-loads or upserts must dual-run only against a copy target, and
  how long to keep the parallel window per wave.

Cutover Steps:
- Numbered, per wave: pause the Talend schedule in TAC, run final
  validation, start the ADF trigger (deployed stopped), confirm the first
  ADF run, and record the confirmation evidence. Name the exact trigger
  and pipeline for each step.

Rollback Plan:
- Per wave: stop the ADF trigger, re-enable the Talend schedule, and state
  the data-repair action needed for each non-idempotent load identified in
  the scorecard before Talend resumes.
- State explicitly which jobs have no clean rollback and why.

Evidence Notes:
- List runbook steps that rest on unevidenced assumptions, especially
  scheduling gaps and jobs whose write semantics could not be confirmed.
