# intodoc — How to Run

All commands assume you're in the `intodoc/` directory.

```bash
cd intodoc
```

## 1. Fresh Scan Only (slow, ~16min for 257 jobs / 1,642 files)

```bash
python -m intodoc scan ../vaxxinova-talend -p talend -o ./docs
```

Output: `./docs/scan_result.json` + segmented files in `./docs/scan/`

## 2. Dry Run — See Estimates Without Calling LLM

```bash
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --dry-run
```

Uses cached scan if `./docs/scan_result.json` exists. Prints chunk count, token estimates, LLM call count, and estimated cost.

## 3. Analyze (LLM Passes)

Requires `INTODOC_LLM_API_KEY` env var set.

```bash
# All 18 passes
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs

# Specific passes only
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --passes 1-3
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --passes 1,5,10

# With custom budget
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --cost-limit 10.0
```

## 4. Full Pipeline (scan + analyze + generate)

```bash
python -m intodoc run ../vaxxinova-talend -p talend -o ./docs
```

## 5. Force Re-scan (bypass cache)

```bash
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --force-rescan --dry-run
```

## 6. Use a Specific Scan File

```bash
python -m intodoc analyze ../vaxxinova-talend -p talend -o ./docs --scan-file /path/to/scan_result.json
```

## Key Flags

| Flag | Description |
|------|-------------|
| `--dry-run` | Skip LLM calls, print estimates only |
| `--force-rescan` | Ignore cached scan, run fresh |
| `--scan-file PATH` | Load scan from a specific file |
| `--passes 1-3` | Run only passes 1, 2, 3 |
| `--cost-limit 10.0` | Set LLM budget cap in USD |
| `-o ./docs` | Output directory |

## Output Structure

```
docs/
  scan_result.json          # monolithic scan (backward compat)
  scan/
    meta.json               # platform, repo_root, stats
    manifest.json           # job cross-reference graph
    connections.json        # metadata connections
    contexts.json           # context variable groups
    routines.json           # non-system routines
    files_index.json        # classified files
    jobs/{group}/{job}.json # individual jobs
  chunks/
    {chunk_id}.json         # distilled analysis chunks
  chunks_summary.json       # chunk overview
  pass_results/
    p01_{chunk_id}.json     # LLM analysis results per pass/chunk
```

## Environment Variables

```bash
export INTODOC_LLM_API_KEY="sk-..."          # required for LLM analysis
export INTODOC_LLM_BASE_URL="http://..."     # optional, for local models
export INTODOC_MODEL_DEFAULT="claude-sonnet-4-5-20250929"  # default model
export INTODOC_MODEL_SYNTHESIS="claude-opus-4-6"           # pass 10 model
```
