# Component Inventory

Vaxxinova's Talend integration platform is the data backbone for a global animal-health company operating across ten geographic regions. The platform comprises **251 Talend jobs** containing a combined **4,528 component instances**, averaging 18 components per job. These jobs extract transactional data from regional ERP systems (Microsoft Dynamics NAV/Business Central and legacy MSSQL databases), transform it through a multi-layer warehouse in Google BigQuery, and push curated data into Salesforce for commercial reporting. Understanding the component landscape is the essential first step for any migration, because the mix of component types reveals which integration patterns the team relies on and where platform-specific lock-in exists.

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

Grouping the 4,528 component instances into functional categories reveals the platform's architectural priorities. Five categories emerge, each reflecting a distinct role in the ETL lifecycle.

### Input Components (1,070 instances, 24%)

Input components read data from external systems and feed it into the transformation pipeline. This category includes:

- **tBigQueryInput** (619) -- the dominant input, reading from the BigQuery data warehouse across raw, clean, and global layers.
- **tFixedFlowInput** (228) -- injects hardcoded or context-variable-derived values (region codes, timestamps, version identifiers) into flows.
- **tMSSqlInput** (found in BRA, ITA, JPN, SAF, USA, ZAF) -- reads from on-premises Microsoft SQL Server ERP databases.
- **tRESTClient** (13) -- calls REST/OData endpoints, primarily in the GENERIC region for Business Central API extraction.
- **tFileInputDelimited** (87) -- reads CSV and delimited files, used in GENERIC (26 instances) and USA (61 instances) for file-based ingestion.
- **tFileInputJSON** (3) -- reads JSON files, used only in scratchpad test jobs.
- **tSalesforceInput** (24) -- reads from Salesforce CRM objects, concentrated in the Global region for account and mapping data.
- **tHashInput** (176+) -- reads from in-memory hash tables populated earlier in the same job, enabling lookup joins without additional BigQuery queries.

The input category's dominance by tBigQueryInput (58% of all inputs) confirms that the platform is primarily a warehouse-to-warehouse transformation system, with external source extraction (MSSQL, REST, files) accounting for the minority of data reads.

### Transformation Components (745 instances, 16%)

Transformation components reshape, filter, enrich, and join data in flight:

- **tMap** (565) -- the universal transformation workhorse, performing field mapping, filtering, type coercion, expression evaluation, and lookup joins.
- **tJava** (180) -- custom Java code for logic that cannot be expressed declaratively in tMap, including date arithmetic, conditional branching, and string manipulation.

The 3:1 ratio of tMap to tJava indicates that most transformation logic is declarative (expressible in tMap's visual editor), but a significant minority (24%) requires imperative Java code -- predominantly in Brazil (83 tJava) and Japan (44 tJava).

### Output Components (716 instances, 16%)

Output components write transformed data to target systems:

- **tBigQueryOutput** (360) -- writes to BigQuery datasets across all layers.
- **tHashOutput** (269) -- writes to in-memory hash tables for downstream lookup joins within the same job.
- **tBigQuerySQLRow** (180 in total, though this component serves both output and control roles) -- executes SQL statements (often DELETE) against BigQuery.
- **tFileOutputDelimited** (small count in scratchpad) -- writes CSV files, used primarily in test scenarios.
- **tSalesforceOutput** (found in ITA and Global) -- writes to Salesforce CRM objects for product sync and account updates.
- **tLogRow** (found in scratchpad and DEBUG variants) -- writes to console/log for diagnostic output.

### Control Components (575 instances, 13%)

Control components manage job lifecycle, execution flow, and operational housekeeping:

- **tPrejob** (191+) -- triggers pre-execution setup logic (context variable initialization, temp directory creation).
- **tPostjob** (191) -- triggers post-execution cleanup and logging.
- **cleanup_temp_file_storage** (191) -- custom joblet for removing temporary files after processing.
- **tUnite** (46+ in JPN, 13 in GENERIC) -- merges multiple input streams into a single output (equivalent to SQL UNION ALL).

### Infrastructure Components (193 instances, 4%)

Infrastructure components provide cross-cutting operational services:

- **Vaxxinova_Logging** (193) -- custom joblet recording execution metadata (start/end timestamps, row counts, status) to a central logging table. Present in 77% of all jobs.

The remaining instances are miscellaneous components (tExtractJSONFields, tJDBCInput, tGSGet, etc.) that appear in small numbers for specialized use cases.

| Category | Instances | % of Total | Key Components |
|----------|-----------|-----------|----------------|
| Input | 1,070 | 24% | tBigQueryInput (619), tFixedFlowInput (228), tHashInput (176+) |
| Transformation | 745 | 16% | tMap (565), tJava (180) |
| Output | 716 | 16% | tBigQueryOutput (360), tHashOutput (269), tBigQuerySQLRow (partial) |
| Control | 575 | 13% | tPrejob (191), tPostjob (191), cleanup_temp_file_storage (191) |
| Infrastructure | 193 | 4% | Vaxxinova_Logging (193) |
| Other/Mixed | 1,229 | 27% | tBigQuerySQLRow (partial), tExtractJSONFields, tFileInputDelimited, etc. |

---

## Region-by-Region Comparison

The ten regions vary enormously in scale. Brazil and the USA together account for more than half of all jobs and components, while South Africa (SAF) has a single job that serves as a proof-of-concept extraction.

| Region | Jobs | Components | Avg/Job | Dominant Pattern | Notable Types |
|---------|------|------------|---------|------------------|---------------|
| BRA | 71 | 1,592 | 22.4 | BigQuery + tHash lookups | tHashOutput (153), tHashInput (120), tJava (83) |
| USA | 61 | 1,126 | 18.5 | BigQuery + tHash lookups + file ingestion | tFileInputDelimited (61), tHashOutput (65), tJava (52) |
| Global | 42 | 476 | 11.3 | Orchestration + Salesforce sync | tSalesforceInput (24), tPrejob (36) |
| JPN | 36 | 872 | 24.2 | BigQuery + tHash + tUnite merging | tUnite (46), tHashOutput (45), tJava (44) |
| GENERIC | 15 | 211 | 14.1 | REST/OData API ingestion | tRESTClient (13), tExtractJSONFields (17), tFileInputDelimited (26) |
| DEU | 12 | 117 | 9.8 | Lean BigQuery ETL | Standard BigQuery + logging only |
| ITA | 6 | 66 | 11.0 | BigQuery + Salesforce output | tSalesforceOutput (2) |
| scratchpad | 4 | 32 | 8.0 | Ad-hoc testing | tLogRow (3), tFileInputJSON (3), tMSSqlInput (2) |
| ZAF | 3 | 30 | 10.0 | BigQuery ETL | tHashInput (2), standard pattern |
| SAF | 1 | 6 | 6.0 | MSSQL-to-BigQuery extraction | tMSSqlInput (1) |

Key observations from the table:

- **BRA and JPN have the highest component density** (22 and 24 components per job respectively), driven by extensive use of tHash lookup patterns that require paired tHashOutput/tHashInput components for every reference table.
- **Global jobs are lightweight** (11 components on average) because they primarily orchestrate calls to regional child jobs and push consolidated data to Salesforce -- they do not perform heavy transformations themselves.
- **GENERIC is architecturally distinct** from every other region. It is the only group that relies primarily on REST API calls (tRESTClient) and JSON parsing (tExtractJSONFields) rather than direct database reads. These jobs pull data from Microsoft Business Central's OData endpoints.

---

## Architectural Patterns

### The Standard Job Skeleton

Across the platform, production jobs follow a consistent six-phase skeleton. Recognizing this pattern is critical for migration because it means the target platform must support equivalent lifecycle hooks:

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

**Phase 1 -- Prejob setup.** tPrejob triggers before the main flow starts. Jobs use this phase to initialize context variables (region code, execution timestamp, BigQuery project ID) and to create temporary directories on the local file system.

**Phase 2 -- Source extraction.** The source component varies by region: tBigQueryInput for warehouse-to-warehouse transforms, tMSSqlInput for on-premises ERP extraction (BRA_VBR_RAW, JPN_VJP_RAW, USA_NPL_RAW), and tRESTClient for OData API ingestion (GEN_BC_Customers_oData, GEN_BC_Item_oData).

**Phase 3 -- Transformation.** tMap performs field mapping, type conversion, and lookup joins. Complex jobs chain multiple tMaps, each narrowing or enriching the data. In BRA and JPN, tHashOutput/tHashInput pairs pre-load reference data that tMap consumes via lookup connections.

**Phase 4 -- Target load.** tBigQueryOutput writes results to the appropriate BigQuery dataset. The naming convention encodes the data layer: `raw_*` datasets for phase-2 outputs, `clean_*` for phase-3 outputs, and `global_*` for cross-region consolidated tables.

**Phase 5 -- Cleanup.** cleanup_temp_file_storage removes any CSV or JSON files staged to local disk during processing. This pattern exists because several jobs write intermediate results to disk before bulk-loading them into BigQuery.

**Phase 6 -- Logging.** Vaxxinova_Logging writes a row to the central execution log with the job name, start/end timestamps, row counts, and success/failure status. This custom joblet is present in 193 of 251 jobs (77%), meaning roughly a quarter of jobs -- mostly in the scratchpad and older BRA jobs -- lack standardized logging.

### Anatomy of a Real Job: VBR_Clean_Ingest_Sales_Set

To make the skeleton concrete, consider `VBR_Clean_Ingest_Sales_Set` from the BRA region (approximately 30 components). This job illustrates every phase of the standard pattern:

1. **tPrejob** initializes context variables: `parameter_BRA_Raw_Env` (resolves to `bra_vbr_raw` in PROD), execution timestamp, and BigQuery project ID.
2. **tBigQueryInput** (x5) reads five reference tables: customer dimension, item master, payment terms, shipment methods, and fiscal periods -- each flowing into a paired **tHashOutput** to build in-memory lookup maps.
3. **tBigQueryInput** (x2) reads the main fact tables: `salesInvoices` and `salesCreditMemos` from the raw layer.
4. **tMap** (x3) joins the invoice/credit memo streams to the five hash lookups, applies fiscal period derivation, currency conversion (BRL to EUR using Budget_Current_Conversion_Rate), and entity tagging ('Biovet').
5. **tBigQuerySQLRow** executes `DELETE FROM bra_vbr_clean.Sales_Set WHERE Entity = 'Biovet'` to clear the target before loading.
6. **tBigQueryOutput** (x2) writes the transformed Sales_Set to both the regional clean dataset (`bra_vbr_clean.Sales_Set`) and the global consolidation dataset (`Global_Clean.Sales_Set`).
7. **tPostjob** triggers **cleanup_temp_file_storage** (removes any staged CSV files) and **Vaxxinova_Logging** (records execution metadata).

This pattern -- pre-load reference data into hash maps, read fact tables, join via tMap, delete-then-insert into target, clean up and log -- is the canonical template that approximately 80% of clean-layer jobs follow across all regions. Variations exist (Japan adds tUnite for stream merging, USA adds tGSGet for SFTP file retrieval), but the skeleton remains recognizable.

### The tHash Lookup Pattern

The most distinctive Talend-specific pattern in this codebase is the paired use of tHashOutput and tHashInput for in-memory reference data. Consider `VBR_Clean_Ingest_Sales_Set` (BRA):

1. A tBigQueryInput reads the customer dimension table.
2. A tHashOutput stores the result in an in-memory hash map keyed by customer ID.
3. A second tBigQueryInput reads the invoice fact table.
4. A tMap joins invoice rows to the customer hash via a tHashInput lookup connection.

This pattern avoids repeated BigQuery queries and is used 269 times across the platform (tHashOutput count). It is most prevalent in BRA (153 instances) and USA (65 instances). The migration target must provide an equivalent mechanism -- either in-memory caching, subquery joins, or pre-materialized lookup tables.

### The tUnite Merge Pattern

Japan jobs make heavy use of tUnite (46 instances), which merges multiple input streams with compatible schemas into a single output stream. This is functionally equivalent to a SQL UNION ALL. For example, `VJA_Clean_Invoice_Set_v7` unions credit memo lines and invoice lines before applying a common tMap transformation. This pattern simplifies downstream processing but means the migration must preserve stream-merging semantics.

---

## Notable Regional Differences

### Brazil (BRA): The Largest and Most Complex Region

With 71 jobs and 1,592 components, Brazil dominates the platform. BRA jobs are distinguished by:

- **Extensive tHash usage** (153 tHashOutput + 120 tHashInput = 273 hash components), reflecting a data model with many reference dimensions (customers, items, payment terms, shipment methods, fiscal documents).
- **Heavy tJava presence** (83 instances), used to implement Brazil-specific fiscal logic -- tax entry calculations (VBR_Raw_fiscalDocumentTaxEntryFNX), currency formatting, and date manipulations that accommodate Brazilian fiscal calendar requirements.
- **Production order tracking** -- a suite of jobs (VBR_Clean_Production_Order_*, VBR_Clean_CBR_*) that have no equivalent in most other regions, reflecting manufacturing operations specific to the Brazilian subsidiary.

### Japan (JPN): The Highest Component Density

JPN jobs average 24.2 components each -- the highest density in the platform. This is driven by:

- **tUnite merging** (46 instances), suggesting a data model where invoices, credit memos, and ledger entries are processed through parallel streams before being unified.
- **Dimension handling** -- dedicated Raw jobs for dimensions, dimensionSetEntries, and dimensionValue (VJP_Raw_dimensions, VJP_Raw_dimensionSetEntries, VJP_Raw_dimensionValue), indicating a heavily normalized ERP schema that requires multiple extraction jobs to flatten.
- **tBigQuerySQLRow** (36 instances), the highest per-region count, suggesting more SQL-driven transformation logic than in other regions.

### GENERIC: The API-First Region

GENERIC is the only region that does not use tBigQueryInput as its primary source. Instead, its 15 jobs are built around:

- **tRESTClient** (13 instances) calling Microsoft Business Central OData endpoints.
- **tExtractJSONFields** (17 instances) parsing the JSON responses.
- **tFileInputDelimited** (26 instances) reading CSV files, likely fallback or batch-export data.

This makes GENERIC the most modern integration pattern in the platform and the closest to what a cloud-native replacement would look like. Jobs like `GEN_BC_Customers_oData` and `GEN_BC_Sales_Invoice_Header_oData` follow a consistent pattern: REST call, JSON extraction, field mapping, BigQuery write.

### Germany (DEU): The Lean Dual-Entity Region

DEU is architecturally notable for its compact component footprint (117 components, 9.8 per job) combined with dual-entity complexity. The VDA jobs process Business Central data while VDD jobs process legacy ERP data, yet both must produce a unified Sales_Set output. Key characteristics:

- **No tHash usage.** Unlike BRA and USA, Germany's jobs rely on tMap joins against directly-read BigQuery tables rather than pre-loaded hash maps. This results in simpler jobs but potentially more BigQuery read operations.
- **Heavy tBigQuerySQLRow presence** (8 instances across 12 jobs), used for the delete-before-insert pattern in Sales_Set lifecycle management (VDA_Clean_Sales_Set_Delete_Ingest, VDD_Clean_Sales_Set_Delete_Ingest).
- **Full context parameterization** -- all 8 DEU jobs using `context.parameter_Raw_Prefix` for table references, making Germany the most consistently parameterized region.

### Italy (ITA): The Compact Salesforce-Integrated Region

With only 6 jobs and 66 components, Italy has the most streamlined pipeline of the major production regions. Yet it includes two tSalesforceOutput components for CRM product synchronization (IZO_Sync_Product_Salesforce_v5), making it one of only three regions with direct Salesforce write capability (alongside BRA and JPN). Italy's architecture is a good template for what a "minimal viable regional pipeline" looks like: one raw extraction job, one Sales_Set builder, lifecycle management jobs, and a Salesforce sync.

### USA (USA): The File-Ingestion Hybrid

The USA region is distinguished from other regions by its file-based data ingestion pathway. In addition to the standard BigQuery-to-BigQuery transformations, USA jobs include:

- **tFileInputDelimited** (61 instances) -- the highest file-read count of any region, driven by the Animalytix third-party distributor data pipeline and CSV-based batch exports.
- **tGSGet** (Google Cloud Storage retrieval) for the VUS_Raw_Animalytix_SFTP pipeline, which downloads files from SFTP/GCS before processing.
- **Dual-subsidiary structure** -- USA contains both VUS (Vaxxinova US) and NPL (Newport Laboratories) jobs, each with independent extraction and cleaning pipelines that must be separately maintained.

### South Africa (ZAF/SAF): The Developing Frontier

The combined ZAF (3 jobs, 30 components) and SAF (1 job, 6 components) regions represent the earliest stage of pipeline development in the platform. ZAF's `VSA_Raw_vw_InvoiceDetails_ETL` is a purpose-built extraction job, while the other jobs are clones from Italy's template. The tHashInput/tHashOutput pattern (2 instances each in ZAF) in the delete-ingest job suggests the team intends to follow the same architectural patterns as mature regions.

### Global: The Orchestration Layer

Global's 42 jobs serve two purposes: orchestrating regional child jobs and synchronizing data with Salesforce. The orchestration role is visible in `Global_Clean_Sales_Set`, which calls 10 regional child jobs (BIO_Clean_Sales_Set, VIT_Clean_Sales_Set, VDD_Clean_Sales_Set, VJP_Clean_Sales_Set, VUS_Clean_Sales_Set, NPL_Clean_Sales_Set, VDA_Clean_Sales_Set, Legacy_Clean_Sales_Set, Forecast_Sales_Set, Raw_Sales_Set). The Salesforce sync role is visible in the 15 Salesforce_* jobs (Salesforce_Account_v7, Salesforce_Budget_v8, Salesforce_Products, etc.) that use tSalesforceInput (24 instances across Global) and tSalesforceOutput to exchange data with the CRM.

---

## Cross-Cutting Observations

Four components appear in nearly every region and represent platform-wide conventions that must be preserved or replaced as a unit:

1. **tMap** appears in all 10 regions. It is the transformation lingua franca of the platform.
2. **tBigQueryInput/Output** appears in 8 of 10 regions (absent only in scratchpad jobs that use alternative connectors for testing).
3. **Vaxxinova_Logging** appears in 9 of 10 regions. As a custom joblet, it has no out-of-the-box equivalent in any target platform and must be reimplemented.
4. **cleanup_temp_file_storage** appears in 9 of 10 regions. Its ubiquity suggests that the temp-file-staging pattern is deeply embedded in the platform's operational assumptions.

The platform also relies on 15 shared Talend routines (DataOperation, Mathematical, Numeric, Relational, SQLike, StringHandling, TalendDate, TalendString, TalendStringUtil, TalendDataGenerator, DataQuality, DQTechnical, DataMasking, DataQualityDependencies, DqStringHandling). These are standard Talend libraries rather than custom code, but any expressions referencing them inside tMap must be translated to the target platform's expression language.

---

## Migration Takeaways

**tMap is the highest-risk migration item.** With 565 instances, tMap contains the bulk of the platform's transformation logic -- field mappings, filter conditions, lookup joins, and expressions using Talend routines. Each tMap must be individually analyzed and translated. Automated migration tools can handle simple field-to-field mappings, but conditional logic and routine calls will require manual review.

**The tHash lookup pattern requires an architectural decision.** The 269 tHashOutput + corresponding tHashInput instances implement in-memory joins that have no direct equivalent in most modern integration platforms. Options include: (a) converting to SQL JOIN operations inside BigQuery, (b) using the target platform's caching/lookup mechanism, or (c) pre-materializing lookup tables. Option (a) is likely simplest for most cases but may change performance characteristics.

**Vaxxinova_Logging must be reimplemented as a shared service.** At 193 instances, this custom joblet is the platform's observability backbone. The migration target should implement a centralized logging service (API endpoint, shared function, or platform-native audit log) before migrating individual jobs, so that operational parity is maintained from day one.

**cleanup_temp_file_storage implies a file-staging dependency.** Many jobs write intermediate data to local disk before loading it into BigQuery. If the target platform uses in-memory streaming or direct API writes, this entire pattern may be unnecessary -- but the migration must verify that no job relies on the staged files for error recovery or audit purposes.

**GENERIC jobs are the easiest migration candidates.** Their REST + JSON + BigQuery pattern maps naturally to modern integration platforms and should be migrated first to build team confidence and establish patterns.

**Scratchpad and deprecated jobs should be excluded from migration.** The 4 scratchpad jobs and numerous `Copy_of_*`, `*_Backup*`, `*_DEBUG*`, and `*_deprecated` jobs (visible across BRA, JPN, and USA) are development artifacts. A pre-migration audit should tag these for exclusion to reduce the migration scope from 251 jobs to an estimated 180-200 active production jobs.
