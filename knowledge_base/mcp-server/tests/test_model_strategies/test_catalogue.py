"""Catalogue parity + availability tests.

The JSON catalogue is the single source of truth. These guard that every model in
it resolves to a real provider strategy and that availability reflects credentials.
"""
from __future__ import annotations

import pytest

from mcp_server.model_strategies import get_model_strategy, supported_model_ids
from mcp_server.model_strategies.catalogue import (
    is_model_available,
    load_catalogue,
    unavailable_reason,
)


def test_every_catalogue_model_resolves_to_a_strategy():
    for descriptor in load_catalogue():
        strategy = get_model_strategy(descriptor.id)
        assert strategy.provider_id == descriptor.provider


def test_supported_ids_include_all_catalogue_ids():
    ids = supported_model_ids()
    for descriptor in load_catalogue():
        assert descriptor.id in ids


def test_local_models_are_always_available():
    for descriptor in load_catalogue():
        if descriptor.is_local:
            assert is_model_available(descriptor) is True
            assert unavailable_reason(descriptor) is None


def test_cloud_model_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    openai_models = [d for d in load_catalogue() if d.provider == "openai"]
    assert openai_models, "expected openai models in the catalogue"
    d = openai_models[0]
    assert is_model_available(d) is False
    assert "OPENAI_API_KEY" in (unavailable_reason(d) or "")


def test_cloud_model_available_with_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    d = [d for d in load_catalogue() if d.provider == "openai"][0]
    assert is_model_available(d) is True


def test_google_available_with_either_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    d = [d for d in load_catalogue() if d.provider == "google"][0]
    assert is_model_available(d) is True


#: Lanes that exist for DOCUMENT GENERATION only and must never be offered to
#: RAG chat. `opencode-go/<model>` is a subscription route redeemed inside an
#: OpenCode CLI session -- the chat surface has no session to redeem it in --
#: and the OpenRouter stealth entry is a doc-gen evaluation route.
DOC_GENERATION_ONLY_PROVIDERS = frozenset({"opencode-go", "openrouter"})


def test_direct_api_models_are_rag_capable():
    """Every model reachable by a direct API client is offerable to RAG chat.

    This asserted `rag_capable is True` for the WHOLE catalogue until now, which
    contradicted the catalogue's own documented meaning of the flag ("models the
    RAG chat surface may offer"). It had been red for a long time, reporting one
    doc-gen-only id at a time -- which read as a single stale model rather than a
    category the assertion never accounted for.
    """
    checked = 0
    for descriptor in load_catalogue():
        if descriptor.provider in DOC_GENERATION_ONLY_PROVIDERS:
            continue
        assert descriptor.rag_capable is True, f"{descriptor.id} should be chat-offerable"
        checked += 1
    assert checked, "expected at least one direct-API model in the catalogue"


def test_doc_generation_only_lanes_are_not_offered_to_chat():
    """The other half of the rule, so the exemption cannot quietly widen."""
    for descriptor in load_catalogue():
        if descriptor.provider in DOC_GENERATION_ONLY_PROVIDERS:
            assert descriptor.rag_capable is False, (
                f"{descriptor.id} is a doc-generation lane and has no chat session "
                "in which to redeem its credential"
            )
