"""Production rollout gates and truthful readiness receipts for living docs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from mcp_server.services.group_doc_dependency import DOC_PACK_MANIFEST_FILENAME
from mcp_server.services.rag_support_package import (
    RAG_SUPPORT_DIRNAME,
    inspect_generation_cache_readiness,
)


class RolloutStage(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    APPROVAL_ONLY = "approval_only"
    AUTO_LOW_RISK = "auto_low_risk"
    AUTO_SCOPED = "auto_scoped"


@dataclass(frozen=True, slots=True)
class ProjectPatchReadiness:
    project_id: str
    ready: bool
    stage: RolloutStage
    reason_codes: tuple[str, ...]
    active_knowledge_revision_id: str | None
    source_revision_id: str | None
    cache: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    allowed: bool
    reason_code: str
    requires_human_approval: bool


def inspect_project_patch_readiness(
    *,
    project_id: str,
    output_dir: Path,
    feature_enabled: bool,
    stage: RolloutStage,
    source_revision_id: str | None,
    active_knowledge_revision_id: str | None,
) -> ProjectPatchReadiness:
    """Reject ambiguous baselines before a candidate can spend or publish."""

    cache = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=bool(source_revision_id),
    )
    reasons: list[str] = []
    if not feature_enabled:
        reasons.append("living_doc_patching_disabled")
    if stage is RolloutStage.DISABLED:
        reasons.append("rollout_stage_disabled")
    if not source_revision_id:
        reasons.append("source_baseline_missing")
    if not active_knowledge_revision_id:
        reasons.append("knowledge_revision_baseline_missing")
    if not cache.targeted_regen_ready:
        reasons.extend(cache.reason_codes)
    manifest = output_dir / "docs" / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
    if not manifest.is_file():
        reasons.append("traceability_manifest_missing")
    return ProjectPatchReadiness(
        project_id=project_id,
        ready=not reasons,
        stage=stage,
        reason_codes=tuple(dict.fromkeys(reasons)),
        active_knowledge_revision_id=active_knowledge_revision_id,
        source_revision_id=source_revision_id,
        cache=cache.to_dict(),
    )


def activation_decision(
    *,
    readiness: ProjectPatchReadiness,
    approved: bool,
    deterministic: bool,
    changed_artifact_count: int,
    max_low_risk_artifacts: int = 3,
) -> ActivationDecision:
    """Apply the staged rollout policy after validation and before activation."""

    if not readiness.ready:
        return ActivationDecision(False, readiness.reason_codes[0], False)
    if readiness.stage is RolloutStage.SHADOW:
        return ActivationDecision(False, "shadow_proposal_only", False)
    if readiness.stage is RolloutStage.APPROVAL_ONLY:
        return ActivationDecision(
            approved,
            "approved" if approved else "human_approval_required",
            not approved,
        )
    if readiness.stage is RolloutStage.AUTO_LOW_RISK:
        low_risk = deterministic and 0 < changed_artifact_count <= max_low_risk_artifacts
        if low_risk:
            return ActivationDecision(True, "low_risk_auto_activation", False)
        return ActivationDecision(
            approved,
            "approved" if approved else "human_approval_required",
            not approved,
        )
    return ActivationDecision(True, "scoped_auto_activation", False)
