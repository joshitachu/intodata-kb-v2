# Project Overview and Component Inventory

> Vaxxinova Talend Integration Platform -- Scope, Structure, and Component Census

## Executive Summary

This documentation covers Vaxxinova's Talend-based data integration platform, which manages the ETL pipelines connecting on-premises ERP systems, Google BigQuery (the central data warehouse), and Salesforce CRM across multiple global subsidiaries. The platform processes sales, product, customer, and financial data for veterinary vaccine operations spanning Brazil, Germany, Italy, Japan, the United States, and South Africa.

The platform comprises **257 Talend jobs** containing a combined **4,528 component instances** organized across **10 regions**: BRA, DEU, GENERIC, Global, ITA, JPN, SAF, USA, ZAF, scratchpad.

---

## Regional Breakdown

| Region | Jobs | Components | Avg/Job | Description |
|--------|------|------------|---------|-------------|
| BRA | 75 | 1,592 | 22.4 | Brazil -- Biovet/VBR subsidiaries, legacy MSSQL + Business Central |
| USA | 61 | 1,126 | 18.5 | United States -- NPL + VUS subsidiaries |
| Global | 43 | 476 | 11.3 | Cross-region consolidation, budget, forecast pipelines |
| JPN | 36 | 872 | 24.2 | Japan -- VJP subsidiary, largest APAC presence |
| GENERIC | 15 | 211 | 14.1 | Shared utilities, reference data loaders |
| DEU | 12 | 117 | 9.8 | Germany -- VDA subsidiary |
| ITA | 6 | 66 | 11.0 | Italy -- VIT subsidiary |
| scratchpad | 5 | 32 | 6.4 | Development/test jobs |
| ZAF | 3 | 30 | 10.0 | South Africa -- additional ZAF entity |
| SAF | 1 | 6 | 6.0 | South Africa -- VSA subsidiary |

---

## Platform Architecture

The Vaxxinova integration platform follows a three-layer architecture:

1. **Raw Layer**: Source system data is extracted from on-premises MSSQL databases, Business Central APIs, Salesforce, and flat files, then landed in BigQuery datasets with a `raw` prefix (e.g., `bra_vbr_raw`). Jobs in this layer perform minimal transformation -- mostly column mapping and CSV staging.

2. **Clean Layer**: Raw data is enriched with reference data (fiscal periods, country master, currency conversion rates), deduplicated, and joined into business-ready datasets. The primary output is the `Sales_Set` table, which consolidates invoice and credit memo data across all subsidiaries.

3. **Global Layer**: Regional clean datasets are consolidated into global reporting tables. Budget, forecast, and prior-year data are processed separately and merged with actuals for management dashboards. Salesforce CRM is synced with product and account master data.

### Key Technology Stack

- **ETL Engine**: Talend Open Studio / Data Integration
- **Data Warehouse**: Google BigQuery
- **Source Systems**: Microsoft SQL Server (on-prem ERPs), Microsoft Dynamics 365 Business Central, Salesforce CRM
- **Staging**: Google Cloud Storage (temp CSV files)
- **Orchestration**: Talend context variables for environment management (PROD, TEST, TEST_LINUX, TEST_MAC)

---

## Component Landscape

Three component types account for roughly a third of the entire inventory, and they tell the story of every core ETL job in the platform:

- **tBigQueryInput** (619 instances, 14% of all components) reads from the BigQuery data warehouse -- both raw-layer tables written by upstream ingestion jobs and clean-layer tables produced by transformation jobs.
- **tMap** (565 instances, 12%) is the universal transformation workhorse. Nearly every job routes data through at least one tMap for field mapping, filtering, type coercion, and lookup joins.
- **tBigQueryOutput** (360 instances, 8%) writes transformed records back into BigQuery, typically into clean-layer or global-layer datasets.

Together these three types form the canonical **read-transform-write** spine that runs through the vast majority of jobs. The remaining component types fall into several functional categories:

**In-memory lookup and staging.** tHashOutput (269 instances) and tHashInput (found heavily in BRA and USA jobs) implement in-memory hash tables that allow jobs to pre-load reference data -- customer lists, dimension sets, item masters -- and join it inside tMap without issuing additional BigQuery queries. For example, `BIO_Clean_Invoice_Set_v5` loads customer and item reference data via tHashOutput/tHashInput pairs before joining them to invoice lines in a tMap.

**Job lifecycle management.** Three custom or semi-standard components appear in nearly every production job and together enforce a consistent operational framework:
- `Vaxxinova_Logging` (193 instances across 9 regions) is a custom joblet that records execution metadata -- start time, end time, row counts, and status -- to a central logging table.
- `cleanup_temp_file_storage` (191 instances) handles post-execution cleanup of temporary files written to local disk during processing.
- `tPostjob` (191 instances) and its counterpart `tPrejob` define the lifecycle triggers that bracket the main data flow.

**Custom Java.** tJava (180 instances) is used for logic that cannot be expressed declaratively in tMap -- date arithmetic, custom string parsing, and conditional branching. Brazil (83 instances) and Japan (44 instances) are the heaviest users, reflecting the more complex business logic in those regional ERP integrations.

**SQL execution.** tBigQuerySQLRow (180 instances) runs ad-hoc SQL statements against BigQuery, typically for DELETE-before-INSERT patterns, schema mutations, or aggregation queries that are easier expressed in SQL than in tMap.

**Fixed-flow inputs.** tFixedFlowInput (228 instances) injects hardcoded or context-variable-derived values into a flow, most commonly to supply region codes, run timestamps, or version identifiers that get stamped onto every output row.

---

## Component Categories

Grouping the 4,528 component instances into functional categories reveals the platform's architectural priorities:

| Category | Instances | % of Total | Key Components |
|----------|-----------|-----------|----------------|
| Input | 1,070 | 24% | tBigQueryInput (619), tFixedFlowInput (228), tHashInput (176+) |
| Transformation | 745 | 16% | tMap (565), tJava (180) |
| Output | 716 | 16% | tBigQueryOutput (360), tHashOutput (269), tBigQuerySQLRow (partial) |
| Control | 575 | 13% | tPrejob (191), tPostjob (191), cleanup_temp_file_storage (191) |
| Infrastructure | 193 | 4% | Vaxxinova_Logging (193) |
| Other/Mixed | 1,229 | 27% | tBigQuerySQLRow (partial), tExtractJSONFields, tFileInputDelimited, etc. |

### Input Components (1,070 instances, 24%)

Input components read data from external systems and feed it into the transformation pipeline:

- **tBigQueryInput** (619) -- the dominant input, reading from the BigQuery data warehouse across raw, clean, and global layers.
- **tFixedFlowInput** (228) -- injects hardcoded or context-variable-derived values (region codes, timestamps, version identifiers) into flows.
- **tMSSqlInput** (found in BRA, ITA, JPN, SAF, USA, ZAF) -- reads from on-premises Microsoft SQL Server ERP databases.
- **tRESTClient** (13) -- calls REST/OData endpoints, primarily in the GENERIC region for Business Central API extraction.
- **tFileInputDelimited** (87) -- reads CSV and delimited files, used in GENERIC (26 instances) and USA (61 instances).
- **tSalesforceInput** (24) -- reads from Salesforce CRM objects, concentrated in the Global region.
- **tHashInput** (176+) -- reads from in-memory hash tables populated earlier in the same job, enabling lookup joins.

The input category's dominance by tBigQueryInput (58% of all inputs) confirms that the platform is primarily a warehouse-to-warehouse transformation system.

### Transformation Components (745 instances, 16%)

- **tMap** (565) -- the universal transformation workhorse, performing field mapping, filtering, type coercion, expression evaluation, and lookup joins.
- **tJava** (180) -- custom Java code for logic that cannot be expressed declaratively in tMap, including date arithmetic, conditional branching, and string manipulation.

The 3:1 ratio of tMap to tJava indicates that most transformation logic is declarative, but a significant minority (24%) requires imperative Java code -- predominantly in Brazil (83 tJava) and Japan (44 tJava).

### Output Components (716 instances, 16%)

- **tBigQueryOutput** (360) -- writes to BigQuery datasets across all layers.
- **tHashOutput** (269) -- writes to in-memory hash tables for downstream lookup joins within the same job.
- **tBigQuerySQLRow** (180 in total, serving both output and control roles) -- executes SQL statements (often DELETE) against BigQuery.
- **tSalesforceOutput** (found in ITA and Global) -- writes to Salesforce CRM objects for product sync and account updates.

### Control Components (575 instances, 13%)

- **tPrejob** (191+) -- triggers pre-execution setup logic (context variable initialization, temp directory creation).
- **tPostjob** (191) -- triggers post-execution cleanup and logging.
- **cleanup_temp_file_storage** (191) -- custom joblet for removing temporary files after processing.
- **tUnite** (46+ in JPN, 13 in GENERIC) -- merges multiple input streams into a single output (equivalent to SQL UNION ALL).

### Infrastructure Components (193 instances, 4%)

- **Vaxxinova_Logging** (193) -- custom joblet recording execution metadata (start/end timestamps, row counts, status) to a central logging table. Present in 77% of all jobs.

---

## Regional Profiles

### Brazil (BRA): The Largest and Most Complex Region

With 75 jobs and 1,592 components, Brazil dominates the platform. BRA jobs are distinguished by:

- **Extensive tHash usage** (153 tHashOutput + 120 tHashInput = 273 hash components), reflecting a data model with many reference dimensions (customers, items, payment terms, shipment methods, fiscal documents).
- **Heavy tJava presence** (83 instances), used to implement Brazil-specific fiscal logic -- tax entry calculations, currency formatting, and date manipulations for the Brazilian fiscal calendar.
- **Production order tracking** -- a suite of jobs (VBR_Clean_Production_Order_*, VBR_Clean_CBR_*) that have no equivalent in most other regions, reflecting manufacturing operations specific to the Brazilian subsidiary.
- **Dual ERP pipelines** -- both legacy MSSQL-based (Biovet) and newer Business Central-based extraction coexist, with approximately 25-30% of jobs being copies, backups, or debug variants.

BRA_VBR_RAW extracts raw product, sales, and returns data from the on-premises MSSQL database and lands it into BigQuery's raw layer. BIO_Clean_Invoice_Set_v5 then enriches sales and returns data with fiscal periods and Salesforce country mappings, outputting a consolidated Sales_Set. Copy_of_VBR_Clean_Build_Sales_Set performs a similar construction from the newer Business Central source, enriching with fiscal periods, payment terms, shipment methods, conversion rates, country master, and Salesforce account mappings.

BIO_Sync_Product_Salesforce_v1 handles product master data synchronization between BigQuery and Salesforce CRM, including vaccine-specific fields (Species, Subspecies, Hierarchy, Doses). Product codes are prefixed with 'BIO-' to namespace Biovet products across the global Salesforce instance.

### Germany (DEU): The Lean Dual-Entity Region

DEU contains 12 Talend jobs covering the complete data pipeline for Vaxxinova's German subsidiaries. Germany is notable for having two distinct entity prefixes -- VDA (Business Central-based) and VDD (legacy ERP) -- reflecting a dual-ERP landscape.

Key characteristics:
- **No tHash usage.** Unlike BRA and USA, Germany's jobs rely on tMap joins against directly-read BigQuery tables rather than pre-loaded hash maps.
- **Heavy tBigQuerySQLRow presence** (8 instances across 12 jobs), used for the delete-before-insert pattern.
- **Full context parameterization** -- all 8 DEU jobs use `context.parameter_Raw_Prefix` for table references, making Germany the most consistently parameterized region.

### Generic / Shared

GENERIC contains 15 shared/template Talend jobs providing reusable data extraction capabilities. The GEN_BC family includes extractors for core Business Central entities in both direct API and OData protocol formats.

With 211 total components, the component profile is heavily oriented toward API-based extraction: tFileInputDelimited (26), tRESTClient (13), tExtractJSONFields (17), tJavaFlex (13), and tUnite (13) reflect a sophisticated API pagination and response parsing architecture. GENERIC is the only region that relies primarily on REST API calls rather than direct database reads, making it architecturally distinct and the closest to a cloud-native integration pattern.

### Global: The Orchestration Layer

Global's 43 jobs serve two purposes: orchestrating regional child jobs and synchronizing data with Salesforce. The orchestration role is visible in `Global_Clean_Sales_Set`, which calls 10 regional child jobs. The Salesforce sync role is visible in 15+ Salesforce_* jobs that use tSalesforceInput (24 instances) and tSalesforceOutput to exchange data with the CRM.

Global jobs are lightweight (11.3 components on average) because they primarily orchestrate and aggregate rather than perform heavy transformations.

### Italy (ITA): The Compact Salesforce-Integrated Region

With only 6 jobs and 66 components, Italy has the most streamlined pipeline of the major production regions. Yet it includes two tSalesforceOutput components for CRM product synchronization (IZO_Sync_Product_Salesforce_v5). Italy's architecture serves as a good template for a "minimal viable regional pipeline": one raw extraction job, one Sales_Set builder, lifecycle management jobs, and a Salesforce sync.

### Japan (JPN): The Highest Component Density

JPN jobs average 24.2 components each -- the highest density in the platform:

- **tUnite merging** (46 instances), indicating a data model where invoices, credit memos, and ledger entries are processed through parallel streams before being unified.
- **Dimension handling** -- dedicated Raw jobs for dimensions, dimensionSetEntries, and dimensionValue, reflecting a heavily normalized Business Central ERP schema.
- **tBigQuerySQLRow** (36 instances), the highest per-region count, suggesting more SQL-driven transformation logic.
- **Multiple job variants** (active, copy, backup) suggest iterative development with maintenance overhead.

### United States (USA): The File-Ingestion Hybrid

The USA region is distinguished by its file-based data ingestion pathway:

- **tFileInputDelimited** (61 instances) -- the highest file-read count, driven by the Animalytix third-party distributor data pipeline.
- **tGSGet** (Google Cloud Storage retrieval) for the VUS_Raw_Animalytix_SFTP pipeline.
- **Dual-subsidiary structure** -- both VUS (Vaxxinova US) and NPL (Newport Laboratories) with independent extraction and cleaning pipelines.

### South Africa (SAF/ZAF): The Developing Frontier

The combined ZAF (3 jobs, 30 components) and SAF (1 job, 6 components) regions represent the earliest stage of pipeline development. ZAF's `VSA_Raw_vw_InvoiceDetails_ETL` is a purpose-built extraction job, while other jobs are clones from Italy's template.

### Scratchpad / Development

5 Talend jobs serve as development experiments and test artifacts: BigQuery_TEST, sandbox, test, test_2, and test_3. None are intended for production use.

---

## Architectural Patterns

### The Standard Job Skeleton

Across the platform, production jobs follow a consistent six-phase skeleton:

```
tPrejob
  --> [setup: context variables, temp directories]
  --> tBigQueryInput / tMSSqlInput / tRESTClient  (source read)
     --> tMap  (transformation, joins, filtering)
        --> tBigQueryOutput  (target write)
  --> tPostjob
     --> cleanup_temp_file_storage  (remove temp files)
     --> Vaxxinova_Logging  (record execution metadata)
```

**Phase 1 -- Prejob setup.** tPrejob triggers before the main flow starts, initializing context variables and creating temporary directories.

**Phase 2 -- Source extraction.** The source component varies by region: tBigQueryInput for warehouse-to-warehouse transforms, tMSSqlInput for on-premises ERP extraction, and tRESTClient for OData API ingestion.

**Phase 3 -- Transformation.** tMap performs field mapping, type conversion, and lookup joins. Complex jobs chain multiple tMaps with tHashOutput/tHashInput pairs for reference data.

**Phase 4 -- Target load.** tBigQueryOutput writes results to the appropriate BigQuery dataset.

**Phase 5 -- Cleanup.** cleanup_temp_file_storage removes any CSV or JSON files staged to local disk during processing.

**Phase 6 -- Logging.** Vaxxinova_Logging writes a row to the central execution log. Present in 77% of jobs.

### The tHash Lookup Pattern

The most distinctive Talend-specific pattern: paired tHashOutput and tHashInput for in-memory reference data. This avoids repeated BigQuery queries and is used 269 times across the platform (most prevalent in BRA at 153 instances). The migration target must provide an equivalent mechanism -- either in-memory caching, subquery joins, or pre-materialized lookup tables.

### The tUnite Merge Pattern

Japan jobs use tUnite (46 instances) to merge multiple input streams with compatible schemas into a single output, functionally equivalent to SQL UNION ALL. This simplifies downstream processing but requires the migration to preserve stream-merging semantics.

---

## Cross-Cutting Observations

Four components appear in nearly every region and represent platform-wide conventions:

1. **tMap** appears in all 10 regions -- the transformation lingua franca of the platform.
2. **tBigQueryInput/Output** appears in 8 of 10 regions.
3. **Vaxxinova_Logging** appears in 9 of 10 regions. As a custom joblet, it has no out-of-the-box equivalent in any target platform.
4. **cleanup_temp_file_storage** appears in 9 of 10 regions, indicating deeply embedded temp-file-staging patterns.

The platform relies on 15 shared Talend routines (DataOperation, Mathematical, Numeric, Relational, SQLike, StringHandling, TalendDate, TalendString, TalendStringUtil, TalendDataGenerator, DataQuality, DQTechnical, DataMasking, DataQualityDependencies, DqStringHandling). These are standard Talend libraries, but any tMap expression referencing them must be translated to the target platform's expression language.

---

## Platform Statistics

| Metric | Value | Notes |
|--------|-------|-------|
| Total Talend jobs | 257 | Across 10 regions including scratchpad |
| Estimated active production jobs | 180-200 | Excluding copies, backups, debug variants, scratchpad |
| Total component instances | 4,528 | Average of 18 components per job |
| Distinct component types | 30+ | tBigQueryInput, tMap, and tBigQueryOutput account for 34% |
| Distinct tables referenced | 321 | 50 (16%) have column-level metadata from static analysis |
| Context variables | 957 | 363 (38%) have PROD/TEST divergences |
| Named MSSQL connections | 5 | One per regional ERP (BRA, ITA, JPN, USA, ZAF) |
| Salesforce integration jobs | 15+ | Bidirectional sync for accounts, budgets, products, mappings |
| Shared reference tables | 5 | Fiscal_Periods, Country_Master, Conversion_Rate, Account/Country Mappings |
| Custom joblets | 2 | Vaxxinova_Logging (193 jobs), cleanup_temp_file_storage (191 jobs) |
| Shared Talend routines | 15 | All standard library (DataOperation, TalendDate, StringHandling, etc.) |
| Analysis chunks processed | 35 | Covering all 10 regions at varying granularity |
| Regions with active ERP extraction | 6 | BRA, DEU, ITA, JPN, USA, ZAF |
| Regions with Salesforce sync | 4 | BRA (BIO), ITA (IZO), JPN (VJA), Global |
| Highest component density | JPN (24.2/job) | Driven by tUnite merging and tHash lookups |
| Largest region by job count | BRA (75 jobs) | Including copies, backups, and debug variants |
| Smallest production region | SAF (1 job) | Template clone from ITA, not yet differentiated |

---

## Platform-Wide Key Findings

- Two parallel sales data pipelines coexist: legacy MSSQL-based (Biovet) and newer Business Central-based, indicating an active ERP migration
- Copy_of_VBR_Clean_Build_Sales_Set is the most complex job with 94 components, using extensive tHash/tJava patterns for in-memory lookup tables
- The GENERIC region's 15 shared Business Central API jobs use a sophisticated OAuth authentication flow against Azure AD
- Japan's dimension handling is the most granular in the platform, with dedicated Raw jobs reflecting a heavily normalized Business Central ERP schema
- Germany operates a dual-entity structure (VDA for Business Central, VDD for legacy ERP) that must be harmonized into a single Sales_Set
- The USA region is unique in having an external SFTP-based data source (Animalytix) alongside the standard ERP extraction
- Every region exhibits the same six-phase job skeleton: tPrejob, source read, tMap transformation, target write, tPostjob cleanup, and Vaxxinova_Logging
- The platform's 957 context variables include 363 (38%) with PROD/TEST divergences
- Salesforce integration is bidirectional: 15+ Global jobs push data to Salesforce while mapping tables flow back
- A typo exists in the source data: 'Produtct_Description' (misspelled) is carried through from MSSQL views to BigQuery and Salesforce
- Currency conversion uses budget exchange rates rather than real-time market rates, appropriate for management reporting
- DELETE-then-INSERT pattern in Ingest jobs creates a window of data unavailability during job runs
- Heavy proliferation of backup/debug variants: approximately 25-30% of all jobs are copies, backups, or debug versions

---

## Migration Takeaways

**tMap is the highest-risk migration item.** With 565 instances, tMap contains the bulk of the platform's transformation logic. Each tMap must be individually analyzed and translated, as automated migration tools can only handle simple field-to-field mappings.

**The tHash lookup pattern requires an architectural decision.** The 269 tHashOutput instances implement in-memory joins with no direct equivalent in most modern platforms. Options include SQL JOINs inside BigQuery, platform-native caching, or pre-materialized lookup tables.

**Vaxxinova_Logging must be reimplemented as a shared service.** At 193 instances, this is the platform's observability backbone. The migration target should implement centralized logging before migrating individual jobs.

**GENERIC jobs are the easiest migration candidates.** Their REST + JSON + BigQuery pattern maps naturally to modern integration platforms and should be migrated first.

**Scratchpad and deprecated jobs should be excluded from migration.** Pre-migration audit should tag copies, backups, debug variants, and scratchpad jobs for exclusion, reducing scope from 257 to an estimated 180-200 active production jobs.

---

## How to Use This Documentation

### For Developers and ETL Engineers

Start with this Project Overview to understand the platform's scope and regional structure. Then read **02_ARCHITECTURE_INVENTORY** for system topology and execution logic, followed by **03_DATA_FLOWS_LINEAGE** for data lineage and schema details. Before making changes, review the environment configuration and runbook documents.

### For Technical Leadership and Architects

Begin here, then proceed to **02_ARCHITECTURE_INVENTORY** for the system topology and dependency graph, and the known issues document for technical debt and risk areas.

### For Migration Planners

Every document contains "Migration Takeaway" sections. Prioritize this overview (for surface area), the architecture document (for connection provisioning), the data flows document (for schema contracts), and the environment configuration document (for environment parity).

### For New Team Members

Follow the onboarding guide, which provides a structured learning path. Use the glossary and FAQ as a reference for unfamiliar terms.

---

*Generated by intodoc -- documentation automation for integration platforms.*
