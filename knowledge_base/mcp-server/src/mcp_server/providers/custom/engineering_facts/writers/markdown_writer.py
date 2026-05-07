"""Markdown serializers for EngineeringFacts artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts


MARKDOWN_ARTIFACTS: dict[str, tuple[str, str, list[str]]] = {
    "dependency_map": ("dependencies", "Dependency Map", ["source", "target", "dependency_type", "confidence"]),
    "change_impact_matrix": ("impacts", "Change Impact Matrix", ["changed_entity", "impacted_entity", "check", "confidence"]),
    "architecture_map": ("architecture", "Architecture Map", ["name", "layer", "component", "boundary", "confidence"]),
    "data_flow_map": ("data_flows", "Data Flow Map", ["source", "target", "transformation", "schema_hint", "confidence"]),
    "api_surface": ("api_surface", "API Surface", ["method", "route", "handler", "name", "confidence"]),
    "config_runtime_map": ("config_runtime", "Config Runtime Map", ["config_type", "key", "name", "value_hint", "confidence"]),
    "test_coverage_map": ("test_coverage", "Test Coverage Map", ["test_file", "covered_entity", "gap", "suggested_scenario", "confidence"]),
    "known_risks": ("known_risks", "Known Risks", ["severity", "affected_entity", "risk_type", "name", "remediation", "confidence"]),
}


class MarkdownFactWriter:
    """Write human-readable EngineeringFacts artifacts."""

    def write(self, facts: EngineeringFacts, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        summary = output_dir / "engineering_facts.md"
        summary.write_text(_summary(facts), encoding="utf-8")
        written.append(summary)
        for artifact, (field_name, title, columns) in MARKDOWN_ARTIFACTS.items():
            values = getattr(facts, field_name)
            if not values:
                continue
            path = output_dir / f"{artifact}.md"
            path.write_text(_table(title, values, columns), encoding="utf-8")
            written.append(path)
        return written


def _summary(facts: EngineeringFacts) -> str:
    return "\n".join([
        "# Engineering Facts",
        "",
        f"- Project: {facts.project_id}",
        f"- Platform: {facts.platform}",
        f"- Entities: {len(facts.entities)}",
        f"- Usage records: {len(facts.usage)}",
        f"- Dependencies: {len(facts.dependencies)}",
        f"- Data flows: {len(facts.data_flows)}",
        f"- API surface entries: {len(facts.api_surface)}",
        f"- Runtime/config facts: {len(facts.config_runtime)}",
        f"- Test coverage facts: {len(facts.test_coverage)}",
        f"- Known risks: {len(facts.known_risks)}",
        "",
    ])


def _table(title: str, values: list[BaseModel], columns: list[str]) -> str:
    lines = [f"# {title}", "", "|" + "|".join(columns + ["evidence"]) + "|", "|" + "|".join("---" for _ in columns + ["evidence"]) + "|"]
    for value in values:
        dumped = value.model_dump(mode="json")
        row = [_cell(dumped.get(column, "")) for column in columns]
        row.append(_cell(_evidence_label(dumped.get("evidence") or [])))
        lines.append("|" + "|".join(row) + "|")
    lines.append("")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text[:240]


def _evidence_label(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return ""
    first = evidence[0]
    label = str(first.get("path") or "")
    if first.get("line"):
        label += f":{first['line']}"
    if len(evidence) > 1:
        label += f" (+{len(evidence) - 1})"
    return label
