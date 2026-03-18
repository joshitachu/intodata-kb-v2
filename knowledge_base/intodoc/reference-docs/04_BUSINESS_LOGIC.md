# Business Logic, Custom Code & State Management

This document consolidates three areas of analysis for the Vaxxinova Talend ETL platform: the business rules embedded in pipeline transformations, the custom Java code landscape across all regions, and the state management and side-effect patterns that govern intermediate data handling. Together, these areas define the platform's functional behavior -- what the pipelines do, how they do it, and what artifacts they produce along the way. Understanding all three is essential for migration planning, because business rules must be faithfully replicated, custom code must be translated, and side-effect patterns must be either preserved or replaced with cloud-native alternatives.

---

## Part 1: Business Rules Catalog

### Overview

The platform embeds hundreds of business rules across 257 Talend jobs spanning 10 regions. These rules govern how sales data is transformed, validated, and enriched as it flows from regional ERP systems through to BigQuery and Salesforce. Each rule must be faithfully replicated in any target platform.

Business rules are organized by functional domain. The following sections catalog the most significant rules by category, with their implementation locations and impact assessments.

### Budgeting & Forecasting

*2 business rules identified across the platform.*

**BR-ZAF-010**: The clean sales set includes parallel columns for multiple planning scenarios: actuals (Quantity, Net_Sales_Amount), budget (_Budget suffix), prior year (_Prior suffix), order (_Order suffix), and forecast (_Forecast suffix). Each scenario has both local currency and EUR-converted variants.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Missing budget/forecast/prior-year columns would prevent variance analysis (actual vs. budget, YoY comparison), which is critical for management reporting and sales target tracking.

**BR-ZAF-011**: Budget and forecast EUR amounts are converted using the same conversion rate as actuals, ensuring consistent FX treatment. The formula pattern is: amount_EUR = amount_local / ConversionRate, with null-safe checks on each amount field.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Inconsistent FX conversion between actuals and budget would show artificial variances driven by rate differences rather than genuine business performance gaps.

### Currency & FX

*4 business rules identified across the platform.*

**BR-DEU-004**: When the Currency_Code from Business Central is empty or null, the system defaults to 'EUR' as the transaction currency, since VDA operates in Germany where EUR is the functional currency.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Invoices in the home currency would have no currency code assigned, breaking FX conversion logic and causing null values in currency reporting fields.

**BR-DEU-005**: The conversion rate defaults to 1 when the Currency_Code is empty (domestic EUR transactions). For foreign currency invoices, the Currency_Factor from the invoice header is used as the conversion rate.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Domestic transactions would have null or zero conversion rates, causing division-by-zero errors when computing EUR-equivalent amounts for group reporting.

**BR-ZAF-005**: All local-currency amounts (Sales_Price, Discount_Amount, Net_Sales_Amount, COGS) are converted to Euro by dividing by the ConversionRate from Global_Clean.Budget_Current_Conversion_Rate. Null amounts are handled with null-safe checks to prevent division errors.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: ZAR-to-EUR conversion failure would make South African figures incomparable with other subsidiaries in the group-level consolidated reports.

**BR-ZAF-006**: The budget conversion rate from Global_Clean.Budget_Current_Conversion_Rate is used for both actual and budget amount conversions, ensuring that FX variances are isolated from operational variances.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Using different rates for actuals vs. budget would introduce FX-driven variances that obscure genuine operational performance differences.

### Customer Management

*62 business rules identified across the platform.*

Customer management rules govern how customer identifiers, addresses, and geographic classification are resolved across regions. Key patterns include:

- **Country resolution fallbacks**: Invoice country is resolved using a cascading pattern -- Salesforce country mapping (Country_Code__c) takes precedence, otherwise the raw source Invoice_Country is used (BRA: BIO_Clean_Invoice_Set_v5). Null or empty country fields default to the domestic country code ('BR' for Brazil, 'DE' for Germany, 'JP' for Japan).

- **Customer address hierarchies**: Multiple regions capture both sell-to (order customer) and bill-to (invoice customer) with separate address structures, supporting complex B2B scenarios where the ordering party differs from the paying party.

- **Salesforce CRM linkage**: Customer accounts are synchronized to Salesforce as Account_Mapping__c custom objects using UPSERT with Local_Id__c as the external key (USA: NPL_Clean_Account_v4_Salesforce), ensuring bidirectional CRM-ERP linkage.

- **Territory assignment**: Territory is derived from Country_Master lookups or hardcoded for single-market subsidiaries (e.g., 'North America' for all NPL customers).

- **Data quality**: Country values are trimmed of whitespace using TalendString.talendTrim before use, and hardcoded overrides correct non-standard entries (e.g., 'LUSAKA' maps to 'ZM' for Zambia in ZAF).

### Sales & Revenue

*166 business rules identified -- the largest category on the platform.*

Sales and revenue rules govern how invoice data is transformed from regional ERP formats into the unified Sales_Set schema. The dominant patterns include:

- **Entity hardcoding**: Each region hardcodes its Entity field ('Vaxxinova Japan', 'Vaxxinova United States', 'Deltamune', etc.) to ensure correct attribution in the global Sales_Set.

- **ID construction**: Record identifiers are prefixed with entity codes (VJP-, VDA-, DEL-, VUS-ANI-) to ensure global uniqueness across all subsidiaries in the consolidated data warehouse.

- **Invoice/credit memo merging**: Sales invoices and credit memos are combined into unified datasets. Japan uses tUnite for explicit stream merging; other regions accomplish this within tMap joins.

- **Sign reversal**: GL entry amounts are multiplied by -1 to convert debit-positive accounting amounts to sales-positive reporting amounts (JPN). Returns quantities are similarly negated.

- **Multi-source consolidation**: Brazil combines BioVet legacy MSSQL data with Business Central API data. The USA integrates Animalytix third-party distributor sales alongside direct ERP sales.

- **Fiscal period enrichment**: All regions join invoice posting dates against Global_Raw.Fiscal_Periods to derive Fiscal_Period and Fiscal_Year values, aligning with the Vaxxinova group fiscal calendar.

### Financial Accounting

*12 business rules identified across the platform.*

Financial rules handle pricing calculations, cost tracking, and chart of accounts classification:

- **Local currency calculation**: Sales prices and net amounts in local currency are derived by dividing Business Central amounts by the conversion factor, with null amounts defaulting to 0.0.

- **Multi-level discounts**: Italy's pricing includes three cascading discount percentages (SCO1_PERC, SCO2_PERC, SCO3_PERC) plus extra discount amounts, enabling full price waterfall reconstruction from list price to net amount. South Africa inherits this same structure.

- **COGS tracking**: Cost of Goods Sold is tracked alongside revenue at the line level in both local currency and EUR, enabling gross margin calculation per invoice line.

- **Accrual accounting**: GEN_BC_AccrualLedgerEntries captures deferred revenue/expense recognition schedules for vaccine delivery contracts, supporting IFRS 15 compliance.

### Production & Manufacturing

*37 business rules identified, concentrated in BRA.*

Brazil's manufacturing pipeline is the most complex on the platform:

- **Absorption costing**: Production order line absorption costs are calculated by joining item ledger entries (filtered to Output/Saida entry types) with value entries (filtered to ALLOC% document numbers).

- **Status filtering**: Only production orders with status 'Finished' or 'Pending' are included in cost calculations, excluding draft or cancelled orders.

- **Write-back pattern**: VBR_Update_* jobs modify production order records with computed cost values. If the cost calculation pipeline produces incorrect results, production records are overwritten with wrong data.

### Data Integration & ETL Patterns

*82 business rules identified across the platform.*

Structural ETL rules govern how data moves through the pipeline layers:

- **Parameterized templates**: Generic Business Central (GEN_BC_*) jobs use context.parameter_BC_Company_Identifier to reuse a single job definition across multiple subsidiaries.

- **TRUNCATE-and-reload**: The dominant write pattern uses TRUNCATE action on BigQuery output, fully replacing data each run for idempotent processing.

- **DELETE-then-INSERT**: The Sales_Set ingest pattern deletes matching records by delta ID before inserting new ones, enabling incremental updates to the global dataset.

- **Data lineage tagging**: Output records are tagged with Source (e.g., 'VBR_Sales_Set', 'VJP_Sales_Set') and Last_Modified_Date for audit trail tracking.

- **Non-standard API paths**: The BC REST API endpoint uses '/v2.0/Production/api/LibertyGrove/financials/v2.0/companies({companyId})/{entity}', where 'LibertyGrove' indicates a custom API publisher -- not the default Microsoft API.

### Data Quality Rules

*6 business rules identified across the platform.*

- **Date boundary clamping**: Delivery dates before 1970-01-01 are replaced with the epoch boundary to prevent BigQuery ingestion failures (ITA, SAF, ZAF).

- **Null-safe datetime parsing**: Null values are replaced with sentinel '0001-01-01 00:00:00' before parsing via TalendDate.parseDate. The 'T' separator in ISO datetime strings is replaced with a space for format compatibility.

- **String trimming**: All string fields from regional source systems are trimmed using TalendString.talendTrim to prevent join failures from whitespace mismatches.

### Forecasting

*4 business rules identified across the platform.*

- **Category filtering**: Budget and forecast records are filtered from the Budget_Master table using Category IN (Budget, Forecast), ensuring only planning data enters the budget pipeline.

- **Forecast tagging**: Forecast quantities and amounts use the same conversion logic as budget but are tagged with Category=Forecast, supporting separate forecast-vs-budget variance reporting.

- **Baseline year logic**: Budgeting v5 reads actual sales from FY 2023/2024 as a baseline for building the next-year budget, combining actuals with Budget FY 2025/2026 and Forecast FY 2024/2025 records. The fiscal year filter is hardcoded and must be manually updated for each budget cycle.

### Business Rules Migration Considerations

Each business rule represents logic that must be faithfully replicated in the target platform:

- **Sales consolidation logic** (return negation, invoice/credit memo merging) is the highest-risk area for migration errors
- **Fiscal period mapping** depends on the Global_Raw.Fiscal_Periods reference table and must be migrated first
- **Currency conversion** rules differ by region and data category (actuals vs. budget vs. forecast)
- **Salesforce sync rules** include specific field mappings and external ID conventions that must be preserved
- **Entity assignment** logic varies by subsidiary and must be validated per-region during migration testing

---

## Part 2: Custom Code Analysis

### Overview

The platform contains 509 custom Java code snippets spread across 10 regions. Despite this volume, the custom code landscape is surprisingly uniform: the vast majority of snippets are infrastructure boilerplate rather than business logic. The dominant pattern is a copy-pasted `tJava_StoreJobStats` block that appears in nearly every job, supplemented by a set of shared routines for data quality handling. Genuine business rules hidden in custom code are rare -- but where they exist, they represent high-risk migration targets because they are easy to overlook.

### Language and Component Profile

All 509 snippets are written in Java. The code appears in three component types:

- **tJava** -- standalone Java blocks, typically triggered on subjob completion (182 instances). These are overwhelmingly `tJava_StoreJobStats` boilerplate.
- **tJavaRow** -- inline Java executed per row during data flow (102 instances). These handle row-level transformations that exceed tMap expression capabilities.
- **tJavaFlex** -- flexible Java blocks with begin/main/end sections (13 instances, all in the GENERIC region). These are used for complex flow control patterns such as paginated API calls.

### The Dominant Pattern: tJava_StoreJobStats

The single most duplicated code pattern across the platform is `tJava_StoreJobStats`. This block captures job execution metrics -- row counts, timestamps, and status -- and writes them to a centralized logging table after each BigQuery interaction completes.

```java
/*
 * tJava_StoreJobStats
 * Trigger: On Subjob Ok of the component that just spoke to BigQuery
 */
// ... metrics capture and logging logic
```

This exact block appears across BRA (83 tJava instances), JPN (44 instances), and USA (52 instances). Each instance is a verbatim copy rather than a call to a shared routine, meaning:

1. Any change to the logging logic must be applied to every job individually.
2. Drift between copies is undetectable without manual review.
3. A bug in the metrics capture logic affects all jobs, but a fix must be deployed to each one separately.

### Shared Routines

The platform employs five genuinely shared routines:

**DataQuality**: The primary validation library, providing reusable functions for null detection, empty-string handling, and value-range assertions. Referenced by all 10 regions -- BRA leads with 8 references, followed by Global (8), USA (7), and JPN (6).

**DataQualityDependencies**: Companion module providing supporting data structures, constants, and helper methods. Distribution mirrors DataQuality exactly. For migration, both modules should be treated as a single logical unit.

**DqStringHandling**: String manipulation utilities -- trimming whitespace, standardizing case, removing non-printable characters, handling locale-specific encoding. Many of the 211 format_check data quality rules rely on DqStringHandling methods.

**DQTechnical**: Technical data quality checks (data type compatibility, encoding validation, structural integrity). Present only in Global (8 references), ITA (1), SAF (1), ZAF (1), and scratchpad (1). The three largest regions do not reference it.

**SQLike**: SQL-like utility functions (COALESCE, CASE-WHEN, IN-list matching). Found only in Global (8 references), SAF (1), and ZAF (1). The limited distribution suggests it was introduced by a specific team and never propagated.

### Hidden Business Rules in Custom Code

Across all 509 snippets, zero were flagged as containing explicit business rules. However, the 102 tJavaRow components perform row-level transformations that may encode implicit business logic -- type conversions, conditional field mappings, BigDecimal arithmetic for financial calculations (e.g., `returnsInput.Quantity_Returns.multiply(BigDecimal.valueOf(-1))`), and data enrichment steps.

### Custom Code Region Profiles

**BRA (163 snippets -- 32% of total):** 83 tJava (StoreJobStats), 32 tJavaRow (row-level transformations for multi-subsidiary consolidation), 24 shared routine references. Does not reference DQTechnical or SQLike.

**USA (128 snippets -- 25% of total):** 52 tJava, 32 tJavaRow (Salesforce field mapping transformations), 21 shared routine references. Salesforce-specific data type conversions contribute notable tJavaRow instances.

**JPN (112 snippets -- 22% of total):** 44 tJava, 32 tJavaRow (multi-source GL entry processing), 18 shared routine references. Disproportionately high tJava count suggests complex jobs with multiple BigQuery interactions.

**Global (48 snippets -- 9% of total):** Entirely shared routine references -- no tJava, no tJavaRow. The only region referencing all five shared routines, reflecting its cross-entity consolidation role. Cleanest migration target from a custom code perspective.

**GENERIC (24 snippets -- 5% of total):** The only region using tJavaFlex (13 instances). These handle paginated API responses, dynamic endpoint construction, and conditional retry logic for Business Central extraction templates. Highest per-snippet complexity on the platform.

**Smaller regions (DEU, ITA, SAF, ZAF, scratchpad -- 34 snippets, 7%):** Rely almost entirely on shared routines with minimal or no job-specific custom code.

### Region Comparison

| Region | Total Snippets | tJava | tJavaRow | tJavaFlex | Shared Routines | Business Rules |
|--------|---------------|-------|----------|-----------|-----------------|----------------|
| BRA | 163 | 83 | 32 | 0 | 24 | 0 |
| USA | 128 | 52 | 32 | 0 | 21 | 0 |
| JPN | 112 | 44 | 32 | 0 | 18 | 0 |
| Global | 48 | 0 | 0 | 0 | 40 | 0 |
| GENERIC | 24 | 0 | 5 | 13 | 3 | 0 |
| DEU | 8 | 1 | 1 | 0 | 3 | 0 |
| ITA | 7 | 1 | 0 | 0 | 4 | 0 |
| scratchpad | 7 | 1 | 0 | 0 | 4 | 0 |
| SAF | 6 | 0 | 0 | 0 | 5 | 0 |
| ZAF | 6 | 0 | 0 | 0 | 5 | 0 |

### Custom Code Risk Assessment for Migration

| Risk Level | Snippet Count | % of Total | Key Concern |
|-----------|--------------|------------|-------------|
| High | 13 | 3% | Complex flow control in tJavaFlex (GENERIC only) |
| Medium | 102 | 20% | Possible implicit business rules in tJavaRow |
| Low | ~180 | 35% | StoreJobStats boilerplate -- replace with shared utility |
| Negligible | ~214 | 42% | Shared routine references -- migrate the routine, not the calls |

77% of custom code snippets fall into the low or negligible risk categories. The 13 high-risk tJavaFlex components and 102 medium-risk tJavaRow components demand careful, per-snippet review.

### The Relationship Between Custom Code and tMap Expressions

Custom code (509 snippets) and Java expressions embedded in tMap components are distinct concerns. The platform's 213 tMap components contain hundreds of inline Java expressions for field transformations, null handling, type coercion, and conditional logic. These are structurally simpler than tJavaRow code (single-expression transformations rather than multi-statement procedures) but far more numerous.

The total Java migration surface is broader than the 509 custom code snippets alone -- it includes all tMap expressions across all 213 tMap components. A unified translation toolkit capable of parsing Java ternary expressions, method calls, and BigDecimal arithmetic into the target language would serve both workstreams.

### Recommended Review Process for tJavaRow Components

1. **Categorize by transformation type.** Determine whether each performs type coercion, conditional logic, arithmetic, or data enrichment. Type coercion and simple arithmetic are mechanical translations; conditional logic and data enrichment may encode business rules.
2. **Flag conditional logic for business review.** Any tJavaRow containing `if`, `switch`, or ternary statements should be reviewed by a business analyst.
3. **Document the transformation contract.** Record input fields, output fields, and the transformation applied for each component.
4. **Test with production data samples.** Run the same input records through the original tJavaRow and the target platform equivalent, and compare outputs field by field.

---

## Part 3: State Management & Side Effects

### Overview

Beyond their primary data output, Vaxxinova's Talend jobs produce a range of side effects during execution. Across the platform, **555 side effects** have been identified across 10 regions. The majority are manageable, but **119 effects (21%) lack cleanup logic**, meaning they accumulate silently on execution servers over time.

### Effect Types and Distribution

| Effect Type | Count | Share |
|-------------|-------|-------|
| Staging file | 364 | 66% |
| Temp file | 191 | 34% |

**Staging files** are CSV files written to a shared directory (typically `context.resource_flow_temp_folder`) that serve as intermediate outputs between job steps. A job extracts data from BigQuery or an API, writes it to a CSV staging file, and a subsequent job or step reads that file for further processing. These files are the primary data exchange mechanism between loosely coupled job steps.

The prevalence of file-based staging (66% of all effects) reflects Talend's architecture, which favors disk-based data exchange over in-memory passing for large datasets. The typical staging file ranges from a few kilobytes (reference data lookups) to several hundred megabytes (full Sales_Set extracts).

**Temp files** are transient artifacts created during job execution -- partial outputs, working copies, serialization buffers, or hash table spillover files. Talend creates temp files automatically when certain components (tSort, tAggregate, tHash) exceed their in-memory buffer limits.

### Risk Analysis

| Risk Level | Count | Share |
|------------|-------|-------|
| Low | 436 | 79% |
| Medium | 119 | 21% |
| High | 0 | 0% |

The 436 low-risk effects have established cleanup mechanisms -- either the `cleanup_temp_file_storage` joblet in the tPostjob phase or external lifecycle policies on managed storage locations.

The 119 medium-risk effects are cases where jobs write staging files without corresponding cleanup logic. The risk is gradual disk space exhaustion over time rather than data corruption. On servers running multiple jobs daily, the accumulation can consume tens of gigabytes per month.

### The Cleanup Pattern: cleanup_temp_file_storage

The platform's primary cleanup mechanism is the `cleanup_temp_file_storage` joblet, which runs in the `tPostjob` phase and deletes files matching staging patterns from the temp directory.

**How It Works:** The tPostjob component executes after the main data flow completes, regardless of success or failure (similar to a `finally` block). The joblet lists files in the temp directory, filters for staging file patterns, and deletes them.

**Complete coverage (0% medium-risk):** Global, JPN, DEU, GENERIC, ITA, ZAF, SAF

**Incomplete coverage:** BRA (46% without cleanup), USA (23% without cleanup), scratchpad (100% without cleanup)

**Limitations:**

1. **Only cleans the designated temp folder.** Files written to non-standard directories are not covered.
2. **Not universally included.** No enforcement mechanism requires jobs to include cleanup logic.
3. **Per-job basis.** If an upstream job's cleanup deletes files before a downstream job reads them, the timing dependency creates fragile coupling.

### Regional Side-Effect Breakdown

| Region | Effects | Staging | Temp | Medium Risk | Medium % |
|--------|---------|---------|------|-------------|----------|
| BRA | 184 | 147 | 37 | 85 | 46% |
| USA | 132 | 88 | 44 | 30 | 23% |
| Global | 84 | 41 | 43 | 0 | 0% |
| JPN | 84 | 49 | 35 | 0 | 0% |
| GENERIC | 25 | 13 | 12 | 0 | 0% |
| DEU | 22 | 11 | 11 | 0 | 0% |
| ITA | 11 | 6 | 5 | 0 | 0% |
| ZAF | 7 | 4 | 3 | 0 | 0% |
| scratchpad | 4 | 4 | 0 | 4 | 100% |
| SAF | 2 | 1 | 1 | 0 | 0% |

Two regions account for 97% of all medium-risk effects: BRA (85 effects, concentrated in Clean-layer Sales_Set builders and production order processing) and USA (30 effects, concentrated in clean-layer processing for VUS and NPL subsidiaries).

### Concrete Examples of Medium-Risk Effects

**Copy_of_VBR_Clean_Build_Sales_Set (BRA):** Writes staging CSV to a dynamically constructed path using multiple context variables. No cleanup logic exists. As a "Copy_of" variant, it was created without the cleanup components present in the original -- a pattern that is one of the primary sources of medium-risk effects.

**VBR_Clean_Production_Order_Register (BRA):** Writes to a path with hardcoded `test_` prefix: `context.resource_flow_temp_folder + "/" + "test_bra_vbr_clean_Production_Order_Register.csv"`. This file is locked to the test environment regardless of runtime context, creating confusion about environment ownership.

**VBR_Clean_CBR_ProductionOrderRoutingLines (BRA):** Writes to `test_global_clean_ProductionOrderLines_CBR.csv` -- a cross-regional filename that references the global clean dataset from within a BRA job, creating ambiguity about which region should clean it up.

**VUS_Clean_Build_Sales_Set (USA):** Follows the correct environment-parameterized path convention but lacks cleanup logic. The file persists between runs, and without atomic overwrites, job failures may leave partially written CSVs.

### Cross-Cutting Patterns

**File-Based Staging as the De Facto Message Bus.** Two-thirds of all side effects are staging files. The `context.resource_flow_temp_folder` directory carries intermediate datasets between job steps. Any migration must either preserve this (using cloud storage) or replace it with in-memory passing, streaming, or direct database-to-database transfers. The staging-file pattern provides natural checkpoints -- if a downstream job fails, the staging file is still available for re-reading.

**Cleanup Discipline Varies by Region.** The seven smaller regions have zero medium-risk effects, while BRA and USA have significant gaps. The correlation between "Copy_of" job variants and missing cleanup is particularly strong.

**Hardcoded Environment Indicators in Filenames.** Multiple BRA jobs embed `test_` directly in staging file paths rather than using `context.parameter_Env_Prefix`. A cleanup script deleting all `test_*` files would destroy production-generated files from these misconfigured jobs.

**Staging Files as Implicit Contracts.** The staging file pattern creates an undocumented contract between upstream writers and downstream readers. If either side changes the filename, directory, or column layout, the other breaks silently.

**Interaction with Error Handling Gaps.** Jobs that lack cleanup logic often also lack error handling. A failed job leaves behind partial staging files and produces no error notification. Subsequent runs may overwrite the partial file or fail if headers are corrupted.

---

## Consolidated Migration Takeaways

### Business Rules

1. **Map every business rule to a migration test case.** The rules catalog provides the specification for what the target platform must do. Each rule should have a corresponding automated test.
2. **Fiscal period mapping is a prerequisite.** The Global_Raw.Fiscal_Periods reference table must be migrated first, as all regions depend on it.
3. **Currency conversion logic varies.** Different regions and data categories (actuals vs. budget vs. forecast) use different conversion approaches that must be preserved.

### Custom Code

4. **Replace tJava_StoreJobStats with a shared metrics utility.** This eliminates 180+ duplicate code blocks and provides a single point of maintenance.
5. **Preserve shared routines as reusable libraries.** DataQuality, DqStringHandling, DQTechnical, and SQLike should become shared utility libraries in the target platform.
6. **Audit tJavaRow components for implicit business logic.** The 102 instances concentrated across BRA, JPN, and USA at 32 each require per-component review.
7. **Prioritize the GENERIC tJavaFlex components for early migration.** The 13 tJavaFlex components carry the highest per-snippet complexity and risk.
8. **Build a unified Java-to-target translation toolkit.** This serves both the 509 custom code snippets and the hundreds of tMap expressions.
9. **Unify the shared routine footprint across all regions.** Resolve the inconsistent distribution of DQTechnical and SQLike during migration.

### State Management

10. **Replace file-based staging with cloud-native storage.** Cloud storage lifecycle policies (TTL-based deletion) eliminate the cleanup problem entirely.
11. **Enforce cleanup as a platform policy.** The opt-in model has a 21% failure rate. The target platform should enforce cleanup through framework-level guarantees or pipeline validation gates.
12. **Audit the 119 medium-risk effects before migration.** Determine whether each file is still needed, whether cleanup was intentionally omitted, or whether the omission was an oversight.
13. **Catalog implicit staging contracts.** Document every staging file serving as a data exchange point: producing job, consuming job(s), expected schema, and freshness window.
14. **Eliminate hardcoded paths.** Replace `test_` prefix filenames with environment-parameterized paths using a convention like `{env}/{region}/{job_name}/{timestamp}.csv`.
15. **Implement monitoring for side-effect accumulation.** A daily metric on file count and total size in the staging directory provides early warning of cleanup policy failures.

---

*Generated by intodoc -- documentation automation for integration platforms.*
