# Current Context

## Status (2026-07-29) -- topology evidence and landscape backend complete

The scanner now emits an approved four-artifact fact set:
`scan_result.json`, `scan/manifest.json`, `scan/lineage.json`, and
`scan/landscape.json`. Talend joblets/routelets, tXMLMap, and exactly-associated
TDM maps are represented; unsupported/partial evidence is warned or marked
unmapped. Workato, Lobster, and Generic parsers emit the same topology-fact
families, currently with one source root per upload.

Redaction is capture-not-discard: raw endpoints and credentials remain removed,
while safe scheme/category/auth-type/TLS/host-present descriptors survive on
component and context evidence. Descriptor transport markers cannot persist.
Landscape rollup is deterministic, root-scoped, provenance-backed, and keeps
control-flow, data, integration, and configuration dependency types separate.

**Last completed**: full scanner validation (588 passed, 8 skipped) plus a real
six-ZIP scan (429 designs, 2,145 lineage entries, 14 applications, 137 system
candidates, 185 edges) and publication/runtime contract verification.

**Immediate next**: frontend illustration/semantic zoom and application/system
curation remain separate product work. Do not add fuzzy system merging or infer
peers from endpoint declarations. Chat-over-topology remains deferred.

**Resolved blockers**: silent Talend artifact omissions, missing tXMLMap/TDM
lineage, topology-fact platform coupling, and redaction signal loss.

## Status
Documentation generation now runs via Claude Code agent + MCP server (not CLI analyzer).
Phase 2 (Universal IR) complete. Phase 3 (Migration Generator) not yet started.

## Current Mode
**MCP-based Documentation Agent** -- Claude Code reads prompts, fetches scan data via MCP tools, and writes .md docs directly to `output/{project_id}/docs/`. See `vaxxinova/CLAUDE.md` for full workflow.

## Current Phase
**Phase 3: Migration Code Generator** -- Not yet started

## What's Complete
- Full project scaffold with pyproject.toml, CLI, config, models
- Talend platform plugin: parser, distiller, file_rules.yaml, best_practices.yaml, 22 prompts (p01-p22)
- Scanner: walker, credential_filter (7 tests passing), git_analyzer
- LLM client with retry logic, cost tracker, budget enforcement (legacy CLI path)
- Chunker groups jobs by group (~80K token target)
- 21 analysis passes (p01-p21) for legacy CLI path
- 22nd prompt (p22_preventive_analysis) for MCP agent workflow
- 25 reference docs defining output format/depth for doc agents
- Jinja2 renderer with 32 markdown templates + dual-audience rendering (legacy CLI path)
- PDF exporter (weasyprint) and Qdrant RAG exporter with enhanced metadata
- Distiller optimization (50-65% token reduction)
- Cross-job connections: ProjectManifest, tRunJob resolution, routine source code
- Phase 0: Project cleanup, .agent/ knowledge base, progress logging, CLAUDE.md updates
- Phase 1: 3 new passes (P19-P21), 8 new templates (24-31), prompt loading fix, RAG metadata
- Phase 2: Universal IR (9 modules in ir/): expression parser (99.4% parse rate), normalizer, DAG, persistence, pseudocode, validator, type inferrer, LLM enrichment stub
- MCP server (mcp-server/) for serving scan data to Claude Code agent
- Dashboard (dashboard/) for project management UI
- Document 25 (Preventive Analysis & Remediation Roadmap) added to pipeline

## Project Structure
```
vaxxinova/
  .agent/                      -- Agent context docs
  .claude/                     -- Claude Code settings
  .mcp.json                    -- MCP server configuration
  CLAUDE.md                    -- Documentation agent instructions (primary)
  mcp-server/                  -- MCP + REST API server
  dashboard/                   -- React + Vite control panel (:5173)
  intodoc/                     -- Scanner CLI
    src/intodoc/platforms/talend/prompts/  -- p01-p22.txt
    reference-docs/            -- 25 example .md files
  intodoc-output/              -- Scan cache
  output/                      -- Agent output (docs + logs)
  vaxxinova-talend/            -- Source Talend repo
```

## Verified
- `pip install -e ".[dev]"` works
- `intodoc scan ../vaxxinova-talend --platform talend` works: 257 jobs, 1642 files
- `intodoc normalize -o ./docs` works: 257 pipelines, 5342 steps, 24837 expressions, 99.4% parse rate
- IR output: docs/ir/ with project.json + 252 segmented pipeline files
- 62 IR tests passing, 71 existing tests passing (133 total)
- Validation: 0 errors, DAG acyclic
- MCP server serves scan data correctly via get_chunks, fetch_chunk, etc.

## Blockers
None.

## Next Steps
- Phase 3: Migration Code Generator (see `.agent/tasks/phase3-migration-generator.md`)
- Optional: Run end-to-end LLM analysis to validate P19-P21 quality
- Optional: LLM enrichment implementation (stub ready in ir/llm_enrichment.py)

## Key Files Reference
- `vaxxinova/CLAUDE.md` -- Documentation agent workflow (MCP-based)
- `.agent/tasks/phase2-universal-ir.md` -- Full Phase 2 FRD (COMPLETE)
- `.agent/tasks/phase3-migration-generator.md` -- Full Phase 3 FRD
- `src/intodoc/ir/` -- IR package (9 modules)
- `tests/test_ir/` -- IR test suite (62 tests)
## Latest completed (2026-07-30 semantic topology)

- The scanner preserves its technical landscape and adds an evidence-backed
  semantic proposal layer: systems, integration solutions, and exchanges.
- Direct/direct-vm route addresses cannot invent cross-project edges; quoted
  empty channels are ignored; JMS evidence counts unique records.
- Exact Talend existing-connection references now preserve database identity
  and input/output direction for landscape and documentation consumers.
- Fresh topology scan: 429 designs, 43 technical systems, 8 integration
  solutions, 25 technical edges, 11 semantic systems, 14 semantic exchanges.

## Immediate next

- Review the live L1/L2 with an authenticated user.
- Add a persistent alias/confirmation registry when supplied business metadata
  becomes available; preserve decisions across rescans by stable IDs.

## Resolved blockers

- False component-derived systems, repeated-project root duplication, fabricated
  direct-channel relationships, and missing existing-connection direction.
