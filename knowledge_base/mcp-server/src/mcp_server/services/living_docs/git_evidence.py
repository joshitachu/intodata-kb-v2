"""Bounded, redacted evidence from Git objects, never a mutable checkout."""

from __future__ import annotations

import re
from pathlib import Path

from mcp_server.providers.custom.noncode_evidence import redact_text
from mcp_server.services.git_remote_service import run_git


async def collect_git_evidence(
    repo: str,
    before: str,
    after: str,
    changes: list,
    *,
    max_files: int = 80,
    max_bytes: int = 600_000,
    additional_paths: list[str] | None = None,
) -> dict:
    for sha in (before, after):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise ValueError("Evidence requires full immutable commit IDs")
        actual = await run_git(repo, "rev-parse", f"{sha}^{{commit}}", strip=True)
        if actual != sha:
            raise ValueError("Source revision did not resolve to the requested commit")
    names = await run_git(repo, "ls-tree", "-r", "--name-only", after)
    catalog = set(names.splitlines())
    old_names = await run_git(repo, "ls-tree", "-r", "--name-only", before)
    old_catalog = set(old_names.splitlines())
    paths = {str(c.path) for c in changes}
    paths.update(str(c.previous_path) for c in changes if c.previous_path)
    extra = set(additional_paths or [])
    if not extra <= catalog | old_catalog:
        raise ValueError("Requested evidence path is not in the fixed source revisions")
    paths.update(extra)
    if len(paths) > max_files:
        raise ValueError(
            "Changed source exceeds the evidence file budget; split or review this update"
        )
    # Neighboring code/tests and indexed relationships provide a deterministic
    # initial expansion. The full path catalog makes absent context visible.
    stems = {Path(p).stem for p in paths}
    references: set[str] = set()
    # Git's indexed search finds consumers outside the changed directory.
    # This is a bounded heuristic, not a language-complete call graph.
    patterns = sorted(
        stem for stem in stems if len(stem) > 3 and stem not in {"index", "config", "__init__"}
    )[:20]
    if patterns:
        args = [value for pattern in patterns for value in ("-e", pattern)]
        grep_args = ("grep", "-l", "-I", "-F", *args, after, "--")
        try:
            hits = await run_git(repo, *grep_args)
            references = {line.removeprefix(after + ":") for line in hits.splitlines()}
        except RuntimeError as exc:
            # No grep matches is exit 1; other errors cannot safely provide
            # complete dependency evidence and are propagated by the read below.
            if str(exc) != f"git {' '.join(grep_args)} failed":
                raise
    related = sorted(
        (references & catalog) - paths
        | {p for p in catalog - paths if any(stem and (stem in Path(p).stem) for stem in stems)}
    )
    requested = sorted(paths) + related[: max_files - len(paths)]
    files: dict = {}
    omissions: list[str] = []
    total = 0
    for path in requested:
        versions: dict = {}
        for version, sha in (("before", before), ("after", after)):
            if path not in (old_catalog if version == "before" else catalog):
                versions[version] = None  # Added/deleted path.
                continue
            size = int(await run_git(repo, "cat-file", "-s", f"{sha}:{path}", strip=True))
            if size > 100_000 or total + size > max_bytes:
                omissions.append(f"{version}:{path}:size_budget")
                versions[version] = None
                continue
            try:
                content = await run_git(repo, "show", f"{sha}:{path}")
            except UnicodeDecodeError:
                omissions.append(f"{version}:{path}:binary")
                versions[version] = None
                continue
            if "\x00" in content:
                omissions.append(f"{version}:{path}:binary")
                versions[version] = None
                continue
            total += size
            versions[version] = redact_text(content)[0]
        files[path] = versions
    messages = await run_git(repo, "log", "-30", "--format=%H%n%B", f"{before}..{after}")
    return {
        "before_sha": before,
        "after_sha": after,
        "files": files,
        "commit_messages": redact_text(messages[:30_000])[0],
        "repository_paths": sorted(catalog)[:20_000],
        "omissions": omissions,
        "related_paths_truncated": len(related) > max_files - len(paths),
        "bytes_read": total,
    }
