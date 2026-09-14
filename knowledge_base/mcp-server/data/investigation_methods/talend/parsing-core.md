---
applies to: Talend DI and Talend ESB
artifact format: Studio workspace export (filesystem) - a directory with `talend.project`, `process/`, and `.item`/`.properties` pairs. NOT verified against Cloud/TMC deployment artifacts.
verified against: Talend 8.x Studio workspace exports, 2 independent
evidence class: export-verified for serialisation; platform mechanics marked [documented] are vendor-doc only
last verified: 2026-07-27
---

# Talend Workspace Parsing Guide (Core)

Shared structures for both Talend Data Integration (DI) and Talend ESB workspaces.

**Companion guides:**
- [parsing-di-jobs.md](parsing-di-jobs.md) - Process job parsing (t\* components, DI call graph)
- [parsing-esb-routes.md](parsing-esb-routes.md) - ESB route & joblet parsing (c\* Camel components, ESB call graph)

> **Scope is a format boundary, not a version number.** Everything here applies to a Talend **Studio workspace**.
> A Talend Cloud/TMC deployment artifact is a built jar/zip with no `talend.project`, no `process/` and no
> `.item`/`.properties` pairs, so these guides are **inapplicable** to it, not merely inaccurate. **Check for
> `talend.project` before applying any of this.**
>
> Vendor documentation does not describe the `.item` XML serialisation at all - the Studio guides are UI-oriented.
> So the parsing mechanics here can only be corroborated by exports, never by documentation.

## Workspace Type Detection

| Indicator              | Workspace Type | Guides to Use                                |
| ---------------------- | -------------- | -------------------------------------------- |
| `process/` only        | Talend DI      | This + `parsing-di-jobs.md`                      |
| `process/` + `routes/` | Talend ESB     | This + `parsing-di-jobs.md` + `parsing-esb-routes.md` |

Additional ESB indicators: `joblets/`, `routelets/`, `services/`, `route_resources/` - but check they hold content,
not just placeholders. See "Project Directory Layout".

## Multi-Project (Referenced Library) Workspaces

A workspace export may contain more than one project directory - a main integration project plus one or more
**referenced projects** used as shared libraries (common routines/beans, logging frameworks). Indicators:

- Two or more sibling project dirs, each with its own `talend.project`.
- One project is code-heavy (routines/beans) but has few/no `process` or `routes` items.

Handling:
- Parse each project separately, then treat the code-heavy one as a shared library the main project depends on.
- Custom routines/beans referenced in the main project's components may be *defined in the referenced project* -
  resolve routine calls (`routines.X.method(...)`) across both `code/` trees.
- Expect duplication across projects (the same bean/routine may exist in both); flag as a maintainability item,
  not as two distinct capabilities.

**Second shape - separate workspace repositories.** The projects may not be siblings at all: each can be its own
workspace repository directory, with the relationship visible *only* in binding values of the form
`PROJECT_NAME:GUID`. Before analysing, scan the bindings for project-prefixed references and check whether every
named project is present. If one is missing, say so explicitly - the call graph is incomplete and any
"unresolvable reference" findings are artifacts of a partial export, not defects in the code.

### Declared references versus inferred ones

Both shapes above are **inferences** - one from directory layout, one from binding prefixes. The relationship is
also **declared**: a project reference is configured in the project properties, and the referencing project then
sees the referenced project's items read-only under an `@`-prefixed node. [documented]

Look for a declared reference list before inferring the set, and **state which you used**. Where the reference is
persisted on disk is not documented, so this is a search instruction rather than a path: check the project
descriptor and any settings tree the export carries.

Constraints that hold on a declared reference [documented]:

| Constraint | What it buys the parse |
|---|---|
| Referenced items are read-only in the referencing project | An edit to a shared routine happened in the owning project, not where it is used |
| No circular references | A cycle in the inferred project graph means the inference is wrong, not that the estate is |
| Two branches of one project cannot both be references | Two directories differing only by branch are not a reference pair |
| Item names cannot collide between a project and its reference - **except joblets** | See below. This one is a decisive test, not a caveat |

**Use the collision rule as a test.** Because non-joblet item names *cannot* collide between a project and its
reference, a duplicate non-joblet label across two project directories means those projects are **not** in a
reference relationship - they are independent projects exported together. That distinguishes the two shapes above
without relying on directory layout.

**Joblet labels are the exception, and resolving one across projects is ambiguous.** Joblets are exempt from the
collision rule, and a project setting - *use strict references to find joblets* - decides whether lookup is
restricted to the originating project or falls back to the others. [documented] So in a multi-project workspace a
joblet label can resolve in more than one place, and which one wins is a setting rather than a property of the
artifact. Before resolving a joblet by label (mechanism 4 below), check whether that label is unique across every
project present. Where it is not, record the ambiguity rather than taking the first match.

## Project Directory Layout

Enumerate the project's top-level directories and count files in each. Studio scaffolds directories whether or
not they are used, and a git-managed workspace commits the empty ones (often holding only a `.gitkeep`) - so
**presence proves nothing; file count does.** Report unknown directories rather than skipping them.

| Directory | Content | Analysis relevance |
|---|---|---|
| `process/` | DI jobs (`.item` + `.properties`) | Core |
| `routes/` | ESB routes | Core (ESB only) |
| `joblets/`, `routelets/` | Reusable embedded fragments | Core if non-empty - see "Joblet embedding" |
| `services/` | SOAP/REST service definitions | Core if non-empty |
| `code/` | Routines, beans, custom code | Cross-project resolution target |
| `context/` | Context (environment) definitions | Often excluded from exports for containing credentials |
| `data/` | Flat `key=value` context files (`.txt`, `.prn`, `.csv`) | Context values that are *not* in `context/` - see "Context values outside `context/`" [documented] |
| `metadata/` | Connection + schema definitions | Connections inventory, **and the join key for repository-bound components** - see below |
| `Structures/` | XML hierarchical structure definitions | Drives `tXMLMap` / SOAP payloads |
| `Maps/` | External `tXMLMap` / `tMap` definition files | Transformation logic held outside the job |
| `Namespace Containers/` | XML namespace definitions | Referenced by Structures/Maps |
| `sqlPatterns/` | SQL pattern templates | Usually Studio defaults - check before reporting |
| `lib/`, `TDQ_Libraries/` | Bundled JAR dependencies | Third-party surface; inflates file counts |
| `poms/` | Maven build descriptors | Build output, not design - exclude from artifact counts |
| `documentations/`, `Reports/`, `images/`, `Databases/` | Generated docs / profiling / diagram assets | Usually noise; exclude from counts |
| `temp/`, `.settings/`, `recycle_bin.index` | Studio working state | Exclude |

`recycle_bin.index` and `temp/` deserve a specific check: deleted-but-recoverable items can still be present on
disk and must not be counted as live artifacts.

**Any "size of the estate" figure must state what it counts.** In one export, `poms/`, `TDQ_Libraries/` and
`documentations/` accounted for over 1,000 files between them - none of which is a designed artifact.

Within a project, the per-artifact layout is a versioned `.item`/`.properties` pair:

```
process/FolderName/JobName_0.1/
├── JobName_0.1.item          # Job definition (XML)
└── JobName_0.1.properties    # Job metadata (holds the id= this GUID resolution depends on)
```

`metadata/` subdivides by connection and schema type (`connections/`, `json/`, `fileXml/`, `fileDelimited/`,
`genericSchema/`, `WSDLSchema/`, `JDBC/`, `tacokit/`, `validationRules/`, plus per-vendor directories). Enumerate
what is present rather than expecting a fixed set.

## Component Inventory: Enumerate, Then Classify

Do not rely on a list of expected components - Talend ships hundreds and any two workspaces overlap only
partially. Instead:

1. Enumerate every distinct value of `componentName="..."` across `process/`, `routes/` and `joblets/`, with counts.
2. Classify each against the decision-relevant table below.
3. **Report the unclassified ones with their counts.** A high-count unclassified component is a gap in the
   analysis, not a detail to omit.

Naming conventions: `t*` = DI job components, `c*` = ESB/Camel route components, anything else is a **joblet**
(its label is used directly as a component name - see "Joblet embedding") or a custom/user component.

Two independent exports of this platform shared only 46 of 123 distinct components, so a fixed list is
permanently incomplete and biases classification toward what it happens to name.

### Decision-relevant components

These change an assessment outcome, so their presence or absence must be stated explicitly.

| Concern | Components to look for | Why it matters |
|---|---|---|
| Error handling (route) | `cOnException`, `cErrorHandler`, `cDeadLetterChannel` [`cDeadLetterChannel` unverified - not observed in either export] | Route-level resilience; absence supports a low reliability score, presence must soften it |
| Error handling (job) | `tWarn`, `tDie`, `tLogCatcher`, `tStatCatcher`, `REJECT`/`ON_COMPONENT_ERROR` links | Same, DI side |
| Transformation | `tMap`, `tXMLMap`, `cMap`, external files under `Maps/` | Mapping complexity; drives lineage feasibility |
| Async / queueing | `cJMS`, `cMQConnectionFactory`, `cSEDA`, `cDirect`, `tMomInput`/`tMomOutput` | Decoupling, and the implicit dependency edges below |
| Sub-flow invocation | `tRunJob`, `cTalendJob`, joblet components | The call graph itself |
| External egress | `cREST`, `tREST`, `tRESTClient`, `tHttpRequest`, `tESBConsumer`, `cCXF` | Outbound surface, including unexpected public-internet calls |
| Context handling | `tContextLoad` | Whether runtime context override exists at all |
| Credentials / crypto | `tSetKeystore`, custom routines invoked for encryption | Security findings |

Every row above names the assessment dimension it serves. Do not add a row that cannot.

Two error-handling idioms have each been observed used exclusively, to the exclusion of the other: one workspace
used `cOnException` throughout, another `cErrorHandler` throughout. Check for both; finding neither is a real
finding.

For anything outside the table, consult the vendor component references rather than guessing:
- DI components: `help.qlik.com/talend/en-US/components/8.0/About`
- Route/mediation components: `help.qlik.com/talend/en-US/mediation-components/8.0/About`

## How Jobs Depend On / Call Each Other

There are four distinct call mechanisms. Only the first two are explicit "A references B" edges; the third is an
implicit async dependency you must infer; the fourth is embedding.

### 1. Job -> Job (`tRunJob`, in `process/`)

```xml
<!-- Parent_Job_0.3.item -->
<node componentName="tRunJob">
  <elementParameter field="TECHNICAL" name="PROCESS:PROCESS_TYPE_PROCESS" value="_21kFECDCEeqDv5Lj2c962Q"/>
  <elementParameter field="TECHNICAL" name="PROCESS:PROCESS_TYPE_VERSION" value="Latest"/>
  <elementParameter field="CHECK"     name="TRANSMIT_WHOLE_CONTEXT" value="true"/>
  <elementParameter field="TABLE"     name="CONTEXTPARAMS">
    <elementValue elementRef="PARAM_NAME_COLUMN"  value="entity_type"/>
    <elementValue elementRef="PARAM_VALUE_COLUMN" value="&quot;Asset&quot;"/>
  </elementParameter>
</node>
```

`PROCESS:PROCESS_TYPE_PROCESS` is a **GUID**, not `process:Name`. Resolve it (see "Resolving a reference" below).
Note the naming split: the `PROCESS_TYPE_*` parameters are prefixed with the binding key, while
`TRANSMIT_WHOLE_CONTEXT` and `CONTEXTPARAMS` are **bare**. `TRANSMIT_WHOLE_CONTEXT=true` means the child also
inherits the parent's entire context, not just the two `CONTEXTPARAMS` rows.

### 2. Route -> Job (`cTalendJob`, in `routes/`)

```xml
<!-- Some_Route_1.2.item -->
<node componentName="cTalendJob">
  <elementParameter field="TECHNICAL" name="SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS" value="_SRqRgAyzEeydttkwxkUBJQ"/>
  <elementParameter field="TEXT"      name="SELECTED_JOB_NAME:PROCESS_TYPE_LABEL" value="Child_Job"/>  <!-- CACHE - may be stale -->
  <elementParameter field="TECHNICAL" name="SELECTED_JOB_NAME:PROCESS_TYPE_CONTEXT" value="Default"/>
  <elementParameter field="TECHNICAL" name="SELECTED_JOB_NAME:PROCESS_TYPE_VERSION" value="Latest"/>
  <elementParameter field="CHECK"     name="USE_ROUTE_CONTEXT" value="true"/>
</node>
```

Same rule: `...:PROCESS_TYPE_PROCESS` is a GUID; the `..._LABEL` is a display cache and is often stale (do not build
the graph from it). `USE_ROUTE_CONTEXT=true` makes the job inherit the route's context at runtime, overriding the
design-time context selection.

**Job side of a route call** - a job invoked by a route receives/returns the message via bridge components:

```xml
<node componentName="tRouteInput"/>   <!-- entry: reads the Camel message body from the route -->
<node componentName="tRouteOutput"/>  <!-- exit:  returns the result to the route -->
```

So `Route --cTalendJob--> Job(tRouteInput ... tRouteOutput) --back--> Route` is one round trip.

### 3. Route -> Route (implicit, via a JMS queue) - INFER THIS EDGE

There is often no direct reference between two routes. One route **publishes** to a queue and another
**consumes** the same queue name. This is a real dependency but only visible by matching queue names:

```xml
<!-- Producer_Route: writes to the queue -->
<node componentName="cJMS"><elementParameter name="DESTINATION" value="&quot;order_updates&quot;"/></node>
<!-- Consumer_Route: reads the same queue -->
<node componentName="cJMS"><elementParameter name="DESTINATION" value="&quot;order_updates&quot;"/></node>
```

Build these edges by grouping `cJMS` nodes on their `DESTINATION` value: producer(s) -> queue -> consumer(s). The
broker itself is configured separately on a `cMQConnectionFactory` node - read it to identify the broker and to
tell environments apart. A `DESTINATION` that is an expression or context variable rather than a quoted literal
cannot be matched statically: record it as unresolvable rather than guessing.
(`cDirect` / `direct:` URIs work the same way but are **in-memory and intra-route** - not a cross-route edge.)

In one export 6 of 59 destinations were expressions rather than literals, so the unresolvable case is normal and
must be counted, not dropped.

### 4. Joblet / routelet embedding (job <- reusable fragment)

A joblet is not "called" - it is embedded, and its internal components run inline in the parent. Its **label
appears directly as a `componentName`** in the parent job:

```xml
<node componentName="My_Reusable_Fragment"/>   <!-- resolves to joblets/My_Reusable_Fragment_0.1.item -->
```

So any `componentName` that is neither `t*` nor `c*` is a joblet or a custom component - resolve it against
`joblets/` (or `routelets/` for routes) by label. Joblets have their own `.item`/`.properties` pair and their own
version. Inside a joblet, the boundary components are `INPUT` / `OUTPUT` / `TRIGGER_INPUT` / `TRIGGER_OUTPUT`.
A heavily-reused joblet is a real dependency hub: treat it as a node in the graph, not as a component.

**Label resolution is ambiguous across projects.** Unlike every other item type, joblet labels may repeat between
a project and one it references, and a project setting decides whether lookup falls back beyond the originating
project. In a multi-project workspace, check that the label is unique across every project present *before*
resolving it - see "Declared references versus inferred ones" above.

Routelets are **unverified**: one export contained an empty `routelets/` directory and no routelet has been
observed. Expect them to behave as joblets do, and verify rather than asserting it. Whether joblet references are
version-pinned the way job calls are has not been checked.

### Resolving a reference to a job

```
1. Collect the reference from PROCESS:PROCESS_TYPE_PROCESS / SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS.
2. If the value contains a colon, split it: "PROJECT_NAME:GUID". The GUID must be resolved in THAT project.
3. Grep the target project's *.properties files for:  id="<that GUID>"
4. The matching file's `label` + `version` is the real target (e.g. Child_Job / 1.0).
5. Read the sibling ...:PROCESS_TYPE_VERSION parameter. "Latest" means the highest version present; a specific
   value (e.g. "0.3") means the edge points at THAT version, which may not be the newest artifact on disk.
6. If no .properties matches, either the target was deleted (dangling reference - report it) or the named project
   is not part of this export (partial export - report that instead, it is not a code defect).
Never resolve via the *_LABEL / SELECTED_JOB_NAME text parameter: it is a cache and is frequently stale.
```

A pinned non-latest version is worth reporting as a maintainability observation: a caller held at an old version
while the callee has moved on.

> **Open item - version dedup.** Multiple versions of the same artifact coexist on disk (one export held 579
> versioned items for ~73 logical artifacts). Deduplicating to the latest version silently redirects every
> version-pinned edge. Deduplicate for *counting* if you must, but resolve edges on the full (project, GUID,
> version) key, and state which you did. The general dedup rule is not yet settled.

### Which artifacts nothing calls: the callee-to-caller diff

The extraction guide requires this diff once per estate, because a caller-to-callee walk cannot establish that an
artifact is invoked by **nobody**. On this platform the invocation parameters to enumerate are:

| Parameter | Component | Where |
|---|---|---|
| `PROCESS:PROCESS_TYPE_PROCESS` | `tRunJob` | `process/`, `joblets/` |
| `SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS` | `cTalendJob` | `routes/`, `routelets/` |

```
# every called-artifact reference in the export, both forms
Grep: (PROCESS|SELECTED_JOB_NAME):PROCESS_TYPE_PROCESS
```

Resolve each value to a real artifact using the six steps above - **GUIDs, not the label parameter**, which is the
whole reason this diff is not simply a name grep. Then subtract the resolved set from the artifact list. What
remains has no caller in this export.

Two things to keep separate when reporting the remainder:

- **An entry point is not a dead artifact.** A job with a scheduler trigger, an inbound endpoint or a broker
  consumer is *supposed* to have no caller. Classify the remainder against the trigger inventory before calling
  anything dead.
- **A missing project is not a missing caller.** Where the export is partial, the caller may simply be outside it -
  which is a statement about the export, and it must be recorded as one rather than as a finding about the code.

## Artifact Types & XML Root Elements

| Artifact | Location | XML Root Element | Detail |
|----------|----------|-----------------|--------|
| Process job | `process/*.item` | `talendfile:ProcessType` | See `parsing-di-jobs.md` |
| ESB route | `routes/*.item` | `talendfile:ProcessType` (with c\* components) | See `parsing-esb-routes.md` |
| Joblet | `joblets/*.item` | `model:JobletProcess` inside `xmi:XMI` | See `parsing-esb-routes.md` |
| Routelet | `routelets/*.item` | `talendfile:ProcessType` (route fragment) | See `parsing-esb-routes.md` |
| Context group | `context/*.item` | `talendfile:ContextType` (multiple per file) | Detailed below |
| Properties | `*/*.properties` | `TalendProperties:Property` | Detailed below |
| Project | `talend.project` | `TalendProperties:Project` | Detailed below |

## Repository-Bound Versus Built-In Declarations

A component's connection and its schema are each declared one of two ways: **built-in** (stated on the component)
or **repository** (bound to a centralized item under `metadata/`). [documented] The centralizable kinds are
numerous - relational and JDBC connections, SAP, cloud sources, the file formats, LDAP, FTP, PGP, WSDL, generic
schemas, validation and rule packages - which is why the directory table above says to enumerate `metadata/`
rather than expect a fixed set.

Three consequences for the parse:

**An inline `<metadata><column>` block is not evidence of where the schema was authored.** It may be a copy of a
centralized item. Reading every inline schema as a local declaration over-counts bespoke structures and
under-counts reuse.

**Where components bind, `metadata/` is a join across flows that the call graph does not show.** Two flows bound
to the same definition share a declared structure - a relationship neither job bindings nor shared-queue matching
produces. Treat a heavily-bound metadata item the way a heavily-reused joblet is treated: a node in the dependency
graph, not a detail.

**Count the split.** How many components declare inline versus bind to `metadata/` is a countable property of the
estate, and it bounds what any lineage tooling can do here - including the platform's own, which cannot see
built-in declarations at all (`platform-behaviour.md`). Report the ratio; do not convert it into a verdict.

### Finding the binding

**The binding attribute is not documented, and must not be guessed.** The vendor guides are UI-oriented and the
`.item` serialisation is described nowhere. A grep for an invented attribute name returns zero matches, and zero
matches reads as "the feature is absent" - the failure the provenance rules in `CONTRIBUTING.md` exist to prevent.

The shape to look for is visible in a construct documented below: a `contextParameter` carries
`repositoryContextId`, whose value is either `built-in` or a reference to a shared item. Look for an analogous
pairing on a component, and find its real name **by diff rather than by search** - take one component you can see
is repository-bound and one you can see is built-in, and compare their `elementParameter` sets. Record what you
found in the analysis notes; it is verified for that export only.

### Drift is possible, and it is a finding

Changing a centralized item raises an update prompt offering to propagate the change to the jobs that use it.
Propagation is **offered, not enforced** - it can be declined, or applied to a subset. [documented]

So a component's local copy of a centralized schema can legitimately differ from the item it was taken from. A
disagreement between the two is neither a parse error nor automatically a defect: it is **drift**, and a job
running against a stale contract is worth reporting as one. Do not silently prefer either side.

## Context Variables

Context items define environment-specific configuration. Each `.item` file contains multiple named contexts.

### XML Structure

```xml
<xmi:XMI xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd">
  <talendfile:ContextType name="Default" xmi:id="...">
    <contextParameter name="db_host" type="id_String" value="" repositoryContextId="built-in" />
    <contextParameter name="db_password" type="id_Password" value="enc:system.encryption.key.v1:..." />
  </talendfile:ContextType>
  <talendfile:ContextType name="PROD" xmi:id="...">
    <contextParameter name="db_host" type="id_String" value="<host>" />
  </talendfile:ContextType>
  <talendfile:ContextType name="TEST" xmi:id="...">
    ...
  </talendfile:ContextType>
</xmi:XMI>
```

### XPaths

| XPath | Returns |
|-------|---------|
| `//talendfile:ContextType[@name]` | All named contexts (Default, PROD, TEST, etc.) |
| `//contextParameter[@name]` | All context variables |
| `//contextParameter[@type='id_Password']` | Password/secret variables |

### Key Attributes

| Attribute | Description |
|-----------|-------------|
| `name` | Variable name (e.g., `connection_activemq_broker`) |
| `type` | Data type: `id_String`, `id_Password`, `id_Integer`, `id_Boolean`, `id_Date` |
| `value` | Value for this context environment |
| `repositoryContextId` | `built-in` or reference to shared context group |
| `prompt` | Prompt text shown when value required at runtime |
| `promptNeeded` | Whether runtime prompt is enabled |

### Password Encryption Detection

Values prefixed with `enc:system.encryption.key.v1:` are Talend-encrypted passwords. Flag these for security assessment - evaluate the *mechanism* (context variable vs vault), not the credential value.

### Environment Detection

Multiple `ContextType` elements with different `name` attributes indicate environment management. Common patterns: Default, DEV, TEST, PROD, TEST_LINUX, PROD_MAC.

### Context Values Outside `context/`

`tContextLoad` loads context values at runtime from an external source, so the values a flow actually runs with
need not be in `context/*.item` at all. One on-disk form is a directory of flat `key=value` files (`.txt`, `.prn`,
`.csv`), conventionally under `data/`, with the delimiter configurable. [documented]

Where `tContextLoad` is present, look for flat context files before recording a context value as `not in export`.
The distinction matters: "the export does not carry this value" and "the export carries it somewhere this guide
did not look" are different statements, and only the first belongs in the runtime-validation register.

## Properties File Parsing

Every artifact (job, route, joblet, context) has a companion `.properties` file containing metadata.

### XML Structure

```xml
<xmi:XMI xmlns:TalendProperties="http://www.talend.org/properties">
  <TalendProperties:Property
    id="_uniqueId"
    label="JobName"
    purpose="Description of the job"
    version="0.1"
    statusCode=""
    displayName="JobName">
    <additionalProperties key="created_date" value="2025-10-22T14:35:26.116+0200"/>
    <additionalProperties key="modified_date" value="2026-02-11T18:36:18.660+0100"/>
    <additionalProperties key="created_product_version" value="8.0.1.20231017_1026-patch"/>
  </TalendProperties:Property>
  <TalendProperties:ItemState path="FolderPath"/>
</xmi:XMI>
```

### XPaths

| XPath | Returns |
|-------|---------|
| `//TalendProperties:Property/@label` | Artifact display name |
| `//TalendProperties:Property/@version` | Version string |
| `//TalendProperties:Property/@purpose` | Description/purpose text |
| `//TalendProperties:Property/@statusCode` | Active/inactive status |
| `//additionalProperties[@key='created_date']/@value` | Creation timestamp |
| `//additionalProperties[@key='modified_date']/@value` | Last modified timestamp |
| `//TalendProperties:ItemState/@path` | Folder path in project tree |

### Use for Inventory

- `label` + `version` = canonical artifact name
- `purpose` = job description (often populated by developers)
- `statusCode` = empty string means active; check for inactive codes
- `path` = logical grouping (e.g., `Account/Out`, `Item/In`)

## talend.project File

The root project descriptor contains workspace-level metadata.

### XML Structure

```xml
<xmi:XMI xmlns:TalendProperties="http://www.talend.org/properties">
  <TalendProperties:Project
    label="project_name"
    technicalLabel="PROJECT_NAME"
    productVersion="Talend Cloud API Services Platform-8.0.1.20231017_1026-patch"
    language="java"
    type="DQ"
    description="Project description"/>
</xmi:XMI>
```

### XPaths

| XPath | Returns |
|-------|---------|
| `//TalendProperties:Project/@technicalLabel` | Project technical name |
| `//TalendProperties:Project/@productVersion` | Talend product + version |
| `//TalendProperties:Project/@type` | Product type (DI, DQ, etc.) |
| `//TalendProperties:Project/@language` | Language (java/perl) |

### ESB Detection

If `productVersion` contains "API Services Platform" or "ESB", or if `routes/` directory exists, this is an ESB workspace.

## Key Grep Patterns - Shared

Patterns applicable to all workspace types:

```
# All component types (any .item file)
componentName=

# Context variables
contextParameter.*name=

# Schema columns - a LOWER BOUND, not a count: a dynamic schema enumerates none
# of its columns. See "Dynamic schema" in parsing-di-jobs.md before quoting this
# number or using it as a coverage denominator.
<column.*name=

# Encrypted passwords
enc:system.encryption.key.v1

# Context environments
ContextType.*name=

# Repository-bound context parameters (contrast with the built-in value)
repositoryContextId=

# Properties metadata
TalendProperties:Property

# Joblet detection (ESB)
model:JobletProcess
```

For job-specific patterns, see `parsing-di-jobs.md` -> "Job-Specific Grep Patterns".
For route-specific patterns, see `parsing-esb-routes.md` -> "Route-Specific Grep Patterns".

## Filtering Inactive Items

Before building call graphs, exclude inactive artifacts from analysis. This applies to `process/`, `routes/`, and `joblets/` directories.

### Recycle Bin Detection

- Items in `process/recycle_bin/` or `process/_Trash/` directories are deleted - **skip entirely**
- Check `.properties` files for `statusCode` indicating deleted/inactive state
- Check `recycle_bin.index` at workspace root for deleted item references

### Copy/Backup Detection

- Items with `Copy_of_` prefix are typically user-created backups or test copies
- Flag as **inactive/backup** - list in inventory but exclude from main analysis
- Only include in main analysis if another active item's `tRunJob` or `cTalendJob` references them

### Disabled Components Inside a Live Artifact

The two rules above filter whole artifacts. A component **inside** a live job can also be switched off, and that is
a separate decision the parse has to make deliberately, because two defensible answers exist: a commercial lineage
scanner analyses disabled components along with the rest, on the grounds that a lineage catalogue documents declared
structure. [documented]

**This toolkit counts them and marks them, and excludes them from live-path claims.** The reason is the deliverable:
an assessment states what the estate *does*, and a disabled component does not run. So:

- **Enumerate disabled components and report the count**, per artifact. Never silently drop them - dead logic left
  in a live job is a maintainability finding, and it is invisible in a count that omitted it.
- **Exclude them from the adjacency, trigger and lineage claims.** A disabled write does not reach its target, and a
  disabled transformation breaks the chain through it: the hops on either side are not connected just because they
  are drawn adjacent. An edge that exists only through a disabled component is `none found`, not an edge.
- **Say which convention was applied** in the analysis notes. A reader comparing this inventory against a catalogue
  built by a lineage tool will find different counts, and the difference is a convention rather than an error.

**Find the flag by diff, not by search.** The attribute is not documented, like the rest of the serialisation. Take
one component you can see is switched off in Studio and one you can see is active, in the same export, and compare
their `elementParameter` sets. A grep for a guessed name returns zero matches, and zero matches here reads as
"nothing is disabled" - which is the answer that needs the most evidence, not the least.

### Grep Patterns

```
# Find recycle bin items
Glob: process/recycle_bin/**/*.item
Glob: process/_Trash/**/*.item

# Find copy/backup items (any directory)
Glob: **/Copy_of_*.item

# Verify if a Copy_of_ item is actually called
Grep: Copy_of_[ItemName] in all .item files (check tRunJob/cTalendJob references)
```

### Reporting

When inactive items are found, report them separately in the inventory:
- Count of recycled items (excluded from analysis)
- List of `Copy_of_` items with status (referenced by tRunJob/cTalendJob = active, not referenced = inactive)
- Total active vs inactive ratio per directory (process/, routes/, joblets/)
- Count of **disabled components inside live artifacts**, with the convention applied - see above

## Parsing Strategy Quick Reference

0. **Check `talend.project` exists.** If it does not, this is not a Studio workspace and this guide does not apply
   (a Cloud/TMC artifact is a built jar/zip) - say so rather than parsing it anyway
1. **Read `talend.project`** -> extract project name, product version, detect ESB vs DI
1b. **Enumerate project directories** -> a workspace may hold more than one project, in more than one shape; see
   "Multi-Project (Referenced Library) Workspaces"
2. **Check for `routes/` directory** -> if present, this is ESB; read `parsing-esb-routes.md`
3. **Always read `parsing-di-jobs.md`** for `process/` directory
4. **Parse `context/`** using this guide -> extract all context groups, environments, variables. Where
   `tContextLoad` is present, also look for flat context files - see "Context Values Outside `context/`"
5. **Parse `metadata/`** -> extract connection definitions and shared schemas, then determine which components
   bind to them and which declare inline - see "Repository-Bound Versus Built-In Declarations"
6. **Scan `.properties` files** -> build inventory with names, versions, descriptions, timestamps
7. **Filter inactive items** -> exclude recycle bin, flag Copy_of_ items, count disabled components inside live
   artifacts and keep them out of the live-path claims
8. **Build call graph(s)** per `parsing-di-jobs.md` (tRunJob) and `parsing-esb-routes.md` (cTalendJob)
9. **Merge graphs** if ESB workspace -> see `parsing-esb-routes.md` -> "Building the ESB Call Graph"

## Parsing Pitfalls

Mechanics that produce a wrong answer rather than a missing one. Each has been observed in both exports.

**Runtime and component behaviour is not here.** Dual-JVM architecture, component quirks and code constraints are
in `platform-behaviour.md`, which Phases 4 and 5 read. Nothing in that file affects parsing.

**Job Bindings Are GUIDs, Not Names**: `tRunJob` (`PROCESS:PROCESS_TYPE_PROCESS`) and `cTalendJob`
(`SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS`) store the target job as a Talend element **GUID** (e.g.
`_SRqRgAyzEeydttkwxkUBJQ`), not a `process:JobName` string. Resolve the GUID against the target artifact's
`.properties` `id=` attribute. The human-readable `SELECTED_JOB_NAME` label is a cache and is often **stale** -
never build the call graph from the label alone.

**Binding Parameter Naming Is Mixed**: on a job binding, only the `PROCESS_TYPE_*` family carries the binding key
as a prefix - `PROCESS:PROCESS_TYPE_PROCESS` / `SELECTED_JOB_NAME:PROCESS_TYPE_CONTEXT` / `...:PROCESS_TYPE_VERSION`.
The remaining parameters are **bare**: `CONTEXTPARAMS`, `TRANSMIT_WHOLE_CONTEXT`, `TRANSMIT_ORIGINAL_CONTEXT`,
`USE_ROUTE_CONTEXT`, `EXTERNAL_JAR_CONTEXTPARAMS`. Query both forms, or match on the suffix. A zero-result query
here means the wrong form was used, not that the parameter is absent - validate any query against one
known-positive instance before concluding anything from an empty result.

**Design-Time Context Pin**: bindings carry `...:PROCESS_TYPE_CONTEXT` to select which context the callee runs with.
`Default` is the neutral value; a named environment (`DEV`, `QA`, `ACC`) is a deliberate design-time pin. Before
grading it, check two things: `USE_ROUTE_CONTEXT=true` (routes) lets the caller's context win at runtime, and the
presence of `tContextLoad` shows whether runtime override exists at all. A named pin plus no `tContextLoad` plus
no route-context override is a genuine promotion-hygiene risk; a named pin with either override present is a
lower-severity hygiene observation.
