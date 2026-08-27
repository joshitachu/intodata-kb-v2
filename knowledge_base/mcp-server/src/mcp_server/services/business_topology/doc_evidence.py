"""The documentation-derived evidence tier, and the design-name resolver.

Two facilities, both needed because Stage A reads the run's OWN finished Phase 3
documents:

1. **A typed evidence tier.** A flow extracted from
   ``03_DATA_FLOWS_AND_LINEAGE.md`` has to cite something, and the validator
   rejects any citation outside ``bundle.evidence_ids``. Registering each doc
   section as a ``documentation_derived`` evidence record makes the citation
   resolvable AND keeps it distinguishable: it is deliberately excluded from
   ``_SCANNER_EVIDENCE_TYPES`` and ``_DIRECTIONAL_EVIDENCE_TYPES``, so it can
   never promote a system or create an inter-system edge on its own. The
   hand-curated werkorder topology cited these docs as bare strings
   (``"03_DATA_FLOWS_AND_LINEAGE.md - 'Maximo-to-AssetCheck Flows'"``), which is
   exactly why that file cannot pass validation today.

2. **A design-name -> ``artifact:`` id resolver.** The model names plain designs
   (``Maximo2AssetCheck_SendWorkOrder``); Python resolves the 20-char hash. Keys
   are EXACT, never fuzzy: ``AssetCheck_GetLocation`` and
   ``AssetCheck_GetLocations`` are both real, separately-deployed Talend jobs,
   and a normaliser that folded them would wire evidence to the wrong job.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)

#: Evidence type for a section of a generated document. Kept out of every
#: scanner type set in :mod:`validation` on purpose.
DOC_EVIDENCE_TYPE = "documentation_derived"

#: Topic keywords used to RANK discovered documents, highest first. Ranking, not
#: filtering: the document set is whatever the run published. A pack replaces the
#: catalog wholesale (`docs:` in `plugin.yaml`), so a fixed filename list would
#: silently disable this whole lane for every pack that renames its documents --
#: and for any future change to `doc_catalog.yaml`. Keywords are matched against
#: the filename, so they degrade to "unranked but included" rather than "absent".
_DOC_PRIORITY_KEYWORDS = (
    ("DATA_FLOW", 0),
    ("LINEAGE", 0),
    ("INTEGRATION", 1),
    ("ARCHITECTURE", 2),
    ("LANDSCAPE", 2),
    ("TOPOLOGY", 2),
    ("BUSINESS_RULE", 3),
    ("TRANSFORMATION", 3),
    ("INTERFACE", 3),
    ("RUNTIME", 4),
    ("EXTERNAL", 4),
    ("COMPONENT", 5),
    ("INVENTORY", 5),
)

#: Never treated as evidence: internal artifacts and the per-unit dossiers, which
#: are numerous and describe one job rather than a business flow.
_DOC_EXCLUDE_PREFIXES = ("_", ".")

#: How many documents to admit. Bounded because they are pushed into the model
#: context; the ranking above decides which ones survive the cut.
DEFAULT_DOC_EVIDENCE_LIMIT = 6


def discover_doc_filenames(
    docs_dir: Path,
    *,
    limit: int = DEFAULT_DOC_EVIDENCE_LIMIT,
) -> tuple[str, ...]:
    """Return this run's published documents, best flow-evidence first.

    Discovery rather than a fixed list: the caller must work for standard runs,
    pack runs with a renamed document set, and any future catalog change,
    without configuration.
    """

    if not docs_dir.is_dir():
        return ()
    candidates: list[tuple[int, str]] = []
    for path in sorted(docs_dir.glob("*.md")):
        if path.name.startswith(_DOC_EXCLUDE_PREFIXES):
            continue
        upper = path.name.upper()
        rank = next(
            (
                rank
                for keyword, rank in _DOC_PRIORITY_KEYWORDS
                if keyword in upper
            ),
            len(_DOC_PRIORITY_KEYWORDS),
        )
        candidates.append((rank, path.name))
    candidates.sort()
    return tuple(name for _, name in candidates[:limit])

#: Heading level that delimits a citable section. H2 is the granularity the
#: curated extraction cited ("'Maximo-to-AssetCheck Flows (REST/File Ingestion)'").
_SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def doc_evidence_id(filename: str, section: str) -> str:
    """Return the stable evidence id for one document section.

    Hashed over (filename, section) so a rerun that regenerates the same
    document with the same headings yields the same ids, and a resumed run does
    not invalidate previously accepted citations.
    """

    digest = hashlib.sha256(
        f"{filename}{section}".encode("utf-8")
    ).hexdigest()[:20]
    return f"doc:{digest}"


def _section_titles(text: str) -> list[str]:
    return [match.group(1).strip() for match in _SECTION_PATTERN.finditer(text)]


def collect_doc_evidence(
    docs_dir: Path,
    *,
    filenames: tuple[str, ...] | None = None,
) -> dict[str, dict[str, object]]:
    """Return ``{evidence_id: record}`` for every citable section on disk.

    A whole-document id is registered alongside each section id so an
    extraction that cites only the filename still resolves. Missing documents
    are skipped silently: a run whose Phase 3 published a subset is normal, and
    a hard failure here would take down a phase that is explicitly non-blocking.
    """

    records: dict[str, dict[str, object]] = {}
    if not docs_dir.is_dir():
        return records
    if filenames is None:
        filenames = discover_doc_filenames(docs_dir)
    for filename in filenames:
        path = docs_dir / filename
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        whole_id = doc_evidence_id(filename, "")
        records[whole_id] = {
            "id": whole_id,
            "type": DOC_EVIDENCE_TYPE,
            "document": filename,
            "section": "",
            "name": filename,
        }
        for section in _section_titles(text):
            section_id = doc_evidence_id(filename, section)
            records[section_id] = {
                "id": section_id,
                "type": DOC_EVIDENCE_TYPE,
                "document": filename,
                "section": section,
                "name": f"{filename} - '{section}'",
            }
    return records


def register_doc_evidence(
    bundle: BusinessTopologyEvidenceBundle,
    docs_dir: Path,
    *,
    filenames: tuple[str, ...] | None = None,
) -> tuple[BusinessTopologyEvidenceBundle, dict[str, dict[str, object]]]:
    """Return a bundle copy that also knows the documentation tier.

    The bundle is a frozen dataclass, so this replaces rather than mutates --
    callers that must keep the scanner-only view (the deterministic overlay's
    own validation pass) simply keep using the original.
    """

    records = collect_doc_evidence(docs_dir, filenames=filenames)
    if not records:
        return bundle, records
    merged_records = {**bundle.evidence_records, **records}
    merged_types = {
        **bundle.evidence_types,
        **{key: DOC_EVIDENCE_TYPE for key in records},
    }
    extended = dataclasses.replace(
        bundle,
        evidence_ids=frozenset(bundle.evidence_ids | set(records)),
        evidence_types=merged_types,
        evidence_records=merged_records,
    )
    return extended, records


def resolve_doc_citation(
    citation: str,
    doc_records: dict[str, dict[str, object]],
) -> list[str]:
    """Map a free-text doc citation onto registered ``doc:`` evidence ids.

    Stage A cites the way a human would ("03_DATA_FLOWS_AND_LINEAGE.md -
    'Maximo-to-AssetCheck Flows'"). Matching is by containment of the recorded
    section title in the citation text, which is safe here because the
    candidate set is only this run's own headings -- unlike design names, two
    headings that differ by one character are not two different systems.
    Returns the document-level id when no section matches, so a flow is never
    dropped for a paraphrased heading.
    """

    text = (citation or "").strip()
    if not text:
        return []
    lowered = text.casefold()

    named_documents = {
        str(record.get("document") or "")
        for record in doc_records.values()
        if str(record.get("document") or "")
        and str(record.get("document") or "").casefold() in lowered
    }

    section_hits: list[tuple[int, str]] = []
    for evidence_id, record in doc_records.items():
        document = str(record.get("document") or "")
        section = str(record.get("section") or "")
        if not section:
            continue
        # A section is only a candidate when its OWN document is named in the
        # citation. Without this scoping, a short heading such as "Systems" in
        # 01_ARCHITECTURE_MAP.md would attach that document's evidence to a
        # citation that only ever mentioned 03_DATA_FLOWS_AND_LINEAGE.md.
        if named_documents and document not in named_documents:
            continue
        if section.casefold() in lowered:
            # Longest heading wins: "Work Order Flow" must not shadow
            # "Work Order Flow - Error Handling" when both are present.
            section_hits.append((len(section), evidence_id))
    if section_hits:
        section_hits.sort(reverse=True)
        return [evidence_id for _, evidence_id in section_hits[:3]]
    return [doc_evidence_id(document, "") for document in sorted(named_documents)]


def build_design_artifact_index(
    bundle: BusinessTopologyEvidenceBundle,
) -> dict[str, str]:
    """Return ``{design name: artifact evidence id}`` for exact-name lookup.

    Keyed on ``name``, ``job_id`` and ``source_design`` because Talend's display
    name, canonical job id and design filename diverge (verified: 79 of
    werkorder's 429 job rows have a ``job_id`` that differs from the name TMC
    schedules under). Case-folded, since doc prose does not preserve case
    reliably -- but never truncated, stemmed or prefix-matched.

    A name claimed by two artifacts is dropped rather than guessed: a wrong
    artifact wiring is worse than an unwired flow, because it publishes a
    drill-down that leads to the wrong job.
    """

    candidates: dict[str, set[str]] = {}
    for artifact_id in bundle.artifact_ids:
        record = bundle.evidence_records.get(artifact_id) or {}
        for key in ("name", "job_id", "source_design"):
            value = str(record.get(key) or "").strip()
            if not value:
                continue
            candidates.setdefault(value.casefold(), set()).add(artifact_id)
    return {
        name: next(iter(ids))
        for name, ids in candidates.items()
        if len(ids) == 1
    }


def resolve_designs(
    design_names: list[str],
    index: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Resolve design names to artifact ids.

    Returns ``(artifact_ids, unresolved_names)``. Order is preserved and
    duplicates removed so the resulting citation list is deterministic.
    """

    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for name in design_names:
        artifact_id = index.get(name.strip().casefold())
        if artifact_id is None:
            unresolved.append(name)
            continue
        if artifact_id not in seen:
            seen.add(artifact_id)
            resolved.append(artifact_id)
    return resolved, unresolved


def primary_document_id(doc_records: dict[str, dict[str, object]]) -> str | None:
    """Return the whole-document evidence id of the best-ranked document.

    Used as the fallback citation for a claim whose own citation did not
    resolve. Derived from what is actually registered -- naming a specific
    document here would reintroduce the fixed-catalog assumption.
    """

    ranked: list[tuple[int, str, str]] = []
    for evidence_id, record in doc_records.items():
        if str(record.get("section") or ""):
            continue
        document = str(record.get("document") or "")
        if not document:
            continue
        upper = document.upper()
        rank = next(
            (rank for keyword, rank in _DOC_PRIORITY_KEYWORDS if keyword in upper),
            len(_DOC_PRIORITY_KEYWORDS),
        )
        ranked.append((rank, document, evidence_id))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][2]
