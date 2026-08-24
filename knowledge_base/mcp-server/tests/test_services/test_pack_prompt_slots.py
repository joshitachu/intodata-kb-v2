"""The slot catalog is what the authoring UI believes about the pipeline.

If it offers a slot the pipeline does not read, an author writes a prompt that
is never used and has no way to discover that. If it omits one, a prompt stays
unreachable in practice even though the mechanism supports it. Either way the
failure is silent, which is why these are asserted against live code rather
than against a fixture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.services.pack_prompt_slots import (
    PHASE_TEMPLATES,
    prompt_slots,
    slot_for,
)

REPO_MCP_SERVER = Path(__file__).resolve().parents[2]
SRC = REPO_MCP_SERVER / "src"
TEMPLATE_ROOT = REPO_MCP_SERVER / "data" / "prompt_templates"


@pytest.fixture(scope="module")
def slots():
    return prompt_slots()


def _templates_rendered_with_a_pack() -> set[str]:
    """Template names some ``render(..., pack_dir=)`` call site actually reads."""
    found: set[str] = set()
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for match in re.finditer(r'render\(\s*"([a-z0-9_]+\.md)"(.*?)pack_dir=', text, re.DOTALL):
            if "render(" in match.group(2):  # the DOTALL span ran into a later call
                continue
            found.add(match.group(1))
    return found


def test_every_body_slot_is_reachable(slots):
    """A body slot the pipeline never reads is a trap for the author."""
    reachable = _templates_rendered_with_a_pack()
    assert reachable, "found no pack-aware render() calls at all"
    for slot in slots:
        if slot.kind != "body":
            continue
        name = slot.path.removeprefix("prompts/templates/")
        assert name in reachable, (
            f"{slot.path} is offered to authors but no render(..., pack_dir=) "
            "call site reads it"
        )


def test_the_patch_templates_are_not_offered(slots):
    """They render only from the living-docs executor, which has no pack.

    Named explicitly rather than covered by the reachability test alone: this is
    the one group someone would plausibly add by hand, reasoning that they are
    templates like the others.
    """
    offered = {slot.path for slot in slots}
    for name in ("phase2_patch.md", "phase3_patch.md", "phase3_synthesis_patch.md"):
        assert (TEMPLATE_ROOT / name).is_file(), f"{name} vanished; revisit this test"
        assert f"prompts/templates/{name}" not in offered


def test_every_body_slot_carries_the_current_built_in_text(slots):
    """The seed must be the live template, byte for byte.

    An override REPLACES the built-in, so seeding from stale text would silently
    revert whatever changed in between -- the redaction rules, the enumeration
    obligation -- and look like the author's own file.
    """
    for slot in slots:
        if slot.kind != "body":
            continue
        source = TEMPLATE_ROOT / slot.path.removeprefix("prompts/templates/")
        assert slot.default_content == source.read_text(encoding="utf-8")


def test_document_slots_track_the_catalog(slots):
    """A document added to the catalog must become offerable without a code edit."""
    catalog_names = {doc.name for doc in load_doc_catalog().docs}
    offered = {
        slot.path.removeprefix("prompts/docs/").removesuffix(".md")
        for slot in slots
        if slot.kind == "document"
    }
    assert offered == catalog_names


def test_investigation_preamble_has_no_default(slots):
    """It is PREPENDED, not substituted.

    The built-in path prepends nothing, so seeding this with any text would
    describe an instruction the pipeline never issues -- and because it is
    additive, a copy of some other prompt here would be sent IN ADDITION to
    the real one.
    """
    slot = slot_for("prompts/investigation.md")
    assert slot is not None
    assert slot.kind == "preamble"
    assert slot.default_content == ""
    assert slot.has_default is False


def test_platform_guides_are_immutable_and_only_extensions_are_offered(slots):
    paths = {slot.path for slot in slots}
    assert not any(path.startswith("prompts/phase2_platform_sections") for path in paths)
    extensions = [slot for slot in slots if slot.kind == "platform_extension"]
    assert extensions
    assert {slot.path for slot in extensions} == {
        f"prompts/extensions/platforms/{path.stem}.md"
        for path in (TEMPLATE_ROOT / "phase2_platform_sections").glob("*.md")
    }
    assert all(slot.default_content == "" for slot in extensions)


def test_preamble_and_body_scorecard_are_distinct_slots(slots):
    """`prompts/scorecard.md` and `prompts/templates/scorecard.md` both exist.

    This collision is the reason bodies live in their own directory. If the two
    ever resolve to one path, one silently shadows the other.
    """
    preamble = slot_for("prompts/scorecard.md")
    body = slot_for("prompts/templates/scorecard.md")
    assert preamble is not None and body is not None
    assert preamble.kind == "preamble"
    assert body.kind == "body"
    assert preamble.default_content != body.default_content


def test_slot_paths_are_unique(slots):
    paths = [slot.path for slot in slots]
    assert len(paths) == len(set(paths))


def test_phase_templates_is_the_only_list(slots):
    """The reference-pack generator must not keep a second copy.

    Two lists would disagree the first time a phase gained a prompt, and the
    one that fell behind would be the pack that ships to authors.
    """
    generator = (REPO_MCP_SERVER / "scripts" / "generate_reference_pack.py").read_text()
    assert "from mcp_server.services.pack_prompt_slots import PHASE_TEMPLATES" in generator
    # No hand-written template filenames left in the generator.
    for _phase, entries in PHASE_TEMPLATES:
        for name, _description in entries:
            assert f'"{name}"' not in generator, (
                f"{name} is hardcoded in the generator; it should come from PHASE_TEMPLATES"
            )


def test_unknown_slot_lookup_returns_none():
    assert slot_for("prompts/templates/not_a_template.md") is None
    assert slot_for("../../etc/passwd") is None
