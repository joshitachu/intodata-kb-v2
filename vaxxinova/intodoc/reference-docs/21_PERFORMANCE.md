# Performance

Vaxxinova's 257 Talend jobs carry 672 performance risks, and the profile is remarkably uniform: 84% of those risks are a single pattern -- `SELECT *` queries against BigQuery. The platform uses no parallel execution in any region, no configured batch sizes for bulk operations, and relies entirely on default streaming behavior for data movement. This is not necessarily a crisis in the current environment -- Talend's default streaming mode works adequately for the data volumes Vaxxinova processes today -- but it means the platform has no performance headroom. As data volumes grow or SLAs tighten, these unoptimized patterns will become bottlenecks. More importantly, migrating these patterns as-is into a target platform would carry forward avoidable cost and latency.

## The SELECT * Problem

567 of 672 performance risks (84%) are `SELECT *` queries in `tBigQueryInput` components. Rather than selecting only the columns needed for downstream transformation, these queries retrieve every column from the source table. In BigQuery's columnar storage model, this is especially costly: BigQuery charges by bytes scanned, and `SELECT *` forces a full-table scan regardless of how many columns the tMap actually uses.

Three concrete examples:

- `BIO_Clean_Invoice_Set_v5` (BRA) -- `tBigQueryInput_2` uses `SELECT *` to read from the raw invoice table, even though the downstream tMap only maps approximately 30 of the table's 60+ columns.
- `VDD_Clean_Invoice_Item_v1` (DEU) -- `tBigQueryInput_1` reads the entire item table with `SELECT *`, then filters to a subset of fields in the tMap.
- `Copy_of_VJP_Clean_Build_Sales_Set` (JPN) -- `tBigQueryInput_6` uses `SELECT *` on a large GL entries table, where the query could be narrowed to the 15 fields actually consumed.

The pattern is universal. Every region with BigQuery interactions uses `SELECT *` as the default query pattern. This appears to be a development convention -- developers used `SELECT *` during initial development for convenience and never revisited the queries for optimization.

The remaining 105 non-SELECT* risks include oversized jobs (the scratchpad `sandbox` job has 21 components, for instance) and other structural complexity indicators that could affect maintainability and runtime performance.

## BigQuery Cost Implications

The pervasive `SELECT *` pattern has a direct and measurable impact on BigQuery costs. BigQuery's on-demand pricing model charges $6.25 per TB of data scanned (as of 2024 pricing). Because BigQuery uses columnar storage, selecting only the columns you need dramatically reduces bytes scanned -- and therefore cost.

### Estimating the Waste

Consider a typical scenario: a Clean layer job reads from a Raw table with 60 columns but only uses 20 of them in its tMap transformation. With `SELECT *`, BigQuery scans all 60 columns. With explicit column selection, it would scan only 20 columns -- a 67% reduction in bytes scanned.

Across the platform's 567 `SELECT *` instances, the aggregate cost impact depends on table sizes and execution frequency:

- **Conservative estimate** (average table size 1 GB, average column utilization 50%, daily execution): Each `SELECT *` wastes approximately 0.5 GB per execution. Across 567 instances running daily, this amounts to roughly 283 GB of unnecessary scanning per day, or approximately 8.5 TB per month. At $6.25/TB, the monthly waste is approximately $53.
- **Moderate estimate** (average table size 10 GB, average column utilization 40%, daily execution): 567 instances x 6 GB waste each = 3.4 TB per day, or approximately 102 TB per month. Monthly waste: approximately $638.
- **Aggressive estimate** (larger tables, weekly full rebuilds with multiple reads per job): Monthly waste could reach $2,000-5,000 depending on data growth.

The precise cost impact depends on actual table sizes and job frequencies, which are not captured in the Talend metadata. However, the direction is clear: replacing `SELECT *` with explicit column lists will reduce BigQuery costs by 40-70%, with the exact savings proportional to how many columns each job actually uses.

### Cost Beyond Dollars

The `SELECT *` pattern also imposes non-monetary costs:

- **Latency.** Scanning more data takes more time. For time-sensitive consolidation jobs (e.g., `Global_Build_Sales_Set`), unnecessary column scanning adds seconds or minutes to execution time.
- **Slot consumption.** BigQuery allocates processing slots based on query complexity. `SELECT *` queries consume more slots than necessary, potentially causing contention when multiple jobs run concurrently.
- **Memory pressure.** Talend's JVM must hold more data in memory when processing wide rows. For the 102 tJavaRow components that process rows individually, wider rows mean more garbage collection pressure and slower throughput.

## No Parallel Execution

Across all 257 jobs and all 10 regions, zero jobs use Talend's parallel execution capabilities. Every data flow runs as a single-threaded, sequential pipeline. This means:

- Large source tables are read row-by-row through a single connection.
- Multiple independent data flows within a job execute sequentially rather than concurrently.
- There is no partitioned processing for large datasets.

For small-to-medium data volumes, sequential execution is adequate. But for the larger tables in BRA (75 jobs, 250 risks), JPN (36 jobs, 137 risks), and USA (61 jobs, 131 risks), parallel execution could significantly reduce processing time -- especially for the Clean layer jobs that perform complex tMap transformations on large datasets.

## No Batch Size Configuration

No jobs in the platform configure explicit batch sizes for BigQuery output operations. All `tBigQueryOutput` components use Talend's default streaming insert behavior, which sends records individually or in small implicit batches. For large datasets, this is significantly slower and more expensive than batch loading through Google Cloud Storage staging.

The platform does use GCS staging for some data flows (the `tPostjob` + `cleanup_temp_file_storage` pattern cleans up staging files), but the batch size and staging configuration appear to use defaults throughout.

## Performance Hotspots by Region

Each region's performance profile is shaped by its pipeline complexity, data volumes, and the number of external system connections. The following analysis identifies the specific performance hotspots in each region.

### BRA (75 jobs, 250 risks -- highest absolute risk count)

Brazil is the performance pain point of the platform. Its 250 performance risks average 3.3 per job, with approximately 210 `SELECT *` instances. The highest-risk jobs are the Clean layer consolidation jobs -- `VBR_Clean_Build_Sales_Set` and `BIO_Clean_Invoice_Set_v5` -- which read from multiple large raw tables and join them in complex tMap transformations. These jobs contain 6-10 `tBigQueryInput` components each, every one using `SELECT *`.

BRA's dual-subsidiary architecture (VBR and BioVet) means that some consolidation jobs read from both subsidiaries' raw datasets, doubling the scan volume. The `VBR_Clean_Ingest_Sales_Set_DEBUG` job is a particularly flagrant example: it contains a `tBigQueryInput_10` component (the highest-numbered BigQuery input seen in any BRA job), suggesting a single job that reads from 10 or more BigQuery tables.

Optimization priority for BRA: Replace `SELECT *` in the 5-10 largest consolidation jobs first. These represent the highest-volume scans and will deliver the most cost and latency reduction per job optimized.

### JPN (36 jobs, 137 risks -- highest risk density)

Japan has the highest risk density at 3.8 risks per job, driven by its complex multi-source GL entry processing. The `Copy_of_VJP_Clean_Build_Sales_Set` job is the primary hotspot: it joins four separate data streams (GL entries, sales invoices, cost of goods sold, inventory) in a single job, with each stream read via `SELECT *` from a large BigQuery table. The `tBigQueryInput_6` component in this job reads the GL entries table -- one of the largest tables in the JPN dataset -- without column filtering.

JPN's risk density is also elevated because many of its jobs are complex: single jobs with multiple BigQuery interactions rather than simple one-table extraction jobs. This architectural choice concentrates performance risk in fewer but more complex jobs.

Optimization priority for JPN: Focus on the `Clean_Build_Sales_Set` job, which is the single largest contributor to JPN's risk count. Replacing its 4-6 `SELECT *` queries with explicit column lists would eliminate approximately 20-30% of JPN's total performance risks.

### USA (61 jobs, 131 risks)

USA's 131 risks are spread across 61 jobs, averaging 2.1 risks per job -- the lowest density among the three major regions. This reflects USA's more granular job architecture: rather than consolidating multiple data flows into single complex jobs (as JPN does), USA tends to use separate jobs for each extraction and transformation step. The Salesforce synchronization jobs (`*_Salesforce`) contribute a meaningful share of risks because they read from BigQuery clean tables (using `SELECT *`) to prepare data for Salesforce pushes.

The `VUS_Clean_BC_SalesInvoices_v15` job is a notable hotspot: at version 15, it has been refined 14 times without anyone replacing the `SELECT *` queries that have persisted since v1. This is a microcosm of the platform's general pattern -- functional improvements are made to business logic while performance patterns remain untouched.

Optimization priority for USA: Address the Salesforce sync jobs first, since they both read from BigQuery (incurring scan costs) and write to an external API (where slower BigQuery reads cascade into longer Salesforce sync windows).

### Global (43 jobs, 102 risks)

Global's 102 risks are concentrated in the `Build_Sales_Set` consolidation jobs, which read from multiple regional clean datasets to produce the enterprise-wide sales view. These jobs scan the largest tables in the platform because they aggregate data from all regions. The `SELECT *` pattern is especially wasteful here: the consolidated Sales Set uses a defined schema, and each regional input table contains many columns that are not needed in the consolidated output.

Optimization priority for Global: Highest. Global's consolidation jobs scan the most data (aggregating from all regions) and benefit the most from column pruning.

### Smaller Regions (DEU, ITA, ZAF, GENERIC, scratchpad, SAF)

DEU (31 risks), ITA (15 risks), ZAF (4 risks), GENERIC (1 risk), scratchpad (1 risk), and SAF (0 risks) collectively account for only 8% of total performance risks. Their small job portfolios and simpler pipelines mean that performance optimization in these regions is low priority. However, the same `SELECT *` pattern exists in DEU and ITA and should be addressed during migration for consistency even if the cost impact is minimal.

GENERIC's near-zero risk count (1 risk) reflects its REST API-based extraction architecture: GENERIC jobs pull data from Business Central APIs rather than querying BigQuery, so the `SELECT *` pattern does not apply.

## Region Comparison

| Region | Jobs | Performance Risks | Risks per Job | Parallelism | SELECT * Count |
|--------|------|-------------------|---------------|-------------|----------------|
| BRA | 75 | 250 | 3.3 | None | ~210 |
| USA | 61 | 131 | 2.1 | None | ~110 |
| JPN | 36 | 137 | 3.8 | None | ~115 |
| Global | 43 | 102 | 2.4 | None | ~86 |
| DEU | 12 | 31 | 2.6 | None | ~26 |
| GENERIC | 15 | 1 | 0.1 | None | ~0 |
| ITA | 6 | 15 | 2.5 | None | ~13 |
| ZAF | 3 | 4 | 1.3 | None | ~3 |
| scratchpad | 5 | 1 | 0.2 | None | ~0 |
| SAF | 1 | 0 | 0.0 | None | 0 |

JPN has the highest risk density at 3.8 risks per job, driven by its complex multi-source GL entry processing (four separate data streams joined in a single job). BRA has the highest absolute risk count (250) due to its large job portfolio. GENERIC and scratchpad have near-zero risks because GENERIC jobs use REST API extraction (not BigQuery queries) and scratchpad contains only test jobs.

SAF has zero performance risks, reflecting its status as a single-job pipeline (a clone of the Italian extraction) that has not yet been expanded.

## Optimization Priority Matrix

The following matrix ranks performance improvements by implementation effort versus expected impact, providing a roadmap for the migration team.

### Quick Wins (Low Effort, High Impact)

| Improvement | Effort | Impact | Affected Jobs |
|------------|--------|--------|---------------|
| Replace `SELECT *` in Global consolidation jobs | 2-3 days | Highest scan reduction (largest tables) | ~10 jobs |
| Replace `SELECT *` in BRA top-5 consolidation jobs | 2-3 days | ~30% of BRA scan volume | 5 jobs |
| Replace `SELECT *` in JPN `Build_Sales_Set` | 1 day | ~25% of JPN scan volume | 1-2 jobs |

### Medium Effort, High Impact

| Improvement | Effort | Impact | Affected Jobs |
|------------|--------|--------|---------------|
| Replace all remaining `SELECT *` (567 instances) | 2-3 weeks | 40-70% BigQuery cost reduction | 257 jobs |
| Configure batch loading via GCS staging | 1-2 weeks | Faster bulk writes, lower streaming costs | ~175 output jobs |
| Implement partition pruning for date-filtered queries | 1-2 weeks | Major scan reduction for time-series tables | ~50 jobs |

### High Effort, Medium Impact

| Improvement | Effort | Impact | Affected Jobs |
|------------|--------|--------|---------------|
| Implement parallel execution for independent data flows | 3-4 weeks | Faster complex jobs (BRA, JPN) | ~30 complex jobs |
| Materialized staging for checkpoint/retry | 2-3 weeks | Improved reliability, reduced reprocessing | ~20 large jobs |

### Low Priority (Low Impact)

| Improvement | Effort | Impact | Affected Jobs |
|------------|--------|--------|---------------|
| Optimize scratchpad/test jobs | N/A | None (do not migrate) | 5 jobs |
| Optimize SAF single-job pipeline | Minimal | Negligible | 1 job |

## Monitoring and Observability

The current platform has almost no performance monitoring infrastructure. The `tJava_StoreJobStats` boilerplate captures row counts and timestamps, but it does not record query execution times, bytes scanned, BigQuery slot utilization, or memory consumption. This means the migration team has no baseline performance data to compare against -- a critical gap that must be addressed before migration begins.

### What Should Be Tracked

The target platform should implement comprehensive performance monitoring across four dimensions:

**Job Execution Metrics:**
- Total job duration (wall clock time from start to finish)
- Per-component execution time (how long each tMap, tBigQueryInput, tBigQueryOutput takes)
- Row counts at each stage (input rows, output rows, rejected rows, filtered rows)
- Memory consumption (JVM heap usage, garbage collection frequency)

**BigQuery Query Metrics:**
- Bytes scanned per query (the primary cost driver)
- Bytes billed per query (after BigQuery's minimum billing thresholds)
- Slot-milliseconds consumed per query (processing resources used)
- Query cache hit rate (how often BigQuery serves results from cache rather than re-scanning)
- Partition pruning effectiveness (bytes skipped due to partition filters)

**Pipeline Health Metrics:**
- Job success/failure rate (per region, per layer, per time period)
- Job duration trends (is the same job getting slower over time as data grows?)
- SLA adherence (did the job complete within its expected window?)
- Retry counts (how often does a job fail and need to be rerun?)

**Cost Metrics:**
- BigQuery cost per job (bytes scanned x price per TB)
- BigQuery cost per region (aggregate daily/monthly spend by region)
- Cost trend analysis (is spending growing faster than data volume, suggesting efficiency degradation?)
- Cost allocation by layer (how much does Raw extraction cost vs. Clean transformation vs. Global consolidation?)

### Establishing Baselines Before Migration

Before migration begins, the team should instrument the current Talend jobs to establish performance baselines. This can be done by:

1. Enabling BigQuery query logging in the GCP project to capture bytes scanned and execution time for every query.
2. Adding timestamps to the existing `tJava_StoreJobStats` boilerplate to capture per-component timing (not just per-job timing).
3. Running each production job at least 3-5 times with instrumentation to establish a representative baseline.
4. Documenting the baseline metrics in a performance reference document that the migration team can use for validation.

Without these baselines, there is no way to verify that migrated jobs perform equivalently or better than their Talend originals.

## What Good Looks Like

For context, a well-optimized BigQuery ETL pipeline would exhibit these characteristics:

- **Explicit column selection**: Queries specify only the columns consumed by downstream transformations, reducing bytes scanned and BigQuery costs.
- **Batch loading via GCS**: Large datasets are written to GCS as Avro or Parquet files, then loaded to BigQuery via `LOAD` jobs rather than streaming inserts.
- **Parallel execution**: Independent data flows run concurrently. Large tables are partitioned and processed in parallel threads.
- **Partition pruning**: Queries filter on BigQuery partition columns (typically date fields) to limit scan scope.
- **Materialized staging**: Intermediate results are materialized as temporary BigQuery tables rather than passed through memory, enabling checkpointing and retry.

The current Vaxxinova platform uses none of these patterns.

## Migration Takeaways

**Replace SELECT * with explicit column lists.** This is the single highest-impact performance improvement. For each of the 567 `SELECT *` instances, the migration team should identify which columns are actually consumed by the downstream tMap and rewrite the query accordingly. In BigQuery, this directly reduces cost and latency. In most target platforms, explicit column selection also improves query planning and memory usage.

**Evaluate parallel execution opportunities.** The target platform should support concurrent execution of independent data flows. The BRA, JPN, and USA regions -- which contain jobs with multiple independent source reads feeding into a single tMap -- are the primary candidates for parallelization.

**Configure appropriate batch sizes.** Bulk operations should use explicit batch sizing tuned to the target platform. For BigQuery specifically, this means using batch `LOAD` jobs via GCS staging for datasets larger than a few thousand rows, reserving streaming inserts only for near-real-time requirements (which Vaxxinova does not have -- all jobs are batch ETL).

**Implement partition strategies.** BigQuery tables that are queried with date filters (fiscal period, posting date) should be partitioned by those columns. Queries should include partition filters to enable pruning. This is especially relevant for the large Sales_Set and Invoice_Set tables that are the primary targets of `SELECT *` queries today.

**Establish performance baselines before migration.** Since the current platform has no performance monitoring beyond the `tJava_StoreJobStats` row counts, the migration team should instrument the current jobs to establish runtime baselines. Without these baselines, it will be impossible to validate that the migrated jobs perform equivalently or better.

**Implement performance monitoring from day one in the target platform.** The target platform should track job duration, bytes scanned, cost per query, and pipeline health metrics from the first day of operation. Do not repeat the current platform's mistake of operating without performance visibility.
