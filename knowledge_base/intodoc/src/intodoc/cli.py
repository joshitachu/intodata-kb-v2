"""Click CLI for intodoc."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import click
from rich.console import Console

from intodoc import __version__
from intodoc.config import IntodocConfig, RedactionLevel
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
@click.option(
    "--redaction-level", "-r",
    type=click.Choice([e.value for e in RedactionLevel], case_sensitive=False),
    default=RedactionLevel.STANDARD.value,
    help="Redaction aggressiveness: standard, strict, paranoid",
)
@click.option("--redaction-config", type=click.Path(exists=True, path_type=Path), default=None, help="Path to .intodoc-redact.yaml")
@click.option("--no-redaction-audit", is_flag=True, default=False, help="Disable redaction_report.json output")
@click.option(
    "--allow-redaction-failures", is_flag=True, default=False,
    help="Escape hatch: warn instead of aborting when post-scan redaction verification fails",
)
@click.pass_context
def scan(
    ctx: click.Context,
    repo_path: Path,
    platform: str,
    output_dir: Path,
    redaction_level: str,
    redaction_config: Path | None,
    no_redaction_audit: bool,
    allow_redaction_failures: bool,
) -> None:
    """Scan a repository and produce structured scan results."""
    # Auto-discover .intodoc-redact.yaml in repo root if not explicitly set
    if redaction_config is None:
        candidate = repo_path / ".intodoc-redact.yaml"
        if candidate.exists():
            redaction_config = candidate

    config = IntodocConfig(
        repo_path=repo_path,
        platform=platform,
        output_dir=output_dir,
        verbose=ctx.obj["verbose"],
        redaction_level=RedactionLevel(redaction_level),
        redaction_config=redaction_config,
        redaction_audit=not no_redaction_audit,
        allow_redaction_failures=allow_redaction_failures,
    )
    scan_result, cred_filter = _do_scan(config)

    # Layer 2: post-parse structural filtering
    from intodoc.scanner.post_filter import filter_scan_result

    filter_scan_result(scan_result, cred_filter)

    # Layer 3: verification gate -- no scan output is written unless the
    # filtered result independently verifies clean (or the operator
    # explicitly opted into --allow-redaction-failures).
    from intodoc.scanner.redaction_verifier import verify_scan_result

    verification = verify_scan_result(scan_result, cred_filter)

    # Write the audit report first so a failed gate is inspectable
    if config.redaction_audit:
        _save_redaction_report(cred_filter, config.output_dir, verification)
        console.print(f"  Redaction report: {config.output_dir / 'redaction_report.json'}")

    if not verification["passed"]:
        n_findings = len(verification["findings"])
        n_residual = sum(verification["residual_pattern_counts"].values())
        summary = (
            f"Redaction verification FAILED: {n_findings} unredacted sensitive "
            f"value(s), {n_residual} residual pattern match(es). "
            f"See redaction_report.json 'verification' section."
        )
        if config.allow_redaction_failures:
            console.print(f"[red]WARNING: {summary} Proceeding due to --allow-redaction-failures.[/red]")
        else:
            console.print(f"[red]{summary}[/red]")
            console.print("[red]Scan output NOT written. Fix redaction rules or re-run with --allow-redaction-failures.[/red]")
            raise click.exceptions.Exit(2)

    _save_scan_result(scan_result, config.output_dir)
    console.print(f"[green]Scan saved to {config.output_dir / 'scan_result.json'}[/green]")


# ── Pipeline Steps ───────────────────────────────────────────────────


def _do_scan(config: IntodocConfig):
    """Execute the scan step. Returns (ScanResult, CredentialFilter)."""
    from intodoc.scanner.credential_filter import CredentialFilter

    t0 = time.monotonic()
    console.print(f"[bold]Scanning[/bold] {config.repo_path} as [cyan]{config.platform}[/cyan]...")
    console.print(f"  Redaction level: [yellow]{config.redaction_level.value}[/yellow]")
    plugin = get_platform(config.platform)
    cred_filter = CredentialFilter(
        level=config.redaction_level,
        config_path=config.redaction_config,
    )
    result = plugin.parse_project(config.repo_path, cred_filter)

    elapsed = time.monotonic() - t0
    console.print(f"[green]Scan complete in {elapsed:.1f}s.[/green] {len(result.jobs)} jobs, {len(result.files)} files found.")
    console.print(f"  Metadata connections: {len(result.metadata_connections)}")
    console.print(f"  Context groups: {len(result.context_groups)}")
    return result, cred_filter


# ── Persistence ──────────────────────────────────────────────────────


def _save_redaction_report(cred_filter, output_dir: Path, verification: dict | None = None) -> None:
    """Write the redaction audit report, including the verification gate result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report = cred_filter.get_audit_report()
    if verification is not None:
        report["verification"] = verification
    path = output_dir / "redaction_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    console.print(f"  Total redactions: [yellow]{report['total_redactions']}[/yellow]")


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
        safe_name = re.sub(r'[/\\:*?"<>|]+', "_", job.name).strip("_") or "unnamed"
        (job_group_dir / f"{safe_name}.json").write_text(
            job.model_dump_json(indent=2), encoding="utf-8"
        )
