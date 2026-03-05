# Lessons Learned

Rules extracted from past generation runs. Read this BEFORE starting any generation.

## Formatting Rules (learned from past failures)

- NEVER use Unicode box-drawing characters -- Windows cp1252 breaks them
- NEVER use em-dashes, smart quotes, or arrows (use --, ', -> instead)
- ALL tables must use plain ASCII pipe characters
- Use backticks for code references: `tMap_1`, `BRA_VBR_RAW_0.1`
- No Unicode symbols of any kind -- stick to ASCII 32-126

## Content Rules

- NEVER hardcode group names -- always use get_chunks() return values
- NEVER assume specific region names -- they vary per project
- ALWAYS include a table of contents in every document
- ALWAYS tag business rules as BR-{GROUP}-NNN
- ALWAYS include job names for traceability
- Quantify everything: "75 jobs" not "many jobs", "89%" not "most"
- Do NOT invent data -- every claim must trace to a group summary or manifest
- When data is sparse, write a brief note explaining what was found rather than padding

## Structural Rules

- Start every document with a title (# heading) and 1-2 sentence summary
- Use ## for major sections, ### for subsections
- End major docs with a "Summary" or "Recommendations" section
- Tag risks: CRITICAL / HIGH / MEDIUM / LOW
- Tag confidence: CERTAIN / LIKELY / INFERRED where applicable

## Process Rules

- Background sub-agents may return empty output files even when completed. Use `TaskOutput` or `resume` to retrieve results. Plan for fallback if retrieval fails for some agents.
- Doc generation now uses a staging directory (`docs_staging/`). Claude Code writes to `docs_staging/`, the server swaps it to `docs/` on success and pushes to RAG automatically. Do NOT call `insert_to_rag` from the generation prompt -- it is handled server-side in `_run_generate_background`.
- Very large docs (e.g. 17_DATA_QUALITY.md with 1,102 embedded rules) may fail RAG ingestion with 500 errors. Consider splitting or trimming oversized docs.

## Run History

### Run: 2026-02-20 -- Project: TESTClAUDE
**Docs generated**: 25
**Groups**: BRA (71 jobs), DEU (12), GENERIC (15), Global (42), ITA (6), JPN (37), SAF (1), USA (61), ZAF (3), scratchpad (5) = 252 total
**What went well:**
- All 25 documents generated successfully across 10 parallel doc-writing agents
- ASCII-only formatting maintained throughout (no Unicode violations detected)
- All documents include TOC, business rule tagging (BR-{GROUP}-NNN), job name traceability
- 7 of 10 Phase 2 group agents returned full detailed summaries (89% job coverage)
- 24 of 25 docs successfully pushed to RAG pipeline

**What went wrong:**
- Phase 2: DEU, GENERIC, ITA sub-agents completed but returned empty output (agent output files 0 bytes). Resuming agents and spawning fresh agents both failed to retrieve text. Metadata-based summaries were composed inline as fallback.
- RAG ingestion: 17_DATA_QUALITY.md failed with 500 error (likely too large)
- insert_to_rag reads from intodoc-output/ not output/ -- had to copy files

**New lessons extracted:**
- Added Process Rules section above with agent output retrieval, RAG path, and doc size guidance
