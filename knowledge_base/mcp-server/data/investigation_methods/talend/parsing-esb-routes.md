---
applies to: Talend ESB (Camel routes, joblets, routelets)
artifact format: Studio workspace export (filesystem). NOT verified against Cloud/TMC artifacts.
verified against: Talend 8.x Studio workspace exports, 2 independent. Routelet mechanics are UNVERIFIED - no routelet has been observed.
evidence class: export-verified for serialisation; platform mechanics marked [documented] are vendor-doc only
last verified: 2026-07-27
---

# Talend ESB Route & Joblet Parsing Guide

Parsing guide for Talend ESB routes (Camel-based, c\* components), joblets, and routelets.

**Prerequisite:** Read [parsing-core.md](parsing-core.md) first for workspace structure, context variables, and properties file parsing.

**Applies to:**
- `.item` files in `routes/` — ESB route definitions
- `.item` files in `joblets/` — Reusable job fragments
- `.item` files in `routelets/` — Reusable route fragments

**Only present in ESB workspaces** — detected by the existence of a `routes/` directory or `productVersion` containing "API Services Platform".

## Route XML Structure

ESB routes share the same root element as process jobs (`talendfile:ProcessType`) but use c\* Camel components instead of t\* components:

```xml
<talendfile:ProcessType
  xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd"
  defaultContext="Default"
  jobType="Standard">

  <context name="Default">
    <contextParameter name="connection_activemq_broker" type="id_String" value="" />
  </context>
  <context name="TEST">
    <contextParameter name="connection_activemq_broker" type="id_String" value="localhost" />
  </context>

  <node componentName="cREST" posX="64" posY="160">
    <elementParameter name="REST_ENDPOINT" value="&quot;/api/webhook&quot;" />
  </node>

  <node componentName="cTalendJob" posX="320" posY="160">
    <!-- The value is a Talend element GUID, never "process:JobName" -->
    <elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS"
      value="_SRqRgAyzEeydttkwxkUBJQ" />
  </node>

  <connection connectorName="ROUTE" source="cREST_1" target="cTalendJob_1" />
</talendfile:ProcessType>
```

### Key Differences from Process Jobs

| Aspect | Process Job | ESB Route |
|--------|------------|-----------|
| Component prefix | `t*` (tMap, tRunJob) | `c*` (cREST, cTalendJob) |
| Connection type | FLOW, REJECT, ITERATE | ROUTE, ROUTE_WHEN, SPLIT |
| Calls other jobs via | `tRunJob` | `cTalendJob` |
| Entry point | tRunJob from parent or standalone | cREST endpoint, cJMS consumer |
| Error routing | REJECT, tLogCatcher | FAULT, route-level error handler |

## ESB Route Component Types

**Enumerate first; this table classifies, it does not enumerate.** The procedure is in `parsing-core.md` ->
"Component Inventory: Enumerate, Then Classify": list every distinct `componentName` in `routes/` with counts,
classify what you recognise against the table below, and **report the unclassified ones with their counts**. Two
independent exports of this platform shared only 46 of 123 distinct components, and the six `c*` components
carrying the most weight in one of them appear in the other zero times. A list of expected components is
permanently incomplete, and it biases classification toward whatever it happens to name.

Each row states **what the component changes in the parse**. That is the bar for adding one: a component that
changes nothing about how the route is read needs no row however common it is, and one that changes an edge, a
boundary or a grade needs one however rare. The component roles themselves are vendor-documented Camel
semantics [documented]; the counts above are export-observed.

| Component | What it changes in the parse |
|-----------|------------------------------|
| `cREST` | An inbound HTTP front door. Record the endpoint path and which front-door pattern it is: handoff to a broker, in-process invocation, or the deployable's own endpoint |
| `cJMS` | Producer or consumer of a named destination, and the route-to-route edge this platform never states explicitly. Group `cJMS` nodes on `DESTINATION` to build it |
| `cMQConnectionFactory` | Names the broker the destinations resolve against. Without it a queue name is not addressable to a system |
| `cMessagingEndpoint` | A Camel URI outside the palette. The URI scheme decides whether it is an edge - read the URI, do not classify the component |
| `cTalendJob` | A route-to-job call. The target is a GUID, not a name; see "Route-to-Job Bindings" below |
| `cDirect` | In-memory endpoint, no network and no broker. Not the same edge class as `cJMS` - see `parsing-core.md` -> "How Jobs Depend On / Call Each Other" |
| `cSEDA` | Staged async queue, in-memory rather than broker-backed. A dependency through it is a same-JVM one, which is a different reliability argument from a brokered handoff |
| `cMessageRouter` | Branches on message content. Each branch is a `ROUTE_WHEN` connection with its own predicate, and the predicate is frequently not resolvable from the export |
| `cRecipientList` | Targets are computed from the message at runtime, so its outbound edges are **not statically resolvable**. Record the parse limit; an edge missing here is not an absence |
| `cSplitter` | One `SPLIT` connection per part. Fan-out, not a branch |
| `cWireTap` | A copy to a second path. Not a branch and not a failure boundary - read the target before classifying it |
| `cDelayer` | Pacing or backoff. A reliability signal, and the mechanism a redelivery pattern is built from |
| `cMap` | Route-level mapping: the ESB-side transformation hop. A parse that reads only `tMap` misses every mapping performed in a route |
| `cProcessor` | Custom Java on the message - a lineage hop whose logic is not in the route. See "Lineage Through Custom Code and Dynamic SQL" in `parsing-di-jobs.md` |
| `cSetBody`, `cSetHeader`, `cConvertBodyTo` | Rewrite body or headers. A field's provenance can change here with no mapping component present anywhere |
| `cOnException`, `cErrorHandler` | Route-level resilience, and their absence is a scoring input. One workspace used `cOnException` throughout and another `cErrorHandler` throughout, so finding neither is a real finding rather than a failed search |
| `cBeanRegister` | Registers a Java bean into the Camel registry. The route then calls it by name, so the call site and the definition are in different files - and in a multi-project workspace, possibly different projects |
| `cConfig` | Route-level configuration. Read it for what it declares about the runtime, not as a step in the flow |
| `cLog` | Logging. Evidence that the route is observable; not an edge |

## Route Connection Types

Routes use a different set of connection types than process jobs:

| connectorName | Description |
|---------------|-------------|
| `ROUTE` | Standard flow between Camel components |
| `ROUTE_WHEN` | Conditional branch from `cMessageRouter` (expression-based) |
| `ROUTE_OTHER` | Default/fallback branch from `cMessageRouter` (else case) |
| `ROUTE_ENDBLOCK` | End of a routing block (closes a branch) |
| `SPLIT` | Output from `cSplitter` (one per split part) |
| `RESPONSE` | Response path from `cREST` endpoint back to caller |
| `FAULT` | Error/fault path for exception handling |

### XPath

```
//connection[@connectorName]
```

Build route graph: `source` → `target` for each connection. Use `connectorName` to identify conditional branches (ROUTE_WHEN) vs default flow (ROUTE).

## Route-to-Job Bindings — cTalendJob

The `cTalendJob` component is the bridge between ESB routes and process jobs. It invokes a process job from within a route.

### XPath

| XPath | Returns |
|-------|---------|
| `//node[@componentName='cTalendJob']` | All cTalendJob components |
| `elementParameter[@name='SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS']` | Referenced process job **GUID**, optionally prefixed `PROJECT_NAME:` |
| `elementParameter[@name='SELECTED_JOB_NAME:PROCESS_TYPE_VERSION']` | Version of the callee: `Latest` or a pinned version - part of the resolution key |
| `elementParameter[@name='SELECTED_JOB_NAME:PROCESS_TYPE_CONTEXT']` | Design-time context pin. `Default` is neutral; a named environment is deliberate |
| `elementParameter[@name='SELECTED_JOB_NAME:PROCESS_TYPE_LABEL']` | Display cache. **Frequently stale - never build the graph from it** |
| `elementParameter[@name='CONTEXTPARAMS']` | Context variables passed to the job. Note: **bare**, not prefixed |
| `elementParameter[@name='USE_ROUTE_CONTEXT']` | Whether job inherits route context. Note: **bare**, not prefixed |

Only the `PROCESS_TYPE_*` family carries the `SELECTED_JOB_NAME:` prefix. Querying the others with a prefix returns
zero matches, which reads as "the parameter is absent" - see `parsing-core.md` -> "Parsing Pitfalls".

### XML Example

```xml
<node componentName="cTalendJob" posX="320" posY="160">
  <!-- GUID, not "process:JobName" -->
  <elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS"
    value="_SRqRgAyzEeydttkwxkUBJQ" />
  <elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_LABEL" value="Child_Job" />  <!-- CACHE - may be stale -->
  <elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_VERSION" value="Latest" />
  <elementParameter name="SELECTED_JOB_NAME:PROCESS_TYPE_CONTEXT" value="Default" />
  <elementParameter name="USE_ROUTE_CONTEXT" value="true" />
  <elementParameter name="CONTEXTPARAMS">
    <elementValue elementRef="PARAM_NAME_COLUMN" value="entity_type" />
    <elementValue elementRef="PARAM_VALUE_COLUMN" value="&quot;Account&quot;" />
  </elementParameter>
</node>
```

### Extraction Steps

1. Find all `cTalendJob` nodes in route `.item` files
2. Read `SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS` - this is a **GUID**, not `process:Name`. Resolve it
   against the target job's `.properties` `id=`. The plain-text `SELECTED_JOB_NAME` label may be stale - do not use
   it as the graph node
3. If the value contains a colon, split it: `PROJECT_NAME:GUID`. Resolve the GUID **in that project**; if the named
   project is absent from the export, report a partial export rather than a dangling reference
4. Read `SELECTED_JOB_NAME:PROCESS_TYPE_VERSION` - `Latest` means the highest version present, a specific value
   means that version, which may not be the newest artifact on disk
5. Extract `CONTEXTPARAMS` → context variables passed to the job
6. Check `USE_ROUTE_CONTEXT` → if `true`, job inherits all route context variables, and the design-time context pin
   is inert for that call
7. Read `SELECTED_JOB_NAME:PROCESS_TYPE_CONTEXT`. `Default` is the neutral value; a named environment (`DEV`, `QA`,
   `ACC`) is a deliberate design-time pin and a promotion-hygiene signal. Grade it together with
   `USE_ROUTE_CONTEXT` and the presence of `tContextLoad` - see `parsing-core.md` -> "Parsing Pitfalls"

### ESB Bridge in Process Jobs

The bridge pair on the job side is `tRouteInput` (receives the message body from the route) and `tRouteOutput`
(returns the result to it). They are the entry and exit points of a route-invoked job, so a job holding either is
reachable from a route whether or not you have found the caller yet. Look for them in jobs referenced by
`cTalendJob`, and treat a `tRouteInput` in a job **no** `cTalendJob` names as an unresolved caller, not as a job
without one.

## Joblet Structure

Joblets are reusable job fragments. They use a different XML root element than process jobs.

### XML Root

```xml
<xmi:XMI xmlns:model="http://www.talend.com/joblet.ecore"
          xmlns:TalendProperties="http://www.talend.org/properties">
  <model:JobletProcess defaultContext="Default" jobType="Standard">
    <context name="PROD">
      <contextParameter name="connection_activemq_broker" type="id_String" value="localhost" />
      <contextParameter name="connection_activemq_password" type="id_Password"
        value="enc:system.encryption.key.v1:..." />
    </context>

    <!-- Components (same t* components as process jobs) -->
    <node componentName="tRESTClient" .../>
    <node componentName="tJavaRow" .../>

    <!-- Connections (same types as process jobs) -->
    <connection connectorName="FLOW" source="..." target="..." />
  </model:JobletProcess>
</xmi:XMI>
```

### Key Differences from Process Jobs

| Aspect | Process Job | Joblet |
|--------|------------|--------|
| Root element | `talendfile:ProcessType` | `model:JobletProcess` inside `xmi:XMI` |
| Namespace | `talendfile` | `model` (http://www.talend.com/joblet.ecore) |
| Placeholders | None | INPUT, OUTPUT, TRIGGER_INPUT, TRIGGER_OUTPUT |
| Standalone execution | Yes | No — must be embedded in a process job |

### Placeholder Components

Joblets define INPUT/OUTPUT/TRIGGER_INPUT/TRIGGER_OUTPUT placeholder components that map to the embedding job's data flow:

| Placeholder | Purpose |
|-------------|---------|
| `INPUT` | Receives data from the parent job's upstream component |
| `OUTPUT` | Sends data back to the parent job's downstream component |
| `TRIGGER_INPUT` | Receives trigger signals (SUBJOB_OK, etc.) |
| `TRIGGER_OUTPUT` / `TRIGGER_OUTPUT_1` | Sends trigger signals to parent job |

### How Joblets Appear When Referenced

When a process job uses a joblet, the joblet name appears as a `componentName` in the parent job's XML. The joblet's internal components are embedded transparently.

### Connection Types in Joblets

Joblets use the same connection types as process jobs: FLOW, ITERATE, COMPONENT_OK, COMPONENT_ERROR, SUBJOB_OK.

### Extraction

```
Glob: joblets/**/*.item
```

Parse each as `model:JobletProcess`. Extract components, connections, and context variables using the same patterns as process jobs (see `parsing-di-jobs.md`).

## Routelet Structure

Routelets are reusable route fragments — the route equivalent of joblets.

### Key Characteristics

- Located in `routelets/` directory
- Same XML root as routes: `talendfile:ProcessType` with c\* Camel components
- Designed for reuse — embedded in other routes
- Cannot be connected to other components externally (self-contained fragment)
- May be empty in some workspaces

### Extraction

```
Glob: routelets/**/*.item
```

Parse as route fragment using the same c\* component patterns described in this guide.

## Building the ESB Call Graph

The ESB call graph connects routes → jobs → routes, extending the DI call graph from `parsing-di-jobs.md`.

### Algorithm

0. **Enumerate before you traverse:** run the component inventory from `parsing-core.md` -> "Component Inventory:
   Enumerate, Then Classify" over `routes/` first. The steps below name the components that carry an edge in the
   exports read so far; the enumeration is what tells you whether this export uses others. An endpoint component
   nobody anticipated is an edge nobody drew
1. **Parse routes:** Glob `routes/**/*.item`, find all `cTalendJob` nodes
2. **Build route-to-job edges:** Extract `SELECTED_JOB_NAME` → create `route → job` adjacency
3. **Build route-to-route edges:** Group `cJMS` nodes on `DESTINATION` → producer route → queue → consumer route.
   In-memory endpoints (`cDirect`, `cSEDA`) are a different edge class: record them, and say which class each edge
   is, because a same-JVM hop and a brokered hop carry different reliability arguments
4. **Identify route entry points:** Routes carrying an inbound endpoint. `cREST` and `cJMS` consumers are the two
   found so far; any component from step 0 that opens a listener is one too, and a DI job with `tRESTRequest` is a
   front door with no route in front of it at all
5. **Merge with DI call graph:** Combine with `tRunJob` adjacency from `parsing-di-jobs.md`
6. **Trace complete chains:**
   ```
   Route[cREST] → Route[cTalendJob] → Job[tRouteInput...tRouteOutput] → Route
                                        ↓ (via tRunJob)
                                        Job → Job → Job
   ```
7. **Identify full entry-to-exit paths:** From route entry (cREST/cJMS) through all job chains to terminal jobs

### Merged Graph Format

```
{
  route_to_job: { "RouteName": [("JobName", context_vars), ...] },
  route_to_route: { "RouteName": ["OtherRouteName", ...] },
  job_to_job: { "JobName": [("ChildJobName", context_vars), ...] },
  entry_points: ["RouteName_with_cREST", "RouteName_with_cJMS"],
  terminals: ["JobName_with_no_children"]
}
```

## Route-Specific Grep Patterns

**The first pattern is the search; the rest are filters.** Every named alternation below is a convenience for
components already met, and a convenience list is not a survey - anything it does not name is invisible to it.
Run the generic form first, count what comes back, then use the filters.

```
# THE SEARCH: every Camel component in the export, with counts
componentName="c[A-Z]
```

```
# Filters over what the search returned
#
# Route-to-job bindings
cTalendJob|SELECTED_JOB_NAME

# Inbound endpoints seen so far - not the definitive set, see step 0 of the algorithm above
componentName.*cREST|componentName.*cJMS

# Route connections
connectorName="ROUTE"|connectorName="ROUTE_WHEN"

# Routing and fan-out components seen so far
cMessageRouter|cSplitter|cWireTap|cRecipientList

# Generic Camel component wrappers - read the URI, it decides whether this is an edge
cMessagingEndpoint

# In-memory endpoints (not brokered edges)
componentName.*cDirect|componentName.*cSEDA

# Route-level error handling - finding neither is a finding
cOnException|cErrorHandler|cDeadLetterChannel

# Joblet definitions
model:JobletProcess

# Joblet references in process jobs
componentName="j[A-Z]
```

## Verify before use

> These are patterns from specific past workspaces, not universal. The same component supports readings this list
> does not cover: a `cWireTap` may be an async-fork or monitoring tap rather than failure-isolation, and a
> two-`cMessageRouter` chain may be an auth gate followed by a content router rather than an A-AND-B
> synchronization. Both alternatives have been observed. Verify intent before classifying; do not assume the
> listed reading is the likely one.

Observed in one workspace each. Where you search for one of these and do not find it, record that in the
inventory's "Patterns Checked and NOT Found" section - a checked absence is a result; silence is not.

| Observation | Where it was seen | What to check |
|---|---|---|
| `cWireTap` used to isolate an intentional-failure probe path | 1 workspace export | What the tap's target does. An async fork for monitoring looks identical and is not failure isolation |
| A two-`cMessageRouter` chain acting as an A-AND-B synchronization gate | 1 workspace export | What each condition tests. An auth gate followed by a content router has the same shape |

### wireTap for Intentional-Failure Isolation

`cWireTap` can be used to isolate intentional-failure paths from the main route. When a route needs to test or probe an endpoint that may fail (e.g., checking if a resource exists before creating), wireTap sends a copy of the message to the probe path without affecting the main flow. If the probe path fails, the main route continues uninterrupted. Look for this pattern when mapping error handling - the wireTap failure is by design, not a gap.

### Two-Condition Sync Gates

ESB routes may implement synchronization gates using two `cMessageRouter` conditions in sequence - one checking for completion of process A and another for process B. Both must be satisfied before the route continues. This pattern appears as two consecutive ROUTE_WHEN connections with AND semantics. When building the call graph, note these as synchronization points rather than simple conditional branches.
