"""Client-language guard: internal provenance never reaches client prose.

Evidence ids, staging paths, claim markers and Evidence Registers stay under
``_evidence_registry`` (RAG, repair, traceability). Client documents name Talend
designs, systems and business objects; they never name the machinery that
produced them (plan decision 10).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.custom.enterprise.layout import INTERNAL_PATH_MARKERS

_ID_PATTERNS = (
    re.compile(r"\bev-[0-9a-f]{24}\b"),
    re.compile(r"\bek-[0-9a-f]{24}\b"),
    re.compile(r"\bclaim-[0-9a-f]{24}\b"),
    re.compile(r"\bsem-[0-9a-f]{24}\b"),
    re.compile(r"\breq-[0-9a-f]{24}\b"),
    re.compile(r"claim:[A-Za-z0-9_-]+"),
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"\.(?:item|properties|xml)#//"),
    re.compile(r"(?<![A-Za-z])scanner[- ]limited", re.IGNORECASE),
    re.compile(r"scanner detail is limited", re.IGNORECASE),
)
_TABLE_ID_CELL = re.compile(r"\bmapping_table:[0-9a-f]{8,}\b")


class LeakageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    findings: dict[str, list[str]] = Field(default_factory=dict)
    scanned: int = 0


def leakage_findings(text: str) -> list[str]:
    findings: list[str] = []
    for marker in INTERNAL_PATH_MARKERS:
        if marker in text:
            findings.append(f"internal marker {marker!r}")
    for pattern in _ID_PATTERNS:
        match = pattern.search(text)
        if match:
            snippet = match.group(0)[:60].replace("\n", " ")
            findings.append(f"internal identifier or comment {snippet!r}")
    if _TABLE_ID_CELL.search(text):
        findings.append("raw mapping table reference id left in prose")
    return findings


def strip_internal_markers(text: str) -> str:
    """Remove HTML comments and bare table-id references; never rewrites prose."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = _TABLE_ID_CELL.sub("the exact mapping table below", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def validate_client_docs(staging_dir: Path, doc_names: list[str]) -> LeakageReport:
    findings: dict[str, list[str]] = {}
    scanned = 0
    for name in doc_names:
        path = staging_dir / f"{name}.md"
        if not path.is_file():
            continue
        scanned += 1
        found = leakage_findings(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            findings[name] = found
    return LeakageReport(ok=not findings, findings=findings, scanned=scanned)
