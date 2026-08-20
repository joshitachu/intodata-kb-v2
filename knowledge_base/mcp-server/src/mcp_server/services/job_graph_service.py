"""Build the Level-2 job component graph from per-job scan cache data.

Pure transformation layer (no I/O): the projects route loads the per-job scan
JSON (the same loader used by the job detail endpoint) and this module turns
it into a `JobGraphResponse` for the topology drill-down UI.

Contract notes (verified against real scan output):
- Edges come exclusively from the job's `connections` array. The per-component
  `incoming_connections` / `outgoing_connections` fields are unreliable — they
  are empty on real scans even when `connections` is fully populated.
- Trigger links (`line_style != 0`, connector names like COMPONENT_OK /
  RUN_IF / SUBJOB_OK) are flagged `is_control_flow` so the frontend can style
  them apart from data-flow rows (only FLOW/LOOKUP edges carry data).
- `tmap_data` entries carry no component name in the scan format; they are
  appended in node parse order, so the Nth entry belongs to the Nth tMap
  component — the same positional association the engineering-facts extractor
  uses (providers/custom/engineering_facts/extractors/talend.py).
- `param_summary` is allowlist-based (high-signal params shared with the
  Phase 3 evidence builder) with an additional credential deny-pattern and
  value-level URL userinfo masking on top. The scanner already redacts
  credential values to "[REDACTED]"; the extra filters are defense in depth so
  passwords/secrets/tokens can never reach an API response even on old scans.
"""

from __future__ import annotations

import re
from typing import Any

from mcp_server.providers.custom.target_evidence import (
    HIGH_SIGNAL_PARAM_NAMES,
    HIGH_SIGNAL_PARAM_PATTERN,
    component_code,
    param_map,
)
from mcp_server.schemas import (
    JobGraphEdge,
    JobGraphNode,
    JobGraphResponse,
    TMapTableSummary,
)

# Role classification is by component-type pattern (never by hardcoded
# job/group/project names). Order of precedence: orchestration and logging
# sets first (their names may also contain Input/Output-like substrings),
# then Input/Output naming convention, then transform as the default.
_ORCHESTRATION_TYPES = frozenset({
    "tRunJob", "tPrejob", "tPostjob", "tLoop", "tInfiniteLoop", "tForeach",
    "tParallelize", "tSleep", "tFlowToIterate", "tIterateToFlow",
})
_ORCHESTRATION_PATTERN = re.compile(r"^t(WaitFor|FileWatcher)")
_LOGGING_TYPES = frozenset({
    "tLogRow", "tLogCatcher", "tStatCatcher", "tFlowMeter", "tFlowMeterCatcher",
    "tAssertCatcher", "tWarn", "tDie", "tChronometerStart", "tChronometerStop",
})

# Connector names that are execution triggers, not data rows.
_TRIGGER_CONNECTOR_PATTERN = re.compile(
    r"^(ON_)?(COMPONENT|SUBJOB)_(OK|ERROR)$|^RUN_IF$"
)

# Deny-pattern applied on top of the high-signal allowlist: a param whose name
# looks credential-bearing never enters param_summary, whatever its value.
_CREDENTIAL_PARAM_PATTERN = re.compile(
    r"(PASS|PWD|SECRET|TOKEN|CREDENTIAL|KEYSTORE|TRUSTSTORE|PRIVATE_?KEY|API_?KEY|AUTHORIZATION)",
    re.IGNORECASE,
)
_REDACTED_VALUE = "[REDACTED]"
# URLs may embed credentials as userinfo (scheme://user:secret@host).
_URL_USERINFO_RE = re.compile(r"(\w+://[^/@:\s]+):[^@\s]+@")

_MAX_SUMMARY_PARAMS = 8
_MAX_SUMMARY_VALUE_LEN = 120

_TMAP_TYPE = "tMap"
_TRUNJOB_TYPE = "tRunJob"

_COMPONENT_EXPLANATIONS = {
    "tMap": "Maps, joins, filters, and transforms rows of data.",
    "tXMLMap": "Maps hierarchical XML data.",
    "tRunJob": "Starts another Talend Job.",
    "tSetGlobalVar": "Stores a value for later components in this Job.",
    "tStatCatcher": "Captures execution statistics for monitoring or logging.",
    "tLogCatcher": "Captures errors and warnings for logging.",
    "tLogRow": "Writes row values to the execution log.",
    "tFlowToIterate": "Runs the next branch once for every incoming row.",
    "tIterateToFlow": "Converts iteration values back into rows of data.",
    "tFixedFlowInput": "Creates rows from configured values.",
    "tAdvancedFileOutputXML": "Writes hierarchical XML data to a file.",
    "tFileInputXML": "Reads hierarchical XML data from a file.",
    "tFileOutputXML": "Writes hierarchical XML data to a file.",
}

_CONNECTOR_EXPLANATIONS = {
    "FLOW": "Transfers rows of data.",
    "LOOKUP": "Supplies lookup rows to a mapping or join.",
    "MERGE": "Merges rows into the following component.",
    "ITERATE": "Starts the next component once for each incoming item; carries iteration values rather than a row flow.",
    "SUBJOB_OK": "Starts after the preceding subjob succeeds; carries no data.",
    "SUBJOB_ERROR": "Starts after the preceding subjob fails; carries no data.",
    "COMPONENT_OK": "Starts after the preceding component succeeds; carries no data.",
    "COMPONENT_ERROR": "Starts after the preceding component fails; carries no data.",
    "RUN_IF": "Starts only when its configured condition is true; carries no data.",
    "ON_COMPONENT_OK": "Starts after the preceding component succeeds; carries no data.",
    "ON_COMPONENT_ERROR": "Starts after the preceding component fails; carries no data.",
    "ON_SUBJOB_OK": "Starts after the preceding subjob succeeds; carries no data.",
    "ON_SUBJOB_ERROR": "Starts after the preceding subjob fails; carries no data.",
}


def derive_component_role(component_type: str) -> str:
    """Classify a Talend component type as input|transform|output|orchestration|logging."""
    if component_type in _ORCHESTRATION_TYPES or _ORCHESTRATION_PATTERN.match(component_type):
        return "orchestration"
    if component_type in _LOGGING_TYPES:
        return "logging"
    if "Input" in component_type:
        return "input"
    if "Output" in component_type:
        return "output"
    return "transform"


def component_display_label(component: dict[str, Any]) -> str:
    """Return Talend's human label without changing the technical identity."""
    label = param_map(component).get("LABEL", "").strip()
    if len(label) >= 2 and label[0] == label[-1] and label[0] in {'"', "'"}:
        label = label[1:-1].strip()
    if not label or label == str(component.get("unique_name") or ""):
        return ""
    return re.sub(r"\s+", " ", label)[:120]


def explain_component_type(component_type: str, platform: str = "talend") -> str:
    """Plain-language component meaning without pretending every scan is Talend."""
    normalized_platform = platform.strip().lower()
    if normalized_platform == "talend" and component_type in _COMPONENT_EXPLANATIONS:
        return _COMPONENT_EXPLANATIONS[component_type]
    if normalized_platform != "talend":
        generic_explanations = {
            "CodeFile": "Source-code file analyzed as part of this application.",
            "DocumentFile": "Documentation file that provides project context.",
            "ConfigFile": "Configuration file used by the application.",
            "DataFile": "Data file used or produced by the application.",
        }
        return generic_explanations.get(
            component_type,
            f"{component_type or 'Project artifact'} analyzed from the source project.",
        )
    lowered = component_type.lower()
    technology = component_type.removeprefix("t") or "configured technology"
    if "connection" in lowered:
        return f"Opens or closes a {technology} connection."
    if "input" in lowered:
        return f"Reads data using the Talend {component_type} component."
    if "output" in lowered:
        return f"Writes data using the Talend {component_type} component."
    return f"Processes data using the Talend {component_type} component."


def explain_connector(
    connector_name: str,
    is_control_flow: bool,
    platform: str = "talend",
) -> str:
    """Explain a technical connection without inferring business intent."""
    if platform.strip().lower() != "talend":
        if is_control_flow:
            return "Controls when the following artifact is processed; carries no data."
        return "References or depends on the following project artifact."
    normalized = connector_name.upper()
    if normalized in _CONNECTOR_EXPLANATIONS:
        return _CONNECTOR_EXPLANATIONS[normalized]
    if is_control_flow:
        return "Controls when the following component starts; carries no row data."
    return "Transfers rows of data between components."


def _sanitize_param_value(value: str) -> str:
    """Mask URL userinfo secrets and bound the value length."""
    masked = _URL_USERINFO_RE.sub(r"\1:***@", value)
    if len(masked) > _MAX_SUMMARY_VALUE_LEN:
        masked = masked[: _MAX_SUMMARY_VALUE_LEN - 3] + "..."
    return masked


def build_param_summary(component: dict[str, Any]) -> dict[str, str]:
    """Bounded, credential-filtered high-signal param summary for one node."""
    summary: dict[str, str] = {}
    params = param_map(component)
    for name in sorted(params):
        if len(summary) >= _MAX_SUMMARY_PARAMS:
            break
        if _CREDENTIAL_PARAM_PATTERN.search(name):
            continue
        if name not in HIGH_SIGNAL_PARAM_NAMES and not HIGH_SIGNAL_PARAM_PATTERN.search(name):
            continue
        value = params[name].strip()
        if not value or value == _REDACTED_VALUE:
            continue
        summary[name] = _sanitize_param_value(value)
    return summary


def _resolve_subjob_target(
    component: dict[str, Any],
    trunjob_index: int,
    trunjob_refs: list[dict[str, Any]],
) -> str | None:
    """Child job name for a tRunJob node.

    The PROCESS param (field_type PROCESS_TYPE) carries the child job name
    directly; the positional trunjob_refs entry is the fallback for scans
    where the param is absent.
    """
    process = param_map(component).get("PROCESS", "").strip()
    if process:
        return process
    if 0 <= trunjob_index < len(trunjob_refs):
        child = str(trunjob_refs[trunjob_index].get("child_job") or "").strip()
        if child:
            return child
    return None


def _build_nodes(
    job_data: dict[str, Any],
    descriptions: dict[str, str],
    platform: str,
) -> list[JobGraphNode]:
    trunjob_refs: list[dict[str, Any]] = list(job_data.get("trunjob_refs") or [])
    nodes: list[JobGraphNode] = []
    trunjob_seen = 0
    for comp in job_data.get("components") or []:
        unique_name = str(comp.get("unique_name") or "")
        component_type = str(comp.get("component_type") or "")
        subjob_target: str | None = None
        if component_type == _TRUNJOB_TYPE:
            subjob_target = _resolve_subjob_target(comp, trunjob_seen, trunjob_refs)
            trunjob_seen += 1
        nodes.append(JobGraphNode(
            unique_name=unique_name,
            display_label=component_display_label(comp),
            component_type=component_type,
            type_explanation=explain_component_type(component_type, platform),
            role=derive_component_role(component_type),
            has_subgraph=component_type == _TMAP_TYPE,
            subjob_target=subjob_target,
            connection_ref=str(comp.get("metadata_connection_ref") or ""),
            param_summary=build_param_summary(comp),
            has_code=bool(component_code(comp).strip()),
            # Empty when no doc row matches -- the anti-fabrication default.
            description=descriptions.get(unique_name, ""),
        ))
    return nodes


def _build_edges(job_data: dict[str, Any], platform: str) -> list[JobGraphEdge]:
    """Edges straight from the job's connections array (never from the
    per-component incoming/outgoing fields — those are empty on real scans)."""
    edges: list[JobGraphEdge] = []
    for conn in job_data.get("connections") or []:
        connector_name = str(conn.get("connector_name") or "")
        raw_style = conn.get("line_style")
        line_style = int(raw_style) if isinstance(raw_style, (int, float)) else 0
        is_control_flow = (
            line_style != 0 or bool(_TRIGGER_CONNECTOR_PATTERN.match(connector_name))
        )
        edges.append(JobGraphEdge(
            name=str(conn.get("name") or ""),
            source=str(conn.get("source") or ""),
            target=str(conn.get("target") or ""),
            connector_name=connector_name,
            line_style=line_style,
            is_control_flow=is_control_flow,
            explanation=explain_connector(connector_name, is_control_flow, platform),
        ))
    return edges


def _build_tmap_tables(job_data: dict[str, Any]) -> dict[str, TMapTableSummary]:
    """Associate tmap_data entries to tMap components positionally (Nth
    entry -> Nth tMap in component order), matching the engineering-facts
    extractor convention. Synthetic keys cover count mismatches on old scans."""
    tmap_names = [
        str(c.get("unique_name") or "")
        for c in job_data.get("components") or []
        if str(c.get("component_type") or "") == _TMAP_TYPE
    ]
    tables: dict[str, TMapTableSummary] = {}
    for idx, tmap in enumerate(job_data.get("tmap_data") or []):
        key = tmap_names[idx] if idx < len(tmap_names) else f"{_TMAP_TYPE}#{idx + 1}"
        tables[key] = TMapTableSummary(
            input_tables=[str(t) for t in tmap.get("input_tables") or []],
            output_tables=[str(t) for t in tmap.get("output_tables") or []],
        )
    return tables


def build_job_graph(
    project_id: str,
    job_name: str,
    group: str,
    job_data: dict[str, Any],
    descriptions: dict[str, str] | None = None,
    platform: str = "talend",
) -> JobGraphResponse:
    """Transform one job's scan JSON into the Level-2 graph response.

    ``descriptions`` is an optional ``{component_id: description}`` map parsed
    from the generated inventory doc (by the caller -- this module does no I/O).
    Components absent from the map keep an empty description.
    """
    return JobGraphResponse(
        project_id=project_id,
        job_name=job_name,
        group=group,
        nodes=_build_nodes(job_data, descriptions or {}, platform),
        edges=_build_edges(job_data, platform),
        tmap_tables=_build_tmap_tables(job_data),
    )
