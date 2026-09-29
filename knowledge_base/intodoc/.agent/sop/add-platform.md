# SOP: Add a New Platform Plugin

## Overview
Platform plugins live in `src/intodoc/platforms/<name>/` and implement the `PlatformPlugin`
abstract interface (`platforms/base.py`). Why platforms are explicit: ADR-027
(`knowledge_base/.agent/decisions/027-platform-explicit-scanning.md`).

## Steps

### 1. Create the plugin directory
```
src/intodoc/platforms/<name>/
  __init__.py            # the PlatformPlugin subclass
  parser.py              # Parse project files into ScanResult
  distiller.py           # Strip noise before chunking (extends BaseDistiller)
  file_rules.yaml        # File classification rules
  best_practices.yaml    # Platform knowledge for LLM context
  prompts/
    ANALYSIS_GUIDE.md    # Phase 2 analysis guide (optional; falls back to _shared/prompts/)
    PHASE_3_GUIDE.md     # Phase 3 writing guide (optional; falls back to _shared/prompts/)
```

The MCP server reads `prompts/ANALYSIS_GUIDE.md` / `PHASE_3_GUIDE.md` with the
`_shared/prompts/` fallback (`mcp-server/src/mcp_server/services/scanner_service.py`).
Do not edit the shipped prompt files as part of this SOP; they are product inputs.

### 2. Implement `PlatformPlugin`
Subclass `PlatformPlugin` (or `GenericPlugin` to inherit the generic fallbacks, as
Workato and Lobster do). Abstract methods: `classify_file`, `parse_project`,
`get_prompt`, `get_best_practices`. See `platforms/base.py` for signatures and
`platforms/talend/__init__.py` for a complete example.

Key rule: `parse_project` MUST apply `cred_filter` to raw text BEFORE parsing.

### 3. Register the plugin
Add an entry to `_PLATFORM_MAP` in `src/intodoc/platforms/registry.py`:

```python
"myplatform": ("intodoc.platforms.myplatform", "MyPlatformPlugin"),
```

### 4. Write file classification rules
Create `file_rules.yaml` mapping file extensions/paths to `FileType` values.

### 5. Add tests
Create `tests/test_platforms/test_<name>.py`.

### 6. Verify
```bash
intodoc scan /path/to/project --platform myplatform --output-dir ../intodoc-output
```
