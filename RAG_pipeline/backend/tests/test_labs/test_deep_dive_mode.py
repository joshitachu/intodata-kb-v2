"""Deep dive is a MODE of this lane, not a second lane.

Same agent, same thread, same model. The only difference is how wide the
evidence menu is: chat sees the generated documentation and the runtime, deep
dive additionally sees the raw scan -- which holds the component parameters,
expressions, conditions and SQL that documentation summarises away.

Four things have to hold for that to be true, and each has its own section
below:

* The mode is carried per TURN, from the request through to the scope the MCP
  bridge rebuilds. Per turn, because the same thread asks an ordinary question
  and then a deep one; a mode stored on the conversation would make the second
  answer depend on the first turn's toggle.
* The catalogue advertises the scan in BOTH modes, always. This is the
  correction: `tools/list` is answered once per OpenCode session and cached --
  measured on conversation 661, one catalogue served three turns -- so a menu
  filtered by mode froze at whatever the thread's first turn was. A chat turn
  followed by a deep-dive turn never saw the scan tools at all, and the agent
  spent 44 seconds on `glob` trying to find the source on the filesystem.
* `tools/call` refuses the scan in chat mode, every call, server-side. That is
  now the whole of the mode gate, and it is the layer that already saved us: on
  conversation 657 a deep-dive-then-chat thread still had the scan in its cached
  menu, the model called it, and the call was refused.
* The system prompt says what the mode means -- in deep dive, that the scan is
  the authority; in chat, that an absence in the documentation is an absence in
  a summary and Deep Dive is the next step, rather than a dead end with a
  referral to the functional owner attached. The two prompts reach the model as
  two AGENT DEFINITIONS, both written on every turn, because a session resolves
  its agent once and only the message body's `agent` field is honoured per turn.

The capability ids are asserted as strings on purpose. They are owned by the
scan provider, not by this lane, and the gate has to be correct before that
provider registers them -- so the bridge tests here register their own stub
definitions under those ids rather than reading the live registry.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.labs import mcp_bridge as labs_bridge
from src.labs import runtime as labs_runtime
from src.labs.contracts import (
    LabsTokenRegistry,
    LabsTurnMode,
    LabsTurnModeRegistry,
    LabsTurnScope,
    labs_turn_budget,
    labs_turn_modes,
)
from src.labs.mcp_bridge import (
    _DEEP_DIVE_CAPABILITY_IDS,
    _DEFAULT_ALLOWLIST,
    _tool_name_map,
    _visible_capabilities,
    deep_dive_tool_names,
    drain_tool_calls,
)
from src.labs.workspace import (
    _AGENT_NAME,
    _DEEP_DIVE_AGENT_NAME,
    _NEXUS_SYSTEM_PROMPT,
    _SCAN_TOOLS,
    ensure_labs_workspace,
    labs_agent_for,
    nexus_system_prompt,
    project_workspace_files,
)
from src.harness.capability_runtime import (
    CapabilityObservation,
    CapabilityObservationStatus,
)
from src.harness.models import (
    CapabilityDefinition,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.schemas import ConversationMessageRequest

CONVERSATION_ID = 9301
PROJECT = "acme_landscape"
SOURCE_SEARCH = "intodoc.source.search"
SOURCE_READ_COMPONENT = "intodoc.source.read_component"
#: An ordinary documentation read, in the chat allowlist. The control: it is in
#: the menu and runnable in BOTH modes, so a test that sees the scan disappear
#: knows the mode removed it rather than the menu being empty.
DOCS_SEARCH = "rag.docs.search_evidence"


# --- Helpers ---------------------------------------------------------------


def _scan_capability(capability_id: str, semantic_name: str) -> CapabilityDefinition:
    """A stand-in for a scan capability the scan provider has not registered yet.

    Deliberately minimal and deliberately READ_ONLY: the gate under test is the
    mode, so every other filter in `_visible_capabilities` -- tier, permissions,
    preconditions -- is set to pass, and a capability that disappears from the
    menu can only have been removed by the mode.
    """

    return CapabilityDefinition(
        id=capability_id,
        label=f"Stub for {capability_id}",
        semantic_name=semantic_name,
        provider_type=HarnessProviderType.SOURCE,
        provider_id="intodoc.source",
        action="read_source_evidence",
        input_schema={"type": "object", "properties": {}},
        output_type="evidence_items",
        risk_tier=HarnessRiskTier.READ_ONLY,
    )


class _RecordingExecutor:
    """Stands in for `HarnessCapabilityExecutor` and records what it was handed.

    The context matters as much as the call: `allowed_capability_ids` is the
    backstop behind the bridge's own mode check, so a test that only asserted
    "the call went through" would not notice that the second layer had been
    handed the unfiltered set.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, set[str] | None]] = []

    async def execute(self, request: Any, context: Any) -> CapabilityObservation:
        self.calls.append((request.capability_id, context.allowed_capability_ids))
        return CapabilityObservation(
            capability_id=request.capability_id,
            status=CapabilityObservationStatus.SUCCEEDED,
            summary="Read the scan.",
        )


def _runtime_with_scan(executor: _RecordingExecutor | None = None) -> SimpleNamespace:
    """A registry holding the two scan capabilities and one ordinary read.

    The ordinary read is not decoration. It is the only capability a chat turn
    may actually run, so it is what proves that the mode filters the scan rather
    than emptying the menu -- and it is the call whose execution context can be
    inspected to check that the policy engine's own allowed set was narrowed.
    """

    capabilities = [
        _scan_capability(SOURCE_SEARCH, "search_source"),
        _scan_capability(SOURCE_READ_COMPONENT, "read_component"),
        _scan_capability(DOCS_SEARCH, "search_documentation"),
    ]
    return SimpleNamespace(
        registry=SimpleNamespace(list=lambda enabled_only=True: capabilities),
        executor=executor if executor is not None else _RecordingExecutor(),
    )


async def _call_tool(
    tool_name: str, mode: LabsTurnMode, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], _RecordingExecutor]:
    """Drive the real `tools/call` handler for one tool in one mode.

    The handler rather than the predicate, because the refusal is not only a
    boolean: what a refused model is TOLD decides whether it answers from the
    documentation or spends the turn hunting for the source on a filesystem that
    does not have it. That text is part of the fix and is asserted here.
    """

    executor = _RecordingExecutor()

    async def _runtime(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return _runtime_with_scan(executor)

    monkeypatch.setattr(labs_bridge, "_build_runtime", _runtime)
    result = await labs_bridge._handle_tools_call(
        None, None, _scope(mode), {"name": tool_name, "arguments": {}}
    )
    return result, executor


def _scope(mode: LabsTurnMode = LabsTurnMode.CHAT) -> LabsTurnScope:
    return LabsTurnScope(
        user_id=7,
        conversation_id=CONVERSATION_ID,
        user_groups=["developer"],
        project_scope=[PROJECT],
        permissions=frozenset(),
        access_token=None,
        workspace_dir="/tmp/labs",
        mode=mode,
    )


def _visible_ids(mode: LabsTurnMode) -> set[str]:
    return {
        capability.id
        for capability in _visible_capabilities(_runtime_with_scan(), _scope(mode))
    }


@pytest.fixture(autouse=True)
def _no_live_turn() -> Any:
    """No live turn is registered unless a test registers one.

    `labs_turn_modes` is a process-wide singleton read by the bridge, so a test
    that left an entry behind would hand the next test a mode it never asked
    for -- which is exactly the leak the turn boundary's `end_turn` exists to
    prevent, and it would be invisible here without this.

    The evidence ledger and the tool budget are process-wide for the same
    reason and are cleared alongside: a refused `tools/call` is recorded and
    counted, so a test that drove one would otherwise hand the next test a
    partly spent allowance and a ledger entry it never made.
    """

    labs_turn_modes.end_turn(CONVERSATION_ID)
    labs_turn_budget.end_turn(CONVERSATION_ID)
    drain_tool_calls(CONVERSATION_ID)
    yield
    labs_turn_modes.end_turn(CONVERSATION_ID)
    labs_turn_budget.end_turn(CONVERSATION_ID)
    drain_tool_calls(CONVERSATION_ID)


@pytest.fixture
def workspace_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(tmp_path / "published"))


# --- The request carries the mode ------------------------------------------


def test_the_mode_defaults_to_chat_when_the_client_sends_none() -> None:
    # Every existing client, and the production `/chat` route that shares this
    # schema, sends no mode at all. They must keep getting the narrow menu.
    request = ConversationMessageRequest(query="Which orders go through FMD?")

    assert request.mode is LabsTurnMode.CHAT


def test_deep_dive_is_accepted_and_arrives_typed() -> None:
    request = ConversationMessageRequest(
        query="Which orders go through FMD?", mode="deep_dive"
    )

    assert request.mode is LabsTurnMode.DEEP_DIVE


@pytest.mark.parametrize("value", ["deep-dive", "DEEP_DIVE", "deep", "", "scan"])
def test_an_unknown_mode_is_rejected_at_the_schema(value: str) -> None:
    # Rejected rather than coerced: a client that misspells the mode is asking
    # for something this server does not have, and silently answering in chat
    # mode would look like deep dive simply not working.
    with pytest.raises(ValidationError):
        ConversationMessageRequest(query="Which orders go through FMD?", mode=value)


# --- The mode survives the turn token --------------------------------------


def test_the_mode_survives_a_mint_and_resolve_round_trip() -> None:
    registry = LabsTokenRegistry(secret="deep-dive-test")

    resolved = registry.resolve(registry.mint(_scope(LabsTurnMode.DEEP_DIVE)))

    assert resolved is not None
    assert resolved.mode is LabsTurnMode.DEEP_DIVE


def test_a_token_minted_before_the_mode_existed_resolves_to_chat() -> None:
    # Forged the way an older build would have minted it: every field except the
    # mode. The token is still valid and must keep working -- at the narrow menu.
    registry = LabsTokenRegistry(secret="deep-dive-test")
    token = registry.mint(_scope(LabsTurnMode.DEEP_DIVE))
    payload_part, _, _ = token.partition(".")
    body = json.loads(
        base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    )
    del body["md"]

    resolved = registry.resolve(_reseal(registry, body))

    assert resolved is not None
    assert resolved.mode is LabsTurnMode.CHAT


def test_a_token_naming_an_unknown_mode_resolves_to_chat() -> None:
    """Fail closed, and keep the rest of the scope.

    A newer build could mint a mode this one has never heard of. Rejecting the
    whole token would 403 every tool call the turn makes; widening on an
    unrecognised name would be worse. The only safe reading of an unknown
    breadth is the narrowest one.
    """

    registry = LabsTokenRegistry(secret="deep-dive-test")
    token = registry.mint(_scope(LabsTurnMode.DEEP_DIVE))
    payload_part, _, _ = token.partition(".")
    body = json.loads(
        base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
    )
    body["md"] = "read_the_whole_estate"

    resolved = registry.resolve(_reseal(registry, body))

    assert resolved is not None
    assert resolved.mode is LabsTurnMode.CHAT
    assert resolved.user_id == 7
    assert resolved.project_scope == [PROJECT]


def _reseal(registry: LabsTokenRegistry, body: dict[str, Any]) -> str:
    """Re-sign an edited payload with the registry's own key.

    Reaching into `_sign` is deliberate: the point of these two tests is a token
    that is genuinely VALID and merely says something this build does not
    understand. An unsigned forgery would only exercise the signature check.
    """

    payload = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":")).encode()
    ).rstrip(b"=")
    return f"{payload.decode()}.{registry._sign(payload)}"


# --- The catalogue is a superset of both modes ------------------------------
#
# Rewritten, not relaxed. These tests used to assert that `tools/list` hid the
# scan in chat mode, which was a true description of the code and a false
# description of the product: the catalogue is requested once per OpenCode
# session and cached, so the hiding froze at the thread's first turn. The
# assertions below state the new gate; the `tools/call` section that follows is
# where the mode is now enforced, and those are the load-bearing tests.


@pytest.mark.parametrize("mode", list(LabsTurnMode))
def test_both_modes_advertise_both_scan_capabilities(mode: LabsTurnMode) -> None:
    """The cached menu has to be a superset, or a later turn cannot widen.

    Measured on conversation 661: three turns, ONE `tools/list`. A chat turn
    followed by a deep-dive turn left the scan permanently out of the menu, and
    the agent -- correctly reporting it could not reach the source -- fell back
    to `glob` for 44 seconds.
    """

    assert _visible_ids(mode) == {SOURCE_SEARCH, SOURCE_READ_COMPONENT, DOCS_SEARCH}


def test_the_scan_is_not_in_the_chat_allowlist() -> None:
    # Held apart from `_DEFAULT_ALLOWLIST` rather than added to it: that set is
    # the chat menu, and it is separately asserted to be a subset of the live
    # registry, which these ids only become once the scan provider lands.
    assert _DEEP_DIVE_CAPABILITY_IDS == {SOURCE_SEARCH, SOURCE_READ_COMPONENT}
    assert not (_DEEP_DIVE_CAPABILITY_IDS & _DEFAULT_ALLOWLIST)


@pytest.mark.parametrize("mode", list(LabsTurnMode))
def test_the_wildcard_override_leaves_the_catalogue_a_superset(
    mode: LabsTurnMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LABS_CAPABILITY_ALLOWLIST="*"` cannot narrow the menu either.

    It means "advertise everything the tier and permission filters allow", and
    the scan now qualifies in every mode. What it must still not do is let a
    chat turn RUN the scan -- see
    `test_the_wildcard_override_cannot_let_chat_run_the_scan`.
    """

    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", "*")

    assert _visible_ids(mode) == {SOURCE_SEARCH, SOURCE_READ_COMPONENT, DOCS_SEARCH}


@pytest.mark.parametrize("mode", list(LabsTurnMode))
def test_a_narrow_operator_allowlist_still_advertises_the_scan(
    mode: LabsTurnMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The scan is added to the configured allowlist rather than replacing it,
    # and unconditionally: an operator who narrowed the menu to debug one
    # capability has not thereby made deep dive impossible for the rest of the
    # session, which is what a mode-conditional union would have done.
    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", DOCS_SEARCH)

    assert _visible_ids(mode) == {SOURCE_SEARCH, SOURCE_READ_COMPONENT, DOCS_SEARCH}


# --- `tools/call` is where the mode is enforced -----------------------------


@pytest.mark.parametrize("tool_name", ["search_source", "read_component"])
async def test_chat_mode_refuses_a_scan_call(
    tool_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate that already saved us, now carrying the whole mode.

    Observed on conversation 657: a deep-dive-then-chat thread kept the scan in
    its cached menu, the model called it, and the call came back `status:
    error`. The agent `tools` map in `opencode.json` is a client-side hint; this
    is the server, and it is the authority.
    """

    result, executor = await _call_tool(tool_name, LabsTurnMode.CHAT, monkeypatch)

    assert result["isError"] is True
    assert result["structuredContent"]["status"] == "denied"
    assert result["structuredContent"]["error_code"] == "allowlist"
    # The executor is never reached. A refusal that ran the capability and threw
    # the result away would still have read the client's scan.
    assert executor.calls == []


async def test_a_refused_scan_call_tells_the_model_what_to_do_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare denial code is what sent one turn hunting the filesystem.

    "Denied by policy: allowlist" reads to a model as a broken tool, and the
    measured response was 44 seconds of `glob`. The message has to name the
    reason, the remedy, and who can apply it -- the user, with a toggle the
    model cannot reach.
    """

    result, _executor = await _call_tool(
        "search_source", LabsTurnMode.CHAT, monkeypatch
    )
    message = result["structuredContent"]["error_message"]

    assert "Deep Dive" in message
    assert "do not go looking for the source with file tools" in message
    assert "You cannot switch mode yourself." in message


@pytest.mark.parametrize("tool_name", ["search_source", "read_component"])
async def test_deep_dive_runs_the_scan_call(
    tool_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, executor = await _call_tool(tool_name, LabsTurnMode.DEEP_DIVE, monkeypatch)

    assert result["isError"] is False
    assert [call[0] for call in executor.calls] == [
        SOURCE_SEARCH if tool_name == "search_source" else SOURCE_READ_COMPONENT
    ]


async def test_an_ordinary_read_still_runs_in_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The control. A chat turn that could run nothing would pass every refusal
    # assertion above for entirely the wrong reason.
    result, executor = await _call_tool(
        "search_documentation", LabsTurnMode.CHAT, monkeypatch
    )

    assert result["isError"] is False
    assert [call[0] for call in executor.calls] == [DOCS_SEARCH]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (LabsTurnMode.CHAT, {DOCS_SEARCH}),
        (
            LabsTurnMode.DEEP_DIVE,
            {DOCS_SEARCH, SOURCE_SEARCH, SOURCE_READ_COMPONENT},
        ),
    ],
)
async def test_the_executor_is_handed_the_mode_filtered_allowed_set(
    mode: LabsTurnMode, expected: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two layers, and the second does not trust the first.

    `CapabilityExecutionContext.allowed_capability_ids` is the policy engine's
    own gate, and it is given the ADVERTISED catalogue minus what this mode may
    not run -- not the whole name map. So the executor would raise an ALLOWLIST
    denial for a scan call in chat even if the bridge's explicit refusal above
    it were deleted. Defence in depth costs one set comprehension here.
    """

    _result, executor = await _call_tool("search_documentation", mode, monkeypatch)

    assert executor.calls
    _capability_id, allowed = executor.calls[0]
    assert allowed == expected


def test_the_scan_is_advertised_under_its_semantic_names() -> None:
    # The MCP tool name is what the model reasons with, and it is what the
    # deep-dive system prompt names. The two have to agree.
    names = _tool_name_map(
        _visible_capabilities(_runtime_with_scan(), _scope(LabsTurnMode.DEEP_DIVE))
    )

    assert names["search_source"] == SOURCE_SEARCH
    assert names["read_component"] == SOURCE_READ_COMPONENT


def test_the_gated_ids_are_the_scan_provider_s_real_ids() -> None:
    """The gate and the provider have to agree on four strings.

    The ids are the contract between this lane and the scan provider, and the
    semantic names are what the model is told to call in the deep-dive system
    prompt. Asserted against the base registry, which is where the provider
    registers, so a rename on either side fails here rather than silently
    producing a mode that advertises nothing and a prompt naming tools that do
    not exist.

    Skipped, loudly, while the provider has not landed. The gate is correct
    before the capabilities exist -- it is written against the ids as strings --
    and the stub-backed tests above prove it; this one cannot run until there is
    something real to compare against.
    """

    from src.harness.registry import base_capability_registry

    registry = base_capability_registry()
    registered = {capability.id: capability for capability in registry.list()}
    missing = sorted(_DEEP_DIVE_CAPABILITY_IDS - set(registered))
    if missing:
        pytest.skip(f"the scan provider has not registered: {', '.join(missing)}")

    assert registered[SOURCE_SEARCH].semantic_name == "search_source"
    assert registered[SOURCE_READ_COMPONENT].semantic_name == "read_component"
    # Both must survive the filters the bridge applies before the mode is even
    # consulted, or deep dive advertises an empty menu for a reason that has
    # nothing to do with the mode.
    for capability_id in _DEEP_DIVE_CAPABILITY_IDS:
        capability = registered[capability_id]
        assert capability.risk_tier is HarnessRiskTier.READ_ONLY
        assert capability.required_permissions == []
        assert capability.metadata.get("requires_explicit_approval") is not True


async def test_the_wildcard_override_cannot_let_chat_run_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same treatment topology already gets, moved to the layer that decides.

    `LABS_CAPABILITY_ALLOWLIST="*"` widens the CATALOGUE, and always did. What
    it must never do is let an ordinary question run a source search, and the
    place that still holds is `_excluded_capability_ids` -- which the wildcard
    is not an input to.
    """

    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", "*")

    result, executor = await _call_tool(
        "search_source", LabsTurnMode.CHAT, monkeypatch
    )

    assert result["isError"] is True
    assert executor.calls == []


async def test_the_live_turn_mode_wins_over_the_mode_baked_into_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect a token-only mode would have shipped.

    The MCP bearer is minted once per CONVERSATION and cached by OpenCode for
    eight hours; `_resolve_binding` reuses a token that still verifies rather
    than re-minting it. So a thread whose first turn was chat would be stuck in
    chat for the life of the token, silently, however often the user toggled.

    Asserted against `tools/call` rather than the catalogue. That is where the
    mode is read now, and it is also the only place the distinction between
    "the token's mode" and "this turn's mode" has any consequence left.
    """

    labs_turn_modes.start_turn(CONVERSATION_ID, LabsTurnMode.DEEP_DIVE)

    # The scope still says chat -- it is what the conversation's token was
    # minted under -- and the live turn is what decides.
    result, executor = await _call_tool(
        "search_source", LabsTurnMode.CHAT, monkeypatch
    )

    assert result["isError"] is False
    assert [call[0] for call in executor.calls] == [SOURCE_SEARCH]


async def test_the_token_mode_is_the_floor_outside_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No live turn is registered (the autouse fixture guarantees it), so the
    # only statement of breadth is the token's own, and it is honoured -- in
    # both directions.
    allowed, _executor = await _call_tool(
        "search_source", LabsTurnMode.DEEP_DIVE, monkeypatch
    )
    refused, refused_executor = await _call_tool(
        "search_source", LabsTurnMode.CHAT, monkeypatch
    )

    assert allowed["isError"] is False
    assert refused["isError"] is True
    assert refused_executor.calls == []


def test_an_unstarted_conversation_reads_as_chat() -> None:
    registry = LabsTurnModeRegistry()

    assert registry.current(CONVERSATION_ID) is LabsTurnMode.CHAT
    assert registry.current(CONVERSATION_ID, LabsTurnMode.DEEP_DIVE) is (
        LabsTurnMode.DEEP_DIVE
    )


# --- The turn boundary owns the mode's lifetime ----------------------------


async def test_the_turn_boundary_publishes_and_then_clears_the_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes = LabsTurnModeRegistry()
    monkeypatch.setattr(labs_runtime, "labs_turn_modes", modes)

    async def _lose_the_race(**_kwargs: Any) -> None:
        return None

    # Published ahead of the claim, so a turn that loses the race still runs
    # under the mode the user asked for rather than the previous turn's.
    monkeypatch.setattr(labs_runtime, "_acquire_labs_claim", _lose_the_race)

    async with labs_runtime.labs_turn_claim(
        db=None,
        conversation_id=CONVERSATION_ID,
        user_id=7,
        message_id=1,
        mode=LabsTurnMode.DEEP_DIVE,
    ) as guard:
        assert guard.claim is None
        assert modes.current(CONVERSATION_ID) is LabsTurnMode.DEEP_DIVE

    # Cleared, or the NEXT turn in this conversation inherits the wide menu
    # without anyone having asked for it.
    assert modes.current(CONVERSATION_ID) is LabsTurnMode.CHAT


async def test_the_default_turn_is_a_chat_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes = LabsTurnModeRegistry()
    monkeypatch.setattr(labs_runtime, "labs_turn_modes", modes)

    async def _lose_the_race(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(labs_runtime, "_acquire_labs_claim", _lose_the_race)

    async with labs_runtime.labs_turn_claim(
        db=None, conversation_id=CONVERSATION_ID, user_id=7, message_id=1
    ):
        assert modes.current(CONVERSATION_ID) is LabsTurnMode.CHAT


# --- The prompt says what the mode means -----------------------------------


def test_the_two_modes_are_given_different_roles() -> None:
    assert nexus_system_prompt(LabsTurnMode.CHAT) != nexus_system_prompt(
        LabsTurnMode.DEEP_DIVE
    )
    assert nexus_system_prompt() == _NEXUS_SYSTEM_PROMPT


def test_deep_dive_introduces_the_scan_and_makes_it_the_authority() -> None:
    prompt = nexus_system_prompt(LabsTurnMode.DEEP_DIVE)

    assert "`search_source` and `read_component` read the SCANNED SOURCE" in prompt
    assert "component parameters" in prompt
    assert "the literal SQL a job runs" in prompt
    assert (
        "The scanned source is the authority when it and the documentation disagree"
        in prompt
    )
    assert "never settle it by picking the more convenient reading" in prompt


def test_deep_dive_widens_the_sources_without_widening_the_budget() -> None:
    """"More sources" reads to a model as permission to gather.

    The measured cost of gathering on this lane was a turn that spent 34 tool
    calls before answering anything, so the economy rules are restated at the
    point the wider menu is introduced rather than left to be inferred from a
    section several thousand tokens earlier.
    """

    prompt = nexus_system_prompt(LabsTurnMode.DEEP_DIVE)

    assert "Deep dive widens the sources, not the budget" in prompt
    assert "Do not sweep the scan to see what is in it" in prompt
    assert "One source search and one component read" in prompt


@pytest.mark.parametrize("mode", list(LabsTurnMode))
def test_every_mode_keeps_the_economy_rules(mode: LabsTurnMode) -> None:
    # Deep dive means "more sources", never "read everything". The rules that
    # took one measured question from 34 tool calls to 4 are invariant.
    prompt = nexus_system_prompt(mode)

    assert "Use the smallest evidence path that can answer the question" in prompt
    assert "one to three evidence calls" in prompt
    assert "three to five" in prompt
    assert "Stop as soon as the answer is adequately supported" in prompt
    assert "Never respond to an oversized full-document result" in prompt
    assert "single `sections` call" in prompt
    assert "More available evidence is not a reason to retrieve it" in prompt


@pytest.mark.parametrize("mode", list(LabsTurnMode))
def test_every_mode_keeps_the_absolute_absence_rule(mode: LabsTurnMode) -> None:
    prompt = nexus_system_prompt(mode)

    assert "may never state that something does not exist" in prompt
    assert "I did not find X" in prompt
    assert "It is never a fact about the client." in prompt


def test_chat_treats_a_documentary_absence_as_a_summary_gap_and_offers_deep_dive() -> None:
    """The measured failure this half of the change exists to answer.

    Asked which orders go through a particular partner, the agent searched the
    generated documentation, found no filter, and published "I did not find any
    filter ... confirm with the functional owner". The scan answers it outright
    -- the condition is a literal value tested in one component's parameters --
    and none of those terms appears in the documentation, because summarising
    them away is what documentation IS.

    The old text made that reply compliant. What it lacked was the right next
    step, so the agent handed the work back to the client.
    """

    prompt = nexus_system_prompt(LabsTurnMode.CHAT)

    assert "The indexed documentation is a SUMMARY of a scan" in prompt
    assert "The scanned source behind it is not in this turn's tool menu" in prompt
    assert (
        'So "the documentation does not record it" is a statement about the '
        "summary, and never about the system." in prompt
    )
    assert "do not close on the absence and refer the user to the functional owner" in prompt
    assert "the scanned source behind it may well carry it" in prompt
    assert "point at Deep Dive" in prompt
    # The escalation must be stated as something the USER does. The looser
    # "offer to continue in Deep Dive" was rendered by the model as "say so and
    # I'll take it into Deep Dive" -- a promise it cannot keep, since it cannot
    # switch its own mode. A reader then waits for a handoff that never comes,
    # and reads the silence as a broken toggle.
    assert "only the user can turn it on" in prompt
    assert "You cannot switch mode" in prompt
    assert "turn on Deep Dive and ask me this again" in prompt


def test_deep_dive_must_search_the_source_before_reporting_an_absence() -> None:
    # The escalation chat mode offers is already spent here, so absence has to
    # be earned against the source before it may be reported at all.
    prompt = nexus_system_prompt(LabsTurnMode.DEEP_DIVE)

    assert "Deep dive is on, so the scanned source is already available to you" in prompt
    assert "search the source for it" in prompt
    assert "reporting on a search you chose not to run" in prompt
    # And the offer that belongs to the other mode is absent: offering a mode
    # the user is already in is the kind of line that makes an answer look
    # automated.
    assert "point at Deep Dive" not in prompt
    assert "only the user can turn it on" not in prompt


def test_chat_does_not_name_tools_it_cannot_call() -> None:
    # A named tool that is not in the menu is a guaranteed failed call and a
    # model that distrusts the rest of the section.
    prompt = nexus_system_prompt(LabsTurnMode.CHAT)

    assert "search_source" not in prompt
    assert "read_component" not in prompt


# --- The workspace carries the mode to the model ---------------------------


async def _config_for(mode: LabsTurnMode) -> tuple[dict[str, Any], str]:
    """Write the workspace for a turn in `mode` and read back what it produced.

    Returns the parsed `opencode.json` and the projected `SYSTEM_PROMPT.md`.
    """

    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token="labs-deep-dive-token",
            mode=mode,
        )
    )
    return (
        json.loads((workspace / "opencode.json").read_text(encoding="utf-8")),
        (workspace / ".nexus" / "SYSTEM_PROMPT.md").read_text(encoding="utf-8"),
    )


@pytest.mark.usefixtures("workspace_roots")
@pytest.mark.parametrize("mode", list(LabsTurnMode))
async def test_both_roles_are_written_whatever_the_turn_s_mode_is(
    mode: LabsTurnMode,
) -> None:
    """The fix, stated as an invariant of the file rather than of a turn.

    This test replaces one that asserted the opposite -- that the `nexus`
    agent's prompt FOLLOWED the turn's mode. That was true of the file and false
    of the session: OpenCode resolves a session's agent once, at create, and
    caches its MCP catalogue with it, so rewriting one agent's prompt per turn
    reached a session that had already decided what it was.

    Both definitions now exist in every write, so the mode can be applied by
    naming one of them in the message body -- the only field honoured per turn.
    """

    config, _projected = await _config_for(mode)
    agents = config["agent"]

    assert agents[_AGENT_NAME]["prompt"] == nexus_system_prompt(LabsTurnMode.CHAT)
    assert agents[_DEEP_DIVE_AGENT_NAME]["prompt"] == nexus_system_prompt(
        LabsTurnMode.DEEP_DIVE
    )
    # Both are selectable: a subagent cannot be named in a message body.
    assert agents[_AGENT_NAME]["mode"] == "primary"
    assert agents[_DEEP_DIVE_AGENT_NAME]["mode"] == "primary"


@pytest.mark.usefixtures("workspace_roots")
@pytest.mark.parametrize("mode", list(LabsTurnMode))
async def test_the_scan_tools_are_off_for_chat_and_on_for_deep_dive(
    mode: LabsTurnMode,
) -> None:
    """The one difference between the two tool maps, and it must not be empty.

    An empty `_SCAN_TOOLS` would make both maps identical and this assertion
    vacuous, so the names are asserted first. They are derived from the
    registry through the bridge's own projection, which is what keeps them from
    being a guess -- a `tools` entry naming a tool that does not exist is
    accepted silently and does nothing.
    """

    assert set(_SCAN_TOOLS) == {"nexus_search_source", "nexus_read_component"}

    config, _projected = await _config_for(mode)
    chat_tools = config["agent"][_AGENT_NAME]["tools"]
    deep_tools = config["agent"][_DEEP_DIVE_AGENT_NAME]["tools"]

    for tool in _SCAN_TOOLS:
        assert chat_tools[tool] is False, tool
        assert deep_tools[tool] is True, tool

    # And nothing else differs. The two agents share their permissions, their
    # step ceiling and every other tool, so a containment rule added to one is
    # added to both.
    assert config["agent"][_AGENT_NAME]["permission"] == (
        config["agent"][_DEEP_DIVE_AGENT_NAME]["permission"]
    )
    assert config["agent"][_AGENT_NAME]["maxSteps"] == (
        config["agent"][_DEEP_DIVE_AGENT_NAME]["maxSteps"]
    )
    assert {
        name: enabled
        for name, enabled in chat_tools.items()
        if name not in _SCAN_TOOLS
    } == {
        name: enabled
        for name, enabled in deep_tools.items()
        if name not in _SCAN_TOOLS
    }


def test_the_mcp_tool_names_are_derived_from_the_bridge_s_own_projection() -> None:
    # The bridge projects `semantic_name` and OpenCode namespaces the result
    # under the MCP server key. Both halves are read rather than written down,
    # so a rename on either side moves the agent tool map with it.
    assert set(deep_dive_tool_names()) == {"search_source", "read_component"}
    assert set(_SCAN_TOOLS) == {
        f"nexus_{name}" for name in deep_dive_tool_names()
    }


@pytest.mark.usefixtures("workspace_roots")
@pytest.mark.parametrize("mode", list(LabsTurnMode))
async def test_the_user_is_shown_the_role_the_agent_that_runs_was_given(
    mode: LabsTurnMode,
) -> None:
    """The projected copy has to track the agent, not just the mode.

    `.nexus/SYSTEM_PROMPT.md` exists so a user can see what their assistant was
    actually told. Now that there are two agent definitions, "what it was told"
    is the prompt of the one this turn selects -- and `labs_agent_for` is the
    single owner of that choice, so the two cannot drift apart.
    """

    config, projected = await _config_for(mode)

    assert projected == config["agent"][labs_agent_for(mode)]["prompt"]


def test_the_mode_selects_the_agent_and_chat_stays_the_default() -> None:
    assert labs_agent_for(LabsTurnMode.CHAT) == _AGENT_NAME
    assert labs_agent_for(LabsTurnMode.DEEP_DIVE) == _DEEP_DIVE_AGENT_NAME
    # Distinct names, or the per-message override selects the same definition in
    # both modes and the whole mechanism is a no-op.
    assert _AGENT_NAME != _DEEP_DIVE_AGENT_NAME


def test_the_workspace_a_user_reads_at_rest_shows_the_chat_role() -> None:
    # The read-only workspace endpoint has no turn in hand: it is describing the
    # conversation at rest, which is a chat conversation.
    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
    )
    prompt_file = next(file for file in files if file.path.endswith("SYSTEM_PROMPT.md"))

    assert prompt_file.content == _NEXUS_SYSTEM_PROMPT


# --- End to end: the request's mode reaches the scope the bridge rebuilds ---


@dataclass
class _FakeConversation:
    id: int
    opencode_session_id: str | None = None
    messages: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class _FakeSession:
    id: str
    directory: str


def _fake_client(seen: dict[str, Any], *, echo_agent: bool = True) -> type:
    """An `OpenCodeClient` stand-in that records which agent each call named.

    `echo_agent` controls whether the reply carries `info.agent`. Both cases
    matter: the field is optional in the protocol, and it is the ONLY evidence
    that the agent the turn asked for is the agent that ran.
    """

    class _Client:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def create_session(self, *, directory: str, agent: str) -> _FakeSession:
            seen["session_agent"] = agent
            return _FakeSession(id="ses_deep_dive", directory=directory)

        async def stream_bus_events(self, *, session_id: str, directory: str):
            del directory
            yield {"type": "session.idle", "properties": {"sessionID": session_id}}

        async def send_message(self, session_id: str, message: str, **kwargs: Any):
            from src.labs.opencode_client import OpenCodeReply

            del session_id, message
            seen["message_agent"] = kwargs.get("agent")
            info = {"agent": seen["message_agent"]} if echo_agent else {}
            return OpenCodeReply(
                text="An answer.", finish="stop", error=None, info=info
            )

    return _Client


async def _run_turn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: LabsTurnMode,
    agent: str | None = None,
    echo_agent: bool = True,
) -> dict[str, Any]:
    """Drive one `execute_labs_turn` against fakes and report what it sent."""

    token_registry = LabsTokenRegistry(secret="deep-dive-e2e")
    seen: dict[str, Any] = {}

    async def _none(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_texts(*_args: Any, **_kwargs: Any) -> dict[int, str]:
        return {}

    async def _workspace(**kwargs: Any) -> str:
        seen["mode"] = kwargs.get("mode")
        seen["token"] = kwargs.get("mcp_token")
        return "/tmp/labs/conversation-9301"

    monkeypatch.setattr(labs_runtime, "labs_token_registry", token_registry)
    monkeypatch.setattr(
        labs_runtime, "OpenCodeClient", _fake_client(seen, echo_agent=echo_agent)
    )
    monkeypatch.setattr(labs_runtime, "compile_guidance", _none)
    monkeypatch.setattr(labs_runtime, "compile_project_material", _none)
    monkeypatch.setattr(labs_runtime, "load_material_texts", _no_texts)
    monkeypatch.setattr(labs_runtime, "ensure_labs_workspace", _workspace)
    # The lane caches one binding per conversation for the life of the process.
    monkeypatch.setattr(labs_runtime, "_CONVERSATION_BINDINGS", {})

    async def _emit(_event_type: str, _payload: dict[str, Any]) -> None:
        return None

    kwargs: dict[str, Any] = {}
    if agent is not None:
        kwargs["agent"] = agent
    await labs_runtime.execute_labs_turn(
        db=None,
        conversation=_FakeConversation(CONVERSATION_ID),
        user_id=7,
        user_groups=["developer"],
        project_scope=[PROJECT],
        permissions=frozenset({"chat:use"}),
        access_token=None,
        message="Which orders go through FMD?",
        emit=_emit,
        mode=mode,
        **kwargs,
    )
    seen["registry"] = token_registry
    return seen


async def test_the_requested_mode_reaches_the_scope_and_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One turn, from `execute_labs_turn` to both places the mode has to land.

    The scope, because it is what the MCP bridge enforces the mode against on
    every `tools/call`; and the workspace, because that is where the role
    explaining the mode is written.
    """

    seen = await _run_turn(monkeypatch, mode=LabsTurnMode.DEEP_DIVE)

    assert seen["mode"] is LabsTurnMode.DEEP_DIVE
    rebuilt = seen["registry"].resolve(seen["token"])
    assert rebuilt is not None
    assert rebuilt.mode is LabsTurnMode.DEEP_DIVE


@pytest.mark.parametrize("mode", list(LabsTurnMode))
async def test_the_turn_names_the_mode_s_agent_in_the_message_body(
    mode: LabsTurnMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the mode actually reaches the model now.

    The session is still CREATED on the default agent -- it outlives the turn,
    so its creation agent must not be decided by whichever mode the thread
    opened in -- and the per-message field overrides it. That override is the
    only part of a turn that OpenCode resolves per message rather than per
    session.
    """

    seen = await _run_turn(monkeypatch, mode=mode)

    assert seen["session_agent"] == _AGENT_NAME
    assert seen["message_agent"] == labs_agent_for(mode)


async def test_an_explicit_plan_agent_outranks_the_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `/plan` is a typed command. A toggle left on from a previous question must
    # not silently replace what the user asked for on this one.
    seen = await _run_turn(
        monkeypatch, mode=LabsTurnMode.DEEP_DIVE, agent=labs_runtime.LABS_PLAN_AGENT
    )

    assert seen["message_agent"] == labs_runtime.LABS_PLAN_AGENT


@pytest.mark.parametrize("echo_agent", [True, False])
async def test_the_turn_logs_which_agent_the_server_says_ran(
    echo_agent: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`info.agent` is the server's confirmation, and it belongs in the log.

    The whole bug class here was "we intended one mode and could not see that
    another one happened" -- a cached tool menu and a session-resolved agent
    both failed silently. A requested/actual pair on the `labs.turn` line is
    what makes the next such divergence visible instead of inferred from what
    the answer said.
    """

    with caplog.at_level("INFO", logger="src.labs.runtime"):
        await _run_turn(
            monkeypatch, mode=LabsTurnMode.DEEP_DIVE, echo_agent=echo_agent
        )

    line = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("labs.turn ")
    )

    assert f"agent={_DEEP_DIVE_AGENT_NAME} " in line
    assert "mode=deep_dive" in line
    # Absent rather than assumed: a server that stops echoing the field must
    # read as unknown, not as confirmation of what we asked for.
    expected = _DEEP_DIVE_AGENT_NAME if echo_agent else "-"
    assert f"ran_agent={expected} " in line


def test_deep_dive_routes_by_question_shape_not_by_a_fixed_source_order() -> None:
    """A component-level question must start at the source, not at the prose.

    Measured on conversation 658. Asked "Check cMessagingEndpoint_15 in route
    processor_shipping_confirmation_map_jde_to_esb_p010 and give me the SQL",
    with `mode=deep_dive` and both scan capabilities verified present in the
    turn's registry, the agent made five `search_documentation` calls, one
    `search_within_document`, one `query_project_metadata`, ZERO scan calls, and
    reported that it could not retrieve the SQL.

    It was obeying the prompt. The text said "Start on the documentation path,
    reach for the scan for the specific parameter ... the documentation does not
    carry", which makes the scan a fallback after a failed search. For a question
    naming a component that search was never going to succeed, so the fallback
    framing bought a wasted round and an absence report on the one turn where the
    answer was a single `read_component` away.
    """

    prompt = nexus_system_prompt(LabsTurnMode.DEEP_DIVE)

    assert "decided by the QUESTION, not by a fixed order" in prompt
    assert "Go to the SOURCE FIRST" in prompt
    assert "Go to the DOCUMENTATION first" in prompt
    # The exact shape that failed, named so a model cannot miss the mapping.
    assert "cMessagingEndpoint_15" in prompt
    assert "answered by `read_component`, not by searching prose" in prompt
    # The fallback framing that caused it must not come back.
    assert "Start on the documentation path" not in prompt
    # And the economy rule survives: source-first is not permission to sweep.
    assert "Do not sweep the scan to see what is in it" in prompt


def test_chat_mode_gets_no_source_routing_rules() -> None:
    # Chat cannot reach the scan at all, so a rule about when to prefer it would
    # describe a choice the turn does not have.
    prompt = nexus_system_prompt(LabsTurnMode.CHAT)

    assert "Go to the SOURCE FIRST" not in prompt
    assert "read_component" not in prompt
