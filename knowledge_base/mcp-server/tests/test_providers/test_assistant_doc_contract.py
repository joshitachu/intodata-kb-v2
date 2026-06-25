"""Tests for the assistant doc contract term-matching gate.

Covers the regression where a brittle exact-string match falsely reported an
obligation as missing when a doc phrased it with a singular/plural variant
(e.g. "External System Dependencies" failing the "External Systems" obligation),
while still failing docs that genuinely omit the concept.
"""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.validators import (
    _assistant_doc_contract_errors,
    _term_satisfied,
)


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform="generic",
        canonical_unit_label="code units",
        unit_count=1,
        groups={"A": 1},
        units=[UnitFact(name="Unit A", group="A")],
    )


# --- _term_satisfied unit-level behavior --------------------------------------


def test_required_plural_satisfied_by_singular() -> None:
    # Regression: the architecture obligation lists "External Systems" but a
    # correct doc wrote "External System Dependencies".
    assert _term_satisfied("External Systems", "### External System Dependencies")


def test_required_singular_satisfied_by_plural() -> None:
    assert _term_satisfied("External System", "a list of External Systems here")


def test_term_genuinely_absent_still_fails() -> None:
    assert not _term_satisfied("External Systems", "nothing relevant here at all")


def test_word_order_is_still_enforced() -> None:
    # Conservative: words must appear adjacent and in order, so a reversed phrase
    # does not falsely satisfy the obligation.
    assert not _term_satisfied("Entry Points", "Points of Entry reversed")


def test_other_plural_obligations_generalize_both_ways() -> None:
    assert _term_satisfied("Red Flags", "## Red Flag")
    assert _term_satisfied("Quick Wins", "Quick Win analysis")
    assert _term_satisfied("Entry Points", "Entry Point inventory")


# --- _assistant_doc_contract_errors integration -------------------------------


def _write_satisfying_docs(staging: Path) -> None:
    docs = {
        "00_AGENT_BRIEF": "Onboarding. Documentation Map. Common Task. Known Trap. Glossary.",
        "01_ARCHITECTURE_MAP": (
            "Topology. Boundaries. Dependencies. Entry Point. "
            "### External System Dependencies\nSome external system row."
        ),
        "03_DATA_FLOWS_AND_LINEAGE": (
            "Source. Target. Transformation. Schema. Validation. Scanner-Limited."
        ),
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS": "Expression. Unit. Domain. Impact.",
        "05_RUNTIME_CONFIG_AND_EXTERNALS": (
            "External System. Config. Redacted. Scanner-Limited."
        ),
        "07_QUALITY_SECURITY_RISK_SCORECARD": (
            "Score. Confidence. Evidence. Red Flag. Quick Win. Unknowns."
        ),
        "08_CHANGE_PLAYBOOK": "Change Impact. Inspect. Test. Rollback.",
        "06_OPERATIONS_TESTING_AND_DEBUGGING": "Debug. Verify. Recovery. Testing.",
    }
    for name, body in docs.items():
        (staging / f"{name}.md").write_text(body, encoding="utf-8")


def test_singular_phrasing_does_not_trip_architecture_obligation(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    _write_satisfying_docs(staging)

    errors = _assistant_doc_contract_errors(staging, _facts())
    arch_errors = [e for e in errors if "01_ARCHITECTURE_MAP.md missing" in e]
    assert arch_errors == [], arch_errors


def test_missing_external_systems_still_reported(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    _write_satisfying_docs(staging)
    # Strip every external-system mention from the architecture doc.
    (staging / "01_ARCHITECTURE_MAP.md").write_text(
        "Topology. Boundaries. Dependencies. Entry Point.\nNothing about the outside world.",
        encoding="utf-8",
    )

    errors = _assistant_doc_contract_errors(staging, _facts())
    assert any(
        "01_ARCHITECTURE_MAP.md missing architecture map obligations: External Systems" in e
        for e in errors
    ), errors


def test_missing_scorecard_plural_obligation_still_reported(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    _write_satisfying_docs(staging)
    (staging / "07_QUALITY_SECURITY_RISK_SCORECARD.md").write_text(
        "Score. Confidence. Evidence. Unknowns.",  # no Red Flag(s) / Quick Win(s)
        encoding="utf-8",
    )

    errors = _assistant_doc_contract_errors(staging, _facts())
    scorecard_errors = [
        e for e in errors if "07_QUALITY_SECURITY_RISK_SCORECARD.md missing" in e
    ]
    assert scorecard_errors, errors
    assert "Red Flags" in scorecard_errors[0]
    assert "Quick Wins" in scorecard_errors[0]
