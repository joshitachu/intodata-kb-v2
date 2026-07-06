import inspect
import re

from src.runtime import registry

FORBIDDEN_PATTERN = re.compile(
    r"runtime_source_credentials|SourceCredentials|credentials_repo|"
    r"Authorization|Bearer|api[_-]?key|token|secret",
    re.IGNORECASE,
)
PROVIDER_CALL_PATTERN = re.compile(r"\.validate_credentials\(|\.sync\(|fetch_runtime_batch\(")


def _assert_no_forbidden_source(source: str) -> None:
    assert not FORBIDDEN_PATTERN.search(source)
    assert not PROVIDER_CALL_PATTERN.search(source)


def test_runtime_chat_tool_registry_has_no_credential_or_provider_path():
    source = inspect.getsource(registry)
    _assert_no_forbidden_source(source)


def test_runtime_chat_tool_input_models_do_not_accept_credentials():
    for spec in registry._TOOL_SPECS.values():
        schema = spec.input_model.model_json_schema()
        source = repr(schema)
        _assert_no_forbidden_source(source)


def test_boundary_checker_catches_synthetic_violation():
    bad_source = "def tool(credentials_repo): return runtime_source_credentials"
    assert FORBIDDEN_PATTERN.search(bad_source)
