---
applies to: Talend DI and Talend ESB
artifact format: not an artifact-parsing document - runtime behaviour, component behaviour, and the platform facilities a design may rely on
verified against: Talend 8.x production ESB deployments, plus Talend 8.0 / 8.0-R2025 vendor documentation for the platform facilities. Each runtime entry is a single-deployment observation unless stated otherwise.
evidence class: export-verified for what the artifact declares; runtime behaviours are production observations, n=1 unless stated; platform facilities are vendor-documented
last verified: 2026-07-26
---

# Talend platform behaviour

**Read this in Phase 4 (Design) and Phase 5 (Review), not Phase 1.** Nothing here helps you parse an export. What
it does is stop a design recommending something the platform will not do, and stop a review raising a finding the
platform has already answered.

Before this file existed, these facts lived at the bottom of a parsing guide that only Phase 1 read, so a design
could recommend a PATCH call the runtime silently converts to POST and a review would not catch it.

> **Every entry is an existence claim.** "This can happen on this platform at this version" - not "expect this".
> Under the one-engagement rule in `CONTRIBUTING.md` that is what a single production observation supports.
> Check whether an entry applies before acting on it; several are specific to the Karaf runtime and do not arise
> on a DI-only estate.

## How to use this in a review

Three distinct moves, and conflating them is how false positives get delivered:

1. **A declared capability the runtime does not honour** is a real finding, and a more serious one than a missing
   capability, because the design looks correct. The PATCH conversion below is the type case.
2. **A missing capability the platform supplies elsewhere** is not a finding. Check the platform first.
3. **A workaround already present in the code** is a finding about *documentation*, not about the design. Code
   using `tJavaFlex` where a component would be idiomatic usually means someone hit one of these; ask before
   flagging it as a maintainability problem.

## Runtime architecture

**Dual-JVM Karaf architecture.** Enterprise Talend ESB runs two separate JVM instances: Runtime, which executes
routes and jobs, and Remote Engine, which handles deployment from TMC. Configuration changes must target the
correct one. Export and compare both `wrapper.conf` files when diagnosing.

*Design and review consequence:* a recommendation to set a JVM argument, heap size or system property must name
which JVM. One that does not is not actionable, and applying it to the wrong JVM looks like the setting had no
effect. `wrapper.conf` is also the only place this architecture is visible - it is absent from every job and route
definition, so a code export alone will lead a design to assume a single JVM.

**OSGi bundle isolation prevents configuration fixes.** Jobs embed their own dependency copies (CXF and others) in
OSGi bundles. JVM arguments, system properties and Karaf-level bundle changes do **not** reach embedded libraries.

*Design and review consequence:* when a design's remediation is configuration-level and the target is an embedded
library, the remediation will not work. Go to a code-level solution using JDK built-ins instead. This is why
several component workarounds below are code rather than config.

**TMC observability is DI-only.** TMC component execution metrics return empty for ESB routes, which run as Camel
microservices. Only DI jobs have TMC-level observability.

*Design and review consequence:* a monitoring recommendation that assumes TMC covers the whole estate is wrong for
the ESB half. ESB runtime monitoring needs a separate mechanism. This also bounds what runtime enrichment can do
for a D3 score on an ESB estate: the data does not exist to be fetched.

## Component behaviour

| Component | Behaviour | Consequence |
|---|---|---|
| `tDie` | Does not propagate its message string to the exception; the `TDieException` constructor receives null | A design relying on `tDie` for a descriptive error in a dead-letter channel or error queue gets a null message. Use `tJava` throwing `new RuntimeException("...")` where the message must survive |
| `tRESTClient` | On Karaf Runtime, silently converts **PATCH to POST**, a CXF / `HttpURLConnection` limitation. Each OSGi bundle embeds its own CXF copy, so configuration cannot override it | A design specifying PATCH semantics is not what will run. The target API sees POST. Workaround is `tJavaFlex` with `java.net.http.HttpClient`, which bypasses CXF |
| `tRouteOutput` | Only generates Camel `setHeader()` code when invoked from a route. Standalone it is a no-op | Standalone testing gives false confidence. A test strategy must exercise it through route invocation, and a review should check that it does |
| Dead-letter channel URI | `activemq:queue:ErrorQueue` creates an **anonymous** JMS connection with no credentials. Talend strips underscores from component IDs in the Camel registry (`cMQConnectionFactory_1` becomes `cMQConnectionFactory1`) | Use the registered factory name: `cMQConnectionFactory1:queue:ErrorQueue`. An anonymous connection is both a reliability and a security finding, and the underscore-stripping makes the correct name non-obvious |

## Code constraints

These bound what a design may specify as implementation, and they explain code that otherwise reads as poor style.

**Java generics fail in `tJavaFlex`.** The code generator misinterprets angle brackets as annotation syntax, so
`HttpResponse<String>` will not compile. Use raw types with explicit casts: `HttpResponse resp`, then
`(String) resp.body()`.

*Review consequence:* raw types in `tJavaFlex` are a platform constraint, not a code-quality finding.

**Component fields use different expression languages with no visual distinction.** Two fields on the same
component can expect different syntaxes: `tRouteOutput` Body accepts `{in.header.X}` interpolation while Header
Name is raw Java requiring quoted strings. There is no indication in the UI, and a compilation error is the only
way to discover which is which.

*Design consequence:* do not specify expression syntax for a component field without verifying which language that
field takes. "Verify against the component reference" is the correct instruction where this is uncertain.

## Platform lineage and impact-analysis facilities

**These entries are vendor-documented, not production observations** - a different evidence class from the rest of
this file, and a stronger one for "does this capability exist". None has been seen in use by this toolkit, so
treat *the platform offers this* as established and *it will work on this estate* as the thing to check.

### Impact analysis and data lineage in Studio

Studio ships two views over the same graph: **impact analysis** (forward - which flows use this item and break if
it changes) and **data lineage** (backward - where a column's value came from, through the components that touched
it). Analysis is column-level, results export to HTML and XML, and the feature is installed through Feature
Manager rather than shipped enabled.

**Their precondition is the whole finding: they only see items centralized in the Repository tree** - Joblet
Designs, Contexts, SQL Templates, Referenced project and Metadata. A built-in declaration is invisible to them.

*Design and review consequence:* "the platform provides no lineage capability" is a wrong finding, and a
recommendation to buy tooling built on it is wrong with it. Check the built-in versus repository ratio first -
`parsing-core.md` says how to count it. On a built-in-heavy estate the correct remediation is **centralize the
metadata**, a governance change the customer already owns, which unlocks a capability they have already paid for.
Note too that a static parse reading inline schemas directly sees *more* than these views do on a built-in-heavy
estate - so do not concede that the vendor tool would have produced a better answer without checking the ratio.

### Runtime lineage

A JVM flag on Studio plus a per-project or per-job setting writes one JSON log per run, under
`<output_path>/<project>/<job>/<version>/`, carrying the job name, the component name, the schema and the query.
Standard Jobs only.

*Design and review consequence:* this is the platform's own answer to a query assembled at run time. A design that
needs field lineage across free-form SQL components should specify enabling it rather than commissioning
hand-documentation, and a review that finds field lineage undocumented should check whether it was ever switched
on. It is also the artifact to request when Phase 1 has recorded hops as `unresolvable - dynamic`.

### Lineage collection to Qlik Cloud

A per-task toggle in Talend Management Console publishes input and output datasets plus lineage to the Qlik Cloud
catalogue, generated on a task's first run and after each modification. It requires a recent Studio version and a
linked tenant.

*Design consequence:* the documented caps are real design constraints rather than fine print - **50 datasets and
500 lineage records per task**, with the Remote Engine retaining lineage 7 days by default. A wide job can exceed
the dataset cap and under-report without failing. Granularity is dataset-level, not field-level, and only
components on a supported list contribute. A design promising field-level lineage through this path is promising
something it does not deliver.

### Big Data jobs

Spark Batch jobs emit lineage to Cloudera Navigator or Apache Atlas, configured per job. Atlas receives one entity
for the job and one per schema-consuming component; configuration and connection components are excluded because
they carry no schema. Navigator is documented as not recommended for production, because it generates lineage for
every entity on the cluster.

*Design consequence:* this path is distribution-coupled and does not apply to a standard DI or ESB estate. Do not
offer it as the lineage answer for one.

## Platform capabilities that pre-empt findings

Check these before recording a finding in the corresponding area.

| Area | What the platform supplies | Do not record as a gap without checking |
|---|---|---|
| Credential encryption | Context values prefixed `enc:system.encryption.key.v1:` are platform-encrypted | "Credentials in plaintext". Assess the *mechanism* - context variable versus vault - not the value |
| Runtime context override | `tContextLoad` provides runtime context loading where present | "No runtime configuration", and a design-time context pin's severity, which drops when an override exists |
| Route error handling | `cOnException`, `cErrorHandler` and `cDeadLetterChannel` are the route-level idioms. Two exports each used one **exclusively**, to the exclusion of the other | "No error handling" after searching for only one idiom. Check for all three; finding none is a real finding, and finding one is not the same as finding the one you looked for |
| Lineage and impact analysis | Studio ships column-level lineage and impact analysis over **centralized** repository items | "The platform provides no lineage". The constraint may be the estate's centralization choice rather than the platform - count the ratio before writing either |
| Custom-code lineage | A documented comment convention lets external lineage tooling read mappings out of hand-written Java components - see `parsing-di-jobs.md` | "Custom code makes lineage impossible". Check whether the convention is already present; where it is not, recommending it is cheaper than any alternative |
| Dynamic query resolution | A runtime lineage log records the query as executed | "Dynamic SQL cannot be traced". It cannot be traced *statically*; ask whether the log can be produced before recording it as a permanent limit |
