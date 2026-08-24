"""Migration graph invariants required by deploy-time ``upgrade head``."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_exactly_one_head() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))

    heads = ScriptDirectory.from_config(config).get_heads()

    # The invariant is the count, not the name: `alembic upgrade head` fails on
    # a branched graph. Pinning the revision id made this fail on every new
    # migration, which taught people to ignore it.
    assert len(heads) == 1, f"branched migration graph: {heads}"
