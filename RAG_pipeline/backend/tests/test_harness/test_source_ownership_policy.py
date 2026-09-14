"""Deterministic semantic contract tests for evidence-source ownership.

These tests do not ask a probabilistic model to classify a sentence. They lock
the exact model-visible capability snapshot and shared turn guidance that route
the production model, including regressions derived from runs 525-531.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import READ_ONLY_PRIMARY_SYSTEM_PROMPT
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import build_semantic_tool_snapshot
from src.harness.surface_policy import surface_denied_capability_ids
from src.harness.topology_guidance import (
    CHAT_TOPOLOGY_BOUNDARY,
    SHARED_ANSWER_GUIDANCE,
)

TOPOLOGY_PAGE_SCOPE = {
    "surface": "topology",
    "project_id": "project-a",
    "level": {"kind": "project"},
}


@dataclass(frozen=True)
class SourceScenario:
    run: str
    question: str
    required_tools: tuple[str, ...]
    excluded_tools: tuple[str, ...] = ()


RUN_525_TO_531_SCENARIOS = (
    SourceScenario(
        "525",
        "What does this project do and which systems are involved?",
        ("read_topology_structure", "answer_from_documentation"),
    ),
    SourceScenario(
        "526",
        "Give me a factual overview of ETTM, including what it does.",
        ("read_topology_structure", "answer_from_documentation"),
    ),
    SourceScenario(
        "527",
        "Do not use topology; answer from documentation.",
        ("answer_from_documentation",),
        ("read_topology_structure",),
    ),
    SourceScenario(
        "528",
        "Tell me about Maximo then.",
        ("answer_from_documentation",),
    ),
    SourceScenario(
        "529",
        "Not from topology, but from documents.",
        ("answer_from_documentation",),
        ("read_topology_structure",),
    ),
    SourceScenario(
        "530",
        "What does Maximo do in this project?",
        ("answer_from_documentation",),
    ),
    SourceScenario(
        "531",
        "Explain inventory management within Maximo in this project.",
        ("answer_from_documentation",),
    ),
)


@pytest.fixture(scope="module")
def descriptions() -> dict[str, str]:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    names = {
        "read_topology_structure",
        "answer_from_documentation",
        "search_documentation",
        "read_runtime_evidence",
        "search_runtime_logs",
    }
    return {
        name: snapshot.tool_for_name(name).description
        for name in names
        if snapshot.tool_for_name(name) is not None
    }


def test_semantic_snapshot_assigns_complementary_source_ownership(
    descriptions: dict[str, str],
) -> None:
    topology = descriptions["read_topology_structure"]
    documentation = descriptions["answer_from_documentation"]
    runtime = descriptions["read_runtime_evidence"]
    logs = descriptions["search_runtime_logs"]

    for term in ("inventory", "counts", "connections", "dependency traversal"):
        assert term in topology
    for term in ("what a project", "why it exists", "business rules", "mappings"):
        assert term in documentation
    for term in ("present-tense", "executions", "failures", "health"):
        assert term in runtime
    assert "present-tense runtime log evidence" in logs


@pytest.mark.parametrize("scenario", RUN_525_TO_531_SCENARIOS, ids=lambda item: item.run)
def test_runs_525_to_531_have_an_explicit_semantic_route(
    scenario: SourceScenario,
    descriptions: dict[str, str],
) -> None:
    """Every observed failure case is represented by a visible source contract."""

    assert scenario.question
    for tool_name in scenario.required_tools:
        assert tool_name in descriptions
        assert "Purpose:" in descriptions[tool_name]
        assert "Use:" in descriptions[tool_name]
    for tool_name in scenario.excluded_tools:
        assert "explicitly excludes topology" in descriptions[tool_name]


def test_purpose_and_structural_inventory_are_not_claimed_by_both_sources(
    descriptions: dict[str, str],
) -> None:
    topology = descriptions["read_topology_structure"]
    documentation = descriptions["answer_from_documentation"]

    assert "asks only for purpose" in topology
    assert "use indexed documentation" in topology
    assert "asks only for complete inventory" in documentation
    assert "use topology rather than similarity search" in documentation
    assert "not from documents" not in topology.casefold()
    assert "not from documents" not in CHAT_TOPOLOGY_BOUNDARY.casefold()


def test_mixed_source_order_keeps_all_evidence_in_the_current_turn(
    descriptions: dict[str, str],
) -> None:
    """The ordering rule lives with the tool it constrains, and in the base prompt.

    It used to be restated in the per-surface guidance as well. That copy is gone:
    the rule is about one capability's terminal behaviour, so the capability
    contract and the shared system prompt are where it belongs, and a third copy
    was free to drift from both.
    """

    documentation = descriptions["answer_from_documentation"]

    assert "topology and runtime tools first" in documentation
    assert "terminal documentation answer" in documentation
    assert "call every needed topology and runtime read before" in (
        READ_ONLY_PRIMARY_SYSTEM_PROMPT
    )


def test_the_canvas_has_a_non_terminal_documentation_route(
    descriptions: dict[str, str],
) -> None:
    """Why the ordering rule above is no longer load-bearing on the canvas.

    ``answer_from_documentation`` is terminal: it returns a deterministic RAG
    answer and short-circuits the turn, so structure read beforehand never
    reaches the prose. When it was the canvas's ONLY documentation route, an
    ordering instruction was the only thing standing between a mixed question
    and a document answer that ignored the diagram. ``search_documentation`` is
    the non-terminal alternative, and it must be reachable there.
    """

    denied = surface_denied_capability_ids(
        default_capability_registry().list(), TOPOLOGY_PAGE_SCOPE
    )
    assert "rag.docs.search_evidence" not in denied
    assert "search_documentation" in descriptions


def test_explicit_source_exclusion_is_honored_or_disclosed(
    descriptions: dict[str, str],
) -> None:
    for tool_name, phrase in (
        ("read_topology_structure", "explicitly excludes topology"),
        ("answer_from_documentation", "explicitly excludes documentation"),
        ("search_documentation", "explicitly excludes documentation"),
        ("read_runtime_evidence", "explicitly excludes runtime evidence"),
        ("search_runtime_logs", "explicitly excludes logs"),
    ):
        assert phrase in descriptions[tool_name]
    assert "do not call that source" in READ_ONLY_PRIMARY_SYSTEM_PROMPT
    assert "answer only the supported" in READ_ONLY_PRIMARY_SYSTEM_PROMPT


def test_present_tense_and_explanatory_claims_cannot_be_inferred_across_families() -> None:
    prompt = " ".join(READ_ONLY_PRIMARY_SYSTEM_PROMPT.split())

    assert "Runtime evidence must not be inferred from documented design" in prompt
    assert "business purpose must not be inferred from topology structure" in prompt
