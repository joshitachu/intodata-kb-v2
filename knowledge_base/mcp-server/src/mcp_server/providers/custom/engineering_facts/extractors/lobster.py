"""Lobster EngineeringFacts extractor."""

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


class LobsterFactExtractor(StructuredScanFactExtractor):
    """Add Lobster/DataWizard facts on top of shared parsed scan extraction."""

    platform = "lobster"

    def job_entity_type(self, job: dict[str, Any]) -> str:
        return "profile"

    def extract_platform(
        self,
        builder: EngineeringFactsBuilder,
        scan: dict[str, Any],
        facts_manifest: FactsManifest,
    ) -> None:
        for job in scan.get("jobs", []) or []:
            self._extract_lobster_profile(builder, job)

    def _extract_lobster_profile(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        group = str(job.get("group") or "root")
        components = job.get("components", []) or []
        ev = [builder.evidence(path, unit_name=unit, source_key="lobster_profile", confidence=1.0)]
        builder.add_architecture(
            name=f"{unit} Lobster profile",
            layer=group,
            component=unit,
            boundary="lobster_datawizard",
            evidence=ev,
            confidence=0.9,
        )

        source_schema = _first_component(components, "LobsterSourceSchema")
        mapping = _first_component(components, "LobsterMapping")
        if source_schema and mapping:
            source_tree = _param(source_schema, "input_tree")
            output_tree = _param(mapping, "output_tree")
            mev = [builder.evidence(path, unit_name=unit, symbol="mapping", source_key="LobsterMapping", excerpt=brief(output_tree), confidence=0.9)]
            builder.add_data_flow(
                source=f"{unit}:source_schema",
                target=f"{unit}:mapping",
                name=f"{unit} source schema to mapping",
                transformation=brief(output_tree),
                schema_hint=brief(source_tree),
                evidence=mev,
                confidence=0.85,
            )

        for component in components:
            ctype = str(component.get("component_type") or "")
            if ctype.startswith("LobsterInputAgent_"):
                self._extract_agent(builder, job, component)
            elif ctype == "LobsterMapping":
                self._extract_mapping(builder, job, component)
            elif ctype.startswith("LobsterResponse_"):
                self._extract_response(builder, job, component)
            elif ctype.startswith("LobsterParser_"):
                self._extract_parser(builder, job, component)

    def _extract_agent(self, builder: EngineeringFactsBuilder, job: dict[str, Any], component: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        ctype = str(component.get("component_type") or "")
        agent_type = ctype.removeprefix("LobsterInputAgent_") or ctype
        params = _params(component)
        cname = component_name(component)
        ev = [builder.evidence(path, unit_name=unit, symbol=cname, source_key=ctype, excerpt=brief(params), confidence=0.9)]
        builder.add_entity(entity_type="trigger", name=f"{unit}:{agent_type}", path=path, unit_name=unit, evidence=ev, metadata={"agent_type": agent_type})
        builder.add_dependency(source=f"{unit}:{agent_type}", target=unit, dependency_type="triggers_profile", evidence=ev)
        builder.add_config_runtime(name=f"{unit} input agent", config_type="lobster_agent", key=agent_type, value_hint=brief(params), evidence=ev)

        route = params.get("url") or params.get("service")
        if route and "HTTP" in agent_type.upper():
            methods = [
                name.removeprefix("accept_").upper()
                for name, value in params.items()
                if name.startswith("accept_") and str(value).lower() == "true"
            ]
            method = ",".join(methods) if methods else None
            builder.add_api_surface(name=f"{unit} HTTP input", route=route, method=method, handler=unit, evidence=ev, confidence=0.85)

        for key in ("dir", "filename", "host", "port", "comm_channel_partner", "comm_channel_name"):
            value = params.get(key)
            if value:
                builder.add_config_runtime(
                    name=f"{unit}.{key}",
                    config_type="lobster_channel",
                    key=key,
                    value_hint=value,
                    evidence=ev,
                    confidence=0.85,
                )

    def _extract_parser(self, builder: EngineeringFactsBuilder, job: dict[str, Any], component: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        ctype = str(component.get("component_type") or "")
        params = _params(component)
        ev = [builder.evidence(path, unit_name=unit, symbol=component_name(component), source_key=ctype, excerpt=brief(params), confidence=0.85)]
        builder.add_config_runtime(name=f"{unit} parser", config_type="lobster_parser", key=ctype.removeprefix("LobsterParser_"), value_hint=brief(params), evidence=ev)

    def _extract_mapping(self, builder: EngineeringFactsBuilder, job: dict[str, Any], component: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        params = component.get("params", []) or []
        for param in params:
            name = str(param.get("name") or "")
            value = str(param.get("value") or "")
            if name == "output_tree" and value:
                ev = [builder.evidence(path, unit_name=unit, symbol="LobsterMapping", source_key=name, excerpt=brief(value), confidence=0.9)]
                builder.add_entity(entity_type="schema", name=f"{unit}:output_tree", path=path, unit_name=unit, evidence=ev)
                builder.add_usage(usage_type="written", entity_id=None, name=f"{unit}:output_tree", location=unit, evidence=ev)
            elif name.startswith("filter_") and value:
                ev = [builder.evidence(path, unit_name=unit, symbol="LobsterMapping", source_key=name, excerpt=value, confidence=0.9)]
                builder.add_entity(entity_type="function", name=f"{unit}:{name}", path=path, unit_name=unit, evidence=ev, metadata={"component_type": "LobsterMapping"})
                builder.add_data_flow(
                    source=f"{unit}:mapping",
                    target=f"{unit}:{name}",
                    name=f"{unit} mapping filter {name}",
                    transformation=value,
                    evidence=ev,
                    confidence=0.85,
                )
                builder.add_impact(
                    changed_entity=f"{unit}:{name}",
                    impacted_entity=unit,
                    check="Review Lobster filter inputs, constants, variables, and output fields.",
                    evidence=ev,
                    confidence=0.8,
                )
                if "(base64)" in value:
                    builder.add_known_risk(
                        name=f"{unit} has scanner-limited encoded filter constant",
                        severity="LOW",
                        affected_entity=f"{unit}:{name}",
                        risk_type="scanner_limited_filter_constant",
                        remediation="Validate encoded Lobster filter constants against the source profile.",
                        evidence=ev,
                        confidence=0.7,
                    )

    def _extract_response(self, builder: EngineeringFactsBuilder, job: dict[str, Any], component: dict[str, Any]) -> None:
        unit = job_name(job)
        path = job_path(job)
        ctype = str(component.get("component_type") or "")
        response_type = ctype.removeprefix("LobsterResponse_") or ctype
        params = _params(component)
        label = params.get("label") or component_name(component)
        target = f"{unit}:response:{label}"
        ev = [builder.evidence(path, unit_name=unit, symbol=label, source_key=ctype, excerpt=brief(params), confidence=0.9)]
        builder.add_entity(entity_type="sink", name=target, path=path, unit_name=unit, evidence=ev, metadata={"response_type": response_type})
        builder.add_data_flow(
            source=f"{unit}:mapping",
            target=target,
            name=f"{unit} output to {label}",
            persistence=response_type,
            evidence=ev,
            confidence=0.85,
        )
        builder.add_config_runtime(name=target, config_type="lobster_response", key=response_type, value_hint=brief(params), evidence=ev)
        for key in ("dir", "filename", "host", "port", "comm_channel_partner", "comm_channel_name"):
            value = params.get(key)
            if value:
                builder.add_config_runtime(name=f"{target}.{key}", config_type="lobster_response_setting", key=key, value_hint=value, evidence=ev)


def _first_component(components: list[dict[str, Any]], component_type: str) -> dict[str, Any] | None:
    return next((c for c in components if str(c.get("component_type") or "") == component_type), None)


def _params(component: dict[str, Any]) -> dict[str, str]:
    return {
        str(param.get("name") or ""): str(param.get("value") or "")
        for param in component.get("params", []) or []
        if str(param.get("name") or "")
    }


def _param(component: dict[str, Any] | None, name: str) -> str:
    if component is None:
        return ""
    return _params(component).get(name, "")
