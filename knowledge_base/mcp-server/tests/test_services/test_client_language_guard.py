"""Tests for the client-language guard and the provenance sidecar validator.

Both directions are tested, because a guard that cries wolf gets switched off:

* it catches OUR machinery -- staging paths, internal filenames, internal ids,
  self-referential analysis phrasing, developer paths;
* it does NOT catch a customer artifact (``Web.config``, ``routines.json``, a
  ``.item`` design), ordinary integration vocabulary ("the pipeline", "based on
  the process"), business phase language, or the two evidence-limitation
  phrasings this lane REQUIRES ("Scanner-Limited Evidence", "Evidence
  Register").

The calibration tests at the end run the guard over two real corpora that ship
in this repository: the nine reference documents (the exemplar of correct
client prose -- must be clean) and a real published document set (which leaks).
"""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from mcp_server.services.client_language_guard import (
    PROVENANCE_DIRNAME,
    count_code_blocks,
    discover_client_docs,
    leakage_findings,
    read_provenance_sidecar,
    sidecar_path,
    sidecar_stays_internal,
    strip_internal_markers,
    validate_client_docs,
    validate_provenance,
    write_leakage_report,
    write_provenance_report,
)

_REPO = Path(__file__).resolve().parents[2]
REFERENCE_DOCS = _REPO.parent / "intodoc" / "reference-docs"
REAL_PUBLISHED_DOCS = (
    _REPO
    / "data"
    / "living_docs"
    / "candidates"
    / "poly"
    / "candidates"
    / "ldc-0352acaef625565ed31bf71221917af0"
    / "workspace"
    / "output"
    / "docs"
)


def _markers(text: str) -> set[tuple[str, str]]:
    return {(f.category, f.marker) for f in leakage_findings(text)}


def _categories(text: str) -> set[str]:
    return {f.category for f in leakage_findings(text)}


# --------------------------------------------------------------------------- #
# Positive direction: our machinery is caught
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "line",
    [
        "| Project metadata | _analysis/_doc_pack_00_AGENT_BRIEF.md | HIGH |",
        "**Source Coverage**: evidence=_preprocess/_source_coverage.json",
        "See `_assessment/scorecard.md` for the ratings.",
        "Derived from the bundle in docs_staging/_analysis/Flows.md.",
        "Evidence: _evidence_registry/doc_slices/03.md",
        "Written into _rag_support/phase2_analysis/Flows.md.",
        "The run wrote _agent_tasks/phase3/00_AGENT_BRIEF/output.md.",
        "Facts came from _facts/engineering_facts.md.",
        "See the analysis in _analysis/Flows.md.",
    ],
)
def test_internal_staging_paths_are_caught(line: str) -> None:
    assert leakage_findings(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Extracted from scan_result.json during the scan.",
        "The full list is in engineering_facts.md.",
        "Cross-references live in dependency_map.md.",
        "Ratings are in scorecard.md.",
        "Column names come from entity_index.csv.",
        "Open items were tracked in open-questions.md.",
        "The catalog is defined by doc_catalog.yaml.",
    ],
)
def test_internal_artifact_filenames_are_caught(line: str) -> None:
    assert "internal_file" in _categories(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Based on our internal analysis method, the job writes to staging.",
        "Based on our analysis, the flow is nightly.",
        "Based on the analysis, the flow is nightly.",
        "Per the analysis in the group bundle, three systems are involved.",
        "According to our analysis, the mapping drops nulls.",
        "The internal analysis shows two error paths.",
        "Our internal method treats routes as mediation.",
        "As documented in our analysis, the retry count is three.",
        "Derived from our preprocessing, the component count is 14.",
        "Each sub-agent covered one group.",
        "The LLM could not resolve the target table.",
        "The system prompt asked for a table of contents.",
        "The evidence bundle contained no SQL.",
        "The doc pack for this document was capped at 100 KB.",
        "### Risk Flags (from Phase 2 Analysis)",
        "Written by phase3_docs after the quality gate.",
        "This documentation pipeline produced nine documents.",
        "The harness retried the call twice.",
    ],
)
def test_self_referential_phrasing_is_caught(line: str) -> None:
    assert "self_reference" in _categories(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Evidence id ev-0123456789abcdef01234567 supports this.",
        "See claim-0123456789abcdef01234567.",
        "Traced to claim:flow-orders-nightly.",
        "Resolved via artifact:0123456789abcdef.",
        "Row source mapping_table:0123456789ab.",
        "<!-- internal: regenerate after Phase 2 -->",
        "Defined in Orders.item#//process/tMap_1.",
    ],
)
def test_internal_ids_are_caught(line: str) -> None:
    assert "internal_id" in _categories(line), line


@pytest.mark.parametrize(
    "line",
    [
        "Read from /Users/joshua/repos/nexus/output/proj/docs/03.md.",
        "Staged under /home/intodata/intodata-kb/knowledge_base/output.",
        "Temporary copy at /tmp/pytest-of-runner/x.md.",
        r"Exported to C:\Users\operator\AppData\Temp\scan.json",
        "Published to output/werkorder/docs/01_ARCHITECTURE_MAP.md.",
    ],
)
def test_developer_and_platform_paths_are_caught(line: str) -> None:
    assert "absolute_path" in _categories(line) or "internal_path" in _categories(line), line


def test_findings_carry_line_numbers_and_are_ordered() -> None:
    text = (
        "# Architecture\n"
        "Clean line.\n"
        "See _analysis/Flows.md for detail.\n"
        "Another clean line.\n"
        "Based on our analysis, three systems participate.\n"
    )
    found = leakage_findings(text, document="01_ARCHITECTURE_MAP.md")

    assert [f.line for f in found] == sorted(f.line for f in found)
    assert {f.line for f in found} == {3, 5}
    assert all(f.document == "01_ARCHITECTURE_MAP.md" for f in found)
    assert found[0].excerpt == "See _analysis/Flows.md for detail."


def test_a_multi_line_html_comment_is_reported_at_its_opening_line() -> None:
    text = "# Doc\n\nfine\n<!-- note\nspanning lines -->\nfine\n"
    found = [f for f in leakage_findings(text) if f.marker == "HTML comment"]

    assert len(found) == 1
    assert found[0].line == 4


def test_excerpts_are_ascii_and_bounded() -> None:
    text = "Caf\u00e9 note: see _analysis/Flows.md " + ("x" * 500) + "\n"
    found = leakage_findings(text)

    assert found
    for finding in found:
        assert finding.excerpt.isascii()
        assert len(finding.excerpt) <= 210


# --------------------------------------------------------------------------- #
# Negative direction: customer artifacts and ordinary prose are left alone
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "line",
    [
        "Connection strings are declared in Web.config.",
        "OrmLite registrations happen in Global.asax.cs.",
        "Shared helpers are exported in routines.json.",
        "The design is process/AssetCheck_GetLocation.item.",
        "Build settings live in pom.xml.",
        "Environment values come from PROD.properties.",
        "The component is tDBOutput_1 in AssetCheck_GetLocation.",
        "Rule BR-DEU-001 applies to Maximo2RHM_Workorders.",
        "Both VISE2AssetCheck and Vise2AssetCheck exist and are different.",
        "The engineering_facts table lists every call edge.",
        "The context group is named Default_Settings.",
    ],
)
def test_customer_artifacts_are_not_flagged(line: str) -> None:
    assert leakage_findings(line) == [], f"false positive: {line} -> {_markers(line)}"


@pytest.mark.parametrize(
    "line",
    [
        "The pipeline loads orders every night at 02:00.",
        "This ingestion pipeline is owned by the finance team.",
        "Based on the process defined in the service contract, retries are capped.",
        "Based on the extraction logic, empty rows are dropped.",
        "Based on the evidence in the contract, the field is mandatory.",
        "Phase 1 of the rollout covered the Dutch entities.",
        "Phase 2 documents were delivered in March.",
        "The Phase 1 pilot ran for six weeks.",
        "See the analysis in Section 4 of this document.",
        "The data model has three fact tables.",
        "The model is refreshed hourly.",
        "The team uses an audit trail for changes.",
        "Records are written to a staging table before the merge.",
        "The Talend runtime is installed under /opt/talend on the job server.",
        "Log files are written to /var/log/talend/.",
        "The share is mounted at /mnt/exchange/inbound.",
    ],
)
def test_ordinary_integration_prose_is_not_flagged(line: str) -> None:
    assert leakage_findings(line) == [], f"false positive: {line} -> {_markers(line)}"


@pytest.mark.parametrize(
    "line",
    [
        "## Scanner-Limited Evidence",
        "### Scanner-Limited Architecture Gaps",
        "Line numbers are scanner-limited in redacted artifacts.",
        "scanner detail is limited for generated code",
        "## Evidence Register",
        "| Claim | Source | Evidence Register confidence |",
    ],
)
def test_required_evidence_limitation_sections_are_not_flagged(line: str) -> None:
    """The two enterprise patterns this lane cannot inherit.

    ``Scanner-Limited Evidence`` is a REQUIRED section of
    02_COMPONENT_INVENTORY (``claude_service._write_doc_templates``) and
    ``Evidence Register`` is a section every real published document carries.
    Flagging either fails every document on a section the pipeline demands, and
    suppressing an honest evidence limitation pushes the model back toward
    confident guessing -- the defect this whole layer exists to fix.
    """
    assert leakage_findings(line) == [], f"false positive: {line} -> {_markers(line)}"


def test_an_underscore_inside_an_identifier_is_not_a_path_marker() -> None:
    text = (
        "The support artifact engineering_facts holds the edges, "
        "config_runtime_map holds the values, and column created_at_audit "
        "records the change. Component tMSSqlOutput_1 writes them.\n"
    )
    assert [f for f in leakage_findings(text) if f.category == "internal_path"] == []


# --------------------------------------------------------------------------- #
# strip_internal_markers
# --------------------------------------------------------------------------- #

def test_strip_internal_markers_removes_comments_and_table_ids_only() -> None:
    text = (
        "# Doc\n\n<!-- regenerate me -->\n"
        "Rows come from mapping_table:0123456789ab.\n"
        "Based on our analysis, this is nightly.\n"
    )
    stripped = strip_internal_markers(text)

    assert "<!--" not in stripped
    assert "mapping_table:" not in stripped
    assert "the exact mapping table below" in stripped
    # Prose is never rewritten: a leaking sentence needs a rewrite, not a sub.
    assert "Based on our analysis" in stripped


# --------------------------------------------------------------------------- #
# validate_client_docs / reporting
# --------------------------------------------------------------------------- #

def test_validate_scans_the_published_set_and_skips_internals(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text("Clean document.\n", encoding="utf-8")
    (tmp_path / "01_ARCHITECTURE_MAP.md").write_text(
        "Evidence: _analysis/Flows.md\n", encoding="utf-8"
    )
    (tmp_path / "_internal_notes.md").write_text(
        "Full of _analysis/ references.\n", encoding="utf-8"
    )
    (tmp_path / "engineering_facts.md").write_text(
        "_analysis/ everywhere\n", encoding="utf-8"
    )

    report = validate_client_docs(tmp_path)

    assert sorted(discover_client_docs(tmp_path)) == [
        "00_AGENT_BRIEF",
        "01_ARCHITECTURE_MAP",
    ]
    assert report.scanned == 2
    assert report.ok is False
    assert set(report.by_document) == {"01_ARCHITECTURE_MAP.md"}
    assert report.advisory is True


def test_a_clean_document_set_reports_ok(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text(
        "# Brief\n\nThe AssetCheck_GetLocation design writes to the asset table.\n",
        encoding="utf-8",
    )
    report = validate_client_docs(tmp_path)

    assert report.ok is True
    assert report.findings == []
    assert "clean" in report.summary_line()


def test_report_is_written_under_assessment_and_is_json(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text(
        "Evidence: _preprocess/Flows.md\n", encoding="utf-8"
    )
    report = validate_client_docs(tmp_path)
    path = write_leakage_report(tmp_path, report)

    assert path == tmp_path / "_assessment" / "client_language.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["advisory"] is True
    assert payload["findings"][0]["category"] == "internal_path"


def test_findings_are_capped_per_document(tmp_path: Path) -> None:
    from mcp_server.services.client_language_guard import MAX_FINDINGS_PER_DOC

    body = "\n".join(f"Line {i}: see _analysis/Flows.md" for i in range(400))
    (tmp_path / "00_AGENT_BRIEF.md").write_text(body, encoding="utf-8")

    report = validate_client_docs(tmp_path)

    assert report.by_document["00_AGENT_BRIEF.md"] == MAX_FINDINGS_PER_DOC
    assert report.truncated_documents == ["00_AGENT_BRIEF.md"]


def test_validate_never_raises_on_a_missing_document(tmp_path: Path) -> None:
    report = validate_client_docs(tmp_path, ["NOT_THERE", "ALSO_MISSING.md"])
    assert report.scanned == 0
    assert report.ok is True


# --------------------------------------------------------------------------- #
# Provenance sidecars
# --------------------------------------------------------------------------- #

def _sidecar(tmp_path: Path, doc: str, anchors: list[dict], **extra) -> None:
    payload = {"schema_version": 1, "document": f"{doc}.md", "anchors": anchors}
    payload.update(extra)
    path = sidecar_path(tmp_path, doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_sidecar_cannot_reach_the_client_by_construction() -> None:
    internal, reason = sidecar_stays_internal()

    assert internal is True
    assert PROVENANCE_DIRNAME in reason
    assert "underscore-prefixed" in reason


def test_sidecar_is_confirmed_internal_against_the_real_rag_push(tmp_path: Path) -> None:
    """Confirm rather than assume: run the actual push-side reader."""
    from mcp_server.services.rag_bridge import RagBridge

    (tmp_path / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    _sidecar(tmp_path, "00_AGENT_BRIEF", [{"kind": "quote", "group": "Flows"}])

    names = {doc["filename"] for doc in RagBridge()._read_md_files(str(tmp_path))}

    assert names == {"00_AGENT_BRIEF.md"}


def test_a_document_quoting_code_with_no_sidecar_is_reported(tmp_path: Path) -> None:
    (tmp_path / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md").write_text(
        "# Rules\n\n```java\nrow1.total = row1.qty * row1.price;\n```\n",
        encoding="utf-8",
    )

    report = validate_provenance(tmp_path)

    assert report.ok is False
    entry = report.documents[0]
    assert entry.code_blocks == 1
    assert entry.sidecar_present is False
    assert "no provenance sidecar" in entry.errors[0]


def test_a_document_quoting_code_with_no_quote_anchor_is_reported(tmp_path: Path) -> None:
    (tmp_path / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md").write_text(
        "# Rules\n\n```java\nrow1.total = row1.qty;\n```\n", encoding="utf-8"
    )
    _sidecar(
        tmp_path,
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        [{"kind": "protected_fact", "group": "Flows", "unit": "A_Main"}],
    )

    report = validate_provenance(
        tmp_path, known_groups=["Flows"], known_units=["A_Main"]
    )

    assert report.ok is False
    assert "no quote anchor" in report.documents[0].errors[0]


def test_partially_anchored_quotes_are_a_warning_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md").write_text(
        "```java\na();\n```\n\n```sql\nSELECT 1;\n```\n", encoding="utf-8"
    )
    _sidecar(
        tmp_path,
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        [{"kind": "quote", "group": "Flows", "unit": "A_Main", "excerpt": "a();"}],
    )

    report = validate_provenance(
        tmp_path, known_groups=["Flows"], known_units=["A_Main"]
    )

    entry = report.documents[0]
    assert entry.errors == []
    assert report.ok is True
    assert "unanchored" in entry.warnings[0]


def test_an_anchor_to_an_unknown_group_or_unit_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    _sidecar(
        tmp_path,
        "00_AGENT_BRIEF",
        [
            {"kind": "protected_fact", "group": "Ghost", "label": "g"},
            {"kind": "protected_fact", "group": "Flows", "unit": "Nope", "label": "u"},
            {"kind": "protected_fact", "label": "no-group"},
        ],
    )

    report = validate_provenance(
        tmp_path, known_groups=["Flows"], known_units=["A_Main"]
    )
    errors = report.documents[0].errors

    assert any("'Ghost' is not one of this run's groups" in e for e in errors)
    assert any("'Nope' is not one of this run's units" in e for e in errors)
    assert any("anchors to nothing" in e for e in errors)


def test_a_malformed_sidecar_is_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    path = sidecar_path(tmp_path, "00_AGENT_BRIEF")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    sidecar, errors = read_provenance_sidecar(tmp_path, "00_AGENT_BRIEF")
    assert sidecar is None
    assert "not readable JSON" in errors[0]

    report = validate_provenance(tmp_path)
    assert report.ok is False


def test_a_clean_document_with_a_matching_sidecar_passes(tmp_path: Path) -> None:
    (tmp_path / "10_UNIT_REFERENCE.md").write_text(
        "# Unit\n\n```java\nrow1.id = row2.id;\n```\n", encoding="utf-8"
    )
    _sidecar(
        tmp_path,
        "10_UNIT_REFERENCE",
        [
            {
                "kind": "quote",
                "group": "Flows",
                "unit": "A_Main",
                "source": "Flows",
                "excerpt": "row1.id = row2.id;",
            }
        ],
    )

    report = validate_provenance(
        tmp_path, known_groups=["Flows"], known_units=["A_Main"]
    )

    assert report.ok is True
    entry = report.documents[0]
    assert entry.quote_anchors == 1
    assert entry.code_blocks == 1
    assert entry.warnings == []


def test_provenance_report_is_written_under_assessment(tmp_path: Path) -> None:
    (tmp_path / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    report = validate_provenance(tmp_path)
    path = write_provenance_report(tmp_path, report)

    assert path == tmp_path / "_assessment" / "provenance_validation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sidecar_stays_internal"] is True
    assert payload["advisory"] is True


def test_code_block_counting_pairs_fences() -> None:
    assert count_code_blocks("```\na\n```\n") == 1
    assert count_code_blocks("```java\na\n```\n\n~~~\nb\n~~~\n") == 2
    assert count_code_blocks("no fences here") == 0
    assert count_code_blocks("`inline` only") == 0


# --------------------------------------------------------------------------- #
# Calibration against the real corpora in this repository
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not REFERENCE_DOCS.is_dir(), reason="reference docs not present")
def test_the_reference_document_set_is_clean() -> None:
    """The false-positive test that matters.

    These nine documents are the exemplar of correct client prose for this
    lane -- thousands of lines of real Talend documentation, shipped in the
    repository and bundled into every run as the format example. A single
    finding here means the guard would fire on prose the platform itself holds
    up as correct.
    """
    report = validate_client_docs(REFERENCE_DOCS)

    assert report.scanned == 9
    assert report.findings == [], [
        f"{f.document}:{f.line} [{f.category}/{f.marker}] {f.excerpt}"
        for f in report.findings[:10]
    ]


@pytest.mark.skipif(
    not REAL_PUBLISHED_DOCS.is_dir(), reason="real published doc set not present"
)
def test_a_real_published_document_set_leaks_and_is_caught() -> None:
    """The true-positive test, on documents a real run actually published.

    Verified leaks in that set: the Evidence Register cites
    ``_analysis/_doc_pack_*.md`` and ``_assessment/scorecard.md`` as sources,
    ``_preprocess/_source_coverage.json`` appears as an evidence path, six
    headings read "(from Phase 2 Analysis)", and the environment variable
    ``MCP_DOCGEN_LLM_SCORECARD_ENABLED=0`` was copied verbatim into two
    client documents.
    """
    report = validate_client_docs(REAL_PUBLISHED_DOCS)

    assert report.ok is False
    assert report.scanned >= 9
    markers = {(f.category, f.marker) for f in report.findings}
    assert ("internal_path", "_analysis") in markers
    assert ("internal_path", "_preprocess") in markers
    assert ("internal_path", "_doc_pack") in markers
    assert ("internal_file", "scorecard.md") in markers
    assert ("self_reference", "pipeline phase reference") in markers
    # And the headings this lane requires are NOT among the reasons.
    assert not any("scanner" in marker.lower() for _, marker in markers)
    assert not any("register" in marker.lower() for _, marker in markers)
