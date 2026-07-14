"""Small deterministic writer contract constrained to used evidence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import EvidenceSnapshot, EvidenceSourceClass


class EvidenceCitation(BaseModel):
    evidence_id: int
    label: str
    uri: str | None = None


class GroundedResponse(BaseModel):
    text: str
    citations: list[EvidenceCitation] = Field(default_factory=list)


class EvidenceGroundedWriter:
    """Render a safe summary and refuse citations to merely collected evidence."""

    def write(self, evidence: list[EvidenceSnapshot]) -> GroundedResponse:
        used = sorted(
            (item for item in evidence if item.id is not None and item.used_order is not None),
            key=lambda item: item.used_order or 0,
        )
        sections: list[str] = []
        citations: list[EvidenceCitation] = []
        labels = {
            EvidenceSourceClass.GENERATED_DOC: "Generated documentation",
            EvidenceSourceClass.INDEXED_DOC: "Indexed documentation",
            EvidenceSourceClass.CURRENT_RUNTIME: "Current runtime evidence",
            EvidenceSourceClass.SOURCE_TOPOLOGY: "Source and topology evidence",
            EvidenceSourceClass.CONNECTOR: "Connector evidence",
        }
        for item in used:
            label = labels[item.source_class]
            conflict = " Conflicts with another observation." if item.conflict_group else ""
            sections.append(f"{label}: {item.summary}{conflict} [{item.id}]")
            citations.append(EvidenceCitation(evidence_id=item.id, label=item.title, uri=item.uri))
        return GroundedResponse(
            text="\n".join(sections) if sections else "No used evidence supports an answer.",
            citations=citations,
        )
