"""Slice 6: the scenarios the whole accountability contract exists for.

Every test here drives a REAL run through ``AgentWorkspaceProvider`` -- the
builder, a fake adapter, publication, the gate -- and asserts on the terminal
outcome rather than on any single module's return value. That is deliberate:
each defect below was present while the unit tests of the individual modules
passed, because every module was correct about its own half and nothing
checked the composition.

The proven failure this slice is written against, from ``.plans/plan.md``: a
run whose approved scan exposed 142 designs opened six of them, wrote five
short dossiers, compiled an integration model that was invalid JSON and
therefore held zero integrations and zero journeys -- and reported success,
because the provider process had exited normally.

Each scenario below is one clause of that sentence, failing on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.contract import AgentRunResult
from mcp_server.providers.agent_workspace.runner import AgentWorkspaceProvider
from tests.test_agent_workspace.workspace_fixture import (
    RecordingAgent,
    analyse_every_group,
    classify,
    make_workspace,
    seed_units,
    write_receipt,
    workspace_builder,
)

#: Large enough that "the agent looked at a handful" is visibly a handful, and
#: the same order as the estate that produced the failure. Nothing in the lane
#: derives behaviour from the number -- it is a fixture size, not a threshold.
ESTATE = tuple(f"Design{index:03d}" for index in range(142))

REVIEWED = ESTATE[:6]


def _provider(agent, *, units=ESTATE):
    """A provider whose builder stages the fixture layout plus a units view."""

    def hook(staging_dir: Path) -> None:
        seed_units(staging_dir, units)
        # The roster the real builder would have seeded. Written through the
        # same function the builder calls, so a divergence between the two
        # shows up here rather than in production.
        from mcp_server.providers.agent_workspace import ledger

        ledger.seed_roster(staging_dir)

    return AgentWorkspaceProvider(
        build_workspace=workspace_builder(staging_dir_hook=hook),
        build_registry=lambda *, model: _registry(agent),
    )


def _registry(agent):
    from mcp_server.providers.coding_agent.registry import CodingAgentRegistry

    registry: CodingAgentRegistry = CodingAgentRegistry()
    registry.register("fake", lambda: agent)
    return registry


def _behaviour(write_state):
    """Turn a ``(workspace) -> None`` state writer into an agent behaviour."""

    def behaviour(request) -> AgentRunResult:
        document = Path(request.output_path) / "01_overview.md"
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("# Overview\n\nThe estate, described.\n", encoding="utf-8")
        write_state(Path(request.workspace_path))
        return AgentRunResult(status="completed", provider_id="fake", model="fake-model")

    return behaviour


def _errors(log_lines) -> str:
    return "\n".join(message for level, message in log_lines.lines if level == "error")


# ---------------------------------------------------------------------------
# 1. Partial coverage is incomplete, and says how partial
# ---------------------------------------------------------------------------


async def test_six_of_one_hundred_and_forty_two_classified_is_incomplete(agent_ctx, log_lines):
    """The headline failure: a handful reviewed, and a success report.

    The ledger is what makes the shortfall a number instead of an
    investigation. Establishing that the published narrative described roughly
    fifteen of a hundred and thirty-nine designs took three agents and a day;
    this asserts the same fact in one line of a run log.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {"unit_id": name, "disposition": "material", "reason": "", "integration_ids": []}
                for name in REVIEWED
            ),
        )
        write_receipt(workspace, complete=True)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"
    assert "136" in (result.error or ""), result.error
    # Incomplete, never failed: the draft is intact and a resume continues.
    assert (agent_ctx.staging_dir / contract.OUTPUT / "01_overview.md").is_file()


async def test_the_outstanding_count_survives_the_roster_being_deleted(agent_ctx, log_lines):
    """The roster is a convenience; the obligation is recomputed.

    ``notes/`` is agent-writable, so an agent that shortens the roster must not
    shorten what the run owes. This is the same class of defect as trusting an
    approval receipt to name its own allowed roots: a control file the
    controlled party can edit controls nothing.
    """

    def state(workspace: Path) -> None:
        (workspace / contract.COVERAGE_ROSTER).unlink()
        classify(
            workspace,
            (
                {"unit_id": name, "disposition": "material", "reason": "", "integration_ids": []}
                for name in REVIEWED
            ),
        )
        write_receipt(workspace, complete=True, unreviewed_count=0)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert "136" in (result.error or ""), result.error


# ---------------------------------------------------------------------------
# 2. A normal provider exit is not a completion claim
# ---------------------------------------------------------------------------


async def test_a_clean_exit_without_a_receipt_is_incomplete(agent_ctx, log_lines):
    """A provider process ending normally means the session stopped.

    This is the specific step that turned an unfinished investigation into a
    published deliverable: ``returncode == 0``, therefore ``status ==
    "completed"``, therefore publish.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {"unit_id": name, "disposition": "operational", "reason": "logging only"}
                for name in ESTATE
            ),
        )

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"
    assert "receipt" in (result.error or "").casefold(), result.error


async def test_a_receipt_claiming_incomplete_is_honoured(agent_ctx, log_lines):
    """``complete: false`` is a correct outcome, not a malfunction.

    An agent that investigated honestly and ran out of estate must be able to
    say so. If the only way to end a run were to claim completion, the gate
    would be teaching the agent to lie to it.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {"unit_id": name, "disposition": "operational", "reason": "logging only"}
                for name in ESTATE
            ),
        )
        write_receipt(
            workspace,
            complete=False,
            limitations=["the runtime mirror was not connected"],
        )

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


# ---------------------------------------------------------------------------
# 3. An unusable model does not publish
# ---------------------------------------------------------------------------


async def test_invalid_model_json_blocks_publication(agent_ctx, log_lines):
    """Invalid JSON compiled to zero integrations and the run still shipped.

    The model file is agent-written, so a syntax error in it is an ordinary
    event. What must not be ordinary is publishing an estate's documentation
    while the knowledge record behind it is unreadable.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {
                    "unit_id": name,
                    "disposition": "material",
                    "reason": "",
                    "integration_ids": ["int-order-sync"],
                }
                for name in ESTATE
            ),
        )
        (workspace / contract.INTEGRATION_MODEL).write_text(
            '{"schema_version": "integration-knowledge-v2", "integrations": [',
            encoding="utf-8",
        )
        write_receipt(workspace, complete=True, integration_count=1)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


async def test_material_designs_with_no_integrations_block_publication(agent_ctx, log_lines):
    """Material candidates and an empty model is a contradiction, not a result."""

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {
                    "unit_id": name,
                    "disposition": "material",
                    "reason": "",
                    "integration_ids": ["int-order-sync"],
                }
                for name in ESTATE
            ),
        )
        write_receipt(workspace, complete=True)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


# ---------------------------------------------------------------------------
# 4. The path that publishes
# ---------------------------------------------------------------------------


def _complete_state(workspace: Path) -> None:
    """A full, honest accounting: every group analysed, every design classified,
    one modelled integration with an ordered journey and a published document."""

    from mcp_server.providers.agent_workspace import ledger

    # The analysis layer comes first because the run cannot be accepted
    # without it -- that is the whole point of the stage.
    analyse_every_group(workspace)
    from mcp_server.providers.agent_workspace.integration_model import (
        EvidenceRef,
        ImplementingUnit,
        Integration,
        IntegrationKnowledgeBase,
        Journey,
        JourneyStep,
        Participant,
    )

    material = ESTATE[:3]
    classify(
        workspace,
        [
            {
                "unit_id": name,
                "disposition": "material",
                "reason": "",
                "integration_ids": ["int-order-sync"],
            }
            for name in material
        ]
        + [
            {"unit_id": name, "disposition": "utility", "reason": "shared formatting helper"}
            for name in ESTATE[3:]
        ],
    )

    evidence = EvidenceRef(
        id="ev-1",
        label="Design000 parse",
        state="exact",
        evidence_type="scan",
        artifact="scan/jobs/Sales/Design000.json",
    )
    source = Participant(id="par-src", label="Order intake", state="exact")
    target = Participant(id="par-dst", label="Billing", state="exact")
    unit = ImplementingUnit(id="unit-1", label="Design000", state="exact")
    integration = Integration(
        id="int-order-sync",
        label="Order synchronisation",
        state="exact",
        document_paths=["01_overview.md"],
        participants=[source, target],
        implementing_units=[unit],
        evidence_refs=[evidence],
        journeys=[
            Journey(
                id="jny-1",
                label="Order intake to billing",
                state="exact",
                participant_ids=["par-src", "par-dst"],
                steps=[
                    JourneyStep(
                        id="stp-1",
                        label="Read the order file",
                        state="exact",
                        sequence=1,
                        source_participant_id="par-src",
                        unit_ids=["unit-1"],
                    ),
                    JourneyStep(
                        id="stp-2",
                        label="Post the invoice",
                        state="exact",
                        sequence=2,
                        target_participant_id="par-dst",
                        unit_ids=["unit-1"],
                    ),
                ],
            )
        ],
    )
    knowledge = IntegrationKnowledgeBase(integrations=[integration])
    assert knowledge.validate_structure() == []
    knowledge.save(workspace / contract.INTEGRATION_MODEL)

    report = ledger.read_ledger(workspace)
    write_receipt(
        workspace,
        coverage_digest=report.digest(),
        candidate_counts=report.counts,
        unreviewed_count=len(report.unreviewed),
        unresolved_count=len(report.unresolved),
        integration_count=1,
        journey_count=1,
        document_paths=["01_overview.md"],
        complete=True,
    )


async def test_skipping_the_analysis_layer_is_incomplete(agent_ctx, log_lines):
    """The mechanism this whole stage exists for.

    VERIFIED (run 252): the agent read every bootstrap file, every instruction
    file and four platform guides -- about 21 KB of method, including all of
    the depth guidance -- then classified 142 designs in roughly 90 seconds
    having opened SIX design files, wrote seven documents straight from the
    index, and claimed completion. The guidance was not missed; it was read and
    it changed nothing.

    The predecessor Claude Code workflow was deeper because its PHASE 2.5 gate
    verified one analysis file per group existed and was non-empty before any
    document could be written. This is that gate: everything else about the run
    is correct here, and it is still not accepted.
    """

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        # Every analysis removed, nothing else touched: full classification,
        # a valid model, an ordered journey, a published document, a receipt.
        for path in sorted((workspace / contract.ANALYSIS_DIR).glob("*.md")):
            path.unlink()

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"
    assert "analys" in (result.error or "").casefold(), result.error
    # Incomplete, never failed: the draft survives for a resume.
    assert (agent_ctx.staging_dir / contract.OUTPUT / "01_overview.md").is_file()


async def test_one_unanalysed_group_is_enough_to_hold_the_run(agent_ctx, log_lines):
    """Per GROUP, not a total. A run that analysed 20 of 21 has a hole in it.

    Pinned separately from the all-or-nothing case because a count-based check
    ("some analyses exist") would pass this and is the obvious wrong
    implementation.
    """

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        first = sorted((workspace / contract.ANALYSIS_DIR).glob("*.md"))[0]
        first.unlink()

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


async def test_a_thin_analysis_is_reported_but_does_not_block(agent_ctx, log_lines):
    """A stub is a warning, not a refusal.

    The floor exists to catch an empty placeholder, and it cannot tell a short
    honest analysis of a two-design group from a lazy one. Blocking on it would
    be a length quota, which the plan forbids -- so it is advisory and the
    operator decides.
    """

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        first = sorted((workspace / contract.ANALYSIS_DIR).glob("*.md"))[0]
        first.write_text("# thin\n", encoding="utf-8")

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is True, _errors(log_lines)
    warnings = "\n".join(message for level, message in log_lines.lines if level == "warning")
    assert "thin" in warnings.casefold(), warnings


async def test_a_focused_run_is_not_held_by_the_unanalysed_estate(agent_ctx, log_lines):
    """Same asymmetry as the coverage ledger: a focused run owes its own scope.

    Nothing deterministic can compute which groups a focused goal reaches, so
    the gap is reported and not enforced. Paired with the whole-estate test
    above -- if both passed, the scope mode would be decorative.
    """

    agent_ctx.generation_profile["agent_workspace"]["scope_mode"] = "focused"

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        for path in sorted((workspace / contract.ANALYSIS_DIR).glob("*.md")):
            path.unlink()

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is True, _errors(log_lines)


async def test_a_complete_accountable_run_publishes(agent_ctx, log_lines):
    """Everything classified, one modelled integration with an ordered journey.

    The positive control. Without it the suite would be satisfied by a gate
    that refuses everything, which is a failure mode this package has shipped
    before: a permission rule that blocked every write passed its own test by
    asserting the rule was correctly scoped.
    """

    agent = RecordingAgent([_behaviour(_complete_state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is True, _errors(log_lines)
    assert result.outcome == "completed"
    assert "01_overview" in result.required_docs


async def test_an_integration_with_no_journey_blocks_publication(agent_ctx, log_lines):
    """An integration nobody can walk through is a name, not documentation."""

    def state(workspace: Path) -> None:
        from mcp_server.providers.agent_workspace.integration_model import (
            IntegrationKnowledgeBase,
        )

        _complete_state(workspace)
        knowledge = IntegrationKnowledgeBase.load(workspace / contract.INTEGRATION_MODEL)
        knowledge.integrations[0].journeys = []
        knowledge.save(workspace / contract.INTEGRATION_MODEL)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


async def test_an_integration_with_no_published_document_blocks_publication(agent_ctx, log_lines):
    """A modelled integration that no client document covers is undelivered."""

    def state(workspace: Path) -> None:
        from mcp_server.providers.agent_workspace.integration_model import (
            IntegrationKnowledgeBase,
        )

        _complete_state(workspace)
        knowledge = IntegrationKnowledgeBase.load(workspace / contract.INTEGRATION_MODEL)
        knowledge.integrations[0].document_paths = ["99_never_written.md"]
        knowledge.save(workspace / contract.INTEGRATION_MODEL)

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


async def test_an_unresolved_design_keeps_the_run_incomplete(agent_ctx, log_lines):
    """An honest ``unresolved`` reports incomplete rather than inventing a verdict.

    The run does NOT fail and the draft survives: the whole point of allowing
    the disposition is that admitting an unsettled design must cost less than
    guessing at one.
    """

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        classify(
            workspace,
            [
                {
                    "unit_id": ESTATE[7],
                    "disposition": "unresolved",
                    "reason": "the routing predicate is set at runtime and the scan has no value",
                }
            ],
        )

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"
    assert (agent_ctx.staging_dir / contract.OUTPUT / "01_overview.md").is_file()


async def test_the_last_classification_for_a_design_wins(agent_ctx, log_lines):
    """Revision by appending is what makes an append-only log usable.

    An agent that classifies early and learns better later must not have to
    rewrite the file -- a read-modify-write over a 142-line record is exactly
    the fragile operation the append-only shape exists to avoid.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            [{"unit_id": ESTATE[7], "disposition": "unresolved", "reason": "not yet understood"}],
        )
        _complete_state(workspace)
        classify(
            workspace,
            [{"unit_id": ESTATE[7], "disposition": "utility", "reason": "a date formatter"}],
        )

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is True, _errors(log_lines)


# ---------------------------------------------------------------------------
# 5. Focused runs, and the absence of a work plan
# ---------------------------------------------------------------------------


async def test_a_focused_run_is_not_blocked_by_the_unvisited_estate(agent_ctx, log_lines):
    """A focused goal investigates its connected scope and leaves the rest.

    Nothing deterministic can compute which designs a goal reaches, so on a
    focused run the unreviewed count is REPORTED and not enforced. Enforcing it
    would make the two scope modes identical and quietly delete the cheaper
    one.
    """

    agent_ctx.generation_profile["agent_workspace"]["scope_mode"] = "focused"

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        # Nine tenths of the estate never classified: correct for a focused run.
        path = workspace / contract.COVERAGE_LOG
        kept = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["unit_id"] in ESTATE[:3]
        ]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is True, _errors(log_lines)
    warnings = "\n".join(message for level, message in log_lines.lines if level == "warning")
    assert "139" in warnings, warnings


async def test_the_same_estate_blocks_the_identical_run_on_whole_estate(agent_ctx, log_lines):
    """The mode is the only difference, and it changes the verdict.

    Paired with the test above on purpose: if both passed, `scope_mode` would
    be decorative.
    """

    def state(workspace: Path) -> None:
        _complete_state(workspace)
        path = workspace / contract.COVERAGE_LOG
        kept = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["unit_id"] in ESTATE[:3]
        ]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    agent = RecordingAgent([_behaviour(state)])
    result = await _provider(agent).run(agent_ctx, log_lines)

    assert result.success is False
    assert result.outcome == "incomplete"


async def test_nothing_in_the_run_assigns_designs_to_workers(agent_ctx, log_lines):
    """The roster enumerates; it must never partition.

    Plan section 5: Nexus does not prescribe a subagent count and does not
    pre-partition designs into worker batches. The request the adapter receives
    carries a workspace, a goal and a ceiling -- and if a batch, a group
    assignment or a worker count ever appears in it, this is where it shows up.
    """

    # Captured DURING the run. A successful publication assembles a clean room
    # and replaces staging, so `notes/` is gone by the time the run returns --
    # which is correct, and which is why this reads the roster from inside the
    # session rather than after it.
    seen: list[str] = []

    def state(workspace: Path) -> None:
        seen.append((workspace / contract.COVERAGE_ROSTER).read_text(encoding="utf-8"))
        _complete_state(workspace)

    agent = RecordingAgent([_behaviour(state)])
    await _provider(agent).run(agent_ctx, log_lines)

    request = agent.requests[0]
    payload = json.dumps(request.model_dump(mode="json"))
    for forbidden in ("batch", "worker", "assigned", "partition", "chunk"):
        assert forbidden not in payload.casefold(), (forbidden, payload)
    # The roster is on disk for the agent to read, and names no owner.
    roster = seen[0]
    assert roster.count("\n") == len(ESTATE)
    for forbidden in ("assigned_to", "worker", "batch"):
        assert forbidden not in roster


# ---------------------------------------------------------------------------
# 6. The artifacts an operator reads afterwards
# ---------------------------------------------------------------------------


async def test_a_refused_run_still_writes_its_evidence(agent_ctx, log_lines):
    """The failing case is the one whose artifacts get read.

    Writing them only on success would leave the operator of a refused run
    with a log line and nothing to act on.
    """

    def state(workspace: Path) -> None:
        classify(
            workspace,
            (
                {"unit_id": name, "disposition": "material", "reason": "", "integration_ids": []}
                for name in REVIEWED
            ),
        )
        write_receipt(workspace, complete=True)

    agent = RecordingAgent([_behaviour(state)])
    await _provider(agent).run(agent_ctx, log_lines)

    audit = agent_ctx.staging_dir / "_audit"
    coverage = json.loads((audit / "coverage_ledger.json").read_text(encoding="utf-8"))
    accountability = json.loads((audit / "accountability.json").read_text(encoding="utf-8"))
    assert coverage["population_total"] == len(ESTATE)
    assert len(coverage["unreviewed"]) == 136
    assert any(
        finding["severity"] == "blocking" for finding in accountability["findings"]
    ), accountability


@pytest.mark.parametrize(
    "artifact",
    ["_audit/coverage_ledger.json", "_audit/accountability.json"],
)
async def test_the_accountability_artifacts_never_reach_a_client(agent_ctx, log_lines, artifact):
    """Underscore-prefixed, so publication cannot carry them into a document.

    They quote agent free text and internal ids. The publisher's visibility
    rule is what keeps them internal, and this asserts the names actually obey
    it rather than that the rule exists.
    """

    from mcp_server.services.doc_quality_scorer import is_published_doc_path

    assert is_published_doc_path(artifact) is False


#: The two modules that hold this vocabulary in order to ban it. Everything
#: else in the package is swept. Listed by name rather than detected by a
#: marker comment, because a marker an author can add is an opt-out.
_GUARD_MODULES = frozenset({Path(__file__).name, "test_instructions.py"})


def test_no_test_module_carries_a_client_identifier():
    """Fixtures are shared and long-lived; a client name in one leaks into the repo.

    The source package has been swept before and re-grew these names twice --
    once in a prompt template staged into EVERY project's workspace, which
    would have shown one client's job names to another. A test fixture is the
    same exposure with a slower fuse, so the sweep is pinned rather than
    repeated by hand.
    """

    import re

    banned = re.compile(r"ettm|maximo|fourps|werkorder|vise2|assetcheck|alitalend", re.IGNORECASE)
    offenders: list[str] = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.name in _GUARD_MODULES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, offenders


def test_the_workspace_layout_is_unchanged_by_the_ledger(tmp_path: Path):
    """The ledger lives under ``notes/``: agent-writable, never evidence.

    If it moved under ``facts/`` the agent could not write to it, and if it
    moved to the workspace root it would be published.
    """

    staging = make_workspace(tmp_path / "ws")
    assert contract.COVERAGE_LOG.parts[0] == contract.NOTES.as_posix()
    assert contract.COMPLETION_RECEIPT.parts[0] == contract.NOTES.as_posix()
    assert contract.NOTES in contract.WRITABLE_ROOTS
    assert contract.COVERAGE_DIR not in contract.EVIDENCE_ROOTS
    assert (staging / contract.NOTES).is_dir()
