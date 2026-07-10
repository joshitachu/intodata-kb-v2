Focus investigation on evidence needed to migrate this Talend project to
Azure. The final output must let an engineer rebuild every pipeline in Azure
Data Factory, so capture Talend implementation detail at the level of
individual components and their configuration.

Treat the following Talend artifacts as primary evidence:

- job designs: `.item` files under `process/`, including subjob layout,
  component names, and connector rows (Main, Lookup, Iterate, OnSubjobOk,
  OnSubjobError, OnComponentOk, RunIf)
- job metadata: `.properties` files (job name, version, description)
- project metadata: `talend.project`, `metadata/` connections (database,
  file, generic schema, WSDL/REST), and repository schemas
- contexts: context groups and per-environment context values under
  `context/`, with credential values redacted but variable names kept
- routines and code: user routines under `code/routines/`, tJava, tJavaRow,
  and tJavaFlex component code
- joblets and orchestration: tRunJob call trees, parent-child parameter
  passing, and any TAC/Job Conductor export, cron, or scheduler evidence

For every job, capture:

- source components and their exact configuration: connection or context
  variables used, table names, queries, file paths, file formats, delimiters,
  encodings, header rows
- transformation components: tMap mappings per output (expression, join keys,
  join model inner/left, reject flows), tFilterRow conditions, tAggregateRow
  group-by and operations, tSortRow keys, tNormalize/tDenormalize,
  tConvertType, tReplace rules
- target components and write semantics: insert, update, upsert, truncate,
  create-table actions, batch sizes, commit behavior
- error handling: tDie, tWarn, tLogCatcher, tLogRow, reject links, and where
  rejected rows are written
- schemas per flow: column names, types, lengths, nullability, and date
  patterns

Redact credential values but always keep the variable or connection name,
for example `context.DB_PASSWORD (redacted)`.

If evidence is missing, say exactly what is missing and which job or
component is affected. Do not guess endpoints, tables, queries, schedules,
credentials, or component settings. A named gap is more useful to the
migration than a plausible invention.
