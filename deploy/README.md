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
    --admin-email ops@intodata.nl \
    --initial-admin-email owner@acme.example
```

On first run the script:

1. Generates `/etc/intodata-kb/envs/<client>.env` with fresh randomized secrets,
   the chosen `ADMIN_INITIAL_EMAIL`, and per-client port assignments. Ops
   telemetry variables are included but left disabled until `MCP_OPS_TELEMETRY_URL` and
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
(`/api/source/*`). On public customer domains `/dashboard/`, `/mcp/*`, and
`/logs/*` are gated to superadmins by nginx `auth_request`
(`nginx/nexus-client.conf.template`); MCP also remains reachable on the
VPS-local port for the frontend container and operator SSH tunnels.

## Ansible compatibility notes

The app repo is compatible with source-build Ansible deployments that clone the
repo and render a root `.env` from Vault. The `.env.production.template` file is
the contract to mirror in Ansible.

The current app-side compatibility shims accept older infra templates that use
`SMTP_USER` and `LLM_API_KEY`, but new deployments should prefer
`SMTP_USERNAME` and provider-specific keys such as `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `KIMI_API_KEY`, and `VOYAGE_API_KEY`.
`ADMIN_INITIAL_EMAIL` should be rendered per VPS/customer run rather than baked
into the app repo.

Infra roles should still handle the following outside the app repo:

- Run database migrations after Postgres is healthy and before app health
  checks depend on migrated tables.
- Use `docker compose exec -T postgres ...` instead of hard-coded container
  names such as `nexus-postgres-1`.
- Use `docker compose up -d --build` when deploying freshly cloned source,
  unless the installed Compose version honors `pull_policy: build`.
- Set nginx `client_max_body_size` for frontend upload routes as well as MCP
  routes, because ZIP uploads enter through `/api/source/*`.
- If `QDRANT_API_KEY` is set, include that key in any direct Ansible Qdrant
  health checks or use a TCP readiness check.

## Files

| File | Purpose |
|---|---|
| `RUNBOOK.md` | Canonical EP-001 fresh VPS deployment runbook using Docker Compose, host Ollama, and nginx/certbot. |
| `SMOKE-TEST.md` | End-to-end deployment smoke checklist for health, HTTPS, auth, ingest, streaming, developer-route lockdown, and IP allowlisting. |
| `WEBHOOK-SETUP.md` | GitHub webhook setup for automatic re-scans on push. |
| `deploy-client.sh` | Main deploy entry point. |
| `docker-compose.yml` (root) | Compose file, ports parameterized via env vars. |
| `nginx/nexus-client.conf.template` | Rendered per client by envsubst. |
| `operator/` | Operator-machine compose overlays and client registry example for licence issuing (`RUNBOOK.md` section 17). |
| `monitoring/` | Prometheus scrape config and Grafana dashboard provisioning. |
| `.env.production.template` (root) | Reference for every env var a stack needs. |
