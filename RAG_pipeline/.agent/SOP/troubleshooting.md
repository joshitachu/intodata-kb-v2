# Troubleshooting Guide

Common failures and fixes for the RAG pipeline.

---

## Ollama Issues

### Ollama not responding
**Symptom**: `curl http://localhost:11434/api/tags` times out or connection refused.
**Fix**: Ollama runs on the host, not in Docker. Start it: `ollama serve` (or it should auto-start as a daemon after install). Verify: `ollama list`.

### Model not found
**Symptom**: `model "gemma3:12b" not found` in backend logs.
**Fix**: `ollama pull gemma3:12b && ollama pull nomic-embed-text`. Check `ollama list` to confirm.

### Slow first query
**Cause**: Model is loading into GPU/CPU memory. First query after startup takes 10-30s.
**Not a bug**: Subsequent queries are fast.

---

## Qdrant Issues

### Empty search results despite documents being ingested
**Check 1**: Verify collection exists:
```bash
curl -s http://localhost:6333/collections | python3 -m json.tool
```
**Check 2**: Count points:
```bash
curl -s http://localhost:6333/collections/documents | python3 -c "import sys,json; d=json.load(sys.stdin); print('points:', d['result']['points_count'])"
```
**Check 3**: RBAC filter may be too restrictive. Check that the user has the correct group assignment and that `allowed_groups` in the Qdrant payload matches.

### Qdrant container restarting
**Symptom**: `docker compose ps` shows qdrant in restart loop.
**Fix**: Check volume permissions. `docker compose down && docker volume prune` (destroys all vectors — only for dev). Then `docker compose up -d qdrant`.

---

## Database / Alembic Issues

### `alembic upgrade head` fails — "Target database is not up to date"
**Fix** (in order of preference):
1. Check current state: `alembic current` and `alembic heads`
2. If schema is already correct: `alembic stamp head`
3. If behind: `alembic upgrade head` after resolving conflicts
4. Never `DROP DATABASE` as a first option — diagnose first.

### `alembic upgrade head` fails — "relation already exists"
**Cause**: Previous partial migration left tables in an inconsistent state.
**Fix**: Check `alembic_version` table. If empty or wrong, stamp the correct revision:
```bash
alembic stamp <revision_id>
```
Then re-run `alembic upgrade head`.

### asyncpg `Connection refused`
**Cause**: Postgres container not ready or not running.
**Fix**: `docker compose up -d postgres` then wait 5-10 seconds before retrying.

---

## Auth / JWT Issues

### 401 Unauthorized after restart
**Cause**: `JWT_SECRET_KEY` changed between restarts (e.g., env file regenerated).
**Fix**: Use a stable `JWT_SECRET_KEY` in `.env`. All existing tokens are invalidated when it changes — users need to log in again (expected behavior).

### `COOKIE_SECURE` causing login to fail on localhost
**Cause**: `COOKIE_SECURE=true` with HTTP (not HTTPS).
**Fix**: Set `COOKIE_SECURE=false` in `.env` for local dev.

### MFA token invalid
**Cause**: System clock drift > 30 seconds.
**Fix**: Sync system clock. TOTP is time-based (30-second windows with 1-step tolerance).

---

## Ingestion Issues

### Document ingestion stuck in `processing`
**Symptom**: IngestionJob stays in `processing` state indefinitely.
**Fix**: Check backend logs for the background task error. Common causes:
- Ollama not responding (embedding step fails)
- Document parser failing (unsupported format or corrupt file)
- Qdrant connection refused

### PDF ingestion produces garbled text
**Cause**: Scanned PDF (image-only, no text layer).
**Fix**: Only text-based PDFs are supported. Image PDFs need OCR pre-processing (not built-in).

---

## Frontend Issues

### All API calls return 500
**Check**: `frontend/.env.local` — `FASTAPI_URL` must point to the running backend.
```
FASTAPI_URL=http://localhost:8000
```
Note: this is a server-only variable. No `NEXT_PUBLIC_` prefix.

### React Query stale data after login/logout
**Cause**: React Query cache persists across auth state changes.
**Fix**: `queryClient.clear()` must be called in both login and logout handlers. See `contexts/auth.tsx`.

### TypeScript build errors after pulling changes
**Fix**: `cd frontend && npm install` — dependencies may have changed.
Then `npx tsc --noEmit` to see all errors at once.

---

## Docker Issues

### Port conflict on startup
**Symptom**: `bind: address already in use` on ports 8000, 5432, 6333, etc.
**Fix**: Find the conflicting process: `lsof -i :<port>`. Stop it or change the docker-compose port mapping for local dev.

### `docker compose ps` shows services as unhealthy
**Fix sequence**:
1. Check logs: `docker compose logs --tail=50 <service>`
2. Restart the failing service: `docker compose restart <service>`
3. If still unhealthy: `docker compose down && docker compose up -d`
