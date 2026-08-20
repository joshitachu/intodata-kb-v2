"""Bounded, evidence-backed Phase 2 claim planning and rendering.

The scanner is intentionally allowed to collect rich project evidence.  This
module prevents that richness from turning into one unbounded prose request by
planning analysis around two independent axes:

* the canonical scanner unit(s) physically present in an evidence task; and
* the documentation concern being answered from that evidence.

Models return a small JSON claim batch.  Python validates the batch and renders
the accepted claims into the legacy sectioned Markdown contract consumed by
Phase 3.  The JSONL ledger remains the machine-readable source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.providers.custom.constants import (
    CROSS_JOB_FINDINGS_SECTION,
    GROUP_SUMMARY_SECTION,
)
from mcp_server.providers.custom.evidence_tasks import EvidenceTask
from mcp_server.providers.custom.models import FactsManifest
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.target_evidence import extract_component_refs


ClaimKind = Literal["fact", "relationship", "risk", "unknown"]
ClaimConfidence = Literal["certain", "supported", "tentative", "unknown"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Verified failure (werkorder Assetcheck run, AssetCheck_Interface: 4 of 11
# tasks rejected -> 90 pipeline-failure unknowns): the prompt DEMANDS the
# decisive tMap/SQL expression quoted verbatim, tMap expressions routinely
# exceed 1600 chars, and the old 1600-char Pydantic ceiling turned a compliant
# verbatim quote into `string_too_long` -> invalid batch -> every unit in the
# bin-packed task blanked to UNKNOWN. The allowance now accommodates real
# expressions; MAX_CLAIM_STATEMENT_CHARS is the single source of truth for the
# schema, the prompt text, and the salvage truncation.
MAX_CLAIM_STATEMENT_CHARS = 4000


class Phase2Claim(_StrictModel):
    """One concise interpretation tied to scanner evidence."""

    section: str
    kind: ClaimKind = "fact"
    unit_names: list[str] = Field(default_factory=list)
    related_units: list[str] = Field(default_factory=list)
    statement: str = Field(min_length=1, max_length=MAX_CLAIM_STATEMENT_CHARS)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: ClaimConfidence = "supported"
    claim_id: str = ""


class Phase2ClaimBatch(_StrictModel):
    """The complete bounded response for one unit/concern task."""

    schema_version: Literal["phase2-claims-v1"] = "phase2-claims-v1"
    group: str
    task_id: str
    concern: str
    claims: list[Phase2Claim] = Field(default_factory=list, max_length=80)


@dataclass(frozen=True)
class ClaimTaskSpec:
    """One semantic slice of an :class:`EvidenceTask`."""

    task: EvidenceTask
    concern: str
    sections: tuple[str, ...]
    artifact_names: frozenset[str]
    max_output_tokens: int

    @property
    def target_id(self) -> str:
        return f"{self.task.task_id}__{self.concern}"


_DESIGN_SECTIONS = (
    "COMPONENTS",
    "EXECUTION_FLOW",
    "DATA_FLOWS",
    "TRANSFORMATION_DETAIL",
    "BUSINESS_RULES",
    "SCHEMA_DICTIONARY",
    "SOURCE_SQL",
    "CUSTOM_CODE",
)
_OPERATIONS_SECTIONS = (
    "EXTERNAL_SYSTEMS",
    "ERROR_HANDLING",
    "ENVIRONMENT_CONFIG",
    "SCHEDULING",
    "NAMING_PATTERNS",
    "RISK_FLAGS",
)
_RELATIONSHIP_SECTIONS = (
    "CROSS_GROUP_REFS",
    "ESB_ROUTES",
)

_DESIGN_ARTIFACTS = frozenset(
    {
        "engineering_facts.md",
        "data_flow_map.md",
        "sql_usage.md",
        "quick_scan.md",
    }
)
_OPERATIONS_ARTIFACTS = frozenset(
    {
        "engineering_facts.md",
        "config_runtime_map.md",
        "known_risks.md",
        "quick_scan.md",
    }
)
# Code-bearing sections (P3.10 density floor): when the task's evidence
# demonstrably contains the corresponding category, these sections must carry
# real, verbatim-cited claims -- an unknown/paraphrase-only skeleton is a
# validation error, not an acceptable answer.
CODE_BEARING_SECTIONS = ("TRANSFORMATION_DETAIL", "SOURCE_SQL", "CUSTOM_CODE")

# Deterministic evidence detectors keyed to the preprocess markdown format
# (see _preprocess/{group}.md: `**QUERY**:` params, `**CODE*`/`**CODE_MAIN**`
# params, and the `Transformation Detail (tMap Data)` tables).
_SQL_EVIDENCE_RE = re.compile(
    r"\*\*QUERY\*\*:\s*`.{10,}", re.IGNORECASE,
)
_CODE_EVIDENCE_RE = re.compile(
    r"\*\*CODE(?:_MAIN|_START|_END|_ONCE)?\*\*:\s*`.{30,}", re.DOTALL,
)
_TMAP_EVIDENCE_RE = re.compile(
    r"Transformation Detail \(tMap Data\)|\|\s*expression\s*\|", re.IGNORECASE,
)
# A quoted span must be at least this long to count as a verbatim citation.
_MIN_VERBATIM_CITATION_CHARS = 12


def code_bearing_sections_with_evidence(evidence_text: str) -> set[str]:
    """Return the code-bearing sections whose category is present in evidence."""
    present: set[str] = set()
    if _SQL_EVIDENCE_RE.search(evidence_text):
        present.add("SOURCE_SQL")
    if _CODE_EVIDENCE_RE.search(evidence_text):
        present.add("CUSTOM_CODE")
    if _TMAP_EVIDENCE_RE.search(evidence_text):
        present.add("TRANSFORMATION_DETAIL")
    return present


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _statement_has_verbatim_citation(statement: str, normalized_evidence: str) -> bool:
    """True when the statement quotes a >=12-char span found verbatim in evidence."""
    for span in re.findall(r"`([^`]+)`", statement):
        normalized = _normalize_ws(span)
        if len(normalized) >= _MIN_VERBATIM_CITATION_CHARS and normalized in normalized_evidence:
            return True
    return False


_RELATIONSHIP_ARTIFACTS = frozenset(
    {
        "engineering_facts.md",
        "dependency_map.md",
        "architecture_map.md",
        "change_impact_matrix.md",
        "quick_scan.md",
        # Composite partition runs only: deterministic cross-sub-project call
        # edges + shared systems staged by the harness. Absent on
        # non-partitioned runs, so the allowlist entry is inert there.
        "cross_project_refs.md",
    }
)


def plan_claim_tasks(
    task: EvidenceTask,
    *,
    evidence_bytes: int,
    dense_threshold_bytes: int = 24_000,
    max_output_tokens: int = 4_500,
) -> list[ClaimTaskSpec]:
    """Create one bounded claim request for one physical unit packet.

    The compact JSON contract removes the old need to ask for separate giant
    essays per documentation concern.  Repeating the same raw unit evidence in
    two or three concern calls would merely move the original token explosion.
    We therefore make one request per unit packet and split by concern only
    after a real ``finish_reason=length`` signal.

    ``evidence_bytes`` and ``dense_threshold_bytes`` remain explicit inputs so
    planning receipts can evolve without changing this stable call contract.
    """

    del evidence_bytes, dense_threshold_bytes
    return [
        ClaimTaskSpec(
            task=task,
            concern="analysis",
            sections=ANALYSIS_SECTION_NAMES,
            artifact_names=(
                _DESIGN_ARTIFACTS | _OPERATIONS_ARTIFACTS | _RELATIONSHIP_ARTIFACTS
            ),
            max_output_tokens=max(1_200, max_output_tokens),
        )
    ]


def split_claim_task(spec: ClaimTaskSpec) -> tuple[ClaimTaskSpec, ClaimTaskSpec] | None:
    """Split a truncated concern task on semantic section boundaries."""

    if len(spec.sections) <= 1:
        return None
    midpoint = max(1, len(spec.sections) // 2)
    left_sections = spec.sections[:midpoint]
    right_sections = spec.sections[midpoint:]

    def child(label: str, sections: tuple[str, ...]) -> ClaimTaskSpec:
        return ClaimTaskSpec(
            task=spec.task,
            concern=f"{spec.concern}_{label}",
            sections=sections,
            artifact_names=spec.artifact_names,
            max_output_tokens=max(1_200, min(spec.max_output_tokens, 700 + 475 * len(sections))),
        )

    return child("a", left_sections), child("b", right_sections)


def claim_task_prompt(
    *,
    facts: FactsManifest,
    group: str,
    spec: ClaimTaskSpec,
) -> str:
    """Return the complete compact extraction contract for one claim task."""

    units = spec.task.all_units
    canonical_units = "\n".join(f"- {name}" for name in units) or "- none"
    sections = "\n".join(f"- {name}" for name in spec.sections)
    return f"""Return one JSON object only. Do not use Markdown fences or explanatory prose.

You are extracting bounded evidence claims for documentation. The scanner artifacts are the
factual record; your response is a concise interpretation index, not a copy of the source.

SCHEMA
{{
  "schema_version": "phase2-claims-v1",
  "group": {json.dumps(group)},
  "task_id": {json.dumps(spec.target_id)},
  "concern": {json.dumps(spec.concern)},
  "claims": [
    {{
      "section": "ONE_ALLOWED_SECTION",
      "kind": "fact|relationship|risk|unknown",
      "unit_names": ["assigned canonical unit"],
      "related_units": ["optional directly connected canonical unit"],
      "statement": "one concise evidenced statement",
      "evidence_refs": ["exact supplied source path and component/locator when available"],
      "confidence": "certain|supported|tentative|unknown",
      "claim_id": ""
    }}
  ]
}}

ASSIGNED CANONICAL UNITS
{canonical_units}

ALLOWED SECTIONS
{sections}

RULES
- Emit at least one claim for every allowed section.
- Use only assigned units in unit_names. A directly connected unit may appear in related_units.
- Every fact, relationship, or risk requires at least one exact evidence_refs entry.
- If evidence for a section is absent, emit exactly one kind=unknown claim naming the missing evidence;
  unknown claims may have an empty evidence_refs list.
- Maximum three non-unknown claims per section and maximum one claim per assigned unit per section.
- CODE-BEARING SECTIONS (TRANSFORMATION_DETAIL, SOURCE_SQL, CUSTOM_CODE): when the evidence
  contains SQL queries, tMap/transformation expressions, or custom code for an assigned unit,
  the section's claim MUST quote the decisive expression VERBATIM inside backticks (up to the
  {MAX_CLAIM_STATEMENT_CHARS}-character statement allowance; quote the decisive portion of a
  longer expression) and explain what it does. A one-line paraphrase without
  the verbatim expression is INVALID for these sections. An unknown claim in a code-bearing
  section is INVALID when the evidence contains that category.
- Other sections: do not reproduce full component inventories, context tables, credentials, URLs,
  or configuration dumps. State the decisive interpretation and cite its source.
- Do not infer business purpose, schedules, ownership, SLAs, or relationships absent from evidence.
- Preserve canonical names exactly. The project has {facts.unit_count} canonical {facts.canonical_unit_label}.
"""


def parse_claim_batch(
    text: str,
    *,
    expected_group: str,
    expected_task_id: str,
    expected_concern: str,
) -> Phase2ClaimBatch:
    """Parse a provider response and assign stable claim IDs."""

    payload_text = _strip_json_fence(text)
    payload = json.loads(payload_text)
    if isinstance(payload, dict) and set(payload) == {"claim_batch"}:
        payload = payload["claim_batch"]
    batch = Phase2ClaimBatch.model_validate(payload)
    if batch.group != expected_group:
        raise ValueError(f"claim batch group mismatch: {batch.group!r} != {expected_group!r}")
    if batch.task_id != expected_task_id:
        raise ValueError(f"claim batch task mismatch: {batch.task_id!r} != {expected_task_id!r}")
    if batch.concern != expected_concern:
        raise ValueError(f"claim batch concern mismatch: {batch.concern!r} != {expected_concern!r}")
    for claim in batch.claims:
        claim.section = _normalize_section(claim.section)
        if not claim.claim_id:
            claim.claim_id = _claim_id(batch, claim)
    return batch


def validate_claim_batch(
    text: str,
    *,
    facts: FactsManifest,
    group: str,
    spec: ClaimTaskSpec,
    evidence_text: str | None = None,
) -> dict[str, object]:
    """Validate one claim batch; STRUCTURAL problems fail, evidential gaps demote.

    R2.4 (verified failure: 21/73 Assetcheck tasks -- 29% -- were rejected
    whole because ONE code-bearing claim lacked a verbatim backtick citation,
    stubbing every unit in the bin-packed task to ``unknown`` and producing
    phantom-absence traceability warnings for units that exist):

    * ``errors`` (fail the batch, drive one repair attempt): unparseable/
      invalid JSON, sections outside the task contract, unit names outside the
      task contract, non-canonical related units, duplicate claim ids, and the
      bounded-claim ceilings (contract-shape violations).
    * ``warnings`` (accepted; the matching claims are DEMOTED per-section by
      :func:`apply_claim_floors` after parsing): a code-bearing claim without
      a verbatim citation, missing evidence_refs, missing unit_names,
      unknown-claims with a mismatched confidence, and missing sections (the
      deterministic renderer emits an explicit UNKNOWN line for those).

    A verbatim backtick citation stays PREFERRED for code-bearing sections --
    the prompt still demands it -- but a claim carrying ``evidence_refs`` is
    accepted even when its statement paraphrases.
    """

    errors: list[str] = []
    warnings: list[str] = []
    try:
        batch = parse_claim_batch(
            text,
            expected_group=group,
            expected_task_id=spec.target_id,
            expected_concern=spec.concern,
        )
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        return {"ok": False, "errors": [f"invalid claim batch JSON: {exc}"], "warnings": []}

    allowed_units = set(spec.task.all_units)
    canonical_units = facts.unit_names()
    allowed_sections = set(spec.sections)
    covered_sections: set[str] = set()
    section_counts: dict[str, int] = {}
    unit_section_counts: dict[tuple[str, str], int] = {}
    seen_ids: set[str] = set()
    for claim in batch.claims:
        covered_sections.add(claim.section)
        if claim.section not in allowed_sections:
            errors.append(f"claim section outside task contract: {claim.section}")
        outside_units = sorted(set(claim.unit_names) - allowed_units)
        if outside_units:
            errors.append("claim unit_names outside task contract: " + ", ".join(outside_units))
        outside_related = sorted(set(claim.related_units) - canonical_units)
        if outside_related:
            errors.append("claim related_units are not canonical: " + ", ".join(outside_related))
        if claim.kind != "unknown" and not claim.unit_names:
            warnings.append(
                f"{claim.claim_id or claim.section} has no assigned unit_names; demoted to unknown"
            )
        if claim.kind != "unknown" and not claim.evidence_refs:
            warnings.append(
                f"{claim.claim_id or claim.section} has no evidence_refs; "
                "demoted to unknown unless it carries a verbatim citation"
            )
        if claim.kind == "unknown" and claim.confidence != "unknown":
            warnings.append(
                f"{claim.claim_id or claim.section} unknown claim coerced to confidence=unknown"
            )
        if claim.claim_id in seen_ids:
            errors.append(f"duplicate claim_id: {claim.claim_id}")
        seen_ids.add(claim.claim_id)
        section_counts[claim.section] = section_counts.get(claim.section, 0) + 1
        for unit in claim.unit_names:
            key = (unit, claim.section)
            unit_section_counts[key] = unit_section_counts.get(key, 0) + 1

    missing_sections = sorted(allowed_sections - covered_sections)
    if missing_sections:
        warnings.append(
            "missing claim sections (rendered as explicit UNKNOWN lines): "
            + ", ".join(missing_sections)
        )

    # P3.10 density floor, R2.4-relaxed: verbatim citations stay preferred for
    # code-bearing sections whose category is demonstrably present, but their
    # absence demotes the individual claim instead of rejecting the batch.
    if evidence_text:
        evidenced = code_bearing_sections_with_evidence(evidence_text) & allowed_sections
        normalized_evidence = _normalize_ws(evidence_text)
        for section in sorted(evidenced):
            section_claims = [c for c in batch.claims if c.section == section]
            non_unknown = [c for c in section_claims if c.kind != "unknown"]
            if not non_unknown:
                warnings.append(
                    f"code-bearing section {section} has no substantive claim although the "
                    "task evidence contains that category; the section ships as an explicit gap"
                )
                continue
            for claim in non_unknown:
                if not _statement_has_verbatim_citation(claim.statement, normalized_evidence):
                    if claim.evidence_refs:
                        warnings.append(
                            f"{claim.claim_id or section} paraphrases without a verbatim "
                            "citation but cites evidence_refs; accepted"
                        )
                    else:
                        warnings.append(
                            f"{claim.claim_id or section} has neither a verbatim citation nor "
                            "evidence_refs in a code-bearing section; demoted to unknown"
                        )
    for section, count in sorted(section_counts.items()):
        non_unknown = sum(
            claim.kind != "unknown" and claim.section == section for claim in batch.claims
        )
        if non_unknown > 3 * max(1, len(allowed_units)):
            errors.append(f"section {section} exceeds bounded claim limit")
        if count > 20:
            errors.append(f"section {section} contains too many claims: {count}")
    for (unit, section), count in sorted(unit_section_counts.items()):
        if count > 1:
            errors.append(f"{unit}/{section} exceeds one-claim limit: {count}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "claim_count": len(batch.claims),
        "covered_sections": sorted(covered_sections),
        "missing_sections": missing_sections,
    }


def apply_claim_floors(
    batch: Phase2ClaimBatch,
    evidence_text: str | None,
) -> tuple[Phase2ClaimBatch, list[str]]:
    """Deterministically demote unsupported claims per-section (R2.4).

    Applied after a batch is parsed (fresh or resume-cached). Never fabricates
    text: demotion only flips ``kind``/``confidence`` metadata so the renderer
    marks the claim UNKNOWN; the model's own statement is preserved verbatim.

    Demotion rules for a non-unknown claim:
    * no ``unit_names`` -> unknown (nothing to attribute the fact to);
    * no ``evidence_refs`` AND no verbatim citation -> unknown (ungrounded);
    * has ``evidence_refs`` -> kept, even when the statement paraphrases
      (verbatim citation is preferred, not mandatory).

    ``kind=unknown`` claims get ``confidence`` coerced to ``unknown``.
    Returns the adjusted batch and human-readable demotion notes.
    """

    normalized_evidence = _normalize_ws(evidence_text) if evidence_text else ""
    notes: list[str] = []
    for claim in batch.claims:
        if claim.kind == "unknown":
            if claim.confidence != "unknown":
                claim.confidence = "unknown"
                notes.append(f"{claim.claim_id or claim.section}: coerced confidence=unknown")
            continue
        has_verbatim = bool(normalized_evidence) and _statement_has_verbatim_citation(
            claim.statement, normalized_evidence
        )
        if not claim.unit_names:
            claim.kind = "unknown"
            claim.confidence = "unknown"
            notes.append(
                f"{claim.claim_id or claim.section}: demoted (no assigned unit_names)"
            )
            continue
        if not claim.evidence_refs and not has_verbatim:
            claim.kind = "unknown"
            claim.confidence = "unknown"
            notes.append(
                f"{claim.claim_id or claim.section}: demoted "
                "(no evidence_refs and no verbatim citation)"
            )
    return batch, notes


def salvage_claim_batch(
    text: str,
    *,
    facts: FactsManifest,
    group: str,
    spec: ClaimTaskSpec,
    allowed_component_refs: frozenset[str] | set[str] | None = None,
) -> tuple[Phase2ClaimBatch | None, list[str]]:
    """Recover the valid claims from a terminally rejected batch (Round 3).

    Verified failure (werkorder Assetcheck, AssetCheck_Interface): ONE invalid
    claim (an overlong verbatim quote / an out-of-contract name) structurally
    failed the WHOLE batch, and after the repair attempts every unit in the
    bin-packed task was blanked to ``unknown_batch`` -- 90 pipeline-failure
    unknowns for jobs whose scan evidence is complete. Terminal rejection now
    salvages per-claim instead: each claim is validated independently and only
    the offending claims are dropped (with a note), so one bad claim can never
    blank a whole unit again.

    Deterministic selection only -- Python never writes or rewrites claim
    prose. The single mutation is truncating an overlong statement to the
    schema ceiling (verbatim prefix, marked with a trailing ellipsis marker).
    Returns ``(batch, notes)``; ``batch`` is ``None`` when the text is not
    parseable JSON at all (the honest ``unknown_batch`` remains the fallback).
    """

    try:
        payload = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return None, ["salvage: response is not parseable JSON"]
    if isinstance(payload, dict) and set(payload) == {"claim_batch"}:
        payload = payload["claim_batch"]
    if not isinstance(payload, dict):
        return None, ["salvage: response JSON is not an object"]
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return None, ["salvage: response JSON carries no claims list"]

    allowed_units = set(spec.task.all_units)
    canonical_units = facts.unit_names()
    allowed_sections = set(spec.sections)
    notes: list[str] = []
    kept: list[Phase2Claim] = []
    unit_section_seen: set[tuple[str, str]] = set()
    section_counts: dict[str, int] = {}

    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            notes.append(f"salvage: dropped claim #{index} (not an object)")
            continue
        raw = dict(raw)
        statement = raw.get("statement")
        if isinstance(statement, str) and len(statement) > MAX_CLAIM_STATEMENT_CHARS:
            raw["statement"] = statement[: MAX_CLAIM_STATEMENT_CHARS - 4] + " ..."
            notes.append(
                f"salvage: truncated overlong statement in claim #{index} "
                f"({len(statement)} chars)"
            )
        try:
            claim = Phase2Claim.model_validate(raw)
        except ValidationError as exc:
            notes.append(f"salvage: dropped claim #{index} (schema: {exc.error_count()} error(s))")
            continue
        claim.section = _normalize_section(claim.section)
        if claim.section not in allowed_sections:
            notes.append(
                f"salvage: dropped claim #{index} (section outside contract: {claim.section})"
            )
            continue
        outside_units = sorted(set(claim.unit_names) - allowed_units)
        if outside_units:
            notes.append(
                f"salvage: dropped claim #{index} (unit_names outside contract: "
                + ", ".join(outside_units)
                + ")"
            )
            continue
        dropped_related = sorted(set(claim.related_units) - canonical_units)
        if dropped_related:
            # Metadata-only strip: the statement itself is untouched.
            claim.related_units = [u for u in claim.related_units if u in canonical_units]
            notes.append(
                f"salvage: stripped non-canonical related_units from claim #{index}: "
                + ", ".join(dropped_related)
            )
        if allowed_component_refs is not None and claim.kind != "unknown":
            fabricated = extract_component_refs(claim.statement) - set(allowed_component_refs)
            if fabricated:
                notes.append(
                    f"salvage: dropped claim #{index} (component refs absent from "
                    "this task's evidence: " + ", ".join(sorted(fabricated)[:5]) + ")"
                )
                continue
        # Bounded-claim ceilings become deterministic first-N selection.
        if claim.kind != "unknown":
            duplicate_keys = [
                (unit, claim.section)
                for unit in claim.unit_names
                if (unit, claim.section) in unit_section_seen
            ]
            if duplicate_keys:
                notes.append(
                    f"salvage: dropped claim #{index} (one-claim-per-unit-per-section "
                    "ceiling: " + ", ".join(f"{u}/{s}" for u, s in duplicate_keys[:3]) + ")"
                )
                continue
        if section_counts.get(claim.section, 0) >= 20:
            notes.append(f"salvage: dropped claim #{index} (section ceiling: {claim.section})")
            continue
        if claim.kind != "unknown":
            unit_section_seen.update((unit, claim.section) for unit in claim.unit_names)
        section_counts[claim.section] = section_counts.get(claim.section, 0) + 1
        kept.append(claim)

    if not kept:
        return None, notes + ["salvage: no claim survived the per-claim checks"]

    batch = Phase2ClaimBatch(
        group=group,
        task_id=spec.target_id,
        concern=spec.concern,
        claims=kept,
    )
    for claim in batch.claims:
        claim.claim_id = _claim_id(batch, claim)
    # Regenerated ids are collision-free by construction unless two claims are
    # byte-identical; deduplicate those deterministically (first wins).
    seen_ids: set[str] = set()
    unique: list[Phase2Claim] = []
    for claim in batch.claims:
        if claim.claim_id in seen_ids:
            notes.append("salvage: dropped byte-identical duplicate claim")
            continue
        seen_ids.add(claim.claim_id)
        unique.append(claim)
    batch.claims = unique
    return batch, notes


def unknown_batch(
    *,
    group: str,
    spec: ClaimTaskSpec,
    reason: str,
) -> Phase2ClaimBatch:
    """Create an honest pipeline-failure batch for an unrecoverable slice.

    The statement wording is load-bearing (Round 3, Steven pattern): it must
    read as an ANALYSIS-PIPELINE gap with a pointer to the scan evidence that
    exists, never as source absence. Phase 3 doc writers and the evidence
    traceability validator key on this phrasing to render "analysis task did
    not complete (scan evidence exists)" instead of "unit is unavailable".
    """

    units = list(spec.task.all_units)
    evidence_path = _task_evidence_pointer(spec)
    batch = Phase2ClaimBatch(
        group=group,
        task_id=spec.target_id,
        concern=spec.concern,
        claims=[
            Phase2Claim(
                section=section,
                kind="unknown",
                unit_names=units,
                statement=(
                    f"The analysis task did not complete for this section ({reason}). "
                    f"This is a documentation-pipeline gap, NOT missing source: scan "
                    f"evidence for the unit(s) exists at {evidence_path}."
                ),
                confidence="unknown",
            )
            for section in spec.sections
        ],
    )
    for claim in batch.claims:
        claim.claim_id = _claim_id(batch, claim)
    return batch


def _task_evidence_pointer(spec: ClaimTaskSpec) -> str:
    """Best-effort staging-relative pointer to the task's evidence file."""
    path = getattr(spec.task, "path", None)
    if path is None:
        return "_preprocess/ (task evidence file)"
    parts = list(Path(path).parts)
    for anchor in ("_preprocess", "_analysis", "_investigation"):
        if anchor in parts:
            return "/".join(parts[parts.index(anchor):])
    return Path(path).name


def render_claims_as_markdown(
    *,
    group: str,
    unit_names: list[str],
    batches: list[Phase2ClaimBatch],
) -> str:
    """Render accepted claims into the legacy Phase 2 section contract."""

    claims = [claim for batch in batches for claim in batch.claims]
    parts: list[str] = []
    for section in ANALYSIS_SECTION_NAMES:
        parts.append(f"### {section}")
        section_claims = [claim for claim in claims if claim.section == section]
        if not section_claims:
            parts.append(
                f"- **UNKNOWN** — No scoped claim batch covered `{section}` for group `{group}`."
            )
            continue
        for claim in section_claims:
            units = ", ".join(f"`{name}`" for name in claim.unit_names) or "group scope"
            related = (
                "; related: " + ", ".join(f"`{name}`" for name in claim.related_units)
                if claim.related_units
                else ""
            )
            evidence = (
                "; evidence: " + ", ".join(f"`{ref}`" for ref in claim.evidence_refs)
                if claim.evidence_refs
                else "; evidence: scanner/model-limited"
            )
            parts.append(
                f"- **{claim.kind.upper()}** [{claim.confidence}] {units}{related} — "
                f"{claim.statement}{evidence} <!-- claim:{claim.claim_id} -->"
            )

    relationship_claims = [
        claim
        for claim in claims
        if claim.kind == "relationship" or claim.section in {"CROSS_GROUP_REFS", "ESB_ROUTES"}
    ]
    parts.append(f"### {CROSS_JOB_FINDINGS_SECTION}")
    if relationship_claims:
        for claim in relationship_claims:
            parts.append(
                f"- {claim.statement} (claim `{claim.claim_id}`; evidence: "
                + (", ".join(f"`{ref}`" for ref in claim.evidence_refs) or "scanner/model-limited")
                + ")"
            )
    else:
        parts.append("- No evidenced cross-unit relationship claim was established for this group.")

    unknown_count = sum(claim.kind == "unknown" for claim in claims)
    parts.extend(
        [
            f"### {GROUP_SUMMARY_SECTION}",
            f"- Canonical group: `{group}`.",
            "- Units covered: " + (", ".join(f"`{name}`" for name in unit_names) or "none"),
            f"- Accepted evidence claims: {len(claims) - unknown_count}; explicit unknowns: {unknown_count}.",
            f"- Relationship claims: {len(relationship_claims)}.",
            "- Detailed source truth remains in the scanner artifacts cited by each claim.",
        ]
    )
    return "\n\n".join(parts).rstrip() + "\n"


def write_claim_ledger(
    path: Path,
    *,
    group: str,
    unit_names: list[str],
    batches: list[Phase2ClaimBatch],
) -> None:
    """Persist accepted claims as JSONL with a deterministic header record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = [
        {
            "record_type": "claim_ledger_header",
            "schema_version": "phase2-claims-v1",
            "group": group,
            "unit_names": unit_names,
            "batch_count": len(batches),
        }
    ]
    for batch in batches:
        for claim in batch.claims:
            rows.append(
                {
                    "record_type": "claim",
                    "group": group,
                    "task_id": batch.task_id,
                    "concern": batch.concern,
                    **claim.model_dump(),
                }
            )
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _normalize_section(value: str) -> str:
    return re.sub(r"[^A-Z0-9_]+", "_", value.strip().upper()).strip("_")


def _claim_id(batch: Phase2ClaimBatch, claim: Phase2Claim) -> str:
    payload = {
        "group": batch.group,
        "task": batch.task_id,
        "concern": batch.concern,
        "section": claim.section,
        "units": claim.unit_names,
        "related": claim.related_units,
        "statement": claim.statement,
        "evidence": claim.evidence_refs,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return f"claim:{digest}"
