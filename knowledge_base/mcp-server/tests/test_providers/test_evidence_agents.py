from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.custom.evidence_agents import (
    EvidenceAgentRunResult,
    EvidenceAgentRunner,
    EvidenceRegistryBuilder,
    _source_is_known,
    resolve_evidence_agents,
)
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.services.plugin_schema import PluginPackManifest


class _FakeProvider:
    model_name = "fake/evidence"
    supports_tools = False

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []
        self.context_files: list[list[Path]] = []

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: ModelOptions | None = None,
    ) -> ModelResult:
        self.prompts.append(prompt)
        self.context_files.append(context_files or [])
        return ModelResult(
            success=True,
            text=self.text,
            model_name=self.model_name,
            input_tokens=100,
            output_tokens=20,
        )


def _manifest(**kwargs) -> PluginPackManifest:
    data = {
        "name": "pack",
        "version": "1.0.0",
        "steps": [{"id": "full", "type": "harness"}],
    }
    data.update(kwargs)
    return PluginPackManifest.model_validate(data)


def test_resolves_default_agents_without_pack() -> None:
    resolution = resolve_evidence_agents(None, None)

    assert resolution.errors == []
    assert {agent.id for agent in resolution.agents} >= {
        "core_architecture_dependency",
        "core_data_flow",
        "core_runtime_config",
        "core_risk_security",
    }
    assert all(agent.prompt.strip() for agent in resolution.agents)
    assert "data-flow evidence agent" in [
        agent.prompt for agent in resolution.agents if agent.id == "core_data_flow"
    ][0]


def test_resolves_pack_builtin_and_prompt_agents(tmp_path: Path) -> None:
    prompt = tmp_path / "prompts" / "agents" / "ddl_alignment.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Find DDL alignment evidence.", encoding="utf-8")
    manifest = _manifest(
        evidence_profiles={"table_inventory": {"include": ["sql_queries", "ddl_schema"]}},
        evidence_agents=[
            {
                "id": "sql",
                "uses": "builtin/sql_usage_agent",
                "evidence_profile": "table_inventory",
                "categories": ["sql_queries"],
            },
            {
                "id": "ddl",
                "prompt_file": "prompts/agents/ddl_alignment.md",
                "evidence_profile": "table_inventory",
                "categories": ["ddl_schema"],
            },
        ],
    )

    resolution = resolve_evidence_agents(manifest, tmp_path)

    assert resolution.errors == []
    assert [agent.id for agent in resolution.agents] == ["sql", "ddl"]
    assert resolution.agents[1].prompt_path == prompt
    assert resolution.agents[0].prompt.strip()
    assert resolution.agents[1].prompt == "Find DDL alignment evidence."


def test_missing_pack_prompt_reports_error(tmp_path: Path) -> None:
    manifest = _manifest(
        evidence_agents=[
            {
                "id": "ddl",
                "prompt_file": "prompts/agents/missing.md",
                "categories": ["ddl_schema"],
            }
        ],
    )

    resolution = resolve_evidence_agents(manifest, tmp_path)

    assert resolution.agents == []
    assert "prompt_file not found" in resolution.errors[0]


def test_unknown_builtin_reports_error(tmp_path: Path) -> None:
    manifest = _manifest(
        evidence_agents=[
            {
                "id": "unknown",
                "uses": "builtin/not_real",
                "categories": ["sql_queries"],
            }
        ],
    )

    resolution = resolve_evidence_agents(manifest, tmp_path)

    assert resolution.agents == []
    assert "unknown builtin" in resolution.errors[0]


def test_registry_builder_writes_claims(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "facts_manifest.json").write_text('{"project": "demo"}', encoding="utf-8")
    (preprocess / "Webservices.md").write_text(
        "# Webservices\n\n```sql\nselect id, name from Customer where active = 1\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)
    data_flow_agent = [agent for agent in resolution.agents if agent.id == "core_data_flow"]

    paths = EvidenceRegistryBuilder(staging).build(data_flow_agent)

    registry = json.loads((staging / paths["json"]).read_text(encoding="utf-8"))
    assert registry["claim_count"] >= 1
    assert registry["agents"][0]["prompt"].strip()
    assert registry["claims"][0]["category"] == "sql_queries"
    assert (staging / paths["md"]).exists()
    assert (staging / paths["survival_report"]).exists()


@pytest.mark.asyncio
async def test_runner_calls_model_and_validates_claims(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    source = preprocess / "Webservices.md"
    source.write_text(
        "# Webservices\n\n```sql\nselect id, name from Customer where active = 1\n```\n",
        encoding="utf-8",
    )
    agent = [agent for agent in resolve_evidence_agents(None, None).agents if agent.id == "core_data_flow"][0]
    provider = _FakeProvider(
        json.dumps(
            {
                "claims": [
                    {
                        "category": "sql_queries",
                        "claim": "Customer is read for active customers.",
                        "source": "_preprocess/Webservices.md",
                        "snippet": "select id, name from Customer where active = 1",
                        "confidence": 0.91,
                    },
                    {
                        "category": "auth",
                        "claim": "Wrong category should be rejected.",
                        "source": "_preprocess/Webservices.md",
                        "snippet": "Authorization",
                        "confidence": 0.5,
                    },
                    {
                        "category": "sql_queries",
                        "claim": "Unknown source should be rejected.",
                        "source": "_preprocess/Missing.md",
                        "snippet": "select 1",
                        "confidence": 0.5,
                    },
                ]
            }
        )
    )

    run_result = await EvidenceAgentRunner(staging).run_agent(agent, provider)

    assert run_result.agent_id == "core_data_flow"
    assert len(run_result.claims) == 1
    assert run_result.claims[0].claim == "Customer is read for active customers."
    assert len(run_result.errors) == 2
    assert "Allowed categories" in provider.prompts[0]
    assert source in provider.context_files[0]


def test_registry_builder_merges_llm_claims(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "# Webservices\n\n```sql\nselect id from Customer\n```\n",
        encoding="utf-8",
    )
    agent = [agent for agent in resolve_evidence_agents(None, None).agents if agent.id == "core_data_flow"][0]
    run_result = EvidenceAgentRunResult(
        agent_id=agent.id,
        result=ModelResult(success=True, text='{"claims":[]}', model_name="fake/evidence"),
        claims=[
            {
                "category": "sql_queries",
                "claim": "Customer is read.",
                "source": "_preprocess/Webservices.md",
                "snippet": "select id from Customer",
                "confidence": 0.9,
            }
        ],
    )

    paths = EvidenceRegistryBuilder(staging).build([agent], agent_results=[run_result])

    registry = json.loads((staging / paths["json"]).read_text(encoding="utf-8"))
    assert registry["agent_runs"][0]["claim_count"] == 1
    assert any(claim["found_by"] == "llm_agent" for claim in registry["claims"])


# --- Session 1: path validator, three-tier output, agent prompt stripping ---


def test_source_validator_accepts_original_source_extensions(tmp_path: Path) -> None:
    known: set[str] = {"_preprocess/Webservices-2_chunk_1.md", "Webservices-2_chunk_1.md"}
    assert _source_is_known("Webservices.ServiceInterface/AutorisationChangesService.cs", known)
    assert _source_is_known("src/DataService.java", known)
    assert _source_is_known("transforms/Lookup.xsl", known)


def test_source_validator_rejects_unknown_staging_paths(tmp_path: Path) -> None:
    known: set[str] = {"_preprocess/Webservices-2_chunk_1.md", "Webservices-2_chunk_1.md"}
    assert not _source_is_known("_preprocess/NonExistent.md", known)
    assert not _source_is_known("made_up_file.txt", known)


def test_source_validator_accepts_basename_match(tmp_path: Path) -> None:
    known: set[str] = {
        "_preprocess/Webservices-2_chunk_1.md",
        "Webservices-2_chunk_1.md",
        "CustomFile.md",
    }
    assert _source_is_known("CustomFile.md", known)
    assert _source_is_known("some/path/CustomFile.md", known)


@pytest.mark.asyncio
async def test_runner_accepts_class_hierarchy_source(tmp_path: Path) -> None:
    """Claims citing class-hierarchy paths (e.g. Namespace/Class.cs) must be accepted."""
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    source = preprocess / "Webservices-2_chunk_1.md"
    source.write_text(
        "# Webservices chunk\n\nAutorisationChangesService handles auth.\n",
        encoding="utf-8",
    )
    agent = [a for a in resolve_evidence_agents(None, None).agents if a.id == "core_data_flow"][0]
    provider = _FakeProvider(
        json.dumps(
            {
                "claims": [
                    {
                        "category": "sql_queries",
                        "claim": "AutorisationChangesService reads data.",
                        "source": "Webservices.ServiceInterface/AutorisationChangesService.cs",
                        "snippet": "select * from Changes",
                        "confidence": 0.85,
                    }
                ]
            }
        )
    )

    run_result = await EvidenceAgentRunner(staging).run_agent(agent, provider)

    assert len(run_result.claims) == 1, f"expected 1 claim, got errors: {run_result.errors}"
    assert run_result.claims[0].source == "Webservices.ServiceInterface/AutorisationChangesService.cs"


def test_registry_builder_writes_evidence_brief(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "facts_manifest.json").write_text('{"project": "demo"}', encoding="utf-8")
    (preprocess / "Webservices.md").write_text(
        "# Webservices\n\n```sql\nselect id, name from Customer where active = 1\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)

    paths = EvidenceRegistryBuilder(staging).build(resolution.agents)

    assert "brief" in paths
    brief_path = staging / paths["brief"]
    assert brief_path.exists()
    brief_text = brief_path.read_text(encoding="utf-8")
    assert brief_text.startswith("# Evidence Brief")
    assert brief_path.stat().st_size < 50 * 1024, "evidence_brief.md exceeds 50 KB"


def test_evidence_brief_no_agent_prompts(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "```sql\nselect 1\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)

    paths = EvidenceRegistryBuilder(staging).build(resolution.agents)

    for output_file in [paths["md"], paths["brief"]]:
        text = (staging / output_file).read_text(encoding="utf-8")
        assert "## Agent Prompts" not in text, f"{output_file} contains agent prompt section"
        assert "You are the core" not in text, f"{output_file} contains agent prompt body"


def test_build_phase2_slice_filters_to_allowed_units(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "# Webservices\n\n```sql\nselect id from Customer\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)
    builder = EvidenceRegistryBuilder(staging)
    paths = builder.build(resolution.agents)

    # All deterministic claims come from "Webservices.md"; unit name "Customer" appears in it.
    slice_path = builder.build_phase2_slice("Webservices-2_chunk_1", ["Customer"])

    assert slice_path is not None
    assert slice_path.exists()
    assert slice_path.parent.name == "phase2_slices"
    assert slice_path.name == "Webservices-2_chunk_1.md"
    text = slice_path.read_text(encoding="utf-8")
    assert "# Evidence Brief" in text


def test_build_phase2_slice_returns_none_when_no_match(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "```sql\nselect id from Customer\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)
    builder = EvidenceRegistryBuilder(staging)
    builder.build(resolution.agents)

    slice_path = builder.build_phase2_slice("OtherGroup_chunk_1", ["NoMatchingUnit"])

    assert slice_path is None


# --- Session 3: per-doc slices ---


def test_build_writes_doc_slices_dir(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "facts_manifest.json").write_text('{"project": "demo"}', encoding="utf-8")
    (preprocess / "Webservices.md").write_text(
        "# Webservices\n\n```sql\nselect id from Customer\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)

    paths = EvidenceRegistryBuilder(staging).build(resolution.agents)

    assert "doc_slices_dir" in paths
    slices_dir = staging / paths["doc_slices_dir"]
    assert slices_dir.is_dir()
    # sql_queries claims (deterministic, from Webservices.md) should produce a
    # slice for 03_DATA_FLOWS_AND_LINEAGE.
    assert (slices_dir / "03_DATA_FLOWS_AND_LINEAGE.md").exists()


def test_doc_slice_contains_only_relevant_category_claims(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "```sql\nselect id from Customer\n```\n",
        encoding="utf-8",
    )
    resolution = resolve_evidence_agents(None, None)

    paths = EvidenceRegistryBuilder(staging).build(resolution.agents)

    slices_dir = staging / paths["doc_slices_dir"]
    # sql_queries claims belong to 03_DATA_FLOWS_AND_LINEAGE but NOT 01_ARCHITECTURE_MAP.
    # Check for the category section heading (## sql_queries), not just the word, since
    # the agents header always lists all agent categories regardless of which claims appear.
    data_flow_text = (slices_dir / "03_DATA_FLOWS_AND_LINEAGE.md").read_text(encoding="utf-8")
    assert "## sql_queries" in data_flow_text
    arch_slice = slices_dir / "01_ARCHITECTURE_MAP.md"
    if arch_slice.exists():
        arch_text = arch_slice.read_text(encoding="utf-8")
        assert "## sql_queries" not in arch_text
