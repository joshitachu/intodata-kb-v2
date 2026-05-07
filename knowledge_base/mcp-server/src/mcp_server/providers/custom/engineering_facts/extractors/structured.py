"""Shared extractor for parsed scanner output.

This adapts platform-neutral scan concepts once: files, jobs, components,
component params, component connections, metadata connections, context groups,
routines, and manifest references.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.extractors.base import EngineeringFactExtractor
from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts
from mcp_server.providers.custom.models import FactsManifest


_CONFIG_KEYWORDS = ("url", "host", "endpoint", "database", "server", "port", "schema", "path", "dir", "file")
_QUERY_KEYWORDS = ("query", "sql", "statement")
_ERROR_CONNECTORS = {"SUBJOB_ERROR", "COMPONENT_ERROR", "ERROR", "ON_ERROR"}
_CONTEXT_REF_RE = re.compile(r"\bcontext\.([A-Za-z_][A-Za-z0-9_]*)")
_ROUTINE_CALL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")


class StructuredScanFactExtractor(EngineeringFactExtractor):
    """Base strategy for platforms represented by the shared scan schema."""

    platform = "structured"

    def extract(self, project_id: str, scan: dict[str, Any], facts_manifest: FactsManifest) -> EngineeringFacts:
        platform = str(scan.get("platform") or self.platform or "generic").lower()
        builder = EngineeringFactsBuilder(project_id, platform)
        self.extract_shared(builder, scan, facts_manifest)
        self.extract_platform(builder, scan, facts_manifest)
        self.infer_missing_tests(builder)
        return builder.build()

    def extract_platform(
        self,
        builder: EngineeringFactsBuilder,
        scan: dict[str, Any],
        facts_manifest: FactsManifest,
    ) -> None:
        """Platform-specific enrichment hook."""

    def extract_shared(
        self,
        builder: EngineeringFactsBuilder,
        scan: dict[str, Any],
        facts_manifest: FactsManifest,
    ) -> None:
        self.extract_files(builder, scan.get("files", []) or [])
        self.extract_metadata_connections(builder, scan.get("metadata_connections", []) or [])
        self.extract_context_groups(builder, scan.get("context_groups", []) or [], path="context")
        self.extract_routines(builder, scan.get("routines", []) or [])
        for job in scan.get("jobs", []) or []:
            self.extract_job(builder, job)
        self.extract_manifest(builder, scan.get("manifest", {}) if isinstance(scan.get("manifest"), dict) else {})

    def extract_files(self, builder: EngineeringFactsBuilder, files: list[dict[str, Any]]) -> None:
        for item in files:
            rel = str(item.get("relative_path") or item.get("path") or "")
            if not rel:
                continue
            ftype = str(item.get("file_type") or "file")
            ev = [builder.evidence(rel, source_key="files_index", confidence=0.9)]
            builder.add_entity(entity_type="file", name=rel, path=rel, evidence=ev, metadata={"file_type": ftype})
            if is_test_path(rel):
                builder.add_test_coverage(name=f"Test file {rel}", test_file=rel, evidence=ev, confidence=0.8)

    def extract_job(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        group = str(job.get("group") or "Global")
        ev = [builder.evidence(path, unit_name=unit, source_key="jobs", confidence=1.0)]
        entity = builder.add_entity(
            entity_type=self.job_entity_type(job),
            name=unit,
            path=path,
            unit_name=unit,
            evidence=ev,
            metadata={
                "group": group,
                "subgroup": job.get("subgroup") or "",
                "version": job.get("version") or "",
                "purpose": job.get("purpose") or "",
            },
        )
        builder.add_usage(usage_type="defined", entity_id=entity.id, name=unit, location=path, evidence=ev)
        builder.add_architecture(
            name=unit,
            layer=group,
            component=unit,
            boundary=group,
            evidence=ev,
            description="Scanner unit grouped by parsed project structure.",
        )

        self.extract_context_groups(builder, job.get("context_groups", []) or [], path=path, unit_name=unit)
        for component in job.get("components", []) or []:
            self.extract_component(builder, job, component, parent_entity_id=entity.id)
        for connection in job.get("connections", []) or []:
            self.extract_component_connection(builder, job, connection)
        self.extract_job_manifest_like_refs(builder, job)
        self.extract_generic_job_risks(builder, job)

    def job_entity_type(self, job: dict[str, Any]) -> str:
        return "module"

    def extract_component(
        self,
        builder: EngineeringFactsBuilder,
        job: dict[str, Any],
        component: dict[str, Any],
        *,
        parent_entity_id: str,
    ) -> None:
        unit = job_name(job)
        path = job_path(job)
        cname = component_name(component)
        ctype = str(component.get("component_type") or "component")
        ev = [builder.evidence(path, unit_name=unit, symbol=cname, source_key="components", confidence=0.95)]
        comp_entity = builder.add_entity(
            entity_type=component_entity_type(ctype),
            name=cname,
            path=path,
            unit_name=unit,
            evidence=ev,
            metadata={"component_type": ctype, "parent_entity_id": parent_entity_id},
        )
        builder.add_usage(usage_type="defined", entity_id=comp_entity.id, name=cname, location=path, evidence=ev)
        builder.add_dependency(source=unit, target=cname, dependency_type="contains", evidence=ev)
        metadata_ref = str(component.get("metadata_connection_ref") or "")
        if metadata_ref:
            builder.add_dependency(source=cname, target=metadata_ref, dependency_type="uses_connection", evidence=ev)
            builder.add_usage(usage_type="configured", entity_id=None, name=metadata_ref, location=cname, evidence=ev)
            builder.add_impact(
                changed_entity=metadata_ref,
                impacted_entity=unit,
                check=f"Review component {cname} connection behavior.",
                evidence=ev,
            )
        self.extract_component_params(builder, job, component, cname)

    def extract_component_params(
        self,
        builder: EngineeringFactsBuilder,
        job: dict[str, Any],
        component: dict[str, Any],
        component_label: str,
    ) -> None:
        unit = job_name(job)
        path = job_path(job)
        for param in component.get("params", []) or []:
            key = str(param.get("name") or "")
            value = str(param.get("value") or "")
            if not key:
                continue
            lowered = key.lower()
            ev = [builder.evidence(path, unit_name=unit, symbol=component_label, source_key=key, excerpt=value, confidence=0.85)]
            if lowered in {"file_path", "path"}:
                builder.add_dependency(source=unit, target=value, dependency_type="contains_file", evidence=ev)
                builder.add_impact(changed_entity=value, impacted_entity=unit, check="Review behavior that depends on this path.", evidence=ev)
            if any(token in lowered for token in _CONFIG_KEYWORDS):
                builder.add_config_runtime(name=key, config_type="setting", key=key, value_hint=value[:160], evidence=ev)
                if any(token in lowered for token in ("url", "host", "endpoint", "database", "server")):
                    system = value[:160]
                    builder.add_entity(entity_type="external_system", name=system, path=path, unit_name=unit, evidence=ev, confidence=0.75)
                    builder.add_dependency(source=unit, target=system, dependency_type="external_system", evidence=ev, confidence=0.75)
            if any(token in lowered for token in _QUERY_KEYWORDS):
                builder.add_entity(entity_type="query", name=f"{unit}:{component_label}:{key}", path=path, unit_name=unit, evidence=ev)
                builder.add_usage(usage_type="queried", entity_id=None, name=value[:160], location=component_label, evidence=ev, confidence=0.75)
                builder.add_impact(
                    changed_entity=f"{component_label}.{key}",
                    impacted_entity=unit,
                    check="Review source/target schema, row counts, and downstream data flow.",
                    evidence=ev,
                )
                if "select *" in value.lower():
                    builder.add_known_risk(
                        name=f"{unit} uses SELECT * in {component_label}",
                        severity="MEDIUM",
                        affected_entity=component_label,
                        risk_type="unsafe_query_pattern",
                        remediation="Replace SELECT * with explicit column lists.",
                        evidence=ev,
                        confidence=0.85,
                    )
            self.extract_context_references(builder, unit, path, component_label, key, value, ev)
            self.extract_routine_references(builder, unit, path, component_label, key, value, ev)
            if is_hardcoded_runtime_value(key, value):
                builder.add_known_risk(
                    name=f"{unit} has hardcoded runtime value {key}",
                    severity="LOW",
                    affected_entity=component_label,
                    risk_type="hardcoded_config",
                    remediation="Move environment-dependent values to managed configuration.",
                    evidence=ev,
                    confidence=0.65,
                )

    def extract_context_references(
        self,
        builder: EngineeringFactsBuilder,
        unit: str,
        path: str,
        component_label: str,
        key: str,
        value: str,
        evidence,
    ) -> None:
        for match in _CONTEXT_REF_RE.finditer(value):
            var_name = match.group(1)
            builder.add_usage(usage_type="configured", entity_id=None, name=var_name, location=component_label, evidence=evidence)
            builder.add_dependency(source=unit, target=var_name, dependency_type="uses_context", evidence=evidence)
            builder.add_impact(
                changed_entity=var_name,
                impacted_entity=unit,
                check=f"Review context-dependent param {component_label}.{key}.",
                evidence=evidence,
            )

    def extract_routine_references(
        self,
        builder: EngineeringFactsBuilder,
        unit: str,
        path: str,
        component_label: str,
        key: str,
        value: str,
        evidence,
    ) -> None:
        for match in _ROUTINE_CALL_RE.finditer(value):
            routine = match.group(1)
            builder.add_usage(usage_type="called", entity_id=None, name=routine, location=component_label, evidence=evidence, confidence=0.7)
            builder.add_dependency(source=unit, target=routine, dependency_type="uses_routine", evidence=evidence, confidence=0.7)

    def extract_component_connection(self, builder: EngineeringFactsBuilder, job: dict[str, Any], connection: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        source = str(connection.get("source") or "")
        target = str(connection.get("target") or "")
        if not source or not target:
            return
        connector = str(connection.get("connector_name") or "flow")
        ev = [builder.evidence(path, unit_name=unit, source_key="connections", excerpt=str(connection.get("name") or connector), confidence=0.9)]
        builder.add_dependency(source=source, target=target, dependency_type=connector, evidence=ev)
        builder.add_data_flow(source=source, target=target, name=str(connection.get("name") or f"{source} to {target}"), evidence=ev, confidence=0.75)
        builder.add_impact(changed_entity=source, impacted_entity=target, check=f"Review {connector} behavior and downstream data flow.", evidence=ev, confidence=0.7)

    def extract_context_groups(
        self,
        builder: EngineeringFactsBuilder,
        groups: list[dict[str, Any]],
        *,
        path: str,
        unit_name: str | None = None,
    ) -> None:
        for group in groups:
            group_name = str(group.get("name") or "context")
            ev = [builder.evidence(path, unit_name=unit_name, source_key=f"context:{group_name}", confidence=0.9)]
            group_entity = builder.add_entity(entity_type="config", name=group_name, path=path, unit_name=unit_name, evidence=ev, metadata={"config_type": "context_group"})
            if unit_name:
                builder.add_dependency(source=unit_name, target=group_name, dependency_type="uses_context_group", evidence=ev)
            for variable in group.get("variables", []) or []:
                name = str(variable.get("name") or "")
                if not name:
                    continue
                values = variable.get("values") if isinstance(variable.get("values"), dict) else {}
                value_hint = ", ".join(f"{env}={value}" for env, value in sorted(values.items()))[:160]
                v_ev = [builder.evidence(path, unit_name=unit_name, source_key=f"context:{group_name}.{name}", excerpt=value_hint, confidence=0.9)]
                var_entity = builder.add_entity(entity_type="config", name=name, path=path, unit_name=unit_name, evidence=v_ev, metadata={"context_group": group_name, "type": variable.get("type") or ""})
                builder.add_config_runtime(name=name, config_type="context_var", key=name, value_hint=value_hint, evidence=v_ev)
                builder.add_dependency(source=group_name, target=name, dependency_type="defines_context_var", evidence=v_ev)
                builder.add_usage(usage_type="configured", entity_id=var_entity.id, name=name, location=group_name, evidence=v_ev)
                builder.add_impact(changed_entity=name, impacted_entity=unit_name or group_name, check="Review all jobs/components that reference this context variable.", evidence=v_ev)
                if len(set(values.values())) > 1:
                    builder.add_known_risk(
                        name=f"{name} differs by environment",
                        severity="LOW",
                        affected_entity=name,
                        risk_type="environment_divergence",
                        remediation="Verify each environment value before deployment.",
                        evidence=v_ev,
                        confidence=0.7,
                    )
                if group_entity.id:
                    builder.add_dependency(source=group_name, target=name, dependency_type="contains", evidence=v_ev)

    def extract_metadata_connections(self, builder: EngineeringFactsBuilder, connections: list[dict[str, Any]]) -> None:
        for conn in connections:
            name = str(conn.get("name") or conn.get("xmi_id") or "")
            if not name:
                continue
            ev = [builder.evidence("metadata_connections", source_key=name, excerpt=brief(conn), confidence=0.9)]
            builder.add_entity(entity_type="connection", name=name, evidence=ev, metadata={"connection_type": conn.get("connection_type") or ""})
            for key in ("host", "port", "database", "schema_name", "file_path"):
                value = str(conn.get(key) or "")
                if value:
                    builder.add_config_runtime(name=f"{name}.{key}", config_type="connection", key=key, value_hint=value[:160], evidence=ev)
                    builder.add_impact(changed_entity=f"{name}.{key}", impacted_entity=name, check="Review all jobs using this metadata connection.", evidence=ev)

    def extract_routines(self, builder: EngineeringFactsBuilder, routines: list[dict[str, Any]]) -> None:
        for routine in routines:
            name = str(routine.get("name") or "")
            if not name:
                continue
            ev = [builder.evidence("routines", symbol=name, source_key="routine", excerpt=str(routine.get("source_code") or "")[:300], confidence=0.85)]
            builder.add_entity(entity_type="function", name=name, evidence=ev, metadata={"is_system": routine.get("is_system", False)})
            builder.add_impact(changed_entity=name, impacted_entity="routine consumers", check="Review jobs and expressions that call this routine.", evidence=ev, confidence=0.65)

    def extract_manifest(self, builder: EngineeringFactsBuilder, manifest: dict[str, Any]) -> None:
        for parent, children in _dict_of_lists(manifest.get("job_calls")).items():
            for child in children:
                ev = [builder.evidence("manifest", source_key=f"job_calls.{parent}", excerpt=child, confidence=0.85)]
                builder.add_dependency(source=parent, target=child, dependency_type="calls_job", evidence=ev)
                builder.add_impact(changed_entity=child, impacted_entity=parent, check="Review parent orchestration and context passing.", evidence=ev)
        for job, conns in _dict_of_lists(manifest.get("job_connections_used")).items():
            for conn in conns:
                ev = [builder.evidence("manifest", source_key=f"job_connections_used.{job}", excerpt=conn, confidence=0.85)]
                builder.add_dependency(source=job, target=conn, dependency_type="uses_connection", evidence=ev)
        for job, contexts in _dict_of_lists(manifest.get("job_contexts_used")).items():
            for context in contexts:
                ev = [builder.evidence("manifest", source_key=f"job_contexts_used.{job}", excerpt=context, confidence=0.8)]
                builder.add_dependency(source=job, target=context, dependency_type="uses_context_group", evidence=ev)
        for routine, jobs in _dict_of_lists(manifest.get("routines_used_by")).items():
            for job in jobs:
                ev = [builder.evidence("manifest", source_key=f"routines_used_by.{routine}", excerpt=job, confidence=0.8)]
                builder.add_dependency(source=job, target=routine, dependency_type="uses_routine", evidence=ev)
        for job, systems in _dict_of_lists(manifest.get("job_external_systems")).items():
            for system in systems:
                ev = [builder.evidence("manifest", source_key=f"job_external_systems.{job}", excerpt=system, confidence=0.8)]
                builder.add_entity(entity_type="external_system", name=system, evidence=ev, confidence=0.8)
                builder.add_dependency(source=job, target=system, dependency_type="external_system", evidence=ev)

    def extract_job_manifest_like_refs(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        for ref in job.get("trunjob_refs", []) or []:
            child = str(ref.get("child_job") or "")
            if not child:
                continue
            ev = [builder.evidence(path, unit_name=unit, source_key="trunjob_refs", excerpt=brief(ref), confidence=0.95)]
            builder.add_dependency(source=unit, target=child, dependency_type="calls_job", evidence=ev)
            builder.add_usage(usage_type="called", entity_id=None, name=child, location=unit, evidence=ev)
            builder.add_impact(changed_entity=child, impacted_entity=unit, check="Review parent orchestration, error handling, and context passing.", evidence=ev)
        for routine in job.get("raw_routines", []) or []:
            rname = str(routine)
            ev = [builder.evidence(path, unit_name=unit, source_key="raw_routines", excerpt=rname, confidence=0.8)]
            builder.add_dependency(source=unit, target=rname, dependency_type="uses_routine", evidence=ev)
            builder.add_usage(usage_type="called", entity_id=None, name=rname, location=unit, evidence=ev, confidence=0.8)

    def extract_generic_job_risks(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        components = job.get("components", []) or []
        connections = job.get("connections", []) or []
        if components and not any(str(conn.get("connector_name") or "").upper() in _ERROR_CONNECTORS for conn in connections):
            ev = [builder.evidence(path, unit_name=unit, source_key="connections", confidence=0.65)]
            builder.add_known_risk(
                name=f"{unit} has no explicit error path evidence",
                severity="MEDIUM",
                affected_entity=unit,
                risk_type="missing_error_path",
                remediation="Add or document error handling for critical paths.",
                evidence=ev,
                confidence=0.65,
            )

    def infer_missing_tests(self, builder: EngineeringFactsBuilder) -> None:
        test_paths = {fact.test_file or "" for fact in builder.test_coverage.values() if fact.test_file}
        if test_paths:
            return
        for entity in list(builder.entities.values())[:25]:
            if entity.entity_type in {"module", "job", "function", "class", "route"}:
                builder.add_test_coverage(
                    name=f"No test evidence for {entity.name}",
                    covered_entity=entity.name,
                    gap="No test file evidence was found in the scan.",
                    suggested_scenario=f"Add tests covering {entity.name}.",
                    evidence=entity.evidence,
                    confidence=0.55,
                )


def job_name(job: dict[str, Any]) -> str:
    return str(job.get("name") or Path(str(job.get("path") or "unit")).stem)


def job_path(job: dict[str, Any]) -> str:
    return str(job.get("path") or job_name(job))


def component_name(component: dict[str, Any]) -> str:
    return str(component.get("unique_name") or component.get("name") or "component")


def component_entity_type(component_type: str) -> str:
    lowered = component_type.lower()
    if "sql" in lowered or "query" in lowered:
        return "table_or_query"
    if "config" in lowered or "context" in lowered:
        return "config"
    if "code" in lowered or "routine" in lowered:
        return "module"
    return "component"


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    return "/test" in lowered or lowered.startswith("test") or "_test." in lowered or ".test." in lowered or ".spec." in lowered


def is_hardcoded_runtime_value(key: str, value: str) -> bool:
    lowered_key = key.lower()
    if not value or "context." in value or value.startswith("$") or "{{" in value:
        return False
    if any(token in lowered_key for token in _CONFIG_KEYWORDS):
        return any(ch in value for ch in ("/", "\\", ".", ":")) or value.isdigit()
    return False


def brief(value: Any, limit: int = 500) -> str:
    text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _dict_of_lists(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        if isinstance(items, list):
            result[str(key)] = [str(item) for item in items if str(item)]
    return result
