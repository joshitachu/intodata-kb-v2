# State and Side Effects

Beyond their primary data output -- rows written to BigQuery tables, records upserted into Salesforce -- Vaxxinova's Talend jobs produce a range of side effects during execution. These include temporary files written to local disk, staging files deposited in shared directories, and intermediate database writes used to coordinate multi-step pipelines. Across the platform, **555 side effects** have been identified across 10 regions. The majority are manageable, but **119 effects (21%) lack cleanup logic**, meaning they accumulate silently on execution servers over time. Understanding these side effects is critical for migration planning, because any target platform must either replicate the staging patterns or replace them with cloud-native alternatives that handle lifecycle management automatically.

This analysis was performed through static code inspection of all 257 Talend jobs, identifying every file write, staging output, and intermediate storage operation. Each effect was then cross-referenced with the job's `tPostjob` phase to determine whether cleanup logic was present.

---

## Effect Types and Distribution

The 555 identified side effects fall into two categories:

| Effect Type | Count | Share |
|-------------|-------|-------|
| Staging file | 364 | 66% |
| Temp file | 191 | 34% |

**Staging files** are CSV files written to a shared directory (typically `context.resource_flow_temp_folder`) that serve as intermediate outputs between job steps. A job extracts data from BigQuery or an API, writes it to a CSV staging file, and a subsequent job or step reads that file for further processing. These files are the primary data exchange mechanism between loosely coupled job steps in the Talend platform.

The staging file pattern is deeply embedded in the platform's architecture. The Sales_Set construction pipeline, for example, writes intermediate CSV files at multiple points: after extracting raw invoice data, after applying fiscal period enrichment, after joining with Salesforce account mappings, and before the final BigQuery load. Each of these intermediate outputs is a staging file that persists on disk until explicitly cleaned up.

**Temp files** are transient artifacts created during job execution -- partial outputs, working copies, serialization buffers, or hash table spillover files. Unlike staging files, temp files are not intended to be consumed by other jobs. They exist only to support in-flight processing and should be deleted when the job completes. Talend creates temp files automatically when certain components (tSort, tAggregate, tHash) exceed their in-memory buffer limits and spill data to disk.

The prevalence of file-based staging (66% of all effects) reflects Talend's architecture, which favors disk-based data exchange over in-memory passing for large datasets. This is a reasonable design choice for on-premises execution where memory is constrained, but it creates operational overhead in the form of disk space management, file permission maintenance, and cleanup scheduling. The typical staging file in this platform is a CSV ranging from a few kilobytes (reference data lookups) to several hundred megabytes (full Sales_Set extracts), so the disk space impact is non-trivial at scale.

---

## Risk Analysis

Each side effect has been assessed for risk based on two factors: whether it could cause operational problems if left unmanaged, and whether the job includes logic to clean it up after execution.

| Risk Level | Count | Share |
|------------|-------|-------|
| Low | 436 | 79% |
| Medium | 119 | 21% |
| High | 0 | 0% |

The **436 low-risk effects** are those with established cleanup mechanisms. In most cases, the job includes the `cleanup_temp_file_storage` joblet in its `tPostjob` phase, which deletes temp and staging files after the main data flow completes. Some low-risk effects are files written to managed locations (such as Google Cloud Storage buckets with lifecycle policies) where external processes handle cleanup.

The **119 medium-risk effects** are all cases where a job writes to a staging file but includes no corresponding cleanup logic. These files will persist indefinitely on the execution server unless an external process (a cron job, a manual cleanup script, or the `cleanup_temp_file_storage` joblet in a different job) handles removal. The risk is not immediate data corruption but rather gradual disk space exhaustion. On servers running multiple jobs daily, the accumulation of uncleaned staging files can consume tens of gigabytes per month, eventually triggering disk-full errors that cause job failures across the entire platform.

The absence of high-risk effects is notable and speaks well of the platform's overall design. No jobs write to production database tables as an unintended side effect, and no jobs create files in locations outside the designated temp/staging directories. The risk is one of accumulation -- disk space exhaustion over time -- rather than data corruption or security exposure. The platform's developers consistently channeled side effects through the `resource_flow_temp_folder` directory, even when they neglected to include cleanup logic.

---

## The Cleanup Pattern: `cleanup_temp_file_storage`

The platform's primary cleanup mechanism is the **`cleanup_temp_file_storage` joblet**, a reusable Talend component that runs as part of the `tPostjob` phase at the end of job execution. When included, this joblet deletes all files in the `context.resource_flow_temp_folder` directory that were created during the current run.

### How It Works

The `tPostjob` component in Talend executes after the main data flow completes, regardless of whether the main flow succeeded or failed. This is similar to a `finally` block in Java or a `try...finally` construct in Python. By placing `cleanup_temp_file_storage` in the `tPostjob` phase, the platform ensures cleanup runs even when the main job encounters errors, providing a basic level of cleanup resilience.

The joblet itself is straightforward: it lists all files in the temp directory, filters for files matching known staging file patterns, and deletes them. The implementation is shared across all regions, meaning cleanup behavior is consistent wherever the joblet is included.

### Adoption Across Regions

The `cleanup_temp_file_storage` joblet is widely but not universally adopted. The following regions achieve complete cleanup coverage:

- **Global**: 84 effects, all low-risk (100% coverage)
- **JPN**: 84 effects, all low-risk (100% coverage)
- **DEU**: 22 effects, all low-risk (100% coverage)
- **GENERIC**: 25 effects, all low-risk (100% coverage)
- **ITA**: 11 effects, all low-risk (100% coverage)
- **ZAF**: 7 effects, all low-risk (100% coverage)
- **SAF**: 2 effects, all low-risk (100% coverage)

The regions with incomplete coverage are BRA (85 medium-risk out of 184 total, 46% without cleanup), USA (30 medium-risk out of 132 total, 23% without cleanup), and scratchpad (4 medium-risk out of 4 total, 100% without cleanup).

### Limitations

The joblet has three significant limitations:

**It only cleans the designated temp folder.** Files written to other locations -- non-standard directories, GCS buckets, or staging areas outside `context.resource_flow_temp_folder` -- are not covered. Jobs that write to custom paths must implement their own cleanup logic. The analysis did not identify any jobs writing to locations outside the temp folder, but the possibility exists, particularly in development variants.

**It is not universally included.** The 119 medium-risk effects exist precisely because their parent jobs do not include the `cleanup_temp_file_storage` joblet. There is no enforcement mechanism -- no linting rule, no deployment gate, no code review checklist item -- that requires jobs to include cleanup logic. Inclusion depends entirely on the developer remembering to add it.

**It operates on a per-job basis.** If a job writes staging files that are consumed by a downstream job, the cleanup joblet in the upstream job may delete the files before the downstream job reads them. This timing dependency is managed by job sequencing in the orchestrator, but it creates a fragile coupling between cleanup timing and execution order. If jobs are rescheduled, parallelized, or retried out of order, staging files may be deleted prematurely or read by the wrong downstream consumer.

---

## Regional Breakdown

Side effects are not evenly distributed across regions. The regions with the most complex pipelines produce the most side effects:

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

Two regions account for **97% of all medium-risk effects**:

### Brazil (BRA): 85 Medium-Risk Effects

Brazil has 85 medium-risk effects out of 184 total, making it the single largest source of unmanaged side effects. This is consistent with Brazil's position as the most complex region in the platform: 75 jobs, the most components, two parallel ERP pipelines (legacy MSSQL and Business Central), and the most elaborate Sales_Set construction process.

The BRA medium-risk effects are concentrated in two job families:

1. **Clean-layer Sales_Set builders** (Copy_of_VBR_Clean_Build_Sales_Set and its variants): These jobs produce multiple staging files per execution as they process invoice headers, invoice lines, credit memos, and returns data through separate enrichment stages. The "Copy_of" variants were likely created for development iteration and never received the cleanup components present in the canonical versions.

2. **Production order processing** (VBR_Clean_Production_Order_Register, VBR_Clean_CBR_ProductionOrderRoutingLines, and related jobs): These jobs write staging CSVs with hardcoded filenames that include environment prefixes baked into the filename string rather than derived from context variables. The hardcoded names suggest these jobs were developed in the test environment and never fully parameterized for production deployment.

### USA: 30 Medium-Risk Effects

USA contributes 30 medium-risk effects, concentrated in the clean-layer processing for the VUS and NPL subsidiaries. The Animalytix pipeline, which ingests third-party distributor data from SFTP via Google Cloud Storage, adds additional staging files that are not covered by the standard cleanup pattern. The VUS_Clean_Build_Sales_Set job follows the same staging file pattern as Brazil's Sales_Set builder but lacks the cleanup joblet.

### Scratchpad: 4 Medium-Risk Effects

The scratchpad region has 4 medium-risk effects, all staging files from development experiments. These jobs are not intended for production use, but if they are deployed to a shared server (as sometimes happens during development), their staging files will never be cleaned up. This is a low-priority concern but illustrates the systemic nature of the cleanup gap: even development jobs should include cleanup logic to prevent disk pollution on shared infrastructure.

---

## Concrete Examples of Medium-Risk Effects

The following examples illustrate the patterns that produce medium-risk side effects -- staging files written without cleanup logic.

### Example 1: Copy_of_VBR_Clean_Build_Sales_Set (BRA)

This job writes a staging CSV to a dynamically constructed path:

```
context.resource_flow_temp_folder + "/" + context.parameter_VBR_Prefix
    + context.parameter_BC_Company_Identifie...
```

The path includes multiple context variable segments, making it difficult to predict the exact file location without knowing the runtime environment. No cleanup logic exists in the job. Because this is a "Copy_of" variant (a development copy of the canonical job), it may have been created without the cleanup components present in the original. This pattern -- copying a job for experimentation and omitting lifecycle management components -- is one of the primary sources of medium-risk effects across the platform.

The dynamic path construction also creates a maintenance concern: if any of the context variables used in the path are renamed or removed, the job will either fail at runtime or write to an unexpected location. In the target platform, staging file paths should be derived from a small number of well-defined variables rather than concatenated from multiple business-level parameters.

### Example 2: VBR_Clean_Production_Order_Register (BRA)

This job writes to:

```
context.resource_flow_temp_folder + "/" + "test_bra_vbr_clean_Production_Order_Register.csv"
```

The hardcoded `test_` prefix in the filename is a red flag: this staging file path is locked to the test environment regardless of which context environment is active. If this job runs in production, it will still write a file with a `test_` prefix, creating confusion about which files belong to which environment. An operator investigating disk usage on the production server would see files named `test_*` and might reasonably assume they are safe to delete -- even though they were produced by a production run.

The lack of cleanup means these files persist and multiply with each execution. If the job runs daily, the production server will accumulate a new `test_bra_vbr_clean_Production_Order_Register.csv` file every day (overwriting the previous one if atomic writes are supported, or leaving partial files alongside complete ones if they are not).

### Example 3: VBR_Clean_CBR_ProductionOrderRoutingLines (BRA)

Writes to:

```
context.resource_flow_temp_folder + "/" + "test_global_clean_ProductionOrderLines_CBR.csv"
```

This job writes to a hardcoded path that references the global clean dataset (`test_global_clean`) rather than a BRA-specific dataset. This cross-regional filename either reflects a legitimate dependency (the BRA job producing output consumed by a Global job) or a copy-paste error from a Global job template. Either way, the filename creates ambiguity about ownership: should BRA cleanup scripts delete files prefixed with `test_global_clean`, or is that the Global region's responsibility? The lack of cleanup in the job itself means the question is moot -- nobody cleans it up.

### Example 4: VBR_Cost_Breakdown_Report_Test_2 (BRA)

Writes to:

```
context.resource_flow_temp_folder + "/" + context.parameter_Env_Prefix + "Sales_Set" + ".csv"
```

This is a test job (indicated by the `_Test_2` suffix) that produces a staging file using the environment prefix pattern. While the job name clearly marks it as experimental, the staging file it writes follows the same naming convention as production staging files. A file named `bra_vbr_raw_Sales_Set.csv` produced by this test job is indistinguishable from one produced by the canonical Sales_Set builder. Without cleanup, repeated test runs will leave behind files that a downstream job or cleanup script might mistakenly treat as production artifacts.

### Example 5: VUS_Clean_Build_Sales_Set (USA)

Writes to:

```
context.resource_flow_temp_folder + "/" + context.parameter_Env_Prefix + "Sales_Set" + ".csv"
```

The USA counterpart of the Brazil Sales_Set staging pattern. This job follows the correct environment-parameterized path convention but lacks cleanup logic. Given that VUS_Clean_Build_Sales_Set runs on every execution cycle, this file is re-created each time. Without cleanup, the file simply persists between runs. If the file system does not support atomic overwrites, a job failure during the write phase could leave a partially written CSV that the next run's overwrite may or may not correctly replace, depending on the file system's behavior.

---

## Cross-Cutting Patterns

Several cross-cutting patterns emerge from the side-effect analysis:

### File-Based Staging as the De Facto Message Bus

Two-thirds of all side effects are staging files written to `context.resource_flow_temp_folder`. This directory is the de facto message bus for the platform, carrying intermediate datasets between job steps. The pattern is consistent across all regions: extract data to a CSV, process the CSV, load the result into BigQuery. Any migration must either preserve this pattern (using cloud storage in place of local disk) or replace it with a fundamentally different approach (in-memory passing, streaming, or direct database-to-database transfers).

The staging-file pattern has one significant advantage: it provides a natural checkpoint. If a downstream job fails, the staging file from the upstream job is still available for re-reading without re-extracting the source data. This checkpoint behavior should be preserved in the migration, whether through explicit staging in cloud storage or through the target platform's native retry/checkpoint mechanisms.

### Cleanup Discipline Varies by Region

The Global, JPN, GENERIC, DEU, ITA, ZAF, and SAF regions have zero medium-risk effects, indicating that their developers consistently included the `cleanup_temp_file_storage` joblet. BRA and USA, despite being the largest and most complex regions, have significant cleanup gaps. This may reflect different development teams, different development eras, or simply the higher likelihood of oversight in complex pipelines with many variants and copies.

The correlation between "Copy_of" job variants and missing cleanup is particularly strong. Development copies inherit the data flow logic but not always the operational scaffolding (cleanup, logging, error handling). This pattern should inform migration testing: when migrating jobs, the operational components should be verified independently of the data flow logic.

### Hardcoded Environment Indicators in Filenames

Multiple BRA jobs embed `test_` directly in their staging file paths rather than using the `context.parameter_Env_Prefix` variable. This creates files whose names suggest they are test artifacts regardless of the runtime environment, complicating automated cleanup logic that might key on file naming patterns. A cleanup script that deletes all files matching `test_*` would correctly clean test artifacts but would also destroy production-generated files from these misconfigured jobs.

### Staging Files as an Implicit Contract

The staging file pattern creates an implicit contract between jobs: the upstream job promises to write a CSV with a specific name and schema to a specific directory, and the downstream job promises to read it. This contract is not documented anywhere in the codebase -- it exists only in the matching file path strings embedded in the upstream writer and the downstream reader. If either side changes the filename, the directory, or the column layout, the other side breaks silently.

In a migration context, these implicit contracts must be explicitly cataloged. For each staging file, the migration team should identify the producing job, the consuming job(s), the expected file schema (column names and types), and the expected file size range. This catalog can then be used to validate that the migration preserves the data exchange patterns or to design explicit contracts (API schemas, message queue topics, or table interfaces) that replace the file-based coupling.

### Interaction with Error Handling Gaps

The side-effect analysis intersects with the error handling findings documented in `11_KNOWN_ISSUES.md`. Many of the jobs that lack cleanup logic also lack error handling: if a job fails mid-execution, it leaves behind partial staging files and produces no error notification. A subsequent run may overwrite the partial file or may fail itself if the partial file has corrupted headers or truncated rows. The combination of no cleanup and no error handling creates a scenario where failures compound silently over multiple runs, with each run leaving behind additional orphaned artifacts.

---

## Migration Takeaways

### 1. Replace File-Based Staging with Cloud-Native Storage

The 364 staging files currently written to local disk should be migrated to cloud storage (Google Cloud Storage, Azure Blob Storage) or eliminated entirely in favor of in-memory data passing. Cloud storage provides automatic lifecycle policies (TTL-based deletion), versioning, and access controls that eliminate the cleanup problem at its root. A lifecycle policy that deletes objects older than 24 hours in the staging bucket would address 100% of the cleanup-related risk identified in this analysis.

### 2. Enforce Cleanup as a Platform Policy

The current system relies on individual developers to include the `cleanup_temp_file_storage` joblet in each job. This opt-in model has a 21% failure rate. The target platform should enforce cleanup either through framework-level guarantees (automatic temp file cleanup on job completion) or through pipeline validation that rejects jobs without cleanup logic. A CI/CD gate that checks for cleanup configuration before allowing deployment would prevent the introduction of new unmanaged side effects.

### 3. Audit the 119 Medium-Risk Effects Before Migration

Each of the 119 staging files without cleanup logic should be reviewed to determine: (a) whether the file is still needed by a downstream process, (b) whether the cleanup was intentionally omitted because the file must persist between job runs, or (c) whether the omission was an oversight. This audit should prioritize the 85 BRA effects and 30 USA effects, which together account for 97% of the risk. The scratchpad effects can be addressed by simply excluding scratchpad jobs from the migration.

### 4. Preserve Idempotency Guarantees

Several jobs use a TRUNCATE-then-write pattern for their staging files, ensuring that each run produces a clean output regardless of what existed before. The target platform should preserve this idempotency, particularly for the Sales_Set staging pipeline, where partial or duplicate files could cascade into incorrect reporting data. Cloud storage with versioning provides a natural implementation: each run writes a new version of the staging object, and readers always consume the latest version.

### 5. Eliminate Hardcoded Paths

The staging files with hardcoded `test_` prefixes (e.g., `test_bra_vbr_clean_Production_Order_Register.csv`) should be refactored to use environment-parameterized paths. In the migration, this can be achieved by deriving all file paths from environment configuration rather than embedding them in job logic. A convention like `{env}/{region}/{job_name}/{timestamp}.csv` would provide clear environment separation, region attribution, and temporal ordering without requiring any job-specific path logic.

### 6. Implement Monitoring for Side-Effect Accumulation

Even with improved cleanup policies, the migration target should include monitoring for unexpected file accumulation. A simple metric -- total file count and total size in the staging directory, reported daily -- would have detected the cleanup gaps identified in this analysis long before they became problematic. Cloud storage platforms offer built-in metrics for object counts and storage consumption that can be piped into alerting systems. Setting a threshold alert (for example, staging directory exceeding 10 GB or 1,000 files) provides an early warning that cleanup policies are not operating as expected.

This monitoring is particularly important during the migration transition period, when jobs may be running in both the old Talend environment and the new target platform simultaneously, potentially doubling the volume of staging files produced.

### 7. Catalog Implicit Staging Contracts

Before migration, document every staging file that serves as a data exchange point between two or more jobs. For each file, record the producing job, the consuming job(s), the expected schema, and the expected freshness window (how old the file can be before downstream processing considers it stale). This catalog transforms implicit file-based contracts into explicit interface definitions that can be implemented as typed schemas, validated data frames, or cloud storage event triggers in the target platform. Without this catalog, the migration risks breaking inter-job dependencies that are invisible in the code but critical to the pipeline's correctness.

---

*Generated by intodoc -- documentation automation for integration platforms.*
