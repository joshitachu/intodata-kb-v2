"""Workato EngineeringFacts extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.extractors.base import EngineeringFactExtractor
from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts
from mcp_server.providers.custom.models import FactsManifest


class WorkatoFactExtractor(EngineeringFactExtractor):
    """Adapt Workato recipes, functions, lookups and connections to shared facts."""

    platform = "workato"

    def extract(self, project_id: str, scan: dict[str, Any], facts_manifest: FactsManifest) -> EngineeringFacts:
        builder = EngineeringFactsBuilder(project_id, "workato")
        for job in scan.get("jobs", []) or []:
            self._extract_recipe_like_unit(builder, job)
        self._extract_manifest_assets(builder, scan.get("manifest", {}) if isinstance(scan.get("manifest"), dict) else {})
        return builder.build()

    def _extract_recipe_like_unit(self, builder: EngineeringFactsBuilder, job: dict[str, Any]) -> None:
        unit = str(job.get("name") or Path(str(job.get("path") or "recipe")).stem)
        path = str(job.get("path") or unit)
        group = str(job.get("group") or "Recipes")
        components = job.get("components", []) or []
        raw = self._raw_json_from_components(components)
        ev = [builder.evidence(path, unit_name=unit, source_key="recipe", excerpt=_brief(raw), confidence=1.0)]
        entity_type = "recipe_function" if "function" in path.lower() or _truthy(raw.get("callable")) else "recipe"
        recipe_entity = builder.add_entity(entity_type=entity_type, name=unit, path=path, unit_name=unit, evidence=ev, metadata={"group": group})
        builder.add_architecture(name=unit, layer=group, component=unit, boundary="workato_workspace", evidence=ev)
        builder.add_usage(usage_type="defined", entity_id=recipe_entity.id, name=unit, location=path, evidence=ev)

        trigger = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else raw.get("code", {}).get("trigger") if isinstance(raw.get("code"), dict) else None
        if isinstance(trigger, dict):
            provider = _string(trigger.get("provider") or trigger.get("application") or trigger.get("keyword"))
            action = _string(trigger.get("name") or trigger.get("operation") or trigger.get("keyword") or "trigger")
            tev = [builder.evidence(path, unit_name=unit, source_key="trigger", excerpt=_brief(trigger), confidence=0.9)]
            builder.add_entity(entity_type="trigger", name=f"{unit}:{action}", path=path, unit_name=unit, evidence=tev, metadata={"provider": provider})
            builder.add_dependency(source=provider or "trigger", target=unit, dependency_type="triggers", evidence=tev)
            if "schedule" in json.dumps(trigger).lower():
                builder.add_config_runtime(name=f"{unit} schedule", config_type="schedule", value_hint=_brief(trigger), evidence=tev)

        steps = _steps_from_raw(raw)
        previous = "trigger"
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            provider = _string(step.get("provider") or step.get("application") or step.get("connector") or step.get("app"))
            action = _string(step.get("action") or step.get("name") or step.get("keyword") or step.get("operation") or step.get("type") or f"step_{idx}")
            step_name = f"{unit}:step:{idx}:{provider or 'workato'}:{action}"
            sev = [builder.evidence(path, unit_name=unit, source_key=f"steps[{idx}]", excerpt=_brief(step), confidence=0.9)]
            step_entity = builder.add_entity(
                entity_type="step",
                name=step_name,
                path=path,
                unit_name=unit,
                evidence=sev,
                metadata={"provider": provider, "action": action, "index": idx, "type": step.get("type")},
            )
            builder.add_usage(usage_type="configured", entity_id=step_entity.id, name=step_name, location=path, evidence=sev)
            builder.add_dependency(source=unit, target=step_name, dependency_type="contains_step", evidence=sev)
            builder.add_dependency(source=previous, target=step_name, dependency_type="execution_order", evidence=sev, confidence=0.75)
            previous = step_name
            if provider:
                system = builder.add_entity(entity_type="external_system", name=provider, path=path, unit_name=unit, evidence=sev, confidence=0.8)
                builder.add_dependency(source=step_name, target=provider, dependency_type="uses_provider", evidence=sev)
                builder.add_usage(usage_type="called", entity_id=system.id, name=provider, location=step_name, evidence=sev)
            self._extract_step_fields(builder, unit, path, step_name, step, sev)

        if not self._has_error_handling(raw):
            builder.add_known_risk(
                name=f"{unit} has no explicit error handling evidence",
                severity="MEDIUM",
                affected_entity=unit,
                risk_type="silent_failure_path",
                remediation="Add or document recipe-level error handling and alerting.",
                evidence=ev,
                confidence=0.65,
            )

    def _extract_step_fields(self, builder: EngineeringFactsBuilder, unit: str, path: str, step_name: str, step: dict[str, Any], evidence) -> None:
        for key, value in _walk_items(step):
            key_l = key.lower()
            text = _string(value)
            if not text:
                continue
            if "connection" in key_l and ("id" in key_l or key_l == "connection"):
                conn = builder.add_entity(entity_type="connection", name=text, path=path, unit_name=unit, evidence=evidence, confidence=0.8)
                builder.add_dependency(source=step_name, target=text, dependency_type="uses_connection", evidence=evidence)
                builder.add_usage(usage_type="configured", entity_id=conn.id, name=text, location=step_name, evidence=evidence)
            elif "lookup" in key_l and ("table" in key_l or "id" in key_l):
                lookup = builder.add_entity(entity_type="table", name=text, path=path, unit_name=unit, evidence=evidence, confidence=0.8)
                builder.add_usage(usage_type="queried", entity_id=lookup.id, name=text, location=step_name, evidence=evidence)
                builder.add_dependency(source=step_name, target=text, dependency_type="lookup_table", evidence=evidence)
            elif "recipe" in key_l and "function" in key_l:
                builder.add_dependency(source=unit, target=text, dependency_type="calls_function", evidence=evidence)
                builder.add_usage(usage_type="called", entity_id=None, name=text, location=step_name, evidence=evidence)
            elif any(token in key_l for token in ("input", "source", "datapill")):
                builder.add_usage(usage_type="read", entity_id=None, name=text[:160], location=step_name, evidence=evidence, confidence=0.7)
            elif any(token in key_l for token in ("output", "target", "field", "upsert")):
                builder.add_usage(usage_type="written", entity_id=None, name=text[:160], location=step_name, evidence=evidence, confidence=0.7)
            elif any(token in key_l for token in ("formula", "mapping", "transform")):
                builder.add_data_flow(source=unit, target=step_name, name=f"{unit} mapping in {step_name}", transformation=text[:500], evidence=evidence, confidence=0.75)

    def _extract_manifest_assets(self, builder: EngineeringFactsBuilder, manifest: dict[str, Any]) -> None:
        for key in ("connections", "lookup_tables", "recipe_functions", "recipes"):
            values = manifest.get(key)
            if isinstance(values, list):
                for item in values:
                    name = str(item.get("name") if isinstance(item, dict) else item)
                    if not name:
                        continue
                    ev = [builder.evidence("manifest", source_key=key, excerpt=_brief(item), confidence=0.75)]
                    entity_type = {
                        "connections": "connection",
                        "lookup_tables": "table",
                        "recipe_functions": "recipe_function",
                        "recipes": "recipe",
                    }[key]
                    builder.add_entity(entity_type=entity_type, name=name, evidence=ev, confidence=0.75)

    @staticmethod
    def _raw_json_from_components(components: list[dict[str, Any]]) -> dict[str, Any]:
        reconstructed: dict[str, Any] = {}
        for component in components:
            for param in component.get("params", []) or []:
                name = str(param.get("name") or "")
                value = param.get("value")
                if name.lower() in {"raw_json", "content"}:
                    if isinstance(value, dict):
                        return value
                    if isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                            if isinstance(parsed, dict):
                                return parsed
                        except json.JSONDecodeError:
                            continue
                if name:
                    if isinstance(value, str):
                        try:
                            reconstructed[name] = json.loads(value)
                            continue
                        except json.JSONDecodeError:
                            pass
                    reconstructed[name] = value
        return reconstructed

    @staticmethod
    def _has_error_handling(raw: dict[str, Any]) -> bool:
        text = json.dumps(raw).lower()
        return any(token in text for token in ("error", "retry", "alert", "notification", "monitor"))


def _steps_from_raw(raw: dict[str, Any]) -> list[Any]:
    if isinstance(raw.get("steps"), list):
        return raw["steps"]
    code = raw.get("code")
    if isinstance(code, str):
        try:
            code = json.loads(code)
        except json.JSONDecodeError:
            code = None
    if isinstance(code, dict):
        block = code.get("block")
        if isinstance(block, list):
            return block
        actions = code.get("actions")
        if isinstance(actions, list):
            return actions
    return []


def _walk_items(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            joined = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_items(child, joined)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk_items(child, f"{prefix}[{idx}]")
    else:
        yield prefix, value


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return False


def _brief(value: Any, limit: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
