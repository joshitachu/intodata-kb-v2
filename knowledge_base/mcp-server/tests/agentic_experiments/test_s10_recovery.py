"""Offline recovery-state tests; no provider, database, or artifact store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mcp_server.experiments.application.recover_experiment import (
    ExperimentRecoverySnapshot,
    RecoveryState,
    SanitizedLeaseSnapshot,
    SanitizedTaskSnapshot,
    SanitizedTaskStatus,
    derive_recovery,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _snapshot(*tasks: SanitizedTaskSnapshot) -> ExperimentRecoverySnapshot:
    return ExperimentRecoverySnapshot(experiment_id="candidate-42", tasks=tuple(tasks))


def test_all_successes_complete_only_with_one_accepted_claim_per_required_output() -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="research",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("research.json",),
                accepted_output_claims=("research.json",),
            ),
            SanitizedTaskSnapshot(
                task_id="render",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("document.md",),
                accepted_output_claims=("document.md",),
            ),
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.COMPLETED
    assert decision.missing_required_outputs == ()
    assert decision.duplicate_required_outputs == ()
    assert not decision.publish_permitted


@pytest.mark.parametrize(
    ("claims", "missing", "duplicate"),
    [
        ((), ("document.md",), ()),
        (("document.md", "document.md"), (), ("document.md",)),
    ],
)
def test_missing_or_duplicate_claim_blocks_completed_state(
    claims: tuple[str, ...], missing: tuple[str, ...], duplicate: tuple[str, ...]
) -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="render",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("document.md",),
                accepted_output_claims=claims,
            )
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.FAILED
    assert decision.missing_required_outputs == missing
    assert decision.duplicate_required_outputs == duplicate


def test_output_claim_cannot_satisfy_another_tasks_required_output() -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="research",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("research.json",),
                accepted_output_claims=("document.md",),
            ),
            SanitizedTaskSnapshot(
                task_id="render",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("document.md",),
            ),
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.FAILED
    assert decision.missing_required_outputs == ("document.md", "research.json")


def test_non_document_receipts_are_declared_outputs_required_for_completion() -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="render",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("document.md", "validation-receipt.json"),
                accepted_output_claims=("document.md",),
            )
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.FAILED
    assert decision.missing_required_outputs == ("validation-receipt.json",)


def test_active_lease_is_running_but_expired_without_external_call_is_only_queued() -> None:
    active = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="agent",
                status=SanitizedTaskStatus.LEASED,
                lease=SanitizedLeaseSnapshot(expires_at=NOW + timedelta(seconds=1), external_call_may_have_started=True),
            )
        ),
        now=NOW,
    )
    assert active.state == RecoveryState.RUNNING

    persisted_running = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="agent",
                status=SanitizedTaskStatus.RUNNING,
                lease=SanitizedLeaseSnapshot(expires_at=NOW + timedelta(seconds=1), external_call_may_have_started=True),
            )
        ),
        now=NOW,
    )
    assert persisted_running.state == RecoveryState.RUNNING

    safe_expired = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="agent",
                status=SanitizedTaskStatus.LEASED,
                lease=SanitizedLeaseSnapshot(expires_at=NOW, external_call_may_have_started=False),
            )
        ),
        now=NOW,
    )
    assert safe_expired.state == RecoveryState.QUEUED
    assert safe_expired.task_recovery[0].reason == "expired_lease_no_external_call"


def test_ambiguous_expired_external_call_fails_and_requires_manual_retry_without_retrying() -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="agent",
                status=SanitizedTaskStatus.LEASED,
                lease=SanitizedLeaseSnapshot(expires_at=NOW, external_call_may_have_started=True),
            )
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.FAILED
    assert decision.manual_retry_task_ids == ("agent",)
    assert decision.task_recovery[0].reason == "expired_lease_external_call_ambiguous"


@pytest.mark.parametrize(
    "status",
    [SanitizedTaskStatus.FAILED, SanitizedTaskStatus.SKIPPED, SanitizedTaskStatus.CANCELLED],
)
def test_failed_skipped_or_cancelled_task_blocks_completion_even_when_other_outputs_exist(status: SanitizedTaskStatus) -> None:
    decision = derive_recovery(
        snapshot=_snapshot(
            SanitizedTaskSnapshot(
                task_id="research",
                status=status,
                declared_outputs=("research.json",),
                accepted_output_claims=("research.json",),
            ),
            SanitizedTaskSnapshot(
                task_id="render",
                status=SanitizedTaskStatus.SUCCEEDED,
                declared_outputs=("document.md",),
                accepted_output_claims=("document.md",),
            ),
        ),
        now=NOW,
    )
    assert decision.state == RecoveryState.FAILED
    assert decision.missing_required_outputs == ("research.json",)


def test_snapshot_contract_rejects_unsanitized_lease_shape() -> None:
    with pytest.raises(ValueError, match="leased or running task requires"):
        SanitizedTaskSnapshot(task_id="task", status=SanitizedTaskStatus.LEASED)
    with pytest.raises(ValueError, match="leased or running task requires"):
        SanitizedTaskSnapshot(task_id="task", status=SanitizedTaskStatus.RUNNING)
    with pytest.raises(ValueError, match="UTC-aware"):
        SanitizedLeaseSnapshot(expires_at=NOW.replace(tzinfo=None), external_call_may_have_started=False)
