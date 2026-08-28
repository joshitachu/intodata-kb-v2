"""Run-level spend observability and governance.

Two measured problems are pinned here:

(a) COST WAS UNMEASURED. ``config.model_pricing_json`` defaulted to ``""`` and
    ``MCP_MODEL_PRICING_JSON`` was absent from the deployment environment, so
    ``load_model_pricing()`` returned ``{}`` and ``calculate_total_cost``
    returned ``None`` for every run -- ``GenerateRun.cost_usd`` was NULL for
    all rows. These tests pin that the shipped default table resolves a real
    number with NO env override, that the env var still overrides it, and that
    an unknown model degrades to ``None`` + a log line rather than to zero.

(b) INPUT SPEND WAS UNGOVERNED. ``docgen_max_run_input_tokens = 0`` makes
    ``harness._run_input_token_budget`` early-return 0, which makes the
    ``if input_budget > 0`` check in ``_reserve_model_budget`` dead code. These
    tests prove the plumbing enforces once enabled, pin that the default stays
    OFF (enabling it is a behaviour change the owner has not approved), and
    pin the measured reason it must not be switched on at the current dynamic
    formula constants.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import (
    FactsManifest,
    HarnessRunState,
    UnitFact,
)
from mcp_server.services.model_pricing import (
    DEFAULT_MODEL_PRICING,
    ModelPricing,
    calculate_model_cost,
    calculate_total_cost,
    find_model_pricing,
    load_model_pricing,
    parse_model_pricing,
    reset_unpriced_model_log,
)

# Observed shape of one production documentation run.
LIVE_RUN_INPUT_TOKENS = 2_000_000
LIVE_RUN_OUTPUT_TOKENS = 208_000


@pytest.fixture(autouse=True)
def _clean_pricing_state(monkeypatch):
    """Every test starts from "no env override" and a clean log guard."""
    monkeypatch.setattr(config, "model_pricing_json", "")
    reset_unpriced_model_log()
    yield
    reset_unpriced_model_log()


# --------------------------------------------------------------------------- #
# (a) Cost resolves out of the box
# --------------------------------------------------------------------------- #

def test_default_pricing_is_used_when_no_env_override_is_set():
    """The root cause: an empty MCP_MODEL_PRICING_JSON used to mean no table."""
    assert config.model_pricing_json == ""
    assert load_model_pricing() != {}
    assert load_model_pricing()["gpt-5.6-luna"] == ModelPricing(0.20, 1.20)


@pytest.mark.parametrize(
    "model_name",
    [
        "gpt-5.6-luna",
        "openai/gpt-5.6-luna",
        "opencode-go/gpt-5.6-luna",
        "anthropic/claude-sonnet-4-6",
        "claude-sonnet-4-6",
    ],
)
def test_models_in_production_use_resolve_a_real_cost(model_name):
    """A run's cost must stop being NULL for the models actually in use, both
    bare and provider-prefixed (the harness records whatever the provider
    reports as ``model_name``)."""
    cost = calculate_total_cost(
        [(model_name, LIVE_RUN_INPUT_TOKENS, LIVE_RUN_OUTPUT_TOKENS)]
    )
    assert cost is not None
    assert cost > 0


def test_live_run_cost_is_arithmetically_correct():
    # 2.0 MTok in * $0.20 + 0.208 MTok out * $1.20
    assert calculate_model_cost(
        "openai/gpt-5.6-luna", LIVE_RUN_INPUT_TOKENS, LIVE_RUN_OUTPUT_TOKENS
    ) == pytest.approx(0.6496)
    # Same run on the previous default write model.
    assert calculate_model_cost(
        "anthropic/claude-sonnet-4-6", LIVE_RUN_INPUT_TOKENS, LIVE_RUN_OUTPUT_TOKENS
    ) == pytest.approx(9.12)


def test_multi_model_run_totals_across_analysis_and_write_models():
    total = calculate_total_cost(
        [
            ("openai/gpt-5.6-luna", 1_800_000, 150_000),
            ("anthropic/claude-sonnet-4-6", 200_000, 58_000),
        ]
    )
    expected = (1.8 * 0.20 + 0.150 * 1.20) + (0.2 * 3.00 + 0.058 * 15.00)
    assert total == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# (a) The override still wins
# --------------------------------------------------------------------------- #

def test_env_override_wins_over_a_shipped_default(monkeypatch):
    monkeypatch.setattr(
        config, "model_pricing_json", '{"gpt-5.6-luna":{"input":9.0,"output":99.0}}'
    )
    assert load_model_pricing()["gpt-5.6-luna"] == ModelPricing(9.0, 99.0)
    assert calculate_model_cost("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(108.0)


def test_override_merges_per_model_and_leaves_other_defaults_intact(monkeypatch):
    """Correcting one rate must not blow away the rest of the table."""
    monkeypatch.setattr(
        config, "model_pricing_json", '{"gpt-5.6-luna":{"input":9.0,"output":99.0}}'
    )
    table = load_model_pricing()
    assert table["claude-sonnet-4-6"] == DEFAULT_MODEL_PRICING["claude-sonnet-4-6"]


def test_override_can_add_a_model_absent_from_the_defaults(monkeypatch):
    """DeepSeek is deliberately unshipped (peak/off-peak tariff); an operator
    picks a convention and sets it explicitly."""
    assert find_model_pricing("deepseek/deepseek-v4-flash") is None
    monkeypatch.setattr(
        config, "model_pricing_json", '{"deepseek-v4-flash":{"input":0.44,"output":1.32}}'
    )
    assert find_model_pricing("deepseek/deepseek-v4-flash") == ModelPricing(0.44, 1.32)


def test_null_override_removes_a_shipped_default(monkeypatch):
    """Escape hatch: an operator who distrusts a shipped rate can make that
    model report unknown cost instead of a possibly-stale number."""
    monkeypatch.setattr(config, "model_pricing_json", '{"claude-opus-4-7":null}')
    table = load_model_pricing()
    assert "claude-opus-4-7" not in table
    assert calculate_model_cost("anthropic/claude-opus-4-7", 1_000, 1_000) is None


def test_include_defaults_false_returns_the_override_table_alone(monkeypatch):
    monkeypatch.setattr(config, "model_pricing_json", '{"only":{"input":1,"output":2}}')
    assert load_model_pricing(include_defaults=False) == {"only": ModelPricing(1.0, 2.0)}


# --------------------------------------------------------------------------- #
# (a) Unknown models degrade to None, never to a wrong number
# --------------------------------------------------------------------------- #

def test_unknown_model_returns_none_and_logs_an_actionable_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.services.model_pricing"):
        assert find_model_pricing("some/never-seen-model") is None
    assert "some/never-seen-model" in caplog.text
    assert "MCP_MODEL_PRICING_JSON" in caplog.text


def test_unknown_model_is_logged_once_not_once_per_call(caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.services.model_pricing"):
        for _ in range(50):
            calculate_model_cost("some/never-seen-model", 10, 10)
    assert caplog.text.count("no USD rate for model") == 1


def test_unknown_model_never_prices_at_zero():
    """A wrong number is worse than no number: one unpriced call invalidates
    the whole run total rather than silently under-reporting it."""
    total = calculate_total_cost(
        [
            ("openai/gpt-5.6-luna", LIVE_RUN_INPUT_TOKENS, LIVE_RUN_OUTPUT_TOKENS),
            ("some/never-seen-model", 500_000, 50_000),
        ]
    )
    assert total is None


def test_no_shipped_rate_is_zero_except_declared_local_models():
    """Guards against a future edit adding a placeholder 0.0 rate, which would
    make an unpriced model indistinguishable from a genuinely free one."""
    local_prefixes = ("gemma3:", "qwen2.5:", "hermes3:")
    for name, rate in DEFAULT_MODEL_PRICING.items():
        if rate.input_per_million == 0.0 and rate.output_per_million == 0.0:
            assert name.startswith(local_prefixes), f"{name} is priced at zero"
        else:
            assert rate.input_per_million > 0 and rate.output_per_million > 0


def test_local_ollama_models_report_zero_dollars_not_unknown():
    assert calculate_total_cost([("ollama/gemma3:12b", 5_000_000, 500_000)]) == 0.0


def test_malformed_and_negative_override_rates_are_rejected_not_coerced(caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.services.model_pricing"):
        parsed = parse_model_pricing(
            '{"a":{"input":-1,"output":2},'
            '"b":{"input":"abc","output":2},'
            '"c":{"input":1},'
            '"d":{"input":1,"output":2}}'
        )
    assert set(parsed) == {"d"}
    assert "ignoring override" in caplog.text


def test_non_object_pricing_json_raises():
    with pytest.raises(ValueError):
        parse_model_pricing("[1, 2, 3]")


# --------------------------------------------------------------------------- #
# (b) The input-token governor: proven to work, and proven to be OFF
# --------------------------------------------------------------------------- #

def _facts(unit_count: int) -> FactsManifest:
    names = [f"Job_{i:03d}" for i in range(unit_count)]
    return FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=unit_count,
        groups={"A": unit_count},
        units=[UnitFact(name=name, group="A") for name in names],
    )


def _provider() -> CustomHarnessProvider:
    mp = MagicMock()
    mp.model_name = "test/model"
    return CustomHarnessProvider(mp)


def test_input_governor_is_off_by_default():
    """Enabling a governor that aborts runs is a behaviour change; the default
    must stay 0 until the owner approves it."""
    assert config.docgen_max_run_input_tokens == 0
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=_facts(10))
    assert provider._run_input_token_budget(state) == 0


@pytest.mark.asyncio
async def test_disabled_governor_admits_an_absurd_input_reservation():
    """With the cap at 0 the `input_budget > 0` check is dead code: a single
    call ten times the size of a whole real run is admitted."""
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=_facts(10))
    state.planned_call_budget = config.docgen_max_run_calls
    reservation, reason = await provider._reserve_model_budget(
        state,
        estimated_input_tokens=20_000_000,
        max_output_tokens=1_000,
        phase="phase2.claims",
    )
    assert reservation is not None, reason


def test_enabled_governor_computes_the_dynamic_budget(monkeypatch):
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 50_000_000)
    provider = _provider()
    facts = _facts(10)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    expected = (
        config.docgen_input_token_budget_base
        + 10 * config.docgen_input_token_budget_per_unit
        + len(provider._required_docs) * config.docgen_input_token_budget_per_doc
    )
    assert provider._run_input_token_budget(state) == expected


def test_enabled_governor_clamps_to_the_hard_ceiling(monkeypatch):
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 200_000)
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=_facts(500))
    assert provider._run_input_token_budget(state) == 200_000


@pytest.mark.asyncio
async def test_enabled_governor_blocks_a_call_that_would_exceed_the_budget(monkeypatch):
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 1_000_000)
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=_facts(1))
    state.planned_call_budget = config.docgen_max_run_calls
    budget = provider._run_input_token_budget(state)
    assert budget > 0

    # Under budget: admitted, and the reservation is booked as pending.
    granted, reason = await provider._reserve_model_budget(
        state,
        estimated_input_tokens=1_000,
        max_output_tokens=1_000,
        phase="phase2.claims",
    )
    assert granted is not None, reason
    assert state.pending_input_tokens >= 1_000

    # Over budget: blocked, with the input-budget reason (not the call or
    # output ceiling, which are both still far from their limits).
    blocked, reason = await provider._reserve_model_budget(
        state,
        estimated_input_tokens=budget * 2,
        max_output_tokens=1_000,
        phase="phase2.claims",
    )
    assert blocked is None
    assert "input token budget would be exceeded" in reason


@pytest.mark.asyncio
async def test_enabled_governor_counts_already_consumed_input(monkeypatch):
    """The cap is cumulative across the run, not per call."""
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 1_000_000)
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=_facts(1))
    state.planned_call_budget = config.docgen_max_run_calls
    budget = provider._run_input_token_budget(state)

    state.budget_consumed_input_tokens = budget - 1_000
    blocked, reason = await provider._reserve_model_budget(
        state,
        estimated_input_tokens=50_000,
        max_output_tokens=1_000,
        phase="phase3",
    )
    assert blocked is None
    assert "input token budget would be exceeded" in reason


def test_dynamic_formula_binds_below_observed_run_spend(monkeypatch):
    """MEASURED REASON NOT TO ENABLE THIS YET.

    The effective budget is min(cap, base + per_unit*units + per_doc*docs), so
    any positive cap also activates the dynamic formula. At the current
    constants that formula sits well under the ~2M input tokens a real run
    consumes for small and medium projects -- a 10-unit project's budget is
    ~1.06M -- so enabling the cap as-is would abort those runs mid-Phase-3.
    The formula only clears observed spend at roughly 48+ units.

    If this test starts failing because the constants were raised, that is the
    intended fix; update the numbers here and re-check the recommendation.
    """
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 6_000_000)
    provider = _provider()

    small = HarnessRunState(project_id="proj", facts_manifest=_facts(10))
    assert provider._run_input_token_budget(small) < LIVE_RUN_INPUT_TOKENS

    # The break-even unit count, derived rather than hardcoded.
    per_unit = config.docgen_input_token_budget_per_unit
    fixed = (
        config.docgen_input_token_budget_base
        + len(provider._required_docs) * config.docgen_input_token_budget_per_doc
    )
    break_even = -(-(LIVE_RUN_INPUT_TOKENS - fixed) // per_unit)
    assert break_even > 40
    large = HarnessRunState(project_id="proj", facts_manifest=_facts(break_even))
    assert provider._run_input_token_budget(large) >= LIVE_RUN_INPUT_TOKENS
