"""Plugin pack schema — Pydantic v2 models for plugin.yaml manifests.

THE pack format. There is exactly one, and this is it. A pack is a git branch
whose root is a directory containing:

  plugin.yaml          — this manifest
  prompts/             — .md prompt files that override harness phase preambles
  prompts/docs/        — .md per-document instructions (fully replace built-ins)
  examples/            — .md reference documents injected into Phase 3 prompts
  (optional) README.md

Step types
----------
llm      : single LLM call; prompt_file rendered with context, response is step output
harness  : delegates to CustomHarnessProvider (full multi-phase doc generation)
           reads prompts/phase2.md, prompts/scorecard.md, prompts/phase3.md if present

The three levers a pack pulls
-----------------------------
``phases``   — WHETHER each phase runs, with which model and concurrency.
``lens``     — WHAT each phase looks at and asks (attention), and whether the
               declared sections EXTEND or REPLACE the built-in analysis
               contract. This is the block that lets a pack stop paying for
               questions it does not care about.
``docs``     — WHICH documents come out, and which analysis sections feed each.

Extraction (parsing source into scan cache) is platform-owned and identical for
every pack; a pack changes attention and output, never the evidence floor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

import yaml
from pydantic import BaseModel, Field, model_validator

from mcp_server.schemas import (
    NexusOutputOrganization,
    NexusStyleTone,
    NexusTechnicalDepth,
)

# The CLOSED focus vocabulary. Mirrors
# providers/custom/evidence_bundles.py SUPPORTED_EVIDENCE_CATEGORIES and
# _EXCLUDE_PATTERNS keys; a test asserts they stay in sync. Closed because an
# unknown category silently narrows evidence to nothing, which reads as "the
# codebase has no SQL" rather than "your pack has a typo".
FOCUS_INCLUDE_VOCABULARY: frozenset[str] = frozenset(
    {
        "sql_queries",
        "ddl_schema",
        "orm_entities",
        "db_connections",
        "config_references",
        "routes",
        "handlers",
        "auth",
        "source_summary",
    }
)
FOCUS_EXCLUDE_VOCABULARY: frozenset[str] = frozenset(
    {"binary", "static_assets", "generated_files"}
)

MAX_QUESTION_CHARS = 500
MAX_REQUIREMENTS_CHARS = 4000
MAX_AUDIENCE_ROLE_CHARS = 80

_SECTION_ID_RE = r"^[A-Z][A-Z0-9_]{1,63}$"


class DocDefinition(BaseModel):
    name: str
    per_job: bool = False
    sections: list[str] = Field(default_factory=list)
    #: Analysis sections routed into THIS document's Phase 2 evidence bundle.
    #: Empty means the pack's whole declared lens (or the built-in routing for
    #: packs that declare no lens).
    phase2_sections: list[str] = Field(default_factory=list)
    evidence_profile: str | None = None
    required_sections: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)

    #: Accepted spelling of ``per_job``. Both mean "one document per scanned
    #: unit"; ``per_job`` is the Talend-flavoured original.
    per_unit: bool = False

    @model_validator(mode="after")
    def _sections_backward_compat(self) -> "DocDefinition":
        if self.per_unit and not self.per_job:
            self.per_job = True
        if not self.required_sections and self.sections:
            self.required_sections = list(self.sections)
        return self


class EvidenceProfile(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# lens: the attention block
# --------------------------------------------------------------------------- #


class SectionDeclaration(BaseModel):
    """One analysis section the pack wants, and the question asked of it.

    ``question`` is what makes a section more than a heading: it is injected
    into the Phase 2 prompt as the thing the analyst must answer for this
    section. A pack that declares ``SQL_INJECTION_SURFACE`` with the question
    "which queries concatenate a variable into SQL text, and where does that
    variable come from?" gets an analysis aimed at that, not a generic dump
    filed under a new heading.
    """

    id: str = Field(pattern=_SECTION_ID_RE)
    question: str = Field(default="", max_length=MAX_QUESTION_CHARS)


class LensSections(BaseModel):
    """The declared analysis contract, and whether it extends or replaces.

    ``extend`` (default) — declared sections are added to the built-in analysis
    contract. Backward-compatible; what every pack did before.

    ``replace`` — the declared sections ARE the contract. The Phase 2 prompt
    asks for exactly these, validation checks exactly these, and doc bundles
    route exactly these. This is the lever that stops a focused pack from
    spending its analysis budget on built-in questions it never wanted.
    ``GROUP_SUMMARY`` is a protected structural floor and survives either way.
    """

    mode: Literal["extend", "replace"] = "extend"
    declare: list[SectionDeclaration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _replace_needs_sections(self) -> "LensSections":
        if self.mode == "replace" and not self.declare:
            raise ValueError(
                "lens.sections.mode=replace requires at least one declared section"
            )
        seen: set[str] = set()
        for decl in self.declare:
            if decl.id in seen:
                raise ValueError(f"duplicate declared section id: {decl.id}")
            seen.add(decl.id)
        return self


class LensFocus(BaseModel):
    """Which evidence categories the pack screens in and out.

    This is where analysis depth actually comes from: narrowing the screen
    means every retained item gets more attention per token. Validated against
    a closed vocabulary so a typo fails at registration rather than silently
    producing an empty evidence bundle.
    """

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed_vocabulary(self) -> "LensFocus":
        bad_include = sorted(set(self.include) - FOCUS_INCLUDE_VOCABULARY)
        if bad_include:
            raise ValueError(
                "lens.focus.include outside the closed vocabulary "
                f"({', '.join(sorted(FOCUS_INCLUDE_VOCABULARY))}): "
                + ", ".join(bad_include)
            )
        bad_exclude = sorted(set(self.exclude) - FOCUS_EXCLUDE_VOCABULARY)
        if bad_exclude:
            raise ValueError(
                "lens.focus.exclude outside the closed vocabulary "
                f"({', '.join(sorted(FOCUS_EXCLUDE_VOCABULARY))}): "
                + ", ".join(bad_exclude)
            )
        return self


class Lens(BaseModel):
    """What the pack looks at, and what it asks."""

    focus: LensFocus | None = None
    sections: LensSections | None = None
    #: Free-text standing requirements injected into the analysis prompt
    #: ("cite the file and line for every finding", "never infer a schema").
    requirements: str = Field(default="", max_length=MAX_REQUIREMENTS_CHARS)


class Audience(BaseModel):
    """Presentation-only readership block, applied at the Phase 3 boundary.

    ``tone`` / ``technical_depth`` / ``output_organization`` are whitelisted
    enums (validated against the platform presentation vocabulary in
    ``mcp_server.schemas`` when compiled) so a pack cannot invent a directive
    the renderer cannot evaluate. ``roles`` is bounded free text: job titles are
    open-ended and carry no execution semantics.
    """

    roles: list[str] = Field(default_factory=list)
    tone: NexusStyleTone | None = None
    technical_depth: NexusTechnicalDepth | None = None
    output_organization: NexusOutputOrganization | None = None

    @model_validator(mode="after")
    def _bounded_roles(self) -> "Audience":
        for role in self.roles:
            if len(role) > MAX_AUDIENCE_ROLE_CHARS:
                raise ValueError(
                    f"audience.roles entries are capped at {MAX_AUDIENCE_ROLE_CHARS} characters"
                )
        return self


class EvidenceAgentDefinition(BaseModel):
    """Declarative evidence agent loaded from plugin.yaml.

    An agent can either import a harness built-in via `uses` or provide a pack
    prompt via `prompt_file`. The harness normalizes both into evidence registry
    claims so downstream phases do not need pack-specific branching.
    """

    id: str
    uses: str | None = None
    prompt_file: str | None = None
    evidence_profile: str | None = None
    categories: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    output: Literal["evidence_registry"] = "evidence_registry"
    model: str | None = None
    concurrency: int | None = None

    @model_validator(mode="after")
    def _source_required(self) -> "EvidenceAgentDefinition":
        if bool(self.uses) == bool(self.prompt_file):
            raise ValueError("evidence agent must define exactly one of uses or prompt_file")
        return self


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


class PhaseConfig(BaseModel):
    """Per-phase execution and resource overrides for a plugin pack."""

    policy: Literal["skip", "run", "bounded"] = "run"
    model: str | None = None        # model ID, e.g. "google/gemini-2.5-flash"
    concurrency: int | None = None  # overrides config.agent_phaseN_concurrency


class PhasePolicy(BaseModel):
    """Per-phase execution policy for a plugin pack run.

    investigation: "skip" | "run" | PhaseConfig
        skip — write stub records, proceed straight to Phase 3.
        run  — run the full single-shot investigator per group.

    analysis: "skip" | "run" | "bounded" | PhaseConfig
        skip    — write stub coverage records (original pack-contract behaviour).
        run     — full group-analysis agent with tool access.
        bounded — _phase2_analysis_direct: one inline call per group, no tools.
                  pack's prompts/phase2.md is used as the complete prompt when present.

    scorecard: "skip" | "run" | PhaseConfig
        skip — omit Phase 2.6 scorecard.
        run  — generate scorecard from analysis outputs.

    doc_bundles: "skip" | "run" | PhaseConfig
        skip — omit Phase 2.8 doc-specific analysis bundles.
        run  — generate _doc_pack_*.md bundles feeding Phase 3.
    """

    investigation: Union[Literal["skip", "run"], PhaseConfig] = "skip"
    analysis: Union[Literal["skip", "run", "bounded"], PhaseConfig] = "skip"
    scorecard: Union[Literal["skip", "run"], PhaseConfig] = "skip"
    doc_bundles: Union[Literal["skip", "run"], PhaseConfig] = "skip"

    def policy_for(self, phase: str) -> str:
        val = getattr(self, phase, "skip")
        return val.policy if isinstance(val, PhaseConfig) else val

    def model_for(self, phase: str) -> str | None:
        val = getattr(self, phase, None)
        return val.model if isinstance(val, PhaseConfig) else None

    def concurrency_for(self, phase: str) -> int | None:
        val = getattr(self, phase, None)
        return val.concurrency if isinstance(val, PhaseConfig) else None


class PackCertification(BaseModel):
    """Declared provenance for a Pack reviewed through its source repository."""

    provenance: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")


class PluginPackManifest(BaseModel):
    name: str
    version: str
    description: str = ""
    docs: list[DocDefinition] | None = None
    #: WHAT each phase looks at and asks. The one block v1 was missing.
    lens: Lens | None = None
    #: Presentation-only readership hints applied at the Phase 3 boundary.
    audience: Audience | None = None
    evidence_profiles: dict[str, EvidenceProfile] = Field(default_factory=dict)
    evidence_agents: list[EvidenceAgentDefinition] = Field(default_factory=list)
    validation: ValidationPolicy = Field(default_factory=ValidationPolicy)
    quality_gates: QualityGates | None = None
    steps: list[PluginStep] = Field(default_factory=list)
    phases: PhasePolicy | None = None
    certification: PackCertification | None = None

    @model_validator(mode="after")
    def _defaults_and_invariants(self) -> "PluginPackManifest":
        # A pack that declares no explicit step wants the full documentation
        # pipeline; that is the only step type worth defaulting to and it is
        # what every authored pack has ever written by hand. Requiring the
        # boilerplate only ever produced confusing registration failures.
        if not self.steps:
            self.steps = [PluginStep(id="generate-docs", type="harness")]
        if "validation" not in self.model_fields_set and self.quality_gates is not None:
            self.validation = ValidationPolicy(mode=self.quality_gates.mode)
            self.model_fields_set.discard("validation")
        # A declared section lens is meaningless if analysis never runs: the
        # sections would be asked for by nobody. Default such a pack to a
        # bounded analysis pass rather than silently producing empty sections.
        if self.lens and self.lens.sections and self.lens.sections.declare:
            if self.phases is None:
                self.phases = PhasePolicy(
                    investigation="skip",
                    analysis="bounded",
                    scorecard="skip",
                    doc_bundles="run",
                )
        return self

    @property
    def declared_section_ids(self) -> tuple[str, ...]:
        if not (self.lens and self.lens.sections):
            return ()
        return tuple(decl.id for decl in self.lens.sections.declare)

    @property
    def section_mode(self) -> str:
        if not (self.lens and self.lens.sections):
            return "extend"
        return self.lens.sections.mode


def load_plugin_manifest(pack_dir: Path) -> PluginPackManifest:
    """Load and validate plugin.yaml from pack_dir."""
    manifest_path = pack_dir / "plugin.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No plugin.yaml found in {pack_dir}")
    legacy_platform_file = pack_dir / "prompts" / "phase2_platform_sections.md"
    legacy_platform_dir = pack_dir / "prompts" / "phase2_platform_sections"
    forbidden = []
    if legacy_platform_file.exists():
        forbidden.append("prompts/phase2_platform_sections.md")
    if legacy_platform_dir.exists():
        forbidden.extend(
            path.relative_to(pack_dir).as_posix()
            for path in sorted(legacy_platform_dir.rglob("*"))
            if path.is_file()
        )
    if forbidden:
        raise ValueError(
            "Nexus platform analysis prompts are immutable and cannot be "
            "replaced by a plugin. Move additive guidance to "
            "prompts/extensions/platforms/<platform>.md. Forbidden file(s): "
            + ", ".join(forbidden)
        )
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return PluginPackManifest.model_validate(raw)
