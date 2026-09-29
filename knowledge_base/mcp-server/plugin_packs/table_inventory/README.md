# Table Inventory Plugin Pack

This pack produces one focused `TABLE_INVENTORY` document for codebases where
the main question is: which database tables and fields are actually used?

It is designed for a client webservices analysis led by the integration consultant:

- extract SQL from `.cs`, `.cshtml`, and config-driven source evidence;
- capture ServiceStack OrmLite usage and implicit POCO table writes;
- compare code usage against DDLs and inventory documents when available;
- flag missing DDLs, missing referenced projects, and naming mismatches;
- avoid bloated schemas by separating confirmed current usage from proposed lean fields;
- never expose full connection strings or secret values.

Expected output:

- database connection inventory;
- table/entity inventory;
- table definitions for every discovered table/entity, complete when DDL exists and
  explicitly incomplete when DDL is missing;
- field usage matrix;
- explicit SQL query inventory;
- SQL-to-table traceability showing which SQL statement proves each table/field;
- ORM/implicit table usage;
- DDL alignment and gap list;
- blocking questions for ClientB.
- DDL rebuild script document for the new environment, marked RUNNABLE, PARTIAL,
  or DRAFT ONLY depending on available DDL evidence.

The pack should never present a usage-derived table as authoritative DDL. If
ClientB supplies full DDLs, the generated rebuild scripts can preserve types,
nullability, keys, constraints, and indexes. If DDLs are missing, the output must
show blockers and draft placeholders instead.

Use this as a plugin pack with a harness step, the same way as `quick-summary`
and `integration-assessor`.
