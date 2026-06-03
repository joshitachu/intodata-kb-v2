Assess the run only for database table-inventory quality.

Score these dimensions from 1 to 5:
- SQL extraction completeness: all explicit SQL statements found and decomposed into tables, fields, parameters, and operation type.
- ORM/entity extraction completeness: ORM calls, POCO inserts/updates, named connections, and implicit table mappings captured.
- Field-level accuracy: each field has read/write/filter/join/sort/group usage and exact evidence.
- DDL/documentation alignment: DDL availability, PDF/documentation mismatches, naming conflicts, and missing fields clearly separated.
- DDL metadata completeness: DDL-backed fields include exact type, max length, precision, scale, nullability, default, identity, computed, collation, key, and index evidence where present.
- Environment handling: ACC/AXTEST/PRD/local/unknown evidence is clearly separated and ACC/AXTEST is not presented as PRD proof without confirmation.
- Review readiness: unresolved blockers and 4-eyes review/sign-off status are explicit.
- Redaction safety: no passwords, account keys, crypto keys, license values, or full connection strings are exposed.
- Actionability: output gives Steven a clear list of DDL/source requests and lean schema candidates without claiming unsupported certainty.

Flag as CRITICAL:
- Any secret value appears in generated documentation.
- A table/field is claimed as confirmed without source evidence.
- Missing DDL is treated as if types/nullability/keys are known.
- Used lean schema includes fields that are only present in DDL/docs without marking them `REQUIRED_BY_CONSTRAINT`.
- ACC/AXTEST DDL is presented as PRD-authoritative without explicit equivalence evidence.
- CosmosDB/non-relational containers are expanded into SQL tables without source evidence.
- DDL-backed output omits type length, precision, scale, nullability, defaults, keys, or indexes when those details are present in schema evidence.
- Referenced but absent projects are ignored when they affect database completeness.

Flag as HIGH:
- Explicit SQL statements are summarized without field-level decomposition.
- ORM writes such as `Db.Insert(...)` are omitted.
- Code-vs-documentation mismatches are not separated from DDL gaps.
- Evidence Review and 4-Eyes Sign-off is missing or not marked pending when no named review evidence exists.
