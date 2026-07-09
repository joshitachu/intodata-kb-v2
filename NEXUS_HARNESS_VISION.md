# Nexus Harness Vision

This note captures the exploratory direction for Nexus before it gets lost in implementation details.

## Core Idea

Nexus should become an agnostic agentic harness for enterprise knowledge, documentation, and operations.

Today, many of the pieces already exist, but they are isolated:

- Intodoc scanner for documentation generation
- topology/context extraction
- RAG pipeline
- vector-based Q&A
- generated documentation website
- source-code-based project understanding
- runtime and operational systems that exist outside the current scanner

The goal is not to make one of these pieces bigger and more hardcoded. The goal is to make Nexus the main harness that can orchestrate all of them through one chat-first interface.

## What Nexus Should Become

Nexus should be the control plane where a user can ask, investigate, generate, publish, and maintain knowledge across many systems.

The chat interface should not only answer questions from a vector DB. It should be able to trigger workflows and use tools:

- generate documentation
- update documentation
- publish documentation to the website
- inspect logs
- query runtime systems
- search source code
- search existing documentation
- search communication platforms
- query operational platforms
- compare static documentation with runtime truth
- explain failures, schedules, dependencies, and changes

In this model, chat is the main user-facing harness, not just a Q&A box.

## Important Product Shift

The old mental model is:

```text
source code / docs -> scanner -> vector DB -> chat answer
```

The desired model is:

```text
user intent
  -> Nexus harness
  -> selects capabilities/tools
  -> gathers context from multiple providers
  -> runs workflows
  -> answers, generates, publishes, or investigates
```

Nexus should feel more like an enterprise Claude Code-style coworker than a static chatbot.

## Intodoc's Role

Intodoc should not become the place where every external platform is hardcoded.

Instead:

```text
Nexus = harness / orchestrator
Intodoc = documentation workflow or capability
RAG = one retrieval capability
Vector DB = one memory/retrieval backend
External systems = pluggable context/tool providers
Website = one publishing target
```

Intodoc can still be very important, but it should become a workflow that Nexus can trigger from chat.

Example:

```text
User:
Generate documentation for the invoicing pipeline and publish it.

Nexus:
- resolves what "invoicing pipeline" means
- gathers source context
- pulls existing docs
- asks runtime/log providers for relevant operational context
- generates documentation
- publishes it to the documentation website
- shows what changed
```

## Agnostic By Default

Nexus should avoid hardcoded product assumptions.

Examples of assumptions to move away from:

- one hardcoded RAG model
- one hardcoded LLM provider
- one hardcoded vector database
- one hardcoded runtime system
- one hardcoded log platform
- one hardcoded documentation platform
- permanent truth stored only in CLAUDE.md or similar context files
- code paths that assume a specific vendor or source is the default truth

The system should treat tools and platforms as interchangeable providers behind common capability contracts.

## Capability Contracts

Instead of building around specific vendors, Nexus should build around capabilities.

Possible provider categories:

- `ModelProvider`
- `RetrievalProvider`
- `SourceCodeProvider`
- `RuntimeProvider`
- `LogProvider`
- `DocumentProvider`
- `CommunicationProvider`
- `PublishingProvider`
- `WorkflowProvider`
- `AuthProvider`

Concrete implementations can then be added without changing the core mental model:

- OpenAI, Anthropic, Kimi, local models, Azure OpenAI
- Elastic, Datadog, Splunk, CloudWatch
- TMC, Airflow, GitHub Actions, Kubernetes, custom runtime APIs
- Confluence, SharePoint, local Markdown, generated Nexus website
- Slack, Teams, Outlook
- vector DBs, SQL stores, graph stores, file indexes

No single implementation should become "the truth" in the architecture.

## Capability Registry

Nexus likely needs a capability registry that answers:

- what tools exist?
- what can they do?
- what inputs do they require?
- what permissions do they need?
- are they read-only or do they mutate state?
- what kind of output do they return?
- which provider should be used for a given task?

Example shape:

```json
{
  "id": "logs.production",
  "type": "LogProvider",
  "capabilities": ["search_logs", "get_error_trends"],
  "risk": "read_only"
}
```

```json
{
  "id": "docs.website",
  "type": "PublishingProvider",
  "capabilities": ["create_page", "update_page", "publish_page"],
  "risk": "writes_content"
}
```

The agent/harness can then reason in terms of capabilities:

```text
I need logs -> use a LogProvider.
I need runtime schedules -> use a RuntimeProvider.
I need to publish docs -> use a PublishingProvider and confirm if required.
```

## Chat As Workflow Trigger

The target experience is that users can do real work through the initial chat interface.

Examples:

```text
Generate documentation for this system and add it to the website.
```

```text
Check the latest failures for this workflow and explain what changed.
```

```text
Search docs, source, and team conversations for the decision around this API.
```

```text
Compare the generated documentation with actual runtime behavior.
```

```text
Find stale documentation and propose updates.
```

The chat should expose activity, not hide it completely:

```text
Resolving target...
Searching source...
Checking available runtime providers...
Reading recent logs...
Generating documentation...
Publishing 3 pages...
Indexing new documentation...
```

## Documentation As A Living Artifact

Documentation should not be only static source-code summaries.

Generated docs can include:

- static structure
- purpose
- dependencies
- inputs and outputs
- schemas
- runtime schedules
- recent operational behavior
- common failure modes
- links to logs or runtime runs
- owner/team/context metadata
- unresolved assumptions
- evidence/citations

The key is that runtime and operational context should be provided through generic providers, not hardcoded into the scanner.

## Entities Matter

Nexus likely needs a stronger entity model so different systems can talk about the same thing.

Possible entities:

- project
- system
- workflow
- job
- service
- API
- table
- dataset
- repository
- source file
- schedule
- runtime run
- failure event
- documentation page
- team or owner

Once entities exist, providers can attach facts to them.

Example:

```json
{
  "entity_type": "workflow",
  "name": "customer_ingestion",
  "source_refs": ["repo/path/..."],
  "runtime_refs": ["provider-specific-id"],
  "log_queries": ["service:customer_ingestion"],
  "docs_refs": ["docs/customer-ingestion"],
  "owners": ["data-platform"]
}
```

This lets Nexus connect source, docs, logs, runtime metadata, and published artifacts without making one platform special.

## Avoiding The Trap

"Agnostic" should not mean abstracting everything before anything works.

The practical path is:

- design the core around generic capabilities
- prove the model with a few real providers
- keep provider implementations replaceable
- avoid baking vendor-specific assumptions into core workflows
- keep configuration and registry records outside hardcoded application logic

For example, one runtime provider can be implemented first, but Nexus should not become centered around that provider.

## North Star

Nexus should become:

```text
an agnostic agentic harness for enterprise knowledge and operations,
where chat is the primary control plane,
tools and providers are pluggable,
documentation generation is a workflow,
and published knowledge stays connected to source, runtime, logs, and evidence.
```

This is bigger than improving the Intodoc scanner.

The real question is:

```text
What is the minimum harness architecture that lets chat trigger workflows,
use pluggable context providers,
and publish outputs back into Nexus without hardcoding one model,
one platform, or one source of truth?
```
