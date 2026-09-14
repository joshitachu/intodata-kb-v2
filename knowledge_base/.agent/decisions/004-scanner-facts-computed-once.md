# ADR-004: Cross-Package Facts Are Computed In The Scanner And Only Read Downstream

## Status
Accepted 2026-07-06; still current

## Context

`intodoc` and `mcp-server` are separate installable packages with no dependency between
them; they communicate only through `scan_result.json` on disk. Talend ESB/Camel route
detection had been implemented as a late `talend_intelligence.py` heuristic that
re-derived component types from raw scan output -- duplicated logic in the place with the
least structured data.

## Decision

Any classification derivable from parsed platform data is computed in the platform parser
and flows through `scan_result.json`. Downstream consumers read the field and treat a
missing field as a defensive default, never as a trigger to re-derive it.

## Rationale

"Cross-package facts (`intodoc` scanner -> `mcp-server`) must be computed once in the
scanner and only read on the mcp-server side, never re-derived. [...] Fixed 2026-07-06 by
moving classification into `TalendParser` (`is_esb_route`/`esb_component_types` on
`ParsedJob`, flowing through `scan_result.json`); `talend_intelligence.py` now only
aggregates those pre-computed fields and treats a missing field as `False` (defensive
boundary read, not a fallback re-derivation). Apply the same pattern to any future
scanner-derived classification: compute it where the platform parser already has the
structured data, not downstream from raw JSON."

## Consequences

- Classification signals must be conventions, not curated lists. ESB detection matches the
  `c[A-Z]` component-name prefix rather than an allowlist, because a real customer route
  used `cHttp`, a genuine Camel component absent from the ~25-entry frozenset.
- Job discovery must follow the same rule: `_parse_all_jobs` walks both `process/` and
  `routes/`, because a real Talend Studio export puts Route Designs in `routes/`.
- A downstream consumer that needs a new fact must add it to the parser, not to its own
  heuristics.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
