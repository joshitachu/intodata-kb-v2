"""Staging layout shared by every enterprise module.

Paths are staging-relative. Directories under ``_evidence_registry`` and
``_assessment`` survive the publication swap (they are on the Phase 4
allowlist) and are INTERNAL; ``_workspace``, ``_method`` and ``_analysis`` are
prompt inputs that Phase 4 deletes before the swap.
"""

from __future__ import annotations

from pathlib import Path

WORKSPACE_ROOT = Path("_workspace")
WORKSPACE_MANIFEST = WORKSPACE_ROOT / "MANIFEST.json"
METHOD_ROOT = Path("_method")
EXACT_ROOT = Path("_analysis") / "_exact"
EXACT_UNITS = EXACT_ROOT / "units.json"
EXACT_DEPENDENCIES = EXACT_ROOT / "dependencies.json"
EXACT_INTERFACES = EXACT_ROOT / "interfaces.json"
EXACT_CONFIGURATION = EXACT_ROOT / "configuration.json"
EXACT_MAPPINGS = EXACT_ROOT / "mapping_accounting.json"
EXACT_MAPPING_TABLES = EXACT_ROOT / "mapping_tables"
EXACT_CAPABILITY = EXACT_ROOT / "capability_ledger.json"
EXACT_INDEX = EXACT_ROOT / "index.md"
RUNTIME_AUTHORITY_JSON = Path("_analysis") / "_runtime_authority.json"
NONCODE_CONTEXT = Path("_analysis") / "_noncode_project_context.md"

ENTERPRISE_ROOT = Path("_evidence_registry") / "enterprise"
PHASE_STATE = ENTERPRISE_ROOT / "phase_state.json"
PHASES_ROOT = ENTERPRISE_ROOT / "phases"
KNOWLEDGE_MODEL = ENTERPRISE_ROOT / "knowledge_model.json"
KNOWLEDGE_VALIDATION = ENTERPRISE_ROOT / "knowledge_model_validation.json"
QUESTION_COVERAGE = ENTERPRISE_ROOT / "question_coverage.json"
DOC_PROVENANCE_ROOT = ENTERPRISE_ROOT / "doc_provenance"
DOSSIER_PLAN = ENTERPRISE_ROOT / "dossier_plan.json"
REVIEW_REPORT = ENTERPRISE_ROOT / "review.json"
REPAIR_LEDGER = ENTERPRISE_ROOT / "repair_ledger.json"
RUN_RECEIPT = Path("_assessment") / "enterprise_run_receipt.json"
PUBLICATION_VERDICT = Path("_assessment") / "publication_verdict.json"
RAG_KNOWLEDGE_ROOT = Path("_rag_support") / "knowledge_model"

CATALOGUE_DOC = "INTEGRATION_CATALOGUE"
DOSSIER_PREFIX = "INTEGRATION__"

#: Evidence a model claim may cite. Anything else (prompts, method guides,
#: prior model output) is not evidence of record.
EVIDENCE_ROOTS: tuple[str, ...] = (
    WORKSPACE_ROOT.as_posix() + "/",
    EXACT_ROOT.as_posix() + "/",
    RUNTIME_AUTHORITY_JSON.as_posix(),
    NONCODE_CONTEXT.as_posix(),
)

#: Internal names that must never appear in client prose (plan decision 10).
INTERNAL_PATH_MARKERS: tuple[str, ...] = (
    "_workspace", "_method", "_analysis", "_exact", "_evidence_registry",
    "_assessment", "_rag_support", "_transient", "_preprocess", "_investigation",
    "_audit", "_topology", "_facts", "docs_staging", "scan_result.json",
    "scan/manifest.json", "facts_manifest.json", "engineering_facts",
    "knowledge_model.json", "Evidence Register", "evidence register",
    "nexus_artifacts_submit_artifact", "submit_artifact",
)


def dossier_doc_name(slug: str) -> str:
    return f"{DOSSIER_PREFIX}{slug}"
