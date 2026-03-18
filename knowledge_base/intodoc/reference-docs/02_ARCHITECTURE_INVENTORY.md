# Architecture and Execution Logic

> System Topology, Connection Inventory, Execution Flow, and Job Dependency Patterns

The Vaxxinova Talend platform implements a three-layer data architecture -- **raw**, **clean**, and **global** -- that consolidates financial and operational data from regional ERP systems into a single Google BigQuery warehouse, then pushes curated datasets outward to Salesforce for commercial reporting. The platform connects 11 distinct external systems across 10 geographic regions, orchestrated by a hub-and-spoke job dependency model where a small number of Global-layer jobs call region-specific child jobs to assemble cross-region datasets. Vaxxinova's 257 Talend jobs follow a uniform execution model: each job is a self-contained unit that runs independently, with no embedded scheduling triggers in the Talend metadata.

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

Key patterns from this inventory:

- **One MSSQL connection per regional ERP.** The naming convention `connection_{REGION}_{SUBSIDIARY}_MSSQL` makes the mapping explicit. BRA connects to VBR (Vaxxinova Brazil), JPN to VJP (Vaxxinova Japan), USA to NPL (Newport Laboratories), ITA to VAX (Vaxxinova Italy), and ZAF to VSA (Vaxxinova South Africa).
- **Cross-region connection reuse is rare but present.** The ITA connection (`connection_ITA_VAX_MSSQL`) is used by jobs in ITA, SAF, and ZAF, suggesting that the Italian MSSQL server may host data for multiple African subsidiaries.
- **The JDBC BigQuery connection is a legacy artifact.** Only one scratchpad job (BigQuery_TEST) uses the JDBC connector. All production jobs use native tBigQueryInput/tBigQueryOutput components.
- **Context variables enforce environment separation.** Every MSSQL connection resolves its host, port, database, schema, and login from context variables, allowing the same job to run against different environments by switching the Talend context group.

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

This is the only explicit job-calls-job dependency in the manifest, meaning the remaining 241 jobs are either leaf-level jobs (triggered by external schedulers) or orchestrated through mechanisms outside the Talend dependency graph.

The dependency structure has two important implications:

1. **Global_Clean_Sales_Set is the natural integration test.** If this job and all 10 children run successfully with correct output, it validates the bulk of the clean-layer and global-layer pipeline.
2. **The flat hierarchy simplifies migration sequencing.** There is only one level of nesting (parent calls children, children do not call further children), so the migration can proceed in two phases: migrate all leaf jobs first, then migrate the orchestration job.

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

This means a failure in any Global reference table update job silently corrupts the output of every regional pipeline that runs afterward. The platform has no built-in mechanism to detect stale reference data.

---

## Trigger Patterns and Scheduling

Of the 257 jobs analyzed, 256 are standalone -- they contain no embedded trigger definitions, no cron expressions, and no parent-child invocation metadata within the Talend project XML. The single exception is `Global_Clean_Sales_Set`, which acts as a master orchestrator calling 10 regional Sales_Set jobs.

This near-universal absence of triggers in the metadata does not mean the jobs are run ad hoc. In practice, production ETL pipelines of this scale are scheduled -- but that scheduling is configured in the runtime environment (TAC, a Linux crontab, or a third-party scheduler), not in the Talend job definitions themselves. The metadata tells us how each job is wired internally, but not when or in what order it runs.

**Migration takeaway:** The migration target must provide an external scheduling layer. Since Talend metadata captures no schedule information, the migration team needs to interview operations staff or inspect the TAC/cron configuration to reconstruct the actual production schedule.

---

## Control Flow Patterns

Three control flow mechanisms govern how work progresses within a Talend job:

### SUBJOB_OK Chaining

The dominant pattern across the platform. When a subjob completes successfully, control passes to the next subjob via a `SUBJOB_OK` connector. This creates a linear, sequential pipeline: subjob A finishes, then subjob B starts, then subjob C, and so on. Nearly every job uses this pattern for its primary flow.

The delete-then-insert pattern relies on SUBJOB_OK chaining for correctness: a `tBigQuerySQLRow` subjob executes a DELETE statement, and only after it succeeds (SUBJOB_OK) does the next subjob write new data via `tBigQueryOutput`. This ordering is critical -- if the DELETE and INSERT ran concurrently, the INSERT could write data that the DELETE then removes.

### COMPONENT_OK Sequencing

Within a single subjob, individual components are linked by `COMPONENT_OK` connectors. This is the intra-subjob equivalent of SUBJOB_OK. It is especially visible in the `tPrejob` and `tPostjob` phases. For instance, in the GENERIC `GEN_BC_*` jobs, the `tPrejob` phase uses COMPONENT_OK to sequence: loading Business Central secret keys, authenticating via OAuth to Azure AD, then proceeding to REST API extraction.

The distinction between SUBJOB_OK and COMPONENT_OK matters for migration because some target platforms (e.g., Airflow) operate at the task level and do not model intra-task sequencing. COMPONENT_OK chains within a subjob should be collapsed into a single atomic task.

### RUN_IF Conditional Branching

Some jobs include conditional branches that execute only when a runtime condition is met. The `RUN_IF` connector evaluates a Java expression and routes control flow accordingly -- for example, deciding whether to process the next page of API results based on `context.nextPageURL != null`.

RUN_IF conditions are less common than SUBJOB_OK chains but appear in the more complex jobs, particularly those handling API pagination or multiple data streams.

### SUBJOB_ERROR (Failure Handling)

SUBJOB_ERROR triggers fire when a subjob encounters an unhandled exception. They are Talend's primary mechanism for routing execution to error-handling logic. As documented across the platform, SUBJOB_ERROR triggers are almost entirely absent from the Vaxxinova codebase. The 229 jobs with no error handling have no SUBJOB_ERROR connections, meaning exceptions propagate uncaught to the Talend runtime.

The near-total absence of SUBJOB_ERROR triggers means the migration team does not need to translate complex error routing logic -- there is almost none to translate. However, the migration should add error handling at every subjob boundary.

---

## Subjob Structure and Trigger Types

Talend jobs are composed of subjobs -- groups of connected components that execute as a unit. Understanding the three trigger types that connect subjobs is essential for accurately translating Talend control flow.

### Trigger Type 1: SUBJOB_OK (Success Continuation)

SUBJOB_OK is by far the most common trigger. When a subjob completes without throwing an unhandled exception, control passes to the next subjob. The typical chain:

```
[Subjob A: Load context] --SUBJOB_OK--> [Subjob B: DELETE existing data]
    --SUBJOB_OK--> [Subjob C: Extract from source]
    --SUBJOB_OK--> [Subjob D: Transform via tMap]
    --SUBJOB_OK--> [Subjob E: Write to BigQuery]
    --SUBJOB_OK--> [Subjob F: Log completion]
```

Each arrow represents a hard dependency. In the target platform, each SUBJOB_OK link should translate to a dependency edge in a DAG or a sequential step in a pipeline definition.

### Trigger Type 2: COMPONENT_OK (Intra-Subjob Sequencing)

A finer-grained sequencing mechanism within a single subjob:

```
[tPrejob_1] --COMPONENT_OK--> [tContextLoad: Load secrets]
    --COMPONENT_OK--> [tJava: Build OAuth token request]
    --COMPONENT_OK--> [tRESTClient: Acquire BC token]
    --COMPONENT_OK--> [tJava: Store token in context variable]
```

### Trigger Type 3: SUBJOB_ERROR (Failure Handling)

Almost entirely absent from the Vaxxinova codebase. Exceptions propagate uncaught, meaning jobs fail silently without cleanup, logging, or compensating actions.

---

## Execution Order and Implicit Dependencies

The platform's jobs fall into three logical tiers that must execute in order:

### The Three-Tier Execution Model

**Tier 1: Raw Extraction.** Jobs like `BRA_VBR_RAW`, `JPN_VJP_RAW`, and the GENERIC `GEN_BC_*` family extract data from source systems and write to BigQuery raw-layer datasets. These must run first because every downstream job depends on fresh raw data.

**Tier 2: Regional Clean Transformation.** Jobs like `BIO_Clean_Invoice_Set_v5`, `VJP_Clean_Build_Sales_Set`, and `VUS_Clean_Build_Sales_Set` read from the raw layer, apply enrichment and business logic, and write to regional clean-layer datasets. These cannot run until their corresponding Tier 1 jobs have completed.

**Tier 3: Global Consolidation.** Jobs in the Global region, particularly `Global_Clean_Sales_Set` and `Global_Build_Sales_Set`, read from regional clean datasets and produce unified analytics tables. These must run after all regional Tier 2 jobs have completed.

### The Regional Execution Sequence

The data dependencies imply a required five-phase execution sequence:

1. **Phase A -- Reference data refresh.** Global reference table jobs must run first: `Global_Clean_Conversion_Rates_v1`, fiscal period updates, Salesforce mapping pulls.
2. **Phase B -- Raw extraction.** Regional RAW jobs extract from ERP source systems.
3. **Phase C -- Regional cleaning.** Clean-layer jobs transform raw data using the reference tables from Phase A.
4. **Phase D -- Global consolidation.** `Global_Clean_Sales_Set` calls all regional child jobs and assembles the consolidated dataset.
5. **Phase E -- CRM synchronization.** Salesforce push jobs deliver the consolidated data to the CRM.

If any phase runs out of order -- for example, if a clean job runs before the reference data is refreshed -- the result is silently incorrect data rather than a visible failure.

### Cross-Tier Data Dependencies

The implicit dependencies between tiers are expressed entirely through shared BigQuery tables:

- `BRA_VBR_RAW` writes to `bra_vbr_raw.salesInvoices`
- `BIO_Clean_Invoice_Set_v5` reads from `bra_vbr_raw.salesInvoices`
- `Global_Build_Sales_Set` reads from `bra_vbr_clean.Sales_Set`

None of these dependencies are encoded in Talend's trigger or invocation metadata. The current platform relies on scheduling timing (e.g., Tier 1 runs at 2 AM, Tier 2 at 4 AM, Tier 3 at 6 AM) to ensure correct ordering. This timing-based approach is fragile.

### Reference Data Dependencies

Beyond the three-tier model, many clean-layer jobs depend on globally shared reference data tables:

- `Global_Master.Fiscal_Periods` -- used by nearly every Sales_Set builder
- `Global_Raw.Salesforce_Account_Mappings` -- links ERP customers to Salesforce accounts
- `Global_Raw.Salesforce_Country_Mappings` -- standardizes country codes
- `Global_Budget.Budget_Current_Conversion_Rate` -- currency conversion for multi-currency reporting

These reference tables are updated by their own jobs, which must complete before any regional clean job that joins against them. This creates a fourth implicit dependency tier: reference data refresh, which should logically sit between Tier 1 and Tier 2.

---

## Complex Control Flow Examples

### Example 1: Simple Linear Flow -- `VIT_Clean_Sales_Set` (ITA)

Italy's Sales_Set builder follows a straightforward SUBJOB_OK chain with no branching:

```
tPrejob [Load context]
  --SUBJOB_OK--> tBigQuerySQLRow [DELETE FROM ita_vit_clean.Sales_Set WHERE ...]
  --SUBJOB_OK--> tBigQueryInput [Read from ita_vit_raw.invoices]
                   --> tMap [Enrich with Fiscal_Periods, Country_Master]
                   --> tBigQueryOutput [Write to ita_vit_clean.Sales_Set]
  --SUBJOB_OK--> tPostjob [cleanup_temp_file_storage + Vaxxinova_Logging]
```

This 4-subjob chain is representative of the simplest production jobs. Each subjob contains 2-4 components linked by COMPONENT_OK.

### Example 2: Multi-Stream Flow -- `JPN_VJP_RAW` (JPN)

Japan's raw extraction consolidates multiple entity extractions into a single job:

```
tPrejob [Load context, establish MSSQL connection]
  --SUBJOB_OK--> Subjob A: [tMSSqlInput(salesInvoices) --> tMap --> tBigQueryOutput]
  --SUBJOB_OK--> Subjob B: [tMSSqlInput(salesCreditMemos) --> tMap --> tBigQueryOutput]
  --SUBJOB_OK--> Subjob C: [tMSSqlInput(generalLedgerEntries) --> tMap --> tBigQueryOutput]
  --SUBJOB_OK--> Subjob D: [tMSSqlInput(items) --> tMap --> tBigQueryOutput]
  --SUBJOB_OK--> Subjob E: [tMSSqlInput(customers) --> tMap --> tBigQueryOutput]
  --SUBJOB_OK--> tPostjob [cleanup]
```

The five extraction subjobs are chained sequentially but logically independent -- a prime candidate for parallelization during migration.

### Example 3: Branching Flow with RUN_IF -- `GEN_BC_SalesInvoices` (GENERIC)

The GENERIC Business Central extractor uses RUN_IF for API pagination:

```
tPrejob [Load BC secrets, acquire OAuth token]
  --SUBJOB_OK--> Subjob A: [tRESTClient(initial page) --> tExtractJSONFields --> tBigQueryOutput]
  --RUN_IF(hasNextPage)--> Subjob B: [tJava(update page URL) --> tRESTClient(next page) --> ...]
  --RUN_IF(hasNextPage)--> [Loop back to Subjob B]
  --SUBJOB_OK--> tPostjob [Vaxxinova_Logging]
```

The RUN_IF condition evaluates `context.nextPageURL != null`. This loop-via-conditional-branching pattern must be translated to the target platform's iteration construct.

---

## Integration Patterns

The platform exhibits four distinct integration patterns, each with its own component profile and migration considerations.

### Pattern 1: MSSQL Extraction (On-Premises ERP to BigQuery)

The oldest and most widespread pattern, used by 5 of 10 regions (BRA, ITA, JPN, USA, ZAF).

**Component chain:** `tPrejob` -> `tMSSqlInput` -> `tMap` -> `tBigQueryOutput` -> `tPostjob` -> `cleanup_temp_file_storage` -> `Vaxxinova_Logging`

**Characteristics:**
- Uses named MSSQL connections with context-variable-driven parameters.
- Reads from SQL Server views rather than base tables.
- Minimal tMap transformation -- primarily column renaming, type coercion, and basic filtering.
- Data is staged to local disk as CSV before bulk-loading into BigQuery.
- Requires VPN or private network connectivity.

**Migration consideration:** Requires network connectivity planning. If the target platform runs in the cloud, a data gateway, VPN tunnel, or private endpoint must be provisioned for each regional ERP.

### Pattern 2: REST/OData API Extraction (Business Central Cloud to BigQuery)

Used exclusively by the GENERIC region's 15 jobs.

**Component chain:** `tPrejob` -> `Create_Business_Central_Token` (OAuth) -> `tRESTClient` -> `tExtractJSONFields` -> `tFileInputDelimited` -> `tUnite` -> `tMap` -> `tBigQueryOutput` -> `tPostjob` -> `Vaxxinova_Logging`

**Characteristics:**
- Begins with an OAuth2 authentication flow against Azure AD.
- Handles API response pagination via tJavaFlex.
- Both direct API and OData protocol variants exist for the same entities.

**Migration consideration:** Easiest pattern to migrate -- uses standard HTTP/REST protocols that map naturally to any modern integration platform.

### Pattern 3: BigQuery-to-BigQuery Transformation (Clean Layer)

Accounts for approximately 150 of 257 jobs.

**Component chain:** `tPrejob` -> `tBigQueryInput` (reference data) -> `tHashOutput` -> `tBigQueryInput` (fact data) -> `tMap` (with tHashInput lookups) -> `tBigQuerySQLRow` (DELETE) -> `tBigQueryOutput` -> `tPostjob` -> `cleanup_temp_file_storage` -> `Vaxxinova_Logging`

**Characteristics:**
- Contains the core transformation logic: joins, enrichment, fiscal period derivation, currency conversion, entity tagging, and Salesforce account linkage.
- Heavy use of the tHash lookup pattern (269 tHashOutput instances platform-wide).
- Delete-before-insert load pattern ensures idempotent execution.
- Version suffixes on job names (v1 through v26) indicate extensive iterative refinement.

**Migration consideration:** Highest-risk pattern because the transformation logic is most tightly coupled to Talend's execution model. Consider replacing with SQL-based transformations (dbt, Dataform, or BigQuery stored procedures).

### Pattern 4: Salesforce CRM Synchronization (Bidirectional)

Concentrated in the Global region (15+ jobs) with regional variants in BRA, ITA, and JPN.

**Outbound (BigQuery to Salesforce):** `tBigQueryInput` -> `tMap` -> `tSalesforceOutput` (upsert/update)

**Inbound (Salesforce to BigQuery):** `tSalesforceInput` -> `tMap` -> `tBigQueryOutput`

**Characteristics:**
- Outbound jobs push product catalogs, budget figures, account data, country mappings, ledger summaries, conversion rates, and standard cost prices.
- Inbound jobs pull Salesforce Account Mappings and Mapped Accounts back into BigQuery.
- Product sync jobs include vaccine-specific fields with region-specific product code prefixes.
- Bidirectional flow creates timing dependencies.

**Migration consideration:** Requires preserving the bidirectional sync timing and external ID mappings. The CRM team should be involved in migration testing.

---

## Most Complex Jobs

Seven jobs stand out for their structural complexity, measured by component count and control flow sophistication:

### 1. Copy_of_VBR_Clean_Build_Sales_Set (BRA) -- 94 components

The most complex job in the entire platform. Constructs the Brazil Sales_Set from Business Central data, enriching from 26 input sources and writing to 17 outputs. Relies heavily on tHash/tJava patterns -- 13 tFixedFlowInput and 14 tHashOutput components load reference data into memory.

### 2. VBR_Raw_salesCreditMemos_Old (BRA)

Legacy raw extraction with GUID normalization, null-safe date parsing, and multiple output paths. The "Old" suffix indicates a legacy variant maintained alongside a newer version.

### 3. VBR_Raw_salesInvoices_Old (BRA)

Invoice counterpart to the credit memos job. Both "Old" variants persist alongside newer Business Central API-based replacements, reflecting Brazil's ongoing ERP migration.

### 4. VUS_Raw_salesCreditMemos (USA)

Complex due to the breadth of fields extracted from Business Central and the multi-step REST API pagination and JSON parsing logic.

### 5. VUS_Raw_salesInvoices (USA)

Invoice extraction for the US subsidiary, processing both direct VUS sales and NPL legacy data with conditional routing.

### 6. VBR_Raw_generalLedgerEntries (BRA)

GL entries spanning multiple account types, posting groups, and dimension combinations. Feeds the production order costing pipeline unique to Brazil.

### 7. JPN_VJP_RAW (JPN)

Japan's consolidated raw extraction combining multiple entity extractions into a single monolithic job.

### Complexity Factors

- High component counts (60-94 per job) driven by reference data lookups
- Multiple data streams joined within a single job
- Legacy/modern coexistence of MSSQL-based and Business Central API-based paths
- Deep enrichment chains (10+ reference table lookups)
- Delete-then-insert idempotency patterns

---

## Context Variables for Parameterization

Every named connection uses Talend context variables to externalize configuration. The convention is `context.connection_{REGION}_{SUBSIDIARY}_MSSQL_{Property}` where Property is one of Server, Port, Database, Schema, or Login. This pattern serves three purposes:

1. **Environment promotion.** The same job binary runs in dev, staging, and production by switching the active context group.
2. **Credential isolation.** Passwords are stored in context variables, not in job XML.
3. **Operational flexibility.** Server migrations, port changes, and database renames require only a context variable update.

The migration target must provide an equivalent mechanism -- environment variables, secret managers, or connection profiles.

---

## Joblets and Shared Components

Two custom joblets enforce operational consistency across the platform:

**Vaxxinova_Logging** is embedded in 77% of all jobs (193 of 251). It records execution metadata to a central BigQuery table. Called in the tPostjob phase, it captures final status even if the main flow encounters errors.

**cleanup_temp_file_storage** is embedded in 76% of all jobs (191 of 251). It removes temporary files from the local file system after processing. This joblet exists because many jobs use a stage-to-disk-then-bulk-load pattern for BigQuery ingestion.

These joblets are Talend-specific constructs -- reusable sub-job fragments inlined at compile time. The migration target must replace them with equivalent shared components.

### Shared Routines

The platform uses 15 Talend routines, all from the standard library: DataOperation, Mathematical, Numeric, Relational, SQLike, StringHandling, TalendDate, TalendString, TalendStringUtil, TalendDataGenerator, DataQuality, DQTechnical, DataMasking, DataQualityDependencies, and DqStringHandling. These are referenced inside tMap expressions for string manipulation, date formatting, numeric conversion, and data quality checks. Every tMap expression that calls `TalendDate.parseDate()` or `StringHandling.TRIM()` must be translated to the target platform's expression language.

---

## Region Comparison

| Region | Jobs | Standalone | Orchestrator | Components | Avg/Job | Dominant Pattern |
|--------|------|------------|--------------|------------|---------|------------------|
| BRA | 75 | 75 | 0 | 1,592 | 21.2 | BigQuery + tHash lookups |
| USA | 61 | 61 | 0 | 1,126 | 18.5 | BigQuery + tHash + file ingestion |
| Global | 43 | 42 | 1 | 476 | 11.1 | Orchestration + Salesforce sync |
| JPN | 36 | 36 | 0 | 872 | 24.2 | BigQuery + tHash + tUnite merging |
| GENERIC | 15 | 15 | 0 | 211 | 14.1 | REST/OData API ingestion |
| DEU | 12 | 12 | 0 | 117 | 9.8 | Lean BigQuery ETL |
| ITA | 6 | 6 | 0 | 66 | 11.0 | BigQuery + Salesforce output |
| scratchpad | 5 | 5 | 0 | 32 | 6.4 | Ad-hoc testing |
| ZAF | 3 | 3 | 0 | 30 | 10.0 | BigQuery ETL |
| SAF | 1 | 1 | 0 | 6 | 6.0 | MSSQL-to-BigQuery extraction |
| **Total** | **257** | **256** | **1** | **4,528** | **17.6** | -- |

Japan has the highest average component density at 24.2 components per job. Brazil is the largest region by total component count (1,592). The Global region's single orchestrator job (`Global_Clean_Sales_Set`) is the only job that invokes other jobs.

---

## Delete-Before-Insert Load Pattern

Many clean-layer jobs use tBigQuerySQLRow (180 instances) to execute a DELETE statement against the target table before the main tBigQueryOutput writes new data. This implements an idempotent "full refresh" pattern. For example, `VDA_Clean_Sales_Set_Delete_Ingest` deletes existing clean-layer records before inserting newly transformed data. The pattern ensures re-runs do not create duplicates but also means a failed job leaves the target table empty or partially loaded.

---

## Regional Naming Conventions

Job and dataset names encode their position in the architecture through a consistent prefix system:

- **{SUBSIDIARY}_Raw_** -- raw-layer extraction jobs (e.g., VBR_Raw_salesInvoiceHeaders)
- **{SUBSIDIARY}_Clean_** -- clean-layer transformation jobs (e.g., VJA_Clean_Invoice_Set_v7)
- **Global_Clean_** -- global consolidation jobs (e.g., Global_Clean_Sales_Set)
- **Salesforce_** -- CRM synchronization jobs (e.g., Salesforce_Budget_v8)
- **GEN_BC_** -- GENERIC Business Central API jobs (e.g., GEN_BC_Customers_oData)
- **Copy_of_** / **_Backup** / **_DEBUG** -- development artifacts

The subsidiary prefixes map to regions: VBR=Brazil, VDD=Germany, VDA=DACH, VIT=Italy, VJP/VJA=Japan, VUS=USA, NPL=USA (Newport Labs), VSA=South Africa, BIO=Brazil (Biovet), IZO=Italy (IZO subsidiary), DEL=South Africa deletion jobs.

---

## Migration Takeaways

**Migrate connections before jobs.** The six named MSSQL connections, the BigQuery service account, and the Salesforce OAuth credentials must be provisioned and tested before any job migration begins.

**Preserve the three-layer data architecture.** The raw/clean/global layering in BigQuery is independent of Talend and should survive migration unchanged.

**Rebuild the orchestration layer last.** Migrate leaf jobs (raw and clean layers) first, validate their outputs, then migrate the orchestration job `Global_Clean_Sales_Set`.

**Replace joblets with shared services early.** Vaxxinova_Logging and cleanup_temp_file_storage are embedded in 75%+ of jobs. Implementing equivalents in the target platform before migrating individual jobs ensures operational parity from day one.

**Map SUBJOB_OK chains to the target orchestration model.** Most modern platforms use DAG-based dependencies. Each SUBJOB_OK link becomes a dependency edge. The delete-then-insert patterns must preserve strict ordering.

**Translate RUN_IF conditions to conditional logic.** Document every RUN_IF condition's Java expression before migration.

**Decompose monolithic jobs.** Jobs like Copy_of_VBR_Clean_Build_Sales_Set (94 components) and JPN_VJP_RAW are candidates for decomposition into smaller, modular units.

**Reconstruct Global_Clean_Sales_Set orchestration.** The 10 regional jobs it calls are independent and can run concurrently on the target platform, with a final consolidation step that waits for all to complete.

**Replace timing-based dependencies with sensor-based triggering.** Instead of scheduling Tier 2 to run "2 hours after Tier 1 starts," implement data sensors that trigger downstream jobs only when upstream BigQuery tables have been updated.

**Plan for MSSQL network connectivity.** If the target platform runs in the cloud, VPN tunnels, private endpoints, or a data gateway must be provisioned for each regional ERP.

**Address the bidirectional Salesforce sync carefully.** The migration must preserve both directions and ensure timing of bidirectional syncs does not create race conditions.

**Evaluate whether temp-file-staging is still needed.** If the target platform supports in-memory streaming or direct BigQuery loads, the cleanup pattern may be unnecessary.

---

*Generated by intodoc -- documentation automation for integration platforms.*
