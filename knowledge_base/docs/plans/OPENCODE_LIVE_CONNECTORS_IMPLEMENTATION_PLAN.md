---
title: OpenCode Live Connector Evidence - Implementation Plan
document_type: engineering_plan
status: proposed
version: 1.0
created: 2026-08-20
prd: ../product/OPENCODE_LIVE_CONNECTORS_PRD.md
delivery_style: standard engineering plan, not LFE
---

# OpenCode Live Connector Evidence - Implementation Plan

## 1. Outcome

Deliver a production-capable, provider-neutral connector platform inside the
knowledge-base MCP server, then prove it with a real Elastic Stack connection used
only during OpenCode Gather. Keep the legacy provider and established Analyze,
Produce, validation, publication, and RAG boundaries unchanged.

The implementation is divided into independently verifiable slices. Each slice must
meet its exit criteria before the next slice relies on it. Unit tests may mock HTTP
transport, but the final release gate requires real Elasticsearch and Kibana services
plus an approved deployment smoke test.

## 2. Current code anchors

The plan is grounded in these existing modules:

- `mcp-server/src/mcp_server/providers/opencode_cli.py`
  - `OpenCodeTaskContract.allowed_tools` is the existing contract field.
  - `_gather_contracts()` owns current static Gather tasks.
  - `_phase_prompt()` already distinguishes Gather from Analyze/Produce.
  - `_execute_step()` invokes a new OpenCode process for each step while preserving
    the session ID.
  - `_finalize_gather()` deterministically validates and materializes investigation
    artifacts.
- `mcp-server/src/mcp_server/services/claude_service.py`
  - resolves `opencode-cli`, constructs `DocGenContext`, and owns generation run
    lifecycle.
- `mcp-server/src/mcp_server/db/models.py`
  - owns projects and generation runs in the shared PostgreSQL database.
- `mcp-server/src/mcp_server/api/app.py`
  - applies JWT authentication and registers dashboard REST routers.
- `mcp-server/src/mcp_server/api/permissions.py`
  - provides project access and project-management checks.
- `mcp-server/src/mcp_server/utils/encryption.py`
  - provides the existing Fernet encryption boundary.
- `dashboard/src/components/GenerateDialog.tsx`
  - already distinguishes Legacy Pipeline from OpenCode.
- `dashboard/src/api/client.ts`, `dashboard/src/types.ts`, and
  `dashboard/src/hooks/useProjects.ts`
  - are the existing dashboard data-access pattern.

## 3. Non-negotiable invariants

1. No code path under the legacy custom provider resolves or calls connectors.
2. The connector domain lives under the knowledge-base MCP server, not RAG.
3. OpenCode never receives provider credentials, base URLs, or raw query DSL.
4. Connector tools exist only in the external-evidence Gather sub-step.
5. Analyze and Produce consume immutable accepted artifacts only.
6. Project/source scope is enforced server-side, not through prompts.
7. External responses are redacted and bounded before model access.
8. Raw vendor responses are never persisted.
9. A connector failure is visible but non-fatal by default.
10. The first release works against a real Elastic Stack deployment.

## 4. Delivery slices

## Slice 0 - Baseline and boundary tests

### Purpose

Freeze current OpenCode and legacy behavior before connector code is introduced.

### Work

- Add characterization tests proving:
  - legacy generation never reads connector configuration;
  - current OpenCode Gather, Analyze, and Produce contract shapes;
  - Analyze and Produce contracts have no allowed connector tools;
  - the same OpenCode session continues across separately launched phase calls;
  - `--auto` is currently active and therefore explicit deny rules are required.
- Record the exact pinned OpenCode version and supported CLI/config contract.
- Add a feature flag such as `MCP_OPENCODE_CONNECTORS_ENABLED=false`.

### Primary files

- `mcp-server/tests/test_providers/test_opencode_cli.py`
- `mcp-server/src/mcp_server/config.py`

### Exit criteria

- Focused baseline tests pass without production behavior changes.
- Turning the feature flag off guarantees byte-equivalent connector-free contract
  indexes for representative OpenCode runs.

## Slice 1 - Provider-neutral connector domain

### Purpose

Create stable abstractions before writing Elasticsearch code.

### Work

- Add domain models:
  - `ConnectorProviderManifest`;
  - `ConnectorCapability`;
  - `ConnectorInstanceConfig`;
  - `ConnectorSourceProfile`;
  - `ProjectConnectorBinding`;
  - `RunConnectorSnapshot`;
  - `CapabilityGrant`;
  - `EvidenceQuestion`;
  - `EvidenceItem`;
  - `EvidenceEnvelope`;
  - `ConnectorLimitation`.
- Add a provider protocol with methods for test, discover, describe, and execute
  semantic capability.
- Add a code-owned registry/factory. Provider modules self-register through explicit
  imports; no filesystem plugin loading in v1.
- Encode trusted metadata for risk, read-only status, accepted entity refs, schemas,
  budgets, and phase eligibility.
- Reject duplicate provider/capability IDs and any run capability not declared
  read-only and Gather-compatible.

### Suggested files

- `mcp-server/src/mcp_server/connectors/domain.py`
- `mcp-server/src/mcp_server/connectors/catalog.py`
- `mcp-server/src/mcp_server/connectors/providers/base.py`
- `mcp-server/tests/test_connectors/test_catalog.py`
- `mcp-server/tests/test_connectors/test_domain.py`

### Exit criteria

- A fake in-test provider can be registered and invoked through the generic protocol.
- Invalid manifests and non-read-only capability grants fail closed.
- No Elastic-specific field is required by the generic evidence envelope.

## Slice 2 - Persistence, encryption, RBAC, and audit

### Purpose

Make connector configuration durable and safe before live calls exist.

### Work

- Add SQLAlchemy models and Alembic migration for:
  - connector instances;
  - connector sources;
  - project connector bindings;
  - generation connector snapshots;
  - connector invocations.
- Keep credential ciphertext separate from safe configuration JSON.
- Reuse the existing Fernet root key through a connector-specific secret service.
- Add credential rotation with replacement, never readback.
- Add `view_connectors` and `manage_connectors` permission checks using the shared
  roles/permissions tables.
- Reuse project access/project management checks for bindings.
- Add audit events for create, update, test, rotation, binding, disable, and delete.
- Add repository/service methods with optimistic binding version increments.

### Suggested files

- `mcp-server/src/mcp_server/db/models.py`
- `mcp-server/alembic/versions/027_live_connectors.py`
- `mcp-server/src/mcp_server/connectors/repository.py`
- `mcp-server/src/mcp_server/connectors/secrets.py`
- `mcp-server/src/mcp_server/connectors/service.py`
- `mcp-server/src/mcp_server/api/permissions.py`
- `mcp-server/tests/test_connectors/test_repository.py`
- `mcp-server/tests/test_connectors/test_secrets.py`
- `mcp-server/tests/test_connectors/test_permissions.py`

### Exit criteria

- Database inspection confirms no plaintext secret columns.
- API/service reads never return credential material.
- Project members cannot mutate bindings; owners and authorized admins can.
- Cross-project connector snapshot creation fails.
- Audit rows contain safe IDs and diffs but no secret values.

## Slice 3 - Connector management REST API and dashboard

### Purpose

Provide a usable self-service management surface instead of operator-only environment
variables.

### Backend work

- Add connector request/response schemas.
- Add `/mcp/connectors` catalogue and lifecycle routes.
- Add test, discover, and credential-rotation routes.
- Add project binding routes.
- Register the router under the existing JWT dependency.
- Return structured safe errors for auth, TLS, reachability, permission, and provider
  failures.

### Dashboard work

- Add a Connectors page with All/Connected/Not connected filters.
- Add an Elastic Stack connect/edit dialog.
- Add status, last check, scope, and project binding indicators.
- Add Project Settings binding panel.
- Add OpenCode-only connector summary and per-run disable option to Generate dialog.
- Add Zod/runtime schemas following existing API client conventions.
- Never store credentials in browser local storage or rehydrate credential inputs.

### Suggested files

- `mcp-server/src/mcp_server/api/routes/connectors.py`
- `mcp-server/src/mcp_server/schemas.py`
- `mcp-server/src/mcp_server/api/app.py`
- `dashboard/src/components/connectors/*`
- `dashboard/src/api/client.ts`
- `dashboard/src/api/schemas.ts`
- `dashboard/src/types.ts`
- `dashboard/src/hooks/useConnectors.ts`
- `dashboard/src/App.tsx`

### Exit criteria

- An authorized admin can create/edit/disable/delete a safe connector record.
- A project owner can bind approved sources but cannot view or alter credentials.
- An unauthorized user receives 403 for management operations.
- UI and API tests prove secrets are write-only.

## Slice 4 - Hardened outbound transport

### Purpose

Centralize network policy so every future provider inherits the same controls.

### Work

- Build an async `httpx` transport wrapper with:
  - HTTPS requirement;
  - URL normalization and embedded-credential rejection;
  - DNS/IP validation;
  - explicit private-host approval;
  - metadata/link-local/loopback denial;
  - redirects disabled or revalidated;
  - standard and custom-CA TLS verification;
  - connect/read/total timeouts;
  - response byte cap while streaming;
  - bounded retry for idempotent transient responses;
  - authorization header redaction;
  - safe error taxonomy.
- Ensure error sanitizer handles Elastic/Kibana error shapes without retaining bodies.

### Suggested files

- `mcp-server/src/mcp_server/connectors/transport.py`
- `mcp-server/src/mcp_server/connectors/errors.py`
- `mcp-server/tests/test_connectors/test_transport.py`

### Exit criteria

- Tests cover DNS rebinding-sensitive resolution, redirects, private hosts, metadata
  endpoints, custom CA, timeout, oversized bodies, 401/403/429/5xx, and header leaks.
- No provider can instantiate an unrestricted client outside the shared transport.

## Slice 5 - Real Elasticsearch provider

### Purpose

Implement the first real data-plane connector without hardcoded customer schemas.

### Connection and discovery work

- Implement API key and Basic auth strategies, with API key as UI default.
- Test the configured Elasticsearch deployment endpoint.
- Discover sources using `_resolve/index/*` with open, non-hidden defaults.
- On selected sources only:
  - call Get Index Information with `features=mappings,aliases`;
  - call Field Capabilities for candidate and operator-selected fields;
  - detect timestamp/message/service/severity/environment candidates;
  - let the operator override every detected field.
- Normalize indices, aliases, and data streams into connector source profiles.

### Runtime capability work

- Implement semantic capabilities:
  - `evidence.sources.describe`;
  - `runtime.events.aggregate`;
  - `runtime.events.search`;
  - `runtime.events.get_item`.
- Construct Query DSL server-side from typed inputs.
- Enforce mandatory time ranges, allowlisted fields, approved targets, sample and
  bucket caps, request timeout, and source filtering.
- Prohibit scroll, PIT, scripts, runtime mappings, raw query fragments, and all write
  endpoints.
- Return opaque run-bound evidence references, never unrestricted index/doc IDs.

### Redaction and normalization

- Normalize common Elastic Common Schema fields where present.
- Preserve custom fields only when they are approved in the source profile.
- Apply existing credential redaction plus log-specific token, authorization, URL
  user-info, and high-risk value filters.
- Cap every string and drop binary/structured payload fields not required for the
  evidence question.

### Suggested files

- `mcp-server/src/mcp_server/connectors/providers/elasticsearch.py`
- `mcp-server/src/mcp_server/connectors/evidence.py`
- `mcp-server/tests/test_connectors/test_elasticsearch_provider.py`
- `mcp-server/tests/integration/test_elasticsearch_live.py`

### Exit criteria

- Integration tests run against a real Elasticsearch service, create a test data
  stream/index, discover it, describe it, aggregate it, and retrieve bounded events.
- Tests prove an unapproved index expression and raw DSL are impossible inputs.
- Captured requests contain no write methods.
- Captured artifacts and logs contain no raw response or credentials.

## Slice 6 - Optional Kibana discovery companion

### Purpose

Use Kibana to understand customer source organization without making Kibana a
dependency for runtime search.

### Work

- Extend the Elastic Stack configuration with optional Kibana endpoint, credential,
  and space.
- Support credential reuse or a separately encrypted Kibana credential.
- Implement:
  - list accessible spaces;
  - list data views in a space;
  - get a selected data-view definition;
  - normalize target patterns and runtime fields into source-profile suggestions.
- Use only documented Kibana APIs; never read `.kibana` directly.
- If Kibana is unavailable, retain Elasticsearch connection health and allow manual
  source profiles.
- Defer dashboards, alert rules, and cases.

### Suggested files

- `mcp-server/src/mcp_server/connectors/providers/kibana.py`
- `mcp-server/tests/test_connectors/test_kibana_provider.py`
- `mcp-server/tests/integration/test_kibana_live.py`

### Exit criteria

- A real Kibana integration test discovers a space and data view created by the
  fixture.
- A Kibana failure does not break direct Elasticsearch discovery/search.
- Separate and reused credentials both pass secrecy tests.

## Slice 7 - Run-scoped capability broker and MCP facade

### Purpose

Expose only authorized semantic evidence tools to OpenCode without exposing vendor
integration details.

### Work

- Add immutable generation connector snapshots.
- Implement short-lived signed/opaque capability grants and nonce replay protection.
- Add atomic budget accounting for calls, time, item count, response bytes, and total
  evidence bytes.
- Implement the connector MCP facade using the existing `mcp` Python dependency.
- Return a tool catalogue derived only from the current grant.
- Dispatch tools through the broker and provider registry.
- Persist invocation receipts before returning results.
- Revoke the grant and close/disable the facade after Gather.

### OpenCode configuration work

- Generate per-invocation OpenCode configuration outside model-readable staging.
- Gather configuration:
  - default deny;
  - allow bounded staging reads/writes required by contracts;
  - allow only the exact Gather validation command form;
  - deny general bash, webfetch, websearch, external directories, and unrelated MCP;
  - configure only the run-scoped Nexus connector MCP server.
- Analyze/Produce configuration:
  - no connector server;
  - explicit deny for connector tool patterns;
  - retain only existing workspace operations needed by those contracts.
- Make `_execution_env`, `_command`, and `_execute_step` accept the phase config.
- Remove name-prefix classification as the authorization mechanism. Retain any
  operator allowlist only as an additional ceiling over trusted capabilities.

### Suggested files

- `mcp-server/src/mcp_server/connectors/broker.py`
- `mcp-server/src/mcp_server/connectors/mcp_facade.py`
- `mcp-server/src/mcp_server/connectors/grants.py`
- `mcp-server/src/mcp_server/providers/opencode_cli.py`
- `mcp-server/tests/test_connectors/test_broker.py`
- `mcp-server/tests/test_connectors/test_mcp_facade.py`
- `mcp-server/tests/test_providers/test_opencode_cli_permissions.py`

### Exit criteria

- Real pinned-OpenCode tests prove authorized Gather calls succeed.
- Analyze and Produce calls fail even with the continued session ID.
- Expired, revoked, replayed, cross-run, and cross-project grants fail.
- `--auto` cannot bypass explicit denies.
- The model-visible workspace and event stream contain no grant or provider secret.

## Slice 8 - Smart Gather integration and evidence reconciliation

### Purpose

Make connector use valuable while preserving the existing documentation method.

### Work

- Keep current static Gather contracts unchanged and connector-free.
- After `_finalize_gather`, build external-evidence contracts from:
  - facts manifest and canonical entity IDs;
  - static investigation findings;
  - required document obligations;
  - project binding/source descriptions;
  - capability `use_when` and `do_not_use_when` metadata;
  - remaining run budgets.
- Add one bounded external-evidence task per relevant group or a deterministically
  selected project-level task when cross-group evidence is more useful.
- Instruct OpenCode to aggregate first, sample second, avoid redundant questions, and
  stop on sufficient or inconclusive evidence.
- Require output to reference evidence IDs already issued by the broker.
- Deterministically validate entity/evidence/run/project relationships.
- Classify findings as corroboration, operational context, discrepancy, or
  inconclusive.
- Materialize accepted artifacts under a connector evidence directory in staging.
- Add only the relevant accepted artifact to each Analyze contract's
  `evidence_artifacts`.
- Keep Analyze and Produce prompts/tool access otherwise unchanged.

### Suggested files

- `mcp-server/src/mcp_server/connectors/planner.py`
- `mcp-server/src/mcp_server/connectors/reconciliation.py`
- `mcp-server/src/mcp_server/providers/opencode_cli.py`
- `mcp-server/tests/test_connectors/test_planner.py`
- `mcp-server/tests/test_connectors/test_reconciliation.py`
- `mcp-server/tests/test_providers/test_opencode_connector_handoff.py`

### Exit criteria

- A code unit produces a bounded evidence question using its canonical ID/aliases.
- An Elastic aggregate is preferred before representative samples.
- Analyze sees accepted evidence artifacts but no connector tools.
- Unknown evidence IDs, entity mismatches, and unsupported negative claims fail
  validation.
- A run without bindings produces the current connector-free behavior.

## Slice 9 - Audit artifacts, run history, cleanup, and operational controls

### Purpose

Make the feature supportable and prove that it does not become a raw export system.

### Work

- Write a sanitized run connector manifest and claim linkage report under `_audit`.
- Add connector summaries to generation run API responses or a dedicated run detail
  endpoint.
- Display status, counts, limitations, time window, and accepted findings in Run
  History.
- Extend Phase 4 cleanup:
  - remove transient evidence items and provider excerpts by default;
  - retain only receipts, digests, counts, timestamps, and claim links;
  - honor configured short excerpt retention if later enabled.
- Add health metrics and safe structured logs.
- Add kill switches at instance, connector, binding, and run levels.
- Ensure resume uses the frozen snapshot and never silently adopts new bindings or
  rotated source profiles mid-run.

### Suggested files

- `mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py`
- `mcp-server/src/mcp_server/api/routes/generate_runs.py`
- `dashboard/src/components/RunHistory.tsx`
- `mcp-server/tests/test_connectors/test_retention.py`
- `mcp-server/tests/test_connectors/test_resume.py`

### Exit criteria

- Published output has durable evidence provenance without raw provider payloads.
- Resume is snapshot-stable.
- Disabling the feature immediately prevents new grants while preserving existing
  audit records.
- Run history explains skipped, failed, denied, truncated, and accepted evidence.

## Slice 10 - Production verification and rollout

### Purpose

Prove the complete path with real services and an actual documentation run.

### Automated verification

- Run backend unit and integration suites for touched modules.
- Run dashboard TypeScript checks, unit tests, and production build.
- Run a real Elasticsearch container integration suite.
- Run a real Kibana container integration suite for spaces/data views.
- Run pinned OpenCode permission and MCP compatibility tests.
- Run legacy non-regression tests.
- Run secret scanning over logs, DB fixtures, staging, and OpenCode event artifacts.

### End-to-end smoke

1. Create an Elastic API key with only `view_index_metadata` and `read` for a test
   pattern.
2. Connect the real endpoint through the dashboard/API.
3. Discover and approve one source without pre-entering its pattern.
4. Bind the source to a project.
5. Run OpenCode generation with a code entity that appears in live events.
6. Confirm at least one accepted external finding reaches Analyze/Produce.
7. Confirm documentation labels the finding as observed runtime evidence.
8. Inspect DB, staging, logs, OpenCode events, and published docs for secret/raw
   response leakage.
9. Attempt connector calls during Analyze/Produce and confirm denial.
10. Disable the connector and confirm the next run remains functional without it.

### Exit criteria

- All automated and live smoke gates pass.
- Security review signs off phase isolation, SSRF controls, redaction, and retention.
- A product reviewer confirms the evidence adds useful information rather than log
  noise.
- Rollback/kill-switch procedure is tested.

## Slice 11 - TMC provider follow-on

### Purpose

Prove provider extensibility after the Elastic path is stable.

### Preconditions

- Agreed TMC API endpoints and supported deployment/version range.
- Authentication contract and credential scope.
- Entity identity mapping between scanner units and TMC artifacts/jobs.
- Freshness requirement: live on-demand read versus recent normalized snapshot.

### Expected semantic mappings

- `runtime.entities.find`;
- `runtime.executions.list`;
- `runtime.executions.get_summary`;
- `runtime.events.search` for bounded logs;
- `runtime.dependencies.get`.

TMC implements the same provider protocol, grants, budgets, evidence envelopes,
reconciliation, and Gather-only policy. No TMC branch is added to Analyze or Produce.

### Exit criteria

- Adding TMC requires a provider adapter, provider tests, and catalogue entry only;
  generic pipeline, REST management, evidence, and OpenCode policy modules remain
  unchanged except for new provider configuration UI fields.

## 5. Test matrix

| Area | Required proof |
|---|---|
| Domain | Manifest validation, schema compatibility, provider independence |
| Persistence | Migration up/down strategy, uniqueness, cascade behavior, versioning |
| Secrets | Encryption, rotation, no readback, no logs/errors/artifacts |
| RBAC | Admin management, owner binding, member use, cross-project denial |
| Transport | SSRF, TLS, custom CA, redirect, timeout, byte cap, retry |
| Elasticsearch | Real discovery, mappings/field caps, aggregate, search, event lookup |
| Kibana | Real spaces/data views, separate/reused auth, optional failure |
| Broker | Grant scope, nonce, expiry, revocation, atomic budgets, audit |
| MCP | Tool catalogue scoping, input validation, provider dispatch, bounded output |
| OpenCode | Gather allow, Analyze/Produce deny, `--auto` denial, continued session |
| Evidence | Redaction, truncation, digest, source/run/entity validation |
| Generation | Smart query progression, accepted handoff, discrepancy behavior |
| Retention | No raw response, transient cleanup, durable receipt only |
| Legacy | No calls, no config dependency, no artifact/prompt behavior change |
| UI | Write-only secrets, permissions, health, bindings, run summary |

## 6. Operational defaults

- Feature flag off until Slice 10 passes.
- API key auth recommended; Basic is fallback.
- Kibana optional.
- Open non-hidden indices/data streams only.
- Project binding required before a source is queryable.
- Default 7-day and maximum 30-day window.
- Aggregate-first strategy.
- Connector failure non-fatal.
- No durable event excerpt retention.
- No dashboards, alerts, cases, Cloud management, OAuth, or personal connectors in
  the first release.

## 7. Migration and rollback

### Migration

- Apply connector tables as additive nullable/new tables; do not alter legacy project
  or generation behavior.
- Add any generation-run relationship as an optional child table, not mandatory
  columns used by legacy runs.
- Seed permissions idempotently.
- Start with no connector instances and feature flag disabled.

### Rollback

- Disable the global connector feature flag.
- Revoke active grants and stop new MCP facades.
- Disable affected connector instances/bindings.
- Leave audit rows and encrypted credentials intact for investigation unless an
  authorized operator explicitly deletes them.
- The legacy and connector-free OpenCode paths continue normally.
- Database table removal is a separate destructive migration and is not part of an
  operational rollback.

## 8. Dependencies and remaining inputs

Implementation can begin with the information available. Final verification needs:

- a reachable Elasticsearch deployment endpoint;
- an API key restricted to approved test targets;
- an optional Kibana endpoint, space, and suitable credential;
- custom CA material if the deployment uses private PKI;
- one project whose code entity can be correlated with real events;
- TMC API/auth details only before Slice 11.

These inputs do not block Slices 0-9. They block only the customer/staging portion of
Slice 10 and the TMC follow-on.

## 9. Recommended first pull request sequence

To keep reviews bounded:

1. PR A: Slices 0-2 - baseline, domain, persistence, encryption, RBAC.
2. PR B: Slices 3-4 - management API/UI and hardened transport.
3. PR C: Slice 5 - real Elasticsearch provider and live integration fixture.
4. PR D: Slice 6 - optional Kibana discovery.
5. PR E: Slices 7-8 - MCP facade, OpenCode phase policy, smart Gather handoff.
6. PR F: Slices 9-10 - audit/cleanup/UI run details and production verification.
7. PR G: Slice 11 - TMC provider after its contract is approved.

Each PR must be independently deployable behind the disabled feature flag until the
final rollout gate passes.

