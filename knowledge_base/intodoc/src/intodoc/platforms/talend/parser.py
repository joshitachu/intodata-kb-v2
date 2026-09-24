"""Talend XML/XMI parser — extracts structured data from Talend project files."""

from __future__ import annotations

import re
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PurePosixPath

from rich.console import Console

from intodoc.models import (
    Component,
    ComponentParam,
    ComponentSchema,
    ComponentSchemaColumn,
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
    ServiceDefinition,
    StructureDefinition,
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
    XsltTransform,
)
from intodoc.platforms.base_parser import BaseParser
from intodoc.platforms.talend.resource_refs import build_resource_refs
from intodoc.platforms.talend.parse_cache import CachedDesign, TalendDesignCache, design_cache_key
from intodoc.platforms.talend.services import (
    claimed_service_files,
    parse_services,
    parse_standalone_wsdls,
)
from intodoc.platforms.talend.structures import parse_structures
from intodoc.platforms.talend.xslt import parse_xslt_transforms
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.fswalk import (
    case_variant_names,
    child_dir,
    child_dirs,
    child_file,
    walk_dirs,
    walk_files,
)
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

    def __init__(self, cred_filter: CredentialFilter, *, parse_cache_dir: Path | None = None) -> None:
        super().__init__(cred_filter)
        self._parse_cache = TalendDesignCache(parse_cache_dir) if parse_cache_dir else None
        # xmi:id → MetadataConnection lookup (built at scan start)
        self._xmi_id_index: dict[str, MetadataConnection] = {}
        # Property.id → (Property.label, version-stripped repository path)
        # for resolving tRunJob/cTalendJob refs by IDENTITY. The path breaks
        # same-name ambiguity inside one root, which a label alone cannot.
        self._job_id_index: dict[str, tuple[str, str]] = {}
        # Superseded job versions skipped by _latest_job_versions (per scan)
        self._superseded_versions = 0
        self._artifact_versions: dict[str, dict[str, str]] = {}

    def parse(self, repo_root: Path) -> ScanResult:
        """Parse every Talend root in a source bundle as one logical project."""
        from intodoc.platforms.talend import TalendPlugin

        plugin = TalendPlugin()
        # Reset warning collection for this scan.
        self._warnings = []
        self._warn_root = repo_root
        self._superseded_versions = 0
        self._artifact_versions = {}
        if self._parse_cache:
            self._parse_cache.reset_metrics()

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
                stats={
                    "parse_cache_enabled": self._parse_cache is not None,
                    "parse_cache_hits": 0,
                    "parse_cache_misses": 0,
                    "parse_cache_invalid_entries": 0,
                    "parse_cache_writes": 0,
                },
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
        source_inventory: list[dict[str, str]] = []
        all_files = walk_repo(
            repo_root, plugin, inventory=source_inventory, on_error=self._walk_error,
        )
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
        structures: list[StructureDefinition] = []
        xslt_transforms: list[XsltTransform] = []
        services: list[ServiceDefinition] = []
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
            routines.extend(self._parse_routines(talend_root, source_root=source_root))
            tdm_maps.extend(self._parse_tdm_maps(talend_root, source_root, root_jobs))
            # Enterprise artifact categories discovered by REPOSITORY
            # LAYOUT: `Structures/` and `services/` are Talend conventions
            # read together with their `.properties` sidecars, so a project
            # root is the correct scope for them and a directory of that
            # name outside a project proves nothing. They are parsed for
            # every root, including support-only roots: a shared Structures/
            # project is exactly the case where the schema lives apart from
            # the map. Stylesheets and standalone WSDLs are discovered by
            # FILE TYPE and are handled bundle-wide after this loop.
            structures.extend(parse_structures(
                talend_root, source_root,
                read_text=self._read_filtered,
                read_sidecar=self._read_properties_sidecar,
                warn=self._warn,
                latest_files=self._latest_versioned_files,
            ))
            services.extend(parse_services(
                talend_root, source_root,
                read_text=self._read_filtered,
                read_sidecar=self._read_properties_sidecar,
                warn=self._warn,
                latest_files=self._latest_versioned_files,
            ))
            self._warn_unscanned_categories(
                talend_root, talend_roots, classify=plugin.classify_file,
            )
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

        # Stylesheets and standalone WSDLs are self-describing: each carries
        # its whole meaning in its own bytes and needs neither a
        # `.properties` sidecar nor a `talend.project` above it. Discovering
        # them inside the per-root loop confined the search to directories
        # holding a Talend project descriptor, so every one an estate ships
        # in a separately uploaded mapping folder was classified in the file
        # inventory and then never parsed -- on the reference estate, 34 of
        # 42 stylesheets and 1 of 2 WSDLs. Discovery is therefore bundle-wide
        # and ownership is resolved per file.
        self._warnings = []
        attribute = _support_artifact_owner(repo_root, talend_roots)
        xslt_transforms = parse_xslt_transforms(
            repo_root, attribute,
            read_text=self._read_filtered,
            warn=self._warn,
        )
        services.extend(parse_standalone_wsdls(
            repo_root, attribute,
            claimed=claimed_service_files(repo_root, services),
            read_text=self._read_filtered,
            warn=self._warn,
        ))
        services.sort(key=lambda service: (service.source_root, service.path))
        all_warnings.extend(self._warnings)

        composite = len(execution_roots) > 1
        connections = self._qualify_composite_connections(jobs, connections, composite)
        self._qualify_composite_jobs(jobs, composite)
        self._qualify_context_groups(context_groups)
        manifest = self._build_manifest(jobs, routines, root_descriptors, context_groups)
        manifest.artifact_versions = list(self._artifact_versions.values())
        # Exact-token binding of components to the support artifacts they
        # name. Also fills XsltTransform.referenced_by, so the transform
        # list and this ledger can never disagree.
        manifest.resource_refs = build_resource_refs(
            jobs,
            structures=structures,
            transforms=xslt_transforms,
            services=services,
            tdm_maps=tdm_maps,
        )
        # `ParsedJob.path` is relative to its DESIGN directory (process/,
        # routes/, ...), not to the source root, so joining it directly
        # produced a path that matched no inventory row -- every parsed job
        # was reported as merely "indexed" and the file-outcome accounting
        # under-counted every scan.
        parsed_paths = {
            (
                Path(job.source_root) / (job.source_design or "process") / job.path
            ).as_posix()
            for job in jobs
        } | {
            (Path(r.source_root) / r.path).as_posix() for r in routines if r.source_complete
        } | {(Path(t.source_root) / t.path).as_posix() for t in tdm_maps} | {
            (Path(artifact.source_root) / artifact.path).as_posix()
            for collection in (structures, xslt_transforms, services)
            for artifact in collection
            if artifact.parse_outcome != "unsupported"
        } | {
            (Path(service.source_root) / service.wsdl_path).as_posix()
            for service in services
            if service.wsdl_path and service.parse_outcome != "unsupported"
        }
        # Only warnings about extraction completeness downgrade a file's
        # outcome. A stale label cache or an unscanned sibling directory
        # says nothing about whether THIS file parsed, and treating every
        # warning kind as a parse failure made a fully parsed job report
        # "partial_or_failed".
        warning_paths = {
            w.file for w in all_warnings if w.kind in _PARTIAL_OUTCOME_WARNINGS
        }
        for entry in source_inventory:
            path = entry["path"]
            if path in self._artifact_versions and self._artifact_versions[path]["selection"] == "superseded":
                entry.update(outcome="superseded", reason="higher_export_version_selected")
            elif path in warning_paths:
                entry.update(outcome="partial_or_failed", reason="see_scan_warnings")
            elif path in parsed_paths:
                entry.update(outcome="parsed", reason="supported_artifact_extracted")
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
        if self._parse_cache:
            console.print(
                "  Talend parse cache: "
                f"{self._parse_cache.hits} hit(s), {self._parse_cache.misses} miss(es), "
                f"{self._parse_cache.invalid_entries} invalid entry/entries"
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
            structures=structures,
            xslt_transforms=xslt_transforms,
            services=services,
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
                "structure_count": len(structures),
                "structure_element_count": sum(s.element_count for s in structures),
                "xslt_transform_count": len(xslt_transforms),
                "xslt_referenced_count": sum(
                    bool(t.referenced_by) for t in xslt_transforms
                ),
                "service_count": len(services),
                "service_operation_count": sum(
                    len(service.operations) for service in services
                ),
                "parse_cache_enabled": self._parse_cache is not None,
                "parse_cache_hits": self._parse_cache.hits if self._parse_cache else 0,
                "parse_cache_misses": self._parse_cache.misses if self._parse_cache else 0,
                "parse_cache_invalid_entries": self._parse_cache.invalid_entries if self._parse_cache else 0,
                "parse_cache_writes": self._parse_cache.writes if self._parse_cache else 0,
                "disabled_component_count": sum(
                    not component.active
                    for job in jobs for component in job.components
                ),
                "component_schema_count": sum(
                    len(component.schemas)
                    for job in jobs for component in job.components
                ),
            },
            warnings=all_warnings,
            source_inventory=source_inventory,
        )

    # ── Metadata Index ───────────────────────────────────────────────

    def _build_metadata_index(self, talend_root: Path) -> None:
        """Build xmi:id → MetadataConnection index from metadata/connections/*.item files."""
        conn_dir = child_dir(talend_root, "metadata", "connections", on_error=self._walk_error)
        if conn_dir is None:
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
            (directory, props_file)
            for directory in self._JOB_SOURCE_DIRS
            if (source := child_dir(talend_root, directory)) is not None
            for props_file in sorted(
                walk_files(source, patterns=("*.properties",), on_error=self._walk_error)
            )
        ]
        if not props_files:
            return

        for directory, props_file in props_files:
            try:
                text = self._read_filtered(props_file)
                root = ET.fromstring(text)
                for elem in root:
                    tag = _local_tag(elem.tag)
                    if tag == "Property":
                        prop_id = elem.get("id", "")
                        prop_label = elem.get("label", "")
                        if prop_id and prop_label:
                            entry = (
                                prop_label,
                                _artifact_path_key(
                                    directory,
                                    props_file.relative_to(
                                        talend_root / directory,
                                    ).as_posix(),
                                ),
                            )
                            self._job_id_index[prop_id] = entry
                            self._job_id_index[prop_id.lstrip("_")] = entry
            except ET.ParseError as e:
                console.print(f"  [yellow]XML parse error in {props_file.name}: {e}[/yellow]")
                self._warn(kind="parse_error", file=props_file, message=str(e), phase="job_id_index")

    # ── Context Parsing ──────────────────────────────────────────────

    def _latest_item_files(self, base_dir: Path) -> list[Path]:
        """Highest-version .item file per (directory, artifact name)."""
        return self._latest_versioned_files(base_dir, "*.item")

    def _latest_versioned_files(self, base_dir: Path, pattern: str = "*.item") -> list[Path]:
        """Highest-version file per (directory, artifact name), recursive.

        Talend Studio exports keep every saved version of an item
        (Name_0.1.item ... Name_3.3.item) and may nest items in per-group
        subfolders (flat and nested layouts both occur in real exports).
        Superseded versions are counted in self._superseded_versions.
        """
        latest: dict[tuple[str, str], tuple[tuple[int, ...], Path]] = {}
        # One walk feeds both passes below. Two independent walks could also
        # disagree with each other if the tree changed between them.
        all_files = sorted(
            walk_files(base_dir, patterns=(pattern,), on_error=self._walk_error)
        )
        for f in all_files:
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
        selected = [f for _, f in sorted(latest.values(), key=lambda t: str(t[1]))]
        for f in all_files:
            match = re.match(r"(.+?)_(\d+(?:\.\d+)*)$", f.stem)
            path = f.relative_to(self._warn_root).as_posix() if self._warn_root and f.is_relative_to(self._warn_root) else f.name
            self._artifact_versions[path] = {
                "path": path,
                "version": match.group(2) if match else "",
                "selection": "selected" if f in selected else "superseded",
                "reason": "highest_export_version_not_deployment_evidence",
            }
        return selected

    def _parse_all_contexts(
        self,
        talend_root: Path,
        *,
        source_root: str = "",
        source_project: str = "",
        source_role: str = "",
    ) -> list[ContextGroup]:
        """Parse context/**/*.item files (latest version per group)."""
        ctx_dir = child_dir(talend_root, "context", on_error=self._walk_error)
        if ctx_dir is None:
            self._warn_case_variant(talend_root, "context", phase="context")
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
        # services/ is parsed (see services.py). resources/ and
        # route_resources/ deliberately stay OUT: their stylesheets are
        # parsed but they also hold payloads nothing reads, and
        # _warn_unscanned_categories counts exactly those.
        "services",
        "lib", "sqlPatterns", ".settings",
    })

    def _parse_all_jobs(self, talend_root: Path) -> list[ParsedJob]:
        """Parse all process/routes/joblets/routelets *.item design files
        (with their .properties sidecars) into ParsedJob records."""
        jobs: list[ParsedJob] = []
        for dir_name, artifact_kind in self._ARTIFACT_DIR_KINDS.items():
            base_dir = child_dir(talend_root, dir_name, on_error=self._walk_error)
            if base_dir is None:
                self._warn_case_variant(talend_root, dir_name, phase="job")
                continue
            for item_file in self._latest_item_files(base_dir):
                try:
                    job = self._parse_job_cached(item_file, base_dir)
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

    def _resolution_fingerprint(self, item_file: Path) -> str:
        """Hash only resolution records this design can actually reference.

        A project-wide index fingerprint made every unchanged design miss its
        cache when an unrelated job was added.  Talend references live in XML
        attributes, so matching index identities against those raw values
        preserves correctness: a newly resolvable or renamed referenced target
        changes the key, while unrelated index growth does not.  Target paths
        remain excluded because replay deliberately rebinds rename-only moves.
        """

        try:
            root = ET.parse(item_file).getroot()
            attribute_values = "\x1f".join(
                str(value)
                for element in root.iter()
                for value in element.attrib.values()
            )
        except (OSError, ET.ParseError):
            attribute_values = ""
        jobs = sorted(
            {
                (key, value[0])
                for key, value in self._job_id_index.items()
                if key and key in attribute_values
            }
        )
        metadata = sorted(
            {
                (key, value.name)
                for key, value in self._xmi_id_index.items()
                if key and key in attribute_values
            }
        )
        return hashlib.sha256(repr((jobs, metadata)).encode("utf-8")).hexdigest()

    def _parse_job_cached(self, item_file: Path, base_dir: Path) -> ParsedJob | None:
        if self._parse_cache is None:
            job = self._parse_job(item_file, base_dir)
            if job is not None:
                self.cred_filter.normalize_model_tokens(job)
            return job
        key = design_cache_key(
            item_file,
            redaction_fingerprint=self.cred_filter.cache_fingerprint,
            resolution_fingerprint=self._resolution_fingerprint(item_file),
        )
        relative_item = (
            item_file.relative_to(self._warn_root).as_posix()
            if self._warn_root and item_file.is_relative_to(self._warn_root)
            else item_file.name
        )
        cached = self._parse_cache.read(key)
        if cached is not None:
            self.cred_filter.replay_audit(cached.redaction_audit)
            return self._replay_cached_design(cached, item_file, base_dir, relative_item)

        warning_start = len(self._warnings)
        audit_checkpoint = self.cred_filter.audit_checkpoint()
        properties = self._read_properties_sidecar(item_file)
        job = self._parse_job(item_file, base_dir, properties=properties)
        if job is not None:
            # Descriptor tokens are process-local transports. Normalize every
            # unconsumed token before returning or caching the public parsed
            # model so cold and warm scans are byte-equivalent.
            self.cred_filter.normalize_model_tokens(job)
            self._parse_cache.write(
                key,
                job=job,
                warnings=self._warnings[warning_start:],
                source_item=relative_item,
                name_from_path=not bool(properties.get("label")),
                redaction_audit=self.cred_filter.audit_since(audit_checkpoint),
            )
        return job

    def _replay_cached_design(
        self, cached: CachedDesign, item_file: Path, base_dir: Path, relative_item: str,
    ) -> ParsedJob:
        job = cached.job.model_copy(deep=True)
        old_path = job.path
        new_path = item_file.relative_to(base_dir).as_posix()
        old_target_paths = {
            ref.child_job_xmi_id: ref.target_item_path for ref in job.trunjob_refs if ref.child_job_xmi_id
        } | {
            ref.target_xmi_id: ref.target_item_path for ref in job.artifact_refs if ref.target_xmi_id
        }
        job.path = new_path
        parts = new_path.split("/")
        job.group = parts[0] if len(parts) > 1 else ""
        job.subgroup = parts[1] if len(parts) > 2 else ""
        if cached.name_from_path:
            job.name = item_file.stem.rsplit("_0.", 1)[0]

        for component in job.components:
            if component.repository_property_ref:
                resolved = self._xmi_id_index.get(component.repository_property_ref)
                component.metadata_connection_ref = resolved.name if resolved else ""
        for ref in job.trunjob_refs:
            identity = next((self._job_id_index.get(candidate)
                             for candidate in _identity_candidates(ref.child_job_xmi_id)
                             if self._job_id_index.get(candidate)), None)
            if identity:
                ref.child_job, ref.target_item_path, ref.reference_source = identity[0], identity[1], "identity"
        for ref in job.artifact_refs:
            identity = next((self._job_id_index.get(candidate)
                             for candidate in _identity_candidates(ref.target_xmi_id)
                             if self._job_id_index.get(candidate)), None)
            if identity:
                ref.target_name, ref.target_item_path, ref.reference_source = identity[0], identity[1], "identity"

        replacements = {cached.source_item: relative_item, old_path: new_path}
        for ref in [*job.trunjob_refs, *job.artifact_refs]:
            guid = getattr(ref, "child_job_xmi_id", "") or getattr(ref, "target_xmi_id", "")
            old_target = old_target_paths.get(guid, "")
            if old_target and ref.target_item_path:
                replacements[old_target] = ref.target_item_path
        for warning in cached.warnings:
            replay = warning.model_copy(deep=True)
            if replay.file == cached.source_item:
                replay.file = relative_item
            for old, new in replacements.items():
                if old and old != new:
                    replay.message = replay.message.replace(old, new)
            self._warnings.append(replay)
        return job

    # File types this parser extracts into the scan result. A directory
    # holding only these is scanned, whatever its name.
    _PARSED_FILE_TYPES: frozenset[FileType] = frozenset({
        FileType.JOB, FileType.JOB_PROPERTIES, FileType.CONNECTION,
        FileType.CONTEXT, FileType.ROUTINE, FileType.JOBLET,
        FileType.TDM_MAP, FileType.STRUCTURE, FileType.XSLT,
        FileType.SERVICE, FileType.WSDL, FileType.PROJECT,
    })

    def _warn_unscanned_categories(
        self,
        talend_root: Path,
        all_roots: Iterable[Path],
        *,
        classify: Callable[[Path], FileType] | None = None,
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
        for child in sorted(child_dirs(talend_root, on_error=self._walk_error)):
            if child.name.startswith("."):
                continue
            if child.name in self._KNOWN_TALEND_ROOT_DIRS:
                continue
            if any(
                root.is_relative_to(child) or child.is_relative_to(root)
                for root in nested_roots
            ):
                continue
            # Count only files nothing extracts. route_resources/ holding
            # nothing but stylesheets IS scanned now, and claiming otherwise
            # would be a false gap; a jar beside them still is one.
            candidates = sorted(walk_files(child, on_error=self._walk_error))
            unparsed = [
                candidate
                for candidate in candidates
                if not (
                    classify is not None
                    and classify(candidate) in self._PARSED_FILE_TYPES
                )
            ]
            if not unparsed:
                continue
            # Name the files, not the directory. `resources/` holds both the
            # stylesheets this scanner parses and payloads it does not, so
            # "the directory is not parsed, its contents are invisible" read
            # as a denial of evidence that is demonstrably in the scan --
            # exactly the failure mode a downstream document must not repeat.
            self._warn(
                kind="unscanned_category",
                file=child,
                message=(
                    f"Talend repository directory '{child.name}': "
                    f"{len(candidates)} files, "
                    f"{len(candidates) - len(unparsed)} parsed as supported "
                    f"artifacts, {len(unparsed)} of a kind this scanner does "
                    "not parse -- those are invisible to downstream analysis."
                ),
                phase="root_discovery",
            )

    def _warn_case_variant(self, parent: Path, *names: str, phase: str) -> None:
        """Say so when a directory exists under a different capitalisation.

        Talend's repository layout has fixed casing, so a differently-cased
        directory is a customer folder rather than the convention one -- but
        skipping it silently would look identical to it not being there.
        """
        variants = case_variant_names(parent, *names)
        if not variants:
            return
        self._warn(
            kind="case_variant_directory",
            file=parent / variants[0],
            message=(
                f"Directory {variants[0]!r} differs only by case from the Talend "
                f"convention {names[0]!r} and was NOT parsed as it; Talend uses "
                "exact lowercase repository directories."
            ),
            phase=phase,
        )

    @staticmethod
    def _version_sort_key(version: str) -> tuple[int, ...]:
        """Numeric sort key for Talend item versions ('3.3' > '0.10' > '0.9')."""
        return tuple(int(part) for part in re.findall(r"\d+", version))

    def _parse_job(
        self, item_file: Path, base_dir: Path, *, properties: dict[str, str] | None = None,
    ) -> ParsedJob | None:
        """Parse a single job .item file + its .properties sidecar.

        `base_dir` is the process/, routes/, joblets/, or routelets/
        directory the file was found under.
        """
        text = self._read_filtered(item_file)
        root = ET.fromstring(text)
        body = _find_process_body(root)

        # Read .properties sidecar for name/version/dates
        props = properties if properties is not None else self._read_properties_sidecar(item_file)

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
                result["property_id"] = elem.get("id", "")
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

    def _parse_element_param(self, param_elem: ET.Element) -> ComponentParam | None:
        """One ``<elementParameter>`` as a ComponentParam, or None if it carries nothing.

        Talend serializes parameters identically wherever they hang -- on a
        ``<node>``, on a ``<connection>``, inside a nested block -- so this is
        the ONE reader for that element. It used to live inline in
        ``_parse_node``; ``_parse_connection`` had its own two-name reader
        (UNIQUE_NAME, ACTIVATE) and silently dropped everything else, which is
        how every Camel ``ROUTE_WHEN`` predicate in every scan was lost. A
        second reader is the defect; there is now one.

        Callers own only their promotions (a node's UNIQUE_NAME becomes
        ``Component.unique_name``, a connection's becomes ``Connection.name``).
        """
        name = param_elem.get("name", "")
        value = param_elem.get("value", "")
        field = param_elem.get("field", "")

        # Skip UI noise (designer-only layout hints, not behaviour).
        if name in ("COMP_DEFAULT_FILE_DIR",) or name.startswith("offsetLabel"):
            return None
        # A TABLE-field parameter carries its payload in <elementValue>
        # children and leaves `value` EMPTY, so the empty-value skip below
        # used to discard the entire table: every tRunJob CONTEXTPARAMS
        # override in every scan was silently lost. A route's NAMESPACES
        # table is the same shape, and an XPath predicate is uninterpretable
        # without it.
        rows = (
            _parse_table_param_rows(param_elem, self.cred_filter)
            if field.strip().upper() == "TABLE" else []
        )
        # Skip empty values
        if not value and not rows:
            return None
        # Layer 1 may have replaced a whole URI with an opaque descriptor
        # marker.  Recover only its safe structural descriptor, then
        # immediately normalize the marker to the ordinary public placeholder
        # before this parsed model can be persisted or exposed downstream.
        descriptor = self.cred_filter.descriptor_from_redaction_token(value)
        value = self.cred_filter.normalize_redaction_tokens(value)
        if descriptor is None and rows:
            # `rows` is dict[str, str] with no descriptor slot, so a row URI
            # is only summarized when exactly one row value carried a marker
            # -- never guessed from several conflicting ones.
            descriptor = _single_row_descriptor(param_elem, self.cred_filter)
        return ComponentParam(
            name=name,
            value=value,
            field_type=field,
            rows=rows,
            redaction_descriptor=descriptor,
        )

    def _parse_node(
        self, node_elem: ET.Element, job: ParsedJob, item_file: Path | None = None,
    ) -> None:
        """Parse a <node> element into a Component."""
        comp_type = node_elem.get("componentName", "")
        unique_name = ""
        params: list[ComponentParam] = []
        txmlmaps: list[TXMLMapData] = []
        tmaps: list[TMapData] = []
        schema_elements: list[ET.Element] = []

        for child in node_elem:
            tag = _local_tag(child.tag)
            if tag == "elementParameter":
                if child.get("name", "") == "UNIQUE_NAME":
                    unique_name = child.get("value", "")
                    continue
                param = self._parse_element_param(child)
                if param is not None:
                    params.append(param)

            elif tag == "metadata":
                # Component row schema (one <metadata> per connector:
                # FLOW, REJECT, or a component-specific name).
                schema_elements.append(child)

            elif tag == "nodeData":
                # tMap MapperData / tXMLMap XmlMapData — try multiple namespace patterns
                xsi_type = (
                    child.get(f"{{{NS['xsi']}}}type", "")
                    or child.get(f"{{{NS['xmi']}}}type", "")
                    or child.get("type", "")
                )
                if "MapperData" in xsi_type and "XmlMapData" not in xsi_type:
                    tmaps.append(self._parse_mapper_data(child))
                elif "XmlMapData" in xsi_type:
                    txmap = self._parse_xml_map_data(child, node_index=len(job.components))
                    txmlmaps.append(txmap)
                    if item_file is not None and txmap.unresolved_connections:
                        self._warn(
                            kind="partial_parse",
                            file=item_file,
                            message=(
                                f"tXMLMap '{unique_name or comp_type}' in job "
                                f"'{job.name}': input/output field trees and "
                                f"output expressions captured; {len(txmap.unresolved_connections)} "
                                "positional connections have unsupported or invalid references."
                            ),
                            phase="txmlmap",
                        )

        # UNIQUE_NAME is a direct property of the enclosing node, so it is
        # reliable component ownership evidence even though XmlMapData's
        # internal EMF proxy wiring remains intentionally unresolved.
        for txmap in txmlmaps:
            txmap.component_name = unique_name
        job.txmlmap_data.extend(txmlmaps)
        # Same ownership evidence for classic tMap. `TMapData.component_name`
        # existed but was never populated, so every tMap in every scan was
        # anonymous and no consumer could attribute a mapping to a component.
        for tmap in tmaps:
            tmap.component_name = unique_name
        job.tmap_data.extend(tmaps)

        # Resolve metadata connection reference (REPOSITORY_PROPERTY_TYPE)
        conn_ref = ""
        repository_property_ref = ""
        declaration = ""
        for p in params:
            if p.name in ("PROPERTY:PROPERTY_TYPE", "PROPERTY_TYPE") and not declaration:
                declaration = p.value.strip().lower()
            elif (
                p.name in ("REPOSITORY_PROPERTY_TYPE", "PROPERTY:REPOSITORY_PROPERTY_TYPE")
                and not repository_property_ref
            ):
                # Value is an xmi:id — resolve to connection name
                repository_property_ref = p.value
                resolved = self._xmi_id_index.get(p.value)
                if resolved:
                    conn_ref = resolved.name

        comp = Component(
            unique_name=unique_name,
            component_type=comp_type,
            params=params,
            metadata_connection_ref=conn_ref,
            schemas=self._parse_component_schemas(schema_elements, params),
            active=_component_is_active(params),
            declaration=(
                declaration if declaration in ("built_in", "repository") else ""
            ),
            repository_property_ref=repository_property_ref,
        )
        # Surface custom code (tJava/tJavaRow/tJavaFlex) as a top-level
        # field so downstream consumers don't have to know param layout.
        comp.code = comp.derive_code_from_params()
        job.components.append(comp)

        # Extract tRunJob references
        if comp_type == "tRunJob":
            self._extract_trunjob_ref(params, job, unique_name, item_file)
        elif comp_type == "cTalendJob":
            self._extract_ctalendjob_ref(params, job, unique_name, item_file)
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

    def _parse_component_schemas(
        self, schema_elements: list[ET.Element], params: list[ComponentParam],
    ) -> list[ComponentSchema]:
        """Parse every <metadata> row schema carried by one component.

        Talend states the schema's origin in a separate parameter pair
        (``SCHEMA:SCHEMA_TYPE`` + ``SCHEMA:REPOSITORY_SCHEMA_TYPE``, and the
        same pair under a connector-specific prefix such as
        ``SCHEMA_REJECT``). The pair is bound to its connector by that
        prefix; when no prefix matches, the declaration stays ``unknown``
        rather than being taken from an unrelated connector -- except for
        the single-schema/single-declaration case, where there is only one
        thing it can mean.
        """
        declarations = _schema_declarations(params)
        schemas: list[ComponentSchema] = []
        for elem in schema_elements:
            connector = elem.get("connector", "")
            columns: list[ComponentSchemaColumn] = []
            dynamic = False
            for column in elem:
                if _local_tag(column.tag) != "column":
                    continue
                column_type = column.get("type", "")
                # An id_Dynamic column stands for an unknown number of real
                # columns: the schema is no longer an enumerated field list.
                column_dynamic = column_type.strip().casefold() == "id_dynamic"
                dynamic = dynamic or column_dynamic
                columns.append(ComponentSchemaColumn(
                    name=column.get("name", ""),
                    type=column_type,
                    nullable=column.get("nullable", "true").strip().casefold() == "true",
                    key=column.get("key", "false").strip().casefold() == "true",
                    length=column.get("length", ""),
                    precision=column.get("precision", ""),
                    default_value=self.cred_filter.normalize_redaction_tokens(
                        column.get("defaultValue", ""),
                    ),
                    pattern=self.cred_filter.normalize_redaction_tokens(
                        column.get("pattern", ""),
                    ),
                    comment=self.cred_filter.normalize_redaction_tokens(
                        column.get("comment", ""),
                    ),
                    source_type=column.get("sourceType", ""),
                    dynamic=column_dynamic,
                ))
            declaration, repository_ref = _bind_schema_declaration(
                declarations, connector, len(schema_elements),
            )
            schemas.append(ComponentSchema(
                connector=connector,
                name=elem.get("name", ""),
                columns=columns,
                dynamic=dynamic,
                declaration=declaration,
                repository_schema_ref=repository_ref,
            ))
        return schemas

    def _parse_mapper_data(self, node_data: ET.Element) -> TMapData:
        """Parse a TalendMapper:MapperData nodeData element."""
        tmap = TMapData()

        for child in node_data:
            tag = _local_tag(child.tag)
            if tag in {"inputTables", "outputTables", "varTables"}:
                tmap.table_details.append({
                    "kind": tag, "attributes": dict(child.attrib),
                    "entries": [dict(entry.attrib) for entry in child if _local_tag(entry.tag) == "mapperTableEntries"],
                })

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

    def _parse_xml_map_data(self, node_data: ET.Element, *, node_index: int | None = None) -> TXMLMapData:
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
        ``raw_connections``. Supported local references are additionally
        resolved into typed pairs; unsupported forms stay explicit and cause
        the caller to emit a ``ScanWarning``.
        """
        txmap = TXMLMapData()
        # Only resolve structural EMF indices relative to this exact node.
        # Cross-node/resource proxies and unknown features remain explicit.
        def resolve(ref: str) -> dict[str, str] | None:
            match = re.fullmatch(r"//@node\.(\d+)/@nodeData(?:\.0)?/(.+)", ref)
            if not match or node_index is None or int(match.group(1)) != node_index:
                return None
            current = node_data
            table = ""
            for index, segment in enumerate(match.group(2).split("/")):
                feature = re.fullmatch(r"@(inputTrees|outputTrees|nodes|children|varTables|mapperTableEntries)\.(\d+)", segment)
                if not feature:
                    return None
                children = [e for e in current if _local_tag(e.tag) == feature.group(1)]
                ordinal = int(feature.group(2))
                if ordinal >= len(children):
                    return None
                current = children[ordinal]
                if index == 0:
                    table = current.get("name", "")
            if _local_tag(current.tag) not in {"nodes", "children", "mapperTableEntries"}:
                return None
            return {"table": table, "field": current.get("name", ""), "xpath": current.get("xpath", ""), "locator": ref}

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
                    source_field, target_field = resolve(source), resolve(target)
                    if source_field is not None and target_field is not None:
                        txmap.resolved_connections.append({
                            **{f"source_{k}": v for k, v in source_field.items()},
                            **{f"target_{k}": v for k, v in target_field.items()},
                            "resolution": "exact_local_emf",
                        })
                    else:
                        txmap.unresolved_connections.append({"source": source, "target": target, "reason": "unsupported_or_out_of_range_emf_reference"})

        return txmap

    # Talend serializes a design-to-design invocation under a parameter
    # prefix that varies by component (tRunJob: PROCESS, cTalendJob:
    # SELECTED_JOB_NAME, older exports: JOB_NAME) but with identical
    # ":PROCESS_TYPE_*" sub-parameters. One resolver serves every caller so
    # the identity-first rule cannot be implemented twice, differently.
    _PROCESS_REFERENCE_PREFIXES = ("PROCESS", "SELECTED_JOB_NAME", "JOB_NAME")

    def _resolve_process_reference(
        self,
        params: list[ComponentParam],
        job: ParsedJob,
        *,
        component_name: str,
        item_file: Path | None,
    ) -> dict | None:
        """Resolve a design reference IDENTITY-FIRST, label second.

        ``PROCESS`` / ``SELECTED_JOB_NAME`` is a Studio label CACHE: it
        holds whatever the child job was called when the caller was last
        saved, and Talend does not rewrite it when the child is renamed.
        ``*:PROCESS_TYPE_PROCESS`` is the child's repository GUID and is
        stable. Preferring the label -- which is what this parser used to do
        -- makes a renamed child resolve to nothing, or worse, to a
        different job that happens to now carry the stale name. When the two
        disagree the GUID wins and the disagreement is reported as a
        ``stale_label`` warning rather than silently normalized away.
        """
        by_name = {param.name: param for param in params}
        for prefix in self._PROCESS_REFERENCE_PREFIXES:
            guid_param = by_name.get(f"{prefix}:PROCESS_TYPE_PROCESS")
            label_param = by_name.get(prefix)
            if guid_param is None and label_param is None:
                continue
            guid = guid_param.value.strip() if guid_param is not None else ""
            label = label_param.value.strip() if label_param is not None else ""
            identity: tuple[str, str] | None = None
            for candidate in _identity_candidates(guid):
                identity = self._job_id_index.get(candidate)
                if identity is not None:
                    break
            if identity is not None:
                target, item_path, source = identity[0], identity[1], "identity"
                if label and label != target:
                    self._warn(
                        kind="stale_label",
                        file=item_file if item_file is not None else Path(job.path),
                        message=(
                            f"'{job.name}' component '{component_name}' caches "
                            f"child label '{label}', but repository identity "
                            f"{guid or '(none)'} resolves to '{target}' "
                            f"({item_path}). The identity is authoritative; "
                            "the cached label is stale."
                        ),
                        phase="artifact_reference",
                    )
            elif label:
                target, item_path, source = label, "", "label"
            else:
                target, item_path, source = guid, "", "none"
            return {
                "prefix": prefix,
                "guid": guid,
                "label": label,
                "target": target,
                "item_path": item_path,
                "reference_source": source,
                "version": _param_value(by_name, f"{prefix}:PROCESS_TYPE_VERSION"),
                "context_name": _param_value(by_name, f"{prefix}:PROCESS_TYPE_CONTEXT"),
                "context_params": _context_param_overrides(by_name.get("CONTEXTPARAMS")),
            }
        return None

    def _extract_trunjob_ref(
        self,
        params: list[ComponentParam],
        job: ParsedJob,
        component_name: str = "",
        item_file: Path | None = None,
    ) -> None:
        """Extract a tRunJob child-job reference and its call semantics."""
        reference = self._resolve_process_reference(
            params, job, component_name=component_name, item_file=item_file,
        )
        if reference is None:
            return
        by_name = {param.name: param for param in params}
        dynamic_job = _bool_param(by_name, "USE_DYNAMIC_JOB") is True
        if not reference["target"] and not dynamic_job:
            return
        job.trunjob_refs.append(TRunJobRef(
            child_job=reference["target"],
            child_job_xmi_id=reference["guid"],
            context_params=reference["context_params"],
            version=reference["version"],
            context_name=reference["context_name"],
            transmit_whole_context=_bool_param(by_name, "TRANSMIT_WHOLE_CONTEXT"),
            transmit_original_context=_bool_param(by_name, "TRANSMIT_ORIGINAL_CONTEXT"),
            die_on_child_error=_bool_param(by_name, "DIE_ON_CHILD_ERROR"),
            dynamic_job=dynamic_job,
            reference_source=reference["reference_source"],
            target_item_path=reference["item_path"],
            # USE_DYNAMIC_JOB picks the child at runtime, so there is no
            # static target to resolve and none must be invented.
            resolution="dynamic" if dynamic_job else "unresolved",
        ))

    def _extract_ctalendjob_ref(
        self,
        params: list[ComponentParam],
        job: ParsedJob,
        component_name: str,
        item_file: Path | None = None,
    ) -> None:
        """Extract a Route -> Job invocation without trusting a display label.

        Same identity-first rule as tRunJob (see
        ``_resolve_process_reference``); the two used to disagree, with the
        route path preferring the label outright.
        """
        reference = self._resolve_process_reference(
            params, job, component_name=component_name, item_file=item_file,
        )
        if reference is None or not (reference["target"] or reference["guid"]):
            return
        by_name = {param.name: param for param in params}
        job.artifact_refs.append(ArtifactReference(
            target_name=reference["target"] or reference["guid"],
            target_xmi_id=reference["guid"],
            relation_type="route_invokes_job",
            component_name=component_name,
            version=reference["version"],
            context_name=reference["context_name"],
            use_route_context=_bool_param(by_name, "USE_ROUTE_CONTEXT"),
            reference_source=reference["reference_source"],
            context_params=reference["context_params"],
            target_item_path=reference["item_path"],
        ))

    def _parse_connection(self, conn_elem: ET.Element, job: ParsedJob) -> None:
        """Parse a <connection> element, including what the edge itself declares.

        A DI row link states only its name. A Camel ``ROUTE_WHEN`` edge states
        the branch predicate (``CONDITION``), the language it is written in
        (``ROUTETYPE``: simple / spel / xpath / constant / javaScript / groovy
        / header), whether it terminates the choice (``ENDOFCHOICE``) and any
        XML namespaces the predicate binds (``NAMESPACES``, a TABLE). Those
        are ordinary element parameters and are read by the ordinary element
        parameter reader.
        """
        name = ""
        params: list[ComponentParam] = []
        for child in conn_elem:
            if _local_tag(child.tag) != "elementParameter":
                continue
            if child.get("name") == "UNIQUE_NAME":
                # Promoted to `name`, exactly as a node's UNIQUE_NAME is
                # promoted to `unique_name` and kept out of `params`.
                name = child.get("value", "")
                continue
            param = self._parse_element_param(child)
            if param is not None:
                params.append(param)

        conn = Connection(
            name=name or conn_elem.get("label", ""),
            source=conn_elem.get("source", ""),
            target=conn_elem.get("target", ""),
            connector_name=conn_elem.get("connectorName", ""),
            line_style=int(conn_elem.get("lineStyle", "0")),
            # ACTIVATE stays IN params and `active` is derived from it by the
            # same helper components use: absent means active, "false" means
            # disabled. One rule, one implementation.
            active=_component_is_active(params),
            params=params,
        )
        job.connections.append(conn)

    # ── Routines ─────────────────────────────────────────────────────

    # System routines shipped with Talend — no useful source to extract
    _SYSTEM_ROUTINES = frozenset({
        "DataMasking", "DataOperation", "Mathematical", "Numeric",
        "Relational", "StringHandling", "TalendDataGenerator", "TalendDate",
        "TalendString", "MDM",
    })

    def _parse_routines(self, talend_root: Path, *, source_root: str = ".") -> list[Routine]:
        """Collect routines from code/routines/ with source code."""
        routines_dir = child_dir(talend_root, "code", "routines", on_error=self._walk_error)
        if routines_dir is None:
            return []

        routines: list[Routine] = []
        for item_file in self._latest_item_files(routines_dir):
            match = re.match(r"(.+?)_(\d+(?:\.\d+)*)$", item_file.stem)
            props = self._read_properties_sidecar(item_file)
            name = props.get("label") or (match.group(1) if match else item_file.stem)
            is_system = name in self._SYSTEM_ROUTINES

            source_code = ""
            complete = False
            if not is_system:
                try:
                    raw = item_file.read_text(encoding="utf-8", errors="replace")
                    source_code = self.cred_filter.normalize_redaction_tokens(self.cred_filter.filter_text(raw))
                    complete = True
                except OSError as exc:
                    self._warn(kind="parse_error", file=item_file, message=f"Routine source read failed: {type(exc).__name__}", phase="routine")

            path = item_file.relative_to(talend_root).as_posix()
            logical_path = (item_file.relative_to(talend_root).parent / name).as_posix()
            routines.append(Routine(
                name=name,
                source_code=source_code,
                is_system=is_system,
                routine_id=f"{source_root}::{props.get('property_id') or logical_path}",
                source_root=source_root,
                path=path,
                version=props.get("version") or (match.group(2) if match else ""),
                property_id=props.get("property_id", ""),
                source_complete=complete,
                source_sha256=hashlib.sha256(source_code.encode("utf-8")).hexdigest() if complete else "",
            ))

        return routines

    # ── TDM Maps (Talend Data Mapper) ─────────────────────────────────
    #
    # A third, separate Talend mapping format (distinct from tMap and
    # tXMLMap above): standalone `Maps/*.xml` artifacts used by ESB/API
    # Services projects to define request/response transformations outside
    # any single job's inline components. See models.TdmMap docstring.

    # Storage preserves the complete supported tree. Consumers paginate their
    # reads; an explicit deployment override still produces the existing gap.
    _MAX_TDM_FIELDS: int | None = None

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
        maps_dir = child_dir(talend_root, "Maps", on_error=self._walk_error)
        if maps_dir is None:
            self._warn_case_variant(talend_root, "Maps", phase="tdm_map")
            return []

        jobs_by_name = {job.name: job for job in root_jobs if job.artifact_kind == "job"}
        routes_by_name = {job.name: job for job in root_jobs if job.artifact_kind == "route"}

        results: list[TdmMap] = []
        for xml_file in sorted(
            walk_files(maps_dir, patterns=("*.xml",), on_error=self._walk_error)
        ):
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
                if self._MAX_TDM_FIELDS is not None and len(fields) >= self._MAX_TDM_FIELDS:
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
                if ref.resolution == "dynamic":
                    # The child is selected at runtime; there is no static
                    # target and none may be invented from the label cache.
                    continue
                same_root = TalendParser._narrow_by_item_path(
                    by_root_name.get((job.source_root, ref.child_job), []),
                    ref.target_item_path,
                )
                global_matches = TalendParser._narrow_by_item_path(
                    by_name.get(ref.child_job, []), ref.target_item_path,
                )
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
                candidates = TalendParser._narrow_by_item_path([
                    item for item in by_root_name.get((job.source_root, ref.target_name), [])
                    if item.artifact_kind == target_kind
                ], ref.target_item_path)
                global_candidates = TalendParser._narrow_by_item_path([
                    item for item in by_name.get(ref.target_name, [])
                    if item.artifact_kind == target_kind
                ], ref.target_item_path)
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
    def _narrow_by_item_path(
        candidates: list[ParsedJob], item_path: str,
    ) -> list[ParsedJob]:
        """Break same-name ambiguity with the repository path the GUID named.

        Two jobs in one root can legitimately share a display name (Talend
        keys designs by path, not label). A label-only resolver reports that
        as ``ambiguous`` forever; the GUID already told us which file it is.
        Narrowing is applied only when it yields exactly one candidate --
        never to pick a "best" match.
        """
        if len(candidates) <= 1 or not item_path:
            return candidates
        exact = [job for job in candidates if _job_path_key(job) == item_path]
        return exact if len(exact) == 1 else candidates

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
        for routine in routines:
            if routine.routine_id:
                m.routine_metadata[routine.routine_id] = routine.model_dump(exclude={"source_code"})
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
                candidates = [r for r in routines if r.name == rname and r.source_root == job.source_root]
                # Support roots are not assumed to be shared libraries merely
                # because a name matches. No cross-root fallback can bind the
                # wrong implementation to a caller.
                if len(candidates) == 1 and candidates[0].routine_id:
                    routine = candidates[0]
                    m.job_routine_links.setdefault(job_key, []).append({
                        "routine_id": routine.routine_id, "name": rname,
                        "resolution": "same_root", "path": routine.path,
                        "source_root": routine.source_root,
                    })
                else:
                    m.unresolved_routine_refs.setdefault(job_key, []).append({
                        "name": rname,
                        "resolution": "ambiguous" if candidates else "unresolved",
                        "candidates": [r.routine_id for r in candidates],
                    })

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
                            # Additive: whether the destination was a literal
                            # name or a runtime expression, captured before
                            # `channel` collapsed a dynamic one.
                            "channel_binding": classify_channel_binding(
                                _find_route_channel_value(comp.params) or "",
                            ),
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
    """A Talend root has a process/ (Job Designs) and/or routes/ (Route Designs) dir.

    The names must match EXACTLY. macOS and Windows mounts are case-insensitive,
    so the old ``(candidate / "process").is_dir()`` was true for any customer
    folder called ``Process`` -- which is how a data directory holding
    ``Process/Processed`` was registered as a Talend execution root on a real
    upload, and then walked as though it held Job Designs.
    """
    return (
        child_dir(candidate, "process") is not None
        or child_dir(candidate, "routes") is not None
    )


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
        p for p in walk_dirs(repo_root)
        if "__MACOSX" not in p.relative_to(repo_root).parts
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
            child_file(candidate, "talend.project") or child_file(candidate, ".project")
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
        for descriptor in walk_files(repo_root, patterns=("talend.project",))
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


def _support_artifact_owner(
    repo_root: Path, talend_roots: Sequence[Path],
) -> Callable[[Path], tuple[str, str]]:
    """Build the ``(source_root, path)`` attribution for a support artifact.

    Inside a Talend project the answer is that project, so a stylesheet that
    always parsed keeps exactly the project-relative path it has always had.
    Outside one -- the separately uploaded mapping folder that holds most of
    an estate's stylesheets -- the owner is the largest ancestor directory
    that contains no Talend project at all, which is the upload as a human
    named it (``ETTM mappings-2``, not ``.``). Both forms join back to the
    file inventory's bundle-relative path, so the parse accounting still
    resolves.
    """

    def owner(path: Path) -> Path:
        enclosing = [root for root in talend_roots if path.is_relative_to(root)]
        if enclosing:
            return max(enclosing, key=lambda root: len(root.parts))
        area = repo_root
        for part in path.relative_to(repo_root).parts[:-1]:
            area = area / part
            if not any(root.is_relative_to(area) for root in talend_roots):
                return area
        # Every ancestor holds a project: the file sits on the bundle's
        # spine and belongs to no area smaller than its own directory.
        return area

    def attribute(path: Path) -> tuple[str, str]:
        area = owner(path)
        return (
            area.relative_to(repo_root).as_posix() or ".",
            path.relative_to(area).as_posix(),
        )

    return attribute


def _has_supported_talend_artifacts(candidate: Path) -> bool:
    """Whether a descriptor-backed project contains scanner-supported artifacts."""
    return any((
        child_dir(candidate, "context") is not None,
        child_dir(candidate, "metadata", "connections") is not None,
        child_dir(candidate, "code", "routines") is not None,
    ))


def _qualified_job_id(source_root: str, discriminator: str) -> str:
    """URL-safe bounded identity for a colliding Talend job."""
    raw = f"{source_root}::{discriminator}"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_") or "job"
    return f"{slug[:56].rstrip('_') or 'job'}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


# Warning kinds that mean "this file was not fully extracted". Any other
# kind (stale_label, unscanned_category) is a finding ABOUT the scan, not a
# statement that the named file failed to parse.
_PARTIAL_OUTCOME_WARNINGS: frozenset[str] = frozenset({
    "parse_error", "partial_parse", "missing_file", "unknown",
})


# ── Component parameter / schema helpers ─────────────────────────────

_VERSION_SUFFIX_RE = re.compile(r"(.+?)_(\d+(?:\.\d+)*)$")

# SCHEMA:SCHEMA_TYPE, SCHEMA_REJECT:SCHEMA_TYPE, ... -- the prefix names the
# connector the declaration belongs to.
_SCHEMA_TYPE_RE = re.compile(r"^(SCHEMA(?:_[A-Za-z0-9_]+)?):SCHEMA_TYPE$")
_SCHEMA_REPOSITORY_RE = re.compile(
    r"^(SCHEMA(?:_[A-Za-z0-9_]+)?):REPOSITORY_SCHEMA_TYPE$"
)


def _strip_version_suffix(stem: str) -> str:
    """'Orders_Load_3.2' -> 'Orders_Load'. A version is not identity."""
    match = _VERSION_SUFFIX_RE.match(stem)
    return match.group(1) if match else stem


def _artifact_path_key(design_dir: str, relative_path: str) -> str:
    """Version-stripped repository path of one design, e.g.
    ``process/Orders/Orders_Load``.

    Used to compare a GUID-resolved target against a parsed job when their
    display names collide. The export version is stripped because the
    ``.properties`` sidecar and the selected ``.item`` need not be the same
    saved version.
    """
    path = PurePosixPath(relative_path)
    stem = _strip_version_suffix(path.stem)
    parent = path.parent.as_posix()
    return f"{design_dir}/{parent}/{stem}" if parent not in ("", ".") else f"{design_dir}/{stem}"


def _job_path_key(job: ParsedJob) -> str:
    return _artifact_path_key(job.source_design or "process", job.path)


def _identity_candidates(raw: str) -> list[str]:
    """Every spelling one serialized repository GUID may take.

    Talend writes either the bare id or ``PROJECT:_id``, and the leading
    underscore is present in some exports and not others.
    """
    if not raw:
        return []
    tail = raw.split(":")[-1]
    ordered = [raw, raw.lstrip("_"), tail, tail.lstrip("_")]
    return [candidate for candidate in dict.fromkeys(ordered) if candidate]


def _param_value(by_name: dict[str, ComponentParam], name: str) -> str:
    param = by_name.get(name)
    return param.value.strip() if param is not None else ""


def _bool_param(by_name: dict[str, ComponentParam], name: str) -> bool | None:
    """Tri-state parameter read: absent stays unknown, never False.

    "The export did not say" and "the export said false" are different
    facts about a job's behaviour.
    """
    param = by_name.get(name)
    if param is None:
        return None
    normalized = param.value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _parse_table_param_rows(
    param_elem: ET.Element, cred_filter: CredentialFilter,
) -> list[dict[str, str]]:
    """Group a TABLE parameter's <elementValue> children into rows.

    Talend serializes a table as one flat run of ``elementValue`` entries
    (``PARAM_NAME_COLUMN``, ``PARAM_VALUE_COLUMN``, ``PARAM_NAME_COLUMN``,
    ...), so a new row starts each time the first column key reappears.
    """
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    first_key = ""
    for child in param_elem:
        if _local_tag(child.tag) != "elementValue":
            continue
        key = child.get("elementRef", "")
        if not key:
            continue
        if not first_key:
            first_key = key
        elif key == first_key and current:
            rows.append(current)
            current = {}
        current[key] = cred_filter.normalize_redaction_tokens(child.get("value", ""))
    if current:
        rows.append(current)
    return rows


def _single_row_descriptor(
    param_elem: ET.Element, cred_filter: CredentialFilter,
) -> RedactionDescriptor | None:
    """Safe structural facts for a TABLE row URI, only when unambiguous."""
    descriptors: dict[str, RedactionDescriptor] = {}
    for child in param_elem:
        if _local_tag(child.tag) != "elementValue":
            continue
        descriptor = cred_filter.descriptor_from_redaction_token(child.get("value", ""))
        if descriptor is not None:
            descriptors[descriptor.model_dump_json(exclude_none=True)] = descriptor
    return next(iter(descriptors.values())) if len(descriptors) == 1 else None


def _component_is_active(params: list[ComponentParam]) -> bool:
    """Whether a component runs.

    Talend omits ACTIVATE entirely on an active component and serializes
    ``value="false"`` on a disabled one, so absence means active. (Joblet
    embeds carry an explicit ``true``.)
    """
    return not any(
        param.name == "ACTIVATE" and param.value.strip().casefold() == "false"
        for param in params
    )


def _schema_declarations(
    params: list[ComponentParam],
) -> dict[str, tuple[str, str]]:
    """prefix -> (declaration, repository schema id) from parameter pairs."""
    declarations: dict[str, str] = {}
    repositories: dict[str, str] = {}
    for param in params:
        type_match = _SCHEMA_TYPE_RE.match(param.name)
        if type_match:
            declarations[type_match.group(1)] = param.value.strip().lower()
            continue
        repository_match = _SCHEMA_REPOSITORY_RE.match(param.name)
        if repository_match:
            repositories[repository_match.group(1)] = param.value.strip()
    return {
        prefix: (declaration, repositories.get(prefix, ""))
        for prefix, declaration in declarations.items()
    }


def _bind_schema_declaration(
    declarations: dict[str, tuple[str, str]], connector: str, schema_count: int,
) -> tuple[str, str]:
    """Bind one <metadata> connector to its SCHEMA*:SCHEMA_TYPE declaration.

    The main flow uses the bare ``SCHEMA`` prefix; every other connector
    uses ``SCHEMA_<CONNECTOR>``. When neither matches, the declaration is
    reported ``unknown`` instead of being borrowed from another connector --
    except when the component has exactly one schema and exactly one
    declaration, where there is only one thing it can mean.
    """
    prefix = "SCHEMA" if connector.upper() in ("", "FLOW") else f"SCHEMA_{connector.upper()}"
    entry = declarations.get(prefix)
    if entry is None and len(declarations) == 1 and schema_count == 1:
        entry = next(iter(declarations.values()))
    if entry is None:
        return "unknown", ""
    declaration, repository_ref = entry
    if declaration == "repository":
        return "repository", repository_ref
    if declaration == "built_in":
        return "built_in", repository_ref
    return "unknown", repository_ref


def _context_param_overrides(param: ComponentParam | None) -> list[ComponentParam]:
    """CONTEXTPARAMS rows as name/value overrides passed to a child design.

    These are the only record of what a parent actually hands its child, and
    the cross-job lineage stitcher already looks for them
    (``lineage._stitch_trunjob_context_params``) -- it just never found any,
    because the TABLE parameter carrying them was dropped at parse time.
    """
    if param is None or not param.rows:
        return []
    overrides: list[ComponentParam] = []
    for row in param.rows:
        name = ComponentParam.row_column(row, "NAME")
        value = ComponentParam.row_column(row, "VALUE")
        if not name and not value:
            continue
        overrides.append(ComponentParam(
            name=name, value=value, field_type="CONTEXTPARAM",
        ))
    return overrides


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


# Markers that make a destination/URI a runtime value rather than a name:
# a context variable, a globalMap lookup, or Java string concatenation.
_DYNAMIC_CHANNEL_MARKERS: tuple[str, ...] = ("context.", "globalmap", "+")


def classify_channel_binding(value: str) -> str:
    """``literal`` | ``dynamic`` | ``absent`` for a channel/endpoint value."""
    text = value.strip()
    if not text:
        return "absent"
    lowered = text.casefold()
    if any(marker in lowered for marker in _DYNAMIC_CHANNEL_MARKERS):
        return "dynamic"
    return "literal"


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
    if classify_channel_binding(value) == "dynamic":
        # `context.QUEUE` is not a channel name: two routes carrying that
        # same expression resolve to different queues per environment.
        # Returning the placeholder keeps both the manifest connection node
        # and the landscape channel identity from fabricating a shared
        # dependency -- the same rule `_has_safe_connection_discriminator`
        # applies to repository connections.
        return "configured channel"
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
