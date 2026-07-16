"""Optional semantic recall over curated runtime insights.

This package intentionally indexes derived insight text only. It never reads
``runtime_log_events.message`` and is safe to leave unavailable: callers retain
their primary FTS/runtime-store path when Qdrant or embeddings are down.
"""

from src.runtime.semantic.models import RuntimeSemanticResult, RuntimeSemanticSource
from src.runtime.semantic.service import RuntimeSemanticService, merge_runtime_search_results

__all__ = [
    "RuntimeSemanticResult",
    "RuntimeSemanticSource",
    "RuntimeSemanticService",
    "merge_runtime_search_results",
]
