# SOP: Add a New Analysis Pass

## Overview
Analysis passes live in `src/intodoc/analyzer/passes/` and extract specific dimensions from integration projects via LLM analysis.

## Steps

### 1. Define the response model
Add a Pydantic model to `src/intodoc/models.py`:

```python
class MyPassResult(BaseModel):
    """Result structure for Pass N."""
    field1: str
    field2: list[SomeEntry]
    # ... structured fields the LLM must fill
```

### 2. Create the pass file
Create `src/intodoc/analyzer/passes/pNN_name.py`:

```python
from intodoc.analyzer.passes.base import AnalysisPass, format_manifest_section
from intodoc.models import MyPassResult, AnalysisChunk


class MyPass(AnalysisPass):
    number = 19  # Next available number
    name = "My Analysis Dimension"
    response_model = MyPassResult

    def build_prompt(self, chunk: AnalysisChunk, platform_prompt: str, best_practices: dict) -> str:
        # Serialize chunk data for LLM
        jobs_text = self._format_jobs(chunk)
        manifest = format_manifest_section(chunk)

        return f"""
{platform_prompt}

## Best Practices
{best_practices}

{manifest}

## Jobs in this chunk
{jobs_text}

Analyze the above and return structured results.
"""
```

Key rules:
- Always include `format_manifest_section(chunk)` for cross-job context
- Always include the platform prompt and best practices
- Set `response_model` to the Pydantic model — LLM returns structured JSON via tool_use

### 3. Register the pass
Edit `src/intodoc/analyzer/pass_registry.py`:

```python
def _build_registry():
    from intodoc.analyzer.passes.pNN_name import MyPass
    # ... existing imports ...
    return {
        # ... existing entries ...
        19: MyPass,
    }
```

### 4. Add platform-tuned prompts
Add `prompts/pNN.txt` to each platform plugin directory with platform-specific instructions.

### 5. Wire into template context
Edit `_build_template_context()` in `src/intodoc/cli.py`:

```python
pass_context_map = {
    # ... existing entries ...
    19: "my_analysis",  # Template variable name
}
```

### 6. Create the Jinja2 template
Create `src/intodoc/generator/templates/NN_DOCUMENT_NAME.md.j2` and register it in `renderer.py`'s `TEMPLATE_MAP`.

### 7. Update pass range default
If the new pass should run by default, update the `--passes` default in `cli.py`.

### 8. Verify
```bash
intodoc analyze /path/to/project --platform talend --passes 19
```
