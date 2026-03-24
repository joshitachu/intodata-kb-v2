# Red Flags Library

Issues that always indicate problems requiring attention. Reference by ID in the scorecard.

## Security Red Flags (RF-SEC)

| ID | Severity | Name | Description | Impact |
|----|----------|------|-------------|--------|
| RF-SEC-001 | CRITICAL | Hardcoded Password | Plain-text password in context variable default value or component parameter | Credential exposure in source control; any developer/operator can see production credentials |
| RF-SEC-002 | CRITICAL | Unguarded DELETE | DELETE operation on production table without WHERE clause guard or confirmation logic | Risk of full table deletion; no safety net for operational errors |
| RF-SEC-003 | CRITICAL | PII in Logs | Personal identifiable information (SSN, email, phone, DOB) written to log files via tLogRow | Compliance violation (GDPR/CCPA); PII exposure in log aggregation systems |
| RF-SEC-004 | HIGH | SQL Injection Vector | Dynamic SQL built by concatenating user input or external file data without parameterization | Data breach risk; unauthorized data access or modification |
| RF-SEC-005 | HIGH | Unencrypted File Transfer | Sensitive data transferred via FTP (not SFTP/FTPS) or HTTP (not HTTPS) | Data interception risk; compliance violation |
| RF-SEC-006 | HIGH | Shared Service Account | Same database credentials used across all jobs; no per-job or per-team service accounts | No attribution for data changes; blast radius of credential compromise is total |
| RF-SEC-007 | MEDIUM | Missing Access Logging | Database modifications without audit trail (no who/when/what tracking) | Cannot investigate data issues; compliance gaps |

## Performance Red Flags (RF-PERF)

| ID | Severity | Name | Description | Impact |
|----|----------|------|-------------|--------|
| RF-PERF-001 | HIGH | Full Table Scan | SELECT * without WHERE on tables known to exceed 1M rows | Long-running queries; database lock contention; memory pressure |
| RF-PERF-002 | HIGH | Unbounded Result Set | Query returns all rows into memory without LIMIT or streaming | OutOfMemoryError risk; JVM crashes in production |
| RF-PERF-003 | MEDIUM | Cartesian Join Risk | tMap with multiple lookups that can produce cross-product on non-unique keys | Exponential row explosion; memory exhaustion; processing time explosion |
| RF-PERF-004 | MEDIUM | Missing Batch Commit | tDBOutput without commit interval; commits every row | 10-100x slower than batch commits; excessive transaction log growth |
| RF-PERF-005 | MEDIUM | Synchronous API Calls in Loop | tRESTClient inside tFlowToIterate loop without parallelism or batching | Linear scaling; N API calls for N records; no timeout handling |
| RF-PERF-006 | LOW | Unnecessary Sort | tSortRow on already-sorted data or data where order doesn't matter | Wasted CPU and memory; slows pipeline without benefit |

## Data Integrity Red Flags (RF-DATA)

| ID | Severity | Name | Description | Impact |
|----|----------|------|-------------|--------|
| RF-DATA-001 | CRITICAL | Write Without Transaction | Multi-table writes without transaction boundary; partial success possible | Inconsistent data state; orphaned records; broken referential integrity |
| RF-DATA-002 | HIGH | Lost Rejects | tMap reject output not connected or connected to /dev/null equivalent | Bad data silently discarded; no visibility into data quality issues |
| RF-DATA-003 | HIGH | Truncation Risk | Source string columns mapped to smaller target columns without length check | Silent data truncation; corrupted business data |
| RF-DATA-004 | HIGH | Date Format Ambiguity | Date parsing without explicit format string; relies on system locale | Different results across environments; data corruption on server migration |
| RF-DATA-005 | MEDIUM | Missing Idempotency | Insert-only jobs without duplicate detection on natural keys | Duplicate records on re-run; inflated metrics; broken aggregations |
| RF-DATA-006 | MEDIUM | Stale Reference Data | Lookup tables loaded once at job start; not refreshed for long-running jobs | Decisions based on outdated reference data; incorrect classifications |

## Architecture Red Flags (RF-ARCH)

| ID | Severity | Name | Description | Impact |
|----|----------|------|-------------|--------|
| RF-ARCH-001 | HIGH | Mega Job (30+ components) | Single job with 30+ components handling multiple business processes | Unmaintainable; testing impossible; single point of failure; memory pressure |
| RF-ARCH-002 | HIGH | Undocumented Custom Code | Java routines or custom code blocks without comments, without clear input/output contracts | Migration blocker; maintenance risk; single-person dependency |
| RF-ARCH-003 | MEDIUM | Dead Code | Jobs, components, or routines that exist but are never executed or referenced | Confusion during maintenance; false dependency analysis; wasted resources if accidentally triggered |
| RF-ARCH-004 | MEDIUM | Implicit Dependencies | Jobs depend on output files or temp tables created by other jobs, with no explicit dependency declaration | Race conditions; failures when job execution order changes |
| RF-ARCH-005 | MEDIUM | Version Control Gaps | Multiple versions of similar jobs (e.g., job_v1, job_v2, job_v2_final) in the codebase | Confusion about active version; risk of running wrong version |
| RF-ARCH-006 | LOW | Over-Engineering | Excessive abstraction layers for simple operations; child job nesting >3 levels deep | Debugging difficulty; performance overhead; maintenance complexity |

## Operational Red Flags (RF-OPS)

| ID | Severity | Name | Description | Impact |
|----|----------|------|-------------|--------|
| RF-OPS-001 | HIGH | No Error Recovery | Failed job requires manual database cleanup + full re-run; no checkpoint mechanism | Extended downtime; risk of human error during recovery; data duplication |
| RF-OPS-002 | HIGH | Environment Leakage | Production connection strings or credentials visible in development/test environments | Security risk; accidental production data modification from test environments |
| RF-OPS-003 | MEDIUM | Silent Job Failure | Job returns success exit code even when data processing errors occurred | Monitoring blind spot; SLA violations go undetected |
| RF-OPS-004 | MEDIUM | Missing Timeout | Long-running operations (API calls, DB queries, file transfers) without timeout configuration | Resource lock-in; cascading failures; zombie processes |
| RF-OPS-005 | LOW | Hardcoded File Paths | Absolute file paths (e.g., C:\Users\dev\..., /home/talend/...) in job configuration | Fails on different environments; deployment friction |
