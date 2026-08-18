import httpx

from src.runtime.providers.base import RuntimeSourceProvider
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider


def test_tmc_provider_contract_and_schema():
    provider = TalendTmcRuntimeProvider()

    assert isinstance(provider, RuntimeSourceProvider)
    assert provider.provider_key() == "tmc"
    assert provider.provider_label() == "Talend Management Console"
    assert provider.container_key_field() == "workspace_id"
    assert provider.static_ref_strategy() is None
    assert provider.credential_schema() == [
        {"name": "api_token", "type": "secret", "label": "API token", "required": True},
        {"name": "base_url", "type": "url", "label": "Base URL", "required": True},
        {
            "name": "task_name_filter",
            "type": "text",
            "label": "Task/artifact filters (comma-separated)",
            "required": False,
        },
        {
            "name": "history_days",
            "type": "text",
            "label": "Run history in days (1-90)",
            "required": False,
        },
    ]


async def test_tmc_validate_credentials_success():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    provider = TalendTmcRuntimeProvider(transport=transport)

    ok, error = await provider.validate_credentials(
        {"api_token": "tmc-token", "base_url": "https://tmc.example.com"}
    )

    assert ok is True
    assert error is None


async def test_tmc_validate_credentials_unauthorized_is_sanitized():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="tmc-token"))
    provider = TalendTmcRuntimeProvider(transport=transport)

    ok, error = await provider.validate_credentials(
        {"api_token": "tmc-token", "base_url": "https://tmc.example.com"}
    )

    assert ok is False
    assert error == "401 Unauthorized"
    assert "tmc-token" not in error


async def test_tmc_validate_credentials_non_ascii_token_is_reported_cleanly():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    provider = TalendTmcRuntimeProvider(transport=transport)

    ok, error = await provider.validate_credentials(
        {"api_token": "tmc-toРken", "base_url": "https://tmc.example.com"}
    )

    assert ok is False
    assert error == "Credentials contain a character that could not be sent in an HTTP header"


async def test_tmc_validate_credentials_transport_error_is_sanitized():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Authorization: Bearer tmc-token failed", request=request)

    provider = TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler))

    ok, error = await provider.validate_credentials(
        {"api_token": "tmc-token", "base_url": "https://tmc.example.com?token=tmc-token"}
    )

    assert ok is False
    assert error is not None
    assert "tmc-token" not in error
    assert "Bearer [redacted]" in error
