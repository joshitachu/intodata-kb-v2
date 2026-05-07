"""CSV serializers for EngineeringFacts artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts


CSV_ARTIFACTS: dict[str, tuple[str, list[str]]] = {
    "entity_index": ("entities", ["id", "entity_type", "name", "path", "unit_name", "confidence"]),
    "usage_index": ("usage", ["id", "usage_type", "name", "entity_id", "location", "confidence"]),
}


class CsvFactWriter:
    """Write tabular fact artifacts."""

    def write(self, facts: EngineeringFacts, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for artifact, (field_name, columns) in CSV_ARTIFACTS.items():
            values = getattr(facts, field_name)
            if not values:
                continue
            path = output_dir / f"{artifact}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[*columns, "evidence"])
                writer.writeheader()
                for value in values:
                    row = _row(value, columns)
                    writer.writerow(row)
            written.append(path)
        return written


def _row(value: BaseModel, columns: list[str]) -> dict[str, Any]:
    dumped = value.model_dump(mode="json")
    row = {column: dumped.get(column, "") for column in columns}
    row["evidence"] = json.dumps(dumped.get("evidence", []), ensure_ascii=True)
    return row
