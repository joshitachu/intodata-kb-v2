from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.infrastructure.validation.structural import measure_markdown


FIXTURE = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "evaluation" / "structural_metric_cases.json"


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"])
def test_structural_contract_cases(case: dict[str, object]) -> None:
    metrics = measure_markdown(str(case["markdown"]))
    assert metrics == measure_markdown(str(case["markdown"]))
    for field_name, expected in dict(case["expected"]).items():
        assert getattr(metrics, field_name) == expected


def test_missing_document_is_not_turned_into_an_empty_document() -> None:
    metrics = measure_markdown(None, required_sections=["Overview"])

    assert metrics.document_count == 0
    assert metrics.required_section_count == 1
    assert metrics.required_section_covered_count == 0


def test_required_section_matching_is_case_and_whitespace_insensitive() -> None:
    metrics = measure_markdown("# System   Overview\n", required_sections=["system overview"])

    assert metrics.required_section_covered_count == 1


def test_duplicate_required_sections_are_rejected_after_normalisation() -> None:
    with pytest.raises(ValueError, match="unique"):
        measure_markdown("# Overview", required_sections=["Overview", " overview "])
