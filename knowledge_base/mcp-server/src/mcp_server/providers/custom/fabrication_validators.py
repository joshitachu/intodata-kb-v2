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
from collections.abc import Iterable

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
    "declared_component_reference_errors",
    "job_scoped_component_reference_errors",
    "availability_claim_errors",
    "protected_fact_errors",
    "unit_count_claim_errors",
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


def declared_component_reference_errors(
    declared_refs: Iterable[str],
    allowed_refs: frozenset[str] | set[str],
    *,
    source_label: str = "the evidence bundle",
) -> list[str]:
    """Set-membership version of :func:`component_reference_errors`.

    ``component_reference_errors`` recovers component names by running a regex
    over the model's prose, so it depends on the shape of that prose and finds
    nothing at all if the shape changes. This takes the references the writer
    DECLARED in a typed sidecar (``_provenance/{DOC}.json``) and never reads the
    document text, which makes verification a set operation.

    A declared miss is the harder finding of the two. "This claim rests on
    tMSSqlOutput_1 in group X" is the writer's own statement about where the
    claim came from; a name scraped out of prose may be a passing mention, a
    cross-reference or a quotation of someone else's component. The wording says
    so, because the operator reading the report has to rank the two.

    ``allowed_refs`` empty disables the check, exactly as in the prose version:
    no evidence inventory means there is nothing to verify against, and an empty
    allowed set would otherwise reject every reference.
    """
    if not allowed_refs:
        return []
    unknown = sorted(set(declared_refs) - set(allowed_refs))
    if not unknown:
        return []
    shown = ", ".join(unknown[:_MAX_REPORTED_REFS])
    more = (
        f" (+{len(unknown) - _MAX_REPORTED_REFS} more)"
        if len(unknown) > _MAX_REPORTED_REFS
        else ""
    )
    return [
        (
            f"output declares that its claims rest on components that do not "
            f"exist in {source_label}: {shown}{more}. A declared reference is "
            "the writer's own statement of where a claim came from, so it must "
            "name a component the evidence actually contains; correct the "
            "reference or withdraw the claim it supports."
        )
    ]


# A model's own per-job sub-heading inside a multi-job Phase 2 task output,
# e.g. "#### AssetCheck_GetWorkOrder" or "### AssetCheck_GetWorkOrder - tMap_1".
# Run 51 produced these at h4 consistently, but the LEVEL is the model's choice
# and pinning it is the defect that cost run 258 ~95% of its evidence: when no
# heading matched, this returned one unattributed span and the job-scoped check
# silently degraded to the weaker task-wide one -- which is exactly the check
# that lets a component real for one job in the batch pass as a sibling's.
# Any level h2-h6 is accepted; h1 is a document title, not a sub-heading.
# A span whose heading names no known unit stays unattributed either way, so
# widening cannot wrongly attribute -- it can only recover attribution.
_JOB_HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.MULTILINE)


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


_FACT_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)


def protected_fact_errors(
    text: str,
    protected_facts: dict[str, str],
) -> list[str]:
    """Enforce the protected-fact ledger on a per-unit document (P1.5).

    The unit doc must contain a ``Protected Scanner Facts`` section whose rows
    echo the deterministic ledger values verbatim. A missing section, a
    missing fact row, or a value that differs from the ledger is an error --
    protected facts are copied from evidence, never recomputed by the model.
    Numeric facts (component_count, connection_count) are additionally checked
    even when the fact-name column was reworded, so a recomputed count cannot
    hide behind a renamed row.
    """
    if not protected_facts:
        return []
    errors: list[str] = []
    lowered = text.lower()
    if "protected scanner facts" not in lowered:
        return [
            "output is missing the 'Protected Scanner Facts' section. Copy the "
            "PROTECTED SCANNER FACTS table from the evidence bundle verbatim."
        ]
    doc_rows: dict[str, str] = {}
    for match in _FACT_ROW_RE.finditer(text):
        name = match.group(1).strip().lower()
        value = match.group(2).strip()
        if name and name not in {"fact", "---", ""}:
            doc_rows.setdefault(name, value)
    for fact, expected in protected_facts.items():
        stated = doc_rows.get(fact.lower())
        if stated is None:
            errors.append(
                f"protected fact '{fact}' is missing from the Protected Scanner "
                f"Facts table; copy it verbatim from the evidence bundle "
                f"(expected value: {expected})"
            )
            continue
        # Strip trailing evidence/fact tags like [fact:unit.X.group] before comparing.
        stated_value = re.sub(r"\s*\[(?:fact|evidence):[^\]]*\]\s*$", "", stated).strip()
        if stated_value.replace("\\|", "|") != expected:
            errors.append(
                f"protected fact '{fact}' was restated as '{stated_value}' but the "
                f"scanner ledger says '{expected}'. Protected facts must be copied "
                "verbatim, never recomputed."
            )
    return errors


def unit_count_claim_errors(
    text: str,
    *,
    unit_count: int,
    canonical_unit_label: str,
    allowed_counts: frozenset[int] | set[int] = frozenset(),
) -> list[str]:
    """Flag canonical-unit-count claims that match no scanner-derived count.

    Summary/catalog docs must quote unit counts from evidence (the facts
    manifest total or a per-group count), never recompute them by inference.
    Only counts adjacent to the canonical unit label are checked, so unrelated
    quantities ("3 external systems") are never flagged.
    """
    label = re.escape(canonical_unit_label.rstrip("s"))
    pattern = re.compile(rf"\b(\d{{1,5}})\s+(?:canonical\s+)?{label}s?\b", re.IGNORECASE)
    allowed = {unit_count, *allowed_counts}
    lowered = (text or "").lower()
    # Only a count presented as the whole-project / canonical TOTAL may be
    # flagged. Legitimate per-group or per-layer sub-counts ("4 jobs in the
    # routing layer", "8 jobs in this group") are not fabrications and must not
    # trip a hard validation error (which forces a full, wasteful doc repair).
    total_terms = (
        "total", "canonical", "all ", "entire", "whole", "altogether",
        "in all", "combined", "project contains", "project has",
        "suite of", "comprising", "consists of", "consist of",
    )
    errors: list[str] = []
    seen: set[int] = set()
    for match in pattern.finditer(text or ""):
        value = int(match.group(1))
        if value in allowed or value in seen:
            continue
        window = lowered[max(0, match.start() - 60): match.end() + 20]
        if not any(term in window for term in total_terms):
            continue
        seen.add(value)
        errors.append(
            f"output claims '{match.group(0)}' as a project total but no "
            f"scanner-derived total equals {value} (canonical total: "
            f"{unit_count}). Quote the total from the facts manifest; never "
            "recompute it."
        )
        if len(errors) >= _MAX_REPORTED_CLAIMS:
            break
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
