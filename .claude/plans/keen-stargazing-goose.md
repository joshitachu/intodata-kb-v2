# Fix: LLM Providers Failing — Ollama Connection Refused + Kimi Timeout

## Context

After the RBAC fix (search now works — 20 results from Qdrant), the LLM generation step fails with:
```
kimi: Request timed out.
ollama: Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible.
```

Ollama is running and responds fine from terminal (`ollama run gemma3:12b "hello"` < 10s, `curl localhost:11434/api/tags` works). But the Python `ollama.AsyncClient` inside FastAPI **cannot connect**.

## Root Cause

**Ollama**: Windows 11 localhost resolution issue. Python's async DNS resolver (`asyncio.getaddrinfo`) resolves `localhost` to `[::1]` (IPv6), but Ollama only listens on `127.0.0.1` (IPv4). The `ollama.AsyncClient` uses httpx's async transport which hits IPv6 first → connection refused. The CLI and curl use different resolvers that prefer IPv4.

**Kimi**: 15s timeout is too tight for Kimi K2.5 API under load. Independent issue but makes it worse since both providers fail.

## Changes

### 1. Fix Ollama connection — `RAG_pipeline/backend/.env` (line 2)

Change `localhost` to `127.0.0.1` to force IPv4:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Also update the config default for consistency — `RAG_pipeline/backend/src/config.py` (line 21):

```python
ollama_base_url: str = "http://127.0.0.1:11434"
```

### 2. Increase timeouts — `RAG_pipeline/backend/src/config.py` (lines 18, 25)

```python
kimi_timeout: float = 60.0    # was 15.0
ollama_timeout: float = 120.0  # was 30.0
```

### 3. Better error logging — `RAG_pipeline/backend/src/generation/llm_client.py` (lines 146-147, 165-166)

Use `repr(e)` to show exception type when message is empty:

```python
# generate_answer() lines 146-147
logger.warning("%s failed (%.0fms): %r", name, elapsed, e)
errors.append(f"{name}: {e!r}")

# generate_answer_stream() lines 165-166
logger.warning("%s streaming failed: %r", name, e)
errors.append(f"{name}: {e!r}")
```

### 4. Add Ollama warmup — `RAG_pipeline/backend/src/api/main.py` (after reranker warmup, ~line 30)

Catch connectivity issues at startup instead of on first query:

```python
try:
    import ollama as ollama_lib
    warmup_client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    await warmup_client.chat(
        model=settings.ollama_llm_model,
        messages=[{"role": "user", "content": "hi"}],
    )
    logger.info("Ollama LLM model warmed up: %s", settings.ollama_llm_model)
except Exception as e:
    logger.warning("Ollama LLM warmup failed (non-fatal): %r", e)
```

## Files to Modify

1. `RAG_pipeline/backend/.env` — line 2 (localhost → 127.0.0.1)
2. `RAG_pipeline/backend/src/config.py` — lines 18, 21, 25
3. `RAG_pipeline/backend/src/generation/llm_client.py` — lines 146-147, 165-166
4. `RAG_pipeline/backend/src/api/main.py` — lifespan function (~line 30)

## Verification

1. Restart backend — check for "Ollama LLM model warmed up: gemma3:12b" in logs
2. If warmup fails, the `%r` logging will show the exact exception type
3. Send query via `/api/v1/query` — should get LLM response, no 500 error
4. If Kimi still times out, Ollama fallback should now work within 120s timeout
