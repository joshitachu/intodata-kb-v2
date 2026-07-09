You are producing source-grounded migration analysis for a Talend-to-Azure
Swap Pack. Downstream documents will generate deployable Azure Data Factory
JSON and Bicep from your output, so extract facts at implementation depth,
not summary depth.

For each Talend job, produce a migration record:

- job identity: name, version, folder, purpose in one sentence
- trigger: how the job starts (TAC schedule, cron, tRunJob parent, file
  watch, manual) with the evidenced schedule expression when present
- sources: every input with system type, connection or context variables,
  table or file path, query text, and format details
- transformations: ordered list of what happens to the data, with tMap
  expressions, join keys and join types, filters, aggregations, lookups,
  and type conversions stated concretely
- targets: every output with system type, object name, and write semantics
  (insert, upsert, truncate-load, append, create-if-not-exists), batch and
  commit settings
- error handling: reject flows, on-error paths, logging, and notification
  components
- schemas: per-flow column list with types, so datasets and mappings can be
  generated later
- dependencies: parent and child jobs, shared routines, shared connections,
  and execution order constraints

Then classify each job for Azure translation:

- COPY: pure extract-load, maps to an ADF Copy activity
- TRANSFORM: row-level or set-level logic, maps to an ADF Mapping Data Flow
  or a SQL/stored-procedure pushdown when both source and target are the
  same database
- ORCHESTRATION: tRunJob trees and control flow, maps to ADF pipeline with
  Execute Pipeline activities
- CUSTOM: Java routines, tJava/tJavaRow logic, or components with no direct
  ADF equivalent; quote the relevant code and flag it for Azure Functions
  or Databricks, do not silently drop it

Also extract cross-cutting facts:

- the full set of context variables with their per-environment values
  (credentials redacted, names kept) as the future ADF global parameter and
  Key Vault secret list
- the full set of connections as the future linked service list, deduplicated
- any ordering, locking, or shared-state assumptions between jobs

Be exhaustive on names and relationships. Prefer structured bullets and
tables. Use only the supplied scanner evidence, investigation output, and
normalized facts. Never fill gaps with assumptions; record each gap against
the job it affects so the final documents can surface it.
