"""Heuristic doc quality scorer — no LLM, runs in <1 second.

Scores each generated Markdown doc 0–5 based on four weighted dimensions:
  line_ratio (30%)      — actual lines vs minimum required
  section_coverage (25%) — expected section headers present
  specificity (25%)     — job refs, BR/AP/RF codes, table data
  risk_depth (20%)      — CRITICAL/HIGH mentions, numbered risk items
"""

from __future__ import annotations

import datetime
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcp_server.config import config


# Baseline minimum line counts calibrated at 50 units (jobs/recipes).
# Scaled at score time by unit_count — see _scaled_min_lines().
_BASE_MIN_LINES_AT_50: dict[str, int] = {
    "00_EXECUTIVE_SUMMARY": 600,
    "01_PROJECT_OVERVIEW": 800,
    "02_ARCHITECTURE_INVENTORY": 1400,
    "03_DATA_FLOWS_LINEAGE": 1800,
    "04_BUSINESS_LOGIC": 1600,
    "05_OPERATIONS_RISK": 1600,
    "06_ENVIRONMENT_EXTERNAL": 1000,
    "07_SECURITY_PERFORMANCE": 1000,
    "08_PATTERNS_STANDARDS": 1000,
    "09_RUNBOOK": 1000,
    "10_ONBOARDING_FAQ": 1000,
    "11_MIGRATION_ASSESSMENT": 1200,
    # Split ops sub-docs (newer pipeline format)
    "05a_OPERATIONS": 400,
    "05b_SECURITY": 300,
    "05c_RISK": 300,
    "05d_CHANGE_MANAGEMENT": 200,
}
_SCALE_REFERENCE_UNITS = 50


def _scaled_min_lines(doc_key: str, unit_count: int) -> int:
    """Return minimum line count scaled to actual project size.

    Projects with few units (e.g. 17 Workato recipes) should not be penalised
    by thresholds calibrated for large Talend codebases.
    Scale factor is sqrt(unit_count / 50) to avoid over-penalising tiny projects
    while still rewarding depth on large ones.  Clamped to [0.3, 2.0]× baseline.
    """
    import math
    base = _BASE_MIN_LINES_AT_50.get(doc_key, 500)
    scale = math.sqrt(max(1, unit_count) / _SCALE_REFERENCE_UNITS)
    scale = max(0.3, min(2.0, scale))
    return max(50, int(base * scale))

# Expected H2 section header keywords per doc (regex alternatives)
_EXPECTED_SECTIONS: dict[str, list[str]] = {
    "00_EXECUTIVE_SUMMARY": [
        r"Project Overview",
        r"Overall Assessment|Score Summary",
        r"Dimension Score|Scoring",
        r"Critical Finding|Top \d+ Critical",
        r"Strength|Top \d+ Strength",
        r"Recommended|Action",
        r"FAQ|Question",
        r"Glossary|Term",
    ],
    "01_PROJECT_OVERVIEW": [
        r"Platform|Technology",
        r"Architecture|Layer|Structure",
        r"Group|Scope|Region",
        r"Dependency|Integration|External",
    ],
    "02_ARCHITECTURE_INVENTORY": [
        r"Inventory|Architecture|Component",
        r"Layer|Tier|Pipeline",
        r"Job|Pattern|Design",
        r"Flow|Orchestration|Dependency",
        r"Connection|Inventory",
        r"Trigger|Route|Topology",
    ],
    "03_DATA_FLOWS_LINEAGE": [
        r"Data Flow|Flow Pattern|Lineage",
        r"Source|Input|Origin",
        r"Transform|tMap|Expression|Mapping",
        r"Schema|Table|Column|Field",
        r"Data Quality|Validation|Defect",
    ],
    "04_BUSINESS_LOGIC": [
        r"Business Rule|Rule Catalog|Rules",
        r"Domain|Category|Entity|Concept",
        r"Logic|Expression|Filter|Condition",
        r"Conflict|Exception|Edge Case|Anomaly",
    ],
    "05_OPERATIONS_RISK": [
        r"Risk|Finding|Issue|Anti-Pattern",
        r"Operation|Monitoring|Schedule",
        r"Alert|SLA|Recovery|Failover",
        r"Incident|Failure|Error",
    ],
    "06_ENVIRONMENT_EXTERNAL": [
        r"Environment|Configuration|Setting",
        r"Connection|Credential|Secret|Auth",
        r"External|Integration|API|System",
        r"Context|Variable|Parameter",
        r"Deployment|Portability",
        r"Hardcoded|Rotation|Credential Risk",
    ],
    "07_SECURITY_PERFORMANCE": [
        r"Security|Access|Credential|Auth",
        r"Performance|Throughput|Latency|Volume",
    ],
    "08_PATTERNS_STANDARDS": [
        r"Pattern|Anti-Pattern|Practice",
        r"Standard|Convention|Naming|Style",
    ],
    "09_RUNBOOK": [
        r"Step|Procedure|How to|Process",
        r"Monitoring|Alert|Health Check",
        r"Incident|Recovery|Troubleshoot",
    ],
    "10_ONBOARDING_FAQ": [
        r"Getting Started|Introduction|Overview",
        r"FAQ|Question|How|Common",
        r"Glossary|Term|Definition|Concept",
    ],
    "11_MIGRATION_ASSESSMENT": [
        r"Migration|Readiness|Assessment",
        r"Risk|Blocker|Concern|Challenge",
        r"Recommendation|Strategy|Plan|Approach",
        r"Scenario|Option|Approach Comparison",
        r"Effort|Estimate|Timeline",
    ],
    "05a_OPERATIONS": [r"Schedule|Job|Trigger|Monitoring"],
    "05b_SECURITY": [r"Security|Access|Credential|Auth"],
    "05c_RISK": [r"Risk|Issue|Finding|Priority"],
    "05d_CHANGE_MANAGEMENT": [r"Change|Release|Deploy|Migration"],
}

_HEADER_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_RISK_RE = re.compile(r"\b(CRITICAL|HIGH|MEDIUM|LOW)\b")
_BR_RE = re.compile(r"\bBR-[A-Z]+-\d+\b")
_AP_RE = re.compile(r"\bAP-[A-Z]+-\d+\b")
_RF_RE = re.compile(r"\bRF-[A-Z]+-\d+\b")
# Job/recipe name heuristics:
#   Talend: all-caps prefix + underscore + mixed identifier (DEU_LoadCustomers)
#   Workato/generic: backtick- or quote-wrapped identifiers, or Title Case multi-word names
_JOB_RE = re.compile(
    r"\b[A-Z]{2,5}_[A-Za-z][A-Za-z0-9_]{3,}\b"       # Talend: DEU_LoadCustomers
    r"|`[A-Za-z][A-Za-z0-9 _\-]{3,}`"                  # backtick: `My Recipe Name`
    r"|\*\*[A-Z][A-Za-z0-9 ]{3,}\*\*"                  # bold: **Real-time Shipment Tracking**
)
_TABLE_ROW_RE = re.compile(r"^\|[^|]+\|[^|]+\|", re.MULTILINE)
_SEPARATOR_ROW_RE = re.compile(r"^\|[-:| ]+\|", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_ERROR_PATTERN_RE = re.compile(
    r"\b(error.handl|exception|retry|fallback|edge.case|on.fail)\b",
    re.IGNORECASE,
)


@dataclass
class DocScore:
    file: str
    lines: int
    score: float          # 0.0–5.0, one decimal
    flags: list[str] = field(default_factory=list)
    dimensions: dict[str, float] = field(default_factory=dict)


@dataclass
class RunQualityReport:
    overall: float
    docs: list[DocScore]
    graded_at: str

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "graded_at": self.graded_at,
            "docs": [asdict(d) for d in self.docs],
        }


def _doc_key(filename: str) -> str:
    """Strip .md extension to get the doc key for lookups."""
    stem = Path(filename).stem
    # Strip leading number prefix if present: "00_EXECUTIVE_SUMMARY" → "00_EXECUTIVE_SUMMARY"
    return stem


def _score_line_ratio(lines: int, min_lines: int) -> float:
    """0–5 based on how close to minimum line count. Full credit at 100%."""
    if min_lines <= 0:
        return 5.0
    ratio = lines / min_lines
    # Gentle curve: 50% of minimum → 2.5, 100% → 5.0, capped at 5.0
    return round(min(5.0, ratio * 5.0), 2)


def _score_section_coverage(content: str, doc_key: str) -> tuple[float, list[str]]:
    """0–5 based on expected section headers present."""
    expected = _EXPECTED_SECTIONS.get(doc_key, [])
    if not expected:
        return 5.0, []

    headers = _HEADER_RE.findall(content)
    headers_text = " | ".join(headers)

    matched = sum(
        1 for pattern in expected
        if re.search(pattern, headers_text, re.IGNORECASE)
    )
    ratio = matched / len(expected)
    score = round(ratio * 5.0, 2)
    flags = [] if ratio >= 0.7 else ["missing_sections"]
    return score, flags


def _score_specificity(content: str) -> tuple[float, list[str]]:
    """0–5 based on presence of specific data signals."""
    flags: list[str] = []

    job_refs = len(_JOB_RE.findall(content))
    br_codes = len(_BR_RE.findall(content))
    ap_codes = len(_AP_RE.findall(content))
    rf_codes = len(_RF_RE.findall(content))

    # Count real table data rows (not separator rows)
    all_table_rows = len(_TABLE_ROW_RE.findall(content))
    separator_rows = len(_SEPARATOR_ROW_RE.findall(content))
    data_table_rows = max(0, all_table_rows - separator_rows)

    score = 0.0

    # Job references: 0–2 points
    if job_refs >= 20:
        score += 2.0
    elif job_refs >= 8:
        score += 1.5
    elif job_refs >= 3:
        score += 1.0
    elif job_refs >= 1:
        score += 0.5
    else:
        flags.append("no_job_refs")

    # Coded IDs (BR/AP/RF): 0–2 points
    code_count = br_codes + ap_codes + rf_codes
    if code_count >= 10:
        score += 2.0
    elif code_count >= 4:
        score += 1.5
    elif code_count >= 1:
        score += 1.0
    else:
        flags.append("no_coded_ids")

    # Table data: 0–1 point
    if data_table_rows >= 10:
        score += 1.0
    elif data_table_rows >= 4:
        score += 0.5
    else:
        flags.append("sparse_tables")

    # Code examples: 0–0.5 bonus (cap stays at 5.0)
    code_blocks = len(_CODE_FENCE_RE.findall(content))
    if code_blocks >= 3:
        score += 0.5
    elif code_blocks >= 1:
        score += 0.25
    else:
        flags.append("no_code_examples")

    # Edge-case / error handling: 0–0.5 bonus
    error_hits = len(_ERROR_PATTERN_RE.findall(content))
    if error_hits >= 5:
        score += 0.5
    elif error_hits >= 2:
        score += 0.25
    else:
        flags.append("no_error_handling")

    return round(min(5.0, score), 2), flags


def _score_risk_depth(content: str) -> tuple[float, list[str]]:
    """0–5 based on risk labels and structured risk items."""
    flags: list[str] = []

    risk_mentions = len(_RISK_RE.findall(content))
    ap_codes = len(_AP_RE.findall(content))
    rf_codes = len(_RF_RE.findall(content))

    score = 0.0

    # Risk label density
    if risk_mentions >= 20:
        score += 2.5
    elif risk_mentions >= 8:
        score += 2.0
    elif risk_mentions >= 3:
        score += 1.0
    elif risk_mentions >= 1:
        score += 0.5
    else:
        flags.append("no_risk_labels")

    # Anti-pattern / red flag codes
    code_count = ap_codes + rf_codes
    if code_count >= 6:
        score += 2.5
    elif code_count >= 3:
        score += 1.5
    elif code_count >= 1:
        score += 0.5
    else:
        flags.append("no_risk_codes")

    return round(min(5.0, score), 2), flags


def score_doc(path: Path, unit_count: int = _SCALE_REFERENCE_UNITS) -> DocScore:
    """Score a single Markdown file. unit_count calibrates minimum line thresholds."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DocScore(file=path.name, lines=0, score=0.0, flags=["unreadable"])

    lines = content.count("\n")
    doc_key = _doc_key(path.name)
    min_lines = _scaled_min_lines(doc_key, unit_count)

    line_score = _score_line_ratio(lines, min_lines)
    section_score, section_flags = _score_section_coverage(content, doc_key)
    specificity_score, spec_flags = _score_specificity(content)
    risk_score, risk_flags = _score_risk_depth(content)

    # Weighted composite
    composite = (
        0.30 * line_score
        + 0.25 * section_score
        + 0.25 * specificity_score
        + 0.20 * risk_score
    )

    flags: list[str] = []
    if lines < min_lines:
        flags.append("below_min_lines")
    flags.extend(section_flags)
    flags.extend(spec_flags)
    flags.extend(risk_flags)

    return DocScore(
        file=path.name,
        lines=lines,
        score=round(composite, 2),
        flags=flags,
        dimensions={
            "line_ratio": line_score,
            "section_coverage": section_score,
            "specificity": specificity_score,
            "risk_depth": risk_score,
        },
    )


class DocQualityScorer:
    """Score all docs in a project's docs/ directory."""

    def score(
        self,
        project_id: str,
        docs_dir: Path | None = None,
    ) -> RunQualityReport:
        if docs_dir is None:
            docs_dir = config.project_root / "output" / project_id / "docs"

        doc_files = sorted(
            p for p in docs_dir.glob("*.md")
            if not p.name.startswith("_")
        )

        if not doc_files:
            return RunQualityReport(
                overall=0.0,
                docs=[],
                graded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )

        # Read unit_count from facts_manifest.json if available for calibrated scoring
        unit_count = _SCALE_REFERENCE_UNITS
        facts_path = docs_dir / "facts_manifest.json"
        if facts_path.exists():
            try:
                import json as _json
                facts = _json.loads(facts_path.read_text(encoding="utf-8"))
                unit_count = int(facts.get("unit_count", _SCALE_REFERENCE_UNITS))
            except Exception:
                pass

        doc_scores = [score_doc(f, unit_count=unit_count) for f in doc_files]
        overall = round(sum(d.score for d in doc_scores) / len(doc_scores), 2)

        return RunQualityReport(
            overall=overall,
            docs=doc_scores,
            graded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
