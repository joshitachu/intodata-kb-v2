"""Fact-preserving response plans and deterministic user-facing rendering.

The agent may choose presentation in its existing terminal decision. It never
writes factual response sentences when a server-issued fact catalog is present.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.harness.capability_runtime import CapabilityObservation
from src.harness.workflow_projection import WorkflowCard


_MAX_FACTS = 160
_MAX_RENDER_CHARS = 19_000
_SECRET_KEY = re.compile(
    r"(?i)(authorization|credential|password|secret|token|api[_-]?key|raw|payload|prompt)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]+|sk-[a-z0-9_-]{8,}|(?:password|secret|token)\s*[:=])"
)
_REDACTIONS = (
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer|Basic)\s+\S+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"
        r"\s*[:=]\s*['\"]?[^\s,'\"}]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)

# Each output contract explicitly opts public scalar paths in. Unknown output
# types and unknown/nested keys expose no data facts; their typed summary,
# error, artifact, and evidence projections remain available.
_PUBLIC_DATA_PATHS: dict[str, frozenset[tuple[str, ...]]] = {
    "workflow_cancellation_outcome": frozenset(
        {("outcome",), ("workflow_status",), ("project_id",)}
    ),
    "documentation_generation_status": frozenset(
        {
            ("project_id",),
            ("found",),
            ("status",),
            ("doc_count",),
            ("quality_score",),
            ("run_verdict",),
            ("client_ready",),
            ("failed_gates",),
            ("error_message",),
        }
    ),
    "active_project_workflows": frozenset(
        {
            ("workflows", "*", "project_id"),
            ("workflows", "*", "display_name"),
            ("workflows", "*", "project_status"),
            ("workflows", "*", "generation_status"),
            ("workflows", "*", "generation_started_at"),
        }
    ),
    "source_options": frozenset({("options", "*", "label"), ("options", "*", "source_type")}),
    "pack_draft": frozenset({("revision",), ("status",), ("name",), ("validation", "valid")}),
    "pack_draft_validation": frozenset(
        {("revision",), ("status",), ("name",), ("validation", "valid")}
    ),
    "private_pack_version": frozenset({("name",), ("version",)}),
    "project_status": frozenset({("project_id",), ("status",), ("platform",)}),
    "source_attachment_receipt": frozenset({("project_id",), ("source_type",)}),
    "scan_workflow_receipt": frozenset({("project_id",), ("status",)}),
    "index_workflow_receipt": frozenset({("project_id",), ("status",)}),
    "workflow_trigger_receipt": frozenset({("project_id",), ("status",)}),
    "onboarding_workflow_receipt": frozenset({("project_id",), ("status",)}),
    **{
        output_type: frozenset(
            {
                ("answer_draft",),
                ("items", "*", "title"),
                ("items", "*", "summary"),
                ("items", "*", "score"),
                ("items", "*", "observed_at"),
                ("items", "*", "source_type"),
                ("items", "*", "metadata", "filename"),
                ("items", "*", "metadata", "project_id"),
                ("items", "*", "metadata", "source_layer"),
                ("items", "*", "metadata", "status"),
                ("items", "*", "metadata", "severity"),
                ("items", "*", "metadata", "role"),
                ("items", "*", "metadata", "type"),
                ("items", "*", "metadata", "modified_at"),
                ("items", "*", "metadata", "query_type"),
                ("items", "*", "metadata", "scope"),
                ("items", "*", "metadata", "size_bytes"),
                ("items", "*", "metadata", "has_pdf"),
            }
        )
        for output_type in {
            "evidence_items",
            "metadata_evidence",
            "conversation_facts",
            "investigation_answer",
        }
    },
    "documentation_pack_catalog": frozenset(
        {
            ("items", "*", "kind"),
            ("items", "*", "name"),
            ("items", "*", "version"),
            ("items", "*", "description"),
            ("items", "*", "adds"),
        }
    ),
    "documentation_change_receipt": frozenset(
        {
            ("project_id",),
            ("target_doc",),
            ("section",),
            ("status",),
            ("changed",),
        }
    ),
    "documentation_revert_receipt": frozenset(
        {("project_id",), ("target_doc",), ("status",), ("reverted",)}
    ),
}
_MANDATORY_DATA_PATHS: dict[str, frozenset[tuple[str, ...]]] = {
    "workflow_cancellation_outcome": frozenset({("outcome",), ("workflow_status",)}),
    "documentation_generation_status": frozenset({("status",), ("found",)}),
}


class ResponseFactKind(StrEnum):
    OBSERVATION_STATUS = "observation_status"
    SUMMARY = "summary"
    ERROR = "error"
    DATA = "data"
    REQUESTED_INPUT = "requested_input"
    WORKFLOW_STATUS = "workflow_status"
    WORKFLOW_SUMMARY = "workflow_summary"
    ARTIFACT_PRESENCE = "artifact_presence"
    EVIDENCE_PRESENCE = "evidence_presence"


class ResponseKind(StrEnum):
    ANSWER = "answer"
    CLARIFICATION = "clarification"
    STATUS = "status"
    ERROR = "error"
    NO_PROGRESS = "no_progress"


class SynthesisSource(StrEnum):
    AGENT_PLAN = "agent_plan"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    DIRECT = "direct"


class ResponseFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,255}$")
    kind: ResponseFactKind
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=12_000)
    mandatory: bool = False
    workflow_run_id: int | None = Field(default=None, ge=1)


class ResponseSynthesisPlan(BaseModel):
    """Fact selection accepted from the agent's terminal decision.

    The server deliberately does not provide selectable conversational leads.
    Facts are rendered directly so application-owned prose cannot masquerade as
    an answer from the agent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_ids: list[str] = Field(default_factory=list, max_length=_MAX_FACTS)

    @model_validator(mode="after")
    def unique_fact_ids(self) -> "ResponseSynthesisPlan":
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("response fact IDs must be unique")
        return self


class TypedAgentResponse(BaseModel):
    """Versioned response persisted and reused by every transport."""

    model_config = ConfigDict(extra="forbid")

    response_type: str = Field(default="agent_response_v1", frozen=True)
    kind: ResponseKind
    content: str = Field(max_length=20_000)
    synthesis_source: SynthesisSource
    facts: list[ResponseFact] = Field(default_factory=list, max_length=_MAX_FACTS)
    artifact_links: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    evidence_refs: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    workflow_cards: list[WorkflowCard] = Field(default_factory=list, max_length=20)
    stop_reason: str = Field(min_length=1, max_length=80)
    factual_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_response_fact_catalog(
    *,
    observations: Sequence[CapabilityObservation],
    conversation_state: Mapping[str, Any],
    workflow_state: Sequence[Mapping[str, Any]],
) -> list[ResponseFact]:
    facts: list[ResponseFact] = []
    for index, observation in enumerate(observations):
        prefix = f"observation:{index}"
        facts.append(
            ResponseFact(
                fact_id=f"{prefix}:status",
                kind=ResponseFactKind.OBSERVATION_STATUS,
                label="Capability result",
                value=observation.status.value,
                mandatory=True,
                workflow_run_id=_workflow_id(observation.workflow_ref),
            )
        )
        if observation.summary:
            facts.append(
                ResponseFact(
                    fact_id=f"{prefix}:summary",
                    kind=ResponseFactKind.SUMMARY,
                    label="Result",
                    value=_redact(observation.summary, 4_000),
                    mandatory=True,
                    workflow_run_id=_workflow_id(observation.workflow_ref),
                )
            )
        if observation.error_message:
            facts.append(
                ResponseFact(
                    fact_id=f"{prefix}:error",
                    kind=ResponseFactKind.ERROR,
                    label="Error",
                    value=_redact(observation.error_message, 2_000),
                    mandatory=True,
                    workflow_run_id=_workflow_id(observation.workflow_ref),
                )
            )
        facts.extend(
            _data_facts(
                prefix,
                observation.data,
                observation.workflow_ref,
                output_type=observation.output_type,
            )
        )
        safe_artifacts = _safe_links(observation.artifact_links)
        if safe_artifacts:
            facts.append(
                ResponseFact(
                    fact_id=f"{prefix}:artifacts",
                    kind=ResponseFactKind.ARTIFACT_PRESENCE,
                    label="Artifacts",
                    value=str(len(safe_artifacts)),
                    mandatory=True,
                    workflow_run_id=_workflow_id(observation.workflow_ref),
                )
            )
        safe_observation_evidence = _safe_evidence(observation.evidence_refs)
        if safe_observation_evidence:
            facts.append(
                ResponseFact(
                    fact_id=f"{prefix}:evidence",
                    kind=ResponseFactKind.EVIDENCE_PRESENCE,
                    label="Evidence references",
                    value=str(len(safe_observation_evidence)),
                    mandatory=True,
                    workflow_run_id=_workflow_id(observation.workflow_ref),
                )
            )

    requested = conversation_state.get("requested_inputs")
    if isinstance(requested, list):
        for index, item in enumerate(requested[:30]):
            if not isinstance(item, Mapping):
                continue
            prompt = item.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            options = item.get("options")
            option_text = (
                f" Options: {', '.join(_redact(str(value), 160) for value in options[:20])}."
                if isinstance(options, list) and options
                else ""
            )
            facts.append(
                ResponseFact(
                    fact_id=f"requested_input:{index}",
                    kind=ResponseFactKind.REQUESTED_INPUT,
                    label=_redact(str(item.get("field") or "Required input"), 200),
                    value=f"{_redact(prompt, 500)}{option_text}",
                    mandatory=True,
                )
            )

    forced_workflow_ids = _forced_workflow_ids(conversation_state)
    for index, workflow in enumerate(workflow_state[:20]):
        if not isinstance(workflow, Mapping):
            continue
        run_id = workflow.get("workflow_run_id")
        status = workflow.get("status")
        if not isinstance(run_id, int) or not isinstance(status, str):
            continue
        if run_id not in forced_workflow_ids:
            continue
        project = workflow.get("project_id")
        label = f"Workflow for {project}" if isinstance(project, str) else "Workflow"
        facts.append(
            ResponseFact(
                fact_id=f"workflow:{run_id}:status",
                kind=ResponseFactKind.WORKFLOW_STATUS,
                label=label,
                value=_clean(status, 80),
                mandatory=True,
                workflow_run_id=run_id,
            )
        )
        summary = workflow.get("error_message") or workflow.get("output_summary")
        if isinstance(summary, str) and summary.strip():
            facts.append(
                ResponseFact(
                    fact_id=f"workflow:{run_id}:summary",
                    kind=(
                        ResponseFactKind.ERROR
                        if workflow.get("error_message")
                        else ResponseFactKind.WORKFLOW_SUMMARY
                    ),
                    label=label,
                    value=_redact(summary, 2_000),
                    mandatory=bool(workflow.get("error_message")),
                    workflow_run_id=run_id,
                )
            )
    return _dedupe(facts)[:_MAX_FACTS]


def render_typed_response(
    *,
    action: str | None,
    content: str | None,
    plan: ResponseSynthesisPlan | None,
    facts: Sequence[ResponseFact],
    state_version: str,
    stop_reason: str,
    workflow_cards: Sequence[WorkflowCard] = (),
    observations: Sequence[CapabilityObservation] = (),
    no_progress: bool = False,
) -> TypedAgentResponse:
    catalog = {fact.fact_id: fact for fact in facts}
    cards = list(workflow_cards)
    card_facts = _card_facts(cards)
    all_facts = _dedupe([*facts, *card_facts])[:_MAX_FACTS]
    fingerprint = factual_fingerprint(all_facts, state_version=state_version)
    if not all_facts:
        direct = (
            _stop_fallback(stop_reason)
            if no_progress
            else (content or _stop_fallback(stop_reason)).strip()
        )
        return TypedAgentResponse(
            kind=(
                ResponseKind.NO_PROGRESS
                if no_progress
                else (ResponseKind.CLARIFICATION if action == "clarify" else ResponseKind.ANSWER)
            ),
            content=direct,
            synthesis_source=SynthesisSource.DIRECT,
            stop_reason=stop_reason,
            factual_fingerprint=fingerprint,
        )

    valid_plan = _valid_plan(plan, catalog)
    selected = _selected_facts(plan if valid_plan else None, all_facts)
    rendered = _render_facts(selected)
    artifacts = _safe_links(
        [item for observation in observations for item in observation.artifact_links]
        + [item for card in cards for item in card.artifact_links]
    )
    evidence = _safe_evidence(
        [item for observation in observations for item in observation.evidence_refs]
    )
    return TypedAgentResponse(
        kind=(ResponseKind.NO_PROGRESS if no_progress else _response_kind(action, selected)),
        content=rendered,
        synthesis_source=(
            SynthesisSource.AGENT_PLAN if valid_plan else SynthesisSource.DETERMINISTIC_FALLBACK
        ),
        facts=list(selected),
        artifact_links=artifacts,
        evidence_refs=evidence,
        workflow_cards=cards,
        stop_reason=stop_reason,
        factual_fingerprint=fingerprint,
    )


def factual_fingerprint(facts: Sequence[ResponseFact], *, state_version: str) -> str:
    payload = {
        "state_version": state_version,
        "facts": sorted(((fact.fact_id, fact.value) for fact in facts), key=lambda item: item[0]),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def referenced_workflow_ids(facts: Sequence[ResponseFact]) -> list[int]:
    return list(dict.fromkeys(fact.workflow_run_id for fact in facts if fact.workflow_run_id))


def _valid_plan(plan: ResponseSynthesisPlan | None, catalog: Mapping[str, ResponseFact]) -> bool:
    if plan is None or any(fact_id not in catalog for fact_id in plan.fact_ids):
        return False
    selected = set(plan.fact_ids)
    return all(not fact.mandatory or fact.fact_id in selected for fact in catalog.values())


def _selected_facts(
    plan: ResponseSynthesisPlan | None, facts: Sequence[ResponseFact]
) -> list[ResponseFact]:
    by_id = {fact.fact_id: fact for fact in facts}
    if plan is None:
        return list(facts)
    selected = [by_id[fact_id] for fact_id in plan.fact_ids if fact_id in by_id]
    selected_ids = {fact.fact_id for fact in selected}
    selected.extend(fact for fact in facts if fact.mandatory and fact.fact_id not in selected_ids)
    return selected


def _render_facts(facts: Sequence[ResponseFact]) -> str:
    lines: list[str] = []
    used = 0
    omitted = False
    has_substantive = any(
        fact.kind
        in {
            ResponseFactKind.SUMMARY,
            ResponseFactKind.ERROR,
            ResponseFactKind.DATA,
            ResponseFactKind.REQUESTED_INPUT,
            ResponseFactKind.WORKFLOW_STATUS,
            ResponseFactKind.WORKFLOW_SUMMARY,
        }
        for fact in facts
    )
    for fact in facts:
        if (
            fact.kind is ResponseFactKind.OBSERVATION_STATUS
            and has_substantive
            and fact.value in {"succeeded", "triggered"}
        ):
            continue
        if fact.kind in {
            ResponseFactKind.SUMMARY,
            ResponseFactKind.ERROR,
            ResponseFactKind.REQUESTED_INPUT,
            ResponseFactKind.WORKFLOW_SUMMARY,
        }:
            sentence = fact.value
        elif fact.kind is ResponseFactKind.DATA and fact.fact_id.endswith(":data:answer_draft"):
            sentence = fact.value
        elif fact.kind is ResponseFactKind.ARTIFACT_PRESENCE:
            sentence = f"{fact.value} artifact{'s' if fact.value != '1' else ''} available"
        elif fact.kind is ResponseFactKind.EVIDENCE_PRESENCE:
            sentence = f"Grounded in {fact.value} evidence source{'s' if fact.value != '1' else ''}"
        else:
            sentence = f"{_display_label(fact)}: {_display_value(fact.value)}"
        sentence = sentence.strip()
        if sentence and sentence[-1] not in ".!?":
            sentence += "."
        if sentence and sentence not in lines:
            addition = len(sentence) + (2 if lines else 0)
            if used + addition <= _MAX_RENDER_CHARS:
                lines.append(sentence)
                used += addition
            else:
                omitted = True
    if omitted:
        note = "Additional authoritative facts are attached to this response."
        if used + len(note) + 2 <= _MAX_RENDER_CHARS:
            lines.append(note)
    return "\n\n".join(lines)


def _response_kind(action: str | None, facts: Sequence[ResponseFact]) -> ResponseKind:
    if any(fact.kind is ResponseFactKind.ERROR for fact in facts):
        return ResponseKind.ERROR
    if action == "clarify" or any(fact.kind is ResponseFactKind.REQUESTED_INPUT for fact in facts):
        return ResponseKind.CLARIFICATION
    if any(fact.kind is ResponseFactKind.WORKFLOW_STATUS for fact in facts):
        return ResponseKind.STATUS
    return ResponseKind.ANSWER


def _data_facts(
    prefix: str,
    value: Any,
    workflow_ref: Mapping[str, Any] | None,
    path: tuple[str, ...] = (),
    depth: int = 0,
    output_type: str | None = None,
) -> list[ResponseFact]:
    allowed = _PUBLIC_DATA_PATHS.get(output_type or "", frozenset())
    if not allowed or depth > 4 or len(path) > 5:
        return []
    if path and _SECRET_KEY.search(path[-1]):
        return []
    if isinstance(value, Mapping):
        facts: list[ResponseFact] = []
        for key, item in list(value.items())[:30]:
            if isinstance(key, str):
                candidate = (*path, key)
                if not _path_prefix_allowed(candidate, allowed):
                    continue
                facts.extend(
                    _data_facts(
                        prefix,
                        item,
                        workflow_ref,
                        (*path, key),
                        depth + 1,
                        output_type,
                    )
                )
        return facts
    if isinstance(value, list):
        if all(isinstance(item, (str, int, float, bool)) for item in value[:20]):
            value = ", ".join(str(item) for item in value[:20])
        else:
            facts = []
            for index, item in enumerate(value[:20]):
                candidate = (*path, str(index))
                if not _path_prefix_allowed(candidate, allowed):
                    continue
                facts.extend(
                    _data_facts(
                        prefix,
                        item,
                        workflow_ref,
                        (*path, str(index)),
                        depth + 1,
                        output_type,
                    )
                )
            return facts
    if (
        not path
        or not isinstance(value, (str, int, float, bool))
        or not _path_exact_allowed(path, allowed)
    ):
        return []
    limit = 8_000 if path == ("answer_draft",) else (2_000 if path[-1] == "summary" else 1_000)
    text = _redact(str(value), limit)
    if not text or _SECRET_VALUE.search(text):
        return []
    safe_path = ".".join(re.sub(r"[^a-z0-9_-]", "_", item.lower()) for item in path)
    return [
        ResponseFact(
            fact_id=f"{prefix}:data:{safe_path}",
            kind=ResponseFactKind.DATA,
            label=path[-1].replace("_", " ").title(),
            value=text,
            mandatory=_path_exact_allowed(
                path, _MANDATORY_DATA_PATHS.get(output_type or "", frozenset())
            ),
            workflow_run_id=_workflow_id(workflow_ref),
        )
    ]


def _card_facts(cards: Sequence[WorkflowCard]) -> list[ResponseFact]:
    facts: list[ResponseFact] = []
    for card in cards:
        label = f"Workflow for {card.project_id}" if card.project_id else card.label
        facts.append(
            ResponseFact(
                fact_id=f"workflow:{card.workflow_run_id}:canonical_status",
                kind=ResponseFactKind.WORKFLOW_STATUS,
                label=label,
                value=card.display_label,
                mandatory=True,
                workflow_run_id=card.workflow_run_id,
            )
        )
        if card.error_message:
            facts.append(
                ResponseFact(
                    fact_id=f"workflow:{card.workflow_run_id}:canonical_error",
                    kind=ResponseFactKind.ERROR,
                    label=label,
                    value=card.error_message,
                    mandatory=True,
                    workflow_run_id=card.workflow_run_id,
                )
            )
        elif card.summary:
            facts.append(
                ResponseFact(
                    fact_id=f"workflow:{card.workflow_run_id}:canonical_summary",
                    kind=ResponseFactKind.WORKFLOW_SUMMARY,
                    label=label,
                    value=card.summary,
                    mandatory=True,
                    workflow_run_id=card.workflow_run_id,
                )
            )
    return facts


def _safe_links(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for item in raw[:50]:
        url = item.get("url") or item.get("path")
        label = item.get("label") or item.get("name")
        if (
            not isinstance(url, str)
            or not isinstance(label, str)
            or _SECRET_KEY.search(url)
            or _SECRET_VALUE.search(url)
        ):
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"", "http", "https"}:
            continue
        if parsed.scheme == "" and not url.startswith("/"):
            continue
        links.append(
            {
                "label": _safe_public_label(label, 200, fallback="Artifact"),
                "url": url[:2_000],
                "kind": _safe_public_label(item.get("kind") or "artifact", 80, fallback="artifact"),
            }
        )
    return list({item["url"]: item for item in links}.values())


def _safe_evidence(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in raw[:100]:
        ref_id = item.get("id") or item.get("evidence_id") or item.get("document_id")
        if not isinstance(ref_id, (str, int)) or _SECRET_VALUE.search(str(ref_id)):
            continue
        refs.append(
            {
                "id": str(ref_id)[:255],
                "label": _redact(
                    str(item.get("label") or item.get("display_name") or "Evidence"),
                    200,
                ),
            }
        )
    return refs


def _workflow_id(value: Mapping[str, Any] | None) -> int | None:
    raw = value.get("workflow_run_id") if value else None
    return raw if isinstance(raw, int) and raw > 0 else None


def _forced_workflow_ids(conversation_state: Mapping[str, Any]) -> set[int]:
    tasks = conversation_state.get("active_tasks")
    if not isinstance(tasks, list):
        return set()
    return {
        run_id
        for task in tasks[:30]
        if isinstance(task, Mapping)
        and isinstance((run_id := task.get("workflow_run_id")), int)
        and run_id > 0
    }


def _dedupe(facts: Sequence[ResponseFact]) -> list[ResponseFact]:
    return list({fact.fact_id: fact for fact in facts}.values())


def _clean(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _display_label(fact: ResponseFact) -> str:
    if fact.kind is ResponseFactKind.OBSERVATION_STATUS:
        return "Status"
    label = fact.label.replace("_", " ")
    aliases = {
        "Project Id": "Project",
        "Generation Status": "Documentation status",
        "Project Status": "Project status",
        "Workflow Status": "Workflow status",
        "Run Verdict": "Result",
    }
    return aliases.get(label, label)


def _display_value(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
        return value.replace("_", " ").replace("-", " ").capitalize()
    return value


def _redact(value: str, limit: int) -> str:
    redacted = value
    for pattern in _REDACTIONS:
        redacted = pattern.sub("[redacted]", redacted)
    return _clean(redacted, limit)


def _safe_public_label(value: Any, limit: int, *, fallback: str) -> str:
    text = str(value).strip()
    if not text or _SECRET_KEY.search(text) or _SECRET_VALUE.search(text):
        return fallback
    return _redact(text, limit)


def _path_prefix_allowed(candidate: tuple[str, ...], allowed: frozenset[tuple[str, ...]]) -> bool:
    return any(
        len(path) >= len(candidate)
        and all(expected == "*" or expected == actual for expected, actual in zip(path, candidate))
        for path in allowed
    )


def _path_exact_allowed(candidate: tuple[str, ...], allowed: frozenset[tuple[str, ...]]) -> bool:
    return any(
        len(path) == len(candidate)
        and all(expected == "*" or expected == actual for expected, actual in zip(path, candidate))
        for path in allowed
    )


def _stop_fallback(stop_reason: str) -> str:
    safe_reason = re.sub(r"[^a-z0-9_-]", "", stop_reason.lower()) or "unknown"
    return f"Agent turn failed ({safe_reason})."


__all__ = [
    "ResponseFact",
    "ResponseFactKind",
    "ResponseKind",
    "ResponseSynthesisPlan",
    "SynthesisSource",
    "TypedAgentResponse",
    "build_response_fact_catalog",
    "factual_fingerprint",
    "referenced_workflow_ids",
    "render_typed_response",
]
