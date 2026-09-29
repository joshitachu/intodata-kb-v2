# ADR-039: Fleet Clients Are Registered Through Mission Control, And Each Heartbeats With Its Own Key

## Status
Accepted 2026-09-29 (plan step waived by the user; feature `fleet-client-registry`). Extends ADR-038.

## Context

ADR-038 pinned each client's `mcp_url` and `control_key` in a hand-edited,
read-only `clients.json`. Adding a client meant editing a file on the operator
host and remounting it. Heartbeats had a second problem. Every instance
authenticated with the one global `MC_API_KEY`, so whoever held it could write
any slug's snapshot, and that key sat in every client's env file. Remote
heartbeats also had no route. The old path went through the dashboard's `/mc/`
proxy, which a Host guard now blocks, correctly.

## Decision

1. **Mission Control owns a writable client registry.**
   `/ops/fleet/clients` (GET, POST, PATCH, DELETE, and
   `POST .../{slug}/telemetry-key`) sits behind `MC_API_KEY`, like every other
   operator route. The dashboard Fleet screen calls it through the loopback
   `/mc/` proxy. Routes are thin. Storage is a `FleetRegistry` repository with
   one JSON implementation (`JsonFleetRegistry`, `MC_FLEET_REGISTRY`, default
   `fleet_clients.json` next to the snapshots directory). The operator compose
   files put it on a named volume.
2. **Writes are atomic and serialised across workers.** Each change is a
   read-modify-write under a thread lock plus `flock` on a sidecar `.lock`
   file, then a temp file in the same directory, fsync, and `os.replace`. The
   file mode is 0600. A registry that exists but cannot be parsed refuses
   writes (503 `registry_unavailable`) instead of being replaced by an empty
   one.
3. **Legacy `clients.json` is still read, behind the registry.**
   `FleetDirectory` merges both sources, and a registry entry wins on the same
   slug. File entries are listed as `source: "file"` and are immutable
   through the API (409 `managed_by_file`). Push and trigger resolution
   (`InstanceControlClient.resolve`) goes through the same directory. The
   ADR-038 addressing rules are unchanged: only the pinned `mcp_url` is used,
   and one validator (`validate_mcp_url`: https unless loopback, no userinfo,
   no query or fragment) now serves both sources.
4. **Each client heartbeats with its own key, and that key only authorises
   its own slug.** Registering a client, or rotating its key, returns a new
   `secrets.token_urlsafe(32)` key exactly once, together with the three env
   lines for the client server. Only `sha256(key)` is stored, and the
   comparison is `hmac.compare_digest`. A plain SHA-256 is enough because the
   key has 256 bits of entropy, so there is nothing for a slow, salted
   password hash to protect against. A key presented for any other slug is
   401. Rotation replaces the hash, so the old key fails immediately.
5. **The shared key is legacy-only, and narrowly.** `POST
   /ops/clients/{slug}/telemetry` accepts `MC_API_KEY` only for instances
   that predate per-client keys, and only when every one of these holds
   (`auth.shared_key_refusal`); otherwise the answer is the same 401 as a
   wrong key:
   - `MC_ALLOW_SHARED_HEARTBEAT` is true. It defaults to true so legacy
     instances keep reporting; set it false once none is left, and only
     per-client keys are accepted.
   - `MC_API_KEY` is not the repo default (`REPO_DEFAULT_API_KEY`). That value
     is public, and the heartbeat route is reachable from the internet
     through the ingress in 7. Startup logs a WARNING whenever Mission
     Control runs on it.
   - The slug has no registered telemetry key. Registering a client ends
     shared-key heartbeats for that slug at once, so a holder of the shared
     key cannot overwrite a re-keyed client's snapshot.
   - The registry is readable. When it is corrupt Mission Control cannot tell
     which slugs have their own key, so it fails closed.
   The slug is validated (422 `invalid_slug`) before any lookup, key check
   or log line. Accepted and refused shared-key heartbeats are each logged
   once per slug per process, in a set capped at 1024 slugs. The list shows
   unkeyed clients as `heartbeat_auth: "shared_key_only"`, and the snapshot
   records `_mc_auth`.
6. **The control key is stored in plaintext, in the 0600 registry file.**
   Mission Control must send it as a bearer on every push, so it cannot be
   hashed. It never appears in a response (only `has_control_key`), a log
   line or a `repr`. A client may be registered without one, since the server
   may not be redeployed yet. Push and triggers then answer 422
   `no_control_key`.
7. **The public heartbeat ingress is exactly one route.** On the operator
   host, nginx exposes only `POST /ops/clients/<slug>/telemetry`. The raw URI
   must match the slug pattern exactly (400 otherwise, and other methods are
   403), and the body is capped at 512k. No other `/ops` or `/mc` path is
   published. The block lives in `deploy/operator/nginx-fleet-telemetry.conf`.
   `deploy-client.sh --fleet-host` renders that same file into the client
   template (`FLEET_TELEMETRY_INGRESS`, empty on every client stack), and a
   host not deployed with the script includes it by hand; `--fleet-host`
   requires an explicit `--fleet-mc-port` (the central Mission Control's
   published port), since this stack's own port is unrelated. `MC_PUBLIC_URL`
   tells the Fleet screen which base URL to hand out.
8. **The receiver does not rely on nginx for its limits.** Loopback callers
   bypass the ingress, so the app caps the heartbeat body at 512 KiB itself
   (413 `payload_too_large`; a declared `Content-Length` is refused up front,
   otherwise the stream is read chunk by chunk) and reads it only after auth.
   An undecodable or too-deeply nested body (`RecursionError`, in parsing or
   in writing the snapshot) is 422 `invalid_payload`, never a 500.
9. **Registry reads are cached.** Every heartbeat with an unknown bearer looks
   the slug up, so `JsonFleetRegistry` caches the parsed file by (path,
   mtime, size, inode) and drops the cache on its own writes. `os.replace`
   gives each write a new inode, so a write by another worker is seen too.
   A corrupt file is cached as corrupt, so it is logged once per version.
   Mutations never use the cache; they re-read under the lock.
   `GET /ops/fleet/clients` reports `registry_unavailable: true` when the
   registry is unreadable, and its `clients` then hold only file entries.

## Consequences

- Adding a client is one form in the Fleet screen plus one redeploy of the
  client with `--ops-telemetry-url` and `--ops-telemetry-key-file` (or the
  `OPS_TELEMETRY_KEY` environment variable, or three pasted env lines).
  `--ops-telemetry-key <key>` still works but warns, because the key is then
  in argv, `ps`, shell history and sudo's auth log. RUNBOOK section 18 has
  the steps.
- A leaked client telemetry key can forge only that client's snapshot. The
  shared `MC_API_KEY` can still forge the snapshot of an UNKEYED slug while
  `MC_ALLOW_SHARED_HEARTBEAT` is true, which is why the list surfaces
  `shared_key_only`. It cannot touch a keyed slug, and on the repo default
  key it is refused outright.
- Registering a legacy shared-key instance stops its heartbeats until its new
  key is applied on the VPS. RUNBOOK 18d orders the steps accordingly.
- The registry volume is now as sensitive as the licence ledger (it holds
  every control key) and must be backed up with it.
- A Mission Control run from the plain root `docker-compose.yml`, without an
  operator override, keeps the registry at `/app/fleet_clients.json` inside
  the container, and it is lost on recreate. Only the central, operator-side
  Mission Control is meant to register clients. Its compose files mount the
  volume.
- Heartbeats now reach Mission Control through nginx, so `_mc_source_ip` in
  a snapshot is the proxy's address unless uvicorn is configured to trust
  forwarded headers. It was informational before and is still not used for
  any decision.
- Option B (multi-tenant SaaS) replaces `JsonFleetRegistry` with a tenant
  table behind the same `FleetRegistry` interface.
