# Nexus Harness Design V0

This document turns the Nexus harness vision into a first design reference. It is not an implementation plan and should not be treated as a committed build sequence.

## Product Thesis

Nexus should become an application-agnostic agentic harness.

The core product should provide the same experience and artifact quality across domains. The installed packs, connectors, rules, and templates define what job Nexus is doing for a specific customer or company.

```text
Nexus Core = generic agentic harness
Packs = domain/job description
Connectors = external systems Nexus can access
Rules = how Nexus reasons and acts in that domain
Artifacts = what Nexus produces
```

For IntoData, Nexus becomes a data integration harness because it has data integration packs, documentation workflows, source scanners, runtime connectors, and log connectors.

For another company, the same Nexus core could become a logistics, marketing, manufacturing, support, or security harness by installing different packs and connectors.

## Primary Wedge

The first product wedge is operational investigation for technical users.

Primary users:

- integration engineers
- data engineers
- developers
- platform/support engineers
- architects

The first high-value experience is:

```text
User asks why something failed.
Nexus scopes the project.
Nexus uses generated docs, source context, logs, runtime context, and available connectors.
Nexus explains the issue with evidence.
Nexus updates documentation automatically if it finds stale or missing documentation.
```

Documentation generation remains a key selling point, but the main product direction is broader than placing Intodoc inside chat.

## Core Product Boundary

Nexus Core should know how to:

- run a chat-first agentic experience
- classify user intent
- ask clarification questions when confidence is low
- maintain project and conversation scope
- select capabilities
- execute workflows
- call tools/connectors
- track activity
- collect evidence
- store entity facts with provenance
- create artifacts
- publish artifacts
- record workflow runs
- enforce permissions and risk tiers

Nexus Core should not hardcode:

- one domain
- one model provider
- one RAG model
- one vector database
- one runtime system
- one log platform
- one scanner workflow
- one documentation target
- one source of truth
- one fixed tool order

Domain-specific concepts belong in packs, provider implementations, templates, rules, or configuration.

## Application-Agnostic Model

Nexus becomes domain-specific through installed packs and connectors.

Example: data integration pack

- entity types: project, integration, workflow, job, table, API, schedule, run, failure
- workflows: investigate failed integration, generate integration docs, compare docs to runtime
- artifact templates: technical docs, runbooks, lineage notes, mapping summaries
- evidence rules: source code for structure, runtime/logs for behavior, docs for interpreted context
- connectors: source repositories, logs, runtime platforms, documentation website

Example: logistics pack

- entity types: shipment, route, warehouse, carrier, delivery event, exception
- workflows: investigate delayed shipment, generate route report, summarize exceptions
- artifact templates: shipment report, carrier performance note, exception summary
- connectors: WMS, TMS, tracking systems, carrier APIs

The harness behavior stays consistent. The domain vocabulary and outputs change.

## Chat As Control Plane

The main chat interface should be the control plane for asking, investigating, generating, updating, and publishing.

Users should be able to ask:

```text
Why did this fail last night?
```

```text
Generate documentation for this repository and publish it.
```

```text
Check whether the documentation matches runtime behavior.
```

```text
Search the available systems for the decision around this API.
```

```text
Update the failure handling section in the docs.
```

The user should not need to know whether the answer comes from vector search, generated docs, source code, runtime metadata, logs, or a connector.

The chat should show summary-level activity by default:

```text
Resolving project scope...
Searching generated documentation...
Checking runtime context...
Reading recent logs...
Collecting evidence...
Updating documentation...
Published 2 pages...
```

This should feel similar to modern tool-using chat products: concise progress is visible in the conversation, with an expandable activity trace for users who want to inspect more detail.

Collapsed view:

```text
Nexus is checking generated docs, logs, and Confluence.
```

Expanded view:

```text
Generated docs: 4 relevant pages found
Logs: 38 matching events found
Confluence: 2 pages found
Slack: 6 related messages found
```

The UI should show what Nexus did, what systems it touched, and what evidence it used. It should not expose hidden model reasoning, raw internal prompts, or every low-level API payload.

## Internal Modes

Nexus should have internal modes even if users do not manually select them.

Initial modes:

- `answer`
- `investigate`
- `generate_docs`
- `update_docs`
- `publish_docs`
- `compare_docs_to_runtime`
- `onboard_project`
- `maintenance`

The intent classifier should choose a mode based on the user message, current scope, available capabilities, and confidence.

This should not become regex routing or a fixed pipeline. The classifier gates clarification and workflow selection, not a hardcoded sequence like "scanner, then topology, then RAG."

## Scope And Clarification

Nexus should make the user's life easier without guessing blindly.

The chat should maintain scope such as:

- current project
- environment
- repository
- customer
- integration/workflow
- time window

When confidence is high, Nexus should proceed. When confidence is low, Nexus should ask a focused clarification question.

Example:

```text
I found three integrations named "customer sync". Which one should I investigate?
```

Scope selection is not a burden. It is a way to reduce wrong tool calls, token waste, and ambiguous answers.

## Capabilities And Providers

Nexus should reason in terms of capabilities, not vendors.

Provider categories:

- `ModelProvider`
- `RetrievalProvider`
- `SourceProvider`
- `RuntimeProvider`
- `LogProvider`
- `DocumentationProvider`
- `CommunicationProvider`
- `PublishingProvider`
- `WorkflowProvider`
- `AuthProvider`

Providers expose low-level primitives:

- `search_docs`
- `search_source`
- `search_logs`
- `get_run_status`
- `get_schedule`
- `get_recent_events`
- `generate_documentation`
- `update_documentation`
- `publish_page`

Nexus composes high-level workflows on top of primitives:

- `investigate_failure`
- `generate_project_documentation`
- `update_existing_docs`
- `compare_docs_to_runtime`
- `onboard_repository`

High-level workflows should live in Nexus or packs, not inside vendor-specific providers. This keeps providers thin and swappable.

## Minimal Connector Contract

A connector is any external or internal system Nexus can talk to through a registered provider.

Examples:

- generated documentation website
- source repository
- log platform
- runtime platform
- Confluence
- Slack
- Teams
- Outlook
- custom API

The minimal connector contract should answer:

- who are you?
- what provider type are you?
- what capabilities do you expose?
- what inputs does each capability need?
- what output shape does each capability return?
- what risk tier applies?
- are you healthy and available?
- how should Nexus authenticate to you?

Example:

```json
{
  "id": "confluence.main",
  "provider_type": "DocumentationProvider",
  "capabilities": [
    {
      "name": "search_documents",
      "input_schema": {
        "query": "string",
        "space": "string"
      },
      "output_type": "evidence_items",
      "risk_tier": "read_only"
    }
  ],
  "health": "available",
  "auth_mode": "admin_configured"
}
```

Nexus should not need to know Confluence-specific behavior in its core. It should only know that a configured connector can search documents and return evidence.

## Capability Registry

Nexus needs a registry of available capabilities and providers.

The registry should answer:

- what tools exist?
- what can they do?
- what inputs do they require?
- what outputs do they return?
- what provider implements them?
- what permissions are required?
- what risk tier applies?
- which packs can use them?

Example:

```json
{
  "id": "logs.production",
  "provider_type": "LogProvider",
  "capabilities": ["search_logs", "get_error_trends"],
  "risk_tier": "read_only",
  "enabled": true
}
```

```json
{
  "id": "docs.website",
  "provider_type": "PublishingProvider",
  "capabilities": ["create_page", "update_page", "publish_page"],
  "risk_tier": "writes_content",
  "enabled": true
}
```

Admins should configure providers through the UI. Files can exist behind the scenes, but the operating model should be admin-managed configuration, not developer-only hardcoding.

## Packs

The word "pack" has two useful meanings that should stay separate.

```text
Documentation Pack = instructions/templates for generating documentation
Domain Pack = broader job description for Nexus in a domain
```

The existing Intodoc-style pack is closest to a documentation pack. It defines how documents are generated: prompts, templates, sections, formatting expectations, and quality rules.

A future Nexus domain pack is broader. It can include documentation packs, but also defines how Nexus understands and works inside a domain.

A domain pack may define:

- domain vocabulary
- entity types
- workflow recipes
- artifact templates
- evidence ranking rules
- connector bindings
- default scopes
- permission policies
- quality checks
- output standards
- suggested prompts or task patterns

Example:

```text
Core workflow: investigate_issue
Data integration pack: investigate failed integration run
Logistics pack: investigate delayed shipment
Marketing pack: investigate campaign performance drop
```

The same core experience should produce high-quality artifacts in each domain because the pack supplies domain meaning.

For IntoData, the initial domain pack would likely include:

- the current documentation generation instructions
- data integration terminology
- project/integration/workflow entity definitions
- documentation artifact templates
- operational investigation workflows
- evidence rules for source, logs, runtime data, and generated docs
- connector preferences for the configured Nexus environment

## Entities

An entity is the thing Nexus believes the user is talking about.

Initial generic entities:

- project
- system
- workflow
- service
- repository
- documentation page
- artifact
- evidence item

Data integration pack entities may include:

- integration
- job
- pipeline
- table
- API
- schedule
- runtime run
- failure event

For the first version, the most important scopes are:

- project
- integration/workflow
- documentation page

Entities allow Nexus to connect source, docs, logs, runtime metadata, and generated artifacts without treating any one platform as the center.

## Entity Facts And Memory

Nexus should store learned facts on entities, but never as context-free truth.

Every stored fact should include:

- claim
- entity reference
- source/provenance
- timestamp
- confidence
- evidence links
- originating workflow run

Example:

```json
{
  "entity": "customer_sync",
  "claim": "Runs daily at 02:00",
  "source": "runtime_provider",
  "confidence": 0.93,
  "observed_at": "2026-07-10T09:00:00Z"
}
```

Stored facts are useful for token efficiency and continuity, but stale or conflicting facts must be detectable.

## Evidence And Truth

Nexus should not have one universal source of truth.

Recommended evidence model:

- source code is authoritative for structure
- runtime systems are authoritative for schedules, runs, and current status
- logs are authoritative for observed behavior and failures
- generated docs are interpreted snapshots
- human docs and communications are claims/context
- vector search is retrieval memory, not truth

When evidence conflicts, Nexus should surface the conflict.

Example:

```text
The generated docs say this workflow runs at 02:00.
The runtime provider shows the current schedule is 03:00.
I used runtime as the fresher source and updated the documentation.
```

Evidence ranking rules should live in packs and registry metadata, not hardcoded application logic.

## Documentation

Intodoc remains a major capability and selling point.

Its role in the harness:

- generate documentation
- update documentation
- publish documentation
- provide structured context for investigations
- create operational memory for Nexus

Documentation should be human-editable, but generated and updated by Nexus.

Automatic publishing is allowed, but automatic should not mean blind overwrite. Documentation updates should be:

- versioned
- tied to entities
- tied to workflow runs
- supported by evidence
- reversible
- visible in activity history

Generated documentation may include:

- purpose
- static structure
- dependencies
- inputs and outputs
- schemas
- runtime schedules
- recent behavior
- common failure modes
- owners or teams
- evidence/citations
- confidence notes

For now, the publishing target is the existing Nexus documentation website only. External publishing targets such as Confluence or SharePoint can be future connectors.

Automatic documentation update can start as a chat-triggered action, not a background autonomous behavior.

Example:

```text
User uploads a file and says:
Update doc X using this file.

Nexus:
- identifies doc X
- reads the uploaded file
- extracts relevant facts
- identifies affected sections
- updates the documentation page
- publishes it to the website
- records the uploaded file as evidence
- links the workflow run to the new document version
```

This can mature later into proactive stale-doc detection, but the first operational meaning is simple: users can ask Nexus to update documentation using provided or connected evidence.

## Workflow Runs

Every meaningful chat-triggered workflow should have a persistent run record.

A workflow run record is the receipt for work Nexus performed.

Example:

```text
User says:
Generate docs for this repo.

Nexus starts:
workflow_run_id = run_123
```

The run record exists so Nexus can debug, audit, resume, summarize, link artifacts, and explain why a page or answer exists.

Stored internally:

- workflow run id
- user
- session
- project/scope
- intent
- mode
- tools/capabilities used
- provider calls
- status
- timestamps
- artifacts created
- documentation updated
- evidence collected
- errors
- token/cost metadata if useful

Shown to the user:

- status
- summary of what Nexus is doing
- tool/provider categories used
- result
- evidence links
- documentation pages created or updated
- errors or clarifications needed

Workflow runs should be cancelable.

## Operational Investigation

For operational investigation, Nexus should try to identify:

- target project/system/integration
- environment
- time window
- symptom
- latest relevant runs/events
- last known good state
- first known bad state
- main error signature
- related documentation
- related source/topology
- likely cause
- confidence level
- evidence used
- suggested next action
- whether documentation needs updating

Not every answer needs to show all fields, but the workflow should gather enough structure to support a useful answer and durable evidence trail.

## Risk Tiers

Nexus should be able to act automatically, but actions should be classified by risk.

Suggested tiers:

- `read_only`: search docs, read source, query logs, read runtime metadata
- `draft_content`: generate draft text or proposed documentation
- `writes_content`: update or publish documentation
- `external_notify`: send messages or notifications
- `mutates_external_system`: change state in an external platform
- `destructive`: delete, overwrite without versioning, disable, or remove resources

Current boundary:

- allowed: query systems, trigger scanner/documentation workflows, update docs, publish docs
- not allowed yet: modify application code, deploy code, change infrastructure, mutate external business systems

Documentation writes can be automatic if versioned, evidence-backed, and reversible.

## User Experience Surfaces

The recommended product shape is separate surfaces with one navigation model.

- chat is the control plane
- scanner dashboard shows scanner runs
- documentation website shows published docs
- RAG/search views can remain specialized
- chat deep-links to the surfaces where results live

This avoids merging every UI before the harness exists.

Example:

```text
Scan started from chat -> visible in scanner dashboard
Docs generated -> visible on documentation website
Investigation completed -> visible in chat with evidence links
```

## First Demo Scenario

The strongest first demo should prove that the main chat interface can orchestrate the important Nexus subsystems end to end.

```text
User:
Investigate this integration, check recent logs, search Confluence and Slack for related context, then generate or update the documentation.

Nexus:
- identifies or asks for project scope
- resolves the relevant entity/workflow
- searches generated documentation
- checks source/topology context if available
- queries the configured log provider
- searches configured knowledge/documentation connectors such as Confluence
- searches configured communication connectors such as Slack
- optionally checks runtime context if available
- summarizes the evidence
- explains the likely cause or current state
- triggers documentation generation or update
- publishes the result to the Nexus documentation website
- links to the updated documentation page and evidence used
```

This proves:

- chat as control plane
- tool/capability selection
- provider access
- evidence collection
- entity resolution
- documentation as operational memory
- automatic documentation updates
- application-agnostic harness behavior
- orchestration across multiple existing Nexus subsystems

## Explicit Non-Goals For Now

- Nexus should not modify application code.
- Nexus should not deploy code.
- Nexus should not change infrastructure.
- Nexus should not mutate external business systems.
- Nexus should not rely on MCP as the first connector mechanism.
- Nexus should not merge all UI surfaces before the harness model is proven.
- Nexus should not encode data integration as a core-only assumption.
- Nexus should not treat vector search as the only retrieval path.
- Nexus should not treat generated documentation as the only source of truth.

## Open Design Questions

These are not blockers for the vision, but they must be answered before implementation:

- What exact fields belong in the first documentation pack schema?
- What exact fields belong in the first domain pack schema?
- How should admin-managed connector configuration be stored?
- What is the first log/runtime provider used to prove the model?
- How are documentation versions and rollback represented?
- How are entity facts expired or revalidated?
- How does the classifier estimate confidence?
- What cost/token controls exist for long-running workflows?
