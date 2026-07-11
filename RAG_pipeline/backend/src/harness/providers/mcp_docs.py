"""MCP documentation-generation provider.

This adapter is deliberately thin: it triggers the existing MCP `/generate`
workflow and returns a safe summary. MCP remains responsible for project access,
generation orchestration, staging, publishing, and run records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import settings


_SECRET_PATTERNS = [
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._+/=\-]+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)=([^&\s]+)"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._+/=\-]{10,}"),
]
_MAX_SAFE_ERROR_CHARS = 240


@dataclass(frozen=True)
class McpDocumentationGenerationResult:
    project_id: str
    status: str
    message: str
    status_path: str
    docs_path: str


@dataclass(frozen=True)
class McpProjectDocInfo:
    name: str
    type: str
    size_bytes: int
    has_pdf: bool
    modified_at: str


@dataclass(frozen=True)
class McpProjectInfo:
    id: str
    display_name: str
    platform: str
    source_type: str
    status: str
    doc_count: int
    job_count: int


@dataclass(frozen=True)
class McpEvidenceDocUpdateResult:
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None
    evidence_refs: list[str]
    before_sha: str
    after_sha: str
    receipt_path: str
    docs_path: str
    rag_result: dict[str, Any]


class McpDocumentationProviderError(Exception):
    """Provider failure whose message is safe for chat/UI surfaces."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class McpDocumentationProvider:
    """HTTP client for triggering MCP documentation generation."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_base_url = base_url if base_url is not None else settings.mcp_docs_base_url
        self._base_url = configured_base_url.strip().rstrip("/")
        self._timeout = timeout if timeout is not None else settings.mcp_docs_request_timeout
        self._transport = transport

    async def trigger_generation(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
        investigation_model: str | None = None,
        model: str | None = None,
        write_model: str | None = None,
        plugin_pack_id: int | None = None,
        resume: bool | None = None,
        mode: str | None = None,
    ) -> McpDocumentationGenerationResult:
        """Trigger MCP doc generation for a project.

        Optional model/plugin values are passed through only when a caller
        already has them. The access token is forwarded as MCP's `access_token`
        cookie so MCP performs the final project-access check.
        """
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation generation provider is not configured."
            )
        if not project_id.strip():
            raise McpDocumentationProviderError(
                "Project scope is required before generating documentation."
            )

        payload: dict[str, Any] = {"project_id": project_id}
        _set_if_present(payload, "investigation_model", investigation_model)
        _set_if_present(payload, "model", model)
        _set_if_present(payload, "write_model", write_model)
        _set_if_present(payload, "plugin_pack_id", plugin_pack_id)
        _set_if_present(payload, "resume", resume)
        _set_if_present(payload, "mode", mode)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    "/generate",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation generation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation generation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation generation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        status = str(data.get("status") or "generation_triggered")
        message = _safe_text(
            str(data.get("message") or f"Documentation generation started for {project_id}")
        )
        return McpDocumentationGenerationResult(
            project_id=project_id,
            status=status,
            message=message,
            status_path=f"/mcp/generate-runs/{project_id}",
            docs_path=f"/projects/{project_id}/docs",
        )

    async def list_project_docs(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
        source: str = "published",
    ) -> list[McpProjectDocInfo]:
        """List generated docs metadata from MCP without exposing file contents."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )
        if source not in {"published", "staging"}:
            raise McpDocumentationProviderError("Invalid documentation source.")

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(
                    f"/projects/{project_id}/docs",
                    params={"source": source},
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        docs = data.get("docs")
        if not isinstance(docs, list):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        result: list[McpProjectDocInfo] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            result.append(
                McpProjectDocInfo(
                    name=str(item.get("name") or ""),
                    type=str(item.get("type") or ""),
                    size_bytes=int(item.get("size_bytes") or 0),
                    has_pdf=bool(item.get("has_pdf")),
                    modified_at=str(item.get("modified_at") or ""),
                )
            )
        return [doc for doc in result if doc.name]

    async def list_projects(
        self,
        *,
        access_token: str | None = None,
    ) -> list[McpProjectInfo]:
        """List MCP projects visible to the current user."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get("/projects")
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        result: list[McpProjectInfo] = []
        for item in projects:
            if not isinstance(item, dict):
                continue
            project_id = str(item.get("id") or "").strip()
            if not project_id:
                continue
            result.append(
                McpProjectInfo(
                    id=project_id,
                    display_name=str(item.get("display_name") or project_id),
                    platform=str(item.get("platform") or ""),
                    source_type=str(item.get("source_type") or ""),
                    status=str(item.get("status") or ""),
                    doc_count=int(item.get("doc_count") or 0),
                    job_count=int(item.get("job_count") or 0),
                )
            )
        return result

    async def update_doc_from_evidence(
        self,
        *,
        project_id: str,
        filename: str,
        evidence_text: str,
        evidence_refs: list[str] | None = None,
        workflow_run_id: str | None = None,
        access_token: str | None = None,
    ) -> McpEvidenceDocUpdateResult:
        """Ask MCP to update one generated markdown doc from explicit evidence."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )
        if not project_id.strip():
            raise McpDocumentationProviderError(
                "Project scope is required before updating documentation."
            )
        if not filename.strip():
            raise McpDocumentationProviderError(
                "A target documentation file is required before updating documentation."
            )
        payload = {
            "evidence_text": evidence_text,
            "evidence_refs": evidence_refs or [],
            "workflow_run_id": workflow_run_id,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    f"/projects/{project_id}/docs/{filename}/evidence-update",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        return McpEvidenceDocUpdateResult(
            project_id=str(data.get("project_id") or project_id),
            filename=str(data.get("filename") or filename),
            status=str(data.get("status") or "updated"),
            message=_safe_text(str(data.get("message") or "Documentation updated from provided evidence.")),
            workflow_run_id=(
                str(data["workflow_run_id"])
                if data.get("workflow_run_id") is not None
                else None
            ),
            evidence_refs=[
                str(item)
                for item in data.get("evidence_refs", [])
                if item is not None
            ],
            before_sha=str(data.get("before_sha") or ""),
            after_sha=str(data.get("after_sha") or ""),
            receipt_path=str(data.get("receipt_path") or ""),
            docs_path=str(data.get("docs_path") or f"/projects/{project_id}/docs/{filename}"),
            rag_result=data.get("rag_result") if isinstance(data.get("rag_result"), dict) else {},
        )


def _set_if_present(payload: dict[str, Any], key: str, value: Any | None) -> None:
    if value is not None:
        payload[key] = value


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise McpDocumentationProviderError(
            "Documentation generation provider returned an invalid response."
        ) from exc
    if not isinstance(data, dict):
        raise McpDocumentationProviderError(
            "Documentation generation provider returned an invalid response."
        )
    return data


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return response.text


def _safe_error(prefix: str, detail: str) -> str:
    safe_detail = _safe_text(detail)
    if not safe_detail:
        return f"{prefix}."
    return f"{prefix}: {safe_detail}"


def _safe_text(value: str) -> str:
    sanitized = value.replace("\n", " ").replace("\r", " ")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(_redact_match, sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[URL_REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > _MAX_SAFE_ERROR_CHARS:
        sanitized = sanitized[: _MAX_SAFE_ERROR_CHARS - 1].rstrip() + "..."
    return sanitized


def _redact_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 1:
        return f"{match.group(1)}=[REDACTED]"
    return "[REDACTED]"
