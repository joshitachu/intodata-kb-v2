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

### SCHEMA_DICTIONARY

For EVERY table and view referenced in the group:
- Table/view name (fully qualified if available)
- Column names, data types, nullable flag, key constraints (if visible)
- Which jobs READ from it (source operations)
- Which jobs WRITE to it (target operations)
- Database connection used to access it

Source locations: tMSSqlInput/tMySqlInput query params, tMap output schemas, tBigQueryOutput table names, FLOW connection schemas, metadata connection definitions.

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

For each risk, document:
- Severity level
- Affected job(s) and component(s)
- Description of the specific risk
- Recommended remediation
