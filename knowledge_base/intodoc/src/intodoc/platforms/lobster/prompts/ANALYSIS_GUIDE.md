# Lobster Data Analysis Guide

This guide replaces the individual analysis prompts for Phase 2 sub-agents.
Use it to produce a structured analysis of each profile group's scan data.
Write markdown, not JSON. Be exhaustive -- include every filter chain, mapping, field, and risk.
Every claim needs a specific profile name and component reference.

---

## Depth Contract

Apply this evidence standard to every section:
- Cite every claim with the exact profile name, component type, field/filter/response unit, communication channel, and artifact/reference where it was found (profile XML, parser config, source/output tree, response unit, scan warning).
- Enumerate concrete evidence before summarizing. List actual profiles, filters, fields, variables, constants, channels, chained profiles, and response units first; then infer architecture, risk, or intent.
- Quote filter operations, decoded constants, SQL, conditions, channel IDs/names, parser settings, mappings, variables, and chained profile names verbatim where present.
- Label scanner-limited evidence explicitly, for example: "constant could not be decoded", "channel host redacted", "profile XML omitted retry details", or "schema type inferred from name only".
- For important findings include impact, confidence (HIGH/MEDIUM/LOW), risk, and affected units (profiles, fields, filters, variables, parser formats, channels, response units, chained profiles).

## Output Quality Gate

Your output is valid only if every canonical section contains cited evidence before interpretation:
- Start each section with concrete profile/filter/field/channel/response-unit evidence. Summaries, architecture conclusions, and recommendations must come after the evidence.
- Empty sections are allowed only after stating what was checked and whether the absence is confirmed or scanner-limited.
- Any broad claim such as "uses FTP", "has error handling", "transforms order data", or "calls another profile" must cite the exact profile, component type, field/filter/response unit, communication channel, and source artifact.
- Important findings must include affected unit, source reference, impact, confidence, risk, and scanner limitation if applicable.
- Do not collapse multiple profiles into one generic paragraph. If several profiles share a pattern, list each profile and then summarize the pattern.

## Anti-Shallow Output Rule

The following output patterns are invalid:
- Architecture prose without profile topology, agent/parser/source/output/response-unit structure, channels, variables, and profile-chain references listed first.
- Data-flow summaries without source field -> filter/result/variable/calculation node -> output field lineage.
- Business-rule summaries without quoting the filter operation, decoded constant, SQL predicate, parser setting, variable reference, or profile-chain setting implementing the rule.
- Error-handling claims without exception_profile, response-unit retry/global-error, parser failure, channel failure, or failure-propagation evidence.
- Change-impact guidance without affected profiles/fields/filters/variables/channels/response units/profile chains, required checks, rollback risk, and confidence.

## Required Evidence Tables

Include these tables when evidence exists; if evidence is absent, state the checked source and limitation:
- **Profile topology table**: profile, group, active/suspended state, agent type, parser type, source/output trees, response units, channels, chained profiles.
- **Filter-chain lineage table**: profile, output field, full filter chain, source fields, result references, variables, decoded constants, calculation nodes, plain-English meaning, downstream consumers.
- **Variable flow table**: variable, profile, save filter, read filters, source field/result, affected output fields, cross-field dependency, scanner limitations.
- **Response-unit/channel table**: response unit, profile, response type, partner/channel/id, target path/URL/table, retry/global-error/timeout behavior, fan-out siblings.
- **Exception/profile-chain table**: profile, profile_after_run, forward_profile, exception_profile, inactive/missing targets, payload handoff assumption, blast radius, failure propagation.
- **Change-impact table**: change target, affected profiles/fields/filters/channels/chains, required validation checks, rollback risk, confidence, scanner limitations.

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
- Agent component: type, host/port/URL, schedule interval, polling directory, file patterns, manual/HTTP endpoint details
- Parser component: parser class, file type, encoding, delimiter/fixed-width/XML/JSON/EDI settings, record tag, XPath settings
- Source schema: compact tree with field names, types, cardinality, node paths, and scanner-limited missing metadata
- Output mapping: compact tree with field names, calculation nodes, no_output fields, and filter chain summaries
- Response units: type, label, target host/directory/URL/table, communication channel, treat_as_global_error/retry settings if visible
- Variables: user variables only (system variables are stripped by distiller), including where each is written/read
- Cross-profile references: profile_after_run, exception_profile, forward_profile, inactive/missing referenced profiles

Also catalog connections:
- **Communication channels**: partner name, channel type, referenced by which profiles
- **External APIs**: URLs referenced in http-json-lookup filters
- **Database connections**: SQL sources/targets
- **Response units**: response type, channel, file/table/endpoint target, fan-out relationship, and failure behavior

### EXECUTION_FLOW

Extract for each profile:
- **Agent trigger analysis**: type, polling interval, file pattern, URL endpoint
- **Data flow path**: agent -> parser -> input tree -> filter chains -> output tree -> response units
- **Filter chain mapping**: for each output field with filters, document the full transformation chain
- **Conditional logic**: filters using `a == b ? c : d`, `a equals b`, `logical-and/or`
- **Variable flow**: where variables are written (save variable) and read (variable type refs)
- **Calculation node architecture**: which calc fields feed which output fields
- **Response unit sequencing**: execution order, dependencies between units, fan-out to multiple outputs, and whether any response failure blocks later responses
- **Profile chain execution**: the complete chain from this profile through profile_after_run, forward_profile, and exception_profile references, including inactive/missing targets
- **Error paths**: exception_profile chain, response unit error handling, treat_as_global_error, retry policy, and failure propagation to caller/chained profile

### DATA_FLOWS

For EVERY output field with filter chains:
- List the full filter chain (all filters in order)
- Source fields referenced (field type parameters)
- Constants used (decoded from base64 where possible)
- Variables read/written
- Destination/output fields referenced by `destination` parameters
- **HUMAN-READABLE explanation** of the transformation logic
- Intermediate results flow (result type references between filters)
- Profile-chain blast radius: downstream profiles/response units that receive this transformed field
- This is mandatory for every output field that has filters. If a full filter chain cannot be reconstructed, state the exact output field, profile, missing scanner artifact, and confidence impact.
- Constants must be decoded where scanner data allows it. If a constant cannot be decoded, write `undecoded scanner limitation` with the profile, field, filter index, and raw reference if visible.

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
- profile_after_run success chain payload assumptions
- exception_profile error payload assumptions
- Response fan-out where one parsed input produces multiple output files, HTTP calls, database writes, or messages

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
   - Decode constants where available and mark undecoded constants explicitly

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

6. **Filter-chain lineage completeness**:
   - Track source field -> filter result -> variable/destination/result reference -> target field for every chain
   - Identify conditional filters, constants, decoded values, and fallback/default branches
   - Flag output fields whose chain references missing source fields, missing variables, or inactive chained profiles

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
- Parser format assumptions (delimiter, record tag, XPath, fixed-width position, EDI message type)
- Response routing and channel selection
- Exception/forward/profile_after_run chain behavior

**Extraction sub-categories** -- apply each systematically:

1. **Domain inference**: From profile names, group paths, channel partner names, parser formats, source/output field names, and response targets, infer the business area. Tag every rule with the inferred domain and confidence.
2. **Filter expression rules**: Extract every non-trivial conditional, comparison, logical operation, calculation, substring/concat/padding operation, decoded constant mapping, and variable-driven branch as a separate rule.
3. **Parser/schema rules**: Record delimiter, encoding, XPath, fixed-width, EDI/IDoc, CSV header, and cardinality settings that constrain accepted input. Explain what breaks if the input format changes.
4. **Profile structure rules**: Document ordering implied by profile_after_run, forward_profile, exception_profile, response fan-out, and inactive chained references.
5. **Parameter catalog**: List every hardcoded value, decoded constant, path fragment, status code, partner/channel name, threshold, date format, and magic number with location, apparent purpose, and staleness risk.
6. **Impact analysis per rule**: Rate blast radius as ISOLATED (single field/profile), MODERATE (response/chained profile affected), or WIDE (multiple channels/groups/external consumers).
7. **Conflict detection**: Find inconsistent parsing, encoding, date formats, status mappings, null/default handling, response routing, or channel use for the same business concept across profiles.

### SCHEMA_DICTIONARY

For EVERY source tree, output tree, database table, API payload, file structure, and response schema referenced in the group:
- Structure name (profile + source tree/output tree/response unit/table/API payload)
- Field/node path, field name, data type, cardinality, nullable/required status if visible
- Which profiles READ from it (agent/parser/source tree/filter field refs)
- Which profiles WRITE to it (output tree fields, response units, database writes, HTTP payloads)
- Parser or response unit that owns the structure
- Connection/communication channel used to access or deliver it

Additional extraction depth:
- Mark source-only, output-only, calculated/no_output, and pass-through fields.
- Flag source fields referenced by filters but absent from the source tree.
- Flag output fields populated by constants/defaults only.
- Compare field expectations across profile chains and response fan-out for schema drift risks.
- Use "unknown" for metadata not present in the scan rather than omitting the column/field.

### SOURCE_SQL

If no SQL queries exist in this group, write exactly: `None`

Extract EVERY SQL query in FULL (never truncate). For each:
- Which database agent, response unit, or filter contains it
- Query type: SELECT, INSERT, UPDATE, DELETE, DDL, stored procedure call
- Tables and columns referenced
- Parameterized placeholders or variable/filter interpolation
- Dynamic parts and injection risk
- WHERE/JOIN/CASE/HAVING clauses that encode business rules

### EXTERNAL_SYSTEMS

For all external connections:
- System type: FTP server, REST API, database, email, AS2 partner, OFTP partner
- Communication channel: partner name, channel type, channel ID
- Protocol: FTP, SFTP, OFTP2, HTTP/HTTPS, AS2, SMTP
- Which profiles use this connection and for what purpose (input/output)
- Criticality: how many profiles depend on this channel
- Failure surface: retry/timeout/error propagation if visible, and profiles affected by channel outage

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
- Channel failure modes (FTP/HTTP/AS2/OFTP/DB/email) and whether failures block, retry, or continue
- Inactive or missing exception/forward/profile_after_run targets
- Encoding/parser failure handling and whether bad files route to exception profiles
- Per-response-unit failure behavior: for each response unit, record retry count/settings, `treat_as_global_error`, timeout, whether later response units still execute, and whether the failed payload remains recoverable.
- Profile-chain blast radius: for every `profile_after_run`, `forward_profile`, and `exception_profile`, document callers/callees, inactive/missing targets, data handoff assumptions, and downstream profiles affected by failure.

### CUSTOM_CODE

If no custom scripts, custom Java/Groovy code, shell commands, database procedures, or embedded scripting hooks exist in this group, write exactly: `None`

For ALL custom code or script-like components:
- Full code or command text where available (never truncate)
- Profile/component containing it
- Purpose and data it reads/writes
- Hidden business rules, constants, field mappings, or routing decisions
- External dependencies and communication channels
- Risk factors: command injection, SQL injection, credential exposure, unvalidated input, encoding issues, resource leaks

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
- Hardcoded partner/channel/path/URL values that should vary by environment

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
- Parser format drift without validation or quarantine path
- Channel outage with no retry, timeout, or alternate route

**Maintainability risks:**
- Long filter chains (>8 filters) without documentation
- Missing profile descriptions
- Flat group organization
- Undocumented calculation nodes
- System variable abuse for business logic

### NAMING_PATTERNS

Document observed conventions:
- Profile naming: domain/system/process prefixes, source-target naming, suffixes for import/export/forward/error/test
- Group path conventions and whether profiles are organized by domain, partner, channel, or process
- Field naming: `S-` source fields, output field conventions, calc/no_output naming, variable naming
- Communication channel naming: partner:channel naming, environment suffixes, protocol suffixes
- Response unit naming and labels
- Chained profile naming: profile_after_run, forward_profile, exception_profile patterns

Rate naming quality:
- **GOOD**: clear, consistent, self-documenting names
- **ADEQUATE**: mostly consistent with some outliers
- **POOR**: inconsistent, unclear, or hides source/target/process meaning

### CROSS_GROUP_REFS

Identify dependencies between this group and other groups:
- profile_after_run, exception_profile, or forward_profile references to profiles in other groups
- Shared communication channels used by profiles across multiple groups
- Shared file directories, HTTP endpoints, database tables, or partner channels
- Shared variables or naming conventions that imply cross-group coupling
- One group writing a file/message/table that another group reads
- Inactive or missing cross-group referenced profiles
- Execution ordering dependencies where group A must complete before group B

### DEBUG_TESTING_EVIDENCE

Extract assistant-grade operations, debugging, and testability evidence:
- Exception handling: exception_profile target, chain depth, inactive/missing exception profile, error notification response units, and whether failed payload details are preserved.
- Response-unit failure behavior: retry count, treat_as_global_error, timeout settings, channel failure behavior, and whether later response units still execute.
- Parser/debug evidence: parser type, encoding, delimiter/record/XPath/fixed-width settings, decoded constants, bad-file behavior, and mismatch risks across chained profiles.
- Filter-chain debugging: long chains, result index dependencies, variable save/read points, calculation nodes, decoded constants, and fields with unclear lineage.
- Channel diagnostics: partner/channel IDs, protocol, host/path/URL/table where visible, active/test flags, and recovery checks for FTP/HTTP/AS2/OFTP/DB/email failures.
- Idempotency and replay: file pickup patterns, archive/delete behavior, duplicate handling, database keys, HTTP retry safety, and manual rerun risks.
- Test hooks: manual/HTTP agents, sample files or schemas if visible, suspended/test profiles, not_on_test response units, and missing fixture/sample evidence.

### CHANGE_IMPACT_EXTRACTION

For each important change target, document affected units, required checks, confidence, and rollback risk:
- Parser format change: agent/parser/profile, source tree fields, encoding/delimiter/XPath/fixed-width/EDI assumptions, filters depending on field paths, and downstream response schemas.
- Field mapping/filter change: target fields, result references, variables, calculation nodes, decoded constants, conditional branches, response units, and chained profiles.
- Variable change: every save/read filter, default value, system-vs-user variable behavior, and cross-field dependency.
- Communication channel change: all profiles using the partner/channel, response units and agents affected, protocol/auth/host/path assumptions, test flags, and outage blast radius.
- Schedule/agent change: polling interval, file pattern, endpoint, duplicate/replay behavior, upstream producer assumptions, and operational monitoring.
- Response unit change: target schema/path/URL/table, fan-out siblings, retry/global-error behavior, downstream consumers, and rollback plan.
- Profile chain change: profile_after_run, forward_profile, exception_profile callers/callees, inactive targets, payload handoff assumptions, and failure propagation.

### ASSISTANT_DOC_OBLIGATIONS

Phase 2 must produce enough evidence for the assistant-grade docs:
- Architecture docs need profile topology, agents, parsers, source/output trees, response units, channels, variables, and profile chains.
- Lineage docs need source field -> filter/result/variable/calc node -> output field traces, decoded constants, response fan-out, and cross-profile handoff evidence.
- Business-rule docs need conditionals, comparisons, constants, parser assumptions, routing/channel choices, SQL predicates, and profile-chain rules with impact/confidence.
- Runtime/config docs need schedules, active/suspended state, channels, parser encodings, not_on_test flags, retry/global-error settings, and environment naming.
- Debugging/runbook docs need exception profiles, response-unit failure behavior, parser failure clues, channel diagnostics, rerun/idempotency evidence, and recovery checks.
- Risk/change docs need affected profiles/fields/channels/chains, blast radius, rollback risk, required validation checks, scanner limitations, and confidence for each major finding.

### SELF_CHECK_BEFORE_RETURNING

Before returning the Phase 2 analysis, verify:
- All canonical sections are present: COMPONENTS, EXECUTION_FLOW, DATA_FLOWS, TRANSFORMATION_DETAIL, BUSINESS_RULES, SCHEMA_DICTIONARY, SOURCE_SQL, EXTERNAL_SYSTEMS, ERROR_HANDLING, CUSTOM_CODE, ENVIRONMENT_CONFIG, SCHEDULING, NAMING_PATTERNS, CROSS_GROUP_REFS, RISK_FLAGS, DEBUG_TESTING_EVIDENCE, CHANGE_IMPACT_EXTRACTION, ASSISTANT_DOC_OBLIGATIONS.
- Every major claim cites profile name, component type, field/filter/response unit or channel, and artifact/source.
- Every important filter chain, decoded constant, SQL query, parser setting, variable reference, channel, response unit, or profile-chain reference is quoted verbatim where present.
- Scanner-limited evidence is labelled rather than hidden or guessed, especially undecoded constants and omitted channel/retry details.
- The analysis contains enough evidence for all 9 assistant-grade docs, especially operations/debugging and change playbook material.
