# SOP: Add a New Platform Plugin

## Overview
Platform plugins live in `src/intodoc/platforms/<name>/` and implement the `PlatformPlugin` abstract interface.

## Steps

### 1. Create the plugin directory
```
src/intodoc/platforms/<name>/
├── __init__.py
├── parser.py          # Parse project files into ScanResult
├── distiller.py       # Strip noise for LLM input
├── file_rules.yaml    # File classification rules
├── best_practices.yaml # Platform knowledge for LLM context
└── prompts/           # Platform-tuned prompts per pass
    ├── p01.txt
    ├── p02.txt
    └── ...
```

### 2. Implement `PlatformPlugin` interface
Create a class in `parser.py` that extends `PlatformPlugin` (from `platforms/base.py`):

```python
class MyPlatformPlugin(PlatformPlugin):
    name = "myplatform"
    display_name = "My Platform"

    def classify_file(self, path: Path) -> FileType: ...
    def parse_project(self, repo_root: Path, cred_filter: CredentialFilter) -> ScanResult: ...
    def distill_for_llm(self, scan_result: ScanResult) -> list[AnalysisChunk]: ...
    def get_prompt(self, pass_number: int) -> str: ...
    def get_best_practices(self) -> dict: ...
```

Key rules:
- `parse_project` MUST apply `cred_filter` to raw text BEFORE parsing
- `distill_for_llm` strips platform-specific noise to minimize token usage
- Prompts in `prompts/` are loaded by pass number (1-18)

### 3. Register the plugin
Edit `src/intodoc/platforms/registry.py`:

```python
_PLATFORMS = {
    "talend": "intodoc.platforms.talend.parser:TalendPlugin",
    "myplatform": "intodoc.platforms.myplatform.parser:MyPlatformPlugin",
}
```

### 4. Create platform-tuned prompts
Write prompt files `prompts/p01.txt` through `prompts/p18.txt`. Each prompt should be tuned to the platform's terminology and concepts.

Reference: `platforms/talend/prompts/` for examples.

### 5. Write file classification rules
Create `file_rules.yaml` mapping file extensions/paths to `FileType` values.

### 6. Add tests
Create `tests/test_platforms/test_<name>_parser.py` and `test_<name>_distiller.py`.

### 7. Verify
```bash
intodoc scan /path/to/project --platform myplatform
```
