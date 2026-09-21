"""Deterministic attribution check for Phase 2 analyses and Phase 3 docs (no LLM).

Phase 2 in the ``claude-cli`` / ``opencode-cli`` lane used to run one sub-agent
per group, so a worker only ever held one group's evidence and could not
confuse two groups. Batching several groups into one worker (the fix for the
3-4-wide sub-agent dispatch that serialised run 259) re-introduces a verified
failure mode from the custom lane's bin-packed Phase 2 tasks: an agent holding
several groups' evidence attributes a component that is real for group A to
group B. On reverseEngineer_ALI run 51 that produced ``tMSSqlOutput_1`` -- real
only for ``AssetCheck_GetLocation`` -- attributed to a sibling job that
actually uses ``tDBOutput_1``. Checking the output against the COMBINED
evidence of the batch lets it pass, because the name is real somewhere in the
batch; only a per-group check catches it.

So this module checks every group's analysis against ONLY that group's own
``_preprocess/`` evidence, and splits what it finds into two defects that are
not equally bad:

* **misattributed** -- the component exists, but in another group's evidence.
  A group that genuinely calls another group's design has a legitimate reason
  to name its components, so this is a signal, not a verdict. The report always
  names the group the component WAS found in.
* **absent** -- the component exists in no group's evidence anywhere in the
  scan. Nothing in this run's evidence could have produced that name; it was
  invented.

Published DOCUMENTS are checked too, and there the references are DECLARED
rather than extracted (plan decision 3: anything Python acts on, the model
declares). Phase 3 already writes one ``_provenance/{DOC}.json`` sidecar per
document, whose entries say which group, unit and components a claim rests on.
Verification of those is set membership -- the declared references against the
evidence the declared group actually holds -- and never touches the document's
prose. Two lanes follow from that, and the report names which one ran for each
document:

* ``declared`` -- the sidecar named at least one component. Only the declared
  references are checked; the document text is not read at all.
* ``inferred`` -- there is no sidecar, it is unreadable, or it declares no
  component. The old regex-over-prose check runs instead. Silently skipping a
  document because its writer omitted a file would be worse than the prose
  dependency this slice is removing, so the fallback stays -- but it is weaker
  by construction and the report says so, so its share can be driven down.

A DECLARED miss is the harder finding: the writer stated where the claim came
from and the evidence does not contain it. Both dimensions are reported
independently -- ``declared``/``inferred`` crossed with ``misattributed``/
``absent`` -- because they answer different questions.

Advisory by design, matching the four Phase 3 final-check gates: it writes
``_assessment/group_attribution.json``, returns warning lines for the run log,
and never raises. The operator decides what to do with a gap.

Calibration: measured against ETTM run 259's 15 real group analyses -- written
by one-agent-per-group, i.e. the UNBATCHED baseline -- this reports zero
findings. A check that is noisy on unbatched output is useless as a batching
safety net. The document lane is calibrated the same way: zero findings on the
werkorder ETTM (20 groups) and EnterpriseServiceBus (24 groups) stagings and on
TEST_V2 part1's 46 published documents, three on run 258's nine documents
(``tSchematron_1``, ``tSchematron_2``, ``tFlowInput_1`` -- none of which exist
anywhere in that run's evidence).

The component-reference regex is deliberately NOT redefined here. It is
imported from ``providers/custom/fabrication_validators`` (which re-exports the
one in ``target_evidence``); a second copy free to drift is the defect class
this codebase has already paid to remove. It matches Talend DI component
instances (``tXxx_N``) only, so a Camel/ESB route component (``cProcessor_1``)
is invisible to both lanes -- a known coverage limit, not a pass.

Analyses are read through ``providers/custom/analysis_store``, so both the flat
``_analysis/{group}.md`` layout and the per-section
``_analysis/{group}/{SECTION}.md`` layout are checked identically.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server.providers.custom import analysis_store
from mcp_server.providers.custom.analysis_store import SectionContractError
from mcp_server.providers.custom.fabrication_validators import (
    component_reference_errors,
    declared_component_reference_errors,
    extract_component_refs,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MODE_DECLARED",
    "MODE_INFERRED",
    "ORIGIN_DECLARED",
    "ORIGIN_INFERRED",
    "AttributionFinding",
    "DocumentAttribution",
    "GroupAttributionReport",
    "ProvenanceEntry",
    "analysis_group_for_slice",
    "analysis_group_names",
    "attribution_warnings",
    "check_group_attribution",
    "declared_refs_by_group",
    "evidence_files_for_group",
    "published_documents",
    "read_provenance_entries",
    "write_group_attribution_report",
]

ANALYSIS_DIRNAME = "_analysis"
PREPROCESS_DIRNAME = "_preprocess"
ASSESSMENT_DIRNAME = "_assessment"
PROVENANCE_DIRNAME = "_provenance"
REPORT_FILENAME = "group_attribution.json"

#: How a document was verified. ``declared`` reads the writer's sidecar and
#: nothing else; ``inferred`` falls back to the regex over the document prose.
MODE_DECLARED = "declared"
MODE_INFERRED = "inferred"

#: Where one finding's component name came from. The same two words, on a
#: finding rather than on a document, because one document can only be checked
#: one way but a report mixes both.
ORIGIN_DECLARED = "declared"
ORIGIN_INFERRED = "inferred"

#: How many findings the JSON report lists before it truncates. Counts are
#: always exact; only the listing is bounded, so a runaway run cannot write a
#: report larger than the documents it is about.
MAX_LISTED_FINDINGS = 500

#: How many findings a warning line names before it says "+N more".
MAX_WARNED_FINDINGS = 5

#: Suffixes ``_preprocess_scan_data`` appends to a group's base name when it
#: writes that group's evidence as more than one file:
#:   ``{group}_part1`` / ``{group}_part2``       -- a group over 40 designs is split
#:   ``{group}_part_ci{k}``                      -- a case-insensitive filesystem
#:                                                  collision (VISE2AssetCheck vs
#:                                                  Vise2AssetCheck)
#:   ``{group}_part_ci{k}_{n}``                  -- a collider that also splits
#: Matched exactly rather than by ``glob(f"{group}*.md")``: the glob makes
#: ``forSuppliersFourps.md`` part of ``forSuppliers``'s evidence (both are real,
#: separate ETTM groups), which is precisely the cross-group leak this module
#: exists to detect.
_SLICE_SUFFIX_RE = re.compile(r"^part(?:_ci\d+(?:_\d+)?|\d+)$")

#: A group whose own name already carries a collision suffix owns its nested
#: split parts, which are named ``{base}_1`` / ``{base}_2`` rather than
#: ``{base}_part1``.
_CI_BASE_RE = re.compile(r"_part_ci\d+$")
_NESTED_SPLIT_RE = re.compile(r"^\d+$")


def evidence_files_for_group(preprocess_dir: Path, group: str) -> list[Path]:
    """Return the ``_preprocess/`` files that are ``group``'s OWN evidence.

    Exact-match based, never prefix-glob based -- see ``_SLICE_SUFFIX_RE``.
    Matching is case-sensitive against the real directory listing so that on a
    case-insensitive filesystem ``Monitoring.md`` is never served as
    ``monitoring``'s evidence (the collision suffix exists precisely because
    those two cannot share a path).
    """
    if not preprocess_dir.is_dir():
        return []
    ci_base = bool(_CI_BASE_RE.search(group))
    matches: list[Path] = []
    for candidate in sorted(preprocess_dir.iterdir()):
        if candidate.suffix != ".md" or candidate.name.startswith("_"):
            continue
        stem = candidate.stem
        if stem == group:
            matches.append(candidate)
            continue
        if not stem.startswith(group + "_"):
            continue
        remainder = stem[len(group) + 1:]
        if _SLICE_SUFFIX_RE.match(remainder):
            matches.append(candidate)
        elif ci_base and _NESTED_SPLIT_RE.match(remainder):
            matches.append(candidate)
    return matches


def analysis_group_for_slice(slice_name: str) -> str:
    """Map a ``_preprocess/`` slice name to the analysis it belongs to.

    The inverse of :func:`evidence_files_for_group`, and the reason the two
    live together: a split group's two slices produce ONE
    ``_analysis/{group}.md`` (the prompt requires the parts to be merged), so
    they must be planned onto the same worker -- a merge that spans two workers
    is a merge nobody owns.

    A collision slice keeps its own name: ``monitoring_part_ci1`` exists
    precisely because ``monitoring`` cannot share a path with ``Monitoring``,
    so stripping the suffix would re-create the collision it was minted to
    avoid. ETTM run 259 wrote ``_analysis/monitoring_part_ci1.md`` for exactly
    that reason.
    """
    nested = re.match(r"^(?P<base>.+_part_ci\d+)_\d+$", slice_name)
    if nested:
        return nested.group("base")
    if _CI_BASE_RE.search(slice_name):
        return slice_name
    split = re.match(r"^(?P<base>.+)_part\d+$", slice_name)
    if split:
        return split.group("base")
    return slice_name


def analysis_group_names(analysis_dir: Path) -> list[str]:
    """Group names with a stored Phase 2 analysis, in either layout.

    Delegated to ``analysis_store.list_groups`` so the flat
    ``_analysis/{group}.md`` and the per-section ``_analysis/{group}/`` layouts
    enumerate identically. A local ``glob("*.md")`` here would count zero groups
    the moment Phase 2 started writing directories -- the check would report
    "nothing to check" and look clean.

    Excludes the ``{group}_summary.md`` navigation aids and every internal
    ``_``-prefixed artifact (bundles, indexes, evidence packs).
    """
    return list(analysis_store.list_groups(Path(analysis_dir)))


def _clip(text: str, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


@dataclass(frozen=True)
class ProvenanceEntry:
    """One declared claim out of a ``_provenance/{DOC}.json`` sidecar.

    Only the fields Python acts on are modelled. ``components`` is the set the
    verification uses; it is read from the entry's own ``components`` list and
    from its ``unit`` field, both of which the writer filled in deliberately.
    The ``claim`` text is NOT mined for component names: it is a quotation, and
    reading structure back out of it is exactly the prose dependency this lane
    removes. That choice is measurable -- on the nine real run-260 sidecars it
    costs 2 of 15 resolvable entries and keeps the rule honest.
    """

    group: str
    unit: str | None
    components: tuple[str, ...]
    claim: str

    @property
    def declares_components(self) -> bool:
        return bool(self.components)


def _entry_components(raw: dict) -> tuple[str, ...]:
    """Component references an entry DECLARES, in sorted order.

    ``components`` may be a list, a tuple or a single string -- a writer that
    wrote one name instead of a list of one is stating the same fact, and
    rejecting it would push the document into the weaker fallback lane for a
    formatting slip. Every declared value is run through the shared
    ``extract_component_refs`` rather than trusted verbatim, so a field holding
    "tMap_1 in OrderLoad_Daily" still yields ``tMap_1`` and a field holding a
    design name yields nothing.
    """
    declared = raw.get("components")
    if isinstance(declared, str):
        candidates = [declared]
    elif isinstance(declared, (list, tuple)):
        candidates = [item for item in declared if isinstance(item, str)]
    else:
        candidates = []
    unit = raw.get("unit")
    if isinstance(unit, str):
        candidates.append(unit)
    refs: set[str] = set()
    for value in candidates:
        refs |= extract_component_refs(value)
    return tuple(sorted(refs))


def read_provenance_entries(path: Path) -> tuple[tuple[ProvenanceEntry, ...], str]:
    """Read one sidecar. Returns ``(entries, problem)``; never raises.

    ``problem`` is empty when the file parsed. Anything else -- absent,
    unreadable, not JSON, not the documented shape -- is reported as the reason
    the document fell back to the inferred lane, because "the writer skipped
    provenance" and "the writer wrote a broken file" are different failures and
    a report that shows neither cannot drive the declared share up.
    """
    if not path.is_file():
        return (), "no sidecar was written"
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        return (), f"sidecar could not be read as JSON ({exc.__class__.__name__})"
    if not isinstance(payload, dict):
        return (), "sidecar is not a JSON object"
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return (), "sidecar has no 'entries' list"

    entries: list[ProvenanceEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        group = raw.get("group")
        if not isinstance(group, str) or not group.strip():
            continue
        unit = raw.get("unit") if isinstance(raw.get("unit"), str) else None
        claim = raw.get("claim") if isinstance(raw.get("claim"), str) else ""
        entries.append(
            ProvenanceEntry(
                group=group.strip(),
                unit=unit,
                components=_entry_components(raw),
                claim=claim,
            )
        )
    if not entries:
        return (), "sidecar declares no usable entries"
    return tuple(entries), ""


def declared_refs_by_group(
    entries: tuple[ProvenanceEntry, ...] | list[ProvenanceEntry],
) -> dict[str, dict[str, str]]:
    """``{group: {component: the first claim that rested on it}}``.

    Deterministic: groups and components are emitted sorted by the caller, and
    the claim kept is the first in sidecar order, so two runs over the same
    sidecar produce the same report.
    """
    by_group: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not entry.components:
            # An entry that rests on no component declares nothing. Creating an
            # empty bucket for its group would make the result truthy and put
            # the whole document in the declared lane with nothing to check --
            # the exact "silently verified nothing" failure this slice exists
            # to prevent.
            continue
        bucket = by_group.setdefault(entry.group, {})
        for component in entry.components:
            bucket.setdefault(component, entry.claim)
    return by_group


@dataclass(frozen=True)
class DocumentAttribution:
    """How one published document was verified, and how far that reached."""

    document: str
    mode: str
    sidecar_present: bool = False
    #: Sidecar entries that parsed, and how many of them named a component.
    #: The ratio is the coverage of the declared path for this document: it is
    #: the number the slice exists to drive up, so it is reported per document
    #: rather than summed away.
    entries: int = 0
    declared_entries: int = 0
    #: Distinct (group, component) pairs actually checked by set membership.
    checked_refs: int = 0
    #: Component references the fallback regex pulled out of the prose. Zero in
    #: the declared lane, where the document text is never read.
    inferred_refs: int = 0
    #: Declared groups with no ``_preprocess/`` evidence file: their references
    #: could not be checked at all. Reported, never counted clean.
    unverifiable_groups: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "mode": self.mode,
            "sidecar_present": self.sidecar_present,
            "entries": self.entries,
            "declared_entries": self.declared_entries,
            "checked_refs": self.checked_refs,
            "inferred_refs": self.inferred_refs,
            "unverifiable_groups": list(self.unverifiable_groups),
            "note": self.note,
        }


@dataclass(frozen=True)
class AttributionFinding:
    """One component reference attributed to evidence that does not hold it."""

    group: str
    component: str
    #: Groups whose own evidence DOES contain this component. Empty means the
    #: component appears nowhere in the scan.
    present_in: tuple[str, ...] = ()
    #: ``declared`` when the writer's provenance sidecar named this component as
    #: the basis of a claim, ``inferred`` when it was read out of prose by the
    #: fallback regex. Orthogonal to ``classification``: what the reference is
    #: wrong about, and how much weight the claim of it carries, are two facts.
    origin: str = ORIGIN_INFERRED
    #: The published document this finding belongs to, or ``None`` for the
    #: Phase 2 analysis lane.
    document: str | None = None
    #: The sidecar's own claim text, so a declared miss can be read without
    #: opening the document. Bounded; empty in the inferred lane.
    claim: str = ""

    @property
    def classification(self) -> str:
        return "misattributed" if self.present_in else "absent"

    @property
    def declared(self) -> bool:
        return self.origin == ORIGIN_DECLARED

    @property
    def severity_rank(self) -> tuple[int, int]:
        """Sort key, lowest first. Declared outranks absent-ness.

        A declared reference is the writer's own statement about where a claim
        came from, so a declared miss is a stronger signal than the same name
        scraped out of prose, where it may be a passing mention or a quotation.
        Within each origin, ``absent`` (nowhere in the scan) still outranks
        ``misattributed`` (real, but another group's).
        """
        return (0 if self.declared else 1, 0 if not self.present_in else 1)

    def _where(self) -> str:
        where = ", ".join(self.present_in[:3])
        more = f" (+{len(self.present_in) - 3} more)" if len(self.present_in) > 3 else ""
        return f"{where}{more}"

    def message(self) -> str:
        if self.document is None:
            # Phase 2 analysis lane. Wording unchanged: operators and the
            # existing report readers depend on it.
            if self.present_in:
                return (
                    f"{self.group}: {self.component} is attributed to {self.group} "
                    f"but is only present in {self._where()}"
                )
            return (
                f"{self.group}: {self.component} is attributed to {self.group} and "
                "exists nowhere in the scan"
            )
        if self.declared:
            claim = f' for the claim "{_clip(self.claim, 80)}"' if self.claim else ""
            if self.present_in:
                return (
                    f"{self.document}: {self.component} is DECLARED as evidence "
                    f"from group {self.group}{claim} but is only present in "
                    f"{self._where()}"
                )
            return (
                f"{self.document}: {self.component} is DECLARED as evidence from "
                f"group {self.group}{claim} and exists nowhere in the scan"
            )
        if self.present_in:
            return (
                f"{self.document}: {self.component} is named in the document and is "
                f"only present in {self._where()}"
            )
        return (
            f"{self.document}: {self.component} is named in the document and exists "
            "nowhere in the scan"
        )

    def to_dict(self) -> dict:
        payload = {
            "group": self.group,
            "component": self.component,
            "classification": self.classification,
            "origin": self.origin,
            "present_in": list(self.present_in),
            "message": self.message(),
        }
        if self.document is not None:
            payload["document"] = self.document
        if self.claim:
            payload["claim"] = _clip(self.claim, 200)
        return payload


@dataclass
class GroupAttributionReport:
    """Result of the check. Advisory: ``ok`` is information, not a gate."""

    analyses_checked: int = 0
    #: Analyses whose group has no ``_preprocess/`` evidence file at all. The
    #: check cannot run for these; an empty allowed set would silently pass
    #: everything, so they are reported separately rather than counted clean.
    groups_without_evidence: tuple[str, ...] = ()
    findings: tuple[AttributionFinding, ...] = ()
    #: Per-group validation strings from
    #: ``fabrication_validators.component_reference_errors`` -- the same text
    #: the custom lane's agent-task ladder produces, so both lanes read alike.
    group_errors: dict[str, list[str]] = field(default_factory=dict)
    #: One entry per published document, in filename order, saying which lane
    #: verified it. Empty when the document lane could not run (no ``_preprocess/``
    #: evidence left on disk, or no published documents).
    documents: tuple[DocumentAttribution, ...] = ()
    #: Document-lane findings. Kept separate from ``findings`` so the Phase 2
    #: calibration ("zero on unbatched analyses") stays a statement about the
    #: analysis lane and cannot be diluted by a second population.
    document_findings: tuple[AttributionFinding, ...] = ()
    #: Per-document validation strings, same role as ``group_errors``.
    document_errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def misattributed(self) -> tuple[AttributionFinding, ...]:
        return tuple(f for f in self.findings if f.present_in)

    @property
    def absent(self) -> tuple[AttributionFinding, ...]:
        return tuple(f for f in self.findings if not f.present_in)

    @property
    def documents_checked(self) -> int:
        return len(self.documents)

    @property
    def declared_documents(self) -> tuple[DocumentAttribution, ...]:
        return tuple(d for d in self.documents if d.mode == MODE_DECLARED)

    @property
    def inferred_documents(self) -> tuple[DocumentAttribution, ...]:
        return tuple(d for d in self.documents if d.mode == MODE_INFERRED)

    @property
    def document_modes(self) -> dict[str, str]:
        return {d.document: d.mode for d in self.documents}

    @property
    def declared_findings(self) -> tuple[AttributionFinding, ...]:
        return tuple(f for f in self.document_findings if f.declared)

    @property
    def ok(self) -> bool:
        return not self.findings and not self.document_findings

    def document_summary_line(self) -> str:
        """The document lane in one line, always naming the declared share.

        The coverage number is the point of the line: a run where every
        document fell back to the prose regex is not a clean run, it is an
        unverified one, and a summary that only reported findings would read
        identically in both cases.
        """
        if not self.documents:
            return "Document attribution: no published documents to check"
        declared = len(self.declared_documents)
        entries = sum(d.entries for d in self.documents)
        declared_entries = sum(d.declared_entries for d in self.documents)
        coverage = (
            f", {declared_entries}/{entries} sidecar entries declared a component"
            if entries
            else ""
        )
        unverifiable = sum(len(d.unverifiable_groups) for d in self.documents)
        unchecked = (
            f", {unverifiable} declared group reference(s) had no evidence file"
            if unverifiable
            else ""
        )
        head = (
            f"Document attribution: {declared}/{len(self.documents)} documents "
            f"verified from declared provenance, "
            f"{len(self.inferred_documents)} from prose (fallback){coverage}{unchecked}"
        )
        if not self.document_findings:
            return f"{head}; no bad references"
        return (
            f"{head}; {len(self.document_findings)} bad reference(s) "
            f"({len(self.declared_findings)} declared)"
        )

    def summary_line(self) -> str:
        if not self.analyses_checked:
            return (
                "Group attribution: no Phase 2 analyses to check "
                "(the evidence was already removed, or Phase 2 produced nothing)"
            )
        if not self.findings:
            # Deliberately NOT ``self.ok``: this line describes the analysis
            # lane, and a document-lane finding must not make it claim that
            # analyses were dirty (nor the reverse).
            unchecked = (
                f", {len(self.groups_without_evidence)} unchecked (no evidence file)"
                if self.groups_without_evidence
                else ""
            )
            return (
                f"Group attribution: clean across {self.analyses_checked} "
                f"group analyses{unchecked}"
            )
        return (
            f"Group attribution: {len(self.findings)} component reference(s) "
            f"across {len({f.group for f in self.findings})} of "
            f"{self.analyses_checked} group analyses are not in that group's own "
            f"evidence ({len(self.misattributed)} present in another group, "
            f"{len(self.absent)} present nowhere in the scan)"
        )

    def to_dict(self) -> dict:
        listed = self.findings[:MAX_LISTED_FINDINGS]
        doc_listed = self.document_findings[:MAX_LISTED_FINDINGS]
        return {
            # 2: the document lane, its per-document mode, and the
            # declared/inferred dimension on every finding.
            "schema_version": 2,
            "ok": self.ok,
            "advisory": True,
            "analyses_checked": self.analyses_checked,
            "finding_count": len(self.findings),
            "misattributed_count": len(self.misattributed),
            "absent_count": len(self.absent),
            "groups_without_evidence": list(self.groups_without_evidence),
            "findings_truncated": len(self.findings) > len(listed),
            "findings": [f.to_dict() for f in listed],
            "group_errors": {g: list(v) for g, v in sorted(self.group_errors.items())},
            "summary": self.summary_line(),
            "documents_checked": self.documents_checked,
            "declared_document_count": len(self.declared_documents),
            "inferred_document_count": len(self.inferred_documents),
            "document_modes": self.document_modes,
            "documents": [d.to_dict() for d in self.documents],
            "document_finding_count": len(self.document_findings),
            "declared_finding_count": len(self.declared_findings),
            "document_findings_truncated": (
                len(self.document_findings) > len(doc_listed)
            ),
            "document_findings": [f.to_dict() for f in doc_listed],
            "document_errors": {
                d: list(v) for d, v in sorted(self.document_errors.items())
            },
            "document_summary": self.document_summary_line(),
        }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # unreadable evidence is a gap, not a crash
        logger.warning("Group attribution: cannot read %s: %s", path, exc)
        return ""


def _read_analysis(analysis_dir: Path, group: str) -> str:
    """One group's analysis text, flat layout or per-section directory.

    ``analysis_store`` owns both layouts; reading ``_analysis/{group}.md``
    directly here would check nothing at all once Phase 2 writes directories,
    and would report that as clean. A group directory that holds no recognised
    section raises ``SectionContractError`` by design -- loud there, reported
    here, because this guard must never be the thing that fails a run.
    """
    try:
        return analysis_store.read_group_analysis(analysis_dir, group)
    except (SectionContractError, OSError, ValueError) as exc:
        logger.warning("Group attribution: cannot read analysis for %s: %s", group, exc)
        return ""


def published_documents(staging_dir: Path) -> list[Path]:
    """Top-level ``*.md`` files, which is what the run publishes.

    An underscore prefix is this pipeline's one universal marker for an
    internal artifact (it is what keeps a file out of the client corpus and out
    of the document count), so it is also what separates a document from a
    staging artifact here.
    """
    staging_dir = Path(staging_dir)
    if not staging_dir.is_dir():
        return []
    return sorted(
        path
        for path in staging_dir.glob("*.md")
        if path.is_file() and not path.name.startswith("_")
    )


def _check_documents(
    staging_dir: Path,
    preprocess_dir: Path,
    universe: dict[str, set[str]],
    group_evidence: dict[str, tuple[frozenset[str], frozenset[str]]],
) -> tuple[
    tuple[DocumentAttribution, ...],
    tuple[AttributionFinding, ...],
    dict[str, list[str]],
]:
    """Verify published documents: declared where possible, prose where not.

    ``group_evidence`` is the per-group ``(refs, stems)`` cache the analysis
    lane already built; a declared group that is not in it yet is resolved on
    demand, because a sidecar may legitimately cite a group whose own analysis
    was never written.
    """
    documents = published_documents(staging_dir)
    if not documents or not universe:
        # No evidence universe means no allowed set, and an empty allowed set
        # would either pass everything or reject everything. The custom lane
        # deletes _preprocess/ in its own Phase 4 and lands here; saying
        # nothing is the correct answer, not "clean".
        return (), (), {}

    provenance_dir = Path(staging_dir) / PROVENANCE_DIRNAME

    def evidence_for(group: str) -> tuple[frozenset[str], frozenset[str]]:
        if group not in group_evidence:
            files = evidence_files_for_group(preprocess_dir, group)
            refs: set[str] = set()
            for path in files:
                refs |= extract_component_refs(_read(path))
            group_evidence[group] = (
                frozenset(refs),
                frozenset(path.stem for path in files),
            )
        return group_evidence[group]

    results: list[DocumentAttribution] = []
    findings: list[AttributionFinding] = []
    errors: dict[str, list[str]] = {}

    for path in documents:
        name = path.stem
        entries, problem = read_provenance_entries(provenance_dir / f"{name}.json")
        by_group = declared_refs_by_group(entries)
        declared_entries = sum(1 for entry in entries if entry.declares_components)

        if by_group:
            checked = 0
            unverifiable: list[str] = []
            declared_misses: list[str] = []
            for group in sorted(by_group):
                allowed, mine = evidence_for(group)
                if not allowed:
                    unverifiable.append(group)
                    continue
                components = sorted(by_group[group])
                checked += len(components)
                unknown = [c for c in components if c not in allowed]
                for component in unknown:
                    elsewhere = tuple(
                        sorted(
                            stem
                            for stem in universe.get(component, ())
                            if stem not in mine
                        )
                    )
                    findings.append(
                        AttributionFinding(
                            group=group,
                            component=component,
                            present_in=elsewhere,
                            origin=ORIGIN_DECLARED,
                            document=name,
                            claim=by_group[group][component],
                        )
                    )
                declared_misses.extend(
                    declared_component_reference_errors(
                        components,
                        allowed,
                        source_label=(
                            f"the evidence for group {group} (the group this "
                            "document's own sidecar named)"
                        ),
                    )
                )
            if declared_misses:
                errors[name] = declared_misses
            results.append(
                DocumentAttribution(
                    document=name,
                    mode=MODE_DECLARED,
                    sidecar_present=True,
                    entries=len(entries),
                    declared_entries=declared_entries,
                    checked_refs=checked,
                    unverifiable_groups=tuple(sorted(unverifiable)),
                    note=(
                        "declared references checked by set membership; the "
                        "document text was not read"
                    ),
                )
            )
            continue

        # Fallback: no sidecar, an unusable one, or one that named no
        # component. Only "exists nowhere in the scan" is decidable here -- a
        # document legitimately spans groups, so a reference that is real in
        # SOME group cannot be called misattributed without a declaration
        # saying which group it was supposed to come from.
        text = _read(path)
        refs = sorted(extract_component_refs(text))
        for component in refs:
            if component in universe:
                continue
            findings.append(
                AttributionFinding(
                    group="",
                    component=component,
                    present_in=(),
                    origin=ORIGIN_INFERRED,
                    document=name,
                )
            )
        fallback_errors = component_reference_errors(
            text,
            frozenset(universe),
            source_label="this run's scanned evidence",
        )
        if fallback_errors:
            errors[name] = fallback_errors
        reason = problem or "sidecar declared no component reference"
        results.append(
            DocumentAttribution(
                document=name,
                mode=MODE_INFERRED,
                sidecar_present=bool(entries),
                entries=len(entries),
                declared_entries=declared_entries,
                checked_refs=0,
                inferred_refs=len(refs),
                note=(
                    f"fallback: {reason}. Component names were read out of the "
                    "document prose, so only references that exist nowhere in "
                    "the scan can be detected"
                ),
            )
        )

    return tuple(results), tuple(findings), errors


def check_group_attribution(staging_dir: Path) -> GroupAttributionReport:
    """Check Phase 2 analyses and published documents against the evidence.

    Two lanes, reported separately (see the module docstring): every group
    analysis against ONLY that group's own ``_preprocess/`` evidence, and every
    published document against the evidence its own provenance sidecar declares
    -- falling back to the prose regex for a document with no usable sidecar.

    Never raises: a missing directory, an unreadable file, a malformed sidecar
    or an analysis for a group with no evidence file is reported, not thrown.
    """
    staging_dir = Path(staging_dir)
    analysis_dir = staging_dir / ANALYSIS_DIRNAME
    preprocess_dir = staging_dir / PREPROCESS_DIRNAME

    groups = analysis_group_names(analysis_dir)

    # Every group's own allowed component set, plus the scan-wide universe used
    # to tell "belongs to another group" from "exists nowhere".
    group_evidence: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for group in groups:
        files = evidence_files_for_group(preprocess_dir, group)
        if not files:
            continue
        refs: set[str] = set()
        for path in files:
            refs |= extract_component_refs(_read(path))
        group_evidence[group] = (
            frozenset(refs),
            frozenset(path.stem for path in files),
        )

    # The universe spans every preprocess file, not just the analysed groups:
    # a component cited by an analysed group may belong to a group whose own
    # analysis was never written (run 259 finished 15 of 20), and calling that
    # "exists nowhere" would be a false accusation.
    universe: dict[str, set[str]] = {}
    if preprocess_dir.is_dir():
        for path in sorted(preprocess_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            for ref in extract_component_refs(_read(path)):
                universe.setdefault(ref, set()).add(path.stem)

    findings: list[AttributionFinding] = []
    group_errors: dict[str, list[str]] = {}
    without_evidence: list[str] = []

    for group in groups:
        entry = group_evidence.get(group)
        if entry is None:
            without_evidence.append(group)
            continue
        allowed, mine = entry
        text = _read_analysis(analysis_dir, group)
        unknown = sorted(extract_component_refs(text) - allowed)
        if not unknown:
            continue
        for component in unknown:
            elsewhere = tuple(
                sorted(stem for stem in universe.get(component, ()) if stem not in mine)
            )
            findings.append(AttributionFinding(group, component, elsewhere))
        errors = component_reference_errors(
            text,
            allowed,
            source_label=(
                f"the evidence for group {group} (another group's evidence in "
                "the same batch does not count)"
            ),
        )
        if errors:
            group_errors[group] = errors

    documents, document_findings, document_errors = _check_documents(
        staging_dir, preprocess_dir, universe, group_evidence
    )

    return GroupAttributionReport(
        analyses_checked=len(groups),
        groups_without_evidence=tuple(without_evidence),
        findings=tuple(findings),
        group_errors=group_errors,
        documents=documents,
        document_findings=document_findings,
        document_errors=document_errors,
    )


def write_group_attribution_report(
    staging_dir: Path, report: GroupAttributionReport
) -> Path | None:
    """Write ``_assessment/group_attribution.json``. Returns None on failure."""
    assessment_dir = Path(staging_dir) / ASSESSMENT_DIRNAME
    path = assessment_dir / REPORT_FILENAME
    try:
        assessment_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Group attribution report could not be written: %s", exc)
        return None
    return path


def attribution_warnings(
    report: GroupAttributionReport, *, report_path: Path | None = None
) -> list[tuple[str, str]]:
    """Log lines for the run, as ``(message, level)`` pairs.

    Findings are ordered by ``severity_rank``: DECLARED misses first (the
    writer stated where the claim came from and the evidence does not hold it),
    then ``absent`` before ``misattributed`` -- a component that exists nowhere
    in the scan was invented, while one that exists in a sibling group may be a
    legitimate cross-group reference.

    The document lane always logs its line when it ran, findings or not: a run
    where every document fell back to the prose regex is unverified rather than
    clean, and only the coverage number tells the two apart.
    """
    if report.analyses_checked == 0 and not report.documents:
        return []

    lines: list[tuple[str, str]] = []
    if report.analyses_checked:
        lines.append(
            (report.summary_line(), "warning" if report.findings else "info")
        )
        ordered = list(report.absent) + list(report.misattributed)
        for finding in ordered[:MAX_WARNED_FINDINGS]:
            lines.append((f"  {finding.message()}", "warning"))
        if len(ordered) > MAX_WARNED_FINDINGS:
            lines.append((f"  (+{len(ordered) - MAX_WARNED_FINDINGS} more)", "warning"))

    if report.documents:
        lines.append((
            report.document_summary_line(),
            "warning" if report.document_findings else "info",
        ))
        doc_ordered = sorted(
            report.document_findings,
            key=lambda f: (f.severity_rank, f.document or "", f.component),
        )
        for finding in doc_ordered[:MAX_WARNED_FINDINGS]:
            lines.append((f"  {finding.message()}", "warning"))
        if len(doc_ordered) > MAX_WARNED_FINDINGS:
            lines.append((
                f"  (+{len(doc_ordered) - MAX_WARNED_FINDINGS} more)",
                "warning",
            ))

    if report.ok:
        return lines

    tail = (
        f"  Attribution is advisory, so the run continues; the full report is "
        f"{ASSESSMENT_DIRNAME}/{REPORT_FILENAME}"
        if report_path is None
        else f"  Attribution is advisory, so the run continues; the full report "
        f"is {report_path.parent.name}/{report_path.name}"
    )
    lines.append((tail, "warning"))
    return lines
