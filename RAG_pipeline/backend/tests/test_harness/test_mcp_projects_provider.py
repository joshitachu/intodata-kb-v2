from __future__ import annotations

import json

import httpx
import pytest

from src.harness.providers.mcp_projects import (
    McpProjectsProvider,
    McpProjectsProviderError,
)


async def test_list_source_options_projects_safe_platform_metadata() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "platforms": [
                    {"id": "talend", "label": "Talend", "badge_color": "secret-ish"},
                    {"id": "unknown", "label": "Unknown"},
                ]
            },
        )

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp/", transport=httpx.MockTransport(handler)
    )

    options = await provider.list_source_options(access_token="user-jwt")

    assert seen == {"path": "/mcp/platforms", "cookie": "access_token=user-jwt"}
    assert [(option.id, option.label, option.source_type) for option in options] == [
        ("talend", "Talend", "git")
    ]


async def test_attach_git_repository_posts_minimal_validated_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read())
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "id": "project_1",
                "status": "registered",
                "platform": "generic",
                "source_type": "git",
                "local_path": "/private/repos/project_1",
                "repo_url": "https://github.com/acme/project.git",
            },
        )

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
    )
    result = await provider.attach_git_repository(
        project_id="project_1",
        repo_url="https://github.com/acme/project.git",
        platform="generic",
        installation_id=42,
        access_token="user-jwt",
    )

    assert seen["path"] == "/mcp/add-repo"
    assert seen["cookie"] == "access_token=user-jwt"
    assert seen["payload"] == {
        "url": "https://github.com/acme/project.git",
        "project_id": "project_1",
        "platform": "generic",
        "installation_id": 42,
    }
    assert result.project_id == "project_1"
    assert result.project_path == "/projects/project_1"
    assert not hasattr(result, "local_path")
    assert not hasattr(result, "repo_url")


async def test_uploaded_zip_receipt_is_verified_before_it_becomes_a_source() -> None:
    receipt = "eyJwcm9qZWN0X2lkIjoicHJvamVjdF8xIiwicGxhdGZvcm0iOiJnZW5lcmljIn0.signature"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read())
        return httpx.Response(200, json={"receipt": receipt, "filename": "fixture.zip", "size_bytes": 12, "sha256": "a" * 64})

    provider = McpProjectsProvider(base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler))
    result = await provider.verify_uploaded_zip_source(upload_receipt=receipt, access_token="user-jwt")

    assert seen == {"path": "/mcp/uploaded-sources/verify", "payload": {"receipt": receipt}}
    assert result.project_id == "project_1"
    assert result.source_type == "zip"
    assert result.sha256 == "a" * 64


@pytest.mark.parametrize("receipt", ["", "x" * 31, "not-a-receipt" * 10])
async def test_invalid_uploaded_zip_receipt_is_rejected_before_dispatch(receipt: str) -> None:
    provider = McpProjectsProvider(base_url="http://mcp.internal/mcp")
    with pytest.raises(McpProjectsProviderError, match="Invalid uploaded source receipt"):
        await provider.verify_uploaded_zip_source(upload_receipt=receipt)


@pytest.mark.parametrize(
    "project_id",
    ["", "../project", "project/name", " project", "a" * 101],
)
async def test_project_ids_are_rejected_before_dispatch(project_id: str) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(McpProjectsProviderError, match="Invalid project ID"):
        await provider.trigger_scan(project_id=project_id)
    assert called is False


@pytest.mark.parametrize(
    "repo_url",
    [
        "file:///etc/passwd",
        "/srv/repo",
        "https://user:secret@github.com/acme/repo.git",
        "https://github.com/acme/repo.git?token=secret",
        "https://github.com/acme/repo.git#main",
        "https://github.com/acme/repo.git;touch",
        " https://github.com/acme/repo.git",
        "http://127.0.0.1/repo.git",
        "http://[::1]/repo.git",
        "git@localhost:acme/repo.git",
    ],
)
async def test_repo_urls_are_rejected_before_dispatch(repo_url: str) -> None:
    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(McpProjectsProviderError, match="Invalid Git repository URL"):
        await provider.attach_git_repository(
            project_id="project-1", repo_url=repo_url, platform="generic"
        )


@pytest.mark.parametrize("platform", ["", "GENERIC", "generic/../x", "unsupported"])
async def test_platforms_are_strictly_allowlisted(platform: str) -> None:
    provider = McpProjectsProvider(base_url="http://mcp.internal/mcp")
    with pytest.raises(McpProjectsProviderError, match="Invalid project platform"):
        await provider.attach_git_repository(
            project_id="project-1",
            repo_url="git@github.com:acme/project.git",
            platform=platform,
        )


@pytest.mark.parametrize("installation_id", [0, -1, True, 2**63])
async def test_installation_ids_must_be_positive_integers(installation_id: int) -> None:
    provider = McpProjectsProvider(base_url="http://mcp.internal/mcp")
    with pytest.raises(McpProjectsProviderError, match="Invalid GitHub installation ID"):
        await provider.attach_git_repository(
            project_id="project-1",
            repo_url="https://github.com/acme/project.git",
            platform="generic",
            installation_id=installation_id,
        )


async def test_scan_status_and_push_use_authoritative_mcp_routes() -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read()) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "project_id": "project-1",
                    "status": "ready",
                    "last_scan_at": "2026-07-11T10:00:00Z",
                    "job_count": 4,
                    "doc_count": 7,
                    "error_message": None,
                    "warnings": [{"raw": "must not escape"}],
                },
            )
        return httpx.Response(200, json={"status": "accepted", "message": "Started"})

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
    )

    scan = await provider.trigger_scan(project_id="project-1")
    status = await provider.get_status(project_id="project-1")
    push = await provider.push_to_rag(project_id="project-1")

    assert requests == [
        ("POST", "/mcp/scan", {"project_id": "project-1"}),
        ("GET", "/mcp/status/project-1", None),
        ("POST", "/mcp/push-to-rag", {"project_id": "project-1"}),
    ]
    assert scan.status_path == "/mcp/status/project-1"
    assert status.job_count == 4
    assert status.docs_path == "/projects/project-1/docs"
    assert not hasattr(status, "warnings")
    assert push.status == "accepted"


async def test_http_errors_and_status_errors_are_sanitized() -> None:
    secret = "sk-r33-super-secret"

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "detail": (
                    f"Authorization: Bearer {secret} failed at "
                    f"http://mcp.internal/mcp/scan?access_token={secret}"
                ),
                "raw": {"token": secret},
            },
        )

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(error_handler)
    )
    with pytest.raises(McpProjectsProviderError) as exc_info:
        await provider.trigger_scan(project_id="project-1", access_token=secret)

    message = exc_info.value.safe_message
    assert "HTTP 500" in message
    assert secret not in message
    assert "http://mcp.internal" not in message
    assert "raw" not in message

    status_provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "project_id": "project-1",
                    "status": "error",
                    "error_message": f"token={secret} at https://private.example/path",
                },
            )
        ),
    )
    status = await status_provider.get_status(project_id="project-1")
    assert secret not in (status.error_message or "")
    assert "private.example" not in (status.error_message or "")


async def test_transport_error_does_not_echo_exception_or_request_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "failed with token=secret at https://private.example/path", request=request
        )

    provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(McpProjectsProviderError) as exc_info:
        await provider.push_to_rag(project_id="project-1", access_token="secret")
    assert exc_info.value.safe_message == "MCP project provider could not be reached."
