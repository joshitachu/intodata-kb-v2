"""Talend XML/XMI parser — extracts structured data from Talend project files."""

from __future__ import annotations

import re
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from rich.console import Console

from intodoc.models import (
    Component,
    ComponentParam,
    Connection,
    ContextGroup,
    ContextVariable,
    FileType,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    RedactionDescriptor,
    Routine,
    ScanResult,
    ScanWarning,
    TdmMap,
    TdmMapField,
    TdmMapTree,
    TMapData,
    TMapEntry,
    TRunJobRef,
    TXMLMapData,
    TXMLMapField,
    TXMLMapTree,
    ArtifactReference,
)
from intodoc.platforms.base_parser import BaseParser
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.walker import walk_repo

console = Console()

# ── External System Detection ────────────────────────────────────────

# Utility / routing components that don't represent external systems
_INTERNAL_COMPONENTS: frozenset[str] = frozenset({
    "tMap", "tLogRow", "tJavaRow", "tJava", "tJavaFlex", "tPrejob", "tPostjob",
    "tRunJob", "tFixedFlowInput", "tFlowToIterate", "tSetGlobalVar", "tContextLoad",
    "tRouteInput", "tRouteOutput",
    "tUnite", "tReplicate", "tSortRow", "tFilterRow", "tAggregateRow",
    "tFileInputDelimited", "tFileOutputDelimited", "tFileInputExcel", "tFileOutputExcel",
    "tWriteJSONField", "tExtractJSONFields", "tExtractXMLField",
    "tSchemaComplianceCheck", "tSleep", "tLoop", "tFileList", "tFileDelete",
    "tFileCopy", "tFileExist",
    "tMsgBox", "tWarn", "tDie", "tBufferInput", "tBufferOutput",
    "tNormalize", "tDenormalize", "tIterateToFlow",
    "tConvertType", "tXMLMap", "tXSLT", "tLibraryLoad", "tLogCatcher",
    "tStatCatcher", "tRowGenerator",
    # ESB/Camel infrastructure — pure routing/transformation, no external system
    "cDirect", "cSplitter", "cAggregator", "cMessageRouter", "cRecipientList",
    "cProcessor", "cSetBody", "cSetHeader", "cSetProperty", "cRemoveHeader",
    "cConvertBodyTo", "cMarshal", "cUnmarshal",
    "cConfig", "cLog", "cTalendJob", "cBeanRegister", "cBeanValidate",
    "cRouteWhen", "cRouteOtherwise", "cWireTap",
})

# Component name prefix -> generic system name
_TALEND_SYSTEM_MAP: dict[str, str] = {
    "tSalesforce": "Salesforce",
    "tBigQuery": "BigQuery",
    "tMysql": "MySQL",
    "tMSSql": "MSSQL",
    "tPostgresql": "PostgreSQL",
    "tOracle": "Oracle",
    "tSnowflake": "Snowflake",
    "tRedshift": "Redshift",
    "tMongoDB": "MongoDB",
    "tCassandra": "Cassandra",
    "tHBase": "HBase",
    "tHive": "Hive",
    "tImpala": "Impala",
    "tNeo4j": "Neo4j",
    "tAmazonS3": "Amazon S3",
    "tS3": "Amazon S3",
    "tAzureStorage": "Azure Storage",
    "tGoogleStorage": "Google Cloud Storage",
    "tRESTClient": "REST API",
    "tREST": "REST API",
    "tHTTP": "HTTP API",
    "tSOAP": "SOAP API",
    "tWebService": "SOAP API",
    "tKafka": "Kafka",
    "tActiveMQ": "ActiveMQ",
    "tRabbitMQ": "RabbitMQ",
    "tFTP": "FTP",
    "tSFTP": "SFTP",
    "tSAP": "SAP",
    "tLDAP": "LDAP",
    "tFileInput": "File endpoint",
    "tFileOutput": "File endpoint",
    "tJDBC": "JDBC",
    "tElasticsearch": "Elasticsearch",
    "tDynamoDB": "DynamoDB",
    "tMarketo": "Marketo",
    "tNetSuite": "NetSuite",
    "tSendMail": "Email",
    # ESB/Camel components that represent external systems
    "cREST": "REST API",
    "cRESTClient": "REST API",
    "cSOAP": "SOAP API",
    "cHttp": "HTTP API",
    "cFile": "File endpoint",
    "cJMS": "JMS Broker",
    "cActiveMQ": "ActiveMQ",
    "cKafkaInput": "Kafka",
    "cKafkaOutput": "Kafka",
    "cMQConnectionFactory": "JMS Broker",
    "cMessagingEndpoint": "Messaging",
    "cRoute": "ESB Route",
}

# ── ESB / Camel Route Classification ─────────────────────────────────
#
# Talend Studio stores executable designs in distinct repository directories:
# process/ for Jobs and routes/ for Camel Routes. The c-prefixed palette
# classifier below is additive component evidence for compatibility and
# diagnostics; directory identity remains the canonical artifact taxonomy.
#
# Classification keys off the palette prefix convention itself (a lowercase "c"
# followed by an uppercase letter), not a hardcoded list of known Camel component
# names -- Talend's Camel palette is far larger than any list we could maintain,
# and a fixed allowlist silently misclassifies routes built from components it
# doesn't happen to mention.
_ESB_COMPONENT_PATTERN = re.compile(r"^c[A-Z]")


def classify_esb_components(component_types: Iterable[str]) -> list[str]:
    """Return the sorted, de-duplicated set of ESB/Camel component types present.

    An empty result means the job is a plain DI job; a non-empty result means the
    job is an ESB/Camel route.
    """
    return sorted({t for t in component_types if _ESB_COMPONENT_PATTERN.match(t)})


def _extract_talend_external_system(component_type: str) -> str | None:
    """Map a Talend component type to a generic external system name.

    Returns None for internal/utility and unknown components. A component
    name is implementation vocabulary, not external-system identity:
    stripping ``t``/``Input`` from an unrecognized type would turn transforms
    such as tHashInput or file writers such as tAdvancedFileOutputXML into
    fictitious systems ("Hash", "AdvancedFileOutputXML").
    """
    if component_type in _INTERNAL_COMPONENTS:
        return None

    # Longest-prefix match against _TALEND_SYSTEM_MAP
    best_match: str | None = None
    best_len = 0
    for prefix, system in _TALEND_SYSTEM_MAP.items():
        if component_type.startswith(prefix) and len(prefix) > best_len:
            best_match = system
            best_len = len(prefix)
    if best_match:
        return best_match

    # Unknown components remain implementation evidence in scan_result.json.
    # They are deliberately not promoted into L1 system identities.
    return None


# XMI namespaces used in Talend files
NS = {
    "xmi": "http://www.omg.org/XMI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "talendfile": "platform:/resource/org.talend.model/model/TalendFile.xsd",
    "TalendMapper": "http://www.talend.org/mapper",
    "TalendMetadata": "http://www.talend.org/metadata/connection/2010",
    "relational": "http://www.talend.org/cwm/resource/relational/2010",
    "orgomg": "http:///orgomg/cwm/resource/relational.ecore",
}


class TalendParser(BaseParser):
    """Parse a Talend project into structured models."""

    def __init__(self, cred_filter: CredentialFilter) -> None:
        super().__init__(cred_filter)
        # xmi:id → MetadataConnection lookup (built at scan start)
        self._xmi_id_index: dict[str, MetadataConnection] = {}
        # Property.id → Property.label lookup for resolving tRunJob XMI refs
        self._job_id_index: dict[str, str] = {}
        # Superseded job versions skipped by _latest_job_versions (per scan)
        self._superseded_versions = 0

    def parse(self, repo_root: Path) -> ScanResult:
        """Parse every Talend root in a source bundle as one logical project."""
        from intodoc.platforms.talend import TalendPlugin

        plugin = TalendPlugin()
        # Reset warning collection for this scan.
        self._warnings = []
        self._warn_root = repo_root
        self._superseded_versions = 0

        execution_roots = _find_talend_roots(repo_root)
        support_roots = [
            root for root in _find_talend_project_roots(repo_root)
            if root not in execution_roots and _has_supported_talend_artifacts(root)
        ]
        talend_roots = sorted(
            [*execution_roots, *support_roots],
            key=lambda root: root.relative_to(repo_root).as_posix(),
        )
        if not execution_roots:
            console.print(f"[yellow]Warning: No Talend project structure found under {repo_root}[/yellow]")
            return ScanResult(
                platform="talend",
                repo_root=str(repo_root),
                warnings=[
                    ScanWarning(
                        kind="missing_file",
                        file="",
                        message=f"No Talend project structure found under {repo_root}",
                        phase="root_discovery",
                    )
                ],
            )

        # Walk and classify files — exclude UNKNOWN to shrink scan output
        all_files = walk_repo(repo_root, plugin)
        files = [f for f in all_files if f.file_type != FileType.UNKNOWN]
        for scanned_file in files:
            matching_roots = [root for root in talend_roots if scanned_file.path.is_relative_to(root)]
            if matching_roots:
                source_root = max(matching_roots, key=lambda root: len(root.parts))
                scanned_file.platform_metadata["source_root"] = source_root.relative_to(repo_root).as_posix() or "."
        console.print(f"  Files walked: {len(all_files)}, indexed (non-UNKNOWN): {len(files)}")
        jobs: list[ParsedJob] = []
        connections: list[MetadataConnection] = []
        context_groups: list[ContextGroup] = []
        routines: list[Routine] = []
        tdm_maps: list[TdmMap] = []
        root_descriptors: list[dict[str, str]] = []
        superseded_versions = 0
        all_warnings: list[ScanWarning] = []
        execution_root_set = set(execution_roots)
        for talend_root in talend_roots:
            source_root = talend_root.relative_to(repo_root).as_posix() or "."
            role = "execution" if talend_root in execution_root_set else "support"
            project_metadata = self._read_talend_project_metadata(talend_root)
            console.print(f"  Talend {role} root: [cyan]{source_root}[/cyan]")
            # XMI identifiers are project-local. Reset all parser indexes between
            # roots so an identifier from one export cannot resolve another.
            self._xmi_id_index = {}
            self._job_id_index = {}
            self._superseded_versions = 0
            self._warnings = []
            self._build_metadata_index(talend_root)
            root_jobs: list[ParsedJob] = []
            if role == "execution":
                self._build_job_id_index(talend_root)
                root_jobs = self._parse_all_jobs(talend_root)
            for job in root_jobs:
                job.source_root = source_root
            jobs.extend(root_jobs)
            root_connections = list(self._xmi_id_index.values())
            for connection in root_connections:
                connection.additional_params["source_root"] = source_root
            connections.extend(root_connections)
            root_contexts = self._parse_all_contexts(
                talend_root,
                source_root=source_root,
                source_project=project_metadata.get("label", ""),
                source_role=role,
            )
            context_groups.extend(root_contexts)
            routines.extend(self._parse_routines(talend_root))
            tdm_maps.extend(self._parse_tdm_maps(talend_root, source_root, root_jobs))
            self._warn_unscanned_categories(talend_root, talend_roots)
            superseded_versions += self._superseded_versions
            all_warnings.extend(self._warnings)
            root_descriptors.append({
                "id": source_root,
                "path": source_root,
                "platform": "talend",
                "role": role,
                "project_id": project_metadata.get("project_id", ""),
                "label": project_metadata.get("label", ""),
                "technical_label": project_metadata.get("technical_label", ""),
                "description": project_metadata.get("description", ""),
            })

        composite = len(execution_roots) > 1
        connections = self._qualify_composite_connections(jobs, connections, composite)
        self._qualify_composite_jobs(jobs, composite)
        self._qualify_context_groups(context_groups)
        manifest = self._build_manifest(jobs, routines, root_descriptors, context_groups)
        job_call_edges = sum(len(callees) for callees in manifest.job_calls.values())
        route_invocation_edges = sum(
            relation.get("relation_type") == "route_invokes_job"
            and bool(relation.get("target"))
            for relation in manifest.artifact_relations
        )
        console.print(
            "  Manifest built: "
            f"{job_call_edges} Job→Job calls, "
            f"{route_invocation_edges} Route→Job invocations"
        )

        return ScanResult(
            platform="talend",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=connections,
            context_groups=context_groups,
            routines=routines,
            manifest=manifest,
            tdm_maps=tdm_maps,
            stats={
                "total_files_walked": len(all_files),
                "total_files_indexed": len(files),
                "total_jobs": len(jobs),
                # ``total_jobs`` is the historic total-design count.  New
                # clients must use the explicit taxonomy below so Routes are
                # never presented as DI Jobs.
                "total_artifacts": len(jobs),
                "job_count": sum(job.artifact_kind == "job" for job in jobs),
                "route_count": sum(job.artifact_kind == "route" for job in jobs),
                "joblet_count": sum(job.artifact_kind == "joblet" for job in jobs),
                "routelet_count": sum(job.artifact_kind == "routelet" for job in jobs),
                "empty_or_unparsed_count": sum(
                    job.scan_quality != "complete" for job in jobs
                ),
                "talend_roots": root_descriptors,
                "total_roots": len(talend_roots),
                "execution_roots": len(execution_roots),
                "support_roots": len(support_roots),
                "superseded_job_versions": superseded_versions,
                "total_connections": len(connections),
                "total_contexts": len(context_groups),
                "total_routines": len(routines),
                "total_warnings": len(all_warnings),
                "txmlmap_component_count": sum(len(job.txmlmap_data) for job in jobs),
                "tdm_map_count": len(tdm_maps),
                "tdm_map_unresolved_count": sum(
                    tdm.association == "unresolved" for tdm in tdm_maps
                ),
            },
            warnings=all_warnings,
        )

    # ── Metadata Index ───────────────────────────────────────────────

    def _build_metadata_index(self, talend_root: Path) -> None:
        """Build xmi:id → MetadataConnection index from metadata/connections/*.item files."""
        conn_dir = talend_root / "metadata" / "connections"
        if not conn_dir.exists():
            return

        for item_file in self._latest_item_files(conn_dir):
            try:
                text = self._read_filtered(item_file)
                root = ET.fromstring(text)
                self._parse_metadata_connection(root, item_file)
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {item_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=item_file, message=str(e), phase="metadata_index")

    def _parse_metadata_connection(self, root: ET.Element, file_path: Path) -> None:
        """Extract connection info from a metadata .item file."""
        # Look for DatabaseConnection element
        for elem in root:
            tag = _local_tag(elem.tag)
            if tag == "DatabaseConnection":
                xmi_id = elem.get(f"{{{NS['xmi']}}}id", "") or elem.get("id", "")
                conn = MetadataConnection(
                    xmi_id=xmi_id,
                    name=elem.get("name", "") or elem.get("label", ""),
                    connection_type=elem.get("DatabaseType", "Unknown"),
                    host=elem.get("ServerName", ""),
                    port=elem.get("Port", ""),
                    database=elem.get("SID", ""),
                    schema_name=elem.get("UiSchema", ""),
                    additional_params={
                        "driver_class": elem.get("DriverClass", ""),
                        "product_id": elem.get("ProductId", ""),
                        "context_mode": elem.get("ContextMode", "false"),
                        "username": elem.get("Username", ""),
                    },
                )
                if xmi_id:
                    self._xmi_id_index[xmi_id] = conn
                # Also index by the 'id' attribute (used in REPOSITORY refs)
                alt_id = elem.get("id", "")
                if alt_id and alt_id != xmi_id:
                    self._xmi_id_index[alt_id] = conn

    # ── Job ID Index ──────────────────────────────────────────────────

    def _build_job_id_index(self, talend_root: Path) -> None:
        """Build Property.id → label index for executable designs.

        Route invocations refer to Jobs, but indexing both repository folders
        also makes the stored evidence useful when a malformed source uses a
        Route identity in a reference; final relation resolution still limits
        cTalendJob targets to canonical ``job`` artifacts.
        """
        props_files = [
            props_file
            for directory in self._JOB_SOURCE_DIRS
            for props_file in sorted((talend_root / directory).rglob("*.properties"))
            if (talend_root / directory).exists()
        ]
        if not props_files:
            return

        for props_file in props_files:
            try:
                text = self._read_filtered(props_file)
                root = ET.fromstring(text)
                for elem in root:
                    tag = _local_tag(elem.tag)
                    if tag == "Property":
                        prop_id = elem.get("id", "")
                        prop_label = elem.get("label", "")
                        if prop_id and prop_label:
                            self._job_id_index[prop_id] = prop_label
                            self._job_id_index[prop_id.lstrip("_")] = prop_label
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {props_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=props_file, message=str(e), phase="job_id_index")

    # ── Context Parsing ──────────────────────────────────────────────

    def _latest_item_files(self, base_dir: Path) -> list[Path]:
        """Highest-version .item file per (directory, artifact name), recursive.

        Talend Studio exports keep every saved version of an item
        (Name_0.1.item ... Name_3.3.item) and may nest items in per-group
        subfolders (flat and nested layouts both occur in real exports).
        Superseded versions are counted in self._superseded_versions.
        """
        latest: dict[tuple[str, str], tuple[tuple[int, ...], Path]] = {}
        for f in sorted(base_dir.rglob("*.item")):
            m = re.match(r"(.+?)_(\d+(?:\.\d+)*)$", f.stem)
            if m:
                name, key = m.group(1), self._version_sort_key(m.group(2))
            else:
                name, key = f.stem, ()
            k = (str(f.parent), name)
            current = latest.get(k)
            if current is None:
                latest[k] = (key, f)
                continue
            self._superseded_versions += 1
            if key > current[0]:
                latest[k] = (key, f)
        return [f for _, f in sorted(latest.values(), key=lambda t: str(t[1]))]

    def _parse_all_contexts(
        self,
        talend_root: Path,
        *,
        source_root: str = "",
        source_project: str = "",
        source_role: str = "",
    ) -> list[ContextGroup]:
        """Parse context/**/*.item files (latest version per group)."""
        ctx_dir = talend_root / "context"
        if not ctx_dir.exists():
            return []

        groups: list[ContextGroup] = []
        for item_file in self._latest_item_files(ctx_dir):
            try:
                text = self._read_filtered(item_file)
                root = ET.fromstring(text)
                group = self._parse_context_file(root, item_file.stem)
                if group:
                    sidecar = self._read_context_sidecar(item_file)
                    group.context_id = sidecar.get("id", "")
                    group.context_key = group.name
                    group.source_root = source_root
                    group.source_project = source_project
                    group.source_role = source_role
                    group.purpose = sidecar.get("purpose", "")
                    group.description = sidecar.get("description", "")
                    groups.append(group)
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {item_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=item_file, message=str(e), phase="context")

        return groups

    def _parse_context_file(self, root: ET.Element, file_stem: str) -> ContextGroup | None:
        """Parse a context .item file into a ContextGroup."""
        # Strip the version suffix from the file stem
        # (e.g., "BRA_VBR_environment_0.1" → "BRA_VBR_environment")
        m = re.match(r"(.+?)_\d+(?:\.\d+)*$", file_stem)
        name = m.group(1) if m else file_stem

        variables: dict[str, ContextVariable] = {}

        for elem in root:
            tag = _local_tag(elem.tag)
            if tag == "ContextType":
                env_name = elem.get("name", "Default")
                for param in elem:
                    param_tag = _local_tag(param.tag)
                    if param_tag == "contextParameter":
                        var_name = param.get("name", "")
                        if not var_name:
                            continue
                        if var_name not in variables:
                            variables[var_name] = ContextVariable(
                                name=var_name,
                                type=param.get("type", "id_String"),
                            )
                        value = param.get("value", "")
                        descriptor = self.cred_filter.descriptor_from_redaction_token(value)
                        variables[var_name].values[env_name] = (
                            self.cred_filter.normalize_redaction_tokens(value)
                        )
                        if descriptor is not None:
                            variables[var_name].redaction_descriptors[env_name] = descriptor

        if not variables:
            return None

        return ContextGroup(name=name, variables=list(variables.values()))

    def _read_context_sidecar(self, item_file: Path) -> dict[str, str]:
        """Read stable identity and descriptive metadata for a context item."""
        props_file = item_file.with_suffix(".properties")
        if not props_file.exists():
            return {}
        try:
            root = ET.fromstring(self._read_filtered(props_file))
        except ET.ParseError as e:
            self._warn(kind="parse_error", file=props_file, message=str(e), phase="context")
            return {}

        for elem in root.iter():
            if _local_tag(elem.tag) == "Property":
                return {
                    "id": elem.get("id", ""),
                    "label": elem.get("label", ""),
                    "purpose": elem.get("purpose", ""),
                    "description": elem.get("description", ""),
                }
        return {}

    def _read_talend_project_metadata(self, talend_root: Path) -> dict[str, str]:
        """Read display metadata from talend.project without relying on its folder name."""
        descriptor = talend_root / "talend.project"
        if not descriptor.is_file():
            return {}
        try:
            root = ET.fromstring(self._read_filtered(descriptor))
        except ET.ParseError:
            return {}
        for elem in root.iter():
            if _local_tag(elem.tag) == "Project":
                return {
                    "project_id": (
                        elem.get(f"{{{NS['xmi']}}}id", "")
                        or elem.get("id", "")
                    ),
                    "label": elem.get("label", ""),
                    "technical_label": (
                        elem.get("technicalLabel", "")
                        or elem.get("technical_label", "")
                    ),
                    "description": elem.get("description", ""),
                }
        return {}

    # ── Job Parsing ──────────────────────────────────────────────────

    # Talend Studio stores DI Job Designs under process/ and ESB/Camel Route
    # Designs under a separate top-level routes/ directory -- both must be
    # walked or Route jobs are silently invisible to the scanner (never parsed
    # at all, not merely misclassified). Used by _build_job_id_index, which
    # only needs to resolve tRunJob/cTalendJob refs -- those target Jobs and
    # Routes, never Joblets/Routelets (which are invoked as pseudo-components
    # by componentName, not by an XMI Property ref).
    _JOB_SOURCE_DIRS = ("process", "routes")

    # Every executable/reusable Talend design directory, mapped to the
    # canonical artifact_kind it produces. Joblets and Routelets are reusable
    # fragments (invoked from a parent job/route as a pseudo-component whose
    # componentName equals the fragment's name) rather than standalone
    # top-level designs, but they are still independently versioned .item
    # files that must be walked and parsed like any other design -- skipping
    # them hides real logic (see module docstring / ground-truth verification
    # notes for the joblet gap).
    _ARTIFACT_DIR_KINDS: dict[str, str] = {
        "process": "job",
        "routes": "route",
        "joblets": "joblet",
        "routelets": "routelet",
    }

    # Talend repository directories this parser recognizes and either parses
    # directly (via _ARTIFACT_DIR_KINDS, context/, metadata/, code/routines/,
    # Maps/) or intentionally treats as inert asset storage that never hides
    # executable logic (Structures/, Namespace Containers/, Sample Data/,
    # lib/, sqlPatterns/, .settings). Anything else (e.g. resources/,
    # services/, tests/ seen in real ESB exports) is a category this scanner
    # does not parse yet; _warn_unscanned_categories surfaces it instead of
    # silently ignoring it.
    _KNOWN_TALEND_ROOT_DIRS: frozenset[str] = frozenset({
        "process", "routes", "joblets", "routelets",
        "context", "metadata", "code", "Maps",
        "Structures", "Namespace Containers", "Sample Data",
        "lib", "sqlPatterns", ".settings",
    })

    def _parse_all_jobs(self, talend_root: Path) -> list[ParsedJob]:
        """Parse all process/routes/joblets/routelets *.item design files
        (with their .properties sidecars) into ParsedJob records."""
        jobs: list[ParsedJob] = []
        for dir_name, artifact_kind in self._ARTIFACT_DIR_KINDS.items():
            base_dir = talend_root / dir_name
            if not base_dir.exists():
                continue
            for item_file in self._latest_item_files(base_dir):
                try:
                    job = self._parse_job(item_file, base_dir)
                    if job:
                        job.source_design = dir_name
                        # Repository location is the canonical identity. A
                        # Route with only a routelet/custom component is still
                        # a Route even when the ESB palette heuristic is empty.
                        job.artifact_kind = artifact_kind
                        if artifact_kind == "route":
                            job.artifact_role = (
                                "routelet_wrapper"
                                if any(
                                    any(
                                        marker in component.component_type.lower()
                                        for marker in ("routelet", "routlet")
                                    )
                                    for component in job.components
                                )
                                else "standalone"
                            )
                        elif artifact_kind in ("joblet", "routelet"):
                            # Joblets/Routelets exist to be invoked by other
                            # designs -- that is their defining trait, not an
                            # inferred heuristic.
                            job.artifact_role = "reusable"
                        else:
                            job.artifact_role = "standalone"
                        job.scan_quality = "empty" if not job.components else "complete"
                        jobs.append(job)
                except ET.ParseError as e:
                    console.print(f"  [yellow]XML parse error in {item_file.name}: {e}[/yellow]")
                    self._warn(kind="parse_error", file=item_file, message=str(e), phase="job")
                except Exception as e:
                    console.print(f"  [yellow]Error parsing {item_file.name}: {e}[/yellow]")
                    self._warn(kind="unknown", file=item_file, message=str(e), phase="job")

        return jobs

    def _warn_unscanned_categories(
        self, talend_root: Path, all_roots: Iterable[Path],
    ) -> None:
        """Flag top-level Talend repository directories this scanner never
        parses, so a future undiscovered artifact category (e.g. resources/,
        services/, tests/ seen in real ESB exports) surfaces as an explicit
        gap instead of vanishing without a trace.

        ``all_roots`` (the full composite-scan root list) lets this skip a
        child directory that is itself -- or contains -- another recognized
        Talend root nested inside this one; that content is already scanned
        under its own root and is not a fourth silently-skipped category.
        """
        nested_roots = [
            root for root in all_roots
            if root != talend_root and root.is_relative_to(talend_root)
        ]
        for child in sorted(talend_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in self._KNOWN_TALEND_ROOT_DIRS:
                continue
            if any(
                root.is_relative_to(child) or child.is_relative_to(root)
                for root in nested_roots
            ):
                continue
            file_count = sum(1 for _ in child.rglob("*") if _.is_file())
            if file_count == 0:
                continue
            self._warn(
                kind="unscanned_category",
                file=child,
                message=(
                    f"Talend repository directory '{child.name}' ({file_count} files) "
                    "is not parsed by this scanner; its contents are invisible to "
                    "downstream analysis."
                ),
                phase="root_discovery",
            )

    @staticmethod
    def _version_sort_key(version: str) -> tuple[int, ...]:
        """Numeric sort key for Talend item versions ('3.3' > '0.10' > '0.9')."""
        return tuple(int(part) for part in re.findall(r"\d+", version))

    def _parse_job(self, item_file: Path, base_dir: Path) -> ParsedJob | None:
        """Parse a single job .item file + its .properties sidecar.

        `base_dir` is the process/, routes/, joblets/, or routelets/
        directory the file was found under.
        """
        text = self._read_filtered(item_file)
        root = ET.fromstring(text)
        body = _find_process_body(root)

        # Read .properties sidecar for name/version/dates
        props = self._read_properties_sidecar(item_file)

        # Derive group/subgroup from path (first two folder levels under base_dir)
        rel = item_file.relative_to(base_dir).as_posix()
        parts = rel.split("/")
        group = parts[0] if len(parts) > 1 else ""
        subgroup = parts[1] if len(parts) > 2 else ""

        job = ParsedJob(
            name=props.get("label", item_file.stem.rsplit("_0.", 1)[0]),
            path=rel,
            author=props.get("author", ""),
            version=props.get("version", ""),
            creation_date=props.get("created_date", ""),
            modification_date=props.get("modified_date", ""),
            group=group,
            subgroup=subgroup,
        )

        # Parse inline context definitions
        for elem in body:
            tag = _local_tag(elem.tag)
            if tag == "context":
                self._parse_job_context(elem, job)

        # Parse parameters block, nodes, and connections
        for elem in body:
            tag = _local_tag(elem.tag)
            if tag == "parameters":
                self._parse_job_params(elem, job)
            elif tag == "node":
                self._parse_node(elem, job, item_file)
            elif tag == "connection":
                self._parse_connection(elem, job)

        # Classify DI job vs ESB/Camel route from the parsed component types.
        job.esb_component_types = classify_esb_components(
            c.component_type for c in job.components
        )
        job.is_esb_route = bool(job.esb_component_types)

        return job

    def _read_properties_sidecar(self, item_file: Path) -> dict[str, str]:
        """Read the .properties XML sidecar for a job."""
        props_file = item_file.with_suffix(".properties")
        if not props_file.exists():
            return {}

        try:
            text = self._read_filtered(props_file)
            root = ET.fromstring(text)
        except ET.ParseError:
            return {}

        result: dict[str, str] = {}
        for elem in root:
            tag = _local_tag(elem.tag)
            if tag == "Property":
                result["label"] = elem.get("label", "")
                result["version"] = elem.get("version", "")
                # Parse additionalProperties for dates
                for child in elem:
                    child_tag = _local_tag(child.tag)
                    if child_tag == "additionalProperties":
                        key = child.get("key", "")
                        val = child.get("value", "")
                        if key in ("created_date", "modified_date"):
                            result[key] = val

        return result

    def _parse_job_context(self, context_elem: ET.Element, job: ParsedJob) -> None:
        """Parse an inline context block within a job."""
        env_name = context_elem.get("name", "Default")
        variables: list[ContextVariable] = []

        for param in context_elem:
            param_tag = _local_tag(param.tag)
            if param_tag == "contextParameter":
                var_name = param.get("name", "")
                if var_name:
                    value = param.get("value", "")
                    descriptor = self.cred_filter.descriptor_from_redaction_token(value)
                    variables.append(ContextVariable(
                        name=var_name,
                        type=param.get("type", "id_String"),
                        values={
                            env_name: self.cred_filter.normalize_redaction_tokens(value),
                        },
                        repository_context_id=param.get("repositoryContextId", ""),
                        redaction_descriptors=(
                            {env_name: descriptor} if descriptor is not None else {}
                        ),
                    ))

        if variables:
            # Merge into existing context group or create new
            existing = next((cg for cg in job.context_groups if cg.name == "inline"), None)
            if existing is None:
                existing = ContextGroup(name="inline", variables=[])
                job.context_groups.append(existing)

            for var in variables:
                found = next((v for v in existing.variables if v.name == var.name), None)
                if found:
                    found.values.update(var.values)
                    found.redaction_descriptors.update(var.redaction_descriptors)
                    if not found.repository_context_id:
                        found.repository_context_id = var.repository_context_id
                else:
                    existing.variables.append(var)

    def _parse_job_params(self, params_elem: ET.Element, job: ParsedJob) -> None:
        """Parse the <parameters> block of a job for routine references."""
        for child in params_elem:
            tag = _local_tag(child.tag)
            if tag == "routinesParameter":
                routine_name = child.get("name", "")
                if routine_name:
                    job.raw_routines.append(routine_name)

    def _parse_node(
        self, node_elem: ET.Element, job: ParsedJob, item_file: Path | None = None,
    ) -> None:
        """Parse a <node> element into a Component."""
        comp_type = node_elem.get("componentName", "")
        unique_name = ""
        params: list[ComponentParam] = []
        txmlmaps: list[TXMLMapData] = []

        for child in node_elem:
            tag = _local_tag(child.tag)
            if tag == "elementParameter":
                name = child.get("name", "")
                value = child.get("value", "")
                field = child.get("field", "")

                if name == "UNIQUE_NAME":
                    unique_name = value
                else:
                    # Skip UI noise
                    if name in ("COMP_DEFAULT_FILE_DIR",) or name.startswith("offsetLabel"):
                        continue
                    # Skip empty values
                    if not value:
                        continue
                    # Layer 1 may have replaced a whole URI with an opaque
                    # descriptor marker.  Recover only its safe structural
                    # descriptor, then immediately normalize the marker to
                    # the ordinary public placeholder before this parsed
                    # model can be persisted or exposed downstream.
                    descriptor = self.cred_filter.descriptor_from_redaction_token(value)
                    value = self.cred_filter.normalize_redaction_tokens(value)
                    params.append(ComponentParam(
                        name=name,
                        value=value,
                        field_type=field,
                        redaction_descriptor=descriptor,
                    ))

            elif tag == "nodeData":
                # tMap MapperData / tXMLMap XmlMapData — try multiple namespace patterns
                xsi_type = (
                    child.get(f"{{{NS['xsi']}}}type", "")
                    or child.get(f"{{{NS['xmi']}}}type", "")
                    or child.get("type", "")
                )
                if "MapperData" in xsi_type and "XmlMapData" not in xsi_type:
                    tmap = self._parse_mapper_data(child)
                    job.tmap_data.append(tmap)
                elif "XmlMapData" in xsi_type:
                    txmap = self._parse_xml_map_data(child)
                    txmlmaps.append(txmap)
                    if item_file is not None:
                        self._warn(
                            kind="partial_parse",
                            file=item_file,
                            message=(
                                f"tXMLMap '{unique_name or comp_type}' in job "
                                f"'{job.name}': input/output field trees and "
                                "output expressions captured, but the "
                                "positional connections list is stored raw "
                                "and not resolved into typed source/target "
                                "pairs."
                            ),
                            phase="txmlmap",
                        )

        # UNIQUE_NAME is a direct property of the enclosing node, so it is
        # reliable component ownership evidence even though XmlMapData's
        # internal EMF proxy wiring remains intentionally unresolved.
        for txmap in txmlmaps:
            txmap.component_name = unique_name
        job.txmlmap_data.extend(txmlmaps)

        # Resolve metadata connection reference (REPOSITORY_PROPERTY_TYPE)
        conn_ref = ""
        for p in params:
            if p.name in ("REPOSITORY_PROPERTY_TYPE", "PROPERTY:REPOSITORY_PROPERTY_TYPE"):
                # Value is an xmi:id — resolve to connection name
                resolved = self._xmi_id_index.get(p.value)
                if resolved:
                    conn_ref = resolved.name
                break

        comp = Component(
            unique_name=unique_name,
            component_type=comp_type,
            params=params,
            metadata_connection_ref=conn_ref,
        )
        # Surface custom code (tJava/tJavaRow/tJavaFlex) as a top-level
        # field so downstream consumers don't have to know param layout.
        comp.code = comp.derive_code_from_params()
        job.components.append(comp)

        # Extract tRunJob references
        if comp_type == "tRunJob":
            self._extract_trunjob_ref(params, job)
        elif comp_type == "cTalendJob":
            self._extract_ctalendjob_ref(params, job, unique_name)
        elif any(
            param.name == "FAMILY" and param.value.strip().lower() == "joblets"
            for param in params
        ):
            # Talend serializes a Joblet invocation as a node whose
            # componentName is the reusable design name and whose FAMILY is
            # explicitly Joblets. This is direct repository evidence, unlike
            # guessing from a custom component name.
            job.artifact_refs.append(ArtifactReference(
                target_name=comp_type,
                relation_type="job_uses_joblet",
                component_name=unique_name,
            ))

    def _parse_mapper_data(self, node_data: ET.Element) -> TMapData:
        """Parse a TalendMapper:MapperData nodeData element."""
        tmap = TMapData()

        for child in node_data:
            tag = _local_tag(child.tag)

            if tag == "inputTables":
                table_name = child.get("name", "")
                tmap.input_tables.append(table_name)
                # Input table entries don't have expressions (they're source fields)

            elif tag == "outputTables":
                table_name = child.get("name", "")
                tmap.output_tables.append(table_name)
                for entry in child:
                    entry_tag = _local_tag(entry.tag)
                    if entry_tag == "mapperTableEntries":
                        tmap.entries.append(TMapEntry(
                            table_name=table_name,
                            column_name=entry.get("name", ""),
                            expression=_unescape_java(entry.get("expression", "")),
                        ))

            elif tag == "varTables":
                table_name = child.get("name", "Var")
                for entry in child:
                    entry_tag = _local_tag(entry.tag)
                    if entry_tag == "mapperTableEntries":
                        tmap.var_entries.append(TMapEntry(
                            table_name=table_name,
                            column_name=entry.get("name", ""),
                            expression=_unescape_java(entry.get("expression", "")),
                        ))

        return tmap

    def _parse_xml_map_data(self, node_data: ET.Element) -> TXMLMapData:
        """Parse a TalendXMLMap:XmlMapData nodeData element (tXMLMap component).

        Distinct wire format from classic tMap MapperData: fields form a
        nested XML tree (xpath-addressed, with a ``children`` element for
        nested output nodes) instead of flat table columns. Output tree
        nodes/children carry their mapping formula directly on an
        ``expression`` attribute -- already referencing the source field by
        name (e.g. ``row7.GUIDElement``) -- so that content is captured in
        full. The sibling ``connections`` elements use positional EMF proxy
        refs (``//@node.N/@nodeData/@inputTrees.0/@nodes.1``) purely to
        redraw the Studio wiring diagram; those are preserved verbatim in
        ``raw_connections`` rather than resolved into typed pairs (see the
        ``ScanWarning`` the caller emits alongside this).
        """
        txmap = TXMLMapData()

        def collect_fields(elem: ET.Element) -> list[TXMLMapField]:
            fields: list[TXMLMapField] = []
            for child in elem:
                tag = _local_tag(child.tag)
                if tag not in ("nodes", "children"):
                    continue
                fields.append(TXMLMapField(
                    name=child.get("name", ""),
                    xpath=child.get("xpath", ""),
                    field_type=child.get("type", ""),
                    expression=_unescape_java(child.get("expression", "")),
                    loop=child.get("loop", "false") == "true",
                ))
                # Output trees nest via <children>; flatten depth-first so
                # every level (including intermediate structural nodes) is
                # preserved with its own xpath.
                fields.extend(collect_fields(child))
            return fields

        for child in node_data:
            tag = _local_tag(child.tag)

            if tag == "inputTrees":
                txmap.input_trees.append(TXMLMapTree(
                    name=child.get("name", ""),
                    fields=collect_fields(child),
                ))

            elif tag == "outputTrees":
                txmap.output_trees.append(TXMLMapTree(
                    name=child.get("name", ""),
                    fields=collect_fields(child),
                ))

            elif tag == "varTables":
                table_name = child.get("name", "Var")
                for entry in child:
                    entry_tag = _local_tag(entry.tag)
                    if entry_tag == "mapperTableEntries":
                        txmap.var_entries.append(TMapEntry(
                            table_name=table_name,
                            column_name=entry.get("name", ""),
                            expression=_unescape_java(entry.get("expression", "")),
                        ))

            elif tag == "connections":
                source = child.get("source", "")
                target = child.get("target", "")
                if source or target:
                    txmap.raw_connections.append(f"{source} -> {target}")

        return txmap

    def _extract_trunjob_ref(self, params: list[ComponentParam], job: ParsedJob) -> None:
        """Extract tRunJob child job reference from component parameters."""
        readable_name = ""
        xmi_ref = ""
        context_params: list[ComponentParam] = []

        for p in params:
            if p.name == "PROCESS":
                readable_name = p.value
            elif p.name == "PROCESS:PROCESS_TYPE_PROCESS":
                xmi_ref = p.value

        # Prefer readable name; resolve XMI ref as fallback
        child_job = readable_name
        if not child_job and xmi_ref:
            # Format is typically "PROJECT_NAME:_xmiId" — extract the ID part
            parts = xmi_ref.split(":")
            xmi_id = parts[-1].lstrip("_") if parts else xmi_ref
            child_job = self._job_id_index.get(xmi_id, xmi_ref)

        if child_job:
            job.trunjob_refs.append(TRunJobRef(
                child_job=child_job,
                child_job_xmi_id=xmi_ref,
                context_params=context_params,
            ))

    def _extract_ctalendjob_ref(
        self, params: list[ComponentParam], job: ParsedJob, component_name: str,
    ) -> None:
        """Extract a Route -> Job invocation without trusting a display label.

        Talend Studio versions serialize the selected Job under different
        parameter names.  We retain both the human-readable target and the
        repository identity, resolving only when there is one safe match.
        """
        target_name = ""
        xmi_ref = ""
        for param in params:
            if param.name in {"SELECTED_JOB_NAME", "JOB_NAME", "PROCESS"}:
                target_name = param.value
            elif param.name in {
                "SELECTED_JOB_NAME:PROCESS_TYPE_PROCESS",
                "PROCESS:PROCESS_TYPE_PROCESS",
                "JOB_NAME:PROCESS_TYPE_PROCESS",
            }:
                xmi_ref = param.value
        if not target_name and xmi_ref:
            candidate_id = xmi_ref.split(":")[-1].lstrip("_")
            target_name = self._job_id_index.get(candidate_id, "")
        if target_name or xmi_ref:
            job.artifact_refs.append(ArtifactReference(
                target_name=target_name or xmi_ref,
                target_xmi_id=xmi_ref,
                relation_type="route_invokes_job",
                component_name=component_name,
            ))

    def _parse_connection(self, conn_elem: ET.Element, job: ParsedJob) -> None:
        """Parse a <connection> element."""
        name = ""
        for child in conn_elem:
            tag = _local_tag(child.tag)
            if tag == "elementParameter" and child.get("name") == "UNIQUE_NAME":
                name = child.get("value", "")
                break

        conn = Connection(
            name=name or conn_elem.get("label", ""),
            source=conn_elem.get("source", ""),
            target=conn_elem.get("target", ""),
            connector_name=conn_elem.get("connectorName", ""),
            line_style=int(conn_elem.get("lineStyle", "0")),
        )
        job.connections.append(conn)

    # ── Routines ─────────────────────────────────────────────────────

    # System routines shipped with Talend — no useful source to extract
    _SYSTEM_ROUTINES = frozenset({
        "DataMasking", "DataOperation", "Mathematical", "Numeric",
        "Relational", "StringHandling", "TalendDataGenerator", "TalendDate",
        "TalendString", "MDM",
    })

    _MAX_ROUTINE_LINES = 200

    def _parse_routines(self, talend_root: Path) -> list[Routine]:
        """Collect routines from code/routines/ with source code."""
        routines_dir = talend_root / "code" / "routines"
        if not routines_dir.exists():
            return []

        routines: list[Routine] = []
        for item_file in sorted(routines_dir.rglob("*.item")):
            name = item_file.stem.rsplit("_0.", 1)[0]
            is_system = name in self._SYSTEM_ROUTINES

            source_code = ""
            if not is_system:
                try:
                    raw = item_file.read_text(encoding="utf-8", errors="replace")
                    # Apply credential filtering
                    raw = self.cred_filter.filter_text(raw)
                    # Cap at max lines
                    lines = raw.splitlines()
                    if len(lines) > self._MAX_ROUTINE_LINES:
                        lines = lines[: self._MAX_ROUTINE_LINES]
                        lines.append(f"// ... truncated at {self._MAX_ROUTINE_LINES} lines")
                    source_code = "\n".join(lines)
                except Exception:
                    pass

            routines.append(Routine(
                name=name,
                source_code=source_code,
                is_system=is_system,
            ))

        return routines

    # ── TDM Maps (Talend Data Mapper) ─────────────────────────────────
    #
    # A third, separate Talend mapping format (distinct from tMap and
    # tXMLMap above): standalone `Maps/*.xml` artifacts used by ESB/API
    # Services projects to define request/response transformations outside
    # any single job's inline components. See models.TdmMap docstring.

    _MAX_TDM_FIELDS = 500

    def _parse_tdm_maps(
        self, talend_root: Path, source_root: str, root_jobs: list[ParsedJob],
    ) -> list[TdmMap]:
        """Discover and structurally parse Maps/*.xml TDM map definitions.

        Ownership is resolved only from the Maps/Jobs/<name>/ and
        Maps/Routes/<name>/ directory convention Talend Studio uses when a
        map is scoped to one design; maps directly under Maps/ (no such
        subfolder) are reported with ``association="unresolved"`` rather
        than guessed from name similarity.
        """
        maps_dir = talend_root / "Maps"
        if not maps_dir.exists():
            return []

        jobs_by_name = {job.name: job for job in root_jobs if job.artifact_kind == "job"}
        routes_by_name = {job.name: job for job in root_jobs if job.artifact_kind == "route"}

        results: list[TdmMap] = []
        for xml_file in sorted(maps_dir.rglob("*.xml")):
            rel_to_root = xml_file.relative_to(talend_root).as_posix()
            parts = xml_file.relative_to(maps_dir).parts

            association = "unresolved"
            owning_artifact = ""
            if len(parts) >= 2 and parts[0] == "Jobs":
                match = jobs_by_name.get(parts[1])
                if match:
                    owning_artifact = match.job_id or match.name
                    association = "directory_jobs"
            elif len(parts) >= 2 and parts[0] == "Routes":
                match = routes_by_name.get(parts[1])
                if match:
                    owning_artifact = match.job_id or match.name
                    association = "directory_routes"

            try:
                text = self._read_filtered(xml_file)
                xml_root = ET.fromstring(text)
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {xml_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=xml_file, message=str(e), phase="tdm_map")
                continue

            tdm = TdmMap(
                name=xml_root.get("name", xml_file.stem),
                path=rel_to_root,
                source_root=source_root,
                input_struct=xml_root.get("inStruct", ""),
                output_struct=xml_root.get("outStruct", ""),
                owning_artifact=owning_artifact,
                association=association,
            )

            input_elem = _find_first_descendant(xml_root, "inputMapElem")
            output_elem = _find_first_descendant(xml_root, "outputMapElem")
            if input_elem is not None:
                tdm.input_tree = self._collect_tdm_tree(input_elem)
                if tdm.input_tree.truncated:
                    self._warn(
                        kind="partial_parse",
                        file=xml_file,
                        message=(
                            f"TDM map '{tdm.name}' input tree truncated at "
                            f"{self._MAX_TDM_FIELDS} fields."
                        ),
                        phase="tdm_map",
                    )
            if output_elem is not None:
                tdm.output_tree = self._collect_tdm_tree(output_elem)
                if tdm.output_tree.truncated:
                    self._warn(
                        kind="partial_parse",
                        file=xml_file,
                        message=(
                            f"TDM map '{tdm.name}' output tree truncated at "
                            f"{self._MAX_TDM_FIELDS} fields."
                        ),
                        phase="tdm_map",
                    )

            if association == "unresolved":
                self._warn(
                    kind="partial_parse",
                    file=xml_file,
                    message=(
                        f"TDM map '{tdm.name}' has no Maps/Jobs/<name>/ or "
                        "Maps/Routes/<name>/ directory scoping; ownership "
                        "could not be resolved to a specific job or route."
                    ),
                    phase="tdm_map",
                )

            results.append(tdm)

        return results

    def _collect_tdm_tree(self, root_elem: ET.Element) -> TdmMapTree:
        """Flatten a TDM inputMapElem/outputMapElem tree into a bounded,
        path-qualified field list.

        Each Talend ``child`` node's own ``<value>`` child element (present
        only on mapped output leaves) is the mapping expression -- already a
        human-readable path reference (e.g. ``in$/KTA/WID``), unlike
        tXMLMap's positional connection refs, so no index resolution is
        needed to read it.
        """
        fields: list[TdmMapField] = []
        truncated = False

        def walk(elem: ET.Element, path_parts: list[str]) -> None:
            nonlocal truncated
            for child in elem:
                if _local_tag(child.tag) != "child":
                    continue
                if len(fields) >= self._MAX_TDM_FIELDS:
                    truncated = True
                    return
                name = child.get("name", "")
                expression = ""
                for grandchild in child:
                    if _local_tag(grandchild.tag) == "value":
                        expression = (grandchild.text or "").strip()
                        break
                fields.append(TdmMapField(
                    name=name,
                    path="/".join([*path_parts, name]) if name else "/".join(path_parts),
                    expression=expression,
                ))
                walk(child, [*path_parts, name])

        walk(root_elem, [])
        return TdmMapTree(
            name=root_elem.get("name", ""),
            fields=fields,
            truncated=truncated,
        )

    # ── Manifest ─────────────────────────────────────────────────────

    @staticmethod
    def _qualify_composite_connections(
        jobs: list[ParsedJob], connections: list[MetadataConnection], composite: bool,
    ) -> list[MetadataConnection]:
        """Deduplicate proven shared connections; namespace every uncertain one.

        This executes after Layer 1 redaction but before the post-parse layer.
        A redaction placeholder or ``context.*`` expression is evidence that a
        connection exists, never evidence that two source projects point at
        the same physical endpoint.  Requiring concrete host *and* database
        values prevents redaction from manufacturing a false shared system.
        """
        if not composite:
            return connections
        by_name: dict[str, list[MetadataConnection]] = {}
        for connection in connections:
            by_name.setdefault(connection.name, []).append(connection)
        remapped: dict[tuple[str, str], str] = {}
        retained: list[MetadataConnection] = []
        for name, candidates in by_name.items():
            fingerprint = {
                (c.connection_type, c.host, c.port, c.database, c.schema_name, c.file_path)
                for c in candidates
            }
            has_safe_physical_identity = all(
                _has_safe_connection_discriminator(c.host)
                and _has_safe_connection_discriminator(c.database)
                for c in candidates
            )
            if len(fingerprint) == 1 and has_safe_physical_identity:
                # Same materialized connection used by several exports: retain
                # one shared topology node, with provenance on the connection.
                retained.append(candidates[0])
                continue
            for connection in candidates:
                root = str(connection.additional_params.get("source_root") or "")
                qualified = f"{root}::{name}"
                remapped[(root, name)] = qualified
                connection.name = qualified
                retained.append(connection)
        for job in jobs:
            for component in job.components:
                key = (job.source_root, component.metadata_connection_ref)
                if key in remapped:
                    component.metadata_connection_ref = remapped[key]
        return retained

    @staticmethod
    def _qualify_composite_jobs(jobs: list[ParsedJob], composite: bool) -> None:
        """Assign stable IDs and resolve tRunJob refs after all roots exist."""
        by_name: dict[str, list[ParsedJob]] = {}
        by_root_name: dict[tuple[str, str], list[ParsedJob]] = {}
        for job in jobs:
            by_name.setdefault(job.name, []).append(job)
            by_root_name.setdefault((job.source_root, job.name), []).append(job)
        for job in jobs:
            duplicate = len(by_name[job.name]) > 1
            same_root = by_root_name[(job.source_root, job.name)]
            if duplicate:
                # Cross-root duplicates remain readable; duplicates inside one
                # root require their path/group to remain collision-free.
                job.job_id = _qualified_job_id(
                    job.source_root,
                    job.name if composite and len(same_root) == 1
                    else f"{job.source_design or 'process'}::{job.path}",
                )
            else:
                job.job_id = job.name
        for job in jobs:
            for ref in job.trunjob_refs:
                same_root = by_root_name.get((job.source_root, ref.child_job), [])
                global_matches = by_name.get(ref.child_job, [])
                if len(same_root) == 1:
                    ref.resolved_job_id = same_root[0].job_id
                    ref.resolution = "same_root"
                elif len(same_root) > 1:
                    ref.resolution = "ambiguous"
                elif len(global_matches) == 1:
                    ref.resolved_job_id = global_matches[0].job_id
                    ref.resolution = "global"
                elif len(global_matches) > 1:
                    ref.resolution = "ambiguous"
            for ref in job.artifact_refs:
                # Each reference type has an explicit, Talend-native target
                # taxonomy. Do not choose a same-named Route/Joblet or a
                # duplicate target; ambiguity remains useful evidence but is
                # never rendered as a false topology edge.
                target_kind = (
                    "joblet" if ref.relation_type == "job_uses_joblet" else "job"
                )
                candidates = [
                    item for item in by_root_name.get((job.source_root, ref.target_name), [])
                    if item.artifact_kind == target_kind
                ]
                global_candidates = [
                    item for item in by_name.get(ref.target_name, [])
                    if item.artifact_kind == target_kind
                ]
                if len(candidates) == 1:
                    ref.resolved_artifact_id = candidates[0].job_id
                    ref.resolution = "same_root"
                elif len(candidates) > 1:
                    ref.resolution = "ambiguous"
                elif len(global_candidates) == 1:
                    ref.resolved_artifact_id = global_candidates[0].job_id
                    ref.resolution = "global"
                elif len(global_candidates) > 1:
                    ref.resolution = "ambiguous"

    @staticmethod
    def _qualify_context_groups(context_groups: list[ContextGroup]) -> None:
        """Assign collision-free context keys without changing display names."""
        by_name: dict[str, list[ContextGroup]] = {}
        for group in context_groups:
            by_name.setdefault(group.name, []).append(group)
        for name, groups in by_name.items():
            if len(groups) == 1:
                groups[0].context_key = name
                continue
            for index, group in enumerate(groups):
                raw = (
                    f"{group.source_root}::{name}::"
                    f"{group.context_id or index}"
                )
                slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_") or "context"
                digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
                group.context_key = f"{slug[:56].rstrip('_') or 'context'}-{digest}"

    @staticmethod
    def _build_manifest(
        jobs: list[ParsedJob],
        routines: list[Routine],
        source_roots: list[dict[str, str]],
        context_groups: list[ContextGroup] | None = None,
    ) -> ProjectManifest:
        """Build cross-reference manifest from parsed jobs."""
        m = ProjectManifest()

        m.source_roots = source_roots
        composite_execution_bundle = sum(
            root.get("role", "execution") == "execution"
            for root in source_roots
        ) > 1
        contexts = context_groups or []
        contexts_by_id: dict[str, list[ContextGroup]] = {}
        for context in contexts:
            context_key = context.context_key or context.name
            if context.context_id:
                contexts_by_id.setdefault(context.context_id, []).append(context)
            m.context_metadata[context_key] = {
                "name": context.name,
                "context_id": context.context_id,
                "source_root": context.source_root,
                "source_project": context.source_project,
                "source_role": context.source_role,
                "purpose": context.purpose,
                "description": context.description,
                "environments": sorted({
                    environment
                    for variable in context.variables
                    for environment in variable.values
                }),
                "variable_count": len(context.variables),
                "redaction_descriptors": {
                    variable.name: {
                        environment: descriptor.model_dump(exclude_none=True)
                        for environment, descriptor in sorted(
                            variable.redaction_descriptors.items(),
                        )
                    }
                    for variable in context.variables
                    if variable.redaction_descriptors
                },
            }

        for job in jobs:
            job_key = job.job_id or job.name
            m.artifact_kinds[job_key] = job.artifact_kind
            m.job_source_roots[job_key] = job.source_root
            # job_groups
            group = job.group or "Global"
            # Keep functional folders as the primary grouping, but qualify
            # them in a multi-project bundle so identically named folders such
            # as "Jobs" from different Talend projects never collapse into one
            # misleading topology group.
            m.job_groups[job_key] = (
                f"{job.source_root}/{group}"
                if composite_execution_bundle and job.source_root
                else group
            )

            # job_calls / job_called_by (from tRunJob refs)
            if job.trunjob_refs:
                children = [r.resolved_job_id for r in job.trunjob_refs if r.resolved_job_id]
                unresolved = [
                    {"child_job": r.child_job, "resolution": r.resolution}
                    for r in job.trunjob_refs if not r.resolved_job_id
                ]
                if unresolved:
                    m.unresolved_job_calls[job_key] = unresolved
                if children:
                    m.job_calls[job_key] = children
                    for child in children:
                        m.job_called_by.setdefault(child, []).append(job_key)
                        m.artifact_relations.append({
                            "source": job_key,
                            "target": child,
                            "relation_type": "job_calls_job",
                            "resolution": "resolved",
                            "provenance": "tRunJob",
                        })

            for ref in job.artifact_refs:
                provenance = (
                    "joblet_family" if ref.relation_type == "job_uses_joblet"
                    else "cTalendJob"
                )
                if ref.resolved_artifact_id:
                    m.artifact_relations.append({
                        "source": job_key,
                        "target": ref.resolved_artifact_id,
                        "relation_type": ref.relation_type,
                        "resolution": ref.resolution,
                        "provenance": provenance,
                        "component_name": ref.component_name,
                    })
                else:
                    m.artifact_relations.append({
                        "source": job_key,
                        "target": "",
                        "target_name": ref.target_name,
                        "relation_type": ref.relation_type,
                        "resolution": ref.resolution,
                        "provenance": provenance,
                    })

            # job_connections_used / connection_used_by (from component refs)
            conn_names: set[str] = set()
            referenced_connection_components = _resolve_existing_connection_refs(job)
            for comp in job.components:
                if comp.metadata_connection_ref:
                    direction = _component_resource_direction(comp.component_type)
                    if (
                        direction == "unknown"
                        and comp.unique_name in referenced_connection_components
                    ):
                        # The connection-holder component is implementation
                        # plumbing. Its input/output consumers below provide
                        # the actual data direction and more useful provenance.
                        continue
                    conn_names.add(comp.metadata_connection_ref)
                    m.artifact_resources.append({
                        "artifact": job_key,
                        "resource_type": "repository_connection",
                        "resource": comp.metadata_connection_ref,
                        "direction": direction,
                        "component_name": comp.unique_name,
                        "provenance": comp.component_type,
                    })
            if conn_names:
                m.job_connections_used[job_key] = sorted(conn_names)
                for cn in conn_names:
                    m.connection_used_by.setdefault(cn, []).append(job_key)

            # Resolve repository contexts by stable Talend Property ID. Never
            # choose the first matching folder/project when an ID is ambiguous.
            linked_contexts: list[str] = []
            seen_context_ids: set[str] = set()
            for inline_group in job.context_groups:
                for variable in inline_group.variables:
                    context_id = variable.repository_context_id
                    if (
                        not context_id
                        or context_id.lower() == "built-in"
                        or context_id in seen_context_ids
                    ):
                        continue
                    seen_context_ids.add(context_id)
                    matches = contexts_by_id.get(context_id, [])
                    if len(matches) == 1:
                        context = matches[0]
                        context_key = context.context_key or context.name
                        linked_contexts.append(context_key)
                        m.context_used_by.setdefault(context_key, []).append(job_key)
                        m.job_context_links.setdefault(job_key, []).append({
                            "context_key": context_key,
                            "context_id": context_id,
                            "resolution": "repository_id",
                        })
                        if context.source_role == "support" and context.source_root:
                            m.job_support_roots.setdefault(job_key, []).append(context.source_root)
                    else:
                        m.unresolved_context_refs.setdefault(job_key, []).append({
                            "context_id": context_id,
                            "resolution": "ambiguous" if matches else "unresolved",
                        })
            if linked_contexts:
                m.job_contexts_used[job_key] = sorted(set(linked_contexts))
            if job_key in m.job_support_roots:
                m.job_support_roots[job_key] = sorted(set(m.job_support_roots[job_key]))

            # routines_used_by
            for rname in job.raw_routines:
                m.routines_used_by.setdefault(rname, []).append(job_key)

            # job_external_systems (derived from component types)
            systems: set[str] = set()
            for comp in job.components:
                system = _extract_talend_external_system(comp.component_type)
                if system:
                    systems.add(system)
            if systems:
                m.job_external_systems[job_key] = sorted(systems)

            # Route communication is first-class structural evidence. These
            # records intentionally keep only endpoint/channel *kind* and a
            # redacted, bounded logical name; scanner output must not expose
            # literal URLs or credentials from component parameters.
            if job.artifact_kind == "route":
                for comp in job.components:
                    if comp.component_type in {"cDirect", "cDirectVM", "cJMS"}:
                        channel_type = {
                            "cDirect": "direct",
                            "cDirectVM": "direct-vm",
                            "cJMS": "jms",
                        }[comp.component_type]
                        channel = _safe_route_channel(comp.params)
                        channel_descriptor = _route_channel_descriptor(
                            comp.params, job=job,
                        )
                        m.artifact_channels.append({
                            "artifact": job_key,
                            "channel_type": channel_type,
                            "channel": channel,
                            # Additive: structural facts (scheme/auth/tls/
                            # category) captured from the raw endpoint value
                            # before it was collapsed into `channel` above.
                            # None when nothing could be safely derived.
                            "channel_descriptor": (
                                channel_descriptor.model_dump(exclude_none=True)
                                if channel_descriptor is not None else None
                            ),
                            "direction": _route_component_direction(
                                job, comp.unique_name,
                            ),
                            "component_name": comp.unique_name,
                            "provenance": comp.component_type,
                        })
                        connection_name = _route_channel_connection_name(
                            channel_type, channel,
                        )
                        if connection_name:
                            used = m.job_connections_used.setdefault(job_key, [])
                            if connection_name not in used:
                                used.append(connection_name)
                            users = m.connection_used_by.setdefault(connection_name, [])
                            if job_key not in users:
                                users.append(job_key)
                    elif comp.component_type in {"cREST", "cSOAP", "cFile", "cTimer"}:
                        endpoint_descriptor = _route_channel_descriptor(
                            comp.params, job=job,
                        )
                        m.artifact_endpoints.append({
                            "artifact": job_key,
                            "endpoint_type": comp.component_type.removeprefix("c").lower(),
                            "direction": _route_component_direction(
                                job, comp.unique_name,
                            ),
                            "component_name": comp.unique_name,
                            "provenance": comp.component_type,
                            "endpoint_descriptor": (
                                endpoint_descriptor.model_dump(exclude_none=True)
                                if endpoint_descriptor is not None else None
                            ),
                        })

        return m

    # ── Helpers ───────────────────────────────────────────────────────

    def _read_filtered(self, path: Path) -> str:
        """Read a file with credential filtering and XML Signature stripping."""
        text = path.read_text(encoding="utf-8", errors="replace")
        text = self.cred_filter.filter_text(
            text,
            filename=str(path),
            capture_uri_descriptors=True,
        )
        # Strip XML Signature blocks (they break parsing and add no value)
        text = _strip_signature(text)
        return text


def _is_talend_root(candidate: Path) -> bool:
    """A Talend root has a process/ (Job Designs) and/or routes/ (Route Designs) dir."""
    return (candidate / "process").is_dir() or (candidate / "routes").is_dir()


def _has_safe_connection_discriminator(value: str) -> bool:
    """Whether a pre-post-filter connection value proves physical identity.

    Layer 1 intentionally replaces hostnames/IPs with placeholders and leaves
    context/globalMap expressions dynamic.  Neither can participate in a
    cross-project equality comparison.
    """
    normalized = value.strip().lower()
    return bool(normalized) and not (
        normalized.startswith("[")
        or "redacted" in normalized
        or "configured" in normalized
        or normalized.startswith("context.")
        or normalized.startswith("globalmap")
    )


def _find_talend_roots(repo_root: Path) -> list[Path]:
    """Find all independent Talend roots below an arbitrary source bundle."""
    candidates = [repo_root] if _is_talend_root(repo_root) else []
    candidates.extend(
        p for p in repo_root.rglob("*")
        if p.is_dir()
        and "__MACOSX" not in p.relative_to(repo_root).parts
        and not any(part.startswith("._") for part in p.relative_to(repo_root).parts)
        and _is_talend_root(p)
    )
    candidates.sort(key=lambda p: p.relative_to(repo_root).as_posix())
    selected: list[Path] = []
    for candidate in candidates:
        # process/routes descendants are part of their containing root, not a
        # second project. Other generated trees (poms/jobs, tests, Maps) can
        # also contain process/routes-like directories; only a nested export
        # carrying a real Talend/Eclipse project marker is independent.
        if any(candidate.is_relative_to(root / "process") or candidate.is_relative_to(root / "routes") for root in selected):
            continue
        if any(candidate.is_relative_to(root) for root in selected) and not (
            (candidate / "talend.project").is_file() or (candidate / ".project").is_file()
        ):
            continue
        selected.append(candidate)
    return selected


def _find_talend_project_roots(repo_root: Path) -> list[Path]:
    """Find descriptor-backed Talend projects, including support-only projects."""
    descriptors: list[Path] = []
    root_descriptor = repo_root / "talend.project"
    if root_descriptor.is_file():
        descriptors.append(root_descriptor)
    descriptors.extend(
        descriptor
        for descriptor in repo_root.rglob("talend.project")
        if descriptor != root_descriptor
        and "__MACOSX" not in descriptor.relative_to(repo_root).parts
        and not any(part.startswith("._") for part in descriptor.relative_to(repo_root).parts)
    )

    roots: list[Path] = []
    for descriptor in sorted(descriptors):
        try:
            xml_root = ET.fromstring(
                descriptor.read_text(encoding="utf-8", errors="replace")
            )
        except (ET.ParseError, OSError):
            continue
        if any(_local_tag(elem.tag) == "Project" for elem in xml_root.iter()):
            roots.append(descriptor.parent)
    return roots


def _has_supported_talend_artifacts(candidate: Path) -> bool:
    """Whether a descriptor-backed project contains scanner-supported artifacts."""
    return any((
        (candidate / "context").is_dir(),
        (candidate / "metadata" / "connections").is_dir(),
        (candidate / "code" / "routines").is_dir(),
    ))


def _qualified_job_id(source_root: str, discriminator: str) -> str:
    """URL-safe bounded identity for a colliding Talend job."""
    raw = f"{source_root}::{discriminator}"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_") or "job"
    return f"{slug[:56].rstrip('_') or 'job'}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


_ROUTE_CHANNEL_PARAM_NAMES = frozenset({
    "URI", "ENDPOINT_URI", "DIRECT_URI", "DESTINATION", "QUEUE",
    "QUEUE_NAME", "TOPIC", "TOPIC_NAME", "JMS_DESTINATION", "NAME",
    "URL", "ADDRESS", "WSDL_FILE",
})


def _find_route_channel_value(params: list[ComponentParam]) -> str | None:
    """Return the raw (unredacted) channel/endpoint value, if any is set."""
    for param in params:
        if param.name.upper() not in _ROUTE_CHANNEL_PARAM_NAMES:
            continue
        value = param.value.strip()
        if value:
            return value
    return None


def _safe_route_channel(params: list[ComponentParam]) -> str:
    """Return non-sensitive channel evidence suitable for the topology API.

    Unchanged behavior/shape -- existing `artifact_channels` consumers rely
    on this exact string. See `_route_channel_descriptor` for the additive
    structured evidence captured alongside it.
    """
    value = _find_route_channel_value(params)
    if value is None:
        return "configured channel"
    if value in {"[URL_REDACTED]", "[CONNECTION_STRING_REDACTED]"}:
        return "configured endpoint"
    # URLs often include infrastructure details, query credentials, or a
    # service path. Preserve only that an endpoint is configured.
    if "://" in value or "@" in value or "password" in value.lower():
        return "configured endpoint"
    return re.sub(r"\s+", " ", value)[:120]


def _route_channel_descriptor(
    params: list[ComponentParam],
    *,
    job: ParsedJob | None = None,
) -> RedactionDescriptor | None:
    """Additive structural descriptor for the value `_safe_route_channel`
    collapses to a placeholder. Runs on the same raw value but never
    returns it -- only scheme/auth/tls/category facts, or None."""
    for param in params:
        if param.name.upper() in _ROUTE_CHANNEL_PARAM_NAMES and param.value.strip():
            # The descriptor is captured at Layer 1 before the URI is
            # replaced.  Prefer it over recomputing from the public
            # placeholder, which intentionally has no recoverable shape.
            if param.redaction_descriptor is not None:
                return param.redaction_descriptor
            context_match = re.fullmatch(
                r"context\.([A-Za-z_][A-Za-z0-9_]*)",
                param.value.strip(),
            )
            if context_match is not None and job is not None:
                variable_name = context_match.group(1)
                descriptors = {
                    descriptor.model_dump_json(exclude_none=True): descriptor
                    for group in job.context_groups
                    for variable in group.variables
                    if variable.name == variable_name
                    for descriptor in variable.redaction_descriptors.values()
                }
                # Environment-specific values are only summarized when their
                # safe structural facts agree. Conflicting schemes/TLS/auth
                # remain unknown instead of picking an arbitrary environment.
                if len(descriptors) == 1:
                    return next(iter(descriptors.values()))
            descriptor = CredentialFilter.describe_value(param.value)
            if descriptor is not None:
                return descriptor
    return None


def _route_component_direction(job: ParsedJob, component_name: str) -> str:
    """Classify a Camel endpoint from its position in the Route graph.

    ``input`` means the resource feeds the Route; ``output`` means the Route
    publishes or writes to the resource. Components with both incoming and
    outgoing Route edges are exchanges, while disconnected/incomplete exports
    remain explicitly unknown.
    """
    has_incoming = any(
        connection.target == component_name for connection in job.connections
    )
    has_outgoing = any(
        connection.source == component_name for connection in job.connections
    )
    if has_outgoing and not has_incoming:
        return "input"
    if has_incoming and not has_outgoing:
        return "output"
    if has_incoming and has_outgoing:
        return "both"
    return "unknown"


def _component_resource_direction(component_type: str) -> str:
    """Infer repository-resource access from canonical Talend component names."""
    normalized = component_type.lower()
    if "input" in normalized and "output" in normalized:
        return "both"
    if normalized.endswith("input") or "input" in normalized:
        return "input"
    if normalized.endswith("output") or "output" in normalized:
        return "output"
    return "unknown"


_EXISTING_CONNECTION_REFERENCE_PARAMS = (
    "CONNECTION",
    "CONNECTION_COMPONENT",
    "CONNECTION_NAME",
)


def _existing_connection_component_name(component: Component) -> str:
    """Return the exact in-job connection component referenced by a component.

    Talend input/output components commonly carry no repository reference of
    their own. Instead they set ``USE_EXISTING_CONNECTION=true`` and point at
    a sibling ``t*Connection`` component. This is exact structural evidence,
    not display-name matching.
    """
    params = {param.name.upper(): param.value for param in component.params}
    if params.get("USE_EXISTING_CONNECTION", "").strip().casefold() != "true":
        return ""
    for name in _EXISTING_CONNECTION_REFERENCE_PARAMS:
        value = params.get(name, "").strip().strip("\"'")
        if value:
            return value
    return ""


def _resolve_existing_connection_refs(job: ParsedJob) -> set[str]:
    """Propagate repository identities through exact component references.

    Returns the connection-holder component names used by another component so
    manifest construction can omit their directionless plumbing record while
    retaining the directional input/output evidence.
    """
    components_by_name = {
        component.unique_name: component
        for component in job.components
        if component.unique_name
    }
    referenced: set[str] = set()
    unresolved = {
        component.unique_name: _existing_connection_component_name(component)
        for component in job.components
        if not component.metadata_connection_ref
    }
    # A bounded fixed-point also supports a connection component that itself
    # references another connection component without introducing recursion.
    for _ in range(len(job.components)):
        changed = False
        for component_name, referenced_name in unresolved.items():
            if not referenced_name:
                continue
            referenced.add(referenced_name)
            target = components_by_name.get(referenced_name)
            component = components_by_name.get(component_name)
            if (
                component is not None
                and target is not None
                and not component.metadata_connection_ref
                and target.metadata_connection_ref
            ):
                component.metadata_connection_ref = target.metadata_connection_ref
                changed = True
        if not changed:
            break
    return referenced


def _route_channel_connection_name(channel_type: str, channel: str) -> str:
    """Create a stable shared-channel topology node without inventing identity."""
    normalized = channel.strip().strip("\"'")
    if not normalized or normalized in {"configured channel", "configured endpoint"}:
        return ""
    return f"{channel_type.upper()} · {normalized}"


def _find_talend_root(repo_root: Path) -> Path | None:
    """Backward-compatible single-root helper for external callers."""
    roots = _find_talend_roots(repo_root)
    return roots[0] if roots else None


def _local_tag(tag: str) -> str:
    """Strip namespace from an XML tag. '{ns}name' → 'name'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_process_body(root: ET.Element) -> ET.Element:
    """Return the element whose direct children are context/parameters/node/
    connection -- the body of an executable or reusable Talend design.

    Jobs, Routes, and Routelets all serialize this body directly as the XML
    root (``<ProcessType>``). Joblets nest it one level down inside an
    ``<xmi:XMI>`` wrapper as ``<model:JobletProcess>`` (Talend's EMF joblet
    metamodel is a distinct ecore from the plain ProcessType jobs/routes/
    routelets use). Matched by suffix rather than a hardcoded
    "JobletProcess" string so any future nested-body variant Talend ships
    is still found without another special case.
    """
    if any(
        _local_tag(child.tag) in ("node", "connection", "context", "parameters")
        for child in root
    ):
        return root
    for child in root:
        if _local_tag(child.tag).endswith("Process"):
            return child
    return root


def _find_first_descendant(root: ET.Element, tag_name: str) -> ET.Element | None:
    """Depth-first search for the first descendant element with the given
    (namespace-stripped) local tag name."""
    for elem in root.iter():
        if _local_tag(elem.tag) == tag_name:
            return elem
    return None


def _unescape_java(text: str) -> str:
    """Unescape XML entities commonly found in Talend Java expressions."""
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#10;", "\n")
        .replace("&#9;", "\t")
        .strip()
    )


def _strip_signature(text: str) -> str:
    """Remove XML Signature blocks that interfere with parsing."""
    return re.sub(
        r'<Signature\s+xmlns="http://www\.w3\.org/2000/09/xmldsig#"[^>]*>.*?</Signature>',
        "",
        text,
        flags=re.DOTALL,
    )
