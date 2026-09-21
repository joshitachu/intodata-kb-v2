"""The manifest section: what it says, what it must never say, and what it costs.

The manifest exists to make the agent act, so the tests are about directives
rather than prose. Two of them pin failures that are silent in production: a
path emitted for a document that was never written (a guaranteed failed read),
and a constraint dropped so that background material could fit (a requirement
the answer was supposed to respect, traded for something it might not need).
"""

from __future__ import annotations

from src.harness.guidance.material import (
    MANIFEST_TOKEN_BUDGET,
    MaterialEntry,
    MaterialKind,
    ProjectMaterial,
    workspace_relpath,
)
from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    ResolvedGuidance,
    estimate_tokens,
)
from src.harness.guidance.renderer import (
    CONFLICTS_SECTION_HEADING,
    MANIFEST_SECTION_HEADING,
    MATERIAL_PATH_PREFIX,
    PROJECT_SECTION_PREFIX,
    render_guidance,
    render_layer,
    render_manifest,
)
from src.harness.packs.models import StyleAudience, StylePresentation, StyleTone

PROJECT = "acme_landscape"
PRESENTATION_HEADING = "## Presentation overlay"
SEARCH_TOOL = "nexus_search_documentation"

_ROLE_BY_KIND = {
    MaterialKind.CONSTRAINT: "policy",
    MaterialKind.METHOD: "guidance",
    MaterialKind.REFERENCE: "reference",
    MaterialKind.EVIDENCE: "source",
}


def entry(
    document_id: int,
    kind: MaterialKind,
    *,
    filename: str | None = None,
    materialized: bool = True,
    role: str | None = None,
) -> MaterialEntry:
    return MaterialEntry(
        document_id=document_id,
        filename=filename or f"document-{document_id}.pdf",
        role=role or _ROLE_BY_KIND[kind],
        kind=kind,
        char_count=1_000 if materialized else 200_000,
        materialized=materialized,
    )


def material(*entries: MaterialEntry, overflow: int = 0) -> ProjectMaterial:
    return ProjectMaterial(
        project_id=PROJECT, entries=entries, overflow_count=overflow, cache_key="k"
    )


def manifest_lines(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.startswith("- ")]


# -- silence ----------------------------------------------------------------


def test_empty_material_emits_no_heading() -> None:
    assert render_manifest(ProjectMaterial()) == ""
    assert render_manifest(ProjectMaterial(project_id=PROJECT, cache_key="k")) == ""


def test_render_guidance_omits_the_section_for_empty_material() -> None:
    resolved = ResolvedGuidance(project_id=PROJECT, user_id=1)

    rendered = render_guidance(resolved, ProjectMaterial(project_id=PROJECT))

    assert MANIFEST_SECTION_HEADING not in rendered


# -- the line itself --------------------------------------------------------


def test_materialized_entry_renders_its_workspace_path() -> None:
    policy = entry(41, MaterialKind.CONSTRAINT, filename="werkorder-integraties-analyse.pdf")
    method = entry(7, MaterialKind.METHOD, filename="nexus-testbevindingen.docx")
    reference = entry(9, MaterialKind.REFERENCE, filename="architecture-model.pdf")

    lines = manifest_lines(render_manifest(material(policy, method, reference)))

    # The path comes from ``workspace_relpath`` here for the same reason it does
    # in the renderer: the layout is the material contract's to decide, and a
    # copy of it in either place is a copy that eventually points somewhere no
    # file was written.
    assert lines == [
        (
            "- [policy] werkorder integraties analyse -- binding constraints; read "
            f"{MATERIAL_PATH_PREFIX}{workspace_relpath(policy)} "
            "before making a recommendation."
        ),
        (
            "- [guidance] nexus testbevindingen -- method; read "
            f"{MATERIAL_PATH_PREFIX}{workspace_relpath(method)} "
            "before doing that kind of work."
        ),
        (
            "- [reference] architecture model -- background; read "
            f"{MATERIAL_PATH_PREFIX}{workspace_relpath(reference)} "
            "before answering a question it covers."
        ),
    ]
    # Layout check: every path is id-prefixed, because a slug alone collides
    # (``guide.docx`` and ``guide.pdf`` share a stem) and a collision would
    # point one manifest line at another document's file.
    assert f"{MATERIAL_PATH_PREFIX}references/41-werkorder-integraties-analyse.md" in lines[0]
    assert f"{MATERIAL_PATH_PREFIX}skills/7-nexus-testbevindingen/SKILL.md" in lines[1]


def test_non_materialized_entry_renders_no_path_and_says_to_search() -> None:
    huge = entry(12, MaterialKind.CONSTRAINT, filename="group-standard.pdf", materialized=False)

    section = render_manifest(material(huge))

    assert manifest_lines(section) == [
        (
            "- [policy] group standard -- binding constraints; too large to mount, search it "
            f"with {SEARCH_TOOL} before making a recommendation."
        )
    ]
    # A path here would cost the agent a failed read and a wasted step.
    assert MATERIAL_PATH_PREFIX not in section
    assert workspace_relpath(huge) not in section


def test_every_line_ends_in_a_trigger_condition() -> None:
    section = render_manifest(
        material(
            entry(1, MaterialKind.CONSTRAINT),
            entry(2, MaterialKind.METHOD, materialized=False),
            entry(3, MaterialKind.REFERENCE),
        )
    )

    triggers = (
        "before making a recommendation.",
        "before doing that kind of work.",
        "before answering a question it covers.",
    )
    for line in manifest_lines(section):
        assert line.endswith(triggers), line


def test_a_line_stays_one_line() -> None:
    section = render_manifest(
        material(entry(1, MaterialKind.CONSTRAINT, filename="house\nrules.pdf"))
    )

    assert len(manifest_lines(section)) == 1
    assert "\n## " not in section


def test_manifest_is_ascii() -> None:
    render_manifest(
        material(
            entry(1, MaterialKind.CONSTRAINT),
            entry(2, MaterialKind.REFERENCE, materialized=False),
            overflow=3,
        )
    ).encode("ascii")


# -- overflow ---------------------------------------------------------------


def test_overflow_line_reports_what_is_not_listed() -> None:
    section = render_manifest(material(entry(1, MaterialKind.CONSTRAINT), overflow=14))

    assert section.splitlines()[-1] == (
        f"- 14 further documents are not listed; search them with {SEARCH_TOOL}."
    )


def test_overflow_line_is_singular_for_one_document() -> None:
    section = render_manifest(material(entry(1, MaterialKind.CONSTRAINT), overflow=1))

    assert section.splitlines()[-1] == (
        f"- 1 further document is not listed; search it with {SEARCH_TOOL}."
    )


def test_overflow_alone_still_renders_the_section() -> None:
    section = render_manifest(ProjectMaterial(project_id=PROJECT, overflow_count=6))

    assert section.startswith(MANIFEST_SECTION_HEADING)
    assert "6 further documents" in section


# -- the token budget -------------------------------------------------------


def test_budget_drops_references_before_constraints() -> None:
    entries = (
        *(entry(index, MaterialKind.CONSTRAINT) for index in range(1, 4)),
        *(entry(index, MaterialKind.METHOD) for index in range(10, 13)),
        *(entry(index, MaterialKind.REFERENCE) for index in range(100, 118)),
    )

    section = render_manifest(material(*entries))
    lines = manifest_lines(section)

    assert estimate_tokens(section) <= MANIFEST_TOKEN_BUDGET
    # Something had to go, and it was references.
    assert len(lines) < len(entries)
    assert sum(1 for line in lines if line.startswith("- [policy]")) == 3
    assert sum(1 for line in lines if line.startswith("- [reference]")) < 18
    # Nothing vanished: the dropped entries are accounted for in the tail line.
    dropped = len(entries) - (len(lines) - 1)
    assert lines[-1] == (
        f"- {dropped} further documents are not listed; search them with {SEARCH_TOOL}."
    )


def test_budget_keeps_constraints_even_when_they_alone_exceed_it() -> None:
    entries = tuple(entry(index, MaterialKind.CONSTRAINT) for index in range(1, 31))

    lines = manifest_lines(render_manifest(material(*entries)))

    # Constraints are last to go, so the section degrades to fewer constraints
    # plus an honest count -- never to a section of references.
    assert all(line.startswith("- [policy]") for line in lines[:-1])
    assert lines[-1].endswith(f"search them with {SEARCH_TOOL}.")


def test_budget_does_not_touch_a_section_that_fits() -> None:
    entries = (entry(1, MaterialKind.CONSTRAINT), entry(2, MaterialKind.REFERENCE))

    section = render_manifest(material(*entries, overflow=2))

    assert len(manifest_lines(section)) == 3
    assert "2 further documents" in section


# -- determinism ------------------------------------------------------------


def test_manifest_is_byte_identical_across_calls() -> None:
    subject = material(
        entry(1, MaterialKind.CONSTRAINT),
        entry(2, MaterialKind.METHOD, materialized=False),
        entry(3, MaterialKind.REFERENCE),
        overflow=4,
    )

    assert render_manifest(subject) == render_manifest(subject)


def test_entry_order_does_not_change_the_rendered_manifest() -> None:
    entries = (
        entry(3, MaterialKind.REFERENCE),
        entry(1, MaterialKind.CONSTRAINT),
        entry(2, MaterialKind.METHOD),
        entry(4, MaterialKind.CONSTRAINT),
    )

    forward = render_manifest(material(*entries))
    reversed_order = render_manifest(material(*reversed(entries)))
    shuffled = render_manifest(material(entries[2], entries[3], entries[0], entries[1]))

    assert forward == reversed_order == shuffled
    assert manifest_lines(forward)[0].startswith("- [policy]")


# -- placement in the stack -------------------------------------------------


def test_manifest_sits_after_the_project_section_and_before_the_overlay() -> None:
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=2,
        project_rules=(
            GuidanceRule(
                id=1,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.RULE,
                project_id=PROJECT,
                content="Name the interface.",
            ),
        ),
        person_style=StylePresentation(tone=StyleTone.CONCISE),
        conflicts=("A conflict.",),
    )

    rendered = render_guidance(resolved, material(entry(1, MaterialKind.CONSTRAINT)))

    assert (
        rendered.index(f"{PROJECT_SECTION_PREFIX}{PROJECT}")
        < rendered.index(MANIFEST_SECTION_HEADING)
        < rendered.index(PRESENTATION_HEADING)
        < rendered.index(CONFLICTS_SECTION_HEADING)
    )


def test_render_guidance_without_material_is_unchanged() -> None:
    resolved = ResolvedGuidance(
        project_id=PROJECT,
        user_id=3,
        org_rules=(
            GuidanceRule(id=1, layer=GuidanceLayer.ORG, kind=GuidanceKind.RULE, content="Be specific."),
        ),
        project_rules=(
            GuidanceRule(
                id=2,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.RULE,
                project_id=PROJECT,
                content="Name the interface.",
            ),
        ),
        person_style=StylePresentation(audience=StyleAudience.ENGINEERING, tone=StyleTone.CONCISE),
        conflicts=("A conflict.",),
    )

    # The pre-change document, assembled from the same public parts the old
    # implementation joined: org, project, person, conflicts, nothing between.
    expected = "\n\n".join(
        section
        for section in (
            render_layer(resolved, GuidanceLayer.ORG),
            render_layer(resolved, GuidanceLayer.PROJECT),
            render_layer(resolved, GuidanceLayer.PERSON),
            f"{CONFLICTS_SECTION_HEADING}\n- A conflict.",
        )
        if section
    )

    assert render_guidance(resolved) == expected
    assert render_guidance(resolved, None) == expected
    assert MANIFEST_SECTION_HEADING not in render_guidance(resolved)
