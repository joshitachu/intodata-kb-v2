"""Scanner capability ledger (deterministic, no LLM).

One table answering "what did this scan see, and what could it not
model?". Every row is derived from the scan result itself -- there is no
hand-maintained list of "supported features" that can drift from what the
code actually does.

Two rules:

* **Only observed constructs appear.** A row with a count of zero is
  omitted; absence in the ledger means the scan contained none of that
  construct.
* **A gap is a row, not a silence.** An unresolved reference, a dynamic
  destination, an unparsed directory and a schema with a dynamic column
  each get their own row with an explicit status, so a reader can see the
  boundary of the evidence instead of inferring it.
"""

from __future__ import annotations

import re
from collections import Counter

from intodoc.models import CapabilityCount, CapabilityLedger, ScanResult

# Talend palette prefix conventions (a lowercase family letter followed by
# an uppercase word). Matched as a convention rather than against a list of
# known component names, which no list could keep up with.
_DI_COMPONENT_RE = re.compile(r"^t[A-Z]")
_CAMEL_COMPONENT_RE = re.compile(r"^c[A-Z]")

_CAPABILITY_VERSION = "talend-capability-2"


class _Rows:
    """Accumulator that drops zero counts and keeps insertion order."""

    def __init__(self) -> None:
        self._rows: list[CapabilityCount] = []

    def add(
        self, category: str, construct: str, status: str, count: int, note: str = "",
    ) -> None:
        if count:
            self._rows.append(CapabilityCount(
                category=category, construct=construct,
                status=status, count=count, note=note,  # type: ignore[arg-type]
            ))

    @property
    def rows(self) -> list[CapabilityCount]:
        return self._rows


def _component_rows(scan_result: ScanResult, rows: _Rows) -> None:
    joblet_invocations = sum(
        reference.relation_type == "job_uses_joblet"
        for job in scan_result.jobs for reference in job.artifact_refs
    )
    joblet_component_names = {
        reference.component_name
        for job in scan_result.jobs for reference in job.artifact_refs
        if reference.relation_type == "job_uses_joblet"
    }
    di = camel = custom = disabled = 0
    for job in scan_result.jobs:
        for component in job.components:
            if not component.active:
                disabled += 1
            if _DI_COMPONENT_RE.match(component.component_type):
                di += 1
            elif _CAMEL_COMPONENT_RE.match(component.component_type):
                camel += 1
            elif component.unique_name not in joblet_component_names:
                custom += 1
    rows.add("component", "di_component", "supported", di)
    rows.add("component", "camel_component", "supported", camel)
    rows.add("component", "joblet_invocation", "supported", joblet_invocations,
             "node whose FAMILY is Joblets: the component name IS the joblet")
    rows.add("component", "custom_component", "partial", custom,
             "component name matches no Talend palette prefix; parameters and "
             "code are captured, its palette semantics are unknown")
    rows.add("component", "disabled", "supported", disabled,
             "ACTIVATE=false: serialized in the design but never executed")
    rows.add("component", "disabled_connection", "supported", sum(
        not connection.active
        for job in scan_result.jobs for connection in job.connections
    ), "link present in the design but disabled")


def _schema_rows(scan_result: ScanResult, rows: _Rows) -> None:
    counts: Counter[str] = Counter()
    dynamic = 0
    for job in scan_result.jobs:
        for component in job.components:
            for schema in component.schemas:
                counts[schema.declaration] += 1
                if schema.dynamic:
                    dynamic += 1
    rows.add("schema", "inline", "supported", counts["built_in"],
             "columns declared on the component itself")
    rows.add("schema", "repository", "partial", counts["repository"],
             "repository schema id captured and the columns are carried on the "
             "component; the metadata schema artifact itself is not parsed")
    rows.add("schema", "undeclared", "unresolved", counts["unknown"],
             "no SCHEMA*:SCHEMA_TYPE parameter could be bound to this connector")
    rows.add("schema", "dynamic", "dynamic", dynamic,
             "contains an id_Dynamic column: the column list is a lower bound "
             "and field-level coverage cannot be complete")


def _mapping_rows(scan_result: ScanResult, accounting: list, rows: _Rows) -> None:
    by_kind: dict[str, list] = {}
    for record in accounting:
        by_kind.setdefault(record.mapper_kind, []).append(record)

    tmaps = by_kind.get("tmap", [])
    rows.add("mapping.tmap", "component", "supported", len(tmaps))
    rows.add("mapping.tmap", "entry", "supported",
             sum(record.written_fields for record in tmaps),
             "output columns written by a tMap")
    rows.add("mapping.tmap", "lookup", "supported",
             sum(len(record.lookup_tables) for record in tmaps))
    rows.add("mapping.tmap", "filter", "supported",
             sum(len(record.filters) for record in tmaps))
    rows.add("mapping.tmap", "reject_output", "supported",
             sum(len(record.reject_outputs) for record in tmaps))
    rows.add("mapping.tmap", "dynamic_or_unresolved_field", "dynamic",
             sum(record.dynamic_unresolved for record in tmaps),
             "written column whose source is a runtime value or an "
             "identifier that is not a declared input")
    rows.add("mapping.tmap", "unenumerable_input", "dynamic",
             sum(len(record.unenumerable_inputs) for record in tmaps),
             "input whose column list cannot be enumerated (dynamic schema)")

    txmlmaps = by_kind.get("txmlmap", [])
    rows.add("mapping.txmlmap", "component", "supported", len(txmlmaps))
    rows.add("mapping.txmlmap", "mapped_field", "supported",
             sum(record.written_fields for record in txmlmaps))
    rows.add("mapping.txmlmap", "resolved_connection", "supported", sum(
        len(txmap.resolved_connections)
        for job in scan_result.jobs for txmap in job.txmlmap_data
    ))
    rows.add("mapping.txmlmap", "unresolved_connection", "unresolved", sum(
        len(txmap.unresolved_connections)
        for job in scan_result.jobs for txmap in job.txmlmap_data
    ), "positional EMF proxy reference that names no captured tree node")

    tdm = by_kind.get("tdm", [])
    rows.add("mapping.tdm", "map", "supported", len(scan_result.tdm_maps))
    rows.add("mapping.tdm", "field", "supported", sum(
        len(tree.fields)
        for mapping in scan_result.tdm_maps
        for tree in (mapping.input_tree, mapping.output_tree)
        if tree is not None
    ))
    rows.add("mapping.tdm", "mapped_field", "supported",
             sum(record.written_fields for record in tdm))
    rows.add("mapping.tdm", "unresolved_ownership", "unresolved", sum(
        mapping.association == "unresolved" for mapping in scan_result.tdm_maps
    ), "map is not under Maps/Jobs|Routes/<name>/, so the export carries no "
       "evidence of which design owns it")

    xslt = by_kind.get("xslt", [])
    rows.add("mapping.xslt", "transform", "supported", sum(
        transform.parse_outcome == "parsed" for transform in scan_result.xslt_transforms
    ))
    rows.add("mapping.xslt", "transform", "partial", sum(
        transform.parse_outcome == "partial" for transform in scan_result.xslt_transforms
    ), "stylesheet uses instructions this parser does not model")
    rows.add("mapping.xslt", "transform", "unsupported", sum(
        transform.parse_outcome == "unsupported"
        for transform in scan_result.xslt_transforms
    ), "stylesheet could not be parsed as XML")
    rows.add("mapping.xslt", "mapped_field", "supported",
             sum(record.written_fields for record in xslt))
    rows.add("mapping.xslt", "unreferenced_transform", "unresolved", sum(
        not transform.referenced_by for transform in scan_result.xslt_transforms
    ), "no component parameter names this stylesheet, so its caller is unknown")


def _structure_rows(scan_result: ScanResult, rows: _Rows) -> None:
    for outcome, status, note in (
        ("parsed", "supported", ""),
        ("partial", "partial", "contains element tags this parser does not model"),
        ("unsupported", "unsupported", "no rootElem, or not parseable as XML"),
    ):
        rows.add("structure", "definition", status, sum(
            structure.parse_outcome == outcome for structure in scan_result.structures
        ), note)
    rows.add("structure", "element", "supported",
             sum(structure.element_count for structure in scan_result.structures))


def _context_rows(scan_result: ScanResult, rows: _Rows) -> None:
    manifest = scan_result.manifest
    rows.add("context", "repository_resolved", "supported", sum(
        len(links) for links in manifest.job_context_links.values()
    ), "bound by Talend Property id, not by folder or name")
    unresolved = [
        reference
        for references in manifest.unresolved_context_refs.values()
        for reference in references
    ]
    rows.add("context", "ambiguous", "unresolved", sum(
        reference.get("resolution") == "ambiguous" for reference in unresolved
    ), "repository context id matches more than one parsed context group")
    rows.add("context", "unresolved", "unresolved", sum(
        reference.get("resolution") != "ambiguous" for reference in unresolved
    ), "repository context id is not present in this scan")
    rows.add("context", "built_in", "supported", sum(
        1
        for job in scan_result.jobs
        for group in job.context_groups
        for variable in group.variables
        if not variable.repository_context_id
        or variable.repository_context_id.lower() == "built-in"
    ), "value lives in the job itself, not in a repository context group")


def _reference_rows(scan_result: ScanResult, rows: _Rows) -> None:
    calls = [reference for job in scan_result.jobs for reference in job.trunjob_refs]
    rows.add("reference.job_call", "identity", "supported", sum(
        reference.reference_source == "identity" for reference in calls
    ), "resolved from the child's repository GUID")
    rows.add("reference.job_call", "label", "partial", sum(
        reference.reference_source == "label" for reference in calls
    ), "resolved from the Studio label cache because no GUID resolved; the "
       "label is stale whenever the child was renamed")
    rows.add("reference.job_call", "dynamic", "dynamic", sum(
        reference.dynamic_job for reference in calls
    ), "USE_DYNAMIC_JOB: the child is chosen at runtime")
    rows.add("reference.job_call", "resolved", "supported", sum(
        bool(reference.resolved_job_id) for reference in calls
    ), "target design was found in this scan")
    rows.add("reference.job_call", "ambiguous", "unresolved", sum(
        reference.resolution == "ambiguous" for reference in calls
    ), "several designs carry the target name and the GUID did not narrow it")
    rows.add("reference.job_call", "unresolved", "unresolved", sum(
        reference.resolution == "unresolved" for reference in calls
    ), "target design is not present in this scan")

    for relation, category in (
        ("route_invokes_job", "reference.route_invocation"),
        ("job_uses_joblet", "reference.joblet"),
    ):
        references = [
            reference for job in scan_result.jobs
            for reference in job.artifact_refs
            if reference.relation_type == relation
        ]
        rows.add(category, "identity", "supported", sum(
            reference.reference_source == "identity" for reference in references
        ))
        rows.add(category, "label", "partial", sum(
            reference.reference_source == "label" for reference in references
        ), "resolved from the cached label, not the repository GUID")
        rows.add(category, "resolved", "supported", sum(
            bool(reference.resolved_artifact_id) for reference in references
        ))
        rows.add(category, "ambiguous", "unresolved", sum(
            reference.resolution == "ambiguous" for reference in references
        ))
        rows.add(category, "unresolved", "unresolved", sum(
            reference.resolution == "unresolved" for reference in references
        ))

    manifest = scan_result.manifest
    rows.add("reference.routine", "resolved", "supported", sum(
        len(links) for links in manifest.job_routine_links.values()
    ))
    routine_gaps = [
        reference
        for references in manifest.unresolved_routine_refs.values()
        for reference in references
    ]
    rows.add("reference.routine", "ambiguous", "unresolved", sum(
        reference.get("resolution") == "ambiguous" for reference in routine_gaps
    ))
    rows.add("reference.routine", "unresolved", "unresolved", sum(
        reference.get("resolution") != "ambiguous" for reference in routine_gaps
    ), "routine name has no implementation in the same source root")

    declarations: Counter[str] = Counter()
    unbound = 0
    for job in scan_result.jobs:
        for component in job.components:
            if not component.declaration:
                continue
            declarations[component.declaration] += 1
            if component.declaration == "repository" and not component.metadata_connection_ref:
                unbound += 1
    rows.add("reference.connection", "repository", "supported", declarations["repository"])
    rows.add("reference.connection", "built_in", "supported", declarations["built_in"],
             "connection details are inline on the component")
    rows.add("reference.connection", "unresolved", "unresolved", unbound,
             "component declares a repository connection whose id is not in this scan")

    resource_refs = manifest.resource_refs
    rows.add("reference.resource", "exact", "supported", sum(
        reference.get("resolution") == "exact" for reference in resource_refs
    ), "component parameter names a parsed structure/xslt/tdm/service file")
    rows.add("reference.resource", "unresolved", "unresolved", sum(
        reference.get("resolution") != "exact" for reference in resource_refs
    ), "referenced support artifact is absent from the scan, or the name is ambiguous")


def _channel_rows(scan_result: ScanResult, rows: _Rows) -> None:
    bindings: Counter[str] = Counter(
        str(channel.get("channel_binding", "unknown"))
        for channel in scan_result.manifest.artifact_channels
    )
    rows.add("channel", "literal_destination", "supported", bindings["literal"],
             "destination/URI is a literal, so it is a matchable channel identity")
    rows.add("channel", "dynamic_destination", "dynamic", bindings["dynamic"],
             "destination/URI is a context/globalMap expression: it resolves per "
             "environment and is deliberately NOT published as a shared channel")
    rows.add("channel", "absent_destination", "unresolved", bindings["absent"],
             "component declares no destination parameter value")
    rows.add("channel", "unclassified_destination", "unresolved", bindings["unknown"],
             "channel record predates binding classification")


def _service_rows(scan_result: ScanResult, rows: _Rows) -> None:
    for outcome, status, note in (
        ("parsed", "supported", ""),
        ("partial", "partial", "no recognizable portType/operation was found"),
        ("unsupported", "unsupported", "could not be parsed as XML"),
    ):
        rows.add("service", "definition", status, sum(
            service.parse_outcome == outcome for service in scan_result.services
        ), note)
    rows.add("service", "operation", "supported",
             sum(len(service.operations) for service in scan_result.services))


def _unscanned_rows(scan_result: ScanResult, rows: _Rows) -> None:
    for warning in scan_result.warnings:
        if warning.kind != "unscanned_category":
            continue
        rows.add(
            "unscanned_directory", warning.file or "(unknown)", "unsupported", 1,
            warning.message,
        )


def build_capability_ledger(scan_result: ScanResult) -> CapabilityLedger:
    """Build the ledger for one completed scan.

    Mapping counts come from `ScanResult.mapping_accounting` when it has
    already been built, and are computed on the fly otherwise, so the two
    artifacts can never report different numbers for the same tMap.
    """
    from intodoc.mapping_accounting import build_mapping_accounting

    accounting = scan_result.mapping_accounting or build_mapping_accounting(scan_result)
    rows = _Rows()
    _component_rows(scan_result, rows)
    _schema_rows(scan_result, rows)
    _mapping_rows(scan_result, accounting, rows)
    _structure_rows(scan_result, rows)
    _context_rows(scan_result, rows)
    _reference_rows(scan_result, rows)
    _channel_rows(scan_result, rows)
    _service_rows(scan_result, rows)
    _unscanned_rows(scan_result, rows)
    return CapabilityLedger(
        capability_version=_CAPABILITY_VERSION,
        file_outcomes=dict(sorted(Counter(
            str(entry.get("outcome", "unknown"))
            for entry in scan_result.source_inventory
        ).items())),
        constructs=rows.rows,
    )
