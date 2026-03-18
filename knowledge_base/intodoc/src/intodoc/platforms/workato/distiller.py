"""Workato distiller -- strip UI/internal noise from recipe components."""

from __future__ import annotations

from intodoc.models import Component, ParsedJob
from intodoc.platforms.base_distiller import BaseDistiller


class WorkatoDistiller(BaseDistiller):
    """Workato-specific noise stripping. Extends BaseDistiller chunking."""

    # Params to always strip (Workato UI / internal metadata)
    STRIP_PARAMS: frozenset[str] = frozenset({
        # Internal IDs
        "uuid", "number",
        # UI metadata
        "position", "visible_config_fields",
        "dynamicPickListSelection", "toggleCfg",
        # Schema bloat (can be 50KB+ per step)
        "extended_input_schema", "extended_output_schema",
        "input_schema", "output_schema",
        # Internal wiring (already processed by parser)
        "block",
        "keyword",  # Already encoded in component_type
    })

    # Input params worth keeping (core action configuration)
    KEEP_INPUT_PARAMS: frozenset[str] = frozenset({
        # Step identification
        "provider", "name", "as", "title", "description",
        # Core action config
        "sobject", "entity_type", "table_name", "recipe_id",
        "channel", "message", "subject", "body",
        "url", "method", "headers",
        "query", "filter", "search_query",
        "list", "while_condition",
        "status", "error_message",
        # Database
        "table", "schema", "database",
        # File operations
        "file_name", "file_path", "folder",
        # Conditions
        "type", "operand", "conditions",
    })

    def _distill_job(self, job: ParsedJob) -> ParsedJob:
        """Strip Workato-specific noise from a recipe's components."""
        distilled_components: list[Component] = []

        for comp in job.components:
            filtered_params = [
                p for p in comp.params
                if p.name not in self.STRIP_PARAMS
                and (p.value is not None and str(p.value).strip())
            ]
            distilled_components.append(
                comp.model_copy(update={"params": filtered_params})
            )

        return job.model_copy(update={"components": distilled_components})
