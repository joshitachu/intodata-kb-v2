from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_073_adds_run_event_and_step_authorities(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/073_agent_run_streaming.py"
    spec = spec_from_file_location("agent_run_streaming_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert migration.down_revision == "072_agent_effect_authority"
    assert [call.args[0] for call in op.create_table.call_args_list] == [
        "harness_agent_runs",
        "harness_agent_public_events",
        "harness_agent_steps",
    ]
