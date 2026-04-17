# Integration Platform Analysis Guide

This guide provides platform-agnostic analysis instructions for Phase 2 sub-agents.
Use it when no platform-specific guide exists (Talend, Workato, etc. have their own).
Write markdown, not JSON. Be exhaustive -- include every expression, rule, column, and risk.

---

## Section Analysis Instructions

For each section below, analyze ALL jobs/flows in your group. Be exhaustive.
Every claim needs a specific job/flow name and component reference.

### COMPONENTS

Extract for each job/flow:
- Name, purpose, and group membership
- All components/steps with type, name, and role
- All connections between components (data flow, triggers, error paths)
- Dependencies: child job references, shared connection references
- Orphaned components: steps with no incoming or outgoing connections

### EXECUTION_FLOW

- Ordered execution sequence for each job/flow
- Trigger conditions between steps
- Parent-child orchestration: which jobs call which
- Parallel execution paths
- Iteration/loop patterns
- Dead paths: unreachable steps

### DATA_FLOWS

- Column-level lineage: source.column -> transformation -> target.column
- Full transformation expressions (do NOT truncate)
- Human-readable explanation of each transformation
- Filter/reject conditions
- Join types between data sources
- Null handling and default value patterns
- Data validation checks (or lack thereof)

### TRANSFORMATION_DETAIL

For EVERY transformation component in each job/flow, produce a per-component breakdown:

1. **Column mapping table** (ALL mapped columns):

   | Source | Expression/Transform | Target | Explanation |
   |--------|---------------------|--------|-------------|
   | input.field_a | direct copy | output.field_a | Pass-through |
   | input.field_b | trim + uppercase | output.field_b | String normalization |
   | (none) | constant "DEFAULT" | output.status | Hardcoded default |

2. **Join/lookup analysis**: For components joining multiple data sources:
   - Main input
   - Lookup/secondary inputs
   - Join keys and join type
   - What happens on no-match (null, default, reject)

3. **Filter/routing analysis**: For components that split or filter data:
   - Filter condition (full expression)
   - Accepted rows destination
   - Rejected/filtered rows destination (or silent discard)

4. **Function chain traces**: For the most complex transformations (3+ chained operations):
   - Step-by-step trace from source to target
   - All intermediate variables or computations
   - Include at least one trace per job with complex transformations

### BUSINESS_RULES

Extract EVERY business rule:
- **BR-{GRP}-NNN**: sequential rule ID
- **Expression**: the actual code/expression
- **Implementing job**: which job and component
- **Domain**: business area
- **Impact**: what happens if violated

### SCHEMA_DICTIONARY

For EVERY table/view/endpoint referenced:
- Name (fully qualified if available)
- Column names, data types, nullable flag
- Which jobs READ vs WRITE
- Connection used to access it

### SOURCE_SQL

Every SQL query in FULL:
- Which component/job contains it
- Whether source query, lookup, or DML
- Tables and columns referenced

### EXTERNAL_SYSTEMS

All external connections:
- System type (database, API, file, etc.)
- Connection name and mode (shared vs inline)
- Host, port, database (sanitize credentials)
- Which jobs use it and for what purpose

### ERROR_HANDLING

Rate EACH job:
- **GOOD / ADEQUATE / POOR / NONE**
- Specific error handling components and patterns
- Silent failure risks
- Retry/recovery mechanisms

### CUSTOM_CODE

All custom code blocks in full:
- Purpose and business logic implemented
- Hidden business rules
- External dependencies
- Risk factors

### ENVIRONMENT_CONFIG

Every configuration variable:
- Name, type, default value
- Environment-specific differences
- Which jobs use it

### SCHEDULING

- Trigger types and schedules
- Dependencies between jobs
- Retry configuration
- Recovery patterns

### NAMING_PATTERNS

- Observed naming conventions
- Consistency assessment (GOOD / ADEQUATE / POOR)

### CROSS_GROUP_REFS

- Shared tables, connections, or variables with other groups
- Cross-group execution dependencies

### RISK_FLAGS

Severity levels: CRITICAL / HIGH / MEDIUM / LOW

Check for:
- **Security**: hardcoded credentials, injection vectors, unencrypted data
- **Performance**: full table scans, unbounded queries, memory-intensive patterns
- **Reliability**: missing error handling, silent failures, no retry logic
- **Maintainability**: complex expressions, duplicated logic, magic numbers
- **Preventive**: single points of failure, cascading failure paths, monitoring gaps

For each risk: severity, affected jobs, description, recommended remediation.
