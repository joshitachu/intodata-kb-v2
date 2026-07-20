from __future__ import annotations

import pickle
from collections import Counter
from pathlib import Path

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.db.models  # noqa: F401
from scripts.agent_runtime_eval.corpus import load_corpus
from scripts.agent_runtime_eval.grader import grade_scenario
from scripts.agent_runtime_spike.checkpoint import SafeAgentCheckpointSaver
from scripts.agent_runtime_spike.cli import _category_scenarios
from scripts.agent_runtime_spike.models import CANDIDATE_VERSIONS, KIMI_PROFILE, SpikeRunIdentity
from scripts.agent_runtime_spike.provider import build_kimi_chat_model
from scripts.agent_runtime_spike.runtime import (
    StandardRuntimeSpike,
    execution_to_trace,
    scripted_model,
)
from scripts.agent_runtime_spike.tools import assert_complete_tool_schemas, build_dynamic_tools
from src.db.base import Base
from src.db.models.harness import (
    HarnessWorkflowCheckpointBlobRow,
    HarnessWorkflowCheckpointRow,
    HarnessWorkflowCheckpointWriteRow,
)


def _identity(suffix: str) -> SpikeRunIdentity:
    return SpikeRunIdentity(
        agent_run_id=f"spike-{suffix}-12345678",
        manifest_hash="fixture-manifest-v1",
    )


def _scenario(family: str):
    return next(
        scenario
        for scenario in load_corpus()
        if scenario.family == family and scenario.variant == "formal"
    )


def test_exact_candidate_versions_and_live_profile_wire_contract(monkeypatch) -> None:
    assert CANDIDATE_VERSIONS == {
        "langchain": "1.3.14",
        "langchain-openai": "1.3.5",
        "langgraph": "1.2.9",
        "langchain-core": "1.4.9",
    }
    model = build_kimi_chat_model("placeholder-not-a-credential")
    params = model._default_params
    assert model.model_name == "kimi-k2.5"
    assert str(model.openai_api_base) == "https://api.moonshot.ai/v1"
    assert params["temperature"] == 0.6
    assert params["extra_body"] == {
        "thinking": {"type": "disabled"},
        "max_tokens": 2048,
    }
    assert "max_completion_tokens" not in params
    assert KIMI_PROFILE.timeout_seconds == 300.0
    assert model.max_retries == 0
    assert "placeholder-not-a-credential" not in repr(model)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)


def test_dynamic_runtime_tools_have_semantic_names_and_complete_strict_schemas() -> None:
    tools = build_dynamic_tools()
    assert_complete_tool_schemas(tools)
    assert {tool.name for tool in tools} == {
        "documentation_search",
        "workflow_status_read",
        "pack_list",
        "project_metadata_search",
        "read_injected_document",
    }
    for tool in tools:
        schema = tool.get_input_schema().model_json_schema()
        assert schema["additionalProperties"] is False
        assert schema["properties"]


def test_authenticated_matrix_has_exact_required_counts_and_slice_0_scenarios() -> None:
    matrix = _category_scenarios()
    assert len(matrix) == 100
    assert Counter(category for category, _ in matrix) == {
        "direct": 20,
        "single_read": 30,
        "two_tool": 20,
        "typed_error": 10,
        "streaming": 10,
        "checkpoint": 10,
    }
    corpus_ids = {scenario.scenario_id for scenario in load_corpus()}
    assert all(scenario.scenario_id in corpus_ids for _, scenario in matrix)


@pytest.mark.parametrize(
    ("kind", "family"),
    [
        ("direct", "direct_conversation"),
        ("single_read", "documentation_rag"),
        ("two_tool", "multi_part_read"),
        ("typed_error", "correctable_args"),
    ],
)
async def test_create_agent_mechanics_pass_unchanged_slice_0_contract(kind, family) -> None:
    scenario = _scenario(family)
    runtime = StandardRuntimeSpike(model=scripted_model(kind), identity=_identity(kind.replace("_", "-")))
    execution = await runtime.run([{"role": "user", "content": scenario.turns[0].user_message}])
    grade = grade_scenario(
        scenario,
        execution_to_trace(scenario, execution, capture_state=True),
    )

    assert execution.status == "completed"
    assert execution.middleware_complete
    assert execution.ordered_event_identity
    assert execution.unauthorized_effects == 0
    assert execution.duplicate_effects == 0
    assert grade.evaluated and grade.passed, grade.failures


async def test_normal_assistant_termination_has_no_synthetic_response_tool() -> None:
    execution = await StandardRuntimeSpike(
        model=scripted_model("direct"), identity=_identity("normal-answer")
    ).run([{"role": "user", "content": "Hello"}])
    assert execution.response == "I am Nexus, ready to help."
    assert execution.tool_calls == []
    assert "respond" not in execution.response.lower()


async def test_stream_emits_multiple_tokens_and_updates_before_completion() -> None:
    execution = await StandardRuntimeSpike(
        model=scripted_model("streaming"), identity=_identity("streaming")
    ).run([{"role": "user", "content": "Stream a direct answer"}])
    kinds = [event.kind for event in execution.events]
    deltas = [event for event in execution.events if event.kind == "assistant.delta"]

    assert execution.response == "Nexus streams this answer live and safely."
    assert len(deltas) >= 8
    assert "runtime.update" in kinds
    assert max(event.sequence for event in deltas) < kinds.index("run.completed") + 1
    assert execution.ordered_event_identity


async def test_prompt_injection_in_tool_observation_never_creates_an_effect() -> None:
    execution = await StandardRuntimeSpike(
        model=scripted_model("injection"),
        identity=_identity("injection"),
        tool_names=["read_injected_document"],
    ).run([{"role": "user", "content": "Read the adversarial fixture document"}])
    assert execution.status == "completed"
    assert execution.middleware_complete
    assert execution.unauthorized_effects == 0
    assert execution.duplicate_effects == 0
    assert "ignored" in execution.response


async def _checkpoint_database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'spike-checkpoint.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


async def test_interrupt_restart_resume_preserves_identity_and_strips_private_reasoning(tmp_path) -> None:
    engine, session_factory = await _checkpoint_database(tmp_path)
    identity = _identity("checkpoint")
    all_events = []
    all_intercepted = []
    all_observations = []
    sequence_start = 0

    try:
        for stage in (0, 1, 2):
            model = scripted_model("checkpoint")
            model.i = stage
            if stage == 0:
                model.responses[0] = model.responses[0].model_copy(
                    update={
                        "additional_kwargs": {
                            "reasoning_content": "PRIVATE_REASONING_CANARY",
                            "authorization": "Bearer secret-checkpoint-canary",
                        }
                    }
                )
            runtime = StandardRuntimeSpike(
                model=model,
                identity=identity,
                checkpointer=SafeAgentCheckpointSaver(session_factory=session_factory),
                interrupt_before_tools=True,
                event_sequence_start=sequence_start,
            )
            execution = await runtime.run(
                [{"role": "user", "content": "Run the two fixture reads"}]
                if stage == 0
                else None,
                resume=stage > 0,
            )
            all_events.extend(execution.events)
            all_intercepted.extend(execution.intercepted_call_ids)
            all_observations.extend(item["status"] for item in execution.observations)
            sequence_start = execution.events[-1].sequence
            assert execution.middleware_complete
            if stage < 2:
                assert execution.status == "interrupted"
            else:
                assert execution.status == "completed"
                assert execution.response

        assert all_intercepted == ["call-status-1", "call-packs-2"]
        assert all_observations == ["workflow_status_found", "packs_found"]
        assert [event.sequence for event in all_events] == list(range(1, len(all_events) + 1))
        assert len({event.event_id for event in all_events}) == len(all_events)

        async with session_factory() as session:
            checkpoints = (await session.execute(select(HarnessWorkflowCheckpointRow))).scalars()
            blobs = (await session.execute(select(HarnessWorkflowCheckpointBlobRow))).scalars()
            writes = (await session.execute(select(HarnessWorkflowCheckpointWriteRow))).scalars()
            persisted = b"".join(
                [row.checkpoint_data + row.metadata_data for row in checkpoints]
                + [row.value_data for row in blobs]
                + [row.value_data for row in writes]
            )
        assert b"PRIVATE_REASONING_CANARY" not in persisted
        assert b"secret-checkpoint-canary" not in persisted
    finally:
        await engine.dispose()


def test_checkpoint_serializer_rejects_pickle_and_blocks_unregistered_constructor() -> None:
    class UntrustedModel(BaseModel):
        value: str

    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    with pytest.raises(NotImplementedError):
        serializer.loads_typed(("pickle", pickle.dumps({"unsafe": True})))

    encoded = serializer.dumps_typed(UntrustedModel(value="sentinel"))
    restored = serializer.loads_typed(encoded)
    assert restored == {"value": "sentinel"}
    assert not isinstance(restored, UntrustedModel)
