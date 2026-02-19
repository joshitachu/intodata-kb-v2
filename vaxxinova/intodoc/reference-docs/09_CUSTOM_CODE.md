# Custom Code

Vaxxinova's Talend platform contains 509 custom Java code snippets spread across 10 regions. Despite this volume, the custom code landscape is surprisingly uniform: the vast majority of snippets are infrastructure boilerplate rather than business logic. The dominant pattern is a copy-pasted `tJava_StoreJobStats` block that appears in nearly every job, supplemented by a set of shared routines for data quality handling. Genuine business rules hidden in custom code are rare -- but where they exist, they represent high-risk migration targets because they are easy to overlook.

## Language and Component Profile

All 509 snippets are written in Java, as expected for a Talend Open Studio codebase. The code appears in three component types:

- **tJava** -- standalone Java blocks, typically triggered on subjob completion (182 instances across all regions). These are overwhelmingly `tJava_StoreJobStats` boilerplate.
- **tJavaRow** -- inline Java executed per row during data flow (102 instances). These handle row-level transformations that exceed tMap expression capabilities.
- **tJavaFlex** -- flexible Java blocks with begin/main/end sections (13 instances, all in the GENERIC region). These are used for more complex flow control patterns.

## The Dominant Pattern: tJava_StoreJobStats

The single most duplicated code pattern across the platform is `tJava_StoreJobStats`. This block is copy-pasted into virtually every production job. Its purpose is to capture job execution metrics -- row counts, timestamps, and status -- and write them to a centralized logging table after each BigQuery interaction completes.

A typical instance looks like this:

```java
/*
 * tJava_StoreJobStats
 * Trigger: On Subjob Ok of the component that just spoke to BigQuery
 */
// ... metrics capture and logging logic
```

This exact block appears across BRA (83 tJava instances), JPN (44 instances), and USA (52 instances) -- the three largest regions by job count. Each instance is a verbatim copy rather than a call to a shared routine, meaning:

1. Any change to the logging logic must be applied to every job individually.
2. Drift between copies is undetectable without manual review.
3. A bug in the metrics capture logic affects all jobs, but a fix must be deployed to each one separately.

For example, `Copy_of_VBR_Clean_Build_Sales_Set/tJava_1`, `VBR_Raw_Customers/tJava_1`, `VBR_Raw_Items/tJava_1`, and `VBR_Raw_salesInvoices_Old/tJava_7` all contain the identical `StoreJobStats` boilerplate in the BRA region alone.

## Shared Routines

Alongside the copy-pasted tJava blocks, the platform does employ a set of genuinely shared routines. These are defined once and referenced across regions:

- **DataQuality** -- appears in all 10 regions (1 to 8 references per region), providing standardized data validation utilities.
- **DataQualityDependencies** -- companion routine to DataQuality, with identical distribution.
- **DqStringHandling** -- string manipulation utilities for data quality processing, also shared broadly.
- **DQTechnical** -- technical data quality checks, present in Global, ITA, SAF, ZAF, and scratchpad regions.
- **SQLike** -- SQL-like utility functions, found in Global, SAF, and ZAF.

These shared routines represent the platform's best practice for code reuse. The contrast with the `tJava_StoreJobStats` pattern is instructive: the data quality routines are properly shared, while the job metrics code is not.

## Shared Routines in Detail

Each of the five shared routines serves a distinct purpose within the platform's data processing architecture. Understanding their responsibilities is essential for migration planning, because these routines must be faithfully replicated in the target platform -- and doing so correctly requires knowing what they do, not just where they appear.

### DataQuality

The DataQuality routine is the platform's primary validation library. It provides a set of reusable functions for checking field values against expected conditions: null detection, empty-string handling, and basic value-range assertions. Every region references this routine -- BRA leads with 8 references, followed by Global (8), USA (7), and JPN (6). The smaller regions (DEU, ITA, SAF, ZAF, GENERIC, scratchpad) each reference it once.

In practice, tMap expressions call DataQuality methods rather than writing inline null-check logic for standard patterns. This is one reason the platform's tMap expressions are not even more verbose than they already are: without the DataQuality routine, each null-safe field access would require its own inline ternary expression. The routine acts as a thin abstraction layer over Java's verbose null-handling syntax.

### DataQualityDependencies

DataQualityDependencies is the companion module to DataQuality. It provides supporting data structures, constants, and helper methods that DataQuality depends on but that are not directly invoked by tMap expressions. Its distribution mirrors DataQuality exactly -- every region that references DataQuality also references DataQualityDependencies, with identical counts (8 in BRA, 8 in Global, 7 in USA, 6 in JPN, and 1 each in DEU, ITA, SAF, ZAF, GENERIC, scratchpad).

The separation into two modules reflects a common Talend design pattern: keeping the API-facing methods in one routine and the internal implementation details in another. For migration, both modules should be treated as a single logical unit -- migrating DataQuality without DataQualityDependencies would break the validation layer.

### DqStringHandling

DqStringHandling provides string manipulation utilities tailored to data quality processing. Its functions handle common string normalization tasks: trimming whitespace, standardizing case, removing non-printable characters, and handling locale-specific encoding issues. The routine is referenced across all 10 regions with the same distribution as DataQuality and DataQualityDependencies (8 in BRA, 8 in Global, 7 in USA, 6 in JPN, 1 each elsewhere).

The string handling utilities are particularly important for the format_check data quality rules documented in the Data Quality analysis. Many of the 211 format_check rules rely on DqStringHandling methods to normalize strings before applying format validation. Without this routine, those rules would need to be rewritten with inline string processing logic.

### DQTechnical

DQTechnical provides technical-level data quality checks that go beyond the basic null and format validation in the DataQuality routine. These include checks for data type compatibility, encoding validation, and structural integrity of complex fields. Unlike DataQuality, DQTechnical is not universally distributed -- it appears only in Global (8 references), ITA (1), SAF (1), ZAF (1), and scratchpad (1). The three largest regions by job count -- BRA, JPN, and USA -- do not reference DQTechnical.

This selective distribution suggests that DQTechnical was developed after the BRA, JPN, and USA pipelines were already established, and was adopted only by regions that were built or refactored later. Alternatively, the larger regions may handle their technical data quality concerns through inline tMap expressions rather than the shared routine. Either way, the inconsistency represents a consolidation opportunity: during migration, all regions should converge on a single technical validation library.

### SQLike

SQLike is the most narrowly distributed shared routine, appearing only in Global (8 references), SAF (1), and ZAF (1). It provides SQL-like utility functions that can be called from Java code -- for example, COALESCE-style null fallback chains, CASE-WHEN equivalents, and IN-list matching. These functions allow developers to express SQL-familiar logic in Talend's Java environment without writing verbose Java conditional statements.

The limited distribution of SQLike -- only 3 of 10 regions use it -- suggests it was introduced by a specific developer or team (likely the Global/Africa team given the SAF and ZAF presence) and never propagated to the wider platform. The BRA, JPN, and USA regions accomplish the same logic through inline Java conditionals in tMap expressions, which is less readable but functionally equivalent.

## Hidden Business Rules in Custom Code

Across all 509 snippets, zero were flagged as containing explicit business rules. This is a positive finding for migration planning -- it means the platform's business logic is concentrated in tMap expressions and SQL queries rather than buried in opaque Java code.

However, "zero flagged" does not mean "zero risk." The tJavaRow components (102 instances) perform row-level transformations that may encode implicit business logic -- type conversions, conditional field mappings, or data enrichment steps that are functionally equivalent to business rules even if they are not labeled as such. These components should be reviewed individually during migration, particularly in the BRA (32 tJavaRow instances), JPN (32 instances), and USA (32 instances) regions.

## Code Patterns by Region

The distribution of custom code across regions reveals distinct usage profiles that reflect each region's pipeline maturity, complexity, and development history.

### BRA (163 snippets -- 32% of total)

Brazil is the heaviest consumer of custom code, with 163 snippets comprising 83 tJava blocks, 32 tJavaRow components, and 24 shared routine references (8 each for DataQuality, DataQualityDependencies, and DqStringHandling). The high tJava count is driven by the `tJava_StoreJobStats` pattern -- virtually every production job in BRA contains this boilerplate. The 32 tJavaRow components are evenly spread across the Clean layer jobs, handling row-level transformations such as BigDecimal arithmetic for financial calculations (e.g., `returnsInput.Quantity_Returns.multiply(BigDecimal.valueOf(-1))` in `BIO_Clean_Invoice_Set_v5`) and conditional field mapping for multi-subsidiary consolidation. BRA does not reference DQTechnical or SQLike, relying instead on inline Java for technical validation and SQL-like operations.

### USA (128 snippets -- 25% of total)

The United States region follows a similar profile to BRA: 52 tJava blocks (StoreJobStats boilerplate), 32 tJavaRow components, and 21 shared routine references (7 each for the three universal routines). The tJavaRow count matches BRA exactly at 32, reflecting a comparable level of row-level transformation complexity. USA's Salesforce synchronization jobs contribute a notable share of the tJavaRow instances, where row-level Java code handles field mapping transformations that exceed tMap expression capabilities -- particularly for multi-valued picklist fields and Salesforce-specific data type conversions. Like BRA, USA does not reference DQTechnical or SQLike.

### JPN (112 snippets -- 22% of total)

Japan's 112 snippets break down into 44 tJava blocks, 32 tJavaRow components, and 18 shared routine references (6 each for the three universal routines). JPN's tJava count is disproportionately high relative to its job count, suggesting more complex jobs with multiple BigQuery interactions per job (each triggering a separate StoreJobStats block). The 32 tJavaRow instances handle the complexity of Japan's multi-source GL entry processing, where four separate data streams are joined and transformed in a single job -- requiring row-level logic that cannot be expressed in a single tMap.

### Global (48 snippets -- 9% of total)

Global's 48 snippets are entirely shared routine references -- no tJava, no tJavaRow, no tJavaFlex. The region references all five shared routines: DataQuality (8), DataQualityDependencies (8), DqStringHandling (8), DQTechnical (8), and SQLike (8). This makes Global the only region that uses the full complement of shared routines, reflecting its role as the cross-entity consolidation layer where the most sophisticated data quality and transformation logic is needed. The absence of job-level custom code (tJava, tJavaRow) means Global's jobs rely entirely on tMap expressions and shared routines for their logic, making them the cleanest migration targets from a custom code perspective.

### GENERIC (24 snippets -- 5% of total)

GENERIC stands out as the only region using tJavaFlex components (13 instances). The tJavaFlex architecture -- with its begin/main/end code sections -- supports the complex flow control required by the parameterized Business Central API extraction templates. These templates must handle paginated API responses, dynamic endpoint construction, and conditional retry logic, all of which exceed the capabilities of simpler tJava or tJavaRow components. GENERIC also contains 5 tJavaRow instances and 3 shared routine references. The tJavaFlex components represent the highest per-snippet complexity in the entire platform and should receive disproportionate attention during migration.

### Smaller Regions (DEU, ITA, SAF, ZAF, scratchpad)

The remaining five regions collectively account for only 34 snippets (7% of total). DEU has 8 snippets (1 tJava, 1 tJavaRow, 3 shared routines), reflecting its small pipeline footprint. ITA has 7 snippets (1 tJava, 4 shared routines including DQTechnical), suggesting a later-built pipeline that adopted the more complete routine set. SAF and ZAF each have 6 snippets, all shared routine references (including DQTechnical and SQLike), with no job-level custom code -- consistent with their minimal pipeline size. The scratchpad region has 7 snippets (1 tJava, 4 shared routines) representing development and test code.

## Region Comparison

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

Three regions -- BRA, USA, and JPN -- account for 79% of all custom code (403 of 509 snippets). The Global region's 48 snippets are exclusively shared routines with no job-level tJava. Smaller regions (DEU, ITA, SAF, ZAF) rely almost entirely on shared routines, with minimal or no job-specific custom code.

The GENERIC region is an outlier: it contains 13 tJavaFlex instances (the only region using this component type), reflecting the more complex flow control needed for the parameterized Business Central extraction templates.

## Risk Assessment for Migration

Not all 509 custom code snippets carry equal migration risk. The following assessment categorizes them by the effort and risk involved in migrating each pattern to a target platform.

### High Risk: tJavaFlex Components (13 snippets)

The 13 tJavaFlex components in the GENERIC region represent the highest migration risk per snippet. Each tJavaFlex has three code sections (begin, main, end) that together implement complex flow control logic -- paginated API calls, conditional looping, and dynamic variable management. These cannot be translated mechanically; each requires a developer to understand the flow logic, reimplement it in the target platform's idiom, and test it against the live Business Central API. Estimated effort: 2-4 hours per component for a developer familiar with both Talend and the target platform.

### Medium Risk: tJavaRow Components (102 snippets)

The 102 tJavaRow components perform row-level transformations that may encode implicit business logic. While many are straightforward (type casting, string formatting), some contain conditional logic that represents business decisions -- such as the sign-reversal pattern for return quantities or conditional field population based on order status. Each must be reviewed to determine whether the logic is pure technical plumbing or encodes a business rule. Estimated effort: 0.5-2 hours per component, depending on complexity.

### Low Risk: tJava_StoreJobStats (approximately 180 snippets)

The StoreJobStats boilerplate is low risk because it is a well-understood, uniform pattern. The migration approach is clear: replace all instances with a single shared metrics utility in the target platform. The risk is not in the translation but in completeness -- ensuring that every job that currently captures metrics continues to do so after migration. Estimated effort: 1-2 days to build the shared utility, plus 0.5 hours per job to wire it in.

### Negligible Risk: Shared Routine References (approximately 214 snippets)

The shared routine references (DataQuality, DataQualityDependencies, DqStringHandling, DQTechnical, SQLike) are negligible risk at the individual snippet level because they are just call sites. The risk is concentrated in the routines themselves, which must be faithfully reimplemented once. The 214 reference points then automatically benefit from the central reimplementation. Estimated effort: 2-5 days to reimplement the full routine library, with no per-snippet effort for the call sites.

### Migration Risk Summary

| Risk Level | Snippet Count | % of Total | Key Concern |
|-----------|--------------|------------|-------------|
| High | 13 | 3% | Complex flow control in tJavaFlex |
| Medium | 102 | 20% | Possible implicit business rules in tJavaRow |
| Low | ~180 | 35% | StoreJobStats boilerplate -- replace with shared utility |
| Negligible | ~214 | 42% | Shared routine references -- migrate the routine, not the calls |

The encouraging takeaway is that 77% of custom code snippets fall into the low or negligible risk categories. The 13 high-risk tJavaFlex components and 102 medium-risk tJavaRow components are the ones that demand careful, per-snippet review during migration planning.

### Recommended Review Process for tJavaRow Components

Given that the 102 tJavaRow components represent the largest block of medium-risk migration targets, the following review process is recommended:

1. **Categorize by transformation type.** For each tJavaRow, determine whether it performs type coercion (string to number, string to date), conditional logic (if/then field selection), arithmetic (BigDecimal operations, sign reversal), or data enrichment (lookup joins, derived fields). Type coercion and simple arithmetic are mechanical translations; conditional logic and data enrichment may encode business rules.

2. **Flag conditional logic for business review.** Any tJavaRow that contains `if`, `switch`, or ternary (`? :`) statements should be reviewed by a business analyst -- not just a developer -- to confirm that the conditional logic is correctly understood and will be faithfully reproduced in the target platform.

3. **Document the transformation contract.** For each tJavaRow, document the input fields, the output fields, and the transformation applied. This documentation becomes the specification for the equivalent transformation in the target platform and serves as a test case definition.

4. **Test with production data samples.** Before and after migration, run the same input records through the original tJavaRow and the target platform equivalent, and compare outputs field by field. Any discrepancy indicates a translation error that must be resolved before go-live.

## The Relationship Between Custom Code and tMap Expressions

It is important to distinguish between custom code (the 509 snippets analyzed in this document) and Java expressions embedded in tMap components. tMap expressions are not counted as custom code snippets -- they are part of the tMap component's configuration -- but they contain substantial Java logic that will also need migration.

The platform's 213 tMap components contain hundreds of inline Java expressions for field transformations, null handling, type coercion, and conditional logic. These expressions are documented in the Data Quality and Transformation analyses rather than here, but they share a common migration concern: all Java-syntax expressions must be translated to the target platform's expression language (SQL, Python, or a proprietary expression language).

The distinction matters for migration planning because tMap expressions are structurally simpler than tJavaRow code (they are single-expression transformations rather than multi-statement procedures) but far more numerous. The custom code migration and the tMap expression migration should be planned as parallel workstreams with shared tooling for Java-to-target translation.

In summary, the total Java migration surface is broader than the 509 custom code snippets alone -- it includes all tMap expressions across all 213 tMap components plus the 509 snippets documented here. The custom code analysis focuses on the standalone snippets because they carry the highest per-unit complexity and risk, but the broader expression landscape should not be overlooked.

A unified translation toolkit -- capable of parsing Java ternary expressions, method calls, and BigDecimal arithmetic into the target language -- would serve both the custom code migration and the tMap expression migration, reducing total effort significantly.

## Migration Takeaways

**Replace tJava_StoreJobStats with a shared metrics utility.** The most impactful consolidation opportunity is converting the copy-pasted StoreJobStats boilerplate into a single, shared logging and metrics module in the target platform. This eliminates 180+ duplicate code blocks and provides a single point of maintenance for execution metrics.

**Preserve shared routines as reusable libraries.** The DataQuality, DqStringHandling, DQTechnical, and SQLike routines are already well-structured for reuse. In the target platform, these should become shared utility libraries or modules, maintaining the separation of concerns the original developers intended.

**Audit tJavaRow components for implicit business logic.** The 102 tJavaRow instances -- concentrated evenly across BRA, JPN, and USA at 32 each -- perform row-level transformations that may encode business-critical logic. Each should be reviewed during migration planning to determine whether the transformation is pure technical plumbing (type casting, string formatting) or encodes a business decision.

**Translate Java expressions in tMap to the target language.** Beyond standalone tJava components, Java expressions embedded in tMap transformations will need translation to SQL, Python, or whatever expression language the target platform supports. This is a separate concern from the custom code analysis but closely related.

**Prioritize the GENERIC tJavaFlex components for early migration.** The 13 tJavaFlex components carry the highest per-snippet complexity and risk. These should be migrated early in the project timeline so that any unexpected difficulties are discovered before the migration reaches the high-volume BRA, JPN, and USA regions.

**Unify the shared routine footprint across all regions.** The inconsistent distribution of DQTechnical and SQLike -- present in some regions but not others -- should be resolved during migration. All regions should converge on a single, complete validation and utility library, eliminating the current fragmentation where BRA, JPN, and USA handle technical validation differently from Global, SAF, and ZAF.
