# Talend Analysis Guide

This guide replaces the 22 individual analysis prompts for Phase 2 sub-agents.
Use it to produce a structured analysis of each job group's scan data.
Write markdown, not JSON. Be exhaustive -- include every expression, rule, column, and risk.

---

## Talend Platform Reference

### Job Structure
A Talend **job** is a data integration workflow containing:
- **Components** (nodes): tMSSqlInput, tMap, tBigQueryOutput, tRunJob, tJava, tLogCatcher, etc.
- **Connections** (edges): FLOW (data rows), SUBJOB_OK/SUBJOB_ERROR (triggers), COMPONENT_OK/COMPONENT_ERROR, ITERATE, LOOKUP
- **Subjobs**: groups of components chained by FLOW connections; subjobs chain via trigger connections
- **tPreJob / tPostJob**: setup and teardown blocks that run before/after the main body

### Context Variables and Groups
- Context variables are environment-specific parameters (PROD, TEST, DEV, Default)
- A **context group** defines a set of variable values for one environment
- Jobs reference context groups via `contextName` property (Default if unset)
- Variables are typed: String, int, long, Boolean, Password, Date, etc.
- Variables can be marked `prompt=true` (overridable at runtime)

### Connection Configuration
- **REPOSITORY mode**: references a shared metadata connection (look for `PROPERTY_TYPE=REPOSITORY` + `REPOSITORY_PROPERTY_TYPE`)
- **BUILT_IN mode**: connection details hardcoded in the component (look for `PROPERTY_TYPE=BUILT_IN`)
- The `metadata_connection_ref` field links components to named connections

### tMap Anatomy
- tMap is the primary transformation component with input tables, output tables, and var tables
- **Input tables**: each maps to an incoming FLOW connection
- **Output tables**: each maps to an outgoing FLOW connection
- **Var table**: intermediate variables for complex expressions
- **Expressions**: Java-like syntax applied to each output column; may call routines, use context vars
- **Filter conditions**: per-output-table filter that accepts/rejects rows
- **Reject connections**: output tables can have `reject=true` to capture filtered rows
- **Join model**: INNER, LEFT OUTER, or UNIQUE match between main and lookup inputs
- **Lookup model**: first match, last match, all matches, unique match

### Key Component Patterns
- **tRunJob**: orchestrates parent-child job execution; passes context vars; `DIE_ON_CHILD_ERROR` controls error propagation
- **tLogCatcher / tStatCatcher**: captures runtime errors/warnings/stats
- **tJava / tJavaRow / tJavaFlex**: custom Java code blocks (begin/main/end for tJavaFlex)
- **tFlowToIterate / tLoop / tForEach**: iteration patterns
- **tWarn / tDie**: explicit warning/error raising
- **tContextLoad**: loads context variables dynamically from a data source

---

## Section Analysis Instructions

For each section below, analyze ALL jobs in your group. Be exhaustive.
Every claim needs a specific job name and component reference.

### COMPONENTS

Extract for each job:
- Job name, purpose, and group membership
- All components with type, unique_name, and role in the job
- All connections: source -> target, connection type (FLOW/SUBJOB_OK/etc.), label
- Dependencies: tRunJob child references, metadata connection references (REPOSITORY refs)
- Referenced-by: which parent jobs call this job
- Orphaned components: components with no incoming or outgoing connections
- Missing references: REPOSITORY refs to non-existent metadata, tRunJob refs to missing jobs

### EXECUTION_FLOW

Extract for each job:
- Subjob chain: list each subjob and its trigger connection to the next, in execution order
- tPreJob/tPostJob blocks: what setup and teardown operations occur
- Trigger conditions: for every SUBJOB_OK, SUBJOB_ERROR, COMPONENT_OK, COMPONENT_ERROR, and RunIf connection -- document source, target, trigger type, condition expression
- tRunJob orchestration: child job called, context variables passed, DIE_ON_CHILD_ERROR setting
- Execution order: numbered sequential order (tPreJob first, main body, tPostJob last)
- Parallelism: subjobs with no trigger dependencies on each other (can run in parallel)
- Iteration loops: tFlowToIterate, tLoop, tForEach patterns -- what they iterate over
- Dead paths: subjobs or components with no reachable trigger chain

### DATA_FLOWS

For EVERY tMap component:
- Source table.column -> target table.column mapping (full column-level lineage)
- The FULL tMap expression (do NOT truncate)
- Human-readable explanation of what each expression does
- Filter/reject conditions with their expressions
- Join type and lookup model between input tables

Also extract:
- Non-tMap data movements: direct FLOW connections between I/O components
- Data type transformations: type mismatches between source and target columns
- Null handling: ISNULL checks, default value substitutions, NVL/COALESCE patterns
- Data volume indicators: row count columns, batch sizes, iteration counts
- Row rejection: reject links, filter conditions, where rows get routed on failure

**Data quality checks to identify:**
- Validation patterns: null checks, range checks, regex validation, reference lookups
- Deduplication: how duplicates are detected and handled
- Default/fallback values: what happens when expected data is missing
- Schema enforcement: explicit type casting, length constraints
- Missing validations: which data flows lack ANY validation

### BUSINESS_RULES

Extract EVERY business rule with this format:
- **BR-{GRP}-NNN**: sequential rule ID (e.g., BR-DEU-001)
- **Expression**: the actual code/expression implementing the rule
- **Implementing job**: which job and component contains it
- **Domain**: business area (pricing, eligibility, routing, scheduling, etc.)
- **Impact**: what happens if the rule is violated or removed

Where to find business rules:
- tMap expressions (especially non-trivial ones with conditionals, calculations, or lookups)
- tJavaRow code blocks
- tJava code blocks
- SQL WHERE clauses and CASE statements
- Filter conditions on tMap output tables
- Context variable logic (environment-specific behavior)
- tRunJob conditional execution (RunIf expressions)

**Extraction sub-categories** -- apply each of these lenses systematically:

1. **Domain inference**: From job names, table names, and context variable names, infer the business domain each rule belongs to (pricing, eligibility, routing, scheduling, inventory, compliance, reporting, etc.). A job named `DEU_PricingCalc_STG` and a table named `t_eligibility_rules` are domain signals. Tag every rule with its inferred domain.

2. **tMap expression rules**: Expressions containing conditionals (`Relational.ISNULL()`, ternary operators `condition ? valueA : valueB`), calculations (arithmetic, date math), string manipulation (`StringHandling.TRIM()`, `StringHandling.CHANGE()`, concatenation), and type coercions encode business logic. Extract each non-trivial tMap expression as a separate business rule with its full expression text.

3. **SQL rules**: WHERE clauses, CASE statements, JOIN conditions, and HAVING clauses in tMSSqlInput/tMySqlInput/tOracleInput query params embed business filtering and classification logic. Extract each meaningful SQL predicate as a business rule -- e.g., `WHERE status_code IN ('A','P') AND effective_date <= GETDATE()` encodes an eligibility rule.

4. **Java code rules**: Business logic embedded in tJava/tJavaRow/tJavaFlex code blocks -- if/else chains, switch statements, calculations, date comparisons, string parsing. Extract each decision branch as a separate rule. Pay special attention to code that transforms, filters, or classifies data.

5. **Job structure rules**: Orchestration order (tRunJob sequences), conditional execution (RunIf expressions), and subjob trigger patterns reflect business processes. The fact that `Job_A` must run before `Job_B` via SUBJOB_OK is itself a business rule about process ordering. Document these structural rules explicitly.

6. **Parameter catalog**: List EVERY hardcoded date (fiscal year boundaries, migration cutoffs, go-live dates), status code, threshold value, magic number, and string constant found in expressions, SQL, and custom code. These are fragile business parameters that break silently when business rules change. Format as a table: value, location (job + component), apparent purpose, risk if value becomes stale.

7. **Impact analysis per rule**: For each extracted business rule, assess its blast radius -- what downstream data, reports, or processes change if this rule is modified or removed? Is the rule duplicated across multiple jobs (creating a consistency risk)? Rate impact as: ISOLATED (single job, single output), MODERATE (affects downstream jobs or shared tables), or WIDE (affects multiple groups or external consumers).

8. **Conflict detection**: Identify cases where the same business concept is handled differently across jobs -- different date formats for the same date field, different status code mappings, inconsistent filter conditions on the same table, different null-handling strategies for the same column. These conflicts indicate either intentional variation (document why) or bugs (flag as risk).

### SCHEMA_DICTIONARY

For EVERY table and view referenced in the group:
- Table/view name (fully qualified if available)
- Column names, data types, nullable flag, key constraints (if visible)
- Which jobs READ from it (source operations)
- Which jobs WRITE to it (target operations)
- Database connection used to access it

Source locations: tMSSqlInput/tMySqlInput query params, tMap output schemas, tBigQueryOutput table names, FLOW connection schemas, metadata connection definitions.

**Additional schema extraction depth:**

- **Fully-qualified names**: Use `database.schema.table_name` format wherever the scan data provides database and schema context (from connection metadata, SQL query prefixes, or component configuration). When the full qualification is not available, note what is missing (e.g., "schema unknown -- only table name visible").
- **Column-level metadata**: For each column, document: data type (as declared in the component schema), nullable flag, primary key membership, and foreign key references (if visible in scan data, SQL constraints, or inferable from JOIN conditions). Use "unknown" for metadata not present in the scan rather than omitting the column.
- **Data ownership**: For each table, identify which job is the **authoritative writer** (source of truth -- the job that creates or fully refreshes the data) vs which jobs are **consumers** (read-only) vs which are **secondary writers** (append, update, or upsert). Flag tables with multiple writers as potential conflict points.
- **Connection cross-reference**: For each table, document which metadata connection (by name and type) is used to access it. If the same table is accessed via different connections in different jobs, flag this as a potential environment mismatch risk.
- **Schema drift risks**: Flag tables accessed by multiple jobs where column expectations differ -- e.g., Job A reads columns `[id, name, status]` while Job B reads `[id, name, status, created_date]`. This indicates schema drift risk where a DDL change could break one job but not another.

### SOURCE_SQL

Extract EVERY SQL query in FULL (never truncate). For each:
- Which component and job contains it
- Whether it is a source query (SELECT), lookup query, DML (INSERT/UPDATE/DELETE), or DDL
- Tables and columns referenced
- Any dynamic parts (context variable substitution via `context.varName`)

### EXTERNAL_SYSTEMS

For all external connections:
- System type: database, API, file system, FTP, cloud storage, message queue
- Connection name and whether REPOSITORY or BUILT_IN mode
- Host pattern, port, database name (sanitize credentials -- show `***` for passwords)
- Which jobs use this connection and for what purpose (read/write/lookup)
- Protocol details: JDBC drivers, REST endpoints, file formats

### ERROR_HANDLING

Rate EACH job's error handling:
- **GOOD**: has tLogCatcher + meaningful error routing + die-on-error for critical paths
- **ADEQUATE**: has some error handling but gaps exist
- **POOR**: minimal error handling
- **NONE**: no error handling components at all

For each job, document:
- tLogCatcher/tStatCatcher presence and what they capture
- SUBJOB_ERROR connections: which subjobs have them, where errors route to
- DIE_ON_CHILD_ERROR settings on tRunJob components
- Try/catch patterns in tJava/tJavaRow code
- Silent failures: subjobs with no error connection where failure would be invisible
- tWarn/tDie usage: explicit error/warning raising

### CUSTOM_CODE

For ALL tJava, tJavaRow, tJavaFlex components:
- The FULL code block (begin, main, end for tJavaFlex)
- Purpose of the code (what business logic or transformation it implements)
- Hidden business rules embedded in the code
- External dependencies (imports, library calls, system commands)
- Risk factors: SQL injection, unvalidated input, hardcoded values, thread safety

For custom routines:
- Routine name and purpose
- Which jobs reference it
- Full code listing (if available in scan data)

**Risk factor checklist** -- evaluate EVERY custom code block against these categories:

- **SQL injection risk**: String concatenation building SQL queries (e.g., `"SELECT * FROM " + context.tableName` or `"WHERE id = " + row.userId`) without parameterization. Any dynamic SQL constructed from external input (context variables, row data, file contents) is a vector. Rate: HIGH if user-facing input reaches the query, MEDIUM if only internal context variables are used.
- **Unvalidated input**: External data (context variables, file contents, API responses, database query results) used in logic without validation or sanitization -- e.g., parsing a string to integer without try/catch, using a file path from a context variable without checking for path traversal, trusting API response structure without null checks.
- **Hardcoded values**: Magic strings, URLs, file paths, credentials, IP addresses, or environment-specific values embedded directly in custom code rather than sourced from context variables. Each hardcoded value is a deployment risk (breaks when moving between environments) and a maintenance risk (requires code change instead of configuration change).
- **Thread safety**: Shared mutable state (static variables, class-level fields, global collections) in routines or tJavaFlex begin/end blocks that may be called from parallel subjobs or concurrent tFlowToIterate iterations. Unsynchronized access to shared state causes intermittent, hard-to-reproduce data corruption.
- **Resource leaks**: Database connections, file handles, input/output streams, or network sockets opened in custom code but not closed in a finally block. Particularly risky in tJavaFlex where the begin block opens a resource and the end block closes it -- if the main block throws an exception, the end block may not execute depending on error handling configuration.
- **Encoding issues**: String operations (substring, replace, regex, byte-to-string conversion) performed without explicit encoding specification. On Windows servers with cp1252 default encoding, UTF-8 data processed without explicit charset declaration causes silent character corruption -- especially in accented characters, currency symbols, and special punctuation.

### ENVIRONMENT_CONFIG

For every context variable:
- Variable name, type, and default value
- PROD vs TEST/DEV value differences
- Whether it is prompt-overridable
- Which jobs use it

Also document:
- Connection strings per environment (sanitized -- mask passwords)
- File paths and directory patterns
- Feature flags or environment-conditional behavior
- tContextLoad usage: jobs that load context variables dynamically

### SCHEDULING

Extract:
- Trigger types: cron, file arrival, event-driven, manual
- Cron expressions with human-readable interpretation
- Execution windows and time zones
- Dependencies between jobs (execution ordering)
- Retry configuration: auto-retry counts, backoff, failure alerts
- Recovery patterns: what happens after a failed run

Where to look: tCron components, process properties, tRunJob chains, job design metadata.

**Additional scheduling analysis:**

- **Recovery patterns**: After a failed run, what happens? Document whether each job supports incremental re-processing (e.g., processes only records newer than a watermark/checkpoint) vs requiring a full re-run (truncate-and-reload). For jobs that require full re-run, assess the cost: how long does a full re-run take, and does it risk re-processing already-committed data to downstream systems? Flag jobs with no documented recovery strategy.
- **Gap detection**: Is there a mechanism to detect missed runs? Look for patterns like: comparing `last_successful_run` timestamps against expected schedule, sentinel files or database flags written on successful completion, monitoring queries that check data freshness. If no gap detection exists, flag this as a monitoring gap -- missed runs will be invisible until downstream consumers notice stale data.
- **SLA windows**: Identify time-sensitive jobs -- jobs that must complete before a business deadline. Look for: end-of-day processing cutoffs, market-open data requirements, report generation windows, upstream feed arrival times, regulatory submission deadlines. Document the window (e.g., "must complete by 06:00 CET") and the consequence of missing it.
- **Concurrency controls**: Can the job safely run concurrently with itself? Document what prevents duplicate execution: file locks, database advisory locks, TAC exclusivity settings, sentinel table rows, or nothing at all. If no concurrency control exists, assess the risk of double-execution: would it produce duplicate records, corrupt running totals, or cause deadlocks?

### NAMING_PATTERNS

Document observed conventions:
- Job naming: prefix patterns (region codes, data domains), suffixes (_RAW, _STG, _FINAL)
- Schema/table naming: source vs staging vs target naming conventions
- Variable naming: context variable naming patterns
- Connection naming: naming convention for metadata connections
- Consistency assessment: how consistently are conventions followed across the group

Rate naming quality:
- **GOOD**: clear, consistent, self-documenting names
- **ADEQUATE**: mostly consistent with some outliers
- **POOR**: inconsistent or unclear naming

### CROSS_GROUP_REFS

Identify dependencies between this group and other groups:
- Shared database tables (read by one group, written by another)
- Shared context variables or context groups
- Cross-group tRunJob calls
- Shared metadata connections
- Execution ordering dependencies (group A must complete before group B)

### RISK_FLAGS

Identify risks using these severity levels:
- **CRITICAL**: data loss, corruption, or security breach possible
- **HIGH**: operational failure, incorrect business results
- **MEDIUM**: maintainability, reliability, or performance concerns
- **LOW**: code quality, best practice deviations

**Risk catalog to check against:**

Security risks:
- Hardcoded credentials (passwords, API keys in component params or custom code)
- SQL injection vectors (string concatenation in SQL queries without parameterization)
- Unencrypted sensitive data in transit or at rest
- Overly broad file/database permissions

Performance risks:
- Missing indexes (queries on large tables without WHERE optimization)
- Full table scans (SELECT * without filtering)
- Memory-intensive patterns (loading entire tables into tMap lookups without filtering)
- Unbounded result sets (no TOP/LIMIT on large queries)
- Sequential processing where parallel is possible

Reliability risks:
- No error handling on critical paths
- Silent failures (errors swallowed without logging)
- Missing retry logic on network operations
- No dead-letter/reject handling for failed records
- Hardcoded file paths (not using context variables)
- Missing data validation on inputs

Maintainability risks:
- Complex tMap expressions (>100 chars) without documentation
- Duplicated logic across multiple jobs
- Magic numbers/strings in expressions
- Unused components or dead code paths
- Inconsistent patterns across similar jobs

Preventive analysis (from operational patterns):
- Single points of failure: components or connections whose failure breaks the entire flow
- Resource contention: jobs competing for same database connections or file handles
- Cascading failure paths: how one job's failure propagates through tRunJob chains
- Recovery gaps: no mechanism to resume from failure point (full re-run required)
- Monitoring gaps: no tStatCatcher, no logging of record counts, no alerting hooks

**Deep preventive analysis** -- go beyond listing risks to trace their full operational impact:

- **Single points of failure (detailed)**: Identify components, connections, lookup tables, or external systems whose failure breaks the entire flow with no fallback. For each, classify as RECOVERABLE (transient failure -- retry or restart fixes it, e.g., network timeout to a database) vs CATASTROPHIC (requires manual intervention or data repair, e.g., corrupt lookup table, lost intermediate file). Document whether any fallback path exists (e.g., a SUBJOB_ERROR branch that routes to an alternative data source or graceful degradation).

- **Resource contention (detailed)**: Identify jobs that compete for the same database connections (same connection metadata, same server/database), same file handles (same file paths in tFileInput/tFileOutput), or same temp directories. Assess whether concurrent execution of these jobs would cause: deadlocks (mutual blocking on database locks), data corruption (simultaneous writes to same table or file), connection pool exhaustion (too many concurrent connections to same server), or temp file collisions (same filenames in same directory). Rate contention risk per resource.

- **Cascading failure paths (detailed)**: For each critical job, trace the full blast radius from initial failure to downstream impact. Map the propagation path: which tRunJob parents would fail (based on DIE_ON_CHILD_ERROR settings), which shared database tables would have stale or partial data, which downstream jobs would operate on incorrect inputs without knowing. Produce a failure propagation chain: `Job_A fails -> table X has partial data -> Job_B reads stale X -> Report Y shows incorrect totals`. Identify cascade breakers (error handling that stops propagation) and cascade amplifiers (fan-out patterns where one failure triggers many downstream failures).

- **Recovery gaps (detailed)**: Identify jobs with no mechanism to resume from the failure point -- where a full re-run is required after any failure. Assess idempotency: if the job re-runs after partial completion, does it produce correct results? Look for patterns that break idempotency: INSERT without prior DELETE/TRUNCATE (causes duplicates), auto-increment IDs (gap in sequence), external side effects (emails sent, API calls made) that cannot be undone. Flag jobs where re-running is dangerous (non-idempotent) vs merely expensive (idempotent but slow).

- **Monitoring gaps (detailed)**: Identify jobs with no observability hooks -- no tStatCatcher, no record count logging, no row-count validation between source and target, no alerting on zero-row results, no execution time tracking. For each gap, assess the detection latency: how long would it take to notice a failure? Minutes (if downstream job fails immediately), hours (if checked by batch monitoring), days (if only noticed when business users see stale reports), or never (silent data quality degradation with no consumer checks).

- **Capacity risks**: Identify jobs that will break or degrade as data volumes grow. Look for: unbounded SELECT queries without WHERE filtering or LIMIT/TOP, in-memory sorts of entire tables (tSortRow without upstream filtering), tMap lookups that load full reference tables into memory, iteration patterns (tFlowToIterate) over unbounded result sets, file operations that read entire files into memory. For each, estimate the growth trajectory: is the data source growing linearly, exponentially, or bounded? At what data volume will the job fail or exceed its SLA window?

For each risk, document:
- Severity level
- Affected job(s) and component(s)
- Description of the specific risk
- Recommended remediation
