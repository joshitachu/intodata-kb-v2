"""Tests for database models."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mcp_server.db.models import GenerateRun, Project, ScanRun


async def test_create_project(db_session):
    p = Project(
        id="test",
        display_name="Test Project",
        local_path="/tmp/test",
        platform="talend",
        source_type="local",
    )
    db_session.add(p)
    await db_session.commit()

    result = await db_session.execute(select(Project).where(Project.id == "test"))
    fetched = result.scalar_one()
    assert fetched.display_name == "Test Project"
    assert fetched.created_at is not None


async def test_scan_run_relationship(db_session):
    p = Project(id="r", display_name="R", local_path="/tmp/r", platform="talend")
    db_session.add(p)
    await db_session.flush()

    run = ScanRun(project_id="r", triggered_by="api", status="running")
    db_session.add(run)
    await db_session.commit()

    result = await db_session.execute(
        select(ScanRun).where(ScanRun.project_id == "r")
    )
    fetched = result.scalar_one()
    assert fetched.triggered_by == "api"


async def test_cascade_delete(db_session):
    p = Project(id="del", display_name="Del", local_path="/tmp/del", platform="talend")
    db_session.add(p)
    await db_session.flush()

    run = ScanRun(project_id="del", triggered_by="api", status="running")
    db_session.add(run)
    gen = GenerateRun(project_id="del", triggered_by="api", status="running")
    db_session.add(gen)
    await db_session.commit()

    # Delete the project
    await db_session.delete(p)
    await db_session.commit()

    # Verify cascade: runs should be gone
    scan_result = await db_session.execute(
        select(ScanRun).where(ScanRun.project_id == "del")
    )
    assert scan_result.scalar_one_or_none() is None

    gen_result = await db_session.execute(
        select(GenerateRun).where(GenerateRun.project_id == "del")
    )
    assert gen_result.scalar_one_or_none() is None
