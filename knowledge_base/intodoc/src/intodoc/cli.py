"""Click CLI for intodoc."""

from __future__ import annotations

import json
import time
from pathlib import Path

import click
from rich.console import Console

from intodoc import __version__
from intodoc.config import IntodocConfig
from intodoc.platforms.registry import get_platform, list_platforms

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="intodoc")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """intodoc - Integration Documentation Generator.

    Scan integration codebases and produce structured JSON for AI analysis.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, path_type=Path))
@click.option("--platform", "-p", required=True, type=click.Choice(list_platforms()), help="Integration platform")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default="./intodoc-output", help="Output directory")
@click.pass_context
def scan(ctx: click.Context, repo_path: Path, platform: str, output_dir: Path) -> None:
    """Scan a repository and produce structured scan results."""
    config = IntodocConfig(
        repo_path=repo_path,
        platform=platform,
        output_dir=output_dir,
        verbose=ctx.obj["verbose"],
    )
    scan_result = _do_scan(config)
    _save_scan_result(scan_result, config.output_dir)
    console.print(f"[green]Scan saved to {config.output_dir / 'scan_result.json'}[/green]")


# ── Pipeline Steps ───────────────────────────────────────────────────


def _do_scan(config: IntodocConfig):
    """Execute the scan step."""
    from intodoc.scanner.credential_filter import CredentialFilter

    t0 = time.monotonic()
    console.print(f"[bold]Scanning[/bold] {config.repo_path} as [cyan]{config.platform}[/cyan]...")
    plugin = get_platform(config.platform)
    cred_filter = CredentialFilter()
    result = plugin.parse_project(config.repo_path, cred_filter)

    elapsed = time.monotonic() - t0
    console.print(f"[green]Scan complete in {elapsed:.1f}s.[/green] {len(result.jobs)} jobs, {len(result.files)} files found.")
    console.print(f"  Metadata connections: {len(result.metadata_connections)}")
    console.print(f"  Context groups: {len(result.context_groups)}")
    return result


# ── Persistence ──────────────────────────────────────────────────────


def _save_scan_result(scan_result, output_dir: Path) -> None:
    """Save scan result as monolithic JSON + segmented files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Monolithic file (backward compat)
    path = output_dir / "scan_result.json"
    path.write_text(scan_result.model_dump_json(indent=2), encoding="utf-8")

    # ── Segmented output ─────────────────────────────────────────
    scan_dir = output_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)

    # meta.json
    (scan_dir / "meta.json").write_text(json.dumps({
        "platform": scan_result.platform,
        "repo_root": scan_result.repo_root,
        "scanned_at": str(scan_result.scanned_at),
        "stats": scan_result.stats,
    }, indent=2, default=str), encoding="utf-8")

    # manifest.json
    (scan_dir / "manifest.json").write_text(
        scan_result.manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    # connections.json
    (scan_dir / "connections.json").write_text(json.dumps(
        [c.model_dump() for c in scan_result.metadata_connections],
        indent=2, default=str,
    ), encoding="utf-8")

    # contexts.json
    (scan_dir / "contexts.json").write_text(json.dumps(
        [c.model_dump() for c in scan_result.context_groups],
        indent=2, default=str,
    ), encoding="utf-8")

    # routines.json (non-system only)
    (scan_dir / "routines.json").write_text(json.dumps(
        [r.model_dump() for r in scan_result.routines if not r.is_system],
        indent=2, default=str,
    ), encoding="utf-8")

    # files_index.json
    (scan_dir / "files_index.json").write_text(json.dumps(
        [f.model_dump() for f in scan_result.files],
        indent=2, default=str,
    ), encoding="utf-8")

    # jobs/{group}/{job_name}.json
    jobs_dir = scan_dir / "jobs"
    for job in scan_result.jobs:
        group = job.group or "Global"
        job_group_dir = jobs_dir / group
        job_group_dir.mkdir(parents=True, exist_ok=True)
        (job_group_dir / f"{job.name}.json").write_text(
            job.model_dump_json(indent=2), encoding="utf-8"
        )
