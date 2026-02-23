# Known Issues & Risk Dashboard

This document aggregates all identified risks, issues, and technical debt across the Vaxxinova Talend platform. Issues are categorized by severity and type to support prioritized remediation and migration planning.

## Summary

**159 issues identified** across the platform:

| Severity | Count |
|----------|-------|
| Critical | 17 |
| High | 49 |
| Medium | 78 |
| Low | 15 |

| Category | Count |
|----------|-------|
| Code Hygiene | 11 |
| Error Handling | 9 |
| Data Integrity | 9 |
| Data Quality | 7 |
| Performance | 7 |
| Security | 5 |
| Authentication | 4 |
| Configuration | 3 |
| Operational | 3 |
| Version Management | 3 |
| Development Hygiene | 3 |
| Data Completeness | 3 |
| API Dependency | 3 |
| Maintainability | 2 |
| API Rate Limiting | 2 |
| Pagination Robustness | 2 |
| Salesforce API Limits | 2 |
| Schema Evolution | 2 |
| Financial Accuracy | 2 |
| Code Duplication | 2 |
| Data Freshness | 2 |
| Single Point of Failure | 2 |
| API Stability | 2 |
| Architecture | 1 |
| Compliance | 1 |
| Business Continuity | 1 |
| Dual-Entity Complexity | 1 |
| ERP Migration Risk | 1 |
| Hidden Raw Dependency | 1 |
| Version Fragmentation | 1 |
| No Salesforce Integration | 1 |
| Central Point of Failure | 1 |
| OAuth Token Management | 1 |
| Dual Protocol Maintenance | 1 |
| Data Dependency Chain | 1 |
| Currency Conversion | 1 |
| Fiscal Calendar Alignment | 1 |
| Legacy Maintenance | 1 |
| Reference Data Dependency | 1 |
| Orchestration | 1 |
| Hardcoded Configuration | 1 |
| Reference Data Freshness | 1 |
| Budget Process Impact | 1 |
| Cross-System Dependency | 1 |
| Authentication Security | 1 |
| Budget Process Criticality | 1 |
| New Market Coverage | 1 |
| Cross-System Reference Data | 1 |
| Version Complexity | 1 |
| Regional Coverage | 1 |
| Product Data Accuracy | 1 |
| Cost Price Freshness | 1 |
| Currency Conversion Consistency | 1 |
| MSSQL Single Point of Failure | 1 |
| Monolithic Invoice Processing | 1 |
| Salesforce Sync Dependency | 1 |
| No Business Central Migration Path | 1 |
| High Version Numbers | 1 |
| Hardcoded Environment References | 1 |
| Cross-Regional Dependency | 1 |
| No Error Handling | 1 |
| Hardcoded Test Environment | 1 |
| Redundant Pipeline Paths | 1 |
| Missing Error Handling | 1 |
| Source System Availability | 1 |
| Multi-Protocol Complexity | 1 |
| Pagination Completeness | 1 |
| Data Volume | 1 |
| No Incremental Extraction | 1 |
| Cross-Regional Code Drift | 1 |
| CRM Integration Failure | 1 |
| Version Proliferation | 1 |
| Implicit Dependencies | 1 |
| No Data Validation | 1 |
| Unfinished Development | 1 |
| Folder Confusion | 1 |
| Credential Exposure | 1 |
| Accidental Execution | 1 |
| External Dependencies | 1 |
| Credential Management | 1 |
| Reference Data Coupling | 1 |
| Legacy System Dependency | 1 |
| Salesforce Sync Failures | 1 |
| Multi-Table Join Complexity | 1 |
| Upstream Dependency Chain | 1 |
| Schema Drift | 1 |
| Token Expiry | 1 |
| Legacy Pattern | 1 |
| Incomplete Pipeline | 1 |
| Folder Duplication | 1 |
| View Dependency | 1 |
| Unmodified Template | 1 |

## Critical Severity Issues (17)

### [Error Handling] BRA / BRA_0

3 of 4 jobs (BIO_Clean_Invoice_Set_v5, BRA_VBR_RAW, Copy_of_VBR_Clean_Build_Sales_Set) have zero error handling -- no tDie, tLogCatcher, or SUBJOB_ERROR flows. Failed runs could silently produce incomplete data or leave truncated tables empty.

**Affected components**: BIO_Clean_Invoice_Set_v5, BRA_VBR_RAW, Copy_of_VBR_Clean_Build_Sales_Set

### [Error Handling] BRA / BRA_1

5 of 6 jobs have zero error handling components. The Build and Ingest jobs process critical sales data without any tDie, tLogCatcher, or SUBJOB_ERROR flows, meaning failures are silent and undetected.

**Affected components**: VBR_Clean_Build_Sales_Set, VBR_Clean_Build_Sales_Set_Backup, VBR_Clean_Ingest_Sales_Set, VBR_Clean_Ingest_Sales_Set, VBR_Clean_Ingest_Sales_Set_Backup

### [Data Integrity] BRA / BRA_1

The DELETE-then-INSERT pattern in Ingest jobs creates a window where the global Sales_Set is empty. If the job fails after DELETE but before INSERT completes, downstream dashboards will show no sales data until manual intervention.

**Affected components**: VBR_Clean_Ingest_Sales_Set tBigQuerySQLRow, VBR_Clean_Ingest_Sales_Set tBigQueryOutput

### [Error Handling] BRA / BRA_2

7 of 8 jobs have no error handling components. The Ingest backup jobs perform destructive DELETE operations against BigQuery without any error catching or rollback capability.

**Affected components**: VBR_Clean_Ingest_Sales_Set_Backup_2, VBR_Clean_Ingest_Sales_Set_Backup_3, VBR_Clean_SalesInvoiceLines, VBR_Clean_SalesInvoices, VBR_Clean_Production_Order_COPY

### [Error Handling] BRA / BRA_3

12 of 17 jobs have zero error handling. Production order processing involves complex multi-table joins and DELETE operations that can silently fail, leaving inconsistent data in the clean layer.

**Affected components**: VBR_Clean_Production_Order_Line_Absorption, VBR_Clean_Production_Order_Line_Consumption_POC, VBR_Clean_Production_Order_Line_Routing, VBR_Clean_Production_Order_POC, VBR_Clean_Production_Order_Register

### [Configuration] BRA / BRA_3

Multiple jobs use hardcoded dataset references (test_bra_vbr_clean, test_bra_vbr_raw) instead of context parameters. These jobs are locked to the test environment and cannot be deployed to production without code changes.

**Affected components**: VBR_Clean_Production_Order_Line_Absorption, VBR_Clean_Production_Order_Line_Consumption_POC, VBR_Clean_Production_Order_Line_POC

### [Error Handling] BRA / BRA_4

16 of 17 jobs have zero error handling. With 19 tBigQuerySQLRow DELETE/UPDATE operations and 40 BigQuery output writes, failed jobs can corrupt production cost data without any alerting or rollback.

**Affected components**: VBR_Update_Pending_Production_Order_Line, VBR_Update_Production_Order, VBR_Clean_CBR_ProductionOrderAbsorptionLines, VBR_Clean_CBR_ProductionOrderConsumptionLines, VBR_Clean_CBR_ProductionOrderRoutingLines

### [Data Integrity] BRA / BRA_4

Write-back pattern in VBR_Update_* jobs modifies production order records with computed cost values. If the cost calculation pipeline produces incorrect results (due to missing data, wrong joins, or aggregation errors), production records will be overwritten with wrong data.

**Affected components**: VBR_Update_Production_Order, VBR_Update_CBR_ProductionOrders, VBR_Update_CBR_Pending_ProductionOrderLines

### [Error Handling] BRA / BRA_5

6 of 7 jobs have zero error handling. REST API failures (timeouts, authentication errors, rate limiting), XML/JSON parse errors, and BigQuery write failures all go undetected. Only BRA_Raw_salesOrder has minimal tLogRow logging.

**Affected components**: Copy_of_BRA_Raw_salesOrder, Copy_of_VBR_Raw_salesOrdersHeaders, VBR_Raw_Customers, VBR_Raw_faturaRegistradaVenda, VBR_Raw_fiscalDocumentTaxEntryFNX

### [Error Handling] BRA / BRA_6

All 9 jobs have zero error handling. The entire manufacturing and inventory raw extraction layer runs without error detection, logging, or alerting. REST API timeouts, parse errors, and BigQuery write failures are all silent.

**Affected components**: VBR_Raw_item, VBR_Raw_itemApplicationEntries, VBR_Raw_itemLedgerEntries, VBR_Raw_Items, VBR_Raw_paymentTerms

### [Error Handling] BRA / BRA_7

All 7 jobs have zero error handling. The most critical extraction jobs in the BRA pipeline (salesInvoiceHeaders/Lines and valueEntries) have no mechanism to detect or alert on extraction failures.

**Affected components**: VBR_Raw_salesInvoiceHeaders, VBR_Raw_salesInvoiceLines, VBR_Raw_salesInvoices_Old, VBR_Raw_salesOrdersHeaders, VBR_Raw_salesOrdersLines

### [Data Integrity] BRA / BRA_7

VBR_Raw_valueEntries is the most data-intensive extraction job and feeds the entire CBR pipeline. The silent pagination termination (tJavaRow try-catch) means a partial extraction will appear successful, leading to incorrect cost calculations downstream.

**Affected components**: VBR_Raw_valueEntries tJavaRow

### [Business Continuity] BRA / BRA_7

salesInvoiceHeaders and salesInvoiceLines are the single source of truth for the Sales_Set pipeline. A failed extraction breaks the entire sales reporting chain (BRA_1 Build -> BRA_1 Ingest -> global reporting). No fallback or retry mechanism exists.

**Affected components**: VBR_Raw_salesInvoiceHeaders, VBR_Raw_salesInvoiceLines

### [Central Point of Failure] GENERIC / GENERIC_all

All Business Central regions depend on these shared extraction jobs. A bug introduced in a GENERIC job affects every region using it simultaneously.

**Affected components**: All GEN_BC_* jobs

### [Cross-System Dependency] Global / Global_3

Salesforce_Account_Mappings is a critical reference table consumed by nearly all global reporting jobs. Any data quality issues here cascade across the entire reporting layer.

**Affected components**: Copy_of_Salesforce_Account_Mappings_v3

### [Cross-System Reference Data] Global / Global_6

Salesforce_Mapped_Accounts is the primary customer dimension table. Data quality issues propagate to every customer-facing report across all regions.

**Affected components**: Salesforce_Mapped_Accounts_v8

### [Incomplete Pipeline] ZAF / ZAF_all

South Africa's pipeline lacks clean-layer processing jobs. Raw data is extracted but cannot be transformed into the Sales_Set without manual intervention or missing jobs.

**Affected components**: Missing: VSA_Clean_* jobs, Missing: VSA_Clean_Sales_Set builder

## High Severity Issues (49)

### [Data Integrity] BRA / BRA_0

Sales_Set table uses TRUNCATE write mode. If a job fails after truncation but before data write completes, the target table will be empty until the next successful run, causing downstream reporting gaps.

**Affected components**: BIO_Clean_Invoice_Set_v5 tBigQueryOutput_1, Copy_of_VBR_Clean_Build_Sales_Set tBigQueryOutput_2, Copy_of_VBR_Clean_Build_Sales_Set tBigQueryOutput_3

### [Configuration] BRA / BRA_0

Copy_of_VBR_Clean_Build_Sales_Set maps Entity as 'Vaxxinova United States' instead of a Brazil-specific entity name. This may cause incorrect entity attribution in the global Sales_Set, affecting cross-subsidiary reporting.

**Affected components**: Copy_of_VBR_Clean_Build_Sales_Set tMap_1

### [Architecture] BRA / BRA_0

Two parallel sales pipelines (legacy MSSQL-based and Business Central-based) both write to Sales_Set. Without clear orchestration, there is a risk of one pipeline overwriting the other's output, or double-counting sales data.

**Affected components**: BIO_Clean_Invoice_Set_v5, Copy_of_VBR_Clean_Build_Sales_Set

### [Code Hygiene] BRA / BRA_1

Multiple duplicate/backup jobs (2x Build, 2x Ingest, plus backups = 6 total) create maintenance confusion. It is unclear which version is canonical, and changes must be propagated to all variants manually.

**Affected components**: VBR_Clean_Build_Sales_Set (x2), VBR_Clean_Ingest_Sales_Set (x2), VBR_Clean_Build_Sales_Set_Backup, VBR_Clean_Ingest_Sales_Set_Backup

### [Data Quality] BRA / BRA_1

No validation or row count checks between Build and Ingest steps. If the Build job produces zero rows due to a source data issue, the Ingest job will delete all existing records and replace them with nothing.

**Affected components**: VBR_Clean_Ingest_Sales_Set

### [Code Hygiene] BRA / BRA_2

5 of 8 jobs are backup, debug, or copy variants that add maintenance overhead without clear production value. Duplicate VBR_Clean_Ingest_Sales_Set_Backup_3 entries suggest version control issues.

**Affected components**: VBR_Clean_Ingest_Sales_Set_Backup_2, VBR_Clean_Ingest_Sales_Set_Backup_3 (x2), VBR_Clean_Ingest_Sales_Set_DEBUG (x2), VBR_Clean_Production_Order_COPY

### [Data Integrity] BRA / BRA_2

13 tBigQuerySQLRow components performing DELETE operations across backup/debug jobs. If any of these are accidentally triggered, they will delete production data from the global Sales_Set without recourse.

**Affected components**: All Ingest variants (tBigQuerySQLRow components)

### [Code Hygiene] BRA / BRA_3

5+ jobs are COPY, POC, or test variants that have not been promoted or cleaned up. This creates confusion about which version is canonical and increases maintenance burden.

**Affected components**: VBR_Clean_Production_Order_Line_Absorption_COPY, VBR_Clean_Production_Order_Line_Consumption_POC, VBR_Clean_Production_Order_Line_POC, VBR_Clean_Production_Order_POC, VBR_Cost_Breakdown_Report_Test

### [Data Integrity] BRA / BRA_3

The delta processing DELETE-INSERT pattern in Production_Order_Line_Absorption can orphan records if the INSERT fails after the DELETE. No transaction wrapping or rollback mechanism exists.

**Affected components**: VBR_Clean_Production_Order_Line_Absorption tBigQuerySQLRow_1

### [Code Hygiene] BRA / BRA_4

7 of 17 jobs are BACKUP, COPY, or DEBUG variants. VBR_Update_CBR_ProductionOrders alone has 4 variants (BACKUP, DEBUG, DEBUG_2). This creates confusion about which version is canonical and increases risk of running the wrong variant.

**Affected components**: VBR_Update_Production_Order_COPY, VBR_Clean_CBR_ProductionOrderAbsorptionLines_Backup_1, VBR_Clean_CBR_ProductionOrderConsumptionLines_BACKUP, VBR_Clean_CBR_ProductionOrderConsumptionLines_BACKUP_2, VBR_Update_CBR_ProductionOrders_BACKUP

### [Data Quality] BRA / BRA_4

VBR_Cost_Breakdown_Report_Test_2 contains a tFilterRow with default template Java code ('input_row.columnName1.equals(input_row.columnName2)') that may not have been customized for the actual filter requirement. If used, it could pass or reject records incorrectly.

**Affected components**: VBR_Cost_Breakdown_Report_Test_2 tFilterRow_11

### [Operational] BRA / BRA_4

CBR jobs must execute in a specific order (line-level before order-level before update). Without orchestration to enforce this sequence, running jobs out of order will produce incorrect cost summaries or write stale data back to production orders.

**Affected components**: All VBR_Clean_CBR_* and VBR_Update_CBR_* jobs

### [Security] BRA / BRA_5

API credentials (BC secret key) are stored in flat files on the execution server, loaded at runtime via tFileInputDelimited. If the file permissions are not restrictive, credentials could be exposed. No encryption at rest is evident.

**Affected components**: All jobs (tFileInputDelimited for BC_Secret_Key_Context)

### [Data Integrity] BRA / BRA_5

The tJavaRow pagination controller uses a try-catch that silently terminates extraction on any exception, printing 'All Records Processed'. This masks actual API errors, potentially causing partial data extraction that appears successful.

**Affected components**: All jobs (tJavaRow pagination loop)

### [Compliance] BRA / BRA_5

faturaRegistradaVenda and fiscalDocumentTaxEntryFNX extract Brazilian tax compliance data. If these jobs fail silently, tax reporting data in the warehouse becomes stale, potentially causing compliance issues with SEFAZ (Brazilian tax authority).

**Affected components**: VBR_Raw_faturaRegistradaVenda, VBR_Raw_fiscalDocumentTaxEntryFNX

### [Data Integrity] BRA / BRA_6

Silent pagination termination: the tJavaRow try-catch pattern masks API errors as successful completion. If the BC API returns an error mid-extraction, the job will stop paginating and report success with incomplete data.

**Affected components**: All 9 jobs (tJavaRow pagination controller)

### [Maintainability] BRA / BRA_6

9 independent copies of the same SOAP extraction pattern with no shared template or parent job. Any change to the extraction framework (e.g., new authentication method, response format change) must be manually applied to all 9 jobs.

**Affected components**: All 9 jobs

### [Security] BRA / BRA_6

Business Central API credentials stored in flat files on disk. The BC_Secret_Key_Context file contains authentication secrets that could be exposed if server file permissions are misconfigured.

**Affected components**: All jobs (tFileInputDelimited for BC_Secret_Key_Context)

### [Operational] BRA / BRA_6

Failure of VBR_Raw_paymentTerms or VBR_Raw_productionOrders goes undetected but causes cascading quality issues in downstream clean-layer jobs (BRA_1 sales enrichment and BRA_3/BRA_4 production analytics respectively).

**Affected components**: VBR_Raw_paymentTerms, VBR_Raw_productionOrders

### [Maintainability] BRA / BRA_7

23 extraction jobs across 3 chunks (BRA_5/6/7) all independently implement the same SOAP extraction pattern. A change to the BI_Service API, authentication method, or response format requires updating all 23 jobs individually.

**Affected components**: All 7 jobs in BRA_7, All jobs in BRA_5 and BRA_6

### [Security] BRA / BRA_7

Consistent with BRA_5 and BRA_6: API credentials stored in flat files on the execution server. All 23 raw extraction jobs across 3 chunks share the same credential management approach with no encryption at rest.

**Affected components**: All 7 jobs (tFileInputDelimited for BC_Secret_Key_Context)

### [Dual-Entity Complexity] DEU / DEU_all

Maintaining separate job families for VDA (Business Central) and VDD (legacy) doubles the maintenance burden and creates risk of schema drift between the two entity outputs.

**Affected components**: VDA_BC_Clean_* jobs, VDD_Clean_* jobs

### [ERP Migration Risk] DEU / DEU_all

During the VDD-to-VDA migration, data may exist in both systems simultaneously, risking duplication in the Sales_Set if records are not properly deduplicated.

**Affected components**: VDA_Clean_Sales_Set_Delete_Ingest

### [OAuth Token Management] GENERIC / GENERIC_all

The Create_Business_Central_Token component handles Azure AD authentication. If the app registration expires, all BC extractions across all regions fail.

**Affected components**: Create_Business_Central_Token

### [API Rate Limiting] GENERIC / GENERIC_all

Multiple regions running these jobs simultaneously could exhaust Business Central API rate limits, causing extraction failures or throttling.

**Affected components**: All tRESTClient components (13 instances)

### [Data Dependency Chain] Global / Global_0

Global_Build_Sales_Set depends on all regional clean datasets being current. Any upstream regional job failure silently propagates stale data to consolidated reports.

**Affected components**: Global_Build_Sales_Set, Budget_Build_Sales_Set

### [Hardcoded Configuration] Global / Global_2

Fiscal year filter '2023/2024' is hardcoded in the SQL query. Must be manually updated for each budget cycle, creating risk of using wrong baseline year.

**Affected components**: Global_Clean_Sales_Set_Budgeting_v5

### [Budget Process Impact] Global / Global_2

Errors in this job directly impact the annual budget planning process. Incorrect baselines lead to unrealistic budget targets across all regions.

**Affected components**: Global_Clean_Sales_Set_Budgeting_v5

### [Budget Process Criticality] Global / Global_4

This is the primary data source for all budget-vs-actual reporting. Extraction failures during the budgeting cycle delay financial planning and management reporting.

**Affected components**: Salesforce_Budget_v8

### [Financial Accuracy] Global / Global_5

Stale or incorrect exchange rates cause systematic misstatement of all multi-currency financial reports. This is a critical reference data job.

**Affected components**: Salesforce_Dated_Conversion_Rates_v3

### [Product Data Accuracy] Global / Global_7

Product_Master is consumed by budgeting and reporting across all regions. Incorrect product attributes (codes, names, categories) cause cross-cutting reporting errors.

**Affected components**: Salesforce_Products, Salesforce_Standard_Cost_Prices_v1

### [Currency Conversion Consistency] Global / Global_7

Update_Currency_Conversions must be run after Salesforce_Dated_Conversion_Rates (Global_5) to keep derived rate tables in sync with raw rates.

**Affected components**: Update_Currency_Conversions

### [MSSQL Single Point of Failure] ITA / ITA_all

Italy's entire raw layer depends on a single tMSSqlInput connection to the on-premises ERP database. Network or database issues halt the entire pipeline.

**Affected components**: ITA_VIT_RAW (tMSSqlInput)

### [Code Duplication] JPN / JPN_0

Three near-identical job variants (active, copy, backup) with 134 components total. Changes must be applied to all three, creating high risk of configuration drift and inconsistent behavior across variants.

**Affected components**: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set_Backup

### [Code Duplication] JPN / JPN_1

Three near-identical Ingest job variants mirror the same pattern seen in JPN_0's Build variants, compounding the total duplicate job count to six. Combined with JPN_0, this represents significant redundancy.

**Affected components**: VJP_Clean_Ingest_Sales_Set, VJP_Clean_Ingest_Sales_Set_Backup, VJP_Clean_Ingest_Sales_Set_Backup_2

### [Hardcoded Test Environment] JPN / JPN_1

All three Ingest variants reference 'test_jpn_vjp_raw' dataset directly in SQL queries. Running these in production without context variable overrides would read from test data.

**Affected components**: All tBigQueryInput components across all three jobs

### [Source System Availability] JPN / JPN_2

On-premises MSSQL connectivity is a single point of failure. Network issues, VPN drops, or database maintenance windows will halt raw data extraction for Japan.

**Affected components**: All tMSSqlInput components (9 instances)

### [API Rate Limiting] JPN / JPN_3

10 jobs making REST API calls to Business Central could hit rate limits if run simultaneously. The pagination logic in tJavaRow makes repeated calls that compound the API load.

**Affected components**: All tREST components (18 instances across 10 jobs)

### [Pagination Completeness] JPN / JPN_3

If the tJavaRow pagination logic fails to follow all @odata.nextLink pages, data will be silently incomplete. No record count validation is apparent to detect partial loads.

**Affected components**: tJavaRow components across all 10 jobs, tHashOutput buffering

### [CRM Integration Failure] JPN / JPN_4

VJA_Sync_Product_Salesforce_v3 writes to Salesforce. If Salesforce API credentials expire, rate limits are hit, or the Salesforce schema changes, product data in CRM becomes stale. Sales teams depend on this data.

**Affected components**: VJA_Sync_Product_Salesforce_v3, tSalesforceOutput

### [Single Point of Failure] JPN / JPN_5

VJP_Clean_Sales_Set_Delete_Ingest is the sole job managing Sales_Set lifecycle. If it fails, the Sales_Set may contain stale or inconsistent data that propagates to all downstream reports.

**Affected components**: VJP_Clean_Sales_Set_Delete_Ingest

### [Unfinished Development] SAF / SAF_all

The sole job is an unmodified clone of Italy's raw extraction. If accidentally executed, it would attempt to connect to Italy's MSSQL server and write to Italy's BigQuery dataset.

**Affected components**: ITA_VIT_RAW_Copy

### [Reference Data Coupling] USA / USA_0

Multiple clean-layer jobs depend on Fiscal_Periods, Salesforce_Country_Mapping, and Account_Mapping reference tables. If these reference tables are stale, corrupted, or unavailable, all downstream clean and aggregation jobs will produce incorrect results.

**Affected components**: VUS_Clean_Animalytix, VUS_Clean_BC_Invoice_Set_v5, VUS_Clean_BC_SalesInvoices_v15, NPL_Clean_Account_v4

### [Legacy System Dependency] USA / USA_1

All NPL jobs depend on the USA_NPL_RAW extraction from an on-premises MSSQL database. Network connectivity issues or MSSQL server downtime will cascade to all 5 jobs in this chunk.

**Affected components**: NPL_Clean_Account_v4_Salesforce, NPL_Clean_Batch_v22, NPL_Clean_Batch_v22_Salesforce, NPL_Clean_Invoice_Set_v6, NPL_Clean_Item_v26

### [API Stability] USA / USA_5

Custom BI_Service Codeunit is a non-standard BC extension. BC platform upgrades could break the SOAP interface if not regression-tested.

**Affected components**: VUS_Raw_dimensionSetEntries

### [Pagination Robustness] USA / USA_5

The tJavaRow pagination loop has a basic try-catch that prints 'All Records Processed' on exception, which could silently mask extraction errors and lead to incomplete data loads.

**Affected components**: VUS_Raw_dimensionSetEntries

### [Financial Accuracy] USA / USA_6

Credit memo extraction failures directly cause revenue overstatement in downstream reports. This job must be treated as financially critical with appropriate alerting.

**Affected components**: VUS_Raw_salesCreditMemoLines

### [Error Handling] USA / USA_6

Silent exception handling in pagination tJavaRow could mask partial extraction failures, leading to incomplete credit memo data without detection.

**Affected components**: VUS_Raw_salesCreditMemoLines

### [Unmodified Template] ZAF / ZAF_all

ITA_VIT_RAW_Copy is still an unmodified Italian clone. If executed, it would connect to Italy's MSSQL server rather than South Africa's.

**Affected components**: ITA_VIT_RAW_Copy

## Medium Severity Issues (78)

- **API Dependency** (3 issues): Affects JPN, USA
  - Business Central API availability and rate limits could throttle or block data extraction. API schema changes (field additions/removals) could break J...
  - Raw extraction jobs depend on Business Central API availability, same risk as JPN_3. Sales orders may contain large volumes requiring paginated extrac...
- **API Stability** (1 issues): Affects USA
  - Dependency on custom BI_Service SOAP Codeunit -- same BC upgrade risk as USA_5.
- **Accidental Execution** (1 issues): Affects scratchpad
  - If accidentally scheduled or included in an orchestration, these test jobs could read from or write to production systems with unpredictable results.
- **Authentication** (4 issues): Affects Global
  - Uses Salesforce Basic login authentication. Salesforce is deprecating password-based auth in favor of OAuth 2.0.
  - Uses Salesforce Basic login authentication, which is being deprecated by Salesforce in favor of OAuth 2.0.
- **Authentication Security** (1 issues): Affects Global
  - Uses Basic login (username/password) rather than OAuth 2.0. Basic auth is being deprecated by Salesforce. Plan migration to OAuth flow.
- **Code Hygiene** (3 issues): Affects BRA
  - Copy_of_BRA_Raw_salesOrder and Copy_of_VBR_Raw_salesOrdersHeaders are duplicate jobs that add confusion about which version is used in production orch...
  - Potential duplicate item extraction (VBR_Raw_item vs VBR_Raw_Items) and deprecated credit memo extraction (VBR_Raw_salesCreditMemos_Old) add confusion...
- **Configuration** (1 issues): Affects BRA
  - Test/sandbox job VBR_Cost_Breakdown_Report_Test_2 reads from hardcoded test datasets. If accidentally scheduled in production, it will read test data ...
- **Cost Price Freshness** (1 issues): Affects Global
  - Standard cost prices must be updated when manufacturing costs change. Stale cost data causes inaccurate margin and COGS reporting.
- **Credential Exposure** (1 issues): Affects scratchpad
  - Test jobs may contain hardcoded connection strings, database passwords, or API keys that were used during development. These could be exposed if the r...
- **Credential Management** (1 issues): Affects USA
  - Salesforce OAuth credentials and Business Central API tokens are stored in Talend context variables. Rotation or expiry of these credentials will brea...
- **Cross-Regional Code Drift** (1 issues): Affects JPN
  - VUS_Raw_salesOrdersLines_Copy is a cloned US job. If the US original is updated, this copy will not receive those updates, leading to divergent behavi...
- **Cross-Regional Dependency** (1 issues): Affects JPN
  - Japan sales pipeline depends on US raw data (VUS_Raw_Env) for payment terms and shipment methods. If the US raw ingestion fails or schema changes, Jap...
- **Currency Conversion** (1 issues): Affects Global
  - Budget_Current_Conversion_Rate must be up-to-date. Stale exchange rates cause systematic over/under-statement of budget figures in the reporting curre...
- **Data Completeness** (3 issues): Affects Global, USA
  - TRUNCATE-and-reload means any Salesforce API timeout or partial extraction results in incomplete budget data until the next successful run.
  - If the finance team fails to enter rates for new periods in Salesforce, downstream conversion jobs may produce null amounts or use outdated rates.
- **Data Freshness** (2 issues): Affects JPN, USA
  - If the orchestrator job (JPN_VJP_RAW) fails silently on a subset of child jobs, downstream clean-layer processing may run against stale data without a...
  - Full TRUNCATE-and-reload strategy means any job failure results in stale data until the next successful run. No incremental fallback exists.
- **Data Integrity** (1 issues): Affects JPN
  - The delete-then-insert pattern relies on delta tables (salesInvoicesDelta, salesCreditMemosDelta). If delta tracking is misconfigured or the delta tab...
- **Data Quality** (5 issues): Affects BRA, Global, USA
  - Persistent typo 'Produtct_Description' propagated from MSSQL source through BigQuery raw layer to Salesforce Product2 Name and Product_Name_Local__c f...
  - Backup_2 reads from salesInvoiceHeaders instead of salesInvoices. If this backup is used as a fallback, the output schema may differ from what downstr...
- **Data Volume** (1 issues): Affects JPN
  - generalLedgerEntry and itemLedgerEntries can contain millions of records. Full extraction via paginated API calls may be very slow compared to direct ...
- **Development Hygiene** (1 issues): Affects Global
  - 'Copy_of_' development variant should not be used for production runs. Risk of deploying the wrong version to production scheduling.
- **Dual Protocol Maintenance** (1 issues): Affects GENERIC
  - Maintaining both direct API and OData extraction variants doubles the testing and update effort when Business Central API versions change.
- **External Dependencies** (1 issues): Affects USA
  - Animalytix pipeline depends on third-party SFTP file delivery and Google Cloud Storage availability. Late or missing file drops will cause the Distrib...
- **Fiscal Calendar Alignment** (1 issues): Affects Global
  - Fiscal_Periods reference table must be maintained yearly. Missing or incorrect fiscal period definitions will cause data to be misclassified or droppe...
- **Folder Confusion** (1 issues): Affects SAF
  - South Africa has two folders (SAF and ZAF) with no clear documentation on which is canonical. This creates confusion for developers about where to pla...
- **Folder Duplication** (1 issues): Affects ZAF
  - ITA_VIT_RAW_Copy exists in both SAF and ZAF folders. Without consolidation, developers may unknowingly work in the wrong folder, creating divergent co...
- **Hardcoded Environment References** (1 issues): Affects JPN
  - The main and backup job variants contain hardcoded dataset names (jpn_vjp_raw, test_jpn_vjp_raw) instead of using context variables, making them unsaf...
- **Hidden Raw Dependency** (1 issues): Affects DEU
  - VDA raw data extraction is not in this chunk -- it depends on GENERIC BC jobs. If GENERIC jobs are modified without considering Germany's needs, VDA c...
- **Implicit Dependencies** (1 issues): Affects JPN
  - The execution order between VJA clean jobs is not enforced by an orchestrator. If jobs run out of order (e.g., Invoice_Set before Invoice_Item), resul...
- **Missing Error Handling** (1 issues): Affects JPN
  - No OnSubjobError, tDie, or conditional flows exist. A failed DELETE step does not prevent the subsequent INSERT, risking data duplication.
- **Monolithic Invoice Processing** (1 issues): Affects ITA
  - VIT_Clean_SalesInvoices_and_Lines combines header and line processing in one job. If it fails, both header and line data are lost. Splitting would imp...
- **Multi-Protocol Complexity** (1 issues): Affects JPN
  - Using both direct MSSQL queries and REST API calls within the same pipeline increases operational complexity. Different failure modes, authentication ...
- **Multi-Table Join Complexity** (1 issues): Affects USA
  - NPL_Clean_Batch_v22 joins three source tables (batch, item, customer) in a single tMap. Schema changes in any of these upstream tables will break the ...
- **No Data Validation** (1 issues): Affects JPN
  - Clean-layer jobs perform transformation but no apparent data validation (row counts, null checks, referential integrity). Bad raw data will silently p...
- **No Error Handling** (1 issues): Affects JPN
  - The jobs lack explicit error-handling flows (tDie, tWarn, or OnSubjobError connections). If a BigQuery read or write fails mid-pipeline, partial data ...
- **No Incremental Extraction** (1 issues): Affects JPN
  - It is unclear whether these jobs extract only delta (changed) records or perform full extractions each run. Full extractions of large tables would be ...
- **Operational** (1 issues): Affects BRA
  - Multiple jobs write to the same target tables (Sales_Set). Without scheduling coordination, concurrent execution of primary and backup jobs could caus...
- **Orchestration** (1 issues): Affects Global
  - Parameterized design assumes a parent job orchestrates per-region execution. If the parent fails mid-sequence, partial regions may be consolidated whi...
- **Pagination Robustness** (1 issues): Affects GENERIC
  - The tJavaFlex pagination loops must handle edge cases: empty pages, API timeouts mid-pagination, and malformed @odata.nextLink URLs. Failure in pagina...
- **Performance** (5 issues): Affects BRA, USA
  - 67 tBigQueryInput components suggest extensive data reads. While incremental loading is available for invoices/credit memos, all reference tables (fis...
  - 38 tBigQueryOutput components and 16 tAggregateRow components indicate heavy I/O and computation. Multiple aggregation levels (line, order, summary) m...
- **Redundant Pipeline Paths** (1 issues): Affects JPN
  - Both Build (JPN_0) and Ingest (JPN_1) job families produce the same Sales_Set output, creating confusion about which pipeline is authoritative and act...
- **Reference Data Dependency** (1 issues): Affects Global
  - Missing or stale Fiscal_Periods or Salesforce_Countries reference data causes enrichment joins to fail silently (nulls in enriched fields), degrading ...
- **Reference Data Freshness** (1 issues): Affects Global
  - Enrichment depends on four reference datasets being current. Stale Product_Master or Salesforce mappings produce incorrect budget baselines.
- **Regional Coverage** (1 issues): Affects Global
  - Each new region onboarded requires updating the account mapping logic. Missing mappings for new regions cause null account references in global report...
- **Salesforce API Limits** (2 issues): Affects Global, USA
  - Salesforce enforces API call limits. If this job runs too frequently or extracts too many records, it could exhaust the daily API allocation affecting...
  - With 2 tSalesforceInput reads and 2 tSalesforceOutput writes per execution, this job consumes significant Salesforce API quota. During bulk product ca...
- **Salesforce Sync Dependency** (1 issues): Affects ITA
  - IZO_Sync_Product_Salesforce_v5 writes to Salesforce CRM. API credential expiry, rate limits, or schema changes will cause sync failures, making CRM pr...
- **Salesforce Sync Failures** (1 issues): Affects USA
  - Salesforce API rate limits or OAuth token expiry can cause sync jobs to fail silently or partially, leading to stale CRM data without clear alerting t...
- **Schema Drift** (1 issues): Affects USA
  - NPL raw extraction queries select explicit column lists. Any schema change in the source MSSQL d_Customer or d_Entity tables will cause extraction fai...
- **Schema Evolution** (2 issues): Affects Global, JPN
  - 8 versions indicate frequent schema changes. Each Budget__c modification in Salesforce requires corresponding updates to the extraction and downstream...
  - Business Central API updates may add or rename fields. The tExtractXMLField and tExtractJSONFields components use fixed field paths that will break if...
- **Security** (2 issues): Affects BRA, USA
  - Salesforce connection component is labeled 'USA_Vaxxinova_Salesforce', suggesting shared credentials across subsidiaries. If the USA Salesforce org cr...
  - OAuth credentials stored in external flat files. File system permissions must be strictly controlled to prevent credential exposure.
- **Token Expiry** (1 issues): Affects USA
  - OAuth tokens have limited validity. Long-running extractions or retries may fail if the token expires mid-execution without refresh logic.
- **Upstream Dependency Chain** (1 issues): Affects USA
  - This job depends on a three-step chain: USA_NPL_RAW (MSSQL extraction) -> NPL_Clean_Item_v26 (cleaning) -> NPL_Clean_Item_v26_Salesforce (sync). A fai...
- **Version Complexity** (1 issues): Affects Global
  - 8 versions indicate significant accumulated complexity. Understanding all mapping rules and their historical context is challenging for new team membe...
- **Version Fragmentation** (1 issues): Affects DEU
  - Different jobs are at different version levels (v1 through v4) with no clear lifecycle management. Multiple active versions could cause inconsistent p...
- **Version Management** (3 issues): Affects Global, JPN
  - Multiple versions (at least v1-v5) of this job likely exist in the repository. Without clear deprecation of old versions, the wrong version could be s...
  - Two versions of Salesforce_Products (0.1 and 1.0) exist. Running the wrong version could extract incorrect or incomplete product data.
- **Version Proliferation** (1 issues): Affects JPN
  - Multiple job versions (v1, v2, v3) exist without clear lifecycle management. Older versions may be accidentally run, producing inconsistent results.
- **View Dependency** (1 issues): Affects ZAF
  - VSA_Raw_vw_InvoiceDetails_ETL depends on a SQL Server view (vw_InvoiceDetails) maintained in the source ERP. Changes to this view will break extractio...

## Low Severity Issues (15)

- **Code Hygiene** (4 issues): Affects BRA, GENERIC, SAF, scratchpad
  - Copy_of_VBR_Clean_Build_Sales_Set is a duplicated version of VBR_Clean_Build_Sales_Set from BRA_1 chunk. Having a 'Copy_of' prefix suggests it was clo...
  - webhook_client_test is a development/test job that should not be in the production repository. It may contain test credentials or URLs.
- **Development Hygiene** (2 issues): Affects USA
  - Presence of 'notworking' and 'Copy_of' prefixed jobs indicates incomplete cleanup of development artifacts that could cause confusion in production.
  - 'Copy_of_' prefix indicates this is a non-production variant. Risk of confusion if both this and the original run in the same environment.
- **High Version Numbers** (1 issues): Affects ITA
  - VIT_Clean_Sales_Set_v6 and IZO_Sync_Product_Salesforce_v5 have high version numbers, indicating frequent changes. These jobs should be closely monitor...
- **Legacy Maintenance** (1 issues): Affects Global
  - Legacy_* jobs maintain old transformation logic. Without clear documentation of differences from current jobs, maintaining both paths increases comple...
- **Legacy Pattern** (1 issues): Affects USA
  - SOAP integration pattern is being superseded by REST API patterns (as seen in USA_4). Consider migrating to the REST-based approach for consistency an...
- **New Market Coverage** (1 issues): Affects Global
  - When Vaxxinova enters new geographic markets with new currencies, corresponding rates must be added in Salesforce and validated in BigQuery.
- **No Business Central Migration Path** (1 issues): Affects ITA
  - Italy still uses legacy MSSQL for raw extraction. When the migration to Business Central occurs, all raw extraction and potentially clean-layer jobs w...
- **No Salesforce Integration** (1 issues): Affects DEU
  - Unlike Japan, Germany has no Salesforce product sync job. If CRM integration is needed, it must be built from scratch or adapted from Japan's VJA_Sync...
- **Performance** (2 issues): Affects Global, USA
  - External sort buffer of 50,000 rows may need tuning as sales volumes grow. Insufficient buffer causes excessive disk I/O; too large a buffer risks out...
  - d_Customer extraction pulls 60+ columns with no WHERE clause filter, resulting in full table scans on every execution. As customer base grows, this ma...
- **Single Point of Failure** (1 issues): Affects USA
  - As the sole job synchronizing NPL product data to Salesforce, any failure in this job means the sales team will not see updated Newport Labs product i...

---

*This risk dashboard should be reviewed regularly and updated as issues are remediated or new risks are identified during migration.*