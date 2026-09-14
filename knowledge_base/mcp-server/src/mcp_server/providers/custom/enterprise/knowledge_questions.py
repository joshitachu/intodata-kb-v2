"""Frozen multi-audience question set answered from the knowledge model only.

Every answer is assembled from model fields; nothing is synthesised. A question
the model cannot support resolves to an explicit ``unknown`` with the reason,
never to an invented answer (plan verification requirement).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.custom.enterprise.knowledge_model import KnowledgeModel
from mcp_server.providers.custom.enterprise.layout import QUESTION_COVERAGE
from mcp_server.providers.custom.knowledge_contracts import write_json

Audience = Literal["developer", "operator", "architect", "business_owner"]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    question_id: str
    audience: Audience
    kind: str
    subject: str
    text: str


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    audience: Audience
    kind: str
    subject: str
    status: Literal["answered", "unknown"]
    answer: str = ""
    reason: str = ""
    evidence: list[dict[str, str]] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


def _refs(entries: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for entry in entries:
        for ref in getattr(entry, "evidence", []) or []:
            out.append({"path": ref.path, "locator": ref.locator})
    return out


def generate_questions(model: KnowledgeModel, exact_units: dict[str, dict[str, Any]]) -> list[Question]:
    """The frozen question set for one estate (deterministic order)."""
    questions: list[Question] = []
    for unit_id in sorted(exact_units):
        questions.append(Question(question_id=f"dev.depends.{unit_id}", audience="developer", kind="dependencies",
                                  subject=unit_id, text=f"Which designs does {unit_id} call and which call it?"))
        questions.append(Question(question_id=f"dev.mappings.{unit_id}", audience="developer", kind="mapping_rules",
                                  subject=unit_id, text=f"What do the mappings in {unit_id} compute?"))
        questions.append(Question(question_id=f"ops.trigger_failure.{unit_id}", audience="operator", kind="trigger_and_failure",
                                  subject=unit_id, text=f"What triggers {unit_id} and what happens when it fails?"))
        questions.append(Question(question_id=f"ops.runtime.{unit_id}", audience="operator", kind="runtime_status",
                                  subject=unit_id, text=f"Has {unit_id} run recently and how reliably?"))
        questions.append(Question(question_id=f"arch.blast_radius.{unit_id}", audience="architect", kind="blast_radius",
                                  subject=unit_id, text=f"What is affected when {unit_id} changes?"))
        questions.append(Question(question_id=f"biz.owner.{unit_id}", audience="business_owner", kind="ownership",
                                  subject=unit_id, text=f"Who owns {unit_id} in the business?"))
    for journey in sorted(model.journeys, key=lambda j: j.journey_id):
        questions.append(Question(question_id=f"arch.systems.{journey.journey_id}", audience="architect", kind="journey_systems",
                                  subject=journey.journey_id, text=f"Which systems does {journey.name} connect and how?"))
        questions.append(Question(question_id=f"biz.purpose.{journey.journey_id}", audience="business_owner", kind="business_purpose",
                                  subject=journey.journey_id, text=f"What business object does {journey.name} move and why?"))
    return questions


def answer_question(question: Question, model: KnowledgeModel, exact_units: dict[str, dict[str, Any]]) -> Answer:
    base = dict(question_id=question.question_id, audience=question.audience, kind=question.kind, subject=question.subject)
    unit = exact_units.get(question.subject, {})
    if question.kind == "dependencies":
        callers, callees = unit.get("callers", []), unit.get("callees", [])
        design = next((d for d in model.designs if d.unit_id == question.subject), None)
        if design is None:
            return Answer(**base, status="unknown", reason="design not analysed in the knowledge model")
        return Answer(**base, status="answered",
                      answer=f"Calls: {', '.join(callees) or 'none'}. Called by: {', '.join(callers) or 'none (entry point or orphan)'}.",
                      evidence=_refs([design]), claim_ids=[design.claim_id])
    if question.kind == "mapping_rules":
        tables = set(unit.get("mapping_table_ids", []))
        if not tables:
            return Answer(**base, status="answered", answer="No mapper component in this design (deterministic scan).")
        interpretations = [m for m in model.mappings if m.table_id in tables]
        missing = sorted(tables - {m.table_id for m in interpretations})
        if missing:
            return Answer(**base, status="unknown", reason=f"exact tables not interpreted: {', '.join(missing)}")
        return Answer(**base, status="answered",
                      answer=" ".join(f"[{m.table_id}] {m.summary}" for m in interpretations),
                      evidence=_refs(interpretations), claim_ids=[m.claim_id for m in interpretations])
    if question.kind == "trigger_and_failure":
        journeys = [j for j in model.journeys if j.trigger.unit_id == question.subject or question.subject in j.implementing_units]
        paths = [e for e in model.error_paths if e.unit_id == question.subject]
        if not journeys:
            return Answer(**base, status="unknown", reason="design is not placed in any reconstructed journey")
        trigger_text = "; ".join(f"{j.name}: {j.trigger.kind} ({j.trigger.detail or 'no detail'})" for j in journeys)
        if paths:
            failure_text = " ".join(f"{p.trigger} -> {p.handling}. Recovery: {p.recovery or 'not described'}." for p in paths)
        elif unit.get("has_error_handling"):
            return Answer(**base, status="unknown", reason="error-handling constructs exist but no error path was analysed")
        else:
            failure_text = "No error-handling construct detected in the design (deterministic scan); failures propagate to the caller or scheduler."
        return Answer(**base, status="answered", answer=f"Triggers: {trigger_text}. Failure: {failure_text}",
                      evidence=_refs(journeys) + _refs(paths), claim_ids=[j.claim_id for j in journeys] + [p.claim_id for p in paths])
    if question.kind == "runtime_status":
        observation = next((r for r in model.runtime if r.unit_id == question.subject), None)
        if observation is None:
            return Answer(**base, status="unknown", reason="no runtime status recorded")
        if observation.status == "observed":
            return Answer(**base, status="answered", answer=observation.summary, evidence=_refs([observation]),
                          claim_ids=[observation.claim_id])
        return Answer(**base, status="unknown", reason=f"runtime {observation.status}: {observation.summary}",
                      claim_ids=[observation.claim_id])
    if question.kind == "blast_radius":
        dependents = unit.get("callers", [])
        journeys = [j for j in model.journeys if question.subject in j.implementing_units or j.trigger.unit_id == question.subject]
        if not journeys and not dependents:
            design = next((d for d in model.designs if d.unit_id == question.subject), None)
            if design is None:
                return Answer(**base, status="unknown", reason="design not analysed in the knowledge model")
        return Answer(**base, status="answered",
                      answer=f"Direct callers: {', '.join(dependents) or 'none'}. Journeys: {', '.join(j.name for j in journeys) or 'none'}.",
                      evidence=_refs(journeys), claim_ids=[j.claim_id for j in journeys])
    if question.kind == "ownership":
        unknown = next((u for u in model.unknowns if u.subject == question.subject and "own" in u.question.lower()), None)
        design = next((d for d in model.designs if d.unit_id == question.subject), None)
        if design is not None and design.business_purpose_status == "evidenced" and "own" in design.views.business_owner.lower():
            return Answer(**base, status="answered", answer=design.views.business_owner, evidence=_refs([design]), claim_ids=[design.claim_id])
        reason = unknown.reason if unknown else "ownership is not recoverable from the exported design; no supplied intent names an owner"
        return Answer(**base, status="unknown", reason=reason, claim_ids=[unknown.claim_id] if unknown else [])
    if question.kind == "journey_systems":
        journey = next((j for j in model.journeys if j.journey_id == question.subject), None)
        if journey is None:
            return Answer(**base, status="unknown", reason="journey not in the knowledge model")
        systems = [f"{d.system} via {d.mechanism or 'unspecified mechanism'}" for d in journey.destinations]
        return Answer(**base, status="answered",
                      answer=f"Trigger {journey.trigger.kind} at {journey.trigger.unit_id}; destinations: {', '.join(systems) or 'none recorded'}.",
                      evidence=_refs([journey]), claim_ids=[journey.claim_id])
    if question.kind == "business_purpose":
        meaning = next((m for m in model.business if m.journey_id == question.subject), None)
        journey = next((j for j in model.journeys if j.journey_id == question.subject), None)
        if meaning is None or meaning.status == "unknown":
            reason = meaning.statement if meaning else "no business meaning entry"
            return Answer(**base, status="unknown", reason=reason, claim_ids=[meaning.claim_id] if meaning else [])
        prefix = "" if meaning.status == "evidenced" else "Inferred from the implemented design (not stated by the business): "
        obj = journey.business_object if journey and journey.business_object else "unspecified object"
        return Answer(**base, status="answered", answer=f"{prefix}{obj}: {meaning.statement}", evidence=_refs([meaning]),
                      claim_ids=[meaning.claim_id])
    return Answer(**base, status="unknown", reason="unsupported question kind")


def question_coverage(model: KnowledgeModel, exact_units: dict[str, dict[str, Any]]) -> dict[str, Any]:
    questions = generate_questions(model, exact_units)
    answers = [answer_question(q, model, exact_units) for q in questions]
    by_audience: dict[str, dict[str, int]] = {}
    for answer in answers:
        bucket = by_audience.setdefault(answer.audience, {"answered": 0, "unknown": 0})
        bucket[answer.status] += 1
    return {
        "schema_version": "enterprise-question-coverage-1",
        "question_count": len(questions),
        "by_audience": by_audience,
        "answers": [a.model_dump(mode="json") for a in answers],
    }


def write_question_coverage(staging_dir: Path, model: KnowledgeModel, exact_units: dict[str, dict[str, Any]]) -> dict[str, Any]:
    report = question_coverage(model, exact_units)
    write_json(staging_dir / QUESTION_COVERAGE, report)
    return report
