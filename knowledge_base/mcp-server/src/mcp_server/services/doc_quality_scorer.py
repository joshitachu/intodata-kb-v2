"""Heuristic doc quality scorer — no LLM, runs in <1 second.

Scores assistant-grade generated Markdown docs 0–5 based on:
  line_ratio (15%)       — actual lines vs minimum required
  section_coverage (20%) — expected assistant doc sections present
  specificity (25%)      — exact unit/artifact refs, coded IDs, tables
  risk_depth (15%)       — structured risk labels and red flags
  assistant_usefulness (25%) — confidence/evidence/change/debug signals
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.custom.doc_catalog import load_doc_catalog

logger = logging.getLogger(__name__)

# Baseline minimum line counts calibrated at 50 units (jobs/recipes).
# Scaled at score time by unit_count — see _scaled_min_lines().
_BASE_MIN_LINES_AT_50: dict[str, int] = {
    "00_AGENT_BRIEF": 500,
    "01_ARCHITECTURE_MAP": 900,
    "02_COMPONENT_INVENTORY": 1200,
    "03_DATA_FLOWS_AND_LINEAGE": 1500,
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS": 1200,
    "05_RUNTIME_CONFIG_AND_EXTERNALS": 900,
    "06_OPERATIONS_TESTING_AND_DEBUGGING": 900,
    "07_QUALITY_SECURITY_RISK_SCORECARD": 900,
    "08_CHANGE_PLAYBOOK": 800,
}
# Retained for backwards compatibility with existing imports/tests. The scorer
# itself is contract-driven (One Pack decision D2): it scores every published
# top-level document rather than filtering against the default catalog, so
# pack-named document sets (e.g. INJECTION_FINDINGS.md) score normally instead
# of returning overall=0.0 with an empty doc list.
_PUBLISHABLE_DOC_KEYS = frozenset(load_doc_catalog().required_docs)
_SCALE_REFERENCE_UNITS = 50

# Non-document top-level markdown files that must never be scored as product
# docs (support artifacts that legitimately live at the docs root).
_NON_PRODUCT_DOC_KEYS = frozenset({"CROSS_PROJECT_TOPOLOGY", "README"})

# Engineering-fact support artifacts (engineering_facts.md, dependency_map.md,
# sql_usage.md, ...) are all-lowercase snake_case at the docs root on older
# runs; product docs -- legacy numbered and pack-declared alike -- carry an
# uppercase or numbered key.
_SUPPORT_ARTIFACT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def is_support_artifact_filename(filename: str) -> bool:
    """True when a top-level ``docs/*.md`` file is an engineering-fact artifact.

    These are deterministic derived fact maps (``engineering_facts.md``,
    ``dependency_map.md``, ``sql_usage.md``, ...), not generated documents.
    """
    return bool(_SUPPORT_ARTIFACT_KEY_RE.match(_doc_key(filename)))


def is_published_doc_filename(filename: str) -> bool:
    """True when a top-level ``docs/*.md`` file belongs to the published set.

    Single definition of the publication predicate, shared with
    ``services.rag_bridge._read_md_files`` (One Pack decisions D45/R8): a
    pack's declared document names are authoritative, so neither a numeric
    ``NN_`` prefix nor a catalog allowlist may gate publication. Only two
    classes are excluded:

    * ``_``-prefixed internal artifacts -- pushed separately as internal RAG
      layers by ``RagBridge.sync_internal_layers``.
    * lowercase snake_case engineering-fact support artifacts.

    ``CROSS_PROJECT_TOPOLOGY.md`` and ``README.md`` ARE published (the
    partition orchestrator pushes the former deliberately); they are excluded
    only from *scoring* and from patch targeting -- see
    :func:`is_product_doc_filename`.
    """
    if not filename.endswith(".md") or filename.startswith("_"):
        return False
    return not is_support_artifact_filename(filename)


def is_product_doc_filename(filename: str) -> bool:
    """True when a top-level ``docs/*.md`` file is a generated product document.

    Narrower than :func:`is_published_doc_filename`: also excludes the
    non-product markdown that legitimately lives at the docs root
    (``CROSS_PROJECT_TOPOLOGY.md``, ``README.md``). Used where a document is
    *scored* or *rewritten* -- the quality scorer and the diff patcher -- and
    deliberately NOT used on the RAG push path, which must stay a superset.
    """
    if not is_published_doc_filename(filename):
        return False
    return _doc_key(filename) not in _NON_PRODUCT_DOC_KEYS


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
    "00_AGENT_BRIEF": [
        r"Project Orientation|Project Summary",
        r"Documentation Map",
        r"Onboarding",
        r"Common Tasks",
        r"Known Traps|Do-Not-Miss",
    ],
    "01_ARCHITECTURE_MAP": [
        r"Topology|Architecture",
        r"Entry Point|Trigger",
        r"Dependency|Orchestration",
        r"Boundary|Group",
    ],
    "02_COMPONENT_INVENTORY": [
        r"Canonical Unit Inventory|Inventory",
        r"Source Artifact|Path",
        r"Component|Step|Module",
        r"Dependency",
        r"Scanner-Limited|Coverage Gap",
    ],
    "03_DATA_FLOWS_AND_LINEAGE": [
        r"Data Flow|Flow Pattern|Lineage",
        r"Source|Input|Origin",
        r"Transform|tMap|Expression|Mapping",
        r"Schema|Table|Column|Field",
        r"Data Quality|Validation|Defect",
    ],
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS": [
        r"Business Rule|Rule Catalog|Rules",
        r"Domain|Category|Entity|Concept",
        r"Logic|Expression|Filter|Condition",
        r"Conflict|Exception|Edge Case|Anomaly",
    ],
    "05_RUNTIME_CONFIG_AND_EXTERNALS": [
        r"Environment|Configuration|Setting",
        r"Connection|Credential|Secret|Auth",
        r"External|Integration|API|System",
        r"Context|Variable|Parameter",
        r"Deployment|Portability",
        r"Hardcoded|Rotation|Credential Risk",
    ],
    "06_OPERATIONS_TESTING_AND_DEBUGGING": [
        r"Run|Execute|Procedure",
        r"Debug|Troubleshoot",
        r"Verify|Validation|Health Check",
        r"Recovery|Rollback",
        r"Testing|Coverage",
    ],
    "07_QUALITY_SECURITY_RISK_SCORECARD": [
        r"Score|Dimension",
        r"Confidence",
        r"Evidence",
        r"Red Flag|Risk",
        r"Quick Win|Unknown",
    ],
    "08_CHANGE_PLAYBOOK": [
        r"Change Impact",
        r"Inspect",
        r"Test|Verify",
        r"Rollback",
    ],
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
_PATH_RE = re.compile(r"\b[\w./-]+\.(?:item|json|xml|py|ts|tsx|js|java|sql|yaml|yml|properties|config)\b")
_ASSISTANT_SIGNAL_RE = re.compile(
    r"\b(confidence|evidence|scanner-limited|source artifact|inspect|test|verify|debug|change impact|rollback|unknown)\b",
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
    # Keep numbered assistant-grade stems such as "00_AGENT_BRIEF".
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


def _score_assistant_usefulness(content: str) -> tuple[float, list[str]]:
    flags: list[str] = []
    hits = len(_ASSISTANT_SIGNAL_RE.findall(content))
    paths = len(_PATH_RE.findall(content))
    table_rows = max(0, len(_TABLE_ROW_RE.findall(content)) - len(_SEPARATOR_ROW_RE.findall(content)))
    score = 0.0
    if hits >= 20:
        score += 2.0
    elif hits >= 10:
        score += 1.5
    elif hits >= 4:
        score += 1.0
    else:
        flags.append("weak_assistant_signals")
    if paths >= 8:
        score += 1.5
    elif paths >= 3:
        score += 1.0
    elif paths >= 1:
        score += 0.5
    else:
        flags.append("no_source_artifacts")
    if table_rows >= 12:
        score += 1.5
    elif table_rows >= 5:
        score += 1.0
    elif table_rows >= 1:
        score += 0.5
    else:
        flags.append("no_actionable_tables")
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
    usefulness_score, usefulness_flags = _score_assistant_usefulness(content)

    # Weighted composite
    composite = (
        0.15 * line_score
        + 0.20 * section_score
        + 0.25 * specificity_score
        + 0.15 * risk_score
        + 0.25 * usefulness_score
    )
    flags: list[str] = []
    if lines < min_lines:
        flags.append("below_min_lines")
    flags.extend(section_flags)
    flags.extend(spec_flags)
    flags.extend(risk_flags)
    flags.extend(usefulness_flags)

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
            "assistant_usefulness": usefulness_score,
        },
    )


class DocQualityScorer:
    """Thin wrapper around score_project for backwards-compatible usage."""

    def score(self, project_id: str, docs_dir: Path | None = None) -> RunQualityReport:
        return score_project(project_id, docs_dir=docs_dir)


def score_project(
    project_id: str,
    docs_dir: Path | None = None,
    allowed_docs: frozenset[str] | set[str] | None = None,
) -> RunQualityReport:
    """Score all published docs in a project's docs/ directory.

    ``allowed_docs`` restricts scoring to an explicit doc-name set (the active
    pack's declared docs). Without it every non-underscore top-level markdown
    file is scored -- the published product set, whatever pack produced it
    (same name-agnostic pattern as ``partition_orchestrator._count_public_docs``).
    """
    if docs_dir is None:
        docs_dir = config.project_root / "output" / project_id / "docs"

    def _is_product_doc(p: Path) -> bool:
        if not p.is_file() or not is_product_doc_filename(p.name):
            return False
        if allowed_docs is not None:
            return _doc_key(p.name) in allowed_docs
        return True

    doc_files = sorted(p for p in docs_dir.glob("*.md") if _is_product_doc(p))

    if not doc_files:
        return RunQualityReport(
            overall=0.0,
            docs=[],
            graded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    unit_count = _SCALE_REFERENCE_UNITS
    facts_path = docs_dir / "facts_manifest.json"
    if facts_path.exists():
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
            unit_count = int(facts.get("unit_count", _SCALE_REFERENCE_UNITS))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to read facts_manifest.json for %s: %s", docs_dir, exc)

    doc_scores = [score_doc(f, unit_count=unit_count) for f in doc_files]
    overall = round(sum(d.score for d in doc_scores) / len(doc_scores), 2)

    return RunQualityReport(
        overall=overall,
        docs=doc_scores,
        graded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
