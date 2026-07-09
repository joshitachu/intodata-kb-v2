# ADR-001: MCP Agent Over CLI Analyzer for Documentation Generation

## Status
Accepted (Feb 2026); superseded for active generation by the custom harness.

**Current state**: this ADR records the historical move away from the CLI analyzer. The project no longer uses Claude Code as the active doc-generation path. The current path is the custom harness in `providers/custom/harness.py`, routed by `DocGenerationCoordinator`, with direct provider/model adapters.

## Context
The original intodoc architecture used a CLI-based LLM analyzer pipeline:
- 21 analysis passes per chunk, each calling an external LLM API (via LiteLLM)
- Required API keys (Anthropic, OpenAI, etc.) and cost tracking ($5 default budget)
- Results processed through 32 Jinja2 templates to produce markdown
- Slow: each pass = separate API call with rate limiting and retries
- Quality: constrained by template rigidity and single-pass context windows

## Decision
Replace the CLI analyzer with an MCP-based agent for documentation generation.

Historically, Claude Code acted as both orchestrator and LLM. That has since been
replaced by the repository-owned custom harness: MCP/server code owns orchestration,
phase state, staging, validation, and provider selection; LLM providers only author
analysis and documentation text inside bounded tasks.

## Rationale

- **Historical rationale only** -- the original Claude Code path avoided API keys and separate cost tracking
- **Better quality** -- agent can reason across groups, adapt to project structure
- **Parallel sub-agents** -- one per group for analysis, one per theme for writing
- **Direct .md writing** -- no Jinja2 template constraints
- **Interactive** -- agent can ask questions, handle errors, adapt on the fly
- **Simpler architecture** -- fewer moving parts (no LiteLLM, no cost tracker, no templates)

## Consequences

- Legacy CLI path (analyzer + generator) is retained but no longer the primary workflow
- Documentation generation uses the custom harness and configured provider/model adapters
- MCP REST/server components must be available for dashboard-driven generation
- Analysis prompts (p01-p22) and reference docs remain the quality backbone
- Budget/cost tracking is handled by the active provider/runtime path

## Alternatives Considered

- **Keep CLI analyzer, improve templates** -- rejected: too rigid, API key dependency
- **Hybrid (CLI analysis + agent writing)** -- rejected: unnecessary complexity
- **Web-based agent UI** -- deferred: dashboard handles project management, not doc generation
