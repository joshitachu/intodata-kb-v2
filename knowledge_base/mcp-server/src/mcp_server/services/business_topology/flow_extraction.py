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
failing the batch, because one bad flow used to cost the entire topology.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

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


class ExtractedTransformationMapping(ExtractionModel):
    source: str = ""
    target: str = ""
    mapping_type: Literal["direct", "value", "derived", "constant", "unknown"] = (
        "unknown"
    )


class ExtractedTransformation(ExtractionModel):
    name: str = ""
    description: str = ""
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
    implementing_designs: list[str] = Field(default_factory=list)
    technical_path: list[ExtractedPathStep] = Field(default_factory=list)
    transformations: list[ExtractedTransformation] = Field(default_factory=list)
    confidence: ExtractionConfidence = "medium"
    evidence: str = ""


class ExtractedExclusion(ExtractionModel):
    name: str
    reason: str = ""


class FlowExtraction(ExtractionModel):
    """One sub-project's extracted architecture."""

    subproject: str = ""
    systems: list[ExtractedSystem] = Field(default_factory=list)
    flows: list[ExtractedFlow] = Field(default_factory=list)
    excluded: list[ExtractedExclusion] = Field(default_factory=list)

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


def _normalize_raw(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair the shapes models emit before schema validation.

    Everything here is a formatting concession, never a semantic one: no field
    is invented and no claim is upgraded.
    """

    normalized = dict(payload)
    for flow in normalized.get("flows") or []:
        if not isinstance(flow, dict):
            continue
        flow["implementing_designs"] = _coerce_str_list(flow.get("implementing_designs"))
        confidence = str(flow.get("confidence") or "medium").strip().lower()
        flow["confidence"] = (
            confidence if confidence in {"certain", "high", "medium", "low"} else "medium"
        )
        for step in flow.get("technical_path") or []:
            if isinstance(step, dict):
                step["implementing_designs"] = _coerce_str_list(
                    step.get("implementing_designs")
                )
        for transformation in flow.get("transformations") or []:
            if isinstance(transformation, dict):
                transformation["implementing_designs"] = _coerce_str_list(
                    transformation.get("implementing_designs")
                )
    for system in normalized.get("systems") or []:
        if isinstance(system, dict):
            system["aliases"] = _coerce_str_list(system.get("aliases"))
            kind = str(system.get("kind") or "application").strip().lower()
            system["kind"] = (
                kind
                if kind in {"application", "database", "external_service", "file", "queue"}
                else "application"
            )
    excluded = normalized.get("excluded")
    if isinstance(excluded, list):
        normalized["excluded"] = [
            {"name": str(item), "reason": ""} if isinstance(item, str) else item
            for item in excluded
        ]
    return normalized


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

    try:
        extraction = FlowExtraction.model_validate(_normalize_raw(payload))
    except ValidationError as exc:
        return None, [f"flow extraction failed schema validation: {exc}"]

    if not extraction.subproject:
        extraction.subproject = subproject

    kept_systems: list[ExtractedSystem] = []
    for system in extraction.systems:
        if is_meaningful_system_name(system.name):
            kept_systems.append(system)
        else:
            dropped.append(f"system {system.name!r}: not a usable system name")
    extraction.systems = kept_systems

    kept_flows: list[ExtractedFlow] = []
    for index, flow in enumerate(extraction.flows, start=1):
        label = flow.name or f"flow #{index}"
        if not is_meaningful_system_name(flow.source_system):
            dropped.append(f"flow {label!r}: unusable source_system {flow.source_system!r}")
            continue
        if not is_meaningful_system_name(flow.target_system):
            dropped.append(f"flow {label!r}: unusable target_system {flow.target_system!r}")
            continue
        if flow.source_system.strip().lower() == flow.target_system.strip().lower():
            dropped.append(f"flow {label!r}: source and target are the same system")
            continue
        kept_flows.append(flow)
    extraction.flows = kept_flows
    return extraction, dropped
