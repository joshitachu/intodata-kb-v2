"""Plugin pack schema — Pydantic v2 models for plugin.yaml manifests.

A plugin pack is a directory containing:
  plugin.yaml          — this manifest
  prompts/             — .md prompt files that override harness phase preambles
  examples/            — .md reference documents injected into Phase 3 prompts
  (optional) README.md

Step types
----------
llm      : single LLM call; prompt_file rendered with context, response is step output
harness  : delegates to CustomHarnessProvider (full multi-phase doc generation)
           reads prompts/phase2.md, prompts/scorecard.md, prompts/phase3.md if present
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class DocDefinition(BaseModel):
    name: str
    sections: list[str] = Field(default_factory=list)


class PluginStep(BaseModel):
    id: str
    type: Literal["llm", "harness"] = "llm"
    prompt_file: str | None = None
    template: str | None = None
    output_schema: str | None = None
    context_from: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _prompt_required_for_llm(self) -> "PluginStep":
        if self.type == "llm" and not self.prompt_file:
            raise ValueError("prompt_file is required for llm steps")
        return self


class PluginPackManifest(BaseModel):
    name: str
    version: str
    description: str = ""
    docs: list[DocDefinition] | None = None
    steps: list[PluginStep]

    @model_validator(mode="after")
    def _steps_not_empty(self) -> "PluginPackManifest":
        if not self.steps:
            raise ValueError("plugin pack must define at least one step")
        return self


def load_plugin_manifest(pack_dir: Path) -> PluginPackManifest:
    """Load and validate plugin.yaml from pack_dir."""
    manifest_path = pack_dir / "plugin.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No plugin.yaml found in {pack_dir}")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return PluginPackManifest.model_validate(raw)
