from __future__ import annotations

from pathlib import Path

from mcp_server.services.doc_quality_scorer import DocQualityScorer, DocScore, score_doc


def _write_doc(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


_BASE_CONTENT = """\
## Inventory and purpose
DEU_LoadCustomers BR-DEU-001 AP-DEU-001 RF-DEU-001 CRITICAL HIGH MEDIUM LOW CRITICAL HIGH MEDIUM
DEU_LoadCustomers BR-DEU-002 AP-DEU-002 RF-DEU-002 CRITICAL HIGH MEDIUM LOW CRITICAL HIGH MEDIUM

| Column | Type | Description |
|--------|------|-------------|
| id     | int  | Primary key |
| name   | str  | Customer name |
| status | str  | Active flag |
| region | str  | Region code |
| score  | float | Risk score |
| tier   | str  | Customer tier |
| code   | str  | Internal code |
| ref    | str  | Ref number |
| tag    | str  | Tag value |
| flag   | bool | Active flag |
| ts     | dt   | Timestamp |
"""


def test_code_example_signal_adds_bonus(tmp_path):
    plain = _write_doc(tmp_path, "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md", _BASE_CONTENT)
    with_code = _write_doc(tmp_path, "04_BUSINESS_RULES_AND_TRANSFORMATIONS_code.md", _BASE_CONTENT + "\n```python\nprint('hi')\n```\n```sql\nSELECT 1\n```\n```bash\necho ok\n```\n")

    score_plain = score_doc(plain).dimensions["specificity"]
    score_code = score_doc(with_code).dimensions["specificity"]

    assert score_code > score_plain


def test_error_handling_signal_adds_bonus(tmp_path):
    plain = _write_doc(tmp_path, "06_OPERATIONS_TESTING_AND_DEBUGGING.md", _BASE_CONTENT)
    with_errors = _write_doc(
        tmp_path,
        "06_OPERATIONS_TESTING_AND_DEBUGGING_err.md",
        _BASE_CONTENT + "\nError handling via retry logic. On exception, fallback to cache. Edge case: empty payload triggers on_fail handler. Exception raised on timeout.\n",
    )

    score_plain = score_doc(plain).dimensions["specificity"]
    score_errors = score_doc(with_errors).dimensions["specificity"]

    assert score_errors > score_plain


def test_no_code_examples_flag(tmp_path):
    doc = _write_doc(tmp_path, "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md", _BASE_CONTENT)
    result = score_doc(doc)
    assert "no_code_examples" in result.flags


def test_score_doc_returns_correct_dimensions(tmp_path):
    doc = _write_doc(tmp_path, "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md", _BASE_CONTENT)
    result = score_doc(doc)

    assert isinstance(result, DocScore)
    assert set(result.dimensions.keys()) == {
        "line_ratio",
        "section_coverage",
        "specificity",
        "risk_depth",
        "assistant_usefulness",
    }
    assert 0.0 <= result.score <= 5.0


def test_run_score_ignores_support_markdown_files(tmp_path):
    _write_doc(tmp_path, "00_AGENT_BRIEF.md", _BASE_CONTENT)
    _write_doc(tmp_path, "engineering_facts.md", "short support file\n")

    result = DocQualityScorer().score("example", docs_dir=tmp_path)

    assert [doc.file for doc in result.docs] == ["00_AGENT_BRIEF.md"]


# -- what counts as a document -------------------------------------------


def test_a_lowercase_document_name_is_scored_not_dropped(tmp_path):
    """The defect that made run 250 score 0.00/5.0 with eight documents published.

    ``_SUPPORT_ARTIFACT_KEY_RE = ^[a-z][a-z0-9_]*$`` classified every lowercase
    snake_case file as an engineering-fact artifact. Every
    ``agent-workspace-v1`` document is lowercase snake_case, because the agent
    chooses its own file names, so the whole publication was dropped before a
    single dimension was computed. The same predicate gates the dashboard
    document list and the PDF export, so those dropped them too.
    """

    _write_doc(tmp_path, "order_to_cash.md", _BASE_CONTENT)
    _write_doc(tmp_path, "engineering_facts.md", "short support file\n")

    result = DocQualityScorer().score("example", docs_dir=tmp_path)

    assert [doc.file for doc in result.docs] == ["order_to_cash.md"]
    assert result.overall > 0.0


def test_nested_documents_are_scored(tmp_path):
    """``glob("*.md")`` saw only the docs root.

    An ``agent-workspace-v1`` publication is a tree: ``integrations/*.md`` and
    ``reference/*.md`` are documents, and none of them could ever be scored.
    """

    (tmp_path / "integrations").mkdir()
    _write_doc(tmp_path, "00_AGENT_BRIEF.md", _BASE_CONTENT)
    _write_doc(tmp_path, "integrations/exchanges.md", _BASE_CONTENT)

    result = DocQualityScorer().score("example", docs_dir=tmp_path)

    # Reported by docs-relative path: two sub-projects can both hold
    # ``orders.md`` and a bare name would collide in the report.
    assert [doc.file for doc in result.docs] == [
        "00_AGENT_BRIEF.md",
        "integrations/exchanges.md",
    ]


def test_internal_directories_are_still_excluded_when_the_walk_is_recursive(tmp_path):
    """Widening the walk must not admit an internal artifact."""

    for internal in ("_assessment", "_rag_support", "_audit", "_topology", ".git"):
        (tmp_path / internal).mkdir()
        _write_doc(tmp_path, f"{internal}/note.md", _BASE_CONTENT)
    _write_doc(tmp_path, "00_AGENT_BRIEF.md", _BASE_CONTENT)

    result = DocQualityScorer().score("example", docs_dir=tmp_path)

    assert [doc.file for doc in result.docs] == ["00_AGENT_BRIEF.md"]


def test_the_support_artifact_set_comes_from_the_writer_that_produces_it(tmp_path):
    """A fact map added to the writer's registry is excluded without an edit here.

    The old rule was a name shape. The property that makes these files internal
    is that the deterministic markdown fact writer produced them, so the set is
    read from that writer's own registry.
    """

    from mcp_server.providers.custom.engineering_facts.writers.markdown_writer import (
        MARKDOWN_ARTIFACTS,
    )
    from mcp_server.services.doc_quality_scorer import (
        is_product_doc_filename,
        is_published_doc_filename,
        is_support_artifact_filename,
    )

    for artifact in MARKDOWN_ARTIFACTS:
        assert is_support_artifact_filename(f"{artifact}.md"), artifact
        assert not is_published_doc_filename(f"{artifact}.md"), artifact
    for name in ("engineering_facts.md", "sql_usage.md", "schema_dictionary.md"):
        assert is_support_artifact_filename(name), name

    # ... and only at the docs ROOT. A nested lowercase document is a document.
    assert is_published_doc_filename("integrations/dependency_map.md")
    assert is_product_doc_filename("integrations/dependency_map.md")


def test_the_internal_segment_rule_is_the_same_one_publication_uses():
    from mcp_server.services.doc_quality_scorer import is_published_doc_path

    assert is_published_doc_path("overview.md")
    assert is_published_doc_path("integrations/orders.md")
    assert not is_published_doc_path("_assessment/scorecard.md")
    assert not is_published_doc_path("integrations/_draft/orders.md")
    assert not is_published_doc_path(".hidden/orders.md")
    assert not is_published_doc_path("notes.txt")
