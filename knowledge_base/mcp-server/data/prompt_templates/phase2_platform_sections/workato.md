SECTION-BY-SECTION EXTRACTION REQUIREMENTS
Write every section below. Be exhaustive within each -- no placeholders, no "see above", no "N/A" without justification.
Never call a Workato recipe a "job" in the architectural sense: in Workato terminology a job is one execution instance of a
recipe (one trigger event flowing through it) -- use "recipe" for the design-time unit and "job" only when discussing runs.

### COMPONENTS
For EVERY recipe and callable function:
- Recipe name, purpose, folder path, group membership
- Trigger (step 0): type (new_record / updated_record polling, scheduled_event, webhook, api_trigger, new_event topic subscriber, new_file), provider, polling interval or cron schedule
- Every step: keyword (action/if/elsif/else/foreach/repeat/monitor/catch/stop), step number, step alias (the `as` field -- the handle other steps reference via datapills), provider, action/operation name, parent block nesting depth
- Connector accounts used per step: provider, authorization type (OAuth2, API key, basic auth, token, certificate)
- call_recipe references: which callable recipe is invoked, whether the call is inside a monitor/catch
- Referenced-by: which parent recipes call this recipe via `call_recipe`
- Orphaned steps: no logical connection in the block tree
- Missing references: call_recipe refs to non-existent recipes, provider refs to missing connections, event-topic refs to undefined topics

Also catalog non-recipe assets in the group:
- Connections: name, provider, auth type, base URL
- Event Topics (pub/sub, no Talend equivalent): name, schema, publisher recipes, subscriber recipes
- API Platform endpoints (`api_trigger`): endpoint name, HTTP method, URL pattern, bound recipe, request/response schema
- Lookup Tables: name, columns, which recipes read/write them
- Custom Adapters (Ruby SDK connectors): name, actions defined, triggers defined, which recipes use them
- Properties: account-level (`account_property`) vs project-level (`project_property`) scope, name, value pattern

### EXECUTION_FLOW
For EVERY recipe:
- Full block tree in depth-first order: keyword, provider, action, step number, nesting depth, `as` alias for every node
- Trigger analysis: polling interval (if polling), event topic (if `new_event` subscriber), API method/URL (if `api_trigger`); note whether polling-based (consumes tasks on every poll, even empty ones) or event-driven
- Every `if` / `elsif` / `else` block: full condition expression (from `input.conditions`), AND/OR operand type, what executes on each branch, whether an `else` exists
- Every `foreach` / `repeat` block: source list/collection datapill, steps per iteration, batch/pagination controls, unbounded-iteration risk
- Every `monitor` / `catch` pair: which steps are inside the monitor, what the catch does (notify / log / retry / stop / continue)
- Every `call_recipe` action: callee recipe, input parameters passed, whether return values are consumed downstream, whether the call is inside monitor/catch (unprotected callee failure terminates the caller)
- Every `stop` action: what triggers it, success vs error status, error message, whether it can leave data inconsistent
- Event-topic publish/subscribe pairs: publisher recipe + step, topic name, payload mapping, subscriber recipe(s) -- publisher and subscriber failures are decoupled and independent
- Dead paths: steps unreachable after an unconditional stop, empty else branches, impossible conditions

### DATA_FLOWS
For EVERY action step with input mappings:
- Every input field's mapping: the full datapill expression `#{_('data.step_alias.field_path')}`, never truncated
- Formula chains (Ruby-like methods): `.to_s`, `.strip`, `.upcase/.downcase`, `.gsub(pattern, replacement)`, `.to_i`/`.to_f`, `.round(n)`, `.to_date`, `.strftime(format)`, `.present?`/`.blank?`, `.presence || default` -- quote the full chain, then give a human-readable translation
- Which step alias each datapill traces back to; trace formula chains through intermediate steps to their ultimate source
- Cross-step fan-out: one step's output consumed by multiple later steps
- Trigger data sources: source system/provider, object/table, filter/search criteria, output schema fields
- Write actions (create/update/upsert/delete/`run_sql`): target system, write mode, fields written and source datapills, upsert match/dedup keys
- HTTP `send_request` actions: method, URL (including datapill path params), header/body mappings, which response fields are consumed downstream
- Cross-recipe flows: call_recipe input/output parameters, event-topic payload (publisher schema -> subscriber trigger), shared lookup tables, shared external tables, property-driven routing
- Schema drift indicators: datapill references to fields absent from known schemas, inconsistent field sets across similar recipes
- Data quality: `.present?`/`.blank?` checks, regex via `.match?()`, dedup strategy, `.presence || default` fallbacks, data flows with NO validation (flag each)

### TRANSFORMATION_DETAIL
For every action step with datapill mappings, produce a per-step mandatory field table:
| Target Field | Source Datapill | Formula Chain | Explanation |
Include ALL mapped fields, never truncate the expression. For chains with 3+ methods, trace step by step
(`Step 1: ... -> nested object`, `Step 2: .city -> extract city`, ...). For every ternary/`.present?`/`.blank?`
conditional: condition, true-path value, false-path/fallback value, business rationale. For lookup-table
reads: table name, lookup key, returned column, downstream use, fallback when no match.

### BUSINESS_RULES
Extract EVERY business rule as BR-{GROUP}-NNN with: full formula/condition/SQL/Ruby expression (never truncate),
implementing recipe + step, inferred business domain, and impact rating (ISOLATED / MODERATE / WIDE).
Mine every source:
- Datapill formulas with conditionals, calculations, string ops, type coercions
- `run_sql` WHERE clauses, CASE statements, JOIN conditions, HAVING clauses
- Custom adapter Ruby `execute()` code: every if/else branch, case statement, calculation
- Recipe orchestration order (`call_recipe` sequencing, conditional execution of calls)
- Event-topic pub/sub patterns as structural business rules
- Property-driven conditional behavior (`account_property`/`project_property` checks)
Parameter catalog (mandatory table): hardcoded value | location (recipe + step) | apparent purpose | staleness risk.
Conflict detection: same business concept handled differently across recipes (date formats, status mappings, null handling).

### SCHEMA_DICTIONARY
For EVERY table/object/view referenced (trigger schemas, search/get actions, `run_sql` text, create/update/upsert
input schemas, lookup tables, event-topic payload schemas):
- Fully-qualified name where available (e.g. `salesforce.Account`, `netsuite.Customer`)
- Column/field name, data type, nullable flag, key constraints if visible
- Recipes that READ it (trigger, search/get/lookup actions) vs WRITE it (create/update/upsert/delete/`run_sql`)
- Connector/provider used to access it
- Schema-drift risk: multiple recipes assuming different field sets for the same object -- flag each

### SOURCE_SQL
If no `run_sql` actions exist in this group, write exactly: `None`
Otherwise, for every `run_sql` action, in full (never truncate):
- Recipe and action containing it
- Type: SELECT / INSERT / UPDATE / DELETE / DDL
- Tables and columns referenced
- Dynamic parts: datapill substitution `#{_('data.step_alias.field')}` and property references
- WHERE-clause business filters, injection risk if datapill values are concatenated unparameterized

### EXTERNAL_SYSTEMS
For every connection: system type (database / SaaS app / REST API / file storage / FTP / message queue / SMTP),
provider, authorization method (OAuth2, API key, basic auth, token, certificate), host/base URL (mask secrets with
`***`), which recipes use it and for what purpose (read/write/lookup/notification), criticality (recipe count
depending on it), topology (source / target / bidirectional).

### ERROR_HANDLING
Rate EACH recipe: GOOD / ADEQUATE / POOR / NONE (per monitor/catch coverage of critical actions, notification
presence, whether callable-recipe calls and loop bodies are protected).
Document per recipe:
- Monitor/catch coverage: protected steps, catch behavior (notify/log/retry/stop/continue), use of `error_message`/`error_step` datapills
- Unprotected critical actions: database writes, `send_request` calls, file ops, `call_recipe` NOT inside monitor/catch
- Stop-action analysis: trigger condition, success/error status, message, data-consistency risk
- Notification coverage: which catch blocks notify (email/Slack/Teams) vs log-only vs silent
- Callable-recipe error propagation: unprotected `call_recipe` -> callee failure terminates caller
- Loop resilience: `foreach` actions without per-item monitor/catch -> one failure kills the whole loop
- Event-topic subscriber resilience: dead-letter mechanism present or absent
- Silent-failure scenarios: no monitor/catch + no notification; catch swallows error; polling trigger misses records during downtime

Workato's own job/error taxonomy to check against (cite the specific error type where evidence supports it):
- **Trigger errors**: 401 (invalid connection), 403 (insufficient permission), schema errors (upstream API schema
  change), timeout errors (transient), formula errors in trigger filter conditions
- **Run-time formula errors**: formulas that pass design-time validation but fail on real data (nil values,
  invalid operations, type mismatches)
- **Missing required fields at run-time**: input fields with datapills evaluating to no value when the job runs
- **Wrong data types**: datapill piping a string into a field expecting a number (or vice versa)
- Recall: one **job** = one trigger event's full pass through the recipe; a "job history" entry corresponds to exactly one job

### CUSTOM_CODE
If no custom adapter (Ruby SDK) components exist in this group, write exactly: `None`
For ALL custom adapters:
- Adapter name and purpose
- Connection config: Ruby code for auth settings, base URL, credentials
- Action definitions: each action's `execute()` method, full Ruby code
- Trigger definitions: polling or webhook logic
- Object definitions: schema for custom objects
- Hidden business rules embedded in Ruby (validation, calculation, categorization, routing)
- Security assessment: credential handling, input validation, SQL-injection vectors in interpolated queries, hardcoded secrets, path-traversal risk on external input passed into system calls
- Note SDK error-handling patterns in use: `detect_on` (connector-wide error interception) vs per-action `after_response` + `error()` (validates HTTP responses, raises custom errors) -- flag adapters with neither

For `run_sql` actions with complex SQL: full listing, dynamic parts, embedded business logic (CASE/WHERE/calculated columns).

### ENVIRONMENT_CONFIG
For every property (account or project level): name, scope, value pattern, which recipes reference it, whether it
varies DEV/TEST/PROD. Also: connection naming/env patterns, hardcoded environment-specific values that should be
properties, feature-flag if-blocks on property values, lookup tables used as environment config.

### SCHEDULING
- Trigger types: polling (`new_record`/`updated_record` with interval), scheduled (`scheduled_event` cron), event-driven (webhook/`new_event`/`api_trigger`), manual
- Polling intervals and cron expressions with human-readable interpretation and time zone
- Task-consumption profile: polling triggers consume tasks on every poll, even empty ones -- estimate consumption
- Polling-vs-event-driven fit: recipes polling short intervals where a webhook/event topic is available
- Recipe dependency ordering via `call_recipe` chains or event-topic sequencing
- Retry/backoff settings, recovery pattern after a failed run (auto-retry, manual re-run, gap detection)
- Concurrency and task-quota exposure: loop/task amplification, connector rate limits, idempotency for safe reruns

### NAMING_PATTERNS
- Recipe naming: domain/system prefixes, suffixes (`_sync`, `_export`, `_notify`), consistency
- Folder structure: organized by domain, system, or process
- Connection naming: environment prefix + system + purpose convention
- Event-topic naming: domain, event type, version
- Property naming: namespace prefix, environment suffix
- Step-alias (`as`) quality: meaningful names vs default auto-generated aliases
Rate: GOOD (clear, consistent, self-documenting) / ADEQUATE (mostly consistent, some outliers) / POOR (inconsistent). List outlier examples for anything below GOOD.

### CROSS_GROUP_REFS
- Cross-folder `call_recipe` references
- Shared connections used by recipes in multiple folders
- Event-topic topology: which folders publish to / subscribe from shared topics
- Shared lookup tables read or written across folders
- Shared external-system tables (one group writes, another reads)
- Execution-ordering dependencies (group A must finish before group B, via call_recipe or topic sequencing)

### RISK_FLAGS
Rate each risk CRITICAL / HIGH / MEDIUM / LOW with affected recipe(s)/step(s) and specific remediation.

Security: hardcoded credentials in action inputs or adapter code; SQL injection via unparameterized datapill
interpolation in `run_sql`; unencrypted sensitive data in event-topic payloads or lookup tables; `api_trigger`
endpoints without authentication/rate limiting; custom-adapter credential exposure.

Performance: unbounded `search_records`/`run_sql` (no filter, no pagination); `foreach` over large lists without
batch controls (task-quota exhaustion); short polling intervals with no dedup (wasted tasks); `SELECT *` without
filtering; sequential processing where event-driven parallelism is available.

Reliability: no monitor/catch on critical paths; catch blocks that swallow errors silently; missing retry on
external API calls; no dead-letter handling for event-topic subscribers; unprotected loop bodies (one failure
kills the loop); unprotected `call_recipe` (callee failure terminates caller); polling triggers with no gap
detection (missed records during downtime); missing `.present?` validation before writes.

Maintainability: complex un-documented datapill chains; duplicated logic that should be a callable sub-recipe;
magic numbers/strings in action inputs; orphaned recipes (`Copy_of_`, `_old`, `_test`, `_backup`); inconsistent
patterns across similar recipes; block nesting deeper than 5 levels.

Deep preventive analysis (mandatory for every group):
- Single points of failure: shared connections/lookup tables, single orchestrator recipes
- Cascading failure chains: trace blast radius through `call_recipe` chains, event-topic subscribers, shared target tables
- Recovery gaps: non-idempotent recipes where a re-run duplicates writes or re-fires side effects
- Monitoring gaps: detection latency if a recipe silently produces wrong output (minutes/hours/days/never)
- Task-consumption/capacity risk: polling recipes burning tasks on empty polls, `foreach` over growing lists approaching quota

WORKATO EXPORT CAVEATS (include in each section where applicable):
- This analysis is based on the JSON export, which may not reflect the live recipe state.
  If the export is more than 30 days old relative to the scan date, flag affected recipes as [EXPORT_DRIFT_RISK].
- Recipe steps that appear unused may be called dynamically via callable recipe references not visible in the
  export. Flag such steps as [POSSIBLY_DYNAMIC] rather than removing them from analysis.
- CSRF token and export authentication quirks may cause recipe parameters to be absent or masked in the export.
  Do not conclude a field is empty if only its export representation is missing.
