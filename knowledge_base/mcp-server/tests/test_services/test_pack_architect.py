from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.models import ModelResult
from mcp_server.services import pack_architect


@pytest.fixture
def architect_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    starter = data / "swap_pack_starter"
    starter.mkdir(parents=True)
    (starter / "plugin.yaml").write_text(
        "name: starter-pack\nversion: 0.1.0\ndescription: A starter Pack for interactive editing.\n"
        "docs:\n  - name: STARTER_REPORT\n    phase2_sections: []\n",
        encoding="utf-8",
    )
    (starter / "README.md").write_text("# Starter\n", encoding="utf-8")
    monkeypatch.setattr(config, "data_dir", data)
    monkeypatch.setattr(config, "pack_author_timeout_seconds", 5)
    monkeypatch.setattr(config, "pack_author_max_output_tokens", 8_000)
    return data


async def _finished(session_id: str, owner: int = 7):
    for _ in range(100):
        value = await pack_architect.get_session(session_id, owner)
        if value.turn_status != "running":
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("Pack Architect test turn did not finish")


async def test_session_is_owner_private_and_survives_reload(architect_data: Path) -> None:
    created = await pack_architect.create_session(7, platform="talend")
    loaded = await pack_architect.get_session(created.id, 7)

    assert loaded.id == created.id
    assert loaded.platform == "talend"
    assert loaded.turn_status == "idle"
    assert loaded.manifest_yaml.startswith("name: starter-pack")
    with pytest.raises(pack_architect.PackArchitectNotFound):
        await pack_architect.get_session(created.id, 8)


async def test_latest_session_recovers_owner_active_draft(architect_data: Path) -> None:
    first = await pack_architect.create_session(7)
    second = await pack_architect.create_session(7)
    await pack_architect.create_session(8)

    latest = await pack_architect.get_latest_session(7)

    assert latest.id == second.id
    assert latest.id != first.id
    with pytest.raises(pack_architect.PackArchitectNotFound):
        await pack_architect.get_latest_session(99)


async def test_turn_is_accepted_immediately_and_uses_one_batch_model_call(
    architect_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeProvider:
        async def complete(self, prompt, context_files=None, options=None):
            calls.append((prompt, options))
            return ModelResult(
                success=True,
                text=json.dumps(
                    {
                        "assistant_message": "Created the audit Pack design.",
                        "needs_clarification": False,
                        "design": {
                            "name": "audit-pack",
                            "description": "Focused source-backed security audit.",
                            "analysis_policy": "bounded",
                            "investigation": False,
                            "scorecard": False,
                            "focus_include": ["source_summary"],
                            "focus_exclude": ["binary"],
                            "sections_mode": "replace",
                            "sections": [
                                {
                                    "id": "AUDIT_FINDINGS",
                                    "question": "Which source-backed security findings require action?",
                                }
                            ],
                            "requirements": "Cite exact source evidence and state uncertainty explicitly.",
                            "documents": [
                                {
                                    "name": "AUDIT",
                                    "purpose": "Give security reviewers an actionable audit report.",
                                    "phase2_sections": ["AUDIT_FINDINGS"],
                                    "required_sections": ["Audit Findings", "Evidence Register"],
                                    "required_evidence": ["source-backed facts"],
                                    "required_outputs": ["remediation actions"],
                                    "per_unit": False,
                                    "instructions": "Prioritize findings by concrete impact.",
                                }
                            ],
                            "audience": {
                                "roles": ["Security reviewer"],
                                "tone": "neutral",
                                "technical_depth": "deep",
                                "output_organization": "sectioned",
                            },
                            "phase2_guidance": "Trace every finding to source evidence.",
                            "phase3_guidance": "Keep remediation concrete.",
                            "platform_guidance": "",
                        },
                    }
                ),
                model_name="kimi-k2.5",
            )

    monkeypatch.setattr(pack_architect, "build_model_provider", lambda _model: FakeProvider())
    created = await pack_architect.create_session(7)
    accepted = await pack_architect.run_turn(
        created.id, 7, "Create an audit Pack", expected_revision=created.revision
    )

    assert accepted.turn_status == "running"
    assert accepted.turn_stage == "queued"
    assert accepted.revision == 2
    assert accepted.messages[-1] == {"role": "user", "content": "Create an audit Pack"}

    finished = await _finished(created.id)
    assert finished.turn_status == "idle"
    assert finished.turn_stage == "complete"
    assert finished.manifest_yaml.startswith("name: audit-pack")
    assert finished.files["prompts/docs/AUDIT.md"].startswith("# AUDIT")
    assert finished.complete is True
    assert finished.messages[-1]["role"] == "assistant"
    assert len(calls) == 1
    assert calls[0][1].json_mode is True
    assert calls[0][1].retries == 0
    assert '"latest_user_request": "Create an audit Pack"' in calls[0][0]


async def test_clarifying_turn_changes_no_files(
    architect_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProvider:
        async def complete(self, *_args, **_kwargs):
            return ModelResult(
                success=True,
                text=json.dumps(
                    {
                        "assistant_message": "Which audience should use this Pack?",
                        "needs_clarification": True,
                        "design": None,
                    }
                ),
                model_name="kimi-k2.5",
            )

    monkeypatch.setattr(pack_architect, "build_model_provider", lambda _model: FakeProvider())
    created = await pack_architect.create_session(7)
    await pack_architect.run_turn(created.id, 7, "Make a useful Pack", expected_revision=1)
    finished = await _finished(created.id)

    assert finished.manifest_yaml == created.manifest_yaml
    assert finished.messages[-1]["content"].startswith("Which audience")
    assert finished.turn_detail == "The Pack Architect needs your answer before changing the draft."


async def test_failed_model_turn_preserves_workspace_and_exposes_safe_error(
    architect_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProvider:
        async def complete(self, *_args, **_kwargs):
            return ModelResult(success=False, raw_error="Authorization: Bearer secret")

    monkeypatch.setattr(pack_architect, "build_model_provider", lambda _model: FailedProvider())
    created = await pack_architect.create_session(7)
    before = created.manifest_yaml
    await pack_architect.run_turn(created.id, 7, "Change it", expected_revision=1)
    finished = await _finished(created.id)

    assert finished.turn_status == "failed"
    assert "did not complete" in (finished.turn_error or "")
    assert "secret" not in (finished.turn_error or "")
    assert finished.manifest_yaml == before


async def test_cancel_stops_running_turn_without_losing_draft(
    architect_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class SlowProvider:
        async def complete(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(pack_architect, "build_model_provider", lambda _model: SlowProvider())
    created = await pack_architect.create_session(7)
    accepted = await pack_architect.run_turn(created.id, 7, "Change it", expected_revision=1)
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = await pack_architect.cancel_turn(
        created.id, 7, expected_revision=accepted.revision
    )

    assert cancelled.turn_status == "failed"
    assert cancelled.turn_error == "The Pack Architect turn was cancelled."
    assert cancelled.manifest_yaml == created.manifest_yaml


async def test_loading_legacy_session_removes_only_opencode_runtime_state(
    architect_data: Path,
) -> None:
    created = await pack_architect.create_session(7)
    root = architect_data / "pack_architect_sessions" / created.id
    metadata_path = root / "session.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["opencode_session_id"] = "ses_old"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    (root / "opencode").mkdir()
    (root / "opencode" / "runtime.db").write_text("runtime", encoding="utf-8")

    loaded = await pack_architect.get_session(created.id, 7)

    assert loaded.manifest_yaml == created.manifest_yaml
    assert not (root / "opencode").exists()
    assert "opencode_session_id" not in json.loads(metadata_path.read_text())


async def test_seeded_artifact_refuses_path_traversal(architect_data: Path) -> None:
    with pytest.raises(pack_architect.PackArchitectError, match="unsafe file path"):
        await pack_architect.create_session(7, files={"../escape.md": "no"})
