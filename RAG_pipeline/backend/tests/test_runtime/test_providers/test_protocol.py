from src.runtime.providers.base import RuntimeSourceProvider, StaticRefStrategy
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
    # Both declare `artifact_name`; `stored` declares nothing because it reads
    # the mirror rather than filling it. See tests/test_runtime/
    # test_static_ref_binding.py for what the declaration is used for.
    assert mock_provider.static_ref_strategy() == StaticRefStrategy(source_field="artifact_name")
    assert stored_provider.static_ref_strategy() is None
    assert tmc_provider.provider_key() == "tmc"
    assert tmc_provider.container_key_field() == "workspace_id"
    assert tmc_provider.static_ref_strategy() == StaticRefStrategy(source_field="artifact_name")
