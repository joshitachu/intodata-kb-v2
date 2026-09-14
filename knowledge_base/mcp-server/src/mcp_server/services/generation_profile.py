"""Versioned, immutable generation contracts; NULL always means legacy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LANDSCAPE_CONTRACT = "talend-landscape-v2"
ENTERPRISE_CONTRACT = "talend-enterprise-v1"
GOAL_DRIVEN_CONTRACT = "integration-intelligence-v1"
#: The one agentic lane. Nexus stages a readable workspace plus the
#: instructions for analysing it, hands both to a coding agent, and publishes
#: the Markdown the agent writes. The three contracts above are its
#: predecessors and are removed once this one is proven.
AGENT_WORKSPACE_CONTRACT = "agent-workspace-v1"
#: The investigation method revision an enterprise run is pinned to. Changing
#: the staged guides without bumping this is a contract change; see
#: ``services/investigation_method.py``.
ENTERPRISE_METHOD_VERSION = "talend-method-1"


class LandscapeBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_requirements_per_task: int = Field(default=12, ge=1, le=100)
    max_evidence_bytes: int = Field(default=60_000, ge=1024, le=600_000)
    max_analysis_followups: int = Field(default=1, ge=0, le=3)
    max_analysis_tasks: int = Field(default=200, ge=1, le=2000)
    max_scope_evidence_bytes: int = Field(default=400_000, ge=24000, le=600_000)
    max_scope_units: int = Field(default=6, ge=1, le=20)
    analysis_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    max_analysis_session_tokens: int = Field(default=400_000, ge=1000, le=2_000_000)
    max_analysis_session_turns: int = Field(default=16, ge=1, le=100)
    analysis_total_timeout_seconds: int = Field(default=1800, ge=60, le=14_400)
    max_analysis_total_processed_tokens: int = Field(default=5_000_000, ge=1000, le=50_000_000)
    analysis_cost_dispatch_ceiling_usd: float = Field(default=10.0, ge=0.1, le=500.0)


class EnterpriseBudgets(BaseModel):
    """Resource controls for the enterprise phase sequence.

    Every ceiling here stops NEW work and produces an explicitly incomplete
    run. None of them may be used to shrink what a phase is allowed to read or
    to publish a first-N subset as complete (plan decision 7).
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    # One OpenCode session (one phase attempt over one scheduling scope).
    session_timeout_seconds: int = Field(default=1800, ge=60, le=14_400)
    session_max_turns: int = Field(default=200, ge=1, le=5000)
    session_max_processed_tokens: int = Field(default=6_000_000, ge=1000, le=200_000_000)
    # The whole run.
    total_timeout_seconds: int = Field(default=21_600, ge=60, le=172_800)
    total_processed_token_limit: int = Field(default=60_000_000, ge=1000, le=2_000_000_000)
    cost_dispatch_ceiling_usd: float = Field(default=75.0, ge=0.1, le=10_000.0)
    # Repair edits an existing artifact that failed named checks; regenerate
    # replaces an absent, truncated or agent-failure output.
    max_repair_attempts: int = Field(default=2, ge=0, le=6)
    max_regenerate_attempts: int = Field(default=1, ge=0, le=4)
    # Scheduling records: how many units one analysis session is asked to
    # account for, and how many sessions run concurrently. Neither limits what
    # a session may read.
    units_per_session: int = Field(default=12, ge=1, le=200)
    concurrency: int = Field(default=2, ge=1, le=8)
    # A related group of technical paths becomes a dossier when it touches at
    # least this many designs or carries an exact mapping table or error path.
    dossier_min_designs: int = Field(default=2, ge=1, le=100)


class GoalDrivenResources(BaseModel):
    """Run-level circuit breakers for a coherent investigation.

    These values stop and checkpoint an investigation.  They are deliberately
    not evidence, file, unit, key, or per-session quotas and therefore cannot
    silently shape the semantic scope of the work.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    wall_time_seconds: int = Field(default=21_600, ge=60, le=172_800)
    processed_token_ceiling: int = Field(default=60_000_000, ge=1000, le=2_000_000_000)
    cost_ceiling_usd: float = Field(default=75.0, ge=0.1, le=10_000.0)
    repeated_failure_limit: int = Field(default=2, ge=1, le=4)


class GoalDrivenSettings(BaseModel):
    """Domain inputs for the separate goal-driven pipeline."""

    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str = Field(
        default="Generate complete enterprise integration documentation",
        min_length=3,
        max_length=4000,
    )
    agent_provider: Literal["opencode", "codex", "claude-code", "hermes"] = "opencode"
    methodology_packs: list[str] = Field(default_factory=lambda: ["base-integration"])
    resources: GoalDrivenResources = Field(default_factory=GoalDrivenResources)


class AgentWorkspaceResources(BaseModel):
    """Whole-run circuit breakers. There is nothing else to tune.

    Deliberately absent, and not to be added back: per-job, per-group,
    per-document, per-mapping and per-session token budgets; unit counts per
    session; concurrency; a model-call ceiling. Those mechanisms split prior
    whole-estate runs into predetermined partitions and sessions. How much to
    read and how far to fan out is the agent's decision; these values only
    decide when the run stops.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    wall_time_seconds: int = Field(default=21_600, ge=60, le=172_800)
    processed_token_ceiling: int = Field(default=60_000_000, ge=1000, le=2_000_000_000)
    cost_ceiling_usd: float = Field(default=75.0, ge=0.1, le=10_000.0)
    #: A vanished provider session is a transport fault, not a result. The run
    #: restarts over the same workspace with a prose checkpoint summary rather
    #: than failing. Bounded so a genuinely broken provider still terminates.
    max_restarts: int = Field(default=2, ge=0, le=5)


class AgentWorkspaceSettings(BaseModel):
    """Domain inputs for the agentic lane: a goal, and who runs it."""

    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str = Field(
        default="Produce complete integration documentation for this estate",
        min_length=3,
        max_length=4000,
    )
    agent_provider: Literal["opencode", "codex", "claude-code", "fake"] = "opencode"
    #: What the run is accountable for. DECLARED, never inferred from the goal
    #: sentence: a whole-estate run must account for every class of parsed
    #: evidence and every meaningful integration, a focused run investigates
    #: what the goal reaches and may leave the rest alone, and the two need
    #: opposite instructions in all four bootstrap files.
    #:
    #: Defaults to ``whole_estate`` because the failure modes are not
    #: symmetric. Too much accountability costs effort; too little publishes an
    #: estate with parts silently out of scope, and a reader cannot tell from
    #: the result that anything was omitted. An earlier build inferred the mode
    #: by comparing the goal against a sentinel string, which classified this
    #: model's own default goal as focused -- so every default run would have
    #: quietly dropped the estate-wide rule.
    scope_mode: Literal["whole_estate", "focused"] = "whole_estate"
    resources: AgentWorkspaceResources = Field(default_factory=AgentWorkspaceResources)
    #: Advisory coverage note. Off by default; it observes, never blocks.
    quality_report: bool = False


class GenerationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: Literal[
        "talend-landscape-v2",
        "talend-enterprise-v1",
        "integration-intelligence-v1",
        "agent-workspace-v1",
    ] = LANDSCAPE_CONTRACT
    engine: Literal["opencode-cli"] = "opencode-cli"
    evidence_version: Literal[2] = 2
    requirement_version: Literal[2] = 2
    claim_version: Literal[2] = 2
    prompt_version: Literal[2] = 2
    analysis_version: Literal[2, 3] = 3
    budgets: LandscapeBudgets = Field(default_factory=LandscapeBudgets)
    # Enterprise-only fields. ``None`` on a landscape profile keeps its digest
    # identical to the pre-enterprise value, so persisted receipts still match.
    enterprise: EnterpriseBudgets | None = None
    goal_driven: GoalDrivenSettings | None = None
    agent_workspace: AgentWorkspaceSettings | None = None
    method_version: str | None = None
    # An explicit model pin for the run. ``None`` means the run inherits the
    # configured OpenCode route (MCP_OPENCODE_MODEL, else the runtime's own
    # default) instead of an orchestrator-chosen substitute.
    model: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _enterprise_defaults(self) -> GenerationProfile:
        if self.contract == ENTERPRISE_CONTRACT:
            if self.enterprise is None:
                object.__setattr__(self, "enterprise", EnterpriseBudgets())
            if self.method_version is None:
                object.__setattr__(self, "method_version", ENTERPRISE_METHOD_VERSION)
        elif self.enterprise is not None or self.method_version is not None:
            raise ValueError("enterprise budgets and method_version require the enterprise contract")
        if self.contract == GOAL_DRIVEN_CONTRACT:
            if self.goal_driven is None:
                object.__setattr__(self, "goal_driven", GoalDrivenSettings())
        elif self.goal_driven is not None:
            raise ValueError("goal_driven settings require the goal-driven contract")
        if self.contract == AGENT_WORKSPACE_CONTRACT:
            if self.agent_workspace is None:
                object.__setattr__(self, "agent_workspace", AgentWorkspaceSettings())
        elif self.agent_workspace is not None:
            raise ValueError("agent_workspace settings require the agent-workspace contract")
        if self.model is not None and "/" not in self.model:
            raise ValueError("a pinned model must be a provider/model route")
        return self


def normalize_profile(profile: dict | GenerationProfile | None) -> dict | None:
    if profile is None:
        return None
    if (
        isinstance(profile, dict)
        and profile.get("contract", LANDSCAPE_CONTRACT) == LANDSCAPE_CONTRACT
        and "contract" in profile
        and "analysis_version" not in profile
    ):
        # Previously persisted profiles must never silently adopt new semantics.
        profile = {**profile, "analysis_version": 2}
    normalized = GenerationProfile.model_validate(profile).model_dump(mode="json")
    if normalized["contract"] != ENTERPRISE_CONTRACT:
        # Keep landscape digests byte-identical to the pre-enterprise shape.
        for key in ("enterprise", "method_version", "model"):
            if normalized.get(key) is None:
                normalized.pop(key, None)
    if normalized["contract"] != GOAL_DRIVEN_CONTRACT and normalized.get("goal_driven") is None:
        normalized.pop("goal_driven", None)
    if (
        normalized["contract"] != AGENT_WORKSPACE_CONTRACT
        and normalized.get("agent_workspace") is None
    ):
        # Keep every pre-existing contract's digest byte-identical, so persisted
        # receipts and `assert_profile_matches` still recognise historical runs.
        normalized.pop("agent_workspace", None)
    elif normalized["contract"] == AGENT_WORKSPACE_CONTRACT:
        # `budgets` is the landscape lane's per-task quota block
        # (`max_analysis_tasks`, `max_evidence_bytes`, `max_scope_units`, ...).
        # It is inert here because nothing in this lane reads it, and that is
        # precisely why it must not be serialized: a quota sitting in the
        # profile is an invitation for some later phase to honour it, and
        # per-task quotas are what this contract exists to remove. The only
        # limits it carries are the whole-run breakers in
        # `AgentWorkspaceResources`.
        normalized.pop("budgets", None)
    return normalized


def is_enterprise_profile(profile: dict | GenerationProfile | None) -> bool:
    if profile is None:
        return False
    if isinstance(profile, GenerationProfile):
        return profile.contract == ENTERPRISE_CONTRACT
    return isinstance(profile, dict) and profile.get("contract") == ENTERPRISE_CONTRACT


def is_goal_driven_profile(profile: dict | GenerationProfile | None) -> bool:
    if profile is None:
        return False
    if isinstance(profile, GenerationProfile):
        return profile.contract == GOAL_DRIVEN_CONTRACT
    return isinstance(profile, dict) and profile.get("contract") == GOAL_DRIVEN_CONTRACT


def is_agent_workspace_profile(profile: dict | GenerationProfile | None) -> bool:
    if profile is None:
        return False
    if isinstance(profile, GenerationProfile):
        return profile.contract == AGENT_WORKSPACE_CONTRACT
    return isinstance(profile, dict) and profile.get("contract") == AGENT_WORKSPACE_CONTRACT


def enterprise_budgets(profile: dict | None) -> EnterpriseBudgets:
    normalized = normalize_profile(profile) or {}
    if normalized.get("contract") != ENTERPRISE_CONTRACT:
        raise ValueError("enterprise budgets are only defined for the enterprise contract")
    return EnterpriseBudgets.model_validate(normalized.get("enterprise") or {})


def profile_digest(profile: dict | None) -> str:
    payload = json.dumps(normalize_profile(profile), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: Selectable for new runs: legacy (``profile is None``) and the one agentic
#: lane. The three below are retired. They stay in the ``contract`` Literal so
#: persisted run rows, published ``_audit/generation_profile.json`` files and
#: ``assert_profile_matches`` digests still load -- history must remain
#: readable -- but a new run may not ask for them.
RETIRED_CONTRACTS: frozenset[str] = frozenset(
    {LANDSCAPE_CONTRACT, ENTERPRISE_CONTRACT, GOAL_DRIVEN_CONTRACT}
)


def validate_profile_route(profile: dict | None, *, platform: str, engine: str) -> None:
    if profile is not None:
        normalized = normalize_profile(profile)
        if normalized["contract"] in RETIRED_CONTRACTS:
            # `talend-landscape-v2` is also the Literal's default, so an empty
            # `{}` profile lands here rather than silently selecting a retired
            # contract. Saying so is better than the alternative: `{}` used to
            # MEAN landscape v2, and quietly remapping it to the agentic lane
            # would start a paid run nobody asked for.
            raise ValueError(
                f"{normalized['contract']} is retired and cannot be selected for a new run. "
                f"Use {AGENT_WORKSPACE_CONTRACT}, or omit the profile entirely for legacy."
            )
        if normalized["contract"] == AGENT_WORKSPACE_CONTRACT:
            # Neither platform- nor engine-constrained. The contract itself
            # selects the provider class, and which coding agent runs it is
            # `agent_workspace.agent_provider` -- an unregistered one fails
            # closed in the registry, which is the honest place for that error.
            # Any platform whose scan produces the workspace inputs qualifies.
            return
        if normalized["contract"] == GOAL_DRIVEN_CONTRACT:
            if engine != normalized["engine"]:
                raise ValueError(f"{normalized['contract']} requires {normalized['engine']}")
        elif platform.lower() != "talend" or engine != normalized["engine"]:
            raise ValueError(f"{normalized['contract']} requires Talend and opencode-cli")


def assert_profile_matches(expected: dict | None, actual: dict | None) -> None:
    if profile_digest(expected) != profile_digest(actual):
        raise ValueError("Generation profile mismatch; start a fresh attempt instead of mixing contracts")


def load_published_profile(docs_dir: Path) -> dict | None:
    path = docs_dir / "_audit" / "generation_profile.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return normalize_profile(data.get("generation_profile")) if isinstance(data, dict) else None
