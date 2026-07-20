from __future__ import annotations

import datetime

import pytest

from src.harness.models import WorkflowChildReceipt, WorkflowRun, WorkflowRunStatus
from src.harness.workflow_projection import (
    AuthoritativeWorkflowCardProjector,
    WorkflowCardStatus,
    WorkflowCardTone,
    project_workflow_card,
)
from src.harness import HarnessRunNotFoundError


NOW = datetime.datetime(2026, 7, 19, 2, 0, tzinfo=datetime.UTC)
STARTED = datetime.datetime(2026, 7, 19, 1, 58, tzinfo=datetime.UTC)


def run(status: WorkflowRunStatus, **changes) -> WorkflowRun:  # noqa: ANN003
    values = {
        "id": 44,
        "conversation_id": 13,
        "user_id": 7,
        "project_id": "alpha",
        "intent_mode": "workflow",
        "capability_id": "mcp.docs.generate_project_documentation",
        "provider_id": "nexus.harness",
        "status": status,
        "started_at": STARTED,
    }
    values.update(changes)
    return WorkflowRun(**values)


@pytest.mark.parametrize(
    ("ledger_status", "card_status", "label", "tone", "active"),
    [
        (WorkflowRunStatus.PENDING, "queued", "Queued", "neutral", True),
        (WorkflowRunStatus.RUNNING, "in_progress", "In progress", "progress", True),
        (WorkflowRunStatus.TRIGGERED, "in_progress", "In progress", "progress", True),
        (WorkflowRunStatus.INTERRUPTED, "needs_input", "Needs input", "warning", True),
        (WorkflowRunStatus.SUCCEEDED, "succeeded", "Complete", "success", False),
        (WorkflowRunStatus.FAILED, "failed", "Failed", "danger", False),
        (WorkflowRunStatus.CANCELLED, "cancelled", "Cancelled", "muted", False),
        (
            WorkflowRunStatus.INDETERMINATE,
            "indeterminate",
            "Outcome unconfirmed",
            "warning",
            False,
        ),
    ],
)
def test_backend_owns_the_finite_status_display_and_active_contract(
    ledger_status, card_status, label, tone, active  # noqa: ANN001
) -> None:
    item = run(
        ledger_status,
        finished_at=(NOW if not active else None),
    )
    card = project_workflow_card(item, projected_at=NOW)

    assert card.status is WorkflowCardStatus(card_status)
    assert card.display_label == label
    assert card.display_tone is WorkflowCardTone(tone)
    assert card.is_active is active


def test_terminal_elapsed_uses_finish_and_never_projection_now() -> None:
    finished = STARTED + datetime.timedelta(seconds=12)
    card = project_workflow_card(
        run(WorkflowRunStatus.FAILED, finished_at=finished), projected_at=NOW
    )

    assert card.elapsed_ms == 12_000
    assert card.finished_at == finished
    assert card.timing_complete is True


def test_missing_terminal_timestamp_is_zero_and_explicitly_incomplete() -> None:
    card = project_workflow_card(run(WorkflowRunStatus.FAILED), projected_at=NOW)

    assert card.elapsed_ms == 0
    assert card.finished_at is None
    assert card.timing_complete is False


def test_exact_terminal_child_timestamp_is_deterministic_fallback() -> None:
    child_finished = STARTED + datetime.timedelta(seconds=30)
    child = WorkflowChildReceipt(
        id=9,
        workflow_run_id=44,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id="provider-secret-id",
        status="failed",
        provider_status="failed",
        version=1,
        provider_finished_at=child_finished,
        last_observed_at=child_finished,
        last_observation_source="poll",
        error_message="Scanner failed.",
    )
    card = project_workflow_card(
        run(WorkflowRunStatus.FAILED, children=[child]), projected_at=NOW
    )

    assert card.elapsed_ms == 30_000
    assert card.children[0].status == "failed"
    assert "provider-secret-id" not in card.model_dump_json()


def test_artifact_links_are_sanitized_and_javascript_is_rejected() -> None:
    card = project_workflow_card(
        run(
            WorkflowRunStatus.SUCCEEDED,
            finished_at=NOW,
            artifact_links=[
                {"label": "Docs", "url": "/projects/alpha/docs", "kind": "docs"},
                {"label": "Unsafe", "url": "javascript:alert(1)"},
                {"label": "Secret", "url": "/docs?access_token=hidden"},
            ],
        ),
        projected_at=NOW,
    )

    assert card.artifact_links == [
        {"label": "Docs", "url": "/projects/alpha/docs", "kind": "docs"}
    ]


def test_unknown_child_status_normalizes_to_finite_unknown() -> None:
    child = WorkflowChildReceipt(
        id=9,
        workflow_run_id=44,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id="provider-id",
        status="provider-invented-state",
        provider_status="provider-invented-state",
        version=1,
        last_observed_at=NOW,
        last_observation_source="poll",
    )

    card = project_workflow_card(
        run(WorkflowRunStatus.RUNNING, children=[child]), projected_at=NOW
    )

    assert card.children[0].status == "unknown"


def test_child_and_artifact_display_labels_reject_secret_bearing_values() -> None:
    child = WorkflowChildReceipt(
        id=9,
        workflow_run_id=44,
        provider_id="mcp.docs",
        child_kind="token=child-secret",
        external_run_id="provider-id",
        status="running",
        provider_status="running",
        version=1,
        last_observed_at=NOW,
        last_observation_source="poll",
    )
    card = project_workflow_card(
        run(
            WorkflowRunStatus.RUNNING,
            children=[child],
            artifact_links=[
                {
                    "label": "password=label-secret",
                    "url": "/docs",
                    "kind": "token=kind-secret",
                }
            ],
        ),
        projected_at=NOW,
    )
    payload = card.model_dump_json()

    assert "child-secret" not in payload
    assert "label-secret" not in payload
    assert "kind-secret" not in payload
    assert card.children[0].kind == "workflow child"
    assert card.artifact_links[0]["label"] == "Artifact"
    assert card.artifact_links[0]["kind"] == "artifact"


class FakeRepository:
    def __init__(self, runs):  # noqa: ANN001
        self.runs = runs
        self.calls = []

    async def get_run(self, scope, run_id, *, owner_user_id=None):  # noqa: ANN001
        self.calls.append((scope, run_id, owner_user_id))
        item = self.runs.get(run_id)
        if item is None or item.user_id != owner_user_id:
            raise HarnessRunNotFoundError
        if scope is not None and item.project_id not in scope:
            raise HarnessRunNotFoundError
        return item


async def test_projector_deduplicates_reconciles_exact_run_and_reloads_terminal_truth() -> None:
    active = run(WorkflowRunStatus.RUNNING)
    terminal = run(
        WorkflowRunStatus.FAILED,
        finished_at=STARTED + datetime.timedelta(seconds=12),
        error_message="Exact child failed.",
    )
    repository = FakeRepository({44: active})
    reconciled = []

    async def reconcile_exact(item, access_token):  # noqa: ANN001
        reconciled.append((item.id, access_token))
        repository.runs[44] = terminal
        return active

    cards = await AuthoritativeWorkflowCardProjector(
        repository,
        reconcile_run=reconcile_exact,
        now=lambda: NOW,
    ).project_many(
        [44, 44],
        owner_user_id=7,
        conversation_id=13,
        project_scope=["alpha"],
        access_token="server-owned-token",
        reconcile=True,
    )

    assert reconciled == [(44, "server-owned-token")]
    assert len(cards) == 1
    assert cards[0].status == "failed"
    assert cards[0].elapsed_ms == 12_000


async def test_projector_denies_cross_conversation_and_scope_without_existence_leak() -> None:
    other_conversation = run(WorkflowRunStatus.RUNNING).model_copy(
        update={"conversation_id": 99}
    )
    repository = FakeRepository({44: other_conversation})
    projector = AuthoritativeWorkflowCardProjector(repository, now=lambda: NOW)

    assert (
        await projector.project_many(
            [44, 999],
            owner_user_id=7,
            conversation_id=13,
            project_scope=["alpha"],
            access_token=None,
            reconcile=False,
        )
        == []
    )


async def test_provider_unavailable_projects_last_canonical_ledger_fact() -> None:
    active = run(WorkflowRunStatus.RUNNING)
    repository = FakeRepository({44: active})

    async def unavailable(item, access_token):  # noqa: ANN001
        del item, access_token
        raise TimeoutError

    cards = await AuthoritativeWorkflowCardProjector(
        repository,
        reconcile_run=unavailable,
        now=lambda: NOW,
    ).project_many(
        [44],
        owner_user_id=7,
        conversation_id=13,
        project_scope=["alpha"],
        access_token=None,
        reconcile=True,
    )

    assert cards[0].status == "in_progress"
    assert cards[0].is_active is True


async def test_indeterminate_card_read_does_not_repeat_provider_poll() -> None:
    uncertain = run(
        WorkflowRunStatus.INDETERMINATE,
        finished_at=STARTED + datetime.timedelta(seconds=45),
    )
    repository = FakeRepository({44: uncertain})
    calls = 0

    async def must_not_poll(item, access_token):  # noqa: ANN001
        del item, access_token
        nonlocal calls
        calls += 1
        raise AssertionError("indeterminate cards are terminal display facts")

    cards = await AuthoritativeWorkflowCardProjector(
        repository,
        reconcile_run=must_not_poll,
        now=lambda: NOW,
    ).project_many(
        [44],
        owner_user_id=7,
        conversation_id=13,
        project_scope=["alpha"],
        access_token=None,
        reconcile=True,
    )

    assert calls == 0
    assert cards[0].status == "indeterminate"
    assert cards[0].is_active is False
