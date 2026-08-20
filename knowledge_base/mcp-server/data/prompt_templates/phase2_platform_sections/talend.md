SECTION-BY-SECTION EXTRACTION REQUIREMENTS
Cover every section below with concise, source-backed findings. The scanner artifacts remain the
complete inventory; do not reproduce large dumps. Cite the decisive component, connection, query,
code, or configuration location. State UNKNOWN with the missing evidence when a section cannot be established.

### COMPONENTS
For each assigned job, summarize:
- Job name, purpose, group membership
- The components that establish entrypoints, transformations, I/O, orchestration, or risk; cite the scanner inventory for the complete list
- The connections that establish execution order, data direction, errors, iteration, lookups, or routing
- tRunJob references: child job called, context variables passed, DIE_ON_CHILD_ERROR setting
- REPOSITORY connection references: look for PROPERTY_TYPE=REPOSITORY + REPOSITORY_PROPERTY_TYPE value
- BUILT_IN connections: note which components have hardcoded connection details
- Referenced-by: which parent jobs call this job via tRunJob
- Orphaned components: no incoming and no outgoing connections
- Missing references: REPOSITORY refs to non-existent metadata, tRunJob refs to missing jobs

### EXECUTION_FLOW
For each assigned job:
- Subjob chain in numbered execution order (tPreJob first, main body, tPostJob last)
- Decisive trigger connections: source subjob, target subjob, trigger type, and a locator for any RunIf expression
- tRunJob orchestration details: child called, vars passed, DIE_ON_CHILD_ERROR
- Parallelism: subjobs with no trigger dependencies that could run concurrently
- Iteration patterns: tFlowToIterate / tLoop / tForEach -- what they iterate over and with what bounds
- Dead paths: components or subjobs with no reachable trigger chain from any entry point

### ESB_ROUTES
CONDITIONAL -- applies ONLY to jobs the scanner classified as ESB/Camel routes
(is_esb_route=true; identifiable by c-prefixed Camel components such as cRoute, cREST,
cJMS, cKafkaInput, cSplitter, cAggregator, cWireTap, cProcessor). If NO job in this group
is an ESB route, write this heading and state "No ESB/Camel routes in this group" -- do NOT
force plain DI (tMap/t*) jobs into route framing.

For EVERY ESB route job, do NOT use the source -> transform -> target -> schema framing used
for DI jobs. Instead document the routing semantics:
- Routing rules: the from() endpoint and each to()/route destination; content-based routing
  (cMessageRouter / cRouteWhen / cRouteOtherwise) with the FULL predicate/condition expression
- Endpoint definitions: every inbound and outbound endpoint URI (cREST/cRESTClient paths and
  verbs, cJMS/cActiveMQ queues/topics, cKafkaInput/cKafkaOutput topics, cDirect/cVM channels,
  file/FTP endpoints) -- mask credentials
- EIP patterns in use: splitter (cSplitter), aggregator (cAggregator + correlation/completion
  criteria), recipient list (cRecipientList), wire tap (cWireTap), message translator
  (cProcessor / cSetBody / cMarshal / cUnmarshal) -- name the pattern and its configuration
- Error / exception channels: onException / dead-letter routing, cErrorHandler usage, redelivery
  policy, and which routes have NO exception handling (flag each explicitly)
- Route-to-route and route-to-DI-job invocation (cTalendJob, cDirect between routes)

### DATA_FLOWS
For relevant tMap components:
- Summarize source-to-target lineage and cite the deterministic lineage artifact for the complete column map
- Quote only short decisive expression fragments; otherwise describe the result and cite the exact mapping locator
- Explain material expressions and variable-table logic without copying the full mapping table
- Summarize filter/reject behavior and cite the exact output/link locator
- Join type (INNER / LEFT OUTER / UNIQUE MATCH) and lookup model between input tables

For all other data movement:
- Non-tMap FLOW connections between I/O components (direct pass-through)
- Data type mismatches between source schema and target schema
- Null handling: Relational.ISNULL(), default substitutions, NVL/COALESCE patterns
- Row rejection paths: reject links, where failed rows route to, whether they are logged

Data quality checklist per job:
- Validation present: null checks, range checks, regex validation, reference lookups
- Deduplication: how duplicates are detected and handled
- Missing validations: data flows with NO validation at all (flag each one explicitly)
- Default/fallback values: what happens when expected data is absent

### TRANSFORMATION_DETAIL
For every job with tMap or custom code transformations:
- Field-by-field mapping table: source field | transformation expression | target field | notes
- Conditional transformation patterns: ternary operators, ISNULL branches, environment switches
- Aggregation and grouping logic
- String manipulations: StringHandling.TRIM/CHANGE/UPCASE/etc. with purpose
- Date arithmetic: date parsing, formatting, offset calculations -- full expressions shown
- Lookup joins: reference table, match key, fallback when no match

### BUSINESS_RULES
Extract material business rules with this exact format:
- BR-{GROUP}-NNN (sequential, e.g. BR-DEU-001)
- Expression: a concise interpretation plus an exact evidence locator; include only a short decisive fragment when needed
- Job + component: exact names
- Domain: business area (pricing / eligibility / routing / scheduling / inventory / compliance / reporting)
- Impact: ISOLATED / MODERATE / WIDE -- what breaks if this rule is removed

Mine every source:
- tMap conditional expressions: ternary operators, Relational.ISNULL, arithmetic, string ops, date math
- tJavaRow / tJava / tJavaFlex code: every if/else branch, switch, calculation
- SQL WHERE clauses, CASE statements, JOIN conditions, HAVING clauses
- RunIf expressions on trigger connections
- tRunJob conditional execution order (structural business rules)
- tWarn / tDie conditions

Parameter catalog (separate markdown table, mandatory):
| Value | Location (job + component) | Apparent purpose | Staleness risk |
List material hardcoded dates, status codes, thresholds, magic numbers, and environment-specific constants; keep the scanner index as the full catalog.

Conflict detection: flag explicitly where the same business concept is handled differently across jobs.

### SCHEMA_DICTIONARY
For EVERY table and view referenced:
- Fully-qualified name: database.schema.table where available; note what qualification is missing
- Every column: name | data type | nullable | key constraints (if visible in scan)
- Jobs that READ it (consumers) vs jobs that WRITE it (authoritative writer vs secondary writers)
- Connection used to access it (name + type: REPOSITORY or BUILT_IN)
- Schema drift risks: multiple jobs reading same table with different column expectations -- flag each
- Tables with multiple writers: flag as potential conflict and data integrity risk

### SOURCE_SQL
For each materially distinct SQL operation, summarize:
- Component and job containing it
- Type: SELECT (source query) / lookup query / DML (INSERT/UPDATE/DELETE) / DDL
- Tables and columns referenced
- Dynamic parts: context-variable substitutions and string concatenation, with an exact query locator
- Never copy complete SQL bodies into the analysis; the scanner SQL artifact is the source of truth

### EXTERNAL_SYSTEMS
For every external connection:
- System type: relational DB / file system / FTP / REST API / SOAP / cloud storage / message queue
- Connection name and REPOSITORY vs BUILT_IN mode
- Host pattern, port, database name (mask passwords with ***)
- Jobs using this connection and their purpose (read / write / lookup / audit)
- Protocol details: JDBC driver class, REST endpoint pattern, file format

### ERROR_HANDLING
Rate EACH job: GOOD / ADEQUATE / POOR / NONE
- GOOD: tLogCatcher + meaningful error routing + die-on-error for critical paths
- ADEQUATE: some error handling but documented gaps
- POOR: minimal (one component at best)
- NONE: no error handling components at all

For each job document:
- tLogCatcher / tStatCatcher: present? what do they capture? where does output go?
- SUBJOB_ERROR connections: which subjobs have them, where errors route to
- DIE_ON_CHILD_ERROR on every tRunJob: true or false
- Try/catch in tJava / tJavaRow: present or absent
- Silent failures: subjobs with no SUBJOB_ERROR connection -- list every one explicitly
- tWarn / tDie usage: what conditions trigger them

### CUSTOM_CODE
For material tJava / tJavaRow / tJavaFlex components:
- Summarize begin/main/end behavior and cite the component; do not reproduce complete code blocks
- Purpose: what business logic or transformation it implements
- Risk checklist, evaluated for EACH block:
  - SQL injection: string concatenation building SQL? Rate HIGH/MEDIUM/LOW
  - Unvalidated input: external data used without sanitization or null checks?
  - Hardcoded values: paths, credentials, magic strings, environment-specific constants?
  - Thread safety: shared mutable state accessed from parallel subjobs or iterations?
  - Resource leaks: connections/streams/files opened without guaranteed finally-block close?
  - Encoding issues: string operations without explicit charset on a Windows deployment?

For routines referenced by jobs:
- Routine name, purpose, which jobs reference it
- Exact scanner/source locator when code is present; do not duplicate the full listing

### ENVIRONMENT_CONFIG
For EVERY context variable:
- Name | type | default value
- PROD vs TEST/DEV value differences (if present in scan)
- Whether prompt-overridable (prompt=true)
- Which jobs use it

Also extract:
- Connection strings per environment (sanitized: mask passwords)
- File path and directory patterns (are they context-variable-driven or hardcoded?)
- Feature flags or environment-conditional behavior
- tContextLoad usage: jobs that load context variables dynamically from a data source

### SCHEDULING
- Trigger type per job: cron / file arrival / event-driven / tRunJob child / manual
- Cron expressions with human-readable interpretation (e.g. "0 6 * * 1-5" = weekdays at 06:00)
- Execution windows, time zones, SLA deadlines
- Job execution dependencies: which jobs must complete before others start
- Retry configuration: auto-retry counts, backoff, failure alerts
- Recovery pattern per job: incremental re-processing (watermark/checkpoint) vs full re-run
- Gap detection: mechanism to detect missed runs, or note absence
- Concurrency controls: what prevents duplicate concurrent execution, or note absence and flag risk
- SLA windows: jobs with business deadlines and consequence of missing them

### NAMING_PATTERNS
- Job naming: prefix conventions (region codes, data domains), suffix patterns (_RAW / _STG / _FINAL)
- Table / schema naming: source vs staging vs target conventions
- Context variable naming: patterns observed
- Connection naming: naming convention for metadata connections
- Consistency rating: GOOD (clear, consistent, self-documenting) / ADEQUATE (mostly consistent with outliers) / POOR (inconsistent or unclear)
- List specific outlier examples for any rating below GOOD

### CROSS_GROUP_REFS
- Shared database tables: read by one group, written by another -- list each with both groups
- Shared context variables or context groups across groups
- Cross-group tRunJob calls: which jobs in this group call jobs in other groups
- Shared metadata connections used by multiple groups
- Execution ordering dependencies: this group must complete before group X starts

### RISK_FLAGS
Rate each risk: CRITICAL / HIGH / MEDIUM / LOW

Security risks:
- Hardcoded credentials (passwords, API keys in component params or custom code)
- SQL injection vectors (string concatenation building queries -- list every instance)
- Unencrypted sensitive data in transit or at rest
- Overly broad database permissions

Performance risks:
- Full table scans (SELECT * without WHERE filtering on large tables)
- Unbounded result sets (no TOP / LIMIT)
- Memory-intensive tMap lookups loading full tables without pre-filtering
- Sequential processing where parallel execution is possible

Reliability risks:
- No error handling on critical paths (flag each job explicitly)
- Silent failures (errors swallowed without logging -- list every SUBJOB_ERROR gap)
- Missing retry logic on network I/O operations
- No dead-letter/reject handling for failed records
- Hardcoded file paths (not using context variables)
- Missing data validation on inputs

Deep preventive analysis (mandatory for every group):
- Single points of failure: classify each as RECOVERABLE (retry/restart fixes it) vs CATASTROPHIC (requires manual data repair); note whether any fallback path exists
- Cascading failure chains: trace full blast radius from each critical job failure -- e.g. "Job_A fails -> table X has partial data -> Job_B reads stale X -> Report Y shows incorrect totals"
- Recovery gaps: non-idempotent jobs where re-run would cause duplicates, corrupt running totals, or re-fire external side effects -- flag each explicitly
- Monitoring gaps: per job, what is the detection latency if it silently produces wrong output? (minutes / hours / days / never)
- Capacity risks: jobs that will fail or miss SLA as data volumes grow -- unbounded SELECTs, full in-memory sorts, tFlowToIterate over unbounded sets

For each risk: severity | affected job(s) + component(s) | specific description | recommended remediation
