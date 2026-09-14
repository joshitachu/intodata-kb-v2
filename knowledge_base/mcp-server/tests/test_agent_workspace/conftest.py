"""Pytest fixtures for the ``agent-workspace-v1`` suite.

Construction helpers live in ``workspace_fixture.py`` so a test module can
import them directly; only pytest fixtures belong here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.config import config
from tests.test_agent_workspace.workspace_fixture import (
    RecordingAgent,
    design,
    write_scan,
)


@pytest.fixture
def scan_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A synthetic intodoc output directory, registered as THE scan location."""

    root = tmp_path / "intodoc-output"
    root.mkdir()
    write_scan(
        root,
        designs=[
            design("OrdersLoad", group="orders"),
            design("OrdersStage", group="orders"),
            design("InvoiceSync", group="invoices"),
        ],
    )
    monkeypatch.setattr(config, "intodoc_output_dir", root)
    return root


@pytest.fixture
def workspace(scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built workspace with the runtime lookup switched off.

    Off rather than mocked: "no runtime mirror is connected" is the normal
    production state and it is the state whose *statement* the tests care
    about.
    """

    from mcp_server.providers.agent_workspace.workspace import build_workspace

    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    target = tmp_path / "staging"
    build_workspace(
        project_id="PROJECT", staging_dir=target, platform="talend", goal="Document it."
    )
    return target


@pytest.fixture
def registered_fake():
    """Register a fake adapter through the factory seam, then remove it.

    Deliberately not a ``monkeypatch.setattr`` on the factory module: the
    point of the seam is that a provider id the profile can select becomes
    resolvable without reaching into module internals.
    """

    from mcp_server.providers.coding_agent import factory

    created: list[RecordingAgent] = []

    def register(agent: RecordingAgent, provider_id: str = "fake") -> RecordingAgent:
        factory.register_agent_provider(provider_id, lambda _model: agent)
        created.append(agent)
        return agent

    yield register
    factory.unregister_agent_provider("fake")


@pytest.fixture
def agent_ctx(tmp_path, monkeypatch):
    """A ``DocGenContext`` pointing at an isolated staging tree."""

    from mcp_server.config import config
    from mcp_server.providers.base import DocGenContext

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc_output")
    staging = tmp_path / "output" / "ACME_ZIP_TEST" / "docs_staging"
    staging.mkdir(parents=True)
    return DocGenContext(
        project_id="ACME_ZIP_TEST",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=0,
        total_groups=0,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="",
        run_id=7,
        generation_profile={
            "contract": "agent-workspace-v1",
            "agent_workspace": {"agent_provider": "fake"},
        },
    )


@pytest.fixture
def log_lines():
    lines: list[tuple[str, str]] = []

    async def log_cb(_project_id: str, message: str, level: str = "info") -> None:
        lines.append((level, message))

    log_cb.lines = lines  # type: ignore[attr-defined]
    return log_cb
