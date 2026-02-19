# Data Flows

Vaxxinova's Talend integration platform moves veterinary vaccine sales, product, customer, and financial data from regional ERP systems through a three-layer warehouse architecture in Google BigQuery. Source systems -- on-premises Microsoft SQL Server databases, Microsoft Dynamics 365 Business Central REST APIs, Salesforce CRM, and third-party SFTP feeds -- push raw transactional data into BigQuery's raw layer. From there, Talend jobs clean, enrich, and reshape the data into standardized business-ready datasets in the clean layer. Finally, global consolidation jobs merge regional clean data into unified reporting tables that power management dashboards, budget-vs-actual analysis, and multi-year trend reporting across all Vaxxinova subsidiaries worldwide. Across the platform, 257 Talend jobs execute 4,715 transformation steps spanning 10 regional codebases.

---

## The Canonical Flow Pattern

Nearly every Talend job in the Vaxxinova platform follows a single dominant pattern: **tBigQueryInput -> tMap -> tBigQueryOutput**. Understanding this pattern is the key to understanding the platform's data movement.

**tBigQueryInput** reads rows from a BigQuery table or view. It is the most heavily used component on the platform, appearing 619 times across all jobs. Each input component binds to a specific BigQuery dataset and table -- for example, `bra_vbr_raw.salesInvoices` or `Global_Master.Fiscal_Periods` -- and pulls the full table or a filtered subset into the Talend runtime.

**tMap** is the universal transformation engine. With 565 instances across all regions, it appears in every production job. A tMap component receives one or more input flows, applies join logic, column mappings, expression-based transformations, type conversions, and conditional routing, then emits one or more output flows. It is where the business rules live: currency defaults, fiscal period derivations, country code fallbacks, Salesforce account lookups, and entity tagging all happen inside tMap expressions. The tMap is Talend-proprietary -- its expression language, join semantics, and null-handling behavior have no direct equivalent in other platforms.

**tBigQueryOutput** writes the transformed rows to a target BigQuery table. With 360 instances, it is the third most common component. Depending on the job, the write mode is either TRUNCATE (full table replacement) or INSERT (append). Some jobs write to both a regional clean table and the Global_Clean dataset in the same run, using tHashOutput to fork the data flow.

Beyond this core trio, several supporting patterns recur:

- **tFixedFlowInput + tHashOutput** (228 + 269 instances): Used to build in-memory lookup tables for reference data. A tFixedFlowInput loads a hardcoded row set, and tHashOutput stages it in memory for tMap to join against.
- **tBigQuerySQLRow** (180 instances): Executes ad-hoc SQL statements, most commonly DELETE statements that clear target tables before a fresh insert, implementing the idempotent delete-then-insert reload pattern.
- **tMSSqlInput**: Reads from on-premises Microsoft SQL Server databases for regions that still extract from legacy ERPs (Brazil, Italy, Japan, South Africa, USA).
- **tRESTClient + tExtractJSONFields**: Calls Business Central OData/REST APIs, parses the JSON response, handles pagination, and stages extracted records for BigQuery loading. Concentrated in the GENERIC shared jobs.
- **tSalesforceInput / tSalesforceOutput**: Reads from and writes to Salesforce CRM for product master and account mapping synchronization.

### Migration Takeaway

The tMap component is the single largest migration risk. Its 565 instances contain embedded expression logic -- string manipulations, null-coalescing, type casts, conditional branches, and multi-table join definitions -- all written in Talend's proprietary expression language. Each tMap must be reverse-engineered, understood, and translated into the target platform's transformation language (SQL, Python, Workato formulas, ADF data flow expressions, or equivalent). There is no automated converter.

---

## Source System Diversity

Data enters the Vaxxinova platform from four distinct source system categories, each with different extraction mechanics.

### MSSQL: On-Premises ERP Extraction

Five named MSSQL connections serve regional subsidiaries: `connection_BRA_VBR_MSSQL` (Brazil/Biovet), `connection_JPN_VJP_MSSQL` (Japan), `connection_ITA_VAX_MSSQL` (Italy, also shared with SAF/ZAF), `connection_SAF_VSA_MSSQL` (South Africa), and `connection_USA_NPL_MSSQL` (USA/Newport Labs). These connections extract data from SQL Server views -- for example, Brazil's `BRA_VBR_RAW` job reads from `VW_Finished_Product`, `VW_Sales`, and `VW_Returns` views. MSSQL extraction requires network connectivity between the Talend runtime and the on-premises database servers, making it the most infrastructure-sensitive source type.

### REST APIs: Business Central

Regions that have migrated (or are migrating) from legacy ERP to Microsoft Dynamics 365 Business Central use REST API extraction. The GENERIC shared jobs (`GEN_BC_SalesInvoices`, `GEN_BC_SalesCreditMemos`, `GEN_BC_Items`, etc.) handle OAuth token acquisition against Azure AD, paginated API calls via tRESTClient, JSON response parsing via tExtractJSONFields, and BigQuery loading. Germany (VDA) and parts of Brazil's newer pipeline use this path. OData v4 variants exist for some entities (e.g., `GEN_BC_Customers_oData`), providing an alternative protocol for the same data.

### Salesforce CRM

Salesforce serves as both a source and a sink. As a source, `tSalesforceInput` reads account mappings and country mappings from Salesforce, which are used as reference data in sales set construction. As a sink, product sync jobs (`BIO_Sync_Product_Salesforce_v1`, `IZO_Sync_Product_Salesforce_v5`, and USA equivalents) push product master data -- including vaccine-specific fields like Species, Subspecies, Hierarchy, and Doses -- back into Salesforce's Product2 object. Product codes are namespaced by region (e.g., `BIO-` prefix for Biovet products).

### SFTP / External Feeds

The USA region has a unique external data source: Animalytix third-party distributor data, delivered via SFTP and staged in Google Cloud Storage (`tGSGet`). This feed provides distributor-level sales data that supplements the direct ERP extraction pipeline.

### Migration Takeaway

Each source type requires a different connector strategy in the target platform. MSSQL connections need network path equivalents (VPN, private endpoints, or gateway agents). Business Central REST extraction needs OAuth token flow reimplementation. Salesforce integration requires re-provisioning of OAuth credentials and field-level security configuration. The SFTP pipeline needs equivalent file-watch and staging logic.

---

## Most Complex Data Flows

The platform's complexity is not evenly distributed. A handful of jobs contain the majority of transformation logic, and they are concentrated overwhelmingly in Brazil (BRA). The seven most transformation-heavy jobs are all BRA jobs, reflecting Brazil's dual-ERP landscape (legacy MSSQL Biovet and newer Business Central) and the resulting data harmonization burden.

### Copy_of_VBR_Clean_Build_Sales_Set (BRA)

This is the most complex job on the entire platform: 94 components, 26 input sources, 17 outputs, and 20 transform steps. It constructs the unified Sales_Set from Business Central data, enriching sales invoice and credit memo lines with fiscal periods, payment terms, shipment methods, currency conversion rates, country master data, and Salesforce account mappings. The 13 tFixedFlowInput and 14 tHashOutput components indicate heavy use of in-memory lookup tables -- essentially building a star-schema join at runtime. This single job touches more BigQuery tables than most entire regional pipelines.

### VBR_Clean_Build_Sales_Set (BRA)

Two variants of this job exist, with 13 and 16 inputs respectively, each producing 4 outputs through 20 transforms. These are the predecessors or parallel versions of the Copy variant above. The existence of multiple near-identical versions -- active, copy, and backup -- suggests iterative development without clear deprecation. The 16-input variant appears to be the more mature version, using context-parameterized environment references (`context.parameter_BRA_Raw_Env`), while others hardcode dataset names.

### BIO_Clean_Invoice_Set_v5 (BRA)

This job handles the legacy Biovet sales pipeline. It reads 4 inputs from the MSSQL-sourced raw layer, applies 20 transforms to enrich sales and returns data with fiscal periods and Salesforce country mappings, and writes a consolidated Sales_Set. It hardcodes the entity as 'Biovet' and the currency as 'BRL', tightly coupling it to the Brazil subsidiary. Despite being legacy, it remains in active use alongside the newer Business Central pipeline.

### BIO_Sync_Product_Salesforce_v1 (BRA)

With 2 inputs, 2 outputs, and 20 transforms, this job is complex not because of its fan-out but because of its bidirectional nature. It reads finished product data from BigQuery, applies Salesforce field mappings (including vaccine-specific metadata), and performs both update and upsert operations against Salesforce's Product2 object. It is one of only a few jobs on the platform with any error handling (a tLogRow for diagnostic output).

### BRA_VBR_RAW (BRA)

The raw extraction job for Brazil reads 3 key datasets (Finished Products, Sales, Returns) from on-premises MSSQL using parameterized schema references, applies 20 transforms for column mapping and type conversion, and writes 3 outputs to BigQuery's raw layer. It is the entry point for all Biovet data and depends on VPN connectivity to the Brazil MSSQL server.

### Why Brazil Dominates

Brazil's disproportionate complexity (71 jobs, 1,448 transforms -- 31% of the platform's total) stems from the coexistence of two ERP systems. The legacy Biovet MSSQL pipeline and the newer Business Central pipeline must produce identical Sales_Set outputs, and the team has maintained both paths with multiple backup and debug variants. Of Brazil's 71 jobs, roughly 5 in 8 are copies, backups, or debug versions -- useful for rollback but a significant maintenance burden.

### Migration Takeaway

The Brazil Sales_Set construction jobs should be the first migration target for proof-of-concept work, because they exercise the widest range of platform capabilities: multi-source joins, in-memory lookups, Salesforce integration, parameterized environments, and both MSSQL and BigQuery I/O. Successfully migrating `Copy_of_VBR_Clean_Build_Sales_Set` would validate the target platform's ability to handle the platform's most demanding workload.

---

## Regional Comparison

The following table summarizes each region's data flow profile. Input and output type classifications are derived from component analysis; the "unknown" designation reflects that Talend's internal XML metadata does not always expose explicit connector types in a machine-readable way.

| Region | Jobs | Transforms | Inputs | Outputs | Primary Sources | Primary Targets |
|-----------|------|------------|--------|---------|----------------------------------------------|-------------------------------|
| BRA | 71 | 1,448 | 548 | 308 | MSSQL (Biovet), Business Central REST, BigQuery | BigQuery raw/clean, Salesforce |
| USA | 61 | 1,106 | 343 | 157 | Business Central REST, MSSQL (NPL), SFTP | BigQuery raw/clean, Salesforce |
| Global | 42 | 786 | 138 | 43 | BigQuery clean (all regions), Salesforce | BigQuery global, Salesforce |
| JPN | 36 | 671 | 263 | 96 | MSSQL (VJP), Business Central REST, BigQuery | BigQuery raw/clean |
| GENERIC | 15 | 276 | 56 | 18 | Business Central REST APIs | BigQuery raw |
| DEU | 12 | 184 | 34 | 12 | Business Central (VDA), Legacy ERP (VDD) | BigQuery clean |
| ITA | 6 | 112 | 21 | 10 | MSSQL (VIT), BigQuery | BigQuery clean, Salesforce |
| scratchpad | 5 | 52 | 8 | 9 | MSSQL, BigQuery, JSON files | BigQuery, CSV, console |
| ZAF | 3 | 60 | 8 | 5 | MSSQL (VSA), BigQuery | BigQuery raw/clean |
| SAF | 1 | 20 | 1 | 1 | MSSQL (cloned ITA template) | BigQuery raw |

Three regions -- BRA, USA, and JPN -- account for 168 of 257 jobs (65%) and 3,225 of 4,715 transforms (68%). These three should receive the majority of migration effort and testing.

### Migration Takeaway

SAF and ZAF together represent South Africa at different stages of development (SAF is a single cloned template; ZAF has 3 purpose-built jobs). These should be consolidated into a single ZAF pipeline during migration. The scratchpad jobs should not be migrated -- they are development artifacts with no production value.

---

## Data Lineage: From Operational Systems to Analytics

Data follows a predictable path through the Vaxxinova platform, though the specifics vary by region and ERP vintage.

### Layer 1: Raw Extraction

Regional raw extraction jobs (`BRA_VBR_RAW`, `JPN_VJP_RAW`, `ITA_VIT_RAW`, `DEU_VDD_RAW`, etc.) pull transactional data from source systems and land it in BigQuery datasets with a `raw` prefix (e.g., `bra_vbr_raw`, `jpn_vjp_raw`). Jobs at this layer perform minimal transformation -- column mapping, type conversion, and CSV staging. For MSSQL sources, extraction uses SQL Server views (e.g., `VW_Sales`, `VW_Returns`, `vw_InvoiceDetails`). For Business Central sources, the GENERIC shared jobs handle API pagination and JSON parsing before loading.

### Layer 2: Regional Clean

Clean-layer jobs read from the raw BigQuery datasets and apply the bulk of business logic. The canonical operation at this layer is Sales_Set construction: invoices, credit memos, and line items are joined with fiscal period calendars, country master data, currency conversion rates, payment terms, shipment methods, and Salesforce account mappings. The output is a standardized `Sales_Set` table that follows a common schema across all regions, enabling apples-to-apples comparison at the global level.

For example, Japan's `VJP_Clean_Build_Sales_Set` reads from `jpn_vjp_raw`, assigns JPY as the currency, tags the entity as 'Vaxxinova Japan', derives fiscal periods from Global_Master.Fiscal_Periods, looks up Salesforce account IDs from Global_Raw.Salesforce_Account_Mappings, converts amounts to EUR using Global_Budget.Budget_Current_Conversion_Rate, and writes the result to both `jpn_vjp_clean.Sales_Set` and `Global_Clean.Sales_Set`.

This dual-write pattern -- local clean plus global clean -- is the primary mechanism for data flowing from regional to consolidated views.

### Layer 3: Global Consolidation

Global-layer jobs in the `Global` region read from the Global_Clean dataset (which has been populated by regional clean jobs) and produce the final analytics-ready datasets. `Global_Build_Sales_Set` applies additional fiscal period mappings, country enrichment, and Salesforce account linkages. `Budget_Build_Sales_Set` and `Prior_Build_Sales_Set` process budget/forecast and prior-year data respectively, enabling variance analysis. `Forecast_Sales_Set` handles forward-looking projections.

The global layer also runs Salesforce synchronization jobs (`Salesforce_Account_Mappings_v3` and variants) that pull CRM data into BigQuery as reference tables, closing the loop between the warehouse and the CRM.

### End-to-End Data Lineage Example: Japan (JPN)

To make the three-layer architecture concrete, the following traces a single data record from its origin in Japan's on-premises MSSQL database through to the global management dashboard. This end-to-end walkthrough illustrates every transformation, enrichment, and staging step that a sales invoice line item undergoes.

**Step 1: MSSQL Source Extraction.** The journey begins on the Vaxxinova Japan (VJP) on-premises SQL Server, where a sales invoice line item exists in the ERP's transactional database. The `JPN_VJP_RAW` extraction job connects to this server via `connection_JPN_VJP_MSSQL`, reads from a SQL Server view that joins invoice headers with line items, and extracts fields including: document number, line number, customer number, item number, quantity, unit price, amount, posting date, and currency code (JPY). The tMap component at the raw layer performs minimal transformation: GUID normalization (removing curly braces from Business Central systemId fields), date format standardization, and null-safe type casting. The extracted record is written to `jpn_vjp_raw.salesInvoiceLines` in BigQuery.

**Step 2: Reference Data Loading.** Before the clean-layer transformation runs, several reference data jobs must have completed. `Salesforce_Account_Mappings_v3` refreshes `Global_Raw.Salesforce_Account_Mappings`, which maps the VJP customer number to a Salesforce Account ID. The Fiscal_Periods table in `Global_Master` must contain the fiscal year and period for the invoice's posting date. `Budget_Current_Conversion_Rate` in `Global_Budget` must contain the JPY-to-EUR conversion rate for the posting period.

**Step 3: Clean-Layer Transformation.** `VJP_Clean_Build_Sales_Set` reads the raw invoice line from `jpn_vjp_raw.salesInvoiceLines`. A tMap component performs the core enrichment: it joins the invoice line with `Global_Master.Fiscal_Periods` (matching on posting date to derive fiscal year, fiscal quarter, and fiscal period), `Global_Raw.Salesforce_Account_Mappings` (matching on customer number to derive the Salesforce Account ID), and `Global_Budget.Budget_Current_Conversion_Rate` (matching on currency code and fiscal period to derive the EUR-equivalent amount). The tMap also applies hardcoded values: entity = 'Vaxxinova Japan', entity_code = 'VJP', default_currency = 'JPY', region = 'JPN'. The enriched record is written to both `jpn_vjp_clean.Sales_Set` and `Global_Clean.Sales_Set` via a forked tBigQueryOutput configuration.

**Step 4: Global Consolidation.** `Global_Build_Sales_Set` reads from `Global_Clean.Sales_Set` (which now contains the Japan record alongside records from all other regions). It applies a final round of enrichment: global fiscal period alignment (ensuring all regions use the same fiscal calendar), country name standardization from `Global_Raw.Salesforce_Country_Mappings`, and budget variance calculation (comparing the actual EUR amount against the budgeted EUR amount for that entity, product, and period). The fully enriched, globally standardized record is written to the canonical `Global_Reporting.Sales_Set` table.

**Step 5: Dashboard Consumption.** A business intelligence tool (likely Looker, Data Studio, or Power BI -- the BI layer is not visible in the Talend metadata) reads from `Global_Reporting.Sales_Set` and presents the Japan invoice line item as part of a global sales dashboard. A finance manager in the Netherlands can see the VJP subsidiary's sales performance in EUR alongside BRA's performance in BRL-to-EUR and USA's performance in USD-to-EUR, all on the same chart.

**Total transformation steps for one record:** 4 extraction fields + 5 reference data joins + 8 enrichment expressions + 2 BigQuery writes + 1 global consolidation pass = approximately 20 distinct transformation operations across 3 Talend jobs. The record touches 6 BigQuery tables between source and destination. If any intermediate step fails silently (and with zero error handling in Japan's pipeline, every step can fail silently), the downstream dashboard shows either stale data or missing data with no indication of the problem.

## Specific Data Flow Patterns

Beyond the canonical three-layer flow, several recurring data flow patterns deserve individual attention because they represent either common migration targets or common failure modes.

### Pattern 1: The Full-Table TRUNCATE Reload

Used by jobs that rebuild an entire target table on each run. The flow is:

```
tBigQueryOutput (mode: TRUNCATE) --> target table completely replaced
```

This pattern is simple and idempotent but creates a window of complete data unavailability while the job runs. If the job fails mid-write, the table is left either empty (if TRUNCATE executes before any rows are written) or partially populated. Jobs using this pattern include `BIO_Clean_Invoice_Set_v5` (BRA) and several of the clean-layer Sales_Set builders. During migration, TRUNCATE reloads should be replaced with staging-table-swap patterns: write to a staging table, validate row counts, then atomically swap the staging table with the production table.

### Pattern 2: The Delta DELETE-then-INSERT

Used by jobs that reload a subset of records (typically identified by a delta date or ID range) without replacing the entire table:

```
tBigQuerySQLRow (DELETE WHERE delta_id IN (...)) --SUBJOB_OK--> tBigQueryOutput (mode: INSERT)
```

This pattern is more surgical than TRUNCATE but still creates a risk window between the DELETE and INSERT. It appears extensively in the Global ingest jobs (`Legacy_Ingest_Sales_Set`, `Budget_Ingest_Sales_Set`, etc.) where specific time-period records are refreshed while historical records are preserved. The 180 instances of `tBigQuerySQLRow` across the platform, most executing DELETE statements, indicate how deeply embedded this pattern is.

### Pattern 3: The In-Memory Lookup Star Join

Used by the most complex transformation jobs, particularly in BRA and JPN:

```
tFixedFlowInput (fiscal periods) --> tHashOutput (hash_FiscalPeriods)
tFixedFlowInput (country master)  --> tHashOutput (hash_CountryMaster)
tFixedFlowInput (conversion rates) --> tHashOutput (hash_ConversionRates)
...
tBigQueryInput (raw sales data) --> tMap [joins against all hash tables] --> tBigQueryOutput
```

This pattern loads reference data into in-memory hash tables at the start of the job, then performs star-schema-style joins inside tMap. The 228 tFixedFlowInput and 269 tHashOutput instances indicate that this is the platform's dominant approach to reference data enrichment. The advantage is performance (in-memory lookups avoid repeated BigQuery round-trips); the disadvantage is memory pressure (all reference data must fit in the Talend JVM's heap). During migration, this pattern should be evaluated for replacement with SQL-based joins in BigQuery, which can handle arbitrarily large reference tables without memory constraints.

### Pattern 4: The Bidirectional Salesforce Sync

Used by the three regions with Salesforce integration (BRA, ITA, USA):

```
Direction 1: Salesforce --> BigQuery (account mappings, country mappings)
  tSalesforceInput --> tMap --> tBigQueryOutput (Global_Raw.Salesforce_*)

Direction 2: BigQuery --> Salesforce (product master data)
  tBigQueryInput (product data) --> tMap (field mapping + namespace prefix) --> tSalesforceOutput (Product2 upsert)
```

The bidirectional nature of this flow creates a data loop: Salesforce account data flows into BigQuery for Sales_Set enrichment, while BigQuery product data flows back into Salesforce for the sales team's catalog. The flows are asymmetric (different entities in each direction) and asynchronous (they run as separate jobs), but they share a dependency on Salesforce API availability and OAuth credential validity.

### The Delete-Then-Insert Pattern

Data freshness at the clean and global layers relies on an idempotent reload pattern: `tBigQuerySQLRow` executes a DELETE statement to remove existing records matching the incoming delta IDs, then `tBigQueryOutput` inserts the new records. This avoids duplicates but creates a brief window of data unavailability during the reload. The 180 instances of tBigQuerySQLRow across the platform, most used for DELETE operations, indicate how deeply embedded this pattern is.

Some jobs use a more aggressive TRUNCATE write mode on the Sales_Set output, meaning full table replacement on each run with no incremental loading. This simplifies logic but increases load times and creates longer unavailability windows.

### Migration Takeaway

The three-layer architecture (raw -> clean -> global) maps naturally to modern ELT patterns and could be reimplemented in dbt, Dataform, or equivalent SQL-based transformation tools. The key challenge is the tMap expression layer: 565 tMap instances contain embedded business logic that must be translated into SQL or another transformation language. The delete-then-insert and truncate patterns should be evaluated for replacement with MERGE/UPSERT semantics in the target platform, which would eliminate the data unavailability window and support true incremental processing.

---

## Platform-Wide Migration Takeaways

1. **tMap translation is the critical path.** The 565 tMap instances are the repository of all business logic. Each contains join definitions, expression-based column transformations, null handling, type conversions, and conditional routing. No automated migration tool exists for Talend tMap expressions; they must be manually reverse-engineered and rewritten.

2. **BigQuery I/O should be straightforward.** With 619 tBigQueryInput and 360 tBigQueryOutput instances, BigQuery is the dominant data store. Any modern platform with native BigQuery connectors can replace these components with minimal risk.

3. **MSSQL connectivity requires infrastructure planning.** Five named MSSQL connections depend on network paths between the Talend runtime and on-premises databases. The target platform must provide equivalent connectivity (self-hosted integration runtime, VPN gateway, or private link).

4. **Consider consolidating backup/copy/debug variants.** Roughly 30% of all jobs are copies, backups, or debug versions of other jobs. Migration is an opportunity to collapse these into a single canonical version per pipeline, with proper version control replacing the copy-based development workflow.

5. **Parallelize where possible.** 256 of 257 jobs are standalone with no orchestration dependencies, and most follow sequential SUBJOB_OK chaining internally. The target platform should evaluate which subjobs can run in parallel to reduce overall execution time.

6. **Prioritize Brazil for proof-of-concept.** Brazil exercises every source type (MSSQL, REST API, Salesforce), every layer (raw, clean, global), and the most complex transformation logic. A successful BRA migration validates the target platform's readiness for the entire portfolio.

---

*Generated by intodoc -- documentation automation for integration platforms.*
