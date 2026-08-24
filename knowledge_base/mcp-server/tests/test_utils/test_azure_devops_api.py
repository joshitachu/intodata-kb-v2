"""Tests for Azure DevOps URL handling and REST helpers.

URL normalization is the load-bearing part: the whole point of the connect flow is
that a user pastes whatever URL their browser is showing and it works, so every
shape that browser can produce has to reduce to the same organization base.
"""

from __future__ import annotations

import pytest

from mcp_server.utils import azure_devops_api as ado


@pytest.mark.parametrize(
    ("raw", "expected_url", "expected_name"),
    [
        ("https://dev.azure.com/my-org", "https://dev.azure.com/my-org", "my-org"),
        ("https://dev.azure.com/my-org/", "https://dev.azure.com/my-org", "my-org"),
        ("dev.azure.com/my-org", "https://dev.azure.com/my-org", "my-org"),
        # Project URL
        ("https://dev.azure.com/my-org/Data%20Platform", "https://dev.azure.com/my-org", "my-org"),
        # Clone URL, with the userinfo Azure DevOps puts in it
        (
            "https://my-org@dev.azure.com/my-org/Data/_git/etl",
            "https://dev.azure.com/my-org",
            "my-org",
        ),
        # Browser URL with a file selection in the query string
        (
            "https://dev.azure.com/my-org/Data/_git/etl?path=/jobs/x.item&version=GBmain",
            "https://dev.azure.com/my-org",
            "my-org",
        ),
        # API URL
        (
            "https://dev.azure.com/my-org/_apis/git/repositories",
            "https://dev.azure.com/my-org",
            "my-org",
        ),
        # Legacy host: the organization is in the host, not the path
        ("https://my-org.visualstudio.com", "https://my-org.visualstudio.com", "my-org"),
        (
            "https://my-org.visualstudio.com/DefaultCollection/Data/_git/etl",
            "https://my-org.visualstudio.com/DefaultCollection",
            "my-org",
        ),
        # Azure DevOps Server (on-prem) collection
        (
            "https://tfs.example.com/tfs/DefaultCollection/Data/_git/etl",
            "https://tfs.example.com/tfs/DefaultCollection",
            "DefaultCollection",
        ),
        (
            "https://azdo.example.com/MyCollection",
            "https://azdo.example.com/MyCollection",
            "MyCollection",
        ),
    ],
)
def test_normalize_organization_url(raw, expected_url, expected_name):
    assert ado.normalize_organization_url(raw) == (expected_url, expected_name)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "https://dev.azure.com",  # no organization
        "ftp://dev.azure.com/my-org",  # not http(s)
        "https://tfs.example.com",  # on-prem without a collection
    ],
)
def test_normalize_organization_url_rejects_unusable_input(raw):
    with pytest.raises(ado.AzureDevOpsUrlError):
        ado.normalize_organization_url(raw)


def test_parse_repo_url_decodes_names():
    org, project, repo = ado.parse_repo_url(
        "https://my-org@dev.azure.com/my-org/Data%20Platform/_git/etl%20jobs"
    )
    assert org == "https://dev.azure.com/my-org"
    assert project == "Data Platform"
    assert repo == "etl jobs"


def test_parse_repo_url_round_trips_through_clone_url():
    """Decode-then-encode must not double-escape, or the clone URL 404s."""
    original = "https://dev.azure.com/my-org/Data%20Platform/_git/etl%20jobs"
    org, project, repo = ado.parse_repo_url(original)
    assert ado.build_repo_clone_url(org, project, repo) == original


@pytest.mark.parametrize(
    "raw",
    [
        "https://dev.azure.com/my-org/Data",  # no /_git/
        "https://dev.azure.com/_git/repo",  # no project
        "https://dev.azure.com/my-org/Data/_git",  # no repository
    ],
)
def test_parse_repo_url_rejects_non_repo_urls(raw):
    with pytest.raises(ado.AzureDevOpsUrlError):
        ado.parse_repo_url(raw)


def test_shell_metacharacters_never_reach_the_clone_url():
    """The guard runs on the built URL, which is what would reach ``git``."""
    with pytest.raises(ado.AzureDevOpsUrlError):
        ado.build_repo_clone_url("https://dev.azure.com/org; rm -rf /", "p", "r")


def test_is_azure_devops_url():
    assert ado.is_azure_devops_url("https://dev.azure.com/org/proj/_git/repo") is True
    assert ado.is_azure_devops_url("https://org.visualstudio.com/proj/_git/repo") is True
    assert ado.is_azure_devops_url("https://tfs.example.com/coll/proj/_git/repo") is True
    assert ado.is_azure_devops_url("https://github.com/acme/repo.git") is False
    assert ado.is_azure_devops_url(None) is False


def test_auth_headers_use_basic_with_empty_username():
    import base64

    headers = ado._auth_headers("pat-value")
    scheme, encoded = headers["Authorization"].split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == ":pat-value"


# --- API behaviour ------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None, text_body: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._text_body = text_body

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _install_fake_client(monkeypatch, responses):
    """Serve *responses* (list of (url_substring, _FakeResponse)) to httpx GETs."""
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append((url, params or {}))
            for fragment, response in responses:
                if fragment in url:
                    return response
            raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(ado.httpx, "AsyncClient", FakeClient)
    return calls


async def test_verify_connection_returns_account_name(monkeypatch):
    _install_fake_client(
        monkeypatch,
        [(
            "_apis/connectionData",
            _FakeResponse(200, {"authenticatedUser": {"providerDisplayName": "Ada L"}}),
        )],
    )
    assert await ado.verify_connection("https://dev.azure.com/org", "pat") == "Ada L"


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_retryable"),
    [(401, 401, False), (403, 403, False), (404, 404, False), (500, 500, True), (429, 429, True)],
)
async def test_verify_connection_error_classification(
    monkeypatch, status, expected_status, expected_retryable
):
    _install_fake_client(monkeypatch, [("_apis/connectionData", _FakeResponse(status))])
    with pytest.raises(ado.AzureDevOpsError) as exc:
        await ado.verify_connection("https://dev.azure.com/org", "pat")
    assert exc.value.status == expected_status
    assert exc.value.retryable is expected_retryable


async def test_verify_connection_treats_html_signin_page_as_bad_credential(monkeypatch):
    """A sign-in page served as 200/HTML must not read as a working token."""
    _install_fake_client(monkeypatch, [("_apis/connectionData", _FakeResponse(200, payload=None))])
    with pytest.raises(ado.AzureDevOpsError) as exc:
        await ado.verify_connection("https://dev.azure.com/org", "pat")
    assert exc.value.status == 401


async def test_list_repositories_normalizes_items(monkeypatch):
    payload = {
        "value": [
            {
                "id": "b2",
                "name": "beta",
                "project": {"name": "Data"},
                "defaultBranch": "refs/heads/main",
                "webUrl": "https://dev.azure.com/org/Data/_git/beta",
                "size": 120,
            },
            {
                "id": "a1",
                "name": "alpha",
                "project": {"name": "Data"},
                "isDisabled": True,
            },
            # No project -> no clone URL -> dropped rather than offered as broken.
            {"id": "c3", "name": "orphan"},
        ]
    }
    _install_fake_client(monkeypatch, [("_apis/git/repositories", _FakeResponse(200, payload))])

    repos = await ado.list_repositories("https://dev.azure.com/org", "pat")

    assert [r["name"] for r in repos] == ["alpha", "beta"]  # sorted by project, name
    beta = repos[1]
    assert beta["clone_url"] == "https://dev.azure.com/org/Data/_git/beta"
    assert beta["default_branch"] == "main"
    assert beta["size_bytes"] == 120
    assert repos[0]["is_disabled"] is True


async def test_list_repositories_falls_back_to_per_project_enumeration(monkeypatch):
    """Older Azure DevOps Server has no organization-scoped repository endpoint."""
    org_level = _FakeResponse(404)
    projects = _FakeResponse(200, {"value": [{"name": "Data"}]})
    project_repos = _FakeResponse(
        200, {"value": [{"id": "a1", "name": "alpha", "project": {"name": "Data"}}]}
    )

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(url)
            # Order matters: the per-project URL also ends in /_apis/git/repositories.
            if "/Data/_apis/git/repositories" in url:
                return project_repos
            if url.endswith("/Coll/_apis/git/repositories"):
                return org_level
            if url.endswith("/_apis/projects"):
                return projects
            raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(ado.httpx, "AsyncClient", FakeClient)

    repos = await ado.list_repositories("https://azdo.example.com/Coll", "pat")
    assert [r["name"] for r in repos] == ["alpha"]
    assert any(url.endswith("/_apis/projects") for url in calls)


async def test_list_projects_follows_continuation_token(monkeypatch):
    first = _FakeResponse(
        200, {"value": [{"name": "One"}]}, headers={"x-ms-continuationtoken": "tok"}
    )
    second = _FakeResponse(200, {"value": [{"name": "Two"}]})
    served: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            served.append(params or {})
            return first if len(served) == 1 else second

    monkeypatch.setattr(ado.httpx, "AsyncClient", FakeClient)

    async with ado.httpx.AsyncClient() as client:
        names = await ado._list_projects(client, "https://dev.azure.com/org", "pat")

    assert names == ["One", "Two"]
    assert served[1]["continuationToken"] == "tok"


async def test_network_failure_is_retryable(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            raise ado.httpx.ConnectError("no route to host")

    monkeypatch.setattr(ado.httpx, "AsyncClient", FakeClient)

    with pytest.raises(ado.AzureDevOpsError) as exc:
        await ado.verify_connection("https://dev.azure.com/org", "pat")
    assert exc.value.retryable is True
