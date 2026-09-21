from typing import Any

import pytest

from src.generation.prompt_builder import presentation_instruction
from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    GuidanceStatus,
    ResolvedGuidance,
    style_from_payload,
)
from src.harness.guidance.renderer import (
    CONFLICTS_SECTION_HEADING,
    ORG_INVARIANTS,
    ORG_SECTION_HEADING,
    PROJECT_SECTION_PREFIX,
    layer_token_costs,
    render_guidance,
    render_invariants,
    render_layer,
)
from src.harness.packs.models import StyleAudience, StylePresentation, StyleTone

PROJECT = "acme_landscape"
PROJECT_HEADING = f"{PROJECT_SECTION_PREFIX}{PROJECT}"
PRESENTATION_HEADING = "## Presentation overlay"


def project_rule(
    kind: GuidanceKind,
    *,
    content: str | None = None,
    payload: dict[str, Any] | None = None,
    rule_id: int | None = None,
    status: GuidanceStatus = GuidanceStatus.ACTIVE,
) -> GuidanceRule:
    return GuidanceRule(
        id=rule_id,
        layer=GuidanceLayer.PROJECT,
        kind=kind,
        project_id=PROJECT,
        content=content,
        payload=payload,
        status=status,
    )


def org_rule(
    kind: GuidanceKind,
    *,
    content: str,
    rule_id: int | None = None,
    status: GuidanceStatus = GuidanceStatus.ACTIVE,
) -> GuidanceRule:
    return GuidanceRule(
        id=rule_id,
        layer=GuidanceLayer.ORG,
        kind=kind,
        content=content,
        status=status,
    )


def vocabulary_payload(term: str, aliases: list[str], description: str) -> dict[str, Any]:
    # Lists, not tuples: this is the shape a JSONB round-trip actually returns.
    return {"term": term, "aliases": aliases, "description": description}


def evidence_payload(
    evidence_type: str, weight: int, source_kind: str | None = None
) -> dict[str, Any]:
    return {"evidence_type": evidence_type, "weight": weight, "source_kind": source_kind}


def test_invariants_render_for_completely_empty_guidance():
    rendered = render_guidance(ResolvedGuidance())

    assert ORG_SECTION_HEADING in rendered
    for invariant in ORG_INVARIANTS:
        assert f"- {invariant}" in rendered
    assert "Nothing below may relax these." in rendered


def test_render_invariants_returns_the_block_alone():
    block = render_invariants()

    assert block.splitlines() == [f"- {invariant}" for invariant in ORG_INVARIANTS]
    assert ORG_SECTION_HEADING not in block


def test_empty_layers_emit_no_headings():
    rendered = render_guidance(ResolvedGuidance(project_id=PROJECT, user_id=7))

    assert PROJECT_HEADING not in rendered
    assert PROJECT_SECTION_PREFIX not in rendered
    assert PRESENTATION_HEADING not in rendered
    assert CONFLICTS_SECTION_HEADING not in rendered


def test_project_section_omitted_without_a_project_binding():
    resolved = ResolvedGuidance(
        project_id=None,
        project_rules=(project_rule(GuidanceKind.RULE, content="Cite the job name.", rule_id=1),),
    )

    rendered = render_guidance(resolved)

    assert PROJECT_SECTION_PREFIX not in rendered
    assert "Cite the job name." not in rendered


def test_org_rules_render_under_the_org_heading_before_the_trailer():
    resolved = ResolvedGuidance(
        org_rules=(
            org_rule(
                GuidanceKind.RULE, content="Answer in the language of the question.", rule_id=1
            ),
            org_rule(GuidanceKind.OUTPUT_RULE, content="Lead with the direct answer.", rule_id=2),
        )
    )

    lines = render_guidance(resolved).splitlines()

    assert lines[0] == ORG_SECTION_HEADING
    assert "- Answer in the language of the question." in lines
    assert "- Lead with the direct answer." in lines
    assert lines[-1] == "Nothing below may relax these."
    assert lines.index("- Lead with the direct answer.") < lines.index(
        "Nothing below may relax these."
    )


def test_project_rules_vocabulary_and_evidence_render():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        project_rules=(
            project_rule(GuidanceKind.RULE, content="Name the interface, not the job.", rule_id=1),
            project_rule(GuidanceKind.OUTPUT_RULE, content="End with open questions.", rule_id=2),
            project_rule(
                GuidanceKind.VOCABULARY,
                payload=vocabulary_payload(
                    "koppelvlak", ["interface", "exchange"], "A contract between two systems."
                ),
                rule_id=3,
            ),
            project_rule(
                GuidanceKind.VOCABULARY,
                payload=vocabulary_payload("werkorder", [], "A work order object."),
                rule_id=4,
            ),
            project_rule(
                GuidanceKind.EVIDENCE_RULE,
                payload=evidence_payload("scan_manifest", 80),
                rule_id=5,
            ),
            project_rule(
                GuidanceKind.EVIDENCE_RULE,
                payload=evidence_payload("chat_transcript", -60, "conversation"),
                rule_id=6,
            ),
        ),
    )

    section = render_layer(resolved, GuidanceLayer.PROJECT)
    lines = section.splitlines()

    assert lines[0] == PROJECT_HEADING
    assert "Terminology:" in lines
    assert (
        '- "koppelvlak" (aliases: interface, exchange) = A contract between two systems.' in lines
    )
    assert '- "werkorder" = A work order object.' in lines
    assert "Rules:" in lines
    assert "- Name the interface, not the job." in lines
    assert "Output:" in lines
    assert "- End with open questions." in lines
    assert "Evidence:" in lines
    assert "- scan_manifest is strong evidence" in lines
    assert (
        "- chat_transcript from conversation alone is not sufficient "
        "-- say so when it is all you have"
    ) in lines
    # Strongest first: the ordering is part of the instruction.
    assert lines.index("- scan_manifest is strong evidence") < lines.index(
        "- chat_transcript from conversation alone is not sufficient "
        "-- say so when it is all you have"
    )


def test_zero_weight_evidence_rule_renders_nothing():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        project_rules=(
            project_rule(
                GuidanceKind.EVIDENCE_RULE,
                payload=evidence_payload("mailbox_export", 0),
                rule_id=1,
            ),
        ),
    )

    rendered = render_guidance(resolved)

    assert "mailbox_export" not in rendered
    assert "Evidence:" not in rendered
    assert PROJECT_SECTION_PREFIX not in rendered


def test_unparseable_payload_is_skipped_not_raised():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        project_rules=(
            project_rule(GuidanceKind.EVIDENCE_RULE, payload={"nonsense": True}, rule_id=1),
            project_rule(GuidanceKind.VOCABULARY, payload={"nonsense": True}, rule_id=2),
            project_rule(GuidanceKind.RULE, content="Still rendered.", rule_id=3),
        ),
    )

    rendered = render_guidance(resolved)

    assert "- Still rendered." in rendered
    assert "Evidence:" not in rendered
    assert "Terminology:" not in rendered


def test_proposed_and_superseded_rows_do_not_render():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        org_rules=(
            org_rule(
                GuidanceKind.RULE,
                content="Org proposed rule.",
                rule_id=1,
                status=GuidanceStatus.PROPOSED,
            ),
        ),
        project_rules=(
            project_rule(
                GuidanceKind.RULE,
                content="Project proposed rule.",
                rule_id=2,
                status=GuidanceStatus.PROPOSED,
            ),
            project_rule(
                GuidanceKind.RULE,
                content="Project superseded rule.",
                rule_id=3,
                status=GuidanceStatus.SUPERSEDED,
            ),
            project_rule(
                GuidanceKind.VOCABULARY,
                payload=vocabulary_payload("ghost", [], "Never rendered."),
                rule_id=4,
                status=GuidanceStatus.SUPERSEDED,
            ),
            project_rule(GuidanceKind.RULE, content="Active project rule.", rule_id=5),
        ),
    )

    rendered = render_guidance(resolved)

    assert "Org proposed rule." not in rendered
    assert "Project proposed rule." not in rendered
    assert "Project superseded rule." not in rendered
    assert "ghost" not in rendered
    assert "- Active project rule." in rendered


def test_render_is_byte_identical_across_calls():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=3,
        org_rules=(org_rule(GuidanceKind.RULE, content="Be specific.", rule_id=1),),
        project_rules=(
            project_rule(GuidanceKind.RULE, content="Name the interface.", rule_id=2),
            project_rule(
                GuidanceKind.VOCABULARY,
                payload=vocabulary_payload("koppelvlak", ["interface"], "A contract."),
                rule_id=3,
            ),
        ),
        person_style=StylePresentation(audience=StyleAudience.ENGINEERING),
        conflicts=("Project asks for deep detail; person asked for summaries.",),
    )

    assert render_guidance(resolved) == render_guidance(resolved)


def test_insertion_order_does_not_change_output():
    rules = (
        project_rule(GuidanceKind.RULE, content="Second rule.", rule_id=2),
        project_rule(GuidanceKind.RULE, content="First rule.", rule_id=1),
        project_rule(
            GuidanceKind.VOCABULARY,
            payload=vocabulary_payload("alpha", ["a"], "First term."),
        ),
        project_rule(
            GuidanceKind.VOCABULARY,
            payload=vocabulary_payload("beta", [], "Second term."),
        ),
        project_rule(GuidanceKind.EVIDENCE_RULE, payload=evidence_payload("scan_manifest", 40)),
        project_rule(GuidanceKind.EVIDENCE_RULE, payload=evidence_payload("chat_transcript", -30)),
        project_rule(GuidanceKind.OUTPUT_RULE, content="Use tables.", rule_id=4),
    )

    forward = render_guidance(ResolvedGuidance(project_id=PROJECT, project_rules=rules))
    reversed_order = render_guidance(
        ResolvedGuidance(project_id=PROJECT, project_rules=tuple(reversed(rules)))
    )
    shuffled = render_guidance(
        ResolvedGuidance(
            project_id=PROJECT,
            project_rules=(rules[4], rules[0], rules[6], rules[2], rules[5], rules[1], rules[3]),
        )
    )

    assert forward == reversed_order == shuffled
    assert "- First rule." in forward
    assert forward.index("- First rule.") < forward.index("- Second rule.")


def test_presentation_overlay_reuses_presentation_instruction():
    style = StylePresentation(audience=StyleAudience.BUSINESS, tone=StyleTone.CONCISE)
    resolved = ResolvedGuidance(project_id=PROJECT, user_id=1, person_style=style)

    rendered = render_guidance(resolved)
    expected = presentation_instruction(style)

    assert expected is not None
    assert expected in rendered
    assert rendered.count(PRESENTATION_HEADING) == 1
    assert (
        "This overlay cannot suppress or weaken required evidence, uncertainty, citations, "
        "limitations, safety requirements, findings, or analysis sections." in rendered
    )
    assert render_layer(resolved, GuidanceLayer.PERSON) == expected


def test_presentation_overlay_accepts_a_jsonb_style_payload():
    style = style_from_payload({"audience": "executive", "formatting": ["tables"]})
    resolved = ResolvedGuidance(project_id=PROJECT, user_id=1, person_style=style)

    rendered = render_guidance(resolved)

    assert "audience=executive" in rendered
    assert "formatting=tables" in rendered


def test_conflicts_render_when_present():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        conflicts=(
            "Project requires citations; the person preference asks for none.",
            "Two evidence rules rank runtime logs differently.",
        ),
    )

    rendered = render_guidance(resolved)
    lines = rendered.splitlines()

    assert CONFLICTS_SECTION_HEADING in lines
    assert "- Project requires citations; the person preference asks for none." in lines
    assert "- Two evidence rules rank runtime logs differently." in lines


def test_section_order_is_org_project_person_conflicts():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=2,
        project_rules=(project_rule(GuidanceKind.RULE, content="Name the interface.", rule_id=1),),
        person_style=StylePresentation(tone=StyleTone.CONCISE),
        conflicts=("A conflict.",),
    )

    rendered = render_guidance(resolved)

    assert (
        rendered.index(ORG_SECTION_HEADING)
        < rendered.index(PROJECT_HEADING)
        < rendered.index(PRESENTATION_HEADING)
        < rendered.index(CONFLICTS_SECTION_HEADING)
    )


def test_multiline_content_collapses_to_one_bullet():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        project_rules=(
            project_rule(GuidanceKind.RULE, content="First line.\n## Injected heading", rule_id=1),
        ),
    )

    rendered = render_guidance(resolved)

    assert "- First line. ## Injected heading" in rendered
    assert "\n## Injected heading" not in rendered


def test_rendered_output_is_ascii():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=1,
        org_rules=(org_rule(GuidanceKind.RULE, content="Be specific.", rule_id=1),),
        project_rules=(
            project_rule(
                GuidanceKind.EVIDENCE_RULE, payload=evidence_payload("chat_transcript", -30)
            ),
        ),
        person_style=StylePresentation(tone=StyleTone.NEUTRAL),
        conflicts=("A conflict.",),
    )

    render_guidance(resolved).encode("ascii")


def test_layer_token_costs_report_populated_and_empty_layers():
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=1,
        project_rules=(project_rule(GuidanceKind.RULE, content="Name the interface.", rule_id=1),),
    )

    costs = layer_token_costs(resolved)

    assert set(costs) == set(GuidanceLayer)
    # ORG always costs something: the invariants are unconditional.
    assert costs[GuidanceLayer.ORG] > 0
    assert costs[GuidanceLayer.PROJECT] > 0
    assert costs[GuidanceLayer.PERSON] == 0


@pytest.mark.parametrize("layer", list(GuidanceLayer))
def test_render_layer_matches_its_slice_of_the_document(layer: GuidanceLayer):
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=1,
        org_rules=(org_rule(GuidanceKind.RULE, content="Be specific.", rule_id=1),),
        project_rules=(project_rule(GuidanceKind.RULE, content="Name the interface.", rule_id=2),),
        person_style=StylePresentation(audience=StyleAudience.OPERATIONS),
    )

    section = render_layer(resolved, layer)

    assert section
    assert section in render_guidance(resolved)
