"""Thin, safety-bounded adapter for MCP project onboarding operations."""

from __future__ import annotations

import re
import ipaddress
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.config import settings
from src.harness.models import SUPPORTED_PROJECT_PLATFORMS


_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_SCP_GIT_URL_RE = re.compile(
    r"^git@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+(?:\.git)?$"
)
_SAFE_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
_SUPPORTED_PLATFORMS = frozenset(SUPPORTED_PROJECT_PLATFORMS)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer|Basic)\s+\S+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)=([^&\s]+)"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._+/=\-]{10,}"),
)
_MAX_SAFE_TEXT_CHARS = 240


@dataclass(frozen=True)
class McpSourceOption:
    id: str
    label: str
    source_type: str = "git"


@dataclass(frozen=True)
class McpProjectAttachmentResult:
    project_id: str
    status: str
    platform: str
    source_type: str
    project_path: str


@dataclass(frozen=True)
class McpUploadedZipAttachmentResult:
    project_id: str
    platform: str
    source_type: str
    filename: str
    size_bytes: int
    sha256: str
    receipt: str
    project_path: str


@dataclass(frozen=True)
class McpProjectOperationResult:
    project_id: str
    status: str
    message: str
    status_path: str


@dataclass(frozen=True)
class McpProjectStatus:
    project_id: str
    status: str
    last_scan_at: str | None
    job_count: int
    doc_count: int
    error_message: str | None
    status_path: str
    docs_path: str


class McpProjectsProviderError(Exception):
    """Provider failure whose message is safe for workflow and UI surfaces."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class McpProjectsProvider:
    """Call MCP's authoritative project repository without duplicating it."""

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

    async def list_source_options(
        self, *, access_token: str | None = None
    ) -> list[McpSourceOption]:
        data = await self._request("GET", "/platforms", access_token=access_token)
        platforms = data.get("platforms")
        if not isinstance(platforms, list):
            raise McpProjectsProviderError("MCP project provider returned an invalid response.")

        options: list[McpSourceOption] = []
        for item in platforms:
            if not isinstance(item, dict):
                continue
            platform_id = item.get("id")
            label = item.get("label")
            if (
                isinstance(platform_id, str)
                and platform_id in _SUPPORTED_PLATFORMS
                and isinstance(label, str)
                and label.strip()
            ):
                options.append(
                    McpSourceOption(id=platform_id, label=_safe_text(label))
                )
        return options

    async def attach_git_repository(
        self,
        *,
        project_id: str,
        repo_url: str,
        platform: str,
        installation_id: int | None = None,
        display_name: str | None = None,
        access_token: str | None = None,
    ) -> McpProjectAttachmentResult:
        project_id = _validate_project_id(project_id)
        repo_url = _validate_repo_url(repo_url)
        platform = _validate_platform(platform)
        installation_id = _validate_installation_id(installation_id)

        payload: dict[str, Any] = {
            "url": repo_url,
            "project_id": project_id,
            "platform": platform,
        }
        if installation_id is not None:
            payload["installation_id"] = installation_id
        if display_name is not None:
            clean_display_name = display_name.strip()
            if not clean_display_name or len(clean_display_name) > 200:
                raise McpProjectsProviderError("Invalid project display name.")
            payload["display_name"] = clean_display_name

        data = await self._request(
            "POST", "/add-repo", access_token=access_token, payload=payload
        )
        response_project_id = data.get("id")
        if response_project_id != project_id:
            raise McpProjectsProviderError("MCP project provider returned an invalid response.")
        return McpProjectAttachmentResult(
            project_id=project_id,
            status=_safe_field(data.get("status"), default="attached"),
            platform=_safe_field(data.get("platform"), default=platform),
            source_type=_safe_field(data.get("source_type"), default="git"),
            project_path=f"/projects/{project_id}",
        )

    async def verify_uploaded_zip_source(
        self, *, upload_receipt: str, access_token: str | None = None
    ) -> McpUploadedZipAttachmentResult:
        if not isinstance(upload_receipt, str) or not 32 <= len(upload_receipt) <= 4096:
            raise McpProjectsProviderError("Invalid uploaded source receipt.")
        payload = _receipt_payload(upload_receipt)
        project_id = _validate_project_id(payload.get("project_id"))
        platform = _validate_platform(payload.get("platform"))
        data = await self._request(
            "POST",
            "/uploaded-sources/verify",
            access_token=access_token,
            payload={"receipt": upload_receipt},
        )
        filename = data.get("filename")
        sha256 = data.get("sha256")
        size_bytes = data.get("size_bytes")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.lower().endswith(".zip")
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise McpProjectsProviderError("MCP project provider returned an invalid upload receipt.")
        return McpUploadedZipAttachmentResult(
            project_id=project_id,
            platform=platform,
            source_type="zip",
            filename=filename,
            size_bytes=size_bytes,
            sha256=sha256,
            receipt=upload_receipt,
            project_path=f"/projects/{project_id}",
        )

    async def trigger_scan(
        self, *, project_id: str, access_token: str | None = None
    ) -> McpProjectOperationResult:
        project_id = _validate_project_id(project_id)
        data = await self._request(
            "POST",
            "/scan",
            access_token=access_token,
            payload={"project_id": project_id},
        )
        return _operation_result(project_id, data)

    async def get_status(
        self, *, project_id: str, access_token: str | None = None
    ) -> McpProjectStatus:
        project_id = _validate_project_id(project_id)
        data = await self._request(
            "GET", f"/status/{project_id}", access_token=access_token
        )
        if data.get("project_id") != project_id:
            raise McpProjectsProviderError("MCP project provider returned an invalid response.")
        return McpProjectStatus(
            project_id=project_id,
            status=_safe_field(data.get("status"), default="unknown"),
            last_scan_at=(
                _safe_field(data["last_scan_at"])
                if data.get("last_scan_at") is not None
                else None
            ),
            job_count=_safe_nonnegative_int(data.get("job_count")),
            doc_count=_safe_nonnegative_int(data.get("doc_count")),
            error_message=(
                _safe_text(data["error_message"])
                if isinstance(data.get("error_message"), str)
                else None
            ),
            status_path=f"/mcp/status/{project_id}",
            docs_path=f"/projects/{project_id}/docs",
        )

    async def push_to_rag(
        self, *, project_id: str, access_token: str | None = None
    ) -> McpProjectOperationResult:
        project_id = _validate_project_id(project_id)
        data = await self._request(
            "POST",
            "/push-to-rag",
            access_token=access_token,
            payload={"project_id": project_id},
        )
        return _operation_result(project_id, data)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._base_url:
            raise McpProjectsProviderError("MCP project provider is not configured.")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise McpProjectsProviderError(
                f"MCP project provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpProjectsProviderError("MCP project provider could not be reached.") from exc

        if response.status_code >= 400:
            detail = _response_error_text(response)
            suffix = f": {detail}" if detail else "."
            raise McpProjectsProviderError(
                f"MCP project provider returned HTTP {response.status_code}{suffix}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise McpProjectsProviderError(
                "MCP project provider returned an invalid response."
            ) from exc
        if not isinstance(data, dict):
            raise McpProjectsProviderError("MCP project provider returned an invalid response.")
        return data


def _validate_project_id(value: str) -> str:
    if not isinstance(value, str) or not _PROJECT_ID_RE.fullmatch(value):
        raise McpProjectsProviderError("Invalid project ID.")
    return value


def _validate_repo_url(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
        raise McpProjectsProviderError("Invalid Git repository URL.")
    if _SCP_GIT_URL_RE.fullmatch(value):
        host = value.split("@", 1)[1].split(":", 1)[0]
        if _unsafe_host(host):
            raise McpProjectsProviderError("Invalid Git repository URL.")
        return value
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise McpProjectsProviderError("Invalid Git repository URL.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in ";|&")
        or _unsafe_host(parsed.hostname)
    ):
        raise McpProjectsProviderError("Invalid Git repository URL.")
    return value


def _unsafe_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _validate_platform(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_PLATFORM_RE.fullmatch(value)
        or value not in _SUPPORTED_PLATFORMS
    ):
        raise McpProjectsProviderError("Invalid project platform.")
    return value


def _validate_installation_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise McpProjectsProviderError("Invalid GitHub installation ID.")
    return value


def _receipt_payload(receipt: str) -> dict[str, Any]:
    try:
        encoded = receipt.split(".", 1)[0]
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpProjectsProviderError("Invalid uploaded source receipt.") from exc
    if not isinstance(payload, dict):
        raise McpProjectsProviderError("Invalid uploaded source receipt.")
    return payload


def _operation_result(
    project_id: str, data: dict[str, Any]
) -> McpProjectOperationResult:
    return McpProjectOperationResult(
        project_id=project_id,
        status=_safe_field(data.get("status"), default="accepted"),
        message=_safe_field(data.get("message"), default="Operation accepted."),
        status_path=f"/mcp/status/{project_id}",
    )


def _safe_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _safe_field(value: Any, *, default: str = "") -> str:
    return _safe_text(value) if isinstance(value, str) and value else default


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return _safe_text(response.text)
    if not isinstance(data, dict) or not isinstance(data.get("detail"), str):
        return ""
    return _safe_text(data["detail"])


def _safe_text(value: str) -> str:
    sanitized = value.replace("\n", " ").replace("\r", " ")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[URL_REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > _MAX_SAFE_TEXT_CHARS:
        return sanitized[: _MAX_SAFE_TEXT_CHARS - 1].rstrip() + "..."
    return sanitized
