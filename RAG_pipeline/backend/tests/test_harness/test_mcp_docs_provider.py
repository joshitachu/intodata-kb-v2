from __future__ import annotations

import httpx
import pytest

from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
)


async def test_trigger_generation_posts_minimal_payload_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "status": "generation_triggered",
                "message": "Doc generation started for fastapi-e2e",
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp/",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.trigger_generation(project_id="fastapi-e2e", access_token="user-jwt")

    assert seen["path"] == "/mcp/generate"
    assert seen["payload"] == b'{"project_id":"fastapi-e2e"}'
    assert seen["cookie"] == "access_token=user-jwt"
    assert result.status == "generation_triggered"
    assert result.message == "Doc generation started for fastapi-e2e"
    assert result.status_path == "/mcp/generate-runs/fastapi-e2e"


async def test_trigger_generation_passes_only_explicit_options() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"status": "generation_triggered", "message": "started"})

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    await provider.trigger_generation(
        project_id="p1",
        investigation_model="anthropic/claude-sonnet-4-5",
        write_model="openai/gpt-5",
        plugin_pack_id=7,
        resume=False,
    )

    assert captured["json"] == (
        b'{"project_id":"p1","investigation_model":"anthropic/claude-sonnet-4-5",'
        b'"write_model":"openai/gpt-5","plugin_pack_id":7,"resume":false}'
    )


async def test_http_failure_raises_sanitized_message() -> None:
    secret = "sk-r33-super-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "detail": (
                    f"Authorization: Bearer {secret} failed at "
                    f"http://mcp.internal/mcp/generate?access_token={secret}"
                )
            },
        )

    provider = McpDocumentationProvider(
        base_url=f"http://mcp.internal/mcp?api_key={secret}",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1", access_token=secret)

    message = exc_info.value.safe_message
    assert "HTTP 500" in message
    assert secret not in message
    assert "Authorization" not in message
    assert "http://mcp.internal" not in message
    assert "[REDACTED]" in message or "[URL_REDACTED]" in message


async def test_timeout_raises_sanitized_timeout_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "timeout while calling http://mcp.internal/mcp/generate?access_token=secret-token",
            request=request,
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        timeout=0.25,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1", access_token="secret-token")

    assert exc_info.value.safe_message == "Documentation generation provider timed out after 0.25s."


async def test_invalid_response_is_sanitized() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="not-json")
        ),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1")

    assert (
        exc_info.value.safe_message
        == "Documentation generation provider returned an invalid response."
    )


async def test_missing_base_url_fails_before_network_call() -> None:
    provider = McpDocumentationProvider(base_url=" ")

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1")

    assert exc_info.value.safe_message == "Documentation generation provider is not configured."


async def test_list_project_docs_returns_safe_metadata_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "project_id": "p1",
                "docs": [
                    {
                        "name": "runbook.md",
                        "type": "md",
                        "size_bytes": 120,
                        "has_pdf": True,
                        "modified_at": "2026-07-10T10:00:00+00:00",
                    }
                ],
                "total": 1,
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    docs = await provider.list_project_docs(project_id="p1", access_token="jwt-token")

    assert seen["path"] == "/mcp/projects/p1/docs"
    assert seen["query"] == "source=published"
    assert seen["cookie"] == "access_token=jwt-token"
    assert docs[0].name == "runbook.md"
    assert docs[0].has_pdf is True


async def test_list_projects_returns_visible_project_metadata_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "projects": [
                    {
                        "id": "project-a",
                        "display_name": "Project A",
                        "platform": "generic",
                        "source_type": "github",
                        "status": "ready",
                        "doc_count": 9,
                        "job_count": 3,
                    }
                ],
                "total": 1,
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    projects = await provider.list_projects(access_token="jwt-token")

    assert seen["path"] == "/mcp/projects"
    assert seen["cookie"] == "access_token=jwt-token"
    assert projects[0].id == "project-a"
    assert projects[0].display_name == "Project A"
    assert projects[0].doc_count == 9


async def test_update_doc_from_evidence_posts_payload_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "project_id": "p1",
                "filename": "04_RUNBOOK.md",
                "status": "updated",
                "message": "Updated 04_RUNBOOK.md from provided evidence.",
                "workflow_run_id": "workflow-1",
                "evidence_refs": ["chat_prompt"],
                "before_sha": "sha256:before",
                "after_sha": "sha256:after",
                "receipt_path": "_harness_doc_updates/receipt.json",
                "docs_path": "/projects/p1/docs/04_RUNBOOK.md",
                "rag_result": {"pushed": 1, "skipped": 0, "errors": []},
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.update_doc_from_evidence(
        project_id="p1",
        filename="04_RUNBOOK.md",
        evidence_text="Nightly sync moved to 02:00 UTC.",
        evidence_refs=["chat_prompt"],
        workflow_run_id="workflow-1",
        access_token="jwt-token",
    )

    assert seen["path"] == "/mcp/projects/p1/docs/04_RUNBOOK.md/evidence-update"
    assert (
        seen["payload"]
        == b'{"evidence_text":"Nightly sync moved to 02:00 UTC.",'
        b'"evidence_refs":["chat_prompt"],"workflow_run_id":"workflow-1"}'
    )
    assert seen["cookie"] == "access_token=jwt-token"
    assert result.status == "updated"
    assert result.filename == "04_RUNBOOK.md"
    assert result.rag_result["pushed"] == 1
