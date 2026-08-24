"""Azure DevOps REST helpers: URL normalization, connection probe, repo discovery.

Azure DevOps has no equivalent of a GitHub App, so the credential is always a
Personal Access Token. The token is entered once per *organization* (not per
repository), verified against ``_apis/connectionData``, and stored Fernet-encrypted
by :mod:`mcp_server.services.azure_devops_connections`. Everything the connect UI
needs — "is this token good", "which repos can it see" — is answered here so no
caller has to know Azure DevOps URL shapes or API versions.

Supported organization URL shapes (all normalize to an organization base URL):

- ``https://dev.azure.com/<org>``                  (current)
- ``https://<org>.visualstudio.com``               (legacy)
- ``https://<host>/<collection>``                  (Azure DevOps Server / on-prem)

and any deeper URL a user is likely to paste from the browser or the clone dialog:
``.../<project>``, ``.../<project>/_git/<repo>``, ``.../_apis/...``.
"""

from __future__ import annotations

import base64
import logging
import re
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

API_VERSION = "7.1"
_CONNECTION_DATA_API_VERSION = "7.1-preview.1"
_TIMEOUT = 20.0

#: Rejects shell metacharacters and whitespace before a URL ever reaches ``git``.
_UNSAFE_URL_CHARS = re.compile(r"[\s;|&`$<>()\"']")


class AzureDevOpsError(RuntimeError):
    """An Azure DevOps API call failed.

    ``retryable`` separates "your token/URL is wrong" (terminal — the user must
    fix something) from "Azure DevOps did not answer" (transient), so callers can
    map to 400/403 vs 502 without re-parsing messages.
    """

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class AzureDevOpsUrlError(ValueError):
    """A supplied URL is not a usable Azure DevOps organization or repository URL."""


# --- URL handling -------------------------------------------------------------


def _strip_userinfo(parsed):
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return parsed._replace(netloc=netloc, query="", fragment="", params="")


def _clean_segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _reject_unsafe(url: str, *, label: str) -> str:
    """Guard the URL that will reach ``git``.

    Applied to the *normalized* URL rather than the raw input: a browser URL
    legitimately carries ``?path=...&version=...``, and those characters are
    dangerous only if they survive into the clone URL — normalization drops them.
    """
    if _UNSAFE_URL_CHARS.search(url):
        raise AzureDevOpsUrlError(f"{label} contains invalid characters")
    return url


def normalize_organization_url(raw: str) -> tuple[str, str]:
    """Return ``(organization_url, organization_name)`` for any Azure DevOps URL.

    Accepts organization, project, repository and ``_apis`` URLs and reduces them
    all to the organization (or on-prem collection) base. Raises
    :class:`AzureDevOpsUrlError` when the input cannot be an Azure DevOps URL.
    """
    if not raw or not raw.strip():
        raise AzureDevOpsUrlError("Organization URL is required")
    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = _strip_userinfo(urlparse(candidate))
    if parsed.scheme not in {"http", "https"}:
        raise AzureDevOpsUrlError("Organization URL must use http or https")
    host = parsed.hostname or ""
    if not host:
        raise AzureDevOpsUrlError("Organization URL has no host")

    segments = _clean_segments(parsed.path)

    # A repository URL carries the project as the segment before "_git".
    lowered = [segment.lower() for segment in segments]
    if "_git" in lowered:
        segments = segments[: lowered.index("_git")][:-1]
    elif "_apis" in lowered:
        segments = segments[: lowered.index("_apis")]

    if host.lower() == "dev.azure.com" or host.lower().endswith(".azure.com"):
        # https://dev.azure.com/<org>[/...]  -> the org is the first segment.
        if not segments:
            raise AzureDevOpsUrlError(
                "dev.azure.com URLs must include the organization, e.g. https://dev.azure.com/my-org"
            )
        segments = segments[:1]
    elif host.lower().endswith(".visualstudio.com"):
        # https://<org>.visualstudio.com[/DefaultCollection] -> org is in the host.
        segments = segments[:1] if segments and segments[0].lower() == "defaultcollection" else []
    else:
        # Azure DevOps Server: https://<host>/<collection>[/<project>...]
        if not segments:
            raise AzureDevOpsUrlError(
                "Azure DevOps Server URLs must include the collection, e.g. https://tfs.example.com/DefaultCollection"
            )
        segments = segments[:2] if len(segments) >= 2 and segments[0].lower() == "tfs" else segments[:1]

    path = "".join(f"/{segment}" for segment in segments)
    organization_url = _reject_unsafe(
        urlunparse(parsed._replace(path=path)), label="Organization URL"
    )
    if host.lower().endswith(".visualstudio.com"):
        # Legacy hosts name the organization in the host, not the path — a
        # "/DefaultCollection" suffix is not the organization name.
        organization_name = host.split(".", 1)[0]
    else:
        organization_name = segments[-1] if segments else host
    return organization_url, organization_name


def parse_repo_url(raw: str) -> tuple[str, str, str]:
    """Return ``(organization_url, project, repository)`` for a repo/clone URL."""
    if not raw or not raw.strip():
        raise AzureDevOpsUrlError("Repository URL is required")
    candidate = raw.strip()
    parsed = _strip_userinfo(urlparse(candidate if "://" in candidate else f"https://{candidate}"))
    segments = _clean_segments(parsed.path)
    lowered = [segment.lower() for segment in segments]
    if "_git" not in lowered:
        raise AzureDevOpsUrlError(
            "Not an Azure DevOps repository URL (expected '/_git/<repository>')"
        )
    index = lowered.index("_git")
    if index == 0 or index + 1 >= len(segments):
        raise AzureDevOpsUrlError("Azure DevOps repository URL is missing the project or repository")
    organization_url, _ = normalize_organization_url(candidate)
    # Path segments arrive percent-encoded ("My%20Project"); decode so callers get
    # the real names and ``build_repo_clone_url`` does not double-encode them.
    return organization_url, unquote(segments[index - 1]), unquote(segments[index + 1])


def build_repo_clone_url(organization_url: str, project: str, repository: str) -> str:
    """Build the credential-free HTTPS clone URL for a repository.

    Project and repository names may contain spaces, so they are percent-encoded;
    the result is re-checked before it can reach ``git``.
    """
    url = f"{organization_url.rstrip('/')}/{quote(project, safe='')}/_git/{quote(repository, safe='')}"
    return _reject_unsafe(url, label="Repository URL")


def is_azure_devops_url(raw: str | None) -> bool:
    """True when *raw* looks like an Azure DevOps HTTPS URL (host heuristic only)."""
    if not raw:
        return False
    parsed = urlparse(raw.strip() if "://" in raw else f"https://{raw.strip()}")
    host = (parsed.hostname or "").lower()
    if host in {"dev.azure.com", "ssh.dev.azure.com"} or host.endswith(".visualstudio.com"):
        return True
    return "/_git/" in (parsed.path or "").lower()


# --- API calls ----------------------------------------------------------------


def _auth_headers(pat: str) -> dict[str, str]:
    """Azure DevOps PAT auth: HTTP Basic with an empty username."""
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    if response.is_success:
        return
    status = response.status_code
    if status in {401, 203}:
        # Azure DevOps answers a bad/expired PAT with 401, or 203 + a sign-in page
        # when the request looks browser-shaped. Both mean "credential rejected".
        raise AzureDevOpsError(
            "Azure DevOps rejected the personal access token. Check the token and its expiry.",
            status=401,
        )
    if status == 403:
        raise AzureDevOpsError(
            "The personal access token lacks permission for this organization "
            "(needs at least Code: Read).",
            status=403,
        )
    if status == 404:
        raise AzureDevOpsError(
            "Azure DevOps could not find that organization or collection. Check the URL.",
            status=404,
        )
    raise AzureDevOpsError(
        f"Azure DevOps returned HTTP {status} for {context}.",
        status=status,
        retryable=status >= 500 or status == 429,
    )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    pat: str,
    *,
    params: dict[str, str] | None = None,
    context: str,
) -> tuple[dict, httpx.Headers]:
    try:
        response = await client.get(url, headers=_auth_headers(pat), params=params)
    except httpx.HTTPError as exc:
        raise AzureDevOpsError(
            "Could not reach Azure DevOps. Check the organization URL and network access.",
            retryable=True,
        ) from exc
    _raise_for_status(response, context=context)
    try:
        return response.json(), response.headers
    except ValueError as exc:
        # A sign-in redirect served as HTML lands here.
        raise AzureDevOpsError(
            "Azure DevOps returned an unexpected response; the token may be invalid.",
            status=401,
        ) from exc


async def verify_connection(organization_url: str, pat: str) -> str | None:
    """Probe the organization with the PAT; return the authenticated account name.

    Raises :class:`AzureDevOpsError` when the credential or URL is unusable, which
    is what keeps an unverified token from ever being persisted.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        payload, _ = await _get_json(
            client,
            f"{organization_url.rstrip('/')}/_apis/connectionData",
            pat,
            params={"api-version": _CONNECTION_DATA_API_VERSION},
            context="connectionData",
        )
    user = payload.get("authenticatedUser") or {}
    name = user.get("providerDisplayName") or user.get("customDisplayName") or user.get("id")
    return str(name) if name else None


def _repo_item(organization_url: str, raw: dict) -> dict[str, object]:
    project = ((raw.get("project") or {}).get("name")) or ""
    name = str(raw.get("name") or "")
    clone_url = build_repo_clone_url(organization_url, project, name) if project and name else None
    default_branch = raw.get("defaultBranch")
    if isinstance(default_branch, str) and default_branch.startswith("refs/heads/"):
        default_branch = default_branch[len("refs/heads/") :]
    return {
        "id": str(raw.get("id") or ""),
        "name": name,
        "project": project,
        "clone_url": clone_url,
        "web_url": raw.get("webUrl"),
        "default_branch": default_branch,
        "is_disabled": bool(raw.get("isDisabled") or False),
        "size_bytes": int(raw.get("size") or 0),
    }


async def _list_projects(client: httpx.AsyncClient, organization_url: str, pat: str) -> list[str]:
    """List project names, following Azure DevOps continuation tokens."""
    names: list[str] = []
    token: str | None = None
    while True:
        params = {"api-version": API_VERSION, "$top": "200"}
        if token:
            params["continuationToken"] = token
        payload, headers = await _get_json(
            client,
            f"{organization_url.rstrip('/')}/_apis/projects",
            pat,
            params=params,
            context="projects",
        )
        for item in payload.get("value") or []:
            name = item.get("name")
            if name:
                names.append(str(name))
        token = headers.get("x-ms-continuationtoken")
        if not token:
            return names


async def list_repositories(organization_url: str, pat: str) -> list[dict[str, object]]:
    """List every Git repository the PAT can see, across all projects.

    Uses the organization-scoped repositories endpoint and falls back to
    per-project enumeration when a deployment does not serve it (older Azure
    DevOps Server answers 404 there).
    """
    base = organization_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        try:
            payload, _ = await _get_json(
                client,
                f"{base}/_apis/git/repositories",
                pat,
                params={"api-version": API_VERSION},
                context="git/repositories",
            )
            raw_repos = list(payload.get("value") or [])
        except AzureDevOpsError as exc:
            if exc.status != 404:
                raise
            logger.info(
                "Organization-scoped repository listing unavailable for %s; enumerating per project",
                base,
            )
            raw_repos = []
            for project in await _list_projects(client, base, pat):
                payload, _ = await _get_json(
                    client,
                    f"{base}/{quote(project)}/_apis/git/repositories",
                    pat,
                    params={"api-version": API_VERSION},
                    context=f"git/repositories ({project})",
                )
                raw_repos.extend(payload.get("value") or [])

    repos = [_repo_item(base, raw) for raw in raw_repos]
    return sorted(
        (repo for repo in repos if repo["clone_url"]),
        key=lambda repo: (str(repo["project"]).lower(), str(repo["name"]).lower()),
    )
