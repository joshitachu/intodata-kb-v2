"""Synthesis Impact Mapper (Living-Document Reingestion, Slice 3).

Maps one group's delta (its ``FieldChange`` list, plus any unit identifiers
the delta fully removed) to the specific ``## Heading`` sections of a
summary/cross-cutting document -- a document with no owning group, e.g.
``09_EXECUTIVE_SUMMARY`` (``phase2_sections: []`` in ``doc_catalog.yaml``,
equivalently ``GroupDocDependency.cross_cutting_docs()``) -- that actually
reference the changed group, so ``TruePatchExecutor`` can amend ONLY those
sections in place instead of re-deriving the whole document on every push.

Design
------
Group-scoped documents have a static component_type -> section map
(``patch_section_map.py``) because the doc's per-section provenance is
known ahead of time from the doc catalog. Summary documents have no such
map -- they are holistic prose that may or may not mention any given group
in any given section. So the mapper works the other direction: it reads the
document's OWN current text, splits it into its existing ``## Heading``
sections, and asks "does this section's content actually reference this
group (by name) or any of its changed unit identifiers (job/component
names drawn from the field diff)?". Only sections that answer yes are
eligible for amendment; everything else is left untouched by construction
(the executor never calls the model about a heading the mapper didn't
return), which is what keeps unrelated sections byte-identical (Slice 3
AC1/AC2).

A document that does not mention the group *anywhere* in its current text
needs no action at all (this push genuinely has nothing to say about it).
A document that DOES mention the group somewhere, but not inside any
individual ``## Heading`` section the mapper can isolate (e.g. only in an
H1 title, a preamble before the first heading, or the document has no H2
structure at all), returns an *unmapped* result: the change cannot be
safely expressed as a section-level edit, and the caller must fall back to
scoped-local re-derivation of that one document (Slice 3 AC3) -- never a
whole-repository rebuild.

Pure module: no I/O, no config, no model calls. Operates entirely on
already-loaded document text and ``FieldChange`` data, so it is directly
unit-testable against fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mcp_server.services.patch_section_map import FieldChange, extract_h2_sections

# Whole-token identifier match (covers underscored component/job names like
# `tMap_1`, `Job_Payroll_3`, `GROUP_A`).
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class SynthesisMapping:
    """Result of mapping one group's delta against one summary document.

    ``headings`` -- ``## Heading`` titles in the document that reference
    this group and are therefore safe to rewrite in place.
    ``removed_identifiers`` -- unit identifiers this delta deleted that were
    found (as whole tokens) inside ``headings``; the executor must verify
    they are gone from the rewritten content, not merely "reconciled"
    (row/section removal, Slice 3 AC2).
    """

    headings: frozenset[str] = field(default_factory=frozenset)
    removed_identifiers: frozenset[str] = field(default_factory=frozenset)

    @property
    def mapped(self) -> bool:
        """True when at least one section was safely isolated for rewrite."""
        return bool(self.headings)


def unit_identifiers(group: str, field_changes: list[FieldChange]) -> frozenset[str]:
    """Return the distinct job/component identifiers a group's delta touches.

    Parsed from each ``FieldChange.field_path`` (``"(job_name, unique_name,
    param_name)"``). Always includes the bare ``group`` name itself so a
    document that merely names the group (without naming any specific unit)
    still counts as a reference.
    """
    ids: set[str] = {group} if group else set()
    for fc in field_changes:
        inner = fc.field_path.strip("()")
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 2:
            job_name, unique_name = parts[0], parts[1]
            if job_name:
                ids.add(job_name)
            if unique_name:
                ids.add(unique_name)
    return frozenset(i for i in ids if i)


def removed_unit_identifiers(
    prev_jobs: list[dict], curr_jobs: list[dict]
) -> frozenset[str]:
    """Return identifiers present in ``prev_jobs`` but absent from ``curr_jobs``.

    Covers both whole jobs dropped entirely and individual components
    removed from a surviving job. Pure over already-loaded job dicts (the
    same lists ``true_patch_executor`` already loads for the field diff --
    no extra file I/O).
    """

    def _units(jobs: list[dict]) -> set[str]:
        found: set[str] = set()
        for job in jobs or []:
            if not isinstance(job, dict):
                continue
            job_name = str(job.get("name") or "")
            if job_name:
                found.add(job_name)
            for comp in job.get("components") or []:
                if isinstance(comp, dict):
                    unique_name = str(comp.get("unique_name") or "")
                    if unique_name:
                        found.add(unique_name)
        return found

    return frozenset(_units(prev_jobs) - _units(curr_jobs))


def doc_references_group(
    doc_text: str, group: str, field_changes: list[FieldChange]
) -> bool:
    """True when ``doc_text`` mentions the group or any of its unit identifiers.

    Checked over the WHOLE document (not just isolated ``## Heading``
    sections) -- this is the "is this push relevant to this document at
    all" gate, distinct from ``SynthesisImpactMapper.map_sections`` which
    additionally requires the reference to be isolable to a specific
    section before it is safe to amend.
    """
    ids = unit_identifiers(group, field_changes)
    if not ids:
        return False
    tokens = set(_TOKEN_RE.findall(doc_text))
    if tokens & ids:
        return True
    return bool(group) and group.lower() in doc_text.lower()


def verify_removal(rewritten_heading_text: str, removed_identifiers: frozenset[str]) -> set[str]:
    """Return the subset of ``removed_identifiers`` still present in the rewrite.

    Empty result means removal succeeded -- none of the deleted units'
    identifiers survived into the patched section. A non-empty result means
    the model left a dangling reference to something that no longer exists
    in the codebase; the caller must reject the amendment (Slice 3 AC2).
    """
    if not removed_identifiers:
        return set()
    tokens = set(_TOKEN_RE.findall(rewritten_heading_text))
    return {rid for rid in removed_identifiers if rid in tokens}


class SynthesisImpactMapper:
    """Maps a group delta to the summary-doc sections that reference it."""

    @staticmethod
    def map_sections(
        *,
        doc_text: str,
        group: str,
        field_changes: list[FieldChange],
        removed_identifiers: frozenset[str] = frozenset(),
    ) -> SynthesisMapping:
        """Return the :class:`SynthesisMapping` for one (doc, group) pair.

        A ``## Heading`` section is included when its own content contains
        the group name (case-insensitive substring -- summary prose commonly
        re-cases group names) or any unit identifier from ``field_changes``
        as a whole token (case-sensitive -- these are code-derived names).
        """
        ids = unit_identifiers(group, field_changes)
        sections = extract_h2_sections(doc_text)
        matched_headings: set[str] = set()
        matched_removed: set[str] = set()

        for title, content in sections.items():
            tokens = set(_TOKEN_RE.findall(content))
            hit = bool(tokens & ids) or (
                bool(group) and group.lower() in content.lower()
            )
            if not hit:
                continue
            matched_headings.add(title)
            matched_removed.update(rid for rid in removed_identifiers if rid in tokens)

        return SynthesisMapping(
            headings=frozenset(matched_headings),
            removed_identifiers=frozenset(matched_removed),
        )
