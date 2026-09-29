"""The ONE definition of the custom harness phase sequence.

Both the run path (``harness.py``) and the dry-run cost estimate
(``api/routes/estimate.py``) build their phase list here. They used to keep
separate hand-written lists, and the estimate silently lost the evidence
quality gate and Phase 3.7 when those were added to the run -- under-reporting
1-4 LLM calls per run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server.providers.custom.dag import ExecutionPlan
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom.pipeline.phase import Phase


def build_phases(
    helpers: "HarnessHelpersImpl | None",
    *,
    execution_plan: "ExecutionPlan | None" = None,
    analysis_policy: "str | None" = None,
) -> list["Phase"]:
    """Return the phases in run order.

    ``helpers`` is ``None`` for estimation, where only ``Phase.estimate`` and
    ``Phase.should_skip`` are called.
    """
    # Imported lazily, as harness.run always did: the phase modules import the
    # harness helpers, which import this package.
    from mcp_server.providers.custom.pipeline.evidence_quality_gate import EvidenceQualityGate
    from mcp_server.providers.custom.pipeline.phases.phase1_investigation import Phase1Investigation
    from mcp_server.providers.custom.pipeline.phases.phase2_analysis import Phase2Analysis
    from mcp_server.providers.custom.pipeline.phases.phase26_scorecard import Phase26Scorecard
    from mcp_server.providers.custom.pipeline.phases.phase28_bundles import Phase28Bundles
    from mcp_server.providers.custom.pipeline.phases.phase3_docs import Phase3Docs
    from mcp_server.providers.custom.pipeline.phases.phase37_business_topology import (
        Phase37BusinessTopology,
    )
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    return [
        Phase1Investigation(helpers),  # type: ignore[arg-type]
        Phase2Analysis(helpers, execution_plan=execution_plan, analysis_policy=analysis_policy),  # type: ignore[arg-type]
        Phase26Scorecard(helpers),  # type: ignore[arg-type]
        Phase28Bundles(helpers),  # type: ignore[arg-type]
        EvidenceQualityGate(),
        Phase3Docs(helpers, execution_plan=execution_plan),  # type: ignore[arg-type]
        Phase37BusinessTopology(helpers),  # type: ignore[arg-type]
        Phase4Cleanup(helpers),  # type: ignore[arg-type]
    ]
