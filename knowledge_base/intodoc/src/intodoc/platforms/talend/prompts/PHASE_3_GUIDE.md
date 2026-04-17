# Phase 3: Cross-Group Synthesis Guide

Use this guide when assembling final documentation from multiple group analyses.
These instructions cover cross-cutting concerns that span groups.
Phase 2 analysis data is your source -- synthesize it, do not re-extract from raw scan data.

---

## 00 Executive Summary (for 00_EXECUTIVE_SUMMARY)

Produce a customer-facing document with NO code or expressions. Structure:

1. **Platform Overview**: what the integration platform does, total jobs, groups, connections
2. **Architecture Summary**: high-level data flow (sources -> processing -> targets), key systems
3. **Health Dashboard**: overall health scores per dimension:
   - Data Quality: GOOD / ADEQUATE / POOR / NONE (aggregate across groups)
   - Error Handling: GOOD / ADEQUATE / POOR / NONE
   - Security Posture: GOOD / ADEQUATE / POOR / NONE
   - Performance: GOOD / ADEQUATE / POOR / NONE
   - Maintainability: GOOD / ADEQUATE / POOR / NONE
4. **Risk Summary**: top 5 critical/high risks across all groups
5. **Recommendations Roadmap**: prioritized action items (immediate / short-term / long-term)
6. **FAQ**: 10+ questions a new team member or stakeholder would ask
7. **Glossary**: platform-specific terms (Talend components, connection types, etc.)
8. **Health Dashboard Integration**: reference specific numeric scores from the Phase 2.6 assessment scorecard. For each dimension, include the quantitative score (e.g., "Error Handling: 2.3/5.0") alongside the qualitative rating, and cite the evidence table entries that justify each score.

---

## 01 Project Overview (for 01_PROJECT_OVERVIEW)

Synthesize COMPONENTS and EXTERNAL_SYSTEMS analysis into a comprehensive project overview.

1. **Regional/Group Breakdown Table**: for each group, list group name, job count, component count, and a one-line functional description. *Example: "DEU -- 12 jobs, 187 components -- German pricing and customer master data synchronization."*
2. **Component Landscape**: narrative describing the functional categories present across the project. Categorize all components into: Input, Transformation, Output, Control, Infrastructure, Custom Code. For each category, list the component types and their total instance count across all jobs. Include percentages of total component count.
3. **Component Categories Summary Table**: category | component types | instance count | percentage of total
4. **Job Dependency Graph**: produce an ASCII tree showing the explicit tRunJob parent-child hierarchy. Below the tree, list implicit dependencies (jobs sharing the same database tables or staging areas). *Example: "Master_Orchestrator -> [DEU_Pricing_STG, DEU_Customer_RAW, DEU_Export_FINAL]"*
5. **Key Technology Stack**: list all external systems, databases, APIs, and protocols identified in the EXTERNAL_SYSTEMS analysis. Group by function (source systems, target systems, infrastructure).

---

## 02 Architecture Inventory (for 02_ARCHITECTURE_INVENTORY)

Synthesize COMPONENTS, EXECUTION_FLOW, EXTERNAL_SYSTEMS, and SCHEDULING analysis into a detailed architecture document.

1. **System Topology Narrative**: describe the overall integration architecture pattern. Identify whether the topology is hub-and-spoke (one orchestrator calling many child jobs), mesh (peer-to-peer data sharing via tables), pipeline (sequential chain), or hybrid. *Example: "Hub-and-spoke: Master_DEU orchestrates 8 child jobs via tRunJob, each reading from staging tables written by upstream ETL."*
2. **Connection Inventory Table**: for every metadata connection, list: name | type (MSSQL, BigQuery, FTP, etc.) | host pattern | regions/groups using it | job count | mode (REPOSITORY/BUILT_IN)
3. **Trigger Patterns and Scheduling Summary**: synthesize SCHEDULING analysis into a summary of how jobs are triggered (cron, file arrival, event, manual), typical execution windows, and any SLA constraints identified.
4. **Control Flow Patterns**: identify the dominant execution patterns (e.g., "sequential subjob chains via SUBJOB_OK" or "parallel fan-out via tRunJob"). List which jobs use each pattern. *Example: "14 of 18 jobs use linear SUBJOB_OK chains; 3 orchestrators use parallel tRunJob fan-out; 1 job uses tFlowToIterate for batch processing."*
5. **Subjob Structure Analysis**: for each group, summarize subjob complexity -- average subjobs per job, range (min/max), and jobs with notably deep or wide subjob graphs.
6. **Per-Job Architecture Detail (MANDATORY -- do NOT skip)**: You MUST create a `#### {JobName}` subsection for EVERY job in the project. No exceptions -- every single job listed in the manifest must have its own entry. Each entry must follow this exact format:

   #### {JobName}

   **Purpose**: 1-2 sentence description of what this job does and why it exists.

   **Component Count**: {N} | **Connections**: {list} | **Group**: {group}

   Any additional architectural notes (e.g., notable patterns, tRunJob children, tMap complexity, error handling).

   This section is the canonical per-job reference. The topology UI reads the `**Purpose**:` lines from this doc to display job descriptions. If a job has no entry here, it will appear without a description in the UI and in RAG search results. Do NOT summarize jobs into tables -- each job gets its own heading.
7. **Route/Layer Topology** (conditional -- for ESB projects): if the project contains ESB routes, document the route hierarchy, service endpoints, and message flow between routes. For ETL-only projects, omit this section.

---

## 03 Data Flows and Lineage (for 03_DATA_FLOWS_LINEAGE)

Synthesize DATA_FLOWS, SCHEMA_DICTIONARY, and SOURCE_SQL analysis into complete data lineage documentation.

1. **Canonical Flow Pattern**: identify the dominant read-transform-write spine across the project. *Example: "MSSQL source -> tMap transformation -> BigQuery staging -> tMap enrichment -> BigQuery target"*
2. **Source System Diversity**: for each source system type (database, API, file, etc.), document the ingestion mechanism, number of jobs reading from it, and data volume indicators if available.
3. **Per-Group Flow Diagrams**: for each group, produce an ASCII flow diagram showing source -> transformation components -> target, with connection labels. Include all major data paths, not just the primary one.
   ```
   tMSSqlInput (CRM) --FLOW--> tMap_1 (enrich) --FLOW--> tBigQueryOutput (DWH)
                                    ^
   tFileInputDelimited (REF) --LOOKUP--+
   ```
4. **Column Mapping Tables**: for every tMap component, produce a table with: source_table.column | expression (FULL -- never truncate) | target_table.column | explanation. Group by tMap component within each job.
5. **Per-Job Transformation Detail (MANDATORY)**: For each job with tMap components, include a `#### {JobName} Transformations` subsection containing:
   - **Column mapping table**: ALL columns (not just notable ones):
     | Source | Expression | Target | Explanation |
     |--------|-----------|--------|-------------|
     | row1.customer_id | `row1.customer_id` | output.cust_id | Direct pass-through |
     | Var.fiscalYear | `Var.fiscalYear` | output.fiscal_year | From var table lookup |
     | (none) | `"EUR"` | output.currency | Hardcoded constant |
   - **Var table**: variable name, expression, which input rows it references
   - **Join logic**: main input, lookup tables, join keys, join type (INNER/LEFT OUTER), lookup model
   - **Filter/reject logic**: filter expression, accepted target, rejected target
   - For projects with >50 tMap jobs, group by functional domain but still include every job
   - This section provides the field-level detail needed to answer questions like "what does column X do in job Y?"
6. **API Endpoint Catalog** (conditional -- if REST/OData sources exist): list every API endpoint URL, HTTP method, authentication method, pagination pattern, and which jobs consume it.
7. **Data Quality Rules Catalog**: synthesize data quality checks from DATA_FLOWS analysis into a table: rule_id | type (validation/dedup/default/schema) | job.component | expression | gap assessment (what is NOT checked)
8. **End-to-End Lineage Examples**: trace at least one complete data path per group from initial source through all transformations to final target. Include every tMap expression along the path. *Example: "CRM.customer_id -> tMap_1: row1.customer_id -> staging.cust_id -> tMap_2: (String)input.cust_id -> DWH.customer_key"*

---

## 04 Business Logic (for 04_BUSINESS_LOGIC)

Synthesize BUSINESS_RULES and CUSTOM_CODE analysis into a business logic reference organized by functional domain.

1. **Domain-Organized Rules**: organize all extracted business rules by functional domain (pricing, currency conversion, customer management, eligibility, routing, scheduling, etc.) -- NOT by job. Each domain section lists all rules from all jobs that belong to that domain.
2. **Pattern-Based Sections**: create dedicated subsections for recurring patterns:
   - **Upsert Patterns**: how records are created-or-updated (INSERT/UPDATE logic, merge keys, conflict resolution)
   - **Transformation Patterns**: recurring data transformations (date formatting, currency conversion, unit conversion, string normalization)
   - **Validation Patterns**: input validation rules (null checks, range validation, reference lookups, regex matching)
   - **Auth/Credential Patterns**: how authentication and credentials are managed in data flows (token refresh, credential injection, session management)
   - **Lookup/Enrichment Patterns**: how reference data is joined (tMap lookup joins, SQL subqueries, file-based lookups)
3. **Function Chain Traces**: For the most complex business rules (3+ chained transformations), include full step-by-step traces showing tMap Var table entries and intermediate values. Include at least one trace per business domain identified. Example format:
   ```
   Input: row1.invoiceDate (String "03/15/24")
   -> Var.parsedDate: TalendDate.parseDate("MM/dd/yy", row1.invoiceDate) = Date(2024-03-15)
   -> Var.fiscalPeriod: lookup(hash_FiscalPeriods, Var.parsedDate) = "FY2024-Q3"
   -> output.Fiscal_Period: Var.fiscalPeriod = "FY2024-Q3"
   ```
   Another example (currency conversion):
   ```
   Input: row1.Net_Sales_Amount (BigDecimal 1500.00), row1.Currency_Code (String "BRL")
   -> Var.convRate: lookup(hash_ConversionRates, row1.Currency_Code) = 5.42
   -> Var.amountEUR: row1.Net_Sales_Amount / Var.convRate = 276.75
   -> output.Net_Sales_Amount_EUR: Var.amountEUR = 276.75
   ```
4. **Parameter Catalog Table**: value | location (job + component) | apparent purpose | risk-if-stale. Include every hardcoded date, status code, threshold, magic number, and string constant found in BUSINESS_RULES analysis.
5. **Conflict Detection Summary**: cases where the same business concept is handled differently across jobs. *Example: "Date formatting: DEU jobs use dd.MM.yyyy, NLD jobs use yyyy-MM-dd -- potential consistency risk in cross-region reporting."*
6. **Custom Code Landscape**: total custom code instances, language breakdown (Java in tJava/tJavaRow/tJavaFlex), purpose categories (transformation, validation, file I/O, API calls, error handling). Reference CUSTOM_CODE analysis.
7. **Impact Analysis**: for the highest-impact rules (those rated WIDE in BUSINESS_RULES analysis), provide a dedicated impact paragraph explaining downstream effects, duplication risk, and remediation priority.

---

## 05 Operations and Risk (for 05_OPERATIONS_RISK)

Synthesize ERROR_HANDLING, RISK_FLAGS, SCHEDULING, and CUSTOM_CODE analysis into a comprehensive operations and risk document.

1. **Error Handling Rating Table**: per-job table: job name | group | rating (GOOD/ADEQUATE/POOR/NONE) | error components present (tLogCatcher, SUBJOB_ERROR connections, tDie, tWarn) | specific gaps identified
2. **Per-Component Error Audit**: for each component in critical data paths, document the DIE_ON_ERROR setting (true/false/unset). Organize by job. Flag dangerous false/unset settings on data-writing components (tBigQueryOutput, tMSSqlOutput, etc.) and API-calling components (tREST, tSOAP, etc.). *Example: "sjAccount_OffRamp_DWH.tBigQueryOutput_1: DIE_ON_ERROR=false -- silent data write failure, rows may be lost without detection."*
3. **Silent Failure Catalog**: organize silent failures by category:
   - Truncate-without-verify: write operations with no post-write row count check
   - Lookup-on-empty: tMap lookups that return null on no match without error
   - API-pagination: REST calls without complete pagination handling
   - Fire-and-forget: tRunJob calls with DIE_ON_CHILD_ERROR=false
4. **Data Quality Risk Assessment**: per group, calculate coverage percentages for: validation checks | deduplication | null handling | schema enforcement. Rate each as percentage of data paths that have each type of check.
5. **Error Handling Maturity Model**: rate each group on a 1-5 scale:
   - 5: Comprehensive (all paths have error handling, logging, alerting, recovery)
   - 4: Strong (most paths covered, minor gaps in non-critical paths)
   - 3: Moderate (critical paths covered, non-critical paths have gaps)
   - 2: Basic (some error handling exists but significant blind spots)
   - 1: Minimal (little to no systematic error handling)
6. **Operational Health Scorecard**: per-group table: group | error handling maturity | data quality coverage | security posture | performance risk level | overall health rating
7. **Consolidated Risk Register**: synthesize all RISK_FLAGS into a single register: risk_id | severity | category | affected jobs | description | recommended remediation | effort estimate

---

## 06 Environment and External Systems (for 06_ENVIRONMENT_EXTERNAL)

Synthesize ENVIRONMENT_CONFIG, EXTERNAL_SYSTEMS, CROSS_GROUP_REFS, and SCHEDULING analysis.

1. **Environment Landscape**: list all environments (PROD, TEST, DEV, Default, etc.) with purpose, risk level, and key differentiators. *Example: "PROD: live data, high risk -- 42 context variables, BigQuery write access. TEST: synthetic data, medium risk -- same structure, different connection strings."*
2. **PROD/TEST Divergence Table**: variable category | count divergent | pattern | risk. Identify context variables where PROD and TEST values differ. *Example: "Database hosts: 12 divergent -- all point to different servers (expected). API keys: 3 divergent -- TEST keys may have different rate limits."*
3. **Runtime Environment Resolution**: describe how the correct environment configuration is selected at execution time. Document the precedence order: explicit context parameter > tContextLoad > job default context group > Default group. Identify any jobs using tContextLoad for dynamic resolution.
4. **External System Inventory Table**: system name | type (database/API/file/FTP/cloud) | regions using it | protocol | auth method | job count | direction (read/write/both)
5. **Cross-Region/Cross-Group Dependencies**: from CROSS_GROUP_REFS analysis, document shared database tables (written by one group, read by another), shared connections, cross-group tRunJob calls, and execution ordering dependencies.
6. **Scheduling and SLA Summary**: trigger types used (cron/file/event/manual), execution windows, concurrency controls, recovery patterns. If SLA windows were identified in SCHEDULING analysis, document them with consequence of missing.

---

## 07 Security and Performance (for 07_SECURITY_PERFORMANCE)

Synthesize RISK_FLAGS (security + performance categories), CUSTOM_CODE (risk factors), and ENVIRONMENT_CONFIG analysis.

1. **Security Finding Summary Table**: category (credentials/injection/encryption/permissions) | count | severity | description
2. **Credential Management Analysis**: how credentials are stored (context variables, REPOSITORY connections, hardcoded), how they are rotated (manual, automated, unknown), and how they are accessed at runtime. Flag any credentials found in custom code or BUILT_IN connections.
3. **Per-Group Security Posture**: for each group, summarize the security stance -- credential handling, input validation, data encryption, access controls. Rate as STRONG / ADEQUATE / WEAK.
4. **Auth Method Inventory Table**: auth method (username/password, API key, OAuth, certificate) | systems using it | groups | rotation policy (if known) | risk level
5. **Compliance Considerations**: based on regions identified (EU -> GDPR, US -> SOX/HIPAA potential, etc.) and data types processed (PII, financial, health), list applicable regulatory frameworks and whether the current implementation addresses them.
6. **Performance Hotspot Analysis**: synthesize performance risks from RISK_FLAGS -- full table scans, unbounded queries, memory-intensive tMap lookups, large file operations. For each hotspot: job | component | issue | estimated impact | recommendation.
7. **Optimization Priority Matrix**: optimization | impact (HIGH/MEDIUM/LOW) | effort (HIGH/MEDIUM/LOW) | affected jobs | recommended action
8. **Monitoring and Observability Assessment**: what is currently monitored (tStatCatcher, tLogCatcher, row counts) vs what is not. For unmonitored areas, estimate detection latency (minutes/hours/days/never) for different failure types.

---

## 08 Patterns and Standards (for 08_PATTERNS_STANDARDS, only if 2+ groups)

Compare groups across these dimensions:

1. **Structural Comparison**: jobs per group, component types used, complexity distribution
2. **Naming Standardization**: are naming conventions consistent across groups? Score each group
3. **Pattern Reuse**: shared patterns vs group-specific patterns
   - Identify template jobs that appear in multiple groups with minor variations
   - Identify shared utility routines
4. **Error Handling Consistency**: compare error handling ratings across groups
5. **Data Flow Patterns**: are similar data flows implemented consistently?
6. **Configuration Consistency**: context variable naming, connection naming across groups
7. **Standardization Score**: rate overall cross-group consistency (1-5 scale):
   - 5: Highly standardized, clear shared patterns
   - 4: Mostly standardized with minor deviations
   - 3: Some shared patterns but significant group-specific variation
   - 2: Minimal standardization, each group evolved independently
   - 1: No standardization, completely independent implementations
8. **Naming Convention Compliance**: synthesize NAMING_PATTERNS analysis into a compliance assessment. For each naming dimension (jobs, tables, variables, connections), document the dominant pattern, the compliance rate (percentage of entities following the pattern), and notable outliers.
9. **Reusability Assessment**: quantify duplication across groups. Count jobs with near-identical structure (same component sequence, different connection targets), duplicated tMap expressions, and copy-paste custom code blocks. Calculate a reuse ratio: shared patterns / total patterns.
10. **Technical Debt Inventory**: categorize accumulated debt by type: naming inconsistencies, dead code paths, unused components, deprecated patterns, hardcoded values, missing error handling. Include count per category and affected jobs.
11. **Remediation Roadmap**: three phases:
    - **Stop-the-bleeding** (immediate): fix critical risks, add error handling to unprotected data-writing paths
    - **Foundation** (1-3 months): standardize naming, extract shared patterns into template jobs, consolidate duplicate logic
    - **Systematic** (3-6 months): implement monitoring, add data quality gates, refactor complex custom code

---

## 09 Runbook (for 09_RUNBOOK)

Synthesize ERROR_HANDLING, EXECUTION_FLOW, SCHEDULING, ENVIRONMENT_CONFIG, and RISK_FLAGS analysis into actionable operational procedures. This is the most operationally critical document -- it must be detailed enough for an on-call engineer to use without prior project knowledge.

1. **Pre-Flight Checks**: organized by category:
   - **Environment Verification**: confirm correct context group is active, verify connection string resolution, check for context variable overrides. *Example: "Verify tContextLoad in Master_DEU resolves to PROD context group before executing."*
   - **Network Connectivity**: list all external systems that must be reachable, with expected ports and protocols. Include health check commands or queries where available.
   - **Reference Data Currency**: identify lookup tables and reference datasets that must be current before execution. Document maximum acceptable staleness.
   - **Resource Availability**: disk space requirements for temp files and staging, memory requirements for tMap lookups, database connection pool availability.

2. **Common Failure Scenarios**: for each, provide structured entries:
   - **Symptom**: what the operator sees (error message, stalled job, missing output)
   - **Root Cause**: the underlying issue
   - **Affected Jobs**: which jobs are impacted
   - **Resolution Steps**: numbered steps to diagnose and fix
   - **Prevention**: how to prevent recurrence
   Cover at minimum:
   - Connection failures (database unavailable, timeout, auth failure)
   - Quota/rate limit exhaustion (API throttling, BigQuery quota, connection pool)
   - Data truncation (target column too narrow, character encoding mismatch)
   - Disk space exhaustion (temp files, log files, staging areas)
   - Reference data staleness (lookup table not refreshed, stale cache)

3. **Per-Job Execution Sequences**: for critical orchestration jobs (those with tRunJob children), document the exact execution order: step number | job/subjob name | purpose | context variables passed | expected duration. Include decision points where conditional execution (RunIf) may skip steps.

4. **Monitoring Recommendations Table**: what to monitor | how (tStatCatcher / row counts / log parsing / external tool) | alert threshold | priority (P1-P4)

5. **Recovery Procedures**: per critical data path, document:
   - **Full Rebuild**: steps to re-run from scratch (truncate + reload)
   - **Partial Recovery**: steps to resume from failure point (if idempotent)
   - **Cascade Recovery**: if upstream job failed, which downstream jobs need re-running and in what order

6. **Verification Queries**: SQL queries or checks to confirm successful execution -- row count comparisons (source vs target), freshness checks (max timestamp in target), cross-dataset consistency checks (referential integrity between related tables).

7. **Escalation Matrix**: tier | role | handles | escalation trigger. *Example: "Tier 1: On-call engineer -- connection failures, restarts. Tier 2: Data engineer -- data quality issues, expression errors. Tier 3: Platform architect -- structural failures, migration blockers."*

8. **Environment-Specific Operational Notes**: per environment (PROD, TEST), document any operational differences -- different scheduling, different data volumes, different error thresholds, different monitoring levels.

9. **Post-Incident Review Template**: provide a template for documenting incidents: timestamp, affected jobs, symptom, root cause, resolution, duration, prevention actions.

---

## 10 Onboarding Guide (for 10_ONBOARDING_FAQ)

1. **Getting Started**: what a new developer needs to know on day 1
2. **Architecture Walkthrough**: how the jobs fit together end-to-end
3. **Common Tasks**: how to add a new job, modify a transformation, add a new connection
4. **Troubleshooting**: common failure modes and how to diagnose them
5. **FAQ**: 20+ questions organized by topic (architecture, data flows, operations, debugging)
6. **Glossary**: all domain-specific and platform-specific terms

---

## 11 Migration Assessment (for 11_MIGRATION_ASSESSMENT)

For each job, assess migration complexity:

1. **Complexity Score (1-5)**:
   - 1: Simple (linear flow, standard components, no custom code)
   - 2: Moderate (tMap transformations, context variables, standard patterns)
   - 3: Complex (custom Java code, complex tMap expressions, multiple lookups)
   - 4: Very Complex (tJavaFlex, external API calls, complex orchestration)
   - 5: Extreme (system-level integrations, undocumented dependencies, legacy patterns)

2. **Auto-Migrate Probability**:
   - HIGH: standard components, clear mappings, no custom code
   - MEDIUM: some custom code but standard patterns, translatable expressions
   - LOW: heavy custom code, platform-specific features, complex orchestration
   - MANUAL: requires complete redesign (tJavaFlex with system calls, etc.)

3. **Blocker Classification**:
   - HARD BLOCKER: no equivalent in target platform (requires redesign)
   - SOFT BLOCKER: equivalent exists but requires manual configuration
   - WARNING: works but may behave differently (data type handling, null semantics)

4. **Migration Groups**: cluster jobs by migration approach:
   - Lift-and-shift candidates (standard ETL patterns)
   - Transform-and-migrate (needs expression translation)
   - Redesign required (complex custom code or platform-specific features)

5. **Dependency Order**: which jobs must be migrated first (shared connections, parent jobs, utility routines)

6. **Migration Readiness Composite Score**: multi-dimension 1-5 rating covering: code complexity, external system dependency count, custom code percentage, test coverage, documentation completeness. Produce a single weighted score per job and an aggregate per group.

7. **Platform Comparison Table** (conditional -- if target platform is specified): for each Talend component type used, list the target platform equivalent, translation complexity (automatic/manual/redesign), and known behavioral differences. *Example: "tMap -> DataWeave (MuleSoft): manual translation, expression syntax differs significantly."*

8. **Scenario Comparison**: compare three migration approaches for the overall project:
   - **Lift-and-shift**: move as-is to compatible runtime (e.g., Talend Cloud). Pros, cons, effort estimate, risk level.
   - **Transform**: translate to target platform preserving logic. Pros, cons, effort estimate, risk level.
   - **Redesign**: rebuild from requirements using target platform best practices. Pros, cons, effort estimate, risk level.
   Include a recommendation with justification based on the project's complexity profile.

9. **Pre-Migration Checklist**: items to complete before migration begins -- document all undocumented dependencies, inventory all hardcoded values, verify test environment parity, establish rollback procedures, define success criteria.

10. **Post-Migration Verification Checklist**: items to verify after migration -- row count parity (source vs target, pre vs post), data quality spot checks, performance benchmarks (execution time comparison), error handling verification, scheduling confirmation.

11. **Technical Debt for Migration**: from RISK_FLAGS and CUSTOM_CODE analysis, list technical debt items that should be resolved BEFORE migration (to avoid migrating debt) vs items that can be addressed DURING migration (as part of the redesign) vs items that are eliminated BY migration (platform-specific issues).

12. **Key Architectural Decisions**: for each major component or pattern, document whether to preserve (migrate as-is), redesign (rebuild with better approach), or eliminate (no longer needed in target platform). Include justification.

13. **Quick Wins**: identify 3-5 jobs or patterns that can be migrated first with minimal risk, to build confidence and establish migration patterns for the team. These should be simple jobs with high visibility.
