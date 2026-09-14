"""Provider-neutral durable knowledge for integration investigations.

The models in this module are deliberately independent of both coding-agent
providers and the legacy documentation pipeline.  They hold accepted,
sanitized knowledge; they do not hold prompts, sessions, source-code bodies or
provider-specific state.

IDs are the durable identity seam.  Labels and explanatory text may improve
over time without changing references.  Every knowledge record also carries
an explicit epistemic state so a missing or dynamic fact is represented rather
than silently promoted to an exact one.

**A journey is ORDERED** (``integration-knowledge-v2``, shape from PRD 11.1 to
11.5).  Until this revision a :class:`Journey` held nothing but unordered id
lists -- ``participant_ids``, ``interface_ids``, ``implementing_unit_ids``,
``mapping_ids`` -- so the model could not say that ``A -> B -> C`` runs in that
order, and it could not say which transformation happens at which step.  A
journey read as a bag of participants because that is literally what it was,
and ``topology._extraction_from_integration_model`` had to guess a direction
from ``participant_ids[0]`` and ``participant_ids[-1]``.
:class:`JourneyStep` is the fix, and five rules come with it:

* **A step is typed by what it DOES** (``kind``: trigger, extract, transform,
  route, decision, deliver, ...), not by a pair of endpoints.  A trigger has no
  source and a transform has no destination, so a source/target pair cannot
  express either, and a schema that demanded one would be asking the agent to
  invent it.
* **Units, mappings and SQL belong to the step where they execute**, not to the
  journey as a whole.  They MOVED; ``Journey.implementing_unit_ids``,
  ``Journey.mapping_ids``, ``Journey.interface_ids`` and ``Journey.sql_refs``
  are derived views over the steps, so the same fact cannot be recorded in two
  places and disagree with itself.
* **Opposite directions are separate journeys**, never a flag on one journey.
  An acknowledgement travelling back is a different sequence of steps with
  different transformations, and flattening the pair loses both.
* **Fan-out and fan-in are transitions.**  A ``decision`` step names each
  outgoing edge with its condition and target; two steps naming one target is a
  merge.  A linear journey declares no transitions -- ``sequence`` orders it.
* **Uncertainty is representable, so nothing has to be invented** (PRD 11.4).
  ``sequence`` may be absent, participants and transition targets may be null,
  ``state`` may be ``unresolved``, and ``kind`` accepts a role this module has
  never heard of.  A step that could not be fully established stays, marked;
  dropping it publishes a shorter journey that is simply wrong.

Because this is a PERSISTED schema, ``schema_version`` moved to
``integration-knowledge-v2``.  A file written against v1 is refused with
:class:`IntegrationModelError` rather than silently half-read; the resume
identity is what turns that into a clean restart.  Everything short of a
whole-file defect is handled by per-item partial acceptance
(:func:`load_report`): a malformed journey costs that journey and is RECORDED,
because the previous behaviour -- one ``ValidationError`` discarding the whole
file, its exception type reaching a client-facing limitations document --
loses an investigation's accumulated work over one bad record.

The model lives on disk in TWO shapes and :func:`load_workspace_model` reads
both. ``notes/integration_model.json`` is the original single nested document;
``notes/integrations/<name>.json`` is one integration per file and is what the
agent is now taught to write. The second shape exists because of a verified
run: 15 KB of hand-written nested JSON, one omitted ``"steps": [`` on line 17,
and the whole investigation -- 5 integrations, 6 journeys -- became unreadable
at ``json.loads``. The agent has no JSON parser in its sandbox, so it could not
have known. One file per record bounds that blast radius to one record, which
is the only fix that scales with how much the agent writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

KnowledgeState = Literal["exact", "partial", "dynamic", "unresolved", "unsupported"]
Severity = Literal["info", "low", "medium", "high", "critical", "unknown"]

#: The persisted schema this module reads and writes. Bumped whenever a
#: previously accepted file would now be misread rather than merely thinner.
SCHEMA_VERSION = "integration-knowledge-v2"

#: Published documents are named RELATIVE TO ``output/`` -- that is how the
#: publisher records them and how a reader cites them. The agent writes to
#: ``output/integrations/orders.md`` and naturally types that whole path, so a
#: leading ``output/`` is normalised away rather than rejected: it is the same
#: file, and refusing it would drop the journey that carries the citation.
_OUTPUT_PREFIX = "output/"

#: Cap on how much of a validation complaint is repeated. Field names and
#: pydantic's own message only -- never the offending value, which came out of
#: a client's estate and must not reach a log line or a database row.
_MAX_REASON_CHARS = 200


class IntegrationModelError(ValueError):
    """The persisted integration model cannot be read AT ALL.

    A WHOLE-FILE defect only: unreadable bytes, text that is not JSON, a
    payload that is not an object, or a ``schema_version`` this revision does
    not speak.  Anything narrower -- one bad journey, one duplicate id -- is
    per-item partial acceptance and never raises.

    ``ValueError`` rather than a sibling of ``WorkspaceUnavailableError`` on
    purpose: this module is provider-neutral and imports nothing from the
    lane, and both existing callers (``topology.py``, ``registers.py:775``)
    already catch ``ValueError`` there.
    """


def stable_id(kind: str, *identity: str, namespace: str = "nexus") -> str:
    """Return a stable opaque ID from immutable, platform-native identity.

    Human-facing labels must not be passed as identity unless the label itself
    is the source system's immutable identifier.  The helper intentionally
    does not guess identity from prose.
    """

    parts = [namespace.strip(), kind.strip(), *(part.strip() for part in identity)]
    if not all(parts):
        raise ValueError("namespace, kind and every identity part must be non-empty")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    safe_kind = "".join(character if character.isalnum() else "_" for character in kind)
    return f"{safe_kind.strip('_').lower()}_{digest}"


class DurableModel(BaseModel):
    """Strict persisted model shared by all knowledge records."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceRef(DurableModel):
    id: str
    label: str
    state: KnowledgeState
    evidence_type: str
    artifact: str
    locator: str | None = None
    observed_at: str | None = None

    _non_empty = field_validator("id", "label", "evidence_type", "artifact")(
        lambda value: _require_text(value)
    )


class KnowledgeRecord(DurableModel):
    """Common identity, confidence and provenance carried by domain records."""

    id: str
    label: str
    state: KnowledgeState
    evidence_ref_ids: list[str] = Field(default_factory=list)

    _non_empty = field_validator("id", "label")(lambda value: _require_text(value))

    @field_validator("evidence_ref_ids")
    @classmethod
    def _unique_evidence_refs(cls, values: list[str]) -> list[str]:
        return _unique_text(values, "evidence_ref_ids")


class Participant(KnowledgeRecord):
    role: str | None = None
    system_ref: str | None = None


class Interface(KnowledgeRecord):
    interface_kind: str | None = None
    direction: str | None = None
    source_participant_id: str | None = None
    target_participant_id: str | None = None
    endpoint_ref: str | None = None
    schema_refs: list[str] = Field(default_factory=list)


class ImplementingUnit(KnowledgeRecord):
    unit_kind: str | None = None
    platform: str | None = None
    project_ref: str | None = None


class Mapping(KnowledgeRecord):
    source_ref: str
    target_ref: str
    transformation_summary: str | None = None
    condition_summaries: list[str] = Field(default_factory=list)

    _mapping_refs = field_validator("source_ref", "target_ref")(lambda value: _require_text(value))


class ConfigurationRef(KnowledgeRecord):
    key_ref: str
    scope: str | None = None
    environment_refs: list[str] = Field(default_factory=list)

    _key_ref = field_validator("key_ref")(lambda value: _require_text(value))


class Dependency(KnowledgeRecord):
    source_id: str
    target_id: str
    relation: str

    _dependency_refs = field_validator("source_id", "target_id", "relation")(
        lambda value: _require_text(value)
    )


class RuntimeRef(KnowledgeRecord):
    source_ref: str
    metric: str | None = None
    window: str | None = None
    value: str | int | float | bool | None = None
    unit: str | None = None

    _source_ref = field_validator("source_ref")(lambda value: _require_text(value))


class Finding(KnowledgeRecord):
    statement: str
    category: str | None = None
    severity: Severity = "unknown"

    _statement = field_validator("statement")(lambda value: _require_text(value))


class Risk(KnowledgeRecord):
    description: str
    likelihood: Severity = "unknown"
    impact: Severity = "unknown"
    mitigation: str | None = None

    _description = field_validator("description")(lambda value: _require_text(value))


class DesignDifference(KnowledgeRecord):
    designed: str
    implemented: str
    disposition: str | None = None

    _difference_text = field_validator("designed", "implemented")(
        lambda value: _require_text(value)
    )


class Unknown(KnowledgeRecord):
    question: str
    reason: str | None = None
    next_step: str | None = None

    _question = field_validator("question")(lambda value: _require_text(value))


#: The step roles the renderers understand. NOT a closed set on the model:
#: an unrecognised kind is kept as written, because dropping a step for an
#: unlisted role loses a hand-off the agent actually established -- the exact
#: outcome the uncertainty rules exist to prevent. Renderers fall back to
#: neutral wording for a kind they do not know.
JOURNEY_STEP_KINDS: tuple[str, ...] = (
    "trigger",
    "extract",
    "transform",
    "route",
    "decision",
    "deliver",
    "acknowledge",
    "error",
    "unknown",
)


class JourneyTransition(DurableModel):
    """One outgoing edge from a step, and the condition that takes it.

    ``target_step_id`` is NULLABLE on purpose: routing decided at runtime, a
    branch whose destination could not be established, and an error path that
    leaves the journey are all real answers, and each of them would otherwise
    force the agent to invent a step to point at.
    """

    condition: str = ""
    target_step_id: str | None = None
    state: KnowledgeState = "partial"

    @field_validator("condition")
    @classmethod
    def _trim_condition(cls, value: str) -> str:
        return value.strip()


class JourneyStep(DurableModel):
    """One step in a journey, TYPED BY WHAT IT DOES.

    A step is the smallest thing a reader can act on. It is not necessarily a
    hand-off between two systems: a trigger has no source, a transform has no
    destination, and a decision has neither -- which is why the role is a
    ``kind`` rather than a source/target pair, and why both participants are
    optional.  Units, mappings and SQL live HERE rather than on the journey
    because "which transformation happens at which step" is the question the
    journey-level id lists could not answer.

    Every field except ``id`` has a default, deliberately.  This record is
    written by an investigating agent mid-run against real evidence, and a
    schema that rejects a step for a field the evidence does not support is a
    schema that asks the agent to invent one.  ``state`` defaults to
    ``partial`` -- the humble value -- so an omission can never read as
    ``exact``, and ``sequence`` may be absent when the order genuinely could
    not be established.
    """

    id: str
    #: 1-based position, or ``None`` when the order is not established.
    #: Ordering is by this number and never by position in the JSON array: the
    #: canonical form sorts records by id for byte-stability, so array order
    #: does not survive a save/load.
    #:
    #: Two steps MAY share a sequence. Under ``transitions`` that is a fork,
    #: not an ambiguity, and refusing it would make fan-out unrepresentable.
    sequence: int | None = Field(default=None, ge=1)
    #: What this step does: see :data:`JOURNEY_STEP_KINDS`.
    kind: str = "unknown"
    label: str = ""
    #: One sentence of prose. This is the field that makes an ordered journey
    #: readable rather than a list of identifiers, and it is what a dossier's
    #: journey section is written from.
    summary: str = ""
    state: KnowledgeState = "partial"
    #: Present only where the step genuinely has that end. ``None`` means "not
    #: applicable, or not established" and is preserved rather than dropped.
    source_participant_id: str | None = None
    target_participant_id: str | None = None
    #: The units that execute at this step, the interfaces it crosses, the
    #: mappings it applies, and the SQL it runs.
    unit_ids: list[str] = Field(default_factory=list)
    interface_ids: list[str] = Field(default_factory=list)
    mapping_ids: list[str] = Field(default_factory=list)
    #: Names the SQL as the published reference register names it. Free text
    #: rather than an id into this model, because the model holds no SQL
    #: collection and inventing one would duplicate the deterministic register.
    sql_refs: list[str] = Field(default_factory=list)
    mechanism: str | None = None
    #: Outgoing edges. Absent on a linear step, whose successor is simply the
    #: next one by sequence.
    transitions: list[JourneyTransition] = Field(default_factory=list)
    evidence_ref_ids: list[str] = Field(default_factory=list)

    _step_id = field_validator("id")(lambda value: _require_text(value))

    @field_validator("kind")
    @classmethod
    def _normalise_kind(cls, value: str) -> str:
        cleaned = "_".join(value.strip().lower().split())
        return cleaned or "unknown"

    @field_validator("label", "summary", "mechanism")
    @classmethod
    def _trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "unit_ids", "interface_ids", "mapping_ids", "sql_refs", "evidence_ref_ids"
    )
    @classmethod
    def _unique_step_links(cls, values: list[str]) -> list[str]:
        return _unique_text(values, "journey step reference list")

    @property
    def order_key(self) -> tuple[int, int, str]:
        """Deterministic ordering. A step with no sequence sorts last, by id.

        Unordered steps are kept and shown, never silently interleaved into a
        position the evidence did not establish.
        """

        return (0, self.sequence, self.id) if self.sequence is not None else (1, 0, self.id)

    @property
    def target_step_ids(self) -> list[str]:
        """The resolved successors this step names, in declared order."""

        return list(
            dict.fromkeys(
                transition.target_step_id
                for transition in self.transitions
                if transition.target_step_id
            )
        )


class Journey(KnowledgeRecord):
    """One directional, event-specific or lifecycle-specific path.

    Create, update, delete, request, response, acknowledgement, retry, reverse
    synchronisation and failure notification are each their OWN journey. An
    opposite direction is never a flag on one journey: it is a different
    sequence of steps applying different transformations, and flattening the
    pair loses both.

    ``interface_ids``, ``implementing_unit_ids``, ``mapping_ids`` and
    ``sql_refs`` are DERIVED from :attr:`steps` and cannot be assigned.  That
    is the whole point of the v2 change: a unit recorded both on a journey and
    on one of its steps is two answers to "where does this run", and the pair
    drifts.
    """

    purpose: str | None = None
    #: The ordered steps. A journey with no steps is legitimate early in an
    #: investigation -- the participants are known and the path is not yet --
    #: and it stays publishable rather than being rejected.
    steps: list[JourneyStep] = Field(default_factory=list)
    #: Everything the journey passes through, including participants no step
    #: could be attributed to. Endpoints still come from the steps when there
    #: are any.
    participant_ids: list[str] = Field(default_factory=list)
    #: The client documents that explain this journey, relative to ``output/``.
    #: A journey may legitimately be covered by several dossiers, so this is a
    #: list and every entry is kept -- this is what replaced the topology
    #: compiler's fixed six-document ceiling.
    document_paths: list[str] = Field(default_factory=list)
    configuration_ref_ids: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    runtime_ref_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    design_difference_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "participant_ids",
        "configuration_ref_ids",
        "dependency_ids",
        "runtime_ref_ids",
        "finding_ids",
        "risk_ids",
        "design_difference_ids",
        "unknown_ids",
    )
    @classmethod
    def _unique_links(cls, values: list[str]) -> list[str]:
        return _unique_text(values, "journey reference list")

    @field_validator("document_paths")
    @classmethod
    def _publishable_document_paths(cls, values: list[str]) -> list[str]:
        return _unique_text([_document_path(value) for value in values], "document_paths")

    @model_validator(mode="after")
    def _step_ids_are_unique(self) -> Journey:
        """Raise only on genuine AMBIGUITY; report everything else as an issue.

        Two steps sharing an id is the same class of defect as two records
        sharing an id, which this module already refuses -- and transitions
        point at step ids, so a repeat makes routing undecidable.

        Two steps sharing a SEQUENCE is not a defect: under ``transitions``
        that is how a fork is expressed.  A dangling transition target is a
        graph defect, reported by :meth:`Integration.validate_structure`, and
        does not cost the journey.
        """

        duplicate_ids = _duplicates(step.id for step in self.steps)
        if duplicate_ids:
            raise ValueError(f"duplicate journey step IDs: {', '.join(duplicate_ids)}")
        self.steps.sort(key=lambda step: step.order_key)
        return self

    @property
    def ordered_steps(self) -> list[JourneyStep]:
        """The steps in execution order, with unordered ones last."""

        return sorted(self.steps, key=lambda step: step.order_key)

    @property
    def unordered_steps(self) -> list[JourneyStep]:
        """Steps whose position in the journey was not established.

        Surfaced rather than hidden: "this happens, we could not place it" is
        a different answer from "this happens last", and a renderer that cannot
        tell them apart publishes the second one.
        """

        return [step for step in self.ordered_steps if step.sequence is None]

    @property
    def interface_ids(self) -> list[str]:
        """Derived: the interfaces crossed, in step order."""

        return list(
            dict.fromkeys(
                interface_id
                for step in self.ordered_steps
                for interface_id in step.interface_ids
            )
        )

    @property
    def implementing_unit_ids(self) -> list[str]:
        """Derived: the units that execute on this journey, in step order.

        A unit shared by several steps appears ONCE here and on every step that
        runs it -- the step is where it executes, this view is who is involved.
        """

        return list(
            dict.fromkeys(
                unit_id for step in self.ordered_steps for unit_id in step.unit_ids
            )
        )

    @property
    def mapping_ids(self) -> list[str]:
        """Derived: the mappings applied on this journey, in step order."""

        return list(
            dict.fromkeys(
                mapping_id for step in self.ordered_steps for mapping_id in step.mapping_ids
            )
        )

    @property
    def sql_refs(self) -> list[str]:
        """Derived: the SQL this journey runs, in step order."""

        return list(
            dict.fromkeys(
                sql_ref for step in self.ordered_steps for sql_ref in step.sql_refs
            )
        )

    @property
    def endpoints(self) -> tuple[str | None, str | None]:
        """The first established source and the last established target.

        Falls back to :attr:`participant_ids` when the journey has no steps,
        because an investigation records its participants before it has
        established the path between them.
        """

        ordered = self.ordered_steps
        source = next(
            (step.source_participant_id for step in ordered if step.source_participant_id),
            None,
        )
        target = next(
            (
                step.target_participant_id
                for step in reversed(ordered)
                if step.target_participant_id
            ),
            None,
        )
        if source is None and self.participant_ids:
            source = self.participant_ids[0]
        if target is None and len(self.participant_ids) > 1:
            target = self.participant_ids[-1]
        return source, target


class StructuralIssue(DurableModel):
    path: str
    code: str
    message: str


_RecordT = TypeVar("_RecordT", bound=DurableModel)


class Integration(KnowledgeRecord):
    """One meaningful integration or business capability.

    It may contain multiple journeys, interfaces and implementation units.  A
    system relationship is merely a possible boundary, not the identity rule.
    """

    capability: str | None = None
    summary: str | None = None
    #: The client documents that cover this integration, relative to
    #: ``output/``. Journeys carry their own; this is the dossier a reader is
    #: sent to when no single journey owns the answer.
    document_paths: list[str] = Field(default_factory=list)
    journeys: list[Journey] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)
    implementing_units: list[ImplementingUnit] = Field(default_factory=list)
    mappings: list[Mapping] = Field(default_factory=list)
    configuration_refs: list[ConfigurationRef] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    runtime_refs: list[RuntimeRef] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    design_differences: list[DesignDifference] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    _COLLECTIONS: ClassVar[tuple[str, ...]] = (
        "journeys",
        "participants",
        "interfaces",
        "implementing_units",
        "mappings",
        "configuration_refs",
        "dependencies",
        "runtime_refs",
        "findings",
        "risks",
        "design_differences",
        "unknowns",
        "evidence_refs",
    )

    @field_validator("document_paths")
    @classmethod
    def _publishable_document_paths(cls, values: list[str]) -> list[str]:
        return _unique_text([_document_path(value) for value in values], "document_paths")

    @model_validator(mode="after")
    def _record_ids_are_unique_per_collection(self) -> Integration:
        for collection_name in self._COLLECTIONS:
            records = getattr(self, collection_name)
            duplicate_ids = _duplicates(record.id for record in records)
            if duplicate_ids:
                raise ValueError(f"duplicate IDs in {collection_name}: {', '.join(duplicate_ids)}")
            records.sort(key=lambda record: record.id)
        return self

    def upsert(self, collection: str, record: DurableModel) -> None:
        """Create or incrementally merge a nested record by stable ID."""

        if collection not in self._COLLECTIONS:
            raise ValueError(f"unknown integration collection: {collection}")
        records = getattr(self, collection)
        expected = type(records[0]) if records else _COLLECTION_TYPES[collection]
        if not isinstance(record, expected):
            raise TypeError(
                f"{collection} accepts {expected.__name__}, got {type(record).__name__}"
            )
        for index, current in enumerate(records):
            if current.id == record.id:
                records[index] = _merge_models(current, record)
                return
        records.append(record)

    def validate_structure(self) -> list[StructuralIssue]:
        """Check graph integrity without judging prose, labels or factual truth."""

        issues: list[StructuralIssue] = []
        evidence_ids = {record.id for record in self.evidence_refs}
        for evidence_id in self.evidence_ref_ids:
            if evidence_id not in evidence_ids:
                issues.append(
                    StructuralIssue(
                        path=f"integrations/{self.id}",
                        code="dangling_evidence_ref",
                        message=f"evidence reference {evidence_id!r} does not exist",
                    )
                )
        for collection_name in self._COLLECTIONS:
            if collection_name == "evidence_refs":
                continue
            for record in getattr(self, collection_name):
                for evidence_id in record.evidence_ref_ids:
                    if evidence_id not in evidence_ids:
                        issues.append(
                            StructuralIssue(
                                path=f"integrations/{self.id}/{collection_name}/{record.id}",
                                code="dangling_evidence_ref",
                                message=f"evidence reference {evidence_id!r} does not exist",
                            )
                        )

        participant_ids = {record.id for record in self.participants}
        interface_ids = {record.id for record in self.interfaces}
        unit_ids = {record.id for record in self.implementing_units}
        mapping_ids = {record.id for record in self.mappings}
        journey_targets = {
            "participant_ids": participant_ids,
            "configuration_ref_ids": {record.id for record in self.configuration_refs},
            "dependency_ids": {record.id for record in self.dependencies},
            "runtime_ref_ids": {record.id for record in self.runtime_refs},
            "finding_ids": {record.id for record in self.findings},
            "risk_ids": {record.id for record in self.risks},
            "design_difference_ids": {record.id for record in self.design_differences},
            "unknown_ids": {record.id for record in self.unknowns},
        }
        #: Step references are checked WITH THEIR STEP in the path. A dangling
        #: unit reported against the journey says which journey is wrong;
        #: reported against the step it says which hand-off is wrong, which is
        #: the thing an author can actually fix.
        step_targets = {
            "source_participant_id": participant_ids,
            "target_participant_id": participant_ids,
            "interface_ids": interface_ids,
            "unit_ids": unit_ids,
            "mapping_ids": mapping_ids,
        }
        for journey in self.journeys:
            journey_path = f"integrations/{self.id}/journeys/{journey.id}"
            for field_name, known_ids in journey_targets.items():
                for target_id in getattr(journey, field_name):
                    if target_id not in known_ids:
                        issues.append(
                            StructuralIssue(
                                path=f"{journey_path}/{field_name}",
                                code="dangling_reference",
                                message=f"referenced record {target_id!r} does not exist",
                            )
                        )
            issues.extend(self._step_issues(journey, journey_path, step_targets, evidence_ids))
        return sorted(issues, key=lambda item: (item.path, item.code, item.message))

    @staticmethod
    def _step_issues(
        journey: Journey,
        journey_path: str,
        step_targets: dict[str, set[str]],
        evidence_ids: set[str],
    ) -> list[StructuralIssue]:
        """Graph integrity for one journey's ordered steps."""

        issues: list[StructuralIssue] = []
        known_steps = {step.id for step in journey.steps}
        for step in journey.ordered_steps:
            step_path = f"{journey_path}/steps/{step.id}"
            for field_name, known_ids in step_targets.items():
                value = getattr(step, field_name)
                candidates = value if isinstance(value, list) else ([value] if value else [])
                for target_id in candidates:
                    if target_id not in known_ids:
                        issues.append(
                            StructuralIssue(
                                path=f"{step_path}/{field_name}",
                                code="dangling_reference",
                                message=f"referenced record {target_id!r} does not exist",
                            )
                        )
            for evidence_id in step.evidence_ref_ids:
                if evidence_id not in evidence_ids:
                    issues.append(
                        StructuralIssue(
                            path=step_path,
                            code="dangling_evidence_ref",
                            message=f"evidence reference {evidence_id!r} does not exist",
                        )
                    )
            for transition in step.transitions:
                # An ABSENT target is legitimate -- runtime routing, an
                # unestablished branch, a path that leaves this journey -- and
                # is never reported. A target that NAMES a step which is not
                # here is a broken reference.
                target_id = transition.target_step_id
                if target_id is None:
                    continue
                if target_id == step.id:
                    issues.append(
                        StructuralIssue(
                            path=f"{step_path}/transitions",
                            code="self_referencing_step",
                            message="a journey step cannot transition to itself",
                        )
                    )
                elif target_id not in known_steps:
                    issues.append(
                        StructuralIssue(
                            path=f"{step_path}/transitions",
                            code="dangling_reference",
                            message=f"referenced journey step {target_id!r} does not exist",
                        )
                    )
        return issues


_COLLECTION_TYPES: dict[str, type[DurableModel]] = {
    "journeys": Journey,
    "participants": Participant,
    "interfaces": Interface,
    "implementing_units": ImplementingUnit,
    "mappings": Mapping,
    "configuration_refs": ConfigurationRef,
    "dependencies": Dependency,
    "runtime_refs": RuntimeRef,
    "findings": Finding,
    "risks": Risk,
    "design_differences": DesignDifference,
    "unknowns": Unknown,
    "evidence_refs": EvidenceRef,
}


class CompletionSummary(DurableModel):
    integration_count: int
    journey_count: int
    #: Ordered hand-offs across every journey. Counted separately from
    #: ``journey_count`` because a journey with no steps is an endpoint pair
    #: with no established path -- a gauge, not a failure, and one that a
    #: journey count alone cannot show.
    step_count: int
    record_count: int
    state_counts: dict[str, int]
    structural_issue_count: int
    unknown_count: int


class CatalogueEntry(DurableModel):
    id: str
    label: str
    state: KnowledgeState
    capability: str | None
    journey_count: int
    participant_labels: list[str]
    interface_count: int
    implementing_unit_count: int
    mapping_count: int
    risk_count: int
    unknown_count: int


class IntegrationKnowledgeBase(DurableModel):
    """A deterministic, checkpoint-friendly integration knowledge snapshot."""

    schema_version: Literal["integration-knowledge-v2"] = "integration-knowledge-v2"
    integrations: list[Integration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integration_ids_are_unique(self) -> IntegrationKnowledgeBase:
        duplicate_ids = _duplicates(integration.id for integration in self.integrations)
        if duplicate_ids:
            raise ValueError(f"duplicate integration IDs: {', '.join(duplicate_ids)}")
        self.integrations.sort(key=lambda integration: integration.id)
        return self

    def upsert_integration(self, incoming: Integration, *, replace: bool = False) -> Integration:
        """Create, replace or incrementally merge an integration by stable ID."""

        for index, current in enumerate(self.integrations):
            if current.id != incoming.id:
                continue
            merged = incoming.model_copy(deep=True) if replace else _merge_models(current, incoming)
            self.integrations[index] = merged
            return merged
        created = incoming.model_copy(deep=True)
        self.integrations.append(created)
        return created

    def merge(self, incoming: IntegrationKnowledgeBase) -> IntegrationKnowledgeBase:
        """Return a new snapshot with ``incoming`` incrementally applied."""

        merged = self.model_copy(deep=True)
        for integration in incoming.integrations:
            merged.upsert_integration(integration)
        return merged

    def validate_structure(self) -> list[StructuralIssue]:
        issues: list[StructuralIssue] = []
        for integration in self.integrations:
            issues.extend(integration.validate_structure())
        graph_ids = {
            record.id
            for integration in self.integrations
            for collection_name in integration._COLLECTIONS
            if collection_name != "evidence_refs"
            for record in getattr(integration, collection_name)
        }
        for integration in self.integrations:
            participant_ids = {record.id for record in integration.participants}
            for interface in integration.interfaces:
                for field_name in ("source_participant_id", "target_participant_id"):
                    participant_id = getattr(interface, field_name)
                    if participant_id is not None and participant_id not in participant_ids:
                        issues.append(
                            StructuralIssue(
                                path=(
                                    f"integrations/{integration.id}/interfaces/"
                                    f"{interface.id}/{field_name}"
                                ),
                                code="dangling_reference",
                                message=f"referenced participant {participant_id!r} does not exist",
                            )
                        )
            for dependency in integration.dependencies:
                for field_name in ("source_id", "target_id"):
                    target_id = getattr(dependency, field_name)
                    if target_id not in graph_ids:
                        issues.append(
                            StructuralIssue(
                                path=(
                                    f"integrations/{integration.id}/dependencies/"
                                    f"{dependency.id}/{field_name}"
                                ),
                                code="dangling_reference",
                                message=f"referenced graph record {target_id!r} does not exist",
                            )
                        )
        return sorted(issues, key=lambda item: (item.path, item.code, item.message))

    def completion_summary(self) -> CompletionSummary:
        states: Counter[str] = Counter()
        journey_count = 0
        step_count = 0
        record_count = 0
        unknown_count = 0
        for integration in self.integrations:
            states[integration.state] += 1
            for collection_name in integration._COLLECTIONS:
                records = getattr(integration, collection_name)
                record_count += len(records)
                states.update(record.state for record in records)
            journey_count += len(integration.journeys)
            # Steps are records with their own epistemic state, so they count
            # in both totals. Omitting them would let a journey of six
            # unresolved hand-offs report the same confidence as a journey of
            # six exact ones.
            for journey in integration.journeys:
                step_count += len(journey.steps)
                record_count += len(journey.steps)
                states.update(step.state for step in journey.steps)
            unknown_count += len(integration.unknowns)
        return CompletionSummary(
            integration_count=len(self.integrations),
            journey_count=journey_count,
            step_count=step_count,
            record_count=record_count,
            state_counts={key: states[key] for key in sorted(states)},
            structural_issue_count=len(self.validate_structure()),
            unknown_count=unknown_count,
        )

    def catalogue(self) -> list[CatalogueEntry]:
        """Compile a deterministic, presentation-neutral catalogue data view."""

        entries = [
            CatalogueEntry(
                id=integration.id,
                label=integration.label,
                state=integration.state,
                capability=integration.capability,
                journey_count=len(integration.journeys),
                participant_labels=sorted(
                    (participant.label for participant in integration.participants),
                    key=str.casefold,
                ),
                interface_count=len(integration.interfaces),
                implementing_unit_count=len(integration.implementing_units),
                mapping_count=len(integration.mappings),
                risk_count=len(integration.risks),
                unknown_count=len(integration.unknowns),
            )
            for integration in self.integrations
        ]
        return sorted(entries, key=lambda entry: (entry.label.casefold(), entry.id))

    def to_canonical_dict(self) -> dict[str, Any]:
        # Container mutation (for example ``items.append``) bypasses Pydantic's
        # assignment hook, so revalidate before any durable write.
        validated = type(self).model_validate(self.model_dump(mode="python"))
        return _canonicalize(validated.model_dump(mode="json", exclude_none=True))

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_canonical_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def save(self, path: Path) -> None:
        """Atomically persist a deterministic checkpoint."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(self.to_json(), encoding="utf-8")
        os.replace(temporary, path)

    checkpoint = save

    @classmethod
    def load(cls, path: Path) -> IntegrationKnowledgeBase:
        """Load with per-item partial acceptance; raise only on a file defect.

        Every rejection is logged with its path and reason.  The previous
        implementation was ``model_validate_json`` over the whole file, so one
        malformed journey discarded an entire investigation's accumulated
        model, and the only trace of it was a ``ValidationError`` type name
        rendered into a CLIENT-FACING limitations document by
        ``registers.py:775``.  Both halves of that were wrong: the loss was
        silent to the agent and the diagnosis was leaked to the reader.

        Callers that want the rejections rather than the log line call
        :func:`load_report`.
        """

        report = load_report(path)
        if report.rejected:
            logger.warning(
                "integration model: dropped %d unusable record(s) from %s: %s",
                len(report.rejected),
                Path(path).name,
                "; ".join(item.describe() for item in report.rejected[:5]),
            )
        return report.knowledge


@dataclass(frozen=True)
class RejectedRecord:
    """One record that could not be accepted, and why.

    ``path`` is positional (``integrations[2]/journeys[0]``) because a record
    that failed validation may well be the one with the unusable id.
    """

    path: str
    kind: str
    label: str
    reason: str

    def describe(self) -> str:
        """One line for a log or an advisory note. The path already says the
        collection, so the kind is not repeated into the prose."""

        name = f" {self.label!r}" if self.label else ""
        return f"{self.path}{name}: {self.reason}"


@dataclass(frozen=True)
class ModelLoadReport:
    """What was accepted from a persisted model, and what was dropped."""

    knowledge: IntegrationKnowledgeBase
    rejected: tuple[RejectedRecord, ...] = ()
    #: Set when the file did not parse as a whole and integrations were
    #: recovered from the readable parts. Reported to the operator, because a
    #: salvaged model is a model with an unknown hole in it and the run's
    #: documentation may describe integrations that no longer resolve.
    salvaged: str | None = None


def load_report(path: Path) -> ModelLoadReport:
    """Read ``path`` with per-item partial acceptance.

    Raises :class:`IntegrationModelError` for a whole-file defect and for a
    ``schema_version`` written by a different revision of this module -- the
    ordered-journey change means a v1 file's journeys would be read as
    journeys with no path at all, which is a worse answer than a clean
    restart.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IntegrationModelError(
            f"the integration model at {path.name} could not be read ({type(exc).__name__})"
        ) from exc
    salvage_notice: str | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # SALVAGE before refusing. Per-item partial acceptance below only ever
        # ran once `json.loads` had succeeded on the WHOLE file, so a single
        # missing bracket discarded an entire investigation -- which is exactly
        # what it did on the verified run: 5 integrations and 6 journeys were
        # written and all of them were lost to one absent `"steps": [`.
        #
        # The agent hand-writes ~15 KB of nested JSON and has no tool to parse
        # it, so a syntax error is an ORDINARY event here, not an exceptional
        # one. Treating it as fatal makes the rarest kind of mistake the most
        # expensive kind.
        salvaged = _salvage_integration_objects(text)
        if not salvaged:
            # Position, never the offending text: this message reaches a log.
            raise IntegrationModelError(
                f"the integration model at {path.name} is not valid JSON "
                f"(line {exc.lineno}, column {exc.colno})"
            ) from exc
        payload = {"schema_version": SCHEMA_VERSION, "integrations": salvaged}
        salvage_notice = (
            f"the integration model at {path.name} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno}); "
            f"{len(salvaged)} integration object(s) were recovered from it. "
            "Records inside the damaged region are missing."
        )
        logger.warning("%s", salvage_notice)
    if not isinstance(payload, dict):
        raise IntegrationModelError(
            f"the integration model at {path.name} is not a JSON object"
        )
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise IntegrationModelError(
            f"the integration model at {path.name} declares schema {version!r}; "
            f"this revision reads {SCHEMA_VERSION!r}"
        )
    raw_integrations = payload.get("integrations", [])
    if not isinstance(raw_integrations, list):
        raise IntegrationModelError(
            f"the integration model at {path.name} has a non-list 'integrations'"
        )

    rejected: list[RejectedRecord] = []
    accepted: list[Integration] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_integrations):
        integration = _accept_integration(raw, f"integrations[{index}]", rejected)
        if integration is None:
            continue
        if integration.id in seen_ids:
            rejected.append(
                RejectedRecord(
                    path=f"integrations[{index}]",
                    kind="integration",
                    label=integration.label,
                    reason=f"duplicate integration id {integration.id!r}",
                )
            )
            continue
        seen_ids.add(integration.id)
        accepted.append(integration)
    return ModelLoadReport(
        knowledge=IntegrationKnowledgeBase(integrations=accepted),
        rejected=tuple(rejected),
        salvaged=salvage_notice,
    )


def load_workspace_model(staging_dir: Path) -> ModelLoadReport:
    """Read the whole workspace record: the one file, then the one-per-file directory.

    ONE BAD FILE COSTS ONE FILE. That is the entire reason this function
    exists, and it is the same reasoning that made the coverage ledger JSONL --
    the ledger came through the verified run intact at 142 of 142 while the
    single nested model did not, because a line-oriented file cannot lose its
    neighbours.

    The two sources are merged in a deliberate order:

    1. ``notes/integration_model.json``, through :func:`load_report`, with its
       salvage and its per-item acceptance unchanged. A resume must never lose
       an older run's record just because the preferred shape now exists.
    2. every ``notes/integrations/*.json``, sorted by filename so two runs over
       the same workspace produce the same model.

    **Later wins, and the loser is RECORDED.** The directory is the newer,
    preferred shape, so it overrides the monolithic file for a repeated id, and
    within the directory the last filename in sort order overrides the earlier
    ones. Every override appends a :class:`RejectedRecord` naming the source
    that lost, because an id silently resolving to one of two different records
    is how a document ends up citing a journey that is not in the model.

    **Nothing here raises.** An absent or empty directory is the ordinary case
    for a run that used the monolithic file; a monolithic file with a
    whole-file defect becomes one rejection rather than an exception, because
    refusing the directory's readable records over a broken neighbour is the
    failure this change exists to end. Callers that want the strict,
    single-file behaviour still have :func:`load_report`, which is unchanged.
    """

    # Local import: the layout is ``contract.py``'s to own and must never be
    # copied here, but the schema half of this module stays importable without
    # dragging in the run contract (and its provider models) behind it.
    from mcp_server.providers.agent_workspace import contract

    staging_dir = Path(staging_dir)
    rejected: list[RejectedRecord] = []
    accepted: dict[str, Integration] = {}
    origin: dict[str, str] = {}
    salvage_notices: list[str] = []

    model_name = contract.INTEGRATION_MODEL.as_posix()
    model_file = staging_dir / contract.INTEGRATION_MODEL
    report: ModelLoadReport | None = None
    if model_file.is_file():
        try:
            report = load_report(model_file)
        except IntegrationModelError as exc:
            # Repeated whole. The message already carries a position and never
            # the bytes, and editing another function's sentence here is how
            # the two drift apart.
            _reject_file(rejected, model_name, str(exc))
        except ValueError as exc:
            # Defence in depth for the promise above: a pydantic
            # ``ValidationError`` is also a ``ValueError`` and its text QUOTES
            # the offending input, so only the type name may be repeated.
            _reject_file(rejected, model_name, f"could not be read ({type(exc).__name__})")
    if report is not None:
        rejected.extend(report.rejected)
        if report.salvaged:
            salvage_notices.append(report.salvaged)
        for integration in report.knowledge.integrations:
            _merge_loaded_integration(accepted, origin, rejected, model_name, integration)

    for path in _integration_files(staging_dir / contract.INTEGRATION_DIR):
        source = (contract.INTEGRATION_DIR / path.name).as_posix()
        for integration in _load_integration_file(path, source, rejected):
            _merge_loaded_integration(accepted, origin, rejected, source, integration)

    return ModelLoadReport(
        knowledge=IntegrationKnowledgeBase(integrations=list(accepted.values())),
        rejected=tuple(rejected),
        salvaged=" ".join(salvage_notices) or None,
    )


def _integration_files(directory: Path) -> list[Path]:
    """Every candidate integration file, in a deterministic order.

    ``glob`` on a missing directory yields nothing rather than raising, which
    is the behaviour wanted: a run that only ever wrote the monolithic file is
    normal, not degraded. Dot-prefixed names are skipped because that is what
    an interrupted atomic write leaves behind (:meth:`IntegrationKnowledgeBase.save`
    writes ``.<name>.tmp``), and a half-written checkpoint is not a record.
    """

    try:
        entries = sorted(directory.glob("*.json"), key=lambda path: path.name)
    except OSError:  # pragma: no cover - an unreadable directory is not a crash
        return []
    return [path for path in entries if path.is_file() and not path.name.startswith(".")]


def _load_integration_file(
    path: Path,
    source: str,
    rejected: list[RejectedRecord],
) -> list[Integration]:
    """Read one file from the integrations directory.

    The taught shape is a single integration object per file. An envelope
    (``{"integrations": [...]}``) and a bare list are accepted as well: the
    agent is being taught this format while the monolithic one still exists in
    front of it, so writing the old wrapper into a new file is the predictable
    mistake, and refusing a file whose records are perfectly readable would
    lose exactly what this change was built to keep.

    No salvage is attempted here, on purpose. Salvage exists because ONE
    bracket in the monolithic file cost an entire investigation; one file per
    integration already bounds that damage to one integration, which is the
    outcome salvage was approximating.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _reject_file(rejected, source, f"could not be read ({type(exc).__name__})")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # Position only. This reason reaches a log line and an operator note,
        # and the file holds a client's estate.
        return _reject_file(
            rejected, source, f"not valid JSON (line {exc.lineno}, column {exc.colno})"
        )

    if isinstance(payload, dict) and isinstance(payload.get("integrations"), list):
        version = payload.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            return _reject_file(
                rejected,
                source,
                f"declares schema {version!r}; this revision reads {SCHEMA_VERSION!r}",
            )
        raw_objects = payload["integrations"]
        indexed = True
    elif isinstance(payload, list):
        raw_objects = payload
        indexed = True
    elif isinstance(payload, dict):
        raw_objects = [payload]
        indexed = False
    else:
        return _reject_file(rejected, source, "not a JSON object")

    accepted: list[Integration] = []
    for index, raw in enumerate(raw_objects):
        item_path = f"{source}[{index}]" if indexed else source
        integration = _accept_integration(raw, item_path, rejected)
        if integration is not None:
            accepted.append(integration)
    return accepted


def _reject_file(
    rejected: list[RejectedRecord],
    source: str,
    reason: str,
) -> list[Integration]:
    """Record a whole file as unusable and keep going with the rest."""

    logger.warning("integration model: %s: %s", source, reason)
    rejected.append(RejectedRecord(path=source, kind="file", label="", reason=reason))
    return []


def _merge_loaded_integration(
    accepted: dict[str, Integration],
    origin: dict[str, str],
    rejected: list[RejectedRecord],
    source: str,
    integration: Integration,
) -> None:
    """Apply the one merge rule: the later source wins, and the loser is visible.

    Deliberately a REPLACEMENT rather than a field-level merge. Two files
    describing one id are two answers, and quietly welding them together
    produces a third record that neither run wrote -- the class of defect that
    derived journey views were introduced to prevent.
    """

    previous = origin.get(integration.id)
    if previous is not None:
        rejected.append(
            RejectedRecord(
                path=previous,
                kind="integration",
                label=accepted[integration.id].label,
                reason=f"integration id {integration.id!r} superseded by {source}",
            )
        )
    accepted[integration.id] = integration
    origin[integration.id] = source


#: How much of an id survives into a filename before it is truncated and
#: disambiguated by digest. Comfortably inside every filesystem's 255-byte
#: limit even with the suffix and the digest.
_FILENAME_STEM_LIMIT = 80


def integration_filename(integration_id: str) -> str:
    """Return the file an integration belongs in, inside ``contract.INTEGRATION_DIR``.

    A NAME, never a path. The id is written by an investigating agent and may
    be anything at all, so every character outside ASCII alphanumerics becomes
    an underscore: a separator cannot survive, ``..`` cannot survive, and a
    leading dot cannot survive, so the result can only ever name a file
    directly inside the directory it was asked for.

    Two guarantees the callers rely on:

    * **Deterministic.** The same id always yields the same name, so a record
      rewritten on a later attempt lands on top of itself rather than beside
      itself.
    * **Distinct where it matters.** An id with no usable ASCII characters, and
      one long enough to be truncated, are disambiguated by a digest of the
      whole id rather than collapsing onto a shared name.

    KNOWN LIMIT: ids differing only in case or only in punctuation slugify to
    one name, and on a case-insensitive filesystem the second write replaces
    the first. That is why :func:`load_workspace_model` records an override
    instead of applying one silently -- the collision is reported, not hidden.
    Ids minted by :func:`stable_id` carry a 24-character digest and cannot
    collide this way.
    """

    slug = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in integration_id
    )
    # Collapses runs and strips both ends in one pass, so "../../etc/passwd"
    # becomes "etc_passwd" rather than "______etc_passwd".
    slug = "_".join(part for part in slug.split("_") if part)
    if len(slug) > _FILENAME_STEM_LIMIT:
        slug = f"{slug[:_FILENAME_STEM_LIMIT].rstrip('_')}_{_id_digest(integration_id)}"
    if not slug:
        slug = f"integration_{_id_digest(integration_id)}"
    return f"{slug}.json"


def _id_digest(integration_id: str) -> str:
    return hashlib.sha256(integration_id.encode("utf-8")).hexdigest()[:8]


def _salvage_integration_objects(text: str) -> list[dict[str, Any]]:
    """Recover whole top-level integration objects from a file that will not parse.

    Scans for balanced ``{...}`` spans and keeps those that parse on their own
    AND look like an integration (an ``id`` and a ``label``). Deliberately
    simple and deliberately conservative:

    * **String-aware.** A brace inside ``"a } b"`` does not close an object,
      and ``\\"`` does not end a string. Without both, a description containing
      a brace silently truncates the span and the salvage produces garbage that
      parses.
    * **Nested objects are not candidates.** Only spans starting at depth zero
      relative to the scan are offered, so a journey is never mistaken for the
      integration that contains it -- it has no ``label``/``id`` pair at the
      right level anyway, but relying on that would be luck.
    * **No repair is attempted.** A damaged object is dropped, never guessed
      at. Reconstructing a missing bracket means inventing structure, and an
      invented journey is worse than an absent one.
    """

    # Every balanced span, at EVERY depth. Scanning only depth zero finds
    # nothing on a real file: integrations live at depth two, inside the root
    # object's "integrations" array, and on a damaged file the root object
    # never closes -- so a depth-zero scan returns an empty list and the
    # salvage silently does nothing. Verified against the run-252 file.
    opens: list[int] = []
    spans: list[tuple[int, int]] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            opens.append(index)
        elif char == "}" and opens:
            spans.append((opens.pop(), index + 1))

    # Outermost first, so a recovered integration is preferred over the
    # journeys nested inside it.
    spans.sort(key=lambda span: (span[0], -span[1]))
    objects: list[dict[str, Any]] = []
    claimed: list[tuple[int, int]] = []
    for start, end in spans:
        if any(begin <= start and end <= finish for begin, finish in claimed):
            continue  # already inside something recovered whole
        try:
            candidate = json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
        if not _looks_like_an_integration(candidate):
            continue
        objects.append(candidate)
        claimed.append((start, end))
    return objects


#: Keys that only an integration carries. ``id`` and ``label`` are not enough
#: on their own -- journeys, participants and steps all have both, so matching
#: on those alone promotes a journey to a top-level integration and invents an
#: estate that was never described.
_INTEGRATION_MARKERS: frozenset[str] = frozenset(
    {
        "journeys",
        "participants",
        "implementing_units",
        "interfaces",
        "document_paths",
        "capability",
    }
)


def _looks_like_an_integration(candidate: Any) -> bool:
    return (
        isinstance(candidate, dict)
        and isinstance(candidate.get("id"), str)
        and isinstance(candidate.get("label"), str)
        and bool(_INTEGRATION_MARKERS.intersection(candidate))
    )


def _accept_integration(
    raw: Any,
    path: str,
    rejected: list[RejectedRecord],
) -> Integration | None:
    """Validate one integration, dropping only the nested records that fail.

    Nested collections are validated ELEMENT BY ELEMENT first, so a single
    malformed journey does not take its integration -- and its participants,
    interfaces, mappings and findings -- down with it.
    """

    if not isinstance(raw, dict):
        rejected.append(
            RejectedRecord(path=path, kind="integration", label="", reason="not a JSON object")
        )
        return None
    cleaned = dict(raw)
    for collection_name, record_type in _COLLECTION_TYPES.items():
        items = cleaned.get(collection_name)
        if items is None:
            continue
        if not isinstance(items, list):
            rejected.append(
                RejectedRecord(
                    path=f"{path}/{collection_name}",
                    kind=collection_name,
                    label="",
                    reason="not a list",
                )
            )
            cleaned[collection_name] = []
            continue
        cleaned[collection_name] = _accept_records(
            items,
            record_type,
            f"{path}/{collection_name}",
            collection_name,
            rejected,
        )
    try:
        return Integration.model_validate(cleaned)
    except ValidationError as exc:
        rejected.append(
            RejectedRecord(
                path=path,
                kind="integration",
                label=_raw_label(raw),
                reason=_validation_reason(exc),
            )
        )
        return None


def _accept_records(
    items: list[Any],
    record_type: type[DurableModel],
    path: str,
    kind: str,
    rejected: list[RejectedRecord],
) -> list[dict[str, Any]]:
    """Keep the elements that validate, record the ones that do not."""

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            rejected.append(
                RejectedRecord(path=item_path, kind=kind, label="", reason="not a JSON object")
            )
            continue
        item = _clean_nested(item, kind, item_path, rejected)
        try:
            record = record_type.model_validate(item)
        except ValidationError as exc:
            rejected.append(
                RejectedRecord(
                    path=item_path,
                    kind=kind,
                    label=_raw_label(item),
                    reason=_validation_reason(exc),
                )
            )
            continue
        record_id = getattr(record, "id", "")
        if record_id in seen:
            # Deduplicated here rather than left to the model's uniqueness
            # check, which would reject the whole integration for one repeat.
            rejected.append(
                RejectedRecord(
                    path=item_path,
                    kind=kind,
                    label=_raw_label(item),
                    reason=f"duplicate id {record_id!r}",
                )
            )
            continue
        seen.add(record_id)
        accepted.append(record.model_dump(mode="python"))
    return accepted


#: Collections that themselves contain records, so partial acceptance reaches
#: all the way down. One malformed hand-off must cost that hand-off, not the
#: whole journey it belongs to -- a journey is where an investigation's work
#: accumulates.
_NESTED_COLLECTIONS: dict[str, tuple[str, str, type[DurableModel]]] = {
    "journeys": ("steps", "journey step", JourneyStep),
}


def _clean_nested(
    item: dict[str, Any],
    kind: str,
    path: str,
    rejected: list[RejectedRecord],
) -> dict[str, Any]:
    """Apply partial acceptance to a record's own nested record list."""

    nested = _NESTED_COLLECTIONS.get(kind)
    if nested is None:
        return item
    field_name, nested_kind, record_type = nested
    items = item.get(field_name)
    if items is None:
        return item
    cleaned = dict(item)
    if not isinstance(items, list):
        rejected.append(
            RejectedRecord(
                path=f"{path}/{field_name}", kind=nested_kind, label="", reason="not a list"
            )
        )
        cleaned[field_name] = []
        return cleaned
    cleaned[field_name] = _accept_records(
        items, record_type, f"{path}/{field_name}", nested_kind, rejected
    )
    return cleaned


def _raw_label(raw: dict[str, Any]) -> str:
    """A best-effort human handle for a record that failed validation."""

    for key in ("id", "label", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return ""


def _validation_reason(exc: ValidationError) -> str:
    """Field names and pydantic's own message. Never the offending value.

    ``ValidationError.errors()`` carries an ``input`` key holding the rejected
    value, which came out of a client's estate; only ``loc`` and ``msg`` are
    safe to repeat.
    """

    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)[:_MAX_REASON_CHARS] or "invalid record"


def _document_path(value: str) -> str:
    """Normalise one published-document reference, or refuse it.

    Accepts the path as the publisher records it (relative to ``output/``) and
    tolerates the ``output/`` prefix the agent naturally types, because that is
    the same file.  Refuses anything that is absolute, escapes upwards, or
    names one of the workspace's read-only roots -- a client document reference
    pointing at ``scan/`` is an internal-path leak waiting to be rendered.
    """

    text = _require_text(value).replace("\\", "/")
    # NOT `lstrip("./")`, which strips a CHARACTER SET: it turned the absolute
    # "/etc/passwd" into the innocent-looking relative "etc/passwd".
    while text.startswith("./"):
        text = text[2:]
    while text.startswith(_OUTPUT_PREFIX):
        text = text[len(_OUTPUT_PREFIX) :]
    if not text or text.startswith("/") or ".." in text.split("/"):
        raise ValueError(f"{value!r} is not a published document path")
    head = text.split("/", 1)[0]
    if head in {"scan", "facts", "notes", "runtime", "instructions", "_agent_control"}:
        raise ValueError(f"{value!r} names workspace evidence rather than a published document")
    return text


def _require_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _unique_text(values: list[str], field_name: str) -> list[str]:
    cleaned = [_require_text(value) for value in values]
    duplicate_values = _duplicates(cleaned)
    if duplicate_values:
        raise ValueError(f"duplicate values in {field_name}: {', '.join(duplicate_values)}")
    return cleaned


def _duplicates(values: Any) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _merge_models(current: _RecordT, incoming: _RecordT) -> _RecordT:
    if type(current) is not type(incoming):
        raise TypeError(f"cannot merge {type(current).__name__} with {type(incoming).__name__}")
    values = current.model_dump(mode="python")
    for field_name in incoming.model_fields_set:
        if field_name == "id":
            continue
        incoming_value = getattr(incoming, field_name)
        current_value = getattr(current, field_name)
        if isinstance(incoming_value, list) and isinstance(current_value, list):
            if incoming_value and isinstance(incoming_value[0], BaseModel):
                by_id = {record.id: record for record in current_value}
                for record in incoming_value:
                    by_id[record.id] = (
                        _merge_models(by_id[record.id], record)
                        if record.id in by_id
                        else record.model_copy(deep=True)
                    )
                values[field_name] = list(by_id.values())
            else:
                values[field_name] = list(dict.fromkeys([*current_value, *incoming_value]))
        else:
            values[field_name] = incoming_value
    return type(current).model_validate(values)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        canonical = [_canonicalize(item) for item in value]
        if all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in canonical):
            return sorted(canonical, key=lambda item: item["id"])
        if all(isinstance(item, str) for item in canonical):
            return sorted(canonical)
        return canonical
    return value


__all__ = [
    "JOURNEY_STEP_KINDS",
    "SCHEMA_VERSION",
    "CatalogueEntry",
    "CompletionSummary",
    "ConfigurationRef",
    "Dependency",
    "DesignDifference",
    "EvidenceRef",
    "Finding",
    "ImplementingUnit",
    "Integration",
    "IntegrationKnowledgeBase",
    "IntegrationModelError",
    "Interface",
    "Journey",
    "JourneyStep",
    "JourneyTransition",
    "KnowledgeState",
    "Mapping",
    "ModelLoadReport",
    "Participant",
    "RejectedRecord",
    "Risk",
    "RuntimeRef",
    "StructuralIssue",
    "Unknown",
    "integration_filename",
    "load_report",
    "load_workspace_model",
    "stable_id",
]
