# Onboarding, Glossary & FAQ

This document consolidates the platform glossary, frequently asked questions, onboarding guides for new team members, and getting started procedures into a single reference. It serves as the starting point for anyone new to the platform and as a quick-reference resource for experienced operators and developers.

---

## Table of Contents

- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Platform Overview](#platform-overview)
  - [Key Concepts](#key-concepts)
  - [Your First Day Checklist](#your-first-day-checklist)
- [Onboarding Guide](#onboarding-guide)
  - [Learning Path Overview](#learning-path-overview)
  - [Week 1: Platform Orientation](#week-1-platform-orientation)
  - [Week 2: Regional Pipelines](#week-2-regional-pipelines)
  - [Week 3: Cross-Region and Global](#week-3-cross-region-and-global)
  - [Week 4: Operations and Maintenance](#week-4-operations-and-maintenance)
  - [Region-Specific Onboarding](#region-specific-onboarding)
- [Glossary](#glossary)
  - [Business Terms](#business-terms)
  - [Technical Terms](#technical-terms)
  - [Platform-Specific Terms](#platform-specific-terms)
  - [Region and Entity Codes](#region-and-entity-codes)
  - [Component Reference](#component-reference)
- [Frequently Asked Questions](#frequently-asked-questions)
  - [Architecture and Design](#architecture-and-design)
  - [Data Flow and Processing](#data-flow-and-processing)
  - [Source Systems and Extraction](#source-systems-and-extraction)
  - [Operations and Troubleshooting](#operations-and-troubleshooting)
  - [Migration and Modernization](#migration-and-modernization)
- [Migration Takeaways](#migration-takeaways)

---

## Getting Started

### Prerequisites

Before working with the integration platform, ensure you have:

- Access to the integration development environment (e.g., Talend Studio or Administration Console)
- Read access to the data warehouse project containing the analytics datasets
- Network connectivity (or VPN access) to on-premises database servers used for raw extraction
- CRM credentials (if working with sync jobs)
- Familiarity with ETL concepts: extraction, transformation, loading, and data warehousing

### Platform Overview

The integration platform connects multiple source systems to a centralized data warehouse and CRM:

```
Source Systems                 Integration Layer              Targets
--------------                 -----------------              -------
On-premises databases          Raw Extraction Jobs            Data Warehouse (raw layer)
Cloud ERP REST API             Clean Transformation Jobs      Data Warehouse (clean layer)
SFTP / File Sources            Output Builder Jobs            Data Warehouse (global layer)
                               CRM Sync Jobs                 CRM Platform
                               Global Consolidation Jobs      Consolidated reporting
```

Jobs are organized across regions. Each region represents a geographic subsidiary or a shared utility layer. Jobs flow through three layers:

1. **Raw Layer** -- Extracts data from source systems with minimal transformation
2. **Clean Layer** -- Transforms, validates, and enriches data with reference tables
3. **Global Layer** -- Consolidates regional outputs into enterprise-wide datasets

### Key Concepts

**Context Variables.** Every job uses context variables to determine which environment it targets (PROD, TEST, etc.) and which datasets it reads from and writes to. Always verify the active context before executing any job.

**Sales_Set.** The primary output table for each region. It contains unified sales data (invoices, credit memos, returns) enriched with fiscal periods, currency conversions, and CRM account mappings. The Global Sales_Set consolidates all regional Sales_Sets.

**TRUNCATE Mode.** Many jobs use TRUNCATE mode when writing to the data warehouse -- the target table is emptied before new data is written. This means a failed job can leave the table empty until the next successful run.

**Idempotent Execution.** All raw extraction and full-rebuild jobs are safe to re-run. They perform complete extracts that overwrite previous data, so re-execution does not create duplicates.

**Reference Data.** Several global reference tables (Fiscal_Periods, Country_Master, Conversion_Rate, CRM_Account_Mappings) are used by clean-layer jobs across all regions. These must be current before running transformation jobs.

### Your First Day Checklist

- [ ] Read the Operational Runbook (09_RUNBOOK.md) for operational procedures
- [ ] Identify which region(s) you will be working with
- [ ] Get credentials for the relevant source systems and data warehouse
- [ ] Review the architecture diagram for your assigned region (see Region-Specific Onboarding below)
- [ ] Run a TEST-context execution of a simple raw extraction job to verify connectivity
- [ ] Familiarize yourself with the Glossary section of this document for platform-specific terminology

---

## Onboarding Guide

### Learning Path Overview

New team members should follow this guided path to build understanding from the ground up. The path progresses from platform fundamentals through regional specifics to operational procedures.

| Week | Focus | Outcome |
|------|-------|---------|
| 1 | Platform orientation | Understand architecture, naming, environments |
| 2 | Regional pipelines | Trace data flow through 2-3 regions end-to-end |
| 3 | Cross-region and Global | Understand consolidation, shared templates, dependencies |
| 4 | Operations and maintenance | Run jobs, handle failures, use monitoring |

### Week 1: Platform Orientation

**Day 1-2: Architecture overview**
- Read the platform architecture documentation
- Understand the three-layer pattern: Raw -> Clean -> Global
- Learn the naming convention: `{Region}_{Subsidiary}_{Layer}_{Description}`
- Review the Glossary section of this document

**Day 3-4: Environment setup**
- Set up access to the integration development environment
- Set up access to the data warehouse
- Understand context variables and how environments are switched
- Run a simple test job in the TEST environment to verify connectivity

**Day 5: Data warehouse exploration**
- Browse the data warehouse datasets and understand the naming pattern
- Query a few key tables (Sales_Set, Fiscal_Periods, Country_Master)
- Understand the relationship between dataset names and job names

### Week 2: Regional Pipelines

**Day 1-2: Start with a small region**
- Pick a small, complete region (e.g., Italy or Germany) as the first deep dive
- Trace the pipeline from raw extraction through clean transformation to Sales_Set output
- Run each job in the TEST environment and verify the output

**Day 3-4: Move to a major region**
- Pick a major region (e.g., Brazil or USA) for the second deep dive
- Understand the additional complexity: multiple subsidiaries, CRM sync, legacy vs modern sources
- Compare the major region's pipeline structure with the small region's

**Day 5: Source system connections**
- Understand how on-premises database connections work (VPN, credentials, context variables)
- Understand how REST API extraction works (OAuth, pagination, JSON parsing)
- Review the connection inventory and which regions use which connections

### Week 3: Cross-Region and Global

**Day 1-2: Shared templates**
- Study the shared parameterized templates (e.g., GENERIC region)
- Understand how context parameters make one job serve multiple subsidiaries
- Run a template job with different context parameters

**Day 3-4: Global consolidation**
- Trace how regional Sales_Set outputs feed into the Global consolidation
- Understand the Budget, Forecast, and Prior-Year pipelines
- Run the Global consolidation job and verify the output

**Day 5: Cross-region patterns**
- Review the cross-region comparison matrix
- Understand which components are shared vs region-specific
- Identify the template reuse opportunities

### Week 4: Operations and Maintenance

**Day 1-2: Runbook familiarization**
- Read the Operational Runbook (09_RUNBOOK.md)
- Understand the pre-flight checklist
- Walk through each failure scenario and recovery procedure

**Day 3-4: Monitoring and alerting**
- Set up or review the monitoring dashboards
- Understand which jobs are critical and why
- Practice the escalation paths

**Day 5: Practice incident response**
- Simulate a common failure scenario (e.g., stale reference data, network outage)
- Follow the runbook to diagnose and recover
- Complete the post-incident review template

### Region-Specific Onboarding

For each region, the onboarding guide should cover:

**Architecture overview** -- a diagram showing the data flow from source to target.

**Key jobs to understand first** -- the 3-5 most important jobs in the region, in the order they should be learned.

**Environment setup** -- the context variables specific to this region and what they control.

**Common debugging steps** -- how to diagnose the most common issues in this region.

**Important caveats** -- known issues, inherited naming, hardcoded references, missing error handling.

Example structure for a region entry:

```
## {Region} ({subsidiary codes})

### Architecture
{Source System} --> {Raw Extraction Job} --> {Data Warehouse Raw}
                                                    |
                                                    v
                                         {Clean Transformation Job}
                                         {Sales_Set Builder Job}
                                                    |
                                                    v
                                         {Data Warehouse Clean / Global}
                                                    |
                                                    v
                                         {CRM Sync Job} --> {CRM}

### Key Jobs
1. {raw_extraction_job} -- Start here. Extracts from {source}.
2. {clean_transformation_job} -- Core transformation logic.
3. {sales_set_builder} -- Produces the Sales_Set output.
4. {crm_sync_job} -- Synchronizes data to CRM (if applicable).

### Environment Setup
- {context_variable_1}: {description}
- {context_variable_2}: {description}
- {context_variable_3}: {description}

### Common Debugging Steps
1. {issue}: {how_to_diagnose_and_fix}
2. {issue}: {how_to_diagnose_and_fix}

### Important Caveats
- {caveat_1}
- {caveat_2}
```

---

## Glossary

### Business Terms

Define key business concepts used across the platform. Each term should include a brief definition and, where relevant, how it is represented in the data model. Format:

**{Term}**
: {Definition. Include context about how the term is used in the platform, which regions or jobs reference it, and any nuances that a new team member should understand.}

Example terms to include:

**Absorption**
: The process of allocating overhead costs (indirect manufacturing costs like facility, utilities, equipment depreciation) to individual production orders based on routing operations.

**Account Mapping**
: The process of linking customer identifiers from different systems (ERP customer codes, CRM account IDs) to create a unified customer view.

**Budget**
: Annual sales targets entered by sales teams, broken down by account, product, fiscal period, and geography. Distinguished from Forecast (in-year updated projections).

**COGS (Cost of Goods Sold)**
: Total cost of manufacturing the products sold, calculated using standard cost prices and actual quantities -- a key profitability metric.

**Composite ID**
: A concatenated identifier (e.g., {Entity}-{docId}-{lineId}) that uniquely identifies each record across the global dataset by combining entity prefix, document ID, and line-level ID.

**Cost Breakdown Report (CBR)**
: An analytical framework that decomposes the total cost of a production order into direct materials (consumption), indirect costs (absorption), labor/machine time (routing), and variances.

**Delta Processing**
: An incremental data loading strategy where only changed records are processed. New/modified records are staged in a Delta table, matching records are deleted from the main table, and delta records are inserted.

**Fiscal Period**
: Custom fiscal calendar periods (may differ from calendar months), used for period-aligned reporting across all subsidiaries.

**Gross Margin**
: Revenue minus COGS, expressed as a percentage or absolute amount -- the primary profitability metric.

**Invoice Set**
: A standardized assembly of invoice headers, lines, and credit memos into a unified dataset that feeds the downstream sales-set aggregation pipeline.

**Sales Set**
: The primary clean-layer fact table combining sales invoices, credit memos, and reference data into a single unified dataset for reporting. Each region builds its own Sales_Set, which feeds into the Global consolidated version.

### Technical Terms

**Context Variable**
: Environment-specific parameter (e.g., connection strings, dataset prefixes) that varies between deployment environments (PROD, TEST). Enables the same job design to run in different environments.

**Context-Parameterized Template**
: A job design pattern where all region-specific values (URLs, dataset names, credentials) are externalized into context variables, allowing the same job to serve multiple regions.

**Clean Layer**
: The transformation tier in the data warehouse where raw data is joined, filtered, enriched, and standardized into business-ready datasets.

**Raw Layer**
: The extraction tier where source system data is landed with minimal transformation.

**Env_Prefix**
: A context variable used to prefix dataset names, enabling environment isolation (e.g., production prefix vs test prefix).

**External Sort**
: A sorting strategy that uses disk-based temporary files when data exceeds the configured memory buffer, preventing out-of-memory errors.

**Idempotent Operation**
: An operation that produces the same result regardless of how many times it is executed. Full-rebuild jobs are idempotent -- re-running them produces the same output without duplicates.

**TRUNCATE Mode**
: A write strategy that empties the target table before loading new data. Ensures no duplicates but creates a data availability gap during execution.

### Platform-Specific Terms

For Talend-based platforms, include terms like:

**cleanup_temp_file_storage**
: A custom component that removes temporary CSV files created during data warehouse loading. Temp files are created as an intermediate step and need cleanup after the load completes.

**Joblet**
: A reusable sub-job that can be embedded in multiple parent jobs. Used for shared logic like logging and cleanup.

**tMap**
: The core transformation component handling column mapping, joins, filtering, and expression evaluation.

**tHash (tHashInput/tHashOutput)**
: In-memory lookup components used to cache reference data (fiscal periods, currency rates) for efficient joins without repeated data warehouse round-trips.

For other platforms, include the equivalent platform-specific terminology.

### Region and Entity Codes

Document all region and subsidiary codes used in the platform:

| Code | Full Name | Type | Notes |
|------|-----------|------|-------|
| BRA | Brazil | Region | Contains VBR and BIO subsidiaries |
| VBR | Vaxxinova Brazil | Subsidiary | Business Central source |
| BIO | Biovet | Subsidiary | Legacy MSSQL source |
| JPN | Japan | Region | Contains VJP subsidiary |
| VJP | Vaxxinova Japan | Subsidiary | |
| USA | United States | Region | Contains VUS and NPL subsidiaries |
| VUS | Vaxxinova United States | Subsidiary | Business Central source |
| NPL | Newport Labs | Subsidiary | Legacy MSSQL source |
| DEU | Germany | Region | Contains VDA and VDD subsidiaries |
| VDA | Vaxxinova Germany Autogenous | Subsidiary | Business Central source |
| VDD | Vaxxinova Germany (legacy) | Subsidiary | Legacy ERP source |
| ITA | Italy | Region | Contains VIT subsidiary |
| VIT | Vaxxinova Italy | Subsidiary | |
| ZAF | South Africa | Region | Active development pipeline |
| SAF | South Africa (legacy folder) | Region | Single cloned template; consolidate into ZAF |
| Global | Cross-entity operations | Functional | Consolidation layer |
| GENERIC | Parameterized templates | Functional | Shared BC extraction jobs |
| scratchpad | Development/test | Functional | Not production; do not migrate |

### Component Reference

List the most frequently used components with their purpose:

| Component | Category | Description |
|-----------|----------|-------------|
| tMap | Transformation | Core transformation logic -- column mapping, joins, filtering, expressions |
| tBigQueryInput | Input | Reads data from BigQuery tables |
| tBigQueryOutput | Output | Writes data to BigQuery tables |
| tBigQuerySQLRow | SQL | Executes SQL statements against BigQuery |
| tHashInput/Output | Lookup | In-memory lookup tables for reference data |
| tUnite | Merge | Combines multiple data streams into a single flow |
| tFixedFlowInput | Input | Generates fixed reference rows for configuration injection |
| tPostjob | Lifecycle | Post-execution hook for cleanup and logging |
| tPrejob | Lifecycle | Pre-execution initialization |
| tJava/tJavaRow | Custom Code | Custom Java logic embedded in jobs |
| tMSSqlInput | Input | Direct MSSQL extraction (region-specific) |
| tRESTClient | API | REST API calls for Business Central extraction |
| tSalesforceOutput | Output | CRM data synchronization via Salesforce API |

---

## Frequently Asked Questions

### Architecture and Design

**Q1: Why are there two different pipeline jobs for the same output table in some regions?**

A: Different jobs serve different source systems. For example, one may process data from a legacy on-premises database while another processes data from a newer cloud ERP. Both output to the same target table, reflecting an ongoing migration from legacy ERP to cloud ERP. Check the source connections to determine which pipeline handles which data.

**Q2: What is the difference between "Build" and "Ingest" jobs?**

A: Build jobs transform raw data into the clean-layer output structure with enrichments like fiscal periods and currency conversions. Ingest jobs take that output and load it into the global reporting layer, performing a delete-and-replace operation. Build runs first, Ingest runs after.

**Q3: Why does the output table use TRUNCATE mode instead of incremental loading?**

A: TRUNCATE mode performs a full table replacement on each execution. This is simpler and ensures data consistency by avoiding duplicate or stale records. For datasets with complex joins across multiple reference tables, full refresh avoids the complexity of change detection logic. The tradeoff is reprocessing all data on every run.

**Q4: What are backup and debug job variants for?**

A: Backup jobs preserve a known-working version of the pipeline while developers iterate on the primary job. Debug variants add logging components for visibility into data flow during troubleshooting. Both should be deprecated once the primary version is stable.

**Q5: What does the GENERIC template pattern do?**

A: GENERIC jobs are parameterized templates that work across subsidiaries by varying context parameters (company identifier, producer name). One job definition serves multiple entities. This is the gold standard for reuse and should be extended to other job families during migration.

### Data Flow and Processing

**Q6: What reference data does the Sales_Set depend on?**

A: Typically: (1) Fiscal period mappings for fiscal year alignment, (2) Payment terms from the source ERP, (3) Shipment methods, (4) Currency conversion rates, (5) Country master for geographic standardization, (6) CRM account mappings for customer linkage.

**Q7: How does delta processing work?**

A: The job writes transformed data to a Delta staging table (with TRUNCATE mode). Then a SQL statement deletes records from the main table where the key matches any key in the Delta table. Finally, delta records are inserted into the main table. This achieves incremental updates without reprocessing the entire dataset.

**Q8: What happens if an Ingest job fails between the DELETE and INSERT steps?**

A: The output table would have records deleted but not replaced, creating a data gap. Since there is no error handling or transaction wrapping in most jobs, the table remains in this incomplete state until the job is manually re-run. This is a significant operational risk for downstream reporting. See the Runbook for recovery procedures.

**Q9: How do in-memory lookup tables work?**

A: A tFixedFlowInput initializes a data structure, tJava populates it with derived reference data, and tHashOutput stores it in memory. Later, tHashInput retrieves these lookups for use in tMap joins. This avoids repeated data warehouse reads for reference data like fiscal period mappings and currency conversions.

**Q10: Why are there hardcoded dataset references instead of context parameters in some jobs?**

A: Jobs with hardcoded dataset references (e.g., `test_bra_vbr_clean`) are locked to a specific environment and cannot be deployed to production without code changes. This typically indicates the job is still in development. During migration, all dataset references should be parameterized.

### Source Systems and Extraction

**Q11: How does Business Central data extraction work?**

A: Each extraction job calls a custom API codeunit on Business Central's endpoint. The call specifies a table number, field filter (with date-based filtering), and batch size. The response contains a data payload that is parsed into individual records. Pagination is handled by a loop that continues fetching until the end-of-records flag is true.

**Q12: Why is a custom API used instead of standard OData APIs?**

A: The custom API provides: (1) server-side pagination for large result sets, (2) field-level filtering, (3) table-number-based access that can extract from any table without pre-configured API pages, and (4) record count reporting. This is more flexible than standard APIs which require page-by-page configuration and have row limits.

**Q13: How are credentials managed for API access?**

A: Credentials are loaded from external files during the pre-job phase. Secret keys and environment-specific configuration (tenant ID, environment name, company name) are stored on the execution server at paths defined by context variables.

**Q14: What is the pagination loop pattern?**

A: A component checks the end-of-records flag from the API response. If false, it extracts the record position bookmark and passes it back for the next batch. If true (or if an exception occurs), it sets the position to null, terminating the loop. Note: error handling within the loop may silently catch API errors and treat them as end-of-records, which could mask extraction failures.

**Q15: Why do some raw extraction filters use dual-language conditions?**

A: In multinational deployments, the same ERP may return data in the local language. For example, filters may check for both the English term ("Output") and the local equivalent ("Saida" in Portuguese). This ensures the query captures records regardless of the language setting in the ERP system.

### Operations and Troubleshooting

**Q16: Is it safe to re-run any job?**

A: Yes. All full-extract and full-rebuild jobs are idempotent. Re-execution will not create duplicates. However, TRUNCATE-mode jobs will briefly empty the target table during rebuild, so do not re-run during active reporting windows.

**Q17: What is the correct job execution order?**

A: Raw extraction runs first, then clean-layer transformation, then output-builder/Sales_Set jobs, then global consolidation, then CRM sync. Within each layer, order generally does not matter unless there are explicit dependencies (e.g., invoice headers before invoice lines).

**Q18: How do I check if a job succeeded?**

A: Query the target data warehouse table for fresh data. Most jobs do not report success/failure status. Check table freshness timestamps and row counts against expected ranges.

**Q19: What should I do if reference data is missing?**

A: Verify the reference tables (fiscal periods, currency rates, country master) are populated for the relevant date range. Missing reference data causes silent enrichment failures -- output records will have NULL values in enriched fields.

**Q20: How do I switch between environments?**

A: Set the execution context to the desired environment (PROD, TEST, etc.). The environment prefix variable controls which datasets the job reads from and writes to. Always verify the context before execution. When in doubt, run with TEST first.

### Migration and Modernization

**Q21: Why should SELECT * queries be replaced?**

A: In columnar data warehouses (like BigQuery), SELECT * forces a full-table scan regardless of how many columns the transformation actually uses. This increases cost (charged by bytes scanned), latency, and memory pressure. Replacing with explicit column lists reduces costs by 40-70%.

**Q22: What is the recommended migration order for regions?**

A: Start with the smallest regions (SAF/ZAF) as proof of concept. Then migrate shared templates (GENERIC). Then major regions in order of size (BRA, USA, JPN). Then smaller regions (DEU, ITA). Then the Global consolidation layer (which depends on all regional outputs). Retire scratchpad last.

**Q23: Should backup and Copy_of_ jobs be migrated?**

A: No. Verify that the primary job is functioning correctly and covers all logic from the backups. Confirm no orchestration references the backup by name. Then exclude backups from migration scope.

**Q24: What patterns should be redesigned during migration?**

A: (1) TRUNCATE-then-load should become transactional swap or MERGE/UPSERT. (2) Copy-pasted code blocks should become shared libraries. (3) Hardcoded constants should become configuration parameters. (4) Implicit job ordering should become dependency-based orchestration. (5) Missing error handling should be added to every job.

**Q25: Should version numbers be preserved in migrated job names?**

A: No. The migration itself is a major version change. Reset version numbers to v1 or drop them entirely if the target platform provides native version control.

---

## Migration Takeaways

### Glossary and Knowledge Preservation

1. **Maintain the glossary as a living document.** New terms will emerge during migration. Update the glossary as the target platform introduces new concepts.

2. **Map legacy terms to target platform equivalents.** Create a translation table showing how platform-specific terms (e.g., tMap, joblet, context variable) map to the target platform's terminology.

3. **Preserve business-domain definitions.** Business terms (Sales_Set, Fiscal Period, CBR, Delta Processing) are platform-independent and should carry forward unchanged.

### FAQ and Knowledge Transfer

1. **Convert FAQ answers into runbook procedures.** Many FAQ items describe operational scenarios that should be formalized as runbook entries in the target platform.

2. **Update FAQ during migration.** Add entries covering migration-specific questions (how to validate migrated jobs, how to compare outputs, how to handle the transition period).

3. **Archive legacy FAQ after migration.** Keep the legacy FAQ available for reference but clearly mark it as historical.

### Onboarding Process

1. **Update the onboarding guide for the target platform.** The learning path structure (4 weeks, orientation -> regional -> cross-region -> operations) should be preserved, but content must reflect the new platform's tools and workflows.

2. **Create hands-on exercises.** Each onboarding week should include practical exercises where the new team member runs jobs, queries tables, and diagnoses simulated failures.

3. **Assign onboarding mentors.** Pair new team members with experienced operators who can provide context beyond what documentation covers.

---

*Generated by intodoc -- documentation automation for integration platforms.*
