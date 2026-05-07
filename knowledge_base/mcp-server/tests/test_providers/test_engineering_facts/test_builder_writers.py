from __future__ import annotations

import json

from mcp_server.providers.custom.engineering_facts.builder import EngineeringFactsBuilder
from mcp_server.providers.custom.engineering_facts.extractors.registry import ExtractorRegistry
from mcp_server.providers.custom.engineering_facts.extractors.registry import default_registry
from mcp_server.providers.custom.engineering_facts.extractors.generic import GenericFactExtractor
from mcp_server.providers.custom.engineering_facts.extractors.talend import TalendFactExtractor
from mcp_server.providers.custom.engineering_facts.extractors.workato import WorkatoFactExtractor
from mcp_server.providers.custom.engineering_facts.writers.csv_writer import CsvFactWriter
from mcp_server.providers.custom.engineering_facts.writers.json_writer import JsonFactWriter
from mcp_server.providers.custom.engineering_facts.writers.markdown_writer import MarkdownFactWriter


def test_builder_deduplicates_stable_ids_and_merges_evidence():
    builder = EngineeringFactsBuilder("proj", "generic")
    ev1 = builder.evidence("src/app.py", line=1)
    ev2 = builder.evidence("src/app.py", line=2)

    first = builder.add_entity(entity_type="function", name="handler", path="src/app.py", evidence=[ev1])
    second = builder.add_entity(entity_type="function", name="handler", path="src/app.py", evidence=[ev2])
    facts = builder.build()

    assert first.id == second.id
    assert len(facts.entities) == 1
    assert len(facts.entities[0].evidence) == 2


def test_registry_falls_back_to_generic():
    registry = ExtractorRegistry()
    generic = GenericFactExtractor()
    registry.register(generic)

    assert registry.get("unknown-platform") is generic


def test_default_registry_selects_platform_strategies():
    registry = default_registry()

    assert isinstance(registry.get("talend"), TalendFactExtractor)
    assert isinstance(registry.get("workato"), WorkatoFactExtractor)
    assert isinstance(registry.get("unknown-platform"), GenericFactExtractor)


def test_writers_emit_required_formats(tmp_path):
    builder = EngineeringFactsBuilder("proj", "generic")
    entity = builder.add_entity(entity_type="module", name="app", path="src/app.py")
    builder.add_usage(usage_type="defined", entity_id=entity.id, name="app", location="src/app.py")
    builder.add_dependency(source="app", target="flask", dependency_type="import")
    facts = builder.build()

    written = []
    written.extend(JsonFactWriter().write(facts, tmp_path))
    written.extend(CsvFactWriter().write(facts, tmp_path))
    written.extend(MarkdownFactWriter().write(facts, tmp_path))

    names = {path.name for path in written}
    assert {"engineering_facts.json", "entity_index.json", "entity_index.csv", "dependency_map.md"} <= names
    assert json.loads((tmp_path / "entity_index.json").read_text(encoding="utf-8"))[0]["name"] == "app"
