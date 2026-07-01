"""Unit tests for the pure group-delta classifier (Slice 3).

Fixture: 2 jobs, 3 components each, 5 params each = 30 fields total.
All tests are pure Python -- no file I/O, no DB.
"""

from __future__ import annotations

import pytest

from mcp_server.services.group_delta_classifier import (
    DeltaClassification,
    classify_group_delta,
    extract_field_map,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_job(name: str, n_components: int = 3, n_params: int = 5) -> dict:
    """Build a synthetic job dict matching the scanner's per-group JSON shape."""
    components = []
    for ci in range(n_components):
        params = [
            {"name": f"param_{pi}", "value": f"val_{name}_{ci}_{pi}", "field_type": "TEXT"}
            for pi in range(n_params)
        ]
        components.append(
            {
                "unique_name": f"comp_{ci}",
                "component_type": "tFileInputDelimited",
                "params": params,
            }
        )
    return {"name": name, "components": components, "group": "grp"}


def _base_group() -> list[dict]:
    """Two jobs, 3 components each, 5 params each = 30 fields."""
    return [_make_job("job_A"), _make_job("job_B")]


def _copy_group(jobs: list[dict]) -> list[dict]:
    """Deep-copy a job list via serialisation so mutations don't cross tests."""
    import copy
    return copy.deepcopy(jobs)


# ---------------------------------------------------------------------------
# NEW_UNIT
# ---------------------------------------------------------------------------


def test_new_unit_added_to_curr() -> None:
    prev = _base_group()
    curr = _copy_group(prev)
    curr.append(_make_job("job_C"))

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "NEW_UNIT"
    assert cls.changed_fraction == 0.0
    assert "job_C" in cls.detail


def test_new_unit_when_prev_empty_and_curr_has_jobs() -> None:
    cls = classify_group_delta([], _base_group())

    assert cls.kind == "NEW_UNIT"
    assert cls.changed_fraction == 0.0


# ---------------------------------------------------------------------------
# DELETED_UNIT
# ---------------------------------------------------------------------------


def test_deleted_unit_removed_from_curr() -> None:
    prev = _base_group()
    curr = [_copy_group(prev)[0]]  # keep only job_A

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "DELETED_UNIT"
    assert cls.changed_fraction == 0.0
    assert "job_B" in cls.detail


def test_deleted_unit_all_units_removed() -> None:
    prev = _base_group()

    cls = classify_group_delta(prev, [])

    assert cls.kind == "DELETED_UNIT"
    assert cls.changed_fraction == 0.0
    assert "all units removed" in cls.detail


# ---------------------------------------------------------------------------
# COMPONENT_TYPE_CHANGED (step 4 -- type string change)
# ---------------------------------------------------------------------------


def test_component_type_changed_same_job_and_unique_name() -> None:
    prev = _base_group()
    curr = _copy_group(prev)
    # Change component_type for comp_1 of job_A
    curr[0]["components"][1]["component_type"] = "tJava"

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "COMPONENT_TYPE_CHANGED"
    assert cls.changed_fraction == 0.0
    assert "comp_1" in cls.detail


# ---------------------------------------------------------------------------
# COMPONENT_TYPE_CHANGED (step 3.5 -- component count change)
# ---------------------------------------------------------------------------


def test_component_count_change_triggers_structural() -> None:
    prev = _base_group()
    curr = _copy_group(prev)
    # Add an extra component to job_B
    curr[1]["components"].append(
        {
            "unique_name": "comp_new",
            "component_type": "tMap",
            "params": [{"name": "p0", "value": "v0", "field_type": "TEXT"}],
        }
    )

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "COMPONENT_TYPE_CHANGED"
    assert cls.changed_fraction == 0.0
    assert "component count" in cls.detail


# ---------------------------------------------------------------------------
# FIELD_EDIT -- below threshold
# ---------------------------------------------------------------------------


def test_field_edit_below_threshold_correct_fraction() -> None:
    """1 of 30 fields changed -> fraction == 1/30 ~= 0.033."""
    prev = _base_group()
    curr = _copy_group(prev)
    # Change one param value in job_A, comp_0, param_0
    curr[0]["components"][0]["params"][0]["value"] = "CHANGED"

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction == pytest.approx(1 / 30)


def test_field_edit_no_actual_change_fraction_zero() -> None:
    """Identical jobs -> FIELD_EDIT with fraction 0.0 (hash differs but no param changed)."""
    prev = _base_group()
    curr = _copy_group(prev)

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction == 0.0


# ---------------------------------------------------------------------------
# FIELD_EDIT -- at / above threshold (routes to scoped_regen)
# ---------------------------------------------------------------------------


def test_field_edit_at_threshold_routes_scoped() -> None:
    """6 of 30 fields changed -> fraction == 0.20 (at threshold -> scoped_regen)."""
    prev = _base_group()
    curr = _copy_group(prev)
    # Change 6 params across both jobs (3 in job_A comp_0, 3 in job_A comp_1)
    for pi in range(3):
        curr[0]["components"][0]["params"][pi]["value"] = f"CHANGED_{pi}"
        curr[0]["components"][1]["params"][pi]["value"] = f"CHANGED_{pi}"

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction == pytest.approx(6 / 30)


def test_field_edit_above_threshold_routes_scoped() -> None:
    """7 of 30 fields changed -> fraction > 0.20 -> scoped_regen."""
    prev = _base_group()
    curr = _copy_group(prev)
    for pi in range(3):
        curr[0]["components"][0]["params"][pi]["value"] = f"CHANGED_{pi}"
        curr[0]["components"][1]["params"][pi]["value"] = f"CHANGED_{pi}"
    curr[0]["components"][2]["params"][0]["value"] = "CHANGED_extra"

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction == pytest.approx(7 / 30)
    assert cls.changed_fraction > 0.20


# ---------------------------------------------------------------------------
# No prior baseline (empty prev) -> conservatively scoped
# ---------------------------------------------------------------------------


def test_empty_prev_jobs_returns_new_unit() -> None:
    """No prev baseline (empty list) -> NEW_UNIT -> scoped_regen."""
    cls = classify_group_delta([], _base_group())

    assert cls.kind == "NEW_UNIT"
    assert cls.changed_fraction == 0.0


# ---------------------------------------------------------------------------
# Param-level add/remove within stable unit (Lens 1 mitigation)
# ---------------------------------------------------------------------------


def test_param_added_within_stable_unit_counted_in_changed_fraction() -> None:
    """New param within a stable job (no unit add/remove) increases changed_fraction."""
    prev = _base_group()
    curr = _copy_group(prev)
    # Add a new param to comp_0 of job_A (no job or component added/removed)
    curr[0]["components"][0]["params"].append(
        {"name": "param_new", "value": "new_val", "field_type": "TEXT"}
    )

    cls = classify_group_delta(prev, curr)

    # total_fields is 31 (30 original + 1 new param in curr), changed = 1 (new param key)
    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction > 0.0
    assert cls.changed_fraction == pytest.approx(1 / 31)


# ---------------------------------------------------------------------------
# Malformed input robustness
# ---------------------------------------------------------------------------


def test_malformed_job_entry_does_not_raise() -> None:
    """Non-dict entries in the job list are silently skipped."""
    prev: list[dict] = [None, "garbage", 42]  # type: ignore[list-item]
    curr: list[dict] = [None, {"name": "job_X", "components": []}]  # type: ignore[list-item]

    cls = classify_group_delta(prev, curr)

    # Should not raise; conservative result
    assert cls.kind in {"NEW_UNIT", "DELETED_UNIT", "COMPONENT_TYPE_CHANGED", "FIELD_EDIT"}


def test_missing_keys_in_job_does_not_raise() -> None:
    """Jobs without expected keys are handled gracefully."""
    prev = [{"name": "job_A", "components": []}]
    curr = [{"name": "job_A"}]  # no components key

    cls = classify_group_delta(prev, curr)

    assert cls.kind == "FIELD_EDIT"  # stable unit, no fields -> fraction 0.0
    assert cls.changed_fraction == 0.0


# ---------------------------------------------------------------------------
# extract_field_map round-trip
# ---------------------------------------------------------------------------


def test_extract_field_map_round_trip() -> None:
    """extract_field_map returns the expected (job, comp, param) -> value mapping."""
    jobs = [_make_job("job_A", n_components=2, n_params=3)]

    field_map = extract_field_map(jobs)

    # 2 components * 3 params = 6 entries
    assert len(field_map) == 6
    assert ("job_A", "comp_0", "param_0") in field_map
    assert ("job_A", "comp_1", "param_2") in field_map
    assert field_map[("job_A", "comp_0", "param_0")] == "val_job_A_0_0"


def test_extract_field_map_empty_jobs() -> None:
    assert extract_field_map([]) == {}


def test_extract_field_map_dict_params() -> None:
    """Handles components whose params is a dict instead of a list."""
    jobs = [
        {
            "name": "job_X",
            "components": [
                {
                    "unique_name": "comp_0",
                    "component_type": "tCustom",
                    "params": {"alpha": "v1", "beta": "v2"},
                }
            ],
        }
    ]

    field_map = extract_field_map(jobs)

    assert len(field_map) == 2
    assert field_map[("job_X", "comp_0", "alpha")] == "v1"
    assert field_map[("job_X", "comp_0", "beta")] == "v2"
