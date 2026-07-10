"""Talend XML/XMI parser — extracts structured data from Talend project files."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

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
    Routine,
    ScanResult,
    ScanWarning,
    TMapData,
    TMapEntry,
    TRunJobRef,
)
from intodoc.platforms.base_parser import BaseParser
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

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
    "tJDBC": "JDBC",
    "tElasticsearch": "Elasticsearch",
    "tDynamoDB": "DynamoDB",
    "tMarketo": "Marketo",
    "tNetSuite": "NetSuite",
    "tSendMail": "Email",
    # ESB/Camel components that represent external systems
    "cREST": "REST API",
    "cRESTClient": "REST API",
    "cJMS": "JMS Broker",
    "cActiveMQ": "ActiveMQ",
    "cKafkaInput": "Kafka",
    "cKafkaOutput": "Kafka",
    "cMQConnectionFactory": "JMS Broker",
    "cMessagingEndpoint": "Messaging",
    "cRoute": "ESB Route",
}

# Suffixes to strip when falling back to component-name heuristic
_STRIP_SUFFIXES = (
    "Input", "Output", "Connection", "Close", "Open", "Row",
    "BulkExec", "CDC", "Commit", "Rollback", "Lookup",
)


# ── ESB / Camel Route Classification ─────────────────────────────────
#
# Talend Studio serializes both regular DI Jobs and ESB Routes (Camel-based)
# into the same .item/.properties format under process/. There is no file-type
# distinction; the only signal that a job is an ESB/Camel route is its use of
# c-prefixed Camel components (versus t-prefixed Talend DI components). This is
# the single source of truth for that detection -- the mcp-server consumes the
# computed is_esb_route / esb_component_types fields rather than re-deriving them.
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

    Returns None for internal/utility components.
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

    # Fallback: strip 't' prefix + known suffixes -> title case
    name = component_type
    if name.startswith("t") and len(name) > 1:
        name = name[1:]
    for suffix in _STRIP_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if len(name) < 3:
        return None
    return name


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

    def parse(self, repo_root: Path) -> ScanResult:
        """Parse the entire Talend project."""
        from intodoc.platforms.talend import TalendPlugin

        plugin = TalendPlugin()
        # Reset warning collection for this scan.
        self._warnings = []
        self._warn_root = repo_root

        # Find the actual Talend project root (may be nested)
        talend_root = _find_talend_root(repo_root)
        if talend_root is None:
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

        console.print(f"  Talend project root: [cyan]{talend_root}[/cyan]")

        # Walk and classify files — exclude UNKNOWN to shrink scan output
        all_files = walk_repo(repo_root, plugin)
        files = [f for f in all_files if f.file_type != FileType.UNKNOWN]
        console.print(f"  Files walked: {len(all_files)}, indexed (non-UNKNOWN): {len(files)}")

        # Step 1a: Build xmi:id index from metadata connections
        self._build_metadata_index(talend_root)
        console.print(f"  Metadata connections indexed: {len(self._xmi_id_index)}")

        # Step 1b: Build job ID index for resolving tRunJob XMI refs
        self._build_job_id_index(talend_root)
        console.print(f"  Job ID index built: {len(self._job_id_index)} entries")

        # Step 2: Parse context files
        context_groups = self._parse_all_contexts(talend_root)
        console.print(f"  Context groups parsed: {len(context_groups)}")

        # Step 3: Parse job files (.item + .properties sidecars)
        jobs = self._parse_all_jobs(talend_root)
        console.print(f"  Jobs parsed: {len(jobs)}")

        # Step 4: Collect routines
        routines = self._parse_routines(talend_root)
        console.print(f"  Routines found: {len(routines)}")

        # Step 5: Build cross-reference manifest
        manifest = self._build_manifest(jobs, routines)
        console.print(f"  Manifest built: {len(manifest.job_calls)} job-call edges")

        return ScanResult(
            platform="talend",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=list(self._xmi_id_index.values()),
            context_groups=context_groups,
            routines=routines,
            manifest=manifest,
            stats={
                "total_files_walked": len(all_files),
                "total_files_indexed": len(files),
                "total_jobs": len(jobs),
                "total_connections": len(self._xmi_id_index),
                "total_contexts": len(context_groups),
                "total_routines": len(routines),
                "total_warnings": len(self._warnings),
            },
            warnings=list(self._warnings),
        )

    # ── Metadata Index ───────────────────────────────────────────────

    def _build_metadata_index(self, talend_root: Path) -> None:
        """Build xmi:id → MetadataConnection index from metadata/connections/*.item files."""
        conn_dir = talend_root / "metadata" / "connections"
        if not conn_dir.exists():
            return

        for item_file in sorted(conn_dir.glob("*.item")):
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
        """Build Property.id → Property.label index from process/**/*.properties files."""
        process_dir = talend_root / "process"
        if not process_dir.exists():
            return

        for props_file in sorted(process_dir.rglob("*.properties")):
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
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {props_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=props_file, message=str(e), phase="job_id_index")

    # ── Context Parsing ──────────────────────────────────────────────

    def _parse_all_contexts(self, talend_root: Path) -> list[ContextGroup]:
        """Parse all context/*.item files."""
        ctx_dir = talend_root / "context"
        if not ctx_dir.exists():
            return []

        groups: list[ContextGroup] = []
        for item_file in sorted(ctx_dir.glob("*.item")):
            try:
                text = self._read_filtered(item_file)
                root = ET.fromstring(text)
                group = self._parse_context_file(root, item_file.stem)
                if group:
                    groups.append(group)
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {item_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=item_file, message=str(e), phase="context")

        return groups

    def _parse_context_file(self, root: ET.Element, file_stem: str) -> ContextGroup | None:
        """Parse a context .item file into a ContextGroup."""
        # Extract the group name from file stem (e.g., "BRA_VBR_environment_0.1" → "BRA_VBR_environment")
        name = file_stem.rsplit("_", 1)[0] if "_0." in file_stem else file_stem

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
                        variables[var_name].values[env_name] = param.get("value", "")

        if not variables:
            return None

        return ContextGroup(name=name, variables=list(variables.values()))

    # ── Job Parsing ──────────────────────────────────────────────────

    # Talend Studio stores DI Job Designs under process/ and ESB/Camel Route
    # Designs under a separate top-level routes/ directory -- both must be
    # walked or Route jobs are silently invisible to the scanner (never parsed
    # at all, not merely misclassified).
    _JOB_SOURCE_DIRS = ("process", "routes")

    def _parse_all_jobs(self, talend_root: Path) -> list[ParsedJob]:
        """Parse all process/**/*.item and routes/**/*.item job files with their .properties sidecars."""
        jobs: list[ParsedJob] = []
        for dir_name in self._JOB_SOURCE_DIRS:
            base_dir = talend_root / dir_name
            if not base_dir.exists():
                continue
            for item_file in sorted(base_dir.rglob("*.item")):
                try:
                    job = self._parse_job(item_file, base_dir)
                    if job:
                        jobs.append(job)
                except ET.ParseError as e:
                    console.print(f"  [yellow]XML parse error in {item_file.name}: {e}[/yellow]")
                    self._warn(kind="parse_error", file=item_file, message=str(e), phase="job")
                except Exception as e:
                    console.print(f"  [yellow]Error parsing {item_file.name}: {e}[/yellow]")
                    self._warn(kind="unknown", file=item_file, message=str(e), phase="job")

        return jobs

    def _parse_job(self, item_file: Path, base_dir: Path) -> ParsedJob | None:
        """Parse a single job .item file + its .properties sidecar.

        `base_dir` is the process/ or routes/ directory the file was found under.
        """
        text = self._read_filtered(item_file)
        root = ET.fromstring(text)

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
        for elem in root:
            tag = _local_tag(elem.tag)
            if tag == "context":
                self._parse_job_context(elem, job)

        # Parse parameters block, nodes, and connections
        for elem in root:
            tag = _local_tag(elem.tag)
            if tag == "parameters":
                self._parse_job_params(elem, job)
            elif tag == "node":
                self._parse_node(elem, job)
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
                    variables.append(ContextVariable(
                        name=var_name,
                        type=param.get("type", "id_String"),
                        values={env_name: param.get("value", "")},
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

    def _parse_node(self, node_elem: ET.Element, job: ParsedJob) -> None:
        """Parse a <node> element into a Component."""
        comp_type = node_elem.get("componentName", "")
        unique_name = ""
        params: list[ComponentParam] = []

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
                    params.append(ComponentParam(name=name, value=value, field_type=field))

            elif tag == "nodeData":
                # tMap MapperData — try multiple namespace patterns
                xsi_type = (
                    child.get(f"{{{NS['xsi']}}}type", "")
                    or child.get(f"{{{NS['xmi']}}}type", "")
                    or child.get("type", "")
                )
                if "MapperData" in xsi_type:
                    tmap = self._parse_mapper_data(child)
                    job.tmap_data.append(tmap)

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
        job.components.append(comp)

        # Extract tRunJob references
        if comp_type == "tRunJob":
            self._extract_trunjob_ref(params, job)

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

    # ── Manifest ─────────────────────────────────────────────────────

    @staticmethod
    def _build_manifest(jobs: list[ParsedJob], routines: list[Routine]) -> ProjectManifest:
        """Build cross-reference manifest from parsed jobs."""
        m = ProjectManifest()

        for job in jobs:
            # job_groups
            if job.group:
                m.job_groups[job.name] = job.group

            # job_calls / job_called_by (from tRunJob refs)
            if job.trunjob_refs:
                children = [r.child_job for r in job.trunjob_refs]
                m.job_calls[job.name] = children
                for child in children:
                    m.job_called_by.setdefault(child, []).append(job.name)

            # job_connections_used / connection_used_by (from component refs)
            conn_names: set[str] = set()
            for comp in job.components:
                if comp.metadata_connection_ref:
                    conn_names.add(comp.metadata_connection_ref)
            if conn_names:
                m.job_connections_used[job.name] = sorted(conn_names)
                for cn in conn_names:
                    m.connection_used_by.setdefault(cn, []).append(job.name)

            # job_contexts_used (from context groups)
            ctx_names = [cg.name for cg in job.context_groups if cg.name != "inline"]
            if ctx_names:
                m.job_contexts_used[job.name] = ctx_names

            # routines_used_by
            for rname in job.raw_routines:
                m.routines_used_by.setdefault(rname, []).append(job.name)

            # job_external_systems (derived from component types)
            systems: set[str] = set()
            for comp in job.components:
                system = _extract_talend_external_system(comp.component_type)
                if system:
                    systems.add(system)
            if systems:
                m.job_external_systems[job.name] = sorted(systems)

        return m

    # ── Helpers ───────────────────────────────────────────────────────

    def _read_filtered(self, path: Path) -> str:
        """Read a file with credential filtering and XML Signature stripping."""
        text = path.read_text(encoding="utf-8", errors="replace")
        text = self.cred_filter.filter_text(text, filename=str(path))
        # Strip XML Signature blocks (they break parsing and add no value)
        text = _strip_signature(text)
        return text


def _is_talend_root(candidate: Path) -> bool:
    """A Talend project root has a process/ (Job Designs) and/or routes/ (Route Designs) dir."""
    return (candidate / "process").is_dir() or (candidate / "routes").is_dir()


def _find_talend_root(repo_root: Path) -> Path | None:
    """Find the actual Talend project root (the dir containing process/ and/or routes/)."""
    # Check if repo_root itself is a Talend project
    if _is_talend_root(repo_root):
        return repo_root

    # Check one level deep
    for child in repo_root.iterdir():
        if child.is_dir() and _is_talend_root(child):
            return child

    # Check two levels deep
    for child in repo_root.iterdir():
        if child.is_dir():
            for grandchild in child.iterdir():
                if grandchild.is_dir() and _is_talend_root(grandchild):
                    return grandchild

    return None


def _local_tag(tag: str) -> str:
    """Strip namespace from an XML tag. '{ns}name' → 'name'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


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
