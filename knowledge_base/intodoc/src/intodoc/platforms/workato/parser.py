"""Workato recipe parser -- raw JSON passthrough with credential filtering.

Workato exports are self-describing JSON. Unlike Talend (opaque XML/XMI with
cross-file ID references), Workato JSON needs no structural transformation.
This parser strips credentials and schema noise, then passes cleaned raw JSON
through to the LLM as a single Component per recipe.
"""

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
    ContextGroup,
    ContextVariable,
    FileType,
    MetadataConnection,
    ParsedJob,
    ProjectManifest,
    Routine,
    ScanResult,
    TRunJobRef,
)
from intodoc.platforms.base_parser import BaseParser
from intodoc.scanner.walker import walk_repo

if TYPE_CHECKING:
    from intodoc.scanner.credential_filter import CredentialFilter

console = Console()

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

# Action-name substrings that hint at read-only vs write-ish connector usage.
# Workato has no structural "input"/"output" marker on an action step the way
# a Talend component name does (tXxxInput / tXxxOutput); the action's own
# verb-shaped name is the only available signal, so this stays a best-effort
# heuristic -- unmatched names fall back to "unknown" rather than a guess.
_WORKATO_INPUT_ACTION_HINTS: tuple[str, ...] = (
    "get", "search", "find", "list", "lookup", "read", "fetch", "query", "export",
)
_WORKATO_OUTPUT_ACTION_HINTS: tuple[str, ...] = (
    "create", "update", "delete", "insert", "upsert", "post", "put",
    "remove", "write", "send", "notify", "import",
)


def _workato_display_system(provider: str) -> str:
    """Map a Workato provider slug to a display name, same table as connections."""
    return _PROVIDER_TYPE_MAP.get(provider, provider.replace("_", " ").title())


def _workato_action_direction(name: str) -> str:
    """Best-effort read/write classification from a Workato action's own name."""
    normalized = name.lower()
    if any(hint in normalized for hint in _WORKATO_OUTPUT_ACTION_HINTS):
        return "output"
    if any(hint in normalized for hint in _WORKATO_INPUT_ACTION_HINTS):
        return "input"
    return "unknown"


def _workato_extract_trigger(data: dict) -> dict | None:
    """Return the trigger step dict, handling both recipe JSON shapes.

    New-format exports carry a standalone ``trigger`` dict; legacy exports
    nest the trigger inside ``code`` with ``keyword: "trigger"``.
    """
    trigger = data.get("trigger")
    if isinstance(trigger, dict) and isinstance(trigger.get("provider"), str) and trigger.get("provider"):
        return trigger
    code = data.get("code")
    if (
        isinstance(code, dict)
        and code.get("keyword") == "trigger"
        and isinstance(code.get("provider"), str)
        and code.get("provider")
    ):
        return code
    return None


def _workato_collect_action_steps(obj: object, actions: list[dict]) -> None:
    """Recursively collect action-step dicts regardless of nesting container.

    Both recipe JSON shapes nest actions under different container keys
    (``block`` / ``steps`` / ``steps_on_true`` / ``steps_on_false``); rather
    than special-casing each container name, this walks every value like the
    existing ``_scan_connection_refs`` / ``_collect_providers`` helpers do.
    """
    if isinstance(obj, dict):
        keyword = obj.get("keyword") or obj.get("type")
        provider = obj.get("provider")
        if keyword == "action" and isinstance(provider, str) and provider:
            # "name" is the connector's own canonical action id (e.g.
            # "create_record", "get_contact") and is what carries the
            # read/write hint; "as"/"label" are human-facing display
            # names only and stay separate so a friendly label never
            # silently steers the direction heuristic.
            action_name = obj.get("name") if isinstance(obj.get("name"), str) else ""
            actions.append({
                "provider": provider,
                "action_name": action_name,
                "component_name": obj.get("as") or obj.get("label") or action_name or provider,
            })
        for val in obj.values():
            _workato_collect_action_steps(val, actions)
    elif isinstance(obj, list):
        for item in obj:
            _workato_collect_action_steps(item, actions)


class WorkatoParser(BaseParser):
    """Parse a Workato export into structured scan results.

    Passes cleaned raw JSON through as a single Component per recipe.
    Handles all 14 Workato asset types. Strips credentials and schema noise.
    """

    # Keys that add noise without semantic value for the LLM
    _NOISE_KEYS = frozenset({
        "extended_input_schema", "extended_output_schema",
        "input_schema", "output_schema",
        "uuid", "number", "position",
        "visible_config_fields", "dynamicPickListSelection", "toggleCfg",
    })

    def __init__(self, cred_filter: CredentialFilter) -> None:
        super().__init__(cred_filter)
        self._connection_index: dict[str, MetadataConnection] = {}
        self._connection_id_index: dict[int, str] = {}  # numeric ID -> conn name
        self._connection_alias_index: dict[str, str] = {}  # manifest alias -> conn name
        self._provider_to_conn: dict[str, str] = {}  # provider -> conn name
        self._topic_index: dict[str, MetadataConnection] = {}
        self._schema_index: dict[str, dict] = {}
        self._recipe_id_index: dict[str, str] = {}
        self._asset_registry: dict[str, list[Path]] = {}
        self._parsed_recipe_data: dict[str, dict] = {}  # job_name -> cleaned recipe dict

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

        # Reset warning collection for this scan.
        self._warnings = []
        self._warn_root = repo_root
        self._parsed_recipe_data = {}

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
        self._index_manifest_connection_aliases(manifest_data)

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

        # Phase 8: establish artifact/source-root identity before creating
        # topology facts. Recipe names are display labels and are not unique
        # enough to be graph identities in a composite upload.
        source_roots = self._prepare_single_root_topology_identities(
            jobs, repo_root, "workato",
        )
        project_manifest = self._build_manifest(jobs, routines, source_roots)

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

        stats["total_warnings"] = len(self._warnings)

        return ScanResult(
            platform="workato",
            repo_root=str(repo_root),
            files=files,
            jobs=jobs,
            metadata_connections=all_connections,
            context_groups=context_groups,
            routines=routines,
            manifest=project_manifest,
            schema_definitions=list(self._schema_index.values()),
            stats=stats,
            warnings=list(self._warnings),
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

    def _index_manifest_connection_aliases(self, manifest_data: object) -> None:
        """Build best-effort alias resolution for manifest dependency formats."""
        for item in self._iter_dicts(manifest_data):
            ids: list[int] = []
            aliases: list[str] = []
            for key, value in item.items():
                key_l = str(key).lower()
                if key_l in {"id", "connection_id", "account_id"}:
                    try:
                        ids.append(int(value))
                    except (TypeError, ValueError):
                        pass
                elif key_l in {"alias", "key", "name", "dependency", "dependency_name"} and isinstance(value, str):
                    aliases.append(value)
            resolved = next((self._connection_id_index[id_] for id_ in ids if id_ in self._connection_id_index), "")
            if not resolved:
                resolved = next((alias for alias in aliases if alias in self._connection_index), "")
            if resolved:
                for alias in aliases:
                    self._connection_alias_index[alias] = resolved

    @staticmethod
    def _iter_dicts(obj: object):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from WorkatoParser._iter_dicts(value)
        elif isinstance(obj, list):
            for item in obj:
                yield from WorkatoParser._iter_dicts(item)

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
                self._warn(kind="parse_error", file=recipe_path, message=str(e), phase="recipe")
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

                self._strip_noise_keys(data)
                trunjob_refs = self._find_call_recipe_refs(data)

                full_name = f"func_{name}"
                params = self._dict_to_params(data)
                comp = Component(
                    unique_name=re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower() or "func",
                    component_type="WorkatoRecipeFunction",
                    params=params,
                )

                job = ParsedJob(
                    name=full_name,
                    path=str(func_path.relative_to(root)) if root in func_path.parents or root == func_path.parent else func_path.name,
                    purpose=data.get("description", ""),
                    components=[comp],
                    trunjob_refs=trunjob_refs,
                    group=group,
                    artifact_kind="recipe_function",
                )
                self._parsed_recipe_data[job.path] = data
                jobs.append(job)
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"  [yellow]Warning: Failed to parse {func_path}: {e}[/yellow]")
                self._warn(kind="parse_error", file=func_path, message=str(e), phase="function")
                continue
        return jobs

    def _parse_recipe(self, recipe_path: Path, root: Path) -> ParsedJob | None:
        """Parse a single recipe file into a ParsedJob with raw JSON passthrough."""
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

        # Merge sibling steps.json (nested export format)
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

        # Parse inline code JSON string into the dict
        code_raw = data.get("code")
        if isinstance(code_raw, str):
            try:
                data["code"] = json.loads(code_raw)
            except json.JSONDecodeError:
                pass

        # Strip noise keys recursively
        self._strip_noise_keys(data)

        # Detect call_recipe references from raw JSON
        trunjob_refs = self._find_call_recipe_refs(data)

        # Build single component with all cleaned recipe keys as params
        params = self._dict_to_params(data)
        conns_used: set[str] = set()
        self._scan_connection_refs(data, conns_used)
        comp = Component(
            unique_name=re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower() or "recipe",
            component_type="WorkatoRecipe",
            params=params,
            metadata_connection_ref=",".join(sorted(conns_used)),
        )

        job = ParsedJob(
            name=name,
            path=str(recipe_path.relative_to(root)) if root in recipe_path.parents or root == recipe_path.parent else recipe_path.name,
            purpose=data.get("description", ""),
            version=str(data.get("version_no", "")),
            components=[comp],
            trunjob_refs=trunjob_refs,
            group=group,
            artifact_kind="recipe",
        )
        # Path is unique within the exported project whereas display recipe
        # names are not necessarily unique.
        self._parsed_recipe_data[job.path] = data
        return job

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
                    artifact_kind="api_endpoint",
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
                        artifact_kind="api_endpoint",
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
                    artifact_kind="lcap_app",
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

    # Internal providers to skip when extracting external systems
    _INTERNAL_PROVIDERS: frozenset[str] = frozenset({
        "clock", "workato", "workato_scheduler", "logger",
    })

    def _extract_external_systems(self, recipe_data: dict) -> list[str]:
        """Extract external system display names from a recipe's provider fields."""
        providers: set[str] = set()
        self._collect_providers(recipe_data, providers)
        # Filter internal providers
        providers -= self._INTERNAL_PROVIDERS
        # Map to display names
        systems: set[str] = set()
        for p in providers:
            display = _PROVIDER_TYPE_MAP.get(p, p.replace("_", " ").title())
            systems.add(display)
        return sorted(systems)

    @staticmethod
    def _collect_providers(obj: object, providers: set[str]) -> None:
        """Recursively collect provider/application values from parsed recipe JSON."""
        if isinstance(obj, dict):
            for key in ("provider", "application", "trigger_application"):
                val = obj.get(key)
                if isinstance(val, str) and val:
                    providers.add(val)
            apps = obj.get("action_applications")
            if isinstance(apps, list):
                for app in apps:
                    if isinstance(app, str) and app:
                        providers.add(app)
            for val in obj.values():
                WorkatoParser._collect_providers(val, providers)
        elif isinstance(obj, list):
            for item in obj:
                WorkatoParser._collect_providers(item, providers)

    def _build_manifest(
        self, jobs: list[ParsedJob], routines: list[Routine],
        source_roots: list[dict[str, str]],
    ) -> ProjectManifest:
        """Build cross-reference manifest from parsed data."""
        job_calls: dict[str, list[str]] = {}
        job_called_by: dict[str, list[str]] = {}
        job_connections_used: dict[str, list[str]] = {}
        connection_used_by: dict[str, list[str]] = {}
        job_contexts_used: dict[str, list[str]] = {}
        routines_used_by: dict[str, list[str]] = {}
        job_groups: dict[str, str] = {}
        job_external_systems: dict[str, list[str]] = {}
        artifact_kinds: dict[str, str] = {}
        artifact_relations: list[dict] = []
        artifact_endpoints: list[dict] = []
        artifact_resources: list[dict] = []

        job_ids_by_name: dict[str, list[str]] = {}
        for job in jobs:
            job_ids_by_name.setdefault(job.name, []).append(job.job_id or job.name)

        for job in jobs:
            job_key = job.job_id or job.name
            job_groups[job_key] = job.group
            artifact_kinds[job_key] = job.artifact_kind

            # Recipe calls (from TRunJobRef)
            calls = [ref.child_job for ref in job.trunjob_refs]
            if calls:
                job_calls[job_key] = calls
                for called in calls:
                    candidates = job_ids_by_name.get(called, [])
                    if len(candidates) == 1:
                        target = candidates[0]
                        job_called_by.setdefault(target, []).append(job_key)
                        artifact_relations.append({
                            "source": job_key,
                            "target": target,
                            "relation_type": "job_calls_job",
                            "resolution": "resolved",
                            "provenance": "call_recipe",
                        })
                    else:
                        resolution = "ambiguous" if candidates else "unresolved"
                        project_manifest_entry = {
                            "source": job_key,
                            "target": "",
                            "target_name": called,
                            "relation_type": "job_calls_job",
                            "resolution": resolution,
                            "provenance": "call_recipe",
                        }
                        artifact_relations.append(project_manifest_entry)

            # Connection usage -- scan raw recipe data
            conns_used: set[str] = set()
            raw = self._parsed_recipe_data.get(job.path, {})
            if raw:
                self._scan_connection_refs(raw, conns_used)
            if conns_used:
                job_connections_used[job_key] = sorted(conns_used)
                for conn in conns_used:
                    connection_used_by.setdefault(conn, []).append(job_key)

            # External systems (derived from provider fields)
            if raw:
                ext_systems = self._extract_external_systems(raw)
                if ext_systems:
                    job_external_systems[job_key] = ext_systems

            # Trigger -> inbound endpoint. Action steps -> outbound/lookup
            # connector resources. Both walk the raw recipe/function JSON
            # already captured for connection scanning above; no new parse.
            if raw:
                trigger = _workato_extract_trigger(raw)
                if trigger:
                    provider = trigger["provider"]
                    artifact_endpoints.append({
                        "artifact": job_key,
                        "endpoint_type": _workato_display_system(provider),
                        "direction": "input",
                        "component_name": trigger.get("as") or trigger.get("name") or "trigger",
                        "provenance": f"workato_trigger:{provider}",
                    })

                actions: list[dict] = []
                _workato_collect_action_steps(raw, actions)
                for action in actions:
                    provider = action["provider"]
                    if provider in self._INTERNAL_PROVIDERS:
                        # call_recipe, table lookups, clock/logger actions are
                        # Workato control-flow, not an external connector --
                        # call_recipe is already captured as artifact_relations.
                        continue
                    artifact_resources.append({
                        "artifact": job_key,
                        "resource_type": "connector_action",
                        "resource": _workato_display_system(provider),
                        "direction": _workato_action_direction(action["action_name"] or action["component_name"]),
                        "component_name": action["component_name"],
                        "provenance": f"workato_action:{provider}",
                    })

            # Exposed API endpoints (Workato API Platform assets) are
            # structurally inbound surfaces, same as Talend's cREST/cSOAP
            # route endpoints. Method/URL stay out of the record -- same
            # redaction posture as the Talend route endpoint records.
            for comp in job.components:
                if comp.component_type == "WorkatoAPIEndpoint":
                    artifact_endpoints.append({
                        "artifact": job_key,
                        "endpoint_type": "api_endpoint",
                        "direction": "input",
                        "component_name": comp.unique_name,
                        "provenance": comp.component_type,
                    })

        return ProjectManifest(
            job_calls=job_calls,
            job_called_by=job_called_by,
            job_connections_used=job_connections_used,
            connection_used_by=connection_used_by,
            job_contexts_used=job_contexts_used,
            routines_used_by=routines_used_by,
            job_groups=job_groups,
            job_external_systems=job_external_systems,
            artifact_kinds=artifact_kinds,
            artifact_relations=artifact_relations,
            artifact_endpoints=artifact_endpoints,
            artifact_resources=artifact_resources,
            source_roots=source_roots,
            job_source_roots={j.job_id or j.name: j.source_root for j in jobs},
        )

    # ── Raw JSON Helpers ─────────────────────────────────────────────

    def _strip_noise_keys(self, obj: object) -> None:
        """Recursively remove noise keys from dicts at all nesting levels."""
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                if key in self._NOISE_KEYS:
                    del obj[key]
                else:
                    self._strip_noise_keys(obj[key])
        elif isinstance(obj, list):
            for item in obj:
                self._strip_noise_keys(item)

    def _find_call_recipe_refs(self, obj: object) -> list[TRunJobRef]:
        """Walk JSON recursively, find call_recipe actions, return TRunJobRefs."""
        refs: list[TRunJobRef] = []
        if isinstance(obj, dict):
            if obj.get("name") == "call_recipe" or obj.get("action") == "call_recipe":
                input_data = obj.get("input", {})
                recipe_id = str(input_data.get("recipe_id", "")) if isinstance(input_data, dict) else ""
                if recipe_id:
                    recipe_name = self._recipe_id_index.get(recipe_id, f"recipe_{recipe_id}")
                    refs.append(TRunJobRef(child_job=recipe_name, child_job_xmi_id=recipe_id))
            for val in obj.values():
                refs.extend(self._find_call_recipe_refs(val))
        elif isinstance(obj, list):
            for item in obj:
                refs.extend(self._find_call_recipe_refs(item))
        return refs

    def _scan_connection_refs(self, obj: object, conns: set[str]) -> None:
        """Recursively scan JSON for connection references."""
        if isinstance(obj, dict):
            # Numeric connection_id / account_id
            conn_id = obj.get("connection_id", obj.get("account_id"))
            if conn_id is not None:
                try:
                    name = self._connection_id_index.get(int(conn_id))
                    if name:
                        conns.add(name)
                except (ValueError, TypeError):
                    alias_name = self._connection_alias_index.get(str(conn_id))
                    if alias_name:
                        conns.add(alias_name)
            # Provider / application / trigger_application fields
            for key in ("provider", "application", "trigger_application"):
                val = obj.get(key)
                if isinstance(val, str) and val:
                    if val in self._connection_index:
                        conns.add(val)
                    elif val in self._provider_to_conn:
                        conns.add(self._provider_to_conn[val])
            # action_applications list
            apps = obj.get("action_applications")
            if isinstance(apps, list):
                for app in apps:
                    if isinstance(app, str):
                        if app in self._connection_index:
                            conns.add(app)
                        elif app in self._provider_to_conn:
                            conns.add(self._provider_to_conn[app])
            # connections_used list
            cu = obj.get("connections_used")
            if isinstance(cu, list):
                for c in cu:
                    if isinstance(c, str) and c in self._connection_index:
                        conns.add(c)
            # Recurse into values
            for val in obj.values():
                self._scan_connection_refs(val, conns)
        elif isinstance(obj, list):
            for item in obj:
                self._scan_connection_refs(item, conns)

    @staticmethod
    def _dict_to_params(data: dict) -> list[ComponentParam]:
        """Convert a dict's top-level keys to ComponentParam list."""
        params: list[ComponentParam] = []
        for key, val in data.items():
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                val_str = json.dumps(val)
                field_type = "INPUT"
            else:
                val_str = str(val)
                field_type = "TEXT"
            if val_str.strip():
                params.append(ComponentParam(name=key, value=val_str, field_type=field_type))
        return params

    # ── Helpers ──────────────────────────────────────────────────────

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
