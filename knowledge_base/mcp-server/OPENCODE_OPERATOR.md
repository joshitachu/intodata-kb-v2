# OpenCode documentation generation

`opencode-cli` is a documentation-generation provider. Nexus performs the
existing deterministic scan/preprocess work, then executes tool-driven Gather
as adaptive logical scopes and Analyze as adaptive contract batches. The
contract-batch Gather path remains available as an operator fallback. Produce
uses ordered, fresh artifact-driven sessions. Between model steps Nexus
validates and materializes the same investigation evidence, document packs,
topology evidence, publication gates, and RAG-support artifacts used by the
modern custom pipeline.

```text
MCP_OPENCODE_COMMAND=opencode
# Optional: omit to use OpenCode's configured default model.
MCP_OPENCODE_MODEL=openai/gpt-5.5
MCP_OPENCODE_PRIMARY_AGENT=build
MCP_OPENCODE_VERSION=1.18.7
MCP_OPENCODE_CONFIG_PATH=/run/secrets/opencode.json
MCP_OPENCODE_AUTO_APPROVE=true
MCP_OPENCODE_TIMEOUT_SECONDS=1800
MCP_OPENCODE_BATCH_TARGET_INPUT_TOKENS=80000
MCP_OPENCODE_GATHER_CONCURRENCY=3
MCP_OPENCODE_ANALYZE_CONCURRENCY=3
MCP_OPENCODE_PRODUCE_CONCURRENCY=2
MCP_OPENCODE_PHASE_MAX_FOLLOWUPS=1
MCP_OPENCODE_GATHER_MODE=tools
MCP_OPENCODE_SCOPE_MAX_UNITS=40
MCP_OPENCODE_SCOPE_MAX_GROUPS=6
MCP_OPENCODE_SCOPE_CONCURRENCY=3
MCP_OPENCODE_SCOPE_MAX_FOLLOWUPS=1
MCP_OPENCODE_OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
MCP_OPENCODE_GO_API_KEY=
MCP_OPENCODE_MCP_READ_ALLOWLIST_JSON={}
```

The batch target is an estimated input-token ceiling for Analyze and contract-
mode Gather, not a fixed number of contracts, groups, or sessions. Nexus
estimates each contract from its prompt, serialized contract data, and declared
evidence artifacts, then packs contracts in stable order. Consequently, a tiny
contract workload can use one batch, while a larger workload creates only as
many fresh sessions as its estimated evidence requires. A single contract
larger than the target is kept intact in its own visibly over-target batch.
Non-positive target and concurrency values are clamped to safe minimums at use
time.

`MCP_OPENCODE_GATHER_CONCURRENCY`, `MCP_OPENCODE_ANALYZE_CONCURRENCY`, and
`MCP_OPENCODE_PRODUCE_CONCURRENCY` are independent provider-capacity ceilings;
they do not force Nexus to create that many sessions. Before dispatch, the run
log reports the remaining contract count, adaptive batch count, effective
concurrency, and estimated input tokens for each batch.

With `MCP_OPENCODE_GATHER_MODE=tools`, Nexus first creates a deterministic
logical-scope plan at `docs_staging/_evidence/scopes.json`. A scope never mixes
partitions, keeps a group whole when it fits, prefers to colocate groups linked
by known engineering dependencies, and splits only when the configured unit or
group count ceiling requires it. The default ceilings therefore produce one
scope for a tiny project and progressively more cohesive scopes for larger or
composite projects. The plan contains no timestamps, has stable scope IDs and
an input digest, and is reused byte-for-byte on resume when its inventory,
dependencies, partitions, and limits still match.

`MCP_OPENCODE_SCOPE_CONCURRENCY` limits simultaneously running tool-driven
scope sessions. `MCP_OPENCODE_SCOPE_MAX_FOLLOWUPS` controls how many fresh,
bounded recovery sessions may continue a partially completed scope; zero means
only the initial attempt. Non-positive ceilings and concurrency clamp to one,
while a negative follow-up count clamps to zero. Set
`MCP_OPENCODE_GATHER_MODE=contracts` to use adaptive contract batches instead.

The initial tool-driven scope remains free to explore the sanitized staging
workspace with native read and search tools. A recovery session is narrower:
Nexus assigns only the units that remain unresolved and authorizes inspection
only of their group preprocess files plus deterministic factual maps. Runtime
bookkeeping such as `_audit`, `_transient`, prompt packs, and prior contracts is
outside that recovery boundary. Each attempt logs the before/after remaining
unit count and evidence/gap deltas; the coordinator does not impose a short
wall-clock interruption on otherwise productive investigations.

When a project uses `opencode-cli`, a model selected for that generation run is
passed directly to `opencode run --model`. Current OpenAI routes include
`openai/gpt-5.6`, `openai/gpt-5.6-luna`, `openai/gpt-5.6-sol`, and
`openai/gpt-5.6-terra`. If no run model is selected, Nexus falls back to
`MCP_OPENCODE_MODEL`, then to OpenCode's configured default.

Ollama routes such as `ollama/gemma3:4b` are registered automatically as an
OpenAI-compatible OpenCode provider for the selected model. In Docker the
default endpoint is `http://host.docker.internal:11434/v1`; override
`MCP_OPENCODE_OLLAMA_BASE_URL` when Ollama runs elsewhere. The generated
runtime overlay merges with existing `OPENCODE_CONFIG_CONTENT` rather than
discarding connector or MCP configuration.

OpenCode Go subscription routes use `opencode-go/<model-id>`, for example
`opencode-go/kimi-k3`. Set `MCP_OPENCODE_GO_API_KEY` to the key issued by the
Go subscription. Nexus materializes only the `opencode-go` credential in an
isolated 0600 auth file for each OpenCode phase and removes it afterward; it
does not mount a user's global OpenCode credential store or write the key to
prompts and run receipts.

The production image pins `opencode-ai@1.18.7` and verifies the CLI version
during its build. Model credentials, compatible base URLs, provider plugins,
and MCP server definitions remain in OpenCode/environment configuration; Nexus
does not copy their values into prompts or logs.

In tool mode, each Gather scope runs in a fresh OpenCode session and submits
evidence through durable, independently validated group-scoped tools. Partial
progress survives a process failure and can be continued by the bounded
follow-up or a later generation. In contract mode, Gather contracts remain
independent units of validation and checkpointing, but one fresh batch session
may process several contracts, including contracts from different groups.
Contract batches run concurrently up to the configured Gather ceiling. Analyze
similarly packs unfinished group-analysis contracts and runs batch sessions up
to its own ceiling. The scorecard is deliberately not included in those
batches: it starts in a separate fresh session only after all group analyses
have crossed the completion barrier, because it depends on the complete
analysis set.

Produce uses the same adaptive planner within each dependency stage. Unit
documents complete before their digest is built; group documents complete
before the executive summary; topology remains last. Batches within one stage
may run concurrently, but every invocation uses a fresh session. Validated
files written by earlier stages provide the hand-off, so Produce never depends
on an OpenCode conversation store that is removed after an invocation.

Analyze and Produce checkpoint each declared output independently. A batch that
writes some files and stops does not invalidate those files or cancel healthy
sibling batches. After all initial sessions settle, Nexus inspects the durable
artifacts and sends only missing contracts to focused, one-contract fresh
sessions. `MCP_OPENCODE_PHASE_MAX_FOLLOWUPS` bounds those recovery rounds; zero
disables same-run recovery. Structural document validation uses the same idea:
each invalid document gets its own targeted repair session before publication.
If topology remains unavailable, Nexus uses its deterministic topology builder.

Analyze and Produce agents do not edit the shared staging workspace directly.
They retain native read, grep, glob, and list access for agentic exploration,
then commit each completed Markdown or JSON deliverable through the local
`nexus_artifacts_submit_artifact` MCP tool. Nexus authorizes only outputs in the
current session's contracts, rejects empty or malformed JSON payloads, writes
accepted content atomically, and records sanitized submission receipts in
`_audit/opencode_artifact_submissions.jsonl`. This avoids `apply_patch` path and
permission failures while preventing concurrent sessions from modifying a
sibling's checkpoint.

Validated artifacts and Gather validation receipts, rather than conversation
state, define completion. A later user-triggered run filters completed contracts
before replanning the unfinished ones, so accepted work can be resumed even
when its original batch grouping or OpenCode session is unavailable. Usage
receipts and attempt-ledger entries are recorded per invocation with stable,
unique phase/batch/recovery step names. OpenCode and the selected model provider
retain their own internal transport/error behavior.

MCP is deny-by-default in the Nexus task contract. Explicitly enumerate read
tools, for example `{"internal-docs":["search","get_page"]}`. Connector use is
limited by the orchestration policy to Gather, is supplementary to scanner
evidence, and must not mutate external systems. Enforce the same restrictions
in the operator-owned OpenCode permission configuration because OpenCode owns
the actual tool runtime.

After a run, sanitized session receipts are under `docs_staging/_audit/` on
failure and move with the normal staging/publication lifecycle on success.
