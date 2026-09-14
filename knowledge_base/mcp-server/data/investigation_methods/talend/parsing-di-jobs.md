---
applies to: Talend DI (process jobs, including DI jobs invoked by ESB routes)
artifact format: Studio workspace export (filesystem). NOT verified against Cloud/TMC artifacts.
verified against: Talend 8.x Studio workspace exports, 2 independent
evidence class: export-verified for serialisation; platform mechanics marked [documented] are vendor-doc only
last verified: 2026-07-27
---

# Talend Process Job Parsing Guide

Parsing guide for Talend process jobs — the standard data integration units using t\* components.

**Prerequisite:** Read [parsing-core.md](parsing-core.md) first for workspace structure, context variables, and properties file parsing.

**Applies to:** All `.item` files in `process/` directory.

## Job XML Structure

Process jobs use `talendfile:ProcessType` as the root element:

```xml
<talendfile:ProcessType
  xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd"
  defaultContext="Default"
  jobType="Standard">

  <context name="Default">
    <contextParameter name="var1" type="id_String" value="..." />
  </context>

  <node componentName="tRESTClient" offsetLabelX="0" offsetLabelY="0" posX="256" posY="160">
    <elementParameter field="TEXT" name="URL" value="&quot;https://api.example.com&quot;" />
    <metadata connector="FLOW" name="tRESTClient_1">
      <column comment="" key="false" name="body" nullable="true" type="id_Object" />
    </metadata>
  </node>

  <connection connectorName="FLOW" label="row1" source="tRESTClient_1" target="tMap_1" />

  <subjob>
    <elementParameter name="UNIQUE_NAME" value="tRESTClient_1" />
  </subjob>
</talendfile:ProcessType>
```

### Key Elements

| Element | Description |
|---------|-------------|
| `<node>` | A component instance with `componentName` attribute |
| `<elementParameter>` | Component configuration (inside `<node>`) |
| `<metadata>` / `<column>` | Schema definition (inside `<node>`) |
| `<connection>` | Data flow link between components |
| `<subjob>` | Sub-job grouping for execution control |
| `<context>` | Job-embedded context variables (may override shared contexts) |

## Process Job Component Types

**Enumerate first; this table classifies, it does not enumerate.** The procedure is in `parsing-core.md` ->
"Component Inventory: Enumerate, Then Classify": list every distinct `componentName` in `process/` with counts,
classify what you recognise against the tables below, and **report the unclassified ones with their counts**. Two
independent exports of this platform shared only 46 of 123 distinct components, 90 of them `t*`. A list of
expected components is permanently incomplete, and it biases classification toward whatever it happens to name.

Each row states **what the component changes in the parse**, which is the bar for adding one. What a component
does in the product is the vendor's documentation to state, not this file's.

### Systems and I/O

**Do not work from a list of connectors.** They are the largest and least stable group, one per system family,
and naming a handful implies the rest do not exist. For each I/O component the enumeration returned, record the
same three things regardless of brand: **the system it names, the direction, and whether its connection is
Built-In or Repository** (`parsing-core.md` -> "Repository-Bound Versus Built-In Declarations"). That triple is
what the inventory and the landscape consume.

The rows below are the ones whose *parse* differs from that default:

| Component | What it changes in the parse |
|-----------|------------------------------|
| `tDBInput` / `tDBOutput` | The query text is the lineage hop, and it may be assembled at runtime - see "Lineage Through Custom Code and Dynamic SQL". A partially interpolated statement is not the query |
| `tDBConnection` / `tDBClose` | The connection is declared once and used by components that do not name it. The node naming the system and the node reading from it are different nodes |
| `tFileList` | An iteration source: the subgraph downstream of it runs once per file, so one node is N executions and the volume is runtime-only |
| `tFileDelete`, and deletes against any store | A write that leaves no target schema. It is an egress edge with no field mapping, and a mapping-only parse drops it |

### API and messaging

| Component | What it changes in the parse |
|-----------|------------------------------|
| `tRESTClient`, `tSOAP`, `tHTTPRequest` | Outbound egress. Record the endpoint and whether it leaves the estate - an unexpected public-internet call is a finding, and the URL is frequently a context variable rather than a literal |
| `tREST`, `tRESTRequest` | The job is its own front door: an inbound HTTP endpoint with no route in front of it. Do not assume every entry point is in `routes/` |
| `tMomOutput` / `tMomInput` | Producer or consumer of a broker destination on the DI side. Same implicit-edge rule as `cJMS`: group on the destination name |
| `tRouteInput` / `tRouteOutput` | The job is invoked by a route. Present in a job no `cTalendJob` names, it is an unresolved caller rather than a job without one |

### Transformation

| Component | What it changes in the parse |
|-----------|------------------------------|
| `tMap` | The transformation logic is in the `.item` and is parseable. Its *configuration* decides whether a mapping is statically resolvable - see "What a `tMap`'s configuration decides" below |
| `tExtractJSONFields`, `tExtractXMLField` | A schema appears mid-flow that no upstream component declared. Fields after this point have provenance the connection metadata does not carry |
| `tWriteJSONField` | The target structure is assembled here, not declared. A target field can exist with no schema row anywhere |
| `tHMap` | Executes a Talend Data Mapper map. The mapping logic is **not in the job** - it is a separate artifact under `Maps/` (see the directory table in `parsing-core.md`), and the node names it rather than containing it |
| `tXMLMap` | Hierarchical map driven by the definitions under `Structures/` |

`tHMap` and `tXMLMap` are the two components in this table whose transformation logic lives outside the `.item`
being parsed. Record the map each one names, and treat the field-level mapping inside it as a separate parse with
its own coverage statement - not as an absence. Both sit in the class `profile.md` section 6 records as not
column-traceable from a job export; `tMap` does **not**.

### Control flow

| Component | What it changes in the parse |
|-----------|------------------------------|
| `tRunJob` | A job-to-job edge, and the target is a GUID - see "tRunJob Inter-Job Dependencies" |
| `tFixedFlowInput` | Seeds a flow from literals. Target fields downstream are `constant`, not `unresolvable` |
| `tFlowToIterate` / `tIterateToFlow` | The connection type changes, so a graph built only on FLOW links breaks here |
| `tUnite` | Several source flows converge on one target, so a target field has more than one provenance and both must be recorded |
| `tSetGlobalVar` | Writes a value read elsewhere by name. The conduit is outside the mapping, which grades the dependent mapping `Inferred` |
| `tContextLoad` | Runtime context override exists at all. Its presence changes how a design-time context pin is graded |
| `tPreJob` / `tPostJob` | Work that runs outside the main flow. A connection opened or a file staged here is invisible to a parse that follows the data path only |

### Code and logging

| Component | What it changes in the parse |
|-----------|------------------------------|
| `tJava` / `tJavaRow` / `tJavaFlex` | A lineage hop the parse cannot follow by structure - see "Lineage Through Custom Code and Dynamic SQL" |
| `tLogRow`, `tStatCatcher` | Observability, not flow. Do not count them as targets |
| `tLogCatcher` | An error path exists and is routed somewhere. Presence or absence is a reliability input |
| `tDie` / `tWarn` | Whether a failure stops the job or is recorded and passed over. The two produce very different blast radii from the same error |

## Schemas

### XPaths

| XPath | Returns |
|-------|---------|
| `//node/metadata/column` | Inline schema columns per component |
| `//column/@name` | Field name |
| `//column/@type` | Field type (`id_String`, `id_Integer`, `id_Date`, `id_Object`, etc.) |
| `//column/@nullable` | Whether field accepts null |
| `//column/@length` | Field length constraint |
| `//column/@key` | Primary key indicator |

### Dynamic schema: the columns are not in the file

[documented] A component configured with a **dynamic schema** does not enumerate its columns. The column set is
resolved at run time from the source, so the `.item` carries no `<column>` element for any of them.

Two consequences, and the first is the one that bites:

- **A `<column>` count is a lower bound, not a count.** There is no error, no empty element and nothing else in the
  file recording that an enumeration was skipped, so a job using dynamic schemas reports few or no columns and the
  figure looks complete. Any field-level coverage number derived from `<column>` nodes over-states its own
  completeness on such a job, silently.
- **`not enumerable (dynamic schema)` and `zero columns` are different findings.** Report the count of components
  whose schema could not be enumerated as its own figure, and subtract it from the denominator of any coverage
  statement rather than letting it read as a traced field.

**The serialisation is not documented**, like every other `.item` detail. The shape to look for is a declared type
that says the schema is dynamic instead of naming a concrete type - not the absent column set, which looks
identical to a component that genuinely has no schema. Find the real attribute value **by diff, not by search**:
take one component known to use a dynamic schema and one known not to, in the same export, and compare. A grep for
a guessed value returns zero matches, and zero matches reads as "no dynamic schemas here", which is the failure
`CONTRIBUTING.md`'s provenance rules exist to prevent.

### External Schemas

Also check shared schema definitions in the workspace:
- `metadata/json/` — JSON schema definitions
- `metadata/fileXml/` — XML schema definitions
- `metadata/salesforce/` — Salesforce object schemas
- `metadata/genericSchema/` — Reusable generic schemas

## Data Flow Connections

Connections define how data moves between components. Organized by Talend's 4 connection categories:

### Row Connections (data transfer)

| connectorName | Description |
|---------------|-------------|
| `FLOW` | Standard data flow between components |
| `MAIN` | Main data flow (alternate naming) |
| `REJECT` | Rejected/error records (e.g., from tMap filter) |
| `LOOKUP` | Lookup input to tMap |
| `OUTPUT_1` | Named output flow (from joblets) |
| `RESPONSE` | Response data from service calls |
| `FAULT` | Fault/error response from service calls |

### Iterate Connections (loops)

| connectorName | Description |
|---------------|-------------|
| `ITERATE` | Loop iteration (e.g., from tFlowToIterate, tFileList) |

### Trigger Connections (execution control)

| connectorName | Description |
|---------------|-------------|
| `SUBJOB_OK` | Execute after sub-job completes successfully |
| `SUBJOB_ERROR` | Execute after sub-job fails |
| `COMPONENT_OK` | Execute after component completes successfully |
| `COMPONENT_ERROR` | Execute after component fails |
| `RUN_IF` | Conditional execution (expression-based) |
| `TRIGGER_OUTPUT_1` | Trigger output from joblet |

### Link Connections (ELT)

| connectorName | Description |
|---------------|-------------|
| `LINK` | ELT push-down to database engine (rare — used when transformations execute in DB) |

### XPath

```
//connection[@connectorName]
```

Attributes: `source`, `target`, `connectorName`, `label`

Build flow graph: `source` → `target` for each connection. Use `connectorName` to classify the edge type.

## tMap Transformations

The `tMap` component contains the core transformation logic:

| XPath | Returns |
|-------|---------|
| `//node[@componentName='tMap']/nodeData/mapperTableEntries` | Mapping entries |
| Input tables | Source data from upstream components |
| Output tables | Target data to downstream components |
| `expression` attribute | Transformation formula per field |

Expressions use Java syntax — see Transformation Expression Syntax below.

### A Target Column With No Source Is Not an Unresolved Hop

An output column whose `expression` is a literal - a quoted string, a number, a generator call - has **no upstream
field**. So does a flow seeded by `tFixedFlowInput`. Record these as `constant` or `generated`.

Reserve `unresolvable` for a hop the parse could not follow: an expression referencing a variable set elsewhere, a
custom routine, a dynamically assembled query. Conflating the two makes an estate look less traceable than it is,
and it hides the opposite problem - a target field that is silently hardcoded is a finding in its own right, and
one recorded as "could not trace" gets filed as an analysis gap instead.

Lineage tooling generally omits constants from its graphs, so a flow fed only by constants can be absent from a
vendor lineage view while being plainly present in the code. [documented]

### What a `tMap`'s configuration decides

A `tMap` is not one construct producing "mappings". It is a component whose **configuration** decides whether a
mapping is statically resolvable at all, and the relevant options are a documented, closed set - so this is a
lookup, not a judgement. Extract, per `tMap`: the join type and match model per lookup, the load model per
lookup, the variable-table chain, input and output filters, reject outputs, and the three flags that change
semantics.

The option sets at 8.0 [documented]:

| Setting | Values | Default |
|---|---|---|
| Lookup **match model** | `Unique Match`, `First Match`, `All Matches` | `Unique Match`, and it means *the last* matching row, not the only one |
| Lookup **load model** | `Load Once`, `Reload at each row`, `Reload at each row (cache)` | `Load Once` |
| Join type | Inner Join, Left Outer Join | not stated in the source read - read it off the component |
| `Die on error` | on / off | on. With it off, `ERROR_MESSAGE` carries the failure instead |
| `Enable Auto-Conversion of types` | on / off | not stated in the source read |
| `Lookup in parallel` | on / off | not stated in the source read |

**What each does to the mapping's `Basis`.** Grade by construct class, per "Grading a field mapping" in
`extraction-guide.md` - these are the Talend constructs that select its rows, not a second grading scheme:

- **Any lookup is `Inferred`**, whatever its match model, because which row supplies the value is data-dependent.
  The match model does not change the grade; it changes what the provenance *set* means. Under `Unique Match` a
  mapping table reporting one source row is reporting the last of several, and nothing in the document says so.
- **`Reload at each row` is additionally a temporal-coupling candidate**: the lookup's contents can change within
  a single run, so two rows of the same execution can resolve the same key differently.
- **`Enable Auto-Conversion of types` is the implicit-coercion class.** It makes a type mismatch invisible, so a
  mapping table reports a clean type match where a coercion happens. Record the coercion; do not report the
  match.
- **`Die on error` off** moves error handling into the flow rather than ending it, which changes the blast radius
  a failure in this component produces.

**Do not report these as transformations.** `Store on disk`, `Temp data directory path`, `Max buffer size (nb of
rows)`, `Ignore trailing zeros for BigDecimal` and `Mapping links display` are execution and rendering settings
with no lineage meaning. Counting them inflates the transformation count with configuration noise.

**The attribute names for all of the above are not documented, and must not be guessed.** The vendor guides are
UI-oriented and the `.item` serialisation is described nowhere, so the option *set* above is solid and the
*parsing instruction* does not exist yet. A grep for an invented attribute name returns zero matches, and zero
matches reads as "the feature is absent" - the failure the provenance rules in `CONTRIBUTING.md` exist to
prevent. Find the names **by diff rather than by search**: take one `tMap` you can see has a lookup and one you
can see has none, compare their `elementParameter` and `nodeData` contents, and record what you found in the
analysis notes as verified for that export only. `profile.md` section 6 carries this as a declared gap.

`tXMLMap`'s Tree schema editor implies a different serialisation again, and the `tELTMap` family pushes the
transformation into the database where the mapping is SQL rather than Java. Neither has been examined; do not
assume the options above transfer to either.

## tRunJob Inter-Job Dependencies

`tRunJob` is the primary mechanism for orchestrating multi-job flows:

| XPath | Returns |
|-------|---------|
| `//node[@componentName='tRunJob']` | All tRunJob components |
| `elementParameter[@name='PROCESS:PROCESS_TYPE_PROCESS']` | Referenced child job **GUID**, optionally prefixed `PROJECT_NAME:` (resolve via `.properties` `id=`) |
| `elementParameter[@name='PROCESS:PROCESS_TYPE_VERSION']` | Version of the callee: `Latest`, or a pinned version (`0.3`) - part of the resolution key |
| `elementParameter[@name='TRANSMIT_WHOLE_CONTEXT']` | If `true`, the entire parent context is passed to the child (not just `CONTEXTPARAMS`). Note: **bare**, not prefixed |
| `elementParameter[@name='TRANSMIT_ORIGINAL_CONTEXT']` | Passes the original (pre-modification) context instead of the current one |
| `elementParameter[@name='CONTEXTPARAMS']` | Per-call parameter table. Note: **bare**, not prefixed |

This is the PRIMARY mechanism for tracing data journeys across process jobs.

**`TRANSMIT_WHOLE_CONTEXT=true` is the norm** (265 of 266 nodes in one export, 10 of 10 in a second). Assume the
child sees the **entire** parent context unless the flag says otherwise: reasoning only about the two enumerated
`CONTEXTPARAMS` rows understates what state the child can depend on, and therefore what counts as an implicit
coupling. Capture `TRANSMIT_ORIGINAL_CONTEXT` alongside it - it selects the pre-modification context, which matters
when a parent mutates context values before the call. Whether `true` is the Studio default or a convention both
teams followed is undetermined; either way, read the flag rather than assuming.

See `parsing-core.md` -> "How Jobs Depend On / Call Each Other" for the worked examples and the resolution procedure.

## Error Handling

| Mechanism | Description |
|-----------|-------------|
| `tLogCatcher` | Catches component errors, routes them for logging |
| `tStatCatcher` | Catches execution statistics per component |
| `tDie` | Stops job execution with error code and message |
| `tWarn` | Logs warning but continues execution |
| `REJECT` connection | Routes rejected records to error handling components |
| `COMPONENT_ERROR` trigger | Execute error handler after component failure |
| `SUBJOB_OK` / `SUBJOB_ERROR` trigger | Execution flow based on sub-job success/failure |
| `tPreJob` / `tPostJob` | Setup and cleanup logic (resource init/release) |

## Transformation Expression Syntax

Talend uses Java-based expressions in tMap and other components:

| Pattern | Meaning |
|---------|---------|
| `input_row.fieldName` | Access input field |
| `StringHandling.CHANGE(str, old, new)` | String replace |
| `TalendDate.parseDate("yyyy-MM-dd", dateStr)` | Date parsing |
| `Numeric.sequence("seq", 1, 1)` | Auto-increment |
| `row1.field != null ? row1.field : "default"` | Null handling |
| `context.VARIABLE_NAME` | Access context variable |
| `globalMap.get("key")` | Access global variable |
| `routines.CustomRoutine.method(args)` | Custom Java routine |
| `(String)globalMap.get("tRouteInput_1_BODY")` | Access ESB route message body |

## Lineage Through Custom Code and Dynamic SQL

Two component families defeat expression parsing. Both have a documented answer, and neither answer is "give up".

### `tJava` / `tJavaRow` / `tJavaFlex`

The mapping lives in hand-written Java, so there is no per-field `expression` attribute to read.

**Check for an embedded lineage convention before recording the hop as unresolvable.** External lineage tooling
reads a mapping specification out of the component's own comments, between marker lines, one assignment per line
with arithmetic, function calls and ternaries permitted: [documented]

```
*** lineage start ***
output_row.target_column = input_row.source_column;
output_row.other_column  = input_row.a + input_row.b;
*** lineage end ***
```

Where an estate has been through a cataloguing exercise the blocks may already be present, and they are then a
parseable lineage source in a component this guide otherwise treats as opaque. Grep for the marker before
concluding the hop is opaque.

Where the convention is absent, the hop is genuinely `unresolvable` and belongs in the runtime-validation
register. *Adopting* it is a cheap design recommendation rather than a parsing instruction, so it lives in
`platform-behaviour.md`.

### `tDBRow` / `tDBInput` / `tDBOutput`

The query text held on the component is not necessarily the statement that executes: it can be assembled at run
time from context variables and `globalMap` entries, and the schema can be discovered at run time rather than
declared. [documented]

Read the static text, then check whether it interpolates anything. Where it does, record the hop as
`unresolvable - dynamic` rather than reporting the literal text as the query; a partially-interpolated statement
presented as fact is worse than a declared gap. This is the specific case the platform's **runtime lineage
artifact** resolves - `profile.md` records what that artifact is, and why it is not part of a standard export.

## Building the DI Call Graph

0. **Build the id index once.** Grep every `.properties` file in every project for `id=`, `label` and `version`,
   and hold an `id -> (project, label, version)` map. Resolving each binding by grepping the tree per reference does
   not scale - one export carried 894 `.properties` files.
1. Glob all `.item` files in `process/`
2. Filter out inactive items (see `parsing-core.md` — Filtering Inactive Items)
3. For each job, extract job name from filename and find all `tRunJob` nodes
4. From each `tRunJob`, read `PROCESS:PROCESS_TYPE_PROCESS` - this is a **GUID**, not a name. Resolve it
   against the target job's `.properties` `id=`. Do not trust any human-readable label parameter (it may be stale).
   The value may carry a `PROJECT_NAME:` prefix; split on the first colon and resolve within that project. Read the
   sibling `PROCESS:PROCESS_TYPE_VERSION` - the edge points at **that** version, which may not be the newest
   artifact on disk
5. Extract context parameters being passed (`CONTEXTPARAMS`), and check `TRANSMIT_WHOLE_CONTEXT`
6. Build adjacency list: `{ parent_job: [(child_job, version, context_vars), ...] }`
7. Find entry points: jobs NOT called by any other job's tRunJob
8. Find terminals: jobs with no tRunJob components
9. Trace path from entry to terminal for complete data journey
10. **Report unresolved references separately**, distinguishing two causes: no `.properties` matches the GUID
    (a dangling reference - the target was deleted, report it as a finding), versus the named project is absent from
    the export (a partial export - the call graph is knowingly incomplete, which is not a code defect)

A joblet is a node in this graph, not a component - see `parsing-core.md` mechanism 4.

In ESB workspaces, merge this graph with the ESB call graph - see `parsing-esb-routes.md` -> "Building the ESB
Call Graph".

## Job-Specific Grep Patterns

**The first pattern is the search; the rest are filters.** Every named alternation below is a convenience for
components already met, and a convenience list is not a survey - anything it does not name is invisible to it.
Run the generic form first, count what comes back, then use the filters. The vendor-specific ones at the end are
there because those systems occurred in a workspace once, not because they are expected.

```
# THE SEARCH: every DI component in the export, with counts
componentName="t[A-Z]
```

```
# Filters over what the search returned
#
# tRunJob references (inter-job calls)
componentName.*tRunJob

# Database components
tDBInput|tDBOutput|tDB.*Connection

# Free-form SQL (check for runtime interpolation before quoting the query text)
tDBRow

# Embedded lineage annotation in custom code
lineage start

# API/HTTP components
tREST|tSOAP|tHTTP|tRESTClient

# Transformation components
componentName.*tMap

# Error handling components
tLogCatcher|tDie|tWarn|tStatCatcher

# Reject connections
connectorName="REJECT"

# Vendor connectors met in one workspace each - the enumeration finds the rest
tSalesforce
tSAP
tBigQuery

# JSON processing
tExtractJSONFields|tWriteJSONField

# ESB bridge components (in process jobs called by routes)
tRouteInput|tRouteOutput

# File operations
tFileList|tFileDelete

# Messaging
tMomOutput|tMomInput

# Trigger connections
SUBJOB_OK|SUBJOB_ERROR|COMPONENT_OK|COMPONENT_ERROR|RUN_IF
```

## Verify before use

> These are patterns observed in specific past workspaces, not universal. Verify each applies before relying on it;
> absence is common (e.g. no SAP/ProBI system present).

Observed in one workspace each. Any of them may be present, absent or differently shaped in the workspace you are
reading. Never treat an item here as an expected pattern - and if you search for one and do not find it, record
that in the inventory's "Patterns Checked and NOT Found" section rather than leaving it silent.

| Observation | Where it was seen | What to check |
|---|---|---|
| ProBI and OData encodings differ for the same logical SAP field | 1 workspace export | Whether both endpoint types are present at all, then compare field values from each |

### ProBI vs OData Encoding Differences

When Talend jobs interact with both ProBI and OData endpoints for the same SAP system, watch for encoding differences in field values. ProBI responses may use different character encoding or escaping than OData responses for the same logical field. Compare field values from both sources during data journey mapping and flag any encoding mismatches for the inventory.
