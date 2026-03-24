# Integration Anti-Patterns Library

Curated catalog of known anti-patterns in integration codebases. When scoring, reference by ID.

## Security Anti-Patterns (AP-SEC)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-SEC-001 | Hardcoded Credentials | Passwords, API keys, or tokens stored directly in job definitions or context variables with plain-text values | CRITICAL | Look for context variables named `*password*`, `*secret*`, `*key*`, `*token*` with non-empty default values |
| AP-SEC-002 | Unencrypted Sensitive Data | PII or financial data transmitted/stored without encryption | CRITICAL | Check tMap outputs and database writes for fields like SSN, credit_card, salary without encryption/masking |
| AP-SEC-003 | Overprivileged DB Access | Production databases accessed with admin/superuser credentials or without role-based filtering | HIGH | Look for connection strings with admin/root users, missing WHERE clauses on broad queries |
| AP-SEC-004 | No Input Validation | External data ingested without schema validation or sanitization | HIGH | Check tFileInput/tRESTClient outputs flowing directly to tMap without validation components |
| AP-SEC-005 | Credential Sharing | Same credentials used across environments (dev/staging/prod) | MEDIUM | Compare connection context variables across environment-specific context groups |
| AP-SEC-006 | Missing Audit Trail | Data modifications (UPDATE/DELETE) without logging who/when/what changed | MEDIUM | Check for DELETE/UPDATE operations without corresponding audit table writes |

## Error Handling Anti-Patterns (AP-ERR)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-ERR-001 | Silent Failure | Job fails but no error is logged, routed, or alerted | CRITICAL | Jobs without tLogCatcher, no OnComponentError, no reject flows |
| AP-ERR-002 | Swallowed Exceptions | Error caught but not logged or re-raised; processing continues silently | HIGH | tLogCatcher present but connected to tJavaRow that does nothing, or empty catch blocks in routines |
| AP-ERR-003 | No Retry Logic | Transient failures (network, DB timeout) cause permanent job failure | HIGH | tRESTClient/tDBInput without retry configuration; no parent job retry wrapper |
| AP-ERR-004 | Error Log Noise | All errors logged at same severity; no categorization; log flooding | MEDIUM | tLogRow capturing every row instead of error rows only; no severity differentiation |
| AP-ERR-005 | Incomplete Reject Handling | tMap rejects captured but not routed to dead-letter queue or error table | MEDIUM | Reject links exist but connect to tLogRow only (not persisted) |
| AP-ERR-006 | Missing Transaction Boundaries | Multi-step writes without transaction control; partial failures leave inconsistent state | HIGH | Multiple tDBOutput/tDBCommit without matching tDBRollback on error paths |

## Data Quality Anti-Patterns (AP-DQ)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-DQ-001 | No Null Handling | Null values propagated without coalesce/default; causes downstream NullPointerExceptions | HIGH | tMap expressions without null checks on nullable source columns |
| AP-DQ-002 | Implicit Type Coercion | String-to-number or date conversions without explicit format/parse; locale-dependent | MEDIUM | Implicit casts in tMap expressions; missing TalendDate.parseDate() calls |
| AP-DQ-003 | Duplicate Processing | Same records processed multiple times without deduplication | HIGH | No tUniqRow or DISTINCT/GROUP BY before key operations; missing idempotency checks |
| AP-DQ-004 | Schema Drift | No schema validation at ingestion; downstream jobs break when source schemas change | HIGH | tFileInput without explicit schema definitions; dynamic column handling without validation |
| AP-DQ-005 | Orphaned Lookups | tMap lookups that reference stale or incomplete reference data | MEDIUM | Lookup tables not refreshed before use; RELOAD option not set; inner joins that silently drop records |
| AP-DQ-006 | Truncation Without Warning | String values truncated to fit target column length without logging | MEDIUM | VARCHAR target columns shorter than source; no length-check expression in tMap |

## Performance Anti-Patterns (AP-PERF)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-PERF-001 | Row-by-Row Processing | Processing data one row at a time when batch operations are available | HIGH | tDBInput in loops; tJavaRow with individual DB calls; no batch commit settings |
| AP-PERF-002 | Unbounded Queries | SELECT without WHERE clause or LIMIT on large tables; full table scans | HIGH | tDBInput queries without WHERE on million-row tables; no partition pruning |
| AP-PERF-003 | Nested tMap Complexity | Deeply nested tMap lookups (3+ levels) creating O(n^m) processing | MEDIUM | tMap with 3+ lookup inputs; each lookup doing sub-queries; high join fan-out |
| AP-PERF-004 | Memory-Intensive Lookups | Loading entire reference tables into memory via tMap lookup (LOAD_ONCE) on large datasets | MEDIUM | tMap lookups on tables >1M rows with LOAD_ONCE; no partitioning or filtering |
| AP-PERF-005 | Sequential Bottleneck | Jobs that must run sequentially when parallel execution is possible | MEDIUM | Independent jobs chained in sequence via tRunJob; no parallelism configured |
| AP-PERF-006 | Missing Index Hints | Database operations on large tables without index awareness | LOW | Queries filtering on non-indexed columns; bulk inserts without disabling indexes |

## Architecture Anti-Patterns (AP-ARCH)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-ARCH-001 | God Job | Single job handling too many responsibilities (>20 components) | HIGH | Jobs with 20+ components; multiple unrelated data flows in one job |
| AP-ARCH-002 | Circular Dependencies | Jobs that directly or indirectly depend on each other, creating deadlock risk | CRITICAL | tRunJob chains that form cycles; mutual file dependencies |
| AP-ARCH-003 | Copy-Paste Jobs | Duplicate job logic across regions/modules instead of parameterized shared jobs | MEDIUM | Jobs with identical structure but different connection params; no use of context variables for parameterization |
| AP-ARCH-004 | Tight Coupling | Jobs directly accessing other jobs' intermediate files or temp tables | HIGH | tFileInput reading another job's tFileOutput path; shared temp tables without clear ownership |
| AP-ARCH-005 | Missing Abstraction Layer | Direct database/API calls scattered across jobs instead of centralized connection management | MEDIUM | Same connection strings duplicated in multiple jobs; no shared context groups for connections |
| AP-ARCH-006 | Inconsistent Naming | No naming convention or mixed conventions across teams/regions | MEDIUM | Mixed prefixes, no region tags, inconsistent case, no job-type indicators |

## Operational Anti-Patterns (AP-OPS)

| ID | Name | Description | Severity | Detection |
|----|------|-------------|----------|-----------|
| AP-OPS-001 | Manual Triggers Only | No scheduling metadata; jobs can only be triggered manually | HIGH | No cron/scheduler references; no tPrejob or scheduling context variables |
| AP-OPS-002 | No Monitoring | No logging, alerting, or health check mechanism | HIGH | No tLogRow/tLogCatcher in any job; no external monitoring integration |
| AP-OPS-003 | Environment Hardcoding | Environment-specific values (URLs, paths, credentials) hardcoded instead of externalized | HIGH | Hardcoded IP addresses, file paths, or URLs in component parameters |
| AP-OPS-004 | Missing Recovery Procedure | No mechanism to restart from failure point; full re-run required | MEDIUM | No checkpoint/bookmark logic; no incremental processing markers |
| AP-OPS-005 | Log Overload | Every row logged in production; log files grow unbounded | LOW | tLogRow on main data flow (not just error path); no log rotation |
