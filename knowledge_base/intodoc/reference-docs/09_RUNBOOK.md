# Operational Runbook

This document is the operational guide for managing the Vaxxinova Talend integration platform. It covers pre-flight checks, common failure scenarios, monitoring recommendations, recovery procedures, and escalation paths. The platform comprises 257 on-demand jobs across 10 regions, connecting on-premises MSSQL databases, Google BigQuery, Salesforce CRM, and Business Central APIs. Because 89% of jobs (229 of 257) have no error handling whatsoever, operators must rely on external monitoring, manual verification, and disciplined procedures to detect and recover from failures that the jobs themselves will silently ignore.

---

## Table of Contents

- [Pre-Flight Checks](#pre-flight-checks)
  - [Context Variable Environment](#1-context-variable-environment)
  - [Network Connectivity](#2-network-connectivity)
  - [Reference Data Currency](#3-reference-data-currency)
  - [Temp File Storage](#4-temp-file-storage)
- [Common Failure Scenarios](#common-failure-scenarios)
  - [Scenario 1: Sales_Set Truncated but Not Repopulated](#scenario-1-sales_set-truncated-but-not-repopulated)
  - [Scenario 2: MSSQL Connection Failure](#scenario-2-mssql-connection-failure)
  - [Scenario 3: BigQuery Quota Exceeded](#scenario-3-bigquery-quota-exceeded)
  - [Scenario 4: Salesforce API Rate Limits](#scenario-4-salesforce-api-rate-limits)
  - [Scenario 5: Temp File Accumulation](#scenario-5-temp-file-accumulation)
- [Monitoring Recommendations](#monitoring-recommendations)
  - [What to Monitor Externally](#what-to-monitor-externally)
  - [Priority Monitoring Targets](#priority-monitoring-targets)
- [Recovery Procedures for Critical Jobs](#recovery-procedures-for-critical-jobs)
  - [Full Regional Sales_Set Recovery](#full-regional-sales_set-recovery)
  - [Full Global Recovery](#full-global-recovery)
  - [Raw Extraction Recovery](#raw-extraction-recovery)
- [Escalation Paths](#escalation-paths)
- [Environment-Specific Notes](#environment-specific-notes)
  - [PROD vs TEST Context Variable Differences](#prod-vs-test-context-variable-differences)
  - [Environment Matrix](#environment-matrix)
- [Post-Incident Review Template](#post-incident-review-template)
- [Appendix: Quick Reference Card](#appendix-quick-reference-card)

---

## Pre-Flight Checks

Before executing any job -- whether for a scheduled production run, an ad-hoc data refresh, or post-incident recovery -- verify the following.

### 1. Context Variable Environment

Every job uses Talend context variables to determine which environment it targets. The platform supports seven environments: Default, PROD, PROD_MAC, TEST, TEST_LINUX, TEST_MAC, and _default. Of the 957 context variables in the platform, 38% (363 variables) have values that differ between PROD and TEST.

**Verify the active context before every execution.** A job launched with the wrong context will read from or write to the wrong BigQuery dataset. For example:

- `Global_Clean.parameter_Env_Prefix` resolves to `Global_Clean` in PROD but `test_global_clean` in TEST. Running a Global consolidation job with TEST context against production data will write results into the test dataset, and downstream consumers will see stale production data.
- `connection_USA_NPL_MSSQL.connection_USA_NPL_MSSQL_Database` points to `BI360DW` in PROD but `BI360DW102622` in TEST. A test run against the production database will not corrupt data (the job reads, not writes, from MSSQL), but it will consume production database resources.
- Salesforce OAuth endpoints differ: PROD uses `https://login.salesforce.com`, TEST uses `https://test.salesforce.com`. A Salesforce sync job running with mismatched context will fail at authentication.

**Checklist:**
- [ ] Confirm the Talend execution context matches the intended environment (PROD, TEST, etc.)
- [ ] For Global jobs, verify that `parameter_Env_Prefix` resolves to the correct BigQuery dataset
- [ ] For Salesforce sync jobs, confirm OAuth audience URL matches the target org

### 2. Network Connectivity

Five on-premises MSSQL connections serve as source systems for regional raw extraction:

| Connection | Region(s) | Host Variable |
|------------|-----------|---------------|
| connection_BRA_VBR_MSSQL | BRA | context.connection_BRA_VBR_MSSQL_Server |
| connection_USA_NPL_MSSQL | USA | context.connection_USA_NPL_MSSQL_Server |
| connection_JPN_VJP_MSSQL | JPN, scratchpad | context.connection_JPN_VJP_MSSQL_Server |
| connection_ITA_VAX_MSSQL | ITA, SAF, ZAF | context.connection_ITA_VAX_MSSQL_Server |
| connection_SAF_VSA_MSSQL | ZAF | context.connection_SAF_VSA_MSSQL_Server |

**Before running any raw extraction job**, verify that the Talend server can reach the relevant MSSQL host on port 1433 (or the configured port). On-premises network outages, VPN tunnel failures, and firewall rule changes are the most common causes of raw extraction failures.

**Checklist:**
- [ ] Test TCP connectivity to each MSSQL server from the Talend execution host
- [ ] Verify BigQuery service account credentials are valid and not expired
- [ ] For Business Central API jobs (GENERIC region), confirm Azure AD OAuth token generation is working
- [ ] For Salesforce sync jobs, verify that the OAuth JWT flow certificate has not expired

### 3. Reference Data Currency

Many clean-layer jobs enrich transactional data with reference tables: fiscal periods, country master, currency conversion rates, and Salesforce account mappings. If reference data is stale, the enrichment will silently produce incorrect results (there are no freshness checks in any job).

**Checklist:**
- [ ] Confirm fiscal period reference table covers the current reporting period
- [ ] Verify currency conversion rates are up to date
- [ ] Check that Salesforce country mappings reflect any recent organizational changes

### 4. Temp File Storage

The platform generates 555 side effects across all regions, of which 364 are staging files and 191 are temp files. All staging files write to paths constructed from `context.resource_flow_temp_folder`. Before a batch run, verify that:

- [ ] The temp folder exists and is writable
- [ ] Sufficient disk space is available (the staging pattern writes full CSV extracts before BigQuery load)
- [ ] No stale files from a previous failed run remain that could cause confusion or name collisions

---

## Common Failure Scenarios

### Scenario 1: Sales_Set Truncated but Not Repopulated

**Symptom:** A regional or global `Sales_Set` table in BigQuery is empty or contains partial data. Downstream reports show zero revenue.

**Root cause:** Several jobs use a TRUNCATE-then-load pattern: the target table is emptied before new data is written. If the job fails after truncation but before the load completes, the table is left empty. Because 89% of jobs lack error handling, there is no automatic rollback or alert.

**Affected jobs (examples):**
- `VBR_Clean_Build_Sales_Set` (BRA) -- truncates and rebuilds the Brazil Sales_Set
- `VUS_Clean_Build_Sales_Set` (USA) -- truncates and rebuilds the US Sales_Set
- `Global_Build_Sales_Set` (Global) -- truncates the consolidated global Sales_Set

**Resolution steps:**
1. Identify which Sales_Set table is empty by querying BigQuery: `SELECT COUNT(*) FROM [dataset].Sales_Set`
2. Check the Talend execution log for the job that populates that table. Look for connection errors, BigQuery quota messages, or Java exceptions.
3. Re-run the failed job with the correct context. The job is designed to fully rebuild the table from upstream sources, so re-execution is idempotent (it will truncate again, then load).
4. After successful completion, verify row counts match expected ranges. Compare against the previous day's count if available.
5. If the Global Sales_Set was affected, re-run `Global_Build_Sales_Set` after confirming all regional Sales_Sets are populated.

**Prevention:** In the target platform, replace TRUNCATE-then-load with a transactional pattern: load into a staging table, validate, then swap. Alternatively, use BigQuery's WRITE_APPEND mode with a deduplication step.

### Scenario 2: MSSQL Connection Failure

**Symptom:** Raw extraction job fails immediately at startup. No data is written to BigQuery raw-layer tables. If running as part of a batch, downstream clean-layer jobs will process stale data.

**Root cause:** On-premises MSSQL servers are accessed over VPN or private network connections. Common causes include:
- VPN tunnel down between cloud execution environment and on-premises datacenter
- MSSQL Server instance restarted for maintenance
- Firewall rule change blocking port 1433
- MSSQL credentials expired or rotated

**Affected connections:**
- BRA: `connection_BRA_VBR_MSSQL` (1 job depends on this)
- USA: `connection_USA_NPL_MSSQL` (1 job depends on this)
- JPN: `connection_JPN_VJP_MSSQL` (3 jobs depend on this)
- ITA/SAF/ZAF: `connection_ITA_VAX_MSSQL` (2 jobs across 3 regions depend on this)

**Resolution steps:**
1. Test TCP connectivity from the Talend host to the MSSQL server and port.
2. If the network path is down, engage the network/infrastructure team to restore VPN or private connectivity.
3. If the server is reachable but authentication fails, verify credentials in the Talend context variables. Credentials are stored as context variables (paths filtered to `[CREDENTIAL_PATH]` in documentation).
4. Once connectivity is restored, re-run the raw extraction job. These jobs are safe to re-run because they perform full extracts (not incremental), so they will overwrite stale data in the raw layer.
5. After raw extraction completes, trigger the corresponding clean-layer and Sales_Set jobs to refresh downstream data.

**Prevention:** Implement connection health checks that run before each batch. Alert on failures before the main pipeline starts.

### Scenario 3: BigQuery Quota Exceeded

**Symptom:** Jobs fail with BigQuery API errors mentioning quota limits, rate limits, or resource exceeded messages. Partial data may have been written.

**Root cause:** Google BigQuery enforces project-level quotas on concurrent queries, data loading, and API calls. The Vaxxinova platform has 35 jobs that directly interact with BigQuery (via tBigQueryInput/tBigQueryOutput), and running many regional jobs in parallel can exceed these limits.

**Resolution steps:**
1. Check the Google Cloud Console for the BigQuery project's quota usage dashboard.
2. Identify which quota was exceeded (query slots, load jobs per table, API requests per minute).
3. If the issue is concurrent load jobs, stagger job execution. Avoid running all regional raw-to-clean pipelines simultaneously.
4. If the issue is query slots, consider requesting a quota increase from Google Cloud, or optimize the most expensive queries (Global consolidation jobs tend to scan the most data).
5. Re-run failed jobs after a brief cooldown period (5-10 minutes for rate limits, longer for daily quotas).

**Prevention:** Implement rate-limiting in the job scheduler. Schedule regional jobs in waves rather than all at once. Monitor BigQuery slot utilization proactively.

### Scenario 4: Salesforce API Rate Limits

**Symptom:** Salesforce sync jobs (product upsert, account mapping) fail or complete with partial results. Salesforce returns HTTP 429 or EXCEEDED_RATE_LIMIT errors.

**Root cause:** Salesforce enforces API call limits per 24-hour period (varies by edition and license count). The platform's Salesforce sync jobs -- such as `BIO_Sync_Product_Salesforce_v1` (BRA) and `IZO_Sync_Product_Salesforce_v5` (ITA) -- perform bulk upserts against the Product2 object. Running multiple sync jobs concurrently or re-running after a failure can approach or exceed these limits.

**Resolution steps:**
1. Check the Salesforce API usage dashboard (Setup > Company Information > API Requests, Last 24 Hours).
2. If the limit is approaching, delay re-execution until the 24-hour window resets.
3. If the sync partially completed, determine which records were successfully upserted. Salesforce sync jobs do not track progress, so you may need to compare BigQuery source records against Salesforce using the external ID field.
4. Re-run the sync job after the rate limit resets. The jobs use upsert operations, so re-execution is safe -- existing records will be updated, missing records will be inserted.

**Prevention:** Schedule Salesforce sync jobs during off-peak hours (evenings/weekends relative to the Salesforce org's timezone). Batch records to minimize API calls. Monitor daily API usage and set alerts at 80% of the limit.

### Scenario 5: Temp File Accumulation

**Symptom:** Disk space on the Talend execution host fills up. Jobs fail with I/O errors when attempting to write staging CSV files.

**Root cause:** The platform writes 555 staging and temp files across all regions. Of these, 119 effects (21%) lack any cleanup logic. The three largest regions -- BRA (184 side effects), USA (132 side effects), and JPN (84 side effects) -- do not use the `cleanup_temp_file_storage` joblet, meaning their temp files persist indefinitely. BRA alone has 85 medium-risk side effects without cleanup.

**Concrete examples of accumulating files:**
- `Copy_of_VBR_Clean_Build_Sales_Set` (BRA): writes to `context.resource_flow_temp_folder + "/" + context.parameter_VBR_Prefix + context.parameter_BC_Company_Identifie...` -- no cleanup
- `VUS_Clean_Build_Sales_Set` (USA): writes to `context.resource_flow_temp_folder + "/" + context.parameter_Env_Prefix + "Sales_Set" + ".csv"` -- no cleanup
- `VBR_Clean_Production_Order_Register` (BRA): writes to `context.resource_flow_temp_folder + "/" + "test_bra_vbr_clean_Production_Order_Register.csv"` -- no cleanup

**Resolution steps:**
1. Check disk usage on the Talend execution host: `df -h` (Linux) or check the relevant drive on Windows/Mac.
2. Identify old temp files in the `resource_flow_temp_folder` directory. Files older than the most recent successful run are safe to delete.
3. Remove stale files manually or via a scheduled cleanup script.
4. Re-run any jobs that failed due to disk space errors.

**Prevention:** Implement a cron job or scheduled task that purges files older than 7 days from the temp folder. In the target platform, replace file-based staging with in-memory or cloud storage patterns (e.g., GCS with lifecycle policies).

---

## Monitoring Recommendations

The platform's near-total absence of error handling (229 of 257 jobs, or 89%, have none) means that failures are silent by default. Only 28 jobs have any form of error awareness, and even those rely on tLogRow for logging rather than tLogCatcher for structured error capture. No SUBJOB_ERROR flows are connected in the majority of the codebase.

### What to Monitor Externally

Since the jobs themselves will not report failures, monitoring must be implemented outside of Talend:

**1. Job Execution Completion**
- Track job start and end times in the external scheduler (TAC, cron).
- Alert if a job does not complete within its expected window. Establish baselines by observing typical run times over several weeks.
- Alert if a job does not start at its expected time.

**2. BigQuery Table Freshness**
- For each critical table (especially `Sales_Set` tables per region and the `Global_Clean.Sales_Set`), monitor the `last_modified_time` metadata.
- Alert if any Sales_Set table has not been updated within the expected refresh interval.
- Query: `SELECT table_id, TIMESTAMP_MILLIS(last_modified_time) FROM [dataset].__TABLES__ WHERE table_id = 'Sales_Set'`

**3. BigQuery Row Counts**
- Establish expected row count ranges for critical tables.
- Alert on anomalies: zero rows (truncation without repopulation), dramatic drops (partial load), or unexpected growth (duplicate loads).

**4. Disk Space on Execution Host**
- Monitor the `resource_flow_temp_folder` volume.
- Alert at 80% capacity.

**5. MSSQL Connectivity**
- Implement a lightweight health-check script that tests TCP connectivity to all five MSSQL servers every 5 minutes.
- Alert immediately on connectivity loss.

**6. Salesforce API Usage**
- Poll Salesforce API usage via the REST API (`/services/data/vXX.0/limits/`) daily.
- Alert at 80% of the daily limit.

### Priority Monitoring Targets

Not all 257 jobs are equally critical. Focus monitoring on:

| Priority | Job(s) | Region | Reason |
|----------|--------|--------|--------|
| Critical | Global_Build_Sales_Set | Global | Consolidates all regional data; single point of failure for global reporting |
| Critical | BIO_Clean_Invoice_Set_v5 | BRA | Core Brazil sales pipeline; no error handling |
| Critical | VUS_Clean_Build_Sales_Set | USA | Core US sales pipeline; TRUNCATE mode risk |
| High | VJP_Clean_Ingest_Sales_Set | JPN | Core Japan sales pipeline; no error handling |
| High | Budget_Build_Sales_Set | Global | Budget/forecast data; affects financial planning |
| High | BRA_VBR_RAW, NPL_Clean_Account_v4 | BRA, USA | Raw extraction; upstream dependency for everything else |

---

## Recovery Procedures for Critical Jobs

### Full Regional Sales_Set Recovery

When a region's Sales_Set is corrupted or empty:

1. **Verify raw data is intact.** Query the BigQuery raw-layer dataset (e.g., `bra_vbr_raw`) to confirm source data is present. If raw data is also missing, re-run the raw extraction job first.
2. **Re-run the clean-layer job** that builds the Sales_Set (e.g., `BIO_Clean_Invoice_Set_v5` for BRA, `VIT_Clean_Sales_Set_v6` for ITA). These jobs perform full rebuilds.
3. **Verify the output.** Compare row counts and sample records against expectations.
4. **Re-run the Global consolidation.** If the regional Sales_Set feeds into Global, trigger `Global_Build_Sales_Set` after the regional rebuild completes.
5. **Notify downstream consumers** that data has been refreshed and any reports generated during the outage should be re-run.

### Full Global Recovery

When the Global Sales_Set is compromised but regional data is intact:

1. **Verify all regional Sales_Sets are current.** Query each region's clean-layer dataset.
2. **Re-run `Global_Build_Sales_Set`.** This is the central orchestration job and rebuilds from regional inputs.
3. **Re-run budget and forecast jobs** if the affected period includes planning data: `Budget_Build_Sales_Set`, `Forecast_Sales_Set`, `Prior_Build_Sales_Set`.
4. **Verify consolidated output.** Cross-check total revenue against the sum of regional Sales_Sets.

### Raw Extraction Recovery

When an MSSQL source extraction fails:

1. **Restore connectivity** (see Scenario 2 above).
2. **Re-run the raw extraction job** (e.g., `BRA_VBR_RAW`, `ITA_VIT_RAW`, `DEU_VDD_RAW`). These perform full extracts and overwrite the raw layer.
3. **Cascade downstream.** After raw extraction, re-run the corresponding clean-layer and Sales_Set jobs in sequence.

---

## Escalation Paths

### Tier 1: Operator (Self-Service)

The operator can resolve:
- Temp file cleanup
- Re-running failed jobs after transient errors (network blip, quota reset)
- Verifying context variables and correcting environment mismatches
- Checking BigQuery table freshness and row counts

### Tier 2: Data Engineering

Escalate to data engineering when:
- A job fails repeatedly with the same error after re-execution
- BigQuery schema changes are needed (column additions, type changes)
- Reference data (fiscal periods, currency rates) needs to be updated in BigQuery
- Salesforce sync produces data quality issues visible to end users
- Context variable values need to be changed (e.g., new MSSQL server address after infrastructure migration)

### Tier 3: Infrastructure / Network

Escalate to infrastructure when:
- MSSQL connectivity cannot be restored from the Talend side (VPN, firewall, server down)
- BigQuery project-level quotas need permanent increases
- Disk space issues require storage provisioning
- Azure AD or Salesforce OAuth certificate renewal

### Tier 4: Business Stakeholders

Notify business stakeholders when:
- Global Sales_Set was empty or incorrect during a reporting period
- Budget or forecast data was affected during a planning cycle
- Salesforce product data was out of sync for more than 24 hours
- A data quality issue is discovered that may have affected past reports

---

## Environment-Specific Notes

### PROD vs. TEST Context Variable Differences

The platform uses context variables to switch between environments. Key differences to be aware of:

**BigQuery Datasets:**
- PROD datasets use production prefixes: `bra_vbr_raw`, `Global_Clean`, `jpn_vjp_clean`
- TEST datasets use test prefixes: `test_bra_vbr_raw`, `test_global_clean`, `test_jpn_vjp_clean`
- Caution: `TEST_LINUX` sometimes uses PROD-style prefixes (e.g., `Global_Clean.parameter_Env_Prefix` = `Global_Clean` in both PROD and TEST_LINUX). This means TEST_LINUX may write to production datasets. Treat TEST_LINUX with the same caution as PROD.

**MSSQL Databases:**
- PROD and TEST sometimes point to different database instances on the same server. For example, USA PROD connects to `BI360DW` while TEST connects to `BI360DW102622`.

**Salesforce Endpoints:**
- PROD: `https://login.salesforce.com` (production org)
- TEST: `https://test.salesforce.com` (sandbox org)
- PROD_MAC: Same as PROD (`https://login.salesforce.com`)

**General Rule:** Always verify the context before execution. When in doubt, run with TEST first, verify results in the test BigQuery dataset, then switch to PROD.

### Environment Matrix

| Environment | Typical Use | BigQuery Target | Salesforce Target | Risk Level |
|-------------|-------------|----------------|-------------------|------------|
| PROD | Scheduled production runs | Production datasets | Production org | High |
| PROD_MAC | Production on macOS execution host | Production datasets | Production org | High |
| TEST | Development and validation | Test datasets | Sandbox org | Low |
| TEST_LINUX | Linux-based testing | Mixed (some PROD datasets) | Sandbox org | Medium |
| TEST_MAC | macOS-based testing | Test datasets | Sandbox org | Low |

---

## Post-Incident Review Template

After any incident that affects data quality, pipeline availability, or business reporting, complete the following review.

### Incident Summary

- **Date/Time of Detection:**
- **Date/Time of Resolution:**
- **Duration of Impact:**
- **Affected Region(s):**
- **Affected Job(s):**
- **Affected Table(s):**
- **Business Impact:** (e.g., "Global Sales_Set was empty for 4 hours; morning management dashboard showed zero revenue")

### Root Cause

- **Failure type:** (connection failure / quota exceeded / context mismatch / data quality / disk space / unknown)
- **Underlying cause:** (describe the specific technical cause)
- **Why was it not caught earlier?** (reference the 89% of jobs with no error handling; identify which monitoring gap allowed the failure to go undetected)

### Timeline

| Time | Event |
|------|-------|
| | Failure occurred |
| | Failure detected (by whom/what) |
| | Investigation started |
| | Root cause identified |
| | Recovery action taken |
| | Recovery verified |
| | Stakeholders notified |

### Resolution

- **Actions taken:** (describe the specific steps to resolve)
- **Jobs re-run:** (list all jobs that were re-executed)
- **Data verified:** (describe how data integrity was confirmed post-recovery)

### Prevention

- **Could this have been prevented?** (yes/no, explain)
- **Monitoring improvement needed?** (describe any new monitoring to add)
- **Error handling improvement needed?** (identify which jobs should gain error handling in the next sprint)
- **Documentation update needed?** (update this runbook if the scenario is not already covered)
- **Process change needed?** (e.g., new pre-flight check, changed run order)

### Follow-Up Actions

| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| | | | |
| | | | |
| | | | |

---

## Appendix: Quick Reference Card

**To check if a job succeeded:** Query the target BigQuery table for fresh data. The jobs themselves will not tell you.

**To re-run any job safely:** All jobs are idempotent (full extract/full rebuild). Re-execution will not create duplicates, but TRUNCATE-mode jobs will briefly empty the target table during rebuild.

**To check current environment:** Look at the Talend execution context parameter. The `parameter_Env_Prefix` variable is the most reliable indicator of which datasets the job will target.

**To check MSSQL connectivity:** `telnet [server] 1433` or `Test-NetConnection -ComputerName [server] -Port 1433` (PowerShell).

**To check BigQuery table freshness:** `SELECT table_id, TIMESTAMP_MILLIS(last_modified_time) FROM [dataset].__TABLES__`

**To check Salesforce API usage:** `GET /services/data/v58.0/limits/` with a valid session token.

**Critical job to watch:** `Global_Build_Sales_Set` -- if this fails, all consolidated reporting is affected.

*Generated by intodoc -- documentation automation for integration platforms.*
