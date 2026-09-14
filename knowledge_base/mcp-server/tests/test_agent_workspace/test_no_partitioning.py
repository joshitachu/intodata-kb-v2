"""Slice 3: there is no partitioning in this lane, at runtime or on paper.

Partitioning is a document-generation scaling mechanism for a per-run
model-call budget. ``agent-workspace-v1`` has no such budget: one run covers
the whole estate, a composite ZIP is ONE workspace with the sub-projects as
siblings under ``scan/``, and how to work through it is the agent's decision.

The operator console still advertised the opposite -- ACME showed "3 of 3
sub-projects will regenerate -- part1 50 units, part2 52, part3 40" with the
note "split into several runs so each fits the per-run model-call budget" --
because ``GenerationPlanSection`` renders whenever ``GET
/projects/{id}/generation-plan`` returns a plan, regardless of contract. The
dashboard half is a separate slice; this module pins the server half.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace.runner import AgentWorkspaceProvider
from tests.test_agent_workspace.workspace_fixture import (
    RecordingAgent,
    workspace_builder,
    writes,
)

_AGENT_WORKSPACE_PROFILE = {
    "contract": "agent-workspace-v1",
    "agent_workspace": {"agent_provider": "fake"},
}

#: Symbols this lane must never reach for. All three exist only to make a
#: fixed number of documents fit inside a fixed number of model calls.
_FORBIDDEN_SYMBOLS = (
    "docgen_max_run_calls",
    "estimate_partition_calls",
    "split_groups",
    "plan_partitions",
    "partition_orchestrator",
    "partition_planner",
    "build_generation_plan",
)

# The independent lane owns its investigation method. Reusing either legacy
# phase prompt would silently bring back the compact/exhaustive mismatch that
# this contract exists to remove.
_FORBIDDEN_LEGACY_PROMPT_COUPLING = (
    "phase2_platform_sections",
    "phase37_flow_extraction",
    "phase_orchestrator",
)


def _boom(name: str):
    def explode(*_args, **_kwargs):
        raise AssertionError(f"{name} must not be reached by agent-workspace-v1")

    return explode


async def _run_background(client, session_factory, tmp_path, monkeypatch, *, generation_profile):
    """Drive ``_run_generate_background`` far enough to pass the bypass."""

    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "ACME_ZIP_TEST"
    output_dir = config.project_root / "output" / project_id
    staging = output_dir / "docs_staging"
    staging.mkdir(parents=True)
    (staging / "00_OVERVIEW.md").write_text("# Overview\n\nOne estate.\n", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(project_id, str(tmp_path / "repo"), source_type="git")
        run = await registry.create_generate_run(project_id, triggered_by="manual")
        await session.commit()
        run_id = run.id

    calls: list[dict] = []

    async def fake_generate_docs(_self, project, **kwargs):
        calls.append(kwargs)
        return {"success": True, "model_name": "fake", "write_model_name": "fake"}

    monkeypatch.setattr(projects_mod.DocGenerationCoordinator, "generate_docs", fake_generate_docs)
    monkeypatch.setattr(projects_mod, "swap_docs", lambda *_a, **_k: None)

    async def _noop_push(*_args, **_kwargs):
        return {}, []

    monkeypatch.setattr(projects_mod, "_push_with_retry", _noop_push)

    from mcp_server.api.app import app as _app

    projects_mod._currently_generating.add(project_id)
    await projects_mod._run_generate_background(
        project_id,
        run_id,
        _app.state.log_manager,
        resume=True,
        generation_profile=generation_profile,
    )
    return calls


async def test_the_partition_orchestrator_is_never_entered(
    client, session_factory, tmp_path, monkeypatch
):
    from mcp_server.services import partition_orchestrator

    monkeypatch.setattr(
        partition_orchestrator,
        "plan_composite_generation",
        _boom("plan_composite_generation"),
    )
    monkeypatch.setattr(
        partition_orchestrator,
        "run_partitioned_generation",
        _boom("run_partitioned_generation"),
    )
    monkeypatch.setattr(
        partition_orchestrator, "build_generation_plan", _boom("build_generation_plan")
    )

    calls = await _run_background(
        client,
        session_factory,
        tmp_path,
        monkeypatch,
        generation_profile=_AGENT_WORKSPACE_PROFILE,
    )

    # One single-run generation, not one per sub-project.
    assert len(calls) == 1
    assert calls[0]["generation_profile"] == _AGENT_WORKSPACE_PROFILE


async def test_the_bypass_is_specific_to_this_contract(
    client, session_factory, tmp_path, monkeypatch
):
    """Control case: a legacy run still consults the planner.

    Without this, the assertion above would pass for a bypass that had been
    applied to every contract.
    """

    from mcp_server.services import partition_orchestrator

    seen: list[str] = []
    monkeypatch.setattr(
        partition_orchestrator,
        "plan_composite_generation",
        lambda project_id: seen.append(project_id),
    )

    await _run_background(client, session_factory, tmp_path, monkeypatch, generation_profile=None)

    assert seen == ["ACME_ZIP_TEST"]


async def test_a_composite_scan_stages_exactly_one_workspace(agent_ctx, log_lines):
    """One run over the whole estate: one workspace, at the project root.

    A partitioned run would stage ``output/{pid}/{subproject}/docs_staging``
    once per sub-project and produce one ``composite:{name}`` child run each.
    """

    staged: list[Path] = []
    agent = RecordingAgent([writes("00_OVERVIEW.md")])
    provider = AgentWorkspaceProvider(
        build_workspace=workspace_builder(staged.append),
        build_registry=lambda *, model: _single(agent),
    )

    result = await provider.run(agent_ctx, log_lines)

    assert result.success, result.error
    assert staged == [agent_ctx.staging_dir]
    assert agent_ctx.staging_dir.name == "docs_staging"
    assert agent_ctx.staging_dir.parent.name == agent_ctx.project_id
    assert len(agent.requests) == 1
    assert agent.requests[0].workspace_path == agent_ctx.staging_dir


async def test_no_partition_budget_symbol_is_read_at_runtime(agent_ctx, log_lines, monkeypatch):
    from mcp_server.providers.custom import partition_planner

    monkeypatch.setattr(
        partition_planner, "estimate_partition_calls", _boom("estimate_partition_calls")
    )
    monkeypatch.setattr(partition_planner, "plan_partitions", _boom("plan_partitions"))
    monkeypatch.setattr(
        partition_planner, "_split_oversized_partitions", _boom("_split_oversized_partitions")
    )

    agent = RecordingAgent([writes("00_OVERVIEW.md")])
    provider = AgentWorkspaceProvider(
        build_workspace=workspace_builder(),
        build_registry=lambda *, model: _single(agent),
    )
    result = await provider.run(agent_ctx, log_lines)
    assert result.success, result.error


@pytest.mark.parametrize(
    "module",
    sorted(
        path.name
        for path in (
            Path(__file__).resolve().parents[2]
            / "src"
            / "mcp_server"
            / "providers"
            / "agent_workspace"
        ).glob("*.py")
    ),
)
def test_the_lane_names_no_partitioning_or_call_budget_symbol(module):
    """A static check, because "is not read" is not observable at runtime.

    ``docgen_max_run_calls`` is a plain settings attribute and
    ``split_groups`` a dataclass field: neither can be instrumented without
    replacing the object they live on. Reading the source is the honest
    assertion.
    """

    source_root = (
        Path(__file__).resolve().parents[2] / "src" / "mcp_server" / "providers" / "agent_workspace"
    )
    source = (source_root / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
    offending = sorted(names & set(_FORBIDDEN_SYMBOLS))
    assert not offending, f"{module} reaches for {offending}"


@pytest.mark.parametrize(
    "module",
    sorted(
        path.name
        for path in (
            Path(__file__).resolve().parents[2]
            / "src"
            / "mcp_server"
            / "providers"
            / "agent_workspace"
        ).glob("*.py")
    ),
)
def test_the_lane_does_not_load_legacy_phase_prompts(module):
    source_root = (
        Path(__file__).resolve().parents[2] / "src" / "mcp_server" / "providers" / "agent_workspace"
    )
    source = (source_root / module).read_text(encoding="utf-8")
    offending = sorted(
        token for token in _FORBIDDEN_LEGACY_PROMPT_COUPLING if token in source
    )
    assert not offending, f"{module} is coupled to legacy prompts: {offending}"


#: Legacy partitioning vocabulary, in its PRESCRIPTIVE forms only. Absent from
#: the code (above) is half the property; the other half is that the prose
#: never reintroduces it, because an instruction to "work through your assigned
#: units, one chunk at a time" is a partitioning scheme whether or not any
#: Python computes it.
#:
#: Bare "partition" and "assigned unit" are deliberately NOT here: the goal and
#: the guide say "there is no partitioning, no assigned unit list", and a
#: substring check that cannot tell a denial from an instruction would delete
#: the sentence that states the property.
_FORBIDDEN_PROSE = (
    "your assigned",
    "assigned to you",
    "for each assigned",
    "your chunk",
    "this chunk",
    "model-call budget",
    "worker per",
    "each partition",
    "per sub-project",
)


def test_the_staged_instructions_describe_no_partitioning(workspace):
    """The "on paper" half, over every file the agent is given."""

    offending: list[str] = []
    for path in sorted(workspace.rglob("*.md")):
        if path.is_relative_to(workspace / "output") or path.is_relative_to(workspace / "notes"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        offending.extend(
            f"{path.relative_to(workspace)} says {phrase!r}"
            for phrase in _FORBIDDEN_PROSE
            if phrase in text
        )
    assert not offending, offending


def _single(agent):
    from mcp_server.providers.coding_agent.registry import CodingAgentRegistry

    registry: CodingAgentRegistry = CodingAgentRegistry()
    registry.register("fake", lambda: agent)
    return registry
