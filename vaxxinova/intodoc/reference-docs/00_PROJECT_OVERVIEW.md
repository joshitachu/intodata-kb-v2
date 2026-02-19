# Project Overview

> Vaxxinova Talend Integration Platform -- Comprehensive Documentation

## Executive Summary

This documentation covers Vaxxinova's Talend-based data integration platform, which manages the ETL pipelines connecting on-premises ERP systems, Google BigQuery (the central data warehouse), and Salesforce CRM across multiple global subsidiaries. The platform processes sales, product, customer, and financial data for veterinary vaccine operations spanning Brazil, Germany, Italy, Japan, the United States, and South Africa.

The platform comprises **257 Talend jobs** organized across **10 regions**: BRA, DEU, GENERIC, Global, ITA, JPN, SAF, USA, ZAF, scratchpad.

| Region | Jobs | Description |
|--------|------|-------------|
| BRA | 75 | Brazil -- Biovet/VBR subsidiaries, legacy MSSQL + Business Central |
| DEU | 12 | Germany -- VDA subsidiary |
| GENERIC | 15 | Shared utilities, reference data loaders |
| Global | 43 | Cross-region consolidation, budget, forecast pipelines |
| ITA | 6 | Italy -- VIT subsidiary |
| JPN | 36 | Japan -- VJP subsidiary, largest APAC presence |
| SAF | 1 | South Africa -- VSA subsidiary |
| USA | 61 | United States -- NPL + VUS subsidiaries |
| ZAF | 3 | South Africa -- additional ZAF entity |
| scratchpad | 5 | Development/test jobs |

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

## Regional Overview

### Brazil (BRA)

BRA_0 contains the foundational data integration jobs for the Vaxxinova Brazil (Biovet) subsidiary, covering both the legacy MSSQL-based extraction pipeline and the newer Business Central-based sales data processing. The chunk includes 4 jobs that together form the core sales data pipeline: BRA_VBR_RAW extracts raw product, sales, and returns data from the on-premises MSSQL database (via VW_Finished_Product, VW_Sales, VW_Returns views) and lands it into BigQuery's raw layer. BIO_Clean_Invoice_Set_v5 then reads from the raw BigQuery layer, enriches sales and returns data with fiscal periods and Salesforce country mappings, and outputs a consolidated Sales_Set to the DWH clean layer. Copy_of_VBR_Clean_Build_Sales_Set performs a similar sales set construction but from the newer Business Central source (salesInvoices, salesCreditMemos, salesInvoiceLines, salesCreditMemoLines), enriching with fiscal periods, payment terms, shipment methods, conversion rates, country master, and Salesforce account mappings before writing both local and global Sales_Set tables.

BIO_Sync_Product_Salesforce_v1 handles the product master data synchronization between BigQuery and Salesforce CRM, reading finished product data from BigQuery and performing both update and upsert operations against Salesforce's Product2 object. This enables the sales team to see accurate vaccine product information (species, subspecies, hierarchy, doses) directly within their CRM. The chunk represents the critical handoff ...

*8 analysis chunks cover this region's full pipeline.*

### Germany (DEU)

DEU_all contains 12 Talend jobs covering the complete data pipeline for Vaxxinova's German subsidiaries. Germany is notable for having two distinct entity prefixes -- VDA (Vaxxinova Deutschland Animal health, the Business Central-based entity) and VDD (Vaxxinova Deutschland legacy entity) -- reflecting a dual-ERP landscape. The VDA jobs (VDA_BC_Clean_Invoice_Set_v3, VDA_BC_Clean_Item_v1, VDA_BC_Clean_SalesInvoices) process data from Business Central ('BC' in the name), while the VDD jobs (VDD_Clean_Credit_Invoice_Item_v1, VDD_Clean_Credit_Invoice_v3, VDD_Clean_Invoice_Item_v1, VDD_Clean_Invoice_Set_v1, VDD_Clean_Invoice_v4) process data from the legacy German ERP system. The DEU_VDD_RAW job handles raw data extraction for the VDD entity, and VDA_Clean_Sales_Set_Delete_Ingest manages the Sales_Set lifecycle.

With 117 components dominated by tBigQueryInput (30 instances) and tMap (19 instances), these jobs are primarily BigQuery-to-BigQuery transformations in the clean layer. The architecture follows the same pattern as Japan: raw extraction -> entity-level cleaning -> Sales_Set aggregation. However, Germany's dual-entity structure adds complexity, as data from both VDA (Business Central) and VDD (legacy) must be harmonized into a single Sales_Set output. The VDA_BC prefix jobs specifically handle the newer Business Central data format, suggesting Germany is in a transition from legacy ERP to Business Central.

Standard operational components (Vaxxinova_Logging at 12 instances...

### Generic / Shared

GENERIC_all contains 15 shared/template Talend jobs that provide reusable data extraction capabilities across multiple Vaxxinova regions. These jobs are divided into two major families: the GEN_BC family (Business Central entity extractors) and shared utility jobs. The GEN_BC family includes extractors for core Business Central entities -- AccrualLedgerEntries, Items, SalesCreditMemos, SalesInvoices -- available in both direct API format (GEN_BC_*) and OData protocol format (GEN_BC_*_oData). The OData variants (Customers_oData, Item_oData, Sales_Cr_Memo_Header/Line_oData, Sales_Invoice_Header/Line_oData) provide an alternative extraction pathway using the OData v4 protocol.

With 211 total components, this is one of the largest chunks. The component profile is heavily oriented toward API-based extraction: tFileInputDelimited (26), tRESTClient (13), tExtractJSONFields (17), tJavaFlex (13), and tUnite (13) reflect a sophisticated API pagination and response parsing architecture. The Create_Business_Central_Token component (found in the p01 inventory) confirms that these jobs handle OAuth authentication against Azure AD for Business Central API access.

The GENERIC jobs serve as parameterized templates: regions set context variables (entity name, API endpoint, BigQuery target dataset) and invoke these shared jobs to extract their region-specific data. This is why Germany's VDA (Business Central) raw extraction is not in the DEU chunk -- it uses these GENERIC BC jobs with VDA-spe...

### Global

This chunk contains 12 jobs forming the core of Vaxxinova's global budget, forecast, and prior-year sales data processing pipeline. The Budget_Build_Sales_Set and Budget_Ingest_Sales_Set jobs process budget and forecast data from the Global_Budget BigQuery dataset, enriching it with country master data, Salesforce account mappings, and currency conversion rates. The Prior_Build_Sales_Set and Prior_Ingest_Sales_Set jobs handle historical prior-year sales data for year-over-year comparisons. The Legacy_* jobs maintain backward-compatible data transformations for older reporting needs.

The Global_Build_Sales_Set job is the central orchestration piece that constructs the unified Sales_Set used across all of Vaxxinova's consolidated reporting. It reads from the Global_Clean dataset, applies fiscal period mappings, country enrichment, and Salesforce account linkages to produce the final analytics-ready dataset. The Forecast_Sales_Set and BIO_Clean_Sales_Set jobs handle forecast projections and biological product-specific sales cleaning respectively.

This collection of jobs represents the highest-value analytical layer in the Vaxxinova DWH, as it produces the consolidated sales datasets that drive management dashboards, budget-vs-actual analysis, and multi-year trend reporting across all Vaxxinova regions worldwide. The heavy reliance on BigQuery-to-BigQuery transformations (tBigQueryInput -> tMap -> tBigQueryOutput) indicates a cloud-native transformation architecture where most ...

*8 analysis chunks cover this region's full pipeline.*

### Italy (ITA)

ITA_all contains 6 Talend jobs that comprise the complete data pipeline for Vaxxinova Italy (VIT). The pipeline covers raw extraction (ITA_VIT_RAW), clean-layer sales processing (VIT_Clean_Sales_Set_v6, VIT_Clean_Sales_Set_Ingest, VIT_Clean_Sales_Set_Delete_Ingest, VIT_Clean_SalesInvoices_and_Lines), and Salesforce product synchronization (IZO_Sync_Product_Salesforce_v5). Italy uses the VIT entity prefix and the IZO prefix for its Salesforce integration jobs.

With 66 total components, Italy has the most compact pipeline of the major regional subsidiaries. The ITA_VIT_RAW job extracts data from on-premises MSSQL using tMSSqlInput, following the same pattern as other regions. VIT_Clean_Sales_Set_v6 is the main Sales_Set builder at version 6, indicating substantial maturity. Italy is notable for having both a Salesforce sync job (IZO_Sync_Product_Salesforce_v5) with tSalesforceOutput (2 instances), making it one of only two regions (alongside Japan) with active CRM integration.

The pipeline architecture is streamlined compared to Japan and Germany: a single raw extraction job, a single Sales_Set builder (with lifecycle management via Delete_Ingest), and a Salesforce sync. The VIT_Clean_SalesInvoices_and_Lines job handles combined invoice header and line processing in a single job rather than splitting them, which reduces job count but increases individual job complexity. The tBigQueryInput dominance (15 instances) and tBigQueryOutput (6 instances) confirm the standard BigQuery...

### Japan (JPN)

JPN_0 contains three closely related Talend jobs focused on building the Sales Set for Vaxxinova Japan (VJP): VJP_Clean_Build_Sales_Set, its Copy variant, and a Backup variant. These jobs form the core of the Japan clean-layer sales data pipeline. They read raw sales invoices, credit memos, and general ledger entries from BigQuery (jpn_vjp_raw dataset), apply complex transformations including currency assignment (JPY), entity tagging ('Vaxxinova Japan'), fiscal period derivation, and product ID extraction, then write the unified Sales_Set to both a VJP-local BigQuery dataset and the Global_Clean dataset.

The pipeline follows a delete-then-insert (idempotent reload) pattern: before loading new data, it deletes existing records from Sales_Set that match incoming delta IDs from salesInvoicesDelta and salesCreditMemosDelta. It enriches sales line items with reference data from Global_Master (Fiscal_Periods, Country_Master), Global_Budget (Conversion Rates), Global_Raw (Salesforce_Account_Mappings), and VUS payment terms and shipment methods. The three job variants (active, copy, backup) suggest an iterative development approach, though maintaining multiple near-identical copies introduces maintenance overhead and divergence risk.

Notably, the Copy variant uses context-parameterized environment references (context.parameter_JPN_Raw_Env), while the main and Backup variants hardcode specific dataset names (jpn_vjp_raw, test_jpn_vjp_raw), indicating that the Copy variant is the mor...

*6 analysis chunks cover this region's full pipeline.*

### South Africa (SAF)

SAF_all contains a single Talend job -- ITA_VIT_RAW_Copy -- with only 6 components. This is a direct copy of Italy's raw extraction job placed under the SAF (South Africa) folder. The job reads from MSSQL via tMSSqlInput, applies a tMap transformation, and writes to BigQuery via tBigQueryOutput, with standard post-job housekeeping (cleanup_temp_file_storage, tPostjob, Vaxxinova_Logging).

The presence of an Italian job copy in the South Africa folder strongly suggests this is an early-stage development artifact for the South Africa subsidiary (VSA). The team likely cloned Italy's raw extraction job as a starting template for South Africa's pipeline, intending to adapt it with South Africa-specific connection parameters, table mappings, and context variables. At this stage, the SAF chunk represents the very beginning of the South Africa data pipeline, not yet differentiated from its Italian source.

The SAF folder appears to be a legacy or alternate location for South African jobs, with the primary South Africa development happening in the ZAF folder (see ZAF_all chunk). The single job and minimal component count make this chunk the least complex in the entire Vaxxinova ETL landscape.

### United States (USA)

USA_0 encompasses nine Talend jobs that form the core data-cleaning and ingestion layer for the Vaxxinova US (VUS) subsidiary and the Newport Labs (NPL) legacy entity. The jobs cover three distinct functional domains: (1) Animalytix third-party distributor data ingestion from SFTP/GCS sources, (2) Business Central ERP data cleansing for invoices, items, credit memos, and invoice sets, and (3) NPL legacy customer account cleaning. Together, these jobs transform raw ERP and distributor data into standardized, analytics-ready datasets that feed into the global sales-set aggregation pipeline.

The technical architecture follows a consistent pattern across all nine jobs: a tPrejob phase handles initialization, the main flow reads from BigQuery raw tables (populated by upstream VUS_Raw_* extraction jobs) or external sources, applies tMap transformations with reference-data lookups (Fiscal_Periods, Salesforce_Country_Mapping, Account_Mapping), and writes cleansed output to BigQuery clean tables. Several jobs also write to tHashOutput for dual-output to both local and global sales-set targets. The Animalytix pipeline is notable for its external dependency on SFTP file delivery via tGSGet (Google Cloud Storage), while the Salesforce-suffixed variants (VUS_Clean_BC_Item_v8_Salesforce) push product master data bi-directionally into Salesforce CRM using tSalesforceConnection and tSalesforceOutput components.

All jobs share a standardized lifecycle with tPostjob cleanup (cleanup_temp_fil...

*7 analysis chunks cover this region's full pipeline.*

### South Africa (ZAF)

ZAF_all contains 3 Talend jobs for Vaxxinova South Africa (VSA): DEL_Clean_Sales_Set_Delete_Ingest, ITA_VIT_RAW_Copy, and VSA_Raw_vw_InvoiceDetails_ETL. With 30 total components, this represents South Africa's developing data pipeline. The ZAF folder is the more active South Africa development location compared to the SAF folder.

The most significant job is VSA_Raw_vw_InvoiceDetails_ETL, which extracts invoice details from South Africa's on-premises MSSQL database using a SQL Server view (vw_InvoiceDetails) and loads them into BigQuery. This is a purpose-built extraction job for South Africa's specific data model, not a clone from another region. ITA_VIT_RAW_Copy is the same Italian template clone found in SAF, indicating it was placed in both folders during initial development. DEL_Clean_Sales_Set_Delete_Ingest handles the Sales_Set deletion lifecycle for South Africa.

The architecture is notable for its use of tHashInput/tHashOutput (2 instances each) in the delete-ingest pattern, and tMSSqlInput (2 instances) for MSSQL extraction. The DEL prefix on the delete job is unusual -- it may represent a 'delete' operation abbreviation rather than a region or entity code. With tBigQueryInput (4), tMap (4), and tBigQueryOutput (4) instances, the clean-layer processing is minimal, suggesting South Africa's pipeline is still being built out. The presence of tPrejob (2) indicates some jobs have initialization logic.

### Scratchpad / Development

scratchpad_all contains 5 Talend jobs that serve as development experiments and test artifacts: BigQuery_TEST, sandbox, test, test_2, and test_3. With 32 total components, this chunk represents the team's scratchpad area for prototyping connectivity patterns, testing component behaviors, and validating data access. None of these jobs are intended for production use.

BigQuery_TEST is the most developed job, containing a tBigQueryInput for reading from BigQuery, a tJDBCInput for JDBC-based BigQuery access, tLogRow for console output, and tMap for transformation testing. This suggests it was used to validate BigQuery connectivity via both native and JDBC drivers. The sandbox and test_* jobs are simpler experiments involving tMSSqlInput (testing MSSQL connectivity), tFileInputJSON (testing JSON file parsing), tFileOutputDelimited (testing CSV output), tExtractJSONFields (testing JSON field extraction), and tHashOutput (testing in-memory buffering).

While these jobs provide no production value, they offer insight into the team's development workflow and the connectivity patterns they validated before implementing production jobs. The presence of tMSSqlInput confirms MSSQL connectivity was tested here before being used in regional raw extraction jobs. Similarly, tBigQueryInput/Output and JSON parsing were prototyped here. These jobs should not be deployed to production environments and ideally should be isolated or removed from the main repository.

## How to Use This Documentation

This documentation suite comprises 24 interconnected documents, each addressing a specific dimension of the Vaxxinova Talend integration platform. Different stakeholders should approach the documentation differently depending on their role and objectives.

### For Developers and ETL Engineers

Start with this Project Overview to understand the platform's scope and regional structure. Then read **01_COMPONENT_INVENTORY** to learn which Talend components are used and where, followed by **02_ARCHITECTURE** for the system topology and connection inventory. When working on a specific region, consult **04_DATA_FLOWS** for data lineage and **09_CUSTOM_CODE** for region-specific Java logic. Before making changes, review **05_ENVIRONMENT_CONFIG** to understand context variable management and **13_RUNBOOK** for operational procedures.

### For Technical Leadership and Architects

Begin here, then proceed to **02_ARCHITECTURE** for the system topology and dependency graph, **11_KNOWN_ISSUES** for technical debt and risk areas, and **21_PERFORMANCE** for bottleneck analysis. The **22_CROSS_REGION** document is essential for understanding how regional pipelines interact, and **17_DATA_QUALITY** highlights data integrity concerns that may affect migration planning.

### For Migration Planners

Every document contains a "Migration Takeaway" section at the end of major sections. Read this overview first, then prioritize **01_COMPONENT_INVENTORY** (to understand the migration surface area), **06_EXTERNAL_SYSTEMS** (to plan connection provisioning), **16_SCHEMA_DICTIONARY** (to document the data contract), and **05_ENVIRONMENT_CONFIG** (to plan environment parity). The **08_STATE_SIDE_EFFECTS** document is critical for understanding which jobs have side effects that must be carefully sequenced during cutover.

### For New Team Members

Follow the **15_ONBOARDING** guide, which provides a structured learning path. Use **14_GLOSSARY_FAQ** as a reference for unfamiliar terms and acronyms. This Project Overview provides the broadest context and should be read first.

---

## Documentation Index

The following table lists all 24 documents in this documentation suite, organized by their primary focus area. Each document addresses a specific dimension of the platform and is designed to be read independently, though cross-references link related topics.

| # | Document | Description | Primary Audience |
|---|----------|-------------|------------------|
| 00 | **PROJECT_OVERVIEW** *(this document)* | Executive summary, regional overview, platform-wide key findings | Everyone |
| 01 | **COMPONENT_INVENTORY** | Census of all 4,528 component instances across 251 jobs, grouped by type and region | Developers, Migration Planners |
| 02 | **ARCHITECTURE** | System topology, connection inventory, job dependency graph, data flow layers | Architects, Migration Planners |
| 03 | **EXECUTION_LOGIC** | Job execution sequences, trigger mechanisms, conditional branching patterns | Developers, Operations |
| 04 | **DATA_FLOWS** | End-to-end data lineage from source systems through raw/clean/global layers to Salesforce | Developers, Data Analysts |
| 05 | **ENVIRONMENT_CONFIG** | Context variable management, PROD/TEST divergences, environment promotion patterns | DevOps, Migration Planners |
| 06 | **EXTERNAL_SYSTEMS** | Inventory of all 11 external systems, connection parameters, authentication methods | Infrastructure, Migration Planners |
| 07 | **ERROR_HANDLING** | Error handling patterns (or lack thereof), retry logic, failure modes across regions | Operations, Developers |
| 08 | **STATE_SIDE_EFFECTS** | Side effects of job execution: file creation, table mutations, Salesforce writes, logging | Operations, Migration Planners |
| 09 | **CUSTOM_CODE** | Analysis of 180 tJava components, custom Java logic by region, routine usage | Developers, Migration Planners |
| 10 | **BUSINESS_LOGIC** | Business rules embedded in tMap expressions, fiscal logic, currency conversion, product hierarchies | Business Analysts, Developers |
| 11 | **KNOWN_ISSUES** | Technical debt, hardcoded values, missing error handling, deprecated jobs, data quality risks | Leadership, Architects |
| 12 | **CHANGE_LOG** | Version history of jobs, iteration patterns, copy/backup proliferation analysis | Developers, Project Managers |
| 13 | **RUNBOOK** | Operational procedures for monitoring, troubleshooting, restarting failed jobs | Operations, Support |
| 14 | **GLOSSARY_FAQ** | Definitions of key terms, acronyms, entity prefixes, and frequently asked questions | Everyone (reference) |
| 15 | **ONBOARDING** | Structured learning path for new team members joining the Vaxxinova data platform team | New Team Members |
| 16 | **SCHEMA_DICTIONARY** | Inventory of 321 tables, column metadata for 50 tables, parameterized table name resolution | Developers, Data Analysts |
| 17 | **DATA_QUALITY** | Data quality patterns and gaps: null handling, type coercion, deduplication, validation rules | Data Engineers, Analysts |
| 18 | **NAMING_CONVENTIONS** | Job, dataset, table, and column naming conventions across all regions | Developers, Standards |
| 19 | **SCHEDULING_SLA** | Job scheduling patterns, execution frequency, SLA implications, dependency timing | Operations, Leadership |
| 20 | **SECURITY_ACCESS** | Credential management, service account usage, access control patterns, security recommendations | Security, Infrastructure |
| 21 | **PERFORMANCE** | Performance bottlenecks, component density analysis, optimization opportunities | Architects, Developers |
| 22 | **CROSS_REGION** | Cross-region data flows, shared dependencies, global consolidation patterns | Architects, Business Analysts |
| 23 | **REUSABILITY** | Reusable patterns, shared joblets, template jobs, opportunities for consolidation | Architects, Migration Planners |

---

## Platform-Wide Key Findings

- Two parallel sales data pipelines coexist: legacy MSSQL-based (Biovet) and newer Business Central-based, indicating an active ERP migration
- BRA_VBR_RAW extracts 3 key datasets (Finished Products, Sales, Returns) from on-prem MSSQL to BigQuery raw layer using parameterized schema references
- BIO_Clean_Invoice_Set_v5 hardcodes entity as 'Biovet' and currency as 'BRL', tightly coupling it to the Brazil subsidiary
- Copy_of_VBR_Clean_Build_Sales_Set is the most complex job with 94 components, using extensive tHash/tJava patterns for in-memory lookup tables (13 tFixedFlowInput, 14 tHashOutput components)
- BIO_Sync_Product_Salesforce_v1 syncs product master data to Salesforce including vaccine-specific fields: Species, Subspecies, Hierarchy, Doses
- Product codes in Salesforce are prefixed with 'BIO-' to namespace Biovet products across the global Vaxxinova Salesforce instance
- Global reference tables (Fiscal_Periods, Country_Master, Salesforce_Country_Mappings, Budget_Current_Conversion_Rate) are shared across subsidiaries
- The TRUNCATE write mode on Sales_Set output means full table replacement on each run -- no incremental loading for this dataset
- A typo exists in the source data: 'Produtct_Description' (misspelled) is carried through from MSSQL views to BigQuery and Salesforce
- 3 out of 4 jobs have zero error handling components; only BIO_Sync_Product_Salesforce_v1 has minimal logging via tLogRow
- Incremental loading pattern detected: newer VBR_Clean_Build_Sales_Set filters salesInvoices and salesCreditMemos by lastModifiedDateTime >= parameter_date, reducing data volume on each run
- Multiple job versions exist (2x Build, 2x Ingest, plus backups) suggesting active development with no clear deprecation of older versions
- Complex multi-source join: Sales_Set construction involves 10+ BigQuery source tables joined through tMap with tHash-based in-memory lookups
- DELETE-then-INSERT pattern in Ingest jobs: tBigQuerySQLRow deletes existing global Sales_Set records before tBigQueryOutput writes new ones, creating a window of data unavailability
- 67 tBigQueryInput components across 6 jobs -- highest density in the BRA region, reflecting the reference-data-heavy nature of sales transformation
- Currency conversion uses Global_Budget.Budget_Current_Conversion_Rate table, tying sales reporting to budget exchange rates rather than real-time market rates
- Fiscal period mapping enables Vaxxinova's non-calendar fiscal year reporting across the Brazil subsidiary
- Salesforce Account Mappings link Business Central customer IDs to Salesforce Account records for CRM-integrated sales analytics
- 5 out of 6 jobs have zero error handling; only one Build variant has a tLogRow for minimal logging
- Backup jobs appear to be full snapshots of the Build/Ingest logic -- useful for rollback but add maintenance burden
- Heavy proliferation of backup/debug variants: 5 of 8 jobs are copies, backups, or debug versions, indicating unstable development iteration
- VBR_Clean_SalesInvoiceLines and VBR_Clean_SalesInvoices represent a modular decomposition of the monolithic Sales_Set Build job into separate header/line cleaning
- 13 tBigQuerySQLRow components used for DELETE operations -- highest density of destructive SQL across BRA chunks
- DEBUG variants include tLogRow components for diagnostic output, useful for troubleshooting but risky if accidentally run in production
- VBR_Clean_Production_Order_COPY is a copied version of production order processing, bridging sales and manufacturing domains
- The GENERIC region's 15 shared Business Central API jobs use a sophisticated OAuth authentication flow (Create_Business_Central_Token) against Azure AD, with both direct and OData protocol variants available for each entity
- Japan's dimension handling is the most granular in the platform: dedicated Raw jobs for dimensions, dimensionSetEntries, and dimensionValue reflect a heavily normalized Business Central ERP schema requiring multiple extraction steps to flatten
- Germany operates a dual-entity structure (VDA for Business Central, VDD for legacy ERP) that must be harmonized into a single Sales_Set, adding complexity that other single-ERP regions do not face
- The USA region is unique in having an external SFTP-based data source (Animalytix third-party distributor data) alongside the standard ERP extraction, making it the only region with a file-transfer protocol dependency
- Across all 35 analysis chunks, every region exhibits the same six-phase job skeleton: tPrejob, source read, tMap transformation, target write, tPostjob cleanup, and Vaxxinova_Logging -- confirming this is a platform-wide convention, not a per-region choice
- The platform's 957 context variables include 363 (38%) with PROD/TEST divergences, indicating that nearly two in five configuration parameters differ between environments -- a significant source of deployment risk
- Salesforce integration is bidirectional: 15+ Global jobs push data to Salesforce (accounts, budgets, products, country mappings, ledger data, conversion rates, standard cost prices), while mapping tables flow from Salesforce back to BigQuery
- South Africa (ZAF) has a purpose-built extraction job (VSA_Raw_vw_InvoiceDetails_ETL) that is not cloned from another region, indicating active pipeline development despite the region's small footprint
- The scratchpad region's test jobs provide archaeological evidence of the team's development workflow: MSSQL connectivity, BigQuery access (both native and JDBC), and JSON parsing were all prototyped there before being used in production

---

## Platform Statistics at a Glance

The following summary captures the quantitative dimensions of the platform, providing a quick reference for sizing migration efforts, estimating complexity, and communicating scope to stakeholders.

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

### Coverage Analysis

The 35 analysis chunks provide complete coverage of the platform. Every region has been analyzed, and every job has been assigned to at least one chunk. The analysis depth varies by region complexity: BRA and USA each have 7-8 dedicated chunks reflecting their size, while smaller regions (DEU, ITA, SAF, ZAF, scratchpad) are each covered in a single chunk. The Global region, despite its moderate job count (43), required 8 chunks due to the diversity of its job types (orchestration, Salesforce sync, budget processing, legacy maintenance).

The analysis captured summaries, FAQs, glossary entries, onboarding guidance, and risk dashboard data for every chunk, ensuring that the downstream documentation (10_BUSINESS_LOGIC, 11_KNOWN_ISSUES, 14_GLOSSARY_FAQ, 15_ONBOARDING) has data from all regions rather than being biased toward the largest or most complex ones.

---

*Generated by intodoc -- documentation automation for integration platforms.*