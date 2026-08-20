"""Unit tests for PluginRunner (plugin_runner.py) and plugin_schema.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.providers.custom.doc_catalog import load_doc_catalog as _load_test_catalog

_test_catalog = _load_test_catalog()
# Per-unit catalog entries expand at run start; the base name is not a doc.
_per_unit_base = {d.name for d in _test_catalog.per_unit_docs}
REQUIRED_DOCS = [d for d in _test_catalog.required_docs if d not in _per_unit_base]
DOC_ASSIGNMENTS = {
    bundle: [d for d in docs if d not in _per_unit_base]
    for bundle, docs in _test_catalog.doc_assignments.items()
}
from mcp_server.services.plugin_schema import (
    DocDefinition,
    PluginPackManifest,
    PluginStep,
    load_plugin_manifest,
)
from mcp_server.services.plugin_runner import PluginRunnerProvider, build_plugin_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manifest(**kwargs) -> PluginPackManifest:
    defaults = {
        "name": "test-pack",
        "version": "1.0.0",
        "steps": [{"id": "step1", "type": "harness"}],
    }
    defaults.update(kwargs)
    return PluginPackManifest.model_validate(defaults)


def _ctx(tmp_path: Path) -> DocGenContext:
    return DocGenContext(
        project_id="proj-test",
        staging_dir=tmp_path / "staging",
        staging_abs=str(tmp_path / "staging"),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["GroupA"],
        total_jobs=2,
        total_groups=1,
        effective_model="test/model",
        write_model="test/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )


def _mock_inner(success: bool = True, error: str | None = None) -> MagicMock:
    inner = MagicMock(spec=DocGenerationProvider)
    inner.run = AsyncMock(
        return_value=AgentResult(
            success=success,
            error=error,
            input_tokens=100,
            output_tokens=50,
        )
    )
    return inner


def _mock_model(text: str = "model output", success: bool = True) -> MagicMock:
    mp = MagicMock()
    mp.model_name = "test/model"
    mp.complete = AsyncMock(
        return_value=ModelResult(
            success=success,
            text=text,
            input_tokens=10,
            output_tokens=5,
            raw_error=None if success else "model error",
        )
    )
    return mp


async def _capture_log(project_id: str, message: str, level: str = "info") -> None:
    pass  # no-op; tests inspect call args on the mock


# ---------------------------------------------------------------------------
# plugin_schema tests
# ---------------------------------------------------------------------------

class TestLoadPluginManifest:
    def test_loads_valid_manifest(self, tmp_path: Path):
        data = {
            "name": "my-pack",
            "version": "2.0.0",
            "description": "Test pack",
            "steps": [
                {"id": "assess", "type": "harness"},
            ],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")
        manifest = load_plugin_manifest(tmp_path)
        assert manifest.name == "my-pack"
        assert manifest.version == "2.0.0"
        assert len(manifest.steps) == 1
        assert manifest.steps[0].type == "harness"

    def test_raises_if_no_yaml(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="plugin.yaml"):
            load_plugin_manifest(tmp_path)

    def test_raises_on_missing_steps(self, tmp_path: Path):
        data = {"name": "empty", "version": "1.0", "steps": []}
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(Exception):
            load_plugin_manifest(tmp_path)

    def test_llm_step_requires_prompt_file(self):
        with pytest.raises(Exception, match="prompt_file"):
            PluginStep(id="x", type="llm")

    def test_harness_step_no_prompt_required(self):
        step = PluginStep(id="x", type="harness")
        assert step.prompt_file is None

    def test_loads_manifest_without_quality_gates(self, tmp_path: Path):
        data = {"name": "pack", "version": "1.0", "steps": [{"id": "s", "type": "harness"}]}
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        manifest = load_plugin_manifest(tmp_path)

        assert manifest.quality_gates is None

    def test_loads_manifest_with_quality_gates(self, tmp_path: Path):
        data = {
            "name": "pack",
            "version": "1.0",
            "docs": [{"name": "WEBHOOK_SMOKE_TEST"}],
            "quality_gates": {
                "mode": "blocking",
                "required_terms": ["webhook", "PASS"],
                "required_sections": {"WEBHOOK_SMOKE_TEST": ["Smoke Test Result"]},
                "min_doc_bytes": {"WEBHOOK_SMOKE_TEST": 300},
                "required_evidence_terms": ["source"],
            },
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        manifest = load_plugin_manifest(tmp_path)

        assert manifest.quality_gates is not None
        assert manifest.quality_gates.mode == "blocking"
        assert manifest.quality_gates.required_terms == ["webhook", "PASS"]

    def test_loads_manifest_with_pack_contract_fields(self, tmp_path: Path):
        data = {
            "name": "pack",
            "version": "1.0",
            "docs": [{
                "name": "TABLE_INVENTORY",
                "evidence_profile": "table_inventory",
                "required_sections": ["Table Inventory"],
                "required_evidence": ["sql_queries"],
                "required_outputs": ["table_inventory"],
            }],
            "evidence_profiles": {
                "table_inventory": {
                    "include": ["sql_queries", "ddl_schema"],
                    "exclude": ["binary"],
                },
            },
            "validation": {"unknown_policy": "explicit", "mode": "advisory"},
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        manifest = load_plugin_manifest(tmp_path)

        assert manifest.docs[0].evidence_profile == "table_inventory"
        assert manifest.docs[0].required_evidence == ["sql_queries"]
        assert manifest.evidence_profiles["table_inventory"].include == ["sql_queries", "ddl_schema"]
        assert manifest.validation.mode == "advisory"

    def test_loads_manifest_with_evidence_agents(self, tmp_path: Path):
        data = {
            "name": "pack",
            "version": "1.0",
            "evidence_profiles": {
                "table_inventory": {
                    "include": ["sql_queries", "ddl_schema"],
                },
            },
            "evidence_agents": [
                {
                    "id": "table_sql_finder",
                    "uses": "builtin/sql_usage_agent",
                    "evidence_profile": "table_inventory",
                    "categories": ["sql_queries"],
                    "inputs": ["facts_manifest", "preprocess_chunks"],
                },
                {
                    "id": "ddl_alignment_agent",
                    "prompt_file": "prompts/agents/ddl_alignment.md",
                    "evidence_profile": "table_inventory",
                    "categories": ["ddl_schema"],
                    "inputs": ["facts_manifest", "evidence_registry"],
                },
            ],
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        manifest = load_plugin_manifest(tmp_path)

        assert manifest.evidence_agents[0].uses == "builtin/sql_usage_agent"
        assert manifest.evidence_agents[1].prompt_file == "prompts/agents/ddl_alignment.md"

    def test_invalid_evidence_agent_without_source_fails(self, tmp_path: Path):
        data = {
            "name": "pack",
            "version": "1.0",
            "evidence_agents": [{"id": "broken", "categories": ["sql_queries"]}],
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(Exception, match="exactly one"):
            load_plugin_manifest(tmp_path)

    def test_invalid_quality_gate_mode_fails(self, tmp_path: Path):
        data = {
            "name": "pack",
            "version": "1.0",
            "quality_gates": {"mode": "strict"},
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(Exception, match="mode"):
            load_plugin_manifest(tmp_path)


# ---------------------------------------------------------------------------
# PluginRunnerProvider — harness step
# ---------------------------------------------------------------------------

class TestHarnessStep:
    @pytest.mark.asyncio
    async def test_delegates_to_inner_provider(self, tmp_path: Path):
        inner = _mock_inner(success=True)
        ctx = _ctx(tmp_path)
        manifest = _manifest(steps=[{"id": "full", "type": "harness"}])

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is True
        inner.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_harness_failure_propagates(self, tmp_path: Path):
        inner = _mock_inner(success=False, error="generation blew up")
        ctx = _ctx(tmp_path)
        manifest = _manifest(steps=[{"id": "full", "type": "harness"}])

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is False
        assert "generation blew up" in (result.error or "")

    @pytest.mark.asyncio
    async def test_tokens_accumulated_from_harness(self, tmp_path: Path):
        inner = _mock_inner(success=True)
        ctx = _ctx(tmp_path)
        manifest = _manifest(steps=[{"id": "full", "type": "harness"}])

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        result = await runner.run(ctx, AsyncMock())

        assert result.input_tokens == 100
        assert result.output_tokens == 50


# ---------------------------------------------------------------------------
# PluginRunnerProvider — llm step
# ---------------------------------------------------------------------------

class TestLlmStep:
    @pytest.mark.asyncio
    async def test_calls_model_with_prompt(self, tmp_path: Path):
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "step1.md").write_text("Analyse this: {{context}}", encoding="utf-8")

        model = _mock_model(text="analysis result")
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(
            steps=[{"id": "step1", "type": "llm", "prompt_file": "prompts/step1.md"}]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner, model_provider=model)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is True
        model.complete.assert_awaited_once()
        call_prompt = model.complete.call_args[0][0]
        assert "Analyse this" in call_prompt

    @pytest.mark.asyncio
    async def test_missing_prompt_file_fails(self, tmp_path: Path):
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(
            steps=[{"id": "step1", "type": "llm", "prompt_file": "prompts/missing.md"}]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is False
        assert "missing.md" in (result.error or "")

    @pytest.mark.asyncio
    async def test_no_model_provider_fails(self, tmp_path: Path):
        (tmp_path / "p.md").write_text("prompt", encoding="utf-8")
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(
            steps=[{"id": "s1", "type": "llm", "prompt_file": "p.md"}]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner, model_provider=None)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is False
        assert "model_provider" in (result.error or "")


# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------

class TestContextPropagation:
    @pytest.mark.asyncio
    async def test_context_from_injects_prior_output(self, tmp_path: Path):
        (tmp_path / "p1.md").write_text("First prompt", encoding="utf-8")
        (tmp_path / "p2.md").write_text("Second prompt", encoding="utf-8")

        model = _mock_model(text="step output")
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(
            steps=[
                {"id": "s1", "type": "llm", "prompt_file": "p1.md"},
                {"id": "s2", "type": "llm", "prompt_file": "p2.md", "context_from": ["s1"]},
            ]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner, model_provider=model)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is True
        assert model.complete.await_count == 2
        # Second call should include s1's output in the prompt
        second_call_prompt = model.complete.call_args_list[1][0][0]
        assert "step output" in second_call_prompt
        assert "s1" in second_call_prompt

    @pytest.mark.asyncio
    async def test_missing_context_from_step_is_silently_skipped(self, tmp_path: Path):
        (tmp_path / "p.md").write_text("The prompt", encoding="utf-8")
        model = _mock_model()
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(
            steps=[{"id": "s1", "type": "llm", "prompt_file": "p.md", "context_from": ["nonexistent"]}]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner, model_provider=model)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is True


# ---------------------------------------------------------------------------
# SSE event emission
# ---------------------------------------------------------------------------

class TestSseEvents:
    @pytest.mark.asyncio
    async def test_emits_step_start_and_done(self, tmp_path: Path):
        inner = _mock_inner()
        ctx = _ctx(tmp_path)
        manifest = _manifest(steps=[{"id": "assess", "type": "harness"}])

        log_calls: list[tuple] = []

        async def capture(pid: str, msg: str, level: str = "info") -> None:
            log_calls.append((pid, msg, level))

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        await runner.run(ctx, capture)

        messages = [m for _, m, _ in log_calls]
        assert any("[STEP:start] assess" in m for m in messages)
        assert any("[STEP:done] assess" in m for m in messages)
        assert any("[PLUGIN:done]" in m for m in messages)

    @pytest.mark.asyncio
    async def test_emits_step_error_on_failure(self, tmp_path: Path):
        inner = _mock_inner(success=False, error="boom")
        ctx = _ctx(tmp_path)
        manifest = _manifest(steps=[{"id": "assess", "type": "harness"}])

        log_calls: list[tuple] = []

        async def capture(pid: str, msg: str, level: str = "info") -> None:
            log_calls.append((pid, msg, level))

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        await runner.run(ctx, capture)

        assert any("[STEP:error] assess" in m for _, m, _ in log_calls)

    @pytest.mark.asyncio
    async def test_stops_after_first_failure(self, tmp_path: Path):
        """Second step must NOT run if first fails."""
        inner = _mock_inner(success=False, error="fail")
        ctx = _ctx(tmp_path)
        second_inner = _mock_inner(success=True)
        manifest = _manifest(
            steps=[
                {"id": "step1", "type": "harness"},
                {"id": "step2", "type": "harness"},
            ]
        )

        runner = PluginRunnerProvider(manifest, tmp_path, inner)
        result = await runner.run(ctx, AsyncMock())

        assert result.success is False
        inner.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# build_plugin_runner factory
# ---------------------------------------------------------------------------

class TestBuildPluginRunner:
    def test_loads_from_directory(self, tmp_path: Path):
        data = {"name": "pack", "version": "1", "steps": [{"id": "s", "type": "harness"}]}
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        inner = _mock_inner()
        runner = build_plugin_runner(tmp_path, inner)

        assert isinstance(runner, PluginRunnerProvider)
        assert runner.manifest.name == "pack"


# ---------------------------------------------------------------------------
# Integration test: real integration_assessor plugin.yaml
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestIntegrationAssessorPack:
    """End-to-end smoke test: runs PluginRunnerProvider with the real
    integration_assessor plugin.yaml against a mock inner provider.

    This validates that:
    - The manifest loads without errors
    - The harness step delegates to the inner provider
    - SSE events are emitted (step_start, step_done, plugin_done)
    - No subprocess calls are involved
    - The result carries token counts from the inner run
    """

    _PACK_DIR = Path(__file__).resolve().parents[2] / "plugin_packs" / "integration_assessor"

    def test_pack_dir_exists_and_manifest_loads(self):
        assert self._PACK_DIR.exists(), f"Pack directory not found: {self._PACK_DIR}"
        manifest = load_plugin_manifest(self._PACK_DIR)
        assert manifest.name == "integration-assessor"
        assert manifest.version == "1.0.0"
        assert len(manifest.steps) >= 1
        assert manifest.steps[0].type == "harness"

    @pytest.mark.asyncio
    async def test_runner_delegates_harness_step_and_emits_events(self, tmp_path: Path):
        inner = _mock_inner(success=True)
        inner.run.return_value = AgentResult(
            success=True,
            input_tokens=420,
            output_tokens=180,
        )

        runner = build_plugin_runner(self._PACK_DIR, inner)

        log_calls: list[tuple[str, str, str]] = []

        async def log_cb(project_id: str, message: str, level: str = "info") -> None:
            log_calls.append((project_id, message, level))

        ctx = _ctx(tmp_path)
        result = await runner.run(ctx, log_cb)

        assert result.success is True
        assert result.input_tokens == 420
        assert result.output_tokens == 180

        messages = [m for _, m, _ in log_calls]
        step_id = runner.manifest.steps[0].id
        assert any(f"[STEP:start] {step_id}" in m for m in messages), \
            f"No [STEP:start] event found. Events: {messages}"
        assert any(f"[STEP:done] {step_id}" in m for m in messages), \
            f"No [STEP:done] event found. Events: {messages}"
        assert any("[PLUGIN:done]" in m for m in messages), \
            f"No [PLUGIN:done] event found. Events: {messages}"

        inner.run.assert_awaited_once_with(ctx, log_cb)

    @pytest.mark.asyncio
    async def test_runner_propagates_inner_failure(self, tmp_path: Path):
        inner = _mock_inner(success=False, error="Phase 1 LLM call failed")

        runner = build_plugin_runner(self._PACK_DIR, inner)

        async def log_cb(project_id: str, message: str, level: str = "info") -> None:
            pass

        result = await runner.run(_ctx(tmp_path), log_cb)

        assert result.success is False
        assert "Phase 1 LLM call failed" in (result.error or "")

    def test_plugin_runner_provider_is_not_subprocess_based(self):
        """PluginRunnerProvider itself uses no subprocess — it delegates to inner_provider."""
        import inspect
        from mcp_server.services import plugin_runner as mod
        src = inspect.getsource(mod)
        assert "subprocess" not in src, \
            "subprocess found in plugin_runner.py — PluginRunner should delegate, not spawn"


# ---------------------------------------------------------------------------
# Custom document sets (EP-005)
# ---------------------------------------------------------------------------

class TestCustomDocSet:
    def _make_provider(self, pack_dir: Path | None):
        from mcp_server.providers.custom.harness import CustomHarnessProvider
        mock_mp = MagicMock()
        mock_mp.model_name = "test/model"
        return CustomHarnessProvider(mock_mp, plugin_pack_dir=pack_dir)

    def test_default_pack_uses_required_docs(self, tmp_path: Path):
        """A pack without docs: key keeps the default 9-doc set."""
        data = {"name": "no-docs-pack", "version": "1.0", "steps": [{"id": "s", "type": "harness"}]}
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        provider = self._make_provider(tmp_path)

        assert provider._required_docs == list(REQUIRED_DOCS)
        assert provider._doc_assignments == dict(DOC_ASSIGNMENTS)

    def test_custom_docs_override_required_docs(self, tmp_path: Path):
        """A pack with docs: list replaces the default 9-doc set."""
        data = {
            "name": "custom-docs-pack",
            "version": "1.0",
            "docs": [
                {"name": "MIGRATION_PLAN", "sections": ["Executive Summary", "Migration Scope"]},
                {"name": "RISK_REGISTER", "sections": ["High Severity Risks"]},
                {"name": "DEPENDENCY_MAP"},
            ],
            "steps": [{"id": "s", "type": "harness"}],
        }
        (tmp_path / "plugin.yaml").write_text(yaml.dump(data), encoding="utf-8")

        provider = self._make_provider(tmp_path)

        assert provider._required_docs == ["MIGRATION_PLAN", "RISK_REGISTER", "DEPENDENCY_MAP"]
        assert provider._doc_assignments == {"custom": ["MIGRATION_PLAN", "RISK_REGISTER", "DEPENDENCY_MAP"]}
        assert provider._doc_bundle_map == {
            "MIGRATION_PLAN": "custom",
            "RISK_REGISTER": "custom",
            "DEPENDENCY_MAP": "custom",
        }
        assert provider._doc_section_requirements_extra["MIGRATION_PLAN"] == ["Executive Summary", "Migration Scope"]
        assert provider._doc_section_requirements_extra["RISK_REGISTER"] == ["High Severity Risks"]
        assert "DEPENDENCY_MAP" not in provider._doc_section_requirements_extra

    def test_no_plugin_pack_dir_uses_defaults(self):
        """No plugin_pack_dir at all — defaults preserved."""
        provider = self._make_provider(None)

        assert provider._required_docs == list(REQUIRED_DOCS)
        assert provider._doc_assignments == dict(DOC_ASSIGNMENTS)

    def test_per_doc_instruction_file_read(self, tmp_path: Path):
        """_doc_specific_from_pack reads the correct file and returns empty when absent."""
        from mcp_server.providers.custom.prompts import _doc_specific_from_pack
        docs_dir = tmp_path / "prompts" / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "MY_DOC.md").write_text("Custom instructions for MY_DOC", encoding="utf-8")

        assert _doc_specific_from_pack(tmp_path, "MY_DOC") == "Custom instructions for MY_DOC"
        assert _doc_specific_from_pack(tmp_path, "MISSING_DOC") == ""
        assert _doc_specific_from_pack(None, "MY_DOC") == ""

    @pytest.mark.integration
    def test_integration_assessor_backward_compat(self):
        """integration_assessor (no docs: key) still uses the 9-doc default set."""
        pack_dir = Path(__file__).resolve().parents[2] / "plugin_packs" / "integration_assessor"
        provider = self._make_provider(pack_dir)

        assert provider._required_docs == list(REQUIRED_DOCS), \
            "integration_assessor should use default 9-doc set (no docs: key in plugin.yaml)"
