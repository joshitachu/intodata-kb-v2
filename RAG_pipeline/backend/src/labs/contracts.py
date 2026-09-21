"""Shared contracts for the labs OpenCode chat lane.

Every module in `src/labs/` codes against this file. It is deliberately small:
the point of the labs lane is that it reuses the production capability executor
rather than reimplementing anything behind it.

The turn token is the RBAC boundary. OpenCode never states who it is acting for --
it presents a bearer token that Nexus minted for one turn, and the MCP bridge
rebuilds the server-owned `CapabilityExecutionContext` from that token alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Final

# Mounted under the `/api/v1` prefix in `src/api/main.py`.
LABS_MCP_ROUTE: Final = "/labs/mcp"

# A labs token is minted once per conversation, not once per turn: it is baked
# into the workspace's `opencode.json` as an MCP header, and OpenCode reads that
# config when it connects the MCP server rather than on every prompt. So the
# token has to outlive a single turn. It still carries the full server-owned
# scope, so RBAC is unchanged -- only the lifetime is longer.
LABS_TOKEN_TTL_SECONDS: Final = 8 * 60 * 60


@dataclass(frozen=True, slots=True)
class LabsTurnScope:
    """Server-owned scope for one labs turn.

    Mirrors the inputs of `build_production_agent_turn_runtime` so the labs lane
    and `/chat` resolve to byte-identical policy.
    """

    user_id: int
    conversation_id: int
    user_groups: list[str]
    project_scope: list[str] | None
    permissions: frozenset[str]
    access_token: str | None
    workspace_dir: str
    #: The tool-call allowance for ONE turn, not for the conversation.
    #:
    #: The token carrying it is minted once per conversation and cached by
    #: OpenCode for 8 hours, so the number has to be a constant allowance that
    #: the server re-applies each turn rather than a live remaining count. The
    #: live count lives in `labs_turn_budget`, which resets at the turn
    #: boundary. Read as "remaining" only at the start of a turn, which is the
    #: one moment the two agree.
    remaining_tool_calls: int = 40


class LabsTokenRegistry:
    """Stateless, signed turn tokens: `<base64url(payload)>.<hmac>`.

    Deliberately holds nothing in memory. The first version was an in-process
    dict, which broke in two ways that both bite in practice:

      * every backend restart invalidated every live token, while OpenCode kept
        using the bearer it had already cached for that workspace -- so all MCP
        tool calls returned 403 and the agent silently fell back to reading
        files, which looks like a model regression and is not one;
      * a second uvicorn worker would never see a token minted by the first.

    The scope is carried inside the token and verified by HMAC, so any process
    holding the signing key can resolve it and a restart changes nothing. The
    token is still server-minted and unforgeable -- the client cannot widen its
    own scope, because doing so invalidates the signature.
    """

    def __init__(self, secret: str | None = None) -> None:
        self._secret = secret

    def _signing_key(self) -> bytes:
        if self._secret is not None:
            return self._secret.encode()
        # Imported lazily: `src.config` pulls in a large settings tree, and this
        # module is imported by the contracts layer before the app is built.
        from src.config import settings

        return f"labs-mcp:{settings.jwt_secret_key}".encode()

    def _sign(self, payload: bytes) -> str:
        digest = hmac.new(self._signing_key(), payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def mint(self, scope: LabsTurnScope, *, ttl_seconds: float | None = None) -> str:
        body = {
            "u": scope.user_id,
            "c": scope.conversation_id,
            "g": list(scope.user_groups),
            "p": list(scope.project_scope) if scope.project_scope is not None else None,
            "m": sorted(scope.permissions),
            "a": scope.access_token,
            "w": scope.workspace_dir,
            "t": scope.remaining_tool_calls,
            "exp": time.time() + (ttl_seconds or LABS_TOKEN_TTL_SECONDS),
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(body, separators=(",", ":")).encode()
        ).rstrip(b"=")
        return f"{payload.decode()}.{self._sign(payload)}"

    def refresh(self, token: str, scope: LabsTurnScope) -> bool:
        """Kept for API compatibility; a signed token needs no server-side update.

        Returning True means "this token is still usable as-is", which is true
        whenever it verifies. The caller only re-mints when this says False.
        """

        return self.resolve(token) is not None

    def resolve(self, token: str) -> LabsTurnScope | None:
        payload_part, _, signature = token.partition(".")
        if not payload_part or not signature:
            return None
        payload = payload_part.encode()
        # Constant-time compare: this is the only thing standing between a
        # caller and a scope of their choosing.
        if not hmac.compare_digest(self._sign(payload), signature):
            return None
        try:
            padding = b"=" * (-len(payload) % 4)
            body = json.loads(base64.urlsafe_b64decode(payload + padding))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict) or float(body.get("exp", 0)) <= time.time():
            return None
        try:
            return LabsTurnScope(
                user_id=int(body["u"]),
                conversation_id=int(body["c"]),
                user_groups=list(body.get("g") or []),
                project_scope=(
                    list(body["p"]) if body.get("p") is not None else None
                ),
                permissions=frozenset(body.get("m") or []),
                access_token=body.get("a"),
                workspace_dir=str(body["w"]),
                remaining_tool_calls=int(body.get("t", 40)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def revoke(self, token: str) -> None:
        """No-op: a signed token carries its own expiry and is not stored.

        Revoking one before it expires would need a deny-list, which is only
        worth building when these tokens outlive a conversation.
        """


labs_token_registry: Final = LabsTokenRegistry()


@dataclass(frozen=True, slots=True)
class LabsToolCallRecord:
    """One capability invocation made by the OpenCode runtime during a turn."""

    capability_id: str
    succeeded: bool
    summary: str | None = None
    evidence_refs: list[dict] = field(default_factory=list)
    error_code: str | None = None
    #: The MCP tool name the model actually called, e.g. `read_topology_structure`.
    #:
    #: Recorded verbatim rather than re-derived from `capability_id`, because the
    #: two are not the same string: the bridge projects `semantic_name` when a
    #: capability has one, so `topology.landscape.read_structure` is called as
    #: `read_topology_structure`. Without this, the late `tool.completed` frame
    #: and the live `tool.running` frame for one call name it differently, and a
    #: client that collapses the two into one row cannot tell they are the same
    #: call -- which is exactly how one turn came to report 81 "tool calls" for
    #: 40 of them. Optional so a record constructed outside the bridge (tests,
    #: future callers) is still valid.
    tool_name: str | None = None


# --- Turn claims -----------------------------------------------------------

# Bound, for the same reason the evidence ledger in `mcp_bridge.py` is bound: a
# claim is removed by the turn that bound it, so the map is normally one entry
# per in-flight turn, and a leak must not be able to grow without limit.
_MAX_TRACKED_CLAIMS: Final = 64


@dataclass(frozen=True, slots=True)
class LabsTurnClaim:
    """The Conversation State lock one labs turn holds while it runs.

    These four values are exactly what `CapabilityPolicyEngine._validate_risk`
    requires before a `requires_turn_claim` capability may execute.
    """

    message_id: int
    state_version: int
    claim_id: int
    claim_token: str


class LabsTurnBudget:
    """How many capability calls the turn in flight has already made.

    Per TURN, keyed by conversation. That is the whole point of this class, and
    it is a correction: the count used to live in the MCP bridge keyed by bearer
    token, and the bearer is minted once per CONVERSATION with an 8-hour TTL. So
    a 40-call allowance was spent across every turn the conversation ever made --
    one observed thread reached 38 of 40 across seven turns in three minutes,
    after which every further turn in that conversation was crippled, with
    nothing told to the user or the model.

    The turn boundary is `src/labs/runtime.py::labs_turn_claim`, which calls
    `start_turn` unconditionally on entry and `release` (which calls `end_turn`)
    unconditionally on exit. `LabsTurnClaimRegistry.bind` also calls `start_turn`,
    which is redundant and kept: it is the reset for any future caller that binds
    a claim without going through that context manager.

    Unconditional, and that is the correction. Driving the reset from `bind`
    alone tied it to winning the claim race: a turn that lost bound nothing, so
    it reset nothing, and two consecutive losing turns shared one count -- the
    second starting with the first's calls already spent. How many tool calls a
    turn may make has nothing to do with whether it holds the conversation lock.
    """

    def __init__(self, max_entries: int = _MAX_TRACKED_CLAIMS) -> None:
        self._used: dict[int, int] = {}
        self._max_entries = max_entries

    def start_turn(self, conversation_id: int) -> None:
        """Begin a turn's allowance. Idempotent within one turn boundary."""

        if (
            conversation_id not in self._used
            and len(self._used) >= self._max_entries
        ):
            # Same argument as the claim registry's eviction: every started turn
            # ends in a `finally`, so an entry old enough to be evicted belongs
            # to a turn whose in-process state is already gone.
            self._used.pop(next(iter(self._used)), None)
        self._used[conversation_id] = 0

    def end_turn(self, conversation_id: int) -> None:
        self._used.pop(conversation_id, None)

    def consume(self, conversation_id: int) -> int:
        """Count one capability call and return the new used-count."""

        used = self._used.get(conversation_id, 0) + 1
        self._used[conversation_id] = used
        return used

    def used(self, conversation_id: int) -> int:
        return self._used.get(conversation_id, 0)

    def remaining(self, conversation_id: int, allowance: int) -> int:
        return max(0, allowance - self.used(conversation_id))


labs_turn_budget: Final = LabsTurnBudget()


class LabsTurnClaimRegistry:
    """In-process `conversation_id -> LabsTurnClaim` for the turn in flight.

    Why this is in-process while `LabsTokenRegistry` deliberately is not: the
    two have opposite lifetimes and opposite failure modes.

    The MCP bearer token is minted once per conversation and baked into the
    workspace's `opencode.json`, which OpenCode reads when it connects and then
    caches. It therefore has to survive a backend restart -- when it did not,
    every tool call came back 403 against a bearer the client had already
    cached. That is why it is HMAC-signed and stateless.

    A claim is per turn: it pins one `message_id` and one `expected_version`,
    and the turn that owns it cannot outlive the process either. Reviving one
    after a restart would only hand a dead turn a live lock over a Conversation
    State whose version has moved on, so nothing here is persisted or signed.
    The claim never leaves the server -- the MCP bridge reads it directly and
    the token registry keeps carrying the scope.
    """

    def __init__(
        self,
        max_entries: int = _MAX_TRACKED_CLAIMS,
        budget: LabsTurnBudget | None = None,
    ) -> None:
        self._claims: dict[int, LabsTurnClaim] = {}
        self._max_entries = max_entries
        # Injected so a test can drive one registry and inspect its own budget;
        # production uses the module-level singleton the MCP bridge reads.
        self._budget = budget if budget is not None else labs_turn_budget

    def bind(self, conversation_id: int, claim: LabsTurnClaim) -> None:
        if (
            conversation_id not in self._claims
            and len(self._claims) >= self._max_entries
        ):
            # Every bound claim is released in a `finally`, so an entry that
            # survives long enough to be evicted belongs to a turn whose
            # in-process state is already gone.
            self._claims.pop(next(iter(self._claims)), None)
        self._claims[conversation_id] = claim
        # Bind/release are the turn boundary -- `labs_turn_claim` calls them
        # around exactly one turn -- so they are also where the per-turn tool
        # allowance is reset. Driving the budget from here rather than from a
        # second hook keeps "when does a turn start" a single fact.
        self._budget.start_turn(conversation_id)

    def get(self, conversation_id: int) -> LabsTurnClaim | None:
        return self._claims.get(conversation_id)

    def release(self, conversation_id: int) -> None:
        self._claims.pop(conversation_id, None)
        self._budget.end_turn(conversation_id)


labs_turn_claims: Final = LabsTurnClaimRegistry()


__all__ = [
    "LABS_MCP_ROUTE",
    "LABS_TOKEN_TTL_SECONDS",
    "LabsTokenRegistry",
    "LabsToolCallRecord",
    "LabsTurnBudget",
    "LabsTurnClaim",
    "LabsTurnClaimRegistry",
    "LabsTurnScope",
    "labs_token_registry",
    "labs_turn_budget",
    "labs_turn_claims",
]
