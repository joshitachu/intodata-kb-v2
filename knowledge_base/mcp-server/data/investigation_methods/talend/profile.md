---
applies to: Talend DI and Talend ESB
artifact format: Studio workspace export (filesystem) - a directory with `talend.project`, `process/`, and `.item`/`.properties` pairs. NOT verified against Cloud/TMC deployment artifacts.
verified against: Talend 8.x Studio workspace exports, 2 independent
evidence class: export-verified for artifact structure; platform facilities and the control-plane model are vendor-documented
last verified: 2026-07-27
---

# Talend platform profile

What the rest of the toolkit needs to know *about* Talend. Parsing mechanics are in `parsing-core.md`,
`parsing-di-jobs.md` and `parsing-esb-routes.md`; runtime and component behaviour is in `platform-behaviour.md`.

## 1. Identity and artifact formats

| Field | Value |
|---|---|
| Platform name | Talend |
| Aliases / product names in scope | Talend Data Integration (DI), Talend ESB, Talend Cloud API Services Platform, Talend Studio |
| Vendor documentation | `help.qlik.com/talend/en-US/components/8.0/About` (DI components), `help.qlik.com/talend/en-US/mediation-components/8.0/About` (route/mediation components) |
| **Acquisition** | **File export** - a Studio workspace directory. Schedule and deployment state are the exception and live in the control plane, obtainable only as an API extract, which is why several rows in section 3 are `Runtime-only` |

**Artifact formats.**

| Format | In scope | How to recognise it | Note |
|---|---|---|---|
| Studio workspace export (filesystem) | **yes** | `talend.project` at the project root, plus `process/` and `.item`/`.properties` pairs | The only format the parsing guides cover |
| Git-managed Studio workspace | **yes** | as above, plus committed empty directory scaffolding (often `.gitkeep`) | Same parsers. Directory *presence* proves nothing here; file count does |
| Cloud / TMC deployment artifact | **no** | a built jar or zip; no `talend.project`, no `process/`, no `.item` pairs | Documented as a packaged Job - jar, execution scripts, log4j configuration. The parsing guides are **inapplicable**, not merely inaccurate. Say so rather than parsing it anyway |
| TMC control-plane API extract | **not assessed** | | Schedules and deployment state live here, which is why cadence is `Runtime-only` below. Section 6 names the object model and what to ask for |
| Runtime lineage log (JSON) | **not assessed** | `<path>/<project>/<job>/<version>/runtime_log_<timestamp>.json`, produced only when the job was run with runtime lineage enabled | A **companion** to a workspace export, never a substitute - it carries no design structure. Documented to hold job name, component name, schema and query, which is what resolves a dynamically assembled statement. Never seen by this toolkit |

**Check `talend.project` before applying any Talend parsing guide.** This is a format gate, not a version check.

Vendor documentation does not describe the `.item` XML serialisation at all - the Studio guides are UI-oriented.
So parsing mechanics here can only be corroborated by a second export, never by documentation. That constraint
sets the corroboration path for every claim in the parsing guides.

## 2. Detection signals

| Signal | Strength | Note |
|---|---|---|
| `talend.project` at a project root | **decisive** | The format gate. Absent means this is not a Studio workspace |
| `.item` / `.properties` file pairs | corroborating | Strong together with `talend.project`, meaningless alone |
| `process/` directory holding `.item` files | corroborating | Present in DI and ESB alike |
| `routes/` directory holding `.item` files | decisive **for ESB vs DI** | Distinguishes the two rows, does not identify the platform |
| `productVersion` containing "API Services Platform" or "ESB" | corroborating for ESB | Read from `talend.project` |

## 3. Signal availability

Row set and availability classes are fixed by `method/extraction-guide.md`.

| Landscape field | Availability | Read from / why not |
|---|---|---|
| System identity (real system) | Available | Endpoint host, driver or auth pattern, and payload vocabulary are separately declared, so several independent classes exist. Label drift is real here: connection and job labels frequently name a system the flow does not actually reach |
| Observed direction | Available | Component type states read vs write |
| Role (SoR / consumer) | **Not code-recoverable** | A domain fact. Register by default |
| Entities / data owned | Available | Queries, file paths, payload vocabulary |
| Protocol | Available | The component type states it (`tDBInput` vs `tRESTClient` vs `cJMS`) |
| Format | Available | Inline `<metadata>` schemas, the `Structures/` definitions, or a centralized `metadata/` item the component binds to rather than declaring inline. The two are not distinguishable without the check in `parsing-core.md`, and an inline schema may be a copy of a centralized one |
| Auth mechanism | Available | Component `elementParameter` attributes |
| Credential storage | Available | Distinguishable: `enc:system.encryption.key.v1:` prefix, plain context variable, or literal |
| Endpoint per environment | Available | Multiple `ContextType` elements carry per-environment values |
| Status (live / unused / stub) | Available | Reference counting across the call graph, plus `statusCode` and recycle-bin state |
| Trigger type | Available | Route endpoints and job entry components |
| **Schedule / cadence** | **Runtime-only** | Held in the control plane - on a *task* (an artifact bound to its runtime configuration) or a *plan* that sequences tasks - or in an external scheduler. Never in the workspace export. Record `not in export`, never a guessed interval. Section 6 says what to ask for |
| Frequency / volume | Runtime-only | Cadence is not volume; neither is in the export |
| Error handling | Available | Declared components and `REJECT` / `COMPONENT_ERROR` links. Whether they fire is still runtime |
| Explicit adjacency | Available | `tRunJob` and `cTalendJob` bindings, resolved by GUID |
| **Implicit adjacency** | **Available but partial** | Shared-channel matching on `cJMS` `DESTINATION` values. **Load-bearing on this platform**: without it the route graph is disconnected. A share of destinations are expressions rather than literals and are `unresolvable` - in one export, 6 of 59 |
| Integration style | Derived | Signals are Available; the style attribution is design recovery, so `Inferred` at best |
| Coupling type | Derived | Structural from schema and protocol; temporal is candidate-only |
| Blast radius | Derived | From the call graph, so it inherits the implicit-adjacency gap |
| Temporal coupling | Candidate-only | Never confirmable statically |
| Criticality | **Not code-recoverable** | Register |
| EIP pattern | Derived | Interpretation over route components |

### Independent signal classes

**Several.** Talend declares the connection target, the driver or auth pattern, and the payload schema in
*separate* artifacts, so the `>=2 independent signals` rule in `method/extraction-guide.md` applies at full
strength and the terse-platform exemption does **not** apply here.

Watch the independence test rather than the count: a connection host and its driver class read from **one**
connection block are one declaration wearing two hats and fail the test. A payload schema vocabulary plus a
connection host pass it.

**A rich export is the analyst's trap.** Because Talend states protocol, auth and per-environment endpoints at
component level, the output templates ask for them as though every platform did. When the same templates are
filled from a connector-abstracted platform, those cells are answerable only as "the connector implies it",
which is a different grade rather than a blank.

## 4. Locus addressing form

| Artifact kind | Expression form | Example |
|---|---|---|
| Job or route component | XPath over the `.item` XML | `process/Load_Orders_0.1.item#//node[@componentName='tMap']` |
| A specific component parameter | XPath to the `elementParameter` | `routes/Sync_1.2.item#//node[@componentName='cJMS']/elementParameter[@name='DESTINATION']` |
| Context variable | XPath over the context `.item` | `context/Env_0.1.item#//contextParameter[@name='db_host']` |
| Artifact metadata | XPath over the `.properties` | `process/Load_Orders_0.1.properties#//TalendProperties:Property/@version` |
| Connection or schema definition | filename alone | `metadata/connections/erp_prod.properties` |

`metadata/` entries frequently have no finer address than the file, and that is an acceptable locus here.

## 5. Scoring deviations

| Dimension | General ceiling | This platform | Why |
|---|---|---|---|
| D3 Operational Excellence | `Unknown` | `Unknown` (no deviation, and the reason is structural) | Schedules live in the control plane, not the export. On a platform whose export carries a scheduler trigger inline this ceiling would lift; here it cannot. The ceiling is a property of **what was supplied**, not of the platform - a control-plane extract carrying tasks and plans would lift it, and section 6 states what to request |
| D5 Security & Compliance | `Inferred` | `Inferred`, but auth mechanism and credential storage are both `Confirmed` | The component states the mechanism and the `enc:` prefix distinguishes storage. Enforcement is still not visible, which is what holds the dimension at `Inferred` |

No other dimension deviates. The general ceilings in `method/scoring-rubric.md` hold.

## 6. Known limits and declared gaps

| Gap | What is unknown | What would close it |
|---|---|---|
| Cloud / TMC artifacts | Whether any of this profile transfers to a built deployment artifact. Documentation confirms the shape - a packaged Job comprising a jar, execution scripts and log4j configuration - so the expected answer "very little" is corroborated rather than merely assumed | A TMC artifact to read, if only to state the boundary precisely rather than by expectation |
| TMC control-plane API | Whether an API extract carries the schedule and deployment state the workspace lacks. The object model is documented: **environment > space > artifact > task > plan**, where a *task* binds an artifact to its runtime configuration (parameters, connections, engine) and a *plan* sequences tasks. A task may reference an artifact from another space in the same environment; a space cannot span environments. Identical space names across environments are a documented prerequisite for promotion, which makes space naming a checkable governance signal rather than a cosmetic one | An API extract or a TMC export. This would move the D3 ceiling, and it is now a specific request - tasks, plans and their schedules - rather than a vague one |
| Runtime lineage log | The JSON structure, and which components it covers. Documented to be enabled per project or per job and to carry job name, component name, schema and query; an external scanner consumes it specifically to resolve `tDBRow`, `tDBInput` and `tDBOutput`, and explicitly does **not** cover `tJava` / `tJavaRow` | One `runtime_log_*.json` from a job run with the flag set. This is the artifact that lifts a dynamically assembled statement out of `unresolvable`, so it is worth asking for whenever field lineage is in scope |
| Repository-binding attribute | The `.item` attribute by which a component binds to a centralized `metadata/` item instead of declaring inline. The *distinction* is vendor-documented; the serialisation is not, as with everything else in the `.item` | A diff of a known repository-bound component against a known built-in one in the same export. `parsing-core.md` instructs the diff and forbids guessing the name |
| Routelet mechanics | Unverified. One export held an empty `routelets/` directory; no routelet has been observed | An export containing one. Expect joblet-like behaviour, verify rather than assert it |
| Joblet version pinning | Whether joblet references are version-pinned the way job calls are | An export with two versions of one joblet |
| Version dedup rule | Multiple versions of an artifact coexist on disk (one export: 579 versioned items for ~73 logical artifacts). Deduplicating to latest silently redirects every version-pinned edge. The platform's own impact-analysis view defaults to latest-version-only, so that is what a practitioner will expect - which is a fact about expectations, not a resolution of the hazard | A settled rule. `parsing-core.md` states the hazard and instructs resolving edges on the full (project, GUID, version) key without settling dedup for counting |
| `tMap` construct serialisation | The `.item` attribute names for the options that decide whether a mapping is statically resolvable - match model, load model, join type, `Die on error`, `Enable Auto-Conversion of types`, `Lookup in parallel`. The option *set* and what each does to a mapping's grade are vendor-documented at 8.0 and are stated in `parsing-di-jobs.md` -> "What a `tMap`'s configuration decides"; only the serialisation is missing | A diff of a `tMap` with a lookup against one without, in the same export. `parsing-di-jobs.md` instructs the diff and forbids guessing the names. Until then a lookup mapping is graded `Inferred` on the construct class alone, which is the correct grade - what is lost is the ability to say *which* option produced it |
| Constraint metadata in `metadata/` | **Narrowed.** Column-level key-ness is representable and already parsed (`//column/@key`); a centralized schema column is documented to carry name, type, length, precision, nullable, key, comment, pattern and DB type. What remains unknown is **inter-table referential structure** - whether a connection entry records foreign keys at all | Reading one connection entry. The open question is foreign keys, not keys. Schema extraction is still a flat field list, which under-represents referential fan-out |
| Dynamic-schema marker | The attribute that declares a component's schema dynamic. The *behaviour* is documented - a dynamic schema enumerates no columns, so a `<column>` count is a lower bound with no signal that it fell short - but the serialisation is not | A diff of a known dynamic-schema component against a known static one. `parsing-di-jobs.md` instructs the diff and forbids guessing. Until then, report the count of non-enumerable schemas separately rather than folding it into a coverage figure |
| Disabled-component marker | The attribute that switches a component off inside a live job. Behaviour is not in question; the attribute name is undocumented like the rest | The same diff, one active component against one switched off. The counting convention itself is already decided - see `parsing-core.md`, "Disabled Components Inside a Live Artifact" |

**Not a gap - a boundary.** Column lineage **through** these components is not recoverable from a workspace export
by any method, and the assessment should state it at intake rather than rediscover it per field:

| Component class | Why | Note |
|---|---|---|
| `tJava`, `tJavaRow`, `tJavaFlex` and routines | The mapping is hand-written code. The runtime lineage log does **not** cover them either | Unless the code carries an explicit lineage annotation - see `parsing-di-jobs.md`, "Lineage Through Custom Code" |
| `tHMap`, `tXMLMap` | The map is a separate artifact under `Maps/` or `Structures/`; the job names it and does not contain it | Parse the map as its own artifact with its own coverage statement, or record `unresolvable` naming the component |
| Unsupported and third-party components | Nothing states their internal column behaviour | A commercial scanner's documented fallback here is name-matching plus a Cartesian product of the leftovers. Do not reproduce that guess as a mapping |

`tMap` is **not** in this class: it is the platform's general-purpose mapper, its expressions are in the `.item`, and
commercial scanners support it. Do not widen the boundary to "complex transformations" - that overstates it, and it
is the class of claim that makes an estate look less traceable than it is.
