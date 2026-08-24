"""Deterministic repair planning for evidence-grounded documentation repairs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from mcp_server.providers.custom.models import CoverageReport, FactsManifest
from mcp_server.providers.custom.validators import (
    missing_required_analysis_sections,
    normalize_analysis_guide_headings,
)

if TYPE_CHECKING:
    from mcp_server.providers.custom.pack_contract import PackContract


RepairTriggerKind = Literal[
    "form_only",
    "coverage_marker",
    "section_heading",
    "byte_depth",
    "provenance_structural",
    "unknown",
]
RepairAction = Literal[
    "no_repair_warning",
    "deterministic_patch",
    "source_grounded_repair",
    "skip_repair",
]
RepairPhase = Literal["phase2", "phase3"]


class RepairEvidenceRef(BaseModel):
    """A compact evidence reference for one repair packet."""

    fact_id: str | None = None
    category: str | None = None
    source_path: str | None = None
    artifact_path: str | None = None
    line: int | None = None
    snippet: str = ""
    evidence_status: str | None = None


class RepairPacket(BaseModel):
    """One planned repair or non-repair decision."""

    phase: RepairPhase
    target: str
    action: RepairAction
    trigger_kind: RepairTriggerKind
    reason: str
    evidence_refs: list[RepairEvidenceRef] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0
    prompt_notes: list[str] = Field(default_factory=list)


class RepairPlan(BaseModel):
    """Serializable repair plan written before any LLM repair call."""

    packets: list[RepairPacket] = Field(default_factory=list)
    skipped_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)

    def with_summary(self) -> "RepairPlan":
        counts: dict[str, int] = {}
        for packet in self.packets:
            counts[packet.action] = counts.get(packet.action, 0) + 1
        return self.model_copy(update={"summary": counts})


REPAIR_TOKEN_CAP = 50_000
_PHASE2_DEPTH_RE = re.compile(r"\btoo shallow\b|\bdepth\b", re.I)
_PHASE2_SECTION_RE = re.compile(r"\bmissing required sections?\b|\bGROUP_SUMMARY\b", re.I)
_PHASE2_COVERAGE_RE = re.compile(r"\bmissing units?\b|\bdoes not mention units\b", re.I)


def estimate_repair_tokens(prompt: str, context_files: list[Path] | None = None) -> int:
    """Cheap stable repair token estimate used before repair calls."""

    total_chars = len(prompt)
    for path in context_files or []:
        try:
            total_chars += path.stat().st_size
        except OSError:
            continue
        total_chars += len(path.name) + 32
    return max(1, total_chars // 4)


def phase2_report_plan(
    *,
    report: CoverageReport,
    report_kind: Literal["coverage", "depth", "guide_sections"],
    target: str = "phase2",
) -> RepairPlan:
    """Classify a Phase 2 validator report before any repair call."""

    if report.ok and report.coverage_count >= report.expected_count:
        return RepairPlan().with_summary()

    errors = " ".join(report.errors)
    if report_kind == "guide_sections" or _PHASE2_SECTION_RE.search(errors):
        return RepairPlan(
            packets=[
                RepairPacket(
                    phase="phase2",
                    target=target,
                    action="deterministic_patch",
                    trigger_kind="section_heading",
                    reason="Guide-section failure is structural; patch heading/summary shell without LLM.",
                )
            ]
        ).with_summary()

    if report_kind == "depth" or _PHASE2_DEPTH_RE.search(errors):
        return RepairPlan(
            packets=[
                RepairPacket(
                    phase="phase2",
                    target=target,
                    action="no_repair_warning",
                    trigger_kind="byte_depth",
                    reason="Byte-depth failure alone does not prove missing source evidence.",
                )
            ]
        ).with_summary()

    if report.missing_units or _PHASE2_COVERAGE_RE.search(errors):
        return RepairPlan(
            packets=[
                RepairPacket(
                    phase="phase2",
                    target=target,
                    action="deterministic_patch",
                    trigger_kind="coverage_marker",
                    reason="Coverage-marker failure may be form-only; patch markers for units already mentioned.",
                )
            ]
        ).with_summary()

    return RepairPlan(
        packets=[
            RepairPacket(
                phase="phase2",
                target=target,
                action="skip_repair",
                trigger_kind="unknown",
                reason="No source-grounded repair evidence was available for this Phase 2 report.",
            )
        ],
        warnings=["Phase 2 repair skipped because the report was not source-grounded."],
    ).with_summary()


def mentioned_units_by_group(facts: FactsManifest, analysis_dir: Path, unit_names: list[str]) -> dict[str, list[str]]:
    """Return missing unit names that are already mentioned in their group analysis file."""

    missing = set(unit_names)
    found: dict[str, list[str]] = {}
    for unit in facts.units:
        if unit.name not in missing:
            continue
        path = analysis_dir / f"{unit.group}.md"
        if not path.exists():
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", unit.group).strip("_") or "group"
            path = analysis_dir / f"{safe}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if unit.name in text:
            found.setdefault(unit.group, []).append(unit.name)
    return found


def deterministic_phase2_section_patch(
    text: str,
    *,
    platform: str,
    group: str,
    contract: "PackContract | None" = None,
) -> tuple[str, list[str]]:
    """Add only safe structural Phase 2 section shells without calling an LLM.

    Contract-driven when ``contract`` is given: missing sections are computed
    against the contract's required set, and the patchable set is the
    protected floor (GROUP_SUMMARY). For a ``mode: replace`` pack the floor IS
    the whole required set, so this patch always converges and the downstream
    "missing required sections after deterministic patch" raise (the silent
    PARTIAL-stub path, recon S9) is unreachable for satisfied pack output.
    """

    normalized = normalize_analysis_guide_headings(text, platform, contract=contract)
    missing = missing_required_analysis_sections(normalized, platform, contract=contract)
    if not missing:
        return normalized, []
    floor = contract.floor_names if contract is not None else {"GROUP_SUMMARY"}
    patchable = [name for name in missing if name in floor]
    if not patchable:
        return normalized, missing
    sections = []
    for name in patchable:
        sections.append(
            f"### {name}\n\n"
            f"Deterministic repair note for `{group}`: the model produced analysis content "
            "but omitted this required heading. Use the preceding model analysis and "
            "source-grounded artifacts for substantive details; no new source claims were "
            "added by this structural patch."
        )
    return f"{normalized.rstrip()}\n\n" + "\n\n".join(sections) + "\n", patchable


def deterministic_coverage_marker_patch(text: str, units: list[str]) -> str:
    """Append exact coverage markers for units that are already present in the text."""

    missing_markers = [unit for unit in units if f"- COVERED: {unit}" not in text]
    if not missing_markers:
        return text
    markers = "\n".join(f"- COVERED: {unit}" for unit in missing_markers)
    return f"{text.rstrip()}\n\n## Deterministic Coverage Marker Repair\n\n{markers}\n"
