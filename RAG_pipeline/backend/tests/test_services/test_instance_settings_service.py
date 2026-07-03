"""Tests for the RAG-side instance settings service."""
from __future__ import annotations

import pytest

from src.services.instance_settings_service import (
    SETTING_KEYS,
    SETTING_REGISTRY,
    get_all,
    get_value,
    normalize_payload,
    set_many,
)


class TestNormalizePayload:
    def test_accepts_empty_payload(self):
        assert normalize_payload({}) == {}

    def test_passes_through_known_keys(self):
        out = normalize_payload({"rag_chat_model": "anthropic/claude-sonnet-4-6"})
        assert out == {"rag_chat_model": "anthropic/claude-sonnet-4-6"}

    def test_coerces_none_to_empty_string(self):
        out = normalize_payload({"rag_chat_model": None})
        assert out == {"rag_chat_model": ""}

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="Unknown setting key"):
            normalize_payload({"not_a_real_key": "x"})

    def test_rejects_non_string_value(self):
        with pytest.raises(ValueError, match="must be a string"):
            normalize_payload({"rag_chat_model": 123})

    def test_rejects_overly_long_value(self):
        with pytest.raises(ValueError, match="exceeds 500 chars"):
            normalize_payload({"rag_chat_model": "x" * 501})


class TestRegistry:
    def test_all_setting_keys_have_registry_entries(self):
        for key in SETTING_KEYS:
            assert key in SETTING_REGISTRY
            assert SETTING_REGISTRY[key].label
            assert SETTING_REGISTRY[key].description


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_set_many_persists_and_get_all_returns(self, db_session):
        before = await get_all(db_session)
        for key in before:
            await db_session.delete(before[key])
        await db_session.flush()
        try:
            await set_many(
                db_session,
                {"rag_chat_model": "anthropic/claude-sonnet-4-6"},
                actor_user_id=None,
                actor_username=None,
            )
            await db_session.commit()
            after = await get_all(db_session)
            assert after == {"rag_chat_model": "anthropic/claude-sonnet-4-6"}
        finally:
            current = await get_all(db_session)
            for row in current.values():
                pass

    @pytest.mark.asyncio
    async def test_set_many_empty_value_clears(self, db_session):
        await set_many(
            db_session,
            {"rag_chat_model": "x"},
            actor_user_id=None,
            actor_username=None,
        )
        await db_session.commit()
        await set_many(
            db_session,
            {"rag_chat_model": ""},
            actor_user_id=None,
            actor_username=None,
        )
        await db_session.commit()
        assert await get_value(db_session, "rag_chat_model") is None