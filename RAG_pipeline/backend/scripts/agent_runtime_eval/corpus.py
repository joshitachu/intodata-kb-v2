"""Load and lint the compact authored corpus into the strict scenario schema."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ArgumentConstraint,
    AuthorizedContext,
    EffectExpectation,
    ExpectedTrajectory,
    GroundingExpectation,
    MechanicsExpectation,
    Scenario,
    SemanticAssertion,
    StateExpectation,
    ToolPolicyExpectation,
    Turn,
)
from .security import SECRET_PATTERNS


CORPUS_VERSION = "agent-runtime-behavior-v2"
BASE_CORPUS_VERSION = "agent-runtime-behavior-v1"
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "eval_fixtures" / "agent_runtime_v1"
BASE_CORPUS_PATH = FIXTURE_ROOT / "corpus_v1.json"
CORPUS_PATH = FIXTURE_ROOT / "corpus_real_project_v2.json"
LEAKAGE_SOURCES_PATH = FIXTURE_ROOT / "leakage_sources_v1.json"
PRODUCTION_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"

REQUIRED_FAMILIES = (
    "direct_conversation", "capability_overview", "capability_discussion", "guidance",
    "explicit_creation", "guided_creation_ambiguity", "negated_action", "correction",
    "unrelated_keyword", "documentation_rag", "rag_followup", "user_format",
    "multi_part_read", "no_evidence", "partial_evidence", "conflicting_evidence",
    "source_unavailable", "scope_denied", "runtime_evidence", "metadata",
    "workflow_status", "cancel_workflow", "pause_conversation", "resume_task",
    "abandon_draft", "task_switch", "active_workflow_interruption", "validation_feedback",
    "correctable_args", "policy_denial", "approval_reject", "approval_edit",
    "multiple_writes", "multiple_reads", "complex_objective", "simple_objective",
    "plan_revision", "plan_restart", "workflow_completion", "workflow_failure",
    "rapid_duplicate_turn", "concurrent_turns", "stream_reconnect",
    "commit_observation_crash", "model_timeout", "tool_timeout", "soft_deadline",
    "hard_budget", "malformed_provider_output", "unknown_tool", "prompt_injection",
    "secret_redaction", "repeated_output", "transport_parity", "grounding_safe_stream",
    "shadow_expensive_read",
)

CRITICAL_FAMILIES = {
    "documentation_rag", "rag_followup", "multi_part_read", "explicit_creation",
    "negated_action", "cancel_workflow", "policy_denial", "multiple_writes",
    "rapid_duplicate_turn", "stream_reconnect", "commit_observation_crash",
    "secret_redaction", "prompt_injection",
}
REQUIRED_CRITICAL_VARIANTS = {
    "formal", "informal", "typo", "pronoun", "negation",
    "keyword_collision", "neighbor", "ambiguous",
}
LEAKAGE_SOURCE_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md", ".txt", ".toml"}


class SeedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    family: str
    split: str = "development"
    variant: str = "formal"
    incidents: list[int] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    prompt: str
    decisions: list[str]
    tools: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    alternate_tools: list[list[str]] = Field(default_factory=list)
    args: dict[str, object] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    task: str = "none"
    plan: str = "none"
    workflow: str = "none"
    approval: str = "none"
    allowed_effects: list[str] = Field(default_factory=list)
    forbidden_effects: list[str] = Field(
        default_factory=lambda: ["write", "workflow_start", "plan_create", "external_effect"]
    )
    response_contains: list[str] = Field(default_factory=list)
    response_contains_any: list[str] = Field(default_factory=list)
    response_excludes: list[str] = Field(default_factory=lambda: ["traceback", "field required"])
    clarify: bool = False
    grounding: str = "none"
    evidence_refs: int = 0
    budget: str = "interactive_single"
    allowed_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    permissions: list[str] = Field(default_factory=lambda: ["chat"])
    project_scope: list[str] = Field(default_factory=lambda: ["fixture-project"])
    manifest_hash: str = "fixture-manifest-v1"


def _expand(row: SeedRow) -> Scenario:
    constraints = [
        ArgumentConstraint(tool_family=row.tools[0], path=path, operator="eq", value=value)
        for path, value in row.args.items()
    ] if row.tools else []
    response = [SemanticAssertion(kind="nonempty")]
    response.extend(SemanticAssertion(kind="contains", value=value) for value in row.response_contains)
    if row.response_contains_any:
        response.append(SemanticAssertion(kind="contains_any", value=row.response_contains_any))
    response.extend(SemanticAssertion(kind="excludes", value=value) for value in row.response_excludes)
    if row.clarify:
        response.append(SemanticAssertion(kind="asks_clarification"))
    allowed_effects = [
        EffectExpectation(
            kind=kind,
            minimum=1,
            operation_id_required=True,
            receipt_required=True,
            maximum=1,
        )
        for kind in row.allowed_effects
    ]
    policies = row.policies or ["allowed"] * len(row.tools)
    if len(policies) != len(row.tools):
        raise ValueError(f"{row.id}: policies must align one-for-one with the primary tool sequence")
    mechanics = MechanicsExpectation(
        turn_authority=(
            "deduplicated" if row.family == "rapid_duplicate_turn"
            else "concurrent" if row.family == "concurrent_turns"
            else "none"
        ),
        ordered_events=row.family in {"stream_reconnect", "transport_parity"},
        replay_identity=row.family == "stream_reconnect",
        recovery_correlation=row.family == "commit_observation_crash",
        recovery_operation_id=(
            "operation-committed" if row.family == "commit_observation_crash" else None
        ),
        recovery_receipt_id=(
            "receipt-committed" if row.family == "commit_observation_crash" else None
        ),
        tool_observation_correlation=bool(row.tools),
        paired_transport_parity=row.family == "transport_parity",
    )
    expected = ExpectedTrajectory(
        allowed_terminal_statuses=row.allowed_statuses,
        boundary_decisions=row.decisions,
        allowed_tool_sequences=[row.tools, *row.alternate_tools],
        tool_policies=[
            ToolPolicyExpectation(
                call_index=index,
                family=family,
                allowed_outcomes=[policies[index]],
            )
            for index, family in enumerate(row.tools)
        ],
        argument_constraints=constraints,
        observations=row.observations,
        state=StateExpectation(
            task=row.task, plan=row.plan, workflow=row.workflow, approval=row.approval
        ),
        allowed_effects=allowed_effects,
        forbidden_effects=row.forbidden_effects,
        response_semantics=response,
        grounding=GroundingExpectation(
            mode=row.grounding,
            minimum_evidence_refs=row.evidence_refs,
            unsupported_claims_allowed=False,
        ),
        budget_class=row.budget,
        mechanics=mechanics,
    )
    return Scenario(
        scenario_id=row.id,
        family=row.family,
        split=row.split,
        variant=row.variant,
        provenance_incidents=row.incidents,
        conversation_seed=row.history,
        authorized_context=AuthorizedContext(
            user="fixture-user",
            project_scope=row.project_scope,
            permissions=row.permissions,
            manifest_hash=row.manifest_hash,
        ),
        turns=[Turn(
            turn_id=f"{row.id}.turn-1",
            user_message=row.prompt,
            transports=["stream", "nonstream"],
            expected=expected,
        )],
    )


def _generic_variant_override(base: dict, family: str, variant: str) -> dict:
    prompt = base["prompt"]
    if variant == "informal":
        return {"prompt": f"Quick one — {prompt.lower()}"}
    if variant == "typo":
        return {"prompt": f"pls hndle this reqest: {prompt.lower()}"}
    if variant == "pronoun":
        return {
            "history": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "I identified the referenced target but did not act."},
            ],
            "prompt": "Could you do that for it now?",
        }
    if variant == "negation":
        return {
            "prompt": f"Do not execute this; explain it only: {prompt}",
            "decisions": ["answer"], "tools": [], "policies": [], "args": {},
            "observations": [], "task": "none", "plan": "none", "workflow": "none",
            "approval": "none", "allowed_effects": [], "grounding": "none",
            "evidence_refs": 0, "response_contains": [],
        }
    if variant == "keyword_collision":
        return {"prompt": f"Without switching to logs, Packs, or documents: {prompt}"}
    if variant == "neighbor":
        return {
            "history": [{"role": "assistant", "content": f"The active family is {family}."}],
            "prompt": "Instead, explain whether Nexus handles unrelated weather forecasts.",
            "decisions": ["answer"], "tools": [], "policies": [], "args": {},
            "observations": [], "task": "none", "plan": "none", "workflow": "none",
            "approval": "none", "allowed_effects": [], "grounding": "none",
            "evidence_refs": 0, "response_contains": [],
        }
    return {
        "history": [
            {"role": "assistant", "content": f"There are multiple possible targets for {family}."}
        ],
        "prompt": "Can you handle that one?", "split": "holdout",
        "decisions": ["clarify"], "tools": [], "policies": [], "args": {},
        "observations": [], "task": "none", "plan": "none", "workflow": "none",
        "approval": "none", "allowed_effects": [], "grounding": "none",
        "evidence_refs": 0, "clarify": True, "response_contains": [],
    }


def _snapshot_manifest_hash(snapshot_path: Path) -> str:
    snapshot = json.loads(snapshot_path.read_text())
    documents = snapshot.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("real-project snapshot has no documents")
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode()).hexdigest()
    expected = str(snapshot.get("inventory_sha256", ""))
    if actual != expected:
        raise ValueError("real-project snapshot inventory hash does not match its contents")
    source_root = (snapshot_path.parent / str(snapshot["source_root"])).resolve()
    for document in documents:
        source = source_root / str(document["document"])
        if not source.is_file():
            raise ValueError(f"real-project snapshot source is missing: {source.name}")
        if source.stat().st_size != int(document["size_bytes"]):
            raise ValueError(f"real-project snapshot size drift: {source.name}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError(f"real-project snapshot content drift: {source.name}")
    return actual


def _replace_strings(value: object, replacements: dict[str, str]) -> object:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def _apply_family_override(
    row: dict,
    overrides: dict[str, dict],
    *,
    manifest_hash: str,
) -> dict:
    override = overrides.get(str(row.get("family")))
    if override is None:
        return row
    updated = dict(row)
    updated = _replace_strings(updated, override.get("prompt_replacements", {}))  # type: ignore[assignment]
    variant_override = override.get("variant_overrides", {}).get(str(updated.get("variant")), {})
    if variant_override:
        updated.update(variant_override)
    updated["project_scope"] = list(override["project_scope"])
    updated["manifest_hash"] = manifest_hash
    if updated.get("tools"):
        if "action_context_suffix" in override:
            updated["prompt"] = str(updated.get("prompt", "")) + str(
                override["action_context_suffix"]
            )
        if "tool_sequence" in override and "tools" not in variant_override:
            updated["tools"] = list(override["tool_sequence"])
            updated["policies"] = ["allowed"] * len(updated["tools"])
        if "decisions" in override:
            updated["decisions"] = list(override["decisions"])
        if "observations" in override:
            updated["observations"] = list(override["observations"])
        updated["args"] = {
            **dict(updated.get("args", {})),
            **dict(override.get("argument_overrides", {})),
        }
        if "alternate_tool_sequences" in override:
            updated["alternate_tools"] = list(override["alternate_tool_sequences"])
        for response_key in ("response_contains", "response_contains_any"):
            if response_key in override:
                updated[response_key] = list(override[response_key])
    if "alternate_tool_sequences" in variant_override:
        updated["alternate_tools"] = list(variant_override["alternate_tool_sequences"])
    return updated


def load_corpus(path: Path = CORPUS_PATH) -> list[Scenario]:
    raw = json.loads(path.read_text())
    family_overrides: dict[str, dict] = {}
    manifest_hash = "fixture-manifest-v1"
    if "base_corpus" in raw:
        if raw.get("corpus_version") != CORPUS_VERSION:
            raise ValueError(f"unsupported corpus version: {raw.get('corpus_version')!r}")
        base_path = path.parent / str(raw["base_corpus"])
        base = json.loads(base_path.read_text())
        if base.get("corpus_version") != BASE_CORPUS_VERSION:
            raise ValueError(f"unsupported base corpus version: {base.get('corpus_version')!r}")
        manifest_hash = _snapshot_manifest_hash(
            path.parent / str(raw["real_project_snapshot"])
        )
        family_overrides = dict(raw.get("family_overrides", {}))
        raw = base
    elif raw.get("corpus_version") != BASE_CORPUS_VERSION:
        raise ValueError(f"unsupported corpus version: {raw.get('corpus_version')!r}")
    rows = list(raw["scenarios"])
    base_by_family = {row["family"]: row for row in rows}
    authored_variants = raw.get("critical_variants", {})
    for family, base in base_by_family.items():
        variants = authored_variants.get(family, {})
        for variant in sorted(REQUIRED_CRITICAL_VARIANTS.difference({"formal"})):
            override = variants.get(variant, _generic_variant_override(base, family, variant))
            row = dict(base)
            row.update(override if isinstance(override, dict) else {"prompt": override})
            if isinstance(override, dict) and "tools" in override and "policies" not in override:
                row["policies"] = []
            if variant in {"negation", "ambiguous", "neighbor"} and not row.get("tools"):
                row.update({
                    "task": "none", "plan": "none", "workflow": "none", "approval": "none",
                    "allowed_effects": [], "args": {}, "observations": [], "grounding": "none",
                    "evidence_refs": 0,
                })
            row["id"] = f"{base['id']}.{variant}"
            row["variant"] = variant
            row["incidents"] = []
            rows.append(row)
    rows = [
        _apply_family_override(row, family_overrides, manifest_hash=manifest_hash)
        for row in rows
    ]
    return [_expand(SeedRow.model_validate(row)) for row in rows]


def real_project_snapshot_path(path: Path = CORPUS_PATH) -> Path | None:
    """Resolve the immutable real-project snapshot declared by a corpus overlay."""

    raw = json.loads(path.read_text())
    value = raw.get("real_project_snapshot")
    return path.parent / str(value) if value else None


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def lint_corpus(
    scenarios: list[Scenario],
    *,
    leakage_sources_path: Path = LEAKAGE_SOURCES_PATH,
    production_source_root: Path | None = PRODUCTION_SOURCE_ROOT,
) -> list[str]:
    errors: list[str] = []
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        errors.append("duplicate scenario IDs")
    prompt_contexts = [
        _normalized(json.dumps(scenario.conversation_seed) + scenario.turns[0].user_message)
        for scenario in scenarios
    ]
    if len(prompt_contexts) != len(set(prompt_contexts)):
        errors.append("duplicate normalized conversation context and user wording")
    missing = set(REQUIRED_FAMILIES).difference(scenario.family for scenario in scenarios)
    if missing:
        errors.append(f"missing acceptance families: {sorted(missing)!r}")
    for family in CRITICAL_FAMILIES:
        variants = {scenario.variant for scenario in scenarios if scenario.family == family}
        absent = REQUIRED_CRITICAL_VARIANTS.difference(variants)
        if absent:
            errors.append(f"critical family {family} missing variants: {sorted(absent)!r}")
    for family in REQUIRED_FAMILIES:
        variants = {scenario.variant for scenario in scenarios if scenario.family == family}
        absent = REQUIRED_CRITICAL_VARIANTS.difference(variants)
        if absent:
            errors.append(f"family {family} missing variants: {sorted(absent)!r}")
    for scenario in scenarios:
        if scenario.variant == "pronoun" and not scenario.conversation_seed:
            errors.append(f"pronoun variant lacks conversation context: {scenario.scenario_id}")
    serialized = json.dumps([scenario.model_dump(mode="json") for scenario in scenarios])
    for pattern in SECRET_PATTERNS:
        if pattern.search(serialized):
            errors.append(f"secret-like content matched {pattern.pattern!r}")
    source_raw = json.loads(leakage_sources_path.read_text())
    source_parts = list(source_raw["sources"])
    if production_source_root is not None:
        source_parts.extend(
            path.read_text(errors="ignore")
            for path in sorted(production_source_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in LEAKAGE_SOURCE_SUFFIXES
        )
    source_text = _normalized(" ".join(source_parts))
    source_words = source_text.split()
    source_ngrams = {
        tuple(source_words[index:index + 6])
        for index in range(max(0, len(source_words) - 5))
    }
    for scenario in scenarios:
        if scenario.split != "holdout":
            continue
        wording = _normalized(scenario.turns[0].user_message)
        if wording and wording in source_text:
            errors.append(f"holdout wording leaked verbatim: {scenario.scenario_id}")
        words = wording.split()
        if any(
            tuple(words[index:index + 6]) in source_ngrams
            for index in range(max(0, len(words) - 5))
        ):
            errors.append(f"holdout six-token wording leak: {scenario.scenario_id}")
    return errors
