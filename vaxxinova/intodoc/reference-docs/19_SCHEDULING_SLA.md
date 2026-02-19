# Scheduling & SLA

All 257 Talend jobs in the Vaxxinova integration platform are marked as "on-demand" in their metadata -- meaning the Talend project files contain no cron expressions, no schedule definitions, and no time-based trigger configurations. This is not unusual for Talend deployments: job design and job scheduling are handled by separate tools. The Talend Designer (or Open Studio) defines what a job does; the Talend Administration Center (TAC), an OS-level cron daemon, or an external orchestrator defines when it runs. The consequence for migration planning is significant: the Talend metadata tells us everything about the internal logic of each job but nothing about its production cadence, execution windows, or service-level expectations. That operational knowledge exists outside the codebase and must be gathered through interviews, TAC exports, or cron configuration audits before migration can proceed.

## What We Know from Talend Metadata

The analysis of 257 jobs across 10 regions reveals a structurally simple scheduling landscape at the metadata level:

**Every job is on-demand.** The frequency distribution is uniform: 257 out of 257 jobs report an on-demand trigger. No job contains an embedded timer, cron trigger, or scheduler component. This holds true across all regions -- from Brazil's 75 jobs to South Africa's single job.

**Dependency chains are shallow.** Only 10 jobs have any detectable dependency chain, and the maximum chain depth across the entire platform is 1. That single-depth chain belongs to the Global region, where `Global_Clean_Sales_Set` orchestrates 10 regional Sales_Set jobs (`BIO_Clean_Sales_Set`, `VIT_Clean_Sales_Set`, `VDD_Clean_Sales_Set`, `VJP_Clean_Sales_Set`, `VUS_Clean_Sales_Set`, `NPL_Clean_Sales_Set`, `VDA_Clean_Sales_Set`, `Legacy_Clean_Sales_Set`, `Forecast_Sales_Set`, `Raw_Sales_Set`). Every other region has a maximum chain depth of 0 -- meaning all jobs are designed to run independently without invoking other Talend jobs.

**One bottleneck job exists.** `Global_Clean_Sales_Set` is flagged as the only bottleneck in the dependency analysis. It sits at the apex of the Sales_Set consolidation pipeline, and its failure would block the construction of the global reporting dataset. All 10 regional Sales_Set jobs must complete before the global consolidation can produce correct results.

## What We Do Not Know

The Talend metadata is silent on several critical operational dimensions:

**Production schedule cadence.** Are these jobs running hourly, daily, weekly? The raw extraction jobs (e.g., `BRA_VBR_RAW`, `JPN_VJP_RAW`, `ITA_VIT_RAW`) likely run on a daily or more frequent schedule to keep the data warehouse current, while budget and forecast jobs (`Budget_Build_Sales_Set`, `Forecast_Sales_Set`) may run weekly or monthly. But the metadata cannot confirm this.

**Execution windows and ordering.** In a typical ETL platform, raw extraction runs first, followed by clean-layer transformation, then global consolidation. The Vaxxinova platform almost certainly follows this layered sequence (raw -> clean -> global), but the specific timing -- which jobs run at 2 AM vs. 6 AM, which must complete before others start -- is not captured in the Talend project files.

**Runtime dependencies beyond Talend.** Several jobs depend on external data availability: the US `VUS_Clean_Animalytix` pipeline depends on SFTP file delivery from the Animalytix third-party distributor; the GENERIC `GEN_BC_*` jobs depend on Business Central API availability and OAuth token freshness; the Salesforce sync jobs depend on CRM API rate limits. These external dependencies shape the real-world schedule but are invisible in the metadata.

**Retry and failure recovery behavior.** When a job fails at 3 AM, is it automatically retried? After how many attempts does it alert? What is the escalation path? The metadata shows that 89% of jobs have no error handling components, which makes the external scheduling layer's retry and alerting configuration even more critical -- and even more opaque.

**Historical execution data.** The Talend metadata captures no execution history: no run durations, no success/failure rates, no row counts processed per run. The `BigQuery_TEST` scratchpad job reads from `Talend_Metadata.TFLOWMETERCATCHER`, suggesting that flow meter data may be captured in BigQuery, but this is a monitoring artifact, not a scheduling configuration.

## Dependency Chain Analysis

The overwhelming majority of jobs are structurally independent. The dependency landscape breaks down as follows:

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

The zero-depth chains across 9 of 10 regions mean that, from Talend's perspective, each job can be scheduled and executed without waiting for any other Talend job to finish. In practice, however, there are implicit data dependencies that the metadata does not capture. For example:

- `BIO_Clean_Invoice_Set_v5` (BRA, clean layer) reads from BigQuery tables that are populated by `BRA_VBR_RAW` (BRA, raw layer). If the raw job has not run, the clean job will process stale data. But this dependency is expressed through shared BigQuery tables, not through Talend's job invocation mechanism.

- `Budget_Ingest_Sales_Set` (Global) reads from entity-specific `Sales_Set_Budget` tables that are written by `Budget_Build_Sales_Set`. Again, the dependency is implicit in the data lineage, not explicit in the Talend trigger configuration.

- The GENERIC `GEN_BC_*` template jobs extract raw data that downstream regional clean jobs consume. Regions like Germany (VDA) depend on the GENERIC extractors running first, but this ordering is managed externally.

These implicit dependencies mean the actual scheduling DAG (directed acyclic graph) is significantly more complex than the Talend metadata suggests. A realistic production DAG for the Vaxxinova platform would have at least three tiers: raw extraction -> clean transformation -> global consolidation, with regional jobs running in parallel within each tier.

## The SLA Gap

No service-level agreement definitions were found anywhere in the Talend metadata. This is expected -- Talend job XML files are not designed to carry SLA metadata -- but it represents a critical gap for migration planning. Without documented SLAs, the migration team cannot answer fundamental questions:

- **When must the global Sales_Set be refreshed each day?** If management dashboards are reviewed at 8 AM, the entire pipeline (raw -> clean -> global) must complete before that deadline. But what is the actual deadline?

- **What is the acceptable data latency?** Can the data warehouse be 24 hours behind the source ERPs, or do some business processes require near-real-time freshness? The incremental loading pattern in newer jobs (filtering by `lastModifiedDateTime >= parameter_date`) suggests the team has invested in reducing latency, but the target latency is not documented.

- **What is the recovery time objective?** If the `Global_Clean_Sales_Set` orchestrator fails on a Monday morning, how quickly must it be recovered? The existence of backup job variants (e.g., `VJP_Clean_Build_Sales_Set_Backup`) hints at a manual recovery strategy, but no formal RTO is defined.

- **Which jobs are business-critical vs. deferrable?** The 5 scratchpad jobs and the various "Copy_of_" and "Backup" variants are clearly non-critical. But among the 200+ production jobs, which ones drive time-sensitive business processes (e.g., Salesforce sync for the sales team's daily workflow) vs. which ones feed monthly reporting that can tolerate a day's delay?

These questions must be answered by the people who operate the platform, not by the code.

## The Documentation Gap -- What Information Is Needed

The absence of scheduling metadata in the Talend codebase creates a documentation gap that must be filled before migration can proceed responsibly. The following is an inventory of the specific information that the migration team must gather from operations staff, TAC administrators, and business stakeholders. Each item is something that cannot be inferred from code analysis alone.

### Operational Information Required from the Platform Team

**Job execution schedule per environment.** For each of the 257 jobs, the team must document: which environment(s) it runs in (PROD, TEST, TEST_LINUX, TEST_MAC, PROD_MAC), the cron expression or trigger condition, and the time zone. Jobs that run in multiple environments may have different schedules in each.

**Execution server assignments.** Talend TAC distributes job execution across one or more Talend JobServers. The migration team needs to know which server runs which jobs, because server-specific configurations (memory allocation, installed drivers, network access) may affect job behavior. If certain jobs require specific JDBC drivers or have been configured with custom JVM arguments, those settings live on the server, not in the job metadata.

**Inter-job ordering enforced by the scheduler.** Beyond the single explicit dependency (`Global_Clean_Sales_Set` orchestrating 10 child jobs), the scheduler almost certainly enforces additional ordering -- for example, ensuring all raw extraction jobs complete before clean-layer jobs start. The migration team needs the complete scheduling DAG as configured in TAC or the external orchestrator.

**Retry and failure handling configuration.** When a job fails, does the scheduler automatically retry? How many times? With what delay? Is there an escalation email or webhook? These settings are configured in TAC's task properties, not in the Talend job XML. They represent the platform's only line of defense against job failures, given that 89% of jobs have no internal error handling.

**Historical execution logs.** If TAC or the scheduler maintains execution history, the migration team should export at least 30 days of run data: start times, end times, durations, success/failure status, and any retry attempts. This data establishes performance baselines and identifies which jobs are most failure-prone.

### Business Information Required from Stakeholders

**Data freshness requirements per downstream consumer.** The global Sales_Set feeds management dashboards, but which dashboards? How often are they reviewed? If the CFO reviews the budget-vs-actual dashboard every Monday morning at 9 AM, then the entire pipeline (raw -> clean -> global) must complete before 9 AM on Mondays. Each downstream consumer may have different freshness requirements.

**Business-critical vs. deferrable job classification.** Of the 257 jobs, which ones support time-sensitive business processes? The Salesforce product sync jobs directly affect the sales team's ability to quote and sell. The raw extraction jobs feed reporting but may tolerate a 24-hour delay. The budget and forecast jobs may only be relevant at month-end. This classification drives SLA tiering.

**Notification preferences and escalation paths.** Who should be notified when a critical job fails? Is there a dedicated data operations team, or do failures escalate to individual developers? Different regions may have different on-call arrangements depending on local business hours and team structure.

**Regulatory and compliance deadlines.** Some data pipelines may feed regulatory reporting (financial statements, tax filings, audit trails). These have hard deadlines that cannot be missed, and the SLA for the underlying data pipeline must account for the end-to-end processing time plus a buffer for failure recovery.

## Recommended Schedule Design

Based on the platform's architecture, data dependencies, and regional distribution, the following schedule design recommendations provide a starting point for the migration target. These are suggestions to be validated and refined with the operations team, not prescriptions to be implemented blindly.

### BRA (Brazil) -- Suggested Schedule

Brazil's pipeline is the most complex, with 75 jobs spanning legacy MSSQL and Business Central extraction paths. The recommended approach is to split Brazil's schedule into two parallel tracks:

- **Legacy MSSQL track:** `BRA_VBR_RAW` and related legacy extraction jobs should run during Brazil's off-peak hours (e.g., 01:00-03:00 BRT / UTC-3) to minimize lock contention on the on-premises Biovet MSSQL server. Clean-layer jobs (`BIO_Clean_Invoice_Set_v5`, `BIO_Clean_Sales_Set`) should be scheduled with an explicit dependency on the raw extraction's completion.
- **Business Central API track:** The `GEN_BC_*` template jobs extracting from Brazil's Business Central instance can run independently of the MSSQL track. Business Central API rate limits (typically 600 calls per minute per tenant) should inform the schedule spacing between API-based extraction jobs.
- **Sales_Set construction:** `Copy_of_VBR_Clean_Build_Sales_Set` should be the final Brazil job, scheduled only after both extraction tracks and all reference data refreshes have completed. Given its 94-component complexity, allocate a generous execution window (45-60 minutes).

### USA (United States) -- Suggested Schedule

The USA region has 61 jobs including the Animalytix SFTP feed, which is externally dependent.

- **SFTP-dependent jobs:** `VUS_Clean_Animalytix` and related jobs should use a file-watch trigger rather than a fixed schedule. The Animalytix SFTP delivery time may vary, and a fixed-time schedule risks running before the file arrives.
- **NPL and VUS extraction:** Both entities extract from Business Central APIs and should run after midnight US Eastern time, with NPL and VUS extraction running in parallel (they target different API endpoints and BigQuery datasets).
- **Salesforce sync:** `VUS_Clean_BC_Item_v8_Salesforce` should run after the USA Sales_Set is complete, to ensure that product data pushed to Salesforce reflects the latest ERP state.

### JPN (Japan) -- Suggested Schedule

Japan's 36-job pipeline uses the monolithic `JPN_VJP_RAW` extraction, which creates a natural serialization point.

- **Raw extraction:** `JPN_VJP_RAW` should run during Japan's off-peak hours (e.g., 02:00-04:00 JST / UTC+9). Given that this single job extracts multiple entities sequentially, it may have a long execution time. The migration should consider decomposing it into parallel per-entity extractions.
- **Clean-layer processing:** Japan's clean jobs can begin immediately after `JPN_VJP_RAW` completes. The high tMap density (128 instances) suggests these are computationally intensive; allocate sufficient BigQuery slot capacity.
- **Time zone coordination with Global:** Japan Standard Time is 14-17 hours ahead of US time zones. If the Global consolidation job runs on a US schedule, Japan's pipeline must complete well before the Global window opens. This time zone gap actually works in Japan's favor -- Japan's night-time processing window naturally precedes the US business day.

### Global -- Suggested Schedule

The Global consolidation layer should run last, after all regional pipelines have completed.

- **Dependency gate:** The target platform should implement an explicit dependency gate: `Global_Build_Sales_Set` triggers only when all 10 regional Sales_Set tables have been updated within the current cycle. A BigQuery metadata check (`last_modified_time` on each regional Sales_Set table) can serve as the gate condition.
- **Budget and forecast jobs:** `Budget_Build_Sales_Set`, `Prior_Build_Sales_Set`, and `Forecast_Sales_Set` process financial planning data that changes less frequently. These can run on a weekly or bi-weekly cadence rather than daily, reducing unnecessary processing.
- **Completion deadline:** The Global consolidation should complete before the earliest business-hours start across all Vaxxinova regions. Given that Japan starts earliest (9 AM JST = midnight UTC), and Global consolidation needs the most recent data, a practical target is Global completion by 06:00 UTC.

### DEU, ITA, ZAF, SAF -- Suggested Schedule

The smaller regions have simpler scheduling needs.

- **DEU (Germany):** 12 jobs, dual-entity (VDA + VDD). Schedule VDA extraction (via GENERIC BC templates) first, then VDD legacy extraction, then clean-layer processing. Total pipeline should fit within a 2-hour window.
- **ITA (Italy):** 6 jobs including Salesforce sync. Run raw extraction first, then clean processing, then Salesforce product sync. The shared MSSQL connection with ZAF means Italy and ZAF should not run simultaneously to avoid connection pool contention.
- **ZAF and SAF:** 4 jobs total. Schedule after Italy completes (due to shared MSSQL connection). These are small enough to complete within 30 minutes.

## Recommendations

### 1. Audit the External Scheduling Configuration

Before migration, export the complete scheduling configuration from whatever system currently orchestrates job execution. This means:

- **If TAC is used:** Export the task and trigger definitions, including cron expressions, execution server assignments, and dependency configurations.
- **If cron is used:** Capture the crontab entries from all servers that run Talend jobs (note the multiple environment variants: PROD, TEST, TEST_LINUX, TEST_MAC, PROD_MAC).
- **If an external orchestrator is used:** Document the workflow definitions, including job ordering, parallelism settings, and failure handling rules.

This audit will produce the scheduling truth that the Talend metadata lacks.

### 2. Define SLAs Before Migration

Work with business stakeholders to define SLAs for at least four job tiers:

| Tier | Description | Example Jobs | Suggested SLA Approach |
|------|-------------|--------------|----------------------|
| **Critical** | Global consolidation, Salesforce sync | `Global_Clean_Sales_Set`, `BIO_Sync_Product_Salesforce_v1` | Hard deadline (e.g., complete by 7 AM); alerting within 15 minutes of failure |
| **High** | Regional Sales_Set builds | `Copy_of_VBR_Clean_Build_Sales_Set`, `VJP_Clean_Build_Sales_Set`, `VUS_Clean_Build_Sales_Set` | Complete before global consolidation trigger; retry 2x before alert |
| **Standard** | Raw extraction, reference data | `BRA_VBR_RAW`, `JPN_VJP_RAW`, `GEN_BC_*` | Daily freshness; alert on 2 consecutive failures |
| **Low** | Budget/forecast, one-off loads | `Budget_Build_Sales_Set`, `Prior_Build_Sales_Set` | Weekly or monthly cadence; alert only on persistent failure |

These SLAs should be encoded in the migration target's monitoring and alerting system, not left as tribal knowledge.

### 3. Make Implicit Dependencies Explicit

The migration target should express all data dependencies explicitly in its orchestration layer. The current Vaxxinova platform has two categories of dependencies:

- **The one explicit dependency:** `Global_Clean_Sales_Set` orchestrates 10 child jobs. This must be preserved as a parent-child relationship in the target DAG.
- **The many implicit dependencies:** Raw-to-clean and clean-to-global data flows create ordering requirements that are currently enforced by scheduling timing (e.g., raw runs at 2 AM, clean at 4 AM). In the target platform, these should be expressed as explicit DAG edges with sensor-based triggers (e.g., "start the clean job only after the raw job's BigQuery table has been updated").

Sensor-based triggering eliminates timing fragility. If the raw job takes longer than expected, the clean job waits rather than processing stale data.

### 4. Implement Scheduling in the Target Platform

The target platform should provide:

- **DAG-based orchestration** (e.g., Apache Airflow, Azure Data Factory pipelines, dbt Cloud jobs) that makes dependencies visible and enforceable
- **Time-based and event-based triggers** to support both scheduled runs and data-availability-driven execution
- **Built-in retry logic** with configurable backoff, given that 89% of current jobs have no error handling and rely entirely on the external scheduler for failure recovery
- **Execution history and duration tracking** to establish baseline performance metrics that the Talend metadata does not provide
- **SLA monitoring with alerting** that notifies stakeholders when jobs miss their deadlines, not just when they fail

### 5. Establish Baseline Metrics Before Cutover

Before migrating jobs to the new platform, capture at least two weeks of production execution data from the current environment:

- Job start times, end times, and durations
- Success/failure rates per job
- Row counts processed per run
- Peak concurrency (how many jobs run simultaneously)

These baselines will serve as acceptance criteria for the migrated platform: if the migrated `Copy_of_VBR_Clean_Build_Sales_Set` takes 45 minutes when it historically took 15 minutes, something needs investigation.

### GENERIC -- Suggested Schedule

The GENERIC shared templates are consumed by multiple regions and must complete before any dependent regional pipeline starts.

- **Template execution timing:** The `GEN_BC_*` jobs should run early in the cycle, before any region-specific clean-layer jobs. Since Germany, Brazil, and USA all depend on GENERIC Business Central extractors, these templates should complete before the earliest regional clean job triggers.
- **Parallelism within GENERIC:** The 15 GENERIC jobs extract from different Business Central entities (SalesInvoices, SalesCreditMemos, Items, Customers). They can run in parallel as long as API rate limits are respected. A concurrency limit of 3-4 simultaneous API extraction jobs should stay within Business Central's default rate limits while maximizing throughput.
- **Token refresh:** Business Central OAuth tokens have a limited lifetime (typically 60 minutes). If the GENERIC extraction cycle takes longer than the token lifetime, jobs later in the cycle will fail with authentication errors. The migration target should implement token refresh logic within long-running extraction pipelines.

## Migration Takeaways

1. **Scheduling is entirely external to the Talend codebase.** All 257 jobs are on-demand in metadata. The actual production schedule must be captured from TAC, cron, or whatever orchestrator is in use. This is a data-gathering task, not a code-analysis task.

2. **Dependency chains are deceptively shallow.** Only `Global_Clean_Sales_Set` has an explicit dependency chain (depth 1, orchestrating 10 jobs). But implicit data-level dependencies (raw -> clean -> global) create a three-tier execution graph that the scheduling layer must enforce. The migration target should make these implicit dependencies explicit.

3. **No SLAs exist in metadata.** Zero SLA definitions were found. This is expected for Talend project files, but it means the migration team must work with business stakeholders to define freshness requirements, completion deadlines, and recovery time objectives before the new platform can be properly configured.

4. **The `Global_Clean_Sales_Set` orchestrator is the single chokepoint.** As the only job that coordinates cross-region work, its failure blocks the entire global reporting pipeline. The migration target should add parallelism (run the 10 regional jobs concurrently) and explicit health checks before triggering consolidation.

5. **Error handling and scheduling are coupled.** With 89% of jobs lacking error handling, the current platform relies entirely on the scheduler to detect failures, retry, and alert. The migration target should decouple these concerns: add error handling within each job and configure retry/alerting at the orchestration layer.

6. **Time zone awareness is essential for a multinational platform.** The Vaxxinova platform serves subsidiaries in Brazil (UTC-3), the United States (UTC-5 to UTC-8), Germany (UTC+1), Italy (UTC+1), Japan (UTC+9), and South Africa (UTC+2). Scheduling must account for these time zones when defining extraction windows (avoiding source system peak hours), SLA deadlines (aligning with regional business hours), and cross-region dependencies (ensuring Japan's pipeline completes before the Global consolidation runs on a European or US schedule). The target platform's scheduler should store and display all schedules in UTC while allowing per-region local-time configuration.

7. **Establish a schedule change management process.** With 257 jobs scheduled externally and no scheduling metadata in Talend, the current platform likely relies on informal processes for schedule changes (an operator edits a crontab, an admin updates a TAC trigger). The migration target should formalize this: schedule changes should be version-controlled, reviewed, and tested before deployment, just like code changes. This prevents the scheduling configuration from drifting into an undocumented state -- the very problem that the current platform suffers from.

---

*Generated by intodoc -- documentation automation for integration platforms.*
