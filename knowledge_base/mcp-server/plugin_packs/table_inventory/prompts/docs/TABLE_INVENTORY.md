SPECIFIC REQUIREMENTS:
- This document is the source-of-truth table inventory for the scanned project, not a broad system overview.
- Open with `## Executive Findings` and include:
  - whether DDL files were available;
  - whether referenced projects/source are missing;
  - the count of confirmed tables/entities and confirmed fields;
  - the highest-risk mismatches or unknowns.
- In `## Evidence Sources and Coverage`, list scanned source files/configs, uploaded archive limitations, referenced projects not present, and any scanner limitations.
- Add `## Environment Baseline and ACC vs PRD Caveats`. Identify ACC, AXTEST, PRD, local/dev, mixed, or unknown evidence per DDL/config/source artifact. State explicitly that ACC/AXTEST DDL is not PRD proof unless PRD equivalence is confirmed.
- In `## Database Connections`, list config key, runtime registration name, provider/dialect, safe catalog/database if available, source path/line, and redaction status. Never include secret values.
- In `## Table Inventory`, include columns:
  `Table / Entity`, `Connection`, `Operation`, `Fields read`, `Fields written`, `Based on SQL/ORM`, `Source evidence`, `DDL status`, `Environment`, `Confidence`.
- Add `## Table Definitions` immediately after `## Table Inventory`. This section is mandatory and must include every discovered table/entity.
- For each table with real DDL evidence, include the actual table definition in a fenced `sql` block and a compact column matrix:
  `Column`, `Data type`, `Max length`, `Precision`, `Scale`, `Nullable`, `Default`, `Identity`, `Computed`, `Collation`, `Key/Index`, `Usage class`, `Used by SQL/ORM`, `Evidence`.
- For each table without real DDL evidence, include an `INCOMPLETE - DDL REQUIRED` definition matrix with all observed fields and explicit `Unknown until DDL received` values for data type, nullability, defaults, keys, constraints, and indexes.
- Never omit a discovered table from `## Table Definitions`; if the table is inferred from ORM or missing source, include it with LOW/MEDIUM confidence and a blocker note.
- In `## Field Usage Matrix`, include columns:
  `Table / Entity`, `Field`, `Usage class`, `Usage type`, `Used by`, `Based on SQL/ORM`, `SQL/ORM evidence`, `DDL definition`, `Confidence`.
- Add `## SQL-to-Table Traceability` immediately after `## Table Inventory`. Include columns:
  `Table / Entity`, `Proving SQL/ORM IDs`, `Why this proves usage`, `Source files / lines`, `Confidence`.
- In `## SQL Query Inventory`, assign each explicit SQL statement a stable ID (`SQL-001`, `SQL-002`, etc.), show it in a fenced code block, and then decompose it into verb, table targets, aliases, selected fields, join fields, filter fields, parameters, and source location.
- For complex SQL, include a short `Trace notes` paragraph explaining how aliases, joins, subqueries, or dynamically concatenated fragments map back to table/field usage.
- In `## ORM and Implicit Table Usage`, include OrmLite/ORM calls such as `SqlList`, `SqlColumn`, `Insert`, and named `OpenDbConnection` usage. For inferred table names, mark confidence MEDIUM unless mapping is explicit.
- In `## DDL Alignment and Gaps`, compare:
  - fields confirmed used in code;
  - fields present in any inventory/PDF/docs if supplied;
  - fields confirmed by DDL if supplied;
  - naming inconsistencies such as CUSTACCOUNT vs ACCOUNTNUM.
- Add `## CosmosDB / Non-relational Caveats` when CosmosDB, document containers, blobs, queues, or other non-relational stores are evidenced. Keep CosmosDB containers separate from SQL Server tables.
- In `## Lean Schema Recommendations`, provide proposed minimal field sets only for confirmed current usage, list the SQL/ORM IDs that justify each field, and label them `PROPOSED - requires DDL validation`.
- Lean schema recommendations must exclude `PRESENT_IN_DDL_ONLY` and `PRESENT_IN_DOCS_ONLY` fields unless they are required by a confirmed key, index, foreign key, default/computed dependency, or runtime insert requirement; mark those as `REQUIRED_BY_CONSTRAINT`.
- Add `## Evidence Review and 4-Eyes Sign-off` with checklist rows for SQL extraction, ORM extraction, DDL-backed metadata, ACC/PRD baseline, CosmosDB caveat, unresolved blockers, and reviewer status. Mark `PENDING 4-EYES REVIEW` unless named approval evidence is present.
- In `## Blocking Questions for Kruitbosch`, request exact DDLs for every involved table plus missing referenced source projects where needed.
- End with `## Evidence Register`.
