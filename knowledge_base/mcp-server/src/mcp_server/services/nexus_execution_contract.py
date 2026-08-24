"""Governed Nexus stack receipt and presentation-only rendering contract."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from mcp_server.schemas import (
    NexusCommunicationOption,
    NexusFormattingOption,
    NexusOutputOrganization,
    NexusPackSnapshot,
    NexusPresentation,
    NexusStyleAudience,
    NexusStyleTone,
    NexusTechnicalDepth,
    compute_nexus_snapshot_hash,
)

if TYPE_CHECKING:  # avoid a providers <-> services import cycle at runtime
    from mcp_server.providers.custom.pack_contract import PackAudienceSpec

NEXUS_EXECUTION_RECEIPT_SCHEMA_VERSION = 1

_AUDIENCE_INSTRUCTIONS = {
    NexusStyleAudience.EXECUTIVE: "Write for executive readers: lead with outcomes, impact, decisions, and material risk.",
    NexusStyleAudience.BUSINESS: "Write for business readers: explain process impact, ownership, dependencies, and operational consequences.",
    NexusStyleAudience.ENGINEERING: "Write for engineering readers: use precise system terminology and implementation detail supported by evidence.",
    NexusStyleAudience.OPERATIONS: "Write for operations readers: emphasize run behavior, monitoring, recovery, dependencies, and actionable checks.",
}
_TONE_INSTRUCTIONS = {
    NexusStyleTone.NEUTRAL: "Use neutral, factual language without promotional or emotive phrasing.",
    NexusStyleTone.CONCISE: "Use concise sentences and remove repetition while retaining required evidence and sections.",
    NexusStyleTone.EXPLANATORY: "Explain technical relationships clearly, defining specialized terms when first used.",
}
_DEPTH_INSTRUCTIONS = {
    NexusTechnicalDepth.SUMMARY: "Keep explanations high-level while retaining every required section, finding, and evidence reference.",
    NexusTechnicalDepth.STANDARD: "Use balanced technical depth: explain behavior, dependencies, and implications supported by evidence.",
    NexusTechnicalDepth.DEEP: "Use implementation-level depth for supported claims; do not infer detail absent from evidence.",
}
_ORGANIZATION_INSTRUCTIONS = {
    NexusOutputOrganization.DIRECT_ANSWER: "Within the required section structure, lead each section with its conclusion before supporting detail.",
    NexusOutputOrganization.SECTIONED: "Use a clear heading hierarchy within the required section structure.",
    NexusOutputOrganization.INCIDENT_REPORT: "Within the required section structure, foreground situation, impact, timeline, contributing factors, and actions where evidenced.",
}
_FORMATTING_INSTRUCTIONS = {
    NexusFormattingOption.MARKDOWN: "Use conventional Markdown headings, paragraphs, and lists.",
    NexusFormattingOption.BULLETED_LISTS: "Use bullet lists for scannable sets of facts, steps, risks, or decisions.",
    NexusFormattingOption.TABLES: "Use tables when they improve comparison of evidenced structured facts.",
    NexusFormattingOption.CODE_BLOCKS: "Use fenced code blocks only for source-backed code, configuration, commands, or queries.",
}
_COMMUNICATION_INSTRUCTIONS = {
    NexusCommunicationOption.QUALIFY_UNCERTAINTY: "State uncertainty explicitly and preserve the evidence confidence level.",
    NexusCommunicationOption.ACTION_ORIENTED: "Phrase supported recommendations as clear owner/action/outcome statements.",
    NexusCommunicationOption.INCLUDE_SOURCES: "Keep evidence references close to the claims they support.",
}


def build_nexus_execution_receipt(
    *,
    pack_ids: list[str],
    snapshot: NexusPackSnapshot | None,
    presentation: NexusPresentation | None,
) -> dict[str, object]:
    """Build data-only receipt metadata; snapshot content is never rendered as a prompt."""
    if snapshot is not None and snapshot.snapshot_hash != compute_nexus_snapshot_hash(
        snapshot, presentation
    ):
        raise ValueError(
            "nexus_pack_snapshot.snapshot_hash does not match the canonical snapshot/presentation payload"
        )
    body: dict[str, object] = {
        "schema_version": NEXUS_EXECUTION_RECEIPT_SCHEMA_VERSION,
        "nexus_pack_ids": list(pack_ids),
        "nexus_pack_snapshot": snapshot.model_dump(mode="json") if snapshot else None,
        "nexus_presentation": presentation.model_dump(mode="json") if presentation else None,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**body, "receipt_sha256": hashlib.sha256(canonical).hexdigest()}


def presentation_from_pack_audience(
    audience: "PackAudienceSpec | None",
) -> NexusPresentation | None:
    """Project a pack manifest ``audience:`` block onto ``NexusPresentation``.

    One Pack (spec section 6, agent-3 item 8): the manifest ``audience:`` block
    is the pack-owned half of presentation; the request-supplied
    ``nexus_presentation`` is the Style layer and wins where both are present
    (foundation -> functional -> style ordering, ``packs/composition.py``).

    Only the three enum-typed fields map onto the bounded presentation
    namespace. ``roles`` is free text and therefore never becomes a presentation
    enum -- it is rendered separately by
    :func:`readership_instructions_for_phase`. Returns ``None`` when nothing
    maps, because ``NexusPresentation`` rejects an all-empty payload.
    """

    if audience is None:
        return None
    try:
        return NexusPresentation(
            tone=NexusStyleTone(audience.tone) if audience.tone else None,
            technical_depth=(
                NexusTechnicalDepth(audience.technical_depth)
                if audience.technical_depth
                else None
            ),
            output_organization=(
                NexusOutputOrganization(audience.output_organization)
                if audience.output_organization
                else None
            ),
        )
    except ValueError:
        # An all-empty presentation (roles-only audience) is rejected by
        # NexusPresentation.validate_bounded_style; that is not an error, it
        # simply means there is no presentation profile to inject.
        return None


def readership_instructions_for_phase(
    phase: Literal["phase1", "phase2", "phase3"],
    audience: "PackAudienceSpec | None",
) -> str:
    """Render the pack's declared reader roles, Phase 3 only, wording-only."""

    if phase != "phase3" or audience is None or not audience.roles:
        return ""
    roles = ", ".join(role.strip() for role in audience.roles if role.strip())
    if not roles:
        return ""
    return (
        "- Primary readership declared by the active pack: "
        f"{roles}. Choose wording and emphasis for these readers; this must not "
        "change findings, evidence requirements, required sections, or gates."
    )


def presentation_instructions_for_phase(
    phase: Literal["phase1", "phase2", "phase3"],
    presentation: NexusPresentation | None,
) -> str:
    """Map bounded enums to instructions only at the Phase 3 rendering boundary."""
    if phase != "phase3" or presentation is None:
        return ""

    instructions = [
        "PRESENTATION PROFILE (WORDING AND LAYOUT ONLY):",
        "These choices must not change findings, evidence requirements, required sections, "
        "Pack selection, certification, or quality and safety gates.",
    ]
    if presentation.audience is not None:
        instructions.append(_AUDIENCE_INSTRUCTIONS[presentation.audience])
    if presentation.tone is not None:
        instructions.append(_TONE_INSTRUCTIONS[presentation.tone])
    if presentation.technical_depth is not None:
        instructions.append(_DEPTH_INSTRUCTIONS[presentation.technical_depth])
    if presentation.output_organization is not None:
        instructions.append(_ORGANIZATION_INSTRUCTIONS[presentation.output_organization])
    for option in NexusFormattingOption:
        if option in presentation.formatting:
            instructions.append(_FORMATTING_INSTRUCTIONS[option])
    for option in NexusCommunicationOption:
        if option in presentation.communication:
            instructions.append(_COMMUNICATION_INSTRUCTIONS[option])
    return "\n".join(f"- {instruction}" for instruction in instructions)
