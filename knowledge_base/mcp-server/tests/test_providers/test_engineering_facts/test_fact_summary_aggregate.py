"""engineering_facts.md is a bounded, deterministic aggregate (WI-1).

The summary is attached to every Phase 2 and Phase 3 prompt, so it is the one
fact artifact guaranteed to reach the model. Three properties are load-bearing
and each has a test here:

* it stays under ``config.phase3_shared_context_file_bytes`` (one byte over
  and ``plan_phase3_slices`` stops replicating it into every slice),
* it is byte-identical for equal facts supplied in a different order
  (provider prefix caching), and
* it carries the complete ``calls_job`` edge list, which is the one relation a
  reader cannot reconstruct from counts.
"""

from __future__ import annotations

import random

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts
from mcp_server.providers.custom.engineering_facts.writers.markdown_writer import (
    SUMMARY_MAX_BYTES,
    MarkdownFactWriter,
    _summary,
)

_BULK_TYPES = (
    "uses_connection",
    "uses_routine",
    "uses_context_group",
    "tMap_input",
    "tMap_output",
    "contains",
    "repository_connection_ref",
)

CALLS_JOB_EDGES = 123
EXTERNAL_ROWS = 900
BULK_ROWS = 19_500


def _fixture_operations() -> list[tuple[str, dict]]:
    """A ~20k-dependency shape modelled on a production Talend run."""
    ops: list[tuple[str, dict]] = []
    for group in range(20):
        for job in range(25):
            unit = f"Job_{group:02d}_{job:03d}"
            ops.append(
                (
                    "architecture",
                    {
                        "name": unit,
                        "layer": f"Group{group:02d}",
                        "component": unit,
                        "boundary": f"Group{group:02d}",
                    },
                )
            )
    for i in range(CALLS_JOB_EDGES):
        ops.append(
            (
                "dependency",
                {
                    "source": f"Job_{i % 20:02d}_{i % 25:03d}",
                    "target": f"Job_{(i + 3) % 20:02d}_{(i + 7) % 25:03d}_child{i}",
                    "dependency_type": "calls_job",
                },
            )
        )
    for i in range(EXTERNAL_ROWS):
        ops.append(
            (
                "dependency",
                {
                    "source": f"Job_{i % 20:02d}_{i % 25:03d}",
                    "target": f"System_{i % 41}",
                    "dependency_type": "external_system",
                },
            )
        )
    for i in range(BULK_ROWS):
        dependency_type = _BULK_TYPES[i % len(_BULK_TYPES)]
        ops.append(
            (
                "dependency",
                {
                    "source": f"Job_{i % 20:02d}_{(i // 7) % 25:03d}",
                    "target": f"{dependency_type}_target_{i}",
                    "dependency_type": dependency_type,
                },
            )
        )
    for i in range(300):
        ops.append(
            (
                "known_risk",
                {
                    "name": f"Risk {i} with a reasonably long descriptive sentence",
                    "severity": ("CRITICAL", "HIGH", "MEDIUM", "LOW")[i % 4],
                    "affected_entity": f"Job_{i % 20:02d}_{i % 25:03d}",
                    "risk_type": "missing_error_handling",
                    "remediation": "Add an explicit error path.",
                },
            )
        )
    for i in range(500):
        ops.append(
            (
                "config_runtime",
                {
                    "name": f"conn{i}.host",
                    "config_type": "connection",
                    "key": "host",
                    "value_hint": f"host{i}.example.internal",
                },
            )
        )
    return ops


def _build(seed: int | None = None) -> EngineeringFacts:
    operations = _fixture_operations()
    if seed is not None:
        random.Random(seed).shuffle(operations)
    builder = EngineeringFactsBuilder("proj", "talend")
    adders = {
        "architecture": builder.add_architecture,
        "dependency": builder.add_dependency,
        "known_risk": builder.add_known_risk,
        "config_runtime": builder.add_config_runtime,
    }
    for kind, kwargs in operations:
        adders[kind](**kwargs)
    return builder.build()


@pytest.fixture(scope="module")
def large_facts() -> EngineeringFacts:
    return _build()


def test_cap_is_below_the_phase3_shared_context_threshold():
    # One byte over reclassifies the file from SHARED to splittable EVIDENCE,
    # so slice 1 and slice 3 of one document would see different facts.
    assert SUMMARY_MAX_BYTES < config.phase3_shared_context_file_bytes


def test_summary_fits_the_cap_on_a_twenty_thousand_dependency_project(large_facts):
    assert len(large_facts.dependencies) > 20_000
    text = _summary(large_facts)
    assert len(text.encode("utf-8")) <= SUMMARY_MAX_BYTES
    assert len(text.encode("utf-8")) < config.phase3_shared_context_file_bytes


def test_summary_is_byte_identical_when_the_input_order_is_shuffled():
    ordered = _summary(_build())
    shuffled = _summary(_build(seed=20260828))
    assert ordered == shuffled


def test_every_calls_job_edge_is_present_verbatim(large_facts):
    text = _summary(large_facts)
    expected = {
        (dep.source, dep.target)
        for dep in large_facts.dependencies
        if dep.dependency_type == "calls_job"
    }
    assert len(expected) == CALLS_JOB_EDGES
    for source, target in expected:
        assert f"|{source}|{target}|" in text
    assert "Complete edge list, every row verbatim." in text


def test_scale_and_counts_are_derived_not_hardcoded(large_facts):
    text = _summary(large_facts)
    assert f"Dependency rows: {len(large_facts.dependencies)}" in text
    assert "Jobs/units: 500 | Groups: 20" in text
    sources = {d.source for d in large_facts.dependencies}
    targets = {d.target for d in large_facts.dependencies}
    assert f"Distinct dependency sources: {len(sources)}" in text
    assert f"Distinct dependency targets: {len(targets)}" in text
    assert f"## Job Call Graph (`calls_job`) -- {CALLS_JOB_EDGES} of " in text


def test_external_systems_are_grouped_by_target_with_usage_counts(large_facts):
    text = _summary(large_facts)
    assert "## External Systems (`external_system`)" in text
    assert "|external system|usages|distinct users|example users|" in text
    # 900 rows spread over 41 targets; the busiest target leads the table.
    assert "41 systems over 900 rows" in text


def test_per_type_row_counts_cover_every_aggregated_type(large_facts):
    text = _summary(large_facts)
    assert "## Dependency Rows By Type" in text
    for dependency_type in _BULK_TYPES:
        assert f"|{dependency_type}|" in text


def test_degree_and_risk_sections_are_present(large_facts):
    text = _summary(large_facts)
    assert "### Out-degree (depends on)" in text
    assert "### In-degree (depended on by)" in text
    assert "## Known Risks -- 300 total" in text
    # Severity census orders CRITICAL first, never alphabetically.
    census = text.split("## Known Risks")[1]
    assert census.index("|CRITICAL|") < census.index("|HIGH|") < census.index("|LOW|")


def test_pointers_name_the_evidence_of_record_literally(large_facts):
    text = _summary(large_facts)
    assert "`dependency_map.md`" in text
    assert "`known_risks.md`" in text
    assert "`config_runtime_map.md`" in text
    assert "## Evidence Of Record" in text


def test_empty_facts_still_emit_scale_and_pointers(tmp_path):
    facts = EngineeringFacts(project_id="empty", platform="generic")
    written = MarkdownFactWriter().write(facts, tmp_path)
    summary = next(path for path in written if path.name == "engineering_facts.md")
    text = summary.read_text(encoding="utf-8")

    assert "## Scale" in text
    assert "Jobs/units: 0 | Groups: 0" in text
    assert "## Evidence Of Record" in text
    assert "`dependency_map.md`" in text
    assert "`known_risks.md`" in text
    assert "`config_runtime_map.md`" in text
    # Absence is stated, never implied by an empty table.
    assert "No `calls_job` dependency rows were extracted." in text
    assert len(text.encode("utf-8")) <= SUMMARY_MAX_BYTES


def test_an_oversized_calls_job_list_truncates_deterministically_and_says_so():
    builder = EngineeringFactsBuilder("p", "talend")
    for i in range(6_000):
        builder.add_dependency(
            source=f"Job_Alpha_With_A_Long_Name_{i:05d}",
            target=f"Job_Beta_With_A_Long_Name_{i:05d}",
            dependency_type="calls_job",
        )
    facts = builder.build()

    text = _summary(facts)
    assert len(text.encode("utf-8")) <= SUMMARY_MAX_BYTES
    assert "TRUNCATED at the" in text
    assert "in `dependency_map.md` under `## calls_job`." in text
    # Same facts, same bytes -- truncation must not be size-race dependent.
    assert _summary(facts) == text


def test_summary_is_built_from_the_model_not_from_the_emitted_maps(tmp_path, monkeypatch):
    """The aggregate must not re-parse dependency_map.md (1.75 MB in prod)."""
    builder = EngineeringFactsBuilder("p", "talend")
    builder.add_dependency(source="A", target="B", dependency_type="calls_job")
    facts = builder.build()

    real_read_text = type(tmp_path).read_text

    def _fail_on_md_read(self, *args, **kwargs):  # pragma: no cover - guard
        if self.suffix == ".md":
            raise AssertionError(f"summary re-read an emitted artifact: {self}")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "read_text", _fail_on_md_read)
    MarkdownFactWriter().write(facts, tmp_path)
