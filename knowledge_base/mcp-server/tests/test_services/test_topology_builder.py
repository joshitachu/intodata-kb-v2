"""The provider-agnostic topology pipeline (`services/business_topology/builder.py`).

Pins the lane order Phase 3.7 always had (flow extraction -> single-shot ->
deterministic floor), that ``llm=None`` is REPORTED rather than silently
downgraded, that enrichment is advisory (it can improve a topology, never cost
the run one), that the bounded CLI strategy cannot exceed its call cap, and
that nothing here can raise into a documentation run.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mcp_server.providers.custom.models import ModelResult
from mcp_server.services.business_topology.builder import (
    PURPOSE_FLOW_EXTRACTION,
    PURPOSE_SINGLE_SHOT,
    BoundedModelTopologyLLM,
    TopologyBuildInputs,
    TopologyCompletion,
    build_business_topology,
)
from tests.test_services.test_topology_flow_assembly import (
    _EXTRACTION,
    _write_docs,
    _write_scan,
)


def _inputs(tmp_path: Path, **overrides: Any) -> TopologyBuildInputs:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    _write_docs(staging)
    values: dict[str, Any] = {
        "project_id": "demo",
        "staging_dir": staging,
        "scan_output_dir": scan_root,
    }
    values.update(overrides)
    return TopologyBuildInputs(**values)


class _ScriptedLLM:
    """Returns a fixed answer per purpose and records what it was asked."""

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = answers
        self.purposes: list[str] = []
        self.target_ids: list[str] = []

    async def complete(self, prompt, *, context_files, purpose, target_id, validate):
        self.purposes.append(purpose)
        self.target_ids.append(target_id)
        text = self.answers.get(purpose)
        if text is None:
            return None
        return TopologyCompletion(text=text, model_name="scripted")


@dataclass
class _Report:
    filled: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    runtime_evidence: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"filled": dict(self.filled), "skipped": dict(self.skipped)}


def _install_enrichment(monkeypatch: pytest.MonkeyPatch, enrich) -> list:
    """Replace the enrichment module with a controllable double."""

    seen: list = []

    @dataclass(frozen=True)
    class EnrichmentSources:
        scan_dir: Path
        runtime_payload: dict | None = None
        docs_dir: Path | None = None
        unit_allowlist: frozenset[str] | None = None

    def enrich_topology(topology, sources):
        seen.append(sources)
        return enrich(topology, sources)

    def register_runtime_evidence(bundle, records):
        return bundle

    def persist_runtime_evidence(evidence_root, records):
        seen.append(("persist", dict(records)))

    module = types.ModuleType("mcp_server.services.business_topology.enrichment")
    module.EnrichmentSources = EnrichmentSources  # type: ignore[attr-defined]
    module.enrich_topology = enrich_topology  # type: ignore[attr-defined]
    module.register_runtime_evidence = register_runtime_evidence  # type: ignore[attr-defined]
    module.persist_runtime_evidence = persist_runtime_evidence  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "mcp_server.services.business_topology.enrichment", module
    )
    return seen


def _identity(topology, sources):
    return topology, _Report()


async def test_llm_none_publishes_the_floor_and_says_why(tmp_path, monkeypatch) -> None:
    _install_enrichment(monkeypatch, _identity)
    inputs = _inputs(tmp_path, llm_unavailable_reason="engine has no completion call")

    result = await build_business_topology(inputs, None)

    assert result.available and result.validated
    assert result.lane == "deterministic"
    assert result.state["model_lanes"] == {
        "enabled": False,
        "reason": "engine has no completion call",
    }
    assert result.metadata["model_lanes_enabled"] is False
    result.write(inputs.staging_dir / "_topology")
    topo_dir = inputs.staging_dir / "_topology"
    assert json.loads((topo_dir / "validation_report.json").read_text())["validated"] is True
    assert (topo_dir / "business_topology.json").is_file()
    state = json.loads((topo_dir / "state.json").read_text())
    assert state["lane"] == "deterministic"
    assert state["enrichment"]["status"] == "applied"


async def test_flow_extraction_lane_wins_and_single_shot_is_not_called(
    tmp_path, monkeypatch
) -> None:
    _install_enrichment(monkeypatch, _identity)
    llm = _ScriptedLLM({PURPOSE_FLOW_EXTRACTION: json.dumps(_EXTRACTION)})

    result = await build_business_topology(_inputs(tmp_path, subproject="Assetcheck"), llm)

    assert result.validated, result.validation_json
    assert result.lane == "flow_extraction"
    assert llm.purposes == [PURPOSE_FLOW_EXTRACTION]
    # Target ids are fingerprint-scoped exactly as Phase 3.7 persisted them.
    assert llm.target_ids[0].startswith("flow_extraction_")
    assert result.topology is not None and result.topology.business_objects
    assert result.state["model_lanes"] == {"enabled": True}


async def test_lanes_fall_through_in_order_to_the_floor(tmp_path, monkeypatch) -> None:
    _install_enrichment(monkeypatch, _identity)
    llm = _ScriptedLLM({})
    logs: list[tuple[str, str]] = []

    async def _log(message: str, level: str) -> None:
        logs.append((message, level))

    result = await build_business_topology(_inputs(tmp_path), llm, log=_log)

    assert llm.purposes == [PURPOSE_FLOW_EXTRACTION, PURPOSE_SINGLE_SHOT]
    assert result.lane == "deterministic" and result.validated
    assert any("model lanes did not produce" in message for message, _ in logs)
    assert all(message.startswith("[custom] ") for message, _ in logs)


async def test_enrichment_report_and_sources_are_recorded(tmp_path, monkeypatch) -> None:
    def _fill(topology, sources):
        return topology, _Report(filled={"systems.description": 2}, skipped={"x": "why"})

    seen = _install_enrichment(monkeypatch, _fill)
    runtime = {"mode": "data", "units": []}
    inputs = _inputs(
        tmp_path, runtime_payload=runtime, unit_allowlist=frozenset({"Maximo2AssetCheck_SendWorkOrder"})
    )

    result = await build_business_topology(inputs, None)

    assert result.validated
    enrichment = result.state["enrichment"]
    assert enrichment["status"] == "applied"
    assert enrichment["filled"] == {"systems.description": 2}
    assert enrichment["skipped"] == {"x": "why"}
    assert enrichment["runtime_payload"] is True
    sources = seen[0]
    assert sources.scan_dir == inputs.scan_output_dir
    assert sources.docs_dir == inputs.staging_dir
    assert sources.runtime_payload is runtime
    assert sources.unit_allowlist == frozenset({"Maximo2AssetCheck_SendWorkOrder"})


async def test_enrichment_that_breaks_validation_is_discarded(tmp_path, monkeypatch) -> None:
    def _corrupt(topology, sources):
        broken = topology.model_copy(deep=True)
        broken.scan_fingerprint = "not-the-scan"  # a global, non-prunable defect
        return broken, _Report(
            filled={"systems.description": 1}, runtime_evidence={"runtime:x": {"id": "runtime:x"}}
        )

    seen = _install_enrichment(monkeypatch, _corrupt)

    result = await build_business_topology(_inputs(tmp_path), None)

    assert result.available and result.validated
    assert result.topology is not None
    assert result.topology.scan_fingerprint != "not-the-scan"
    assert result.state["enrichment"]["status"] == "rejected"
    assert result.state["enrichment"]["validation_errors"]
    # The rejected overlay's runtime evidence is not persisted; a stale ledger
    # entry from an earlier attempt is cleared instead.
    assert ("persist", {}) in seen


async def test_real_enrichment_runs_and_the_result_still_validates(tmp_path) -> None:
    """No double: the shipped enrichment module against the shared fixture."""

    inputs = _inputs(tmp_path)

    result = await build_business_topology(inputs, None)

    assert result.available and result.validated, result.validation_json
    enrichment = result.state["enrichment"]
    assert enrichment["status"] in {"applied", "rejected"}, enrichment
    assert "filled" in enrichment and "skipped" in enrichment
    result.write(inputs.staging_dir / "_topology")
    # What publication re-reads must still accept the served payload.
    from mcp_server.services.business_topology import (
        BusinessTopology,
        load_business_topology_evidence,
        validate_business_topology,
    )

    served = BusinessTopology.model_validate_json(
        (inputs.staging_dir / "_topology" / "business_topology.json").read_text()
    )
    reloaded = load_business_topology_evidence(inputs.staging_dir / "_topology")
    assert validate_business_topology(served, bundle=reloaded, project_id="demo").validated


async def test_enrichment_failure_is_isolated(tmp_path, monkeypatch) -> None:
    def _boom(topology, sources):
        raise RuntimeError("enrichment exploded")

    _install_enrichment(monkeypatch, _boom)

    result = await build_business_topology(_inputs(tmp_path), None)

    assert result.available and result.validated
    assert result.state["enrichment"] == {"status": "failed", "error": "enrichment exploded"}


async def test_builder_never_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_server.services.business_topology.build_business_topology_evidence",
        lambda **_: (_ for _ in ()).throw(RuntimeError("synthetic evidence failure")),
    )

    result = await build_business_topology(_inputs(tmp_path), None)

    assert result.available is False and result.validated is False
    report = json.loads(result.validation_json)
    assert "synthetic evidence failure" in report["errors"][0]
    assert result.run_warnings
    assert result.state["model_lanes"]["enabled"] is False


class _FakeProvider:
    model_name = "fake-model"

    def __init__(self, text: str = "{}", success: bool = True) -> None:
        self.text = text
        self.success = success
        self.calls = 0

    async def complete(self, prompt, context_files=None, options=None):
        self.calls += 1
        assert options is not None and options.retries == 0
        return ModelResult(success=self.success, text=self.text, model_name=self.model_name)


async def test_bounded_strategy_cannot_exceed_its_call_cap() -> None:
    provider = _FakeProvider(text='{"ok": true}')
    strategy = BoundedModelTopologyLLM(provider, max_calls=1)

    def _validate(text: str) -> dict:
        return {"ok": False, "errors": ["still wrong"]}

    first = await strategy.complete(
        "p", context_files=[], purpose="flow_extraction", target_id="t1", validate=_validate
    )
    second = await strategy.complete(
        "p", context_files=[], purpose="business_topology", target_id="t2", validate=_validate
    )

    # A rejected completion is still handed on: the pipeline prunes it.
    assert first == TopologyCompletion(text='{"ok": true}', model_name="fake-model")
    assert second is None
    assert provider.calls == 1
    assert [call["status"] for call in strategy.calls] == ["ok", "skipped_call_budget"]
    assert strategy.calls[0]["accepted"] is False


async def test_bounded_strategy_turns_provider_failure_into_a_fallback() -> None:
    strategy = BoundedModelTopologyLLM(_FakeProvider(success=False), max_calls=2)

    result = await strategy.complete(
        "p", context_files=[], purpose="flow_extraction", target_id="t",
        validate=lambda text: {"ok": True},
    )

    assert result is None
    assert strategy.calls[0]["status"] == "failed"


async def test_runtime_enrichment_publishes_and_survives_the_publication_reload(
    tmp_path,
) -> None:
    """Runtime observations cite evidence the scanner bundle lacks.

    The builder must register it for validation AND persist it into the staged
    ledger, or the read path (which reloads from disk and re-validates) rejects
    the published overlay.
    """

    from mcp_server.services.business_topology import (
        BusinessTopology,
        load_business_topology_evidence,
        validate_business_topology,
    )
    from tests.test_services.test_topology_enrichment import (
        PROJECT,
        _runtime_payload,
        _write_scan as _write_enrichment_scan,
    )

    scan_root = _write_enrichment_scan(tmp_path / "scan_cache")
    staging = tmp_path / "staging"
    staging.mkdir()
    inputs = TopologyBuildInputs(
        project_id=PROJECT,
        staging_dir=staging,
        scan_output_dir=scan_root,
        runtime_payload=_runtime_payload(),
    )

    result = await build_business_topology(inputs, None)

    assert result.validated, result.validation_json
    enrichment = result.state["enrichment"]
    assert enrichment["status"] == "applied", enrichment
    assert enrichment["runtime_evidence_ids"]
    assert result.topology is not None and result.topology.runtime_observations
    result.write(staging / "_topology")

    served = BusinessTopology.model_validate_json(
        (staging / "_topology" / "business_topology.json").read_text()
    )
    reloaded = load_business_topology_evidence(staging / "_topology")
    assert set(enrichment["runtime_evidence_ids"]) <= reloaded.evidence_ids
    report = validate_business_topology(served, bundle=reloaded, project_id=PROJECT)
    assert report.validated, report.errors
