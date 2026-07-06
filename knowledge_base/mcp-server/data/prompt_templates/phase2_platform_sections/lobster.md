SECTION-BY-SECTION EXTRACTION REQUIREMENTS
Write every section below. Be exhaustive within each -- no placeholders, no "see above", no "N/A" without justification.
Lobster_data (DataWizard) is an EDI/EAI integration platform: a **profile** is the complete data-integration flow
(agent -> parser -> input tree -> filter-chain mapping -> output tree -> response unit). Never call a profile a
"job" or "recipe" -- use "profile" for the design-time unit.

### COMPONENTS
For EVERY profile:
- Profile name, type (agent type), group (path_of_group), active/suspended status
- Agent (entry point -- how data arrives): `LobsterInputAgent_FTP_Cron` (scheduled SFTP/FTP pickup), `LobsterInputAgent_HTTP` (REST endpoint), `LobsterInputAgent_DB_Cron` (scheduled DB polling), `LobsterInputAgent_File_Cron` (scheduled local file pickup), `LobsterInputAgent_HTTP_Cron` (scheduled HTTP polling), `LobsterInputAgent_Message` (email/SMTP), `LobsterInputAgent_Portal` (web-portal upload), `LobsterInputAgent_Manual` (manual UI upload) -- record host/port/URL, schedule interval, polling directory/file pattern, and for HTTP agents which methods are accepted (`accept_get`/`accept_post`/`accept_put`/`accept_patch`/`accept_delete` -- flag any profile accepting POST/PUT/PATCH/DELETE on what looks like a read-only endpoint)
- Parser (`LobsterParser_*`, data properties): file type (XML, JSON, CSV, Fixed-width, EDI/EDIFACT, Excel, IDoc, Swift), encoding, delimiters, record boundaries, XPath settings
- Input tree (`LobsterSourceSchema`): node/field names, types (String, Integer, BigInteger, Decimal, Date, Boolean), `S-` source-field prefix, cardinality (min/max occurrence)
- Output tree (`LobsterMapping`): field names, calculation nodes (`no_output=true`, intermediate computations only), filter-chain summary per field
- Response units (`LobsterResponse_*`): type (FTP/SFTP upload, HTTP request/response, file write, email, database, AS2, OFTP), target host/directory/URL/table, communication channel, `treat_as_global_error`/retry settings if visible -- multiple response units on one profile means fan-out to multiple targets
- Variables: user variables only (system variables `VAR_SYS_*`/`VAR_FILENAME_*`/`VAR_ORIGINATOR_*` are auto-populated and stripped by the distiller) -- name, where written (save-variable filter), where read
- Profile chaining: `profile_after_run` (success chain), `exception_profile` (error handler), `forward_profile` (data forwarding) -- flag inactive or missing referenced profiles

Also catalog connections:
- Communication channels: partner name (`comm_channel_partner`), channel type (`comm_channel_name`: FTP/HTTP/AS2/OFTP), channel ID (`comm_channel_id`) -- profiles sharing the same partner:channel combination share a connection
- External APIs referenced in `http json-lookup` filters
- Database connections used as SQL sources/targets

### EXECUTION_FLOW
For EVERY profile:
- Agent trigger analysis: type, polling interval, file pattern, URL endpoint
- Full data-flow path: agent -> parser -> input tree -> filter chains -> output tree -> response unit(s)
- Filter-chain mapping for every output field with filters (full transformation chain)
- Conditional logic: `a == b ? c : d` ternaries, `a equals b` comparisons, `logical-and`/`logical-or`
- Variable flow: where each variable is written (`save variable`) and read (variable-type filter parameter)
- Calculation-node architecture: which calc fields feed which output fields
- Response-unit sequencing: execution order, dependencies between units, fan-out to multiple outputs, whether a failed response blocks later responses
- Profile-chain execution: the complete chain through `profile_after_run`, `forward_profile`, `exception_profile`, including inactive/missing targets
- Error paths: `exception_profile` chain, response-unit error handling, `treat_as_global_error`, retry policy, failure propagation to the caller/chained profile

### DATA_FLOWS
For EVERY output field with a filter chain (mandatory -- if a chain cannot be fully reconstructed, name the exact
field/profile/missing artifact and state the confidence impact):
- The full filter chain in order, never truncated
- Source fields referenced (`field` parameters, e.g. `S-companyName`)
- Constants used, decoded from base64 where the scanner allows it; write `undecoded scanner limitation` with
  profile/field/filter-index/raw-reference if a constant cannot be decoded
- Variables read/written; destination/output fields referenced by `destination` parameters
- HUMAN-READABLE explanation of the transformation logic
- Intermediate-result flow (`result` type references between chained filters)
- Profile-chain blast radius: downstream profiles/response units receiving this transformed field

Agent data sources: source type (FTP directory, HTTP endpoint, DB query), file pattern or URL, communication channel.
Response-unit outputs: target type (FTP upload, HTTP call, file write, email), target location, channel used.
Cross-profile flows: file-based handoff (profile A writes, profile B reads), `MSG_CALL` variable passing,
forward-profile data passing, `profile_after_run` success-chain payload assumptions, `exception_profile` error
payload assumptions, response fan-out where one parsed input produces multiple outputs.

### TRANSFORMATION_DETAIL
For EVERY profile with filter chains, produce a mandatory field-mapping table covering ALL output fields --
including direct copies, pass-through fields, and SQL-alias-only fields (a bare `copy(field:x)` or an SQL
`TRIM(ABAC08) AS IndustrySoldToCode` alias with no output-tree filter is NOT exempt):
| Source Field | Filter Chain Summary | Target Field | Explanation |
For chains with 3+ filters, trace step by step (`Step 1: copy(field:S-amount) -> result_1`, `Step 2:
find-substring(result_1, const:",") -> result_2`, ...); decode constants where possible, mark undecoded ones
explicitly. Document variable-flow diagrams (`field -> save variable X -> read by field Y`), conditional-logic
maps (condition / true-path / false-path / affected fields), and calculation-node architecture (calc fields ->
dependent output fields). Flag chains referencing missing source fields, missing variables, or inactive chained
profiles.

### BUSINESS_RULES
Extract EVERY business rule as BR-{GROUP}-NNN with: the filter chain or conditional implementing it (verbatim),
implementing profile + output field, inferred domain, and impact (violated/removed consequence).
Mine every source: conditional filters (`a == b ? c : d`), comparison filters (`a equals b`, `a > b`), logical
combinators (`logical-and`/`logical-or`), variable-driven branches, calculation-node logic, SQL in DB
agents/response units, profile-chaining conditions, parser-format assumptions (delimiter/record tag/XPath/
fixed-width position/EDI message type), response routing/channel selection, exception/forward/`profile_after_run`
chain behavior.
Parameter catalog (mandatory table): hardcoded value/decoded constant/path fragment/status code/partner-or-channel
name/threshold/date format/magic number | location | apparent purpose | staleness risk.
Conflict detection: inconsistent parsing, encoding, date formats, status mappings, null handling, response
routing, or channel use for the same business concept across profiles.

### SCHEMA_DICTIONARY
For EVERY source tree, output tree, database table, API payload, file structure, and response schema:
- Structure name (profile + source tree / output tree / response unit / table / API payload)
- Field/node path, name, data type, cardinality, nullable/required status if visible
- Profiles that READ it (agent/parser/source-tree/filter field refs) vs WRITE it (output-tree fields, response
  units, DB writes, HTTP payloads)
- Parser or response unit that owns the structure; connection/channel used to access or deliver it
- Mark source-only, output-only, calculated (`no_output`), and pass-through fields
- Flag source fields referenced by filters but absent from the source tree; flag output fields populated only by
  constants/defaults
- Compare field expectations across profile chains and response fan-out for schema-drift risk
- Use "unknown" rather than omitting a column/field when metadata is not present in the scan

### SOURCE_SQL
If no SQL queries exist in this group, write exactly: `None`
SQL appears in two places -- extract both with equal depth, never truncate:
- Top-level `source_sql` in `<dataproperties>` for `DB_Cron` profiles
- Node `sqltable` attributes on input-tree nodes for HTTP profiles doing inline DB lookups
- Also check for SQL stored as a filter constant: a `fieldconstant` value on a `<a type="constant">` element inside
  a filter chain (commonly a `concat` filter) -- the scanner promotes these to `inline_sql_{id}` params with
  `field_type=SQL`; treat identically to top-level SQL
For each query: profile/component/node path containing it; type (SELECT/INSERT/UPDATE/DELETE/DDL/stored-procedure
call); a full field-alias inventory for every `COLUMN AS alias` or `TRIM(x) AS alias` in the SELECT list (this is
the ground-truth source-to-API field mapping); tables and JOIN conditions including schema prefix (e.g.
`CRPDTA.F0101`); parameterized placeholders or filter/variable interpolation and injection risk; WHERE/JOIN/
CASE/HAVING clauses that encode business rules.

### EXTERNAL_SYSTEMS
For all external connections: system type (FTP server, REST API, database, email, AS2 partner, OFTP partner);
communication channel (partner name, channel type, channel ID); protocol (FTP, SFTP, OFTP2, HTTP/HTTPS, AS2,
SMTP, SAP-ALE, WebServices); which profiles use it and for what purpose (input/output); criticality (profile
count depending on it); failure surface (retry/timeout/error propagation if visible, profiles affected by a
channel outage).

### ERROR_HANDLING
Rate EACH profile: GOOD (has `exception_profile`, response units with retries and `treat_as_global_error`) /
ADEQUATE (`exception_profile` present but response units lack retry config) / POOR (no `exception_profile`, some
response units have `treat_as_global_error`) / NONE (neither).
Document: exception-profile chains; response-unit retry configuration; error-notification patterns; profile-chain
failure propagation; channel failure modes (FTP/HTTP/AS2/OFTP/DB/email) and whether failures block, retry, or
continue; inactive/missing exception/forward/`profile_after_run` targets; encoding/parser-failure handling and
whether bad files route to exception profiles.
Per-response-unit failure behavior: retry count/settings, `treat_as_global_error`, timeout, whether later response
units still execute, whether the failed payload remains recoverable.
Profile-chain blast radius: for every `profile_after_run`/`forward_profile`/`exception_profile`, document
callers/callees, inactive/missing targets, data-handoff assumptions, downstream profiles affected by failure.

### CUSTOM_CODE
If no custom scripts, custom Java/Groovy code, shell commands, database procedures, or embedded scripting hooks
exist in this group, write exactly: `None`
For ALL custom code/script-like components: full code or command text (never truncate); profile/component
containing it; purpose and data read/written; hidden business rules, constants, field mappings, or routing
decisions; external dependencies and communication channels; risk factors (command injection, SQL injection,
credential exposure, unvalidated input, encoding issues, resource leaks).

### ENVIRONMENT_CONFIG
For every user variable: name, type, default value, which filters read/write it, whether it varies by
environment. Also: communication-channel environment patterns (production vs test); file-path patterns that
differ by environment; encoding settings across profiles; `not_on_test` flags on response units; hardcoded
partner/channel/path/URL values that should vary by environment.

### SCHEDULING
Agent types: cron (FTP/DB/File/HTTP with interval), HTTP (on-demand), manual. Polling intervals (frequency,
human-readable interpretation). File-age settings (minimum age before processing). Profile-chain execution
dependency ordering. Active/inactive/suspended status per profile. Conflict detection: profiles polling the same
directory with overlapping file patterns.

### NAMING_PATTERNS
Profile naming: domain/system/process prefixes, source-target naming, suffixes for import/export/forward/error/
test variants. Group-path conventions (organized by domain, partner, channel, or process). Field naming: `S-`
source-field prefix, output-field conventions, calc/`no_output` naming, variable naming. Communication-channel
naming: partner:channel convention, environment suffixes, protocol suffixes. Response-unit naming/labels. Chained-
profile naming patterns (`profile_after_run`/`forward_profile`/`exception_profile`).
Rate: GOOD (clear, consistent, self-documenting) / ADEQUATE (mostly consistent, some outliers) / POOR
(inconsistent, unclear, or hides source/target/process meaning).

### CROSS_GROUP_REFS
`profile_after_run`/`exception_profile`/`forward_profile` references to profiles in other groups; shared
communication channels used across groups; shared file directories, HTTP endpoints, database tables, or partner
channels; shared variables or naming conventions implying cross-group coupling; one group writing a file/message/
table that another group reads; inactive or missing cross-group referenced profiles; execution-ordering
dependencies where group A must complete before group B.

### RISK_FLAGS
Rate each risk CRITICAL / HIGH / MEDIUM / LOW.

Security: OBF (obfuscated, NOT encrypted) passwords in agent settings; base64-encoded credentials in filter-chain
constants; HTTP endpoints without authentication (`auth_type=0`); API keys in filter constants; credentials or
tokens embedded in URL query parameters (e.g. `?access_token=...`, `?api_key=...` -- flag CRITICAL regardless of
whether the URL is in an agent, response unit, or `http-json-lookup` filter); file-based lookup references
(`./conf/...` paths -- document name, purpose, dependents; flag as risk if not version-controlled or undocumented
format).

Performance: unbounded `max_records` (`2147483647`); short polling intervals (< 5 min) on large directories;
repeated `http-json-lookup` calls without caching; large input trees with many optional nodes.

Reliability: missing `exception_profile` on production profiles; response units without retry configuration;
profile chains referencing inactive profiles; encoding mismatches across chained profiles; parser-format drift
without validation or quarantine path; channel outage with no retry, timeout, or alternate route.

Maintainability: filter chains longer than 8 filters without documentation; missing profile descriptions; flat
group organization; undocumented calculation nodes; system-variable abuse for business logic.

Platform notes to weigh into risk scoring: Lobster_data ships 250+ pre-defined filters (most deployments use only
20-30 of them) -- an unusually wide filter vocabulary in one profile is a maintainability flag; the platform's
native protocol set is (S)FTP(S), OFTP, OFTP2, SMTP, HTTP(S), SAP-ALE, AS2, and WebServices -- any channel using a
protocol outside this set is likely a custom/scripted integration and warrants CUSTOM_CODE + RISK_FLAGS scrutiny.

For each risk: severity | affected profile(s)/field(s) | specific description | recommended remediation.
