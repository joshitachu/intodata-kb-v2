Verify all Docker services and health endpoints are up. Run before any infra-touching
work and after docker-compose changes.

### Steps

#### 1. Docker service status

```bash
docker compose -f /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/docker-compose.yml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || docker-compose -f /Users/joshuakabel/Documents/Source/intodata/intodata-kb-v2/docker-compose.yml ps 2>/dev/null
```

#### 2. Health endpoint checks

Run all of these — capture output and exit code for each:

```bash
curl -sf http://localhost:8000/health 2>/dev/null && echo "RAG_backend: UP" || echo "RAG_backend: DOWN"
```

```bash
curl -sf http://localhost:8001/api/health 2>/dev/null && echo "MCP_server: UP" || echo "MCP_server: DOWN"
```

```bash
curl -sf http://localhost:6333/healthz 2>/dev/null && echo "Qdrant: UP" || echo "Qdrant: DOWN"
```

```bash
curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); models=[m['name'] for m in d.get('models',[])]; print('Ollama: UP —', models if models else 'no models pulled')" 2>/dev/null || echo "Ollama: DOWN"
```

```bash
docker exec intodata-kb-v2-postgres-1 pg_isready -U admin -d intodata_kb 2>/dev/null && echo "Postgres: UP" || echo "Postgres: DOWN"
```

#### 3. Output a service table

```
| Service       | Docker Status | Health      | Detail                       |
|---------------|---------------|-------------|------------------------------|
| postgres      | running       | UP          |                              |
| qdrant        | running       | UP          |                              |
| backend       | running       | UP          | {"status": "ok"}             |
| frontend      | running       | —           | no health endpoint           |
| mcp-server    | running       | UP          |                              |
| dashboard     | running       | —           | no health endpoint           |
| ollama        | host-level    | UP          | gemma3:12b, nomic-embed-text |
| hindsight     | running       | —           |                              |
```

Rules:
- Flag any service with Docker status not `running` or health check `DOWN` with **ACTION NEEDED**
- If Ollama is missing models, list which ones need `ollama pull`
- If postgres is down, note that alembic migrations and all DB-dependent services will fail

Final line:
- All UP → `STACK OK — all services healthy.`
- Any DOWN → `STACK DEGRADED — fix the flagged services before continuing.`
