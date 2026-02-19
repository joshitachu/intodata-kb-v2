# Onboarding Guide

This guide helps new team members get up to speed with the Vaxxinova Talend integration platform. Each section covers a specific region's pipeline, including architecture, key jobs, environment setup, and common debugging steps.

## Brazil (BRA)

### BRA_0

## Onboarding Guide: BRA_0 -- Core Sales & Product Pipeline

### What This Chunk Does
BRA_0 contains the foundational ETL jobs for Vaxxinova Brazil's sales data. These jobs extract data from two source systems (legacy MSSQL and Business Central), transform it into a unified sales reporting structure, and sync product data to Salesforce CRM.

### Architecture Overview
```
MSSQL (Biovet legacy) --> BRA_VBR_RAW --> BigQuery Raw --> BIO_Clean_Invoice_Set_v5 --> Sales_Set (Clean)
Business Central API  --> [BRA_5-7 Raw jobs] --> BigQuery Raw --> Copy_of_VBR_Clean_Build_Sales_Set --> Sales_Set (Clean)
BigQuery Raw --> BIO_Sync_Product_Salesforce_v1 --> Salesforce Product2
```

### Key Jobs to Understand First
1. **BRA_VBR_RAW** -- Start here. This is the raw extraction job that pulls 3 views from MSSQL (VW_Finished_Product, VW_Sales, VW_Returns) into BigQuery. Simple pattern: tMSSqlInput -> tMap -> tBigQueryOutput.
2. **BIO_Clean_Invoice_Set_v5** -- The legacy clean transformation. Reads raw sales + returns from BigQuery, joins with fiscal periods and country mappings, outputs a unified Sales_Set.
3. **Copy_of_VBR_Clean_Build_Sales_Set** -- The newer Business Central clean transformation. More complex (94 components) with extensive in-memory lookups.
4. **BIO_Sync_Product_Salesforce_v1** -- Product master sync to Salesforce CRM.

### Environment Setup
- Context variables control all environment-specific values. Key ones:
  - `context.parameter_Raw_Prefix` -- BigQuery dataset for raw data (e.g., test_bra_bio_raw)
  - `context.parameter_VBR_Raw_Env` -- BigQuery dataset for VBR raw data
  - `context.parameter_Env_Prefix` -- Target clean dataset prefix
  - `context.connection_BRA_VBR_MSSQL_Schema` -- MSSQL schema for the Biovet database
  - `context.parameter_GS_Bucket` -- Google Cloud Storage bucket for temp CSV staging

### Common Debugging Steps
1. **Sales_Set data looks wrong**: Check which pipeline produced it (BIO_Clean or Copy_of_VBR_Clean). The entity field will say 'Biovet' for legacy or 'Vaxxinova United States' for BC-sourced data (note: this may be a configuration issue -- the BC job maps Entity as 'Vaxxinova United States' even for Brazil data).
2. **Product sync failures**: Check Salesforce connection credentials in tSalesforceConnection_1 (labeled 'USA_Vaxxinova_Salesforce'). The upsert uses External_Id__c field.
3. **Missing fiscal periods**: Verify Global_Raw.Fiscal_Periods or Global_Master.Fiscal_Periods table is populated for the relevant date range.
4. **MSSQL connection issues**: The schema reference is dynamic via context variable. Verify `context.connection_BRA_VBR_MSSQL_Schema` is correct for the target environment.

### Important Caveats
- The Sales_Set uses TRUNCATE mode -- every run replaces all data. Do not run during active reporting windows.
- The 'Produtct_Description' typo is intentional (inherited from source). Do not attempt to fix without coordinating across the full pipeline.
- Copy_of_VBR_Clean_Build_Sales_Set maps Entity as 'Vaxxinova United States' -- investigate whether this is correct for Brazil subsidiary data.
- There is virtually no error handling. If a job fails mid-run, the Sales_Set table may be truncated but not repopulated. Monitor job completion carefully.

### BRA_1

## Onboarding Guide: BRA_1 -- Sales Set Build & Ingest Pipeline

### What This Chunk Does
BRA_1 is the core sales data transformation layer for Vaxxinova Brazil's Business Central data. It takes raw invoice and credit memo data from BigQuery, enriches it with reference data, and produces the Sales_Set that powers global reporting.

### Pipeline Flow
```
BigQuery Raw (VBR)
  |-- salesInvoices + salesInvoiceLines --> tMap (Map_Invoice_Lines)
  |-- salesCreditMemos + salesCreditMemoLines --> tMap (Map_Credit_Memo_Lines)
  |-- tUnite (Unite_Invoice_and_Credit)
  |-- + Fiscal_Periods, paymentTerms, shipmentMethods, Conversion_Rate, Country_Master, Account_Mappings
  |-- tMap (Map_Sales_Set) --> Local Sales_Set + Global Sales_Set
```

### Job Execution Order
1. **VBR_Clean_Build_Sales_Set** runs first to create the clean Sales_Set
2. **VBR_Clean_Ingest_Sales_Set** runs after to push into the global layer

### Key Context Variables
- `context.parameter_VBR_Raw_Env` -- Source raw dataset (e.g., test_bra_vbr_raw)
- `context.parameter_Env_Prefix` -- Target clean dataset
- `parameter_date` -- Incremental cutoff date (via globalMap)

### Understanding the Build Job
The Build job has 3 main processing stages:
1. **Stage 1 - Pre-job**: Loads lookup data into tHash memory (fiscal periods, payment terms, conversion rates, etc.)
2. **Stage 2 - Invoice/Credit Processing**: Reads invoice lines and credit memo lines, maps them to a common schema, and unites them
3. **Stage 3 - Final Enrichment**: Joins unified data with all reference lookups and writes to both local and global Sales_Set tables

### Understanding the Ingest Job
The Ingest job performs a simple but critical operation:
1. DELETE existing records from the global Sales_Set (via tBigQuerySQLRow)
2. INSERT fresh records from the local Sales_Set (via tBigQueryOutput)

### Version Management
- The primary Build and Ingest jobs are the actively developed versions
- Backup variants (_Backup suffix) preserve last-known-good versions
- When modifying, always update the backup before changing the primary


*[Guide truncated for brevity]*

*Additional onboarding guides available for chunks: BRA_2, BRA_3, BRA_4, BRA_5, BRA_6, BRA_7*

## Germany (DEU)

### DEU_all

## Onboarding Guide: DEU_all -- Germany Complete Pipeline

### What This Chunk Does
This chunk covers the **complete Germany ETL pipeline** from raw extraction through clean-layer processing. Germany is unique in having two entity sources (VDA for Business Central, VDD for legacy ERP).

### Entity Structure
```
Germany Pipeline
|
+-- VDA (Business Central)
|   +-- VDA_BC_Clean_Invoice_Set_v3
|   +-- VDA_BC_Clean_Item_v1
|   +-- VDA_BC_Clean_SalesInvoices
|
+-- VDD (Legacy ERP)
|   +-- DEU_VDD_RAW (extraction)
|   +-- VDD_Clean_Credit_Invoice_Item_v1
|   +-- VDD_Clean_Credit_Invoice_v3
|   +-- VDD_Clean_Invoice_Item_v1
|   +-- VDD_Clean_Invoice_Set_v1
|   +-- VDD_Clean_Invoice_v4
|
+-- Unified
    +-- VDA_Clean_Sales_Set_Delete_Ingest
```

### Execution Order
1. **Raw Layer**: DEU_VDD_RAW (VDA raw via GENERIC BC jobs)
2. **Clean Layer - VDA**: VDA_BC_Clean_Item_v1 -> VDA_BC_Clean_SalesInvoices -> VDA_BC_Clean_Invoice_Set_v3
3. **Clean Layer - VDD**: VDD_Clean_Invoice_v4 -> VDD_Clean_Invoice_Item_v1 -> VDD_Clean_Credit_Invoice_v3 -> VDD_Clean_Credit_Invoice_Item_v1 -> VDD_Clean_Invoice_Set_v1
4. **Sales_Set**: VDA_Clean_Sales_Set_Delete_Ingest

### Key Differences from Japan
- Dual entity sources (VDA + VDD) vs Japan's single VJP
- No Salesforce sync in Germany
- VDA raw extraction uses shared GENERIC BC jobs
- Lower version numbers suggest newer/less mature pipeline

### Troubleshooting
- **VDA data missing**: Check GENERIC BC job execution for Germany context

*[Guide truncated for brevity]*

## Generic / Shared

### GENERIC_all

## Onboarding Guide: GENERIC_all -- Shared Business Central Extraction

### What This Chunk Does
This chunk provides **shared, reusable extraction jobs** for Business Central data. Any Vaxxinova region using BC as their ERP can use these jobs by setting region-specific context variables.

### Job Families

#### Direct API Extractors
| Job | BC Entity | Description |
|-----|-----------|-------------|
| GEN_BC_AccrualLedgerEntries | Accrual ledger | Revenue/expense accruals |
| GEN_BC_Item | Items | Product master |
| GEN_BC_SalesCreditMemos | Sales credit memos | Returns/refunds |
| GEN_BC_SalesInvoice | Sales invoices | Sales transactions |

#### OData Extractors
| Job | BC Entity | Description |
|-----|-----------|-------------|
| GEN_BC_Customers_oData | Customers | Customer master |
| GEN_BC_Item_oData | Items | Product master (OData) |
| GEN_BC_Sales_Cr_Memo_Header_oData | Credit memo headers | Return documents |
| GEN_BC_Sales_Cr_Memo_Line_oData | Credit memo lines | Return line items |
| GEN_BC_Sales_Invoice_Header_oData | Invoice headers | Sales documents |
| GEN_BC_Sales_invoice_Line_oData | Invoice lines | Sales line items |

### How to Use for a New Region
1. Create region-specific context variables:
   - `parameter_BC_Tenant_Id`: Azure AD tenant for the region's BC instance
   - `parameter_BC_Environment`: BC environment name (e.g., 'Production')
   - `parameter_BC_Company_Id`: BC company ID
   - `parameter_Target_Dataset`: BigQuery target dataset (e.g., 'deu_vda_raw')
2. Configure the Create_Business_Central_Token with client credentials
3. Run the desired GEN_BC_* job with the region's context

### Architecture Pattern
```
Create_Business_Central_Token (OAuth2)
         |
         v
tRESTClient (API call with bearer token)

*[Guide truncated for brevity]*

## Global

### Global_0

# Onboarding Guide: Global_0 -- Budget, Forecast & Prior-Year Sales Pipeline

## What This Chunk Does
Contains 12 jobs that build the consolidated sales datasets for budget-vs-actual analysis, forecast tracking, prior-year comparisons, and legacy reporting.

## Architecture
```
Global_Budget.Budget_Master --->
  + Country_Master             --> Budget_Build_Sales_Set --> Global_Budget.Sales_Set_Budget
  + Salesforce_Account_Mappings
  + Conversion_Rates

Regional Clean Data --> Global_Build_Sales_Set --> Global_Clean.Sales_Set
```

## Key Jobs (Start Here)
1. **Budget_Build_Sales_Set**: Enriches budget data with country, account, and currency mappings
2. **Global_Build_Sales_Set**: Central consolidation producing the unified Sales_Set
3. **Prior_Build_Sales_Set**: Prior-year sales for YoY comparison

## Data Flow Categories
- **Actual**: Real sales from ERP systems (flows through regional Clean jobs)
- **Budget**: Annual planning targets from Budget_Master
- **Forecast**: In-year updated projections
- **Prior**: Previous fiscal year actuals for comparison

## Dependencies
- All regional Clean jobs (USA, BRA, DEU, etc.) must complete before Global_Build_Sales_Set
- Salesforce account mappings must be current (updated by Global_3 Salesforce jobs)
- Currency conversion rates must be loaded before budget/forecast builds

### Global_1

# Onboarding Guide: Global_1 -- Raw Sales Set Aggregation

## What This Job Does
Aggregates regional actual sales data from BigQuery, enriches it with fiscal periods and country definitions, and produces the consolidated raw sales set for global reporting.

## Architecture
```
Regional Sales_Set (Actual only)
  --> Filter --> Sort --> Aggregate
  --> Join Fiscal Periods
  --> Join Salesforce Countries
  --> Global Consolidated Output
```

## Key Design Patterns
- **Parameterized**: Same job handles multiple regions via context variables
- **Memory-efficient**: External sort + sorted aggregation for large volumes
- **Reference enrichment**: Fiscal periods and country standardization

## Prerequisites
- Regional Clean jobs must have populated the regional Sales_Set tables
- Global_Raw.Fiscal_Periods must contain current fiscal year definitions
- Global_Raw.Salesforce_Countries must be up-to-date

## Configuration
- `parameter_Env_Prefix`: Target BigQuery dataset prefix (e.g., 'USA_Clean')
- `parameter_table_name`: Source table name within the regional dataset
- `resource_flow_temp_folder`: Must have sufficient disk space for external sort

## Troubleshooting
- **Out of memory**: Increase JVM heap or reduce EXTERNAL_SORT_BUFFERSIZE
- **Missing fiscal periods**: Check Global_Raw.Fiscal_Periods for current year entries
- **Empty output**: Verify Category = 'Actual' filter matches source data format

*Additional onboarding guides available for chunks: Global_2, Global_3, Global_4, Global_5, Global_6, Global_7*

## Italy (ITA)

### ITA_all

## Onboarding Guide: ITA_all -- Italy Complete Pipeline

### What This Chunk Does
This chunk contains Italy's **complete ETL pipeline** from raw extraction to Salesforce sync. It is the most compact regional pipeline.

### Pipeline Overview
```
MSSQL (on-prem) --> ITA_VIT_RAW --> BigQuery (ita_vit_raw)
                                         |
                                         v
                              VIT_Clean_SalesInvoices_and_Lines
                              VIT_Clean_Sales_Set_v6
                              VIT_Clean_Sales_Set_Ingest
                              VIT_Clean_Sales_Set_Delete_Ingest
                                         |
                                         v
                              BigQuery (ita_vit_clean / Global_Clean)
                                         |
                                         v
                              IZO_Sync_Product_Salesforce_v5 --> Salesforce CRM
```

### Job Inventory
| Job | Function | Source | Target |
|-----|----------|--------|--------|
| ITA_VIT_RAW | Raw extraction | MSSQL | BigQuery raw |
| VIT_Clean_SalesInvoices_and_Lines | Invoice processing | BigQuery raw | BigQuery clean |
| VIT_Clean_Sales_Set_v6 | Sales_Set builder | BigQuery clean | Sales_Set |
| VIT_Clean_Sales_Set_Ingest | Incremental Sales_Set | BigQuery clean | Sales_Set |
| VIT_Clean_Sales_Set_Delete_Ingest | Sales_Set cleanup | Sales_Set | Sales_Set |
| IZO_Sync_Product_Salesforce_v5 | CRM product sync | BigQuery | Salesforce |

### Execution Order
1. ITA_VIT_RAW (raw extraction from MSSQL)
2. VIT_Clean_SalesInvoices_and_Lines (invoice processing)
3. VIT_Clean_Sales_Set_Delete_Ingest (delete stale records)
4. VIT_Clean_Sales_Set_v6 or VIT_Clean_Sales_Set_Ingest (build/ingest Sales_Set)
5. IZO_Sync_Product_Salesforce_v5 (sync to CRM)

### Key Differences from Other Regions

*[Guide truncated for brevity]*

## Japan (JPN)

### JPN_0

## Onboarding Guide: JPN_0 -- Japan Sales Set Builder

### What This Chunk Does
This chunk contains the jobs that build the **Sales_Set** table for Vaxxinova Japan. The Sales_Set is the primary clean-layer fact table that combines sales invoices, credit memos, and general ledger entries into a single unified dataset for reporting.

### Architecture Overview
```
Raw Layer (BigQuery)          Clean Layer (BigQuery)
-------------------          --------------------
jpn_vjp_raw.salesInvoiceLines  --> VJP_Clean.Sales_Set
jpn_vjp_raw.salesCreditLines   --> Global_Clean.Sales_Set
jpn_vjp_raw.generalLedgerEntry -->
Global_Master.Fiscal_Periods   -->
Global_Budget.Conversion_Rate  -->
```

### Key Jobs
| Job | Status | Description |
|-----|--------|-------------|
| Copy_of_VJP_Clean_Build_Sales_Set | Recommended | Fully parameterized version with context variables |
| VJP_Clean_Build_Sales_Set | Legacy | Some hardcoded dataset references |
| VJP_Clean_Build_Sales_Set_Backup | Archive | Backup copy, hardcoded to test datasets |

### Data Flow (Step by Step)
1. **Pre-job**: Initialize logging context
2. **Delete phase**: Remove existing records from Sales_Set matching delta IDs (salesInvoicesDelta + salesCreditMemosDelta)
3. **Read phase**: Pull sales invoice lines, credit memo lines, and four categories of GL entries from BigQuery raw layer
4. **Transform phase**: Map fields, assign entity='Vaxxinova Japan', currency='JPY', derive fiscal periods, generate composite IDs (VJP-{docId}-{lineId})
5. **Enrich phase**: Join with fiscal periods, payment terms, shipment methods, conversion rates, country master, and Salesforce account mappings
6. **Union phase**: Merge invoice and credit memo streams
7. **Write phase**: Output to local VJP dataset (TRUNCATE mode) and global dataset (APPEND mode)
8. **Post-job**: Clean up temp files, log completion

### Common Maintenance Tasks
- **Adding a new GL category**: Duplicate an existing tBigQueryInput + tMap pair, adjust the account number filter, and add a new tUnite to merge it into the pipeline
- **Changing fiscal period logic**: Modify the tMap_3 (Map_Sales_Set) expression that derives Fiscal_Period and Fiscal_Year
- **Environment switching**: Set context variables (parameter_JPN_Raw_Env, parameter_VJP_Prefix, parameter_Env_Prefix) in the Talend context configuration

### Dependencies
- **Upstream**: Raw layer must be populated first (see JPN_2 chunk for VJP_Raw jobs)

*[Guide truncated for brevity]*

### JPN_1

## Onboarding Guide: JPN_1 -- Japan Sales Set Ingest Pipeline

### What This Chunk Does
This chunk handles **incremental ingestion** of sales data into the Japan Sales_Set. It is functionally similar to JPN_0 (Build) but focused on an ingestion-oriented workflow with delta awareness.

### Jobs Overview
| Job | Status | Notes |
|-----|--------|-------|
| VJP_Clean_Ingest_Sales_Set | Primary | Main ingestion job |
| VJP_Clean_Ingest_Sales_Set_Backup | Archive | Contains detailed invoice mapping logic |
| VJP_Clean_Ingest_Sales_Set_Backup_2 | Archive | Similar to primary, GL-focused |

### How It Relates to JPN_0
- JPN_0 (Build) and JPN_1 (Ingest) produce the **same Sales_Set output**
- Both read from the same raw sources and reference tables
- The Ingest variant appears to be a parallel development track
- **Recommendation**: Consolidate Build and Ingest into a single parameterized job

### Key Transformation Logic
1. **Invoice Lines**: Entity='Vaxxinova Japan', Category='Actual', Currency='JPY', ID='VJP-{systemId}-{docSystemId}'
2. **Credit Memo Lines**: Same structure but sourced from salesCreditHeaders/Lines
3. **GL Journal Entries**: ID='VJP-GL-GJH-{TransactionNumber}', Product_Id derived from description regex
4. **GL Cost Entries**: Filtered by account number ranges for CoGS, GCR, GRC categories

### Environment Configuration
Currently hardcoded to TEST environment (test_jpn_vjp_raw). To run in production:
- Set context.parameter_JPN_Raw_Env = 'jpn_vjp_raw'
- Set context.parameter_VJP_Prefix = 'jpn_vjp_clean'
- Set context.parameter_Env_Prefix = 'Global_Clean'

### Upstream Dependencies
- Raw ingestion jobs must run first (see JPN_2, JPN_3 for VJP_Raw_* jobs)
- Global_Master tables must be current (Fiscal_Periods, Country_Master)
- US payment terms must be available (VUS_Raw_Env.paymentTerms)

*Additional onboarding guides available for chunks: JPN_2, JPN_3, JPN_4, JPN_5*

## South Africa (SAF)

### SAF_all

## Onboarding Guide: SAF_all -- South Africa (Legacy Folder)

### What This Chunk Does
This chunk contains a **single cloned template** job for South Africa's data pipeline. It is NOT operational.

### Current State
- **1 job**: ITA_VIT_RAW_Copy (cloned from Italy)
- **Status**: Template/development artifact
- **Not adapted**: Still uses Italy's naming and likely Italy's connection parameters

### Recommended Actions
1. Check if the ZAF folder is the canonical South Africa location
2. If yes, this job should be migrated to ZAF or deleted
3. If no, rename to VSA_RAW (Vaxxinova South Africa) and adapt:
   - Update MSSQL connection to South Africa's database
   - Update context variables for SAF-specific BigQuery datasets
   - Update tMap expressions for any schema differences

### Relationship to ZAF
The ZAF_all chunk contains 3 more developed South Africa jobs. Consolidate all South Africa jobs into one folder (recommend ZAF as the ISO standard).

## United States (USA)

### USA_0

## Getting Started with USA_0

### Overview
USA_0 contains 9 Talend jobs responsible for cleaning and transforming raw data from the Vaxxinova US (VUS) Business Central ERP, the Newport Labs (NPL) legacy MSSQL database, and the Animalytix third-party distributor platform.

### Job Categories

**Animalytix Distributor Pipeline:**
- **VUS_Raw_Animalytix_SFTP** -- Retrieves distributor data files from SFTP via Google Cloud Storage (tGSGet)
- **VUS_Raw_Animalytix_v6** -- Parses and loads raw Animalytix data into BigQuery
- **VUS_Clean_Animalytix** -- Cleans and enriches distributor data with fiscal period mapping, writes to Distributor_Sales_Set

**Business Central Invoice & Item Cleaning:**
- **VUS_Clean_BC_Invoice_Set_v5** -- Merges sales invoice lines and credit memo lines (via tUnite) into a unified invoice set
- **VUS_Clean_BC_Item_v8** -- Cleans product/item master data from Business Central
- **VUS_Clean_BC_Item_v8_Salesforce** -- Pushes cleaned item data to Salesforce CRM via tSalesforceOutput
- **VUS_Clean_BC_SalesCreditMemo_v2** -- Cleans sales credit memo records
- **VUS_Clean_BC_SalesInvoices_v15** -- Cleans sales invoice header and line records

**Legacy NPL Cleaning:**
- **NPL_Clean_Account_v4** -- Cleans Newport Labs customer account data from BigQuery raw tables

### Data Flow
1. Raw data is extracted by upstream VUS_Raw_* or USA_NPL_RAW jobs into BigQuery raw tables
2. USA_0 clean jobs read from these raw tables, apply transformations and reference lookups
3. Cleaned data lands in BigQuery clean tables and tHashOutput staging for global aggregation
4. Salesforce-suffixed jobs additionally push master data to Salesforce CRM

### Key Dependencies
- **Upstream**: VUS_Raw_* extraction jobs (USA_4, USA_5, USA_6 chunks), USA_NPL_RAW (USA_3 chunk)
- **Reference Data**: Fiscal_Periods, Salesforce_Country_Mapping, Account_Mapping (Global chunk jobs)
- **Downstream**: VUS_Clean_Ingest_Sales_Set, Global sales-set aggregation pipeline

### Environment Setup
All jobs use Talend context variables for environment switching. Set the context to PROD, TEST, TEST_LINUX, or TEST_MAC depending on your target environment. Connection strings, GCS bucket paths, and Salesforce OAuth credentials are all environment-specific.

### USA_1

## Getting Started with USA_1

### Overview
USA_1 contains 5 Talend jobs for the Newport Labs (NPL) legacy data cleaning pipeline and its Salesforce CRM synchronization. These jobs transform raw NPL data (extracted from MSSQL) into clean BigQuery datasets and push key master data to Salesforce.

### Job Inventory

**Account Sync:**
- **NPL_Clean_Account_v4_Salesforce** -- Reads cleaned NPL customer data from BigQuery and upserts account mappings to Salesforce CRM

**Batch Processing:**
- **NPL_Clean_Batch_v22** -- Cleans raw batch data by joining with item and customer reference tables; writes enriched batches to BigQuery
- **NPL_Clean_Batch_v22_Salesforce** -- Reads cleaned batch data and pushes to Salesforce for sales team visibility

**Invoice Assembly:**
- **NPL_Clean_Invoice_Set_v6** -- Assembles NPL invoice data into the standardized invoice-set format for sales-set aggregation

**Product Sync:**
- **NPL_Clean_Item_v26** -- Cleans NPL product/item master data from raw BigQuery tables

### Data Flow
1. USA_NPL_RAW (in USA_3) extracts tables from MSSQL to BigQuery raw layer
2. NPL_Clean_* jobs read raw tables, apply transformations, write to clean layer
3. Salesforce-suffixed jobs read clean tables and push to CRM
4. NPL_Clean_Invoice_Set_v6 output feeds into VUS_Clean_Ingest_Sales_Set

### Key Dependencies
- **Upstream**: USA_NPL_RAW (USA_3 chunk) for raw data extraction
- **Downstream**: VUS_Clean_Ingest_Sales_Set (USA_3), Global sales-set pipeline
- **External**: Salesforce CRM (for sync jobs only)

*Additional onboarding guides available for chunks: USA_2, USA_3, USA_4, USA_5, USA_6*

## South Africa (ZAF)

### ZAF_all

## Onboarding Guide: ZAF_all -- South Africa (Active Development)

### What This Chunk Does
This chunk contains South Africa's **developing data pipeline**. It is more advanced than the SAF folder but still incomplete.

### Current Pipeline State
```
MSSQL (vw_InvoiceDetails)  -->  VSA_Raw_vw_InvoiceDetails_ETL  -->  BigQuery raw
                                                                         |
                                                                         v
                                                    [MISSING: Clean-layer processing]
                                                                         |
                                                                         v
                                                    DEL_Clean_Sales_Set_Delete_Ingest
```

### Job Inventory
| Job | Function | Status |
|-----|----------|--------|
| VSA_Raw_vw_InvoiceDetails_ETL | Raw invoice extraction | Active |
| ITA_VIT_RAW_Copy | Template (from Italy) | Needs adaptation |
| DEL_Clean_Sales_Set_Delete_Ingest | Sales_Set cleanup | Active |

### What's Missing
1. **Clean-layer entity jobs** (VSA_Clean_Invoice, VSA_Clean_Item, etc.)
2. **Sales_Set builder** (VSA_Clean_Sales_Set)
3. **Salesforce sync** (optional)
4. **Orchestrator job** (ZAF_VSA_RAW or equivalent)

### Next Steps for Development
1. Consolidate SAF and ZAF folders
2. Adapt ITA_VIT_RAW_Copy --> rename to VSA_RAW, update connections
3. Build clean-layer jobs following the Japan or Italy pattern
4. Build Sales_Set builder following VJP_Clean_Build_Sales_Set pattern
5. Test end-to-end with South Africa data

### Key Differences from Other Regions
- Uses a SQL Server view (vw_InvoiceDetails) instead of raw tables
- Pipeline is incomplete -- no clean-layer processing yet
- Two folders (SAF/ZAF) need consolidation

## Scratchpad / Development

### scratchpad_all

## Onboarding Guide: scratchpad_all -- Development Test Area

### What This Chunk Does
This chunk is a **development scratchpad** containing test and prototype jobs. None of these jobs are production-ready.

### Job Inventory
| Job | Purpose | Components |
|-----|---------|------------|
| BigQuery_TEST | Test BQ native + JDBC connectivity | tBigQueryInput, tJDBCInput, tLogRow, tMap |
| sandbox | General prototyping | tMSSqlInput, tMap, tBigQueryOutput |
| test | JSON parsing experiment | tFileInputJSON, tExtractJSONFields, tLogRow |
| test_2 | JSON + BigQuery experiment | tFileInputJSON, tMap, tBigQueryOutput, tHashOutput |
| test_3 | File output test | tFileInputJSON, tFileOutputDelimited, tMap |

### Guidelines
- **DO NOT** schedule or run these jobs against production data
- **DO NOT** rely on these jobs for any operational purpose
- **DO** use them as reference for connectivity patterns if needed
- **CONSIDER** removing or archiving them to reduce repository clutter

### What You Can Learn from These Jobs
- How to set up BigQuery native vs JDBC connectivity (BigQuery_TEST)
- How to parse JSON files/responses (test, test_2)
- How to test MSSQL connectivity (sandbox)
- How to use tHashOutput for in-memory buffering (test_2)

### Security Reminder
These jobs may contain hardcoded credentials, connection strings, or test data references. Review them before sharing the repository externally.
