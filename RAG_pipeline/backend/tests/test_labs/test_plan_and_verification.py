"""A visible plan, and a verification pass that cannot run away with the turn.

Two gaps this covers, both measured rather than theoretical:

* **No plan.** The agent produced nothing until the answer arrived, so a
  ninety-second turn and a hung turn looked identical, and the user could not
  tell whether the question had been understood before it was answered.
* **No verification.** `task` was denied globally, so OpenCode's subagents were
  unreachable and a labs turn was a single flat loop. The model that wrote a
  claim was the only thing that ever read it back -- and self-policing is the one
  thing a model is reliably worst at.

Opening `task` is a cost and a recursion risk, so the containment is asserted
here structurally. Every claim below is about the CONFIG, not about the prompt:
a rule a model can decline to follow is not a boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labs.workspace import (
    _BUILTIN_SUBAGENTS,
    _NEXUS_SYSTEM_PROMPT,
    _PLAN_TOOL_WRITE,
    _PLAN_TOOLS,
    _SPAWN_TOOL,
    _VERIFY_AGENT_NAME,
    ensure_labs_workspace,
)

CONVERSATION_ID = 8811
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-verification-token"


@pytest.fixture
def workspace_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(tmp_path / "published"))


async def _config() -> dict:
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
        )
    )
    return json.loads((workspace / "opencode.json").read_text(encoding="utf-8"))


# --- The plan ---------------------------------------------------------------


@pytest.mark.usefixtures("workspace_roots")
async def test_the_agent_can_keep_a_todo_list() -> None:
    config = await _config()
    agent = config["agent"]["nexus"]

    for tool in _PLAN_TOOLS:
        assert config["permission"][tool] == "allow"
        assert agent["tools"][tool] is True


@pytest.mark.usefixtures("workspace_roots")
async def test_the_role_reserves_plans_for_work_that_needs_them() -> None:
    assert "does not need a plan merely because it takes two or three reads" in (
        _NEXUS_SYSTEM_PROMPT
    )
    assert "at least three independent" in _NEXUS_SYSTEM_PROMPT
    assert "long-running workflow" in _NEXUS_SYSTEM_PROMPT
    assert _PLAN_TOOL_WRITE in _NEXUS_SYSTEM_PROMPT


@pytest.mark.usefixtures("workspace_roots")
async def test_the_plan_may_not_be_narrated_into_the_answer() -> None:
    """The half of "plan first" that was missing, and cost a turn its answer.

    The rule used to be "write the plan, then call `todowrite`", which reads as a
    licence to narrate -- and the model took it, turning one turn into thirteen
    paragraphs of running commentary above the answer, internal component ids and
    all. The steps now have a structured route out of the backend
    (`runtime._BusStream._maybe_publish_plan` -> `plan.updated`), so prose is not
    a second channel for them. It is the regression.
    """

    assert (
        "Do not write a plan, or any running commentary on it, into your reply"
        in _NEXUS_SYSTEM_PROMPT
    )
    # Stated a second time where a reader looks for it: the list of what may
    # never appear in an answer, alongside tool names and internal identifiers.
    assert "Running commentary on your own steps" in _NEXUS_SYSTEM_PROMPT


# --- The verification pass --------------------------------------------------


@pytest.mark.usefixtures("workspace_roots")
async def test_only_the_primary_agent_may_spawn_and_only_the_verifier() -> None:
    config = await _config()

    # The floor: every agent OpenCode ships, and the plan agent, cannot spawn.
    assert config["permission"][_SPAWN_TOOL] == "deny"
    assert config["agent"]["plan"]["permission"][_SPAWN_TOOL] == "deny"

    nexus = config["agent"]["nexus"]["permission"][_SPAWN_TOOL]
    assert nexus[_VERIFY_AGENT_NAME] == "allow"
    assert nexus["*"] == "deny"
    # Order is the policy: OpenCode applies the LAST matching rule, so the
    # catch-all has to come first or it would override the exception.
    assert list(nexus) == ["*", _VERIFY_AGENT_NAME]


@pytest.mark.usefixtures("workspace_roots")
async def test_no_subagent_can_spawn_another() -> None:
    """The recursion bound, and it does not depend on a prompt or a pattern.

    Every subagent this workspace declares denies `task` in BOTH its tool map and
    its permission block. A verifier that decides to delegate finds nothing to
    delegate with.
    """

    config = await _config()
    subagents = [_VERIFY_AGENT_NAME, *_BUILTIN_SUBAGENTS]

    for name in subagents:
        agent = config["agent"][name]
        assert agent["mode"] == "subagent", name
        assert agent["tools"][_SPAWN_TOOL] is False, name
        assert agent["permission"][_SPAWN_TOOL] == "deny", name


@pytest.mark.usefixtures("workspace_roots")
async def test_the_verifier_is_read_only_and_bounded() -> None:
    config = await _config()
    verify = config["agent"][_VERIFY_AGENT_NAME]

    assert verify["permission"]["edit"] == "deny"
    assert verify["tools"]["nexus_*"] is False
    assert verify["tools"]["nexus_read_*"] is True
    # Low enough that a verifier which started investigating runs out before it
    # can redo the turn it was asked to check.
    assert verify["maxSteps"] <= 8
    # It reports; it does not rewrite. A second author's prose would be text no
    # grounding rule ever saw.
    assert "Do not rewrite the answer" in verify["prompt"]


@pytest.mark.usefixtures("workspace_roots")
async def test_opencodes_own_subagents_cannot_write_either() -> None:
    """They exist whether this config names them or not.

    Declared because `nexus` now holds a `task` permission whose scoping is a
    claim about how OpenCode matches a rule. If the claim holds these are
    unreachable; if it does not, the worst case is a read-only helper.
    """

    config = await _config()

    for name in _BUILTIN_SUBAGENTS:
        agent = config["agent"][name]
        assert agent["permission"]["edit"] == "deny", name
        assert agent["permission"]["bash"] == "deny", name
        # Consequential capabilities stay denied by exact name, so the deny
        # survives a release that stops honouring wildcard keys.
        assert any(
            key.startswith("nexus_") and value is False
            for key, value in agent["tools"].items()
        ), name


@pytest.mark.usefixtures("workspace_roots")
async def test_verification_is_bounded_to_the_answers_that_need_it() -> None:
    # Paying for a second agent on "what does this job do" wastes the turn.
    assert "needs no second agent" in _NEXUS_SYSTEM_PROMPT
    assert "explicitly asks for an audit" in _NEXUS_SYSTEM_PROMPT
    assert "high-stakes" in _NEXUS_SYSTEM_PROMPT
    assert "do not let it repeat the investigation" in _NEXUS_SYSTEM_PROMPT
