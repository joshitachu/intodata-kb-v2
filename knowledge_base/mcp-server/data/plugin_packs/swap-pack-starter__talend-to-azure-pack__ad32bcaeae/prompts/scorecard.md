Generate a migration-readiness scorecard for this Talend-to-Azure migration
from the supplied analysis and evidence.

Score each Talend job on:

- migration complexity: LOW for pure copy jobs, MEDIUM for mapped
  transformations with direct ADF equivalents, HIGH for custom Java code,
  unusual components, or undocumented dependencies
- evidence completeness: whether sources, targets, schemas, transformations,
  and triggers are all confirmed by evidence, with the missing items named
- automation confidence: how much of the job can be expressed in the
  generated ADF artifacts versus needing manual engineering, and why

Call out migration risks that would matter during cutover:

- Java routines and tJava code with no declared ADF equivalent
- jobs whose schedule or trigger could not be found in evidence
- shared state between jobs (temp tables, files, sequence dependencies)
- write semantics that change behavior under retry (non-idempotent loads)
- credential and connection names that must exist in Key Vault before the
  generated artifacts can run
- volume or performance assumptions that evidence does not confirm

Score only what the evidence supports. Mark runtime-only conclusions as low
confidence unless source evidence confirms them. Do not invent incidents,
SLAs, owners, schedules, data volumes, or compliance claims.
