"""JSON serializers for EngineeringFacts artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts


JSON_ARTIFACTS: dict[str, str] = {
    "entity_index": "entities",
    "usage_index": "usage",
    "dependency_map": "dependencies",
    "change_impact_matrix": "impacts",
    "architecture_map": "architecture",
    "data_flow_map": "data_flows",
    "api_surface": "api_surface",
    "config_runtime_map": "config_runtime",
    "test_coverage_map": "test_coverage",
    "known_risks": "known_risks",
}


class JsonFactWriter:
    """Write per-artifact JSON files and a complete aggregate."""

    def write(self, facts: EngineeringFacts, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        aggregate = output_dir / "engineering_facts.json"
        aggregate.write_text(facts.model_dump_json(indent=2), encoding="utf-8")
        written.append(aggregate)
        for artifact, field_name in JSON_ARTIFACTS.items():
            values = getattr(facts, field_name)
            if not values:
                continue
            path = output_dir / f"{artifact}.json"
            path.write_text(json.dumps(_dump(values), indent=2), encoding="utf-8")
            written.append(path)
        return written


def _dump(values: list[BaseModel]) -> list[dict[str, Any]]:
    return [value.model_dump(mode="json") for value in values]
