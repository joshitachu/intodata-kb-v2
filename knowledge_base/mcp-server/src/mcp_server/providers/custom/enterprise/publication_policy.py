"""Fail-closed client-readiness verdict for the final staged product.

Evaluated by the runner after review/repair and again by the publication
background task before the atomic swap. A failing verdict holds the swap and
the RAG push; ``docs_staging`` stays on disk as a labelled draft and the last
client-ready baseline in ``docs/`` is untouched (plan decision 12).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.custom.enterprise.client_language import leakage_findings
from mcp_server.providers.custom.enterprise.integration_projection import (
    DOSSIER_SECTIONS,
    DossierPlan,
    load_dossier_plan,
)
from mcp_server.providers.custom.enterprise.knowledge_model import KnowledgeModel, load_knowledge_model
from mcp_server.providers.custom.enterprise.layout import (
    CATALOGUE_DOC,
    DOC_PROVENANCE_ROOT,
    EXACT_MAPPING_TABLES,
    KNOWLEDGE_VALIDATION,
    PUBLICATION_VERDICT,
    QUESTION_COVERAGE,
    RUN_RECEIPT,
    dossier_doc_name,
)
from mcp_server.providers.custom.knowledge_contracts import write_json

_TABLE_HEADING = re.compile(r"^#### Exact mapping table ([0-9a-f]+)\s*$", re.MULTILINE)
_ROW = re.compile(r"^\|(?!---)(?:[^|\n]*\|){5}[^|\n]*\|\s*$", re.MULTILINE)


class PublicationVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "enterprise-publication-verdict-1"
    ok: bool
    contract: str = "talend-enterprise-v1"
    checks: dict[str, bool] = Field(default_factory=dict)
    blocking: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    draft_label: str = ""
    evaluated_at: float = Field(default_factory=time.time)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _mapping_depth_errors(staging_dir: Path, plan: DossierPlan) -> list[str]:
    errors: list[str] = []
    for entry in plan.dossiers:
        doc = dossier_doc_name(entry.slug)
        path = staging_dir / f"{doc}.md"
        if not path.is_file():
            errors.append(f"{doc}: document missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        present = set(_TABLE_HEADING.findall(text))
        for table_id in entry.mapping_table_ids:
            if table_id not in present:
                errors.append(f"{doc}: exact mapping table {table_id} not embedded")
                continue
            exact = staging_dir / EXACT_MAPPING_TABLES / f"{table_id}.md"
            if exact.is_file():
                expected_rows = len(_ROW.findall(exact.read_text(encoding="utf-8", errors="replace")))
                section = text.split(f"#### Exact mapping table {table_id}", 1)[1]
                section = section.split("\n#### ", 1)[0].split("\n## ", 1)[0]
                actual_rows = len(_ROW.findall(section))
                if actual_rows < expected_rows:
                    errors.append(f"{doc}: mapping table {table_id} embedded with {actual_rows} rows, exact table has {expected_rows}")
        for section_name in DOSSIER_SECTIONS:
            if f"## {section_name}" in text:
                errors.append(f"{doc}: raw section key {section_name} left in prose")
    return errors


def deterministic_review(staging_dir: Path, plan: DossierPlan | None, model: KnowledgeModel | None) -> list[dict[str, Any]]:
    """Python-owned review findings, merged with the model's review."""
    findings: list[dict[str, Any]] = []
    if plan is None:
        return [{"doc": CATALOGUE_DOC, "section": "", "severity": "blocking", "kind": "missing_required_content",
                 "detail": "no document plan", "claim_ids": [], "source": "deterministic"}]
    for doc in plan.required_docs:
        path = staging_dir / f"{doc}.md"
        if not path.is_file() or len(path.read_text(encoding="utf-8", errors="replace").strip()) < 400:
            findings.append({"doc": doc, "section": "", "severity": "blocking", "kind": "missing_required_content",
                             "detail": "document absent or shorter than 400 characters", "claim_ids": [], "source": "deterministic"})
            continue
        leaks = leakage_findings(path.read_text(encoding="utf-8", errors="replace"))
        for leak in leaks:
            findings.append({"doc": doc, "section": "", "severity": "blocking", "kind": "leakage", "detail": leak,
                             "claim_ids": [], "source": "deterministic"})
    for error in _mapping_depth_errors(staging_dir, plan):
        doc = error.split(":", 1)[0]
        findings.append({"doc": doc, "section": "mapping_notes", "severity": "blocking", "kind": "missing_required_content",
                         "detail": error, "claim_ids": [], "source": "deterministic"})
    catalogue = staging_dir / f"{CATALOGUE_DOC}.md"
    if model is not None and catalogue.is_file():
        text = catalogue.read_text(encoding="utf-8", errors="replace")
        for entry in plan.dossiers:
            if f"{dossier_doc_name(entry.slug)}.md" not in text:
                findings.append({"doc": CATALOGUE_DOC, "section": "", "severity": "blocking", "kind": "missing_required_content",
                                 "detail": f"dossier {dossier_doc_name(entry.slug)} not linked from the catalogue",
                                 "claim_ids": [], "source": "deterministic"})
        for journey in model.journeys:
            if journey.name not in text and journey.journey_id not in text:
                findings.append({"doc": CATALOGUE_DOC, "section": "", "severity": "blocking", "kind": "inconsistent_with_model",
                                 "detail": f"journey {journey.name} absent from the catalogue", "claim_ids": [journey.claim_id],
                                 "source": "deterministic"})
        for contradiction in model.contradictions:
            if contradiction.resolution == "open":
                mentioned = any(contradiction.subject in (staging_dir / f"{doc}.md").read_text(encoding="utf-8", errors="replace")
                                for doc in plan.required_docs if (staging_dir / f"{doc}.md").is_file())
                if not mentioned:
                    findings.append({"doc": CATALOGUE_DOC, "section": "risks_and_open_questions", "severity": "blocking",
                                     "kind": "contradiction", "detail": f"open contradiction not disclosed: {contradiction.subject}",
                                     "claim_ids": [contradiction.claim_id], "source": "deterministic"})
    return findings


def evaluate_publication(staging_dir: Path) -> PublicationVerdict:
    checks: dict[str, bool] = {}
    blocking: list[str] = []
    warnings: list[str] = []
    receipt = _read_json(staging_dir / RUN_RECEIPT) or {}
    status = str(receipt.get("status") or "")
    checks["run_complete"] = status.startswith("complete")
    if not checks["run_complete"]:
        blocking.append(f"run status is {status or 'unknown'}: {receipt.get('error') or 'analysis incomplete'}")
    validation = _read_json(staging_dir / KNOWLEDGE_VALIDATION) or {}
    checks["knowledge_model_grounded"] = bool(validation.get("ok"))
    if not checks["knowledge_model_grounded"]:
        blocking.append("knowledge model validation did not pass: " + "; ".join((validation.get("errors") or ["no validation report"])[:3]))
    checks["question_coverage_recorded"] = (staging_dir / QUESTION_COVERAGE).is_file()
    if not checks["question_coverage_recorded"]:
        blocking.append("question coverage report missing")
    plan = load_dossier_plan(staging_dir)
    model = load_knowledge_model(staging_dir)
    checks["document_plan"] = plan is not None
    if plan is None:
        blocking.append("document plan missing")
        return _finish(staging_dir, checks, blocking, warnings)
    missing_docs = [doc for doc in plan.required_docs if not (staging_dir / f"{doc}.md").is_file()]
    checks["required_documents"] = not missing_docs
    if missing_docs:
        blocking.append("required documents missing: " + ", ".join(missing_docs[:10]))
    depth_errors = _mapping_depth_errors(staging_dir, plan)
    checks["mapping_depth"] = not depth_errors
    blocking.extend(depth_errors[:20])
    leak_findings = [f for f in deterministic_review(staging_dir, plan, model) if f["kind"] == "leakage"]
    checks["no_internal_leakage"] = not leak_findings
    blocking.extend(f"{f['doc']}: {f['detail']}" for f in leak_findings[:20])
    topology_findings = [f for f in deterministic_review(staging_dir, plan, model)
                         if f["kind"] in {"inconsistent_with_model", "contradiction"} or "not linked" in f["detail"]]
    checks["topology_and_contradictions"] = not topology_findings
    blocking.extend(f"{f['doc']}: {f['detail']}" for f in topology_findings[:20])
    provenance_missing = [doc for doc in plan.required_docs if not (staging_dir / DOC_PROVENANCE_ROOT / f"{doc}.json").is_file()]
    checks["internal_provenance"] = not provenance_missing
    if provenance_missing:
        blocking.append("provenance sidecars missing: " + ", ".join(provenance_missing[:10]))
    if model is not None:
        placed = {u for j in model.journeys for u in {j.trigger.unit_id, *j.implementing_units, *(s.unit_id for s in j.steps)}}
        excluded = {e.unit_id for e in model.excluded_units}
        planned_units = {u for d in [*plan.dossiers, *plan.catalogue_only] for u in d.unit_ids}
        uncovered = sorted(placed - planned_units)
        checks["coverage"] = not uncovered
        if uncovered:
            blocking.append("designs placed in journeys but absent from every integration: " + ", ".join(uncovered[:10]))
        if excluded:
            warnings.append(f"{len(excluded)} design(s) excluded from journeys with a stated reason")
    else:
        checks["coverage"] = False
        blocking.append("knowledge model unreadable")
    review = _read_json(staging_dir / "_evidence_registry" / "enterprise" / "review.json") or {}
    open_blocking = [f for f in review.get("findings") or [] if f.get("severity") == "blocking"]
    checks["review_clear"] = not open_blocking
    blocking.extend(f"review: {f.get('doc')}: {f.get('detail')}" for f in open_blocking[:20])
    return _finish(staging_dir, checks, blocking, warnings)


def _finish(staging_dir: Path, checks: dict[str, bool], blocking: list[str], warnings: list[str]) -> PublicationVerdict:
    verdict = PublicationVerdict(ok=not blocking, checks=checks, blocking=blocking, warnings=warnings,
                                 draft_label="" if not blocking else "draft: held by client-readiness policy")
    write_json(staging_dir / PUBLICATION_VERDICT, verdict)
    return verdict


def load_publication_verdict(staging_dir: Path) -> PublicationVerdict | None:
    payload = _read_json(staging_dir / PUBLICATION_VERDICT)
    if not isinstance(payload, dict):
        return None
    try:
        return PublicationVerdict.model_validate(payload)
    except ValueError:
        return None


def write_enterprise_gates(staging_dir: Path, verdict: PublicationVerdict) -> dict[str, Any]:
    """Compatibility shape for ``_load_enterprise_gate_results`` and the run record."""
    gates = {name: {"ok": ok, "errors": [b for b in verdict.blocking if name.split("_")[0] in b] if not ok else []}
             for name, ok in verdict.checks.items()}
    gates["client_readiness_policy"] = {"ok": verdict.ok, "errors": verdict.blocking, "remediation": None if verdict.ok else
                                        "Resolve the blocking findings and resume the run; the previous baseline remains published."}
    report = {"mode": "enterprise", "contract": verdict.contract, "client_ready": verdict.ok, "gates": gates,
              "warnings": verdict.warnings, "generated_at": time.time()}
    (staging_dir / "_assessment").mkdir(parents=True, exist_ok=True)
    (staging_dir / "_assessment" / "enterprise_gates.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
