# Nexus Harness Progress Log

Last updated: 2026-07-11

This file tracks how far the current implementation is compared with
`NEXUS_HARNESS_VISION.md` and `NEXUS_HARNESS_DESIGN_V0.md`.

The end-state goals BEYOND the 16-slice plan (live connectors, entity facts
memory, proactive behavior, multi-domain proof, production hardening) live in
`NEXUS_HARNESS_NORTH_STAR.md` - check it at every slice completion.

It is intentionally architecture-facing. It is not a raw git changelog.

## Target Architecture In One Sentence

Nexus should become an application-agnostic, chat-first agentic harness where
installed packs, connectors, rules, and templates define the job Nexus performs,
while the core experience stays the same across domains.

Current wedge:

```text
chat -> planner -> scope resolution -> capability registry -> workflow run
     -> provider/tool call -> evidence/activity/result -> answer/artifact
```

## What We Did In The Exploratory/Design Session

We clarified the product direction:

- Nexus is not just a RAG chatbot.
- Intodoc remains a major selling point, but it should be a capability/workflow
  that Nexus can trigger, not the place where every system is hardcoded.
- Nexus should become the harness/control plane for documentation generation,
  runtime/log investigation, connector reads, doc updates, publishing, and
  evidence-backed answers.
- The architecture must be application-agnostic: data integration is only one
  installed domain shape. Different packs/connectors should let the same Nexus
  core work for logistics, marketing, manufacturing, support, security, etc.

Files produced:

- `NEXUS_HARNESS_VISION.md`
- `NEXUS_HARNESS_DESIGN_V0.md`
- `.plans/nexus-vision-qa-report.md`
- `.plans/capability-aware-chat-planner/01_grill_summary.md`
- `.plans/capability-aware-chat-planner/02_prd.md`
- `.plans/capability-aware-chat-planner/03_slices.md`
- `.plans/capability-aware-chat-planner/pipeline_status.md`

Important design decisions:

- Chat is the control plane.
- Nexus core reasons in capabilities, not vendors.
- Providers expose low-level primitives.
- Packs compose domain meaning and higher-level workflows.
- Workflow runs are the durable receipt for meaningful work.
- Evidence must carry provenance; vector search is retrieval memory, not truth.
- Documentation remains human-editable but can be generated, updated, and
  published by Nexus when evidence and workflow receipts exist.

## What We Built Before This Session

The previous builder pass created the first real harness rails.

Implemented:

- Generic harness domain contracts:
  - capability definitions
  - provider types
  - risk tiers
  - capability health
  - workflow run status
  - activity event status
  - evidence item/result shape
- In-process capability registry with enabled/healthy capabilities.
- Workflow run persistence:
  - workflow run table/model/migration
  - activity event table/model/migration
  - repository/service layer
- Harness API routes:
  - list capabilities
  - list connector metadata
  - inspect workflow runs
- Chat integration:
  - harness dispatch from normal conversation routes
  - streaming `harness_activity` events
  - persisted assistant/user messages for harness turns
- MCP documentation generation provider:
  - calls MCP `/generate`
  - forwards access-token cookie
  - sanitizes provider errors
- Evidence and investigation scaffold:
  - search indexed docs
  - list generated docs metadata
  - query runtime evidence
  - read connector metadata
  - produce evidence-backed operational investigation answers
- Documentation update from explicit evidence:
  - chat detects update-from-evidence requests
  - MCP endpoint updates one markdown doc from explicit evidence
  - writes receipt/version data
  - pushes changed doc back to RAG
- Frontend harness activity UI:
  - parses `harness_activity`
  - renders collapsible safe details
  - keeps runtime tool progress separate

This proved the core shape, but live testing showed chat was still not truly
capability-aware:

- Capability questions still fell through to RAG and could answer falsely.
- Unscoped generation produced a brittle hardcoded response.
- Scoped generation failed in Docker because backend used container-local
  `localhost` for MCP.
- Chat did not consistently expose planner/scope/capability/provider activity.

## What We Built In This Session

This session implemented all slices from the capability-aware chat planner.

### Slice 1 - Documentation Capability Connectivity

Done:

- Backend Compose now sets:

```text
MCP_DOCS_BASE_URL=http://mcp-server:8002/mcp
```

- Added a regression test checking the Compose environment.
- Live E2E proved scoped generation no longer fails with
  `All connection attempts failed`.

### Slice 2 - Live Capability Self-Awareness

Done:

- Chat recognizes capability questions such as:
  - `u can make documentation or not?`
  - `can you generate documentation?`
  - `what can you do?`
- Answers are generated from the live capability registry, not stale RAG docs.
- Capability answers do not call MCP or create workflow runs.

Live result:

```text
Yes. I can generate project documentation through the documentation-generation capability.
```

### Slice 3 - Project Scope Slot Filling

Done:

- Unscoped generation asks which project to use.
- Clarification lists accessible projects.
- No workflow run is created for clarification-only turns.
- If one project is already scoped, Nexus proceeds.
- If the user names an accessible project in text, Nexus resolves it.

Live result:

```text
Which project should I use to generate documentation?
I can see: `cdc`, `cdc_job_route`, `cdc_reverse`, `IntodataStreams`, ...
```

### Slice 4 - Structured Planner For Docs Generation

Done:

- Added a deterministic structured planner boundary with actions:
  - `answer_direct`
  - `ask_clarification`
  - `call_capability`
  - `use_rag`
- Existing RAG documentation questions still route to RAG.
- Natural generation phrasings route to the docs-generation capability.

Important caveat:

- This is not yet a model-backed planner. It is a deterministic planner shaped
  like the future planner contract. The execution guard is server-owned.

### Slice 5 - Tool Result Response Writer

Done:

- Successful generation answer is based on the MCP result.
- Provider failure answer uses sanitized error fields.
- Final response includes workflow id and artifact links.
- Stale RAG read-only answers are not used for capability turns.

Live result:

```text
Documentation generation started for `IntodataStreams`.

- Generation status: `/mcp/generate-runs/IntodataStreams`
- Project documentation: `/projects/IntodataStreams/docs`
- Workflow run: `3`
```

### Slice 6 - Streaming Planner And Activity UX

Done:

- Stream emits safe harness activity for:
  - planner decision
  - scope resolution
  - capability selected
  - provider trigger
  - provider result
- Frontend normalizes generic activity status aliases.
- Expanded activity filters unsafe details.
- Runtime tool events remain separate.

Live streaming result:

```text
event: harness_activity
event: token
event: usage
event: done
```

### Slice 7 - E2E Harness Smoke Procedure

Done:

- Added repeatable smoke procedure to `deploy/SMOKE-TEST.md`.
- Live local smoke was run against the rebuilt stack.

Live E2E evidence:

- Created conversation `35`.
- Capability question answered from registry.
- Unscoped generation listed visible projects.
- Scoped generation for `IntodataStreams` created workflow run `3`.
- MCP showed new generation run `37`.
- Workflow run `3` stored:
  - `planner.decision`
  - `capability.selected`
  - `scope`
  - `provider_trigger`
  - `provider_result`
- MCP docs endpoint returned 11 published docs.

## Current Maturity Compared To The Architecture

| Architecture Area | Target State | Current State | Maturity |
|---|---|---|---|
| Chat as control plane | Chat can answer, investigate, generate, update, publish, and call connectors | Chat can answer capability questions, ask scope, generate docs, update a doc from explicit evidence, run a basic investigation, read connector metadata | Partial but real |
| Capability registry | Live registry of provider-agnostic capabilities with permissions, risk, health, packs | In-process registry exists with provider type, risk tier, health, permissions, packs metadata | Strong V0 |
| Provider abstraction | Swappable providers behind common contracts | MCP docs provider exists; runtime evidence and connector reads are scaffolded; no generic provider plugin/config lifecycle yet | Partial |
| Minimal connector contract | Connectors expose identity, provider type, capabilities, inputs, outputs, risk, auth, health | Runtime source metadata and connector inventory exist; no universal connector registration/admin contract yet | Early |
| Workflow runs | Every meaningful workflow has persistent receipt, activities, artifacts, errors | Implemented for docs generation, doc update, investigation, connector inventory | Strong V0 |
| Activity UX | Concise visible tool activity with expandable safe details | Implemented for harness streams and persisted workflow activities | Strong V0 |
| Project scope | Nexus maintains scope and asks focused clarification | Project-id scope resolution works for chat/UI/text; no richer environment/integration/time-window scope yet | Partial |
| Planner | Planner sees message, scope, capabilities, projects and chooses answer/clarify/tool/RAG | Deterministic planner with strict decision shape; not yet model-backed | Contract exists, intelligence early |
| RAG as capability | RAG is one capability among others, not the whole assistant | RAG remains normal path for doc Q&A; harness routes capability turns away from RAG | Partial |
| Evidence model | Evidence has provenance, source type, confidence, timestamps, conflicts | `EvidenceItem` exists with source/type/title/summary/metadata; operational answers cite evidence refs; no full entity-fact store yet | Partial |

## LangGraph Docs Workflow Update - 2026-07-11

After the initial capability-aware harness pass, we completed the first real
LangGraph-backed workflow slice.

Implemented:

- Added `langgraph>=1.2.9` to the backend dependency set.
- Replaced the placeholder runtime boundary with a real local
  `LangGraphRuntimeProvider`.
- The graph uses `StateGraph` with nodes for manifest loading, capability
  selection, executor call, observation collection, and finalization.
- Docs-generation chat turns now run through LangGraph before calling the
  Nexus guarded executor.
- The graph cannot call MCP directly; provider execution still goes through
  `HarnessCapabilityExecutor`.
- Runtime requests now carry step-limit and timeout budgets, and timeout/error
  paths return sanitized `runtime.failed` activity.
- Workflow runs now persist runtime provider/thread/run/current-node metadata,
  checkpoint metadata, artifact receipts, and graph activity events.
- Missing project scope now creates an interrupted, resume-ready workflow
  receipt instead of only returning a one-off clarification response.

Live smoke evidence:

- Rebuilt backend container with the real LangGraph dependency.
- `Generate documentation for cdc_job_route` through the browser-facing stream
  route created workflow run `5`.
- Run `5` stored `runtime_provider_id=langgraph.nexus`,
  `runtime_thread_id=harness-run-5`, `current_node=finalize`, checkpoint
  metadata, and generation/docs artifact links.
- MCP accepted generation run `38` for `cdc_job_route`.
- `Generate documentation for me` created interrupted workflow run `6` with
  `current_node=await_project_scope`, `resume_token=harness-run-6`, and
  `runtime_metadata.resume.ready=true`.

Validation:

- Focused backend graph/API tests: `40 passed`.
- Full backend suite: `1006 passed`.
- Frontend harness activity tests: `19 passed`.
- Frontend production build: `PASS`.
- Final backend container rebuild completed and `/health` returned 200 with
  database, Qdrant, and Hindsight healthy.

Important remaining gap:

- The planner is still deterministic before entering the graph. The next
  architecture move is Slice 6 plus a model-backed planner node: true
  interrupt/resume semantics and more intelligent orchestration while keeping
  Nexus-owned policy and executor boundaries.
| Entity facts/memory | Facts are stored against entities with provenance/confidence/staleness | Not implemented as first-class entity fact store; project/doc/runtime evidence is transient per workflow | Not yet |
| Documentation generation | Generate/publish human-editable docs through chat | Chat-triggered generation works and reaches MCP; website docs links returned | Strong for first capability |
| Documentation update | Evidence-backed, versioned, reversible doc updates | Explicit evidence-text update path exists; no file upload parsing yet; no rich section patching workflow yet | Partial |
| Automatic doc update | Nexus updates stale docs based on evidence and workflow outcomes | Manual chat-triggered evidence update exists; proactive stale-doc update not yet | Early |
| Operational investigation | Uses docs, source, runtime, logs, connectors to explain failures with evidence | Basic investigation workflow uses indexed docs, generated docs metadata, runtime evidence; external logs/connectors not mature yet | Early V0 |
| Packs | Domain packs define vocabulary, workflows, evidence ranking, templates, connector bindings | Documentation packs exist in Intodoc/MCP world; Nexus domain packs are still conceptual | Not yet |
| Application agnosticism | Same core works for different domains via packs/connectors/rules | Harness code uses generic capability/provider language; first concrete provider is docs/MCP; domain packs not first-class yet | Directionally aligned |
| Risk tiers/permissions | Actions classified and governed by risk/policy/HITL | Risk tiers exist in registry and workflow metadata; full policy/HITL approval model not implemented | Partial |
| Model/provider agnosticism | No hardcoded model or truth assumptions | Some model/provider configuration exists, but planner is deterministic and older RAG/model defaults remain | Partial |
| Admin-managed configuration | Providers/connectors configured through UI/admin, not code | Runtime source admin exists; capability registry itself is code-defined | Early |

## Overall Assessment

We are no longer at "simple chatbot with isolated subsystems."

We now have the first working harness loop:

```text
user asks in chat
-> Nexus plans
-> Nexus resolves/asks for scope
-> Nexus selects a live capability
-> Nexus creates a workflow receipt
-> Nexus calls an existing subsystem/provider
-> Nexus streams/persists activity
-> Nexus answers from actual tool result
```

That is a meaningful step toward the architecture.

But we are not yet at the full Nexus harness vision.

The architecture is currently around:

```text
25-35% implemented as product architecture
60-70% proven for the documentation-generation wedge
10-20% implemented for fully application-agnostic packs/connectors
```

The main reason: the core execution rails exist, but Packs, connector contracts,
entity memory, model-backed planning, evidence ranking, and admin-managed
provider configuration are not first-class yet.

## What Is Actually Demoable Now

Demoable today:

1. Ask `what can you do?`
2. Ask `u can make documentation or not?`
3. Ask `generate documentation for me pls`
4. Ask `generate documentation for IntodataStreams`
5. Inspect workflow run/activity receipt.
6. Inspect MCP generation run.
7. Show project docs link.
8. Stream a capability question and show harness activity.

This proves the first capability-aware chat path.

## What Still Needs To Exist Before Nexus Feels Like The Full Harness

Priority next architecture steps:

1. Make Packs first-class.
   - Separate documentation packs from domain packs.
   - Define pack-owned workflows, vocabulary, evidence rules, artifact templates,
     and connector bindings.

2. Replace deterministic planner with a model-backed planner behind the same
   strict schema.
   - Planner output must remain validated by backend execution guards.
   - Invalid/unsafe plans must fail safe.

3. Formalize the connector/provider contract.
   - Identity
   - provider type
   - capabilities
   - input/output schemas
   - risk tier
   - health
   - auth mode
   - permission requirements

4. Add admin-managed provider/connector configuration.
   - Registry entries should not require code edits for every connector.

5. Add entity memory/fact store.
   - Facts must include entity, claim, source, timestamp, confidence, evidence,
     and workflow run.

6. Mature operational investigation.
   - Logs, runtime schedules, recent failures, generated docs, source/topology,
     and external connectors should all be evidence sources.

7. Mature documentation updates.
   - File upload parsing.
   - Section-aware patch proposals.
   - Versioning/revert UX.
   - Evidence diff/approval where needed.

8. Add policy/HITL controls by risk tier.
   - Read-only can run automatically.
   - Documentation writes can be automatic only when versioned and reversible.
   - External notifications/mutations need explicit policy.

## 2026-07-11 LangGraph-Readiness Foundation

After rotating the architecture around `HYBRID_WITH_LANGGRAPH_PRIMARY`, we built
the prerequisite backend rails for LangGraph orchestration.

Implemented:

- Runtime-safe capability manifest:
  - hides provider ids and raw provider metadata from runtime-facing tool views
  - filters by health, permissions, and packs
  - exposes `/api/v1/harness/manifest`
- Guarded capability executor:
  - validates capability existence, availability, input schema, permission,
    project scope, risk tier, and approval requirements
  - returns sanitized observations
  - routes documentation generation through the executor
- Workflow/activity spine extensions:
  - runtime provider/thread/run metadata
  - current node and resume token fields
  - budget metadata
  - evidence refs and artifact receipts
  - activity runtime node/tool-call metadata
- Minimal runtime boundary:
  - `AgentRuntimeProvider` protocol
  - deterministic `LangGraphRuntimeProvider`
  - proves runtime can only call Nexus capabilities through the guarded
    executor
- User-facing capability answer correction:
  - no raw capability IDs in normal chat answers
  - real API path now filters by user permissions

Important boundary:

```text
LangGraph is not integrated as a dependency yet.
The codebase is now ready to add it behind the runtime provider boundary.
```

Next architecture step:

```text
Add real LangGraph dependency
-> implement graph/checkpoint wrapper behind LangGraphRuntimeProvider
-> move one docs-generation workflow onto the graph
-> add interrupt/resume for missing project scope
```

## Current Validation Snapshot

Most recent validation from the LangGraph-readiness foundation:

- Backend focused manifest/executor/runtime/workflow/chat tests:
  `29 passed, 18 warnings`.
- Backend harness/conversation regression tests:
  `59 passed, 18 warnings`.
- Full backend suite:
  `1003 passed, 38 warnings`.
- Frontend stream/activity/message tests:
  `19 passed`.
- Frontend production build:
  passed.

## Current Branch/Working Tree Note

Work is on branch:

```text
spike/nexus-agentic-harness
```

There are many uncommitted files from the broader harness spike. Do not treat
the current diff as a small isolated patch. It includes prior harness rails plus
the current capability-aware planner slices.
