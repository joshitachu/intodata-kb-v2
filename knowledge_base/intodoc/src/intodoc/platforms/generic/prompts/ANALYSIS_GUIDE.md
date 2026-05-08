# Generic Codebase Analysis Guide

This guide is used by Phase 2 sub-agents analysing an arbitrary codebase (Java, Python,
TypeScript/JS, C#, SQL, or any mix). It replaces the shared platform-agnostic guide.

Write markdown, not JSON. Be exhaustive and deeply analytical. Every claim must cite a
specific file name and function/class/method reference.

**Depth target**: 500-2000 lines of analysis per group. This is a deep code review, not a
metadata extraction exercise. Explain what the code *actually does*, not just what exists.

---

## Depth Contract

Apply this evidence standard to every section:
- Cite every claim with the exact file path, function/class/method, approximate line number if available, and artifact/reference where it was found (source file, config, SQL, test, package manifest, deployment file).
- Enumerate concrete evidence before summarizing. List actual routes, functions, imports, config keys, SQL, models, queues, jobs, and tests first; then infer architecture, risk, or intent.
- Quote formulas, SQL, code, conditions, validators, serializers, mappings, route paths, config keys, constants, and error branches verbatim where present.
- Label scanner-limited evidence explicitly, for example: "line numbers unavailable", "test fixtures not included", "deployment config not present", or "schema inferred from usage only".
- For important findings include impact, confidence (HIGH/MEDIUM/LOW), risk, and affected units (files, functions, routes, models, schemas, queues, config keys, tests, external systems).

## Output Quality Gate

Your output is valid only if every canonical section contains cited evidence before interpretation:
- Start each section with concrete file/function/route/model/config/test evidence. Summaries, architecture conclusions, and recommendations must come after the evidence.
- Empty sections are allowed only after stating what was checked and whether the absence is confirmed or scanner-limited.
- Any broad claim such as "has an API", "uses PostgreSQL", "validates orders", or "has tests" must cite the exact file path, function/class/method, line number if available, and source artifact.
- Important findings must include affected unit, source reference, impact, confidence, risk, and scanner limitation if applicable.
- Do not collapse multiple files or modules into one generic paragraph. If several files share a pattern, list each file/function first and then summarize the pattern.

## Anti-Shallow Output Rule

The following output patterns are invalid:
- Architecture prose without entrypoints, modules/services, routes/APIs, scripts/jobs, dependency boundaries, and external systems listed first.
- Data-flow summaries without source-to-sink traces and field/config/model/queue payload evidence.
- Business-rule summaries without quoting the condition, validator, serializer, SQL predicate, constant, authorization check, or code implementing the rule.
- Debugging/testing claims without tests, logs, metrics, health checks, retry/dead-letter, runtime command, or scanner-limited evidence.
- Change-impact guidance without affected files/functions/routes/models/config keys/jobs/tests, required checks, rollback risk, and confidence.

## Required Evidence Tables

Include these tables when evidence exists; if evidence is absent, state the checked source and limitation:
- **Entrypoint/module table**: file, inferred job type, route/command/scheduler/worker entrypoint, primary functions/classes, dependencies, external systems, tests.
- **Source-to-sink data-flow table**: source, validation/auth step, transformation expression, sink, schema/model/payload, config keys, failure path.
- **Business-rule table**: rule ID, quoted expression/code, file/function/line, domain, impact, blast radius, duplicate/conflict evidence.
- **Config/external-system table**: config key/env var/connection, file/function using it, default/value pattern, external system, auth/secret risk, environment/rollback risk.
- **Test/debug table**: behavior or unit, visible tests/fixtures, logs/metrics/health checks, error observability, replay/idempotency evidence, missing coverage.
- **Change-impact table**: change target, affected files/functions/routes/models/jobs/tests/config, required validation checks, rollback risk, confidence, scanner limitations.

---

## How to Approach This Analysis

You are acting as an expert code reviewer and integration architect reading this codebase for
the first time. Your goal is to produce documentation at the same depth and quality as a
senior engineer explaining the system to a new team member.

**Read every function body.** Do not list function names and move on. For each significant
function, explain:
- What business problem it solves
- What data it consumes and produces
- What rules, conditions, or calculations are embedded in it
- What could go wrong

**Explain, don't enumerate.** Bad: "There is a `validateOrder` function." Good: "`validateOrder`
enforces three rules before an order can be placed: (1) `amount > 0` (line 42), (2) the SKU
must exist in the product catalogue -- checked via a DB query on every call, which is a
performance risk for high-throughput endpoints, (3) the customer account must not have status
`'SUSPENDED'` -- this string is hardcoded at line 51 with no named constant."

**Trace every data flow end-to-end.** Follow data from where it enters (HTTP request, DB row,
file, queue message) through every transformation to where it exits (DB write, HTTP response,
file write, queue publish, return value).

**Quote business rules verbatim.** Show the actual expression from the source code, not a
paraphrase. Use code fences.

**Never declare a section empty without exhaustive search.** If CONNECTIONS appears to have
nothing, re-read every config file and look for connection strings in code before writing
"none found."

---

## Platform Reference

### What Counts as a "Job"

In this codebase there is no explicit job definition file. Use these heuristics to identify
logical units of work -- treat each as a separate "job" in your analysis:

**Python**
- File containing `if __name__ == "__main__":` -- standalone script
- Module with route decorators: `@app.route`, `@router.get/post/put/delete`, `urlpatterns =`
- Celery task module: `@celery.task`, `@shared_task`, `@app.task`
- Django management command: `class.*Command(BaseCommand):`
- Any file that performs a complete, self-contained unit of work when executed

**Java / Kotlin**
- Class with `public static void main(`
- `@SpringBootApplication` entry point
- `@RestController` or `@Controller` class -- each controller is one job
- `@Service` class with `@Scheduled` methods -- each scheduled service is one job
- Class `implements Job` (Quartz) or extends `AbstractBatchlet` / step listener (Spring Batch)

**TypeScript / JavaScript**
- Next.js route file: `app/api/**/route.ts`, `pages/api/**/*.ts`
- Express/Fastify/Hapi router file (imports `Router` or `express()`)
- NestJS `@Controller` class
- React page-level component (exported default from a `pages/` or `app/` directory)
- Standalone Node.js script with a main execution block

**C# / .NET**
- Class with `[ApiController]` attribute -- each controller is one job
- Class extending `Controller` or `ControllerBase`
- Class extending `BackgroundService` or implementing `IHostedService`
- `Program.cs` or `Startup.cs` -- the application entry point
- Worker service classes

**SQL**
- Each `.sql` file containing a stored procedure, function, or trigger definition
- Each standalone DML batch script

**Fallback**
- If none of the above match, treat each second-level directory as a job boundary

### Detecting Connections and External Systems

There is no structured connection registry. Infer connections from:

**Database connection strings in source code or config:**
```
jdbc:(mysql|postgresql|sqlserver|oracle)://host:port/database
psycopg2.connect(host=..., database=...)
create_engine("postgresql://...")
DATABASES = {"default": {"HOST": ..., "NAME": ...}}        # Django
DATABASE_URL=postgres://...                                 # .env
new Pool({host: ..., database: ...})                        # Node.js
mongoose.connect("mongodb://...")
new SqlConnection(connectionString)                         # C#
UseSqlServer(...) / UseNpgsql(...)                          # EF Core
<connectionStrings><add connectionString="..."/>            # Web.config
spring.datasource.url=jdbc:...                              # application.properties
```

**REST / HTTP clients:**
```
axios.create({baseURL: "https://..."})
new HttpClient { BaseAddress = new Uri("https://...") }    # C#
requests.Session() + session.headers / base_url            # Python
RestClient("https://...")                                   # RestSharp
fetch("https://...")                                        # JS/TS
```

**Infer external system from import / using / package name:**

| Import / Package | External System |
|-----------------|----------------|
| `boto3`, `com.amazonaws`, `AWSSDK.*` | AWS |
| `google.cloud.*`, `com.google.cloud` | Google Cloud Platform |
| `azure.*`, `Microsoft.Azure.*` | Microsoft Azure |
| `redis`, `ioredis`, `StackExchange.Redis` | Redis |
| `pymongo`, `mongoose`, `MongoDB.Driver` | MongoDB |
| `kafka-python`, `org.apache.kafka`, `Confluent.Kafka` | Apache Kafka |
| `elasticsearch`, `org.elasticsearch` | Elasticsearch |
| `psycopg2`, `asyncpg`, `Npgsql`, `org.postgresql` | PostgreSQL |
| `pymssql`, `com.microsoft.sqlserver`, `System.Data.SqlClient` | SQL Server |
| `cx_Oracle`, `oracle.jdbc` | Oracle Database |
| `requests`, `httpx`, `axios`, `node-fetch`, `System.Net.Http` | External HTTP API |
| `pika`, `aio_pika`, `com.rabbitmq` | RabbitMQ |
| `celery` | Celery task queue |
| `sendgrid`, `mailchimp_marketing` | Email delivery API |
| `stripe`, `paypalrestsdk` | Payment processing API |
| `twilio` | SMS / Voice API |
| `ldap3`, `Microsoft.Identity`, `ActiveDirectory` | LDAP / Active Directory |
| `Microsoft.ApplicationInsights`, `opentelemetry` | Application monitoring |

### Error Handling Patterns by Language

**Python -- silent failure indicators:**
- `except:` with no re-raise and no specific exception type
- `except Exception as e: logger.error(e)` without `raise` = swallowed
- Missing `except` on external I/O (DB queries, HTTP calls, file reads)
- `@retry` / `tenacity.retry` = retry logic present (document the policy)

**Java -- silent failure indicators:**
- `catch (Exception e) { log.error(...); }` without re-throw = swallowed
- Checked exception declared in signature but caller ignores it
- `@Retryable(maxAttempts=N, backoff=@Backoff(...))` = retry logic present

**TypeScript / JavaScript -- silent failure indicators:**
- `.catch(err => console.error(err))` without re-throw = swallowed
- `async` function with no try/catch and no `.catch()` chain = unhandled rejection
- Empty `catch {}` block = completely silenced

**C# -- silent failure indicators:**
- `catch (Exception ex) { _logger.LogError(ex, "..."); }` without re-throw = swallowed
- `BackgroundService` with no try/catch in `ExecuteAsync` = crash on first error
- Polly policy present = retry/circuit-breaker logic (document configuration)

### Scheduling Patterns

**Python**: `@app.on_event("startup")`, APScheduler `scheduler.add_job(func, "cron", ...)`,
Celery beat `beat_schedule = {...}`, crontab comment in file header

**Java / Spring**: `@Scheduled(cron="0 0 * * * *")`, `@Scheduled(fixedRate=60000)`,
`@EnableScheduling` on configuration class, Quartz `CronTrigger`

**TypeScript / Node.js**: `cron.schedule("*/5 * * * *", callback)`,
Bull queue `queue.add({}, {repeat: {cron: "..."}})`, NestJS `@Cron("...")`

**C#**: Hangfire `RecurringJob.AddOrUpdate("jobId", () => ..., Cron.Daily())`,
Quartz.NET `ITrigger trigger = TriggerBuilder.Create().WithCronSchedule("...").Build()`,
`IHostedService` with `new Timer(callback, null, TimeSpan.Zero, TimeSpan.FromMinutes(5))`

---

## Section Analysis Instructions

Analyse ALL jobs/files in your chunk. Every claim must cite a specific file and
function/class/method. Each section should have substantive content -- do not skip.

### COMPONENTS

For each job (file or class) in the chunk:

1. **Identity**: file path, inferred job type (from the heuristics above), one-sentence purpose
2. **Entrypoints and boundaries**: API route, controller, service method, script main, CLI command, scheduled job, queue consumer/producer, background worker, frontend page/action, migration, or library module boundary
3. **Functions / Methods**: for each significant function or method:
   - Signature (name, parameters, return type)
   - What it actually does -- explain the business logic, not just the name
   - What data it reads (parameters, DB queries, config values, external calls)
   - What data it produces (return value, DB writes, HTTP responses, side effects)
   - Decorators / annotations and their meaning
4. **SQL queries**: full text of every SQL query embedded in the file
5. **External calls**: every HTTP request, queue message, file I/O, cloud SDK call, email/payment/SMS/search/cache operation (URL/endpoint/topic/bucket/path, method, purpose)
6. **Configuration used**: every environment variable, config key, feature flag, constant, command-line argument, or deployment setting the file reads
7. **Dependencies**: which other files/modules this file imports (internal and external), plus dependency-boundary notes (controller -> service -> repository, job -> queue, script -> DB)
8. **Tests**: test files or fixtures that exercise this job/function if visible, including missing tests for important behavior
9. **Orphaned code**: functions defined but apparently never called

### EXECUTION_FLOW

For each job:
- Entry point (the function/method called first, or the route handler triggered)
- Step-by-step call sequence through the job's main path
- Conditional branches that affect execution (with the condition expression quoted)
- Loop patterns (what is iterated, when it terminates)
- Async / concurrent patterns (threads, async/await, task queues)
- Error exit paths (where exceptions terminate or redirect flow)
- Job-to-job orchestration: which jobs call or trigger other jobs (via import + call, HTTP, queue)
- Dependency-boundary crossings: route/controller -> service -> repository/model -> external API/DB/queue/cache/file
- Scheduler/worker orchestration: cron/timer, queue trigger, event listener, background worker startup, retry/dead-letter behavior
- Dead code: functions defined but unreachable

### DATA_FLOWS

For each job, trace every data flow from source to sink:

**Source** (where data enters): HTTP request body/params, DB query result, file read, queue
message, function parameter, environment variable, config file, feature flag, hardcoded constant, scheduler payload

**Transformations** (what happens to it): mapping, filtering, aggregation, type conversion,
enrichment, validation, authorization, serialization/deserialization, calculation -- describe each step and quote the expression

**Sink** (where data exits): HTTP response, DB write, file write, queue publish, return value,
in-memory state, cache write, external API call, log/metric, email/SMS/payment/search/cloud storage operation

For each flow:
- Which fields are passed through unchanged
- Which fields are transformed (quote the transformation expression)
- Which fields are added (source: constant, calculated, or external lookup)
- Which fields are dropped / filtered
- Null handling and default value patterns
- End-to-end trace: request/file/queue/DB/config -> validation/auth -> service/domain logic -> persistence/external call/response
- Boundary contracts: DTO/schema/model names, serializer/parser behavior, queue/topic payload shape, DB model/table fields, and config keys that alter the flow

### TRANSFORMATION_DETAIL

For EVERY function that maps, transforms, or calculates data, produce:

1. **Field mapping table**:

   | Source field | Expression / Transform | Target field | Explanation |
   |-------------|----------------------|-------------|-------------|
   | `request.body.email` | `.toLowerCase().trim()` | `user.email` | Normalise on ingest |
   | `row["amount"]` | `* TAX_RATE` (0.21) | `invoice.tax` | Apply fixed 21% VAT |
   | (none) | `"PENDING"` | `order.status` | Hardcoded initial status |

2. **Join / lookup analysis** (for functions correlating multiple data sources):
   - Primary input
   - Secondary / lookup input (source, how fetched)
   - Join key
   - Behaviour on no-match (null, default value, exception, skip row)

3. **Filter / routing analysis** (for functions that split or discard data):
   - Filter condition (full expression)
   - Path when condition is true
   - Path when condition is false / what happens to excluded records

4. **Complex expression traces** (3+ chained operations):
   - Step-by-step from source value to final output
   - All intermediate variables

5. **Validation / serialization analysis**:
   - Validator, serializer, parser, schema, form, or DTO used
   - Required/optional fields and defaults
   - Authorization checks or role gates applied before transformation
   - Error response or exception raised on invalid input

6. **Configuration-driven transformation**:
   - Config keys, feature flags, constants, or environment variables that change mappings, filters, destinations, or business behavior
   - Default behavior when the config key is missing

### BUSINESS_RULES

Extract every business rule visible in code. A business rule is any conditional, calculation,
validation, or constant that encodes domain knowledge.

For each rule:
- **BR-{GRP}-NNN**: sequential ID
- **Expression**: the actual code, verbatim, in a code fence
- **Location**: file + function/method + approximate line number
- **Domain**: inferred business area (e.g. pricing, auth, inventory, routing, compliance)
- **Impact**: what breaks or changes if this rule is removed or altered

Look specifically for:
- Conditionals with domain-meaningful values (`if status == "APPROVED"`, `if amount > threshold`)
- Hardcoded business constants (tax rates, status strings, category codes, magic numbers)
- Calculation expressions (fee calculation, discount logic, conversion rates, SLA deadlines)
- Validation rules (required fields, format checks, range assertions)
- Date/time logic (cutoff windows, grace periods, scheduling rules)
- Security rules (role checks, permission assertions, rate limiting thresholds)
- Serializer/model/schema constraints and database constraints
- Authorization and ownership checks
- Feature flags/config branches
- SQL WHERE/JOIN/CASE/HAVING predicates
- Orchestration and error-path rules (retry, rollback, compensating action, dead-letter routing)

**Extraction sub-categories** -- apply each systematically:

1. **Domain inference**: From file paths, route names, model names, table names, config keys, and external systems, infer the business domain and confidence for each rule.
2. **Validator/serializer/model rules**: Required fields, type constraints, enum values, default values, normalized fields, and schema validations are business rules. Quote the code or schema definition.
3. **Authorization/routing rules**: Role checks, ownership checks, tenant filters, feature-flag branches, queue/topic routing, and error response selection encode policy.
4. **SQL/data rules**: WHERE/JOIN/CASE/HAVING clauses, ORM filters, migrations, constraints, and indexes can encode eligibility, deduplication, ordering, and uniqueness rules.
5. **Error-path rules**: Retry limits, fallback defaults, rollback/compensation, swallowed exceptions, and dead-letter paths determine business outcomes under failure.
6. **Parameter catalog**: List every hardcoded status, date, threshold, timeout, rate limit, currency/tax value, category code, role name, queue/topic name, and external URL with location, apparent purpose, and staleness risk.
7. **Impact analysis per rule**: Rate blast radius as ISOLATED (single function/output), MODERATE (module/API/job/schema affected), or WIDE (multiple services/external consumers/data stores).
8. **Conflict detection**: Find inconsistent validation, status mappings, date/time handling, null/default handling, authorization filters, or schema assumptions across files.

### SCHEMA_DICTIONARY

For every table, collection, view, API endpoint schema, ORM model, or data structure:
- Name (fully qualified: `dbo.Orders`, `/api/v1/invoices`, `OrderModel`)
- Fields / columns: name, type, nullable (from ORM model, type hints, SQL DDL, or inference)
- Which jobs READ vs WRITE vs CREATE this structure
- Connection or external system used to access it

### SOURCE_SQL

Every SQL query in full -- never truncate:
- File and function/method where it appears
- Query type: SELECT, INSERT, UPDATE, DELETE, DDL, stored procedure call
- Tables and columns referenced
- Parameterised placeholders (`:param`, `%s`, `@param`, `{name}`)
- Dynamic parts (string concatenation, f-strings into SQL -- flag as injection risk)

### EXTERNAL_SYSTEMS

All external systems detected:
- System type and product name (e.g. "PostgreSQL", "Azure Blob Storage", "Stripe API")
- Detection evidence (import statement, connection string, config key -- quote it)
- Connection details where visible (host pattern, port, database name -- do not include credentials)
- Which jobs interact with it
- Direction: read-only, write-only, or read/write
- Auth method if visible (API key, OAuth2, basic auth, managed identity)

### ERROR_HANDLING

Rate each job: **GOOD / ADEQUATE / POOR / NONE**

- **GOOD**: errors caught appropriately, failures logged and re-raised or handled, retries where transient
- **ADEQUATE**: most errors handled; some paths may swallow failures
- **POOR**: catch blocks swallow exceptions silently; limited coverage; missing try/catch on external calls
- **NONE**: no error handling visible

For each job include:
- Every try/catch/except block and what it handles (or swallows)
- Silent failures: caught, not re-raised, not surfaced to caller
- Retry logic: decorator, policy, or manual retry loop (document the policy)
- Rollback or compensating actions on failure
- Whether background/async failures are observable
- User-facing/API error responses and status codes
- Queue dead-letter, retry, ack/nack, visibility timeout, or poison-message behavior
- Test coverage for error paths if visible

### CUSTOM_CODE

All non-trivial custom logic (more than getters/setters/simple CRUD):
- Full function body in a code fence -- never truncate
- Exact business logic and rules it encodes
- Hidden business rules not obvious from the function name
- External dependencies introduced
- Risk factors: injection vectors, unvalidated input, hardcoded secrets, thread-safety issues,
  resource leaks (unclosed connections/files)

### ENVIRONMENT_CONFIG

Every configuration or environment variable:
- Name, inferred type, default value (if any visible)
- Which jobs consume it
- Environment-specific differences if detectable (different values for dev/staging/prod)
- Flag any variable that appears to hold a credential even if already redacted
- Feature flags (booleans that enable/disable behaviour)

### SCHEDULING

For each job that runs on a schedule or is triggered asynchronously:
- Trigger type: HTTP, cron/timer, queue consumer, event-driven, startup, manual
- Schedule expression (cron string, interval, delay) -- quote verbatim
- Dependencies on other jobs (must finish before this one starts)
- Retry configuration
- Idempotency assessment: can the job be safely re-run if it fails mid-way?
- Queue/topic/worker binding, concurrency settings, locks, and duplicate-message handling where visible

### NAMING_PATTERNS

- File and directory naming conventions observed
- Function / method naming conventions (camelCase, snake_case, PascalCase, mixed)
- Variable naming conventions
- Consistency assessment: **GOOD / ADEQUATE / POOR**
- Mixed conventions or patterns that suggest multiple origins or legacy code

### CROSS_GROUP_REFS

- Shared utility modules imported by jobs across multiple groups
- Shared data stores written by one group and read by another
- Shared configuration or environment variables used across groups
- Cross-group HTTP calls (one service calling another internal service)
- Shared external system connections used by multiple groups

### RISK_FLAGS

Severity: **CRITICAL / HIGH / MEDIUM / LOW**

For each risk: severity, affected files and functions, description, recommended remediation.

**Security**
- Hardcoded credentials, API keys, or secrets in source code
- SQL or command injection vectors (string concatenation into queries/shell commands)
- Unvalidated user input passed to DB, filesystem, or shell
- Insecure HTTP (non-HTTPS external calls)
- JWT secrets or signing keys in source code
- Overly broad CORS or authentication bypass patterns

**Data Integrity**
- Multi-step writes without a transaction wrapper
- Queue consumers without idempotency (duplicate message processing risk)
- Partial-write risk: failure mid-way leaves data in inconsistent state
- No unique constraint enforcement at application level

**Performance**
- Full-table scans with no WHERE clause or LIMIT
- N+1 query patterns (query inside a loop)
- Synchronous blocking I/O in async/non-blocking contexts
- Large in-memory data structures (loading entire table into memory)
- No pagination on list endpoints

**Reliability**
- Silent exception swallowing (catch block that doesn't re-raise or alert)
- No try/catch around external API or DB calls
- No timeout configured on HTTP clients or DB connections
- Background jobs that crash silently with no monitoring
- No retry logic on transient failures (network errors, rate limits)

**Maintainability**
- Deeply nested conditionals (4+ levels of indentation)
- Duplicated business logic across multiple files
- Magic numbers without named constants
- Functions exceeding ~150 lines
- Commented-out code blocks
- TODO/FIXME comments indicating known technical debt

**Observability**
- No structured logging (only print statements or no logging at all)
- No request/correlation ID threading through log statements
- No metrics or health endpoint
- Background job failures not surfaced in any observable way

**Dependencies**
- Patterns suggesting deprecated library versions
- Direct dependency on internal service URLs hardcoded as strings
- Missing null/undefined checks on external API responses

### DEBUG_TESTING_EVIDENCE

Extract assistant-grade test, debugging, and operations evidence:
- Test inventory: unit/integration/e2e files, fixtures, factories, snapshots, mocks, contract tests, migration tests, and which functions/routes/jobs they cover.
- Missing test gaps: important business rules, serializers/validators, error paths, queue consumers, scheduled jobs, migrations, config branches, and external integrations without visible tests.
- Debugging hooks: logging statements, structured fields, correlation/request IDs, metrics, health checks, tracing, audit logs, admin/debug endpoints, and command-line dry-run flags.
- Error observability: exception propagation, logged context, user-facing status codes, background worker failure visibility, retry/dead-letter evidence, and alerting hooks.
- Runtime evidence: scripts, Makefile/package commands, Docker/compose/Kubernetes/deployment files, CI workflows, migration commands, and local run/test commands.
- Idempotency and replay evidence: unique keys, upsert patterns, dedupe tables, queue ack semantics, transaction boundaries, locks, checkpoint files, and safe rerun behavior.
- Scanner limitations: tests/config/deployment files missing from chunk, generated code not included, line numbers unavailable, or schemas inferred only from usage.

### CHANGE_IMPACT_EXTRACTION

For each important change target, document affected units, required checks, confidence, and rollback risk:
- Function/module change: callers, imports, public API, side effects, tests, config keys, exceptions, and downstream sinks.
- Schema/model/database change: migrations, ORM models, serializers/DTOs, SQL queries, indexes/constraints, seed data, backward compatibility, and rollback migration risk.
- Route/API change: route path/method, request/response schema, auth/authorization, clients, tests, documentation, status codes, and versioning risk.
- Config/feature-flag change: all read sites, defaults, environment differences, deployment files, secrets, rollback behavior, and startup failure risk.
- External system change: client wrapper, credentials, base URL/topic/bucket/table, timeout/retry policy, mocks/tests, and fallback behavior.
- Queue/job/scheduler change: producer/consumer contracts, payload schema, idempotency, retry/dead-letter, concurrency/locking, and replay/rollback plan.
- Business-rule change: validators, constants, SQL predicates, authorization checks, error paths, tests, duplicate implementations, and affected reports/data stores.
- Test-only change: production behavior assumptions encoded in tests/fixtures, brittle snapshots, missing integration coverage, and confidence impact.
- Deployment/rollback change: migrations, environment variables, startup order, background workers, health checks, and operational runbook steps.

### ASSISTANT_DOC_OBLIGATIONS

Phase 2 must produce enough evidence for the assistant-grade docs:
- Architecture docs need entrypoints, modules/services, routes/APIs, scripts, schedulers, workers/queues, models/schemas, dependency boundaries, and external systems.
- Lineage docs need end-to-end traces from request/file/queue/DB/config through validation, transformations, persistence, external calls, and responses.
- Business-rule docs need validators, serializers, constants, authorization checks, SQL/ORM filters, orchestration rules, and error-path behavior with impact/confidence.
- Runtime/config docs need config keys, env vars, deployment files, commands, queues/jobs/schedules, retries/timeouts, migrations, and external connection evidence.
- Debugging/runbook docs need tests, logs, metrics, health checks, error handling, replay/idempotency guidance, and manual recovery hazards.
- Risk/change docs need affected files/functions/routes/models/jobs/tests, blast radius, rollback risk, required validation checks, scanner limitations, and confidence for each major finding.

### SELF_CHECK_BEFORE_RETURNING

Before returning the Phase 2 analysis, verify:
- All canonical sections are present: COMPONENTS, EXECUTION_FLOW, DATA_FLOWS, TRANSFORMATION_DETAIL, BUSINESS_RULES, SCHEMA_DICTIONARY, SOURCE_SQL, EXTERNAL_SYSTEMS, ERROR_HANDLING, CUSTOM_CODE, ENVIRONMENT_CONFIG, SCHEDULING, NAMING_PATTERNS, CROSS_GROUP_REFS, RISK_FLAGS, DEBUG_TESTING_EVIDENCE, CHANGE_IMPACT_EXTRACTION, ASSISTANT_DOC_OBLIGATIONS.
- Every major claim cites file path, function/class/method or config/test artifact, and line number if available.
- Every important condition, validator, serializer, SQL query, constant, config key, route path, queue/topic, error branch, or code block is quoted verbatim where present.
- Scanner-limited evidence is labelled rather than hidden or guessed.
- The analysis contains enough evidence for all 9 assistant-grade docs, especially operations/debugging and change playbook material.
