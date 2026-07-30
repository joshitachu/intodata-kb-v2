# Progress Log

## Session: 2026-02-16

### [Feature]: Milestones 1-9 -- Full intodoc implementation
- **Files created**: ~60 files across src/intodoc/, tests/, .agent/, .claude/
- **Problem**: No documentation generator existed for the vaxxinova Talend project
- **Root cause**: N/A (greenfield)
- **Change**: Created complete intodoc Python CLI tool with:
  - Click CLI with scan/analyze/generate/export/run commands
  - Pydantic Settings config (IntodocConfig)
  - 30+ Pydantic data models for all pass results
  - Platform-explicit plugin architecture (base.py, registry.py)
  - Talend platform plugin with XML/XMI parser, distiller, file_rules.yaml, best_practices.yaml
  - 10 LLM prompts tuned for Talend analysis
  - Credential filter (7 regex patterns for passwords, IPs, JDBC URLs, certs, etc.)
  - File walker with SHA-256 hashing
  - LLM client with Anthropic API, retry logic, structured output via tool_use
  - Cost tracker with budget enforcement
  - Chunker grouping jobs by region (~80K token target)
  - 10 analysis passes (inventory, execution, data flows, env, external systems, error handling, state, custom code, business logic, synthesis)
  - Jinja2 renderer with 16 markdown templates
  - PDF exporter (weasyprint) and Qdrant RAG exporter
  - Git history analyzer
  - Full pipeline wired: scan -> chunk -> analyze -> generate
- **Why this approach**: Following the implementation plan's platform-explicit architecture
- **Verification**:
  - `pip install -e ".[dev]"` -- success
  - `intodoc scan ../vaxxinova-talend --platform talend` -- 257 jobs, 1642 files
  - All imports verified
  - 7/7 credential filter tests passing
  - Chunking tested: groups by region correctly

### [Fix]: Unicode encoding error on Windows
- **Files changed**: `src/intodoc/cli.py` (lines 22-24, 102)
- **Problem**: `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` on Windows cp1252
- **Root cause**: Em-dash and arrow characters in Click help text not encodable in cp1252
- **Change**: Replaced Unicode characters with ASCII equivalents
- **Verification**: `intodoc --help` now renders correctly

## Session: 2026-02-17

### [Fix]: Token bloat in TalendDistiller
- **Files changed**: `src/intodoc/platforms/talend/distiller.py` (full rewrite of filtering logic)
- **Files created**: `tests/test_platforms/test_talend_distiller.py` (55 unit tests)
- **Problem**: Distiller produced ~75K tokens/chunk (~3.8M total) — 18-pass run would exceed $5 budget
- **Root cause**: Filter was too narrow — only 7 param names and 5 prefixes stripped. CHECK (boolean) and CLOSED_LIST (dropdown) field types passed through unfiltered. Default values like "false", "0", "utf-8" added noise. Inline context groups duplicated chunk-level contexts.
- **Change**:
  - Expanded STRIP_PARAMS from 7 to 16 names (added UNIQUE_NAME, REPOSITORY_*, TSTATCATCHER_STATS, MAPPING, RESULT_SIZE, etc.)
  - Added STRIP_SUBSTRINGS filter (TOKEN, SECRET, WARNING, ENDPOINT)
  - Added STRIP_VALUES filter (false, 0, auto, utf-8, iso-8859-15, 2000000)
  - Added field_type-based filtering: CHECK params stripped unless in KEEP_CHECK_PARAMS whitelist (7 items); CLOSED_LIST params stripped unless in KEEP_CLOSED_LIST_PARAMS whitelist (10 items)
  - Empty components now dropped (except structural: tPrejob, tPostjob, tUnite, tFlowToIterate)
  - Context dedup removes inline job context variables already present in chunk.related_contexts
  - Added Rich console token reduction report (jobs, raw tokens, distilled tokens, reduction %, chunks, avg/chunk)
- **Why this approach**: Multi-layer filtering catches noise at every level: name, prefix, substring, value, field_type, and structural emptiness. Whitelists for CHECK/CLOSED_LIST ensure only semantically meaningful boolean/enum params survive.
- **Verification**: 55/55 unit tests passing. Expected reduction: 50-65% (from ~3.8M to ~1.3-2M tokens)

### [Feature]: Cross-Job Connections & 6 Bug Fixes
- **Files changed**:
  - `src/intodoc/models.py` — Added `Routine`, `ProjectManifest` models; added `metadata_connection_ref` to `Component`, `child_job_xmi_id` to `TRunJobRef`, `related_routines`/`manifest` to `AnalysisChunk`, changed `ScanResult.routines` from `list[str]` to `list[Routine]`; renamed `region`→`group`, `subsidiary`→`subgroup` everywhere
  - `src/intodoc/platforms/talend/parser.py` — Added `_build_job_id_index()` (resolves XMI IDs to job names), fixed `_extract_trunjob_ref()` to produce readable names, added REPOSITORY_PROPERTY_TYPE extraction in `_parse_node()`, rewrote `_parse_routines()` to extract Java source code from `.item` files (500-line cap, credential filtering, system routine detection), added `_build_manifest()` static method; renamed region→group vars
  - `src/intodoc/platforms/talend/distiller.py` — Removed `ENDPOINT` from `STRIP_SUBSTRINGS` (preserves REST/OData URLs), removed blanket `PROPERTY:` prefix strip (replaced with specific `PROPERTY:REPOSITORY_PROPERTY_TYPE` and `PROPERTY:REPOSITORY_SCHEMA_TYPE` in `STRIP_PARAMS`), added `_match_connections_for_jobs()` (replaces broken region-name matching with component ref-based matching), added `_match_routines_for_jobs()`, attaches manifest to every chunk; renamed region→group
  - `src/intodoc/analyzer/passes/base.py` — Added `format_manifest_section(chunk)` helper that serializes non-empty manifest dicts to JSON
  - `src/intodoc/analyzer/passes/p01_inventory.py` through `p18_reusability.py` (all 18 passes) — Each imports `format_manifest_section`, inserts Global Project Graph section into prompts, renamed region→group in serialized data
  - `src/intodoc/analyzer/passes/p08_custom_code.py` — Additionally includes routine source code in prompt (2K char cap per routine)
  - `src/intodoc/generator/templates/00_project_overview.md.j2` — Renamed "Regions" → "Groups"
  - `src/intodoc/generator/templates/15_onboarding.md.j2` — Renamed "Region" → "Group"
  - `tests/test_platforms/test_talend_distiller.py` — Updated region→group in fixtures, renamed `test_distill_multiple_regions`→`test_distill_multiple_groups`, flipped `test_strips_endpoint_param`→`test_keeps_endpoint_param`, split `test_strips_property_prefix` into `test_strips_property_repository_refs` + `test_keeps_property_type`
  - `tests/test_platforms/test_talend_parser.py` — Updated `job.region`→`job.group` assertion
- **Problem**: LLM couldn't understand cross-job dependencies — each chunk was analyzed in isolation. tRunJob refs showed opaque XMI IDs, BigQuery connections were missed, API endpoint URLs were stripped, routine source code was lost, and there was no global project graph.
- **Changes (6 fixes)**:
  1. **region→group rename** — Made folder-level grouping generic (not Vaxxinova-specific)
  2. **ENDPOINT restored** — REST/OData/webhook URLs now survive distillation
  3. **tRunJob XMI resolution** — Job ID index maps `Property.id`→`Property.label` so tRunJob refs show human-readable names
  4. **Connection matching fixed** — Components' `REPOSITORY_PROPERTY_TYPE` refs resolved against metadata connections (replaces broken region-name matching)
  5. **Routine source code** — `.item` files parsed as Java source; `Routine` model carries `name`, `source_code`, `is_system`
  6. **ProjectManifest** — Global cross-reference graph (job_calls, job_called_by, job_connections_used, connection_used_by, job_contexts_used, routines_used_by, job_groups) attached to every chunk and injected into all 18 pass prompts
- **Why this approach**: The LLM needs to see how jobs, connections, contexts, and routines relate across the entire project — not just within one chunk. The manifest is lightweight (~675 tokens for 50 jobs) and gives the LLM a project-wide map.
- **Verification**: 67/71 tests passed (all 57 distiller unit + first 10 parser integration). Remaining 4 parser integration tests timed out (each re-parses the full vaxxinova-talend project — needs optimization).

## Session 2026-02-18 — Phase 0: Project Cleanup & Knowledge Base

### What was done
- **Phase 0A: File Cleanup**
  - Deleted: `__pycache__/`, `test.item`, `test.py`, `distilled_output.json` (root level)
  - Deleted: `.github/agents/logic-backend.agent.md` (empty template from old project)
  - Deleted: `vaxxinova/gen_p09.py` (65KB generated script, output already in pass_results)
  - Created root `.gitignore` (Python, Node, .env, IDE, OS, test artifacts)
  - Verified `RAG_pipeline/backend/.env` not tracked by git

- **Phase 0B: Knowledge Base Population**
  - `vaxxinova/.agent/README.md` — Project documentation index with vision, subprojects, key docs
  - `vaxxinova/.agent/system/architecture.md` — High-level project architecture (pipeline, tech stack, metrics)
  - `vaxxinova/.agent/tasks/phase0-cleanup.md` — Phase 0 FRD (this cleanup)
  - `vaxxinova/.agent/tasks/phase1-docs-improvement.md` — Phase 1 FRD (doc quality + migration)
  - `vaxxinova/.agent/tasks/phase2-universal-ir.md` — Phase 2 FRD (universal IR)
  - `vaxxinova/.agent/tasks/phase3-migration-generator.md` — Phase 3 FRD (code generation)
  - `intodoc/.agent/sop/add-platform.md` — SOP for adding platform plugins
  - `intodoc/.agent/sop/add-pass.md` — SOP for adding analysis passes
  - `intodoc/.agent/sop/run-analysis.md` — SOP for running full analysis pipeline
  - `intodoc/.agent/decisions/001-platform-explicit.md` — ADR: why platform-explicit
  - `intodoc/.agent/decisions/002-credential-filter-first.md` — ADR: why filter first
  - `intodoc/.agent/tasks/milestones-completed.md` — M1-M9 completion records

- **Phase 0C: Progress Logging**
  - Updated PROGRESS_LOG.md, PROJECT_MEMORY.md, context.md

- **Phase 0D: CLAUDE.md Updates**
  - Updated `vaxxinova/CLAUDE.md` with mandatory reads, logging protocol, FRD links
  - Updated `vaxxinova/intodoc/CLAUDE.md` with current/planned state, known pitfalls

### What went well
- Clean separation between knowledge base levels (vaxxinova/ vs intodoc/)
- SOPs are accurate and reference actual code patterns

### What went wrong / lessons learned
- intodoc/.agent/ directories existed but were all empty — README.md listed files that didn't exist
- Root .gitignore didn't exist — RAG_pipeline had its own but root had none

### What's no longer needed
- `gen_p09.py` — Was a one-off generated script, output lives in pass_results
- `.github/agents/logic-backend.agent.md` — Empty template from different project

### Next steps
- Phase 1: Start with P1-F01 (doc restructure) and P1-F07 (expression catalog) in parallel
- Consider running full analysis with LLM to validate 18 passes produce good output before adding 3 more

## Session 2026-02-18 — Phase 1: Documentation Quality & Migration Readiness

### What was done
- **Step 1: Fix prompt loading** — `get_prompt()` in `platforms/talend/__init__.py` was broken: it expected `p09.txt` but files are named `p09_business_logic.txt`. Updated to glob-match `pNN_*.txt` pattern.

- **Step 2: New Pydantic models** — Added 3 model groups (~80 lines) to `src/intodoc/models.py`:
  - `MigrationAssessment` (P19): `MigrationBlocker`, `JobMigrationAssessment`, `MigrationAssessment`
  - `LineageMatrix` (P20): `ColumnTransformation`, `LineageEntry`, `LineageMatrix`
  - `ExpressionCatalog` (P21): `ExpressionCatalogEntry`, `ExpressionCatalog`

- **Steps 3-5: New analysis passes** — Created 3 new pass files + 3 new prompt files:
  - `analyzer/passes/p19_migration_assessment.py` + `prompts/p19_migration_assessment.txt`
  - `analyzer/passes/p20_lineage_matrix.py` + `prompts/p20_lineage_matrix.txt`
  - `analyzer/passes/p21_expression_catalog.py` + `prompts/p21_expression_catalog.txt`

- **Step 6: Registry + CLI wiring**
  - `pass_registry.py` — 3 new imports + 3 registry entries (19-21)
  - `cli.py` — 3 new `pass_context_map` entries, `--passes` default `"1-18"` -> `"1-21"`, `--audience` option on `generate` and `run` commands
  - `config.py` — default passes `range(1, 19)` -> `range(1, 22)`

- **Steps 7-8: Templates + renderer**
  - Created 8 new Jinja2 templates: 24_executive_dashboard, 25_migration_assessment, 26_gap_analysis, 27_data_governance, 28_lineage_matrix, 29_expression_catalog, 30_validation_test_plan, 31_migration_runbook
  - `renderer.py` — Added 8 TEMPLATE_MAP entries (24->32 total), added `_BUSINESS_TEMPLATES` set, added `audience` param to `render_all()` with business/technical/all modes

- **Step 9: RAG metadata**
  - Updated all 24 existing templates: metadata line changed from `> Auto-generated by intodoc — {date}` to `> Platform: {platform} | Generated: {date}`
  - New 8 templates already have enhanced metadata + cross-references
  - `exporter/qdrant.py` — Added `allowed_groups`, `embedding_model`, `document_id`, `chunk_index` to chunk metadata

### What went well
- Prompt loading fix caught a real latent bug (would have crashed on first LLM run)
- All verification checks passed on first try: models import, passes register, prompts load, CLI options work, 32 templates in TEMPLATE_MAP
- Dual-audience rendering is clean: just a set of business-appropriate template names

### What went wrong / lessons learned
- Prompt file naming was `pNN_name.txt` but `get_prompt()` expected `pNN.txt` — glob matching is the right fix (descriptive names are better than bare numbers)
- No test files exist in the project — verification was done via manual Python imports and CLI checks

### What's no longer needed
- Old prompt naming pattern `pNN.txt` — glob matching handles both patterns now

### Next steps
- Run end-to-end analysis with LLM API key to test passes 19-21 produce quality output
- Phase 2: Universal Intermediate Representation (see `.agent/tasks/phase2-universal-ir.md`)
- Consider adding automated tests for the new passes and templates

## Session 2026-02-19 -- Document 25 (Preventive Analysis) + Architecture Doc Updates

### What was done
- **New prompt file**: `src/intodoc/platforms/talend/prompts/p22_preventive_analysis.txt`
  - Forward-looking risk analysis prompt covering: anti-patterns, missing safeguards, fragile patterns, single points of failure, implicit assumptions
  - 7 extraction dimensions: latent failures, missing safeguards, anti-pattern instances, fragility scores, technical debt, cascading failure chains, health score inputs
  - Output format: structured JSON matching the p06_error_handling.txt pattern

- **New reference doc**: `reference-docs/25_PREVENTIVE_ANALYSIS.md`
  - Template for Phase 3 doc agent output (~400 lines)
  - Structure: Health Score Summary, Methodology, Critical Path Risks (CPR-NNN), Bug Potential Catalog (PA-XX-NNN categories), Recommended Fixes (prioritized by timeframe), Technical Debt Assessment, Remediation Roadmap (3 phases), Appendix by Region
  - Follows same conventions as 11_KNOWN_ISSUES.md (ASCII-only, severity tables, concrete job references)

- **Deleted stray file**: `reference-docs/02_ARCHITECTURE - kopie.md` (Dutch copy artifact)

- **Updated `vaxxinova/CLAUDE.md`** (8 changes):
  - Added PREVENTIVE_RISKS section to Phase 2 group summary structure
  - Added p22 to prompt files table
  - Added Preventive agent to doc agent groups table
  - Added 25 to always-generate list
  - Added Preventive row to analysis prompt reference table
  - Updated project structure (added .agent/, .claude/, .mcp.json, .gitignore; p01-p21->p01-p22; 24->25 reference docs)
  - Removed 24_EXECUTIVE_DASHBOARD.md from Overview agent (not in reference-docs)
  - Added 12_CHANGE_LOG.md to User Docs agent (exists in reference-docs)
  - Added mandatory progress logging rule to STRICT RULES section

- **Updated `.agent/system/architecture.md`**: Major rewrite
  - Replaced old single-pipeline description with dual-mode architecture (Scanner CLI + MCP Agent)
  - Updated tech stack: added "Agent: Claude Code (via MCP server)", marked Jinja2 as legacy
  - Updated data pipeline diagrams for both MCP and legacy paths
  - Updated counts: 22 prompts, 25 reference docs
  - Added mcp-server/ and dashboard/ to project structure

- **Updated `.agent/README.md`**:
  - Added current architecture section describing dual-mode operation
  - Added references to CLAUDE.md, mcp-server/, dashboard/, .mcp.json
  - Verified intodoc/RUN.md exists (kept reference)

- **Updated `.agent/tasks/phase1-docs-improvement.md`**:
  - Fixed `01_EXECUTIVE_DASHBOARD.md` -> `24_EXECUTIVE_DASHBOARD.md`, noted as superseded

- **Updated `intodoc/CLAUDE.md`**: Architecture corrections
  - Added note at top directing to vaxxinova/CLAUDE.md for MCP workflow
  - Updated current state to reflect MCP-based doc generation as primary
  - Marked CLI analyzer + Jinja2 templates as "legacy CLI path"
  - Updated prompt count to 22, reference doc count to 25
  - Added progress logging rule

- **Updated `intodoc/.claude/context.md`**: Full rewrite
  - Reflects MCP-based doc generation as current mode
  - Added mcp-server/, dashboard/, document 25 to what's complete
  - Updated project structure diagram

- **Updated `intodoc/.claude/PROJECT_MEMORY.md`**:
  - Added decision #9: MCP-based doc generation replaces CLI analyzer
  - Added lesson learned about keeping .agent/ and .claude/ docs in sync

### What went well
- All changes are consistent across files -- no contradictions
- Reference doc count is now accurate: 25 files (00-23 + 25, minus the deleted kopie)
- Prompt count is now accurate: 22 files (p01-p22, noting p17 cross_region exists)

### Lessons learned
- Always update .agent/ and .claude/ docs after architecture changes -- they had drifted significantly
- The project had shifted from CLI-based LLM pipeline to MCP-based agent workflow but docs still described the old architecture

### Next steps
- Run full doc generation pipeline to verify document 25 is generated correctly
- Verify insert_to_rag picks up the new document

## Session 2026-02-18 -- Phase 2: Universal Intermediate Representation

### What was done
- **New package: `src/intodoc/ir/`** (9 files, ~1,700 lines):
  - `ir/models.py` -- 30+ Pydantic models: enums (StepType, ConnectorType, ExprNodeType, ExprDataType, JoinType, WriteMode, ErrorStrategy), expression AST discriminated union (FieldRef, LiteralNode, FunctionCall, MethodCall, Ternary, NullCoalesce, BinaryOp, UnaryOp, Cast, Unparsed), pipeline models (PipelineStep, ColumnMapping, JoinSpec, FilterSpec, AggregateSpec, DataSource, DataSink, ConnectionDefinition, NormalizedPipeline), orchestration models (OrchestrationNode, OrchestrationEdge, OrchestrationDAG), top-level NormalizedProject + NormalizationStats
  - `ir/expression_parser.py` -- Hand-written recursive descent parser with 3-tier approach: regex fast path (85%), recursive descent (14.5%), graceful fallback to Unparsed (0.5%). Post-parse: null coalesce detection, complexity scoring (1-5)
  - `ir/function_map.py` -- 35 static function mappings, 30 instance method mappings, Java type map
  - `ir/normalizer.py` -- `normalize(ScanResult) -> NormalizedProject`. 35 component type mappings, tMap special handling, connection normalization, step wiring
  - `ir/dag.py` -- `build_dag(manifest, jobs) -> OrchestrationDAG`. BFS depth, DFS cycle detection, Mermaid rendering
  - `ir/persistence.py` -- `save_ir()` / `load_ir()` with monolithic + segmented output
  - `ir/pseudocode.py` -- Human-readable pseudocode renderer
  - `ir/validator.py` -- 6 validation checks
  - `ir/type_inferrer.py` -- Bottom-up AST type inference
  - `ir/llm_enrichment.py` -- Stub interface for future LLM enrichment
- **CLI**: Added `intodoc normalize` command with --output-dir, --scan-file, --validate options
- **Tests**: 62 new tests in `tests/test_ir/` (expression parser, normalizer, DAG, persistence, validator)

### Verification (257 real Vaxxinova jobs)
- 257 pipelines, 5342 steps, 24837 expressions
- **99.4% parse rate** (target was 95%)
- DAG: 251 nodes, 10 edges, depth 1, acyclic
- Validation passed (0 errors)
- 62/62 IR tests + 71 existing tests passing
- Completed in 1.1 seconds

### What went well
- 99.4% expression parse rate far exceeds 95% target
- Three-tier parsing highly effective -- regex handles vast majority
- Full normalization in ~1 second for 257 jobs

### What went wrong / lessons learned
- Test fixture used `c.name` but field is `connection_id` -- caught by tests
- 637 orphaned step warnings from tMap internal flows (expected, not a bug)

### Next steps
- Phase 3: Migration Code Generator
- Optional: LLM enrichment implementation (stub ready)
- Optional: Reduce orphaned step warnings for tMap internal flows

## Session: 2026-07-29

### [Feature]: Complete the static topology evidence, landscape, and L4 lineage roadmap

- **Files changed**: `src/intodoc/{cli.py,landscape.py,lineage.py,models.py}`; `src/intodoc/platforms/{base_distiller.py,base_parser.py,generic/parser.py,lobster/parser.py,talend/parser.py,workato/parser.py}`; `src/intodoc/scanner/{credential_filter.py,post_filter.py,redaction_verifier.py}`; `tests/test_landscape/*`; `tests/test_lineage/test_build_lineage_matrix.py`; `tests/test_platforms/{test_generic.py,test_lobster.py,test_parser_warnings.py,test_talend_esb_classification.py,test_talend_parser.py,test_workato.py}`; `tests/test_scanner/{test_credential_filter.py,test_post_filter.py}`.
- **Problem**: Composite topology scans omitted Talend joblets, routelets, tXMLMap, and TDM maps; unsupported categories were silent; redaction discarded safe protocol/auth/TLS facts; only Talend emitted useful topology facts; and no deterministic organization landscape existed above job level.
- **Root cause**: The scanner walked only `process/` and `routes/`, modeled only classic tMap, treated redaction as a string replacement, and had no common topology-fact or landscape contract across platform parsers.
- **Change**: Added reusable-artifact and mapping parsers with explicit partial/unmapped warnings; exact-only L4 lineage for tMap/tXMLMap/TDM; safe `RedactionDescriptor` capture including context environments; platform-neutral relations/resources/channels/endpoints for Talend, Workato, Lobster, and Generic; deterministic application/system rollup with typed provenance and dependency classes; and atomic four-artifact publication approval for scan, manifest, lineage, and landscape.
- **Why this approach**: Static extraction stays deterministic and auditable. Ambiguous mappings, contexts, connections, and peers remain unresolved instead of being guessed; redaction preserves only safe structure, never original endpoint or credential values.
- **Verification**: full scanner suite **588 passed, 8 skipped**; Ruff and compileall clean. A fresh six-ZIP Talend scan completed with 429 designs, 2,145 lineage entries, 14 applications, 137 system candidates, 185 landscape edges, 2,332 safe context descriptors, one exact interface descriptor, zero private descriptor markers, and a valid four-hash publication receipt.

## Session: 2026-07-30

### [Fix]: Make Talend landscape identities evidence-backed and stable

- **Files changed**: `src/intodoc/{landscape.py,models.py}`; `src/intodoc/platforms/talend/parser.py`; `tests/test_landscape/test_build_landscape.py`; `tests/test_platforms/test_talend_parser.py`.
- **Problem**: The L1 graph promoted arbitrary unknown component names such as Hash, ContextDump, and AdvancedFileOutputXML to external systems, and represented repeated copies of the same Talend project as separate applications.
- **Root cause**: Unknown component names were normalized by a generic suffix-stripping fallback, while application identity defaulted to the physical ZIP/source-root path rather than Talend's stable project XMI identity.
- **Change**: Removed the unknown-component system fallback; retained generic REST/JMS/file mechanisms as L2 evidence without inventing peer identities; captured Talend project ID, label, technical label, and description from `talend.project`; grouped repeated roots by project ID; retained every physical root as provenance; deduplicated identical artifacts; and remapped/merged landscape edges onto canonical project identities.
- **Why this approach**: A component kind or transport proves implementation mechanics, not a peer system. Talend's XMI project ID is exported platform evidence and remains stable across nested or repeated ZIP imports.
- **Verification**: focused scanner/landscape suite **81 passed, 7 skipped**; Ruff and compileall passed. The six-ZIP topology rescan completed with 429 designs and 2,145 lineage entries; landscape reduced from 14 to 8 project identities, 123 to 43 system/configuration identities, and 185 to 32 supported edges. Hash, ContextDump, and AdvancedFileOutputXML are absent. References is one project with six-copy provenance and seven deduplicated designs.
