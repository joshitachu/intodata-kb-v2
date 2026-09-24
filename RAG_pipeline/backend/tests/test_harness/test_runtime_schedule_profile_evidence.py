"""The clock-time half of `runtime.stored.query_evidence`.

The measured failure: asked which jobs run between 22:00 and 07:00, the agent
answered that the run times "zitten in de beheeromgeving (TMC) en zijn niet
meegeleverd" -- that they live in the runtime console and were not supplied.
They were. `runtime_executions` held 10,628 production runs for that project,
and the generated planning document had already counted 9,199 of them. What was
missing was a way to pool runs by local time of day: `list_executions` returns
flat rows capped at `limit`, and `get_runtime_outcome_trend` buckets along the
calendar, so neither can say when a job normally starts.

`list_runtime_job_schedule_profile` is that shape. These tests cover the
capability wiring -- the store and provider halves are pinned in
`tests/test_runtime/test_schedule_profile.py`.

The first test is the one that matters most, and it guards a silent failure
rather than a loud one: the operation dispatched and returned `succeeded` while
the summary renderer's key allowlist dropped every clock time, so the agent
received a schedule answer containing run counts, a duration, and no schedule.
An answer like that reads as confirmation that the times are unavailable, which
is the exact wrong conclusion this whole change exists to prevent.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.harness.evidence import (
    _RUNTIME_EVIDENCE_OPERATIONS,
    query_structured_runtime_evidence,
)
from src.harness.models import STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY
from src.runtime.models import RuntimeJobScheduleProfile

OPERATION = "list_runtime_job_schedule_profile"

#: One overnight job, with a median start after midnight and an earliest start
#: before it -- the wrap case, so a renderer that mangles the times has
#: something to mangle.
PROFILE = RuntimeJobScheduleProfile(
    project_id="acme",
    source_key="tmc",
    job_name="AIS_DWH_Patient_Split",
    timezone="Europe/Amsterdam",
    window_start_hour=22,
    window_end_hour=7,
    total_runs=192,
    distinct_days=66,
    median_start_time=datetime.time(0, 11, 35),
    earliest_start_time=datetime.time(23, 36, 36),
    latest_start_time=datetime.time(1, 2, 47),
    p50_duration_ms=2_151_600,
    non_success_runs=17,
)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, returns: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    method = _RUNTIME_EVIDENCE_OPERATIONS[OPERATION].provider_method

    async def provider_call(_self: Any, params: Any, context: Any) -> Any:
        seen["params"] = params
        seen["scope"] = context.project_scope
        seen["db"] = context.db
        return returns

    monkeypatch.setattr(
        f"src.harness.evidence.StoredRuntimeProvider.{method}", provider_call
    )
    return seen


async def _run(db: Any, **kwargs: Any) -> Any:
    """One capability call with this operation's defaults.

    `db` is required rather than defaulted to a stub: the entry point resolves
    the request's runtime read set against stored read grants before it dispatches,
    so a real session is part of the call now, not an optional dependency.
    """

    defaults: dict[str, Any] = {
        "operation": OPERATION,
        "project_scope": ["acme"],
        "db": db,
        "project_id": "acme",
    }
    return await query_structured_runtime_evidence(**{**defaults, **kwargs})


async def test_the_clock_times_survive_into_the_evidence_item(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """The regression guard. A `succeeded` result with no times is the bug.

    `_runtime_record_summary` renders through a key allowlist, so a model whose
    fields are absent from it serializes to the handful of keys that happen to
    overlap. Measured before the fix, this operation returned `job_name`,
    `project_id`, `source_key`, `total_runs` and `p50_duration_ms` -- every
    field except the four the question was about.
    """

    _patch_provider(monkeypatch, [PROFILE])

    result = await _run(db_session, timezone="Europe/Amsterdam", start_hour=22, end_hour=7)

    assert result.status == "succeeded"
    rendered = json.loads(result.items[0].summary)

    assert rendered["median_start_time"] == "00:11:35"
    assert rendered["earliest_start_time"] == "23:36:36"
    assert rendered["latest_start_time"] == "01:02:47"
    # The zone and the window travel WITH the times: "00:11:35" alone does not
    # say which clock it is on, and a reader cannot tell a nightly job from an
    # all-day one without knowing what was pooled.
    assert rendered["timezone"] == "Europe/Amsterdam"
    assert rendered["window_start_hour"] == 22
    assert rendered["window_end_hour"] == 7
    # Nights, not calendar dates -- the distinction the store makes.
    assert rendered["distinct_days"] == 66
    assert rendered["non_success_runs"] == 17


async def test_the_window_and_zone_reach_the_provider(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    seen = _patch_provider(monkeypatch, [PROFILE])

    await _run(
        db_session,
        timezone="Europe/Amsterdam",
        start_hour=22,
        end_hour=7,
        job_name="AIS_DWH_Patient_Split",
        limit=25,
    )

    params = seen["params"]
    assert params.timezone == "Europe/Amsterdam"
    assert params.start_hour == 22
    assert params.end_hour == 7
    assert params.job_name == "AIS_DWH_Patient_Split"
    assert params.limit == 25


async def test_a_half_specified_window_is_not_forwarded(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """One hour without the other is an argument error, not a different window.

    The input model rejects a half-specified window. Forwarding `start_hour`
    alone would either raise inside input construction or -- worse, had the
    model defaulted -- silently pool a different span than the caller asked for
    and label it with their number.
    """

    seen = _patch_provider(monkeypatch, [PROFILE])

    await _run(db_session, start_hour=22)

    assert seen["params"].start_hour is None
    assert seen["params"].end_hour is None


async def test_an_omitted_zone_leaves_the_models_own_default(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """No second default is hardcoded in the dispatch table."""

    seen = _patch_provider(monkeypatch, [PROFILE])

    await _run(db_session)

    assert seen["params"].timezone == "Europe/Amsterdam"


async def test_project_scope_reaches_the_provider_context(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """Scope is an access boundary: a schedule is operational client detail."""

    seen = _patch_provider(monkeypatch, [PROFILE])
    db = db_session

    await _run(db, project_scope=["acme"], project_id="acme")

    assert seen["scope"] == ["acme"]
    assert seen["db"] is db


async def test_an_out_of_scope_project_narrows_to_nothing(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """Asking outside the scope must narrow to empty, never widen to all."""

    seen = _patch_provider(monkeypatch, [PROFILE])

    await _run(db_session, project_scope=["acme"], project_id="other_client")

    assert seen["scope"] == []


def test_the_schema_accepts_the_window_arguments_and_rejects_bad_hours() -> None:
    validator = Draft202012Validator(
        STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.input_schema,
        format_checker=FormatChecker(),
    )

    validator.validate(
        {
            "operation": OPERATION,
            "project_id": "acme",
            "timezone": "Europe/Amsterdam",
            "start_hour": 22,
            "end_hour": 7,
        }
    )

    assert not validator.is_valid({"operation": OPERATION, "start_hour": 24})
    assert not validator.is_valid({"operation": OPERATION, "end_hour": -1})


async def test_a_clamped_read_says_so(monkeypatch: pytest.MonkeyPatch, db_session) -> None:
    """A capped list must not be able to read as the complete one.

    Measured against live data: `pluripharm` has 65 jobs in a 22:00-07:00
    window, the provider clamps every rollup to 25, and nothing in the result
    said so. An agent receiving 25 rows would publish them as the night
    schedule -- a truncated ranking presented as the estate, which is the same
    defect `EvidenceQueryResult.scope` was introduced to stop.
    """

    from src.runtime.providers.stored import aggregate_row_cap

    cap = aggregate_row_cap("list_runtime_job_schedule_profile")
    _patch_provider(monkeypatch, [PROFILE] * cap)

    result = await _run(db_session, limit=cap, timezone="Europe/Amsterdam", start_hour=22, end_hour=7)

    assert result.scope["records_returned"] == cap
    assert result.scope["may_be_truncated"] is True
    assert "not present this as the complete set" in result.scope["truncation_note"]
async def test_a_read_below_the_cap_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """The counterpart: a complete result must not carry a truncation warning.

    A warning on every read is a warning on none -- it would be ignored by the
    turn it is meant to inform.
    """

    _patch_provider(monkeypatch, [PROFILE])

    result = await _run(db_session, limit=25, job_name="AIS_DWH_Patient_Split")

    assert result.scope["records_returned"] == 1
    assert result.scope["may_be_truncated"] is False
    assert "truncation_note" not in result.scope


def test_every_raised_cap_names_a_real_rollup() -> None:
    """A typo in the cap table silently reverts that rollup to the default.

    The failure would be invisible: the operation still works, still answers,
    and is simply clamped where it was meant not to be -- which is how the nine
    hourly slices happened in the first place.
    """

    from src.runtime.providers.stored import _AGGREGATE_ROW_CAPS, StoredRuntimeProvider

    dispatched = {
        spec.provider_method for spec in _RUNTIME_EVIDENCE_OPERATIONS.values()
    }
    for provider_method in _AGGREGATE_ROW_CAPS:
        assert hasattr(StoredRuntimeProvider, provider_method), provider_method
        assert provider_method in dispatched, provider_method


def test_an_inventory_read_is_allowed_a_whole_estate_in_one_call() -> None:
    """Inventory gets the raised cap; rankings keep the default.

    Measured on live data before this split: 67 jobs in a 22:00-07:00 window
    against a cap of 25, so the agent sliced the window into nine hourly calls
    -- 9 model steps, 9 re-prefills, 172 rows for 67 jobs, and the same job
    reported under four headings with four different medians. One call now
    returns all 67.
    """

    from src.runtime.providers.stored import AGGREGATE_MAX_ROWS, aggregate_row_cap

    assert aggregate_row_cap("list_runtime_job_schedule_profile") >= 67
    # A ranking is still a correct ranking when clipped, and pluripharm has 158
    # job names and 306 error signatures -- nobody wants either in full.
    for ranking in (
        "list_runtime_job_reliability",
        "list_runtime_job_throughput",
        "list_runtime_error_signatures",
        "list_runtime_component_hotspots",
    ):
        assert aggregate_row_cap(ranking) == AGGREGATE_MAX_ROWS


async def test_the_disclosed_page_size_is_the_one_actually_enforced(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """Scope must quote this rollup's cap, not the shared default.

    Reporting the default would mark a complete 67-row inventory as possibly
    truncated -- a false warning, which trains the reader to ignore the true
    ones.
    """

    from src.runtime.providers.stored import aggregate_row_cap

    _patch_provider(monkeypatch, [PROFILE] * 67)

    result = await _run(db_session, limit=100, timezone="Europe/Amsterdam", start_hour=22, end_hour=7)

    assert result.scope["provider_page_size"] == aggregate_row_cap(
        "list_runtime_job_schedule_profile"
    )
    assert result.scope["records_returned"] == 67
    assert result.scope["may_be_truncated"] is False


def test_the_schema_permits_a_limit_large_enough_to_use_the_raised_cap() -> None:
    """A provider cap of 100 is unreachable if the schema rejects `limit: 100`."""

    from src.runtime.providers.stored import aggregate_row_cap

    schema_max = STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.input_schema["properties"]["limit"][
        "maximum"
    ]
    assert schema_max >= aggregate_row_cap("list_runtime_job_schedule_profile")


def test_the_guidance_names_this_operation_for_a_when_question() -> None:
    """Plumbing without guidance leaves the model reaching for the wrong tool.

    The failure was never that the agent lacked reasoning -- it was that nothing
    told it a time-of-day path existed, so it paged `list_executions`, saw the
    tail of one night, and generalized to "not supplied". The capability text is
    where that gets corrected, so it is asserted like code.
    """

    use_when = " ".join(STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.use_when)
    do_not = " ".join(STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.do_not_use_when)

    assert OPERATION in use_when
    assert "start_hour" in use_when and "end_hour" in use_when
    # The specific wrong answer this replaces.
    assert "never answer that run times are unavailable" in use_when.casefold()
    # And the near-miss it must not be confused with.
    assert "get_runtime_outcome_trend" in do_not
    assert "consecutive" in do_not.casefold()
