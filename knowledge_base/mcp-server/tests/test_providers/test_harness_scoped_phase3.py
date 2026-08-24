"""Doc-targeted Phase 3 (Slice 3): unaffected docs preserved byte-stable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.prompts import doc_prompt


def _ctx(tmp_path: Path, scoped: set[str] | None) -> DocGenContext:
    staging = tmp_path / "output" / "proj" / "docs_staging"
    staging.mkdir(parents=True, exist_ok=True)
    return DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=0,
        total_groups=0,
        effective_model="m",
        write_model="m",
        platform="generic",
        analysis_guide_file="",
        scoped_docs=scoped,
    )


async def test_scoped_phase3_preserves_unaffected_docs_byte_stable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    baseline = tmp_path / "output" / "proj" / "docs"
    baseline.mkdir(parents=True)
    a_bytes = "# A doc\n\nOriginal A content with unit job_a.\n".encode("utf-8")
    b_bytes = "# B doc\n\nOriginal B content with unit job_b.\n".encode("utf-8")
    c_bytes = "# C doc\n\nOriginal C content.\n".encode("utf-8")
    (baseline / "01_A.md").write_bytes(a_bytes)
    (baseline / "02_B.md").write_bytes(b_bytes)
    (baseline / "03_C.md").write_bytes(c_bytes)

    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["01_A", "02_B", "03_C"]
    ctx = _ctx(tmp_path, scoped={"02_B"})

    logs: list[tuple[str, str]] = []

    async def log_cb(project_id: str, msg: str, level: str) -> None:
        logs.append((msg, level))

    await provider._apply_scoped_docs(ctx, log_cb)

    # Only the scoped doc remains in the regeneration set.
    assert provider._required_docs == ["02_B"]
    # Unaffected docs copied into staging byte-for-byte.
    assert (ctx.staging_dir / "01_A.md").read_bytes() == a_bytes
    assert (ctx.staging_dir / "03_C.md").read_bytes() == c_bytes
    # The scoped doc is NOT pre-copied -- Phase 3 will (re)write it.
    assert not (ctx.staging_dir / "02_B.md").exists()


async def test_scoped_phase3_regenerates_doc_missing_from_baseline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    baseline = tmp_path / "output" / "proj" / "docs"
    baseline.mkdir(parents=True)
    (baseline / "01_A.md").write_text("# A", encoding="utf-8")
    # 02_B has no baseline copy.

    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = ["01_A", "02_B"]
    ctx = _ctx(tmp_path, scoped={"01_A"})

    async def log_cb(project_id: str, msg: str, level: str) -> None:
        return None

    await provider._apply_scoped_docs(ctx, log_cb)

    # 02_B cannot be preserved (no baseline) so it is regenerated alongside the
    # scoped 01_A -- never silently dropped.
    assert set(provider._required_docs) == {"01_A", "02_B"}
    assert not (ctx.staging_dir / "01_A.md").exists()
