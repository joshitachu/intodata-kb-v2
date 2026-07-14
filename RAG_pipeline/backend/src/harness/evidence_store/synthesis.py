"""Question-aware synthesis contracts for non-durable evidence answers.

The model may draft prose, but this module owns the answer shape, citation
integrity, evidence-policy enforcement, and deterministic aggregations.
"""

from __future__ import annotations

import re
from collections import Counter
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from src.harness.planner import TurnPlan

from .models import EvidenceSnapshot, EvidenceSourceClass

if TYPE_CHECKING:
    from .composer import InvestigationAnswer


class EvidenceAnswerStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class EvidenceAnswerCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    label: str
    uri: str | None = None
    source_class: EvidenceSourceClass


class EvidenceAnswerSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    heading: str
    body: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


class StructuredEvidenceAnswer(BaseModel):
    """Provider-neutral answer payload consumed by chat and the Slice 16 UI."""

    model_config = ConfigDict(frozen=True)

    output_contract: str
    headline: str
    summary: str
    sections: list[EvidenceAnswerSection] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    citations: list[EvidenceAnswerCitation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    answer_status: EvidenceAnswerStatus = EvidenceAnswerStatus.COMPLETE
    execution_status: str = "completed"
    domain_status: str = "unknown"
    artifacts: list[dict[str, str]] = Field(default_factory=list)

    def as_markdown(self) -> str:
        parts = [f"## {self.headline}", self.summary]
        for section in self.sections:
            if section.body:
                parts.extend((f"### {section.heading}", section.body))
            if section.rows:
                parts.extend((f"### {section.heading}", _markdown_table(section.rows)))
        if self.limitations:
            parts.extend(("### Limitations", "\n".join(f"- {item}" for item in self.limitations)))
        return "\n\n".join(part for part in parts if part.strip())


_SOURCE_POLICY = {
    EvidenceSourceClass.INDEXED_DOC: "indexed_documentation",
    EvidenceSourceClass.GENERATED_DOC: "generated_documentation",
    EvidenceSourceClass.CURRENT_RUNTIME: "runtime_evidence",
    EvidenceSourceClass.CONNECTOR: "connector_evidence",
    EvidenceSourceClass.SOURCE_TOPOLOGY: "indexed_documentation",
}
_INTERNAL_ID_RE = re.compile(
    r"\b(?:mcp|rag|runtime|external|connector|nexus)\.[a-z0-9_.-]+\b", re.IGNORECASE
)
_RAW_PAYLOAD_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_DOCUMENT_NAME_RE = re.compile(r"([^/\\\s]+\.md)\b", re.IGNORECASE)
_PROJECT_SUFFIX_RE = re.compile(r"\s*\(Project:\s*[^)]+\)\s*$", re.IGNORECASE)
_FAILURE_RE = re.compile(
    r"(?:runtime\s+failure\s*:\s*)?([A-Za-z0-9_. -]+?)\s*(?:-|:)\s*"
    r"(?:execution\s+[^ ]+\s+)?(?:failed|failure)",
    re.IGNORECASE,
)


class EvidenceAnswerSynthesizer:
    """Build and validate useful answers from normalized, allowed evidence."""

    def compose(
        self,
        *,
        question: str,
        plan: TurnPlan,
        evidence: list[EvidenceSnapshot],
        draft: str,
    ) -> StructuredEvidenceAnswer:
        usable = self._validate_evidence_policy(plan, evidence)
        contract = plan.output_contract
        citations = [_citation(item) for item in usable]
        limitations = _sufficiency_limitations(plan, usable)
        sections: list[EvidenceAnswerSection] = []

        if contract == "component_inventory":
            rows = _inventory_rows(usable)
            if rows:
                sections.append(EvidenceAnswerSection(heading="Components", rows=rows))
            headline = "Component inventory"
        elif contract == "runtime_ranking":
            rows, aggregation_limitations = aggregate_runtime_failures(usable)
            limitations.extend(aggregation_limitations)
            if rows:
                sections.append(EvidenceAnswerSection(heading="Failure ranking", rows=rows))
            else:
                sections.append(
                    EvidenceAnswerSection(
                        heading="Result",
                        body="No countable runtime failure observations were available for the requested scope.",
                    )
                )
            headline = "Runtime failure ranking"
        elif contract == "comparison":
            headline = "Comparison"
        elif contract == "migration_proposal":
            headline = "ADF migration proposal"
        elif contract == "incident_report":
            headline = "Operational finding"
        else:
            headline = "Documentation answer" if plan.task_type.value == "document_answer" else "Answer"

        # Runtime rankings are deterministic aggregations. Provider prose can contain
        # planning chatter, tool names, or claims from context that the TurnPlan denied,
        # so it must never become part of the ranked answer.
        safe_draft = "" if contract == "runtime_ranking" else _safe_draft(draft, evidence)
        if safe_draft:
            sections.insert(0, EvidenceAnswerSection(heading="Findings", body=safe_draft))
        summary = _summary_for(contract, safe_draft, usable)
        status = _answer_status(usable, limitations)
        answer = StructuredEvidenceAnswer(
            output_contract=contract,
            headline=headline,
            summary=summary,
            sections=sections,
            limitations=_dedupe(limitations),
            citations=citations,
            evidence_refs=[item.evidence_id for item in citations],
            answer_status=status,
            domain_status=_domain_status(contract, usable),
        )
        self.validate(answer, plan=plan, evidence=usable)
        return answer

    def compose_incident(
        self,
        *,
        question: str,
        plan: TurnPlan,
        evidence: list[EvidenceSnapshot],
        investigation: InvestigationAnswer,
    ) -> StructuredEvidenceAnswer:
        """Render a typed incident result without passing through legacy prose."""
        usable = self._validate_evidence_policy(plan, evidence)
        operational = [
            item
            for item in usable
            if item.source_class
            in {EvidenceSourceClass.CURRENT_RUNTIME, EvidenceSourceClass.CONNECTOR}
        ]
        has_status = investigation.has_supported_status
        has_cause = investigation.has_supported_cause

        if has_cause:
            headline = "Cause identified"
            summary = investigation.likely_cause
        elif operational:
            headline = "Failure evidence found; cause not established"
            summary = (
                "The available operational evidence describes the failure, but it does not "
                "establish a supported root cause."
            )
        else:
            headline = "Cause could not be determined"
            summary = (
                "I could not determine why the reported execution failed because no runtime "
                "or connector/log evidence was available for the requested time window."
            )

        sections: list[EvidenceAnswerSection] = []
        if has_status:
            sections.append(
                EvidenceAnswerSection(
                    heading="Observed status", body=investigation.direct_status
                )
            )
        if has_cause:
            sections.append(
                EvidenceAnswerSection(
                    heading="Likely cause", body=investigation.likely_cause
                )
            )
        elif operational:
            sections.append(
                EvidenceAnswerSection(
                    heading="What the evidence shows",
                    body="A failure was observed, but the available evidence contains no supported cause signal.",
                )
            )
        elif usable:
            sections.append(
                EvidenceAnswerSection(
                    heading="What is available",
                    body=(
                        "Project documentation provides general operational guidance, but it "
                        "does not record what happened in this specific execution."
                    ),
                )
            )
        sections.append(
            EvidenceAnswerSection(heading="Next action", body=investigation.next_action)
        )

        limitations = [
            qualification
            for qualification in investigation.qualifications
            if not qualification.startswith("A complete investigation requires ")
            and qualification != "No source supplied a supported cause claim."
        ]
        if investigation.missing_required_sources:
            limitations.append(
                "Unavailable for this investigation: "
                + ", ".join(investigation.missing_required_sources)
                + "."
            )
        if usable and not operational:
            limitations.append(
                "Project documentation cannot establish the state or cause of a specific execution."
            )

        answer_status = (
            EvidenceAnswerStatus.COMPLETE
            if investigation.answered
            else EvidenceAnswerStatus.PARTIAL
            if operational
            else EvidenceAnswerStatus.INSUFFICIENT
        )
        citations = _deduplicated_citations(usable)
        answer = StructuredEvidenceAnswer(
            output_contract="incident_report",
            headline=headline,
            summary=summary,
            sections=sections,
            limitations=_dedupe(limitations),
            citations=citations,
            evidence_refs=[item.evidence_id for item in citations],
            answer_status=answer_status,
            domain_status=_domain_status("incident_report", usable),
            artifacts=[item.model_dump(mode="json") for item in investigation.artifacts],
        )
        self.validate(answer, plan=plan, evidence=usable)
        return answer

    def validate(
        self,
        answer: StructuredEvidenceAnswer,
        *,
        plan: TurnPlan,
        evidence: list[EvidenceSnapshot],
    ) -> None:
        available = {_evidence_id(item) for item in evidence}
        cited = {item.evidence_id for item in answer.citations}
        if not cited.issubset(available):
            raise ValueError("answer citations must reference available evidence")
        if set(answer.evidence_refs) != cited:
            raise ValueError("answer evidence refs must match citations")
        self._validate_evidence_policy(plan, evidence)
        rendered = answer.as_markdown()
        if _INTERNAL_ID_RE.search(rendered) or _RAW_PAYLOAD_RE.match(rendered):
            raise ValueError("answer exposes capability identifiers or raw provider payloads")

    @staticmethod
    def _validate_evidence_policy(
        plan: TurnPlan, evidence: Iterable[EvidenceSnapshot]
    ) -> list[EvidenceSnapshot]:
        result = list(evidence)
        for item in result:
            source = _SOURCE_POLICY[item.source_class]
            if not plan.allows(source):
                raise ValueError(f"evidence source {source!r} is denied by the TurnPlan")
        return result


def aggregate_runtime_failures(
    evidence: list[EvidenceSnapshot],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Aggregate failure observations without manufacturing provider counts."""
    counts: Counter[str] = Counter()
    explicit_counts: dict[str, int] = {}
    for item in evidence:
        if item.source_class is not EvidenceSourceClass.CURRENT_RUNTIME:
            continue
        job = str(item.metadata.get("job_name") or item.entity_ref or "").strip()
        if not job:
            match = _FAILURE_RE.search(item.summary)
            job = match.group(1).strip(" .-") if match else ""
        state = str(item.metadata.get("state") or item.metadata.get("status") or item.claim_value or "")
        if not job or not re.search(r"fail|error|timeout", f"{state} {item.summary}", re.I):
            continue
        raw_count = item.metadata.get("failure_count")
        if isinstance(raw_count, int) and raw_count >= 0:
            explicit_counts[job] = max(explicit_counts.get(job, 0), raw_count)
        else:
            counts[job] += 1

    jobs = sorted(set(counts) | set(explicit_counts))
    rows = [
        {"rank": index, "job": job, "failures": explicit_counts.get(job, counts.get(job))}
        for index, job in enumerate(
            sorted(jobs, key=lambda name: (-(explicit_counts.get(name, counts.get(name, 0))), name.casefold())),
            1,
        )
    ]
    limitations: list[str] = []
    if rows and any(job not in explicit_counts for job in jobs):
        limitations.append(
            "Counts reflect the returned failure observations; the provider did not supply a complete aggregate."
        )
    if not rows:
        if any(item.source_class is EvidenceSourceClass.CURRENT_RUNTIME for item in evidence):
            limitations.append(
                "The available runtime evidence contains no countable failure observations."
            )
        else:
            limitations.append(
                "No runtime failure observations were returned for the requested scope."
            )
    return rows, limitations


def _inventory_rows(evidence: list[EvidenceSnapshot]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        component = str(item.metadata.get("component") or item.metadata.get("job_name") or "").strip()
        if not component or component.casefold() in seen:
            continue
        seen.add(component.casefold())
        connections = item.metadata.get("connections") or item.metadata.get("connects_to") or []
        if isinstance(connections, str):
            connections = [connections]
        rows.append(
            {
                "component": component,
                "purpose": str(item.metadata.get("purpose") or "Not stated"),
                "connections": ", ".join(str(value) for value in connections) or "Not stated",
            }
        )
    return rows


def _sufficiency_limitations(plan: TurnPlan, evidence: list[EvidenceSnapshot]) -> list[str]:
    present = {_SOURCE_POLICY[item.source_class] for item in evidence}
    missing = [source for source in plan.evidence_policy.required if source not in present]
    if not evidence:
        if plan.output_contract == "runtime_ranking":
            return []
        return ["No allowed evidence was available for this answer."]
    if missing:
        return [f"Required evidence was unavailable: {', '.join(missing)}."]
    return []


def _answer_status(
    evidence: list[EvidenceSnapshot], limitations: list[str]
) -> EvidenceAnswerStatus:
    if not evidence:
        return EvidenceAnswerStatus.INSUFFICIENT
    return EvidenceAnswerStatus.PARTIAL if limitations else EvidenceAnswerStatus.COMPLETE


def _domain_status(contract: str, evidence: list[EvidenceSnapshot]) -> str:
    if contract not in {"runtime_answer", "runtime_ranking", "incident_report"}:
        return "unknown"
    operational = [
        item
        for item in evidence
        if item.source_class
        in {EvidenceSourceClass.CURRENT_RUNTIME, EvidenceSourceClass.CONNECTOR}
    ]
    text = " ".join(
        f"{item.claim_value or ''} {item.summary}" for item in operational
    ).casefold()
    if re.search(r"\b(failed|failure|error|timeout|timed out)\b", text):
        return "failed"
    if re.search(r"\b(degraded|warning|unhealthy)\b", text):
        return "degraded"
    if re.search(r"\b(healthy|success|succeeded|completed)\b", text):
        return "healthy"
    return "unknown"


def _summary_for(contract: str, draft: str, evidence: list[EvidenceSnapshot]) -> str:
    if contract == "runtime_ranking":
        rows, _ = aggregate_runtime_failures(evidence)
        if rows:
            return "The available runtime failure observations were grouped by job using supported counts."
        if evidence:
            return "Runtime evidence was returned, but it contained no countable failure observations."
        return "No runtime failure observations were available for the requested scope."
    if not evidence:
        return "I could not find allowed evidence that supports an answer."
    if draft:
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"[#*_`]", "", draft), maxsplit=1)[0]
        return first[:500]
    labels = {
        "component_inventory": "The available documentation supports a component inventory.",
        "runtime_ranking": "The available runtime observations were grouped by job.",
        "migration_proposal": "The available documentation supports an initial migration proposal.",
        "incident_report": "Runtime evidence was collected for the reported incident.",
    }
    return labels.get(contract, "The answer is grounded in the available evidence.")


def _safe_draft(draft: str, evidence: list[EvidenceSnapshot]) -> str:
    forbidden = {item.capability_id for item in evidence if item.capability_id}
    safe_lines = []
    for line in draft.strip().splitlines():
        fragments = re.split(r"(?<=[.!?])\s+", line)
        safe_lines.append(
            " ".join(
                fragment
                for fragment in fragments
                if not any(token in fragment for token in forbidden)
                and not _INTERNAL_ID_RE.search(fragment)
            )
        )
    text = "\n".join(safe_lines)
    if _RAW_PAYLOAD_RE.match(text):
        return ""
    return text[:12_000]


def _citation(item: EvidenceSnapshot) -> EvidenceAnswerCitation:
    return EvidenceAnswerCitation(
        evidence_id=_evidence_id(item),
        label=_PROJECT_SUFFIX_RE.sub("", item.title).strip(),
        uri=item.uri,
        source_class=item.source_class,
    )


def _deduplicated_citations(
    evidence: list[EvidenceSnapshot],
) -> list[EvidenceAnswerCitation]:
    citations: list[EvidenceAnswerCitation] = []
    seen: set[str] = set()
    for item in evidence:
        document_match = _DOCUMENT_NAME_RE.search(f"{item.title} {item.uri or ''}")
        if document_match:
            key = f"document:{document_match.group(1).casefold()}"
        elif item.uri:
            key = f"uri:{item.uri.split('#', 1)[0].rstrip('/').casefold()}"
        else:
            key = f"source:{item.source_class.value}:{item.title.strip().casefold()}"
        if key in seen:
            continue
        seen.add(key)
        citations.append(_citation(item))
    return citations


def _evidence_id(item: EvidenceSnapshot) -> str:
    return str(item.id) if item.id is not None else f"{item.source_class.value}:{item.source_id}"


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    columns = list(rows[0])
    header = "| " + " | ".join(column.replace("_", " ").title() for column in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join((header, separator, *body))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
