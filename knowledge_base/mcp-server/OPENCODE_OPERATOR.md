# OpenCode documentation generation

`opencode-cli` is a transport adapter for the canonical Claude Code CLI
documentation workflow. Nexus performs the same deterministic preprocessing,
creates one whole-estate staging workspace, and sends the exact same four-phase prompt
to one autonomous CLI session. OpenCode-specific Gather/Analyze/Produce
schedulers, generation profiles, workspace contracts, and connector tools are
not part of this path.

```text
MCP_OPENCODE_COMMAND=opencode
# Optional: omit to use OpenCode's configured default model.
MCP_OPENCODE_MODEL=openai/gpt-5.6-terra
MCP_OPENCODE_PRIMARY_AGENT=build
MCP_OPENCODE_VERSION=1.18.7
MCP_OPENCODE_CONFIG_PATH=/run/secrets/opencode.json
MCP_OPENCODE_AUTO_APPROVE=true
MCP_OPENCODE_TIMEOUT_SECONDS=1800
MCP_OPENCODE_OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
MCP_OPENCODE_GO_API_KEY=
```

The adapter launches one command:

```text
opencode run --format json [--model <provider/model>] \
  --dir <docs_staging> [--agent build] [--auto]
```

The canonical prompt is written once to stdin. Nexus parses OpenCode's JSON
events only for progress, errors, session identity, token usage, and cost. It
does not split the workflow into provider-specific phases or repair model
outputs outside the shared Claude-compatible lifecycle.

Each invocation receives private HOME and XDG data/state/config directories.
The service-owned cache at `{data_dir}/opencode_cache` is shared between runs
and checked for write access during preflight. Temporary state is removed after
the child process and its process group have been reaped.

OpenCode Go routes use `opencode-go/<model-id>` and require
`MCP_OPENCODE_GO_API_KEY`. Nexus writes that key to a per-run 0600 `auth.json`;
it is not placed in the prompt. OpenRouter routes accept either
`OPENROUTER_API_KEY` or `MCP_DOC_GENERATION_OPENROUTER_API_KEY`. Ollama routes
such as `ollama/gemma3:4b` receive a merged OpenAI-compatible provider entry
using `MCP_OPENCODE_OLLAMA_BASE_URL`.

The configured runtime version is mandatory and must exactly match
`opencode --version`. Stderr is streamed as bounded, credential-redacted
operator diagnostics. A timeout or cancellation terminates and reaps the whole
OpenCode process group before temporary state is removed.

Successful generation still follows the shared server-owned publication path:
validate the nine-document contract, atomically promote `docs_staging/` to
`docs/`, then push the published documents to RAG. OpenCode never publishes or
ingests directly. Unlike Claude CLI and custom providers, OpenCode bypasses the
composite partition orchestrator: even oversized or multi-root scans are handled
as one root run and are never split into generated sub-projects.
