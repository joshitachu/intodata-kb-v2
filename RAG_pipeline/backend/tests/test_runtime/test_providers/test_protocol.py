from src.runtime.providers.base import RuntimeSourceProvider
from src.runtime.providers.mock import provider as mock_provider
from src.runtime.providers.stored import provider as stored_provider
from src.runtime.providers.talend_tmc import provider as tmc_provider


def test_runtime_source_providers_satisfy_protocol():
    assert isinstance(mock_provider, RuntimeSourceProvider)
    assert isinstance(stored_provider, RuntimeSourceProvider)
    assert isinstance(tmc_provider, RuntimeSourceProvider)


def test_protocol_identity_and_mapper_slots():
    assert mock_provider.provider_key() == "mock"
    assert mock_provider.container_key_field() == "workspace_id"
    assert mock_provider.static_ref_strategy() is None
    assert tmc_provider.provider_key() == "tmc"
    assert tmc_provider.container_key_field() == "workspace_id"
    assert tmc_provider.static_ref_strategy() is None
