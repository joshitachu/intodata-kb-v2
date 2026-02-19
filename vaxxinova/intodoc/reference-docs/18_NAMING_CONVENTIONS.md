# Naming Conventions

Vaxxinova's Talend codebase follows a well-established naming convention for jobs, datasets, and layers. The standard pattern -- `{Region}_{Subsidiary}_{Layer}_{Description}` -- is applied consistently across most regions, with few violations detected. This disciplined approach to naming is one of the platform's strengths: it makes jobs self-documenting, enables quick identification of a job's scope and purpose, and simplifies operational monitoring. The convention should be preserved in any migration to maintain continuity for the operations and engineering teams who depend on it.

## The Standard Pattern

Job names across the platform follow a four-segment structure:

```
{Region/Subsidiary}_{Layer}_{Description}
```

For example:
- `VBR_Clean_Build_Sales_Set` -- VBR subsidiary, Clean layer, building the Sales Set
- `VJP_Raw_salesInvoiceHeaders` -- VJP subsidiary, Raw layer, extracting sales invoice headers
- `DEL_Clean_Sales_Set_Delete_Ingest` -- DEL subsidiary, Clean layer, delete-and-ingest pattern for Sales Set
- `NPL_Clean_Account_v4_Salesforce` -- NPL subsidiary, Clean layer, version 4 of Salesforce account sync

The first segment identifies the owning region or subsidiary. The second identifies the data layer. The remainder describes the specific function, often including version numbers (v1, v5, v15) or target systems (Salesforce, BigQuery).

## The Naming Hierarchy

The naming convention encodes a four-level hierarchy that maps directly to Vaxxinova's organizational and technical structure: Region, Subsidiary, Layer, and Description. Understanding this hierarchy is essential for interpreting job names correctly and for maintaining the convention during migration.

### Level 1: Region

The region is the broadest organizational unit. It corresponds to a geographic area where Vaxxinova operates: BRA (Brazil), JPN (Japan), USA (United States), DEU (Germany), ITA (Italy), and so on. Some "regions" are functional rather than geographic -- Global represents cross-entity operations, GENERIC represents parameterized templates, and scratchpad represents development and test work.

The region determines which Talend project folder a job lives in, which context variable groups it can access, and which BigQuery datasets it reads from and writes to. A job's region is the first thing an operator checks when triaging a production issue.

### Level 2: Subsidiary

Within a region, the subsidiary code identifies the specific legal entity. A single region may contain multiple subsidiaries -- the USA region contains both VUS (Vaxxinova United States) and NPL (Newport Labs), for example. The subsidiary code appears as the first segment of the job name (e.g., `VUS_Clean_...`, `NPL_Clean_...`) and determines which Business Central company, MSSQL database, or Salesforce instance the job connects to.

The distinction matters because different subsidiaries within the same region may have different source systems, different data models, and different business rules. A job named `VUS_Clean_BC_SalesInvoices_v15` connects to VUS's Business Central instance, while `NPL_Clean_Account_v4_Salesforce` connects to NPL's Salesforce org -- even though both are in the USA region.

### Level 3: Layer

The layer code identifies where the job sits in the data processing pipeline:

- **Raw** -- Extraction from source systems with minimal transformation. Raw jobs pull data from MSSQL, Business Central, or Salesforce and land it in BigQuery raw datasets.
- **Clean** -- Transformation, validation, and business logic. Clean jobs read from raw datasets, apply data quality rules and business transformations, and write to clean datasets.
- **Report** -- Reporting-specific views and aggregations that serve downstream BI tools.
- **Forecast** -- Forward-looking financial projections, distinct from historical reporting.
- **Global** -- Cross-entity consolidation that aggregates data from multiple subsidiaries.

The layer code is the second segment of the job name (e.g., `VBR_Raw_...`, `VBR_Clean_...`) and determines both the source and target datasets for the job.

### Level 4: Description

The description is the free-form remainder of the job name. It identifies the specific data entity or business process the job handles: `Customers`, `salesInvoiceHeaders`, `Build_Sales_Set`, `Invoice_Set_v5`, `Account_v4_Salesforce`. Descriptions often include version numbers (indicating iterative refinement of the job logic) and target system identifiers (indicating synchronization jobs).

### Hierarchy Examples

The following examples show how the four levels combine:

| Job Name | Region | Subsidiary | Layer | Description |
|----------|--------|-----------|-------|-------------|
| `VBR_Clean_Build_Sales_Set` | BRA | VBR | Clean | Build Sales Set |
| `NPL_Raw_salesInvoiceHeaders` | USA | NPL | Raw | salesInvoiceHeaders |
| `VJP_Clean_Build_Sales_Set` | JPN | VJP | Clean | Build Sales Set |
| `VDA_BC_Clean_Invoice_Set_v3` | DEU | VDA | Clean | BC Invoice Set v3 |
| `VUS_Clean_Animalytix` | USA | VUS | Clean | Animalytix |
| `Global_Build_Sales_Set` | Global | (none) | Global | Build Sales Set |

Note that Global-level jobs omit the subsidiary code because they operate across subsidiaries. The GENERIC jobs use `GEN_BC_` as a prefix, omitting both region and subsidiary because they are parameterized templates designed to work for any subsidiary.

## Region and Subsidiary Codes

The platform uses two overlapping code systems: three-letter ISO-style region codes and Vaxxinova-specific subsidiary codes.

**Region codes** identify the geographic scope:

| Code | Region | Usage Count |
|------|--------|-------------|
| Global | Cross-entity / shared | 10 |
| NPL | Newport Labs (USA) | 8 |
| VDA | Vaxxinova Germany Autogenous | 5 |
| Legacy | Historical / migrated jobs | 3 |
| ITA | Italy | 3 |
| BRA | Brazil | 2 |
| DEU | Germany | 1 |
| JPN | Japan | 1 |
| USA | United States | 1 |

**Subsidiary codes** identify the specific legal entity within a region:

| Code | Subsidiary | Region |
|------|-----------|--------|
| VBR | Vaxxinova Brazil | BRA |
| VIT | Vaxxinova Italy | ITA |
| VUS | Vaxxinova United States | USA |
| VJP | Vaxxinova Japan | JPN |
| VDD | Vaxxinova Germany (second entity) | DEU |
| NPL | Newport Labs | USA |

The distinction between region codes and subsidiary codes is meaningful. Some regions contain multiple subsidiaries (USA has both VUS and NPL), while the subsidiary code is always specific to a single legal entity. Jobs that operate at the subsidiary level use subsidiary codes; jobs that operate at the regional or cross-entity level use region codes.

## Layer Codes

The second segment of the naming pattern identifies the data processing layer:

| Layer Code | Meaning | Job Count |
|-----------|---------|-----------|
| Clean | Transformed, validated, business-ready data | 101 |
| Raw | Source system extracts, minimal transformation | 72 |
| RAW | Variant capitalization of Raw | 7 |
| Global | Cross-entity shared datasets | 10 |
| Report | Reporting-specific views | 6 |
| Forecast | Forecasting pipeline | 1 |

The Clean layer is the most heavily used (101 jobs), reflecting the platform's emphasis on data transformation and validation. The Raw layer (72 + 7 = 79 jobs, counting both capitalization variants) represents source extracts. The split between `Raw` and `RAW` is a minor inconsistency -- both refer to the same conceptual layer, but the casing varies. BRA and DEU use `RAW` in some jobs while other regions consistently use `Raw`.

## Convention Compliance by Region

While the global naming convention is strong, compliance varies by region. The following assessment scores each region on how consistently its jobs follow the standard `{Region}_{Subsidiary}_{Layer}_{Description}` pattern.

### BRA -- Compliance: Good

BRA jobs consistently use the subsidiary prefix (VBR for Vaxxinova Brazil, BIO for BioVet) followed by the layer code and a descriptive name. The only inconsistency is the occasional use of `RAW` instead of `Raw` in some older jobs. Examples of compliant names: `VBR_Clean_Build_Sales_Set`, `BIO_Clean_Invoice_Set_v5`, `VBR_Raw_Customers`. The dual-subsidiary structure (VBR and BIO) is handled cleanly -- the subsidiary code unambiguously identifies which legal entity the job serves.

### JPN -- Compliance: Good

Japan jobs follow the convention reliably, with `VJP` (Vaxxinova Japan) and `VJA` (a secondary entity) as subsidiary prefixes. Layer codes are consistently capitalized as `Clean` and `Raw`. The only notable pattern is the use of `Copy_of_` prefixes on some jobs (e.g., `Copy_of_VJP_Clean_Build_Sales_Set`), which indicates that the job was cloned from another and not yet renamed. These `Copy_of_` jobs are functionally correct but violate the spirit of the naming convention by carrying metadata about their creation history rather than their current purpose.

### USA -- Compliance: Good

USA has the most complex subsidiary landscape (VUS, NPL) and handles it cleanly. Jobs are consistently prefixed with the appropriate subsidiary code, and layer codes follow standard capitalization. The Salesforce synchronization jobs append `_Salesforce` to the description (e.g., `NPL_Clean_Account_v4_Salesforce`), which is a useful extension of the convention that immediately identifies the target system. Version numbers (v4, v8, v15, v26) are prevalent in the USA region, reflecting iterative development of complex sync logic.

### DEU -- Compliance: Good

Germany uses two subsidiary codes (VDA for Vaxxinova Germany Autogenous, VDD for a second entity) and follows the convention consistently. The `VDA_BC_` prefix adds an additional segment identifying the Business Central source, which is a minor deviation from the standard pattern but improves readability. Some jobs use `RAW` capitalization instead of `Raw`.

### ITA -- Compliance: Good

Italy jobs use the `ITA` region prefix and `VIT` subsidiary code consistently. The `IZO` subsidiary code appears in Salesforce sync jobs (e.g., `IZO_Sync_Product_Salesforce_v5`), maintaining the pattern of subsidiary-specific prefixes. Both `RAW` and `Clean` layer codes appear, with the same minor capitalization inconsistency seen in BRA and DEU.

### ZAF -- Compliance: Good (with caveat)

ZAF jobs follow the naming convention in structure but not in content: they carry inherited Italian names (e.g., `ITA_VIT_RAW_Copy`) because the ZAF pipeline was cloned from the ITA pipeline and has not been fully renamed. The structure is compliant -- region prefix, subsidiary code, layer code, description -- but the region and subsidiary codes refer to Italy rather than South Africa. This is a documentation and clarity issue rather than a structural violation.

### SAF -- Compliance: Good (with caveat)

SAF has the same inherited-naming issue as ZAF. Its single job carries the ITA/VIT prefix from the original Italian template. The naming structure is formally correct, but the content is misleading.

### GENERIC -- Compliance: Poor (by design)

GENERIC jobs use the `GEN_BC_` prefix (e.g., `GEN_BC_AccrualLedgerEntries`), which does not follow the standard pattern. This is intentional: GENERIC jobs are parameterized templates that work across subsidiaries, so a region or subsidiary prefix would be misleading. The `GEN_` prefix clearly identifies these as generic templates, and the `BC_` segment identifies Business Central as the source. While technically non-compliant, the GENERIC naming scheme is internally consistent and well-understood.

### Global -- Compliance: Poor

Global contains the most heterogeneous naming. Jobs use a mix of prefixes: `Global_Build_Sales_Set`, `Legacy_Build_Sales_Set`, `NPL_Clean_Account_...`, `VDA_Clean_...`. The `Legacy_` prefix predates the current convention and refers to historical jobs that were created before the naming standard was established. The `NPL_` and `VDA_` prefixes appear because some Global jobs are actually subsidiary-specific jobs that were organizationally placed in the Global folder. This mixing of conventions makes the Global region harder to navigate than it should be.

### scratchpad -- Compliance: Poor (expected)

The scratchpad region contains development and test jobs that intentionally do not follow production naming conventions. Job names like `BigQuery_TEST`, `sandbox`, and `test_connection` are appropriate for their purpose but would be inappropriate in a production context.

## Anomalies and Exceptions

Several job naming patterns break the standard convention in ways that deserve specific attention during migration planning.

### Copy_of_* Jobs

Multiple jobs across the platform carry the `Copy_of_` prefix, indicating they were created by cloning an existing job in Talend Studio. Examples include `Copy_of_VBR_Clean_Build_Sales_Set` (BRA), `Copy_of_VJP_Clean_Build_Sales_Set` (JPN), and `ITA_VIT_RAW_Copy` (SAF/ZAF). These prefixes are Talend Studio artifacts -- they provide no operational value and obscure the job's actual purpose. During migration, all `Copy_of_` prefixes should be stripped and the jobs renamed to follow the standard convention.

### Version Number Proliferation

Some regions -- particularly USA -- have accumulated high version numbers: `VUS_Clean_BC_SalesInvoices_v15`, `NPL_Clean_Item_v26_Salesforce`. These version numbers reflect iterative refinement of the job logic over time, but they provide no information about what changed between versions. During migration, consider resetting version numbers to v1 (since the migration is itself a major version change) or dropping them entirely if the target platform provides built-in version control.

### Sandbox and Test Jobs

The scratchpad region contains jobs with names like `BigQuery_TEST`, `sandbox`, and unnamed test jobs that do not follow any convention. These should not be migrated to production but may need to be preserved as development templates or reference implementations.

### Legacy Prefix

Three jobs in the Global region carry the `Legacy_` prefix (e.g., `Legacy_Build_Sales_Set`). This prefix indicates that the jobs predate the current naming convention and were not renamed when the standard was adopted. The `Legacy_` prefix should be removed during migration and the jobs renamed to follow the standard `Global_` prefix pattern.

### Inherited Region Codes

As noted in the ZAF and SAF compliance analysis, some jobs carry region and subsidiary codes from the region they were cloned from rather than the region they currently operate in. `ITA_VIT_RAW_Copy` in the SAF region should be renamed to `VSA_Raw_*` (or whatever the South African subsidiary code is). This pattern will cause confusion in monitoring and alerting if carried forward to the target platform.

## Naming Conventions in BigQuery

The naming conventions extend beyond Talend job names into the BigQuery datasets and tables that the jobs read from and write to. Understanding these conventions is important because the target platform will need to preserve or improve them.

### Dataset Naming

BigQuery datasets follow a `{region}_{subsidiary}_{layer}` naming pattern that mirrors the Talend job convention:

- `bra_vbr_raw` -- Brazil, VBR subsidiary, Raw layer
- `bra_vbr_clean` -- Brazil, VBR subsidiary, Clean layer
- `jpn_vjp_raw` -- Japan, VJP subsidiary, Raw layer
- `usa_vus_clean` -- USA, VUS subsidiary, Clean layer
- `global_clean` -- Global consolidation, Clean layer

This dataset naming is consistently lowercase and uses underscores as separators, which is a BigQuery best practice. The alignment between Talend job names and BigQuery dataset names makes it straightforward to determine which dataset a job operates on -- a `VBR_Clean_*` job writes to the `bra_vbr_clean` dataset.

### Table Naming

Within datasets, table names follow a less formal convention. Raw layer tables typically mirror the source system entity names (e.g., `salesInvoiceHeaders`, `Customers`, `generalLedgerEntries`), preserving the camelCase or PascalCase from the source API. Clean layer tables use more descriptive names that reflect the business concept (e.g., `Sales_Set`, `Invoice_Set`, `Account_Master`).

The inconsistency between Raw table naming (source-system conventions) and Clean table naming (business-oriented conventions) is a natural consequence of the platform's architecture: Raw tables are generated automatically from API responses, while Clean tables are designed by developers.

### Naming Gaps in BigQuery

Several BigQuery naming issues should be addressed during migration:

- **No consistent case convention.** Raw tables use camelCase from the source API, Clean tables use PascalCase_With_Underscores, and Global tables use a mix. A single case convention (e.g., snake_case) should be adopted platform-wide.
- **No table versioning.** When a Clean table's schema changes, the old table is overwritten rather than versioned. Table names like `Invoice_Set` do not indicate which version of the schema they represent.
- **No temporal partitioning in table names.** Tables are not named to indicate their partitioning strategy, making it unclear from the name alone whether a table is partitioned by date, by region, or not at all.

## Naming Pattern Examples

The following examples illustrate how the convention works in practice across different regions:

**BRA (Brazil)**:
- `VBR_Clean_Build_Sales_Set` -- subsidiary VBR, Clean layer, building Sales Set
- `BIO_Clean_Invoice_Set_v5` -- subsidiary BIO (BioVet), Clean layer, Invoice Set version 5
- `VBR_Raw_Customers` -- subsidiary VBR, Raw layer, customer extraction

**JPN (Japan)**:
- `VJP_Clean_Build_Sales_Set` -- subsidiary VJP, Clean layer, building Sales Set
- `VJP_Raw_salesInvoiceHeaders` -- subsidiary VJP, Raw layer, sales invoice headers
- `VJA_Clean_Inventory_value_Item` -- subsidiary VJA, Clean layer, inventory valuation

**USA (United States)**:
- `NPL_Clean_Account_v4_Salesforce` -- subsidiary NPL, Clean layer, Salesforce account sync v4
- `VUS_Clean_BC_SalesInvoices_v15` -- subsidiary VUS, Clean layer, BC sales invoices v15
- `VUS_Clean_Animalytix` -- subsidiary VUS, Clean layer, Animalytix distributor data

**DEU (Germany)**:
- `VDA_BC_Clean_Invoice_Set_v3` -- subsidiary VDA, BC source, Clean layer, Invoice Set v3
- `VDD_Clean_Invoice_Item_v1` -- subsidiary VDD, Clean layer, invoice items v1

## Migration Takeaways

**Preserve the naming convention in the target platform.** The `{Region}_{Subsidiary}_{Layer}_{Description}` pattern is well-understood by the operations team and is one of the few aspects of the platform that does not need improvement. Any target platform should adopt the same naming structure to maintain continuity and minimize retraining.

**Standardize the RAW/Raw capitalization.** The minor inconsistency between `RAW` and `Raw` should be resolved during migration. Adopting a single convention (lowercase `raw` is the modern standard) and applying it uniformly eliminates a trivial but persistent source of confusion.

**Rename cloned jobs before deploying to production.** Jobs like `ITA_VIT_RAW_Copy` in the SAF region should be renamed to follow the SAF naming convention (e.g., `VSA_Raw_TABANALISI`) during migration. Carrying inherited names into the target platform would perpetuate confusion about job ownership.

**Document exceptions explicitly.** The GENERIC and Global regions intentionally deviate from the standard pattern. These exceptions should be documented in a naming convention guide so that future developers understand that `GEN_BC_*` and `Legacy_*` are recognized prefixes, not violations.

**Strip Copy_of_ prefixes and reset version numbers.** The migration is an opportunity to clean up accumulated naming artifacts. All `Copy_of_` prefixes should be removed, and version numbers should be reset or dropped entirely if the target platform provides native version control.

**Adopt snake_case for BigQuery objects.** The target platform should standardize on snake_case for all dataset and table names, resolving the current inconsistency between camelCase raw tables and PascalCase clean tables. This aligns with BigQuery naming best practices and eliminates case-sensitivity issues.

**Establish a naming convention governance process.** The current convention was adopted through informal consensus and is maintained through developer discipline. The migration is the right time to formalize the convention in a written standard, with automated validation that flags non-compliant names during development and deployment.
