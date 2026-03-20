"""Workato distiller -- noise already stripped during parse (raw JSON passthrough)."""

from __future__ import annotations

from intodoc.models import ParsedJob
from intodoc.platforms.base_distiller import BaseDistiller


class WorkatoDistiller(BaseDistiller):
    """Workato-specific distiller. Noise is stripped during parsing,
    so _distill_job is a no-op. Extends BaseDistiller for chunking."""

    def _distill_job(self, job: ParsedJob) -> ParsedJob:
        """No-op -- noise already stripped in parser via _strip_noise_keys()."""
        return job
