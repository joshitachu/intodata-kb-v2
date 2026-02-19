# Error Handling

Of the 257 Talend jobs in the Vaxxinova integration platform, 229 (89%) have zero error handling. No `tLogCatcher` components, no `tDie` components, no `SUBJOB_ERROR` trigger flows -- nothing. When these jobs fail, they fail silently. Tables may be truncated and left empty, data may be partially written, and no one receives an alert. This is the platform's most critical technical debt and the single highest-risk area for migration planning. Any target platform must treat error handling not as a nice-to-have enhancement but as a foundational requirement that the current system entirely lacks.

## Rating Breakdown

Every job in the platform was evaluated for error handling maturity and assigned one of three ratings:

- **None (229 jobs, 89%):** No error handling components detected. The job has no mechanism to catch exceptions, log failures, or notify operators when something goes wrong.
- **Adequate (27 jobs, 11%):** Basic error logging is present, typically through `tLogRow` components that write diagnostic output to the console. These jobs can surface failures if someone is watching the logs, but they do not stop execution, send alerts, or implement recovery logic.
- **Good (1 job, <1%):** A single job in the entire codebase uses a `tDie` component, which actively terminates execution on error and returns a non-zero exit code. This is the only job that would reliably signal failure to an external orchestrator.

The distribution is stark: for every job that handles errors adequately, there are nearly nine that handle them not at all.

## What "No Error Handling" Means in Talend

In Talend's visual job design paradigm, error handling is not automatic. Unlike modern orchestration platforms that wrap every task in implicit try-catch blocks, Talend requires developers to explicitly add error-handling components and connect them via trigger flows. A job without these components will simply terminate on an unhandled exception -- or worse, continue executing downstream steps with corrupt or missing data.

The three primary error-handling mechanisms in Talend are:

- **tLogCatcher:** Captures Java exceptions, Talend warnings, and component-level errors into a structured log stream that can be routed to a file, database, or alerting system. Not a single production job in the Vaxxinova codebase uses `tLogCatcher`.

- **tDie:** Immediately terminates job execution with a custom error message and a non-zero return code. This enables external schedulers (cron, Talend TAC, cloud orchestrators) to detect that a job failed. Only one job in the entire platform uses `tDie`.

- **SUBJOB_ERROR triggers:** Talend's visual flow connectors include a `SUBJOB_ERROR` trigger type that fires when a subjob (a connected group of components) encounters an error. These triggers can route execution to cleanup logic, error logging, or compensating actions. SUBJOB_ERROR flows are rarely connected anywhere in this codebase.

The absence of all three mechanisms means that the vast majority of jobs operate in a "fire and forget" mode. If a database connection times out, an API returns a 500 error, or a data type mismatch causes a transformation to fail, the job either crashes with an unhelpful stack trace or -- in many cases -- continues processing with null or partial data.

## Region Comparison

Every region in the platform is rated "poor" for error handling. The table below shows the breakdown by region.

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

Three regions -- JPN (36 jobs), ITA (6 jobs), and ZAF (3 jobs) -- have absolutely no error handling across any of their jobs. Japan is the most concerning: it operates 36 jobs processing sales invoices, credit memos, and general ledger data for one of Vaxxinova's largest subsidiaries, and not a single one of those jobs can detect or report its own failure.

The Global region is nearly as exposed at 95% unhandled. These are the consolidation jobs that produce the unified Sales_Set for management dashboards and budget-vs-actual reporting. If an upstream regional extraction fails silently and the Global consolidation job runs on stale data, the resulting reports will be wrong with no indication that anything went amiss.

GENERIC has the best ratio at 67% unhandled, likely because its template jobs were designed with slightly more engineering discipline. But even here, 10 of 15 jobs have no error handling at all.

The weighted distribution is also instructive. If we weight each region by its job count, the four largest regions (BRA at 87%, USA at 89%, Global at 95%, JPN at 100%) account for 215 jobs, of which 196 have no error handling. In other words, the regions with the most jobs -- and therefore the most surface area for failure -- are among the least instrumented. The smaller, more mature regions (GENERIC at 67%, scratchpad at 60%) have better ratios but too few jobs to meaningfully improve the platform-wide average.

## The 27 "Adequate" Jobs: What They Do Differently

The 27 jobs rated "adequate" share a common pattern: they include one or more `tLogRow` components that write intermediate data or error indicators to the console during execution. The error-handling components used across these jobs are:

- **tLogRow_1** -- present in 25 of the 27 jobs (BRA: 10, USA: 6, GENERIC: 4, Global: 2, DEU: 1, scratchpad: 2)
- **tLogRow_2** -- present in 12 jobs as a secondary log output
- **tLogRow_3** and **tLogRow_4** -- present in 3 jobs with more verbose logging

It is important to note that `tLogRow` is a diagnostic component, not a true error handler. It writes data to standard output (the Talend console) without any filtering for error conditions. In practice, these jobs log every record they process, and an operator would need to manually scan the console output to identify anomalies. There is no conditional logic that distinguishes a successful record from a failed one, no threshold that triggers an alert, and no persistence of log data beyond the console session.

For example, BRA's jobs with adequate handling log transformation outputs during the Sales_Set construction, which can help a developer debug issues during interactive testing. But in a scheduled production run where no one is watching the console, these `tLogRow` outputs are written to a log file at best, or lost entirely at worst.

## The One "Good" Job

A single job in the scratchpad region earned a "good" rating: it is the only job in the entire platform that uses a `tDie` component. The `tDie_1` component causes the job to terminate immediately when an error condition is met, returning a non-zero exit code that external schedulers can detect.

This job also includes `tLogRow_1` and `tLogRow_2` for diagnostic output, making it the most instrumented job in the codebase. Its presence in the scratchpad region suggests it was a development experiment -- someone on the team understood how error handling should work and built a prototype, but the pattern was never propagated to production jobs.

The irony is pointed: the only job that properly handles errors is a test job that was never meant for production use.

## Why Error Handling Was Never Adopted

The platform's near-total absence of error handling is unlikely to be accidental. Several factors in the Talend development workflow and the Vaxxinova codebase's history help explain how 229 jobs came to be deployed without any error instrumentation.

**Talend's opt-in model.** Unlike platforms that provide error handling by default (Airflow wraps every task in a try-catch; Azure Data Factory logs every activity execution), Talend requires the developer to explicitly add error-handling components and wire them into the visual flow. This opt-in design places the burden on the developer, and under time pressure, error handling is the first thing omitted. The Talend Designer does not warn when a job lacks error handling, and there is no project-level policy that enforces it.

**Copy-based development.** The Vaxxinova codebase contains numerous "Copy_of_" and "Backup" variants of jobs, indicating that new jobs are often created by copying existing ones. If the original job lacked error handling, every copy inherits that gap. The copy-based workflow perpetuates the absence of error handling across the entire codebase, making it systemic rather than incidental.

**No code review or quality gate.** There is no evidence in the Talend metadata of a code review process, a deployment checklist, or an automated quality gate that would catch missing error handling before a job reaches production. In a mature engineering workflow, a pull request reviewer or a CI pipeline would flag the absence of error-handling components. The Vaxxinova platform appears to lack both.

**Incremental growth without architectural oversight.** The platform grew from a small set of regional extraction jobs to 257 jobs across 10 regions. This growth appears to have been incremental -- adding jobs one at a time to meet immediate business needs -- without periodic architectural reviews that might have identified the error handling gap and mandated corrections. By the time the gap became systemic, the cost of retrofitting 229 jobs was prohibitive.

**The scratchpad prototype was never evangelized.** The single "good" job in scratchpad demonstrates that at least one team member understood error handling best practices. But there is no evidence that this prototype was shared with other developers, documented as a recommended pattern, or proposed as a standard for production jobs. Institutional knowledge about error handling remained siloed in the scratchpad.

## Concrete Examples of At-Risk Jobs

The following examples illustrate the real-world risk of operating 229 jobs without error handling:

**BIO_Clean_Invoice_Set_v5 (BRA):** This job reads raw sales and returns data from BigQuery, enriches it with fiscal periods and Salesforce country mappings, and writes a consolidated Sales_Set to the clean layer. It uses a TRUNCATE write mode, meaning it deletes all existing data before writing new records. If the job fails after truncation but before the write completes, the Sales_Set table is left empty. With no error handling, no alert is sent, and downstream jobs (including Global consolidation) will process an empty table as if it were valid data.

**VBR_Raw_Customers (BRA):** This job extracts customer records from the on-premises MSSQL database. If the MSSQL server is unreachable (network issue, maintenance window, credential expiration), the job fails silently. The raw customer table in BigQuery retains its last-loaded data, which grows progressively stale. Clean-layer jobs that join against this table will continue producing output, but with outdated customer information -- a silent data quality degradation.

**Legacy_Ingest_Sales_Set (Global):** As a Global consolidation job, this one aggregates regional sales data into the platform's canonical reporting table. It executes DELETE-then-INSERT logic: first removing existing records that match incoming delta IDs, then writing new ones. A failure between DELETE and INSERT leaves a gap in the global Sales_Set. Without error handling, this gap persists until someone notices missing data in a dashboard -- which could be hours, days, or never.

**VJP_Clean_Ingest_Sales_Set (JPN):** Japan's entire 36-job pipeline lacks error handling, but the ingest job is particularly risky because it is the final step that publishes cleaned data to the Global_Clean dataset. Any upstream corruption propagates silently through the entire Japan pipeline and into global reporting.

**NPL_Clean_Account_v4 (USA):** This job cleans Newport Labs legacy customer account data. At version 4, it has been iterated on multiple times, yet no version has added error handling. Each revision was an opportunity to introduce basic error logging, and each was missed.

## Error Handling by Region -- Detailed Analysis

While every region rates "poor" overall, the nature and severity of the error handling gap varies by region. The following analysis examines each region's specific exposure, taking into account job count, pipeline criticality, and the particular failure modes that each region's architecture is vulnerable to.

### BRA (Brazil) -- 65 of 75 jobs unhandled (87%)

Brazil's error handling gap is the most consequential by volume. With 75 jobs spanning both a legacy MSSQL extraction pipeline (Biovet) and a newer Business Central REST API pipeline, Brazil has twice the surface area for failure as the next-largest region. The 10 jobs that do have adequate handling are concentrated in the Sales_Set construction phase, where `tLogRow` components log intermediate transformation results. However, the raw extraction layer -- which depends on VPN connectivity to the on-premises MSSQL server and OAuth token freshness for Business Central APIs -- has zero error handling. This means the most failure-prone phase of the pipeline (network-dependent source system extraction) is the least instrumented.

### USA (United States) -- 54 of 61 jobs unhandled (89%)

The USA region mirrors Brazil's pattern but adds a unique risk: the Animalytix third-party SFTP feed. The `VUS_Clean_Animalytix` pipeline depends on external file delivery from a distributor, yet none of the SFTP retrieval or staging jobs include error handling. If the SFTP server is unavailable, if the file format changes, or if the delivery is simply late, the pipeline fails silently. The 7 adequate jobs in USA use `tLogRow` at multiple verbosity levels (tLogRow_1 through tLogRow_4), indicating that the USA development team experimented more with diagnostic logging than other regions, but still fell short of true error recovery.

### Global -- 41 of 43 jobs unhandled (95%)

The Global region's 95% unhandled rate is alarming because these are the consolidation jobs that produce the unified reporting datasets consumed by management. Every Global job reads from regional clean-layer BigQuery tables; if any upstream regional extraction or transformation has failed silently, the Global consolidation propagates that silent failure into the final reporting layer. The two adequate jobs in Global use basic `tLogRow_1` logging, but even these do not validate data freshness or completeness before consolidating.

### JPN (Japan) -- 36 of 36 jobs unhandled (100%)

Japan's pipeline is the most exposed region on the platform. All 36 jobs operate with zero error handling, zero diagnostic logging, and zero failure detection capability. Japan's architecture compounds this risk through its monolithic `JPN_VJP_RAW` extraction job, which consolidates multiple entity extractions into a single job. If any entity extraction within that monolith fails, the entire job may continue with partial data, and downstream clean-layer jobs will process incomplete datasets without any indication that something is wrong. Japan also has the highest average component density (24.2 components per job), meaning each unhandled job has more internal points of failure.

### GENERIC -- 10 of 15 jobs unhandled (67%)

GENERIC is the best-instrumented region, though that distinction is relative. Its 5 adequate jobs are the shared Business Central API extraction templates (`GEN_BC_SalesInvoices`, `GEN_BC_SalesCreditMemos`, etc.), which include `tLogRow` components that log API response parsing results. The template design suggests the GENERIC team intended to build observable, reusable extractors. However, the logging remains console-only, with no persistence or alerting. Since GENERIC templates are consumed by other regions (particularly Germany), a failure in a GENERIC job can cascade across multiple downstream pipelines.

### DEU (Germany) -- 10 of 12 jobs unhandled (83%)

Germany's 12 jobs cover dual entities (VDA for Business Central, VDD for legacy ERP), and 10 of them have no error handling. The 2 adequate jobs include one `tLogRow_1` and one `tLogRow_2` instance -- minimal diagnostic output. Germany's primary risk is that its raw extraction depends on the GENERIC Business Central templates, creating a cross-region dependency chain where neither the template jobs nor the downstream Germany-specific jobs can detect or report failures.

### ITA (Italy) -- 6 of 6 jobs unhandled (100%)

Italy's small pipeline is fully unhandled. Its 6 jobs include raw extraction from MSSQL, clean-layer Sales_Set construction, and Salesforce product synchronization (`IZO_Sync_Product_Salesforce_v5`). The Salesforce sync job is particularly risky: if it fails mid-write, product records in the global Salesforce instance may be partially updated, leading to inconsistent product catalog data visible to the entire sales organization.

### ZAF and SAF (South Africa) -- 4 of 4 jobs unhandled (100%)

South Africa's combined 4 jobs across two regional designations (ZAF with 3 jobs, SAF with 1 job) are entirely unhandled. The interesting wrinkle is that ZAF shares the `connection_ITA_VAX_MSSQL` connection with Italy, meaning a connectivity failure at Italy's MSSQL server simultaneously affects both regions -- and neither region would detect or report it. SAF's single extraction job is the only one in the entire platform that uses `tMSSqlInput` directly rather than through a connection-based abstraction, making it structurally unique but equally unprotected.

### scratchpad -- 3 of 5 jobs unhandled (60%)

The scratchpad region is a development workspace, not a production pipeline, so its error handling gap is less operationally concerning. However, it is notable because it contains the only "good" rated job in the entire platform -- the sole job with a `tDie` component. This suggests that at least one developer understood and prototyped proper error handling, but the pattern was never adopted by production teams.

## The Cost of Silent Failures

The absence of error handling is not merely a technical shortcoming; it carries tangible business costs that compound over time. While precise financial impact depends on factors outside the codebase (revenue per subsidiary, reporting cadence, regulatory exposure), the structural risks identified in this analysis point to several categories of business impact.

**Incorrect management decisions based on stale or empty data.** The Global Sales_Set is the foundation for budget-vs-actual reporting across all Vaxxinova subsidiaries. When a regional extraction fails silently and the Global consolidation runs on stale data, the resulting variance analysis is wrong. If management makes resource allocation, pricing, or investment decisions based on these reports, the cost of the silent failure is measured not in engineering hours but in business outcomes. A single quarter of inaccurate budget-vs-actual reporting could lead to misallocated marketing spend, incorrect sales incentive calculations, or delayed responses to underperforming product lines.

**Salesforce CRM inconsistency affecting sales team productivity.** Three regions (BRA, ITA, USA) synchronize product master data to Salesforce. A silent failure in `BIO_Sync_Product_Salesforce_v1` or `IZO_Sync_Product_Salesforce_v5` could leave the Salesforce product catalog out of sync with the ERP system. Sales representatives relying on Salesforce for quoting and order entry would see incorrect product information -- wrong pricing, missing items, or stale availability data. The downstream impact includes lost sales, customer dissatisfaction, and manual reconciliation effort.

**Regulatory and audit risk from incomplete financial records.** Japan's 36-job pipeline processes general ledger entries and sales invoices without error handling. If GL data is partially loaded or stale, financial reports for the Japanese subsidiary may not reconcile with the source ERP system. In a regulated industry like veterinary pharmaceuticals, discrepancies in financial reporting can trigger audit findings, regulatory scrutiny, or delayed filings.

**Cumulative engineering time spent on reactive firefighting.** Without proactive error detection, the current team's failure-response workflow is entirely reactive: someone notices incorrect data in a report, traces it back through the pipeline, identifies the failed job, and manually reruns it. This reactive loop is time-consuming and error-prone. A conservative estimate of 2-4 hours per silent failure incident, multiplied by even a few incidents per month, represents a significant ongoing engineering cost that proper error handling would eliminate.

**Data trust erosion.** Perhaps the most insidious cost is the erosion of trust in the data platform itself. When stakeholders encounter incorrect data -- even once -- they begin to question every subsequent report. This leads to parallel manual data collection, shadow spreadsheets, and a gradual shift away from the centralized data warehouse. The entire purpose of the integration platform (providing a single source of truth for Vaxxinova's global operations) is undermined when silent failures make that source unreliable.

**Delayed detection compounds the damage.** The absence of alerting means that the time between a silent failure and its discovery is unbounded. In the best case, an analyst notices incorrect data in a morning dashboard review and escalates within hours. In the worst case, a budget-vs-actual variance is accepted as real (rather than recognized as a data artifact), and the error propagates into financial planning decisions that are not revisited for weeks or months. The longer the detection latency, the greater the blast radius of the original failure.

The cumulative business impact of these costs is difficult to quantify precisely, but the structural analysis leaves no doubt that it is material. Error handling is not an engineering nicety; it is a business continuity requirement.

## Quick Wins

Not every error handling improvement requires a full migration or platform overhaul. Several high-impact improvements could be implemented in the existing Talend codebase with relatively modest effort, providing immediate risk reduction while the longer-term migration is planned and executed.

**1. Add tLogCatcher to the 10 highest-risk jobs.** The five concrete at-risk jobs listed above (BIO_Clean_Invoice_Set_v5, VBR_Raw_Customers, Legacy_Ingest_Sales_Set, VJP_Clean_Ingest_Sales_Set, NPL_Clean_Account_v4) plus the five regional Sales_Set builder jobs should be retrofitted with a `tLogCatcher` component connected to a `tFileOutputDelimited` that writes error logs to a persistent file or BigQuery table. This single change would transform silent failures into detectable events. Estimated effort: 1-2 days per job, roughly 2-3 weeks for all 10.

**2. Add row-count validation after every TRUNCATE write.** Jobs that use TRUNCATE mode on `tBigQueryOutput` should add a post-write `tBigQuerySQLRow` step that checks `SELECT COUNT(*) FROM target_table`. If the count is zero (or below a configurable threshold), the job should write an error record to a monitoring table. This catches the most dangerous failure mode -- truncated tables left empty -- without requiring changes to the job's core logic. Estimated effort: half a day per job.

**3. Implement a simple monitoring query.** A scheduled BigQuery SQL query could check the `lastModifiedTime` of critical tables (all Sales_Set tables, all raw extraction targets). If any table has not been updated within its expected cadence (e.g., 26 hours for a daily job), the query flags it. This creates an external watchdog that detects stale data regardless of whether the Talend job itself has error handling. Estimated effort: 1-2 days total, as a single query covering all critical tables.

**4. Propagate the scratchpad tDie pattern to the Global orchestrator.** The `Global_Clean_Sales_Set` orchestrator job is the single most critical job on the platform. Adding a `tDie` component to this job would ensure that if the orchestration fails, the scheduler receives a non-zero exit code and can trigger an alert. Since the scratchpad already contains a working tDie prototype, the pattern can be copied directly. Estimated effort: half a day.

**5. Enable Talend's built-in stats and logs capture.** Talend jobs can be configured at the project level to write execution statistics to a database table (`TALEND_STAT` and `TALEND_LOG` tables). This feature requires minimal per-job changes -- it is a project-level setting that applies to all jobs. Enabling it would provide a persistent record of every job execution, including start time, end time, and basic error information. Estimated effort: 1 day for project-level configuration plus validation.

These five quick wins, totaling roughly 4-5 weeks of engineering effort, would not bring the platform to a modern standard of error handling. But they would dramatically reduce the risk of the most damaging silent failure scenarios while the broader migration is underway. The monitoring query alone (quick win 3) would catch the majority of stale-data incidents that currently go undetected.

## Silent Failure Risks

The combination of no error handling and several common Talend patterns in this codebase creates specific categories of silent failure:

**Truncated tables left empty.** Multiple jobs use TRUNCATE or DELETE-then-INSERT patterns for idempotent reloads. If the reload fails after the truncation, the target table is empty. Downstream jobs see zero records and process them without complaint, producing empty or misleading reports.

**Partial writes without rollback.** Talend's `tBigQueryOutput` does not operate within a database transaction in the traditional sense. A job that writes 100,000 records may fail after 50,000, leaving the table in an inconsistent state. Without error handling, there is no mechanism to detect the partial write, roll it back, or flag the table as dirty.

**Stale data masquerading as current.** When extraction jobs fail silently, the raw layer retains its previous data. Clean and global jobs process this stale data and produce output that looks normal but reflects an earlier point in time. This is perhaps the most insidious risk because the data is structurally correct -- it just is not current.

**No alerting, no escalation.** Even if a job's failure is logged to a file somewhere, there is no mechanism in the Talend job definitions to send an email, post to a Slack channel, or trigger a PagerDuty incident. The team's current failure-detection strategy appears to rely entirely on noticing incorrect data in downstream reports -- a reactive approach with potentially long detection latency.

## Error Handling Maturity Model

To provide a framework for measuring improvement -- both during the quick-win phase and throughout the migration -- the following maturity model defines five levels of error handling capability. The current Vaxxinova platform sits firmly at Level 0 for 89% of its jobs and Level 1 for the remaining 11%.

**Level 0: No Handling (229 jobs today).** The job has no error detection, logging, or recovery mechanism. Failures are invisible to operators and may corrupt downstream data. This is the current state of nearly the entire platform.

**Level 1: Diagnostic Logging (27 jobs today).** The job writes diagnostic output via tLogRow or equivalent, but does not distinguish between success and failure cases. An operator who actively monitors the console might notice anomalies, but there is no automated alerting or execution termination. This is the "adequate" rating in the current assessment.

**Level 2: Fail-Fast with Signaling (1 job today).** The job uses tDie or equivalent to terminate on error and return a non-zero exit code. The external scheduler can detect the failure and trigger retry or alerting logic. This is the minimum acceptable standard for production workloads.

**Level 3: Structured Error Handling.** The job implements tLogCatcher (or equivalent) to capture exceptions into a structured log, routes errors to a persistent store, and executes compensating actions (e.g., rolling back partial writes, sending an alert, marking the target table as stale). No job in the current platform achieves this level.

**Level 4: Self-Healing with Observability.** The job implements retry logic for transient failures, circuit-breaker patterns for persistent failures, data quality validation post-load, and publishes structured metrics (execution duration, row counts, error counts) to an observability platform. The migration target should aim for Level 4 as the standard for all critical jobs.

The migration should establish Level 2 as the absolute floor (every migrated job must at minimum signal its own failure) and Level 3 as the target for all jobs classified as "critical" or "high" in the SLA tier. Level 4 should be pursued for the Global consolidation layer and the regional Sales_Set builders.

## The Relationship Between Error Handling and Data Patterns

The error handling gap does not exist in isolation; it interacts with specific data handling patterns used across the platform to amplify risk. Understanding these interactions clarifies why error handling is not merely a "nice to have" but a structural requirement.

**TRUNCATE + No Error Handling = Catastrophic Data Loss Risk.** When a job uses TRUNCATE write mode, it deletes all existing data before writing new records. If the job then fails (for any reason: network timeout, memory overflow, API rate limit), the target table is left empty. With no error handling to detect the failure, no alert to notify operators, and no rollback to restore the previous data, the empty table persists until someone manually notices. In the worst case, downstream consolidation jobs process the empty table and produce reports showing zero sales for an entire region.

**In-Memory Lookups + No Error Handling = Silent Join Failures.** The tHash/tFixedFlowInput pattern loads reference data into JVM memory. If the reference data source is unavailable or returns zero rows, the hash table is empty. Subsequent tMap joins against the empty hash table produce null values in enrichment columns (fiscal period, country, conversion rate). With no error handling to validate that the hash table loaded correctly, these null values propagate silently through the pipeline. The downstream Sales_Set contains records with missing fiscal periods and zero-value currency conversions -- structurally valid data that is substantively wrong.

**API Pagination + No Error Handling = Truncated Extractions.** The Business Central API extraction jobs use pagination to retrieve large datasets across multiple API calls. If a pagination call fails mid-sequence (OAuth token expires, rate limit hit, network interruption), the extraction stops with only a partial dataset loaded. With no error handling, the partial load is treated as a complete load by downstream jobs. The result is missing records -- not just stale data, but records that exist in the source system but were never extracted.

## Migration Takeaways

The near-total absence of error handling in the current platform is not just a code quality issue; it is a design constraint for the migration target. The new platform must compensate for years of accumulated risk.

1. **Implement try-catch at every job level.** Every migrated pipeline should wrap its core logic in structured error handling. In most modern platforms (Airflow, Azure Data Factory, dbt, Prefect), this is a default capability rather than an opt-in feature. The migration should enforce it as a non-negotiable standard.

2. **Add centralized error logging.** Errors should be captured in a persistent, queryable store -- a logging database, Application Insights, CloudWatch, or equivalent. This replaces the current pattern of ephemeral console output with a durable record of every job execution, including start time, end time, records processed, and any errors encountered.

3. **Build alerting for critical job failures.** The migration must establish notification channels (email, Slack, Microsoft Teams, PagerDuty) for critical job failures. At minimum, the 43 Global consolidation jobs and the regional Sales_Set construction jobs should trigger immediate alerts on failure. The alerting should distinguish between transient failures (worth retrying) and persistent failures (requiring human intervention).

4. **Design retry logic for transient failures.** Network timeouts, API rate limits, and temporary database unavailability are common in a platform that connects to on-premises MSSQL servers, Business Central APIs, and Salesforce. The migration target should implement configurable retry policies (exponential backoff, maximum retry count) for these predictable failure modes.

5. **Eliminate the DELETE-then-INSERT anti-pattern.** The current idempotent reload strategy -- delete existing records, then insert new ones -- creates a window of data unavailability and risks leaving tables empty on failure. The migration should replace this with MERGE/UPSERT operations or staging-table-swap patterns that maintain data availability throughout the load process.

6. **Add data quality checks post-load.** Even with proper error handling, jobs should validate their output: row counts should be non-zero, key columns should not be null, and record counts should be within expected ranges. These checks are entirely absent in the current platform and represent a second layer of defense against silent data corruption.

7. **Establish error handling as a non-negotiable code review criterion.** The migration is an opportunity to prevent the error handling gap from recurring in the new platform. Every pipeline submitted for deployment should pass a review checklist that includes: structured error handling is present, failure signals a non-zero exit code, errors are logged to a persistent store, critical failures trigger alerts, and data quality checks validate output. This criterion should be enforced through automated CI checks where possible and through peer review where automation is not feasible.

8. **Create an error handling dashboard.** The migration target should include a centralized dashboard that provides a real-time view of: which jobs are currently running, which have failed in the last 24 hours, which have been retried and how many times, and which have breached their SLA deadlines. This dashboard replaces the current approach of "nobody knows until someone sees bad data" with a proactive operational posture. Tools like Grafana (for Airflow/Prefect), Azure Monitor (for ADF), or Datadog can serve this purpose.

## Error Handling in the Context of Migration Phasing

The error handling improvements should not be deferred to the end of the migration. Instead, they should be implemented from the very first migrated job. Each phase of the migration presents an opportunity to demonstrate the value of proper error handling.

**Phase 1 (SAF/ZAF proof of concept):** Implement basic error handling (Level 2: fail-fast with signaling) on the 4 South African jobs. This establishes the pattern and validates the target platform's error handling capabilities.

**Phase 2 (GENERIC templates):** Implement Level 3 (structured error handling) on the 15 shared Business Central extraction templates. Since these templates are consumed by multiple regions, error handling in the templates propagates to every region that uses them.

**Phase 3-5 (Major regions: BRA, USA, JPN):** Implement Level 3 for all jobs, with Level 4 (self-healing with observability) for the Sales_Set builders and any job classified as "critical" in the SLA tier.

**Phase 6-7 (DEU, ITA, Global):** By this phase, the error handling patterns are well-established. Apply the standard Level 3 treatment, with Level 4 for the Global consolidation layer.

This phased approach means that even if the migration takes 6-12 months, the platform's error handling posture improves incrementally throughout, reducing risk at each phase rather than deferring all risk reduction to the final cutover.

---

*Generated by intodoc -- documentation automation for integration platforms.*
