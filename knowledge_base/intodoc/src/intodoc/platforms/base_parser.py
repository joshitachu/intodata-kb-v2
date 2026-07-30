"""Shared base class for all platform parsers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from intodoc.models import ScanWarning

if TYPE_CHECKING:
    from intodoc.models import ParsedJob
    from intodoc.scanner.credential_filter import CredentialFilter


class BaseParser:
    """Common warning infrastructure and credential-filtered file reading.

    All platform parsers (Talend, Workato, Lobster, Generic) inherit from this
    class to avoid duplicating ~30 LOC of identical boilerplate.
    """

    def __init__(self, cred_filter: CredentialFilter) -> None:
        self.cred_filter = cred_filter
        self._warnings: list[ScanWarning] = []
        self._warn_root: Path | None = None

    def _warn(self, *, kind: str, file: Path, message: str, phase: str) -> None:
        """Record a non-fatal scan warning alongside the usual console output."""
        try:
            rel = str(file.relative_to(self._warn_root)) if self._warn_root else str(file)
        except ValueError:
            rel = str(file)
        self._warnings.append(
            ScanWarning(kind=kind, file=rel, message=message, phase=phase)
        )

    def _read_filtered(self, path: Path) -> str:
        """Read a file and apply credential filtering."""
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.cred_filter.filter_text(text, filename=str(path))

    @staticmethod
    def _prepare_single_root_topology_identities(
        jobs: list[ParsedJob], repo_root: Path, platform: str,
    ) -> list[dict[str, str]]:
        """Give non-composite platform artifacts deterministic identities.

        Talend has its own multi-root identity resolver.  The other platform
        parsers scan one uploaded project at a time, but they still need the
        same manifest contract: every artifact belongs to a source root and
        duplicate display names must not collapse into one topology node.

        Keep the historic display-name key when it is unique.  When it is
        ambiguous, qualify it using the parsed source-relative path.  This is
        deterministic, preserves existing consumers for ordinary projects,
        and deliberately refuses to infer that two same-named artifacts are
        the same thing.
        """
        by_name: dict[str, list[ParsedJob]] = {}
        for job in jobs:
            by_name.setdefault(job.name, []).append(job)

        root_id = "."
        for name, same_named_jobs in by_name.items():
            if len(same_named_jobs) == 1:
                same_named_jobs[0].job_id = name
                same_named_jobs[0].source_root = root_id
                continue

            # The normal parsers create one artifact per relative source path.
            # A collision even at that level is retained as a deterministic
            # numbered suffix rather than being silently merged.
            for index, job in enumerate(sorted(same_named_jobs, key=lambda item: item.path), start=1):
                relative = Path(job.path).as_posix().lstrip("./") or "artifact"
                job.job_id = f"{root_id}::{relative}"
                if any(
                    other is not job and other.job_id == job.job_id
                    for other in same_named_jobs
                ):
                    job.job_id = f"{job.job_id}#{index}"
                job.source_root = root_id

        return [{
            "id": root_id,
            "path": root_id,
            "platform": platform,
            "role": "execution",
            "label": repo_root.name,
        }]
