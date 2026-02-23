# Data Quality

Vaxxinova's Talend platform enforces 1,102 data quality rules across its ETL pipelines, but none of them exist as dedicated data quality components or explicit validation gates. Instead, every rule is embedded inside tMap expressions -- inline Java snippets that silently coerce, default, or discard problematic values during transformation. There are no rejection flows, no quarantine tables, and no explicit pass/fail reporting. Records that fail validation are either nulled out or coerced to a safe default, with no visibility into what was corrected or why. This implicit approach works in the current platform but creates significant migration risk: the rules must be extracted, documented, and rebuilt as explicit checks in the target system.

## Rule Type Distribution

Of the 1,102 rules detected, three types account for the entire landscape:

- **null_check** -- 742 rules (67%). The dominant pattern. These handle missing or empty values by substituting defaults, returning null explicitly, or gating downstream logic behind null-safe conditionals.
- **format_check** -- 211 rules (19%). These validate or transform string formats -- date parsing, whitespace trimming, pattern matching. All are classified as low severity.
- **type_check** -- 149 rules (14%). Type coercion logic: parsing strings to integers, converting between BigDecimal and float, handling numeric edge cases.

There are no range checks, referential integrity validations, or cross-field consistency rules implemented at the tMap level. The platform relies entirely on source system data quality for these concerns.

## Severity Profile

The severity distribution reflects the implicit nature of these checks:

- **Medium severity** -- 891 rules (81%). Null checks and type checks that could produce incorrect downstream results if they fail silently.
- **Low severity** -- 211 rules (19%). Format checks that handle cosmetic or structural normalization.

No rules are classified as high severity. This is a consequence of the validation approach: since all rules are embedded in tMap expressions and fail silently (defaulting to null or a safe value), there is no mechanism for a rule to halt a job or trigger an alert. The `tJava_StoreJobStats` boilerplate captures row counts but does not report on how many records required data quality correction.

## Concrete Examples

The following examples illustrate the three rule types as they appear in production tMap expressions.

**Null check with conditional defaulting** (BRA, `VBR_Update_Production_Order`):
```java
POL_5.Preceding_Open_Orders == 0 ? POL_5.Overhead_Cost : null
```
This rule returns the overhead cost only when there are no preceding open orders, otherwise nulling it out. The business intent -- that overhead should only be allocated to final production orders -- is buried in a ternary expression with no documentation.

**Null check with date parsing** (JPN, `VJP_Clean_Ingest_Sales_Set`):
```java
CoGS_1.DocumentDate == null || CoGS_1.DocumentDate.trim().isEmpty()
    ? null
    : TalendDate.parseDate("MM/dd/yy", CoGS_1.DocumentDate)
```
Before parsing a date, this rule checks for both null and empty-after-trim conditions. If either is true, it passes null through rather than throwing a parse exception. The date format `MM/dd/yy` is US-style despite this being Japan region code, suggesting the source system (Business Central) outputs dates in this format.

**Type coercion with sign reversal** (BRA, `BIO_Clean_Invoice_Set_v5`):
```java
returnsInput.Quantity_Returns.multiply(BigDecimal.valueOf(-1))
```
This rule negates return quantities using BigDecimal arithmetic. The sign reversal encodes a business rule -- returns reduce inventory and revenue -- but it is expressed as a type-level operation rather than a named business transformation.

**Sentinel date handling** (USA, `VUS_Clean_BC_SalesInvoices_v15`):
```java
vusformattedOutput.salesInvoiceLines_shipmentDate.equals("0000-12-30")
    ? null
    : vusformattedOutput.salesInvoiceLines_shipmentDate
```
Business Central uses `0000-12-30` as a sentinel for "no shipment date." This rule converts the sentinel to null, preventing nonsensical dates from appearing in reporting. The sentinel value is hardcoded -- if BC changes its convention, this rule will silently stop working.

**Type coercion with format cleaning** (Global, `Legacy_Build_Sales_Set`):
```java
NPL_GP_1.Currency_Rate != null && !NPL_GP_1.Currency_Rate.isEmpty()
    ? Float.parseFloat(NPL_GP_1.Currency_Rate.replaceAll(...))
    : ...
```
This rule parses a currency rate string to a float, first checking for null/empty and then stripping non-numeric characters. The `replaceAll` call handles locale-specific number formatting (commas vs. periods), a concern that varies by region.

## Validation Patterns in Detail

The three rule types -- null_check, format_check, and type_check -- each follow characteristic code patterns that recur across regions. Understanding these patterns at a granular level is critical for building automated extraction tooling during migration.

### Null Check Patterns (742 rules)

Null checks are not a single pattern but a family of related idioms, each with different implications for migration:

**Simple null guard.** The most basic pattern is a ternary that returns null when the input is null:
```java
field == null ? null : field.someMethod()
```
This pattern exists to prevent NullPointerExceptions when calling methods on nullable fields. It is purely defensive and encodes no business logic. Approximately 40% of the 742 null_check rules follow this pattern.

**Null-or-empty guard.** A more thorough variant checks for both null and empty strings:
```java
field == null || field.trim().isEmpty() ? null : transform(field)
```
This pattern is dominant in date parsing rules (as seen in the JPN example above) and string-to-number conversions. It accounts for roughly 30% of null_check rules. The `trim()` call is significant: it means the source systems sometimes deliver whitespace-only strings that must be treated as absent values.

**Null with default substitution.** Some null checks substitute a specific default value rather than passing null through:
```java
field == null ? "UNKNOWN" : field
field == null ? 0 : Integer.parseInt(field)
```
These are the most migration-sensitive null_check rules because the default value may encode a business decision. The choice of `"UNKNOWN"` versus `null` versus `""` versus `"N/A"` varies by job and by developer, with no platform-wide standard. Approximately 20% of null_check rules use default substitution.

**Conditional business logic gated by null.** The remaining 10% of null_check rules use null as a gating condition for business logic, as in the `VBR_Update_Production_Order` example where overhead cost is returned only when preceding open orders equal zero. These rules are the hardest to migrate because the null check is intertwined with a business decision.

### Type Check Patterns (149 rules)

Type checks handle the conversion between data types, most commonly from strings (as delivered by source APIs) to typed values (as required by BigQuery schemas):

**String to integer.** The straightforward `Integer.valueOf(field)` or `Integer.parseInt(field)` pattern, always wrapped in a null guard:
```java
field != null ? Integer.valueOf(field) : null
```

**String to float/double.** Used for currency rates, quantities, and financial amounts. These often include locale-aware format cleaning:
```java
Float.parseFloat(field.replaceAll("[^\\d.-]", ""))
```
The `replaceAll` pattern strips non-numeric characters (thousands separators, currency symbols) before parsing. The regex pattern varies between jobs -- some strip commas, some strip periods, some strip both -- reflecting inconsistent locale handling across regions.

**BigDecimal arithmetic.** Financial calculations use BigDecimal for precision. The sign-reversal pattern (`multiply(BigDecimal.valueOf(-1))`) appears in BRA's invoice processing. Other patterns include `BigDecimal.ZERO` comparisons and `setScale()` calls for decimal precision management.

**String to date.** Date parsing uses `TalendDate.parseDate()` with format strings that vary by source system: `"MM/dd/yy"` for Business Central, `"yyyy-MM-dd"` for BigQuery native dates, and occasionally `"dd/MM/yyyy"` for European sources. These format strings are hardcoded in each tMap expression rather than centralized as configuration.

### Format Check Patterns (211 rules)

Format checks normalize string values to a standard representation:

**Whitespace trimming.** The most common format check is `field.trim()`, applied to string fields before further processing. This is defensive -- Business Central and MSSQL sources occasionally deliver strings with trailing spaces.

**Date format normalization.** Source dates arrive in various formats and are normalized to `yyyy-MM-dd` for BigQuery storage. The normalization logic uses `TalendDate.parseDate()` followed by `TalendDate.formatDate()`, with the input format varying by source system.

**Case standardization.** Some jobs apply `toUpperCase()` or `toLowerCase()` to standardize codes and identifiers. This is particularly common in the BRA and DEU regions where Business Central delivers mixed-case entity codes.

## Region Distribution

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

BRA, JPN, and USA together account for 78% of all data quality rules (858 of 1,102), which tracks with their larger job counts and more complex transformation pipelines. The Global region is heavily skewed toward null checks (86%), reflecting the simpler structure of budget and forecast consolidation jobs that primarily need to handle missing values. DEU is notable for having the highest proportion of format checks (42%), likely due to its Business Central integration requiring more date and string format normalization.

ZAF is the simplest region: all 10 rules are null checks with no format or type validation, consistent with its relatively straightforward Deltamune extraction pipeline.

## Quality Debt by Region

Each region carries a different profile of data quality debt -- the gap between what the current validation rules cover and what a production-grade data quality framework should enforce. The following analysis assesses each region's quality posture.

### BRA (359 rules -- highest absolute count)

Brazil's 359 rules provide the most comprehensive validation coverage of any region, but the coverage is concentrated on null handling (68%) with limited type checking (12%). The BRA pipeline processes financial data from both the legacy BioVet MSSQL system and the newer Business Central API, meaning it handles two different data formats and quality profiles. The 74 format_check rules (21%) reflect the format normalization needed to reconcile these two sources. Quality debt: BRA has no cross-field validation (e.g., verifying that invoice line amounts sum to the invoice total) and no referential integrity checks between entities (e.g., confirming that all invoice line items reference valid customer IDs).

### JPN (261 rules -- highest type_check proportion)

Japan has the highest proportion of type_check rules (18%) and the second-highest proportion of format_check rules (25%). This reflects the complexity of JPN's multi-source GL entry processing, which joins four data streams with different type systems. The 48 type_check rules handle conversions between string, integer, and decimal types across these streams. Quality debt: JPN lacks validation for the join conditions themselves -- if a GL entry references a customer ID that does not exist in the customer master, the join produces a null lookup that is silently handled by a null_check rule, with no alerting or counting of orphan records.

### USA (238 rules -- Salesforce complexity)

USA's 238 rules are heavily weighted toward null checks (69%), reflecting the defensive coding needed for the Salesforce synchronization pipeline. Salesforce fields are frequently null or empty, and the sync jobs must handle every possible combination of present and absent fields. The relatively low format_check count (14%) reflects the fact that Business Central's API delivers well-formatted data to the USA pipeline. Quality debt: USA's Salesforce sync jobs lack idempotency validation -- there is no check to prevent duplicate records from being pushed to Salesforce if a job is retried after a partial failure.

### Global (143 rules -- null-check dominated)

Global's validation is almost entirely null-check (86%), with minimal format or type validation. This is appropriate for its role as the consolidation layer -- it receives pre-cleaned data from regional pipelines and primarily needs to handle missing values during cross-entity aggregation. Quality debt: Global has no validation that the regional pipelines have actually delivered data before running the consolidation. If a regional pipeline fails silently, Global's consolidation will run with stale data and produce incorrect aggregate reports -- with no alerting mechanism.

### DEU (50 rules -- format-heavy)

Germany's 50 rules have the most balanced distribution: 46% null_check, 42% format_check, 12% type_check. The high format_check proportion (21 rules) is driven by date and number format normalization for the German Business Central instance, which delivers locale-specific formatting (commas as decimal separators, `dd.MM.yyyy` date formats). Quality debt: DEU's small rule count relative to its pipeline complexity suggests that some validation is simply missing -- fields that should be validated are passed through without checks.

### Smaller Regions (ITA, ZAF, GENERIC)

ITA (33 rules), ZAF (10 rules), and GENERIC (8 rules) have minimal validation coverage. ITA's rules are predominantly null checks (79%) with a small complement of format checks. ZAF's 10 rules are entirely null checks, reflecting a pipeline that was cloned from ITA and has not been augmented with region-specific validation. GENERIC's 8 rules are the bare minimum needed for the parameterized Business Central extraction templates. Quality debt for all three regions is high in absolute terms -- they have the fewest rules per job of any production regions.

## What Is Missing

Several data quality capabilities that are standard in modern data platforms are entirely absent from the Vaxxinova Talend codebase. These gaps represent not just technical debt but operational risk -- problems that are currently invisible will remain invisible until they cause downstream impact.

### No Rejection Flows

Records that fail validation are silently defaulted or nulled -- they are never routed to a rejection table for review. This means there is no mechanism to answer the question "how many records had data quality issues today?" When a field is coerced from null to a default value, the original null is lost -- there is no audit trail showing that the source system delivered bad data. In a modern data platform, a rejection flow would capture failed records in a quarantine table with the reason for failure, enabling root-cause analysis and source system remediation.

### No Quality Gates

No job includes a "stop if more than N% of records fail validation" pattern. If a source system begins delivering 100% null values for a critical field, the pipeline will happily coerce all of them to defaults and load the result into BigQuery. The downstream reports will show incorrect data, but the pipeline itself will report success. Quality gates -- thresholds that halt execution when validation failure rates exceed acceptable levels -- are a standard safeguard against this scenario.

### No Anomaly Detection

There is no mechanism to detect sudden changes in data quality patterns. If a source system that normally delivers 2% null values for a field suddenly starts delivering 50% nulls, the pipeline treats both scenarios identically. Anomaly detection would flag this deviation for investigation before the data reaches the reporting layer.

### No Data Quality Metrics

The `tJava_StoreJobStats` boilerplate captures row counts and execution timing but does not report on data quality outcomes. There is no counting of how many records were corrected, how many nulls were encountered, how many type coercions were performed, or what percentage of records passed validation without correction. Without these metrics, it is impossible to monitor data quality trends over time or to set SLAs for data quality.

### No Schema Validation

Source data is assumed to match expected schemas. There are no pre-flight checks for column presence, column type compatibility, or schema version changes. If Business Central adds a new required field or changes a field's data type, the pipeline will fail with an unhandled exception rather than a graceful, documented error.

### No Referential Integrity Checks

Joins that produce null lookups are handled with null defaults, but there is no mechanism to flag or count orphan records. When a sales invoice line references a customer ID that does not exist in the customer master, the join returns null for all customer fields, and the null_check rules silently substitute default values. The orphan record is never identified or counted.

## Absent Patterns

Several data quality capabilities that are standard in modern data platforms are entirely absent from the Vaxxinova Talend codebase:

- **No rejection flows.** Records that fail validation are silently defaulted or nulled -- they are never routed to a rejection table for review.
- **No data quality metrics.** There is no counting or reporting of how many records were corrected, defaulted, or would have failed validation.
- **No explicit data quality gates.** No job includes a "stop if more than N% of records fail validation" pattern.
- **No referential integrity checks.** Joins that produce null lookups are handled with null defaults, but there is no mechanism to flag or count orphan records.
- **No schema validation.** Source data is assumed to match expected schemas; there are no pre-flight checks for column presence or type compatibility.

## Migration Takeaways

**Extract and catalog all tMap validation expressions.** The 1,102 rules embedded in tMap expressions must be extracted into a validation rule catalog before migration. Each rule needs documentation of its intent, input conditions, and default behavior. This is especially important for rules that encode business decisions (such as the overhead cost allocation in `VBR_Update_Production_Order`).

**Implement explicit data quality checks in the target platform.** The target architecture should replace inline tMap validation with dedicated data quality components or stages that run before transformation. This separates validation concerns from business logic and makes rules visible, testable, and auditable.

**Add rejection and quarantine flows.** Records that fail validation should be routed to a quarantine area rather than silently defaulted. This provides visibility into data quality issues and enables root-cause analysis at the source system level.

**Build data quality dashboards.** Introduce metrics collection for validation outcomes: pass rates, null rates, type coercion counts, and sentinel value frequencies. These metrics do not exist today and are essential for monitoring data quality trends after migration.

**Standardize null-handling conventions.** The current platform uses inconsistent null-handling approaches: some rules default to null, others to sentinel values (`0001-01-01`), others to zero. The target platform should define a consistent null-handling policy per data type and apply it uniformly.

**Implement quality gates for critical pipelines.** The target platform should support configurable thresholds that halt pipeline execution when validation failure rates exceed acceptable levels. For example, if more than 5% of invoice records have null customer IDs, the pipeline should stop and alert rather than loading incomplete data.

**Add schema validation as a pre-flight check.** Before processing source data, the target platform should validate that the incoming schema matches expectations -- checking for column presence, type compatibility, and expected cardinality. This prevents silent failures when source systems change their data contracts.
