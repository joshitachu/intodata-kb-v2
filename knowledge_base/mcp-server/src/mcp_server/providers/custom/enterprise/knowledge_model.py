"""Enterprise knowledge model: schema, assembly and fail-closed validation.

The model is the product boundary between analysis and writing (plan decision
6). Documentation and RAG are projections of it; a shallow summary can never
stand in for it. Every entry carries evidence references into the sanitized
workspace or the exact fact tables, and Python computes the claim ids -- model
text cannot certify itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.providers.custom.enterprise.layout import (
    EVIDENCE_ROOTS,
    KNOWLEDGE_MODEL,
    KNOWLEDGE_VALIDATION,
)
from mcp_server.providers.custom.knowledge_contracts import write_json

SCHEMA_VERSION = "enterprise-knowledge-1"

Confidence = Literal["certain", "high", "medium", "low"]
EvidenceStatus = Literal["evidenced", "inferred", "unknown"]
Authority = Literal["implemented_design", "documented_intent", "runtime_observation", "inference"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    path: str = Field(min_length=1, max_length=600)
    locator: str = Field(default="", max_length=600)
    quote: str = Field(default="", max_length=2000)


class AudienceViews(StrictModel):
    developer: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    architect: str = Field(min_length=1)
    business_owner: str = Field(min_length=1)


class DesignEntry(StrictModel):
    unit_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    views: AudienceViews
    systems: list[str] = Field(default_factory=list)
    business_purpose_status: EvidenceStatus = "unknown"
    evidence: list[EvidenceRef] = Field(min_length=1)
    unknowns: list[str] = Field(default_factory=list)
    claim_id: str = ""


class SystemEntry(StrictModel):
    system_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class JourneyTrigger(StrictModel):
    kind: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    detail: str = ""


class JourneyStep(StrictModel):
    order: int = Field(ge=1)
    unit_id: str = Field(min_length=1)
    components: list[str] = Field(default_factory=list)
    mechanism: str = ""
    description: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class JourneyDestination(StrictModel):
    system: str = Field(min_length=1)
    mechanism: str = ""
    detail: str = ""


class Journey(StrictModel):
    journey_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    name: str = Field(min_length=1)
    trigger: JourneyTrigger
    steps: list[JourneyStep] = Field(min_length=1)
    destinations: list[JourneyDestination] = Field(default_factory=list)
    business_object: str = ""
    business_capability: str = ""
    implementing_units: list[str] = Field(min_length=1)
    mapping_table_ids: list[str] = Field(default_factory=list)
    error_path_ids: list[str] = Field(default_factory=list)
    monitoring: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class ExcludedUnit(StrictModel):
    unit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MappingInterpretation(StrictModel):
    table_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    component_name: str = ""
    summary: str = Field(min_length=1)
    rules: list[str] = Field(default_factory=list)
    business_meaning: str = ""
    business_meaning_status: EvidenceStatus = "unknown"
    risks: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class ConfigurationEntry(StrictModel):
    scope: Literal["context_group", "unit", "connection"]
    key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    environment_differences: list[str] = Field(default_factory=list)
    drives: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class ErrorPath(StrictModel):
    error_path_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    unit_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    handling: str = Field(min_length=1)
    recovery: str = ""
    blast_radius: str = ""
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class RuntimeObservation(StrictModel):
    unit_id: str = Field(min_length=1)
    status: Literal["observed", "not_observed_in_window", "lookup_failed", "unavailable", "not_applicable"]
    summary: str = Field(min_length=1)
    environments: list[str] = Field(default_factory=list)
    window_days: int | None = None
    authority: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    claim_id: str = ""


class BusinessMeaning(StrictModel):
    journey_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: EvidenceStatus = "unknown"
    source_authority: Authority = "inference"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    claim_id: str = ""


class Contradiction(StrictModel):
    contradiction_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    subject: str = Field(min_length=1)
    authorities: list[Authority] = Field(min_length=2)
    statements: list[str] = Field(min_length=2)
    resolution: Literal["open", "favours_code", "favours_runtime", "favours_documentation"] = "open"
    evidence: list[EvidenceRef] = Field(min_length=1)
    claim_id: str = ""


class Unknown(StrictModel):
    unknown_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    subject: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    audience: list[Literal["developer", "operator", "architect", "business_owner"]] = Field(default_factory=list)
    locator: str = ""
    claim_id: str = ""


class KnowledgeModel(StrictModel):
    schema_version: Literal["enterprise-knowledge-1"] = SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    designs: list[DesignEntry] = Field(default_factory=list)
    systems: list[SystemEntry] = Field(default_factory=list)
    journeys: list[Journey] = Field(default_factory=list)
    excluded_units: list[ExcludedUnit] = Field(default_factory=list)
    mappings: list[MappingInterpretation] = Field(default_factory=list)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    configuration: list[ConfigurationEntry] = Field(default_factory=list)
    error_paths: list[ErrorPath] = Field(default_factory=list)
    runtime: list[RuntimeObservation] = Field(default_factory=list)
    business: list[BusinessMeaning] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(StrictModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    # Per-section entry keys that failed, so repair can target them.
    failed_entries: dict[str, list[str]] = Field(default_factory=dict)


def claim_id_for(section: str, key: str, content: Any) -> str:
    payload = json.dumps({"section": section, "key": key, "content": content}, sort_keys=True, ensure_ascii=True)
    return "ek-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


_SECTION_KEYS: dict[str, str] = {
    "designs": "unit_id", "systems": "system_id", "journeys": "journey_id",
    "mappings": "table_id", "configuration": "key", "error_paths": "error_path_id",
    "runtime": "unit_id", "business": "journey_id", "contradictions": "contradiction_id",
    "unknowns": "unknown_id",
}


def assign_claim_ids(model: KnowledgeModel) -> KnowledgeModel:
    for section, key_field in _SECTION_KEYS.items():
        for entry in getattr(model, section):
            content = entry.model_dump(mode="json", exclude={"claim_id"})
            entry.claim_id = claim_id_for(section, str(getattr(entry, key_field)), content)
    return model


def _evidence_root_ok(path: str) -> bool:
    return any(path == root or path.startswith(root) for root in EVIDENCE_ROOTS)


def _check_evidence(
    staging_dir: Path, refs: list[EvidenceRef], where: str, errors: list[str], cache: dict[str, str | None],
    *, required: bool = True,
) -> None:
    if required and not refs:
        errors.append(f"{where}: no evidence reference")
        return
    for ref in refs:
        path = ref.path.replace("\\", "/").lstrip("./")
        if not _evidence_root_ok(path):
            errors.append(f"{where}: evidence path outside the evidence roots: {path}")
            continue
        if ".." in Path(path).parts:
            errors.append(f"{where}: evidence path escapes staging: {path}")
            continue
        if path not in cache:
            candidate = staging_dir / path
            text: str | None = None
            if candidate.is_file():
                try:
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
            cache[path] = text
        text = cache[path]
        if text is None:
            errors.append(f"{where}: evidence file does not exist: {path}")
            continue
        if ref.quote and ref.quote.strip() and ref.quote.strip() not in text:
            errors.append(f"{where}: quoted evidence not found in {path}")


def validate_knowledge_model(
    model: KnowledgeModel,
    *,
    staging_dir: Path,
    unit_ids: set[str],
    mapping_table_ids: set[str],
    entry_point_units: set[str],
    error_handling_units: set[str],
    context_group_keys: set[str],
    inline_context_units: set[str],
) -> ValidationReport:
    """Grounding + completeness. Errors block writing; warnings are recorded."""
    errors: list[str] = []
    warnings: list[str] = []
    failed: dict[str, set[str]] = {section: set() for section in _SECTION_KEYS}
    cache: dict[str, str | None] = {}

    def fail(section: str, key: str, message: str) -> None:
        errors.append(message)
        failed[section].add(key)

    # --- identities -------------------------------------------------------
    for section, key_field in _SECTION_KEYS.items():
        seen: set[str] = set()
        for entry in getattr(model, section):
            key = str(getattr(entry, key_field))
            if section in {"runtime", "business", "designs"}:
                composite = key
            else:
                composite = key
            if composite in seen and section not in {"business"}:
                fail(section, key, f"{section}: duplicate entry {key}")
            seen.add(composite)

    # --- designs: every unit, grounded ------------------------------------
    design_units = {d.unit_id for d in model.designs}
    for unit in sorted(unit_ids - design_units):
        fail("designs", unit, f"designs: unit not analysed: {unit}")
    for design in model.designs:
        if design.unit_id not in unit_ids:
            fail("designs", design.unit_id, f"designs: unknown unit {design.unit_id}")
        before = len(errors)
        _check_evidence(staging_dir, design.evidence, f"designs[{design.unit_id}]", errors, cache)
        if len(errors) > before:
            failed["designs"].add(design.unit_id)
    for system in model.systems:
        before = len(errors)
        _check_evidence(staging_dir, system.evidence, f"systems[{system.system_id}]", errors, cache)
        if len(errors) > before:
            failed["systems"].add(system.system_id)

    # --- journeys: entry points and full-estate placement ----------------
    covered_units: set[str] = set()
    error_ids = {e.error_path_id for e in model.error_paths}
    for journey in model.journeys:
        covered_units.update(journey.implementing_units)
        covered_units.add(journey.trigger.unit_id)
        covered_units.update(step.unit_id for step in journey.steps)
        for unit in [journey.trigger.unit_id, *journey.implementing_units, *(s.unit_id for s in journey.steps)]:
            if unit not in unit_ids:
                fail("journeys", journey.journey_id, f"journeys[{journey.journey_id}]: unknown unit {unit}")
        for table_id in journey.mapping_table_ids:
            if table_id not in mapping_table_ids:
                fail("journeys", journey.journey_id, f"journeys[{journey.journey_id}]: unknown mapping table {table_id}")
        for error_id in journey.error_path_ids:
            if error_id not in error_ids:
                fail("journeys", journey.journey_id, f"journeys[{journey.journey_id}]: unknown error path {error_id}")
        orders = [step.order for step in journey.steps]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            fail("journeys", journey.journey_id, f"journeys[{journey.journey_id}]: step order is not strictly increasing")
        before = len(errors)
        _check_evidence(staging_dir, journey.evidence, f"journeys[{journey.journey_id}]", errors, cache)
        for step in journey.steps:
            _check_evidence(staging_dir, step.evidence, f"journeys[{journey.journey_id}].step{step.order}", errors, cache, required=False)
        if len(errors) > before:
            failed["journeys"].add(journey.journey_id)
    excluded = {e.unit_id: e.reason for e in model.excluded_units}
    for unit in sorted(entry_point_units - covered_units):
        if unit not in excluded:
            fail("journeys", unit, f"journeys: entry point not placed in any journey and not excluded with a reason: {unit}")
    for unit in sorted(unit_ids - covered_units):
        if unit not in excluded:
            fail("journeys", unit, f"journeys: design not placed in any journey and not excluded with a reason: {unit}")
    for unit in sorted(set(excluded) & covered_units):
        warnings.append(f"journeys: {unit} is both placed and excluded; exclusion ignored")

    # --- mappings: every exact table interpreted ---------------------------
    interpreted = {m.table_id for m in model.mappings}
    for table_id in sorted(mapping_table_ids - interpreted):
        fail("mappings", table_id, f"mappings: exact table not interpreted: {table_id}")
    for mapping in model.mappings:
        if mapping.table_id not in mapping_table_ids:
            fail("mappings", mapping.table_id, f"mappings: unknown table {mapping.table_id}")
        if _looks_like_field_table(mapping.summary) or any(_looks_like_field_table(r) for r in mapping.rules):
            fail("mappings", mapping.table_id, f"mappings[{mapping.table_id}]: reproduces a field table instead of referencing it")
        before = len(errors)
        _check_evidence(staging_dir, mapping.evidence, f"mappings[{mapping.table_id}]", errors, cache)
        if len(errors) > before:
            failed["mappings"].add(mapping.table_id)

    # --- configuration ---------------------------------------------------
    config_keys = {(c.scope, c.key) for c in model.configuration}
    for key in sorted(context_group_keys):
        if ("context_group", key) not in config_keys:
            fail("configuration", key, f"configuration: context group not explained: {key}")
    for unit in sorted(inline_context_units):
        if ("unit", unit) not in config_keys:
            fail("configuration", unit, f"configuration: unit with inline context not explained: {unit}")
    for entry in model.configuration:
        before = len(errors)
        _check_evidence(staging_dir, entry.evidence, f"configuration[{entry.key}]", errors, cache)
        if len(errors) > before:
            failed["configuration"].add(entry.key)

    # --- error paths ------------------------------------------------------
    units_with_paths = {e.unit_id for e in model.error_paths}
    for unit in sorted(error_handling_units - units_with_paths):
        fail("error_paths", unit, f"error_paths: unit with error-handling constructs has no error path: {unit}")
    for path in model.error_paths:
        if path.unit_id not in unit_ids:
            fail("error_paths", path.error_path_id, f"error_paths[{path.error_path_id}]: unknown unit {path.unit_id}")
        before = len(errors)
        _check_evidence(staging_dir, path.evidence, f"error_paths[{path.error_path_id}]", errors, cache)
        if len(errors) > before:
            failed["error_paths"].add(path.error_path_id)

    # --- runtime: every unit has an explicit status ------------------------
    runtime_units = {r.unit_id for r in model.runtime}
    for unit in sorted(unit_ids - runtime_units):
        fail("runtime", unit, f"runtime: no runtime status for {unit}")
    for observation in model.runtime:
        if observation.status == "observed":
            before = len(errors)
            _check_evidence(staging_dir, observation.evidence, f"runtime[{observation.unit_id}]", errors, cache)
            if len(errors) > before:
                failed["runtime"].add(observation.unit_id)
        elif observation.evidence:
            _check_evidence(staging_dir, observation.evidence, f"runtime[{observation.unit_id}]", errors, cache, required=False)

    # --- business meaning / contradictions / unknowns ----------------------
    journey_ids = {j.journey_id for j in model.journeys}
    for meaning in model.business:
        if meaning.journey_id not in journey_ids:
            fail("business", meaning.journey_id, f"business: unknown journey {meaning.journey_id}")
        if meaning.status == "evidenced" and not meaning.evidence:
            fail("business", meaning.journey_id, f"business[{meaning.journey_id}]: evidenced meaning without evidence")
        if meaning.status == "evidenced" and meaning.source_authority == "inference":
            fail("business", meaning.journey_id, f"business[{meaning.journey_id}]: evidenced meaning cannot rest on inference")
        _check_evidence(staging_dir, meaning.evidence, f"business[{meaning.journey_id}]", errors, cache, required=False)
    for journey_id in sorted(journey_ids - {m.journey_id for m in model.business}):
        fail("business", journey_id, f"business: journey has no business meaning entry (evidenced, inferred or unknown): {journey_id}")
    for contradiction in model.contradictions:
        if len(set(contradiction.authorities)) < 2:
            fail("contradictions", contradiction.contradiction_id, f"contradictions[{contradiction.contradiction_id}]: needs two distinct authorities")
        before = len(errors)
        _check_evidence(staging_dir, contradiction.evidence, f"contradictions[{contradiction.contradiction_id}]", errors, cache)
        if len(errors) > before:
            failed["contradictions"].add(contradiction.contradiction_id)

    coverage = {
        "units": len(unit_ids), "designs": len(design_units & unit_ids),
        "entry_points": len(entry_point_units),
        "entry_points_in_journeys": len(entry_point_units & covered_units),
        "units_in_journeys": len(unit_ids & covered_units), "excluded_units": len(excluded),
        "journeys": len(model.journeys), "mapping_tables": len(mapping_table_ids),
        "mapping_tables_interpreted": len(mapping_table_ids & interpreted),
        "context_groups": len(context_group_keys),
        "error_handling_units": len(error_handling_units),
        "error_paths": len(model.error_paths), "runtime_statuses": len(runtime_units & unit_ids),
        "contradictions": len(model.contradictions), "unknowns": len(model.unknowns),
    }
    return ValidationReport(
        ok=not errors, errors=errors, warnings=warnings, coverage=coverage,
        failed_entries={section: sorted(keys) for section, keys in failed.items() if keys},
    )


def _looks_like_field_table(text: str) -> bool:
    """A markdown table with several rows inside prose is a retyped mapping table."""
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    return len(rows) >= 4


def load_knowledge_model(staging_dir: Path) -> KnowledgeModel | None:
    path = staging_dir / KNOWLEDGE_MODEL
    if not path.is_file():
        return None
    try:
        return KnowledgeModel.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


def persist_knowledge_model(staging_dir: Path, model: KnowledgeModel, report: ValidationReport) -> None:
    write_json(staging_dir / KNOWLEDGE_MODEL, model)
    write_json(staging_dir / KNOWLEDGE_VALIDATION, report)
