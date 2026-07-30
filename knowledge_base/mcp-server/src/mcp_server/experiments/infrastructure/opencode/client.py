"""Small authenticated HTTP client for the pinned OpenCode 1.18.4 server API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_server.experiments.domain.runtime import redact_payload


class OpenCodeClientError(RuntimeError):
    category = "opencode_error"
    retryable = False

    def __init__(self, message: str, *, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = redact_payload(detail or {})


class OpenCodeUnavailable(OpenCodeClientError):
    category = "runtime_unavailable"
    retryable = True


class OpenCodeTimeout(OpenCodeClientError):
    category = "runtime_timeout"
    retryable = True


class OpenCodeMalformedResponse(OpenCodeClientError):
    category = "malformed_runtime_response"


class OpenCodeUnsupportedVersion(OpenCodeClientError):
    category = "unsupported_runtime_version"


class OpenCodeProviderModelUnavailable(OpenCodeClientError):
    category = "provider_model_unavailable"


class OpenCodeAmbiguousSubmission(OpenCodeClientError):
    """A message POST may have reached the server; callers must not retry it."""

    category = "ambiguous_message_submission"


@dataclass(frozen=True, slots=True)
class OpenCodeSession:
    session_id: str
    status: str | None = None
    raw: Mapping[str, Any] | None = None


class OpenCodeHttpClient:
    """HTTP-only OpenCode adapter; credentials are never logged or serialized."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = client

    async def health(self) -> Mapping[str, Any]:
        return await self._request_json("GET", "/global/health", retry_get=True)

    async def providers(self) -> Mapping[str, Any] | list[Any]:
        return await self._request_json("GET", "/provider", retry_get=True)

    async def create_session(
        self,
        *,
        title: str,
        directory: str | None = None,
    ) -> OpenCodeSession:
        path = "/session"
        if directory is not None:
            from urllib.parse import urlencode

            path = f"{path}?{urlencode({'directory': directory})}"
        payload = await self._request_json("POST", path, json={"title": title})
        session_id = _identifier(payload, "id", "sessionID", "session_id")
        if not session_id:
            raise OpenCodeMalformedResponse("session creation returned no session identifier")
        return OpenCodeSession(session_id=session_id, status=_string(payload, "status"), raw=payload)

    async def session(self, session_id: str) -> OpenCodeSession:
        """Return immutable session metadata used by recovery identity checks."""

        payload = await self._request_json(
            "GET",
            f"/session/{session_id}",
            retry_get=True,
        )
        raw = _expect_mapping(payload, "session")
        returned_id = _identifier(raw, "id", "sessionID", "session_id")
        if not returned_id:
            raise OpenCodeMalformedResponse(
                "session lookup returned no session identifier"
            )
        if returned_id != session_id:
            raise OpenCodeMalformedResponse(
                "session lookup returned a different session identifier"
            )
        return OpenCodeSession(
            session_id=returned_id,
            status=_string(raw, "status"),
            raw=raw,
        )

    async def session_status(self, session_id: str) -> Mapping[str, Any] | None:
        """Return active-session status; OpenCode omits idle sessions from this map."""

        payload = await self._request_json("GET", "/session/status", retry_get=True)
        if not isinstance(payload, Mapping):
            raise OpenCodeMalformedResponse("session status response was not an object")
        status = payload.get(session_id)
        if status is None:
            return None
        if isinstance(status, str):
            return {"status": status}
        return _expect_mapping(status, "session status")

    async def messages(self, session_id: str) -> list[Mapping[str, Any]]:
        payload = await self._request_json("GET", f"/session/{session_id}/message", retry_get=True)
        if isinstance(payload, list):
            return [_expect_mapping(item, "message") for item in payload]
        if isinstance(payload, Mapping):
            values = payload.get("messages") or payload.get("data")
            if isinstance(values, list):
                return [_expect_mapping(item, "message") for item in values]
        raise OpenCodeMalformedResponse("messages response was not a list")

    async def children(self, session_id: str) -> list[OpenCodeSession]:
        """Return direct child sessions created through OpenCode's Task tool."""

        payload = await self._request_json(
            "GET",
            f"/session/{session_id}/children",
            retry_get=True,
        )
        if not isinstance(payload, list):
            raise OpenCodeMalformedResponse("session children response was not a list")
        children: list[OpenCodeSession] = []
        for item in payload:
            raw = _expect_mapping(item, "child session")
            child_id = _identifier(raw, "id", "sessionID", "session_id")
            if not child_id:
                raise OpenCodeMalformedResponse(
                    "child session entry returned no session identifier"
                )
            children.append(
                OpenCodeSession(
                    session_id=child_id,
                    status=_string(raw, "status"),
                    raw=raw,
                )
            )
        return children

    async def prompt_async(
        self,
        session_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        try:
            return await self._request_json(
                "POST",
                f"/session/{session_id}/prompt_async",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
                allow_empty=True,
            )
        except (OpenCodeUnavailable, OpenCodeTimeout) as exc:
            raise OpenCodeAmbiguousSubmission(
                "async prompt submission could not be proven absent; do not retry automatically",
                detail={"session_id": session_id, "cause": exc.category},
            ) from exc

    async def abort(self, session_id: str) -> Mapping[str, Any]:
        return await self._request_json("POST", f"/session/{session_id}/abort", json={})

    async def events(self, session_id: str) -> AsyncIterator[Mapping[str, Any]]:
        """Read an SSE response once; caller decides whether to reconcile by polling."""

        try:
            async with self._client.stream(
                "GET",
                self._url("/event"),
                auth=self._auth(),
            ) as response:
                response.raise_for_status()
                event_data: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if event_data:
                            yield _parse_sse_data("\n".join(event_data))
                            event_data = []
                    elif line.startswith("data:"):
                        event_data.append(line.removeprefix("data:").strip())
                if event_data:
                    yield _parse_sse_data("\n".join(event_data))
        except httpx.TimeoutException as exc:
            raise OpenCodeTimeout("OpenCode event stream timed out") from exc
        except httpx.HTTPError as exc:
            raise OpenCodeUnavailable("OpenCode event stream unavailable") from exc

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        retry_get: bool = False,
        allow_empty: bool = False,
    ) -> Mapping[str, Any] | list[Any]:
        attempts = 2 if retry_get else 1
        last_error: OpenCodeClientError | None = None
        for _ in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    self._url(path),
                    json=json,
                    headers=headers,
                    auth=self._auth(),
                )
                if response.status_code >= 500:
                    raise OpenCodeUnavailable(f"OpenCode returned {response.status_code}")
                if response.status_code in {401, 403}:
                    raise OpenCodeClientError("OpenCode authentication was rejected", detail={"status": response.status_code})
                response.raise_for_status()
                if allow_empty and response.status_code == 204:
                    return {}
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise OpenCodeMalformedResponse("OpenCode response was not JSON") from exc
                if not isinstance(payload, (Mapping, list)):
                    raise OpenCodeMalformedResponse("OpenCode JSON response had an unsupported shape")
                return payload
            except OpenCodeClientError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            except httpx.TimeoutException:
                last_error = OpenCodeTimeout("OpenCode request timed out")
            except httpx.HTTPError:
                last_error = OpenCodeUnavailable("OpenCode request unavailable")
        assert last_error is not None
        raise last_error

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self._username, self._password)


def _identifier(payload: Mapping[str, Any] | list[Any], *keys: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _string(payload: Mapping[str, Any] | list[Any], key: str) -> str | None:
    return str(payload[key]) if isinstance(payload, Mapping) and payload.get(key) is not None else None


def _expect_mapping(value: Any, item_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OpenCodeMalformedResponse(f"{item_name} entry was not an object")
    return value


def _parse_sse_data(value: str) -> Mapping[str, Any]:
    try:
        parsed = httpx.Response(200, content=value).json()
    except ValueError as exc:
        raise OpenCodeMalformedResponse("OpenCode SSE event was not JSON") from exc
    return _expect_mapping(parsed, "event")
