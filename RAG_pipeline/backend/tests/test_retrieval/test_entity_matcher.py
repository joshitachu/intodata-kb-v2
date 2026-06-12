"""Tests for query-time entity matching over the entity_aliases table."""

from src.db.models import EntityAlias
from src.retrieval.entity_matcher import (
    EntityMatch,
    match_query_entities,
    matched_unit_names,
)


async def _seed(db_session, project_id="phase2"):
    db_session.add_all([
        EntityAlias(
            project_id=project_id, alias="resource_logDir", alias_normalized="resource_logdir",
            canonical_name="resource_logDir", entity_type="config",
            unit_name=None, source_path="TALEND_CDC/context/resource_logDir_0.1.item",
            source_artifact="entity_index.csv", confidence=1.0,
        ),
        EntityAlias(
            project_id=project_id, alias="tDBOutput_1", alias_normalized="tdboutput_1",
            canonical_name="tDBOutput_1", entity_type="component",
            unit_name="sjItem_OffRamp_DB", source_path="Item/Out/job.item",
            source_artifact="entity_index.csv", confidence=0.95,
        ),
        EntityAlias(
            project_id=project_id, alias="sjItem_OffRamp_DB", alias_normalized="sjitem_offramp_db",
            canonical_name="sjItem_OffRamp_DB", entity_type="unit",
            unit_name="sjItem_OffRamp_DB", source_path=None,
            source_artifact="entity_index.csv", confidence=1.0,
        ),
        EntityAlias(
            project_id="other_proj", alias="tDBOutput_1", alias_normalized="tdboutput_1",
            canonical_name="tDBOutput_1", entity_type="component",
            unit_name="otherJob", source_path=None,
            source_artifact="entity_index.csv", confidence=1.0,
        ),
    ])
    await db_session.flush()


async def test_exact_alias_match(db_session):
    await _seed(db_session)
    result = await match_query_entities(
        db_session, "What is the resource_logDir context variable used for?", ["phase2"]
    )
    assert result.alias_rows_present is True
    assert [m.canonical_name for m in result.matches] == ["resource_logDir"]
    assert result.matches[0].source_path == "TALEND_CDC/context/resource_logDir_0.1.item"


async def test_case_insensitive_match(db_session):
    await _seed(db_session)
    result = await match_query_entities(db_session, "what does TDBOUTPUT_1 do?", ["phase2"])
    assert [m.canonical_name for m in result.matches] == ["tDBOutput_1"]


async def test_project_scoping_excludes_other_projects(db_session):
    await _seed(db_session)
    result = await match_query_entities(db_session, "what does tDBOutput_1 do?", ["phase2"])
    assert all(m.project_id == "phase2" for m in result.matches)


async def test_no_match_with_alias_rows_present(db_session):
    await _seed(db_session)
    result = await match_query_entities(
        db_session, "Is JavaScript used anywhere in the frontend?", ["phase2"]
    )
    assert result.matches == []
    assert result.alias_rows_present is True, (
        "alias rows exist — caller must not fall back to the heuristic"
    )


async def test_no_alias_rows_signals_legacy_fallback(db_session):
    result = await match_query_entities(db_session, "what does tDBOutput_1 do?", ["unsynced"])
    assert result.matches == []
    assert result.alias_rows_present is False


async def test_boundary_matching_rejects_longer_identifiers(db_session):
    await _seed(db_session)
    result = await match_query_entities(
        db_session, "what about tDBOutput_12 here?", ["phase2"]
    )
    assert result.matches == [], "tDBOutput_1 must not match inside tDBOutput_12"


async def test_longest_match_first(db_session):
    await _seed(db_session)
    result = await match_query_entities(
        db_session, "does tDBOutput_1 in sjItem_OffRamp_DB write items?", ["phase2"]
    )
    names = [m.canonical_name for m in result.matches]
    assert names[0] == "sjItem_OffRamp_DB"
    assert "tDBOutput_1" in names


def test_matched_unit_names_collects_units_and_unit_entities():
    matches = [
        EntityMatch(
            alias="tDBOutput_1", canonical_name="tDBOutput_1", entity_type="component",
            project_id="p", unit_name="sjItem_OffRamp_DB", source_path=None,
            source_artifact=None, confidence=1.0,
        ),
        EntityMatch(
            alias="sjItem_OnRamp", canonical_name="sjItem_OnRamp", entity_type="unit",
            project_id="p", unit_name=None, source_path=None,
            source_artifact=None, confidence=1.0,
        ),
        EntityMatch(
            alias="resource_logDir", canonical_name="resource_logDir", entity_type="config",
            project_id="p", unit_name=None, source_path=None,
            source_artifact=None, confidence=1.0,
        ),
    ]
    assert matched_unit_names(matches) == ["sjItem_OffRamp_DB", "sjItem_OnRamp"]
