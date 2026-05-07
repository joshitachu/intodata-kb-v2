# Generic Codebase Analysis Guide

This guide is used by Phase 2 sub-agents analysing an arbitrary codebase (Java, Python,
TypeScript/JS, C#, SQL, or any mix). It replaces the shared platform-agnostic guide.

Write markdown, not JSON. Be exhaustive and deeply analytical. Every claim must cite a
specific file name and function/class/method reference.

**Depth target**: 500-2000 lines of analysis per group. This is a deep code review, not a
metadata extraction exercise. Explain what the code *actually does*, not just what exists.

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
2. **Functions / Methods**: for each significant function or method:
   - Signature (name, parameters, return type)
   - What it actually does -- explain the business logic, not just the name
   - What data it reads (parameters, DB queries, config values, external calls)
   - What data it produces (return value, DB writes, HTTP responses, side effects)
   - Decorators / annotations and their meaning
3. **SQL queries**: full text of every SQL query embedded in the file
4. **External calls**: every HTTP request, queue message, file I/O (URL/endpoint, method, purpose)
5. **Configuration used**: every environment variable, config key, or constant the file reads
6. **Dependencies**: which other files/modules this file imports (internal and external)
7. **Orphaned code**: functions defined but apparently never called

### EXECUTION_FLOW

For each job:
- Entry point (the function/method called first, or the route handler triggered)
- Step-by-step call sequence through the job's main path
- Conditional branches that affect execution (with the condition expression quoted)
- Loop patterns (what is iterated, when it terminates)
- Async / concurrent patterns (threads, async/await, task queues)
- Error exit paths (where exceptions terminate or redirect flow)
- Job-to-job orchestration: which jobs call or trigger other jobs (via import + call, HTTP, queue)
- Dead code: functions defined but unreachable

### DATA_FLOWS

For each job, trace every data flow from source to sink:

**Source** (where data enters): HTTP request body/params, DB query result, file read, queue
message, function parameter, environment variable, hardcoded constant

**Transformations** (what happens to it): mapping, filtering, aggregation, type conversion,
enrichment, calculation -- describe each step and quote the expression

**Sink** (where data exits): HTTP response, DB write, file write, queue publish, return value,
in-memory state

For each flow:
- Which fields are passed through unchanged
- Which fields are transformed (quote the transformation expression)
- Which fields are added (source: constant, calculated, or external lookup)
- Which fields are dropped / filtered
- Null handling and default value patterns

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
