"""Pure group-delta classifier for Smart Reingestion (Slice 3).

All file / DB I/O stays OUT of this module. The caller reads parsed dicts
from disk and passes them in. This makes the classifier independently
testable and reusable by Slice 4's field-diff logic.

Classification priority order (first match wins):

    1.  All units removed from group          -> DELETED_UNIT
    2.  Any new job name in curr              -> NEW_UNIT
    3.  Any job name in prev absent from curr -> DELETED_UNIT
    3.5 Any stable job's component count changed
                                              -> COMPONENT_TYPE_CHANGED (conservative)
    4.  Same (job, component) pair has a different component_type
                                              -> COMPONENT_TYPE_CHANGED
    5.  Param-value diff over stable units    -> FIELD_EDIT (changed_fraction computed)

Critique Lens 1+2 mitigations:
    - Step 3.5 catches component add/remove within a stable job (conservative structural).
    - Step 5 includes symmetric-difference of param keys (add/remove within stable
      unit) in changed_fields, so a param addition is not silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeltaKind = Literal[
    "NEW_UNIT",
    "DELETED_UNIT",
    "COMPONENT_TYPE_CHANGED",
    "FIELD_EDIT",
]


@dataclass(frozen=True)
class DeltaClassification:
    """Typed result of classifying a dirty group's delta.

    ``changed_fraction`` is 0.0 for all structural kinds (NEW_UNIT,
    DELETED_UNIT, COMPONENT_TYPE_CHANGED). For FIELD_EDIT it is
    ``changed_fields / total_fields`` where ``total_fields`` counts all
    (job_name, unique_name, param_name) triples in ``curr_jobs``.
    ``detail`` is a human-readable reason string used in routing logs.
    """

    kind: DeltaKind
    changed_fraction: float
    detail: str


def extract_field_map(jobs: list[dict]) -> dict[tuple[str, str, str], str]:
    """Return {(job_name, unique_name, param_name): param_value} for all jobs.

    Public for Slice 4 reuse: Slice 4's Phase 2 patch-prompt builder calls
    this on both prev_jobs and curr_jobs to produce the field-diff dict
    (old values vs new values) per (job_name, unique_name, param_name) key.

    Handles both list-of-dicts params (Talend/Generic) and dict params
    gracefully. Malformed entries (missing keys) are skipped without raising.
    """
    result: dict[tuple[str, str, str], str] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name") or "")
        for comp in (job.get("components") or []):
            if not isinstance(comp, dict):
                continue
            unique_name = str(comp.get("unique_name") or "")
            params = comp.get("params") or []
            if isinstance(params, list):
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    param_name = str(p.get("name") or "")
                    param_value = str(p.get("value") if p.get("value") is not None else "")
                    result[(job_name, unique_name, param_name)] = param_value
            elif isinstance(params, dict):
                for param_name, param_value in params.items():
                    result[(job_name, unique_name, str(param_name))] = (
                        str(param_value) if param_value is not None else ""
                    )
    return result


def classify_group_delta(
    prev_jobs: list[dict],
    curr_jobs: list[dict],
) -> DeltaClassification:
    """Classify the delta between two versions of a group's parsed job list.

    ``prev_jobs`` and ``curr_jobs`` are lists of job dicts matching the
    scanner's per-group output (keys: ``name``, ``components``, ...).
    No file I/O is performed; the caller passes already-parsed dicts.

    Returns a ``DeltaClassification`` with the first matching kind
    (highest priority wins) and ``changed_fraction`` as defined above.
    """
    # --- Unit-level (job-name) structural checks ---
    prev_names: set[str] = {str(j.get("name") or "") for j in prev_jobs if isinstance(j, dict) and j.get("name")}
    curr_names: set[str] = {str(j.get("name") or "") for j in curr_jobs if isinstance(j, dict) and j.get("name")}

    # Step 1: All units removed
    if prev_names and not curr_names:
        return DeltaClassification(
            kind="DELETED_UNIT",
            changed_fraction=0.0,
            detail="all units removed from group",
        )

    # Step 2: New unit appeared
    added: set[str] = curr_names - prev_names
    if added:
        return DeltaClassification(
            kind="NEW_UNIT",
            changed_fraction=0.0,
            detail=f"new unit(s): {', '.join(sorted(added))}",
        )

    # Step 3: Unit disappeared
    removed: set[str] = prev_names - curr_names
    if removed:
        return DeltaClassification(
            kind="DELETED_UNIT",
            changed_fraction=0.0,
            detail=f"deleted unit(s): {', '.join(sorted(removed))}",
        )

    # Build per-job component lookup maps for stable-unit structural checks.
    # Key: job_name -> {unique_name -> component_dict}
    def _comp_map(jobs: list[dict]) -> dict[str, dict[str, dict]]:
        result: dict[str, dict[str, dict]] = {}
        for j in jobs:
            if not isinstance(j, dict):
                continue
            name = str(j.get("name") or "")
            if not name:
                continue
            result[name] = {
                str(c.get("unique_name") or ""): c
                for c in (j.get("components") or [])
                if isinstance(c, dict) and c.get("unique_name")
            }
        return result

    prev_comp_map = _comp_map(prev_jobs)
    curr_comp_map = _comp_map(curr_jobs)

    # Step 3.5: Component count changed for any stable job (conservative structural
    # signal catching component add/remove within a job — Lens 2 mitigation).
    for job_name in curr_names:
        prev_count = len(prev_comp_map.get(job_name, {}))
        curr_count = len(curr_comp_map.get(job_name, {}))
        if prev_count != curr_count:
            return DeltaClassification(
                kind="COMPONENT_TYPE_CHANGED",
                changed_fraction=0.0,
                detail=(
                    f"component count changed for job '{job_name}': "
                    f"{prev_count} -> {curr_count}"
                ),
            )

    # Step 4: Same (job, component) pair has a different component_type.
    for job_name in curr_names:
        prev_comps = prev_comp_map.get(job_name, {})
        curr_comps = curr_comp_map.get(job_name, {})
        for unique_name, curr_comp in curr_comps.items():
            if unique_name in prev_comps:
                prev_type = str(prev_comps[unique_name].get("component_type") or "")
                curr_type = str(curr_comp.get("component_type") or "")
                if prev_type != curr_type:
                    return DeltaClassification(
                        kind="COMPONENT_TYPE_CHANGED",
                        changed_fraction=0.0,
                        detail=(
                            f"component type changed: '{unique_name}' in job "
                            f"'{job_name}': {prev_type!r} -> {curr_type!r}"
                        ),
                    )

    # Step 5: Field-level diff over stable units.
    prev_fields = extract_field_map(prev_jobs)
    curr_fields = extract_field_map(curr_jobs)

    total_fields = len(curr_fields)
    if total_fields == 0:
        return DeltaClassification(
            kind="FIELD_EDIT",
            changed_fraction=0.0,
            detail="no param fields in group",
        )

    # Value-changed fields: same key, different value.
    value_changed = sum(
        1
        for key, curr_val in curr_fields.items()
        if key in prev_fields and prev_fields[key] != curr_val
    )

    # Param-level structural delta within stable units (Lens 1 mitigation):
    # params added or removed within a unit that didn't gain/lose a job-name.
    curr_keys = set(curr_fields)
    prev_keys = set(prev_fields)
    param_structural = len((curr_keys - prev_keys) | (prev_keys - curr_keys))

    changed_fields = value_changed + param_structural
    changed_fraction = min(changed_fields / total_fields, 1.0)

    return DeltaClassification(
        kind="FIELD_EDIT",
        changed_fraction=changed_fraction,
        detail=(
            f"{changed_fields} of {total_fields} fields changed "
            f"(value_changed={value_changed}, param_structural={param_structural}, "
            f"fraction={changed_fraction:.4f})"
        ),
    )
