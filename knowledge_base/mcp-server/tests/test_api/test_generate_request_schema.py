"""Schema-level tests for the GenerateRequest plugin_pack_id override."""

from __future__ import annotations

import pytest

from mcp_server.schemas import GenerateRequest


def test_generate_request_accepts_plugin_pack_id_override():
    req = GenerateRequest(project_id="p1", plugin_pack_id=42)
    assert req.plugin_pack_id == 42


def test_generate_request_plugin_pack_id_optional():
    req = GenerateRequest(project_id="p1")
    assert req.plugin_pack_id is None


@pytest.mark.parametrize("bad", [0, -1, "abc", 1.5])
def test_generate_request_rejects_invalid_plugin_pack_id(bad):
    with pytest.raises(ValueError):
        GenerateRequest(project_id="p1", plugin_pack_id=bad)  # type: ignore[arg-type]


def test_generate_request_plugin_pack_id_explicit_null_means_use_project_default():
    req = GenerateRequest(project_id="p1", plugin_pack_id=None)
    assert req.plugin_pack_id is None