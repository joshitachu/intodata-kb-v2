# Environment Configuration, External Systems & Cross-Region Dependencies

This document consolidates three areas of the Vaxxinova Talend platform's infrastructure analysis: the environment configuration layer (957 context variables governing runtime behavior), the external system landscape (11 systems connected across 257 jobs), and the cross-region dependency patterns that shape how data flows between subsidiaries. Together, these areas define the platform's operational surface -- every variable must be mapped, every connection re-established, every credential re-provisioned, and every cross-region dependency preserved or explicitly redesigned during migration.

---

## Part 1: Environment Configuration

### Overview

Talend context variables are the primary mechanism by which the platform manages environment-specific configuration. Across the 257 jobs, a total of **957 context variables** have been identified, of which **363 (38%) carry risk** due to divergent values between production and test environments. These variables encode the operational differences between environments that must be preserved -- or deliberately redesigned -- in the target platform.

### Environment Landscape

The platform defines seven named environments:

| Environment | Purpose |
|-------------|---------|
| **Default** | Fallback values; typically empty strings |
| **PROD** | Production execution on the primary server |
| **PROD_MAC** | Production execution on macOS (Global and USA only) |
| **TEST** | Test execution on the primary server |
| **TEST_LINUX** | Test execution on Linux infrastructure |
| **TEST_MAC** | Test execution on macOS |
| **_default** | Talend internal default; baseline before environment overrides |

The **Default** environment is almost universally set to empty strings -- a deliberate safety mechanism that prevents accidental execution against live systems, but creates a stumbling block for new team members who may not realize they must explicitly set the environment. The PROD_MAC and TEST_MAC variants reflect historical heterogeneity in development and execution infrastructure.

### Environment Selection at Runtime

When a Talend job executes, the runtime resolves context variables in order:

1. Command-line override: `--context_param environment=PROD`
2. TAC-configured environment for scheduled jobs
3. The `_default` environment if no override is provided

The `_default` values mirror the Default environment (empty strings), so the fallback behavior is equivalent to running with no configuration at all.

### Variable Naming Convention

Context variables follow a structured pattern:

```
{region}_{subsidiary}_{domain}.parameter_{name}
```

Examples:
- `BRA_PROBI_BC_environment.parameter_Env_Prefix` -- Brazil environment prefix
- `USA_Vaxxinova_Salesforce.parameter_USA_Vaxxinova_Salesforce_oauth2JwtFlow_audience` -- USA Salesforce OAuth audience
- `connection_USA_NPL_MSSQL.connection_USA_NPL_MSSQL_Database` -- USA NPL database name

### Variable Categories

The 957 variables group into functional categories:

**Environment prefixes** (`parameter_Env_Prefix`): Control which BigQuery dataset a job targets. The most operationally critical variables -- they determine whether a job reads from and writes to production or test data.

**Connection parameters** (`connection_*`): Database hostnames, ports, database names, and authentication details for MSSQL, BigQuery, and Salesforce connections.

**Resource paths** (`resource_*`): File system paths for temp files, staging directories, credential files, and log outputs.

**API configuration** (`parameter_*_oauth2*`, `parameter_*_endpoint`): OAuth endpoints, client IDs, and API base URLs for Salesforce and Business Central.

**Business parameters** (`parameter_*_Company`, `parameter_*_Entity`): Business-level constants like company identifiers and default currencies. These typically do not vary between environments.

### PROD/TEST Divergences

Of the 957 total variables, **363 (38%)** have different values between PROD and TEST environments. The divergence falls into predictable categories:

**Environment Prefix Pattern** (most common):

| Environment | Example Value (BRA) |
|-------------|---------------------|
| PROD | `bra_vbr_raw` |
| TEST | `test_bra_vbr_raw` |
| TEST_LINUX | `test_bra_vbr_raw` |
| TEST_MAC | `test_bra_vbr_raw` |

This pattern is clean and predictable. The `test_` prefix convention is uniform across all regions. However, co-locating test and production datasets in the same BigQuery project carries inherent risk.

**Inconsistent Case in Global Variables** (notable risk):

The `Global_Clean.parameter_Env_Prefix` variable has **TEST_LINUX** resolving to `Global_Clean` (the production value) rather than `test_global_clean`. This means a test run on Linux infrastructure reads from and writes to the production dataset. Whether intentional or accidental, this represents a genuine risk of unintended production data modification during testing.

**Database Name Divergences:**

| Environment | Example Value (USA NPL) |
|-------------|--------------------------|
| PROD | `BI360DW` |
| TEST | `BI360DW102622` |

The test database name `BI360DW102622` includes a date suffix (October 26, 2022), suggesting a frozen snapshot. Test data may be significantly outdated.

**API Endpoint Divergences:**

| Environment | Salesforce OAuth Audience |
|-------------|---------------------------|
| PROD | `https://login.salesforce.com` |
| TEST | `https://test.salesforce.com` |
| PROD_MAC | `https://login.salesforce.com` |

Standard Salesforce production/sandbox separation. Correctly configured across all environments.

### Regional Distribution of Configuration Risk

| Region | Variables | With Risk | Risk % | Environments | Key Systems |
|--------|-----------|-----------|--------|--------------|-------------|
| USA | 385 | 147 | 38% | 7 (incl. PROD_MAC) | MSSQL, BC, Salesforce, GCS |
| BRA | 248 | 64 | 26% | 6 | MSSQL, BC, Salesforce |
| Global | 144 | 96 | 67% | 7 (incl. PROD_MAC) | BigQuery, Salesforce |
| JPN | 138 | 42 | 30% | 6 | MSSQL, BC, Salesforce |
| DEU | 18 | 8 | 44% | 6 | BC (VDA), MSSQL (VDD) |
| ITA | 14 | 3 | 21% | 6 | MSSQL, Salesforce |
| SAF | 10 | 3 | 30% | 6 | MSSQL |

USA dominates with 385 variables (40% of total), reflecting two subsidiaries and a complex mix of integrations. Global has the highest risk percentage at 67%, consistent with its role as the cross-regional consolidation layer. SAF has 10 variables despite having only 1 job, suggesting provisioning for a full pipeline that has not yet been built.

### Credential Management

Credentials are managed through two mechanisms:

**Context-based secret references:** Sensitive values stored as context variables, encrypted by Talend's built-in encryption. The encryption is reversible by anyone with workspace access. Credentials are decrypted at runtime and passed to connection components in plaintext.

**File-based credential loading:** Several BRA jobs load credentials from flat files at runtime using `tFileInputDelimited`. The `BC_Secret_Key_Context` file is referenced across all 23 raw extraction jobs. Credentials are stored in plaintext with no encryption at rest -- their security depends entirely on file system permissions.

Neither mechanism meets modern security standards. Both store secrets in locations accessible to anyone with server access, and neither supports automated rotation, audit logging, or access controls beyond OS file permissions.

---

## Part 2: External System Landscape

### System Inventory

The platform connects 257 jobs to 11 unique external systems:

| System | Type | Criticality | Regions | Job Count |
|--------|------|-------------|---------|-----------|
| Google BigQuery | Database | Medium | All 10 regions | 35 |
| File System (GCS/local) | Cloud | Medium | All 10 regions | 34 |
| REST API | Cloud | Medium | BRA, GENERIC, JPN, USA | 10 |
| Microsoft SQL Server (generic) | Database | Medium | BRA, ITA, JPN, SAF, USA, ZAF, scratchpad | 6 |
| connection_JPN_VJP_MSSQL | MSSQL | High | JPN, scratchpad | 3 |
| connection_ITA_VAX_MSSQL | MSSQL | High | ITA, SAF, ZAF | 2 |
| FTP/SFTP | Cloud | Medium | USA | 1 |
| connection_BRA_VBR_MSSQL | MSSQL | High | BRA | 1 |
| connection_JDBC_Google_BigQuery | JDBC | High | scratchpad | 1 |
| connection_SAF_VSA_MSSQL | MSSQL | High | ZAF | 1 |
| connection_USA_NPL_MSSQL | MSSQL | High | USA | 1 |

### Google BigQuery: The Central Data Warehouse

BigQuery is the gravitational center of the platform, appearing in 35 of 257 jobs and referenced by every region. It serves three roles:

1. **Raw landing zone:** Regional extraction jobs write source data into datasets prefixed with `raw` (e.g., `bra_vbr_raw`, `jpn_vjp_raw`).
2. **Clean transformation layer:** BigQuery-to-BigQuery transformations using tBigQueryInput, tMap, and tBigQueryOutput. Reference data tables (Fiscal_Periods, Country_Master, Salesforce_Country_Mappings, Budget_Current_Conversion_Rate) live in global BigQuery datasets.
3. **Global consolidation:** The Global region reads from regional clean datasets and produces the unified Sales_Set.

BigQuery is accessed through two connector mechanisms: native tBigQueryInput/tBigQueryOutput (production) and JDBC-based connection (scratchpad only). Service account keys are managed through context variables.

### MSSQL: On-Premises ERP Databases

Five named MSSQL connections serve as primary source systems:

**connection_BRA_VBR_MSSQL (Brazil):** Extracts from SQL Server views (VW_Finished_Product, VW_Sales, VW_Returns). The view-based pattern abstracts the ERP's internal schema complexity. Extraction windows must avoid peak transactional hours.

**connection_ITA_VAX_MSSQL (Italy, shared with SAF and ZAF):** The only MSSQL connection shared across regions. ZAF references Italy's connection rather than its own, indicating either shared hosting infrastructure or an uncorrected clone. Network maintenance or credential rotation on Italy's server simultaneously affects both pipelines.

**connection_JPN_VJP_MSSQL (Japan):** Feeds the monolithic JPN_VJP_RAW extraction job pulling sales invoices, credit memos, GL entries, items, and customers. Japan's 128 tMap instances mean this single connection feeds exceptionally complex downstream transformations.

**connection_SAF_VSA_MSSQL (South Africa):** Despite the name, referenced by ZAF region jobs rather than SAF. The SAF region's single job uses tMSSqlInput directly with inline configuration, bypassing the named connection -- a metadata hygiene issue.

**connection_USA_NPL_MSSQL (United States):** Serves the Newport Labs legacy entity. Used by 1 job in metadata, but NPL customer data flows into the USA Sales_Set and then Global consolidation, giving it downstream impact well beyond its single direct consumer.

**Network connectivity consideration:** All MSSQL connections target on-premises servers behind corporate firewalls. Any cloud-based migration target must establish secure network paths (VPN tunnels, ExpressRoute, Direct Connect, or hybrid gateways). This is typically a 4-8 week infrastructure task.

### Salesforce: CRM Integration

Salesforce integration is embedded through product synchronization and account mapping:

- **BIO_Sync_Product_Salesforce_v1 (BRA):** Syncs vaccine product master data to Salesforce Product2 object with BIO- prefix.
- **IZO_Sync_Product_Salesforce_v5 (ITA):** Equivalent product sync for Italy, at version 5.
- **VUS_Clean_BC_Item_v8_Salesforce (USA):** Pushes product master data bi-directionally.

Nearly every regional Sales_Set construction job reads from `Global_Raw.Salesforce_Account_Mappings` and `Global_Raw.Salesforce_Country_Mappings` tables in BigQuery, making this a bidirectional but asymmetric integration (BigQuery to Salesforce for products; Salesforce to BigQuery for reference data).

### REST APIs: Business Central OData Endpoints

Ten jobs across four regions (BRA, GENERIC, JPN, USA) consume REST API endpoints targeting Microsoft Dynamics 365 Business Central's OData v4 protocol. The GENERIC region houses reusable template jobs:

- **GEN_BC_SalesInvoices / GEN_BC_SalesInvoices_oData** -- sales invoice extraction
- **GEN_BC_SalesCreditMemos** -- credit memo extraction
- **GEN_BC_Items / GEN_BC_Item_oData** -- product master data
- **GEN_BC_Customers_oData** -- customer records

These use a sophisticated architecture: `tRESTClient` (13 instances), `tExtractJSONFields` (17 instances), `tJavaFlex` (13 instances) for pagination, and `Create_Business_Central_Token` for OAuth against Azure AD.

Regional jobs invoke GENERIC templates by setting context variables. This is why Germany's VDA extraction does not appear in the DEU folder -- it calls GENERIC BC jobs with VDA-specific parameters.

### FTP/SFTP

The single SFTP system (USA only) supports the Animalytix third-party distributor pipeline, retrieving files from an external SFTP server via Google Cloud Storage (`tGSGet` component).

### File System and Cloud Storage

34 of 257 jobs reference file system or GCS paths, spanning all 10 regions:

- **Temporary CSV staging:** Intermediate data written to local disk, read back for processing. The `cleanup_temp_file_storage` joblet handles removal but is absent from BRA, USA, and JPN.
- **Google Cloud Storage staging:** USA Animalytix pipeline uses tGSGet for GCS file retrieval.
- **Context and configuration files:** Environment-specific connection parameters and Business Central secret keys loaded from local files.

### Connection Parameterization

All external connections use Talend context variables for parameterization:

```
connection_BRA_VBR_MSSQL  -->  host: context.connection_BRA_VBR_MSSQL_Server
connection_ITA_VAX_MSSQL  -->  host: context.connection_ITA_VAX_MSSQL_Server
connection_JPN_VJP_MSSQL  -->  host: context.connection_JPN_VJP_MSSQL_Server
```

Each connection defines server address, database name, port, username, and password as separate context variables. The full connection string is assembled at runtime, and the migration team must capture all parameters -- not just the host.

### Credential and Authentication Landscape

| System Type | Authentication | Migration Notes |
|-------------|----------------|-----------------|
| MSSQL | SQL Server auth (username + password) | Encrypted in Talend; extract plaintext or request fresh credentials |
| BigQuery | Service account JSON key files | Verify IAM bindings; consider Workload Identity Federation on GCP |
| Salesforce | OAuth 2.0 Connected App | Re-provision connected app with read and write scopes |
| Business Central | Azure AD OAuth 2.0 | Capture tenant ID, client ID, client secret for registered application |
| SFTP | Key-based or password | Determine auth type; provision in target platform |

### Region System Density

| Region | System References | High Criticality | Medium Criticality |
|--------|-------------------|-------------------|--------------------|
| BRA | 20 | 1 | 19 |
| USA | 20 | 1 | 19 |
| JPN | 17 | 1 | 16 |
| Global | 16 | 0 | 16 |
| ZAF | 5 | 2 | 3 |
| scratchpad | 5 | 2 | 3 |
| ITA | 4 | 1 | 3 |
| SAF | 4 | 1 | 3 |
| GENERIC | 3 | 0 | 3 |
| DEU | 2 | 0 | 2 |

BRA and USA lead with 20 system references each. The Global region has zero high-criticality connections -- it operates entirely within BigQuery, never touching source systems directly.

---

## Part 3: Cross-Region Dependencies

### Region Comparison Matrix

| Region | Jobs | % of Total | Key Distinguishing Characteristics |
|--------|------|-----------|--------------------------------------|
| BRA | 75 | 29% | Dual-source (legacy MSSQL + Business Central); largest component footprint |
| USA | 61 | 24% | Two subsidiaries (NPL + VUS); FTP/SFTP integration unique to this region |
| Global | 43 | 17% | Cross-region consolidation; the only region with dependency chains |
| JPN | 36 | 14% | Uses tUnite for stream merging; monolithic raw extraction |
| GENERIC | 15 | 6% | Shared BC API extractors; parameterized templates consumed by other regions |
| DEU | 12 | 5% | Dual-entity (VDA + VDD) reflecting BC migration from legacy ERP |
| ITA | 6 | 2% | Clean-layer sales plus Salesforce product sync |
| scratchpad | 5 | 2% | Development workspace; only region with tDie error component |
| ZAF | 3 | 1% | Minimal South Africa; shares MSSQL connection with ITA |
| SAF | 1 | <1% | Single extraction job; only region using tMSSqlInput directly |

The four largest regions -- BRA, USA, Global, and JPN -- account for 84% of all jobs.

### Shared vs. Region-Specific Components

**Universal:** tMap (100% of regions -- the core transformation engine).

**Near-universal:** tBigQueryInput (7 of 10 regions), tBigQueryOutput (6 of 10).

**Operationally shared:** cleanup_temp_file_storage (5 of 10, absent from BRA/USA/JPN), Vaxxinova_Logging (4 of 10, absent from BRA/USA/JPN).

**Region-specific:** tMSSqlInput (SAF only), tFixedFlowInput (USA only), tUnite (JPN only), tExtractJSONFields and tFileInputDelimited (GENERIC only).

### Global Orchestration

The Global region (43 jobs) is the analytical capstone. It does not extract from source systems itself; instead it reads regional clean-layer outputs from BigQuery:

1. Regional jobs produce regional Sales_Set tables (e.g., `bra_vbr_clean.Sales_Set`)
2. Global_Build_Sales_Set reads from regional datasets, applies enrichment, produces Global_Clean.Sales_Set
3. Budget and forecast jobs process financial planning data from Global_Budget
4. Legacy compatibility jobs maintain backward-compatible transformations

`Global_Clean_Sales_Set` is the single bottleneck. If any regional Sales_Set is stale when Global runs, the consolidated output contains gaps. There are no explicit dependency checks or data freshness validations -- the current approach relies on implicit timing.

### The SAF/ZAF Consolidation

Two regional designations exist for South Africa -- SAF and ZAF -- representing the same subsidiary at different development stages:

**SAF** (1 job): Extracts invoice details using tMSSqlInput directly. Includes logging and cleanup best practices.

**ZAF** (3 jobs): Handles broader entity coverage but references `connection_ITA_VAX_MSSQL` instead of SAF's own connection. This cross-wiring means either South Africa's data is hosted on Italy's infrastructure or the pipeline was cloned without updating connection references.

**Consolidation path:** During migration, merge into a single ZAF pipeline that uses a dedicated South African connection, incorporates ZAF's entity coverage, retains SAF's operational best practices, and adds error handling.

### Template Reuse Opportunities

The cross-region analysis reveals remarkable structural similarity. Four templates would cover approximately 70-75% of all production jobs:

**Sales_Set Builder Template:** Every region follows the same pattern: read from raw BigQuery tables, enrich with fiscal periods and Salesforce mappings, apply currency conversion, tag with entity identifiers, write to regional clean and Global_Clean datasets. Parameters: raw dataset name, entity code, default currency, Salesforce namespace prefix, target clean dataset.

**Raw Extraction Template:** The GENERIC GEN_BC_* family already demonstrates this for Business Central. Extend to cover MSSQL extraction. Parameters: connection name, source view/table, target BigQuery dataset, column mapping.

**Salesforce Sync Template:** Three regions (BRA, ITA, USA) use nearly identical logic for Product2 upserts. Parameters: region-specific namespace prefix, product field mapping.

**Delete-Then-Insert Template:** The idempotent reload pattern appears in dozens of jobs. Replace with MERGE/UPSERT where supported, or provide a reusable utility handling the sequence atomically.

### Regional Maturity Assessment

| Region | Logging | Cleanup | Parameterization | Error Handling | Overall Maturity |
|--------|---------|---------|------------------|----------------|------------------|
| GENERIC | Yes | No | Strong | 33% adequate | High |
| DEU | Yes | Yes | Strong | 17% adequate | Medium-High |
| Global | Yes | Yes | Standard | 5% adequate | Medium |
| ITA | No | Yes | Standard | 0% | Medium |
| ZAF | No | Yes | Standard | 0% | Medium |
| SAF | Yes | Yes | Standard | 0% | Medium |
| BRA | No | No | Mixed | 13% adequate | Low |
| USA | No | No | Mixed | 11% adequate | Low |
| JPN | No | No | Standard | 0% | Low |

The counterintuitive pattern: smallest regions tend to be most mature. SAF, ZAF, and DEU include logging and cleanup, while BRA, USA, and JPN lack both. This likely reflects a chronological effect -- smaller regions were built later with more experience.

### Component Overlap Analysis

| Reach | Component | Regions Using | Coverage |
|-------|-----------|---------------|----------|
| Universal | tMap | 10 of 10 | 100% |
| Near-universal | tBigQueryInput | 7 of 10 | 70% |
| Broad | tBigQueryOutput | 6 of 10 | 60% |
| Moderate | cleanup_temp_file_storage | 5 of 10 | 50% |
| Moderate | tPostjob | 5 of 10 | 50% |
| Limited | Vaxxinova_Logging | 4 of 10 | 40% |
| Limited | tHashOutput | 3 of 10 | 30% |

A migration team that builds robust translations for tMap, tBigQueryInput, and tBigQueryOutput will have a reusable foundation covering every region.

---

## Consolidated Migration Takeaways

### Environment Configuration

1. **Map all 957 context variables to the target platform's configuration management.** Create a machine-readable registry (CSV or JSON) mapping each Talend variable to its target equivalent, with PROD value, TEST value, credential flag, and responsible team.
2. **Validate all 363 PROD/TEST divergences.** Confirm each is intentional, verify test values point to valid resources, and ensure consistency across test environments. The Global_Clean TEST_LINUX misconfiguration is a concrete example requiring review.
3. **Migrate credentials to a secrets vault.** Replace context-variable and flat-file credentials with Azure Key Vault, AWS Secrets Manager, or HashiCorp Vault. Enforce encrypted storage, automated rotation, audit trails, and least-privilege access.
4. **Eliminate platform-specific environments.** Reduce from seven variants (Default, PROD, PROD_MAC, TEST, TEST_LINUX, TEST_MAC, _default) to three (default, production, test).
5. **Centralize environment prefix logic.** Replace per-region independent prefix variables with a single, centrally managed naming convention.

### External Systems

6. **Map every connection type to the target platform's connector library.** Five distinct families: native BigQuery, JDBC BigQuery, MSSQL native, Salesforce, REST/HTTP.
7. **Solve on-premises MSSQL connectivity early.** The five connections require secure network paths. This is typically a 4-8 week infrastructure task.
8. **Re-provision Salesforce OAuth credentials.** The target platform needs a connected app with read and write scopes.
9. **Migrate BigQuery service account keys to a secrets store.** Consider Workload Identity Federation on GCP to eliminate keys entirely.
10. **Preserve the GENERIC template pattern.** Parameterized Business Central extraction is one of the platform's best architectural features.
11. **Plan for file system dependencies.** 34 jobs referencing file paths must be mapped. Temporary CSV staging may be eliminated if the target supports in-memory or streaming transforms.
12. **Document all credential lifetimes and rotation schedules.** Create a credential inventory capturing type, owning team, rotation frequency, and expiration date.

### Cross-Region Dependencies

13. **Prioritize migration by impact.** BRA + USA first (53% of platform), then Global (depends on regional outputs), then JPN, then GENERIC (shared templates), then smaller regions.
14. **Make Global dependencies explicit.** Replace implicit timing coordination with sensor-based triggers and dependency gates.
15. **Standardize operational components across all regions.** Every migrated job gets cleanup_temp_file_storage, Vaxxinova_Logging equivalents, and error handling -- regardless of whether the original had them.
16. **Validate MSSQL connections per-region.** Each of the five named connections requires independent testing of network, firewall, and credentials.
17. **Resolve the ITA/ZAF shared connection.** Determine whether ZAF's data genuinely resides on Italy's server or whether the connection was a clone artifact.
18. **Consolidate SAF and ZAF** into a single regional pipeline.
19. **Time zone coordination.** Subsidiaries span Brazil (UTC-3), US (UTC-5 to UTC-8), Germany/Italy (UTC+1), Japan (UTC+9), and South Africa (UTC+2). Schedule extraction to avoid peak hours in each zone.

### Recommended Migration Phasing

| Phase | Regions | Jobs | Rationale |
|-------|---------|------|-----------|
| 1 (Proof of concept) | SAF + ZAF | 4 | Smallest production pipeline; includes operational best practices |
| 2 (Core templates) | GENERIC | 15 | Must be migrated before dependent regions |
| 3 (First major) | BRA | 75 | Largest, most complex; exercises every source type |
| 4 (Second major) | USA | 61 | Similar to BRA; reuse Phase 3 patterns |
| 5 (Third major) | JPN | 36 | Adds tUnite and monolithic extraction patterns |
| 6 (Smaller regions) | DEU + ITA | 18 | Small enough for single sprint |
| 7 (Consolidation) | Global | 43 | Depends on all regional outputs |
| 8 (Cleanup) | scratchpad | 5 | Do not migrate; retire development artifacts |

---

*Generated by intodoc -- documentation automation for integration platforms.*
