"""Isolated tests for deterministic Slice 10 resolution contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.harness.resolution import (
    ConfidenceLevel,
    ConversationalFocus,
    EntityCandidate,
    EntityReference,
    EntityType,
    MatchReason,
    ResolutionStatus,
    SlotValueResolutionStatus,
    TimeWindowKind,
    WorkflowKind,
    merge_conversational_focus,
    normalize_reference_text,
    resolve_continuation_slot_value,
    resolve_entity,
    resolve_time_window,
    resolve_workflow_kind,
)


def candidate(
    stable_id: str,
    name: str,
    *aliases: str,
    entity_type: EntityType = EntityType.PROJECT,
    workflow_kind: WorkflowKind | None = None,
) -> EntityCandidate:
    return EntityCandidate(
        entity_type=entity_type,
        stable_id=stable_id,
        canonical_name=name,
        aliases=aliases,
        workflow_kind=workflow_kind,
    )


def test_normalization_is_case_and_punctuation_insensitive() -> None:
    assert normalize_reference_text("  Re-Ingest_DRILL!! ") == "re ingest_drill"


def test_exact_canonical_project_name_resolves_to_stable_reference() -> None:
    result = resolve_entity(
        "reingestdrill",
        EntityType.PROJECT,
        [candidate("project-17", "reingestdrill", "Reingest Drill")],
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.reference is not None
    assert result.reference.stable_id == "project-17"
    assert result.confidence is not None
    assert result.confidence.score == 0.99
    assert result.confidence.level is ConfidenceLevel.EXACT
    assert result.confidence.reason is MatchReason.CANONICAL_NAME


def test_compact_project_reference_resolves_hyphenated_stable_id() -> None:
    result = resolve_entity(
        "generate docs for reingestdrill",
        EntityType.PROJECT,
        [candidate("reingest-drill", "Reingest Drill")],
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.reference is not None
    assert result.reference.stable_id == "reingest-drill"


def test_stable_id_has_priority_over_display_name_matches() -> None:
    result = resolve_entity(
        "project-17",
        EntityType.PROJECT,
        [
            candidate("project-17", "Canonical Project"),
            candidate("other", "project-17"),
        ],
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.reference is not None
    assert result.reference.stable_id == "project-17"
    assert result.confidence is not None
    assert result.confidence.reason is MatchReason.STABLE_ID


def test_alias_and_embedded_name_resolution() -> None:
    inventory = [candidate("project-ml-sleep", "Machine Learning Sleep", "ML Sleep")]

    alias = resolve_entity("ML Sleep", EntityType.PROJECT, inventory)
    embedded = resolve_entity("what is the status of ML Sleep?", EntityType.PROJECT, inventory)

    assert alias.status is ResolutionStatus.RESOLVED
    assert alias.confidence is not None
    assert alias.confidence.reason is MatchReason.ALIAS
    assert embedded.status is ResolutionStatus.RESOLVED
    assert embedded.confidence is not None
    assert embedded.confidence.reason is MatchReason.EMBEDDED_NAME


def test_prefix_tie_returns_one_focused_ambiguity() -> None:
    result = resolve_entity(
        "orders",
        EntityType.WORKFLOW,
        [
            candidate("wf-a", "Orders Daily", entity_type=EntityType.WORKFLOW),
            candidate("wf-b", "Orders Delta", entity_type=EntityType.WORKFLOW),
            candidate("wf-c", "Billing Daily", entity_type=EntityType.WORKFLOW),
        ],
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.reference is None
    assert result.ambiguity is not None
    assert result.ambiguity.prompt == "Which workflow did you mean?"
    assert [option.value for option in result.ambiguity.options] == ["wf-a", "wf-b"]
    assert [match.reference.stable_id for match in result.matches] == ["wf-a", "wf-b"]


def test_duplicate_exact_aliases_are_ambiguous_and_sorted_deterministically() -> None:
    result = resolve_entity(
        "sales",
        EntityType.PROJECT,
        [
            candidate("z-project", "West Sales", "sales"),
            candidate("a-project", "East Sales", "sales"),
        ],
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.ambiguity is not None
    assert [option.label for option in result.ambiguity.options] == ["East Sales", "West Sales"]


def test_unresolved_entity_does_not_guess_or_return_inventory() -> None:
    result = resolve_entity(
        "unknown project",
        EntityType.PROJECT,
        [candidate("project-17", "reingestdrill")],
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.reference is None
    assert result.matches == ()


def test_resolver_only_considers_requested_entity_type() -> None:
    result = resolve_entity(
        "production",
        EntityType.ENVIRONMENT,
        [
            candidate("project-prod", "production"),
            candidate(
                "env-prod",
                "Production",
                entity_type=EntityType.ENVIRONMENT,
            ),
        ],
    )

    assert result.reference is not None
    assert result.reference.stable_id == "env-prod"


def test_empty_query_and_invalid_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="query must contain"):
        resolve_entity("---", EntityType.PROJECT, [])
    with pytest.raises(ValueError, match="score thresholds"):
        resolve_entity("project", EntityType.PROJECT, [], minimum_score=1.1)


def test_candidate_workflow_kind_is_preserved_in_reference() -> None:
    result = resolve_entity(
        "generation 41",
        EntityType.WORKFLOW_RUN,
        [
            candidate(
                "41",
                "Generation 41",
                entity_type=EntityType.WORKFLOW_RUN,
                workflow_kind=WorkflowKind.DOCUMENTATION_GENERATION,
            )
        ],
    )

    assert result.reference is not None
    assert result.reference.workflow_kind is WorkflowKind.DOCUMENTATION_GENERATION


def test_workflow_kind_resolves_explicit_status_language() -> None:
    result = resolve_workflow_kind("Did the documentation generation finish?")

    assert result.status is ResolutionStatus.RESOLVED
    assert result.workflow_kind is WorkflowKind.DOCUMENTATION_GENERATION
    assert result.confidence is not None
    assert result.confidence.level is ConfidenceLevel.EXACT


def test_workflow_kind_uses_typed_conversational_focus_for_is_it_done() -> None:
    focus = ConversationalFocus(workflow_kind=WorkflowKind.SCAN)

    result = resolve_workflow_kind("is it done?", focus=focus)

    assert result.status is ResolutionStatus.RESOLVED
    assert result.workflow_kind is WorkflowKind.SCAN
    assert result.confidence is not None
    assert result.confidence.reason is MatchReason.CONVERSATIONAL_FOCUS


def test_workflow_kind_does_not_conflate_multiple_available_statuses() -> None:
    result = resolve_workflow_kind(
        "is the project done?",
        available_kinds=[
            WorkflowKind.ONBOARDING,
            WorkflowKind.DOCUMENTATION_GENERATION,
            WorkflowKind.DOCUMENTATION_QUALITY,
        ],
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.ambiguity is not None
    assert result.ambiguity.prompt == "Which workflow status do you mean?"
    assert [option.value for option in result.ambiguity.options] == [
        "onboarding",
        "documentation_generation",
        "documentation_quality",
    ]


def test_only_available_workflow_kind_resolves_with_medium_confidence() -> None:
    result = resolve_workflow_kind(
        "is it done?",
        available_kinds=[WorkflowKind.INDEXING],
    )

    assert result.workflow_kind is WorkflowKind.INDEXING
    assert result.confidence is not None
    assert result.confidence.reason is MatchReason.ONLY_AVAILABLE
    assert result.confidence.level is ConfidenceLevel.MEDIUM


def test_workflow_kind_is_unresolved_without_signal_focus_or_inventory() -> None:
    result = resolve_workflow_kind("is it done?")

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.workflow_kind is None


def test_conversational_focus_rejects_mistyped_references() -> None:
    wrong_reference = EntityReference(
        entity_type=EntityType.DOCUMENT,
        stable_id="doc-1",
        canonical_name="Overview",
    )

    with pytest.raises(ValidationError, match="project must reference project"):
        ConversationalFocus(project=wrong_reference)


def test_focus_merge_preserves_prior_scope_and_adopts_workflow_kind() -> None:
    project = EntityReference(
        entity_type=EntityType.PROJECT,
        stable_id="project-17",
        canonical_name="reingestdrill",
    )
    run = EntityReference(
        entity_type=EntityType.WORKFLOW_RUN,
        stable_id="41",
        canonical_name="Generation 41",
        workflow_kind=WorkflowKind.DOCUMENTATION_GENERATION,
    )

    focus = merge_conversational_focus(ConversationalFocus(project=project), references=[run])

    assert focus.project == project
    assert focus.workflow_run == run
    assert focus.workflow_kind is WorkflowKind.DOCUMENTATION_GENERATION


def test_focus_merge_rejects_non_focus_reference_types() -> None:
    source = EntityReference(
        entity_type=EntityType.SOURCE,
        stable_id="source-1",
        canonical_name="main repository",
    )

    with pytest.raises(ValueError, match="cannot be conversational focus"):
        merge_conversational_focus(references=[source])


NOW = datetime(2026, 7, 12, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize("prefix", ["past", "last"])
@pytest.mark.parametrize(
    ("amount", "unit", "expected_delta"),
    [
        (24, "hours", timedelta(hours=24)),
        (2, "hrs", timedelta(hours=2)),
        (7, "days", timedelta(days=7)),
    ],
)
def test_relative_windows_use_exact_utc_duration(
    prefix: str,
    amount: int,
    unit: str,
    expected_delta: timedelta,
) -> None:
    window = resolve_time_window(f"{prefix} {amount} {unit}", now=NOW)

    assert window.kind is TimeWindowKind.RELATIVE
    assert window.end == NOW
    assert window.start == NOW - expected_delta


def test_past_24_hours_excludes_stale_and_end_boundary_evidence() -> None:
    window = resolve_time_window("past 24 hours", now=NOW)

    assert window.contains(NOW - timedelta(hours=23, minutes=59))
    assert window.contains(NOW - timedelta(hours=24))
    assert not window.contains(NOW - timedelta(hours=24, microseconds=1))
    assert not window.contains(NOW)


@pytest.mark.parametrize(
    ("expression", "expected_delta"),
    [
        ("24 hours", timedelta(hours=24)),
        ("24 h", timedelta(hours=24)),
        ("2 days", timedelta(days=2)),
        ("  24   H  ", timedelta(hours=24)),
        ("LAST 2 DAYS", timedelta(days=2)),
    ],
)
def test_pending_time_window_slot_accepts_contextual_duration_values(
    expression: str,
    expected_delta: timedelta,
) -> None:
    result = resolve_continuation_slot_value("time_window", expression, now=NOW)

    assert result.status is SlotValueResolutionStatus.RESOLVED
    assert result.value is not None
    assert result.value.end == NOW
    assert result.value.start == NOW - expected_delta


def test_bare_duration_is_not_a_general_time_window_expression() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        resolve_time_window("24 hours", now=NOW)


@pytest.mark.parametrize("expression", ["23", "soon", "0 h", ""])
def test_pending_time_window_slot_rejects_incomplete_or_invalid_values(
    expression: str,
) -> None:
    result = resolve_continuation_slot_value("time_window", expression, now=NOW)

    assert result.status is SlotValueResolutionStatus.INVALID
    assert result.value is None
    assert result.clarification is not None
    assert '"24 hours"' in result.clarification
    assert '"2 days"' in result.clarification


def test_unknown_continuation_slot_is_left_to_its_domain_resolver() -> None:
    result = resolve_continuation_slot_value("project_id", "alitalend", now=NOW)

    assert result.status is SlotValueResolutionStatus.NOT_APPLICABLE
    assert result.value is None


def test_today_is_midnight_utc_through_now() -> None:
    window = resolve_time_window("today", now=NOW)

    assert window.kind is TimeWindowKind.CALENDAR_DAY
    assert window.start == datetime(2026, 7, 12, tzinfo=UTC)
    assert window.end == NOW


def test_yesterday_is_previous_utc_calendar_day() -> None:
    window = resolve_time_window("yesterday", now=NOW)

    assert window.start == datetime(2026, 7, 11, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 12, tzinfo=UTC)


def test_explicit_iso_slash_bounds_are_normalized_to_utc() -> None:
    window = resolve_time_window("2026-07-12T10:00:00+02:00/2026-07-12T12:00:00+02:00")

    assert window.kind is TimeWindowKind.EXPLICIT
    assert window.start == datetime(2026, 7, 12, 8, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 12, 10, tzinfo=UTC)


def test_explicit_keyword_bounds_accept_datetimes_and_z_suffix() -> None:
    window = resolve_time_window(
        start=datetime(2026, 7, 12, 10, tzinfo=timezone(timedelta(hours=2))),
        end="2026-07-12T11:00:00Z",
    )

    assert window.start == datetime(2026, 7, 12, 8, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 12, 11, tzinfo=UTC)


def test_explicit_iso_date_bounds_are_utc_midnights() -> None:
    window = resolve_time_window("2026-07-01/2026-07-03")

    assert window.start == datetime(2026, 7, 1, tzinfo=UTC)
    assert window.end == datetime(2026, 7, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "2026-07-12T10:00:00Z"},
        {
            "start": "2026-07-12T12:00:00Z",
            "end": "2026-07-12T10:00:00Z",
        },
        {"start": "2026-07-12T10:00:00", "end": "2026-07-12T11:00:00Z"},
    ],
)
def test_invalid_explicit_bounds_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        resolve_time_window(**kwargs)


def test_zero_relative_amount_and_unsupported_expression_are_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        resolve_time_window("last 0 days", now=NOW)
    with pytest.raises(ValueError, match="unsupported"):
        resolve_time_window("recently", now=NOW)


def test_time_window_rejects_naive_candidate_timestamp() -> None:
    window = resolve_time_window("past 24 hours", now=NOW)

    with pytest.raises(ValueError, match="candidate timestamp"):
        window.contains(datetime(2026, 7, 12, 10))
