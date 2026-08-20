# OpenCode documentation generation

`opencode-cli` is a documentation-generation provider. Nexus performs the
existing deterministic scan/preprocess work, then continues one OpenCode
session through Gather, Analyze, and Produce. Between session messages Nexus
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
MCP_OPENCODE_OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
MCP_OPENCODE_MCP_READ_ALLOWLIST_JSON={}
```

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

The production image pins `opencode-ai@1.18.7` and verifies the CLI version
during its build. Model credentials, compatible base URLs, provider plugins,
and MCP server definitions remain in OpenCode/environment configuration; Nexus
does not copy their values into prompts or logs.

Nexus dispatches every phase step once and never automatically resumes or
restarts an OpenCode process. OpenCode and the selected model provider retain
their own internal transport/error behavior.

MCP is deny-by-default in the Nexus task contract. Explicitly enumerate read
tools, for example `{"internal-docs":["search","get_page"]}`. Connector use is
limited by the orchestration policy to Gather, is supplementary to scanner
evidence, and must not mutate external systems. Enforce the same restrictions
in the operator-owned OpenCode permission configuration because OpenCode owns
the actual tool runtime.

After a run, sanitized session receipts are under `docs_staging/_audit/` on
failure and move with the normal staging/publication lifecycle on success.
