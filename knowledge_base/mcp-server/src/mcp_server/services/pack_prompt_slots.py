"""What a pack can override, derived from the live pipeline.

The pack format has two prompt namespaces and they are easy to confuse -- a
preamble is a paragraph substituted into a slot, a body is the whole prompt.
Putting a body in the preamble slot produces a pack that looks complete and
renders a broken prompt (this happened: the Talend section skeleton went into
``prompts/phase2.md`` and the result was the skeleton three times with no
preamble at all). An author cannot be expected to keep that straight from
memory, so the authoring surfaces read the answer from here instead.

Everything is derived from live code -- the document catalog, the platform
strategies, the prompt constants, the template directory. A hand-maintained
list would drift the first time someone added a document, and an authoring UI
offering a slot the pipeline does not read is worse than offering nothing.

Reachability is the entry criterion. A slot appears here only if some
``render(..., pack_dir=)`` call site or preamble resolver actually reads it.
Platform analysis is the deliberate exception to replacement: Nexus owns the
baseline and the catalog exposes only empty additive extension slots.
``phase2_patch.md`` / ``phase3_patch.md`` / ``phase3_synthesis_patch.md`` are
therefore absent: they render only from the living-docs patch executor, which
builds its own renderer with no pack context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.providers.custom.prompts import (
    _PHASE2_PREAMBLE_DEFAULT,
    _PHASE3_PREAMBLE_DEFAULT,
    _SCORECARD_PREAMBLE_DEFAULT,
)

#: Phase ids in the order ``PipelineRunner`` executes them, with a label for
#: display. Deterministic phases are included so an author can see that they
#: have no prompt, rather than wondering where it went.
PHASE_ORDER: tuple[tuple[str, str], ...] = (
    ("phase1_investigation", "Phase 1 -- investigation"),
    ("phase2_analysis", "Phase 2 -- analysis"),
    ("phase26_scorecard", "Phase 2.6 -- scorecard"),
    ("phase28_bundles", "Phase 2.8 -- evidence bundles (no prompt)"),
    ("evidence_quality_gate", "Evidence quality gate (no prompt)"),
    ("phase3_docs", "Phase 3 -- documents"),
    ("phase37_business_topology", "Phase 3.7 -- business topology"),
    ("phase4_cleanup", "Phase 4 -- cleanup (no prompt)"),
)

#: Prompt BODIES the pipeline renders through ``PromptRenderer.render`` with a
#: pack dir, by phase. The template filename IS the override path: a pack
#: replaces one by shipping ``prompts/templates/<filename>``.
#:
#: ``scripts/generate_reference_pack.py`` imports this rather than keeping its
#: own copy -- two lists would disagree the first time a phase gained a prompt,
#: and the pack that ships to authors would be the one that fell behind.
PHASE_TEMPLATES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("phase1_investigation", (
        ("phase1_extraction.md",
         "Extract per-unit evidence from one scanner chunk (single-shot mode)."),
        ("phase1_chunk_selection.md",
         "Choose which preprocess chunks are worth reading (manifest mode)."),
        ("phase1_manifest_extraction.md",
         "Extract evidence from the chunks selected above (manifest mode)."),
    )),
    ("phase2_analysis", (
        ("phase2_analysis.md",
         "The group analysis contract: which sections to answer, the "
         "unit-enumeration obligation, and the evidence rules."),
        ("phase2_claims.md",
         "Bounded claim extraction -- the compact JSON interpretation index."),
    )),
    ("phase26_scorecard", (
        ("scorecard.md",
         "The assessment scorecard contract. Note this is a different file "
         "from the prompts/scorecard.md preamble."),
    )),
    ("phase3_docs", (
        ("phase3_doc.md",
         "How one document gets written: required headings, evidence "
         "handling, redaction and fabrication rules."),
        ("phase3_unit_doc.md",
         "The per-unit dossier contract (per_unit documents only)."),
    )),
    ("phase37_business_topology", (
        ("phase37_business_topology.md",
         "Synthesise the business-topology JSON graph from scanner facts."),
    )),
)

_TEMPLATE_ROOT = Path(__file__).resolve().parents[2].parent / "data" / "prompt_templates"


@dataclass(frozen=True)
class PromptSlot:
    """One overridable prompt, with the built-in text it replaces."""

    path: str
    phase: str
    kind: str  # preamble | body | document | platform_extension
    title: str
    description: str
    default_content: str

    @property
    def has_default(self) -> bool:
        return bool(self.default_content.strip())


def _template_root() -> Path:
    """Resolve the template directory the same way the renderer does."""
    if _TEMPLATE_ROOT.exists():
        return _TEMPLATE_ROOT
    for candidate in (Path("/app/data/prompt_templates"), Path.cwd() / "data" / "prompt_templates"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("prompt_templates/ not found")


def prompt_slots() -> list[PromptSlot]:
    """Every prompt file a pack can ship, in pipeline order."""
    root = _template_root()
    slots: list[PromptSlot] = []

    # Phase 1: the preamble is PREPENDED rather than substituted, which is why
    # it has no default -- the built-in path prepends nothing. Anything an
    # author writes here is additive, so a copy of the built-in text would be
    # actively wrong.
    slots.append(PromptSlot(
        path="prompts/investigation.md",
        phase="phase1_investigation",
        kind="preamble",
        title="Investigation preamble (prepended)",
        description=(
            "Prepended to every Phase 1 prompt. The built-in path prepends "
            "nothing, so this starts empty and whatever you write is added on "
            "top of the extraction contract rather than replacing it."
        ),
        default_content="",
    ))

    _PREAMBLES = {
        "phase2_analysis": (
            "prompts/phase2.md", "Analysis preamble", _PHASE2_PREAMBLE_DEFAULT,
            "Framing substituted into the top of the Phase 2 prompt.",
        ),
        "phase26_scorecard": (
            "prompts/scorecard.md", "Scorecard preamble", _SCORECARD_PREAMBLE_DEFAULT,
            "Framing substituted into the top of the scorecard prompt.",
        ),
        "phase3_docs": (
            "prompts/phase3.md", "Document preamble", _PHASE3_PREAMBLE_DEFAULT,
            "Framing substituted into the top of every Phase 3 document prompt.",
        ),
    }

    templates = dict(PHASE_TEMPLATES)
    for phase, _label in PHASE_ORDER:
        if phase in _PREAMBLES:
            path, title, default, description = _PREAMBLES[phase]
            slots.append(PromptSlot(
                path=path, phase=phase, kind="preamble", title=title,
                description=description,
                default_content=default.rstrip() + "\n",
            ))

        for filename, description in templates.get(phase, ()):
            source = root / filename
            slots.append(PromptSlot(
                path=f"prompts/templates/{filename}",
                phase=phase,
                kind="body",
                title=filename.removesuffix(".md").replace("_", " "),
                description=description,
                default_content=source.read_text(encoding="utf-8") if source.is_file() else "",
            ))

        if phase == "phase2_analysis":
            section_dir = root / "phase2_platform_sections"
            for source in sorted(section_dir.glob("*.md")):
                slots.append(PromptSlot(
                    path=f"prompts/extensions/platforms/{source.stem}.md",
                    phase=phase,
                    kind="platform_extension",
                    title=f"{source.stem} analysis extension",
                    description=(
                        f"Optional additive analysis guidance for {source.stem}. "
                        "The Nexus-owned platform guide always remains active "
                        "and wins any conflict."
                    ),
                    default_content="",
                ))

        if phase == "phase3_docs":
            for doc in load_doc_catalog().docs:
                instructions = (doc.doc_specific_instructions or "").rstrip()
                slots.append(PromptSlot(
                    path=f"prompts/docs/{doc.name}.md",
                    phase=phase,
                    kind="document",
                    title=doc.name,
                    description=(
                        f"Instructions for {doc.name}. Fully replaces the "
                        "built-in text for this document -- the single most "
                        "effective file to edit if output is too thin."
                    ),
                    default_content=(instructions + "\n") if instructions else "",
                ))

    return slots


def slot_for(path: str) -> PromptSlot | None:
    """Look up one slot by its pack-relative path."""
    for slot in prompt_slots():
        if slot.path == path:
            return slot
    return None
