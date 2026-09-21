"""The `nexus` agent has a system role, and the role carries the hard rules.

Verified in a live container before this change: the workspace's
`opencode.json` defined `nexus` with `maxSteps: 24` and no `prompt` at all,
while `plan` right beside it had one. `nexus` therefore ran on OpenCode's
built-in generic prompt -- "You are opencode, an interactive CLI tool that helps
users with software engineering tasks" -- and every Nexus instruction reached it
as `AGENTS.md` project context, which is advisory material rather than what the
model IS.

Four things are asserted here because each corresponds to a measured
client-facing failure or product boundary:

* The agent has a role at all.
* The role covers all authorized project knowledge rather than integrations only.
* Indexed evidence, generated artifacts, runtime data, and Packs remain distinct.
* Absence discipline: the agent may report what it did not find, never what does
  not exist.

`_NEXUS_SYSTEM_PROMPT` is now the CHAT-mode role specifically -- the role is
assembled per turn by `nexus_system_prompt`, because deep dive is told two
things chat is not. Everything asserted in this file is mode-invariant and is
asserted against the chat build; what differs between the modes is pinned in
`test_deep_dive_mode.py`, which also asserts that the invariants below survive
both.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from src.labs.contracts import LabsTurnMode
from src.labs.workspace import (
    _NEXUS_SYSTEM_PROMPT,
    ORIGIN_SYSTEM_PROMPT,
    ensure_labs_workspace,
    nexus_system_prompt,
    project_workspace_files,
)

CONVERSATION_ID = 8801
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-system-prompt-token"


@pytest.fixture
def workspace_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(tmp_path / "published"))


@pytest.mark.usefixtures("workspace_roots")
async def test_the_nexus_agent_is_configured_with_a_prompt() -> None:
    # The whole defect, in one assertion. `plan` is checked alongside it so a
    # future edit cannot fix one agent by breaking the other.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
        )
    )
    config = json.loads((workspace / "opencode.json").read_text(encoding="utf-8"))

    assert config["agent"]["nexus"]["prompt"] == _NEXUS_SYSTEM_PROMPT
    assert config["agent"]["plan"]["prompt"]
    assert config["permission"]["external_directory"] == "deny"
    assert stat.S_IMODE((workspace / "opencode.json").stat().st_mode) == 0o600


def test_the_constant_is_the_chat_mode_build_of_the_role() -> None:
    # A default argument and a module constant are two statements of the same
    # thing, so they are pinned to each other rather than maintained apart. A
    # workspace written with no mode, and every test below, must be asserting
    # against the role an ordinary turn is actually given.
    assert _NEXUS_SYSTEM_PROMPT == nexus_system_prompt(LabsTurnMode.CHAT)
    assert _NEXUS_SYSTEM_PROMPT == nexus_system_prompt()


def test_the_role_states_who_the_agent_is_and_where_its_knowledge_comes_from() -> None:
    assert _NEXUS_SYSTEM_PROMPT.startswith("You are Nexus")
    assert "project knowledge assistant" in _NEXUS_SYSTEM_PROMPT
    assert "business processes" in _NEXUS_SYSTEM_PROMPT
    assert "controls" in _NEXUS_SYSTEM_PROMPT
    assert "no independent knowledge of the client" in _NEXUS_SYSTEM_PROMPT
    assert "integration-documentation and integration-runtime analyst" not in _NEXUS_SYSTEM_PROMPT


def test_source_boundaries_are_short_and_unambiguous() -> None:
    assert "search_documentation" in _NEXUS_SYSTEM_PROMPT
    assert "search_within_document" in _NEXUS_SYSTEM_PROMPT
    assert "read_document" in _NEXUS_SYSTEM_PROMPT
    assert "list_generated_documents" in _NEXUS_SYSTEM_PROMPT
    assert "start_documentation_generation" in _NEXUS_SYSTEM_PROMPT
    assert "Runtime tools answer present-tense questions" in _NEXUS_SYSTEM_PROMPT
    assert "A Pack is configuration" in _NEXUS_SYSTEM_PROMPT
    assert "read_full_document" not in _NEXUS_SYSTEM_PROMPT
    assert "list_project_documents" not in _NEXUS_SYSTEM_PROMPT


def test_efficiency_rules_prevent_the_measured_tool_loop() -> None:
    assert "does not need a plan merely because it takes two or three reads" in _NEXUS_SYSTEM_PROMPT
    assert "one to three evidence calls" in _NEXUS_SYSTEM_PROMPT
    assert "three to five" in _NEXUS_SYSTEM_PROMPT
    assert "Stop as soon as the answer is adequately supported" in _NEXUS_SYSTEM_PROMPT
    assert "Never respond to an oversized full-document result" in _NEXUS_SYSTEM_PROMPT
    # The prohibition alone was not enough: the measured run obeyed the tool's own
    # "read the sections you need one at a time" over this rule, because that text
    # arrives at the moment of the decision. The rule now has to name the cheap
    # alternative, not only forbid the expensive one.
    assert "firing one call per section" in _NEXUS_SYSTEM_PROMPT
    assert "single `sections` call" in _NEXUS_SYSTEM_PROMPT
    assert "A count or complete list from a complete section needs no second agent" in _NEXUS_SYSTEM_PROMPT
    assert "do not let it repeat the investigation" in _NEXUS_SYSTEM_PROMPT


def test_documentation_questions_are_rag_first_and_stop_when_search_is_sufficient() -> None:
    assert "make one `search_documentation` call first" in _NEXUS_SYSTEM_PROMPT
    assert "returned passages are answer-ready evidence" in _NEXUS_SYSTEM_PROMPT
    assert "answer from them immediately" in _NEXUS_SYSTEM_PROMPT
    assert "not a required second stage after search" in _NEXUS_SYSTEM_PROMPT
    assert "only for a specific detail that is missing or truncated" in _NEXUS_SYSTEM_PROMPT
    assert "Do not open an outline or section merely to gather more context" in _NEXUS_SYSTEM_PROMPT


def test_documentation_answers_do_not_expand_beyond_the_question() -> None:
    assert "Do not expand a design or documentation answer" in _NEXUS_SYSTEM_PROMPT
    assert "monitoring, security, runtime behavior, operational risks" in _NEXUS_SYSTEM_PROMPT
    assert "unless the user asks for them or they are essential" in _NEXUS_SYSTEM_PROMPT
    assert "More available evidence is not a reason to retrieve it" in _NEXUS_SYSTEM_PROMPT


def test_completeness_requires_an_ordered_read() -> None:
    assert "never establishes" in _NEXUS_SYSTEM_PROMPT
    assert "read the complete relevant section" in _NEXUS_SYSTEM_PROMPT
    assert "Never invent or infer" in _NEXUS_SYSTEM_PROMPT


def test_asserting_non_existence_is_forbidden_and_the_alternative_is_given() -> None:
    # The measured failure: the agent searched a prose corpus that provably does
    # not contain a rule, found nothing, and published "No order- or
    # product-level filter is recorded" to a client.
    assert "may never state that something does not exist" in _NEXUS_SYSTEM_PROMPT
    assert "I did not find X" in _NEXUS_SYSTEM_PROMPT
    assert "It is never a fact about the client." in _NEXUS_SYSTEM_PROMPT


def test_an_empty_result_requires_a_stated_next_step() -> None:
    assert "followed by the next step that would settle it" in _NEXUS_SYSTEM_PROMPT
    assert "read the `scope` it came back with" in _NEXUS_SYSTEM_PROMPT


def test_the_role_is_shown_to_the_user_as_part_of_their_workspace() -> None:
    # `opencode.json` carries the MCP bearer and is deliberately never
    # projected. Without a projected copy the one surface where a user can see
    # what their assistant was told would show them everything except the part
    # with the most authority over the answer.
    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
    )
    prompt_file = next(file for file in files if file.origin == ORIGIN_SYSTEM_PROMPT)

    assert prompt_file.path == ".nexus/SYSTEM_PROMPT.md"
    assert prompt_file.content == _NEXUS_SYSTEM_PROMPT
    assert prompt_file.always_loaded is True


@pytest.mark.usefixtures("workspace_roots")
async def test_the_projected_role_is_the_configured_role() -> None:
    # Two copies of the same text are only safe while they are provably the same
    # text: one reaches the model, the other is what a user is shown.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
        )
    )
    config = json.loads((workspace / "opencode.json").read_text(encoding="utf-8"))
    on_disk = (workspace / ".nexus" / "SYSTEM_PROMPT.md").read_text(encoding="utf-8")

    assert config["agent"]["nexus"]["prompt"] == on_disk
