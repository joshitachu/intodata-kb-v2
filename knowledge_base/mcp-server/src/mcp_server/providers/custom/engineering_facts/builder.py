"""Builder for EngineeringFacts with stable IDs and deduplication."""

from __future__ import annotations

import hashlib
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from mcp_server.providers.custom.engineering_facts.models import (
    ApiSurfaceFact,
    ArchitectureFact,
    ConfigRuntimeFact,
    DataFlowFact,
    DependencyFact,
    EngineeringFacts,
    EntityFact,
    EvidenceRef,
    ImpactFact,
    KnownRiskFact,
    TestCoverageFact,
    UsageFact,
)

FactT = TypeVar("FactT", bound=BaseModel)


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:48] or prefix
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{slug}-{digest}"


class EngineeringFactsBuilder:
    """Accumulates normalized facts and deduplicates by stable fact IDs."""

    def __init__(self, project_id: str, platform: str) -> None:
        self.project_id = project_id
        self.platform = platform
        self.entities: dict[str, EntityFact] = {}
        self.usage: dict[str, UsageFact] = {}
        self.dependencies: dict[str, DependencyFact] = {}
        self.impacts: dict[str, ImpactFact] = {}
        self.architecture: dict[str, ArchitectureFact] = {}
        self.data_flows: dict[str, DataFlowFact] = {}
        self.api_surface: dict[str, ApiSurfaceFact] = {}
        self.config_runtime: dict[str, ConfigRuntimeFact] = {}
        self.test_coverage: dict[str, TestCoverageFact] = {}
        self.known_risks: dict[str, KnownRiskFact] = {}

    @staticmethod
    def evidence(
        path: str,
        *,
        unit_name: str | None = None,
        line: int | None = None,
        symbol: str | None = None,
        source_key: str | None = None,
        excerpt: str | None = None,
        confidence: float = 1.0,
    ) -> EvidenceRef:
        return EvidenceRef(
            path=path,
            unit_name=unit_name,
            line=line,
            symbol=symbol,
            source_key=source_key,
            excerpt=_shorten(excerpt),
            confidence=confidence,
        )

    def add_entity(self, *, entity_type: str, name: str, path: str | None = None, unit_name: str | None = None,
                   evidence: list[EvidenceRef] | None = None, confidence: float = 1.0,
                   description: str = "", metadata: dict[str, Any] | None = None) -> EntityFact:
        fact = EntityFact(
            id=stable_id("ent", entity_type, name, path, unit_name),
            kind="entity",
            entity_type=entity_type,
            name=name,
            path=path,
            unit_name=unit_name,
            description=description,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.entities, fact)

    def add_usage(self, *, usage_type: str, entity_id: str | None, name: str, location: str | None = None,
                  evidence: list[EvidenceRef] | None = None, confidence: float = 1.0,
                  description: str = "", metadata: dict[str, Any] | None = None) -> UsageFact:
        fact = UsageFact(
            id=stable_id("use", usage_type, entity_id, name, location),
            kind="usage",
            usage_type=usage_type,  # type: ignore[arg-type]
            entity_id=entity_id,
            name=name,
            location=location,
            description=description,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.usage, fact)

    def add_dependency(self, *, source: str, target: str, dependency_type: str = "depends_on",
                       evidence: list[EvidenceRef] | None = None, confidence: float = 1.0,
                       description: str = "", metadata: dict[str, Any] | None = None) -> DependencyFact:
        fact = DependencyFact(
            id=stable_id("dep", dependency_type, source, target),
            kind="dependency",
            name=f"{source} -> {target}",
            source=source,
            target=target,
            dependency_type=dependency_type,
            description=description,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.dependencies, fact)

    def add_impact(self, *, changed_entity: str, impacted_entity: str, check: str, impact_type: str = "change",
                   evidence: list[EvidenceRef] | None = None, confidence: float = 0.7,
                   metadata: dict[str, Any] | None = None) -> ImpactFact:
        fact = ImpactFact(
            id=stable_id("imp", impact_type, changed_entity, impacted_entity, check),
            kind="impact",
            name=f"{changed_entity} impacts {impacted_entity}",
            changed_entity=changed_entity,
            impacted_entity=impacted_entity,
            check=check,
            impact_type=impact_type,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.impacts, fact)

    def add_architecture(self, *, name: str, layer: str | None = None, component: str | None = None,
                         boundary: str | None = None, evidence: list[EvidenceRef] | None = None,
                         confidence: float = 0.8, description: str = "",
                         metadata: dict[str, Any] | None = None) -> ArchitectureFact:
        fact = ArchitectureFact(
            id=stable_id("arch", layer, component, boundary, name),
            kind="architecture",
            name=name,
            layer=layer,
            component=component,
            boundary=boundary,
            description=description,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.architecture, fact)

    def add_data_flow(self, *, source: str, target: str, name: str | None = None, transformation: str | None = None,
                      schema_hint: str | None = None, persistence: str | None = None,
                      evidence: list[EvidenceRef] | None = None, confidence: float = 0.8,
                      metadata: dict[str, Any] | None = None) -> DataFlowFact:
        fact = DataFlowFact(
            id=stable_id("flow", source, target, transformation, name),
            kind="data_flow",
            name=name or f"{source} -> {target}",
            source=source,
            target=target,
            transformation=transformation,
            schema_hint=schema_hint,
            persistence=persistence,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.data_flows, fact)

    def add_api_surface(self, *, name: str, route: str | None = None, method: str | None = None,
                        handler: str | None = None, evidence: list[EvidenceRef] | None = None,
                        confidence: float = 0.8, metadata: dict[str, Any] | None = None, **kwargs: Any) -> ApiSurfaceFact:
        fact = ApiSurfaceFact(
            id=stable_id("api", method, route, handler, name),
            kind="api_surface",
            name=name,
            route=route,
            method=method,
            handler=handler,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
            **kwargs,
        )
        return self._merge(self.api_surface, fact)

    def add_config_runtime(self, *, name: str, config_type: str = "config", key: str | None = None,
                           value_hint: str | None = None, evidence: list[EvidenceRef] | None = None,
                           confidence: float = 0.9, metadata: dict[str, Any] | None = None) -> ConfigRuntimeFact:
        fact = ConfigRuntimeFact(
            id=stable_id("cfg", config_type, key, name, value_hint),
            kind="config_runtime",
            name=name,
            config_type=config_type,
            key=key,
            value_hint=value_hint,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.config_runtime, fact)

    def add_test_coverage(self, *, name: str, test_file: str | None = None, covered_entity: str | None = None,
                          gap: str | None = None, suggested_scenario: str | None = None,
                          evidence: list[EvidenceRef] | None = None, confidence: float = 0.7,
                          metadata: dict[str, Any] | None = None) -> TestCoverageFact:
        fact = TestCoverageFact(
            id=stable_id("test", test_file, covered_entity, gap, name),
            kind="test_coverage",
            name=name,
            test_file=test_file,
            covered_entity=covered_entity,
            gap=gap,
            suggested_scenario=suggested_scenario,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.test_coverage, fact)

    def add_known_risk(self, *, name: str, severity: str = "LOW", affected_entity: str | None = None,
                       risk_type: str = "risk", remediation: str | None = None,
                       evidence: list[EvidenceRef] | None = None, confidence: float = 0.7,
                       description: str = "", metadata: dict[str, Any] | None = None) -> KnownRiskFact:
        fact = KnownRiskFact(
            id=stable_id("risk", severity, risk_type, affected_entity, name),
            kind="known_risk",
            name=name,
            severity=severity,
            affected_entity=affected_entity,
            risk_type=risk_type,
            remediation=remediation,
            description=description,
            metadata=metadata or {},
            evidence=evidence or [],
            confidence=confidence,
        )
        return self._merge(self.known_risks, fact)

    def build(self) -> EngineeringFacts:
        return EngineeringFacts(
            project_id=self.project_id,
            platform=self.platform,
            entities=sorted(self.entities.values(), key=lambda f: f.id),
            usage=sorted(self.usage.values(), key=lambda f: f.id),
            dependencies=sorted(self.dependencies.values(), key=lambda f: f.id),
            impacts=sorted(self.impacts.values(), key=lambda f: f.id),
            architecture=sorted(self.architecture.values(), key=lambda f: f.id),
            data_flows=sorted(self.data_flows.values(), key=lambda f: f.id),
            api_surface=sorted(self.api_surface.values(), key=lambda f: f.id),
            config_runtime=sorted(self.config_runtime.values(), key=lambda f: f.id),
            test_coverage=sorted(self.test_coverage.values(), key=lambda f: f.id),
            known_risks=sorted(self.known_risks.values(), key=lambda f: f.id),
        )

    @staticmethod
    def _merge(bucket: dict[str, FactT], fact: FactT) -> FactT:
        existing = bucket.get(fact.id)
        if existing is None:
            bucket[fact.id] = fact
            return fact
        existing_evidence = getattr(existing, "evidence", [])
        seen = {
            (ev.path, ev.unit_name, ev.line, ev.symbol, ev.source_key, ev.excerpt)
            for ev in existing_evidence
        }
        for ev in getattr(fact, "evidence", []):
            key = (ev.path, ev.unit_name, ev.line, ev.symbol, ev.source_key, ev.excerpt)
            if key not in seen:
                existing_evidence.append(ev)
                seen.add(key)
        if hasattr(existing, "confidence"):
            existing.confidence = max(existing.confidence, getattr(fact, "confidence", 0.0))
        return existing


def _shorten(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    compact = " ".join(str(value).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
