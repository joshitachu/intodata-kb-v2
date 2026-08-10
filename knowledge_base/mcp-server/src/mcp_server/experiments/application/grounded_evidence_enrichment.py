"""Closed contracts for additive Grounded project-evidence enrichment.

Scanner-derived ``FACT_LEDGER.json`` remains the only implemented-truth
authority.  This module exposes prepared, approved non-scanner evidence to the
investigation roles and validates the summaries handed to later roles.  Raw
source bytes and snapshot persistence stay outside this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from mcp_server.experiments.domain.project_evidence import ProjectEvidenceSnapshot


PROJECT_EVIDENCE_INDEX_NAME = "PROJECT_EVIDENCE_INDEX.json"
PROJECT_EVIDENCE_HANDOFF_NAME = "PROJECT_EVIDENCE_HANDOFF.json"
PROJECT_EVIDENCE_ITEM_ROOT = "project_evidence/items"

# Staged supplemental evidence must carry a file suffix that a line-based text
# reader will decode. Prepared supplemental representations are always redacted
# *extracted text* -- the snapshot pipeline turns OOXML/PDF/etc into text before
# staging (see prepare_supporting_evidence) -- yet they used to be staged under
# ``.bin``. The OpenCode built-in ``read`` tool (the staged grounded runtime's
# only evidence reader) refuses a ``.bin`` file as binary and returns no
# content, silently stripping every supplemental fact from the investigation
# roles. Naming the staged file after its declared text media type restores
# content-level reads. The suffix is a pure function of the declared media type
# so it can be recomputed identically during index validation.
_TEXT_EVIDENCE_SUFFIXES = {
    "application/json": ".json",
    "text/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/html": ".html",
    "application/x-yaml": ".yaml",
    "application/yaml": ".yaml",
    "text/yaml": ".yaml",
}


def evidence_relative_suffix(media_type: str) -> str:
    """Choose a text-reader-friendly staged-file suffix from a declared type."""

    normalized = media_type.split(";", 1)[0].strip().casefold()
    if normalized in _TEXT_EVIDENCE_SUFFIXES:
        return _TEXT_EVIDENCE_SUFFIXES[normalized]
    if normalized.endswith("+json"):
        return ".json"
    if normalized.endswith("+xml"):
        return ".xml"
    # Every prepared supplemental representation is decodable text; default to a
    # plain-text suffix so the reader never treats it as opaque bytes.
    return ".txt"


_INDEX_KEYS = frozenset(
    {
        "schema_version",
        "snapshot",
        "authority",
        "declared_sources",
        "assigned_evidence",
    }
)
_SNAPSHOT_KEYS = frozenset({"id", "fingerprint_sha256"})
_AUTHORITY_KEYS = frozenset(
    {"implemented_truth", "supplemental_policy", "missing_evidence_policy"}
)
_INDEX_ITEM_KEYS = frozenset(
    {
        "evidence_id",
        "claim_class",
        "source_descriptor_id",
        "logical_identity",
        "media_type",
        "sha256",
        "size_bytes",
        "relative_path",
    }
)
_DECLARED_SOURCE_KEYS = frozenset(
    {
        "source_descriptor_id",
        "source_kind",
        "source_role",
        "status",
        "status_reason",
        "redaction_status",
        "trust_level",
        "prepared_evidence_ids",
        "assigned_evidence_ids",
    }
)
_PACKET_KEYS = frozenset(
    {
        "schema_version",
        "coverage",
        "claims",
        "agreements",
        "conflicts",
        "unknowns",
    }
)
_COVERAGE_KEYS = frozenset(
    {
        "declared_source_ids",
        "unavailable_source_ids",
        "assigned_evidence_ids",
        "examined_evidence_ids",
        "missing_evidence_ids",
    }
)
_CLAIM_KEYS = frozenset(
    {"claim_id", "claim_class", "statement", "evidence_ids", "fact_ids"}
)
_RELATION_KEYS = frozenset({"summary", "evidence_ids", "fact_ids"})
_CONFLICT_KEYS = frozenset({"summary", "evidence_ids", "fact_ids", "resolution"})
_UNKNOWN_KEYS = frozenset({"summary", "evidence_ids", "source_descriptor_ids"})
_CLAIM_CLASSES = frozenset({"implemented", "observed_runtime", "intended_documented"})
_CONFLICT_RESOLUTIONS = frozenset({"scanner_authoritative", "unresolved"})
_ANALYSIS_SECTIONS = (
    "Implemented Truth",
    "Observed Runtime",
    "Intended or Documented",
    "Agreements",
    "Conflicts",
    "Unknowns",
)
_SUPPLEMENTAL_DOCUMENT_SECTIONS = frozenset(
    {"Supplemental Evidence", "Evidence Discrepancies"}
)
_EVIDENCE_CITATION = re.compile(r"\[evidence:([^\]\s]+)\]")


@dataclass(frozen=True, slots=True)
class GroundedSupplementalEvidence:
    evidence_id: str
    claim_class: str
    source_descriptor_id: str
    logical_identity: str
    media_type: str
    sha256: str
    size_bytes: int
    content: bytes

    @property
    def relative_path(self) -> str:
        source_namespace = hashlib.sha256(
            self.source_descriptor_id.encode("utf-8")
        ).hexdigest()
        return (
            f"{PROJECT_EVIDENCE_ITEM_ROOT}/{source_namespace}/"
            f"{self.evidence_id}{evidence_relative_suffix(self.media_type)}"
        )

    def index_entry(self) -> dict[str, object]:
        return {
            "claim_class": self.claim_class,
            "evidence_id": self.evidence_id,
            "logical_identity": self.logical_identity,
            "media_type": self.media_type,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_descriptor_id": self.source_descriptor_id,
        }


def supplemental_project_evidence(
    snapshot: ProjectEvidenceSnapshot,
) -> tuple[GroundedSupplementalEvidence, ...]:
    """Return approved non-scanner prepared items in stable evidence-ID order.

    Implemented scanner material is already present in the frozen source packet
    and ``FACT_LEDGER.json``.  Omitting it here prevents a second full-project
    read and, importantly, prevents a supplemental copy from competing with the
    deterministic ledger.
    """

    content_by_id = {item.item_id: item for item in snapshot.prepared_content_items}
    items: list[GroundedSupplementalEvidence] = []
    for source in snapshot.sources:
        claim_class = _source_claim_class(source.source_kind, source.source_role)
        if claim_class == "implemented":
            continue
        for prepared in source.prepared_representations:
            item_id = _nonblank(prepared.get("item_id"), "prepared evidence id")
            evidence_id = hashlib.sha256(item_id.encode("utf-8")).hexdigest()
            content = content_by_id[item_id]
            items.append(
                GroundedSupplementalEvidence(
                    evidence_id=evidence_id,
                    claim_class=claim_class,
                    source_descriptor_id=source.descriptor_id,
                    logical_identity=_nonblank(
                        prepared.get("logical_identity"),
                        "prepared evidence logical identity",
                    ),
                    media_type=content.media_type,
                    sha256=content.extracted_sha256,
                    size_bytes=content.size_bytes,
                    content=content.extracted_bytes,
                )
            )
    return tuple(sorted(items, key=lambda item: item.evidence_id))


def has_supplemental_project_evidence(snapshot: ProjectEvidenceSnapshot) -> bool:
    """Return whether the declared inventory includes any non-scanner source."""

    return any(
        _source_claim_class(source.source_kind, source.source_role) != "implemented"
        for source in snapshot.sources
    )


def project_evidence_index(
    snapshot: ProjectEvidenceSnapshot,
    assigned: tuple[GroundedSupplementalEvidence, ...],
) -> dict[str, object]:
    """Build the exact role-local investigation contract."""

    assigned_ids = {item.evidence_id for item in assigned}
    declared_sources = []
    for source in snapshot.sources:
        prepared_ids = sorted(
            hashlib.sha256(str(item["item_id"]).encode("utf-8")).hexdigest()
            for item in source.prepared_representations
        )
        declared_sources.append(
            {
                "assigned_evidence_ids": [
                    evidence_id for evidence_id in prepared_ids if evidence_id in assigned_ids
                ],
                "prepared_evidence_ids": prepared_ids,
                "redaction_status": source.redaction_status,
                "source_descriptor_id": source.descriptor_id,
                "source_kind": source.source_kind,
                "source_role": source.source_role,
                "status": source.status.value,
                "status_reason": source.status_reason,
                "trust_level": source.trust_level,
            }
        )
    declared_sources.sort(key=lambda item: str(item["source_descriptor_id"]))
    return {
        "assigned_evidence": [item.index_entry() for item in assigned],
        "authority": {
            "implemented_truth": "input/FACT_LEDGER.json",
            "missing_evidence_policy": "record_explicitly",
            "supplemental_policy": "additive_never_override",
        },
        "declared_sources": declared_sources,
        "schema_version": 1,
        "snapshot": {
            "fingerprint_sha256": snapshot.fingerprint_sha256,
            "id": snapshot.id,
        },
    }


def project_evidence_handoff(snapshot: ProjectEvidenceSnapshot) -> dict[str, object]:
    """Build the metadata-only marker used by downstream accepted-artifact roles."""

    return {
        "fingerprint_sha256": snapshot.fingerprint_sha256,
        "policy": "accepted_upstream_artifacts_only",
        "schema_version": 1,
        "snapshot_id": snapshot.id,
    }


def validate_project_evidence_index(value: object) -> dict[str, object]:
    """Validate and return a defensive copy of a closed role-local index."""

    data = _mapping(value, "project evidence index")
    _exact_keys(data, _INDEX_KEYS, "project evidence index")
    if data.get("schema_version") != 1:
        raise ValueError("project evidence index schema version is unsupported")
    snapshot = _mapping(data.get("snapshot"), "project evidence snapshot identity")
    _exact_keys(snapshot, _SNAPSHOT_KEYS, "project evidence snapshot identity")
    _nonblank(snapshot.get("id"), "project evidence snapshot id")
    _sha(snapshot.get("fingerprint_sha256"), "project evidence snapshot fingerprint")
    authority = _mapping(data.get("authority"), "project evidence authority")
    _exact_keys(authority, _AUTHORITY_KEYS, "project evidence authority")
    if authority != {
        "implemented_truth": "input/FACT_LEDGER.json",
        "missing_evidence_policy": "record_explicitly",
        "supplemental_policy": "additive_never_override",
    }:
        raise ValueError("project evidence authority contract is invalid")
    assigned = _object_list(data.get("assigned_evidence"), "assigned evidence")
    seen_ids: set[str] = set()
    seen_identities: set[str] = set()
    normalized: list[dict[str, object]] = []
    for raw in assigned:
        _exact_keys(raw, _INDEX_ITEM_KEYS, "assigned evidence item")
        evidence_id = _sha(raw.get("evidence_id"), "evidence id")
        logical_identity = _nonblank(raw.get("logical_identity"), "evidence logical identity")
        if evidence_id in seen_ids or logical_identity in seen_identities:
            raise ValueError("assigned project evidence identities must be unique")
        seen_ids.add(evidence_id)
        seen_identities.add(logical_identity)
        claim_class = _nonblank(raw.get("claim_class"), "evidence claim class")
        if claim_class not in _CLAIM_CLASSES - {"implemented"}:
            raise ValueError("supplemental evidence cannot be classified as implemented truth")
        relative_path = _nonblank(raw.get("relative_path"), "evidence relative path")
        media_type = _nonblank(raw.get("media_type"), "evidence media type")
        source_descriptor_id = _nonblank(
            raw.get("source_descriptor_id"), "source descriptor id"
        )
        source_namespace = hashlib.sha256(source_descriptor_id.encode("utf-8")).hexdigest()
        if relative_path != (
            f"{PROJECT_EVIDENCE_ITEM_ROOT}/{source_namespace}/{evidence_id}"
            f"{evidence_relative_suffix(media_type)}"
        ):
            raise ValueError("supplemental evidence path differs from its evidence id")
        normalized.append(
            {
                "claim_class": claim_class,
                "evidence_id": evidence_id,
                "logical_identity": logical_identity,
                "media_type": media_type,
                "relative_path": relative_path,
                "sha256": _sha(raw.get("sha256"), "evidence sha256"),
                "size_bytes": _nonnegative_int(raw.get("size_bytes"), "evidence size"),
                "source_descriptor_id": source_descriptor_id,
            }
        )
    if normalized != sorted(normalized, key=lambda item: str(item["evidence_id"])):
        raise ValueError("assigned project evidence must be sorted by evidence id")
    normalized_sources: list[dict[str, object]] = []
    seen_source_ids: set[str] = set()
    for raw in _object_list(data.get("declared_sources"), "declared project evidence sources"):
        _exact_keys(raw, _DECLARED_SOURCE_KEYS, "declared project evidence source")
        descriptor_id = _nonblank(raw.get("source_descriptor_id"), "source descriptor id")
        if descriptor_id in seen_source_ids:
            raise ValueError("declared project evidence source ids must be unique")
        seen_source_ids.add(descriptor_id)
        status = _nonblank(raw.get("status"), "declared source status")
        if status not in {"readable", "unreadable", "unsupported"}:
            raise ValueError("declared project evidence source status is invalid")
        status_reason = raw.get("status_reason")
        if status == "readable" and status_reason is not None:
            raise ValueError("readable project evidence source cannot have a failure reason")
        if status != "readable" and (
            not isinstance(status_reason, str) or not status_reason.strip()
        ):
            raise ValueError("unavailable project evidence source must have a failure reason")
        prepared_ids = _unique_strings(
            raw.get("prepared_evidence_ids"), "prepared source evidence ids"
        )
        assigned_ids = _unique_strings(
            raw.get("assigned_evidence_ids"), "assigned source evidence ids"
        )
        if not set(assigned_ids).issubset(set(prepared_ids)):
            raise ValueError("assigned source evidence differs from declared prepared evidence")
        if not set(assigned_ids).issubset(seen_ids):
            raise ValueError("declared source assigns evidence absent from the role index")
        if status != "readable" and prepared_ids:
            raise ValueError("unavailable project evidence source cannot expose prepared evidence")
        normalized_sources.append(
            {
                "assigned_evidence_ids": list(assigned_ids),
                "prepared_evidence_ids": list(prepared_ids),
                "redaction_status": _nonblank(
                    raw.get("redaction_status"), "source redaction status"
                ),
                "source_descriptor_id": descriptor_id,
                "source_kind": _nonblank(raw.get("source_kind"), "source kind"),
                "source_role": _nonblank(raw.get("source_role"), "source role"),
                "status": status,
                "status_reason": status_reason,
                "trust_level": _nonblank(raw.get("trust_level"), "source trust level"),
            }
        )
    if normalized_sources != sorted(
        normalized_sources, key=lambda item: str(item["source_descriptor_id"])
    ):
        raise ValueError("declared project evidence sources must be sorted")
    source_assigned_ids = {
        evidence_id
        for source in normalized_sources
        for evidence_id in source["assigned_evidence_ids"]
        if isinstance(evidence_id, str)
    }
    if source_assigned_ids != seen_ids:
        raise ValueError("declared source coverage differs from assigned evidence")
    return {
        "assigned_evidence": normalized,
        "authority": dict(authority),
        "declared_sources": normalized_sources,
        "schema_version": 1,
        "snapshot": dict(snapshot),
    }


def validate_investigation_packets(
    artifacts: Mapping[str, object],
    *,
    index: object,
    fact_ledger: Mapping[str, object],
) -> dict[str, object]:
    """Validate complete Phase 1 coverage and closed, typed evidence claims."""

    normalized_index = validate_project_evidence_index(index)
    assigned_entries = normalized_index["assigned_evidence"]
    if not isinstance(assigned_entries, list):  # kept explicit for type narrowing
        raise ValueError("assigned evidence must be a list")
    assigned_classes = {
        str(item["evidence_id"]): str(item["claim_class"])
        for item in assigned_entries
        if isinstance(item, dict)
    }
    declared_sources = normalized_index["declared_sources"]
    if not isinstance(declared_sources, list):
        raise ValueError("declared project evidence sources must be a list")
    declared_source_ids = {
        str(item["source_descriptor_id"])
        for item in declared_sources
        if isinstance(item, dict)
    }
    unavailable_source_ids = {
        str(item["source_descriptor_id"])
        for item in declared_sources
        if isinstance(item, dict) and item.get("status") != "readable"
    }
    fact_ids = {
        str(item["fact_id"])
        for item in fact_ledger.get("protected_facts", [])
        if isinstance(item, dict) and item.get("fact_id")
    }
    covered: set[str] = set()
    examined_all: set[str] = set()
    missing_all: set[str] = set()
    claims: list[dict[str, object]] = []
    agreements: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    unknowns: list[dict[str, object]] = []
    claim_ids: set[str] = set()
    for artifact, value in sorted(artifacts.items()):
        packet = _mapping(value, f"investigation packet {artifact}")
        _required_keys(packet, _PACKET_KEYS, "investigation packet")
        if packet.get("schema_version") != 1:
            raise ValueError("investigation packet schema version is unsupported")
        coverage = _mapping(packet.get("coverage"), "investigation coverage")
        _required_keys(coverage, _COVERAGE_KEYS, "investigation coverage")
        packet_assigned = set(
            _unique_strings(coverage.get("assigned_evidence_ids"), "assigned evidence ids")
        )
        packet_sources = set(
            _unique_strings(coverage.get("declared_source_ids"), "declared source ids")
        )
        packet_unavailable = set(
            _unique_strings(coverage.get("unavailable_source_ids"), "unavailable source ids")
        )
        if packet_sources != declared_source_ids or packet_unavailable != unavailable_source_ids:
            raise ValueError("investigation source coverage differs from the declared inventory")
        examined = set(
            _unique_strings(coverage.get("examined_evidence_ids"), "examined evidence ids")
        )
        missing = set(
            _unique_strings(coverage.get("missing_evidence_ids"), "missing evidence ids")
        )
        if examined & missing or examined | missing != packet_assigned:
            raise ValueError("investigation coverage must explicitly classify every assignment")
        if covered & packet_assigned or not packet_assigned.issubset(assigned_classes):
            raise ValueError("investigation packet coverage is duplicate or out of scope")
        covered.update(packet_assigned)
        examined_all.update(examined)
        missing_all.update(missing)
        for raw in _object_list(packet.get("claims"), "investigation claims"):
            _required_keys(raw, _CLAIM_KEYS, "investigation claim")
            claim_id = _nonblank(raw.get("claim_id"), "claim id")
            if claim_id in claim_ids:
                raise ValueError("investigation claim ids must be unique")
            claim_ids.add(claim_id)
            claim_class = _nonblank(raw.get("claim_class"), "claim class")
            if claim_class not in _CLAIM_CLASSES:
                raise ValueError("investigation claim class is invalid")
            evidence_ids = _unique_strings(raw.get("evidence_ids"), "claim evidence ids")
            referenced_facts = _unique_strings(raw.get("fact_ids"), "claim fact ids")
            if not evidence_ids and not referenced_facts:
                raise ValueError("investigation claim must cite evidence or a scanner fact")
            if not set(evidence_ids).issubset(examined):
                raise ValueError("investigation claim cites unexamined evidence")
            if not set(referenced_facts).issubset(fact_ids):
                raise ValueError("investigation claim cites an unknown scanner fact")
            if claim_class == "implemented" and not referenced_facts:
                raise ValueError("implemented claims must cite authoritative scanner facts")
            if claim_class == "implemented" and evidence_ids:
                raise ValueError("implemented claims cannot be sourced from supplemental evidence")
            if claim_class != "implemented" and referenced_facts:
                raise ValueError("supplemental claims cannot restate protected scanner facts")
            if claim_class != "implemented" and (
                not evidence_ids
                or any(assigned_classes[value] != claim_class for value in evidence_ids)
            ):
                raise ValueError("supplemental claim classification differs from its source")
            claims.append(
                {
                    "claim_class": claim_class,
                    "claim_id": claim_id,
                    "evidence_ids": list(evidence_ids),
                    "fact_ids": list(referenced_facts),
                    "statement": _nonblank(raw.get("statement"), "claim statement"),
                }
            )
        agreements.extend(
            _validate_relations(
                packet.get("agreements"),
                label="agreement",
                allowed_evidence=examined,
                allowed_facts=fact_ids,
            )
        )
        conflicts.extend(
            _validate_conflicts(
                packet.get("conflicts"),
                allowed_evidence=examined,
                allowed_facts=fact_ids,
            )
        )
        unknowns.extend(
            _validate_unknowns(
                packet.get("unknowns"),
                allowed_evidence=packet_assigned,
                allowed_sources=declared_source_ids,
            )
        )
    if covered != set(assigned_classes):
        raise ValueError("investigation packets do not cover every assigned evidence item")
    disclosed_unavailable = {
        source_id
        for item in unknowns
        for source_id in item["source_descriptor_ids"]
        if isinstance(source_id, str)
    }
    if not unavailable_source_ids.issubset(disclosed_unavailable):
        raise ValueError("investigation unknowns omit unavailable declared sources")
    return {
        "agreements": agreements,
        "claims": claims,
        "conflicts": conflicts,
        "examined_evidence_ids": sorted(examined_all),
        "missing_evidence_ids": sorted(missing_all),
        "unknowns": unknowns,
    }


def validate_analysis_enrichment(
    artifacts: Mapping[str, str],
    *,
    investigation_packets: Mapping[str, object],
) -> None:
    """Require Phase 2 to separate evidence classes and reconcile conflicts."""

    if not investigation_packets:
        return
    summary = _validated_packet_summary_without_index(investigation_packets)
    known_ids = summary["known_evidence_ids"]
    for artifact, text in artifacts.items():
        sections = _markdown_sections(text)
        missing_sections = [name for name in _ANALYSIS_SECTIONS if not sections.get(name)]
        if missing_sections:
            raise ValueError(
                f"enriched analysis {artifact} is missing required sections: {missing_sections}"
            )
        cited = set(_EVIDENCE_CITATION.findall(text))
        if not cited.issubset(known_ids):
            raise ValueError("enriched analysis cites unknown supplemental evidence")
        for claim in summary["claims"]:
            if not isinstance(claim, dict):
                continue
            section_name = {
                "implemented": "Implemented Truth",
                "observed_runtime": "Observed Runtime",
                "intended_documented": "Intended or Documented",
            }[str(claim["claim_class"])]
            _require_references(
                sections[section_name],
                tuple(str(value) for value in claim["evidence_ids"]),
                f"{section_name} analysis",
            )
            for fact_id in claim["fact_ids"]:
                if f"[fact:{fact_id}]" not in sections[section_name]:
                    raise ValueError("implemented analysis omits its scanner fact citation")
        for relation in summary["agreements"]:
            _require_references(
                sections["Agreements"],
                tuple(str(value) for value in relation["evidence_ids"]),
                "agreement analysis",
            )
        for conflict in summary["conflicts"]:
            _require_references(
                sections["Conflicts"],
                tuple(str(value) for value in conflict["evidence_ids"]),
                "conflict analysis",
            )
            if conflict["fact_ids"] and "scanner authoritative" not in sections[
                "Conflicts"
            ].casefold():
                raise ValueError("scanner conflict analysis must preserve scanner authority")
        _require_references(
            sections["Unknowns"],
            tuple(str(value) for value in summary["missing_evidence_ids"]),
            "unknown analysis",
        )
        for source_id in summary["unavailable_source_ids"]:
            if f"[source:{source_id}]" not in sections["Unknowns"]:
                raise ValueError("unknown analysis omits an unavailable source citation")


def validate_writing_enrichment(
    documents: Mapping[str, str],
    *,
    analyses: Mapping[str, str],
) -> None:
    """Allow only supported additive context and render known discrepancies."""

    if not analyses:
        return
    analysis_sections = [_markdown_sections(text) for text in analyses.values()]
    if not any(all(sections.get(name) for name in _ANALYSIS_SECTIONS) for sections in analysis_sections):
        return
    known_ids = {
        value for text in analyses.values() for value in _EVIDENCE_CITATION.findall(text)
    }
    document_citations = {
        value for text in documents.values() for value in _EVIDENCE_CITATION.findall(text)
    }
    if not document_citations.issubset(known_ids):
        raise ValueError("enriched document cites evidence absent from accepted analysis")
    for artifact, text in documents.items():
        sections = _markdown_sections(text)
        citations_outside_supplemental_sections = {
            value
            for name, section_text in sections.items()
            if name not in _SUPPLEMENTAL_DOCUMENT_SECTIONS
            for value in _EVIDENCE_CITATION.findall(section_text)
        }
        preamble = text.split("## ", 1)[0]
        citations_outside_supplemental_sections.update(
            _EVIDENCE_CITATION.findall(preamble)
        )
        if citations_outside_supplemental_sections:
            raise ValueError(
                "supplemental document citations must remain in additive evidence sections"
            )
        for section_name in _SUPPLEMENTAL_DOCUMENT_SECTIONS:
            section_text = sections.get(section_name, "")
            for block in _supplemental_assertion_blocks(section_text):
                if not _EVIDENCE_CITATION.search(block):
                    raise ValueError(
                        f"enriched document {artifact} has an uncited supplemental assertion"
                    )
    conflict_ids = {
        value
        for sections in analysis_sections
        for value in _EVIDENCE_CITATION.findall(sections.get("Conflicts", ""))
    }
    if conflict_ids:
        discrepancy_text = "\n".join(
            _markdown_sections(text).get("Evidence Discrepancies", "")
            for text in documents.values()
        )
        if not discrepancy_text.strip():
            raise ValueError("enriched documents must add an Evidence Discrepancies section")
        _require_references(
            discrepancy_text,
            tuple(sorted(conflict_ids)),
            "document discrepancy context",
        )


def investigation_contract_prompt() -> str:
    return (
        "Progressively list/search/read every item assigned by "
        "input/PROJECT_EVIDENCE_INDEX.json; do not re-read unassigned project sources. "
        "Every owned evidence-packet JSON must use exactly this schema: schema_version, "
        "coverage, claims, agreements, conflicts, unknowns. coverage must explicitly "
        "partition assigned_evidence_ids into examined_evidence_ids and "
        "missing_evidence_ids, list every declared_source_id and unavailable_source_id, "
        "and disclose unavailable sources in unknowns. Claims must classify implemented, "
        "observed_runtime, or "
        "intended_documented and cite evidence IDs and/or FACT_LEDGER protected fact IDs. "
        "FACT_LEDGER is authoritative implemented truth; supplemental evidence is additive."
    )


def analysis_contract_prompt() -> str:
    return (
        "Use the accepted investigation packets; do not re-read the entire project. "
        "Every owned analysis must contain non-empty sections: Implemented Truth, "
        "Observed Runtime, Intended or Documented, Agreements, Conflicts, Unknowns. "
        "Cite supplemental items exactly as [evidence:<id>] and scanner facts as "
        "[fact:<fact_id>]. Conflicts with scanner facts must say scanner authoritative."
    )


def writing_contract_prompt() -> str:
    return (
        "Preserve the recipe's existing document family, headings, and content requirements. "
        "Use accepted analyses instead of re-reading the project. Add business, runtime, or "
        "intended context only under a Supplemental Evidence or Evidence Discrepancies "
        "section, and cite every assertion paragraph, list item, or table row as "
        "[evidence:<id>]. Never rewrite FACT_LEDGER facts. "
        "When accepted analysis contains conflicts, add an Evidence Discrepancies section in "
        "a relevant document and render every cited conflict additively."
    )


def _source_claim_class(source_kind: str, source_role: str) -> str:
    kind = source_kind.casefold()
    role = source_role.casefold()
    if kind == "intodoc_scan":
        return "implemented"
    if kind == "runtime_export" or role in {"runtime", "runtime_observation"}:
        return "observed_runtime"
    return "intended_documented"


def _validated_packet_summary_without_index(
    packets: Mapping[str, object],
) -> dict[str, Any]:
    claims: list[dict[str, object]] = []
    agreements: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    missing: set[str] = set()
    known: set[str] = set()
    unavailable: set[str] = set()
    for artifact, value in packets.items():
        packet = _mapping(value, f"accepted investigation packet {artifact}")
        _required_keys(packet, _PACKET_KEYS, "accepted investigation packet")
        if packet.get("schema_version") != 1:
            raise ValueError("accepted investigation packet schema version is unsupported")
        coverage = _mapping(packet.get("coverage"), "accepted investigation coverage")
        _required_keys(coverage, _COVERAGE_KEYS, "accepted investigation coverage")
        assigned = set(
            _unique_strings(coverage.get("assigned_evidence_ids"), "assigned evidence ids")
        )
        declared_sources = set(
            _unique_strings(coverage.get("declared_source_ids"), "declared source ids")
        )
        unavailable_sources = set(
            _unique_strings(coverage.get("unavailable_source_ids"), "unavailable source ids")
        )
        if not unavailable_sources.issubset(declared_sources):
            raise ValueError("accepted investigation source coverage is inconsistent")
        examined = set(
            _unique_strings(coverage.get("examined_evidence_ids"), "examined evidence ids")
        )
        packet_missing = set(
            _unique_strings(coverage.get("missing_evidence_ids"), "missing evidence ids")
        )
        if examined & packet_missing or examined | packet_missing != assigned:
            raise ValueError("accepted investigation coverage is inconsistent")
        known.update(assigned)
        missing.update(packet_missing)
        unavailable.update(unavailable_sources)
        for raw in _object_list(packet.get("claims"), "accepted claims"):
            _required_keys(raw, _CLAIM_KEYS, "accepted claim")
            claims.append(dict(raw))
        for raw in _object_list(packet.get("agreements"), "accepted agreements"):
            _required_keys(raw, _RELATION_KEYS, "accepted agreement")
            agreements.append(dict(raw))
        for raw in _object_list(packet.get("conflicts"), "accepted conflicts"):
            _required_keys(raw, _CONFLICT_KEYS, "accepted conflict")
            conflicts.append(dict(raw))
        for raw in _object_list(packet.get("unknowns"), "accepted unknowns"):
            _required_keys(raw, _UNKNOWN_KEYS, "accepted unknown")
    return {
        "agreements": agreements,
        "claims": claims,
        "conflicts": conflicts,
        "known_evidence_ids": known,
        "missing_evidence_ids": missing,
        "unavailable_source_ids": unavailable,
    }


def _validate_relations(
    value: object,
    *,
    label: str,
    allowed_evidence: set[str],
    allowed_facts: set[str],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in _object_list(value, f"investigation {label}s"):
        _required_keys(raw, _RELATION_KEYS, f"investigation {label}")
        evidence_ids = _unique_strings(raw.get("evidence_ids"), f"{label} evidence ids")
        fact_ids = _unique_strings(raw.get("fact_ids"), f"{label} fact ids")
        if not evidence_ids or not set(evidence_ids).issubset(allowed_evidence):
            raise ValueError(f"investigation {label} must cite examined evidence")
        if not set(fact_ids).issubset(allowed_facts):
            raise ValueError(f"investigation {label} cites an unknown scanner fact")
        result.append(
            {
                "evidence_ids": list(evidence_ids),
                "fact_ids": list(fact_ids),
                "summary": _nonblank(raw.get("summary"), f"{label} summary"),
            }
        )
    return result


def _validate_conflicts(
    value: object,
    *,
    allowed_evidence: set[str],
    allowed_facts: set[str],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in _object_list(value, "investigation conflicts"):
        _required_keys(raw, _CONFLICT_KEYS, "investigation conflict")
        evidence_ids = _unique_strings(raw.get("evidence_ids"), "conflict evidence ids")
        fact_ids = _unique_strings(raw.get("fact_ids"), "conflict fact ids")
        if not evidence_ids or not set(evidence_ids).issubset(allowed_evidence):
            raise ValueError("investigation conflict must cite examined evidence")
        if not set(fact_ids).issubset(allowed_facts):
            raise ValueError("investigation conflict cites an unknown scanner fact")
        resolution = _nonblank(raw.get("resolution"), "conflict resolution")
        if resolution not in _CONFLICT_RESOLUTIONS:
            raise ValueError("investigation conflict resolution is invalid")
        if fact_ids and resolution != "scanner_authoritative":
            raise ValueError("supplemental evidence cannot override a protected scanner fact")
        result.append(
            {
                "evidence_ids": list(evidence_ids),
                "fact_ids": list(fact_ids),
                "resolution": resolution,
                "summary": _nonblank(raw.get("summary"), "conflict summary"),
            }
        )
    return result


def _validate_unknowns(
    value: object,
    *,
    allowed_evidence: set[str],
    allowed_sources: set[str],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in _object_list(value, "investigation unknowns"):
        _required_keys(raw, _UNKNOWN_KEYS, "investigation unknown")
        evidence_ids = _unique_strings(raw.get("evidence_ids"), "unknown evidence ids")
        source_ids = _unique_strings(
            raw.get("source_descriptor_ids"), "unknown source descriptor ids"
        )
        if not set(evidence_ids).issubset(allowed_evidence):
            raise ValueError("investigation unknown cites out-of-scope evidence")
        if not set(source_ids).issubset(allowed_sources):
            raise ValueError("investigation unknown cites an undeclared source")
        result.append(
            {
                "evidence_ids": list(evidence_ids),
                "source_descriptor_ids": list(source_ids),
                "summary": _nonblank(raw.get("summary"), "unknown summary"),
            }
        )
    return result


def _markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _supplemental_assertion_blocks(text: str) -> tuple[str, ...]:
    """Return prose/list/table assertions while ignoring Markdown structure."""

    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("\n".join(paragraph))
            paragraph.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if stripped.startswith("#") or re.fullmatch(r"\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?", stripped):
            flush_paragraph()
            continue
        if stripped.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)]\s", stripped):
            flush_paragraph()
            blocks.append(stripped)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            blocks.append(stripped)
            continue
        paragraph.append(stripped)
    flush_paragraph()
    return tuple(blocks)


def _require_references(text: str, values: tuple[str, ...], label: str) -> None:
    missing = [value for value in values if f"[evidence:{value}]" not in text]
    if missing:
        raise ValueError(f"{label} omits evidence citations: {missing}")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _object_list(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return tuple(dict(item) for item in value)


def _unique_strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result = tuple(_nonblank(item, label) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must be unique")
    return result


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unknown or missing fields")


def _required_keys(value: Mapping[str, object], required: frozenset[str], label: str) -> None:
    """Require every declared field, but tolerate model-added extra fields.

    Used only for LLM-authored packet content (investigation packets,
    coverage, claims, agreements, conflicts, unknowns). A model that adds a
    harmless self-descriptive field (e.g. ``packet_role``) alongside every
    required field is not a content defect and must not fail the role --
    only a genuinely missing required field is. ``_exact_keys`` remains used
    for Nexus-generated structures (the project evidence index and its
    sub-objects), where any deviation is a real internal bug, not model
    creativity.
    """
    missing = required - set(value)
    if missing:
        raise ValueError(f"{label} is missing required fields: {sorted(missing)}")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _sha(value: object, label: str) -> str:
    result = _nonblank(value, label)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value
