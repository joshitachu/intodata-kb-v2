"""The runtime static-topology push is advisory to a completed scan."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from mcp_server.db.models import Project, ScanRun
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scan_output_lock import acquire_scan_output_lock


async def test_runtime_publication_failure_does_not_invalidate_completed_scan(
    session_factory, monkeypatch, tmp_path,
):
    import mcp_server.api.routes.projects as projects

    monkeypatch.setattr(projects, "async_session_factory", session_factory)
    output_dir = tmp_path / "scan-output"
    output_dir.mkdir()
    scan_lock = acquire_scan_output_lock(output_dir)
    assert scan_lock is not None
    repo = tmp_path / "repo"
    repo.mkdir()

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project("static-topology", str(repo), platform="talend")
        run = await registry.create_scan_run("static-topology", triggered_by="api")
        await session.commit()
        run_id = run.id

    log_manager = MagicMock()
    log_manager.reset_log_file = MagicMock()
    log_manager.emit = AsyncMock()

    async def _no_component_summary(*_args, **_kwargs):
        return None

    with (
        patch.object(projects.ScannerService, "run_scan", new=AsyncMock(return_value={
            "success": True,
            "scan_result_path": str(output_dir / "scan_result.json"),
            "output_dir": str(output_dir),
            "job_count": 2,
        })),
        patch.object(projects, "_safe_build_component_summaries", new=_no_component_summary),
        patch.object(projects, "StaticTopologyPublisher") as publisher_cls,
    ):
        publisher_cls.return_value.publish = AsyncMock(side_effect=RuntimeError("runtime unavailable"))
        await projects._run_scan_background(
            "static-topology", str(repo), "talend", run_id, log_manager, str(output_dir), scan_lock,
        )

    async with session_factory() as session:
        project = await session.get(Project, "static-topology")
        scan_run = (await session.execute(select(ScanRun).where(ScanRun.id == run_id))).scalar_one()

    assert project is not None and project.status == "ready"
    assert project.job_count == 2
    assert scan_run.status == "completed"
    publisher_cls.return_value.publish.assert_awaited_once_with(
        project_id="static-topology", output_dir=str(output_dir), source_sequence=run_id,
    )
    assert any(
        "completed scan remains valid" in call.args[1]
        for call in log_manager.emit.await_args_list
    )
    replacement_lock = acquire_scan_output_lock(output_dir)
    assert replacement_lock is not None
    replacement_lock.release()
