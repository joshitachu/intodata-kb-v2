from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.agent_runtime_canary import collector as collector_module
from scripts.agent_runtime_canary.collector import collect_canary_window, sample_from_audit
from scripts.agent_runtime_canary.gate import evaluate_canary_window
from scripts.agent_runtime_canary.models import (
    BackupParityRecord,
    CanaryEvidenceInputs,
    CanaryReleaseIdentity,
    CanaryTurnSample,
    CanaryWindowEvidence,
    QualityReview,
    RollbackRehearsalRecord,
    SafetyReview,
)
from src.db.models.harness import HarnessAgentTurnAuditRow
from src.db.models import Conversation, ConversationMessage, User


UTC = datetime.UTC
START = datetime.datetime(2026, 7, 1, tzinfo=UTC)
REVISION = "61cdacce996daefad98923abb1bc5793e3d3f7a0"
DB_REVISION = "074_pack_capability_permission"


def _identity() -> CanaryReleaseIdentity:
    return CanaryReleaseIdentity(
        application_revision=REVISION,
        database_revision=DB_REVISION,
        image_digest="sha256:" + "a" * 64,
        config_sha256="b" * 64,
        lockfile_sha256="c" * 64,
        sbom_sha256="d" * 64,
        manifest_sha256="e" * 64,
    )


def _backup() -> BackupParityRecord:
    return BackupParityRecord(
        backup_id="backup-prod-20260701",
        environment="production-canary",
        application_revision=REVISION,
        database_revision=DB_REVISION,
        created_at=START,
        restore_verified_at=START + datetime.timedelta(hours=1),
        parity_checks=["alembic-head", "agent-run-count", "workflow-ledger"],
        signer="operator-a",
        signature_sha256="1" * 64,
    )


def _rollback() -> RollbackRehearsalRecord:
    return RollbackRehearsalRecord(
        rehearsal_id="rollback-20260709",
        application_revision=REVISION,
        database_revision=DB_REVISION,
        from_stage="full_standard",
        started_at=START + datetime.timedelta(days=8),
        finished_at=START + datetime.timedelta(days=8, minutes=15),
        config_restored=True,
        health_check_passed=True,
        state_reconciled=True,
        duplicate_effects=0,
        signer="operator-b",
        evidence_uri="artifact://rollback/20260709",
        signature_sha256="2" * 64,
    )


def _complete_evidence() -> CanaryWindowEvidence:
    stages = (
        "internal_no_tool", "read_only", "planning",
        "reversible_effects", "workflow_approvals", "full_standard",
    )
    samples = [
        CanaryTurnSample(
            audit_id=index + 1,
            canary_stage=stages[index % len(stages)],
            runtime_cohort="v2_effect_primary" if index % len(stages) >= 3 else "v2_readonly_primary",
            transport="stream" if index % 2 else "nonstream",
            terminal_action="answer",
            stop_reason="answered",
            input_tokens=100,
            output_tokens=20,
            tool_calls=1,
            cost_usd=.01,
            latency_ms=1_000,
            observed_at=START + datetime.timedelta(seconds=index * (8 * 24 * 3600 / 999)),
        )
        for index in range(1_000)
    ]
    return CanaryWindowEvidence(
        identity=_identity(),
        samples=samples,
        safety=SafetyReview(
            unauthorized_effects=0,
            cross_scope_evidence=0,
            duplicate_effects=0,
            secret_exposures=0,
            unsupported_claims=0,
            reviewer="security-reviewer",
            reviewed_at=START + datetime.timedelta(days=9),
            evidence_uri="artifact://security/canary",
            signature_sha256="3" * 64,
        ),
        quality=QualityReview(
            reviewed_turns=200,
            passed_turns=192,
            denial_turns=20,
            correct_denials=20,
            no_evidence_turns=20,
            correct_no_evidence_turns=20,
            unexpected_clarifications=2,
            repeated_outputs=1,
            rubric_version="canary-quality-v1",
            reviewer="quality-reviewer",
            reviewed_at=START + datetime.timedelta(days=9),
            evidence_uri="artifact://quality/canary",
            signature_sha256="4" * 64,
        ),
        backup=_backup(),
        rollback=_rollback(),
        restart_suite_passed=True,
        transport_parity_passed=True,
        full_cutover_owner="product-owner",
        full_cutover_decision="accept",
    )


def test_complete_verified_live_window_can_go() -> None:
    verdict = evaluate_canary_window(_complete_evidence())
    assert verdict.verdict == "GO"
    assert verdict.blockers == []
    assert verdict.total_turns == 1_000
    assert verdict.consecutive_hours >= 168


def test_incomplete_template_fails_closed_with_all_major_blockers() -> None:
    evidence = _complete_evidence()
    evidence.samples = evidence.samples[:10]
    evidence.backup = None
    evidence.rollback = None
    evidence.restart_suite_passed = False
    evidence.transport_parity_passed = False
    evidence.full_cutover_owner = None
    evidence.full_cutover_decision = None
    verdict = evaluate_canary_window(evidence)
    assert verdict.verdict == "NO_GO"
    text = " ".join(verdict.blockers)
    assert "10/1000" in text
    assert "168 hours" in text
    assert "backup" in text
    assert "rollback" in text
    assert "owner approval" in text


def test_any_absolute_safety_event_overrides_perfect_quality() -> None:
    evidence = _complete_evidence()
    evidence.safety.unauthorized_effects = 1
    evidence.samples[0].stale_workflow = True
    verdict = evaluate_canary_window(evidence)
    assert verdict.verdict == "NO_GO"
    assert any("unauthorized effects" in item for item in verdict.blockers)
    assert any("stale workflow" in item for item in verdict.blockers)


def test_cost_latency_and_quality_bounds_are_enforced() -> None:
    evidence = _complete_evidence()
    evidence.quality.passed_turns = 189
    evidence.quality.correct_denials = 19
    evidence.quality.unexpected_clarifications = 7
    evidence.quality.repeated_outputs = 3
    for sample in evidence.samples[940:]:
        sample.latency_ms = 31_000
        sample.cost_usd = .26
    verdict = evaluate_canary_window(evidence)
    text = " ".join(verdict.blockers)
    assert "quality below" in text
    assert "denial correctness" in text
    assert "clarification" in text
    assert "repeat rate" in text
    assert "p95 latency" in text
    assert "cost bounds" in text


def test_operator_records_reject_placeholder_and_identity_mismatch() -> None:
    with pytest.raises(ValidationError, match="verified environment evidence"):
        _backup().model_copy(update={"signer": "TBD"}).__class__.model_validate(
            _backup().model_copy(update={"signer": "TBD"}).model_dump()
        )
    evidence = _complete_evidence().model_dump()
    evidence["backup"]["application_revision"] = "different-release"
    with pytest.raises(ValidationError, match="application revision mismatch"):
        CanaryWindowEvidence.model_validate(evidence)


def test_collector_rejects_pre_attribution_rows_and_maps_safe_fields() -> None:
    base = dict(
        id=7,
        route="agent",
        transport="stream",
        terminal_action="answer",
        stop_reason="answered",
        capability_ids=["safe.read"],
        observation_statuses=["succeeded"],
        input_tokens=10,
        output_tokens=5,
        tool_calls=1,
        cost_usd=.001,
        latency_ms=100,
        created_at=START,
    )
    with pytest.raises(ValueError, match="predates required"):
        sample_from_audit(SimpleNamespace(**base, flags={}))
    sample = sample_from_audit(SimpleNamespace(
        **base,
        flags={
            "canary_stage": "read_only",
            "runtime_cohort": "v2_readonly_primary",
            "clarification": False,
        },
    ))
    assert sample.audit_id == 7
    assert sample.capability_ids == ["safe.read"]
    assert sample.model_dump().keys().isdisjoint({"query", "answer", "prompt"})


@pytest.mark.asyncio
async def test_collector_rejects_windows_above_hard_limit(monkeypatch) -> None:
    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [object()]

    class _Database:
        async def execute(self, _statement):
            return _Rows()

    monkeypatch.setattr(collector_module, "MAX_CANARY_AUDITS", 0)
    with pytest.raises(ValueError, match="exceeds the 0 audit collection limit"):
        await collect_canary_window(
            _Database(),
            started_at=START,
            ended_at=START + datetime.timedelta(hours=1),
            inputs=CanaryEvidenceInputs(
                identity=_identity(),
                safety=_complete_evidence().safety,
                quality=_complete_evidence().quality,
            ),
        )


@pytest.mark.asyncio
async def test_database_collector_reads_only_attributed_audit_window(db_session) -> None:
    user = User(username="canary-collector", email="canary-collector@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Canary collector", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="content must not enter evidence",
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        HarnessAgentTurnAuditRow(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            user_message_id=message.id,
            assistant_message_id=None,
            route="agent",
            cohort_source="flag_readonly_canary",
            cohort_bucket=4,
            transport="stream",
            state_version="state-1",
            terminal_action="answer",
            stop_reason="answered",
            capability_ids=["safe.read"],
            observation_statuses=["succeeded"],
            response_fingerprint="a" * 64,
            flags={
                "canary_stage": "read_only",
                "runtime_cohort": "v2_readonly_primary",
            },
            input_tokens=10,
            output_tokens=5,
            tool_calls=1,
            cost_usd=.001,
            latency_ms=100,
            shadow_result=None,
            created_at=START + datetime.timedelta(hours=1),
        )
    )
    await db_session.flush()
    inputs = CanaryEvidenceInputs(
        identity=_identity(),
        safety=SafetyReview(
            unauthorized_effects=0,
            cross_scope_evidence=0,
            duplicate_effects=0,
            secret_exposures=0,
            unsupported_claims=0,
            reviewer="reviewer",
            reviewed_at=START,
            evidence_uri="artifact://security",
            signature_sha256="3" * 64,
        ),
        quality=QualityReview(
            reviewed_turns=0,
            passed_turns=0,
            denial_turns=0,
            correct_denials=0,
            no_evidence_turns=0,
            correct_no_evidence_turns=0,
            unexpected_clarifications=0,
            repeated_outputs=0,
            rubric_version="canary-quality-v1",
            reviewer="reviewer",
            reviewed_at=START,
            evidence_uri="artifact://quality",
            signature_sha256="4" * 64,
        ),
    )
    evidence = await collect_canary_window(
        db_session,
        started_at=START,
        ended_at=START + datetime.timedelta(hours=2),
        inputs=inputs,
    )
    assert len(evidence.samples) == 1
    assert evidence.samples[0].runtime_cohort == "v2_readonly_primary"
