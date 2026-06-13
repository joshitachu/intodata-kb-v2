"""Unit tests for the layered-RAG evaluation harness (pure functions only)."""

import importlib.util
from pathlib import Path

import pytest

# The harness lives under scripts/ (not an installed package) — load by path.
_SPEC = importlib.util.spec_from_file_location(
    "retrieval_eval",
    Path(__file__).resolve().parents[2] / "scripts" / "retrieval_eval.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


# --- Fixture parsing & validation ------------------------------------------


def test_parse_legacy_fixture_shape():
    cases = ev.parse_fixture([
        {"question": "What is the architecture?", "expected_filename": "01_ARCH.md"},
    ])
    assert len(cases) == 1
    assert cases[0].category == "final_doc_answerable"
    assert cases[0].id == "case-1"
    assert cases[0].expected_behavior == "answer"


def test_parse_full_case():
    cases = ev.parse_fixture([{
        "id": "x1", "category": "entity_lookup", "question": "what is resource_logDir?",
        "expected_source_layers": ["support_maps"], "must_pass": True,
    }])
    assert cases[0].id == "x1"
    assert cases[0].must_pass is True
    assert cases[0].expected_source_layers == ["support_maps"]


def test_unsupported_must_refuse():
    with pytest.raises(ValueError, match="must expect refuse"):
        ev.parse_fixture([{
            "id": "u1", "category": "unsupported", "question": "q",
            "expected_behavior": "answer",
        }])


def test_unsupported_cannot_declare_source():
    with pytest.raises(ValueError, match="must not declare an expected source"):
        ev.parse_fixture([{
            "id": "u2", "category": "unsupported", "question": "q",
            "expected_behavior": "refuse", "expected_filename": "x.md",
        }])


def test_answerable_needs_expected_source():
    with pytest.raises(ValueError, match="need expected_filename or expected_source_layers"):
        ev.parse_fixture([{
            "id": "a1", "category": "phase2_only", "question": "q",
            "expected_behavior": "answer",
        }])


# --- Scoring helpers --------------------------------------------------------


def _src(filename, layer=None, section=None, unit=None):
    return ev.SourceLite(filename=filename, source_layer=layer, section=section, unit=unit)


def test_filename_and_layer_hits():
    srcs = [_src("phase2/_internal/technical_analysis/Item.md", layer="technical_analysis")]
    assert ev.filename_hit("Item.md", srcs) is True
    assert ev.filename_hit("Other.md", srcs) is False
    assert ev.layer_hit(["technical_analysis"], srcs) is True
    assert ev.layer_hit(["scorecard"], srcs) is False


def test_section_and_unit_optional():
    srcs = [_src("p/d.md", section="Item > ERROR_HANDLING", unit="sjItem_OffRamp_DB")]
    assert ev.section_hit(None, srcs) is True  # not asserted
    assert ev.section_hit("ERROR_HANDLING", srcs) is True
    assert ev.unit_hit("sjItem_OffRamp_DB", srcs) is True
    assert ev.unit_hit("ghost", srcs) is False


def test_answer_contains_and_forbidden():
    assert ev.answer_contains_ok(["MSSQL"], "Connects to the MSSQL server") is True
    assert ev.answer_contains_ok(["kafka"], "no mention here") is False
    assert ev.forbidden_absent(["rotated every"], "It is rotated every 90 days") is False
    assert ev.forbidden_absent(["rotated every"], "Not documented.") is True


def test_score_answerable_pass():
    case = ev.FixtureCase(
        id="a", category="entity_lookup", question="q",
        expected_source_layers=["support_maps"], expected_answer_contains=["log"],
    )
    passed, reasons = ev.score_ladder_case(
        case, answer="The resource_logDir points to the log directory.",
        sources=[_src("p/_internal/support_maps/config.md", layer="support_maps")],
        primary_source_layer="support_maps",
    )
    assert passed is True
    assert reasons == []


def test_score_answerable_wrong_layer_fails():
    case = ev.FixtureCase(
        id="a", category="entity_lookup", question="q",
        expected_source_layers=["support_maps"],
        expected_primary_source_layer="support_maps",
    )
    passed, reasons = ev.score_ladder_case(
        case, answer="something",
        sources=[_src("p/01_DOC.md", layer="canonical_docs")],
        primary_source_layer="canonical_docs",
    )
    assert passed is False
    assert any("primary layer" in r for r in reasons)


def test_score_refusal_pass_and_leak():
    case = ev.FixtureCase(
        id="u", category="unsupported", question="q", expected_behavior="refuse",
        forbidden_answer_contains=["rotated every"],
    )
    ok, _ = ev.score_ladder_case(
        case, answer="Not documented.", sources=[], primary_source_layer=None,
    )
    assert ok is True

    leaked, reasons = ev.score_ladder_case(
        case, answer="It is rotated every 90 days.", sources=[], primary_source_layer=None,
    )
    assert leaked is False
    assert any("forbidden" in r for r in reasons)


def test_grounded_refusal_with_sources_passes():
    # A grounded "not found" legitimately retrieves canonical chunks first;
    # source presence alone must not fail a refusal (plan-aligned).
    case = ev.FixtureCase(
        id="u", category="unsupported", question="q", expected_behavior="refuse",
        forbidden_answer_contains=["rebalance timeout is"],
    )
    ok, reasons = ev.score_ladder_case(
        case, answer="The documents do not mention Kafka.",
        sources=[_src("p/05_RUNTIME.md")], primary_source_layer="canonical_docs",
    )
    assert ok is True
    assert reasons == []


# --- Verdicts & report rendering -------------------------------------------


def test_compute_verdicts_gates():
    summary = {
        "retrieval": {"recall_at_8": 0.95, "layer_accuracy": 0.92},
        "ladder": {
            "must_pass": {"passed": 3, "failed": 0, "total": 3},
            "unsupported": {"total": 2, "clean": 2, "leaked": 0},
        },
    }
    verdicts = ev.compute_verdicts(summary)
    assert all(v.startswith("PASS") for v in verdicts)


def test_compute_verdicts_failure():
    summary = {
        "retrieval": {"recall_at_8": 0.5, "layer_accuracy": 1.0},
        "ladder": {
            "must_pass": {"passed": 1, "failed": 1, "total": 2},
            "unsupported": {"total": 1, "clean": 0, "leaked": 1},
        },
    }
    verdicts = ev.compute_verdicts(summary)
    assert any(v.startswith("FAIL") and "recall@8" in v for v in verdicts)
    assert any(v.startswith("FAIL") and "must-pass" in v for v in verdicts)
    assert any(v.startswith("FAIL") and "leak" in v for v in verdicts)


def test_render_report_no_raw_internal_paths():
    summary = {
        "meta": {
            "timestamp": "2026-06-13", "git_commit": "abc1234", "project_id": "phase2",
            "fixture": "f.json", "user_groups": ["admin"], "mode": "both", "total_cases": 1,
        },
        "config": {"default_threshold": 0.3},
        "retrieval": {
            "indexed_cases": 1, "recall_at_30": 1.0, "recall_at_30_hits": 1,
            "recall_at_8": 1.0, "recall_at_8_hits": 1, "layer_cases": 1,
            "layer_hits": 1, "layer_accuracy": 1.0,
        },
        "ladder": {
            "by_category": {"entity_lookup": {
                "passed": 1, "total": 1, "must_pass_passed": 1,
                "must_pass_total": 1, "avg_ms": 1200.0,
            }},
            "cases": [{
                "id": "e1", "category": "entity_lookup", "passed": True,
                "primary_source_layer": "support_maps", "fallback_used": True,
                "doc_gap": True, "elapsed_ms": 1200.0, "reasons": [],
                "source_display_names": ["Support Map: config_runtime_map (Project: phase2)"],
            }],
            "must_pass": {"passed": 1, "failed": 0, "total": 1},
            "unsupported": {"total": 0, "clean": 0, "leaked": 0},
            "latency": {"rung1_avg_ms": 0, "rung2_avg_ms": 1200.0, "refuse_avg_ms": 0, "max_ms": 1200.0},
        },
        "slice5": {"alias_rows": 567, "alias_match_ms_avg": 12.3},
        "verdicts": ["PASS: recall@8 100.0% (gate 90%)"],
        "notes": [],
    }
    report = ev.render_report(summary)
    assert "Support Map: config_runtime_map" in report
    assert "_internal/" not in report, "raw internal paths must never appear in the report"
    assert "recall@8" in report
