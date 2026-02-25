# Plan: Fix Kimi K2.5 temperature parameter

## Context
Kimi K2.5 API rejects `temperature=0.3` with error: `invalid temperature: only 1 is allowed for this model`. This causes every Kimi call to fail, falling back through groq (401) → openrouter (429) → ollama. The fix is to set `temperature=1` for Kimi calls.

## Changes

**File: `backend/src/generation/llm_client.py`**
- Line 93 (`_kimi_generate`): Change `temperature=0.3` → `temperature=1`
- Line 109 (`_kimi_stream`): Change `temperature=0.3` → `temperature=1`

That's it — two lines.

## Verification
- Run `pytest backend/tests/` to confirm no test regressions
- Restart backend and make a query — logs should show `LLM response from kimi` instead of the temperature error
