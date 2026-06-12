"""DocGapEvent model roundtrip (SQLite-compatible JSON variant)."""

from sqlalchemy import select

from src.db.models import DocGapEvent


async def test_doc_gap_event_roundtrip(db_session):
    event = DocGapEvent(
        project_id="proj",
        run_id=209,
        user_id=7,
        conversation_id=3,
        message_id=42,
        query_text="What happens when the database insert fails?",
        detected_entities=["sjItem_OffRamp_DB", "DIE_ON_ERROR"],
        insufficiency_reason="need_search",
        primary_source_layer="technical_analysis",
        layers_used=["technical_analysis"],
        supporting_artifacts=["proj/_internal/technical_analysis/Item.md"],
        supporting_chunk_ids=["12:0", "12:3"],
    )
    db_session.add(event)
    await db_session.flush()

    row = (await db_session.execute(select(DocGapEvent))).scalar_one()
    assert row.project_id == "proj"
    assert row.insufficiency_reason == "need_search"
    assert row.detected_entities == ["sjItem_OffRamp_DB", "DIE_ON_ERROR"]
    assert row.layers_used == ["technical_analysis"]
    assert row.supporting_chunk_ids == ["12:0", "12:3"]
    assert row.created_at is not None


async def test_doc_gap_event_nullable_fields(db_session):
    event = DocGapEvent(
        project_id="proj",
        query_text="q",
        insufficiency_reason="no_sources",
    )
    db_session.add(event)
    await db_session.flush()

    row = (await db_session.execute(select(DocGapEvent))).scalar_one()
    assert row.run_id is None
    assert row.message_id is None
    assert row.detected_entities is None
