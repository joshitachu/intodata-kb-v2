"""Deep dive is a MODE of this lane, not a second lane.

Same agent, same thread, same model. The only difference is how wide the
evidence menu is: chat sees the generated documentation and the runtime, deep
dive additionally sees the raw scan -- which holds the component parameters,
expressions, conditions and SQL that documentation summarises away.

Three things have to hold for that to be true, and each has its own section
below:

* The mode is carried per TURN, from the request through to the scope the MCP
  bridge rebuilds. Per turn, because the same thread asks an ordinary question
  and then a deep one; a mode stored on the conversation would make the second
  answer depend on the first turn's toggle.
* The two scan capabilities are advertised in deep dive and in nothing else,
  including under an operator's `LABS_CAPABILITY_ALLOWLIST="*"`.
* The system prompt says what the mode means -- in deep dive, that the scan is
  the authority; in chat, that an absence in the documentation is an absence in
  a summary and Deep Dive is the next step, rather than a dead end with a
  referral to the functional owner attached.

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

from src.labs import runtime as labs_runtime
from src.labs.contracts import (
    LabsTokenRegistry,
    LabsTurnMode,
    LabsTurnModeRegistry,
    LabsTurnScope,
    labs_turn_modes,
)
from src.labs.mcp_bridge import (
    _DEEP_DIVE_CAPABILITY_IDS,
    _DEFAULT_ALLOWLIST,
    _tool_name_map,
    _visible_capabilities,
)
from src.labs.workspace import (
    _NEXUS_SYSTEM_PROMPT,
    ensure_labs_workspace,
    nexus_system_prompt,
    project_workspace_files,
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


def _runtime_with_scan() -> SimpleNamespace:
    capabilities = [
        _scan_capability(SOURCE_SEARCH, "search_source"),
        _scan_capability(SOURCE_READ_COMPONENT, "read_component"),
    ]
    return SimpleNamespace(
        registry=SimpleNamespace(list=lambda enabled_only=True: capabilities)
    )


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
    """

    labs_turn_modes.end_turn(CONVERSATION_ID)
    yield
    labs_turn_modes.end_turn(CONVERSATION_ID)


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


# --- The bridge gates the scan on the mode ---------------------------------


def test_chat_mode_advertises_neither_scan_capability() -> None:
    # The whole reason for a mode. An ungated scan tool makes every routine
    # question pay for a source search it never needed.
    assert _visible_ids(LabsTurnMode.CHAT) == set()


def test_deep_dive_mode_advertises_both_scan_capabilities() -> None:
    assert _visible_ids(LabsTurnMode.DEEP_DIVE) == {
        SOURCE_SEARCH,
        SOURCE_READ_COMPONENT,
    }


def test_the_scan_is_not_in_the_chat_allowlist() -> None:
    # Held apart from `_DEFAULT_ALLOWLIST` rather than added to it: that set is
    # the chat menu, and it is separately asserted to be a subset of the live
    # registry, which these ids only become once the scan provider lands.
    assert _DEEP_DIVE_CAPABILITY_IDS == {SOURCE_SEARCH, SOURCE_READ_COMPONENT}
    assert not (_DEEP_DIVE_CAPABILITY_IDS & _DEFAULT_ALLOWLIST)


def test_the_wildcard_override_cannot_put_the_scan_into_ordinary_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same treatment topology already gets, for the same reason.

    `LABS_CAPABILITY_ALLOWLIST="*"` means "advertise everything the tier and
    permission filters allow". An operator setting it to debug one thing must
    not thereby put the scan into every routine question in the estate.
    """

    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", "*")

    assert _visible_ids(LabsTurnMode.CHAT) == set()
    assert _visible_ids(LabsTurnMode.DEEP_DIVE) == {
        SOURCE_SEARCH,
        SOURCE_READ_COMPONENT,
    }


def test_a_narrow_operator_allowlist_still_admits_the_scan_in_deep_dive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mode widens the configured allowlist; it never replaces it. An
    # operator who narrowed the menu has not thereby switched deep dive off.
    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", "rag.docs.search_evidence")

    assert _visible_ids(LabsTurnMode.CHAT) == set()
    assert _visible_ids(LabsTurnMode.DEEP_DIVE) == {
        SOURCE_SEARCH,
        SOURCE_READ_COMPONENT,
    }


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


def test_the_live_turn_mode_wins_over_the_mode_baked_into_the_token() -> None:
    """The defect a token-only mode would have shipped.

    The MCP bearer is minted once per CONVERSATION and cached by OpenCode for
    eight hours; `_resolve_binding` reuses a token that still verifies rather
    than re-minting it. So a thread whose first turn was chat would be stuck in
    chat for the life of the token, silently, however often the user toggled.
    """

    labs_turn_modes.start_turn(CONVERSATION_ID, LabsTurnMode.DEEP_DIVE)

    # The scope still says chat -- it is what the conversation's token was
    # minted under -- and the live turn is what decides.
    assert _visible_ids(LabsTurnMode.CHAT) == {SOURCE_SEARCH, SOURCE_READ_COMPONENT}


def test_the_token_mode_is_the_floor_outside_a_turn() -> None:
    # No live turn is registered (the autouse fixture guarantees it), so the
    # only statement of breadth is the token's own, and it is honoured.
    assert _visible_ids(LabsTurnMode.DEEP_DIVE) == {
        SOURCE_SEARCH,
        SOURCE_READ_COMPONENT,
    }
    assert _visible_ids(LabsTurnMode.CHAT) == set()


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
    assert "offer to continue in Deep Dive" in prompt


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
    assert "offer to continue in Deep Dive" not in prompt


def test_chat_does_not_name_tools_it_cannot_call() -> None:
    # A named tool that is not in the menu is a guaranteed failed call and a
    # model that distrusts the rest of the section.
    prompt = nexus_system_prompt(LabsTurnMode.CHAT)

    assert "search_source" not in prompt
    assert "read_component" not in prompt


# --- The workspace carries the mode to the model ---------------------------


@pytest.mark.usefixtures("workspace_roots")
async def test_a_deep_dive_turn_is_given_the_deep_dive_role() -> None:
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token="labs-deep-dive-token",
            mode=LabsTurnMode.DEEP_DIVE,
        )
    )
    config = json.loads((workspace / "opencode.json").read_text(encoding="utf-8"))
    projected = (workspace / ".nexus" / "SYSTEM_PROMPT.md").read_text(encoding="utf-8")

    assert config["agent"]["nexus"]["prompt"] == nexus_system_prompt(
        LabsTurnMode.DEEP_DIVE
    )
    # The two copies are only safe while they are provably the same text: one
    # reaches the model, the other is what the user is shown they were told.
    assert projected == config["agent"]["nexus"]["prompt"]


@pytest.mark.usefixtures("workspace_roots")
async def test_the_role_follows_the_turn_rather_than_the_conversation() -> None:
    """`opencode.json` is per conversation; the mode is per turn.

    This is what makes the second expressible in the first: the workspace is
    rewritten on every turn, so the same conversation can run a deep turn and
    then an ordinary one without the second inheriting the first's role.
    """

    async def _turn(mode: LabsTurnMode) -> str:
        workspace = Path(
            await ensure_labs_workspace(
                conversation_id=CONVERSATION_ID,
                project_id=PROJECT,
                mcp_token="labs-deep-dive-token",
                mode=mode,
            )
        )
        config = json.loads((workspace / "opencode.json").read_text(encoding="utf-8"))
        return config["agent"]["nexus"]["prompt"]

    assert await _turn(LabsTurnMode.DEEP_DIVE) == nexus_system_prompt(
        LabsTurnMode.DEEP_DIVE
    )
    assert await _turn(LabsTurnMode.CHAT) == _NEXUS_SYSTEM_PROMPT


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


class _FakeOpenCodeClient:
    """The minimum of `OpenCodeClient` one labs turn touches."""

    async def __aenter__(self) -> _FakeOpenCodeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def create_session(self, *, directory: str, agent: str) -> _FakeSession:
        del agent
        return _FakeSession(id="ses_deep_dive", directory=directory)

    async def stream_bus_events(self, *, session_id: str, directory: str):
        del directory
        yield {"type": "session.idle", "properties": {"sessionID": session_id}}

    async def send_message(self, session_id: str, message: str, **_kwargs: Any):
        from src.labs.opencode_client import OpenCodeReply

        del session_id, message
        return OpenCodeReply(text="An answer.", finish="stop", error=None)


async def test_the_requested_mode_reaches_the_scope_and_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One turn, from `execute_labs_turn` to both places the mode has to land.

    The scope, because it is what the MCP bridge rebuilds the tool menu from in
    a different request; and the workspace, because that is where the role
    explaining the mode is written.
    """

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
    monkeypatch.setattr(labs_runtime, "OpenCodeClient", _FakeOpenCodeClient)
    monkeypatch.setattr(labs_runtime, "compile_guidance", _none)
    monkeypatch.setattr(labs_runtime, "compile_project_material", _none)
    monkeypatch.setattr(labs_runtime, "load_material_texts", _no_texts)
    monkeypatch.setattr(labs_runtime, "ensure_labs_workspace", _workspace)
    # The lane caches one binding per conversation for the life of the process.
    monkeypatch.setattr(labs_runtime, "_CONVERSATION_BINDINGS", {})

    async def _emit(_event_type: str, _payload: dict[str, Any]) -> None:
        return None

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
        mode=LabsTurnMode.DEEP_DIVE,
    )

    assert seen["mode"] is LabsTurnMode.DEEP_DIVE
    rebuilt = token_registry.resolve(seen["token"])
    assert rebuilt is not None
    assert rebuilt.mode is LabsTurnMode.DEEP_DIVE
