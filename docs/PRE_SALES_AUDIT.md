# Pre-Sales Reality Audit & Build Plan

> **Date**: 2026-04-20
> **Purpose**: Before selling, close the gap between what the product promises and what it actually does. This is both the diagnosis and the day-by-day build plan.
> **How to use**: Work top-to-bottom on the Day Plan. Check boxes as you go. When in doubt, consult the gap detail above the plan.

---

## TL;DR Verdict

| Area | Status | Sellable today? |
|---|---|---|
| **Plugin architecture** (Talend/Workato/Generic/Lobster) | Works, clean boundary | ✅ Yes, with honest messaging |
| **Deep Dive** (Claude CLI subprocess) | Works end-to-end | ⚠️ Only if customer VPS has Claude CLI installed |
| **RBAC on every Qdrant query** | Works uniformly | ✅ Yes |
| **Hindsight memory capture** | Load-bearing, reads + writes | ✅ Yes (input side) |
| **Doc generation pipeline** | Works but not atomic | ⚠️ Data-loss risk on crash |
| **GitHub "push → docs auto-update"** | Only webhook → scan, no auto-generate, no App/OAuth, no diffing | ❌ No |
| **Thumbs up/down feedback** | UI only, zero persistence | ❌ Visible lie |
| **Benchmarking "dashboard"** | One-shot eval runner vs 10-Q corpus | ❌ Not what the UI implies |
| **Intent / business-context capture** | Not captured anywhere | ❌ Missing entirely |
| **Chat → docs learning loop** | Hindsight captures but nothing writes back | ❌ Missing the write arrow |
| **Onboarding "2 min" copy** | Aspirational, some stages are cosmetic | ⚠️ Needs alignment |
| **Multi-model / A/B testing across AI models** | CLI-only eval, no UI trigger, no query-time switching | ❌ No (misleading if pitched as a product feature) |
| **Doc-gen "AI picks the best model for your code"** | Two Claude models hardcoded in config, zero per-project adaptation | ❌ No (pitch is aspirational) |
| **Template/Profile CMS for doc customization** | 7 dashboard components + 4 DB tables for what should be "paste a GitHub URL" | ⚠️ Overengineered; replace with plug-in prompt repo |

---

## Findings — 14 Gaps Ranked by Customer Impact

### 🔴 GAP 1 — "Connect GitHub, we auto-update docs on every push"
**UI promise**: `RAG_pipeline/frontend/components/onboarding/steps/SourceUploadStep.tsx:106-110`, product tour copy, marketing.
**Reality**:
- Webhook endpoint exists at `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py:19-63`, but:
  - No GitHub App. No OAuth. User must manually paste webhook URL into GitHub.
  - Webhook handler only `git pull`s and triggers a **scan** — doc regeneration is still a manual button-press.
  - Full re-scan on every push. `knowledge_base/intodoc/src/intodoc/scanner/git_analyzer.py` reads commits but the scan re-parses everything regardless.
  - No HMAC signature validation — any POST is accepted.
- `docs/ROADMAP.md:471` marks "auto-assessment on git push" as Q3 2026 internally, but the wizard sells it as available today.

### 🔴 GAP 2 — Doc generation swap is not atomic
**Reality** (`knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:339-347`):
```python
if docs_dir.exists():
    shutil.rmtree(docs_dir, ignore_errors=True)   # step 1: delete old
staging_dir.rename(docs_dir)                       # step 2: rename new
```
A crash / network FS hiccup / Windows file lock between the two steps leaves `docs/` missing or half-renamed. Failed runs preserve `docs_staging/` for "resume" but **nothing ever cleans them up** → disk fills.

### 🔴 GAP 9 — Thumbs up/down is UI-only, zero persistence
**Reality** (`RAG_pipeline/frontend/components/MessageBubble.tsx:139-166`):
```tsx
onClick={() => setFeedback(feedback === "up" ? null : "up")}
```
Only flips a local React state variable. No `fetch()`, no API, no DB row. Refresh → gone. **Every click is a promise the product doesn't keep.** Also: this is the prerequisite for Gap 11 (chat→docs loop) — feedback must be persisted before it can drive anything downstream.

### 🔴 GAP 10 — No intent / business-context capture
`UserOnboarding` (`RAG_pipeline/backend/src/db/models/onboarding.py:49-50`) captures **platforms** and **comm tools** as checkboxes. Never captures:
- Why does this integration exist? (business purpose)
- Who owns it? (stakeholder)
- What problem does it solve? (success criteria)
- What is your goal for using IntoData? (onboard new hires / audit / migration / …)

The product is called "Integration **Intelligence**" but it has no field for the *intent* behind any integration. Generated docs answer "what does this code do" — never "why does this exist, and is it still serving its purpose." That's the difference between code-search and a knowledge base.

### 🔴 GAP 11 — Chat never flows back into docs (Hindsight write-back loop)
Good news first:
- `RAG_pipeline/backend/src/services/hindsight_client.py` **already** captures every Q&A into 4 memory banks (user, project, **quality**, **corrections**) via `retain_interaction()` / `retain_correction()`.
- `is_likely_correction()` (lines 622-630) detects "no that's wrong" / "actually..." and stores as a correction.
- Hindsight recall is wired into `_build_memory_context()` (`conversational_rag.py:97-100`) — corrections already influence next-turn answers.

What's missing:
- **Nothing writes back to `docs/`.** Quality bank knows "12× zero-result on topic X"; no worker turns that into a doc-gen task.
- **No "accepted answer" promotion** into canonical doc snippets.
- **Corrections live only in Hindsight** — never in the markdown a new engineer would read.
- Admin Hindsight page (`RAG_pipeline/frontend/app/admin/hindsight/page.tsx`) is read-only.

This is the differentiator the founder keeps describing. Closer than it looks — the capture side is done.

### 🟢 GAP 3 — Scan completeness is invisible ✅ FIXED (Day 3, 2026-04-20)
**Original**: `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` catches `ET.ParseError` and logs to stderr, then continues. A corrupted `.item` file silently disappears from the scan. No `warnings` field on `ScanResult`, no delta between runs ("last scan: 500 jobs, now: 495 — is that right?"). A customer pushes broken code and gets silently incomplete docs, never knowing.

**Fix shipped**: Added a `ScanWarning` Pydantic model and a `warnings: list[ScanWarning]` field on `ScanResult`. Threaded warning capture through all four parsers (Talend — 4 swallow sites; Workato — 2; Lobster — 1; Generic — 2, previously fully silent). `GET /mcp/status/{project_id}` now returns `warnings`, `warnings_count`, `previous_job_count`, and `job_delta` (derived from the last two completed `ScanRun` rows — no DB migration). Dashboard's Scan History renders a yellow `! N` chip on the latest completed run and a signed green/red delta next to the Components column. 12 new `tmp_path` pytest tests green; 274 existing parser tests unchanged. See PROGRESS_LOG.md entry for 2026-04-20.

### 🟡 GAP 4 — Benchmarking "dashboard" implies continuous monitoring it doesn't do
`RAG_pipeline/frontend/app/admin/benchmark/page.tsx` shows "Best Precision@5", "Best Relevance", "Avg Latency" cards — looks like a live monitoring surface. Reality: `RAG_pipeline/backend/benchmark/runner.py` is a one-shot CLI against a hardcoded 10-question corpus (`benchmark/corpus/default.json`). No CI hook, no regression alerting, no multi-run A/B, no custom rubric. It's an eval framework, not a dashboard.

### 🟡 GAP 5 — Deep Dive is 100% coupled to Claude CLI subprocess
`knowledge_base/mcp-server/src/mcp_server/services/deep_dive_service.py:105-114` spawns `claude -p …`. Works today on dev boxes, but:
- Customer VPS without Claude CLI → feature is dead.
- No fallback to Anthropic SDK or LangGraph.
- CLI version-drift can break streaming parsing any time.
- Founder's instinct is right: this doesn't need Claude CLI to work. A native SDK path kills a whole class of support tickets.

### 🟡 GAP 12 — Query intent classification is coarse
`RAG_pipeline/backend/src/generation/intent_classifier.py:57-76` classifies into ~10 actions but only to **route retrieval** (vector vs Postgres vs skip). It doesn't distinguish task intent (troubleshooting / onboarding / compliance audit / architecture review) and doesn't adapt prompt tone/detail. An auditor and a new hire asking the same question get the same answer.

### 🟡 GAP 13 — Notifications framework exists, only 1 trigger wired
`RAG_pipeline/backend/src/db/models/onboarding.py:77-94` defines a `Notification` table with full CRUD routes. Only **one** code path ever creates a notification (onboarding completion). Nothing fires for: doc ingestion complete, scan complete, RAG query failure, admin action, webhook-triggered regen. The bell icon in the header will be empty for 99% of a customer's usage.

### 🟡 GAP 14 — Long conversations lose context silently
`conversational_rag.py:51` hard-caps history at ~20 messages. Turn 25 of a debugging session has less context than turn 10. A `conversation_summary` action exists in the intent classifier but only fires when the user *explicitly* asks for a summary — no auto-summarization feeding the next turn's system prompt.

### 🟢 GAP 6 — Plugin mechanism is decent, but oversold
`knowledge_base/intodoc/src/intodoc/platforms/base.py:14-42` defines a clean 4-method `PlatformPlugin` ABC; Talend/Workato/Generic/Lobster all implement it. But:
- `ScanResult` (`intodoc/models.py:154`) leaks Talend-isms (`tmap_data`, `trunjob_refs`).
- "Add a platform in 5 minutes" is false — the parser work for a structured format (XML/JSON) is the real cost, not the plugin boundary.
- Prompts are inline `.txt` files, not versioned or tested.

### 🟢 GAP 7 — Onboarding timing copy
`WelcomeStep.tsx:60` claims "2 minutes". `ProvisioningStep.tsx:141` claims "under a minute". `ProvisioningStep.tsx:26-31` has 4 stages ("Creating your workspace / Configuring knowledge base / Setting up integrations / Preparing your environment") that may or may not correspond to real backend work — needs verification and either wiring or trimming.

### 🟢 GAP 8 — "Coming soon, we'll enable them automatically when they ship"
`PlatformSelectStep.tsx:71`, `CommToolSelectStep.tsx:59-62`. No worker reads `wizard_event` rows and flips platforms on when ready. Current wording is aspirational at best.

### 🟡 GAP 15 — "A/B testing / multi-model comparison" is CLI-only and not surfaced
The pitch "we test multiple AI models and pick what works best for you" cannot be honestly made today. Evidence:
- `RAG_pipeline/backend/benchmark/runner.py` runs against one model per invocation; head-to-head requires the admin to manually run it twice and eyeball the frontend's numeric cards.
- `BenchmarkRun.llm_model` (`RAG_pipeline/backend/src/db/models/benchmark.py:23`) stores one model per run — data supports multi-model, the UI does not.
- `RAG_pipeline/frontend/app/admin/benchmark/page.tsx` is display-only: no trigger, no model selector, no side-by-side, no "run both" button.
- Query-time: `OLLAMA_LLM_MODEL` env var is a single global. No per-query / per-user / per-project model selection. `_ollama_generate()` in `llm_client.py:160` always uses `settings.ollama_llm_model`. No fallback / routing / ensembling.
- Dashboard cards ("Best Precision@5 / Best Relevance / Best Latency") imply a portfolio comparison; the data shown is just the max over manually-created runs.

Impact: remove the claim from sales/marketing material, **or** build a real "Run benchmark now" UI button + model picker (roughly a Week-3 day).

### 🟡 GAP 16 — Doc generation model choice is hardcoded, quality is unmeasured
Pitch: "our AI picks the right model for your codebase and continuously improves your docs." Reality:
- `knowledge_base/mcp-server/src/mcp_server/config.py:52-53` hardcodes `doc_generation_model = "claude-sonnet-4-6"` and `doc_generation_write_model = "claude-opus-4-6"`. Same two models, every project, every run. No per-project / per-platform override, no A/B between models, no "try this with Gemma" knob.
- The benchmark corpus measures **RAG retrieval quality** (precision@5, relevance) — not **generated doc quality**. Doc output has never been scored, regressed, or compared between models. A customer has no way to see "does this model actually produce good docs for my Talend codebase?"
- Combined with Gap 5 (Claude CLI coupling): "AI-written documentation" is really "Claude-written documentation by way of an Anthropic CLI subprocess." Anthropic's IP produces the value; IntoData's IP orchestrates it. Worth being honest about in pitch.
- Gap 11 covers the missing chat→docs write-back loop; this is the separate concern of doc-gen quality measurement and model-choice transparency.

Impact: downgrade the pitch to "generates docs once using Anthropic's Claude" (accurate), **or** add a doc-gen benchmark + model picker (Week 4+).

### 🟡 GAP 17 — Template/Profile CMS is overengineered; replace with a plug-in prompt-repo URL
**Current state** (the "feels overwhelming" finding):
- `knowledge_base/mcp-server/src/mcp_server/db/template_models.py` defines 4 tables: `templates` (slot), `template_versions` (immutable content), `template_profiles` (named set of assignments), `profile_assignments` (template→version map).
- `knowledge_base/mcp-server/src/mcp_server/api/routes/templates.py` + `services/template_service.py` expose full CRUD + versioning + cloning + diff.
- Dashboard carries **7** components to drive it: `TemplateEditor`, `TemplateCatalogue`, `ProfileManager`, `ProfileDetail`, `NewTemplateModal`, `VersionTimeline`, `ABComparison`, plus `DocumentCatalogue`.
- Tweaking one prompt = navigate to template → create new version → open profile → update assignment → save. Four steps for what should be "paste a URL".

**Better model** — a doc-gen **prompt set is a Git repo**, layout mirroring the bundled `knowledge_base/intodoc/src/intodoc/platforms/*/prompts/` directories. `Project` gets `prompts_repo_url TEXT NULL` + `prompts_repo_ref TEXT DEFAULT 'main'`. Blank URL = bundled defaults (today's behavior). On doc generation: if URL set, `git clone --depth 1 -b <ref>` into `data/prompt_repos/<sha>/`, point the prompt loader at that dir, `git pull` on subsequent runs. **Per-file fallback**: missing prompt in the customer's repo → use the bundled default. Customers override only what they care about. Publish one reference repo on GitHub (e.g. `intodata/docgen-prompts`) as the starter — customers fork → tweak → paste URL. That is the entire "swap / branch / share / version" story, handled by real Git. v1 scope: **public repos only**.

**What gets deleted** after a 5-minute audit (`SELECT COUNT(*)` on the 4 template tables + list of profile names/creators):
- DB tables: `templates`, `template_versions`, `template_profiles`, `profile_assignments` — drop in the same migration that adds `prompts_repo_url`. If any load-bearing content exists, export first to the starter repo, then drop.
- Server files: `api/routes/templates.py`, `services/template_service.py`, `db/template_models.py`, template schemas in `schemas.py`.
- Dashboard files: all 7 components above + `hooks/useTemplates.ts` + template types in `types.ts`.
- The `template_profile_id` param on `/generate` (`projects.py:294`) — gone with its storage.

**Pro for sales**: "GitHub of doc instructions" is a concrete, honest answer to Gap 16's "AI adapts to your codebase" — the prompts literally are the customer's, versioned in their repo, forkable and auditable. **Con**: customers without git-fluent users lose an in-app editor; mitigation is defaults-work-fine or `github.com/.../edit/main/prompts/pass10.txt`.

---

## What Actually Works (don't rewrite these)

- **RBAC**: `RAG_pipeline/backend/src/retrieval/search.py:28-100` filters every Qdrant path — hybrid, semantic, rerank. Admin bypass is explicit. Metadata retriever applies project-level RBAC too.
- **Hindsight integration**: 4 recall sites, 2 retain sites, all load-bearing. Not decorative.
- **Conversation persistence**: `Conversation` + `ConversationMessage` tables; list + retrieve endpoints work; auto-titled from first query. (Only missing FTS.)
- **Plugin boundary**: Clean ABC, 4 working implementations, shared prompt fallback chain.
- **Deep Dive plumbing**: Subprocess + MCP + streaming SSE end-to-end works (caveat: CLI dependency — see Gap 5).
- **Token/cost tracking**: Every LLM call writes `QueryUsage` with model, provider, input/output tokens, embedding tokens, latency, cost.
- **Doc-gen staging → docs pattern**: Right idea; just needs atomicity fix (Gap 2).

---

## Day-by-Day Build Plan

> Each checkbox is sized for ~2-4 hours of focused work. Days assume one engineer. Double timings for first-time work in an area.

### WEEK 1 — Stop the bleeding (data safety + visible lies)

**Day 1 — Persist thumbs feedback (Gap 9) ✅ DONE 2026-04-20**
- [x] Create Alembic migration: `RAG_pipeline/backend/alembic/versions/016_add_feedback.py` adding `feedback` table with columns: `id`, `user_id`, `conversation_id`, `message_id`, `kind` ('up','down','correction','cleared'), `text TEXT NULL`, `citations_snapshot JSONB NULL`, `created_at`, `updated_at`
- [x] Add `Feedback` model at `RAG_pipeline/backend/src/db/models/feedback.py`; register in `__init__.py`
- [x] Add `POST /api/v1/feedback` + `GET /api/v1/feedback/mine` routes at `RAG_pipeline/backend/src/api/routes/feedback.py`; mount in `main.py`; Next.js proxy at `frontend/app/api/feedback/route.ts`; client helper at `frontend/lib/feedback.ts`
- [x] Wire `MessageBubble.tsx:139-166` — replaced local-state toggle with async handler + optimistic UI + rollback; threaded `conversationId` through `MessageList` + `ChatWindow`
- [x] Manual test: verified 5 rows landed with correct `user_id`, `message_id`, `kind`
- [x] Log session per `CLAUDE.md` rules (`RAG_pipeline/.claude/PROGRESS_LOG.md`, `context.md`)
- *Follow-up noted*: thumbs-up rows currently also carry a populated `citations_snapshot`, though the handler was intended to only snapshot on 'down'. Harmless (more analytics data) but worth investigating.

**Day 2 — Atomic docs swap + staging GC (Gap 2) ✅ DONE 2026-04-20**
- [x] Extracted swap logic to `knowledge_base/mcp-server/src/mcp_server/services/atomic_swap.py` — `swap_docs()`, `recover_stale_backups()`, `gc_abandoned_staging()` pure functions
- [x] Replaced `projects.py:338-342` with `swap_docs(output_dir)` — backup-then-rename via `os.rename` (atomic on same FS), with rollback on mid-swap failure; `docs_prev/` kept only for the swap duration
- [x] Added startup reconciliation: lifespan hook now calls `recover_stale_backups` (promotes orphaned `docs_prev/` when `docs/` missing; removes leftover backups when both exist) + `gc_abandoned_staging` (removes `docs_staging/` older than `MCP_STAGING_GC_DAYS`, default 7)
- [x] New config field `staging_gc_days: int = 7`
- [x] 12 unit tests with `tmp_path` covering happy path, rollback on mocked `os.rename` failure, recovery both states, GC with backdated mtime — all passing (0.02s)
- [ ] **Remaining**: user restart of local MCP dev server (PID on :8002) to pick up the new code; then trigger a gen → assert no `docs_prev/` or `docs_staging/` remain after success
- [x] Log session (`knowledge_base/.claude/PROGRESS_LOG.md`)

**Day 3 — Scan completeness warnings (Gap 3)** ✅ SHIPPED 2026-04-20
- [x] Add `ScanWarning` class + `warnings: list[ScanWarning]` field to `ScanResult` in `knowledge_base/intodoc/src/intodoc/models.py`
- [x] In `platforms/talend/parser.py`, capture every swallowed `ET.ParseError` / `Exception` as `ScanWarning` (4 sites: metadata_index, job_id_index, context, job)
- [x] Thread the same pattern through Workato (recipe, function), Lobster (profile), and Generic (connection — JSON + YAML, previously fully silent) parsers
- [x] Extend `ScanStatusResponse` with `warnings`, `warnings_count`, `previous_job_count`, `job_delta`; `GET /mcp/status/{project_id}` reads warnings from `scan_result.json` and derives delta from the 2 most recent completed `ScanRun` rows (no DB migration)
- [x] Dashboard Scan History: yellow `! N` chip on the latest completed row when `warnings_count > 0`; signed green/red `±N` delta next to Components when `job_delta !== 0`
- [x] 12 new `tmp_path` pytest tests — all green; 274 existing parser tests unchanged; `npx tsc --noEmit` clean
- [ ] **Remaining (manual e2e)**: clean-scan → `warnings: []` in `scan_result.json`; malformed file scan → `warnings_count > 0` in `/status`; job-delete scan → `job_delta < 0`; dashboard shows chip + delta
- [x] Log session (`knowledge_base/.claude/PROGRESS_LOG.md`)

**Day 4 — Onboarding copy alignment (Gap 7, Gap 8)**
- [ ] Audit `ProvisioningStep.tsx:26-31` — does each of the 4 stages map to a real backend event? If not, collapse to single "Setting up your workspace" spinner
- [ ] Replace "Takes about 2 minutes" with progress-driven estimate (or remove until Workstream A lands)
- [ ] Rewrite "we'll enable them automatically when they ship" → "We'll email you when this ships" (and add email capture)
- [ ] Walk through the full wizard as a fresh tenant; note every remaining claim that doesn't match backend behavior
- [ ] Log session

**Day 5 — Buffer / regression testing / catch-up**
- [ ] Run benchmark before + after week's changes — no regression
- [ ] Deploy to staging, smoke-test full customer flow
- [ ] Update `knowledge_base/CLAUDE.md` + `RAG_pipeline/CLAUDE.md` with new conventions (Feedback table, scan warnings, atomic swap)

### WEEK 2 — Flagship promise: "Push → docs auto-update"

**Day 6 — Webhook secret validation + auto-generate chain (Gap 1, pt 1)**
- [ ] Add `webhook_secret TEXT` column to `projects` table (new migration)
- [ ] On repo registration (`projects.py:85-118`), generate + store a secret; expose to user for manual webhook install
- [ ] In `webhook.py:19-63`, validate `X-Hub-Signature-256` HMAC against stored secret — 401 on mismatch
- [ ] Add `auto_generate_on_push BOOLEAN DEFAULT true` column
- [ ] Extend webhook handler to await scan success, then invoke the same `_run_generate_background` runner the dashboard uses
- [ ] Test end-to-end with `gh api` or a dummy webhook POST

**Day 7 — GitHub App / PAT webhook installer (Gap 1, pt 2)**
- [ ] Add optional GitHub PAT field on repo add; on save, call `POST /repos/{owner}/{repo}/hooks` to install the webhook automatically with the secret from Day 6
- [ ] If no PAT: show a dashboard panel with the webhook URL, secret, and copy buttons + step-by-step screenshots
- [ ] Log session

**Day 8 — Onboarding goals step (Gap 10, pt 1)**
- [ ] Migration: add `goals JSONB` + `team_size INTEGER` + `primary_role TEXT` to `user_onboarding`
- [ ] New `WizardGoalsStep.tsx` after WelcomeStep: multi-select goals (`["Onboard new hires", "Migrate off a legacy platform", "Audit / compliance", "Reduce on-call load", "Executive visibility"]`) + team size + role
- [ ] Persist via `PATCH /onboarding/me`
- [ ] Log session

**Day 9 — Integration context capture (Gap 10, pt 2)**
- [ ] Migration: add `business_context JSONB` to `projects`
- [ ] After first scan completes, dashboard shows one-line "What does this integration do and who owns it?" prompt per top-level project/folder
- [ ] Persist via new `PATCH /projects/{id}` path
- [ ] Inject `business_context` + user `goals` into `conversational_rag.py` system prompt (start with simple concatenation; refine later)
- [ ] Log session

**Day 10 — Buffer / end-to-end test**
- [ ] Push a commit to a test repo → webhook fires → scan runs → docs regenerate → RAG picks up new content. Total time under 5 min.
- [ ] Regression benchmark; ensure goals/context injection didn't degrade quality
- [ ] Copy cleanup based on Day 4 walkthrough notes

### WEEK 3 — Learning loop: chat drives docs

**Day 11 — Knowledge-gaps admin view (Gap 11, pt 1)**
- [ ] New page `RAG_pipeline/frontend/app/admin/knowledge-gaps/page.tsx`
- [ ] New backend endpoint `GET /api/v1/admin/knowledge-gaps` that joins:
  - Hindsight quality bank (zero-result, low-citation queries — already populated)
  - `feedback` table (cluster thumbs-down by retrieved doc)
  - Hindsight corrections bank
- [ ] Each row renders: topic summary + count + example query + affected docs + "Generate doc update" button (button wired Day 12)
- [ ] Log session

**Day 12 — Gap-fill doc generation (Gap 11, pt 2)**
- [ ] Extend `claude_service.py` with `mode="gap_fill"` — takes list of gap queries + sample retrieved context + corrections as input
- [ ] Output lands in `docs_staging/inferred/` and stays there pending admin review (no auto-swap)
- [ ] Admin UI: "Review" modal showing proposed doc section with diff vs existing docs
- [ ] Admin clicks "Approve" → runs the atomic swap from Day 2
- [ ] End-to-end test: thumbs-down a wrong answer → admin sees gap → generates fix → approves → same question now answered correctly
- [ ] Log session

**Day 13 — Correction-driven doc patches (Gap 11, pt 3)**
- [ ] When a correction lands in Hindsight, check `citations_snapshot` to map it to a specific doc section
- [ ] If match found, surface on admin page as "1-click patch this section" suggestion
- [ ] Log session

**Day 14 — Notifications breadth (Gap 13)**
- [ ] Wire `create_notification()` into: doc ingestion complete, scan complete (webhook-triggered), RAG query failure, doc regeneration ready for review (from Day 12)
- [ ] Verify bell icon actually populates during normal usage
- [ ] Log session

**Day 15 — Buffer + staging release**
- [ ] Deploy to staging; have a non-technical teammate do a full walkthrough from empty tenant → connected repo → generated docs → chat → thumbs down → knowledge gap → approved fix
- [ ] Document any copy mismatches that remain

### WEEK 4 — Production-hardening (optional for pilot, required for scale)

**Day 16-17 — Deep Dive SDK backend (Gap 5)**
- [ ] Define `DeepDiveBackend` protocol in `deep_dive_service.py` with `start_session / send_message / stream_events`
- [ ] Keep existing subprocess path as `ClaudeCodeBackend`
- [ ] New `AnthropicSDKBackend` using Python SDK directly; map each MCP tool to an Anthropic tool definition
- [ ] Config flag `DEEP_DIVE_BACKEND` in `RAG_pipeline/backend/src/config.py`
- [ ] Test on a box without Claude CLI installed — verify Deep Dive works with `DEEP_DIVE_BACKEND=anthropic_sdk`

**Day 18 — Benchmark corpus expansion + auto-run (Gap 4)**
- [ ] Split `benchmark/corpus/default.json` into per-platform corpora
- [ ] Seed each with ≥30 Q&A pairs drawn from real/anticipated customer questions
- [ ] Hook atomic swap (Day 2) to auto-run benchmark on successful generation; store `BenchmarkRun` linked to project version
- [ ] Frontend: show last-run metrics on project card

**Day 19 — Benchmark regression detection (Gap 4, pt 2)**
- [ ] Compute rolling baseline per project (last 5 runs)
- [ ] Flag new run as regression if precision@5 or relevance drops >15%
- [ ] Red badge on project card + notification (uses Day 14 wiring)

**Day 20 — Buffer / polish / pilot-ready release**
- [ ] Full regression pass
- [ ] Update `docs/ROADMAP.md` to reflect what shipped
- [ ] Update `docs/PRESENTATION.md` claims to match current reality

### LATER (not blocking pilot)

- [ ] **Gap 17 — Plug-in prompt repos (replaces template CMS)**: Add `prompts_repo_url` + `prompts_repo_ref` to `projects`. Implement `PromptRepoLoader` service (clone-or-pull into `data/prompt_repos/<sha>/`, resolve name → path with bundled fallback). Wire `claude_service.py` to it. Add one dashboard input: "Custom prompt repo URL (optional)". Delete the template CMS in the same migration (tables, routes, service, 7 dashboard components, `useTemplates.ts`). Publish starter `intodata/docgen-prompts` public repo. Update `knowledge_base/CLAUDE.md`. Estimate: 1 day if tables empty; 1.5–2 days if real content has to be migrated.
- [ ] **Gaps 15 & 16 business decision**: Multi-model A/B (Gap 15) and doc-gen model transparency (Gap 16) are deliberately **not scheduled** as code tasks yet. The right next move is a claim-vs-build decision — tone down the pitch (cheap) vs build a model picker + doc-gen benchmark (1–2 weeks). Flag in the next founder conversation before committing sales language.
- [ ] **Workstream E — Plugin polish**: Move Talend-isms out of `ScanResult` into `platform_data: dict[str, Any]`; convert prompts to versioned `.md.j2`; write `knowledge_base/intodoc/PLUGINS.md`; add per-platform smoke tests.
- [ ] **Workstream A stretch — Diff-aware re-scan**: Wire `intodoc/scanner/git_analyzer.py` to skip unchanged files. Massive win for large repos.
- [ ] **Workstream G-3 — Tone adaptation**: Combine goal + detected intent to parameterize response prompt (auditor vs new hire).
- [ ] **Gap 14 — Long conversation auto-summary**: Background task after N turns runs `hindsight.areflect()` and injects summary into next turn's system prompt.
- [ ] **Gap 12 — Fine-grained query intent**: Extend taxonomy with task intent (troubleshoot / onboard / audit) and adapt prompt injection.
- [ ] **Chat history full-text search**: Add `tsquery` index on `conversation_messages.content`.

---

## File Reference Index

Quick lookup of where each gap lives in the codebase.

| Gap | Primary file(s) |
|---|---|
| 1 (GitHub push→docs) | `knowledge_base/mcp-server/src/mcp_server/api/routes/webhook.py:19-63` • `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:85-118, 304-418` |
| 2 (atomic swap) | `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py:339-347` |
| 3 (scan completeness) | `knowledge_base/intodoc/src/intodoc/platforms/talend/parser.py` • `knowledge_base/intodoc/src/intodoc/models.py:154` |
| 4 (benchmarking) | `RAG_pipeline/backend/benchmark/runner.py` • `RAG_pipeline/backend/benchmark/corpus/default.json` • `RAG_pipeline/frontend/app/admin/benchmark/page.tsx` |
| 5 (Deep Dive) | `knowledge_base/mcp-server/src/mcp_server/services/deep_dive_service.py:105-114` |
| 6 (plugin polish) | `knowledge_base/intodoc/src/intodoc/platforms/base.py:14-42` • `knowledge_base/intodoc/src/intodoc/models.py:154` |
| 7 (onboarding timing) | `RAG_pipeline/frontend/components/onboarding/steps/WelcomeStep.tsx:60` • `ProvisioningStep.tsx:26-31, 141` |
| 8 (coming soon) | `PlatformSelectStep.tsx:71` • `CommToolSelectStep.tsx:59-62` |
| 9 (thumbs feedback) | `RAG_pipeline/frontend/components/MessageBubble.tsx:139-166` |
| 10 (intent/context) | `RAG_pipeline/backend/src/db/models/onboarding.py:49-50` • `RAG_pipeline/backend/src/generation/conversational_rag.py` |
| 11 (chat→docs) | `RAG_pipeline/backend/src/services/hindsight_client.py:622-630` • `conversational_rag.py:97-100` • `RAG_pipeline/frontend/app/admin/hindsight/page.tsx` |
| 12 (intent classification) | `RAG_pipeline/backend/src/generation/intent_classifier.py:57-76` |
| 13 (notifications) | `RAG_pipeline/backend/src/db/models/onboarding.py:77-94` • `onboarding_service.py` |
| 14 (conversation context) | `RAG_pipeline/backend/src/generation/conversational_rag.py:51` |
| 15 (multi-model A/B) | `RAG_pipeline/backend/benchmark/runner.py` • `RAG_pipeline/backend/src/db/models/benchmark.py:23` • `RAG_pipeline/frontend/app/admin/benchmark/page.tsx` • `RAG_pipeline/backend/src/generation/llm_client.py:160` |
| 16 (doc-gen model transparency) | `knowledge_base/mcp-server/src/mcp_server/config.py:52-53` • `RAG_pipeline/backend/benchmark/corpus/default.json` |
| 17 (template CMS → plug-in prompt repo) | `knowledge_base/mcp-server/src/mcp_server/db/template_models.py` • `knowledge_base/mcp-server/src/mcp_server/api/routes/templates.py` • `knowledge_base/dashboard/src/components/templates/*` |

---

## How to Keep This Document Alive

- After each day's work: check the boxes, note actual elapsed time, add any scope shifts in a `### Day N notes` sub-section.
- If reality surprises you (promise turned out true, or a gap turned out bigger), update the relevant gap section immediately — stale audits are worse than no audits.
- When you finish Week 1-3, run one more pass of the audit against current code to catch new promises that crept in.
- Sync updates into `knowledge_base/.claude/PROGRESS_LOG.md` and `RAG_pipeline/.claude/PROGRESS_LOG.md` per the existing logging rules in `CLAUDE.md`.
