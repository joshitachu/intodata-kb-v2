# Progress Tracker

## Milestone 1: Project Scaffold
- [x] pyproject.toml
- [x] CLAUDE.md
- [x] .agent/ documentation skeleton
- [x] cli.py -- Click CLI with --platform flag
- [x] config.py -- IntodocConfig Pydantic Settings
- [x] models.py -- ALL Pydantic models
- [x] platforms/base.py -- abstract PlatformPlugin interface
- [x] platforms/registry.py -- load platform by name
- [x] __init__.py and __main__.py

## Milestone 2: Talend Platform Plugin + Scanner Core
- [x] scanner/walker.py
- [x] scanner/credential_filter.py
- [x] platforms/talend/ (parser, distiller, file_rules, best_practices, prompts)
- [x] Tested: 257 jobs parsed, 1642 files, 12 connections, 31 contexts, 15 routines

## Milestone 3: LLM Client + Chunker
- [x] analyzer/llm_client.py
- [x] analyzer/cost_tracker.py
- [x] chunker/grouper.py
- [x] analyzer/pass_registry.py + passes/base.py

## Milestone 4: Pass 1 (Component Inventory) End-to-End
- [x] passes/p01_inventory.py
- [x] generator/renderer.py
- [x] templates/01_component_inventory.md.j2

## Milestone 5: Pass 3 (Data Flows)
- [x] passes/p03_data_flows.py
- [x] templates/04_data_flows.md.j2

## Milestone 6: Remaining Passes (2, 4-9)
- [x] Pass 2: Execution Logic
- [x] Pass 4: Environment & Config
- [x] Pass 5: External Systems
- [x] Pass 6: Error Handling & Resilience
- [x] Pass 7: State & Side Effects
- [x] Pass 8: Custom Code
- [x] Pass 9: Business Logic Detection

## Milestone 7: Pass 10 (Synthesis) + Remaining Templates
- [x] passes/p10_synthesis.py
- [x] All 16 templates created (00-15)

## Milestone 8: Export
- [x] exporter/pdf.py
- [x] exporter/qdrant.py

## Milestone 9: Polish
- [x] scanner/git_analyzer.py
- [ ] Incremental update mode
- [ ] Rich progress bars

## Remaining
- [ ] End-to-end test with ANTHROPIC_API_KEY
- [ ] Incremental mode (.intodoc_state.json)
- [ ] Rich progress bars for all stages
