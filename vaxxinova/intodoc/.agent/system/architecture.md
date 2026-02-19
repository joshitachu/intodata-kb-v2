# intodoc Architecture

## Overview
intodoc is a platform-explicit CLI tool that generates structured documentation from integration codebases via LLM analysis.

## Pipeline
```
scan → chunk → analyze (10 passes) → generate (16 markdown docs) → export (PDF/Qdrant)
```

## Platform Plugin System
Each platform is a self-contained plugin under `src/intodoc/platforms/<name>/`:
- `parser.py` — platform-specific file parser
- `distiller.py` — noise stripping for LLM input
- `file_rules.yaml` — file classification rules
- `best_practices.yaml` — platform knowledge for LLM context
- `prompts/` — platform-tuned prompts per analysis pass

Plugins implement `PlatformPlugin` (defined in `platforms/base.py`) and are loaded by name via `platforms/registry.py`.

## Data Flow
1. **Scanner** walks the repo, delegates file classification to the platform plugin
2. **Credential filter** redacts sensitive data from raw text BEFORE parsing
3. **Platform parser** extracts structured data (jobs, connections, context vars, etc.)
4. **Chunker** groups parsed jobs into LLM-sized chunks (~80K tokens)
5. **Analyzer** runs 10 passes per chunk, each extracting a different dimension

6. **Generator** renders pass results into 16 markdown documents via Jinja2
7. **Exporter** converts to PDF and/or pushes to Qdrant for RAG

## Key Design Decisions
- Platform-explicit (user declares `--platform`) vs auto-detect
- Credential filter runs on raw text BEFORE XML parsing
- Shared Pydantic models across all platforms (in `models.py`)
- 10-pass analysis with platform-tuned prompts per pass
