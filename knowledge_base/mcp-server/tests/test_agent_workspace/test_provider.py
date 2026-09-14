"""Slice 2: the provider contract, the adapters, and restart-not-resume.

The two verified failures these pin:

* ``opencode_cli.py`` denied ``edit`` outright whenever an artifact facade was
  attached, and the goal-driven path always attached one -- so the agent could
  not physically write a document and spent its budget on bookkeeping instead.
* A repair attempt died with ``Error: Session not found`` (returncode 1) after
  a valid 22,723-byte artifact had already been written, and that transport
  fault failed the whole run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.contract import AgentRunResult
from mcp_server.providers.agent_workspace.runner import AgentWorkspaceProvider
from tests.test_agent_workspace.workspace_fixture import (
    RecordingAgent,
    account_for_everything,
    workspace_builder,
    writes,
)


def _provider(agent, **kwargs):
    return AgentWorkspaceProvider(
        build_workspace=workspace_builder(),
        build_registry=lambda *, model: _registry(agent),
        **kwargs,
    )


def _registry(agent):
    from mcp_server.providers.coding_agent.registry import CodingAgentRegistry

    registry: CodingAgentRegistry = CodingAgentRegistry()
    registry.register("fake", lambda: agent)
    return registry


# -- the seam ------------------------------------------------------------


def test_a_fake_adapter_is_registrable_without_monkeypatching(registered_fake):
    """The whole point of the factory seam.

    Before this, substituting an adapter meant
    ``monkeypatch.setattr(factory, "OpenCodeCliProvider", Fake)`` -- which
    replaces a transport class the registry happens to name and cannot
    register a DIFFERENT provider id at all.
    """

    from mcp_server.providers.coding_agent.factory import build_agent_provider_registry

    agent = registered_fake(RecordingAgent([writes("x.md")]))
    registry = build_agent_provider_registry(model="fake/model")

    assert "fake" in registry.provider_ids()
    assert registry.create("fake") is agent
    # The built-in adapter is untouched by the registration.
    assert registry.create("opencode").provider_id == "opencode"


def test_an_unregistered_provider_fails_closed():
    from mcp_server.providers.coding_agent.base import UnsupportedCodingAgentError
    from mcp_server.providers.coding_agent.factory import build_agent_provider_registry

    registry = build_agent_provider_registry(model="fake/model")
    with pytest.raises(UnsupportedCodingAgentError):
        registry.create("claude-code")


def test_importing_the_models_module_does_not_drag_in_the_transport():
    """``coding_agent.models`` is three dataclasses; it must stay cheap.

    ``agent_workspace.contract`` imports exactly that module, so an eager
    ``factory`` re-export put ``opencode_cli`` and the settings object behind
    every consumer of the new lane's seam.
    """

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import mcp_server.providers.coding_agent.models as m; "
                "print('mcp_server.providers.opencode_cli' in sys.modules)"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stdout


# -- end to end ----------------------------------------------------------


async def test_a_fake_adapter_drives_an_end_to_end_run(agent_ctx, log_lines):
    agent = RecordingAgent(
        [writes("00_OVERVIEW.md", "integrations/orders.md", "integrations/invoices.md")]
    )
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success, result.error
    staging = agent_ctx.staging_dir
    assert (staging / "00_OVERVIEW.md").is_file()
    assert (staging / "integrations" / "orders.md").is_file()
    assert (staging / "integrations" / "invoices.md").is_file()
    assert sorted(result.required_docs) == [
        "00_OVERVIEW",
        "integrations/invoices",
        "integrations/orders",
    ]
    # The evidence workspace never reaches docs/.
    for leftover in (
        contract.AGENT_GUIDE,
        contract.GOAL,
        contract.SCAN,
        contract.INSTRUCTIONS,
        contract.OUTPUT,
        contract.NOTES,
    ):
        assert not (staging / leftover).exists(), leftover


async def test_compiler_owned_reference_is_rebuilt_even_if_agent_deletes_it(
    agent_ctx, log_lines, monkeypatch
):
    base_builder = workspace_builder()

    def builder(**kwargs):
        receipt = base_builder(**kwargs)
        reference = Path(receipt.workspace_path) / "output" / "reference"
        reference.mkdir(parents=True)
        (reference / "README.md").write_text("# Reference\n", encoding="utf-8")
        return receipt.model_copy(update={"reference_registers": {"units": 1}})

    def removes_reference_then_writes(request) -> AgentRunResult:
        shutil.rmtree(Path(request.output_path) / "reference")
        (Path(request.output_path) / "overview.md").write_text(
            "# Overview\n\nDeep narrative.\n", encoding="utf-8"
        )
        account_for_everything(Path(request.workspace_path))
        return AgentRunResult(status="completed", provider_id="fake", model="fake-model")

    rebuilt: list[Path] = []

    def compile_reference(workspace: Path):
        rebuilt.append(Path(workspace))
        target = Path(workspace) / "output" / "reference"
        target.mkdir(parents=True)
        (target / "README.md").write_text("# Rebuilt reference\n", encoding="utf-8")
        return SimpleNamespace(unit_count=1, mapping_table_count=0, mapping_row_count=0)

    from mcp_server.providers.agent_workspace import registers

    monkeypatch.setattr(registers, "compile_reference_registers", compile_reference)
    agent = RecordingAgent([removes_reference_then_writes])
    provider = AgentWorkspaceProvider(
        build_workspace=builder,
        build_registry=lambda *, model: _registry(agent),
    )

    result = await provider.run(agent_ctx, log_lines)

    assert result.success, result.error
    assert rebuilt == [agent_ctx.staging_dir]
    assert (agent_ctx.staging_dir / "reference" / "README.md").read_text() == (
        "# Rebuilt reference\n"
    )


async def test_the_request_carries_no_domain_content(agent_ctx, log_lines):
    """No fact graph, no scope seed, no output schema, no prompt splicing."""

    agent = RecordingAgent([writes("00_OVERVIEW.md")])
    await _provider(agent).run(agent_ctx, log_lines)

    request = agent.requests[0]
    fields = set(type(request).model_fields)
    assert not fields & {
        "fact_graph",
        "initial_scope_seed",
        "expected_outcome",
        "provider_options",
        "method_files",
        "goal",
    }
    assert request.workspace_path == agent_ctx.staging_dir
    assert request.guide_path.name == contract.AGENT_GUIDE.name
    assert request.goal_path.name == contract.GOAL.name
    assert request.checkpoint_summary is None
    assert request.allowed_tools == contract.DEFAULT_TOOLS


# -- restart, not resume -------------------------------------------------


def _session_lost(request) -> AgentRunResult:
    # A valid document was already written before the transport died -- the
    # verified shape of the run this lane replaces.
    path = Path(request.output_path) / "00_OVERVIEW.md"
    path.write_text("# Overview\n\nHalf the estate.\n", encoding="utf-8")
    (Path(request.scratch_path) / "progress.md").write_text(
        "Done: overview. Left: the integration dossiers.\n", encoding="utf-8"
    )
    return AgentRunResult(
        status="failed",
        provider_id="fake",
        model="fake-model",
        error="OpenCode agent_workspace_7_0 exited with code 1: Error: Session not found",
    )


async def test_a_lost_session_restarts_with_a_checkpoint_instead_of_failing(agent_ctx, log_lines):
    agent = RecordingAgent([_session_lost, writes("integrations/orders.md")])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success, result.error
    assert len(agent.requests) == 2
    first, second = agent.requests
    assert first.checkpoint_summary is None
    assert second.checkpoint_summary is not None
    # Composed from what is actually on disk, and it preserves the agent's own
    # notes rather than overwriting them.
    assert "00_OVERVIEW.md" in second.checkpoint_summary
    assert "the integration dossiers" in second.checkpoint_summary
    assert "Session not found" in second.checkpoint_summary
    # A fresh session: nothing in the request carries a provider session id.
    assert "session_id" not in type(second).model_fields
    # Both documents ship.
    assert (agent_ctx.staging_dir / "00_OVERVIEW.md").is_file()
    assert (agent_ctx.staging_dir / "integrations" / "orders.md").is_file()


async def test_restarts_are_bounded_by_max_restarts(agent_ctx, log_lines):
    """Exhausted restarts are a FAILED run, and the draft is kept for a resume.

    This used to assert that what the doomed attempts wrote still published and
    that ``result.success`` was true. That was the defect: the runner returned
    ``success=True`` whenever publication accepted anything, so a provider that
    never reported completion produced a "completed" run with a partial
    deliverable in ``docs/``. Run 250 is the verified case. Salvage still
    happens -- the draft stays in ``docs_staging/`` and a resume continues from
    it -- but it does not replace published documentation.
    """

    agent = RecordingAgent([_session_lost])
    agent_ctx.generation_profile["agent_workspace"]["resources"] = {"max_restarts": 1}

    result = await _provider(agent).run(agent_ctx, log_lines)

    assert len(agent.requests) == 2  # the first attempt plus one restart
    assert not result.success
    assert result.has_warnings
    assert "failed" in (result.error or "")
    # No swap: the draft is untouched in the workspace, not copied to the root.
    assert not (agent_ctx.staging_dir / "00_OVERVIEW.md").exists()
    assert (agent_ctx.staging_dir / contract.OUTPUT / "00_OVERVIEW.md").is_file()
    assert (agent_ctx.staging_dir / contract.SCAN).is_dir()


async def test_a_hard_provider_failure_is_not_retried(agent_ctx, log_lines):
    def preflight_failure(request) -> AgentRunResult:
        return AgentRunResult(
            status="failed",
            provider_id="fake",
            model="fake-model",
            error="OpenCode CLI not found: opencode",
        )

    agent = RecordingAgent([preflight_failure])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert len(agent.requests) == 1
    assert not result.success
    assert "CLI not found" in (result.error or "")


async def test_a_ceiling_trip_keeps_the_draft_and_reports_incomplete(agent_ctx, log_lines):
    """An exhausted ceiling is an INCOMPLETE run, not a successful one.

    The previous assertion was ``result.success`` with the partial document
    copied to the docs root. Preserving a compatible draft for resumption is
    right; replacing published documentation with it is not, and calling it a
    success is what made a thin run invisible.
    """

    def out_of_budget(request) -> AgentRunResult:
        Path(request.output_path).joinpath("00_OVERVIEW.md").write_text(
            "# Overview\n\nPartial.\n", encoding="utf-8"
        )
        from mcp_server.providers.coding_agent.models import UsageReceipt

        return AgentRunResult(
            status="incomplete",
            provider_id="fake",
            model="fake-model",
            usage=UsageReceipt(uncached_input_tokens=10_000_000),
            stop_reason="resource_ceiling",
        )

    agent = RecordingAgent([out_of_budget])
    agent_ctx.generation_profile["agent_workspace"]["resources"] = {
        "processed_token_ceiling": 1000,
        "max_restarts": 2,
    }
    result = await _provider(agent).run(agent_ctx, log_lines)

    # The ceiling is spent, so no restart is attempted even though the status
    # is retryable and restarts remain.
    assert len(agent.requests) == 1
    assert not result.success
    assert "incomplete" in (result.error or "")
    assert not result.cancelled
    assert not (agent_ctx.staging_dir / "00_OVERVIEW.md").exists()
    # The draft is preserved exactly where a resume expects it.
    assert (agent_ctx.staging_dir / contract.OUTPUT / "00_OVERVIEW.md").is_file()


# -- honest status -------------------------------------------------------


def _reference_only(request) -> AgentRunResult:
    """A provider that FAILS while ``output/reference/`` is already populated.

    Run 250's exact shape. The compiler writes the registers before the agent
    starts, so ``output/`` is never empty and every proxy for "the agent did
    something" -- a non-empty directory, an accepted-document floor, the
    provider's own status -- was satisfied by files the agent never touched.
    """

    reference = Path(request.output_path) / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "units.md", "mappings.md"):
        (reference / name).write_text(f"# {name}\n\nCompiler output.\n", encoding="utf-8")
    return AgentRunResult(
        status="failed",
        provider_id="fake",
        model="fake-model",
        error="the provider gave up",
    )


async def test_a_failed_provider_with_reference_files_present_returns_failed(
    agent_ctx, log_lines
):
    agent = RecordingAgent([_reference_only])
    agent_ctx.generation_profile["agent_workspace"]["resources"] = {"max_restarts": 0}

    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.required_docs == []
    assert "failed" in (result.error or "")
    # Nothing was swapped in: the workspace is exactly as the agent left it.
    assert (agent_ctx.staging_dir / contract.OUTPUT / "reference" / "units.md").is_file()
    assert not (agent_ctx.staging_dir / "reference").exists()


async def test_compiler_output_alone_cannot_satisfy_completion(agent_ctx, log_lines):
    """Plan decision 4, with the provider reporting success.

    The registers are rebuilt from the scan on every run, so they always
    "change" on disk. Attribution is therefore against the pre-run snapshot AND
    excludes the compiler-owned root -- either test alone passes this run.
    """

    def completes_without_writing(request) -> AgentRunResult:
        reference = Path(request.output_path) / "reference"
        reference.mkdir(parents=True, exist_ok=True)
        (reference / "units.md").write_text("# units\n", encoding="utf-8")
        # Fully accountable, so the refusal below can only be the attribution
        # check. Without this the test would pass on the missing receipt and
        # stop testing the thing it is named after.
        account_for_everything(Path(request.workspace_path))
        return AgentRunResult(status="completed", provider_id="fake", model="fake-model")

    agent = RecordingAgent([completes_without_writing])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert "authored no client document" in (result.error or "")
    assert not (agent_ctx.staging_dir / "reference").exists()


async def test_a_document_identical_to_the_staged_one_is_not_authored(agent_ctx, log_lines):
    """"The file exists" is not "the agent wrote it"."""

    seeded = "# Seeded\n\nStaged before the agent ran.\n"

    def builder(**kwargs):
        receipt = workspace_builder()(**kwargs)
        (Path(receipt.workspace_path) / "output" / "seeded.md").write_text(
            seeded, encoding="utf-8"
        )
        return receipt

    def touches_nothing(request) -> AgentRunResult:
        # Accountable but productive of nothing new: the refusal has to come
        # from attribution, not from the completion handshake.
        account_for_everything(Path(request.workspace_path))
        return AgentRunResult(status="completed", provider_id="fake", model="fake-model")

    agent = RecordingAgent([touches_nothing])
    provider = AgentWorkspaceProvider(
        build_workspace=builder,
        build_registry=lambda *, model: _registry(agent),
    )
    result = await provider.run(agent_ctx, log_lines)

    assert result.success is False
    assert "authored no client document" in (result.error or "")


async def test_a_cancelled_run_is_reported_as_cancelled_and_never_swaps(agent_ctx, log_lines):
    def cancelled(request) -> AgentRunResult:
        Path(request.output_path).joinpath("00_OVERVIEW.md").write_text(
            "# Overview\n\nPartial.\n", encoding="utf-8"
        )
        return AgentRunResult(
            status="cancelled",
            provider_id="fake",
            model="fake-model",
            stop_reason="cancelled",
        )

    agent = RecordingAgent([cancelled])
    agent_ctx.generation_profile["agent_workspace"]["resources"] = {"max_restarts": 0}
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    # ``cancelled`` is the field ``_run_generate_background`` reads to record
    # the run as cancelled rather than failed; without it the two are one.
    assert result.cancelled is True
    assert not (agent_ctx.staging_dir / "00_OVERVIEW.md").exists()


async def test_the_four_outcomes_are_distinguishable_in_the_run_record(agent_ctx, log_lines):
    """completed / incomplete / cancelled / failed must not collapse to two."""

    outcomes = {}
    for status in ("completed", "incomplete", "cancelled", "failed"):
        ctx = agent_ctx
        staging = ctx.staging_dir.parent / f"docs_staging_{status}"
        staging.mkdir(parents=True, exist_ok=True)
        ctx = type(ctx)(**{**ctx.__dict__, "staging_dir": staging, "staging_abs": str(staging)})
        agent = RecordingAgent([writes("00_OVERVIEW.md", status=status)])
        result = await _provider(agent).run(ctx, log_lines)
        outcomes[status] = (result.success, result.cancelled, result.error, result.outcome)

    assert outcomes["completed"][0] is True
    assert outcomes["incomplete"][:2] == (False, False)
    assert "incomplete" in outcomes["incomplete"][2]
    assert outcomes["cancelled"][1] is True
    assert outcomes["failed"][:2] == (False, False)
    assert "failed" in outcomes["failed"][2]

    # The four are distinguishable in the RUN RECORD, not only in the message.
    # ``success``/``cancelled`` can express three states; ``outcome`` rides on
    # ``run_verdict`` (already free-form) so ``status`` keeps its existing
    # vocabulary and historical rows stay comparable.
    assert {status: value[3] for status, value in outcomes.items()} == {
        "completed": "completed",
        "incomplete": "incomplete",
        "cancelled": "cancelled",
        "failed": "failed",
    }
    assert len({value[3] for value in outcomes.values()}) == 4


# -- usage accounting ----------------------------------------------------


async def test_every_charged_token_dimension_reaches_the_run_record(agent_ctx, log_lines):
    """Cache writes are input and reasoning tokens are output. Both were dropped.

    ``_finish`` reported ``uncached_input_tokens + cache_read_tokens`` as input
    and bare ``output_tokens`` as output, so a run's recorded usage was not the
    run's bill and the cost ceiling was reading a number that did not exist.
    ``UsageReceipt`` has carried all five dimensions the whole time.
    """

    from mcp_server.providers.coding_agent.models import UsageReceipt

    def with_usage(request) -> AgentRunResult:
        Path(request.output_path).joinpath("00_OVERVIEW.md").write_text(
            "# Overview\n\nWritten.\n", encoding="utf-8"
        )
        account_for_everything(Path(request.workspace_path))
        return AgentRunResult(
            status="completed",
            provider_id="fake",
            model="fake-model",
            usage=UsageReceipt(
                uncached_input_tokens=100,
                cache_read_tokens=200,
                cache_write_tokens=400,
                output_tokens=50,
                reasoning_tokens=800,
                cost_usd=1.25,
            ),
        )

    agent = RecordingAgent([with_usage])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success, result.error
    assert result.input_tokens == 700
    assert result.output_tokens == 850
    assert result.cost_usd == 1.25


# -- error normalization -------------------------------------------------


async def test_every_staging_failure_names_its_stage_and_keeps_existing_docs(
    agent_ctx, log_lines
):
    """A navigation or instruction failure is not "Generation failed".

    ``runner`` caught ``WorkspaceUnavailableError`` only, so anything raised by
    navigation, instruction rendering or reference compilation escaped the
    single ``except`` and surfaced with nothing an operator could act on.
    """

    from mcp_server.providers.agent_workspace.contract import WorkspaceUnavailableError

    def builder(**kwargs):
        raise WorkspaceUnavailableError(
            "facts/navigation/units: shard could not be written", stage="navigation"
        )

    provider = AgentWorkspaceProvider(
        build_workspace=builder,
        build_registry=lambda *, model: _registry(RecordingAgent([writes("x.md")])),
    )
    result = await provider.run(agent_ctx, log_lines)

    assert not result.success
    assert "[navigation]" in (result.error or "")
    assert "facts/navigation/units" in (result.error or "")


async def test_an_unexpected_failure_carries_a_correlation_id_and_no_detail(
    agent_ctx, log_lines
):
    """A raw message may quote a file's contents, so it never reaches a client.

    The server log keeps the exception chain under the same correlation id;
    ``caplog`` is not asserted here because the message the OPERATOR sees is
    the property that matters.
    """

    def builder(**kwargs):
        raise ValueError("/etc/shadow line 3: root:$6$secret")

    provider = AgentWorkspaceProvider(
        build_workspace=builder,
        build_registry=lambda *, model: _registry(RecordingAgent([writes("x.md")])),
    )
    result = await provider.run(agent_ctx, log_lines)

    assert not result.success
    assert "correlation id" in (result.error or "")
    assert "ValueError" in (result.error or "")
    assert "shadow" not in (result.error or "")
    assert "secret" not in (result.error or "")


async def test_a_reference_compilation_failure_is_a_named_failed_result(agent_ctx, log_lines):
    from mcp_server.providers.agent_workspace import registers

    def builder(**kwargs):
        receipt = workspace_builder()(**kwargs)
        return receipt.model_copy(update={"reference_registers": {"units": 1}})

    def boom(workspace, **kwargs):
        raise registers.ReferenceRegisterError("required factual input is absent: units.json")

    agent = RecordingAgent([writes("00_OVERVIEW.md")])
    provider = AgentWorkspaceProvider(
        build_workspace=builder,
        build_registry=lambda *, model: _registry(agent),
    )
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(registers, "compile_reference_registers", boom)
        result = await provider.run(agent_ctx, log_lines)

    assert not result.success
    assert "units.json" in (result.error or "")
    # Existing documentation survives: no clean room was ever assembled.
    assert not (agent_ctx.staging_dir / "00_OVERVIEW.md").exists()


async def test_three_restarts_leave_the_agent_s_progress_file_alone(agent_ctx, log_lines):
    """The runner never writes to a file the agent owns (plan decision 6).

    ``notes/progress.md`` used to be BOTH halves: the runner read it, composed
    a restart summary that embedded it, and wrote that summary back over the
    same path. Three restarts produced a file containing three nested copies of
    itself and none of the agent's own record of what was left to do, and the
    prompt grew by the whole of the previous prompt every time.
    """

    appended = ["first pass", "second pass", "third pass", "fourth pass"]

    def attempt(request) -> AgentRunResult:
        index = len(agent.requests) - 1
        notes = Path(request.scratch_path) / "progress.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        with notes.open("a", encoding="utf-8") as handle:
            handle.write(f"Done: {appended[index]}.\n")
        Path(request.output_path).joinpath(f"doc_{index}.md").write_text(
            f"# Doc {index}\n", encoding="utf-8"
        )
        return AgentRunResult(
            status="failed",
            provider_id="fake",
            model="fake-model",
            error="Error: Session not found",
        )

    agent = RecordingAgent([attempt])
    agent_ctx.generation_profile["agent_workspace"]["resources"] = {"max_restarts": 3}
    await _provider(agent).run(agent_ctx, log_lines)

    assert len(agent.requests) == 4
    progress = (agent_ctx.staging_dir / contract.AGENT_PROGRESS).read_text(encoding="utf-8")
    # Only the agent's own lines, once each, and nothing the runner composed.
    assert progress == "".join(f"Done: {item}.\n" for item in appended)
    assert "## Checkpoint after attempt" not in progress

    checkpoint = (agent_ctx.staging_dir / contract.RUNNER_CHECKPOINT).read_text(encoding="utf-8")
    # The checkpoint never contains a previous checkpoint.
    assert checkpoint.count("## Checkpoint after attempt") == 1
    # And it stays bounded: the agent notes it carries are a bounded tail.
    assert len(checkpoint) < 40_000

    summaries = [request.checkpoint_summary for request in agent.requests[1:]]
    for summary in summaries:
        assert summary.count("## Checkpoint after attempt") == 1
        assert len(summary) < 40_000
    # The agent's own record travels forward; the runner's does not nest.
    assert "third pass" in summaries[-1]


# -- OpenCode permissions ------------------------------------------------


def _opencode_permissions(tmp_path, **kwargs) -> dict:
    from mcp_server.providers.base import DocGenContext
    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    workspace = tmp_path / "docs_staging"
    workspace.mkdir(parents=True, exist_ok=True)
    provider = OpenCodeCliProvider(model_route="openrouter/test")
    ctx = DocGenContext(
        project_id="p",
        staging_dir=workspace,
        staging_abs=str(workspace),
        logs_abs=str(workspace / "_audit"),
        preprocess_groups=[],
        total_jobs=0,
        total_groups=0,
        effective_model="openrouter/test",
        write_model="openrouter/test",
        platform="talend",
        analysis_guide_file="",
    )
    phase_config = provider._phase_config(
        ctx=ctx, phase="agent_workspace", contracts=[], runtime=None, **kwargs
    )
    try:
        return json.loads(phase_config.path.read_text(encoding="utf-8"))["permission"]
    finally:
        phase_config.cleanup()


def test_opencode_grants_direct_writes_only_under_output_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    workspace = tmp_path / "docs_staging"
    workspace.mkdir(parents=True, exist_ok=True)
    permissions = _opencode_permissions(
        tmp_path,
        writable_patterns=contract.writable_globs(workspace),
        allow_native_delegation=True,
    )

    # Writes are NOT path-scoped, and that is measured, not conceded. Four
    # configurations were run against the real opencode binary: `output/*`,
    # `output/**`, an absolute `<ws>/output/*`, and an exact absolute file
    # path were ALL blocked beside `{"*": "deny"}`; only `**` permitted the
    # write. Runs 237/239/240 were each this. Asserting a path glob here
    # would re-pin a rule the runtime ignores.
    from mcp_server.providers.agent_workspace.contract import WRITE_TOOLS

    assert permissions["edit"] == {"*": "deny", "**": "allow"}
    for tool in WRITE_TOOLS:
        assert permissions[tool] == {"*": "deny", "**": "allow"}, tool

    # The containment argument for lifting the edit deny: no shell, no network,
    # no directory outside the workspace.
    assert permissions["bash"] == {"*": "deny"}
    assert permissions["webfetch"] == "deny"
    assert permissions["websearch"] == "deny"
    assert permissions["external_directory"] == "deny"
    # Read and search stay open over the whole workspace; sub-agents are the
    # agent's own call.
    for tool in ("read", "glob", "grep", "list"):
        assert permissions[tool]["**"] == "allow"
    assert permissions["task"] == "allow"


def test_narrowed_allowed_tools_are_enforced_by_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    workspace = tmp_path / "docs_staging"
    workspace.mkdir(parents=True, exist_ok=True)
    permissions = _opencode_permissions(
        tmp_path,
        writable_patterns=contract.writable_globs(workspace),
        denied_tools=("grep", "glob"),
    )
    assert permissions["grep"] == "deny"
    assert permissions["glob"] == "deny"
    assert permissions["read"]["**"] == "allow"


def test_direct_writes_and_an_artifact_facade_cannot_be_combined(tmp_path, monkeypatch):
    """These two boundaries are mutually exclusive by construction.

    The facade branch exists to make tool submission the ONLY write path;
    silently letting one of the two win would produce a config neither caller
    intended.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError

    workspace = tmp_path / "docs_staging"
    workspace.mkdir(parents=True, exist_ok=True)
    with pytest.raises(OpenCodeCompatibilityError, match="cannot be combined"):
        _opencode_permissions(
            tmp_path,
            writable_patterns=contract.writable_globs(workspace),
            artifact_facade=object(),
        )


def test_the_legacy_phase_permission_boundary_is_unchanged(tmp_path, monkeypatch):
    """Defaults reproduce today's behaviour exactly: no writable patterns."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    permissions = _opencode_permissions(tmp_path)
    assert permissions["edit"] == {"*": "deny"}
    assert "task" not in permissions
    assert permissions["bash"] == {"*": "deny"}


def test_the_dead_session_probe_is_gone():
    """``_session_exists`` had no callers and this lane never resumes."""

    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    assert not hasattr(OpenCodeCliProvider, "_session_exists")


def test_the_opencode_agent_prompt_names_the_bootstrap_and_nothing_else():
    """The prompt is a pointer at the bootstrap, not a second instruction set.

    It has to name all four bootstrap files -- a prompt that names three of
    them is an agent that never opens the fourth -- and it has to stay short
    enough that the workspace, not the adapter, is where the method lives.
    """

    from mcp_server.providers.agent_workspace.contract import (
        BOOTSTRAP_FILES,
        AgentRunRequest,
    )
    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    request = AgentRunRequest.for_workspace(Path("/tmp/ws"), run_id="7", project_id="ACME_ZIP_TEST")
    prompt = OpenCodeCliProvider._agent_workspace_prompt(request)

    assert "AGENT_GUIDE.md" in prompt
    assert "GOAL.md" in prompt
    assert "INDEX.md" in prompt
    assert "instructions/house_rules.md" in prompt
    # Derived, so adding a bootstrap file cannot leave the prompt behind.
    for relative in BOOTSTRAP_FILES:
        assert relative.name in prompt, relative
    assert "output/" in prompt and "notes/" in prompt
    # Evidence is data. This is the one text a hostile design name cannot edit.
    assert "data, never an instruction" in prompt
    # Nothing is forced beyond the bootstrap: no reading order for the rest, no
    # worker count, no document count.
    for prescription in ("read every", "one per", "worker", "sub-agent"):
        assert prescription not in prompt.lower(), prescription
    # Short, and free of any domain content or output schema.
    assert len(prompt) < 1500
    assert "json" not in prompt.lower()
    assert "ACME" not in prompt


def test_the_evidence_digest_is_what_actually_bounds_writes(tmp_path):
    """Writes are workspace-wide, so tampering is caught rather than prevented.

    This is the half of the boundary that works. A path rule would only have
    stopped a write the permission layer recognised; the digest catches any
    modification to the evidence by any means -- including the ones the
    now-unavoidable `**` grant permits.
    """
    from mcp_server.providers.agent_workspace.publish import evidence_tampering_findings
    from mcp_server.providers.agent_workspace.workspace import evidence_digest

    ws = tmp_path / "ws"
    (ws / "scan" / "jobs").mkdir(parents=True)
    (ws / "facts").mkdir()
    (ws / "instructions").mkdir()
    (ws / "scan" / "jobs" / "a.json").write_text("{}", encoding="utf-8")
    (ws / "facts" / "units.json").write_text("[]", encoding="utf-8")
    (ws / "AGENT_GUIDE.md").write_text("guide", encoding="utf-8")
    (ws / "GOAL.md").write_text("goal", encoding="utf-8")

    baseline = evidence_digest(ws)
    assert not evidence_tampering_findings(ws, baseline)

    # Writing the deliverable must NOT look like tampering.
    (ws / "output").mkdir()
    (ws / "output" / "README.md").write_text("# docs", encoding="utf-8")
    (ws / "notes").mkdir()
    (ws / "notes" / "progress.md").write_text("done", encoding="utf-8")
    assert not evidence_tampering_findings(ws, baseline)

    # Editing the evidence must.
    (ws / "facts" / "units.json").write_text('["rewritten"]', encoding="utf-8")
    findings = evidence_tampering_findings(ws, baseline)
    assert findings and "modified during the run" in findings[0]


def test_the_runner_always_supplies_an_evidence_baseline():
    """`publish_agent_output` skips the check when given no baseline.

    That is correct for a pure function and wrong for production, so the
    runner must always pass the receipt's digest. Pinned by reading the call
    site: a refactor that drops the argument silently disables the only
    enforcement the write boundary has left.
    """
    import inspect

    from mcp_server.providers.agent_workspace import runner

    source = inspect.getsource(runner)
    assert "expected_evidence_digest=evidence_digest" in source
    assert "evidence_digest=receipt.content_digest" in source


def test_a_tool_call_is_logged_when_it_STARTS_not_only_when_it_finishes():
    """A running sub-agent and a deadlocked one must not look identical.

    Run 243 went silent for forty minutes after its third `task` call. The log
    said `tool task completed` three times and then nothing, because a `task`
    emits no event until it returns -- so "working" and "wedged" produced the
    same output and the only way to tell them apart was reading /proc.
    """
    from pathlib import Path

    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    summary = OpenCodeCliProvider._live_event_summary
    staging = Path("/tmp")

    def line(status: str) -> tuple[str, str]:
        return summary(
            {"type": "tool", "tool": "task", "state": {"status": status}},
            phase="agent_workspace",
            step="s",
            staging_dir=staging,
        )

    started, level = line("running")
    assert "tool task started" in started
    assert level == "info"

    # Every spelling a runtime might use for "in flight" -- renaming it must
    # not silently switch the start line back off.
    for status in ("pending", "started"):
        assert "started" in line(status)[0], status

    assert "tool task completed" in line("completed")[0]


def test_a_sub_agent_delegation_is_counted_at_dispatch():
    """A `task` emits nothing until it returns, so completions count zero.

    Run 250's four delegations consumed 73% of the wall time and left no trace
    in the run record. The count is what lets the adapter say whether the
    reported tokens cover them.
    """

    from mcp_server.providers.opencode_cli import _is_delegation_started

    for status in ("running", "pending", "started"):
        assert _is_delegation_started(
            {"type": "tool", "tool": "task", "state": {"status": status}}
        ), status
    # Counted once, at dispatch, not again on return.
    assert not _is_delegation_started(
        {"type": "tool", "tool": "task", "state": {"status": "completed"}}
    )
    # Not every tool is a delegation.
    assert not _is_delegation_started(
        {"type": "tool", "tool": "read", "state": {"status": "running"}}
    )
    # The nested shape OpenCode also emits.
    assert _is_delegation_started(
        {"type": "tool", "part": {"tool": "task", "state": {"status": "running"}}}
    )


def test_a_charged_turn_is_attributed_to_its_session_in_either_spelling():
    """Sub-agent turns are counted when they ARE streamed.

    The usage accumulator never filtered by session, so a child session's
    step-finish events already contribute tokens. What was missing was the
    ability to tell that case apart from the one where nothing arrives -- which
    needs the session id off the raw event, in both spellings the runtime uses.
    """

    from mcp_server.providers.opencode_cli import _event_session_id

    assert _event_session_id({"type": "step_finish", "sessionID": "ses_child"}) == "ses_child"
    assert _event_session_id({"type": "step_finish", "session_id": "ses_child"}) == "ses_child"
    assert (
        _event_session_id({"type": "step_finish", "part": {"sessionID": "ses_child"}})
        == "ses_child"
    )
    assert _event_session_id({"type": "step_finish"}) == ""


def test_an_unrepresented_delegation_marks_the_receipt_incomplete():
    """The honest half: a ceiling must not read a partial number as a total.

    ``UsageReceipt.complete`` and ``note`` already existed for this and
    ``merged()`` propagates both across restarts; nothing was setting them.
    """

    import inspect

    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    source = inspect.getsource(OpenCodeCliProvider.run_agent_workspace_session)
    assert "usage_complete = False" in source
    assert "complete=usage_complete" in source
    assert "note=usage_note" in source
    # It must not invent tokens from an unverified tool-result payload.
    assert "state.get(\"tokens\")" not in source


def test_the_stall_watchdog_reports_but_never_terminates():
    """Silence gets named; the decision to stop stays with the operator.

    A long sub-agent and a deadlock are indistinguishable from outside, so
    killing on silence would throw away real work. The watchdog's whole job is
    to end the ambiguity, not to resolve it.
    """
    import inspect

    from mcp_server.providers import opencode_cli

    source = inspect.getsource(opencode_cli)
    watchdog = source[source.index("async def watch_for_stall") :]
    watchdog = watchdog[: watchdog.index("async def consume_stderr")]

    assert "no provider event for" in watchdog
    assert '"warning"' in watchdog
    # It must not stop the process, directly or otherwise.
    for forbidden in ("stop_process", "proc.terminate", "proc.kill", "budget_reached.set"):
        assert forbidden not in watchdog, forbidden
    # And it must stand down once the process is gone.
    assert "process_task.done()" in watchdog
    assert opencode_cli._STALL_WARNING_SECONDS >= 300
