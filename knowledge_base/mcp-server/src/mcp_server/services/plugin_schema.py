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
    evidence_profile: str | None = None
    required_sections: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sections_backward_compat(self) -> "DocDefinition":
        if not self.required_sections and self.sections:
            self.required_sections = list(self.sections)
        return self


class EvidenceProfile(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ValidationPolicy(BaseModel):
    unknown_policy: Literal["explicit"] = "explicit"
    mode: Literal["blocking", "advisory"] = "blocking"


class QualityGates(BaseModel):
    mode: Literal["blocking", "advisory"] = "blocking"
    required_terms: list[str] = Field(default_factory=list)
    required_sections: dict[str, list[str]] = Field(default_factory=dict)
    min_doc_bytes: dict[str, int] = Field(default_factory=dict)
    required_evidence_terms: list[str] = Field(default_factory=list)


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


class PhasePolicy(BaseModel):
    """Per-phase execution policy for a plugin pack run.

    investigation: "skip" | "run"
        skip — write stub records, proceed straight to Phase 3.
        run  — run the full single-shot investigator per group.

    analysis: "skip" | "run" | "bounded"
        skip    — write stub coverage records (original pack-contract behaviour).
        run     — full group-analysis agent with tool access.
        bounded — _phase2_analysis_direct: one inline call per group, no tools.
                  pack's prompts/phase2.md is used as the complete prompt when present.

    scorecard: "skip" | "run"
        skip — omit Phase 2.6 scorecard.
        run  — generate scorecard from analysis outputs.

    doc_bundles: "skip" | "run"
        skip — omit Phase 2.8 doc-specific analysis bundles.
        run  — generate _doc_pack_*.md bundles feeding Phase 3.
    """

    investigation: Literal["skip", "run"] = "skip"
    analysis: Literal["skip", "run", "bounded"] = "skip"
    scorecard: Literal["skip", "run"] = "skip"
    doc_bundles: Literal["skip", "run"] = "skip"


class PluginPackManifest(BaseModel):
    name: str
    version: str
    description: str = ""
    docs: list[DocDefinition] | None = None
    evidence_profiles: dict[str, EvidenceProfile] = Field(default_factory=dict)
    validation: ValidationPolicy = Field(default_factory=ValidationPolicy)
    quality_gates: QualityGates | None = None
    steps: list[PluginStep]
    phases: PhasePolicy | None = None

    @model_validator(mode="after")
    def _steps_not_empty(self) -> "PluginPackManifest":
        if not self.steps:
            raise ValueError("plugin pack must define at least one step")
        if "validation" not in self.model_fields_set and self.quality_gates is not None:
            self.validation = ValidationPolicy(mode=self.quality_gates.mode)
            self.model_fields_set.discard("validation")
        return self


def load_plugin_manifest(pack_dir: Path) -> PluginPackManifest:
    """Load and validate plugin.yaml from pack_dir."""
    manifest_path = pack_dir / "plugin.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No plugin.yaml found in {pack_dir}")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return PluginPackManifest.model_validate(raw)
