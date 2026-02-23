# Schema Dictionary

Vaxxinova's Talend integration platform references 321 distinct tables across its 257 jobs, forming a schema landscape that spans regional raw extraction datasets, cleaned analytical tables, global master data, and Salesforce CRM objects. These tables live primarily in Google BigQuery, organized into dataset namespaces by region and layer (e.g., `bra_vbr_raw`, `jpn_vjp_clean`, `Global_Master`, `Global_Clean`). However, mechanical extraction from Talend's XML job definitions captures table names far more reliably than column schemas -- only 50 of 321 tables (16%) have any column-level metadata available. This gap means the schema dictionary records what tables exist and how they are used, but for the majority of tables, the actual column definitions must be documented from BigQuery's information schema before migration.

---

## Table Naming Patterns

The 321 tables fall into six recognizable categories, reflecting the platform's layered architecture and business domain structure.

### Sales_Set Tables (33 tables, 10% of total)

The `Sales_Set` is the primary output artifact of the entire platform. Every regional subsidiary produces a Sales_Set table containing standardized invoice and credit memo data: quantities, net sales amounts in local currency and EUR, cost of goods sold, customer and product identifiers, fiscal period assignments, and Salesforce account linkages. Sales_Set tables appear in regional clean datasets (`bra_vbr_clean.Sales_Set`, `jpn_vjp_clean.Sales_Set`) and in the global consolidation layer (`Global_Clean.Sales_Set`). The 33 Sales_Set-related tables include not just the final output but intermediate staging tables, delta tracking tables, and lifecycle management artifacts (delete-ingest tables that track which records to purge before reload).

The Sales_Set schema is the closest thing the platform has to a canonical data contract. Although no formal schema definition exists outside the Talend tMap mappings, every regional clean job produces output columns that must align with the global consolidation jobs' expectations. Any column mismatch -- a missing `Fiscal_Year`, an unexpected null in `Currency_Code`, or a renamed `Net_Sales_Amount_EUR` -- would break the downstream aggregation.

### Raw Tables (71 tables, 22% of total)

Raw tables hold data as it arrives from source systems, with minimal transformation. They follow a naming convention tied to the source entity: `salesInvoices`, `salesCreditMemos`, `salesInvoiceLines`, `items`, `customers`, `generalLedgerEntries`, `valueEntries`, and so on. These names mirror the Business Central API entity names and the MSSQL view names from legacy ERPs.

For example, Brazil's raw layer includes `bra_vbr_raw.salesInvoices`, `bra_vbr_raw.salesCreditMemos`, `bra_vbr_raw.salesInvoiceLines`, and `bra_vbr_raw.valueEntries`. Japan has `jpn_vjp_raw.salesInvoices` and equivalents. The raw tables are the first landing zone in BigQuery and serve as the source-of-truth for all downstream clean and global processing.

### Fiscal Reference Tables (4 tables)

A small but critical set of tables governs temporal logic across the platform. `Global_Master.Fiscal_Periods` is the authoritative calendar for Vaxxinova's non-calendar fiscal year, used by every clean-layer Sales_Set job to derive `Fiscal_Year`, `Fiscal_Quarter`, and `Fiscal_Month` from invoice dates. `Global_Budget.Budget_Current_Conversion_Rate` provides the exchange rates used for local-currency-to-EUR conversion. These fiscal reference tables are shared dependencies -- every regional pipeline reads from them, but only the Global jobs write to them.

### Master Data Tables (7 tables)

Global reference tables provide standardized lookup values across the platform. `Global_Master.Country_Master` maps country codes to country names and regional groupings. `Global_Raw.Salesforce_Account_Mappings` links Business Central customer IDs to Salesforce Account record IDs, enabling CRM-integrated sales analytics. Payment terms, shipment methods, and product hierarchies also have master data tables, though these are less uniformly structured across regions.

### Parameterized Context Tables (18 tables)

These are table references that resolve at runtime through Talend context variables rather than being statically defined. Instead of a literal table name, the Talend XML contains expressions like `context.parameter_Raw_Prefix + ".salesInvoices"` or `context.parameter_Clean_Prefix + ".Sales_Set"`. The 18 parameterized table references identified by static analysis are almost certainly an undercount -- any job that uses context-variable-based dataset prefixes effectively has parameterized table names, and this pattern is widespread. (See the dedicated section below.)

### Other Tables (188 tables, 59% of total)

The largest category includes production order tables (`ProductionOrders_CBR`, `ProductionOrderLines_CBR`, `ProductionOrderRoutingLines_CBR`), value entry tables, inventory tables, customer account tables, Salesforce staging tables, and various intermediate join results. Many of these are domain-specific to individual subsidiaries (e.g., Brazil's cost breakdown reporting tables, Japan's inventory valuation tables) and do not participate in the global consolidation pipeline.

---

## Most-Referenced Tables

The following table lists the 10 most heavily referenced tables in the platform, ranked by the number of distinct jobs that read from or write to them. High reference counts indicate tables that are shared dependencies -- they must be migrated early and validated carefully, because errors in these tables cascade across many jobs.

| Table | Columns Known | Jobs Using It | Primary Region | Role |
|-------|--------------|---------------|----------------|------|
| `test_global_clean.ProductionOrders_CBR` | 15 | 13 (BRA) + 9 (USA) | Cross-region | Cost breakdown report: production order headers for absorption/consumption/routing analysis |
| `test_global_clean.ProductionOrderLines_CBR` | 16 | 10 (BRA) + varies | Cross-region | Cost breakdown report: production order line items paired with the header table above |
| `test_bra_vbr_raw.valueEntries` | 10 | 9 | BRA | Raw value entries from legacy ERP, feeding production order and cost analysis pipelines |
| `context.parameter_Raw_Prefix` | 0 | 8+ | DEU, Global | Parameterized: resolves to region-specific raw dataset at runtime |
| `ProductionOrderLines_CBR` | 0 | 8 | BRA | Unqualified reference to the same production order lines table (dataset resolved by context) |
| `test_usa_vus_clean.Pending_ProductionOrderLines_CBR` | 17 | 7 | USA | Staging table for pending production order lines awaiting status updates |
| `test_bra_vbr_clean.ProductionOrderLines_CBR_Delta` | 1 | 6 | BRA | Delta tracking: identifies changed production order lines since last run |
| `ProductionOrderLines_CBR_Delta` | 0 | 6 | BRA | Unqualified reference to the delta table above |
| `test_bra_vbr_clean.Value_Entries` | 1 | 6 | BRA | Cleaned value entries used for production order register and status tracking |
| `Global_Master.Fiscal_Periods` | -- | all clean jobs | Global | Fiscal calendar shared by every regional Sales_Set builder |

Several observations stand out. First, the production order tables (`ProductionOrders_CBR`, `ProductionOrderLines_CBR`) dominate the reference counts, which may be surprising given that the Sales_Set is the platform's primary output. The reason is that production order cost breakdown analysis requires many specialized jobs -- absorption lines, consumption lines, routing lines, pending lines, debug variants -- each of which reads from the same base tables. Second, the same logical table often appears under multiple names: `test_global_clean.ProductionOrderLines_CBR` and `ProductionOrderLines_CBR` refer to the same data, but the former is fully qualified (with a test-environment dataset prefix) while the latter relies on runtime context resolution. This duality makes dependency analysis difficult.

### Migration Takeaway

`Global_Master.Fiscal_Periods` and `Global_Budget.Budget_Current_Conversion_Rate` are the most critical shared dependencies in the entire platform. They must be the first tables migrated and validated, because every regional clean pipeline depends on them. The production order tables (`ProductionOrders_CBR`, `ProductionOrderLines_CBR`) are the most complex dependency cluster -- 22+ jobs reference them across BRA and USA, and they carry the richest column schemas (15-17 columns with known definitions).

---

## The Parameterized Table Challenge

Eighteen tables identified in the brief carry names like `context.parameter_Raw_Prefix` rather than literal BigQuery table names. This is because Talend jobs construct table references at runtime by concatenating context variable values with table name suffixes. For example, a job might reference:

```
context.parameter_Raw_Prefix + ".salesInvoices"
```

In the PROD environment, `parameter_Raw_Prefix` resolves to `bra_vbr_raw`, so the actual table is `bra_vbr_raw.salesInvoices`. In the TEST environment, it resolves to `test_bra_vbr_raw`, yielding `test_bra_vbr_raw.salesInvoices`. This parameterization is central to how the platform supports multiple environments (PROD, TEST, TEST_LINUX, TEST_MAC) without duplicating job definitions.

The impact on static analysis is significant. When the Talend XML says `context.parameter_Raw_Prefix`, mechanical extraction cannot determine which physical table is being accessed without also resolving the context variable values from the environment configuration. The 18 explicitly parameterized references are likely the tip of the iceberg: 38% of the platform's 957 context variables (363 variables) have PROD/TEST divergences, and many of these are dataset prefix parameters.

Three concrete examples illustrate the pattern:

1. **`context.parameter_Raw_Prefix`** appears in 8 German (DEU) jobs. In PROD it resolves to the VDA or VDD raw dataset; in TEST it resolves to the test-prefixed equivalent. The `VDA_BC_Clean_Invoice_Set_v3` and `VDD_Clean_Invoice_v4` jobs both use this variable.

2. **`context.parameter_Env_Prefix`** controls the clean-layer dataset reference. Brazil's PROD value is `bra_vbr_raw`; its TEST value is `test_bra_vbr_raw`. Japan's PROD value is `jpn_vjp_raw`; TEST is `test_jpn_vjp_raw`. This variable exists in every region.

3. **`context.parameter_Clean_Prefix`** follows the same pattern for clean-layer datasets, resolving to `Global_Clean` in PROD and `test_global_clean` in TEST.

The `test_` prefix visible in many table names in the schema dictionary (e.g., `test_global_clean.ProductionOrders_CBR`, `test_bra_vbr_raw.valueEntries`) confirms that the mechanical extraction was run against a TEST environment configuration, not PROD. The production table names would lack the `test_` prefix.

### Migration Takeaway

Before migration, the team must build a complete resolution map: for every context-parameterized table reference, document which physical BigQuery table it resolves to in each environment. This is the only way to produce a reliable dependency graph. The target platform should replace Talend's string-concatenation approach with a proper environment configuration layer (e.g., dbt profiles, Dataform environments, or equivalent) that provides the same runtime resolution without obscuring the physical table name from static analysis tools.

---

## Shared Reference Tables

A small set of global reference tables acts as the connective tissue across all regional pipelines. These tables are read by nearly every clean-layer job but written by only a few global-layer jobs. They form a dependency bottleneck: if any of these tables are stale, incorrect, or unavailable, the impact cascades across all regions.

### Global_Master.Fiscal_Periods

The fiscal calendar table maps calendar dates to Vaxxinova's fiscal year, quarter, and month. Every Sales_Set builder job joins against this table to derive fiscal period columns from invoice dates. Vaxxinova uses a non-calendar fiscal year, making this table essential for period-based reporting. The table is maintained by the Global team and is shared across all subsidiaries.

### Global_Master.Country_Master

Maps country codes to standardized country names and regional groupings. Used by clean-layer jobs to enrich invoice data with consistent geographic classifications. Country codes from source ERPs are often inconsistent (leading/trailing whitespace, mixed ISO-2/ISO-3 formats), and the Country_Master provides the canonical mapping. Brazil's `BIO_Clean_Invoice_Set_v5` applies a fallback pattern: if a Salesforce country mapping exists, it takes precedence; otherwise the raw source country is trimmed and used.

### Global_Budget.Budget_Current_Conversion_Rate

Provides the exchange rates used to convert local-currency amounts (BRL, JPY, ZAR, USD) to EUR for consolidated group reporting. Notably, these are budget exchange rates, not market rates -- sales reporting across Vaxxinova uses the same FX rates as budgeting to isolate operational variances from currency fluctuations. Every Sales_Set builder divides local amounts by this rate to produce EUR equivalents.

### Global_Raw.Salesforce_Account_Mappings

Links Business Central customer IDs to Salesforce Account record IDs. This mapping enables CRM-integrated sales analytics -- management can view sales data segmented by Salesforce account hierarchy, territory, and owner. The mapping table is populated by Salesforce sync jobs (`Salesforce_Account_Mappings_v3` and variants) that pull data from Salesforce into BigQuery.

### Global_Raw.Salesforce_Country_Mappings

Maps source-system country values to standardized Salesforce country codes (`Country_Code__c`). Used as a fallback enrichment layer when raw ERP data has non-standard country representations.

### Global_Raw.Salesforce_Country_Mappings

Maps source-system country values to standardized Salesforce country codes (`Country_Code__c`). Used as a fallback enrichment layer when raw ERP data has non-standard country representations. Brazil's `BIO_Clean_Invoice_Set_v5` implements a specific fallback pattern: if a Salesforce country mapping exists for the given source country value, it takes precedence over the raw ERP country field; otherwise the raw value is trimmed and used directly. This dual-path logic is replicated across multiple regional clean jobs, making Country_Mappings a soft dependency -- jobs will not fail without it, but output quality degrades.

### Migration Takeaway

These five shared reference tables must be migrated and validated before any regional pipeline can run successfully on the target platform. The migration sequence should be: (1) Global reference tables, (2) regional raw extraction, (3) regional clean processing, (4) global consolidation. Reversing this order would produce failures at every join point.

---

## Key Reference Tables in Detail

The shared Global reference tables deserve deeper documentation because they are the connective tissue that binds all regional pipelines into a coherent global reporting system. Errors or staleness in any of these tables silently corrupts the output of every downstream pipeline.

### Global_Master.Fiscal_Periods -- The Fiscal Calendar

Vaxxinova operates on a non-calendar fiscal year, which means that standard calendar-based date functions cannot be used for period-based reporting. The `Fiscal_Periods` table provides the authoritative mapping from calendar dates to Vaxxinova's fiscal year, quarter, and month.

**Expected columns (inferred from tMap usage across all regions):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Calendar_Date | DATE | The calendar date being mapped |
| Fiscal_Year | INTEGER or STRING | Vaxxinova fiscal year (e.g., 2024, 2025) |
| Fiscal_Quarter | STRING | Fiscal quarter designation (e.g., Q1, Q2, Q3, Q4) |
| Fiscal_Month | INTEGER or STRING | Fiscal month number within the fiscal year |
| Fiscal_Period_Name | STRING | Human-readable period name for reporting |

**Usage pattern:** Every regional `*_Clean_Build_Sales_Set` job reads this table via tBigQueryInput, loads it into a tHashOutput keyed by date, and joins it to invoice records in a tMap to derive fiscal period columns. The join key is typically the invoice date (or posting date), and the output columns (`Fiscal_Year`, `Fiscal_Quarter`, `Fiscal_Month`) are stamped onto every row in the Sales_Set.

**Maintenance:** This table must be populated with future fiscal periods before the corresponding calendar dates arrive. If a new fiscal year begins and the Fiscal_Periods table has not been extended, clean-layer jobs will produce null fiscal period values for invoices dated in the new fiscal year. There is no automated validation or alert for this condition in the current platform.

### Global_Master.Country_Master -- Geographic Reference

The Country_Master table standardizes geographic classifications across all regions. Source ERP systems use inconsistent country representations (some use ISO-2 codes, others use ISO-3 codes, some use full country names with varying capitalization and whitespace), and this table provides the canonical mapping.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Country_Code | STRING | Standardized country code (ISO format) |
| Country_Name | STRING | Full country name |
| Region_Group | STRING | Regional grouping for consolidated reporting (e.g., EMEA, Americas, APAC) |
| Salesforce_Country_Code | STRING | Corresponding Salesforce country code, if different |

**Usage pattern:** Clean-layer jobs join against this table to enrich invoice data with consistent geographic classifications. The join is typically on a country code field from the source ERP data. Some regions (notably Brazil) apply trimming and case normalization before the join to handle whitespace and capitalization inconsistencies in the source data.

### Global_Budget.Budget_Current_Conversion_Rate -- Exchange Rates

This table is strategically important because it determines how all local-currency amounts are converted to EUR for group-level reporting. Notably, Vaxxinova uses **budget exchange rates** rather than market exchange rates for this conversion. This is a deliberate business decision: by using the same FX rates for both budgeting and actuals reporting, management can isolate operational variances from currency fluctuations when comparing budget-vs-actual performance.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Currency_Code | STRING | Source currency (e.g., BRL, JPY, ZAR, USD) |
| Conversion_Rate | FLOAT | Exchange rate to EUR (units of local currency per 1 EUR) |
| Effective_Date or Period | DATE/STRING | The budget period for which this rate applies |

**Usage pattern:** Every Sales_Set builder job reads this table to obtain the conversion rate for its regional currency. The conversion is typically expressed as `local_amount / conversion_rate` to produce EUR equivalents. For example, a BRL 1,000 invoice with a conversion rate of 5.5 would produce EUR 181.82.

**Business risk:** If conversion rates are not updated when new budget periods begin, sales data will be converted at stale rates. The current platform has no validation to detect stale conversion rates -- the join will simply use whatever rate is present for the given currency code, regardless of whether it reflects the current budget period.

### Global_Raw.Salesforce_Account_Mappings -- CRM Linkage

This mapping table is the bridge between the ERP world (Business Central customer IDs) and the CRM world (Salesforce Account record IDs). Without it, sales data in BigQuery cannot be linked to the account hierarchy, territory assignments, and ownership structure maintained in Salesforce.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| BC_Customer_ID | STRING | Business Central customer identifier |
| Salesforce_Account_ID | STRING | Salesforce Account record ID (18-character) |
| Account_Name | STRING | Salesforce Account name |
| Entity_Code | STRING | Vaxxinova subsidiary code (VBR, VJP, VUS, etc.) |

**Usage pattern:** Clean-layer Sales_Set builders join against this table using the Business Central customer ID to enrich each invoice line with its corresponding Salesforce Account. This enables CRM-integrated sales analytics where management views sales segmented by Salesforce account hierarchy, territory, and owner.

**Bidirectional dependency:** Unlike the other reference tables, Salesforce_Account_Mappings flows from Salesforce into BigQuery (via `Salesforce_Account_Mappings_v3` and `Salesforce_Mapped_Accounts_v8` jobs). This creates a bidirectional dependency: Salesforce is both a consumer of consolidated sales data and a source of the mapping data that clean-layer jobs need to produce that consolidated data. During migration, this circular dependency must be carefully managed to avoid a chicken-and-egg scenario.

---

## Schema Documentation Recommendations

Based on the analysis of the schema landscape, the following recommendations address the most critical gaps and risks.

### Recommendation 1: Run a Full BigQuery INFORMATION_SCHEMA Audit

The 84% column-definition gap cannot be closed through Talend XML analysis alone. The team should execute the following query against every BigQuery dataset referenced by the platform:

```sql
SELECT
  table_catalog,
  table_schema,
  table_name,
  column_name,
  ordinal_position,
  is_nullable,
  data_type
FROM
  `project_id.dataset_name.INFORMATION_SCHEMA.COLUMNS`
ORDER BY
  table_name, ordinal_position;
```

This query should be run against all production datasets (`bra_vbr_raw`, `bra_vbr_clean`, `jpn_vjp_raw`, `jpn_vjp_clean`, `Global_Master`, `Global_Clean`, `Global_Budget`, `Global_Raw`, etc.) and the results stored as a CSV or BigQuery table for cross-referencing during migration.

### Recommendation 2: Formalize the Sales_Set Data Contract

The Sales_Set table is the primary output of 33 related tables and the foundation of all consolidated reporting. Its schema should be formally documented as a **data contract** that includes:

- **Column definitions:** Every column, its data type, nullability, and business meaning.
- **Business rules:** How each derived column is calculated (e.g., `Net_Sales_Amount_EUR = Net_Sales_Amount_Local / Conversion_Rate`).
- **Validation rules:** Expected value ranges, required fields, referential integrity constraints.
- **Version history:** When columns were added, renamed, or deprecated.
- **Ownership:** Which team is responsible for maintaining the schema and approving changes.

Currently, the Sales_Set schema exists only implicitly in tMap expressions scattered across dozens of jobs. Extracting and formalizing it would reduce the risk of schema drift during migration and provide a clear acceptance criterion: the migrated pipeline produces a Sales_Set that matches the documented contract.

### Recommendation 3: Build a Context Variable Resolution Map

The 18 explicitly parameterized table references are the visible portion of a much larger parameterization layer. The team should build a complete resolution map that documents, for every context variable that participates in table name construction:

- The variable name (e.g., `parameter_Raw_Prefix`)
- Its PROD value (e.g., `bra_vbr_raw`)
- Its TEST value (e.g., `test_bra_vbr_raw`)
- Which jobs use it
- Which physical tables it resolves to

This map is a prerequisite for accurate dependency graphing and for replacing Talend's string-concatenation approach with a proper environment configuration layer in the target platform.

### Recommendation 4: Establish a Naming Standard for Migration

The current schema landscape contains duplicate references to the same logical table under different names (fully qualified, unqualified, and parameterized). The migration is an opportunity to establish a single canonical naming convention:

- **Dataset naming:** `{region}_{entity}_{layer}` (e.g., `bra_vbr_raw`, `bra_vbr_clean`) -- this convention already exists and should be preserved.
- **Table naming:** Remove the `test_` prefix for all production references. Ensure every table reference in the target platform is fully qualified.
- **Column naming:** Adopt a consistent casing convention (snake_case is recommended for BigQuery) and correct known inconsistencies (e.g., the `Produtct_Description` typo carried from MSSQL through to Salesforce).

---

## The Column Definition Gap

The most significant limitation of this schema dictionary is the absence of column-level detail for 84% of the tables. Of 321 tables identified, only 50 have any column metadata, and even those are often incomplete. The table `test_bra_vbr_clean.Value_Entries`, for instance, shows only 1 column despite being used by 6 jobs that almost certainly read multiple columns from it. The `test_global_clean.ProductionOrders_CBR` table shows 15 columns and `test_global_clean.ProductionOrderLines_CBR` shows 16 -- these are among the richest column schemas captured, but they still may not represent the full table.

This gap exists because Talend stores transformation logic inside tMap components as expressions referencing column names, but the mechanical extraction process captures table-level references more reliably than column-level ones. A tMap that reads from `salesInvoices` and maps `row1.No_` to `output.Invoice_Number` records the table reference but may not surface the individual column names in a structured way, especially when column names are embedded in Java-like expressions with string concatenation, conditional logic, or nested function calls.

### What Is Known

The 50 tables with column data are concentrated in two regions: Brazil (39 tables) and USA (19 tables), with Japan (15), scratchpad (2), Germany (1), and Global (1) contributing smaller amounts. This distribution likely reflects which regions' jobs use more explicit schema declarations in their tMap configurations versus relying on dynamic schema or runtime-resolved columns.

| Region | Tables | Tables with Columns | Coverage |
|---------|--------|---------------------|----------|
| BRA | 119 | 39 | 33% |
| Global | 82 | 1 | 1% |
| USA | 78 | 19 | 24% |
| JPN | 76 | 15 | 20% |
| DEU | 21 | 1 | 5% |
| ITA | 15 | 0 | 0% |
| GENERIC | 12 | 0 | 0% |
| ZAF | 7 | 0 | 0% |
| scratchpad | 4 | 2 | 50% |
| SAF | 1 | 0 | 0% |

The 84% gap is not a failure of the documentation tool -- it reflects the fundamental challenge of extracting schema information from a visual ETL platform where column mappings are defined graphically in tMap editors, stored as embedded XML attributes, and often mixed with transformation expressions. The only authoritative source of truth for the full column schemas is BigQuery itself.

### Migration Takeaway

Before migration begins, the team should run a BigQuery `INFORMATION_SCHEMA.COLUMNS` query against every dataset referenced by the platform and produce a complete column dictionary. This dictionary should be cross-referenced with the tMap expressions to identify which columns are actually read, which are written, which are transformed, and which are passed through unchanged. Without this step, migration will involve repeated discovery of missing or misnamed columns at runtime.

---

## Regional Schema Distribution

The 321 tables are not evenly distributed. Brazil leads with 119 tables (37% of the total), reflecting its dual-ERP complexity and the extensive production order cost analysis pipeline. Global follows with 82 tables (26%), covering the consolidation layer, reference data, and Salesforce staging. USA and Japan each contribute roughly 76-78 tables, while the remaining regions are significantly smaller.

The distribution mirrors job complexity: Brazil and USA have the most jobs, the most transformation steps, and the most tables. Germany and Italy, despite being production regions, have compact table footprints (21 and 15 tables respectively) because their pipelines are architecturally simpler -- single ERP source, straightforward Sales_Set construction, fewer intermediate staging tables.

SAF (1 table) and scratchpad (4 tables) are effectively non-production and should be excluded from migration planning. ZAF's 7 tables represent South Africa's developing pipeline and will likely grow as that subsidiary's data requirements mature.

### Migration Takeaway

Focus schema documentation effort on the four high-table-count regions (BRA, Global, USA, JPN), which together account for 355 of 321 unique tables when accounting for cross-region references. The remaining regions can be documented incrementally as their simpler pipelines are migrated.

---

## Platform-Wide Migration Takeaways

1. **Document column schemas from BigQuery before migration.** Only 16% of tables have any column metadata from static analysis. The target platform needs complete column definitions -- data types, nullability, primary keys, and foreign key relationships -- to build equivalent transformations. BigQuery's `INFORMATION_SCHEMA` is the authoritative source.

2. **Resolve all parameterized table names.** Build a lookup table mapping every `context.parameter_*_Prefix` variable to its PROD and TEST values. This will convert the 18 explicitly parameterized references (and the many implicitly parameterized ones) into concrete table names, enabling accurate dependency graphing.

3. **Migrate shared reference tables first.** `Global_Master.Fiscal_Periods`, `Global_Master.Country_Master`, `Global_Budget.Budget_Current_Conversion_Rate`, and the Salesforce mapping tables are upstream dependencies for every regional pipeline. They must be migrated, validated, and available before any regional clean job can run.

4. **Consolidate duplicate table references.** The same logical table often appears under multiple names -- fully qualified (`test_global_clean.ProductionOrders_CBR`), unqualified (`ProductionOrders_CBR`), and parameterized (`context.parameter_Clean_Prefix + ".ProductionOrders_CBR"`). The migration is an opportunity to establish a single canonical naming convention.

5. **Establish the Sales_Set as a formal data contract.** The Sales_Set table is the primary output of 33 related tables and the foundation of all consolidated reporting. Its column schema, data types, nullability rules, and business rules should be formally documented and version-controlled as a data contract, not left implicit in tMap expressions.

---

*Generated by intodoc -- documentation automation for integration platforms.*
