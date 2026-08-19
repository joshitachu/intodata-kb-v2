"""Bounded patch-plan contracts for living-document candidates.

A patch task receives frozen evidence and an admitted target set, may submit
one typed patch plan, and has no filesystem, shell, RAG, or publication
authority.  The agent-runtime envelope that used to wrap this contract was
removed with the experiment surface; the deterministic machinery here is the
part the living-docs path actually depends on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from mcp_server.services.living_docs.contracts import (
    DeterministicImpactFloor,
    EvidenceReference,
    PatchCandidate,
    PatchOperation,
    PatchPlan,
    PatchPlanProducer,
    PatchTarget,
    canonical_json_bytes,
)


PATCH_PLAN_SCHEMA_VERSION = 1
PATCH_PLAN_MEDIA_TYPE = "application/vnd.nexus.living-docs.patch-plan+json"
PATCH_PLAN_ARTIFACT_NAME = "patch-plan.json"
PATCH_MCP_TOOLS = (
    "nexus_living_docs_get_contract",
    "nexus_living_docs_list_evidence",
    "nexus_living_docs_read_evidence",
    "nexus_living_docs_list_targets",
    "nexus_living_docs_submit_patch_plan",
)


class PatchPlanRejected(ValueError):
    """An agent plan is outside the frozen patch task contract."""


class PatchToolDenied(PermissionError):
    """A patch task tried to access content or an effect it was not granted."""


@dataclass(frozen=True, slots=True)
class PatchTaskContract:
    """Nexus-owned, frozen capabilities for one patch-plan task."""

    project_id: str
    candidate: PatchCandidate
    evidence: tuple[EvidenceReference, ...]
    admitted_targets: tuple[PatchTarget, ...]
    expansion_targets: tuple[PatchTarget, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id or self.project_id != self.candidate.change_set.source_revision.project_id:
            raise ValueError("patch task project must match candidate project")
        if not self.evidence or len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("patch task evidence must be non-empty and unique")
        if not self.admitted_targets or len(set(self.admitted_targets)) != len(self.admitted_targets):
            raise ValueError("patch task admitted targets must be non-empty and unique")
        if len(set(self.expansion_targets)) != len(self.expansion_targets):
            raise ValueError("patch task expansion targets must be unique")
        floor = set(self.candidate.impact_floor.targets)
        if not floor.issubset(self.admitted_targets):
            raise ValueError("patch task admitted targets must retain deterministic impact floor")
        if not set(self.expansion_targets).issubset(self.admitted_targets):
            raise ValueError("patch task expansion targets must be admitted")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if not set(self.candidate.impact_floor.evidence_ids).issubset(evidence_ids):
            raise ValueError("patch task is missing deterministic impact evidence")

    @property
    def id(self) -> str:
        return f"patch-plan:{self.candidate.id}"

    @property
    def contract_sha256(self) -> str:
        return sha256(canonical_json_bytes({
            "candidate_id": self.candidate.id,
            "evidence": self.evidence,
            "expansion_targets": self.expansion_targets,
            "project_id": self.project_id,
            "schema_version": PATCH_PLAN_SCHEMA_VERSION,
            "targets": self.admitted_targets,
        })).hexdigest()

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)


class PatchTaskToolSurface:
    """In-process server contract for a frozen patch-plan task.

    It is intentionally not a general MCP dispatcher.  The only mutating call
    is ``submit_patch_plan`` and that call validates before retaining a plan;
    there is no RAG or document publication method to invoke.
    """

    def __init__(self, *, contract: PatchTaskContract, evidence_content: Mapping[str, bytes]) -> None:
        if set(evidence_content) != contract.evidence_ids:
            raise ValueError("patch task evidence content must match exact admitted evidence ids")
        for reference in contract.evidence:
            if sha256(evidence_content[reference.evidence_id]).hexdigest() != reference.content_sha256:
                raise ValueError("patch task evidence bytes do not match frozen evidence hash")
        self._contract = contract
        self._evidence_content = dict(evidence_content)
        self._submitted: PatchPlan | None = None

    @property
    def submitted_plan(self) -> PatchPlan | None:
        return self._submitted

    def get_contract(self) -> Mapping[str, Any]:
        return {
            "candidate_id": self._contract.candidate.id,
            "knowledge_revision_id": self._contract.candidate.knowledge_revision_id,
            "schema_version": PATCH_PLAN_SCHEMA_VERSION,
            "source_revision_key": self._contract.candidate.change_set.source_revision.key,
        }

    def list_evidence(self) -> tuple[Mapping[str, str], ...]:
        return tuple(
            {"evidence_id": item.evidence_id, "content_sha256": item.content_sha256, "locator": item.locator}
            for item in self._contract.evidence
        )

    def read_evidence(self, *, evidence_id: str, max_bytes: int) -> Mapping[str, Any]:
        if evidence_id not in self._contract.evidence_ids:
            raise PatchToolDenied("evidence is outside patch task scope")
        if max_bytes < 1:
            raise PatchToolDenied("evidence read must have a positive byte limit")
        content = self._evidence_content[evidence_id][:max_bytes]
        return {"evidence_id": evidence_id, "content": content.decode("utf-8", errors="replace"), "returned_bytes": len(content)}

    def list_targets(self) -> tuple[str, ...]:
        return tuple(target.key for target in self._contract.admitted_targets)

    def submit_patch_plan(self, plan: PatchPlan) -> PatchPlan:
        validate_patch_plan(contract=self._contract, plan=plan)
        if self._submitted is not None and self._submitted != plan:
            raise PatchToolDenied("patch task may submit exactly one immutable patch plan")
        self._submitted = plan
        return plan


def validate_patch_plan(*, contract: PatchTaskContract, plan: PatchPlan) -> PatchPlan:
    """Enforce deterministic floor retention and evidence-cited expansion."""

    candidate = contract.candidate
    if plan.schema_version != PATCH_PLAN_SCHEMA_VERSION:
        raise PatchPlanRejected("patch plan schema version is unsupported")
    if plan.candidate_id != candidate.id or plan.source_revision_key != candidate.change_set.source_revision.key:
        raise PatchPlanRejected("patch plan is bound to a different candidate or source revision")
    if plan.base_knowledge_revision_id != candidate.base_knowledge_revision_id:
        raise PatchPlanRejected("patch plan is bound to a different base knowledge revision")
    if plan.full_rewrite_requested or plan.publish_requested or plan.rag_mutation_requested:
        raise PatchPlanRejected("patch plans may not request full rewrite, publish, or RAG mutation")
    floor = set(candidate.impact_floor.targets)
    planned = set(plan.targets)
    if not floor.issubset(planned):
        raise PatchPlanRejected("patch plan may not remove deterministic impact scope")
    admitted = set(contract.admitted_targets)
    if not planned.issubset(admitted):
        raise PatchPlanRejected("patch plan includes a target outside the admitted task scope")
    expansions = planned - floor
    if not expansions.issubset(contract.expansion_targets):
        raise PatchPlanRejected("patch plan expansion target is not specifically admitted")
    for operation in plan.operations:
        if not set(operation.evidence_ids).issubset(contract.evidence_ids):
            raise PatchPlanRejected("patch plan operation has uncited or unavailable evidence")
        if operation.target in expansions and not operation.evidence_ids:
            raise PatchPlanRejected("patch plan expansion requires cited evidence")
    return plan


class DeterministicPatchPlanProducer:
    """Mechanical no-provider path that satisfies the same patch-plan schema."""

    def produce(
        self,
        *,
        candidate: PatchCandidate,
        operations: tuple[PatchOperation, ...],
    ) -> PatchPlan:
        plan = PatchPlan(
            candidate_id=candidate.id,
            source_revision_key=candidate.change_set.source_revision.key,
            base_knowledge_revision_id=candidate.base_knowledge_revision_id,
            producer=PatchPlanProducer.DETERMINISTIC,
            operations=operations,
        )
        _validate_mechanical_plan(candidate.impact_floor, plan)
        return plan


def _validate_mechanical_plan(floor: DeterministicImpactFloor, plan: PatchPlan) -> None:
    if plan.full_rewrite_requested or plan.publish_requested or plan.rag_mutation_requested:
        raise PatchPlanRejected("mechanical plan may not request product effects")
    if set(plan.targets) != set(floor.targets):
        raise PatchPlanRejected("mechanical plan must patch exactly deterministic impact scope")
    for operation in plan.operations:
        if not set(operation.evidence_ids).issubset(set(floor.evidence_ids)):
            raise PatchPlanRejected("mechanical plan operation must cite deterministic evidence")


@dataclass(frozen=True, slots=True)
class PatchWorkerHeld:
    reason_code: str
    exceeded: tuple[str, ...]


def hold_if_budget_exceeded(*, request: Any, result: Any) -> PatchWorkerHeld | None:
    """Convert every observed runtime budget breach into a non-retrying hold.

    ``request`` and ``result`` are structural: any object exposing the budget
    and usage attributes read below works.  They were nominally typed against
    the deleted experiment runtime envelope; the rule itself is deterministic
    and independent of who produced the numbers.
    """

    budgets = request.budgets
    actual_cost = result.usage.billed_cost_usd
    if actual_cost is None:
        actual_cost = result.usage.normalized_cost_usd
    checks = (
        ("input_tokens", result.usage.input_tokens, budgets.max_input_tokens),
        ("output_tokens", result.usage.output_tokens, budgets.max_output_tokens),
        ("tool_calls", result.tool_calls, budgets.max_tool_calls),
        ("tool_bytes", result.tool_bytes, budgets.max_tool_bytes),
        ("elapsed_ms", result.timings.elapsed_ms, budgets.max_elapsed_ms),
        ("billed_cost_usd", actual_cost, budgets.max_billed_cost_usd),
    )
    exceeded = tuple(name for name, actual, maximum in checks if actual is not None and maximum is not None and actual > maximum)
    if exceeded:
        return PatchWorkerHeld(reason_code="agent_budget_exceeded", exceeded=exceeded)
    if result.retry_count:
        return PatchWorkerHeld(reason_code="agent_retry_not_allowed", exceeded=())
    return None
