# Architecture

The Vaxxinova Talend platform implements a three-layer data architecture -- **raw**, **clean**, and **global** -- that consolidates financial and operational data from regional ERP systems into a single Google BigQuery warehouse, then pushes curated datasets outward to Salesforce for commercial reporting. The platform connects 11 distinct external systems across 10 geographic regions, orchestrated by a hub-and-spoke job dependency model where a small number of Global-layer jobs call region-specific child jobs to assemble cross-region datasets. This document maps the system topology, connection inventory, job dependency graph, data flow architecture, and integration patterns that together define how data moves through the platform.

---

## System Topology

The architecture centers on Google BigQuery as the universal data warehouse. Every region writes to BigQuery and reads from BigQuery; it is the only system that spans all 10 regions. Around this central node, four categories of external systems connect at different points in the data lifecycle:

**Regional ERP databases (Microsoft SQL Server).** Five named MSSQL connections serve as source systems, each providing on-premises ERP data for a specific subsidiary. These connections are the entry points for raw transactional data -- invoices, sales orders, customers, items, production orders, ledger entries, and value entries. The ERP systems run Microsoft Dynamics NAV or Business Central on-premises, and the Talend jobs extract from them via JDBC using the SQL Server driver.

**Cloud API endpoints (REST/OData).** The GENERIC region bypasses direct database access entirely, instead pulling data from Microsoft Business Central's OData REST APIs. Four regions (BRA, GENERIC, JPN, USA) use REST connectivity in some capacity, but GENERIC is the only one where it is the primary ingestion method. The REST pattern uses tRESTClient to call OData endpoints, tExtractJSONFields to parse responses, and tBigQueryOutput to land the results.

**Salesforce CRM.** The Global layer hosts 15+ Salesforce integration jobs that synchronize warehouse data with the CRM. These jobs use tSalesforceInput and tSalesforceOutput to exchange account mappings, budget data, product catalogs, country mappings, ledger data, conversion rates, and standard cost prices. Salesforce is the primary consumer of the consolidated global datasets and is where commercial teams interact with the integrated data.

**File system and FTP/SFTP.** The file system is used across all 10 regions, primarily as a staging mechanism -- jobs write intermediate data to local disk during processing and clean up via the cleanup_temp_file_storage joblet afterward. The USA region additionally uses FTP/SFTP for the Animalytix integration (VUS_Raw_Animalytix_SFTP), making it the only region with a dedicated file-transfer protocol connection.

The resulting topology looks like a star: BigQuery at the center, MSSQL databases feeding in from the left (source systems), Salesforce consuming from the right (CRM), and REST APIs providing an alternative ingestion path for Business Central cloud tenants.

---

## Connection Inventory

The platform defines six named database connections plus generic connectors for BigQuery, REST, file system, and FTP. All named connections use Talend context variables for parameterization -- host, port, database, schema, and credentials are never hardcoded.

| Connection Name | Type | Host Variable | Regions Used | Jobs Using |
|----------------|------|---------------|--------------|------------|
| connection_BRA_VBR_MSSQL | Microsoft SQL Server | `context.connection_BRA_VBR_MSSQL_Server` | BRA | BRA_VBR_RAW |
| connection_ITA_VAX_MSSQL | Microsoft SQL Server | `context.connection_ITA_VAX_MSSQL_Server` | ITA, SAF, ZAF | ITA_VIT_RAW, ITA_VIT_RAW_Copy |
| connection_JPN_VJP_MSSQL | Microsoft SQL Server | `context.connection_JPN_VJP_MSSQL_Server` | JPN, scratchpad | JPN_VJP_RAW, sandbox, test |
| connection_USA_NPL_MSSQL | Microsoft SQL Server | `context.connection_USA_NPL_MSSQL_Server` | USA | USA_NPL_RAW |
| connection_SAF_VSA_MSSQL | Microsoft SQL Server | `context.connection_SAF_VSA_MSSQL_Server` | ZAF | VSA_Raw_vw_InvoiceDetails_ETL |
| connection_JDBC_Google_BigQuery | JDBC | *(context-driven)* | scratchpad | BigQuery_TEST |
| Google BigQuery | BigQuery native | *(service account)* | All 10 regions | 35+ jobs |
| REST API | HTTP/OData | *(endpoint URLs)* | BRA, GENERIC, JPN, USA | 10 jobs |
| File System | Local/NFS | *(local paths)* | All 10 regions | 34 jobs |
| FTP/SFTP | FTP | *(context-driven)* | USA | VUS_Raw_Animalytix_SFTP |

Several patterns emerge from this inventory:

- **One MSSQL connection per regional ERP.** The naming convention `connection_{REGION}_{SUBSIDIARY}_MSSQL` makes the mapping explicit. BRA connects to VBR (Vaxxinova Brazil), JPN to VJP (Vaxxinova Japan), USA to NPL (Newport Laboratories), ITA to VAX (Vaxxinova Italy), and ZAF to VSA (Vaxxinova South Africa).
- **Cross-region connection reuse is rare but present.** The ITA connection (`connection_ITA_VAX_MSSQL`) is used by jobs in ITA, SAF, and ZAF, suggesting that the Italian MSSQL server may host data for multiple African subsidiaries, or that these jobs were cloned from the Italian template.
- **The JDBC BigQuery connection is a legacy artifact.** Only one scratchpad job (BigQuery_TEST) uses the JDBC connector for BigQuery. All production jobs use the native tBigQueryInput/tBigQueryOutput components, which connect via Google service account authentication rather than JDBC.
- **Context variables enforce environment separation.** Every MSSQL connection resolves its host, port, database, schema, and login from context variables (e.g., `context.connection_BRA_VBR_MSSQL_Server`). This allows the same job to run against development, staging, and production databases by switching the Talend context group.

---

## Job Dependency Graph

The manifest reveals a single, well-defined parent-child orchestration tree rooted in `Global_Clean_Sales_Set`. This is the platform's most critical job -- it assembles the consolidated sales dataset that feeds Salesforce reporting by calling 10 regional child jobs:

```
Global_Clean_Sales_Set
  |-- BIO_Clean_Sales_Set         (BRA - Biovet/Vaxxinova Brazil)
  |-- VIT_Clean_Sales_Set         (ITA - Vaxxinova Italy)
  |-- VDD_Clean_Sales_Set         (DEU - Vaxxinova Germany)
  |-- VJP_Clean_Sales_Set         (JPN - Vaxxinova Japan)
  |-- VUS_Clean_Sales_Set         (USA - Vaxxinova US)
  |-- NPL_Clean_Sales_Set         (USA - Newport Laboratories)
  |-- VDA_Clean_Sales_Set         (DEU - Vaxxinova DACH)
  |-- Legacy_Clean_Sales_Set      (Global - historical/legacy data)
  |-- Forecast_Sales_Set          (Global - forward-looking projections)
  |-- Raw_Sales_Set               (Global - unprocessed baseline)
```

This is the only explicit job-calls-job dependency in the manifest, which means the remaining 241 jobs are either leaf-level jobs (triggered by external schedulers) or orchestrated through mechanisms outside the Talend dependency graph (e.g., cron, Cloud Scheduler, or manual execution).

The dependency structure has two important implications for migration:

1. **Global_Clean_Sales_Set is the natural integration test.** If this job and all 10 children run successfully on the target platform with correct output, it validates the bulk of the clean-layer and global-layer data pipeline.
2. **The flat hierarchy simplifies migration sequencing.** There is only one level of nesting (parent calls children, children do not call further children), so the migration can proceed in two phases: migrate all leaf jobs first, then migrate the orchestration job.

Beyond explicit job calls, the platform uses two shared joblets -- `Vaxxinova_Logging` and `cleanup_temp_file_storage` -- that are embedded in 191-193 jobs each. These are not visible in the job-calls graph because Talend inlines joblet logic at build time, but they represent shared dependencies that must be migrated before any job that uses them.

### Implicit Dependencies: The Shared Reference Data Graph

While the explicit job-calls-job tree is shallow (one level of nesting), the implicit dependency graph created by shared reference tables is much deeper and wider. Every regional clean-layer job depends on at least four global reference tables:

```
Global_Master.Fiscal_Periods
  |-- Every regional *_Clean_Build_Sales_Set job (BRA, DEU, ITA, JPN, USA, ZAF)

Global_Master.Country_Master
  |-- Every regional *_Clean_Build_Sales_Set job
  |-- Budget_Build_Sales_Set
  |-- Prior_Build_Sales_Set

Global_Budget.Budget_Current_Conversion_Rate
  |-- Every regional *_Clean_Build_Sales_Set job (for EUR conversion)

Global_Raw.Salesforce_Account_Mappings
  |-- Every regional *_Clean_Build_Sales_Set job (for CRM linkage)
  |-- Salesforce_Account_v7, Salesforce_Mapped_Accounts_v8 (bidirectional)
```

This means a failure in any Global reference table update job silently corrupts the output of every regional pipeline that runs afterward. The platform has no built-in mechanism to detect stale reference data -- if `Budget_Current_Conversion_Rate` has not been refreshed before a regional Sales_Set build runs, the build will succeed but produce amounts converted at outdated exchange rates.

### The Regional Execution Sequence

Although the platform lacks a formal orchestration scheduler visible in the Talend metadata, the data dependencies imply a required execution sequence:

1. **Phase A -- Reference data refresh.** Global reference table jobs must run first: `Global_Clean_Conversion_Rates_v1`, fiscal period updates, Salesforce mapping pulls.
2. **Phase B -- Raw extraction.** Regional RAW jobs (`BRA_VBR_RAW`, `JPN_VJP_RAW`, `ITA_VIT_RAW`, `DEU_VDD_RAW`, `USA_NPL_RAW`) extract from ERP source systems.
3. **Phase C -- Regional cleaning.** Clean-layer jobs transform raw data using the reference tables from Phase A.
4. **Phase D -- Global consolidation.** `Global_Clean_Sales_Set` calls all regional child jobs and assembles the consolidated dataset.
5. **Phase E -- CRM synchronization.** Salesforce push jobs deliver the consolidated data to the CRM.

If any phase runs out of order -- for example, if a clean job runs before the reference data is refreshed, or if the global consolidation runs before all regional cleans have completed -- the result is silently incorrect data rather than a visible failure.

---

## Data Flow Architecture

Data moves through the platform in four stages, each corresponding to a layer in BigQuery and a category of Talend jobs:

### Stage 1: Source Extraction (Raw Layer)

**Raw jobs** extract data from regional ERP systems and land it in BigQuery's raw-layer datasets with minimal transformation. The naming convention is `{SUBSIDIARY}_Raw_{EntityName}` -- for example, `VBR_Raw_salesInvoiceHeaders` (BRA), `VJP_Raw_salesCreditMemoLines` (JPN), `VUS_Raw_itemLedgerEntries` (USA).

Each region has a single "master RAW" job that coordinates extraction from the MSSQL source: `BRA_VBR_RAW`, `JPN_VJP_RAW`, `ITA_VIT_RAW`, `USA_NPL_RAW`, `DEU_VDD_RAW`. These jobs use the region's named MSSQL connection to read from on-premises ERP views and tables, apply a tMap for basic type conversion and column renaming, and write to BigQuery via tBigQueryOutput.

For the GENERIC region, raw extraction bypasses MSSQL entirely. Jobs like `GEN_BC_Customers_oData` and `GEN_BC_Sales_Invoice_Header_oData` call Business Central's OData REST API, parse the JSON response, and write directly to BigQuery. This represents the platform's evolution toward cloud-native ERP integration.

### Stage 2: Regional Cleaning (Clean Layer)

**Clean jobs** read from the raw layer, apply business logic transformations, and write to clean-layer datasets. The naming convention is `{SUBSIDIARY}_Clean_{EntityName}_{version}` -- for example, `VBR_Clean_SalesInvoiceLines`, `VJA_Clean_Invoice_Set_v7`, `NPL_Clean_Account_v4`.

This is where the bulk of the platform's complexity resides. Clean jobs:

- Join invoice headers to invoice lines (using tHash lookups or tMap joins)
- Enrich records with dimension data (customer names, item descriptions, ledger accounts)
- Apply regional business rules (Brazilian fiscal calculations in tJava, Japanese dimension-set flattening)
- Union parallel data streams (credit memos + invoices via tUnite in JPN)
- Calculate derived fields (margins, exchange-rate-adjusted amounts, cost breakdowns)

Version suffixes (v1 through v26) on job names indicate iterative refinement. For example, `NPL_Clean_Item_v26` has been revised 26 times, suggesting the item-cleaning logic for Newport Laboratories has been a persistent source of business-rule changes.

### Stage 3: Global Consolidation (Global Layer)

**Global jobs** read from the clean layer across multiple regions and produce consolidated cross-region datasets. `Global_Clean_Sales_Set` is the primary example, orchestrating 10 regional Clean_Sales_Set jobs to produce a unified sales dataset. Other Global jobs include:

- `Global_Clean_Conversion_Rates_v1` -- currency conversion rates across all regions
- `Global_Clean_General_Ledger_Entries_v5` -- consolidated ledger entries
- `Global_Clean_Sales_5_Year_Plan` -- long-range planning data
- `Global_Build_Sales_Set` / `Global_Copy_Sales_Set_Test` -- sales set assembly and testing
- `Budget_Build_Sales_Set` / `Budget_Ingest_Sales_Set` -- budgeting pipeline

These jobs tend to be simpler in component count (11.3 components on average vs. 22.4 for BRA) because they primarily aggregate rather than transform.

### Stage 4: CRM Synchronization (Salesforce)

**Salesforce jobs** push curated data from the global layer into Salesforce objects. Fifteen jobs follow the `Salesforce_{EntityName}_{version}` naming convention:

- `Salesforce_Account_v7` -- account master data
- `Salesforce_Budget_v8` -- budget figures
- `Salesforce_Products` -- product catalog
- `Salesforce_Country_Mappings_v9` -- country-to-account mappings
- `Salesforce_Ledger_v6` -- ledger summaries
- `Salesforce_Dated_Conversion_Rates_v3` -- historical exchange rates
- `Salesforce_Standard_Cost_Prices_v1` -- standard costing data

A parallel set of jobs pulls reference data from Salesforce back into BigQuery (`Salesforce_Account_Mappings_v3`, `Salesforce_Mapped_Accounts_v8`), creating a bidirectional sync. This two-way flow means Salesforce is both a consumer and a source of truth for certain mapping tables.

Additionally, several regional jobs sync product data directly to Salesforce (`BIO_Sync_Product_Salesforce_v1` in BRA, `IZO_Sync_Product_Salesforce_v5` in ITA, `VJA_Sync_Product_Salesforce_v3` in JPN), bypassing the Global layer.

---

## Integration Patterns

The platform exhibits four distinct integration patterns, each with its own component profile, data flow characteristics, and migration considerations. Understanding these patterns is essential because the migration strategy for each is fundamentally different.

### Pattern 1: MSSQL Extraction (On-Premises ERP to BigQuery)

This is the oldest and most widespread pattern, used by 5 of the 10 regions (BRA, ITA, JPN, USA, ZAF). The pattern extracts transactional data from on-premises Microsoft Dynamics NAV or Business Central databases running on SQL Server.

**Component chain:** `tPrejob` -> `tMSSqlInput` -> `tMap` -> `tBigQueryOutput` -> `tPostjob` -> `cleanup_temp_file_storage` -> `Vaxxinova_Logging`

**Characteristics:**
- Uses named MSSQL connections (`connection_BRA_VBR_MSSQL`, `connection_JPN_VJP_MSSQL`, etc.) with context-variable-driven host/port/database parameters.
- Reads from SQL Server views (e.g., `VW_Finished_Product`, `VW_Sales`, `VW_Returns` in BRA; `vw_InvoiceDetails` in ZAF) rather than base tables, indicating the ERP teams provide curated extraction views.
- The tMap transformation is minimal -- primarily column renaming, type coercion, and basic filtering. The heavy transformation logic is deferred to the clean layer.
- Data is staged to local disk as CSV before bulk-loading into BigQuery, which is why cleanup_temp_file_storage is required.
- Requires VPN or private network connectivity between the Talend runtime and the on-premises SQL Server instances.

**Migration consideration:** This pattern requires network connectivity planning. If the target platform runs in the cloud, a data gateway, VPN tunnel, or private endpoint must be provisioned for each regional ERP. The extraction views on the SQL Server side are independent of Talend and should remain usable with any replacement ETL tool.

### Pattern 2: REST/OData API Extraction (Business Central Cloud to BigQuery)

This pattern is used exclusively by the GENERIC region's 15 jobs and represents the platform's evolution toward cloud-native ERP integration. It extracts data from Microsoft Business Central's OData REST APIs rather than connecting to on-premises databases.

**Component chain:** `tPrejob` -> `Create_Business_Central_Token` (OAuth) -> `tRESTClient` -> `tExtractJSONFields` -> `tFileInputDelimited` -> `tUnite` -> `tMap` -> `tBigQueryOutput` -> `tPostjob` -> `Vaxxinova_Logging`

**Characteristics:**
- Begins with an OAuth2 authentication flow against Azure AD to obtain a bearer token for the Business Central API.
- Uses tRESTClient (13 instances) to call OData endpoints, with response pagination handled via tJavaFlex (13 instances) -- a component that allows custom Java code to wrap an iteration loop around the REST calls.
- JSON responses are parsed by tExtractJSONFields (17 instances), which extracts individual fields from the nested JSON structure.
- Parsed records are written to intermediate CSV files (tFileInputDelimited reads them back), then unioned via tUnite (13 instances) to combine paginated batches, before a final tMap applies column mapping and type conversion.
- Both direct API and OData protocol variants exist for the same entities (e.g., `GEN_BC_Item` vs. `GEN_BC_Item_oData`), providing fallback extraction pathways.

**Migration consideration:** This is the easiest pattern to migrate because it uses standard HTTP/REST protocols. The OAuth flow, endpoint URLs, and JSON parsing logic must be replicated, but the pattern maps naturally to any modern integration platform's REST connector.

### Pattern 3: BigQuery-to-BigQuery Transformation (Clean Layer)

This pattern accounts for the majority of jobs in the platform (approximately 150 of 251 jobs). It reads from BigQuery raw-layer tables, applies transformation and enrichment logic, and writes to BigQuery clean-layer or global-layer tables.

**Component chain:** `tPrejob` -> `tBigQueryInput` (x1-5 for reference data) -> `tHashOutput` (x1-5) -> `tBigQueryInput` (x1-2 for fact data) -> `tMap` (with tHashInput lookups) -> `tBigQuerySQLRow` (DELETE) -> `tBigQueryOutput` -> `tPostjob` -> `cleanup_temp_file_storage` -> `Vaxxinova_Logging`

**Characteristics:**
- The core of the transformation logic: joins, enrichment, fiscal period derivation, currency conversion, entity tagging, and Salesforce account linkage all happen here.
- Heavy use of the tHash lookup pattern (269 tHashOutput instances platform-wide) to pre-load reference data into in-memory hash maps for efficient lookup joins in tMap.
- Delete-before-insert load pattern ensures idempotent execution: tBigQuerySQLRow deletes existing records in the target table before tBigQueryOutput writes new data.
- Version suffixes on job names (v1 through v26) indicate extensive iterative refinement.
- Regional variations: BRA and JPN use the most tHash lookups; JPN adds tUnite for stream merging; DEU uses context-parameterized table references extensively.

**Migration consideration:** This is the highest-risk pattern to migrate because the transformation logic (tMap expressions, tHash lookups, tJava code) is the most tightly coupled to Talend's execution model. Consider replacing this layer with SQL-based transformations (dbt, Dataform, or BigQuery stored procedures) that operate natively on BigQuery without an external ETL tool.

### Pattern 4: Salesforce CRM Synchronization (Bidirectional)

This pattern synchronizes data between BigQuery and Salesforce CRM, operating in both directions. It is concentrated in the Global region (15+ jobs) with regional variants in BRA, ITA, and JPN.

**Outbound (BigQuery to Salesforce):** `tBigQueryInput` -> `tMap` -> `tSalesforceOutput` (upsert/update)

**Inbound (Salesforce to BigQuery):** `tSalesforceInput` -> `tMap` -> `tBigQueryOutput`

**Characteristics:**
- Outbound jobs push product catalogs (BIO_Sync_Product_Salesforce_v1, IZO_Sync_Product_Salesforce_v5), budget figures (Salesforce_Budget_v8), account data (Salesforce_Account_v7), country mappings, ledger summaries, conversion rates, and standard cost prices.
- Inbound jobs pull Salesforce Account Mappings and Mapped Accounts back into BigQuery, creating a bidirectional dependency loop.
- Product sync jobs include vaccine-specific fields (Species, Subspecies, Hierarchy, Doses) with region-specific product code prefixes (e.g., 'BIO-' for Brazil's Biovet products).
- The tSalesforceOutput component supports both update (modify existing records) and upsert (insert or update based on external ID) operations.
- The bidirectional flow creates a timing dependency: Salesforce mapping tables must be pulled into BigQuery before clean-layer jobs run, and clean-layer output must be pushed to Salesforce after consolidation completes.

**Migration consideration:** The Salesforce integration requires preserving the bidirectional sync timing and the external ID mappings. The target platform must support Salesforce OAuth authentication, bulk API operations, and upsert semantics. The CRM team should be involved in migration testing to validate that field mappings and record matching rules are preserved.

### Context Variables for Parameterization

Every named connection uses Talend context variables to externalize configuration. The convention is `context.connection_{REGION}_{SUBSIDIARY}_MSSQL_{Property}` where Property is one of Server, Port, Database, Schema, or Login. This pattern serves three purposes:

1. **Environment promotion.** The same job binary runs in dev, staging, and production by switching the active context group.
2. **Credential isolation.** Passwords are stored in context variables, not in job XML, reducing the risk of credential leakage in version control.
3. **Operational flexibility.** Server migrations, port changes, and database renames require only a context variable update, not a job rebuild.

The migration target must provide an equivalent mechanism -- environment variables, secret managers, or connection profiles -- to preserve this separation.

### Joblets for Reuse

Two custom joblets enforce operational consistency across the platform:

**Vaxxinova_Logging** is embedded in 77% of all jobs (193 of 251). It records execution metadata to a central BigQuery table, providing the operations team with a single pane of glass for monitoring job health. The joblet is called in the tPostjob phase, ensuring it captures the final status even if the main flow encounters errors.

**cleanup_temp_file_storage** is embedded in 76% of all jobs (191 of 251). It removes temporary files from the local file system after processing completes. This joblet exists because many jobs use a stage-to-disk-then-bulk-load pattern for BigQuery ingestion, writing CSV or JSON files to a temp directory that must be cleaned up to prevent disk space exhaustion on the Talend runtime server.

These joblets are Talend-specific constructs -- reusable sub-job fragments that are inlined at compile time. The migration target must replace them with equivalent shared components: a logging API/function and a cleanup utility.

### Shared Routines

The platform uses 15 Talend routines, all from the standard Talend library: DataOperation, Mathematical, Numeric, Relational, SQLike, StringHandling, TalendDate, TalendString, TalendStringUtil, TalendDataGenerator, DataQuality, DQTechnical, DataMasking, DataQualityDependencies, and DqStringHandling. These routines are referenced inside tMap expressions for string manipulation, date formatting, numeric conversion, and data quality checks. They span virtually every job in the platform (the DataOperation and Mathematical routines alone are referenced by jobs across all 10 regions).

While these are standard Talend functions rather than custom code, they embed platform-specific syntax. Every tMap expression that calls `TalendDate.parseDate()` or `StringHandling.TRIM()` must be translated to the target platform's expression language.

### Delete-Before-Insert Load Pattern

Many clean-layer jobs use tBigQuerySQLRow (180 instances) to execute a DELETE statement against the target table before the main tBigQueryOutput writes new data. This implements an idempotent "full refresh" pattern: each execution replaces the previous dataset entirely. For example, `VDA_Clean_Sales_Set_Delete_Ingest` and `VDD_Clean_Sales_Set_Delete_Ingest` delete existing clean-layer records for their region before inserting the newly transformed data. This pattern ensures that re-runs do not create duplicates but also means that a failed job leaves the target table in an empty or partially loaded state until the next successful run.

### Regional Naming Conventions

Job and dataset names encode their position in the architecture through a consistent prefix system:

- **{SUBSIDIARY}_Raw_** -- raw-layer extraction jobs (e.g., VBR_Raw_salesInvoiceHeaders)
- **{SUBSIDIARY}_Clean_** -- clean-layer transformation jobs (e.g., VJA_Clean_Invoice_Set_v7)
- **Global_Clean_** -- global consolidation jobs (e.g., Global_Clean_Sales_Set)
- **Salesforce_** -- CRM synchronization jobs (e.g., Salesforce_Budget_v8)
- **GEN_BC_** -- GENERIC Business Central API jobs (e.g., GEN_BC_Customers_oData)
- **Copy_of_** / **_Backup** / **_DEBUG** -- development artifacts (e.g., Copy_of_VBR_Clean_Build_Sales_Set)

The subsidiary prefixes map to regions: VBR=Brazil, VDD=Germany, VDA=DACH, VIT=Italy, VJP/VJA=Japan, VUS=USA, NPL=USA (Newport Labs), VSA=South Africa, BIO=Brazil (Biovet), IZO=Italy (IZO subsidiary), DEL=South Africa deletion jobs.

---

## Migration Takeaways

**Migrate connections before jobs.** The six named MSSQL connections, the BigQuery service account, and the Salesforce OAuth credentials must be provisioned and tested in the target platform before any job migration begins. All connection parameters are externalized via context variables, so the migration involves recreating the variable-to-connection mapping in the target's equivalent configuration mechanism (environment variables, secret managers, or connection profiles).

**Preserve the three-layer data architecture.** The raw/clean/global layering in BigQuery is independent of Talend and should survive migration unchanged. The target platform needs to support the same read-transform-write pattern against these three layers, and the dataset naming conventions should be preserved to minimize downstream impact.

**Rebuild the orchestration layer last.** `Global_Clean_Sales_Set` and its 10 child jobs form the only explicit dependency tree. Migrate the leaf jobs (raw and clean layers) first, validate their outputs, then migrate the orchestration job. This sequencing ensures that each child job can be independently tested before it is called by the parent.

**Replace joblets with shared services early.** Vaxxinova_Logging and cleanup_temp_file_storage are embedded in over 75% of jobs. Implementing their equivalents in the target platform (a logging API and a cleanup utility) before migrating any jobs ensures operational parity from day one and avoids having to retrofit logging after migration.

**Plan for MSSQL network connectivity.** The five regional MSSQL connections point to on-premises ERP databases. If the target integration platform runs in the cloud, it will need VPN tunnels, private endpoints, or a data gateway to reach these servers. Network connectivity testing should happen in the connection-provisioning phase, well before job migration.

**Address the bidirectional Salesforce sync carefully.** The Salesforce integration is not a simple one-way push. Account mappings and mapped accounts flow from Salesforce back into BigQuery, creating a dependency loop. The migration must preserve both directions and ensure that the timing of bidirectional syncs does not create race conditions or stale data.

**Evaluate whether the temp-file-staging pattern is still needed.** The ubiquity of cleanup_temp_file_storage (191 jobs) suggests that the platform was originally designed for an era when BigQuery ingestion required staging files to disk. Modern BigQuery APIs support streaming inserts and direct loads from memory. If the target platform can write to BigQuery without staging files, the entire cleanup pattern can be eliminated, simplifying both the architecture and the operational burden.
