# ADR-038: Licences Are Issued Centrally And Pushed, And The Instance Verifies Before Storing

## Status
Accepted 2026-09-28 (plan `.plans/plan.md`, feature `licence-issuing-ui`). Extends ADR-037.

## Context

ADR-037 made the licence a signed file that the operator issues by CLI and
copies to the VPS by hand. The user asked for a superadmin screen that issues
and renews licences instead. The signing key must still never reach a client
stack: anyone who holds it can grant any client any plan.

## Decision

1. **One central Mission Control signs.** It is the only process configured
   with `MC_LICENCE_SIGNING_KEY_FILE` / `MC_LICENCE_SIGNING_KID`. The operator
   runs it as the `mission-control` service of their own Nexus stack, with
   `deploy/operator/docker-compose.issuer.yml`. The headless alternative is
   `deploy/operator/docker-compose.operator.yml`. The Mission Control copy that
   every client stack runs has no key, and its issuing endpoint answers 503
   `issuing_disabled`.
2. **The screen is the dashboard Fleet tab, and it is loopback-only.** The Fleet
   tab calls `/mc/*`. Only the dashboard container's own nginx serves that path
   (bound to `127.0.0.1:8001`). The client edge template has no `/mc/` route,
   so issuing is never exposed on a customer domain. Plan Decision 8 had
   assumed `/dashboard/` gated `/mc/`. The code showed that `/mc/` is not
   routed at the edge at all, and the loopback bind is kept as the boundary on
   purpose.
3. **Delivery is a push over the control channel.** Mission Control sends
   `POST {mcp_url}/ops/control/licence` with the client's
   `MCP_OPS_CONTROL_KEY`. Both sides compare keys in constant time. nginx
   routes `/ops/control/` to mcp-server, optionally behind
   `OPS_CONTROL_ALLOWED_IPS`.
4. **The instance verifies before it stores.** The install endpoint accepts a
   token only if `verify_token` returns VALID or NOT_YET_VALID for the pinned
   keys and this instance's slug. A leaked control key therefore cannot
   install a forged licence or another client's licence; the most it can do
   is replay a genuine one. The write is atomic (temp file in the same
   directory, fsync, `os.replace`) into the directory that is bind-mounted
   read-write into mcp-server only (the backend mounts it read-only). The mtime
   is forced strictly forward, and `FileLicenceSource` keys its cache on inode,
   size and `st_mtime_ns`, so both services see every install. `core.py` (still
   byte-mirrored) gained only the next-slot selection in point 6.
5. **Addressing is pinned by the operator, with no fallback.** `clients.json` must
   carry each client's `control_key` and `mcp_url`. `https://` is required, except on
   loopback, and a URL containing credentials is refused. Pushes and triggers never
   use the URL a heartbeat reports. Heartbeats share one `MC_API_KEY`, so trusting
   them would let anyone holding that key redirect a push, and the client's control
   key with it. For the same reason, "start when the current licence ends" takes the
   date from the issuance ledger, not from telemetry.
6. **A renewal waits in a next slot.** A pushed licence that is NOT_YET_VALID while
   the current one is VALID is written to `licence.next.jwt`, not over the current
   licence. `EntitlementPolicy(next_source=...)` serves it once it starts, and
   mcp-server promotes it to `licence.jwt` at the next install or on startup.
   Overwriting would have turned a gapless renewal into an immediate lockout, and a
   replayed future-dated token into a denial of service. Replacing the current
   licence with a shorter genuine one stays possible on purpose (revoke by replace),
   and it drops a queued renewal that no longer follows without a gap.
7. **An append-only ledger records every issuance** in
   `issued.jsonl`: licence id, client, plan, period, issued_at, issued_by, and
   the push result. It never records the token. The token is returned only on
   explicit request (`return_token`, for a manual install) and the UI never
   caches it.

## Consequences

- The operator issuer refuses to sign while `MC_API_KEY` is the repo default, and the
  dashboard's `/mc/` proxy only answers `Host: localhost` or `127.0.0.1`. Neither
  is a boundary against a hostile container on the same Docker network. For strict
  isolation, run the headless `docker-compose.operator.yml` (its own project and
  network).
- Renewals take one click from the Fleet screen. The scp procedure (RUNBOOK 17b)
  remains as the fallback.
- `issued_by` is self-reported by the dashboard, because Mission Control has no
  user identity of its own. It is a record of who issued, not authentication.
- The central Mission Control host is now the crown jewel: key file 0400, read-only
  mount, and a backup of both the key and the ledger volume.
- A client instance that has not re-run `deploy-client.sh` has no
  `MCP_OPS_CONTROL_KEY`, so a push fails with 503. Mission Control records that
  as a failed push, and the licence can still be installed by hand.
- Option B (multi-tenant SaaS) replaces the ledger and `FileLicenceSource` with
  tenant subscription rows. The issuing function stays the one that
  `billing/` already shares.
