You are generating Phase 2 analysis for a database table inventory.
Your output is the factual foundation for a field-level table inventory document.

Primary objective:
- Identify every database table, view, stored query target, ORM-mapped entity, and field that is actually used by the scanned code or present in supplied DDL/inventory documents.
- Separate confirmed facts from inferred facts.
- Detect gaps between code usage, available documentation, and DDL/source schema evidence.
- Establish the current-version schema baseline needed to rebuild the database objects in a new environment.
- Keep a hard distinction between objects/fields used by current code and objects/fields that merely exist in DDL or documentation.
- Never include database passwords, account keys, license keys, crypto keys, or full connection strings. Use redacted connection names, database/catalog names when safely available, and source paths/line numbers.

Extraction rules:
- Inspect C#, Razor, config, project files, generated facts, and source excerpts for SQL and ORM usage.
- Capture raw SQL strings exactly when present, including SELECT/INSERT/UPDATE/DELETE verbs, FROM/JOIN targets, WHERE fields, ORDER/GROUP fields, parameters, and aliases.
- Capture ServiceStack OrmLite and other ORM operations such as SqlList, SqlColumn, Insert, Update, Delete, OpenDbConnection, and named connections.
- Treat POCO writes such as `Db.Insert(entry)` as implicit table usage. Record the POCO type, properties assigned before the call, and the likely table name as inferred unless an explicit mapping is present.
- Treat referenced but missing projects/files as coverage gaps, not as evidence to invent tables.
- When DDL is absent, state that column types/nullability/keys are unknown. Do not infer SQL Server types from C# types unless clearly marked as proposed.

Required database-focused analysis:
- DATABASE_CONNECTIONS: connection keys, logical connection names, registration points, safe catalog/database names when available, and redaction status.
- SQL_STATEMENTS: every explicit SQL statement with a stable ID such as `SQL-001`, file path, line, method/context, connection used if known, verb, raw SQL, tables, fields, aliases, parameters, joins, filters, and confidence.
- SQL_PROVENANCE: for every table and field extracted from SQL, record which `SQL-###` statement(s) prove it, the exact source file/line, and the raw SQL excerpt. Complex SQL must be decomposed into table aliases and field origins.
- TABLE_USAGE: one row per table/entity with operation types, fields read, fields written, source evidence, connection, proving `SQL-###` IDs or ORM call IDs, and confidence.
- TABLE_DEFINITIONS: for every discovered table/entity, collect its definition. If DDL is present, capture exact schema/table name, columns, data types, nullability, default constraints, identity/computed settings, primary keys, foreign keys, indexes, unique constraints, and source DDL path. If DDL is absent, create an `INCOMPLETE - DDL REQUIRED` definition from observed fields only and mark every unknown type/key/nullability explicitly.
- FIELD_USAGE: one row per table.field with read/write/filter/join/sort/group usage, proving `SQL-###` IDs or ORM call IDs, evidence location, and whether the field is confirmed by DDL.
- ORM_USAGE: ORM/ServiceStack operations, POCO/entity types, assigned properties, and inferred table mappings.
- DDL_STATUS: DDL present/missing per table, unknown types/nullability/keys, and exact questions required to resolve it.
- FIELD_EVIDENCE_CLASS: classify every table.field as exactly one of `USED_BY_CURRENT_CODE`, `PRESENT_IN_DDL_ONLY`, `PRESENT_IN_DOCS_ONLY`, `INFERRED_FROM_ORM`, or `UNRESOLVED`. Lean schema candidates may include only `USED_BY_CURRENT_CODE` plus DDL-required key/index/dependency columns explicitly marked as `REQUIRED_BY_CONSTRAINT`.
- DDL_GENERATION_INPUTS: for each table, collect the fields, data types, max length, precision, scale, nullability, defaults with constraint names/values, primary keys, foreign keys, indexes, unique constraints, identity seed/increment, computed expressions, collations, schemas, and dependencies only when confirmed by DDL/schema facts or explicit source evidence.
- NEW_ENVIRONMENT_READINESS: identify which objects can be generated as runnable DDL, which can only be generated as draft placeholders, and which are blocked by missing DDL/source.
- DOCUMENTATION_GAPS: mismatches such as fields used in code but missing from inventory docs, fields in docs but unused by code, and naming inconsistencies such as CUSTACCOUNT vs ACCOUNTNUM.
- LEAN_SCHEMA_CANDIDATES: minimal field set actually needed by current code, explicitly marked as proposed until DDL is received.
- ENVIRONMENT_BASELINE: identify whether each DDL/source/config item is ACC, AXTEST, PRD, local/dev, mixed, or unknown. Do not treat ACC/AXTEST DDL as PRD proof unless explicit PRD equivalence is supplied.
- COSMOSDB_USAGE: if CosmosDB or non-relational storage is referenced, identify database/container names, document model evidence, and mark it separately from SQL Server DDL. Do not normalize a CosmosDB container into multiple SQL tables without evidence.

Evidence discipline:
- Every table and field claim must include a source path and line number when available.
- Every table/field row derived from SQL must include one or more proving SQL IDs from SQL_STATEMENTS.
- If a table/field appears in multiple SQL statements, list all proving SQL IDs and summarize the different usage roles.
- If SQL is dynamically assembled across multiple strings, include the source line range or nearest line references for each fragment and show the reconstructed SQL as `reconstructed from fragments`.
- Do not claim a DDL script is runnable unless types, nullability, keys, and required constraints are confirmed by DDL/source evidence.
- Do not claim a DDL script is runnable for PRD unless the DDL source is PRD or PRD equivalence is explicitly confirmed.
- If only code usage is known, produce a candidate field list, not an authoritative CREATE TABLE.
- The final inventory must never omit a table just because its definition is incomplete. Include it with `INCOMPLETE - DDL REQUIRED`.
- Use HIGH confidence for explicit SQL/ORM calls in scanned source, MEDIUM for direct POCO-table inference, and LOW for missing-source or undocumented assumptions.
- If ServiceInterface or ServiceModel projects are referenced but not included, call that out as a blocker for complete table inventory.
- Do not say files are unavailable if scan/source evidence shows them present. Do say when referenced sibling projects are not in the uploaded archive.
- Include an `Evidence Review and 4-Eyes Sign-off` status. Unless named reviewer approval evidence is supplied, mark the output `PENDING 4-EYES REVIEW`.
