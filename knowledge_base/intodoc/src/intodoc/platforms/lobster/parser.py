"""Lobster XML profile parser -- extracts structured data from DataWizard profiles.

Each Lobster profile is a self-contained <datawizardprofile> XML file containing:
- Agent (transport/trigger config: FTP, HTTP, Cron, Message, etc.)
- DataProperties (parser config: filetype, encoding, delimiters)
- Input/Output trees (hierarchical data schemas with field-level filters)
- Variables (system + user-defined context variables)
- Response units (output destinations: FTP, HTTP, file, email, etc.)
- Integration settings (output encoding, format)
- Cross-profile references (profile_after_run, exception_profile)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from intodoc.models import (
    Component,
    ComponentParam,
    ContextGroup,
    ContextVariable,
    FileType,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    ScanResult,
    TRunJobRef,
)
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

console = Console()

# ── Agent type resolution ────────────────────────────────────────────

_AGENT_CLASS_MAP: dict[str, str] = {
    "com.ebd.hub.datawizard.data.HttpSettings": "HTTP",
    "com.ebd.hub.datawizard.data.CronSettings": "Cron",
    "com.ebd.hub.datawizard.data.MessageSettings": "Message",
    "com.ebd.hub.datawizard.data.PortalAgentSettings": "Portal",
}

# Cron sub-types resolved from <type> element
_CRON_TYPE_MAP: dict[str, str] = {
    "Cron:Ftp": "FTP_Cron",
    "Cron:DB": "DB_Cron",
    "Cron:File": "File_Cron",
    "Cron:Http": "HTTP_Cron",
    "Man. upload": "Manual",
}

# Node/field attributes that are always default-valued (skip during extraction)
_NODE_DEFAULTS: dict[str, str] = {
    "deleted": "false",
    "is_node": "false",
    "mandatory": "false",
    "rel_xpath": "true",
    "traverse_mode": "2",
    "new_record": "-1",
    "sqltype": "5",
    "sqlmode": "0",
    "sql_commit_mode": "0",
    "no_output": "false",
    "use_template_for_output_fields": "false",
    "autonumber": "false",
    "calcNode": "false",
    "log_field": "false",
}

# DataProperties keys to extract (skip noise like renderer classes)
_DATAPROP_KEYS: frozenset[str] = frozenset({
    "filetype", "file_encoding", "xml_recordtag", "delim_row", "delim_column",
    "start_row", "end_row", "max_records", "use_xpath", "ignore_namespace",
    "empty_value_is_null", "trim_sql_values", "source_sql",
})


class LobsterParser:
    """Parse Lobster/DataWizard XML profile exports into intodoc models."""

    def __init__(self, cred_filter: CredentialFilter) -> None:
        self.cred_filter = cred_filter

    # ── Main entry ───────────────────────────────────────────────────

    def parse(self, repo_root: Path) -> ScanResult:
        """Parse all Lobster profiles under repo_root into a ScanResult."""
        from intodoc.platforms.lobster import LobsterPlugin

        plugin = LobsterPlugin()
        all_files = walk_repo(repo_root, plugin)
        classified_files = [f for f in all_files if f.file_type != FileType.UNKNOWN]

        profiles = self._find_profiles(repo_root)
        jobs: list[ParsedJob] = []
        all_connections: dict[str, MetadataConnection] = {}
        all_context_groups: list[ContextGroup] = []
        cross_refs: dict[str, list[str]] = {}  # job_name -> [called profiles]

        for profile_path in sorted(profiles):
            try:
                job, conns, ctx_groups, refs = self._parse_profile(profile_path, repo_root)
                jobs.append(job)
                for conn in conns:
                    key = (conn.additional_params.get("partner", ""), conn.additional_params.get("channel", ""))
                    conn_key = f"{key[0]}:{key[1]}" if key[0] else conn.name
                    if conn_key not in all_connections:
                        all_connections[conn_key] = conn
                all_context_groups.extend(ctx_groups)
                if refs:
                    cross_refs[job.name] = refs
            except (ET.ParseError, OSError) as e:
                console.print(f"  [yellow]Warning: Failed to parse {profile_path}: {e}[/yellow]")

        manifest = self._build_manifest(jobs, cross_refs)

        stats = {
            "total_files": len(all_files),
            "classified_files": len(classified_files),
            "profiles": len(profiles),
            "jobs": len(jobs),
            "connections": len(all_connections),
            "context_groups": len(all_context_groups),
        }

        console.print(
            f"  Lobster parser: {len(jobs)} profiles, "
            f"{len(all_connections)} connections"
        )

        return ScanResult(
            platform="lobster",
            repo_root=str(repo_root),
            files=classified_files,
            jobs=jobs,
            metadata_connections=list(all_connections.values()),
            context_groups=all_context_groups,
            manifest=manifest,
            stats=stats,
        )

    # ── Profile discovery ────────────────────────────────────────────

    def _find_profiles(self, repo_root: Path) -> list[Path]:
        """Find all XML files with <datawizardprofile> root element."""
        profiles: list[Path] = []
        for xml_path in repo_root.rglob("*.xml"):
            try:
                # Quick check: read first 500 bytes for root tag
                with xml_path.open("r", encoding="utf-8", errors="replace") as f:
                    head = f.read(500)
                if "<datawizardprofile" in head:
                    profiles.append(xml_path)
            except OSError:
                continue
        return profiles

    # ── Single profile parsing ───────────────────────────────────────

    def _parse_profile(
        self, path: Path, repo_root: Path,
    ) -> tuple[ParsedJob, list[MetadataConnection], list[ContextGroup], list[str]]:
        """Parse a single profile XML into a ParsedJob + connections + context."""
        text = self._read_filtered(path)
        root = ET.fromstring(text)

        name = root.get("name", path.stem)
        profile_type = self._text(root, "type") or "Unknown"
        group = self._text(root, "path_of_group") or "root"
        description = self._text(root, "description") or ""
        version = self._text(root, "version") or ""
        created_at = self._text(root, "created_at") or ""
        active = self._text(root, "active") or "false"

        components: list[Component] = []
        connections: list[MetadataConnection] = []
        cross_refs: list[str] = []

        # 1. Agent component
        agent_el = root.find("agent")
        if agent_el is not None:
            agent_comp, agent_conns, agent_refs = self._parse_agent(agent_el, profile_type)
            components.append(agent_comp)
            connections.extend(agent_conns)
            cross_refs.extend(agent_refs)

        # 2. DataProperties component
        dp_el = root.find("dataproperties")
        if dp_el is not None:
            dp_comp = self._parse_dataproperties(dp_el)
            components.append(dp_comp)

        # 3. Structure definition (input/output trees, filters, variables)
        sd_el = dp_el.find("structuredefinition") if dp_el is not None else None
        context_groups: list[ContextGroup] = []

        if sd_el is not None:
            # Input tree
            it_el = sd_el.find("inputtree")
            if it_el is not None:
                it_comp = self._parse_tree(it_el, "LobsterSourceSchema", "input")
                components.append(it_comp)

            # Output tree (includes filter chains)
            ot_el = sd_el.find("outputtree")
            if ot_el is not None:
                ot_comp = self._parse_tree(ot_el, "LobsterMapping", "output")
                components.append(ot_comp)

            # Variables
            vars_el = sd_el.find("variables")
            if vars_el is not None:
                ctx_groups = self._parse_variables(vars_el)
                context_groups.extend(ctx_groups)

        # 4. Integration settings
        is_el = root.find("integrationsettings")
        if is_el is not None:
            is_comp = self._parse_integration_settings(is_el)
            if is_comp.params:
                components.append(is_comp)

        # 5. Response units
        rs_el = root.find("responsesettings")
        if rs_el is not None:
            ru_comps, ru_conns = self._parse_response_settings(rs_el)
            components.extend(ru_comps)
            connections.extend(ru_conns)

        # 6. Cross-profile references at root level
        for ref_tag in ("exception_profile", "forward_profile", "replace_profile"):
            ref_val = self._text(root, ref_tag)
            if ref_val:
                cross_refs.append(ref_val)

        # Build TRunJobRef from cross-profile references
        trunjob_refs = [TRunJobRef(child_job=ref) for ref in cross_refs]

        try:
            rel_path = str(path.relative_to(repo_root))
        except ValueError:
            rel_path = path.name

        job = ParsedJob(
            name=name,
            path=rel_path,
            purpose=description or f"{profile_type} profile",
            version=version,
            creation_date=created_at,
            components=components,
            context_groups=context_groups,
            trunjob_refs=trunjob_refs,
            group=group,
        )

        return job, connections, context_groups, cross_refs

    # ── Agent parsing ────────────────────────────────────────────────

    def _parse_agent(
        self, agent_el: ET.Element, profile_type: str,
    ) -> tuple[Component, list[MetadataConnection], list[str]]:
        """Parse <agent> element into a component + connections + cross-refs."""
        agent_class = self._text(agent_el, "agent_class") or ""
        base_type = _AGENT_CLASS_MAP.get(agent_class, "")

        # Resolve Cron sub-types
        if base_type == "Cron":
            agent_type = _CRON_TYPE_MAP.get(profile_type, f"Cron_{profile_type}")
        elif base_type:
            agent_type = base_type
        elif profile_type in _CRON_TYPE_MAP:
            agent_type = _CRON_TYPE_MAP[profile_type]
        else:
            agent_type = "Manual"

        comp_type = f"LobsterInputAgent_{agent_type}"

        # Extract meaningful params
        params: list[ComponentParam] = []
        _AGENT_KEYS = (
            "url", "host", "port", "dir", "filename", "mimetype", "mime_type",
            "schedule_type", "interval", "ftp_client_type", "file_handling",
            "http_method", "auth_type", "content_type", "service",
            "accept_get", "accept_post", "accept_put", "accept_delete",
            "accept_patch", "accept_all_subURI", "rest_placeholder",
            "com_channel", "comm_channel_partner", "comm_channel_name",
        )
        for key in _AGENT_KEYS:
            val = self._text(agent_el, key)
            if val:
                params.append(ComponentParam(name=key, value=val))

        # Cross-profile reference
        cross_refs: list[str] = []
        par = self._text(agent_el, "profile_after_run")
        if par:
            params.append(ComponentParam(name="profile_after_run", value=par))
            cross_refs.append(par)

        # Build connection from comm channel info
        connections: list[MetadataConnection] = []
        partner = self._text(agent_el, "comm_channel_partner") or ""
        channel = self._text(agent_el, "comm_channel_name") or ""
        host = self._text(agent_el, "host") or ""
        port = self._text(agent_el, "port") or ""
        if partner or host:
            connections.append(MetadataConnection(
                xmi_id=self._text(agent_el, "com_channel") or f"agent_{partner}_{channel}",
                name=f"{partner}:{channel}" if partner else f"agent_{host}:{port}",
                connection_type=agent_type,
                host=host,
                port=port,
                additional_params={"partner": partner, "channel": channel},
            ))

        comp = Component(
            unique_name="agent",
            component_type=comp_type,
            params=params,
        )
        return comp, connections, cross_refs

    # ── DataProperties parsing ───────────────────────────────────────

    def _parse_dataproperties(self, dp_el: ET.Element) -> Component:
        """Parse <dataproperties> into a parser component."""
        filetype_code = self._text(dp_el, "filetype") or "0"
        filetype_name = {
            "0": "CSV", "1": "XML", "2": "Fixed", "3": "EDI",
            "4": "Excel", "5": "IDoc", "6": "Swift", "10": "JSON",
        }.get(filetype_code, f"type_{filetype_code}")

        params: list[ComponentParam] = []
        for key in _DATAPROP_KEYS:
            val = self._text(dp_el, key)
            if val and val not in ("2147483647", "0", "false", ""):
                params.append(ComponentParam(name=key, value=val))

        return Component(
            unique_name="parser",
            component_type=f"LobsterParser_{filetype_name}",
            params=params,
        )

    # ── Tree parsing (input + output) ────────────────────────────────

    def _parse_tree(
        self, tree_el: ET.Element, comp_type: str, direction: str,
    ) -> Component:
        """Parse <inputtree> or <outputtree> into a compact component."""
        params: list[ComponentParam] = []

        # Compact tree representation
        tree_repr = self._compact_tree(tree_el)
        if tree_repr:
            params.append(ComponentParam(
                name=f"{direction}_tree", value=tree_repr, field_type="INPUT",
            ))

        # Extract filter chains from output tree fields
        if direction == "output":
            filter_params = self._extract_filters(tree_el)
            params.extend(filter_params)

        return Component(
            unique_name=direction,
            component_type=comp_type,
            params=params,
        )

    def _compact_tree(self, tree_el: ET.Element, depth: int = 0) -> str:
        """Recursively build compact tree representation."""
        lines: list[str] = []

        for node_el in tree_el.findall("node"):
            name = node_el.get("name", "")
            is_node = node_el.get("is_node", "false") == "true"
            indent = "  " * depth

            # Only include meaningful attributes (skip defaults)
            attrs: list[str] = []
            xpath = node_el.get("xpath", "") or node_el.get("xpathexpression", "")
            if xpath:
                attrs.append(f"xpath={xpath}")
            sqltable = node_el.get("sqltable", "")
            if sqltable:
                attrs.append(f"sql={sqltable}")
            min_val = node_el.get("min", "0")
            max_val = node_el.get("max", "1")
            if min_val != "0" or max_val not in ("1", "2147483647", "99999"):
                attrs.append(f"[{min_val}..{max_val}]")
            mandatory = node_el.get("mandatory", "false")
            if mandatory == "true":
                attrs.append("required")

            attr_str = f" ({', '.join(attrs)})" if attrs else ""

            if is_node:
                lines.append(f"{indent}+ {name}{attr_str}")
                # Show fields directly under container nodes
                for field_el in node_el.findall("field"):
                    fname = field_el.get("name", "")
                    ftype = field_el.get("type", "String")
                    fmandatory = field_el.get("mandatory", "false") == "true"
                    sql_col = field_el.get("sql_column_name", "")
                    sql_key = field_el.get("sql_key", "false") == "true"
                    fix_val = field_el.get("fix_value", "")

                    fattrs: list[str] = [ftype]
                    if fmandatory:
                        fattrs.append("required")
                    if sql_col:
                        fattrs.append(f"col={sql_col}")
                    if sql_key:
                        fattrs.append("PK")
                    if fix_val:
                        fattrs.append(f"fix={fix_val}")

                    lines.append(f"{indent}  - {fname} ({', '.join(fattrs)})")
                sub = self._compact_tree(node_el, depth + 1)
                if sub:
                    lines.append(sub)
            else:
                # Leaf node -- show fields
                for field_el in node_el.findall("field"):
                    fname = field_el.get("name", "")
                    ftype = field_el.get("type", "String")
                    fmandatory = field_el.get("mandatory", "false") == "true"
                    sql_col = field_el.get("sql_column_name", "")
                    sql_key = field_el.get("sql_key", "false") == "true"
                    fix_val = field_el.get("fix_value", "")

                    fattrs: list[str] = [ftype]
                    if fmandatory:
                        fattrs.append("required")
                    if sql_col:
                        fattrs.append(f"col={sql_col}")
                    if sql_key:
                        fattrs.append("PK")
                    if fix_val:
                        fattrs.append(f"fix={fix_val}")

                    lines.append(f"{indent}  - {fname} ({', '.join(fattrs)})")

                # Also recurse into child nodes
                sub = self._compact_tree(node_el, depth + 1)
                if sub:
                    lines.append(sub)

        return "\n".join(lines)

    def _extract_filters(self, tree_el: ET.Element) -> list[ComponentParam]:
        """Extract all filter chains from an output tree."""
        params: list[ComponentParam] = []

        for filter_el in tree_el.iter("filter"):
            fid = filter_el.get("id", "")
            desc = filter_el.get("description", "")
            if not desc:
                continue

            # Build compact expression from filter parameters
            args: list[str] = []
            for param_el in filter_el:
                if param_el.tag in ("note", "aiDoc", "aiDocumentation"):
                    continue
                ptype = param_el.get("type", "")
                pfc = param_el.get("fieldconstant", "")
                value_text = param_el.findtext("value", "")

                # Skip empty parameter slots
                if ptype == "constant" and not pfc and not value_text:
                    continue

                if ptype == "field":
                    args.append(f"field:{pfc}")
                elif ptype == "variable":
                    args.append(f"var:{pfc}")
                elif ptype == "result":
                    args.append(f"result:{pfc}")
                elif ptype == "destination":
                    args.append(f"dest:{pfc}")
                elif ptype == "constant":
                    # Use fieldconstant as the display value (pre-encoding)
                    val = pfc if pfc else "(base64)"
                    args.append(f'const:"{val}"')
                elif ptype:
                    args.append(f"{ptype}:{pfc}")

            expr = f"{desc}({', '.join(args)})" if args else desc
            params.append(ComponentParam(
                name=f"filter_{fid}", value=expr, field_type="FILTER",
            ))

        return params

    # ── Variables parsing ────────────────────────────────────────────

    def _parse_variables(self, vars_el: ET.Element) -> list[ContextGroup]:
        """Parse <variables> into user and system context groups."""
        user_vars: list[ContextVariable] = []
        system_vars: list[ContextVariable] = []

        for var_el in vars_el.findall("variable"):
            key = var_el.findtext("key", "")
            if not key:
                continue

            description = var_el.findtext("description", "") or ""
            vtype = var_el.findtext("type", "0")
            value = var_el.findtext("value", "") or ""

            type_name = {"0": "String", "1": "Integer", "2": "Decimal",
                         "3": "Date", "4": "Boolean"}.get(vtype, "String")

            cv = ContextVariable(
                name=key,
                type=type_name,
                values={"_default": value} if value else {},
            )

            if self._is_system_var(key):
                system_vars.append(cv)
            else:
                user_vars.append(cv)

        groups: list[ContextGroup] = []
        if user_vars:
            groups.append(ContextGroup(name="user_variables", variables=user_vars))
        if system_vars:
            groups.append(ContextGroup(name="system_variables", variables=system_vars))
        return groups

    @staticmethod
    def _is_system_var(key: str) -> bool:
        """Check if a variable key is a system variable."""
        return (
            key.startswith("VAR_SYS_")
            or key.startswith("VAR_FILENAME")
            or key.startswith("VAR_ORIGINATOR")
            or key.startswith("MSG_CALL_")
            or key in (
                "VAR_MAILSUBJECT", "VAR_RECORDS", "VAR_FILESIZE",
                "VAR_AUTOSERIALIZE_DATA", "VAR_SYS_MESSAGE",
                "VAR_SYS_MESSAGE_ID",
            )
        )

    # ── Integration settings ─────────────────────────────────────────

    def _parse_integration_settings(self, is_el: ET.Element) -> Component:
        """Parse <integrationsettings> for output format config."""
        params: list[ComponentParam] = []

        encoding = self._text(is_el, "encoding")
        if encoding:
            params.append(ComponentParam(name="output_encoding", value=encoding))

        realm_el = is_el.find("realm")
        if realm_el is not None:
            for key in ("xml_root_node", "pretty_print", "xml_declaration"):
                val = self._text(realm_el, key)
                if val and val not in ("false", ""):
                    params.append(ComponentParam(name=key, value=val))

        return Component(
            unique_name="output_format",
            component_type="LobsterOutputFormat",
            params=params,
        )

    # ── Response settings ────────────────────────────────────────────

    def _parse_response_settings(
        self, rs_el: ET.Element,
    ) -> tuple[list[Component], list[MetadataConnection]]:
        """Parse <responsesettings> for response units."""
        components: list[Component] = []
        connections: list[MetadataConnection] = []

        units_el = rs_el.find("responseunits")
        if units_el is None:
            return components, connections

        for idx, unit_el in enumerate(units_el):
            tag = unit_el.tag  # e.g. unit_ftp, unit_mail, unit_http
            unit_type = tag.replace("unit_", "").upper()
            label = unit_el.findtext("label", "") or f"response_{idx}"
            active = unit_el.findtext("active", "true")
            description = unit_el.findtext("description", "") or ""

            params: list[ComponentParam] = []
            params.append(ComponentParam(name="type", value=unit_type))
            params.append(ComponentParam(name="label", value=label))
            if active != "true":
                params.append(ComponentParam(name="active", value=active))
            if description:
                params.append(ComponentParam(name="description", value=description))

            _UNIT_KEYS = (
                "host", "port", "dir", "filename", "encoding",
                "comm_channel_partner", "comm_channel_name",
                "comm_channel_id", "clienttype",
            )
            for key in _UNIT_KEYS:
                val = unit_el.findtext(key, "")
                if val:
                    params.append(ComponentParam(name=key, value=val))

            comp = Component(
                unique_name=f"response_{idx}_{label}",
                component_type=f"LobsterResponse_{unit_type}",
                params=params,
            )
            components.append(comp)

            # Build connection from response unit
            partner = unit_el.findtext("comm_channel_partner", "") or ""
            channel = unit_el.findtext("comm_channel_name", "") or ""
            host = unit_el.findtext("host", "") or ""
            port = unit_el.findtext("port", "") or ""
            if partner or host:
                connections.append(MetadataConnection(
                    xmi_id=unit_el.findtext("comm_channel_id", "") or f"ru_{idx}",
                    name=f"{partner}:{channel}" if partner else f"response_{host}:{port}",
                    connection_type=unit_type,
                    host=host,
                    port=port,
                    additional_params={"partner": partner, "channel": channel},
                ))

        return components, connections

    # ── Manifest building ────────────────────────────────────────────

    @staticmethod
    def _build_manifest(
        jobs: list[ParsedJob],
        cross_refs: dict[str, list[str]],
    ) -> ProjectManifest:
        """Build cross-reference manifest from parsed profiles."""
        job_calls: dict[str, list[str]] = {}
        job_called_by: dict[str, list[str]] = defaultdict(list)
        job_connections_used: dict[str, list[str]] = {}
        connection_used_by: dict[str, list[str]] = defaultdict(list)
        job_groups: dict[str, str] = {}
        job_external_systems: dict[str, list[str]] = {}

        for job in jobs:
            job_groups[job.name] = job.group

            # Cross-profile calls
            calls = cross_refs.get(job.name, [])
            if calls:
                job_calls[job.name] = calls
                for called in calls:
                    job_called_by[called].append(job.name)

            # Connection usage from components
            conn_names: set[str] = set()
            ext_systems: set[str] = set()
            for comp in job.components:
                ctype = comp.component_type
                if ctype.startswith("LobsterInputAgent_"):
                    ext_systems.add(ctype.replace("LobsterInputAgent_", ""))
                elif ctype.startswith("LobsterResponse_"):
                    ext_systems.add(ctype.replace("LobsterResponse_", ""))
                # Extract partner:channel from params
                partner = ""
                channel = ""
                for p in comp.params:
                    if p.name == "comm_channel_partner":
                        partner = p.value
                    elif p.name == "comm_channel_name":
                        channel = p.value
                if partner:
                    conn_names.add(f"{partner}:{channel}")

            if conn_names:
                job_connections_used[job.name] = sorted(conn_names)
                for cn in conn_names:
                    connection_used_by[cn].append(job.name)
            if ext_systems:
                job_external_systems[job.name] = sorted(ext_systems)

        return ProjectManifest(
            job_calls=job_calls,
            job_called_by=dict(job_called_by),
            job_connections_used=job_connections_used,
            connection_used_by=dict(connection_used_by),
            job_groups=job_groups,
            job_external_systems=job_external_systems,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _read_filtered(self, path: Path) -> str:
        """Read a file and apply credential filtering."""
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.cred_filter.filter_text(text)

    @staticmethod
    def _text(el: ET.Element, tag: str) -> str:
        """Get text content of a child element, or empty string."""
        child = el.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""
