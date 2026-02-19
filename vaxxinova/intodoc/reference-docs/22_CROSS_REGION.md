# Cross-Region Analysis

Vaxxinova's Talend integration platform spans 10 organizational regions, each representing a subsidiary, a shared utility layer, or a cross-regional consolidation function. Together these regions contain 257 jobs that move sales, product, customer, and financial data between on-premises ERP systems, Google BigQuery, and Salesforce CRM. The regions vary enormously in scope: Brazil alone accounts for 75 jobs with complex multi-source extraction, while South Africa operates a single job pulling from one MSSQL instance. Understanding these differences -- and, more importantly, the patterns they share -- is essential for planning a migration that avoids duplicated work without overlooking region-specific edge cases.

---

## Region Comparison Matrix

| Region | Jobs | % of Total | Key Components | Distinguishing Characteristics |
|--------|------|-----------|----------------|-------------------------------|
| BRA | 75 | 29% | tBigQueryInput, tBigQueryOutput, tHashInput, tHashOutput, tMap | Dual-source (legacy MSSQL + Business Central); Biovet/VBR subsidiaries; largest component footprint |
| USA | 61 | 24% | tBigQueryInput, tBigQueryOutput, tFixedFlowInput, tHashOutput, tMap | Two subsidiaries (NPL + VUS); on-prem MSSQL + Salesforce; FTP/SFTP integration unique to this region |
| Global | 43 | 17% | Vaxxinova_Logging, cleanup_temp_file_storage, tBigQueryInput, tMap, tPostjob | Cross-region consolidation; budget/forecast/prior-year pipelines; the only region with dependency chains |
| JPN | 36 | 14% | tBigQueryInput, tBigQueryOutput, tHashOutput, tMap, tUnite | VJP subsidiary; uses tUnite for merging invoice/credit memo streams; MSSQL on-prem source |
| GENERIC | 15 | 6% | Vaxxinova_Logging, tExtractJSONFields, tFileInputDelimited, tMap, tPrejob | Shared Business Central API extractors; parameterized templates consumed by other regions |
| DEU | 12 | 5% | Vaxxinova_Logging, cleanup_temp_file_storage, tBigQueryInput, tMap, tPostjob | Dual-entity (VDA + VDD) reflecting Business Central migration from legacy ERP |
| ITA | 6 | 2% | cleanup_temp_file_storage, tBigQueryInput, tBigQueryOutput, tMap, tPostjob | VIT subsidiary; clean-layer sales processing plus Salesforce product sync (IZO prefix) |
| scratchpad | 5 | 2% | tBigQueryOutput, tFileInputJSON, tFileOutputDelimited, tLogRow, tMap | Development/test workspace; only region with tDie error component; not production-critical |
| ZAF | 3 | 1% | cleanup_temp_file_storage, tBigQueryInput, tBigQueryOutput, tMap, tPostjob | Minimal South Africa footprint; shares MSSQL connection with ITA (connection_ITA_VAX_MSSQL) |
| SAF | 1 | <1% | Vaxxinova_Logging, cleanup_temp_file_storage, tMSSqlInput, tMap, tPostjob | Single extraction job; only region using tMSSqlInput directly; VSA subsidiary |

The four largest regions -- BRA, USA, Global, and JPN -- account for 84% of all jobs (215 of 257). Any migration effort that covers these four regions first will address the vast majority of pipeline logic.

---

## Shared Components vs. Region-Specific Patterns

Seven components appear across multiple regions, forming the common vocabulary of the platform. But not all of them are truly universal.

### Universally Shared

**tMap** is the only component present in every single region. It serves as the core transformation engine, handling column mapping, joins, filtering, and expression evaluation in every pipeline stage. Any migration strategy must have a robust tMap translation pattern as its foundation.

**tBigQueryInput** appears in 7 of 10 regions (absent from GENERIC, SAF, and scratchpad). Since BigQuery is the central data warehouse, this component represents the dominant read pattern. GENERIC does not need it because its jobs extract from REST APIs and files, not BigQuery. SAF reads from MSSQL directly via tMSSqlInput.

**tBigQueryOutput** spans 6 regions (BRA, ITA, JPN, USA, ZAF, scratchpad). Together with tBigQueryInput, these two components define the platform's primary I/O pattern: read from BigQuery, transform, write back to BigQuery.

### Operationally Shared

Two components reflect the platform's operational scaffolding rather than its data logic:

- **cleanup_temp_file_storage**: Used in DEU, Global, ITA, SAF, and ZAF. This is a joblet that runs in tPostjob phases to remove temporary CSV files written during staging. Its absence from BRA, JPN, and USA -- the three largest regional pipelines -- is notable and explains why those regions accumulate 85, 35, and 44 temp files respectively without guaranteed cleanup.

- **Vaxxinova_Logging**: Present in DEU, GENERIC, Global, and SAF. This centralized logging component provides execution tracing. Its absence from BRA (75 jobs), USA (61 jobs), and JPN (36 jobs) means that 67% of the platform's jobs have no structured logging. Combined with the 89% rate of missing error handling, this creates significant observability gaps in the largest pipelines.

### Data-Processing Shared

- **tHashOutput**: Used in BRA, JPN, and USA. This in-memory lookup component is characteristic of the high-volume regions where jobs need to join large datasets without repeated BigQuery round-trips. It typically holds reference data (fiscal periods, country master, currency rates) that enriches transactional records.

- **tPostjob**: Found in DEU, Global, ITA, SAF, and ZAF. Runs post-processing logic (cleanup, logging) after the main job flow completes.

### Region-Specific Components

Several components are unique to individual regions, reflecting distinct source system integrations:

- **tMSSqlInput** (SAF only): Direct MSSQL extraction. Other MSSQL-connected regions (BRA, JPN, USA) use connection-based approaches via tDBInput or tMap SQL queries rather than the dedicated MSSQL component.

- **tFixedFlowInput** (USA only): Generates fixed reference rows. Used in NPL and VUS jobs for creating lookup constants that do not come from any source system.

- **tUnite** (JPN only): Merges multiple data streams (invoice lines + credit memo lines) into a single flow before downstream transformation. Other regions accomplish similar merges within tMap joins, but Japan's pipeline structure uses explicit union operations.

- **tExtractJSONFields** and **tFileInputDelimited** (GENERIC only): These reflect the API-extraction nature of the shared Business Central jobs, which parse JSON responses from REST calls and read delimited staging files.

- **tFileInputJSON**, **tFileOutputDelimited**, **tLogRow** (scratchpad only): Development-oriented components for ad-hoc testing and data inspection.

---

## Size Disparity: The Long Tail of Regions

The platform exhibits a pronounced long-tail distribution. BRA's 75 jobs contain more pipeline logic than DEU, GENERIC, ITA, SAF, ZAF, and scratchpad combined (42 jobs total). This concentration has practical consequences.

### The "Big Four" Regions

BRA and USA together hold 136 jobs (53% of the platform) and share a similar architectural pattern: on-premises MSSQL extraction, BigQuery staging, clean-layer transformation with fiscal period and Salesforce enrichment, and Sales_Set output. Both regions also use tHashOutput for in-memory lookups and lack both cleanup_temp_file_storage and Vaxxinova_Logging. From a migration standpoint, a single well-tested template covering the MSSQL-to-BigQuery-to-Sales_Set flow would address the majority of jobs in both regions.

JPN (36 jobs) follows a parallel pattern but introduces tUnite for stream merging, and Global (43 jobs) sits atop the regional outputs, consolidating them into budget, forecast, and prior-year analytics. Global is the only region with dependency chains (max depth 1), where `Global_Clean_Sales_Set` depends on regional Sales_Set outputs being current.

### The Minimal Regions

SAF (1 job) and ZAF (3 jobs) represent South African operations. Despite the geographic overlap, they use different MSSQL connections: SAF uses `connection_SAF_VSA_MSSQL` while ZAF uses `connection_ITA_VAX_MSSQL`, indicating that ZAF's data actually routes through Italy's on-premises infrastructure. Both regions include cleanup_temp_file_storage and tPostjob, suggesting they were built later and benefited from operational best practices that the older, larger regions did not adopt.

ITA (6 jobs) occupies a middle ground, with a complete pipeline from raw extraction through Salesforce sync, but at a scale small enough to migrate in a single sprint.

### The SAF/ZAF Consolidation Recommendation

The coexistence of two regional designations for South Africa -- SAF and ZAF -- is the clearest example of organizational debt in the platform's regional structure. The two regions represent the same geographic subsidiary (Vaxxinova South Africa / VSA) at different stages of pipeline development, and their continued separation creates confusion in naming conventions, connection management, and regional reporting.

**SAF** contains a single job (`VSA_Raw_InvoiceDetails`) that extracts invoice detail data from the South African MSSQL server. This job uses `tMSSqlInput` directly -- the only job on the entire platform that uses this component -- rather than referencing a named connection. It includes both `Vaxxinova_Logging` and `cleanup_temp_file_storage`, indicating it was built with awareness of the platform's operational best practices. The SAF region was likely created as a quick initial pipeline for South Africa, extracting a single entity from the local ERP.

**ZAF** contains three jobs that handle raw extraction, clean-layer transformation, and BigQuery output for a broader set of South African data. ZAF's jobs reference `connection_ITA_VAX_MSSQL` -- Italy's MSSQL connection -- rather than `connection_SAF_VSA_MSSQL`. This cross-wiring suggests one of two scenarios: either (a) South Africa's ERP data is physically hosted on Italy's server infrastructure (perhaps through a shared hosting arrangement), or (b) ZAF's pipeline was cloned from Italy's and the connection reference was never updated to point to the correct South African server. Either scenario creates operational risk: a change to Italy's server configuration could break South Africa's pipeline, and the naming inconsistency makes troubleshooting more difficult.

**Consolidation path.** During migration, SAF and ZAF should be merged into a single `ZAF` pipeline that: (a) uses a dedicated South Africa connection (resolving the ITA connection dependency), (b) incorporates the broader entity coverage from ZAF's 3 jobs, (c) retains the operational best practices from SAF (logging and cleanup), and (d) adds the error handling that both regions currently lack. The merged pipeline would contain approximately 4-5 jobs (raw extraction, clean transformation, Sales_Set construction, and optional Salesforce sync), making it a manageable single-sprint migration target. The SAF regional designation should be deprecated and removed from the platform's organizational structure.

**Connection resolution.** As part of the consolidation, the migration team must determine the correct MSSQL server for South African data. If `connection_SAF_VSA_MSSQL` points to the actual South African server and `connection_ITA_VAX_MSSQL` was used by ZAF only because it was cloned from Italy, then the consolidated pipeline should use the SAF connection. If South Africa's data genuinely resides on Italy's server, that hosting arrangement should be documented explicitly and the connection renamed to reflect the cross-region dependency (e.g., `connection_ZAF_VSA_MSSQL_via_ITA`).

### Template Reuse Opportunities Across Regions

One of the most impactful findings from the cross-region analysis is the degree of structural similarity across regional pipelines. While each region has unique source system connections and entity-specific field mappings, the underlying data flow patterns are remarkably consistent. This consistency creates significant template reuse opportunities during migration.

**The Sales_Set Builder Template.** Every region that contributes to the global Sales_Set follows the same high-level pattern: read from raw BigQuery tables, enrich with fiscal periods and Salesforce mappings, apply currency conversion, tag with entity and region identifiers, and write to both a regional clean dataset and the Global_Clean dataset. The specific input tables, currency codes, and entity names vary, but the transformation structure is identical. A single parameterized Sales_Set builder template in the target platform could replace the current approach of maintaining separate, manually copied job variants per region. The template would accept parameters for: raw dataset name, entity code, default currency, Salesforce namespace prefix, and target clean dataset.

The current platform partially achieves this through context variable parameterization (e.g., `context.parameter_BRA_Raw_Env`), but only in some jobs -- others hardcode these values. Migration is an opportunity to enforce full parameterization across all regions.

**The Raw Extraction Template.** The GENERIC region's `GEN_BC_*` family of jobs already demonstrates a template-based approach for Business Central API extraction. These templates handle OAuth authentication, API pagination, JSON parsing, and BigQuery loading. The template pattern should be extended to cover MSSQL extraction as well. A generic MSSQL raw extraction template would accept parameters for: connection name, source view or table, target BigQuery dataset, and column mapping. BRA, JPN, ITA, and ZAF all use similar MSSQL extraction patterns that could share a single template.

**The Salesforce Sync Template.** Three regions (BRA, ITA, USA) synchronize product master data to Salesforce using nearly identical logic: read product data from BigQuery, map fields to Salesforce's Product2 object, apply a region-specific namespace prefix (e.g., `BIO-` for Biovet, `IZO-` for Italy), and perform an upsert operation. A parameterized Salesforce sync template would reduce the current three separate job implementations to a single reusable component, with region-specific configuration passed as parameters.

**The Delete-Then-Insert Template.** The idempotent reload pattern (DELETE matching records, then INSERT new ones) appears in dozens of jobs across all regions. Rather than implementing this pattern individually in each migrated job, the target platform should provide a reusable utility that accepts a target table, a match predicate (e.g., WHERE delta_id IN (...)), and a data source, and handles the delete-insert sequence atomically. Better yet, the migration should replace this pattern with MERGE/UPSERT operations where BigQuery supports them, eliminating the data unavailability window entirely.

**Estimated template coverage.** Based on the pattern analysis, four templates (Sales_Set builder, raw extractor, Salesforce sync, idempotent loader) would cover approximately 70-75% of all production jobs. The remaining 25-30% are jobs with unique logic (budget/forecast processing, Animalytix SFTP ingestion, GL entry extraction) that require bespoke implementations. This template-first approach would dramatically reduce migration effort and ensure consistency across regions.

---

## Global Orchestration: How Regional Data Consolidates

The Global region (43 jobs) is the platform's analytical capstone. It does not extract from source systems itself. Instead, it reads the outputs of regional clean-layer jobs from BigQuery and builds consolidated datasets.

The orchestration pattern works as follows:

1. **Regional jobs** run independently (all 257 are marked on-demand, with scheduling managed externally through TAC or cron). Each regional pipeline produces a regional `Sales_Set` table (e.g., `bra_vbr_clean.Sales_Set`, `jpn_vjp_clean.Sales_Set`).

2. **Global_Build_Sales_Set** reads from regional clean datasets, applies fiscal period mappings, country enrichment, and Salesforce account linkages, then produces the unified `Global_Clean.Sales_Set`.

3. **Budget and forecast jobs** (Budget_Build_Sales_Set, Forecast_Sales_Set, Prior_Build_Sales_Set) process financial planning data from the `Global_Budget` dataset and merge it with actuals.

4. **Legacy compatibility jobs** (Legacy_Ingest_Sales_Set and related) maintain backward-compatible transformations for older reporting tools.

`Global_Clean_Sales_Set` is the single identified bottleneck job in the platform. If any regional Sales_Set is stale or incomplete when the Global job runs, the consolidated output will contain gaps. Since there are no explicit dependency checks or data freshness validations, the current approach relies on implicit timing: regional jobs are assumed to have completed before the Global consolidation runs.

### Migration Takeaway

The Global layer's dependency on regional outputs must become explicit in any target platform. The current implicit coordination works only because an operator knows the correct run order. A migrated system should implement dependency-based orchestration where Global jobs trigger only after all upstream regional jobs have successfully completed.

---

## Regional Maturity Assessment

Beyond job counts and component inventories, the regions differ substantially in pipeline maturity -- defined as the degree to which a region follows engineering best practices for logging, cleanup, parameterization, error handling, and modular design. This assessment synthesizes findings from across the documentation to assign each region a qualitative maturity rating.

| Region | Logging | Cleanup | Parameterization | Error Handling | Modular Design | Overall Maturity |
|--------|---------|---------|------------------|----------------|----------------|------------------|
| GENERIC | Yes | No | Strong | 33% adequate | Template-based | High |
| DEU | Yes | Yes | Strong | 17% adequate | Standard | Medium-High |
| Global | Yes | Yes | Standard | 5% adequate | Orchestrated | Medium |
| ITA | No | Yes | Standard | 0% | Standard | Medium |
| ZAF | No | Yes | Standard | 0% | Standard | Medium |
| SAF | Yes | Yes | Standard | 0% | Minimal | Medium |
| BRA | No | No | Mixed | 13% adequate | Copy-heavy | Low |
| USA | No | No | Mixed | 11% adequate | Copy-heavy | Low |
| JPN | No | No | Standard | 0% | Monolithic | Low |
| scratchpad | No | No | N/A | 40% adequate | Experimental | N/A |

The maturity assessment reveals a counterintuitive pattern: the smallest regions tend to be the most mature. SAF (1 job), ZAF (3 jobs), and DEU (12 jobs) all include logging and cleanup, while the three largest production regions (BRA, USA, JPN) lack both. This likely reflects a chronological effect: the smaller regions were built later, when the development team had more experience and had adopted the `cleanup_temp_file_storage` and `Vaxxinova_Logging` patterns. The larger regions were built earlier, grew organically through copy-based development, and were never retroactively upgraded to include operational best practices.

## Component Overlap Analysis

To quantify reuse, the 7 shared components can be grouped by how broadly they are deployed:

| Reach | Component | Regions Using | Coverage |
|-------|-----------|---------------|----------|
| Universal | tMap | 10 of 10 | 100% |
| Near-universal | tBigQueryInput | 7 of 10 | 70% |
| Broad | tBigQueryOutput | 6 of 10 | 60% |
| Moderate | cleanup_temp_file_storage | 5 of 10 | 50% |
| Moderate | tPostjob | 5 of 10 | 50% |
| Limited | Vaxxinova_Logging | 4 of 10 | 40% |
| Limited | tHashOutput | 3 of 10 | 30% |

The practical implication: a migration team that builds robust translations for tMap, tBigQueryInput, and tBigQueryOutput will have a reusable foundation covering every region. The cleanup_temp_file_storage and Vaxxinova_Logging patterns should be standardized across all migrated jobs -- not just the regions that currently use them -- to close the operational gaps in BRA, USA, and JPN.

Region-specific components (tMSSqlInput, tFixedFlowInput, tUnite, tExtractJSONFields) each affect at most one or two regions and can be handled as targeted extensions to the common migration template.

---

## Migration Takeaways

### Prioritize by Impact

Migration should follow the regions in order of job count and business criticality:

1. **BRA (75 jobs)** and **USA (61 jobs)**: Together 53% of the platform. Both follow the same MSSQL + BigQuery + Sales_Set pattern. Build and validate the migration template here.
2. **Global (43 jobs)**: Depends on regional outputs. Migrate after BRA and USA to enable end-to-end testing of the consolidation layer.
3. **JPN (36 jobs)**: Structurally similar to BRA/USA but needs tUnite handling. Migrate third.
4. **GENERIC (15 jobs)**: Shared templates. Must be migrated before any region that depends on them for Business Central extraction.
5. **DEU (12 jobs)**: Dual-entity complexity (VDA + VDD) but small enough for a single sprint.
6. **ITA (6 jobs), scratchpad (5 jobs), ZAF (3 jobs), SAF (1 job)**: Low volume, migrate last. SAF and ZAF can serve as early proof-of-concept targets due to their small size and inclusion of operational best practices (logging, cleanup).

### Shared Patterns Enable Common Templates

The dominance of tMap + tBigQueryInput + tBigQueryOutput across the platform means a single well-tested transformation template will cover the core logic of most jobs. Region-specific extensions (MSSQL connections, API extraction, Salesforce sync) can be layered on top of this common base.

### Operational Gaps Must Be Closed During Migration

The three largest regions (BRA, USA, JPN -- 172 jobs, 67% of the platform) lack both Vaxxinova_Logging and cleanup_temp_file_storage. Rather than replicating this gap, the migration should mandate centralized logging, error handling, and temp file management for every migrated job regardless of whether the original Talend job included them.

### MSSQL Connections Require Per-Region Testing

Five distinct MSSQL connections exist, each pointing to a different on-premises server. Network connectivity, firewall rules, and credentials must be validated independently for each region in the target architecture. The shared connection between ITA and ZAF (`connection_ITA_VAX_MSSQL`) is a cross-regional dependency that must be documented and tested carefully.

### Explicit Orchestration Replaces Implicit Timing

All 257 jobs are on-demand with no scheduling metadata in Talend. The external scheduler (TAC, cron, or similar) must be documented before migration so that the target platform can implement equivalent -- or improved -- dependency-based orchestration. The Global consolidation layer in particular needs explicit upstream completion gates.

### Standardize Error Handling and Logging Across All Regions

The cross-region analysis reveals a striking inconsistency in operational instrumentation. Vaxxinova_Logging is present in only 4 of 10 regions (DEU, GENERIC, Global, SAF), and cleanup_temp_file_storage appears in only 5 of 10 (DEU, Global, ITA, SAF, ZAF). The three largest regions -- BRA, USA, and JPN, representing 67% of all jobs -- have neither. The migration must not replicate this inconsistency. Every migrated job, regardless of region, should include standardized logging (capturing job start, end, duration, row counts, and error status) and standardized cleanup (removing temporary files and staging tables after each run). This is not an enhancement; it is the correction of a foundational gap.

### Migration Sequencing Across Regions

The order in which regions are migrated matters because of cross-region data dependencies. The recommended migration sequence accounts for both region size and dependency structure:

**Phase 1 (Proof of Concept): SAF + ZAF.** These 4 jobs are the smallest production pipeline and include operational best practices (logging, cleanup). Migrating them first provides a low-risk proof of concept that validates the target platform's BigQuery connectivity, MSSQL extraction capability, and operational scaffolding. The SAF/ZAF consolidation (merging two regions into one) adds a modest design task that exercises the migration team's architectural decision-making.

**Phase 2 (Core Template): GENERIC.** The 15 shared Business Central extraction templates must be migrated before any region that depends on them. This phase validates the target platform's REST API extraction, OAuth token management, and JSON parsing capabilities. It also establishes the reusable template pattern that subsequent phases will build upon.

**Phase 3 (First Major Region): BRA.** With 75 jobs, Brazil is the largest and most complex region. It exercises every source type (MSSQL, REST API, Salesforce), every layer (raw, clean, global), and the platform's most complex transformation logic. A successful BRA migration validates the target platform's readiness for the full portfolio. Brazil's dual-ERP landscape (legacy MSSQL + Business Central) makes it the definitive stress test.

**Phase 4 (Second Major Region): USA.** The 61 USA jobs share a similar architecture to BRA, so migration patterns developed in Phase 3 can be reused. The unique Animalytix SFTP integration adds one new capability to validate.

**Phase 5 (Third Major Region): JPN.** Japan's 36 jobs add the tUnite stream-merging pattern and the monolithic raw extraction job. The high component density (24.2 per job) means Japan's clean-layer transformations are computationally intensive and should be tested for performance in the target platform.

**Phase 6 (Smaller Regions): DEU + ITA.** Germany's 12 jobs and Italy's 6 jobs are small enough to migrate together in a single sprint. Both depend on GENERIC templates (already migrated in Phase 2) and feed into the Global layer.

**Phase 7 (Consolidation Layer): Global.** The 43 Global jobs should be migrated last because they depend on all regional outputs. This phase includes rebuilding the `Global_Clean_Sales_Set` orchestrator with explicit dependency gates, migrating the budget and forecast pipelines, and validating end-to-end data flow from source systems through regional pipelines to global consolidation.

**Phase 8 (Cleanup): scratchpad retirement.** The 5 scratchpad jobs should not be migrated. They are development artifacts with no production value. The one "good" error handling job in scratchpad has already served its purpose as a reference pattern -- the error handling pattern it demonstrates should be incorporated into every migrated job, making the scratchpad version redundant.
