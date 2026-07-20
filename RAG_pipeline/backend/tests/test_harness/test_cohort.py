from __future__ import annotations

from src.harness.cohort import (
    CohortSelector,
    RuntimeCohort,
    stable_user_bucket,
)


def _selector(**overrides) -> CohortSelector:
    base = {
        "enabled": True,
        "readonly_users": [7],
        "readonly_percent": 0,
        "transports": ["nonstream", "stream"],
    }
    base.update(overrides)
    return CohortSelector(**base)


def test_disabled_routes_every_turn_to_legacy_with_real_source() -> None:
    selector = _selector(enabled=False)
    decision = selector.select(
        user_id=7, transport="stream", manifest_has_consequential=False
    )
    assert decision.cohort is RuntimeCohort.LEGACY
    assert decision.cohort_source == "legacy_default"
    assert decision.cohort_source != "single_path"
    assert decision.reason_code == "v2_master_switch_off"
    assert decision.cohort_bucket == stable_user_bucket(7)


def test_allowlisted_readonly_user_routes_to_v2() -> None:
    selector = _selector(readonly_users=[7])
    decision = selector.select(
        user_id=7, transport="stream", manifest_has_consequential=False
    )
    assert decision.cohort is RuntimeCohort.V2_READONLY_PRIMARY
    assert decision.cohort_source == "flag_readonly_canary"
    assert decision.reason_code == "readonly_allowlist"


def test_user_not_in_allowlist_stays_legacy() -> None:
    selector = _selector(readonly_users=[7], readonly_percent=0)
    decision = selector.select(
        user_id=999_999, transport="stream", manifest_has_consequential=False
    )
    assert decision.cohort is RuntimeCohort.LEGACY
    assert decision.reason_code == "not_in_readonly_canary"


def test_consequential_manifest_never_routes_to_readonly() -> None:
    selector = _selector(readonly_users=[7])
    decision = selector.select(
        user_id=7, transport="stream", manifest_has_consequential=True
    )
    assert decision.cohort is RuntimeCohort.LEGACY
    assert decision.reason_code == "manifest_exposes_consequential_capability"


def test_disabled_transport_stays_legacy() -> None:
    selector = _selector(readonly_users=[7], transports=["nonstream"])
    decision = selector.select(
        user_id=7, transport="stream", manifest_has_consequential=False
    )
    assert decision.cohort is RuntimeCohort.LEGACY
    assert decision.reason_code == "transport_not_enabled"


def test_bucket_is_stable_and_content_independent() -> None:
    # The bucket depends only on the user id: identical user routes identically
    # regardless of message text (the selector never sees message text at all).
    assert stable_user_bucket(7) == stable_user_bucket(7)
    # Two different users can land in different buckets.
    buckets = {stable_user_bucket(user) for user in range(200)}
    assert len(buckets) > 1


def test_percentage_selects_by_stable_bucket_not_message() -> None:
    # A user whose bucket is below the percentage is selected; another whose
    # bucket is above is not. Identical for both regardless of any message.
    inside = next(user for user in range(10_000) if stable_user_bucket(user) < 10)
    outside = next(user for user in range(10_000) if stable_user_bucket(user) >= 10)
    selector = _selector(readonly_users=[], readonly_percent=10)
    assert (
        selector.select(
            user_id=inside, transport="stream", manifest_has_consequential=False
        ).cohort
        is RuntimeCohort.V2_READONLY_PRIMARY
    )
    assert (
        selector.select(
            user_id=outside, transport="stream", manifest_has_consequential=False
        ).cohort
        is RuntimeCohort.LEGACY
    )


def test_percentage_zero_selects_nobody() -> None:
    selector = _selector(readonly_users=[], readonly_percent=0)
    for user in range(50):
        assert (
            selector.select(
                user_id=user, transport="stream", manifest_has_consequential=False
            ).cohort
            is RuntimeCohort.LEGACY
        )


def test_decision_is_frozen_and_immutable() -> None:
    selector = _selector()
    decision = selector.select(
        user_id=7, transport="stream", manifest_has_consequential=False
    )
    import pydantic

    try:
        decision.cohort = RuntimeCohort.LEGACY  # type: ignore[misc]
    except (pydantic.ValidationError, AttributeError, TypeError):
        return
    raise AssertionError("CohortDecision must be immutable")
