"""One Pack item 8 -- the manifest ``audience:`` block reaches Phase 3.

Agent 2 made ``audience:`` parse and validate but never wired it; the
``nexus_presentation`` -> ``presentation_instructions_for_phase`` seam was fed
only from the RAG side. These tests pin the wiring, the layer ordering (a
request-supplied Style presentation wins over the pack's audience), and the
authority boundary (the enum fields are a closed whitelist, ``roles`` is
bounded free text).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mcp_server.providers.custom.pack_contract import (
    PackAudienceSpec,
    default_contract,
)
from mcp_server.providers.custom.prompts import doc_prompt
from mcp_server.schemas import (
    NexusPresentation,
    NexusStyleAudience,
    NexusStyleTone,
    NexusTechnicalDepth,
)
from mcp_server.services.nexus_execution_contract import (
    presentation_from_pack_audience,
    readership_instructions_for_phase,
)
from mcp_server.providers.custom.pack_contract import contract_from_plugin_manifest
from mcp_server.services.plugin_schema import PluginPackManifest

REPO_MCP_SERVER = Path(__file__).resolve().parents[2]
STARTER_PACK = REPO_MCP_SERVER / "data" / "swap_pack_starter"

AUDIENCE_PACK_YAML = """\
name: sql-injection-review
version: 0.3.0
lens:
  sections:
    mode: replace
    declare:
      - id: INJECTION_SINKS
        question: "every place user-controlled data reaches a query"
docs:
  - name: INJECTION_FINDINGS
    sections: [Summary, Findings by Severity]
audience:
  roles: [security reviewer, tech lead]
  tone: concise
  technical_depth: deep
  output_organization: direct_answer
"""


@pytest.fixture()
def facts():
    """A minimal facts manifest; ``doc_prompt`` only reads platform/units."""
    from mcp_server.providers.custom.models import FactsManifest

    return FactsManifest(
        project_id="p1",
        platform="talend",
        groups={"G1": 1},
        units=[],
        unit_count=0,
        canonical_unit_label="job",
    )


def _compile(yaml_text: str):
    manifest = PluginPackManifest.model_validate(yaml.safe_load(yaml_text))
    return contract_from_plugin_manifest(manifest, "talend")


# ── compilation ───────────────────────────────────────────────────────────


def test_audience_block_compiles_onto_the_contract():
    contract = _compile(AUDIENCE_PACK_YAML)
    assert contract.audience is not None
    assert contract.audience.roles == ("security reviewer", "tech lead")
    assert contract.audience.tone == "concise"
    assert contract.audience.technical_depth == "deep"
    assert contract.audience.output_organization == "direct_answer"


def test_absent_audience_leaves_the_contract_untouched():
    text = AUDIENCE_PACK_YAML.split("audience:")[0]
    assert _compile(text).audience is None


def test_starter_pack_declares_its_audience():
    """The starter is what every pack is copied from: it must show the block."""
    manifest = PluginPackManifest.model_validate(
        yaml.safe_load((STARTER_PACK / "plugin.yaml").read_text(encoding="utf-8"))
    )
    contract = contract_from_plugin_manifest(manifest, "talend")
    assert contract.audience is not None
    assert contract.audience.tone == "neutral"
    assert contract.audience.technical_depth == "standard"
    assert "Integration engineer" in contract.audience.roles


# ── authority boundary: whitelisted enums, bounded roles ──────────────────


@pytest.mark.parametrize(
    "field,value",
    [
        ("tone", "salesy"),
        ("technical_depth", "extremely_deep"),
        ("output_organization", "freeform"),
    ],
)
def test_audience_enum_fields_are_a_closed_whitelist(field, value):
    payload = yaml.safe_load(AUDIENCE_PACK_YAML)
    payload["audience"][field] = value
    with pytest.raises(ValidationError):
        PluginPackManifest.model_validate(payload)


def test_audience_role_count_is_not_artificially_capped():
    payload = yaml.safe_load(AUDIENCE_PACK_YAML)
    payload["audience"]["roles"] = [f"role-{i}" for i in range(50)]
    manifest = PluginPackManifest.model_validate(payload)
    contract = contract_from_plugin_manifest(manifest, "talend")
    assert contract.audience is not None
    assert len(contract.audience.roles) == 50


def test_audience_role_length_is_capped():
    payload = yaml.safe_load(AUDIENCE_PACK_YAML)
    payload["audience"]["roles"] = ["x" * 200]
    with pytest.raises(ValidationError):
        PluginPackManifest.model_validate(payload)


# ── the presentation seam ─────────────────────────────────────────────────


def test_presentation_from_pack_audience_maps_the_enum_fields():
    spec = PackAudienceSpec(tone="concise", technical_depth="deep")
    presentation = presentation_from_pack_audience(spec)
    assert presentation is not None
    assert presentation.tone is NexusStyleTone.CONCISE
    assert presentation.technical_depth is NexusTechnicalDepth.DEEP
    # ``roles`` is free text and must never become a presentation enum.
    assert presentation.audience is None


def test_roles_only_audience_yields_no_presentation_but_a_readership_line():
    spec = PackAudienceSpec(roles=("security reviewer",))
    assert presentation_from_pack_audience(spec) is None
    line = readership_instructions_for_phase("phase3", spec)
    assert "security reviewer" in line


def test_readership_is_phase3_only():
    spec = PackAudienceSpec(roles=("security reviewer",))
    assert readership_instructions_for_phase("phase1", spec) == ""
    assert readership_instructions_for_phase("phase2", spec) == ""


def test_no_audience_injects_nothing():
    assert presentation_from_pack_audience(None) is None
    assert readership_instructions_for_phase("phase3", None) == ""


# ── end to end through doc_prompt ─────────────────────────────────────────


def test_pack_audience_reaches_the_phase3_prompt(facts):
    contract = _compile(AUDIENCE_PACK_YAML)
    prompt = doc_prompt(facts, "INJECTION_FINDINGS", contract=contract)
    assert "PRESENTATION PROFILE" in prompt
    assert "concise" in prompt.lower()
    assert "security reviewer" in prompt


def test_no_contract_reproduces_todays_prompt(facts):
    """Regression guard: the default (no-pack) contract injects no profile."""
    plain = doc_prompt(facts, "00_AGENT_BRIEF")
    defaulted = doc_prompt(facts, "00_AGENT_BRIEF", contract=default_contract("talend"))
    assert plain == defaulted
    assert "PRESENTATION PROFILE" not in plain


def test_request_style_layer_wins_over_the_pack_audience(facts):
    """Layer ordering: foundation -> functional -> style. Style wins."""
    contract = _compile(AUDIENCE_PACK_YAML)
    style = NexusPresentation(
        audience=NexusStyleAudience.EXECUTIVE,
        tone=NexusStyleTone.EXPLANATORY,
    )
    prompt = doc_prompt(
        facts,
        "INJECTION_FINDINGS",
        nexus_presentation=style,
        contract=contract,
    )
    # The Style layer's tone is rendered; the pack's ``concise`` is not.
    assert "Explain technical relationships clearly" in prompt
    assert "Use concise sentences" not in prompt
    # The pack's declared readership is additive and survives either way.
    assert "security reviewer" in prompt


def test_unit_reference_branch_also_gets_the_audience(facts):
    """The per-unit dossier branch is a second, independent injection site."""
    contract = _compile(AUDIENCE_PACK_YAML)
    prompt = doc_prompt(
        facts,
        "INJECTION_FINDINGS",
        scoped_unit="Job_A",
        unit_reference=True,
        contract=contract,
    )
    assert "PRESENTATION PROFILE" in prompt
    assert "security reviewer" in prompt
