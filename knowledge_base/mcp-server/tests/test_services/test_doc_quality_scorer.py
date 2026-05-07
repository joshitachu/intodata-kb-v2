from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.services.doc_quality_scorer import DocScore, score_doc


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
    plain = _write_doc(tmp_path, "04_BUSINESS_LOGIC.md", _BASE_CONTENT)
    with_code = _write_doc(tmp_path, "04_BUSINESS_LOGIC_code.md", _BASE_CONTENT + "\n```python\nprint('hi')\n```\n```sql\nSELECT 1\n```\n```bash\necho ok\n```\n")

    score_plain = score_doc(plain).dimensions["specificity"]
    score_code = score_doc(with_code).dimensions["specificity"]

    assert score_code > score_plain


def test_error_handling_signal_adds_bonus(tmp_path):
    plain = _write_doc(tmp_path, "05_OPERATIONS_RISK.md", _BASE_CONTENT)
    with_errors = _write_doc(
        tmp_path,
        "05_OPERATIONS_RISK_err.md",
        _BASE_CONTENT + "\nError handling via retry logic. On exception, fallback to cache. Edge case: empty payload triggers on_fail handler. Exception raised on timeout.\n",
    )

    score_plain = score_doc(plain).dimensions["specificity"]
    score_errors = score_doc(with_errors).dimensions["specificity"]

    assert score_errors > score_plain


def test_no_code_examples_flag(tmp_path):
    doc = _write_doc(tmp_path, "04_BUSINESS_LOGIC.md", _BASE_CONTENT)
    result = score_doc(doc)
    assert "no_code_examples" in result.flags


def test_score_doc_returns_correct_dimensions(tmp_path):
    doc = _write_doc(tmp_path, "04_BUSINESS_LOGIC.md", _BASE_CONTENT)
    result = score_doc(doc)

    assert isinstance(result, DocScore)
    assert set(result.dimensions.keys()) == {"line_ratio", "section_coverage", "specificity", "risk_depth"}
    assert 0.0 <= result.score <= 5.0
