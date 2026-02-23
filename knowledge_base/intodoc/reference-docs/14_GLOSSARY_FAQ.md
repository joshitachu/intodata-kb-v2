# Glossary & FAQ

This document provides definitions for key terms and answers to common questions about the Vaxxinova Talend integration platform. It is intended for onboarding new team members and as a quick reference for anyone working with the platform.

## Glossary

**@odata.nextLink**
: A URL returned in OData API responses indicating the next page of results. The pagination loop follows this link until it is absent, indicating all data has been retrieved.

**Absorption**
: The process of allocating overhead costs (indirect manufacturing costs like facility, utilities, equipment depreciation) to individual production orders based on routing operations. VBR_Clean_Production_Order_Line_Absorption calculates these allocations.

**Absorption Lines**
: Cost allocations from overhead pools (facility costs, equipment depreciation, utilities) to individual production orders based on routing operation time. Calculated by multiplying overhead rates by the time spent on each manufacturing step.

**Account Mapping**
: The process of linking customer identifiers from different systems (ERP customer codes, Salesforce account IDs) to create a unified customer view

**AccrualLedgerEntries**
: Business Central entity representing accrual-based accounting entries that differ from cash-based postings. Used for revenue recognition and expense accrual reporting.

**Animalytix**
: A third-party veterinary product distribution analytics platform whose data is ingested via SFTP/GCS file drops and cleaned into the Distributor_Sales_Set.

**Basic Login**
: Salesforce authentication method using username + password + security token (as opposed to OAuth 2.0 web flow)

**Batch**
: In the Newport Labs context, a production batch represents a manufacturing run of a specific vaccine or biological product, tracked from production through sale.

**BC (Business Central)**
: Microsoft Dynamics 365 Business Central, the cloud-based ERP system. VDA_BC jobs specifically process data from this platform.

**BC REST API**
: The OData-based REST API provided by Business Central for external data access, using the endpoint pattern api.businesscentral.dynamics.com/v2.0/

**BI_Service**
: A custom SOAP codeunit in Microsoft Dynamics 365 Business Central designed for bulk data extraction. Exposes the GetBIData4TableNoAndFieldNoListWithCount method that returns data as JSON within SOAP XML envelopes, with pagination and field filtering support.

**BI_Service Codeunit**
: A custom SOAP web service deployed in Business Central that provides optimized bulk data extraction with pagination, date filtering, and field selection for BI/DWH use cases

**BigQuery_TEST**
: A test job that validates BigQuery connectivity via both the native Talend component (tBigQueryInput) and JDBC driver (tJDBCInput).

**BIO / Biovet**
: The legacy brand name for Vaxxinova's Brazilian operations (Biovet Laboratorios). Jobs prefixed with 'BIO_' operate on the older Biovet MSSQL-based data model.

**Budget__c**
: Custom Salesforce object where Vaxxinova sales teams enter annual budget targets by account, product, fiscal period, and geography

**Budget_Current_Conversion_Rate**
: A global reference table containing exchange rates used for converting local currency (BRL) amounts to the corporate reporting currency. Uses budget rates rather than spot market rates for consistency with financial planning.

**Budget_Master**
: The central budget/forecast data table containing planned sales volumes, amounts, and targets by account, product, and period

**Budgeting Sales Set**
: A specialized view of historical actual sales data enriched with product, account, and geographic dimensions, used as the baseline for annual budget planning

**Business Central**
: Microsoft Dynamics 365 Business Central -- VUS's cloud-based ERP system for managing finance, sales, and operations

**Category='Actual'**
: A classification tag applied to real sales transactions (invoices and credit memos) to distinguish them from budget forecasts or manual journal entries in the unified Sales_Set.

**Category__c**
: Classifies the budget record type (Budget vs Forecast), enabling separation of initial annual budgets from in-year forecast revisions

**CBR (Cost Breakdown Report)**
: An analytical framework that decomposes the total cost of a vaccine production order into its constituent cost categories: direct materials (consumption), indirect costs (absorption), labor and machine time (routing), and variances. The primary management tool for manufacturing cost control.

**Clean Layer**
: The transformation tier in the BigQuery data warehouse where raw data is joined, filtered, enriched, and standardized into business-ready datasets. Jobs prefixed with 'VBR_Clean_' operate in this layer.

**cleanup_temp_file_storage**
: A custom Talend component that removes temporary CSV files created during BigQuery loading. These temp files are created by tBigQueryOutput as an intermediate step (CSV -> GCS -> BigQuery) and need cleanup after the load completes.

**COGS (Cost of Goods Sold)**
: The total cost of manufacturing the products sold, calculated using standard cost prices and actual quantities -- a key profitability metric

**Combined Invoice Processing**
: Italy's approach of handling invoice headers and lines in a single job (VIT_Clean_SalesInvoices_and_Lines) rather than separate jobs, reducing pipeline complexity.

**Composite ID**
: A concatenated identifier (e.g., VJP-{docId}-{lineId}) that uniquely identifies each Sales_Set record across the global dataset by combining entity prefix, document ID, and line-level ID.

**Consumption**
: Raw materials and components consumed during vaccine production. Tracked as negative item ledger entries (inventory decreases) linked to specific production orders.

**Consumption Lines**
: Direct material costs representing raw materials, components, and packaging consumed during vaccine production. Each consumption line links a material item to a production order with quantity and cost.

**context variable**
: Talend environment-specific parameter (e.g., connection strings, dataset prefixes) that varies between PROD, TEST, TEST_LINUX, and TEST_MAC deployments. Enables the same job design to run in different environments.

**Context-parameterized template**
: A job design pattern where all region-specific values (URLs, dataset names, credentials) are externalized into context variables, allowing the same job to serve multiple regions.

**context.parameter_VJP_Prefix**
: Context variable resolving to the Japan clean-layer BigQuery dataset name (e.g., 'jpn_vjp_clean' in PROD or 'test_jpn_vjp_clean' in TEST).

**ConversionRate**
: The exchange rate multiplier for converting from the source currency to the corporate reporting currency (likely EUR)

**Cost Breakdown Report (CBR)**
: An analytical output that decomposes the total cost of a production order into its constituent parts: direct materials, direct labor, overhead absorption, and variances. Used by management to assess production efficiency and product profitability.

**Cost_Price__c**
: Standard cost price per unit for the budgeted product, enabling margin calculation at the budget level

**Country_Master**
: Reference table mapping country codes to full country names, regions, and other geographic classification attributes

**Create_Business_Central_Token**
: A custom Talend component (joblet) that acquires an OAuth2 bearer token from Azure Active Directory for authenticating against Business Central APIs.

**Create_Business_Central_Token_v2**
: A custom Talend component that generates OAuth2 access tokens for Business Central API authentication. Reads client credentials from a context-referenced file and requests a token from Azure AD's token endpoint.

**Credit Memo Line**
: An individual line item on a credit memo specifying the product, quantity, price, and credit amount for a specific return or adjustment

**CurrencyIsoCode**
: Standard ISO currency code (e.g., USD, EUR) indicating the local currency in which the budget amounts are denominated

**d_Customer**
: The customer dimension table in the NPL MSSQL database containing master data for all veterinary customers including contact details, sales territory, and representative assignments

**d_Entity**
: The entity dimension table representing business units or legal entities within the NPL organizational structure, including currency and database configuration

**DatedConversionRate**
: A standard Salesforce object that stores historical exchange rates with date ranges, enabling period-accurate multi-currency conversion

**DEBUG variant**
: A job copy with added tLogRow components that print intermediate data to the console/log during execution. Used for troubleshooting data flow issues but should not be scheduled in production.

**DEL_Clean_Sales_Set_Delete_Ingest**
: A job that manages the deletion of stale Sales_Set records for South Africa, preparing the table for fresh data ingestion.

**Delta Processing**
: An incremental data loading strategy where only changed records are processed. New/modified records are staged in a Delta table, matching records are deleted from the main table, and delta records are inserted. More efficient than full table replacement for large datasets.

**Delta tables (salesInvoicesDelta, salesCreditMemosDelta)**
: Tables containing only the records that have changed since the last synchronization, enabling incremental processing rather than full reloads.

**DEU_VDD_RAW**
: The raw extraction orchestrator for Germany's legacy VDD entity. Pulls data from on-premises MSSQL into BigQuery raw layer.

**Dimension Set Entry**
: A Business Central record that links a transaction to a specific set of analytical dimensions (e.g., cost center, department), enabling multi-dimensional financial reporting

**Dimensions (Business Central)**
: Analytical categorizations in Microsoft Dynamics 365 Business Central (e.g., cost centers, departments, projects) used to tag financial transactions for multi-dimensional reporting.

**DimensionSetEntries**
: Records that link specific transactions to dimension values in Business Central, enabling filtered reporting by department, cost center, etc.

**Dual-entity harmonization**
: The process of combining data from two different ERP sources (VDA/Business Central and VDD/legacy) into a single consistent clean-layer schema.

**Entity-level cleaning**
: The pattern of cleaning and transforming one business entity at a time (e.g., items, invoices, ledger entries) before combining them into aggregate tables. Promotes modularity and independent testing.

**Entity__c**
: Budget field identifying the Vaxxinova business entity (subsidiary/legal entity) responsible for the budget line

**Entry_Number**
: The unique identifier for item ledger entries and value entries in Business Central. Used as the key for delta processing -- matching Entry_Numbers in the delta table trigger deletion and replacement in the main table.

**Env_Prefix**
: A context variable used to prefix BigQuery dataset names, enabling environment isolation (e.g., 'PROD_' vs 'TEST_')

**ERP migration**
: The transition from legacy ERP to Business Central. During migration, both systems may be active, requiring parallel data pipelines.

**External Sort**
: A sorting strategy that uses disk-based temporary files when data exceeds the configured memory buffer, preventing out-of-memory errors

**External_Id__c**
: A Salesforce custom field used as a unique identifier for upsert operations. Set to 'BIO-' + Product_ID to enable idempotent product synchronization without creating duplicates.

**EXTERNAL_SORT_BUFFERSIZE**
: Configuration parameter (set to 50,000) controlling how many rows are held in memory before spilling to disk during sort operations

**faturaRegistradaVenda**
: Portuguese for 'registered sales invoice'. A Business Central table containing Brazil-specific sales fiscal documents that comply with Nota Fiscal requirements. Extracted for tax reporting and audit purposes.

**Fiscal Period**
: Vaxxinova's custom fiscal calendar periods (likely a July-June fiscal year split into 12 periods), used for period-aligned reporting

**Fiscal Year 2023/2024**
: Vaxxinova's fiscal year running approximately July 2023 to June 2024, used as the baseline for the subsequent budget cycle

**Fiscal_Period__c**
: Budget field identifying the fiscal period number within the fiscal year for the budget target

**Fiscal_Periods**
: A global reference table (Global_Raw.Fiscal_Periods or Global_Master.Fiscal_Periods) that maps calendar dates to Vaxxinova's fiscal year periods, enabling standardized financial reporting across subsidiaries.

**fiscalDocumentTaxEntryFNX**
: Business Central table for Brazilian fiscal document tax entries. Contains detailed tax line items (ICMS, IPI, PIS, COFINS rates and amounts) for each fiscal document, required for Brazilian tax compliance and reporting.

**GEN_BC (Generic Business Central)**
: The job family prefix for shared, parameterized Business Central extraction jobs that can be used by any region with BC as their ERP.

**General Ledger Entry**
: The fundamental accounting record in Business Central. Every financial transaction (sales, purchases, production costs, payments) creates one or more G/L entries. Extracted for financial reporting and reconciliation.

**generalLedgerEntry**
: A Business Central entity representing individual financial transaction postings. Each entry has an AccountId, PostingDate, Amount, and other attributes. The core data for GL-based sales analysis.

**glAccount**
: The chart of accounts table from Business Central. Maps SystemId to account Number, enabling categorization of GL entries by account type.

**Global_Budget**
: BigQuery dataset containing budget and forecast master data, conversion rates, and related planning data

**Global_Clean**
: BigQuery dataset containing cleansed, standardized data from all regions ready for consolidation

**Global_Master**
: BigQuery dataset containing enterprise-wide master/reference data including Product_Master and Country_Master

**Gross Margin**
: Revenue minus COGS, expressed as a percentage or absolute amount -- the primary profitability metric for vaccine products

**HTTP Chunked Transfer**
: A connection setting (httpChunked=true) that enables streaming data transfer, useful for large Salesforce query results

**ICMS / PIS / COFINS / IPI**
: Brazilian tax types extracted in the VW_Returns view. ICMS is the state-level value-added tax, PIS and COFINS are federal social contribution taxes, and IPI is the federal excise tax on manufactured goods. Critical for accurate revenue and returns reporting in Brazil.

**Ingest (vs Build)**
: A pipeline variant focused on incremental data ingestion into the Sales_Set, as opposed to a full rebuild. Both ultimately populate the same target table.

**Inventory Value Item**
: A cleaned entity representing the financial value of inventory items, derived from value entries and item ledger entries in Business Central.

**Invoice Ledger Item**
: A record that links an invoice line item to its corresponding general ledger posting, enabling reconciliation between sales documents and financial entries.

**Invoice Set**
: A standardized assembly of invoice headers, lines, and credit memos into a unified dataset that feeds the downstream sales-set aggregation pipeline.

*... and 128 additional terms.*

## Frequently Asked Questions

### 1. Why are there two different sales pipeline jobs (BIO_Clean_Invoice_Set_v5 and Copy_of_VBR_Clean_Build_Sales_Set)?

They serve different source systems. BIO_Clean_Invoice_Set_v5 processes data from the legacy Biovet MSSQL database (accessed via VW_Sales and VW_Returns views), while Copy_of_VBR_Clean_Build_Sales_Set processes data from Microsoft Dynamics 365 Business Central (salesInvoices, salesCreditMemos). Both output to a Sales_Set table in BigQuery, suggesting a migration from legacy ERP to Business Central is underway.

### 2. What does the BRA_VBR_RAW job extract and where does the data go?

BRA_VBR_RAW connects to the on-premises MSSQL server using context-parameterized schema (connection_BRA_VBR_MSSQL_Schema) and extracts three views: VW_Finished_Product (product master), VW_Sales (invoice/sales data with 27+ columns), and VW_Returns (returns with Brazilian tax fields like ICMS, PIS, COFINS, IPI). Each is loaded via tMap into BigQuery tables in the raw layer using TRUNCATE mode.

### 3. How does the Salesforce product sync work?

BIO_Sync_Product_Salesforce_v1 reads VW_Finished_Product from BigQuery, compares with existing Salesforce Product2 records, then performs updates for matching records and upserts (via External_Id__c = 'BIO-' + Product_ID) for new ones. It maps vaccine-specific fields like Species, Subspecies, Hierarchy tiers, Product Family, and Doses into corresponding Salesforce custom fields.

### 4. What are the tHash and tFixedFlowInput components used for in Copy_of_VBR_Clean_Build_Sales_Set?

The tFixedFlowInput/tJava/tHashOutput pattern is used to create in-memory lookup tables at the start of the job. Each tFixedFlowInput initializes a data structure, tJava populates it with derived reference data, and tHashOutput stores it in memory. Later, tHashInput retrieves these lookups for use in tMap joins. This avoids repeated BigQuery reads for reference data like fiscal period mappings, currency conversions, and territory assignments.

### 5. Why is Sales_Set written with TRUNCATE mode instead of incremental loading?

The TRUNCATE mode performs a full table replacement on each execution, which is simpler but less efficient. This approach ensures data consistency by avoiding duplicate or stale records, but means every run reprocesses all sales data. For a dataset of this complexity (joining invoices, credit memos, fiscal periods, payment terms, conversion rates, and country mappings), full refresh avoids the complexity of change detection logic.

### 6. What is the 'Produtct_Description' field -- is that a typo?

Yes, 'Produtct_Description' is a misspelling of 'Product_Description' that originates in the MSSQL source views. It is propagated unchanged through BRA_VBR_RAW into BigQuery and then used in BIO_Sync_Product_Salesforce_v1 when mapping to Salesforce. Correcting it would require coordinating changes across the source database, all ETL jobs, and downstream consumers.

### 7. What is the difference between VBR_Clean_Build_Sales_Set and VBR_Clean_Ingest_Sales_Set?

Build jobs transform raw Business Central data (invoices, credit memos, payment terms, etc.) into the consolidated Sales_Set structure with enrichments like fiscal periods and currency conversions. Ingest jobs then take that Sales_Set and load it into the global DWH reporting layer, performing a delete-and-replace operation to ensure the global tables reflect the latest data.

### 8. Why are there backup versions of these jobs?

The backup jobs (VBR_Clean_Build_Sales_Set_Backup, VBR_Clean_Ingest_Sales_Set_Backup) preserve a known-working version of the pipeline while developers iterate on the primary jobs. This is a common Talend practice when version control is not used extensively -- the backup serves as a rollback point. They should ideally be deprecated once the primary version is stable.

### 9. How does the incremental loading work?

The newer VBR_Clean_Build_Sales_Set job filters source data using: 'WHERE lastModifiedDateTime >= parameter_date'. The parameter_date is loaded from a globalMap variable, which is typically set by an upstream orchestration job or context parameter. This means only recently modified invoices and credit memos are processed, but all reference data (fiscal periods, payment terms, etc.) is still fully loaded.

### 10. What happens if the Ingest job fails between the DELETE and INSERT steps?

The global Sales_Set table would have records deleted but not replaced, creating a data gap. Since there is no error handling or transaction wrapping, the table remains in this incomplete state until the job is manually re-run or the next scheduled execution. This is a significant operational risk for downstream reporting.

### 11. What reference data does the Sales_Set depend on?

The Sales_Set requires: (1) Global_Master.Fiscal_Periods for fiscal year mapping, (2) paymentTerms from VBR raw for payment conditions, (3) shipmentMethods from VBR raw for delivery information, (4) Global_Budget.Budget_Current_Conversion_Rate for BRL-to-EUR/USD conversion, (5) Global_Master.Country_Master for country standardization, and (6) Global_Raw.Salesforce_Account_Mappings for CRM account linking.

### 12. Why are there so many backup and debug versions of the Ingest job?

This pattern typically emerges when developers encounter production issues and create copies to experiment with fixes without disrupting the running pipeline. Each backup represents a snapshot at a different stage of troubleshooting. The DEBUG variants add tLogRow components for visibility into data flow. Ideally, once the primary job is stabilized, all backup/debug variants should be archived or deleted.

### 13. What is the difference between VBR_Clean_SalesInvoices and the Sales_Set Build job?

VBR_Clean_SalesInvoices and VBR_Clean_SalesInvoiceLines clean and transform individual Business Central entities (invoice headers and line items) separately. The Sales_Set Build job (in BRA_1) combines these with credit memos and reference data into the final reporting structure. The separated approach is more modular and easier to debug, suggesting an evolution in pipeline architecture.

### 14. Is it safe to delete the backup jobs?

Before deleting, verify: (1) the primary job in BRA_1 is functioning correctly and covers all logic from the backups, (2) no orchestration job references the backup versions by name, (3) the backups do not contain unique transformation logic not present in the primary version. Once verified, they should be removed to reduce confusion and maintenance overhead.

### 15. What does VBR_Clean_Ingest_Sales_Set_Backup_2 read from salesInvoiceHeaders -- is that different from salesInvoices?

Yes, salesInvoiceHeaders and salesInvoices are different Business Central API endpoints/tables. salesInvoiceHeaders is a more detailed header view that may include additional fields. The fact that Backup_2 uses a different source table than other variants suggests it was testing an alternative data source, possibly to resolve data completeness issues.

### 16. Why does VBR_Clean_Production_Order_COPY appear in a sales-focused chunk?

Chunk assignment is based on file size grouping, not strict functional boundaries. VBR_Clean_Production_Order_COPY processes production order data (manufacturing) but was grouped here due to chunking logistics. Its COPY suffix indicates it was cloned from the primary version in BRA_3 for testing or modification.

### 17. What production data does this chunk process?

This chunk processes vaccine manufacturing data including: production orders (batch records for vaccine production), production order lines (materials and outputs per batch), item ledger entries (inventory movements), value entries (cost allocations), routing lines (manufacturing steps/operations), and generates cost breakdown reports that summarize production efficiency and cost allocation.

### 18. What is the delta processing pattern used in Production_Order_Line_Absorption?

The job writes transformed data to a Production_Order_Line_Delta staging table (with TRUNCATE mode). Then, a tBigQuerySQLRow executes a DELETE statement that removes records from the main Production_Order_Line table where the Entry_Number matches any Entry_Number in the Delta table. Finally, the delta records are inserted into the main table. This achieves incremental updates without reprocessing the entire dataset.

### 19. Why does the itemLedgerEntries filter use both 'Saida' and 'Output'?

Business Central in Brazil stores some data in Portuguese. 'Saida' is Portuguese for 'Output'. The dual-language filter ensures the query captures production output records regardless of the language setting in the ERP system. This is common in multinational deployments where the same Business Central codeunit may return data in the local language.

### 20. What are the cost breakdown report jobs for?

VBR_Cost_Breakdown_Report_* jobs analyze production costs by aggregating value entries (direct materials, labor, overhead) at the production order level. They join production orders with their associated value entries and item ledger entries to calculate total production cost, cost per dose, material vs. overhead ratios, and other KPIs used for manufacturing management and pricing decisions.

### 21. Why are there hardcoded dataset references instead of context parameters?

Jobs like VBR_Clean_Production_Order_Line_Absorption use 'test_bra_vbr_clean' and 'test_bra_vbr_raw' directly in SQL queries instead of context.parameter_Env_Prefix. This means these jobs are currently locked to the test environment and cannot be deployed to production without code changes. This is likely because these jobs are still in development (indicated by their Dev folder path).

### 22. What is the relationship between Production_Order, Production_Order_Line, and Value_Entry?

Production_Order is the header (one per vaccine batch). Production_Order_Line contains the materials consumed and outputs produced per order. Value_Entry records the financial value of each inventory movement (consumption cost, output value, variance). Together they enable full cost accounting: what was produced, what materials were used, and what each cost.

### 23. What is the Cost Breakdown Report (CBR) pipeline?

The CBR pipeline calculates the true cost of manufacturing each vaccine batch. It decomposes costs into: (1) Absorption Lines -- overhead costs allocated through routing operations (labor, equipment, facility), (2) Consumption Lines -- direct material costs (raw materials, packaging consumed), (3) Routing Lines -- manufacturing operation costs per step, and (4) Production Order Summaries -- rolled-up totals per production order. The output is used for product profitability analysis and pricing decisions.

### 24. What does the VBR_Update_* pattern do differently from VBR_Clean_* jobs?

VBR_Clean_* jobs read from raw, transform, and write to clean tables (forward pipeline). VBR_Update_* jobs read from clean tables, compute derived metrics (like total cost, cost allocation percentages), and write those metrics BACK into the production order records. This is a write-back pattern that enriches existing records with calculated values rather than creating new output tables.

### 25. Why are there so many variants of VBR_Update_CBR_ProductionOrders?

This job performs the most complex operation in the CBR pipeline -- it updates production orders with computed cost totals from all cost categories. The multiple variants (primary, BACKUP, DEBUG, DEBUG_2) suggest this logic has been difficult to get right, with developers creating copies to debug specific cost calculation issues. The BACKUP variants preserve working versions during experimentation.

### 26. What is the difference between Pending and Finished production order processing?

Finished production orders have final, actual costs that reflect what was really consumed and produced. Pending orders have estimated costs based on planned consumption and standard rates. VBR_Update_Pending_Production_Order_Line specifically handles the pending case, providing management with cost projections for orders still in progress.

### 27. Why do some jobs export to CSV files?

The 6 tFileOutputDelimited components export cost breakdown data to CSV files, likely for: (1) sharing with finance/accounting teams who work in Excel, (2) feeding into external reporting tools that do not connect to BigQuery directly, or (3) creating audit trails of cost calculations. The files are written to a temp folder and may be emailed or uploaded to a shared drive by a post-processing step.

### 28. What is the tFilterRow with 'advanced condition' in VBR_Cost_Breakdown_Report_Test_2?

The tFilterRow uses a custom Java condition to filter records. The default template ('input_row.columnName1.equals(input_row.columnName2)') suggests the filter may not have been fully customized, or it performs a self-join validation check. This should be reviewed to confirm the filter logic is correct for production use.

### 29. How does the Business Central data extraction work?

Each job calls a custom SOAP codeunit (BI_Service) on Business Central's API endpoint. The call specifies a table number (piTableNo), field filter (piFieldFilterListJson with date-based filtering), and batch size (piMaxNoOfRecords). The response is a SOAP XML envelope containing a JSON data payload. The job parses the XML to extract the JSON, then parses the JSON to extract individual records. Pagination is handled by a loop: if poEndOfRecords is false, the job calls the API again with the returned pioRecPosition to fetch the next batch.

### 30. What is the BI_Service codeunit and why is it used instead of standard OData APIs?

BI_Service is a custom codeunit developed within Business Central specifically for BI data extraction. It provides: (1) server-side pagination for large result sets, (2) field-level filtering via JSON filter definitions, (3) table-number-based access that can extract from any BC table without pre-configured API pages, and (4) record count reporting. This is more flexible than standard OData APIs, which require page-by-page configuration and have row limits.

### 31. What are faturaRegistradaVenda and fiscalDocumentTaxEntryFNX?

These are Brazilian-specific Business Central tables. 'faturaRegistradaVenda' contains registered sales invoices (Nota Fiscal de Venda) -- the official tax documents required by Brazilian tax authorities for every sale. 'fiscalDocumentTaxEntryFNX' contains the detailed tax line items for each fiscal document, including ICMS, IPI, PIS, and COFINS tax calculations. These are essential for Brazilian tax compliance and are not available in standard Business Central APIs.

### 32. How are credentials managed for the Business Central API?

Credentials are loaded from external files during the pre-job phase: (1) BC_Secret_Key_Context contains the API authentication secret key, loaded via tFileInputDelimited, and (2) BC_environment contains environment-specific configuration (tenant ID, environment name, company name). These files are stored on the Talend execution server at paths defined by context variables (resource_file_BC_Secret_Key_Context, resource_file_BC_environment).

### 33. How does the pagination loop work?

A tJavaRow component checks the poEndOfRecords boolean from the API response. If false, it extracts the pioRecPosition (bookmark) and passes it back to the tREST call for the next batch. If true (or if an exception occurs), it sets pioRecPosition to null, terminating the loop. The try-catch in the tJavaRow means API errors are silently caught and treated as end-of-records, which could mask data extraction failures.

### 34. What does VBR_Raw_generalLedgerEntries extract?

This job extracts general ledger (G/L) entries from Business Central -- the fundamental accounting records that capture every financial transaction (sales, purchases, payments, journal entries). In the context of Vaxxinova Brazil, G/L entries are used for financial reporting, cost analysis, and reconciliation between the ERP and data warehouse.

### 35. What is the difference between VBR_Raw_item and VBR_Raw_Items?

These likely extract from different Business Central tables or API endpoints. VBR_Raw_item (singular, lowercase) may extract from the BC 'item' API page (standard item card data), while VBR_Raw_Items (plural, capitalized) may extract from a different table number via BI_Service. Review the piTableNo parameter in each job's tREST configuration to confirm which BC table each targets. One may be deprecated in favor of the other.

### 36. Why is there an 'Old' version of salesCreditMemos?

VBR_Raw_salesCreditMemos_Old likely represents an earlier extraction approach that was replaced when Business Central's credit memo data model changed or when the team adopted separate header/line extraction (salesCreditMemoHeaders + salesCreditMemoLines). The old version may extract a denormalized view while the new versions follow a normalized header-line pattern consistent with the invoice extraction approach.

### 37. What are itemApplicationEntries used for?

Item application entries in Business Central track the relationship between inventory movements. For example, they link a production output (finished vaccine added to inventory) to a subsequent sales shipment (vaccine leaving inventory). This chain enables cost tracing: you can follow the cost from raw material purchase through production to eventual sale. The production cost analytics in BRA_3/BRA_4 use these entries for accurate cost allocation.

### 38. Why extract paymentTerms via the full SOAP pipeline?

While paymentTerms is a small reference table, it uses the same extraction framework as all other BC entities for consistency. The overhead of the SOAP/XML/JSON pipeline is negligible for small tables. This approach ensures paymentTerms data stays synchronized with the same extraction schedule as transactional data, avoiding stale reference data.

### 39. How do these raw jobs relate to downstream processing?

The dependency chain is: (1) VBR_Raw_productionOrders + VBR_Raw_itemLedgerEntries + VBR_Raw_itemApplicationEntries feed production order processing in BRA_3, (2) Value entries from a related raw job feed the CBR pipeline in BRA_4, (3) VBR_Raw_salesCreditMemoHeaders/Lines feed the Sales_Set build in BRA_1, (4) VBR_Raw_paymentTerms provides reference data for invoice enrichment in BRA_1, (5) VBR_Raw_item/Items provides product master data for various clean-layer jobs.

### 40. What does Create_Business_Central_Token_v2 do?

This custom Talend component handles OAuth2 token generation for the Business Central API. It uses the client credentials loaded from the BC_Secret_Key_Context file to request an access token from Azure Active Directory. The token is then used by the tREST component for authenticating SOAP API calls. The 'v2' suffix suggests this is an updated version, possibly migrated from basic authentication to OAuth2.

*... and 125 additional questions.*
