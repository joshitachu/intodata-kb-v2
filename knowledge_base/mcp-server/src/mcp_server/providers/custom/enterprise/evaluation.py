"""Offline acceptance evaluation for a frozen enterprise Talend fixture.

Consumes retained artifacts only (no provider, no network). Proves exact mapping
rows, connected journeys, dossier coverage, multi-audience question coverage,
internal-path leakage prevention and the publication verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.enterprise.client_language import leakage_findings
from mcp_server.providers.custom.enterprise.integration_projection import load_dossier_plan
from mcp_server.providers.custom.enterprise.knowledge_model import load_knowledge_model
from mcp_server.providers.custom.enterprise.layout import (
    EXACT_MAPPING_TABLES,
    PUBLICATION_VERDICT,
    QUESTION_COVERAGE,
)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def evaluate_enterprise_talend(staging_dir: Path, expected_path: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    model = load_knowledge_model(staging_dir)
    plan = load_dossier_plan(staging_dir)
    verdict = _read(staging_dir / PUBLICATION_VERDICT) or {}
    coverage = _read(staging_dir / QUESTION_COVERAGE) or {}
    tables_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                            for p in sorted((staging_dir / EXACT_MAPPING_TABLES).glob("*.md"))) \
        if (staging_dir / EXACT_MAPPING_TABLES).is_dir() else ""
    public_docs = {p.stem: p.read_text(encoding="utf-8", errors="replace")
                   for p in sorted(staging_dir.glob("*.md")) if not p.name.startswith("_")}
    missing_rows = [row for row in expected.get("exact_mapping_rows", []) if row not in tables_text]
    journey_checks = []
    for spec in expected.get("connected_journeys", []):
        journey = next((j for j in (model.journeys if model else []) if j.journey_id == spec["journey_id"]), None)
        ok = journey is not None and set(spec.get("units", [])) <= {j for j in journey.implementing_units} | {journey.trigger.unit_id}
        journey_checks.append({"journey_id": spec["journey_id"], "ok": ok})
    expected_docs = set(expected.get("required_docs", []))
    docs_ok = expected_docs <= set(public_docs)
    answers = {a["question_id"]: a for a in coverage.get("answers", [])}
    question_checks = []
    for spec in expected.get("questions", []):
        answer = answers.get(spec["question_id"])
        ok = answer is not None and answer["status"] == spec["status"] and all(
            fragment in (answer.get("answer") or answer.get("reason") or "") for fragment in spec.get("contains", []))
        question_checks.append({"question_id": spec["question_id"], "ok": ok, "actual": answer["status"] if answer else "missing"})
    leaks = {name: leakage_findings(text) for name, text in public_docs.items()}
    leaks = {name: found for name, found in leaks.items() if found}
    checks = {
        "exact_mapping_rows_retained": not missing_rows,
        "journeys_connected": all(c["ok"] for c in journey_checks),
        "dossier_coverage": docs_ok and plan is not None and set(plan.required_docs) <= set(public_docs),
        "question_coverage": all(c["ok"] for c in question_checks),
        "no_internal_leakage": not leaks,
        "publication_verdict": bool(verdict.get("ok")) == bool(expected.get("publication_ok", True)),
    }
    return {
        "schema_version": "talend-enterprise-eval-1", "ok": all(checks.values()), "checks": checks,
        "missing_exact_rows": missing_rows, "journeys": journey_checks, "questions": question_checks,
        "leaks": leaks, "verdict": verdict,
    }
