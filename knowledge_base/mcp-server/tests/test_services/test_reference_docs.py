"""Content guardrails for assistant-grade reference docs."""
from __future__ import annotations

import re
from pathlib import Path


REFERENCE_DOCS = [
    "00_AGENT_BRIEF.md",
    "01_ARCHITECTURE_MAP.md",
    "02_COMPONENT_INVENTORY.md",
    "03_DATA_FLOWS_AND_LINEAGE.md",
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md",
    "05_RUNTIME_CONFIG_AND_EXTERNALS.md",
    "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
    "07_QUALITY_SECURITY_RISK_SCORECARD.md",
    "08_CHANGE_PLAYBOOK.md",
]

PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


def _reference_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "intodoc" / "reference-docs"


def test_reference_docs_use_assistant_grade_taxonomy() -> None:
    ref_dir = _reference_dir()

    assert sorted(path.name for path in ref_dir.glob("*.md")) == REFERENCE_DOCS


def test_reference_docs_keep_evidence_confidence_and_boundary_language() -> None:
    ref_dir = _reference_dir()

    for doc_name in REFERENCE_DOCS:
        text = (ref_dir / doc_name).read_text(encoding="utf-8")
        lower = text.lower()

        assert PLACEHOLDER_RE.search(text), doc_name
        assert "evidence" in lower, doc_name
        assert "confidence" in lower, doc_name
        assert "scanner-limited" in lower or "unknown" in lower, doc_name


def test_scorecard_contains_required_exemplars() -> None:
    text = (_reference_dir() / "07_QUALITY_SECURITY_RISK_SCORECARD.md").read_text(
        encoding="utf-8"
    )
    lower = text.lower()

    for required in ("score", "confidence", "evidence", "red flag", "quick win", "unknown"):
        assert required in lower


def test_change_playbook_contains_required_exemplars() -> None:
    text = (_reference_dir() / "08_CHANGE_PLAYBOOK.md").read_text(encoding="utf-8")
    lower = text.lower()

    for required in ("inspect", "test", "verify", "rollback", "affected-unit", "confidence"):
        assert required in lower
