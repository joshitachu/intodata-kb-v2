Generate `{doc_name}.md` as a durable database table inventory for engineers rebuilding or validating the Kruitbosch webservices database schema.

Audience:
- Steven Knolle and engineers who need a reliable source of truth for which tables and fields are actually used.
- Developers who may later generate lean CREATE scripts from DDL plus code usage.

Output principles:
- Be table-first and field-level. Avoid generic architecture narrative unless it explains database evidence.
- Prefer compact markdown tables with exact source evidence.
- Distinguish confirmed code usage, inferred ORM usage, missing DDL, missing source, and documentation mismatch.
- Distinguish `USED_BY_CURRENT_CODE`, `PRESENT_IN_DDL_ONLY`, `PRESENT_IN_DOCS_ONLY`, `INFERRED_FROM_ORM`, `UNRESOLVED`, and `REQUIRED_BY_CONSTRAINT` fields.
- Treat structured schema facts from DDL extraction as the preferred source for data type, max length, precision, scale, nullability, defaults, keys, indexes, identity, computed expressions, and collation.
- For DDL rebuild output, distinguish runnable SQL from draft/proposed SQL.
- Do not output full connection strings, passwords, account keys, crypto keys, license keys, or secret values. Mention only safe connection key names and safe catalog/database names when available in already-redacted evidence.
- If a DDL is not available, say so plainly and mark field type/nullability/key data as `Unknown until DDL received`.
- Do not generate final CREATE TABLE scripts as authoritative without DDL. You may provide lean schema candidates marked `PROPOSED - requires DDL validation`.
- Do not treat ACC/AXTEST DDL as PRD proof unless explicit PRD equivalence evidence is supplied.
- Keep CosmosDB/non-relational containers separate from SQL Server tables.

Required document behavior:
- Start with the most important findings, especially missing DDLs and missing referenced projects.
- Include a coverage section explaining which uploaded/scanned files were analyzed and which referenced projects are absent.
- Include a database connections table mapping config keys to runtime registration names and safe usage notes.
- Include environment baseline and ACC vs PRD caveats when evidence references ACC, AXTEST, PRD, local/dev, mixed, or unknown sources.
- Include all discovered tables/entities. Do not omit tables with incomplete definitions; mark them `INCOMPLETE - DDL REQUIRED`.
- Include a table inventory with one row per table/entity and columns for connection, operation, fields read, fields written, proving SQL/ORM IDs, source evidence, confidence, and DDL status.
- Include a table definitions section for every discovered table/entity. Actual DDL definitions must be shown when available. When DDL is unavailable, show a definition matrix with observed fields and explicit unknowns for type, nullability, keys, constraints, and indexes.
- Include a field usage matrix with one row per table.field, including the proving SQL/ORM IDs for each field.
- Include an SQL query inventory with stable SQL IDs, verb, raw SQL excerpt, table/field extraction, aliases, joins, filters, parameters, and source location.
- Include a SQL-to-table traceability section that shows which SQL statements each table inventory row is based on. This is mandatory for complex SQL.
- Include ORM and implicit table usage, especially inserts/updates through POCOs.
- Include a DDL alignment and gaps section comparing code usage, documentation/inventory if present, and DDL if present.
- Include CosmosDB / non-relational caveats if CosmosDB/container evidence is present.
- Include Evidence Review and 4-Eyes Sign-off; mark `PENDING 4-EYES REVIEW` unless named review evidence exists.
- Include blocking questions for Kruitbosch, phrased as actionable DDL/source requests.
- When generating `DDL_REBUILD_SCRIPTS`, produce SQL Server compatible scripts for the new environment only where source DDL or complete schema evidence exists. Otherwise produce clearly marked draft templates with TODOs and blockers.
- `DDL_REBUILD_SCRIPTS` must reference `TABLE_INVENTORY` concepts by table name and SQL/ORM IDs so every generated column can be traced to current-version evidence.

Confidence labels:
- HIGH: directly observed SQL, ORM calls, config keys, or field accesses in scanned source.
- MEDIUM: inferred table name from POCO/entity type and ORM convention.
- LOW: referenced by generated docs or incomplete evidence but not present in scanned source.

Formatting:
- Use markdown headings and plain ASCII tables.
- Use fenced code blocks for SQL excerpts.
- Assign stable IDs: `SQL-001`, `SQL-002`, etc. for explicit SQL statements and `ORM-001`, `ORM-002`, etc. for ORM-only operations.
- Reuse those IDs in Table Inventory, Field Usage Matrix, DDL gaps, and Lean Schema Recommendations.
- For generated DDL, add comments above each table script listing the evidence IDs and DDL source status.
- For inventory PDFs or exported inventory documents, preserve the table definitions section. It is a minimum required deliverable, not optional appendix material.
- Include an `## Evidence Register` table with columns:
  `| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |`
