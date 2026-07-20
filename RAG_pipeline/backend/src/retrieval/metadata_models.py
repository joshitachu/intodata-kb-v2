"""Typed arguments for explicit metadata retrieval capabilities."""

from enum import StrEnum


class MetadataIntent(StrEnum):
    PROJECT_LIST = "project_list"
    PROJECT_DETAIL = "project_detail"
    DOCUMENT_STATS = "document_stats"
    DOCUMENT_LIST = "document_list"
    SYSTEM_OVERVIEW = "system_overview"
    HYBRID = "hybrid"
    MY_PROFILE = "my_profile"
