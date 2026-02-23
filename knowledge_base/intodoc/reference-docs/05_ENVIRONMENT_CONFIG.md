# Environment Configuration

Talend context variables are the primary mechanism by which the Vaxxinova integration platform manages environment-specific configuration. Every job in the platform -- from raw extraction to Salesforce synchronization -- relies on context variables to resolve database connections, API endpoints, file paths, and dataset names at runtime. Across the 257 jobs in the platform, a total of **957 context variables** have been identified, of which **363 (38%) carry risk** due to divergent values between production and test environments. Understanding this configuration layer is essential for any migration effort, because these variables encode the operational differences between environments that must be preserved -- or deliberately redesigned -- in the target platform.

---

## Environment Landscape

The platform defines seven named environments, each representing a distinct execution context:

| Environment | Purpose |
|-------------|---------|
| **Default** | Fallback values; typically empty strings |
| **PROD** | Production execution on the primary server |
| **PROD_MAC** | Production execution on macOS (Global and USA only) |
| **TEST** | Test execution on the primary server |
| **TEST_LINUX** | Test execution on Linux infrastructure |
| **TEST_MAC** | Test execution on macOS |
| **_default** | Talend internal default; used as the baseline before environment overrides |

Most regions define six of these environments (Default, PROD, TEST, TEST_LINUX, TEST_MAC, _default). The **PROD_MAC** environment appears only for **Global** and **USA** regions, reflecting the fact that some development and production workflows for these regions run on macOS workstations. The existence of platform-specific environments (TEST_LINUX, TEST_MAC, PROD_MAC) reveals that the team has historically developed and tested on heterogeneous hardware, which introduces configuration surface area that a cloud-based migration would eliminate.

The **Default** environment is almost universally set to an empty string. This is a deliberate design choice: it forces operators to explicitly select an environment at runtime. However, it also means that a job launched without an environment override will run with blank connection strings, dataset names, and API endpoints -- a silent failure mode that produces no errors but writes no data. In a production context, this blank-default pattern acts as a safety mechanism preventing accidental execution against live systems. In a development context, it creates a stumbling block for new team members who may not realize they must explicitly set the environment before running any job.

### Environment Selection at Runtime

When a Talend job is executed, the runtime selects which environment's values to use based on a command-line parameter or the Talend Administration Center (TAC) job trigger configuration. The resolution order is:

1. Command-line override: `--context_param environment=PROD`
2. TAC-configured environment for scheduled jobs
3. The `_default` environment if no override is provided

The `_default` environment serves as the Talend-internal baseline. Its values are loaded first, then overridden by the selected environment's values. This layered resolution means that any variable not explicitly set in the selected environment will fall back to its `_default` value. In practice, `_default` values mirror the Default environment (empty strings), so the fallback behavior is equivalent to running with no configuration at all.

This resolution model has implications for migration: the target platform must support a similar layered configuration mechanism, or all variables must be fully specified in every environment with no reliance on fallback behavior.

---

## Variable Naming Convention

Context variables follow a structured naming pattern:

```
{region}_{subsidiary}_{domain}.parameter_{name}
```

For example:

- `BRA_PROBI_BC_environment.parameter_Env_Prefix` -- Brazil, PROBI subsidiary, Business Central domain, environment prefix parameter
- `USA_Vaxxinova_Salesforce.parameter_USA_Vaxxinova_Salesforce_oauth2JwtFlow_audience` -- USA, Vaxxinova subsidiary, Salesforce domain, OAuth audience parameter
- `connection_USA_NPL_MSSQL.connection_USA_NPL_MSSQL_Database` -- USA, Newport Labs subsidiary, MSSQL connection, database name

Connection-type variables use a slightly different pattern: `connection_{region}_{subsidiary}_{system}.connection_{region}_{subsidiary}_{system}_{parameter}`. This redundant nesting is a Talend convention rather than a design choice, but it does make variable names long and occasionally unwieldy. The full path for a single connection parameter can exceed 80 characters, which complicates logging, debugging, and configuration management interfaces that truncate long strings.

The naming convention is generally consistent, but not enforced by any tooling. Global-scope variables use `Global_` as the region prefix (e.g., `Global_Clean.parameter_Env_Prefix`), while shared connection variables sometimes omit the subsidiary component entirely. This inconsistency is minor but means that automated tooling parsing variable names must account for optional segments rather than relying on a fixed number of underscore-delimited tokens.

### Variable Categories

The 957 variables can be grouped into functional categories based on their naming patterns:

**Environment prefixes** (`parameter_Env_Prefix`): Control which BigQuery dataset a job targets. Every region has at least one environment prefix variable. These are the most operationally critical variables because they determine whether a job reads from and writes to production or test data.

**Connection parameters** (`connection_*`): Store database hostnames, ports, database names, and authentication details for MSSQL, BigQuery, and Salesforce connections. These variables form the connectivity layer of the platform.

**Resource paths** (`resource_*`): Define file system paths for temp files, staging directories, credential files, and log outputs. These are environment-specific because production and test servers may have different directory structures.

**API configuration** (`parameter_*_oauth2*`, `parameter_*_audience`, `parameter_*_endpoint`): Store OAuth endpoints, client IDs, and API base URLs for Salesforce and Business Central integrations. These must differ between production and sandbox API instances.

**Business parameters** (`parameter_*_Company`, `parameter_*_Entity`, `parameter_*_Currency`): Store business-level constants like company identifiers, entity names, and default currencies. These typically do not vary between environments but are included in the context variable system for consistency.

---

## PROD/TEST Divergences

Of the 957 total context variables, **363 (38%)** have different values between PROD and TEST environments. This divergence rate is expected -- production and test environments should point to different databases, datasets, and API endpoints. However, the specific patterns of divergence reveal several risks that must be carefully managed during migration.

The divergence falls into predictable categories. The largest group consists of environment prefix variables, followed by database connection strings, and then API endpoint URLs. A smaller but more concerning group includes variables where the divergence pattern is inconsistent across the test environments (TEST, TEST_LINUX, TEST_MAC), suggesting configuration errors.

### Environment Prefix Pattern

The most common divergence pattern is the **environment prefix**, which controls which BigQuery dataset a job reads from or writes to. In production, a job targeting Brazil's raw layer uses the dataset `bra_vbr_raw`; in test, it uses `test_bra_vbr_raw`. This prefix-based separation is the platform's primary mechanism for isolating test and production data within the same BigQuery project.

**Example -- Brazil environment prefix (`BRA_PROBI_BC_environment.parameter_Env_Prefix`):**

| Environment | Value |
|-------------|-------|
| Default | *(empty)* |
| PROD | `bra_vbr_raw` |
| TEST | `test_bra_vbr_raw` |
| TEST_LINUX | `test_bra_vbr_raw` |
| TEST_MAC | `test_bra_vbr_raw` |

This pattern is clean and predictable. All three test environments resolve to the same prefix, ensuring consistent behavior regardless of which test machine runs the job. The `test_` prefix convention is uniform across all regions, making it straightforward for automated tooling to identify test datasets.

However, the approach of co-locating test and production datasets in the same BigQuery project carries inherent risk. A misconfigured environment variable or a job run without an environment override will land data in the wrong dataset. In a cloud-native migration, separate projects or accounts for test and production environments would provide stronger isolation.

### Inconsistent Case in Global Variables

The Global region introduces an inconsistency worth noting. The `Global_Clean.parameter_Env_Prefix` variable has the following values:

| Environment | Value |
|-------------|-------|
| Default | *(empty)* |
| PROD | `Global_Clean` |
| TEST | `test_global_clean` |
| TEST_LINUX | `Global_Clean` |
| TEST_MAC | `test_global_clean` |
| PROD_MAC | `Global_Clean` |

Here, **TEST_LINUX** resolves to `Global_Clean` (the production value) rather than `test_global_clean`. This means a test run on Linux infrastructure will read from and write to the production Global_Clean dataset. Whether this is intentional (Linux test runs validate against production data) or accidental (a misconfiguration that was never caught) is unclear from the code alone, but it represents a genuine risk of unintended production data modification during testing.

Additionally, the casing differs between environments: PROD uses `Global_Clean` (Pascal case) while TEST uses `test_global_clean` (snake case). BigQuery dataset names are case-sensitive, so these refer to physically different datasets. This casing inconsistency adds a subtle layer of complexity to any automated configuration validation.

### Database Name Divergences

Connection variables for MSSQL databases show clean separation between environments:

**Example -- USA Newport Labs MSSQL database (`connection_USA_NPL_MSSQL.connection_USA_NPL_MSSQL_Database`):**

| Environment | Value |
|-------------|-------|
| Default | *(empty)* |
| PROD | `BI360DW` |
| TEST | `BI360DW102622` |
| TEST_LINUX | `BI360DW102622` |
| TEST_MAC | `BI360DW102622` |

The test database name `BI360DW102622` appears to include a date suffix (October 26, 2022), suggesting it is a point-in-time snapshot of the production database used for testing. This is a common pattern, but it means test data may be significantly outdated relative to production, potentially masking issues that only manifest with current data volumes or schema changes.

This frozen-snapshot approach to test data is typical of on-premises MSSQL environments where creating fresh snapshots is operationally expensive. In a cloud-native migration, test databases can be provisioned as recent clones of production, reducing the staleness problem. The migration team should document the age and provenance of each test database to assess whether test results remain valid.

### API Endpoint Divergences

Salesforce OAuth endpoints diverge by environment in the expected way:

**Example -- USA Salesforce OAuth audience (`USA_Vaxxinova_Salesforce.parameter_USA_Vaxxinova_Salesforce_oauth2JwtFlow_audience`):**

| Environment | Value |
|-------------|-------|
| Default | *(empty)* |
| PROD | `https://login.salesforce.com` |
| TEST | `https://test.salesforce.com` |
| TEST_LINUX | `https://test.salesforce.com` |
| TEST_MAC | `https://test.salesforce.com` |
| PROD_MAC | `https://login.salesforce.com` |

This is the standard Salesforce pattern: production uses `login.salesforce.com` and sandboxes use `test.salesforce.com`. The PROD_MAC environment correctly mirrors the PROD endpoint, confirming that macOS production runs are intended to behave identically to standard production. All three test environments consistently use the sandbox endpoint, which is the expected configuration.

Salesforce sandbox environments have their own rate limits, storage quotas, and refresh schedules. The migration team should verify that the Salesforce sandbox referenced by TEST context variables is actively maintained (refreshed from production periodically) and that its API limits are sufficient for the test workload.

---

## Regional Distribution of Configuration Risk

The 957 variables are unevenly distributed across regions, and the risk profile varies significantly:

| Region | Variables | With Risk | Risk % | Environments | Key Systems |
|--------|-----------|-----------|--------|--------------|-------------|
| USA | 385 | 147 | 38% | 7 (incl. PROD_MAC) | MSSQL, BC, Salesforce, GCS |
| BRA | 248 | 64 | 26% | 6 | MSSQL, BC, Salesforce |
| Global | 144 | 96 | 67% | 7 (incl. PROD_MAC) | BigQuery, Salesforce |
| JPN | 138 | 42 | 30% | 6 | MSSQL, BC, Salesforce |
| DEU | 18 | 8 | 44% | 6 | BC (VDA), MSSQL (VDD) |
| ITA | 14 | 3 | 21% | 6 | MSSQL, Salesforce |
| SAF | 10 | 3 | 30% | 6 | MSSQL |

Several observations emerge from this distribution:

**USA dominates the variable count** with 385 variables (40% of the total), reflecting its two subsidiaries (VUS and NPL) and its complex mix of MSSQL, Business Central, Salesforce, and Google Cloud Storage integrations. The USA region connects to more external systems than any other region, and each connection requires its own set of environment-specific variables for hostnames, ports, credentials, and API endpoints.

**Global has the highest risk percentage** at 67%. Nearly two-thirds of Global variables have PROD/TEST divergences, which is consistent with the Global region's role as the cross-regional consolidation layer. The Global region references datasets from every other region (to build the consolidated Sales_Set) and connects to multiple Salesforce objects (accounts, products, budgets, conversion rates). Each of these cross-regional references requires its own PROD/TEST differentiation.

**Brazil has a relatively low risk percentage** (26%) despite being the second-largest region by variable count. This suggests that many of Brazil's 248 variables are shared constants (entity names, currency codes, product prefixes) that do not change between environments. The constants still consume configuration space but do not require environment-specific management.

**Germany and Italy represent the extremes of simplicity.** Germany has only 18 variables but a 44% risk rate, reflecting the dual-entity structure (VDA for Business Central, VDD for legacy) that doubles the connection-level configuration. Italy has 14 variables with only a 21% risk rate, consistent with its streamlined single-entity pipeline.

**SAF has 10 variables** despite having only 1 job (a copy of Italy's raw extraction). This suggests the variables were provisioned in anticipation of a full South African pipeline that has not yet been built. The 3 risky variables are likely connection parameters for the South African MSSQL source that differ between environments.

---

## Credential Management

The static analysis scanner detected credential-related variables throughout the platform, but their values have been replaced with `[CREDENTIAL_PATH]` placeholders to prevent accidental exposure. This filtering is applied at scan time, meaning the actual credential values are never captured in the documentation output.

Credentials in the Talend platform are managed through two mechanisms:

### Context-Based Secret References

Sensitive values such as database passwords, API keys, and OAuth client secrets are stored as context variables whose values differ per environment. The variable naming convention makes these identifiable: names containing `Password`, `Secret`, `oauth2`, or `Key` are typically credentials. For example, `USA_Vaxxinova_Salesforce.parameter_USA_Vaxxinova_Salesforce_oauth2JwtFlow_audience` is a credential-adjacent variable that controls the OAuth endpoint, while the actual client secret would be stored in a corresponding `_secret` or `_key` variable.

Context-based credentials are embedded in the Talend project's XML files. While Talend encrypts these values in the project file, the encryption is reversible by anyone with access to the Talend workspace. The credentials are decrypted at runtime and passed to connection components in plaintext, meaning they may appear in debug logs, error messages, or memory dumps if proper precautions are not taken.

### File-Based Credential Loading

Several jobs (particularly in the BRA region) load credentials from flat files on the execution server at runtime using `tFileInputDelimited` components. The `BC_Secret_Key_Context` file is referenced across all 23 raw extraction jobs in BRA_5, BRA_6, and BRA_7. This approach has been flagged as a security concern (see `11_KNOWN_ISSUES.md`, High severity) because the credentials are stored in plaintext with no encryption at rest, and their security depends entirely on file system permissions.

The file-based approach does have one advantage over context-based credentials: credential rotation does not require redeploying the Talend job. An operator can update the credential file on the server, and the next job execution will pick up the new value. However, this advantage is outweighed by the security risks: no audit trail, no access controls beyond file permissions, no encryption, and no automated rotation.

### Credential Scope and Sharing

Credentials appear to be shared across jobs within a region. The USA Salesforce OAuth credentials, for instance, are used by both VUS and NPL jobs. The Business Central API token (created by the `Create_Business_Central_Token` component in the GENERIC region) is shared across all regions that extract from Business Central. This sharing is efficient but creates a single point of failure: if a shared credential expires or is rotated, every job that depends on it will fail simultaneously.

Neither mechanism meets modern security standards. Both store secrets in locations accessible to anyone with server access, and neither supports automated rotation, audit logging, or access controls beyond the operating system's file permissions.

---

## Migration Takeaways

### 1. Map Context Variables to the Target Platform's Configuration Management

The 957 context variables represent the complete configuration surface of the platform. Each variable must be mapped to an equivalent in the target system -- whether that is environment variables, configuration files, a secrets manager, or a combination. The naming convention (`{region}_{subsidiary}_{domain}.parameter_{name}`) provides a natural taxonomy that can be preserved or simplified.

A practical approach is to create a configuration registry: a spreadsheet or database that maps each Talend context variable to its target-platform equivalent, along with its PROD value, TEST value, and the responsible team. This registry serves as both a migration checklist and an ongoing configuration reference.

### 2. Validate All 363 PROD/TEST Divergences During Migration

Every variable where PROD and TEST values differ represents a configuration decision that must be explicitly carried forward. The Global_Clean environment prefix inconsistency (TEST_LINUX resolving to the production value) is a concrete example of a divergence that could cause production data corruption if replicated without review.

The validation should include three checks for each divergent variable: (a) confirm the divergence is intentional, (b) verify the test value points to a valid and current test resource, and (c) ensure the divergence pattern is consistent across all test environments (TEST, TEST_LINUX, TEST_MAC).

### 3. Migrate Credentials to a Secrets Vault

The current mix of context-variable credentials and flat-file credential loading should be replaced with a centralized secrets manager such as Azure Key Vault, AWS Secrets Manager, or HashiCorp Vault. This migration should enforce:

- Encrypted storage at rest and in transit
- Automated rotation policies with configurable intervals
- Access audit trails recording who accessed which secret and when
- Least-privilege access controls tied to service identities rather than server-level permissions
- Secret versioning to support rollback without exposing old credentials

The 23 BRA raw extraction jobs that load credentials from `BC_Secret_Key_Context` files are the highest-priority candidates for vault migration, followed by the Salesforce OAuth credentials shared across regions.

### 4. Eliminate Platform-Specific Environments

The existence of TEST_LINUX, TEST_MAC, and PROD_MAC environments reflects historical heterogeneity in the development and execution infrastructure. A cloud-native migration should target a single execution platform, reducing the environment matrix from seven entries to three (default, production, test) and eliminating an entire class of configuration drift. If platform-specific behavior is needed (for local development, for example), it should be handled through platform detection at runtime rather than through pre-configured environment variants.

### 5. Centralize Environment Prefix Logic

The `parameter_Env_Prefix` pattern -- where every region independently defines a prefix that selects the correct BigQuery dataset -- should be replaced with a single, centrally managed environment resolution mechanism. This could be as simple as a naming convention enforced by the deployment pipeline (e.g., all test datasets are prefixed with `test_`) or as sophisticated as a service discovery layer that resolves dataset names at runtime based on the deployment context.

Centralizing this logic eliminates the risk of inconsistencies like the Global_Clean TEST_LINUX misconfiguration. It also simplifies onboarding new regions: instead of creating a full set of environment-specific prefix variables, a new region inherits the platform-wide naming convention automatically.

### 6. Document the Variable-to-Config Mapping

During migration, create a machine-readable mapping file (CSV or JSON) that maps each of the 957 Talend context variables to its target-platform equivalent. This mapping should include the Talend variable name, the target configuration key, the PROD value, the TEST value, a flag indicating whether the variable contains a credential, and the name of the team responsible for validating the value. This registry serves triple duty: as a migration checklist, as a configuration reference for the operations team, and as an audit artifact demonstrating that every configuration point was deliberately reviewed during the transition.

The mapping should be reviewed by region owners before the migration cutover. Each region owner should verify that their region's variables point to the correct target resources and that no stale references (such as the October 2022 test database snapshot for USA NPL) have been carried forward without validation.

---

*Generated by intodoc -- documentation automation for integration platforms.*
