"""Stage A of topology synthesis: the business-level flow extraction contract.

The previous single-shot contract asked one model call for the semantics AND the
bookkeeping -- ``sys-`` ids, ``ClaimStatus`` enums, 20-char ``artifact:`` hashes,
identity cross-links, coverage numbers -- and rejected the whole object when any
of it was wrong. Verified on the werkorder run: ``ETTM_part2`` produced 14
systems / 8 streams / 8 delivery paths / 5 business objects and lost all of it
to three bookkeeping defects (every claim at ``status: observed``, no
``source_identity_evidence_id``, one solution with empty ``artifact_ids``). The
published fallback had 4 systems / 3 streams / 0 paths / 0 objects.

This module is the contract that produced the hand-curated werkorder topology
(``output/werkorder/topology/_evidence_flows/*.json``). The model states the
architecture in business language and names implementing designs by their plain
job names; :mod:`assembly` owns every derived identifier, status and citation.

Parsing is deliberately lenient at the ITEM level and strict at the FIELD level:
a flow missing a source system is dropped with a recorded reason rather than
failing the batch, because one bad flow used to cost the entire topology. The
same holds one level down: each condition, business rule, transformation,
mapping, control, risk, open question and conflict is validated on its own, so
a malformed optional item costs only itself.

The business layer (per flow: ``conditions``, ``key_form``, ``verdict``,
``generation``, ``business_rules``, typed transformations; top level:
``controls``, ``risks``, ``open_questions``, ``conflicts``, ``systems[].owner``)
stays semantic: plain names, confidence words, flow names as references and
doc citations. Ids, statuses and cross-links remain assembly's job.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.services.business_topology.validation import _strip_json_fence

#: Confidence vocabulary. Words, not numbers or ``ClaimStatus`` values -- the
#: model picking ``observed`` vs ``inferred`` is the single most frequent
#: verified rejection cause, and it is a policy decision Python should own.
ExtractionConfidence = Literal["certain", "high", "medium", "low"]

#: Business-level system kinds. Mapped to ``BusinessSystem.kind`` in assembly.
ExtractionSystemKind = Literal[
    "application",
    "database",
    "external_service",
    "file",
    "queue",
]

#: Closed vocabularies mirrored from the published contract (``models.py``).
#: A value outside them is normalised to ``unknown`` (or the contract's own
#: "not established" default) in :func:`_normalize_raw` -- a downgrade, never
#: an upgrade, so model drift costs the label and not the item.
ConditionKind = Literal[
    "trigger", "filter", "ordering", "schedule", "disabled", "guard", "unknown"
]
RuleKind = Literal[
    "value_mapping",
    "precedence",
    "derivation",
    "selection",
    "feedback",
    "ordering",
    "disabled",
    "threshold",
    "unknown",
]
TransformationKind = Literal[
    "field_mapping", "value_domain", "derived_rule", "constant_set", "unknown"
]
ReachesTarget = Literal["yes", "no", "partial", "disabled", "unknown"]
RiskSeverity = Literal["low", "medium", "high", "critical", "unknown"]
ConflictType = Literal[
    "supporting_vs_implementation",
    "runtime_vs_implementation",
    "supporting_sources_disagree",
    "unresolved",
]

_MAX_SYSTEM_NAME_CHARS = 38

#: Phrases a model uses when it means "I could not determine this". Treated as
#: an absent system rather than promoted to a node named "unclear".
_NON_NAMES = (
    "unclear",
    "not evidenced",
    "unspecified",
    "unknown",
    "not established",
    "not determined",
    "n/a",
    "none",
    "tbd",
)


class ExtractionModel(BaseModel):
    """Lenient base: unknown keys are ignored, not fatal.

    ``extra="forbid"`` on the published :class:`BusinessTopology` is correct --
    it is a served contract. Here it would throw away a whole extraction over
    one hallucinated key, which is the failure mode this rebuild removes.
    """

    model_config = ConfigDict(extra="ignore")


class ExtractedSystem(ExtractionModel):
    name: str
    role: str = ""
    kind: ExtractionSystemKind = "application"
    aliases: list[str] = Field(default_factory=list)
    #: The owning team, department or role, only when a document states it.
    owner: str = ""


class ExtractedTransformationMapping(ExtractionModel):
    source: str = ""
    target: str = ""
    mapping_type: Literal["direct", "value", "derived", "constant", "unknown"] = (
        "unknown"
    )
    expression: str = ""
    category: str = ""
    reaches_target: ReachesTarget = "unknown"
    note: str = ""


class ExtractedTransformation(ExtractionModel):
    name: str = ""
    description: str = ""
    kind: TransformationKind = "unknown"
    target_field: str = ""
    implementing_designs: list[str] = Field(default_factory=list)
    mappings: list[ExtractedTransformationMapping] = Field(default_factory=list)


class ExtractedPathStep(ExtractionModel):
    """One ordered hand-off in a flow's technical path.

    ``implementing_designs`` are plain design/job names. Assembly resolves them
    to ``artifact:`` ids; the model never sees or copies a hash.
    """

    name: str = ""
    description: str = ""
    mechanism: str = ""
    protocol: str = ""
    mode: str = ""
    component: str = ""
    resource: str = ""
    implementing_designs: list[str] = Field(default_factory=list)


class ExtractedCondition(ExtractionModel):
    """Whether, when or in what order a flow happens -- never a value mapping."""

    kind: ConditionKind = "unknown"
    statement: str
    evidence: str = ""


class ExtractedRule(ExtractionModel):
    """A decision the integration takes on the business's behalf.

    ``transformation`` optionally names a transformation of the SAME flow the
    rule is implemented by; assembly resolves the name to an id.
    """

    name: str = ""
    kind: RuleKind = "unknown"
    statement: str
    inputs: list[str] = Field(default_factory=list)
    output: str = ""
    owner: str = ""
    owner_question: str = ""
    transformation: str = ""
    confidence: ExtractionConfidence | None = None
    evidence: str = ""


class ExtractedFlow(ExtractionModel):
    name: str = ""
    source_system: str = ""
    target_system: str = ""
    business_object: str = ""
    carries: str = ""
    mechanism: str = ""
    requiredness: Literal["required", "optional", "conditional", "not_established"] = (
        "not_established"
    )
    #: Which platform generation / era the documents place this flow in.
    generation: str = ""
    #: The business key the exchange is matched on, as documented.
    key_form: str = ""
    #: The short assessment the documents state (keep / duplicate / obsolete).
    verdict: str = ""
    implementing_designs: list[str] = Field(default_factory=list)
    technical_path: list[ExtractedPathStep] = Field(default_factory=list)
    transformations: list[ExtractedTransformation] = Field(default_factory=list)
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    business_rules: list[ExtractedRule] = Field(default_factory=list)
    confidence: ExtractionConfidence = "medium"
    evidence: str = ""


class ExtractedExclusion(ExtractionModel):
    name: str
    reason: str = ""


class ExtractedControl(ExtractionModel):
    """A safeguard the documents describe (validation, monitoring, filter)."""

    name: str
    description: str = ""
    control_type: str = ""
    flows: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence | None = None
    evidence: str = ""


class ExtractedRisk(ExtractionModel):
    name: str
    description: str = ""
    severity: RiskSeverity = "unknown"
    flows: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence | None = None
    evidence: str = ""


class ExtractedOpenQuestion(ExtractionModel):
    """Something the documents explicitly leave open."""

    question: str
    name: str = ""
    flows: list[str] = Field(default_factory=list)
    evidence: str = ""


class ExtractedConflict(ExtractionModel):
    """A disagreement the documents report between two accounts of a flow."""

    description: str
    name: str = ""
    conflict_type: ConflictType = "unresolved"
    flows: list[str] = Field(default_factory=list)
    evidence: str = ""


class FlowExtraction(ExtractionModel):
    """One sub-project's extracted architecture."""

    subproject: str = ""
    systems: list[ExtractedSystem] = Field(default_factory=list)
    flows: list[ExtractedFlow] = Field(default_factory=list)
    excluded: list[ExtractedExclusion] = Field(default_factory=list)
    controls: list[ExtractedControl] = Field(default_factory=list)
    risks: list[ExtractedRisk] = Field(default_factory=list)
    open_questions: list[ExtractedOpenQuestion] = Field(default_factory=list)
    conflicts: list[ExtractedConflict] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.systems and not self.flows


def is_meaningful_system_name(name: str | None) -> bool:
    """Return whether ``name`` can stand as a topology node.

    Rejects the "I don't know" vocabulary and prose-length strings. A long
    sentence in ``source_system`` is a model narrating, not naming, and
    promoting it produces the wrong-shape landscape the rebuild targets.
    """

    raw = (name or "").strip()
    if not raw or len(raw) > _MAX_SYSTEM_NAME_CHARS:
        return False
    lowered = raw.lower()
    return not any(token in lowered for token in _NON_NAMES)


def _coerce_str_list(value: Any) -> list[str]:
    """Accept a list, a comma/plus-separated string, or a single string.

    Verified model drift: ``implementing_designs`` arrives as
    ``"jobA, jobB"`` often enough that failing the flow over it would discard
    real architecture for a formatting choice.
    """

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = re.split(r"[,;]|\s\+\s", value)
        return [part.strip() for part in parts if part.strip()]
    return []


def _vocab(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """Normalise a closed-vocabulary word; anything else becomes ``default``."""

    word = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    return word if word in allowed else default


def _args(literal: Any) -> tuple[str, ...]:
    return tuple(literal.__args__)


_REACHES_TARGET_WORDS = {
    "true": "yes",
    "false": "no",
    "y": "yes",
    "n": "no",
}


def _normalize_raw(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair the shapes models emit before schema validation.

    Everything here is a formatting concession, never a semantic one: no field
    is invented and no claim is upgraded. An unrecognised enum word becomes the
    contract's "not established" value, which is always the weaker reading.
    """

    normalized = dict(payload)
    confidence_words = _args(ExtractionConfidence)
    for flow in _dicts(normalized.get("flows")):
        flow["implementing_designs"] = _coerce_str_list(flow.get("implementing_designs"))
        flow["confidence"] = _vocab(flow.get("confidence"), confidence_words, "medium")
        flow["requiredness"] = _vocab(
            flow.get("requiredness"),
            ("required", "optional", "conditional", "not_established"),
            "not_established",
        )
        for step in _dicts(flow.get("technical_path")):
            step["implementing_designs"] = _coerce_str_list(
                step.get("implementing_designs")
            )
        for transformation in _dicts(flow.get("transformations")):
            transformation["implementing_designs"] = _coerce_str_list(
                transformation.get("implementing_designs")
            )
            transformation["kind"] = _vocab(
                transformation.get("kind"), _args(TransformationKind), "unknown"
            )
            for mapping in _dicts(transformation.get("mappings")):
                mapping["mapping_type"] = _vocab(
                    mapping.get("mapping_type"),
                    ("direct", "value", "derived", "constant", "unknown"),
                    "unknown",
                )
                reaches = mapping.get("reaches_target")
                if isinstance(reaches, bool):
                    reaches = "yes" if reaches else "no"
                reaches = _REACHES_TARGET_WORDS.get(
                    str(reaches or "").strip().lower(), reaches
                )
                mapping["reaches_target"] = _vocab(
                    reaches, _args(ReachesTarget), "unknown"
                )
        for condition in _dicts(flow.get("conditions")):
            condition["kind"] = _vocab(condition.get("kind"), _args(ConditionKind), "unknown")
        for rule in _dicts(flow.get("business_rules")):
            rule["kind"] = _vocab(rule.get("kind"), _args(RuleKind), "unknown")
            rule["inputs"] = _coerce_str_list(rule.get("inputs"))
            if rule.get("confidence") is not None:
                rule["confidence"] = _vocab(rule.get("confidence"), confidence_words, "medium")
    for system in _dicts(normalized.get("systems")):
        system["aliases"] = _coerce_str_list(system.get("aliases"))
        system["kind"] = _vocab(system.get("kind"), _args(ExtractionSystemKind), "application")
    for key in ("controls", "risks", "open_questions", "conflicts"):
        for item in _dicts(normalized.get(key)):
            item["flows"] = _coerce_str_list(item.get("flows"))
            if item.get("confidence") is not None:
                item["confidence"] = _vocab(item.get("confidence"), confidence_words, "medium")
    for risk in _dicts(normalized.get("risks")):
        risk["severity"] = _vocab(risk.get("severity"), _args(RiskSeverity), "unknown")
    for conflict in _dicts(normalized.get("conflicts")):
        conflict["conflict_type"] = _vocab(
            conflict.get("conflict_type"), _args(ConflictType), "unresolved"
        )
    excluded = normalized.get("excluded")
    if isinstance(excluded, list):
        normalized["excluded"] = [
            {"name": str(item), "reason": ""} if isinstance(item, str) else item
            for item in excluded
        ]
    return normalized


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _validate_items(
    raw: Any,
    model: type[_ModelT],
    *,
    label: str,
    dropped: list[str],
) -> list[_ModelT]:
    """Validate a list item by item: a malformed item costs only itself.

    This is the ITEM-level leniency the contract promises. Validating the whole
    payload in one call made one bad optional rule or mapping reject the entire
    extraction -- the same all-or-nothing loss the two-stage design removed.
    """

    if raw is None:
        return []
    if not isinstance(raw, list):
        dropped.append(f"{label}: expected a list, got {type(raw).__name__}")
        return []
    kept: list[_ModelT] = []
    for index, item in enumerate(raw, start=1):
        name = item.get("name") if isinstance(item, dict) else None
        tag = f"{label} {name!r}" if name else f"{label} #{index}"
        if not isinstance(item, dict):
            dropped.append(f"{tag}: not an object")
            continue
        try:
            kept.append(model.model_validate(item))
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
            dropped.append(f"{tag}: invalid field(s) {', '.join(fields) or '?'}")
    return kept


def _nonblank(items: list[_ModelT], field: str, label: str, dropped: list[str]) -> list[_ModelT]:
    """Drop items whose load-bearing text field is empty after stripping."""

    kept: list[_ModelT] = []
    for item in items:
        if str(getattr(item, field) or "").strip():
            kept.append(item)
        else:
            dropped.append(f"{label} {getattr(item, 'name', '') or '?'!r}: empty {field}")
    return kept


def _parse_flow(raw: dict[str, Any], label: str, dropped: list[str]) -> ExtractedFlow | None:
    body = dict(raw)
    nested = {
        key: body.pop(key, None)
        for key in ("technical_path", "transformations", "conditions", "business_rules")
    }
    try:
        flow = ExtractedFlow.model_validate(body)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        dropped.append(f"flow {label!r}: invalid field(s) {', '.join(fields) or '?'}")
        return None
    flow.technical_path = _validate_items(
        nested["technical_path"], ExtractedPathStep,
        label=f"flow {label!r} path step", dropped=dropped,
    )
    transformations: list[ExtractedTransformation] = []
    for index, item in enumerate(
        nested["transformations"] if isinstance(nested["transformations"], list) else [],
        start=1,
    ):
        tag = f"flow {label!r} transformation #{index}"
        if not isinstance(item, dict):
            dropped.append(f"{tag}: not an object")
            continue
        item_body = dict(item)
        mappings = item_body.pop("mappings", None)
        parsed = _validate_items([item_body], ExtractedTransformation, label=tag, dropped=dropped)
        if not parsed:
            continue
        parsed[0].mappings = _validate_items(
            mappings, ExtractedTransformationMapping, label=f"{tag} mapping", dropped=dropped
        )
        transformations.append(parsed[0])
    flow.transformations = transformations
    flow.conditions = _nonblank(
        _validate_items(
            nested["conditions"], ExtractedCondition,
            label=f"flow {label!r} condition", dropped=dropped,
        ),
        "statement", f"flow {label!r} condition", dropped,
    )
    flow.business_rules = _nonblank(
        _validate_items(
            nested["business_rules"], ExtractedRule,
            label=f"flow {label!r} rule", dropped=dropped,
        ),
        "statement", f"flow {label!r} rule", dropped,
    )
    return flow


def parse_flow_extraction(
    text: str,
    *,
    subproject: str,
) -> tuple[FlowExtraction | None, list[str]]:
    """Parse Stage A output into a :class:`FlowExtraction`.

    Returns ``(extraction, dropped)``. ``dropped`` names the items that could
    not be used and why; it is reported, not raised. ``None`` is returned only
    when there is no parseable object at all -- the one case Stage B cannot work
    from.
    """

    dropped: list[str] = []
    if not text or not text.strip():
        return None, ["model returned no output text"]
    try:
        payload = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError as exc:
        return None, [f"flow extraction is not valid JSON: {exc}"]
    if isinstance(payload, list):
        payload = {"flows": payload}
    if not isinstance(payload, dict):
        return None, ["flow extraction is not a JSON object"]
    # Tolerate a single wrapper key, the shape json_mode providers add most.
    for wrapper in ("flow_extraction", "extraction", "topology", "result"):
        if set(payload) == {wrapper} and isinstance(payload[wrapper], dict):
            payload = payload[wrapper]
            break

    raw = _normalize_raw(payload)
    extraction = FlowExtraction(
        subproject=str(raw.get("subproject") or "").strip() or subproject,
    )

    for system in _validate_items(
        raw.get("systems"), ExtractedSystem, label="system", dropped=dropped
    ):
        if is_meaningful_system_name(system.name):
            extraction.systems.append(system)
        else:
            dropped.append(f"system {system.name!r}: not a usable system name")

    raw_flows = raw.get("flows")
    if raw_flows is not None and not isinstance(raw_flows, list):
        dropped.append(f"flows: expected a list, got {type(raw_flows).__name__}")
        raw_flows = []
    for index, item in enumerate(raw_flows or [], start=1):
        label = (item.get("name") if isinstance(item, dict) else None) or f"flow #{index}"
        if not isinstance(item, dict):
            dropped.append(f"flow {label!r}: not an object")
            continue
        flow = _parse_flow(item, str(label), dropped)
        if flow is None:
            continue
        if not is_meaningful_system_name(flow.source_system):
            dropped.append(f"flow {label!r}: unusable source_system {flow.source_system!r}")
            continue
        if not is_meaningful_system_name(flow.target_system):
            dropped.append(f"flow {label!r}: unusable target_system {flow.target_system!r}")
            continue
        if flow.source_system.strip().lower() == flow.target_system.strip().lower():
            dropped.append(f"flow {label!r}: source and target are the same system")
            continue
        extraction.flows.append(flow)

    extraction.excluded = _validate_items(
        raw.get("excluded"), ExtractedExclusion, label="excluded", dropped=dropped
    )
    extraction.controls = _nonblank(
        _validate_items(raw.get("controls"), ExtractedControl, label="control", dropped=dropped),
        "name", "control", dropped,
    )
    extraction.risks = _nonblank(
        _validate_items(raw.get("risks"), ExtractedRisk, label="risk", dropped=dropped),
        "name", "risk", dropped,
    )
    extraction.open_questions = _nonblank(
        _validate_items(
            raw.get("open_questions"), ExtractedOpenQuestion,
            label="open question", dropped=dropped,
        ),
        "question", "open question", dropped,
    )
    extraction.conflicts = _nonblank(
        _validate_items(raw.get("conflicts"), ExtractedConflict, label="conflict", dropped=dropped),
        "description", "conflict", dropped,
    )
    return extraction, dropped
