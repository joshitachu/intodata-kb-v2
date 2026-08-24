# Duplication audit — 2026-08-24

Evidence-based inventory of places where one concept has two implementations.
Every LOC figure and importer count below was measured, not estimated. Nothing
in this document has been changed yet; it exists so the collapse order can be
chosen deliberately.

## The constraint that shapes every answer

`RAG_pipeline/backend` and `knowledge_base/mcp-server` are **two independently
deployed containers** (ports 8000 and 8002), and the recorded plan is to split
them into separate repos. So for anything duplicated *across* those two, "single
source of truth" cannot mean "delete one" — it means one of:

- **(a) shared internal package** — extract to a versioned library both depend on.
  Real single source, but adds a build/publish step and cross-repo version skew.
- **(b) one owns it, the other calls it over HTTP** — no shared code, but couples
  runtime availability and adds latency.
- **(c) accept the duplication, document the canonical one** — what the codebase
  does today in several places, deliberately.

Picking (a), (b) or (c) per row is the actual decision. Within a single service,
duplication can just be deleted.

## Tier 1 — dead or near-zero risk

| # | Duplication | Evidence | Recommendation |
|---|---|---|---|
| 1 | **`providers/langchain_provider.py`** — 222 LOC | Zero importers outside itself. `RAG_pipeline/CLAUDE.md` states the design is "No LangChain (direct implementation, full control)". | **Delete.** Dead code contradicting a stated architecture decision. |
| 2 | **Second docker-compose** — `RAG_pipeline/docker-compose.yml` redefines `postgres`, `qdrant`, `hindsight`, `backend` | Root `docker-compose.yml` is the documented health boundary (7 services per root `CLAUDE.md`). Four service definitions exist in both files and can drift. | **Collapse to root**, or keep only as an explicitly-labelled standalone-dev override. Verify nothing in CI/scripts targets it first. |
| 3 | **`mission-control` is an undocumented 8th service** | Present in root `docker-compose.yml` (`MC_PORT:-9100`, builds `./mission_control`) but root `CLAUDE.md` documents seven services and EP-001 names seven as the Compose health boundary. | **Doc fix, not code.** Either add it to the documented boundary or retire it. Right now the docs understate the deployment. |

## Tier 2 — real duplication, worth collapsing

| # | Duplication | Evidence | Recommendation |
|---|---|---|---|
| 4 | **Two LLM provider abstractions.** RAG `generation/providers/` = **1,432 LOC** (`registry.py`, `base.py`, `anthropic`, `google`, `openai_compat`, `ollama`, `prompt_cache`). MCP `model_strategies/` = **656 LOC** (`registry.py`, `strategies.py`, `catalogue.py`, `connections.py`). | Both solve the same problem: resolve a model id to a vendor, call it, track usage/cost. Two registries, two catalogues, two retry/fallback policies. This is the strongest candidate for your "single model for everything". | **Option (a) — shared package.** Biggest consistency win: one model catalogue, one fallback policy, one usage ledger. ~2,100 LOC consolidating to materially less. |
| 5 | **Two secret-encryption paths.** `RAG_pipeline/backend/src/runtime/secrets.py` vs `mcp-server/utils/encryption.py` + `connectors/secrets.py` | Both wrap Fernet for at-rest credential storage. Divergence here is a security risk, not just duplication — two places to get key rotation and error handling right. | **Option (a) — shared package.** Small surface, high value. Do this alongside #4. |

## Tier 3 — duplication that was chosen on purpose

Collapsing these is a genuine architecture change, gated behind `/inspect` per
root `CLAUDE.md`. Do not treat them as cleanup.

| # | Duplication | Evidence | Note |
|---|---|---|---|
| 6 | **Two connector stacks / "two Elastics".** RAG `harness/connectors/` = **983 LOC** with its own `catalog.py`, `repository.py`, `service.py`, `capabilities.py`, `http_elastic.py`. MCP `connectors/` = **3,418 LOC** with `catalog.py`, `repository.py`, `service.py`, `broker.py`, `grants.py`, `planner.py`, and providers for `elasticsearch`, `kibana`, `talend_tmc`. | This is the "two Elastics" you named — confirmed. But `knowledge_base/.claude/context.md` already records the decision: the MCP catalogue is **canonical**, and the RAG stack **stays** because it has 6 live importers (`agent_chat_runtime`, `capability_handlers`, `executor`, `packs/runtime`, `routes/harness`, `db/models/harness_connector`) and the chat harness runs its capability system on it. Collapsing means re-pointing the chat capability system at the MCP broker — option (b). |
| 7 | **Two doc-generation paths.** `providers/custom/` pipeline = **29,234 LOC** (canonical 7-phase harness). `providers/opencode_cli.py` = **3,492 LOC**. Plus `claude_cli.py` 577 LOC. | Intentional: OpenCode is a *selectable* generation method, restored deliberately in commit `9b287d4`. Not dead code. The question is whether you still want three selectable engines or one — a product decision, not a refactor. |

## Tier 4 — process duplication

| # | Duplication | Evidence | Recommendation |
|---|---|---|---|
| 8 | **`.codex/skills/` vs `.claude/commands/`** — 11 workflow definitions each | Same workflows, **drifted**: `session-start` is 47 lines in Codex vs 95 in Claude; `session-end` 46 vs 57; `validate` 53 vs 76. Not copies — forks. Only 4 of the 11 `.claude/commands` files are even tracked in git. | Pick one authoring source and generate the other, or drop the unused tool. Currently the two agents follow measurably different rules for the same named workflow. |
| 9 | **`deploy/deploy.sh` vs `deploy/deploy-client.sh`** — 221 vs 287 lines | Only **9 identical lines**, so these are *not* copies. `deploy-client.sh` is canonical per root `CLAUDE.md`. | Confirm `deploy.sh` is legacy and move it to `deploy/legacy/` next to the Caddy scripts, or delete. Low risk, reduces "which script do I run" ambiguity. |

## Recommended order

1. **Tier 1** — pure subtraction, no design decision. Do first.
2. **#5 then #4** — start with secrets (small, security-relevant), which
   establishes the shared-package pattern; then move the provider layer through
   the same seam.
3. **Tier 4** — cheap, and #8 currently causes two agents to follow different
   rules, which is its own source of drift.
4. **Tier 3** — only with an explicit decision and the full pipeline + `/inspect`.

## Health caveat

The baseline is still unvalidated: `457622c` was committed without a test run
(see `.claude/context.md` in both sub-projects). **Establish a green baseline
before starting any of this**, otherwise a refactor and a pre-existing failure
are indistinguishable.
