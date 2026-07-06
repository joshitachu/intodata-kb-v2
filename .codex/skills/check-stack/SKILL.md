---
name: check-stack
description: Verify Docker services and local health endpoints. Use before infra-touching work and after docker-compose changes to report service status and health.
---

# Check Stack

Verify all Docker services and health endpoints are up.

## Commands

Run from the workspace root.

1. Docker status:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || docker-compose ps 2>/dev/null
```

2. Health checks:

```bash
curl -sf http://localhost:8000/health 2>/dev/null && echo "RAG_backend: UP" || echo "RAG_backend: DOWN"
curl -sf http://localhost:8001/api/health 2>/dev/null && echo "MCP_server: UP" || echo "MCP_server: DOWN"
curl -sf http://localhost:6333/healthz 2>/dev/null && echo "Qdrant: UP" || echo "Qdrant: DOWN"
curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); models=[m['name'] for m in d.get('models',[])]; print('Ollama: UP -', models if models else 'no models pulled')" 2>/dev/null || echo "Ollama: DOWN"
docker exec intodata-kb-v2-postgres-1 pg_isready -U admin -d intodata_kb 2>/dev/null && echo "Postgres: UP" || echo "Postgres: DOWN"
```

## Output

Return a table:

```markdown
| Service | Docker Status | Health | Detail |
|---------|---------------|--------|--------|
```

Flag non-running services or DOWN health checks with `ACTION NEEDED`.

Final line:

- All healthy: `STACK OK - all services healthy.`
- Any down: `STACK DEGRADED - fix the flagged services before continuing.`

