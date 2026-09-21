"""Provider factory for doc generation backends."""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.base import DocGenerationProvider


def get_provider(
    provider_type: str | None = None,
    model: str | None = None,
    investigation_model: str | None = None,
    write_model: str | None = None,
    log_manager=None,
    instruction_pack_dir: str | None = None,
    plugin_pack_dir: Path | None = None,
) -> DocGenerationProvider:
    """Return the configured doc generation provider.

    The provider is selected by MCP_DOC_GENERATION_PROVIDER (default: custom).
    Supported values:
      - "custom"             — custom Python harness using MCP_DOC_GENERATION_MODEL
      - "openai/<model>"     — custom harness + OpenAI-compatible adapter
      - "deepseek/<model>"   — custom harness + DeepSeek OpenAI-compatible adapter
      - "route33/<model>"    — custom harness + Route33 OpenAI-compatible adapter
      - "openrouter/<model>" — custom harness + OpenRouter OpenAI-compatible adapter
      - "anthropic/<model>"  — custom harness + Anthropic API adapter
      - "google/<model>"     — custom harness + Gemini OpenAI-compatible adapter
      - "ollama/<model>"     — custom harness + Ollama-compatible adapter
      - "claude-cli"         — legacy Claude Code CLI subprocess fallback
      - "opencode-cli"       — OpenCode transport for the canonical Claude CLI workflow

    investigation_model: optional override for Phase 1 investigation model.
    write_model: optional override for Phase 3 doc-writing model (must be "provider/model" format).
                 Falls back to MCP_DOC_GENERATION_WRITE_MODEL config value.
    """
    from mcp_server.config import config

    from mcp_server.model_strategies import resolve_generation_models

    resolution = resolve_generation_models(
        provider_type=provider_type,
        model=model,
        write_model=write_model,
        default_provider=config.doc_generation_provider,
        default_analysis_model=config.doc_generation_model,
        default_write_model=config.doc_generation_write_model,
        default_claude_cli_model=config.claude_model,
    )

    if resolution.uses_claude_cli:
        from mcp_server.providers.claude_cli import ClaudeCliProvider
        return ClaudeCliProvider()

    if resolution.uses_opencode_cli:
        from mcp_server.providers.opencode_cli import OpenCodeCliProvider
        route = resolution.analysis_model
        return OpenCodeCliProvider(
            model_route=None if route == "opencode" else route,
            plugin_pack_dir=plugin_pack_dir,
        )

    if resolution.uses_custom_harness:
        from mcp_server.providers.custom import build_custom_provider

        write_model_str = (
            resolution.write_model
            if resolution.write_model
            and "/" in resolution.write_model
            and resolution.write_model != resolution.analysis_model
            else None
        )
        return build_custom_provider(
            resolution.analysis_model,
            write_model_str,
            investigation_model_str=investigation_model or config.doc_generation_investigation_model,
            fallback_model_str=config.doc_generation_fallback_model,
            instruction_pack_dir=instruction_pack_dir,
            plugin_pack_dir=plugin_pack_dir,
        )

    raise ValueError(
        f"Unknown doc generation provider: {provider_type or config.doc_generation_provider!r}. "
        f"Supported: 'custom', 'openai/<model>', 'deepseek/<model>', 'route33/<model>', "
        f"'openrouter/<model>', 'anthropic/<model>', 'google/<model>', 'ollama/<model>', "
        f"'claude-cli', or 'opencode-cli'."
    )
