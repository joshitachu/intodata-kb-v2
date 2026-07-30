"""Offline proof for importing one bounded historical model-input receipt."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mcp_server.experiments.application.import_golden import (
    GoldenImportCommand,
    GoldenImportError,
    GoldenParityImporter,
)
from mcp_server.experiments.domain.parity import (
    GoldenCheckpointArtifact,
    GoldenLineageInput,
)


REAL_ROOT = (
    Path(__file__).resolve().parents[3]
    / "output/alitalend/docs/_debug/model_inputs/phase3/016_09_EXECUTIVE_SUMMARY"
)
REAL_HASHES = {
    "prompt": "3718fb5cbd6d7ecb3a77699c3f6efa536a95145bf9c03c8064830bc58fe12b09",
    "context": "ab7775311a6cec989a07313c16603909fca979dd187a616d4ed0bd677a5a12cf",
    "request": "3b02b0c75f5f222baeed4926a96832977656ec7a3769e8d9666ab84f5f95f1ec",
    "response": "c7c517fb9f906ca1552217942e6ddc996b91f4cc3d6612851facbbd9029195a9",
}


def _command(
    root: Path,
    *,
    hashes: dict[str, str] | None = None,
    request_path: Path = Path("request.json"),
    response_path: Path = Path("response.json"),
    max_artifact_bytes: int = 4 * 1024 * 1024,
) -> GoldenImportCommand:
    return GoldenImportCommand(
        task_id="09_EXECUTIVE_SUMMARY",
        allowed_root=root,
        prompt_relative_path=Path("prompt.md"),
        context_relative_path=Path("context_bundle.md"),
        request_relative_path=request_path,
        response_relative_path=response_path,
        output_contract={"artifact_name": "09_EXECUTIVE_SUMMARY.md", "media_type": "text/markdown"},
        expected_sha256=hashes or REAL_HASHES,
        lineage=GoldenLineageInput(
            baseline_run_id=44,
            run_model_route="deepseek/deepseek-v4-pro @ https://api.deepseek.com/v1",
            resume_requested=True,
            attributable_call_count=None,
            inherited_checkpoint_artifacts=(
                GoldenCheckpointArtifact(
                    logical_id="phase2/005_AssetCheck_Interface/request.json",
                    raw_model_route="deepseek/deepseek-v4-flash @ https://api.deepseek.com/v1",
                ),
            ),
        ),
        max_artifact_bytes=max_artifact_bytes,
    )


def test_imports_real_run_44_receipt_with_exact_hashes_and_lineage() -> None:
    receipt = GoldenParityImporter().import_receipt(command=_command(REAL_ROOT))

    assert receipt.request.artifact_hashes == REAL_HASHES
    assert receipt.request.task_model_route.canonical_route == "deepseek/deepseek-v4-pro"
    assert receipt.request.task_model_route.raw_provider_route == (
        "deepseek/deepseek-v4-pro @ https://api.deepseek.com/v1"
    )
    assert receipt.request.lineage.baseline_run_id == 44
    assert receipt.request.lineage.resume_requested is True
    assert receipt.request.lineage.attributable_call_count is None
    inherited = receipt.request.lineage.inherited_checkpoint_artifacts
    assert len(inherited) == 1 and inherited[0].attributable_to_baseline_run is False
    assert receipt.baseline_usage.input_tokens == 79898
    assert receipt.baseline_usage.output_tokens == 6091
    assert receipt.baseline_usage.latency_ms == 112760
    assert receipt.baseline_usage.retries == 0
    assert receipt.baseline_usage.billed_cost_usd is None
    assert receipt.request.model_options == {
        "max_tokens": 32000,
        "provider_options": {},
        "temperature": 0.1,
    }
    assert receipt.fake_runtime.usage.input_tokens == 79898
    assert receipt.fake_runtime.provider_latency_ms == 112760
    assert receipt.aggregate_receipt_sha256 == "4979b151f174ef80c8600ca36ea82f572cc68f58e528e29d6527b77d2f4519dc"
    # The receipt contains only logical/hash data and never an absolute host path.
    assert str(REAL_ROOT) not in repr(receipt)


def test_task_level_model_wins_over_run_metadata() -> None:
    command = _command(REAL_ROOT)
    command = GoldenImportCommand(
        task_id=command.task_id,
        allowed_root=command.allowed_root,
        prompt_relative_path=command.prompt_relative_path,
        context_relative_path=command.context_relative_path,
        request_relative_path=command.request_relative_path,
        response_relative_path=command.response_relative_path,
        output_contract=command.output_contract,
        expected_sha256=command.expected_sha256,
        lineage=GoldenLineageInput(
            baseline_run_id=44,
            run_model_route="deepseek/deepseek-v4-flash @ https://api.deepseek.com/v1",
        ),
    )
    receipt = GoldenParityImporter().import_receipt(command=command)

    assert receipt.request.model_id == "deepseek-v4-pro"
    assert receipt.request.lineage.run_model_route is not None
    assert receipt.request.lineage.run_model_route.model_id == "deepseek-v4-flash"
    assert receipt.request.lineage.task_model_precedence == "task_request"


def _write_bundle(root: Path, *, request: str | None = None, response: str | None = None) -> dict[str, str]:
    values = {
        "prompt": "write only from supplied evidence",
        "context": "facts only",
        "request": request
        or json.dumps(
            {
                "provider": "example/example-model @ https://example.invalid/v1",
                "model": "example-model",
                "provider_options": {"temperature": 0.1},
            }
        ),
        "response": response
        or json.dumps(
            {
                "input_tokens": None,
                "output_tokens": None,
                "input_cache_read_tokens": None,
                "input_cache_miss_tokens": None,
                "latency_ms": None,
                "retries": None,
                "billed_cost_usd": None,
            }
        ),
    }
    (root / "prompt.md").write_text(values["prompt"], encoding="utf-8")
    (root / "context_bundle.md").write_text(values["context"], encoding="utf-8")
    (root / "request.json").write_text(values["request"], encoding="utf-8")
    (root / "response.json").write_text(values["response"], encoding="utf-8")
    return {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in values.items()}


def test_unknown_historical_usage_stays_unknown_and_contract_is_immutable(tmp_path: Path) -> None:
    hashes = _write_bundle(tmp_path)
    command = _command(tmp_path, hashes=hashes)
    receipt = GoldenParityImporter().import_receipt(command=command)

    assert receipt.baseline_usage.input_tokens is None
    assert receipt.baseline_usage.latency_ms is None
    assert receipt.fake_runtime.retry_count is None
    with pytest.raises(TypeError):
        receipt.request.output_contract["artifact_name"] = "mutated"  # type: ignore[index]


def test_provider_route_receipt_removes_url_credentials_query_and_fragment(
    tmp_path: Path,
) -> None:
    hashes = _write_bundle(
        tmp_path,
        request=json.dumps(
            {
                "provider": (
                    "example/example-model @ "
                    "https://user:password@example.invalid:8443/v1?key=secret#fragment"
                ),
                "model": "example-model",
                "max_tokens": 100,
                "temperature": 0,
                "provider_options": {},
            }
        ),
    )

    receipt = GoldenParityImporter().import_receipt(
        command=_command(tmp_path, hashes=hashes)
    )

    assert receipt.request.raw_provider_route == (
        "example/example-model @ https://example.invalid:8443/v1"
    )
    assert "password" not in repr(receipt)
    assert "secret" not in repr(receipt)


def test_rejects_disagreement_between_provider_route_and_task_model(
    tmp_path: Path,
) -> None:
    hashes = _write_bundle(
        tmp_path,
        request=json.dumps(
            {
                "provider": "example/different-model",
                "model": "example-model",
            }
        ),
    )

    with pytest.raises(GoldenImportError, match="route and task model disagree"):
        GoldenParityImporter().import_receipt(
            command=_command(tmp_path, hashes=hashes)
        )


def test_rejects_nonfinite_programmatic_contract_values(tmp_path: Path) -> None:
    hashes = _write_bundle(tmp_path)

    with pytest.raises(ValueError, match="finite"):
        GoldenImportCommand(
            task_id="task",
            allowed_root=tmp_path,
            prompt_relative_path=Path("prompt.md"),
            context_relative_path=Path("context_bundle.md"),
            request_relative_path=Path("request.json"),
            response_relative_path=Path("response.json"),
            output_contract={"score": float("nan")},
            expected_sha256=hashes,
            lineage=GoldenLineageInput(baseline_run_id=44),
        )


@pytest.mark.parametrize(
    ("request_text", "response_text", "message"),
    [
        ('{"provider":"x","provider":"y","model":"m"}', None, "request JSON is invalid"),
        ("[]", None, "request JSON must be a top-level object"),
        (None, '{"input_tokens":NaN}', "response JSON is invalid"),
        (None, "[]", "response JSON must be a top-level object"),
    ],
)
def test_rejects_duplicate_nonstandard_or_nonobject_json(
    tmp_path: Path,
    request_text: str | None,
    response_text: str | None,
    message: str,
) -> None:
    hashes = _write_bundle(tmp_path, request=request_text, response=response_text)
    with pytest.raises(GoldenImportError, match=message):
        GoldenParityImporter().import_receipt(command=_command(tmp_path, hashes=hashes))


def test_rejects_hash_drift_byte_limit_and_absolute_paths(tmp_path: Path) -> None:
    hashes = _write_bundle(tmp_path)
    (tmp_path / "prompt.md").write_text("drift", encoding="utf-8")
    with pytest.raises(GoldenImportError, match="hash drift"):
        GoldenParityImporter().import_receipt(command=_command(tmp_path, hashes=hashes))

    hashes = _write_bundle(tmp_path)
    with pytest.raises(GoldenImportError, match="byte limit"):
        GoldenParityImporter().import_receipt(
            command=_command(tmp_path, hashes=hashes, max_artifact_bytes=1)
        )
    with pytest.raises(ValueError, match="safe relative"):
        _command(tmp_path, hashes=hashes, request_path=Path("/etc/passwd"))


def test_rejects_symlink_parent_without_leaking_host_path(tmp_path: Path) -> None:
    hashes = _write_bundle(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "request.json").write_text((tmp_path / "request.json").read_text(), encoding="utf-8")
    os.unlink(tmp_path / "request.json")
    (tmp_path / "link").symlink_to(nested, target_is_directory=True)
    hashes["request"] = hashlib.sha256((nested / "request.json").read_bytes()).hexdigest()
    with pytest.raises(GoldenImportError, match="may not contain symlinks") as error:
        GoldenParityImporter().import_receipt(
            command=_command(tmp_path, hashes=hashes, request_path=Path("link/request.json"))
        )
    assert str(tmp_path) not in str(error.value)
