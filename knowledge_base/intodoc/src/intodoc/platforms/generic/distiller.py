"""Generic distiller -- strip empty/boilerplate params, keep semantic content."""

from __future__ import annotations

from intodoc.models import (
    AnalysisChunk,
    Component,
    ComponentParam,
    ParsedJob,
    ScanResult,
)
from intodoc.platforms.base_distiller import BaseDistiller

# Params that carry no semantic value for a generic codebase
STRIP_PARAMS = frozenset({
    # Structural noise added by the directory-grouping job builder
    # (sequential flow connections are fake and have no meaning)
})

# Param name substrings that indicate noise
STRIP_SUBSTRINGS = ("TOKEN", "SECRET", "WARNING", "PASSWORD", "CREDENTIAL")

# Boilerplate values to always strip
STRIP_VALUES = frozenset({"none", "null", "undefined", "false", "0", "", "n/a"})

# Params whose value we always preserve regardless of value
ALWAYS_KEEP = frozenset({
    "content",            # full source code -- primary LLM input
    "file_path",          # orientation reference
    "annotation",         # route/controller decorators
    "class_name",         # class declaration
    "connection_string",  # DB/API connection info
    "imports",            # import lines -- used for external system inference
    "usings",             # C# using statements
    "statement_type",     # SQL operation type
    "procedure_name",     # SQL procedure name
    "tables",             # SQL tables referenced
    "routes",             # Express/Fastify route definitions
})


class GenericDistiller(BaseDistiller):
    """Strip noise from generic scan results, preserving full source content."""

    def __init__(self) -> None:
        super().__init__()
        self._constant_params: set[str] = set()

    def distill(self, scan_result: ScanResult) -> list[AnalysisChunk]:
        # Detect project-wide constant params (same value across 3+ components)
        # -- but never remove ALWAYS_KEEP params even if they are constant
        self._constant_params = self._find_constant_params(scan_result) - ALWAYS_KEEP
        return super().distill(scan_result)

    def _distill_job(self, job: ParsedJob) -> ParsedJob:
        """Strip noise params from every component in the job."""
        cleaned: list[Component] = []
        for comp in job.components:
            kept = [p for p in comp.params if self._keep_param(p)]
            if kept:
                cleaned.append(comp.model_copy(update={"params": kept}))
        return job.model_copy(update={"components": cleaned})

    def _keep_param(self, p: ComponentParam) -> bool:
        # Always preserve semantically important params
        if p.name in ALWAYS_KEEP:
            return True

        # Strip project-wide constants (zero discriminative value)
        if p.name in self._constant_params:
            return False

        # Strip None / empty values
        val = str(p.value).strip() if p.value is not None else ""
        if not val or val.lower() in STRIP_VALUES:
            return False

        # Strip by exact name
        if p.name in STRIP_PARAMS:
            return False

        # Strip params whose name contains sensitive substrings
        name_upper = p.name.upper()
        if any(sub in name_upper for sub in STRIP_SUBSTRINGS):
            return False

        return True
