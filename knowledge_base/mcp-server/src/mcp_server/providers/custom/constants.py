"""Shared constants for the custom provider pipeline."""

GROUP_SUMMARY_SECTION = "GROUP_SUMMARY"

# Canonical Phase 2 section name for Talend ESB/Camel route analysis. Required only
# when the project contains at least one job the scanner classified as an ESB route
# (is_esb_route=true). Collapses the former ROUTE_GRAPH / ESB_ROUTES / ESB_ROUTE_GRAPH
# synonyms to a single documented name.
ESB_ROUTES_SECTION = "ESB_ROUTES"
