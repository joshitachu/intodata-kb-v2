# Execution Logic

Vaxxinova's 257 Talend jobs follow a uniform execution model: each job is a self-contained unit that runs independently, with no embedded scheduling triggers in the Talend metadata. Scheduling and orchestration happen outside of Talend -- likely through the Talend Administration Center (TAC), cron, or an external orchestrator. Within each job, execution proceeds through a well-defined lifecycle: a `tPrejob` phase handles initialization (loading context variables, establishing connections), the main body performs data extraction and transformation through chained subjobs, and a `tPostjob` phase triggers cleanup (temp file removal via the `cleanup_temp_file_storage` joblet) and audit logging (via `Vaxxinova_Logging`). The only exception to the standalone pattern is `Global_Clean_Sales_Set`, which orchestrates 10 downstream regional Sales_Set jobs in a single coordinated run. Understanding these execution patterns is essential for mapping Talend's control flow semantics to whatever orchestration and workflow engine the migration target provides.

## Trigger Patterns

Of the 257 jobs analyzed, 256 are standalone -- they contain no embedded trigger definitions, no cron expressions, and no parent-child invocation metadata within the Talend project XML. The single exception is `Global_Clean_Sales_Set` in the Global region, which acts as a master orchestrator calling 10 regional Sales_Set jobs: `BIO_Clean_Sales_Set`, `VIT_Clean_Sales_Set`, `VDD_Clean_Sales_Set`, `VJP_Clean_Sales_Set`, `VUS_Clean_Sales_Set`, `NPL_Clean_Sales_Set`, `VDA_Clean_Sales_Set`, `Legacy_Clean_Sales_Set`, `Forecast_Sales_Set`, and `Raw_Sales_Set`.

This near-universal absence of triggers in the metadata does not mean the jobs are run ad hoc. In practice, production ETL pipelines of this scale are scheduled -- but that scheduling is configured in the runtime environment (TAC, a Linux crontab, or a third-party scheduler), not in the Talend job definitions themselves. The metadata tells us how each job is wired internally, but not when or in what order it runs. That operational knowledge must be captured separately from the people who manage the production environment.

**Migration takeaway:** The migration target must provide an external scheduling layer. Since Talend metadata captures no schedule information, the migration team needs to interview operations staff or inspect the TAC/cron configuration to reconstruct the actual production schedule before migration.

## Control Flow Patterns

Three control flow mechanisms govern how work progresses within a Talend job:

### SUBJOB_OK Chaining

The dominant pattern across the platform. When a subjob completes successfully, control passes to the next subjob via a `SUBJOB_OK` connector. This creates a linear, sequential pipeline: subjob A finishes, then subjob B starts, then subjob C, and so on. Nearly every job in the platform uses this pattern for its primary flow. For example, in `BIO_Clean_Invoice_Set_v5` (BRA), the flow proceeds from reading raw BigQuery tables, through tMap transformations that enrich sales and returns data with fiscal periods and Salesforce country mappings, to writing the consolidated Sales_Set -- each stage gated by the prior stage's successful completion.

The delete-then-insert pattern used extensively in ingest jobs relies on SUBJOB_OK chaining for correctness: a `tBigQuerySQLRow` subjob executes a DELETE statement against the target table, and only after it succeeds (SUBJOB_OK) does the next subjob write new data via `tBigQueryOutput`. This ordering is critical -- if the DELETE and INSERT ran concurrently, the INSERT could write data that the DELETE then removes.

### COMPONENT_OK Sequencing

Within a single subjob, individual components are linked by `COMPONENT_OK` connectors. This is the intra-subjob equivalent of SUBJOB_OK: component A finishes, then component B starts. This pattern is especially visible in the `tPrejob` and `tPostjob` phases. For instance, in the GENERIC `GEN_BC_*` family of jobs, the `tPrejob` phase uses COMPONENT_OK to sequence three steps: loading Business Central secret keys from a context file, authenticating via OAuth to Azure AD (`Create_Business_Central_Token`), and then proceeding to the REST API extraction. Each step depends on the prior step's output -- the token call needs the secrets, and the API call needs the token.

### RUN_IF Conditional Branching

Some jobs include conditional branches that execute only when a runtime condition is met. The `RUN_IF` connector evaluates a Java expression and routes control flow accordingly. This is used for conditional branch execution -- for example, deciding whether to process salesInvoices or salesCreditMemos based on a context variable, or skipping certain enrichment steps when reference data is unavailable.

RUN_IF conditions are less common than SUBJOB_OK chains but appear in the more complex jobs, particularly those that handle multiple data streams within a single job definition. They add branching complexity that must be carefully mapped to the target platform's conditional logic capabilities.

## Subjob Patterns

Talend jobs are composed of subjobs -- groups of connected components that execute as a unit. Understanding the three trigger types that connect subjobs is essential for accurately translating Talend control flow into the target platform's orchestration model.

### Trigger Type 1: SUBJOB_OK (Success Continuation)

SUBJOB_OK is by far the most common trigger, governing the vast majority of inter-subjob transitions across the platform. When a subjob completes without throwing an unhandled exception, the SUBJOB_OK trigger fires and control passes to the next subjob in the chain. The semantics are straightforward: "if the previous step succeeded, proceed to the next step."

The typical SUBJOB_OK chain looks like this in conceptual terms:

```
[Subjob A: Load context] --SUBJOB_OK--> [Subjob B: DELETE existing data]
    --SUBJOB_OK--> [Subjob C: Extract from source]
    --SUBJOB_OK--> [Subjob D: Transform via tMap]
    --SUBJOB_OK--> [Subjob E: Write to BigQuery]
    --SUBJOB_OK--> [Subjob F: Log completion]
```

Each arrow represents a hard dependency: Subjob C will not start until Subjob B has completed successfully. This sequential guarantee is critical for the delete-then-insert pattern, where the DELETE must finish before the INSERT begins. In the target platform, each SUBJOB_OK link should translate to a dependency edge in a DAG or a sequential step in a pipeline definition.

### Trigger Type 2: COMPONENT_OK (Intra-Subjob Sequencing)

Within a single subjob, components are linked by COMPONENT_OK connectors. This is a finer-grained sequencing mechanism that operates at the component level rather than the subjob level. COMPONENT_OK is especially visible in the `tPrejob` and `tPostjob` lifecycle phases.

A typical tPrejob sequence using COMPONENT_OK:

```
[tPrejob_1] --COMPONENT_OK--> [tContextLoad: Load secrets]
    --COMPONENT_OK--> [tJava: Build OAuth token request]
    --COMPONENT_OK--> [tRESTClient: Acquire BC token]
    --COMPONENT_OK--> [tJava: Store token in context variable]
```

The distinction between SUBJOB_OK and COMPONENT_OK matters for migration because some target platforms (e.g., Airflow) operate at the task level (equivalent to subjobs) and do not model intra-task sequencing. For these platforms, COMPONENT_OK chains within a subjob should be collapsed into a single atomic task, with the internal sequencing handled by the task's implementation (e.g., a Python function that runs the steps in order).

### Trigger Type 3: SUBJOB_ERROR (Failure Handling)

SUBJOB_ERROR triggers fire when a subjob encounters an unhandled exception. They are Talend's primary mechanism for routing execution to error-handling logic -- cleanup, logging, compensating actions, or graceful termination. As documented in the Error Handling analysis, SUBJOB_ERROR triggers are almost entirely absent from the Vaxxinova codebase. The 229 jobs with no error handling have no SUBJOB_ERROR connections, meaning exceptions propagate uncaught to the Talend runtime, which either terminates the job or (in some cases) continues to the next subjob via SUBJOB_OK if the exception was swallowed at the component level.

The near-total absence of SUBJOB_ERROR triggers means the migration team does not need to translate complex error routing logic -- there is almost none to translate. However, the migration should add error handling at every subjob boundary, effectively inserting SUBJOB_ERROR equivalents that the original developers never implemented.

## Execution Order and Implicit Dependencies

While 256 of 257 jobs are standalone in Talend metadata (no parent-child invocation), the platform has deep implicit execution ordering requirements that are enforced by external scheduling rather than by Talend's internal trigger mechanisms. These implicit dependencies are just as important as explicit ones for migration planning, because violating them produces silently incorrect results.

### The Three-Tier Execution Model

The platform's jobs fall into three logical tiers that must execute in order:

**Tier 1: Raw Extraction.** Jobs like `BRA_VBR_RAW`, `JPN_VJP_RAW`, and the GENERIC `GEN_BC_*` family extract data from source systems (MSSQL, Business Central APIs, SFTP) and write to BigQuery raw-layer datasets (`bra_vbr_raw`, `jpn_vjp_raw`, etc.). These must run first because every downstream job depends on fresh raw data.

**Tier 2: Regional Clean Transformation.** Jobs like `BIO_Clean_Invoice_Set_v5`, `VJP_Clean_Build_Sales_Set`, and `VUS_Clean_Build_Sales_Set` read from the raw layer, apply enrichment and business logic, and write to regional clean-layer datasets. These cannot run until their corresponding Tier 1 jobs have completed, because they would otherwise process stale data.

**Tier 3: Global Consolidation.** Jobs in the Global region, particularly `Global_Clean_Sales_Set` and `Global_Build_Sales_Set`, read from regional clean datasets and produce the unified analytics tables. These must run after all regional Tier 2 jobs have completed.

### Cross-Tier Data Dependencies

The implicit dependencies between tiers are expressed entirely through shared BigQuery tables. For example:

- `BRA_VBR_RAW` writes to `bra_vbr_raw.salesInvoices`
- `BIO_Clean_Invoice_Set_v5` reads from `bra_vbr_raw.salesInvoices`
- `Global_Build_Sales_Set` reads from `bra_vbr_clean.Sales_Set`

None of these dependencies are encoded in Talend's trigger or invocation metadata. The current platform relies on scheduling timing (e.g., Tier 1 runs at 2 AM, Tier 2 at 4 AM, Tier 3 at 6 AM) to ensure correct ordering. This timing-based approach is fragile: if a Tier 1 job takes longer than expected, a Tier 2 job may start before fresh data is available, processing yesterday's data without any warning.

### Reference Data Dependencies

Beyond the three-tier model, many clean-layer jobs depend on globally shared reference data tables that are updated independently:

- `Global_Master.Fiscal_Periods` -- used by nearly every Sales_Set builder
- `Global_Raw.Salesforce_Account_Mappings` -- links ERP customers to Salesforce accounts
- `Global_Raw.Salesforce_Country_Mappings` -- standardizes country codes
- `Global_Budget.Budget_Current_Conversion_Rate` -- currency conversion for multi-currency reporting

These reference tables are updated by their own jobs (e.g., `Salesforce_Account_Mappings_v3`), which must complete before any regional clean job that joins against them. This creates a fourth implicit dependency tier: reference data refresh, which should logically sit between Tier 1 and Tier 2. The current scheduling does not appear to enforce this ordering explicitly.

### Migration Recommendation: Sensor-Based Triggering

The migration target should replace timing-based implicit dependencies with sensor-based explicit dependencies. Instead of scheduling Tier 2 to run "2 hours after Tier 1 starts," the target platform should implement data sensors that trigger Tier 2 only when the upstream BigQuery tables have been updated. Apache Airflow's `BigQueryTableExistenceSensor` or Azure Data Factory's `GetMetadata` activity with timestamp checks are examples of this pattern. Sensor-based triggering eliminates timing fragility and makes the dependency chain self-documenting.

## Complex Control Flow Examples

To illustrate how the three trigger types combine in practice, the following examples trace the execution flow of representative jobs from different complexity tiers.

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

This 4-subjob chain is representative of the simplest production jobs. Each subjob contains 2-4 components linked by COMPONENT_OK. The entire flow is linear and deterministic.

### Example 2: Multi-Stream Flow -- `JPN_VJP_RAW` (JPN)

Japan's raw extraction consolidates multiple entity extractions into a single job with parallel internal data streams:

```
tPrejob [Load context, establish MSSQL connection]
  --SUBJOB_OK--> Subjob A: [tMSSqlInput(salesInvoices) --> tMap --> tBigQueryOutput(raw.salesInvoices)]
  --SUBJOB_OK--> Subjob B: [tMSSqlInput(salesCreditMemos) --> tMap --> tBigQueryOutput(raw.creditMemos)]
  --SUBJOB_OK--> Subjob C: [tMSSqlInput(generalLedgerEntries) --> tMap --> tBigQueryOutput(raw.glEntries)]
  --SUBJOB_OK--> Subjob D: [tMSSqlInput(items) --> tMap --> tBigQueryOutput(raw.items)]
  --SUBJOB_OK--> Subjob E: [tMSSqlInput(customers) --> tMap --> tBigQueryOutput(raw.customers)]
  --SUBJOB_OK--> tPostjob [cleanup]
```

Although the five extraction subjobs are chained sequentially via SUBJOB_OK, they are logically independent -- each reads from a different source table and writes to a different target. This is a prime candidate for parallelization during migration. The target platform could run all five extractions concurrently, reducing total execution time.

### Example 3: Branching Flow with RUN_IF -- `GEN_BC_SalesInvoices` (GENERIC)

The GENERIC Business Central extractor uses RUN_IF conditions to handle API pagination:

```
tPrejob [Load BC secrets, acquire OAuth token]
  --SUBJOB_OK--> Subjob A: [tRESTClient(initial page) --> tExtractJSONFields --> tBigQueryOutput]
  --RUN_IF(hasNextPage)--> Subjob B: [tJava(update page URL) --> tRESTClient(next page) --> tExtractJSONFields --> tBigQueryOutput]
  --RUN_IF(hasNextPage)--> [Loop back to Subjob B]
  --SUBJOB_OK--> tPostjob [Vaxxinova_Logging]
```

The RUN_IF condition evaluates a Java expression (`context.nextPageURL != null`) to determine whether another page of API results exists. This loop-via-conditional-branching pattern must be translated to the target platform's loop or iteration construct (e.g., Airflow's `while` sensor, ADF's `Until` activity, or a recursive dbt macro).

## Most Complex Jobs

Seven jobs stand out for their structural complexity, measured by component count, number of data sources, and the sophistication of their control flow. These represent the highest-risk items for migration.

### 1. Copy_of_VBR_Clean_Build_Sales_Set (BRA)

This is the most complex job in the entire platform with 94 components. It constructs the Brazil Sales_Set from Business Central data (salesInvoices, salesCreditMemos, salesInvoiceLines, salesCreditMemoLines), enriching each record with fiscal periods, payment terms, shipment methods, currency conversion rates, country master data, and Salesforce account mappings. The job pulls from 26 distinct input sources and writes to 17 outputs. Its architecture relies heavily on tHash/tJava patterns for in-memory lookup tables -- 13 `tFixedFlowInput` and 14 `tHashOutput` components load reference data into memory at the start of the job, which subsequent tMap components then join against. This is a context-parameterized variant (using `context.parameter_JPN_Raw_Env`-style references), making it the more production-ready version compared to its non-Copy counterpart that hardcodes dataset names.

### 2. VBR_Raw_salesCreditMemos_Old (BRA)

A legacy raw extraction job that pulls sales credit memo data from Brazil's on-premises MSSQL database. Its complexity stems from the volume of fields extracted and the transformation logic applied during what should be a "raw" extraction layer. The job handles GUID normalization (stripping curly braces from Business Central systemId fields), null-safe date parsing, and multiple output paths. The "Old" suffix indicates this is a legacy variant maintained alongside a newer version -- the coexistence of old and new extraction jobs is a recurring pattern in BRA that adds maintenance overhead.

### 3. VBR_Raw_salesInvoices_Old (BRA)

The invoice counterpart to the credit memos job above. It extracts sales invoice headers and lines from the legacy MSSQL source, applying similar GUID cleaning and date parsing transformations. Together with `VBR_Raw_salesCreditMemos_Old`, these two jobs form the legacy extraction pathway that feeds into the clean-layer Sales_Set builder. Both "Old" variants persist alongside their newer Business Central API-based replacements, reflecting Brazil's ongoing ERP migration from MSSQL to Business Central.

### 4. VUS_Raw_salesCreditMemos (USA)

The US credit memo extraction job. Its complexity arises from the breadth of fields extracted from Business Central (customer addresses, financial amounts, tax breakdown, discount structures) and the multi-step REST API pagination and JSON parsing logic. The USA region has 61 jobs total with 1,126 components, and this extraction job is among the densest. It uses `tRESTClient` for API calls, `tExtractJSONFields` for response parsing, and `tFileInputDelimited` for intermediate staging -- a pattern shared with the GENERIC BC extractor family but customized with US-specific field mappings.

### 5. VUS_Raw_salesInvoices (USA)

The invoice extraction counterpart for the US subsidiary. Like its credit memo sibling, this job handles Business Central API pagination, JSON field extraction, and BigQuery staging. The US invoice pipeline is notable for processing both direct sales (VUS entity) and Newport Labs (NPL) legacy data, adding conditional logic to route records correctly. The job also feeds downstream clean-layer processing that includes Salesforce CRM synchronization -- US is one of only three regions (alongside Brazil and Italy) with active bidirectional Salesforce integration.

### 6. VBR_Raw_generalLedgerEntries (BRA)

This job extracts general ledger entries from Brazil's Business Central instance. GL entries are structurally more complex than sales documents because they span multiple account types, posting groups, and dimension combinations. The extraction captures debit/credit amounts, document references, VAT entries, and dimension set links. This data feeds into the production order costing pipeline (absorption cost allocation) that is unique to Brazil among all Vaxxinova regions.

### 7. JPN_VJP_RAW (JPN)

Japan's consolidated raw extraction job. Rather than splitting extraction into separate per-entity jobs as Brazil does, Japan combines multiple entity extractions (salesInvoices, salesCreditMemos, generalLedgerEntries, items, customers) into a single monolithic job. With the JPN region containing 872 total components across 36 jobs and 128 tMap instances (the highest tMap density of any region), this raw extraction job sets up the complex downstream processing where sales data is sourced from four GL entry streams (General Journal, Cost of Goods Sold, General Credit, General Receipt) plus direct invoice/credit memo lines.

### What Makes These Jobs Complex

Several factors recur across the most complex jobs:

- **High component counts** (60-94 components per job) driven by reference data lookups using tHash/tFixedFlowInput patterns
- **Multiple data streams** joined within a single job rather than decomposed into modular subjobs
- **Legacy/modern coexistence** where both MSSQL-based and Business Central API-based extraction paths are maintained simultaneously
- **Deep enrichment chains** where raw data passes through 10+ reference table lookups (fiscal periods, country master, conversion rates, Salesforce mappings) before reaching the output
- **Delete-then-insert idempotency** patterns that add subjob stages for pre-load cleanup

## Region Comparison

| Region | Jobs | Standalone | Orchestrator | Components | Avg Components/Job | Flowcharts Available |
|--------|------|------------|--------------|------------|-------------------|---------------------|
| BRA | 75 | 75 | 0 | 1,592 | 21.2 | 75 |
| USA | 61 | 61 | 0 | 1,126 | 18.5 | 61 |
| Global | 43 | 42 | 1 | 476 | 11.1 | 43 |
| JPN | 36 | 36 | 0 | 872 | 24.2 | 36 |
| GENERIC | 15 | 15 | 0 | 211 | 14.1 | 15 |
| DEU | 12 | 12 | 0 | 117 | 9.8 | 12 |
| ITA | 6 | 6 | 0 | 66 | 11.0 | 6 |
| scratchpad | 5 | 5 | 0 | 32 | 6.4 | 3 |
| ZAF | 3 | 3 | 0 | 30 | 10.0 | 3 |
| SAF | 1 | 1 | 0 | 6 | 6.0 | 1 |
| **Total** | **257** | **256** | **1** | **4,528** | **17.6** | **255** |

Japan has the highest average component density at 24.2 components per job, reflecting its use of consolidated multi-entity extraction jobs. Brazil is the largest region by total component count (1,592), driven by the combination of legacy MSSQL and modern Business Central extraction paths. The Global region's single orchestrator job (`Global_Clean_Sales_Set`) is the only job in the platform that invokes other jobs, making it the sole point of cross-job coordination within Talend metadata.

## Mermaid Flowcharts

The analysis pipeline generated Mermaid-format flowcharts for 255 of the 257 jobs (only 2 scratchpad jobs lack flowchart data). These flowcharts capture the complete internal wiring of each job: subjob boundaries, component connections, trigger types (SUBJOB_OK, COMPONENT_OK, RUN_IF), and data flow direction. They are stored in the pass data alongside the job metadata.

These flowcharts serve two purposes during migration:

1. **Visual validation**: migration engineers can compare the source Talend flowchart with the target platform's implementation to verify structural equivalence.
2. **Documentation artifacts**: the Mermaid format renders natively in GitHub, Confluence, and most documentation platforms, making them immediately usable in migration runbooks without additional tooling.

For the most complex jobs (such as `Copy_of_VBR_Clean_Build_Sales_Set` with its 94 components), the flowcharts are particularly valuable because they make the subjob chaining and reference data lookup patterns visible at a glance -- something that is difficult to discern from the Talend Designer canvas when component counts are high.

## Migration Takeaways

1. **Map SUBJOB_OK chains to the target orchestration model.** Most modern integration platforms use DAG-based (directed acyclic graph) task dependencies rather than sequential chaining. Each SUBJOB_OK link becomes a dependency edge in the DAG. The linear chains in most Vaxxinova jobs translate directly, but the delete-then-insert patterns must preserve strict ordering.

2. **Translate RUN_IF conditions to the target platform's conditional logic.** RUN_IF connectors evaluate Java expressions at runtime. The target platform needs equivalent conditional branching -- whether that is a gateway in a workflow engine, an IF activity in Azure Data Factory, or a conditional operator in Airflow. Document every RUN_IF condition's Java expression before migration.

3. **Decompose monolithic jobs during migration.** Jobs like `Copy_of_VBR_Clean_Build_Sales_Set` (94 components) and `JPN_VJP_RAW` are candidates for decomposition into smaller, modular units. The target platform should separate reference data loading, data extraction, transformation, and output into distinct steps that can be tested and monitored independently.

4. **Reconstruct the Global_Clean_Sales_Set orchestration pattern.** The single orchestrator job that coordinates 10 regional Sales_Set builds must be rebuilt in the target scheduler. This is an opportunity to add parallelism -- the 10 regional jobs are independent and can run concurrently, with a final consolidation step that waits for all to complete.

5. **Preserve the tPrejob/tPostjob lifecycle.** Every production job follows a standard lifecycle: initialize (tPrejob), execute (main flow), clean up (tPostjob + cleanup_temp_file_storage), and log (Vaxxinova_Logging). The target platform must replicate this pattern -- particularly the temp file cleanup, which prevents storage accumulation on the execution host.

6. **Leverage the Mermaid flowcharts.** Use the 255 generated flowcharts as source-of-truth references during migration implementation and testing. They capture structural detail that is easy to lose when translating between platforms.

---

*Generated by intodoc -- documentation automation for integration platforms.*
