---
title: Nexus Agentic Platform Target Architecture
status: proposed_north_star
updated: 2026-07-20
tracking_plan: pl-179
implementation_status: architecture_only
---

# Nexus Agentic Platform Target Architecture

## Purpose

This document records the complete target for Nexus so the vision is not reduced
to a collection of chat fixes.

The objective is to make Nexus feel like a capable, natural collaborator that
can safely use everything Nexus exposes: documentation, metadata, runtime
evidence, Packs, onboarding, source connections, workflows, updates, and future
capabilities.

The target is not an unrestricted model with database access. It is a genuinely
agentic system in which:

> The model decides what the user means, what to do next, whether to plan,
> whether to ask, which tools or specialists to use, and how to explain the
> result. Deterministic code defines what exists, what is true, what is allowed,
> and whether the proposed action executed safely.

Related recovery artifacts:

- `.plans/agentic-conversation-recovery/01_grill_summary.md`
- `.plans/agentic-conversation-recovery/02_prd.md`
- `.plans/agentic-conversation-recovery/03_slices.md`
- `.plans/agentic-conversation-recovery/plan_critique.md`

Current replacement architecture and implementation plan:

- `.plans/nexus-agent-runtime-replacement/01_grill_summary.md`
- `.plans/nexus-agent-runtime-replacement/02_prd.md`
- `.plans/nexus-agent-runtime-replacement/architecture.md`
- `.plans/nexus-agent-runtime-replacement/03_slices.md`
- `.plans/nexus-agent-runtime-replacement/acceptance_test_matrix.md`
- `.plans/nexus-agent-runtime-replacement/migration_rollback.md`

The recovery artifacts remain historical diagnosis and behavioral requirements.
Their direction to extend the custom bounded loop is superseded by the standard
agent-runtime replacement plan under `pl-179`.

## Executive summary

Nexus already has strong foundations:

- A single bounded agent loop for public conversation traffic.
- A Capability Registry with registered handlers.
- A deterministic Policy Engine.
- Typed tool observations.
- Workflow Ledger state and durable receipts.
- Conversation turn claims and idempotency.
- Durable LangGraph workflows and checkpoints.
- Project scope, RBAC, approval, evidence, time, cost, and tool-call boundaries.

However, the current implementation is not the complete agentic target.

It currently has an agentic execution-selection layer inside an overly
deterministic conversational state machine. Active tasks can force requested
inputs into every response; facts can remove natural response content; normal
answers masquerade as tool calls; provider failures fall into fixed renderers;
and task pause, feedback, long-term planning, governed memory, and delegation are
incomplete or missing.

The work therefore has two levels:

1. **Make the single agent trustworthy and conversationally free.**
2. **Build the full agent platform: planning, feedback, memory, orchestration,
   and governed multi-agent delegation.**

Multi-agent behavior must not be used to hide a confused single-agent core. The
coordinator must first be able to understand, act, recover, and communicate
reliably by itself.

## What “agentic” means for Nexus

An agentic Nexus must be able to:

1. Understand meaning across paraphrases, typos, corrections, and interruptions.
2. Distinguish discussion, guidance, clarification, and requested action.
3. Know which authorized capabilities are available and what they do.
4. Choose and sequence tools based on the objective rather than keyword routes.
5. Observe tool and policy results, correct itself, or replan.
6. Maintain useful working state without trapping the user in a workflow.
7. Create durable plans for genuinely multi-step objectives.
8. Pause, resume, switch, and complete work across turns and restarts.
9. Use relevant memory without treating memory as permission or current truth.
10. Delegate bounded specialist tasks when specialization or parallelism helps.
11. Explain what it did, what it knows, what failed, and what it needs next.
12. Remain grounded, scoped, authorized, bounded, auditable, and recoverable.

Agentic does **not** mean:

- Letting the model invent backend operations.
- Letting the model decide its own permissions.
- Removing schemas, policies, approvals, or workflow invariants.
- Allowing uncontrolled agents to spawn other agents.
- Treating every question as a multi-step plan.
- Replacing deterministic validation with model confidence.

## Current state assessment

| Concern | Current state | Target state |
|---|---|---|
| Intent | Model chooses some actions, but state and response machinery can override relevance | Current-message meaning is model-led; no semantic keyword/state router |
| Planning | Reactive next-action selection | Optional durable goal plans with steps, dependencies, and replanning |
| Feedback | Typed observations return; many denials/errors terminate | Observe, evaluate, correct, replan, clarify, approve, or finish |
| Orchestration | Bounded loop plus predefined durable workflows | Agent plans across capabilities and workflows using one ledger |
| Working memory | Recent messages plus intrusive Conversation State | Compact, relevant, interruptible task and objective context |
| Episodic memory | Messages, receipts, and audits exist separately | Agent-readable summaries of prior actions and outcomes |
| Semantic memory | Hindsight exists but is disabled | Governed user/org preferences and durable learned facts |
| Procedural memory | Capability definitions and Packs | Complete tool contracts, playbooks, and successful patterns |
| Tools | Strong registry; some schemas/names/availability are weak | Stable semantic tools with complete schemas and parity proof |
| Responses | Facts can replace natural content | Natural grounded content with explicit fact references |
| Approvals | Some visible actions are unreachable from chat | Durable proposal, human approval, exact-call resume |
| Multi-agent | No governed chat delegation protocol | Coordinator plus bounded specialist handoffs |
| Evaluation | Strong structural tests, weak real-provider behavior proof | Versioned real-model multi-turn corpus and canary gates |

## Target architecture

```text
User message
  ↓
Conversation + working memory + relevant episodic/semantic memory
  ↓
Coordinator agent
  ├── answer naturally
  ├── ask a contextual clarification
  ├── transition task focus
  ├── call one governed capability
  ├── create/update a durable plan
  └── delegate a bounded specialist task
        ↓
Capability / specialist policy validation
        ↓
Registered handler / specialist runtime
        ↓
Typed observation or typed handoff
        ↓
Progress and feedback evaluation
  ├── continue
  ├── correct arguments
  ├── replan
  ├── ask the user
  ├── request approval
  ├── wait for external work
  └── finish
        ↓
Update plan, task state, ledger, and permitted memory
        ↓
Natural grounded final response
```

## Authority boundaries

### The model owns

- Intent interpretation.
- Current-message relevance.
- Conversational focus.
- Whether a task is simple or requires a plan.
- Clarification strategy.
- Tool or specialist selection.
- Bounded sequence and replanning decisions.
- Which available facts are relevant to the response.
- Natural final wording.

### Deterministic code owns

- Capability and specialist registration.
- Tool and handoff schemas.
- Authentication, RBAC, project scope, and tenant isolation.
- Risk classification and approval requirements.
- Workflow and domain state transitions.
- Plan/task optimistic concurrency.
- Tool, model, time, cost, and delegation limits.
- Idempotency and exact effect receipts.
- Evidence provenance and secret redaction.
- Validation of proposed actions and persisted outcomes.

### The LLM never owns authoritative facts

The model reasons over facts but cannot manufacture:

- Workflow status.
- Tool success.
- Project existence or access.
- Pack revisions or validation state.
- Approvals.
- Evidence provenance.
- Executed effects.

## Decision protocol

Each coordinator decision is exactly one of:

1. **Terminal natural response** — answer using natural content and optional fact
   references.
2. **Contextual clarification** — ask the smallest useful question needed to
   continue.
3. **Task-focus transition** — focus, background, pause, resume, or switch a
   conversational task without inventing a domain effect.
4. **One governed capability call** — invoke one registered operation.
5. **Plan operation** — create, revise, suspend, resume, or complete a durable
   plan.
6. **Specialist delegation** — issue one bounded task envelope to a registered
   specialist.

Capability calls remain sequential by default:

```text
decide → validate → execute → observe → decide again
```

This provides fresh policy and state validation before every action.

Multiple writes in one decision are forbidden. Independent read-only calls or
specialists may eventually execute in parallel, but only through an explicit
fan-out contract with a shared budget, individual scope validation, and
deterministic result collection.

A terminal response is not an operational Nexus capability. Provider-native
structured output may encode the decision, but answering must not pretend to be
a backend effect.

## Removing hardcoded conversational pathways

Production code must not choose semantic behavior from:

- Keywords or regular expressions in the user message.
- Example phrases copied from tests.
- Conversation or project IDs.
- The existence of an active task alone.
- Task type alone.
- Requested-input presence alone.
- A mandatory response-fact list.
- A mode-specific if/elif dispatcher.
- A fixed incident response branch.

Deterministic parsing remains valid for explicit syntax and safety, including:

- Slash commands deliberately invoked by the user.
- IDs, timestamps, time windows, and versions.
- Schema validation.
- Secret redaction.
- Authorization and scope checks.

The distinction is:

> Parse and validate deterministic data. Do not use deterministic text matching
> to decide the user’s semantic objective.

## Grounded natural responses

The current fact catalog concept should be retained but its role must change.

Incorrect model:

```text
facts exist → all mandatory facts must be spoken → model content is discarded
```

Target model:

```text
agent writes natural content
→ identifies authoritative facts used for factual claims
→ backend validates references, safety, and provenance
→ unused facts remain silent context
```

Example:

```json
{
  "decision": "answer",
  "content": "I created an empty Pack draft. I have not added any analysis content yet.",
  "fact_refs": [
    "pack:23:created",
    "pack:23:revision",
    "pack:23:artifact_empty"
  ]
}
```

Raw validator messages such as repeated `Field required` entries never become
ordinary assistant prose. Validation is structured using field paths, codes,
safe hints, and machine-readable constraints. The agent chooses how to explain
or ask about them.

## Interruptible conversation tasks

Domain lifecycle and conversational focus are different authorities.

Example:

```text
Pack draft domain status: active
Conversation task status: paused
```

Required conversational task projections:

- Focused.
- Background.
- Paused.
- Awaiting user input.
- Awaiting approval.
- Waiting for external work.
- Completed.
- Abandoned/cancelled from terminal domain truth.

Expected semantics:

- “Stop asking me about this” pauses or backgrounds the task.
- “Let’s discuss something else” changes focus without deleting work.
- “Continue the Pack” resumes the exact authorized task.
- “Abandon/delete the draft” starts the governed destructive approval path.
- “Cancel generation” invokes workflow cancellation policy, not conversational
  pause.

## Durable planning

Reactive tool selection is not sufficient for long objectives. Nexus needs an
authoritative `AgentPlan` for work that spans multiple steps, turns, workflows,
or specialists.

Example:

```json
{
  "plan_id": "plan-42",
  "goal": "Onboard project X and generate security documentation",
  "status": "executing",
  "version": 3,
  "steps": [
    {
      "id": "resolve-project",
      "objective": "Confirm project identity",
      "status": "completed",
      "depends_on": []
    },
    {
      "id": "attach-source",
      "objective": "Attach the authorized source repository",
      "status": "waiting_for_user",
      "depends_on": ["resolve-project"]
    },
    {
      "id": "scan",
      "objective": "Trigger and reconcile the scan",
      "status": "blocked",
      "depends_on": ["attach-source"]
    }
  ]
}
```

Planning rules:

- Simple questions and single actions do not require a plan.
- The model proposes and revises plans.
- The server validates plan shape, scope, budgets, versions, and legal status
  transitions.
- A plan step does not grant new tool authority.
- Completed tool receipts and Workflow Ledger outcomes are the source of truth
  for step completion.
- A plan can wait for user input, approval, time, or external workflows.
- Replanning records why the previous step or assumption changed.
- Plans survive process restart and conversation interruption.

## Feedback and bounded self-correction

Every action returns a typed observation. The coordinator evaluates the
observation against the current objective or plan and chooses the next step.

Feedback sources include:

- Tool success or failure.
- Schema validation.
- Policy denial or approval requirement.
- Workflow state.
- Evidence availability and quality.
- Conflict detection.
- Plan progress.
- User correction.
- Optional high-risk output evaluation.

Target feedback loop:

```text
propose action
→ validate
→ execute if allowed
→ observe
→ evaluate progress
→ continue | correct | replan | clarify | request approval | finish
```

Denial and non-effectful validation failures should normally return to the agent
while budget remains so it can explain, request approval, or correct arguments.
Unsafe actions remain blocked. A retry never replays an ambiguous write.

## Orchestration

Nexus orchestration has three levels:

### Turn orchestration

Short bounded cycles of decision, capability, observation, and response.

### Workflow orchestration

Durable predefined operations implemented through LangGraph and external
providers, with Workflow Ledger reconciliation, interrupts, and receipts.

### Goal orchestration

The coordinator uses an AgentPlan to combine capabilities, workflows, waiting,
approvals, memory, and specialists into one user objective.

The agent decides which registered workflows contribute to the goal. Workflow
implementations remain deterministic where fixed steps are part of the domain
contract.

## Memory architecture

Memory must be separated by purpose and authority.

### Working memory

Contains the current objective, recent relevant dialogue, focused/background
tasks, current plan, pending approvals, referenced entities, and recent
observations.

Working memory is bounded and rebuilt from authoritative state.

### Episodic memory

Records compact prior events:

- What the user requested.
- Which capability executed.
- What outcome occurred.
- Which correction or approval followed.
- Whether the work was paused or resumed.

Messages, receipts, and audits already contain much of this evidence, but the
agent needs a bounded, relevant projection rather than raw transcript replay.

### Semantic memory

Stores durable learned facts and preferences when policy allows:

- User preferences.
- Organization terminology.
- Stable entity aliases.
- Accepted corrections.
- Preferred output style.

Semantic memory never grants access, approval, or current workflow truth. It has
provenance, scope, retention, correction, and deletion controls. Hindsight may
provide this layer after it is explicitly governed and enabled.

### Procedural memory

Defines how Nexus work is performed:

- Capability contracts.
- Pack instructions.
- Workflow playbooks.
- Organization policies.
- Validated successful patterns.

Procedural memory guides the agent but does not bypass runtime validation.

## Tool architecture

Every agent-visible capability must provide:

- Stable semantic provider name.
- Canonical registry ID.
- User-oriented purpose.
- When to use it.
- When not to use it.
- Preconditions.
- Complete JSON Schema.
- Output and observation contract.
- Risk and approval behavior.
- Common failures and recovery guidance.
- Representative paraphrases used as guidance, never routing rules.

The provider definition and server validator must derive from the same schema.
Core control-plane capabilities cannot disappear silently during Pack or project
manifest compilation.

The effective manifest is authoritative for the turn and must explain why a
known capability is unavailable rather than letting the model hallucinate an
absence.

## Provider and model contract

Chat must have an explicit agent model/profile separate from general RAG prose
generation. The profile includes:

- Certified provider/model.
- Structured-decision support.
- Tool-choice mode.
- Reasoning/thinking policy.
- Temperature and sampling parameters.
- Timeout and retry behavior.
- Maximum output and tool-call behavior.

Provider tool names must be semantic and collision-checked, not opaque hashes.

Malformed output repair includes the original user message, conversation state,
manifest, observations, budgets, and typed rejection reason. Repair cannot
change the objective or manufacture missing arguments.

Provider selection is based on the same versioned evaluation corpus, not
anecdotal impressions.

## Approval protocol

Approval-required operations must be reachable without letting the model approve
itself.

```text
user requests governed action
→ agent proposes exact capability + arguments
→ policy returns approval required
→ durable approval proposal is created
→ agent explains consequence
→ authorized user approves or rejects
→ exact validated call resumes or is closed
```

The proposal records capability, canonical arguments/hash, user, scope, target
revision/version, expiry, and risk. Approval of one proposal cannot authorize a
different call.

## Governed multi-agent protocol

Multi-agent behavior is optional specialization under one coordinator, not a
free-form hierarchy.

Potential specialists include:

- Documentation research specialist.
- Runtime investigation specialist.
- Pack design specialist.
- Source/code analysis specialist.
- Evidence conflict specialist.
- Output review specialist.

The coordinator owns:

- User communication.
- Goal and plan.
- Scope and budgets.
- Delegation decisions.
- Progress evaluation.
- Final answer.

Each specialist receives a typed task envelope:

```json
{
  "task_id": "plan-42:step-evidence",
  "objective": "Identify evidence required by the security Pack",
  "allowed_capability_ids": [
    "nexus.documents.search",
    "nexus.packs.drafts.read"
  ],
  "project_scope": ["IntodataStreams"],
  "evidence_scope": ["generated_documentation"],
  "budget": {
    "max_iterations": 4,
    "max_tool_calls": 3,
    "max_cost_usd": 0.05
  },
  "deadline_seconds": 30
}
```

Each specialist returns a typed handoff:

```json
{
  "task_id": "plan-42:step-evidence",
  "status": "completed",
  "summary": "Required evidence categories were identified.",
  "evidence_refs": [],
  "artifacts": [],
  "unresolved_questions": [],
  "recommended_next_action": null
}
```

Specialist rules:

- Specialists cannot expand their own scope or budgets.
- Specialists cannot grant approvals.
- Specialists do not mutate global Conversation State directly.
- Specialists use the same Capability Registry and Policy Engine.
- Every delegation and handoff is auditable and idempotent.
- Only explicitly safe independent tasks may run in parallel.
- The coordinator validates handoffs before using them.
- Simple work stays with the coordinator.

## Example behaviors

### Natural guidance without a tool

```text
User: Help me design a security Pack. Give me tips.

Agent: Start with a narrow purpose, define exactly which evidence the Pack may
inspect, and make every output actionable. We can build it together. What kind
of systems should it assess?
```

No tool executes until the user asks to create or modify a draft.

### Sequential multi-tool turn

```text
User: Which projects are generating documentation, and what Packs can I use?

1. Agent calls workflow-status capability.
2. Policy validates; observation returns.
3. Agent calls Pack-list capability.
4. Policy validates; observation returns.
5. Agent produces one grounded natural answer.
```

### Durable multi-step goal

```text
User: Onboard repository X, scan it, and create security documentation.

1. Agent creates a durable plan.
2. Agent resolves project identity.
3. Agent requests missing repository authorization/input.
4. Agent attaches the source through a governed capability.
5. Agent launches and reconciles the scan workflow.
6. Agent launches documentation generation with the selected Pack.
7. Agent verifies workflow outcome and artifacts.
8. Agent summarizes the completed goal.
```

### Specialist delegation

```text
User: Investigate last night's failures and tell me whether our docs need an
update.

1. Coordinator creates a plan.
2. Runtime specialist gathers authorized execution/log evidence.
3. Documentation specialist reads relevant current docs.
4. Coordinator compares typed handoffs and evidence.
5. Coordinator answers, or proposes an approval-governed documentation update.
```

## Implementation phases

### Phase 1 — Replace the custom conversation runtime

Use the nine successor slices under
`.plans/nexus-agent-runtime-replacement/03_slices.md`:

1. Global behavioral contract and baseline.
2. Standard runtime compatibility and shadow spike.
3. Semantic capability tool adapter and policy middleware.
4. Natural grounded read-only conversation path.
5. Conditional objective and non-executable durable plan projection.
6. Approvals, interruptible tasks, child workflows, and executable plan lifecycle.
7. True streaming, layered budgets, and observability.
8. Authenticated real-provider evaluation and release evidence.
9. Canary migration, full cutover, and legacy deletion.

Phase 1 replaces the custom coordinator, response protocol, and transport while
preserving Nexus safety and durable execution authorities. The historical
recovery slices remain regression inputs, not the current implementation plan.

### Phase 2 — Durable planning and feedback

- AgentPlan schema, repository, transitions, and optimistic versioning.
- Planning threshold: simple response/action versus planned objective.
- Step dependency and completion rules.
- Progress evaluation and bounded replanning.
- Plan-aware approval, wait, workflow, and restart behavior.
- Plan UI and audit projection.

### Phase 3 — Governed memory

- Working-memory projection and relevance selection.
- Episodic summaries derived from messages and receipts.
- Semantic memory with consent, scope, provenance, correction, retention, and
  deletion.
- Procedural memory integration through capabilities and Packs.
- Memory quality, poisoning, privacy, and stale-fact tests.

### Phase 4 — Multi-agent delegation

- Specialist Registry.
- Delegation policy and task envelope.
- Typed handoff and evidence merge.
- Shared plan/ledger integration.
- Per-specialist scope, budgets, deadlines, idempotency, and audit.
- Optional safe read-only parallel fan-out.
- Coordinator synthesis and conflict handling.

### Phase 5 — Full platform evaluation and promotion

- Multi-turn planning corpus.
- Memory correctness and privacy corpus.
- Delegation/tool-selection corpus.
- Long-running workflow/restart tests.
- Adversarial scope, approval, and prompt-injection tests.
- Authenticated real-provider canary.
- Human quality review and explicit promotion.

## Quality targets

“99% accurate” must be defined by category rather than promised for unrestricted
open-world conversation.

| Metric | Target |
|---|---:|
| Authorization and project-scope safety | 100% |
| Unapproved consequential writes | 0 |
| Capability/handler/schema parity | 100% |
| Correct boundary decision on approved corpus | at least 98% |
| Correct capability selection on approved corpus | at least 98% |
| Correct arguments after allowed clarification | at least 99% |
| Incorrect durable state transition | below 0.1% |
| Repeated visible response | below 0.5% |
| Raw internal/validator error exposed | 0 |
| Human-rated relevance/helpfulness | at least 4.5/5 |
| Human-rated conversational naturalness | at least 4.5/5 |
| Memory scope/provenance correctness | 100% |
| Specialist scope/budget correctness | 100% |

Clarification and safe abstention are part of accuracy. A system forced to guess
cannot achieve trustworthy task completion.

## Evaluation requirements

Evaluation must assert more than a terminal label such as `ANSWERED`.

Every scenario records:

- User intent and allowed ambiguity.
- Boundary decision.
- Capability or specialist selection.
- Canonical arguments and scope.
- Expected observations.
- Allowed and forbidden effects.
- Conversation/task/plan transition.
- Visible response semantic requirements.
- Grounding and evidence references.
- Approval behavior.
- Repetition and internal-error constraints.
- Latency, token, tool, and cost budgets.

Test layers:

1. Pure schema/state/policy unit tests.
2. Handler and manifest contract tests.
3. Scripted-model loop tests.
4. Authenticated public-API integration tests.
5. Configured-provider behavioral evaluation without injected decisions.
6. Restart, concurrency, timeout, denial, approval, and recovery tests.
7. Human-reviewed canary traffic.

## Release gates

No architecture phase advances merely because source tests pass.

Required gates:

- Clean, reviewable change boundary.
- Database backup and migration parity proof.
- No production semantic keyword/regex routing.
- Deterministic safety suite passes completely.
- Configured-provider quality thresholds pass.
- Streaming and non-streaming behavior match.
- Restart and replay preserve exact effects and task/plan state.
- At least 1,000 authenticated turns over seven days for final promotion.
- Explicit human approval before removing rollback compatibility.

## Anti-patterns to prevent

- Adding phrase-specific branches for failed conversations.
- Treating active tasks as mandatory conversational modes.
- Rendering raw validation messages as assistant responses.
- Marking every fact mandatory because it is authoritative.
- Using scripted model output as proof of provider behavior.
- Allowing a Pack compiler to hide core tools silently.
- Giving tools opaque names that remove semantic context.
- Sharing a prose-generation model profile accidentally with agent decisions.
- Terminating immediately on every denial or non-effectful validation error.
- Creating multi-agent systems before the coordinator is reliable.
- Letting memory become hidden permission, stale truth, or untraceable context.
- Letting specialists communicate unreviewed conclusions directly to users.

## Durable architectural rule

The long-term rule for Nexus is:

> The coordinator decides what the user means and how to pursue the objective.
> Plans describe intended progress. Capabilities and specialists describe what
> can be done. Policy decides what is allowed. Handlers and workflows perform
> validated effects. The Workflow Ledger records what happened. Memory provides
> relevant prior context with provenance. Typed observations and handoffs provide
> feedback. The coordinator decides the next step and communicates naturally.

This document is a north star, not implementation evidence. Current production
behavior must not be described as satisfying this target until the corresponding
phases, evaluation gates, and canary requirements have passed.
