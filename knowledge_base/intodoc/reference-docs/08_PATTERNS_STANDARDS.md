# Patterns & Standards

This document consolidates naming conventions, code patterns, reusability assessment, design patterns, preventive analysis, technical debt assessment, and remediation planning into a single reference. It covers how jobs, datasets, and tables are named; how regions share (or fail to share) components and logic; where consolidation opportunities exist; what will go wrong next if left unaddressed; and how to prioritize remediation. Understanding these patterns is essential for planning a migration that preserves what works, fixes what does not, and avoids duplicating effort across regions.

---

## Table of Contents

- [Naming Conventions](#naming-conventions)
  - [The Standard Pattern](#the-standard-pattern)
  - [The Naming Hierarchy](#the-naming-hierarchy)
  - [Region and Subsidiary Codes](#region-and-subsidiary-codes)
  - [Layer Codes](#layer-codes)
  - [Convention Compliance by Region](#convention-compliance-by-region)
  - [Anomalies and Exceptions](#anomalies-and-exceptions)
  - [Naming Conventions in the Data Warehouse](#naming-conventions-in-the-data-warehouse)
- [Reusability Assessment](#reusability-assessment)
  - [Top Shared Components](#top-shared-components)
  - [The Copy Problem](#the-copy-problem)
  - [The Duplication Problem](#the-duplication-problem)
  - [Duplicated Logic Patterns](#duplicated-logic-patterns)
  - [Measuring Reusability](#measuring-reusability)
  - [The Template Pattern (GENERIC)](#the-template-pattern-generic)
  - [Reuse Maturity by Region](#reuse-maturity-by-region)
  - [Opportunities for Consolidation](#opportunities-for-consolidation)
  - [What Is Done Well](#what-is-done-well)
- [Preventive Analysis](#preventive-analysis)
  - [Health Score Summary](#health-score-summary)
  - [Methodology](#methodology)
  - [Critical Path Risks](#critical-path-risks)
  - [Bug Potential Catalog](#bug-potential-catalog)
  - [Recommended Fixes (Prioritized)](#recommended-fixes-prioritized)
- [Technical Debt Assessment](#technical-debt-assessment)
  - [Debt by Category](#debt-by-category)
  - [Debt by Region](#debt-by-region)
  - [Debt Trend Indicators](#debt-trend-indicators)
- [Remediation Roadmap](#remediation-roadmap)
  - [Phase 1: Stop the Bleeding (Weeks 1-4)](#phase-1-stop-the-bleeding-weeks-1-4)
  - [Phase 2: Foundation Hardening (Months 2-3)](#phase-2-foundation-hardening-months-2-3)
  - [Phase 3: Systematic Improvement (Months 4-6)](#phase-3-systematic-improvement-months-4-6)
- [Migration Takeaways](#migration-takeaways)

---

## Naming Conventions

The platform follows a well-established naming convention for jobs, datasets, and layers. The standard pattern is applied consistently across most regions, with few violations detected. This disciplined approach is one of the platform's strengths: it makes jobs self-documenting, enables quick identification of a job's scope and purpose, and simplifies operational monitoring. The convention should be preserved in any migration to maintain continuity.

### The Standard Pattern

Job names follow a multi-segment structure:

```
{Region/Subsidiary}_{Layer}_{Description}
```

Examples:
- `VBR_Clean_Build_Sales_Set` -- VBR subsidiary, Clean layer, building the Sales Set
- `VJP_Raw_salesInvoiceHeaders` -- VJP subsidiary, Raw layer, extracting sales invoice headers
- `DEL_Clean_Sales_Set_Delete_Ingest` -- DEL subsidiary, Clean layer, delete-and-ingest pattern for Sales Set
- `NPL_Clean_Account_v4_Salesforce` -- NPL subsidiary, Clean layer, version 4 of Salesforce account sync

The first segment identifies the owning region or subsidiary. The second identifies the data layer. The remainder describes the specific function, often including version numbers (v1, v5, v15) or target system identifiers (Salesforce, BigQuery).

### The Naming Hierarchy

The naming convention encodes a four-level hierarchy mapping to the organizational and technical structure: Region, Subsidiary, Layer, and Description.

**Level 1: Region.** The broadest organizational unit corresponding to a geographic area (BRA, JPN, USA, DEU, ITA). Some regions are functional rather than geographic -- Global represents cross-entity operations, GENERIC represents parameterized templates, and scratchpad represents development and test work. The region determines which project folder a job lives in, which context variable groups it can access, and which datasets it reads from and writes to.

**Level 2: Subsidiary.** The specific legal entity within a region. A single region may contain multiple subsidiaries (e.g., USA contains both VUS and NPL). The subsidiary code determines which source system instance the job connects to. Different subsidiaries within the same region may have different source systems, data models, and business rules.

**Level 3: Layer.** Where the job sits in the data processing pipeline:
- **Raw** -- Extraction from source systems with minimal transformation
- **Clean** -- Transformation, validation, and business logic
- **Report** -- Reporting-specific views and aggregations
- **Forecast** -- Forward-looking financial projections
- **Global** -- Cross-entity consolidation

**Level 4: Description.** Free-form remainder identifying the specific data entity or business process. Often includes version numbers (indicating iterative refinement) and target system identifiers (indicating synchronization jobs).

Hierarchy examples:

| Job Name | Region | Subsidiary | Layer | Description |
|----------|--------|-----------|-------|-------------|
| `VBR_Clean_Build_Sales_Set` | BRA | VBR | Clean | Build Sales Set |
| `NPL_Raw_salesInvoiceHeaders` | USA | NPL | Raw | salesInvoiceHeaders |
| `VJP_Clean_Build_Sales_Set` | JPN | VJP | Clean | Build Sales Set |
| `VDA_BC_Clean_Invoice_Set_v3` | DEU | VDA | Clean | BC Invoice Set v3 |
| `VUS_Clean_Animalytix` | USA | VUS | Clean | Animalytix |
| `Global_Build_Sales_Set` | Global | (none) | Global | Build Sales Set |

Note: Global-level jobs omit the subsidiary code because they operate across subsidiaries. GENERIC jobs use `GEN_BC_` as a prefix, omitting both region and subsidiary because they are parameterized templates.

### Region and Subsidiary Codes

The platform uses two overlapping code systems: three-letter ISO-style region codes and entity-specific subsidiary codes.

**Region codes** identify the geographic scope:

| Code | Region | Usage Count |
|------|--------|-------------|
| Global | Cross-entity / shared | {n} |
| NPL | Newport Labs (USA) | {n} |
| VDA | Vaxxinova Germany Autogenous | {n} |
| Legacy | Historical / migrated jobs | {n} |
| ITA | Italy | {n} |
| BRA | Brazil | {n} |
| DEU | Germany | {n} |
| JPN | Japan | {n} |
| USA | United States | {n} |

**Subsidiary codes** identify the specific legal entity:

| Code | Subsidiary | Region |
|------|-----------|--------|
| VBR | Vaxxinova Brazil | BRA |
| VIT | Vaxxinova Italy | ITA |
| VUS | Vaxxinova United States | USA |
| VJP | Vaxxinova Japan | JPN |
| VDD | Vaxxinova Germany (second entity) | DEU |
| NPL | Newport Labs | USA |

Some regions contain multiple subsidiaries (USA has both VUS and NPL), while the subsidiary code is always specific to a single legal entity. Jobs that operate at the subsidiary level use subsidiary codes; jobs that operate at the regional or cross-entity level use region codes.

### Layer Codes

The second segment of the naming pattern identifies the data processing layer:

| Layer Code | Meaning | Job Count |
|-----------|---------|-----------|
| Clean | Transformed, validated, business-ready data | {n} |
| Raw | Source system extracts, minimal transformation | {n} |
| RAW | Variant capitalization of Raw | {n} |
| Global | Cross-entity shared datasets | {n} |
| Report | Reporting-specific views | {n} |
| Forecast | Forecasting pipeline | {n} |

The Clean layer is the most heavily used, reflecting the platform's emphasis on data transformation and validation. The split between `Raw` and `RAW` is a minor inconsistency -- both refer to the same conceptual layer, but casing varies by region.

### Convention Compliance by Region

While the global naming convention is strong, compliance varies by region.

**BRA -- Compliance: Good**

Jobs consistently use the subsidiary prefix (VBR, BIO) followed by the layer code and a descriptive name. The only inconsistency is the occasional use of `RAW` instead of `Raw` in some older jobs. The dual-subsidiary structure is handled cleanly -- the subsidiary code unambiguously identifies which legal entity the job serves.

**JPN -- Compliance: Good**

Jobs follow the convention reliably with `VJP` and `VJA` as subsidiary prefixes. Layer codes are consistently capitalized. The only notable pattern is `Copy_of_` prefixes on some jobs, indicating cloned jobs not yet renamed. These are functionally correct but carry metadata about creation history rather than current purpose.

**USA -- Compliance: Good**

The most complex subsidiary landscape (VUS, NPL), handled cleanly with consistent prefixes. Salesforce synchronization jobs append `_Salesforce` to the description, a useful extension identifying the target system. Version numbers (v4, v8, v15, v26) are prevalent, reflecting iterative development.

**DEU -- Compliance: Good**

Uses two subsidiary codes (VDA, VDD) consistently. The `VDA_BC_` prefix adds an additional segment identifying the Business Central source -- a minor deviation that improves readability. Some jobs use `RAW` capitalization.

**ITA -- Compliance: Good**

Jobs use the `ITA` region prefix and `VIT` subsidiary code consistently. The `IZO` subsidiary code appears in Salesforce sync jobs. Both `RAW` and `Clean` layer codes appear with the same minor capitalization inconsistency.

**ZAF -- Compliance: Good (with caveat)**

Jobs follow the naming structure but carry inherited Italian names (e.g., `ITA_VIT_RAW_Copy`) because the pipeline was cloned from ITA and not fully renamed. The structure is compliant but the content is misleading.

**SAF -- Compliance: Good (with caveat)**

Same inherited-naming issue as ZAF. Its single job carries the ITA/VIT prefix from the original Italian template.

**GENERIC -- Compliance: Poor (by design)**

Jobs use the `GEN_BC_` prefix, which does not follow the standard pattern. This is intentional: GENERIC jobs are parameterized templates that work across subsidiaries. The naming is internally consistent and well-understood.

**Global -- Compliance: Poor**

Contains the most heterogeneous naming. Jobs use a mix of prefixes: `Global_`, `Legacy_`, and subsidiary-specific prefixes for jobs organizationally placed in Global but actually subsidiary-specific. This mixing of conventions makes Global harder to navigate.

**scratchpad -- Compliance: Poor (expected)**

Contains development and test jobs with names like `BigQuery_TEST` and `sandbox`. Appropriate for their purpose but not for production migration.

### Anomalies and Exceptions

Several naming patterns break the standard convention:

**Copy_of_* Jobs.** Multiple jobs carry the `Copy_of_` prefix, indicating cloning in the IDE. These provide no operational value and obscure the job's purpose. During migration, all `Copy_of_` prefixes should be stripped and jobs renamed.

**Version Number Proliferation.** Some regions have accumulated high version numbers (v15, v26) reflecting iterative refinement with no information about what changed between versions. Consider resetting during migration or dropping versions entirely if the target platform provides native version control.

**Sandbox and Test Jobs.** Jobs with names like `BigQuery_TEST` and `sandbox` should not be migrated to production but may need preservation as development templates.

**Legacy Prefix.** Jobs in Global carrying the `Legacy_` prefix predate the current convention. Rename during migration to follow the standard pattern.

**Inherited Region Codes.** Jobs in ZAF and SAF carrying ITA/VIT codes from cloning rather than their actual region. Must be renamed to avoid confusion in monitoring and alerting.

### Naming Conventions in the Data Warehouse

Naming conventions extend into data warehouse datasets and tables.

**Dataset naming** follows `{region}_{subsidiary}_{layer}` (lowercase, underscore-separated):
- `bra_vbr_raw` -- Brazil, VBR subsidiary, Raw layer
- `jpn_vjp_raw` -- Japan, VJP subsidiary, Raw layer
- `usa_vus_clean` -- USA, VUS subsidiary, Clean layer
- `global_clean` -- Global consolidation, Clean layer

This alignment between job names and dataset names makes it straightforward to determine which dataset a job operates on.

**Table naming** follows a less formal convention. Raw layer tables mirror source system entity names (camelCase/PascalCase from APIs). Clean layer tables use business-oriented names (PascalCase_With_Underscores). The inconsistency is a natural consequence of architecture: Raw tables are generated from API responses, Clean tables are designed by developers.

**Naming gaps to address during migration:**
- No consistent case convention across layers. Adopt snake_case platform-wide.
- No table versioning. Schema changes overwrite rather than version tables.
- No temporal partitioning indicators in table names.

---

## Reusability Assessment

The platform presents a split personality on code reuse. The shared infrastructure layer is well-designed: the `tPostjob` + `cleanup_temp_file_storage` pattern and the logging joblet appear in the majority of jobs, providing consistent post-execution cleanup and audit logging. On the other hand, duplicated logic patterns are scattered across regions -- expressions copy-pasted between jobs, constants hardcoded in multiple places, and stats boilerplate reproduced verbatim rather than abstracted into a shared component.

### Top Shared Components

The most frequently reused components reveal the platform's architectural spine:

| Component | Type | Reuse Count | Purpose |
|-----------|------|-------------|---------|
| tMap | Transformation | {n} | Core transformation logic in every data flow |
| tBigQueryOutput | Output | {n} | BigQuery write operations |
| tPostjob | Lifecycle | {n} | Post-execution hook for cleanup |
| cleanup_temp_file_storage | Joblet | {n} | Removes staging files after load |
| Vaxxinova_Logging | Joblet | {n} | Centralized execution audit trail |
| tPrejob | Lifecycle | {n} | Pre-execution initialization |
| tFixedFlowInput | Input | {n} | Configuration/parameter injection |
| tBigQueryInput | Input | {n} | BigQuery read operations |
| tBigQuerySQLRow | SQL | {n} | BigQuery SQL execution |
| tUnite | Merge | {n} | Combining multiple data streams |

The tPostjob / cleanup / logging trio forms a standard job template applied to the majority of production jobs. This is a well-executed pattern ensuring every production job cleans up temporary files and produces a consistent audit log. Jobs lacking this pattern are typically scratchpad/test jobs and early-stage development jobs.

The gap between tPostjob and tPrejob counts suggests that some jobs rely on implicit initialization rather than an explicit pre-job setup step.

### The Copy Problem

The platform's most significant reusability failure is the prevalence of copying as a development strategy. Job cloning and expression copying have produced a landscape where similar logic exists in multiple places, each copy evolving independently.

**Explicit copy markers.** Multiple jobs carry the `Copy_of_` prefix -- IDE artifacts indicating duplication.

**Cross-region structural clones.** Core jobs (e.g., `Clean_Build_Sales_Set`) exist in multiple regions with near-identical structures. Each region's version was created by copying a template and modifying for regional specifics. The shared logic -- 80-90% of the job -- is duplicated rather than parameterized.

**Version proliferation.** Jobs with high version numbers (v15, v26) were likely created by copying the previous version and making incremental changes, resulting in a chain of copies where only the latest is active.

Three consequences for migration:

1. **Maintenance burden.** When a shared pattern changes, the change must be applied to every copy individually, each with its own testing and deployment cycle.
2. **Drift between copies.** Over time, copies diverge. One region's version may have bug fixes never propagated to others. Without a diff tool, these divergences are invisible.
3. **Ambiguity about which copy is authoritative.** When multiple copies exist, it is not always clear which is the active production version.

### The Duplication Problem

The most significant single duplication is the stats capture boilerplate (e.g., `tJava_StoreJobStats`) -- a block of code copy-pasted into virtually every job to capture execution metrics. Unlike the logging joblet (which is a proper shared component), the stats block exists as raw code copied between components.

The irony is instructive: the developers understood shared components (they built the logging joblet and cleanup joblet) but did not apply the same discipline to stats capture. The likely explanation is organic evolution: one developer wrote it, others copied it, and the pattern became entrenched before abstraction.

Converting this to a shared module would be the single largest consolidation win, eliminating 180+ duplicate code blocks.

### Duplicated Logic Patterns

Beyond the stats boilerplate, the analysis detects duplicated expression patterns scattered across the codebase:

| Region | Duplicated Patterns | Examples |
|--------|-------------------|----------|
| JPN | {n} (highest) | Hardcoded constants: entity name repeated 6 times, currency code 6 times, country code 4 times in single jobs |
| BRA | {n} | Repeated field references, product ID construction patterns |
| USA | {n} | Salesforce field mappings repeated across output paths |
| Global | {n} | Account fields repeated across output branches |
| ITA | {n} | Product identifiers mapped to multiple outputs |
| GENERIC | {n} | globalMap.get calls repeated in BC onramp jobs |

Types of duplication:
- **Hardcoded constants** -- entity names, currency codes, country codes repeated in tMap expressions instead of referenced from configuration
- **Repeated field references** -- the same field accessed identically in multiple output paths
- **Product ID construction patterns** -- string concatenation patterns for ID generation duplicated across outputs
- **Salesforce field mappings** -- identical field mappings across multiple sync jobs

### Measuring Reusability

**Shared Component Adoption Rate.** The platform has numerous unique shared components. The adoption rate for the core infrastructure trio is approximately 68%. This is strong for infrastructure-level reuse. However, application-level shared component adoption is much lower, with data quality routines and utility routines used inconsistently across regions.

**Duplication Ratio.** Approximately 30-40% of the platform's total logic is duplicated rather than shared. This is high by modern standards (a well-designed platform targets below 10%) but not unusual for codebases that evolved over several years.

**Parameterization Score.** The GENERIC region scores high -- its templates use context variables to parameterize logic for different subsidiaries. Major production regions score low -- they hard-code entity names, currency codes, and subsidiary identifiers rather than parameterizing. Overall: low. The concept is proven but not applied broadly.

### The Template Pattern (GENERIC)

The GENERIC region's parameterized jobs represent the platform's best reusability example.

**How it works.** Templates accept context parameters (company identifier, producer name) that determine the target data source, output dataset, and metadata fields. The same job definition serves multiple entities without code duplication.

**Why it succeeds.**
1. The source API is uniform across all companies, so extraction logic is genuinely identical.
2. Variation is confined to configuration (company identifier, output dataset name).
3. Complex flow control logic for pagination, error handling, and retry is implemented once centrally.

**Where it could be extended:**
- **Sales_Set builders** -- exist as separate copies in multiple regions with 80-90% shared logic. A parameterized template could replace 4+ copies with 1.
- **Salesforce synchronization** -- sync jobs across regions share substantial field mapping logic. A parameterized template with per-entity configuration could reduce duplication.
- **Raw extraction from on-premises databases** -- follow the same pattern (connect, execute SELECT, write to data warehouse). A parameterized template with connection details and query as parameters could consolidate these.

### Reuse Maturity by Region

**GENERIC -- Maturity: High.** Parameterized templates achieve true code reuse. Complex logic is centralized. Minimal duplication detected. This should be the model for other regions.

**Global -- Maturity: Medium-High.** Benefits from the shared infrastructure trio and uses all shared routines. Low duplication count relative to job count. However, consolidation jobs are monolithic rather than parameterized.

**USA -- Maturity: Medium.** Good infrastructure reuse, moderate application-level duplication. Salesforce sync jobs are the primary source of duplication. High version numbers suggest iterative refinement without consolidation.

**BRA -- Maturity: Medium-Low.** More duplicated patterns than USA. Largest jobs are full copies rather than parameterized templates. The dual-subsidiary architecture creates natural duplication that has not been abstracted.

**JPN -- Maturity: Low.** Leads all regions in duplicated patterns. Heavy duplication concentrated in hardcoded constants repeated across multiple output paths within single jobs. Both a copy (Copy_of_ prefix) and a source of internal duplication.

**ITA -- Maturity: Medium.** Few duplicated patterns due to small pipeline footprint. However, ITA's pipeline was used as a clone source for SAF and ZAF through direct copying rather than parameterization.

**SAF/ZAF -- Maturity: Low.** Created by cloning the ITA pipeline wholesale. Jobs carry inherited Italian names. Improvements to ITA must be manually applied to both. This is the lowest form of reuse.

**DEU -- Maturity: Medium.** No significant duplication detected. Small pipeline limits both the opportunity for and impact of duplication. Uses shared infrastructure and routines appropriately.

### Opportunities for Consolidation

Three categories of duplication are candidates for consolidation:

1. **Shared constants.** Entity names, currency codes, and country codes should be defined once as configuration parameters, not hardcoded in expressions. Low-risk, high-reward refactoring.

2. **Common field mapping patterns.** CRM synchronization jobs share substantial mapping logic. Implement as parameterized mapping templates accepting entity-specific configuration.

3. **Cross-region job templates.** The platform already has one successful template pattern (GENERIC). Extend it to other standardized flows -- particularly the Sales_Set builders that share 80-90% of logic across regions.

### What Is Done Well

- **Shared logging joblet** -- genuine reuse providing consistent audit trail without duplication
- **Shared cleanup joblet** -- consistent resource management paired with lifecycle hooks
- **GENERIC template pattern** -- demonstrates parameterized job design that works
- **Centralized shared routines** -- data quality, string handling, and SQL utility routines properly centralized rather than copy-pasted

The gap is in application-level logic reuse, where convenience copying has prevailed over disciplined abstraction.

---

## Preventive Analysis

This section provides a forward-looking assessment. Unlike issue catalogs that document current problems, this analysis predicts what will go wrong next, identifies missing safeguards, scores job fragility, and provides a phased remediation roadmap with effort estimates.

### Health Score Summary

**Composite Health Score: {score}/100**

The composite score is a weighted average across seven dimensions, each scored 0-100 based on evidence from the scan data.

| Dimension | Weight | Score | Rationale |
|-----------|--------|-------|-----------|
| Error Handling | 25% | {N}/100 | % of jobs with at least one error handler (tDie, tLogCatcher, SUBJOB_ERROR, tCatch) |
| Data Integrity | 20% | {N}/100 | % of write operations with guards (row count validation, transaction wrapping, rollback) |
| Code Hygiene | 15% | {N}/100 | % of jobs that are NOT copy/backup/debug/test variants |
| Security | 15% | {N}/100 | % of jobs with no credential exposure, no hardcoded secrets, proper auth patterns |
| Performance | 10% | {N}/100 | % of jobs with bounded queries, pagination, appropriate commit intervals |
| Observability | 10% | {N}/100 | % of jobs with logging or monitoring (tLogCatcher, tStatCatcher, tLogRow on error paths) |
| Operational Resilience | 5% | {N}/100 | % of critical paths with retry logic, fallbacks, or graceful degradation |

**Score Interpretation:**
- 80-100: Well-managed platform with minor gaps
- 60-79: Functional but significant risks in specific areas
- 40-59: Material risks that require active remediation
- 20-39: High probability of production incidents without intervention
- 0-19: Critical -- production failures are likely imminent

### Methodology

This analysis applies six lenses to every job:

1. **Anti-Pattern Detection**: Scan for known dangerous patterns (DELETE-INSERT without guards, silent exception handling, hardcoded values)
2. **Safeguard Gap Analysis**: Identify protective mechanisms that SHOULD exist but DON'T (error catchers, retry logic, alerting, row count validation)
3. **Fragility Scoring**: Rate each job BRITTLE / FRAGILE / ADEQUATE / ROBUST based on how it handles unexpected conditions
4. **Cascade Modeling**: Trace failure propagation paths to identify blast radius of each potential failure
5. **Technical Debt Quantification**: Count copy variants, version proliferation, hardcoded values, code duplication, and abandoned jobs
6. **Forward Projection**: Based on patterns and growth trends, predict which failures will manifest first

All findings include specific job names, component references, and actionable fix proposals with effort estimates. Confidence levels (CERTAIN / LIKELY / INFERRED) indicate whether the risk is guaranteed by code inspection, probable based on patterns, or inferred from missing evidence.

### Critical Path Risks

The top risks ranked by (probability x business impact), representing the most likely and most damaging failures not yet addressed:

| ID | Risk | Probability | Business Impact | Affected Jobs | Current Safeguard | Proposed Fix | Effort | Fix Type |
|----|------|-------------|-----------------|---------------|-------------------|--------------|--------|----------|
| CPR-001 | {API rate limiting causes silent partial extraction} | {HIGH} | {CRITICAL} | {Raw extraction jobs} | {None -- try-catch masks error} | {Retry with backoff + row count validation} | {MEDIUM} | {Preventive} |
| CPR-002 | {DELETE-INSERT gap leaves target empty during failure} | {MEDIUM} | {CRITICAL} | {Ingest jobs} | {None} | {Temp table swap pattern: write staging, validate, swap} | {MEDIUM} | {Preventive} |
| CPR-003 | {Shared reference table corruption cascades to all regions} | {LOW} | {CRITICAL} | {Mapping jobs, all consumers} | {None -- single version, no backup} | {Pre-write row count check, backup copy, staleness alert} | {SMALL} | {Preventive} |
| CPR-004 | {Hardcoded fiscal year expires, reports produce wrong data} | {CERTAIN (annual)} | {HIGH} | {Budget/forecast jobs} | {None -- requires manual code edit} | {Replace with context variable, derive from config} | {TRIVIAL} | {Corrective} |
| CPR-005 | {On-premises database unavailable halts regional pipeline} | {MEDIUM} | {HIGH} | {On-prem extraction jobs} | {None -- no retry, no fallback} | {Connection retry with timeout, alert on failure, failover doc} | {SMALL} | {Preventive} |

### Bug Potential Catalog

**Error Handling Gaps**

| ID | Description | Trigger Scenario | Current Behavior | Proposed Fix | Effort | Confidence |
|----|-------------|------------------|------------------|--------------|--------|------------|
| PA-EH-001 | {No SUBJOB_ERROR handler on critical write subjobs} | {Data warehouse write fails} | {Error silently swallowed, job reports success} | {Add SUBJOB_ERROR -> tDie on every write subjob} | {TRIVIAL} | {CERTAIN} |
| PA-EH-002 | {DIE_ON_CHILD_ERROR=false on orchestrator calls} | {Child extraction job fails} | {Parent continues, downstream processes stale data} | {Set DIE_ON_CHILD_ERROR=true, add return code checking} | {TRIVIAL} | {CERTAIN} |
| PA-EH-003 | {tMap reject flows not connected} | {Lookup miss, type conversion error, null in required field} | {Row silently dropped} | {Connect reject outputs to error table for audit trail} | {SMALL} | {CERTAIN} |

**Data Integrity Risks**

| ID | Description | Trigger Scenario | Current Behavior | Proposed Fix | Effort | Confidence |
|----|-------------|------------------|------------------|--------------|--------|------------|
| PA-DI-001 | {TRUNCATE write mode with no row count validation} | {Source query returns zero rows due to upstream failure} | {Target truncated and zero rows written -- appears successful} | {Pre-write row count assertion: if source < threshold, abort} | {SMALL} | {CERTAIN} |
| PA-DI-002 | {No deduplication between legacy and new pipelines} | {Both pipelines active during migration} | {Both write to same target -- potential double-counting} | {Entity-based dedup logic or orchestration mutex} | {MEDIUM} | {LIKELY} |

**Silent Failure Scenarios**

| ID | Description | Trigger Scenario | Current Behavior | Proposed Fix | Effort | Confidence |
|----|-------------|------------------|------------------|--------------|--------|------------|
| PA-SF-001 | {Pagination try-catch masks API errors as success} | {API returns error mid-pagination} | {Exception caught, "All Records Processed" printed, job succeeds} | {Check HTTP status in loop, throw on non-2xx, add row count output} | {MEDIUM} | {CERTAIN} |
| PA-SF-002 | {Flat file credential load fails silently} | {Credential file missing or permissions changed} | {Read fails, subsequent API calls fail with auth error} | {File existence check and non-empty validation before API calls} | {SMALL} | {LIKELY} |

**Race Conditions, Resource Leaks, and Implicit Assumptions**

| ID | Description | Trigger Scenario | Current Behavior | Proposed Fix | Effort | Confidence |
|----|-------------|------------------|------------------|--------------|--------|------------|
| PA-RC-001 | {Concurrent execution of primary and backup ingest jobs} | {Scheduler runs both simultaneously} | {Race condition on target table} | {Remove backup from scheduler or add mutex} | {SMALL} | {INFERRED} |
| PA-RL-001 | {Unbounded queries exhaust processing slots} | {Source table grows beyond current size} | {Full table scan on every execution} | {Add WHERE clause with date filter or incremental extraction} | {SMALL} | {LIKELY} |
| PA-IA-001 | {Upstream job assumed to run before downstream without enforcement} | {Jobs run out of order} | {Clean job reads stale or empty data} | {Explicit dependency checks: verify freshness before processing} | {MEDIUM} | {LIKELY} |
| PA-IA-002 | {Schema assumed stable for hardcoded column lists} | {Source schema changes} | {Extraction fails with column not found error} | {Schema contract documentation and validation} | {MEDIUM} | {LIKELY} |

**Single Points of Failure**

| ID | Description | Trigger Scenario | Current Behavior | Proposed Fix | Effort | Confidence |
|----|-------------|------------------|------------------|--------------|--------|------------|
| PA-SP-001 | {Shared extraction template used across all regions} | {Bug in shared logic} | {All regions receive corrupted data simultaneously} | {Per-region validation checks, version-pin shared jobs} | {LARGE} | {CERTAIN} |
| PA-SP-002 | {Critical mapping table single version, no backup} | {Data corrupted or deleted} | {All reports use wrong attributions} | {Versioned backup, row count + hash validation before overwrite} | {SMALL} | {CERTAIN} |

### Recommended Fixes (Prioritized)

**Immediate (Week 1-2)** -- highest impact, lowest effort:

| Priority | Fix | Finding Refs | Effort | Impact |
|----------|-----|--------------|--------|--------|
| 1 | Replace hardcoded fiscal year with context variable | CPR-004 | 2 hours | Eliminates annual manual code change risk |
| 2 | Set DIE_ON_CHILD_ERROR=true on all orchestrator calls | PA-EH-002 | 4 hours | Prevents silent child failure propagation |
| 3 | Add SUBJOB_ERROR -> tDie on critical write subjobs | PA-EH-001 | 1 day | Converts silent failures to detected failures |
| 4 | Remove/disable all Copy_of_/Backup/Debug jobs from scheduler | PA-RC-001 | 2 hours | Eliminates accidental execution of non-canonical jobs |

**Short-Term (Month 1)** -- structural improvements:

| Priority | Fix | Finding Refs | Effort | Impact |
|----------|-----|--------------|--------|--------|
| 5 | Add row count validation to TRUNCATE and DELETE-INSERT patterns | PA-DI-001, CPR-002 | 3 days | Prevents empty/corrupt target tables |
| 6 | Connect all tMap reject flows to error logging | PA-EH-003 | 3 days | Makes data loss visible and auditable |
| 7 | Add error catchers to all jobs without error logging | PA-EH-001 | 5 days | Creates audit trail for all failures |
| 8 | Fix pagination try-catch to check HTTP status codes | PA-SF-001, CPR-001 | 5 days | Prevents silent partial extractions |

**Medium-Term (Quarter 1)** -- platform-level improvements:

| Priority | Fix | Finding Refs | Effort | Impact |
|----------|-----|--------------|--------|--------|
| 9 | Implement retry logic for all API extractions | CPR-001, CPR-005 | 2 weeks | Handles transient failures automatically |
| 10 | Replace DELETE-INSERT with temp table swap pattern | CPR-002 | 1 week | Eliminates data gap window during loads |
| 11 | Add data freshness monitoring and alerting | PA-SF-002 | 1 week | Detects stale data before it reaches reports |
| 12 | Consolidate identical extraction jobs into templates | Technical debt | 2 weeks | Reduces maintenance from Nx to 1x |

**Long-Term** -- strategic improvements:

| Priority | Fix | Finding Refs | Effort | Impact |
|----------|-----|--------------|--------|--------|
| 13 | Implement proper credential management (vault/secrets manager) | PA-SF-002 | 2 weeks | Secrets encrypted, rotation automated |
| 14 | Add schema contract testing for all source interfaces | PA-IA-002 | 3 weeks | Detects schema drift before production impact |
| 15 | Build monitoring dashboard for pipeline health | Multiple | 3 weeks | Proactive detection of all failure modes |

---

## Technical Debt Assessment

### Debt by Category

| Category | Debt Items | Remediation Estimate | Risk if Unaddressed |
|----------|-----------|---------------------|---------------------|
| Copy/Backup Variants | {N} jobs with Copy_of_, _Backup, _DEBUG, _COPY, _Test | {X} days to audit and remove | Accidental execution of wrong variant, configuration drift |
| Version Proliferation | {N} jobs at v5+ with unclear canonical version | {X} days to consolidate | Wrong version deployed, inconsistent outputs |
| Hardcoded Values | {N} instances of hardcoded datasets, fiscal years, URLs | {X} days to parameterize | Annual breakage, environment-locked jobs |
| Code Duplication | {N} groups of near-identical jobs | {X} weeks to template | N-times maintenance burden for any change |
| Abandoned Jobs | {N} jobs never called and likely deprecated | {X} hours to archive | Confusion, accidental execution, repo clutter |

### Debt by Region

| Region | Copy Variants | Missing Error Handling | Hardcoded Values | Debt Score |
|--------|--------------|----------------------|------------------|------------|
| BRA | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| JPN | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| USA | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| DEU | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| ITA | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| Global | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| GENERIC | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |
| SAF/ZAF | {N} | {N} of {total} jobs | {N} | {HIGH/MEDIUM/LOW} |

### Debt Trend Indicators

Evidence of development patterns that accumulate debt:

- **Copy-Based Development**: Copy_of_ jobs indicate developers clone rather than parameterize, creating maintenance burden
- **Version Accumulation**: Jobs with 8+ iterations without consolidation suggest iterative refinement without cleanup
- **Environment Coupling**: Jobs with hardcoded test/prod dataset names cannot be safely promoted between environments
- **No Deprecation Process**: Old job versions remain in the repository with no archival or removal process

---

## Remediation Roadmap

### Phase 1: Stop the Bleeding (Weeks 1-4)

**Goal**: Eliminate the highest-probability, highest-impact failure modes.

| Week | Action | Targets | Expected Outcome |
|------|--------|---------|-----------------|
| 1 | Fix hardcoded values (fiscal years, dataset names) | Budget/config jobs | Jobs deploy to any environment without code changes |
| 1 | Set DIE_ON_CHILD_ERROR=true everywhere | All orchestrator calls | Child failures always detected |
| 2 | Add SUBJOB_ERROR -> tDie on critical write paths | Top 20 highest-risk jobs | Silent write failures become detected failures |
| 2 | Disable/archive Copy_of_ and _Backup jobs | All non-canonical variants | Eliminates accidental execution risk |
| 3 | Add row count validation to DELETE-INSERT jobs | All ingest jobs | Prevents empty target tables |
| 4 | Fix pagination try-catch to check status codes | Template for all API extraction jobs | Partial extractions detected |

### Phase 2: Foundation Hardening (Months 2-3)

**Goal**: Build systematic safeguards across the platform.

| Week | Action | Targets | Expected Outcome |
|------|--------|---------|-----------------|
| 5-6 | Add error catchers to all production jobs | All jobs without centralized logging | Complete audit trail for all failures |
| 6-7 | Connect all tMap reject flows | All tMap components with unconnected rejects | Data loss becomes visible and auditable |
| 7-8 | Implement retry logic for API extractions | All REST/SOAP extraction jobs | Transient failures handled automatically |
| 8-9 | Add data freshness monitoring | All critical pipeline endpoints | Stale data detected within SLA window |
| 9-10 | Consolidate identical extraction jobs into templates | Groups of identical jobs | Single template to maintain instead of N copies |

### Phase 3: Systematic Improvement (Months 4-6)

**Goal**: Transform from reactive to proactive operations.

| Month | Action | Targets | Expected Outcome |
|-------|--------|---------|-----------------|
| 4 | Implement proper credential management | All jobs using flat-file credentials | Secrets encrypted, rotation automated |
| 4-5 | Build pipeline health dashboard | All production pipelines | Real-time visibility into pipeline health |
| 5 | Add schema contract testing | All source system interfaces | Schema drift detected before production impact |
| 5-6 | Implement temp table swap for all ingest patterns | All DELETE-INSERT and TRUNCATE jobs | Zero-downtime data refreshes |
| 6 | Clean up deprecated jobs and consolidate versions | All regions | Lean, maintainable job catalog |

---

## Migration Takeaways

### Naming Convention Priorities

1. **Preserve the naming convention.** The `{Region}_{Subsidiary}_{Layer}_{Description}` pattern is well-understood and self-documenting. Adopt the same structure in the target platform.

2. **Standardize capitalization.** Resolve RAW/Raw inconsistency. Adopt a single convention (lowercase `raw` is the modern standard).

3. **Rename cloned jobs.** Jobs carrying inherited region codes from other regions must be renamed to their correct region prefix before production deployment.

4. **Document exceptions explicitly.** GENERIC and Global intentionally deviate from the standard pattern. Document these as recognized prefixes.

5. **Strip Copy_of_ prefixes and reset version numbers.** The migration is an opportunity to clean up accumulated naming artifacts.

6. **Adopt snake_case for data warehouse objects.** Standardize on snake_case for all dataset and table names, resolving the current inconsistency between camelCase raw tables and PascalCase clean tables.

7. **Establish naming governance.** Formalize the convention in a written standard with automated validation.

### Reusability Migration Priorities

1. **Consolidate the most-duplicated code block into a shared module.** This is the single highest-impact reusability improvement (e.g., converting stats boilerplate into a centralized metrics function eliminating 180+ duplicates).

2. **Convert logging to a reusable utility library.** Enhance with structured logging (JSON format), configurable log levels, and centralized aggregation.

3. **Define shared constants as configuration.** Entity names, currency codes, and country defaults should be externalized into a configuration layer (environment variables, config table, or parameter store). This eliminates duplicated expression patterns and makes future changes a single-point update.

4. **Build parameterized pipeline templates.** Extend the existing GENERIC template pattern to cover Sales_Set builders, raw extraction, and CRM sync job families. A small set of templates (4-5) can cover the majority of production jobs.

5. **Apply DRY principles to sync jobs.** Implement sync logic as configurable modules where region-specific mappings are expressed as data (mapping configuration) rather than code (duplicated expressions).

6. **Establish reuse as a development standard.** No new pipeline may be created by copying an existing one. Common patterns must use parameterized templates. Code review processes should enforce this.

### Preventive and Remediation Priorities

1. **Address critical path risks first.** The five identified critical path risks represent the highest-probability, highest-impact failures. Each has a specific proposed fix with an effort estimate.

2. **Convert silent failures to detected failures.** Adding error handlers to write operations and fixing exception masking in API extraction loops addresses the most dangerous category of latent bugs.

3. **Implement data integrity guards.** Row count validation on TRUNCATE and DELETE-INSERT operations prevents the scenario where upstream failures result in empty target tables that appear to be successful loads.

4. **Build monitoring before migration.** Establish performance baselines, implement data freshness alerting, and build a pipeline health dashboard before migration begins. This provides the forensic foundation needed to validate migrated jobs.

5. **Execute the phased roadmap.** Phase 1 (weeks 1-4) eliminates the most dangerous failure modes with minimal effort. Phase 2 (months 2-3) builds systematic safeguards. Phase 3 (months 4-6) transforms operations from reactive to proactive.

6. **Regenerate this analysis after each phase.** The preventive analysis should be re-run after each remediation phase to track progress and identify newly exposed risks that were previously masked by higher-priority issues.

---

*Generated by intodoc -- documentation automation for integration platforms.*
