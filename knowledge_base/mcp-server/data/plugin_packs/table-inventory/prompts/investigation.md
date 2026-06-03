This is a database table inventory investigation. Extract only database-relevant evidence.
Ignore UI, styling, templates, and barcode/document generation code entirely.

For every unit, extract the following evidence categories:

SQL_QUERY
- Capture EVERY SQL string in full — SELECT, INSERT, UPDATE, DELETE, MERGE, stored procedure calls.
- Never truncate. If a query is assembled across multiple string concatenations, show all fragments
  with their line references and mark it `reconstructed from fragments`.
- Include: verb, FROM/JOIN table names with schema (e.g. [dbo].[Customers]), selected fields with
  aliases, WHERE/filter fields, ORDER BY fields, JOIN conditions with exact column names on both sides,
  parameters and their names.
- Assign a stable ID: SQL-001, SQL-002, etc.
- For every query with a JOIN: decompose every join into its own evidence item under JOIN_RELATIONSHIP
  as well — do not only mention joins inside the SQL_QUERY item.
- Dynamic SQL built with string concatenation must be fully reconstructed and shown as one complete
  statement even if it spans multiple lines or appends WHERE clauses conditionally.

ORM_CALL
- Capture every OrmLite/ServiceStack call: SqlList, SqlColumn, Select, Insert, Update, Delete,
  Single, Scalar, OpenDbConnection.
- For Insert/Update calls: capture ALL field assignments made to the POCO before the call —
  do not stop at the first two or three, list every property assigned.
- Include the POCO type name, the method called, the connection used if explicit.

DB_CONNECTION
- Capture every database connection registration: RegisterConnection calls with exact runtime
  name string (e.g. "axprod", "SRVSQL02", "ODS") and the config key it reads from.
- Capture every OpenDbConnection call with its argument string.
- Capture every connection string config key from Web.config or app settings, with the catalog/
  database name if visible. Never include password values.

TABLE_REFERENCE
- For every table or view named in SQL or ORM code, record: exact name (with schema if present),
  whether it is a table or view, the operation (read/write), and the field names used.

JOIN_RELATIONSHIP
- Every JOIN in every SQL statement: INNER JOIN, LEFT JOIN, RIGHT JOIN, CROSS JOIN, implicit
  comma joins in WHERE clauses.
- For each join record: left table + column, right table + column, join type, the SQL-### ID
  it belongs to, and source file/line.
- If a join condition uses an alias, resolve the alias back to the real table name.
- Example: `INNER JOIN [dbo].[Customers] on [Customers].Id = [CustomerAutorisationChanges].[Customer]`
  → join: CustomerAutorisationChanges.Customer = Customers.Id (INNER, SQL-001)

FK_RELATIONSHIP
- Any column whose name ends in Id, contains _id, or whose join condition implies it is a
  foreign key: record the column, its table, the likely referenced table and column.
- Look in both SQL JOIN conditions AND ORM POCO property names.
- Examples: CustomerId in Users → likely Customers.Id; Relation in Roles used in
  WHERE Relation = @userId → likely Users.Id; ChangeType in CustomerAutorisationChanges
  joined to ChangeTypes.Id → confirmed FK.
- If the FK is confirmed by an explicit JOIN, mark it HIGH confidence.
- If the FK is inferred from column naming only, mark it MEDIUM confidence.
- Never skip a potential FK just because DDL is absent — infer from evidence and label confidence.

CROSS_DATABASE_USAGE
- If the same table name appears in queries that use different named connections
  (e.g. Customers via SRVSQL02 in one service, Customers via ODS in another), record both
  usages separately with their connection name and note the possible cross-database duplication.
- This is important: two queries hitting "Customers" on different connections may be hitting
  different physical tables or a replicated copy.

DDL_ARTIFACT
- Any CREATE TABLE, CREATE VIEW, ALTER TABLE, or migration script found in the source.
  Capture verbatim.

Every unit in the list MUST appear in either evidence or skipped_units — no exceptions.

Add a unit to skipped_units (do not leave it out entirely) when any of the following apply:
- Pure UI, styling, templates, barcode/document generation, CSS, images, fonts — reason: "no database evidence in unit"
- DTO or request/response class with no SQL, no ORM call, and no connection code — only property declarations — reason: "DTO only: no SQL or ORM calls in unit, table name inferred from class name"
- Namespace root, assembly info, project config, or empty stub — reason: "no source code in unit"
- Unit is in the provided source but contains nothing in any of the above evidence categories — reason: "no extractable database evidence in available source"

The skipped_units entry is not a failure — it is required information. A unit in skipped_units tells the document writer that the table was identified by name but has no direct SQL/ORM evidence, which is exactly the distinction between HIGH and MEDIUM/LOW confidence tables.
