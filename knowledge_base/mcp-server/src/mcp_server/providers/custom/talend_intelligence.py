"""Talend-specific intelligence: ESB detection and activity flag extraction.

These functions scan the raw scan_result.json to produce structured facts
that the harness writes to staging before Phase 2 runs.  Prompts and validators
consume these facts rather than rediscovering them from raw scanner output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


_RECYCLE_BIN_RE = re.compile(r"(?i)(recycle.?bin|_recycle_|\.recycle)", re.IGNORECASE)
_COPY_OF_RE = re.compile(r"^Copy_of_", re.IGNORECASE)
_ENCRYPTED_VALUE_RE = re.compile(r"enc:[a-zA-Z0-9._]+:")


class TalendESBFacts(BaseModel):
    """Structured facts about ESB-specific Talend component usage."""

    esb_detected: bool = False
    esb_components: list[str] = Field(default_factory=list)
    esb_component_types: list[str] = Field(default_factory=list)
    esb_job_count: int = 0


class TalendActivityFlags(BaseModel):
    """Per-artifact activity status flags for Talend scan output."""

    recycle_bin_artifacts: list[str] = Field(default_factory=list)
    copy_of_artifacts: list[str] = Field(default_factory=list)
    deleted_artifacts: list[str] = Field(default_factory=list)
    encrypted_value_artifacts: list[str] = Field(default_factory=list)


def detect_talend_esb_patterns(scan_result: dict[str, Any]) -> TalendESBFacts:
    """Aggregate ESB/Camel route facts from a Talend scan_result dict.

    Consumes the scanner-computed ``is_esb_route`` / ``esb_component_types`` fields
    on each job (the single source of truth lives in the intodoc Talend parser).
    Jobs are not re-classified here; the component-type heuristic is intentionally
    not duplicated. A scan cache produced before those fields existed simply has
    ``is_esb_route`` absent, so the job is treated as a plain DI job -- a defensive
    read across the intodoc/mcp-server JSON boundary, not a re-derivation fallback.

    Returns a TalendESBFacts describing what was found. If no ESB routes are present
    the result has esb_detected=False and empty lists.
    """
    jobs: list[dict[str, Any]] = scan_result.get("jobs") or []
    esb_job_names: list[str] = []
    esb_type_set: set[str] = set()

    for job in jobs:
        if not job.get("is_esb_route"):
            continue
        job_name: str = str(job.get("name") or job.get("job_name") or "")
        if job_name and job_name not in esb_job_names:
            esb_job_names.append(job_name)
        for comp_type in job.get("esb_component_types") or []:
            esb_type_set.add(str(comp_type))

    return TalendESBFacts(
        esb_detected=bool(esb_job_names),
        esb_components=sorted(esb_job_names),
        esb_component_types=sorted(esb_type_set),
        esb_job_count=len(esb_job_names),
    )


def detect_talend_activity_flags(scan_result: dict[str, Any]) -> TalendActivityFlags:
    """Identify Talend artifacts that are likely inactive or unclean.

    Checks for:
    - Recycle-bin paths (Copy_of_, _recycle_bin, etc.)
    - Jobs with statusCode == "DELETED"
    - Encrypted property values (enc:system.encryption.key.v1:...)
    """
    jobs: list[dict[str, Any]] = scan_result.get("jobs") or []
    recycle_bin: list[str] = []
    copy_of: list[str] = []
    deleted: list[str] = []
    encrypted: list[str] = []

    for job in jobs:
        job_name: str = str(job.get("name") or job.get("job_name") or "")
        job_path: str = str(job.get("path") or job.get("file_path") or "")
        status: str = str(job.get("statusCode") or job.get("status") or "")

        label = job_name or job_path or "<unnamed>"

        if _RECYCLE_BIN_RE.search(job_path):
            recycle_bin.append(label)
        if _COPY_OF_RE.match(job_name):
            copy_of.append(label)
        if status.upper() in {"DELETED", "DELETED_"}:
            deleted.append(label)

        # Scan properties/parameters for encrypted values
        for prop in job.get("properties") or job.get("parameters") or []:
            val = str(prop.get("value") or "")
            if _ENCRYPTED_VALUE_RE.search(val):
                if label not in encrypted:
                    encrypted.append(label)
                break

    return TalendActivityFlags(
        recycle_bin_artifacts=sorted(recycle_bin),
        copy_of_artifacts=sorted(copy_of),
        deleted_artifacts=sorted(deleted),
        encrypted_value_artifacts=sorted(encrypted),
    )


def write_talend_intelligence(
    scan_result: dict[str, Any],
    staging_dir: Path,
) -> tuple[TalendESBFacts, TalendActivityFlags]:
    """Run all Talend intelligence checks and write results to staging.

    Writes:
    - _preprocess/talend_esb_facts.json  (if ESB detected)
    - _preprocess/talend_activity_flags.json  (if any flags found)

    Returns (esb_facts, activity_flags).
    """
    preprocess_dir = staging_dir / "_preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)

    esb_facts = detect_talend_esb_patterns(scan_result)
    activity_flags = detect_talend_activity_flags(scan_result)

    if esb_facts.esb_detected:
        (preprocess_dir / "talend_esb_facts.json").write_text(
            esb_facts.model_dump_json(indent=2), encoding="utf-8"
        )

    if any([
        activity_flags.recycle_bin_artifacts,
        activity_flags.copy_of_artifacts,
        activity_flags.deleted_artifacts,
        activity_flags.encrypted_value_artifacts,
    ]):
        (preprocess_dir / "talend_activity_flags.json").write_text(
            activity_flags.model_dump_json(indent=2), encoding="utf-8"
        )

    return esb_facts, activity_flags
