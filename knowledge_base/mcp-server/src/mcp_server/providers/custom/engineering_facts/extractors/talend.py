"""Talend EngineeringFacts extractor."""

from __future__ import annotations

from typing import Any

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.extractors.structured import (
    StructuredScanFactExtractor,
    brief,
    component_name,
    job_name,
    job_path,
)
from mcp_server.providers.custom.models import FactsManifest


class TalendFactExtractor(StructuredScanFactExtractor):
    """Add Talend-specific facts on top of shared parsed scan extraction."""

    platform = "talend"

    def job_entity_type(self, job: dict[str, Any]) -> str:
        return "job"

    def extract_platform(
        self,
        builder: EngineeringFactsBuilder,
        scan: dict[str, Any],
        facts_manifest: FactsManifest,
    ) -> None:
        for job in scan.get("jobs", []) or []:
            self._extract_talend_job(builder, job)

    def _extract_talend_job(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        components = job.get("components", []) or []
        self._extract_trunjob_components(builder, job, components)
        self._extract_tmap_data(builder, job)
        self._extract_connection_modes(builder, job, components)
        self._extract_talend_error_risks(builder, job)

    def _extract_trunjob_components(self, builder: EngineeringFactsBuilder, job: dict[str, Any], components: list[dict[str, Any]]) -> None:
        unit = job_name(job)
        path = job_path(job)
        refs = list(job.get("trunjob_refs", []) or [])
        ref_by_child = {str(ref.get("child_job") or ""): ref for ref in refs}

        for component in components:
            if str(component.get("component_type") or "") != "tRunJob":
                continue
            cname = component_name(component)
            params = {str(p.get("name") or ""): str(p.get("value") or "") for p in component.get("params", []) or []}
            child = params.get("PROCESS") or next(iter(ref_by_child.keys()), "")
            ev = [builder.evidence(path, unit_name=unit, symbol=cname, source_key="tRunJob", excerpt=brief(params), confidence=0.95)]
            if child:
                builder.add_dependency(source=unit, target=child, dependency_type="tRunJob", evidence=ev)
                builder.add_usage(usage_type="called", entity_id=None, name=child, location=cname, evidence=ev)
                builder.add_impact(
                    changed_entity=child,
                    impacted_entity=unit,
                    check="Review tRunJob orchestration, context passing, and failure propagation.",
                    evidence=ev,
                )
            die_on_error = params.get("DIE_ON_CHILD_ERROR") or params.get("PROCESS:DIE_ON_CHILD_ERROR")
            if str(die_on_error).lower() == "false":
                builder.add_known_risk(
                    name=f"{unit} has tRunJob with DIE_ON_CHILD_ERROR=false",
                    severity="HIGH",
                    affected_entity=cname,
                    risk_type="silent_child_failure",
                    remediation="Set DIE_ON_CHILD_ERROR=true or add explicit COMPONENT_ERROR handling.",
                    evidence=ev,
                    confidence=0.9,
                )
            if any(str(key).startswith("PROCESS:") or "CONTEXT" in str(key) for key in params):
                builder.add_config_runtime(
                    name=f"{cname} context passing",
                    config_type="context_passing",
                    key=cname,
                    value_hint=brief(params),
                    evidence=ev,
                    confidence=0.8,
                )

    def _extract_tmap_data(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        tmap_components = [component_name(c) for c in job.get("components", []) or [] if str(c.get("component_type") or "") == "tMap"]
        for idx, tmap in enumerate(job.get("tmap_data", []) or [], start=1):
            tmap_name = tmap_components[idx - 1] if idx <= len(tmap_components) else f"{unit}:tMap:{idx}"
            ev = [builder.evidence(path, unit_name=unit, symbol=tmap_name, source_key=f"tmap_data[{idx}]", excerpt=brief(tmap), confidence=0.95)]
            builder.add_entity(
                entity_type="function",
                name=tmap_name,
                path=path,
                unit_name=unit,
                evidence=ev,
                metadata={"component_type": "tMap"},
            )
            for table in tmap.get("input_tables", []) or []:
                table_name = str(table)
                builder.add_entity(entity_type="table", name=table_name, path=path, unit_name=unit, evidence=ev, metadata={"role": "tmap_input"})
                builder.add_dependency(source=table_name, target=tmap_name, dependency_type="tMap_input", evidence=ev)
            for table in tmap.get("output_tables", []) or []:
                table_name = str(table)
                builder.add_entity(entity_type="table", name=table_name, path=path, unit_name=unit, evidence=ev, metadata={"role": "tmap_output"})
                builder.add_dependency(source=tmap_name, target=table_name, dependency_type="tMap_output", evidence=ev)
                builder.add_impact(changed_entity=tmap_name, impacted_entity=table_name, check="Review mapped output fields and downstream consumers.", evidence=ev)
            for entry in tmap.get("var_entries", []) or []:
                self._extract_tmap_entry(builder, unit, path, tmap_name, entry, ev, role="var")
            for entry in tmap.get("entries", []) or []:
                self._extract_tmap_entry(builder, unit, path, tmap_name, entry, ev, role="output")

    def _extract_tmap_entry(
        self,
        builder: EngineeringFactsBuilder,
        unit: str,
        path: str,
        tmap_name: str,
        entry: dict[str, Any],
        evidence,
        *,
        role: str,
    ) -> None:
        table = str(entry.get("table_name") or "")
        column = str(entry.get("column_name") or "")
        expression = str(entry.get("expression") or "")
        field_name = ".".join(part for part in (table, column) if part) or f"{tmap_name}:{role}"
        ev = [builder.evidence(path, unit_name=unit, symbol=tmap_name, source_key=f"tMap.{role}.{field_name}", excerpt=expression, confidence=0.95)]
        builder.add_entity(entity_type="field", name=field_name, path=path, unit_name=unit, evidence=ev, metadata={"role": role, "tmap": tmap_name})
        builder.add_usage(usage_type="written" if role == "output" else "defined", entity_id=None, name=field_name, location=tmap_name, evidence=ev)
        builder.add_data_flow(
            source=expression or tmap_name,
            target=field_name,
            name=f"{tmap_name} maps {field_name}",
            transformation=expression,
            schema_hint=table,
            evidence=ev,
            confidence=0.9 if expression else 0.75,
        )
        builder.add_impact(
            changed_entity=field_name,
            impacted_entity=unit,
            check="Review tMap expression, output schema, and downstream fields.",
            evidence=ev,
        )
        if expression:
            self.extract_context_references(builder, unit, path, tmap_name, field_name, expression, ev)
            self.extract_routine_references(builder, unit, path, tmap_name, field_name, expression, ev)
            if any(token in expression for token in ("Relational.ISNULL", "== null", "!= null")):
                builder.add_known_risk(
                    name=f"{unit} has null-sensitive tMap expression for {field_name}",
                    severity="LOW",
                    affected_entity=field_name,
                    risk_type="null_handling",
                    remediation="Verify null/default behavior and add validation coverage.",
                    evidence=ev,
                    confidence=0.65,
                )

    def _extract_connection_modes(self, builder: EngineeringFactsBuilder, job: dict[str, Any], components: list[dict[str, Any]]) -> None:
        unit = job_name(job)
        path = job_path(job)
        for component in components:
            cname = component_name(component)
            params = {str(p.get("name") or ""): str(p.get("value") or "") for p in component.get("params", []) or []}
            prop_type = params.get("PROPERTY_TYPE") or params.get("PROPERTY:PROPERTY_TYPE")
            repo_ref = params.get("REPOSITORY_PROPERTY_TYPE") or params.get("PROPERTY:REPOSITORY_PROPERTY_TYPE")
            if not prop_type and not repo_ref:
                continue
            ev = [builder.evidence(path, unit_name=unit, symbol=cname, source_key="connection_mode", excerpt=brief(params), confidence=0.85)]
            builder.add_config_runtime(name=f"{cname} connection mode", config_type="connection_mode", key=cname, value_hint=prop_type or repo_ref, evidence=ev)
            if repo_ref:
                builder.add_dependency(source=cname, target=repo_ref, dependency_type="repository_connection_ref", evidence=ev, confidence=0.75)
            if str(prop_type).upper() == "BUILT_IN":
                builder.add_known_risk(
                    name=f"{unit} uses BUILT_IN connection in {cname}",
                    severity="MEDIUM",
                    affected_entity=cname,
                    risk_type="built_in_connection",
                    remediation="Move connection settings to shared metadata or managed runtime configuration.",
                    evidence=ev,
                    confidence=0.85,
                )

    def _extract_talend_error_risks(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        component_types = {str(component.get("component_type") or "") for component in job.get("components", []) or []}
        connectors = {str(conn.get("connector_name") or "").upper() for conn in job.get("connections", []) or []}
        if {"tDie", "tWarn", "tLogCatcher"} & component_types:
            return
        if "SUBJOB_ERROR" in connectors or "COMPONENT_ERROR" in connectors:
            return
        if component_types:
            ev = [builder.evidence(path, unit_name=unit, source_key="talend_error_handling", excerpt=brief(sorted(component_types)), confidence=0.75)]
            builder.add_known_risk(
                name=f"{unit} has no Talend error handler evidence",
                severity="MEDIUM",
                affected_entity=unit,
                risk_type="missing_talend_error_handler",
                remediation="Add tLogCatcher, SUBJOB_ERROR, COMPONENT_ERROR, tDie, or tWarn handling for critical paths.",
                evidence=ev,
                confidence=0.75,
            )
