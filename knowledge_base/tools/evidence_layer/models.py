"""Data model for the field-level evidence layer.

One principle: capture the complete primitive, do not curate answers.

Every field of every artifact enters the layer -- resolved, or explicitly
recorded as unresolved with a reason. Nothing is dropped silently. Questions
("does this interface carry asset identification?", "what does this system
emit before mapping?", "which artifacts contain business logic?") are then
filters over the primitive, not features that have to be built one by one.

Mirrors the conventions already established by ``intodoc/src/intodoc/lineage.py``:
deterministic, no LLM, confidence-tiered, never silently dropping an
unresolved reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Controlled vocabularies. Kept as module constants (not free strings) so a
# typo fails loudly and so consumers can enumerate the legal values.
# ---------------------------------------------------------------------------

class Stage:
    """Where in a hop a field was observed.

    The three-way split is the load-bearing part of this model. A single-stage
    field inventory is not merely incomplete, it is misleading: on the verified
    Maximo->4PS work-order interface the mapping PRODUCES 12 fields and the
    target actually RECEIVES 5, out of ~400 offered by the source. "Which
    fields are integrated to the target" has a different correct answer at
    each stage, so the stage must be carried, not collapsed.
    """

    OFFERED = "offered"    # the source system put it on the wire
    MAPPED = "mapped"      # a transformation produced it
    WRITTEN = "written"    # the target actually receives it
    ALL = (OFFERED, MAPPED, WRITTEN)


class Direction:
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"
    UNKNOWN = "unknown"
    ALL = (INBOUND, OUTBOUND, INTERNAL, UNKNOWN)


class Confidence:
    """Same tiers as the scanner lineage pass, for the same reason."""

    CERTAIN = "certain"        # read directly out of a declaration
    LIKELY = "likely"          # single unambiguous resolution step
    INFERRED = "inferred"      # cross-artifact or heuristic resolution
    UNRESOLVED = "unresolved"  # recorded on purpose, never dropped
    ALL = (CERTAIN, LIKELY, INFERRED, UNRESOLVED)


class TransformKind:
    DIRECT = "direct"            # target <- source, no change
    EXPRESSION = "expression"    # computed from one or more sources
    CONSTANT = "constant"        # literal, no source
    CONCAT = "concat"            # several sources folded into one field
    CONDITIONAL = "conditional"  # choose/when/ternary over source values
    LOOKUP = "lookup"            # resolved via a join or reference table
    SPLIT = "split"              # one source fanned out to several targets
    DECLARED = "declared"        # a schema declaration, no mapping seen
    UNKNOWN = "unknown"
    ALL = (DIRECT, EXPRESSION, CONSTANT, CONCAT, CONDITIONAL, LOOKUP, SPLIT,
           DECLARED, UNKNOWN)


class RuleClass:
    """Does a transformation encode a business decision or plumbing?

    This is the axis that lets "which artifacts contain business-level
    mappings?" be answered at all -- it is derived from deterministic signals
    over the expression, and every verdict carries the signals that produced
    it so a reviewer can disagree with the rule rather than with a black box.
    """

    BUSINESS = "business"        # a domain decision lives here
    TECHNICAL = "technical"      # format, unit, encoding, envelope, transport
    WORKAROUND = "workaround"    # historical accident; do not rebuild
    NONE = "none"                # no transformation to classify
    ALL = (BUSINESS, TECHNICAL, WORKAROUND, NONE)


UNCATEGORISED = "uncategorised"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _tup(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class FieldRecord:
    """One field, at one stage, on one hop of one interface.

    This is the central relation. Everything else in the layer either feeds it
    or indexes it.
    """

    # --- identity -------------------------------------------------------
    interface_id: str          # stable id of the hop (see capture.artifacts)
    artifact_id: str           # the job/route/mapping that carries it
    direction: str             # Direction.*
    stage: str                 # Stage.*
    field_path: str            # "No" | "WORKORDER/WONUM" | "/json/no"

    # --- content --------------------------------------------------------
    field_type: str = ""
    source_expr: str = ""
    source_fields: tuple[str, ...] = ()
    transform_kind: str = TransformKind.UNKNOWN

    # --- derived (filled by derive/) ------------------------------------
    category: str = UNCATEGORISED
    category_confidence: str = Confidence.UNRESOLVED
    category_signals: tuple[str, ...] = ()
    rule_class: str = RuleClass.NONE
    rule_signals: tuple[str, ...] = ()

    # --- provenance -----------------------------------------------------
    evidence_file: str = ""
    evidence_locator: str = ""   # component unique name / template / xpath
    confidence: str = Confidence.CERTAIN
    notes: str = ""

    # Stable, total sort key. Deliberately NOT a casefold-only key: on real
    # data (`VISE2AssetCheck` vs `Vise2AssetCheck`) casefold-equal values kept
    # set-iteration order and the output stopped being byte-stable across
    # processes. Sort on the exact string as the final tiebreak.
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.interface_id, self.artifact_id, self.direction, self.stage,
            self.field_path.casefold(), self.field_path,
            self.evidence_locator, self.source_expr,
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, tuple):
                d[k] = list(v)
        return d


@dataclass(frozen=True)
class PayloadFieldRecord:
    """Census of a real message population -- never the raw payload.

    Answers "what does this application actually emit, before any mapping?"
    and, joined against the declared contract, "which declared fields are
    never populated?". A census rather than a copy keeps both the volume and
    the personal-data exposure bounded.
    """

    source_id: str              # census scope, e.g. "PathRef/Ortec/gepland_direct"
    message_root: str           # root element / envelope name
    field_path: str
    messages_seen: int
    messages_populated: int
    value_domain: tuple[str, ...] = ()   # bounded distinct values, if small
    value_domain_truncated: bool = False
    evidence_file: str = ""

    def sort_key(self) -> tuple[str, ...]:
        return (self.source_id, self.message_root,
                self.field_path.casefold(), self.field_path)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["value_domain"] = list(self.value_domain)
        return d


@dataclass(frozen=True)
class ArtifactRecord:
    """An addressable unit of work, plus how it connects."""

    artifact_id: str
    name: str
    kind: str                    # job | route | joblet | routelet | mapping | register
    group: str = ""
    source_root: str = ""
    path: str = ""
    purpose: str = ""
    component_types: tuple[str, ...] = ()
    external_systems: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()          # downstream artifact ids
    called_by: tuple[str, ...] = ()      # upstream artifact ids
    mapping_refs: tuple[str, ...] = ()   # XSL/XSLT/XSD it uses
    name_resolved: bool = True
    notes: str = ""

    def sort_key(self) -> tuple[str, ...]:
        return (self.kind, self.artifact_id)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, tuple):
                d[k] = list(v)
        return d


@dataclass(frozen=True)
class InterfaceRecord:
    """A directed hop between two systems.

    Sourced from whatever declares it -- a configuration register when one
    exists (authoritative for routing), otherwise inferred from artifact
    endpoints and channels. The two are distinguishable via ``origin`` so a
    consumer never has to guess which it is looking at.
    """

    interface_id: str
    source_system: str
    target_system: str
    origin: str                   # register | artifact | inferred
    mechanism: str = ""           # jms | http | soap | file | db | ftp | unknown
    mapping_ref: str = ""
    artifact_ids: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    endpoint: str = ""
    label: str = ""
    confidence: str = Confidence.CERTAIN
    notes: str = ""

    def sort_key(self) -> tuple[str, ...]:
        return (self.source_system.casefold(), self.target_system.casefold(),
                self.interface_id)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, tuple):
                d[k] = list(v)
        return d


@dataclass
class Unresolved:
    """Something the layer could not resolve, kept as data.

    A gap that is recorded is a work item; a gap that is dropped is a wrong
    answer waiting to happen. The scanner's four silent coverage gaps are
    exactly what this field exists to prevent repeating.
    """

    kind: str          # missing_mapping_file | unparsed_expression | unnamed_artifact | ...
    subject: str
    reason: str
    evidence_file: str = ""

    def sort_key(self) -> tuple[str, ...]:
        return (self.kind, self.subject, self.reason)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceLayer:
    """Everything the capture and derive passes produced, in one object."""

    fields: list[FieldRecord] = field(default_factory=list)
    payload_fields: list[PayloadFieldRecord] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    interfaces: list[InterfaceRecord] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def sort(self) -> None:
        """Total, deterministic order. Byte-stability is a requirement, not a
        nicety: these artifacts are diffed between scans and fed into
        prompt-cached contexts."""
        self.fields.sort(key=FieldRecord.sort_key)
        self.payload_fields.sort(key=PayloadFieldRecord.sort_key)
        self.artifacts.sort(key=ArtifactRecord.sort_key)
        self.interfaces.sort(key=InterfaceRecord.sort_key)
        self.unresolved.sort(key=Unresolved.sort_key)
