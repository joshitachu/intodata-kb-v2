"""Unit tests for EvidenceTraceabilityValidator."""
from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.providers.custom.evidence_traceability import (
    AbsenceClaim,
    EvidenceTraceabilityValidator,
    TraceabilityReport,
)


def _make_facts(unit_names: list[str]) -> MagicMock:
    facts = MagicMock()
    units = []
    for n in unit_names:
        u = MagicMock()
        u.name = n
        units.append(u)
    facts.units = units
    return facts


def _make_report(group: str, evidenced_units: list[str]) -> MagicMock:
    report = MagicMock()
    report.group = group
    report.evidence = [MagicMock(unit_name=u) for u in evidenced_units]
    return report


def test_phantom_absence_detected() -> None:
    facts = _make_facts(["Web.config", "Global.asax.cs"])
    report = _make_report("config", ["Web.config", "Global.asax.cs"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "The file Web.config was not available in the scan output."
    result = validator.validate("02_COMPONENT_INVENTORY", doc_text)
    assert not result.ok
    assert len(result.phantom_absences) == 1
    assert result.phantom_absences[0].unit_name == "Web.config"
    assert len(result.repair_hints()) == 1
    assert "Web.config" in result.repair_hints()[0]


def test_no_false_positive_when_no_evidence() -> None:
    facts = _make_facts(["some_job.xml"])
    report = _make_report("group1", [])  # No evidence items
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "some_job.xml was not available."
    result = validator.validate("doc1", doc_text)
    assert result.ok  # No evidence → no phantom claim


def test_no_phantom_when_unit_not_mentioned() -> None:
    facts = _make_facts(["Web.config"])
    report = _make_report("group1", ["Web.config"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "The configuration is well-documented."  # No mention of Web.config
    result = validator.validate("doc1", doc_text)
    assert result.ok
    assert "Web.config" in result.missing_units


def test_no_phantom_when_unit_mentioned_without_absence() -> None:
    facts = _make_facts(["Web.config"])
    report = _make_report("group1", ["Web.config"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "Web.config defines connection strings for the database layer."
    result = validator.validate("doc1", doc_text)
    assert result.ok
    assert "Web.config" in result.cited_units


def test_multiple_absence_patterns_detected() -> None:
    facts = _make_facts(["app.config"])
    report = _make_report("config", ["app.config"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    for phrase in [
        "app.config was unavailable during analysis.",
        "no evidence for app.config was found.",
        "app.config cannot be identified from scan output.",
        "app.config is scanner-limited.",
    ]:
        result = validator.validate("doc1", phrase)
        assert not result.ok, f"Expected phantom absence for: {phrase!r}"
        assert len(result.phantom_absences) == 1


def test_evidence_path_includes_group() -> None:
    facts = _make_facts(["Global.asax.cs"])
    report = _make_report("webapp_root", ["Global.asax.cs"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "Global.asax.cs was not found in the scan."
    result = validator.validate("03_ARCHITECTURE", doc_text)
    assert not result.ok
    assert result.phantom_absences[0].evidence_path == "_investigation/webapp_root.md"


def test_repair_hints_reference_evidence_path() -> None:
    facts = _make_facts(["Web.config"])
    report = _make_report("config_group", ["Web.config"])
    validator = EvidenceTraceabilityValidator(
        investigation_reports=[report],
        facts=facts,
        investigation_dir=Path("/tmp"),
    )
    doc_text = "Web.config was not available."
    result = validator.validate("02_COMPONENT_INVENTORY", doc_text)
    hints = result.repair_hints()
    assert len(hints) == 1
    assert "_investigation/config_group.md" in hints[0]
    assert "Web.config" in hints[0]
