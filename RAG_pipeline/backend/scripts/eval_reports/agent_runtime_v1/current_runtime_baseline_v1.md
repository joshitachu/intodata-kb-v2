# Nexus Agent Runtime — Current Baseline

- Corpus: `agent-runtime-behavior-v1`
- Target: `current-custom-runtime-recorded-v1`
- Capture watermark: `2026-07-20T12:00:00+02:00`
- Scenarios: 448
- Evaluated historical captures: 6
- Product passes: 0
- Product failures: 6
- Not evaluated (coverage gaps): 442
- Evaluator-invalid traces: 0

This is an offline, sanitized recorded-trace baseline. It performs no provider call and no effect. Missing historical captures are evaluator gaps, not product passes or failures.

## Failures by taxonomy

| Taxonomy | Count |
|---|---:|
| `EVALUATOR` | 442 |
| `GROUNDING` | 1 |
| `MODEL_OBJECTIVE` | 4 |
| `MODEL_TOOL` | 3 |
| `OBSERVATION` | 3 |
| `PLAN_STATE` | 3 |
| `POLICY` | 1 |
| `RESPONSE` | 11 |
| `RUNTIME_PROTOCOL` | 3 |
| `WORKFLOW_STATE` | 1 |

## Evaluated failure detail

| Scenario | Primary failure | Detail |
|---|---|---|
| `pack.create.001` | `POLICY` | missing required effect: pack_draft_create |
| `pack.guided.001` | `MODEL_OBJECTIVE` | boundary ['answer'] != ['clarify'] |
| `workflow.keyword_collision.001` | `MODEL_TOOL` | tool sequence ['runtime_log_search'] is not allowed |
| `rag.followup.jobs.001` | `MODEL_OBJECTIVE` | boundary ['tool_call', 'tool_call', 'tool_call', 'tool_call', 'answer'] != ['tool_call', 'tool_call', 'answer'] |
| `workflow.cancel.001` | `OBSERVATION` | missing observations: ['approval_required'] |
| `transport.parity.001` | `MODEL_OBJECTIVE` | boundary [] != ['answer'] |

## Historical capture dimension gaps

These dimensions were not present in the sanitized source capture and do not contribute product taxonomy.

| Scenario | Uncaptured dimensions |
|---|---|
| `pack.create.001` | `arguments, grounding, policy, tool_observation_correlation` |
| `pack.guided.001` | `effects, grounding` |
| `workflow.keyword_collision.001` | `effects, grounding, policy, tool_observation_correlation` |
| `rag.followup.jobs.001` | `effects, policy, tool_observation_correlation` |
| `workflow.cancel.001` | `effects, grounding, policy, tool_observation_correlation` |
| `transport.parity.001` | `effects, events, grounding, transport_parity` |

## Attribution

- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `not_reached`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
- runtime `custom-bounded-agent-loop`; model `configured-provider-historical`; prompt `legacy-agent-prompt`; manifest `historical-effective-manifest`; transport `stream`
