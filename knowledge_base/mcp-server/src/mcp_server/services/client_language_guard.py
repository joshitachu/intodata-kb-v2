"""Client-language guard for the standard documentation lane.

A published document names Talend designs, components, systems and business
objects. It never names the machinery that produced them: no staging
directory, no internal artifact filename, no developer path, no internal id,
and no sentence of the form "based on our internal analysis method in
_analysis/whatever".

Relationship to the enterprise lane
-----------------------------------
``providers/custom/enterprise/client_language.py`` solves this problem for the
enterprise lane. It cannot be imported here: it reads
``INTERNAL_PATH_MARKERS`` from ``enterprise/layout.py``, and importing anything
under ``mcp_server.providers.custom`` executes that package's ``__init__``,
which imports the 7,876-line harness. This module is the equivalent for the
standard lane.

REUSED from it, deliberately unchanged in shape: the internal-marker list as
the core of the path category, the internal-id patterns (``ev-``/``ek-``/
``claim-``/``sem-``/``req-`` + 24 hex, ``claim:``, HTML comments, the
``.item#//`` fragment form), the ``mapping_table:`` cell check, and
:func:`strip_internal_markers` as a narrow non-prose rewrite.

DROPPED from it, because they do not transfer to this lane: the two
"scanner-limited" patterns and ``Evidence Register``. Both are REQUIRED
document content here, not leakage -- see the note below the pattern table.

ADDED here: per-line reporting with line numbers and a category, a
self-referential-phrasing category, an internal-artifact-filename category, a
developer-path category, and the provenance sidecar reader/validator.

The false-positive boundary, which is the whole design
------------------------------------------------------
Only OUR machinery is forbidden. A document may legitimately name a customer
artifact, and a guard that cries wolf gets switched off, so:

* ``Web.config``, ``Global.asax.cs``, ``routines.json``, ``pom.xml``, a
  ``.item`` design, a ``.properties`` file, ``process/Foo.item`` -- all
  allowed. ``routines.json`` in particular is a name a Talend document has a
  real reason to use.
* Underscore markers match only as whole path-ish tokens, so
  ``engineering_facts`` does not trip the ``_facts`` marker and
  ``tDBOutput_1`` / ``AssetCheck_GetLocation`` trip nothing.
* ``/opt/...``, ``/var/...``, ``/srv/...``, ``/mnt/...``, ``/data/...`` are NOT
  flagged: a Talend runtime genuinely lives in ``/opt/talend`` and a client
  document should be free to say so. Only paths that can only be ours are
  flagged -- a developer or container home, a temp dir, or any path containing
  one of our internal markers.
* "Phase 1" is flagged only in the pipeline sense ("the Phase 2 analysis",
  ``phase3_docs``), never in the business sense ("Phase 1 of the rollout").
* "the model" is not flagged; "the LLM" and "language model" are. Data models
  are a normal subject of these documents.
* Generic Phase 2 section names (``RISK_FLAGS``, ``CUSTOM_CODE``, ...) are NOT
  in the marker set: a Talend context variable can carry those names. Only
  unambiguous pipeline markers are (``GROUP_SUMMARY``, ``THIN_BUNDLE``, ...).

Advisory by convention
----------------------
The four Phase 3 final-check gates in this codebase warn and ship rather than
raise. This guard follows that convention: it writes a report and returns it.
Nothing here raises on a finding.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.services.doc_quality_scorer import is_published_doc_path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Where the report lands. ``_assessment/`` is on Phase 4's published-staging
#: allowlist, so the report survives the swap and an operator can read it off
#: the published tree.
ASSESSMENT_DIRNAME = "_assessment"
LEAKAGE_REPORT_FILENAME = "client_language.json"
PROVENANCE_REPORT_FILENAME = "provenance_validation.json"

#: Staging-relative directory the doc writers emit provenance sidecars into.
PROVENANCE_DIRNAME = "_provenance"

#: Per-document finding cap. A document that leaks on every line should not
#: produce a megabyte of report.
MAX_FINDINGS_PER_DOC = 200

#: Internal directory / vocabulary tokens. Matched as whole tokens (see
#: :func:`_token_pattern`), which is the difference between catching
#: ``docs/_analysis/x.md`` and falsely catching ``engineering_facts``.
INTERNAL_TOKEN_MARKERS: tuple[str, ...] = (
    "_analysis",
    "_preprocess",
    "_assessment",
    "_transient",
    "_evidence_registry",
    "_rag_support",
    "_agent_tasks",
    "_model_inputs",
    "_debug",
    "_investigation",
    "_audit",
    "_topology",
    "_facts",
    "_workspace",
    "_method",
    "_exact",
    "_provenance",
    "_connector_evidence",
    "_context_packets",
    "_claims",
    "_analysis_guide",
    "_doc_templates",
    "_phase3_guide",
    "_since_last_run",
    "_knowledge",
    "_unit_docs_digest",
    "_runtime_profile",
    "_cross_cutting_facts",
    "docs_staging",
    "analysis_cache",
    "intodoc",
    "intodoc-output",
    "intodoc_output",
    "mcp-server",
    "mcp_server",
    "intodata-kb",
    "plugin_packs",
    "prompt_templates",
    "GROUP_SUMMARY",
    "CROSS_JOB_FINDINGS",
    "CROSS_CHUNK_FINDINGS",
    "THIN_BUNDLE",
)

#: Internal markers whose useful form is a PREFIX (they end in ``_`` or carry a
#: variable tail), so the whole-token rule cannot express them.
INTERNAL_PREFIX_MARKERS: tuple[str, ...] = (
    "_doc_pack",
    "_bundle_",
    "_evidence_",
)

#: Our own artifact filenames. Every entry here is produced by this platform.
#: ``routines.json`` is deliberately absent -- see the module docstring.
INTERNAL_FILENAMES: tuple[str, ...] = (
    "scan_result.json",
    "scan/manifest.json",
    "facts_manifest.json",
    "mapping_accounting.json",
    "capability_ledger.json",
    "engineering_facts.md",
    "dependency_map.md",
    "config_runtime_map.md",
    "known_risks.md",
    "change_impact_matrix.md",
    "sql_usage.md",
    "api_surface.md",
    "cross_project_refs.md",
    "cross_project_index.json",
    "entity_index.csv",
    "usage_index.csv",
    "quick_scan.md",
    "scorecard.md",
    "failed_docs.json",
    "slice_cap_flags.json",
    "pack_section_coverage.json",
    "evidence_traceability.json",
    "publication_verdict.json",
    "enterprise_gates.json",
    "runtime_evidence.json",
    "runtime_evidence_coverage.json",
    "business_topology.json",
    "knowledge_model.json",
    "doc_catalog.yaml",
    "plugin.yaml",
    "ANALYSIS_GUIDE.md",
    # The project-memory files (services/project_memory.py). Internal by
    # construction; naming one in a client document exposes the memory layer.
    "key-notes.md",
    "decisions.md",
    "lessons-learned.md",
    "terminology.md",
    "open-questions.md",
    "known-limitations.md",
)

_SELF_REFERENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        # Possessive form: unambiguous, so the noun list can be wide.
        "based on our analysis/method",
        re.compile(
            r"based\s+(?:up)?on\s+(?:our|my|this)\s+(?:own\s+)?(?:internal\s+)?"
            r"(?:analys[ei]s|analysis\s+method|method|methodology|investigation|"
            r"evidence\s+(?:base|bundle|pack)|preprocessing|extraction|pipeline|"
            r"process(?:ing)?|scan\s+output|tooling|notes)",
            re.IGNORECASE,
        ),
    ),
    (
        # "the" form: the noun list is narrow on purpose. "based on the
        # process" and "based on the extraction logic" are ordinary client
        # prose about the integration, not references to our machinery.
        "based on the analysis",
        re.compile(
            r"based\s+(?:up)?on\s+the\s+(?:internal\s+)?"
            r"(?:analys[ei]s|analysis\s+method|methodology|investigation|"
            r"evidence\s+(?:base|bundle|pack))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "based on our internal <something>",
        re.compile(r"based\s+(?:up)?on\s+our\s+internal\b", re.IGNORECASE),
    ),
    (
        "per the analysis in",
        re.compile(
            r"per\s+the\s+(?:analys[ei]s|evidence|investigation|notes)\s+(?:in|at|under|of)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "according to our/the analysis",
        re.compile(
            r"according\s+to\s+(?:our|the|this)\s+(?:internal\s+)?"
            r"(?:analys[ei]s|investigation|method|methodology|evidence\s+(?:base|bundle))",
            re.IGNORECASE,
        ),
    ),
    (
        "the internal analysis/method",
        re.compile(
            r"\bthe\s+internal\s+(?:analys[ei]s|method|methodology|evidence|notes|"
            r"documentation|process|tooling|pipeline|model|review)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "our internal method/process",
        re.compile(
            r"\bour\s+internal\s+(?:analys[ei]s|method|methodology|process|pipeline|"
            r"tooling|notes|evidence|documentation|review)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "as documented in our analysis",
        re.compile(
            r"(?:as\s+)?(?:described|documented|noted|recorded|captured|detailed|stated)"
            r"\s+in\s+(?:our|the|this)\s+(?:internal\s+)?"
            r"(?:analys[ei]s|investigation|evidence|notes|method)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Narrowed to a path-ish target: "see the analysis in Section 4" is a
        # cross-reference inside the document and entirely legitimate.
        "see the analysis in <path>",
        re.compile(
            r"\bsee\s+(?:the\s+)?(?:analys[ei]s|evidence|investigation)"
            r"(?:\s+(?:file|files|artifact|artifacts|output|outputs|bundle|bundles))?"
            r"\s+(?:in|at|under)\s+[`'\"]?[_./]",
            re.IGNORECASE,
        ),
    ),
    (
        "in the group analysis file",
        re.compile(
            r"\b(?:in|from)\s+the\s+(?:group\s+)?analys[ei]s\s+"
            r"(?:file|files|artifact|artifacts|output|outputs|bundle|bundles|layer|cache)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "derived from our analysis/preprocessing",
        re.compile(
            r"\bderived\s+from\s+(?:our|the)\s+"
            r"(?:analys[ei]s|preprocessing|extraction|evidence\s+bundle|scan\s+pipeline)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # "the pipeline" alone is standard data-integration vocabulary and is
        # NOT flagged. Only a qualifier that names OUR machinery is.
        "the documentation pipeline/harness/generator",
        re.compile(
            r"\b(?:this|our|the)\s+(?:documentation|generation|doc|analysis|scanning)\s+"
            r"(?:pipeline|harness|generator|engine)\b"
            r"|\b(?:documentation|doc)\s+harness\b"
            r"|\bthe\s+harness\b",
            re.IGNORECASE,
        ),
    ),
    ("sub-agent", re.compile(r"\bsub-?agents?\b", re.IGNORECASE)),
    ("LLM / language model", re.compile(r"\b(?:LLMs?|language\s+models?)\b", re.IGNORECASE)),
    (
        "prompt machinery",
        re.compile(r"\b(?:system\s+prompt|prompt\s+template|mega-?prompt)\b", re.IGNORECASE),
    ),
    (
        "evidence bundle / doc pack vocabulary",
        re.compile(
            r"\b(?:evidence\s+(?:bundle|pack)|doc\s+pack|target\s+evidence|"
            r"analysis\s+cache|thin\s+bundle|bin-?pack(?:ed|ing)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # The pipeline sense only, established by collocation. "Phase 1 of the
        # rollout", "Phase 2 documents were delivered in March" and "the Phase
        # 1 pilot" are business language and are NOT flagged -- a bare
        # "the Phase N" pattern was tried and dropped for exactly that reason.
        "pipeline phase reference",
        re.compile(
            r"\bphase\s*\d(?:\.\d)?\s+(?:analys[ei]s|investigation|agents?|"
            r"sub-?agents?|evidence|prompts?|writer|synthesis|cleanup|scorecard|"
            r"bundles?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pipeline phase identifier",
        re.compile(r"\bphase\d(?:\.\d)?_[a-z_]+\b", re.IGNORECASE),
    ),
)

# DROPPED from the enterprise pattern set, both verified against the real
# documents this lane publishes:
#
#   re.compile(r"(?<![A-Za-z])scanner[- ]limited", re.IGNORECASE)
#   re.compile(r"scanner detail is limited", re.IGNORECASE)
#       "Scanner-Limited Evidence" is a REQUIRED section heading of
#       02_COMPONENT_INVENTORY (claude_service._write_doc_templates), and
#       "Scanner-Limited ..." headings appear in 00, 01, 03 and 07 of a real
#       published set. Flagging them fails every document on a section the
#       pipeline itself demands. A document stating that the scanner could not
#       extract something is an honest evidence limitation and is WANTED --
#       it is the same channel as open-questions.md, and suppressing it pushes
#       the model back toward confident guessing, which is the defect being
#       fixed.
#
#   re.compile(r"\bEvidence Register\b")
#       Same class of error: "## Evidence Register" is a section heading in
#       every document of a real published set in this lane, and
#       _write_doc_templates specifies the format of its confidence cells. It
#       is internal vocabulary in the enterprise lane and a published section
#       here, so the enterprise rule does not transfer.

_INTERNAL_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal evidence id", re.compile(r"\bev-[0-9a-f]{24}\b")),
    ("internal evidence-key id", re.compile(r"\bek-[0-9a-f]{24}\b")),
    ("internal claim id", re.compile(r"\bclaim-[0-9a-f]{24}\b")),
    ("internal semantic id", re.compile(r"\bsem-[0-9a-f]{24}\b")),
    ("internal requirement id", re.compile(r"\breq-[0-9a-f]{24}\b")),
    ("internal claim reference", re.compile(r"\bclaim:[A-Za-z0-9_-]+")),
    ("internal artifact reference", re.compile(r"\bartifact:[0-9a-f]{8,}\b")),
    ("raw mapping table reference id", re.compile(r"\bmapping_table:[0-9a-f]{8,}\b")),
    ("internal step reference", re.compile(r"\bstep:[0-9a-f]{8,}\b")),
    ("HTML comment", re.compile(r"<!--.*?-->", re.DOTALL)),
    ("internal artifact fragment", re.compile(r"\.(?:item|properties|xml)#//")),
)

#: Paths that can only be ours: a developer or container home, or a temp dir.
#: ``/opt``, ``/var``, ``/srv``, ``/mnt`` and ``/data`` are absent on purpose --
#: those are places a client's Talend runtime actually lives.
_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "developer or container filesystem path",
        re.compile(
            r"(?<![A-Za-z0-9_:.~-])/(?:Users|home|root|tmp|private/var|var/folders)/"
            r"[^\s\)\]\}`\"',;]*"
        ),
    ),
    (
        "Windows user path",
        re.compile(r"\b[A-Za-z]:\\+(?:Users|Temp|Windows\\+Temp)\\+[^\s\)\]\}`\"',;]*"),
    ),
    (
        "platform output-tree path",
        re.compile(
            r"(?<![A-Za-z0-9_/-])output/[A-Za-z0-9_.-]+/(?:docs|docs_staging|knowledge)\b"
        ),
    ),
)

_CODE_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:```|~~~)", re.MULTILINE)
_TABLE_ID_CELL = re.compile(r"\bmapping_table:[0-9a-f]{8,}\b")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

Category = Literal[
    "internal_path", "internal_file", "self_reference", "internal_id", "absolute_path"
]


def _token_pattern(marker: str) -> re.Pattern[str]:
    """Whole-token match for an internal marker.

    ``(?<![A-Za-z0-9_])`` / ``(?![A-Za-z0-9_])`` is what separates
    ``docs/_facts/x.md`` (a finding) from ``engineering_facts`` (not one, the
    marker is preceded by ``g``) and from ``tDBOutput_1`` (no marker at all).
    A trailing ``/``, backtick, quote, space or end of line all satisfy it.
    """
    return re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(marker) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE if marker.islower() or "-" in marker else 0,
    )


_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (marker, _token_pattern(marker)) for marker in INTERNAL_TOKEN_MARKERS
)
_PREFIX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        marker,
        re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(marker) + r"[A-Za-z0-9_.-]*",
            re.IGNORECASE,
        ),
    )
    for marker in INTERNAL_PREFIX_MARKERS
)
_FILENAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        name,
        re.compile(
            r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    )
    for name in INTERNAL_FILENAMES
)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class LeakageFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str
    line: int
    category: str
    marker: str
    excerpt: str


class LeakageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    ok: bool = True
    advisory: bool = True
    scanned: int = 0
    findings: list[LeakageFinding] = Field(default_factory=list)
    by_document: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    truncated_documents: list[str] = Field(default_factory=list)

    def summary_line(self) -> str:
        if self.ok:
            return (
                f"Client-language guard: {self.scanned} document(s) clean "
                "(no internal paths, filenames, ids or self-referential phrasing)"
            )
        worst = sorted(self.by_document.items(), key=lambda item: (-item[1], item[0]))[:3]
        detail = ", ".join(f"{name} ({count})" for name, count in worst)
        return (
            f"Client-language guard: {len(self.findings)} finding(s) across "
            f"{len(self.by_document)} of {self.scanned} document(s) -- {detail}"
        )


class ProvenanceAnchor(BaseModel):
    """One quoted block or protected fact traced back to its evidence."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["quote", "protected_fact"]
    group: str = ""
    unit: str = ""
    source: str = ""
    label: str = ""
    excerpt: str = ""


class ProvenanceSidecar(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    document: str = ""
    anchors: list[ProvenanceAnchor] = Field(default_factory=list)


class ProvenanceDocReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str
    sidecar_present: bool = False
    sidecar_path: str = ""
    anchors: int = 0
    quote_anchors: int = 0
    protected_fact_anchors: int = 0
    code_blocks: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProvenanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    ok: bool = True
    advisory: bool = True
    scanned: int = 0
    sidecar_dir: str = PROVENANCE_DIRNAME
    sidecar_stays_internal: bool = True
    sidecar_internal_reason: str = ""
    documents: list[ProvenanceDocReport] = Field(default_factory=list)

    def summary_line(self) -> str:
        errors = sum(len(doc.errors) for doc in self.documents)
        warnings = sum(len(doc.warnings) for doc in self.documents)
        missing = sum(1 for doc in self.documents if not doc.sidecar_present)
        return (
            f"Provenance sidecars: {self.scanned} document(s), {missing} without a "
            f"sidecar, {errors} error(s), {warnings} warning(s)"
        )


# --------------------------------------------------------------------------- #
# Leakage detection
# --------------------------------------------------------------------------- #

def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    low, high = 0, len(starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return low + 1


def _excerpt(text: str, starts: list[int], line: int) -> str:
    start = starts[line - 1]
    end = text.find("\n", start)
    raw = text[start:] if end == -1 else text[start:end]
    raw = raw.strip()
    if len(raw) > 200:
        raw = raw[:200] + " ..."
    return raw.encode("ascii", "backslashreplace").decode("ascii")


def leakage_findings(text: str, *, document: str = "") -> list[LeakageFinding]:
    """Every internal-language finding in ``text``, with line numbers.

    Ordered by line, then category, then marker, so two runs over the same
    document produce the same report.
    """
    starts = _line_starts(text)
    seen: set[tuple[int, str, str]] = set()
    findings: list[LeakageFinding] = []

    def _scan(
        patterns: Iterable[tuple[str, re.Pattern[str]]], category: Category
    ) -> None:
        for marker, pattern in patterns:
            for match in pattern.finditer(text):
                line = _line_of(starts, match.start())
                key = (line, category, marker)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    LeakageFinding(
                        document=document,
                        line=line,
                        category=category,
                        marker=marker,
                        excerpt=_excerpt(text, starts, line),
                    )
                )

    _scan(_TOKEN_PATTERNS, "internal_path")
    _scan(_PREFIX_PATTERNS, "internal_path")
    _scan(_FILENAME_PATTERNS, "internal_file")
    _scan(_SELF_REFERENCE_PATTERNS, "self_reference")
    _scan(_INTERNAL_ID_PATTERNS, "internal_id")
    _scan(_PATH_PATTERNS, "absolute_path")

    findings.sort(key=lambda item: (item.line, item.category, item.marker))
    return findings


def strip_internal_markers(text: str) -> str:
    """Remove HTML comments and bare table-id references; never rewrites prose.

    Same narrow contract as the enterprise lane's function of the same name:
    it deletes non-prose artifacts and substitutes one machine reference. It
    does not attempt to rewrite a leaking sentence -- a sentence that cites our
    method is a content defect and needs a rewrite, not a search-and-replace.
    """
    text = _HTML_COMMENT_RE.sub("", text)
    text = _TABLE_ID_CELL.sub("the exact mapping table below", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def discover_client_docs(staging_dir: Path) -> list[str]:
    """Top-level published document names (without ``.md``) in ``staging_dir``.

    Uses ``doc_quality_scorer.is_published_doc_path`` rather than a local rule,
    so the guard scans exactly the set that publication and the RAG push will
    see. A pack-invented document name is therefore covered automatically.
    """
    staging_dir = Path(staging_dir)
    if not staging_dir.is_dir():
        return []
    return sorted(
        path.stem
        for path in staging_dir.glob("*.md")
        if path.is_file() and is_published_doc_path(path.name)
    )


def validate_client_docs(
    staging_dir: Path, doc_names: Iterable[str] | None = None
) -> LeakageReport:
    """Scan the published document set for internal language. Never raises."""
    staging_dir = Path(staging_dir)
    names = list(doc_names) if doc_names is not None else discover_client_docs(staging_dir)

    report = LeakageReport(scanned=0)
    for name in names:
        path = staging_dir / (name if name.endswith(".md") else f"{name}.md")
        if not path.is_file():
            continue
        report.scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Client-language guard: cannot read %s (%s)", path, exc)
            continue
        found = leakage_findings(text, document=path.name)
        if len(found) > MAX_FINDINGS_PER_DOC:
            report.truncated_documents.append(path.name)
            found = found[:MAX_FINDINGS_PER_DOC]
        if not found:
            continue
        report.findings.extend(found)
        report.by_document[path.name] = len(found)
        for finding in found:
            report.by_category[finding.category] = (
                report.by_category.get(finding.category, 0) + 1
            )
    report.ok = not report.findings
    return report


def write_leakage_report(staging_dir: Path, report: LeakageReport) -> Path:
    """Write the leakage report under ``_assessment/``."""
    target = Path(staging_dir) / ASSESSMENT_DIRNAME / LEAKAGE_REPORT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


# --------------------------------------------------------------------------- #
# Provenance sidecars
# --------------------------------------------------------------------------- #

def provenance_dir(staging_dir: Path) -> Path:
    return Path(staging_dir) / PROVENANCE_DIRNAME


def sidecar_path(staging_dir: Path, doc_name: str) -> Path:
    stem = doc_name.removesuffix(".md")
    return provenance_dir(staging_dir) / f"{stem}.json"


def sidecar_stays_internal() -> tuple[bool, str]:
    """Confirm, rather than assume, that a sidecar cannot reach the client.

    Derived from the same predicate publication uses
    (``doc_quality_scorer.is_published_doc_path``, which is also the rule
    ``RagBridge._read_nested_md_files`` applies): a path with any ``_``- or
    ``.``-prefixed segment, or a non-``.md`` suffix, is not published. A
    sidecar is both -- ``_provenance/`` is underscore-prefixed AND the file is
    ``.json`` -- so it fails the predicate twice.
    """
    probe = f"{PROVENANCE_DIRNAME}/EXAMPLE_DOC.json"
    md_probe = f"{PROVENANCE_DIRNAME}/EXAMPLE_DOC.md"
    if is_published_doc_path(probe):
        return False, f"{probe} satisfies the publication predicate"
    if is_published_doc_path(md_probe):
        return (
            False,
            f"{md_probe} would publish; the sidecar is only safe because it is .json",
        )
    return True, (
        f"{probe} fails the publication predicate on both counts: the "
        f"'{PROVENANCE_DIRNAME}' segment is underscore-prefixed and the suffix is "
        "not .md"
    )


def count_code_blocks(text: str) -> int:
    """Number of fenced code blocks (opening fences paired with a closing one)."""
    return len(_CODE_FENCE_RE.findall(text)) // 2


def read_provenance_sidecar(
    staging_dir: Path, doc_name: str
) -> tuple[ProvenanceSidecar | None, list[str]]:
    """Read and shape-validate one sidecar. Returns ``(sidecar, errors)``."""
    path = sidecar_path(staging_dir, doc_name)
    if not path.is_file():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [f"sidecar {path.name} is not readable JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"sidecar {path.name} must be a JSON object, got {type(payload).__name__}"]
    try:
        return ProvenanceSidecar.model_validate(payload), []
    except Exception as exc:  # noqa: BLE001 - shape errors are reported, not raised
        return None, [f"sidecar {path.name} does not match the expected shape: {exc}"]


def validate_provenance(
    staging_dir: Path,
    doc_names: Iterable[str] | None = None,
    *,
    known_groups: Iterable[str] = (),
    known_units: Iterable[str] = (),
) -> ProvenanceReport:
    """Validate every document's provenance sidecar. Never raises.

    A document that quotes code with no corresponding anchor is the case this
    exists to catch: a verbatim block with no traceable origin is
    indistinguishable from an invented one.
    """
    staging_dir = Path(staging_dir)
    names = list(doc_names) if doc_names is not None else discover_client_docs(staging_dir)
    groups = {g for g in known_groups}
    units = {u for u in known_units}

    internal, reason = sidecar_stays_internal()
    report = ProvenanceReport(sidecar_stays_internal=internal, sidecar_internal_reason=reason)

    for name in names:
        doc_path = staging_dir / (name if name.endswith(".md") else f"{name}.md")
        if not doc_path.is_file():
            continue
        report.scanned += 1
        try:
            text = doc_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Provenance validation: cannot read %s (%s)", doc_path, exc)
            continue

        blocks = count_code_blocks(text)
        entry = ProvenanceDocReport(
            document=doc_path.name,
            code_blocks=blocks,
            sidecar_path=str(sidecar_path(staging_dir, doc_path.name).name),
        )
        sidecar, errors = read_provenance_sidecar(staging_dir, doc_path.name)
        entry.errors.extend(errors)
        entry.sidecar_present = sidecar is not None

        if sidecar is None:
            if blocks > 0 and not errors:
                entry.errors.append(
                    f"quotes {blocks} code block(s) but has no provenance sidecar, so "
                    "no quoted block can be traced to a group analysis or unit"
                )
            report.documents.append(entry)
            continue

        entry.anchors = len(sidecar.anchors)
        entry.quote_anchors = sum(1 for a in sidecar.anchors if a.kind == "quote")
        entry.protected_fact_anchors = entry.anchors - entry.quote_anchors

        if sidecar.document and sidecar.document not in (doc_path.name, doc_path.stem):
            entry.errors.append(
                f"sidecar names document {sidecar.document!r}, expected {doc_path.name!r}"
            )
        if sidecar.schema_version != SCHEMA_VERSION:
            entry.warnings.append(
                f"sidecar schema_version is {sidecar.schema_version}, expected {SCHEMA_VERSION}"
            )

        for index, anchor in enumerate(sidecar.anchors):
            label = anchor.label or anchor.excerpt[:40] or f"anchor {index}"
            if not anchor.group:
                entry.errors.append(f"{label}: no group, so it anchors to nothing")
                continue
            if groups and anchor.group not in groups:
                entry.errors.append(
                    f"{label}: group {anchor.group!r} is not one of this run's groups"
                )
            if anchor.unit and units and anchor.unit not in units:
                entry.errors.append(
                    f"{label}: unit {anchor.unit!r} is not one of this run's units"
                )
            if anchor.kind == "quote" and not anchor.excerpt:
                entry.warnings.append(
                    f"{label}: quote anchor carries no excerpt, so it cannot be matched "
                    "back to a block in the document"
                )

        if blocks > 0 and entry.quote_anchors == 0:
            entry.errors.append(
                f"quotes {blocks} code block(s) but the sidecar has no quote anchor"
            )
        elif blocks > entry.quote_anchors:
            entry.warnings.append(
                f"{blocks} code block(s) but only {entry.quote_anchors} quote anchor(s); "
                f"{blocks - entry.quote_anchors} quoted block(s) are unanchored"
            )

        report.documents.append(entry)

    report.ok = not any(doc.errors for doc in report.documents)
    return report


def write_provenance_report(staging_dir: Path, report: ProvenanceReport) -> Path:
    """Write the provenance report under ``_assessment/``."""
    target = Path(staging_dir) / ASSESSMENT_DIRNAME / PROVENANCE_REPORT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
