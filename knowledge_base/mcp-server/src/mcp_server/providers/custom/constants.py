"""Shared constants for the custom provider pipeline."""

GROUP_SUMMARY_SECTION = "GROUP_SUMMARY"

# Canonical Phase 2 section name for Talend ESB/Camel route analysis. Required only
# when the project contains at least one job the scanner classified as an ESB route
# (is_esb_route=true). Collapses the former ROUTE_GRAPH / ESB_ROUTES / ESB_ROUTE_GRAPH
# synonyms to a single documented name.
ESB_ROUTES_SECTION = "ESB_ROUTES"

# Group-level synthesis section covering findings that span multiple jobs
# (shared systems, repeated patterns, contradictions, end-to-end flows).
CROSS_JOB_FINDINGS_SECTION = "CROSS_JOB_FINDINGS"

# Pre-2026-07 runs named the synthesis section after the char-based chunker.
# Kept registered so cached analyses from those runs still parse on resume.
LEGACY_CROSS_CHUNK_SECTION = "CROSS_CHUNK_FINDINGS"
