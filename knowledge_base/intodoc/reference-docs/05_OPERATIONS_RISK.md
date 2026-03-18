# Operations, Risk & Change Management

This document consolidates the operational analysis of the Vaxxinova Talend ETL platform across four dimensions: error handling assessment, scheduling and SLA requirements, the known issues and risk dashboard, and change history practices. Together, these areas define the platform's operational posture -- how it handles failures, when it runs, what risks it carries, and how changes are tracked. The findings paint a picture of a platform with significant operational debt: 89% of jobs have no error handling, no SLAs are documented, 159 known issues exist across severity levels, and no structured change log has been maintained. Addressing these gaps is a non-negotiable requirement for any migration.

---

## Part 1: Error Handling Assessment

### Overview

Of the 257 Talend jobs in the platform, **229 (89%) have zero error handling**. No tLogCatcher components, no tDie components, no SUBJOB_ERROR trigger flows -- nothing. When these jobs fail, they fail silently. Tables may be truncated and left empty, data may be partially written, and no one receives an alert. This is the platform's most critical technical debt and the single highest-risk area for migration planning.

### Rating Breakdown

| Rating | Jobs | % | Description |
|--------|------|---|-------------|
| None | 229 | 89% | No error handling components detected |
| Adequate | 27 | 11% | Basic tLogRow diagnostic logging present |
| Good | 1 | <1% | Uses tDie component for active failure signaling |

The 27 "adequate" jobs include tLogRow components that write intermediate data to the console. This is diagnostic output, not true error handling -- there is no conditional logic distinguishing successful records from failed ones, no threshold triggering alerts, and no persistence beyond the console session.

The single "good" job exists in the scratchpad region -- a development experiment that was never propagated to production. The irony is pointed: the only job that properly handles errors is a test job never meant for production use.

### What "No Error Handling" Means in Talend

In Talend's visual job design paradigm, error handling is not automatic. Unlike modern orchestration platforms that wrap every task in implicit try-catch blocks, Talend requires developers to explicitly add error-handling components and connect them via trigger flows. The three primary mechanisms are:

- **tLogCatcher**: Captures Java exceptions, Talend warnings, and component-level errors into a structured log stream. Not a single production job uses tLogCatcher.
- **tDie**: Immediately terminates job execution with a custom error message and non-zero return code, enabling external schedulers to detect failure. Only one job uses tDie.
- **SUBJOB_ERROR triggers**: Fire when a subjob encounters an error, routing execution to cleanup or compensating logic. Rarely connected anywhere in this codebase.

### Region Comparison

| Region | Total Jobs | None | Adequate | Good | % Without Handling |
|--------|------------|------|----------|------|--------------------|
| BRA | 75 | 65 | 10 | 0 | 87% |
| USA | 61 | 54 | 7 | 0 | 89% |
| Global | 43 | 41 | 2 | 0 | 95% |
| JPN | 36 | 36 | 0 | 0 | 100% |
| GENERIC | 15 | 10 | 5 | 0 | 67% |
| DEU | 12 | 10 | 2 | 0 | 83% |
| ITA | 6 | 6 | 0 | 0 | 100% |
| scratchpad | 5 | 3 | 1 | 1 | 60% |
| ZAF | 3 | 3 | 0 | 0 | 100% |
| SAF | 1 | 1 | 0 | 0 | 100% |

Three regions -- JPN (36 jobs), ITA (6 jobs), and ZAF (3 jobs) -- have absolutely no error handling. Japan is the most concerning: 36 jobs processing sales invoices, credit memos, and general ledger data with zero ability to detect or report failures. The Global region at 95% unhandled is equally alarming because these are the consolidation jobs producing unified reporting datasets.

### Why Error Handling Was Never Adopted

- **Talend's opt-in model**: Error handling is not a default; developers must explicitly add components.
- **Copy-based development**: New jobs created by copying existing ones inherit the gap.
- **No code review or quality gate**: No deployment checklists, no CI pipeline checks.
- **Incremental growth without architectural oversight**: 257 jobs accumulated without periodic reviews.
- **The scratchpad prototype was never evangelized**: At least one team member understood best practices but never propagated the pattern.

### Silent Failure Risks

The absence of error handling interacts with specific data patterns to amplify risk:

- **TRUNCATE + No Error Handling = Catastrophic Data Loss**: Jobs using TRUNCATE mode delete all existing data before writing. Failure after truncation leaves tables empty with no alert.
- **In-Memory Lookups + No Error Handling = Silent Join Failures**: Empty hash tables from failed reference data loads produce null enrichment values that propagate silently.
- **API Pagination + No Error Handling = Truncated Extractions**: Pagination failures mid-sequence result in partial data treated as complete.
- **No Alerting, No Escalation**: No mechanism for email, Slack, or PagerDuty notifications.

### Concrete At-Risk Jobs

- **BIO_Clean_Invoice_Set_v5 (BRA)**: TRUNCATE write mode. Failure after truncation leaves Sales_Set empty; Global consolidation processes empty data.
- **VBR_Raw_Customers (BRA)**: Extracts from on-premises MSSQL. Silent failure causes progressive data staleness.
- **Legacy_Ingest_Sales_Set (Global)**: DELETE-then-INSERT logic; failure between steps leaves gaps in the global Sales_Set.
- **VJP_Clean_Ingest_Sales_Set (JPN)**: Final step publishing to Global_Clean. Upstream corruption propagates into global reporting.
- **NPL_Clean_Account_v4 (USA)**: At version 4, iterated multiple times, yet no version added error handling.

### The Cost of Silent Failures

- **Incorrect management decisions** based on stale or empty data in budget-vs-actual reporting
- **Salesforce CRM inconsistency** affecting sales team productivity when product sync fails silently
- **Regulatory and audit risk** from incomplete financial records in Japan's GL pipeline
- **Cumulative engineering time** spent on reactive firefighting (estimated 2-4 hours per incident)
- **Data trust erosion** leading to parallel manual data collection and shadow spreadsheets

### Error Handling Maturity Model

| Level | Description | Current Jobs |
|-------|-------------|-------------|
| 0: No Handling | No detection, logging, or recovery | 229 |
| 1: Diagnostic Logging | tLogRow output, no automated alerting | 27 |
| 2: Fail-Fast with Signaling | tDie or equivalent; non-zero exit code | 1 |
| 3: Structured Error Handling | tLogCatcher, persistent logs, compensating actions | 0 |
| 4: Self-Healing with Observability | Retry logic, circuit breakers, data validation, metrics | 0 |

The migration should establish Level 2 as the absolute floor and Level 3 as the target for critical jobs. Level 4 should be pursued for Global consolidation and regional Sales_Set builders.

### Quick Wins for Error Handling

1. **Add tLogCatcher to the 10 highest-risk jobs.** Transforms silent failures into detectable events. Estimated: 2-3 weeks.
2. **Add row-count validation after every TRUNCATE write.** A post-write COUNT(*) check catches empty-table failures. Estimated: half a day per job.
3. **Implement a monitoring query.** A scheduled BigQuery query checking lastModifiedTime of critical tables. Estimated: 1-2 days.
4. **Propagate the tDie pattern to the Global orchestrator.** Estimated: half a day.
5. **Enable Talend's built-in stats and logs capture.** Project-level setting providing persistent execution records. Estimated: 1 day.

---

## Part 2: Scheduling & SLA Requirements

### Overview

All 257 Talend jobs are marked as "on-demand" in their metadata -- no cron expressions, no schedule definitions, no time-based triggers. Job scheduling is managed externally by TAC, cron, or an external orchestrator. The metadata tells us everything about internal logic but nothing about production cadence, execution windows, or service-level expectations.

### What We Know from Metadata

- **Every job is on-demand.** 257 of 257 jobs report on-demand triggers.
- **Dependency chains are shallow.** Only 10 jobs have detectable dependencies; maximum chain depth is 1.
- **One bottleneck job exists.** `Global_Clean_Sales_Set` orchestrates 10 regional Sales_Set jobs and is the single chokepoint.

### Dependency Chain Analysis

| Region | Jobs | Max Chain Depth | Has Dependencies |
|--------|------|-----------------|------------------|
| BRA | 75 | 0 | No |
| USA | 61 | 0 | No |
| Global | 43 | 1 | Yes (1 orchestrator) |
| JPN | 36 | 0 | No |
| GENERIC | 15 | 0 | No |
| DEU | 12 | 0 | No |
| ITA | 6 | 0 | No |
| scratchpad | 5 | 0 | No |
| ZAF | 3 | 0 | No |
| SAF | 1 | 0 | No |

Despite zero-depth chains across 9 of 10 regions, implicit data dependencies create a three-tier execution graph: raw extraction -> clean transformation -> global consolidation. These are expressed through shared BigQuery tables, not through Talend's job invocation mechanism. The actual scheduling DAG is significantly more complex than metadata suggests.

### The SLA Gap

No SLA definitions were found in Talend metadata. Critical unanswered questions:

- **When must the global Sales_Set be refreshed each day?**
- **What is the acceptable data latency?** Can the warehouse be 24 hours behind source ERPs?
- **What is the recovery time objective?** How quickly must Global_Clean_Sales_Set be recovered?
- **Which jobs are business-critical vs. deferrable?**

### Recommended SLA Tiers

| Tier | Description | Example Jobs | SLA Approach |
|------|-------------|--------------|--------------|
| Critical | Global consolidation, Salesforce sync | Global_Clean_Sales_Set, BIO_Sync_Product_Salesforce_v1 | Hard deadline; 15-minute alert on failure |
| High | Regional Sales_Set builds | Copy_of_VBR_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set | Complete before global trigger; 2x retry |
| Standard | Raw extraction, reference data | BRA_VBR_RAW, JPN_VJP_RAW, GEN_BC_* | Daily freshness; alert on 2 consecutive failures |
| Low | Budget/forecast, one-off loads | Budget_Build_Sales_Set, Prior_Build_Sales_Set | Weekly/monthly; alert on persistent failure |

### Recommended Schedule Design

**BRA (Brazil):** Split into legacy MSSQL track (01:00-03:00 BRT) and Business Central API track (parallel). Sales_Set construction last after both tracks complete. Allocate 45-60 minutes for the 94-component build job.

**USA (United States):** Animalytix pipeline uses file-watch triggers rather than fixed schedules. NPL and VUS extraction run in parallel. Salesforce sync after Sales_Set completion.

**JPN (Japan):** Raw extraction during 02:00-04:00 JST. Japan's processing window naturally precedes US business day. Consider decomposing the monolithic JPN_VJP_RAW into parallel per-entity extractions.

**Global:** Dependency gate -- triggers only when all 10 regional Sales_Set tables are updated. Budget/forecast jobs can run weekly. Completion target: 06:00 UTC.

**GENERIC:** Templates complete before dependent regional clean-layer jobs. Concurrency limit of 3-4 simultaneous API jobs respects Business Central rate limits. Token refresh logic needed for long-running extraction cycles.

**DEU, ITA, ZAF, SAF:** Total pipelines fit within 30-minute to 2-hour windows. Italy and ZAF should not run simultaneously due to shared MSSQL connection pool contention.

### Documentation Gap -- Information Required

**From the platform team:**
- Job execution schedules per environment (cron expressions, trigger conditions, time zones)
- Execution server assignments and server-specific configurations
- Inter-job ordering enforced by the scheduler
- Retry and failure handling configuration from TAC
- Historical execution logs (at least 30 days)

**From business stakeholders:**
- Data freshness requirements per downstream consumer
- Business-critical vs. deferrable job classification
- Notification preferences and escalation paths
- Regulatory and compliance deadlines

---

## Part 3: Known Issues & Risk Dashboard

### Summary

**159 issues identified** across the platform:

| Severity | Count |
|----------|-------|
| Critical | 17 |
| High | 49 |
| Medium | 78 |
| Low | 15 |

### Top Issue Categories

| Category | Count |
|----------|-------|
| Code Hygiene | 11 |
| Error Handling | 9 |
| Data Integrity | 9 |
| Data Quality | 7 |
| Performance | 7 |
| Security | 5 |
| Authentication | 4 |
| Configuration | 3 |
| Operational | 3 |
| Version Management | 3 |

### Critical Severity Issues (17)

The 17 critical issues are dominated by error handling gaps and data integrity risks:

**Error Handling -- BRA (8 critical issues):** Across all BRA chunks (BRA_0 through BRA_7), the vast majority of jobs have zero error handling. REST API failures, XML/JSON parse errors, BigQuery write failures, and DELETE operations all go undetected. The most critical extraction jobs (salesInvoiceHeaders, salesInvoiceLines, valueEntries) have no mechanism to detect or alert on failures.

**Data Integrity -- BRA (3 critical issues):** The DELETE-then-INSERT pattern creates windows where the global Sales_Set is empty. The write-back pattern in VBR_Update_* jobs can overwrite production order records with incorrect cost values. Silent pagination termination means partial extractions appear successful.

**Business Continuity -- BRA (1 critical issue):** salesInvoiceHeaders and salesInvoiceLines are the single source of truth for the Sales_Set pipeline. Failed extraction breaks the entire sales reporting chain with no fallback.

**Central Point of Failure -- GENERIC (1 critical issue):** All Business Central regions depend on shared GEN_BC_* extraction jobs. A bug affects every dependent region simultaneously.

**Cross-System Dependency -- Global (2 critical issues):** Salesforce_Account_Mappings and Salesforce_Mapped_Accounts are critical reference tables. Data quality issues cascade across the entire reporting layer.

**Configuration -- BRA (1 critical issue):** Multiple jobs use hardcoded dataset references locked to the test environment.

**Incomplete Pipeline -- ZAF (1 critical issue):** South Africa lacks clean-layer processing jobs. Raw data cannot be transformed into the Sales_Set without manual intervention.

### High Severity Issues (49) -- Key Themes

- **Data Integrity (8):** TRUNCATE write mode risks, DELETE-then-INSERT orphan records, silent pagination termination masking API errors.
- **Code Hygiene (7):** Duplicate/backup job variants creating maintenance confusion. Up to 4 variants of a single job (e.g., VBR_Update_CBR_ProductionOrders with BACKUP, DEBUG, DEBUG_2).
- **Security (5):** API credentials stored in flat files on the execution server with no encryption at rest across all 23 BRA raw extraction jobs.
- **Operational (3):** CBR jobs requiring specific execution order without orchestration enforcement.
- **OAuth Token Management (1):** GENERIC Create_Business_Central_Token -- app registration expiry breaks all BC extractions.
- **API Rate Limiting (2):** Simultaneous GEN_BC_* jobs could exhaust Business Central rate limits.
- **Data Quality (3):** Persistent typo 'Produtct_Description' propagated from MSSQL through BigQuery to Salesforce. Default template filter code left uncustomized.
- **Single Points of Failure (3):** MSSQL connectivity for ITA, JPN; VJP_Clean_Sales_Set_Delete_Ingest as sole Sales_Set lifecycle manager.
- **Dual-Entity Complexity (1):** DEU maintaining separate VDA and VDD job families doubles maintenance and risks schema drift.
- **ERP Migration Risk (1):** DEU during VDD-to-VDA migration may have data in both systems, risking duplication.

### Medium Severity Issues (78) -- Key Themes

Medium issues span multiple categories:

- **Authentication (4):** Salesforce deprecating Basic auth in favor of OAuth 2.0 across multiple Global jobs.
- **API Dependencies (3):** Business Central API availability and rate limits could throttle or block extraction for JPN and USA.
- **Performance (5):** Extensive BigQuery reads (67 tBigQueryInput components), heavy aggregation (38 tBigQueryOutput, 16 tAggregateRow).
- **Data Freshness (2):** TRUNCATE-and-reload means any failure results in stale data until next successful run.
- **External Dependencies (1):** Animalytix SFTP file delivery timing affects USA pipeline.
- **Schema Evolution (2):** Business Central API updates may break fixed field paths in tExtractXMLField/tExtractJSONFields.
- **Hardcoded Configuration (1):** Fiscal year filter '2023/2024' hardcoded in Global budgeting query.
- **Version Proliferation:** Multiple job versions without clear lifecycle management across JPN, Global, and DEU.
- **Cross-Regional Dependencies:** Japan depends on US raw data for payment terms; GENERIC templates consumed by multiple regions.

### Low Severity Issues (15)

Low issues include development hygiene (test jobs in production repository, Copy_of_ prefixed jobs), legacy maintenance burdens, performance tuning opportunities (external sort buffers, full table scans), and missing integrations (DEU has no Salesforce product sync).

---

## Part 4: Change History & Change Management

### Current State: No Change History Available

The platform does not maintain a structured change log. When a production issue occurs, there is no quick way to determine what changed recently, who changed it, or how to roll it back.

### Indirect Change Indicators

**Version numbers in job names:** Jobs like VIT_Clean_Sales_Set_v6, Salesforce_Budget_v8, and NPL_Clean_Item_v26 encode version history. Version 26 implies at least 25 prior iterations, but the nature of changes is unrecorded. The versioning convention is inconsistent -- some use _v1/_v2, others use _0.1/_1.0, and still others have no version indicator.

**Backup and copy variants:** Approximately 40 jobs are identified as copies, backups, or debug variants. Each is a snapshot at a point in time, but without dates, commit messages, or authorship, they serve only as rollback targets.

**Git history:** If stored in Git, the commit log provides the most complete history available. However, Talend's XML-based project files produce large, difficult-to-read diffs. Git history may also suffer from batch commits, missing messages, and mixed functional/formatting changes.

**TAC logs:** Operational records (job X ran at time T with exit code 0) rather than change records.

### Recommended Change Log Standard

Every entry should answer five questions:

1. **What** changed? (Which jobs, configurations, or schemas?)
2. **Why** did it change? (Business requirement, bug fix, improvement?)
3. **Who** made the change?
4. **What is affected?** (Downstream jobs, reports, systems?)
5. **How to roll back?** (Steps to reverse the change)

### Change Log Template

```
## [YYYY-MM-DD] -- Brief Title of Change

**Author:** [Name or team]
**Ticket:** [Issue tracker reference, if applicable]
**Environment:** [PROD / TEST / both]
**Deployment date:** [Date deployed to production]

### Summary
[1-3 sentences describing what changed and why. Focus on business intent.]

### Affected Jobs
| Job Name | Region | Change Type |
|----------|--------|-------------|
| [job_name] | [region] | [added / modified / deleted / config change] |

### Technical Details
[Optional. Non-obvious logic changes, new join conditions, API endpoints, etc.]

### Testing
[How was this change validated? Environments tested? Regression tests?]

### Rollback Plan
[Specific steps to reverse this change:
- Which jobs to revert or redeploy
- Whether data needs reprocessing
- Whether database rollback is required
- Estimated time to execute
- Who to contact for manual intervention]

### Dependencies
[Other changes that must deploy alongside this one]
```

### Change Management Guidelines

- **Record changes contemporaneously** as part of pull requests, not after the fact
- **One entry per logical change**, not per file modified
- **Include configuration changes** -- updates to context variables (connection strings, API credentials, BigQuery dataset names) are often the most impactful
- **Flag breaking changes** prominently when output schemas or shared joblets are modified
- **Review during incident response** -- the change log should be the first diagnostic step
- **Archive periodically** to keep the active log focused on recent changes

### Automation Opportunities

- **CI/CD pipelines** auto-generating entries from pull request titles and descriptions
- **Database migration tools** (Alembic, Flyway, Liquibase) maintaining their own schema change logs
- **Deployment automation** recording which version was deployed where and when
- **Monitoring systems** appending entries when production incidents are detected and resolved

### Rollback Scenarios

Common rollback scenarios for this platform include:

- **Schema changes**: Revert pipeline code and re-run with old schema. If the target table was already altered, a schema migration rollback may be needed.
- **Data flow logic changes**: Revert code, delete output of the changed job (DELETE-then-INSERT), re-run with old logic.
- **Configuration changes**: Restore previous environment variable values and restart affected jobs.
- **Credential changes**: Restore previous credentials from the secrets vault and restart affected jobs.

---

## Consolidated Migration Takeaways

### Error Handling (Non-Negotiable)

1. **Implement try-catch at every job level.** Every migrated pipeline must wrap its core logic in structured error handling.
2. **Add centralized error logging.** Errors must be captured in a persistent, queryable store (logging database, Application Insights, CloudWatch, or equivalent).
3. **Build alerting for critical job failures.** At minimum, Global consolidation and regional Sales_Set jobs trigger immediate alerts. Distinguish transient failures (worth retrying) from persistent failures (requiring human intervention).
4. **Design retry logic for transient failures.** Configurable exponential backoff for network timeouts, API rate limits, and temporary unavailability.
5. **Eliminate the DELETE-then-INSERT anti-pattern.** Replace with MERGE/UPSERT operations or staging-table-swap patterns.
6. **Add data quality checks post-load.** Row counts, null checks, expected range validations.
7. **Establish error handling as a non-negotiable code review criterion.** CI checks where possible, peer review where not.
8. **Create an error handling dashboard.** Real-time view of running jobs, recent failures, retries, and SLA breaches.

### Scheduling & SLAs

9. **Audit the external scheduling configuration** before migration (TAC, cron, or orchestrator exports).
10. **Define SLAs before migration** with business stakeholders for at least four job tiers.
11. **Make implicit dependencies explicit** using sensor-based triggers (start clean job only after raw job's BigQuery table has been updated).
12. **Implement DAG-based orchestration** with time-based and event-based triggers, built-in retry logic, and SLA monitoring.
13. **Establish baseline metrics** (durations, success rates, row counts, peak concurrency) before cutover.
14. **Time zone awareness is essential.** Subsidiaries span UTC-8 to UTC+9. Schedule extraction windows to avoid source system peak hours.
15. **Establish a schedule change management process.** Version-controlled, reviewed, and tested schedule changes.

### Risk Management

16. **Prioritize the 17 critical issues** for immediate remediation or migration prioritization.
17. **Address security concerns** -- migrate flat-file credentials to a secrets vault.
18. **Resolve the ZAF incomplete pipeline** by building missing clean-layer processing jobs.
19. **Consolidate duplicate job variants** during migration to reduce the 40+ backup/copy/debug jobs.

### Change Management

20. **Integrate change logging with version control.** Every pipeline change gets a meaningful commit message and change log entry.
21. **Require rollback plans** for every production pipeline change.
22. **Automate where possible.** CI/CD pipelines generate entries from pull request descriptions.
23. **Review the change log during incident response** before diving into logs, metrics, or code diffs.

### Phased Error Handling Implementation

- **Phase 1 (SAF/ZAF proof of concept):** Level 2 (fail-fast) on 4 South African jobs.
- **Phase 2 (GENERIC templates):** Level 3 (structured handling) on 15 shared templates. Error handling in templates propagates to every consuming region.
- **Phase 3-5 (BRA, USA, JPN):** Level 3 for all jobs, Level 4 for Sales_Set builders and critical jobs.
- **Phase 6-7 (DEU, ITA, Global):** Level 3 standard, Level 4 for Global consolidation layer.

This phased approach means the platform's error handling posture improves incrementally throughout the 6-12 month migration, reducing risk at each phase rather than deferring all risk reduction to the final cutover.

---

*Generated by intodoc -- documentation automation for integration platforms.*
