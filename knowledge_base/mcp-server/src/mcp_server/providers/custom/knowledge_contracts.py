"""Versioned, source-grounded analysis contracts shared by generation engines.

Grounding means the quoted evidence exists in this snapshot. It deliberately
does not mean that a model's interpretation has passed semantic review.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.providers.custom.phase2_claims import Phase2Claim

LANDSCAPE_ROOT = Path("_evidence_registry/landscape")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value)[:60] + "_" + digest(value)[:12]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeEvidence(StrictModel):
    evidence_id: str
    snapshot_id: str
    owner_id: str
    source_path: str
    locator: str
    text: str
    sha256: str
    complete: bool = True
    authority: Literal["implemented_design", "documented_intent", "runtime_observation"] = "implemented_design"


class KnowledgeRequirement(StrictModel):
    requirement_id: str
    unit_id: str
    group: str
    concern: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    extraction_status: Literal["extracted", "partial", "unresolved"] = "extracted"
    gap_stage: Literal["", "source", "extraction", "resolution", "packet", "analysis", "document"] = ""
    gap_reason: str = ""
    mandatory: bool = True
    exact_preservation: bool = True
    explanation_required: bool = True
    output_destinations: list[str] = Field(default_factory=list)


class KnowledgeManifest(StrictModel):
    schema_version: Literal["talend-knowledge-v2"] = "talend-knowledge-v2"
    snapshot_id: str
    project_id: str
    requirements: list[KnowledgeRequirement]
    evidence: dict[str, KnowledgeEvidence]


class KnowledgeTask(StrictModel):
    task_id: str
    snapshot_id: str
    group: str
    unit_ids: list[str]
    requirement_ids: list[str]
    evidence_ids: list[str]
    input_artifact: str
    output_artifact: str


class KnowledgePlan(StrictModel):
    schema_version: Literal["talend-knowledge-plan-v2"] = "talend-knowledge-plan-v2"
    snapshot_id: str
    tasks: list[KnowledgeTask]
    manifest: KnowledgeManifest


class KnowledgeCitation(StrictModel):
    evidence_id: str
    quote: str = Field(min_length=1)


class KnowledgeClaim(Phase2Claim):
    requirement_ids: list[str] = Field(default_factory=list)
    citations: list[KnowledgeCitation] = Field(default_factory=list)
    # Model attestation of inspection, not certification of semantic correctness.
    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    authority: Literal["implemented_design", "documented_intent", "runtime_observation", "inference"] = "implemented_design"
    # This is Python-owned; incoming model text cannot certify itself.
    review_state: Literal["grounded", "unreviewed"] = "unreviewed"


class KnowledgeClaimBatch(StrictModel):
    schema_version: Literal["knowledge-claims-v2"] = "knowledge-claims-v2"
    snapshot_id: str
    task_id: str
    group: str
    claims: list[dict] = Field(default_factory=list, max_length=80)


class KnowledgeBatchValidation(StrictModel):
    accepted: list[KnowledgeClaim] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    unresolved_requirement_ids: list[str] = Field(default_factory=list)


def write_json(path: Path, value: BaseModel | dict) -> None:
    """Atomically persist a coordinator-owned artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_knowledge_manifest(staging_dir: Path) -> KnowledgeManifest:
    return KnowledgeManifest.model_validate_json((staging_dir / LANDSCAPE_ROOT / "requirements.json").read_text(encoding="utf-8"))


def knowledge_task_prompt(task: KnowledgeTask, manifest: KnowledgeManifest) -> str:
    selected = [r for r in manifest.requirements if r.requirement_id in task.requirement_ids]
    contract = {
        "schema_version": "knowledge-claims-v2", "snapshot_id": task.snapshot_id,
        "task_id": task.task_id, "group": task.group,
        "claims": [{"section": "BUSINESS_RULES", "kind": "fact", "unit_names": task.unit_ids,
                    "related_units": [], "statement": "Explain the behavior and consequences.",
                    "evidence_refs": [], "confidence": "supported", "claim_id": "",
                    "requirement_ids": ["one assigned requirement ID"],
                    "citations": [{"evidence_id": "assigned evidence ID", "quote": "exact source substring"}],
                    "reviewed_evidence_ids": ["every evidence ID reviewed for these requirements"],
                    "authority": "implemented_design"}],
    }
    semantic_guidance = (
        "\nThese semantic requirements describe coherent concerns. Review ALL evidence assigned to each "
        "requirement and record its IDs in reviewed_evidence_ids. Explain significant rules, exceptions, "
        "branches, dependencies and failure effects together. Cite decisive examples for each concern; "
        "you do not need a separate claim or quotation for every raw record. Do not infer business purpose "
        "from a name alone. Mark unsupported purpose or behavior unknown. Your review list is an "
        "attestation of inspection, not proof that your interpretation is correct.\n"
        if any(r.requirement_id.startswith("sem-") for r in selected) else ""
    )
    return (
        "Analyze the assigned Talend evidence requirements. Read the entire assigned packet, including "
        "referenced routine implementation. Return only JSON matching this contract:\n"
        + json.dumps(contract, ensure_ascii=True)
        + "\nExplain actual conditions, transformations, failure propagation and caller effects where evidenced. "
        "Several independent rules in the same section are permitted. Each claim must identify the exact "
        "requirements it explains and quote decisive evidence for EACH requirement. Group only behavior "
        "that is actually equivalent. Exact source text remains in deterministic references; do not duplicate "
        "a long routine in a statement. Distinguish implementation, intent, runtime and inference. "
        "A source citation verifies existence, not your reasoning. Never claim a packet omission is scanner "
        "failure; report an unknown with its precise cause. Missing evidence remains unknown. "
        "Do not claim ownership of a referenced dependency. Use canonical unit IDs, not display aliases.\n"
        + semantic_guidance
        + "Requirements:\n" + json.dumps([r.model_dump() for r in selected], ensure_ascii=True)
    )


def validate_knowledge_batch(text: str, task: KnowledgeTask, manifest: KnowledgeManifest) -> KnowledgeBatchValidation:
    result = KnowledgeBatchValidation(unresolved_requirement_ids=list(task.requirement_ids))
    try:
        payload = text.strip()
        if payload.startswith("```"):
            payload = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload)
        batch = KnowledgeClaimBatch.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        result.errors.append(f"invalid knowledge batch: {exc}")
        return result
    if (batch.snapshot_id, batch.task_id, batch.group) != (task.snapshot_id, task.task_id, task.group) or manifest.snapshot_id != task.snapshot_id:
        result.errors.append("snapshot/task/group mismatch")
        return result
    requirements = {r.requirement_id: r for r in manifest.requirements}
    seen: set[str] = set()
    addressed: set[str] = set()
    for index, raw in enumerate(batch.claims):
        try:
            claim = KnowledgeClaim.model_validate(raw)
            requested = set(claim.requirement_ids)
            if not requested or not requested <= set(task.requirement_ids):
                raise ValueError("claim requirement IDs are absent or outside task")
            owners = {requirements[r].unit_id for r in requested}
            if set(claim.unit_names) != owners or not owners <= set(task.unit_ids):
                raise ValueError("claim canonical unit ownership does not match requirements")
            if not set(claim.related_units) <= {r.unit_id for r in manifest.requirements}:
                raise ValueError("noncanonical related unit")
            reviewed = set(claim.reviewed_evidence_ids)
            if not reviewed <= set(task.evidence_ids):
                raise ValueError("reviewed evidence outside assigned evidence")
            cited: set[str] = set()
            for citation in claim.citations:
                evidence = manifest.evidence.get(citation.evidence_id)
                if not evidence or citation.evidence_id not in task.evidence_ids:
                    raise ValueError("citation outside assigned evidence")
                if evidence.snapshot_id != task.snapshot_id or digest(evidence.text) != evidence.sha256:
                    raise ValueError("evidence snapshot/content mismatch")
                if citation.quote not in evidence.text:
                    raise ValueError("quoted source text does not exist at cited locator")
                if claim.authority != "inference" and claim.authority != evidence.authority:
                    raise ValueError("claim promotes evidence to a different authority")
                cited.add(citation.evidence_id)
            if claim.kind != "unknown":
                for requirement_id in requested:
                    req = requirements[requirement_id]
                    if req.extraction_status != "extracted":
                        raise ValueError("unresolved extraction requirement cannot be claimed resolved")
                    if not cited.intersection(req.evidence_ids):
                        raise ValueError("each requirement needs its own supporting citation")
                    if requirement_id.startswith("sem-") and not set(req.evidence_ids) <= reviewed:
                        raise ValueError("semantic requirement needs review of all assigned evidence")
                if claim.confidence == "unknown":
                    raise ValueError("substantive claim has unknown confidence")
            else:
                claim.confidence = "unknown"
            # IDs bind the content, source and owner, independent of model-chosen labels.
            claim.claim_id = "claim-" + digest({"task": task.task_id, "requirements": sorted(requested), "statement": claim.statement})[:24]
            if claim.claim_id in seen:
                raise ValueError("duplicate claim")
            seen.add(claim.claim_id)
            claim.review_state = "grounded" if claim.kind != "unknown" else "unreviewed"
            claim.evidence_refs = sorted(cited)
            result.accepted.append(claim)
            if claim.kind != "unknown":
                addressed.update(requested)
        except (ValidationError, ValueError, KeyError) as exc:
            result.errors.append(f"claim[{index}]: {exc}")
    result.unresolved_requirement_ids = sorted(set(task.requirement_ids) - addressed)
    return result


def persist_knowledge_batch(staging_dir: Path, task: KnowledgeTask, result: KnowledgeBatchValidation) -> Path:
    """Merge only coordinator-validated claims; failed siblings survive for repair."""
    path = staging_dir / LANDSCAPE_ROOT / "accepted" / f"{safe_id(task.task_id)}.json"
    claims: dict[str, dict] = {}
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("snapshot_id") != task.snapshot_id or previous.get("task_id") != task.task_id:
            raise ValueError("accepted claim snapshot/task mismatch")
        claims = {c["claim_id"]: c for c in previous.get("claims", [])}
    claims.update({c.claim_id: c.model_dump(mode="json") for c in result.accepted})
    # A successful follow-up supersedes earlier unknowns for those requirements.
    resolved = {r for c in claims.values() if c["kind"] != "unknown" for r in c["requirement_ids"]}
    claims = {key: c for key, c in claims.items() if c["kind"] != "unknown" or not set(c["requirement_ids"]) <= resolved}
    write_json(path, {"schema_version": "knowledge-claims-v2", "snapshot_id": task.snapshot_id,
                      "task_id": task.task_id, "group": task.group, "claims": list(claims.values())})
    return path


def accepted_knowledge_claims(staging_dir: Path, manifest: KnowledgeManifest) -> list[KnowledgeClaim]:
    claims: list[KnowledgeClaim] = []
    for path in sorted((staging_dir / LANDSCAPE_ROOT / "accepted").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("snapshot_id") != manifest.snapshot_id:
            raise ValueError("stale accepted claim ledger")
        claims.extend(KnowledgeClaim.model_validate(c) for c in record.get("claims", []))
    return claims


def render_knowledge_group(staging_dir: Path, group: str, manifest: KnowledgeManifest) -> str:
    required = {r.requirement_id: r for r in manifest.requirements
                if r.group == group and r.explanation_required}
    claims = [c for c in accepted_knowledge_claims(staging_dir, manifest) if set(c.requirement_ids) & set(required)]
    lines = [f"# Phase 2 Analysis: {group}", "", "### GROUP_SUMMARY", "",
             "Source-grounded interpretations. Semantic review and documentation retention are separate checks.", ""]
    for section in sorted({c.section for c in claims}):
        lines.extend([f"### {section}", ""])
        for claim in claims:
            if claim.section == section:
                lines.append(f"- **{claim.kind.upper()}** {', '.join(claim.unit_names)}: {claim.statement} "
                             f"<!-- claim:{claim.claim_id} -->")
                lines.append("  Evidence: " + ", ".join(claim.evidence_refs))
    addressed = {rid for c in claims if c.kind != "unknown" for rid in c.requirement_ids}
    lines.extend(["", "### UNCLASSIFIED", ""])
    for rid, req in required.items():
        if rid not in addressed:
            lines.append(f"- UNKNOWN: {req.unit_id}: {req.description}; stage={req.gap_stage or 'analysis'}; "
                         f"{req.gap_reason or 'No accepted substantive explanation yet.'}")
    return "\n".join(lines) + "\n"
