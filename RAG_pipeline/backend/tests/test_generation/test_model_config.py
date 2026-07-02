"""Slice 3: superadmin-curated LLM answer-model standard (model_config)."""
import pytest

from src.config import settings
from src.generation import model_config as mc


@pytest.fixture(autouse=True)
def _reset_override():
    mc.clear_override()
    yield
    mc.clear_override()


def test_defaults_without_override() -> None:
    assert mc.effective_provider_chain() == list(settings.llm_providers)
    assert mc.model_for("kimi") == settings.kimi_llm_model
    assert mc.model_for("ollama") == settings.ollama_llm_model


def test_is_allowed() -> None:
    assert mc.is_allowed("ollama", "gemma3:12b") is True
    assert mc.is_allowed("ollama", "not-a-model") is False
    assert mc.is_allowed("unknown-provider", "x") is False


def test_override_reorders_chain_and_sets_model() -> None:
    mc.set_override("ollama", "gemma3:12b")
    chain = mc.effective_provider_chain()
    assert chain[0] == "ollama"
    assert set(chain) == set(settings.llm_providers)  # reordered, not truncated
    assert mc.model_for("ollama") == "gemma3:12b"
    # A non-targeted provider keeps its env default.
    assert mc.model_for("kimi") == settings.kimi_llm_model


def test_set_override_rejects_disallowed() -> None:
    with pytest.raises(ValueError):
        mc.set_override("ollama", "bogus-model")


def test_load_from_row_valid_invalid_and_none() -> None:
    mc.load_from_row("ollama", "gemma3:4b")
    assert mc.get_override() == {"provider": "ollama", "model": "gemma3:4b"}

    # Invalid persisted value is ignored (cleared), not applied.
    mc.load_from_row("ollama", "bogus-model")
    assert mc.get_override() is None

    # Empty persisted config clears any prior override.
    mc.set_override("kimi", "kimi-k2.5")
    mc.load_from_row(None, None)
    assert mc.get_override() is None
