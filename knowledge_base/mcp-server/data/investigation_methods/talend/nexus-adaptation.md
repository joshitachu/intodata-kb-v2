---
applies to: Talend DI and Talend ESB, inside a Nexus documentation run
role: nexus-adaptation
adapted from: integration-suite, revision 0c68b22633a6d067fcc30e29dd18e86d9ea6d24f
adapted on: 2026-09-11
evidence class: platform contract -- describes THIS runtime, not Talend
---

# Nexus adaptation of the Talend investigation method

The companion files beside this one under `instructions/platform/` are copied
verbatim from the Integration Suite. They were written for an interactive
assistant with a shell, a writable working directory, web access, and a Talend
Studio workspace export on disk. This file is the only thing that reconciles them
with a Nexus documentation run. Read it first, then read `profile.md`.

None of them is startup reading. `instructions/platform/README.md` says which
guide answers which question; open one when a question needs it.

Nothing here overrides the Talend facts in the companion guides. It overrides only
the procedural instructions -- where the evidence is, how you look at it, and how a
result leaves your hands.

Companion files and what each is for:

- `profile.md` (role `profile`) -- what Talend is, which artifact formats exist,
  and which landscape fields are recoverable from an export at all versus
  runtime-only. Read this before you claim a field is missing.
- `platform-behaviour.md` (role `behaviour`) -- runtime and component behaviour,
  and the platform facilities that pre-empt a finding. Use it to avoid asserting
  something the platform will not do. It is not a parsing guide.
- `parsing-core.md`, `parsing-di-jobs.md`, `parsing-esb-routes.md` (roles `core`,
  `di`, `esb`) -- the analytical method is usable; the XPath, grep and glob
  procedures in them are not, because the `.item` XML is not staged. See
  section 3.

## 1. There is no Studio workspace. There is a parse of one.

Your working root is the run's workspace directory. No Talend source tree is
present in it: no `talend.project`, no `process/`, no `.item` or `.properties`
pairs, no `route_resources/`. A deterministic scanner read all of that before you
were invoked, and its output IS your evidence.

    INDEX.md                          the map: evidence classes and populations
    scan/jobs/<group>/<design>.json   one file per job, route, joblet or routelet
    scan/contexts.json                repository context groups and their values
    scan/connections.json             centralised metadata connections
    scan/routines.json                Java routines: names, signatures, and what
                                      each body referenced -- not the bodies
    scan/xslt.json                    stylesheets: templates, instructions, variables
    scan/services.json                published port types and operations
    scan/structures.json              shared schema structures (TDM)
    scan/tdm_maps.json                TDM map definitions
    scan/landscape.json               systems, applications and proven edges
    scan/lineage.json                 field-level source-to-target lineage
    scan/mapping_accounting.json      per mapper: offered / written / mapped counts
    scan/inventory.json               every file the scanner saw, and its outcome
    scan/capability_ledger.json       every construct observed, with a status

A collection the estate has nothing of is an empty list, not a missing file.
Absence of `routes/` is the DI-versus-ESB signal `profile.md` describes, and here
it reads as: no design carries `artifact_kind: "route"`. That is a fact about the
estate, not a gap in your evidence.

Two derived layers sit beside the scan. `facts/` holds deterministic rollups --
`units.json`, `dependencies.json`, `interfaces.json`, `configuration.json`, one
exact untruncated mapping table per mapper under `facts/mappings/`, and
`facts/navigation/`, which restates the same material as one-line JSONL rows so
`grep` returns a whole record rather than a fragment of one. `runtime/` holds what
production actually did, when a runtime mirror is connected; it states its own
status either way.

## 1b. No source text is staged, and that includes code bodies

The rule is enforced on bytes, not on filenames: nothing the agent can read is
original source. Component code bodies (`tJava`, `tJavaRow`, `tJavaFlex`, the
`CODE_*` parameters) and routine sources are therefore absent from the parse as
well, replaced in place by a stated `code_withheld` summary -- how long the body
was, a digest of it, and the identifiers it referenced.

What that costs, and what it does not:

- You can still tell that two components carry the same body, and still see that
  a routine reaches for a date formatter or a status table. You cannot see which
  branch it takes, what it defaults, or what it swallows.
- **Mapping expressions are staged in full.** `tmap_data[]` and `txmlmap_data[]`
  entries keep their `expression` verbatim, and `facts/mappings/` keeps the exact
  per-field table. That is the field-level evidence, and it is untouched by this
  rule.
- Component `params[]` keep their queries, conditions, endpoints, file patterns
  and headers. Most of what a design decides is there, not in a code block.

A withheld body is present and hidden, exactly like a redacted value. Report the
boundary -- the code exists, it was not available to this analysis, and here is
what would settle the question. Never write that the code does not exist, never
conclude that a component has no logic because its body was withheld, and never
reconstruct a body from its summary.

## 2. The format gate is already closed

`profile.md` section 1 tells you to check `talend.project` before applying any
Talend guide. That gate was applied at scan time: a directory without a valid
`talend.project` is not parsed as a Talend project at all, so a scan you are
reading is by construction a scan of a Studio workspace export.

What still applies from that section is the *negative*: a Cloud/TMC deployment
artifact, a built jar and a runtime lineage log are all out of scope, and if the
scan's evidence looks like one of those rather than a design export, say so rather
than analysing it anyway. `scan/inventory.json` is where that would show.

## 3. The parsing guides' procedures cannot be run here, and their answers are
already computed

`parsing-core.md`, `parsing-di-jobs.md` and `parsing-esb-routes.md` are staged
beside this file, because the analytical method in them -- which components change
an outcome, what a mapper's configuration actually decides, what each connection
type means for execution order -- has no substitute anywhere else. What cannot be
carried over is their procedure: they teach XPath, grep and glob over `.item` XML,
and that XML is not staged. Read them for the reasoning, not for the commands.

Their *results* are here already, computed deterministically:

| The guide would have you derive | It is already here |
|---|---|
| Component inventory per design | `components[]` on each design file, with `component_type`, `active`, `params[]`, `schemas[]` and, where a body existed, a `code_withheld` summary of it (section 1b) |
| Connection topology and trigger order | `connections[]`, with `connector_name` (`FLOW`, `ROUTE`, `ON_SUBJOB_ERROR`, `RUNIF`, ...) |
| `tMap` / `tXMLMap` internals | `tmap_data[]` / `txmlmap_data[]`, plus the exact tables in `facts/mappings/` |
| `tRunJob` and `cTalendJob` bindings | `trunjob_refs[]` and `artifact_refs[]`, already resolved -- see section 5 |
| DI-versus-ESB classification | `is_esb_route` and `esb_component_types`, computed from the c-prefix convention |
| Context variables and repository-versus-built-in declarations | `context_groups[]`, `declaration`, `metadata_connection_ref`, `repository_property_ref` |
| Recycle-bin, copy and version filtering | done at scan time; see section 5 |
| The call graph | `facts/dependencies.json` |

Do not look for a directory those guides name. Do not report a construct as absent
because you could not find the file the guide told you to grep. If a question the
guides answer is not answerable from the fields above, that is a scanner limitation
and belongs in `facts/parser_limitations.md`'s terms -- section 8.

## 4. Secrets are redacted, and redacted values must not be joined on

The scan has been through a credential filter. Secret-shaped values were replaced
with placeholders, for example:

    [PASSWORD_REDACTED]   [URL_REDACTED]   [HOSTNAME]   [REDACTED]
    [HOST_REDACTED]       [DATABASE_REDACTED]           [FILE_PATH_REDACTED]
    [CONNECTION_STRING_REDACTED]

Two consequences, both mandatory:

1. **Never establish identity by matching redacted values.** Two connections whose
   host both read `[HOSTNAME]` are not the same host, and two context variables
   both reading `[URL_REDACTED]` are not the same endpoint. A placeholder means
   "one value was here", never "this value". Joining on it manufactures systems and
   merges systems that are distinct. Match on the structural signals instead --
   component type, driver, `repository_property_ref`, port, database or schema name
   when unredacted, and payload vocabulary.
2. **Never reconstruct, guess or echo a secret.** Do not quote a value that looks
   like a credential even if the filter missed it. Report it as a redaction gap
   with its locator and move on. Describe connection NAMES and target systems; do
   not describe secret values.

`profile.md` section 3 lists credential storage as recoverable. That remains true
in the sense that matters here: you can still distinguish an
`enc:system.encryption.key.v1:` prefixed value, a context-variable indirection and
a literal, because the SHAPE survives redaction even when the value does not.

A redacted value is present and hidden. It is never missing configuration, and
saying otherwise in a client document is the single error a client spots fastest.

## 5. GUID resolution and version dedup are already done

Two of the most error-prone procedures in `parsing-core.md` have already been
carried out deterministically:

- **Reference resolution by GUID.** `tRunJob` and `cTalendJob` bindings that name a
  job by its repository GUID are already resolved: each entry in `trunjob_refs[]`
  and `artifact_refs[]` carries `resolved_job_id` / `resolved_artifact_id`,
  `resolution` and `reference_source` (`identity` beats `label`; a cached Studio
  label goes stale on rename). `facts/dependencies.json` is the assembled graph.
- **Version dedup.** A workspace commonly holds several versions of the same
  artifact (`_0.1.item`, `_0.2.item`). The scanner selected the version set for this
  run and recorded the rest as `superseded` in `scan/inventory.json`. Do not count
  versions as separate designs.

Re-derive either of these only to VERIFY a specific claim you are about to make. A
re-derivation that contradicts the scan is a finding: report both numbers and the
locators, do not silently prefer your own.

Unit identity is a known trap. `unit_id` (a design's `job_id`) is the scanner
identity and is what everything joins on; `display_name` is what a human called it
in Studio. Both are in `facts/units.json`. Key on the identity, quote the display
name. Never normalise or fuzzy-match design names to make two of them meet:
`LoadCustomer` and `LoadCustomers` are two real, separately
scheduled jobs.

## 6. You write your output directly

There is no submission tool and no artifact facade in this runtime. You write
Markdown files yourself:

- `output/` is the deliverable. Sub-directories are preserved --
  `output/integrations/order_to_invoice.md` publishes as
  `integrations/order_to_invoice.md`. `output/topology.json`, if you write one, is
  compiled into the landscape view; it is optional and never blocks the documents.
- `notes/` is your own scratch, including `notes/progress.md`, which is what a
  fresh session is given if this one is interrupted.

Everything else in the workspace is read-only, and nothing outside it is reachable:
no shell, no network, no writes beyond those two directories. Publication checks
that every published file is Markdown under `output/`, that no credential leaked,
and that no internal workspace path or platform vocabulary reached the client
prose. Nothing else is validated -- there is no required filename, no required
section and no identity to match.

## 7. Do not reproduce exact tables from memory

`facts/` is the authority for anything countable: per-field mapping accounting,
dependency edges, interface inventories, configuration keys, capability presence.
It was computed exactly and it may be far larger than what you can hold.

- Quote at most the few rows your sentence is actually about, from the file in
  front of you.
- Never re-tabulate a mapping table from recollection or from a sample. A
  regenerated table that is 95 percent right is worse than a citation, because a
  reader cannot tell which 5 percent is wrong.
- Never present a count you did not read from a deterministic table or compute from
  evidence in view.

Explain, interpret and connect the facts. That is the work only you can do. The
counting is already done and re-doing it by hand introduces the one error class the
deterministic layer exists to eliminate.

## 8. Unsupported, dynamic and ambiguous constructs are explicit unknowns

Some constructs are not statically resolvable: dynamic schemas that enumerate no
columns, SQL assembled at runtime from context variables, JMS destinations that are
expressions rather than literals, custom code whose lineage is not annotated.
`facts/parser_limitations.md` lists what this particular scan could not read, and
`scan/capability_ledger.json` is the row-level version of it.

When you meet one, report it as an unknown with a locator, in this shape:

    unknown: <what could not be determined>
    locator: <scan path>:<element> (for example
             scan/jobs/billing/BillingWriter.json, component tDBRow_2, param QUERY)
    reason:  <dynamic | not in the scan | ambiguous | unsupported construct |
              code body withheld from this workspace>
    what would resolve it: <the runtime extract, log or owner question>

Never fill an unknown with a plausible value, and never let silence stand in for
one. A named unknown with a locator is a usable deliverable; an invented value is
a defect that survives review because it reads well. If a field is marked
`Runtime-only` or `Not code-recoverable` in `profile.md` section 3, check
`runtime/` before calling it unknown -- that is exactly what `runtime/` carries
when a mirror is connected, and it states its own absence when one is not.

## 9. What does NOT apply in Nexus

These parts of the Integration Suite method are out of scope in this runtime.
Ignore them wherever the companion guides or their cross-references invoke them.

| Not applicable | Why |
|---|---|
| Every grep, glob and XPath procedure in `parsing-core.md`, `parsing-di-jobs.md` and `parsing-esb-routes.md` | The `.item` XML they operate on is not staged. Their results are (section 3); their reasoning still applies. |
| Any instruction to read a component's or routine's code body | Code bodies are withheld from this workspace (section 1b). Work from mapping expressions, parameters and the withheld-body summary, and disclose the boundary. |
| The scoring rubric (`method/scoring-rubric.md`) and any numeric score | Nexus does not score an estate in this lane. Do not emit a score, a grade or a rubric dimension. |
| Pipeline phases 2 through 6 -- Assess, Score, Design, Review, Summarize | Only the Phase 1 style of work (analyze the estate) has an equivalent here. Produce assessments, designs or roadmaps only if the goal asks for them. |
| `bypassPermissions` and any permission-mode instruction | Your tool set and its limits are fixed by the platform. There is nothing to bypass and no mode to request. |
| `WebFetch` and any web or vendor-documentation lookup | There is no network. Vendor behaviour you cannot verify from the scan is an unknown (section 8), not a lookup. Facts already written into `platform-behaviour.md` remain usable as documented claims. |
| The assessment and output templates (`outputs/assessment-document.md`, `scorecard-template.md`, `design-document.md`, `improvement-roadmap.md`, `review-report-template.md`, `customer-summary-template.md`, `inventory-document.md`, `system-landscape.md`) | `instructions/writing_the_docs.md` describes the document set for this run. Follow that, not these. |
| The interactive assessment questionnaire | There is no human in the loop during a run. A question you would have asked becomes a named unknown. |
| Provenance headers, gate retries and the `run-pipeline.sh` gate checks | Publication runs its own checks (section 6). Do not emulate the shell gates or add their headers. |

What DOES apply, in full: everything the companion guides say about Talend itself
-- the artifact-format taxonomy, component semantics, the call-graph construction
rules, the corroboration constraints, the availability classes, and the discipline
of enumerating before classifying.
