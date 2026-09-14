"""Click CLI for intodoc."""

from __future__ import annotations

import json
import hashlib
import re
import time
from pathlib import Path

import click
from rich.console import Console

from intodoc import __version__
from intodoc.config import IntodocConfig, RedactionLevel
from intodoc.platforms.registry import get_platform, list_platforms

console = Console()


def _bounded_segment(value: str, fallback: str) -> str:
    """Filesystem-safe segment with a digest to prevent sanitization collisions."""
    normalized = re.sub(r'[/\\:*?"<>|]+', "_", value).strip("_") or fallback
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:64].rstrip('_') or fallback}-{digest}"


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
    # A receipt is an authorization for Runtime publication, not just another
    # scan artifact.  Remove any older authorization before work begins so a
    # failed or explicitly-bypassed run can never reuse it.
    _publication_approval_path(output_dir).unlink(missing_ok=True)

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

    # Deterministic derived artifacts, built BEFORE the verification gate.
    # Both copy expressions and identifiers out of the parsed models, so
    # they must be inside the same defense-in-depth sweep as
    # scan_result.json -- computing them after the gate would publish
    # unverified derivatives of verified data.
    from intodoc.capability_ledger import build_capability_ledger
    from intodoc.mapping_accounting import build_mapping_accounting

    scan_result.mapping_accounting = build_mapping_accounting(scan_result)
    scan_result.capability_ledger = build_capability_ledger(scan_result)

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

    _save_scan_result(scan_result, config.output_dir, cred_filter)
    if scan_result.platform == "talend":
        console.print(
            "[green]Talend designs: "
            f"{scan_result.stats.get('total_artifacts', len(scan_result.jobs))} "
            f"({scan_result.stats.get('job_count', len(scan_result.jobs))} Jobs · "
            f"{scan_result.stats.get('route_count', 0)} Routes)[/green]"
        )
    console.print(f"[green]Scan saved to {config.output_dir / 'scan_result.json'}[/green]")

    # Deterministic lineage pass (S4): tMap expression tokenizer + Var
    # chaining + row->component resolution. Runs after the main scan gate
    # so lineage.json is built from already Layer-1/2-filtered data, and
    # is independently verified before being written (Layer 3, D6) --
    # mirrors the scan_result.json gate above exactly.
    from intodoc.lineage import build_lineage_matrix, save_lineage_matrix
    from intodoc.scanner.redaction_verifier import verify_lineage_matrix

    lineage_matrix = build_lineage_matrix(scan_result, cred_filter)
    lineage_verification = verify_lineage_matrix(lineage_matrix, cred_filter)

    if not lineage_verification["passed"]:
        n_residual = sum(lineage_verification["residual_pattern_counts"].values())
        summary = (
            f"Lineage redaction verification FAILED: {n_residual} residual "
            f"pattern match(es) in scan/lineage.json."
        )
        if config.allow_redaction_failures:
            console.print(f"[red]WARNING: {summary} Proceeding due to --allow-redaction-failures.[/red]")
        else:
            console.print(f"[red]{summary}[/red]")
            console.print("[red]scan/lineage.json NOT written. Fix redaction rules or re-run with --allow-redaction-failures.[/red]")
            raise click.exceptions.Exit(2)

    lineage_path = save_lineage_matrix(lineage_matrix, config.output_dir)
    console.print(
        f"[green]Lineage saved to {lineage_path}[/green] "
        f"({lineage_matrix.summary['total_entries']} entries)"
    )

    # Deterministic landscape rollup pass (sibling of the lineage pass
    # above): rolls the manifest + lineage matrix up into an L1/L2
    # systems-and-connections graph. Same gate pattern -- built after the
    # main scan + lineage gates so it only ever sees already-filtered
    # data, and independently verified before being written.
    from intodoc.landscape import build_landscape, save_landscape
    from intodoc.scanner.redaction_verifier import verify_landscape

    landscape = build_landscape(scan_result, lineage_matrix, cred_filter)
    landscape_verification = verify_landscape(landscape, cred_filter)

    if not landscape_verification["passed"]:
        n_residual = sum(landscape_verification["residual_pattern_counts"].values())
        summary = (
            f"Landscape redaction verification FAILED: {n_residual} residual "
            f"pattern match(es) in scan/landscape.json."
        )
        if config.allow_redaction_failures:
            console.print(f"[red]WARNING: {summary} Proceeding due to --allow-redaction-failures.[/red]")
        else:
            console.print(f"[red]{summary}[/red]")
            console.print("[red]scan/landscape.json NOT written. Fix redaction rules or re-run with --allow-redaction-failures.[/red]")
            raise click.exceptions.Exit(2)

    landscape_path = save_landscape(landscape, config.output_dir)
    console.print(
        f"[green]Landscape saved to {landscape_path}[/green] "
        f"({landscape.summary['system_count']} systems, "
        f"{landscape.summary['application_count']} applications, "
        f"{landscape.summary['edge_count']} edges)"
    )

    # This is deliberately stricter than the CLI escape hatch.  Operators may
    # choose to retain local artifacts after a redaction failure for diagnosis,
    # but those artifacts must never become eligible for Runtime publication.
    if not config.allow_redaction_failures:
        approval_path = _save_publication_approval(
            config.output_dir,
            scan_verification=verification,
            lineage_verification=lineage_verification,
            landscape_verification=landscape_verification,
        )
        console.print(f"  Publication approval: {approval_path}")
    else:
        console.print(
            "[yellow]Publication approval withheld: "
            "--allow-redaction-failures was used.[/yellow]"
        )


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
    result = plugin.parse_project(
        config.repo_path,
        cred_filter,
        cache_dir=config.cache_dir / "deterministic-parse" / config.platform,
    )

    elapsed = time.monotonic() - t0
    if result.platform == "talend":
        design_summary = (
            f"{result.stats.get('total_artifacts', len(result.jobs))} Talend designs "
            f"({result.stats.get('job_count', len(result.jobs))} Jobs · "
            f"{result.stats.get('route_count', 0)} Routes)"
        )
    else:
        design_summary = f"{len(result.jobs)} jobs"
    console.print(f"[green]Scan complete in {elapsed:.1f}s.[/green] {design_summary}, {len(result.files)} files found.")
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


_PUBLICATION_APPROVAL_FILENAME = "publication_approval.json"
_PUBLICATION_ARTIFACTS = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/lineage.json",
    "scan/landscape.json",
)

# ── Agent-eligible artifact tree ─────────────────────────────────────
#
# The four aggregates above are not the bytes a documentation workspace
# stages.  It stages every `scan/jobs/<group>/<design>.json` shard, the estate
# collections, and the inventory inside `scan/meta.json`; none of those was
# hashed, so editing one shard after approval was undetectable and the
# redaction verification that makes an approval mean anything had run against
# different bytes.
#
# The receipt therefore also states the COMPLETE eligible set.  It carries the
# membership RULE (roots plus exclusions) as well as the resulting membership,
# so a consumer derives the set from the receipt instead of keeping its own
# copy of the rule -- two copies drift, and a drifted tree digest either
# misses evidence or rejects a harmless file.
_APPROVAL_TREE_ALGORITHM = "sha256-relpath-pairs-1"

#: Everything under these roots, relative to the scan output directory.
_AGENT_ARTIFACT_ROOTS: tuple[str, ...] = ("scan",)

#: ...except these.  `scan/sanitized/` is the redacted mirror of the SOURCE
#: TREE and `scan/evidence/` holds routine source fragments; neither may ever
#: reach a documentation agent, both are large, and hashing them on every scan
#: and again on every workspace build would cost more than the binding buys.
_AGENT_ARTIFACT_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "scan/evidence/",
    "scan/sanitized/",
)


def _publication_approval_path(output_dir: Path) -> Path:
    return output_dir / _PUBLICATION_APPROVAL_FILENAME


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _agent_artifact_files(
    output_dir: Path, roots: tuple[str, ...], excluded: tuple[str, ...]
) -> dict[str, str]:
    """`{relative posix path: sha256}` for every agent-eligible artifact.

    Symlinks are skipped.  The scanner writes this tree itself and creates
    none; following one would let `rglob` recurse forever, and a symlink added
    afterwards still cannot be staged -- every consumer copy either routes
    through the approved manifest with a containment check or names a fixed
    file.  The consumer applies the same skip, so the two sides agree.
    """

    files: dict[str, str] = {}
    for root in roots:
        base = output_dir / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(output_dir).as_posix()
            if any(relative.startswith(prefix) for prefix in excluded):
                continue
            files[relative] = _sha256_file(path)
    return dict(sorted(files.items()))


def _approval_tree_digest(files: dict[str, str]) -> str:
    """Digest over `relpath + sha256` pairs in sorted order.

    Byte-stable by construction: sorted keys, and an explicit separator
    between the path and its digest so `a/b` + `c` cannot collide with `a` +
    `b/c`.
    """

    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _save_publication_approval(
    output_dir: Path,
    *,
    scan_verification: dict,
    lineage_verification: dict,
    landscape_verification: dict,
) -> Path:
    """Atomically authorize Runtime publication of this exact completed scan.

    The receipt is intentionally written only after every published artifact
    exists and its final verification passed.  It binds that authorization to
    the exact bytes Runtime's publisher is allowed to consume -- both the four
    named aggregates and, through `tree`, every other artifact a downstream
    workspace may stage.  This function must stay the LAST thing `scan` does:
    the tree is a snapshot, and anything written under `scan/` afterwards
    would read to a consumer as post-approval tampering.
    """

    verification_by_artifact = {
        "scan_result.json": scan_verification,
        "scan/manifest.json": scan_verification,
        "scan/lineage.json": lineage_verification,
        "scan/landscape.json": landscape_verification,
    }
    if any(result.get("passed") is not True for result in verification_by_artifact.values()):
        raise RuntimeError("publication approval requires every final verification to pass")

    artifact_hashes: dict[str, str] = {}
    for relative_path in _PUBLICATION_ARTIFACTS:
        path = output_dir / relative_path
        if not path.is_file():
            raise RuntimeError(f"publication approval artifact is missing: {relative_path}")
        artifact_hashes[relative_path] = _sha256_file(path)

    tree_files = _agent_artifact_files(
        output_dir, _AGENT_ARTIFACT_ROOTS, _AGENT_ARTIFACT_EXCLUDED_PREFIXES
    )
    if not tree_files:
        raise RuntimeError(
            "publication approval found no agent-eligible artifacts under "
            f"{'/, '.join(_AGENT_ARTIFACT_ROOTS)}/; the scan output is incomplete"
        )

    # schema_version 2 adds `tree`.  A consumer that stages per-design shards
    # must REFUSE a version-1 receipt rather than fall back to the four
    # aggregate hashes: "older scan" and "someone removed the binding" are the
    # same file on disk, and only one of them is safe.
    receipt = {
        "schema_version": 2,
        "approved": True,
        "allow_redaction_failures": False,
        "artifacts": artifact_hashes,
        "tree": {
            "algorithm": _APPROVAL_TREE_ALGORITHM,
            "roots": list(_AGENT_ARTIFACT_ROOTS),
            "excluded_prefixes": list(_AGENT_ARTIFACT_EXCLUDED_PREFIXES),
            "file_count": len(tree_files),
            "digest": _approval_tree_digest(tree_files),
            "files": tree_files,
        },
        "verifications": {
            path: {"passed": result["passed"]}
            for path, result in verification_by_artifact.items()
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    target = _publication_approval_path(output_dir)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _save_scan_result(scan_result, output_dir: Path, cred_filter) -> None:
    """Save scan result as monolithic JSON + segmented files.

    `cred_filter` is required, not optional: the sanitized workspace is
    written from the ORIGINAL sources and is only safe when it is redacted
    by the same filter instance (same level, same custom rules) that
    produced the scan. A default filter here would silently under-redact a
    strict/paranoid scan.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Monolithic file (backward compat).  It is written after segmented jobs
    # so its manifest includes the completed job_files index.
    path = output_dir / "scan_result.json"

    # ── Segmented output ─────────────────────────────────────────
    scan_dir = output_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    from intodoc.scanner.evidence_store import (
        write_evidence_package,
        write_sanitized_workspace,
    )

    write_evidence_package(scan_result, scan_dir)
    # Must precede the manifest/monolith writes below: it sets
    # manifest.sanitized_workspace, and both persisted representations have
    # to carry the same manifest.
    sanitized = write_sanitized_workspace(scan_result, scan_dir, cred_filter)
    console.print(
        f"  Sanitized workspace: {len(sanitized['files'])} files, "
        f"{len(sanitized['excluded'])} excluded"
    )

    # meta.json
    (scan_dir / "meta.json").write_text(json.dumps({
        "platform": scan_result.platform,
        "repo_root": scan_result.repo_root,
        "scanned_at": str(scan_result.scanned_at),
        "stats": scan_result.stats,
        "warnings": [warning.model_dump() for warning in scan_result.warnings],
        "source_inventory": scan_result.source_inventory,
    }, indent=2, default=str), encoding="utf-8")

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

    (scan_dir / "tdm_maps.json").write_text(json.dumps(
        [mapping.model_dump() for mapping in scan_result.tdm_maps], indent=2,
    ), encoding="utf-8")

    # Enterprise artifact collections + the two deterministic derived
    # ledgers. Segmented so a consumer can read the mapping coverage of a
    # 400-job project without loading scan_result.json.
    for filename, payload in (
        ("structures.json", [s.model_dump() for s in scan_result.structures]),
        ("xslt.json", [t.model_dump() for t in scan_result.xslt_transforms]),
        ("services.json", [x.model_dump() for x in scan_result.services]),
        ("mapping_accounting.json",
         [record.model_dump() for record in scan_result.mapping_accounting]),
        ("capability_ledger.json",
         scan_result.capability_ledger.model_dump()
         if scan_result.capability_ledger is not None else None),
    ):
        (scan_dir / filename).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8",
        )

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
        # A composite source bundle can contain identical names and groups.
        # Keep the legacy layout for ordinary scans; use manifest.job_files as
        # the authoritative additive lookup when an identity needs escaping.
        qualified = bool(job.job_id and job.job_id != job.name)
        composite = len(scan_result.manifest.source_roots) > 1
        if job.source_root and composite:
            group = f"{job.source_root}__{group}"
        safe_group = _bounded_segment(group, "Global") if composite else (re.sub(r'[/\\:*?"<>|]+', "_", group).strip("_") or "Global")
        job_group_dir = jobs_dir / safe_group
        job_group_dir.mkdir(parents=True, exist_ok=True)
        job_key = job.job_id or job.name
        safe_name = _bounded_segment(job_key, "unnamed") if qualified or composite else (re.sub(r'[/\\:*?"<>|]+', "_", job_key).strip("_") or "unnamed")
        filename = f"{safe_name}.json"
        (job_group_dir / filename).write_text(
            job.model_dump_json(indent=2), encoding="utf-8"
        )
        scan_result.manifest.job_files[job_key] = f"jobs/{safe_group}/{filename}"

    # job_files is populated while segmented output is written.
    (scan_dir / "manifest.json").write_text(
        scan_result.manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    # Keep both persisted representations in lockstep.  job_files is filled
    # only after every segmented job payload has a physical location.
    path.write_text(scan_result.model_dump_json(indent=2), encoding="utf-8")
