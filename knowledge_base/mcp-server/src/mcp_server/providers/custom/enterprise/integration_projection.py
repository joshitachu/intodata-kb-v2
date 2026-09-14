"""Integration catalogue and conditional dossiers projected from the model.

Deterministic grouping and rendering (plan decisions 8 and 9): every detected
interface appears once in the catalogue; related technical paths group by the
supplied business capability where the model recorded one, otherwise by exact
system pair and direction; a group becomes a dossier only when it is complex or
material. Exact mapping tables are embedded verbatim; the model's job is the
explanation around them.

Dossiers are published as top-level ``INTEGRATION__<slug>.md`` documents. The
plan named an ``integrations/`` sub-directory, but every publication consumer
(doc listing, doc count, RAG push, PDF export, the viewer route, quality scoring)
enumerates top-level ``docs/*.md`` and the viewer rejects path separators, so a
sub-directory would have made every dossier invisible or unreachable.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.custom.enterprise.client_language import strip_internal_markers
from mcp_server.providers.custom.enterprise.knowledge_model import KnowledgeModel
from mcp_server.providers.custom.enterprise.layout import (
    CATALOGUE_DOC,
    DOC_PROVENANCE_ROOT,
    DOSSIER_PLAN,
    EXACT_MAPPING_TABLES,
    dossier_doc_name,
)
from mcp_server.providers.custom.knowledge_contracts import write_json

DOSSIER_SECTIONS: tuple[str, ...] = (
    "overview", "business_purpose", "end_to_end_flow", "mapping_notes", "configuration",
    "operations", "errors_and_recovery", "risks_and_open_questions",
)
CATALOGUE_SECTIONS: tuple[str, ...] = ("overview", "how_to_read", "estate_observations")

_SECTION_TITLES = {
    "overview": "Overview",
    "business_purpose": "Business purpose",
    "end_to_end_flow": "End-to-end flow",
    "mapping_notes": "Data mapping",
    "configuration": "Configuration and environments",
    "operations": "Operations and monitoring",
    "errors_and_recovery": "Errors and recovery",
    "risks_and_open_questions": "Risks and open questions",
    "how_to_read": "How to read this catalogue",
    "estate_observations": "Estate-level observations",
}


class InterfaceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interface_id: str
    unit_id: str
    display_name: str
    kind: str
    type: str
    direction: str
    target: str = ""
    journey_ids: list[str] = Field(default_factory=list)
    dossier: str = ""


class DossierPlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    title: str
    basis: str  # business_capability | system_pair | single_journey
    journey_ids: list[str]
    unit_ids: list[str]
    mapping_table_ids: list[str]
    error_path_ids: list[str]
    interface_ids: list[str]
    material: bool
    materiality_reasons: list[str] = Field(default_factory=list)


class DossierPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "enterprise-dossier-plan-1"
    dossiers: list[DossierPlanEntry]
    catalogue_only: list[DossierPlanEntry]
    interfaces: list[InterfaceRow]
    required_docs: list[str]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:60] or "integration"


def _interface_rows(model: KnowledgeModel, exact_units: dict[str, dict[str, Any]], interfaces: dict[str, Any]) -> list[InterfaceRow]:
    journeys_by_unit: dict[str, list[str]] = {}
    for journey in model.journeys:
        for unit in {journey.trigger.unit_id, *journey.implementing_units, *(s.unit_id for s in journey.steps)}:
            journeys_by_unit.setdefault(unit, []).append(journey.journey_id)
    rows: list[InterfaceRow] = []
    for unit_id, items in sorted((interfaces.get("by_unit") or {}).items()):
        unit = exact_units.get(unit_id, {})
        for item in items:
            target = str(item.get("channel") or item.get("resource") or item.get("family") or item.get("repository_connection") or "")
            key = f"{unit_id}|{item.get('kind')}|{item.get('type')}|{item.get('component_name')}|{target}"
            rows.append(InterfaceRow(
                interface_id="if-" + hashlib.sha256(key.encode()).hexdigest()[:12],
                unit_id=unit_id, display_name=str(unit.get("display_name") or unit_id),
                kind=str(item.get("kind") or ""), type=str(item.get("type") or ""),
                direction=str(item.get("direction") or "unknown"), target=target,
                journey_ids=sorted(set(journeys_by_unit.get(unit_id, []))),
            ))
    return rows


def build_dossier_plan(
    model: KnowledgeModel,
    exact_units: dict[str, dict[str, Any]],
    interfaces: dict[str, Any],
    *,
    min_designs: int,
) -> DossierPlan:
    groups: dict[str, dict[str, Any]] = {}
    for journey in sorted(model.journeys, key=lambda j: j.journey_id):
        if journey.business_capability.strip():
            key = "cap:" + _slug(journey.business_capability)
            title = journey.business_capability.strip()
            basis = "business_capability"
        else:
            sources = sorted({journey.trigger.unit_id and exact_units.get(journey.trigger.unit_id, {}).get("group", "")})
            destinations = sorted({d.system for d in journey.destinations}) or ["unspecified"]
            pair = f"{journey.trigger.kind}:{','.join(s for s in sources if s)}->{','.join(destinations)}"
            key = "pair:" + _slug(pair)
            title = f"{journey.trigger.kind} to {', '.join(destinations)}"
            basis = "system_pair"
        group = groups.setdefault(key, {"title": title, "basis": basis, "journeys": []})
        group["journeys"].append(journey)
    rows = _interface_rows(model, exact_units, interfaces)
    rows_by_unit: dict[str, list[InterfaceRow]] = {}
    for row in rows:
        rows_by_unit.setdefault(row.unit_id, []).append(row)
    error_by_unit: dict[str, list[str]] = {}
    for path in model.error_paths:
        error_by_unit.setdefault(path.unit_id, []).append(path.error_path_id)
    dossiers: list[DossierPlanEntry] = []
    catalogue_only: list[DossierPlanEntry] = []
    used_slugs: set[str] = set()
    for key, group in sorted(groups.items()):
        journeys = group["journeys"]
        units = sorted({u for j in journeys for u in {j.trigger.unit_id, *j.implementing_units, *(s.unit_id for s in j.steps)}})
        tables = sorted({t for j in journeys for t in j.mapping_table_ids} | {
            t for u in units for t in exact_units.get(u, {}).get("mapping_table_ids", [])
        })
        errors = sorted({e for u in units for e in error_by_unit.get(u, [])})
        interface_ids = sorted({r.interface_id for u in units for r in rows_by_unit.get(u, [])})
        reasons = []
        if len(units) >= min_designs:
            reasons.append(f"{len(units)} designs")
        if tables:
            reasons.append(f"{len(tables)} exact mapping tables")
        if errors:
            reasons.append(f"{len(errors)} error paths")
        base_slug = _slug(group["title"]) if group["basis"] == "business_capability" else key.split(":", 1)[1]
        slug = base_slug
        counter = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        used_slugs.add(slug)
        entry = DossierPlanEntry(
            slug=slug, title=group["title"], basis=group["basis"] if len(journeys) > 1 else (
                group["basis"] if group["basis"] == "business_capability" else "single_journey"),
            journey_ids=[j.journey_id for j in journeys], unit_ids=units, mapping_table_ids=tables,
            error_path_ids=errors, interface_ids=interface_ids, material=bool(reasons), materiality_reasons=reasons,
        )
        (dossiers if entry.material else catalogue_only).append(entry)
    dossier_by_interface = {iid: d.slug for d in dossiers for iid in d.interface_ids}
    for row in rows:
        row.dossier = dossier_by_interface.get(row.interface_id, "")
    required = [CATALOGUE_DOC, *(dossier_doc_name(d.slug) for d in dossiers)]
    return DossierPlan(dossiers=dossiers, catalogue_only=catalogue_only, interfaces=rows, required_docs=required)


def write_dossier_plan(staging_dir: Path, plan: DossierPlan) -> None:
    write_json(staging_dir / DOSSIER_PLAN, plan)


def load_dossier_plan(staging_dir: Path) -> DossierPlan | None:
    path = staging_dir / DOSSIER_PLAN
    if not path.is_file():
        return None
    try:
        return DossierPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").encode("ascii", "replace").decode("ascii")


def _exact_table_body(staging_dir: Path, table_id: str) -> str:
    path = staging_dir / EXACT_MAPPING_TABLES / f"{table_id}.md"
    if not path.is_file():
        return f"Exact mapping table {table_id} is not available in this run."
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # Drop the H1 and the internal provenance sentence; keep the counts and rows.
    body = [line for line in lines[1:] if "Deterministic scanner extraction" not in line]
    return "\n".join(body).strip()


def _display(exact_units: dict[str, dict[str, Any]], unit_id: str) -> str:
    unit = exact_units.get(unit_id, {})
    version = unit.get("version") or ""
    name = unit.get("display_name") or unit_id
    return f"{name} (v{version})" if version else str(name)


def render_dossier(
    entry: DossierPlanEntry,
    sections: dict[str, dict[str, Any]],
    model: KnowledgeModel,
    exact_units: dict[str, dict[str, Any]],
    interfaces: list[InterfaceRow],
    staging_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Compose the client document; returns (markdown, provenance sidecar)."""
    journeys = [j for j in model.journeys if j.journey_id in set(entry.journey_ids)]
    runtime = {r.unit_id: r for r in model.runtime}
    lines = [f"# Integration: {_cell(entry.title)}", ""]
    lines += [
        "This dossier describes one integration of the Talend estate end to end: what starts it, "
        "which designs implement it, which systems it touches, how data is mapped, how it is "
        "configured, how it fails and recovers, and what was observed at runtime. Statements "
        "about business purpose are marked as evidenced or inferred.", "",
    ]
    provenance: dict[str, Any] = {"doc": dossier_doc_name(entry.slug), "sections": {}, "journey_ids": entry.journey_ids,
                                  "mapping_table_ids": entry.mapping_table_ids, "unit_ids": entry.unit_ids}

    def add_section(name: str, deterministic_tail: list[str] | None = None) -> None:
        section = sections.get(name) or {}
        text = strip_internal_markers(str(section.get("text") or "")).strip()
        lines.append(f"## {_SECTION_TITLES[name]}")
        lines.append("")
        if text:
            lines.append(text)
            lines.append("")
        if deterministic_tail:
            lines.extend(deterministic_tail)
            lines.append("")
        provenance["sections"][name] = {
            "claim_ids": list(section.get("claim_ids") or []),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    add_section("overview")
    meanings = {m.journey_id: m for m in model.business}
    purpose_tail: list[str] = []
    for journey in journeys:
        meaning = meanings.get(journey.journey_id)
        status = meaning.status if meaning else "unknown"
        label = {"evidenced": "Evidenced", "inferred": "Inferred from the implementation", "unknown": "Unknown"}[status]
        purpose_tail.append(f"- {_cell(journey.name)}: {label}.")
    add_section("business_purpose", purpose_tail)
    flow_tail: list[str] = []
    for journey in journeys:
        flow_tail.append(f"### {_cell(journey.name)}")
        flow_tail.append("")
        flow_tail.append(f"Trigger: {_cell(journey.trigger.kind)} at {_display(exact_units, journey.trigger.unit_id)}"
                         + (f" - {_cell(journey.trigger.detail)}" if journey.trigger.detail else "") + ".")
        flow_tail.append("")
        flow_tail.append("| Step | Design | Mechanism | What happens |")
        flow_tail.append("|---|---|---|---|")
        for step in journey.steps:
            flow_tail.append(f"| {step.order} | {_display(exact_units, step.unit_id)} | {_cell(step.mechanism)} | {_cell(step.description)} |")
        if journey.destinations:
            flow_tail.append("")
            flow_tail.append("Destinations: " + "; ".join(
                f"{_cell(d.system)} via {_cell(d.mechanism) or 'unspecified mechanism'}" for d in journey.destinations))
        flow_tail.append("")
        flow_tail.append(f"Confidence: {journey.confidence}.")
        flow_tail.append("")
    add_section("end_to_end_flow", flow_tail)
    iface_tail = ["| Design | Interface | Direction | Target |", "|---|---|---|---|"]
    for row in interfaces:
        if row.interface_id in set(entry.interface_ids):
            iface_tail.append(f"| {_cell(row.display_name)} | {_cell(row.type or row.kind)} | {_cell(row.direction)} | {_cell(row.target) or '-'} |")
    lines.append("## Systems and interfaces")
    lines.append("")
    lines.extend(iface_tail)
    lines.append("")
    mapping_tail: list[str] = []
    interpretations = {m.table_id: m for m in model.mappings}
    for table_id in entry.mapping_table_ids:
        interpretation = interpretations.get(table_id)
        owner = _display(exact_units, interpretation.unit_id) if interpretation else "unknown design"
        component = interpretation.component_name if interpretation else ""
        mapping_tail.append(f"### Mapping in {owner}" + (f", {_cell(component)}" if component else ""))
        mapping_tail.append("")
        if interpretation:
            mapping_tail.append(strip_internal_markers(interpretation.summary).strip())
            mapping_tail.append("")
            if interpretation.rules:
                mapping_tail.extend(f"- {strip_internal_markers(rule).strip()}" for rule in interpretation.rules)
                mapping_tail.append("")
        mapping_tail.append(f"<a id=\"mapping-{table_id}\"></a>")
        mapping_tail.append(f"#### Exact mapping table {table_id}")
        mapping_tail.append("")
        mapping_tail.append(_exact_table_body(staging_dir, table_id))
        mapping_tail.append("")
    if not entry.mapping_table_ids:
        mapping_tail.append("No field mapping component is part of this integration (deterministic scan).")
    add_section("mapping_notes", mapping_tail)
    add_section("configuration")
    add_section("operations")
    error_tail: list[str] = []
    for path in model.error_paths:
        if path.error_path_id in set(entry.error_path_ids):
            error_tail.append(f"- {_display(exact_units, path.unit_id)}: {_cell(path.trigger)} -> {_cell(path.handling)}."
                              + (f" Recovery: {_cell(path.recovery)}." if path.recovery else "")
                              + (f" Blast radius: {_cell(path.blast_radius)}." if path.blast_radius else ""))
    no_handling = [u for u in entry.unit_ids if not exact_units.get(u, {}).get("has_error_handling")]
    if no_handling:
        error_tail.append("- No error-handling construct was found in: " + ", ".join(_display(exact_units, u) for u in no_handling) + ".")
    add_section("errors_and_recovery", error_tail)
    runtime_tail = ["| Design | Runtime status | Environments | Observation |", "|---|---|---|---|"]
    for unit in entry.unit_ids:
        observation = runtime.get(unit)
        if observation is None:
            runtime_tail.append(f"| {_display(exact_units, unit)} | unavailable | - | No runtime status recorded. |")
            continue
        status = observation.status.replace("_", " ")
        runtime_tail.append(f"| {_display(exact_units, unit)} | {status} | {_cell(', '.join(observation.environments)) or '-'} | {_cell(observation.summary)} |")
    runtime_tail.append("")
    runtime_tail.append("A missing or unavailable observation means no runtime record was available for the read "
                        "window; it does not mean the design never ran.")
    lines.append("## Runtime observations")
    lines.append("")
    lines.extend(runtime_tail)
    lines.append("")
    risk_tail: list[str] = []
    for contradiction in model.contradictions:
        if any(u in contradiction.subject for u in entry.unit_ids) or any(j.name in contradiction.subject for j in journeys):
            risk_tail.append(f"- Contradiction ({contradiction.resolution.replace('_', ' ')}): {_cell(contradiction.subject)} - "
                             + " / ".join(_cell(s) for s in contradiction.statements))
    for unknown in model.unknowns:
        if unknown.subject in set(entry.unit_ids) or unknown.subject in set(entry.journey_ids):
            risk_tail.append(f"- Open question: {_cell(unknown.question)} ({_cell(unknown.reason)})")
    add_section("risks_and_open_questions", risk_tail)
    lines.append("## Design artifacts")
    lines.append("")
    lines.extend(f"- {_display(exact_units, u)} [{_cell(exact_units.get(u, {}).get('kind', 'design'))}]" for u in entry.unit_ids)
    lines.append("")
    return "\n".join(lines).rstrip() + "\n", provenance


def render_catalogue(
    plan: DossierPlan,
    sections: dict[str, dict[str, Any]],
    model: KnowledgeModel,
    exact_units: dict[str, dict[str, Any]],
    capability: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    lines = ["# Integration Catalogue", ""]
    provenance: dict[str, Any] = {"doc": CATALOGUE_DOC, "sections": {}, "journey_ids": [j.journey_id for j in model.journeys]}

    def add_section(name: str, tail: list[str] | None = None) -> None:
        section = sections.get(name) or {}
        text = strip_internal_markers(str(section.get("text") or "")).strip()
        lines.append(f"## {_SECTION_TITLES[name]}")
        lines.append("")
        if text:
            lines.append(text)
            lines.append("")
        if tail:
            lines.extend(tail)
            lines.append("")
        provenance["sections"][name] = {
            "claim_ids": list(section.get("claim_ids") or []),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    add_section("overview")
    add_section("how_to_read", [
        "Each integration below groups related technical paths. Complex or material integrations have "
        "their own dossier; simple flows are fully described in this catalogue. Business purpose is marked "
        "evidenced, inferred or unknown.",
    ])
    lines.append("## Integrations")
    lines.append("")
    names = {j.journey_id: j.name for j in model.journeys}
    lines.append("| Integration | Basis | Journeys | Designs | Mapping tables | Dossier |")
    lines.append("|---|---|---|---|---|---|")
    for entry in plan.dossiers:
        journeys = "; ".join(_cell(names.get(j, j)) for j in entry.journey_ids)
        lines.append(f"| {_cell(entry.title)} | {_cell(entry.basis.replace('_', ' '))} | {journeys} | "
                     f"{len(entry.unit_ids)} | {len(entry.mapping_table_ids)} | [{dossier_doc_name(entry.slug)}]({dossier_doc_name(entry.slug)}.md) |")
    for entry in plan.catalogue_only:
        journeys = "; ".join(_cell(names.get(j, j)) for j in entry.journey_ids)
        lines.append(f"| {_cell(entry.title)} | {_cell(entry.basis.replace('_', ' '))} | {journeys} | "
                     f"{len(entry.unit_ids)} | {len(entry.mapping_table_ids)} | catalogue only |")
    lines.append("")
    if plan.catalogue_only:
        lines.append("### Simple flows")
        lines.append("")
        meanings = {m.journey_id: m for m in model.business}
        for entry in plan.catalogue_only:
            for journey in model.journeys:
                if journey.journey_id in set(entry.journey_ids):
                    meaning = meanings.get(journey.journey_id)
                    lines.append(f"- {_cell(journey.name)}: trigger {_cell(journey.trigger.kind)} at "
                                 f"{_display(exact_units, journey.trigger.unit_id)}; designs "
                                 + ", ".join(_display(exact_units, u) for u in journey.implementing_units)
                                 + "; destinations " + (", ".join(_cell(d.system) for d in journey.destinations) or "none recorded")
                                 + f". Business purpose: {meaning.status if meaning else 'unknown'}.")
        lines.append("")
    lines.append("## Interface inventory")
    lines.append("")
    lines.append("| Design | Interface | Direction | Target | Journeys | Dossier |")
    lines.append("|---|---|---|---|---|---|")
    for row in plan.interfaces:
        lines.append(f"| {_cell(row.display_name)} | {_cell(row.type or row.kind)} | {_cell(row.direction)} | "
                     f"{_cell(row.target) or '-'} | {len(row.journey_ids)} | {_cell(row.dossier) or 'catalogue only'} |")
    lines.append("")
    lines.append("## Coverage and limitations")
    lines.append("")
    lines.append(f"- Designs in the estate: {len(exact_units)}; designs placed in journeys: "
                 f"{len({u for j in model.journeys for u in {j.trigger.unit_id, *j.implementing_units}} & set(exact_units))}; "
                 f"excluded with a stated reason: {len(model.excluded_units)}.")
    for excluded in model.excluded_units:
        lines.append(f"  - {_display(exact_units, excluded.unit_id)}: {_cell(excluded.reason)}")
    for construct in capability.get("constructs", []) or []:
        status = str(construct.get("status") or "")
        if status in {"partial", "dynamic", "unsupported", "unresolved"} and int(construct.get("count") or 0) > 0:
            lines.append(f"- {_cell(construct.get('category'))} / {_cell(construct.get('construct'))}: "
                         f"{construct.get('count')} {status}" + (f" - {_cell(construct.get('note'))}" if construct.get("note") else ""))
    lines.append(f"- Open questions recorded: {len(model.unknowns)}; contradictions recorded: {len(model.contradictions)}.")
    lines.append("")
    add_section("estate_observations")
    return "\n".join(lines).rstrip() + "\n", provenance


def write_client_document(staging_dir: Path, doc_name: str, markdown: str, provenance: dict[str, Any]) -> None:
    (staging_dir / f"{doc_name}.md").write_text(markdown, encoding="utf-8")
    write_json(staging_dir / DOC_PROVENANCE_ROOT / f"{doc_name}.json", provenance)
