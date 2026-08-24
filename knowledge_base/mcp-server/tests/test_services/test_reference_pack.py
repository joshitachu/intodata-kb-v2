"""The reference pack must keep telling the truth about the built-in behaviour.

``pack-repos/nexus-baseline/`` expresses what a run does when no pack is
selected, in pack form, so an author can read it and fork it. Nothing loads it
at runtime.

That makes it documentation, and documentation about code rots. Someone adds a
document to ``doc_catalog.yaml`` or a section to the contract, the reference does
not move, and now it misleads everyone who forks it -- worse than not existing,
because it reads as authoritative.

These tests are the thing that stops that. Two independent checks:

* the committed files match what the generator produces from live code
  (byte-for-byte, via ``--check``);
* the committed pack, loaded through the real pack loader, resolves to the same
  section contract and document set as ``default_contract`` and the catalog.

The second is not redundant. The first catches a stale file; the second catches a
generator that is itself wrong -- it validates the OUTPUT against the source of
truth rather than trusting the code that wrote it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.providers.custom.pack_contract import (
    contract_from_plugin_manifest,
    default_contract,
)
from mcp_server.services.plugin_schema import load_plugin_manifest

REPO_MCP_SERVER = Path(__file__).resolve().parents[2]
REFERENCE_PACK = REPO_MCP_SERVER.parent / "pack-repos" / "nexus-baseline"
GENERATOR = REPO_MCP_SERVER / "scripts" / "generate_reference_pack.py"
PLATFORM = "talend"


@pytest.fixture(scope="module")
def reference_contract():
    manifest = load_plugin_manifest(REFERENCE_PACK)
    return manifest, contract_from_plugin_manifest(manifest, PLATFORM)


def test_reference_pack_exists():
    assert (REFERENCE_PACK / "plugin.yaml").is_file(), (
        f"{REFERENCE_PACK} is missing. Run: python scripts/generate_reference_pack.py"
    )


def test_committed_reference_pack_is_not_stale():
    """The generator's --check mode is the drift gate."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_MCP_SERVER,
    )
    assert result.returncode == 0, (
        "The reference pack has drifted from the live catalog/contract.\n"
        "Regenerate it:\n"
        "    cd knowledge_base/mcp-server && python scripts/generate_reference_pack.py\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_reference_pack_is_certified_for_the_configured_repository(reference_contract):
    """Without the provenance the governance config expects, branch discovery
    silently SKIPS it -- the pack would sit on GitHub looking fine and never
    appear in the shared list."""
    manifest, _ = reference_contract
    assert manifest.certification is not None, "no certification block"
    assert manifest.certification.provenance


def test_reference_pack_is_a_valid_pack(reference_contract):
    """A reference that will not load teaches the wrong thing."""
    manifest, contract = reference_contract
    assert manifest.name == "nexus-baseline"
    assert contract.is_pack
    assert contract.docs, "the reference must declare the document set"
    assert contract.declared_sections, "the reference must declare the section contract"


def test_reference_sections_match_the_builtin_contract(reference_contract):
    _, contract = reference_contract
    builtin = default_contract(PLATFORM)
    assert tuple(contract.analysis_section_names) == tuple(
        builtin.analysis_section_names
    ), "the reference declares a different section set than a no-pack run uses"


def test_reference_documents_match_the_catalog(reference_contract):
    _, contract = reference_contract
    catalog = load_doc_catalog()
    assert contract.doc_names() == tuple(d.name for d in catalog.docs)
    assert {d.name: d.phase2_sections for d in contract.docs} == {
        d.name: tuple(d.phase2_sections) for d in catalog.docs
    }, "the reference routes analysis differently than doc_catalog.yaml does"
    assert {d.name for d in contract.docs if d.per_unit} == {
        d.name for d in catalog.docs if d.per_unit
    }


def test_reference_mode_is_extend(reference_contract):
    """`extend` + the built-in list is a no-op, which is what makes it a
    faithful mirror. `replace` would be a narrowing the built-in path does not
    do, and would quietly change the relationship rollups."""
    _, contract = reference_contract
    assert contract.mode == "extend"
    assert contract.relationship_section_names == ("CROSS_GROUP_REFS", "ESB_ROUTES")


def test_reference_pack_declares_no_section_questions(reference_contract):
    """The built-in path asks by section NAME; meaning lives in the skeleton.

    Inventing a `question` here would make the reference describe an instruction
    the built-in path never issues -- wrong in the way that reads as right.
    The skeleton is shipped verbatim as prompts/phase2.md instead.
    """
    _, contract = reference_contract
    assert all(spec.question == "" for spec in contract.declared_sections)
    assert (REFERENCE_PACK / "prompts" / "phase2.md").is_file()


#: Pack paths the pipeline actually reads: the three preamble slots, the
#: per-document instructions and the prompt bodies under ``prompts/templates/``
#: (resolved generically by
#: ``PromptRenderer.render``).
_PREAMBLE_SLOTS = {
    "prompts/phase2.md",
    "prompts/phase3.md",
    "prompts/scorecard.md",
}


def _is_read_by_the_pipeline(rel: str) -> bool:
    return (
        rel in _PREAMBLE_SLOTS
        or rel == "prompts/investigation.md"
        or rel.startswith("prompts/docs/")
        or rel.startswith("prompts/templates/")
    )


def test_everything_under_prompts_is_a_slot_the_harness_reads():
    """A file under prompts/ that nothing reads teaches the wrong thing."""
    for path in (REFERENCE_PACK / "prompts").rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REFERENCE_PACK).as_posix()
        assert _is_read_by_the_pipeline(rel), (
            f"{rel} is under prompts/ but the harness never reads it"
        )


def test_every_shipped_template_override_is_reachable_from_the_pipeline():
    """A body override is only real if some render() call passes pack_dir.

    ``prompts/templates/`` is resolved generically, so shipping a file named
    after a template that no pack-aware call site renders would produce a repo
    entry that silently does nothing -- exactly the failure this pack exists to
    avoid. The three patch templates are the live example: they render, but only
    from the living-docs executor, which builds its own renderer with no pack.
    """
    import re

    src = REPO_MCP_SERVER / "src"
    rendered_with_pack: set[str] = set()
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for match in re.finditer(
            r'render\(\s*"([a-z0-9_]+\.md)"(.*?)pack_dir=', text, re.DOTALL
        ):
            # Guard the DOTALL span: it must stay inside this one call, so
            # reject a match that swallowed a later render( of another template.
            if "render(" in match.group(2):
                continue
            rendered_with_pack.add(match.group(1))

    shipped = {
        p.name for p in (REFERENCE_PACK / "prompts" / "templates").glob("*.md")
    }
    assert shipped, "no prompt bodies shipped"
    unreachable = shipped - rendered_with_pack
    assert not unreachable, (
        "shipped template overrides that no pack-aware render() call reads: "
        f"{sorted(unreachable)}"
    )


def test_pack_template_override_actually_replaces_the_body():
    """The mechanism, not the manifest: a body override must WIN.

    Asserted per shipped template rather than once, because each is reached
    through a different call site and a missing ``pack_dir=`` on any one of them
    is invisible at the manifest level.
    """
    import shutil
    import tempfile

    from mcp_server.providers.custom.prompt_renderer import get_renderer

    renderer = get_renderer()
    templates = sorted((REFERENCE_PACK / "prompts" / "templates").glob("*.md"))
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "pack"
        (pack / "prompts" / "templates").mkdir(parents=True)
        for template in templates:
            marker = f"SENTINEL-{template.stem}"
            shutil.copy(template, pack / "prompts" / "templates" / template.name)
            (pack / "prompts" / "templates" / template.name).write_text(
                marker, encoding="utf-8"
            )
            assert renderer.render(template.name, {}, pack_dir=pack) == marker, (
                f"{template.name} override was ignored"
            )
            # ...and the built-in must be untouched for a no-pack run.
            assert renderer.render(template.name, {}) != marker


def test_reference_pack_carries_every_per_document_instruction():
    """The per-document prompt is the highest-leverage file a fork can edit.

    The first version of this pack shipped none of them, so a fork inherited a
    reference that silently dropped the instructions doing most of the work.
    """
    catalog = load_doc_catalog()
    expected = {
        d.name for d in catalog.docs if (d.doc_specific_instructions or "").strip()
    }
    assert expected, "the catalog should carry per-document instructions"
    present = {p.stem for p in (REFERENCE_PACK / "prompts" / "docs").glob("*.md")}
    assert expected <= present, f"missing per-document prompts: {sorted(expected - present)}"


def test_reference_pack_documents_every_immutable_platform_skeleton():
    """The baseline is visible without pretending plugins may replace it."""
    shipped = {
        p.stem
        for p in (
            REFERENCE_PACK / "reference" / "immutable-platform-analysis"
        ).glob("*.md")
    }
    available = {
        p.stem
        for p in (
            REPO_MCP_SERVER / "data" / "prompt_templates" / "phase2_platform_sections"
        ).glob("*.md")
    }
    assert available <= shipped, f"missing skeletons: {sorted(available - shipped)}"


def test_reference_pack_does_not_offer_platform_replacement_files():
    assert not list(
        (REFERENCE_PACK / "prompts" / "phase2_platform_sections").glob("*.md")
    )
    assert not (REFERENCE_PACK / "prompts" / "phase2_platform_sections.md").exists()


def test_pack_renders_byte_identical_prompts_on_every_platform():
    """The whole point: selecting this pack must change nothing.

    Every prompt the harness builds, on every platform with a section skeleton,
    compared byte for byte against the same prompt with no pack selected. This
    is the only assertion that actually proves the pack mirrors the built-in
    behaviour -- earlier versions matched on manifest fields and section counts
    while rendering a prompt 13k chars too long with the preamble missing.

    It also pins three fixes this comparison forced:
      * `lens.requirements` must stay ABSENT (it replaces the PER-PLATFORM
        evidence contract with one string);
      * pack per-document prompts get the same `{unit_count}` substitution the
        built-in ones do (they did not, so a faithful pack was impossible);
      * they are normalized to one trailing newline, as the YAML block scalars
        the built-in path reads always are.
    """
    import json
    import tempfile

    from mcp_server.providers.custom.prompts import (
        doc_prompt,
        group_analysis_prompt,
        scorecard_prompt,
    )
    from mcp_server.providers.custom.validators import build_facts_manifest

    manifest = load_plugin_manifest(REFERENCE_PACK)
    documents = [d.name for d in load_doc_catalog().docs]

    for platform in ("talend", "workato", "lobster", "generic"):
        with tempfile.TemporaryDirectory() as tmp:
            scan = Path(tmp) / "scan"
            scan.mkdir()
            (scan / "scan_result.json").write_text(
                json.dumps(
                    {
                        "platform": platform,
                        "repo_root": "/r",
                        "files": [],
                        "jobs": [
                            {
                                "name": "LoadOrders",
                                "path": "LoadOrders.item",
                                "group": "Orders",
                                "components": [],
                            }
                        ],
                        "manifest": {},
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            facts = build_facts_manifest("p", scan)
            packed = contract_from_plugin_manifest(manifest, platform)
            native = default_contract(platform)

            assert group_analysis_prompt(
                facts, "Orders", contract=packed,
                required_docs=["01_ARCHITECTURE_MAP"], plugin_pack_dir=REFERENCE_PACK,
            ) == group_analysis_prompt(
                facts, "Orders", contract=native, required_docs=["01_ARCHITECTURE_MAP"],
            ), f"phase 2 prompt differs on {platform}"

            for name in documents:
                assert doc_prompt(
                    facts, name, contract=packed, plugin_pack_dir=REFERENCE_PACK
                ) == doc_prompt(facts, name, contract=native), (
                    f"{name} prompt differs on {platform}"
                )

            assert scorecard_prompt(
                facts, plugin_pack_dir=REFERENCE_PACK
            ) == scorecard_prompt(facts), f"scorecard prompt differs on {platform}"


def test_pack_declares_no_lens_requirements():
    """One string cannot express a per-platform contract."""
    manifest = load_plugin_manifest(REFERENCE_PACK)
    assert manifest.lens is not None
    assert not (manifest.lens.requirements or "").strip()


def test_pack_contains_only_runtime_files_and_declared_immutable_references():
    """Every non-runtime file must be an explicit Nexus-policy reference.

    Platform guides are deliberately visible for authors but live outside
    prompts/ so nothing can mistake them for replacement slots.
    """
    allowed_top = {"plugin.yaml", "README.md"}
    for path in REFERENCE_PACK.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(REFERENCE_PACK).as_posix()
        assert (
            rel in allowed_top
            or rel.startswith("reference/immutable-platform-analysis/")
            or _is_read_by_the_pipeline(rel)
        ), (
            f"{rel} is not something the harness reads"
        )


def test_reference_pack_is_inert():
    """It must not be anywhere the server loads packs from on its own.

    The previous attempt at this lived in `mcp-server/data/packs/` and was wired
    as the live default, which created two sources of truth for what actually
    runs. Keeping it outside `data/` is the structural guarantee that cannot
    happen again by accident.
    """
    data_dir = REPO_MCP_SERVER / "data"
    assert not REFERENCE_PACK.is_relative_to(data_dir)
    assert not (data_dir / "packs").exists(), (
        "data/packs/ is back -- that was the live-default activation lane"
    )
