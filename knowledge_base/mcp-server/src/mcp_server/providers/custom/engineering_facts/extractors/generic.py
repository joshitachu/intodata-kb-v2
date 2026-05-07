"""Generic EngineeringFacts extractor for normal code repositories."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.extractors.structured import (
    StructuredScanFactExtractor,
    component_name,
    job_name,
    job_path,
)
from mcp_server.providers.custom.models import FactsManifest


class GenericFactExtractor(StructuredScanFactExtractor):
    """Extract code-oriented facts on top of shared structured scan facts."""

    platform = "generic"

    _ROUTE_RE = re.compile(r"@(app|router|bp)\.(get|post|put|patch|delete|route)\(([^)]*)\)")
    _EXPRESS_ROUTE_RE = re.compile(r"\b(app|router)\.(get|post|put|patch|delete)\((['\"])([^'\"]+)\2")
    _ENV_RE = re.compile(r"\b(?:os\.environ(?:\.get)?|os\.getenv|process\.env)\[?\(?['\"]([A-Z0-9_]+)['\"]")
    _IMPORT_JS_RE = re.compile(
        r"^\s*(?:import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]|const\s+.+?=\s+require\(['\"]([^'\"]+)['\"]\))",
        re.MULTILINE,
    )
    _CONFIG_SUFFIXES = {
        ".config", ".xml", ".json", ".yaml", ".yml", ".toml", ".env", ".properties", ".ini", ".cfg", ".conf"
    }
    _STARTUP_NAMES = {
        "global.asax", "global.asax.cs", "startup.cs", "program.cs", "main.cs", "app.cs",
        "startup.java", "application.java", "main.java", "server.js", "app.js", "index.js",
        "main.py", "app.py",
    }
    _KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][\w:.\-]*)\s*[:=]\s*(.+?)\s*$", re.MULTILINE)
    _CSTRING_KEY_RE = re.compile(r"(?:connectionstring|connection_string|databaseurl|database_url|dburl|db_url)$", re.I)
    _SECRET_KEY_RE = re.compile(r"(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|license[_-]?key)", re.I)
    _ORM_FACTORY_RE = re.compile(r"\bOrmLiteConnectionFactory\s*\((?P<args>[\s\S]*?)\)\s*;", re.IGNORECASE)
    _REGISTER_CONNECTION_RE = re.compile(r"\bRegisterConnection\s*\(\s*([\"'])(?P<name>[^\"']+)\1(?P<args>[\s\S]*?)\)\s*;", re.IGNORECASE)
    _NAMED_CONNECTION_RE = re.compile(r"(?:GetConnectionString|ConnectionStrings)\s*(?:\(|\[)\s*([\"'])(?P<name>[^\"']+)\1", re.IGNORECASE)

    def extract_platform(
        self,
        builder: EngineeringFactsBuilder,
        scan: dict[str, Any],
        facts_manifest: FactsManifest,
    ) -> None:
        for job in scan.get("jobs", []) or []:
            for component in job.get("components", []) or []:
                unit = job_name(job)
                path = _component_path(component) or job_path(job)
                cname = component_name(component)
                for param in component.get("params", []) or []:
                    if str(param.get("name") or "").lower() == "content":
                        self._extract_code_content(builder, unit, path, cname, str(param.get("value") or ""))
        self._extract_manifest(builder, scan.get("manifest", {}) if isinstance(scan.get("manifest"), dict) else {})

    def _extract_code_content(self, builder: EngineeringFactsBuilder, unit: str, path: str, component_label: str, content: str) -> None:
        if not content:
            return
        suffix = Path(path).suffix.lower()
        if suffix == ".py" or "def " in content or "class " in content:
            self._extract_python(builder, unit, path, content)
        self._extract_text_patterns(builder, unit, path, component_label, content)
        if self._is_config_path(path, component_label):
            self._extract_config_content(builder, unit, path, content)
        if self._is_startup_path(path):
            self._extract_startup_content(builder, unit, path, component_label, content)

    def _extract_python(self, builder: EngineeringFactsBuilder, unit: str, path: str, content: str) -> None:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbol = node.name
                etype = "class" if isinstance(node, ast.ClassDef) else "function"
                ev = [builder.evidence(path, unit_name=unit, line=node.lineno, symbol=symbol, excerpt=_line_at(content, node.lineno), confidence=0.95)]
                entity = builder.add_entity(entity_type=etype, name=symbol, path=path, unit_name=unit, evidence=ev)
                builder.add_usage(usage_type="defined", entity_id=entity.id, name=symbol, location=path, evidence=ev)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for imported in names:
                    ev = [builder.evidence(path, unit_name=unit, line=getattr(node, "lineno", None), source_key="import", excerpt=_line_at(content, getattr(node, "lineno", 1)), confidence=0.9)]
                    builder.add_usage(usage_type="imported", entity_id=None, name=imported, location=path, evidence=ev)
                    builder.add_dependency(source=unit, target=imported, dependency_type="import", evidence=ev)
            elif isinstance(node, ast.Call):
                called = _call_name(node.func)
                if called:
                    ev = [builder.evidence(path, unit_name=unit, line=getattr(node, "lineno", None), symbol=called, excerpt=_line_at(content, getattr(node, "lineno", 1)), confidence=0.75)]
                    builder.add_usage(usage_type="called", entity_id=None, name=called, location=path, evidence=ev, confidence=0.75)

    def _extract_text_patterns(self, builder: EngineeringFactsBuilder, unit: str, path: str, component_label: str, content: str) -> None:
        for match in self._ROUTE_RE.finditer(content):
            method = match.group(2).upper()
            route = _first_string(match.group(3)) or "<dynamic-route>"
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), symbol=component_label, excerpt=match.group(0), confidence=0.8)]
            builder.add_api_surface(name=f"{method} {route}", route=route, method=method, handler=component_label, evidence=ev)
        for match in self._EXPRESS_ROUTE_RE.finditer(content):
            method = match.group(2).upper()
            route = match.group(4)
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), excerpt=match.group(0), confidence=0.8)]
            builder.add_api_surface(name=f"{method} {route}", route=route, method=method, handler=component_label, evidence=ev)
        for match in self._ENV_RE.finditer(content):
            key = match.group(1)
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), source_key=key, excerpt=match.group(0), confidence=0.85)]
            builder.add_config_runtime(name=key, config_type="env_var", key=key, evidence=ev)
            builder.add_usage(usage_type="configured", entity_id=None, name=key, location=path, evidence=ev)
        for match in self._IMPORT_JS_RE.finditer(content):
            imported = match.group(1) or match.group(2)
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), source_key="import", excerpt=match.group(0), confidence=0.8)]
            builder.add_usage(usage_type="imported", entity_id=None, name=imported, location=path, evidence=ev)
            builder.add_dependency(source=unit, target=imported, dependency_type="import", evidence=ev)

    def _is_config_path(self, path: str, component_label: str) -> bool:
        suffix = Path(path).suffix.lower()
        name = Path(path).name.lower()
        return suffix in self._CONFIG_SUFFIXES or name in {"web.config", "app.config"} or "config" in component_label.lower()

    def _is_startup_path(self, path: str) -> bool:
        return Path(path).name.lower() in self._STARTUP_NAMES

    def _extract_config_content(self, builder: EngineeringFactsBuilder, unit: str, path: str, content: str) -> None:
        if not content or content.startswith("[FILE TOO LARGE") or content.startswith("[BINARY"):
            return
        ev = [builder.evidence(path, unit_name=unit, source_key="content", excerpt="config file present; content scanned", confidence=0.95)]
        builder.add_config_runtime(
            name=path,
            config_type="config_file",
            key=Path(path).name,
            value_hint="content scanned; sensitive values redacted",
            evidence=ev,
            metadata={"path": path},
        )
        suffix = Path(path).suffix.lower()
        if suffix in {".xml", ".config"} or _looks_like_xml(content):
            self._extract_xml_config(builder, unit, path, content)
            return
        if suffix == ".json":
            self._extract_json_config(builder, unit, path, content)
            return
        self._extract_key_value_config(builder, unit, path, content)

    def _extract_xml_config(self, builder: EngineeringFactsBuilder, unit: str, path: str, content: str) -> None:
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            self._extract_key_value_config(builder, unit, path, content)
            return
        for elem in root.iter():
            tag = _strip_namespace(elem.tag)
            if elem is root:
                continue
            section_path = _xml_section_path(elem, tag)
            ev = [builder.evidence(path, unit_name=unit, line=_line_for_xml_tag(content, tag), source_key=section_path, excerpt=f"<{tag}> section present", confidence=0.85)]
            builder.add_config_runtime(name=section_path, config_type="config_section", key=tag, value_hint="section present", evidence=ev)

            attrs = {str(k): str(v) for k, v in elem.attrib.items()}
            name = attrs.get("name") or attrs.get("key")
            value = attrs.get("value") or attrs.get("connectionString") or ""
            if tag == "add" and name:
                parent_hint = _parent_section_hint(content, name)
                if "connectionString" in attrs or self._CSTRING_KEY_RE.search(name):
                    self._add_connection_config(builder, unit, path, name, value, attrs.get("providerName") or "", _line_for_text(content, name))
                else:
                    self._add_setting(builder, unit, path, parent_hint or "xml_setting", name, value, _line_for_text(content, name))

    def _extract_json_config(self, builder: EngineeringFactsBuilder, unit: str, path: str, content: str) -> None:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            self._extract_key_value_config(builder, unit, path, content)
            return
        if not isinstance(data, dict):
            return
        for key, value in _flatten(data).items():
            if self._CSTRING_KEY_RE.search(key) or "connectionstrings." in key.lower():
                self._add_connection_config(builder, unit, path, key.split(".")[-1], str(value), "", _line_for_text(content, key.split(".")[-1]))
            else:
                self._add_setting(builder, unit, path, "json_setting", key, str(value), _line_for_text(content, key.split(".")[-1]))

    def _extract_key_value_config(self, builder: EngineeringFactsBuilder, unit: str, path: str, content: str) -> None:
        for match in self._KEY_VALUE_RE.finditer(content):
            key = match.group(1).strip().strip("\"'")
            raw_value = match.group(2).strip().strip(",").strip().strip("\"'")
            if not key:
                continue
            line = _line_no(content, match.start())
            if self._CSTRING_KEY_RE.search(key) or "connectionstrings:" in key.lower() or "connectionstrings." in key.lower():
                self._add_connection_config(builder, unit, path, key.split(":")[-1].split(".")[-1], raw_value, "", line)
            else:
                self._add_setting(builder, unit, path, "setting", key, raw_value, line)

    def _add_setting(self, builder: EngineeringFactsBuilder, unit: str, path: str, source: str, key: str, value: str, line: int | None) -> None:
        safe_hint = _safe_value_hint(key, value)
        ev = [builder.evidence(path, unit_name=unit, line=line, source_key=key, excerpt=f"{key}={safe_hint}", confidence=0.85)]
        builder.add_config_runtime(name=key, config_type="setting", key=key, value_hint=safe_hint, evidence=ev, metadata={"config_source": source, "path": path})
        builder.add_usage(usage_type="configured", entity_id=None, name=key, location=path, evidence=ev)
        if _has_inline_secret(key, value):
            self._add_inline_secret_risk(builder, unit, path, key, line)

    def _add_connection_config(self, builder: EngineeringFactsBuilder, unit: str, path: str, name: str, value: str, provider: str, line: int | None) -> None:
        name = name or "connectionString"
        metadata = _connection_metadata(value, provider)
        safe_hint = _connection_hint(metadata)
        ev = [builder.evidence(path, unit_name=unit, line=line, source_key=name, excerpt=f"{name} connectionString=[REDACTED]", confidence=0.9)]
        builder.add_config_runtime(
            name=name,
            config_type="connection_string",
            key=name,
            value_hint=safe_hint,
            evidence=ev,
            metadata={"path": path, **metadata},
        )
        builder.add_entity(entity_type="connection", name=name, path=path, unit_name=unit, evidence=ev, metadata=metadata)
        builder.add_dependency(source=path, target=name, dependency_type="defines_connection", evidence=ev)
        builder.add_usage(usage_type="configured", entity_id=None, name=name, location=path, evidence=ev)
        if _has_inline_secret(name, value):
            self._add_inline_secret_risk(builder, unit, path, name, line)

    def _extract_startup_content(self, builder: EngineeringFactsBuilder, unit: str, path: str, component_label: str, content: str) -> None:
        for match in self._ORM_FACTORY_RE.finditer(content):
            metadata = _connection_metadata(match.group("args"), "")
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), symbol=component_label, excerpt="OrmLiteConnectionFactory([REDACTED])", confidence=0.85)]
            builder.add_config_runtime(name="OrmLiteConnectionFactory", config_type="db_registration", key="OrmLiteConnectionFactory", value_hint=_connection_hint(metadata), evidence=ev, metadata=metadata)
            builder.add_dependency(source=unit, target="OrmLiteConnectionFactory", dependency_type="registers_connection_factory", evidence=ev)
        for match in self._REGISTER_CONNECTION_RE.finditer(content):
            name = match.group("name")
            metadata = _connection_metadata(match.group("args"), "")
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), source_key=name, excerpt=f"RegisterConnection(\"{name}\", [REDACTED])", confidence=0.9)]
            builder.add_config_runtime(name=name, config_type="db_registration", key=name, value_hint=_connection_hint(metadata), evidence=ev, metadata=metadata)
            builder.add_dependency(source=unit, target=name, dependency_type="startup_uses_connection", evidence=ev)
            builder.add_usage(usage_type="configured", entity_id=None, name=name, location=path, evidence=ev)
        for match in self._NAMED_CONNECTION_RE.finditer(content):
            name = match.group("name")
            ev = [builder.evidence(path, unit_name=unit, line=_line_no(content, match.start()), source_key=name, excerpt=f"GetConnectionString(\"{name}\")", confidence=0.8)]
            builder.add_config_runtime(name=name, config_type="connection_reference", key=name, value_hint="startup reads named connection string", evidence=ev)
            builder.add_dependency(source=unit, target=name, dependency_type="startup_uses_connection", evidence=ev)

    def _add_inline_secret_risk(self, builder: EngineeringFactsBuilder, unit: str, path: str, key: str, line: int | None) -> None:
        ev = [builder.evidence(path, unit_name=unit, line=line, source_key=key, excerpt=f"{key}=[REDACTED]", confidence=0.85)]
        builder.add_known_risk(
            name=f"{path} contains inline sensitive configuration for {key}",
            severity="HIGH",
            affected_entity=path,
            risk_type="inline_secret_config",
            remediation="Move sensitive values to managed secrets and keep only references in source-controlled config.",
            evidence=ev,
            confidence=0.85,
        )

    def _extract_manifest(self, builder: EngineeringFactsBuilder, manifest: dict[str, Any]) -> None:
        scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
        if isinstance(scripts, dict):
            for name, command in sorted(scripts.items()):
                ev = [builder.evidence("package manifest", source_key=f"scripts.{name}", excerpt=str(command), confidence=0.9)]
                builder.add_config_runtime(name=name, config_type="package_script", key=name, value_hint=str(command), evidence=ev)
        deps = manifest.get("dependencies") if isinstance(manifest, dict) else None
        if isinstance(deps, dict):
            for name in sorted(deps):
                ev = [builder.evidence("package manifest", source_key=f"dependencies.{name}", confidence=0.8)]
                builder.add_dependency(source="project", target=name, dependency_type="package_dependency", evidence=ev, confidence=0.8)


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = _call_name(func.value)
        return f"{prefix}.{func.attr}" if prefix else func.attr
    return None


def _line_no(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _line_at(content: str, line: int) -> str:
    lines = content.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _first_string(text: str) -> str | None:
    try:
        value = ast.literal_eval(text.split(",", 1)[0].strip())
        return str(value)
    except Exception:
        pass
    match = re.search(r"['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def _component_path(component: dict[str, Any]) -> str | None:
    for param in component.get("params", []) or []:
        if str(param.get("name") or "").lower() == "file_path" and param.get("value"):
            return str(param.get("value"))
    return None


def _looks_like_xml(content: str) -> bool:
    return content.lstrip().startswith("<")


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_section_path(elem: ElementTree.Element, tag: str) -> str:
    name = elem.get("name") or elem.get("key")
    return f"{tag}:{name}" if name else tag


def _parent_section_hint(content: str, name: str) -> str:
    before = content[: content.find(name)] if name in content else ""
    matches = re.findall(r"<([A-Za-z][\w:.-]*)\b[^>]*>", before)
    return _strip_namespace(matches[-1]) if matches else "xml_setting"


def _line_for_xml_tag(content: str, tag: str) -> int | None:
    match = re.search(rf"<(?:\w+:)?{re.escape(tag)}\b", content)
    return _line_no(content, match.start()) if match else None


def _line_for_text(content: str, text: str) -> int | None:
    if not text:
        return None
    idx = content.find(text)
    return _line_no(content, idx) if idx >= 0 else None


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        elif isinstance(value, (str, int, float, bool)):
            out[path] = value
    return out


def _safe_value_hint(key: str, value: str) -> str:
    if _has_inline_secret(key, value):
        return "[REDACTED]"
    if not value:
        return "empty"
    lowered = value.lower()
    labels: list[str] = []
    for marker in ("encrypt=", "encrypt=true", "encrypt=false", "trustservercertificate=", "persist security info=", "timeout=", "connect timeout="):
        if marker in lowered:
            labels.append(marker.rstrip("="))
    if labels:
        return ", ".join(dict.fromkeys(labels))
    return "value present"


def _has_inline_secret(key: str, value: str) -> bool:
    if not value or value.strip() in {"[REDACTED]", "***", "****"}:
        return False
    return bool(GenericFactExtractor._SECRET_KEY_RE.search(key) or re.search(r"(password|pwd|user id|uid)\s*=", value, re.I))


def _connection_metadata(value: str, provider: str) -> dict[str, str]:
    combined = f"{provider} {value}".lower()
    db_type = ""
    for marker, label in [
        ("sqlclient", "SQL Server"), ("sqlserver", "SQL Server"), ("mssql", "SQL Server"),
        ("postgresql", "PostgreSQL"), ("postgres", "PostgreSQL"), ("npgsql", "PostgreSQL"),
        ("mysql", "MySQL"), ("oracle", "Oracle"), ("mongodb", "MongoDB"), ("sqlite", "SQLite"),
    ]:
        if marker in combined:
            db_type = label
            break
    flags: dict[str, str] = {}
    for key, label in [
        ("encrypt", "encrypt"),
        ("trustservercertificate", "trust_server_certificate"),
        ("persist security info", "persist_security_info"),
        ("connect timeout", "connect_timeout"),
        ("connection timeout", "connection_timeout"),
        ("timeout", "timeout"),
    ]:
        match = re.search(rf"{re.escape(key)}\s*=\s*([^;,\s]+)", value, re.I)
        if match:
            flags[label] = match.group(1)
    metadata = {"provider": provider, "database_type": db_type}
    metadata.update(flags)
    return {k: v for k, v in metadata.items() if v}


def _connection_hint(metadata: dict[str, str]) -> str:
    parts = []
    if metadata.get("database_type"):
        parts.append(f"database_type={metadata['database_type']}")
    if metadata.get("provider"):
        parts.append(f"provider={metadata['provider']}")
    for key in ("encrypt", "trust_server_certificate", "persist_security_info", "connect_timeout", "connection_timeout", "timeout"):
        if key in metadata:
            parts.append(f"{key}={metadata[key]}")
    return "; ".join(parts) if parts else "connection string present; values redacted"
