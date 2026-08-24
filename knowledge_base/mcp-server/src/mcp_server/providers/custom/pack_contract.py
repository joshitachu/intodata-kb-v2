"""PackContract -- the resolved, validated section/doc contract for one run.

Every consumer that previously keyed section behaviour on ``platform: str`` or
on ``self._plugin_mode`` (a filesystem predicate) takes an explicit contract
object instead. ``platform`` becomes an input to the *compiler* that builds the
contract, not a runtime discriminator scattered across validators, prompts,
bundle routing, and the living-docs path.

The contract is resolvable from two sources -- there is one pack format, so
there is one compiler:

* :func:`default_contract` -- no pack selected; the built-in behaviour (Talend
  enforces the 13 required sections + GROUP_SUMMARY, every other platform
  enforces GROUP_SUMMARY only).
* :func:`contract_from_plugin_manifest` -- a ``plugin.yaml``
  (:class:`~mcp_server.services.plugin_schema.PluginPackManifest`). The
  manifest's ``lens`` block supplies the declared sections, their questions,
  the extend/replace mode, and the evidence focus; ``docs`` supplies the output
  set and its Phase 2 routing.

Protected floor: ``GROUP_SUMMARY`` is always present in ``floor_names`` and
cannot be removed by any manifest -- there is no field for it.

Unsatisfied sections do not fail the run. A declared section the model
legitimately found nothing for is simply absent from the output; the gap is
recorded under ``_assessment/`` for debugging and the documents publish clean.
The only sections whose absence is a pipeline failure are the protected floor
(:attr:`hard_required_names`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from mcp_server.providers.custom.constants import (
    CROSS_JOB_FINDINGS_SECTION,
    GROUP_SUMMARY_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
)
from mcp_server.providers.custom.prompt_packs import (
    ANALYSIS_SECTION_NAMES,
    REQUIRED_SECTION_NAMES,
)

ADDITIONAL_FINDINGS_SECTION = "UNCLASSIFIED_OR_EXTRA_FINDINGS"

# Structural sections owned by the harness (assembly, synthesis, parser
# buckets). Always recognised by the parser regardless of the pack contract.
STRUCTURAL_SECTION_NAMES: tuple[str, ...] = (
    GROUP_SUMMARY_SECTION,
    CROSS_JOB_FINDINGS_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
    ADDITIONAL_FINDINGS_SECTION,
)

# Default relationship rollup sections (legacy behaviour in
# phase2_claims.render_claims_as_markdown).
DEFAULT_RELATIONSHIP_SECTIONS: tuple[str, ...] = ("CROSS_GROUP_REFS", "ESB_ROUTES")

_SECTION_ID_RE = r"^[A-Z][A-Z0-9_]{1,63}$"
MAX_QUESTION_CHARS = 500
MAX_REQUIREMENTS_CHARS = 4000
MAX_AUDIENCE_ROLE_CHARS = 80

#: Debugging record of declared sections a run produced nothing for. Written
#: under ``_assessment/`` so an author can see which of their questions the
#: evidence could not answer. Advisory only: it does not gate publication,
#: annotate documents, or change the run status.
SECTION_COVERAGE_FILENAME = "pack_section_coverage.json"


class PackAudienceSpec(BaseModel):
    """The manifest ``audience:`` block, resolved onto the presentation seam.

    Authority tier (spec section 4): a user pack may set these, but only from
    whitelisted enums -- hence the enum-typed fields rather than free text.
    ``roles`` is bounded free text (job titles are open-ended and carry no
    execution semantics); it renders as an additive readership line and never
    as an instruction that could change findings or gates.
    """

    model_config = {"frozen": True}

    roles: tuple[str, ...] = ()
    tone: str | None = None
    technical_depth: str | None = None
    output_organization: str | None = None

    @field_validator("roles")
    @classmethod
    def _bounded_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for role in value:
            if len(role) > MAX_AUDIENCE_ROLE_CHARS:
                raise ValueError(
                    f"audience.roles entries are capped at {MAX_AUDIENCE_ROLE_CHARS} characters"
                )
        return value

    @property
    def is_empty(self) -> bool:
        return not (
            self.roles or self.tone or self.technical_depth or self.output_organization
        )


class PackSectionSpec(BaseModel):
    """One declared Phase 2 section: its canonical id and the question asked of it."""

    model_config = {"frozen": True}

    id: str = Field(pattern=_SECTION_ID_RE)
    question: str = Field(default="", max_length=MAX_QUESTION_CHARS)


class PackDocSpec(BaseModel):
    """One declared output document and its Phase 2 evidence routing."""

    model_config = {"frozen": True}

    name: str = Field(min_length=1, max_length=200)
    sections: tuple[str, ...] = ()
    phase2_sections: tuple[str, ...] = ()
    per_unit: bool = False


class PackContract(BaseModel):
    """Resolved section/doc contract for one documentation run.

    ``mode`` semantics:

    * ``extend`` -- declared sections are ADDED to the legacy 16-section
      contract (today's only behaviour).
    * ``replace`` -- declared sections REPLACE the legacy contract; the only
      protected floor is ``GROUP_SUMMARY``.

    ``required_names`` is the hard-enforcement set used by validators and the
    deterministic patcher. It is intentionally decoupled from the declared
    sections: for v2 packs the floor is ``GROUP_SUMMARY`` only (matching the
    existing non-Talend behaviour the spec generalizes), so a pack section the
    model legitimately found nothing for can never stub the whole group.
    """

    model_config = {"frozen": True}

    pack_id: str | None = None
    platform: str = "generic"
    mode: Literal["extend", "replace"] = "extend"
    declared_sections: tuple[PackSectionSpec, ...] = ()
    required_names: tuple[str, ...] = ()
    docs: tuple[PackDocSpec, ...] = ()
    focus_include: tuple[str, ...] = ()
    focus_exclude: tuple[str, ...] = ()
    requirements: str = Field(default="", max_length=MAX_REQUIREMENTS_CHARS)
    # Manifest ``audience:``. Presentation-only; consumed at the Phase 3
    # rendering boundary via ``presentation_instructions_for_phase``. A
    # request-supplied ``nexus_presentation`` (the Style layer) wins over it,
    # matching the foundation -> functional -> style layer ordering.
    audience: PackAudienceSpec | None = None
    relationship_section_names: tuple[str, ...] = DEFAULT_RELATIONSHIP_SECTIONS
    # True only for the default (no-pack) contract: enterprise doc-set
    # validators, scorecard requirement, count-claim checks.
    enforce_default_docs: bool = False

    @field_validator("declared_sections")
    @classmethod
    def _bounded_declared(
        cls, value: tuple[PackSectionSpec, ...]
    ) -> tuple[PackSectionSpec, ...]:
        seen: set[str] = set()
        for spec in value:
            if spec.id in seen:
                raise ValueError(f"duplicate declared section id: {spec.id}")
            seen.add(spec.id)
        return value

    @model_validator(mode="after")
    def _protect_floor(self) -> "PackContract":
        # GROUP_SUMMARY is the protected floor; a replace pack must not be able
        # to redeclare or drop it. It is structural, never declared.
        declared_ids = {spec.id for spec in self.declared_sections}
        if GROUP_SUMMARY_SECTION in declared_ids:
            raise ValueError(
                "GROUP_SUMMARY is a protected structural section and cannot be declared"
            )
        return self

    # ── Derived views ─────────────────────────────────────────────────────

    @property
    def is_pack(self) -> bool:
        return self.pack_id is not None

    @property
    def floor_names(self) -> frozenset[str]:
        """Sections guaranteed in every Phase 2 output. Protected; not configurable."""
        return frozenset({GROUP_SUMMARY_SECTION})

    @property
    def analysis_section_names(self) -> tuple[str, ...]:
        """Sections the Phase 2 prompt asks for (excludes structural sections)."""
        declared = tuple(spec.id for spec in self.declared_sections)
        if self.mode == "replace":
            return declared
        extras = tuple(n for n in declared if n not in ANALYSIS_SECTION_NAMES)
        return tuple(ANALYSIS_SECTION_NAMES) + extras

    @property
    def ordered_names(self) -> tuple[str, ...]:
        """Full ordered section universe: prompted sections + structural tail."""
        return self.analysis_section_names + STRUCTURAL_SECTION_NAMES

    @property
    def parser_names(self) -> frozenset[str]:
        """Every section name the Phase 2 parser recognises for this contract."""
        return frozenset(self.ordered_names)

    @property
    def normalizable_names(self) -> tuple[str, ...]:
        """Names the heading normalizer canonicalises.

        Default contract: legacy behaviour (all 16 on Talend, GROUP_SUMMARY
        only elsewhere). Pack contracts: every prompted section + the floor.
        """
        if not self.is_pack and not self.declared_sections:
            if self.platform == "talend":
                return tuple(ANALYSIS_SECTION_NAMES) + (GROUP_SUMMARY_SECTION,)
            return (GROUP_SUMMARY_SECTION,)
        return self.analysis_section_names + (GROUP_SUMMARY_SECTION,)

    @property
    def hard_required_names(self) -> tuple[str, ...]:
        """Sections whose absence is a PIPELINE failure (raise -> salvage stub).

        For the default (no-pack) contract this is the whole required set --
        the built-in behaviour (Talend 13 + GROUP_SUMMARY, floor-only
        elsewhere) is unchanged. For any pack contract this is the protected
        floor alone: a declared section the model found nothing for is a gap in
        the evidence, not a pipeline defect, and must never stub the group or
        block publication.
        """
        if not self.is_pack:
            return self.required_names
        return tuple(n for n in self.required_names if n in self.floor_names)

    @property
    def questions(self) -> dict[str, str]:
        return {spec.id: spec.question for spec in self.declared_sections if spec.question}

    @property
    def enforces_sections(self) -> bool:
        return bool(self.required_names)

    def doc_spec(self, name: str) -> PackDocSpec | None:
        for spec in self.docs:
            if spec.name == name:
                return spec
        return None

    def doc_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.docs)


def default_contract(platform: str) -> PackContract:
    """The no-pack contract. Reproduces today's platform-conditional behaviour."""

    platform_lower = (platform or "generic").lower()
    required: tuple[str, ...] = (
        (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION)
        if platform_lower == "talend"
        else (GROUP_SUMMARY_SECTION,)
    )
    return PackContract(
        pack_id=None,
        platform=platform_lower,
        mode="extend",
        declared_sections=(),
        required_names=required,
        enforce_default_docs=True,
    )


def contract_from_plugin_manifest(manifest: object, platform: str = "generic") -> PackContract:
    """Contract for a ``plugin.yaml`` pack -- the only pack compiler.

    Reads the manifest's ``lens`` block for the declared sections, their
    questions, the extend/replace mode and the evidence focus, and its ``docs``
    block for the output set and Phase 2 routing.

    A pack that declares no lens behaves exactly as packs always did: no
    declared sections, ``extend`` mode, nothing section-enforced. That keeps
    every pack authored before the lens existed working unchanged.
    """

    lens = getattr(manifest, "lens", None)
    lens_sections = getattr(lens, "sections", None) if lens else None
    lens_focus = getattr(lens, "focus", None) if lens else None

    declared = tuple(
        PackSectionSpec(id=decl.id, question=decl.question)
        for decl in (getattr(lens_sections, "declare", None) or ())
    )
    mode: Literal["extend", "replace"] = (
        "replace" if getattr(lens_sections, "mode", "extend") == "replace" else "extend"
    )

    docs = []
    for doc in getattr(manifest, "docs", None) or []:
        docs.append(
            PackDocSpec(
                name=getattr(doc, "name", ""),
                sections=tuple(
                    getattr(doc, "required_sections", None)
                    or getattr(doc, "sections", None)
                    or ()
                ),
                phase2_sections=tuple(getattr(doc, "phase2_sections", None) or ()),
                per_unit=bool(getattr(doc, "per_job", False)),
            )
        )

    # The protected floor, and ONLY the protected floor, is enforced -- and
    # only once the pack declares its own sections (a pack with no lens keeps
    # the historical "nothing enforced" behaviour byte for byte). Declared
    # sections are never added here: an unsatisfied declared section is a gap
    # to record, not a failure to raise.
    required: tuple[str, ...] = (GROUP_SUMMARY_SECTION,) if declared else ()

    name = getattr(manifest, "name", None) or "plugin-pack"
    return PackContract(
        pack_id=str(name),
        platform=(platform or "generic").lower(),
        mode=mode,
        declared_sections=declared,
        required_names=required,
        docs=tuple(docs),
        focus_include=tuple(getattr(lens_focus, "include", None) or ()),
        focus_exclude=tuple(getattr(lens_focus, "exclude", None) or ()),
        requirements=getattr(lens, "requirements", "") if lens else "",
        audience=audience_spec_for(getattr(manifest, "audience", None)),
        # A replace pack asked for exactly its own sections; the built-in
        # relationship rollups are not among them.
        relationship_section_names=(
            () if mode == "replace" else DEFAULT_RELATIONSHIP_SECTIONS
        ),
        enforce_default_docs=False,
    )


def audience_spec_for(audience: object | None) -> PackAudienceSpec | None:
    """Project a manifest ``audience:`` block onto the contract.

    Returns ``None`` when the block is absent or declares nothing, so the
    presentation seam stays untouched for packs that express no readership
    preference.
    """

    if audience is None:
        return None

    def _value(name: str) -> str | None:
        raw = getattr(audience, name, None)
        if raw is None:
            return None
        return getattr(raw, "value", raw)

    spec = PackAudienceSpec(
        roles=tuple(getattr(audience, "roles", None) or ()),
        tone=_value("tone"),
        technical_depth=_value("technical_depth"),
        output_organization=_value("output_organization"),
    )
    return None if spec.is_empty else spec
