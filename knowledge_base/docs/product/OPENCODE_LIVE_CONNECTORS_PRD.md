---
title: OpenCode Live Connector Evidence
document_type: product_requirements_document
status: proposed
version: 1.0
created: 2026-08-20
owner: Nexus Knowledge Base
scope: OpenCode documentation generation only
first_provider: Elastic Stack
---

# OpenCode Live Connector Evidence - Product Requirements Document

## 1. Executive summary

Nexus will add governed live connectors to the OpenCode documentation path so
OpenCode can enrich source-based documentation with current operational evidence.
The legacy documentation path will not change.

The connector platform will be implemented in the knowledge-base MCP server, which
already owns project scanning, documentation generation, OpenCode orchestration,
staging, validation, publication, and audit artifacts. The RAG service remains a
downstream consumer of published documentation and does not own connector-assisted
generation.

OpenCode will never receive vendor credentials, arbitrary vendor URLs, unrestricted
query languages, or general network access. During a dedicated Gather sub-step it
will receive a small set of read-only, run-scoped Nexus MCP tools. Those tools call
provider adapters inside the MCP server, normalize and redact responses, enforce
budgets, and return bounded evidence envelopes. Analyze and Produce will receive
only accepted evidence artifacts and will have no live connector access.

Elasticsearch is the first runtime evidence provider. Kibana is an optional
companion used to discover spaces, data views, index patterns, and runtime fields.
Elasticsearch remains the system queried for actual event and log evidence. The
design is provider-neutral so TMC and future systems can implement the same
capability and evidence contracts without adding Elastic-specific branches to the
documentation pipeline.

The first production milestone is complete when a workspace administrator can
connect a real Elastic Stack deployment, a project owner can bind approved sources
to a project, and an OpenCode documentation run can produce a source-backed runtime
finding from live Elasticsearch data without persisting a raw export or changing
legacy generation behavior.

## 2. Product context

### 2.1 Existing generation paths

Nexus currently offers two working documentation methods:

1. The legacy custom harness, which executes the established seven-phase pipeline.
2. The `opencode-cli` provider, which preserves the same deterministic preparation,
   prompts, validators, staging, publication, and document contracts while using one
   continued OpenCode session across the user-visible Gather, Analyze, and Produce
   phases.

The connector feature applies only when a run resolves to `opencode-cli`. A legacy
run must not resolve connector bindings, issue connector credentials, call external
evidence capabilities, or change its prompt/context shape.

### 2.2 Existing OpenCode seam

`mcp-server/src/mcp_server/providers/opencode_cli.py` already provides the required
orchestration seam:

- Gather contracts are built separately from Analyze and Produce contracts.
- `OpenCodeTaskContract.allowed_tools` exists.
- OpenCode is invoked once per phase step while continuing the same session.
- Deterministic Python finalizers verify outputs and move artifacts between phases.
- Analyze consumes `_investigation` and scanner facts.
- Produce consumes deterministic document packs and analysis artifacts.

The current `MCP_OPENCODE_MCP_READ_ALLOWLIST_JSON` mechanism is not sufficient as a
security boundary. It classifies tools by name prefix and communicates the result in
the prompt. The production design must replace this with trusted capability metadata,
generated deny-by-default OpenCode configuration, and run-scoped authorization.

### 2.3 Why live connector evidence

Source code proves intended and implemented behavior, but it cannot prove every
operational fact. Useful runtime evidence includes:

- whether a code path is deployed and active;
- recurring error families and their relative frequency;
- observed service, endpoint, queue, or job identifiers;
- recent execution success/failure and duration;
- operational drift between the repository and deployed behavior;
- configured alerts or data views that reveal how operators monitor the system.

This evidence makes documentation more useful when it is clearly time-bound and
supplementary. It becomes dangerous when it is treated as implementation truth,
queried without scope, or copied wholesale into model context.

## 3. Problem statement

### 3.1 Evidence gap

The current generator can document what the scanner and repository prove, but it
cannot validate those claims against live operational systems during generation.
Operators must manually export logs or provide supporting files, making evidence
stale, hard to scope, and difficult to audit.

### 3.2 Integration gap

Adding vendor REST calls directly to prompts or to `OpenCodeCliProvider` would mix
authentication, vendor schemas, query construction, and documentation logic. Every
new source would create more special cases and would give the model excessive
authority.

### 3.3 Security gap

External systems contain credentials, personal data, customer payloads, internal
URLs, and adversarial or instruction-like text. A connector can be technically
read-only while still leaking data or exhausting model context. Tool naming and
prompt instructions alone cannot enforce project scope, query limits, or phase
boundaries.

### 3.4 Trust gap

Runtime data changes continuously. Documentation must distinguish code facts from
observations, retain the observation time and scope, and represent no-result or
ambiguous evidence as inconclusive rather than as proof of absence.

## 4. Goals

### G-1: Enrich only OpenCode generation

Add live evidence to OpenCode Gather while leaving the legacy provider unchanged.

### G-2: Support real external systems

The production path must query real provider APIs. Unit tests may replace network
transport, but the release gate requires end-to-end tests against a real
Elasticsearch service and a deployment smoke test with approved credentials.

### G-3: Remain provider-neutral

Represent capabilities, sources, questions, results, provenance, budgets, and
health generically. Elasticsearch is the first adapter, not the architecture.

### G-4: Preserve code authority

Scanner and repository evidence remain the implementation source of truth. Runtime
evidence may corroborate, add operational context, identify a discrepancy, or remain
inconclusive. It may not silently overwrite a code fact.

### G-5: Avoid raw exports

Query providers live and materialize only bounded, redacted evidence needed for the
current run. Do not mirror indices, download log archives, use scroll-based bulk
retrieval, or persist raw vendor responses.

### G-6: Enforce least privilege

Use read-only vendor credentials, project-bound source allowlists, run-scoped MCP
authority, phase-specific OpenCode permissions, and fixed query/result budgets.

### G-7: Make connector use intelligent

OpenCode should ask evidence questions derived from code entities and documentation
obligations, use cheap aggregate queries before detailed samples, and stop when the
question is answered or the budget is exhausted.

### G-8: Make every external claim auditable

Connector-derived claims must resolve to an evidence ID, connector instance,
capability, normalized query summary, observation time, source time window,
redaction/truncation state, and durable invocation receipt.

## 5. Non-goals

The first release will not:

- change legacy generation prompts, phases, results, or provider selection;
- give OpenCode direct Elasticsearch, Kibana, TMC, HTTP, or network credentials;
- expose raw Elasticsearch Query DSL to the model;
- support write, update, delete, deploy, restart, acknowledge, or publish actions;
- use the Elastic Cloud control-plane API to search logs;
- require Elasticsearch customers to have Kibana;
- assume fixed index names such as `logs-*`;
- mirror or continuously ingest external systems into Nexus;
- implement live log tailing during documentation generation;
- treat dashboards, alerts, cases, or human notes as code truth;
- make connector evidence mandatory for a documentation run to succeed;
- add personal/private connector instances in v1;
- complete the TMC provider without an agreed TMC API and authentication contract.

## 6. Product principles and authority model

### 6.1 Authority order

When evidence conflicts, Nexus uses this order:

1. Repository and deterministic scanner facts for implementation structure.
2. Live provider observations for time-bound runtime behavior.
3. Kibana configuration, alerts, dashboards, and cases for operational context.
4. Model interpretation for synthesis only.

The higher position does not erase lower evidence. A conflict is published as a
clearly labelled discrepancy with both scopes and timestamps.

### 6.2 Observation classifications

Every accepted external finding has one classification:

- `corroborates`: runtime evidence supports a code-derived statement;
- `operational_context`: runtime evidence adds behavior not provable from code;
- `discrepancy`: runtime evidence and code appear inconsistent;
- `inconclusive`: scope, freshness, identity matching, truncation, or no results
  prevent a reliable conclusion.

No-result responses are `inconclusive` unless Nexus can prove the queried source,
time range, filters, and coverage are sufficient for a negative claim.

### 6.3 Untrusted external content

All provider text is untrusted data. The broker must normalize it into typed fields,
remove control characters, redact secrets and sensitive values, cap each field, and
label excerpts as data. Provider content must never be treated as instructions or be
allowed to alter tool policy.

## 7. Ownership and permissions

### 7.1 V1 ownership model

Connector instances are Nexus-instance/workspace owned, not user-private. This fits
the current product model, where users receive project assignments but there is no
first-class organization entity in the knowledge-base service.

Each connector stores `created_by_user_id`, `updated_by_user_id`, and audit events.
Only superadmins or users with a dedicated `manage_connectors` permission may create,
rotate, disable, or delete connector instances.

Project owners or superadmins may bind an existing connector to a project and select
the sources/capabilities available to that project. A user may use a binding during a
run only if they can access the project. The run records the initiating user and an
immutable snapshot of the effective bindings.

### 7.2 Future personal connectors

Personal connectors may be added later for exploratory work. They are excluded from
canonical/published documentation by default and require a separate review policy.
V1 schema should retain an `ownership_scope` field so this addition does not require
a domain rewrite.

### 7.3 Required permissions

- `view_connectors`: list safe connector metadata and health.
- `manage_connectors`: create, update, test, rotate, disable, and delete instances.
- Existing project access: view bindings and use them in an OpenCode run.
- Existing project management: add, edit, enable, or disable project bindings.

Secrets are never returned by any read endpoint, even to administrators.

## 8. User experience

### 8.1 Connector catalogue

The dashboard adds a Connectors page similar to the reference experience. It shows:

- connector name and provider;
- `Connected`, `Degraded`, `Not connected`, or `Disabled` status;
- last successful health check;
- scope and number of project bindings;
- actions allowed by the current user's permissions.

The first catalogue tile is `Elastic Stack`. TMC is shown only when its provider is
implemented and enabled.

### 8.2 Connect Elastic Stack

The Elastic Stack form contains:

- display name;
- Elasticsearch HTTPS endpoint;
- authentication type: API key (recommended) or Basic authentication;
- credential input, write-only;
- TLS verification enabled by default;
- optional custom CA certificate;
- optional Kibana HTTPS endpoint;
- optional Kibana space selection;
- option to reuse the Elasticsearch credential or store a separate Kibana
  credential;
- connection test and discovery result.

Elastic Cloud management credentials are not required. A future read-only Cloud
management companion may discover deployments and endpoints, but it is separate
from the data-plane credential used to query Elasticsearch.

### 8.3 Discover and approve sources

After authentication succeeds, Nexus discovers visible non-hidden indices, aliases,
data streams, and optional Kibana data views. The administrator does not need to know
an index pattern in advance.

The operator selects logical sources and assigns a safe profile:

- display label;
- Elasticsearch target expression;
- optional Kibana data view and space;
- timestamp field;
- message fields;
- service/entity fields;
- severity field;
- optional environment field;
- maximum query window;
- whether event samples are allowed or aggregates only.

Auto-detected fields are editable because customer schemas vary.

### 8.4 Bind sources to a project

In Project Settings, a project owner can enable approved sources, choose capabilities,
and set narrower budgets. A project binding cannot broaden the connector-level
allowlist.

Recommended default capabilities:

- source description;
- aggregate events;
- bounded event search;
- one-event retrieval by an opaque reference returned earlier in the same run.

### 8.5 Start documentation generation

The Generate dialog shows a connector summary only when OpenCode is selected:

```text
Runtime evidence
  Elastic Stack / Production logs / Connected
  Default window: 7 days
  Maximum samples: 20
```

The user can disable connector use for that run. The user cannot add unapproved
sources or raise connector-level limits from the Generate dialog.

### 8.6 Review outcome

Run history shows:

- connectors and binding versions used;
- number of successful, failed, denied, and truncated calls;
- accepted findings by classification;
- observation time range;
- whether evidence was unavailable or skipped;
- a link to a sanitized audit receipt.

Connector evidence appears in existing relevant documentation sections, especially
operations, testing, risk, and discrepancy sections. V1 does not force a new document
or change the established document catalogue.

## 9. Functional requirements

### FR-1: Provider catalogue

The MCP server shall expose a code-owned provider catalogue. Every provider manifest
shall declare:

- provider ID, label, and version;
- supported authentication types;
- configuration schema and secret fields;
- setup/discovery capabilities;
- run-time evidence capabilities;
- `use_when` and `do_not_use_when` guidance;
- accepted entity reference types;
- risk class and read-only status;
- input and output schemas;
- default time, item, byte, and call budgets;
- redaction policy version.

Capability safety is determined by this trusted metadata, never by a tool-name
prefix.

### FR-2: Connector instance lifecycle

The service shall support create, list, read-safe-metadata, update, test, disable,
rotate credentials, and delete operations. Delete shall be blocked while active
project bindings exist unless the operator explicitly removes those bindings first.

Connection status shall distinguish authentication failure, authorization failure,
DNS/network failure, TLS failure, invalid endpoint, rate limiting, and provider
failure without exposing secrets or raw response bodies.

### FR-3: Credential handling

Credentials shall be encrypted at rest using the existing Nexus encryption key and
shall live outside general connector configuration JSON. Plaintext credentials may
exist only in process memory for the duration of a provider call.

Logs, exceptions, audit payloads, tool responses, staging files, OpenCode prompts,
OpenCode configuration files, and subprocess arguments shall not contain vendor
credentials.

API key authentication is the default for Elasticsearch and Kibana. Basic
authentication is a fallback. Elasticsearch permissions should be limited to
`view_index_metadata` and `read` on approved targets. Nexus does not require
`manage`.

### FR-4: Outbound connection policy

The provider transport shall:

- require HTTPS outside explicit development mode;
- reject URLs containing user-info credentials;
- reject loopback, link-local, and cloud metadata endpoints;
- permit private RFC1918 targets only when an administrator explicitly approves the
  hostname/network for the connector;
- resolve and validate every redirect target or disable redirects;
- verify TLS by default and support an approved custom CA;
- enforce connect, read, and total timeouts;
- cap response bytes before parsing;
- use bounded retry only for transient idempotent reads;
- never log request authorization headers.

### FR-5: Source discovery

Elasticsearch discovery shall use the Resolve Index API as the lightweight first
step. It shall default to open, non-hidden resources and return safe summaries of
indices, aliases, and data streams.

For operator-selected targets, the adapter may use Get Index Information with
`features=mappings,aliases` and Field Capabilities to build a normalized source
profile. It shall not call `GET /*` for full mappings/settings across the cluster.

Kibana discovery shall be optional. It shall list accessible spaces and data views,
then retrieve selected data-view definitions to resolve index patterns and runtime
fields. Nexus shall use documented Kibana APIs and shall never read the `.kibana`
index directly.

### FR-6: Project bindings

A binding shall identify one connector instance, one project, approved logical
sources, enabled semantic capabilities, field profiles, budgets, and status.

The service shall verify existing project ownership/management permission for every
binding mutation. Generation shall fail closed on cross-project or disabled binding
references.

### FR-7: Run snapshot and authorization

At OpenCode run preflight, Nexus shall snapshot the effective connector binding
versions and issue a short-lived capability grant containing:

- generation run ID;
- project ID;
- initiating user ID;
- Gather phase only;
- exact connector instance and source IDs;
- exact capability IDs;
- per-capability and total budgets;
- issued-at and expiry timestamps;
- a unique nonce.

The grant shall be unusable after Gather completes, expires, or is revoked. Analyze
and Produce shall receive phase configurations that omit or explicitly deny every
connector MCP tool.

### FR-8: Run-scoped MCP facade

OpenCode shall access connectors through one Nexus-owned MCP facade implemented in
the knowledge-base MCP server codebase. The facade shall expose only semantic tools
authorized by the run grant, for example:

- `list_evidence_sources()`;
- `describe_evidence_source(source_id)`;
- `aggregate_runtime_events(source_id, entity_refs, time_window, group_by, limit)`;
- `search_runtime_events(source_id, question, entity_refs, time_window, severities,
  limit)`;
- `get_evidence_item(evidence_ref)`.

Provider URLs, credentials, raw query DSL, concrete index names not approved by the
binding, and arbitrary headers shall not be MCP inputs.

The MCP tool catalogue returned to OpenCode shall contain only tools available to
the current run. This reduces context use and prevents tool discovery from leaking
the existence of unrelated connectors.

### FR-9: Phase-specific OpenCode enforcement

The provider shall generate a separate OpenCode configuration for each invocation.
Gather shall deny all unspecified actions, permit only required workspace reads and
writes, permit the exact local validation command pattern, and permit the authorized
Nexus connector tools. Web search, web fetch, arbitrary shell, external directories,
and unrelated MCP servers shall be denied.

Analyze and Produce shall omit the connector server and deny connector tool patterns.
This remains required even though the same OpenCode session ID continues across
phases. Explicit deny rules must be tested against the pinned OpenCode version because
`--auto` approves otherwise-askable actions but does not override explicit denies.

### FR-10: Smart evidence planning

Connector use shall occur in a dedicated internal Gather sub-step after current
static investigation has produced canonical code entities and before Analyze
contracts are built.

Nexus shall provide OpenCode with:

- canonical code entity references and aliases;
- current investigation findings;
- documentation obligations relevant to runtime behavior;
- available semantic capabilities and their usage guidance;
- source freshness and budgets.

OpenCode shall formulate evidence questions rather than arbitrary vendor queries.
Examples include:

- Does this unit appear in recent runtime events?
- Which failure families recur for this code entity?
- Are endpoints or service names observed at runtime consistent with code?
- Is the deployed behavior different from the repository's current structure?

The expected strategy is progressive:

1. identify high-value questions from code;
2. describe the approved source only when field/profile information is needed;
3. aggregate first;
4. retrieve a few representative samples only when the aggregate needs explanation;
5. stop when the question is answered, evidence is inconclusive, or the budget is
   exhausted.

### FR-11: Evidence envelope

Every successful provider call shall return a provider-neutral `EvidenceEnvelope`:

```json
{
  "evidence_id": "ev_...",
  "run_id": 123,
  "project_id": "assetcheck",
  "connector_instance_id": "con_...",
  "source_id": "src_...",
  "capability_id": "runtime.events.search",
  "question": "Which timeout failures affect Load_Workorders?",
  "entity_refs": ["unit:Load_Workorders"],
  "observed_at": "2026-08-20T12:00:00Z",
  "source_window": {"from": "...", "to": "..."},
  "items": [],
  "summary": {},
  "limitations": [],
  "redaction": {"applied": true, "count": 2},
  "truncation": {"applied": false},
  "digest": "sha256:..."
}
```

The envelope schema shall be stable across providers. Provider-specific data may
appear only in a bounded namespaced metadata object and may not be required by the
documentation pipeline.

### FR-12: Reconciliation and handoff

OpenCode shall write a connector finding that references evidence IDs, canonical
code entities, a classification, and an explanation. Deterministic validation shall
reject findings that reference unknown entities, unissued evidence IDs, evidence
from another run/project, expired evidence, or unsupported negative conclusions.

The accepted reconciled artifact shall be added to the relevant Analyze contract's
`evidence_artifacts`. Analyze and Produce shall not call connectors. Existing prompt
renderers, document catalogue, validation, cleanup, and publication remain in force.

### FR-13: Elastic query construction

The Elasticsearch adapter shall build Query DSL server-side from typed semantic
inputs. V1 shall support:

- mandatory bounded time range;
- filters over approved source-profile fields;
- entity anchors from code such as unit, flow, job, service, endpoint, or correlation
  identifiers;
- severity filters;
- aggregate grouping by approved logical dimensions;
- bounded representative event samples;
- source field inclusion only for approved evidence fields.

The adapter shall set request timeouts, disable expensive total-hit counting where
unnecessary, cap aggregation buckets, and reject scroll, point-in-time, script,
runtime mapping, write, and arbitrary query operations.

### FR-14: Kibana role

Kibana is an optional context/discovery plane:

- V1: spaces, data views, patterns, runtime fields.
- Later: read-only dashboards, alert rules, and cases after separate permission and
  value review.

Kibana does not proxy all runtime searches. Once a data view is resolved to approved
targets and fields, Nexus queries Elasticsearch directly for actual evidence.

### FR-15: Failure behavior

Connector failure shall not silently become evidence. The Gather artifact shall
record a sanitized limitation such as `source_unavailable`, `authorization_failed`,
`rate_limited`, `query_timeout`, `budget_exhausted`, or `result_truncated`.

By default, documentation generation continues with scanner evidence and a visible
connector limitation. A project may later opt into `required` bindings, but that is
not part of v1.

### FR-16: Audit and observability

For every invocation Nexus shall record:

- run, user, project, binding snapshot, connector, source, and capability IDs;
- normalized query summary without secrets or raw DSL;
- start/end time, provider latency, status, item count, and byte count;
- redaction and truncation counts;
- evidence digest and limitation codes;
- denial reason when authorization or budget prevents a call.

Logs and metrics shall expose connector health and latency without indexing provider
payloads into Nexus logs.

### FR-17: Retention

Bounded evidence envelopes and excerpts may exist under the current run's staging
tree while Gather, Analyze, and Produce execute. Phase cleanup shall delete transient
provider items after publication by default.

The durable audit record shall retain the binding snapshot, invocation receipt,
digests, counts, timestamps, limitations, and evidence-to-claim links. It shall not
retain the raw vendor response. A deployment may configure a short retention period
for redacted excerpts when compliance permits; the default is no durable excerpt
retention.

## 10. Architecture

### 10.1 Ownership boundary

```text
Dashboard
  -> Knowledge-base REST API
     -> Connector registry and encrypted credential store
     -> Project connector bindings

OpenCodeCliProvider
  -> existing static Gather
  -> external evidence Gather sub-step
     -> run-scoped Nexus connector MCP facade
        -> Connector capability broker
           -> Elastic Stack provider
              -> Kibana APIs (optional discovery/context)
              -> Elasticsearch APIs (live evidence)
  -> existing Analyze handoff (artifacts only)
  -> existing Produce handoff (artifacts only)
  -> existing publication and RAG push
```

No connector generation logic belongs in the RAG backend. RAG continues to receive
published markdown through the existing `RagBridge` boundary.

### 10.2 Proposed knowledge-base modules

```text
mcp-server/src/mcp_server/connectors/
  domain.py                 # provider-neutral types and enums
  catalog.py                # trusted provider/capability manifests
  repository.py             # instances, bindings, snapshots, receipts
  service.py                # lifecycle and authorization orchestration
  secrets.py                # encrypted credential strategies
  transport.py              # outbound URL/TLS/timeout/size policy
  broker.py                 # run grant, budgets, dispatch, audit
  evidence.py               # normalization, redaction, envelope validation
  reconciliation.py         # code/runtime relationship classification
  providers/
    base.py
    elasticsearch.py
    kibana.py
  mcp_facade.py             # run-scoped semantic MCP tools

mcp-server/src/mcp_server/api/routes/connectors.py
mcp-server/src/mcp_server/providers/opencode_cli.py
dashboard/src/components/connectors/
dashboard/src/api/client.ts
dashboard/src/types.ts
```

Exact filenames may change during implementation, but provider code must remain
behind the connector domain and must not be placed in prompt or pipeline modules.

## 11. Data model

### 11.1 `connector_instances`

- `id` UUID/string primary key;
- `provider_id`;
- `display_name`;
- `ownership_scope` (`workspace` in v1);
- safe configuration JSON;
- encrypted credential ciphertext and credential type;
- optional separately encrypted Kibana credential;
- enabled/status/health fields;
- provider and redaction-policy versions;
- creator/updater user IDs and timestamps.

Endpoint hostnames may be returned to administrators in safe metadata. URLs with
paths, query strings, or credentials must be sanitized.

### 11.2 `connector_sources`

- connector instance ID;
- opaque source ID;
- provider source type (`index`, `alias`, `data_stream`, `kibana_data_view`);
- safe display label;
- encrypted or access-controlled provider locator;
- normalized field profile;
- discovery timestamp and digest;
- enabled status.

### 11.3 `project_connector_bindings`

- project ID and connector instance ID;
- selected source IDs;
- enabled semantic capability IDs;
- binding-level budgets and maximum time window;
- enabled/status;
- version number;
- creator/updater user IDs and timestamps.

### 11.4 `generation_connector_snapshots`

- generation run ID;
- project and initiating user IDs;
- immutable binding/provider/source profile snapshot;
- grant issue/expiry/revocation times;
- capability and budget snapshot;
- digest.

### 11.5 `connector_invocations`

- invocation and evidence IDs;
- generation snapshot ID;
- capability/source IDs;
- normalized question/query summary;
- timing/status/count/byte fields;
- redaction, truncation, and limitation metadata;
- result digest;
- no raw response body.

## 12. REST API requirements

Recommended management surface:

```text
GET    /mcp/connectors/catalog
GET    /mcp/connectors
POST   /mcp/connectors
GET    /mcp/connectors/{connector_id}
PATCH  /mcp/connectors/{connector_id}
DELETE /mcp/connectors/{connector_id}
POST   /mcp/connectors/{connector_id}/test
POST   /mcp/connectors/{connector_id}/rotate-credential
GET    /mcp/connectors/{connector_id}/sources
POST   /mcp/connectors/{connector_id}/discover

GET    /mcp/projects/{project_id}/connector-bindings
PUT    /mcp/projects/{project_id}/connector-bindings/{connector_id}
DELETE /mcp/projects/{project_id}/connector-bindings/{connector_id}

GET    /mcp/generate-runs/{run_id}/connector-evidence
```

Provider calls used by OpenCode are not exposed as ordinary browser REST endpoints.
They are reachable only through the run-scoped MCP facade and capability broker.

## 13. Default budgets

V1 recommended defaults per documentation run:

- maximum connector calls: 12;
- maximum source-description calls: 2;
- maximum aggregation buckets per call: 25;
- maximum event samples per call: 10;
- maximum event samples per run: 30;
- maximum normalized tool response: 128 KiB;
- maximum total normalized connector evidence: 512 KiB;
- default time window: 7 days;
- maximum time window: 30 days;
- provider request timeout: 15 seconds;
- total connector wall time: 90 seconds.

Connector and project administrators may lower these limits. Raising them above
deployment ceilings requires instance-level configuration.

## 14. Elasticsearch and Kibana API mapping

### Elasticsearch data plane

- Connectivity: cluster info or ping against the configured deployment endpoint.
- Lightweight discovery: `GET /_resolve/index/{name}`.
- Selected source description: `GET /{index}?features=mappings,aliases`.
- Searchability/type discovery: Field Capabilities.
- Evidence: bounded `_search` requests constructed by the adapter.

The Get Index API supports indices, aliases, wildcard expressions, and data streams,
and can return only aliases, mappings, or settings. It requires appropriate index
metadata privilege. The Resolve Index API provides the safer lightweight inventory
step and requires `view_index_metadata`.

### Kibana context plane

- List spaces accessible to the credential.
- List data views in the selected space.
- Retrieve the selected data-view definition and runtime fields.

Kibana is optional and may use a different credential from Elasticsearch. If Kibana
is absent or unauthorized, the operator may configure Elasticsearch source profiles
directly.

## 15. Acceptance criteria

### AC-1: Legacy isolation

Given identical project input and legacy run configuration, adding connector tables
and bindings causes no connector resolution, external call, prompt change, artifact
change, or new failure mode in the legacy provider.

### AC-2: Real Elastic connection

An administrator can save an API-key-authenticated HTTPS Elasticsearch connection,
test it, discover non-hidden accessible sources, and bind at least one source to a
project without entering a pre-known index pattern.

### AC-3: Optional Kibana discovery

When a Kibana endpoint is configured, Nexus can list permitted spaces and data views
and use a chosen data view to suggest its target pattern and runtime fields. When
Kibana is absent, Elasticsearch discovery still works.

### AC-4: Smart live evidence

For an OpenCode run with a bound source, Gather derives at least one question from a
real code entity, performs a bounded aggregate or search, and produces an accepted
finding linked to a real evidence envelope.

### AC-5: No raw export

The run performs no scroll, bulk export, full-index download, or continuous sync. No
raw provider response is present in the database, workspace, OpenCode transcript,
application logs, or published docs.

### AC-6: Phase denial

Against the pinned OpenCode runtime, connector tools succeed only during the
external-evidence Gather sub-step. The same continued session cannot call them during
Analyze or Produce, and an expired/replayed grant is denied.

### AC-7: Scope isolation

A run cannot name or retrieve an unbound connector, project, source, index, data
view, or evidence ID. Cross-project and cross-run attempts are denied and audited.

### AC-8: Credential secrecy

Secrets are encrypted at rest and absent from every API response, error message,
audit row, staging artifact, generated OpenCode configuration artifact, subprocess
argument, and log entry inspected by automated tests.

### AC-9: Bounded failure

Timeout, 401, 403, 404/no source, 429, TLS failure, oversized response, malformed
response, partial results, and connector unavailability produce sanitized limitations
and do not create unsupported claims.

### AC-10: Authority preservation

When runtime evidence conflicts with code, the generated artifact records a
discrepancy. It does not rewrite the scanner fact as though runtime evidence proved a
different implementation.

## 16. Success metrics

- Percentage of connected OpenCode runs that obtain at least one useful accepted
  external finding.
- Percentage of connector calls classified as useful, inconclusive, denied, failed,
  or redundant.
- Median connector calls and bytes per run.
- Median and p95 provider latency.
- Redaction and truncation rates.
- Number of runtime discrepancies surfaced and reviewed.
- Connector-induced generation failure rate.
- Zero confirmed credential or cross-project data leaks.
- No statistically significant legacy run regression.

## 17. Rollout

1. Internal feature flag with management API and Elasticsearch only.
2. Real local/staging Elasticsearch integration test.
3. Optional Kibana spaces/data-view discovery.
4. One internal project-bound OpenCode run with audit review.
5. Limited customer pilot using API-key authentication and explicit source binding.
6. General availability after denial, redaction, retention, and failure gates pass.
7. Add TMC through the same capability broker after its API/auth contract is known.

Kill switches shall exist at instance, connector, project binding, and run levels.
Disabling the feature shall return OpenCode generation to its current behavior.

## 18. Open decisions that do not block implementation planning

1. Exact TMC API endpoints, authentication, and freshness contract.
2. Whether redacted excerpts may be retained briefly for customer audit; default is
   no durable excerpt retention.
3. Whether dashboards, alert rules, and cases provide enough value for the second
   Kibana slice; they are excluded from the first slice.
4. Whether a future organization entity replaces instance/workspace ownership.
5. Whether a binding may later be marked required and fail generation when
   unavailable.

## 19. External references

- Elasticsearch Get Index Information:
  https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-get
- Elasticsearch Resolve Index:
  https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-resolve-index
- Elasticsearch Field Capabilities:
  https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-field-caps
- Elastic Cloud API overview:
  https://www.elastic.co/docs/api/doc/cloud
- Elastic Cloud Hosted control-plane API:
  https://www.elastic.co/docs/reference/cloud/cloud-hosted/ec-api-restful
- Kibana API overview:
  https://www.elastic.co/docs/api/doc/kibana
- Kibana data-view listing:
  https://www.elastic.co/docs/api/doc/kibana/operation/operation-getalldataviewsdefault
- Kibana data-view details:
  https://www.elastic.co/docs/api/doc/kibana/operation/operation-getdataviewdefault
- OpenCode MCP server configuration:
  https://opencode.ai/docs/mcp-servers/
- OpenCode permissions:
  https://opencode.ai/docs/permissions/

