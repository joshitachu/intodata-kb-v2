# ADR-001: MCP Agent Over CLI Analyzer for Documentation Generation

## Status
Accepted (Feb 2026)

**Partially superseded**: The rationale "No API keys needed — Claude Code IS the LLM" no longer holds universally. A second provider (`custom`) was added (Apr 2026) supporting Anthropic API, OpenAI, Gemini, and DeepSeek directly. See `PROJECT_MEMORY.md` "Two-provider doc gen design" for the follow-on decision.

## Context
The original intodoc architecture used a CLI-based LLM analyzer pipeline:
- 21 analysis passes per chunk, each calling an external LLM API (via LiteLLM)
- Required API keys (Anthropic, OpenAI, etc.) and cost tracking ($5 default budget)
- Results processed through 32 Jinja2 templates to produce markdown
- Slow: each pass = separate API call with rate limiting and retries
- Quality: constrained by template rigidity and single-pass context windows

## Decision
Replace the CLI analyzer with an MCP-based Claude Code agent for documentation generation.

The MCP server exposes scan data via 8 tools. Claude Code acts as both the orchestrator
and the LLM, reading analysis prompts and writing documentation directly.

## Rationale

- **No API keys needed** -- Claude Code IS the LLM; no external API calls
- **No cost tracking** -- included in Claude Code subscription
- **Better quality** -- agent can reason across groups, adapt to project structure
- **Parallel sub-agents** -- one per group for analysis, one per theme for writing
- **Direct .md writing** -- no Jinja2 template constraints
- **Interactive** -- agent can ask questions, handle errors, adapt on the fly
- **Simpler architecture** -- fewer moving parts (no LiteLLM, no cost tracker, no templates)

## Consequences

- Legacy CLI path (analyzer + generator) is retained but no longer the primary workflow
- Documentation generation requires Claude Code (not just any LLM)
- MCP server must be running and configured (`.mcp.json`)
- Analysis prompts (p01-p22) and reference docs remain the quality backbone
- Budget concerns eliminated for the MCP path

## Alternatives Considered

- **Keep CLI analyzer, improve templates** -- rejected: too rigid, API key dependency
- **Hybrid (CLI analysis + agent writing)** -- rejected: unnecessary complexity
- **Web-based agent UI** -- deferred: dashboard handles project management, not doc generation
