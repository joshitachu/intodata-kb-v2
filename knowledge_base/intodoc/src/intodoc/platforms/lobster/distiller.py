"""Lobster distiller -- strip system variables and default-valued params.

Most noise elimination happens in the parser (selective extraction of only
meaningful attributes from nodes, fields, and agent settings). The distiller
handles:
1. Stripping system variable ContextGroups
2. Stripping known default-valued ComponentParams
3. Base class handles chunking (~30K token limit)
"""

from __future__ import annotations

from intodoc.models import ParsedJob
from intodoc.platforms.base_distiller import BaseDistiller

# ComponentParam values that are always defaults (noise)
_DEFAULT_PARAMS: dict[str, frozenset[str]] = {
    "active": frozenset({"true"}),
    "schedule_type": frozenset({"0"}),
    "interval": frozenset({"1"}),
    "auth_type": frozenset({"0", "2"}),
    "file_handling": frozenset({"0"}),
    "ftp_client_type": frozenset({"0"}),
    "clienttype": frozenset({"0"}),
}


class LobsterDistiller(BaseDistiller):
    """Lobster-specific distiller. Strips system variables and default params."""

    def _distill_job(self, job: ParsedJob) -> ParsedJob:
        """Strip system variable groups and default-valued params."""
        # 1. Strip system variable context groups
        job.context_groups = [
            cg for cg in job.context_groups
            if cg.name != "system_variables"
        ]

        # 2. Strip known default-valued params from all components
        for comp in job.components:
            comp.params = [
                p for p in comp.params
                if p.name not in _DEFAULT_PARAMS
                or p.value not in _DEFAULT_PARAMS[p.name]
            ]

        return job
