---
title: Nexus OpenCode Documentation Agent
document_type: product_requirements_document
status: proposed_for_approval
version: 0.1
created: 2026-08-14
product: Nexus
scope: documentation analysis, generation, update, review, and publication
runtime_decision: OpenCode-first, runtime-adapter bounded
---

# Nexus OpenCode Documentation Agent — Product Requirements Document

## 1. Executive summary

Nexus will replace the rigid AI-controlled portion of its documentation pipeline
with an OpenCode-powered documentation agent.

OpenCode will be the primary coding-assistant runtime for documentation work. It
will understand a user's objective, investigate an approved project workspace,
choose what to search and read, call authorized Nexus tools, create candidate
documentation, and revise its work when validation identifies defects.

Nexus will not rebuild OpenCode's planning, context-management, file-navigation,
tool-selection, or editing loop. Nexus will remain the product and deterministic
control plane. It will own users, projects, access control, source ingestion,
redaction, immutable evidence, Documentation Packs, depth requirements, tool
authorization, budgets, audit records, validation, candidate review, publication,
rollback, indexing, and durable memory.

The target is neither a fixed generation pipeline nor an unconstrained coding
agent. It is a governed agentic documentation system:

> Nexus defines the objective, methodology, required depth, evidence rules,
> output contracts, and safety boundaries. OpenCode decides how to perform the
> investigation and satisfy those contracts.

The first product milestone will generate an unpublished, evidence-grounded
documentation candidate for a natural-language target such as “Document only
the work-order functionality.” Targeted requests use Standard depth by default.
Full project onboarding uses Deep depth by default.

## 2. Product vision

Nexus should feel like a capable coding assistant for enterprise knowledge rather
than a form that starts one predefined documentation workflow.

A user should be able to provide a project or select an existing one and state an
objective in ordinary language:

- “Document only the work-order functionality.”
- “Create a technical onboarding guide for this integration project.”
- “Update the runtime section because the schedule changed.”
- “Explain this failure and correct any documentation that is now demonstrably
  stale.”
- “Create operations documentation for the components that write to Elasticsearch.”

OpenCode should explore the available evidence as a coding assistant explores a
codebase: search before reading, follow dependencies, inspect only relevant areas,
use tools when local files are insufficient, record uncertainty, and produce or
modify artifacts iteratively.

Nexus should make the result trustworthy and operationally safe. Every published
artifact must be attributable to an immutable source snapshot, a certified Pack
snapshot, an OpenCode/runtime version, a provider/model route, an evidence ledger,
validation results, and an authorized publication decision.

## 3. Background and current state

Nexus currently has several strong but partially separate foundations:

- IntoDoc scanners that convert supported project sources into normalized facts.
- A legacy multi-phase documentation generator with mature prompts, templates,
  validators, staging, and publication behavior.
- A governed Pack model with Foundation/Core, Functional/Analysis, and
  Style/Presentation layers.
- Immutable source and project-evidence snapshots.
- Evidence, fact, coverage, validation, cost, and artifact receipts.
- Isolated agentic experiment workspaces.
- An `AgentRuntime` boundary with OpenCode and other runtime adapters.
- An OpenCode HTTP client, event normalization, provider/model routing, health
  checks, cancellation, and result collection.
- Scoped Nexus tools for contract discovery, evidence listing, evidence search,
  bounded evidence reads, relationship traversal, artifact writes, and task
  submission.
- Grounded OpenCode execution with role isolation, evidence packets, deterministic
  validators, bounded repairs, candidate artifacts, and non-publishing behavior.
- Candidate publication, document versioning, rollback, and RAG reindexing
  foundations.

The current grounded OpenCode path proves that an external coding-agent runtime can
operate behind Nexus controls. However, its outer behavior remains too predefined:

- Nexus selects a fixed recipe before the runtime understands the objective.
- Recipes prescribe roles, stages, dependencies, and output documents.
- The UI presents several implementation modes rather than one user-centered task.
- “Grounded” execution still schedules a known Investigation → Analysis → Writing
  → Review structure.
- The generated artifact set can remain fixed even when only a narrow subject is
  relevant.
- Dynamic fan-out improves scalability but does not make document selection or
  investigation strategy truly objective-driven.

The new product direction promotes OpenCode from an experimental worker inside a
fixed documentation DAG to the primary documentation-analysis and authoring
runtime. Existing deterministic controls remain and become the safety envelope
around the agent.

This PRD does not make OpenCode the primary brain for all Nexus conversation and
enterprise workflow behavior. It is the primary runtime for source/workspace-heavy
documentation tasks. Other Nexus capabilities may continue to use other governed
runtimes behind their own boundaries.

## 4. Problem statement

### 4.1 User problem

Users cannot express the documentation outcome they actually need without being
forced into a workflow designed around full-project generation. The system may
analyze too much, create irrelevant documents, miss the user's real scope, or
require new hardcoded routes when additional evidence systems are introduced.

The experience feels static even though the underlying models could reason more
flexibly. Users expect behavior closer to a coding assistant: understand the goal,
inspect the project intelligently, use relevant tools, and produce a result suited
to the request.

### 4.2 Product problem

Nexus currently owns both the safety boundary and too much of the AI execution
strategy. Fixed roles, phases, prompt routing, and artifact lists make the system
expensive to extend and cause domain-specific workflow assumptions to accumulate in
the product core.

### 4.3 Quality problem

Removing structure entirely would create a different failure mode: inconsistent
depth, skipped concerns, unsupported claims, unpredictable artifacts, and weak
comparability across projects. A coding assistant must receive a defined
documentation methodology and be held to falsifiable completion criteria.

### 4.4 Operational problem

OpenCode sessions are not durable Nexus workflow authority. Runtime processes can
restart, APIs can drift, models can behave differently, and agent output can be
malformed or unsafe. Nexus must be able to recover, audit, validate, reject, and
publish without trusting an agent transcript as the source of truth.

## 5. Goals

### G-1: Natural objective-driven documentation

Allow users to request documentation using natural language without choosing an
internal phase graph or a predetermined document bundle.

### G-2: Defined and consistent analysis quality

Ensure every run follows a governed documentation methodology with explicit depth,
coverage lenses, evidence requirements, output contracts, and completion gates.

### G-3: Dynamic investigation

Allow OpenCode to choose its search terms, file-reading order, dependency traversal,
Nexus tools, internal work plan, and repair strategy within bounded authority.

### G-4: Selective analysis

Support narrow tasks that inspect only the requested subject and the dependencies
needed to explain it correctly, without requiring complete project analysis.

### G-5: Deep full-project onboarding

Retain a rigorous full-onboarding mode that analyzes all canonical production units
at Deep depth and cannot silently omit difficult or low-evidence areas.

### G-6: Provider and model flexibility

Use OpenCode as the coding-agent harness while allowing any provider/model route
that passes Nexus certification for tool use, context, structured output, cost, and
quality.

### G-7: Evidence-grounded outputs

Require material factual claims, relationships, counts, configuration values, and
operational assertions to resolve to authorized evidence.

### G-8: Safe document lifecycle

Keep generation isolated and non-publishing until validation passes and an
authorized user approves publication.

### G-9: Shared capability surface

Expose existing documentation, runtime, log, topology, retrieval, and future
connectors to OpenCode through governed Nexus tools instead of adding them directly
to the documentation pipeline.

### G-10: Durable correction and improvement

Allow confirmed user corrections and stable preferences to influence later work
with provenance, scope, versioning, and deletion controls.

## 6. Non-goals

The first release will not:

- Build a new Codex-, Claude Code-, or OpenCode-style agent loop inside Nexus.
- Replace Nexus's entire chat runtime or all non-documentation workflows with
  OpenCode.
- Give OpenCode unrestricted access to production filesystems, databases,
  credentials, networks, or enterprise systems.
- Allow the model or OpenCode to grant permissions or approve its own actions.
- Allow autonomous publication without an authorized human decision.
- Modify application source code, deploy code, or mutate external business systems.
- Treat the OpenCode transcript as the durable workflow record.
- Require a fixed set of ten documents for every objective.
- Require every targeted request to inspect the entire project.
- Store hidden chain-of-thought or private model reasoning.
- Guarantee support for every OpenCode version or every provider/model combination.
- Introduce Nexus-owned multi-agent planning. Native OpenCode subagents may be
  evaluated later under inherited constraints.
- Remove the current production generator before the new path passes defined
  quality, safety, and recovery gates.

## 7. Canonical terminology

### Documentation Job

A durable Nexus-owned unit of requested documentation work. It contains the user's
objective, project scope, depth profile, Pack snapshot, evidence snapshot, runtime
route, budgets, status, artifacts, validation results, and publication state.

### OpenCode Session

An execution session created for one Documentation Job or one bounded continuation
of it. It is an implementation detail of the runtime and is not workflow authority.

### Documentation Methodology

The mandatory, provider-neutral process and coverage standard for analyzing source
and producing trustworthy documentation. It defines required outcomes, not every
tool call.

### Documentation Pack

A governed package of methodology additions, document applicability rules,
templates, required sections, evidence rules, validators, quality gates, and style
constraints. It does not contain executable permissions.

### Domain Pack

A broader domain description that supplies vocabulary, entity types, relationships,
evidence preferences, and domain-specific documentation modules. It may select or
extend Documentation Packs.

### Depth Profile

A governed declaration of how deeply the selected scope must be analyzed: Overview,
Standard, or Deep.

### Scope Manifest

The agent-proposed and Nexus-validated record of primary subjects, required
supporting dependencies, explicitly excluded areas, unresolved candidates, and the
evidence used to justify the scope.

### Coverage Ledger

A structured record showing whether each required analysis lens and applicable
document contract is satisfied, not applicable with evidence, blocked, or unknown.

### Candidate Artifact

An isolated, immutable-on-submission output that has not been published or indexed
as active documentation.

### Evidence Reference

A stable reference to an authorized item in an immutable source or project-evidence
snapshot. It is not a raw host filesystem path supplied by the model.

### Confirmed Memory

A user-, project-, or organization-scoped fact or preference explicitly accepted by
an authorized user, stored with provenance and lifecycle controls.

## 8. Product principles

1. **Define outcomes, not keystrokes.** Nexus defines required methodology and
   proof; OpenCode chooses the investigation path.
2. **Search before bulk reading.** The system supplies catalogues and bounded tools
   so targeted work does not require loading the whole project into context.
3. **Evidence before prose.** Documents are accepted only after scope, evidence,
   and coverage artifacts exist.
4. **Dynamic scope, deterministic authority.** The agent may propose scope but
   cannot broaden authorization.
5. **Candidate first.** Agent-generated content is isolated until validation and
   human approval.
6. **No provider-specific domain logic.** Documentation rules live in Packs and
   Nexus contracts, not in OpenCode adapters or model-specific branches.
7. **Durable outside the runtime.** Every state needed to resume, audit, validate,
   or publish survives without the OpenCode transcript.
8. **Honest incompleteness.** Unknown, unavailable, conflicting, and denied evidence
   are represented explicitly rather than filled with plausible text.
9. **Corrections are governed facts, not prompt fragments.** Memory never grants
   access or overrides current evidence silently.
10. **One product experience.** Chat and Generate Docs invoke the same Documentation
    Job capability and show the same status and artifacts.

## 9. Users and roles

### Project contributor

Needs targeted explanations and documentation for an owned project. Can start
read-only/candidate jobs and review results within project permissions.

### Project owner

Can configure project defaults, select allowed Packs, approve document updates, and
publish candidates for the project.

### Documentation reviewer

Reviews scope, evidence, completeness, document diffs, validation findings, and
unknowns before approval.

### Organization administrator

Certifies runtime versions, provider/model routes, Foundation and Functional Packs,
budgets, connector availability, retention, and organization-wide publication
policy.

### Operator or security reviewer

Investigates failures, tool activity, usage, authorization, redaction, runtime
health, recovery, and audit receipts without access to private reasoning.

## 10. Primary user journeys

### 10.1 Targeted generation

1. The user selects a project or uploads a source archive.
2. The user enters “Document the work-order functionality.”
3. Nexus selects Standard depth unless the user or policy requests another level.
4. Nexus performs source safety checks and creates immutable snapshots.
5. OpenCode discovers work-order subjects and necessary supporting dependencies.
6. OpenCode proposes a scope and artifact plan.
7. Nexus validates the proposal against Packs, permissions, and budgets.
8. OpenCode investigates, creates candidate documents, and submits proof artifacts.
9. Nexus validates grounding, coverage, structure, consistency, and secret safety.
10. Failed checks are returned to the same job for a bounded repair.
11. The user reviews the candidate and evidence summary.
12. An authorized user may approve publication.

### 10.2 Full project onboarding

1. The user requests complete project documentation.
2. Nexus selects Deep depth by default.
3. All canonical production units must appear in the scope and coverage ledgers.
4. OpenCode chooses how to partition and investigate the project.
5. The applicable document catalogue is derived from discovered project features.
6. Tests, samples, duplicates, and generated assets may receive lighter treatment
   only when the Pack's tiering rules classify them accordingly.
7. No canonical production unit may be silently dropped because its evidence is
   large, malformed, unfamiliar, or sparse.
8. Publication remains an explicit review action.

### 10.3 Evidence-based document correction

1. The user states that an existing document is incorrect and supplies a correction.
2. Nexus distinguishes a proposed correction from a confirmed durable fact.
3. OpenCode inspects the current document, source evidence, and relevant runtime
   evidence.
4. Conflicts are shown explicitly.
5. OpenCode produces a version-bound candidate patch and supporting evidence.
6. Nexus validates and presents the exact diff.
7. After approval, Nexus publishes atomically and records the prior version for
   rollback.

### 10.4 Runtime-enriched documentation

1. OpenCode determines that source files cannot prove schedule, recent duration,
   deployment state, or observed failures.
2. It calls authorized Nexus runtime or log tools.
3. Nexus returns bounded, sanitized, time-qualified observations.
4. The document separates declared/static behavior from observed/runtime behavior.
5. Runtime claims include observation time and source identity.

## 11. Responsibility boundary

| Concern | OpenCode owns | Nexus owns |
|---|---|---|
| Objective interpretation | Understand the requested documentation outcome | Preserve the original request and authorized project scope |
| Work planning | Choose investigation steps and internal sequencing | Validate proposed scope, artifacts, permissions, and budgets |
| Codebase navigation | Search, list, read, correlate, and revisit relevant evidence | Create the isolated workspace and authorize readable evidence |
| Tool selection | Decide when an available tool is useful | Define, expose, authorize, execute, meter, and audit tools |
| Context management | Select and compact working context | Keep durable evidence and artifact references outside the transcript |
| Documentation method | Apply the supplied methodology | Certify Packs, depth profiles, lenses, contracts, and gates |
| Authoring | Create and revise candidate artifacts | Restrict writes to the candidate workspace and validate outputs |
| Subagents | Optionally use native runtime features when enabled | Decide whether enabled and enforce inherited permissions/budgets |
| Runtime state | Maintain the active session | Own job state, idempotency, recovery, cancellation, and receipts |
| Validation feedback | Interpret findings and repair artifacts | Run deterministic validators and limit repair attempts |
| Publication | Never publish directly | Require approval, publish atomically, index, version, and roll back |
| Memory use | Use relevant supplied memory as context | Store, scope, redact, version, correct, and delete confirmed memory |

## 12. Target architecture

```text
Nexus chat / Generate Docs / API
                 |
                 v
      Documentation Job Service
      - objective and depth
      - project/RBAC resolution
      - Pack and model selection
      - budgets and idempotency
                 |
                 v
       Source and Evidence Gateway
      - scan and redact
      - immutable snapshots
      - searchable catalogue
      - scoped Nexus MCP tools
                 |
                 v
        OpenCode Runtime Adapter
      - isolated workspace/session
      - model/provider route
      - native agent tool loop
      - events, resume, cancel
                 |
                 v
        Candidate Artifact Store
      - scope manifest
      - analysis plan
      - evidence manifest
      - coverage ledger
      - candidate documents
      - self-review report
                 |
                 v
         Nexus Validation Service
      - contract and structure
      - evidence resolution
      - claim grounding
      - coverage and consistency
      - redaction and secret safety
                 |
          pass / bounded repair
                 |
                 v
       Review and Publication Service
      - preview and diff
      - human approval
      - atomic activation
      - RAG indexing
      - version and rollback
```

### 12.1 Runtime boundary

The existing provider-neutral runtime boundary remains the architectural seam.
The production OpenCode adapter must support at least:

- Health and pinned-version compatibility checks.
- Provider/model capability discovery.
- Idempotent session creation.
- Task submission with an immutable objective envelope.
- Safe event streaming.
- Constant-size liveness polling.
- Result and artifact receipt collection.
- Cancellation.
- Bounded reconciliation after runtime or process loss.

OpenCode-specific payloads, endpoints, agent names, tool IDs, provider settings,
and transcript formats must not leak into domain models or frontend contracts.

### 12.2 Workspace boundary

Each Documentation Job receives an isolated workspace containing only:

- The approved project/source snapshot or a read-only projection of it.
- Nexus-generated workspace instructions.
- The compiled Documentation and Domain Pack instructions.
- A safe index/catalogue for discovery.
- Task-scoped MCP configuration or function tools.
- A writable candidate directory.
- A durable handoff directory for manifests and accepted intermediate artifacts.

The workspace must not contain provider credentials, database credentials, hidden
Nexus configuration, unrelated tenant data, previous users' workspaces, or active
published-document directories.

### 12.3 Instruction boundary

Nexus supplies OpenCode with a compact workspace instruction set equivalent in
purpose to an `AGENTS.md` and task-specific skills. It contains:

- The user's unmodified objective.
- The selected methodology and depth profile.
- Mandatory lenses and conditional rules.
- Available tools with semantic descriptions.
- Explicit non-permissions.
- Output and evidence contracts.
- Budget and stop conditions.
- How to report unknowns and conflicts.
- How to submit candidates.

It must not prescribe a complete sequence of file reads, hardcode keywords as
intent routing, or require a fixed artifact bundle unrelated to discovered scope.

## 13. Documentation methodology

The methodology is mandatory. OpenCode may combine, revisit, or internally reorder
the work, but the submitted artifacts must prove that every applicable outcome was
addressed.

### 13.1 Required methodological outcomes

#### Discovery

- Identify project types, technologies, canonical units, source roots, and entry
  points relevant to the objective.
- Search for user terminology, aliases, filenames, entities, and relationships.
- Distinguish production units, shared dependencies, tests, samples, duplicates,
  generated assets, and unknowns.

#### Scope construction

- Identify primary subjects requested by the user.
- Include supporting dependencies only when required to explain the subject.
- Record excluded candidates and the reason for exclusion.
- Record unresolved ambiguities instead of silently choosing when the choice would
  materially change the result.

#### Structural mapping

- Map components, entry points, calls, dependencies, inputs, outputs, data stores,
  external systems, and runtime boundaries applicable to the selected depth.

#### Behavioral analysis

- Explain what the implementation does, how data and control move, where decisions
  occur, and how configuration changes behavior.
- Analyze failure, retry, recovery, and observability behavior at the selected depth.

#### Evidence cross-check

- Compare source, mappings, existing documentation, runtime metadata, logs, and
  confirmed memory when available and relevant.
- Separate static declarations from observed runtime facts.
- Record contradictions and time-qualified observations.

#### Artifact selection and generation

- Select applicable document contracts from the Pack catalogue.
- Produce only documents justified by the scope and applicability rules.
- Preserve user-requested audience and presentation within governed style limits.

#### Self-review and submission

- Check evidence references, coverage, contradictions, unknowns, and output
  structure before submission.
- Submit all required proof artifacts along with candidate documents.

### 13.2 Depth profiles

| Dimension | Overview | Standard | Deep |
|---|---|---|---|
| Default use | Orientation and quick explanation | Targeted generation and focused updates | Full onboarding and explicit deep analysis |
| Scope | Primary components and direct dependencies | Primary components plus dependencies needed for behavior | All canonical production units in scope plus transitive dependencies required for completeness |
| Architecture | Major components and boundaries | Component responsibilities and meaningful interactions | Detailed boundaries, coupling, exceptions, and cross-system implications |
| Control flow | Principal happy paths | Principal paths, branches, triggers, and failure paths | Branch-level behavior, edge cases, retries, recovery, and concurrency where evidenced |
| Data flow | Major inputs and outputs | Sources, targets, transformations, and important schemas | Field-level lineage/mappings where available, data quality behavior, and transformation details |
| Configuration | Key configuration surfaces | Behavior-changing configuration and environment dependencies | Defaults, overrides, precedence, environment differences, and security-sensitive semantics without secret values |
| Runtime | High-level runtime role if known | Schedules, deployments, monitoring, and recent evidence when available | Time-qualified runtime behavior, duration/failure patterns, recovery, and declared-versus-observed contradictions |
| Tests | Mention test strategy if visible | Relevant tests and covered behavior | Important gaps, edge cases, fixture meaning, and confidence implications |
| Evidence | Evidence for material claims | Resolvable evidence for all protected/material claims | Fine-grained evidence and explicit unknowns for every mandatory lens |
| Output | Concise overview artifacts | Focused technical and operational artifacts | Complete applicable catalogue plus per-unit/deep supporting artifacts when required |

### 13.3 Depth-selection rules

- Targeted requests default to Standard.
- Full-project onboarding defaults to Deep.
- Users may request a deeper profile if policy and budgets allow.
- A user may request Overview for exploration, but Overview output must be labeled
  as non-comprehensive.
- Organization policy may raise the minimum depth for regulated projects.
- Style settings cannot lower analysis depth.
- OpenCode may propose an escalation when evidence reveals complexity that makes the
  requested depth misleading.
- Nexus must obtain confirmation before an escalation that materially changes cost
  or elapsed-time budgets.

### 13.4 Mandatory analysis lenses

Each selected subject must be assessed against these lenses. A lens may be marked
not applicable only with an evidence-backed reason.

1. Purpose and business responsibility.
2. Boundaries and ownership.
3. Entry points and triggers.
4. Control flow and decision points.
5. Inputs, outputs, and data movement.
6. Dependencies and external systems.
7. Configuration and environment behavior.
8. Scheduling and orchestration.
9. Errors, retries, recovery, and idempotency.
10. Observability, logs, and operational signals.
11. Security-relevant behavior without secret disclosure.
12. Tests, examples, and confidence.
13. Existing-document agreement or contradiction.
14. Unknowns, assumptions, and evidence gaps.

### 13.5 Conditional document catalogue

Documentation Packs define document types through applicability rules rather than a
fixed list. Each document contract must declare:

- Stable document type ID.
- Purpose and intended audience.
- Applicability conditions.
- Minimum depth.
- Mandatory sections and analysis lenses.
- Evidence requirements.
- Dependencies on other artifacts.
- Template or schema.
- Validators and quality gates.
- Naming and update/merge rules.

Examples:

- Generate a runtime operations document only when runnable or scheduled units are
  present or runtime evidence is connected.
- Generate a mapping/lineage document only when mappings, schemas, or transformations
  are discovered.
- Generate an API or interface document only when callable interfaces are present.
- Generate a failure/recovery runbook when meaningful operational failure paths are
  discovered.
- Generate per-unit deep documents for canonical production units in Deep onboarding,
  while tests and scratch units may be summarized under certified tiering rules.

## 14. Required proof artifacts

Every successful candidate submission must contain structured artifacts in addition
to human-readable documents.

### 14.1 Job contract snapshot

Records the immutable objective, authorized project, selected depth, Pack hashes,
source/evidence snapshot IDs, runtime/version, provider/model, available tools,
budgets, and output boundaries.

### 14.2 Analysis plan

Records OpenCode's proposed subjects, intended investigation areas, likely evidence
sources, expected outputs, and known questions. This is inspectable planning output,
not hidden reasoning or execution authority.

### 14.3 Scope manifest

Records primary subjects, supporting dependencies, excluded candidates, unresolved
items, subject classifications, and scope evidence.

### 14.4 Evidence manifest

Records stable evidence IDs, source snapshot identity, media type, content hash,
subject association, authorization class, and the artifact/claim areas that use the
evidence.

### 14.5 Coverage ledger

Records every required lens and applicable document contract with status:

- `satisfied`
- `not_applicable`
- `blocked`
- `unknown`
- `failed_validation`

Each status requires evidence references or a bounded explanation.

### 14.6 Candidate documents

Contains only files declared in the artifact plan and accepted by the candidate
artifact store. Undeclared files are ignored or rejected.

### 14.7 Self-review report

Records checks performed by OpenCode, known limitations, remaining contradictions,
and the requested terminal disposition. It cannot override Nexus validators.

### 14.8 Validation report

Nexus-generated record of deterministic checks, findings, repair attempts, final
verdict, validator versions, and hashes of checked artifacts.

## 15. Functional requirements

Requirements use MUST, SHOULD, and MAY as normative terms.

### 15.1 Job intake and intent

- **FR-001:** Nexus MUST accept a natural-language documentation objective through
  chat, Generate Docs, and an authenticated API.
- **FR-002:** Nexus MUST preserve the original user objective unchanged in the
  durable Job record.
- **FR-003:** A Job MUST resolve to exactly one authorized project/source scope
  before OpenCode receives project evidence.
- **FR-004:** Nexus MUST support at least `targeted_generation`, `full_onboarding`,
  `document_update`, and `documentation_investigation` job intentions without
  keyword-based production routing.
- **FR-005:** OpenCode MAY interpret the user's desired artifact and scope, but Nexus
  MUST validate the resulting proposal against the effective Pack and policy.
- **FR-006:** If ambiguity would materially change scope, publication target, access,
  or cost, the Job MUST pause with one focused clarification request.
- **FR-007:** Ordinary ambiguity that can be represented as an explicit assumption
  MAY proceed, provided the assumption is visible in the candidate.

### 15.2 Source ingestion and evidence preparation

- **FR-010:** Source archives MUST pass size, archive traversal, file type, and
  redaction checks before runtime access.
- **FR-011:** A failed redaction or trust gate MUST create no OpenCode prompt and
  expose no sensitive value in the response or audit record.
- **FR-012:** Every Job MUST bind to immutable source and Pack snapshots.
- **FR-013:** OpenCode MUST receive logical, stable paths or evidence IDs rather than
  access to arbitrary host paths.
- **FR-014:** Targeted Jobs MUST begin with a searchable catalogue or index so the
  agent can discover relevance without reading every source item.
- **FR-015:** Full onboarding MUST enumerate every canonical production unit in the
  Scope Manifest and Coverage Ledger.
- **FR-016:** Duplicate logical units across roots MUST retain source-root identity
  and MUST NOT be merged accidentally.
- **FR-017:** Nexus SHOULD allow additional approved evidence sources such as
  mappings, tabular exports, existing documents, runtime snapshots, and logs.
- **FR-018:** A Documentation Job MAY include user-supplied supporting evidence such
  as screenshots, error images, log excerpts, PDFs, or spreadsheets only after the
  attachment passes type, size, malware, redaction, and project-scope checks.
- **FR-019:** When the certified OpenCode model route cannot consume an attachment's
  media type directly, Nexus MUST either provide a bounded trusted extraction or
  report the unsupported capability; it MUST NOT silently discard the attachment.

### 15.3 OpenCode runtime and session lifecycle

- **FR-020:** OpenCode MUST be the first production coding-agent runtime for
  documentation Jobs.
- **FR-021:** OpenCode MUST be integrated only through the runtime adapter and
  guarded tool boundary.
- **FR-022:** The deployed OpenCode version MUST be pinned and pass a compatibility
  suite before promotion.
- **FR-023:** Each Job MUST have isolated runtime identity, workspace identity, and
  idempotency keys.
- **FR-024:** Repeating a start request with the same idempotency key MUST NOT create
  duplicate provider work or publication effects.
- **FR-025:** Nexus MUST be able to cancel an active Job and prevent new tool or
  artifact writes after cancellation is accepted.
- **FR-026:** Runtime events MUST be normalized before persistence or UI exposure.
- **FR-027:** Runtime loss MUST NOT cause accepted artifacts, evidence receipts,
  validation state, or publication decisions to disappear.
- **FR-028:** Recovery MUST reconcile durable Nexus state before deciding whether to
  resume an existing OpenCode session or create a replacement session.
- **FR-029:** A replacement session MUST receive a bounded durable handoff; it MUST
  NOT require replaying an unbounded transcript.

### 15.4 OpenCode permissions and native tools

- **FR-030:** OpenCode MUST have read access only to the Job's approved workspace
  projection.
- **FR-031:** OpenCode MUST write only within the candidate and approved handoff
  directories.
- **FR-032:** Direct writes to active documentation, source snapshots, Pack sources,
  or published indexes MUST be denied.
- **FR-033:** Initial production permissions MUST allow bounded file listing, search,
  reading, candidate editing/writing, and approved Nexus tools.
- **FR-034:** General shell and unrestricted network access MUST be denied by default.
- **FR-035:** If bounded shell commands are introduced later, they MUST use an
  allowlist, isolated execution, explicit resource limits, and complete audit.
- **FR-036:** Native OpenCode subagents MUST be disabled for the first production
  milestone. A later release MAY enable them only if they inherit or reduce the
  parent Job's tools, workspace, budget, and evidence scope.
- **FR-037:** Neither an OpenCode agent nor a subagent may create a new permission,
  connector credential, or publication authority.

### 15.5 Documentation Packs and methodology

- **FR-040:** Every Job MUST use a compiled, hash-addressed Foundation/Core Pack.
- **FR-041:** Organization policy MAY require certified Functional/Analysis modules.
- **FR-042:** Style/Presentation overlays MUST NOT reduce analysis depth, evidence
  requirements, required lenses, safety, or output validity.
- **FR-043:** Packs MUST define methodology outcomes, depth rules, conditional
  document contracts, evidence standards, validators, and quality gates.
- **FR-044:** Packs MUST NOT require a fixed document merely because a legacy phase
  generated it; applicability must be tied to objective, scope, or discovered
  project features.
- **FR-045:** Pack compilation MUST reject unknown validators, incompatible
  dependencies, weakened protected invariants, duplicate output identities, and
  unsafe paths.
- **FR-046:** The exact compiled Pack snapshot and hash MUST be recorded on the Job
  and publication receipt.
- **FR-047:** OpenCode MUST be able to read the relevant Pack instructions inside the
  workspace without gaining access to Pack mutation paths.

### 15.6 Dynamic investigation and scope

- **FR-050:** Nexus MUST NOT preselect a complete file-reading order for OpenCode.
- **FR-051:** Nexus MUST NOT require OpenCode to call a fixed sequence of model or
  tool steps when equivalent evidence and outputs can satisfy the contract.
- **FR-052:** OpenCode MUST produce a Scope Manifest before final candidate
  submission.
- **FR-053:** Supporting dependencies MAY expand beyond the user's literal search
  term when required to explain behavior correctly.
- **FR-054:** Scope expansion MUST remain inside project authorization and Job
  budgets.
- **FR-055:** The candidate MUST distinguish primary requested subjects from
  supporting dependencies.
- **FR-056:** Targeted outputs MUST NOT include unrelated deep documentation merely
  because those components exist in the project.
- **FR-057:** Full onboarding MUST fail validation if any canonical production unit
  is absent from the ledger without a certified exclusion classification.
- **FR-058:** OpenCode MUST use evidence tools or local search based on relevance;
  Nexus MUST NOT route tools from hardcoded user-message keywords.
- **FR-059:** OpenCode MAY revisit earlier findings and revise its plan while the
  Job is active; material scope or cost expansion MUST pass policy again.

### 15.7 Nexus tool gateway

- **FR-060:** Every agent-visible Nexus tool MUST have a stable semantic name,
  complete schema, purpose, non-use guidance, preconditions, risk class, output
  contract, and recovery guidance.
- **FR-061:** Tool schemas exposed to OpenCode and schemas enforced by Nexus MUST
  derive from the same authoritative definition.
- **FR-062:** Every tool invocation MUST bind to user, organization, project, Job,
  runtime session, operation ID, allowed tool set, and budget.
- **FR-063:** Unknown, malformed, unauthorized, unavailable, or out-of-scope calls
  MUST execute no provider action and return typed, sanitized feedback.
- **FR-064:** Reuse of an operation ID with different arguments MUST be rejected.
- **FR-065:** Tool results MUST be bounded and sanitized before returning to OpenCode.
- **FR-066:** Connector credentials MUST remain inside Nexus/provider boundaries and
  MUST NOT be written to the workspace or prompt.
- **FR-067:** Initial tools MUST cover contract discovery, evidence listing, evidence
  search, bounded evidence reads, relationship traversal, artifact writes, and
  candidate submission.
- **FR-068:** Subsequent tools SHOULD add existing-document search/read, topology,
  runtime metadata, log search, and confirmed-memory retrieval through the same
  boundary.

### 15.8 Artifact generation and submission

- **FR-070:** OpenCode MUST write documents and proof artifacts only to declared
  candidate paths.
- **FR-071:** Artifact writes MUST enforce path containment, media type, maximum
  bytes, and declared ownership.
- **FR-072:** Candidate submission MUST fail when required proof artifacts are
  missing.
- **FR-073:** Documents MUST use stable identity rules so updates can target an
  existing artifact without relying only on a generated filename.
- **FR-074:** Candidate artifacts MUST be content-hashed and immutable after accepted
  submission; repairs create a new attempt/version.
- **FR-075:** The agent's final chat message MUST NOT be treated as candidate
  completion. Completion is determined from accepted artifacts and Nexus state.
- **FR-076:** Undeclared or temporary OpenCode files MUST NOT be publishable.

### 15.9 Validation and repair

- **FR-080:** Nexus MUST validate source trust, redaction, artifact structure,
  required sections, evidence resolution, claim grounding, coverage, cross-document
  consistency, static/runtime distinctions, security language, and secret safety.
- **FR-081:** Material claims with missing, unauthorized, or non-resolving evidence
  MUST fail validation or be rewritten as explicit unknowns.
- **FR-082:** `not_applicable` coverage entries MUST include evidence-backed rationale.
- **FR-083:** Validation findings returned to OpenCode MUST use stable codes, artifact
  locations, bounded explanations, and safe remediation guidance.
- **FR-084:** Repair MUST continue in the same Job and use accepted durable artifacts;
  it MUST NOT restart from an untrusted candidate state.
- **FR-085:** Repairs MUST be bounded by attempts, elapsed time, tool calls, tokens,
  bytes, and cost.
- **FR-086:** Exhausted repair limits MUST leave the candidate unpublished and show
  actionable findings to the user.
- **FR-087:** Nexus validators remain authoritative even if OpenCode's self-review
  reports success.

### 15.10 Review, approval, publication, and rollback

- **FR-090:** A passing candidate MUST enter `ready_for_review`; it MUST NOT publish
  automatically.
- **FR-091:** Review MUST show objective, selected scope, depth, Pack identity,
  provider/model, evidence summary, coverage, unknowns, validation verdict, usage,
  candidate documents, and diffs against active documents.
- **FR-092:** Publication MUST require an authorized user and an exact candidate
  version.
- **FR-093:** Approval MUST bind to artifact hashes and become invalid if the
  candidate changes.
- **FR-094:** Publication MUST use the existing atomic document activation and RAG
  indexing boundary or an equivalently recoverable transaction.
- **FR-095:** Partial activation MUST fail closed and expose a recovery state.
- **FR-096:** The prior active version MUST remain available for authorized rollback.
- **FR-097:** OpenCode MUST never receive the ability to invoke the final publication
  effect directly.

### 15.11 User experience

- **FR-100:** Generate Docs MUST present one primary agentic experience instead of
  exposing internal experimental runner variants to ordinary users.
- **FR-101:** Users MUST be able to enter an objective and see the selected scope,
  depth, and expected publication behavior before starting.
- **FR-102:** Advanced users MAY override depth and an allowed provider/model route.
- **FR-103:** Normal users SHOULD use organization/project defaults rather than
  selecting separate investigation, analysis, and writing models.
- **FR-104:** Activity MUST show concise states such as preparing evidence,
  investigating, using a named Nexus capability, writing candidates, validating,
  repairing, awaiting input, and ready for review.
- **FR-105:** The UI MUST NOT expose hidden reasoning, raw prompts, credentials,
  unredacted tool payloads, or full provider transcripts.
- **FR-106:** Users MUST be able to cancel, resume when safe, answer a clarification,
  inspect findings, and open candidate artifacts.
- **FR-107:** Chat-triggered and dashboard-triggered Jobs MUST resolve to the same
  durable Job, event, validation, and publication contracts.
- **FR-108:** Job history MUST allow users to distinguish current active docs,
  unpublished candidates, failed attempts, and rolled-back versions.

### 15.12 Memory and corrections

- **FR-110:** A user correction MUST NOT become durable memory solely because it was
  typed in chat.
- **FR-111:** Nexus MUST distinguish proposed corrections, evidence-supported facts,
  confirmed preferences, and disputed statements.
- **FR-112:** Durable memory MUST record scope, subject, statement, provenance,
  confirmer, timestamps, confidence/status, retention, and supersession.
- **FR-113:** Memory MUST NOT grant permissions, satisfy approval, or override newer
  source/runtime evidence.
- **FR-114:** Relevant confirmed memory MAY be supplied to OpenCode as a bounded,
  clearly labeled evidence class.
- **FR-115:** Users with authority MUST be able to inspect, correct, supersede, and
  delete applicable memory.
- **FR-116:** Markdown MAY be an export or workspace projection of memory; it MUST NOT
  be the only authoritative multi-user persistence mechanism.

### 15.13 Audit, usage, and observability

- **FR-120:** Every Job MUST record runtime version, provider/model, Pack hash,
  source/evidence hashes, tool manifest, budgets, timing, tokens, cost when known,
  artifact hashes, validator versions, approval, and terminal reason.
- **FR-121:** Tool activity MUST use stable operation and attempt identifiers.
- **FR-122:** Runtime and validation events MUST be ordered, replayable, and safe for
  reconnecting clients.
- **FR-123:** Audit records MUST contain structured reason and outcome codes, not
  hidden chain-of-thought.
- **FR-124:** Observability failure MUST NOT authorize, duplicate, or reinterpret an
  operation.
- **FR-125:** Operators MUST be able to identify stalls, runtime unavailability,
  budget exhaustion, validation loops, cancellation failures, and publication
  recovery states.

## 16. Documentation Job state model

The durable state model must support at least:

```text
draft
  -> preparing
  -> queued
  -> running
     -> awaiting_user_input -> running
     -> validating
        -> repairing -> validating
        -> ready_for_review
        -> failed
     -> cancelled
  -> approval_required
  -> publishing
  -> published
  -> publication_recovery_required
```

Rules:

- State transitions are performed by Nexus, not inferred from OpenCode prose.
- Terminal runtime completion does not imply validation success.
- Validation success does not imply publication approval.
- Cancellation and publication are idempotent.
- An accepted candidate version cannot return to mutable `running` state; repair
  creates a successor attempt.
- Every state transition records actor, time, expected prior version, and reason.

## 17. Core data contracts

### 17.1 Documentation Job

Minimum fields:

- Job ID and version.
- Organization, project, requester, and authorization context.
- Original objective and optional user constraints.
- Intent and Depth Profile.
- Pack activation/snapshot identity and hash.
- Source and project-evidence snapshot identities and hashes.
- OpenCode/runtime route and compatibility version.
- Provider/model route and certified profile.
- Workspace and session references.
- Tool manifest snapshot.
- Budget limits and consumed usage.
- Status, terminal reason, and timestamps.
- Active candidate attempt and publication reference.

### 17.2 Scope subject

- Stable subject ID and kind.
- Display name and aliases.
- Source-root/logical-unit identity.
- Classification: primary, supporting, excluded, unresolved.
- Inclusion/exclusion rationale.
- Evidence references.

### 17.3 Coverage item

- Methodology lens or document-contract ID.
- Subject IDs.
- Required depth.
- Status.
- Evidence references.
- Artifact references.
- Safe explanation.
- Validator result.

### 17.4 Candidate artifact

- Candidate and attempt IDs.
- Stable document identity and logical path.
- Media type, byte size, and content hash.
- Declaring Pack/document contract.
- Subject IDs and depth.
- Evidence-manifest reference.
- Validation status and findings.
- Base active version for updates.

### 17.5 Publication proposal

- Exact candidate version and artifact hashes.
- Target project/site/index.
- Proposed creates, updates, and removals.
- Diff and validation references.
- Required permission and approver.
- Approval status and expiry.
- Execution and rollback receipts.

## 18. Nexus tool catalogue for OpenCode

### Initial milestone

- `nexus.documentation.get_job_contract`
- `nexus.evidence.list`
- `nexus.evidence.search`
- `nexus.evidence.read`
- `nexus.evidence.traverse_relationships`
- `nexus.artifacts.write_candidate`
- `nexus.documentation.submit_candidate`

### Subsequent capability expansion

- `nexus.docs.search`
- `nexus.docs.read`
- `nexus.topology.query`
- `nexus.runtime.query_metadata`
- `nexus.logs.search`
- `nexus.memory.get_confirmed_context`
- `nexus.documentation.get_validation_findings`
- `nexus.documentation.request_clarification`

Tool naming may be adapted to provider-safe identifiers, but the mapping to stable
canonical capability IDs must be collision-checked and recorded.

No OpenCode-visible tool may directly perform final publication.

## 19. Security and trust requirements

### 19.1 Source and workspace security

- Treat uploaded and connected source content as untrusted input.
- Detect source-contained prompt injection and prevent it from changing tool,
  permission, Pack, or publication rules.
- Mount or copy only approved content into a per-Job workspace.
- Prevent path traversal, symlink escapes, special-device access, and cross-workspace
  references.
- Destroy or quarantine workspaces according to retention policy after durable
  artifacts and receipts are secured.

### 19.2 Credential security

- Keep provider and connector credentials outside the workspace and model context.
- Redact credential-shaped source data before runtime access.
- Never include sensitive values in errors, validation findings, events, metrics,
  prompts, or generated documents.
- Fail closed when approved redaction receipts are absent or mismatched.

### 19.3 Tool and effect security

- Default all new tools to unavailable until registered, typed, risk-classified,
  and covered by policy.
- Bind task tokens to exact Job, project, tools, budgets, and expiry.
- Enforce idempotency before executing provider actions.
- Treat evidence reads as data disclosure operations subject to scope and limits.
- Separate candidate content writes from consequential publication effects.

### 19.4 Runtime supply-chain security

- Pin OpenCode and all adapter dependencies.
- Maintain a compatibility suite against the pinned server API and event behavior.
- Review license, SBOM, known vulnerabilities, container provenance, and update
  impact before promotion.
- Do not consume experimental OpenCode APIs without a versioned adapter fallback or
  explicit release gate.

## 20. Non-functional requirements

### Reliability

- A process restart must not duplicate a Job, provider submission, artifact
  acceptance, approval, or publication effect.
- Candidate and publication state must be recoverable without relying on OpenCode's
  in-memory session store.
- Validation and publication failures must be distinguishable and actionable.
- The current production generator must remain available during migration.

### Performance

- Job creation should acknowledge within two seconds after source upload/ingestion is
  complete.
- After OpenCode accepts a task, users should receive the first safe activity update
  within five seconds under normal local conditions.
- Status polling must not repeatedly download or parse a growing transcript.
- Search and read responses must be bounded by item count, bytes, and elapsed time.
- Cancellation should prevent new runtime/tool work within ten seconds under normal
  service conditions.

These targets exclude third-party provider latency and may be refined from measured
production baselines.

### Scalability

- Workspaces, runtime sessions, budgets, events, and artifacts must be isolated per
  Job and tenant.
- Concurrency must be controlled at organization, project, runtime, provider, and
  Job levels.
- Large projects must be handled through dynamic search, bounded reads, durable
  artifacts, and optional runtime-native delegation rather than one unbounded prompt.

### Portability

- Documentation domain code must depend on the runtime contract, not OpenCode HTTP
  payloads.
- Provider/model identifiers must remain open strings governed by certification.
- Replacing OpenCode later must not require rewriting Packs, evidence contracts,
  validators, candidate publication, or the user-facing Job model.

### Accessibility and UX

- All primary Job actions and status information must be keyboard accessible.
- Status must not rely on color alone.
- Progress text must be user-oriented and must not expose implementation internals
  unless the viewer opens an authorized technical detail panel.

### Cost control

- Every Job must have ceilings for elapsed time, model tokens, tool calls, tool/read
  bytes, repair attempts, and billed cost when the provider supports trustworthy
  reporting.
- Cost escalation beyond the approved budget must pause or terminate rather than
  continue on model discretion.
- Usage must be attributable by Job, project, organization, provider/model, and
  candidate attempt.

## 21. Quality and acceptance criteria

### 21.1 Global safety gates

The release cannot pass if any test produces:

- Unauthorized evidence access.
- Cross-project or cross-tenant data exposure.
- Credential or secret leakage.
- Path escape from the assigned workspace.
- Direct agent publication.
- Duplicate consequential effects.
- Published artifacts whose hashes differ from the approved candidate.
- Unsupported protected claims accepted as grounded.

### 21.2 Targeted-generation acceptance scenario

Given a representative project containing work-order and unrelated functionality,
when the user requests “Document only the work-order functionality”:

- The Job defaults to Standard depth.
- The Scope Manifest identifies the relevant work-order subjects.
- Supporting dependencies are separately labeled.
- Unrelated components are not given deep standalone documentation.
- Every mandatory Standard lens is satisfied, explicitly unknown, or not applicable
  with evidence-backed rationale.
- Material claims resolve to authorized evidence.
- The artifact catalogue is selected from applicability rules rather than a fixed
  ten-document list.
- Candidate output remains unpublished until approval.

### 21.3 Full-onboarding acceptance scenario

Given a representative multi-root project, when the user requests full onboarding:

- The Job defaults to Deep depth.
- Every canonical production unit appears exactly once in planning and coverage
  identities while retaining source-root provenance.
- Tests, samples, duplicates, and scratch units follow certified tiering rules.
- Applicable runtime, mapping, interface, dependency, configuration, failure,
  operations, and security lenses are covered.
- Large or low-evidence units are not silently skipped.
- All selected document contracts pass structure, evidence, consistency, and secret
  validators.

### 21.4 Correction acceptance scenario

Given an existing active document and a user correction:

- The correction is treated as a proposal until evidence or authorized confirmation
  establishes its status.
- The candidate is based on an exact active document version.
- The user sees the proposed diff, supporting and conflicting evidence, and memory
  impact.
- Approval binds to the candidate hash.
- Publication updates the active document and index atomically or rolls back.

### 21.5 Recovery acceptance scenario

If OpenCode or Nexus restarts after one or more candidate artifacts are accepted:

- Nexus reconciles the Job from durable state.
- No provider task or artifact write is duplicated.
- A replacement OpenCode session can continue from bounded durable handoff artifacts.
- Unverifiable runtime-only output is not accepted or published.

### 21.6 Quality evaluation

Before default promotion, the new path must be evaluated against the current best
grounded/legacy baseline using a versioned corpus that includes:

- Targeted requests.
- Full onboarding.
- Multiple source roots with colliding logical names.
- Sparse evidence.
- Oversized units.
- Mappings and tabular sources.
- Runtime/static contradictions.
- Secret-shaped source content.
- Prompt-injection content in source files.
- Cancellation, restart, and repair.

Evaluation must measure:

- Scope precision and omission rate.
- Mandatory-lens coverage.
- Evidence resolution and unsupported-claim rate.
- Cross-document consistency.
- Secret safety.
- Human-rated usefulness and depth.
- Latency, input/output tokens, cache usage, tool calls, and cost.
- Recovery and idempotency behavior.

Default promotion requires zero safety-gate failures and demonstrated quality that
is at least equivalent to the accepted baseline for factuality and mandatory
coverage. Better style cannot compensate for weaker grounding or missing subjects.

## 22. API and event expectations

Exact route names are implementation details, but the stable product surface must
support:

- Create a Documentation Job.
- Read/list Jobs with permission-scoped filters.
- Stream ordered safe Job events with reconnect cursor.
- Provide clarification input to a paused Job.
- Cancel a Job.
- Retrieve scope, coverage, evidence summary, findings, usage, and candidates.
- Request approval for an exact candidate.
- Approve or reject publication.
- Retrieve publication and rollback receipts.
- Roll back an authorized active document version.

Public events should include:

- Job accepted/preparing/started.
- Runtime connected/unavailable.
- Investigation activity summaries.
- Named Nexus tool started/completed/denied/failed.
- Candidate artifact created or revised.
- Validation started/passed/failed.
- Repair started/exhausted.
- Clarification requested/resolved.
- Ready for review.
- Approval requested/approved/rejected/expired.
- Publication started/completed/recovery-required.
- Job cancelled/failed/completed.

Events must never expose raw prompts, private reasoning, credentials, raw tool
arguments, unredacted evidence bodies, or provider transcripts.

## 23. User-interface requirements

### Generate Docs

The normal experience should contain:

- Objective input.
- Project/source selection.
- Suggested depth with an explanation.
- Optional audience/style controls from allowed Style Packs.
- Clear candidate-only versus publication behavior.
- Estimated budget/impact where available.
- One primary start action.

Internal choices such as `single_agent`, `orchestrated`, `grounded`, or a specific
role DAG must not be ordinary product choices after migration. They may remain in an
operator-only experiment/comparison surface until retirement.

### Job activity

The activity view should show what Nexus and OpenCode are doing at a safe level:

- “Searching project evidence for work-order concepts.”
- “Inspecting three supporting dependencies.”
- “Checking existing documentation.”
- “Creating two candidate documents.”
- “Validation found one unsupported runtime claim; repairing.”

It should not show chain-of-thought, raw OpenCode events, low-level API payloads, or
unbounded logs.

### Review

The review experience must make it easy to answer:

- Did the agent understand my objective?
- What did it include and exclude?
- How deep was the analysis?
- What evidence supports the documents?
- What remains unknown or contradictory?
- What changed from the current documentation?
- What will publication affect?
- What did the run cost and which model/runtime produced it?

## 24. Migration and rollout

### Stage 0: Establish the baseline

- Complete a live end-to-end grounded OpenCode run on the current representative
  work-order project.
- Record factuality, coverage, artifacts, validator results, duration, tokens, tool
  calls, and cost.
- Freeze a sanitized regression corpus and accepted baseline artifacts.
- Preserve the current generator and candidate experiment paths.

### Stage 1: Objective-driven OpenCode candidate

- Add the durable Documentation Job envelope.
- Create one isolated OpenCode session with native search/read/candidate-write tools.
- Supply a natural objective, Standard/Deep methodology, and proof contracts.
- Remove the fixed ten-document requirement from this candidate path.
- Support targeted Standard generation and non-publishing preview.
- Keep native subagents and general shell disabled.

### Stage 2: Methodology and conditional catalogue

- Compile Documentation Packs into OpenCode-readable instructions plus Nexus
  validators.
- Add Scope Manifest, Analysis Plan, Evidence Manifest, Coverage Ledger, and
  Self-review contracts.
- Add conditional document applicability and full-onboarding Deep coverage.
- Prove style overlays cannot weaken analysis.

### Stage 3: Enriched Nexus capabilities

- Add existing-document, topology, runtime, log, and confirmed-memory tools.
- Prove connector credentials and unauthorized evidence never enter the workspace.
- Add static-versus-runtime contradiction handling.

### Stage 4: Unified product experience

- Route Generate Docs and chat requests into the same Documentation Job service.
- Replace runner selection with objective/depth/audience controls for normal users.
- Add progress, clarification, review, evidence, and validation experiences.

### Stage 5: Governed publication and living updates

- Connect passing candidates to version-bound approval and atomic publication.
- Add correction confirmation and governed memory.
- Prove rollback and RAG/index consistency.

### Stage 6: Canary and legacy retirement

- Run shadow comparisons and a permission-scoped canary.
- Promote OpenCode documentation Jobs as the default only after acceptance gates pass.
- Retain a rollback window and signed legacy/runtime artifacts.
- Remove legacy AI orchestration only after separate human approval and operational
  evidence.

## 25. Rollback strategy

- The new path remains feature-flagged and candidate-only until publication gates
  pass.
- The current documentation generator remains deployable during the migration.
- OpenCode runtime failure routes new Jobs back to a clear unavailable state; Nexus
  must not silently run a materially different methodology.
- Additive Job, artifact, evidence, and publication schemas must remain readable by
  the rollback version or have explicit compatibility projections.
- Published document rollback uses the stored prior version and corresponding index
  revision.
- Runtime version rollback requires compatibility-suite passage and must not resume
  incompatible sessions blindly.

## 26. Product success metrics

### User value

- Percentage of targeted Jobs accepted without scope correction.
- Percentage of candidates approved with no or minor edits.
- Time from objective submission to reviewable candidate.
- Reduction in irrelevant documents produced for targeted requests.
- Successful answers to follow-up questions using generated evidence.

### Quality

- Mandatory-lens completion rate.
- Unsupported material claim rate.
- Evidence-reference resolution rate.
- Cross-document contradiction rate.
- Human reviewer scores for usefulness, depth, clarity, and scope fidelity.

### Safety and operations

- Unauthorized tool/evidence attempts and blocked outcomes.
- Secret/redaction findings before and after runtime access.
- Duplicate-operation rate.
- Recovery success after restart.
- Validation repair success and exhaustion rates.
- Publication rollback and recovery incidents.

### Efficiency

- Tokens, tool calls, bytes read, duration, and cost per accepted artifact.
- Difference between targeted and full-onboarding resource use.
- Cache effectiveness.
- Percentage of source items actually read for targeted work.

## 27. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OpenCode API or event drift | Runtime integration breaks | Pin version, compatibility suite, isolate adapter, staged upgrades |
| Model behaves inconsistently across providers | Quality and tool reliability vary | Certify provider/model profiles against the same corpus |
| Agent under-scopes targeted work | Incorrect or incomplete documentation | Scope Manifest, dependency rules, required lenses, validation, review |
| Agent over-scopes targeted work | Cost and irrelevant output | Search-first contract, scope budget, primary/supporting distinction, review |
| Fixed Packs recreate the old rigid workflow | Product remains static | Define outcomes and conditional applicability, prohibit fixed tool-call sequences |
| Too little structure lowers quality | Unpredictable depth and omissions | Governed methodology, depth profiles, coverage ledger, quality gates |
| Source prompt injection changes behavior | Unauthorized actions or bad output | Treat source as evidence only, protected instructions, tool policy outside prompt |
| Workspace contains secrets | Data exposure | Scanner/redaction receipt, isolated projection, post-generation secret checks |
| Runtime session loss | Work cannot resume | Durable Job/artifacts/events, bounded handoff, reconciliation before replacement |
| Tool loop consumes excessive resources | High cost or denial of service | Per-Job and aggregate budgets, continuous metering, cancellation |
| Native subagents broaden authority | Scope/security breach | Disabled initially; inherited/reduced permissions if later enabled |
| Candidate passes style but lacks facts | Misleading docs | Factuality and coverage gates outrank style/human polish |
| Automatic memory preserves bad corrections | Persistent misinformation | Explicit confirmation, provenance, conflict status, correction/deletion controls |

## 28. Dependencies

- Stable source scanning and redaction receipts.
- Immutable source and project-evidence snapshots.
- OpenCode service deployment and authenticated runtime adapter.
- Runtime compatibility and model certification harness.
- Task-scoped Nexus tool gateway.
- Pack governance and snapshot compilation.
- Artifact and validation services.
- Durable Job/event persistence and idempotency.
- Candidate review, document versioning, publication, rollback, and RAG indexing.
- Project RBAC and organization policy.

## 29. Resolved product decisions

1. OpenCode is the first production coding-agent runtime for documentation work.
2. OpenCode is not the universal Nexus product brain.
3. Nexus will not build a competing planning/tool loop for documentation.
4. Nexus defines a mandatory methodology and validates its outcomes.
5. OpenCode dynamically chooses navigation, evidence use, tool sequence, and
   authoring approach.
6. Targeted generation defaults to Standard depth.
7. Full project onboarding defaults to Deep depth.
8. Document selection is conditional, not a universal fixed ten-document bundle.
9. Existing scanners, evidence gates, validators, staging, publication, rollback,
   and indexing will be reused.
10. Generated content remains a candidate until an authorized human approves an
    exact validated version.
11. OpenCode is accessed behind a replaceable runtime adapter.
12. Provider/model routes are selectable only after Nexus certification.
13. Initial runtime permissions exclude unrestricted shell, network, and native
    subagents.
14. Confirmed memory is governed Nexus data; Markdown is at most a projection or
    export.
15. The current generator remains a baseline and rollback path until the new system
    passes production gates.

## 30. Open implementation decisions

These do not block agreement on the product direction, but must be resolved before
production implementation reaches the affected boundary:

1. Exact storage schema and retention period for Documentation Jobs and workspaces.
2. Initial certified provider/model route and the minimum model capability matrix.
3. The exact boundary between native OpenCode filesystem search and Nexus evidence
   search for very large projects.
4. Whether the first release supports pause/clarification mid-session or ends the
   attempt and starts a continuation session.
5. The first set of conditional document contracts in the certified Foundation Pack.
6. Quantitative promotion thresholds derived from the baseline evaluation corpus.
7. When, if ever, native OpenCode subagents and bounded shell commands become
   production-eligible.
8. Workspace retention and quarantine policy after failed secret or injection gates.

## 31. Definition of done for the product initiative

The initiative is complete when an authorized user can submit either a targeted or
full-onboarding documentation objective and Nexus can:

1. Prepare safe immutable evidence.
2. Start a pinned OpenCode coding-agent session through the runtime adapter.
3. Supply a governed methodology, depth profile, tools, and budgets.
4. Allow OpenCode to investigate dynamically without a fixed Nexus-owned phase DAG.
5. Produce scope, evidence, coverage, candidate, and review artifacts.
6. Validate the candidate deterministically and perform bounded repair.
7. Present a comprehensible review experience.
8. Publish only an exact approved candidate through an atomic, recoverable boundary.
9. Reindex active documentation consistently.
10. Resume or fail safely across process/runtime loss without duplicate work.
11. Demonstrate at least baseline-equivalent factuality and coverage with zero
    safety-gate failures.
12. Retire the legacy AI orchestration only after explicit human approval and a
    proven rollback window.

## 32. Follow-on expansion beyond documentation

The Documentation Agent is the first bounded use of OpenCode, not the final limit of
the architecture. After this initiative proves the runtime, workspace, tool,
evidence, validation, and recovery boundaries, Nexus may introduce a related Source
Investigation Job.

That future capability would let a user provide an error screenshot or question and
allow OpenCode to inspect relevant project sources, existing documentation, logs,
runtime metadata, topology, and confirmed context before returning an
evidence-grounded explanation. It would reuse the same OpenCode adapter and Nexus
tool gateway, but it would produce an answer or investigation report instead of
requiring documentation artifacts.

This follow-on must remain a specialized capability invoked by Nexus. It does not
require OpenCode to become the universal conversation runtime, and it does not
weaken the permissions, evidence, approval, or audit boundaries defined in this
PRD.
