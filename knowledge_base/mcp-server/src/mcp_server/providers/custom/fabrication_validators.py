"""Deterministic fabrication checks for agent task outputs.

Two verified failure modes from production forensics (reverseEngineer run 50):

1. Component fabrication -- an agent without a job's evidence invented
   plausible component names (tSetGlobalVar_3 for a job whose real components
   are tSetGlobalVar_1/_4/_5) and the invented inventory shipped to the docs.
2. False unavailability -- a doc writer whose bundle contained the verbatim
   Java code and tMap expressions still wrote "code unavailable", anchoring on
   misleading derived signals.

Both checks run pre-acceptance inside the agent-task validation ladder: their
error strings land in ``AgentTaskContract.validation_gaps`` and drive the
repair attempt. The final publish gates stay advisory by design.
"""

from __future__ import annotations

import re

from mcp_server.providers.custom.target_evidence import (
    CATEGORY_CODE,
    CATEGORY_CONTEXT,
    CATEGORY_SQL,
    CATEGORY_TMAP,
    COMPONENT_REF_RE,
    extract_component_refs,
)

__all__ = [
    "component_reference_errors",
    "job_scoped_component_reference_errors",
    "availability_claim_errors",
    "extract_component_refs",
    "COMPONENT_REF_RE",
    "UNAVAILABILITY_PHRASES",
]

# Phrases that claim evidence is missing. Keep the list small and literal:
# every entry has produced (or would produce) a false "we could not see the
# source" statement in a published document.
UNAVAILABILITY_PHRASES: tuple[str, ...] = (
    "unavailable",
    "not available",
    "not captured",
    "not exported",
    "scanner-limited",
    "not present in the scan",
    "not in the scan",
    "truncated in the scan",
    "cuts off",
    "could not be extracted",
)

# Maps an evidence category to the keywords that tie an unavailability claim
# to that category. Keywords are deliberately narrow: runtime facts (metrics,
# schedules, sample files) are legitimately unavailable in a static scan and
# must not be flagged.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    CATEGORY_CODE: ("code", "java", "tjava", "custom logic"),
    CATEGORY_TMAP: ("tmap", "map expression", "ternary", "mapping logic"),
    CATEGORY_SQL: ("sql", "query"),
    CATEGORY_CONTEXT: ("context variable", "context value", "environment value"),
}

_CATEGORY_SECTION: dict[str, str] = {
    CATEGORY_CODE: "CUSTOM CODE (VERBATIM)",
    CATEGORY_TMAP: "TMAP EXPRESSIONS (VERBATIM)",
    CATEGORY_SQL: "SQL QUERIES (VERBATIM)",
    CATEGORY_CONTEXT: "CONTEXT VARIABLES (PER ENVIRONMENT, WITH PROVENANCE)",
}

_MAX_REPORTED_REFS = 15
_MAX_REPORTED_CLAIMS = 5


def component_reference_errors(
    text: str,
    allowed_refs: frozenset[str] | set[str],
    *,
    source_label: str = "the evidence bundle",
) -> list[str]:
    """Flag component references in ``text`` that do not exist in the evidence.

    ``allowed_refs`` must contain every component reference present in the
    evidence given to the agent (own components plus caller/callee components
    listed there). An empty allowed set disables the check -- no evidence
    inventory means nothing can be verified against it.
    """
    if not allowed_refs:
        return []
    unknown = sorted(extract_component_refs(text) - set(allowed_refs))
    if not unknown:
        return []
    shown = ", ".join(unknown[:_MAX_REPORTED_REFS])
    more = f" (+{len(unknown) - _MAX_REPORTED_REFS} more)" if len(unknown) > _MAX_REPORTED_REFS else ""
    return [
        f"output references components that do not exist in {source_label}: "
        f"{shown}{more}. Every component citation must come from the provided "
        "evidence; remove these references or replace them with the real "
        "component names listed in the evidence inventory."
    ]


# A model's own per-job sub-heading inside a multi-job Phase 2 task output,
# e.g. "#### AssetCheck_GetWorkOrder" or "#### AssetCheck_GetWorkOrder - tMap_1".
# Verified reliable in production output without any prompt change (run 51):
# every registered section that has per-job content uses this heading shape.
_JOB_HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)


def _job_attributed_spans(
    text: str, unit_names: list[str]
) -> list[tuple[str | None, str]]:
    """Split ``text`` into (unit_or_None, span_text) chunks on ``#### `` headings.

    A heading is attributed to a unit when the heading text starts with that
    unit's name at a word boundary -- longest unit name checked first, so
    'AssetCheck_GetWorkOrder' never swallows 'AssetCheck_GetWorkOrders'. Any
    text before the first heading, and any heading that names no known unit
    (business rules, risk summaries, cross-job table ownership, ...), is
    unattributed (``None``) -- genuine cross-job synthesis is not something a
    single job's evidence can validate.
    """
    headings = list(_JOB_HEADING_RE.finditer(text))
    if not headings:
        return [(None, text)]

    ordered_units = sorted(unit_names, key=len, reverse=True)
    boundary_res = {u: re.compile(rf"^{re.escape(u)}(?![A-Za-z0-9_])") for u in ordered_units}

    spans: list[tuple[str | None, str]] = []
    preamble = text[: headings[0].start()]
    if preamble.strip():
        spans.append((None, preamble))

    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        span_text = text[match.start():end]
        heading_text = match.group(1).strip()
        attributed = next(
            (unit for unit in ordered_units if boundary_res[unit].match(heading_text)),
            None,
        )
        spans.append((attributed, span_text))
    return spans


def job_scoped_component_reference_errors(
    text: str,
    unit_refs: dict[str, frozenset[str]],
    fallback_refs: frozenset[str],
    *,
    source_label: str = "this task's evidence file",
) -> list[str]:
    """Job-attributed version of :func:`component_reference_errors`.

    Verified failure mode: a Phase 2 task that bin-packs several sibling jobs
    into one call can misattribute a component that is real for one job to a
    different job in the same batch (e.g. 'tMSSqlOutput_1', real for
    AssetCheck_GetLocation, wrongly attributed to AssetCheck_GetWorkOrder,
    which actually uses 'tDBOutput_1'). Checking the whole task's output
    against the combined evidence of every job in the batch lets that pass,
    since the name IS real somewhere in the batch -- just not for the job it
    was attributed to. This checks each job-attributed span (see
    ``_job_attributed_spans``) against ONLY that job's own evidence, falling
    back to ``fallback_refs`` (the task-wide combined evidence) for content
    that cannot be attributed to a single job, so genuine cross-job synthesis
    is not incorrectly flagged.
    """
    if not unit_refs and not fallback_refs:
        return []
    by_unit: dict[str, list[str]] = {}
    unattributed: list[str] = []
    for unit, span_text in _job_attributed_spans(text, list(unit_refs.keys())):
        if unit is None:
            unattributed.append(span_text)
        else:
            by_unit.setdefault(unit, []).append(span_text)

    errors: list[str] = []
    for unit in sorted(by_unit):
        job_refs = unit_refs.get(unit) or frozenset()
        if not job_refs:
            # No evidence for this job to check against (e.g. a task's
            # missing_units) -- nothing to verify a citation against.
            continue
        errors.extend(
            component_reference_errors(
                "\n".join(by_unit[unit]),
                job_refs,
                source_label=(
                    f"the evidence for job {unit} (a sibling job's evidence "
                    "in this same batch does not count)"
                ),
            )
        )
    if unattributed:
        errors.extend(
            component_reference_errors(
                "\n".join(unattributed), fallback_refs, source_label=source_label
            )
        )
    return errors


def availability_claim_errors(
    text: str,
    present_categories: frozenset[str] | set[str],
) -> list[str]:
    """Flag claims that code/tMap/SQL/context evidence is unavailable when the
    evidence bundle demonstrably contains it."""
    if not present_categories:
        return []
    errors: list[str] = []
    reported = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not lowered:
            continue
        if not any(phrase in lowered for phrase in UNAVAILABILITY_PHRASES):
            continue
        for category in sorted(present_categories):
            keywords = _CATEGORY_KEYWORDS.get(category, ())
            if not any(keyword in lowered for keyword in keywords):
                continue
            if reported >= _MAX_REPORTED_CLAIMS:
                return errors
            snippet = line if len(line) <= 160 else line[:157] + "..."
            errors.append(
                f'output claims {category} evidence is unavailable ("{snippet}") '
                f"but the evidence bundle section '{_CATEGORY_SECTION[category]}' "
                "contains it verbatim. Quote the bundle instead of claiming a gap."
            )
            reported += 1
            break
    return errors
