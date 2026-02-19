# External Systems

Vaxxinova's Talend integration platform does not operate in isolation. It connects 257 ETL jobs to 11 unique external systems that collectively form the data backbone of a multinational veterinary vaccine business. These systems range from on-premises Microsoft SQL Server databases serving as regional ERP backends, to Google BigQuery as the central cloud data warehouse, to Salesforce CRM for sales team enablement, to REST API endpoints for newer Business Central subsidiaries. Understanding the full external system landscape is essential for migration planning: every connection must be re-established, every credential re-provisioned, and every network path validated in whatever target platform replaces Talend.

## System Inventory

The following table summarizes all 11 external systems detected across the codebase, ordered by the number of jobs that reference them.

| System | Type | Criticality | Regions | Job Count |
|--------|------|-------------|---------|-----------|
| Google BigQuery | Database | Medium | BRA, DEU, GENERIC, Global, ITA, JPN, SAF, USA, ZAF, scratchpad | 35 |
| File System (GCS/local) | Cloud | Medium | BRA, DEU, GENERIC, Global, ITA, JPN, SAF, USA, ZAF, scratchpad | 34 |
| REST API | Cloud | Medium | BRA, GENERIC, JPN, USA | 10 |
| Microsoft SQL Server (generic) | Database | Medium | BRA, ITA, JPN, SAF, USA, ZAF, scratchpad | 6 |
| connection_JPN_VJP_MSSQL | Microsoft SQL Server | High | JPN, scratchpad | 3 |
| connection_ITA_VAX_MSSQL | Microsoft SQL Server | High | ITA, SAF, ZAF | 2 |
| FTP/SFTP | Cloud | Medium | USA | 1 |
| connection_BRA_VBR_MSSQL | Microsoft SQL Server | High | BRA | 1 |
| connection_JDBC_Google_BigQuery | JDBC | High | scratchpad | 1 |
| connection_SAF_VSA_MSSQL | Microsoft SQL Server | High | ZAF | 1 |
| connection_USA_NPL_MSSQL | Microsoft SQL Server | High | USA | 1 |

Six of the 11 systems are rated high criticality -- all of them named, parameterized database connections to specific regional MSSQL instances or the JDBC BigQuery connector. The remaining five (BigQuery via native connector, File System, REST API, generic MSSQL, and FTP/SFTP) are rated medium, though BigQuery's ubiquity across all 10 regions makes it arguably the most critical system on the platform.

## Google BigQuery: The Central Data Warehouse

BigQuery is the gravitational center of the Vaxxinova data platform. It appears in 35 of 257 jobs and is referenced by every single region, from the smallest (SAF with 1 job) to the largest (BRA with 75 jobs). BigQuery serves three distinct roles in the architecture:

1. **Raw landing zone.** Regional extraction jobs write source system data into BigQuery datasets prefixed with `raw` (e.g., `bra_vbr_raw`, `jpn_vjp_raw`). These datasets hold minimally transformed copies of ERP views, Business Central API responses, and flat file contents.

2. **Clean transformation layer.** The majority of job logic consists of BigQuery-to-BigQuery transformations using `tBigQueryInput`, `tMap`, and `tBigQueryOutput` components. Reference data tables -- Fiscal_Periods, Country_Master, Salesforce_Country_Mappings, Budget_Current_Conversion_Rate -- live in global BigQuery datasets and are joined into regional flows during the clean phase.

3. **Global consolidation.** The Global region's jobs read from regional clean datasets and produce the unified `Sales_Set` table that drives management dashboards and budget-vs-actual reporting across all subsidiaries.

BigQuery is accessed through two distinct connector mechanisms. The native `tBigQueryInput`/`tBigQueryOutput` components are used in production jobs and account for the bulk of BigQuery interactions. A secondary JDBC-based connection (`connection_JDBC_Google_BigQuery`) exists in the scratchpad area, likely used for testing or ad-hoc queries. The JDBC connector is rated high criticality because it stores connection metadata differently from the native components, and both pathways must be accounted for during migration.

BigQuery service account keys are managed through Talend context variables rather than hardcoded in job definitions. However, the exact key rotation and access control policies are not visible in the Talend metadata -- this is a gap that must be addressed during migration planning.

## MSSQL: On-Premises ERP Databases

Five named MSSQL connections serve as the primary source systems for regional subsidiaries, each connecting to an on-premises ERP database:

- **connection_BRA_VBR_MSSQL** -- Brazil (Biovet/VBR), extracting product, sales, and returns data via SQL Server views (`VW_Finished_Product`, `VW_Sales`, `VW_Returns`)
- **connection_ITA_VAX_MSSQL** -- Italy (VIT), used for raw invoice extraction; notably also referenced by SAF and ZAF jobs, since South Africa's pipeline was initially cloned from Italy's
- **connection_JPN_VJP_MSSQL** -- Japan (VJP), feeding sales invoices, credit memos, and general ledger entries into the raw layer
- **connection_SAF_VSA_MSSQL** -- South Africa (VSA), extracting invoice details via the `vw_InvoiceDetails` view
- **connection_USA_NPL_MSSQL** -- United States (Newport Labs legacy entity), used for customer account data extraction

Each connection follows a consistent naming convention: `connection_{REGION}_{ENTITY}_{TYPE}`. Host addresses are parameterized through context variables -- for example, `connection_BRA_VBR_MSSQL` resolves its server address from `context.connection_BRA_VBR_MSSQL_Server`. This parameterization means no IP addresses or hostnames are embedded directly in job definitions, which is good practice but also means the migration team must extract actual connection details from the Talend context configuration files or runtime environment.

A sixth generic "Microsoft SQL Server" entry (medium criticality, 6 jobs) represents MSSQL connections that use inline configuration rather than named connection metadata. These appear across BRA, ITA, JPN, SAF, USA, ZAF, and scratchpad regions, likely representing jobs where the developer configured the database connection directly in the component properties rather than referencing a shared connection definition.

### MSSQL Connection Details

Each of the five named MSSQL connections has distinct characteristics that affect migration planning. The following expanded profiles capture what the Talend metadata reveals about each connection's scope, usage pattern, and risk profile.

**connection_BRA_VBR_MSSQL -- Brazil (Biovet/VBR)**
This connection serves Brazil's legacy ERP extraction pipeline. It is referenced by 1 named job in the connection metadata but is implicitly used by the broader BRA raw extraction family (`BRA_VBR_RAW` and related jobs) through generic MSSQL component configuration. The connection extracts from SQL Server views (`VW_Finished_Product`, `VW_Sales`, `VW_Returns`) that provide pre-joined, denormalized projections of the underlying ERP tables. This view-based extraction pattern is significant: it means the Talend jobs do not need to understand the ERP's internal schema complexity; the views abstract it away. During migration, the target platform must either continue reading from these same views or negotiate equivalent access to the underlying tables. The Brazil MSSQL server hosts the Biovet subsidiary's operational data, making this connection time-sensitive -- extraction windows must avoid peak transactional hours to minimize lock contention on the source database.

**connection_ITA_VAX_MSSQL -- Italy (VIT), shared with SAF and ZAF**
This is the only MSSQL connection shared across multiple regions. Italy's raw extraction jobs use it directly, but South Africa's ZAF pipeline also references `connection_ITA_VAX_MSSQL` rather than SAF's own connection. This cross-region sharing indicates that ZAF's data physically resides on Italy's on-premises server infrastructure -- either because South Africa's ERP instance is hosted in Italy, or because the data is replicated there. The shared connection creates a dependency: network maintenance or credential rotation on Italy's server simultaneously affects both ITA and ZAF pipelines. The connection extracts raw invoice data used for both Italy's `VIT_Clean_Sales_Set` construction and ZAF's invoice detail processing. During migration, the team must determine whether ZAF should continue routing through Italy's infrastructure or establish a direct connection to South Africa's source system.

**connection_JPN_VJP_MSSQL -- Japan (VJP)**
Japan's MSSQL connection feeds the monolithic `JPN_VJP_RAW` extraction job, which pulls sales invoices, credit memos, general ledger entries, items, and customers in a single consolidated run. The connection is referenced by 3 jobs across the JPN and scratchpad regions (the scratchpad reference likely being a development copy). Japan's pipeline has the highest tMap density of any region (128 instances across 36 jobs), meaning the data extracted through this single MSSQL connection fans out into exceptionally complex downstream transformations. The connection's criticality is amplified by Japan's complete lack of error handling -- if the MSSQL server is unreachable, the monolithic extraction fails silently, and the entire Japan pipeline processes stale data.

**connection_SAF_VSA_MSSQL -- South Africa (VSA)**
Despite being named as a South Africa connection, this connection is referenced by ZAF region jobs rather than SAF. The SAF region's single job (`VSA_Raw_InvoiceDetails`) uses `tMSSqlInput` directly with inline configuration, bypassing the named connection entirely. This naming inconsistency -- where SAF's named connection is used by ZAF and SAF's actual job uses inline config -- is a metadata hygiene issue that the migration team must untangle. The connection extracts from the `vw_InvoiceDetails` view, which provides a denormalized projection of invoice header and line data. The VSA entity represents Vaxxinova South Africa's operational data.

**connection_USA_NPL_MSSQL -- United States (Newport Labs)**
This connection serves the Newport Labs legacy entity within the USA region. NPL is one of two US subsidiaries (the other being VUS for Vaxxinova US), and its MSSQL connection extracts customer account data that feeds into `NPL_Clean_Account_v4`. The connection is used by 1 job in the metadata, making it the least-referenced named connection. However, NPL customer data flows into the USA Sales_Set construction, which in turn feeds Global consolidation, so the connection's downstream impact extends well beyond its single direct consumer. The "v4" version suffix on the consuming job indicates that the extraction has been iterated multiple times, yet no version has addressed the underlying error handling gap.

### Migration consideration: network connectivity

All MSSQL connections target on-premises servers behind corporate firewalls. Any cloud-based migration target must establish secure network paths to these servers -- whether through VPN tunnels, Azure ExpressRoute, AWS Direct Connect, or a hybrid gateway architecture. This is often the longest-lead-time item in a migration project and should be addressed early in the planning phase.

## Salesforce: CRM Integration

Salesforce CRM integration is embedded in the platform through product synchronization and account mapping jobs. While Salesforce does not appear as a standalone entry in the top systems list (its connections are configured inline within components rather than as named connection metadata), it is referenced in several critical jobs:

- **BIO_Sync_Product_Salesforce_v1** (BRA) -- syncs vaccine product master data from BigQuery to Salesforce's `Product2` object, including species, subspecies, hierarchy, and doses fields. Products are namespaced with a `BIO-` prefix to distinguish Biovet products in the global Salesforce instance.
- **IZO_Sync_Product_Salesforce_v5** (ITA) -- equivalent product sync for Italy, at version 5, indicating substantial iteration.
- **VUS_Clean_BC_Item_v8_Salesforce** (USA) -- pushes product master data bi-directionally into Salesforce using `tSalesforceConnection` and `tSalesforceOutput` components.

Beyond direct Salesforce writes, nearly every regional Sales_Set construction job reads from `Global_Raw.Salesforce_Account_Mappings` and `Global_Raw.Salesforce_Country_Mappings` tables in BigQuery. These reference tables link Business Central customer IDs to Salesforce Account records, enabling CRM-integrated sales analytics. The data flows one direction (BigQuery to Salesforce for product sync; Salesforce to BigQuery for account/country mappings), making this a bidirectional but asymmetric integration.

Salesforce OAuth credentials will need to be re-provisioned in the target platform. The current authentication mechanism uses Talend's built-in Salesforce components, which handle OAuth token management internally. A migration target that uses raw REST calls or a different connector library will need to implement its own OAuth flow against Salesforce's connected app configuration.

## REST APIs: Business Central OData Endpoints

Ten jobs across four regions (BRA, GENERIC, JPN, USA) consume REST API endpoints, primarily targeting Microsoft Dynamics 365 Business Central's OData v4 protocol. These jobs represent the newer extraction pathway for subsidiaries that have migrated from legacy MSSQL-based ERPs to Business Central.

The GENERIC region houses the reusable template jobs that power most of these integrations. The `GEN_BC_*` family of jobs provides parameterized extractors for core Business Central entities:

- **GEN_BC_SalesInvoices** / **GEN_BC_SalesInvoices_oData** -- sales invoice header extraction via direct API and OData variants
- **GEN_BC_SalesCreditMemos** -- credit memo extraction
- **GEN_BC_Items** / **GEN_BC_Item_oData** -- product/item master data
- **GEN_BC_Customers_oData** -- customer records

These template jobs use a sophisticated API pagination and response-parsing architecture built from `tRESTClient` (13 instances across GENERIC), `tExtractJSONFields` (17 instances), and `tJavaFlex` (13 instances) components. A dedicated `Create_Business_Central_Token` component handles OAuth authentication against Azure AD before each API call.

Regional jobs invoke these GENERIC templates by setting context variables for entity name, API endpoint URL, and BigQuery target dataset. This is why, for example, Germany's VDA (Business Central) raw extraction jobs do not appear in the DEU region folder -- they call the GENERIC BC jobs with VDA-specific parameters.

The FTP/SFTP system (1 job, USA only) supports the Animalytix third-party distributor data ingestion pipeline, which retrieves files from an external SFTP server via Google Cloud Storage (`tGSGet` component).

## Connection Parameterization

All external system connections use Talend context variables for parameterization. This is one of the platform's strongest architectural decisions, enabling the same job definitions to run against different environments (PROD, TEST, TEST_LINUX, TEST_MAC) by swapping context variable files.

The parameterization follows a consistent pattern visible in the connection metadata:

```
connection_BRA_VBR_MSSQL  -->  host: context.connection_BRA_VBR_MSSQL_Server
connection_ITA_VAX_MSSQL  -->  host: context.connection_ITA_VAX_MSSQL_Server
connection_JPN_VJP_MSSQL  -->  host: context.connection_JPN_VJP_MSSQL_Server
connection_SAF_VSA_MSSQL  -->  host: context.connection_SAF_VSA_MSSQL_Server
```

Each connection defines not just a server address but also database name, port, username, and password as separate context variables. This means the full connection string is assembled at runtime from multiple context parameters, and the migration team must capture all of them -- not just the host -- to replicate connectivity.

The JDBC BigQuery connection (`connection_JDBC_Google_BigQuery`) is an exception: its host field is empty in the metadata, indicating that BigQuery JDBC connections use a connection string format that embeds project and dataset information rather than a traditional host/port model.

## Connection Parameterization Patterns

The parameterization architecture deserves closer examination because it is one of the few areas where the Vaxxinova platform demonstrates genuine engineering discipline. Understanding how context variables govern connection behavior is essential for any migration that must replicate the same multi-environment flexibility.

### How Context Variables Work for Connections

In Talend, context variables are runtime-resolved placeholders that allow the same job definition to target different environments without code changes. Each connection's properties (server address, port, database name, username, password) are individually parameterized. At runtime, the Talend engine loads a context file that maps each variable name to a concrete value.

For example, the Brazil MSSQL connection resolves as follows:

```
connection_BRA_VBR_MSSQL:
  Server   -> context.connection_BRA_VBR_MSSQL_Server      (e.g., "10.1.2.30" in PROD, "10.1.2.31" in TEST)
  Port     -> context.connection_BRA_VBR_MSSQL_Port        (e.g., "1433")
  Database -> context.connection_BRA_VBR_MSSQL_Database     (e.g., "BiovetERP")
  Username -> context.connection_BRA_VBR_MSSQL_Login        (e.g., "talend_svc")
  Password -> context.connection_BRA_VBR_MSSQL_Password     (encrypted at rest)
```

This means a single job definition can target production, test, or development databases by simply loading a different context file. The platform uses at least five named contexts: `PROD`, `TEST`, `TEST_LINUX`, `TEST_MAC`, and `PROD_MAC`. Each context file contains the full set of connection parameters for every external system, allowing the entire platform to switch environments atomically.

### Environment-Specific Context Files

The existence of five environment contexts reveals something about the platform's operational infrastructure. The `PROD_MAC` and `TEST_MAC` variants suggest that at least some Talend jobs run on macOS machines -- an uncommon but not unheard-of configuration for development environments. The `TEST_LINUX` context points to a Linux-based test environment, likely a staging server. The migration team should inventory all context files and their contents to build a complete connection map across environments.

### BigQuery Dataset Parameterization

Beyond MSSQL connections, the platform also parameterizes BigQuery dataset names through context variables. Jobs reference datasets like `context.parameter_BRA_Raw_Env` and `context.parameter_JPN_Raw_Env` rather than hardcoding `bra_vbr_raw` or `jpn_vjp_raw`. This allows the same transformation logic to read from and write to test or production BigQuery datasets depending on the loaded context. However, not all jobs follow this pattern -- several older jobs (the non-"Copy_of" variants) hardcode dataset names directly, creating a mix of parameterized and non-parameterized BigQuery references that the migration must reconcile.

### Implications for Migration

The context variable system is conceptually equivalent to environment variables, Kubernetes secrets, or platform-specific secret stores (Azure Key Vault, GCP Secret Manager). During migration, the team must: (a) extract the actual values from each Talend context file for every environment, (b) map each context variable to the target platform's equivalent parameterization mechanism, and (c) ensure that the multi-environment switching capability is preserved. The variable naming convention (`connection_{REGION}_{ENTITY}_{TYPE}_{PROPERTY}`) is well-structured and could be reused directly as key names in a target secret store, preserving organizational clarity.

## File System and Cloud Storage

Beyond databases, APIs, and CRM systems, the platform makes extensive use of the local file system and Google Cloud Storage (GCS) as intermediate staging areas. The File System / GCS entry appears in 34 of 257 jobs -- nearly as prevalent as BigQuery itself -- and spans all 10 regions.

File system usage falls into three categories:

**Temporary CSV staging.** Many jobs write intermediate data to CSV files on the Talend runtime host's local disk, then read those files back for subsequent processing steps. This pattern is especially common in the GENERIC Business Central extraction jobs, where API responses are parsed into JSON, written to a temporary file, then read back via `tFileInputDelimited`. The `cleanup_temp_file_storage` joblet exists specifically to remove these temporary files after job completion, but only 5 of 10 regions include it, meaning the three largest regions (BRA, USA, JPN) accumulate temporary files without guaranteed cleanup.

**Google Cloud Storage staging.** The USA region's Animalytix pipeline uses `tGSGet` to retrieve files from Google Cloud Storage (originally delivered to an SFTP server and transferred to GCS). This is the platform's only GCS integration beyond BigQuery, and it represents a file-based ingestion pattern that differs from the database and API extraction patterns used elsewhere.

**Context and configuration files.** Talend context files (containing environment-specific connection parameters) are loaded from the local file system during the tPrejob phase. Business Central secret keys are also stored in local files and loaded at runtime. These file system dependencies must be accounted for in the migration: the target platform must provide equivalent secure file storage or replace file-based configuration with a secrets manager.

## Region System Density

The number of external system references varies significantly by region, reflecting both pipeline maturity and complexity:

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

BRA and USA lead with 20 system references each, reflecting their status as the most complex regional pipelines with multiple ERP backends, Business Central APIs, Salesforce integrations, and file-based data sources. Germany (DEU) has just 2 references because its Business Central extraction is handled by the GENERIC template jobs rather than region-specific connections.

The Global region has 16 references but zero high-criticality connections -- it operates entirely within BigQuery, reading from regional datasets and writing to consolidated tables. It never touches source systems directly, which is architecturally sound but means any upstream extraction failure silently propagates stale data into global reports.

## Credential and Authentication Landscape

Each external system type uses a different authentication mechanism, and all of them must be reprovisioned in the migration target. The following summarizes the authentication landscape by system type.

**MSSQL: SQL Server Authentication.** All five named MSSQL connections use SQL Server authentication (username + password), stored as encrypted context variables. The passwords are encrypted by Talend's built-in encryption utility, which uses a project-specific master key. During migration, the actual plaintext passwords must be extracted (either from Talend's encryption store or by requesting fresh credentials from the database administrators) and stored in the target platform's secrets manager. If the migration timeline is long, coordinate with the DBA team to ensure credentials are not rotated mid-migration.

**BigQuery: Service Account Keys.** BigQuery access is authenticated via Google Cloud service account JSON key files. These keys are referenced through Talend context variables but the actual key files reside on the Talend runtime host's file system. The migration must locate these key files, verify their project and role bindings in Google Cloud IAM, and re-provision them in the target platform. If the target platform runs on GCP (e.g., Dataflow, Cloud Composer), Workload Identity Federation may eliminate the need for service account keys entirely.

**Salesforce: OAuth 2.0 Connected App.** Salesforce integration uses Talend's built-in Salesforce components, which manage OAuth token acquisition and refresh internally. The underlying OAuth credentials (client ID, client secret, refresh token) are configured within Talend's component properties. The migration target must either use Salesforce's connected app configuration directly (if it has a native Salesforce connector) or implement the OAuth 2.0 flow manually (if it uses raw REST calls). The connected app must be scoped for both read (account/country mapping queries) and write (Product2 upsert) operations.

**Business Central: Azure AD OAuth 2.0.** The GENERIC `GEN_BC_*` extraction jobs authenticate against Azure Active Directory to obtain bearer tokens for Business Central API calls. The `Create_Business_Central_Token` component in the tPrejob phase handles this flow. The migration must capture the Azure AD tenant ID, client ID, and client secret for the registered application, and replicate the OAuth client credentials flow in the target platform.

**SFTP: Key-Based or Password Authentication.** The single SFTP connection (USA region, Animalytix feed) uses credentials stored in Talend context variables. The migration must determine whether the SFTP server uses password or SSH key authentication and provision the appropriate credentials in the target platform.

## Migration Takeaways

1. **Map every connection type to the target platform's connector library.** The current platform uses five distinct connector families: native BigQuery components, JDBC BigQuery, MSSQL native components, Salesforce components, and REST/HTTP clients. Each must have a validated equivalent in the migration target.

2. **Solve on-premises network connectivity early.** The five MSSQL connections require secure network paths from the target platform to on-premises servers. This is typically a 4-8 week infrastructure task and should not be deferred to the implementation phase.

3. **Re-provision Salesforce OAuth credentials.** Talend's built-in Salesforce components manage OAuth internally. The target platform will need a Salesforce connected app configuration with appropriate scopes for both reading (account/country mappings) and writing (product sync via upsert).

4. **Migrate BigQuery service account keys to a target secret store.** Current keys are referenced through context variables but stored in Talend's context configuration. These must move to a centralized secrets manager (Azure Key Vault, GCP Secret Manager, AWS Secrets Manager) in the target architecture.

5. **Preserve the GENERIC template pattern.** The parameterized Business Central extraction templates are one of the platform's best architectural features. Any migration should replicate this pattern rather than creating region-specific copies of API extraction logic.

6. **Validate both BigQuery connector paths.** The native and JDBC BigQuery connections behave differently. The scratchpad JDBC connector must be evaluated to determine whether it is needed in production or can be retired.

7. **Plan for file system dependencies.** The 34 jobs that reference file system or GCS paths must have their file I/O patterns mapped to the target platform. Temporary CSV staging may be eliminated entirely if the target platform supports in-memory or streaming transformations. GCS-based file ingestion (Animalytix) needs equivalent cloud storage connectivity.

8. **Document all credential lifetimes and rotation schedules.** MSSQL passwords, BigQuery service account keys, Salesforce OAuth tokens, and Business Central client secrets all have different lifetimes and rotation policies. The migration team should create a credential inventory that captures: credential type, owning team, rotation frequency, and expiration date. This prevents credential-related failures during the migration cutover period.

---

*Generated by intodoc -- documentation automation for integration platforms.*
