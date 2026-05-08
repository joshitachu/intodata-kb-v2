# Workato Analysis Guide

This guide replaces the individual analysis prompts for Phase 2 sub-agents.
Use it to produce a structured analysis of each recipe group's scan data.
Write markdown, not JSON. Be exhaustive -- include every datapill formula, rule, field, and risk.
Every claim needs a specific recipe name and component reference.

---

## Depth Contract

Apply this evidence standard to every section:
- Cite every claim with the exact recipe, step number, step alias, action/provider, and artifact/reference where it was found (recipe JSON, connection export, lookup table, topic schema, custom adapter file, scan warning).
- Enumerate concrete evidence before summarizing. List the actual units, expressions, fields, connections, and references first; then infer architecture, risk, or intent.
- Quote formulas, SQL, Ruby code, conditions, filters, mappings, endpoint paths, property names, lookup keys, and event topic names verbatim where present. Do not paraphrase away source evidence.
- Label scanner-limited evidence explicitly, for example: "schema not visible in scan", "connection target redacted", "job history unavailable", or "lookup row values not exported".
- For important findings include impact, confidence (HIGH/MEDIUM/LOW), risk, and affected units (recipes, steps, connectors, topics, tables, properties, callable recipes).

## Output Quality Gate

Your output is valid only if every canonical section contains cited evidence before interpretation:
- Start each section with concrete recipe/step/asset evidence. Summaries, architecture conclusions, and recommendations must come after the evidence.
- Empty sections are allowed only after stating what was checked and whether the absence is confirmed or scanner-limited.
- Any broad claim such as "uses Salesforce", "has error handling", "syncs customer data", or "depends on a lookup table" must cite the exact recipe, step number, step alias, provider/action, and source artifact.
- Important findings must include affected unit, source reference, impact, confidence, risk, and scanner limitation if applicable.
- Do not collapse multiple recipes into one generic paragraph. If several recipes share a pattern, list each recipe and then summarize the pattern.

## Anti-Shallow Output Rule

The following output patterns are invalid:
- Architecture prose without a recipe topology, callable recipe/event topic relationships, and connector/property/lookup assets listed first.
- Data-flow summaries without field-level datapill lineage and source step aliases.
- Business-rule summaries without quoting the formula, condition, SQL predicate, lookup key, property reference, or Ruby code implementing the rule.
- Error-handling claims without monitor/catch, stop action, notification, retry, job history, or failure-propagation evidence.
- Change-impact guidance without affected recipes/steps/properties/lookup tables/topics/callable recipes, required checks, rollback risk, and confidence.

## Required Evidence Tables

Include these tables when evidence exists; if evidence is absent, state the checked source and limitation:
- **Recipe topology table**: recipe, folder/group, trigger type, schedule/event/API endpoint, primary systems, child callable recipes, event topics, shared assets.
- **Step/action table**: recipe, step number, step alias, keyword, provider/action, parent block, important inputs, outputs consumed downstream.
- **Datapill lineage table**: target recipe/step/field, full datapill expression, source recipe/step alias/field, formula chain, plain-English meaning, downstream consumers.
- **Lookup/property table**: asset name, scope/type, key/value or property reference, recipes/steps using it, fallback behavior, environment/change risk.
- **Monitor/catch table**: recipe, protected steps, catch steps, stop/notification/logging/retry behavior, unprotected critical actions, failure visibility.
- **Change-impact table**: change target, affected recipes/steps/assets, required validation checks, rollback risk, confidence, scanner limitations.

---

## Workato Platform Reference

### Recipe Structure
A Workato **recipe** is an automation workflow containing:
- **Trigger (step 0)**: The entry point. Types include `new_record` (polling), `updated_record` (polling), `scheduled_event` (cron-like timer), `webhook` (push HTTP callback), `api_trigger` (REST endpoint), `new_event` (event topic subscriber), `new_file` (file/storage watcher).
- **Actions**: Processing steps that follow the trigger. Each has a `keyword: "action"`, a `provider` (app connector), and a `name` (operation such as `create_record`, `search_records`, `run_sql`).
- **Block tree**: Recursive nesting of steps. Control flow keywords (`if`, `elsif`, `else`, `foreach`, `repeat`, `monitor`, `catch`, `stop`) create nested scopes. Child steps execute within the scope of their parent keyword.
- **Step alias (`as` field)**: The unique identifier for accessing a step's output downstream via datapill references (e.g., `data.salesforce_1.account_name`).
- **Step number**: Every block node has a `number` field defining its position. Step 0 is always the trigger. Steps are numbered sequentially in depth-first order.

### Connections
- Connections encapsulate authentication credentials for external systems (Salesforce, NetSuite, PostgreSQL, REST APIs, etc.).
- Identified by a `provider` field (e.g., `"salesforce"`, `"netsuite"`, `"postgresql"`).
- Multiple recipes can share the same connection.
- Connection details include provider type, authorization method (OAuth2, API key, basic auth), and base URL.

### Datapills
- Workato's primary data mapping mechanism, analogous to Talend context variables and tMap expressions.
- Syntax: `#{_('data.step_alias.field_path')}` references output fields from previous steps.
- Dot notation traverses nested objects: `#{_('data.netsuite_1.billing_address.city')}`.
- Formula methods (Ruby-like) chain onto references: `.to_s`, `.strip`, `.upcase`, `.downcase`, `.gsub(pattern, replacement)`, `.to_i`, `.to_f`, `.round(n)`, `.to_date`, `.strftime(format)`, `.present?`, `.blank?`, `.presence || default_value`.
- Conditional expressions: `condition ? true_val : false_val`.

### Block Tree
- Each recipe is a recursive tree of blocks. The trigger is the root; actions, conditionals, loops, and error handlers form the branches.
- `if` / `elsif` / `else` -- conditional branching with boolean expressions.
- `foreach` -- iterate over a list (child steps execute once per item).
- `repeat` -- repeat child steps N times or until a condition is met.
- `monitor` -- try block (wraps steps for error handling).
- `catch` -- catch block (paired with monitor, handles errors).
- `stop` -- halt recipe execution with success or error status.

### Event Topics
- Pub/sub messaging channels for asynchronous communication between recipes (no Talend equivalent).
- A publisher recipe sends events via `publish_event` action to a named topic.
- Subscriber recipes trigger on `new_event` from that topic.
- Subscribers execute independently and asynchronously -- publisher is not notified of subscriber failures.

### Callable Recipes
- Analogous to Talend's tRunJob. A `call_recipe` action invokes a callable sub-recipe synchronously.
- The caller pauses until the callee completes. Input parameters are passed and return values are received.
- If the callee fails and the `call_recipe` is not inside a `monitor/catch`, the error propagates up and terminates the caller.

### Properties
- Account-level or project-level configuration variables (analogous to Talend context variables/groups).
- `account_property('name')` -- environment-wide properties shared across all recipes.
- `project_property('name')` -- scoped to a single project/folder.
- Used to parameterize recipes across environments (DEV, TEST, PROD).

### Custom Adapters (Ruby SDK)
- Custom connector code written in Ruby that extends Workato's built-in connector library.
- Defines connection settings, actions, triggers, and object definitions.
- Analogous to Talend's tJava/tJavaRow but written in Ruby.
- Can contain hidden business logic, validation rules, and security-sensitive code.

---

## Section Analysis Instructions

For each section below, analyze ALL recipes in your group. Be exhaustive.
Every claim needs a specific recipe name and component reference.

### COMPONENTS

Extract for each recipe:
- Recipe name, purpose, folder path, and group membership
- All steps with type (WorkatoAction/WorkatoTrigger/WorkatoCondition/WorkatoLoop/WorkatoMonitor/WorkatoCatch/WorkatoStop), step alias (`as` field), provider, action name, and role in the recipe
- Block tree structure: parent-child nesting, depth levels
- Dependencies: `call_recipe` child references, connection/provider references, event topic references, lookup table references, property references, callable recipe inputs/outputs, custom adapter references
- Referenced-by: which parent recipes call this recipe via `call_recipe`
- Orphaned steps: steps with no logical connection in the block tree
- Missing references: `call_recipe` refs to non-existent recipes, provider refs to missing connections, topic refs to undefined topics

Also catalog all non-recipe assets in the group:
- **Connections**: name, provider, connection type, authorization type, base URL
- **Event Topics**: name, schema, publishers, subscribers
- **API Endpoints**: name, method, URL pattern, linked recipe, request/response schemas
- **LCAP Apps**: name, page count, recipe bindings
- **Lookup Tables**: name, columns, estimated row count, which recipes read/write them
- **Custom Adapters**: name, actions defined, triggers defined, which recipes use them
- **Properties**: account vs project scope, name, value pattern

### EXECUTION_FLOW

Extract for each recipe:

- **Trigger analysis**: trigger type, provider, polling interval (if applicable), event topic (if subscriber), API method/URL (if API trigger), whether polling-based or event-driven
- **Recipe interfaces**: trigger input schema, callable recipe input/output schema, API request/response schema, event topic payload schema, and scanner limitations for missing schemas
- **Block tree mapping**: the complete block tree showing all nesting levels. For each node: keyword, provider, action name, step number, nesting depth, and `as` alias
- **Conditional branches**: for every `if/elsif/else` block:
  - The condition expression (from `input.conditions`)
  - The operand type (AND/OR)
  - What steps execute in each branch
  - Whether all branches are covered (is there an `else`?)
- **Loop structures**: for every `foreach` and `repeat` block:
  - What list/collection is being iterated (source datapill)
  - What steps execute per iteration
  - Whether there are batch controls or pagination
  - Risk of unbounded iteration (large lists without limits)
- **Monitor/Catch blocks**: for every `monitor`/`catch` pair:
  - Which steps are protected by the monitor
  - What recovery logic is in the catch block
  - Whether the catch includes notification, logging, retry, or stop
- **call_recipe orchestration**: for each `call_recipe` action:
  - Which callable recipe is being invoked (recipe_id resolution)
  - What input parameters are passed
  - Whether return values are used downstream
  - Position in the execution sequence
  - Error propagation behavior and whether the call is protected by monitor/catch
- **Event topic orchestration**: for each publish/subscribe pattern:
  - Publisher recipe and publish step
  - Topic name and payload mapping
  - Subscriber trigger recipe(s)
  - Whether publisher and subscriber failures are decoupled
- **Stop actions**: all `stop` keywords:
  - What triggers them (condition, catch block, validation failure)
  - Whether they stop with success or error
  - What error message is provided
  - Whether stopping could leave data in an inconsistent state
- **Execution order**: numbered sequential order accounting for nesting, conditionals, and loops
- **Dead paths**: steps or blocks that are unreachable (after unconditional stop, empty else branches, impossible conditions)

### DATA_FLOWS

For EVERY action step with input mappings:
- List every input field and its mapping expression (datapill reference or static value)
- The FULL datapill formula (do NOT truncate)
- Quote nested formulas, conditional expressions, lookup calls, property references, and concatenated strings exactly as exported
- **HUMAN-READABLE explanation** of what each expression does -- translate to plain English, do not just repeat the expression. Examples:
  - `#{_('data.salesforce_1.account_name')}` -> "Takes the account name from the Salesforce trigger output"
  - `#{_('data.salesforce_1.annual_revenue')}.to_f.round(2)` -> "Takes the annual revenue, converts to decimal, rounds to 2 places"
  - `#{_('data.netsuite_1.email')}.present? ? #{_('data.netsuite_1.email')} : 'no-email@placeholder.com'` -> "Uses NetSuite email if present, otherwise defaults to placeholder"
- Identify which step alias each datapill references (trace back to source step)
- Trace formula chains through intermediate steps: source trigger/action -> transformation action -> downstream action/output
- Identify cross-step fan-out: one step output or datapill consumed by multiple later steps
- Flag hardcoded values that should be properties or lookup table entries

For trigger data sources:
- Source system, provider, object type or table name
- Filter criteria, SQL query, or search conditions
- Output schema: all fields with data types (if available)

For data outputs (create/update/upsert/delete/run_sql actions):
- Target system, provider, object type or table name
- Write mode: CREATE, UPDATE, UPSERT, DELETE
- Fields written and their source datapill mappings
- Match/deduplication keys for upsert operations

For HTTP request/response (`send_request` actions):
- HTTP method, URL (including datapill-based path parameters)
- Request headers and body field mappings
- Response parsing: which response fields are used downstream

Cross-recipe data flows:
- `call_recipe` input/output parameters
- Event topic payloads (publisher schema to subscriber trigger)
- Shared lookup tables or Workato DB tables
- Shared external system tables (recipe A writes, recipe B reads)
- Property-driven data flows: account/project properties that change source, filter, target, URL, credentials, or routing behavior
- Schema drift indicators: datapill references to fields not present in known schemas, different recipes using different field sets for the same object, payload fields used by subscribers but not published

**Data quality checks to identify:**
- Validation patterns: `.present?` / `.blank?` checks, range checks, regex validation via `.match?()`, lookup table reference lookups
- Deduplication: how duplicates are detected and handled (upsert keys, search-before-create)
- Default/fallback values: `.presence || default` patterns
- Missing validations: which data flows lack ANY validation

### TRANSFORMATION_DETAIL

For EVERY action step that maps data (datapill references in input fields), extract a per-step transformation summary:

**Per-Step Datapill Mapping Table (MANDATORY for every action with input mappings):**

| Target Field | Source Datapill | Formula Chain | Explanation |
|-------------|----------------|---------------|-------------|
| billing_city | `#{_('data.netsuite_1.billing_address.city')}` | -- | City from NetSuite billing address |
| annual_revenue | `#{_('data.salesforce_1.annual_revenue')}` | `.to_f.round(2)` | Revenue converted to decimal, rounded to 2 places |
| email | `#{_('data.netsuite_1.email')}` | `.present? ? ... : 'no-email@placeholder.com'` | Email with fallback default |

Include ALL mapped fields, not just notable ones. For each:
- The full datapill expression (never truncate)
- The formula chain (`.to_s`, `.strip`, `.gsub()`, `.to_f.round(2)`, etc.)
- A human-readable explanation of what the mapping does

**Formula Chain Traces (for complex datapill formulas with 3+ chained methods):**

Trace the full evaluation path step by step:
```
Step 1: #{_('data.salesforce_1.billing_address')}  -> nested address object
Step 2: .city                                       -> extract city string
Step 3: .strip                                      -> remove whitespace
Step 4: .presence || 'UNKNOWN'                      -> default if blank
Result: Cleaned city name or 'UNKNOWN' fallback
```

Include at least one trace per recipe that has complex formulas.

**Conditional Mapping Analysis:**

For every conditional datapill expression (ternary `? :`, `.present?`, `.blank?`):
- The condition being tested
- The true-path value
- The false-path/fallback value
- Business rationale (why this conditional exists)

**Cross-Step Data Lineage:**

For each action that consumes datapills from multiple prior steps:
- Which steps contribute data (list step aliases)
- How fields from different sources are combined (merged into one object? concatenated? conditional selection?)
- Whether any step's output is used by multiple downstream steps (fan-out pattern)

**Lookup Table Mappings:**

For every lookup table reference:
- Table name and lookup key field
- Which column is returned
- How the result is used downstream
- Fallback behavior if lookup finds no match
- Whether the lookup result feeds a conditional branch, write target, notification, or callable recipe parameter
- Whether the lookup table is read-only configuration or is mutated by recipes in the group

### BUSINESS_RULES

Extract EVERY business rule with this format:
- **BR-{GRP}-NNN**: sequential rule ID (e.g., BR-ORD-001)
- **Expression**: the actual code/expression implementing the rule
- **Implementing recipe**: which recipe and step contains it
- **Domain**: business area (pricing, eligibility, routing, scheduling, etc.)
- **Impact**: what happens if the rule is violated or removed

Where to find business rules:
- Datapill formulas with conditionals, calculations, or lookups
- `if/elsif/else` condition expressions
- `run_sql` WHERE clauses, CASE statements, JOIN conditions
- Custom adapter Ruby code (execute methods, validation logic)
- Action input mappings with non-trivial transformations
- Recipe orchestration order (call_recipe sequences)
- Property-based conditional behavior (account_property/project_property checks)
- Lookup table entries used as category mappings or routing logic

**Extraction sub-categories** -- apply each systematically:

1. **Domain inference**: From recipe names, folder paths, connection providers, table/object names, and field names, infer the business domain each rule belongs to (sales, finance, HR, manufacturing, logistics, compliance, reporting, etc.). A recipe named `Order_Sync_SF_to_NS` and a connection to `netsuite` are domain signals. Tag every rule with its inferred domain.

2. **Datapill formula rules**: Formulas containing conditionals (`.present?`, ternary `? :`), calculations (arithmetic, `.to_f`, `.round()`), string manipulation (`.strip`, `.gsub()`, `.upcase`, `.downcase`, concatenation), and type coercions encode business logic. Extract each non-trivial formula as a separate business rule with its full expression text.

3. **SQL rules**: WHERE clauses, CASE statements, JOIN conditions, and HAVING clauses in `run_sql` action queries embed business filtering and classification logic. Extract each meaningful SQL predicate as a business rule.

4. **Custom adapter Ruby code rules**: Business logic in custom adapter `execute()` methods -- if/else chains, case statements, calculations, date comparisons, string parsing. Extract each decision branch as a separate rule. Pay special attention to code that transforms, filters, or classifies data.

5. **Recipe structure rules**: Orchestration order (call_recipe sequences), conditional execution (if blocks controlling call_recipe), and event topic pub/sub patterns reflect business processes. The fact that Recipe A must complete before Recipe B is itself a business rule. Document these structural rules explicitly.

6. **Parameter catalog**: List EVERY hardcoded date (fiscal year boundaries, migration cutoffs, go-live dates), status code, threshold value, magic number, and string constant found in datapill formulas, SQL queries, if-block conditions, and action input mappings. These are fragile business parameters. Format as a table: value, location (recipe + step), apparent purpose, risk if value becomes stale.

7. **Impact analysis per rule**: For each extracted business rule, assess its blast radius -- what downstream data, reports, or processes change if this rule is modified or removed? Is the rule duplicated across multiple recipes (creating a consistency risk)? Rate impact as: ISOLATED (single recipe, single output), MODERATE (affects downstream recipes or shared tables), or WIDE (affects multiple groups or external consumers).

8. **Conflict detection**: Identify cases where the same business concept is handled differently across recipes -- different date formats for the same field, different status code mappings, inconsistent filter conditions on the same object, different null-handling strategies for the same field. These conflicts indicate either intentional variation (document why) or bugs (flag as risk).

### SCHEMA_DICTIONARY

For EVERY table, object, and view referenced in the group:
- Table/object name (fully qualified if available -- e.g., `salesforce.Account`, `netsuite.Customer`)
- Column/field names, data types, nullable flag, key constraints (if visible)
- Which recipes READ from it (trigger or search/get/lookup actions)
- Which recipes WRITE to it (create/update/upsert/delete/run_sql actions)
- Connection/provider used to access it

Source locations: trigger output schemas, search/get action schemas, `run_sql` query text, create/update/upsert action input schemas, lookup table definitions, event topic payload schemas.

### SOURCE_SQL

If no SQL queries (run_sql actions) exist in this group, write exactly: `None`

Extract EVERY SQL query in FULL (never truncate). For each:
- Which `run_sql` action and recipe contains it
- Whether it is a source query (SELECT), DML (INSERT/UPDATE/DELETE), or DDL (CREATE/ALTER/DROP)
- Tables and columns referenced
- Any dynamic parts (datapill substitution via `#{_('data.step_alias.field')}` or property references)
- WHERE clause analysis: what business filters are applied

### EXTERNAL_SYSTEMS

For all external connections:
- System type: database, SaaS application, REST API, file storage, FTP, message queue, SMTP
- Connection name and provider type
- Authorization method: OAuth2, API key, basic auth, token-based, certificate
- Host pattern, base URL (sanitize credentials -- show `***` for passwords/tokens)
- Which recipes use this connection and for what purpose (read/write/lookup/notification)
- Criticality rating: how many recipes depend on this connection
- Topology: source system, target system, or bidirectional

### ERROR_HANDLING

Rate EACH recipe's error handling:
- **GOOD**: has monitor/catch blocks around critical actions, catch blocks include notification (email/Slack/Teams), error details are logged, stop-on-error provides clear messages, callable recipe calls are protected
- **ADEQUATE**: has some monitor/catch coverage but gaps exist (some critical actions unprotected, catch block only stops without notification)
- **POOR**: minimal error handling -- only one monitor/catch for the entire recipe or catch blocks that just stop without logging
- **NONE**: no monitor/catch blocks detected -- all failures terminate the recipe silently with only job history as evidence

For each recipe, document:
- **Monitor/Catch coverage**: which steps are inside each monitor block, what the catch block does (notification? logging? retry? stop? continue?), whether error datapills (error_message, error_step) are used
- **Unprotected critical actions**: database writes (create/update/run_sql), external API calls (send_request), file operations, and call_recipe actions NOT inside any monitor/catch
- **Stop action analysis**: where stops occur, whether they stop with success or error, what error message is provided, whether stopping leaves data inconsistent
- **Notification coverage**: which catch blocks send notifications (email, Slack, Teams), which log to persistent storage (Workato DB, external database), which have NO notification
- **Callable recipe error propagation**: for each call_recipe action, is it inside a monitor/catch? If not, callee failure terminates the caller
- **Loop error resilience**: for foreach loops, are actions inside wrapped in monitor/catch? If not, one failed item stops the entire loop
- **Event topic subscriber resilience**: do subscriber recipes have monitor/catch? Is there a dead-letter mechanism for failed events?
- **Silent failure scenarios**: specific scenarios where failure would go unnoticed (no monitor/catch and no notification, catch swallows error, subscriber fails without dead-letter, polling trigger misses records during downtime)

### CUSTOM_CODE

If no custom adapter components exist in this group, write exactly: `None`

For ALL custom adapter components:
- Adapter name and purpose
- **Connection config**: Ruby code defining connection settings (authentication, base URL, credentials)
- **Action definitions**: each action's `execute()` method with full Ruby code
- **Trigger definitions**: each trigger's polling or webhook logic
- **Object definitions**: schema definitions for custom objects
- **Hidden business rules**: validation, calculation, categorization, or routing logic embedded in Ruby code
- **External dependencies**: gem requires, API calls, system commands
- **Security assessment**: credential handling, input validation, SQL injection vectors, hardcoded secrets
- **Risk factors**: unvalidated input, thread safety, error handling gaps

For `run_sql` actions with complex SQL:
- Full SQL listing
- Dynamic parts (datapill interpolation)
- Business logic embedded in SQL (CASE statements, WHERE filters, calculated columns)

### ENVIRONMENT_CONFIG

For every property (account or project level):
- Property name, scope (account/project), and value pattern
- Which recipes reference it
- Whether it varies by environment (DEV/TEST/PROD)

Also document:
- Connection environment patterns: how connections differ across environments (naming conventions, separate connections per env)
- Hardcoded environment-specific values in recipe action inputs (should be properties)
- Feature flags or environment-conditional behavior (if-blocks checking property values)
- Lookup tables used as environment configuration

### SCHEDULING

Extract:
- **Trigger types**: polling (new_record/updated_record with interval), scheduled (scheduled_event with cron), event-driven (webhook, new_event, api_trigger), manual
- **Polling intervals**: frequency and human-readable interpretation
- **Cron expressions**: schedule with human-readable interpretation and time zones
- **Task consumption profile**: estimate tasks consumed per trigger interval (polling triggers consume tasks on every poll, even if no new records)
- **Polling vs event-driven assessment**: which recipes could benefit from switching trigger types (e.g., polling every 5 minutes when webhooks are available)
- **Dependencies between recipes**: execution ordering via call_recipe chains or event topic sequences
- **Retry configuration**: auto-retry settings, backoff patterns
- **Recovery patterns**: what happens after a failed run (automatic retry, manual re-run, gap detection)
- **Concurrency and task limits**: concurrent job settings if visible, loop/task amplification, connector rate-limit exposure, and idempotency checks for safe reruns

### NAMING_PATTERNS

Document observed conventions:
- **Recipe naming**: prefix patterns (domain codes, system names), suffixes (_sync, _export, _notify), naming consistency
- **Folder structure**: how recipes are organized into folders (by domain, by system, by process)
- **Connection naming**: naming convention for connections (environment prefix, system name, purpose)
- **Event topic naming**: naming convention for topics (domain, event type, version)
- **Property naming**: naming convention for account/project properties (namespace prefix, environment suffix)
- **Step alias naming**: whether step aliases (`as` field) use meaningful names or default auto-generated names

Rate naming quality:
- **GOOD**: clear, consistent, self-documenting names
- **ADEQUATE**: mostly consistent with some outliers
- **POOR**: inconsistent or unclear naming

### CROSS_GROUP_REFS

Identify dependencies between this group and other groups:
- **Cross-folder dependencies**: call_recipe references to recipes in other folders
- **Shared connections**: connections used by recipes in multiple folders
- **Event topic topology**: which folders publish to and subscribe from shared topics
- **Shared lookup tables**: lookup tables read or written by recipes in multiple folders
- **Shared external system tables**: one group writes to an external table that another group reads
- **Execution ordering dependencies**: group A must complete before group B (via call_recipe or event topic sequencing)

### RISK_FLAGS

Identify risks using these severity levels:
- **CRITICAL**: data loss, corruption, or security breach possible
- **HIGH**: operational failure, incorrect business results
- **MEDIUM**: maintainability, reliability, or performance concerns
- **LOW**: code quality, best practice deviations

**Risk catalog to check against:**

Security risks:
- Hardcoded credentials (API keys, tokens, passwords in action input fields or custom adapter code)
- SQL injection vectors (datapill interpolation in `run_sql` queries without parameterization)
- Unencrypted sensitive data in event topic payloads or lookup tables
- API recipes without authentication or rate limiting
- Custom adapter code with credential exposure

Performance risks:
- Unbounded queries (`search_records` or `run_sql` without WHERE filters, no pagination)
- Foreach loops over large lists without batch controls (task quota exhaustion)
- Polling triggers on short intervals with no deduplication (wasted tasks)
- Full table scans in `run_sql` (SELECT * without filtering or LIMIT)
- Sequential processing where parallel event-driven patterns are possible

Reliability risks:
- No monitor/catch on critical paths (silent failures)
- Catch blocks that swallow errors (no notification, no logging, no stop)
- Missing retry logic on external API calls
- No dead-letter handling for event topic subscriber failures
- Foreach loops without per-item error handling (one failure kills the loop)
- Unprotected call_recipe actions (callee failure terminates caller)
- Polling triggers without gap detection (missed records during downtime)
- Missing data validation on inputs (`.present?` checks absent before writes)

Maintainability risks:
- Complex datapill formulas (deeply chained methods) without documentation
- Duplicated logic across multiple recipes (should be callable sub-recipes)
- Magic numbers/strings in action input mappings
- Unused or orphaned recipes (Copy_of_, _old, _test, _backup variants)
- Inconsistent patterns across similar recipes
- Overly deep block nesting (>5 levels -- difficult to debug)

Preventive analysis (from operational patterns):
- **Single points of failure**: shared connections, shared lookup tables, single orchestrator recipes whose failure breaks the entire flow
- **Resource contention**: recipes competing for same connection rate limits, Workato task quota exhaustion under load
- **Cascading failure paths**: how one recipe's failure propagates through call_recipe chains, event topic subscribers, or shared target tables
- **Recovery gaps**: no mechanism to resume from failure point (full re-run required, re-processing already-processed records)
- **Monitoring gaps**: no logging of record counts, no notification channels, no audit trail to Workato DB or external store
- **Schema drift**: datapill references that assume static source app schemas -- field additions/removals/renames silently break mappings
- **Task consumption risks**: polling triggers consuming tasks on empty polls, foreach loops over large lists burning through daily task quota

For each risk, document:
- Severity level
- Affected recipe(s) and step(s)
- Description of the specific risk
- Recommended remediation

### DEBUG_TESTING_EVIDENCE

Extract assistant-grade operations, debugging, and testability evidence:
- Job history or runtime evidence if available: failed step, error message, retry count, task count, duration, trigger time, and whether the recipe processed zero records or partial records.
- Monitor/catch debug path: protected steps, catch variables/error datapills used, notification targets, persistent logs, stop status/message, and whether catch resumes or terminates.
- Retry and backoff evidence: connector retry settings, Workato auto-retry settings, explicit retry loops, repeat blocks used as retries, and gaps for transient HTTP/DB/rate-limit failures.
- Notification coverage: email/Slack/Teams/Webhook recipients, message body fields, included record identifiers, and missing correlation IDs.
- Idempotency evidence: search-before-create, upsert keys, dedupe lookup tables, external IDs, replay guards, and unsafe create/delete/write sequences.
- Rate-limit and task-consumption evidence: polling interval, foreach size, connector quota hints, pagination/batch size, repeated API calls inside loops, and task amplification path.
- Manual recovery evidence: rerunnable recipe inputs, replay endpoint or API trigger, gap-detection logic, checkpoint properties/lookup rows, and manual stop/restart hazards.
- Test hooks: callable recipes that can be invoked independently, sample payloads, validation stops, sandbox/test connections, and missing test fixtures.

### CHANGE_IMPACT_EXTRACTION

For each important change target, document affected units, required checks, confidence, and rollback risk:
- Recipe step change: downstream datapill consumers, branch/loop nesting, monitor/catch coverage, data sinks, callable recipe callers, event publishers/subscribers.
- Connector/connection change: all recipes using the connection, auth method assumptions, base URL/object schema differences, rate limits, and environment naming.
- Property change: account/project scope, every formula/condition/URL/query using the property, default/fallback behavior, and environment blast radius.
- Lookup table change: lookup keys, returned columns, fallback behavior, recipes reading/writing the table, and business rules driven by row values.
- Event topic change: publisher payload schema, subscriber trigger schemas, fan-out consumers, backward compatibility, and dead-letter/replay risk.
- Formula or mapping change: target fields, source datapill schema, type conversions, null/default behavior, validation coverage, and duplicate formulas elsewhere.
- API endpoint change: route/method, request/response schema, auth/rate-limit expectations, downstream callable recipes, and external clients.
- Custom adapter change: actions/triggers/object definitions affected, Ruby code paths, auth handling, schema contracts, and recipes using the adapter.
- Callable recipe change: callers, input parameters, return datapills, synchronous failure propagation, and versioning/rollback path.

### ASSISTANT_DOC_OBLIGATIONS

Phase 2 must produce enough evidence for the assistant-grade docs:
- Architecture docs need recipe topology, callable recipe chains, event topic topology, connector ownership, API endpoints, lookup/property assets, and custom adapters.
- Lineage docs need field-level datapill mappings, formula-chain traces, lookup fallback behavior, cross-step fan-out, cross-recipe event/callable payloads, and schema drift notes.
- Business-rule docs need every conditional, SQL predicate, lookup-driven mapping, property-driven branch, magic value, and orchestration rule with impact/confidence.
- Runtime/config docs need properties, connections, schedules, trigger modes, connector accounts, environment naming, retry settings, task/rate-limit evidence, and concurrency notes.
- Debugging/runbook docs need monitor/catch paths, stop actions, notifications, logs/job history evidence, rerun/idempotency guidance, and manual recovery hazards.
- Risk/change docs need affected recipes/steps, blast radius, rollback risk, required validation checks, scanner limitations, and confidence for each major finding.

### SELF_CHECK_BEFORE_RETURNING

Before returning the Phase 2 analysis, verify:
- All canonical sections are present: COMPONENTS, EXECUTION_FLOW, DATA_FLOWS, TRANSFORMATION_DETAIL, BUSINESS_RULES, SCHEMA_DICTIONARY, SOURCE_SQL, EXTERNAL_SYSTEMS, ERROR_HANDLING, CUSTOM_CODE, ENVIRONMENT_CONFIG, SCHEDULING, NAMING_PATTERNS, CROSS_GROUP_REFS, RISK_FLAGS, DEBUG_TESTING_EVIDENCE, CHANGE_IMPACT_EXTRACTION, ASSISTANT_DOC_OBLIGATIONS.
- Every major claim cites recipe name, step number/alias or asset name, provider/action, and artifact/source.
- Every important formula, condition, SQL query, Ruby snippet, property reference, lookup key, topic, API endpoint, or callable recipe reference is quoted verbatim where present.
- Scanner-limited evidence is labelled rather than hidden or guessed.
- The analysis contains enough evidence for all 9 assistant-grade docs, especially operations/debugging and change playbook material.
