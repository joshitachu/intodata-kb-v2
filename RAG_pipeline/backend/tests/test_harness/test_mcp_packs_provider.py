"""The RAG->MCP pack-authoring gateway.

These tests pin the boundary, not the happy path alone: the adapter must refuse
model-shaped junk (traversal paths, oversized file sets, bad ids) BEFORE it
crosses the service boundary, and must never leak a token into an error string.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.harness.providers.mcp_packs import (
    McpPackProvider,
    McpPackProviderError,
)


def _provider(handler) -> McpPackProvider:
    return McpPackProvider(
        base_url="http://mcp.test/mcp",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def _captured(store: list[httpx.Request], payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        store.append(request)
        return httpx.Response(status, json=payload)

    return handler


@pytest.mark.asyncio
async def test_draft_pack_forwards_intent_and_access_token():
    seen: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/architect/sessions"):
            return httpx.Response(201, json={"id": "abc123", "revision": 1})
        if request.method == "POST":
            return httpx.Response(202, json={
                "id": "abc123", "revision": 2, "turn_status": "running"
            })
        return httpx.Response(200, json={
                "manifest_yaml": "name: sql-review\nversion: 1.0.0\n",
                "name": "sql-review",
                "files": {"prompts/docs/MY_DOC.md": "# doc"},
                "complete": True,
                "turn_status": "idle",
                "messages": [{"role": "assistant", "content": "Draft ready"}],
            })
    provider = _provider(handler)

    draft = await provider.draft_pack(
        intent="find sql injection", platform="talend", access_token="tok-abc"
    )

    assert draft.name == "sql-review"
    assert draft.manifest_yaml.startswith("name: sql-review")
    assert draft.files == {"prompts/docs/MY_DOC.md": "# doc"}
    assert draft.complete is True

    assert seen[0].url.path == "/mcp/plugins/architect/sessions"
    assert json.loads(seen[0].content) == {"platform": "talend"}
    request = seen[1]
    assert request.url.path == "/mcp/plugins/architect/sessions/abc123/messages"
    assert json.loads(request.content) == {
        "message": "find sql injection",
        "expected_revision": 1,
    }
    assert seen[2].method == "GET"
    assert seen[2].url.path == "/mcp/plugins/architect/sessions/abc123"
    # Identity travels as the access-token cookie; MCP keys personal packs on it.
    assert "access_token=tok-abc" in request.headers.get("cookie", "")


@pytest.mark.asyncio
async def test_draft_pack_rejects_empty_manifest_from_the_author():
    calls = 0
    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"id": "abc", "revision": 1}) if calls == 1 else httpx.Response(200, json={"manifest_yaml": "   ", "messages": []})
    provider = _provider(handler)
    with pytest.raises(McpPackProviderError, match="empty manifest"):
        await provider.draft_pack(intent="anything at all")


@pytest.mark.asyncio
async def test_create_personal_pack_disables_starter_inheritance_for_authored_drafts():
    seen: list[httpx.Request] = []
    provider = _provider(
        _captured(
            seen,
            {
                "id": 7,
                "name": "sql-review",
                "version": "1.0.0",
                "description": None,
                "origin": "personal",
                "personal": True,
                "usable": True,
                "certification_status": "legacy_untrusted",
            },
            status=201,
        )
    )

    pack = await provider.create_personal_pack(
        name="sql review",
        manifest_yaml="name: sql-review\n",
        files={"prompts/phase2.md": "body"},
        inherit_starter=False,
        access_token="tok",
    )

    assert (pack.id, pack.personal, pack.usable) == (7, True, True)
    body = json.loads(seen[0].content)
    assert body["inherit_starter"] is False
    assert body["files"] == {"prompts/phase2.md": "body"}


@pytest.mark.asyncio
async def test_pack_file_set_refuses_traversal_and_unknown_extensions():
    provider = _provider(_captured([], {}))
    for bad in (
        {"../escape.md": "x"},
        {"prompts/../../etc/passwd.md": "x"},
        {"/abs/path.md": "x"},
        {"payload.sh": "x"},
        {"binary.png": "x"},
    ):
        with pytest.raises(McpPackProviderError, match="Invalid pack file set"):
            await provider.create_personal_pack(
                name="pack name", files=bad, inherit_starter=False
            )


@pytest.mark.asyncio
async def test_pack_file_set_has_no_arbitrary_document_count_cap():
    seen: list[httpx.Request] = []
    provider = _provider(_captured(seen, {
        "id": 7, "name": "pack-name", "version": "1.0.0", "description": None,
        "origin": "personal", "personal": True, "usable": True,
        "certification_status": "personal",
    }, status=201))
    await provider.create_personal_pack(
        name="pack name",
        files={f"prompts/docs/D{i}.md": "x" for i in range(41)},
        inherit_starter=True,
    )
    assert len(json.loads(seen[0].content)["files"]) == 41


@pytest.mark.asyncio
async def test_select_project_pack_sends_null_to_clear():
    seen: list[httpx.Request] = []
    provider = _provider(_captured(seen, {}))
    await provider.select_project_pack(project_id="werkorder", pack_id=None)
    assert seen[0].method == "PATCH"
    assert seen[0].url.path == "/mcp/projects/werkorder/plugin"
    assert json.loads(seen[0].content) == {"plugin_pack_id": None}


@pytest.mark.asyncio
async def test_select_project_pack_validates_project_id():
    provider = _provider(_captured([], {}))
    with pytest.raises(McpPackProviderError, match="Invalid project ID"):
        await provider.select_project_pack(project_id="../etc", pack_id=1)


@pytest.mark.asyncio
async def test_publish_returns_branch_and_pull_request():
    provider = _provider(
        _captured(
            [],
            {
                "branch": "pack/sql-review",
                "repo_url": "https://github.com/example/swap-pack-starter",
                "commit_sha": "abc123",
                "pull_request_url": "https://github.com/example/swap-pack-starter/pull/4",
            },
        )
    )
    publication = await provider.publish_pack(pack_id=7)
    assert publication.branch == "pack/sql-review"
    assert publication.pull_request_url.endswith("/pull/4")


@pytest.mark.asyncio
async def test_publish_tolerates_branch_pushed_without_pull_request():
    provider = _provider(
        _captured(
            [],
            {
                "branch": "pack/sql-review",
                "repo_url": "https://github.com/example/swap-pack-starter",
                "commit_sha": "abc123",
                "pull_request_url": None,
                "note": "Branch pushed; open the PR manually.",
            },
        )
    )
    publication = await provider.publish_pack(pack_id=7)
    assert publication.pull_request_url is None
    assert "Branch pushed" in publication.note


@pytest.mark.asyncio
async def test_upstream_error_detail_is_surfaced_without_leaking_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": "bad manifest; Authorization: Bearer sk-live-abcdefghijklmnop"},
        )

    provider = _provider(handler)
    with pytest.raises(McpPackProviderError) as excinfo:
        await provider.draft_pack(intent="something")
    message = str(excinfo.value)
    assert "HTTP 422" in message
    assert "sk-live-abcdefghijklmnop" not in message
    assert "[REDACTED]" in message


@pytest.mark.asyncio
async def test_unconfigured_provider_fails_closed():
    provider = McpPackProvider(base_url="", timeout=1.0)
    with pytest.raises(McpPackProviderError, match="not configured"):
        await provider.list_packs()


@pytest.mark.asyncio
async def test_list_packs_requires_a_list_response():
    provider = _provider(_captured([], {"not": "a list"}))
    with pytest.raises(McpPackProviderError, match="invalid response"):
        await provider.list_packs()
