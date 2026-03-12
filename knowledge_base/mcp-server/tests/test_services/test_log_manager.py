"""Tests for LogManager service."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server.services.log_manager import LogManager


async def test_emit_writes_to_file(log_manager, tmp_path):
    await log_manager.emit("proj", "Test message", level="info")

    # Check that a log file was created and contains the message
    log_files = list((tmp_path / "logs").glob("proj_*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "Test message" in content
    assert "[INFO]" in content


async def test_subscribe_and_receive(log_manager):
    queue = log_manager.subscribe("proj")

    await log_manager.emit("proj", "Hello subscriber", level="info")

    event = queue.get_nowait()
    assert event["project_id"] == "proj"
    assert "Hello subscriber" in event["message"]


async def test_unsubscribe(log_manager):
    queue = log_manager.subscribe("proj")
    log_manager.unsubscribe("proj", queue)

    await log_manager.emit("proj", "After unsubscribe", level="info")

    assert queue.empty()


async def test_emit_done(log_manager):
    queue = log_manager.subscribe("proj")
    # Need a log file path first
    await log_manager.emit("proj", "setup", level="info")

    await log_manager.emit_done("proj")

    event = queue.get_nowait()  # skip the setup emit
    done_event = queue.get_nowait()
    assert done_event["level"] == "done"
    assert done_event["project_id"] == "proj"


async def test_reset_log_file(log_manager):
    await log_manager.emit("proj", "Before reset", level="info")
    path_before = log_manager.get_current_log_path("proj")
    assert path_before is not None

    log_manager.reset_log_file("proj")

    # After reset, the internal mapping is cleared
    assert log_manager.get_current_log_path("proj") is None
