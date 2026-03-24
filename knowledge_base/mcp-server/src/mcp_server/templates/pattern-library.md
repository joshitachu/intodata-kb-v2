# Integration Pattern Library

Catalog of known good patterns. Reference when evaluating architecture and design quality.

## Error Handling Patterns

### P-ERR-001: Centralized Error Handler
**Description**: Dedicated error-handling subjob called via tRunJob from main job's OnComponentError trigger. Captures error context (job name, component, timestamp, message), writes to error table, and optionally triggers alerts.
**Benefits**: Consistent error logging across all jobs; single point to modify alerting logic.
**Look for**: Shared error-handler jobs referenced from multiple parent jobs; error tables with standardized schema.

### P-ERR-002: Dead Letter Queue
**Description**: Rejected/failed records routed to a persistent queue (database table, file, or message queue) for later investigation and reprocessing.
**Benefits**: No data loss on processing failures; allows batch reprocessing of problematic records.
**Look for**: tMap reject flows connected to tDBOutput (error tables) or tFileOutput; separate reprocessing jobs that read from error tables.

### P-ERR-003: Retry with Backoff
**Description**: Transient failures (API timeouts, DB locks) handled with configurable retry count and increasing delay between attempts.
**Benefits**: Resilience against temporary infrastructure issues; self-healing for common failure modes.
**Look for**: Loop components (tLoop, tFlowToIterate) with retry counter variables; increasing sleep intervals between retries.

### P-ERR-004: Circuit Breaker
**Description**: After N consecutive failures to an external system, stop attempting calls for a cooldown period. Prevents cascading failures and resource exhaustion.
**Benefits**: Prevents overwhelming failing systems; fast failure instead of timeout accumulation.
**Look for**: Error counter variables; conditional routing that skips calls after threshold exceeded.

## Data Quality Patterns

### P-DQ-001: Schema Validation at Boundary
**Description**: Every external data source (file, API, database) validated against expected schema immediately after ingestion, before entering the transformation pipeline.
**Benefits**: Early failure with clear error message; prevents corrupt data from propagating.
**Look for**: tSchemaComplianceCheck or custom validation tJavaRow immediately after tFileInput/tRESTClient; reject flows for invalid records.

### P-DQ-002: Null-Safe Transformations
**Description**: All tMap expressions include explicit null handling via ternary operators, COALESCE patterns, or default values.
**Benefits**: No NullPointerException surprises; predictable behavior for missing data.
**Look for**: Ternary expressions (`field != null ? field : defaultValue`); Talend's `Relational.ISNULL()` usage.

### P-DQ-003: Idempotent Operations
**Description**: Jobs produce the same result when run multiple times. Uses UPSERT (INSERT ON CONFLICT), merge keys, or delete-then-insert with transaction wrapping.
**Benefits**: Safe re-runs after partial failures; no duplicate data.
**Look for**: ON CONFLICT clauses; MERGE statements; transaction-wrapped delete+insert patterns; natural key deduplication before insert.

### P-DQ-004: Data Quality Metrics
**Description**: Jobs count total/valid/rejected/skipped records and write summary metrics after processing.
**Benefits**: Visibility into data quality trends; anomaly detection (sudden spike in rejects).
**Look for**: Counter variables (globalMap); summary tLogRow at job end; metrics tables with row counts per run.

## Architecture Patterns

### P-ARCH-001: Shared Context Groups
**Description**: Connection parameters, environment settings, and common configuration stored in shared context groups, referenced across all jobs.
**Benefits**: Single point of change for environment migration; consistent configuration.
**Look for**: Context groups with environment-prefixed variables; context loading at job start; no hardcoded connection strings.

### P-ARCH-002: Layered Job Design
**Description**: Three-tier job structure: (1) orchestrator jobs that manage flow, (2) worker jobs that process data, (3) utility jobs for cross-cutting concerns (logging, notification).
**Benefits**: Separation of concerns; testable components; reusable utilities.
**Look for**: tRunJob hierarchies with clear parent/child roles; utility jobs called from multiple parents.

### P-ARCH-003: Consistent Naming Convention
**Description**: Standardized naming across all artifacts: `{region}_{domain}_{operation}_{target}` (e.g., `DEU_Customer_Load_DWH`).
**Benefits**: Self-documenting job inventory; easy filtering and grouping; clear ownership.
**Look for**: Consistent prefixes per region/team; operation type indicators (Load, Extract, Transform, Sync).

### P-ARCH-004: Parameterized Jobs
**Description**: Jobs designed to work with different configurations via context variables, avoiding copy-paste for regional variants.
**Benefits**: Single source of truth for logic; reduced maintenance; easier testing.
**Look for**: Context variable-driven behavior (connection switching, filter criteria, output paths); same job called with different contexts.

## Operations Patterns

### P-OPS-001: Structured Logging
**Description**: All log entries follow a consistent format: `[timestamp] [job_name] [severity] [component] message`. Severity levels used correctly (DEBUG/INFO/WARN/ERROR).
**Benefits**: Parseable logs; effective monitoring and alerting; fast troubleshooting.
**Look for**: tLogRow with formatted output expressions; severity-level differentiation; timestamp inclusion.

### P-OPS-002: Health Check Jobs
**Description**: Lightweight jobs that verify connectivity to all external systems (databases, APIs, file servers) before main processing begins.
**Benefits**: Fast failure with clear diagnostic; prevents partial processing when infrastructure is down.
**Look for**: Pre-check jobs in scheduling chains; connection test components; status reporting.

### P-OPS-003: Incremental Processing
**Description**: Jobs track last-processed timestamp or ID, processing only new/changed records in each run.
**Benefits**: Faster execution; reduced resource usage; scalable to large datasets.
**Look for**: Watermark variables (last_run_timestamp, max_processed_id); WHERE clauses filtering by timestamp; checkpoint tables.

### P-OPS-004: Graceful Shutdown
**Description**: Long-running jobs check for shutdown signals at safe points, completing current batch before exiting.
**Benefits**: Clean termination; no partial writes; no resource leaks.
**Look for**: Context variables for shutdown flags; batch completion checks; cleanup routines on exit.

## Security Patterns

### P-SEC-001: Externalized Credentials
**Description**: All credentials stored outside the job repository -- in environment variables, vault systems (HashiCorp Vault, AWS Secrets Manager), or encrypted credential stores.
**Benefits**: No credential exposure in source control; easy rotation; environment isolation.
**Look for**: Context variables referencing environment variables (System.getenv()); vault client components; empty default values for credential fields.

### P-SEC-002: Data Masking Pipeline
**Description**: Dedicated masking/anonymization step for non-production environments. Sensitive fields replaced with realistic but fake data.
**Benefits**: Safe test data; compliance with data protection regulations.
**Look for**: Masking jobs or components; environment-conditional masking logic; hash/anonymization functions.

### P-SEC-003: Least Privilege Access
**Description**: Each job/service account has only the minimum database/API permissions needed for its specific task.
**Benefits**: Reduced blast radius; defense in depth; compliance.
**Look for**: Different connection contexts per job type (read-only for extracts, write for loads); restricted schema access.
