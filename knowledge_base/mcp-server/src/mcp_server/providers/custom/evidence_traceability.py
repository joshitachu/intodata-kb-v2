"""Evidence traceability validator.

Detects when a generated document claims a unit's evidence is absent
when Phase 1 investigation actually found evidence for that unit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server.providers.custom.models import FactsManifest, InvestigationReport


# Phrases in a window around a unit mention that indicate an absence claim.
_ABSENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnot\s+(?:available|found|captured|scanned|present)\b", re.I),
    re.compile(r"\bunavailable\b", re.I),
    re.compile(r"\bno\s+(?:evidence|data|information|details?|content)\b", re.I),
    re.compile(r"\bcannot\s+(?:be\s+)?(?:found|identified|verified|confirmed)\b", re.I),
    re.compile(r"\bscanner[- ]limited\b", re.I),
    re.compile(r"\bnot\s+included\s+in\s+(?:the\s+)?scan\b", re.I),
)

# Properly-attributed PIPELINE-failure wording (Round 3): "the analysis task
# did not complete ... (scan evidence exists at <path>)" is the required
# rendering for an analysis-pipeline gap and explicitly asserts the evidence
# EXISTS. It must not count as a phantom source-absence claim, or the doc
# writer is punished for following the pipeline-gap rendering rule.
_PIPELINE_GAP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\banalysis\s+task\s+did\s+not\s+complete\b", re.I),
    re.compile(r"\bdocumentation[- ]pipeline\s+gap\b", re.I),
    re.compile(r"\bscan\s+evidence\b[^.\n]{0,80}\bexists\b", re.I),
)

# Characters around a unit mention to search for absence phrases.
_ABSENCE_WINDOW = 300


@dataclass
class AbsenceClaim:
    unit_name: str
    doc_name: str
    matched_phrase: str
    evidence_path: str


@dataclass
class TraceabilityReport:
    ok: bool
    cited_units: list[str] = field(default_factory=list)
    missing_units: list[str] = field(default_factory=list)
    phantom_absences: list[AbsenceClaim] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def repair_hints(self) -> list[str]:
        """Return structured repair hints, one per phantom absence."""
        return [
            f"Investigation evidence for {c.unit_name!r} exists at "
            f"{c.evidence_path} — do not claim it is unavailable."
            for c in self.phantom_absences
        ]


class EvidenceTraceabilityValidator:
    """Cross-check a generated doc against the Phase 1 investigation corpus.

    Parameters
    ----------
    investigation_reports:
        All Phase 1 reports for this run (from state.investigation_reports).
    facts:
        The canonical unit registry (state.facts_manifest).
    investigation_dir:
        Staging sub-directory containing investigation artifacts
        (``ctx.staging_dir / "_investigation"``).
    """

    def __init__(
        self,
        investigation_reports: list["InvestigationReport"],
        facts: "FactsManifest",
        investigation_dir: Path,
    ) -> None:
        self._facts = facts
        self._investigation_dir = investigation_dir

        # InvestigationReport.evidence is list[InvestigationEvidence].
        # InvestigationEvidence.unit_name is the unit identifier field.
        self._evidenced_units: set[str] = set()
        self._unit_evidence_path: dict[str, str] = {}
        for report in investigation_reports:
            evidence_list = getattr(report, "evidence", []) or []
            group = getattr(report, "group", "unknown")
            rel_path = f"_investigation/{group}.md"
            for ev in evidence_list:
                unit_name = getattr(ev, "unit_name", None)
                if unit_name:
                    self._evidenced_units.add(unit_name)
                    if unit_name not in self._unit_evidence_path:
                        self._unit_evidence_path[unit_name] = rel_path

    def validate(self, doc_name: str, doc_text: str) -> TraceabilityReport:
        """Check doc_text for phantom absence claims about evidenced units."""
        cited: list[str] = []
        missing: list[str] = []
        phantom_absences: list[AbsenceClaim] = []
        errors: list[str] = []

        for unit in self._facts.units:
            name = unit.name if hasattr(unit, "name") else str(unit)
            pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
            occurrences = list(pattern.finditer(doc_text))
            if not occurrences:
                missing.append(name)
                continue
            cited.append(name)

            # Only check for phantom absences if we have investigation evidence.
            if name not in self._evidenced_units:
                continue

            for match in occurrences:
                start = max(0, match.start() - _ABSENCE_WINDOW)
                end = min(len(doc_text), match.end() + _ABSENCE_WINDOW)
                window = doc_text[start:end]
                if any(pat.search(window) for pat in _PIPELINE_GAP_PATTERNS):
                    # Attributed pipeline gap: the text asserts the evidence
                    # exists and blames the pipeline, not the source.
                    continue
                for abs_pat in _ABSENCE_PATTERNS:
                    m = abs_pat.search(window)
                    if m:
                        evidence_path = self._unit_evidence_path.get(
                            name, "_investigation/<group>.md"
                        )
                        phantom_absences.append(AbsenceClaim(
                            unit_name=name,
                            doc_name=doc_name,
                            matched_phrase=m.group(0),
                            evidence_path=evidence_path,
                        ))
                        break  # One claim per unit per doc

        if phantom_absences:
            errors.append(
                f"{doc_name}: {len(phantom_absences)} phantom absence claim(s) for "
                f"units with investigation evidence: "
                + ", ".join(c.unit_name for c in phantom_absences[:10])
            )

        return TraceabilityReport(
            ok=len(errors) == 0,
            cited_units=cited,
            missing_units=missing,
            phantom_absences=phantom_absences,
            errors=errors,
        )
