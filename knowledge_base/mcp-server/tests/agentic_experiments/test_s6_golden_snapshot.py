from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.import_golden import (
    GoldenImportCommand,
    GoldenParityImporter,
)
from mcp_server.experiments.application.snapshot_golden import (
    GoldenParityArtifactSink,
)
from mcp_server.experiments.domain.parity import GoldenLineageInput
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    ImmutableArtifactError,
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


def _bundle():
    return GoldenParityImporter().import_bundle(
        command=GoldenImportCommand(
            task_id="09_EXECUTIVE_SUMMARY",
            allowed_root=REAL_ROOT,
            prompt_relative_path=Path("prompt.md"),
            context_relative_path=Path("context_bundle.md"),
            request_relative_path=Path("request.json"),
            response_relative_path=Path("response.json"),
            output_contract={
                "artifact_name": "09_EXECUTIVE_SUMMARY.md",
                "media_type": "text/markdown",
            },
            expected_sha256=REAL_HASHES,
            lineage=GoldenLineageInput(
                baseline_run_id=44,
                run_model_route="deepseek/deepseek-v4-pro",
                resume_requested=True,
            ),
        )
    )


def test_snapshot_preserves_exact_golden_bytes_and_writes_manifest_last(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(
        output_root=tmp_path,
        project_id="alitalend",
        experiment_id="00000000-0000-4000-8000-000000000006",
    )
    bundle = _bundle()

    persisted = GoldenParityArtifactSink(store).save(bundle=bundle)

    for name, relative_path in persisted.artifact_relative_paths.items():
        frozen = store.experiment_root / relative_path
        assert frozen.read_bytes() == bundle.artifacts[name]
        assert frozen != REAL_ROOT
    manifest = json.loads(
        (
            store.experiment_root / persisted.manifest_relative_path
        ).read_text(encoding="utf-8")
    )
    assert manifest["aggregate_receipt_sha256"] == (
        bundle.receipt.aggregate_receipt_sha256
    )
    assert manifest["model_options"] == {
        "max_tokens": 32000,
        "provider_options": {},
        "temperature": 0.1,
    }
    assert manifest["task_model_route"]["canonical_route"] == (
        "deepseek/deepseek-v4-pro"
    )
    with pytest.raises(TypeError):
        persisted.artifact_relative_paths["prompt"] = "changed"  # type: ignore[index]

    with pytest.raises(ImmutableArtifactError):
        GoldenParityArtifactSink(store).save(bundle=bundle)
