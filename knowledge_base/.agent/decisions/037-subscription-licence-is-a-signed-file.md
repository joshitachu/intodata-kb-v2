# ADR-037: The Subscription Licence Is A Signed File, Verified Offline

## Status
Accepted 2026-09-28 (internal plan, not in repo; feature `subscription-licence`)

## Context

Nexus access becomes subscription-based. A client instance needs a time-boxed
entitlement (plan, features, limits, period). When it lapses, the instance must
become view-only: no chat turns, no generations or scans, and no token spend.
Everything already produced must stay readable. Today every client has its own
VPS (ADR-024). The target is a shared multi-tenant SaaS with self-service
payment. The entitlement checks must survive that move unchanged.

## Decision

1. **The licence is an Ed25519-signed JWT (EdDSA) in a file.** Claims:
   `iss=intodata-nexus`, `sub=<client slug>`, `lid`, `plan`, `features`,
   `limits`, `nbf` (period start), `exp` (period end), `iat`. The header
   carries `kid`. The operator signs it on the operator machine
   (`python -m mission_control.licensing issue`). The private key never
   enters the repo or a client stack.
2. **Verification is offline, against a pinned key ring.** Public keys are
   pinned by `kid` in `licensing/keys.py`. The checks are signature, `iss`,
   `nbf` and `exp` (60s leeway), and `sub == LICENCE_CLIENT_SLUG`, so a licence
   cannot be copied to another client. There is no phone-home and no grace
   period after `exp`.
3. **One file serves both sub-systems.** The host directory
   `/etc/intodata-kb/licences/<client>/` is bind-mounted read-only at
   `/etc/intodata-kb/licence/` into `backend` and `mcp-server`. It is mounted as
   a directory rather than a single file, so an atomic replace (new inode) is
   visible. The verifier re-reads the file on an mtime change, so renewal needs no
   restart. One set of env-file vars (`LICENCE_*`) feeds both services'
   settings, so enforcement, path and subject cannot disagree.
4. **The verifier is mirrored, not shared.** `core.py`, `keys.py` and the
   `golden_vectors.json` fixture are byte-identical between
   `RAG_pipeline/backend/src/licensing/` and
   `knowledge_base/mcp-server/src/mcp_server/licensing/` (fixtures under each
   service's `tests/test_licensing/`). Change both copies together; the parity
   test (`test_licence_mirror_parity.py`, backend suite) enforces
   this. This follows ADR-016 (duplicated RBAC) and the pending polyrepo split.
5. **Refusal is HTTP 402** with
   `detail = {code, message, capability, ...}`, where `code` is one of
   `licence_expired | licence_not_yet_valid | licence_missing | licence_invalid |
   feature_not_in_plan | quota_exhausted`. It is never 401, because only a real
   401 may log a user out.
6. **Enforcement points are structural.** In the backend, a
   `require_capability` dependency on every LLM route runs before any SSE stream
   opens, and a route-inventory test fails when a mutating route has no gate
   and is not on the allowlist. In the mcp-server, the check runs where generate and scan
   runs are *created* (the chokepoint). The generation quota counts top-level
   runs in the period under an advisory lock.
7. **`LICENCE_ENFORCEMENT=enforce|off`.** `off` is for development. With
   `APP_ENV=production` both services refuse to start unless the value is
   `enforce` and the slug is set (same pattern as ADR-025).
8. **Billing is an interface only.** `mission_control/billing/` defines a
   `PaymentProvider` protocol, a working `ManualProvider` (activation = issue a
   licence), and a provider-neutral stub. `POST /ops/billing/webhook` returns
   501. The choice of payment provider (Mollie or Stripe) is open.

## Rationale

- An offline signed file keeps a client working when Mission Control is down.
  It needs no new cross-service table (backend and mcp-server have separate
  alembic chains), and it is the smallest thing that can be verified.
- The claims have the same shape a per-tenant subscription row will carry.
  For multi-tenant SaaS, only the `LicenceSource` strategy changes (file ->
  subscription record). `EntitlementPolicy` and every enforcement point stay.

## Consequences

- **Key custody is the main risk.** Anyone holding the signing key can mint a
  licence for any client. `deploy-client.sh` aborts if a `*SIGNING_KEY*`
  variable or a PEM private key appears in a client env file, the connectors
  env file or the licence directory. `mission_control` ships in client stacks
  but never receives the key.
- **Rotation is add-first.** Pin the new `kid` in both mirrors, deploy
  everywhere, then issue with it. Remove an old `kid` only after its licences
  have expired, or at once if the key is compromised (RUNBOOK section 17d).
- **Existing clients need a licence installed before the upgrade.** The
  production startup check makes enforcement mandatory.
- **Accepted risk:** a customer with root on their own VPS could tamper with the
  clock or the files. We host the instances, so this is out of scope for per-instance
  licensing.
- The plan and limits live in the operator-side `plans.json`. Instances only read
  claims, so a new plan needs no client redeploy. A new *feature* that
  needs enforcing does need a redeploy.

## Source
Internal plan, not in repo (subscription-licence), 2026-09-28. Runbook:
`deploy/RUNBOOK.md` section 17. Smoke test: `deploy/SMOKE-TEST.md` section 10.
