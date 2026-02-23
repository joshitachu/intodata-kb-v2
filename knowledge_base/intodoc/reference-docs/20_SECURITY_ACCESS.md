# Security & Access

The Vaxxinova Talend integration platform handles credentials for on-premises databases, cloud data warehouses, CRM systems, and REST APIs across seven regional subsidiaries. A security analysis of the codebase identified 190 findings, all classified as credential exposure at medium severity. The good news: no secrets are hardcoded directly in job definitions. Every credential is managed through Talend context variables, which externalize sensitive values from the job logic. The concerning news: password-type context variables are detected across every region, no role-based access control (RBAC) governs who can run which jobs or access which data, no row-level security restricts data visibility by region or role, and no audit logging tracks who executed what and when. The platform's security posture is functional but minimal -- adequate for a small team with implicit trust, but insufficient for enterprise governance standards.

## Finding Summary

The security scan produced 190 total findings. Every single one falls into the `credential_exposure` category at `medium` severity. No `high` or `critical` severity findings were detected, which reflects the consistent use of context variables rather than inline credentials. No findings related to injection vulnerabilities, data leakage, or insecure protocols were flagged -- the scope of the security analysis is limited to what is observable in the Talend job metadata and context configuration files.

| Category | Count | Severity |
|----------|-------|----------|
| Credential Exposure | 190 | Medium |

The 190 findings represent individual password-type context variable declarations detected across the platform's context groups. Each finding corresponds to a specific parameter that stores a credential value -- database passwords, API keys, Salesforce tokens, and service account credentials.

## Credential Management: Talend Context Variables

Talend's context variable system is the platform's primary credential management mechanism. Rather than embedding database passwords or API keys directly in job component configurations, developers reference context variables that resolve at runtime. This is a meaningful architectural decision: it means job XML files committed to version control do not contain plaintext secrets, and the same job can connect to different environments (PROD, TEST, TEST_LINUX, TEST_MAC) by swapping context variable sets.

However, the context variable system has significant limitations compared to a dedicated secrets manager:

**No encryption at rest.** Talend context variable files store values as plaintext or with reversible encoding. While the variable declarations in the job metadata use a "password" type flag (which masks the value in the Talend Studio UI), the underlying storage is not encrypted. Anyone with access to the Talend project repository can extract credential values from context configuration files.

**No centralized rotation.** Each connection's password is defined independently. Rotating a database password requires updating the context variable in every environment where it is defined (PROD, TEST, etc.) and for every job that references it. There is no centralized credential store that propagates changes automatically.

**No access control on variables.** Any developer with access to the Talend project can read, modify, or create context variables for any region. There is no mechanism to restrict Brazil team members from viewing Japan's database credentials, or to prevent a junior developer from modifying production connection parameters.

## Security Posture by Region

Each region presents a distinct security profile driven by the number of external system connections it maintains, the types of authentication used, and the sensitivity of the data it processes. The following analysis assesses each region's security posture individually.

### USA (105 findings -- 55% of total)

The United States region has the most complex and exposed security surface. Its 105 credential findings span four distinct external systems: MSSQL (Newport Labs legacy ERP), Business Central (VUS subsidiary financials), Salesforce (CRM synchronization), and SFTP (Animalytix distributor data). Each system requires multiple credential parameters (username, password, host, security token, OAuth secret), and each environment variant (PROD, TEST, TEST_GC) defines its own set. The USA region's credential proliferation is a direct consequence of its integration complexity -- it connects to more external systems than any other region.

Key concerns for USA:
- The Salesforce integration uses username-password authentication (`parameter_USA_Vaxxinova_Salesforce_userPassword`), tying the integration to a specific user account. If that user leaves the organization or changes their password, the integration breaks.
- The `_GC` suffix on some context groups (e.g., `USA_VUS_environment_GC`) suggests Google Cloud-specific credential sets, meaning credentials are duplicated across deployment targets.
- The SFTP connection for Animalytix data uses a separate credential set that is likely not subject to the same rotation policies as the database credentials (if any rotation policies exist at all).

### BRA (48 findings -- 25% of total)

Brazil's 48 findings reflect its dual-pipeline architecture: legacy MSSQL-based BioVet extraction and newer Business Central API integration. Two separate context groups -- `BRA_PROBI_BC_environment` and `BRA_VBR_BC_environment` -- each contain a `parameter_bc_password`, meaning two independent Business Central credentials must be managed. The MSSQL connection adds a third credential set. Brazil's security concern is primarily credential sprawl: three separate credential sets for two source systems, with no centralized management.

Key concerns for BRA:
- The dual Business Central credential sets (`PROBI` and `VBR`) increase the rotation burden and the probability that one will be missed during a rotation cycle.
- Brazil's LGPD (Lei Geral de Protecao de Dados) regulation imposes data protection requirements that the current platform cannot enforce -- there is no mechanism to restrict access to Brazilian citizen data or to audit who has accessed it.

### JPN (24 findings -- 13% of total)

Japan's 24 findings are concentrated entirely around its MSSQL connection to the VJP ERP database (`connection_JPN_VJP_MSSQL`). This single connection is the gateway to all of Japan's financial and sales data. The credential simplicity is a strength (only one credential set to manage) but also a risk: a single compromised credential exposes all of VJP's data.

Key concerns for JPN:
- Japan's APPI (Act on the Protection of Personal Information) regulation requires organizations to implement "necessary and appropriate" measures for personal information protection. The current platform's lack of access controls and audit logging may not meet this standard.
- The MSSQL connection does not show evidence of SSL/TLS encryption in the job metadata, though this may be configured at the infrastructure level.

### Global (8 findings -- 4% of total)

Global's 8 findings likely represent BigQuery service account credentials and shared reference data access parameters. Since Global operates as the consolidation layer, its credentials provide access to cross-entity data -- potentially the most sensitive access scope in the platform.

Key concerns for Global:
- A compromised Global credential provides access to data from all regions, making it the highest-impact single point of compromise.
- Service account credentials for BigQuery may have overly broad IAM permissions (e.g., BigQuery Admin rather than BigQuery Data Viewer), though this cannot be determined from the Talend metadata alone.

### DEU (2 findings -- 1% of total)

Germany's 2 findings represent Business Central credentials for the VDA subsidiary. The minimal credential footprint is a strength, but GDPR compliance requirements make even these 2 credentials high-stakes: unauthorized access to German employee or customer data could trigger regulatory notification obligations.

### ITA (2 findings -- 1% of total)

Italy's 2 findings represent MSSQL credentials, following the same pattern as other regions with on-premises ERP connections. GDPR applies to Italian data with the same force as German data.

### SAF (1 finding -- <1% of total)

South Africa's single finding reflects its nascent pipeline -- a single cloned extraction job with one MSSQL credential. The security surface is minimal, but the POPIA (Protection of Personal Information Act) regulation imposes data protection requirements similar to GDPR.

## Authentication Methods

The platform uses four distinct authentication methods across its external system connections. Each method carries different security characteristics and different migration requirements.

### SQL Server Authentication (MSSQL)

Regions: BRA, JPN, USA, ITA, SAF

The MSSQL connections use SQL Server authentication -- a username and password pair stored in Talend context variables. This is the traditional authentication method for SQL Server and is widely supported, but it has known weaknesses: credentials are transmitted over the network (unless SSL/TLS is enforced), passwords do not expire unless Active Directory policies are applied, and there is no multi-factor authentication option.

For migration, MSSQL connections should transition to Windows Integrated Authentication or Azure AD authentication where possible, eliminating the need to store SQL Server passwords in the integration platform. For on-premises servers that cannot support these methods, the existing credentials should be migrated to a secrets manager with automated rotation.

### Business Central API Authentication

Regions: BRA, DEU, USA

The Business Central connections use API key or OAuth client secret authentication, stored as `parameter_bc_password` in context variables. This is a service-to-service authentication pattern appropriate for backend integrations. The credentials are specific to each Business Central company (tenant), meaning each subsidiary requires its own credential set.

For migration, Business Central connections should use the OAuth 2.0 client credentials flow with certificates rather than shared secrets. Microsoft's recommended approach for BC API access is an Azure AD application registration with certificate-based authentication, which eliminates the need to store and rotate a password.

### Salesforce Username-Password Authentication

Regions: USA

The Salesforce connection uses the username-password OAuth flow, which requires a user-level password and a security token. This is the least secure authentication method available for Salesforce and is being deprecated by Salesforce in favor of more secure flows. The integration is tied to a specific user account (`parameter_USA_Vaxxinova_Salesforce_userPassword`), creating a single point of failure.

For migration, the Salesforce connection must be upgraded to either the JWT Bearer flow (for server-to-server authentication) or the Client Credentials flow (available in Salesforce since Winter '23). Both approaches use a connected app with certificates and do not depend on a specific user's credentials.

### BigQuery Service Account Authentication

Regions: Global (and implicitly all regions that write to BigQuery)

BigQuery authentication uses Google Cloud service account credentials -- typically a JSON key file that contains a private key. This is the standard authentication method for GCP services. The service account key is referenced through Talend context variables, meaning the key file itself is stored on the Talend runtime server.

For migration, service account keys should be replaced with Workload Identity Federation where possible, which allows the target platform to authenticate to GCP without storing a long-lived key. If Workload Identity is not feasible, the service account keys should be stored in a secrets manager and rotated on a regular schedule (90 days or less).

## Region Comparison

Credential exposure findings are distributed unevenly across regions, roughly proportional to the number of external system connections each region maintains.

| Region | Finding Count | % of Total | Primary Credential Types |
|--------|---------------|------------|--------------------------|
| USA | 105 | 55% | MSSQL, Business Central, Salesforce, SFTP |
| BRA | 48 | 25% | MSSQL, Business Central |
| JPN | 24 | 13% | MSSQL |
| Global | 8 | 4% | BigQuery service account |
| DEU | 2 | 1% | Business Central |
| ITA | 2 | 1% | MSSQL |
| SAF | 1 | <1% | MSSQL |

The United States region accounts for 55% of all credential exposure findings -- 105 out of 190. This reflects the USA's complex integration landscape: it connects to MSSQL (Newport Labs legacy), Business Central (VUS subsidiary), Salesforce CRM, and an external SFTP server (Animalytix distributor data). Each of these systems requires multiple credential parameters (username, password, host, token), and each environment variant (PROD, TEST) defines its own set.

Brazil follows with 48 findings (25%), driven by its dual-pipeline architecture: legacy MSSQL-based Biovet extraction and newer Business Central API integration. Japan has 24 findings concentrated entirely around its MSSQL connection to the VJP ERP database.

The Global region's 8 findings likely represent BigQuery service account credentials and shared reference data access parameters. Germany and Italy each have just 2 findings, consistent with their simpler connection profiles. South Africa (SAF) has a single finding, reflecting its nascent pipeline with only one cloned extraction job.

## Concrete Examples

The following examples illustrate the types of credential exposure detected across the platform:

**BRA: Business Central API passwords.** Two separate context groups -- `BRA_PROBI_BC_environment` and `BRA_VBR_BC_environment` -- each contain a `parameter_bc_password` variable. These store the OAuth client secret or API key used to authenticate against Business Central for the Probi and VBR entities respectively. The presence of two separate password parameters for two entities in the same region means credential rotation must be performed independently for each, doubling the operational burden.

**JPN: MSSQL database password.** The context group `connection_JPN_VJP_MSSQL` contains a `connection_JPN_VJP_MSSQL_Password` variable that stores the SQL Server authentication password for Japan's on-premises ERP database. This single credential gates access to all of VJP's financial and sales data. If compromised, an attacker could read or modify source ERP data directly.

**USA: Salesforce user password.** The context group `USA_Vaxxinova_Salesforce` contains a `parameter_USA_Vaxxinova_Salesforce_userPassword` variable. This is a Salesforce user-level password (not an OAuth client secret), suggesting the platform authenticates to Salesforce using the username-password OAuth flow rather than a more secure server-to-server flow. This approach ties the integration to a specific user account and breaks if that user's password is changed or the account is deactivated.

**USA: Business Central credentials.** The context group `USA_VUS_environment_GC` contains a `parameter_bc_password` variable, following the same pattern as Brazil. The `_GC` suffix likely denotes a Google Cloud-specific environment variant, suggesting that credential sets are duplicated per deployment target.

## Compliance Considerations

Vaxxinova operates across multiple jurisdictions, each with its own data protection regulation. The current platform's security posture has implications for compliance in each jurisdiction.

### GDPR (General Data Protection Regulation) -- Germany, Italy

GDPR applies to the processing of personal data of EU residents. The Vaxxinova Talend platform processes financial and customer data from the German (DEU) and Italian (ITA) subsidiaries, which likely includes personal data such as customer names, addresses, and contact information.

GDPR compliance gaps in the current platform:
- **No data access logging.** GDPR Article 30 requires organizations to maintain records of processing activities. The platform has no audit logging to record who accessed what data and when.
- **No access controls on personal data.** GDPR Article 25 (Data Protection by Design) requires appropriate technical measures to ensure that only necessary personal data is processed. The platform's lack of RBAC and row-level security means any team member can access all personal data from any region.
- **No data minimization in queries.** The pervasive `SELECT *` pattern means that every query retrieves all columns, including personal data fields that may not be needed for the specific transformation. This conflicts with GDPR's data minimization principle.
- **No right-to-erasure mechanism.** GDPR Article 17 grants individuals the right to have their personal data erased. The platform has no mechanism to identify or remove a specific individual's data across BigQuery datasets.

### LGPD (Lei Geral de Protecao de Dados) -- Brazil

Brazil's LGPD is closely modeled on GDPR and imposes similar requirements. The BRA pipeline processes customer data from BioVet and VBR Business Central instances, which likely includes personal data of Brazilian citizens.

LGPD-specific concerns:
- **Data localization.** LGPD does not strictly require data to remain in Brazil, but cross-border data transfers must be justified and documented. The current platform stores all data in BigQuery (presumably in a single GCP region), which may or may not align with LGPD's cross-border transfer provisions.
- **Consent tracking.** If any of the personal data processed by the BRA pipeline was collected under consent, the platform has no mechanism to track which records have valid consent and which do not.

### APPI (Act on the Protection of Personal Information) -- Japan

Japan's APPI requires organizations to take "necessary and appropriate" measures for personal information protection, including access controls, audit trails, and incident response capabilities. The current platform's JPN pipeline processes financial and customer data from the VJP ERP system with no access controls and no audit logging, which may not satisfy APPI's requirements.

### POPIA (Protection of Personal Information Act) -- South Africa

South Africa's POPIA imposes data protection requirements similar to GDPR. The SAF pipeline is nascent (a single cloned job), but as it grows, it will need to comply with POPIA's requirements for access controls, data minimization, and breach notification.

### Data Residency Implications

The platform stores all data in BigQuery, which operates in Google Cloud regions. The choice of GCP region for BigQuery datasets has implications for data residency compliance:
- German and Italian data stored outside the EU may violate GDPR's data transfer restrictions.
- Brazilian data stored outside Brazil may require additional legal safeguards under LGPD.
- Japanese data stored outside Japan may require a cross-border transfer assessment under APPI.

The current Talend metadata does not specify which GCP region the BigQuery datasets reside in, so this concern must be validated during migration planning.

## What Is Missing

Beyond the 190 credential exposure findings, the more significant security concern is what the analysis did not find -- controls that should exist but do not:

**No role-based access control (RBAC).** The Talend project has no mechanism to restrict which users can execute which jobs or access which data. Any team member with access to the Talend Studio project can run any job against any environment, including production. There is no separation between a developer who should only access test environments and an operator who manages production runs.

**No row-level security.** BigQuery datasets are not segmented by access permissions. A user who can query the `bra_vbr_raw` dataset can also query `jpn_vjp_raw`, `usa_vus_raw`, and every other regional dataset. In a multinational organization where subsidiaries may have regulatory requirements to restrict data access by geography (GDPR in Germany, LGPD in Brazil), this lack of data-level access control is a compliance gap.

**No audit logging.** There is no record of who ran which job, when, against which environment, or what data was accessed. If a data breach occurred, the organization would have no forensic trail to determine what happened, when it happened, or who was responsible. Talend does not generate audit logs by default, and no custom logging infrastructure has been built to fill this gap.

**No credential rotation policy.** While context variables enable credential externalization, there is no evidence of a rotation schedule or automated rotation mechanism. Database passwords and API keys may have been set once during initial setup and never changed -- a common pattern in integration platforms that gradually accumulates risk over time.

**No network security configuration.** The Talend job metadata contains no evidence of encrypted connections (SSL/TLS) being enforced for MSSQL connections. While this may be configured at the infrastructure level (outside the Talend project), the absence of explicit SSL configuration in the connection metadata is a yellow flag that should be validated during migration planning.

## Migration Takeaways

1. **Migrate all credentials to a secrets manager.** Every password-type context variable should be migrated to a centralized secrets management service -- Azure Key Vault, GCP Secret Manager, AWS Secrets Manager, or HashiCorp Vault. This provides encryption at rest, access control policies, audit logging, and automated rotation capabilities that Talend context variables lack. The 190 credential exposure findings map directly to 190 secrets that need to be onboarded.

2. **Implement RBAC in the target platform.** The migration target must enforce role-based access control at multiple levels: who can deploy pipelines, who can trigger execution, who can view logs, and who can modify connection parameters. At minimum, define separate roles for developers (test environment access only), operators (production execution), and administrators (credential and configuration management).

3. **Establish credential rotation policies.** Before migration, audit every credential to determine its age and rotation history. During migration, rotate all credentials as part of the cutover process -- this ensures that legacy credentials from the Talend platform are invalidated. Post-migration, implement automated rotation on a 90-day cycle for database passwords and a 180-day cycle for API keys and service account credentials.

4. **Implement row-level security for regional data.** In the target data warehouse, configure dataset-level or row-level access controls so that regional teams can only access their own subsidiary's data. This is particularly important for compliance with GDPR (Germany), LGPD (Brazil), and APPI (Japan) -- each of which may impose geographic data access restrictions.

5. **Enable audit logging from day one.** The target platform should log every pipeline execution, including the user or service account that triggered it, the environment it ran against, the data sources and targets accessed, and the outcome (success, failure, records processed). These logs should be retained for a minimum of one year and should be queryable for incident investigation.

6. **Replace username-password Salesforce authentication.** The current Salesforce integration uses user-level password authentication, which is being deprecated by Salesforce in favor of OAuth 2.0 server-to-server flows. The migration should implement the JWT Bearer or Client Credentials OAuth flow, which does not depend on a specific user account and supports more granular permission scoping.

7. **Conduct a data residency assessment.** Before migration, determine the GCP region(s) where BigQuery datasets reside and validate that the data residency configuration complies with GDPR, LGPD, APPI, and POPIA requirements. If data must remain within specific geographic boundaries, configure the target BigQuery datasets accordingly.

8. **Upgrade MSSQL connections to encrypted transport.** Validate that all MSSQL connections use SSL/TLS encryption. If they do not, configure encrypted connections as part of the migration. For on-premises SQL Servers that do not support SSL/TLS, consider VPN or SSH tunnel alternatives to protect data in transit.

---

*Generated by intodoc -- documentation automation for integration platforms.*
