# Business Logic

This document catalogs the business rules embedded in Vaxxinova's Talend ETL pipelines. These rules govern how sales data is transformed, validated, and enriched as it flows from regional ERP systems through to BigQuery and Salesforce. Understanding these rules is critical for migration planning -- each rule must be faithfully replicated in any target platform.

## Budgeting & Forecasting

*2 business rules identified across the platform.*

### South Africa (ZAF)

**BR-ZAF-010**: The clean sales set includes parallel columns for multiple planning scenarios: actuals (Quantity, Net_Sales_Amount), budget (_Budget suffix), prior year (_Prior suffix), order (_Order suffix), and forecast (_Forecast suffix). Each scenario has both local currency and EUR-converted variants.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Missing budget/forecast/prior-year columns would prevent variance analysis (actual vs. budget, YoY comparison), which is critical for management reporting and sales target tracking.

**BR-ZAF-011**: Budget and forecast EUR amounts are converted using the same conversion rate as actuals, ensuring consistent FX treatment. The formula pattern is: amount_EUR = amount_local / ConversionRate, with null-safe checks on each amount field.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Inconsistent FX conversion between actuals and budget would show artificial variances driven by rate differences rather than genuine business performance gaps.

## Currency & FX

*4 business rules identified across the platform.*

### Germany (DEU)

**BR-DEU-004**: When the Currency_Code from Business Central is empty or null, the system defaults to 'EUR' as the transaction currency, since VDA operates in Germany where EUR is the functional currency.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Invoices in the home currency would have no currency code assigned, breaking FX conversion logic and causing null values in currency reporting fields.

**BR-DEU-005**: The conversion rate defaults to 1 when the Currency_Code is empty (domestic EUR transactions). For foreign currency invoices, the Currency_Factor from the invoice header is used as the conversion rate.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Domestic transactions would have null or zero conversion rates, causing division-by-zero errors when computing EUR-equivalent amounts for group reporting.

### South Africa (ZAF)

**BR-ZAF-005**: All local-currency amounts (Sales_Price, Discount_Amount, Net_Sales_Amount, COGS) are converted to Euro by dividing by the ConversionRate from Global_Clean.Budget_Current_Conversion_Rate. Null amounts are handled with null-safe checks to prevent division errors.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: ZAR-to-EUR conversion failure would make South African figures incomparable with other subsidiaries in the group-level consolidated reports. Null handling failure would propagate NaN values.

**BR-ZAF-006**: The budget conversion rate from Global_Clean.Budget_Current_Conversion_Rate is used for both actual and budget amount conversions, ensuring that FX variances are isolated from operational variances when comparing actual vs. budget figures.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Using different rates for actuals vs. budget would introduce FX-driven variances that obscure genuine operational performance differences between actual and planned results.

## Customer Management

*62 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-008**: Invoice country is resolved using a fallback pattern: if a Salesforce country mapping (Country_Code__c) exists, it takes precedence; otherwise the raw source Invoice_Country value is used, ensuring standardized country codes across the organization
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Inconsistent country codes would break territory-based sales reporting and Salesforce integration

**BR-BRA-009**: Country values from the raw source are trimmed of whitespace using TalendString.talendTrim before being used, preventing data quality issues from leading/trailing spaces in the DS_Country field
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Country lookups and joins would fail due to whitespace mismatches, causing missing territory assignments

**BR-BRA-009**: Null or empty country fields for billing, selling, and shipping addresses default to 'BR' (Brazil), assuming domestic transactions when country is not specified in the source ERP
  - *Implemented in*: VBR_Clean_Build_Sales_Set
  - *Impact if wrong*: Null country values would propagate to reports, causing territory assignment failures and gaps in geographic sales analysis

*... and 5 additional rules in this region.*

### Germany (DEU)

**BR-DEU-009**: The Invoice_Country_Original from the billing address is enriched via a lookup against Global_Raw.Salesforce_Country_Mappings to derive a standardized Invoice_Country. If the mapping is missing or null, the original country code is retained.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Customer geographic segmentation and territory-based reporting would use inconsistent country codes, making regional sales analysis unreliable.

**BR-DEU-010**: For VDA sales invoices, when the Bill_to_Country_Region_Code is null or blank, the system defaults the country to 'DE' (Germany), since VDA's primary market is domestic.
  - *Implemented in*: VDA_BC_Clean_SalesInvoices
  - *Impact if wrong*: Domestic German customers without explicit country codes would show as null in geographic reports, understating the German market share.

### Generic / Shared

**BR-GENERIC-012**: Credit memo customer data includes both sell-to (order customer) and bill-to (invoice customer) with separate address hierarchies: addressLine1, addressLine2, city, country, state, postCode for each. This supports complex B2B scenarios where the ordering party differs from the paying party.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Confusing sell-to and bill-to customers would misattribute geographic sales data and break customer-level receivables reporting.

### Global

**BR-Global-005**: Salesforce Account Mappings are joined to link local entity account IDs to global Salesforce Account IDs, enabling cross-entity customer identification for budget records.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Budget records would lack Salesforce account links, breaking CRM-based customer analytics

**BR-Global-009**: Invoice, order, and delivery addresses are all sourced from Salesforce Account Mapping billing address, since budget records have no physical shipment.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Address-based analytics would show null values for budget records

**BR-Global-018**: Account_Salesforce_Id links each budget line to the Salesforce CRM account, enabling sales team performance tracking against targets.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Budget targets cannot be compared to CRM pipeline data by account

*... and 11 additional rules in this region.*

### Italy (ITA)

**BR-ITA-005**: Italian customer data includes both invoice customer (CLI_*) and delivery destination customer (CLI_DEST_*) with separate address hierarchies. The DEST_CLI field links invoice lines to specific delivery destinations, supporting Italy's multi-destination delivery model for veterinary vaccines.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Bill-to and ship-to customer confusion would misattribute geographic sales analysis and delivery logistics reporting for the Italian market.

**BR-ITA-009**: Customer categorization in Italy uses CLI_CAT_CODICE and CLI_CAT_DESCRIZIONE for customer category classification, and FATT_IT_EXT to distinguish domestic Italian vs. export transactions. NAZI_CODICE/NAZI_DESCRIZIONE track the customer's nationality.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Customer segmentation for marketing and sales strategy would be lost. Domestic vs. export revenue split -- critical for VAT compliance -- would be unavailable.

### Japan (JPN)

**BR-JPN-009**: Invoice account ID is constructed as VJP- prefix plus BilltoCustomerNumber from BC, linking to the local customer master.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Customer attribution for Japan invoices would fail

**BR-JPN-010**: Ship-to and sell-to addresses are captured separately from BC invoice headers, with country defaulting to JP when CountryRegionCode is null or empty.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Delivery addresses would be null for domestic Japan shipments

**BR-JPN-018**: Order_Id handles the BC null UUID pattern (00000000-0000-0000-0000-000000000000), converting it to null to avoid storing meaningless order references.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Null UUIDs would appear as valid order IDs, causing confusion in order tracking

*... and 4 additional rules in this region.*

### South Africa (SAF)

**BR-SAF-005**: South African customer geography is tracked through the same Italian-origin fields: NAZI_CODICE/NAZI_DESCRIZIONE (nationality), CLI_DEST_NAZIONE (destination country), and ZONA_CODICE (zone), requiring adaptation for South African provinces and African export markets.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Geographic segmentation would use Italian field naming conventions with South African data, potentially causing confusion in territory reporting if zone codes differ between the two markets.

**BR-SAF-015**: The FATT_IT_EXT (domestic vs. export) flag and customer address fields (CLI_DEST_VIA, CLI_DEST_CAP, CLI_DEST_LOCALITA, CLI_DEST_PROV) are extracted using the Italian field naming convention, requiring business users to understand that these map to South African address concepts.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Address field interpretation would be ambiguous -- Italian province codes (PROV) mapped to South African provinces would create confusion in customer geographic analysis.

### United States (USA)

**BR-USA-001**: Newport Labs (NPL) customer accounts are synchronized to Salesforce as Account_Mapping__c custom objects using UPSERT with Local_Id__c as the external key, ensuring bidirectional CRM-ERP linkage
  - *Implemented in*: NPL_Clean_Account_v4_Salesforce
  - *Impact if wrong*: Salesforce CRM would lose synchronization with NPL customer data, breaking customer 360 views and sales team workflows

**BR-USA-002**: Customer billing addresses (country, state, city, street, zipcode) are mapped to Salesforce custom fields (Local_Billing_Country__c, etc.) to maintain address consistency between ERP and CRM
  - *Implemented in*: NPL_Clean_Account_v4_Salesforce
  - *Impact if wrong*: Customer addresses in Salesforce would diverge from ERP, causing confusion for sales reps and incorrect shipping

**BR-USA-003**: Territory is hardcoded to 'North America' for all NPL customers, reflecting that Newport Labs operates exclusively in the North American market
  - *Implemented in*: NPL_Clean_Account_v4_Salesforce
  - *Impact if wrong*: Territory-based reporting and sales team assignments would be incorrect for NPL customers

*... and 21 additional rules in this region.*

### South Africa (ZAF)

**BR-ZAF-008**: South African country mapping includes specific hardcoded overrides: 'LUSAKA' maps to 'ZM' (Zambia) and 'ZIMBABWE' maps to 'ZW'. This corrects non-standard country entries in the Deltamune source system to ISO country codes.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: African export markets would have inconsistent country codes, preventing accurate geographic revenue analysis across Zambia, Zimbabwe, and other Southern African markets.

**BR-ZAF-016**: The ZAF clean sales set captures full billing and shipping address hierarchies: street, city, zipcode, county, state, and country for both invoice (Bill_To_*) and delivery (Ship_To_*) customers, supporting detailed geographic analysis of the South African market.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Geographic customer analysis would be limited to country-level only, preventing city/state-level market penetration analysis within South Africa and its export markets.

## Data Integration

*40 business rules identified across the platform.*

### Global

**BR-Global-008**: Output is written to Global_Budget.Sales_Set_Budget with TRUNCATE action, fully replacing data each run for idempotent processing.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Duplicate or stale budget records would accumulate if append mode were used

**BR-Global-011**: Budget_Ingest_Sales_Set reads from entity-specific Sales_Set_Budget and maps all fields into the global Sales_Set schema for consolidation.
  - *Implemented in*: Budget_Ingest_Sales_Set
  - *Impact if wrong*: Budget data would not appear in the global Sales_Set, breaking consolidated reporting

**BR-Global-013**: Post-job cleanup removes temporary CSV staging files after BigQuery load, preventing storage accumulation.
  - *Implemented in*: Budget_Build_Sales_Set, Budget_Ingest_Sales_Set
  - *Impact if wrong*: Temporary files accumulate on disk, eventually causing storage issues

*... and 20 additional rules in this region.*

### Japan (JPN)

**BR-JPN-011**: Before loading new data, existing VJP sales records are deleted from both entity-specific and global Sales_Set tables using a CTE-based DELETE to prevent duplicates.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Duplicate records would accumulate with each run, inflating Japan sales figures

**BR-JPN-014**: Fiscal periods, payment terms, shipment methods, conversion rates, country master, and account mappings are all joined from shared Global reference tables.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Japan data would lack fiscal calendar alignment, payment terms, and currency conversion context

**BR-JPN-016**: VJP_Clean_Build_Sales_Set_Backup is a safety copy of the main build job, providing a rollback option if the primary job logic changes cause issues.
  - *Implemented in*: VJP_Clean_Build_Sales_Set_Backup
  - *Impact if wrong*: No rollback option if the primary build job introduces errors

*... and 14 additional rules in this region.*

## Data Integration/ETL

*82 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-012**: The final Sales Set output is written to BigQuery with TRUNCATE action, completely replacing the previous dataset each run to ensure data consistency and prevent duplicate accumulation
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Without truncation, duplicate records would accumulate with each ETL run, inflating revenue figures

**BR-BRA-020**: The output CSV file naming convention uses environment prefix and company identifier (parameter_Env_Prefix + parameter_BC_Company_Identifier) to support environment separation (test vs production) for BioVet data
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Test and production data could be mixed, potentially corrupting production reporting

**BR-BRA-019**: The data lineage is tagged with Source = 'VBR_Sales_Set' and Last_Modified_Date, enabling audit trail tracking and incremental load debugging
  - *Implemented in*: VBR_Clean_Build_Sales_Set
  - *Impact if wrong*: Data lineage would be lost, making it impossible to trace data quality issues back to their source

*... and 26 additional rules in this region.*

### Germany (DEU)

**BR-DEU-018**: All DEU jobs follow the Vaxxinova standard ETL pattern: tPrejob for initialization, tPostjob triggering cleanup_temp_file_storage to remove CSV staging files, and Vaxxinova_Logging for centralized audit trail. Data flows through BigQuery via GCS bucket staging.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3, VDA_BC_Clean_Item_v1, VDA_BC_Clean_SalesInvoices, VDA_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Temporary staging files would accumulate on disk, job execution would not be logged for audit purposes, and the standardized pipeline pattern would break.

### Generic / Shared

**BR-GENERIC-001**: Generic Business Central (GEN_BC_*) jobs are parameterized template jobs that extract data from the BC REST API using context.parameter_BC_Company_Identifier. This allows a single job definition to be reused across multiple subsidiaries (VDA, VDD, etc.) by varying the company identifier at runtime.
  - *Implemented in*: GEN_BC_AccrualLedgerEntries, GEN_BC_Item, GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Without parameterized company identifiers, separate job definitions would be needed per subsidiary, multiplying maintenance effort and increasing the risk of inconsistencies between entity pipelines.

**BR-GENERIC-002**: All GEN_BC jobs follow the same REST API extraction pattern: tPrejob loads BC secret keys from a context file, Create_Business_Central_Token authenticates via OAuth, tRESTClient calls the BC API v2.0 endpoint, tExtractJSONFields parses the JSON response, and tBigQueryOutput loads to the warehouse.
  - *Implemented in*: GEN_BC_AccrualLedgerEntries, GEN_BC_Item, GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Breaking the authentication chain would prevent all BC data extraction. Token management failures would cause API 401 errors, halting raw data ingestion for all BC-connected subsidiaries.

**BR-GENERIC-003**: The BC REST API endpoint uses the path pattern '/v2.0/Production/api/LibertyGrove/financials/v2.0/companies({companyId})/{entity}', where 'LibertyGrove' indicates a custom API publisher and 'financials' is the API group. This is a non-standard BC API extension, not the default Microsoft API.
  - *Implemented in*: GEN_BC_AccrualLedgerEntries, GEN_BC_Item, GEN_BC_SalesCreditMemos
  - *Impact if wrong*: API path changes during BC upgrades or publisher changes would silently break all data extraction. Using the wrong API version would return incorrect or incomplete data schemas.

*... and 3 additional rules in this region.*

### Italy (ITA)

**BR-ITA-001**: The ITA_VIT_RAW job extracts the complete TABANALISI table from the VIT (Vaxxinova Italy) on-premise MSSQL database and loads it into BigQuery as a raw data ingestion step. This is a full-table extract (no WHERE clause), replacing the entire target table via TRUNCATE.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: If the extraction fails or is incomplete, all downstream Italy sales analysis will use stale data. The full-table TRUNCATE pattern means partial failures result in an empty table rather than stale data.

**BR-ITA-016**: The ITA pipeline connects to VIT's MSSQL server using context-parameterized connection settings (server, port, schema, database, login) and stages data through Google Cloud Storage before loading to BigQuery, following the Vaxxinova standard cloud ingestion pattern.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Connection parameter misconfiguration would prevent data extraction from the Italian source system, halting the entire ITA data pipeline.

### South Africa (SAF)

**BR-SAF-001**: The SAF group contains a copy of the ITA_VIT_RAW job (ITA_VIT_RAW_Copy), indicating this is a work-in-progress adaptation of the Italian TABANALISI extraction pattern for the South African subsidiary (VSA). The job still references ITA_VAX_MSSQL connection parameters, suggesting it is being cloned and modified for SAF use.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: If deployed without updating connection parameters, this job would extract Italian data instead of South African data, loading the wrong subsidiary's sales into the SAF dataset.

**BR-SAF-002**: The SAF extraction follows the same full-table TABANALISI extract pattern as Italy, using MSSQL as the source database and BigQuery as the target via GCS staging. The TRUNCATE action on data ensures complete table replacement on each run.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Partial data loads or append-mode ingestion would create duplicates. The TRUNCATE-and-reload pattern ensures data consistency but means any failure leaves an empty table.

**BR-SAF-009**: The SAF job uses the tMap component with DIE_ON_ERROR set to true, meaning any mapping failure (type conversion, null handling) will halt the entire job rather than silently dropping or corrupting records.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Without fail-fast behavior, corrupt or type-mismatched records would be silently loaded into BigQuery, creating data quality issues that are difficult to detect and debug.

*... and 2 additional rules in this region.*

### United States (USA)

**BR-USA-016**: The two-stage tMap pipeline first normalizes Animalytix fields to the standard Distributor_Sales_Set schema, then enriches with fiscal period data, separating concerns for maintainability
  - *Implemented in*: VUS_Clean_Animalytix
  - *Impact if wrong*: The transformation pipeline would be monolithic and harder to debug, increasing maintenance risk

**BR-USA-019**: The Animalytix source data comes from VUS_Raw.Animalytix table with a comprehensive column selection, indicating raw data has already been loaded from Animalytix data files into BigQuery
  - *Implemented in*: VUS_Clean_Animalytix
  - *Impact if wrong*: Missing columns in the source query would propagate as null values through the entire transformation pipeline

**BR-USA-009**: Customer data is read from the clean layer Customers table (parameter_Env_Prefix + '.Customers'), indicating a multi-stage ETL where raw data is first cleaned before being pushed to Salesforce
  - *Implemented in*: NPL_Clean_Account_v4_Salesforce
  - *Impact if wrong*: Uncleaned customer data would be pushed to Salesforce, potentially including duplicates or formatting issues

*... and 32 additional rules in this region.*

### South Africa (ZAF)

**BR-ZAF-018**: The ZAF group also contains a copy of ITA_VIT_RAW (ITA_VIT_RAW_Copy) using the TABANALISI extraction pattern, indicating a second South African data source being onboarded alongside the Deltamune-specific pipeline. Both use the Vaxxinova standard tPostjob/cleanup/logging pattern.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest, ITA_VIT_RAW_Copy
  - *Impact if wrong*: Running the unconfigured ITA copy job would extract Italian data into the ZAF dataset, contaminating South African sales reporting with Italian figures.

### Scratchpad / Development

**BR-scratchpad-003**: The BigQuery_TEST job tests two different BigQuery connectivity methods: the native tBigQueryInput component and a tJDBCInput component using the Google BigQuery JDBC driver. The JDBC path references a reusable connection (connection_JDBC_Google_BigQuery) for shared connectivity.
  - *Implemented in*: BigQuery_TEST
  - *Impact if wrong*: If either connectivity method fails, it indicates infrastructure problems (JDBC driver version, credentials, network) that would affect production jobs using the same approach.

**BR-scratchpad-004**: The BigQuery_TEST job uses hardcoded project ID ('vaxxinova-dwh') and local file paths (C:/CustomerWorkspace, D:/Talend/TS801_Vaxxinova), indicating it is a developer workstation test rather than a parameterized production job.
  - *Implemented in*: BigQuery_TEST
  - *Impact if wrong*: Running this job in production would fail due to missing local paths. It should never be deployed -- it exists only for local developer testing of BigQuery connectivity.

**BR-scratchpad-010**: Both scratchpad jobs (BigQuery_TEST and sandbox) lack the standard Vaxxinova production patterns: no tPostjob cleanup, no Vaxxinova_Logging, no parameterized context variables. This confirms they are development-only jobs that should not be promoted to production.
  - *Implemented in*: BigQuery_TEST, sandbox
  - *Impact if wrong*: If accidentally deployed to production, these jobs would not clean up temp files, would not produce audit logs, and would use hardcoded paths that do not exist on production servers.

## Data Quality

*6 business rules identified across the platform.*

### Generic / Shared

**BR-GENERIC-006**: The lastModifiedDateTime field is parsed with null-safe handling: null values are replaced with the sentinel '0001-01-01 00:00:00' before parsing via TalendDate.parseDate. The 'T' separator in ISO datetime strings is replaced with a space for format compatibility.
  - *Implemented in*: GEN_BC_AccrualLedgerEntries, GEN_BC_Item, GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Null datetime parsing would throw NullPointerExceptions, halting the entire job. Incorrect format handling would produce wrong timestamps, breaking incremental load logic that depends on modification dates.

**BR-GENERIC-010**: Credit memo dates (creditMemoDate, postingDate, dueDate) are parsed with null-safe handling: null values default to '0001-01-01', and the date string is truncated to 10 characters before parsing to strip time components from date-only fields.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Null date handling failure would crash the ETL job. Format inconsistencies (dates with/without time components) would cause parsing errors, blocking credit memo processing.

**BR-GENERIC-011**: Shipment dates on credit memo lines are validated against the 1970-01-01 epoch boundary: dates before 1970 are replaced with 1970-01-01. This is the same sentinel date handling pattern used across ITA, SAF, and ZAF pipelines.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Pre-epoch shipment dates would cause BigQuery ingestion failures or produce records with year-0001 dates that corrupt delivery date analytics.

### Italy (ITA)

**BR-ITA-003**: Delivery dates (DATA_CONSEGNA) that fall before 1970-01-01 are replaced with 1970-01-01 as a minimum date boundary. This guards against legacy system sentinel dates or corrupt date values that would break BigQuery date handling.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Pre-epoch dates would cause BigQuery ingestion failures or produce nonsensical delivery date analytics, potentially blocking the entire Italy data pipeline.

### South Africa (SAF)

**BR-SAF-003**: Delivery dates (DATA_CONSEGNA) prior to 1970-01-01 are clamped to the epoch boundary, identical to the ITA pattern. This prevents BigQuery ingestion failures from legacy or sentinel date values in the South African source system.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Pre-epoch dates would cause BigQuery type errors, potentially blocking the entire SAF data pipeline and leaving the warehouse without South African sales data.

### South Africa (ZAF)

**BR-ZAF-015**: All string fields from the Deltamune source system are trimmed using TalendString.talendTrim(field, ' ', 0) to remove leading and trailing spaces, ensuring consistent matching in joins and clean presentation in reports.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Untrimmed strings would cause join failures (e.g., 'DEL-123 ' != 'DEL-123'), creating orphan records in lookups and inconsistent grouping in aggregations.

## Data Transformation

*28 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-002**: The systemId GUID is cleaned by removing curly braces and trimming whitespace (replaceAll('[{}]', '').trim()), normalizing BC's GUID format for consistent use as a join key
  - *Implemented in*: VBR_Raw_salesInvoiceHeaders
  - *Impact if wrong*: GUID formatting inconsistencies would cause join failures when matching invoice headers with other tables

### Global

**BR-Global-006**: Territory classification is enriched from the Country_Master lookup, associating each budget record with its geographic sales territory.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Territory-based budget reporting would show blank or incorrect regions

**BR-Global-010**: Source field is hardcoded to Sales_Set_Budget to identify provenance in the unified Sales_Set, distinguishing budget from actual and forecast data.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Unable to filter or distinguish budget records from actuals in downstream reporting

**BR-Global-020**: Financial metric columns span actual, budget, forecast, prior, and order categories in a single wide-format record, supporting multi-dimensional variance analysis.
  - *Implemented in*: Budget_Build_Sales_Set, Budget_Ingest_Sales_Set
  - *Impact if wrong*: Variance analysis would require complex joins instead of simple column comparisons

*... and 9 additional rules in this region.*

### Japan (JPN)

**BR-JPN-002**: Record IDs are generated with VJP- prefix followed by the BC SystemId (with curly braces stripped), creating globally unique identifiers for Japan sales records.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Non-unique IDs could cause duplicate records or overwrite data from other entities

**BR-JPN-005**: Product ID is extracted from GL entry descriptions using regex: if the first 6 characters match two letters followed by four digits (e.g., IF9905), the code is prefixed with VJP-.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Product attribution from GL entries would fail, leaving many sales records without product linkage

**BR-JPN-012**: Source field is hardcoded to VJP_Sales_Set to identify Japan-originated records in the global Sales_Set.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Cannot filter or identify Japan sales records in the consolidated dataset

*... and 10 additional rules in this region.*

### United States (USA)

**BR-USA-003**: Customer-related fields (MemberId, Description, CustomerClassCode, CustomerNumber) are directly mapped from the MSSQL source to BigQuery, preserving the original NPL customer taxonomy
  - *Implemented in*: USA_NPL_RAW
  - *Impact if wrong*: Customer classification data would be lost, preventing customer segmentation analysis

**BR-USA-003**: SystemId is extracted from dimension set entries for unique record identification and potential change tracking across extraction runs
  - *Implemented in*: VUS_Raw_dimensionSetEntries
  - *Impact if wrong*: Record-level tracking would be lost, preventing deduplication and change detection

## DevOps & Monitoring

*3 business rules identified across the platform.*

### Scratchpad / Development

**BR-scratchpad-001**: The BigQuery_TEST job reads from Talend_Metadata.TFLOWMETERCATCHER, which stores Talend's built-in flow metering data (row counts, timestamps, job hierarchy). This is a diagnostic job for monitoring ETL pipeline execution metrics across all Vaxxinova Talend jobs.
  - *Implemented in*: BigQuery_TEST
  - *Impact if wrong*: This is a test/diagnostic job. If flow meter data is not accessible, ETL operations teams lose visibility into row counts and pipeline throughput for production jobs.

**BR-scratchpad-002**: The BigQuery_TEST job also writes to Talend_Metadata.TLOGCATCHER_TEMP (error/warning logs), using both APPEND and TRUNCATE modes for different output paths. This tests the ability to capture Talend log data in BigQuery for centralized monitoring.
  - *Implemented in*: BigQuery_TEST
  - *Impact if wrong*: As a test job, failure here indicates connectivity or schema issues with the Talend metadata tables in BigQuery that would also affect production monitoring.

**BR-scratchpad-007**: The sandbox job also includes a Talend log data path (moment, root_pid, father_pid, project, context, priority, origin, message), suggesting it was used to test both sales data extraction and log monitoring capabilities in a single development job.
  - *Implemented in*: sandbox
  - *Impact if wrong*: No production impact. The combined sales + logging paths in one job indicate this was a multi-purpose developer sandbox for testing various Talend patterns.

## Financial Accounting

*12 business rules identified across the platform.*

### Germany (DEU)

**BR-DEU-006**: Sales prices and net amounts in local currency are calculated by dividing the Business Central amounts by the conversion factor. If the currency code is empty (EUR), division is by 1 (no conversion). Null amounts default to 0.0.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Revenue figures would be incorrectly converted, showing inflated or deflated local-currency amounts. Null handling failure would propagate nulls through downstream aggregations.

### Generic / Shared

**BR-GENERIC-005**: GEN_BC_AccrualLedgerEntries extracts accrual accounting entries including entry type, plan type, source document references, posting dates, amounts, and price impact flags. These capture deferred revenue/expense recognition schedules for Vaxxinova's vaccine delivery contracts.
  - *Implemented in*: GEN_BC_AccrualLedgerEntries
  - *Impact if wrong*: Missing accrual ledger data would prevent accurate revenue recognition reporting, causing compliance issues with IFRS 15 or local GAAP requirements for multi-delivery contracts.

**BR-GENERIC-013**: Credit memo financial fields include totalAmountExcludingTax, totalTaxAmount, totalAmountIncludingTax, discountAmount, and discountAppliedBeforeTax flag at the header level, plus per-line netAmount, amountExcludingTax, discountAmount, invoiceDiscountAllocation, and taxPercent.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Incomplete financial fields would prevent accurate tax reporting, discount analysis, and net/gross revenue reconciliation for credit memos.

### Italy (ITA)

**BR-ITA-007**: Italian pricing includes a multi-level discount structure: SCO1_PERC, SCO2_PERC, SCO3_PERC (three cascading discount percentages), S001_SCONTO (discount 1 amount), S002_EXTRASCONTO (extra discount), with R_IM_NO_EXTRASCONTO tracking the amount before extra discount and R_IM tracking the final invoiced amount.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Discount waterfall analysis would be impossible. Net revenue calculations would be inaccurate, and pricing strategy analysis across discount tiers would fail.

**BR-ITA-008**: The CAMBIO field captures the exchange rate applied to Italian transactions, and PREZ_UNIT_LORDO holds the gross unit price. Combined with RLOR (gross revenue), these fields enable reconstruction of the complete pricing chain from list price to net amount.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Price-to-revenue reconciliation would be impossible, breaking margin analysis and audit trail from unit price to invoiced amount.

**BR-ITA-014**: The VCONT_* fields (VCONT_CLASS, VCONT_CONTO, VCONT_SOTTC, VCONT_CONTM) capture the Italian chart of accounts classification for each invoice line, enabling financial accounting integration beyond pure sales analysis.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: G/L account mapping for Italian statutory reporting would be unavailable, preventing reconciliation between the sales analysis table and the general ledger.

*... and 1 additional rules in this region.*

### South Africa (SAF)

**BR-SAF-006**: The multi-level Italian discount structure (SCO1_PERC, SCO2_PERC, SCO3_PERC, S001_SCONTO, S002_EXTRASCONTO) is preserved in the SAF extraction, indicating the South African subsidiary may use the same cascading discount model for veterinary vaccine pricing.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Discount waterfall calculations would be lost for the South African market, preventing accurate net revenue computation and pricing strategy analysis.

**BR-SAF-013**: The VCONT_* chart of accounts fields (VCONT_CLASS, VCONT_CONTO, VCONT_SOTTC, VCONT_CONTM) and ANTICIPO (advance payment) tracking are preserved, enabling financial accounting integration for the South African entity.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: G/L account mapping and advance payment tracking would be unavailable for South Africa, preventing financial reconciliation with the general ledger.

### South Africa (ZAF)

**BR-ZAF-004**: Net_Sales_Amount_Local_Currency is calculated as Shipped_Quantity * Unit_Price at the line level, deriving net sales from quantity and price rather than relying on a pre-computed source field. This ensures consistency between quantity, price, and amount.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Using a pre-computed amount field that is out of sync with quantity/price would create reconciliation discrepancies between line-level detail and summary amounts.

**BR-ZAF-013**: Cost of Goods Sold (COGS) is tracked alongside revenue at the line level in both local currency (Cost_Of_Goods_Sold_Amount_Local_Currency) and EUR, enabling gross margin calculation per invoice line for the South African subsidiary.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Gross margin analysis at the product and customer level would be impossible for Deltamune, preventing profitability insights for the South African market.

### Scratchpad / Development

**BR-scratchpad-009**: The sandbox exposes BC's VAT structure: VAT_Calculation_Type, VAT_Bus_Posting_Group, VAT_Prod_Posting_Group, VAT_Base_Amount, VAT_Difference, and VAT_Identifier. These are available for tax compliance reporting but are not currently extracted by production clean jobs.
  - *Implemented in*: sandbox
  - *Impact if wrong*: No production impact. If tax compliance reporting is needed, these fields are available in the BC source and could be added to the production GEN_BC or VDA clean jobs.

## Fiscal Calendar

*2 business rules identified across the platform.*

### Germany (DEU)

**BR-DEU-008**: Invoice posting dates are joined to the Global_Raw.Fiscal_Periods reference table to derive Fiscal_Period and Fiscal_Year values, mapping calendar dates to the Vaxxinova group fiscal calendar.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Sales would be attributed to incorrect fiscal periods, causing misalignment with budget targets and quarterly financial reports.

### South Africa (ZAF)

**BR-ZAF-007**: Invoice dates are joined to Global_Raw.Fiscal_Periods to derive Fiscal_Period and Fiscal_Year, and to Global_Raw.Salesforce_Countries to derive Territory. This aligns South African sales data with the Vaxxinova group fiscal calendar and Salesforce territory model.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Sales would be attributed to incorrect fiscal periods and territories, causing misalignment with group-level budget targets and territory performance metrics.

## Forecasting

*4 business rules identified across the platform.*

### Global

**BR-Global-001**: Budget and forecast records are filtered from the Budget_Master table using Category IN (Budget, Forecast), ensuring only planning data enters the budget Sales Set pipeline.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Actual sales records could contaminate budget/forecast datasets, leading to incorrect financial planning reports

**BR-Global-015**: Forecast quantities and amounts use the same conversion logic as budget but are tagged with Category=Forecast, supporting separate forecast-vs-budget variance reporting.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Forecast data missing or mislabeled, breaking forecast-vs-actual comparisons

**BR-Global-031**: Budgeting v5 reads actual sales from FY 2023/2024 as a baseline for building the next-year budget, combining actuals with Budget FY 2025/2026 and Forecast FY 2024/2025 records.
  - *Implemented in*: Global_Clean_Sales_Set_Budgeting_v5
  - *Impact if wrong*: Budget baseline would use wrong fiscal year actuals, producing incorrect projections

*... and 1 additional rules in this region.*

## Inventory

*11 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-001**: Item master data is extracted from Business Central via SOAP/BI_Service, capturing product catalog details including assembly policy, base unit of measure, costing method, description, dose, and family classification for VBR products
  - *Implemented in*: VBR_Raw_item
  - *Impact if wrong*: Product master data would be missing from the data warehouse, breaking all product-based reporting and analysis

**BR-BRA-002**: Item descriptions are trimmed of whitespace using .trim(), ensuring clean product names for reporting and preventing matching issues with leading/trailing spaces from BC
  - *Implemented in*: VBR_Raw_item
  - *Impact if wrong*: Product name lookups and joins would fail due to whitespace mismatches, causing orphaned records in joins

**BR-BRA-003**: The Dose field is parsed from string to integer (Integer.parseInt) with null safety, representing the number of doses per unit for vaccine products - a critical business attribute for vaccine inventory management
  - *Implemented in*: VBR_Raw_item
  - *Impact if wrong*: Dose-per-unit data would be missing or wrong, causing incorrect dose quantity reporting for vaccine inventory

*... and 4 additional rules in this region.*

### Japan (JPN)

**BR-JPN-042**: Item ledger entries and item application entries track inventory movements and item costing, supporting COGS and inventory valuation.
  - *Implemented in*: VJP_Raw_itemLedgerEntries, VJP_Raw_itemApplicationEntries
  - *Impact if wrong*: Inventory movement tracking and item cost calculations would be unavailable

**BR-JPN-047**: Value entries are extracted from BC, providing cost valuation records that link inventory movements to financial values for COGS calculation.
  - *Implemented in*: VJP_Raw_valueEntries
  - *Impact if wrong*: Item cost valuation data missing, breaking COGS and inventory value calculations

**BR-JPN-055**: VJA_Clean_Inventory_value_Item transforms raw Value_Entry data into clean Inventory_Value_Item records, enriched with fiscal period data for period-based inventory valuation.
  - *Implemented in*: VJA_Clean_Inventory_value_Item
  - *Impact if wrong*: Inventory valuation data unavailable, preventing accurate balance sheet reporting for Japan

*... and 1 additional rules in this region.*

## Product Master Data

*7 business rules identified across the platform.*

### Germany (DEU)

**BR-DEU-014**: VDA item master records are cleaned with Entity set to 'Vaxxinova Germany Autogenous', Item_Id prefixed with 'VDA-', and Last_Modified_Date preserved to support incremental data loading and product catalog synchronization.
  - *Implemented in*: VDA_BC_Clean_Item_v1
  - *Impact if wrong*: Product master data would lack subsidiary identification, making it impossible to distinguish VDA products from other entities in the unified product catalog.

### Generic / Shared

**BR-GENERIC-007**: GEN_BC_Item extracts the product master from Business Central including item number, display name, category, blocked status, inventory levels, unit price, unit cost, tax configuration, and unit of measure. The lastModifiedDateTime supports incremental synchronization.
  - *Implemented in*: GEN_BC_Item
  - *Impact if wrong*: Missing or stale product master data would cause lookup failures in sales transformation jobs, leading to null Product_Name fields and incorrect product categorization in reports.

**BR-GENERIC-008**: Item blocked status is preserved from Business Central, indicating products that are discontinued or suspended from sale. This flag is critical for filtering active vs. inactive products in the vaccine portfolio.
  - *Implemented in*: GEN_BC_Item
  - *Impact if wrong*: Blocked items would appear as active in product catalogs, potentially allowing orders for discontinued vaccines or including them in active product performance metrics.

**BR-GENERIC-017**: The lastModifiedDateTime for items is parsed using TalendDate.parseDateInUTC to ensure timezone-consistent timestamps. The 'T' separator in ISO 8601 format is replaced with a space to match the parser's expected format.
  - *Implemented in*: GEN_BC_Item
  - *Impact if wrong*: Non-UTC timestamp parsing would produce timezone-shifted modification dates, potentially causing incremental loads to miss recently modified items or reprocess old ones.

### Italy (ITA)

**BR-ITA-010**: Italian product data includes a rich classification hierarchy: ART_TIPO (product type), ART_CAT_CONTAB (accounting category), ART_CLAS_MERC (commercial class), ART_RAMO_AZIENDA (business branch), and ART_SETTORE (sector). Each has code, description, and combined code+description fields.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Product categorization for vaccine portfolio analysis, business branch reporting, and sector-level P&L would be unavailable for the Italian entity.

**BR-ITA-011**: The ART_STABILIMENTO fields (COD, DESC, COD_DESC) track the production facility/plant for each product. ARTI_STABILIMENTO_STORICO preserves the historical plant assignment, while FASE_STABILIMENTO tracks the manufacturing phase facility.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Manufacturing site attribution for COGS allocation and production capacity analysis would be lost, affecting transfer pricing between Vaxxinova production facilities.

### South Africa (SAF)

**BR-SAF-007**: South African product classification uses the same Italian hierarchy: ART_TIPO (type), ART_CAT_CONTAB (accounting category), ART_CLAS_MERC (commercial class), ART_RAMO_AZIENDA (business branch), ART_SETTORE (sector), and ART_STABILIMENTO (production facility).
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Product-level reporting dimensions would be missing for the South African entity, preventing group-wide product portfolio analysis across ITA and SAF.

## Production

*37 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-001**: Production order line absorption costs are calculated by joining item ledger entries (filtered to Output/Saida entry types) with value entries (filtered to ALLOC% document numbers), linking production output to overhead cost allocation records
  - *Implemented in*: VBR_Clean_Production_Order_Line_Absorption
  - *Impact if wrong*: Absorption costs would not be attributed to production orders, understating the true cost of manufactured vaccine products

**BR-BRA-002**: Only production orders with status 'Finished' or 'Pending' are included in the absorption calculation, excluding draft or cancelled orders that should not carry cost allocations
  - *Implemented in*: VBR_Clean_Production_Order_Line_Absorption
  - *Impact if wrong*: Draft or cancelled production orders would receive cost allocations, distorting product costing

**BR-BRA-003**: Absorption entry numbers are constructed as 'AVE-' prefix plus Entry_Number and Absorption_Document_Number, creating unique identifiers for each absorption value entry linked to production
  - *Implemented in*: VBR_Clean_Production_Order_Line_Absorption
  - *Impact if wrong*: Non-unique absorption entries would cause double-counting of overhead costs

*... and 31 additional rules in this region.*

### Global

**BR-Global-074**: Standard cost prices are extracted from Salesforce via Salesforce_Standard_Cost_Prices_v1, providing the reference cost basis for margin and COGS calculations.
  - *Implemented in*: Salesforce_Standard_Cost_Prices_v1
  - *Impact if wrong*: Standard costs would be outdated, producing incorrect margin and profitability calculations

### Japan (JPN)

**BR-JPN-041**: Production orders are extracted from BC to track manufacturing operations for Vaxxinova Japan vaccine production.
  - *Implemented in*: VJP_Raw_productionOrders
  - *Impact if wrong*: Production planning and cost tracking data would be missing from the data warehouse

**BR-JPN-061**: VJA_Clean_Item_v2 transforms the raw Item master into a clean Item table, providing the product catalog used in invoice and inventory processing.
  - *Implemented in*: VJA_Clean_Item_v2
  - *Impact if wrong*: Japan product catalog would be missing, causing product lookup failures

## Reporting

*2 business rules identified across the platform.*

### Global

**BR-Global-039**: Sales_Set_VOPAN reads from Sales_Set_Reporting and outputs a VOPAN-specific view for Pan-American operations reporting.
  - *Implemented in*: Sales_Set_VOPAN
  - *Impact if wrong*: VOPAN regional reports would be unavailable or show incorrect data

### Japan (JPN)

**BR-JPN-070**: The Japan data pipeline follows a layered architecture: Raw (MSSQL/BC extraction) -> Clean (VJA_Clean_* transformations) -> Invoice_Set (consolidation) -> Sales_Set (global integration), ensuring data quality at each stage.
  - *Implemented in*: VJA_Clean_Inventory_value_Item, VJA_Clean_Invoice_Item_v2, VJA_Clean_Invoice_v2, VJA_Clean_Invoice_Set_v7, VJP_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Skipping any layer would produce unvalidated data in the global reporting dataset

## Sales & Revenue

*166 business rules identified across the platform.*

### Brazil (BRA)

**BR-BRA-001**: BioVet (BIO) sales invoices and product returns are combined into a unified Invoice Set by reading from VW_Sales and VW_Returns views in the raw layer, then merging them via tUnite to create a single sales dataset for downstream reporting
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Sales and returns would not be consolidated, leading to incomplete revenue reporting for the BioVet subsidiary

**BR-BRA-002**: For sales records, the unique identifier (Id) is directly taken from the source Invoice_Item_Id, preserving the ERP-assigned line-item identifier for traceability back to the BioVet source system
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Loss of traceability between clean layer sales records and source ERP invoice line items

**BR-BRA-003**: For product returns, the unique identifier is constructed by concatenating Return_Invoice_Number and Product_Id with a hyphen separator, creating a composite key since returns lack a dedicated line-item ID
  - *Implemented in*: BIO_Clean_Invoice_Set_v5
  - *Impact if wrong*: Duplicate or missing return records in the sales set, causing inaccurate net revenue calculations

*... and 55 additional rules in this region.*

### Germany (DEU)

**BR-DEU-001**: VDA (Vaxxinova Germany Autogenous) sales invoices and credit memos from Business Central are combined into a unified Invoice_Set_BC_2 dataset. Invoice lines are filtered to only include Type IN ('Charge (Item)', 'Item'), excluding non-product charges such as G/L accounts or resources.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3
  - *Impact if wrong*: Non-product line items (e.g., G/L entries, comments) would pollute the sales dataset, inflating revenue figures and creating invalid product-level records.

**BR-DEU-002**: Entity is hardcoded to 'Vaxxinova Germany Autogenous' for VDA subsidiary jobs and 'Vaxxinova Germany' for VDD subsidiary jobs, enabling group-level consolidation of sales data across the Vaxxinova organization.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3, VDA_BC_Clean_SalesInvoices
  - *Impact if wrong*: Revenue would be attributed to the wrong legal entity, causing incorrect subsidiary-level P&L reporting and group consolidation errors.

**BR-DEU-003**: All record identifiers (Id, Invoice_Id, Product_Id, Invoice_Account_Id) are prefixed with 'VDA-' to ensure global uniqueness across all Vaxxinova subsidiaries in the consolidated data warehouse.
  - *Implemented in*: VDA_BC_Clean_Invoice_Set_v3, VDA_BC_Clean_SalesInvoices, VDA_BC_Clean_Item_v1
  - *Impact if wrong*: ID collisions between subsidiaries would cause data overwrites, incorrect joins, and misattribution of invoices, products, or customers across entities.

*... and 7 additional rules in this region.*

### Generic / Shared

**BR-GENERIC-009**: GEN_BC_SalesCreditMemos extracts both header and line detail data from the BC Sales Credit Memos API, joining them into a denormalized structure. Header fields include customer, billing/shipping addresses, currency, payment terms, and totals; detail fields include item, quantity, pricing, discounts, and tax.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Missing credit memo data would overstate net revenue since returns and refunds would not be subtracted. The header-detail denormalization failure would produce orphan lines without customer context.

**BR-GENERIC-014**: The customerReturnReasonId field is extracted from credit memo headers, linking credit memos to their return reason classification. This enables return reason analysis (quality issues, shipping errors, etc.) across the Vaxxinova group.
  - *Implemented in*: GEN_BC_SalesCreditMemos
  - *Impact if wrong*: Return reason analysis would be impossible, preventing identification of systematic quality issues or process failures that drive credit memo volumes.

### Global

**BR-Global-002**: Budget quantities and sales amounts are conditionally populated only when Category equals Budget; forecast fields are populated only when Category equals Forecast. This separation ensures budget vs. forecast metrics never mix in a single record.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Budget and forecast figures would be conflated, producing misleading variance analysis

**BR-Global-003**: Net sales amounts in EUR are calculated by dividing local currency amounts by the Budget_Current_Conversion_Rate, applying the budget-period exchange rate rather than a spot rate for consistency.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: EUR-denominated budget figures would be incorrect, distorting cross-entity financial comparisons

**BR-Global-004**: Cost of Goods Sold (COGS) in EUR uses the same budget conversion rate as revenue, ensuring margin calculations are internally consistent.
  - *Implemented in*: Budget_Build_Sales_Set
  - *Impact if wrong*: Margin calculations in EUR would be inconsistent if revenue and COGS use different conversion rates

*... and 20 additional rules in this region.*

### Italy (ITA)

**BR-ITA-002**: TABANALISI is VIT's comprehensive sales analysis table containing invoice data (FATT_*), delivery data (DATA_CONSEGNA_*), customer data (CLI_*, CLIE_*), product data (ART_*, ARTI_*), agent/salesperson data (AGE_*, AGEN_*), and financial amounts (RLOR, R_IM, R_NF, etc.). It is the single source of truth for Italian subsidiary sales reporting.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Missing or corrupt TABANALISI data would eliminate all Italian subsidiary sales visibility from the consolidated Vaxxinova data warehouse.

**BR-ITA-004**: Italian invoice types are tracked through multiple classification fields: FATT_TIPO (invoice type), FATT_CAUSALE (invoice reason code), DOC_TIPO (document type), and MOV_TIPO (movement type). These enable granular filtering of sales vs. credit notes, budget entries, and other document categories.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Inability to distinguish between actual sales invoices, credit notes, budget entries, and other movement types would corrupt net revenue calculations and financial reporting.

**BR-ITA-006**: Italian sales territories are captured through ZONA_CODICE and ZONA_DESCRIZIONE (zone code and description), providing geographic sales segmentation within Italy. The PROV field captures the Italian province for further regional granularity.
  - *Implemented in*: ITA_VIT_RAW
  - *Impact if wrong*: Territory-based sales target tracking and agent commission calculations would lose their geographic dimension for the Italian market.

*... and 4 additional rules in this region.*

### Japan (JPN)

**BR-JPN-001**: Entity is hardcoded to Vaxxinova Japan for all VJP sales records, ensuring proper entity attribution in the global Sales_Set.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set_Backup
  - *Impact if wrong*: Japan sales records would not be attributed to the correct entity, corrupting entity-level P&L

**BR-JPN-003**: Sales data is sourced from four GL entry streams (GJH=General Journal, CoGS=Cost of Goods Sold, GCR=General Credit, GRC=General Receipt) plus sales invoice/credit memo lines, capturing all revenue and cost components.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Missing data streams would result in incomplete sales figures for Japan

**BR-JPN-004**: Quantities and amounts from GL entries are multiplied by -1 to reverse the accounting sign convention, converting debit-positive GL amounts to sales-positive reporting amounts.
  - *Implemented in*: Copy_of_VJP_Clean_Build_Sales_Set, VJP_Clean_Build_Sales_Set
  - *Impact if wrong*: Sales amounts would appear as negative values, reversing the sign of all Japan revenue

*... and 23 additional rules in this region.*

### South Africa (SAF)

**BR-SAF-004**: The SAF TABANALISI schema mirrors the Italian structure with invoice data (FATT_*), delivery data (DATA_CONSEGNA_*), customer data (CLI_*, CLIE_*), product data (ART_*), and financial amounts, suggesting VSA uses the same ERP system (or a closely related one) as VIT.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: If the South African source schema diverges from the Italian template, column mapping mismatches would produce null or incorrect values across all sales metrics.

**BR-SAF-008**: Invoice temporal dimensions (FATT_DATA_ANNO/TRIMESTRE/MESE/SETTIMANA/GIORNO) and delivery temporal dimensions (DATA_CONSEGNA_ANNO/TRIMESTRE/MESE/SETTIMANA/GIORNO) are extracted with pre-computed time hierarchies, enabling time-series analysis without warehouse-side date decomposition.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Time-based aggregations for the South African subsidiary would need to be recomputed, and any inconsistency between pre-computed and actual dates would cause reporting discrepancies.

**BR-SAF-011**: The FASE (manufacturing phase) and FASE_STABILIMENTO (phase production facility) fields are extracted, tracking which production stage and facility are associated with each South African sale, paralleling the Italian vaccine production tracking model.
  - *Implemented in*: ITA_VIT_RAW_Copy
  - *Impact if wrong*: Production phase revenue attribution would be lost for South Africa, preventing alignment of manufacturing capacity with regional sales demand.

*... and 1 additional rules in this region.*

### United States (USA)

**BR-USA-001**: Animalytix distributor sales data is transformed into a standardized Distributor_Sales_Set for Vaxxinova United States, integrating third-party distributor sell-through data with internal sales reporting
  - *Implemented in*: VUS_Clean_Animalytix
  - *Impact if wrong*: Distributor channel sales would be missing from VUS revenue reporting, significantly understating total market performance

**BR-USA-002**: Entity is hardcoded to 'Vaxxinova United States' for all Animalytix records, enabling proper entity identification in the consolidated global sales dataset
  - *Implemented in*: VUS_Clean_Animalytix
  - *Impact if wrong*: Distributor sales would not be attributed to VUS in consolidated reporting

**BR-USA-003**: Unique transaction identifiers are constructed as 'VUS-ANI-' prefix plus trs_id, creating globally unique keys that identify Animalytix-sourced transactions distinctly from direct sales
  - *Implemented in*: VUS_Clean_Animalytix
  - *Impact if wrong*: Animalytix transaction IDs could collide with direct sales IDs, causing data corruption in the unified sales dataset

*... and 23 additional rules in this region.*

### South Africa (ZAF)

**BR-ZAF-001**: Deltamune (DEL) sales data from the South African subsidiary is extracted from the vw_InvoiceDetails_ETL BigQuery view with a hard-coded date filter of POST_DATE >= '2016-07-01', establishing July 2016 as the earliest fiscal boundary for ZAF sales analysis.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Removing or changing the date filter would include pre-migration legacy data that may have inconsistent formats, or excluding it would create gaps in historical trend analysis.

**BR-ZAF-002**: Entity is hardcoded to 'Deltamune' for the DEL subsidiary within South Africa. All record IDs (Id, Invoice_Account_Id, Product_Id, Invoice_Item_Id) are prefixed with 'DEL-' to ensure global uniqueness across the Vaxxinova group data warehouse.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: ID collisions with other subsidiaries (VDA, VDD, VIT) would cause data corruption during group consolidation, misattributing South African sales to other entities.

**BR-ZAF-003**: The composite record ID is constructed as 'DEL-' + numeric ID + '-' + trimmed Item_Number, creating a unique line-level identifier. Invoice_Item_Id combines invoice number and item number ('DEL-InvoiceNo-ItemNo'), enabling traceability from the consolidated dataset back to source invoice lines.
  - *Implemented in*: DEL_Clean_Sales_Set_Delete_Ingest
  - *Impact if wrong*: Non-unique line IDs would cause record deduplication errors. Loss of the invoice-line composite key would break drill-down from summary to detail in sales reports.

*... and 4 additional rules in this region.*

### Scratchpad / Development

**BR-scratchpad-005**: The sandbox job extracts Business Central Sales Invoice Line data (via tBigQueryInput reading from BC raw tables) including full line-level detail: Document_No, Line_No, quantities, pricing (Unit_Price, Unit_Cost_LCY), discounts, amounts, VAT fields, and posting groups.
  - *Implemented in*: sandbox
  - *Impact if wrong*: This is a development exploration job. No production impact, but it reveals the full BC sales invoice line schema that production jobs (VDA, VDD) work with.

**BR-scratchpad-006**: The sandbox job maps all Business Central sales invoice line fields through a tMap with direct 1:1 field mappings (no transformations), creating a passthrough for data exploration. Fields include Shortcut_Dimension_1_Code and Shortcut_Dimension_2_Code for BC analytics dimensions.
  - *Implemented in*: sandbox
  - *Impact if wrong*: No production impact. This test job demonstrates the available BC field set that could be incorporated into production clean jobs if additional dimensions are needed.

**BR-scratchpad-008**: The sandbox reveals additional BC sales fields not currently used in production clean jobs: Gross_Weight, Net_Weight, Units_per_Parcel, Unit_Volume (logistics), Job_No/Job_Task_No (project-based billing), IC_Partner_Code/Reference (intercompany), and Return_Reason_Code.
  - *Implemented in*: sandbox
  - *Impact if wrong*: No production impact. These fields represent potential enrichment opportunities for production jobs if logistics tracking, project billing, or intercompany analysis is needed.

## Migration Considerations

Each business rule above represents logic that must be faithfully replicated in the target platform. Key considerations:

- **Sales consolidation logic** (return negation, invoice/credit memo merging) is the highest-risk area for migration errors
- **Fiscal period mapping** depends on the Global_Raw.Fiscal_Periods reference table and must be migrated first
- **Currency conversion** rules differ by region and data category (actuals vs. budget vs. forecast)
- **Salesforce sync rules** include specific field mappings and external ID conventions that must be preserved
- **Entity assignment** logic varies by subsidiary and must be validated per-region during migration testing
