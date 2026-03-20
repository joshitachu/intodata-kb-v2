"""Workato recipe parser -- extracts structured data from Workato export ZIPs."""

from __future__ import annotations

import json
import re
from collections import defaultdict
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
    TMapData,
    TMapEntry,
    TRunJobRef,
)
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

console = Console()

# Regex to extract datapill references from input field values
_DATAPILL_RE = re.compile(r"#\{_\('([^']+)'\)\}")
# New-format handlebars datapill syntax: {{trigger.Email}}, {{steps.foo.bar}}
_DATAPILL_NEW_RE = re.compile(r"\{\{([^}]+)\}\}")

# Map Workato provider names to standardized connection types
_PROVIDER_TYPE_MAP: dict[str, str] = {
    "salesforce": "Salesforce",
    "netsuite": "NetSuite",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "snowflake": "Snowflake",
    "redshift": "Redshift",
    "bigquery": "BigQuery",
    "s3": "S3",
    "gcs": "GCS",
    "http": "HTTP/REST",
    "workato_webhooks": "Webhook",
    "ftp": "FTP",
    "sftp": "SFTP",
    "smtp": "SMTP",
    "jira": "Jira",
    "zendesk": "Zendesk",
    "slack": "Slack",
    "teams": "MSTeams",
    "sap": "SAP",
    "oracle": "Oracle",
    "mssql": "MSSQL",
    "dynamodb": "DynamoDB",
    "mongodb": "MongoDB",
    "google_sheets": "GoogleSheets",
    "box": "Box",
    "dropbox": "Dropbox",
    "workday": "Workday",
    "hubspot": "HubSpot",
    "marketo": "Marketo",
    "stripe": "Stripe",
    "quickbooks": "QuickBooks",
    "xero": "Xero",
    "servicenow": "ServiceNow",
}


class WorkatoParser:
    """Parse a Workato export into structured scan results.

    Handles all 14 Workato asset types and the recursive recipe block tree.
    """

    def __init__(self, cred_filter: CredentialFilter) -> None:
        self.cred_filter = cred_filter
        self._connection_index: dict[str, MetadataConnection] = {}
        self._connection_id_index: dict[int, str] = {}  # numeric ID -> conn name
        self._provider_to_conn: dict[str, str] = {}  # provider -> conn name
        self._topic_index: dict[str, MetadataConnection] = {}
        self._schema_index: dict[str, dict] = {}
        self._recipe_id_index: dict[str, str] = {}
        self._asset_registry: dict[str, list[Path]] = {}

    # ── Content-Based Asset Classification ─────────────────────────

    @staticmethod
    def _classify_json_content(data: object, filename: str = "") -> str | None:
        """Classify a parsed JSON object by its content structure.

        Returns an asset type string or None if unrecognizable.
        Handles both bare and wrapper-key formats (e.g. {"recipe": {...}}).
        """
        # Handle arrays
        if isinstance(data, list):
            if not data:
                return None
            # Array of {name, value} dicts -> property_list
            if all(
                isinstance(item, dict) and "name" in item and "value" in item
                for item in data[:5]  # sample first 5
            ):
                return "property_list"
            # Array of step-like objects with type+provider -> steps
            if all(
                isinstance(item, dict) and "type" in item
                for item in data[:5]
            ):
                return "steps"
            return None

        if not isinstance(data, dict):
            return None

        # Check for wrapper keys and recurse into inner dict
        _WRAPPER_KEYS = (
            "recipe", "connection", "lookup_table", "custom_adapter",
            "api_endpoint", "api_collection", "lcap_app", "recipe_function",
            "account_property", "project_property", "topic",
            "workato_schema", "workato_db_table",
        )
        for wk in _WRAPPER_KEYS:
            inner = data.get(wk)
            if isinstance(inner, dict) and len(data) <= 3:
                # Wrapped format -- classify inner, but if the wrapper key
                # itself is a known type, prefer that.
                _KEY_TO_TYPE = {
                    "recipe": "recipe",
                    "connection": "connection",
                    "lookup_table": "lookup_table",
                    "custom_adapter": "custom_adapter",
                    "api_endpoint": "api_endpoint",
                    "api_collection": "api_collection",
                    "lcap_app": "lcap_app",
                    "recipe_function": "recipe_function",
                    "account_property": "property",
                    "project_property": "property",
                    "topic": "topic",
                    "workato_schema": "schema",
                    "workato_db_table": "lookup_table",
                }
                if wk in _KEY_TO_TYPE:
                    return _KEY_TO_TYPE[wk]

        keys = set(data.keys())

        # Manifest: has export_id or sha256, or filename is manifest.json
        if "export_id" in keys or ("sha256" in keys and "export_manifest_id" not in keys):
            return "manifest"
        if filename.lower() == "manifest.json":
            return "manifest"

        # Package: has changelog or (version + recipes list)
        if "changelog" in keys:
            return "package"
        if "version" in keys and "recipes" in keys and isinstance(data.get("recipes"), list):
            return "package"

        # Recipe: has code dict with keyword/block, or has trigger dict, or
        # has steps list + running/version_no
        code = data.get("code")
        if isinstance(code, dict) and ("keyword" in code or "block" in code):
            return "recipe"
        if isinstance(code, str):
            try:
                code_parsed = json.loads(code)
                if isinstance(code_parsed, dict) and ("keyword" in code_parsed or "block" in code_parsed):
                    return "recipe"
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(data.get("trigger"), dict):
            return "recipe"
        if isinstance(data.get("steps"), list) and (
            "running" in keys or "version_no" in keys or "trigger_application" in keys
        ):
            return "recipe"

        # Recipe without trigger/code/steps: metadata-only recipe.json (steps in sibling file)
        if "trigger_application" in keys or "action_applications" in keys or "connections_used" in keys:
            return "recipe"

        # Connection: has provider/application + auth marker
        has_conn_identity = "provider" in keys or (
            "application" in keys and "code" not in keys and "steps" not in keys and "trigger" not in keys
        )
        has_conn_marker = (
            "authorization_type" in keys or "authentication_type" in keys
            or "base_url" in keys or "connection_id" in keys or "verified" in keys
        )
        if has_conn_identity and has_conn_marker:
            return "connection"

        # Custom adapter: has source_code, or has both actions + triggers
        if "source_code" in keys:
            return "custom_adapter"
        if "actions" in keys and "triggers" in keys:
            return "custom_adapter"

        # Lookup table: has columns (array)
        if "columns" in keys and isinstance(data.get("columns"), list):
            return "lookup_table"

        # API collection: has endpoints array
        if "endpoints" in keys and isinstance(data.get("endpoints"), list):
            return "api_collection"

        # API endpoint: has method + (url or path)
        if "method" in keys and ("url" in keys or "path" in keys):
            return "api_endpoint"

        # LCAP app: has pages array
        if "pages" in keys and isinstance(data.get("pages"), list):
            return "lcap_app"

        # Recipe function: has steps list wrapped differently (no recipe indicators)
        if isinstance(data.get("steps"), list) and "name" in keys and "trigger" not in keys:
            return "recipe_function"

        # Topic: has schema key and no recipe indicators
        if "schema" in keys and "code" not in keys and "steps" not in keys:
            return "topic"

        # Schema: has fields/schema with field definitions
        if "fields" in keys and isinstance(data.get("fields"), (list, dict)):
            return "schema"

        # Property: simple name + value pair
        if "name" in keys and "value" in keys and len(keys) <= 4:
            return "property"

        return None

    def _build_asset_registry(self, walked_files: list) -> dict[str, list[Path]]:
        """Classify walked JSON files by content. Single pass, no extra rglob.

        Args:
            walked_files: List of ScannedFile objects from walk_repo().

        Returns:
            Dict mapping asset type strings to lists of Path objects.
        """
        registry: dict[str, list[Path]] = defaultdict(list)
        for sf in walked_files:
            path = sf.path if isinstance(sf.path, Path) else Path(str(sf.path))
            if path.suffix.lower() != ".json":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(text)
                asset_type = self._classify_json_content(data, path.name)
                if asset_type:
                    registry[asset_type].append(path)
            except (json.JSONDecodeError, OSError):
                continue
        return dict(registry)

    @staticmethod
    def _unwrap(data: dict, key: str) -> dict:
        """Unwrap nested format: if data[key] is a dict, return it; else pass through."""
        inner = data.get(key)
        if isinstance(inner, dict):
            return inner
        return data

    def parse(self, repo_root: Path) -> ScanResult:
        """Main entry point -- orchestrates all parsing phases."""
        from intodoc.platforms.workato import WorkatoPlugin

        workato_root = self._find_workato_root(repo_root)

        # Phase 1: Walk and classify all files
        plugin = WorkatoPlugin()
        all_files = walk_repo(repo_root, plugin)
        files = [f for f in all_files if f.file_type != FileType.UNKNOWN]

        # Phase 1b: Content-based asset classification (single pass, no extra rglob)
        self._asset_registry = self._build_asset_registry(all_files)

        # Phase 2: Parse manifest
        manifest_data = self._parse_manifest(workato_root)

        # Phase 3: Build connection index (from registry)
        self._build_connection_index(workato_root)

        # Phase 4: Build recipe ID index (from registry)
        self._build_recipe_id_index(workato_root)

        # Phase 5: Parse properties as context groups (from registry)
        context_groups = self._parse_properties(workato_root)

        # Phase 6: Parse all recipes + API endpoints + LCAP apps (from registry)
        jobs = self._parse_all_recipes(workato_root)
        jobs.extend(self._parse_recipe_functions(workato_root))
        jobs.extend(self._parse_api_assets(workato_root))
        jobs.extend(self._parse_lcap_assets(workato_root))

        # Phase 7: Parse custom adapters as routines + schemas (from registry)
        routines = self._parse_custom_adapters(workato_root)
        self._parse_schemas(workato_root)

        # Phase 8: Build manifest
        project_manifest = self._build_manifest(jobs, routines)

        # Merge all connections
        all_connections = (
            list(self._connection_index.values())
            + list(self._topic_index.values())
            + self._parse_lookup_tables(workato_root)
        )

        stats = {
            "total_files": len(all_files),
            "classified_files": len(files),
            "jobs": len(jobs),
            "recipes": len(self._asset_registry.get("recipe", [])),
            "api_endpoints": len(self._asset_registry.get("api_endpoint", [])) + len(self._asset_registry.get("api_collection", [])),
            "connections": len(all_connections),
            "context_groups": len(context_groups),
            "routines": len(routines),
            "schemas": len(self._schema_index),
            "manifest_assets": len(manifest_data) if isinstance(manifest_data, dict) else 0,
        }

        console.print(
            f"  Workato parser: {len(jobs)} jobs, "
            f"{len(all_connections)} connections, "
            f"{len(routines)} custom adapters"
        )

        return ScanResult(
            platform="workato",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=all_connections,
            context_groups=context_groups,
            routines=routines,
            manifest=project_manifest,
            stats=stats,
        )

    # ── Phase 1: Find Workato root ──────────────────────────────────

    @staticmethod
    def _find_workato_root(repo_root: Path) -> Path:
        """Find the Workato export root (look for manifest.json, *.recipe.json, or content-based)."""
        # Check if manifest.json exists at root
        if (repo_root / "manifest.json").exists():
            return repo_root

        # Look for manifest.json in subdirectories (1 level deep)
        for child in repo_root.iterdir():
            if child.is_dir() and (child / "manifest.json").exists():
                return child

        # Look for any .recipe.json file
        recipe_files = list(repo_root.rglob("*.recipe.json"))
        if recipe_files:
            # Return the common parent
            parents = {f.parent for f in recipe_files}
            if len(parents) == 1:
                return parents.pop()
            return repo_root

        # Content-based fallback: find directories with JSON files that look like recipes
        for json_path in repo_root.rglob("*.json"):
            try:
                text = json_path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(text)
                asset_type = WorkatoParser._classify_json_content(data, json_path.name)
                if asset_type == "recipe":
                    # Found a recipe -- return the common ancestor
                    # Walk up to find the export root (parent of recipes dir, or repo_root)
                    return repo_root
            except (json.JSONDecodeError, OSError):
                continue

        # Fallback to repo_root
        return repo_root

    # ── Phase 2: Parse manifest ─────────────────────────────────────

    def _parse_manifest(self, root: Path) -> dict:
        """Read manifest.json for asset index and dependency info."""
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            text = self._read_filtered(manifest_path)
            return json.loads(text)
        except (json.JSONDecodeError, OSError):
            return {}

    # ── Phase 3: Build connection index ─────────────────────────────

    def _build_connection_index(self, root: Path) -> None:
        """Parse all connection and topic files (from asset registry)."""
        for conn_path in sorted(self._asset_registry.get("connection", [])):
            try:
                conn = self._parse_connection(conn_path)
                self._connection_index[conn.name] = conn
                # Also index by provider for component matching
                provider = conn.additional_params.get("provider", "")
                if provider:
                    self._provider_to_conn[provider] = conn.name
                # Index by numeric ID for new-format connection_id lookups
                try:
                    self._connection_id_index[int(conn.xmi_id)] = conn.name
                except (ValueError, TypeError):
                    pass
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        for topic_path in sorted(self._asset_registry.get("topic", [])):
            try:
                topic = self._parse_topic(topic_path)
                self._topic_index[topic.name] = topic
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def _parse_connection(self, conn_path: Path) -> MetadataConnection:
        """Parse a single *.connection.json file."""
        text = self._read_filtered(conn_path)
        data = json.loads(text)
        data = self._unwrap(data, "connection")

        name = data.get("name", conn_path.stem.replace(".connection", ""))
        provider = data.get("provider") or data.get("application", "unknown")

        return MetadataConnection(
            xmi_id=str(data.get("id", name)),
            name=name,
            connection_type=_PROVIDER_TYPE_MAP.get(provider, provider.upper()),
            host=str(data.get("host", "")),
            port=str(data.get("port", "")),
            database=str(data.get("database", "")),
            schema_name=str(data.get("schema", "")),
            additional_params={
                "provider": provider,
                "application": data.get("application", provider),
                "authorization_type": str(data.get("authorization_type") or data.get("authentication_type", "")),
                "base_url": str(data.get("base_url", "")),
            },
        )

    def _parse_topic(self, topic_path: Path) -> MetadataConnection:
        """Parse a *.topic.json file as a MetadataConnection (event channel)."""
        text = self._read_filtered(topic_path)
        data = json.loads(text)

        name = data.get("name", topic_path.stem.replace(".topic", ""))

        return MetadataConnection(
            xmi_id=str(data.get("id", name)),
            name=f"topic_{name}",
            connection_type="EventTopic",
            additional_params={
                "topic_name": name,
                "schema": data.get("schema", {}),
            },
        )

    # ── Phase 4: Build recipe ID index ──────────────────────────────

    def _build_recipe_id_index(self, root: Path) -> None:
        """Map recipe IDs to names for call_recipe resolution (from registry)."""
        for recipe_path in sorted(self._asset_registry.get("recipe", [])):
            try:
                text = self._read_filtered(recipe_path)
                data = json.loads(text)
                data = self._unwrap(data, "recipe")
                recipe_id = str(data.get("id", ""))
                recipe_name = data.get("name", "")
                # Name fallback: if stem is just "recipe", use parent dir name
                if not recipe_name:
                    if recipe_path.stem == "recipe" or recipe_path.stem == "recipe.json":
                        recipe_name = recipe_path.parent.name
                    else:
                        recipe_name = recipe_path.stem.replace(".recipe", "")
                if recipe_id:
                    self._recipe_id_index[recipe_id] = recipe_name
            except (json.JSONDecodeError, OSError):
                continue

    # ── Phase 5: Parse properties ───────────────────────────────────

    def _parse_properties(self, root: Path) -> list[ContextGroup]:
        """Parse property files (from registry). Handles both individual files and property lists."""
        context_groups: list[ContextGroup] = []

        # Individual property files (account + project properties)
        all_vars: list[ContextVariable] = []
        for prop_path in sorted(self._asset_registry.get("property", [])):
            try:
                text = self._read_filtered(prop_path)
                data = json.loads(text)
                # Unwrap both account_property and project_property wrappers
                for wrapper_key in ("account_property", "project_property"):
                    data = self._unwrap(data, wrapper_key)
                name = data.get("name", prop_path.stem)
                all_vars.append(ContextVariable(
                    name=name,
                    type="String",
                    values={"_default": str(data.get("value", "[property]"))},
                ))
            except (json.JSONDecodeError, OSError):
                continue

        if all_vars:
            context_groups.append(ContextGroup(
                name="account_properties",
                variables=all_vars,
            ))

        # Property list files (single file containing array of {name, value} dicts)
        for pl_path in sorted(self._asset_registry.get("property_list", [])):
            try:
                text = self._read_filtered(pl_path)
                data = json.loads(text)
                if isinstance(data, list):
                    pl_vars: list[ContextVariable] = []
                    for item in data:
                        if isinstance(item, dict) and "name" in item:
                            pl_vars.append(ContextVariable(
                                name=item["name"],
                                type="String",
                                values={"_default": str(item.get("value", ""))},
                            ))
                    if pl_vars:
                        group_name = pl_path.stem.replace(".json", "").replace(".", "_")
                        context_groups.append(ContextGroup(
                            name=group_name,
                            variables=pl_vars,
                        ))
            except (json.JSONDecodeError, OSError):
                continue

        return context_groups

    # ── Phase 6: Parse recipes ──────────────────────────────────────

    def _parse_all_recipes(self, root: Path) -> list[ParsedJob]:
        """Parse all recipe files into ParsedJob objects (from registry)."""
        jobs: list[ParsedJob] = []
        for recipe_path in sorted(self._asset_registry.get("recipe", [])):
            try:
                job = self._parse_recipe(recipe_path, root)
                if job:
                    jobs.append(job)
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"  [yellow]Warning: Failed to parse {recipe_path}: {e}[/yellow]")
                continue
        return jobs

    def _parse_recipe_functions(self, root: Path) -> list[ParsedJob]:
        """Parse all recipe function files into ParsedJob objects (from registry)."""
        jobs: list[ParsedJob] = []
        for func_path in sorted(self._asset_registry.get("recipe_function", [])):
            try:
                text = self._read_filtered(func_path)
                data = json.loads(text)
                data = self._unwrap(data, "recipe_function")

                name = data.get("name", func_path.stem.replace(".recipe_function", ""))
                group = self._extract_group(func_path, root)

                components: list[Component] = []
                connections: list[Connection] = []
                tmap_entries: list[TMapEntry] = []
                trunjob_refs: list[TRunJobRef] = []

                steps_data = data.get("steps", [])
                if isinstance(steps_data, list):
                    self._walk_steps(
                        steps_data, 1, components, connections,
                        tmap_entries, trunjob_refs, "",
                    )

                tmap_data: list[TMapData] = []
                if tmap_entries:
                    tmap_data.append(TMapData(
                        input_tables=list({e.table_name for e in tmap_entries if e.table_name != "output"}),
                        output_tables=["output"],
                        entries=tmap_entries,
                    ))

                jobs.append(ParsedJob(
                    name=f"func_{name}",
                    path=str(func_path.relative_to(root)) if root in func_path.parents or root == func_path.parent else func_path.name,
                    purpose=data.get("description", ""),
                    components=components,
                    connections=connections,
                    tmap_data=tmap_data,
                    trunjob_refs=trunjob_refs,
                    group=group,
                ))
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"  [yellow]Warning: Failed to parse {func_path}: {e}[/yellow]")
                continue
        return jobs

    def _parse_recipe(self, recipe_path: Path, root: Path) -> ParsedJob | None:
        """Parse a single recipe file into a ParsedJob."""
        text = self._read_filtered(recipe_path)
        data = json.loads(text)
        data = self._unwrap(data, "recipe")

        # Name with fallback: if filename is generic (e.g. "recipe.json"), use parent dir name
        name = data.get("name", "")
        if not name:
            stem = recipe_path.stem
            if stem in ("recipe", "recipe.json"):
                name = recipe_path.parent.name
            else:
                name = stem.replace(".recipe", "")
        group = self._extract_group(recipe_path, root)

        # Check for sibling steps.json (nested export format)
        steps_file = recipe_path.parent / "steps.json"
        if steps_file.exists():
            try:
                steps_text = self._read_filtered(steps_file)
                steps_data_sibling = json.loads(steps_text)
                if isinstance(steps_data_sibling, list):
                    data.setdefault("steps", steps_data_sibling)
                elif isinstance(steps_data_sibling, dict) and isinstance(steps_data_sibling.get("steps"), list):
                    data.setdefault("steps", steps_data_sibling["steps"])
            except (json.JSONDecodeError, OSError):
                pass

        # Parse the code block tree
        code_raw = data.get("code", "{}")
        if isinstance(code_raw, str):
            try:
                code = json.loads(code_raw)
            except json.JSONDecodeError:
                code = {}
        elif isinstance(code_raw, dict):
            code = code_raw
        else:
            code = {}

        components: list[Component] = []
        connections: list[Connection] = []
        tmap_entries: list[TMapEntry] = []
        trunjob_refs: list[TRunJobRef] = []

        # Detect new format: recipe has trigger/steps at top level (after unwrap)
        trigger_data = data.get("trigger")
        steps_data = data.get("steps")
        if isinstance(trigger_data, dict) or isinstance(steps_data, list):
            # New format: walk trigger + steps
            last_name = ""
            if isinstance(trigger_data, dict):
                last_name = self._walk_trigger(
                    trigger_data, components, connections, tmap_entries,
                )
            if isinstance(steps_data, list):
                self._walk_steps(
                    steps_data, 1, components, connections,
                    tmap_entries, trunjob_refs, last_name,
                )
        elif code:
            self._walk_code_blocks(
                code, 0, components, connections,
                tmap_entries, trunjob_refs, "",
            )

        # Build TMapData from extracted entries
        tmap_data: list[TMapData] = []
        if tmap_entries:
            tmap_data.append(TMapData(
                input_tables=list({e.table_name for e in tmap_entries if e.table_name != "output"}),
                output_tables=["output"],
                entries=tmap_entries,
            ))

        return ParsedJob(
            name=name,
            path=str(recipe_path.relative_to(root)) if root in recipe_path.parents or root == recipe_path.parent else recipe_path.name,
            purpose=data.get("description", ""),
            version=str(data.get("version_no", "")),
            components=components,
            connections=connections,
            tmap_data=tmap_data,
            trunjob_refs=trunjob_refs,
            group=group,
        )

    # ── Core: Recursive Block Walker ────────────────────────────────

    def _walk_code_blocks(
        self,
        node: dict,
        depth: int,
        components: list[Component],
        connections: list[Connection],
        tmap_entries: list[TMapEntry],
        trunjob_refs: list[TRunJobRef],
        parent_name: str,
    ) -> str:
        """Recursively walk the code block tree.

        Creates Components and Connections for each step.
        Returns the unique_name of the last component created.
        """
        keyword = node.get("keyword", "")
        number = node.get("number", 0)
        provider = node.get("provider", "")
        action_name = node.get("name", "")

        last_name = parent_name

        if keyword in ("trigger", "action"):
            comp = self._make_component(node, depth)
            components.append(comp)

            # Sequential connection from parent
            if parent_name:
                connections.append(Connection(
                    name=f"flow_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name="FLOW",
                    line_style=0,
                ))

            # Extract datapill field mappings
            input_data = node.get("input", {})
            if isinstance(input_data, dict):
                tmap_entries.extend(
                    self._extract_datapill_refs(input_data, comp.unique_name)
                )

            # Detect call_recipe
            if action_name == "call_recipe" or (
                provider == "workato" and action_name == "call_recipe"
            ):
                recipe_id = str(input_data.get("recipe_id", "")) if isinstance(input_data, dict) else ""
                recipe_name = self._recipe_id_index.get(
                    recipe_id, f"recipe_{recipe_id}"
                )
                trunjob_refs.append(TRunJobRef(
                    child_job=recipe_name,
                    child_job_xmi_id=recipe_id,
                ))

            last_name = comp.unique_name

        elif keyword in ("if", "elsif", "else"):
            comp = Component(
                unique_name=f"condition_{keyword}_{number}",
                component_type=f"WorkatoCondition_{keyword}",
                params=self._extract_condition_params(node),
            )
            components.append(comp)
            if parent_name:
                line_style = 7 if keyword == "if" else 3
                connections.append(Connection(
                    name=f"cond_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name=keyword.upper(),
                    line_style=line_style,
                ))
            last_name = comp.unique_name

        elif keyword == "monitor":
            comp = Component(
                unique_name=f"monitor_{number}",
                component_type="WorkatoMonitor",
                params=[ComponentParam(
                    name="keyword", value="monitor", field_type="TEXT",
                )],
            )
            components.append(comp)
            if parent_name:
                connections.append(Connection(
                    name=f"monitor_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name="MONITOR",
                    line_style=0,
                ))
            last_name = comp.unique_name

        elif keyword == "catch":
            comp = Component(
                unique_name=f"catch_{number}",
                component_type="WorkatoCatch",
                params=[ComponentParam(
                    name="keyword", value="catch", field_type="TEXT",
                )],
            )
            components.append(comp)
            if parent_name:
                connections.append(Connection(
                    name=f"catch_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name="ON_ERROR",
                    line_style=2,
                ))
            last_name = comp.unique_name

        elif keyword in ("foreach", "repeat"):
            comp = Component(
                unique_name=f"loop_{keyword}_{number}",
                component_type=f"WorkatoLoop_{keyword}",
                params=self._extract_step_params(node),
            )
            components.append(comp)
            if parent_name:
                connections.append(Connection(
                    name=f"loop_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name=keyword.upper(),
                    line_style=0,
                ))
            last_name = comp.unique_name

        elif keyword == "stop":
            comp = Component(
                unique_name=f"stop_{number}",
                component_type="WorkatoStop",
                params=self._extract_step_params(node),
            )
            components.append(comp)
            if parent_name:
                connections.append(Connection(
                    name=f"stop_{parent_name}_to_{comp.unique_name}",
                    source=parent_name,
                    target=comp.unique_name,
                    connector_name="STOP",
                    line_style=4,
                ))
            last_name = comp.unique_name

        # Recurse into child blocks
        child_parent = last_name
        for child_node in node.get("block", []):
            if isinstance(child_node, dict):
                child_parent = self._walk_code_blocks(
                    child_node, depth + 1, components, connections,
                    tmap_entries, trunjob_refs, child_parent,
                )

        return last_name

    # ── New-Format Step Walker ──────────────────────────────────────

    def _walk_trigger(
        self,
        trigger: dict,
        components: list[Component],
        connections: list[Connection],
        tmap_entries: list[TMapEntry],
    ) -> str:
        """Extract trigger as the first Component from new-format recipe.trigger."""
        provider = trigger.get("provider") or trigger.get("application", "unknown")
        name = trigger.get("name") or trigger.get("type", "unknown")
        comp_type = f"WorkatoTrigger_{provider}_{name}"
        unique_name = f"{provider}_trigger_{name}"

        params = self._extract_trigger_params(trigger)
        conn_id = trigger.get("connection_id")
        conn_ref = self._resolve_connection_ref(provider, conn_id)

        # Extract datapill refs from trigger input
        input_data = trigger.get("input", {})
        if isinstance(input_data, dict):
            tmap_entries.extend(self._extract_datapill_refs(input_data, unique_name))

        components.append(Component(
            unique_name=unique_name,
            component_type=comp_type,
            params=params,
            metadata_connection_ref=conn_ref,
        ))
        return unique_name

    def _walk_steps(
        self,
        steps: list,
        depth: int,
        components: list[Component],
        connections: list[Connection],
        tmap_entries: list[TMapEntry],
        trunjob_refs: list[TRunJobRef],
        parent_name: str,
    ) -> str:
        """Recursively walk new-format steps[] array."""
        last_name = parent_name

        for step in steps:
            if not isinstance(step, dict):
                continue

            step_type = step.get("type", "")

            if step_type == "action":
                comp = self._make_step_component(step, depth)
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"flow_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name="FLOW",
                        line_style=0,
                    ))
                # Extract datapill refs
                input_data = step.get("input", {})
                if isinstance(input_data, dict):
                    tmap_entries.extend(
                        self._extract_datapill_refs(input_data, comp.unique_name)
                    )
                # Detect call_recipe
                provider = step.get("provider") or step.get("application", "")
                action_name = step.get("name") or step.get("action", "")
                if action_name == "call_recipe":
                    recipe_id = str(input_data.get("recipe_id", "")) if isinstance(input_data, dict) else ""
                    recipe_name = self._recipe_id_index.get(
                        recipe_id, f"recipe_{recipe_id}"
                    )
                    trunjob_refs.append(TRunJobRef(
                        child_job=recipe_name,
                        child_job_xmi_id=recipe_id,
                    ))
                # Detect lookup_table references
                if action_name == "lookup_table_lookup" and isinstance(input_data, dict):
                    table_name = input_data.get("table_name", "")
                    if table_name:
                        tmap_entries.append(TMapEntry(
                            table_name=f"lookup_{table_name}",
                            column_name=input_data.get("lookup_column", ""),
                            expression=input_data.get("lookup_value", ""),
                        ))
                last_name = comp.unique_name

            elif step_type in ("if", "conditional"):
                comp = Component(
                    unique_name=f"condition_if_{depth}_{id(step) % 10000}",
                    component_type="WorkatoCondition_if",
                    params=self._extract_new_condition_params(step),
                )
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"cond_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name="IF",
                        line_style=7,
                    ))
                # Recurse into true/false branches
                true_steps = step.get("steps_on_true", [])
                if true_steps:
                    self._walk_steps(
                        true_steps, depth + 1, components, connections,
                        tmap_entries, trunjob_refs, comp.unique_name,
                    )
                false_steps = step.get("steps_on_false", [])
                if false_steps:
                    self._walk_steps(
                        false_steps, depth + 1, components, connections,
                        tmap_entries, trunjob_refs, comp.unique_name,
                    )
                last_name = comp.unique_name

            elif step_type in ("foreach", "repeat", "repeat_for_each"):
                comp = Component(
                    unique_name=f"loop_{step_type}_{depth}_{id(step) % 10000}",
                    component_type=f"WorkatoLoop_{step_type}",
                    params=[ComponentParam(
                        name="type", value=step_type, field_type="TEXT",
                    )],
                )
                # Add list param for foreach
                list_val = step.get("list", "")
                if list_val:
                    comp.params.append(ComponentParam(
                        name="list", value=str(list_val), field_type="INPUT",
                    ))
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"loop_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name=step_type.upper(),
                        line_style=0,
                    ))
                # Recurse into loop body
                inner_steps = step.get("steps", [])
                if inner_steps:
                    self._walk_steps(
                        inner_steps, depth + 1, components, connections,
                        tmap_entries, trunjob_refs, comp.unique_name,
                    )
                last_name = comp.unique_name

            elif step_type == "monitor":
                comp = Component(
                    unique_name=f"monitor_{depth}_{id(step) % 10000}",
                    component_type="WorkatoMonitor",
                    params=[ComponentParam(
                        name="type", value="monitor", field_type="TEXT",
                    )],
                )
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"monitor_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name="MONITOR",
                        line_style=0,
                    ))
                inner_steps = step.get("steps", [])
                if inner_steps:
                    self._walk_steps(
                        inner_steps, depth + 1, components, connections,
                        tmap_entries, trunjob_refs, comp.unique_name,
                    )
                last_name = comp.unique_name

            elif step_type == "catch":
                comp = Component(
                    unique_name=f"catch_{depth}_{id(step) % 10000}",
                    component_type="WorkatoCatch",
                    params=[ComponentParam(
                        name="type", value="catch", field_type="TEXT",
                    )],
                )
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"catch_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name="ON_ERROR",
                        line_style=2,
                    ))
                inner_steps = step.get("steps", [])
                if inner_steps:
                    self._walk_steps(
                        inner_steps, depth + 1, components, connections,
                        tmap_entries, trunjob_refs, comp.unique_name,
                    )
                last_name = comp.unique_name

            elif step_type == "stop":
                comp = Component(
                    unique_name=f"stop_{depth}_{id(step) % 10000}",
                    component_type="WorkatoStop",
                    params=[
                        ComponentParam(name="type", value="stop", field_type="TEXT"),
                        ComponentParam(
                            name="status",
                            value=step.get("status", ""),
                            field_type="TEXT",
                        ),
                        ComponentParam(
                            name="message",
                            value=step.get("message", ""),
                            field_type="TEXT",
                        ),
                    ],
                )
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"stop_{last_name}_to_{comp.unique_name}",
                        source=last_name,
                        target=comp.unique_name,
                        connector_name="STOP",
                        line_style=4,
                    ))
                last_name = comp.unique_name

            elif step_type == "formula":
                params = self._extract_new_step_params(step)
                label = step.get("label", "")
                unique = f"formula_{depth}_{id(step) % 10000}"
                if label:
                    unique = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
                comp = Component(unique_name=unique, component_type="WorkatoFormula", params=params)
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"flow_{last_name}_to_{comp.unique_name}",
                        source=last_name, target=comp.unique_name,
                        connector_name="FLOW", line_style=0,
                    ))
                last_name = comp.unique_name

            elif step_type == "lookup":
                provider = step.get("provider") or step.get("application", "unknown")
                action_name = step.get("name") or step.get("action", "lookup")
                comp = self._make_step_component(step, depth)
                comp.component_type = f"WorkatoLookup_{provider}_{action_name}"
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"flow_{last_name}_to_{comp.unique_name}",
                        source=last_name, target=comp.unique_name,
                        connector_name="FLOW", line_style=0,
                    ))
                input_data = step.get("input", {})
                if isinstance(input_data, dict):
                    tmap_entries.extend(self._extract_datapill_refs(input_data, comp.unique_name))
                last_name = comp.unique_name

            elif step_type == "trigger":
                # Trigger appearing as a step (e.g., in steps.json)
                app = step.get("provider") or step.get("application", "unknown")
                trigger_name = step.get("name") or step.get("type", "unknown")
                label = step.get("label", "")
                params = self._extract_new_step_params(step)
                unique = f"{app}_trigger_step"
                if label:
                    unique = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
                comp = Component(
                    unique_name=unique,
                    component_type=f"WorkatoTrigger_{app}_{trigger_name}",
                    params=params,
                )
                components.append(comp)
                if last_name:
                    connections.append(Connection(
                        name=f"flow_{last_name}_to_{comp.unique_name}",
                        source=last_name, target=comp.unique_name,
                        connector_name="FLOW", line_style=0,
                    ))
                last_name = comp.unique_name

        return last_name

    def _make_step_component(self, step: dict, depth: int) -> Component:
        """Create a Component from a new-format action step."""
        provider = step.get("provider") or step.get("application", "unknown")
        action_name = step.get("name") or step.get("action", "unknown")
        label = step.get("label", "")
        comp_type = f"WorkatoAction_{provider}_{action_name}"

        # Build unique name from label (snake_case) or provider_action
        if label:
            unique_name = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
        else:
            unique_name = f"{provider}_{action_name}_{depth}"

        params = self._extract_new_step_params(step)
        conn_id = step.get("connection_id")
        conn_ref = self._resolve_connection_ref(provider, conn_id)

        return Component(
            unique_name=unique_name,
            component_type=comp_type,
            params=params,
            metadata_connection_ref=conn_ref,
        )

    @staticmethod
    def _extract_new_step_params(step: dict) -> list[ComponentParam]:
        """Extract params from new-format action step (generic -- all keys)."""
        _SKIP_KEYS = {"input", "output", "output_schema", "steps", "steps_on_true",
                      "steps_on_false", "steps_on_error", "type", "step_id"}
        params: list[ComponentParam] = []
        for key, val in step.items():
            if key in _SKIP_KEYS or val is None:
                continue
            if isinstance(val, (dict, list)):
                val_str = json.dumps(val)
                field_type = "INPUT"
            else:
                val_str = str(val)
                field_type = "TEXT"
            if val_str.strip():
                params.append(ComponentParam(name=key, value=val_str, field_type=field_type))
        # Also extract input fields
        input_data = step.get("input", {})
        if isinstance(input_data, dict):
            for field_name, field_val in input_data.items():
                if field_val is None:
                    continue
                val_str = json.dumps(field_val) if isinstance(field_val, (dict, list)) else str(field_val)
                if val_str.strip():
                    params.append(ComponentParam(
                        name=field_name, value=val_str, field_type="INPUT",
                    ))
        return params

    @staticmethod
    def _extract_trigger_params(trigger: dict) -> list[ComponentParam]:
        """Extract params from new-format trigger object (generic -- all keys)."""
        _SKIP_KEYS = {"input", "output_schema", "output"}
        params: list[ComponentParam] = []
        for key, val in trigger.items():
            if key in _SKIP_KEYS or val is None:
                continue
            if isinstance(val, (dict, list)):
                val_str = json.dumps(val)
                field_type = "INPUT"
            else:
                val_str = str(val)
                field_type = "TEXT"
            if val_str.strip():
                params.append(ComponentParam(name=key, value=val_str, field_type=field_type))
        # Also extract input fields (datapill-level detail)
        input_data = trigger.get("input", {})
        if isinstance(input_data, dict):
            for field_name, field_val in input_data.items():
                if field_val is None:
                    continue
                val_str = json.dumps(field_val) if isinstance(field_val, (dict, list)) else str(field_val)
                if val_str.strip():
                    params.append(ComponentParam(
                        name=field_name, value=val_str, field_type="INPUT",
                    ))
        return params

    @staticmethod
    def _extract_new_condition_params(step: dict) -> list[ComponentParam]:
        """Extract condition params from new-format if step."""
        params: list[ComponentParam] = [
            ComponentParam(name="type", value="if", field_type="TEXT"),
        ]
        condition = step.get("condition", {})
        if isinstance(condition, dict):
            for key in ("field", "operator", "value"):
                val = condition.get(key)
                if val:
                    params.append(ComponentParam(
                        name=key, value=str(val), field_type="TEXT",
                    ))
        return params

    # ── Component Creation ──────────────────────────────────────────

    def _make_component(self, node: dict, depth: int) -> Component:
        """Create a Component from an action/trigger node."""
        keyword = node.get("keyword", "action")
        provider = node.get("provider", "unknown")
        action_name = node.get("name", "unknown")
        number = node.get("number", 0)

        # Build component type: WorkatoTrigger_salesforce_new_record
        # or WorkatoAction_netsuite_create_record
        prefix = "WorkatoTrigger" if keyword == "trigger" else "WorkatoAction"
        comp_type = f"{prefix}_{provider}_{action_name}"

        unique_name = node.get("as", f"{provider}_{action_name}_{number}")

        params = self._extract_step_params(node)

        # Add provider reference as metadata_connection_ref
        conn_ref = ""
        if provider:
            # Try direct match first, then provider-to-connection mapping
            if provider in self._connection_index:
                conn_ref = provider
            elif provider in self._provider_to_conn:
                conn_ref = self._provider_to_conn[provider]

        return Component(
            unique_name=unique_name,
            component_type=comp_type,
            params=params,
            metadata_connection_ref=conn_ref,
        )

    def _resolve_connection_ref(self, provider: str, connection_id: int | None = None) -> str:
        """Resolve a connection reference by numeric ID, provider name, or provider-to-conn map."""
        # 1. Numeric connection_id lookup
        if connection_id is not None:
            name = self._connection_id_index.get(connection_id)
            if name:
                return name
        # 2. Direct provider name in connection index
        if provider and provider in self._connection_index:
            return provider
        # 3. Provider-to-connection mapping
        if provider and provider in self._provider_to_conn:
            return self._provider_to_conn[provider]
        return ""

    def _extract_step_params(self, node: dict) -> list[ComponentParam]:
        """Extract meaningful parameters from a step node."""
        params: list[ComponentParam] = []

        # Core identification fields
        for key in ("provider", "name", "as", "title", "description", "keyword"):
            val = node.get(key)
            if val and isinstance(val, str):
                params.append(ComponentParam(
                    name=key, value=val, field_type="TEXT",
                ))

        # Input fields (the core action configuration)
        input_data = node.get("input", {})
        if isinstance(input_data, dict):
            for field_name, field_val in input_data.items():
                if field_val is None:
                    continue
                val_str = json.dumps(field_val) if isinstance(field_val, (dict, list)) else str(field_val)
                if val_str.strip():
                    params.append(ComponentParam(
                        name=field_name,
                        value=val_str,
                        field_type="INPUT",
                    ))

        return params

    def _extract_condition_params(self, node: dict) -> list[ComponentParam]:
        """Extract condition parameters from if/elsif/else nodes."""
        params: list[ComponentParam] = []

        params.append(ComponentParam(
            name="keyword",
            value=node.get("keyword", ""),
            field_type="TEXT",
        ))

        input_data = node.get("input", {})
        if isinstance(input_data, dict):
            # Extract condition type and operand
            for key in ("type", "operand"):
                val = input_data.get(key)
                if val:
                    params.append(ComponentParam(
                        name=key, value=str(val), field_type="TEXT",
                    ))
            # Extract conditions array
            conditions = input_data.get("conditions", [])
            if conditions:
                params.append(ComponentParam(
                    name="conditions",
                    value=json.dumps(conditions),
                    field_type="CONDITION",
                ))

        return params

    # ── Datapill Extraction ─────────────────────────────────────────

    def _extract_datapill_refs(
        self, input_data: dict, table_name: str = "output",
    ) -> list[TMapEntry]:
        """Extract datapill references from input fields as TMapEntry objects."""
        entries: list[TMapEntry] = []
        for field_name, field_value in input_data.items():
            if not isinstance(field_value, str):
                continue
            # Find all datapill references (old #{_('...')} and new {{...}} syntax)
            pills_old = _DATAPILL_RE.findall(field_value)
            pills_new = _DATAPILL_NEW_RE.findall(field_value)
            if pills_old or pills_new:
                for _pill_path in pills_old or pills_new:
                    entries.append(TMapEntry(
                        table_name=table_name,
                        column_name=field_name,
                        expression=field_value,
                    ))
            elif (
                field_value.strip()
                and not field_value.startswith("{")
                and not field_value.startswith("[")
            ):
                # Static mapping (constant value)
                entries.append(TMapEntry(
                    table_name=table_name,
                    column_name=field_name,
                    expression=f'"{field_value}"',
                ))
        return entries

    # ── Phase 6 continued: API and LCAP assets ──────────────────────

    def _parse_api_assets(self, root: Path) -> list[ParsedJob]:
        """Parse API endpoint and collection files as API jobs (from registry)."""
        jobs: list[ParsedJob] = []

        # Individual API endpoints
        for api_path in sorted(self._asset_registry.get("api_endpoint", [])):
            try:
                text = self._read_filtered(api_path)
                data = json.loads(text)
                data = self._unwrap(data, "api_endpoint")
                name = data.get("name", api_path.stem.replace(".api_endpoint", ""))
                group = self._extract_group(api_path, root)

                params: list[ComponentParam] = []
                for key in ("method", "url", "path", "content_type", "request_schema", "response_schema"):
                    val = data.get(key)
                    if val:
                        val_str = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
                        params.append(ComponentParam(
                            name=key, value=val_str, field_type="TEXT",
                        ))

                comp = Component(
                    unique_name=f"api_{name}",
                    component_type="WorkatoAPIEndpoint",
                    params=params,
                )

                jobs.append(ParsedJob(
                    name=f"api_{name}",
                    path=str(api_path.relative_to(root)) if root in api_path.parents else api_path.name,
                    purpose=data.get("description", ""),
                    components=[comp],
                    group=group,
                ))
            except (json.JSONDecodeError, OSError):
                continue

        # API collections (one file with multiple endpoints)
        for coll_path in sorted(self._asset_registry.get("api_collection", [])):
            try:
                text = self._read_filtered(coll_path)
                coll_data = json.loads(text)
                coll_data = self._unwrap(coll_data, "api_collection")
                coll_name = coll_data.get("name", coll_path.stem)
                group = self._extract_group(coll_path, root)

                for ep in coll_data.get("endpoints", []):
                    if not isinstance(ep, dict):
                        continue
                    ep_name_raw = ep.get("name", ep.get("path", "unknown"))
                    # Sanitize: replace path separators and other filesystem-unsafe chars
                    ep_name = re.sub(r"[/\\:*?\"<>|]+", "_", ep_name_raw).strip("_")
                    params_ep: list[ComponentParam] = []
                    for key in ("method", "url", "path", "content_type"):
                        val = ep.get(key)
                        if val:
                            params_ep.append(ComponentParam(
                                name=key, value=str(val), field_type="TEXT",
                            ))

                    comp = Component(
                        unique_name=f"api_{coll_name}_{ep_name}",
                        component_type="WorkatoAPIEndpoint",
                        params=params_ep,
                    )
                    jobs.append(ParsedJob(
                        name=f"api_{coll_name}_{ep_name}",
                        path=str(coll_path.relative_to(root)) if root in coll_path.parents else coll_path.name,
                        purpose=ep.get("description", ""),
                        components=[comp],
                        group=group,
                    ))
            except (json.JSONDecodeError, OSError):
                continue

        return jobs

    def _parse_lcap_assets(self, root: Path) -> list[ParsedJob]:
        """Parse LCAP app files as LCAP app jobs (from registry)."""
        jobs: list[ParsedJob] = []
        for lcap_path in sorted(self._asset_registry.get("lcap_app", [])):
            try:
                text = self._read_filtered(lcap_path)
                data = json.loads(text)
                name = data.get("name", lcap_path.stem.replace(".lcap_app", ""))
                group = self._extract_group(lcap_path, root)

                params: list[ComponentParam] = []
                for key in ("name", "description", "version"):
                    val = data.get(key)
                    if val:
                        params.append(ComponentParam(
                            name=key, value=str(val), field_type="TEXT",
                        ))

                # Parse pages as sub-components
                pages = data.get("pages", [])
                if isinstance(pages, list):
                    params.append(ComponentParam(
                        name="page_count",
                        value=str(len(pages)),
                        field_type="TEXT",
                    ))

                comp = Component(
                    unique_name=f"lcap_{name}",
                    component_type="WorkatoLCAPApp",
                    params=params,
                )

                jobs.append(ParsedJob(
                    name=f"lcap_{name}",
                    path=str(lcap_path.relative_to(root)) if root in lcap_path.parents else lcap_path.name,
                    purpose=data.get("description", ""),
                    components=[comp],
                    group=group,
                ))
            except (json.JSONDecodeError, OSError):
                continue
        return jobs

    # ── Phase 7: Custom adapters and schemas ────────────────────────

    def _parse_custom_adapters(self, root: Path) -> list[Routine]:
        """Parse custom adapter files as Routines (from registry)."""
        routines: list[Routine] = []
        for adapter_path in sorted(self._asset_registry.get("custom_adapter", [])):
            try:
                text = self._read_filtered(adapter_path)
                data = json.loads(text)
                name = data.get("name", adapter_path.stem.replace(".custom_adapter", ""))

                # The source_code field contains the Ruby SDK code
                source_code = data.get("source_code", data.get("code", ""))
                if isinstance(source_code, dict):
                    source_code = json.dumps(source_code, indent=2)

                routines.append(Routine(
                    name=f"adapter_{name}",
                    source_code=str(source_code),
                    is_system=False,
                ))
            except (json.JSONDecodeError, OSError):
                continue
        return routines

    def _parse_schemas(self, root: Path) -> None:
        """Parse schema files into schema_index (from registry)."""
        for schema_path in sorted(self._asset_registry.get("schema", [])):
            try:
                text = self._read_filtered(schema_path)
                data = json.loads(text)
                name = data.get("name", schema_path.stem.replace(".workato_schema", ""))
                self._schema_index[name] = data
            except (json.JSONDecodeError, OSError):
                continue

    def _parse_lookup_tables(self, root: Path) -> list[MetadataConnection]:
        """Parse lookup table files (from registry)."""
        connections: list[MetadataConnection] = []

        for lt_path in sorted(self._asset_registry.get("lookup_table", [])):
            try:
                text = self._read_filtered(lt_path)
                data = json.loads(text)
                data = self._unwrap(data, "lookup_table")
                data = self._unwrap(data, "workato_db_table")
                name = data.get("name", lt_path.stem.replace(".lookup_table", "").replace(".workato_db_table", ""))

                # Determine if it's a WorkatoDB or LookupTable based on content
                if "workato_db_table" in lt_path.name.lower() or (
                    "table_type" in data and data.get("table_type") == "workato_db"
                ):
                    connections.append(MetadataConnection(
                        xmi_id=str(data.get("id", name)),
                        name=f"wdb_{name}",
                        connection_type="WorkatoDB",
                        additional_params={
                            "columns": data.get("columns", []),
                        },
                    ))
                else:
                    connections.append(MetadataConnection(
                        xmi_id=str(data.get("id", name)),
                        name=f"lookup_{name}",
                        connection_type="LookupTable",
                        additional_params={
                            "columns": data.get("columns", []),
                            "row_count": data.get("row_count", 0),
                        },
                    ))
            except (json.JSONDecodeError, OSError):
                continue

        return connections

    # ── Phase 8: Build manifest ─────────────────────────────────────

    def _build_manifest(
        self, jobs: list[ParsedJob], routines: list[Routine],
    ) -> ProjectManifest:
        """Build cross-reference manifest from parsed data."""
        job_calls: dict[str, list[str]] = {}
        job_called_by: dict[str, list[str]] = {}
        job_connections_used: dict[str, list[str]] = {}
        connection_used_by: dict[str, list[str]] = {}
        job_contexts_used: dict[str, list[str]] = {}
        routines_used_by: dict[str, list[str]] = {}
        job_groups: dict[str, str] = {}

        for job in jobs:
            job_groups[job.name] = job.group

            # Recipe calls (from TRunJobRef)
            calls = [ref.child_job for ref in job.trunjob_refs]
            if calls:
                job_calls[job.name] = calls
                for called in calls:
                    job_called_by.setdefault(called, []).append(job.name)

            # Connection usage
            conns_used: set[str] = set()
            for comp in job.components:
                if comp.metadata_connection_ref:
                    conns_used.add(comp.metadata_connection_ref)
                # Also check provider param for implicit connection
                for p in comp.params:
                    if p.name == "provider":
                        if p.value in self._connection_index:
                            conns_used.add(p.value)
                        elif p.value in self._provider_to_conn:
                            conns_used.add(self._provider_to_conn[p.value])
            if conns_used:
                job_connections_used[job.name] = sorted(conns_used)
                for conn in conns_used:
                    connection_used_by.setdefault(conn, []).append(job.name)

        return ProjectManifest(
            job_calls=job_calls,
            job_called_by=job_called_by,
            job_connections_used=job_connections_used,
            connection_used_by=connection_used_by,
            job_contexts_used=job_contexts_used,
            routines_used_by=routines_used_by,
            job_groups=job_groups,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _read_filtered(self, path: Path) -> str:
        """Read a file and apply credential filtering."""
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.cred_filter.filter_text(text)

    @staticmethod
    def _extract_group(file_path: Path, root: Path) -> str:
        """Extract folder-based group name from the file path."""
        try:
            rel = file_path.relative_to(root)
            parts = rel.parts
            if len(parts) > 1:
                return parts[0]
            return "root"
        except ValueError:
            return "root"
