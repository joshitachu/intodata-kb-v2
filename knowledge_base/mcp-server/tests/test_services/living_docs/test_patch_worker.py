from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    EvidenceReference,
    PatchCandidate,
    PatchOperation,
    PatchOperationKind,
    PatchPlan,
    PatchPlanProducer,
    PatchTarget,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
    content_sha256,
)
from mcp_server.services.living_docs.patch_worker import (
    DeterministicPatchPlanProducer,
    PatchPlanRejected,
    PatchTaskContract,
    PatchTaskToolSurface,
    PatchToolDenied,
    hold_if_budget_exceeded,
    validate_patch_plan,
)


# ``hold_if_budget_exceeded`` is structural: it reads budget/usage attributes and
# does not depend on the deleted agent-runtime envelope types.
@dataclass(frozen=True)
class _Budgets:
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    max_tool_bytes: int | None = None
    max_elapsed_ms: int | None = None
    max_billed_cost_usd: float | None = None


@dataclass(frozen=True)
class _Request:
    budgets: _Budgets


@dataclass(frozen=True)
class _Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    billed_cost_usd: float | None = None
    normalized_cost_usd: float | None = None


@dataclass(frozen=True)
class _Timings:
    elapsed_ms: int | None = None


@dataclass(frozen=True)
class _Result:
    usage: _Usage
    timings: _Timings
    tool_calls: int | None = None
    tool_bytes: int | None = None
    retry_count: int = 0


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
EVIDENCE_BYTES = b"timeout was changed from 30 to 60"


def _contract() -> PatchTaskContract:
    source = SourceRevision(
        project_id="project-a",
        origin=SourceOrigin.GIT,
        source_identity="github:org/repo",
        revision_id="commit-2",
        content_sha256=content_sha256(b"repository snapshot"),
        observed_at=NOW,
    )
    floor_target = PatchTarget("docs/customer.md", "timeout", ArtifactKind.CANONICAL_DOCUMENT)
    expansion_target = PatchTarget("docs/runbook.md", "timeout", ArtifactKind.CANONICAL_DOCUMENT)
    candidate = PatchCandidate.new(
        change_set=ChangeSet(
            source_revision=source,
            base_source_revision_id="commit-1",
            idempotency_key="push:commit-2",
            changes=(SemanticChange(ChangeKind.MODIFIED, "jobs/customer.json", "timeout"),),
        ),
        impact_floor=DeterministicImpactFloor(
            source_revision_key=source.key,
            targets=(floor_target,),
            evidence_ids=("evidence:timeout",),
        ),
        base_knowledge_revision_id="knowledge-r1",
        knowledge_revision_id="knowledge-r2",
    )
    return PatchTaskContract(
        project_id="project-a",
        candidate=candidate,
        evidence=(EvidenceReference("evidence:timeout", content_sha256(EVIDENCE_BYTES), "scanner/jobs/customer.json#timeout"),),
        admitted_targets=(floor_target, expansion_target),
        expansion_targets=(expansion_target,),
    )


def _operation(target: PatchTarget, *, evidence_ids: tuple[str, ...] = ("evidence:timeout",)) -> PatchOperation:
    return PatchOperation(
        target=target,
        kind=PatchOperationKind.REPLACE,
        evidence_ids=evidence_ids,
        replacement_sha256=content_sha256(b"60"),
        expected_current_sha256=content_sha256(b"30"),
    )


def _plan(contract: PatchTaskContract, operations: tuple[PatchOperation, ...], **overrides: object) -> PatchPlan:
    values: dict[str, object] = {
        "candidate_id": contract.candidate.id,
        "source_revision_key": contract.candidate.change_set.source_revision.key,
        "base_knowledge_revision_id": contract.candidate.base_knowledge_revision_id,
        "producer": PatchPlanProducer.DETERMINISTIC,
        "operations": operations,
    }
    values.update(overrides)
    return PatchPlan(**values)  # type: ignore[arg-type]


def test_plan_cannot_remove_floor_add_uncited_target_or_request_product_effects():
    contract = _contract()
    floor, expansion = contract.admitted_targets
    with pytest.raises(PatchPlanRejected, match="remove deterministic"):
        validate_patch_plan(contract=contract, plan=_plan(contract, (_operation(expansion),)))
    with pytest.raises(PatchPlanRejected, match="uncited"):
        validate_patch_plan(
            contract=contract,
            plan=_plan(contract, (_operation(floor), _operation(expansion, evidence_ids=("unknown",)))),
        )
    with pytest.raises(PatchPlanRejected, match="full rewrite, publish, or RAG"):
        validate_patch_plan(
            contract=contract,
            plan=_plan(contract, (_operation(floor),), full_rewrite_requested=True),
        )


def test_patch_tool_surface_exposes_only_admitted_evidence_targets_and_single_validated_submission():
    contract = _contract()
    surface = PatchTaskToolSurface(contract=contract, evidence_content={"evidence:timeout": EVIDENCE_BYTES})
    floor = contract.candidate.impact_floor.targets[0]
    plan = _plan(contract, (_operation(floor),))

    assert surface.read_evidence(evidence_id="evidence:timeout", max_bytes=8)["returned_bytes"] == 8
    with pytest.raises(PatchToolDenied, match="outside patch task scope"):
        surface.read_evidence(evidence_id="other-project-evidence", max_bytes=10)
    assert surface.submit_patch_plan(plan) == plan
    assert surface.submit_patch_plan(plan) == plan
    with pytest.raises(PatchToolDenied, match="exactly one"):
        surface.submit_patch_plan(
            _plan(
                contract,
                (
                    PatchOperation(
                        target=floor,
                        kind=PatchOperationKind.REPLACE,
                        evidence_ids=("evidence:timeout",),
                        replacement_sha256=content_sha256(b"other replacement"),
                    ),
                ),
            )
        )


def test_deterministic_fast_path_makes_valid_patch_plan_without_runtime_provider_call():
    contract = _contract()
    floor = contract.candidate.impact_floor.targets[0]

    plan = DeterministicPatchPlanProducer().produce(
        candidate=contract.candidate,
        operations=(_operation(floor),),
    )

    assert plan.producer is PatchPlanProducer.DETERMINISTIC
    assert plan.targets == (floor,)
    assert plan.full_rewrite_requested is False
    assert plan.publish_requested is False
    assert plan.rag_mutation_requested is False


def test_budget_exhaustion_is_held_and_never_signals_retry():
    request = _Request(
        budgets=_Budgets(max_input_tokens=10, max_tool_calls=1, max_billed_cost_usd=0.01)
    )
    result = _Result(
        usage=_Usage(input_tokens=11, billed_cost_usd=0.02),
        timings=_Timings(elapsed_ms=1),
        tool_calls=2,
        retry_count=0,
    )

    held = hold_if_budget_exceeded(request=request, result=result)

    assert held is not None
    assert held.reason_code == "agent_budget_exceeded"
    assert set(held.exceeded) == {"input_tokens", "tool_calls", "billed_cost_usd"}


def test_within_budget_is_not_held_but_a_retry_is():
    request = _Request(budgets=_Budgets(max_input_tokens=100, max_tool_calls=10))
    usage = _Usage(input_tokens=10)
    timings = _Timings(elapsed_ms=1)

    assert hold_if_budget_exceeded(
        request=request,
        result=_Result(usage=usage, timings=timings, tool_calls=1),
    ) is None

    held = hold_if_budget_exceeded(
        request=request,
        result=_Result(usage=usage, timings=timings, tool_calls=1, retry_count=1),
    )
    assert held is not None
    assert held.reason_code == "agent_retry_not_allowed"
