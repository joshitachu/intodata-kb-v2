# Slice 7 Bounded Authenticated Evaluation

## Verdict

**NO_GO**. The bounded gate required at least 45 of 50 authenticated public-API
trajectories to pass with no absolute deterministic failure. The reproducible
review scored **28/50 (56%)** and found absolute grounding, restart/replay, and
transport-parity failures.

The local backend was returned to its default-off cohort configuration after
capture.

## Evidence

- `authenticated_public_api_checkpoint.json`: all 50 sanitized public-API
  captures, assistant answers, public events, latency, token use, and cost.
- `deterministic_capture_review.json`: per-attempt pass/fail decision, expected
  and observed tool path, taxonomy, retained answer, and review rationale.
- `release_evidence.json`: attributed bounded release-evidence envelope.
- `release_verdict.json`: fail-closed gate output.
- `sbom.cdx.json`: fresh CycloneDX 1.5 production SBOM.

Checkpoint SHA-256:
`b92d3cdd2fbb3ebf1f15042c4aa0d14e7bd297d76071b9ca7d9750e138e249ad`.

## Results by family

| Family | Passed | Total | Result |
|---|---:|---:|---|
| direct_answer | 4 | 5 | One otherwise-good answer lacked terminal public events. |
| rag_followup | 0 | 5 | Prior context/project scope was not used reliably; one runtime failure and one ungrounded answer. |
| multi_tool_read | 5 | 5 | Correct bounded two-read or no-tool behavior. |
| discuss_versus_act | 5 | 5 | Correctly discussed capability without acting. |
| pack_write | 4 | 5 | Correct draft boundary; one response exposed raw `Field required` wording. |
| workflow_approval | 4 | 5 | Approval paths passed; status-only negation failed to call the read tool. |
| task_interruption | 2 | 5 | Wrong read choice or missing grounding in three attempts. |
| no_evidence | 2 | 5 | Three attempts searched runtime data instead of documentation. |
| denial_recovery | 2 | 5 | Three attempts requested missing IDs instead of exercising policy denial. |
| stream_reconnect | 0 | 1 | Provider connection failed and replay identity was not demonstrated. |
| grounding_safe_stream | 0 | 1 | Selected workflow status instead of documentation search; no evidence reference. |
| rapid_duplicate_turn | 0 | 1 | Collector did not exercise a duplicate replay, so deduplication remained unproven. |
| transport_parity | 0 | 2 | Stream and nonstream answers differed materially. |

## Gate blockers

1. Two attempts had no assistant answer; one early capture also had no public
   event evidence.
2. Accuracy was 28/50 rather than the required 45/50.
3. Absolute deterministic failures were present for grounding,
   restart/replay, and transport parity.
4. Clarification precision was 0.50 (recall 1.00), showing frequent
   unnecessary requests for more context.
5. Checkpoint serialization review was FAIL because the first pilot attempt
   lacks terminal public events.
6. Release-owner acceptance is correctly absent for a failing run.

Failure taxonomy counts: 15 model-tool, 8 response, 7 grounding, 4 runtime
protocol, 3 transport, and 1 evaluator-evidence failure.

## Usage and performance

- Input tokens: 785,991
- Output tokens: 13,588
- Model cost: **$0.243948**
- Latency: p50 6.85 s; p95 19.02 s; max 25.32 s
- HTTP non-200 responses: 0
- Public `run.failed` terminals: 2

## Environment and dependency evidence

- Model profile: `kimi/kimi-k2.5`
- Runtime: `agent-runtime-v2-effect-primary/langgraph-1.2.9`
- Prompt: `slice-3-readonly-primary-v2`
- `uv audit --frozen --no-dev --no-extra hindsight`: no known
  vulnerabilities or adverse statuses in 114 packages.
- Focused evaluator tests: 11 passed.
- Ruff: all checks passed.

## Effect containment note

The run created three intended local Pack-write fixtures (draft IDs 29, 30,
and 31). No workflow cancellation executed and no approval proposal remains,
but the three draft rows remain in the local database. They are recorded here
and were not deleted automatically because cleanup is a destructive action.
