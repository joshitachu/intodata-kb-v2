# SOP: Run Full Scan + Analyze + Generate

## Prerequisites
- Python 3.12+ with intodoc installed: `pip install -e ".[dev]"` (from `intodoc/` directory)
- LLM API key set: `export INTODOC_LLM_API_KEY=sk-ant-...`
- Source project available (e.g., `../vaxxinova-talend`)

## Quick Start (Full Pipeline)
```bash
cd intodoc
intodoc run ../vaxxinova-talend --platform talend --output-dir ./docs
```

This runs: scan → chunk → analyze (18 passes) → generate (24 markdown docs)

## Step-by-Step

### 1. Scan Only
```bash
intodoc scan ../vaxxinova-talend --platform talend --output-dir ./docs
```
Produces: `docs/scan_result.json` + `docs/scan/` (segmented output)

### 2. Dry Run (Estimate Costs)
```bash
intodoc analyze ../vaxxinova-talend --platform talend --dry-run
```
Shows: chunk count, total tokens, estimated cost. No LLM calls made.

### 3. Analyze (Specific Passes)
```bash
# Run passes 1-3 only
intodoc analyze ../vaxxinova-talend --platform talend --passes 1-3

# Run specific passes
intodoc analyze ../vaxxinova-talend --platform talend --passes 1,5,9

# Use cached scan result
intodoc analyze ../vaxxinova-talend --platform talend --scan-file ./docs/scan_result.json

# Custom budget
intodoc analyze ../vaxxinova-talend --platform talend --cost-limit 10.0
```
Produces: `docs/pass_results/pNN_<chunk_id>.json`

### 4. Generate Docs
```bash
intodoc generate --output-dir ./docs
```
Produces: 24 markdown documents in `docs/`

## Configuration
Environment variables (all prefixed `INTODOC_`):
- `INTODOC_LLM_API_KEY` — Anthropic API key (required for analysis)
- `INTODOC_LLM_BASE_URL` — Custom LLM endpoint (for local models)
- `INTODOC_MODEL_DEFAULT` — Default model (default: claude-sonnet)
- `INTODOC_MODEL_SYNTHESIS` — Model for synthesis pass (default: claude-opus)
- `INTODOC_COST_LIMIT` — Budget limit in USD (default: 5.0)

## Output Structure
```
docs/
├── scan_result.json       # Monolithic scan output
├── scan/                  # Segmented scan output
│   ├── meta.json
│   ├── manifest.json
│   ├── connections.json
│   ├── contexts.json
│   ├── routines.json
│   ├── files_index.json
│   └── jobs/{group}/{name}.json
├── chunks/                # Distilled LLM-ready chunks
├── chunks_summary.json
├── pass_results/          # LLM analysis results per pass/chunk
├── briefs/                # Brief summaries per pass
└── *.md                   # 24 generated markdown documents
```

## Troubleshooting
- **Budget exceeded**: Increase `--cost-limit` or reduce passes
- **Token bloat**: Check distiller — should achieve 50-65% reduction
- **Windows encoding**: Unicode characters replaced with ASCII in CLI help text
