# Deployment tooling

Per-client deployments of the IntoData KB platform under
`<client>.nexus.intodata.nl`.

## Model

One VPS per client in production. Port offsets + per-client env files make it
possible to also run multiple stacks on a single host for dev / staging. The
`deploy-client.sh` contract accepts `--port-offset 0` for the standard
one-VPS-per-client layout and a non-zero offset for shared hosts.

Each client stack is fully isolated:

- Separate Docker Compose project (`-p <client>`), which prefixes every
  container, network, and volume name with the client slug.
- Separate Postgres database (`intodata_kb_<client>`).
- Separate Qdrant collection name (`<client>_documents`).
- Separate JWT secret, Postgres password, MCP ingest key, admin default
  password — all randomized by the deploy script on first run.
- Separate nginx server block → separate TLS cert via certbot.
- Separate MCP API container in the client's Docker Compose project.

Optional IntoData Ops telemetry is configured per client through the MCP env
file. It is disabled by default and sends only operational metadata to the
central Ops API: health, project status, run status, document counts, quality
scores, token/cost totals, and redacted error summaries.

## First-time host setup

Once per VPS:

Make sure nginx, certbot, docker, envsubst, and openssl are installed.

## Deploying a client

```bash
deploy/deploy-client.sh \
    --client-name acme \
    --port-offset 0 \
    --base-domain nexus.intodata.nl \
    --admin-email ops@intodata.nl
```

On first run the script:

1. Generates `/etc/intodata-kb/envs/<client>.env` with fresh randomized secrets
   and per-client port assignments. Ops telemetry variables are included but
   left disabled until `MCP_OPS_TELEMETRY_URL` and
   `MCP_OPS_TELEMETRY_API_KEY` are set.
2. Renders `/etc/nginx/sites-available/nexus-<client>.conf` from
   `deploy/nginx/nexus-client.conf.template` and symlinks it into
   `sites-enabled`.
3. Brings up the Docker stack with
   `docker compose --env-file … -p <client> up -d`.
4. Prints the remaining manual steps (`nginx -t && systemctl reload nginx`,
   `certbot --nginx …`, and health checks).

The script is idempotent — re-running with the same `--client-name` refreshes
the nginx block and re-applies the compose stack without touching the
previously generated secrets.

## What the onboarding wizard expects

Once the stack is up and DNS / TLS are live, visit
`https://<client>.nexus.intodata.nl`. The login page detects an empty users
table and shows the **"create first admin"** form, which calls
`POST /api/v1/auth/bootstrap-admin`. After the admin is created, the standard
7-step wizard runs; ZIP uploads in the Source step stream to
source upload and live logs go through the customer-facing Next.js API
(`/api/source/*`). Public customer domains do not expose `/dashboard/`,
`/mcp/*`, or `/logs/*`; MCP remains reachable on the VPS-local port for the
frontend container and operator SSH tunnels.

## Files

| File | Purpose |
|---|---|
| `RUNBOOK.md` | Canonical EP-001 fresh VPS deployment runbook using Docker Compose, host Ollama, and nginx/certbot. |
| `SMOKE-TEST.md` | End-to-end deployment smoke checklist for health, HTTPS, auth, ingest, streaming, developer-route lockdown, and IP allowlisting. |
| `deploy-client.sh` | Main deploy entry point. |
| `docker-compose.yml` (root) | Compose file, ports parameterized via env vars. |
| `nginx/nexus-client.conf.template` | Rendered per client by envsubst. |
| `mcp-server@.service` | Legacy systemd template unit retained for reference. |
| `mcp-server.service` | Legacy single-tenant systemd unit retained for reference. |
| `.env.production.template` (root) | Reference for every env var a stack needs. |
