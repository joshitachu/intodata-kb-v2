# Unified Agent Chat Retirement and Deployment Runbook

> Current status: `implemented_not_deployed`; `retirement_gate_unmet`.
> Migration 069 has not been applied and the services have not been restarted
> or deployed for Slice 8.

The conversation APIs now have one application path:

```text
authenticated message
  -> execute_production_agent_chat_turn
  -> bounded agent loop
  -> policy-validated registered handler
  -> typed observation
  -> agent_response_v1
  -> persisted assistant message
  -> non-stream or SSE projection
```

There is no route mode, cohort selector, shadow planner, legacy fallback,
keyword gate, separate conversational-RAG route, or runtime keyword router.
Rollback must therefore use a reviewed application/database recovery procedure;
there is no configuration switch that reactivates legacy chat.

## Deployment authority gate

Do not deploy Slice 8 or apply migration 069 until an operator attaches and
signs a completed copy of
[`agent-chat-retirement-evidence-template.md`](./agent-chat-retirement-evidence-template.md).
The blank template is not evidence and must fail review.

The signed record must prove, from the deployed Slice 7 full-agent release:

- at least 168 consecutive hours and 1,000 authenticated agent turns;
- conversation-163/166-shaped replay, paraphrase, security, restart, backend,
  and frontend suites passed;
- repeat rate at most 1%;
- unexpected clarification rate at most 3%;
- denial correctness exactly 100%;
- quality at least 95%;
- zero stale workflow cards, duplicate effects, unauthorized effects,
  cross-scope evidence, shadow side effects, or secret leaks;
- approved p95 latency and cost bounds;
- a protected database backup and an application/database rollback drill.

Local tests are implementation evidence only. They do not replace deployed
observation. The reviewer must reject missing timestamps, sample counts,
queries, suite artifacts, incident links, backup identifiers, signatures, or
unchecked acceptance boxes.

## Offline implementation validation

Run from the repository root and stop on any failure:

```bash
cd RAG_pipeline/backend
UV_CACHE_DIR=/tmp/uv-cache uv run --extra dev pytest -q

cd ../frontend
npx tsc --noEmit
npm test
npm run build

cd ../..
git diff --check
```

Also confirm the old chat surfaces have no production callers:

```bash
rg -n "harness\\.(chat|planner|chat_cutover)|generation\\.(intent_classifier|conversational_rag)|configured_chat_route_selector|CapabilityExecutionMode|legacy_dispatch_ids|registered_handler_ids|manifest_only_ids|_dispatch_legacy|is_pack_builder_request|runtime\\.tools\\.query_evidence|nexus\\.workflow\\.operational_investigation|external\\.connector\\.read_evidence" \
  RAG_pipeline/backend/src RAG_pipeline/frontend
```

Expected result: no matches. Regex used here is an operator source audit, not
runtime intent routing.

## Remaining configuration

Only bounded-loop limits remain:

| Variable | Default | Meaning |
|---|---:|---|
| `AGENT_CHAT_MAX_ITERATIONS` | `6` | Maximum model-loop iterations |
| `AGENT_CHAT_MAX_TOOL_CALLS` | `4` | Maximum registered capability calls |
| `AGENT_CHAT_DEADLINE_SECONDS` | `30` | Per-turn deadline |
| `AGENT_CHAT_MAX_COST_USD` | `0.25` | Per-turn cost ceiling |

Do not add a route, canary, shadow, keyword, mode, or fallback setting.

## Backup and migration 069

After the retirement evidence is signed, create and protect a database backup
before applying migration 069:

```bash
docker compose exec -T postgres sh -c \
  'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > /tmp/nexus-agent-chat-pre-069.dump
wc -c /tmp/nexus-agent-chat-pre-069.dump
docker compose exec backend alembic current
docker compose exec backend alembic heads
```

Move the non-empty dump to the environment's protected backup store and record
its identifier in the retirement evidence. Do not print credentials.

Migration `069_retire_legacy_chat_paths` fails closed before dropping
`harness_conversation_continuations`. Every active envelope must be valid and
must match revision-067 Conversation State objective, requested inputs, last
message, and any referenced task/workflow/draft authority.

Apply only after a reviewed backup:

```bash
docker compose exec backend alembic upgrade 069_retire_legacy_chat_paths
docker compose exec backend alembic current
docker compose exec -T postgres sh -c \
  'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select to_regclass(\$\$public.harness_conversation_continuations\$\$);"'
```

Expected revision: `069_retire_legacy_chat_paths`. Expected table query:
empty/null.

If migration 069 reports malformed state, parity mismatch, or missing authority,
stop. Do not delete or edit rows to force it through. Reconcile the source
authority under the Slice 7 image, repeat the backup, rerun the parity review,
and obtain a new sign-off.

## Deployment

Only after the signed gate and successful migration:

```bash
docker compose up -d --build backend frontend
docker compose ps backend frontend
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:3000/ >/dev/null
```

Run authenticated smoke checks for both transports and verify:

- exactly one user and one assistant row per `client_turn_id`;
- non-stream JSON and SSE `agent_response` contain the same typed response;
- `agent_final` contains metadata only;
- Pack answers expose the server-issued `/pack?draft=<id>` artifact link;
- workflow cancellation/status cards reconcile to terminal child state;
- no `workspace`, `token`, `final_answer`, runtime-tool, legacy activity,
  or streaming pending-action event is emitted.

## Observation after deployment

Monitor:

- `agent_chat_turns_total{transport,outcome}`;
- `agent_chat_guard_total{guard,result}`;
- `agent_chat_latency_seconds{transport}`;
- `agent_chat_cost_usd`;
- `agent_chat_tool_calls`;
- bounded rows in `harness_agent_turn_audits`.

Audit rows may retain historical fixed route/cohort fields from revision 068.
New code records the single path as `agent`; those columns are historical
schema compatibility, not a router.

Escalate on any repeated answer, stale workflow projection, cross-scope fact,
secret exposure, duplicate side effect, unexpected clarification loop, missing
typed response, or handler/definition mismatch.

## Rollback

A Slice 7 application image must not be redeployed after migration 069 unless
it is confirmed to remain on its full-agent route. Never use rollback to
reactivate legacy chat.

Migration downgrade recreates only an empty legacy continuation table; it
cannot recreate deleted envelopes. Conversation State, Workflow Ledger,
receipts, interrupts, and audit rows remain authoritative.

Rollback procedure:

1. stop promotion and capture logs/metrics without secrets;
2. restore the protected pre-069 database backup when continuation data is
   required;
3. deploy the reviewed full-agent Slice 7 image, never a legacy-route image;
4. run authenticated replay, security, restart, backend, and frontend suites;
5. record the incident, recovery timestamps, image digests, database revision,
   and reviewer sign-off.

No local evaluator or deleted Slice 7 module is required to enforce this
runbook: deployment authority is the signed evidence record and human review.
