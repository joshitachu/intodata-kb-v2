# Lobster Data Analysis Guide

This guide replaces the individual analysis prompts for Phase 2 sub-agents.
Use it to produce a structured analysis of each profile group's scan data.
Write markdown, not JSON. Be exhaustive -- include every filter chain, mapping, field, and risk.
Every claim needs a specific profile name and component reference.

---

## Lobster Platform Reference

### Profile Structure

A Lobster **profile** (datawizardprofile) is a complete data integration flow containing:

1. **Agent**: The entry point -- how data arrives. Types:
   - `LobsterInputAgent_FTP_Cron` -- scheduled SFTP/FTP file pickup (polls directory)
   - `LobsterInputAgent_HTTP` -- REST API endpoint (receives HTTP requests)
   - `LobsterInputAgent_DB_Cron` -- scheduled database polling
   - `LobsterInputAgent_File_Cron` -- scheduled local file pickup
   - `LobsterInputAgent_HTTP_Cron` -- scheduled HTTP polling
   - `LobsterInputAgent_Message` -- email/SMTP message receiver
   - `LobsterInputAgent_Portal` -- web portal upload
   - `LobsterInputAgent_Manual` -- manual file upload via UI

2. **Data Properties** (`LobsterParser_*`): Input parsing configuration:
   - File type: XML, JSON, CSV, Fixed, EDI/EDIFACT, Excel, IDoc, Swift
   - Encoding, delimiters, record boundaries, XPath settings

3. **Input Tree** (`LobsterSourceSchema`): Hierarchical source data structure:
   - Nodes: container elements (is_node=true) and leaf elements
   - Fields: named, typed (String, Integer, BigInteger, Decimal, Date, Boolean)
   - Field name prefix `S-` indicates source field
   - Cardinality: min/max define occurrence constraints

4. **Output Tree** (`LobsterMapping`): Target structure with transformation logic:
   - Same hierarchical node/field structure as input tree
   - Fields contain **filter chains** that define all transformations
   - Calculation nodes (no_output=true) hold intermediate computations

5. **Response Units** (`LobsterResponse_*`): Output destinations:
   - FTP/SFTP upload, HTTP request/response, file write, email, database, AS2, OFTP
   - Each has its own communication channel configuration
   - Multiple response units = fan-out to multiple targets

6. **Variables**: Profile-scoped storage:
   - System variables (`VAR_SYS_*`, `VAR_FILENAME_*`, `VAR_ORIGINATOR_*`): auto-populated
   - User variables: defined per-profile, read/written by filter chains

### Filter Chains (Transformation Logic)

Filter chains are Lobster's equivalent of Talend tMap expressions or Workato datapills.
Each output field can have one or more `<filter>` elements forming a chain.

**Filter parameter types:**
- `field` -- references an input tree field by name (e.g., `S-companyName`)
- `constant` -- literal value (often base64-encoded in the XML)
- `result` -- references output of a previous filter in the chain (by position)
- `variable` -- references a profile variable by name
- `destination` -- references another output field by name

**Common filter operations:**
- `copy(field/value/variable)` -- direct field mapping
- `concat(a,b,c,...)` -- string concatenation
- `substring(a, start, length)` -- substring extraction
- `find-substring(a, b)` -- find position of b in a
- `a equals b` -- equality comparison
- `a == b ? c : d` -- conditional (ternary)
- `logical-and(a, b, ...)` / `logical-or(a, b, ...)` -- boolean logic
- `save variable a(b) type-safe` -- store value in variable for later use
- `http json-lookup(...)` -- external API call with response parsing
- `fill-from-left(a, b, c)` -- left-pad string
- `iteration-level` -- current loop nesting depth
- `copy field by name(variable/result a)` -- dynamic field reference

### Profile Chaining

Profiles chain through three mechanisms:
- `profile_after_run` -- success chain (next profile after completion)
- `exception_profile` -- error handler (triggered on failure)
- `forward_profile` -- data forwarding (passes data to another profile)

### Communication Channels

External system connections identified by:
- `comm_channel_partner` -- partner/organization name
- `comm_channel_name` -- channel type (FTP, HTTP, AS2, OFTP)
- `comm_channel_id` -- system-internal channel ID

Multiple profiles sharing the same partner:channel combination use the same connection.

---

## Section Analysis Instructions

For each section below, analyze ALL profiles in your group. Be exhaustive.
Every claim needs a specific profile name and component reference.

### COMPONENTS

Extract for each profile:
- Profile name, type (agent type), group (path_of_group), active/suspended status
- Agent component: type, host/port/URL, schedule interval, file patterns
- Parser component: file type, encoding, record tag, XPath settings
- Source schema: compact tree with field names, types, cardinality
- Output mapping: compact tree with field names, filter chain summaries
- Response units: type, label, target host/directory, communication channel
- Variables: user variables only (system variables are stripped by distiller)
- Cross-profile references: profile_after_run, exception_profile, forward_profile

Also catalog connections:
- **Communication channels**: partner name, channel type, referenced by which profiles
- **External APIs**: URLs referenced in http-json-lookup filters
- **Database connections**: SQL sources/targets

### EXECUTION_FLOW

Extract for each profile:
- **Agent trigger analysis**: type, polling interval, file pattern, URL endpoint
- **Data flow path**: agent -> parser -> input tree -> filter chains -> output tree -> response units
- **Filter chain mapping**: for each output field with filters, document the full transformation chain
- **Conditional logic**: filters using `a == b ? c : d`, `a equals b`, `logical-and/or`
- **Variable flow**: where variables are written (save variable) and read (variable type refs)
- **Calculation node architecture**: which calc fields feed which output fields
- **Response unit sequencing**: execution order, dependencies between units
- **Profile chain execution**: the complete chain from this profile through all chained profiles
- **Error paths**: exception_profile chain, response unit error handling

### DATA_FLOWS

For EVERY output field with filter chains:
- List the full filter chain (all filters in order)
- Source fields referenced (field type parameters)
- Constants used (decoded from base64 where possible)
- Variables read/written
- **HUMAN-READABLE explanation** of the transformation logic
- Intermediate results flow (result type references between filters)

For agent data sources:
- Source type (FTP directory, HTTP endpoint, database query)
- File pattern or URL
- Connection details (communication channel)

For response unit outputs:
- Target type (FTP upload, HTTP call, file write, email)
- Target location (host, directory, filename pattern)
- Communication channel used

Cross-profile data flows:
- File-based handoff (profile A writes file, profile B reads file)
- Variable passing via MSG_CALL variables
- Forward profile data passing

### TRANSFORMATION_DETAIL

For EVERY profile with filter chains, produce a per-profile field mapping breakdown:

1. **Field mapping table** (ALL output fields with filters):

   | Source Field | Filter Chain Summary | Target Field | Explanation |
   |--------------|---------------------|--------------|-------------|
   | S-companyName | copy(field:S-companyName) | companyName | Direct mapping |
   | S-orderDate | copy(field:S-orderDate), substring(result:0, const:"0", const:"10") | date | Extract date portion |
   | (variable) | copy(var:VAR_PARTNER_ID) | partnerId | From profile variable |

2. **Step-by-step filter chain traces**: For complex chains (3+ filters):
   - Document each filter in order with its inputs and output
   - Example:
     ```
     Step 1: copy(field:S-amount) -> result_1 (raw amount string)
     Step 2: find-substring(result_1, const:",") -> result_2 (comma position)
     Step 3: substring(result_1, const:"0", result_2) -> result_3 (integer part)
     Step 4: concat(result_3, const:".", ...) -> final output
     ```
   - Include at least one trace per profile with complex transformations

3. **Variable flow diagrams**: For profiles using save/read variable patterns:
   - Which fields save to which variables (save variable filter)
   - Which fields read from those variables (variable type refs)
   - Cross-field dependencies through variables
   - Example: `S-orderHeader.customerId -> save variable VAR_CUST_ID -> read by orderLine.customerId`

4. **Conditional logic maps**: For profiles with conditional filters:
   - Condition expression
   - True path (what value/transformation is applied)
   - False path (what value/transformation is applied)
   - Which output fields are affected

5. **Calculation node architecture**: For profiles with calcNode fields:
   - Which calc fields exist and what they compute
   - Which output fields depend on calc results
   - Data flow: input -> calc nodes -> output fields

### BUSINESS_RULES

Extract EVERY business rule with this format:
- **BR-{GRP}-NNN**: sequential rule ID
- **Expression**: the filter chain or conditional implementing the rule
- **Implementing profile**: which profile and output field contains it
- **Domain**: business area
- **Impact**: what happens if the rule is violated or removed

Where to find business rules:
- Conditional filters (a == b ? c : d)
- Comparison filters (a equals b, a > b)
- Logical combination filters (logical-and, logical-or)
- Variable-based conditional behavior
- Calculation node logic
- SQL queries in database agents/response units
- Profile chaining conditions

### EXTERNAL_SYSTEMS

For all external connections:
- System type: FTP server, REST API, database, email, AS2 partner, OFTP partner
- Communication channel: partner name, channel type, channel ID
- Protocol: FTP, SFTP, OFTP2, HTTP/HTTPS, AS2, SMTP
- Which profiles use this connection and for what purpose (input/output)
- Criticality: how many profiles depend on this channel

### ERROR_HANDLING

Rate EACH profile's error handling:
- **GOOD**: has exception_profile, response units with retries and treat_as_global_error
- **ADEQUATE**: has exception_profile but response units lack retry config
- **POOR**: no exception_profile, some response units have treat_as_global_error
- **NONE**: no exception_profile, no response unit error handling

Document:
- Exception profile chains
- Response unit retry configuration
- Error notification patterns
- Profile chain failure propagation

### ENVIRONMENT_CONFIG

For every user variable:
- Variable name, type, default value
- Which filters read/write it
- Whether it varies by environment

Document:
- Communication channel environment patterns (production vs test)
- File path patterns that differ by environment
- Encoding settings across profiles
- not_on_test flags on response units

### SCHEDULING

Extract:
- **Agent types**: cron (FTP/DB/File/HTTP with interval), HTTP (on-demand), manual
- **Polling intervals**: frequency and human-readable interpretation
- **File age settings**: minimum file age before processing
- **Profile chains**: execution dependency ordering
- **Active/inactive status**: which profiles are active
- **Conflict detection**: profiles polling the same directory with overlapping patterns

### RISK_FLAGS

Check against these risk categories:

**Security risks:**
- OBF passwords (obfuscated, not encrypted) in agent settings
- Base64-encoded credentials in filter chain constants
- HTTP endpoints without authentication (auth_type=0)
- API keys in filter constants

**Performance risks:**
- Unbounded max_records (2147483647)
- Short polling intervals (< 5 min) on large directories
- Repeated http-json-lookup calls without caching
- Large input trees with many optional nodes

**Reliability risks:**
- Missing exception_profile on production profiles
- Response units without retry configuration
- Profile chains referencing inactive profiles
- Encoding mismatches in profile chains

**Maintainability risks:**
- Long filter chains (>8 filters) without documentation
- Missing profile descriptions
- Flat group organization
- Undocumented calculation nodes
- System variable abuse for business logic
