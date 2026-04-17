# Data Flows, Schema Dictionary, and Data Quality

> End-to-End Data Lineage, Source-to-Target Mappings, Table Inventory, and Validation Rules

Vaxxinova's Talend integration platform moves veterinary vaccine sales, product, customer, and financial data from regional ERP systems through a three-layer warehouse architecture in Google BigQuery. Source systems -- on-premises Microsoft SQL Server databases, Microsoft Dynamics 365 Business Central REST APIs, Salesforce CRM, and third-party SFTP feeds -- push raw transactional data into BigQuery's raw layer. From there, Talend jobs clean, enrich, and reshape the data into standardized business-ready datasets in the clean layer. Finally, global consolidation jobs merge regional clean data into unified reporting tables. Across the platform, 257 Talend jobs execute 4,715 transformation steps spanning 10 regional codebases, referencing 321 distinct tables with 1,102 embedded data quality rules.

---

## The Canonical Flow Pattern

Nearly every Talend job follows a single dominant pattern: **tBigQueryInput -> tMap -> tBigQueryOutput**.

**tBigQueryInput** reads rows from a BigQuery table or view. It is the most heavily used component, appearing 619 times across all jobs. Each input component binds to a specific BigQuery dataset and table -- for example, `bra_vbr_raw.salesInvoices` or `Global_Master.Fiscal_Periods`.

**tMap** is the universal transformation engine. With 565 instances across all regions, it appears in every production job. A tMap component receives one or more input flows, applies join logic, column mappings, expression-based transformations, type conversions, and conditional routing, then emits one or more output flows. It is where the business rules live: currency defaults, fiscal period derivations, country code fallbacks, Salesforce account lookups, and entity tagging all happen inside tMap expressions. The tMap is Talend-proprietary -- its expression language, join semantics, and null-handling behavior have no direct equivalent in other platforms.

**tBigQueryOutput** writes the transformed rows to a target BigQuery table. With 360 instances, it is the third most common component. Depending on the job, the write mode is either TRUNCATE (full table replacement) or INSERT (append). Some jobs write to both a regional clean table and the Global_Clean dataset in the same run, using tHashOutput to fork the data flow.

Beyond this core trio, several supporting patterns recur:

- **tFixedFlowInput + tHashOutput** (228 + 269 instances): Builds in-memory lookup tables for reference data.
- **tBigQuerySQLRow** (180 instances): Executes ad-hoc SQL, most commonly DELETE statements for idempotent reloads.
- **tMSSqlInput**: Reads from on-premises MSSQL databases for legacy ERP regions.
- **tRESTClient + tExtractJSONFields**: Calls Business Central OData/REST APIs and parses JSON responses. Concentrated in GENERIC shared jobs.
- **tSalesforceInput / tSalesforceOutput**: Reads from and writes to Salesforce CRM.

---

## Source System Diversity

Data enters the platform from four distinct source system categories:

### MSSQL: On-Premises ERP Extraction

Five named MSSQL connections serve regional subsidiaries: `connection_BRA_VBR_MSSQL` (Brazil/Biovet), `connection_JPN_VJP_MSSQL` (Japan), `connection_ITA_VAX_MSSQL` (Italy, also shared with SAF/ZAF), `connection_SAF_VSA_MSSQL` (South Africa), and `connection_USA_NPL_MSSQL` (USA/Newport Labs). These connections extract data from SQL Server views -- for example, Brazil's `BRA_VBR_RAW` job reads from `VW_Finished_Product`, `VW_Sales`, and `VW_Returns` views. MSSQL extraction requires network connectivity between the Talend runtime and the on-premises database servers.

### REST APIs: Business Central

Regions migrating to Microsoft Dynamics 365 Business Central use REST API extraction. The GENERIC shared jobs handle OAuth token acquisition against Azure AD, paginated API calls, JSON response parsing, and BigQuery loading. OData v4 variants exist for some entities, providing an alternative protocol for the same data.

### Salesforce CRM

Salesforce serves as both a source and a sink. As a source, `tSalesforceInput` reads account mappings and country mappings. As a sink, product sync jobs push product master data -- including vaccine-specific fields like Species, Subspecies, Hierarchy, and Doses -- back into Salesforce's Product2 object. Product codes are namespaced by region (e.g., `BIO-` prefix for Biovet products).

### SFTP / External Feeds

The USA region has a unique external data source: Animalytix third-party distributor data, delivered via SFTP and staged in Google Cloud Storage (`tGSGet`).

---

## Data Lineage: From Operational Systems to Analytics

Data follows a predictable three-layer path through the platform, though specifics vary by region and ERP vintage.

### Layer 1: Raw Extraction

Regional raw extraction jobs (`BRA_VBR_RAW`, `JPN_VJP_RAW`, `ITA_VIT_RAW`, `DEU_VDD_RAW`, etc.) pull transactional data from source systems and land it in BigQuery datasets with a `raw` prefix (e.g., `bra_vbr_raw`, `jpn_vjp_raw`). Jobs at this layer perform minimal transformation -- column mapping, type conversion, and CSV staging. For MSSQL sources, extraction uses SQL Server views. For Business Central sources, the GENERIC shared jobs handle API pagination and JSON parsing before loading.

### Layer 2: Regional Clean

Clean-layer jobs read from the raw BigQuery datasets and apply the bulk of business logic. The canonical operation is Sales_Set construction: invoices, credit memos, and line items are joined with fiscal period calendars, country master data, currency conversion rates, payment terms, shipment methods, and Salesforce account mappings. The output is a standardized `Sales_Set` table following a common schema across all regions.

For example, Japan's `VJP_Clean_Build_Sales_Set` reads from `jpn_vjp_raw`, assigns JPY as the currency, tags the entity as 'Vaxxinova Japan', derives fiscal periods from Global_Master.Fiscal_Periods, looks up Salesforce account IDs, converts amounts to EUR, and writes the result to both `jpn_vjp_clean.Sales_Set` and `Global_Clean.Sales_Set`.

This dual-write pattern -- local clean plus global clean -- is the primary mechanism for data flowing from regional to consolidated views. Version suffixes (v1 through v26) on job names indicate iterative refinement.

### Layer 3: Global Consolidation

Global-layer jobs read from the Global_Clean dataset and produce the final analytics-ready datasets. `Global_Build_Sales_Set` applies additional fiscal period mappings, country enrichment, and Salesforce account linkages. `Budget_Build_Sales_Set` and `Prior_Build_Sales_Set` process budget/forecast and prior-year data respectively, enabling variance analysis.

The global layer also runs Salesforce synchronization jobs that pull CRM data into BigQuery as reference tables, closing the loop between the warehouse and the CRM.

### End-to-End Lineage Example: Japan (JPN)

**Step 1: MSSQL Source Extraction.** The `JPN_VJP_RAW` extraction job connects to the VJP on-premises SQL Server, reads from a view joining invoice headers with line items, extracts fields (document number, line number, customer number, item number, quantity, unit price, amount, posting date, currency code JPY). The tMap performs minimal transformation: GUID normalization, date format standardization, null-safe type casting. Output: `jpn_vjp_raw.salesInvoiceLines` in BigQuery.

**Step 2: Reference Data Loading.** Before clean-layer transformation, several reference data jobs must have completed: `Salesforce_Account_Mappings_v3` refreshes account mappings, the Fiscal_Periods table must contain current fiscal periods, and `Budget_Current_Conversion_Rate` must contain the JPY-to-EUR rate.

**Step 3: Clean-Layer Transformation.** `VJP_Clean_Build_Sales_Set` reads the raw invoice line, joins it with Fiscal_Periods (posting date -> fiscal year/quarter/period), Salesforce_Account_Mappings (customer number -> Salesforce Account ID), and Budget_Current_Conversion_Rate (currency code + period -> EUR amount). Applies hardcoded values: entity = 'Vaxxinova Japan', entity_code = 'VJP', default_currency = 'JPY'. Writes to both `jpn_vjp_clean.Sales_Set` and `Global_Clean.Sales_Set`.

**Step 4: Global Consolidation.** `Global_Build_Sales_Set` reads from `Global_Clean.Sales_Set`, applies global fiscal alignment, country name standardization, and budget variance calculation. Output: `Global_Reporting.Sales_Set`.

**Step 5: Dashboard Consumption.** A BI tool reads from `Global_Reporting.Sales_Set` and presents VJP subsidiary performance alongside all other regions in EUR.

**Total transformation steps for one record:** approximately 20 distinct operations across 3 Talend jobs, touching 6 BigQuery tables. If any intermediate step fails silently (and with zero error handling in Japan's pipeline, every step can fail silently), downstream dashboards show stale or missing data with no indication of the problem.

---

## Recurring Data Flow Patterns

### Pattern 1: The Full-Table TRUNCATE Reload

```
tBigQueryOutput (mode: TRUNCATE) --> target table completely replaced
```

Simple and idempotent but creates a window of complete data unavailability. If the job fails mid-write, the table is left empty or partially populated. Should be replaced with staging-table-swap patterns during migration.

### Pattern 2: The Delta DELETE-then-INSERT

```
tBigQuerySQLRow (DELETE WHERE delta_id IN (...)) --SUBJOB_OK--> tBigQueryOutput (mode: INSERT)
```

More surgical than TRUNCATE, refreshing a subset of records while preserving historical data. The 180 instances of tBigQuerySQLRow across the platform indicate how deeply embedded this pattern is. Still creates a risk window between DELETE and INSERT.

### Pattern 3: The In-Memory Lookup Star Join

```
tFixedFlowInput (fiscal periods) --> tHashOutput (hash_FiscalPeriods)
tFixedFlowInput (country master)  --> tHashOutput (hash_CountryMaster)
tFixedFlowInput (conversion rates) --> tHashOutput (hash_ConversionRates)
...
tBigQueryInput (raw sales data) --> tMap [joins against all hash tables] --> tBigQueryOutput
```

Loads reference data into in-memory hash tables, then performs star-schema-style joins inside tMap. The 228 tFixedFlowInput and 269 tHashOutput instances indicate this is the dominant reference data enrichment approach. Advantage: performance (in-memory lookups). Disadvantage: memory pressure (all reference data must fit in the JVM heap). Consider replacement with SQL-based joins during migration.

### Pattern 4: The Bidirectional Salesforce Sync

```
Direction 1: Salesforce --> BigQuery (account mappings, country mappings)
  tSalesforceInput --> tMap --> tBigQueryOutput (Global_Raw.Salesforce_*)

Direction 2: BigQuery --> Salesforce (product master data)
  tBigQueryInput --> tMap --> tSalesforceOutput (Product2 upsert)
```

The bidirectional nature creates a data loop and timing dependencies. Salesforce account data flows into BigQuery for enrichment, while BigQuery product data flows back for the sales team's catalog.

---

## Regional Comparison

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

---

## Most Complex Data Flows

The platform's complexity is concentrated in Brazil. The seven most transformation-heavy jobs are all BRA jobs, reflecting the dual-ERP landscape and data harmonization burden.

### Copy_of_VBR_Clean_Build_Sales_Set (BRA)

The most complex job on the entire platform: 94 components, 26 input sources, 17 outputs, and 20 transform steps. Constructs the unified Sales_Set from Business Central data with in-memory star-schema joins against 13 reference tables.

### BIO_Clean_Invoice_Set_v5 (BRA)

Handles the legacy Biovet pipeline. Reads 4 inputs from the MSSQL-sourced raw layer, applies 20 transforms, hardcodes entity as 'Biovet' and currency as 'BRL'. Remains in active use alongside the newer Business Central pipeline.

### BIO_Sync_Product_Salesforce_v1 (BRA)

Complex for its bidirectional nature -- reads from BigQuery and performs both update and upsert operations against Salesforce's Product2 object. One of only a few jobs with any error handling.

### Why Brazil Dominates

Brazil's disproportionate complexity (71 jobs, 1,448 transforms -- 31% of the platform total) stems from the coexistence of two ERP systems. Of Brazil's 71 jobs, roughly 5 in 8 are copies, backups, or debug versions.

---

## Per-Job Transformation Detail

This section provides field-level transformation documentation for each job with mapping components. For brevity, only representative jobs are shown; a complete document includes every job.

#### VJP_Clean_Build_Sales_Set Transformations

**tMap_1 -- Sales Invoice Enrichment**

Main input: `jpn_vjp_raw.salesInvoiceLines` | Lookups: `hash_FiscalPeriods`, `hash_CountryMaster`, `hash_ConversionRates`, `hash_SalesforceAccountMappings`

**Var Table**

| Variable | Expression | Source |
|----------|-----------|--------|
| parsedDate | `TalendDate.parseDate("MM/dd/yy", row1.postingDate)` | row1 |
| fiscalPeriod | `hash_FiscalPeriods.get(Var.parsedDate)` | lookup |
| convRate | `hash_ConversionRates.get("JPY")` | lookup |
| amountEUR | `row1.amount == null ? null : row1.amount / Var.convRate` | row1 + Var |

**Output: jpn_vjp_clean.Sales_Set**

| Source | Expression | Target | Explanation |
|--------|-----------|--------|-------------|
| row1.documentNo | `"VJP-" + row1.documentNo` | Record_ID | Prefixed with entity code for global uniqueness |
| row1.customerNo | `row1.customerNo` | Customer_Number | Direct pass-through |
| (none) | `"Vaxxinova Japan"` | Entity | Hardcoded entity name |
| (none) | `"VJP"` | Entity_Code | Hardcoded entity code |
| (none) | `"JPY"` | Default_Currency | Hardcoded domestic currency |
| Var.parsedDate | `Var.parsedDate` | Posting_Date | Parsed from raw date string |
| Var.fiscalPeriod | `Var.fiscalPeriod` | Fiscal_Period | From fiscal calendar lookup |
| Var.amountEUR | `Var.amountEUR` | Net_Sales_Amount_EUR | JPY converted to EUR via budget rate |
| row1.amount | `row1.amount` | Net_Sales_Amount | Local currency amount unchanged |
| lookup | `hash_SalesforceAccountMappings.get(row1.customerNo)` | Salesforce_Account_ID | CRM linkage via customer number |

**Join Logic**: LEFT OUTER join from salesInvoiceLines to each lookup hash table. No-match produces null (handled by downstream null checks).

**Filter/Reject**: No filter expression on main output. No reject link.

#### DEU_VDA_BC_Clean_Invoice_Set_v3 Transformations

**tMap_1 -- Invoice Line Transformation**

Main input: `deu_vda_raw.salesInvoiceLines` | Lookups: `hash_FiscalPeriods`, `hash_CountryMaster`

**Output: deu_vda_clean.Sales_Set**

| Source | Expression | Target | Explanation |
|--------|-----------|--------|-------------|
| row1.currencyCode | `row1.currencyCode == null \|\| row1.currencyCode.isEmpty() ? "EUR" : row1.currencyCode` | Currency_Code | Default to EUR for domestic transactions |
| row1.currencyFactor | `row1.currencyCode == null \|\| row1.currencyCode.isEmpty() ? 1 : row1.currencyFactor` | Conversion_Rate | Default rate 1 for EUR |
| row1.lineAmount | `row1.lineAmount / Var.convRate` | Net_Sales_Amount_EUR | Convert to EUR |

---

## Schema Dictionary

The platform references **321 distinct tables** across its 257 jobs, spanning regional raw extraction datasets, cleaned analytical tables, global master data, and Salesforce CRM objects. These tables live primarily in Google BigQuery, organized into dataset namespaces by region and layer (e.g., `bra_vbr_raw`, `jpn_vjp_clean`, `Global_Master`, `Global_Clean`). However, only 50 of 321 tables (16%) have column-level metadata available from static analysis.

### Table Naming Patterns

The 321 tables fall into six categories:

**Sales_Set Tables (33 tables, 10%).** The primary output artifact of the entire platform. Every regional subsidiary produces a Sales_Set table containing standardized invoice and credit memo data. Sales_Set tables appear in regional clean datasets and in the global consolidation layer. The Sales_Set schema is the closest thing the platform has to a canonical data contract -- any column mismatch breaks downstream aggregation.

**Raw Tables (71 tables, 22%).** Data as it arrives from source systems, with minimal transformation. Names mirror Business Central API entity names: `salesInvoices`, `salesCreditMemos`, `salesInvoiceLines`, `items`, `customers`, `generalLedgerEntries`.

**Fiscal Reference Tables (4 tables).** A critical set governing temporal logic. `Global_Master.Fiscal_Periods` is the authoritative calendar for Vaxxinova's non-calendar fiscal year. `Global_Budget.Budget_Current_Conversion_Rate` provides exchange rates for EUR conversion.

**Master Data Tables (7 tables).** Global reference tables: `Global_Master.Country_Master`, `Global_Raw.Salesforce_Account_Mappings`, payment terms, shipment methods, product hierarchies.

**Parameterized Context Tables (18 tables).** Table references that resolve at runtime through Talend context variables (e.g., `context.parameter_Raw_Prefix + ".salesInvoices"`). The 18 identified are likely an undercount.

**Other Tables (188 tables, 59%).** Production order tables, value entries, inventory tables, customer accounts, Salesforce staging tables, and domain-specific intermediate results.

### Most-Referenced Tables

| Table | Columns Known | Jobs Using It | Primary Region | Role |
|-------|--------------|---------------|----------------|------|
| `test_global_clean.ProductionOrders_CBR` | 15 | 13 (BRA) + 9 (USA) | Cross-region | Production order headers for cost analysis |
| `test_global_clean.ProductionOrderLines_CBR` | 16 | 10 (BRA) + varies | Cross-region | Production order line items |
| `test_bra_vbr_raw.valueEntries` | 10 | 9 | BRA | Raw value entries for cost analysis |
| `context.parameter_Raw_Prefix` | 0 | 8+ | DEU, Global | Parameterized: resolves to region-specific raw dataset |
| `Global_Master.Fiscal_Periods` | -- | all clean jobs | Global | Fiscal calendar for every Sales_Set builder |

The `test_` prefix visible in many table names confirms that static analysis was run against a TEST environment configuration, not PROD.

### Regional Schema Distribution

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

The 84% column-definition gap reflects the fundamental challenge of extracting schema information from a visual ETL platform where column mappings are defined graphically in tMap editors.

---

## Shared Reference Tables

A small set of global reference tables acts as the connective tissue across all regional pipelines. These form a dependency bottleneck: if any are stale or incorrect, the impact cascades across all regions.

### Global_Master.Fiscal_Periods -- The Fiscal Calendar

Maps calendar dates to Vaxxinova's non-calendar fiscal year, quarter, and month. Used by every Sales_Set builder. Must be populated with future fiscal periods before the corresponding dates arrive.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Calendar_Date | DATE | The calendar date being mapped |
| Fiscal_Year | INTEGER or STRING | Vaxxinova fiscal year |
| Fiscal_Quarter | STRING | Fiscal quarter designation (Q1-Q4) |
| Fiscal_Month | INTEGER or STRING | Fiscal month number |
| Fiscal_Period_Name | STRING | Human-readable period name |

### Global_Master.Country_Master -- Geographic Reference

Standardizes geographic classifications. Source ERPs use inconsistent country representations; this table provides the canonical mapping. Some regions apply trimming and case normalization before the join.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Country_Code | STRING | Standardized country code (ISO format) |
| Country_Name | STRING | Full country name |
| Region_Group | STRING | Regional grouping (EMEA, Americas, APAC) |
| Salesforce_Country_Code | STRING | Corresponding Salesforce country code |

### Global_Budget.Budget_Current_Conversion_Rate -- Exchange Rates

Vaxxinova uses **budget exchange rates** rather than market exchange rates for consolidated reporting -- a deliberate business decision to isolate operational variances from currency fluctuations.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| Currency_Code | STRING | Source currency (BRL, JPY, ZAR, USD) |
| Conversion_Rate | FLOAT | Exchange rate to EUR (local currency per 1 EUR) |
| Effective_Date or Period | DATE/STRING | Budget period for which this rate applies |

**Business risk:** If conversion rates are not updated for new budget periods, sales data will be converted at stale rates with no validation to detect this.

### Global_Raw.Salesforce_Account_Mappings -- CRM Linkage

The bridge between the ERP world (Business Central customer IDs) and the CRM world (Salesforce Account record IDs). This is a bidirectional dependency: Salesforce data flows into BigQuery for enrichment, while consolidated data flows back to Salesforce.

**Expected columns (inferred from tMap usage):**

| Column | Type (Inferred) | Description |
|--------|-----------------|-------------|
| BC_Customer_ID | STRING | Business Central customer identifier |
| Salesforce_Account_ID | STRING | Salesforce Account record ID (18-character) |
| Account_Name | STRING | Salesforce Account name |
| Entity_Code | STRING | Vaxxinova subsidiary code (VBR, VJP, VUS, etc.) |

### Global_Raw.Salesforce_Country_Mappings

Maps source-system country values to standardized Salesforce country codes. Used as a fallback enrichment layer when raw ERP data has non-standard country representations. Brazil's `BIO_Clean_Invoice_Set_v5` implements a specific fallback pattern: if a Salesforce mapping exists, it takes precedence; otherwise the raw value is trimmed and used directly.

---

## The Parameterized Table Challenge

Eighteen tables carry names like `context.parameter_Raw_Prefix` rather than literal table names. Talend jobs construct table references at runtime by concatenating context variable values with table name suffixes:

```
context.parameter_Raw_Prefix + ".salesInvoices"
```

In PROD, `parameter_Raw_Prefix` resolves to `bra_vbr_raw`; in TEST, to `test_bra_vbr_raw`. The 18 explicitly parameterized references are likely the tip of the iceberg: 38% of the platform's 957 context variables have PROD/TEST divergences.

Three concrete examples:

1. **`context.parameter_Raw_Prefix`** appears in 8 German (DEU) jobs. In PROD it resolves to the VDA or VDD raw dataset.
2. **`context.parameter_Env_Prefix`** controls the clean-layer dataset reference. Brazil's PROD value is `bra_vbr_raw`; TEST is `test_bra_vbr_raw`.
3. **`context.parameter_Clean_Prefix`** resolves to `Global_Clean` in PROD and `test_global_clean` in TEST.

---

## The Column Definition Gap

Only 50 of 321 tables (16%) have any column metadata from static analysis. The 84% gap exists because Talend stores transformation logic inside tMap components as expressions referencing column names, and mechanical extraction captures table-level references more reliably than column-level ones.

The 50 tables with column data are concentrated in Brazil (39 tables) and USA (19 tables). The only authoritative source of truth for full column schemas is BigQuery itself.

Before migration, the team should run a BigQuery `INFORMATION_SCHEMA.COLUMNS` query against every referenced dataset:

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

---

## Data Quality Rules

The platform enforces **1,102 data quality rules** across its ETL pipelines, but none exist as dedicated validation gates. Every rule is embedded inside tMap expressions -- inline Java snippets that silently coerce, default, or discard problematic values during transformation. There are no rejection flows, no quarantine tables, and no explicit pass/fail reporting.

### Rule Type Distribution

| Type | Count | % | Description |
|------|-------|---|-------------|
| null_check | 742 | 67% | Handles missing or empty values by substituting defaults or returning null |
| format_check | 211 | 19% | Validates or transforms string formats -- date parsing, whitespace trimming, pattern matching |
| type_check | 149 | 14% | Type coercion: parsing strings to integers, BigDecimal conversions, numeric edge cases |

There are no range checks, referential integrity validations, or cross-field consistency rules implemented at the tMap level.

### Severity Profile

- **Medium severity** -- 891 rules (81%). Null checks and type checks that could produce incorrect downstream results.
- **Low severity** -- 211 rules (19%). Format checks for cosmetic normalization.
- No rules are classified as high severity because there is no mechanism for a rule to halt a job or trigger an alert.

### Concrete Examples

**Null check with conditional defaulting** (BRA, `VBR_Update_Production_Order`):
```java
POL_5.Preceding_Open_Orders == 0 ? POL_5.Overhead_Cost : null
```
Returns overhead cost only when there are no preceding open orders -- business logic buried in a ternary expression.

**Null check with date parsing** (JPN, `VJP_Clean_Ingest_Sales_Set`):
```java
CoGS_1.DocumentDate == null || CoGS_1.DocumentDate.trim().isEmpty()
    ? null
    : TalendDate.parseDate("MM/dd/yy", CoGS_1.DocumentDate)
```
Checks for both null and empty-after-trim conditions before parsing. The US-style date format `MM/dd/yy` in Japan region code suggests Business Central outputs dates in this format.

**Type coercion with sign reversal** (BRA, `BIO_Clean_Invoice_Set_v5`):
```java
returnsInput.Quantity_Returns.multiply(BigDecimal.valueOf(-1))
```
Negates return quantities using BigDecimal arithmetic -- a business rule expressed as a type-level operation.

**Sentinel date handling** (USA, `VUS_Clean_BC_SalesInvoices_v15`):
```java
vusformattedOutput.salesInvoiceLines_shipmentDate.equals("0000-12-30")
    ? null
    : vusformattedOutput.salesInvoiceLines_shipmentDate
```
Business Central uses `0000-12-30` as a sentinel for "no shipment date." This hardcoded sentinel value would silently stop working if BC changes its convention.

### Null Check Patterns (742 rules)

Null checks are a family of related idioms:

- **Simple null guard** (~40%): `field == null ? null : field.someMethod()` -- purely defensive, no business logic.
- **Null-or-empty guard** (~30%): `field == null || field.trim().isEmpty() ? null : transform(field)` -- dominant in date parsing and string-to-number conversions.
- **Null with default substitution** (~20%): `field == null ? "UNKNOWN" : field` -- most migration-sensitive because the default value may encode a business decision. No platform-wide standard for defaults.
- **Conditional business logic gated by null** (~10%): Null check intertwined with a business decision. Hardest to migrate.

### Type Check Patterns (149 rules)

- **String to integer**: `Integer.valueOf(field)` wrapped in null guards.
- **String to float/double**: Often includes locale-aware format cleaning via `replaceAll("[^\\d.-]", "")`. Regex patterns vary between jobs.
- **BigDecimal arithmetic**: For financial precision -- sign reversal, zero comparisons, `setScale()` calls.
- **String to date**: `TalendDate.parseDate()` with format strings varying by source: `"MM/dd/yy"` (BC), `"yyyy-MM-dd"` (BigQuery), `"dd/MM/yyyy"` (European).

### Format Check Patterns (211 rules)

- **Whitespace trimming**: `field.trim()` -- defensive against trailing spaces from source systems.
- **Date format normalization**: Parse then reformat to `yyyy-MM-dd` for BigQuery storage.
- **Case standardization**: `toUpperCase()` or `toLowerCase()` for codes and identifiers, particularly in BRA and DEU.

### Region Distribution

| Region | Total Rules | null_check | format_check | type_check | Dominant Severity |
|--------|------------|------------|--------------|------------|-------------------|
| BRA | 359 | 243 (68%) | 74 (21%) | 42 (12%) | Medium |
| JPN | 261 | 147 (56%) | 66 (25%) | 48 (18%) | Medium |
| USA | 238 | 164 (69%) | 33 (14%) | 41 (17%) | Medium |
| Global | 143 | 123 (86%) | 12 (8%) | 8 (6%) | Medium |
| DEU | 50 | 23 (46%) | 21 (42%) | 6 (12%) | Mixed |
| ITA | 33 | 26 (79%) | 5 (15%) | 2 (6%) | Medium |
| ZAF | 10 | 10 (100%) | 0 | 0 | Medium |
| GENERIC | 8 | 6 (75%) | 0 | 2 (25%) | Medium |

BRA, JPN, and USA together account for 78% of all rules (858 of 1,102). The Global region is heavily skewed toward null checks (86%), reflecting simpler consolidation logic. DEU is notable for having the highest proportion of format checks (42%), likely due to German locale-specific formatting.

### Quality Debt by Region

**BRA (359 rules):** Most comprehensive coverage but concentrated on null handling. Missing: cross-field validation, referential integrity checks between entities.

**JPN (261 rules):** Highest type_check proportion (18%) due to multi-source GL entry processing with different type systems. Missing: join condition validation for orphan records.

**USA (238 rules):** Heavily weighted toward null checks (69%) for Salesforce synchronization. Missing: idempotency validation for Salesforce pushes.

**Global (143 rules):** Almost entirely null-check (86%). Missing: validation that regional pipelines have delivered data before running consolidation.

**DEU (50 rules):** Most balanced distribution. Missing: rules relative to pipeline complexity suggest gaps in validation coverage.

---

## What Is Missing from Data Quality

Several capabilities standard in modern data platforms are entirely absent:

**No Rejection Flows.** Records that fail validation are silently defaulted or nulled -- never routed to a rejection table for review. There is no audit trail showing that the source system delivered bad data.

**No Quality Gates.** No job includes a "stop if more than N% of records fail validation" pattern. A source system delivering 100% null values for a critical field will not halt the pipeline.

**No Anomaly Detection.** No mechanism to detect sudden changes in data quality patterns. A field going from 2% to 50% null values would be processed identically.

**No Data Quality Metrics.** The `tJava_StoreJobStats` boilerplate captures row counts but does not report on data quality outcomes -- no counting of corrections, nulls encountered, or type coercions performed.

**No Schema Validation.** No pre-flight checks for column presence, type compatibility, or schema version changes. A source system schema change would cause an unhandled exception.

**No Referential Integrity Checks.** Joins producing null lookups are handled with null defaults, but orphan records are never identified or counted.

---

## Schema Documentation Recommendations

### Recommendation 1: Run a Full BigQuery INFORMATION_SCHEMA Audit

Execute column metadata queries against every production dataset to close the 84% column-definition gap.

### Recommendation 2: Formalize the Sales_Set Data Contract

Document every column, its data type, nullability, business meaning, derivation formula, validation rules, version history, and ownership. The Sales_Set schema currently exists only implicitly in tMap expressions scattered across dozens of jobs.

### Recommendation 3: Build a Context Variable Resolution Map

For every context variable participating in table name construction, document the variable name, PROD value, TEST value, which jobs use it, and which physical tables it resolves to. This is a prerequisite for accurate dependency graphing.

### Recommendation 4: Establish a Naming Standard for Migration

Remove `test_` prefixes for production references. Ensure every table reference is fully qualified. Adopt consistent casing (snake_case). Correct known inconsistencies (e.g., the `Produtct_Description` typo).

---

## Migration Takeaways

**tMap translation is the critical path.** The 565 tMap instances are the repository of all business logic. Each contains join definitions, expression-based transformations, null handling, and conditional routing. No automated migration tool exists; they must be manually reverse-engineered.

**BigQuery I/O should be straightforward.** Any modern platform with native BigQuery connectors can replace the 619 tBigQueryInput and 360 tBigQueryOutput instances with minimal risk.

**MSSQL connectivity requires infrastructure planning.** Five named connections depend on network paths between the Talend runtime and on-premises databases.

**Document column schemas from BigQuery before migration.** The 84% column-definition gap cannot be closed through Talend XML analysis alone. BigQuery's `INFORMATION_SCHEMA` is the authoritative source.

**Resolve all parameterized table names.** Build a lookup table mapping every `context.parameter_*_Prefix` variable to its PROD and TEST values.

**Migrate shared reference tables first.** `Global_Master.Fiscal_Periods`, `Global_Master.Country_Master`, `Global_Budget.Budget_Current_Conversion_Rate`, and the Salesforce mapping tables are upstream dependencies for every regional pipeline.

**Establish the Sales_Set as a formal data contract.** Column schema, data types, nullability rules, and business rules should be version-controlled.

**Extract and catalog all tMap validation expressions.** The 1,102 embedded rules must be documented with intent, input conditions, and default behavior before migration.

**Implement explicit data quality checks in the target platform.** Replace inline tMap validation with dedicated quality stages that separate validation from business logic.

**Add rejection and quarantine flows.** Route validation failures to a quarantine area for visibility and root-cause analysis.

**Build data quality dashboards.** Introduce metrics for pass rates, null rates, type coercion counts, and sentinel value frequencies.

**Standardize null-handling conventions.** The current platform uses inconsistent approaches; define a consistent policy per data type.

**Implement quality gates for critical pipelines.** Halt execution when validation failure rates exceed configurable thresholds.

**Prioritize Brazil for proof-of-concept.** Brazil exercises every source type, every layer, and the most complex transformation logic. A successful BRA migration validates readiness for the entire portfolio.

**Consolidate backup/copy/debug variants.** Roughly 30% of all jobs are development artifacts. Migration is an opportunity to collapse these into single canonical versions with proper version control.

---

*Generated by intodoc -- documentation automation for integration platforms.*
